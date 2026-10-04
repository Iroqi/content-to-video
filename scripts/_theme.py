"""主题注册表。配色数据直接内联在本模块。包含：
  - "dark"：背景/文字主题配色（get_theme_colors；单一主题，无选择面）
  - "_ACCENT_PALETTE"：8 色 accent 色板，供 build_from_structured
    按内容段序号轮询取色（get_accent_palette）
  - "_DEFAULT_ACCENT"：默认段落强调色

"""
import copy
import re
# ── 主题注册表──
# 单一主题 dark。accent 相关是独立常量：混在一张 dict 里要靠 startswith("_")
# 区分，新加的下划线键容易被误当成配色。
_THEMES = {
    "dark": {
        "bg_gradient": "linear-gradient(135deg,#060709 0%,#0d0f13 45%,#080a0c 100%)",
        "grid_color": "rgba(0,220,150,0.055)",
        "text_color": "#eef2ee",
    },
}
DEFAULT_THEME = "dark"
_DEFAULT_ACCENT = "#2dd4bf"
_ACCENT_PALETTE = [
    "#ffd54f", "#4fc3f7", "#81c784", "#ffb74d",
    "#ef5350", "#ba68c8", "#f06292", "#4dd0e1",
]


def get_theme_colors(theme):
    """根据主题名返回主题配色字典。

    影响背景渐变、网格线与文字颜色；每段 accent 彩色不受影响。

    Args:
        theme: 主题名，当前只有 "dark"。

    Returns:
        dict: 包含 bg_gradient, grid_color, text_color 三个键。
    """
    if theme not in _THEMES:
        # 不做静默回退：未知主题名直接报错并列出可用主题。
        raise ValueError(
            f"[theme] 未知主题名: {theme!r}（可用: {', '.join(sorted(_THEMES))}）")
    return copy.deepcopy(_THEMES[theme])


def list_theme_names():
    """返回注册表里所有可用主题名（当前只有 "dark"）。"""
    return sorted(_THEMES.keys())


def get_accent_palette():
    """返回 accent 色板（8 色列表），供按内容段序号轮询分配颜色。

    唯一权威来源是本模块的 _ACCENT_PALETTE 常量；Python 侧
    （build_from_structured.py）必须从本函数读取，不要再落一份硬编码副本。
    """
    return list(_ACCENT_PALETTE)


def get_default_accent():
    """返回默认段落强调色（opening/closing 与 fallback 分组使用）。

    唯一权威来源是本模块的 _DEFAULT_ACCENT 常量
    （单一数据源：改动默认色只改这一处，不散落字面量）。
    """
    return _DEFAULT_ACCENT


# ── 颜色数学 ────
# 纯函数、无注册表依赖：用于运行时的明暗适配与颜色数学。


def _hex_rgb_bytes(hex_color):
    """'#rrggbb'/'#rgb' → (r,g,b) 整数 0–255；解析不了返回 None。

    3 位缩写（#fff）先展开成 6 位——is_safe_css_color 放行用户传入的
    "#rgb" 缩写色，不展开会解析失败返回 None，深浅主题判断
    静默失效（dark 被当浅色，tagline 走压暗分支，对比度掉到 ~2.1）。
    全模块的 hex 解析只有这一份：darken / hex_to_rgb01 / css_color_to_hex
    各自再写一遍 3 位展开与 int(h,16) 迟早漂移。
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or not all(c in "0123456789abcdef" for c in h.lower()):
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def darken(hex_color, factor=0.6):
    """把 accent 十六进制色压暗一档，用于浅色主题下的小字（如 tagline）。

    保持色相不变、只降低亮度，让文字在米白/浅色背景上达到可读对比度，
    同时不改变该段落 accent 色在大元素（竖条/glow/进度条）上的视觉效果。
    """
    rgb = _hex_rgb_bytes(hex_color)
    if rgb is None:
        return hex_color
    return "#" + "".join(f"{int(v * factor):02x}" for v in rgb)


def hex_to_rgb01(hex_color):
    """'#rrggbb'/'#rgb' → (r,g,b) 归一化到 0-1；解析不了返回 None。"""
    rgb = _hex_rgb_bytes(hex_color)
    if rgb is None:
        return None
    return tuple(v / 255 for v in rgb)


def is_safe_css_color(color):
    """颜色值是否是浏览器真正认得的 hex / CSS 颜色名（且可安全嵌入 HTML 属性）。

    accent 来自稿件（信源内容经模型写入），本项目把它当**不可信数据**：
    它会被拼进 `data-accent="..."` 与 `style="background:..."` 两处 HTML
    上下文（GSAP 补间只写 opacity/scale/width，颜色经 CSS 变量派生，不进 JS
    字面量）。只放行两种形态——`#rgb`/`#rrggbb`
    十六进制，或 CSS 标准颜色名（`red` / `tomato`）——从而排除引号、
    分号、括号、反斜杠等一切能闭合属性/声明/字符串的字符。成片 HTML 会被
    preview.js 打开、被 headless Chrome 渲染，所以
    `x"><script>...</script>` 这类值必须在契约层就挡下，不能指望下游转义。

    色名要求**查得到表**而不是"纯字母"：`hazyblue` 这种拼错的色名如果只按
    字母放行，会过了校验再在下游被静默换成默认色——用户在契约层就该听到
    拒绝，而不是渲染完才发现颜色不对却无从下手。需要 `rgb(...)`/`var(...)`
    等函数式写法时请直接给 6 位 hex（括号会打开注入面）。
    """
    return css_color_to_hex(color) is not None


_CSS3_NAMES = None

# 浏览器标准色名全集（name-hex 紧凑副本，首次使用时解析）。stdlib 没有
# 现成的色名表可借（没有 html.color；colorsys 只有换算函数），只能内嵌。
_CSS3_NAMES_SRC = """
aliceblue-f0fff8 antiquewhite-faebd7 aqua-00ffff aquamarine-7fffd4 azure-f0ffff
beige-f5f5dc bisque-ffe4c4 black-000000 blanchedalmond-ffebcd blue-0000ff
blueviolet-8a2be2 brown-a52a2a burlywood-deb887 cadetblue-5f9ea0
chartreuse-7fff00 chocolate-d2691e coral-ff7f50 cornflowerblue-6495ed
cornsilk-fff8dc crimson-dc143c cyan-00ffff darkblue-00008b darkcyan-008b8b
darkgoldenrod-b8860b darkgray-a9a9a9 darkgreen-006400 darkgrey-a9a9a9
darkkhaki-bdb76b darkmagenta-8b008b darkolivegreen-556b2f darkorange-ff8c00
darkorchid-9932cc darkred-8b0000 darksalmon-e9967a darkseagreen-8fbc8f
darkslateblue-483d8b darkslategray-2f4f4f darkslategrey-2f4f4f
darkturquoise-00ced1 darkviolet-9400d3 deeppink-ff1493 deepskyblue-00bfff
dimgray-696969 dimgrey-696969 dodgerblue-1e90ff firebrick-b22222
floralwhite-fffaf0 forestgreen-228b22 fuchsia-ff00ff gainsboro-dcdcdc
ghostwhite-f8f8ff gold-ffd700 goldenrod-daa520 gray-808080 green-008000
greenyellow-adff2f grey-808080 honeydew-f0fff0 hotpink-ff69b4
indianred-cd5c5c indigo-4b0082 ivory-fffff0 khaki-f0e68c lavender-e6e6fa
lavenderblush-fff0f5 lawngreen-7cfc00 lemonchiffon-fffacd lightblue-add8e6
lightcoral-f08080 lightcyan-e0ffff lightgoldenrodyellow-fafad2
lightgray-d3d3d3 lightgreen-90ee90 lightgrey-d3d3d3 lightpink-ffb6c1
lightsalmon-ffa07a lightseagreen-20b2aa lightskyblue-87cefa
lightslategray-778899 lightslategrey-778899 lightsteelblue-b0c4de
lightyellow-ffffe0 lime-00ff00 limegreen-32cd32 linen-faf0e6 magenta-ff00ff
maroon-800000 mediumaquamarine-66cdaa mediumblue-0000cd mediumorchid-ba55d3
mediumpurple-9370db mediumseagreen-3cb371 mediumslateblue-7b68ee
mediumspringgreen-00fa9a mediumturquoise-48d1cc mediumvioletred-c71585
midnightblue-191970 mintcream-f5fffa mistyrose-ffe4e1 moccasin-ffe4b5
navajowhite-ffdead navy-000080 oldlace-fdf5e0 olive-808000 olivedrab-6b8e23
orange-ffa500 orangered-ff4500 orchid-da70d6 palegoldenrod-eee8aa
palegreen-98fb98 paleturquoise-afeeee palevioletred-db7093 papayawhip-ffefd5
peachpuff-ffdab9 peru-cd853f pink-ffc0cb plum-dda0dd powderblue-b0e0e6
purple-800080 rebeccapurple-663399 red-ff0000 rosybrown-bc8f8f
royalblue-4169e1 saddlebrown-8b4513 salmon-fa8072 sandybrown-f4a460
seagreen-2e8b57 seashell-fff5ee sienna-a0522d silver-c0c0c0 skyblue-87ceeb
slateblue-6a5acd slategray-708090 slategrey-708090 snow-fffafa
springgreen-00ff7f steelblue-4682b4 tan-d2b48c teal-008080 thistle-d8bfd8
tomato-ff6347 turquoise-40e0d0 violet-ee82ee wheat-f5deb3 white-ffffff
whitesmoke-f5f5f5 yellow-ffff00 yellowgreen-9acd32
"""


def _css_name_table():
    """CSS 颜色名 → hex（无 #）映射表。

    既是 normalize_accent 的换算依据，也是 is_safe_css_color 的色名白名单：
    表里没有的色名在契约层就被拒。
    """
    global _CSS3_NAMES
    if _CSS3_NAMES is None:
        _CSS3_NAMES = dict(tok.split("-", 1)
                           for tok in _CSS3_NAMES_SRC.split())
    return _CSS3_NAMES


def css_color_to_hex(color):
    """任意安全颜色（#rgb/#rrggbb/CSS 色名）→ '#rrggbb'；解析不了返回 None。

    alpha 后缀（{ac}40 / {ac}15）只能拼在 6 位 hex 上：色名拼后缀会产出
    `red40` 这类非法值，整条声明被浏览器静默丢弃。所有要拼 alpha 的
    accent 必须先过本函数归一化成 6 位 hex。
    """
    if not isinstance(color, str):
        return None
    c = color.strip().lower()
    if not c:
        return None
    if c.startswith("#"):
        rgb = _hex_rgb_bytes(c)
        return None if rgb is None else "#{:02x}{:02x}{:02x}".format(*rgb)
    if c.isalpha():
        hex_digits = _css_name_table().get(c)
        return "#" + hex_digits if hex_digits else None
    return None


def normalize_accent(color, default):
    """不可信 accent → 保证可安全拼 alpha 的 '#rrggbb'；解析不了回落 default。"""
    return css_color_to_hex(color) or default


def relative_luminance(hex_color):
    """WCAG 相对亮度（用于判断主题深浅底）。"""
    rgb = hex_to_rgb01(hex_color)
    if rgb is None:
        return None
    lin = tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                for c in rgb)
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def mix(hex_color, other_hex, ratio):
    """按比例混两色（0=全 hex_color，1=全 other_hex）。"""
    a = hex_to_rgb01(hex_color)
    b = hex_to_rgb01(other_hex)
    if a is None or b is None:
        return hex_color
    mixed = (round(255 * (a[i] * (1 - ratio) + b[i] * ratio)) for i in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def contrast_ratio(color_a, color_b):
    """WCAG 对比度（1.0–21.0）；任一色解析不了返回 0.0（视为不达标）。"""
    la = relative_luminance(color_a)
    lb = relative_luminance(color_b)
    if la is None or lb is None:
        return 0.0
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def theme_bg_stops(theme):
    """主题背景渐变里的十六进制色标——文字真正落其上的候选底色集合。"""
    return re.findall(r"#[0-9a-fA-F]{6}", get_theme_colors(theme)["bg_gradient"])


def ensure_text_contrast(color, backgrounds, target=4.5):
    """accent 文字色的对比度保底：达标原样返回，否则朝黑/白逐步推移到达标。

    darken/mix 的固定系数只保证"典型 accent × 典型底色"的观感，色板里的
    中亮度色（黄、天蓝）在浅色主题下会跌破 hyperframes check 的门禁——
    本函数把"调到位"从经验常数变成可证明的下界。方向跟随底色深浅
    （浅底压向黑、深底提向白），推移从给定色起步。

    默认 4.5 取的是 check 门禁的**最严一档**：同一元素在不同画幅会被
    按不同字号阈值判 3.0/4.0/4.5（实测：kicker 横屏判 3:1、竖屏判
    4.5:1），按最严的保底才能让两个画幅、任意 accent 都不再触发。
    """
    if not backgrounds:
        return color

    def worst(c):
        return min(contrast_ratio(c, b) for b in backgrounds)

    if worst(color) >= target:
        return color
    # 最坏底色决定推移方向
    bg = min(backgrounds, key=lambda b: contrast_ratio(color, b))
    lum = relative_luminance(bg)
    toward = "#ffffff" if lum is not None and lum < 0.4 else "#000000"
    # 停在"首个达标档"的下一档：check 与这里的亮度换算存在末位舍入差
    # （实测 4.50 vs 4.48），留一档余量才不会贴着门禁线抖动。
    for k in range(1, 19):  # 最多推到 0.90，仍不达标就用最极端的一档
        if worst(mix(color, toward, k * 0.05)) >= target:
            return mix(color, toward, min(0.95, (k + 1) * 0.05))
    return mix(color, toward, 0.95)
