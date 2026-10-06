#!/usr/bin/env python3
"""手绘 SVG 配图的静态自查（只依赖标准库）。

管线对槽位版式的 SVG 只校验"文件存在"，对整页画布只校验比例与 px 字号。
`references/image_options.md` 里那几条"没有门禁替你查"的硬约束，这里补一道
**尽力而为**的静态检查——它能抓明显的错，抓不到的（文字实际溢出、数据图比例
是否画对）仍然要自己看图。

用法：
    python scripts/check_svg.py images/seg1.svg images/seg2.svg
    python scripts/check_svg.py images/                       # 目录：检查其中全部 .svg
    python scripts/check_svg.py images/seg3.svg --layout canvas --aspect landscape

检查项（error 让退出码为 1，warn 只提示）：
  - 根节点：纯数字 width/height 或 viewBox；槽位版式必须 4:3，整页画布必须与
    画幅等比（竖屏 1080×1440、横屏 1920×1080）                          [error]
  - 安全：<script>、on* 事件属性、<foreignObject>、外链资源（http/https）、
    DOCTYPE/ENTITY                                                      [error]
  - 字号：按"画布宽 ÷ SVG 宽"折算后的最终 px 低于 26px                   [warn]
    写成 em/%/class 读不出绝对值的文字数量                              [warn]
  - 对比度：文字用字面 hex 填充时，对**主题渐变的最坏一档** stop 算 WCAG 对比度；
    低于 3:1 为 error，低于 4.5:1 为 warn；背景族（深蓝 #0c1320/#16233a/#1a2536 加上
    当前主题渐变的各档 stop）当文字色为 error——只在深色页底主题（dark，唯一主题）下查，
    且只查槽位。整页画布只 warn 不 error：画布上
    的字可能压着自己画的浅色局部底板，按页底算出来的数对它不成立
  - 透明度：文字带 opacity / fill-opacity < 0.8                          [warn]
    （整页画布上的 opacity=0 除外：那是导演逐拍点亮的起始态，终帧才亮）
  - 铺满整幅的背景 <rect>：槽位版式会在页面上形成一圈色差框；整页画布会盖掉模板本来
    就在这一页底下的三层背景（渐变 / 网格 / accent 氛围光）                          [warn]
  - 含中文却没有任何 font-family（<img> 载入读不到页面字体）             [warn]
  - 整页画布：字面 x 坐标落进安全边距（竖屏 50px / 横屏 96px）           [warn]
  - 整页画布密度：存在高度 ≥25% 页高、横着没有任何图元的**整幅空带**      [warn]
    （实拍复现的"画布页中间一大块空白"就是这个形状。盲区：只投到 y 轴，所以横向留白、
    挤成一团、以及一根贯穿上下的细线替整页"占位"，它都看不见——构图仍归人眼）
  - 墙钟动画（SMIL <animate*> / CSS animation / transition）：无法与口播
    时间轴同步，提示改用 images.json 的 director                        [warn]
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

from _path_morph import y_span
from _theme import (contrast_ratio, css_color_to_hex, list_theme_names,
                    relative_luminance, theme_bg_stops, DEFAULT_THEME)  # WCAG 数学与 hex 归一化不在这里重抄
from _template import load_template

# 画布与槽位尺寸的真源在 _template（视觉单一数据源规则）：这里读模板而不是
# 手抄数值——上一版手抄的页底色就漂过一次（见 PAGE_BG 注释）。
_TPL = load_template()
CANVAS = {"portrait": (_TPL["canvas"]["vertical"]["width"],
                       _TPL["canvas"]["vertical"]["height"]),
          "landscape": (_TPL["canvas"]["landscape"]["width"],
                        _TPL["canvas"]["landscape"]["height"])}
_IMG = _TPL["layout"]["vertical"]["image"]
SLOT_W, SLOT_H = _IMG["width"], _IMG["height"]


def _safe_margins():
    """画布页四周留白：竖屏 = segCard 左右内边距（CSS 简写取横向值，与
    渲染端读同一键同一口径），横屏 = layout.landscape.margin。"""
    _v = _TPL["layout"]["vertical"]["segCard"]["padding"].split()
    _h = _v[1] if len(_v) >= 2 else _v[0]
    return {"portrait": int(float(_h.replace("px", ""))),
            "landscape": _TPL["layout"]["landscape"]["margin"]}


SAFE_MARGIN = _safe_margins()
MIN_PX = 26
RATIO_TOL = 0.01
# 根节点上声明"这一页就是要自铺满幅底色"的豁免开关。满幅底从 warn 升成 error
# 之后，这个开关是必须的：否则"确实该换底色"的场景只能被逼着删掉正确的东西，
# 门禁下一轮就会被绕开。
FULL_BLEED_ATTR = "data-ctv-full-bleed"
# 「满幅」的判据真源。canvas_kit 的 panel 自查原来抄了一份 0.98 字面量，两处
# 漂移过一次注释（这里写"宽或高"，代码是 and）。抽成函数是有理由的：判据本身
# 有三处逻辑（贴边、占满、百分比单位），复制出去必然有一处忘了同步。
#
# 为什么判"面积"而不是"宽和高各自越过 0.98"：一张 1056×1440 的底板宽只有
# 97.8%，双阈值下它能静默溜过去，而它在画面上和满幅没有区别——模板那三层照样
# 被盖死。三层只要被盖掉 97.8%，剩下的 2.2% 也只是让页缘露出条更细的网格，
# 并不会让层次回来。面积判据一次性覆盖这类"差一点点"。
FULL_BLEED_AREA = 0.92
FULL_BLEED_EDGE = 1.0
BG_FAMILY = {"#0c1320", "#16233a", "#1a2536"}
# 整页画布的文字真正落在主题背景渐变的中段（45% 位），底色从 _theme 注册表
# 派生而不是抄一份字面量——上一版手抄的 dark 底色是旧主题遗留，与真实页底
# 已漂移，对比度门禁一直在拿不存在的颜色当基准。
PAGE_BG = {t: theme_bg_stops(t)[1] for t in list_theme_names()}
# BG_FAMILY 抓的失败模式是"深蓝文字待在浅色局部底板上"——对页底对比度正常、
# 只有家族判定能抓；单一深色主题（dark）下始终启用。家族集合并入当前主题渐变
# stop：手抄族会与注册表漂移（上一版 PAGE_BG 的注释记的就是这类漂移）。
_BG_BY_THEME = {}


def is_full_bleed(el, vb_w, vb_h):
    """这张 rect 是不是"铺了满幅底板"：贴边 + 面积占比越线。

    只判面积不判宽高各别：贴边 + 覆盖九成以上的画幅就已经把模板那三层背景盖死了，
    剩下那一成边距露出的网格细到看不出层次，按面积判既更严也更少一条分支。
    `width="100%" height="100%"` 这种写法按面积与画幅相同处理。

    调用方（canvas_kit）直接调它而不是重算——判据只有这一份。
    """
    def num(v, pct_of):
        if str(v).strip().endswith("%"):
            try:
                return pct_of * float(str(v).strip().rstrip("%")) / 100.0
            except ValueError:
                return None
        return _parse_num(v)

    w = num(el.get("width"), vb_w)
    h = num(el.get("height"), vb_h)
    x = _parse_num(el.get("x")) or 0.0
    y = _parse_num(el.get("y")) or 0.0
    if w is None or h is None:
        return False
    return (x <= FULL_BLEED_EDGE and y <= FULL_BLEED_EDGE
            and w * h >= vb_w * vb_h * FULL_BLEED_AREA)


def _bg_family(theme):
    """该主题下判"背景/底板专属色"的集合（小写 hex）。"""
    if theme not in _BG_BY_THEME:
        dark = (relative_luminance(PAGE_BG[theme]) or 0) < 0.2
        fam = BG_FAMILY | {s.lower() for s in theme_bg_stops(theme)}
        _BG_BY_THEME[theme] = fam if dark else set()
    return _BG_BY_THEME[theme]

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CJK = re.compile(r"[\u3400-\u9fff]")
_NUM = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*(px)?\s*$")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _style_map(el):
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _prop(el, name):
    """属性优先取 style，其次取同名表现属性。"""
    sm = _style_map(el)
    return sm.get(name, el.get(name))


def _parse_num(v):
    if v is None:
        return None
    m = _NUM.match(str(v))
    return float(m.group(1)) if m else None


# ── 整页画布的竖向占位（密度自查）─────────────────────────────
# 判据来自实拍：一张 1080×1440 的画布只有顶部两行标题（y≈150-240）和一条 y≈800 的
# 轴，中间 480px（1/3 页高）横着什么都没有——成片暂停就是一眼假的"空页"。槽位版式
# 有模板网格兜着构图，画布页的构图责任全在那张 SVG 自己身上，所以只在这里查这一条
# 最客观的：把可读几何投到 y 轴上，找最大的整幅空带。
MAX_EMPTY_BAND = 0.25
_DEFAULT_FS = float(MIN_PX)   # 字号读不出时给文字一个保守高度


def _stroke_pad(el, inherited):
    """一维图形（线 / 折线 / path）按描边宽度撑出可看见的厚度，至少 1px。"""
    sw = _parse_num(inherited(el, "stroke-width")) or 0.0
    return max(sw / 2.0, 1.0)


def _ink_band(el, tag, inherited, page_w, page_h):
    """一个图元在 y 轴上的占位区间 (y0, y1)；读不出几何就返回 None。

    口径故意"宁可高估"：不解析 transform / `<use>` / 曲线精确 bbox，path 连控制点
    一起算，opacity=0 的导演元素照算（那是终帧会亮起来的内容，不是空位）。高估只
    会让这条 warn 少打一次，低估会误报，所以往安全那侧偏。
    铺满整页的背景底板返回 None——它能把空带填成"看起来有东西"，正是本检查要防的假象。
    """
    def num(name):
        return _parse_num(el.get(name))

    if tag == "rect":
        y, hh = num("y") or 0.0, num("height")
        ww = num("width")
        if hh is None or ww is None:
            return None                     # 百分号/缺省：读不出就不算
        if ww >= page_w * 0.98 and hh >= page_h * 0.98 \
                and (num("x") or 0) <= 1 and y <= 1:
            return None
        return (y, y + hh)
    if tag == "circle":
        c, r = num("cy"), num("r")
        return None if c is None or r is None else (c - r, c + r)
    if tag == "ellipse":
        c, ry = num("cy"), num("ry")
        return None if c is None or ry is None else (c - ry, c + ry)
    if tag == "line":
        y1, y2 = num("y1") or 0.0, num("y2") or 0.0
        return (min(y1, y2) - _stroke_pad(el, inherited),
                max(y1, y2) + _stroke_pad(el, inherited))
    if tag in ("polygon", "polyline"):
        pts = [float(v) for v in re.split(r"[,\s]+", (el.get("points") or "").strip())
               if _NUM.match(v)]
        ys = pts[1::2]
        if not ys:
            return None
        p = _stroke_pad(el, inherited)
        return (min(ys) - p, max(ys) + p)
    if tag == "path":
        try:
            span = y_span(el.get("d") or "")
        except ValueError:                  # 畸形 d：别拿它当占位，交给解析报错那条路
            return None
        if span is None:
            return None
        p = _stroke_pad(el, inherited)      # 一条水平轴本身零高度，不补就漏成"空带"
        return (span[0] - p, span[1] + p)
    if tag == "text":
        fs = _parse_num(inherited(el, "font-size")) or _DEFAULT_FS
        y = num("y") or 0.0
        return (y - fs, y + fs * 0.3)       # 基线上方是字身，下方留一点下伸部
    if tag == "image":
        y, hh = num("y"), num("height")
        return None if y is None or hh is None else (y, y + hh)
    return None


def _largest_empty_band(bands, page_h):
    """合并 y 占位区间后，返回页面里最大的整幅横向空带 (y0, y1)。"""
    spans = sorted((max(0.0, a), min(page_h, b)) for a, b in bands if b > a)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if not merged:
        return 0.0, page_h
    gaps = ([(0.0, merged[0][0])]
            + [(merged[i - 1][1], merged[i][0]) for i in range(1, len(merged))]
            + [(merged[-1][1], page_h)])
    return max(gaps, key=lambda g: g[1] - g[0])


def _root_size(root):
    """返回 (宽, 高, 说明)；读不出纯数字尺寸则宽高为 None。"""
    w, h = _parse_num(root.get("width")), _parse_num(root.get("height"))
    vb = root.get("viewBox")
    vbw = vbh = None
    if vb:
        parts = re.split(r"[,\s]+", vb.strip())
        if len(parts) == 4:
            try:
                vbw, vbh = float(parts[2]), float(parts[3])
            except ValueError:
                pass
    if w and h:
        if vbw and vbh and abs(w / h - vbw / vbh) > RATIO_TOL:
            return w, h, f"width/height 比例 {w / h:.3f} 与 viewBox 比例 {vbw / vbh:.3f} 不一致"
        return w, h, ""
    if vbw and vbh:
        return vbw, vbh, ""
    return None, None, ""


def check_file(path, layout, aspect, theme):
    errors, warns = [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return [f"读不了文件: {e}"], []

    # 先拦实体声明再解析，避免实体膨胀；图库素材极少带 DOCTYPE
    if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        return ["含 DOCTYPE/ENTITY 声明，拒绝解析（实体膨胀风险）；请删掉这些声明"], []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [f"XML 解析失败: {e}"], []
    if _local(root.tag) != "svg":
        return ["根节点不是 <svg>"], []

    # 显式豁免：作者声明"这一页就是要自铺满幅底色"（等于主动放弃模板那三层背景）。
    # 有了这个开关，满幅底才能升成 error——否则"确实该铺"的场景就只能被逼着
    # 删掉正确的东西，门禁立刻会被绕开。
    full_bleed_ok = str(root.get(FULL_BLEED_ATTR, "")).strip().lower() in ("1", "true", "yes")
    if full_bleed_ok:
        warns.append(f"已声明 {FULL_BLEED_ATTR}=\"1\"：这一页自铺满幅底色，模板的主题渐变 / "
                     f"页缘网格 / accent 氛围光三层被盖掉，画布页会退回一片死平（这是你"
                     f"主动换来的，确认过即可）")

    # ── 根节点几何 ──
    w, h, note = _root_size(root)
    if note:
        warns.append(note)
    if not w or not h:
        errors.append("根标签读不出纯数字 width/height 或 viewBox（% / em 不算），没法验比例")
        scale = None
    else:
        if layout == "canvas":
            cw, ch_ = CANVAS[aspect]
            target, label = cw / ch_, f"{cw}×{ch_}（{aspect} 整页画布）"
        else:
            cw, ch_ = SLOT_W, SLOT_H
            target, label = 4 / 3, "4:3（槽位版式，建议 980×735）"
        if abs(w / h - target) / target > RATIO_TOL:
            errors.append(f"比例 {w:g}×{h:g}（{w / h:.3f}）与目标 {label} 不等比；"
                          f"cover 会裁掉贴边内容，请改根节点尺寸后重排，别缩内容凑比例")
        scale = cw / w

    # ── 遍历元素 ──
    text_nodes = 0
    unknown_size = 0
    min_eff = None
    has_font_family = False
    has_cjk = False
    has_wallclock = False
    ink_bands = []
    parent = {c: p for p in root.iter() for c in p}

    def inherited(el, name):
        cur = el
        while cur is not None:
            v = _prop(cur, name)
            if v is not None:
                return v
            cur = parent.get(cur)
        return None

    def has_transform_ancestor(el):
        cur = el
        while cur is not None:
            if cur.get("transform"):
                return True
            cur = parent.get(cur)
        return False

    vb_w, vb_h = w, h
    for el in root.iter():
        tag = _local(el.tag)

        if tag == "script":
            errors.append("含 <script>（内联后会在页面里执行）")
        if tag == "foreignObject":
            errors.append("含 <foreignObject>（渲染不可控，且可嵌入 HTML）")
        if tag in ("animate", "animateTransform", "animateMotion", "set"):
            has_wallclock = True
        for k, v in el.attrib.items():
            lk = _local(k).lower()
            if lk.startswith("on"):
                errors.append(f"<{tag}> 带事件属性 {lk}")
            _lv = v.strip().lower()
            if lk == "href" and (
                    _lv.startswith("//")
                    or re.match(r"^[a-z][a-z0-9+.-]*:", _lv)):
                # 与净化器同一口径：任何 scheme 都算外部资源（file:/data:/
                # blob:/javascript: …），内部片段 "#id" 与相对路径放行。
                errors.append(f"<{tag}> 引用外链/脚本/本地文件资源 {v[:60]}")
        if tag == "style" and el.text and re.search(
                r"@import|url\(\s*['\"]?(?:https?:|//|file:|data:)", el.text, re.I):
            errors.append("<style> 里有 @import 或外链 url()")
        if el.get("style") and re.search(
                r"url\(\s*['\"]?(?:https?:|//|file:|data:)", el.get("style"), re.I):
            errors.append(f"<{tag}> 的 style 里有外链 url()")
        if (tag == "style" and el.text and re.search(r"\b(animation|transition)\b", el.text, re.I)) \
                or (el.get("style") and re.search(r"\b(animation|transition)\b", el.get("style"), re.I)):
            has_wallclock = True

        if tag == "text":
            text_nodes += 1
            content = "".join(el.itertext())
            if _CJK.search(content):
                has_cjk = True

            # 字号
            fs = inherited(el, "font-size")
            px = _parse_num(fs) if fs else None
            if px is None:
                unknown_size += 1
            elif scale is not None:
                eff = px * scale
                min_eff = eff if min_eff is None else min(min_eff, eff)

            # 字面色对页底的对比度。底色取主题渐变的**最坏一档**：深色页底上的浅字怕最亮
            # 那档、浅色页底上的深字怕最暗那档，min 两边都自动选对。两档主题下这恰好等于
            # 原先手挑的 PAGE_BG（渐变的中间档），所以槽位判定一字不变，只是从"赌中间那档"
            # 变成"取最坏"。
            # 画布页只 warn、不 error：画布上的字可能压在自己画的局部浅色底板上（那是正当
            # 画法，见 image_options.md「图内文字的对比度」），按页底算出来的数对它不成立，
            # 所以这里没有判死的资格。背景族那条启发式同理留在槽位。
            fill = inherited(el, "fill")
            fh = css_color_to_hex(fill) if fill and _HEX.match(fill.strip()) else None
            if fh:
                if layout == "slot" and fh in _bg_family(theme):
                    errors.append(f"文字 {content.strip()[:12]!r} 用了背景色族 {fh} 当填充色（瞎字）")
                else:
                    c = min(contrast_ratio(fh, s) for s in theme_bg_stops(theme))
                    if layout == "canvas" and c < 4.5:
                        warns.append(f"文字 {content.strip()[:12]!r} 填充 {fh} 对 {theme} 主题页底最坏一档只有 "
                                     f"{c:.1f}:1（<4.5:1）。它若直接落在页底上就是看不清；若压在"
                                     f"自己画的浅色底板上，本条不成立可忽略。画布的氛围光还会再吃一档")
                    elif c < 3:
                        errors.append(f"文字 {content.strip()[:12]!r} 填充 {fh} 对 {theme} 页底对比度仅 {c:.1f}:1（<3:1）")
                    elif c < 4.5:
                        warns.append(f"文字 {content.strip()[:12]!r} 填充 {fh} 对 {theme} 页底对比度 {c:.1f}:1（<4.5:1），"
                                     f"还会被 accent 辉光再吃一档")
            op = inherited(el, "opacity")
            fop = inherited(el, "fill-opacity")
            for name, val in (("opacity", op), ("fill-opacity", fop)):
                n = _parse_num(val) if val else None
                if n is None or n >= 0.8:
                    continue
                # 整页画布上 opacity=0 是导演逐拍点亮的起始态（终帧才亮），不是吃掉对比度；
                # 半透明才是真问题——它会让亮起来的那一帧本身就偏灰。
                if layout == "canvas" and n == 0:
                    continue
                warns.append(f"文字 {content.strip()[:12]!r} 带 {name}={n:g}，会真实吃掉对比度；换更暗的实色")

            # 整页画布安全边距（只看字面 x、且祖先无 transform 时才可信）
            if layout == "canvas" and scale is not None and not has_transform_ancestor(el):
                xv = _parse_num(el.get("x"))
                if xv is not None:
                    anchor = inherited(el, "text-anchor") or "start"
                    m = SAFE_MARGIN[aspect]
                    cw = CANVAS[aspect][0]
                    px_x = xv * scale
                    if anchor == "start" and px_x < m:
                        warns.append(f"文字 {content.strip()[:12]!r} 起点 x≈{px_x:.0f}px 进了左安全边距（{m}px）")
                    if anchor == "end" and px_x > cw - m:
                        warns.append(f"文字 {content.strip()[:12]!r} 终点 x≈{px_x:.0f}px 进了右安全边距（{cw - m}px）")

        if el.get("font-family") or _style_map(el).get("font-family"):
            has_font_family = True
        if tag == "style" and el.text and "font-family" in el.text:
            has_font_family = True

        # 满幅背景 rect
        #
        # 这里曾是 warn，结果它一路跟着出了片：主视觉自带一张满幅底板把模板在底下
        # 画好的三层背景（主题渐变 / 页缘网格 / 本段 accent 氛围光）整个盖掉，
        # 画布页退化成一片死平，SVG 也就"融不进背景"——而这正是本该被拦下的失败。
        # warn 的问题是它不改变退出码，检查器在流水线里就成了摆设。
        # 现在升 error：要么删掉那张 rect，要么显式声明"这一页就是要换底色"。
        if tag == "rect" and vb_w and vb_h and layout in ("slot", "canvas") \
                and is_full_bleed(el, vb_w, vb_h):
            if full_bleed_ok:
                continue
            hint = (f"在根 <svg> 上加 {FULL_BLEED_ATTR}=\"1\" 表示这一页确实要"
                    f"换底色（等于放弃模板那三层背景），本条即豁免。")
            if layout == "slot":
                errors.append("槽位 SVG 铺满了 viewBox：不要铺满幅底（会在页面上形成"
                              f"一圈色差框，图也融不进背景）。删掉这张 <rect>；"
                              f"确需自铺局部底板请缩小到远小于画幅。{hint}")
            else:
                errors.append("整页画布铺了一张满幅底板 <rect>：模板本来就在这一页底下"
                              "画好了三层（主题渐变 / 只在页缘显形的网格 / 本段 accent "
                              "氛围光），满幅 rect 把三层整个盖掉，画布页就退回一片"
                              "死平，SVG 也融不进页面背景。删掉它让三层透出来（见 "
                              "image_options.md「整页画布的密度与层次」）。" + hint)

        # 竖向占位登记（只有画布档用得上：整页构图没有模板兜底，空带得自己发现）
        if layout == "canvas" and w and h:
            band = _ink_band(el, tag, inherited, w, h)
            if band:
                ink_bands.append(band)

    if min_eff is not None and min_eff < MIN_PX:
        warns.append(f"折算后最小字号约 {min_eff:.0f}px（<{MIN_PX}px 下限），两画幅缩放+压缩后读不出来")
    if unknown_size:
        warns.append(f"{unknown_size} 个 <text> 的字号不是 px 数值（em/%/class/缺省），读不出绝对值，请自己按画幅复核")
    if has_cjk and not has_font_family:
        warns.append("含中文但没有任何 font-family：<img> 载入的 SVG 读不到页面字体，请在 <style> 里自带中文无衬线字体栈")
    if has_wallclock:
        warns.append("含墙钟动画（SMIL <animate*> / CSS animation / transition）：这类动画按页面墙上时钟自走，"
                     "无法与口播时间轴同步，只适合氛围循环。要"
                     "「随某句话变化」的时间轴同步动画，改用 images.json 的 director（净化内联后由 GSAP 补间驱动；"
                     "内联时这些墙钟动画会被剥离，见 references/image_options.md 方式 C「SVG 动画：两档」）")
    if layout == "canvas":
        warns.append("整页画布：字面色的对比度已经按主题页底的最坏一档查过了（上面那几条就是），"
                     "剩下两样静态查不到——压在自己画的局部底板上的那些字（按页底算对它不成立）、"
                     "以及文字是否真的溢出。用 image_options.md「图内文字的对比度」的内联副本 "
                     "+ 渲染后抽帧目检一次")
        if w and h:
            gy0, gy1 = _largest_empty_band(ink_bands, h)
            if gy1 - gy0 >= MAX_EMPTY_BAND * h:
                warns.append(
                    f"整页画布有一整条横向空带：y≈{gy0:.0f}–{gy1:.0f}（{gy1 - gy0:.0f}px，"
                    f"占页高 {(gy1 - gy0) / h:.0%}，阈值 {MAX_EMPTY_BAND:.0%}）横着没有任何图元。"
                    f"画布页的构图没人兜底，暂停成片时这就是一页空白——把主视觉撑开到这个带里、"
                    f"或按 image_options.md「整页画布的密度与层次」补中景/底层信息。"
                    f"有意的极简留白可忽略本条（它只查竖向：横向留白、挤成一团，以及一根"
                    f"贯穿上下的细线把整页'填上'了却仍然很空，它都看不见）")
    return errors, warns


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(".svg"):
                    files.append(os.path.join(p, name))
        else:
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser(description="手绘 SVG 配图静态自查（尽力而为，不替代人眼复核）")
    ap.add_argument("paths", nargs="+", help="SVG 文件或目录")
    ap.add_argument("--layout", choices=["slot", "canvas"], default="slot",
                    help="slot = 4:3 槽位配图（默认）；canvas = 整页画布")
    ap.add_argument("--aspect", choices=list(CANVAS), default="portrait",
                    help="画幅（仅 --layout canvas 用到，默认 portrait）")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        print("[error] 没找到任何 .svg 文件", file=sys.stderr)
        return 2
    n_err = n_warn = 0
    for f in files:
        errors, warns = check_file(f, args.layout, args.aspect, DEFAULT_THEME)
        status = "FAIL" if errors else ("WARN" if warns else "OK")
        print(f"[{status}] {f}")
        for e in dict.fromkeys(errors):
            print(f"    error: {e}")
        for w in dict.fromkeys(warns):
            print(f"    warn:  {w}")
        n_err += len(set(errors))
        n_warn += len(set(warns))
    print(f"\n{len(files)} 个文件，{n_err} 条 error，{n_warn} 条 warn")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
