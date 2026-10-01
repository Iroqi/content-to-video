#!/usr/bin/env python3
"""segments_source.json 的契约：封闭字段集 + 取值校验（原 _contracts 的 source 层）。

用户写稿、pipeline 读取的唯一入口。四层（顶层 / 段落 / speakers / dialogue 轮次）
字段集各自封闭，多一个键就报错——读侧全是 .get()，拼错的字段不生效也不出声，
最坏的一类后果是整页悄悄消失。校验范围不止结构：speed / voice_id / accent /
layout 这些取值也在这里把关，坏参数在进 TTS 之前就 fail-fast。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import read_json_file  # noqa: E402
from _segments import (CONTENT_SID_PREFIX, _validate_layout, _validate_sid,  # noqa: E402
                       is_content_sid)
from _timeline import validate_speed  # noqa: E402
from _validate import (_reject_unknown_keys, _validate_accent,  # noqa: E402
                       _validate_text)
from _voices import is_valid_voice_id, list_voice_ids  # noqa: E402

# segments_source.json 的封闭字段集：顶层 / 段落 / 对话轮次 / speakers 条目四层
# 各自只读列出的这些键，多出来的键一律报错。
# 为什么不留"未知键静默忽略"的余地：读侧全是 .get()，拼错的字段不会生效也不会
# 报错，最坏的一类后果是整页悄悄消失——实测把 closing 写成 closing_text 后，
# 结尾 agenda 卡压根不生成，而产物看起来完全正常（帧是两张槽位页，check 全绿）。
# 清单必须与下面 validate_segments_source 里逐个 .get() 的字段同步。
SOURCE_KEYS = ("title", "opening", "opening_title", "opening_tagline",
               "opening_speed", "opening_layout", "closing", "closing_title",
               "closing_tagline", "closing_speed", "closing_layout", "cta",
               "speakers", "segments")
SEGMENT_KEYS = ("id", "title", "tagline", "text", "dialogue", "accent",
                "speed", "voice_id", "voice_style", "takeaway", "layout")
SPEAKER_KEYS = ("voice_id", "voice_style", "label")
# 对话轮次只认这两个键：音色一律查顶层 speakers，per-turn 的 voice_id
# 写了也不会生效（读侧只取 speaker/text），必须出声而不是静悄悄当没看见。
TURN_KEYS = ("speaker", "text")


def validate_segments_source(data):
    """segments_source.json：需要非空的 segments 列表，opening/closing 可选。

    每个 segment 需要 'text'（单人独白）或 'dialogue'（双人/多人对话）二选一。
    使用 'dialogue' 时，顶层必须有 'speakers' 声明每个说话人的 voice_id。

    除了结构校验，还做取值校验：speed 必须是 >0 的有限数值、voice_id 必须是
    预置音色之一。这样坏参数在进 TTS 之前就 fail-fast，而不是每句重试到超时。
    字段集封闭（SOURCE_KEYS / SEGMENT_KEYS / SPEAKER_KEYS），未知键直接报错。
    """
    if not isinstance(data, dict):
        raise ValueError("segments_source.json 顶层必须是 JSON 对象")
    _reject_unknown_keys(data, SOURCE_KEYS, "segments_source.json 顶层")
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

    # 结构性页的版式覆盖：不写 = agenda 投影卡（pipeline 盖章），写 "canvas" =
    # 整页海报。只有这两个去向，所以这里按取值分派、不看段 id（content=False）。
    for key in ("opening_layout", "closing_layout"):
        _validate_layout(data.get(key), f"segments_source.json 的 '{key}'")

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
            _reject_unknown_keys(spk_cfg, SPEAKER_KEYS,
                                 f"segments_source.json 的 speakers['{spk}']")
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
        _reject_unknown_keys(seg, SEGMENT_KEYS,
                             f"segments[{i}]（title={seg.get('title')!r}）")
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
                         f"segments[{i}]（title={seg.get('title')!r}）",
                         content=True)
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
                _reject_unknown_keys(turn, TURN_KEYS,
                                     f"segments[{i}].dialogue[{j}]")
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


def load_segments_source(path):
    """读取并校验 segments_source.json（读取失败统一成带路径的 ValueError）。"""
    return validate_segments_source(read_json_file(path))
