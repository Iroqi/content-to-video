#!/usr/bin/env python3
"""跨段场景延续（persistent stage）：把上一段"演完之后"的画面烘焙进本段这份内联副本。

**要解决的问题**：整页画布每一页都是独立的一张图，GSAP 的主时间轴也按段落切。于是
"镜头推近到局部 → 下一页继续在这个局部上讲"是演不出来的——下一页重新从原始 SVG 起
跑，上一段画出来的状态（描边长出来的线、滚到 68 的数字、淡出的对照组、推近的相机）
在页界处整体回弹。观众看到的不是"同一幕换了下一句台词"，而是"换了另一幅画"。

**做法（一句话）**：作者只画**一张** SVG，把旁白切成几段，每段只写**接续的那几拍**。
生成期在 `gen_hyperframes.director_prepare` 里多做一遍：把上一段 steps 的**收尾态**
烘焙进本段那份内联 SVG 的副本（本段自己的 steps 到点在副本之上继续演）。运行期零改动、
零新原语——每页仍然是一份静态 SVG + 一条挂在自己时间线上的补间，逐帧 seek 可复现。

**为什么"收尾态"是可算的**：director 的全部原语都是可序列化的补间（无 on* 回调，见
_images_schema._validate_tween_vars），所以每一步的结束态只由它的 `to`/`set`/`draw`/
`morph`/`count` 字面量决定，与运行时无关。按 beat 时刻排序逐条结算即可（同一元素后到
的说话）。`from`-only 步骤的结束态 = 演之前的样子，而"演之前的样子"已经是上一段烘焙的
结果——所以它天然是 no-op，不用碰。

**搬运的边界（故意的，别当漏检）**：

- 搬：CSS 样式（opacity/fill/visibility/…，含 GSAP 的 autoAlpha 展开）、`attr:{}` 属性、
  morph 的终态 `d`、count 的终态文本、描边的终态（dashoffset 0）、相机的收尾位。
- 搬一半：元素级 transform 只搬 **x/y**（纯平移，跟原点无关，见 `_carry_translate`）。
  不搬 scale/rotation/skew/xPercent/yPercent：那要把每个元素自己的 bbox 中心当原点做
  折算，而中心是"占位盒"估的（见 _cam_crop.cam_content_center 的偏差说明）——相机整幅
  推近时这个偏差就是取景偏移，本来就只 warn；元素级若也照此办理，就会把"看着差不多"的
  东西悄悄挪位，那是更难查的一类。跳过 + 出声。
- 相机折进 `#cam` 里新增的一层静态 `<g data-ctv-stage>`，**不是** `#cam` 自己的 transform。
  两个理由：① 投影不会连乘两次（_cam_crop 读子树时自然算进这层，本段姿态在其之上乘一层，
  正是接续语义）；② 每段相机绕的那一点是按 `#cam` **子树**的几何算的（`cam_geometry`
  的口径明确不含 `#cam` 自身的 transform），折在子层上，下一段才是在"当前可见画面"的
  中心推；折到 `#cam` 自己身上，这层推近对本段的原点计算就是隐形的，链越长飘得越远。
- 只搬**上一段自己写的** steps：更早的段落在上一段烘焙时就已经进了它的副本，所以链路
  是传递闭包（A→B→C 里 C 拿到的是 A+B 的叠加），每层相机各包一个 `<g>`，深度=链长。
- 同一张 SVG 内联两份 ⇒ 图内 id 在成片 DOM 里重复。导演选择器带 `#img-{sid}` 前缀
  （作用域天然分开），所以动画照常；但图内的 `url(#id)` / `href="#id"` 按文档序只认第一份
  （= 上一页那一幅），所以**接续页给 `<defs>` 或被引用过的 id 写补间等于没写**——那一拍
  由 `global_ref_leaks` 在生成期按步报错，不留成作者须知。
"""
import re
import xml.etree.ElementTree as ET

from _cam_crop import (CAM_ID, cam_projection_origin, cam_resting_pose,
                       decompose_transform, resolve_num)
from _timeline import beat_cycles, ends_at_start

SVG_NS = "http://www.w3.org/2000/svg"
# 净化后的副本再序列化时不给标签套 ns0: 前缀（与 _svg_sanitize 同一套；重复注册无害）。
ET.register_namespace("", SVG_NS)

# 相机烘焙层的标记：纯给人看的（成片 DOM 里一眼认得出哪层是接续带进来的），引擎不读它。
STAGE_ATTR = "data-ctv-stage"

# GSAP 的控制参数：不是视觉状态，收尾态里不该出现。
_CONTROL_KEYS = frozenset({"duration", "ease", "delay", "stagger"})
# 变换类属性里**搬不动**的那些：见模块头"元素级 transform 只搬 x/y"那条理由。
_TRANSFORM_KEYS = frozenset({
    "xPercent", "yPercent", "scale", "scaleX", "scaleY",
    "rotation", "rotationX", "rotationY", "skewX", "skewY",
    "transform", "transformOrigin", "svgOrigin", "shortRotation",
})
# 纯平移：跟原点无关，能精确搬进副本（_carry_translate）。
_TRANSLATE_KEYS = frozenset({"x", "y"})
# 这些键走 attribute，不走 style（GSAP 的 attr:{} 显式通道）。
_ATTR_KEY = "attr"
# 纯数字才认的样式：相对写法（"+=0.2"）要有基值可加。
_NUMERIC_STYLE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
# count 的终值是一行文本，只有这些元素搬得动（写在 <rect> 上就是作者放错了 target）。
_TEXT_TAGS = frozenset({"text", "tspan", "textPath"})


def _kebab(prop):
    """GSAP 的驼峰 CSS 名 → 写进静态 style 属性的小连字符名。

    必须是这个方向：GSAP 运行期是 `el.style.strokeWidth = 3`（CSSOM 认驼峰，序列化成
    stroke-width），而我们写的是 `style="…"` 字符串——`strokeWidth:3` 在 CSS 里非法，
    会被浏览器整条丢掉，于是"烘焙了"等于"没烘焙"，且没有任何提示。
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "-", prop).lower()


def _style_map(el):
    """现有 inline style → 有序 {小写属性名: 值}（重复属性按后者说话，与 CSS 一致）。"""
    out = {}
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            name, _, value = decl.partition(":")
            name = name.strip().lower()
            if name:
                out[name] = value.strip()
    return out


def _css_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return float(v) if _NUMERIC_STYLE.match(str(v).strip()) else None


def _fmt(v):
    """数值得写成 CSS 能吃的样子：定点 6 位再去尾零——1.0 → "1"，而 0.4+0.2 那种
    浮点尾巴（0.6000000000000001）不能原样进 style，它既难读也没多准。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return f"{float(v):.6f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _set_style(el, prop, value):
    styles = _style_map(el)
    if prop.lower() == "autoalpha":        # GSAP 专有：opacity + visibility 的合写
        # 必须在 _kebab 之前认出它：驼峰一转成 auto-alpha 就不是合法 CSS 属性，
        # 浏览器整条丢掉，于是"烘焙了"等于"没烘焙"。
        # 基值与下面 else 分支同一口径：先 inline style、再呈现属性，都没有才落到
        # 1.0（opacity 的 CSS 初值）。原先这里无条件按 1.0 起算，于是 "+=0.5" 在一个
        # opacity="0.2" 的元素上烘焙出 1、运行期却是 0.7——同一元素的两条等价写法烘
        # 焙出两个值，接续页就在页界静默跳一下，正是本模块要消灭的阶跃。
        cur = _css_num(styles.get("opacity"))
        if cur is None:
            cur = _css_num(el.get("opacity"))
        av = resolve_num(value, 1.0 if cur is None else cur) or 0.0
        styles["opacity"] = _fmt(min(av, 1.0))
        styles["visibility"] = "visible" if av > 0 else "hidden"
    else:
        key = _kebab(prop)
        # 相对写法的基值：GSAP 读的是**计算值**，而 SVG 计算值的来源既可能是 inline
        # style 也可能是呈现属性（opacity="0.2"）。只认 style 就会把基值当成 0，
        # 于是 "+=0.5" 烘焙出 0.5、运行期却是 0.7——两页之间静默跳一下。
        cur = _css_num(styles.get(key))
        if cur is None:
            cur = _css_num(el.get(key))
        resolved = resolve_num(value, cur)
        styles[key] = _fmt(resolved if resolved is not None else value)
    el.set("style", ";".join(f"{k}:{v}" for k, v in styles.items()))


def _set_attr(el, name, value):
    cur = _css_num(el.get(name))
    resolved = resolve_num(value, cur)
    el.set(name, _fmt(resolved if resolved is not None else value))


def _targets(root, selector):
    """单个 `#id` / `.class` 选择器 → 元素列表（schema 已挡掉复合选择器）。"""
    kind, name = selector[0], selector[1:]
    if kind == "#":
        el = root.find(".//*[@id='%s']" % name)
        return [el] if el is not None else []
    return [el for el in root.iter() if name in (el.get("class") or "").split()]


def _ordered(steps, beats):
    """按**时刻**排序的 (下标, step)：后演的说话，所以结算必须跟时间走、不跟数组走。

    作者完全可以把 steps 写成乱序（数组第 3 条的 at 比第 1 条早），渲染端照数组顺序发射
    补间也没问题——因为每条都自带绝对时刻，GSAP 按时间求值。而"收尾态"是把时间轴折叠到
    最后一帧，折叠顺序必须是时间顺序，否则后写先演的属性会被先写后演的覆盖掉。
    beats 缺省（调用方算不出落点）时退回数组序。
    """
    if not beats or len(beats) != len(steps):
        return list(enumerate(steps))
    return sorted(enumerate(steps), key=lambda pair: (beats[pair[0]][0], pair[0]))


def _carry_translate(el, moves):
    """把 x/y 的终值折成元素 transform 上的一层**外层平移**；搬不动返回 False。

    能直接前缀在元素自己的 transform 上（不像相机那样另包一层 `<g>`）：GSAP 的 x/y 就是
    元素 transform 矩阵的 e/f 分量（父空间里的外层平移），平移跟平移可交换，所以
    `translate(dx,dy) 原串` 与运行期演到的位置在几何上一字不差；而原串一个字都不动，
    本段自己的补间读到的基值仍是作者写的那些。
    基值从元素**当前**的 transform 现读（跟 _cam_crop 同一个 `decompose_transform`）：
    结算多条步骤时每次都按"当前 → 终值"补差，所以绝对值和 `+=`/`*=` 相对写法都落得准，
    不用另外记账。
    读不懂基值就整体放弃 x/y（元素的 transform 里有 rotate / matrix / skew）——那正好
    也是不搬 scale/rotation 的那一类元素，硬搬只会把东西悄悄挪位。
    """
    base = decompose_transform(el)
    if base is None:
        return False
    dx = dy = 0.0
    for key, value in moves.items():
        cur = base[0] if key == "x" else base[1]
        end = resolve_num(value, cur)
        if end is None:
            return False
        if key == "x":
            dx = end - cur
        else:
            dy = end - cur
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return True
    old = (el.get("transform") or "").strip()
    el.set("transform", f"translate({_fmt(dx)},{_fmt(dy)})" + (f" {old}" if old else ""))
    return True


def _settle_payload(el, payload):
    """一条补间字典（to / set / attr 混合）的终值写进元素。"""
    skipped = []
    moves = {}
    for key, value in payload.items():
        if key in _CONTROL_KEYS:
            continue
        if key in _TRANSLATE_KEYS:
            # 两个轴攒到一起再写：一条补间里 x 和 y 常常同时给，分两次前缀会留两串平移
            moves[key] = value
            continue
        if key in _TRANSFORM_KEYS:
            skipped.append(key)
            continue
        if key == _ATTR_KEY and isinstance(value, dict):
            for aname, avalue in value.items():
                _set_attr(el, aname, avalue)
            continue
        _set_style(el, key, value)
    if moves and not _carry_translate(el, moves):
        skipped += sorted(moves)
    return skipped


def _settle_step(root, step):
    """一步的结束态：draw / morph / count / set / to（from-only 是 no-op）。

    返回 (搬不动的 transform 键, 落错元素的 count 标签)，由调用方合成说明——跳过必须
    出声：静默"少搬一样东西"就是成片里那种"我明明演了，下一页怎么回去了"。
    """
    selector = step["target"]
    if selector == "#" + CAM_ID:
        return [], []                   # 相机走 _fold_camera，不进元素通道
    transforms, misfires = [], []
    for el in _targets(root, selector):
        tag = el.tag.rsplit("}", 1)[-1]
        if step.get("draw"):
            # 演完 = 线长满了：pathLength=1 归一 + dasharray=1 + dashoffset=0（solid）。
            transforms += _settle_payload(el, {
                "attr": {"pathLength": 1},
                "strokeDasharray": 1, "strokeDashoffset": 0})
        if "morph" in step:
            transforms += _settle_payload(el, {"attr": {"d": step["morph"]["to"]}})
        count = step.get("count")
        if count is not None:
            # schema 保证写了 count 就不会有 set/to/draw/morph，所以这里不用继续往下走
            if tag in _TEXT_TAGS:
                dec = int(count.get("decimals", 0))
                # 与运行期 onUpdate 同一个语义：`textContent = …` 会连带清掉子节点，
                # 只 set el.text 会把原文留在树上——接续页上就多出一截从没演过的字。
                for child in list(el):
                    el.remove(child)
                el.text = (f"{count.get('prefix', '')}"
                           f"{float(count['to']):.{dec}f}{count.get('suffix', '')}")
            else:
                misfires.append(tag)
            continue
        for key in ("set", "to"):
            payload = step.get(key)
            if isinstance(payload, dict):
                transforms += _settle_payload(el, payload)
    return transforms, misfires


def _fold_camera(root, steps, center=None):
    """把 `#cam` 的收尾姿态折进一层包住其全部子元素的静态 `<g>`。

    写法是 `translate(E, F) scale(S)`（不是 matrix）：_cam_crop 只解 translate/scale，
    写成 matrix 就等于对下一环的自查与烘焙双双失明（它按口径跳过读不懂的子树）。

    `center` 是这一页**运行期真正绕的那一点**，由调用方在动这棵树之前量好传进来（见
    `bake_settled_state`）；省略则按当前树现算——只有不关心与渲染器对齐的场合才该省。
    """
    pose = cam_resting_pose(steps)
    if pose is None or not pose.get("rests", True):
        return None
    cam = root.find(".//*[@id='%s']" % CAM_ID)
    if cam is None or not len(cam):
        return None
    cx_cy = center if center is not None else cam_projection_origin(root, pose)
    if cx_cy is None:
        return None
    cx, cy = cx_cy
    s = pose["scale"]
    # p' = C + s·(p − C) + t = s·p + [(1−s)C + t]  →  translate 分量就是方括号那一项。
    e = (1.0 - s) * cx + pose["x"]
    f = (1.0 - s) * cy + pose["y"]
    if abs(s - 1.0) < 1e-9 and abs(e) < 1e-9 and abs(f) < 1e-9:
        return None                    # 收尾位 = 原位：不包这层，副本与原始内联完全一致
    # 带命名空间建：序列化结果与裸 "g" 一字不差（默认命名空间继承），但内存里这棵树
    # 和"再解析一遍"的树就长得一样——链式接续正是拿产出的字符串再解析一次的。
    stage = ET.Element(f"{{{SVG_NS}}}g",
                       {STAGE_ATTR: "1",
                        "transform": f"translate({e:.4f},{f:.4f}) scale({s:.6f})"})
    for child in list(cam):
        cam.remove(child)
        stage.append(child)
    cam.append(stage)
    return s, e, f


def bake_settled_state(markup, steps, beats=None):
    """把一段 director 的**收尾画面**烘焙进它的内联副本，返回 (新副本, 跳过说明)。

    参数是上一段的 (净化后 markup, steps, beat 落点)；产物交给下一段当 inline_svg 用。
    只在生成期调用一次每页一份，不进渲染循环，所以这里的开销是"读一遍 XML 改一遍树"。

    值全部来自 _images_schema 校验过的标量（JSON 可序列化、无 on* 回调），写回时由
    ElementTree 序列化转义（属性里的引号/尖括号它来逃）——这条链不新增未转义插值面。
    """
    try:
        root = ET.fromstring(markup)
    except ET.ParseError as e:
        raise ValueError(f"接续烘焙失败：上一段的内联副本解析不动（{e}）")
    # 相机绕的那一点必须在**动这棵树之前**量：渲染器给这一页注入的 svgOrigin 就是拿这棵
    # （它被交到时那棵树）算的（`_cam_crop.cam_default_origin`），烘焙若改用结算之后的
    # 树，两边又分家——而分家正是这次要修的跨页阶跃。结算会挪内容并集（元素平移、count
    # 重写文本），中心跟着走，所以这个先后不是洁癖。
    pose = cam_resting_pose(steps or [])
    pre_center = cam_projection_origin(root, pose) if pose else None
    notes = []
    transforms, misfires = [], []
    loops, cam_loops = [], []
    for _i, step in _ordered(steps or [], beats):
        # 没有收尾态的两种一步都不搬：`repeat:-1` 永远演不完（终点无从谈起），`yoyo` 走
        # 偶数遍的元素回到它原来的样子（搬了等于凭空挪走一个从没出现过的状态）。前者必须
        # 出声，后者什么都不必说。相机步单独记账：它决定 _fold_camera 折不折（见下）。
        if beat_cycles(step) is None:
            (cam_loops if step.get("target") == "#" + CAM_ID else loops) \
                .append(step["target"])
            continue
        if ends_at_start(step):
            continue
        t, m = _settle_step(root, step)
        transforms += t
        misfires += m
    if loops:
        # 只有非相机元素：它们各自的终态不搬，但相机照常折（_fold_camera 只认
        # target=="#cam" 的步）——旧文案把两件事混成一句话，实测打印出"相机一律
        # 不折"的同时相机层照常折进去了，自相矛盾。
        notes.append("这些步写了 repeat:-1，永远演不完，也就没有'最后停在哪儿'：" 
                    + "、".join(sorted(set(loops)))
                    + "。接续页不搬它们的终态；要接续就写明确遍数 repeat:N")
    if cam_loops:
        # 相机步无限循环：整台相机没有收尾位，_fold_camera 照例不折（这层静默没有
        # 别的出口，必须在这里说清，不然作者以为推近的镜头会带进下一页）。
        notes.append("相机步 " + "、".join(sorted(set(cam_loops)))
                     + " 写了 repeat:-1，相机永不停下——整台相机的收尾位不作数，"
                       "本页不折 <g data-ctv-stage>（那一页的镜头没有'最后停在哪儿'；"
                       "要接续就把 repeat 改成明确遍数）")
    if transforms:
        notes.append("接续搬不动元素级 transform 属性 " + "、".join(sorted(set(transforms)))
                     + "——它们要么要以元素自己的 bbox 中心为原点折算（scale/rotation/"
                       "skew/xPercent），要么元素自身的 transform 写法让平移基值读不准"
                       "（x/y 只会落在后一类里）。这些补间演出来的位移/缩放不会带进下一页"
                       "（要持续的画面变化请在接续页里重演那一拍，或改用相机/attr）")
    if misfires:
        notes.append("count 的终值只能落在 <text>/<tspan> 上，命中的 "
                     + "、".join(sorted("<%s>" % t for t in set(misfires)))
                     + " 搬不动（target 放错了，运行期那一拍同样写不进去）")
    cam = _fold_camera(root, steps or [], pre_center)
    if cam:
        notes.append("相机收尾位已折进本层（scale={:g}、平移=({:g}, {:g})）".format(*cam))
    return ET.tostring(root, encoding="unicode"), notes


# 图内引用只认"文档里第一个匹配元素"的两种写法：url(#id)（fill/filter/mask/marker/…）
# 与 href="#id"（<use> 实例）。
_GLOBAL_REF_RE = re.compile(
    r"""url\(\s*['"]?\s*#([^'")\s]+)|\bhref\s*=\s*['"]#([^'"\s]+)""")


def global_ref_leaks(markup, steps):
    """本段 steps 里打在"两份副本共享的元素"上的那些 → [(下标, target)]。

    为什么只有接续页会中：同一张 SVG 内联两份后图内 id 在成片 DOM 里重复，而**绘制端**的
    `url(#id)` / `href="#id"` 按文档序解析到第一个匹配元素——也就是上一页那一幅。导演选择器
    带 `#img-{sid}` 前缀，补间确实打在**本页这份**的渐变/滤镜上，本页的图形却一个都不读它：
    那一拍在成片里根本不落地。元素在 `<defs>` 之外也一样，只要它的 id 被 url() / href 引过
    （`<use href="#icon">` 那类）。

    markup 解析不动时返回空：门禁不该因为解析失败误报，那件事在别处已经报过了。
    """
    try:
        root = ET.fromstring(markup)
    except ET.ParseError:
        return []
    shared = set()
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "defs":
            # <defs> 整棵子树（含它自己的 id）：这些元素只被引用、不直接渲染
            shared.update(d.get("id") for d in el.iter() if d.get("id"))
    for m in _GLOBAL_REF_RE.finditer(markup):
        shared.add(m.group(1) or m.group(2))
    out = []
    for i, step in enumerate(steps or []):
        sel = step.get("target")
        if not isinstance(sel, str) or sel[:1] not in ("#", "."):
            continue
        for el in _targets(root, sel):
            if (el.get("id") or "") in shared:
                out.append((i, sel))
                break
    return out
