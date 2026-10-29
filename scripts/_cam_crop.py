#!/usr/bin/env python3
"""运镜会不会把内容推出画幅——生成期一次纯几何自查，纯标准库、无第三方依赖。

**为什么要单独做这一件事**：这类 bug 在 SVG 源码里完全看不出来。元素坐标明明落在
0..1440 里，是相机 `scale` 把它推出去的。实测踩过的正是这一类：
`scale:1.16 svgOrigin:"540 660"` 把 y=1362 映射到 y≈1477（>1440），底栏整条在成片里
消失，源码与画面之间没有任何线索。`check_svg.py` 只看单文件、读不到 images.json 里的
animate steps，所以它没有判据；唯一同时握着 SVG 原文和运镜关键帧的地方是
`gen_hyperframes.animate_prepare`，检查就落在那里。

**口径（每一条都是故意的，别当漏检）**：

- 只查**收尾位**——所有运镜步骤走完、画面静止时的那个姿态。途中出画是合法演法
  （GSAP 用 `from` 把元素摆在画外再滑进来是标准入场），报它就是造噪声。
- `repeat:-1` 的运镜没有收尾位（相机永不停下），这一页就无从判定：这里出声说"没查"，
  绝不说"一切在幅内"——后者在这一页上是假话。
- 姿态里有**写了却读不出**的分量（`scale:"/=0"`、带单位的串）同样不出判定：GSAP 运行期
  算得出（`/=0` 是 Infinity，元素被推出画幅），静态几何算不出，于是 pose 标 `resolved=False`，
  消费方（投影自查与跨段折层）一律先看这个标记、宁可说"没查"也不按旧值编一个结论。
- `from` 不算收尾，`to`/`set` 才算：`from` 是"从这些值动回原值"，结束态是补间前的姿态；
  `to`/`set` 写的是终值，同一条属性后写的说话（GSAP 缓存 scale/x/y 三个组件，每条
  补间改的就是这三个数，所以这里也在组件空间里逐条结算）。
- 变换按 GSAP 写 SVG transform 的语义：`p' = o + s·(p − o) + t`。`o` 取该步的
  `svgOrigin`；作者没写时取 `#cam` 内容并集中心（不是 viewBox 中心）——但**这个中心
  不是"GSAP 的默认"，是渲染器钉进去的**：GSAP 自己的缺省绕 bbox 左上角、且那个角跟着
  别的步的 `from` 瞬态飘，所以 `cam_default_origin` 把它写成显式 `svgOrigin` 注入，
  让运行期与本模块、与 `_stage_carry` 用的是同一个点（钉之前两边分家，实测差 24~36px）。
- 相机属性支持 GSAP 的相对写法（`"+=40"` / `"*=1.2"` / `"/=2"`）：结算在**组件空间**里
  逐条累加（scale/x/y 各自跟着前一条走），与 GSAP 缓存 scale/x/y 三个组件的语义一致。
  早先这里把相对串当"读不出"直接跳过，于是"接续段继续推近"整条不进投影——那正是该报的。
- 跨段延续（`_stage_carry`）不额外传参：上一段的收尾相机位被折进 `#cam` 里的一层静态
  `<g>`，`_walk` 读子树时自然把它算进占位盒，本段姿态是在已推近的画面**之上**再乘一层
  ——这正是这里想要的合成结果。（不能把上一段的位烘焙到 `#cam` 自己的 transform 上：
  那样投影会连乘两次，而且 GSAP 的推近原点会退回"未推近内容的中心"，画面越推越飘。）
- 几何沿用 `_path_morph` 的"占位跨度"口径：path 连控制点一起算，宁可高估。祖先
  `<g transform>` 只解 translate/scale；子树里出现 rotate / matrix / skew 就整棵跳过——
  没有布局引擎，硬猜旋转只会让 warn 变得不可信。
- 铺满整幅的底板 `<rect>`（≥98% 宽高）不查：它本来就该溢出画幅。

只认文档约定的那个 `<g id="cam">`。往别的 `<g>` 上补间 scale 也能动，但不在自查视野里，
所以运镜请写在 `#cam` 上。
"""
import re
import xml.etree.ElementTree as ET

from _svg_sanitize import (FULL_BLEED_FRAC,  # noqa: E402  满幅阈值唯一真源（canvas_kit 同一份）
                           _local, _parse_num,  # noqa: E402  剥命名空间 + SVG 数字只有这一份实现
                           root_viewbox_size, shape_bbox)
from _timeline import beat_cycles, ends_at_start
from _template import svg_text_band

CAM_ID = "cam"
# 一条运镜步骤里能改变姿态的属性：scale 各向同性地放大，x/y 是平移（用户单位）。
_NUM_KEYS = ("scale", "x", "y")
# 文字横向宽度要布局引擎才知道，竖向不需要（基线 + 字号就够），所以切边只查上下。
_EDGE_TOL = 0.02   # 越过画幅边缘 2% 以内当 rounding/描边，不报
# GSAP 的相对写法：+= / -= / *= / /= 后面接一个数。组件空间里逐条结算，
# 与 GSAP 自己对 scale/x/y 缓存的处理同构。
_REL_RE = re.compile(r"^\s*([+\-*/])=\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*$")


def resolve_num(value, current=None):
    """GSAP 属性值 → 绝对数：认数字、绝对数字串，以及 `"+=5"`/`"*=2"` 相对写法
    （`current` 是该属性结算到此刻的值；相对写法没有基值时按中性元 1/0 起算，
    与 GSAP 从原位开始补间的行为一致）。读不出返回 None。

    `"/=0"` 读不出（返回 None），不回落成 `current`：GSAP 运行期算出的是
    Infinity，元素被推出画幅；把它当"没写"会让投影按旧值报"一切在幅内"——
    那一句在这一页上是假话。返回 None 让上层把这一页判成"没法算"并出声。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _REL_RE.match(value)
    if m:
        operand = float(m.group(2))
        base = current
        if m.group(1) in "*/":
            if base is None:
                base = 1.0
            if m.group(1) == "*":
                return base * operand
            return base / operand if operand else None
        if base is None:
            base = 0.0
        return base + operand if m.group(1) == "+" else base - operand
    return _parse_num(value)


def decompose_transform(el):
    """元素的 translate/scale 分解 → (tx, ty, sx, sy)；读不懂的返回 None。

    按**顺序复合**，不是把 translate 的参数加起来：`translate(10) scale(2) translate(5)`
    的真矩阵 e 是 20 而不是 15——夹在中间的 scale 会乘到后面的平移上。只认 translate /
    scale 才继续，因为纯这两类组成的矩阵一定还是 `T(tx,ty)·S(sx,sy)` 的形状，(e, f, a, d)
    就是无损分解。出现 rotate / matrix / skewX / skewY 返回 None：那种形状这里拆不出来
    （`_walk` 按"读不懂"跳过整棵子树，`_stage_carry` 按同一口径放弃搬 x/y）。
    """
    t = (el.get("transform") or "").strip()
    if not t:
        return 0.0, 0.0, 1.0, 1.0
    tx = ty = 0.0
    sx = sy = 1.0
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", t):
        # 与 points 同一口径：读不出的参数让整条 transform 判废（返回 None），
        # 不能丢掉再按位置取——`translate(10, abc)` 丢成 `translate(10)` 是
        # "读不懂的 transform 被当成读懂了"，投影照错的姿态算。
        vals = [_parse_num(a) for a in re.split(r"[,\s]+", args.strip()) if a.strip() != ""]
        if any(v is None for v in vals):
            return None
        op = name.lower()
        if op == "translate":
            if len(vals) > 2:
                return None
            ox = vals[0] if vals else 0.0
            oy = vals[1] if len(vals) > 1 else 0.0
            tx += sx * ox                    # M ← M · T(ox, oy)
            ty += sy * oy
        elif op == "scale":
            if not vals or len(vals) > 2:
                return None
            sx *= vals[0]
            sy *= vals[1] if len(vals) > 1 else vals[0]
        else:                       # rotate / matrix / skewX / skewY：不猜
            return None
    return tx, ty, sx, sy


def _box(el, tag):
    """元素自身坐标系里的占位盒 (x0, x1, y0, y1)；读不出返回 None。"""
    # 形状类（rect/circle/ellipse/line/polygon/polyline/path）走共享那份包围盒
    # （_svg_sanitize.shape_bbox，与 check_svg 的空带/越界同一份）；text / image
    # 要字号与锚点，口径只在运镜这边用，留在下面。
    if tag == "text":
        x, y = _parse_num(el.get("x")), _parse_num(el.get("y"))
        if y is None:
            return None
        # 竖向占位走 _template.svg_text_band（与 check_svg 的空带/越界同一口径）。
        # 这里原先读不到 font-size 时兜 17.0，而 check_svg 兜 26——同一份 SVG 在
        # 运镜自查里比在密度自查里矮 9px，"推镜把底栏推出画幅"就没人报。
        y0, y1 = svg_text_band(y, _parse_num(el.get("font-size")))
        # 横向只拿锚点当点：宽度要布局引擎，硬编一个只会在"竖向切边"之外多造误报。
        x = x if x is not None else 0.0
        return (x, x, y0, y1)
    if tag == "image":
        x, y, w, h = (_parse_num(el.get(k)) for k in ("x", "y", "width", "height"))
        if None in (x, y, w, h):
            return None
        return (x, x + w, y, y + h)
    return shape_bbox(el, tag)


def _label(el, tag):
    eid = el.get("id")
    if eid:
        return "#" + eid
    if tag == "text":
        body = "".join(el.itertext()).strip()
        return f"<text> {body[:10]!r}" if body else "<text>"
    return f"<{tag}>"


def _walk(cam, vb_w, vb_h):
    """`#cam` 子树 → [(label, box, has_text)]，box 已在 viewBox 坐标系里（含祖先平移/缩放）。

    跳过：铺满整幅的底板、以及祖先带 rotate/matrix/skew 的子树（读不准就不报，见模块
    开头那条口径）。`<g>` 自身的 transform 会复合到子元素上，一层都不丢。
    """
    out = []
    queue = [(cam, (0.0, 0.0, 1.0, 1.0))]      # (元素, 从 cam 累加到此的仿射)
    i = 0
    while i < len(queue):                       # 广度推进：报告顺序＝文档顺序（输出可复现）
        el, (tx, ty, sx, sy) = queue[i]
        i += 1
        tag = _local(el.tag)
        if el is not cam:
            own = decompose_transform(el)
            if own is None:                   # rotate/matrix/skew：整棵跳过
                continue
            otx, oty, osx, osy = own
            tx, ty = tx + sx * otx, ty + sy * oty
            sx, sy = sx * osx, sy * osy
        if tag in ("g", "svg", "a", "switch"):
            queue.extend((child, (tx, ty, sx, sy)) for child in list(el))
            continue
        box = _box(el, tag)
        if box is None:
            continue
        w, h = box[1] - box[0], box[3] - box[2]
        # 满幅底板阈值走 _svg_sanitize.FULL_BLEED_FRAC（唯一真源）：这里曾硬编码
        # 0.98，与 _covers_viewbox 的阈值分两家——改一处不改另一处时，同一张图
        # 在净化层算"铺满"、在取景投影里算"该溢出"，两道门给两个答案。
        if w >= vb_w * FULL_BLEED_FRAC and h >= vb_h * FULL_BLEED_FRAC:
            continue                          # 满幅底板本来就该溢出
        x0 = box[0] * sx + tx
        x1 = box[1] * sx + tx
        y0 = box[2] * sy + ty
        y1 = box[3] * sy + ty
        out.append((_label(el, tag), (x0, x1, y0, y1), tag == "text"))
    return out


def cam_resting_pose(steps):
    """走完全部运镜后 `#cam` 静止在什么姿态：{scale, x, y, origin, rests} 或 None（没运镜）。

    只认 target == "#cam" 的步骤。同一条属性后写的说话（GSAP 缓存 scale/x/y 三个组件，
    每条补间改的就是这三个数），值可以是绝对数也可以是 `+=`/`*=` 相对写法——后者跟着
    已结算到的组件值累加，与 GSAP 自己的处理同构。`from` 不动它（`from` 的值是瞬态
    起始态，结束态是补间前的姿态）。origin 例外：`svgOrigin` 是一次性设定，出现在任何
    一类步骤里都留下来，所以最后写的那个说话。

    `repeat` 的两类要按 GSAP 的真实收尾处理，不能按"演过就算"：`yoyo` 且遍数为偶数的
    那条走回起点，对静止位没有净贡献，跳过；`repeat:-1` 那条**永不停下**，于是整页没有
    收尾位可言——这里只把 `rests` 标成 False，绝不替它编一个姿态（出画自查与跨段烘焙都
    读这个标记，各自出声）。

    `resolved` 标 False = 姿态里有**写了却读不出**的分量（`scale:"/=0"`、带单位的串）。
    这时 pose 里的数字是上一条已结算到的值、不是这一页真实的收尾位，绝不能拿去投影
    （那会报出"一切在幅内"这种假话）或折层（那会折出一个错的位），消费方一律先看这个标记。
    """
    pose = {"scale": 1.0, "x": 0.0, "y": 0.0, "origin": None, "rests": True,
            "resolved": True}
    seen = False
    for step in steps or []:
        if step.get("target") != "#cam":
            continue
        seen = True
        if beat_cycles(step) is None:
            pose["rests"] = False
            continue
        if ends_at_start(step):
            continue
        for key in ("from", "to", "set"):
            payload = step.get(key)
            if not isinstance(payload, dict):
                continue
            origin = payload.get("svgOrigin") or payload.get("transformOrigin")
            if origin is not None:
                pose["origin"] = origin
            if key == "from":               # 瞬态起始态，不是收尾位
                continue
            for name in _NUM_KEYS:
                if name in payload:
                    n = resolve_num(payload[name], pose[name])
                    if n is None:
                        pose["resolved"] = False
                    else:
                        pose[name] = n
    return pose if seen else None


def cam_geometry(root):
    """(vb_w, vb_h, boxes)：`#cam` 子树的占位盒，坐标在 viewBox 系里；读不出返回 None。

    参数是**已解析的树**，因为 `_stage_carry` 要在同一棵树上改完再序列化——两处若各自
    从字符串重解析，算出的内容中心就会分家（烘焙式 `translate(e,f) scale(s)` 的 e/f 依赖
    原点，原点一分家接续画面逐段飘）。

    口径：含 `#cam` 之内所有层级的 translate/scale，但**不含 `#cam` 自身的 transform**
    （`_walk` 从 cam 起算时把它的 transform 当"由相机姿态负责"，正对应 GSAP 的
    getBBox() 不含元素自身 transform 这件事）。
    """
    size = root_viewbox_size(root)
    if not size:
        return None
    vb_w, vb_h = size
    cam = root.find(".//*[@id='%s']" % CAM_ID)
    if cam is None:
        return None
    boxes = _walk(cam, vb_w, vb_h)
    return (vb_w, vb_h, boxes) if boxes else None


def cam_projection_origin(root, pose):
    """这台相机实际绕着哪一点缩放：`svgOrigin` 优先，否则内容并集中心。

    投影自查和跨段烘焙**必须**用同一个原点算同一个姿态，否则"报出来的画面"和
    "烘焙出来的画面"不是同一幅，warn 就失去意义了（所以这个函数是两边唯一的入口）。
    读不出几何返回 None。
    """
    center = cam_content_center(root)
    if center is None:
        return None
    size = root_viewbox_size(root)
    return _origin_point((pose or {}).get("origin"), center, size[0], size[1])


def cam_default_origin(markup, steps):
    """该页运行期该绕的**缺省**原点，写成 GSAP 的 `svgOrigin` 串；None = 不注入。

    为什么要有这个函数：GSAP 对没写原点的 SVG 元素**不是**绕 bbox 中心缩放。实测
    （GSAP 3.14.2 + 本模板，浏览器里读 `transform` 矩阵反解）它绕的是 `#cam` 自身
    bbox 的**左上角**，而那个角还跟着别的步的 `from` 瞬态走（一个 `from:{x:-420}`
    的入场能把 bbox.x 从 90 拽到 −40）——静态几何无从复现，于是烘焙与出画 warn 算的
    取景和成片里的取景分家，跨段接续页界上留下 (1−s)·Δ 的纯平移阶跃（24~36px 量级）。
    与其猜那个角，不如把它**钉死**：渲染器给这类相机步注入本函数算出的内容中心，
    运行期就照着这个数走，三方（GSAP / `_stage_carry._fold_camera` / 本模块的投影）
    第一次是同一个点。
    """
    pose = cam_resting_pose(steps)
    if pose is None or pose["origin"] is not None:
        return None                        # 没运镜 / 作者自己管原点：一律不插手
    if abs(pose["scale"] - 1.0) < 1e-9:
        return None                        # 只有平移：原点不参与，注入了也没区别
    try:
        root = ET.fromstring(markup or "")
    except ET.ParseError:
        return None
    center = cam_projection_origin(root, pose)
    return None if center is None else "%g %g" % center


def cam_content_center(root):
    """`#cam` 内容并集的几何中心 (cx, cy)；读不出返回 None。

    这个点是**渲染器钉给相机的缺省原点**（见 `cam_default_origin`），GSAP 自己的默认并非如
    此——它绕 bbox 左上角、还跟着 `from` 瞬态飘，所以那个默认我们不认、由本函数出的数顶上。
    注意这是"占位盒"并集：path 连控制点一起算、文字按基线±字号算，与浏览器真 bbox 有偏差——
    偏差只影响 scale≠1 时的绝对取景（误差 ≈(1−s)·Δ中心），推得越近越明显，所以推近幅度别拿
    它当精确对位用。
    """
    got = cam_geometry(root)
    if not got:
        return None
    _vb_w, _vb_h, boxes = got
    xs = [b[1][0] for b in boxes] + [b[1][1] for b in boxes]
    ys = [b[1][2] for b in boxes] + [b[1][3] for b in boxes]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _origin_point(origin, box_center, vb_w, vb_h):
    """svgOrigin → viewBox 坐标下的点；读不出退回本页钉的缺省原点（内容并集中心）。"""
    if origin:
        parts = [p for p in re.split(r"[,\s]+", str(origin).strip()) if p]
        if len(parts) == 2:
            out = []
            for i, p in enumerate((parts[0], parts[1])):
                if p.endswith("%"):
                    n = _parse_num(p.rstrip("%"))
                    if n is None:
                        return box_center
                    base = (vb_w, vb_h)[i]
                    out.append(base * n / 100.0)
                else:
                    n = _parse_num(p)
                    if n is None:
                        return box_center
                    out.append(n)
            return (out[0], out[1])
    return box_center


def crop_warnings(svg_text, steps):
    """返回一组可读的越界说明（空列表 = 没运镜 / 没法算 / 一切在幅内）。

    两类，都只在**收尾位**判定：
      1. 某个元素整体被推到画幅外（看不见的内容）；
      2. 含文字的元素越过上/下边缘（半切）——横向需要文本宽度，这里没有布局引擎，
         宁可不查也不误报。

    跨段延续的接续页要拿**烘焙后的** SVG 调这里（`_stage_carry` 的产物），不能拿盘上的
    原图：原图里没有上一段推近的几何，报出来的"一切在幅内"是假话。
    """
    pose = cam_resting_pose(steps)
    if pose is None:
        return []
    if not pose.get("rests", True):
        return ["这一页有 repeat:-1 的运镜，相机不会停在任何一处——出画自查按'收尾位'判定，"
                "这一页没有收尾位可查。要它演完就写明确遍数（repeat:N），或按最大推近"
                "幅度自己核一遍"]
    if not pose.get("resolved", True):
        # 姿态里有读不出的分量（如 scale:"/=0"，GSAP 运行期算成 Infinity）：手上这组
        # 数字不是真实的收尾位。按它投影只会报出一句"一切在幅内"的假话，所以这里说
        # "没查"，与 repeat:-1 那条同一口径。
        return ["这一页的运镜里有静态几何读不出的分量（如 scale:\"/=0\"，GSAP 运行期"
                "算成 Infinity，元素会被推出画幅）——收尾位算不出来，出画自查这一页"
                "没查。要它可查就把该分量改写成绝对数，或 /=N（N≠0）"]
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return []
    got = cam_geometry(root)
    if not got:
        return []
    vb_w, vb_h, boxes = got
    ox, oy = cam_projection_origin(root, pose)
    s, tx, ty = pose["scale"], pose["x"], pose["y"]

    tol_x, tol_y = vb_w * _EDGE_TOL, vb_h * _EDGE_TOL
    warns = []
    for label, (x0, x1, y0, y1), has_text in boxes:
        px0 = ox + s * (x0 - ox) + tx
        px1 = ox + s * (x1 - ox) + tx
        py0 = oy + s * (y0 - oy) + ty
        py1 = oy + s * (y1 - oy) + ty
        out_left, out_right = px1 < -tol_x, px0 > vb_w + tol_x
        out_top, out_bottom = py1 < -tol_y, py0 > vb_h + tol_y
        if out_left or out_right or out_top or out_bottom:
            side = ("右" if out_right else "左" if out_left
                    else "下" if out_bottom else "上")
            far = max(px0 - vb_w, -px1, py0 - vb_h, -py1)
            warns.append(f"运镜收尾位把 {label} 整个推出画幅{side}边（离边 ≈{far:.0f}"
                         f" 用户单位，scale={s:g}、平移=({tx:g}, {ty:g})）——这一页静止时"
                         "它是不存在的。要么把它挪出 <g id=\"cam\">，要么把相机推得少一点")
            continue
        if has_text:
            cut_top = py0 < -tol_y
            cut_bottom = py1 > vb_h + tol_y
            if cut_top or cut_bottom:
                over = (-py0 if cut_top else py1 - vb_h)
                warns.append(f"运镜收尾位把 {label} 切在画幅{'上' if cut_top else '下'}边"
                             f"外 ≈{over:.0f} 用户单位（scale={s:g}）——文字被切一半比整条消失"
                             "更难被发现；标题/字幕/图例这类文字带请放在 <g id=\"cam\"> 之外")
    return warns
