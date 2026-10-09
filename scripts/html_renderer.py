#!/usr/bin/env python3
"""Composition HTML compiler for Content-to-Video.

This module owns the presentation compiler: normalized composition inputs, HTML/CSS
and GSAP timeline generation. CLI orchestration, file validation and rendering
remain outside this module.
"""
import html
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from _theme import (
    get_theme_colors, get_default_accent, mix, normalize_accent,
    theme_bg_stops, ensure_text_contrast, DEFAULT_THEME,
)
from _template import load_template, get_canvas, normalize_aspect
from _images_schema import (unknown_media_keys, MEDIA_ENTRY_KEYS,  # noqa: E402
                            classify_media_path, is_svg_path)
from _segments import (is_content_sid, seg_layout)
from _path_morph import make_morph, interp as _morph_interp
from _ease import curve as ease_curve
from _timeline import beat_positions, beat_span, beat_cycles
from _cam_crop import cam_default_origin


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
    return html.escape(text)


def segment_duration(seg):
    """段落时长（秒）：末句 start_time+duration − 首句 start_time。
    与 generate_html 的 clip 计时同一口径。
    """
    sents = seg["sentences"]
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
    content_clips = [c for c in clips if is_content_sid(c["seg"]["id"])]

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
        rows = [(f"{n:02d}", _cell(c["seg"]["title"], c["seg"]["id"]),
                 _fmt_mmss(c["duration"]))
                for n, c in enumerate(content_clips, 1)]
    else:
        rows = [(f"{n:02d}",
                 _cell(c["seg"].get("takeaway") or c["seg"]["title"],
                       c["seg"]["id"]), "")
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


def _agenda_col_html(seg, clips, manifest, ag, ac, ac_attr,
                     title_size, bgs, apple_opening=False, card=None):
    """纯文字 agenda 卡的前半段：.agenda-col > 题头 + agenda 行（col 不闭合）。

    调用方拼上句子流（verse）后再闭合 .agenda-col——flex 列"题头在顶、
    agenda 垂直居中、句子流锚底"两画幅共用同一套 DOM，几何差异全在 CSS。
    kicker 取 opening/closing 的 tagline 字段（可选的一行小标题）。
    apple_opening 时额外带一枚题头光晕（.apple-halo）与 data 标记——光晕在
    DOM 最前、z-index:-1，衬在标题后面，由 _card_timeline_lines 编排淡入与
    呼吸；data 标记供 CSS/调试识别人，不参与渲染路径。

    card 形参只用于**回写**实际画出来的行数（_apple_opening_lines 要拿它算
    整支舞的收尾时刻）。行数只在这里算得出，而 `_agenda_rows` 超上限时会打
    [warn]——时间线侧再算一遍就是把同一条 warn 打两次，所以把结果递过去，
    不重算。
    """
    sid = seg["id"]
    halo_html = ('    <div class="apple-halo" id="halo-%s"></div>\n' % sid
                 if apple_opening else "")
    col_attr = (' data-opening-anim="apple"' if apple_opening else "")
    kicker_html = ""
    if seg.get("tagline"):
        # 深底起手色向白提亮，再统一过 ensure_text_contrast 的对比度保底。
        _kc = ensure_text_contrast(mix(ac, "#ffffff", 0.55), bgs)
        kicker_html = (f'<div class="agenda-kicker" style="color:{_kc}">'
                       f'{esc(seg["tagline"])}</div>')
    rows, tail_rows = _agenda_rows(sid, clips, manifest, ag)
    if card is not None:
        # 截断后的真实行数（正文 + cta 尾行）。苹果开场按它算 stagger 的收尾时刻，
        # 必须是**画在画面上**的行数，不是稿件里想写的行数。
        card.agenda_row_count = len(rows) + len(tail_rows)
    agenda_html = (f'<div class="agenda">{_agenda_row_html(rows)}</div>'
                   if rows else "")
    tail_html = (f'<div class="agenda-tail">{_agenda_row_html(tail_rows)}</div>'
                 if tail_rows else "")
    return (f'    <div class="agenda-col"{col_attr}>\n'
            f'{halo_html}'
            f'      <div class="agenda-head">{kicker_html}'
            f'<div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 {ag["titleGlow"]}px {ac_attr}40">'
            f'{esc(seg["title"])}</div></div>\n'
            f'      {agenda_html}{tail_html}\n')


def _normalize_images(images):
    """把媒体条目归一成渲染器的单一形状 {src, opts}。

    入参必须是过 _images_schema.validate_images_json 的数据（契约先校验是本
    技能的入口规则：gen_hyperframes 经 load_images_json 进来，键合法性、
    媒体对象形状、src 非空都在那里拦下），这里只做归一与丢键提醒。
    素材类型分两档：静态图（image/svg/gif 走 <img>）与视频（video 走 <video>
    自动循环静音播放）。gif 与 image 同档——它就是一张会自己动的静态图。
    """
    normalized = {}
    for sid, media in (images or {}).items():
        # 清单外的键会跟着进 opts 而无人读。静默丢会让
        # "images.json 里明明写了 alt，画面上什么都没有"变成无解的困惑，
        # 所以点名叫出它们——清单定义在 _images_schema。
        unknown = unknown_media_keys(media)
        if unknown:
            print(f"[warn] images.json 的 '{sid}' 含渲染端不读的字段："
                  f"{', '.join(unknown)}——它们不会出现在画面上。条目只认："
                  f"{', '.join(sorted(MEDIA_ENTRY_KEYS))}；"
                  "写错了就删掉。"
                  "想改构图请改稿件或 SVG 本体，images.json 只管映射与播放属性",
                  file=sys.stderr)
        media_path = media["src"]
        media_type = classify_media_path(media_path, media.get("type", "auto"))
        media_opts = {k: v for k, v in media.items() if k not in ("src", "type")}
        normalized[sid] = {
            "src": media_path,
            "type": media_type,
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


# ── 渲染上下文：一次派生，处处共用 ──────────────────────────────────────
# generate_html 只做三件事：归一化入参 → 建上下文 → 逐卡装配。主题、模板
# 几何与 :root 变量表都在 _build_render_context 里派生一次；单卡的归一、
# 装配与动画各自成函数，版式差异只体现在装配一处。


def _build_render_context(tpl, aspect, width, height, theme, images, fps=24):
    """派生渲染端消费的一切：主题配色、分画幅几何（含模板一致性护栏）与
    :root 变量表。任何一条护栏发现模板配错即 raise。

    返回 SimpleNamespace：generate_html 与各卡片函数只从它取值，不再各自
    摸模板——同一数值的第二个真相源就是漂移的开始。
    """
    rc = SimpleNamespace(aspect=aspect, width=width, height=height,
                         images=images, fps=fps)

    # 主题配色（背景/网格/文字），accent 色不受主题影响
    rc.theme_colors = get_theme_colors(theme)
    # 背景渐变的十六进制色标集合：accent 派生文字色的对比度保底按其中最坏
    # 一档判定（theme_bg_stops 只解析自家 _THEMES 的渐变串）。
    rc.bgs = theme_bg_stops(theme)

    # 从模板加载布局/动画/字体参数
    tpl_layout = tpl["layout"][aspect]
    rc.anim = tpl["animation"]
    tpl_typo = tpl["typography"]

    # 从模板提取 CSS 变量值
    _tl = rc.tl = tpl_layout["title"]
    _ag = rc.ag = tpl_layout["agenda"]
    _vv = tpl_layout["verse"]
    _il = tpl_layout["image"]
    _tgl = tpl_layout["tagline"]

    _sl = tpl_layout["subtitle"]
    # 字幕字号是唯一被消费的 subtitle 参数（两画幅各读各的 subtitle 块）
    css_sub_font = f'{_sl["fontSize"]}px'

    # ── 分画幅几何派生 ──────────────────────────────────────────────
    # 两画幅各挂一块 verse / 图片 / agenda 几何，数值互不共用；只有两画幅
    # 都消费的键才两边都写。竖屏独有的定位键（segCard.padding、verse.bottom、
    # image.marginSide/bottomGapToVerse）只存在于 vertical 块——横屏是弹性
    # 列，没有这些概念，放一个 0 值占位只会让人以为改得动。
    rc.verse_clip = _vv["clipPad"]
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

    css_tagline_font = f'{_tgl["fontSize"]}px'
    # tagline 显式行高：竖屏标题组几何推导需要确定行高（依赖浏览器 normal
    # 行高约 1.14-1.2 随字体浮动，会让几何不可推导）。
    css_tagline_lh = _tgl["lineHeight"]

    # 其余 CSS 变量（网格/进度条/字体排印）也从模板读取
    _grid = tpl_layout["grid"]
    css_grid_size = _grid["size"]
    # 段落氛围光（.seg-card::after 的径向渐变几何）。它是随画幅变的——竖屏那团
    # 居中、宽大于高；横屏偏媒体区中心（68% 50%）且高大于宽。所以几何进模板，
    # CSS 只拿 var(--ctv-amb-*) 拼字符串，不在 [data-aspect] 分支里存第二份。
    _amb = tpl_layout["ambience"]
    _camb = tpl["canvasAmbience"]
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
  --ctv-bg-gradient:{rc.theme_colors["bg_gradient"]};
  --ctv-font-family:{css_font_family};
  --ctv-font-mono:{css_mono_family};
  --ctv-grid-size:{css_grid_size}px;--ctv-grid-color:{rc.theme_colors["grid_color"]};
  --ctv-text-color:{rc.theme_colors["text_color"]};
  --ctv-img-glow:{_il["glow"]}px;
  --ctv-title-weight:{css_title_weight};
  --ctv-title-lh:{css_title_lh};--ctv-title-tracking:{css_title_tracking};
  --ctv-tagline-font:{css_tagline_font};--ctv-tagline-mt:{css_tagline_mt}px;
  --ctv-tagline-lh:{css_tagline_lh};--ctv-tagline-weight:{css_tagline_weight};
  --ctv-tagline-indent:{_tgl["indent"]}px;--ctv-tagline-tick:{_tgl["tickWidth"]}px;
  --ctv-prog-height:{css_prog_height}px;
  --ctv-sub-font:{css_sub_font};
  --ctv-verse-h:{_vv["windowHeight"]}px;--ctv-verse-clip:{_vv["clipPad"]}px;
  --ctv-verse-line:{_vv["linePad"]}px;--ctv-verse-lh:{_vv["lineHeight"]};
  --ctv-verse-rule:{_vv["activeRule"]}em;
  --ctv-verse-who-font:{_vv["whoFontSize"]}px;--ctv-verse-who-gap:{_vv["whoGap"]}px;
  --ctv-ag-x:{_ag["insetX"]}px;--ctv-ag-t:{_ag["insetTop"]}px;--ctv-ag-b:{_ag["insetBottom"]}px;
  --ctv-ag-title-lh:{_ag["titleLineHeight"]};--ctv-ag-title-mt:{_ag["titleMarginTop"]}px;
  --ctv-ag-kicker:{_ag["kickerSize"]}px;--ctv-ag-idx:{_ag["idxSize"]}px;
  --ctv-ag-idx-min:{_ag["idxMinWidth"]}px;--ctv-ag-name:{_ag["nameSize"]}px;
  --ctv-ag-dur:{_ag["durSize"]}px;--ctv-ag-gap:{_ag["rowGap"]}px;
  --ctv-ag-row-pad:{_ag["rowPad"]}px;--ctv-ag-verse-w:{_ag["verseMaxWidth"]}px;--ctv-ag-list-mt:{_ag["listMarginTop"]}px;
  --ctv-amb-rx:{_amb["rx"]}%;--ctv-amb-ry:{_amb["ry"]}%;
  --ctv-amb-cx:{_amb["cx"]}%;--ctv-amb-cy:{_amb["cy"]}%;
  --ctv-amb-alpha:{_amb["alpha"]}%;--ctv-amb-edge:{_amb["edge"]}%;
  --ctv-amb-agenda-cx:{_amb["agendaCx"]}%;--ctv-amb-agenda-cy:{_amb["agendaCy"]}%;
  --ctv-camb-rx:{_camb["rx"]}%;--ctv-camb-ry:{_camb["ry"]}%;
  --ctv-camb-cx:{_camb["cx"]}%;--ctv-camb-cy:{_camb["cy"]}%;
  --ctv-camb-alpha:{_camb["alpha"]}%;--ctv-camb-edge:{_camb["edge"]}%;"""
    if aspect == "vertical":
        # 标题区定高 = 该盒必须装下的东西：maxLines 行标题 + tagline 一行。
        # 这是个 overflow:hidden 的绝对定位盒，画布与句子流都不为它让位（竖屏三区
        # 各自固定定位）：算小了会把 tagline 静默切掉一截，算大了会往画布上压，
        # 两头都不报错——所以放不进时在这里直接 raise。
        # 行高与字号成对定（template.layout.vertical.title.fontSize=64 +
        # typography.titleLineHeight=1.45）：行高是下限不是口味——竖屏标题带
        # line-clamp + overflow:hidden，CJK 墨迹必须装进行盒。微软雅黑度量盒
        # ≈1.32em 刚好不裁，但 Linux headless 回退 Noto Sans CJK Black 时墨迹
        # ≈1.39em，1.32 会被官方门禁 hyperframes check 报 clipped_text
        # （references/rendering.md「官方校验命令」：不在噪声之列）。64×1.45
        # 两行 + tagline 一行的预算 = 原 72×1.32 几乎不变，两画幅都够装。
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
  --ctv-v-img-w:{int(_il["width"])}px;--ctv-v-img-ms:{_v_img_ms}px;
  --ctv-v-img-top:{_v_img_top}px;--ctv-v-img-height:{int(_il["height"])}px;--ctv-v-img-radius:{_v_img_radius}px;
  --ctv-v-verse-bottom:{_v_verse_bottom}px;"""
    else:
        # 刻意不注入 --ctv-l-gap：左右两栏都是绝对定位，栏间距由 margin/textW/imgW
        # 的算术决定，注入一个没人读的空格令牌只会让人以为改它能挪版式
        # （columnGap 仍然参与上面的越界护栏，那是它的唯一消费者）。
        root_aspect = f"""  --ctv-l-margin:{_lm}px;
  --ctv-l-text-w:{int(_ltext["width"])}px;--ctv-l-text-gap:{int(_ltext["gap"])}px;
  --ctv-l-title-lh:{_tl["lineHeight"]};
  --ctv-l-img-w:{int(_il["width"])}px;--ctv-l-img-h:{_l_img_h}px;
  --ctv-l-img-radius:{_il["borderRadius"]}px;"""
    rc.root_vars = ":root{\n" + root_shared + "\n" + root_aspect + "\n}"
    return rc


def _card_colors(rc, seg):
    """一段卡的 accent 三形态：归一化 hex、HTML 属性副本、正文安全色副本。"""
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
    # 文本安全 accent：accent 从原色起步，过 ensure_text_contrast 兜到 check
    # 门禁的最严一档。只喂给"写在底上的字"（活动句着色 / .ag-idx），装饰
    # 仍走原色 --seg-accent。
    ac_text = ensure_text_contrast(ac, rc.bgs)
    return ac, ac_attr, esc(ac_text)


def _title_font_px(rc, seg, sid, is_agenda):
    """标题字号（内联，唯一不被 CSS 覆盖的标题数值），阈值/字号一律取模板：
    - agenda 页带长度守卫：agenda 头豁免了行数钳制（见 composition.css），
      长标题会无限折行挤爆定高列，故按 CJK 字宽 ≈ 字号 估算，压到目标行数
      内放得下为止；横屏的行预算被 maxRows 吃满，标题只锁 1 行，竖屏允许
      压进 2 行；
    - 横屏内容段超阈值按 guardSize 降档，配合 text-wrap:balance 折行，避免
      长标题把左栏撑出孤字；竖屏内容段一律取 fontSize。
    """
    _tl, _ag, aspect, width = rc.tl, rc.ag, rc.aspect, rc.width
    if is_agenda:
        _title_font_px = _ag["titleSize"]
        _tlen = len(seg["title"].strip())
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
        _tlen = len(seg["title"])
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
    return f"{_title_font_px}px"


def _tagline_html(rc, seg, sid, ac):
    """段落 tagline 行（可空）。缩进与对齐归 CSS（--ctv-tagline-indent）。"""
    if not seg.get("tagline"):
        return ""
    # 深色主题向白提亮（无差别压暗在 dark 下对比度只有 ~3.3，不达
    # WCAG AA），再由 ensure_text_contrast 兜到门禁最严档。
    _tag_color = ensure_text_contrast(mix(ac, "#ffffff", 0.62), rc.bgs)
    return (f'<div class="tagline" id="tag-{sid}" '
            f'style="color:{_tag_color}">{esc(seg["tagline"])}</div>')


def _fmt_attr_num(v):
    """秒 / 增益写进 HTML 属性时的字面形态。

    固定 6 位小数再剥尾零：`2` 不写成 `2.0`（多余），`0.5` 不写成
    `0.5000000000000001`（浮点噪音）。同一份稿件两次生成必须字节一致——
    直接 repr(float) 会让 1/3 这类值带上二进制余尾，diff 里全是假改动。
    """
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def _video_audio_attrs(media_opts, sid, d):
    """视频音轨那几个 data-* 属性（一行一个，末尾带空格）。

    Hyperframes 的 ffmpeg 混音只读两类源：`<audio>` 元素，和声明了
    `data-has-audio="true"` 的 `<video>`。**不写就一条都不混**——所以
    `muted: false`（用户明确要原声）时必须补上这个声明，否则成片有画面、
    没原声，而整条链上没有任何报错。这是本函数存在的唯一理由。

    反过来，`muted: true`（默认）时**不能**写它：契约是二选一，同时挂
    muted 和 data-has-audio 会让 lint 的 video_missing_muted 判据失效。
    """
    attrs = []
    muted = media_opts.get("muted", True)
    if not muted:
        attrs.append('data-has-audio="true"')
        # 知情告警：口播是 <audio id="main-audio">（见 templates/composition.html），
        # 视频原声是第二条进混音器的轨。两条都响 = 两条人声/环境声叠着播，
        # 跑完不报错、成片却没法听——这正是要出声提醒的那一类。
        print(f"[warn] images.json 的 '{sid}' 关掉了 muted：视频原声会与口播"
              "（audio/combined.wav）一起混进成片。原声不是配乐，它和旁白"
              "抢同一段频谱，建议同时给 volume（0.15~0.3 是实测能听清旁白的档）"
              "让原声退到背景。", file=sys.stderr)
    # 增益与裁剪四件套：只对不静音的轨有意义（静音轨调增益听不出区别，
    # 但写了不算错，照样透传——契约层已按 video 校验过类型与范围）。
    for opt, attr in (("volume", "data-volume"),
                      ("fade_in", "data-fade-in"),
                      ("fade_out", "data-fade-out"),
                      ("media_start", "data-media-start")):
        if opt in media_opts:
            attrs.append(f'{attr}="{_fmt_attr_num(media_opts[opt])}"')
    # 淡入淡出之和超过片段时长会被引擎按比例缩放——写了一段"淡入"却因为
    # 时长不够被压成几乎看不见，是典型的"写了没生效"。渲染端这里正好知道
    # 片段有多长（d），所以这句只能在这儿说，契约层说不了。
    fin = float(media_opts.get("fade_in", 0.0) or 0.0)
    fout = float(media_opts.get("fade_out", 0.0) or 0.0)
    if fin + fout > d > 0:
        print(f"[warn] images.json 的 '{sid}' 的 fade_in+fade_out"
              f"（{_fmt_attr_num(fin + fout)}s）超过本段时长"
              f"（{_fmt_attr_num(d)}s）：引擎会把两段淡变按比例压进片段内，"
              "实际听到的大概率比写的短。", file=sys.stderr)
    return " ".join(a + " " for a in attrs) if attrs else ""


def _media_html(rc, sid, s, d):
    """配图/视频容器 HTML（右栏或画布槽位）。仅在 sid 有配图映射时调用。

    静态图与动图统一走 <img>（gif 也 <img>），视频走 <video> 自动循环静音播放。
    SVG 按 C3 规范不铺满幅底，外面再套描边和发光就等于给一片空白画框，
    挂 bare-media 让 CSS 撤掉这两层装饰。
    """
    media_info = rc.images[sid]
    media_path = media_info["src"]
    media_opts = media_info["opts"]
    if media_info.get("type") == "video":
        loop = "loop" if media_opts.get("loop", True) else ""
        muted = "muted" if media_opts.get("muted", True) else ""
        playsinline = "playsinline" if media_opts.get("playsinline", True) else ""
        # autoplay 同样读 images.json 选项（与其余 video 选项一致）
        autoplay = "autoplay" if media_opts.get("autoplay", True) else ""
        poster = media_opts.get("poster", "")
        poster_attr = f'poster="{quote(poster)}"' if poster else ""
        return (
            f'\n    <div class="seg-image" id="img-{sid}">\n'
            f'      <video id="vid-{sid}" src="{quote(media_path)}" '
            f'data-start="{s}" data-duration="{d}" '
            f'{loop} {muted} {autoplay} {playsinline} '
            f'{poster_attr}{_video_audio_attrs(media_opts, sid, d)}>\n'
            f'      </video>\n'
            f'    </div>'
        )
    # 导演模式（images.json 写了 director）：gen_hyperframes 已把净化后的 SVG
    # 回填进 inline_svg。这里内联成活 DOM，GSAP 才能逐帧驱动图内命名元素
    # （见 _director_timeline_lines）。净化去掉了 script/on*/SMIL/墙钟动画，
    # 内联的安全性与决定性由 _svg_sanitize 保证——普通 SVG 仍是 <img>。
    _bare = " bare-media" if is_svg_path(media_path) else ""
    inline_svg = media_opts.get("inline_svg")
    if inline_svg:
        return (
            f'\n    <div class="seg-image{_bare} svg-inline" id="img-{sid}">\n'
            f'      {inline_svg}\n'
            f'    </div>'
        )
    return (
        f'\n    <div class="seg-image{_bare}" id="img-{sid}">\n'
        f'      <img src="{quote(media_path)}" alt="">\n'
        f'    </div>'
    )


def _verse_html(seg, sid, ac_text_attr):
    """句子流（歌词式 verse）DOM：该段全部句子按序渲染成静态行。

    对话段（`dialogue`）在**轮次切换的首句**行首挂一枚说话人标签
    （`<span class="verse-who">`）。它上墙的理由与「画布页要把关键句画进图里」
    同源：谁在说这件事原本只存在于声音里——静音播放、听障观看、或两个音色
    选得相近时，观众手里只剩一串分不清轮次的字幕。只在轮次首句挂，同一
    说话人连着说三句不刷三遍标签（那会让字幕行首变成一列复读的抬头）。
    """
    # 三个 layout 豁免属性打在 .verse 与**每一行**上，两处都要，缺一不可：
    # 检查器只认元素自己身上的标记，**不继承祖先的**。只打 .verse 的话它照样
    # 去量每行，而被 .verse-clip 裁在窗口外的半截行 rect 仍在原位 → 判
    # text_occluded 报 error。实测（7 段竖屏稿）：只打 .verse 报 1 error，
    # 连每行一起打则 0 error、warning 条数不变。快照确认画面本身没问题。
    _prev_who = None
    _vlines = []
    for _s2 in seg["sentences"]:
        _who = _s2.get("speaker") or None
        # 轮次首句才标：与上一句同一说话人时不再重复。
        _who_html = ""
        if _who and _who != _prev_who:
            # 三个豁免属性**也得打在 span 自己身上**：检查器只认元素自己带
            # 的标记、不继承祖先（`.verse` 打过、每行 `.verse-line` 也打过，
            # 实测同一份稿子加了这个 span 之后 Layout 立刻报
            # `text_occluded … span:nth-of-type(1) "主播"`——滚出窗口的行 rect
            # 仍在原位，判据与那两层一模一样，只是又往下走了一层 DOM）。
            _who_html = (f'<span class="verse-who" data-layout-allow-overflow'
                         f' data-layout-allow-overlap'
                         f' data-layout-allow-occlusion>{esc(_who)}</span>')
        _prev_who = _who
        _vlines.append(
            # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
            # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
            # 永久失灵或错行——宁可都不高亮，也不错误高亮
            f'<div class="verse-line" data-i="{_s2.get("index", -1)}"'
            f' data-layout-allow-overflow data-layout-allow-overlap'
            f' data-layout-allow-occlusion>'
            f'{_who_html}{esc(_s2["text"])}</div>'
        )
    return (
        # 豁免的成因：滚出窗口的行视觉上被 overflow:hidden 裁掉，但静态 DOM
        # rect 仍在原位——上越标题区（allow-overlap）、下碰底部元素如进度条
        # （allow-occlusion）、整体越出卡片（allow-overflow）。活动行锚定在
        # 窗口内 clipPad 处，真实重叠不可能发生。
        # --ctv-verse-who-color 只在这里写一次：值来自本段 accent 的文本安全
        # 副本（_card_colors 已过 ensure_text_contrast 保到 check 门禁最严档），
        # CSS 侧只消费变量、不存第二份色值。
        f'\n    <div class="verse" id="verse-{sid}" '
        f'data-layout-allow-overflow data-layout-allow-overlap '
        f'data-layout-allow-occlusion>'
        f'<div class="verse-clip" data-accent="{ac_text_attr}" '
        f'style="--ctv-verse-who-color:{ac_text_attr}">'
        f'{"".join(_vlines)}</div></div>'
    )


def _is_keep_page(rc, sid):
    """这一页是不是跨段接续页（images.json 的 ``stage: "keep"``）。

    烘焙本身在生成期的 ``gen_hyperframes.director_prepare``（见 ``_stage_carry``），
    渲染端只读这一个布尔：它决定"还要不要把这一页当新页再演一遍"。
    """
    entry = rc.images.get(sid)
    return bool(entry) and entry["opts"].get("stage") == "keep"


def _prepare_card(rc, clip):
    """把一段归一成渲染消费的全部形状：id/颜色/版式判定 + 标题字号 +
    各部件 HTML（题头组、配图、句子流、卡壳、进度条）。

    版式差异（agenda/canvas/slot）在这里只做判定和护栏，装配顺序留给
    _assemble_card。
    """
    seg = clip["seg"]
    # sid 由 validate_timing_manifest 把守（存在、合法、唯一，见 _manifest_schema
    # 的 _validate_sid），契约先校验是入口规则，这里直接下标取值。
    sid = seg["id"]
    s = clip["start"]
    d = clip["duration"]
    # 可见窗口 = [擦除起点, 被下一页盖住的时刻]（几何在 generate_html 的
    # clips 预处理里算齐）；inline clip-path 与 fromTo 的 from 值同型，
    # 管住 JS 加载前的整页闪现。
    win_start = clip["win_start"]
    vis_d = clip["vis"]
    ac, ac_attr, ac_text_attr = _card_colors(rc, seg)
    # 版式一律由 _segments.seg_layout 分派（layout 字段优先，结构性页按 id 兜
    # 档），不再各处写 sid 字面量。agenda = 纯文字投影卡：不配图，用章节罗列/
    # 要点总结填充。gen_hyperframes 已把 agenda 版式的 opening/closing 键弹出，
    # 库调用方仍带映射时这里也强制忽略。
    layout = seg_layout(seg)
    is_agenda = layout == "agenda"
    # 蘋果風開場：只对开屏 agenda 卡生效（opening_animation 取值已由契约层把守，
    # 渲染端只认 "apple"）。其他段/其他值一律不进入这条编排——静态開場是缺省。
    apple_opening = (sid == "opening" and is_agenda
                     and seg.get("opening_animation") == "apple")
    has_image = (sid in rc.images) and not is_agenda
    # 整页画布（layout: "canvas"）：配图就是这一页——槽位拉满全屏，HTML 的
    # 标题层与句子流层都不渲染，标题/文字由画布自己画。取值已由
    # _segments._validate_layout 把守；这里只认 canvas 且必须有配图。
    is_canvas = layout == "canvas"
    if is_canvas and not has_image:
        # 不拦就会出一帧只有进度条的空页：画布没图，标题和字幕又都不画。
        raise ValueError(
            f"段落 '{sid}' 声明了 layout=\"canvas\"（整页画布），但没有配图"
            "——画布版式不渲染标题层与句子流层，没有配图就什么都不剩。"
            "请在 images.json 给该段配一张按当前画幅出的图，或去掉 layout 字段"
            "回到槽位版式。")

    title_size = _title_font_px(rc, seg, sid, is_agenda)
    tagline_html = _tagline_html(rc, seg, sid, ac)
    image_html = _media_html(rc, sid, s, d) if has_image else ""
    verse_html = _verse_html(seg, sid, ac_text_attr)

    # 标题与句子流都走 CSS flex 文档流，宽度由 CSS 约束，Python 侧
    # 不推导盒几何。
    # 卡内自带一份与 root 同名的 .bg/.grid 层：卡片原本是透明容器，两页交叠
    # 时新旧文字直接叠影（擦除转场的前提是"每一页都是不透明图层"，对标
    # Remotion slide）。几何与 root 那两层逐像素一致（inset:0、同网格尺寸/掩膜），
    # 擦除时网格严丝合缝，只有内容在换。z-index:-2 排在卡内 accent 光晕(::after,-1)
    # 之下、卡片背景色之上。
    card_open = (
        f'  <div id="{sid}" class="clip seg-card'
        + (" agenda-card" if is_agenda else " seg-canvas" if is_canvas else "")
        + f'" data-start="{win_start:.2f}" data-duration="{vis_d:.2f}" data-accent="{ac_attr}" '
        f'data-track-index="1" style="clip-path:{_wipe_pair(rc)[0]};--seg-accent:{ac_attr};--seg-accent-text:{ac_text_attr}">\n'
        '    <div class="bg"></div>\n    <div class="grid"></div>\n'
    )
    progress_html = (
        f'    <div class="seg-progress" id="prog-{sid}" '
        f'style="background:{ac_attr};width:0"></div>\n'
    )
    title_wrap = (
        f'    <div class="seg-title-wrap">\n'
        f'      <div class="seg-title" id="title-{sid}" '
        f'style="font-size:{title_size};text-shadow:0 0 {rc.tl["glow"]}px {ac_attr}40">'
        f'{esc(seg["title"])}</div>\n'
        f'      {tagline_html}\n'
        f'    </div>'
    )
    return SimpleNamespace(
        seg=seg, sid=sid, s=s, d=d, wipe=clip["wipe"], win_start=win_start,
        # vis = 这一页真正在画面上的时长（[擦除起点, 被下一页盖住]），也是
        # data-duration 写进标签的那个数。苹果开场按它归一，不按 d：编排锚在
        # 擦除起点，可用时长里还含着入场那段 wipe，短开场段里两者差一整个 gap。
        vis=vis_d,
        peel=clip.get("peel"), gets_peeled=clip["gets_peeled"],
        ac=ac, ac_attr=ac_attr,
        ac_text_attr=ac_text_attr, layout=layout, is_agenda=is_agenda,
        has_image=has_image, is_canvas=is_canvas, title_size=title_size,
        apple_opening=apple_opening,
        # agenda 卡装配时回写实际行数（_agenda_col_html）；非 agenda 页用不到，
        # 留 0 而不是 getattr 兜底——苹果开场只在 agenda 卡上生效，读到的
        # 必然是回写过的那个值。
        agenda_row_count=0,
        is_keep=_is_keep_page(rc, sid),
        tagline_html=tagline_html, image_html=image_html,
        verse_html=verse_html, card_open=card_open,
        progress_html=progress_html, title_wrap=title_wrap)


def _assemble_card(rc, card, clips, manifest):
    """按版式把部件拼成卡片 DOM：四种形态只差部件取舍与顺序。"""
    if card.is_agenda:
        # agenda 卡：head+列表在 .agenda-col 内，verse 收在列尾（锚底），
        # 进度条留在卡底部（col 之外，贴屏底）。
        return (card.card_open
                + _agenda_col_html(card.seg, clips, manifest, rc.ag,
                                   card.ac, card.ac_attr, card.title_size,
                                   rc.bgs, card.apple_opening, card)
                + f'    {card.verse_html}\n    </div>\n'
                + card.progress_html
                + '  </div>')
    if card.is_canvas:
        # 整页画布：只有画布 + 进度条。标题层/句子流层连 DOM 都不生成
        # （不是 display:none）：留着的空盒子会给 Layout/Contrast 门禁添一
        # 笔不存在的账，标题补间也会指向一个永远看不见的元素。
        return (card.card_open
                + f'{card.image_html}\n'
                + card.progress_html
                + '  </div>')
    if rc.aspect == "landscape":
        # 横屏内容段：左栏（标题组+句子流）垂直居中成一块，媒体卡居右。
        return (card.card_open
                + '    <div class="text-col">\n'
                + card.title_wrap
                + f'\n    {card.verse_html}\n'
                + '    </div>'
                + f'{card.image_html}\n'
                + card.progress_html
                + '  </div>')
    return (card.card_open
            + card.title_wrap
            + f'{card.image_html}\n'
            + card.progress_html
            + f'    {card.verse_html}\n'
            + '  </div>')


# wipe 各档几何（对标 Codrops/GSAP 遮罩换页：同结构 clip-path 纯属性补间，
# 逐帧 seek 确定性）。from = 全遮蔽，to = 全覆盖：
#  - circle 的 71% 是"圆心到矩形角点"的精确值（√2/2≈70.7%，任意画幅同值）；
#  - diagonal 顶边 30% 斜度、左角先行，是斜向擦除的常见取值。
_WIPES = {
    "vertical": ("inset(100% 0 0 0)", "inset(0% 0 0 0)"),
    "diagonal": ("polygon(0% 100%, 100% 130%, 100% 230%, 0% 200%)",
                 "polygon(0% 0%, 100% 0%, 100% 100%, 0% 100%)"),
    "circle": ("circle(0% at 50% 50%)", "circle(71% at 50% 50%)"),
    # line = 引导线转场（进度条立起来画下一页）：几何同 vertical 的自下而上揭屏，
    # 但揭示边缘骑一条 accent 高亮线（_reveal_line_html），旧页被线犁过上移剥离
    # （peel，_line_timeline_lines）。
    "line": ("inset(100% 0 0 0)", "inset(0% 0 0 0)"),
}


def _wipe_pair(rc):
    style = rc.anim["segmentWipe"]["style"]
    if style not in _WIPES:
        raise ValueError(
            f'animation.segmentWipe.style 不认识 "{style}"，可选：'
            + "、".join(f'"{k}"' for k in _WIPES))
    return _WIPES[style]


def _wipe_ease(rc):
    """揭开曲线唯一口径：clip-path 与引导线必须同一条（线骑边缘的咬合靠它，
    两处各读各的=漂移穿帮）。line 档走 propLine.ease 的书写节奏（慢起—快行—
    慢收），几何档沿用 segmentWipe.ease。"""
    a_ = rc.anim
    if a_["segmentWipe"]["style"] == "line":
        return a_["propLine"]["ease"]
    return a_["segmentWipe"]["ease"]


def _reveal_line_html(rc, card):
    """引导线：进度条的续命——转场时这条 accent 高亮线从页底"脱开"向上扫，
    新页跟着它被画出来（线的下缘 = clip-path 揭示边，同窗同曲线）。它是画面
    本来就有的元素（每段底部都在走的进度条）的下一段生命，不是闯进来的道具。
    作为新卡的末子注入（绘制在内容之上、随父卡 clip-path 只露出揭示边以下部分）。"""
    if rc.anim["segmentWipe"]["style"] != "line" or card.wipe <= 0:
        return ""
    lh = rc.anim["propLine"]["thickness"]
    return (f'    <div class="reveal-line" id="line-{card.sid}" '
            f'style="height:{lh}px"></div>\n')


def _peel_shade_html(rc, card):
    """被揭旧页的底缘暗边：纸绕底边轴掀起，明暗集中在折页线附近（Material
    elevation 的浮起表面边缘变暗）。用覆盖层而非 box-shadow 是两个物理坑：
    卡的外投影会被自身 clip-path 整层裁掉；inset 内阴影又会被后画的 .bg 子层
    盖住。opacity 随 peel 同窗补间（纯属性，seek 确定性不变）。"""
    if not card.gets_peeled or rc.anim["segmentWipe"]["style"] != "line":
        return ""
    sh = round(rc.height * rc.anim["propLine"]["peelShadeFrac"])
    return (f'    <div class="peel-shade" id="shade-{card.sid}" '
            f'style="height:{sh}px"></div>\n')


def _attach_card_tail(card_html, tail_html):
    """把 tail 插进卡片最内层闭合 div 之前（卡片 DOM 的末尾 = 绘制在最上）。"""
    if not tail_html:
        return card_html
    j = card_html.rfind("</div>")
    return card_html[:j] + tail_html + card_html[j:]


def _line_timeline_lines(rc, card):
    """line 档的两条补间：① 引导线骑揭示边从页底扫到页顶（与 clip-path 揭开
    同窗同曲线 _wipe_ease，线的终位 -thickness 让它扫到顶时恰好缩没进边沿）；
    ② 旧页被线犁过之后整层上移剥离（power2.in 加速 + peelRotation 平面逆旋 +
    peelTilt 绕底边的透视后倒，纸真正"揭"起来而不是图层平移）。"""
    if rc.anim["segmentWipe"]["style"] != "line" or card.wipe <= 0:
        return []
    a_ = rc.anim
    h = rc.height
    lines = [
        f'tl.fromTo("#line-{card.sid}",{{y:{h:.1f}}},{{y:'
        f'{-a_["propLine"]["thickness"]:.1f},duration:{card.wipe:.2f},'
        f'ease:"{_wipe_ease(rc)}"}},{card.win_start:.2f})',
    ]
    if card.peel:
        psid, pwd = card.peel
        pl = a_["propLine"]
        # rotationX 正值 + 底边轴 = 上缘向屏幕里倒（右手定则），纸被"揭"起
        # 而非平移；clip-path 在元素自身平面内先裁后变换，3D 不破坏揭开边。
        lines.append(
            f'tl.to("#{psid}",{{y:{-pl["peelFrac"] * h:.1f},'
            f'rotation:{pl["peelRotation"]},'
            f'rotationX:{pl["peelTilt"]},transformOrigin:"50% 100%",'
            f'transformPerspective:{pl["peelPerspective"]},'
            f'duration:{pwd:.2f},ease:"{pl["peelEase"]}"}},{card.win_start:.2f})'
        )
        lines.append(
            f'tl.to("#shade-{psid}",{{opacity:1,duration:{pwd:.2f},'
            f'ease:"{pl["peelEase"]}"}},{card.win_start:.2f})'
        )
    return lines


def _cycle_vars(step):
    """step 上的 repeat / yoyo → 要塞进 GSAP 补间变量的那几项（没写就不塞）。

    跨度由 `_timeline.beat_span` 算，门禁、接续烘焙和这里读的都是同一个数；这里只负责
    把它变成 GSAP 听得懂的写法。`repeat:0` 与缺省等价，不塞进去省字节。
    """
    out = {}
    if step.get("repeat"):
        out["repeat"] = int(step["repeat"])
    if step.get("yoyo"):
        out["yoyo"] = True
    return out


def _cycle_js(step):
    """count / type 的手写代理补间要的那段 JS（含尾逗号）；无循环时是空串。"""
    vars_ = _cycle_vars(step)
    return "".join(f'{k}:{json.dumps(v)},' for k, v in vars_.items())


def _director_timeline_lines(rc, card):
    """方式 C SVG「导演」补间：把 images.json 的 director.steps 展开成挂在同一条
    时间线上的 GSAP 补间，位置取自该段旁白的句子（可为句内小数偏移）——于是图内
    元素的状态变化、描边生长、运镜都和口播对齐，且因为是时间线属性补间（非墙钟），
    逐帧 seek 完全可复现。

    - `at` / `at_time` 两套锚点怎么落成绝对秒，见 `_timeline.beat_positions`（生成期
      门禁与对轴报告调的是同一个函数）。要点：`at` 跟着 manifest 的句子走，重配音自动
      对得上；`at_time` 是手算绝对秒，**与语音无绑定**，换了配音就会整体错位且不报错，
      所以给旁白服务的节拍优先用 `at`（`at_time` 留给真不跟旁白的镜头，如相机推镜）。
    - `target`：图内单个 id 选择器，作用域收到 `#img-{sid}` 之下，避免多段内联
      SVG 之间 id 撞车。运镜就是对一个包住场景的 `<g id="cam">` 补间 scale/x/y/
      svgOrigin（GSAP 写 SVG transform，逐帧确定）——不需要额外原语。作者整页没写
      原点时，这里替相机步注入 `_cam_crop.cam_default_origin` 算出的内容中心：GSAP
      自己的缺省原点是 `#cam` bbox 的左上角、还跟着别的步的 `from` 瞬态飘，不钉死的话
      出画 warn 与跨段烘焙算的取景就不是成片里那一幅。
    - `draw:true`：自绘。用 `pathLength=1` 把任意 path 长度归一，免运行时测量：
      段起点先 `stroke-dasharray:1; stroke-dashoffset:1`（描边收起、视觉上无形），
      念到 pos 时把 `strokeDashoffset` 补到 0（线自己长出来）。目标须是有描边的形状。
    - `morph:{from,to}`：同拓扑 path 形变（命令序列一致、只换坐标）。生成期按 fps×2
      采样成一段 `tl.set(attr:{d})` 关键帧；时间均匀推进、形状进度按该步 `ease`（缺省
      模板 `power2.out`）取样，逐帧 seek 可复现、无运行时依赖。
    - `set` 瞬时赋值；`from`+`to` = fromTo；单独 `to`/`from` 各走一路。缺省
      duration/ease 取模板 animation.director（视觉真源单一数据源）。
    - `count:{to,...}` 数字滚动、`type:{}` 打字机逐字揭示：都展开成"渲染端生成的代理
      补间 + onUpdate 写 textContent"（读的是校验过的标量/元素自身文本，非信源回调）。
      二者与 morph 一样各负责整段、忽略 stagger，且块以 `(function…)()` 起头，故发射时
      一律前导 `;` 断掉上一条无分号的补间行（否则 ASI 会把 `tl.x(…)(function…)()` 黏成
      把 timeline 当函数调，主时间轴建到此处崩）。
    - 补间变量经 json.dumps 序列化：schema 已挡掉 on* 回调与非标量，这里只把它
      变成合法 JS 对象字面量，字符串引号/特殊字符由 JSON 转义兜住。
    """
    if not card.has_image:
        return []
    entry = rc.images.get(card.sid)
    opts = (entry or {}).get("opts", {})
    director = opts.get("director")
    if not director:
        return []
    sentences = card.seg["sentences"]
    dflt = rc.anim["director"]
    sid = card.sid
    # 节拍→绝对秒的解析在 _timeline.beat_positions，与生成期门禁/对轴报告共用一份
    # （两处各写一套，门禁就会对着渲染器不会用的时刻报错）。
    try:
        beats = beat_positions(director["steps"], sentences, card.s, dflt["duration"])
    except ValueError as e:
        raise ValueError(f"段落 '{sid}' 的 director.steps 有一步 {e}")
    # 相机缺省原点的钉值：作者整页没写 svgOrigin/transformOrigin 且真的推了近，就把
    # 生成期算的那个内容中心写成显式 svgOrigin 注入。不钉的话 GSAP 绕的是 `#cam` 自身
    # bbox 的左上角（实测，见 _cam_crop.cam_default_origin 的 docstring），那个角还跟着
    # 别的步的 from 瞬态飘 —— 出画 warn 与跨段烘焙算的取景就和成片不是一幅画。
    pin = cam_default_origin(opts.get("inline_svg"), director["steps"])

    lines = []
    for step, (pos, dur) in zip(director["steps"], beats):
        sel = f"#img-{sid} {step['target']}"
        sel_js = json.dumps(sel)
        ease = step.get("ease", dflt["ease"])
        stagger = step.get("stagger")   # 命中一组元素时逐个错峰（GSAP 原生 stagger）
        cam_step = pin is not None and step["target"] == "#cam"

        def _pin(v):
            return v if not cam_step else dict(v, svgOrigin=pin)

        def _with_timing(v):
            out = dict(v)
            out.setdefault("duration", dur)
            out["ease"] = ease
            if stagger is not None:
                out["stagger"] = stagger
            out.update(_cycle_vars(step))
            return _pin(out)

        if "morph" in step:
            # path 形变：生成期按渲染帧率的 2 倍采样成离散 tl.set(attr:{d}) 关键帧。
            # 时间均匀推进（t=pos+u·span），形状进度 k=ease(每一遍内的位置)——因为 GSAP
            # 不会对 set 再缓动，缓动曲线就由这里的采样定义（缺省取模板 director.ease，与
            # 兄弟 tween 同手性）。逐帧 seek 时 GSAP 只取"最近一个已到的 set"，于是形状是
            # 时间的确定函数、跨平台可复现，不需要任何运行时 morph 库。拓扑相符性在契约层
            # 已校验。repeat/yoyo 折进采样：整段跨度 = dur×遍数，遍序号奇偶决定这一遍正放
            # 还是倒放（倒放喂 ease(1-p)，实测与 GSAP 的 yoyo 一字不差）。
            pf, pt = make_morph(step["morph"]["from"], step["morph"]["to"])
            ease_fn = ease_curve(step.get("ease"), dflt["ease"])
            cycles = beat_cycles(step) or 1
            yoyo = bool(step.get("yoyo"))
            span = beat_span(step, dur)
            n = max(2, min(240, round(span * rc.fps * 2)))
            for i in range(n + 1):
                u = i / n
                phase = u * cycles
                c = int(phase)
                p = phase - c
                if p == 0.0 and c > 0:
                    # 正好踩在遍与遍的分界：GSAP 在这一瞬间报的是**上一遍的末尾**
                    # （实测 t=1.000 处 v=1，1.001 才回到 0），下一遍从 0 重放。
                    c -= 1
                    p = 1.0
                if yoyo and c % 2:
                    p = 1.0 - p             # GSAP 的 yoyo 是"拿倒放的进度去查同一条缓动"
                d = _morph_interp(pf, pt, ease_fn(p))
                t = round(pos + u * span, 2)
                lines.append(
                    f"tl.set({sel_js}, {json.dumps({'attr': {'d': d}})}, {t:.2f})")
            continue

        if "count" in step:
            # 数字滚动：代理对象 p.v 从 from 补间到 to，onUpdate 把当前值写进 <text>
            # 的 textContent。这是**渲染端生成**的回调（只读上面校验过的标量），不是
            # 信源塞的 on* ——GSAP 逐帧 seek 时会重算 p.v 并调 onUpdate，故 seek 可复现。
            cnt = step["count"]
            from_v = float(cnt.get("from", 0))
            to_v = float(cnt["to"])
            dec = int(cnt.get("decimals", 0))
            pre = json.dumps(cnt.get("prefix", ""))
            suf = json.dumps(cnt.get("suffix", ""))
            lines.append(
                # 前导分号：本行以 `(function...)()` 开头，若前一行是无分号的
                # `tl.set(...)`/`tl.to(...)`（补间行都靠 ASI 断句），ASI 会把两者黏成
                # `tl.set(...)(function...)()` —— 把 timeline 当函数调，运行期抛
                # "is not a function"，整条主时间轴建到此处中断（__timelines.main 永不注册）。
                ";(function(){var e=document.querySelector(" + sel_js + ");if(!e)return;"
                "var p={v:" + json.dumps(from_v) + "};"
                "var f=function(){e.textContent=" + pre + "+p.v.toFixed(" + str(dec) + ")+" + suf + ";};"
                "f();"
                "tl.to(p,{v:" + json.dumps(to_v) + ",duration:" + json.dumps(dur)
                + ",ease:" + json.dumps(ease) + "," + _cycle_js(step)
                + "onUpdate:f}," + f"{pos:.2f}" + ");})();")
            continue

        if "type" in step:
            # 打字机逐字揭示：运行时读该 <text> 现有整段文本 s，代理 p.k 从 0 补到
            # s.length，onUpdate 把 s.slice(0, round(p.k)) 写回——和 count 复用同一条
            # onUpdate 代理补间路：零 DOM 改写、零新净化面，逐帧 seek 可复现（依赖渲染
            # harness 触发 onUpdate，与字幕高亮/count 同一前提）。build 期 f() 先把文本
            # 收成空串（未打出），seek 越过 pos 才逐字长回。前导 `;` 同 count 的 ASI 护栏。
            lines.append(
                ";(function(){var e=document.querySelector(" + sel_js + ");if(!e)return;"
                "var s=e.textContent||'';"
                "var p={k:0};"
                "var f=function(){e.textContent=s.slice(0,Math.round(p.k));};"
                "f();tl.to(p,{k:s.length,duration:" + json.dumps(dur)
                + ",ease:" + json.dumps(ease) + "," + _cycle_js(step)
                + "onUpdate:f}," + f"{pos:.2f}" + ");})();")
            continue

        if step.get("draw"):
            lines.append(
                f"tl.set({sel_js}, "
                f"{json.dumps({'attr': {'pathLength': 1}, 'strokeDasharray': 1, 'strokeDashoffset': 1})}, "
                f"{card.s:.2f})")
            draw_vars = {"strokeDashoffset": 0, "duration": dur, "ease": ease}
            draw_vars.update(_cycle_vars(step))
            lines.append(
                f"tl.to({sel_js}, {json.dumps(draw_vars)}, {pos:.2f})")
        if "set" in step:
            set_vars = _pin(dict(step["set"]))
            if stagger is not None:
                set_vars["stagger"] = stagger
            lines.append(f"tl.set({sel_js}, {json.dumps(set_vars)}, {pos:.2f})")
        if "from" in step and "to" in step:
            lines.append(
                f"tl.fromTo({sel_js}, {json.dumps(_pin(step['from']))}, "
                f"{json.dumps(_with_timing(step['to']))}, {pos:.2f})")
        elif "to" in step:
            lines.append(
                f"tl.to({sel_js}, {json.dumps(_with_timing(step['to']))}, {pos:.2f})")
        elif "from" in step:
            lines.append(
                f"tl.from({sel_js}, {json.dumps(_with_timing(step['from']))}, {pos:.2f})")
    return lines


def _apple_opening_budget(rc, card):
    """蘋果風開場的等比压缩系数（1.0 = 原速不压）。

    这支舞的收尾时刻由**行数**决定：最后一行浮起 = rows.delay +
    stagger×(行数−1) + duration，7 行 2.12s、1 行 1.40s。而它能用到的时长是
    开屏页的**可见窗口**（锚在擦除起点 win_start 的整段 life，不是口播段长）。
    两者不挂钩，于是短开场会静默丢尾巴：opening 写一句话（~1.0s）、内容段
    写满 7 行时，最后三行是在页面已被下一页盖住之后才浮起来的——没人看得到，
    日志也不吭声。

    口径对齐 entranceBudget：够就原速，不够按比例压，压到 minFactor 就停手
    并告警（压得更狠就成闪烁了，那比"演到一半被切"更难看）。光晕的呼吸
    (breatheDur) 不参与压缩——它是淡入之后的稳态循环，压缩它等于让开场一直
    在喘，而它也不参与"舞是否演完"的判定。
    """
    ap = rc.anim["opening"]["apple"]
    bud = ap["budget"]
    a_t, a_k, a_r = ap["title"], ap["kicker"], ap["rows"]
    n = max(1, card.agenda_row_count)
    has_kicker = bool(card.seg.get("tagline"))
    # 收尾时刻只数**真会生成**的那几条补间：没写 tagline 就没有 kicker 那条。
    ends = [a_t["duration"],
            a_r["delay"] + a_r["stagger"] * (n - 1) + a_r["duration"]]
    if has_kicker:
        ends.append(a_k["delay"] + a_k["duration"])
    need = max(ends)
    if need <= 0:
        return 1.0
    k = 1.0
    if card.vis < need:
        raw = card.vis / need
        k = max(raw, bud["minFactor"])
        if raw < bud["minFactor"]:
            print(f"[warn] 开屏（opening_animation:\"apple\"）的可见窗口 "
                  f"{card.vis:.2f}s 装不下整支开场动画（{need:.2f}s，"
                  f"{n} 行 agenda）：已压到地板 "
                  f"{bud['minFactor']:.0%}，收尾仍会被下一页切走。"
                  "把 opening 写长一点（建议说完主题 + 为什么值得看，2.5s 以上）"
                  "、减少内容段数，或不写 opening_animation 退回静态开场。",
                  file=sys.stderr)
    return k


def _apple_opening_lines(rc, card):
    """蘋果風開場编排（opening_animation:"apple"）。

    苹果式开场的手感：不是"弹出来"，是"浮出来"——标题带一层高斯模糊由虚到实、
    配一个 1.06→1 的微缩放落定（power3.out 的缓入缓出比 back.out 更"沉"），
    光晕先随标题淡入、随后慢呼吸（repeat:-1 yoyo），kicker 短延迟跟上，agenda
    行逐行浮起（y 24 → 0，stagger 0.12）。

    全部锚在擦除起点 win_start：页面从 clip-path 被擦开的同时内容就在演化。
    时长是编排的一部分，**但按可见窗口等比归一**（_apple_opening_budget）：
    固定 choreography 遇到短开场会静默丢尾巴，而"丢了"这件事只有页面被盖住
    之后才知道，那时已经无法回溯。压缩是等比的，节奏比例不变。
    模糊是短暂的（标题 1.6s 内收敛到 0），成片只有开头约 40 帧带 filter
    开销，无头渲染可接受。
    """
    sid, t = card.sid, card.win_start
    ap = rc.anim["opening"]["apple"]
    k = _apple_opening_budget(rc, card)
    has_kicker = bool(card.seg.get("tagline"))
    lines = []
    halo = ap["halo"]
    lines.append(
        f'tl.fromTo("#halo-{sid}",{{opacity:0}},'
        f'{{opacity:{halo["opacity"]:.2f},duration:{halo["in"] * k:.2f},'
        f'ease:"sine.out"}},{t:.2f})'
    )
    # 呼吸挂淡入完成之后：repeat:-1 永不停，seek 回放由 GSAP 按时间解析，
    # 与 director 的 repeat:-1 同一种确定性（无"最后停在哪儿"可争）。
    # breatheDur 不参与压缩——它是淡入之后的稳态循环，与"舞演完没有"无关。
    lines.append(
        f'tl.to("#halo-{sid}",{{opacity:{halo["breatheTo"]:.2f},'
        f'duration:{halo["breatheDur"]:.2f},repeat:-1,yoyo:true,'
        f'ease:"sine.inOut"}},{t + halo["in"] * k:.2f})'
    )
    a_t = ap["title"]
    lines.append(
        f'tl.from("#title-{sid}",{{opacity:0,scale:{a_t["scale"]},'
        f'filter:"blur({a_t["blur"]}px)",duration:{a_t["duration"] * k:.2f},'
        f'ease:"{a_t["ease"]}"}},{t:.2f})'
    )
    if has_kicker:
        a_k = ap["kicker"]
        lines.append(
            f'tl.from("#{sid} .agenda-kicker",{{opacity:0,'
            f'filter:"blur({a_k["blur"]}px)",duration:{a_k["duration"] * k:.2f},'
            f'ease:"{a_k["ease"]}"}},{t + a_k["delay"] * k:.2f})'
        )
    a_r = ap["rows"]
    lines.append(
        f'tl.from("#{sid} .agenda-row",{{opacity:0,y:{a_r["y"]},'
        f'duration:{a_r["duration"] * k:.2f},stagger:{a_r["stagger"] * k:.2f},'
        f'ease:"{a_r["ease"]}"}},{t + a_r["delay"] * k:.2f})'
    )
    return lines


def _card_timeline_lines(rc, card):
    """本卡的 GSAP 时间线：遮罩擦除入场、标题/配图入场、进度条。"""
    sid, s, d = card.sid, card.s, card.d
    # 动画参数快捷引用
    a_ = rc.anim
    lines = []

    # 换页 = 方向性遮罩擦除（对标 Remotion slide 的 clip-path 等效实现）：本页
    # 整层自下而上从 inset(100% 0 0 0) 揭开，恰在本句音频起点完成；上一页不淡出、
    # 被揭开"盖"掉后随引擎窗口切走。不选 cross-fade（两页互相透明度溶解=凭空
    # 消失再出现，PPT 观感的来源），不选容器位移（clip-path 只裁切，满幅海报
    # 不露页底），纯属性补间逐帧 seek 确定性。
    # 擦除时长被 gap 钳住：字幕活在卡片里，侵入上一页说话期=字幕跟着页被擦走。
    w_from, w_to = _wipe_pair(rc)
    lines.append(
        f'tl.fromTo("#{sid}",{{clipPath:"{w_from}"}},'
        f'{{clipPath:"{w_to}",duration:{card.wipe:.2f},'
        f'ease:"{_wipe_ease(rc)}"}},{card.win_start:.2f})'
    )
    lines.extend(_line_timeline_lines(rc, card))
    # 入场动效预算随段长归一化：短段整体压缩，长段维持原速。
    _eb = a_["entranceBudget"]
    _k = min(1.0, max(_eb["minFactor"], d / _eb["normSeconds"]))
    # Title entrance（画布页没有 HTML 标题，标题在画布里，补间一起跳过）
    if not card.is_canvas and not card.apple_opening:
        a_title = a_["titleEntrance"]
        lines.append(
            f'tl.from("#title-{sid}",{{scale:{a_title["from"]},'
            f'duration:{a_title["duration"] * _k:.2f},'
            f'ease:"{a_title["ease"]}"}},{s:.2f})'
        )
    # 蘋果風開場：整页编排取代通用标题入场（opening_animation:"apple"）。
    # 锚在擦除起点而非段起点：页面从 clip-path 里被擦开，标题若等擦完再出现，
    # 会先"完整亮 0.28s 再跳回模糊起点"——编排从页一出现就在演，模糊→锐利
    # 正好铺满擦除窗口。时长是编排的一部分，不随段长归一化。
    if card.apple_opening:
        lines.extend(_apple_opening_lines(rc, card))
    if card.has_image and not card.is_canvas and not card.is_keep:
        a_img = a_["imageEntrance"]
        # 配图卡（两画幅）从下方滑入（y）。agenda 卡无配图，不入场。
        # 整页画布连这条补间也不生成：wipe 揭开即要求画面到位，配图再自带
        # 0.2s 延迟淡入会演成"先擦出空页、再浮出海报"的两段式（实测 15.9s 帧）。
        # 接续页（stage:"keep"）同样跳过：这一页的画面是上一页演完的样子，
        # 再淡入+上移 40px 就是 _stage_carry 要消灭的那种"页界回弹"。
        lines.append(
            f'tl.from("#img-{sid}",{{opacity:0,y:{a_img["vert_y"]},'
            f'duration:{a_img["duration"] * _k:.2f},'
            f'ease:"{a_img["ease"]}"}},'
            f'{s + a_img["startDelay"] * _k:.2f})'
        )
    lines.append(
        f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
    )
    # 导演补间挂在本卡时间线末尾：位置各自取句子起点，与上面的入场/进度条互不干扰。
    lines.extend(_director_timeline_lines(rc, card))
    return lines


def generate_html(manifest, audio_src, images=None,
                  width=None, height=None,
                  gsap_src=_DEFAULT_GSAP_SRC,
                  aspect="portrait", theme=DEFAULT_THEME, fps=24):
    """Generate complete Hyperframes HTML composition string.

    Args:
        images: {段落 id: 媒体对象}，形态与 images.json 一致（裸字符串路径由
            `_images_schema.validate_images_json` 在上游拒收）；None/缺键 = 该段纯文字。
        aspect: portrait/vertical（3:4）或 landscape（16:9）；data-aspect 与默认
            画布尺寸都按它取，归一化在函数体内完成。
        theme: 只改背景渐变/网格/正文，不改每段 accent 彩色；
            当前只有 "dark"（见 _theme 的主题注册表）。
        gsap_src: 默认指向 composition 项目内的 vendor/，不访问 CDN。
        fps: 写进 data-fps 的渲染提示（渲染命令 --fps 可覆盖）；24 比 30 少抓
            20% 帧、出片更快。

        字幕/内容呈现模式不作为参数暴露；固定为 verse（歌词式句子流）。
    """
    # 先归一化画幅，再按对应画幅取默认画布。否则库调用方省略
    # width/height 时，即使传入 landscape 也会拿到竖屏尺寸。
    aspect = normalize_aspect(aspect)
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
    gsap_src_attr = gsap_src.replace("&", "&amp;").replace('"', "&quot;")

    rc = _build_render_context(tpl, aspect, width, height, theme, images, fps=fps)

    # 段落分组只有一份：manifest["segments"]（_manifest_schema 已保证非空、每段自带
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
            "gets_peeled": False,
        })
    # 擦除转场几何一次算齐（_prepare_card 与 timeline 共用）。引擎按
    # data-start/data-duration 硬切 clip 可见性（实测补间排在窗口外等于没写，
    # 边界全黑帧），所以元素窗口必须精确覆盖"这张页在屏幕上"的全程：从自己的
    # 擦除起点（本句音频起点 − 擦除时长）到下一页擦除完成把它盖住的时刻。
    _is_line = rc.anim["segmentWipe"]["style"] == "line"
    # line 档自带时长档（0.40 > 几何档 0.28）：书写感需要更多笔程，gap 钳制兜底。
    _wd = (rc.anim["propLine"]["duration"] if _is_line
           else rc.anim["segmentWipe"]["duration"])
    for i, clip in enumerate(clips):
        s_i = clip["start"]
        prev_end = (clips[i - 1]["start"] + clips[i - 1]["duration"]) if i else None
        clip["wipe"] = _wd if prev_end is None else round(
            min(_wd, max(0.0, s_i - prev_end)), 2)
        # 整页画布段：入场交给导演逐拍演，模板转场（clip-path 擦除 / 引导线 / 旧页剥离）
        # 一律退场。把 wipe 归零即命中既有的"瞬间切"路径——_reveal_line_html、
        # _line_timeline_lines、peel 三处都以 wipe<=0 为闸自动不生成，画布页在自己的
        # 音频起点整页出现、运动全由 director 驱动（对标 3b1b 的连续镜头，而非翻页）。
        # 接续页（stage:"keep"）同理且更要紧：这一页的画面是上一页演完的样子，
        # 擦进来就是把同一幅画"翻页"了一次，正好抵消 _stage_carry 烘焙的意义。
        if (seg_layout(clip["seg"]) == "canvas"
                or _is_keep_page(rc, clip["seg"]["id"])):
            clip["wipe"] = 0.0
        clip["win_start"] = round(max(0.0, s_i - clip["wipe"]), 2)
        win_end = clips[i + 1]["start"] if i + 1 < len(clips) else round(total_dur, 2)
        clip["vis"] = round(win_end - clip["win_start"], 2)
        # line 档的剥离挂在被犁走的旧卡上：本卡揭开窗口 [win_start, +wipe]
        # 就是上一页的"被推走"窗口（旧卡窗口恰铺到本段音频起点，补间在窗内）。
        # wipe 被 gap 钳到 0 的段没有扫过的空间，线与剥离都不生成（瞬间切）。
        # 上一页是画布段时也不剥：把一张活 diagram 像纸一样掀走正是"演"要取代的 PPT 转场，
        # 画布页退场同样走硬切（被下一页直接盖住）。
        clip["peel"] = None
        if (_is_line and i and clip["wipe"] > 0
                and seg_layout(clips[i - 1]["seg"]) != "canvas"):
            prev_sid = clips[i - 1]["seg"]["id"]  # sid 由契约把守，见 _prepare_card
            clip["peel"] = (prev_sid, clip["wipe"])
            clips[i - 1]["gets_peeled"] = True  # 旧卡要挂底缘暗边（_peel_shade_html）

    # ── 段落卡片 HTML + GSAP 时间线 ──────────────────────
    # closing_cta 只有结尾 agenda 卡这一个消费者：没有 closing 段、或那一页被作者
    # 换成整页海报（closing_layout: "canvas"）时，这条尾行都无处可画。不 warn 就成了
    # "稿子里写了行动号召，画面上什么都没有"，与 writing.md 承诺的"丢弃都只向
    # stderr 打 [warn]"口径矛盾。
    if (str(manifest.get("closing_cta") or "").strip()
            and not any(c["seg"]["id"] == "closing"
                        and seg_layout(c["seg"]) == "agenda" for c in clips)):
        print("[warn] manifest 有 closing_cta，但本次没有 agenda 版式的结尾页可承载它——"
              "行动号召只画在结尾 agenda 卡上，这条尾行不会出现在画面里"
              "（要保留 cta 就补一段 closing 并把 closing_layout 留空/改回 agenda，"
              "或删掉 closing_cta）", file=sys.stderr)

    seg_cards = []
    gsap_lines = []
    for clip in clips:
        card = _prepare_card(rc, clip)
        # 引导线/底缘暗边注入卡 DOM 末尾（绘制在内容之上；线随父卡 clip-path
        # 裁切，shade 挂在被揭的那张旧卡上）
        seg_cards.append(_attach_card_tail(
            _assemble_card(rc, card, clips, manifest),
            _reveal_line_html(rc, card) + _peel_shade_html(rc, card)))
        gsap_lines.extend(_card_timeline_lines(rc, card))

    # ── Subtitle cues ──────────────────────────────────────────────
    sub_cues_js = _build_subtitle_cues(sentences)

    gsap_code = "\n  ".join(gsap_lines)

    # ── 装配最终 HTML ────────────────────────────────────
    # 骨架在 templates/composition.html。样式与脚本各自装配好后填入占位符；
    # CSS 走 <style> 内联（无头浏览器首帧不能等外链 CSS，否则白屏错版），
    # GSAP 是脚本、可以外链（vendor/ 相对路径）。
    style = _fill(_load_asset("composition.css"), {
        "__CTV_ROOT_VARS__": rc.root_vars,
    }, "composition.css")
    script = _fill(_load_asset("runtime.js"), {
        "__CTV_GSAP__": gsap_code,
        "__CTV_CUES__": sub_cues_js,
        "__CTV_VERSE_CLIP__": f"{rc.verse_clip:g}",
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
