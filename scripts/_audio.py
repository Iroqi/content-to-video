#!/usr/bin/env python3
"""TTS 管线的 FFmpeg 音频操作。

包含：时长测量、静音生成、atempo 变速、拼接、BGM 混音。
所有函数都只依赖"ffmpeg 路径 + 参数"，不碰 TTS/网络，可脱离 pipeline 单独测试。
"""
import os
import subprocess
import re
import shutil
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _timeline import validate_speed, needs_speed_change  # noqa: E402
from _script_utils import remove_if_exists  # noqa: E402  失败清理用的删文件


def _wav_duration(audio_path):
    """WAV 样本精确时长（秒）：帧数 / 帧率。零子进程、无量化误差。

    ffmpeg 打印的 `Duration:` 固定两位小数（10ms 量化），pipeline 把每句
    量化值累加进 manifest 的 start_time 时误差随机游走，长稿（200 句）可
    漂移数十 ms 到近秒级——字幕/动效同步精度直接受损。所有中间产物都是
    WAV，标准库 wave 读帧数即可精确到样本。非 WAV 或解析失败返回 None
    （调用方回退 ffmpeg -i 路径）。
    """
    try:
        with wave.open(audio_path, "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if frames > 0 and rate > 0:
                return frames / float(rate)
    except Exception:
        pass
    return None


def wav_data_consistent(audio_path):
    """WAV 的 RIFF `data` 块声明长度是否与文件实际字节自洽（即没被截断）。

    `wave.getnframes()` 只读头部：上次写入被 Ctrl-C / 磁盘写满 / 进程杀掉
    打断时，头部可能已按完整长度写好而 data 只落了半截。这种文件量出来的
    时长是"想象中的音频"，`--resume` 会把它当成有效缓存跳过——句尾被切、
    时间轴与字幕错位，全程零报错。逐块走一遍 RIFF，比对 data 块声明长度与
    文件剩余字节。

    只在**拿到确凿截断证据**时返回 False：非 WAV、块链读不懂、size 字段为
    0/0xFFFFFFFF 这类"未知长度"写法一律返回 True（宁可不拦，也不要把
    合法但非典型的 WAV 误判成损坏而反复重合成）。
    """
    try:
        size = os.path.getsize(audio_path)
        with open(audio_path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return True
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    return True
                offset = f.tell()
                declared = int.from_bytes(chunk[4:8], "little")
                if declared in (0, 0xFFFFFFFF):
                    # 流式写入常见的"长度待定"写法，量不出是否截断
                    return True
                if chunk[:4] == b"data":
                    return offset + declared <= size
                # 块按偶数字节对齐（奇数长度后有一个填充字节）
                f.seek(offset + declared + (declared & 1))
    except OSError:
        return True


def measure_duration(ffmpeg_path, audio_path):
    """Measure audio duration (ffprobe not available in imageio-ffmpeg).

    WAV 优先走样本精确路径（_wav_duration，帧数/帧率）；非 WAV 或 wave
    解析失败才回退 `ffmpeg -i` 的 stderr 正则解析（`Duration: HH:MM:SS.xx`
    行比字符串切分对 locale/格式变化更稳健）。
    Returns 0.0 if parsing fails (callers should treat 0.0 as invalid).
    """
    wav_dur = _wav_duration(audio_path)
    if wav_dur is not None:
        return wav_dur
    try:
        result = subprocess.run(
            [ffmpeg_path, "-i", audio_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30
        )
        stderr = result.stderr or ""
        dur = parse_duration(stderr)
        if dur is not None:
            return dur
    except Exception as e:
        print(f"  [duration] error measuring {audio_path}: {e}",
              file=sys.stderr)
    return 0.0


def generate_silence(ffmpeg_path, duration, out_path):
    """Generate a silent WAV file of given duration.

    Primary path: ffmpeg lavfi (anullsrc). Fallback: Python wave module —
    used when the resolved ffmpeg is a minimal build (e.g. system PATH
    ffmpeg with `--disable-everything`) that does not support the lavfi
    demuxer. The Python fallback produces a standards-compliant 16-bit
    PCM at 24kHz/mono, matching what ffmpeg -ar/-ac emits.

    两条路都失败时抛 RuntimeError 而不是落一个 0 字节空文件——空文件混进
    concat 要么整链失败要么被静默丢弃，而调用方（pipeline 的静音兜底分支）
    已经按"异常=兜底失败"处理，能正确走 skip 路径，不会带着坏文件错位时间轴。

    固定 24k 单声道：与语音句格式不同的稿件（如原生 44.1k）会在 concat 前
    由 _normalize_wav 统一重采样成多数派，静音不例外。
    """
    sample_rate = 24000
    # encoding/errors 显式指定：中文 Windows 下 text=True 默认按 cp936 解码
    # ffmpeg stderr（UTF-8），输出路径含中文时会先抛 UnicodeDecodeError 而
    # 不是走兜底。TimeoutExpired 同样落入 wave 兜底（lavfi 卡死 30s 的
    # ffmpeg 写不出比 Python wave 更好的静音）。
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-f", "lavfi",
            "-i", "anullsrc=r={0}:cl=mono".format(sample_rate),
            "-t", str(duration), "-ar", str(sample_rate), "-ac", "1",
            out_path
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30)
    except subprocess.TimeoutExpired:
        result = None
    if (result is not None and result.returncode == 0
            # 必须确有数据帧：0 帧 WAV 若算成功，这句会以 0 样本混进 concat，
            # 整条时间轴悄悄前移，要靠拼接对账才兜得住。不能用尺寸 >44 判
            # （ffmpeg 的 LIST/INFO 元数据块让 0 帧文件也有 ~78 字节）。
            and _wav_has_frames(out_path)):
        return
    # Fallback: write silent WAV via Python wave module (no ffmpeg lavfi needed)
    try:
        n_frames = int(duration * sample_rate)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(sample_rate)
            # Silent frames = all zeros（bytes 直乘，比 struct.pack 巨型参数列表便宜得多）
            w.writeframes(b"\x00\x00" * n_frames)
        return
    except Exception as e:
        raise RuntimeError(
            f"生成静音文件失败（ffmpeg lavfi 与 Python wave 兜底都不可用）: "
            f"{out_path}: {e}") from e


def build_atempo_filter(speed):
    """Build an ffmpeg atempo filter chain.

    A single atempo instance only supports 0.5x–2.0x, so decompose a
    wider range into chained instances (e.g. 3.0x -> atempo=2.0,atempo=1.5).

    入口先做一次 validate_speed：speed<=0（或非有限值）时，下面第二个
    `while remaining < 0.5` 会因 `remaining /= 0.5` 对非正数永远不收敛而死循环，
    这里改为立即抛 ValueError，而不是挂死。
    """
    validate_speed(speed)
    if not needs_speed_change(speed):
        return None
    factors = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(round(remaining, 4))
    return ",".join(f"atempo={f}" for f in factors)


def apply_speed(ffmpeg_path, wav_path, speed, prev_speed=None):
    """Apply a tempo change to a WAV via ffmpeg atempo.

    Returns True on success. Used to enforce a deterministic speech-speed
    multiplier (e.g. 1.5x) regardless of what the TTS model produces.

    Non-destructive: the original TTS WAV is preserved as `<wav_path>.orig.wav`
    on first invocation. Subsequent re-application (e.g. switching from 1.5x
    to 1.2x in a later run) reads from the original instead of compounding
    atempo on an already-modified file. When speed=1.0 and an original backup
    exists, the backup is restored to wav_path so the audio returns to natural
    pace without re-calling the TTS API.

    `prev_speed`（可选）：当前 wav 已被施加过的语速（来自 .spd sidecar，
    pipeline 的 resume 分支传入）。仅在 `<wav_path>.orig.wav` 原始备份丢失
    时生效：此时无法回到原速再变速，改为补偿变速 atempo(speed/prev_speed)
    ——数学上等价于从原速一次变速到 speed，避免"已 1.5x 的文件被当作原速
    备份、再叠 1.2x 实际变成 1.8x"的静默叠加变速。
    """
    filt = build_atempo_filter(speed)
    orig_path = wav_path + ".orig.wav"

    # 原始备份丢失、但调用方告知当前文件已被变速过：走补偿变速，
    # 且不能再把当前（已变速的）文件备份成 .orig.wav——那会让下一次
    # 换速继续在错误的基础上叠加。
    compensate = (prev_speed is not None
                  and needs_speed_change(prev_speed)
                  and not os.path.exists(orig_path))
    if compensate:
        comp_filt = build_atempo_filter(speed / prev_speed)
        if comp_filt is None:
            return True  # speed == prev_speed，文件已在目标速率
        filt = comp_filt

    # speed=1.0 means "restore original pace": copy the backup (if any) back over
    # wav_path. Without this branch, a previous 1.5x run stays at 1.5x.
    if not filt:
        if os.path.exists(orig_path):
            try:
                shutil.copy2(orig_path, wav_path)
                return True
            except OSError as e:
                print(f"  [speed-restore] failed to restore orig: {e}",
                      file=sys.stderr)
                return False
        return True  # nothing to do (no prior speed change, no backup needed)

    if not compensate:
        # 首次备份原速音频，后续从干净源重放 atempo（避免二次变速）。
        # prev_speed≈1.0 是 .spd 给出的"当前文件就在原速"事实声明：备份必须跟着
        # 刷成当前文件。pipeline 的 sidecar 清理失败路径下，盘上可能残留上一稿的
        # 旧 .orig.wav（删不掉也改不了名），不刷新就拿旧稿音频做变速源——新字幕配
        # 旧配音复活（见 pipeline._clear_stale_sidecars）。
        refresh_backup = (prev_speed is not None
                          and not needs_speed_change(prev_speed))
        if refresh_backup or not os.path.exists(orig_path):
            try:
                shutil.copy2(wav_path, orig_path)
            except OSError:
                # 备份失败（含"旧 .orig.wav 被占用、覆盖不动"）时绝不退回
                # 用现存备份做源——直接以当前文件原地变速。
                orig_path = wav_path
    tmp = wav_path + ".spd.tmp.wav"
    # 补偿变速必须以当前文件为源（它就是最新状态）；常规路径优先用原速备份
    src = wav_path if compensate else (
        orig_path if os.path.exists(orig_path) and orig_path != wav_path else wav_path)
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", src,
            "-filter:a", filt,
            "-ar", "24000", "-ac", "1", tmp
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
    except subprocess.TimeoutExpired:
        # 超时异常原路径直接穿透（tmp 残留）；统一转成 False 走失败清理
        remove_if_exists(tmp)
        print("  [speed-skip] atempo timeout", file=sys.stderr)
        return False
    if result.returncode == 0 and _wav_has_frames(tmp):
        # 覆盖写目标必须和"成功"绑在一起判：Windows 上播放器/杀毒扫描会短暂
        # 锁住 wav，os.replace 抛 OSError 是常态而不是意外。这一层在
        # synth_sentence 的 except 里能被收敛成"该句失败"，但 pipeline 的
        # --resume 分支（还原原速 / 重新对齐语速）是直接调用，裸抛就是半条
        # 管线炸完后的 traceback。tmp 由下方的失败清理路径负责删掉。
        try:
            os.replace(tmp, wav_path)
        except OSError as e:
            print(f"  [speed-skip] 变速产物替换失败（{e}）：{wav_path}",
                  file=sys.stderr)
            remove_if_exists(tmp)
            return False
        return True
    if result.returncode == 0:
        # returncode=0 却没有可用产物（0 字节/仅 44 字节头）：ffmpeg 被信号
        # 打断或磁盘写满时的形态。当成失败，绝不让空文件顶掉原音频。
        print("  [speed-skip] atempo 产物为空，未替换原音频", file=sys.stderr)
    # atempo 失败时及时清掉半写的 tmp（多次失败堆积会留磁盘残渣）
    remove_if_exists(tmp)
    print(f"  [speed-skip] atempo failed: {result.stderr[-200:]}",
          file=sys.stderr)
    return False


def _wav_has_frames(path):
    """WAV 是否真的含有数据帧——取代 `getsize > 44` 的老判据。

    ffmpeg 的 WAV muxer 默认往文件里写 LIST/INFO 元数据块（encoder tag，
    约 30+ 字节），实测 0 帧文件就有 ~78 字节：仅头无数据的坏产物会穿过
    ">44" 被判成成功，混进 concat 后整条时间轴悄悄前移。改读 wave 头的
    真实帧数；wave 读不出（扩展头/非 WAV）时退回尺寸判据——所有调用点
    都显式 `-c:a pcm_s16le`，正常产物必然可读。
    """
    if not os.path.exists(path):
        return False
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() > 0
    except Exception:
        try:
            return os.path.getsize(path) > 44
        except OSError:
            # 判帧窗口内文件被并发删除：按"无帧"处理，绝不让 OSError
            # 冒到调用点把"检查产物"变成整条管线的 traceback。
            return False


def _wav_format(audio_path):
    """(采样率, 声道数, 位深)；读不出（非 WAV/扩展头/损坏）返回 None。"""
    try:
        with wave.open(audio_path, "rb") as w:
            return (w.getframerate(), w.getnchannels(), w.getsampwidth())
    except Exception:
        return None


def _normalize_wav(ffmpeg_path, src, dst, target_fmt):
    """把单个音频重采样成 target (采样率, 声道, 16-bit PCM)。失败返回 False。

    只在 concat 前被调用到"格式与多数派不符"的那几个文件上，不是全量重编码。
    """
    rate, channels, _width = target_fmt
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", src,
            "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", dst
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
    except subprocess.TimeoutExpired:
        result = None
    if (result is not None and result.returncode == 0
            and _wav_has_frames(dst)):
        return True
    if result is not None:
        print(f"  [concat] 格式归一失败 {src}: {result.stderr[-200:]}",
              file=sys.stderr)
    else:
        print(f"  [concat] 格式归一超时 {src}", file=sys.stderr)
    remove_if_exists(dst)
    return False


def concat_audio(ffmpeg_path, file_list, gap_sec, out_path):
    """Concatenate audio files with silence gaps. Uses absolute paths (Windows safe).

    格式归一仍走 ffmpeg（标准库不会重采样），但**拼接本体用标准库 wave
    逐帧串接**，不再经 ffmpeg concat demuxer：demuxer 对混格式会"返回 0
    却产出截断音频"，copy 路径还要求经列表文件（Windows 绝对路径与路径中
    单引号的转义两处历史坑）。wave 逐帧拷贝两类都不存在，gap 还拿到整帧
    精度（round(gap×rate) 帧，不再受 lavfi `-t` 的量化影响）。
    """
    out_dir = os.path.dirname(os.path.abspath(out_path))

    if not file_list:
        print("  [concat] 输入列表为空", file=sys.stderr)
        return False

    # 混合采样率必须在这里挡掉：串接只写一个目标格式头、按字节拷帧，
    # 少数派若不先重采样成目标格式，就会被按错误的采样率播放（音调偏移、
    # 总时长与 manifest 错位，且没有任何报错信号——demuxer 时代实测：
    # 44100Hz 与 24000Hz 各 0.3s 拼出"返回 0 却只剩 0.627s"的产物，规则
    # 沿用至今：喂给串接的输入必须同格式）。
    # 目标格式取语音句里的多数派，位深一律 16-bit（apply_speed 产物与静音文件
    # 都是 16-bit）。不写死 24000：--speed 1.0 时句子保持 TTS 原生格式（如 44.1k）。
    counts = {}
    for fmt in (_wav_format(p) for p in file_list):
        if fmt:
            key = (fmt[0], fmt[1], 2)
            counts[key] = counts.get(key, 0) + 1
    target_fmt = (max(counts.items(), key=lambda kv: kv[1])[0]
                  if counts else (24000, 1, 2))

    expanded = []
    # 归一化出来的句子副本（归一化中途失败也要收走）
    temp_files = []
    for i, fp in enumerate(file_list):
        src_path = fp
        if _wav_format(fp) != target_fmt:
            dst = os.path.join(out_dir, f"_concat_norm{i}.wav")
            if not _normalize_wav(ffmpeg_path, fp, dst, target_fmt):
                for tmp in temp_files:
                    remove_if_exists(tmp)
                return False
            temp_files.append(dst)
            src_path = dst
        expanded.append(src_path)

    rate, channels = target_fmt[0], target_fmt[1]
    gap_frames = round(gap_sec * rate) if gap_sec > 0 else 0
    gap_bytes = b"\x00" * (gap_frames * channels * 2)
    try:
        with wave.open(out_path, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)  # 16-bit（归一循环已保证全部输入同格式同位深）
            w.setframerate(rate)
            for i, p in enumerate(expanded):
                with wave.open(p, "rb") as r:
                    w.writeframes(r.readframes(r.getnframes()))
                if i < len(expanded) - 1 and gap_frames:
                    w.writeframes(gap_bytes)
    except Exception as e:
        # 读不出来的文件（归一后仍非标准 PCM16 WAV）在这里唯一正确的行为
        # 是大声失败：宁可 [error] Audio concat failed 停住，也不让某一句
        # 被静默跳过后整条时间轴悄悄前移。
        print(f"  [concat] 标准库拼接失败: {e}", file=sys.stderr)
        remove_if_exists(out_path)
        return False
    finally:
        # 尽力回收归一化副本（删不掉也不影响已产出的母带）
        for tmp in temp_files:
            remove_if_exists(tmp)

    return True


def mix_bgm(ffmpeg_path, voice_path, bgm_path, bgm_volume, out_path):
    """Mix background music under voice audio. BGM loops to match voice duration.

    全程使用固定的 bgm_volume。
    """
    volume_filter = f"volume={bgm_volume}"
    # 输出格式跟着人声母带走，不写死 24000/mono（同 apply_loudnorm 的理由）。
    # 人声读不出头时退回 24000/mono：混音输入已不正常，宁可保守。
    voice_fmt = _wav_format(voice_path)
    rate, channels = (voice_fmt[0], voice_fmt[1]) if voice_fmt else (24000, 1)
    # normalize=0：amix 默认把每路输入各乘 1/inputs（两路即人声 -6dB），
    # 带 BGM 的成片会系统性比不带的一半响度；关掉 normalize 后音量
    # 关系完全交给 volume_filter 控制
    try:
        result = subprocess.run([
            ffmpeg_path, "-y",
            "-i", voice_path,
            "-i", bgm_path,
            "-filter_complex",
            # aloop size expects an integer; 2e+09 (Python float literal) would
            # be passed verbatim into the ffmpeg filter string and may fail to
            # parse on some ffmpeg builds, causing BGM loop to silently break.
            f"[1:a]{volume_filter},aloop=loop=-1:size=2000000000[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0",
            "-ar", str(rate), "-ac", str(channels),
            "-c:a", "pcm_s16le", out_path
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300)
    except subprocess.TimeoutExpired:
        print("  [BGM mix failed] ffmpeg 混音超时", file=sys.stderr)
        remove_if_exists(out_path)
        return False
    if result.returncode != 0:
        print(f"  [BGM mix failed] {result.stderr[-300:]}", file=sys.stderr)
        remove_if_exists(out_path)
        return False
    # amix + aloop 会以 returncode=0 退出却吐出一个空/极短文件（BGM 本身不是
    # 音频流、或滤镜图被静默截断），调用方拿它当"带 BGM 的母带"，而总时长仍是
    # 混音前量的那个值——manifest 完全合法，成片却是无声/短片。与 loudnorm 同
    # 一道口径：产物必须真有数据帧才算成功。
    if not _wav_has_frames(out_path):
        print("  [BGM mix failed] 混音产物为空，未替换人声音频", file=sys.stderr)
        remove_if_exists(out_path)
        return False
    return True


def apply_loudnorm(ffmpeg_path, in_path, out_path, target_lufs):
    """对整条音频做响度归一化，输出到 out_path。返回是否成功。

    用于把逐句 TTS 拼出来的音频统一到目标响度（跨句/跨视频音量一致）。在 concat
    之后、对 combined 整段做，loudnorm 只做增益、不做变速，不改变句子间相对时序，
    字幕时间轴仍按 timing_manifest.json 的实测值对齐。
    """
    # 单遍 loudnorm：-16 LUFS 是网络视频/播客常见响度；TP/LRA 用固定值即可。
    filt = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    # 输出格式跟着输入走，而不是写死 24000/mono：loudnorm 内部按 192kHz 处理，
    # 完全不写 -ar 会把母带上采样到 192k（实测 44.1k 输入 → 192k 输出，体积
    # 翻 4 倍），写死 24000 又会把 --speed 1.0 保生的原生 44.1k 母带悄悄降一档
    # ——同一条片子加不加 --loudness 格式都不一致，排查时最容易误判。
    in_fmt = _wav_format(in_path)
    # 读不出头（非 WAV/扩展头）时退回旧的 24000/mono：调用方喂进来的永远是
    # concat 自己的 pcm_s16le 产物，真走到这里说明输入已经不正常，宁可保守
    # 也不能让 loudnorm 的 192kHz 内部律漏进母带。
    rate, channels = (in_fmt[0], in_fmt[1]) if in_fmt else (24000, 1)
    fmt_args = ["-ar", str(rate), "-ac", str(channels)]
    try:
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", in_path,
            "-af", filt,
            *fmt_args,
            "-c:a", "pcm_s16le", out_path,
        ], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
    except subprocess.TimeoutExpired:
        # 不显式接住的话，ffmpeg 卡死时用户直接吃裸栈
        print("  [loudnorm] ffmpeg timeout (120s)", file=sys.stderr)
        remove_if_exists(out_path)
        return False
    # 产物必须真有数据帧（_wav_has_frames）：44 字节的"纯头"尺寸线会被
    # ffmpeg 的 LIST/INFO 元数据块越过，0 帧文件量出来 0 秒，调用方只会
    # 拿到一个"成功但空"的母带（mix_bgm 同口径）。
    # 失败路径一律收走产物：留着半截 combined_loud.wav，下次 --resume 或人工
    # 挑文件时它和正常产物长得一模一样。
    if result.returncode == 0 and _wav_has_frames(out_path):
        return True
    print(f"  [loudnorm] failed: {result.stderr[-200:]}", file=sys.stderr)
    remove_if_exists(out_path)
    return False

# ── FFmpeg runtime helpers ─────────────────────────────────────────
def _system_ffmpeg():
    """检测系统 PATH 上是否有能正常运行的 ffmpeg，返回路径或 None。"""
    path = shutil.which("ffmpeg")
    if not path:
        return None
    try:
        r = subprocess.run([path, "-version"], capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout:
            return path
    except Exception:
        pass
    return None


def ffmpeg_usable(ffmpeg_path):
    """探测已解析出的 ffmpeg 是否真的可执行（`-version`）。

    get_ffmpeg() 在系统 ffmpeg 与 imageio-ffmpeg 都不可用时会回退成字面量
    "ffmpeg"（通常不可执行）；调用方须在开工前用本函数预检，否则会一路
    烧完 TTS 额度才在拼接/测时长阶段报出误导性错误。
    """
    try:
        r = subprocess.run([ffmpeg_path, "-version"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def get_ffmpeg():
    """Get ffmpeg executable path.

    优先用系统自带且能运行的 ffmpeg（多数 Windows 机器已通过 winget/官网安装），
    没有才回退到 imageio-ffmpeg 打包的完整版二进制。
    返回值不保证可执行（末位回退是字面量 "ffmpeg"），调用方用 ffmpeg_usable 预检。

    兜底只接 ImportError 是不够的：imageio-ffmpeg 的 `get_ffmpeg_exe()` 在本机
    没有缓存二进制时会**现场下载**，离线/代理/缓存损坏的机器上抛的是
    RuntimeError（实测裸栈穿透到 main，末尾还留一句 "Download failed"，
    用户看不出这跟"没装 ffmpeg"是同一件事）。这里统一降级成字面量 "ffmpeg"，
    让 ffmpeg_usable 预检给出那条可执行的诊断信息。
    """
    sys_ff = _system_ffmpeg()
    if sys_ff:
        return sys_ff
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"
    except Exception as e:
        print(f"[warn] imageio-ffmpeg 兜底不可用（{type(e).__name__}: {e}）；"
              f"它需要联网下载自带二进制。改用 PATH 上的 ffmpeg。",
              file=sys.stderr)
        return "ffmpeg"


def parse_duration(stderr_text):
    """从 `ffmpeg -i` 的 stderr 解析 `Duration: HH:MM:SS.xx`，返回秒数；
    解析失败返回 None。
    """
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

