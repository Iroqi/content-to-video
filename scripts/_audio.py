#!/usr/bin/env python3
"""TTS 管线的 FFmpeg 音频操作。

包含：时长测量、静音生成、atempo 变速、拼接。所有函数都只依赖
"ffmpeg 路径 + 参数"，不碰 TTS/网络，可脱离 pipeline 单独测试。
"""
import os
import subprocess
import re
import shutil
import sys
import wave
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _timeline import validate_speed, needs_speed_change  # noqa: E402
from _script_utils import remove_if_exists, unique_tmp  # noqa: E402  失败清理与唯一中转名

# 整条配音轨的文件名：pipeline 写它（output 根），gen_hyperframes 在 manifest 的
# combined_audio 失效时按它回落（output 的 audio/ 下，由 _stage_audio_file 暂存）。
# 两边各写一份字面量时，改一侧 = 回落那条路静默退化成"永远找不到"，而报错文案
# 还写着一个不存在的文件名。
COMBINED_AUDIO_NAME = "combined.wav"

# 静音兜底、变速产物、无语音句时的串接目标格式，三处同一个数：分片各写一份
# 字面量时改一处 = 另两条路按旧采样率出声（音调偏移 + 时长与 manifest 错位，
# 且没有任何报错信号）。语音句本身保持 TTS 原生格式，不套这个常量。
DEFAULT_SAMPLE_RATE = 24000

# 变速中转文件的命名两半：**跨模块真源**——这里落盘，pipeline 的 sidecar 清理按
# `<句>.spd.*.tmp.wav` 这个 glob 扫残留。任一侧改了字面量，另一侧就扫不到残渣，
# 而残渣的下场是被当成"原速备份"再做一次变速——错语速静默上线，全程零报错。
# 不加前导点：隐藏文件扫不到；后缀必须是 .wav：ffmpeg 按输出名推断容器。
SPEED_TMP_PREFIX = ".spd."
SPEED_TMP_SUFFIX = ".tmp.wav"


def _run_ff(args, timeout):
    """ffmpeg 子进程统一入口：捕获输出、超时返回 None。

    encoding/errors 显式指定：中文 Windows 下 text=True 默认按 cp936 解码
    ffmpeg stderr（UTF-8），输出路径含中文时会先抛 UnicodeDecodeError 而
    不是走兜底。其余异常（ffmpeg 不存在等 OSError）照原样上抛，由各调用点
    决定收敛还是穿透。
    """
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


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
    except (wave.Error, EOFError, OSError):
        # 只吞"读不出样本数"的三种真实形态：非 WAV（wave.Error）、头部截断
        # （EOFError）、打不开或无权限（OSError）。非 WAV 是预期路径
        # （measure_duration 也量 media 文件），不打 warn。
        # 其余异常必须穿透：静默吞掉会退回 ffmpeg 的两位小数时长，而绕开那份
        # 量化误差正是本函数存在的理由——宁可见到报错，不要悄悄丢精度。
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
        # _run_ff 超时返回 None：与"读不出 Duration 行"同判（返回 0.0），
        # 调用方本就按 0.0 = 无效处理
        result = _run_ff([ffmpeg_path, "-i", audio_path], 30)
        dur = parse_duration(result.stderr if result else "")
    except Exception as e:
        print(f"  [duration] error measuring {audio_path}: {e}",
              file=sys.stderr)
        return 0.0
    return dur if dur is not None else 0.0


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
    sample_rate = DEFAULT_SAMPLE_RATE
    # lavfi 卡死 30s（_run_ff 超时返回 None）的 ffmpeg 写不出比 Python wave
    # 更好的静音——超时与失败一并落 wave 兜底。
    result = _run_ff([
        ffmpeg_path, "-y", "-f", "lavfi",
        "-i", "anullsrc=r={0}:cl=mono".format(sample_rate),
        "-t", str(duration), "-ar", str(sample_rate), "-ac", "1",
        out_path
    ], 30)
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
    ——避免"已 1.5x 的文件被当作原速备份、再叠 1.2x 实际变成 1.8x"的静默
    叠加变速。

    **补偿是近似，不是数学等价。** atempo 的输出长度由 WSOLA 的搜索窗决定，
    并不严格等于 时长/atempo（实测 0.5s 音频：atempo=1.5 得 0.3385s 而非
    0.3333s）。两次 atempo 的误差会部分抵消但不会归零——实测
    prev→speed 各档补偿后与目标时长相差 **0.98~1.03 倍**（最坏 1.5→2.0 差
    2.6%，约 0.03 个半音）。所以这条路径只该在"原速备份丢了"时用，且必须
    出声（见下方 `[speed-compensate]` warn）：它换回来的是"不再叠加"，不是
    "与新合成逐样本一致"。
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
        # 补偿变速是"不再叠加"的次优解：见函数 docstring 的实测区间。这条
        # 路径只在 .orig.wav 丢失时走到，而它又是静默的——不打印的话，
        # "语速好像差一点点"永远查不到这儿。点名备份缺失与近似程度，
        # 并给出恢复精确解的那一步（删掉重合成）。
        print(f"  [speed-compensate] {os.path.basename(wav_path)} 的原速备份"
              f"（{os.path.basename(orig_path)}）缺失，改用补偿变速 "
              f"atempo={speed / prev_speed:.4f}（{prev_speed:g}→{speed:g}）——"
              "近似解，与目标语速有约 ±3% 偏差（atempo 自身的长度误差）。"
              "要精确值就删掉该句音频与 sidecar 重新合成。",
              file=sys.stderr)

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
    # 后缀必须是 .wav：ffmpeg 按输出名推断容器格式。中段留随机串以避开名撞车
    # （同一 -o 目录并行跑时两条 FFmpeg 写同一个中转会互踩）——所以这里不加前导
    # 点：pipeline 的 sidecar 清理按 `<句>.spd.*.tmp.wav` 扫它，隐藏文件扫不到。
    # 名字的两半是**跨模块真源**（SPEED_TMP_PREFIX / SPEED_TMP_SUFFIX，见文件
    # 头）：清理那一侧照字面拼一遍的话，改一侧另一侧就扫不到残渣，而残渣的下
    # 场是被当成原速源再做一次变速——错语速静默上线。
    tmp = unique_tmp(os.path.dirname(os.path.abspath(wav_path)),
                     os.path.basename(wav_path) + SPEED_TMP_PREFIX,
                     SPEED_TMP_SUFFIX)
    # 补偿变速必须以当前文件为源（它就是最新状态）；常规路径优先用原速备份
    src = wav_path if compensate else (
        orig_path if os.path.exists(orig_path) and orig_path != wav_path else wav_path)
    try:
        result = _run_ff([
            ffmpeg_path, "-y", "-i", src,
            "-filter:a", filt,
            "-ar", str(DEFAULT_SAMPLE_RATE), "-ac", "1", tmp
        ], 60)
    except OSError as e:
        # _run_ff 只兜超时；ffmpeg 半路没了/权限坏了这类 OSError 原样上抛时，
        # 上面 mkstemp 建的空 tmp 会留在盘上（sidecar 清理按 glob 扫得到，但
        # 那是下一次跑同一句才发生；本次运行里它就是残渣）。清掉再按失败返回，
        # 与 atempo 非零退出同一口径（保留原速，调用方已各自收敛）。
        remove_if_exists(tmp)
        print(f"  [speed-skip] atempo 起不来（{e}）：{wav_path}", file=sys.stderr)
        return False
    if result is None:
        # 超时 = 拿不到 returncode，tmp 可能留残骸；统一转成 False 走失败清理
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
        # 这里必须 own 掉清理与收尾：往下走会再补一条 "atempo failed"——
        # returncode 明明是 0，一次失败打两条 warn，第二条的 stderr 还常常
        # 是空的，读日志的人会以为有两个独立故障。
        remove_if_exists(tmp)
        print("  [speed-skip] atempo 产物为空，未替换原音频", file=sys.stderr)
        return False
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
    真实帧数。

    wave 读不出（截断 / 扩展头 / 打不开）一律按"无帧"：**不能再退回尺寸判据**——
    那条判据就是本函数被发明来取代的，退回它等于在"最读不出的那份产物"上
    重新启用它。三个调用点都显式 `-c:a pcm_s16le` 或走 wave 模块写盘，
    产物必然是可解析的 WAV；读不出就是坏产物，报出来好过混进去。
    """
    if not os.path.exists(path):
        return False
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError, OSError, ValueError):
        # 判帧窗口内文件被并发删除、头部截断、非 WAV：都按"无帧"处理，绝不让
        # 异常冒到调用点把"检查产物"变成整条管线的 traceback。
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
    result = _run_ff([
        ffmpeg_path, "-y", "-i", src,
        "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", dst
    ], 60)
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
    counts = Counter((fmt[0], fmt[1], 2)
                     for fmt in (_wav_format(p) for p in file_list) if fmt)
    # most_common 与 max(items, key=计数) 同为"平票取先出现者"，换手不改判据
    target_fmt = (counts.most_common(1)[0][0]
                  if counts else (DEFAULT_SAMPLE_RATE, 1, 2))

    expanded = []
    # 归一化出来的句子副本（归一化中途失败也要收走）
    temp_files = []
    for i, fp in enumerate(file_list):
        src_path = fp
        if _wav_format(fp) != target_fmt:
            # 同样是中转产物：唯一名，两次拼接并行跑同一 out_dir 不会互盖副本
            dst = unique_tmp(out_dir, f"_concat_norm{i}.", ".wav")
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


# ── FFmpeg runtime helpers ─────────────────────────────────────────
def ffmpeg_usable(ffmpeg_path):
    """探测已解析出的 ffmpeg 是否真的可执行（`-version`）。

    get_ffmpeg() 在系统 ffmpeg 与 imageio-ffmpeg 都不可用时会回退成字面量
    "ffmpeg"（通常不可执行）；调用方须在开工前用本函数预检，否则会一路
    烧完 TTS 额度才在拼接/测时长阶段报出误导性错误。

    除了 returncode 还要求有输出：Windows 上"应用执行别名"的 AppInstallerCLI
    stub 打开微软商店页面也返回 0 且静默，只查 returncode 会把它当成可用的
    ffmpeg 放行。
    """
    try:
        r = subprocess.run([ffmpeg_path, "-version"],
                           capture_output=True, timeout=10)
        return r.returncode == 0 and bool(r.stdout or r.stderr)
    except Exception:
        return False


def get_ffmpeg():
    """Get ffmpeg executable path.

    优先用系统自带且能运行的 ffmpeg（PATH 探测与可用性判定共用
    ffmpeg_usable，多数 Windows 机器已通过 winget/官网安装），没有才回退到
    imageio-ffmpeg 打包的完整版二进制。
    返回值不保证可执行（末位回退是字面量 "ffmpeg"），调用方用 ffmpeg_usable 预检。

    兜底只接 ImportError 是不够的：imageio-ffmpeg 的 `get_ffmpeg_exe()` 在本机
    没有缓存二进制时会**现场下载**，离线/代理/缓存损坏的机器上抛的是
    RuntimeError（实测裸栈穿透到 main，末尾还留一句 "Download failed"，
    用户看不出这跟"没装 ffmpeg"是同一件事）。这里统一降级成字面量 "ffmpeg"，
    让 ffmpeg_usable 预检给出那条可执行的诊断信息。
    """
    sys_ff = shutil.which("ffmpeg")
    if sys_ff and ffmpeg_usable(sys_ff):
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


def parse_video_size(stderr_text):
    """从 `ffmpeg -i` 的 stderr 解析视频流的像素尺寸 `(w, h)`；没有视频流或
    解析失败返回 None。

    与 parse_duration 同一条路：视频的时长与尺寸都只有 ffmpeg 量得出来（没有
    ffprobe，也不引 Pillow），而那一次全解码探测已经在跑了，这里只多读一行
    stderr，不再多起一次子进程。

    只认流信息行（`Stream #…: Video:`）里的那个 `WxH`：同一行后面还会跟着
    `[SAR 1:1 DAR 16:9]` 这类比例对，第一个 `WxH` 才是实际编码像素。
    """
    for line in (stderr_text or "").splitlines():
        if "Video:" not in line:
            continue
        m = re.search(r",\s*(\d{2,5})x(\d{2,5})\b", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None

