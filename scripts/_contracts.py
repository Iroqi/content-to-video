#!/usr/bin/env python3
"""跨脚本文件契约的显式化：加载 + 校验中间产物。

各脚本通过文件（segments_source.json / timing_manifest.json / images.json）
通信契约曾长期隐式存在；字段缺失或类型错误会导致难以定位的报错
或静默降级收场。这里把"必需字段"写成校验函数，缺什么报什么。
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _theme import is_safe_css_color  # noqa: E402  _theme 不反向依赖本模块

# 段落 id 的合法形态见 _SID_RE / SID_RULE。之所以要收口成一条正则而不是各处
# 宽松判断：id 会被 gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP
# 选择器字符串（tl.fromTo("#{sid}",...)）——手写 manifest 里带引号/点号/方
# 括号的 sid 轻则选择器匹配失败动画静默丢失，重则内联 <script> 整段
# SyntaxError、字幕同步与时间轴注册全部死亡且无报错。在契约层收口校验。
_SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# 同一条规则的对外说法。报错文案在 4 个调用点（source/manifest 的
# _validate_sid、images.json key、html_renderer 的两道兜底）各写一份汉字副本时
# 已经漂过三次（"下划线/连字符" vs "-/_"、有无"1–64
# 字符"）。规则本身仍以 _SID_RE 为准，这里只是给人看的那一句的唯一副本，
# 改正则必须同步这句。
SID_RULE = "字母开头，仅字母/数字/-/_，1–64 字符"
# accent 的合法形态见 _theme.is_safe_css_color（hex 或 CSS 标准颜色名），
# 白名单之外不进生成 HTML。

# ── 内容段落 id 约定──────────────────────────────────────────────
# "哪些段落需要配图" 由 sid 前缀决定：opening/closing 是结构性段落，天生
# 不需要配图。判断"是不是内容段"一律调 is_content_sid()，不要再写一份
# startswith（结构性页反过来按 id 字面量点名）。漏掉一处的后果是静默改变
# 配图覆盖率统计口径（把不该算的算进去，或该配图的段落被跳过拦截）。
CONTENT_SID_PREFIX = "seg"


def is_valid_sid(sid):
    """sid 是否是合法段 id（规则见 SID_RULE）。

    这些 id 会拼进 HTML 属性、JS 对象键与 GSAP 选择器（见 _SID_RE 注释）。
    CLI 路径由 _validate_sid/validate_images_json 强制；库调用方直接传 dict
    给 html_renderer 时也用它兜底，避免"单点依赖 CLI 校验"。
    """
    return isinstance(sid, str) and bool(_SID_RE.match(sid))


def is_content_sid(sid):
    """该 sid 是否属于"需要配图的内容段落"（排除 opening/closing）。"""
    return (sid or "").startswith(CONTENT_SID_PREFIX)


def validate_speed(speed):
    """校验语速倍率必须是 >0 的有限数值，非法时抛 ValueError。

    跨脚本领域规则收口在这里（与 DEFAULT_SPEED 同一模块）：pipeline 的
    --speed 入口、本模块的 segments_source 值校验、_audio.build_atempo_filter
    的入口守卫共用这一份判断，避免多处各写一份而漂移。
    """
    if not isinstance(speed, (int, float)) or isinstance(speed, bool):
        raise ValueError(f"speed 必须是数值（收到 {speed!r}）")
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError(f"speed 必须是大于 0 的有限数值（收到 {speed!r}）")
    return speed


# 语速"是否偏离原速 1.0"的判定阈值 + .spd marker 的存储精度：pipeline 的变
# 速分支与写 marker、resume 状态机、_audio 的 atempo 入口共用同一判断。原来
# 六处各写一份 `abs(speed-1.0) > 0.01` / `round(speed, 4)`，改一处就会漂移。
SPEED_EPS = 0.01
SPEED_MARKER_DIGITS = 4


def needs_speed_change(speed):
    """本次语速是否要真的施加 atempo 变速（偏离 1.0 超过阈值）。"""
    return abs(speed - 1.0) > SPEED_EPS


def speed_marker_value(speed):
    """.spd marker 里写入的语速值：统一精度，供 --resume 对账。"""
    return round(speed, SPEED_MARKER_DIGITS)


# ── 跨脚本默认值──────────────────────────────────────────────────
# 各写一份且互不一致：estimate 相对 pipeline 实测系统性偏长约 50%、
# "默认 TTS 倍速"的入口一律从这里取，改时只改这一处。
DEFAULT_SPEED = 1.5
# opening/closing 段的默认语速：比正文略慢，凸显开场/收尾的节奏。
# build_from_structured 造段配置时写入，pipeline 的时长估算/静音占位经
# 段落 speed 复用同一假设——两处必须一致，所以也收口在这里。
OPENING_CLOSING_DEFAULT_SPEED = 1.2

# ── 时长估算跨脚本默认值──────────────────────────────────────────
# DEFAULT_CHARS_PER_SEC / DEFAULT_GAP / estimate_sentence_seconds 放这里而不是
# 原来的模块，是为了不让核心管线反向依赖一个可选 CLI 工具（与 DEFAULT_SPEED 同理）。
DEFAULT_CHARS_PER_SEC = 4.3  # "语速适中"参考值，纯启发式
# 句间停顿默认值：pipeline 的 --gap 直接 import 这里作默认，所以只有一处。
# 估算与实测的口径差会累积成 (n-1)×Δ 的系统性偏移。
DEFAULT_GAP = 0.4

# 时间轴对账的唯一容差。pipeline 的"拼接产物不得比预期短 250ms"与下面
# validate_timing_manifest 的"末句不得超出 total_duration 250ms"是同一个量级
# 判断：分成两处写字面量就会漂移——上游放宽到 0.3s、下游仍按 0.25s 拒收，
# 等于 TTS 额度烧完才告诉用户产物不能用。
TIMELINE_TOLERANCE = 0.25

def estimate_sentence_seconds(sentence, chars_per_sec, speed):
    """单句预计时长（秒）：字数 / 语速 / 倍速。"""
    return len(sentence) / chars_per_sec / max(speed, 0.01)


def _validate_accent(value, where):
    """accent 取值校验（可选字段）：必须是浏览器认得的颜色（hex 或 CSS 标准色名）。

    为什么必须白名单、放行哪两种形态、挡掉哪些字符，见 _theme.is_safe_css_color
    ——判定规则也在那里，此处不另写一份正则。
    """
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 的 'accent' 必须是非空字符串"
                         f"（如 #2dd4bf），实际: {value!r}")
    v = value.strip()
    if not is_safe_css_color(v):
        raise ValueError(
            f"{where} 的 accent={value!r} 不是合法颜色：只接受 #rgb / #rrggbb "
            f"十六进制（如 #2dd4bf）或浏览器标准色名（如 red、tomato）。"
            f"accent 会拼进成片 HTML，不接受 rgb()/var() 这类函数式写法")


# 段落版式。不写 layout = 槽位版式（标题区 + 配图槽 + 句子流）；"canvas" =
# 整页画布（配图拉满全屏，HTML 的标题层与句子流层都不渲染，标题与文字由画布
# 自己画）。版式后果见 references/rendering.md「画面结构」，画这张图的规格见
# references/image_options.md「整页画布」。
LAYOUT_VALUES = ("canvas",)


def _validate_layout(value, where, *, agenda=False):
    """layout 取值校验（可选字段）。严格匹配：不做大小写归一、不 strip。
    放宽才是灾难——把 'Canvas' 猜成 'canvas' 猜错方向的那一段会静默按槽位版式
    渲染，画布自己画的标题和 HTML 标题层并排出现在同一帧。宁可在这里报错。

    agenda=True（结构性段 opening/closing）时任何 layout 值都是 error：开屏/结尾
    是纯文字 agenda 卡，画布版式不生成标题层与句子流层，写上去等于把开场标题或
    结尾行动号召从画面上删掉——而 `check` 只数 HTML 文本，看不见这种丢失。
    """
    if value is None:
        return
    if agenda:
        raise ValueError(
            f"{where} 写了 layout={value!r}，但 opening/closing 是结构性 agenda 卡"
            "（只有文字版式）——layout 只对内容段有效：画布版式不生成标题层与"
            "句子流层，用在结构性段上会把开场标题/结尾行动号召整个抹掉")
    if not isinstance(value, str) or value not in LAYOUT_VALUES:
        raise ValueError(
            f"{where} 的 layout={value!r} 不是合法版式：只接受 "
            f"{'、'.join(repr(v) for v in LAYOUT_VALUES)}，或整个不写"
            "（不写 = 槽位版式：标题区 + 配图槽 + 句子流）")


def _validate_text(value, where, *, required=False):
    """Validate a user-facing text field before any downstream ``.strip()``/HTML use."""
    if value is None:
        if required:
            raise ValueError(f"{where} 必须是字符串")
        return
    if not isinstance(value, str):
        raise ValueError(f"{where} 必须是字符串（实际: {type(value).__name__}）")
    if required and not value.strip():
        raise ValueError(f"{where} 不能为空字符串")


def _validate_finite_number(value, where, *, nonnegative=False):
    """Validate numeric manifest fields without accepting booleans or NaN/Inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} 必须是数值（实际: {value!r}）")
    if not math.isfinite(float(value)):
        raise ValueError(f"{where} 必须是有限数值（实际: {value!r}）")
    if nonnegative and float(value) < 0:
        raise ValueError(f"{where} 必须大于等于 0（实际: {value!r}）")


def _validate_sid(sid, where, seen):
    """段落 id 校验：合法字符集 + 跨段唯一。

    两个调用点都只在 id 非空白时才调进来（source 侧跳过缺省/空白 id，
    manifest 侧先要求非空字符串），这里不再兼容 None/空串——收到它们
    说明调用点写错了，直接走下面的报错。
    """
    if not is_valid_sid(sid):
        raise ValueError(
            f"{where} 的 id={sid!r} 含非法字符或格式不对——id 的合法形态是"
            f"{SID_RULE}（如 seg1、opening），"
            f"因为它会被拼进 HTML 属性与 GSAP 选择器")
    if sid in seen:
        raise ValueError(f"{where} 的 id={sid!r} 与前面的段落重复"
                         f"（选择器会互相串台）")
    seen.add(sid)


def validate_segments_source(data):
    """segments_source.json：需要非空的 segments 列表，opening/closing 可选。

    每个 segment 需要 'text'（单人独白）或 'dialogue'（双人/多人对话）二选一。
    使用 'dialogue' 时，顶层必须有 'speakers' 声明每个说话人的 voice_id。

    除了结构校验，还做取值校验：speed 必须是 >0 的有限数值、voice_id 必须是
    预置音色之一。这样坏参数在进 TTS 之前就 fail-fast，而不是每句重试到超时。
    """
    if not isinstance(data, dict):
        raise ValueError("segments_source.json 顶层必须是 JSON 对象")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments_source.json 需要非空的 'segments' 列表"
                         "（至少一条内容段落）")

    for key in ("title", "opening_title", "closing_title",
                "opening_tagline", "closing_tagline", "opening", "closing"):
        _validate_text(data.get(key), f"segments_source.json 的 '{key}'")

    # ── 值校验：speed / voice_id ───────────────────────────────────
    for key in ("opening_speed", "closing_speed"):
        if data.get(key) is not None:
            try:
                validate_speed(data[key])
            except ValueError as e:
                raise ValueError(f"segments_source.json 的 '{key}' 非法：{e}") from e

    # 结尾 agenda 尾行（可选，至多一条）：cta=行动号召或下期预告二选一。渲染时直接
    # 进 HTML 文本，只接受非空字符串（esc 之后），坏类型在进 TTS 前报对人。
    # 单行是硬约束（writing.md）：agenda 的行数预算按"一行"计，含换行的 cta
    # 会挤爆预算、让"先丢 cta 再裁正文"的截断优先级失效。
    if data.get("cta") is not None:
        if not isinstance(data["cta"], str):
            raise ValueError("segments_source.json 的 'cta' 必须是字符串"
                             "（结尾 agenda 的尾行文字，可选字段）")
        if "\n" in data["cta"] or "\r" in data["cta"]:
            raise ValueError("segments_source.json 的 'cta' 必须是单行文字"
                             "（结尾 agenda 尾行只占一行，不要含换行符）")

    speakers = data.get("speakers")
    if speakers is not None and not isinstance(speakers, dict):
        raise ValueError("segments_source.json 的 'speakers' 必须是对象"
                         "（{说话人 key: {voice_id, voice_style?, label?}}）")
    if speakers:
        for spk, spk_cfg in speakers.items():
            if not isinstance(spk_cfg, dict):
                raise ValueError(f"segments_source.json 的 speakers['{spk}'] 必须是对象"
                                 "（{voice_id, voice_style?, label?}）")
            vid = spk_cfg.get("voice_id")
            if vid is None or not isinstance(vid, str) or not is_valid_voice_id(vid):
                raise ValueError(
                    f"segments_source.json 的 speakers['{spk}'].voice_id={vid!r} "
                                     f"不在预置音色里（可用：{', '.join(list_voice_ids())}）")
            for _key in ("label", "voice_style"):
                _validate_text(spk_cfg.get(_key),
                               f"segments_source.json 的 speakers['{spk}'].{_key}")

    seen_source_ids = set()
    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, dict):
            raise ValueError(f"segments[{i}] 必须是对象")
        _validate_text(seg.get("title"), f"segments[{i}].title", required=True)
        _validate_text(seg.get("tagline"), f"segments[{i}].tagline")
        # 可选的稳定 id：SKILL.md 承诺"给段稳定 id，改稿顺序不乱时间轴锚点"。
        # 必须是内容段前缀（seg…），否则下游配图覆盖率/统计把它当结构性页；
        # 必须跨段唯一，否则 HTML 选择器串台。
        seg_id = seg.get("id")
        if seg_id is not None and str(seg_id).strip() != "":
            _validate_sid(seg_id, f"segments[{i}]（title={seg.get('title')!r}）",
                          seen_source_ids)
            if not is_content_sid(seg_id):
                raise ValueError(
                    f"segments[{i}] 的 id={seg_id!r} 必须以 {CONTENT_SID_PREFIX}… 开头"
                    "（如 seg1-openai），opening/closing 为结构性页保留")
        if seg.get("speed") is not None:
            try:
                validate_speed(seg["speed"])
            except ValueError as e:
                raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                                 f"的 'speed' 非法：{e}") from e
        if seg.get("voice_id") is not None and (
                not isinstance(seg["voice_id"], str)
                or not is_valid_voice_id(seg["voice_id"])):
            raise ValueError(
                f"segments[{i}]（title={seg.get('title')!r}）的 voice_id="
                f"{seg['voice_id']!r} 不在预置音色里"
                f"（可用：{', '.join(list_voice_ids())}）")
        if seg.get("takeaway") is not None:
            if not isinstance(seg["takeaway"], str):
                raise ValueError(
                    f"segments[{i}]（title={seg.get('title')!r}）的 'takeaway' 必须是字符串"
                                     "（结尾 agenda 要点总结用的一句话结论，可选字段）")
            if "\n" in seg["takeaway"] or "\r" in seg["takeaway"]:
                raise ValueError(
                    f"segments[{i}]（title={seg.get('title')!r}）的 'takeaway' 必须是单行"
                    "（结尾 agenda 每段只占一行，含换行会挤爆行数预算）")
        _validate_text(seg.get("voice_style"),
                       f"segments[{i}]（title={seg.get('title')!r}）的 voice_style")
        _validate_accent(seg.get("accent"),
                         f"segments[{i}]（title={seg.get('title')!r}）")
        _validate_layout(seg.get("layout"),
                         f"segments[{i}]（title={seg.get('title')!r}）")
        dialogue = seg.get("dialogue")
        # 类型必须在这里拦下：非列表的 truthy dialogue（字符串/对象）若放行，
        # 会绕过 has_dialogue 判定、又在 build_from_structured 的 if dialogue:
        # 真值分支里被当 turns 迭代，turn.get() 直接 AttributeError 裸栈。
        if dialogue is not None and not isinstance(dialogue, list):
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）的 "
                             "'dialogue' 必须是数组 [{speaker, text}, ...]")
        has_dialogue = isinstance(dialogue, list) and len(dialogue) > 0
        if seg.get("text") is not None and not isinstance(seg.get("text"), str):
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）的 'text' 必须是字符串")
        has_text = isinstance(seg.get("text"), str) and bool(seg.get("text").strip())
        if not has_text and not has_dialogue:
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                             "需要 'text'（独白）或 'dialogue'（对话）字段之一")
        if has_text and has_dialogue:
            raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                             "'text' 和 'dialogue' 只能二选一，不要同时提供")
        if has_dialogue:
            if not speakers:
                raise ValueError(f"segments[{i}]（title={seg.get('title')!r}）"
                                 "使用了 'dialogue'，但顶层缺少 'speakers' 字段"
                                 "（需要为每个说话人声明 voice_id）")
            for j, turn in enumerate(dialogue, 1):
                if not isinstance(turn, dict):
                    raise ValueError(f"segments[{i}].dialogue[{j}] 必须是对象 {{speaker, text}}")
                spk = turn.get("speaker")
                if not isinstance(spk, str) or not spk.strip():
                    raise ValueError(f"segments[{i}].dialogue[{j}] 缺少 'speaker' 字段")
                if not isinstance(turn.get("text"), str) or not turn.get("text").strip():
                    raise ValueError(f"segments[{i}].dialogue[{j}]（speaker={spk!r}）缺少 'text' 字段")
                spk_cfg = speakers.get(spk)
                if not isinstance(spk_cfg, dict) or not spk_cfg.get("voice_id"):
                    raise ValueError(f"segments[{i}].dialogue[{j}] 引用了说话人 {spk!r}，"
                                     "但顶层 'speakers' 里没有声明它，或声明里缺少 'voice_id'")
    return data


def _read_json_file(path):
    """读一份用户/上游写出的 JSON 文件，把三种读不出来统一成带路径的 ValueError。

    OSError 不是 ValueError 子类：不转的话调用方的 ``except ValueError`` 接不住，
    路径打错就抛裸栈——而路径打错是最常见的用户输入错误。

    utf-8-sig：Windows 记事本 / PowerShell `Set-Content -Encoding UTF8` 存出来
    的合法 JSON 带 BOM，纯 utf-8 解码会让 json 抛 "Unexpected UTF-8 BOM"，把一份
    没写错的稿子判成语法错误。非 UTF-8（GBK/UTF-16 无 BOM）仍然失败，但转成点名
    文件 + 给出"另存为 UTF-8"的可执行指令，而不是裸的编解码报错。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"无法读取 {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise ValueError(
            f"{path} 不是 UTF-8 编码的 JSON（{e}）——请另存为 UTF-8 后重试；"
            "Windows 记事本/PowerShell 保存时选 UTF-8（不带 BOM 也行）") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} 不是合法 JSON: {e}") from e
    return data


def load_segments_source(path):
    """读取并校验 segments_source.json（读取失败统一成带路径的 ValueError）。"""
    return validate_segments_source(_read_json_file(path))


def validate_timing_manifest(data):
    """timing_manifest.json：sentences（非空）与 total_duration（数值）必填。"""
    if not isinstance(data, dict):
        raise ValueError("timing_manifest.json 顶层必须是 JSON 对象")
    # 结尾 agenda 尾行（pipeline 从 segments_source 顶层 cta 透传）：
    # 同样只接受单行字符串，renderer 直接切片/转义。
    if data.get("closing_cta") is not None:
        if not isinstance(data["closing_cta"], str):
            raise ValueError("timing_manifest.json 的 'closing_cta' 必须是字符串")
        if "\n" in data["closing_cta"] or "\r" in data["closing_cta"]:
            raise ValueError("timing_manifest.json 的 'closing_cta' 必须是单行文字")
    if data.get("combined_audio") is not None and not isinstance(data["combined_audio"], str):
        raise ValueError("timing_manifest.json 的 'combined_audio' 必须是字符串")
    # voice_id 与 source 侧同一白名单口径：pipeline 写入的是本次真实用到的
    # 音色逗号拼接串（多音色对话稿），逐 token 都要落在预置音色里。手写
    # manifest 塞任意串会在下游报告/缓存指纹里当合法音色用。
    if data.get("voice_id") is not None:
        vid = data["voice_id"]
        if not isinstance(vid, str) or not all(
                is_valid_voice_id(v) for v in vid.split(",")):
            raise ValueError(
                f"timing_manifest.json 的 voice_id={vid!r} 含不在预置音色里的值"
                f"（单个音色或逗号拼接串均可；可用：{', '.join(list_voice_ids())}）")
    if data.get("gap") is not None:
        _validate_finite_number(data["gap"], "timing_manifest.json 的 gap",
                                nonnegative=True)
    # schema_version 与 status 都是必填：二者只有 pipeline.py 的两个合法取值
    # （2 / ok|degraded），"缺字段"从来不是有效状态而是漏写或旧版产物。允许
    # schema_version 缺席会留一条逃逸口——手写 manifest 少写它就能绕开 status
    # 校验，把降级产物伪装成正常成片交给下游（降级显式化是硬规则）。
    schema_version = data.get("schema_version")
    if schema_version != 2:
        raise ValueError("timing_manifest.json 必须声明 schema_version: 2"
                         f"（实际: {schema_version!r}）——缺字段说明它出自更早版本的"
                         " pipeline 或手写时漏写，重跑 pipeline.py 生成新 manifest")
    status = data.get("status")
    if status not in ("ok", "degraded"):
        raise ValueError("timing_manifest.json 的 status 必须是 'ok' 或 'degraded'"
                         f"（实际: {status!r}）")
    degraded = data.get("degraded")
    if degraded is not None and not isinstance(degraded, dict):
        raise ValueError("timing_manifest.json 的 degraded 必须是对象")
    if status == "degraded" and not degraded:
        raise ValueError("timing_manifest.json 标记为 degraded 时必须提供非空 degraded 详情")

    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("timing_manifest.json 需要非空的 'sentences' 列表")
    total_duration = data.get("total_duration")
    if isinstance(total_duration, bool) or not isinstance(total_duration, (int, float)):
        raise ValueError("timing_manifest.json 缺少数值字段 'total_duration'"
                         "（应为 ffmpeg 实测总时长）")
    if not math.isfinite(float(total_duration)) or float(total_duration) <= 0:
        raise ValueError("timing_manifest.json 的 total_duration 必须是正有限数值")

    def _check_sentence_fields(s, where):
        if not isinstance(s, dict):
            raise ValueError(f"{where} 必须是对象")
        for key in ("index", "text", "start_time", "duration"):
            if key not in s:
                raise ValueError(f"{where} 缺少字段 '{key}'"
                                 "（pipeline.py 产出格式）")
        if not isinstance(s["text"], str) or not s["text"].strip():
            raise ValueError(f"{where}.text 必须是非空字符串")
        idx = s.get("index")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise ValueError(f"{where}.index 必须是非负整数")
        start = s.get("start_time")
        duration = s.get("duration")
        if (isinstance(start, bool) or not isinstance(start, (int, float))
                or not math.isfinite(float(start)) or float(start) < 0):
            raise ValueError(f"{where}.start_time 必须是非负有限数值")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(float(duration)) or float(duration) <= 0):
            raise ValueError(f"{where}.duration 必须是正有限数值")
        if ("speaker" in s and s["speaker"] is not None
                and (not isinstance(s["speaker"], str) or not s["speaker"].strip())):
            raise ValueError(f"{where}.speaker 必须是非空字符串")
        if "synth_failed" in s and not isinstance(s["synth_failed"], bool):
            raise ValueError(f"{where}.synth_failed 必须是布尔值")
        if float(start) + float(duration) > float(total_duration) + TIMELINE_TOLERANCE:
            raise ValueError(f"{where} 超出 total_duration"
                             f"（允许 {TIMELINE_TOLERANCE * 1000:.0f}ms 浮点/编码裕量）")

    seen_indices = set()
    top_by_index = {}
    prev_start = None
    prev_end = None
    for i, s in enumerate(sentences):
        _check_sentence_fields(s, f"sentences[{i}]")
        idx = s["index"]
        if idx in seen_indices:
            raise ValueError(f"timing_manifest.json 的 sentence index 重复：{idx}")
        seen_indices.add(idx)
        top_by_index[idx] = s
        start = float(s["start_time"])
        if prev_start is not None and start + 1e-6 < prev_start:
            raise ValueError("timing_manifest.json 的 sentences 必须按 start_time 升序排列")
        # 相邻两句不得重叠：只查升序的话，[0,5) + 2.0 起步的第二句照样通过，
        # 于是同一时刻有两句字幕，下游按 start_time 算的 cue 时长还会算出
        # 负数。10ms 容差只够吸收 start/duration 各自 round(x,3) 的舍入，
        # 真重叠（哪怕 50ms）依然拦得住。
        if (prev_end is not None
                and start + 0.01 < prev_end):
            raise ValueError(
                f"timing_manifest.json 的 sentences[{i}]（index {idx}）与上一句"
                f"重叠：上一句到 {prev_end:.3f}s，本句 {start:.3f}s 就开始了。"
                "pipeline.py 产出的时间轴句间至少隔 --gap，出现重叠一般是"
                "手工裁剪/改过 start_time 或 duration——请把后一句整体往后挪")
        prev_end = start + float(s["duration"])
        prev_start = start
    failed_indices = {s["index"] for s in sentences if s.get("synth_failed")}
    if failed_indices and status == "ok":
        raise ValueError(
            "timing_manifest.json 含 synth_failed 句子，却标记为 status=ok；"
            "请重跑 pipeline，或将降级状态正确标记为 degraded")
    # segments 若提供，每段必须自带非空 sentences 列表——
    # segments 必填且非空：画面标题/tagline/accent 与开屏/结尾 agenda 只从这些分组
    # 取，没有它就没有内容段版式可渲染。段内 sentences 一并显性化：
    # gen_hyperframes.py 对 seg["sentences"] 是直接下标访问，缺失时炸裸
    # KeyError: 'sentences'，不指向真正缺的键——手写/裁剪 manifest 的报错
    # 必须第一时间报对人。
    segs = data.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError(
            "timing_manifest.json 需要非空的 'segments' 列表（分组渲染的数据源："
            "画面标题/tagline/accent 与开屏/结尾 agenda 只从这里取；pipeline.py 恒写"
            "该字段，手写/裁剪 manifest 请按格式给出分组）")
    _seen_sids = set()
    assigned_indices = set()
    previous_segment_end = None
    for i, sg in enumerate(segs):
        if not isinstance(sg, dict):
            raise ValueError(f"segments[{i}] 必须是对象")
        sid = sg.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"segments[{i}].id 必须是非空字符串")
        if not (sid == "opening" or sid == "closing" or is_content_sid(sid)):
            raise ValueError(
                f"segments[{i}].id={sid!r} 必须是 opening/closing 或以 "
                f"{CONTENT_SID_PREFIX} 开头")
        # id 会拼进 HTML 属性与 GSAP 选择器字符串（见 _SID_RE 注释），
        # 手写 manifest 里坏 sid 的失败模式是"动画静默丢失/整段脚本
        # 语法错误"，必须在契约层报对人
        _validate_sid(sg.get("id"), f"segments[{i}]", _seen_sids)
        _validate_text(sg.get("title"), f"segments[{i}].title", required=True)
        _validate_text(sg.get("tagline"), f"segments[{i}].tagline")
        _validate_accent(sg.get("accent"), f"segments[{i}]（{sg.get('id', '?')}）")
        _validate_layout(sg.get("layout"),
                         f"timing_manifest.json 的段落 '{sg.get('id', '?')}'",
                         agenda=not is_content_sid(sid))
        # agenda 数据源字段（渲染层直接读 manifest）：类型错会在
        # renderer 的 [:trim] 切片处炸裸 TypeError，这里提前报对人。
        if sg.get("takeaway") is not None:
            if not isinstance(sg["takeaway"], str):
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' 的 "
                    "'takeaway' 必须是字符串")
            # 与 source 侧同一单行约束（writing.md）：manifest 可手写，
            # 含换行的 takeaway 会挤爆结尾 agenda 的行数预算
            if "\n" in sg["takeaway"] or "\r" in sg["takeaway"]:
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' 的 "
                    "'takeaway' 必须是单行（含换行会挤爆 agenda 行数预算）")
        # 段级 voice_id 与顶层同一白名单口径（pipeline 从已校验的 source
        # 透传，手写 manifest 是另一条入口）：坏音色名混进来只会在配音
        # 错位时才被发现
        _seg_vid = sg.get("voice_id")
        if _seg_vid is not None:
            if (not isinstance(_seg_vid, str)
                    or not all(is_valid_voice_id(v)
                               for v in _seg_vid.split(","))):
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sid}' 的 voice_id="
                    f"{_seg_vid!r} 含不在预置音色里的值"
                    f"（单个音色或逗号拼接串均可；可用：{', '.join(list_voice_ids())}）")
        ss = sg.get("sentences")
        if not isinstance(ss, list) or not ss:
            sid = sg.get("id", f"segments[{i}]")
            raise ValueError(
                f"timing_manifest.json 的段落 '{sid}' 缺少非空 "
                f"'sentences' 列表（segments 分组渲染的数据源，"
                f"pipeline.py 产出格式）")
        # 段内句子与顶层 sentences 走同一份必需字段校验（只查
        # "非空列表"的话，段内缺 start_time 会在 gen_hyperframes/
        # 下游炸裸 KeyError，不指向真正缺的键）
        segment_seen = set()
        segment_prev = None
        for j, s in enumerate(ss):
            _check_sentence_fields(
                s, f"segments[{i}]（{sg.get('id', '?')}）.sentences[{j}]")
            if s["index"] not in seen_indices:
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' 引用了不存在的 sentence index {s['index']}")
            if s["index"] in segment_seen:
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' 内 sentence index 重复：{s['index']}")
            if s["index"] in assigned_indices:
                raise ValueError(
                    f"timing_manifest.json 的 sentence index {s['index']} 被多个段落重复引用")
            top_sentence = top_by_index[s["index"]]
            if s.get("text") != top_sentence.get("text"):
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' sentence "
                    f"index {s['index']} 的 text 与顶层 sentences 不一致")
            if abs(float(s["start_time"]) - float(top_sentence["start_time"])) > 1e-6 or \
               abs(float(s["duration"]) - float(top_sentence["duration"])) > 1e-6:
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' sentence "
                    f"index {s['index']} 的时间轴与顶层 sentences 不一致")
            segment_seen.add(s["index"])
            assigned_indices.add(s["index"])
            start = float(s["start_time"])
            if segment_prev is not None and start + 1e-6 < segment_prev:
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sg.get('id', '?')}' sentences 必须按 start_time 升序排列")
            segment_prev = start
        segment_start = float(ss[0]["start_time"])
        segment_end = float(ss[-1]["start_time"]) + float(ss[-1]["duration"])
        if previous_segment_end is not None and segment_start + 1e-6 < previous_segment_end:
            raise ValueError(
                f"timing_manifest.json 的 segments 顺序与时间轴不一致："
                f"'{sid}' 从 {segment_start:.3f}s 开始，但前一段结束于 "
                f"{previous_segment_end:.3f}s")
        previous_segment_end = segment_end
    missing_indices = seen_indices - assigned_indices
    if missing_indices:
        raise ValueError(
            "timing_manifest.json 的 segments 未覆盖全部顶层句子："
            f"缺少 index {sorted(missing_indices)}。每个句子必须恰好属于一个可视段落")
    return data


def load_timing_manifest(path):
    """读取并校验 timing_manifest.json（读取失败统一成带路径的 ValueError）。"""
    return validate_timing_manifest(_read_json_file(path))


def validate_relative_project_path(src, where="media path"):
    """Reject absolute/traversal paths and normalize separator semantics."""
    if not isinstance(src, str) or not src.strip():
        raise ValueError(f"{where} 必须是非空字符串")
    norm = src.replace("\\", "/")
    if "\x00" in norm:
        raise ValueError(f"{where} 不得包含 NUL 字节")
    # 注意：Windows 的 ntpath.isabs("/tmp/a.png") 返回 False——盘符缺省的
    # POSIX 绝对路径会被误判成相对路径，下游 abspath 再解析到 <当前盘>:\tmp。
    # 所以任何前导 "/"（含 UNC 的 "//"）都必须显式拦掉，不能只靠 isabs。
    if (os.path.isabs(norm) or norm.startswith("/")
            or re.match(r"^[A-Za-z]:/", norm)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", norm)):
        raise ValueError(f"{where} 禁止使用绝对路径：{src!r}")
    depth = 0
    for part in (p for p in norm.split("/") if p not in ("", ".")):
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ValueError(f"{where} 越过项目根目录：{src!r}")
        else:
            depth += 1
    return norm

# images.json 条目里渲染端**真正会读**的键。
# src/type/poster/loop/muted/autoplay/playsinline → html_renderer 的媒体分支；
# 其余五个是 provenance 记账字段，画面不读、但按 SKILL.md 要求留存。
# 之外的键（`position`/`fit`/`alt` 这类凭空发明的写法）过去会被静默丢掉：
# "我明明写了 alt，画面上什么都没有"变成无解的困惑。判定收口在这一个函数，
# warn 只在丢键的那一处（html_renderer
# ._normalize_images）打；run.py/gen_hyperframes 的预检走 validate_images_json。
MEDIA_ENTRY_KEYS = frozenset({
    "src", "type", "poster", "loop", "muted", "autoplay", "playsinline",
    "source_url", "license", "attribution", "query", "provider",
})


def unknown_media_keys(entry):
    """该 images.json 条目里渲染端不会读的键（升序列表）。"""
    return sorted(set(entry) - MEDIA_ENTRY_KEYS)


def validate_images_json(data):
    """images.json：{segment_id: 媒体对象}。

    唯一写法（图片/视频统一）：
        {"seg1": {"src": "images/seg1.png"},
         "seg2": {"src": "images/seg2.mp4", "type": "video", "loop": true,
                  "muted": true, "autoplay": true, "poster": "...png"}}
      "type" 可选（auto=按扩展名判断；image/video/gif 显式指定），其余字段均为
      可选（静态图只需 src）。不接受裸字符串路径。
    """
    if not isinstance(data, dict):
        raise ValueError("images.json 顶层必须是对象 {segment_id: 媒体对象}")
    for key, value in data.items():
        # key 与 manifest 段 id 同一口径（is_valid_sid 包住 _SID_RE）：这些键会拼进 HTML 的
        # id/选择器链路，"seg 3"、"seg.png" 之类在渲染端静默失联。
        if not is_valid_sid(key):
            raise ValueError(
                f"images.json 的段落 key 必须是合法段 id"
                f"（{SID_RULE}；实际: {key!r}）")
        if isinstance(value, str):
            raise ValueError(
                f"images.json 的 '{key}' 必须是媒体对象（如 {{\"src\": \"images/{key}.png\"}}），"
                f"不接受裸字符串路径")
        if isinstance(value, dict):
            src = value.get("src")
            # type 先查、src 后查：旧 C1/C2 项目留下的 {"type": "chart"} 本来就没有
            # src，先查 src 会报"缺少 src"，把人往错的方向引。
            _t = value.get("type")
            if _t is not None and _t not in ("auto", "image", "video", "gif"):
                raise ValueError(
                    f"images.json 的 '{key}' 的 'type' 必须是 auto/image/video/gif"
                    f"（实际: {_t!r}）"
                    + ("——图表 / 公式卡（旧 C1/C2）已从本技能移除，数据图和公式"
                       "改走手绘 SVG，需要整页表达时给段落 layout: \"canvas\""
                       if _t in ("chart", "formula") else ""))
            if "src" not in value:
                raise ValueError(f"images.json 的 '{key}' 对象格式缺少 'src' 字段（媒体路径）")
            if not value["src"]:
                raise ValueError(f"images.json 的 '{key}' 的 'src' 不能为空")
        else:
            raise ValueError(
                f"images.json 的 '{key}' 必须是媒体对象（实际: {type(value).__name__}）")
        # 走到这里 src 必然存在且非空（上面 dict 分支已拦缺键与空值）——旧 chart
        # 条目"可以不写 src"的豁免随 C1/C2 一起删掉了，这里不再留条件。
        if not isinstance(src, str) or not src:
            raise ValueError(f"images.json 的 '{key}' 的 src 必须是非空字符串")
        # 回写归一值：Windows 手写的 images\seg1.png 若原样流到渲染端
        # 会被 quote() 成 images%5Cseg1.png，跨平台即坏图。
        value["src"] = validate_relative_project_path(
            src, f"images.json 的 '{key}' 的 src")
        # poster 与 src 同一校验口径：禁绝对路径/越出项目根。键存在就必须是
        # 非空字符串——旧写法 `if poster:` 让空串 "" 与 None 同判，坏空串原样
        # 流到渲染端当封面路径用。
        if "poster" in value:
            poster = value["poster"]
            if not isinstance(poster, str) or not poster:
                raise ValueError(
                    f"images.json 的 '{key}' 的 poster 必须是非空字符串"
                    f"（不用封面就删掉这个键；实际: {poster!r}）")
            value["poster"] = validate_relative_project_path(
                poster, f"images.json 的 '{key}' 的 poster")
        # video 播放开关必须是 JSON 布尔：渲染端按 `opts.get("loop", True)`
        # 判真值，字符串 "false" 是 truthy，会画出意外的循环/静音行为。
        for field in ("loop", "muted", "autoplay", "playsinline"):
            if field in value and not isinstance(value[field], bool):
                raise ValueError(
                    f"images.json 的 '{key}' 的 {field} 必须是 JSON 布尔"
                    f"true/false（实际: {value[field]!r}）")
        for field in ("source_url", "license", "attribution", "query", "provider"):
            if field in value and not isinstance(value[field], str):
                raise ValueError(f"images.json 的 '{key}' 的 {field} 必须是字符串")

    return data


def load_images_json(path):
    """读取并校验 images.json（读取失败统一成带路径的 ValueError）。"""
    return validate_images_json(_read_json_file(path))


MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def classify_media_path(path, explicit_type="auto"):
    """根据文件扩展名和显式 type 判断媒体类型。

    Returns:
        "video" | "image" | "gif"
    """
    if explicit_type != "auto":
        return explicit_type
    ext = os.path.splitext(path.lower())[1]
    if ext in MEDIA_VIDEO_EXTS:
        return "video"
    if ext == ".gif":
        return "gif"
    return "image"

# ── Voice registry（内嵌） ──
# MiMo TTS 预置音色。CLI choices 与 _contracts 校验的唯一权威来源，
# 增删音色只改这里。
VOICE_IDS = ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]


def list_voice_ids():
    """返回全部预置音色 ID（副本），供 CLI --voice-id 的 choices 动态生成。"""
    return list(VOICE_IDS)


def is_valid_voice_id(voice_id):
    """判断 voice_id 是否属于预置音色。"""
    return voice_id in VOICE_IDS

