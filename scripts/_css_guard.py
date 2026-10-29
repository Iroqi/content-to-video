#!/usr/bin/env python3
"""CSS 与 GSAP 抢 `transform` 的生成期门禁。

**这条红线的来历**：成片里所有位移/缩放的补间都写成 GSAP 的 `x`/`y`/`scale`/`rotation`，
GSAP 把它们合成一条 inline `transform` 写进元素的 `style`。只要 CSS 对**同一个元素**
也声明了 `transform`，两者就抢同一个属性——谁赢取决于特异性与书写顺序，而 GSAP 读到
的"起点"也会跟着变。症状是**静默的**：那一页的标题/配图不进场，或者从半路弹出来，
没有任何报错，`hyperframes check` 也看不见（它只判布局与可读性）。

所以这条约束一直是文档里的红线（`references/look.md`「动画：实现要点」，
`_assets/composition.css` 里三处注释都在说"别声明 transform，居中走 auto 外边距"）。
写成门禁只需要一次静态解析：**CSS 里声明 transform 的选择器** ∩ **这一版 HTML
真会补间到的元素选择器**。

两类选择器分开求：

- CSS 侧：扫 `_assets/composition.css`（以及运行期注入的任何同源样式串）里的规则，
  取声明块中带 `transform:` 的那些，`@media` 里的也一并收（本样式表按 `[data-aspect]`
  前缀区分画幅，不用媒体查询；收进来只是为了让"以后有人加了一个 @media"也不漏）。
- 补间侧：从**生成好的 HTML** 里读 GSAP 选择器字符串（`tl.fromTo("#x",…)`
  / `tl.from("#x",…)` / `tl.set("#x",…)` / `tl.to("#x",…)`），标出来源是哪个函数。

白名单只有一类：伪元素（`::before` / `::after`）。`content:""` 的伪元素不是 DOM 节点，
GSAP 永远不会补间它，样式表里那几条（`.seg-card::after` 的高光边）是安全的。

**故意不开"豁免标记"**：没有一个"我知道我在干什么"的开关。因为这条约束的正确修法
永远是"换个元素承载 CSS 动效"，而不是"豁免一次"——开了口子，下一个 agent 就会用它。
"""
import re

# 一个 CSS 规则：`选择器列表 { 声明块 }`。`[^{}]` 不收花括号，所以 `@media{…{…}}`
# 的外层大括号不会被当成规则；内层规则由 finditer 扫到的第二段命中（选择器不以 @
# 开头，故 @media 自身被 `_AT_RULE` 排除）。
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_CSS_AT_RULE_RE = re.compile(r"^@")
_PLACEHOLDER_RE = re.compile(r"__CTV_[A-Z_]+__")
_TRANSFORM_DECL_RE = re.compile(r"(?:^|[;\s])transform\s*:", re.I)
_PSEUDO_RE = re.compile(r"::?(?:before|after)\b", re.I)

# GSAP 时间线调用里第一个参数是选择器的那几个函数。只认 `tl.` 前缀：产物里的补间
# 一律挂在 `tl` 上（`html_renderer` 生成的全部语句都是 `tl.xxx(`），把裸 `gsap.to(`
# 也算进来只会误报预览脚本里的东西。
_GSAP_CALL_RE = re.compile(r"\btl\.(fromTo|from|to|set|call)\(\s*([\"'])(.*?)\2")
_FN_ZH = {"fromTo": "入场补间", "from": "入场补间", "to": "补间",
          "set": "瞬时置值", "call": "回调"}


def strip_css_comments(css):
    return _CSS_COMMENT_RE.sub("", css)


def selector_parts(sel):
    """选择器列表 → 去空白的单个选择器；伪元素选择器原样保留（由调用方判）。

    `:root`、`[data-aspect="vertical"] .seg-title`、`.a,.b` 都收；空串丢弃。
    """
    return [s.strip() for s in sel.split(",") if s.strip()]


def transform_rules(css):
    """一条样式表里所有声明了 `transform` 的选择器 → {选择器: 声明里那一段}。"""
    out = {}
    for m in _CSS_RULE_RE.finditer(strip_css_comments(css)):
        head, body = m.group(1).strip(), m.group(2)
        if not head or _CSS_AT_RULE_RE.match(head):
            continue
        # 选择器列表取**最后一个换行之后**的那一段：样式表里的规则常写成
        # `/* 说明 */\n__CTV_ROOT_VARS__\n\n.seg-title{…}`，前一行是占位符而不是选择器；
        # 真正的选择器永远紧贴 `{`，所以整段按行取末尾即可（`:is(a,\n b)` 这类跨行
        # 括号写法本样式表不用，真用了也只会少认一个选择器、不会误报）。
        # head 非空由上方 `if not head` 保证，非空串 splitlines() 恒有末行
        head = head.splitlines()[-1].strip()
        if not head or _PLACEHOLDER_RE.search(head):
            continue
        if not _TRANSFORM_DECL_RE.search(body):
            continue
        # 只留 transform 那一条声明，报错时给人看的就是"这一行干了什么"。
        # 判据复用 _TRANSFORM_DECL_RE（match 只试位置 0，`^` 分支即等价于
        # `^\s*transform`）：另写一条正则等于给"是不是 transform 声明"留两份口径。
        decl = next((d.strip() for d in body.split(";")
                     if _TRANSFORM_DECL_RE.match(d.strip())), "")
        for sel in selector_parts(head):
            out.setdefault(sel, decl)
    return out


def tween_selectors(html):
    """产物里被补间到的选择器 → 首次出现它的调用名（给报错文案用）。"""
    out = {}
    for m in _GSAP_CALL_RE.finditer(html):
        fn, sel = m.group(1), m.group(3).strip()
        if not sel or fn == "call":
            continue
        out.setdefault(sel, fn)
    return out


def transform_tween_kind(sel):
    """补间选择器会不会往元素上写 `transform`：'always' / 'maybe' / None。

    - `always`：`.x` / `#x` / 带后代或属性选择器的组合——HTML 里任何元素的 x/y/scale
      都合成到 transform，只要这条补间打到了元素身上就写。
    - `maybe`：`*` 结尾的组选择器（`.row *`）等无法静态定论的，按 may-be 报。
    - None：SVG 内部选择器（`#img-x svg …`）不归 CSS 管（SVG 的 transform 是属性
      或 CSS transform 的另一套子集，且样式表够不着内联 SVG 副本）。
    """
    # 只留一条判据，且必须 `re.I`：原先那条 `sel.lower().split()` 是大小写不敏感
    # 的，而裸 `\bsvg\b` 不是——删掉前者会让 `SVG` 选择器从"不归 CSS 管"变成
    # "always"，那是悄悄改判据。带 re.I 之后它包含前者的全部命中。
    if re.search(r"\bsvg\b", sel, re.I):
        return None
    return "maybe" if sel.rstrip().endswith("*") else "always"


def _matches(tween_sel, css_sel):
    """一条 GSAP 选择器是否可能命中被 CSS 声明了 `transform` 的元素。

    只做保守的**字符串包含**判定：CSS 选择器出现在 GSAP 选择器里（`.seg-title`
    ⊂ `#seg-a .seg-title`）就算命中。会漏（`.a > .b` 与 `.b` 写法不同不会互相包含），
    但不会误报——门禁宁可漏报也不要把干净产物判红，误报一次就会有人来加豁免开关。
    """
    if tween_sel == css_sel:
        return True
    # 元素级兜底：CSS 或 GSAP 任一侧是"选择器链的最后一个复合选择器"时，按**尾词**
    # 比。`.verse-line` 与 `#seg-a .verse-line` 是两个写法、同一个元素；只比整串包含
    # 会漏掉后者（`.verse-line` 确实是 `… .verse-line` 的子串，但反过来
    # `[data-aspect="vertical"] .verse-line` 里的 `.verse-line` 就比不出来）。
    # 尾词比对不引入误报：尾词相同意味着最后一个复合选择器相同，而元素要么命中
    # 要么不命中——命中才可能冲突，没命中的那条本来就不写 transform。
    if _tail_compound(tween_sel) == _tail_compound(css_sel):
        return bool(_tail_compound(css_sel))
    if not css_sel.startswith(("#", ".", "[")):
        return False          # 元素名/伪类选择器（:root 等）比不出可靠子串
    return css_sel in tween_sel


def _tail_compound(sel):
    """选择器链的最后一个复合选择器（`.a > .b` → `.b`，`#x .y:hover` → `.y`）。"""
    toks = re.findall(r"[#.][A-Za-z0-9_-]+|\[[^\]]*\]", sel)
    return toks[-1] if toks else ""


def css_transform_leaks(css, html):
    """→ [(CSS 选择器, transform 声明, GSAP 选择器, 调用名)]；空列表 = 干净。"""
    rules = transform_rules(css)
    if not rules:
        return []
    tw = tween_selectors(html)
    leaks = []
    for css_sel, decl in rules.items():
        if _PSEUDO_RE.search(css_sel):
            continue          # 伪元素不是 DOM 节点，GSAP 够不着
        for tween_sel, fn in tw.items():
            if transform_tween_kind(tween_sel) is None:
                continue
            if _matches(tween_sel, css_sel):
                leaks.append((css_sel, decl, tween_sel, fn))
                break
    return leaks


def leak_message(leaks, where="scripts/_assets/composition.css"):
    """把泄漏点写成一条人话的 error（点名到选择器与修补办法）。"""
    lines = []
    for css_sel, decl, tween_sel, fn in leaks:
        lines.append(
            f"{where} 的 `{css_sel}` 声明了 `{decl}`，而 `{tween_sel}` 上挂着"
            f"{_FN_ZH.get(fn, '补间')}（tl.{fn}）：GSAP 会把 x/y/scale 合成一条 inline "
            f"transform 写进同一个元素，两条 transform 互斥——成片里这条补间会静默"
            f"失效或从错起点开始。居中等静态位移改用绝对定位 + auto 外边距")
    return "；".join(lines) if len(lines) == 1 else "；\n".join(lines)
