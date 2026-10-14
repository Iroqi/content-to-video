#!/usr/bin/env python3
"""手绘 SVG 配图的静态自查（只依赖标准库）。

管线对槽位版式的 SVG 只校验"文件存在"，对整页画布只校验比例与 px 字号。
`references/canvas.md` 里那几条"没有门禁替你查"的硬约束，这里补一道
**尽力而为**的静态检查——它能抓明显的错，抓不到的（文字实际溢出、数据图比例
是否画对）仍然要自己看图。

用法：
    python scripts/check_svg.py images/seg1.svg images/seg2.svg
    python scripts/check_svg.py images/                       # 目录：检查其中全部 .svg
    python scripts/check_svg.py images/seg3.svg --layout canvas --aspect landscape
    python scripts/check_svg.py images/ --layout auto         # 混用目录：逐文件推断档位

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
  - 图元越出画布边界：rect/circle/ellipse/line/polygon/polyline 与 path 的
    精确几何、以及文字的纵向（基线上下）与锚点位置超出 viewBox。cover 对
    槽位图按框裁切、画布页就是成片的一页，越界内容必然被裁掉/出画——
    跟"比例不等"是同一类"贴边内容会丢"的失败模式。带 transform 祖先的
    元素跳过（最终位置静态算不出，不误报）                          [error]
  - 线条端点悬空：line/polyline/带描边的开 path，首尾端点 4‰ 画幅内
    没有落在任何"别的"笔画/描边/圆点/文字上。与 craft 的"线要结束在某
    处"同一条规则：悬空线头是画到一半的残线。只查 stroke 可见的元素、
    只以**其他元素**为落点目标（自己的线段不算）；带 `data-free-ends`
    属性的线条跳过（canvas_kit 的 rule 分隔线/下划线就是有意悬空的装饰
    线，手绘作者同样可以用它声明"这条线就是悬空的"）。有意悬空的装饰线
    可忽略本条（warn 不挡 CI）                                        [warn]
"""
import argparse
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

from _path_morph import path_endpoints, path_segments, x_span, y_span
from _svg_sanitize import _WALLCLOCK_CSS as _CSS_WALLCLOCK
from _theme import (contrast_ratio, css_color_to_hex, list_theme_names,
                    relative_luminance, theme_bg_stops, DEFAULT_THEME)  # WCAG 数学与 hex 归一化不在这里重抄
from _template import (MIN_READABLE_FONT_PX, load_template,  # noqa: E401
                       same_aspect, svg_text_band)

# 画布与槽位尺寸的真源在 _template（视觉单一数据源规则）：这里读模板而不是
# 手抄数值——上一版手抄的页底色就漂过一次（见 PAGE_BG 注释）。
_TPL = load_template()
CANVAS = {"portrait": (_TPL["canvas"]["vertical"]["width"],
                       _TPL["canvas"]["vertical"]["height"]),
          "landscape": (_TPL["canvas"]["landscape"]["width"],
                        _TPL["canvas"]["landscape"]["height"])}
_IMG = _TPL["layout"]["vertical"]["image"]
_IMG_L = _TPL["layout"]["landscape"]["image"]
# 槽位尺寸**按画幅**取：竖屏是 4:3 主图槽（980×735），横屏是右侧槽位
# （1067×800，也是 4:3 但更大）。早期这里只读 vertical 一份，于是横屏槽位
# 也按 980 折算字号——系统性低估 8.2%，一张 1067×800 上 26px 的字会被算成
# 24px 而误报"小于下限"，且建议尺寸恒写竖屏的 980×735。封面式的"整幅一图"
# 与槽位共用一个 cover 语义，但框的大小由版式决定，不由素材决定。
SLOT = {"portrait": (_IMG["width"], _IMG["height"]),
        "landscape": (_IMG_L["width"], _IMG_L["height"])}


def _safe_margins():
    """画布页四周留白：竖屏 = segCard 左右内边距（CSS 简写取横向值，与
    html_renderer 读同一键同一口径），横屏 = layout.landscape.margin。"""
    _v = _TPL["layout"]["vertical"]["segCard"]["padding"].split()
    _h = _v[1] if len(_v) >= 2 else _v[0]
    return {"portrait": int(float(_h.replace("px", ""))),
            "landscape": _TPL["layout"]["landscape"]["margin"]}


SAFE_MARGIN = _safe_margins()
BG_FAMILY = {"#0c1320", "#16233a", "#1a2536"}
# 整页画布的文字真正落在主题背景渐变的中段（45% 位），底色从 _theme 注册表
# 派生而不是抄一份字面量——上一版手抄的 dark 底色是旧主题遗留，与真实页底
# 已漂移，对比度门禁一直在拿不存在的颜色当基准。
PAGE_BG = {t: theme_bg_stops(t)[1] for t in list_theme_names()}
# BG_FAMILY 抓的失败模式是"深蓝文字待在浅色局部底板上"——对页底对比度正常、
# 只有家族判定能抓；单一深色主题（dark）下始终启用。家族集合并入当前主题渐变
# stop：手抄族会与注册表漂移（上一版 PAGE_BG 的注释记的就是这类漂移）。
_BG_BY_THEME = {}


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
        # 口径在 _template.svg_text_band（含读不到 font-size 时的兜底字号）——
        # 同文件的「文字越界」与 _cam_crop 的运镜切边都读同一份，三处不再各写一遍。
        return svg_text_band(num("y") or 0.0,
                             _parse_num(inherited(el, "font-size")))
    if tag == "image":
        y, hh = num("y"), num("height")
        return None if y is None or hh is None else (y, y + hh)
    return None


def _largest_empty_band(bands, page_h):
    """合并 y 占位区间后，返回页面里最大的整幅横向空带 (y0, y1)。"""
    # 先裁到页内再判有效：`b > a` 判未裁剪值的写法会让越界图元（y=3000 的文字）
    # 通过判定，裁完变成 (2940, 1440) 这种**逆序区间**混进合并，算出"空带占页高
    # 189%"的假告警——它盖掉的正是真正该看的构图问题。
    clipped = [(max(0.0, a), min(page_h, b)) for a, b in bands]
    spans = sorted(s for s in clipped if s[1] > s[0])
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


def _resolve_layout(layout, w, h, aspect):
    """把 `--layout auto` 解成 (档位, 画幅, 推断失败的话)。

    混用目录（槽位图和整页画布同放一个 `images/`）此前必须跑两遍，而且每一遍
    都会把另一类文件判成"比例不符"的 **error**——那不是图错了，是档位选错了。
    比例在这一档里够用：槽位两画幅都是 4:3，整页画布竖屏 0.75 / 横屏 1.778，
    四类互不重叠。

    - 整页画布先判：它的比例随画幅变，命中即**连画幅一起定下来**。`--aspect`
      写反时不该拿另一档的框去量这张图（1080×1440 被当成横屏画布，折算字号
      与安全边距全错）。
    - 槽位保留 `--aspect` 给的画幅：两画幅的槽位框都是 4:3（980×735 与
      1067×800，比例差 0.03%），比例分辨不出，画幅是作者自己声明的事。
    - 都命中不了：不猜。报一条点名比例的 error，并按槽位继续查其余项——
      少报一条比例 error 比"推断不出就整体跳过"划算。
    """
    if layout != "auto" or not w or not h:
        return layout, aspect, ""
    r = w / h
    for a in sorted(CANVAS):
        cw, ch = CANVAS[a]
        if same_aspect(w, h, (cw, ch)):
            return "canvas", a, ""
    for a in sorted(SLOT):
        if same_aspect(w, h, SLOT[a]):
            return "slot", aspect, ""
    _known = " / ".join(f"{a} {CANVAS[a][0] / CANVAS[a][1]:.3f}" for a in sorted(CANVAS))
    return "slot", aspect, (
        f"比例 {w:g}×{h:g}（{r:.3f}）既不是槽位的 4:3，也不是任何画幅的整页画布"
        f"（{_known}）——`--layout auto` 推断不出该按哪一档查，已按槽位继续查其余项。"
        f"请确认根节点尺寸，或显式给 --layout 与 --aspect")


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
        if vbw and vbh and not same_aspect(w, h, (vbw, vbh)):
            return w, h, f"width/height 比例 {w / h:.3f} 与 viewBox 比例 {vbw / vbh:.3f} 不一致"
        return w, h, ""
    if vbw and vbh:
        return vbw, vbh, ""
    return None, None, ""


# ── 越界与端点落点（2D 版自证，借鉴"线要结束在某处"那条 craft 规则）────────
# 端点容差：画幅短边的 4‰，下限 2 个 SVG 单位。短边 735 → 2.94、短边 800 →
# 3.2：随图缩放而不是拍死一个绝对值，与手绘"贴到就能算根住"的直觉一致。
END_TOL_FRAC = 0.004
END_TOL_MIN = 2.0
# 终端圆点判据：直径 ≤ 8 单位（≈ 短边 1%）的小圆按"落点"算；更大的圆按
# "圆周带"算（端点需落在圆周附近才算根住）。
DOT_MAX_R = 4.0


def _pt_list(points):
    """points 属性 → [(x, y), ...]；空/畸形返回 None。"""
    vals = [float(v) for v in re.split(r"[,\s]+", (points or "").strip())
            if _NUM.match(v)]
    if len(vals) < 4 or len(vals) % 2:
        return None
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]


def _shape_bounds(el, tag, vb_w, vb_h):
    """精确几何元素在画布坐标下的 (x0, y0, x1, y1)；读不出返回 None。

    path 用 x_span/y_span（含曲线控制点，宁可高估——越界是 error，低估会漏报）。
    """
    def num(name):
        return _parse_num(el.get(name))

    if tag == "rect":
        x, y, w, h = num("x") or 0.0, num("y") or 0.0, num("width"), num("height")
        if w is None or h is None:
            return None
        return (x, y, x + w, y + h)
    if tag == "circle":
        cx, cy, r = num("cx"), num("cy"), num("r")
        if None in (cx, cy, r):
            return None
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx, cy, rx, ry = num("cx"), num("cy"), num("rx"), num("ry")
        if None in (cx, cy, rx, ry):
            return None
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == "line":
        x1, y1, x2, y2 = num("x1"), num("y1"), num("x2"), num("y2")
        if None in (x1, y1, x2, y2):
            return None
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag in ("polygon", "polyline"):
        pts = _pt_list(el.get("points"))
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    if tag == "path":
        d = el.get("d") or ""
        try:
            xs = x_span(d)
            ys = y_span(d)
        except ValueError:
            return None
        if xs is None or ys is None:
            return None
        return (xs[0], ys[0], xs[1], ys[1])
    return None


def _text_oob(el, inherited, vb_w, vb_h):
    """文字越界：纵向按基线 ± 字身（上方 fs、下方 0.3fs，与 _ink_band 同一
    口径），横向按锚点。text-anchor=middle 的宽度静态算不出，只查纵向。
    返回错误文案或 None。"""
    fs = inherited(el, "font-size")
    px = _parse_num(fs) if fs else None
    y = _parse_num(el.get("y")) or 0.0
    # 读不到 px（写在 <style> 的 class 里）时按可读下限兜底，与空带自查同一口径：
    # 早先这里直接 return None（"没法验"），而密度自查已经按 26px 给它算了一格
    # 占位——同一份图两道自查一个说"越界"一个说"没事"，且越界那条连出声都没有。
    # 兜底取能读的下限：宁可多报一条让作者确认，也不让贴边的字静默被 cover 裁掉。
    y0, y1 = svg_text_band(y, px)
    if y0 < -1e-6 or y1 > vb_h + 1e-6:
        top = y0 < -1e-6
        _fs = f"{px:g}px" if px else f"字号读不出，按 {MIN_READABLE_FONT_PX}px 估"
        return (f"文字越出画布{'上' if top else '下'}边缘"
                f"（基线 y={y:g}，字身 {_fs}）——cover 会裁掉贴边内容，"
                f"把文字整体移进画布")
    x = _parse_num(el.get("x"))
    if x is not None:
        anchor = inherited(el, "text-anchor") or "start"
        if anchor == "start" and x < -1e-6:
            return f"文字起点 x={x:g} 在画布左缘之外——把文字移进画布"
        if anchor == "end" and x > vb_w + 1e-6:
            return f"文字终点 x={x:g} 在画布右缘之外——把文字移进画布"
    return None


def _dist_pt_seg(px, py, x1, y1, x2, y2):
    """点到线段的距离（退化线段按点算）。"""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    qx, qy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - qx, py - qy)


def _in_box(px, py, box, pad):
    x0, y0, x1, y1 = box
    return x0 - pad <= px <= x1 + pad and y0 - pad <= py <= y1 + pad


def _check_loose_ends(strokes, segs, dots, rings, discs, boxes, tol):
    """线条端点是否都悬空：首尾端点没有一个落在"别的"元素上 → warn 一次。

    落点目标 = 其他元素（线段集带归属 owner，自己的线段不算）：线段集 /
    终端圆点 / 大圆圆周带（仅描边） / 实心圆盘（圆心在内即根住） / 文字盒。
    任一端点根住即放过——线只要有一头接上东西就不算画到一半。
    """
    warns = []
    for st in strokes:
        rooted = False
        for ex, ey in st["ends"]:
            if any(_dist_pt_seg(ex, ey, s[0], s[1], s[2], s[3]) <= tol
                   for s in segs if s[4] is not st["owner"]):
                rooted = True
                break
            if any((ex - cx) ** 2 + (ey - cy) ** 2 <= (r + tol) ** 2
                   for (cx, cy, r) in dots):
                rooted = True
                break
            if any(abs(math.hypot(ex - cx, ey - cy) - r) <= tol
                   for (cx, cy, r) in rings):
                rooted = True
                break
            if any((ex - cx) ** 2 + (ey - cy) ** 2 <= (r + tol) ** 2
                   for (cx, cy, r) in discs):
                rooted = True
                break
            if any(_in_box(ex, ey, b, tol) for b in boxes):
                rooted = True
                break
        if not rooted:
            ex, ey = st["ends"][0]
            warns.append(
                f"{st['desc']} 的端点悬空：({ex:g}, {ey:g}) 距最近的笔画/圆点/"
                f"文字 > {tol:g}px——按 craft 的“线要结束在某处”，把端点接到"
                f"另一条线、圆点或文字上（有意悬空的装饰线可加 data-free-ends 声明）")
    return warns


def check_file(path, layout, aspect, theme, resolved=None):
    """查一个 SVG；`resolved` 给一个 dict 时，把 `--layout auto` 实际用的
    (档位, 画幅) 写进去——调用方要把"我按哪一档查的"打给人看，不然 auto 的
    判定是一次没人复核的猜测。"""
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

    # ── 根节点几何 ──
    w, h, note = _root_size(root)
    if note:
        warns.append(note)
    if not w or not h:
        errors.append("根标签读不出纯数字 width/height 或 viewBox（% / em 不算），没法验比例")
        scale = None
    else:
        layout, aspect, _infer_err = _resolve_layout(layout, w, h, aspect)
        if resolved is not None:
            resolved.update(layout=layout, aspect=aspect)
        if _infer_err:
            errors.append(_infer_err)
        if layout == "canvas":
            cw, ch_ = CANVAS[aspect]
            label = f"{cw}×{ch_}（{aspect} 整页画布）"
        else:
            # 槽位框随画幅变（竖屏 980×735 / 横屏 1067×800，都是 4:3）。
            # 折算字号与建议尺寸都必须按当前画幅算——拿竖屏的 980 去折横屏的
            # 1067，26px 会被误判成 24px（假 warn），建议尺寸也永远是竖屏那一档。
            cw, ch_ = SLOT[aspect]
            label = f"4:3（{aspect} 槽位版式，建议 {cw}×{ch_}）"
        # 推断失败时不再叠一条"比例不等比"——同一件事说两遍，且第二遍的说法
        # （目标 4:3）会让人以为是槽位图画错了，其实是谁都不知道它该按哪档查。
        if not _infer_err and not same_aspect(w, h, (cw, ch_)):
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
    strokes = []      # 待查端点悬空的线条：{desc, ends, owner}
    segs = []         # 落点目标：线段（line / polyline / polygon / rect 边 / path），带归属元素
    dots = []         # 落点目标：终端圆点（r ≤ DOT_MAX_R）
    rings = []        # 落点目标：大圆的圆周带（仅描边，圆心在内不算）
    discs = []        # 落点目标：实心大圆盘（圆心在内即根住，覆盖"线画进圆里"）
    boxes = []        # 落点目标：文字盒（粗估宽，只用于根住判定）
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
        # 墙钟动画的 CSS 判定**借用净化器那条正则**，不在我这里另写一份
        # （口径漂移的代价是真问题：净化剥掉的东西与 check 报的东西必须是同一
        # 类）。旧写法是裸的 \b(animation|transition)\b，`.animation-card` 这种
        # 类名、以及 `--animation-duration` 这种自定义属性都会被误判成墙钟动画，
        # 作者画一张静态图也要吃一条假 warn——误报训练人忽略 warn，比漏报更贵。
        if (tag == "style" and el.text and _CSS_WALLCLOCK.search(el.text)) \
                or (el.get("style") and _CSS_WALLCLOCK.search(el.get("style"))):
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
            # 画法，见 canvas.md「图内文字的对比度」），按页底算出来的数对它不成立，
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

            # 文字越界（error）+ 端点落点的文字盒素材
            if w and h and not has_transform_ancestor(el):
                msg = _text_oob(el, inherited, w, h)
                if msg:
                    errors.append(msg)
                ty = _parse_num(el.get("y")) or 0.0
                tx = _parse_num(el.get("x"))
                if px is not None and tx is not None:
                    anchor = inherited(el, "text-anchor") or "start"
                    # 文字宽按"CJK≈1em、拉丁≈0.55em"粗估——只用于端点落点（多算
                    # 只会少报 warn，安全方向）；越界判定刻意不用它（会误报）。
                    est_w = px * sum(1.0 if _CJK.search(ch) else 0.55 for ch in content)
                    ty0, ty1 = svg_text_band(ty, px)
                    if anchor == "end":
                        boxes.append((tx - est_w, ty0, tx, ty1))
                    elif anchor == "middle":
                        boxes.append((tx - est_w / 2, ty0, tx + est_w / 2, ty1))
                    else:
                        boxes.append((tx, ty0, tx + est_w, ty1))

        if el.get("font-family") or _style_map(el).get("font-family"):
            has_font_family = True
        if tag == "style" and el.text and "font-family" in el.text:
            has_font_family = True

        # 满幅背景 rect
        if tag == "rect" and vb_w and vb_h and layout in ("slot", "canvas"):
            rw = _parse_num(el.get("width")) if not str(el.get("width", "")).endswith("%") else None
            rh = _parse_num(el.get("height")) if not str(el.get("height", "")).endswith("%") else None
            pct = str(el.get("width", "")) == "100%" and str(el.get("height", "")) == "100%"
            if pct or (rw and rh and rw >= vb_w * 0.98 and rh >= vb_h * 0.98
                       and (_parse_num(el.get("x")) or 0) <= 1 and (_parse_num(el.get("y")) or 0) <= 1):
                if layout == "slot":
                    warns.append("疑似铺满 viewBox 的背景 <rect>：槽位 SVG 不要铺满幅底（会在页面上形成一圈色差框）")
                else:
                    warns.append("整页画布铺了一张满幅底板 <rect>：模板本来就在这一页底下画好了三层"
                                 "（主题渐变 / 只在页缘显形的网格 / 本段 accent 氛围光），满幅 rect 把三层"
                                 "整个盖掉，画布页就退回一片死平。删掉它让三层透出来（见 canvas.md"
                                 "「整页画布的密度与层次」）。确实要把整页换成另一种底色时可忽略本条")

        # ── 图元越界（error）+ 端点落点素材收集（2D 自证）────────────
        # transform 祖先的元素最终位置静态算不出，跳过（与安全边距同一取舍）。
        if w and h and not has_transform_ancestor(el):
            if tag in ("rect", "circle", "ellipse", "line", "polygon", "polyline", "path"):
                b = _shape_bounds(el, tag, w, h)
                if b:
                    x0, y0, x1, y1 = b
                    if x0 < -1e-6 or y0 < -1e-6 or x1 > w + 1e-6 or y1 > h + 1e-6:
                        errors.append(
                            f"图元越出画布（{x0:g},{y0:g}–{x1:g},{y1:g}，画布 {w:g}×{h:g}）"
                            "——cover 会裁掉贴边内容，把图元整体移进画布")
        _fill = _prop(el, "fill")
        _stroke = _prop(el, "stroke")
        fill_on = _fill is None or (_fill.strip().lower() != "none")
        stroke_on = _stroke is not None and _stroke.strip().lower() != "none"
        if w and h and not has_transform_ancestor(el):
            if tag == "rect" and (fill_on or stroke_on):
                rx = _parse_num(el.get("x")) or 0.0
                ry = _parse_num(el.get("y")) or 0.0
                rw = _parse_num(el.get("width"))
                rh = _parse_num(el.get("height"))
                if rw is not None and rh is not None:
                    segs += [(rx, ry, rx + rw, ry, el), (rx + rw, ry, rx + rw, ry + rh, el),
                             (rx + rw, ry + rh, rx, ry + rh, el), (rx, ry + rh, rx, ry, el)]
            elif tag in ("polygon", "polyline") and (fill_on or stroke_on):
                pts = _pt_list(el.get("points"))
                if pts:
                    for i in range(len(pts) - 1):
                        segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], el))
                    if tag == "polygon":
                        segs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], el))
                    if tag == "polyline" and stroke_on and el.get("data-free-ends") is None:
                        strokes.append({"desc": f"<polyline>（起 {pts[0][0]:g},{pts[0][1]:g}）",
                                        "ends": [pts[0], pts[-1]], "owner": el})
            elif tag == "line" and stroke_on:
                x1_, y1_, x2_, y2_ = (_parse_num(el.get(k)) for k in ("x1", "y1", "x2", "y2"))
                if None not in (x1_, y1_, x2_, y2_):
                    segs.append((x1_, y1_, x2_, y2_, el))
                    if el.get("data-free-ends") is None:
                        strokes.append({"desc": f"<line>（起 {x1_:g},{y1_:g}）",
                                        "ends": [(x1_, y1_), (x2_, y2_)], "owner": el})
            elif tag == "circle" and (fill_on or stroke_on):
                cx, cy, r = _parse_num(el.get("cx")), _parse_num(el.get("cy")), _parse_num(el.get("r"))
                if None not in (cx, cy, r):
                    if r <= DOT_MAX_R:
                        dots.append((cx, cy, r))
                    elif fill_on:
                        discs.append((cx, cy, r))
                    else:
                        rings.append((cx, cy, r))
            elif tag == "ellipse" and (fill_on or stroke_on):
                cx, cy, rx, ry = (_parse_num(el.get(k)) for k in ("cx", "cy", "rx", "ry"))
                if None not in (cx, cy, rx, ry):
                    rr = max(rx, ry)
                    if rr <= DOT_MAX_R:
                        dots.append((cx, cy, rr))
                    elif fill_on:
                        discs.append((cx, cy, rr))
                    else:
                        rings.append((cx, cy, rr))
            elif tag == "path" and stroke_on:
                segs.extend((s[0], s[1], s[2], s[3], el) for s in path_segments(el.get("d") or ""))
                if el.get("data-free-ends") is None:
                    ends = path_endpoints(el.get("d") or "")
                    if ends:
                        strokes.append({"desc": f"<path>（起 {ends[0][0]:g},{ends[0][1]:g}）",
                                        "ends": list(ends), "owner": el})

        # 竖向占位登记（只有画布档用得上：整页构图没有模板兜底，空带得自己发现）
        if layout == "canvas" and w and h:
            band = _ink_band(el, tag, inherited, w, h)
            if band:
                ink_bands.append(band)

    # 线条端点落点（warn）：容差按画幅短边 4‰，至少 2 单位
    if w and h:
        tol = max(END_TOL_MIN, min(w, h) * END_TOL_FRAC)
        warns.extend(_check_loose_ends(strokes, segs, dots, rings, discs, boxes, tol))

    if min_eff is not None and min_eff < MIN_READABLE_FONT_PX:
        warns.append(f"折算后最小字号约 {min_eff:.0f}px"
                     f"（<{MIN_READABLE_FONT_PX}px 下限），两画幅缩放+压缩后读不出来")
    if unknown_size:
        warns.append(f"{unknown_size} 个 <text> 的字号不是 px 数值（em/%/class/缺省），读不出绝对值，请自己按画幅复核")
    if has_cjk and not has_font_family:
        warns.append("含中文但没有任何 font-family：<img> 载入的 SVG 读不到页面字体，请在 <style> 里自带中文无衬线字体栈")
    if has_wallclock:
        warns.append("含墙钟动画（SMIL <animate*> / CSS animation / transition）：这类动画按页面墙上时钟自走，"
                     "无法与口播时间轴同步，只适合氛围循环。要"
                     "「随某句话变化」的时间轴同步动画，改用 images.json 的 director（净化内联后由 GSAP 补间驱动；"
                     "内联时这些墙钟动画会被剥离，见 references/canvas.md「SVG 动画：两档」）")
    if layout == "canvas":
        warns.append("整页画布：字面色的对比度已经按主题页底的最坏一档查过了（上面那几条就是），"
                     "剩下两样静态查不到——压在自己画的局部底板上的那些字（按页底算对它不成立）、"
                     "以及文字是否真的溢出。用 canvas.md「图内文字的对比度」的内联副本 "
                     "+ `hyperframes check` 量一次")
        if w and h:
            gy0, gy1 = _largest_empty_band(ink_bands, h)
            if gy1 - gy0 >= MAX_EMPTY_BAND * h:
                warns.append(
                    f"整页画布有一整条横向空带：y≈{gy0:.0f}–{gy1:.0f}（{gy1 - gy0:.0f}px，"
                    f"占页高 {(gy1 - gy0) / h:.0%}，阈值 {MAX_EMPTY_BAND:.0%}）横着没有任何图元。"
                    f"画布页的构图没人兜底，暂停成片时这就是一页空白——把主视觉撑开到这个带里、"
                    f"或按 canvas.md「整页画布的密度与层次」补中景/底层信息。"
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
    ap.add_argument("--layout", choices=["slot", "canvas", "auto"], default="slot",
                    help="slot = 4:3 槽位配图（默认）；canvas = 整页画布；"
                         "auto = 按每个文件自身的比例推断（混用目录一次跑完，"
                         "整页画布还会连画幅一起定下来）")
    ap.add_argument("--aspect", choices=list(CANVAS), default="portrait",
                    help="画幅（默认 portrait）。显式 --layout 时按它取框；"
                         "--layout auto 下整页画布的比例能定画幅，会覆盖它")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        print("[error] 没找到任何 .svg 文件", file=sys.stderr)
        return 2
    n_err = n_warn = 0
    for f in files:
        box = {}
        errors, warns = check_file(f, args.layout, args.aspect, DEFAULT_THEME,
                                   resolved=box)
        status = "FAIL" if errors else ("WARN" if warns else "OK")
        # auto 判了什么必须打出来：它是按尺寸猜的档位，猜错了要能被一眼看出来
        how = ""
        if args.layout == "auto" and box.get("layout"):
            how = f"  （auto → {box['layout']}"
            if box.get("aspect") and box["aspect"] != args.aspect:
                how += f"/{box['aspect']}"
            how += "）"
        print(f"[{status}] {f}{how}")
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
