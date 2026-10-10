#!/usr/bin/env python3
"""把 SVG 净化成可安全内联进成片 HTML 的活 DOM。

背景：`references/image_options.md`「图内文字的对比度」记的那条老规矩是
"交付 HTML 必须保持 `<img>`"——理由是 `<img>` 载入的 SVG 按 data 处理，
浏览器不执行它的 `<script>`；一旦内联进 DOM 它就成了同源活 SVG，脚本照跑。
"导演"（时间轴同步的 SVG 动画，见 image_options.md 方式 C）必须内联才能让
GSAP 逐帧驱动图内元素，所以内联不再是禁区，但**必须先净化**：这一层就是把
"内联会执行脚本"的风险拆掉，让内联回到和 `<img>` 同样安全的水位。

同时服务第二个不变量——**逐帧 seek 的决定性**（见 rendering.md「动画」）：
渲染器按时间线时刻 seek 每一帧，凡按墙上时钟自走的东西（CSS `@keyframes` /
`transition`、SMIL `<animate*>`）都会在成片里漂移、不可复现。所以净化时把这
类墙钟动画一并剥掉；导演的运动全部改由时间线补间驱动。要"氛围循环"就别写
director，走 `<img>` 那条路（墙钟循环，见 image_options.md）。

只用标准库。净化基于 ElementTree 结构化改写，不做正则删标签那种能被绕过的活。
"""
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# 序列化时保留默认命名空间与 xlink 前缀，否则 ET 会给每个标签套 ns0: 前缀，
# 浏览器照样认，但产物难读、也和作者手写的 SVG 对不上。
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

# 一律删掉的元素（内联后会执行脚本或嵌入不可控 HTML）。
_DROP_TAGS = {"script", "foreignObject"}
# 墙钟动画元素：逐帧 seek 下不可复现，director 模式剥掉，运动交给时间线补间。
_WALLCLOCK_TAGS = {"animate", "animateTransform", "animateMotion", "set"}
# CSS 里驱动墙钟动画的属性名（在 <style> 文本与 inline style 上都清）。
# 值的终止要同时认 `;` 和 `}`：手绘 SVG 里 `.a{animation:x}` 结尾没有分号，
# 若值吃到 `;` 为止会把闭合 `}` 之后下一条规则一起吞掉。
#
# 左边必须是一个**声明起始**（规则开括号 / 前一条声明的分号 / 串开头）。没有这个
# 边界，CSS 自定义属性会被拦腰截断：`--animation-duration:2s;fill:red` 里
# `animation-duration:` 同样命中旧写法，剥完之后 `--` 残留、`fill` 那条真声明被
# 当成属性值一并吃掉（实测剩 `--fill:red`）——作者写的颜色在成片里静默消失。
# 单短横的厂商前缀（-webkit-animation）照样放行，那是真 CSS 属性；
# `--` 开头是自定义属性，天然不匹配。
_WALLCLOCK_CSS = re.compile(
    r"(?:(?<=[;{])|^)\s*(?:-[a-z]{1,10}-)?(?:animation|transition)(?:-[a-z]+)?\s*:[^;}]*;?",
    re.I | re.M)
# 外链 / 脚本 URL：@import、url(http…)、以及 href 里的 javascript:。
# url() 上下文一并拦 file:/data:——CSS 里 url(file:…) 同样会把本地文件拉进
# 成片（HTML 以 file:// 打开时可达本机磁盘）。
# 匹配一直吃到收尾的 `)`：替换是"整颗 url() 换成 about:blank"，只吃 scheme 那一截
# 会留下半截（`url(about:blank//evil.com/x.png)` 既不成语法、URL 也还看得见）。
_EXT_URL = re.compile(r"url\(\s*['\"]?\s*(?:https?:|//|file:|data:)[^)]*\)?", re.I)
# CSS 注释：下面每条判定（@import / 外链 / 墙钟 / @keyframes）都只看真 CSS，所以注释
# 先剥。不剥的后果实测过：`/* 用 @keyframes 做循环 */ .a{fill:red}` 里那条 .a 会被
# 当成 @keyframes 的规则体吃掉。注释无语义，剥掉不损失任何东西。
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# href 的 scheme 判定：任何 "scheme:" 开头都算外部资源（file:/data:/blob:/
# chrome:/javascript: 等一律不收——内联 SVG 是同源活节点，且产物 HTML 常以
# file:// 打开，file: 引用会触及本机磁盘）。内部片段 "#id" 与相对路径除外。
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)
# @keyframes 规则（含厂商前缀）的起始标记：剥定义用的，见 _strip_keyframes。
# 前缀组 [a-z]* 可为零字符，纯 @keyframes 也要命中（@-webkit-keyframes 靠 -? 接上）。
_KEYFRAMES_AT = re.compile(r"@-?[a-z]*-?keyframes\b", re.I)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _is_external_href(value):
    v = (value or "").strip().lower()
    if not v or v.startswith("#"):
        # 空值 / 内部片段引用（<use href="#id"> 同文档引用）合法。
        return False
    return (_SCHEME_RE.match(v) is not None
            or v.startswith("//")
            or _EXT_URL.search(v) is not None)


def _strip_keyframes(css):
    """剥掉 @keyframes（含厂商前缀）规则体：墙钟动画的定义本身。

    声明剥掉后规则体还留在 <style> 里，等于把作者写死的旧动画原样带进
    成片——@keyframes 本身不驱动任何元素（惰性），但"墙钟动画已剥净"的
    不变量应该连定义一起兑现。用括号配平扫：规则体里每个 keyframe 选择器
    各带一层 {}（总深度 2），配平到 0 就是规则结束。遇到未闭合的残缺规则
    就保留剩余文本（解析器对坏 CSS 的容忍，净化不越权去修它）。

    存在性判定用 `_KEYFRAMES_AT`（唯一真源）而不是再写一遍 "@keyframes" 子串：
    子串认不出厂商前缀（实测 `@-webkit-keyframes` 整块留下不剥），而前缀形式正是
    它存在的理由。
    """
    m0 = _KEYFRAMES_AT.search(css)
    if not m0:
        return css
    out = []
    i, n = 0, len(css)
    while i < n:
        m = _KEYFRAMES_AT.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        j, depth = m.start(), 0
        while j < n:
            ch = css[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        if depth != 0:
            # 没配平到 0 = 残缺规则。按上面那条口径保留剩余文本：硬删会把
            # @keyframes 之后**所有**合法规则一起吞掉（实测注释里提一句就把
            # 下一条规则吃掉），净化不越权替作者修坏 CSS。
            out.append(css[m.start():])
            break
        i = j
    return "".join(out)


def _clean_style_text(css, notes):
    """从 <style> 文本里剥掉墙钟动画（声明 + @keyframes 定义）与外链 url()/@import。"""
    if not css:
        return css
    out = _CSS_COMMENT.sub("", css)
    if re.search(r"@import", out, re.I):
        out = re.sub(r"@import[^;]*;?", "", out, flags=re.I)
        notes.append("<style> 里的 @import 已删除（外链资源不进成片）")
    if _EXT_URL.search(out):
        out = _EXT_URL.sub("url(about:blank)", out)
        notes.append("<style> 里的外链 url() 已中和")
    if _WALLCLOCK_CSS.search(out):
        out = _WALLCLOCK_CSS.sub("", out)
        notes.append("<style> 里的 CSS animation/transition 声明已剥离"
                     "（墙钟动画在逐帧渲染里不可复现；要动请用 director 补间）")
    if _KEYFRAMES_AT.search(out):
        out = _strip_keyframes(out)
        notes.append("<style> 里的 @keyframes 规则已剥离"
                     "（墙钟动画的定义；要动请用 director 补间）")
    # 声明被剥掉后留下的空壳选择器（.a{}）一并清掉，不留死规则。
    out = re.sub(r"[^{}]*\{\s*\}", "", out)
    return out


def _clean_style_attr(el, notes):
    st = el.get("style")
    if not st:
        return
    new = st
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


def sanitize_svg_for_inline(raw):
    """把 SVG 源文本净化成可内联的活 DOM 串。

    Returns:
        (markup, notes)：markup 是改写后的 `<svg …>…</svg>` 字符串（root 已按
        cover 语义重写为 100%×100% + preserveAspectRatio slice，viewBox 保留）；
        notes 是人类可读的净化说明（去掉了什么），调用方按 [warn] 打出来。

    Raises:
        ValueError：不是 SVG、含 DOCTYPE/ENTITY、或 XML 解析失败——这些一律
        拒绝内联，退回 `<img>`（由调用方决定报错口径）。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("SVG 内容为空")
    # 先拦实体声明再解析：ENTITY 膨胀（billion laughs）在解析阶段就已经发生，
    # 结构化改写救不回来，只能在入口拒。
    if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise ValueError("含 DOCTYPE/ENTITY 声明，拒绝内联（实体膨胀风险）；"
                         "请删掉这些声明，或改用 <img> 载入")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ValueError(f"SVG XML 解析失败，拒绝内联: {e}")
    if _local(root.tag) != "svg":
        raise ValueError("根节点不是 <svg>，拒绝内联")

    notes = []

    # 结构化改写：删除危险元素与墙钟动画元素，清属性。用父指针表原地删，
    # 避免边遍历边改树。
    parent = {c: p for p in root.iter() for c in p}
    for el in list(root.iter()):
        tag = _local(el.tag)
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
                         "要动请用 director 补间）")
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
                if _EXT_URL.search(v):
                    el.set(k, _EXT_URL.sub("url(about:blank)", v))
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
