#!/usr/bin/env python3
"""Composition HTML compiler for Content-to-Video.

This module owns the presentation compiler: normalized composition inputs, HTML/CSS
and GSAP timeline generation. CLI orchestration, file validation and rendering
remain outside this module.
"""
import html
import json
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from _script_utils import num_fmt, seeded_wobble  # noqa: E402  数值字面形态的单一实现
from _theme import (
    get_theme_colors, get_default_accent, mix, normalize_accent,
    theme_bg_stops, ensure_text_contrast, DEFAULT_THEME,
)
from _template import (MIN_READABLE_FONT_PX, load_template, get_canvas,
                       get_image_box, normalize_aspect, seg_card_pad_x)
from _images_schema import (classify_media_path, is_svg_path,  # noqa: E402
                            MEDIA_DUCK_VOLUME)
from _segments import (is_content_sid, seg_layout)
from _path_morph import make_morph, interp as _morph_interp
from _ease import curve as ease_curve
from _timeline import DEFAULT_FPS, beat_positions, beat_span, beat_cycles
from _cam_crop import cam_default_origin
from _stage_carry import infer_stage_keep


DEFAULT_ACCENT = get_default_accent()

# 合成 ID：根元素 data-composition-id 与 runtime 注册时间线用的键**同一个值**
# （hyperframes 契约要求两者一致）。它是本项目唯一的合成 ID 真源，经
# __CTV_COMPOSITION_ID__ 注进 composition.html 与 runtime.js；templates/preview.js
# 不走占位符装配，改为从根元素的 data-composition-id 现读。三处曾经各自硬编码
# "main"——改一处漏两处时，注册键与根 ID 不匹配：只有一条时间线时 runtime 仍能
# 兜住绑定，两条以上则渲染**静默冻结在 t=0**（无报错）。
COMPOSITION_ID = "main"

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
    halo_html = ('    <div class="apple-halo" id="halo-%s"></div>\n'
                 '    <div class="apple-glow" id="glow-%s"></div>\n' % (sid, sid)
                 if apple_opening else "")
    col_attr = (' data-opening-anim="apple"' if apple_opening else "")
    # kicker 拆不拆字只看开屏（拆字是字距舞的载体），见 _agenda_kicker_html。
    # 这个函数只为 agenda 卡服务（调用点只有 _assemble_card 的 agenda 分支），
    # 所以"要垫"就是"有这一行"：两种形态都在演字距展开，都缺同一个容差。
    kicker_html = _agenda_kicker_html(seg, bgs, ac, split_chars=apple_opening,
                                      pad_kern=True)
    if kicker_html and not apple_opening:
        # 非开屏的 kicker 挂 id，给 _kicker_entrance_lines 一个落点；开屏那行由
        # 苹果编排按 `.k-ch` 逐字演，不需要 id（也不需要第二条整行补间）。
        kicker_html = kicker_html.replace('<div class="agenda-kicker"',
                                          f'<div class="agenda-kicker" id="kicker-{sid}"',
                                          1)
    rows, tail_rows = _agenda_rows(sid, clips, manifest, ag)
    if card is not None:
        # 截断后的真实行数（正文 + cta 尾行）。苹果开场按它算 stagger 的收尾时刻，
        # 必须是**画在画面上**的行数，不是稿件里想写的行数。
        card.agenda_row_count = len(rows) + len(tail_rows)
    agenda_html = (f'<div class="agenda">{_agenda_row_html(rows)}</div>'
                   if rows else "")
    tail_html = (f'<div class="agenda-tail">{_agenda_row_html(tail_rows)}</div>'
                 if tail_rows else "")
    # 标题：静态开场 = 纯文本；苹果开场 = 逐字拆成 .t-ch span（空格为 .t-sp），
    # GSAP 按字符 stagger 浮出。拆在 esc() 之前：拆的是原文、逐字符转义再包。
    title_html = esc(seg["title"])
    if apple_opening:
        # 逐字拆 span 拆的是**原文**、每个字符各自转义后再包：先 esc 整句再拆
        # 会把 &amp; 拆成五个独立 span，浏览器不再把它当实体解析，标题里的
        # & / < / > 会以字面文本上画面（实测 "Q&A" 渲染成 "Q&amp;A"）。
        title_html = "".join(
            f'<span class="t-ch t-sp">{esc(ch)}</span>' if ch.isspace()
            else f'<span class="t-ch">{esc(ch)}</span>'
            for ch in seg["title"])
    return (f'    <div class="agenda-col"{col_attr}>\n'
            f'{halo_html}'
            f'      <div class="agenda-head">{kicker_html}'
            f'<div class="seg-title" id="title-{sid}" '
            f'style="font-size:{title_size};text-shadow:0 0 {ag["titleGlow"]}px {ac_attr}40">'
            f'{title_html}</div></div>\n'
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
        # 未知键的告警已在 _images_schema.validate_images_json（预检）打过——
        # 本函数契约要求入参必须过那道预检（见 docstring），这里不重复打。
        media_path = media["src"]
        media_type = classify_media_path(media_path, media.get("type", "auto"))
        media_opts = {k: v for k, v in media.items() if k not in ("src", "type")}
        normalized[sid] = {
            "src": media_path,
            "type": media_type,
            "opts": media_opts,
        }
    return normalized


def _subtitle_cue_spans(sentences):
    """每句字幕 cue 的 (起点, 终点, 全局句序)：**唯一口径**，两个消费者共用。

    - `_build_subtitle_cues` 把它序列化成运行时的 cue 数组；
    - `_ved_settle_seconds` 读同一份，算"句尾留白有多长、下一个接手的是不是同一页"。

    两处各算一遍的话，收束会以分钟级的偏差"以为"某句后面还有留白（对着另一份
    起点/终点算），而症状只是"某几句念完之后没收"——没人会去查生成期的两份算术。
    """
    n = len(sentences)

    def _speak_on(sent):
        return (float(sent["start_time"])
                + max(0.0, float(sent.get("onset") or 0.0)))

    spans = []
    for i, sent in enumerate(sentences):
        t = _speak_on(sent)
        end = (_speak_on(sentences[i + 1]) if i + 1 < n
               else float(sent["start_time"]) + float(sent["duration"]))
        spans.append((round(t, 2), round(end, 2), sent.get("index", -1)))
    return spans


def _build_subtitle_cues(sentences):
    """Build the JS cue payload (t/d/si) from sentence timing.

    cue 里只有数字：文案由 verse 静态 DOM 承载（见 esc），运行时按 si 切高亮、
    按 t/d 判定当前句，不需要序列化任何文本。

    时长一律由"下一句的起点"倒推：t 与 d 各自独立四舍五入到 0.01s 时，相邻 cue
    之间会留下至多 10ms 的空洞，24fps 下恰好吞掉一帧——那一帧落在空洞里就沿用
    上一句的高亮，切换看起来慢了一帧。末句没有下一句，按自身实测时长收尾。

    **起点取"开口那一刻"，不是音频块开头**：manifest 的 `onset` 是这句音频开头的
    静默（TTS 的吸气与起音，实测几十到几百毫秒）。以前 cue 从 `start_time` 起，
    上一句念完、下一句还没开口，字幕就已经换了——观众读到的字幕比听到的话早半拍，
    而页末那句还会在"已经没人说话"时亮着。d 仍由相邻起点倒推，所以句间 gap 自动
    变长（gap 的真实含义一直是"两句之间没声音的那段"）。老 manifest 没有 onset 时
    退回 start_time。
    """
    cues = []
    for t, end, si in _subtitle_cue_spans(sentences):
        # dt：本句"读到句尾"的区间（起点、终点）——词级强调按它把句子切成
        # "已念 / 未念"两段。与 d 的终点同一口径（下一句起点），末尾句取自身实测
        # 时长。写成定长小数，避免 json 里出现 3.5999999 这种尾巴。
        cues.append(f'{{t:{t:.2f},d:{max(0.01, end - t):.2f},si:{si},'
                    f'dt:[{t:.2f},{end:.2f}]}}')
    return ",\n    ".join(cues)


def _ved_settle_seconds(sentences, page_last_si):
    """词级强调"念完之后的收束"该演多久（秒）：0 = 不演。

    收束（见 runtime.js 的 verseEmSettle / composition.css 的 .verse-ved-settled）
    是把整句 accent **逐字**交还给"已过去"那一档。它不占独立节拍，只借用**句尾
    留白**——上一句念完、下一句还没开口的那段空档，以前是画面完全静止的空白，现在
    让最后一个动作在里面收尾。

    三条边界都在生成期一次算完（运行时只读一个数）：

    - **只收"下一句会在同一页里接手"的句**：cues 是**每页一份**的（见 generate_html
      的 __CTV_CUES__），所以某一页的最后一句后面跟的是转场——它整句的 accent 会随
      页面被擦走。`page_last_si` 因此显式传进来，不靠 si 连续性去猜。
    - **留白里先扣掉换句接力**：新一句浮起用 `--ctv-relay`（0.32s，composition.css
      / runtime.js 的 RELAY_SECONDS）。收束要在这之前收干净，否则同一行里"旧的还在
      退、新的已经进"两件事叠着演。
    - **上限 SETTLE_MAX_SECONDS、地板两帧**：见那两个常量。
    """
    spans = _subtitle_cue_spans(sentences)
    # 留白 = 下一句开口 − **本句念完**。`spans` 的第二项是"cue 的终点"（= 下一句
    # 起点），不是"这句念完的时刻"——两者只在本句没有尾静默时才重合。所以念完
    # 时刻要单独从 voiced 取，与运行时 verseEmEnd 同一口径（`_speak_on(sent)` 同
    # 一函数，runtime 用 t0 + voiced，这里写成等价的 start + onset + voiced）。
    # 首尾相接的 fixture（每句 start = 上一句 start + duration，无 onset/voiced）
    # 因此得到 0 留白 → 不演，正是期望。
    by_index = {s.get("index", -1): s for s in sentences}
    best = None
    for t, end, si in spans:
        if si in page_last_si:
            continue
        sent = by_index.get(si) or {}
        voiced = float(sent.get("voiced") or 0.0)
        # 没有 voiced（老 cue / 手写 manifest）：运行时退回 t1，念完 = 本 cue 终点，
        # 这里同口径写成 end，于是留白恒为负 → 不演，与改动前逐字节一致。
        done = min(end, t + voiced) if voiced > 0 else end
        hold = end - done - RELAY_SECONDS
        if best is None or hold < best:
            best = hold
    if best is None or best <= 0:
        return 0.0
    # 上限封顶与网格取整：收束是逐字错峰的过渡，落在帧间的那几毫秒不会有人看见，
    # 而整段时长的十进制尾巴（0.58）会一路带进 CSS 变量与文档的示例里。
    # **向下**取整而不是四舍五入：这个数就是"留给收束的窗口"，向上圆会把最后一两
    # 个字挤进下一句已经开口的那一帧——宁可少演 20ms，也不让两句的动作重叠。
    d = math.floor(min(best, SETTLE_MAX_SECONDS) * 20) / 20
    return d if d >= SETTLE_MIN_SECONDS else 0.0


_DEFAULT_GSAP_SRC = "vendor/gsap.min.js"

# Ken Burns 的单轴取景幅度下限：低于这个值观众读不出方向，这一拍就退化成纯推近
# （等于白丢一次"每段各有取景"的机会）。用法见 `_image_drift_lines` 的 `_aim`。
# 不登记进 check_numbers——它不改变作者写法，改了也不需要重算别的数。
AIM_FLOOR = 0.35

# 句尾收束（verse settle）的时长上限（秒）。收束只能演在"这句已经念完、下一句
# 还没开口"的那段留白里（见 `_ved_settle_seconds`）。留白 = 下一句开口 − 本句
# 念完 − 换句接力，与 `--gap` **不是**同一个东西：TTS 的句尾拖音、下一句的起音
# 静默都会参与，长句之后偶尔能到 1s 以上。上限封顶的理由是**别让留白富余变成
# 慢动作**：2 秒的空档里花 2 秒把一句话逐字退回去，观众读到的是"画面卡住了"，
# 而它本该是一个几乎看不见的收尾动作。
SETTLE_MAX_SECONDS = 0.6
# 收束时长落到 0.05s 网格后低于这个值就不演（30fps 下一帧 = 0.033s，不足两帧的
# 回落只会是一下闪烁）。不演 = 变量写 0，CSS 规则里那段过渡自动没有时长、整条
# 失效，成片与改动前逐字节一致。
SETTLE_MIN_SECONDS = 0.08
# 换句接力的入场时长（秒）：新一句浮起、"旧的压到 0.45"的过渡窗。收束必须在这
# 之前把旧句退干净，所以它在算留白时被扣掉。**这是第二份真源**，第一份在
# templates/runtime.js 的 RELAY_SECONDS——test_ved_settle 锁住两者相等。
RELAY_SECONDS = 0.32


# ── 渲染上下文：一次派生，处处共用 ──────────────────────────────────────
# generate_html 只做三件事：归一化入参 → 建上下文 → 逐卡装配。主题、模板
# 几何与 :root 变量表都在 _build_render_context 里派生一次；单卡的归一、
# 装配与动画各自成函数，版式差异只体现在装配一处。


def _build_render_context(tpl, aspect, width, height, theme, images,
                          fps=DEFAULT_FPS):
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
        # 卡片左右内边距（CSS padding 简写取横向值）：卡片内容盒由它内缩，标题
        # 与画布水平方向对齐。解析口径只有 `_template.seg_card_pad_x` 一份——
        # `check_svg` 的安全边距读的是同一个函数，两边不可能各解出两个数。
        _v_pad_left = seg_card_pad_x()
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
    # 句尾收束时长：这里先落 0（= 不演，CSS 规则自动失效），generate_html 的
    # 卡片循环里拿到全片 cue 之后再覆写成真值（那边才有句子表）。
    _settle_now = 0.0
    # 句子流的焦点衰减（runtime.js 的 verseFocusOp 按离焦距离查表）。三个数都是
    # **观感常量**，真源在模板 subtitle.focus：future/past 是"未念/已念"两档的
    # 既有值（composition.css 里的同名兜底字面量与这里必须一致），ratio 是衰减
    # 比——0.45 ≈ 0.62 × 0.726，"已念"本来就是"未念"沿同一条曲线多走的一步，
    # 曲线在那里被砍断了，这里把它接下去。
    _focus = tpl["subtitle"]["focus"]
    _focus_future = float(_focus["future"])
    _focus_past = float(_focus["past"])
    _focus_ratio = float(_focus["ratio"])
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
    # 覆盖层压暗未念段要一个**实色**底：把文本色按 0.62 混到页面主底色上，
    # 得到与 .verse-line base 档同观感的不透明色。底色取渐变第一个色标
    # （面积最大的一段），没有色标时退回纯黑。
    _bg_flat = rc.bgs[0] if rc.bgs else "#000000"
    _kicker_pad = _kicker_kern_pad_px(rc)
    root_shared = f"""  --ctv-w:{width}px;--ctv-h:{height}px;
  --ctv-bg-gradient:{rc.theme_colors["bg_gradient"]};
  --ctv-bg-color:{_bg_flat};
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
  --ctv-verse-pad-right:{_vv["shadePadRight"]};--ctv-verse-shade:{_vv["shade"]};
  --ctv-verse-rule:{_vv["activeRule"]}em;
  --ctv-verse-who-font:{_vv["whoFontSize"]}px;--ctv-verse-who-gap:{_vv["whoGap"]}px;
  --ctv-ag-x:{_ag["insetX"]}px;--ctv-ag-t:{_ag["insetTop"]}px;--ctv-ag-b:{_ag["insetBottom"]}px;
  --ctv-ag-title-lh:{_ag["titleLineHeight"]};--ctv-ag-title-mt:{_ag["titleMarginTop"]}px;
  --ctv-ag-kicker:{_ag["kickerSize"]}px;--ctv-ag-idx:{_ag["idxSize"]}px;
  --ctv-ag-kern-pad:{_kicker_pad:.2f}px;
  --ctv-ag-idx-min:{_ag["idxMinWidth"]}px;--ctv-ag-name:{_ag["nameSize"]}px;
  --ctv-ag-dur:{_ag["durSize"]}px;--ctv-ag-gap:{_ag["rowGap"]}px;
  --ctv-ag-row-pad:{_ag["rowPad"]}px;--ctv-ag-verse-w:{_ag["verseMaxWidth"]}px;--ctv-ag-list-mt:{_ag["listMarginTop"]}px;
  --ctv-amb-rx:{_amb["rx"]}%;--ctv-amb-ry:{_amb["ry"]}%;
  --ctv-amb-cx:{_amb["cx"]}%;--ctv-amb-cy:{_amb["cy"]}%;
  --ctv-amb-alpha:{_amb["alpha"]}%;--ctv-amb-edge:{_amb["edge"]}%;
  --ctv-amb-agenda-cx:{_amb["agendaCx"]}%;--ctv-amb-agenda-cy:{_amb["agendaCy"]}%;
  --ctv-camb-rx:{_camb["rx"]}%;--ctv-camb-ry:{_camb["ry"]}%;
  --ctv-camb-cx:{_camb["cx"]}%;--ctv-camb-cy:{_camb["cy"]}%;
  --ctv-camb-alpha:{_camb["alpha"]}%;--ctv-camb-edge:{_camb["edge"]}%;
  --ctv-ved-settle:{_settle_now:.2f}s;
  --ctv-focus-future:{num_fmt(_focus_future)};--ctv-focus-past:{num_fmt(_focus_past)};--ctv-focus-ratio:{num_fmt(_focus_ratio)};"""
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
            _raw = _ag_avail // -(-_tlen // _ag_lines)
            # 压到可读下限就停：标题长度契约层不限（只禁分号），没有下限时 1900 字的
            # 标题会算出 font-size:0px——成片里这行字整条消失，而 warn 只说"降字号"，
            # 作者看不到降到了多少。下限与 check_svg / 画布页自查同一条（_template）。
            _fit = max(MIN_READABLE_FONT_PX, _raw)
            _tail = (f"——已压到 {MIN_READABLE_FONT_PX}px 可读下限，再长的标题压进 "
                     f"{_ag_lines} 行仍会溢出，请改写成短句"
                     if _fit > _raw else "——建议写稿时压到短句")
            print(f"[warn] agenda 标题长 {_tlen} 字（{sid}，单行容量 "
                  f"{_cap} 字），降字号压进 {_ag_lines} 行：{_title_font_px}→{_fit}px"
                  f"{_tail}", file=sys.stderr)
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

    这个"定点去尾零"的写法与 `_script_utils.num_fmt` 是同一件事：这里只钉住
    6 位档，实现只有那一份。
    """
    return num_fmt(v, 6)


# ── 视频原声的让路 EQ ────────────────────────────────────────────────
# 只压整条轨的音量（volume）是把原声整个拧小：环境声、音乐、现场厚度一起没
# 了，"有原声"这一诉求只剩下一点残余。Hyperframes 的 voiceover carve 走的是
# 另一条路——只在人声占住的那几档频段上开槽，床铺的其余部分保持原样。那是
# Studio 分析语音后落下来的一条 `data-fx-chain`（`data-fx-carve` 本身在播放
# 期不读，见官方 audio-effects 参考），本技能没有那次分析，就给同一类链的**静态
# 保守版**：三段 peaking 窄带衰减，落在人声最占地方的中高频。
# 为什么是这三档：carve 的候选中心是 160~6000 Hz 的 1/3 倍频程，而官方明确写了
# "真正掩蔽人声的频段更高"——按能量排会一直选到基频（160/250 Hz），切那里只是
# 把床铺削薄，对听清旁白几乎没有帮助。所以取 1.6k / 2.5k / 4k，深度 -3/-3/-2 dB、
# Q=1（宽到不留下"被挖了个洞"的听觉痕迹）。
_DUCK_BANDS = ((1600.0, -3.0, 1.0), (2500.0, -3.0, 1.0), (4000.0, -2.0, 1.0))


def _duck_chain_attr():
    """让路 EQ 的 `data-fx-chain` 属性（整条，含属性名，末尾无空格）。

    属性值里的 JSON 引号必须写成 `&quot;`：官方的 carve 脚本用 `name="..."` 的
    正则找这些属性，单引号写法它看不见。转义走本模块唯一的 esc()（= 标准库
    html.escape）——手写 `.replace("&",…).replace('"',…)` 只覆盖 5 个字符里的 2 个，
    剩下三个在 label 里出现时就裸插进了属性值。
    """
    nodes = [{
        "type": "peaking",
        "id": f"n{i}",
        "label": f"Make room for VO {int(freq)}Hz",
        "params": {"frequency": freq, "gain": gain, "q": q},
    } for i, (freq, gain, q) in enumerate(_DUCK_BANDS, start=1)]
    payload = json.dumps({"version": 1, "nodes": nodes},
                         separators=(",", ":"))
    return f'data-fx-chain="{esc(payload)}"'


def _video_audio_attrs(media_opts, sid, d):
    """视频音轨那几个 data-* 属性（每项自带一个尾随空格，见末行）。

    Hyperframes 的 ffmpeg 混音只读两类源：`<audio>` 元素，和声明了
    `data-has-audio="true"` 的 `<video>`。**不写就一条都不混**——所以
    `muted: false`（用户明确要原声）时必须补上这个声明，否则成片有画面、
    没原声，而整条链上没有任何报错。这是本函数存在的第二个理由。

    第一个理由是**让路**：口播是 <audio id="main-audio">（见
    templates/composition.html），视频原声是第二条进混音器的轨，两条默认都按
    1.0 进混音器。默认结果就是"原声压着旁白一起播"，跑完不报错、成片没法听。
    所以关掉 muted 而没写 volume 时，这里直接落一个让路默认增益
    （`MEDIA_DUCK_VOLUME`）并说明怎么覆盖——**默认那一版必须是不盖旁白的那一版**，
    "记得自己压音量"不是一道门禁，只是句话。显式写了 volume 就完全按写的来。

    反过来，`muted: true`（默认）时**不能**写 data-has-audio：契约是二选一，
    同时挂 muted 和 data-has-audio 会让 lint 的 video_missing_muted 判据失效。

    **每项都以一个空格收尾**（`" ".join(a + " ")`）：调用方把它直接拼在
    `poster="…"` 后面，两边都不带空格就会产出
    `poster="x.png"data-has-audio="true"` ——两个属性粘成一个畸形属性名。
    主流解析器会自行恢复，但拼接这件事本身不该靠解析器的容错兜底。
    """
    attrs = []
    muted = media_opts.get("muted", True)
    if not muted:
        attrs.append('data-has-audio="true"')
        duck = media_opts.get("duck", True)
        # 让路分两半，各管一段：响度交给 volume，频段交给 duck 的让路 EQ。
        # 两半都得**说出口**——替人改了混音却一声不吭，本身就是另一类静默行为，
        # 哪怕改的方向是对的（写了 volume 的人也该知道这条链挂上去了）。
        _notes = []
        if duck:
            attrs.append(_duck_chain_attr())
            _notes.append("并在人声频段上开了让路 EQ（data-fx-chain，duck:false 撤掉）")
        if "volume" not in media_opts:
            # 没写响度 → 替他压到让路默认。只提醒"建议给 volume"等于把一支
            # 默认盖住旁白的片子交出去：那句话没有改任何东西，成片照样没法听。
            attrs.append(f'data-volume="{_fmt_attr_num(MEDIA_DUCK_VOLUME)}"')
            _notes.append(f"没写 volume，已落让路默认 data-volume="
                          f"{_fmt_attr_num(MEDIA_DUCK_VOLUME)}")
        if _notes:
            print(f"[warn] images.json 的 '{sid}' 关掉了 muted：视频原声与口播"
                  f"（audio/combined.wav）抢同一段频谱，两条轨默认都按 1.0 进混音"
                  f"器、互相盖住。本次已让路——{'；'.join(_notes)}。"
                  "要原声当主体请显式写 volume（例如 1.0）并给 duck:false。",
                  file=sys.stderr)
        # 写了 volume 就完全尊重，不再自动兜底——他要的响度只有他知道。
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
        poster_attr = f'poster="{quote(poster)}" ' if poster else ""
        return (
            f'\n    <div class="seg-image" id="img-{sid}">\n'
            f'      <video id="vid-{sid}" src="{quote(media_path)}" '
            # 窗口值两位小数，与卡片标签的写法同型（唯一来源都是同一组 clip 值）
            f'data-start="{s:.2f}" data-duration="{d:.2f}" '
            f'{loop} {muted} {autoplay} {playsinline} '
            f'{poster_attr}{_video_audio_attrs(media_opts, sid, d)}>\n'
            f'      </video>\n'
            f'    </div>'
        )
    # 导演档（images.json 写了 director）：gen_hyperframes 已把净化后的 SVG
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


def _em_marker():
    """行文的词级强调挂点：一个**空** span，放在正文之后。

    生成期只给挂点、不切任何字——切成什么取决于成片时刻（逐帧 seek）与浏览器
    实测字宽，都属于运行时；正文因此保持成**一个裸文本节点**，运行时不用改
    DOM 也能把它量出来。

    两条都是实测撞出来的，不是风格：
    · 正文不能被 span 包住。含文字的叶子元素会被 Layout 门禁逐字量重叠，滚出
      窗口的行 rect 仍在原位，于是静态内容段成批报 text_occluded（散文级的
      proseCoverageFloor 救不了——包成 span 之后它就不再是散文单元）。
    · 挂点本身必须**空**：空 span 零宽零高，不参与重叠判定；一旦写成整行宽的
      盒子（display:block）或带上文字，门禁立刻按它报 text_occluded。
    """
    return '<span class="verse-em"></span>'


def _ved_layer(text):
    """词级强调的覆盖层：一个**生成期就位、内容为整句原文**的定位盒子。

    它的任务是让"还没念到"的那半句压暗。做法上复制整句原文、由运行时逐字把已念
    部分的 alpha 抹成 0（见 composition.css / runtime.js 的 verseVedGlyphs）。
    **内容必须在生成期写死**：运行时只改每个字的不透明度，不改文本、不改宽度——
    覆盖层与正文排版完全一致，逐字重合，没有位移也没有折行差异。

    为什么是完整一句而不是"剩下那截"：这是踩出来的。覆盖层若只写 slice(k)，它自己
    从头起排，折行点与正文不同，中段起每个字都被推挤，屏幕上出现一格叠一格的重影。

    为什么模板里先放一个空盒子、而不是让运行时插节点：逐帧渲染每一帧都要重测
    布局，DOM 结构在同一份 HTML 里恒定才能保证"同一时刻同一画面"；盒子与文字都是
    生成期就位的骨架。

    为什么是**元素**而不是行上的 ::before：伪元素 + content:attr() + clip-path
    在 Chrome 144 上有实测漂移（裁切左缘落后于 DOM 值几十像素、随机跳动、有时
    整层不再被裁），真元素走普通层叠没有这个问题，详见 composition.css 的注释。
    它是 data-layout-allow-occlusion 自带的一份豁免来源：覆盖层与正文逐字重合是
    设计使然，重叠本就预期，豁免由自身属性承担（不继承祖先的规则）。

    data-text 存原文：运行时按字符建 span、逐字上 alpha，字符与正文一一对应。
    """
    return ('<div class="verse-ved" aria-hidden="true"'
            ' data-layout-allow-overlap data-layout-allow-occlusion'
            f' data-text="{esc(text)}"></div>')


def _verse_html(seg, sid, ac_text_attr):
    """句子流（歌词式 verse）DOM：该段全部句子按序渲染成静态行。

    对话段（`dialogue`）在**轮次切换的首句**行首挂一枚说话人标签
    （`<span class="verse-who">`）。它上墙的理由与「画布页要把关键句画进图里」
    同源：谁在说这件事原本只存在于声音里——静音播放、听障观看、或两个音色
    选得相近时，观众手里只剩一串分不清轮次的字幕。只在轮次首句挂，同一
    说话人连着说三句不刷三遍标签（那会让字幕行首变成一列复读的抬头）。
    """
    # 三个 layout 豁免属性的继承规则**不一样**，别按一套规则打：
    #   · allow-occlusion / allow-overlap —— 检查器只认元素自己身上的标记，
    #     **不继承祖先**：每行 .verse-line 与 .verse-who span 都得自带。缺了
    #     就量那一行滚出窗口后仍在原位的 rect，判 text_occluded 报 error
    #     （实测：只打 .verse 报 1 error；补上每行后 0 error，warning 条数不变）。
    #   · allow-overflow —— **继承到子树**（感知探针向上遍历祖先），只打在
    #     .verse（overflow:hidden 所在层）就覆盖整棵子树。曾经三个属性一起
    #     打在每一层，行数是冗余的：实测改成只在 .verse 打，check 的 Layout
    #     输出逐条一致（7 段稿与 12 句长段稿都是）。
    # 留着 overflow 的理由：撤掉它，长段滚动时 check 会报一批 info 级的
    # text_box_overflow / container_overflow——行滚出窗口本来就是歌词式滚动
    # 的预期行为，那批 info 会随段落长度线性增长、把真正的噪声清单淹掉。
    # 代价是它顺带抑制子树内的 text-clipping / content-cramped-container /
    # foreground-over-panel；这三样在 verse 里本就无意义（行天生要被窗口裁）。
    _prev_who = None
    _vlines = []
    _sents = seg["sentences"]
    for _k, _s2 in enumerate(_sents):
        _who = _s2.get("speaker") or None
        # 轮次首句才标：与上一句同一说话人时不再重复。
        _who_html = ""
        if _who and _who != _prev_who:
            # occlusion/overlap 不继承，span 自己必须带（实测加了 span 之后
            # Layout 立刻报 text_occluded … span:nth-of-type(1)，判据与行那层
            # 一样：滚出窗口的 rect 仍在原位，只是又往下走了一层 DOM）。
            # overflow 不写：祖先 .verse 上的那份会继承覆盖到它。
            _who_html = (f'<span class="verse-who"'
                         f' data-layout-allow-overlap'
                         f' data-layout-allow-occlusion>{esc(_who)}</span>')
        _prev_who = _who
        # 句内读到的时刻区间挂进行上：词级强调按它算"这句话已经念到第几个字"。
        # 终点取**下一句起点**而不是 start+duration——与 cue 的 d 同一口径（推理
        # 见 _build_subtitle_cues 的 docstring），否则末字高亮会比字幕切换晚半拍，
        # 中间那段句间静音是"整句念完、等下一句"的状态。
        _t0 = round(float(_s2["start_time"]), 2)
        _t1 = (round(float(_sents[_k + 1]["start_time"]), 2)
               if _k + 1 < len(_sents)
               else round(float(_s2["start_time"]) + float(_s2["duration"]), 2))
        # 发声区间（manifest 的 onset/voiced，见 _speech.py）：音频块开头与结尾
        # 各有一段静默，真正开口的时刻在后面。词级强调的进度分母必须是**发声
        # 长度**而不是 t1−t0，否则长句的最后一个词先亮完、再干等几百毫秒。
        # 老 manifest 没有这两个字段 → 不写 data-voiced，运行时退回整块时长。
        _onset = float(_s2.get("onset") or 0.0)
        _voiced = _s2.get("voiced")
        _voiced_attr = ""
        if _voiced is not None:
            _voiced_attr = f' data-voiced="{max(0.01, float(_voiced)):.2f}"'
        _vlines.append(
            # data-i 兜底与 cue 侧 si 保持一致（缺 index 都落 -1）：
            # 两边兜底值不一致时，库调用传入无 index 句子会让 JS 高亮
            # 永久失灵或错行——宁可都不高亮，也不错误高亮
            f'<div class="verse-line" data-i="{_s2.get("index", -1)}"'
            f' data-t0="{_t0 + _onset:.2f}" data-t1="{max(_t0 + 0.01, _t1):.2f}"'
            f'{_voiced_attr}'
            f' data-layout-allow-overlap'
            f' data-layout-allow-occlusion>'
            f'{_who_html}{esc(_s2["text"])}{_em_marker()}'
            f'{_ved_layer(_s2["text"])}</div>'
        )
    return (
        # 豁免的成因：滚出窗口的行视觉上被 overflow:hidden 裁掉，但静态 DOM
        # rect 仍在原位——上越标题区（allow-overlap）、下碰底部元素如进度条
        # （allow-occlusion）、整体越出卡片（allow-overflow）。活动行锚定在
        # 窗口内 clipPad 处，真实重叠不可能发生。
        # 三个属性在这里齐全：overflow 由这一层向下继承到每一行。
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
    # 苹果风开场：开屏页（sid="opening"）且是 agenda 版式时**默认强绑定**——
    # 不读任何可配置键（opening_animation 已随默认强绑定从契约移除，见
    # _source_schema）。closing 页与内容段不进入这条编排；开屏页写 canvas
    # 版式（整页海报）时同样没有文本编排（agenda 的 DOM 都不生成）。
    apple_opening = sid == "opening" and is_agenda
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

    # 整页画布不生成标题层（连 DOM 都没有），所以标题字号这条守卫**整条都不该
    # 触发**：_title_font_px 里横屏降字号那条 [warn] 说的是"HTML 标题层会把
    # 字号降到 48px"，而画布页的标题是作者自己在 SVG 里画的，与 48px 无关。
    # 照旧调用只会产出一条内容为假的告警——把作者引去改一段根本不存在的标题。
    # tagline / verse / title_wrap 同理整条不生成：canvas 分支的装配一个都不
    # 消费，白算之外还会把 `font-size:;`（title_size 为空的守卫值）嵌进一串
    # 永不发射的 HTML 里——死代码不该长得像坏 CSS。
    title_size = ("" if is_canvas
                  else _title_font_px(rc, seg, sid, is_agenda))
    # agenda 卡的副标题是 `.agenda-kicker`（`_agenda_col_html` 画在 `.agenda-head`
    # 里），**不是** `#tag-{sid}`——这一行算出来也不会进 DOM。留着它，
    # `card.tagline_html` 就对"这一段到底画没画副标题"给了第二个答案：副标题入场
    # 补间会指向一个永远不存在的元素（与画布页那段"死代码不该长得像坏 CSS"同源）。
    tagline_html = "" if (is_canvas or is_agenda) else _tagline_html(rc, seg, sid, ac)
    # 结尾 kicker 的**存在性**：`_kicker_entrance_lines` 拿它当闸门，与
    # `tagline_html` 同一个口径——补间只在"真有这一行"时生成。开屏那行不在这里
    # 生成（它挂在 _apple_opening_lines 里），所以苹果开场时为 False。
    kicker_html = bool(seg.get("tagline")) and is_agenda and not apple_opening
    # <video> 必须落在**卡片那个可见窗口**上，不能用口播段长：
    # hyperframes 给每个带 data-start 的元素按**它自己**的窗口独立判显隐
    # （runtime 的 applyTimedElementVisibility + 逐帧 style.visibility），
    # 所以两段窗口的差集不是"稍早一点/稍晚一点"，而是头尾各有一段卡片还在
    # 画面上、video 已被判出窗——画面上只剩一个空图槽（实测 0.4s ≈ 10 帧）。
    # <img> 没有 data-start、继承祖先卡的窗口，两种素材必须同一口径。
    image_html = _media_html(rc, sid, win_start, vis_d) if has_image else ""
    verse_html = "" if is_canvas else _verse_html(seg, sid, ac_text_attr)

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
    title_wrap = ("" if is_canvas else (
        f'    <div class="seg-title-wrap">\n'
        f'      <div class="seg-title" id="title-{sid}" '
        f'style="font-size:{title_size};text-shadow:0 0 {rc.tl["glow"]}px {ac_attr}40">'
        f'{esc(seg["title"])}</div>\n'
        f'      {tagline_html}\n'
        f'    </div>'
    ))
    return SimpleNamespace(
        seg=seg, sid=sid, s=s, d=d, wipe=clip["wipe"], win_start=win_start,
        # vis = 这一页真正在画面上的时长（[擦除起点, 被下一页盖住]），也是
        # data-duration 写进标签的那个数。苹果开场按它归一，不按 d：编排锚在
        # 擦除起点，可用时长里还含着入场那段 wipe，短开场段里两者差一整个 gap。
        vis=vis_d,
        peel=clip.get("peel"), gets_peeled=clip["gets_peeled"],
        gets_line=clip["gets_line"],
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
        kicker_html=kicker_html,
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
    作为新卡的末子注入（绘制在内容之上、随父卡 clip-path 只露出揭示边以下部分）。
    闸门读 card.gets_line（唯一真源，在 clips 阶段算好）。"""
    if not card.gets_line:
        return ""
    lh = rc.anim["propLine"]["thickness"]
    return (f'    <div class="reveal-line" id="line-{card.sid}" '
            f'style="height:{lh}px"></div>\n')


def _peel_shade_html(rc, card):
    """被揭旧页的底缘暗边：纸绕底边轴掀起，明暗集中在折页线附近（Material
    elevation 的浮起表面边缘变暗）。用覆盖层而非 box-shadow 是两个物理坑：
    卡的外投影会被自身 clip-path 整层裁掉；inset 内阴影又会被后画的 .bg 子层
    盖住。opacity 随 peel 同窗补间（纯属性，seek 确定性不变）。"""
    if not card.gets_peeled:
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
    if not card.gets_line:
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


def _canvas_first_beat(card, director, beats):
    """画布页的首拍前置：让"这一页的第一件事"尽量发生在段的头一帧。

    **要解决的现象**：整页画布段没有标题层也没有句子流层，画面上只有一张 SVG。
    若导演的第一拍落在第 2 句（`at:1`）或更晚，画面从段起点到那一刻是**完全
    静止的一帧**——观众切页后先看到一张不动的海报，再"突然开始动"。门禁里那条
    "画布没有 appear 补间"的 warn 说的就是它。

    **做法**：不替作者改节拍（那会篡改他写的时间轴），而是在段起点给所有"第一拍
    之前还不会动到"的目标补一次**就位**（`tl.set`），让它们在 t=s 那一刻处于
    自己 `from` 态——画面从一开始就"已经在演"，而不是从浏览器默认态静止。
    这不会让任何元素提前出现（`from` 态本身就是补间的起点），只是把"起点"从
    第一拍的瞬间挪到段起点，代价为零、可逆。

    只处理**首拍晚于段起点 + 0.2s** 的情形；首拍本来就在开头的画布不重复写。
    也只看 `from`/`fromTo` 与 `set` 这类有明确起点态的目标，`to` 没有起点语义。
    """
    if not card.is_canvas or not beats:
        return []
    first_pos = beats[0][0]
    if first_pos - card.s <= 0.2:
        return []
    sid = card.sid
    seen, out = set(), []
    for step in director["steps"]:
        target = step.get("target")
        if not target or target in seen:
            continue
        src = step.get("from")
        if src is None and "morph" in step:
            src = {"attr": {"d": step["morph"]["from"]}}
        if not src:
            continue
        seen.add(target)
        sel = json.dumps(f"#img-{sid} {target}")
        out.append(f"tl.set({sel},{json.dumps(src)},{card.s:.2f})")
    return out


def _director_timeline_lines(rc, card):
    """SVG「导演」补间：把 images.json 的 director.steps 展开成挂在同一条
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
    lines.extend(_canvas_first_beat(card, director, beats))
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
            cycles = beat_cycles(step)
            if cycles is None:
                # repeat:-1 在 morph 上没有终点可采（契约层已拒，director 校验那条
                # “没有终点可采”）。此前这里写 `beat_cycles(step) or 1` 静默压成
                # 一遍：成片演一遍就停，而对轴门禁与接续烘焙仍按"无限、无收尾态"
                # 记账——报告与成片分家，且全程无 warn。宁可直接炸出来。
                raise ValueError(
                    f"director 步 {step.get('target')!r} 的 morph 不支持 "
                    "repeat:-1：形变是生成期采样，没有终点就采不出中间态")
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
            # 起点收起用 `tl.set`、位置在 `card.s`——**不能比它自己的步骤更晚**：GSAP
            # 建补间时会把每条 `from`/`fromTo` 的终点值预渲染一遍，后面的收尾补间把
            # `strokeDashoffset` 的终值（0，也就是"整条线都已画出"）提前写进了计算样式。
            # 实测：收起补间若写在 `pos`（第 2 秒），t=0…1.9 这段线的 dashoffset 已经
            # 是 0 —— 切页第一秒整条曲线先亮满，到了该画的时候反而往回缩。
            # 放在 `card.s` 就反过来了：它的终值（收起态）是最后被写下的那一个，
            # 段起点之前元素处在收起态，与"这段还没开始画"的语义一致。
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


def _kicker_kern_pad_px(rc):
    """结尾 kicker 行尾的字距垫宽度（px）：整行字距展开的总量，一字一格。

    起点位移是 `x = i × span`（第 i 字从 0 起），行的右缘最远多出 `(n−1) × span`。
    垫片按 `n × span` 给——一字不留余量，多出来那半格正是容器的 cushion。
    取整到 0.01px：CSS 里写的是浮点数，留全精度只会让金样里多一串尾数。
    """
    ak = rc.anim["opening"]["apple"]["kicker"]
    return round(rc.ag["kickerSize"] * max(0.0, ak["tracking"] - ak["rest"]), 2)


def _agenda_kicker_html(seg, bgs, ac, split_chars=False, pad_kern=False):
    """agenda 卡（开屏 / 结尾）的 kicker 行 HTML（`.agenda-kicker`，可空）。

    `split_chars=True` 逐字拆 `.k-ch`——字距舞（逐字 x = i×span）的载体，**只有
    开屏（苹果风开场）拆**。结尾不拆：它是一行固定姿态的小字，逐字拆只会丢掉
    整行的字距对（kern），同一句 kicker 在两页上会差半个像素的间距，读成"这两页
    的小标题不是同一个字号"。

    `from` 态的字距展开让行变宽，行宽若恰好贴着容器（`overflow:hidden`），起点
    态会被裁掉最后一个字——那一页得靠渲染端把展开量垫在行尾（见
    `_kicker_kern_pad_px` 与 composition.css 的 `.agenda-kicker[data-kern]`）。
    """
    if not seg.get("tagline"):
        return ""
    # 深底起手色向白提亮，再统一过 ensure_text_contrast 的对比度保底。
    _kc = ensure_text_contrast(mix(ac, "#ffffff", 0.55), bgs)
    # 拆在 esc() 之前拆的是原文、逐字符转义再包（同标题）；空格也拆、不特殊标记：
    # GSAP 的函数式值按**目标序号**求值，序号必须等于字符序号——空格不可见，但
    # 后面每个字的位移都指着它的序号。
    text = esc(seg["tagline"])
    if split_chars:
        text = "".join(f'<span class="k-ch">{esc(ch)}</span>' for ch in seg["tagline"])
    # data-kern：字距展开的容差垫，**只有真会演这一拍的那一页**才挂——
    # `data-opening-anim="apple"` 的开屏也演（逐字 x），它同样要垫；只在开屏
    # 一个选择器上写死会在"开屏改走非苹果编排"的那天悄悄失效。
    pad = ' data-kern="1"' if (split_chars or pad_kern) else ""
    return (f'<div class="agenda-kicker"{pad} style="color:{_kc}">'
            f'{text}</div>')


def _kicker_entrance_lines(rc, card):
    """结尾页 kicker 的入场：跟题头组同一拍（标题先起、kicker 错峰跟上）。

    **为什么需要**：`agenda-kicker` 是题头组的第一行，而它只在苹果风开场里有
    编排——非开屏的 agenda 卡（典型是结尾）里它没有 id、也没有任何补间，从卡片
    第一帧就站在那儿。这与第五轮"副标题是画面上唯一没进过时间线的元素"是同一个
    毛病在另一种版式上的那一半。

    **为什么只给结尾、不给开屏**：开屏的 kicker 由 `_apple_opening_lines` 逐字
    演（整支舞锚在擦除起点）；再挂一条整行补间，同一个元素会有两套补间互相覆盖。
    `card.apple_opening` 就是那道闸门。

    **幅度也走比例、终点必须精确落位**：起点位移写成"kicker 字距展开量"的
    `easeFrac` 倍，收拢到 `x:0`——`x:0` 就是静态字距（`letter-spacing` 归 CSS），
    所以补间是 `fromTo` 而不是 `from`：`from` 的起点若含 0.5px 的半像素吸附，
    落位时会留下残值，reading 上就是"小标题比其他行糊一点"。
    """
    a_k = rc.anim["titleEntrance"].get("kicker")
    if not a_k or card.apple_opening or not card.kicker_html:
        return []
    ease_frac = max(0.0, _unit_frac(a_k.get("easeFrac", 0.0)))
    if ease_frac <= 0.0:
        return []                     # easeFrac 0 = 明确不要这层
    a_title = rc.anim["titleEntrance"]
    _k = _entrance_k(rc, card)
    start = a_title["duration"] * max(0.0, _unit_frac(a_k.get("delayFrac", 0.0))) * _k
    dur = a_title["duration"] * max(0.0, _unit_frac(a_k.get("durationFrac", 1.0))) * _k
    if dur <= 0.01:
        return []
    # 单字展开量：与字距垫同一个数（`_kicker_kern_pad_px`），不在这里再推一遍公式
    # ——两处各写一份的话，改了 tracking 就会有一处没跟着走：补间的起点与垫片
    # 对不上，最后那一两个像素的差只在长 kicker 上露出来。
    x0 = _kicker_kern_pad_px(rc) * ease_frac
    sel = f"#kicker-{card.sid}"
    if x0 <= 0.01:
        # 没有字距可展开（kicker 很短或 tracking 已收回）：只演淡入，不动宽。
        return [f'tl.from("{sel}",{{opacity:0,duration:{dur:.2f},'
                f'ease:"{a_k["ease"]}"}},{card.s + start:.2f})']
    return [f'tl.fromTo("{sel}",{{opacity:0,x:{num_fmt(x0)}}},'
            f'{{opacity:1,x:0,duration:{dur:.2f},'
            f'ease:"{a_k["ease"]}"}},{card.s + start:.2f})']


def _apple_opening_budget(rc, card):
    """苹果风开场的等比压缩系数（1.0 = 原速不压）。

    这支舞的收尾时刻由**标题字数、行数与编号列**共同决定：最后一个字符落定 =
    chars.delay + stagger×(非空格字数−1) + duration，最后一行编号落定 =
    rows.idx.delay + stagger×(行数−1) + duration，最后一行内容滑入 =
    rows.content.delay + stagger×(行数−1) + duration。而它能用到的时长是
    开屏页的**可见窗口**（锚在擦除起点 win_start 的整段 life，不是口播段长）。
    两者不挂钩，于是短开场会静默丢尾巴：opening 写一句话（~1.0s）、内容段
    写满 7 行时，最后几行是在页面已被下一页盖住之后才浮起来的——没人看得到，
    日志也不吭声。

    口径对齐 entranceBudget：够就原速，不够按比例压，压到 minFactor 就停手
    并告警（压得更狠就成闪烁了，那比"演到一半被切"更难看）。光晕主光的呼吸
    与副光漂移不参与压缩——它们是淡入之后的稳态循环，压缩它们等于让开场一直
    在喘，而它们也不参与"舞是否演完"的判定。
    """
    ap = rc.anim["opening"]["apple"]
    bud = ap["budget"]
    a_t, a_k, a_r = ap["title"], ap["kicker"], ap["rows"]
    n = max(1, card.agenda_row_count)
    has_kicker = bool(card.seg.get("tagline"))
    title = card.seg.get("title") or ""
    n_chars = len(re.sub(r"\s", "", title))
    # 收尾时刻只数**真会生成**的那几条补间：没写 tagline 就没有 kicker 那条；
    # 空格字符（.t-sp）不参与字符 stagger；idx 与 content 两拍都生成（编号
    # 先行、内容跟上），各自算收尾——曾漏过 idx 那条，模板里 content 的
    # delay+duration 数值较大时永远盖得住，一旦被调小，预算就对着一个比真实
    # 收尾更早的时刻开灯，压不压、压多少全凭巧合。
    ends = [a_t["chars"]["delay"] + a_t["chars"]["stagger"] * max(0, n_chars - 1)
            + a_t["chars"]["duration"],
            a_r["idx"]["delay"] + a_r["stagger"] * (n - 1)
            + a_r["idx"]["duration"],
            a_r["content"]["delay"] + a_r["stagger"] * (n - 1)
            + a_r["content"]["duration"]]
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
            print(f"[warn] 开屏（苹果风开场，默认强绑定）的可见窗口 "
                  f"{card.vis:.2f}s 装不下整支开场动画（{need:.2f}s，"
                  f"{n} 行 agenda、{n_chars} 字标题）：已压到地板 "
                  f"{bud['minFactor']:.0%}，收尾仍会被下一页切走。"
                  "把 opening 写长一点（建议说完主题 + 为什么值得看，2.5s 以上）"
                  "、减少内容段数，或把 opening_layout 写成 \"canvas\""
                  "（整页海报，无文本编排）。",
                  file=sys.stderr)
    return k


def _apple_opening_lines(rc, card):
    """苹果风开场编排（开屏 agenda 卡默认强绑定，无契约键）。

    v2 的先进开场手感（对标科技发布会开场，全部 GSAP 时间线补间、逐帧 seek
    确定性）：
      - 标题**逐字浮出**：每字 blur + 上浮 + 微缩放落定，stagger 0.035 的
        波次比整行 blur 更"活"——苹果发布会大标题那种字逐个落定的节奏；
      - 背景**双光斑**：主光晕呼吸（accent 色）+ 副光漂移（冷色），两枚错位
        异色光在标题身后形成景深层次，替代单枚静态光斑的"贴纸感"；
      - kicker **字距展开**：整行 blur 淡入 + 逐字 x 从拉开态收拢到静态
        字距（letterSpacing 补间的 transform 等价式，见 _apple_opening_lines
        里的等价性推导——门禁禁 animate reflow 属性）；
      - agenda 行**两拍进入**：编号（.ag-idx）先行 scale+blur 落定，行内容
        （.ag-name）随后 x 滑入——前后景关系让行列表的读取顺序更强。

    全部锚在擦除起点 win_start：页面从 clip-path 被擦开的同时内容就在演化。
    时长是编排的一部分，**但按可见窗口等比归一**（_apple_opening_budget）：
    固定 choreography 遇到短开场会静默丢尾巴，而"丢了"这件事只有页面被盖住
    之后才知道，那时已经无法回溯。压缩是等比的，节奏比例不变。
    模糊是短暂的（标题约 1s 内收敛到 0），成片只有开头几十帧带 filter
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
    # 主光呼吸挂淡入完成之后：repeat:-1 永不停，seek 回放由 GSAP 按时间解析，
    # 与 director 的 repeat:-1 同一种确定性（无"最后停在哪儿"可争）。
    # breatheDur 不参与压缩——它是淡入之后的稳态循环，与"舞演完没有"无关。
    lines.append(
        f'tl.to("#halo-{sid}",{{opacity:{halo["breatheTo"]:.2f},'
        f'duration:{halo["breatheDur"]:.2f},repeat:-1,yoyo:true,'
        f'ease:"sine.inOut"}},{t + halo["in"] * k:.2f})'
    )
    # 副光斑：等 delay 之后淡入（duration 是淡入时长，配置里的 glow.duration，
    # 不拿 delay 充当——曾出现 delay 兼任淡入时长、glow.duration 整条死配置的
    # 读法），淡入完成后 x/y/scale 缓慢漂移（repeat:-1 yoyo）。漂移是稳态循环，
    # 同主光呼吸一样不参与压缩；它的锚点却要按压缩后的淡入收尾算（delay+
    # duration 都乘 k），否则短开场里漂移会抢在淡入还没演完时就开始。
    gl = halo["glow"]
    lines.append(
        f'tl.fromTo("#glow-{sid}",{{opacity:0}},'
        f'{{opacity:{gl["opacity"]:.2f},duration:{gl["duration"] * k:.2f},'
        f'ease:"sine.out"}},{t + gl["delay"] * k:.2f})'
    )
    lines.append(
        f'tl.to("#glow-{sid}",{{x:{gl["x"]},y:{gl["y"]},'
        f'scale:{gl["scale"]},opacity:{gl["breatheTo"]:.2f},'
        f'duration:{gl["breatheDur"]:.2f},repeat:-1,yoyo:true,'
        f'ease:"sine.inOut"}},{t + (gl["delay"] + gl["duration"]) * k:.2f})'
    )
    # 标题逐字浮出：目标选 .t-ch 排除空格 .t-sp（空格的 transform 无视觉意义）。
    # 字符 span 由渲染器拆好（见 _agenda_col_html），inline-block + white-space:pre
    # 保证 transform 生效且折行/字距不变。
    a_t = ap["title"]["chars"]
    lines.append(
        f'tl.from("#{sid} .t-ch:not(.t-sp)",{{opacity:0,'
        f'y:{a_t["y"]},scale:{a_t["scale"]},'
        f'filter:"blur({a_t["blur"]}px)",'
        f'duration:{a_t["duration"] * k:.2f},'
        f'stagger:{a_t["stagger"] * k:.2f},'
        f'ease:"{a_t["ease"]}"}},{t + a_t["delay"] * k:.2f})'
    )
    if has_kicker:
        a_k = ap["kicker"]
        # kicker 字距展开：blur + 逐字 x 从拉开态收拢。**不 animate
        # letterSpacing**——那是 reflow 属性，逐帧吸附整数设备像素，被
        # hyperframes 门禁判 gsap_non_transform_motion（error，无抑制开关），
        # 且 Lint 有 error 时 shouldBlockRender 直接把 browser 四阶段
        # （Runtime/Layout/Motion/Contrast）整个跳过——不是红叉，是半副
        # 门禁失明。改走门禁处方：拆字 + 逐字 animate x。
        # 等价性：单行 nowrap 下 letter-spacing L 让第 i 字左缘位移 i×L，
        # 于是 from 态 = 静态字距 rest + 每字 x=i×(tracking−rest)，两式
        # 逐像素重合；收拢态 x=0 即静态 rest。函数式值按目标序号求值，
        # build 时定值、seek 只插值，确定性与标题逐字 stagger 同源。
        step = (a_k["tracking"] - a_k["rest"]) * rc.ag["kickerSize"]
        lines.append(
            f'tl.from("#{sid} .agenda-kicker",{{opacity:0,'
            f'filter:"blur({a_k["blur"]}px)",'
            f'duration:{a_k["duration"] * k:.2f},'
            f'ease:"{a_k["ease"]}"}},{t + a_k["delay"] * k:.2f})'
        )
        lines.append(
            f'tl.from("#{sid} .k-ch",{{x:(i)=>i*{step:.2f},'
            f'duration:{a_k["duration"] * k:.2f},'
            f'ease:"{a_k["ease"]}"}},{t + a_k["delay"] * k:.2f})'
        )
    # agenda 行两拍：编号先行 scale+blur 落定，行内容随后 x 滑入，同 stagger
    # 波次，idx 的 delay 略先于 content——编号先"钉"住，名字再跟上。
    a_r = ap["rows"]
    a_i, a_c = a_r["idx"], a_r["content"]
    lines.append(
        f'tl.from("#{sid} .agenda-row .ag-idx",{{opacity:0,'
        f'scale:{a_i["scale"]},filter:"blur({a_i["blur"]}px)",'
        f'duration:{a_i["duration"] * k:.2f},'
        f'stagger:{a_r["stagger"] * k:.2f},'
        f'ease:"{a_i["ease"]}"}},{t + a_i["delay"] * k:.2f})'
    )
    lines.append(
        f'tl.from("#{sid} .agenda-row .ag-name",{{opacity:0,'
        f'x:{a_c["x"]},duration:{a_c["duration"] * k:.2f},'
        f'stagger:{a_r["stagger"] * k:.2f},'
        f'ease:"{a_c["ease"]}"}},{t + a_c["delay"] * k:.2f})'
    )
    return lines


def _still_image_carrier_sid(rc, card):
    """这张卡有没有可独立运镜的静态图载体（有则返回 sid，否则 None）。

    判据收成一份是因为它有三个消费者——`_image_drift_lines` 问"该不该漂"、
    `_image_settle_lines` 问"该不该收拢"、generate_html 的卡片循环问"轮到第几张"
    给方向编号。写两遍的话，第二次一定把某类载体算漏，于是编号跳号、相邻两段
    漂向同一侧（正是编号要防的事）。

    入场收拢与持续运镜作用在**同一个元素**（`#img-{sid} > img`）上，所以"哪些
    载体能独立运镜"只能有一个答案——不是两处巧合地一致。
    """
    if not card.has_image or card.is_canvas or card.is_keep:
        return None
    media = rc.images.get(card.sid) or {}
    if media.get("type") == "video" or (media.get("opts") or {}).get("inline_svg"):
        return None
    return card.sid


def _entrance_k(rc, card):
    """入场动效预算的压缩系数：短段整体压缩，长段维持原速。

    容器层入场、内容层收拢、持续运镜三处**都**按它压缩——三处各写一遍
    `min(1, max(minFactor, d/normSeconds))` 的话，改了归一化口径就会有一处没跟着
    走：那一段的图"框已经到位了、镜头还在按旧速度收"，而它看起来只是有点不对劲。
    """
    _eb = rc.anim["entranceBudget"]
    return min(1.0, max(_eb["minFactor"], card.d / _eb["normSeconds"]))


def _entrance_lead(rc, card):
    """入场预算（秒）：入场补间收尾的时刻，也是持续运镜的起点。

    容器层入场、内容层收拢、持续运镜三处都拿它当接缝，所以它必须是**一个**数：
    容器层在 `startDelay + duration` 收尾，运镜从那一刻起接手，内容层收拢的终点
    也钉在同一刻（见 `_image_settle_lines`）——三处各算一份的话，改了入场时长
    就会有一处没跟着走，成片里是"图还在收、运镜已经开始推"的重叠。
    """
    a_img = rc.anim["imageEntrance"]
    return (a_img["startDelay"] + a_img["duration"]) * _entrance_k(rc, card)


def _unit_frac(v):
    """把"占某个上限的比例"钳到 [-1,1]，**符号即方向**。

    这类量一律写比例、不写绝对值，是因为**上限本身随画幅与段长变**：写绝对值的
    话，一份在竖屏长段安全的输入换到横屏短段就越界了（运镜的 panX/panY 是同一
    条约束，见 `_image_drift_lines`）。同一件事只该有一份钳制。

    两种用法，符号各来自一处，互不干扰：
    · 入场收拢（`_image_settle_lines`）**直接把符号当方向**——档位表里写 `-0.6`
      就是"从左侧来"，所以这里必须保号；早先取 `abs` 的版本把四档里的两档压成
      同一个来向，轮换只剩三种。
    · 持续运镜的符号由 `_aim` 按段序给（相邻两张必反向），传进来的应当只带幅度。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return max(-1.0, min(1.0, f))


def _drift_for(rc, card, tpl_drift):
    """模板运镜档 + 本段 images.json 的 drift 覆盖 → 这一段真正用的四个数。

    覆盖是"逐项替换"而不是整块替换：作者写 `{"ratePerSec": 0.01}` 的意思是
    "只把推近推快些"，不该顺带把 panX/panY 归零。`minSeconds` 不在覆盖项里——
    它是"短段不值得起运镜"的判据，与取景手感无关。
    """
    raw = ((rc.images.get(card.sid) or {}).get("opts") or {}).get("drift")
    if raw is None:
        return tpl_drift
    if raw is False:
        return None
    merged = dict(tpl_drift)
    merged.update({k: v for k, v in raw.items() if k in merged})
    return merged


def _image_drift_lines(rc, card):
    """静态图在画面上的持续缓慢运镜（Ken Burns）。

    **为什么需要这一条**：配图入场那 0.8s 的淡入上移演完之后，这张图在剩下的
    8~20 秒里是**完全静止**的。一段里实时在动的只有句子流，画面本身是一张钉死的
    海报——这正是 README 开篇要划清的那条线（"不是幻灯片录屏"）。前沿做法（纪录片
    Ken Burns、Kurzgesagt、Apple 产品页）给静止素材叠一层**恒定速率的缓慢推近**：
    不为演出什么，只为让画面维持呼吸感。

    **三条约束是 cover 语义直接推出来的，不是审美偏好**：

    1. **只推近，不拉远。** 图片载体已是 `width:100%;height:100%;object-fit:cover`
       铺满槽位，任何 `scale < 1` 都会被槽位的 `overflow:hidden` 切出一个露背景的
       边——不是"图小了一点"，是**露底**。所以补间只在 [1, maxScale] 上单调推进，
       极小值就是起点 1；要"来回"必须让最小值仍等于 1，否则端点即事故。
    2. **位移必须落在推近让出来的盈余里。** 放大 s 倍后单侧各多出 (s−1)/2 × 槽位
       边长；越过这个数同样露底。所以位移**不写绝对 px**，写"最多用掉多少盈余"
       （panX/panY ∈ [0,1]，1 = 边缘刚好贴住槽位边），再按当前画幅的槽位尺寸乘
       出来——换画幅、改槽位几何都不会把一份安全的输入变成露底。
       **方向不写在这里**：所有段的图都往同一侧漂本身就是种会被读出来的模式，
       实际方向按"第几张静态图"轮 Four 象限（见 `_aim`）。
    3. **匀速。** 要的是"发现有运动"同时"别注意到运动"：任何 ease 都会把一层背景
       运动读成有意图的镜头调度（"它为什么在这儿加速"）。所以 ease 恒为 `none`，
       不开放成旋钮——这一层不该有脾气。

    **作用点是图载体本身**（`#img-{sid} > img`）而不是槽位盒 `#img-{sid}`：盒上挂着
    圆角、外发光与 `::after` 内描边（见 composition.css），对盒 scale 会把描边和辉光
    一起放大；盒的 transform 也被入场补间的 `y` 独占着。分开两个元素，各演各的。

    **不漂的三类载体**（判据在 `_drift_carrier_sid`）：`<video>`（每秒 24 帧自己在
    放，再加运镜是运动冗余）、写了 `director` 的内联 SVG（那段作者已手写过编排，
    别替他叠第二层意图）、整页画布与 `stage:"keep"` 接续页——后两条与跳过**入场
    补间**的口径同源（画布由导演负责、接续页搬的是上一页演完的终态）。
    """
    if _still_image_carrier_sid(rc, card) is None:
        return []
    a_img = rc.anim["imageEntrance"]
    tpl_drift = a_img.get("drift")
    if tpl_drift is None:
        return []
    drift = _drift_for(rc, card, tpl_drift)
    if drift is None:
        return []                     # 这一段写了 drift:false
    # 起点 = 入场补间收尾的时刻（入场段长同样吃了入场预算那份压缩，两处同一式）。
    # 错开不是怕两个补间打架（它们作用在不同元素上），是不要"一边滑进来一边放大"。
    lead = _entrance_lead(rc, card)
    # 终点 = 本段口播收尾，与进度条同一口径（不取可见窗口：往后那点 gap 里画面
    # 马上被下一页擦走，继续补只是在切页邻域多留一拍没人看的运动）。
    avail = card.d - lead
    if avail < float(drift["minSeconds"]):
        return []                     # 短段：还没展开就被切走，读成抖动
    scale_to = min(1.0 + float(drift["ratePerSec"]) * avail, float(drift["maxScale"]))
    if scale_to <= 1.0:
        return []                     # 速率与封顶都给 0 = 明确不要这层
    bw, bh = get_image_box(rc.aspect)

    def _aim(axis):
        """第 axis 轴的取景方向：[-1,-AIM_FLOOR] ∪ [AIM_FLOOR,1] 上的值。

        **为什么按"第几张图"而不是按 id 哈希取符号**：所有段都朝同一侧漂本身就是
        一种能被读出来的模式——连看三段"图都往上飘"，观众学到的是"它是个模板"
        而不是每段各有取景。先试过纯哈希（`seeded_wobble` 的符号），它长期分布
        均匀但**局部会扎堆**：实测 seg-a…seg-f 连续六张全落在同一侧。**相邻同侧的
        概率太高**，正是要防的那件事，所以符号必须由段序保证严格交替、哈希只
        负责幅度（让速度/距离各异，不像处方式 repeat）。

        y 轴按序号奇偶翻、x 轴按每两张翻一张 —— 四张一轮回走遍四个象限，像
        纪录片 per-take 换过的机位，而不是"左右横跳"。
        """
        ordinal = getattr(card, "drift_ordinal", 0)
        flip = ordinal % 2 if axis == 1 else (ordinal // 2) % 2
        magnitude = (AIM_FLOOR + (1.0 - AIM_FLOOR) * abs(seeded_wobble(card.sid, axis)))
        return (-magnitude) if flip else magnitude

    slack_x = (scale_to - 1.0) * bw / 2.0
    slack_y = (scale_to - 1.0) * bh / 2.0
    x = _unit_frac(drift["panX"]) * slack_x * _aim(0)   # axis 0 = x
    y = _unit_frac(drift["panY"]) * slack_y * _aim(1)   # axis 1 = y
    xf, yf = num_fmt(x), num_fmt(y)
    breath = _breath_segments(drift.get("breathe"), avail, scale_to)
    # 段起点之前先钉住起点态。**这不是保险，是必需的**：GSAP 建时间线时会把每个
    # `fromTo` 的**终点值**预渲染一遍（immediateRender），于是最后一条运镜补间
    # 的终点（本例 scale 1.06 / x 3.859）在 t=0 就已经写在元素上了——成片里是
    # "切页第一秒图比后面大一圈，到 1.0s 又缩回去"（实测 t=0…0.98 恒为 1.06，
    # t=1.0 才跳到 1）。终点值按语句顺序互相覆盖，所以只要**最后**一条把起点写
    # 回去就行：0.01s 的 fromTo，与段擦除同一条口径（真 tween、回退 seek 也回滚）。
    pin = (f'tl.fromTo("#img-{card.sid} > img",{{scale:1,x:0,y:0}},'
           f'{{scale:1,x:0,y:0,duration:0.01,ease:"none"}},'
           f'{max(0.0, card.s + lead - 0.01):.2f})')
    if not breath:
        return [f'tl.fromTo("#img-{card.sid} > img",{{scale:1,x:0,y:0}},'
                f'{{scale:{num_fmt(scale_to, 4)},x:{xf},y:{yf},'
                f'duration:{avail:.2f},ease:"none"}},{card.s + lead:.2f})', pin]
    # 呼吸：把"匀速推到顶"换成"快 / 缓 / 快"的分段推进。每段仍写成 fromTo
    # 并显式给出**自己的起点值**（= 上一段的终值），所以乱序 seek 依然幂等
    # ——任何时候 seek 到第 i 段，GSAP 都能从写死的起点直接算出该帧，不依赖
    # 上一帧留下的状态。首段的起点就是 (1, 0, 0)，与不呼吸时逐像素一致。
    out = []
    t0 = card.s + lead
    prev = (1.0, 0.0, 0.0)
    for dt, sc, xr, yr, ease in breath:
        cur = (sc, x * xr, y * yr)
        out.append(
            f'tl.fromTo("#img-{card.sid} > img",'
            f'{{scale:{num_fmt(prev[0], 5)},x:{num_fmt(prev[1])},y:{num_fmt(prev[2])}}},'
            f'{{scale:{num_fmt(cur[0], 5)},x:{num_fmt(cur[1])},y:{num_fmt(cur[2])},'
            f'duration:{dt:.2f},ease:"none"}},{t0:.2f})')
        prev = cur
        t0 += dt
    return out + [pin]


def _title_rhythm(a_title, ordinal):
    """按段序取标题入场的节奏档，让每章开场不是同一套曲线。

    **为什么需要**：`back.out(1.7)` + `scale:0.5` 是好的默认，但**每段都一样**时，
    观众在第二段就学会了"哦，又是这样弹一下"——标题变成装饰而非节奏。前沿的
    章节式解说（Kurzgesagt、Apple 发布会章节页）会让开场在"弹一下 / 滑上来 /
    清爽淡入"之间轮换，同一部片子里读得出推进。

    **档位是有限集而不是随机数**：随机让相邻两段有概率撞成同一种（与 Ken Burns
    那边踩过的坑同源），而严格轮换保证连续四段必不重样。档位表在
    `_template.titleEntrance.rhythm` 里，`from`/`y`/`ease` 逐项覆盖同一套键——
    缺 `rhythm` 时退回顶层的 `from`/`ease`，老产物行为不变。
    """
    base = {"from": a_title["from"], "y": 0, "ease": a_title["ease"]}
    rhythm = a_title.get("rhythm")
    if not rhythm:
        return base
    pick = dict(base)
    pick.update(rhythm[ordinal % len(rhythm)])
    return pick


def _tagline_entrance_lines(rc, card, rh):
    """副标题行的入场：跟着标题那一档节奏进来，幅度小一号、起点错峰。

    **为什么需要**：题头组是两行（标题 + 副标题），而副标题曾经是画面上**唯一
    一个从头到尾没进过时间线的元素**——转场（擦除 + 引导线 + 剥离）、标题、
    配图（框 / 收拢 / 运镜）、字幕（接力 + 词级强调）、背景三层、苹果风开场
    都各有一拍，唯独它从卡片第一帧就在那儿、钉到被下一页盖走。前沿的章节页
    （Material 的 choreography、Apple Keynote 的 build、Framer 的
    staggerChildren）里同一组元素**从不一起到位**：主线先起、附属层错峰跟上，
    读出来是"一支编排"而不是"一块东西浮上来"——这与第四轮「框先落位、图本体
    后收拢」是同一条原则，只是从图内部的两层升到题头组的两行。

    **为什么挂在标题那一档上，不另开一组旋钮**：副标题是**这一组里的第二行**，
    另开一组参数等于允许"标题弹进来、副标题滑上来"——两层各有脾气就不再是
    编排，是两块各自为政的动效。所以它的幅度写成标题那一档的**比例**：

        y_tag    = y_title × ampFrac
        from_tag = 1 − (1 − from_title) × ampFrac

    `ampFrac ∈ [0,1]`，于是"副标题永远不比标题动得多"是**结构上恒成立**的
    （与运镜 panX/panY 写"占盈余的比例"同源：让越界不可表达，而不是生成期钳
    住并 warn）。符号也随之继承，两层不会反向打架——第四轮"纵向一律与容器层
    同向"是同一条理由。`ampFrac = 0` = 明确不要这层（退回改动前的行为）。

    **起点由结构推出，不是第二个数**：起点 = 标题入场时长 × delayFrac。"标题
    先起、副标题跟上"因此不需要人对着两个数字，改标题时长时副标题自动跟。两拍
    **交叠**而不是首尾相接（`delayFrac < 1`）是刻意的：overlapping action 才
    有编排感，排成首尾相接的队列是 PPT 的"逐条出现"。

    **曲线固定，不跟着标题那档跳**（`tagline.ease`）。幅度与方向继承标题，是为
    了证明"它属于这一组"；**速度**不继承，是逐帧实测逼出来的：标题 rhythm 四档
    里有 `expo.out`，Chromium 上按 30fps 网格 seek 实测，副标题在这条曲线上
    0→76% 只用了 0.08s ≈ **2 帧**，"错峰"在成片里根本读不出来（同一个
    delayFrac 下换成慢起慢收的 `power2.out`，可见行程才有 ~0.25s ≈ 7 帧）；
    另一档 `back.out(1.7)` 会让副标题冲过 1 再弹回来——小字号上那一下是抖动
    不是弹性。代价是副标题少了轮换那一层变化，而轮换本就由标题那一档提供：
    "主线快、附庸稳"本身就是一种对比。

    **给 `opacity:0`**：`from` 补间在页面加载时就把起点态写在元素上
    （immediateRender），而卡片从擦除起点起就部分可见——没有 opacity 的话，
    副标题会在擦除窗里以缩放态先露一小截。标题的 `scale 0.5` 读成"正在进来"，
    小字号副标题的缩放态读成"半截字"。
    """
    tg = rc.anim["titleEntrance"].get("tagline")
    if not tg or not card.tagline_html:
        return []
    amp = max(0.0, _unit_frac(tg.get("ampFrac", 0.0)))
    if amp <= 0.0:
        return []                     # ampFrac 0 = 明确不要这层
    _k = _entrance_k(rc, card)
    a_title = rc.anim["titleEntrance"]
    start = a_title["duration"] * max(0.0, _unit_frac(tg.get("delayFrac", 0.0))) * _k
    dur = a_title["duration"] * max(0.0, _unit_frac(tg.get("durationFrac", 1.0))) * _k
    if dur <= 0.01:
        return []
    y = float(rh.get("y") or 0.0) * amp
    frm = 1.0 - (1.0 - float(rh["from"])) * amp
    parts = ["opacity:0", f"scale:{num_fmt(frm, 4)}"]
    if y:
        parts.append(f"y:{num_fmt(y)}")
    return [f'tl.from("#tag-{card.sid}",{{' + ",".join(parts)
            + f',duration:{dur:.2f},ease:"{tg["ease"]}"}},{card.s + start:.2f})']


def _settle_rhythm(a_img, ordinal):
    """按段序取素材入场的收拢档，让每张图不是以同一种方式到位。

    **为什么需要**：`scale 1.06 + panY 0.6` 是好的默认，但**每段都一样**时，观众
    在第二段就学会了"哦，图又是从下面浮上来的"——入场变成装饰而非节奏。这与
    `_title_rhythm` 是同一件事的同一类毛病，所以结构照抄它：档位是有限集、按段序
    取模、逐项覆盖同一套键。

    **档位是有限集而不是随机数**：随机让相邻两段有概率撞成同一种（与 Ken Burns
    那边踩过的坑同源），严格轮换保证连续四段必不重样。四档刻意给四个**不同的
    来向**（纵 / 右 / 纯收焦 / 左）——纵向一律与容器层同向（容器层 `vert_y` 是从
    下方上来），反向会让"框往上走、图往下沉"读成两层在打架。
    """
    base = {k: a_img["settle"][k] for k in ("scale", "panX", "panY", "ease")}
    rhythm = a_img.get("rhythm")
    if not rhythm:
        return base
    pick = dict(base)
    pick.update(rhythm[ordinal % len(rhythm)])
    return pick


def _image_settle_lines(rc, card, lead):
    """内容层（图本体）的收拢落位：从"略大、略偏"收拢到精确位置。

    **为什么需要**：容器层入场（`opacity` + `y`）把圆角、1px 内描边、外发光和图
    本体四样东西**同时、同曲线、同方向**送到位——画面上读出来的是"一块东西浮
    上来"，而不是"镜头落到素材上"。前沿的素材入场（Apple 产品页 hero、Kurzgesagt
    的 B-roll 入画、电影 rack focus）是 **settle**：素材从略大、略偏的状态收拢到
    精确位置。"滑进来"是素材在动，"收拢"是镜头在落，读起来是两回事。

    **为什么不写进容器层**：容器层（`> img` 之外的 `#img-{sid}`）上挂着圆角、描边
    与外发光，对它 scale 会把这三样一起放大（与 `_image_drift_lines` 不漂盒是同一
    条理由）；它的 transform 也被入场补间的 `y` 独占着。两层分开，各演各的。

    **不露底靠结构，不靠钳制**：偏移写成"最多用掉多少盈余"的比例（`panX/panY` ∈
    [0,1]，与运镜同源同构，见 `_unit_frac`），实际 px 由 `(scale−1) × 槽位边长 / 2`
    乘出来——于是 `|y₀| ≤ (s₀−1)·bh/2` **恒成立**，换画幅、改槽位几何都不会把一份
    安全的输入变成露底。GSAP 写出的 transform 是 `translate(x,y) scale(s)`，平移量
    是未被缩放的屏幕 px，所以盈余算式不用再乘 `s`。

    **终点恒等于运镜起点**：duration 不独立配置，由 `lead − 起点` 推出。写死
    duration 就会出现"还没收完、运镜已经开始推"的重叠或"收完了干等"的空档，两者
    在成片里都是速度突变。`startFrac` 是唯一旋钮，且它占的是入场预算的比例，因此
    起点永远落在预算之内。

    **顺序**：本函数返回的补间必须排在运镜补间**之前**——运镜末尾那条钉住补间的
    终值（`scale:1,x:0,y:0`）是最后落地的值，GSAP 建时间线时的预渲染才不会把
    `scale 1.06` 留在元素上（同样的坑见 `_image_drift_lines` 的 pin 注释）。
    """
    if _still_image_carrier_sid(rc, card) is None:
        return []
    a_img = rc.anim["imageEntrance"]
    settle = a_img.get("settle")
    if not settle:
        return []
    # 起点占入场预算的比例 → 绝对偏移；终点恒为 lead
    offset = (a_img["startDelay"] + a_img["duration"]
              * max(0.0, _unit_frac(settle.get("startFrac", 0.0)))) * _entrance_k(rc, card)
    dur = lead - offset
    if dur <= 0.01:
        return []
    pick = _settle_rhythm(a_img, getattr(card, "image_ordinal", 0))
    scale = float(pick["scale"])
    span = scale - 1.0
    if not (span > 0):
        return []                     # scale ≤ 1 = 明确不要这层（且必然露底）
    bw, bh = get_image_box(rc.aspect)
    x = _unit_frac(pick["panX"]) * span * bw / 2.0
    y = _unit_frac(pick["panY"]) * span * bh / 2.0
    sel = f"#img-{card.sid} > img"
    return [f'tl.fromTo("{sel}",{{scale:{num_fmt(scale, 4)},x:{num_fmt(x)},'
            f'y:{num_fmt(y)}}},{{scale:1,x:0,y:0,duration:{dur:.2f},'
            f'ease:"{pick["ease"]}"}},{card.s + offset:.2f})']


def _breath_segments(breathe, avail, scale_to):
    """把单调推近切成"推 → 缓 → 推"的小段，让长段落后半程不再死住。

    **为什么需要**：匀速推近在 8~20 秒里只做一件事——一直变大。段尾那几秒观众
    读到的是"还在动"，但读不出节奏；而前沿的纪录片/产品页运镜是**有呼吸的**：
    推近一段、几乎停住、再推一段。这里给同一段总时长加一层低频起伏。

    **回弹只能落在盈余里**（与 cover 语义同一条硬约束，见 `_image_drift_lines`）：
    scale 永远 ≥ 1，且永不越过 `scale_to`——回弹是把当前值往回退到 1 与峰值之间，
    不是缩到 1 以下。所以在 s(t) = 1 + A(t)·(scale_to−1) 里起伏的是**进度系数**
    A ∈ [1−depth, 1]，A 的下界由 depth 钳住（`depthFrac` 上限 0.5，留足余量）。
    位移同比例缩放，保证"图始终盖满槽位"。

    返回 [(duration, scale, xRatio, yRatio, ease), …]；空列表 = 走原来的单段匀速。
    只有**够长**的段才分段：呼吸感要靠"推 → 缓 → 推"的完整周期读出来，8 秒以内
    的段切三段只会读成卡顿；短段保持一段匀速，行为与加这个旋钮之前逐像素一致。
    """
    if not breathe or avail <= 0 or scale_to <= 1.0:
        return []
    try:
        period = float(breathe.get("periodSeconds", 0))
        depth = float(breathe.get("depthFrac", 0))
    except (TypeError, ValueError):
        return []
    if not (period > 0.8) or not (0 < depth <= 0.5):
        return []
    # 段数：按周期切，至少三段（推 / 缓 / 推），最多 6 段——再多就碎成抖动。
    # 不足两个周期的段不分段：那点长度装不下一次完整的"推—缓—推"，切了只会
    # 读成卡顿，不如老老实实匀速推完（也就退化成加这个旋钮之前的行为）。
    if avail < period * 2:
        return []
    n = int(max(3, min(6, round(avail / period) + 1)))
    dt = avail / n
    span = scale_to - 1.0
    out = []
    for i in range(n):
        # 进度系数 A **单调不减**、但增量不均匀：一段推得多、一段推得少，于是
        # 曲线是"快 → 缓 → 快"，读起来是呼吸而不是匀速直线。退步只体现在
        # "这一段几乎没动"（增量被压到 depth 倍），绝不出现"往回退"——回退会让
        # 观众以为画面在缩，且段与段之间的可读性反而更差。
        # 末段固定落在 1.0（= scale_to），保证终点与不呼吸时完全一致。
        if i == n - 1:
            a = 1.0
        else:
            # 前 n-1 段走到"还剩最后一步"的位置；偶数段推进多、奇数段推进少
            share = (i + 1) / n
            if i % 2 == 1:
                share = share - depth / n
            a = max(0.0, min(1.0, share))
        out.append((dt, 1.0 + span * a, a, a, "none"))
    return out


def _card_timeline_lines(rc, card):
    """本卡的 GSAP 时间线：遮罩擦除入场、标题/配图入场、配图漂移、进度条。"""
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
    # wipe=0（整页画布段/接续页的"瞬间切"）：GSAP 把 duration:0 的 fromTo 视作 set()，
    # immediateRender 在页面加载时即渲染 to 态（不裁剪）——预览首帧被画布页直接
    # 盖住、全程覆盖前面页面（渲染端因并行/回退 seek 会回滚 set 而不受影响，
    # 于是预览与成片分家）。给 0.01s 让它成为真 tween：加载时渲染 from 态
    # （inset 100%，与内联样式一致），回退 seek 时也回滚到 from 态，预览/渲染
    # 两端口径一致；0.01s 在 30fps 下不足一帧，视觉上仍是"瞬间切"。
    w_from, w_to = _wipe_pair(rc)
    wipe_dur = max(card.wipe, 0.01)
    lines.append(
        f'tl.fromTo("#{sid}",{{clipPath:"{w_from}"}},'
        f'{{clipPath:"{w_to}",duration:{wipe_dur:.2f},'
        f'ease:"{_wipe_ease(rc)}"}},{card.win_start:.2f})'
    )
    lines.extend(_line_timeline_lines(rc, card))
    # 入场动效预算随段长归一化：短段整体压缩，长段维持原速。
    _k = _entrance_k(rc, card)
    # Title entrance（画布页没有 HTML 标题，标题在画布里，补间一起跳过）
    if not card.is_canvas and not card.apple_opening:
        a_title = a_["titleEntrance"]
        _rh = _title_rhythm(a_title, getattr(card, "title_ordinal", 0))
        lines.append(
            f'tl.from("#title-{sid}",{{scale:{_rh["from"]},'
            + (f'y:{_rh["y"]},' if _rh["y"] else "")
            + f'duration:{a_title["duration"] * _k:.2f},'
            f'ease:"{_rh["ease"]}"}},{s:.2f})'
        )
        # 副标题跟着**同一档**节奏进来（幅度小一号、起点错峰）：题头组两行一起
        # 到位就还是"一块东西浮上来"。参数挂在标题档位上而不是另开一组，理由见
        # `_tagline_entrance_lines`。
        lines.extend(_tagline_entrance_lines(rc, card, _rh))
    # 结尾页（非苹果开场的 agenda 卡）的 kicker：题头组的第一行，跟标题同一拍。
    # 苹果开场不在这里——那一行由下面 _apple_opening_lines 逐字演。
    lines.extend(_kicker_entrance_lines(rc, card))
    # 苹果风开场：整页编排取代通用标题入场（开屏 agenda 卡默认强绑定）。
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
        #
        # 容器层只占入场预算的 frameFrac：**框先落位**，图本体随后在框里收拢
        # （_image_settle_lines）。两层错峰才有"镜头落到素材上"的层次——四样东西
        # （圆角/描边/辉光/图）同时到位读出来只是一块东西浮上来。
        lead = _entrance_lead(rc, card)
        lines.append(
            f'tl.from("#img-{sid}",{{opacity:0,y:{a_img["vert_y"]},'
            f'duration:{a_img["duration"] * max(0.0, _unit_frac(a_img.get("frameFrac", 1.0))) * _k:.2f},'
            f'ease:"{a_img["ease"]}"}},'
            f'{s + a_img["startDelay"] * _k:.2f})'
        )
        # 同一个 guard 内挂收拢与持续运镜：能配图入场的才有资格演这两层。
        # 顺序不可换——运镜末尾那条钉住补间必须最后落地（见 _image_settle_lines）。
        lines.extend(_image_settle_lines(rc, card, lead))
        lines.extend(_image_drift_lines(rc, card))
    lines.append(
        f'tl.to("#prog-{sid}",{{width:"100%",duration:{d:.2f},ease:"none"}},{s:.2f})'
    )
    # 导演补间挂在本卡时间线末尾：位置各自取句子起点，与上面的入场/进度条互不干扰。
    lines.extend(_director_timeline_lines(rc, card))
    return lines


def generate_html(manifest, audio_src, images=None,
                  width=None, height=None,
                  gsap_src=_DEFAULT_GSAP_SRC,
                  aspect="portrait", theme=DEFAULT_THEME, fps=DEFAULT_FPS):
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
    # 接续页自动盖章：判据唯一真源在 `_stage_carry.infer_stage_keep`。放在这里是为了
    # **渲染端与生成期看到同一个答案**——生成期（gen_hyperframes.director_prepare）
    # 拿它决定"烘不烘焙"，渲染端拿它决定"要不要擦进来 / 重播入场"。两边各判一次
    # 就一定会分家，而分家的症状是"烘了却照样擦进来"，烘焙的意义正好被抵消掉。
    # 判据只读 src / director / inline_svg，这三样在归一化前后同值。
    infer_stage_keep(images, [seg["id"] for seg in manifest["segments"]],
                     src_of=lambda sid: images[sid]["src"])
    total_dur = manifest["total_duration"]
    sentences = manifest["sentences"]
    # <script src> 属性上下文转义：CLI 传的本地路径可能含 & 或引号，裸插
    # 会破坏 head 结构（媒体路径都走了 quote/转义；这里不能 URL 编码——
    # CDN 地址的 :/? 会被 quote 破坏）。转义函数用本模块的 esc()（标准库
    # html.escape），不再手写只覆盖 & 与 " 的半边 replace。
    gsap_src_attr = esc(gsap_src)

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
            "gets_line": False,
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
        # 上一页是整页画布段时也不剥：把一张活diagram 像纸一样掀走正是"演"要取代的 PPT 转场，
        # 画布页退场同样走硬切（被下一页直接盖住）。
        #
        # gets_line 与 gets_peeled 在这里一次算定，是"这一页挂不挂引导线"的**唯一
        # 真源**：线元素由 _reveal_line_html 注入、它的补间由 _line_timeline_lines 生成、
        # 底缘暗边由 _peel_shade_html 注入，三处一律读这里算好的标记。闸门分家就会
        # 静默错位——DOM 里有线、却没有任何补间驱动它，线永远停在初始位置（页底一条
        # accent 横杠），画面照出、全流程零报错。
        clip["gets_line"] = bool(_is_line and clip["wipe"] > 0)
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
    # Ken Burns 的方向轮换序号：只对**真的会漂**的那些段编号（判据在
    # _still_image_carrier_sid，与 _image_drift_lines 同一份），所以整页画布段/
    # video 段不会占掉一个号、让两张相邻静态图撞到同一侧。
    _drift_ordinal = 0
    # 入场收拢的轮换序号：与上面同源——只对**真的会收拢**的那些段编号（判据同样
    # 是 `_still_image_carrier_sid`），整页画布段/video 段不占号，相邻两张静态图
    # 不会撞到同一个来向。
    _image_ordinal = 0
    _title_ordinal = 0
    # 句尾收束的时长：全片一个数，取**所有非页尾句**里最短的那段留白（见
    # _ved_settle_seconds）——任何一句收不完都会和下一句撞车，所以按最紧的那句定。
    # 页尾句的 si 来自同一批 manifest 句子。
    _page_last_sis = {c["seg"]["sentences"][-1].get("index", -1)
                      for c in clips if c["seg"]["sentences"]}
    _settle_s = _ved_settle_seconds(sentences, _page_last_sis)
    for clip in clips:
        card = _prepare_card(rc, clip)
        card.settle_s = _settle_s
        if _still_image_carrier_sid(rc, card) is not None:
            card.drift_ordinal = _drift_ordinal
            _drift_ordinal += 1
            card.image_ordinal = _image_ordinal
            _image_ordinal += 1
        # 标题入场节奏的轮换序号：按**所有**卡片的段序编号（含画布/接续页），
        # 因为标题节奏要读出"这是第几章"的推进感，中间少掉一段会让周期错位。
        card.title_ordinal = _title_ordinal
        _title_ordinal += 1
        # 引导线/底缘暗边注入卡 DOM 末尾（绘制在内容之上；线随父卡 clip-path
        # 裁切，shade 挂在被揭的那张旧卡上）
        seg_cards.append(_attach_card_tail(
            _assemble_card(rc, card, clips, manifest),
            _reveal_line_html(rc, card) + _peel_shade_html(rc, card)))
        gsap_lines.extend(_card_timeline_lines(rc, card))

    # ── Subtitle cues ──────────────────────────────────────────────
    sub_cues_js = _build_subtitle_cues(sentences)

    # 收束变量只在真会演时才改写（0 值已在上下文里写好，_fill 的替换串同长，
    # 不存在的占位符不会被误替换）。
    if _settle_s > 0:
        rc.root_vars = rc.root_vars.replace(
            "--ctv-ved-settle:0.00s;", f"--ctv-ved-settle:{_settle_s:.2f}s;")
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
        # 时钟轨把 cue 时刻钉到帧网格（见 runtime.js 的 buildCueTrack）：渲染
        # 帧率改了这里也跟着变，所以必须在生成期落数字，不能写死 30
        "__CTV_FPS__": f"{float(fps):g}",
        "__CTV_COMPOSITION_ID__": COMPOSITION_ID,
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
        "__CTV_COMPOSITION_ID__": COMPOSITION_ID,
    }, "composition.html")

    return html


if __name__ == "__main__":
    # 与 build_from_structured 同一条守卫：本模块是库（CLI 编排在 run.py / pipeline.py），
    # 直接运行会静默 exit 0 且不产出任何文件——"没报错"会被误读成"成功了"。
    import sys as _sys
    _sys.exit("[error] html_renderer.py 是库模块，不能直接运行。"
              "成片渲染的入口是 scripts/run.py。")
