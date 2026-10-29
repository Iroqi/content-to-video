"""SVG 编排的唯一入口：一个字段 `animate`，一次校验，一次展开。

## 只有一种写法

```json
"animate": {
  "params": {"theta": 0},
  "steps": [{"at": 0, "target": "#a", "to": {"opacity": 1}},
            {"at": 1, "param": "theta", "to": 90}],
  "bind":  [{"target": "#tip", "attr": "cx", "expr": "490+260*cos(theta*pi/180)"}]
}
```

`steps` 里的一拍二选一：

- 写 `target`：属性补间（`to` / `from` / `set` / `draw` / `morph` / `count` / `type`）；
- 写 `param`：把声明过的参数推到某个值，`bind` 里的表达式按参数**逐帧重算**几何。

## 为什么不分成三套

原来的 `director` / `evolve` / `derive` 说的是同一件事：某一拍让图里的东西变成什么
样。真正的差别只有一处——**续演到下一段**要搬运上一段收尾态，这要求整页重绘；它由
版式决定（满幅页），不是作者要选的写法。其余差别都能由判据推出：版面是作者的（满幅）
就让作者调机位，版面是模板的（槽位）就拒；元素没声明 `opacity` 就从 1 起算。

## 静 = 不写 animate

不写 `animate` 的图是静图（`<img>`）。"先静后动"只需把第一拍推到那句话上。
"""
import xml.etree.ElementTree as ET

from _svg_sanitize import _local, _style_map  # noqa: E402  SVG DOM 的读法只有一份

# 相机写法：只有满幅版式（版面归作者）能写。契约层与渲染端读同一份。
CAMERA_KEYS = frozenset({"svgOrigin", "transformOrigin", "scale", "rotation",
                         "rotationX", "rotationY", "x", "y"})
# 属性补间里可以带相机键的位置（`to`/`from`/`set` 是补间变量表）。
_TWEEN_KEYS = ("to", "from", "set")

# 只给"结构底"用的标签：不承载内容，不参与"起点态兜底"，也不进"可渲染元素"。
_STRUCTURAL_TAGS = frozenset({"svg", "defs", "title", "desc", "metadata", "style",
                              "clippath", "mask", "symbol", "marker", "pattern",
                              "lineargradient", "radialgradient", "filter"})


def is_motion(entry):
    """这条条目要不要"内联 + 补间"（而不是当静态 `<img>`）。"""
    return bool(steps_of(entry))


def steps_of(entry):
    """从 images.json 条目（或它的 opts）里取 steps；没有就 []。"""
    if not isinstance(entry, dict):
        return []
    return list(entry.get("steps") or [])


def animate_of(entry):
    """条目里的 animate 块（允许省略 `"animate": {...}` 这层壳，直接写 steps）。"""
    if not isinstance(entry, dict):
        return {}
    block = entry.get("animate")
    if isinstance(block, dict):
        return block
    if "steps" in entry:
        return {k: entry[k] for k in ("steps", "params", "bind") if k in entry}
    return {}


def selectors_of(entry):
    """这条编排会改写的选择器（`#id` / `.class`）。"""
    out = []
    for st in steps_of(entry):
        if isinstance(st, dict) and isinstance(st.get("target"), str):
            out.append(st["target"])
    for b in animate_of(entry).get("bind") or []:
        if isinstance(b, dict) and isinstance(b.get("target"), str):
            out.append(b["target"])
    return out


# ── 起点态 ────────────────────────────────────────────────────────────
# 槽位页落位那半秒里标题、句子流、配图都在入场，"图里所有东西从 opacity 0 起步"
# 会让配图落位那一帧是一块空框。所以只写 `to` 的补间从元素**当前值**起补（GSAP
# 缺省语义），这里不补 `from`——只负责把"整页都藏着又没人点亮"这种真·空框报出来。


def _declared_opacity(el):
    """元素静态声明的 opacity（含 style 里那一份）；没声明返回 None。"""
    raw = el.get("opacity")
    if raw is None:
        raw = _style_map(el).get("opacity")
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _starts_hidden(el):
    """静态声明里就不可见（写死 opacity<=0）。

    "没声明"在渲染上等于 1，必须与 0 分开——写成 `(declared or 0) <= 0` 会把任何
    没写 opacity 的元素都算成隐藏，守卫就从"抓全空页"变成"抓每一页"。
    """
    o = _declared_opacity(el)
    return o is not None and o <= 0


def _points_early(step, limit=0.2):
    """这一拍会不会落在段首（< limit 秒）。

    两个锚点不是一个量纲：`at` 是句序（`at=0` 就是段首），`at_time` 才是该段绝对
    秒。相对串 `"+0.5"` 要求出前一拍才知道值，静态自查求不出——按"不算早"处理，
    宁可漏报也不误报。
    """
    if not (step.get("set") or step.get("to") or step.get("from")):
        return False
    if "at" in step:
        return float(step["at"]) == 0
    if "at_time" in step:
        v = step["at_time"]
        if isinstance(v, str):
            return False
        return float(v) < limit
    return False


# ── 生成期体检：读 SVG 源文本 ─────────────────────────────────────────


def _selector_universe(root):
    """图上所有可被 `#id` / `.class` 命中的选择器。"""
    out = set()
    for el in root.iter():
        eid = el.get("id")
        if eid:
            out.add("#" + eid)
        for c in (el.get("class") or "").split():
            out.add("." + c)
    return out


def _painter_elements(root):
    """能在画面上画出东西的元素（排掉结构件与它们的后代，且必须有 id/class）。

    必须按**祖先**排除，不能只判自己：`<defs>` 里的 `<linearGradient>` 自己不在
    结构件名单里，进了"可渲染"却永远不会有 opacity=0，而真实 SVG 几乎都带
    `<defs>`——这条守卫此前等于从未生效。
    """
    parent = {c: p for p in root.iter() for c in p}

    def inside_container(el):
        p = parent.get(el)
        while p is not None and p is not root:
            if _local(p.tag) in _STRUCTURAL_TAGS:
                return True
            p = parent.get(p)
        return False

    return [el for el in root.iter()
            if el is not root
            and (el.get("id") or el.get("class"))
            and _local(el.tag) not in _STRUCTURAL_TAGS
            and not inside_container(el)]


def check_animate(raw, block, sid, *, full_bleed):
    """编排的静态体检，返回 (errors, warnings)。只读 SVG 源文本，生成期跑。

    四条判据都是"改了但看不见"或"写了但不会发生"：

    1. **target 落空**（error）：选择器匹配不到 = 静默没动画，成片里看着"没动"。
    2. **槽位页调机位**（error）：镜头归模板，图内再推一次是两层意图打架。
    3. **起点态全空**（warn）：所有可渲染元素都写死 `opacity=0`，又没有任何一拍在
       段首点亮——配图落位那一帧是一块空框。
    4. **可演元素为零**（warn）：整页没有带 id/class 的元素，编排无从落子。
    """
    errs, warns = [], []
    steps = list(block.get("steps") or [])
    if not steps:
        return errs, warns
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return errs, warns          # 解析不了由净化那一步报，这里不重复
    if _local(root.tag) != "svg":
        return errs, warns
    universe = _selector_universe(root)

    for i, step in enumerate(steps):
        target = step.get("target")
        if target is not None and target not in universe:
            errs.append(
                f"段落 '{sid}' 的 animate.steps[{i}] target={target} 在 SVG 里找不到"
                f"对应 id/class 的元素——选择器落空不会报错，只会静默没有动画")
        if full_bleed:
            continue
        # 参数推进的拍（`param` + 数值 `to`）没有补间变量表，先看形状再取键。
        for k in _TWEEN_KEYS:
            tween = step.get(k)
            if not isinstance(tween, dict):
                continue
            cam = sorted(CAMERA_KEYS & set(tween))
            if cam:
                errs.append(
                    f"段落 '{sid}' 的 animate.steps[{i}] 写了 {'、'.join(cam)}："
                    f"槽位版式的镜头归模板（入场收拢），图内不调机位。要整页跟着"
                    f"旁白推镜，把这段换成 layout:\"canvas\"")

    painter = _painter_elements(root)
    if not painter:
        warns.append(
            f"段落 '{sid}' 的图里没有任何带 id/class 的元素——animate 无从落子")
        return errs, warns
    if not any(_points_early(st) for st in steps):
        hidden = [el for el in painter if _starts_hidden(el)]
        if len(hidden) == len(painter):
            warns.append(
                f"段落 '{sid}' 的图起始帧全空：{len(painter)} 个可渲染元素全部写死 "
                f"opacity=0，而 animate 没有任何一拍在段首点亮它们。配图落位那一帧会是"
                f"一块空框——让结构底直接可见，或把第一拍提到段首")
    return errs, warns
