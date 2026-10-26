"""槽位版式 SVG 的「同一元素演进」：讲到哪儿，那张图就跟着变到哪儿。

## 为什么需要它

槽位版式（不写 `layout`）的配图默认是 `<img>`。不进导演档时，一张示意图要么
静态，要么只有 CSS/SMIL 氛围循环——**旁白讲到第三个状态，图还停在第一个状态**。
观众听到的"从 X 变成 Y"在画面上没有任何对应动作，这一屏就只剩字幕在推进。

导演档能演，但它要的是整页画布（标题层、句子流层都不生成），代价对"只想让图
跟着变"的段落太大：字幕没了、标题得自己画。所以这里开第三条路——**留在槽位、
留在 `<img>` 之外，只让图里的元素按句子演进**：

- images.json 写 `"evolve": {"steps": [...]}`，写法和 `director.steps` 一样
  （`at` / `at_time` / `set` / `to` / `from` / `stagger` / `duration` / `ease`）。
- 与 `director` 的区别只有两条，都来自"版式不是作者画的"这个前提：
  1. **不在图里调机位**。槽位版式的镜头归模板（入场收拢 + Ken Burns 运镜），
     作者再推一次就是两层意图打架。`svgOrigin` / `scale` 这类相机写法不在此档。
  2. **起点态自动兜底**（见下）。
- 有 `evolve` 的图照旧净化内联（同样的安全清单），但**不接管整页编排**：
  标题层、句子流层、配图入场、Ken Burns 全部照旧，只是图内元素多了一条补间。

## 起点态：作者只写"变到哪儿"

槽位版式的画面上半秒就有标题、句子流和配图在入场，"图里所有东西从 opacity 0
起步"会让配图落位那一帧是空的——比不动还差。所以 `evolve` 的默认语义是
**"结构底就位、内容从初态长到终态"**：

- `to` 的目标：若 SVG 里该元素**没有写** `opacity`（缺省即可见）而补间想改它，
  起点按可见算（`1`），不是 0。换句话说 `{"to": {"opacity": 0.35}}` 读作
  "从当前可见**压暗到** 0.35"，不是"从无到有"。
- 真要从无到有就显式写 `from`：`{"from": {"opacity": 0}, "to": {"opacity": 1}}`。
- 作者仍可在 SVG 里把内容写成 `opacity="0"`，那是"整段看不见、等着被点亮"的
  显式意图，`evolve` 不去覆盖它。

## 与 `director` / `stage: keep` 的边界

- `evolve` **按段独立**：每段各自从同一张图的静态初态起演，不跨段接续。
  跨段接续（`stage: "keep"`）是导演档的能力，它要求整页画布，不进这一档。
- 同一段既写 `director` 又写 `evolve` 在契约层报错（一个是整页编排，一个是
  槽位内演进，同时写等于两个作者抢同一张图）。
"""
import xml.etree.ElementTree as ET

# 槽位版式不接受的相机属性：镜头归模板（入场收拢 + Ken Burns），作者再推一层
# 就是两条运镜叠在同一张图上，观众读到的是"图在自己乱动"。
# `scale` 也在列：契约层按这个元组判，生成期再按它挡一次，两份必须同一份。
CAMERA_KEYS = ("svgOrigin", "transformOrigin", "scale", "xPercent", "yPercent")

# 只给"结构底"用的标签：这些元素本来就不承载内容，不参与"起点态兜底"。
_STRUCTURAL_TAGS = ("svg", "defs", "title", "desc", "metadata", "style")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _iter_targets(root):
    """id → element（只收可被 `#id` 命中的元素）。"""
    out = {}
    for el in root.iter():
        eid = el.get("id")
        if eid:
            out.setdefault(eid, el)
    return out


def _declared_opacity(el):
    """元素自己写死的 opacity（含 `style` 里的那一份）；不继承、不算动画。

    读的是**静态声明**：作者在 SVG 里写 `opacity="0"` 就是"这段先不显示"，
    `style="opacity:.4"` 同义。缺省返回 None（= 没声明，渲染上等于 1）。
    """
    raw = el.get("opacity")
    if raw is None:
        style = el.get("style") or ""
        for decl in style.split(";"):
            k, _, v = decl.partition(":")
            if k.strip().lower() == "opacity":
                raw = v.strip()
                break
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def check_evolve(raw, steps, sid):
    """`evolve` 的静态体检，返回 (错误列表, 警告列表)。

    只在生成期跑（要读 SVG 源文本），与 `director_prepare` 同一类判据：

    - **target 落空**（error）：选择器匹配不到 = 静默无动画，成片里看着"没动"
      却零提示，和 director 那条同一个病。
    - **不再调机位**（error）：`svgOrigin` / `scale` 等写进槽位演进里——镜头归
      模板，这条挡的是"作者以为自己在做运镜"。
    - **起始帧全空**（warn）：这一页的图里所有元素都写死 `opacity=0`，又没有一步
      在段首把它们点亮——配图落位那一帧是一块空框。
    """
    errs, warns = [], []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return errs, warns          # 解析不了由净化那一步报，这里不重复
    if _local(root.tag) != "svg":
        return errs, warns
    targets = _iter_targets(root)
    selectors = set("#" + k for k in targets)
    for el in root.iter():
        cls = el.get("class")
        if cls:
            selectors.update("." + c for c in cls.split())

    for i, step in enumerate(steps):
        target = step.get("target")
        if target not in selectors:
            errs.append(
                f"段落 '{sid}' 的 evolve.steps[{i}] target={target} 在 SVG 里找不到"
                f"对应 id/class 的元素——选择器落空不会报错，只会静默没有动画")
        for key in CAMERA_KEYS:
            if key in (step.get("to") or {}) or key in (step.get("from") or {}) \
                    or key in (step.get("set") or {}):
                errs.append(
                    f"段落 '{sid}' 的 evolve.steps[{i}] 写了 {key}：槽位版式的镜头归"
                    f"模板（入场收拢 + 运镜），图内不再调机位。要让整页跟着旁白推镜，"
                    f"把这段换成 layout:\"canvas\" 写 director")

    # 起始帧全空：所有可渲染元素都声明 opacity=0，且没有任何一步在段首（<0.2s）
    # 把它们点亮。判据只读静态声明，宁可多报一条也不让空框静默出货。
    painter = [el for el in root.iter()
               if el is not root and _local(el.tag) not in _STRUCTURAL_TAGS]
    lit_early = any(
        float(step.get("at", 0) if "at" in step else 0) < 0.2
        and (step.get("set") or step.get("to") or step.get("from"))
        for step in steps)
    if painter and not lit_early:
        hidden = [el for el in painter
                  if (_declared_opacity(el) or 0) <= 0 and el.get("id")]
        if len(hidden) == len(painter):
            warns.append(
                f"段落 '{sid}' 的演进图起始帧全空：SVG 里 {len(painter)} 个可渲染元素"
                f"全部写死 opacity=0，而 evolve 没有任何一拍在段首把它们点亮。"
                f"配图落位那一帧会是一块空框——让结构底直接可见，或把第一拍提前到段首")
    return errs, warns
