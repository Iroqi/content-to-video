#!/usr/bin/env python3
"""timing_manifest.json 的契约：加载 + 校验。

manifest 是"确需手写可按格式提供"的接口，所以三层字段集封闭（顶层 /
segments[i] / 句子对象）：读侧全是 .get()，拼错的键不报错也不生效——
takeaway 拼成 take_away 时结尾 agenda 那行悄悄退回标题、layout 拼错时段
默默回到槽位版式。pipeline 写盘前自校验走这里，坏产物出不了 TTS 步骤。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import read_json_file  # noqa: E402
from _degraded import KEYS as DEGRADED_KEYS  # noqa: E402  降级词汇表（注册表派生，单一来源）
from _segments import (CONTENT_SID_PREFIX, STRUCTURAL_SIDS,  # noqa: E402
                       _validate_layout, _validate_sid, is_content_sid)
from _timeline import TIMELINE_TOLERANCE  # noqa: E402
from _validate import (_reject_unknown_keys, _validate_accent,  # noqa: E402
                        _validate_finite_number, _validate_text)
from _voices import is_valid_voice_id, list_voice_ids  # noqa: E402

# timing_manifest.degraded 的全部合法键 = _degraded.KEYS（注册表派生，见 _degraded.py 文件头）。

# timing_manifest 的两个对象层同样字段集封闭（清单 = pipeline 写侧的原样产出，
# 少一个键就会在 producer 自校验时立刻炸，多一个键说明有人手写过它）。
# 收这一层的理由与 source 侧不同：manifest 是文档明写"确需手写可按格式提供"的
# 接口，而读侧全是 .get()——takeaway 拼成 take_away 时结尾 agenda 那行悄悄退回
# 标题、layout 拼错时段默默回到槽位版式，产物照样"完全正常"。
MANIFEST_SENTENCE_KEYS = ("index", "text", "start_time", "duration",
                          "speaker", "synth_failed")
MANIFEST_SEGMENT_KEYS = ("id", "title", "tagline", "accent", "sentences",
                         "speed", "layout", "voice_id", "voice_style",
                         "takeaway", "turns")
# 顶层也一起封闭：只封下面两层会留一半——combined_audio / closing_cta 都是可选键，
# 拼错了读侧 .get() 兜默认值，一声不吭（清单在 17 份真实 manifest 上实测稳定）。
MANIFEST_TOP_KEYS = ("schema_version", "status", "degraded", "sentences",
                     "segments", "total_duration", "gap", "voice_id",
                     "combined_audio", "closing_cta")
# 对话轮的键集（build_from_structured.turns 的产出形状）。它在 manifest 里，
# 但只服务人工回查、不进 HTML——所以拼错的键同样是"静默忽略"，一并封。
_TURN_KEYS = ("start", "end", "speaker", "label", "voice_id", "voice_style")


def _validate_voice_id(value, where):
    """voice_id（单个音色或逗号拼接串）：逐 token 落在预置音色白名单里。

    顶层与段级是同一条判据、同一句报错，所以只有这一份——两边各写一遍时改一边
    另一边就漂，而"可用音色"清单是从 _voices 派生的，漂移就是报一串错的音色名。
    """
    if not isinstance(value, str) or not all(
            is_valid_voice_id(v) for v in value.split(",")):
        raise ValueError(
            f"{where} 的 voice_id={value!r} 含不在预置音色里的值"
            f"（单个音色或逗号拼接串均可；可用：{', '.join(list_voice_ids())}）")


def validate_timing_manifest(data):
    """timing_manifest.json：sentences（非空）与 total_duration（数值）必填。"""
    if not isinstance(data, dict):
        raise ValueError("timing_manifest.json 顶层必须是 JSON 对象")
    _reject_unknown_keys(data, MANIFEST_TOP_KEYS, "timing_manifest.json 顶层")
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
        _validate_voice_id(data["voice_id"], "timing_manifest.json")
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
    if isinstance(degraded, dict):
        # 降级项的键是一份跨模块词汇表：pipeline.py 写、run.py 的制作报告读、这里
        # 验。三处各知一份时，写侧把键拼错（或多一档新降级没登记）就会让报告静默
        # 少一条，只剩 status 兜底拦交付却说不清拦的是哪一项。收在这里，坏键在
        # producer 侧（pipeline 写盘前自己会 validate 一遍）就炸，不等到渲染完。
        stray = sorted(str(k) for k in degraded if k not in DEGRADED_KEYS)
        if stray:
            raise ValueError(
                f"timing_manifest.json 的 degraded 有未知键 {'、'.join(repr(k) for k in stray)}"
                f"——只认 {'、'.join(repr(k) for k in DEGRADED_KEYS)}"
                "（新增降级档要同时在这里登记，否则制作报告读不到它）")
    if status == "degraded" and not degraded:
        raise ValueError("timing_manifest.json 标记为 degraded 时必须提供非空 degraded 详情")

    sentences = data.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise ValueError("timing_manifest.json 需要非空的 'sentences' 列表")
    total_duration = data.get("total_duration")
    _validate_finite_number(total_duration,
                            "timing_manifest.json 的 total_duration"
                            "（应为 ffmpeg 实测总时长）", positive=True)

    def _check_sentence_fields(s, where):
        if not isinstance(s, dict):
            raise ValueError(f"{where} 必须是对象")
        _reject_unknown_keys(s, MANIFEST_SENTENCE_KEYS, f"{where}")
        for key in ("index", "text", "start_time", "duration"):
            if key not in s:
                raise ValueError(f"{where} 缺少字段 '{key}'"
                                 "（pipeline.py 产出格式）")
        if not isinstance(s["text"], str) or not s["text"].strip():
            raise ValueError(f"{where}.text 必须是非空字符串")
        idx = s["index"]
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
            raise ValueError(f"{where}.index 必须是非负整数")
        start = s["start_time"]
        duration = s["duration"]
        _validate_finite_number(start, f"{where}.start_time", nonnegative=True)
        _validate_finite_number(duration, f"{where}.duration", positive=True)
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
    # segments 必填且非空：画面标题/tagline/accent 与开屏/结尾 agenda 只从这些分组
    # 取，没有它就没有内容段版式可渲染；每段还要自带非空 sentences 列表——
    # 段内 sentences 一并显性化：
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
        _reject_unknown_keys(sg, MANIFEST_SEGMENT_KEYS,
                             f"timing_manifest.json 的 segments[{i}]"
                             f"（id={sg.get('id', '?')}）")
        sid = sg.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"segments[{i}].id 必须是非空字符串")
        if not (sid in STRUCTURAL_SIDS or is_content_sid(sid)):
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
                         content=is_content_sid(sid))
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
        if sg.get("voice_id") is not None:
            _validate_voice_id(
                sg["voice_id"],
                "timing_manifest.json 的段落 '{}'".format(sid))
        # speed / voice_style / turns 都在封闭键集里，值也一起封：键集封闭的
        # rationale 是"拼错即报错"，值类型错同样会让读侧炸裸异常或静默走
        # 默认分支——`"speed": "fast"` 到 apply_speed 那步才炸，`turns: 5`
        # 到切片处才炸（那里 [:trim] 遇 int 直接 TypeError）。
        _seg_speed = sg.get("speed")
        if _seg_speed is not None:
            _validate_finite_number(
                _seg_speed, f"timing_manifest.json 的段落 '{sid}' 的 'speed'",
                positive=True)
        _seg_style = sg.get("voice_style")
        if _seg_style is not None and not isinstance(_seg_style, str):
            raise ValueError(
                f"timing_manifest.json 的段落 '{sid}' 的 'voice_style' "
                f"必须是字符串（实际: {_seg_style!r}）")
        _seg_turns = sg.get("turns")
        if _seg_turns is not None:
            if isinstance(_seg_turns, bool) or not isinstance(_seg_turns, (list, tuple)):
                raise ValueError(
                    f"timing_manifest.json 的段落 '{sid}' 的 'turns' "
                    f"必须是列表（对话轮的元数据，实际: {_seg_turns!r}）")
            for j, turn in enumerate(_seg_turns):
                if not isinstance(turn, dict):
                    raise ValueError(
                        f"timing_manifest.json 的段落 '{sid}' 的 "
                        f"turns[{j}] 必须是对象（实际: {turn!r}）")
                where_turn = f"timing_manifest.json 的段落 '{sid}' 的 turns[{j}]"
                _reject_unknown_keys(turn, _TURN_KEYS, where_turn)
                # 轮次区间是段内 0-based 的 [start, end)：坏了不会炸在渲染端
                # （它不进 HTML），只让人工回查看错说话人归属，值域一起封。
                for edge in ("start", "end"):
                    if edge in turn:
                        _validate_finite_number(turn[edge], f"{where_turn} 的 {edge!r}",
                                                nonnegative=True)
                # 两个边只要存在，上面就已过 _validate_finite_number（非 bool 的有限
                # 数值），所以这里直接比。原先按 isinstance(…, int) 判，
                # {"start":5.0,"end":2.0} 这种浮点写法整个漏过去——注释里那句"值域
                # 一起封"就只对一半写法成立。
                if "start" in turn and "end" in turn and turn["end"] < turn["start"]:
                    raise ValueError(
                        f"{where_turn} 的 end={turn['end']} 小于 start="
                        f"{turn['start']}（轮次区间是 [start, end)）")
                if turn.get("speaker") is not None and not isinstance(turn["speaker"], str):
                    raise ValueError(
                        f"{where_turn} 的 'speaker' 必须是字符串"
                        f"（实际: {turn['speaker']!r}）")
        ss = sg.get("sentences")
        if not isinstance(ss, list) or not ss:
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
    return validate_timing_manifest(read_json_file(path))
