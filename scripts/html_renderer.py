#!/usr/bin/env python3
"""Composition HTML compiler for Content-to-Video.

This module owns the presentation compiler: normalized composition inputs, HTML/CSS
and GSAP timeline generation. CLI orchestration, file validation and rendering
remain outside this module.
"""
import json
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
                        media_needs_chartjs, unknown_media_keys, MEDIA_ENTRY_KEYS)
from _script_utils import split_subtitle_cues, subtitle_params_for


DEFAULT_ACCENT = get_default_accent()
_CHART_CFG = load_template()["chart"]

# ── 模板资产：版式的静态骨架 ────────────────────────────────────────────
# CSS / JS / HTML 骨架是 templates/ 下的真实文件：结构与选择器写死，数值一律
# 走 var(--ctv-*)——由本模块从 _template 内联的版式数据派生后注入 :root。
# 因此**改版式不需要碰 Python**。
# 模板是技能包的组成部分，随 zip 分发；缺文件即硬失败（静默产出无样式页面
# 比立刻报错难排查得多）。
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _load_asset(name):
    """读取 templates/ 下的模板资产（CSS / JS / HTML 骨架）。"""
    p = _TEMPLATES_DIR / name
    if not p.is_file():
        raise FileNotFoundError(f"[renderer] 模板资产缺失: {p}")
    return p.read_text(encoding="utf-8")


_CTV_PH_RE = re.compile(r"__CTV_[A-Z0-9_]+?__")


def _fill(template, mapping, name):
    """单遍替换骨架占位符：只有骨架里的那些位置会被填，填进去的内容不再扫描。

    逐串 str.replace 是链式全文扫描，先注入的内容会被后一次替换再读一遍——
    稿件里一句原样写着 __CTV_GSAP__ 的文案（esc 与 json.dumps 都不碰下划线）
    就能把真实代码塞进 JS 字符串字面量，整段内联脚本 SyntaxError、时间线和字幕
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

    For JS string literals (e.g. subtitle cue text assigned via
    textContent), use json.dumps() instead -- HTML entity escaping there
    would render as literal "&amp;" since textContent does not decode
    entities, and raw newline/quote would break the JS source.
    """
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def fallback_segments(sentences):
    """manifest 没有 segments 字段时的兜底分组：每 5 句一组。

    独立成模块级函数（而不是内联在 generate_html 里）。分组规则只写一遍，
    所有调用方共享同一份 timing_manifest.json 作为权威场景描述。
    """
    segments = []
    chunk = 5
    for i in range(0, len(sentences), chunk):
        group = sentences[i:i + chunk]
        segments.append({
            "id": f"seg{len(segments) + 1}",
            "title": group[0]["text"][:20],
            "tagline": "",
            "accent": DEFAULT_ACCENT,
            "sentences": group,
            # 不注入 speed —— auto-group 不应假设语速，必须由上游 manifest 显式声明。
        })
    return segments


def manifest_segments(manifest):
    """本次使用的段落分组：manifest 自带的 segments，没有则按兜底规则自动分组。

    分组结果必须有唯一一份：gen_hyperframes 的孤儿键判定、缺图判定与视频截断
    提示都要按段 id 对账，而自动分组的 seg1/seg2/… 只存在于渲染阶段。各处各自
    重推（或干脆只读 manifest 的 segments）就会与画面实际用的 id 漂移——最坏的
    一种是无 segments 的 manifest 下 images.json 的每个键都被判成孤儿并删掉。
    """
    return manifest.get("segments") or fallback_segments(manifest["sentences"])


def _segment_duration(seg):
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


def _agenda_row_html(rows, esc):
    return "".join(
        f'<div class="agenda-row"><span class="ag-idx">{idx}</span>'
        f'<span class="ag-name">{esc(name)}</span>'
        + (f'<span class="ag-dur">{dur}</span>' if dur else "")
        + '</div>'
        for idx, name, dur in rows)


def _agenda_col_html(seg, clips, manifest, ag, dark_theme, ac, ac_attr,
                     title_size, esc, bgs):
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
    agenda_html = (f'<div class="agenda">{_agenda_row_html(rows, esc)}</div>'
                   if rows else "")
    tail_html = (f'<div class="agenda-tail">{_agenda_row_html(tail_rows, esc)}</div>'
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
    chart spec 当成未知字段清成 null。
    """
    normalized = {}
    for sid, media in (images or {}).items():
        # sid 会拼进 HTML 属性、JS 对象键和 GSAP 选择器。CLI 路径由
        # validate_images_json 把守；库调用方直接传 dict 时这里是唯一防线，
        # 与 CLI 同一口径（_SID_RE），不让"单点依赖 CLI 校验"成为转义豁免。
        if not is_valid_sid(sid):
            raise ValueError(
                f"images.json 的段落 key 必须是合法段 id"
                f"（字母开头，仅字母/数字/-/_，1–64 字符；实际: {sid!r}）")
        if isinstance(media, dict) and "media_type" in media:
            normalized[sid] = media
            continue
        if isinstance(media, dict):
            # 清单外的键在下面两条分支里都会跟着进 opts 而无人读。静默丢会让
            # "images.json 里明明写了 alt，画面上什么都没有"变成无解的困惑，
            # 所以点名叫出它们——清单是 _contracts 的单一来源。
            unknown = unknown_media_keys(media)
            if unknown:
                print(f"[warn] images.json 的 '{sid}' 含渲染端不读的字段："
                      f"{', '.join(unknown)}——它们不会出现在画面上。条目只认："
                      f"{', '.join(sorted(MEDIA_ENTRY_KEYS))}（图表条目另加 chart）；"
                      "写错了就删掉。"
                      "想改构图请改稿件或 SVG 本体，images.json 只管映射与播放属性",
                      file=sys.stderr)
            if media.get("type") == "chart":
                normalized[sid] = {"src": "", "media_type": "chart",
                                   "opts": {k: v for k, v in media.items() if k != "type"}}
                continue
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


def _build_subtitle_cues(sentences, aspect):
    """Build the JS subtitle cue payload from sentence timing and text."""
    sub_cues = []

    def _js(value):
        return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

    sub_params = subtitle_params_for(aspect)
    max_chars = sub_params["max_chars"]
    hard_cap = sub_params["hard_cap"]
    cue_max_lines = sub_params["cue_max_lines"]
    pending = []
    for sent in sentences:
        groups = split_subtitle_cues(sent["text"], max_chars=max_chars,
                                     hard_cap=hard_cap,
                                     cue_max_lines=cue_max_lines)
        total_chars = sum(len("".join(g)) for g in groups)
        t0 = sent["start_time"]
        for group in groups:
            js_lines = "[" + ",".join(_js(line) for line in group) + "]"
            frac = (sum(len(line) for line in group) / total_chars) if total_chars else 1.0
            duration = sent["duration"] * frac
            pending.append((t0, t0 + duration, sent.get("index", -1), js_lines))
            t0 += duration
    # 时长一律由"下一个 cue 的起点"倒推：t 与 d 各自独立四舍五入到 0.01s 时，
    # 相邻 cue 之间会留下至多 10ms 的空洞，24fps 下恰好吞掉一帧——那一帧落在
    # 空洞里就沿用上一句的高亮，句内换行看起来慢了一帧。
    for i, (t_start, t_end, si, js_lines) in enumerate(pending):
        t_emit = round(t_start, 2)
        nxt_emit = round(t_end if i + 1 >= len(pending) else pending[i + 1][0], 2)
        sub_cues.append(f'{{t:{t_emit:.2f},d:{max(0.01, nxt_emit - t_emit):.2f},'
                        f'si:{si},lines:{js_lines}}}')
    return ",\n    ".join(sub_cues)


# vendor 资源的默认相对路径。收口成常量：调用方（gen_hyperframes）在没有
# 图表段落时会显式传 chartjs_src=None，直接覆盖掉签名默认值，而下方
# gsap_src/chartjs_src 又要无条件 .replace() 做属性转义——None 会让每次
# 生成 HTML 崩在 AttributeError。默认值只有一处，调用方传 None 也安全。
_DEFAULT_GSAP_SRC = "vendor/gsap.min.js"
_DEFAULT_CHARTJS_SRC = "vendor/chart.umd.min.js"


def chart_palette_for_theme(theme):
    """按主题派生图表配色（刻度/图例/标题文字色 + 网格线色 + 多系列色板）。

    图表渲染在 seg-image 槽位里，槽位背景跟随主题明暗，因此图表文字必须
    跟着翻转：深底用浅字、浅底用深字。这里以主题主文字色为准（_theme._THEMES
    是唯一权威），网格线用同色低透明度，保证在任意主题下刻度都清晰可读。

    series 是多系列/多扇区色板：默认 accent 打头、接 _accent_palette 轮询，
    pie 按扇区、curve 按数据集轮询取色——旧实现只有 accent+主题辅色两色，
    三个以上扇区的饼图第二块起全是同一个颜色，占比无法分辨。
    """
    colors = get_theme_colors(theme)
    text = colors["text_color"]
    mono = load_template()["typography"]["monoStack"]
    series = [DEFAULT_ACCENT] + get_accent_palette()
    # 主文字色可能是 #rgb/#rrggbb/#rrggbbaa 或 rgb()/rgba()，统一解析出
    # RGB 三元组用于生成网格 rgba；解析失败就退回纯色 + 固定透明度。
    rgb = _parse_rgb(text)
    if rgb is None:
        return {"text": text, "grid_rgba": _with_alpha(text, 0.12),
                "mono": mono, "series": series}
    r, g, b = rgb
    return {
        "text": f"#{r:02x}{g:02x}{b:02x}",
        "grid_rgba": f"rgba({r},{g},{b},.12)",
        "mono": mono,
        "series": series,
    }


def _parse_rgb(value):
    """把 #rgb / #rrggbb / rgb() / rgba() 解析成 (r, g, b)，失败返回 None。"""
    s = str(value).strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) >= 6:
            try:
                return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            except ValueError:
                return None
        return None
    m = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", s)
    if m:
        return tuple(int(float(x)) for x in m.groups())
    return None


def _with_alpha(value, alpha):
    """给颜色加透明度，解析不出 RGB 时原样返回（不静默篡改颜色）。"""
    rgb = _parse_rgb(value)
    if rgb is None:
        return value
    r, g, b = rgb
    return f"rgba({r},{g},{b},{alpha})"


def _build_chart_boot(normalized_images, chart_palette):
    """生成 Chart.js 引导脚本（chart spec 内联 + 配置函数 + load 钩子）。

    从 generate_html 抽出的纯函数：输入只有已归一化的 images 映射与一组
    配色，输出是可直接内联进 <script> 的 JS 文本，不读任何外层状态。抽
    出来是因为这段逻辑与渲染流程正交（图表渲染是浏览器端的事，Python
    侧只负责把 spec 序列化进去），且正确性完全由输入决定、易于单测。

    输入契约：必须传 _normalize_images 归一化"之后"的结构（该函数幂等，
    重复归一化无害）。chart 条目缺 chart 字段仍属坏数据，这里显式报错而非
    静默产出空脚本——渲染成空白图表比立刻失败难排查得多。

    chart_palette：图表文字/网格配色 + 多系列色板 series。图表画在 seg-image
    槽位里，槽位底色跟随主题（cream 是近白、dark 是深底），所以配色必须由调用方从
    主题派生后传入，缺字段直接报错——把任一档颜色写死在这里，另一个主题
    下坐标轴刻度就会几乎不可见。
    """
    if not isinstance(chart_palette, dict) or not all(k in chart_palette for k in ("text", "grid_rgba", "mono", "series")):
        raise ValueError("[renderer] chart_palette 必须由当前主题显式提供 text/grid_rgba/mono/series")
    palette = chart_palette
    c_text = palette["text"]
    # 图例/标题/提示框文字色与刻度同色；网格用低透明度版本
    c_grid_rgba = palette["grid_rgba"]
    chart_specs = []
    for sid, media in normalized_images.items():
        if media.get("media_type") != "chart":
            continue
        opts = media.get("opts") or {}
        if "chart" not in opts:
            raise ValueError(
                f"配图 {sid!r} 的 media_type 是 chart，但 opts 里没有 chart 字段。"
                "这通常意味着传进来的 images 是原始结构而非 _normalize_images 的"
                "输出，或该结构被二次归一化过。请只归一化一次再传入。"
            )
        chart = opts.get("chart") or {}
        if chart.get("type") == "formula":
            continue
        if not chart:
            raise ValueError(
                f"配图 {sid!r} 的 chart spec 为空（None/{{}}），无法渲染。"
                "请检查 images.json 中该条目的 chart 定义是否完整。"
            )
        chart_specs.append((sid, chart))
    lines = ["const ctvCharts = {};"]
    for sid, chart in chart_specs:
        payload = json.dumps(chart, ensure_ascii=False).replace("</", "<\\/")
        # key 与 payload 同一防护：json.dumps 不转义 `/`，段 id 若含
        # `</script>` 会截断内联 <script> 块。
        sid_js = json.dumps(sid, ensure_ascii=False).replace("</", "<\\/")
        lines.append(f"ctvCharts[{sid_js}] = {payload};")
    if chart_specs:
        lines.extend([
            "const ctvInk = " + json.dumps(
                {"text": c_text, "gridRgba": c_grid_rgba,
                 "mono": palette["mono"], "series": palette["series"]},
                ensure_ascii=False) + ";",
            "function ctvChartConfig(spec, accent) {",
            "  const kind = spec.type === 'curve' ? 'line' : spec.type;",
            # 图例只在多数据集时才出现。单序列图（bar/line 的常见形态）里
            # 图例必然与标题同文，只是多一块小字噪音，且在 4:3 槽位里挤压
            # 绘图区高度。例外：pie 的图例是"哪块是哪项"的唯一线索，必须留。
            "  const seriesCount = (spec.curve_datasets||[]).length || 1;",
            "  const showLegend = seriesCount > 1 || spec.type === 'pie';",
            # 图表视觉参数由 _template 内联的 chart 块统一提供；renderer
            # 只负责把配置编译成 Chart.js 选项，不再暗藏第二套视觉常量。
            "  const CTV_CHART = " + json.dumps(_CHART_CFG, ensure_ascii=False) + ";",
            "  const seriesColor = (seg) => ctvInk.series[seg % ctvInk.series.length];",
            "  const F_TITLE = CTV_CHART.titleFontSize, F_LEGEND = CTV_CHART.legendFontSize, F_TICK = CTV_CHART.tickFontSize, F_AXTITLE = CTV_CHART.axisTitleFontSize;",
            "  const AXPAD = {x:{title:{padding:{top:F_AXTITLE*CTV_CHART.axisTitlePaddingFactor}}}, y:{title:{padding:{bottom:F_AXTITLE*CTV_CHART.axisTitlePaddingFactor}}}};",
            "  const options = {responsive:true, maintainAspectRatio:false, animation:false, layout:{padding:CTV_CHART.layoutPadding}, plugins:{legend:{display:showLegend,labels:{color:ctvInk.text,font:{size:F_LEGEND,weight:'600'}}}, title:{display:!!spec.title,text:spec.title||'',color:ctvInk.text,font:{size:F_TITLE,weight:'700'},padding:{top:6,bottom:18}}, tooltip:{enabled:true,titleFont:{size:F_LEGEND},bodyFont:{size:F_TICK}}}, scales:{x:{ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.x_label,text:spec.x_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.x.title.padding}},y:{ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.y_label,text:spec.y_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.y.title.padding}}}};",
            "  if (kind === 'pie') delete options.scales;",
            "  let data;",
            "  if (spec.type === 'curve') { data={datasets:(spec.curve_datasets||[]).map((d,i)=>({label:d.label,data:d.data,parsing:false,borderColor:seriesColor(i),backgroundColor:'transparent',pointRadius:CTV_CHART.curvePointRadius,borderWidth:CTV_CHART.curveBorderWidth,tension:CTV_CHART.curveTension,spanGaps:true}))}; options.scales={x:{type:'linear',ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.x_label,text:spec.x_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.x.title.padding}},y:{ticks:{color:ctvInk.text,font:{size:F_TICK,weight:'600',family:ctvInk.mono}},grid:{color:ctvInk.gridRgba},title:{display:!!spec.y_label,text:spec.y_label||'',color:ctvInk.text,font:{size:F_AXTITLE,weight:'600'},padding:AXPAD.y.title.padding}}}; }",
            "  else if (spec.type === 'scatter') data={datasets:[{label:spec.title||'',data:(spec.points||spec.values||[]).map((v,i)=>Array.isArray(v)?{x:v[0],y:v[1]}:{x:i,y:v}),backgroundColor:accent,borderColor:accent,pointRadius:CTV_CHART.scatterPointRadius}]};",
            "  else data={labels:spec.labels||[],datasets:[{label:spec.title||'',data:spec.values||[],backgroundColor:spec.type==='pie'?(spec.labels||[]).map((_,i)=>seriesColor(i)):accent,borderColor:spec.type==='pie'?ctvInk.text:accent,borderWidth:CTV_CHART.datasetBorderWidth,fill:false,tension:CTV_CHART.datasetTension}]};",
            "  return {type:kind,data,options};",
            "}",
            "function ctvInitCharts(){ if(!window.Chart) return; for(const [sid,spec] of Object.entries(ctvCharts)){ const canvas=document.getElementById('chart-'+sid); if(!canvas) continue; const host=document.getElementById(sid); const accent=host?.dataset?.accent||" + json.dumps(DEFAULT_ACCENT) + "; new Chart(canvas.getContext('2d'),ctvChartConfig(spec,accent)); } }",
            "window.addEventListener('load',ctvInitCharts);",
        ])
    # 模板装配：静态骨架在 templates/chart-boot.js。lines[0] 是模板里已有的
    # "const ctvCharts = {};" 声明，跳过；其后依次是 spec 数据行（len 条）
    # 与引导函数体（无图表时为空）。必须走 _fill 的单遍替换：链式
    # str.replace 会让第二次替换读到第一次注入的 spec——稿件标题里原样
    # 写着 "__CTV_CHART_BODY__" 就能把引导代码拼进 JSON 字符串字面量，
    # 整段内联脚本 SyntaxError 且构建期零报错（与本文件 _fill 的注释同理）。
    return _fill(_load_asset("chart-boot.js"), {
        "__CTV_CHART_SPECS__": chr(10).join(lines[1:1 + len(chart_specs)]),
        "__CTV_CHART_BODY__": chr(10).join(lines[1 + len(chart_specs):]),
    }, "chart-boot.js")


def _build_subtitle_layer():
    """生成字幕/内容呈现层的 CSS 与 JS（verse 唯一形态，两画幅共用）。

    静态骨架在 templates/subtitle-verse.{css,js}：结构与选择器写死，
    数值走 var(--ctv-*)（由 generate_html 注入 :root）。
    不可信内容的边界在两处生成期转义，运行时不参与文案：verse 行由
    generate_html 用 esc() 写成静态 DOM，cue 数组由 _build_subtitle_cues 用
    json.dumps() 序列化（JS 字符串上下文里 HTML 实体转义反而会显示成字面量）。
    运行时只做 class 切换与 color 赋值，不再写任何文本。
    """
    return _load_asset("subtitle-verse.css"), _load_asset("subtitle-verse.js")


def generate_html(manifest, audio_src, images=None,
                  width=None, height=None,
                  gsap_src=_DEFAULT_GSAP_SRC,
                  chartjs_src=_DEFAULT_CHARTJS_SRC,
                  aspect="portrait", theme="dark", fps=24):
    """Generate complete Hyperframes HTML composition string.

    Args:
        manifest: dict with sentences[], total_duration, optional segments[]
        audio_src: path to audio file (relative to HTML output dir)
        images: optional dict mapping segment ID -> media object.
            Single object format: {"src": "images/seg1.mp4", "type": "video",
                                   "loop": true, "muted": true, "autoplay": true,
                                   "poster": "images/seg1-poster.png"}
            "type" defaults to "auto" (infer from file extension); static
            images only need "src". A bare string path is rejected upstream by
            `_contracts.validate_images_json`.
        width, height: composition dimensions
        gsap_src: GSAP script path. 默认使用 composition 项目内的
            `vendor/gsap.min.js`；不自动访问 CDN。
        aspect: 画幅比例 portrait/vertical（3:4，1080x1440）或 landscape
            （16:9，1920x1080）；data-aspect 相应写 "vertical"/"landscape"，
            归一化在函数体内完成，两套 CSS 规则按 data-aspect 命中。
        theme: 主题配色（可选值以 `_theme.py` 内嵌的主题注册表为唯一权威来源，
            当前 cream/dark），影响背景渐变、
            网格、正文文字与配图底板色，不影响每段 accent 彩色。
        fps: 输出帧率提示（写入 data-fps，渲染命令可用 --fps 覆盖；
            默认 24 比 30 少抓 20% 帧、渲染更快）。

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
    # 后续渲染阶段（含下面的 has_chart 判定）都复用这份结果，同一输入
    # 不经过第二次归一化。
    images = _normalize_images(images or {})
    total_dur = manifest["total_duration"]
    sentences = manifest["sentences"]
    # <script src> 属性上下文转义：CLI 传的本地路径可能含 & 或引号，裸插
    # 会破坏 head 结构（媒体路径都走了 quote/转义；这里不能 URL 编码——
    # CDN 地址的 :/? 会被 quote 破坏）
    # None 安全：调用方可能显式传 None（见上方常量注释），回落默认路径。
    gsap_src_attr = (gsap_src or _DEFAULT_GSAP_SRC).replace("&", "&amp;").replace('"', "&quot;")
    chartjs_src_attr = (chartjs_src or _DEFAULT_CHARTJS_SRC).replace("&", "&amp;").replace('"', "&quot;")
    # 是否需要 Chart.js：判定与 gen_hyperframes 装 vendor 时共用 _contracts 那一份
    has_chart = any(media_needs_chartjs(v) for v in images.values())

    # 主题配色（背景/网格/文字/配图底板），accent 色不受主题影响
    theme_colors = get_theme_colors(theme)
    # 按主题主文字色亮度判深浅底（_theme._THEMES 是唯一权威，
    # 新增主题无需改这里的枚举）——tagline 的"同色相只调明度"在深色
    # 底下方向要反过来（提亮而不是压暗）。
    _theme_lum = relative_luminance(theme_colors["text_color"])
    _dark_theme = _theme_lum is not None and _theme_lum > 0.5
    # 背景渐变的十六进制色标集合：accent 派生文字色的对比度保底按其中最坏
    # 一档判定（theme_bg_stops 只解析自家 _THEMES 的渐变串）。
    _bgs = theme_bg_stops(theme)

    # 从模板加载布局/动画/字体参数
    tpl_layout = tpl["layout"][aspect]
    tpl_v = tpl["layout"]["vertical"]
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
        # 竖屏（3:4）：只有 verse 一种字幕形态，无模式分支。全部参数读模板
        # vertical 块（segCard.padding / image.width/height /
        # verse.windowHeight / verse.clipPad），改排版只动 _template.py 内联版式数据。
        _v_pad = tpl_layout["segCard"]["padding"]
        # 卡片左内边距（CSS padding 简写：3 值=上/左右/下，2 值=上下/左右，1 值=四边）。
        # 卡片内容盒由它内缩，标题与画布水平方向对齐。
        _v_pad_parts = _v_pad.split()
        if not _v_pad_parts:
            raise ValueError("[template] layout.vertical.segCard.padding 不能为空")
        try:
            if len(_v_pad_parts) >= 3:
                _v_pad_left = float(_v_pad_parts[1].replace("px", ""))
            elif len(_v_pad_parts) == 2:
                _v_pad_left = float(_v_pad_parts[1].replace("px", ""))
            else:
                _v_pad_left = float(_v_pad_parts[0].replace("px", ""))
        except (ValueError, IndexError) as e:
            raise ValueError("[template] layout.vertical.segCard.padding 必须是合法 CSS 长度") from e
        _v_verse_h = _vv["windowHeight"]
        _v_verse_bottom = _vv["bottom"]
        # 竖屏画布圆角（读模板 vertical.image.borderRadius）
        _v_img_radius = _il["borderRadius"]
        # 竖屏画布左右外扩边距（px）：画布相对"卡片内容盒"左右各外扩
        # marginSide（0 = 与内容盒同宽）。注意这不是"距屏幕边距"——卡片
        # segCard.padding 的左右值（当前 50px）才是槽到屏幕边的距离，
        # marginSide 是在此基础上再往外推多少。想让槽距屏幕 50px，保持
        # marginSide=0 即可（内容盒本身已内缩 50px）。
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

    # ══════════════════════════════════════════════════════════════════
    # 分区索引（本函数 600+ 行，改代码前先定位分区，避免整段通读）：
    #   1/6 参数归一化与模板装载（函数开头 ~ 配图归一化）
    #   2/6 竖屏几何派生（_v_pad_* / _v_img_*）
    #   3/6 字幕形态 CSS+JS（verse 句子流，两画幅共用）← 本区，模块级 _build_subtitle_layer
    #   4/6 段落卡片 HTML + GSAP 时间线
    #   5/6 图表引导脚本（模块级 _build_chart_boot）
    #   6/6 装配最终 HTML
    # ══════════════════════════════════════════════════════════════════
    _sub_css, _sub_js = _build_subtitle_layer()

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
    # C2 公式文本卡（chart type=formula）的版式参数：全部来自模板顶层
    # formulaCard（唯一来源在模板，这里不留字面量兜底）；字段缺失时严格失败。
    _fc = tpl["formulaCard"]
    css_fc_pad = _fc["padding"]
    css_fc_title_size = _fc["titleSize"]
    css_fc_title_opacity = _fc["titleOpacity"]
    css_fc_title_mb = _fc["titleMarginBottom"]
    css_fc_value_size = _fc["valueSize"]
    # 字体由模板统一定义，renderer 不再保留重复字面量。
    css_font_family = tpl_typo["fontFamily"]
    css_mono_family = tpl_typo["monoStack"]
    css_tagline_mt = _tgl["marginTop"]

    # ── :root 变量表：版式的唯一数值出口 ──────────────────────────────────
    # templates/*.css 里不出现硬编码尺寸，一律走 var(--ctv-*)。本表从
    # _template 内联的版式数据派生（含标题/画布等推导值）。模板是版式
    # 唯一真源，改版式只改模板；渲染层只负责派生 CSS 变量。
    # 命名空间约定：无后缀 = 两画幅同名派生（值各取各块）；--ctv-v-* 仅
    # 竖屏、--ctv-l-* 仅横屏；--ctv-ag-* agenda 变量两画幅同名，值取自
    # 各自 layout 块的 agenda（开屏/结尾纯文字版式共用同一套结构）。
    root_shared = f"""  --ctv-w:{width}px;--ctv-h:{height}px;
  --ctv-bg-gradient:{theme_colors["bg_gradient"]};
  --ctv-font-family:{css_font_family};
  --ctv-font-mono:{css_mono_family};
  --ctv-grid-size:{css_grid_size}px;--ctv-grid-color:{theme_colors["grid_color"]};
  --ctv-text-color:{theme_colors["text_color"]};
  --ctv-media-bg:{theme_colors["media_bg"]};
  --ctv-title-weight:{css_title_weight};
  --ctv-title-lh:{css_title_lh};--ctv-title-tracking:{css_title_tracking};
  --ctv-tagline-font:{css_tagline_font};--ctv-tagline-mt:{css_tagline_mt}px;
  --ctv-tagline-lh:{css_tagline_lh};--ctv-tagline-weight:{css_tagline_weight};
  --ctv-prog-height:{css_prog_height}px;
  --ctv-fc-pad:{css_fc_pad}px;--ctv-fc-title-size:{css_fc_title_size}px;
  --ctv-fc-title-opacity:{css_fc_title_opacity};
  --ctv-fc-title-mb:{css_fc_title_mb}px;--ctv-fc-value-size:{css_fc_value_size}px;
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
        # 这个盒是 overflow:hidden 的绝对定位盒，画布与句子流都不为它让位
        #（竖屏三区各自固定定位，见 SKILL.md「单一版式」），所以算小了就是把
        # tagline 静默切掉一截、算大了就是往画布上压——两头都不报错，只能靠
        # 人工预览发现。按模板自身的字号/行数/行高派生，改版式仍只改 _template.py。
        _v_title_area = (_tl["maxLines"] * _tl["fontSize"] * css_title_lh
                         + tpl_v["tagline"]["marginTop"]
                         + tpl_v["tagline"]["fontSize"] * tpl_v["tagline"]["lineHeight"])
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
  --ctv-v-tagline-mt:{tpl_v["tagline"]["marginTop"]}px;
  --ctv-v-tagline-indent:{tpl_v["tagline"]["indent"]}px;
  --ctv-v-img-w:{int(_il["width"])}px;--ctv-v-img-ms:{_v_img_ms}px;
  --ctv-v-img-top:{_v_img_top}px;--ctv-v-img-height:{int(_il["height"])}px;--ctv-v-img-radius:{_v_img_radius}px;
  --ctv-v-verse-bottom:{_v_verse_bottom}px;
  --ctv-v-tagline-tick:{tpl_v["tagline"]["tickWidth"]}px;"""
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

    # Auto-group if no segments provided.
    segments = manifest_segments(manifest)

    # Calculate clip timing
    clips = []
    for seg in segments:
        seg_sents = seg["sentences"]
        if not seg_sents:
            continue
        start = seg_sents[0]["start_time"]
        end = seg_sents[-1]["start_time"] + seg_sents[-1]["duration"]
        clips.append({
            "seg": seg,
            "start": round(start, 2),
            "duration": round(end - start, 2),
        })

    # ── 分区 4/6：段落卡片 HTML + GSAP 时间线 ──────────────────────
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
                f"（字母开头，仅字母/数字/-/_，1–64 字符；实际: {sid!r}）")
        s = clip["start"]
        d = clip["duration"]
        # normalize_accent：accent 统一归一化成 6 位 hex——alpha 后缀
        # （{ac}40/{ac}15）只有拼在 #rrggbb 后才合法，3 位 hex（#fff）或
        # CSS 色名（red）拼出的非法值会被浏览器整条声明静默丢弃。
        # accent 来自稿件（信源内容经模型写入），属不可信数据，要拼进
        # data-accent / style / GSAP 三处上下文——解析不了的一律回落默认色，
        # 绝不让引号/分号/括号进入 HTML。
        ac = normalize_accent(seg.get("accent", DEFAULT_ACCENT), DEFAULT_ACCENT)
        # HTML 属性上下文用 esc 后的副本（纵深防御：即使将来白名单放宽，
        # 属性闭合仍不可能）。GSAP 的 backgroundColor:"{ac}" 是 JS 字符串
        # 字面量，走 json.dumps 语义、不能用 esc（会渲染出字面量 &quot;）。
        ac_attr = esc(ac)
        # 文本安全 accent：cream 浅底上原色 accent（中亮色色板）做正文色对比度
        # 不足、字面发糊，与 tagline 同法压暗（同色相只降明度）；dark 底从原色
        # 起步。两条路径最后都过 ensure_text_contrast 保底到 check 门禁的最严
        # 一档——色板里的黄/天蓝在 cream 上 darken 一档照样不到 4.5:1。
        # 只喂给"写在底上的字"（活动句着色 / .ag-idx），装饰仍走
        # 原色 --seg-accent。
        ac_text = ensure_text_contrast(ac if _dark_theme else darken(ac), _bgs)
        ac_text_attr = esc(ac_text)
        # 开屏/结尾 = 纯文字 agenda（两画幅统一）：不配图，用章节罗列/
        # 要点总结的文字内容填充。gen_hyperframes 已把 opening/closing 的
        # images 键弹出，库调用方仍带映射时这里也强制忽略。
        is_agenda = sid in ("opening", "closing")
        has_image = (sid in images) and not is_agenda

        # 动画参数快捷引用（从模板加载，替代硬编码数值）
        a_ = tpl_anim

        # 标题字号（内联，唯一不被 CSS 覆盖的标题数值）：
        # - agenda 页取各画幅 agenda 标题字号（竖 84 / 横 96），带长度守卫：
        #   agenda 头豁免了行数钳制（见 composition.css），长标题会无限折行
        #   挤爆定高列，故按 CJK 字宽 ≈ 字号 估算，压到目标行数内放得下为止。
        #   横屏 7 行上限吃满列预算，标题锁 1 行、不为第二行预留空间；竖屏
        #   留有余量，仍允许压进 2 行；
        # - 横屏内容段有长度守卫：超阈值先降字号（62→54→48），配合
        #   text-wrap:balance 折行，避免长标题把左栏撑出孤字；
        # - 竖屏内容段一律取模板 fontSize。
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
            # 深色主题向白提亮（无差别 _darken 在 dark 下
            # 对比度只有 ~3.3，不达 WCAG AA）。浅色主题压暗一档后仍由
            # ensure_text_contrast 兜到门禁最严档（中亮度 accent 压一档
            # 照样不够）。缩进与对齐由 CSS
            # 负责（--ctv-v-tagline-indent；agenda 卡两画幅居左）。
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
            if media_type == "chart":
                chart = media_opts.get("chart") or {}
                chart_type = str(chart.get("type") or "").lower()
                if chart_type == "formula":
                    formula = esc(str(chart.get("formula") or ""))
                    title = esc(str(chart.get("title") or ""))
                    # 媒体卡的淡染面板/描边由 composition.css 从 --seg-accent
                    # （注入在 seg-card 上）经 color-mix 派生，这里不再逐分支传色。
                    image_html = (f'\n    <div class="seg-image chart-media" id="img-{sid}" style="box-shadow:0 0 {_il["glow"]}px {ac_attr}40">'
                                  f'<div class="chart-formula"><div class="chart-title">{title}</div><div class="formula-value">{formula}</div></div></div>')
                else:
                    image_html = (f'\n    <div class="seg-image chart-media" id="img-{sid}" style="box-shadow:0 0 {_il["glow"]}px {ac_attr}40">'
                                  f'<canvas id="chart-{sid}"></canvas></div>')
            elif media_type == "video":
                # 视频配图：<video> 自动循环静音播放
                loop = "loop" if media_opts.get("loop", True) else ""
                muted = "muted" if media_opts.get("muted", True) else ""
                playsinline = "playsinline" if media_opts.get("playsinline", True) else ""
                # autoplay 同样读 images.json 选项（与其余 video 选项一致）
                autoplay = "autoplay" if media_opts.get("autoplay", True) else ""
                poster = media_opts.get("poster", "")
                poster_attr = f'poster="{quote(poster)}"' if poster else ""
                image_html = (
                    f'\n    <div class="seg-image" id="img-{sid}" '
                    f'style="box-shadow:0 0 {_il["glow"]}px {ac_attr}40">\n'
                    f'      <video id="vid-{sid}" src="{quote(media_path)}" '
                    f'data-start="{s}" data-duration="{d}" '
                    f'{loop} {muted} {autoplay} {playsinline} '
                    f'{poster_attr} '
                    f'style="width:100%;height:100%;object-fit:cover">\n'
                    f'      </video>\n'
                    f'    </div>'
                )
            else:
                # 静态图 / 动图：<img> 不变
                image_html = (
                    f'\n    <div class="seg-image" id="img-{sid}" '
                    f'style="box-shadow:0 0 {_il["glow"]}px {ac_attr}40">\n'
                    f'      <img src="{quote(media_path)}" alt="">\n'
                    f'    </div>'
                )

        # 竖屏标题区内联样式已收进 CSS 骨架（composition.css vertical 覆盖块），
        # 这里只保留随段数据变化的 font-size 与 accent 光晕 text-shadow。
        # 字幕 DOM 只有 verse（歌词式句子流）一种形态、两画幅共用：该段全部
        # 句子按序渲染成静态行（完整句子，CSS 自动换行），运行时由 cue 的 si
        # 高亮当前句、已播句淡出、窗口随播报滚动。所有文字由句子流逐句
        # 呈现；DOM 渲染在 seg-card 尾部，竖屏绝对定位钉底、横屏在左文字栏
        # 文档流里随内容垂直居中（见 subtitle-verse.css 头部注释）。
        _vlines = [
            # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
            # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
            # 永久失灵或错行——宁可都不高亮，也不错误高亮
            f'<div class="verse-line" data-i="{_s2.get("index", -1)}">'
            f'{esc(_s2["text"])}</div>'
            for _s2 in seg["sentences"]
        ]
        verse_html = (
            # 三个 layout 豁免属性都源于同一误报机制：滚动出窗的
            # 行视觉上被窗口 overflow:hidden 裁掉，但静态 DOM rect
            # 仍在原位——越过窗口上缘与标题区相交（content_overlap，
            # allow-overlap）、越过窗口下缘与底部元素（如进度条）
            # 相交（text_occluded，allow-occlusion，portrait 底部
            # 留白只有 80px 时会触发）、整体越出卡片（allow-
            # overflow）。活动行锚定在窗口内 clipPad 处，真实重叠不可能
            # 发生。
            f'\n    <div class="verse" id="verse-{sid}" '
            f'data-layout-allow-overflow data-layout-allow-overlap '
            f'data-layout-allow-occlusion>'
            f'<div class="verse-clip" data-accent="{ac_text_attr}">'
            f'{"".join(_vlines)}</div></div>'
        )
        _cls_extra = " agenda-card" if is_agenda else ""
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
                                   ac, ac_attr, title_size, esc, _bgs)
                + f'    {verse_html}\n    </div>\n'
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
        # Fade out + hard kill. 段落之间天然隔着一句静音（gap = 下一段 start −
        # 本段 end）：淡出只按配置时长（0.3s）从本段结束起算时，默认 gap（0.4s）
        # 就更长，上一段已经淡干净、下一段还没开始淡入，边界上留下只剩背景的
        # 空帧（24fps 实测 2~3 帧）。淡出因此跨过整段间隔、铺到下一段淡入结束，
        # 两张卡真正交叠成文档承诺的 cross-fade。
        # （试过"静音期保持全显、再与淡入对称同步淡出"的写法：gap 一大，交接点
        # 仍露出约 0.15s 的双低透明空档，实测 6 帧空白，故弃用。）
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
        # Title entrance
        a_title = a_["titleEntrance"]
        gsap_lines.append(
            f'tl.from("#title-{sid}",{{scale:{a_title["from"]},'
            f'duration:{a_title["duration"] * _k:.2f},'
            f'ease:"{a_title["ease"]}"}},{s:.2f})'
        )
        if has_image:
            a_img = a_["imageEntrance"]
            # 配图卡（两画幅）从下方滑入（y）。agenda 卡无配图，不入场。
            _img_ent = f'y:{a_img["vert_y"]}'
            gsap_lines.append(
                f'tl.from("#img-{sid}",{{opacity:0,'
                f'{_img_ent},'
                f'duration:{a_img["duration"] * _k:.2f},'
                f'ease:"{a_img["ease"]}"}},'
                f'{s + a_img["startDelay"] * _k:.2f})'
            )
        gsap_lines.append(
            f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
        )

    # ── Subtitle cues ──────────────────────────────────────────────
    sub_cues_js = _build_subtitle_cues(sentences, aspect)

    gsap_code = "\n  ".join(gsap_lines)

    # ── 分区 5/6：图表引导脚本（实现见模块级 _build_chart_boot） ──
    # images 在此处已是归一化后的结构（_build_chart_boot 要求该输入契约）。
    chart_boot = _build_chart_boot(images, chart_palette_for_theme(theme))

    # ── 分区 6/6：装配最终 HTML ────────────────────────────────────
    # 骨架在 templates/composition.html。样式与脚本各自装配好后填入占位符；
    # CSS 走 <style> 内联（无头浏览器首帧不能等外链 CSS，否则白屏错版），
    # GSAP/chart.js 是脚本、可以外链（vendor/ 相对路径）。
    style = _fill(_load_asset("composition.css"), {
        "__CTV_SUBTITLE_CSS__": _sub_css,
        "__CTV_ROOT_VARS__": root_vars,
    }, "composition.css")
    script = _fill(_load_asset("runtime.js"), {
        "__CTV_CHART_BOOT__": chart_boot,
        "__CTV_GSAP__": gsap_code,
        "__CTV_CUES__": sub_cues_js,
        "__CTV_VERSE_CLIP__": f"{_v_verse_clip:g}",
        "__CTV_SUBTITLE_JS__": _sub_js,
    }, "runtime.js")
    chartjs_tag = (f'<script src="{chartjs_src_attr}"></script>') if has_chart else ''
    html = _fill(_load_asset("composition.html"), {
        "__CTV_GSAP_SRC__": gsap_src_attr,
        "__CTV_CHARTJS_TAG__": chartjs_tag,
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

