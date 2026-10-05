"""交付级降级的唯一注册表（只依赖标准库，不 import 任何同目录模块）。

timing_manifest 的 `degraded` 里每一项都意味着"交付的不是用户要的那一版"。
词汇表 `KEYS` 从 `KINDS` 派生——登记一行 Kind 就同时有了校验词汇与人话文案：

  - `pipeline.py` 写键时用本模块的常量（`D.` 属性访问），拼错是 AttributeError，而不是一条静默丢失的降级；
  - 渲染编排（pipeline/生成器）只调用 `items()`。

新增一档降级：① 加常量 ② 写一个 reader ③ 在 `KINDS` 里登记一行，
再在 pipeline 里用常量写入。`tests/test_degraded.py` 会核对 pipeline 写的键都已登记。
"""
from typing import NamedTuple

# ── 键名常量：pipeline 写侧与 KINDS 共用 ──
SILENCE_FALLBACK_COUNT = "tts_silence_fallback_count"
LOST_SENTENCE_COUNT = "tts_lost_sentence_count"
SEGMENTS_DROPPED = "segments_dropped"
AUDIO_SHORTER_THAN_TIMELINE = "audio_shorter_than_timeline"


class Kind(NamedTuple):
    report_type: str   # production_report.degraded 里的 type
    key: str           # timing_manifest.degraded 里消费的键
    read: object       # (tm, deg) -> (计数, 人话说明) | None；None = 这一项没发生


# 每个 reader 返回 (计数, 人话说明) 或 None（None = 这一项没发生）。读法各家
# 不同是 manifest 的历史事实，不是这里的设计：计数可能是 bool 旗标、可能是
# sid 列表、也可能是被写坏的字符串，所以逐键保留原有的容错语义。
def _read_synth_failed(_tm, deg):
    # 计数从 sentences 现场数，不读 degraded 里的汇总键：万一汇总键被写坏或
    # 没写，逐句的 synth_failed 边车状态仍然成立。
    n = sum(1 for s in _tm.get("sentences", []) if s.get("synth_failed"))
    return (n, f"{n} 句 TTS 失败并使用静音兜底") if n else None


def _read_lost(_tm, deg):
    # 兜底也没兜住的句子压根不在 sentences 里（--on-fail silence 下 TTS 与
    # 静音双双失败），只看 synth_failed 会让"少了几句配音和字幕"的产物按正常
    # 成片放行。
    try:
        n = int(deg.get(LOST_SENTENCE_COUNT) or 0)
    except (TypeError, ValueError):
        n = 0
    return (n, f"{n} 句完全丢失（连静音占位都没生成）") if n else None


def _read_dropped(_tm, deg):
    # segments_dropped 是 pipeline 写下的被剔除 sid 列表（不是计数）
    ids = deg.get(SEGMENTS_DROPPED) or []
    if isinstance(ids, list) and ids:
        return (len(ids), f"{len(ids)} 个段落因没有任何可用音频被剔除"
                          f"（{', '.join(str(s) for s in ids)}）")
    return None


def _read_audio_short(_tm, deg):
    # 音频比时间轴短：末尾那几秒画面有字幕没声音，是交付级差异
    v = deg.get(AUDIO_SHORTER_THAN_TIMELINE)
    if isinstance(v, (int, float)) and v > 0:
        return (round(v, 2), f"音频比时间轴终点短 {v:.2f}s（片尾无配音）")
    return None


# 顺序即 production_report.degraded 的顺序，也是 manifest 校验报错时词汇的列出顺序。
KINDS = (
    Kind("tts_silence_fallback", SILENCE_FALLBACK_COUNT, _read_synth_failed),
    Kind("tts_lost_sentences", LOST_SENTENCE_COUNT, _read_lost),
    Kind("segments_dropped", SEGMENTS_DROPPED, _read_dropped),
    Kind("audio_shorter_than_timeline", AUDIO_SHORTER_THAN_TIMELINE, _read_audio_short),
)

# 封闭词汇表：_manifest_schema.validate_timing_manifest 用它拦未知键
KEYS = tuple(k.key for k in KINDS)


def items(tm):
    """读 timing_manifest，返回 [(报告 type, 计数, 人话说明), ...]。

    闸门条件、production_report 的机器可读条目、给用户看的人话全部从这一次遍历派生。
    """
    deg = tm.get("degraded") or {}
    out = []
    for kind in KINDS:
        got = kind.read(tm, deg)
        if got:
            out.append((kind.report_type, got[0], got[1]))
    return out
