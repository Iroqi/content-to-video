#!/usr/bin/env python3
"""Composition HTML compiler for Content-to-Video.

This module owns the presentation compiler: normalized composition inputs, HTML/CSS
and GSAP timeline generation. CLI orchestration, file validation and rendering
remain outside this module.
"""
import html
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from _theme import (
    get_theme_colors, get_default_accent, darken,
    relative_luminance, mix, normalize_accent, theme_bg_stops,
    ensure_text_contrast,
)
from _template import load_template, get_canvas
from _images_schema import (classify_media_path, unknown_media_keys,
                            MEDIA_ENTRY_KEYS)
from _segments import (is_content_sid, is_valid_sid, seg_layout, SID_RULE)


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
    """把媒体条目归一成渲染器的单一形状 {src, media_type, opts}。"""
    normalized = {}
    for sid, media in (images or {}).items():
        # sid 会拼进 HTML 属性、JS 对象键和 GSAP 选择器。CLI 路径由
        # validate_images_json 把守；库调用方直接传 dict 时这里是唯一防线，
        # 与 CLI 同一口径（_SID_RE），不让"单点依赖 CLI 校验"成为转义豁免。
        if not is_valid_sid(sid):
            raise ValueError(
                f"images.json 的段落 key 必须是合法段 id"
                f"（{SID_RULE}；实际: {sid!r}）")
        if isinstance(media, dict):
            # 清单外的键在下面两条分支里都会跟着进 opts 而无人读。静默丢会让
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


# ── 渲染上下文：一次派生，处处共用 ──────────────────────────────────────
# generate_html 只做三件事：归一化入参 → 建上下文 → 逐卡装配。主题、模板
# 几何与 :root 变量表都在 _build_render_context 里派生一次；单卡的归一、
# 装配与动画各自成函数，版式差异只体现在装配一处。


def _build_render_context(tpl, aspect, width, height, theme, images):
    """派生渲染端消费的一切：主题配色、分画幅几何（含模板一致性护栏）与
    :root 变量表。任何一条护栏发现模板配错即 raise。

    返回 SimpleNamespace：generate_html 与各卡片函数只从它取值，不再各自
    摸模板——同一数值的第二个真相源就是漂移的开始。
    """
    rc = SimpleNamespace(aspect=aspect, width=width, height=height,
                         images=images)

    # 主题配色（背景/网格/文字），accent 色不受主题影响
    rc.theme_colors = get_theme_colors(theme)
    # 按主题主文字色亮度判深浅底（_theme._THEMES 是唯一权威，
    # 新增主题无需改这里的枚举）——tagline 的"同色相只调明度"在深色
    # 底下方向要反过来（提亮而不是压暗）。
    rc.dark = relative_luminance(rc.theme_colors["text_color"]) > 0.5
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
    _vv = rc.vv = tpl_layout["verse"]
    _il = rc.il = tpl_layout["image"]
    _tgl = rc.tgl = tpl_layout["tagline"]

    _sl = tpl_layout["subtitle"]
    # 字幕字号是唯一被消费的 subtitle 参数（两画幅各读各的 subtitle 块）
    css_sub_font = f'{_sl["fontSize"]}px'

    # ── 分画幅几何派生 ──────────────────────────────────────────────
    # 两画幅各挂一块 verse / 图片 / agenda 几何，数值互不共用；只有两画幅
    # 都消费的键才两边都写。竖屏独有的定位键（segCard.padding、verse.bottom、
    # image.marginSide/bottomGapToVerse）只存在于 vertical 块——横屏是弹性
    # 列，没有这些概念，放一个 0 值占位只会让人以为改得动。
    _v_verse_clip = rc.verse_clip = _vv["clipPad"]
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
    # 文本安全 accent：cream 浅底上原色 accent 做正文色对比度不足、字面发糊，
    # 与 tagline 同法压暗（同色相只降明度）；dark 底从原色起步。两条路径最后
    # 都过 ensure_text_contrast 兜到 check 门禁的最严一档。只喂给"写在底上的
    # 字"（活动句着色 / .ag-idx），装饰仍走原色 --seg-accent。
    ac_text = ensure_text_contrast(ac if rc.dark else darken(ac), rc.bgs)
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
    return f"{_title_font_px}px"


def _tagline_html(rc, seg, sid, ac):
    """段落 tagline 行（可空）。缩进与对齐归 CSS（--ctv-v-tagline-indent）。"""
    if not seg.get("tagline"):
        return ""
    # 深色主题向白提亮（无差别 _darken 在 dark 下对比度只有 ~3.3，不达
    # WCAG AA）；浅色主题压暗一档后仍由 ensure_text_contrast 兜到门禁最
    # 严档。
    _tag_color = ensure_text_contrast(
        mix(ac, "#ffffff", 0.62) if rc.dark else darken(ac), rc.bgs)
    return (f'<div class="tagline" id="tag-{sid}" '
            f'style="color:{_tag_color}">{esc(seg["tagline"])}</div>')


def _media_html(rc, sid, s, d):
    """配图/视频容器 HTML（右栏或画布槽位）。仅在 sid 有配图映射时调用。"""
    media_info = rc.images[sid]
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
        return (
            f'\n    <div class="seg-image" id="img-{sid}">\n'
            f'      <video id="vid-{sid}" src="{quote(media_path)}" '
            f'data-start="{s}" data-duration="{d}" '
            f'{loop} {muted} {autoplay} {playsinline} '
            f'{poster_attr}>\n'
            f'      </video>\n'
            f'    </div>'
        )
    # 静态图 / 动图走 <img>。SVG 按 C3 规范不铺满幅底，外面再套描边和
    # 发光就等于给一片空白画框，挂 bare-media 让 CSS 撤掉这两层装饰。
    _bare = " bare-media" if media_path.lower().endswith(".svg") else ""
    return (
        f'\n    <div class="seg-image{_bare}" id="img-{sid}">\n'
        f'      <img src="{quote(media_path)}" alt="">\n'
        f'    </div>'
    )


def _verse_html(seg, sid, ac_text_attr):
    """句子流（歌词式 verse）DOM：该段全部句子按序渲染成静态行。"""
    _vlines = [
        # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
        # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
        # 永久失灵或错行——宁可都不高亮，也不错误高亮
        f'<div class="verse-line" data-i="{_s2.get("index", -1)}">'
        f'{esc(_s2["text"])}</div>'
        for _s2 in seg["sentences"]
    ]
    return (
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


def _prepare_card(rc, clip, i):
    """把一段归一成渲染消费的全部形状：id/颜色/版式判定 + 标题字号 +
    各部件 HTML（题头组、配图、句子流、卡壳、进度条）。

    版式差异（agenda/canvas/slot）在这里只做判定和护栏，装配顺序留给
    _assemble_card。
    """
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
        f'{esc(seg.get("title") or "")}</div>\n'
        f'      {tagline_html}\n'
        f'    </div>'
    )
    return SimpleNamespace(
        seg=seg, sid=sid, s=s, d=d, wipe=clip["wipe"], win_start=win_start,
        peel=clip.get("peel"), gets_peeled=clip["gets_peeled"],
        ac=ac, ac_attr=ac_attr,
        ac_text_attr=ac_text_attr, layout=layout, is_agenda=is_agenda,
        has_image=has_image, is_canvas=is_canvas, title_size=title_size,
        tagline_html=tagline_html, image_html=image_html,
        verse_html=verse_html, card_open=card_open,
        progress_html=progress_html, title_wrap=title_wrap)


def _assemble_card(rc, card, clips, manifest):
    """按版式把部件拼成卡片 DOM：四种形态只差部件取舍与顺序。"""
    if card.is_agenda:
        # agenda 卡：head+列表在 .agenda-col 内，verse 收在列尾（锚底），
        # 进度条留在卡底部（col 之外，贴屏底）。
        return (card.card_open
                + _agenda_col_html(card.seg, clips, manifest, rc.ag, rc.dark,
                                   card.ac, card.ac_attr, card.title_size, rc.bgs)
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
    if not card.is_canvas:
        a_title = a_["titleEntrance"]
        lines.append(
            f'tl.from("#title-{sid}",{{scale:{a_title["from"]},'
            f'duration:{a_title["duration"] * _k:.2f},'
            f'ease:"{a_title["ease"]}"}},{s:.2f})'
        )
    if card.has_image and not card.is_canvas:
        a_img = a_["imageEntrance"]
        # 配图卡（两画幅）从下方滑入（y）。agenda 卡无配图，不入场。
        # 整页画布连这条补间也不生成：wipe 揭开即要求画面到位，配图再自带
        # 0.2s 延迟淡入会演成"先擦出空页、再浮出海报"的两段式（实测 15.9s 帧）。
        lines.append(
            f'tl.from("#img-{sid}",{{opacity:0,y:{a_img["vert_y"]},'
            f'duration:{a_img["duration"] * _k:.2f},'
            f'ease:"{a_img["ease"]}"}},'
            f'{s + a_img["startDelay"] * _k:.2f})'
        )
    lines.append(
        f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
    )
    return lines


def generate_html(manifest, audio_src, images=None,
                  width=None, height=None,
                  gsap_src=_DEFAULT_GSAP_SRC,
                  aspect="portrait", theme="dark", fps=24):
    """Generate complete Hyperframes HTML composition string.

    Args:
        images: {段落 id: 媒体对象}，形态与 images.json 一致（裸字符串路径由
            `_images_schema.validate_images_json` 在上游拒收）；None/缺键 = 该段纯文字。
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
    gsap_src_attr = gsap_src.replace("&", "&amp;").replace('"', "&quot;")

    rc = _build_render_context(tpl, aspect, width, height, theme, images)

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
        s_i, d_i = clip["start"], clip["duration"]
        prev_end = (clips[i - 1]["start"] + clips[i - 1]["duration"]) if i else None
        clip["wipe"] = _wd if prev_end is None else round(
            min(_wd, max(0.0, s_i - prev_end)), 2)
        clip["win_start"] = round(max(0.0, s_i - clip["wipe"]), 2)
        win_end = clips[i + 1]["start"] if i + 1 < len(clips) else round(total_dur, 2)
        clip["vis"] = round(win_end - clip["win_start"], 2)
        # line 档的剥离挂在被犁走的旧卡上：本卡揭开窗口 [win_start, +wipe]
        # 就是上一页的"被推走"窗口（旧卡窗口恰铺到本段音频起点，补间在窗内）。
        # wipe 被 gap 钳到 0 的段没有扫过的空间，线与剥离都不生成（瞬间切）。
        clip["peel"] = None
        if _is_line and i and clip["wipe"] > 0:
            prev_sid = (clips[i - 1]["seg"].get("id")
                        or f"seg{i}")  # 与 _prepare_card 的缺 id 兜底同式
            clip["peel"] = (prev_sid, clip["wipe"])
            clips[i - 1]["gets_peeled"] = True  # 旧卡要挂底缘暗边（_peel_shade_html）

    # ── 段落卡片 HTML + GSAP 时间线 ──────────────────────
    # closing_cta 只有结尾 agenda 卡这一个消费者：没有 closing 段、或那一页被作者
    # 换成整页海报（closing_layout: "canvas"）时，这条尾行都无处可画。不 warn 就成了
    # "稿子里写了行动号召，画面上什么都没有"，与 writing.md 承诺的"丢弃都只向
    # stderr 打 [warn]"口径矛盾。
    if (str(manifest.get("closing_cta") or "").strip()
            and not any(c["seg"].get("id") == "closing"
                        and seg_layout(c["seg"]) == "agenda" for c in clips)):
        print("[warn] manifest 有 closing_cta，但本次没有 agenda 版式的结尾页可承载它——"
              "行动号召只画在结尾 agenda 卡上，这条尾行不会出现在画面里"
              "（要保留 cta 就补一段 closing 并把 closing_layout 留空/改回 agenda，"
              "或删掉 closing_cta）", file=sys.stderr)

    seg_cards = []
    gsap_lines = []
    for i, clip in enumerate(clips):
        card = _prepare_card(rc, clip, i)
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
