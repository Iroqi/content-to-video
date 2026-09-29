#!/usr/bin/env python3
"""Composition HTML compiler for Content-to-Video.

This module owns the presentation compiler: normalized composition inputs, HTML/CSS
and GSAP timeline generation. CLI orchestration, file validation and rendering
remain outside this module.
"""
import re
import sys
from pathlib import Path
from urllib.parse import quote

from _theme import (
    get_theme_colors, get_default_accent, get_accent_palette, darken,
    relative_luminance, mix, normalize_accent, theme_bg_stops,
    ensure_text_contrast,
)
from _template import load_template, get_canvas
from _contracts import (classify_media_path, is_content_sid, is_valid_sid,
                        seg_layout, unknown_media_keys, MEDIA_ENTRY_KEYS,
                        SID_RULE)


DEFAULT_ACCENT = get_default_accent()

# ── 模板资产：版式的静态骨架 ────────────────────────────────────────────
# CSS / JS / HTML 骨架是 templates/ 下的真实文件：结构与选择器写死，数值一律
# 走 var(--ctv-*)——由本模块从 _template 内联的版式数据派生后注入 :root。
# 缺模板文件即硬失败：静默产出无样式页面比立刻报错难排查得多。
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _load_asset(name):
    """读取 templates/ 下的模板资产（CSS / JS / HTML 骨架）。"""
    p = TEMPLATES_DIR / name
    if not p.is_file():
        raise FileNotFoundError(f"[renderer] 模板资产缺失: {p}")
    return p.read_text(encoding="utf-8")


_CTV_PH_RE = re.compile(r"__CTV_[A-Z0-9_]+?__")


def _fill(template, mapping, name):
    """单遍替换骨架占位符：只有骨架里的那些位置会被填，填进去的内容不再扫描。

    逐串 str.replace 是链式全文扫描，先注入的内容会被后一次替换再读一遍——
    稿件里一句原样写着 __CTV_GSAP__ 的文案（esc 不碰下划线）就能把真实代码
    塞进 JS 字符串字面量，整段内联脚本 SyntaxError、时间线和字幕
    一起死掉，而构建过程一行报错都没有。按本技能"信源只是数据"的边界，注入值
    永远不该成为下一次替换的输入。

    顺带把骨架与本次映射对账：骨架里冒出没人填的占位符、或映射里有骨架不认的
    名字（改名/拼错），立刻报错——不留一份缺内容的半成品 HTML 等人工预览去发现。
    """
    found = set(_CTV_PH_RE.findall(template))
    unfilled = sorted(found - set(mapping))
    unused = sorted(set(mapping) - found)
    if unfilled or unused:
        raise ValueError(
            f"[renderer] {name} 占位符与注入项对不上："
            + (f"骨架里没人填的 {unfilled}" if unfilled else "")
            + ("；" if unfilled and unused else "")
            + (f"映射里骨架没有的 {unused}" if unused else ""))
    return _CTV_PH_RE.sub(lambda m: mapping[m.group(0)], template)

def esc(text):
    """Escape HTML special characters (HTML attribute / element text).

    稿件文案进画面只有这一条转义路径：文案全部写成静态 DOM，运行时只切 class、
    赋值 color，从不把文本塞进 JS 字符串字面量（那种上下文里实体转义会渲染出
    字面量 &amp;，本模块没有这种调用点）。
    """
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def segment_duration(seg):
    """段落时长（秒）：从 sentences 推算——末句 start_time+duration − 首句
    start_time。与 generate_html 的 clip 计时同一口径。空 sentences 返回 0。
    """
    sents = seg.get("sentences") or []
    if not sents:
        return 0.0
    return ((sents[-1].get("start_time", 0) + sents[-1].get("duration", 0))
            - sents[0].get("start_time", 0))


def _fmt_mmss(seconds):
    """秒 → 'm:ss'（agenda 行右侧时长列）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def _agenda_rows(seg_id, clips, manifest, ag):
    """开屏/结尾纯文字 agenda 的行数据：返回 (正文行, 尾行) 两段。

    - opening：章节罗列——所有内容段的标题 + 时长（m:ss）。
    - closing：要点总结——每段 takeaway（缺省回退标题）、无时长，
      外加至多一条可选尾行（manifest.closing_cta，行动号召或下期预告二选一）。
    - 超上限静默截断（要点优先）：总行数 > maxRows 时先整条丢弃 cta 尾行，
      正文要点能放多少放多少；正文仍超 maxRows 才裁尾丢正文行。画面不放
      「…另有 N 条」提示行（上限本身就是给写稿的硬约束），两种丢弃都打
      [warn] 报给写稿人。
    """
    trim = int(ag["nameTrim"])
    content_clips = [c for c in clips if is_content_sid(c["seg"].get("id") or "")]

    def _cell(raw, sid):
        """agenda 单元格文本：按 nameTrim 硬截断，截掉字数要报出来。

        超出部分是被 Python 切掉的（CSS 的 text-overflow 只对没截断、纯靠宽度
        溢出的行生效），所以画面不会出现省略号——不 warn 就是"稿子里写的要点
        少了几字，且无人知道"。
        """
        text = str(raw or "").strip()
        if len(text) > trim:
            print(f"[warn] agenda 行（{sid}）文本 {len(text)} 字超出上限 {trim} 字，"
                  f"尾部 {len(text) - trim} 字已截断且画面没有省略号：{text[trim:]}\n"
                  "       写稿时把要点压进一句（见 references/writing.md）",
                  file=sys.stderr)
        return text[:trim]

    tail_rows = []
    if seg_id == "opening":
        rows = [(f"{n:02d}", _cell(c["seg"].get("title"), c["seg"].get("id")),
                 _fmt_mmss(c["duration"]))
                for n, c in enumerate(content_clips, 1)]
    else:
        rows = [(f"{n:02d}",
                 _cell(c["seg"].get("takeaway") or c["seg"].get("title"),
                       c["seg"].get("id")), "")
                for n, c in enumerate(content_clips, 1)]
        cta = str(manifest.get("closing_cta") or "").strip()
        if cta:
            tail_rows.append(("→", _cell(cta, "closing_cta"), ""))
    max_rows = int(ag["maxRows"])
    # 要点优先：cta 只在预算有余量时才占行——放不下就先丢 cta，
    # 正文行仍超上限才继续裁正文（均打 [warn]，画面静默）。
    if tail_rows and len(rows) + len(tail_rows) > max_rows:
        print(f"[warn] agenda（{seg_id}）正文 {len(rows)} 条 + cta 尾行超出上限 "
              f"{max_rows} 行——要点优先，cta 尾行不上画面"
              "（段数已占满预算，建议稿件里删掉 cta 或合并要点）", file=sys.stderr)
        tail_rows = []
    if len(rows) > max_rows:
        print(f"[warn] agenda（{seg_id}）正文 {len(rows)} 条超出上限 {max_rows} 行，"
              f"后 {len(rows) - max_rows} 条不会出现在画面上"
              "——建议压段数或合并要点", file=sys.stderr)
        rows = rows[:max_rows]
    return rows, tail_rows


def _agenda_row_html(rows):
    return "".join(
        f'<div class="agenda-row"><span class="ag-idx">{idx}</span>'
        f'<span class="ag-name">{esc(name)}</span>'
        + (f'<span class="ag-dur">{dur}</span>' if dur else "")
        + '</div>'
        for idx, name, dur in rows)


def _agenda_col_html(seg, clips, manifest, ag, dark_theme, ac, ac_attr,
                     title_size, bgs):
    """纯文字 agenda 卡的前半段：.agenda-col > 题头 + agenda 行（col 不闭合）。

    调用方拼上句子流（verse）后再闭合 .agenda-col——flex 列"题头在顶、
    agenda 垂直居中、句子流锚底"两画幅共用同一套 DOM，几何差异全在 CSS。
    kicker 取 opening/closing 的 tagline 字段（可选的一行小标题）。
    """
    sid = seg.get("id")
    kicker_html = ""
    if seg.get("tagline"):
        # 深浅底的起手色与内容段 tagline 同方向（深底提亮、浅底压暗），
        # 再统一过 ensure_text_contrast 的对比度保底。
        _kc = ensure_text_contrast(
            mix(ac, "#ffffff", 0.55) if dark_theme else darken(ac, 0.75), bgs)
        kicker_html = (f'<div class="agenda-kicker" style="color:{_kc}">'
                       f'{esc(seg["tagline"])}</div>')
    rows, tail_rows = _agenda_rows(sid, clips, manifest, ag)
    agenda_html = (f'<div class="agenda">{_agenda_row_html(rows)}</div>'
                   if rows else "")
    tail_html = (f'<div class="agenda-tail">{_agenda_row_html(tail_rows)}</div>'
                 if tail_rows else "")
    return (f'    <div class="agenda-col">\n'
            f'      <div class="agenda-head">{kicker_html}'
            f'<div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 {ag["titleGlow"]}px {ac_attr}40">'
            f'{esc(seg.get("title") or "")}</div></div>\n'
            f'      {agenda_html}{tail_html}\n')


def _normalize_images(images):
    """把媒体条目归一成渲染器的单一形状 {src, media_type, opts}。

    幂等：已归一化（含 media_type 键）的结构原样返回——再来一遍会把
    条目里的字段当成未知键清掉。
    """
    normalized = {}
    for sid, media in (images or {}).items():
        # sid 会拼进 HTML 属性、JS 对象键和 GSAP 选择器。CLI 路径由
        # validate_images_json 把守；库调用方直接传 dict 时这里是唯一防线，
        # 与 CLI 同一口径（_SID_RE），不让"单点依赖 CLI 校验"成为转义豁免。
        if not is_valid_sid(sid):
            raise ValueError(
                f"images.json 的段落 key 必须是合法段 id"
                f"（{SID_RULE}；实际: {sid!r}）")
        if isinstance(media, dict) and "media_type" in media:
            normalized[sid] = media
            continue
        if isinstance(media, dict):
            # 清单外的键在下面两条分支里都会跟着进 opts 而无人读。静默丢会让
            # "images.json 里明明写了 alt，画面上什么都没有"变成无解的困惑，
            # 所以点名叫出它们——清单定义在 _contracts。
            unknown = unknown_media_keys(media)
            if unknown:
                print(f"[warn] images.json 的 '{sid}' 含渲染端不读的字段："
                      f"{', '.join(unknown)}——它们不会出现在画面上。条目只认："
                      f"{', '.join(sorted(MEDIA_ENTRY_KEYS))}；"
                      "写错了就删掉。"
                      "想改构图请改稿件或 SVG 本体，images.json 只管映射与播放属性",
                      file=sys.stderr)
            media_path = media.get("src")
            if not isinstance(media_path, str) or not media_path.strip():
                # 契约层已要求 src；作为库直接调用时给出行号级病因，
                # 而不是 KeyError 裸栈或渲染出一个空白槽位。
                raise ValueError(f"images.json 条目 {sid!r} 缺少非空字符串 src")
            media_type = classify_media_path(media_path, media.get("type", "auto"))
            media_opts = {k: v for k, v in media.items() if k not in ("src", "type")}
        else:
            # 与 src 缺失同一条口径：裸字符串/None 一律说清是哪一键，
            # 不静默丢弃（静默丢会让"图没了"看起来像渲染 bug）。
            # 契约层 validate_images_json 已在命令行路径拦下这种输入，
            # 这里只兜住直接把 dict 传进来的库调用方。
            raise ValueError(f"images.json 条目 {sid!r} 必须是媒体对象"
                             f'（如 {{"src": "images/{sid}.png"}}），'
                             f"收到 {type(media).__name__}")
        normalized[sid] = {
            "src": media_path,
            "media_type": media_type,
            "opts": media_opts,
        }
    return normalized


def _build_subtitle_cues(sentences):
    """Build the JS cue payload (t/d/si) from sentence timing.

    cue 里只有数字：文案由 verse 静态 DOM 承载（见 esc），运行时按 si 切高亮、
    按 t/d 判定当前句，不需要序列化任何文本。

    时长一律由"下一句的起点"倒推：t 与 d 各自独立四舍五入到 0.01s 时，相邻 cue
    之间会留下至多 10ms 的空洞，24fps 下恰好吞掉一帧——那一帧落在空洞里就沿用
    上一句的高亮，切换看起来慢了一帧。末句没有下一句，按自身实测时长收尾。
    """
    n = len(sentences)
    cues = []
    for i, sent in enumerate(sentences):
        t = round(sent["start_time"], 2)
        end = (round(sentences[i + 1]["start_time"], 2) if i + 1 < n
               else round(sent["start_time"] + sent["duration"], 2))
        si = sent.get("index", -1)
        cues.append(f'{{t:{t:.2f},d:{max(0.01, end - t):.2f},si:{si}}}')
    return ",\n    ".join(cues)


_DEFAULT_GSAP_SRC = "vendor/gsap.min.js"


def generate_html(manifest, audio_src, images=None,
                  width=None, height=None,
                  gsap_src=_DEFAULT_GSAP_SRC,
                  aspect="portrait", theme="dark", fps=24):
    """Generate complete Hyperframes HTML composition string.

    Args:
        images: {段落 id: 媒体对象}，形态与 images.json 一致（裸字符串路径由
            `_contracts.validate_images_json` 在上游拒收）；None/缺键 = 该段纯文字。
        aspect: portrait/vertical（3:4）或 landscape（16:9）；data-aspect 与默认
            画布尺寸都按它取，归一化在函数体内完成。
        theme: 只改背景渐变/网格/正文，不改每段 accent 彩色；
            可选值见 _theme 的主题注册表。
        gsap_src: 默认指向 composition 项目内的 vendor/，不访问 CDN。
        fps: 写进 data-fps 的渲染提示（渲染命令 --fps 可覆盖）；24 比 30 少抓
            20% 帧、出片更快。

        字幕/内容呈现模式不作为参数暴露；固定为 verse（歌词式句子流）。
    """
    # 先归一化画幅，再按对应画幅取默认画布。否则库调用方省略
    # width/height 时，即使传入 landscape 也会拿到竖屏尺寸。
    if aspect == "portrait":
        aspect = "vertical"
    elif aspect not in ("vertical", "landscape"):
        raise ValueError(f"[renderer] 未知画幅 {aspect!r}（可用: portrait/vertical、landscape）")
    tpl = load_template()
    if width is None or height is None:
        width, height = get_canvas(aspect)

    # ── 配图/视频归一化（只做一次）─────────────────────────────────
    # 后续渲染阶段都复用这份结果，同一输入不经过第二次归一化。
    images = _normalize_images(images or {})
    total_dur = manifest["total_duration"]
    sentences = manifest["sentences"]
    # <script src> 属性上下文转义：CLI 传的本地路径可能含 & 或引号，裸插
    # 会破坏 head 结构（媒体路径都走了 quote/转义；这里不能 URL 编码——
    # CDN 地址的 :/? 会被 quote 破坏）
    # None 安全：调用方可能显式传 None，回落默认路径。
    gsap_src_attr = (gsap_src or _DEFAULT_GSAP_SRC).replace("&", "&amp;").replace('"', "&quot;")

    # 主题配色（背景/网格/文字），accent 色不受主题影响
    theme_colors = get_theme_colors(theme)
    # 按主题主文字色亮度判深浅底（_theme._THEMES 是唯一权威，
    # 新增主题无需改这里的枚举）——tagline 的"同色相只调明度"在深色
    # 底下方向要反过来（提亮而不是压暗）。
    _dark_theme = relative_luminance(theme_colors["text_color"]) > 0.5
    # 背景渐变的十六进制色标集合：accent 派生文字色的对比度保底按其中最坏
    # 一档判定（theme_bg_stops 只解析自家 _THEMES 的渐变串）。
    _bgs = theme_bg_stops(theme)

    # 从模板加载布局/动画/字体参数
    tpl_layout = tpl["layout"][aspect]
    tpl_anim = tpl["animation"]
    tpl_typo = tpl["typography"]

    # 从模板提取 CSS 变量值
    _tl = tpl_layout["title"]

    _sl = tpl_layout["subtitle"]
    # 字幕字号是唯一被消费的 subtitle 参数（两画幅各读各的 subtitle 块）
    css_sub_font = f'{_sl["fontSize"]}px'

    # ── 分画幅几何派生 ──────────────────────────────────────────────
    # 两画幅各挂一块 verse / 图片 / agenda 几何，数值互不共用；只有两画幅
    # 都消费的键才两边都写。竖屏独有的定位键（segCard.padding、verse.bottom、
    # image.marginSide/bottomGapToVerse）只存在于 vertical 块——横屏是弹性
    # 列，没有这些概念，放一个 0 值占位只会让人以为改得动。
    _vv = tpl_layout["verse"]
    _il = tpl_layout["image"]
    _ag = tpl_layout["agenda"]
    _v_verse_clip = _vv["clipPad"]
    if aspect == "vertical":
        # 竖屏（3:4）：只有 verse 一种字幕形态，参数全部读模板 vertical 块。
        _v_pad = tpl_layout["segCard"]["padding"]
        # 卡片左内边距（CSS padding 简写：3 值=上/左右/下，2 值=上下/左右，1 值=四边）。
        # 卡片内容盒由它内缩，标题与画布水平方向对齐。
        _v_pad_parts = _v_pad.split()
        if not _v_pad_parts:
            raise ValueError("[template] layout.vertical.segCard.padding 不能为空")
        # 左右内边距永远是简写的第二个值（3 值=上/左右/下、2 值=上下/左右），
        # 只有一个值时四边共用，就是它自己。取不到数就是模板写错了长度单位。
        _v_pad_raw = _v_pad_parts[1] if len(_v_pad_parts) >= 2 else _v_pad_parts[0]
        try:
            _v_pad_left = float(_v_pad_raw.replace("px", ""))
        except ValueError as e:
            raise ValueError("[template] layout.vertical.segCard.padding 必须是合法 CSS 长度") from e
        _v_verse_h = _vv["windowHeight"]
        _v_verse_bottom = _vv["bottom"]
        # 竖屏画布圆角
        _v_img_radius = _il["borderRadius"]
        # 画布左右外扩边距 marginSide：相对"卡片内容盒"各外扩多少（0 = 与内容盒
        # 同宽），不是"距屏幕边距"——segCard.padding 的左右值才是槽到屏幕边的
        # 距离。想让槽距屏幕正好是 padding 那个值，保持 marginSide=0 即可。
        _v_img_ms = _il["marginSide"]
        _v_img_bottom_gap = _il["bottomGapToVerse"]
        # 画布与 verse 都脱离标题文档流，分别固定在下半区；标题换行只影响自身，
        # 不再推动画布或歌词。画布底边 = verse 顶部 - 固定间距。
        _v_img_top = (height - _v_verse_bottom - _v_verse_h
                      - _v_img_bottom_gap - int(_il["height"]))
        if _v_img_top <= 0:
            raise ValueError("[template] 画布位置不足：image.height / verse.windowHeight / bottom 配置超出画布")
        # 画布宽度一致性护栏：CSS 侧画布宽 = image.width + marginSide*2，
        # image.width 必须等于"屏宽 − segCard 左右内边距"，否则模板与布局漂移。
        if int(_il["width"]) != int(width - 2 * _v_pad_left):
            raise ValueError(
                f"[template] image.width ({_il['width']}) 应等于屏宽 − segCard 左右内边距 "
                f"({int(width)} − 2×{_v_pad_left:g} = {int(width - 2 * _v_pad_left)})")
    else:
        # 横屏（16:9）：96 四周留白 / 48 栏距 / 左栏 613 / 媒体卡 1067×800。
        _lm = tpl_layout["margin"]
        _lgap = tpl_layout["columnGap"]
        _ltext = tpl_layout["textCol"]
        _l_img_h = int(_il["height"])
        # 4:3 允许 ±1px 取整：高 800 的严格 4:3 宽是 1066.67，整数像素只能
        # 取 1067。容差按 w×3 与 h×4 的差衡量（±1px 宽 = 差 ≤ 3），差更大
        # 就是真配错了比例（如 16:9）。
        if abs(int(_il["width"]) * 3 - _l_img_h * 4) > 3:
            raise ValueError(
                f"[template] landscape image 必须为 4:3（宽 = 高×4/3 取整，±1px），"
                f"实际 {_il['width']}×{_il['height']}")
        if (_l_img_h <= 0 or _l_img_h > height - 2 * _lm
                or int(_ltext["width"]) + _lgap + int(_il["width"]) + 2 * _lm > width):
            raise ValueError(
                f"[template] landscape 版式越界：媒体卡 {_l_img_h}px 高必须 ≤ "
                f"画布高 − 2×margin({height} − 2×{_lm} = {height - 2 * _lm})，"
                f"且 textCol.width({_ltext['width']}) + columnGap({_lgap}) "
                f"+ image.width({_il['width']}) + 2×margin({_lm}) 必须 ≤ 画布宽({width})")

    _tgl = tpl_layout["tagline"]
    css_tagline_font = f'{_tgl["fontSize"]}px'
    # tagline 显式行高：竖屏标题组几何推导需要确定行高（依赖浏览器 normal
    # 行高约 1.14-1.2 随字体浮动，会让几何不可推导）。
    css_tagline_lh = _tgl["lineHeight"]

    # 其余 CSS 变量（网格/进度条/字体排印）也从模板读取
    _grid = tpl_layout["grid"]
    css_grid_size = _grid["size"]
    _prog = tpl_layout["progressBar"]
    css_prog_height = _prog["height"]
    # 字体排印
    css_title_weight = tpl_typo["titleWeight"]
    css_title_lh = tpl_typo["titleLineHeight"]
    css_title_tracking = tpl_typo["titleTracking"]
    css_tagline_weight = tpl_typo["taglineWeight"]
    css_font_family = tpl_typo["fontFamily"]
    css_mono_family = tpl_typo["monoStack"]
    css_tagline_mt = _tgl["marginTop"]

    # ── :root 变量表 ──────────────────────────────────────────────────────
    # templates/*.css 里不出现硬编码尺寸，一律走 var(--ctv-*)；本表从 _template
    # 派生。命名空间：无后缀 = 两画幅同名派生（值各取各块），--ctv-v-* 仅竖屏、
    # --ctv-l-* 仅横屏，--ctv-ag-* agenda 两画幅同名、值取各自 layout 的 agenda。
    root_shared = f"""  --ctv-w:{width}px;--ctv-h:{height}px;
  --ctv-bg-gradient:{theme_colors["bg_gradient"]};
  --ctv-font-family:{css_font_family};
  --ctv-font-mono:{css_mono_family};
  --ctv-grid-size:{css_grid_size}px;--ctv-grid-color:{theme_colors["grid_color"]};
  --ctv-text-color:{theme_colors["text_color"]};
  --ctv-img-glow:{_il["glow"]}px;
  --ctv-title-weight:{css_title_weight};
  --ctv-title-lh:{css_title_lh};--ctv-title-tracking:{css_title_tracking};
  --ctv-tagline-font:{css_tagline_font};--ctv-tagline-mt:{css_tagline_mt}px;
  --ctv-tagline-lh:{css_tagline_lh};--ctv-tagline-weight:{css_tagline_weight};
  --ctv-prog-height:{css_prog_height}px;
  --ctv-sub-font:{css_sub_font};
  --ctv-verse-h:{_vv["windowHeight"]}px;--ctv-verse-clip:{_vv["clipPad"]}px;
  --ctv-verse-line:{_vv["linePad"]}px;--ctv-verse-lh:{_vv["lineHeight"]};
  --ctv-verse-rule:{_vv["activeRule"]}em;
  --ctv-ag-x:{_ag["insetX"]}px;--ctv-ag-t:{_ag["insetTop"]}px;--ctv-ag-b:{_ag["insetBottom"]}px;
  --ctv-ag-title-lh:{_ag["titleLineHeight"]};--ctv-ag-title-mt:{_ag["titleMarginTop"]}px;
  --ctv-ag-kicker:{_ag["kickerSize"]}px;--ctv-ag-idx:{_ag["idxSize"]}px;
  --ctv-ag-idx-min:{_ag["idxMinWidth"]}px;--ctv-ag-name:{_ag["nameSize"]}px;
  --ctv-ag-dur:{_ag["durSize"]}px;--ctv-ag-gap:{_ag["rowGap"]}px;
  --ctv-ag-row-pad:{_ag["rowPad"]}px;--ctv-ag-verse-w:{_ag["verseMaxWidth"]}px;--ctv-ag-list-mt:{_ag["listMarginTop"]}px;"""
    if aspect == "vertical":
        # 标题区定高 = 该盒必须装下的东西：maxLines 行标题 + tagline 一行。
        # 这是个 overflow:hidden 的绝对定位盒，画布与句子流都不为它让位（竖屏三区
        # 各自固定定位）：算小了会把 tagline 静默切掉一截，算大了会往画布上压，
        # 两头都不报错——所以放不进时在这里直接 raise。
        _v_title_area = (_tl["maxLines"] * _tl["fontSize"] * css_title_lh
                         + _tgl["marginTop"]
                         + _tgl["fontSize"] * _tgl["lineHeight"])
        _v_title_bottom = _tl["top"] + _v_title_area
        if _v_title_bottom > _v_img_top:
            raise ValueError(
                f"[template] 竖屏标题区放不进：title.top({_tl['top']}) + 标题 "
                f"{_tl['maxLines']} 行×{_tl['fontSize']}px×行高 {css_title_lh} "
                f"+ tagline 一行 = {_v_title_bottom:.0f}px，已越过画布顶边 "
                f"{_v_img_top}px——请减少 title.maxLines、调小 title.fontSize/"
                "tagline.fontSize，或抬高画布（image.bottomGapToVerse/verse 配置）")
        root_aspect = f"""  --ctv-v-pad-left:{_v_pad_left:g}px;
  --ctv-v-title-top:{_tl["top"]}px;
  --ctv-v-title-area:{_v_title_area:.2f}px;--ctv-v-title-lines:{_tl["maxLines"]};
  --ctv-v-tagline-mt:{_tgl["marginTop"]}px;
  --ctv-v-tagline-indent:{_tgl["indent"]}px;
  --ctv-v-img-w:{int(_il["width"])}px;--ctv-v-img-ms:{_v_img_ms}px;
  --ctv-v-img-top:{_v_img_top}px;--ctv-v-img-height:{int(_il["height"])}px;--ctv-v-img-radius:{_v_img_radius}px;
  --ctv-v-verse-bottom:{_v_verse_bottom}px;
  --ctv-v-tagline-tick:{_tgl["tickWidth"]}px;"""
    else:
        # 刻意不注入 --ctv-l-gap：左右两栏都是绝对定位，栏间距由 margin/textW/imgW
        # 的算术决定，注入一个没人读的空格令牌只会让人以为改它能挪版式
        # （columnGap 仍然参与上面的越界护栏，那是它的唯一消费者）。
        root_aspect = f"""  --ctv-l-margin:{_lm}px;
  --ctv-l-text-w:{int(_ltext["width"])}px;--ctv-l-text-gap:{int(_ltext["gap"])}px;
  --ctv-l-title-lh:{_tl["lineHeight"]};
  --ctv-l-tag-indent:{_tgl["indent"]}px;--ctv-l-tag-tick:{_tgl["tickWidth"]}px;
  --ctv-l-img-w:{int(_il["width"])}px;--ctv-l-img-h:{_l_img_h}px;
  --ctv-l-img-radius:{_il["borderRadius"]}px;"""
    root_vars = ":root{\n" + root_shared + "\n" + root_aspect + "\n}"

    # 段落分组只有一份：manifest["segments"]（_contracts 已保证非空、每段自带
    # 非空 sentences）。gen_hyperframes 的孤儿键判定与缺图统计读的是同一个字段，
    # 在这里另推一套 id 就会和画面实际用的段 id 漂移。
    segments = manifest["segments"]

    # Calculate clip timing
    clips = []
    for seg in segments:
        start = seg["sentences"][0]["start_time"]
        clips.append({
            "seg": seg,
            "start": round(start, 2),
            "duration": round(segment_duration(seg), 2),
        })

    # ── 段落卡片 HTML + GSAP 时间线 ──────────────────────
    # closing_cta 只有结尾 agenda 卡这一个消费者：稿件没有 closing 段时这条尾行
    # 无处可画。不 warn 就成了"稿子里写了行动号召，画面上什么都没有"，与
    # writing.md 承诺的"两种丢弃都只向 stderr 打 [warn]"口径矛盾。
    if (str(manifest.get("closing_cta") or "").strip()
            and not any(c["seg"].get("id") == "closing" for c in clips)):
        print("[warn] manifest 有 closing_cta，但本次没有 closing 段可承载它——"
              "行动号召只画在结尾 agenda 卡上，这条尾行不会出现在画面里"
              "（要保留 cta 就在稿件里补一段 closing，或删掉 closing_cta）",
              file=sys.stderr)

    seg_cards = []
    gsap_lines = []

    for i, clip in enumerate(clips):
        seg = clip["seg"]
        # 用 .get + 默认 id，避免缺 id 字段时 KeyError 让整个渲染崩溃。
        # 上游 pipeline.py 已保证有 id，但 gen_hyperframes 也要能独立处理
        # 用户手写/外部工具产出的 manifest，做防御性兜底。
        sid = seg.get("id") or f"seg{i+1}"
        # sid 会拼进 id="..." 属性与 GSAP "#..." 选择器。CLI 路径由
        # validate_timing_manifest 的 _validate_sid 把守；库调用方直接传
        # manifest 时这里与 _normalize_images 同口径补一道，不让坏 sid
        # 变成"动画静默丢失/内联脚本 SyntaxError"。
        if not is_valid_sid(sid):
            raise ValueError(
                f"timing_manifest 的段落 id 必须是合法段 id"
                f"（{SID_RULE}；实际: {sid!r}）")
        s = clip["start"]
        d = clip["duration"]
        # normalize_accent：accent 统一归一化成 6 位 hex——alpha 后缀
        # （{ac}40/{ac}15）只有拼在 #rrggbb 后才合法，3 位 hex 或 CSS 色名拼出的
        # 非法值会被浏览器整条声明静默丢弃。accent 来自稿件、属不可信数据，会拼进
        # data-accent 与 style 两处 HTML 上下文——解析不了的一律回落默认色，
        # 绝不让引号/分号/括号进入 HTML。
        ac = normalize_accent(seg.get("accent", DEFAULT_ACCENT), DEFAULT_ACCENT)
        # HTML 属性上下文用 esc 后的副本（纵深防御：即使将来白名单放宽，
        # 属性闭合仍不可能）。GSAP 补间只写 opacity/scale/width，颜色一律经
        # CSS 变量派生，所以 accent 没有"进 JS 字符串字面量"的那条路。
        ac_attr = esc(ac)
        # 文本安全 accent：cream 浅底上原色 accent 做正文色对比度不足、字面发糊，
        # 与 tagline 同法压暗（同色相只降明度）；dark 底从原色起步。两条路径最后
        # 都过 ensure_text_contrast 兜到 check 门禁的最严一档。只喂给"写在底上的
        # 字"（活动句着色 / .ag-idx），装饰仍走原色 --seg-accent。
        ac_text = ensure_text_contrast(ac if _dark_theme else darken(ac), _bgs)
        ac_text_attr = esc(ac_text)
        # 版式一律由 _contracts.seg_layout 分派（layout 字段优先，结构性页按 id 兜
        # 档），不再各处写 sid 字面量。agenda = 纯文字投影卡：不配图，用章节罗列/
        # 要点总结填充。gen_hyperframes 已把 agenda 版式的 opening/closing 键弹出，
        # 库调用方仍带映射时这里也强制忽略。
        _layout = seg_layout(seg)
        is_agenda = _layout == "agenda"
        has_image = (sid in images) and not is_agenda
        # 整页画布（layout: "canvas"）：配图就是这一页——槽位拉满全屏，HTML 的
        # 标题层与句子流层都不渲染，标题/文字由画布自己画。取值已由
        # _contracts._validate_layout 把守；这里只认 canvas 且必须有配图。
        is_canvas = _layout == "canvas"
        if is_canvas and not has_image:
            # 不拦就会出一帧只有进度条的空页：画布没图，标题和字幕又都不画。
            raise ValueError(
                f"段落 '{sid}' 声明了 layout=\"canvas\"（整页画布），但没有配图"
                "——画布版式不渲染标题层与句子流层，没有配图就什么都不剩。"
                "请在 images.json 给该段配一张按当前画幅出的图，或去掉 layout 字段"
                "回到槽位版式。")

        # 动画参数快捷引用
        a_ = tpl_anim

        # 标题字号（内联，唯一不被 CSS 覆盖的标题数值），阈值/字号一律取模板：
        # - agenda 页带长度守卫：agenda 头豁免了行数钳制（见 composition.css），
        #   长标题会无限折行挤爆定高列，故按 CJK 字宽 ≈ 字号 估算，压到目标行数
        #   内放得下为止；横屏的行预算被 maxRows 吃满，标题只锁 1 行，竖屏允许
        #   压进 2 行；
        # - 横屏内容段超阈值按 guardSize 降档，配合 text-wrap:balance 折行，避免
        #   长标题把左栏撑出孤字；竖屏内容段一律取 fontSize。
        if is_agenda:
            _title_font_px = _ag["titleSize"]
            _tlen = len(str(seg.get("title") or "").strip())
            _ag_avail = width - 2 * _ag["insetX"]
            _ag_lines = 1 if aspect == "landscape" else 2
            # 折行按整行离散打包（每行放 floor(宽/字号) 个 CJK 字），
            # 面积估算会在边界处差一个字挤成下一行；降字号到
            # 每行至少 ceil(字数/目标行数) 个字为止。
            _cap = _ag_avail // _title_font_px
            if _tlen > _ag_lines * _cap:
                _fit = _ag_avail // -(-_tlen // _ag_lines)
                print(f"[warn] agenda 标题长 {_tlen} 字（{sid}，单行容量 "
                      f"{_cap} 字），降字号压进 {_ag_lines} 行：{_title_font_px}→{_fit}px"
                      "——建议写稿时压到短句", file=sys.stderr)
                _title_font_px = _fit
        elif aspect == "landscape":
            _title_font_px = _tl["fontSize"]
            _tlen = len(seg.get("title") or "")
            if _tlen > _tl["guardChars2"]:
                _title_font_px = _tl["guardSize2"]
            elif _tlen > _tl["guardChars1"]:
                _title_font_px = _tl["guardSize1"]
            if _tlen > _tl["guardChars1"]:
                print(f"[warn] 横屏标题长 {_tlen} 字（>{_tl['guardChars1']}），"
                      f"字号降为 {_title_font_px}px：{seg.get('title')!r}"
                      "——建议写稿时压到短句", file=sys.stderr)
        else:
            _title_font_px = _tl["fontSize"]
        title_size = f"{_title_font_px}px"

        # Tagline
        tagline_html = ""
        if seg.get("tagline"):
            # 深色主题向白提亮（无差别 _darken 在 dark 下对比度只有 ~3.3，不达
            # WCAG AA）；浅色主题压暗一档后仍由 ensure_text_contrast 兜到门禁最
            # 严档。缩进与对齐归 CSS（--ctv-v-tagline-indent）。
            _tag_color = ensure_text_contrast(
                mix(ac, "#ffffff", 0.62) if _dark_theme else darken(ac), _bgs)
            tagline_html = (
                f'<div class="tagline" id="tag-{sid}" '
                f'style="color:{_tag_color}">{esc(seg["tagline"])}</div>'
            )

        # 标题与句子流都走 CSS flex 文档流，宽度由 CSS 约束，Python 侧
        # 不推导盒几何。
        # Image / video container (right side, vertically centered).
        image_html = ""
        if has_image:
            media_info = images[sid]
            media_path = media_info["src"]
            media_type = media_info["media_type"]
            media_opts = media_info["opts"]
            if media_type == "video":
                # 视频配图：<video> 自动循环静音播放
                loop = "loop" if media_opts.get("loop", True) else ""
                muted = "muted" if media_opts.get("muted", True) else ""
                playsinline = "playsinline" if media_opts.get("playsinline", True) else ""
                # autoplay 同样读 images.json 选项（与其余 video 选项一致）
                autoplay = "autoplay" if media_opts.get("autoplay", True) else ""
                poster = media_opts.get("poster", "")
                poster_attr = f'poster="{quote(poster)}"' if poster else ""
                image_html = (
                    f'\n    <div class="seg-image" id="img-{sid}">\n'
                    f'      <video id="vid-{sid}" src="{quote(media_path)}" '
                    f'data-start="{s}" data-duration="{d}" '
                    f'{loop} {muted} {autoplay} {playsinline} '
                    f'{poster_attr}>\n'
                    f'      </video>\n'
                    f'    </div>'
                )
            else:
                # 静态图 / 动图走 <img>。SVG 按 C3 规范不铺满幅底，外面再套描边和
                # 发光就等于给一片空白画框，挂 bare-media 让 CSS 撤掉这两层装饰。
                _bare = " bare-media" if media_path.lower().endswith(".svg") else ""
                image_html = (
                    f'\n    <div class="seg-image{_bare}" id="img-{sid}">\n'
                    f'      <img src="{quote(media_path)}" alt="">\n'
                    f'    </div>'
                )

        # 字幕 DOM 只有 verse（歌词式句子流）一种形态、两画幅共用：该段全部句子
        # 按序渲染成静态行（完整句子，CSS 自动换行），运行时由 cue 的 si 高亮当前
        # 句、已播句淡出、窗口随播报滚动。DOM 在 seg-card 尾部，竖屏绝对定位钉底、
        # 横屏在左文字栏文档流里垂直居中（定位细节见 composition.css 的 verse 块）。
        _vlines = [
            # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
            # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
            # 永久失灵或错行——宁可都不高亮，也不错误高亮
            f'<div class="verse-line" data-i="{_s2.get("index", -1)}">'
            f'{esc(_s2["text"])}</div>'
            for _s2 in seg["sentences"]
        ]
        verse_html = (
            # 三个 layout 豁免属性源于同一误报机制：滚出窗口的行视觉上被
            # overflow:hidden 裁掉，但静态 DOM rect 仍在原位——上越标题区
            # （allow-overlap）、下碰底部元素如进度条（allow-occlusion）、
            # 整体越出卡片（allow-overflow）。活动行锚定在窗口内 clipPad
            # 处，真实重叠不可能发生。
            f'\n    <div class="verse" id="verse-{sid}" '
            f'data-layout-allow-overflow data-layout-allow-overlap '
            f'data-layout-allow-occlusion>'
            f'<div class="verse-clip" data-accent="{ac_text_attr}">'
            f'{"".join(_vlines)}</div></div>'
        )
        _cls_extra = (" agenda-card" if is_agenda
                      else " seg-canvas" if is_canvas else "")
        card_open = (
            f'  <div id="{sid}" class="clip seg-card{_cls_extra}" '
            f'data-start="{s:.2f}" data-duration="{d:.2f}" data-accent="{ac_attr}" '
            f'data-track-index="1" style="opacity:0;--seg-accent:{ac_attr};--seg-accent-text:{ac_text_attr}">\n'
        )
        progress_html = (
            f'    <div class="seg-progress" id="prog-{sid}" '
            f'style="background:{ac_attr};width:0"></div>\n'
        )
        title_wrap = (
            f'    <div class="seg-title-wrap">\n'
            f'      <div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 {_tl["glow"]}px {ac_attr}40">'
            f'{esc(seg.get("title") or "")}</div>\n'
            f'      {tagline_html}\n'
            f'    </div>'
        )
        if is_agenda:
            # agenda 卡：head+列表在 .agenda-col 内，verse 收在列尾（锚底），
            # 进度条留在卡底部（col 之外，贴屏底）。
            seg_cards.append(
                card_open
                + _agenda_col_html(seg, clips, manifest, _ag, _dark_theme,
                                   ac, ac_attr, title_size, _bgs)
                + f'    {verse_html}\n    </div>\n'
                + progress_html
                + '  </div>'
            )
        elif is_canvas:
            # 整页画布：只有画布 + 进度条。标题层/句子流层连 DOM 都不生成
            # （不是 display:none）：留着的空盒子会给 Layout/Contrast 门禁添一
            # 笔不存在的账，标题补间也会指向一个永远看不见的元素。
            seg_cards.append(
                card_open
                + f'{image_html}\n'
                + progress_html
                + '  </div>'
            )
        elif aspect == "landscape":
            # 横屏内容段：左栏（标题组+句子流）垂直居中成一块，媒体卡居右。
            seg_cards.append(
                card_open
                + '    <div class="text-col">\n'
                + title_wrap
                + f'\n    {verse_html}\n'
                + '    </div>'
                + f'{image_html}\n'
                + progress_html
                + '  </div>'
            )
        else:
            seg_cards.append(
                card_open
                + title_wrap
                + f'{image_html}\n'
                + progress_html
                + f'    {verse_html}\n'
                + '  </div>'
            )

        # GSAP animations — first clip fades in; later clips cross-fade.
        is_first = (i == 0)
        a_first = a_["firstSegmentFadeIn"]
        a_fadein = a_["segmentFadeIn"]
        a_fadeout = a_["segmentFadeOut"]
        _fadein_dur = a_fadein["duration"]
        if is_first:
            gsap_lines.append(
                f'tl.fromTo("#{sid}",{{opacity:0}},{{opacity:1,'
                f'duration:{a_first["duration"]}}},{s:.2f})'
            )
        else:
            gsap_lines.append(
                f'tl.fromTo("#{sid}",{{opacity:0}},'
                f'{{opacity:1,duration:{_fadein_dur},'
                f'ease:"{a_fadein["ease"]}"}},{s:.2f})'
            )
        # 淡出 + 硬清。段落之间天然隔着一句静音（gap = 下一段 start − 本段 end）：
        # 淡出只按配置时长从本段结束起算时，默认 gap 更长，上一段已淡干净、下一段
        # 还没开始淡入，边界留下只剩背景的空帧（24fps 实测 2~3 帧）。淡出因此跨过
        # 整段间隔、铺到下一段淡入结束，两张卡真正交叠成 cross-fade。
        # （"静音期保持全显、再与淡入对称淡出"试过：gap 一大仍露约 0.15s 空档。）
        _next_start = clips[i + 1]["start"] if i + 1 < len(clips) else None
        _fadeout_dur = a_fadeout["duration"]
        if _next_start is not None:
            _fadeout_dur = max(_fadeout_dur,
                               max(0.0, _next_start - (s + d)) + _fadein_dur)
        gsap_lines.append(
            f'tl.to("#{sid}",{{opacity:0,duration:{_fadeout_dur:.2f},'
            f'ease:"{a_fadeout["ease"]}"}},{s + d:.2f})'
        )
        gsap_lines.append(
            f'tl.set("#{sid}",{{opacity:0}},{s + d + _fadeout_dur:.2f})'
        )
        # 入场动效预算随段长归一化：短段整体压缩，长段维持原速。
        _eb = a_["entranceBudget"]
        _k = min(1.0, max(_eb["minFactor"], d / _eb["normSeconds"]))
        # Title entrance（画布页没有 HTML 标题，标题在画布里，补间一起跳过）
        if not is_canvas:
            a_title = a_["titleEntrance"]
            gsap_lines.append(
                f'tl.from("#title-{sid}",{{scale:{a_title["from"]},'
                f'duration:{a_title["duration"] * _k:.2f},'
                f'ease:"{a_title["ease"]}"}},{s:.2f})'
            )
        if has_image:
            a_img = a_["imageEntrance"]
            # 配图卡（两画幅）从下方滑入（y）。agenda 卡无配图，不入场。
            # 整页画布例外：满幅媒体再位移 40px 就在页底露出一条 40px 页面底
            # （实测 t=48.6s 帧），只剩淡入。
            _img_ent = "" if is_canvas else f',y:{a_img["vert_y"]}'
            gsap_lines.append(
                f'tl.from("#img-{sid}",{{opacity:0{_img_ent},'
                f'duration:{a_img["duration"] * _k:.2f},'
                f'ease:"{a_img["ease"]}"}},'
                f'{s + a_img["startDelay"] * _k:.2f})'
            )
        gsap_lines.append(
            f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
        )

    # ── Subtitle cues ──────────────────────────────────────────────
    sub_cues_js = _build_subtitle_cues(sentences)

    gsap_code = "\n  ".join(gsap_lines)

    # ── 装配最终 HTML ────────────────────────────────────
    # 骨架在 templates/composition.html。样式与脚本各自装配好后填入占位符；
    # CSS 走 <style> 内联（无头浏览器首帧不能等外链 CSS，否则白屏错版），
    # GSAP 是脚本、可以外链（vendor/ 相对路径）。
    style = _fill(_load_asset("composition.css"), {
        "__CTV_ROOT_VARS__": root_vars,
    }, "composition.css")
    script = _fill(_load_asset("runtime.js"), {
        "__CTV_GSAP__": gsap_code,
        "__CTV_CUES__": sub_cues_js,
        "__CTV_VERSE_CLIP__": f"{_v_verse_clip:g}",
    }, "runtime.js")
    html = _fill(_load_asset("composition.html"), {
        "__CTV_GSAP_SRC__": gsap_src_attr,
        "__CTV_STYLE__": style,
        "__CTV_SCRIPT__": script,
        "__CTV_SEG_CARDS__": chr(10).join(seg_cards),
        "__CTV_ASPECT__": aspect,
        "__CTV_DURATION__": f"{total_dur:.2f}",
        "__CTV_WIDTH__": str(width),
        "__CTV_HEIGHT__": str(height),
        "__CTV_FPS__": str(fps),
        "__CTV_AUDIO_SRC__": quote(audio_src),
    }, "composition.html")

    return html

