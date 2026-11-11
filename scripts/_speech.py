#!/usr/bin/env python3
"""逐句音频的「发声区间」测量：这句话从第几秒开口、真正说了多久。

为什么需要：一句 TTS 音频的文件长度里含着**句首吸气**与**句尾收尾拖音**，
而下游要的是"这个人在说这话"的那一段——字幕换句、卡拉OK进度、页尾收信
都该落在它上面。以前 `duration` 直接取文件长度，于是那句尾的几百毫秒被
算进"这句话的时长"，字幕先念完再干等、页尾永远多出一段死时间。

判据用 ffmpeg 自带的 `silencedetect`（沙箱实测：埋 300ms 前导静音检出
0.372s＝300ms＋TTS 自身 72ms 起音，误差 ≤ 1 帧；−30/−35/−40/−45dB 四档
阈值检出完全一致）。不自己算能量包络：那等于把一份没校准过的算法塞进来。

所有返回值都**对齐到扫描帧网格**（`_FRAME`）。静音检测按 20ms 步长扫，
原始边界本来就是这个粒度；不先对齐的话，`start_time + onset` 会在契约层
和下一句的 `start_time` 撞出 0.001s 的"重叠"，而那只是噪声。
"""
import os
import re
import subprocess
import sys
import wave

# silencedetect 的扫描步长（ffmpeg 内部固定 20ms，可 `-af silencedetect=...:framelen` 改，
# 这里不改，所以对齐粒度就是它）。
_FRAME = 0.02

# 判"这里是静音"的门限。取 −35dB 是因为样本上 −30 到 −45 的检出完全一致——
# 取中间值不是因为它"最对"，是因为它对两侧都不敏感。
NOISE_DB = -35

# 最短静音长度：低于它的不算停顿。50ms 卡在"齿音/爆破间隙（<40ms）不当停顿、
# 句尾换气（>100ms）算停顿"之间；太短会把字与字之间的正常间隙也切进来。
MIN_SILENCE = 0.05

# 发声段下限：一句真念出来的话不可能短于这个数（再短就是"嗯"这类残句，
# 按发声长度算只会让卡拉OK一帧就跳完）。
MIN_VOICED = 0.10

_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def _quantize(seconds):
    """对齐到扫描帧网格（静音检测报的边界本来就是这个粒度）。就近取整。"""
    return round(round(seconds / _FRAME) * _FRAME, 3)


def _parse_silences(stderr_text):
    """ffmpeg `-af silencedetect` 的 stderr → [(start, end)]，按出现顺序。

    首个静音段若从 0 起算，ffmpeg 只印 `silence_start: 0`；末尾那段若一直
    静到文件结束，只印 `silence_start` 而没有 `silence_end`（它不知道文件
    多长），此时 `end` 记 `None`——由调用方按文件长度收口。

    纯字符串解析、不碰进程：这条路径是"线上真实数据长什么样"的唯一入口，
    必须能脱离 ffmpeg 单测。
    """
    spans = []
    pending = None
    for line in (stderr_text or "").splitlines():
        m = _SIL_START.search(line)
        if m:
            # 上一段还没收口（理论上是畸形输出）：丢弃，别把两段拼成一段。
            pending = float(m.group(1))
            continue
        m = _SIL_END.search(line)
        if m and pending is not None:
            spans.append((pending, float(m.group(1))))
            pending = None
    if pending is not None:
        spans.append((pending, None))
    return spans


def _wav_rate(path):
    """读 WAV 头拿采样率；不是可读 WAV 返回 None。

    本模块的**唯一前置条件**就藏在这里：发声区间只对 WAV 算。WAV 时长走样本
    精确路径（`_audio._wav_duration`：帧数/帧率），与静音检测用的是同一个时钟；
    非 WAV 的 `file_seconds` 来自 ffmpeg 的 `Duration:` 行，两套时钟相加会得出
    一个"看着精确"的错数，所以调用方拿 None 直接放弃。
    """
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return rate if rate > 0 else None
    except (wave.Error, EOFError, OSError, ValueError):
        return None


def _run_silencedetect(ffmpeg, path, timeout=120):
    args = [ffmpeg, "-hide_banner", "-nostats", "-i", path,
            "-af", f"silencedetect=noise={NOISE_DB}dB:d={MIN_SILENCE}",
            "-f", "null", "-"]
    try:
        got = subprocess.run(args, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    # 必须看 returncode：ffmpeg 起不来 / 文件打不开（无 ffmpeg、坏 WAV、权限）时
    # 它照样退出并往 stderr 写一版错误，而 stderr 里一个 silence_* 都没有。
    # 只看"有没有 stderr"会把**测量失败**当成"整段都在说话"——voiced_span 拿到
    # lead=tail=0，回报 (0.0, 文件全长)，连"量不出来"那句 warn 都不打，与真正的
    # 成功测量完全不可分辨。
    if got.returncode != 0:
        return None
    return got.stderr


def voiced_span(pitches, file_seconds):
    """静音区间 → (onset, voiced)；`pitches` 是 `_parse_silences` 的产物。

    分两步，因为两件事的边界不同：

    - `onset` = **第一个非静音时刻**。只看"从 0 起算的那段静音"：它结束在
      哪，人就在哪开口。没有这段静音（开口就是话）→ 0。
    - `voiced` = 文件长度 − **首尾**静音。句中停顿不算——那是在说话，
      只是换气（卡拉OK的分母就该包含它，否则念到停顿处进度会超）。

    结果夹在 `[MIN_VOICED, 文件长度]` 与 `[0, 文件长度]`：检测给出任何
    物理上讲不通的数（负边界、比文件还长）都被收成边界值，而不是把噪声
    传给下游。
    """
    total = float(file_seconds)
    if total <= 0:
        return None
    spans = [(max(0.0, s), total if e is None else min(total, e))
             for s, e in pitches or []]
    spans = [(s, e) for s, e in spans if e > s]

    lead = 0.0
    for s, e in spans:
        if s <= 1e-6:
            lead = e
            break
    tail = 0.0
    for s, e in reversed(spans):
        if e >= total - 1e-6:
            tail = total - s
            break

    onset = _quantize(min(max(lead, 0.0), total))
    voiced = _quantize(total - lead - tail)
    # 上限是"扣掉前导静音之后还剩多少"，不是文件长度——两者差了 onset 那么多。
    # 全静音时 voiced 落到 MIN_VOICED 下限，若上限仍按文件长度算，就会写出
    # onset + voiced > duration（契约层直接拒收，整份 manifest 报废）。
    room = round(total - onset, 3)
    if room < _FRAME:
        # 整块静音（只剩不到一帧）：没有"发声区间"可言。返回 None = 调用方
        # 退回按文件长度计时，能写出一份合法 manifest——这比硬编一个
        # onset=整块、voiced=0 的真值更安全（后者会被契约层的 voiced>0 判据
        # 直接拒收，整份产物报废）。
        return None
    # 地板是 MIN_VOICED，但不得高过 room：剩余空间比下限还小时贴着 room 走。
    voiced = min(max(voiced, MIN_VOICED), room)
    return onset, voiced


def measure_voiced_span(ffmpeg, path, file_seconds):
    """量一句音频的 (onset, voiced)；量不出来返回 None（调用方退回文件长度）。

    量不出来 = 不是可读 WAV、ffmpeg 不在/超时、整句静音。**不进 degraded**：
    这改变的不是产物内容（少了"这句话说到哪"的精度），而 `degraded` 的语义是
    "用户显式要的东西没做到"——混进去会让正式渲染被一条无关紧要的 warn 拦下。
    """
    if not ffmpeg or not os.path.exists(path):
        return None
    if _wav_rate(path) is None:
        # 非 WAV（或头读不出来）：`file_seconds` 多来自 ffmpeg 的 Duration 行，
        # 精度与静音边界不是同一套时钟，硬算出来只会是个"看着精确"的错数。
        return None
    stderr = _run_silencedetect(ffmpeg, path)
    if stderr is None:
        return None
    span = voiced_span(_parse_silences(stderr), file_seconds)
    if span is None:
        print(f"  [speech] {os.path.basename(path)} 量不出发声区间，"
              f"这一句按文件长度计时", file=sys.stderr)
    return span
