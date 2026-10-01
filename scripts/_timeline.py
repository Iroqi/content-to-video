#!/usr/bin/env python3
"""语速、句间停顿与时间轴容差：跨脚本唯一的一份假设（原 _contracts 收口）。

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
# 各写一份且互不一致：estimate 相对 pipeline 实测系统性偏长约 50%、
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
