#!/usr/bin/env python3
"""把 SVG 净化成可安全内联进成片 HTML 的活 DOM。

内联会让 SVG 成为同源活 DOM（`<img>` 载入时按 data 处理、脚本不执行），所以导演档
（见 `references/svg-motion.md`「契约」）**必须先净化**，让它回到 `<img>` 的安全水位。

同时保证**逐帧 seek 的决定性**（见 rendering.md「动画」）：墙钟自走的东西（CSS
`@keyframes`/`transition`、SMIL `<animate*>`）在成片里会漂移，一并剥掉。要氛围循环
就别写 animate，走 `<img>`。

基于 ElementTree 结构化改写，不用正则删标签（那种能被绕过）。
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.parsers import expat

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _path_morph import (path_last_subpath_start, path_segments, x_span,
                         y_span)  # noqa: E402  path 占位/线段只此一份
from _script_utils import NUMERIC_ONLY  # noqa: E402  数字串判据只有一份，这里在它上面加单位后缀
from _template import same_aspect  # noqa: E402  等比判据（含容差）只有一份

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# 实体声明的拒答文案（净化器与 check_svg 同一份口径）。
_ENTITY_DECL_MSG = ("含 DOCTYPE/ENTITY 声明，拒绝内联（实体膨胀风险）；"
                    "请删掉这些声明，或改用 <img> 载入")

# 序列化时保留默认命名空间与 xlink 前缀，否则 ET 会给每个标签套 ns0: 前缀，
# 浏览器照样认，但产物难读、也和作者手写的 SVG 对不上。
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# 一律删掉的元素（内联后会执行脚本或嵌入不可控 HTML）。
# **标签名一律小写**：ElementTree 是 XML 解析器、保留原始大小写，而浏览器 HTML
# tokenizer 把标签名统一转小写——`<SCRIPT>` 在净化层的小写集合眼里不存在、进了
# 成片 HTML 就是 script 元素（同源活 DOM，直接执行）。判定层必须先折成小写再比，
# 合法 SVG 标签本就小写（foreignObject 是唯一驼峰，折后仍命中）。
_DROP_TAGS = {"script", "foreignobject"}
# 墙钟动画元素：逐帧 seek 下不可复现，内联档剥掉，运动交给时间线补间。
_WALLCLOCK_TAGS = {"animate", "animatetransform", "animatemotion", "set"}
# CSS 里驱动墙钟动画的属性名（在 <style> 文本与 inline style 上都清）。
# 值的终止同时认 `;` 和 `}`：`.a{animation:x}` 结尾没有分号，吃到 `;` 会把闭合 `}`
# 之后下一条规则一起吞掉。
# 左边必须是**声明起始**（`{` / `;` / 串首）：否则 `--animation-duration:2s;fill:red`
# 里的自定义属性会被拦腰截断，连带吃掉后面那条真声明（剩 `--fill:red`）。
_WALLCLOCK_CSS = re.compile(
    r"(?:(?<=[;{])|^)\s*(?:-[a-z]{1,10}-)?(?:animation|transition)(?:-[a-z]+)?\s*:[^;}]*;?",
    re.I | re.M)
# 外链 / 脚本 URL：@import、url(http…)、href 里的 javascript:。url() 里一并拦
# file:/data:（以 file:// 打开 HTML 时可达本机磁盘）。
# 必须吃到收尾 `)`：只吃 scheme 那一截会留下半截坏语法。
# scheme 备选认**任意** scheme 而不只 https?：ftp:/blob:/chrome: 同样发起外部
# 请求；`\\`（CSS 转义还原后剩下的单反斜杠）与 `//`、`/\`、`\/` 一并认——
# WHATWG URL 解析对特殊 scheme 把 `\` 当 `/`，`url(/\evil.com/x.png)` 在浏览器
# 眼里就是协议相对 URL（`\` 与 `/` 混写同理），只认 `//` 会整类漏过。
_EXT_URL = re.compile(
    r"url\(\s*['\"]?\s*(?:[a-z][a-z0-9+.-]*:|\\|[/\\]{2,})[^)]*\)?", re.I)
# CSS 转义序列（CSS Syntax §4.3.7）：`\XXXXXX`（1~6 位十六进制，后可跟一个空白被
# 吃掉）、`\<其它字符>`、以及 `\`+换行（续行，整体抹掉）。浏览器解析 CSS 值与
# URL 之前做的就是这一步还原——净化层不先还原，`\75rl(…)`、`\61nimation:`、
# `java\tscript:`（XML 字符引用 &#9; 解析后落成制表符）这类写法在正则眼里不
# 存在、在浏览器眼里成立（Chromium 与 Firefox 均实测执行）。
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(\r\n|[\r\n\f])|(.))",
                         re.S)
# CSS 字符串字面量（含转义，整段原子）。所有"按标点扫描"的清理都必须把串当作
# 一个整体：`content:"a;b}"` 里的 `;`/`}`、`content:"@import"` 里的 `@` 都不是语法。
# 串尾未闭合时吃到串末——作者写坏的 CSS 原样保留，净化不越权替他修。
_CSS_STRING = re.compile(r'"(?:[^"\\]|\\.)*"?|\'(?:[^\'\\]|\\.)*\'?', re.S)
# 单条 at-rule（CSS Syntax §5.4：`@` + 关键字 + prelude，到 `;` 或 `{` 为止）。
# `;` / `{}` 是块的边界，prelude 里不会真出现（要出现只可能在串里，而串由
# _drop_at_rules 整颗跳过）。关键字按 `@[\w-]+` 取：`@-webkit-keyframes` 也要认，
# 否则整块会被当成"不带块体的指令"，只删题头、块体里的规则落进全局。
_AT_RULE = re.compile(r"@[\w-]+[^{};]*")
# CSS 注释：下面每条判定（@import / 外链 / 墙钟 / @keyframes）都只看真 CSS，所以注释
# 先剥。不剥的后果实测过：`/* 用 @keyframes 做循环 */ .a{fill:red}` 里那条 .a 会被
# 当成 @keyframes 的规则体吃掉。注释无语义，剥掉不损失任何东西。
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# href 的 scheme 判定：任何 "scheme:" 开头都算外部资源（file:/data:/blob:/
# chrome:/javascript: 等一律不收——内联 SVG 是同源活节点，且产物 HTML 常以
# file:// 打开，file: 引用会触及本机磁盘）。内部片段 "#id" 与相对路径除外。
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def _local(tag):
    """剥 XML 命名空间（`{ns}tag` → `tag`）；非字符串一律按空串处理。"""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _style_map(el):
    """inline style → {小写属性名: 值}（重复属性按后者说话，与 CSS 一致）。

    错别字/空白声明（`"a:1;;b:2"`、`";color:red"`）在本层丢掉空名条目——让它们
    落进字典会让下游 `styles.get("")` 之类的查询返回 None 而不是报错，属于把错
    误藏远。取源码里的值之前都先 `_local` 过 k，这里不需要再剥一次命名空间。
    """
    out = {}
    for decl in (el.get("style") or "").split(";"):
        name, sep, value = decl.partition(":")
        if sep and name.strip():
            out[name.strip().lower()] = value.strip()
    return out


def _css_unescape(s):
    """按 CSS 转义规则还原字符串（见 _CSS_ESCAPE 的契约注释）。"""
    def repl(m):
        if m.group(1) is not None:
            cp = int(m.group(1), 16)
            if cp == 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                return "\ufffd"
            return chr(cp)
        if m.group(2) is not None:
            return ""          # 续行：反斜杠与换行一起抹掉
        return m.group(3)      # 普通转义：只吃反斜杠
    return _CSS_ESCAPE.sub(repl, s)


def _is_external_href(value):
    # WHATWG URL 解析器的同款预处理：先抹掉所有制表/换行/回车（`java\tscript:`
    # 在浏览器眼里就是 `javascript:`——XML 字符引用 &#9;/&#10;/&#13; 解析后就
    # 落成这些字符，scheme 正则不过它们），再撕掉首尾的 C0 控制与空格
    # （`\x01javascript:` 同理）。不按这条口径看，判定层看不见浏览器看见的
    # 那个 URL。
    # 反斜杠归一成 `/`：WHATWG 解析器对特殊 scheme 把 `\` 视作 `/`，
    # `href="/\evil.com"` 在浏览器眼里就是 `//evil.com`（协议相对 URL）。
    # 相对路径里的 `\`（Windows 写法）归一后仍不是 scheme/`//`，不受影响。
    v = re.sub(r"[\t\n\r]", "", value or "").lower()
    v = v.replace("\\", "/")
    v = re.sub(r"^[\x00-\x20]+", "", v)
    v = re.sub(r"[\x00-\x20]+$", "", v)
    if not v or v.startswith("#"):
        # 空值 / 内部片段引用（<use href="#id"> 同文档引用）合法。
        return False
    return (_SCHEME_RE.match(v) is not None
            or v.startswith("//")
            or _EXT_URL.search(v) is not None)


# ── 满幅底板判据（单一真源）─────────────────────────────────────────
# check_svg.py 的自查 warn、gen_hyperframes.py 的生成期 warn、canvas_kit.py 的
# 发射期报错都以这一份为准，谁都不许再抄一份（SVG 应透明、让模板三层透出来，
# 见 references/canvas.md「不铺满幅底」）。
#
# 判的是**"这个元素的可见填充会不会把页面背景盖死"**，不是"它是不是 <rect>"：
# 同一个视觉结果（一张铺满 viewBox 的底色）有 rect / polygon / path 三种等价
# 写法，只认 rect 时另外两种一声不吭——而那正是「不铺满幅底」要防的事：槽位
# 版式在页面上形成一圈色差框，整页画布把模板三层（渐变 / 网格 / 氛围光）整个
# 盖掉退回一片死平。实测漏网：`<path d="M0 0 H980 V735 H0 Z">`、
# `<polygon points="0,0 980,0 980,735 0,735">` 都零 warn 通过。
#
# 占比阈值与贴边容差是**两个数字**，canvas_kit 的发射期报错用同一对（改这里
# 就必须一起动那边，所以那两个常量从这里导入，不在 canvas_kit 再写一份）。
FULL_BLEED_FRAC = 0.98    # 形状占位达到 viewBox 这个比例算"铺满"
FULL_BLEED_EDGE = 1.0     # 起点贴边容差：x=0 与 x=1 同判
_FILL_SCANLINES = 240     # 填充面积量几条扫描线：矩形/折线下结果与线数无关，曲线近似才受它影响
# 数字主体直接复用 `NUMERIC_ONLY`（含指数写法 `1e3`），只在外面加一层单位后缀：
# 两个判据各写一份时，`width="1e3"` 会在一道门里读得出、在另一道里被当字符串。
_NUM = re.compile(r"^\s*(" + NUMERIC_ONLY.pattern[1:-1] + r")\s*(px)?\s*$")


def _parse_num(v):
    """纯数字（可带 px 后缀、可带指数）→ float；其他（% / em / 缺省）→ None。"""
    if v is None:
        return None
    m = _NUM.match(str(v))
    return float(m.group(1)) if m else None


def svg_points(points):
    """polygon/polyline 的 points 属性 → [x0, y0, x1, y1, ...]（扁平列表）；
    读不全返回 None。

    切分口径只有这一份：`check_svg` 的占位盒、`_cam_crop` 的投影盒、
    `_polygon_backdrop` 的满幅判据必须读同一个 points，否则同一份 SVG 在两道
    门里量出两个盒子。

    **读不全就整条返回 None，不丢掉读不出的那几个再按奇偶配对**——丢值之后
    个数仍可能是偶数，配对从错的那一个起全部错位，量出一个谁也没画过的盒子
    且不报错。`points="0,0 1e2,1e2 200,100 0,100"` 就是这条：丢掉两个 1e2 后
    剩下 4 个数，照样凑出"合法"的两个点。
    """
    if not points:
        return None
    vals = []
    for tok in re.split(r"[,\s]+", str(points).strip()):
        if tok == "":
            continue
        v = _parse_num(tok)
        if v is None:
            return None
        vals.append(v)
    return vals if len(vals) >= 4 and not len(vals) % 2 else None


def _covers_viewbox(x0, y0, x1, y1, vb_w, vb_h):
    """形状占位 [x0,x1]×[y0,y1] 是否铺满 viewBox：两轴都够宽 + 起点贴边。

    起点略微越界（x=-5）算贴边——负偏移只会盖得更多，不会更少。
    """
    return (x1 - x0 >= vb_w * FULL_BLEED_FRAC
            and y1 - y0 >= vb_h * FULL_BLEED_FRAC
            and x0 <= FULL_BLEED_EDGE and y0 <= FULL_BLEED_EDGE)


def _fill_hidden(el):
    """元素有没有可见填充：`fill:none` 与 `opacity:0` 都不盖任何东西。

    旧判据连这两种也 warn，是误报——透明的满幅 rect 无害，而误报训练人忽略
    warn，比漏报更贵。只认元素自己身上的 inline style 与呈现属性：class/id
    控制的 fill 读不到，那种按"有填充"算（warn 方向，不漏报）。
    """
    st = _style_map(el)
    fill = st.get("fill", el.get("fill"))
    if fill is not None and fill.strip().lower() == "none":
        return True
    opacity = st.get("opacity", el.get("opacity"))
    if opacity is not None:
        try:
            if float(str(opacity).strip()) == 0.0:
                return True
        except ValueError:
            pass
    return False


def _rect_len(v, full):
    """长度属性 → 用户单位数（`%` 按对应轴的满量折算）；读不出 → None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s.endswith("%"):
        p = _parse_num(s[:-1])
        return p * full / 100.0 if p is not None else None
    return _parse_num(s)


def _rect_backdrop(el, vb_w, vb_h):
    """rect：宽高占满（100% / 像素 / 略微越界都算），起点贴边。"""
    rw = _rect_len(el.get("width"), vb_w)
    rh = _rect_len(el.get("height"), vb_h)
    if not (rw and rh):
        return False
    x = _rect_len(el.get("x"), vb_w) or 0.0
    y = _rect_len(el.get("y"), vb_h) or 0.0
    return _covers_viewbox(x, y, x + rw, y + rh, vb_w, vb_h)


def _polygon_backdrop(el, vb_w, vb_h):
    """polygon：points 的占位盒铺满。

    points 解析走 `svg_points`（与 check_svg / _cam_crop 同一份），不在这里另写。
    """
    nums = svg_points(el.get("points"))
    if not nums:
        return False          # points 里混进命令字母之类：读不出占位就不判
    xs, ys = nums[0::2], nums[1::2]
    return _covers_viewbox(min(xs), min(ys), max(xs), max(ys), vb_w, vb_h)


def _path_fill_ratio(d, vb_w, vb_h):
    """path 的填充（even-odd 扫描线）占 viewBox 的比例。

    bbox 铺满不等于填充铺满：一条对角线的 bbox 占满画面、面积是 0，报它就是误报
    （warn 贬值比漏报更贵）。逐水平线奇偶配对累加覆盖宽；曲线按弦近似，误差只让
    面积略小（安全方向）。
    """
    try:
        segs = path_segments(d)
    except ValueError:
        return 0.0
    if not segs:
        return 0.0
    # 未闭合 path 的 fill 在浏览器眼里是隐式闭合的（首尾拉一条直线）：
    # `M0 0 H980 V735 H0` 漏写 Z，视觉上照样铺满——不补这段就漏报它。
    # 闭合回的是**最后一条子路径**的起点：多子路径 path 里每个子路径各自闭合
    # （`… Z … Z`），回第一条子路径的起点会凭空多一条贯穿画面的斜边，even-odd
    # 的奇偶被它翻转——整幅铺满的多边形反而算成约一半，底板判据因此漏报。
    hx, hy = path_last_subpath_start(d)
    tx, ty = segs[-1][2], segs[-1][3]
    if (hx, hy) != (tx, ty):
        segs = segs + [(tx, ty, hx, hy)]
    covered = 0.0
    row = vb_h / _FILL_SCANLINES
    for i in range(_FILL_SCANLINES):
        y = row * (i + 0.5)
        xhits = []
        for x1, y1, x2, y2 in segs:
            lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
            if lo <= y < hi:                # 半开区间：顶点只算一次
                xhits.append(x1 + (x2 - x1) * (y - y1) / (y2 - y1))
        xhits.sort()
        for j in range(0, len(xhits) - 1, 2):
            covered += max(0.0, min(xhits[j + 1], vb_w) - max(xhits[j], 0.0)) * row
    return covered / (vb_w * vb_h)


def _path_backdrop(el, vb_w, vb_h):
    """path：`d` 的轴投影占位铺满，且**填充**也铺满。

    bbox 用 `_path_morph.x_span / y_span`（按命令语义走光标，比切数字取极值准），
    只当快筛：铺满的再过 `_path_fill_ratio` 的填充面积，对角线/细线因此不误报。
    面积按 even-odd 算，"挖空一条"的 path 如实只算框条。
    """
    d = el.get("d")
    if not d:
        return False
    try:
        xs, ys = x_span(d), y_span(d)
    except ValueError:
        return False          # d 畸形：读不出占位就不判
    if not xs or not ys:
        return False
    if not _covers_viewbox(xs[0], ys[0], xs[1], ys[1], vb_w, vb_h):
        return False
    return _path_fill_ratio(d, vb_w, vb_h) >= FULL_BLEED_FRAC


def is_full_bleed_backdrop(el, vb_w, vb_h):
    """元素的可见填充形状是否铺满 viewBox（背景底板）。

    `vb_w/vb_h` 必须来自 `root_viewbox_size`（拿固有像素尺寸判会把铺满的底板判成
    只占一半）。覆盖 rect/polygon/path；`fill:none` / `opacity:0` 不判。
    """
    if not vb_w or not vb_h:
        return False
    # 折小写再比：`<RECT>` 在浏览器眼里就是 rect（HTML tokenizer 转小写），
    # 大小写敏感会把满幅底板整类放过去。
    tag = _local(el.tag).lower()
    if tag == "rect":
        covers = _rect_backdrop(el, vb_w, vb_h)
    elif tag == "polygon":
        covers = _polygon_backdrop(el, vb_w, vb_h)
    elif tag == "path":
        covers = _path_backdrop(el, vb_w, vb_h)
    else:
        return False
    return bool(covers) and not _fill_hidden(el)


def shape_bbox(el, tag):
    """图元在**自身坐标系**里的占位盒 (x0, x1, y0, y1)；读不出返回 None。

    `check_svg`（端点落点 / 越界 / 空带）与 `_cam_crop`（运镜取景）共用这一份。
    两处各写一套 rect/circle/ellipse/… 的包围盒时，"同一张图在两道门里占不同
    大小的盒子"只能靠注释同步——而注释不会在有人改了一侧之后报错。

    `text` / `image` 不在这里：它们要字号与锚点，口径由各调用方自己补（见
    `_cam_crop._box`）。
    """
    def num(name):
        return _parse_num(el.get(name))

    if tag == "rect":
        x, y, w, h = num("x"), num("y"), num("width"), num("height")
        if w is None or h is None:
            return None
        x, y = x or 0.0, y or 0.0
        return (x, x + w, y, y + h)
    if tag == "circle":
        cx, cy, r = num("cx"), num("cy"), num("r")
        if None in (cx, cy, r):
            return None
        return (cx - r, cx + r, cy - r, cy + r)
    if tag == "ellipse":
        cx, cy, rx, ry = num("cx"), num("cy"), num("rx"), num("ry")
        if None in (cx, cy, rx, ry):
            return None
        return (cx - rx, cx + rx, cy - ry, cy + ry)
    if tag == "line":
        x1, y1, x2, y2 = num("x1"), num("y1"), num("x2"), num("y2")
        if None in (x1, y1, x2, y2):
            return None
        return (min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2))
    if tag in ("polygon", "polyline"):
        pts = svg_points(el.get("points"))
        if not pts:
            return None
        xs, ys = pts[0::2], pts[1::2]
        return (min(xs), max(xs), min(ys), max(ys))
    if tag == "path":
        # path 用 x_span/y_span（含曲线控制点，宁可高估——越界是 error，低估会漏报）
        try:
            xs, ys = x_span(el.get("d") or ""), y_span(el.get("d") or "")
        except ValueError:                     # 畸形 d：没法算，按未知处理
            return None
        if not xs or not ys:
            return None
        return (xs[0], xs[1], ys[0], ys[1])
    return None


# ── 根标签的两套尺寸（真源只此一份）─────────────────────────────────
# SVG 根标签上有**两个不是一回事**的尺寸，混用就是"两道门给两个答案"：
#   ① 固有像素尺寸（width/height，没有时退回 viewBox）——浏览器按它定这张图的
#      原始大小，"够不够大 / 比例对不对得上画幅槽位"问的是这一对；
#   ② viewBox 坐标系尺寸——图元坐标、字号、rect 的宽高全都写在这一套里，
#      "文字越界 / rect 铺没铺满"问的是这一对。
# 缩放写法（width="800" height="600" viewBox="0 0 400 300"，手写 SVG 最常见）
# 下两套差一个倍数。此前 check_svg 与 gen_hyperframes 各写一份取尺寸的函数，
# 且都把 ① 当成 ② 喂给满幅判据：铺满 400×300 的底板被判成
# "只占一半、没铺满"，两道门都一声不吭，成片里就是一张铺满的死平底板。
def root_px_size(root):
    """根标签的**固有像素尺寸** (宽, 高, 说明)；读不出时宽高为 None。

    `check_svg` 与 `gen_hyperframes` 都调这一份。说明串非空 = width/height 与
    viewBox 比例不一致（preserveAspectRatio 会留白或拉伸）。
    """
    w, h = _parse_num(root.get("width")), _parse_num(root.get("height"))
    vbw, vbh = _viewbox_size(root)
    if w and h:
        if vbw and vbh and not same_aspect(w, h, (vbw, vbh)):
            return w, h, (f"width/height 比例 {w / h:.3f} 与 viewBox 比例 "
                          f"{vbw / vbh:.3f} 不一致")
        return w, h, ""
    if vbw and vbh:
        return vbw, vbh, ""
    return None, None, ""


def root_viewbox_size(root):
    """根标签的**坐标系尺寸** (宽, 高)：图元坐标写在这一套里。

    viewBox 优先；没有 viewBox 时退回固有尺寸（那时两者本就合一）。
    """
    vbw, vbh = _viewbox_size(root)
    if vbw and vbh:
        return vbw, vbh
    w, h, _note = root_px_size(root)
    return w, h


def _viewbox_size(root):
    """viewBox 的第 3/4 段（宽、高）；没有或写坏了返回 (None, None)。"""
    vb = root.get("viewBox")
    if not vb:
        return None, None
    parts = re.split(r"[,\s]+", vb.strip())
    if len(parts) != 4:
        return None, None
    try:
        return float(parts[2]), float(parts[3])
    except ValueError:
        return None, None


def _outside_strings(pattern, css, fn):
    """对 css 里每个字符串字面量**之外**的片段执行 fn（串原样保留）。"""
    parts, last = [], 0
    for m in pattern.finditer(css):
        parts.append(fn(css[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(fn(css[last:]))
    return "".join(parts)


def _at_key(head):
    """at-rule 块头 → note 里那个名字（只取 `@` 与关键字，别把整段 prelude 抄进去）。"""
    return head.strip().split(None, 1)[0].rstrip("{:")


def _drop_at_rules(css, notes):
    """删掉所有 at-rule，且**不碰字符串字面量**。

    这里不能套 `_outside_strings`：它要求 pattern 的匹配边界与"串边界"一致，而
    at-rule 块是任意长的（块体里本来就有串），一旦按串切段，块体被剖成几份、配平
    从半截开始——实测 `@media (x){.a{content:"{}"}}` 会把后面的规则一起吃掉。
    所以整条扫描：自己认串（整颗跳过），只在串外认 at-rule。

    - 带块体的（`@media …{}` / `@supports …{}` / `@font-face{}`）：连块头带块体
      一起删。只删块头会让块体里的选择器变成全局规则，作用于作者没期望的元素；
      `@media` 在成片里本就没有意义（SVG 按画布整体缩放，视口宽度不是画图坐标系）。
    - 不带块体的（`@import …;` / `@charset …;` / `@namespace …;`）：只删指令。
    """
    out, i, n = [], 0, len(css)
    while i < n:
        ch = css[i]
        if ch in "\"'":
            m = _CSS_STRING.match(css, i)
            out.append(m.group(0))
            i = m.end()
            continue
        m = _AT_RULE.match(css, i) if ch == "@" else None
        if not m:
            out.append(ch)
            i += 1
            continue
        if css[m.end():m.end() + 1] != "{":
            # 无块体：指令到 `;` 为止，`;` 一起吃掉（作者漏写就算了）。
            j = m.end() + (css[m.end():m.end() + 1] == ";")
            notes.append(f"<style> 里的 {_at_key(m.group(0))} 已删除"
                         "（外链资源不进成片）")
            i = j
            continue
        depth, j = 1, m.end() + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        if depth == 0:
            key = _at_key(m.group(0))
            if "keyframes" in key:
                # @keyframes 的真实删除原因是墙钟动画定义，不是"外部资源"——
                # note 说错原因会把作者引去删一条不存在的外链。
                why = "（墙钟动画的定义；要动请用 animate 补间）"
            else:
                why = ("（作者漏了分号，按 CSS 语法它已吞进下一条规则）"
                       if "\n" in m.group(0) else "（成片里要么无意义，要么是外部资源）")
            notes.append(f"<style> 里的 {key} 整块已删除{why}")
            i = j
        else:
            # 括号没配平 = 作者写坏了 CSS：只删块头，剩余文本原样保留。
            notes.append(f"<style> 里的 {_at_key(m.group(0))} 块头已删除"
                         "（括号没配平，剩余文本原样保留）")
            i = m.end()
    return "".join(out)


def _clean_style_text(css, notes):
    """从 <style> 文本里剥掉墙钟动画声明、外链 url()/@import 与其余整块 at-rule。

    顺序：剥注释 → 还原 CSS 转义 → at-rule（整块删，自带串识别）→ 其余两条按
    "串外片段"处理。前两步与浏览器解析 CSS 的前置处理同序，否则
    `\\75rl(https://…)`、`\\61nimation:x` 在正则眼里不存在、在浏览器眼里成立。

    @keyframes 的定义体由上一步的 at-rule 整块删除覆盖（含厂商前缀与残缺
    括号两种形态），这里不再单独扫一遍：那样只会命中字符串字面量里的
    "@keyframes" 字样，而 _strip_keyframes 的括号配平会把串内容剖掉——
    `content:"@keyframes k{…}"` 被改成 `content:""`，作者的字面量平白受损。
    """
    if not css:
        return css
    out = _drop_at_rules(_css_unescape(_CSS_COMMENT.sub("", css)), notes)

    def strip(s):
        if _EXT_URL.search(s):
            s = _EXT_URL.sub("url(about:blank)", s)
            notes.append("<style> 里的外链 url() 已中和")
        if _WALLCLOCK_CSS.search(s):
            s = _WALLCLOCK_CSS.sub("", s)
            notes.append("<style> 里的 CSS animation/transition 声明已剥离"
                         "（墙钟动画在逐帧渲染里不可复现；要动请用 animate 补间）")
        # 剥空的规则体连选择器一起清掉（`.a{animation:x 1s}` 剥完只剩 `.a{}`）
        return re.sub(r"(?m)^[ \t]*[^{}\n]*\{[ \t\r\n]*\}[ \t]*\n?", "", s)

    return _outside_strings(_CSS_STRING, out, strip)


def _clean_style_attr(el, notes):
    st = el.get("style")
    if not st:
        return
    # 与 <style> 文本同两条预处理：注释先剥（`style="fill:red;/*c*/animation:x 1s"`
    # 在浏览器眼里就是一条活的墙钟声明——实测注释后面的 animation 原样存活），
    # 转义后还原（`\61nimation:` 同理）。
    new = _css_unescape(_CSS_COMMENT.sub("", st))
    if _EXT_URL.search(new):
        new = _EXT_URL.sub("url(about:blank)", new)
        notes.append(f"<{_local(el.tag)}> 的 style 外链 url() 已中和")
    if _WALLCLOCK_CSS.search(new):
        new = _WALLCLOCK_CSS.sub("", new)
        notes.append(f"<{_local(el.tag)}> 的 style 墙钟动画已剥离")
    if new != st:
        new = new.strip().strip(";")
        if new:
            el.set("style", new)
        else:
            del el.attrib["style"]


def parse_svg_strict(raw):
    """解析 SVG 文本 → root Element；含实体声明或不是良构 XML 时抛 ValueError。

    实体膨胀发生在**解析期**，事后改写救不回来，所以必须在建树之前拦。
    不用 `re.search(r"<!DOCTYPE|<!ENTITY", raw)` 扫全文：注释、`<desc>` 文本、
    属性值里的同名串一律命中——实测 `<!-- <!DOCTYPE svg> -->` 会把一份干净的
    SVG 判成"拒绝内联"，而它根本没声明实体。改用 expat 的 `EntityDeclHandler`：
    只在**真的**出现实体声明时回调，零误报；顺带把良构性也验了（外部 DTD
    expat 默认不取，无 XXE 面）。
    """
    def _reject(*_args):
        raise ValueError(_ENTITY_DECL_MSG)

    probe = expat.ParserCreate()
    probe.EntityDeclHandler = _reject
    try:
        probe.Parse(raw, True)
    except ValueError:
        raise
    except expat.ExpatError as e:
        raise ValueError(f"SVG XML 解析失败，拒绝内联: {e}")
    return ET.fromstring(raw)


def sanitize_svg_for_inline(raw):
    """把 SVG 源文本净化成可内联的活 DOM 串。

    Returns:
        (markup, notes)：markup 是改写后的 `<svg …>…</svg>` 字符串（root 已按
        cover 语义重写为 100%×100% + preserveAspectRatio slice，viewBox 保留）；
        notes 是人类可读的净化说明（去掉了什么），调用方按 [warn] 打出来。

    Raises:
        ValueError：不是 SVG / 含 DOCTYPE-ENTITY / 解析失败——一律拒绝内联，
        退回 `<img>`（报错口径由调用方定）。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("SVG 内容为空")
    # 必须在解析前拦：ENTITY 膨胀在解析阶段就发生，事后改写救不回来。
    root = parse_svg_strict(raw)
    if _local(root.tag) != "svg":
        raise ValueError("根节点不是 <svg>，拒绝内联")

    notes = []

    # 结构化改写：删除危险元素与墙钟动画元素，清属性。用父指针表原地删，
    # 避免边遍历边改树。
    # 标签名先折小写：_DROP_TAGS/_WALLCLOCK_TAGS 与下面的 style 判定都按小写
    # 集合比（浏览器 HTML tokenizer 同款口径，见那两个集合的注释）。
    parent = {c: p for p in root.iter() for c in p}
    for el in list(root.iter()):
        tag = _local(el.tag).lower()
        if tag in _DROP_TAGS:
            p = parent.get(el)
            if p is not None:
                p.remove(el)
            notes.append(f"<{tag}> 已删除（内联后会执行脚本/嵌入 HTML）")
            continue
        if tag in _WALLCLOCK_TAGS:
            p = parent.get(el)
            if p is not None:
                p.remove(el)
            notes.append(f"SMIL <{tag}> 已删除（墙钟动画在逐帧渲染里不可复现；"
                         "要动请用 animate 补间）")
            continue
        # 事件属性 on*、外部/脚本 href 一律摘掉。
        for k in list(el.attrib):
            lk = _local(k).lower()
            if lk.startswith("on"):
                del el.attrib[k]
                notes.append(f"<{tag}> 的事件属性 {lk} 已删除")
            elif lk == "href" and _is_external_href(el.get(k)):
                del el.attrib[k]
                notes.append(f"<{tag}> 的外链/脚本 href 已删除")
        if tag == "style":
            el.text = _clean_style_text(el.text, notes)
        else:
            _clean_style_attr(el, notes)
            # 呈现属性里的 url()：style 那条路管的是 `style=""` 里的写法，而
            # fill / stroke / filter / mask / clip-path / marker-* 这些**属性**同样
            # 能放 url()，此前整条漏网（`fill="url(https://…)"` 原样内联，浏览器照样
            # 去取那个地址，`file:` 还能触及本机磁盘）。外延到此为止：只中和 url()，
            # 不删属性本身（那是覆写作者意图，且这些属性多数是颜色/变换）；内部的
            # `url(#id)` 正则本就不命中，不受影响。
            for k in list(el.attrib):
                if _local(k).lower() == "style":     # style 已由上一步清过
                    continue
                v = el.get(k) or ""
                # 呈现属性的值也过 CSS 转义：`fill="url(\68ttp://…)"` 在浏览器
                # 眼里就是一条外链 url()。不含反斜杠时 decoded 就是原值，行为
                # 与改造前逐字节一致；判定不命中的转义不重写（不动作者其余字节）。
                decoded = _css_unescape(v) if "\\" in v else v
                if _EXT_URL.search(decoded):
                    el.set(k, _EXT_URL.sub("url(about:blank)", decoded))
                    notes.append(f"<{tag}> 的 {_local(k)} 属性外链 url() 已中和")

    # root 重写成 cover 语义：铺满 .seg-image 容器，短边对齐、长边居中裁切，
    # 和 <img>+object-fit:cover 的取景一致（作者按 4:3 槽位或整幅画布画的
    # viewBox 原样保留，几何不变）。
    root.set("width", "100%")
    root.set("height", "100%")
    root.set("preserveAspectRatio", "xMidYMid slice")
    # xmlns 不用手动补：register_namespace("", SVG_NS) 已让 tostring 在 root 上
    # 输出默认命名空间声明，再 set 一次会得到两个 xmlns 属性。

    markup = ET.tostring(root, encoding="unicode")
    return markup, list(dict.fromkeys(notes))
