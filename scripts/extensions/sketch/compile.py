"""sketch：画风后处理参考扩展包（L1 的第一个可运行切片）。

契约见 references/extensions.md。这里只做一件事：给一份**已经画好的标准 SVG**
套上纯 SVG 画风滤镜，产出仍是标准 SVG，`images.json` 与 `animate.steps` 一行
都不用改。

三条自律（由 scripts/_extensions.py 在加载时机器校验）：

- 纯函数：不读墙钟、不用随机、不开文件、不联网、不写全局状态。seed 由作者
  显式给，同一份 spec 两次编译逐字节一致。
- 只改外观：`#id` 集合、几何坐标、`<text>` 内容一律不动。编排按 #id 寻址，
  动了身份就会让 steps 静默落空。
- 不用外链：`url(#ctv-ext-sketch)` 是内部片段引用，净化器不拦；渲染端仍是
  离线可跑的活 DOM。

Python 3.9 兼容，只用标准库。
"""

import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
FILTER_ID = "ctv-ext-sketch"


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _num(value, where, lo, hi, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s 必须是数字（实际: %r）" % (where, value))
    value = float(value)
    if not lo <= value <= hi:
        raise ValueError("%s 必须在 %g~%g 之间（实际: %g）" % (where, lo, hi, value))
    return value


def compile(spec, ctx):  # noqa: A001 —— 契约要求的函数名，遮蔽内置 compile 是有意的
    """spec → {"svg": "..."}; ctx 首版不用，留作未来传画幅/主题的入口。"""
    presets = ("sketch", "rough", "pixel")
    spec_keys = ("svg", "preset", "seed", "intensity", "targets")
    extra = sorted(set(spec) - set(spec_keys))
    if extra:
        raise ValueError("spec 含未登记的键：%s" % "、".join(extra))
    svg = spec.get("svg")
    if not isinstance(svg, str) or not svg.strip():
        raise ValueError("spec.svg 必须是非空字符串")
    preset = spec.get("preset", "sketch")
    if preset not in presets:
        raise ValueError("preset 只收 %s（实际: %r）"
                         % (" / ".join(presets), preset))
    seed = spec.get("seed", 7)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 9999:
        raise ValueError("seed 必须是 0~9999 的整数（实际: %r）" % (seed,))
    intensity = _num(spec.get("intensity"), "spec.intensity", 0.0, 1.0, 0.5)
    targets = spec.get("targets", [])
    if not isinstance(targets, list) or any(not isinstance(t, str) for t in targets):
        raise ValueError("spec.targets 必须是字符串数组")
    wanted = set(t.lstrip("#") for t in targets)

    ET.register_namespace("", SVG_NS)
    root = ET.fromstring(svg)
    if _local(root.tag) != "svg":
        raise ValueError("spec.svg 的根节点必须是 <svg>")

    # defs 已存在就复用，没有就插在最前（滤镜定义不参与 xlink 寻址）。
    defs = None
    for child in root:
        if _local(child.tag) == "defs":
            defs = child
            break
    if defs is None:
        defs = ET.Element("{%s}defs" % SVG_NS)
        root.insert(0, defs)
    # 重复编译同一份产物时不叠加滤镜定义：按 id 找到旧的那条就替换。
    for old in list(defs):
        if old.get("id") == FILTER_ID:
            defs.remove(old)

    def q(tag):
        return "{%s}%s" % (SVG_NS, tag)

    if preset in ("sketch", "rough"):
        scale = 2.0 + 6.0 * intensity if preset == "sketch" else 4.0 + 10.0 * intensity
        freq = 0.02 + 0.03 * intensity if preset == "sketch" else 0.05 + 0.05 * intensity
        filt = ET.SubElement(defs, q("filter"), {
            "id": FILTER_ID, "x": "-20%", "y": "-20%",
            "width": "140%", "height": "140%",
        })
        ET.SubElement(filt, q("feTurbulence"), {
            "type": "fractalNoise", "baseFrequency": "%.4f" % freq,
            "numOctaves": "2" if preset == "sketch" else "3",
            "seed": str(seed), "result": "noise",
        })
        ET.SubElement(filt, q("feDisplacementMap"), {
            "in": "SourceGraphic", "in2": "noise",
            "scale": "%.2f" % scale,
            "xChannelSelector": "R", "yChannelSelector": "G",
        })
        ref_attr, ref_val = "filter", "url(#%s)" % FILTER_ID
    else:
        # 色阶量化：把连续色压成几档，读起来是海报/像素感。纯 filter，无外链。
        steps_n = 3 + int(round(5 * intensity))
        table = " ".join("%.2f" % (i / float(steps_n - 1))
                         for i in range(steps_n))
        filt = ET.SubElement(defs, q("filter"), {
            "id": FILTER_ID, "x": "0%", "y": "0%",
            "width": "100%", "height": "100%",
        })
        transfer = ET.SubElement(filt, q("feComponentTransfer"))
        for channel in ("feFuncR", "feFuncG", "feFuncB"):
            ET.SubElement(transfer, q(channel), {
                "type": "discrete", "tableValues": table,
            })
        ref_attr, ref_val = "filter", "url(#%s)" % FILTER_ID

    # 只给"内容图形"套画风，跳过 defs / text / 结构底。targets 显式指定时
    # 只认这些 id；没给就覆盖所有带 id 的图形元素。
    skip = ("defs", "text", "tspan", "filter", "pattern", "lineargradient",
            "radialgradient", "mask", "clippath", "style", "title", "desc")
    touched = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag in skip or el is root:
            continue
        eid = el.get("id")
        if eid is None:
            continue
        if wanted and eid not in wanted:
            continue
        el.set(ref_attr, ref_val)
        if preset == "pixel":
            el.set("shape-rendering", "crispEdges")
        touched.append(eid)
    if wanted:
        missing = sorted(wanted - set(touched))
        if missing:
            raise ValueError("spec.targets 里这些 id 在 SVG 里找不到：%s"
                             % "、".join(missing))

    return {"svg": ET.tostring(root, encoding="unicode", short_empty_elements=True)}
