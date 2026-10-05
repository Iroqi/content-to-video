"""交付级降级的唯一注册表（只依赖标准库，不import 任何同目录模块）。

timing_manifest 的 `degraded` 里每一项都意味着"交付的不是用户要的那一版"。
这里同时供两侧使用，共用一份登记——写侧和读侧的键名不可能漂移：

  - 写侧：`pipeline.py` 用本模块的键名常量（`D.<常量>`）写入，拼错是
    AttributeError，而不是一条静默丢失的降级；
  - 读侧：`pipeline.py` 降级提示与 `tests/` 都调 `items()`，把 (计数, 人话)
    渲染出来——那一行提示是**唯一**会被人读到的降级描述。

新增一档降级：① 加键名常量 ② 写一个 reader ③ 在 `KINDS` 里登记一行，
再在 pipeline 里用该常量写入。`tests/test_degraded.py` 会核对 pipeline 写的键
都已登记，`test_degraded_chain.py` 把降级链整条串起来跑。
"""
from typing import NamedTuple

# ── 键名常量：pipeline 写侧与 KINDS 共用 ──
SILENCE_FALLBACK_COUNT = "tts_silence_fallback_count"
LOST_SENTENCE_COUNT = "tts_lost_sentence_count"
SEGMENTS_DROPPED = "segments_dropped"
AUDIO_SHORTER_THAN_TIMELINE = "audio_shorter_than_timeline"


class Kind(NamedTuple):
    type: str         # 人话提示前缀（"· 2 句 TTS 失败并使用静音兜底"）
    key: str          # timing_manifest.degraded 里消费的键
    read: object      # (tm, deg) -> (计数, 人话说明) | None；None = 这一项没发生


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


# 顺序即 KINDS 的登记顺序：提示按"内容缺失程度"从轻到重排——先是句子级
# 兜底，再到整段消失（这一档最伤，用户必须先看到）。
KINDS = (
    Kind("tts_silence_fallback", SILENCE_FALLBACK_COUNT, _read_synth_failed),
    Kind("tts_lost_sentences", LOST_SENTENCE_COUNT, _read_lost),
    Kind("segments_dropped", SEGMENTS_DROPPED, _read_dropped),
    Kind("audio_shorter_than_timeline", AUDIO_SHORTER_THAN_TIMELINE, _read_audio_short),
)

# 封闭词汇表：_manifest_schema.validate_timing_manifest 用它拦未知键
KEYS = tuple(k.key for k in KINDS)


def items(tm):
    """读 timing_manifest，返回 [(类型, 计数, 人话说明), ...]。

    pipeline 打到 stderr 的降级提示、给用户看的人话，全部从这一次遍历派生——
    各处自己拼一遍文案，迟早与 reader 里的实际语义脱节。
    """
    deg = tm.get("degraded") or {}
    out = []
    for kind in KINDS:
        got = kind.read(tm, deg)
        if got:
            out.append((kind.type, got[0], got[1]))
    return out
