#!/usr/bin/env python3
"""语速、句间停顿与时间轴容差：跨脚本唯一的一份假设。

pipeline 的 --speed 入口、_audio 的 atempo 守卫、resume 状态机、
build_from_structured 的段落默认值共用这里的常量与判定；
任何一处另写一份 `abs(speed-1.0) > 0.01` / `round(speed, 4)` 都会漂移。
"""
import math


def validate_speed(speed):
    """校验语速倍率必须是 >0 的有限数值，非法时抛 ValueError。

    跨脚本领域规则收口在这里（与 DEFAULT_SPEED 同一模块）：pipeline 的
    --speed 入口、source 值校验、_audio.build_atempo_filter
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
# "默认 TTS 倍速"的入口一律从这里取，改时只改这一处。
DEFAULT_SPEED = 1.5
# opening/closing 段的默认语速：比正文略慢，凸显开场/收尾的节奏。
# build_from_structured 造段配置时写入，pipeline 的时长估算/静音占位经
# 段落 speed 复用同一假设——两处必须一致，所以也收口在这里。
OPENING_CLOSING_DEFAULT_SPEED = 1.2

# ── 时长估算跨脚本默认值──────────────────────────────────────────
# DEFAULT_CHARS_PER_SEC / DEFAULT_GAP / estimate_sentence_seconds 放在契约层
# 而不是可选 CLI 工具里，是为了不让核心管线反向依赖一个可选脚本（与
# DEFAULT_SPEED 同理）。
DEFAULT_CHARS_PER_SEC = 4.3  # "语速适中"参考值，纯启发式
# 句间停顿默认值：pipeline 的 --gap 直接 import 这里作默认，所以只有一处。
# 估算与实测的口径差会累积成 (n-1)×Δ 的系统性偏移。
DEFAULT_GAP = 0.4

# 时间轴对账的唯一容差。pipeline 的"拼接产物不得比预期短 250ms"与
# _manifest_schema.validate_timing_manifest 的"末句不得超出 total_duration
# 250ms"是同一个量级判断：分成两处写字面量就会漂移——上游放宽到 0.3s、
# 下游仍按 0.25s 拒收，等于 TTS 额度烧完才告诉用户产物不能用。
TIMELINE_TOLERANCE = 0.25


def estimate_sentence_seconds(sentence, chars_per_sec, speed):
    """单句预计时长（秒）：字数 / 语速 / 倍速。"""
    return len(sentence) / chars_per_sec / max(speed, 0.01)


# ── 导演节拍 → 时间轴位置──────────────────────────────────────────
# 「这一步落在绝对秒哪儿」只有一份实现：渲染端（Remotion 求值器）与
# 生成期门禁/对轴报告（_director_prepare.director_prepare）都调这里。两处各写一套就
# 会漂移——门禁拿着渲染器根本不会用的时刻报错，比没门禁更糟。
_WHOLE_BEAT_KEYS = ("morph", "count", "type")   # 各自负责整段，从 pos 一直占满 dur


def beat_cycles(step):
    """这一拍演几遍（公开口径）；None = `repeat:-1` 无限，**没有收尾态可言**。

    渲染端、接续烘焙和出画判定都读这一个数：遍数一旦在两处各算一遍，"成片里停在哪"
    和"门禁报的那一站"就分家，而那正是跨页接续最难查的一类错。
    """
    repeat = int(step.get("repeat", 0))
    return None if repeat < 0 else repeat + 1


def beat_span(step, dur):
    """这一拍**实际占多久**（秒）：单遍时长 × 遍数。无限循环返回 None（没有终点）。

    读它的三处都必须用同一个数：相对链 `at_time:"+N"` 的游标、"这一拍会不会被下一页
    盖住"的窗口判定、以及接续烘焙"最后停在哪儿"的收尾态。只按 duration 算的话，一条
    `repeat:3` 的呼吸动画在门禁里被当成一拍就完，报出来的时刻与成片差四倍。
    """
    n = beat_cycles(step)
    return None if n is None else round(float(dur) * n, 6)


def ends_at_start(step):
    """这一拍收尾时回到起点没有：`yoyo` 且总遍数为偶数就是回到起点。

    不确定（无限循环）返回 None——调用方不能替它猜一个终态，只能跳过并出声。
    无 `yoyo` 时永远停在目标值（每遍都从头重放，收尾仍是 `to`）。
    """
    n = beat_cycles(step)
    if n is None:
        return None
    return bool(step.get("yoyo")) and n % 2 == 0


def _beat_spans_time(step):
    """这一步是否占用 `beat_span` 这么久（决定相对链 `at_time:"+N"` 的游标往前推多少）。

    有补间的都占：`draw`/`from`/`to`；morph·count·type 这三类原语自己负责整段，
    即使没有 from/to 也按 dur 推进。纯 `set` 是瞬时赋值，游标就停在 pos。
    """
    return bool(step.get("draw")) or "from" in step or "to" in step or any(
        k in step for k in _WHOLE_BEAT_KEYS)


def beat_positions(steps, sentences, seg_start, default_duration):
    """把 director.steps 解析成 `[(绝对秒位置, 补间时长)]`，与 steps 同序。

    - `at`（句序锚）：整数=该段第几句（0 基），小数=句内偏移。位置 =
      `sentences[floor(at)].start_time + frac × 该句时长 + delay`。**跟着 manifest 走，
      重配音/改文案后自动对得上**；代价是句内 frac 是"语速均匀"的线性假设（见下）。
    - `at_time`（段落锚）：数字=从该段音频起点 `seg_start` 起的绝对秒；字符串
      `"+N"/"-N"`=从上一条 beat 的**结束**时刻再推 N 秒（首条没有上一条时退到 seg_start）。
      位置 = `seg_start + at_time + delay`。**和语音没有任何绑定**——它按某一版配音手算，
      重配音、改句长、动 `--gap` 都会整体错位，且不会有任何报错。
    - `delay`：两类锚都叠加。
    - 缺省 `duration` 由调用方传（模板 `animation.director`），门禁与渲染必须同值。

    `at` 越界抛 ValueError（消息只说"at=N 越界…"，由调用方补上段落/步序上下文）：
    句序锚的整数部分必须 < 该段句数，否则渲染端会拿着不存在的句子算位置。
    """
    out = []
    cursor = None   # 上一条 beat 的结束时刻（绝对秒），供 "+N" 相对串链接
    for step in steps:
        if "at_time" in step:
            av = step["at_time"]
            delay = float(step.get("delay", 0.0))
            if isinstance(av, str):
                base = cursor if cursor is not None else seg_start
                pos = round(base + float(av) + delay, 2)
            else:
                pos = round(seg_start + float(av) + delay, 2)
        else:
            at = step["at"]
            idx = int(at)   # at 已由 schema 保证 ≥0，int() 即向下取整
            if idx >= len(sentences):
                raise ValueError(
                    f"at={at} 越界：该段只有 {len(sentences)} 句旁白"
                    f"（at 的整数部分=句序，最大 {len(sentences) - 1}）")
            sent = sentences[idx]
            pos = round(float(sent["start_time"])
                        + (at - idx) * float(sent.get("duration", 0.0))
                        + float(step.get("delay", 0.0)), 2)
        dur = float(step.get("duration", default_duration))
        out.append((pos, dur))
        if _beat_spans_time(step):
            # 游标按**总跨度**推进（repeat 的拍子占 dur×遍数）。无限循环没有终点，
            # 退回"算它演了一遍"，好让后面的 "+N" 还有个落点——真要接下一拍就该写明确遍数。
            span = beat_span(step, dur)
            cursor = pos + (span if span is not None else dur)
        else:
            cursor = pos
    return out

