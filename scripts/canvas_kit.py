#!/usr/bin/env python3
"""整页画布（layout:"canvas"）那页 SVG 的脚手架：一份 spec.json → 一张按画幅原生分辨率画的画布页 + 一份 director 草稿。

为什么需要它：画布页是目前最贵的一档。按 1080×1440 原生手画、十几个轴刻度逐一定位，
而导演层还要求内容元素第 0 帧就写 opacity="0"，于是每一页都在重复同一段机械劳动。
本模块收走的正是机械的部分（画布几何、三档字号、取色、初始态、节拍编排），**构图仍归人**：
所有坐标由 spec 给，脚本不做任何自动布局——那是版式引擎，不是脚手架。

用法：
    python scripts/canvas_kit.py --spec spec.json --aspect portrait -o seg1.svg [--emit-steps steps.json]

两份产物配套消费：
  - seg1.svg：透明底（绝不自铺满幅底板；模板的渐变/网格/氛围光三层要从内容背后透出来）
  - stdout（或 steps.json）：{"steps": […]}，整块塞进 images.json 该段条目的 "director" 就能用

三条决定成败的隐藏约束（代码里各有对应动作）：
  - **谁能藏、谁不能藏**：结构底随页面一起到位（不写 opacity）；内容元素靠导演逐拍点亮，
    所以写 opacity="0"。但 draw/count/type 这三种"演"的起始态由渲染端自己配（dash 收起 /
    显示 from 值 / 文本清空），再给它们写 opacity="0" 就永远不会亮。
  - **锚点只用 at（句序），不用 at_time**：绝对秒是按某一版配音手算的，重配音后整体漂移
    且不报错；at 跟着 manifest 的句子走，改配音自动对得上。
  - **输出必须过 check_svg 的 canvas 门禁**：空带 / 对比度 / 字号三条都在它那里判，所以生成后
    照它同一口径自查一遍（判据不重抄一份——抄一份就会漂）。

公共旋钮 duration / ease / stagger / repeat / yoyo 都能写进内容元素，草稿原样带出；
repeat/yoyo 的取值与互斥校验跟 _images_schema 同一套（yoyo 必须带 repeat、只挂内容元素），
跨键约束（如 morph 不能配 repeat:-1）由 selfcheck 过真契约时兜底。
"""
import argparse
import json
import os
import sys
from collections import OrderedDict
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_svg  # noqa: E402  门禁判据以它为准，自查直接调它而不是重算一遍
from _images_schema import (_DIRECTOR_TARGET_RE, _validate_count,  # noqa: E402
                            _validate_stagger, validate_images_json)
from _script_utils import read_json_file  # noqa: E402
from _template import get_canvas, load_template, normalize_aspect  # noqa: E402
from _theme import (DEFAULT_THEME, contrast_ratio, css_color_to_hex,  # noqa: E402
                    ensure_text_contrast, get_accent_palette,
                    get_default_accent, get_theme_colors, mix, theme_bg_stops)
from _validate import _reject_unknown_keys, _validate_accent  # noqa: E402
from _validate import _validate_finite_number  # noqa: E402

_TPL = load_template()
ASPECTS = ("portrait", "landscape")

# ── 尺寸真源：模板里已有的键，本模块一个 px 字面量都不写 ──────────────
# 画布尺寸 = 那一页的原生分辨率（缩放凑不出来，见 check_svg 的比例校验）。
# 三档字号 = layout.<画幅>.{title,subtitle,tagline}.fontSize：画布页没有模板标题层，
#   图内文字是唯一的字，主次就靠这三档拉开（title 档另有模板层用，画布上归图内大标题）。
# 线宽 = layout.<画幅>.progressBar.height：页面上唯一另一处"一条实线该多粗"的既有决定，
#   跟着它走，画布上的轴线就和进度条同一份量；竖屏 4px / 横屏 5px 也都够过压缩。
# 动画缺省 = animation.director：steps 里不写 duration/ease 时渲染端用的就是它，
#   所以草稿只在作者显式写了、且与缺省不同值时才把这两个键印出来。
TIERS = {a: OrderedDict((("title", _TPL["layout"][k]["title"]["fontSize"]),
                         ("body", _TPL["layout"][k]["subtitle"]["fontSize"]),
                         ("label", _TPL["layout"][k]["tagline"]["fontSize"])))
         for a, k in zip(ASPECTS, ("vertical", "landscape"))}
LINE_W = {a: _TPL["layout"][k]["progressBar"]["height"]
          for a, k in zip(ASPECTS, ("vertical", "landscape"))}
DIRECTOR_DEFAULT = _TPL["animation"]["director"]
# 面板圆角 = 模板给该画幅配图槽定的那颗圆角：画布页上的局部底板和槽位配图理应用同一个
# 倒角语言，别在这里再定一个"看起来差不多"的数。
PANEL_R = {a: _TPL["layout"][k]["image"]["borderRadius"]
           for a, k in zip(ASPECTS, ("vertical", "landscape"))}

# 只允许"相对模板量的比值"，不许写 px：这三个比决定局部底板的明度台阶、刻度线长、
# 以及次要文字的压暗幅度，改它们改的是观感层级，不是某个具体像素位置。
PLATE_FRAC = 0.14    # 局部底板 = 页底中段往文字色偏这么多档（浮在页底之上、又不至于像新底色）
DIM_FRAC = 0.6       # 次要文字 = 文字色往页底压一档，再按 7:1 兜底
LINE_FRAC = 0.45     # 轴线/刻度线 = 比次要文字再暗一档（线要的是"在"，不是"抢眼"）
TICK_FRAC = 0.5      # 刻度线长 = label 档字号的一半
TEXT_BASE_FRAC = 0.35  # 竖排刻度 label 的基线修正：让字身中线对齐刻度而不是基线对齐
# 「满幅」的判据不在这里定义：check_svg.is_full_bleed 是唯一一份，本模块调它。
# 这一版之前这里是手抄的 0.98 字面量，两处已经漂移过一次（那边注释写"宽或高"，
# 两边代码都是 and），而 97.8% 宽的底板能静默溜过去。
# 次要文字的对比度目标：check_svg 的门禁线是 4.5，但画布页的小字还要经两画幅缩放和成片
# 压缩编码，取色表那条"次要说明按 ≥7:1 取"就是为了留出这两档损耗。
DIM_CONTRAST_TARGET = 7.0
# 线/图形的可见性下限：check_svg 给图内大字判的就是 3:1，而线比字更经不起两画幅缩放
# 与压缩编码，所以低于这一档基本读不出来（浅色页底上中等亮度的 accent 同样落进来）。
LINE_CONTRAST_FLOOR = 3.0

# 元素 kind → 该 kind 允许的键（键集封闭；公共键另见 COMMON_KEYS）。
ELEMENT_KEYS = {
    "panel": ("x", "y", "w", "h", "r", "fill", "opacity", "id"),
    "axis": ("id", "orient", "at", "from", "to", "ticks", "label_side"),
    "polyline": ("id", "points", "stroke", "width", "fill", "draw"),
    "bars": ("id", "base_y", "bars", "fill"),
    "circle": ("id", "cx", "cy", "r", "fill"),
    "text": ("id", "x", "y", "tier", "content", "fill", "anchor", "type", "count"),
    "rule": ("id", "x1", "y1", "x2", "y2", "stroke", "width", "draw"),
}
COMMON_KEYS = ("kind", "role", "beat", "duration", "ease", "stagger",
               "repeat", "yoyo")
# 每个 kind 必填的纯数值键，进函数就按这张表统一查（报错口径全 kind 一致）。
# polyline 没有数值标量，点在 points 数组里，由它自己那条结构校验管。
REQUIRED_NUMS = {
    "panel": ("x", "y", "w", "h"),
    "axis": ("at", "from", "to"),
    "polyline": (),
    "bars": ("base_y",),
    "circle": ("cx", "cy", "r"),
    "text": ("x", "y"),
    "rule": ("x1", "y1", "x2", "y2"),
}
# 数值键里额外要"必须为正"的那些：半径为 0 或负是个看不见的点，宽高为 0 或负是个不存在
# 的面板/竖不起来的柱——在 spec 门口就拦掉，渲染层不必再各查一遍。
POSITIVE_NUMS = {("circle", "r"), ("panel", "w"), ("panel", "h")}
# 结构底默认档：面板与坐标轴是"页面一出现就该在"的东西，其余是要逐拍长出来的内容。
STRUCTURE_BY_DEFAULT = ("panel", "axis")
TOP_KEYS = ("aspect", "theme", "accent", "elements")
DEFAULT_ASPECT = "portrait"
TICK_KEYS = ("v", "label", "unit")
BAR_KEYS = ("x", "w", "h", "label")
LABEL_SIDES = {"x": ("above", "below"), "y": ("left", "right")}
DEFAULT_LABEL_SIDE = {"x": "below", "y": "left"}

# check_svg 对画布档无条件打的那一条是"知情提示"（说它查了什么、还剩什么查不到），
# 不是待修的问题；自查把它排除，否则每一页都自带一条永远消不掉的噪声。
_STANDING_ADVISORY = "字面色的对比度已经按主题页底的最坏一档查过"


# ── 数值与转义 ────────────────────────────────────────────────
def _num(v):
    """坐标落进 SVG 的口径：整数值不带小数点，分数值保留三位（产物可读、diff 稳定）。"""
    f = float(v)
    if f == int(f):
        return str(int(f))
    return ("%.3f" % f).rstrip("0").rstrip(".")


def _attr_text(s):
    return escape(str(s), {'"': "&quot;"})


def _node(tag, attrs, kids=None, text=None, raw=False):
    """SVG 片段的最小树：属性按写入顺序出（None 值不落笔），None/文本/子节点三选一。"""
    return {"tag": tag,
            "attrs": [(k, v) for k, v in attrs.items() if v is not None],
            "kids": kids or [], "text": text, "raw": raw}


def _dump(node, depth=0):
    pad = "  " * depth
    a = "".join(' {}="{}"'.format(k, _attr_text(v)) for k, v in node["attrs"])
    tag = node["tag"]
    if not node["kids"]:
        if node["text"] is None:
            return f"{pad}<{tag}{a}/>"
        body = node["text"] if node["raw"] else escape(str(node["text"]))
        return f"{pad}<{tag}{a}>{body}</{tag}>"
    inner = "\n".join(_dump(k, depth + 1) for k in node["kids"])
    return f"{pad}<{tag}{a}>\n{inner}\n{pad}</{tag}>"


# ── spec 契约：键集封闭 + 取值校验（fail-fast，不静默丢键）──────────
def _need_num(el, key, where, *, nonnegative=False, positive=False):
    v = el.get(key)
    if v is None:
        raise ValueError(f"{where} 缺少必填几何字段 {key!r}")
    _validate_finite_number(v, f"{where} 的 {key}",
                            nonnegative=nonnegative, positive=positive)
    return float(v)


def _opt_num(el, key, where, default, *, nonnegative=False, positive=False):
    if el.get(key) is None:
        return default
    return _need_num(el, key, where, nonnegative=nonnegative, positive=positive)


def _need_str(el, key, where):
    v = el.get(key)
    if v is None:
        raise ValueError(f"{where} 缺少必填字段 {key!r}")
    if not isinstance(v, str):
        raise ValueError(f"{where} 的 {key} 必须是字符串（实际: {type(v).__name__}）")
    return v


def _choice(value, allowed, where, what):
    if value not in allowed:
        raise ValueError(f"{where} 的 {what}={value!r} 不是合法取值：只接受 "
                         f"{'、'.join(repr(a) for a in allowed)}")
    return value


def _validate_element(el, i):
    """单个元素的键集与取值；返回校验过的 dict（原样返回，解析留给渲染层）。"""
    where = f"spec 的 elements[{i}]"
    if not isinstance(el, dict):
        raise ValueError(f"{where} 必须是对象（{{\"kind\": …, …}}）")
    kind = el.get("kind")
    if kind not in ELEMENT_KEYS:
        raise ValueError(f"{where} 的 kind={kind!r} 不是已知元素类型：只接受 "
                         f"{'、'.join(sorted(ELEMENT_KEYS))}")
    where = f"{where}（kind={kind!r}）"
    _reject_unknown_keys(el, ELEMENT_KEYS[kind] + COMMON_KEYS, where)
    for k in REQUIRED_NUMS[kind]:
        _need_num(el, k, where, positive=(kind, k) in POSITIVE_NUMS)
    if "role" in el:
        _choice(el["role"], ("structure", "content"), where, "role")
    if "beat" in el:
        _validate_finite_number(el["beat"], f"{where} 的 beat", nonnegative=True)
    if "duration" in el:
        _validate_finite_number(el["duration"], f"{where} 的 duration", nonnegative=True)
    if "ease" in el and not isinstance(el["ease"], str):
        raise ValueError(f"{where} 的 ease 必须是字符串（GSAP 缓动名，如 power2.inOut）")
    if "stagger" in el:
        _validate_stagger(el["stagger"], f"{where} 的 stagger")
    # repeat / yoyo 与 _images_schema 同一套判据（那里管 images.json 的 director，
    # 这里管 spec）：整数遍数、yoyo 必须是 JSON 布尔、yoyo 必须有 repeat。
    # 另外只挂内容元素——结构底不进 steps，写了等于没写，且没有"哪一拍"可反复。
    if "repeat" in el:
        v = el["repeat"]
        if isinstance(v, bool) or not isinstance(v, int) or v < -1:
            raise ValueError(f"{where} 的 repeat 必须是 ≥-1 的整数（-1=无限循环，"
                             f"0=只演一遍；实际: {v!r}）")
    if "yoyo" in el:
        if not isinstance(el["yoyo"], bool):
            raise ValueError(f"{where} 的 yoyo 必须是 JSON 布尔 true/false"
                             f"（实际: {el['yoyo']!r}）")
        if not el.get("repeat"):
            raise ValueError(f"{where} 写了 yoyo 但 repeat 缺省或 0——只演一遍谈不上 "
                             "回头。要来回就写 repeat:1（奇数遍收尾在 to，"
                             "偶数遍收尾回起点）")
    if (el.get("repeat") is not None or el.get("yoyo")) and role_of(el) != "content":
        raise ValueError(f"{where} 写了 repeat / yoyo，但它不是内容元素——结构底随页面"
                         "一起到位、不进 director steps，没有'哪一拍'可反复。"
                         "要反复的是内容元素")

    if kind == "panel":
        _opt_num(el, "r", where, None, nonnegative=True)
        _opt_num(el, "opacity", where, None, nonnegative=True)
    elif kind == "axis":
        _choice(_need_str(el, "orient", where), ("x", "y"), where, "orient")
        if el.get("label_side") is not None:
            sides = LABEL_SIDES[el["orient"]]
            _choice(el["label_side"], sides, where, "label_side")
        ticks = el.get("ticks")
        if not isinstance(ticks, list) or not ticks:
            raise ValueError(f"{where} 的 ticks 必须是非空数组 "
                             '[{"v": 数值, "label"?: "文本", "unit"?: "…"}]')
        for j, t in enumerate(ticks, 1):
            tw = f"{where} 的 ticks[{j}]"
            if not isinstance(t, dict):
                raise ValueError(f"{tw} 必须是对象 {{v, label?, unit?}}")
            _reject_unknown_keys(t, TICK_KEYS, tw)
            _need_num(t, "v", tw)
            for k in ("label", "unit"):
                if k in t and not isinstance(t[k], str):
                    raise ValueError(f"{tw} 的 {k} 必须是字符串")
    elif kind == "polyline":
        pts = el.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            raise ValueError(f"{where} 的 points 必须是至少两个点的数组 "
                             "[[x, y], …]（点少于两个连不成线）")
        for j, p in enumerate(pts, 1):
            if (not isinstance(p, (list, tuple)) or len(p) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in p)):
                raise ValueError(f"{where} 的 points[{j}] 必须是两个数值 [x, y]（实际: {p!r}）")
            _validate_finite_number(p[0], f"{where} 的 points[{j}][0]")
            _validate_finite_number(p[1], f"{where} 的 points[{j}][1]")
        _opt_num(el, "width", where, None, positive=True)
        if "draw" in el and not isinstance(el["draw"], bool):
            raise ValueError(f"{where} 的 draw 必须是 JSON 布尔 true/false"
                             f"（实际: {el['draw']!r}）")
    elif kind == "bars":
        bars = el.get("bars")
        if not isinstance(bars, list) or not bars:
            raise ValueError(f"{where} 的 bars 必须是非空数组 "
                             '[{"x": …, "w": …, "h": …, "label"?: "文本"}]')
        for j, b in enumerate(bars, 1):
            bw = f"{where} 的 bars[{j}]"
            if not isinstance(b, dict):
                raise ValueError(f"{bw} 必须是对象 {{x, w, h, label?}}")
            _reject_unknown_keys(b, BAR_KEYS, bw)
            for k in ("x", "w", "h"):
                _need_num(b, k, bw, positive=k in ("w", "h"))
            if "label" in b and not isinstance(b["label"], str):
                raise ValueError(f"{bw} 的 label 必须是字符串")
    elif kind == "text":
        if el.get("tier") is not None:
            _choice(el["tier"], tuple(TIERS[DEFAULT_ASPECT]), where, "tier")
        if el.get("anchor") is not None:
            _choice(el["anchor"], ("start", "middle", "end"), where, "anchor")
        if "type" in el and not isinstance(el["type"], bool):
            raise ValueError(f"{where} 的 type 必须是 JSON 布尔 true/false"
                             "（逐字揭示的字取自 content，快慢用 duration/ease）")
        if "count" in el:
            # count 与 content 互斥：数字节点的初始文本就是 from 那一档的渲染值，
            # 另给一句 content 等于让屏幕上先印一个和滚动无关的数（frame 0 撒谎）。
            _validate_count(el["count"], f"{where} 的 count")
            if el.get("content") is not None:
                raise ValueError(f"{where} 写了 count 就不要再写 content——"
                                 "第 0 帧的文本由 count 的 from/decimals/prefix/suffix 算出")
        else:
            _need_str(el, "content", where)
    elif kind == "rule":
        _opt_num(el, "width", where, None, positive=True)
        if "draw" in el and not isinstance(el["draw"], bool):
            raise ValueError(f"{where} 的 draw 必须是 JSON 布尔 true/false"
                             f"（实际: {el['draw']!r}）")
    for color_key in ("fill", "stroke"):
        if color_key in el and not isinstance(el[color_key], str):
            raise ValueError(f"{where} 的 {color_key} 必须是字符串"
                             f"（实际: {el[color_key]!r}）")
    return el


def validate_spec(data, *, aspect=None, theme=None, accent=None):
    """校验 spec 并归一化（CLI 覆盖项在这里并入，别让三个调用点各算一遍优先级）。

    aspect/theme 的缺省顺序：显式参数 > spec 顶层 > 技能默认（portrait / DEFAULT_THEME）。
    未知画幅/主题一律报错：静默兜到竖屏会让横屏稿件出竖屏尺寸。
    """
    if not isinstance(data, dict):
        raise ValueError("spec 顶层必须是 JSON 对象 "
                         '{"aspect"?, "theme"?, "accent"?, "elements": [ … ]}')
    _reject_unknown_keys(data, TOP_KEYS, "spec 顶层")
    _aspect = aspect or data.get("aspect") or DEFAULT_ASPECT
    if _aspect not in ASPECTS:
        raise ValueError(f"spec 的画幅={_aspect!r} 不是已知画幅：只接受 "
                         f"{'、'.join(repr(a) for a in ASPECTS)}")
    normalize_aspect(_aspect)          # 报错口径与文案归模板管，这里不另写一份
    _theme = theme or data.get("theme") or DEFAULT_THEME
    get_theme_colors(_theme)           # 未知主题名由它点名并列可用主题
    _validate_accent(data.get("accent"), "spec 顶层的 'accent'")
    _validate_accent(accent, "命令行传入的 --accent")
    _accent = accent or data.get("accent") or get_default_accent()
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError("spec 需要非空的 'elements' 列表（一张画布页至少有一个图元）")
    els = [_validate_element(el, i) for i, el in enumerate(elements, 1)]
    _check_ids_unique(els)
    return {"aspect": _aspect, "theme": _theme, "accent": css_color_to_hex(_accent),
            "elements": els}


def load_spec(path, **overrides):
    """读盘 + 校验（读不出来的三种失败统一成带路径的 ValueError，见 read_json_file）。"""
    return validate_spec(read_json_file(path), **overrides)


def _target_of(el):
    """元素在 director 里被点名的选择器：一个元素一个 id，class 目标由作者手写。"""
    return "#" + el["id"]


def _check_ids_unique(els):
    """id 必须唯一且字符集受限：它是渲染端选择器的一部分，撞车=一条 step 演两个地方。"""
    seen = {}
    for i, el in enumerate(els, 1):
        if "id" not in el:
            continue
        el_id = el["id"]
        where = f"spec 的 elements[{i}]（kind={el['kind']!r}）的 id"
        if not isinstance(el_id, str) or not _DIRECTOR_TARGET_RE.match("#" + el_id):
            raise ValueError(f"{where}={el_id!r} 不能当选择器用——必须是单个标识符"
                             "（字母或下划线起头，仅字母/数字/-/_）")
        if el_id in seen:
            raise ValueError(f"{where}={el_id!r} 与 elements[{seen[el_id]}] 重复"
                             "（director 的 target 会同时命中两处）")
        seen[el_id] = i


# ── 取色：全部从主题/accent 派生，不写字面 hex ────────────────────
def _contrast_safe(color, theme):
    """该色对主题页底最坏一档的对比度（与 check_svg 同一算法，同一判读口径）。"""
    stops = theme_bg_stops(theme)
    return min(contrast_ratio(color, s) for s in stops)


def _palette(theme, accent):
    """一页画布用的色板。

    plate（局部底板）与 line（轴线/刻度线）都是从**主题自己的量**派生：底板 = 页底中段
    往文字色偏一档（明度差做层级，不靠 opacity），线 = 比次要文字再暗一档。写死 hex 的
    底板换主题就穿帮，所以这里一个色值都不抄。
    文字三档（text/dim/accent）不止过 check_svg 的 4.5:1：dim 按 DIM_CONTRAST_TARGET（7:1）
    兜底，因为画布页的小字还要经两画幅缩放与成片压缩编码，4.5 是"能读"，7 是"还读得清"。
    """
    stops = theme_bg_stops(theme)
    mid = stops[len(stops) // 2]           # 画布页的字真正落在渐变中段（与 check_svg 同档）
    tc = get_theme_colors(theme)["text_color"]
    return {
        "text": tc,
        "dim": ensure_text_contrast(mix(tc, mid, DIM_FRAC), stops, DIM_CONTRAST_TARGET),
        "plate": mix(mid, tc, PLATE_FRAC),
        "line": mix(mid, tc, LINE_FRAC),
        "accent": accent,
        "accent_text": ensure_text_contrast(accent, stops),
    }


def _color(el, key, ctx, default, where, for_text=False):
    """fill/stroke 的取值解析：命名档（accent/text/dim/plate/line/none）或安全颜色名。"""
    raw = el.get(key)
    v = default if raw is None else raw
    if v == "none" and key == "fill":
        return "none"
    named = {"accent": ctx["pal"]["accent_text"] if for_text else ctx["pal"]["accent"],
             "text": ctx["pal"]["text"], "dim": ctx["pal"]["dim"],
             "plate": ctx["pal"]["plate"], "line": ctx["pal"]["line"]}
    if v in named:
        return named[v]
    hexed = css_color_to_hex(v)
    if hexed is None:
        raise ValueError(f"{where} 的 {key}={v!r} 不是合法颜色：给 #rgb/#rrggbb、"
                         f"CSS 标准色名，或 {'、'.join(sorted(named))}")
    return hexed


# ── 几何：角色、初始态、标尺 ───────────────────────────────────
def role_of(el):
    """structure = 结构底（第 0 帧就在，不进 steps）；content = 内容（逐拍长出来）。"""
    if "role" in el:
        return el["role"]
    return "structure" if el["kind"] in STRUCTURE_BY_DEFAULT else "content"


def _starts_hidden(el):
    """内容元素是否该写 opacity="0"。

    draw/count/type 例外，而且必须例外：它们的起始态由渲染端在页面 build 时就配好
    （dash 把描边收起、count 先落 from 那个数、type 先把文本清空）。给它们再套一层
    opacity="0"，那条 step 只负责 dash/数值/文本，没人把 opacity 抬回去——元素就永远
    不出现，而这恰恰是画布页最难查的一种坏法（成片看着"演过了"，其实是被藏死了）。
    """
    if role_of(el) != "content":
        return False
    return not (el.get("draw") or el.get("type") or "count" in el)


def _tick_positions(ticks, frm, to, where):
    """刻度值 → 轴上像素：vmin 落在 from 端、vmax 落在 to 端（先定标尺再画图形）。

    from/to 是轴两端的**像素**坐标，所以数据增长方向由作者排布决定：竖轴把 from 写在
    页面下方、to 写在上方，数值就朝上长。两端重合或刻度值全同一个数就没有标尺可言，
    报错而不是画出一堆叠在一起的标签。
    """
    vals = [float(t["v"]) for t in ticks]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        raise ValueError(f"{where} 的 ticks 只有一个刻度值或全部相同，定不出标尺"
                         "（至少要两个不同的 v）")
    if frm == to:
        raise ValueError(f"{where} 的 from 与 to 重合，轴长为 0（from/to 是轴两端的像素坐标）")
    span = (to - frm) / (hi - lo)
    return [frm + (v - lo) * span for v in vals], (lo, hi)


def _count_render(count):
    """count 节点在 SVG 里写的初始文本 = from（缺省 0）那一档的渲染值。

    frame 0 不能撒谎：导演还没落子时观众看到的就是这个数，写 to 等于先把答案印在屏幕上。
    """
    dec = int(count.get("decimals", 0))
    val = float(count.get("from", 0))
    return "{}{}{}".format(count.get("prefix", ""), ("%.*f" % (dec, val)),
                           count.get("suffix", ""))


# ── 元素发射 ─────────────────────────────────────────────────
def _emit_panel(el, ctx, where):
    x, y = _need_num(el, "x", where), _need_num(el, "y", where)
    w = _need_num(el, "w", where, positive=True)
    h = _need_num(el, "h", where, positive=True)
    cw, ch = ctx["canvas"]
    # 满幅判据直接调门禁那一份（判据只有一份，见模块顶部 FULL_BLEED 那条注释）：
    # 在这里就报而不是留一条 warn——本模块的立身之本就是"生成物一定过门禁"，
    # 而满幅 rect 唯一的后果是把模板那三层背景盖死。
    if check_svg.is_full_bleed({"x": _num(x), "y": _num(y), "width": _num(w),
                                 "height": _num(h)}, cw, ch):
        raise ValueError(f"{where} 的 panel 铺满了整页（{int(w)}×{int(h)} 起于 "
                         f"{int(x)},{int(y)}）：画布页必须留透明，让模板的渐变/网格/"
                         "accent 氛围光三层从内容背后透出来。要局部层次就缩进边距、"
                         "分几块面板；确实要换一种页色就手写 SVG，别走本脚手架")
    r = _opt_num(el, "r", where, ctx["panel_r"], nonnegative=True)
    opacity = _opt_num(el, "opacity", where, None)
    return _node("rect", OrderedDict([
        ("id", el.get("id")), ("x", _num(x)), ("y", _num(y)),
        ("width", _num(w)), ("height", _num(h)), ("rx", _num(r)),
        ("fill", _color(el, "fill", ctx, "plate", where)),
        ("opacity", None if opacity is None else _num(opacity))]))


def _emit_axis(el, ctx, where):
    orient = el["orient"]
    at, frm, to = _need_num(el, "at", where), _need_num(el, "from", where), _need_num(el, "to", where)
    side = el.get("label_side") or DEFAULT_LABEL_SIDE[orient]
    fs = ctx["tiers"]["label"]
    sw = ctx["line_w"]
    tick_len = fs * TICK_FRAC
    pos, (lo, hi) = _tick_positions(el["ticks"], frm, to, where)
    kids = []
    horizontal = orient == "x"
    axis_attrs = ({"x1": _num(frm), "y1": _num(at), "x2": _num(to), "y2": _num(at)}
                  if horizontal else
                  {"x1": _num(at), "y1": _num(frm), "x2": _num(at), "y2": _num(to)})
    axis_attrs.update({"stroke": ctx["pal"]["line"], "stroke-width": _num(sw)})
    kids.append(_node("line", OrderedDict(axis_attrs)))
    labels = []
    for t, p in zip(el["ticks"], pos):
        mark = ({"x1": _num(p), "y1": _num(at), "x2": _num(p), "y2": _num(at + tick_len)}
                if horizontal else
                {"x1": _num(at), "y1": _num(p), "x2": _num(at - tick_len), "y2": _num(p)})
        mark.update({"stroke": ctx["pal"]["line"], "stroke-width": _num(sw)})
        kids.append(_node("line", OrderedDict(mark)))
        text = t.get("label")
        if text is None:
            continue
        if t.get("unit"):
            text += t["unit"]
        if horizontal:
            lx, anchor = p, "middle"
            ly = (at + tick_len + fs) if side == "below" else (at - tick_len)
        else:
            ly = p + fs * TEXT_BASE_FRAC
            anchor = "start" if side == "right" else "end"
            lx = (at + tick_len) if side == "right" else (at - tick_len)
        labels.append(_node("text", OrderedDict([
            ("class", "tick"), ("x", _num(lx)), ("y", _num(ly)),
            ("font-size", _num(fs)), ("fill", ctx["pal"]["dim"]),
            ("text-anchor", anchor)]), text=text))
    kids.extend(labels)
    return _node("g", OrderedDict([("id", el.get("id"))]), kids=kids), \
        "  <!-- 刻度标尺：v 属于 [{}, {}] 线性映射到 {} 上的 [{}, {}] -->\n".format(
            _num(lo), _num(hi), "x" if horizontal else "y", _num(frm), _num(to))


def _emit_polyline(el, ctx, where):
    pts = " ".join("{},{}".format(_num(p[0]), _num(p[1])) for p in el["points"])
    attrs = OrderedDict([("id", el.get("id")), ("points", pts),
                         ("fill", _color(el, "fill", ctx, "none", where)),
                         ("stroke", _color(el, "stroke", ctx, "accent", where)),
                         ("stroke-width", _num(_opt_num(el, "width", where, ctx["line_w"], positive=True)))])
    if el.get("draw"):
        # pathLength=1 让渲染端不必测量真实长度就能把 dash 归一（draw 步骤会再写一次，
        # 这里先带上，是为了这张 SVG 单独用 <img> 看时也保持同一个几何口径）。
        attrs["pathLength"] = "1"
    return _node("polyline", attrs)


def _emit_bars(el, ctx, where):
    base = _need_num(el, "base_y", where)
    fs = ctx["tiers"]["label"]
    fill = _color(el, "fill", ctx, "accent", where)
    kids = []
    for b in el["bars"]:
        x, w, h = float(b["x"]), float(b["w"]), float(b["h"])
        kids.append(_node("rect", OrderedDict([
            ("class", "bar"), ("x", _num(x)), ("y", _num(base - h)),
            ("width", _num(w)), ("height", _num(h)), ("fill", fill)])))
        if b.get("label"):
            kids.append(_node("text", OrderedDict([
                ("class", "bar-label"), ("x", _num(x + w / 2.0)),
                ("y", _num(base + fs)), ("font-size", _num(fs)),
                ("fill", ctx["pal"]["dim"]), ("text-anchor", "middle")]),
                text=b["label"]))
    return _node("g", OrderedDict([("id", el.get("id"))]), kids=kids)


def _emit_circle(el, ctx, where):
    return _node("circle", OrderedDict([
        ("id", el.get("id")), ("cx", _num(_need_num(el, "cx", where))),
        ("cy", _num(_need_num(el, "cy", where))), ("r", _num(_need_num(el, "r", where))),
        ("fill", _color(el, "fill", ctx, "accent", where))]))


def _emit_text(el, ctx, where):
    tier = el.get("tier") or "label"
    fs = ctx["tiers"][tier]
    content = _count_render(el["count"]) if "count" in el else el["content"]
    return _node("text", OrderedDict([
        ("id", el.get("id")), ("x", _num(_need_num(el, "x", where))),
        ("y", _num(_need_num(el, "y", where))), ("font-size", _num(fs)),
        ("fill", _color(el, "fill", ctx, "text", where, for_text=True)),
        ("text-anchor", el.get("anchor") or "start")]), text=content)


def _emit_rule(el, ctx, where):
    attrs = OrderedDict([
        ("id", el.get("id")),
        ("x1", _num(_need_num(el, "x1", where))), ("y1", _num(_need_num(el, "y1", where))),
        ("x2", _num(_need_num(el, "x2", where))), ("y2", _num(_need_num(el, "y2", where))),
        ("stroke", _color(el, "stroke", ctx, "accent", where)),
        ("stroke-width", _num(_opt_num(el, "width", where, ctx["line_w"], positive=True)))])
    if el.get("draw"):
        attrs["pathLength"] = "1"
    return _node("line", attrs)


_EMITTERS = {"panel": _emit_panel, "polyline": _emit_polyline, "bars": _emit_bars,
             "circle": _emit_circle, "text": _emit_text, "rule": _emit_rule}


def _context(spec):
    w, h = get_canvas(spec["aspect"])
    return {"canvas": (w, h), "aspect": spec["aspect"], "theme": spec["theme"],
            "tiers": TIERS[spec["aspect"]], "line_w": LINE_W[spec["aspect"]],
            "panel_r": PANEL_R[spec["aspect"]],
            "pal": _palette(spec["theme"], spec["accent"])}


def render_svg(spec):
    """spec → 一张画布页 SVG 文本（viewBox 按画幅原生分辨率，页底透明）。"""
    ctx = _context(spec)
    cw, ch = ctx["canvas"]
    nodes = []
    for i, el in enumerate(spec["elements"], 1):
        where = f"spec 的 elements[{i}]（kind={el['kind']!r}）"
        if role_of(el) == "content" and "id" not in el:
            raise ValueError(f"{where} 是内容元素却缺 id：director 的 step 要靠 "
                             '"#id" 点名它，没有 id 就永远不会被点亮')
        if el["kind"] == "axis":
            node, note = _emit_axis(el, ctx, where)
        else:
            node, note = _EMITTERS[el["kind"]](el, ctx, where), None
        if _starts_hidden(el):
            node["attrs"].append(("opacity", "0"))
        if note:
            nodes.append(("note", note.strip()))
        nodes.append(("node", node))
    font = _TPL["typography"]["fontFamily"]
    style = _node("style", OrderedDict(), text="text{font-family:%s}" % font, raw=True)
    body = [_dump(style, 1)]
    for kind_, val in nodes:
        if kind_ == "note":
            body.append("  " + val)
        else:
            body.append(_dump(val, 1))
    head = ('<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" '
            'viewBox="0 0 {} {}">').format(_num(cw), _num(ch), _num(cw), _num(ch))
    # 注释放在根节点**之内**：整张文件仍只有一个根元素，任何按 DOM 读它的工具
    # （check_svg / _svg_sanitize）拿到的都是同一棵树。
    comment = ("  <!-- 由 scripts/canvas_kit.py 生成：画布 {}×{}、主题 {}、accent {}。\n"
               "       页底留透明——模板的渐变 / 网格 / accent 氛围光三层要从内容背后透出来。 -->"
               ).format(_num(cw), _num(ch), spec["theme"], spec["accent"])
    return "\n".join(['<?xml version="1.0" encoding="UTF-8"?>', head, comment,
                      "\n".join(body), "</svg>", ""])


# ── director 草稿：一份能直接进 images.json 的 {"steps": […]} ──────
def build_director(spec, sentences=None):
    """结构底不进 steps（它和页面一起到位），内容元素按文档顺序一拍一个 at 锚点。

    句序锚点只在"这一拍跟着某句话"的意义上成立，具体秒数由渲染端按 manifest 的句子算——
    所以草稿不需要知道配音时长，也不会因为重配音而漂移。这就是本模块只用 at 的理由。

    `sentences` 是该段旁白句数。给了它，自动拍的游标就按它取模回绕，让"内容元素比句子多"
    的稿子也产出一份**能直接生成**的草稿：多出来的元素与前面的同拍一起亮，而不是把 at
    顶到句序之外、被生成期当 error 拒掉（实测一个 3 句段配 5 个内容元素，自动拍到 at=4
    就整条管线 exit 1）。    不给时游标只增不减——那份草稿的 at 是否越界由 `--sentences` 的
    告警和生成期兜底，脚本不替作者猜段里有几句。
    """
    steps = []
    cursor = 0
    for el in spec["elements"]:
        if role_of(el) != "content":
            continue
        if "beat" in el:
            at = float(el["beat"])
            cursor = max(cursor, at + 1)   # 显式钉拍也把游标推过去，后面的自动拍不会插到它前面
        else:
            # 显式钉拍是作者意图，越界要照实报（见 main 的告警）；自动拍是机械分配，
            # 已知句数时按句数回绕，产出的草稿才真的能直接用。
            at = cursor % sentences if sentences else cursor
            cursor += 1
        at = int(at) if at == int(at) else at
        step = OrderedDict([("at", at), ("target", _target_of(el))])
        if el.get("draw"):
            step["draw"] = True
        elif "count" in el:
            step["count"] = el["count"]
        elif el.get("type"):
            step["type"] = {}
        else:
            # 起始态 SVG 已经写了 opacity=0，这里仍给全 from：草稿要能在作者删掉
            # SVG 那行 opacity 之后依旧演得对（fromTo 不依赖元素当前值）。
            step["from"] = {"opacity": 0}
            step["to"] = {"opacity": 1}
        if "stagger" in el:
            step["stagger"] = el["stagger"]
        # repeat / yoyo：新特性同样给脚手架出口（呼吸/脉动的"一条步"写法）。
        # repeat:0 与缺省等价，不塞进去省字节（与渲染端 _cycle_vars 同口径）。
        # 校验在 _validate_element 做（yoyo 必须有 repeat、只挂内容元素），
        # 互斥与 morph:repeat:-1 那类跨键约束由 selfcheck 过真契约时兜底。
        if el.get("repeat") not in (None, 0):
            step["repeat"] = el["repeat"]
        if el.get("yoyo"):
            step["yoyo"] = True
        for key, dflt in (("duration", DIRECTOR_DEFAULT["duration"]),
                          ("ease", DIRECTOR_DEFAULT["ease"])):
            # 只在与模板缺省不同值时才印出来：与缺省相同的值写进 JSON 就是第二份真源，
            # 模板改一档时这些副本永远不会跟着改。
            if el.get(key) is not None and el[key] != dflt:
                step[key] = el[key]
        steps.append(step)
    return {"steps": steps}


def selfcheck_fragment(spec, sid="seg1", sentences=None):
    """把草稿套成一份 images.json 过一遍真契约。

    为什么不自己判 steps 的形状：_images_schema 才是那份契约的正文，它改一个键集，
    这里就该同时报错——否则脚手架印出的草稿要等生成器在渲染前才拒掉。
    sid 用一个合法段 id 当壳（真实段 id 由作者替换）。
    """
    frag = build_director(spec, sentences=sentences)
    if not frag["steps"]:
        return frag
    validate_images_json({sid: {"src": "images/{}.svg".format(sid), "director": frag}})
    return frag


# ── 门禁自查 ────────────────────────────────────────────────
def lint_file(path, aspect, theme):
    """按 check_svg 的口径复查生成的 SVG；返回 (error, warn) 两份可执行问题。

    画布档那条无条件的"知情提示"滤掉（见 _STANDING_ADVISORY），其余一律照原样报——
    包括那条只看竖向的空带 warn：它判的是"暂停一帧还是不是一页空白"，脚手架替不了构图。
    """
    errors, warns = check_svg.check_file(path, "canvas", aspect, theme)
    return (sorted(set(errors)),
            sorted(set(w for w in warns if _STANDING_ADVISORY not in w)))


def _line_visibility_notes(spec, ctx):
    """线条与 accent 的可见性提示（check_svg 只量字不量线，这条只能自己打）。

    判据是 3:1 这一档：细线对页底低于它、又只有几 px 粗时，压完缩编码后读作"没画"
    （浅色页底上中等亮度的 accent 同样落进这一档，所以两个主题都查）。只提示不拦：
    线该不该抢本是构图判断，工具没有判据。底板不查——它的任务就是"和页底差一档"，
    按对比度判它等于判错。
    """
    notes = []
    pal = ctx["pal"]
    for key in ("accent", "line"):
        ratio = _contrast_safe(pal[key], spec["theme"])
        if ratio < LINE_CONTRAST_FLOOR:
            notes.append("{} 档用的 {} 对 {} 页底只有 {:.1f}:1：线/图形压进成片后可能读不出来，"
                         "换更亮（或更暗）一档".format(key, pal[key], spec["theme"], ratio))
    accent = spec["accent"]
    if accent != get_default_accent() and accent not in get_accent_palette():
        notes.append("accent {} 既不在本技能 accent 色板、也不是默认强调色——色板按段落轮询"
                     "分配给各段，另挑一支会让这一页和相邻几页不同调".format(accent))
    return notes


# ── CLI ─────────────────────────────────────────────────────
def _write(path, text):
    # newline="\n"：本仓库一律 LF，Windows 上默认换行会写出 CRLF 产物（diff 全是噪声）
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as exc:
        # 输出目录没建是第一次用的常态，裸栈 FileNotFoundError 不会告诉人该做什么。
        raise SystemExit(
            f"[error] 写不出 {path}：{exc.strerror or exc}。"
            "多半是目录还没建（先用 mkdir -p 建它的父目录）；同名已存在一个目录、"
            "或没有写权限也会到这里") from exc


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="整页画布 SVG 脚手架：spec.json → 画布页 SVG + director 草稿（steps 走 stdout 或 --emit-steps）")
    ap.add_argument("--spec", required=True, help="画布页元素清单 JSON")
    ap.add_argument("-o", "--out", required=True, help="输出 SVG 路径")
    ap.add_argument("--aspect", choices=list(ASPECTS), help="画幅（覆盖 spec 顶层；默认 portrait）")
    ap.add_argument("--accent", help="本页强调色（覆盖 spec 顶层；默认取段落缺省 accent）")
    ap.add_argument("--emit-steps", help="把 director 草稿另写一份 JSON 到该路径")
    ap.add_argument("--sentences", type=int,
                    help="该段旁白句数。给了它，自动拍按句数回绕（内容元素多于句子时产出的"
                         "草稿仍可直接生成），并对显式钉拍的 at 越界提前告警")
    ap.add_argument("--no-check", action="store_true",
                    help="跳过 check_svg 门禁自查（有意为之的极简页才用）")
    args = ap.parse_args(argv)
    # 句数不是"给不给"，是"给的一定是正整数"。这里不拦有两个后果：0 会让自动拍的取模
    # 回绕静默退化成顺序递增（`if sentences` 对 0 为假），草稿看着像给过句数、其实没回绕；
    # 负数更直接——at 被顶成负数，契约校验抛的是裸 Python 栈（实测 `--sentences -3` 打印
    # ValueError traceback）而不是本模块口径里那句人话。两种都是"参数错了却报不清"。
    if args.sentences is not None and args.sentences < 1:
        print("[error] --sentences 必须是正整数（该段旁白句数），实际: {}"
              .format(args.sentences), file=sys.stderr)
        return 2

    try:
        spec = load_spec(args.spec, aspect=args.aspect, accent=args.accent)
        svg = render_svg(spec)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    frag = selfcheck_fragment(spec, sentences=args.sentences)
    _write(args.out, svg)
    if args.emit_steps:
        _write(args.emit_steps, json.dumps(frag, ensure_ascii=False, indent=2) + "\n")

    ctx = _context(spec)
    for note in _line_visibility_notes(spec, ctx):
        print(f"[hint] {note}", file=sys.stderr)
    n_struct = sum(1 for e in spec["elements"] if role_of(e) == "structure")
    if args.sentences is not None:
        # 自动拍已按句数回绕，这里只会剩显式钉拍越界——那是作者意图，照实报。
        pinned = [el.get("beat") for el in spec["elements"]
                  if role_of(el) == "content" and "beat" in el
                  and float(el["beat"]) >= args.sentences]
        if pinned:
            print("[warn] {} 个显式 beat 超出该段句数（只有 {} 句旁白，越界的 beat：{}）"
                  "——生成期会按 error 拦，请补旁白或把 beat 收回来".format(
                      len(pinned), args.sentences,
                      "、".join(str(b) for b in pinned)), file=sys.stderr)
    elif len(frag["steps"]) > 1:
        # 实例实测的摩擦：不给 --sentences 时自动拍的 at 从 0 一路递增，内容元素多于
        # 该段旁白句数时，生成期把越界拍整条管线 error 拒。这里提前出声，让作者在
        # 写稿阶段就对好句数，而不是等 _director_prepare 那一声报错。
        n = len(frag["steps"])
        print("[hint] 未给 --sentences：自动拍 at 从 0 递增到 {}（草稿共 {} 拍）。"
              "若该段旁白不足 {} 句，生成期会把越界拍当 error 拒——"
              "给上 --sentences N 让草稿按句数回绕".format(n - 1, n, n), file=sys.stderr)

    rc = 0
    if not args.no_check:
        errors, warns = lint_file(args.out, spec["aspect"], spec["theme"])
        for e in errors:
            print(f"[gate error] {e}", file=sys.stderr)
        for w in warns:
            print(f"[gate warn]  {w}", file=sys.stderr)
        if errors or warns:
            print("[error] SVG 已写出，但没过 check_svg 的 canvas 门禁：上面每条都是成片暂停时"
                  "看得见的毛病。改 spec 重跑；确属有意的取舍再加 --no-check", file=sys.stderr)
            rc = 1

    # 门禁没过时别打 [ok]：同一条 stderr 里 [error] 后面跟一个 [ok]，读起来是
    # "报错但成功了"——退出码才是真的，日志不该跟退出码打架。
    print(f"{'[ok]' if rc == 0 else '[done]'} {n_struct} 个结构底 / "
          f"{len(frag['steps'])} 拍内容 / 已写 {args.out}"
          f"{'' if rc == 0 else '（未过门禁，见上）'}", file=sys.stderr)
    print(json.dumps(frag, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
