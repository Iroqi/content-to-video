#!/usr/bin/env python3
"""校验「代码里的数字」与「文档里写的数字」说的是同一件事。

第一性原理：文档里的数字是**代码常量的复制品**。复制品必然漂移——有人改了常量、
忘了改文档，于是两个人拿着不同的阈值做决策，而且谁也看不出来。靠人记得对齐是不
可行的，所以把这件事变成机器保证。

锁法是双向的，两头都堵：
  1. 代码这边改了、文档没改→ `expect` 与代码里的值不符，报错；
  2. 文档这边删了/改了这句说法、代码没动 → `doc` 正则在文档里匹配不到，报错。

两种取代码值的方式：
  ("const", 文件, 常量名)——顶层 `NAME = <字面量>` 赋值；
  ("json",  文件, 点分路径)——文件里那段内嵌 JSON 模板（`_template.py` 的
  `_TEMPLATE_JSON`）里逐层下钻，如 "layout.vertical.agenda.maxRows"。

登记标准（最小必要契约）：只登记**作者的决策阈值**——改了它，作者的写法就得
跟着变的那种数字（几何尺寸、告警阈值、字长上限、时间容差、版式钳制）。以下两
类**不登记**，它们在更近的一层自文档化：
  - 内部实现细节（canvas_kit 的刻度比例/基线修正、check_svg 的端点容差/圆点判据、
    局部底板明度等）：由 `canvas_kit.py` / `check_svg.py` 的报错与注释当场带出，
    改判据不影响作者写法，不构成"文档说了不算数"的风险。
  - 同一数字的重复表述：只在**一处权威文档**上锁（字段规范 / 版式表 / 参数表），
    症状表、正文复述不重复上锁——数字只有一个真源，重复锁只会放大文档改动的成本。
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# id, 取值方式, 期望值, 必须命中的文档与正则, 为什么它重要
FACTS = [
    # ── 画布几何：作者按这个尺寸出图/画 SVG，差一截就被 cover 裁掉或判比例不符 ──
    (
        "canvas-slot-vertical",
        ("json", "scripts/_template.py", "layout.vertical.image.width"), 980,
        [("references/canvas.md", r"\| 槽位（不写 `layout`，默认） \| 980×735 \| 1067×800 \|")],
        "槽位宽度（竖屏）；作者按 4:3 出图，差一截就被 cover 裁掉",
    ),
    (
        "canvas-slot-vertical-height",
        ("json", "scripts/_template.py", "layout.vertical.image.height"), 735,
        [("references/canvas.md", r"980[×x]735")],
        "同上，高度",
    ),
    (
        "canvas-slot-landscape",
        ("json", "scripts/_template.py", "layout.landscape.image.width"), 1067,
        [("references/canvas.md", r"\| 槽位（不写 `layout`，默认） \| 980×735 \| 1067×800 \|")],
        "槽位宽度（横屏）；画布 4:3 的权威出处",
    ),
    (
        "canvas-slot-landscape-height",
        ("json", "scripts/_template.py", "layout.landscape.image.height"), 800,
        [("references/canvas.md", r"1067[×x]800")],
        "同上，高度",
    ),
    (
        "canvas-full-vertical",
        ("json", "scripts/_template.py", "canvas.vertical.width"), 1080,
        [("references/canvas.md", r"1080[×x]1440"),
         ("SKILL.md", r"1080[×x]1440")],
        "整页画布竖屏原生宽度；照错尺寸画会被 check_svg 判比例不符",
    ),
    (
        "canvas-full-vertical-height",
        ("json", "scripts/_template.py", "canvas.vertical.height"), 1440,
        [("references/canvas.md", r"1080[×x]1440")],
        "同上，高度",
    ),
    (
        "canvas-full-landscape",
        ("json", "scripts/_template.py", "canvas.landscape.width"), 1920,
        [("references/canvas.md", r"1920[×x]1080"),
         ("SKILL.md", r"1920[×x]1080")],
        "整页画布横屏宽度",
    ),
    (
        "canvas-full-landscape-height",
        ("json", "scripts/_template.py", "canvas.landscape.height"), 1080,
        [("references/canvas.md", r"1920[×x]1080")],
        "同上，高度",
    ),
    # ── 素材与可读性：作者据此判断素材够不够糊、字号能不能读 ──
    (
        "upscale-warn",
        ("const", "scripts/_media_size.py", "UPSCALE_WARN"), 1.25,
        [("references/imagery.md", r"1\.25\s*倍")],
        "ImageGen 放大告警阈值：作者据此判断素材够不够糊",
    ),
    (
        "upscale-severe",
        ("const", "scripts/_media_size.py", "UPSCALE_SEVERE"), 2.0,
        [("references/imagery.md", r"2\.0\s*倍")],
        "严重档阈值；缺这一档说明少说了一级严重性",
    ),
    # ── 时间轴：作者据此判断字幕什么时候换、卡拉OK从哪儿开始数 ──
    (
        "speech-min-silence",
        ("const", "scripts/_speech.py", "MIN_SILENCE"), 0.05,
        [("references/tts_pipeline.md", r"50ms 是最短静音")],
        "发声区间的最短静音（秒）：它决定 `onset` 会不会把句尾换气吃进来，"
        "于是直接决定字幕换句的时刻——文档与代码分开时，作者按文档算出来的"
        "时间轴会和产物对不上",
    ),
    (
        "crop-warn",
        ("const", "scripts/_media_size.py", "CROP_WARN"), 0.20,
        [("references/imagery.md", r"裁掉超过\s*20%\s*画面")],
        "cover 裁切告警阈值：作者据此判断构图丢多少、出图该选哪档",
    ),
    (
        "min-font-px",
        ("const", "scripts/_template.py", "MIN_READABLE_FONT_PX"), 26,
        [("references/canvas.md", r"\*\*26px 是下限\*\*")],
        "图内字号下限的权威定义句；作者照此定字号，改了它所有字号建议都要重算",
    ),
    (
        "empty-band",
        ("const", "scripts/check_svg.py", "MAX_EMPTY_BAND"), 0.25,
        [("references/canvas.md", r"25%\s*页高")],
        "整页画布横向空带阈值（0.25 → 文档写百分比）",
    ),
    (
        "speaker-label-max",
        ("const", "scripts/_source_schema.py", "SPEAKER_LABEL_MAX_CHARS"), 6,
        [("references/writing.md", r"最多\s*6\s*字")],
        "说话人标签上墙字长上限；超了契约层直接报错，作者必须知道这个数",
    ),
    # ── 字幕焦点衰减：作者改曲线就是改这三份抄写（模板/运行时兜底/文档） ──
    (
        "focus-future",
        ("json", "scripts/_template.py", "subtitle.focus.future"), 0.62,
        [("references/rendering.md", r"d=1→0\.62")],
        "未念档的亮度；衰减曲线的锚点，composition.css 的兜底字面量与它必须一致",
    ),
    (
        "focus-past",
        ("json", "scripts/_template.py", "subtitle.focus.past"), 0.45,
        [("references/rendering.md", r"d=-1→0\.45")],
        "已念档的亮度；必须是 future×ratio（同一条曲线多走一档）",
    ),
    (
        "focus-ratio",
        ("json", "scripts/_template.py", "subtitle.focus.ratio"), 0.726,
        [("references/rendering.md", r"ratio 0\.726")],
        "每远一档的衰减比；0.62×0.726≈0.45 是「曲线本来就在那里」的证据",
    ),
    (
        "focus-scale",
        ("json", "scripts/_template.py", "subtitle.focus.scale"), 0.94,
        [("references/rendering.md", r"subtitle\.focus\.scale.{0,12}默认\s*`0\.94`"),
         ("references/rendering.md", r"0\.94³=0\.8306")],
        "离焦的第二个维度（尺寸）每远一档的缩放比；封两档是守住 26px 可读下限，"
        "改大改小都会动到「远处的行还读不读得出来」",
    ),
    (
        "focus-blur",
        ("json", "scripts/_template.py", "subtitle.focus.blur"), 0.02,
        [("references/rendering.md", r"subtitle\.focus\.blur.{0,12}默认\s*`0\.02`")],
        "离焦的第四个维度（景深）每远一档加多少模糊（em）；最深档 = 2 × 它，"
        "改大改小都会动到「远处的行还认不认得出字」",
    ),
    (
        "focus-blur-max",
        ("const", "scripts/_template.py", "FOCUS_BLUR_MAX_EM"), 0.05,
        [("references/rendering.md", r"FOCUS_BLUR_MAX_EM`（`0\.05em")],
        "景深的糊掉线（em）：封顶档的模糊半径不得越过它，半径逼近笔画宽度"
        "（≈0.07em）时相邻笔画并成一片",
    ),
    # ── 配音与时间：作者写稿时按这些数填 speed/gap、预期转场宽度 ──
    (
        "default-speed",
        ("const", "scripts/_timeline.py", "DEFAULT_SPEED"), 1.5,
        [("references/tts_pipeline.md", r"默认\s*`?1\.5")],
        "全局语速默认；写稿时不看代码就按这个填 speed",
    ),
    (
        "opening-closing-speed",
        ("const", "scripts/_timeline.py", "OPENING_CLOSING_DEFAULT_SPEED"), 1.2,
        [("references/tts_pipeline.md", r"开场/结尾用\s*`?1\.2")],
        "开屏/结尾建议语速；与正文默认值不同，是一条独立建议",
    ),
    (
        "default-gap",
        ("const", "scripts/_timeline.py", "DEFAULT_GAP"), 0.4,
        [("references/tts_pipeline.md", r"默认\s*`?0\.4")],
        "句间静音默认；直接影响转场宽度",
    ),
    (
        "timeline-tolerance",
        ("const", "scripts/_timeline.py", "TIMELINE_TOLERANCE"), 0.25,
        [("references/tts_pipeline.md", r"250ms")],
        "末句不得超出 total_duration 的容差（0.25s → 文档写 250ms）",
    ),
    (
        "duration-tolerance-floor",
        ("const", "scripts/_timeline.py", "DURATION_TOLERANCE_FLOOR_S"), 1.0,
        [("SKILL.md", r"max\(1s"),
         ("references/rendering.md", r"max\(1s, 2%\)")],
        "实测长度对账的绝对容差下限：短片按绝对量放行，不按 2% 放大到不足一秒",
    ),
    (
        "duration-tolerance-ratio",
        ("const", "scripts/_timeline.py", "DURATION_TOLERANCE_RATIO"), 0.02,
        [("SKILL.md", r"2%\)"),
         ("references/rendering.md", r"max\(1s, 2%\)")],
        "同上，相对档：长片按比例放宽。音频回落闸与成片时长闸共用这一档",
    ),
    # ── 写稿预算：超过直接不上画面 / 直接被拒，作者写稿时就知道 ──
    (
        "agenda-max-rows",
        ("json", "scripts/_template.py", "layout.vertical.agenda.maxRows"), 7,
        [("references/writing.md", r"7\s*行")],
        "开屏/结尾卡总行数硬上限；两画幅同值",
    ),
    (
        "agenda-name-trim-portrait",
        ("json", "scripts/_template.py", "layout.vertical.agenda.nameTrim"), 20,
        [("references/writing.md", r"竖屏\s*20\s*字")],
        "agenda 行名竖屏截断字数；超了直接不上画面且无省略号",
    ),
    (
        "agenda-name-trim-landscape",
        ("json", "scripts/_template.py", "layout.landscape.agenda.nameTrim"), 26,
        [("references/writing.md", r"横屏\s*26\s*字")],
        "同上，横屏",
    ),
    (
        "long-sentence-warn",
        ("const", "scripts/build_from_structured.py", "LONG_SENTENCE_CHARS"), 45,
        [("references/writing.md", r"45\s*字")],
        "逐句告警的字长阈值",
    ),
    (
        "long-sentence-hint",
        ("const", "scripts/build_from_structured.py", "LONG_SENTENCE_HINT_CHARS"), 32,
        [("references/writing.md", r"32[–-]45\s*字")],
        "段落级 hint 的字长下界",
    ),
    # ── 环境与版式：SKILL.md 环境表与画面结构手写的数 ──
    (
        "min-node-major",
        ("const", "scripts/_render_backend.py", "MIN_NODE_MAJOR"), 22,
        [("SKILL.md", r"Node\.js\s*22\+")],
        "渲染前置条件：Hyperframes CLI 的 engines 写死 node>=22",
    ),
    (
        "tts-default-model",
        ("const", "scripts/_tts.py", "DEFAULT_MODEL"), "mimo-v2.5-tts",
        [("SKILL.md", r"mimo-v2\.5-tts")],
        "TTS 默认模型；SKILL.md「环境」节写了默认值",
    ),
    (
        "landscape-text-col-width",
        ("json", "scripts/_template.py", "layout.landscape.textCol.width"), 613,
        [("references/rendering.md", r"613px")],
        "横屏左文字栏宽度；rendering.md「画面结构」手写同数",
    ),
    (
        "subtitle-scroll-ease",
        ("json", "scripts/_template.py", "subtitle.scroll.ease"), "sine.out",
        [("references/rendering.md", r"`subtitle\.scroll\.ease`（默认 `sine\.out`）")],
        "字幕滚动的缓动档：作者改了它，成片里滚动的手感就换了——"
        "文档必须跟着说清当前是哪一档",
    ),
    (
        "subtitle-relay-from",
        ("json", "scripts/_template.py", "subtitle.relay.from"), 0.35,
        [("references/rendering.md",
          r"模板 `subtitle\.relay\.from`，默认 `0\.35`")],
        "入场浮出的起点：只叠给当前行。作者调它等于调换句那一下的强弱，"
        "文档必须跟着说清当前是哪一档",
    ),
    # ── 画布三档字号（竖屏/横屏各锁一组，label 档=刻度线长的分母）──
    (
        "canvas-tier-title-v",
        ("json", "scripts/_template.py", "layout.vertical.title.fontSize"), 64,
        [("references/canvas.md", r"64/40/32")],
        "画布三档字号 title 档（竖屏）；画布页没有 HTML 标题层，图内文字是唯一的字",
    ),
    (
        "canvas-tier-title-l",
        ("json", "scripts/_template.py", "layout.landscape.title.fontSize"), 62,
        [("references/canvas.md", r"62/33/28")],
        "同上，横屏 title 档",
    ),
    # ── 手绘旋钮：作者按这个上限取 roughness，超过只是噪声 ──
    (
        "canvas-roughness-max",
        ("const", "scripts/canvas_kit.py", "ROUGHNESS_MAX"), 10,
        [("references/canvas.md", r"roughness` 0[–-]10")],
        "canvas_kit 手绘抖动上限（与官方 rough.js 同款口径）：超过只是噪声，"
        "作者按它取 roughness",
    ),
]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _const(rel, name):
    """取顶层赋值 `NAME = <字面量>` 的值。"""
    tree = ast.parse(_read(rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError("%s 里找不到常量 %s" % (rel, name))


def _json(rel, path):
    """在文件内嵌的 JSON 模板里按点分路径下钻，如 layout.vertical.agenda.maxRows。"""
    tree = ast.parse(_read(rel))
    blob = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            try:
                candidate = json.loads(node.value.value)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                blob = candidate
                break
    if blob is None:
        raise KeyError("%s 里找不到内嵌 JSON 模板" % rel)
    cur = blob
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError("%s 的 JSON 模板里找不到路径 %s 的一环 %s" % (rel, path, key))
        cur = cur[key]
    return cur


def read_code_value(spec):
    kind = spec[0]
    if kind == "const":
        return _const(spec[1], spec[2])
    if kind == "json":
        return _json(spec[1], spec[2])
    raise ValueError("未知的取值方式 %r" % (kind,))


def main():
    problems = []
    for fid, spec, expect, docs, why in FACTS:
        # 第一头锁：代码实际值必须等于登记的期望值
        try:
            actual = read_code_value(spec)
        except (KeyError, ValueError, SyntaxError) as exc:
            problems.append("[code-missing] %s：读不到代码里的值（%s）" % (fid, exc))
            continue
        if actual != expect:
            problems.append(
                "[code-drift] %s：%s = %r，但文档按 %r 写着。改常量就要同步改文档"
                % (fid, " ".join(spec[1:]), actual, expect)
            )
            continue
        # 第二头锁：文档必须还留着这句说法
        for rel, pattern in docs:
            try:
                text = _read(rel)
            except OSError as exc:
                problems.append("[doc-missing] %s：读不到 %s（%s）" % (fid, rel, exc))
                continue
            if not re.search(pattern, text):
                problems.append(
                    "[doc-drift] %s：%s 里找不到 /%s/（%s）"
                    % (fid, rel, pattern, why)
                )

    for line in problems:
        print(line)
    if problems:
        print("\n数字契约漂移 %d 处。改代码就改文档，别只改一边。" % len(problems))
        return 1
    print("数字契约一致：%d 条决策阈值全部等于登记值，文档表述均在场。" % len(FACTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
