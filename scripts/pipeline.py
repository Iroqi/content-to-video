#!/usr/bin/env python3
"""content-to-video TTS Pipeline：结构化写稿 → 逐句 TTS → 拼接音频 +
timing_manifest.json（字幕时间轴的唯一来源）。

参数与行为详见 SKILL.md 第 2 步与 references/tts_pipeline.md；
完整参数列表见 ``--help``。
"""
import argparse
import concurrent.futures
from dataclasses import dataclass
import glob
import hashlib
import math
import os
import random
import shlex
import sys
from urllib.parse import unquote
import time


sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _audio import (apply_speed, concat_audio,  # noqa: E402
                    ffmpeg_usable, generate_silence, get_ffmpeg, measure_duration,
                    wav_data_consistent, COMBINED_AUDIO_NAME,
                    SPEED_TMP_PREFIX, SPEED_TMP_SUFFIX)
# 核心管线不反向依赖任何可选脚本：默认倍速与时长估算一律从契约模块取
from _timeline import (DEFAULT_SPEED, DEFAULT_GAP, TIMELINE_TOLERANCE,  # noqa: E402
                       DEFAULT_CHARS_PER_SEC, estimate_sentence_seconds,
                       needs_speed_change, speed_marker_value, validate_speed)
from _segments import is_content_sid  # noqa: E402
from _voices import list_voice_ids  # noqa: E402
from _speech import measure_voiced_span  # noqa: E402  句首发声点与发声长度（见 _speech.py）
from _source_schema import load_segments_source  # noqa: E402
from _manifest_schema import validate_timing_manifest, MANIFEST_FILENAME  # noqa: E402
import _degraded as D  # noqa: E402  降级键常量（拼错即 AttributeError）
from _script_utils import (setup_stdio, guard_not_in_skill_dir,  # noqa: E402  重定向场景 UTF-8 + 产物路径守卫
                           write_json_atomic, remove_if_exists)
from build_from_structured import build_parts  # noqa: E402
# TTS 网络层收在 provider 协议模块（契约见 _tts.py 文件头）；管线只拿
# 默认模型/base URL、client 构造、重试裁决与提供方元信息。
# **不再直接 import 具体提供方的类**：那是"加一家要改 pipeline"的旧形状，
# 现在是 `make_client(provider, ...)` 按名字取。测试要替换 client 就 patch
# 下面那个 TTS_CLIENT_FACTORY 接缝，不要回来 patch 类名。
from _tts import (DEFAULT_MODEL, DEFAULT_BASE_URL,  # noqa: E402
                 DEFAULT_PROVIDER, PROVIDER_NAMES, make_client,
                 is_non_retryable, provider_label, provider_needs_api_key,
                 provider_takes_base_url_for_instance)

#: 测试/嵌入方的注入点：非 None 时替代 PROVIDERS 里的构造器，签名
#: `(api_key, base_url) -> client`。模块级名字而不是参数，是为了让
#: `patch.object(pipeline, "TTS_CLIENT_FACTORY", ...)` 能一次性覆盖
#: main() 里那两次构造（探针 + 真客户端）。
TTS_CLIENT_FACTORY = None

# 密钥的两级查找路径：CLI > os.environ > 这份用户级 .env
_USER_ENV_PATH = os.path.join(os.path.expanduser("~"), ".config", "ai-video", ".env")


def _decode_env(raw, path):
    """`.env` 的字节 → 文本；解不出来返回 None（已打过带处置建议的 warn）。

    ① 有 UTF-16 BOM（PowerShell 5.1 的 `>` 会写它）→ utf-16；
    ② 严格 utf-8-sig；解出 NUL 字节则弃用（无 BOM UTF-16 的 ASCII 文本在 utf-8
       下每两字节一个 0x00，能"解码成功"但内容是 mojibake）；
    ③ 仍失败 → gb18030（记事本 ANSI 保存的中文注释），再失败就报编码错。

    编码错必须在这里就报：穿透到 main() 会被读成"没有 API key"。
    """
    for enc in ("utf-16", "utf-8-sig"):
        if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text
    if b"\x00" in raw:
        print(f"[warn] {path} 内容含 NUL 字节——多半是无 BOM 的 UTF-16 编码"
              f"（PowerShell 5.1 重定向产物）。请用 UTF-8 重新保存该文件",
              file=sys.stderr)
        return None
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        print(f"[warn] 无法读取 {path}: 不是可识别的文本编码"
              f"(尝试过 utf-8 / utf-16 / gb18030)。"
              f"请用 UTF-8 重新保存该文件（记事本另存为右下角选 UTF-8）",
              file=sys.stderr)
        return None


def _parse_env_file(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict。

    编码判别见 `_decode_env`。值里的百分号转义（`abc%2Fdef`）按 URL 解码——
    token 常连 % 一起从浏览器粘过来，不去掉就是 401。
    """
    result = {}
    if not os.path.isfile(path):
        return result
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
        return result
    text = _decode_env(raw, path)
    if text is None:
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            # 兼容 shell 习惯写法（export KEY=VALUE）：
            # key 上的 export 前缀剥掉，否则查表永远 miss
            k = k.removeprefix("export ").strip()
            v = v.strip()
            # 引号/转义交给 shlex（手写 find(引号) 吃不下 `"a\"b"`）；
            # 闭引号之后的行内注释 / 多余词丢掉。
            if v[:1] in ("\"", "'"):
                try:
                    parts = shlex.split(v)
                except ValueError:
                    parts = [v]      # 引号不成对：原样保留，别静默改值
                v = parts[0] if parts else ""
            elif " #" in v:
                # 未加引号的值才可能有行内注释；只认"空格 + #"——紧贴的 #
                # 是值本身（`#f59e0b`），不能动。
                v = v.split(" #", 1)[0].rstrip()
            if "%" in v:
                v = unquote(v)
            if k:
                result[k] = v
    return result


def load_env():
    """
    合并两级 .env 来源（`~/.config/ai-video/.env` < `os.environ`），返回 dict。
    """
    merged = {}
    user_env = _parse_env_file(_USER_ENV_PATH)
    merged.update(user_env)

    # 最高优先：系统环境变量（只取非空值）
    for k, v in os.environ.items():
        if v:
            merged[k] = v

    return merged


# ===================================================================
# 单句合成（网络请求经 _tts.py 的 provider 协议，本函数只管落盘/重试/变速）
# ===================================================================
def _sentence_entry(index, text, path, duration, ffmpeg_path):
    """一句 → manifest 行（音频块长度 + 可选发声区间），三条产出路径共用。

    加字段只改这里：三条路径（缓存命中 / 变速重测 / 新合成）各写一份就会出现
    "有的句子有 `onset` 有的没有"，而下游是逐句读的，缺字段的句子静默退回旧行为。

    `onset`/`voiced` 见 `_speech.py`：量不出来就不写（下游按文件长度计时），
    不进 degraded——只是少了精度，不改产物内容。
    """
    dur = round(duration, 3)
    entry = {"index": index, "text": text, "file": path, "duration": dur}
    span = measure_voiced_span(ffmpeg_path, path, dur)
    if span:
        entry["onset"], entry["voiced"] = span
    return entry


# 每句音频 (sNNN.wav) 的伴生文件后缀：.orig.wav 原速备份、.spd/.spd.*.tmp.wav
# 语速状态、.failed 放弃标记。凡动一句的音频都要连这些一起动。
# 条目是**拼在句子路径后的 glob 片段**，不是字面后缀：变速中转名带 mkstemp
# 随机串，写成 `.spd.tmp.wav` 就扫不到残留。
# 放弃标记的后缀收成常量：它在四处各拼一次字面量（本行、指纹比对时的探测、
# 兜底分支的改写、补录成功后的清理），改名时漏一处 = "这句是静音占位"这个事实
# 悄悄读不到或清不掉。
_FAILED_SUFFIX = ".failed"
# 变速中转名带 mkstemp 随机串，所以那一格是 `前缀 + * + 后缀` 的 glob 片段，
# 不能写字面后缀（见 _audio.SPEED_TMP_*）。
_SIDECAR_SUFFIXES = (".orig.wav", ".spd",
                     SPEED_TMP_PREFIX + "*" + SPEED_TMP_SUFFIX, _FAILED_SUFFIX)


def _sidecar_paths(out_path, suffixes):
    """out_path 的全部伴生文件路径（含隔离出的 `.stale` 变体）。

    清理失败改名隔离的那条路径（见 _clear_stale_sidecars）产出 `<名>.stale`：
    隔离即终点，没有任何 glob 再扫它——只进不出，永久留在输出目录。所以每个
    片段除了本体还要并上 `<片段>.stale`：锁释放后的下一轮把残渣收走。
    """
    for suffix in suffixes:
        base = glob.escape(out_path) + suffix
        yield from glob.glob(base)
        yield from glob.glob(base + ".stale")


def _clear_stale_sidecars(out_path):
    """新合成前清掉上一轮残留的变速/兜底 sidecar。

    返回是否清干净（或本来没有残留）。删不掉（Windows 下被占用）就先改名加
    `.stale` 隔离；改名也失败才返回 False，调用方必须据此跳过变速——残留的旧
    .orig.wav 会顶掉新音频（改稿后不带 --resume 重跑即触发，成片念旧稿配新字幕）。
    `.stale` 残渣本身不参与变速/续跑判读（那些只认精确名），扫到就顺手删，
    删不掉也隔离过一轮了，不再改名也不再计失败。
    """
    ok = True
    for p in _sidecar_paths(out_path, _SIDECAR_SUFFIXES):
        if p.endswith(".stale"):
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        try:
            os.remove(p)
        except OSError:
            try:
                os.replace(p, p + ".stale")
                print(f"    [sidecar] {os.path.basename(p)} 被占用无法删除，"
                      f"已改名隔离为 {os.path.basename(p)}.stale",
                      file=sys.stderr, flush=True)
            except OSError:
                ok = False
                print(f"    [sidecar][warn] {os.path.basename(p)} 删除与改名"
                      f"隔离均失败（文件被占用？）", file=sys.stderr, flush=True)
    return ok


def _log_label(text, sentence_label=None):
    """日志里代表这一句的字符串：优先用调用方给的 label，否则取原文前 30 字。

    省略号只在**真的截断了**才补。短句子挂上 `...` 会让日志读起来像后半句被吞了，
    排查 `[fail]` 时误判成"音频被截短"那一类故障。
    """
    if sentence_label:
        return sentence_label
    return text[:30] + ("..." if len(text) > 30 else "")


def synth_sentence(client, text, voice_id, voice_style, out_path,
                   ffmpeg_path, speed, model, api_timeout,
                   max_retries=3, sentence_label=""):
    """Synthesize one sentence through the provider protocol (_tts.py).
    Returns (ok, speed_applied).

    ok：TTS 音频是否成功落盘；speed_applied：atempo 变速是否落上
    （ok=True 而 speed_applied=False 时音频有效但仍是原速——调用方据此
    在 .spd 里写 1.0 这一事实，而非请求语速；写请求语速才会把原速音频
    永久钉成"已在目标速率"，见 _write_sentence_sidecars）。

    语速在合成后用 ffmpeg atempo 精确变速，与 TTS 模型的自然语速无关。

    **API 失败不碰磁盘**：本函数只有在写盘那一步才真正拥有 out_path。API 阶段
    失败（400/401/重试耗尽）时盘上那份是**上一次成功合成的同句 WAV**——删了它，
    再配 `--on-fail silence` 就等于拿静音覆盖掉一条好配音，而且 sidecar 随后
    为这条静音重写指纹，下次 `--resume` 命中缓存，好音频再也回不来。要清的从来
    只有"本次写了一半"的那种文件，而那一种只在写盘异常里出现。
    """
    for attempt in range(max_retries):
        try:
            audio_bytes = client.synthesize(text, voice_id, voice_style,
                                            model, api_timeout)
        except Exception as e:
            label = _log_label(text, sentence_label)
            if is_non_retryable(e):
                print(f"    [{label}][fatal] {e}（确定性失败，不重试）",
                      flush=True)
                return False, False
            # 最后一次不再打 retry：打出来会让人以为还有下一次重试在排队，
            # 排障时会去找"第 4 次"并不存在。耗尽就直说耗尽。
            # 不用嵌套 f-string（同一引号族要 3.12+，本技能下限是 3.9）。
            if attempt == max_retries - 1:
                tag = "giveup"
            else:
                tag = "retry %d/%d" % (attempt + 1, max_retries)
            print(f"    [{label}][{tag}] {e}", flush=True)
            if attempt < max_retries - 1:
                # 线性退避 + 随机抖动：多 worker 在 429 下若同步休眠同步
                # 唤醒，会一起撞上限流窗口反复踩踏；抖动把重试时间打散
                time.sleep(2 * (attempt + 1) + random.uniform(0.0, 1.0))
            continue
        # 落盘在重试循环之外：API 已成功，磁盘满/路径错误这类 OSError 重试
        # 救不回来，只会再发起 billable 调用白烧 2 次额度——按确定性失败处理。
        try:
            with open(out_path, 'wb') as f:
                f.write(audio_bytes)
        except OSError as e:
            label = _log_label(text, sentence_label)
            print(f"    [{label}][fatal] 音频写盘失败（{e}），不重试",
                  file=sys.stderr, flush=True)
            # 写盘失败才清：半截 WAV 的 wave 头可能已完整，下次 --resume 会把它
            # 当有效缓存跳过。清的是本次自己写的那一份。
            remove_if_exists(out_path)
            return False, False
        break  # API 成功、音频已落盘，跳出重试循环
    else:
        return False, False

    # 新合成 = 全新内容：先清上一轮残留 sidecar（.sha 由调用方在 synth 成功后
    # 重写，不在此处动，理由见 _clear_stale_sidecars docstring）。清理失败时不能
    # 继续 apply_speed：残留的旧 .orig.wav 会被当作原速源做变速，新音频被整体
    # 丢弃。跳过变速只损失语速且可恢复——speed_applied=False 时调用方写
    # .spd=1.0 声明"当前确在原速"，下次 --resume 在缓存音频上重放 atempo
    # （见 _write_sentence_sidecars）。
    if not _clear_stale_sidecars(out_path):
        print(f"    [{_log_label(text, sentence_label)}][speed-skip] "
              f"残留 sidecar 无法清理，跳过变速以保护新音频（原速可用）",
              file=sys.stderr, flush=True)
        return True, False

    # 语速不交给 TTS，而是音频落盘后用 ffmpeg atempo 就地变速（确定、可复现）。
    # 这一步在 API 重试循环之外——重新调 TTS 只会白烧额度；变速失败保留原速
    # 音频即可，时长由 pipeline 实测，字幕时间轴仍然准确。
    speed_applied = True
    if needs_speed_change(speed):
        try:
            speed_applied = apply_speed(ffmpeg_path, out_path, speed)
        except Exception as e:
            speed_applied = False
            label = _log_label(text, sentence_label)
            print(f"    [{label}][speed-skip] atempo 变速失败，保留原始语速: {e}",
                  flush=True)
    return True, speed_applied


# ===================================================================
# Main pipeline
# ===================================================================


def _sentence_hash(text, voice_id=None, voice_style=None, model=None,
                   base_url=None):
    """句子 TTS 输入（文本+音色+风格+模型+端点）的内容指纹（resume 缓存键）。

    缓存 wav 文件名只有句序号（s005.wav），不含内容——改了第 5 句文案后
    带 --resume 重跑会复用旧音频，而 manifest 的 text 用新稿，配音与
    字幕从此错位。合成成功时把该句输入的 sha1 写进 s005.wav.sha sidecar，
    resume 时比对：不一致视为缓存失效，删掉重合成。
    指纹除文本外还覆盖 voice_id/voice_style/model——只含文本时
    换音色后带 --resume 重跑会静默复用旧音色音频（manifest 声明的
    是新音色、实际音频是旧音色，且无任何警告）。旧缓存（纯文本指纹）
    会统一失配重合成（宁可多花一次合成时间，也不复用指纹对不上的音频）。

    **base_url 也在指纹里**，理由与上面那条一模一样：同一个模型名可以由不同
    端点提供（官方 API / 自建网关 / 代理），它们的音色实现、采样率、甚至响应的
    音频编码都未必相同。换 --base-url 后若指纹不含它，`--resume` 会静默复用
    另一个端点的音频——"请求发给了新端点、嘴里播的是旧端点合成的"这种错位
    比换音色更难查（两边都不报错，日志里还都打着新 base_url）。
    """
    payload = "\x1f".join([text, voice_id or "", voice_style or "",
                           model or "", base_url or ""])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()

def _write_spd(out_path, value, label, on_error_hint):
    """写 .spd 语速 marker，失败只警告不抛。

    两处调用点（reapply 后补写、restore 成功时替换坏 marker）语义不同但失败
    处理是同一件事：磁盘满/文件被占都不该把已合成好的音频变成丢句，也该告诉
    用户下次 --resume 会多做什么。收在一处，口径与提示语不再两份各自漂移。
    返回是否写成功。合成路径的 marker 不走这里（与 .sha 一起落，见
    _write_sentence_sidecars）。
    """
    try:
        with open(out_path + ".spd", "w", encoding="utf-8") as f:
            f.write(str(value))
        return True
    except OSError as e:
        print(f"  [{label}][warn] .spd marker 写入失败（{e}），{on_error_hint}",
              file=sys.stderr)
        return False


def _write_sentence_sidecars(out_path, text, speed, speed_applied=True,
                             voice_id=None, voice_style=None, model=None,
                             base_url=None):
    """合成/兜底成功后统一落 sidecar：内容指纹 .sha + 已施加语速 .spd。

    .spd 记录的是"当前 WAV 确实处于哪个语速"（事实），不是"用户想要哪个语速"。
    speed_applied=False 表示本次 atempo 没落上（音频确凿仍在原速）——此时写
    显式 "1.0"：若只删 marker，下次 --resume 会落到"无 .spd 也无可恢复原速
    备份"的状态，resolve_resume_state 无法证明语速只能 regen，白白重烧一次
    TTS 额度；显式 1.0 会让 resume 判 needs_reapply，直接在缓存 WAV 上重放
    atempo。绝不能在此写请求语速（那才会把原速音频永久钉成"已在目标速率"）。
    """
    with open(out_path + ".sha", "w", encoding="utf-8") as f:
        f.write(_sentence_hash(text, voice_id, voice_style, model, base_url))
    if needs_speed_change(speed):
        # 已施加的写实际语速、未施加的写显式 "1.0"（两种都是"事实"，见
        # 函数 docstring：绝不能写请求语速）。同一段 open/write 不需要两份。
        with open(out_path + ".spd", "w", encoding="utf-8") as f:
            f.write(str(speed_marker_value(speed) if speed_applied else "1.0"))
    else:
        remove_if_exists(out_path + ".spd")

def _record_sentence_cache(out_path, text, speed, speed_applied=True,
                           voice_id=None, voice_style=None, model=None,
                           base_url=None):
    """落 resume sidecar，写失败只警告、不向上抛。

    音频此刻已在盘上，sidecar 只是缓存元数据：让它抛 OSError 会被调用方的
    外层 `except Exception` 当成"这句失败"，把已经花过额度（或已经写好静音
    占位）的句子整句丢出 sentence_data——成片与字幕凭空少一句。元数据缺失的
    后果只是下次 --resume 重合成这一句，损失远小于丢句。
    """
    try:
        _write_sentence_sidecars(out_path, text, speed,
                                 speed_applied=speed_applied,
                                 voice_id=voice_id, voice_style=voice_style,
                                 model=model, base_url=base_url)
    except OSError as e:
        print(f"    [sidecar][warn] {os.path.basename(out_path)} 的 .sha/.spd "
              f"写入失败（{e}）：音频可用，但下次 --resume 会重合成这一句",
              file=sys.stderr, flush=True)


def _drop_stale_cache(out_path):
    """缓存判为失效（resolve_resume_state 返回 regen）时清掉旧音频及其全部
    sidecar，避免带着旧状态续跑。

    尽力而为：清不干净（Windows 下文件被杀毒扫描/播放器占用）也不阻断——
    调用方紧接着无条件把该句排进重合成队列，合成侧以覆盖写落盘，
    失效缓存不会被当成"文件存在=可用"。
    """
    # "" = wav 本体；.sha 只在这里清（新合成路径上 .sha 由合成成功后重写，
    # 不需要提前隔离，见 _clear_stale_sidecars 的调用注释）。
    # 同样走 glob：中转名带随机串（见 _SIDECAR_SUFFIXES 的注释）；隔离出的
    # .stale 残渣也在这里一并收走（_sidecar_paths 的第二个 glob）。
    for p in _sidecar_paths(out_path, ("", ".sha") + _SIDECAR_SUFFIXES):
        try:
            os.remove(p)
        except OSError:
            pass




# ===================================================================
# Resume 决策（纯函数：只吃已读出的边车事实，不碰文件 IO / ffmpeg；
# 副作用留在 main 执行层——读盘与写盘的错误处理因此只有一处）
# ===================================================================

@dataclass
class ResumeDecision:
    action: str
    apply_speed_to: float = None
    prev_speed: float = None
    write_spd: str = None
    restore_first: bool = False
    synth_failed: bool = False
    # 判定 regen 的成因（改稿？坏文件？换参数？）必须报出来，否则只能靠猜。
    reason: str = None


@dataclass
class ResumeFacts:
    sha_exists: bool = False
    sha_matches: bool = False
    audio_exists: bool = False
    audio_duration_ok: bool = False
    audio_duration: float = 0.0
    audio_intact: bool = True
    spd_exists: bool = False
    spd_readable: bool = False
    spd_applied: str = None
    orig_wav_exists: bool = False
    failed_marker_exists: bool = False


def resolve_resume_state(facts, requested_speed):
    # 缺 .sha 就当没缓存：否则删掉 .sha 后改稿重跑会走 cached、再给旧音频补写
    # 一个新文本的 .sha（self-certify），错位音频从此固化且再无一处报出来。
    if not facts.sha_exists:
        return ResumeDecision("regen", reason="缺少内容指纹 .sha，无法确认这段音频就是当前稿件")
    if not facts.sha_matches:
        return ResumeDecision("regen", reason="稿件内容或音色/模型已变化")
    if not facts.audio_exists:
        return ResumeDecision("regen", reason="音频文件不存在")
    if not facts.audio_duration_ok:
        return ResumeDecision("regen", reason="音频时长测量失败（文件可能已损坏）")
    if not facts.audio_intact:
        return ResumeDecision("regen", reason="WAV 头部声明的样本数超出文件实际字节（上次写入被截断）")
    restore_first = False
    applied = facts.spd_applied if facts.spd_readable else None
    if facts.spd_exists and not facts.spd_readable:
        if facts.orig_wav_exists:
            applied = 1.0
            restore_first = True
        else:
            # 无可读 marker、也无原速备份：无法证明当前语速。宁可重合成，
            # 也不让旧缓存静默带错语速配上"看似正确"的时间轴。
            return ResumeDecision("regen",
                                  reason="语速 marker 不可读且无原速备份，无法证明当前 WAV 的语速")
    if applied is None and facts.orig_wav_exists:
        # 只剩 .orig.wav（旧版或人工清理）：原速是可证明的基线，
        # 先恢复再按本次请求施加语速。
        restore_first = True
    if (applied is None
            and needs_speed_change(requested_speed)
            and not facts.orig_wav_exists):
        return ResumeDecision("regen",
                              reason="无 .spd marker 也无可恢复的原速备份，"
                                     "无法证明当前 WAV 已是请求语速")
    needs_reapply = (applied is None) or (applied != speed_marker_value(requested_speed))
    if needs_reapply:
        write_spd = (speed_marker_value(requested_speed)
                     if needs_speed_change(requested_speed) else None)
        return ResumeDecision("reapply", apply_speed_to=requested_speed,
                              prev_speed=applied, write_spd=write_spd,
                              restore_first=restore_first,
                              synth_failed=facts.failed_marker_exists)
    return ResumeDecision("use_cached",
                          restore_first=restore_first,
                          synth_failed=facts.failed_marker_exists)

def _observe_resume_state(out_path, ffmpeg_path, text, voice_id, voice_style,
                          model, base_url=None):
    sha_path, spd_path = out_path + ".sha", out_path + ".spd"
    sha_exists = os.path.exists(sha_path)
    sha_matches = False
    if sha_exists:
        try:
            with open(sha_path, encoding="utf-8") as f:
                cached = f.read().strip()
        except (OSError, UnicodeDecodeError):
            cached = ""
        sha_matches = (cached == _sentence_hash(text, voice_id, voice_style,
                                                model, base_url))
    audio_exists = os.path.exists(out_path)
    audio_duration_ok = False
    audio_duration = 0.0
    if audio_exists:
        audio_duration = measure_duration(ffmpeg_path, out_path)
        audio_duration_ok = audio_duration > 0
    spd_exists = os.path.exists(spd_path)
    spd_readable, spd_applied = False, None
    if spd_exists:
        try:
            with open(spd_path, encoding="utf-8") as f:
                spd_applied = float(f.read().strip())
            spd_readable = math.isfinite(spd_applied) and spd_applied > 0
        except (OSError, ValueError):
            spd_readable = False
    return ResumeFacts(
        sha_exists=sha_exists, sha_matches=sha_matches,
        audio_exists=audio_exists, audio_duration_ok=audio_duration_ok,
        audio_duration=audio_duration,
        audio_intact=(wav_data_consistent(out_path)
                      if audio_exists else True),
        spd_exists=spd_exists, spd_readable=spd_readable, spd_applied=spd_applied,
        orig_wav_exists=os.path.exists(out_path + ".orig.wav"),
        failed_marker_exists=os.path.exists(out_path + _FAILED_SUFFIX),
    )

def _tts_worker(task, client, ffmpeg_path, model, api_timeout):
    """线程池 worker：合成一个句子并返回任务与结果。"""
    ok, speed_applied = synth_sentence(
        client, task["text_tts"], task["voice_id"], task["voice_style"],
        task["out_path"], ffmpeg_path, task["speed"],
        sentence_label=task["label"], model=model,
        api_timeout=api_timeout,
    )
    return task, ok, speed_applied

def _build_parser():
    parser = argparse.ArgumentParser(description="content-to-video TTS Pipeline")
    parser.add_argument("--source", default=None,
                        help="结构化 segments_source.json 路径。"
                             "逐段独立分句，直接产出带 segments 分组的 manifest")
    parser.add_argument("-o", "--output", default=None, help="Output directory")
    parser.add_argument("--api-key", default=None,
                        help="TTS API key。优先级：命令行 > 环境变量 > "
                             "~/.config/ai-video/.env（技能目录内 .env 不参与读取）。"
                             "默认提供方 mimo 读 MIMO_API_KEY")
    parser.add_argument("--tts-provider", default=None,
                        choices=list(PROVIDER_NAMES),
                        help=f"TTS 提供方（默认 {DEFAULT_PROVIDER}，也可用环境变量 "
                             f"CTV_TTS_PROVIDER 指定）。提供方协议只有 "
                             f"synthesize 一个方法，新增一家见 scripts/_tts.py "
                             f"的 PROVIDERS；不需要 key 的提供方不受 API key 硬闸门约束")
    parser.add_argument("--voice-id", default=list_voice_ids()[0],
                        choices=list_voice_ids(),
                        help=f"Voice ID（默认 {list_voice_ids()[0]}）；"
                             "各音色的语言/性别与搭配建议见 "
                             "references/tts_pipeline.md 的预置音色表")
    parser.add_argument("--voice-style",
                        default="专业新闻播报，语速适中，语气沉稳自信，中英文表达流畅自然",
                        help="Voice style description")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP,
                        help="Silence gap between sentences (seconds)。拼接音频与 "
                             "manifest 时间轴同步插入；它同时决定转场宽度（见 "
                             "references/rendering.md「动画」）")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help="Speech speed multiplier via ffmpeg atempo "
                             "(1.0=normal, 1.5=faster). Default follows "
                             "_timeline.DEFAULT_SPEED (single source). "
                             "变速后字幕按实测时长对齐。")
    parser.add_argument("--resume", action="store_true",
                        help="仅当缓存 WAV 能被「证明」属于当前稿件时跳过重合成："
                             "内容指纹 .sha、可测时长、无截断、语速状态 .spd 全部"
                             "对上才算命中（命中打 [skip,cached]/[skip,respeed]，"
                             "失效打「缓存失效（原因）」）。判定口径见 "
                             "references/tts_pipeline.md")
    parser.add_argument("--model", default=None,
                        help=f"TTS model name (default: MIMO_TTS_MODEL env var or "
                             f"'{DEFAULT_MODEL}')")
    parser.add_argument("--base-url", default=None,
                        help=f"MiMo TTS API base URL (default: MIMO_BASE_URL env var "
                             f"or '{DEFAULT_BASE_URL}')")
    parser.add_argument("--api-timeout", type=float, default=30.0,
                        help="单次 TTS API 调用超时（默认 30s）")

    parser.add_argument("--dry-run", action="store_true",
                        help="Only split sentences + detect segments; print preview "
                             "without calling TTS API or writing audio. Useful for "
                             "verifying script formatting without spending credits.")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行 TTS 调用数（default 4）；大量 [retry] 日志时调小")
    parser.add_argument("--on-fail", choices=["abort", "silence"], default="abort",
                        help="单句 TTS 反复失败（如触发内容审核）后的处理方式："
                             "abort（默认）让任何句子失败都阻断管线，避免悄悄"
                             "丢失内容；silence 改为该句降级为静音（时长按 "
                             "estimate_sentence_seconds 同款字数/语速启发式估算），"
                             "保留在成片时间轴与字幕位置上，不阻断整条视频，"
                             "manifest 对应句子会带 \"synth_failed\": true，"
                             "方便事后定位要不要人工补录。")
    return parser


def _validate_args(parser, args):

    # ── speed 值校验（fail-fast：坏语速进入 atempo 会死循环或白烧额度）──
    try:
        validate_speed(args.speed)
    except ValueError as e:
        parser.error(str(e))

    # ── gap/workers 校验（同款 fail-fast）───────────────────────────
    # 负 gap 会让 manifest 时间轴（无条件累加 args.gap）与实际拼接（gap<=0
    # 不插静音）系统性脱节；workers<=0 会让 ThreadPoolExecutor 直接 ValueError。
    if not math.isfinite(args.gap) or args.gap < 0:
        parser.error(f"--gap 必须是非负有限数（句间静音秒数，收到 {args.gap}）；"
                     "要无间隙拼接请显式传 0")
    if args.workers < 1:
        parser.error(f"--workers 至少为 1（收到 {args.workers}）")
    # --api-timeout 直接交给 HTTP 客户端当超时用：0 意味着"每次请求立刻超时"，
    # 负数/NaN 则由客户端报一串迷惑性错误——三种情况都会让每句白重试 3 次、
    # 整条管线在烧完时间后才报"全部句子失败"。
    if not math.isfinite(args.api_timeout) or args.api_timeout <= 0:
        parser.error(f"--api-timeout 必须是大于 0 的有限秒数（收到 {args.api_timeout}）")


def api_key_for_provider(args, env, probe_client):
    """解析这家提供方要用的 key；返回空串表示"它要 key 但没给"。

    合并顺序沿用整条管线的口径：命令行 > 环境变量 > 用户级 .env（`load_env`
    已经依次串好，这里只看 `args` 与合并结果）。

    只有需要 key 的提供方才查 key：本地/离线提供方（`needs_api_key=False`）
    拿到空 key 是正常状态，不该被"没 key 就停"那道闸拦下——那道闸的用意是
    "别在没鉴权的情况下打付费端点"，不是"每条链路都必须有个 key"。
    """
    if not provider_needs_api_key(probe_client):
        return ""
    return args.api_key or env.get("MIMO_API_KEY") or ""


def _concat_voice_audio(args, ffmpeg_path, sentence_data):
    """拼接句音频成母带并做时长对账，返回 (combined_path, total_dur)。

    拼接失败 / 测量失败 / 与逐句预期对不上都是硬失败：缺音频 = 字幕从某一刻
    起整体提前，成片照样播完，宁可停住也不要悄悄少一段。
    """
    # ── Concatenate ────────────────────────────────────────────────
    print(f"\n[concat] {len(sentence_data)} clips (gap {args.gap}s)...", flush=True)
    audio_files = [s["file"] for s in sentence_data]
    combined_path = os.path.join(args.output, COMBINED_AUDIO_NAME)
    concat_ok = concat_audio(ffmpeg_path, audio_files, args.gap, combined_path)

    if not concat_ok:
        print("[error] Audio concat failed", file=sys.stderr)
        sys.exit(1)

    total_dur = measure_duration(ffmpeg_path, combined_path)
    if not total_dur or total_dur <= 0:
        # total_dur=0 能过契约的数值校验，产出"合法但废掉"的 manifest
        # （data-duration=0 渲染出无声空片），必须在这里拦。
        print("[error] combined.wav 时长测量失败（0.0s）——ffmpeg 无法读取"
              "拼接产物？检查磁盘空间与 ffmpeg 可用性", file=sys.stderr)
        sys.exit(1)
    # 对账抓的是上游单句被写坏/截断：这类文件 ffmpeg 仍量得出"正常"时长，
    # 只看头部检不出。
    expected_total = (sum(sd["duration"] for sd in sentence_data)
                      + args.gap * max(0, len(sentence_data) - 1))
    drift = expected_total - total_dur
    # 必须用 _timeline 里同一个 TIMELINE_TOLERANCE：这一步放行、下游拒收，
    # 等于额度烧完才告诉用户产物不能用。
    if drift > TIMELINE_TOLERANCE:
        print(f"[error] 拼接产物比预期短 {drift:.2f}s（实测 {total_dur:.2f}s / "
              f"预期 {expected_total:.2f}s）——多半是句子音频格式不一致或写盘被"
              f"截断。检查 {combined_path} 与音频目录里各句 WAV 的采样率/声道；"
              "确认无误后可删掉 combined.wav 重跑（--resume 会复用已合成的句子）",
              file=sys.stderr)
        sys.exit(1)
    print(f"[done] Total audio: {total_dur:.2f}s", flush=True)
    return combined_path, total_dur


def _manifest_sentence_entries(sentence_data):
    """句数据 → manifest.sentences 行（只带下游要读的键）。"""
    manifest_sentences = []
    for s in sentence_data:
        entry = {
            "index": s["index"],
            "text": s["text"],
            "start_time": s["start_time"],
            "duration": s["duration"],
        }
        if s.get("speaker"):
            # 对话段：渲染端在轮次切换的首句行首挂标签（见 html_renderer._verse_html）
            entry["speaker"] = s["speaker"]
        if s.get("synth_failed"):
            entry["synth_failed"] = True  # TTS 失败降级为静音占位（见 --on-fail）
        # 发声区间（_speech.py 量出来）：下游据此把换句/念到哪对齐真正开口的
        # 时刻。缺了就按文件长度计时。
        if "onset" in s and "voiced" in s:
            entry["onset"] = round(float(s["onset"]), 3)
            entry["voiced"] = round(float(s["voiced"]), 3)
        manifest_sentences.append(entry)
    return manifest_sentences


def _reconcile_timeline(manifest_sentences, total_dur, degraded):
    """把成片长度与时间轴终点对账，返回最终 total_dur。"""
    # 用写进 manifest 的（已 round 的）值算——与契约层"最后一句不得超出
    # total_duration + 250ms"是同一个量。宁可让 composition 比音频长几十毫秒，
    # 也不让下游按一条它自己必然违反的规则拒收。
    _timeline_end = max((s["start_time"] + s["duration"])
                        for s in manifest_sentences)
    # 0.005s 门槛吸收 round(x,3) 的累加噪声：用裸 `>` 会常打出"实测 5.48s
    # 短于终点 5.48s"这种自相矛盾的日志。
    if _timeline_end - total_dur > 0.005:
        # 超出 250ms 就超出契约容差，是音频真被截断。无条件按时间轴取值会把
        # 它抹平成 status=ok 的 manifest（片尾几秒没声），必须记 degraded 让
        # run.py 的闸门拦得住。
        _deficit = _timeline_end - total_dur
        if _deficit > TIMELINE_TOLERANCE:
            degraded[D.AUDIO_SHORTER_THAN_TIMELINE] = round(_deficit, 3)
            print(f"[duration][warn] 音频实测 {total_dur:.2f}s 比时间轴终点 "
                  f"{_timeline_end:.2f}s 短 {_deficit:.2f}s（超出 "
                  f"{TIMELINE_TOLERANCE * 1000:.0f}ms 舍入裕量，"
                  f"多半是混音/响度归一化把文件截断了），total_duration 按时间轴"
                  f"取值，末尾 {_deficit:.2f}s 无音频，已记入 degraded 明细",
                  file=sys.stderr, flush=True)
        else:
            print(f"[duration] 音频实测 {total_dur:.2f}s 短于时间轴终点 "
                  f"{_timeline_end:.2f}s，total_duration 按时间轴取值", flush=True)
        total_dur = _timeline_end
    return total_dur


def _group_segments(seg_config, manifest_sentences, degraded):
    """段落分组：manifest 的 segments 逐段构造；没有任何可用音频的段剔除并记降级。"""
    # _source_schema 保证至少一段且每段都能分句出内容，无条件执行；
    # 少一段是下面 dropped 的事。
    grouped = []
    dropped = []
    for seg in seg_config:
        start_idx = seg["start"]   # 0-based sentence index (inclusive)
        end_idx = seg["end"]       # exclusive
        seg_sentences = [
            s for s in manifest_sentences
            if start_idx <= s["index"] < end_idx
        ]
        _seg_id = seg["id"]
        if not seg_sentences:
            # 空段进 manifest 会被 _manifest_schema 拒收（"缺少非空 sentences
            # 列表"），gen_hyperframes 随之退出、整条视频出不来。剔除并点名。
            dropped.append(_seg_id)
            continue
        # tagline 兜底只给 seg… 内容段（文案须内容中立）；opening/closing 是
        # 结构段，留空即不显示小标题。
        _tagline = seg["tagline"]
        if not _tagline and is_content_sid(_seg_id):
            _tagline = "补充阅读"
        # id/title/tagline/accent 由 _segments_from_blocks 保证存在：直接下标
        # 取值，缺键就是上游写坏了，不该被默认值掩盖。
        seg_out = {
            "id": _seg_id,
            "title": seg["title"],
            "tagline": _tagline,
            "accent": seg["accent"],
            "sentences": seg_sentences,
        }
        # 可选字段原样带走（段落级 speed / 音色、layout）。
        if seg.get("speed") is not None:
            seg_out["speed"] = seg["speed"]
        if seg.get("layout") is not None:
            seg_out["layout"] = seg["layout"]
        if seg.get("voice_id") is not None:
            seg_out["voice_id"] = seg["voice_id"]
        if seg.get("voice_style") is not None:
            seg_out["voice_style"] = seg["voice_style"]
        if seg.get("takeaway") is not None:
            seg_out["takeaway"] = seg["takeaway"]
        if seg.get("turns"):
            seg_out["turns"] = seg["turns"]
        grouped.append(seg_out)
    if dropped:
        # 整段消失是最重的降级，只打 [warn] 不够——下游只看 manifest 状态。
        degraded[D.SEGMENTS_DROPPED] = list(dropped)
        # 静默剔除会让"我写了 8 段、成片只有 6 段"变成无解的困惑；
        # 报出被剔除的 sid 与原因，让失败可归因。
        # 归因必须跟控制流一致：这里剔除的是 **index 区间内一句都没有**的段
        # （区间空了 manifest 装不下），不是 TTS 失败——TTS 失败走的是
        # synth_failed 静音占位（--on-fail silence）或提前退出（abort），
        # 句子仍在表里。文案原先写着"TTS 连续失败"，指向一条不存在的路径。
        print(f"\n[warn] {len(dropped)} 个段落的句子区间内没有任何句子，"
              f"已从 manifest 剔除：{', '.join(dropped)}\n"
              f"       这通常是分段落盘的结果与句子索引对不上（段落 start/end "
              f"区间为空）；请检查上面的分段日志，修掉后重跑即可自动补回。",
              file=sys.stderr, flush=True)
    return grouped


def _validate_and_write_manifest(args, manifest, degraded):
    """降级状态收口 → 写盘前自证 → 原子落盘。

    不合格必须在产出这一刻说：留到 run.py / gen_hyperframes 加载时才报，
    等于额度花完、报了"[done] 成功"之后才告诉用户不能用。
    """
    # ── Write manifest ─────────────────────────────────────────────
    # 降级状态收口：上面任何一条"显式要了却没做到"都会把 status 翻成
    # degraded，run.py 的 --allow-degraded 闸门才有东西可拦。
    manifest["status"] = "degraded" if degraded else "ok"
    if degraded:
        print(f"[degraded] 本次产物含降级项：{degraded}\n"
              "           明细见 timing_manifest.json 的 degraded 字段；"
              "默认会阻断正式渲染，确认接受后再加 --allow-degraded。",
              file=sys.stderr, flush=True)

    # 自证：写盘前按下游同一份契约校一遍。
    try:
        validate_timing_manifest(manifest)
    except ValueError as e:
        print(f"[error] 本次生成的 timing_manifest.json 不合格，已拒绝写盘：{e}\n"
              f"       音频中间产物仍保留在 {args.output}，修掉上面这条原因后重跑"
              "（不要手工改 manifest 绕过校验）。", file=sys.stderr)
        sys.exit(1)
    # 原子写：写到一半被 Ctrl-C 会留下截断的 JSON，下次 --resume 崩在
    # json.load 且堆栈看不出"上次中断了，重跑一遍就好"。
    manifest_path = os.path.join(args.output, MANIFEST_FILENAME)
    write_json_atomic(manifest_path, manifest, indent=2)

    print(f"\n[manifest] {manifest_path}", flush=True)


def _finalize_audio_and_manifest(args, ffmpeg_path, sentence_data, source_data, seg_config,
                                 silence_fallback_count, total_sentences, cached_count,
                                 voices_used):
    """TTS 之后的收口编排：拼接 → 时长对账 → 组装 manifest → 校验落盘。

    "用户显式要了但这次没做到"的事都记进 degraded，统一翻成 status=degraded
    交给 run.py 的 --allow-degraded 闸门——只打一行滚过去的 [warn] 等于静默降级。
    """
    degraded = {}

    combined_path, total_dur = _concat_voice_audio(
        args, ffmpeg_path, sentence_data)

    # ── Calculate start times ──────────────────────────────────────
    cumulative = 0.0
    for i, sd in enumerate(sentence_data):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(sentence_data) - 1:
            cumulative += args.gap

    manifest_sentences = _manifest_sentence_entries(sentence_data)
    total_dur = _reconcile_timeline(manifest_sentences, total_dur, degraded)

    # 丢失句数按"应产出 − 实产出"算（每个 index 恰好 append 一次），不复用
    # 调用方的 failed 列表：任何一条"某句没进 manifest"的路径都要翻 degraded。
    lost_count = max(0, total_sentences - len(sentence_data))
    if silence_fallback_count:
        degraded[D.SILENCE_FALLBACK_COUNT] = silence_fallback_count
    if lost_count:
        degraded[D.LOST_SENTENCE_COUNT] = lost_count
    _voices = sorted(voices_used)
    manifest = {
        "schema_version": 2,
        # status 在写盘前按 degraded 明细统一复核（段落剔除发生在下面）。
        "status": "ok",
        "degraded": degraded,
        "sentences": manifest_sentences,
        "total_duration": round(total_dur, 3),
        "gap": args.gap,
        # 本次真正用到的音色（分段音色时顶层 --voice-id 只代表默认值）：
        # 单音色是那一个词，多音色是逗号清单。
        "voice_id": ",".join(_voices),
        "combined_audio": os.path.abspath(combined_path),
    }
    # 结尾 agenda 卡的至多一条可选尾行：行动号召或下期预告二选一，
    # 顶层字段透传进 manifest，renderer 拼在要点总结之后。
    _val = str(source_data.get("cta") or "").strip()
    if _val:
        manifest["closing_cta"] = _val

    manifest["segments"] = _group_segments(seg_config, manifest_sentences, degraded)

    _validate_and_write_manifest(args, manifest, degraded)

    # total_sentences / cached_count 必须由调用方传入：它们是 main() 的局部变量。
    # 计数按"有没有配音"算，不按行数算：--on-fail silence 的静音占位也在
    # sentence_data 里，整条 API 打不通时旧写法会打成 "6/6 sentences OK"，
    # 紧接着三行就是 [degraded] 与 run.py 的渲染阻断——自相矛盾的流水账会让
    # 人不信这条链，也就抵消了"降级显式化"的意义。
    _voiced = len(sentence_data) - silence_fallback_count
    print(f"[stats] {_voiced}/{total_sentences} sentences 有配音"
          + (f"，{silence_fallback_count} 句静音占位" if silence_fallback_count else "")
          + f"（{cached_count} cached）", flush=True)
    print(f"[duration] {total_dur:.2f}s", flush=True)

def main(argv=None):
    """argv=None 走 sys.argv；run.py 进程内直调时传入参数列表，
    参数校验只有本文件这一份 parser，run.py 不再复制。"""
    setup_stdio()
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)

    # ── Validate required args ────────────────────────────────────
    if not args.output and not args.dry_run:
        parser.error("the following arguments are required: -o/--output "
                     "(not needed for --dry-run)")
    if not args.source:
        parser.error("the following arguments are required: --source")
    # 产物路径守卫：--dry-run 承诺不写文件，不需要拦
    if not args.dry_run:
        guard_not_in_skill_dir(("-o/--output", os.path.abspath(args.output)))

    # ── Resolve config (CLI > 环境变量/~/.config/ai-video/.env > 默认值) ──
    env = load_env()
    # 提供方先定：它决定"要不要 key"、"base_url 有没有意义"。默认 mimo 时
    # 密钥环境变量名仍是 MIMO_API_KEY；换一家需要别的变量名时，在 _tts.py
    # 的 PROVIDERS 里接（本文件不认第二套变量名，避免"看起来配了其实没读到"）。
    provider = args.tts_provider or env.get("CTV_TTS_PROVIDER") or DEFAULT_PROVIDER
    model = args.model or env.get("MIMO_TTS_MODEL") or DEFAULT_MODEL
    base_url = args.base_url or env.get("MIMO_BASE_URL") or DEFAULT_BASE_URL
    # 先造一次只为"问元信息"（要不要 key / 认不认 base_url）：key 与端点
    # 都按最终值传，别传 None——MimoTtsClient 的 __init__ 就对 base_url
    # 做 rstrip，传 None 只会在解析阶段换成一条指向 __init__ 的报错。
    try:
        probe = make_client(provider, args.api_key or env.get("MIMO_API_KEY"),
                            base_url, factory=TTS_CLIENT_FACTORY)
    except ValueError as e:
        parser.error(str(e))
    if not args.dry_run and not api_key_for_provider(args, env, probe):
        print(f"[error] No API key for provider '{provider_label(probe)}'. "
              f"Use --api-key or set MIMO_API_KEY in .env", file=sys.stderr)
        sys.exit(1)
    # 只解析一次：下面构造真 client 与写缓存指纹（_sentence_hash 覆盖
    # base_url）都必须用同一个值，两处各算一遍迟早漂开。
    api_key = api_key_for_provider(args, env, probe)

    # ── Read script（--source 结构化输入，逐段独立分句）───────────────
    try:
        source_data = load_segments_source(args.source)
        sentences, seg_config = build_parts(source_data)
    except ValueError as e:
        print(f"[error] 结构化稿件无效：{e}", file=sys.stderr)
        sys.exit(1)

    print(f"[script] {sum(len(s) for s in sentences)} chars", flush=True)

    print(f"[split] {len(sentences)} sentences", flush=True)
    for i, s in enumerate(sentences):
        preview = s[:35] + "..." if len(s) > 35 else s
        print(f"  {i+1}. {preview}", flush=True)

    print(f"\n[source] 结构化输入：{len(seg_config)} 个段落，逐段独立分句"
          f"（不存在跨段短句合并问题）", flush=True)

    if args.dry_run:
        print("\n[dry-run] Sentence split + segment detection complete. "
              "No TTS calls made, no audio written.", flush=True)
        return

    # ── Setup output ───────────────────────────────────────────────
    # -o 指到已存在的同名文件（手滑把文件路径当目录传）时，makedirs 会
    # 裸抛 FileExistsError 不指向真正原因——提前拦下
    if os.path.isfile(args.output):
        print(f"[error] 输出路径 {args.output} 是一个已存在的文件，"
              f"--output 需要的是目录路径", file=sys.stderr)
        sys.exit(1)
    os.makedirs(args.output, exist_ok=True)
    sentences_dir = os.path.join(args.output, "sentences")
    os.makedirs(sentences_dir, exist_ok=True)

    ffmpeg_path = get_ffmpeg()

    # 预检：get_ffmpeg() 的末位回退是字面量 "ffmpeg"，通常不可执行。
    # 不在这里拦下，后面每条句子都会以 measure_duration=0 判失败——
    # --on-fail silence 分支的 generate_silence 有 Python wave 兜底，
    # 会把真实合成音频覆盖成静音；abort 分支则报 "All sentences failed"，
    # 两种结局都把锅指向 TTS/API 而非缺失的 ffmpeg。
    if not ffmpeg_usable(ffmpeg_path):
        print(f"[error] ffmpeg 不可用（执行 `{ffmpeg_path} -version` 失败）。"
              f"请安装 ffmpeg 或 pip install imageio-ffmpeg 后重试；"
              f"尚未发起任何 TTS 调用，额度未消耗。", file=sys.stderr)
        sys.exit(1)

    # ── Initialize TTS client ──────────────────────────────────────
    # 真正的 client 在这里造（上面那次 make_client 只用于问元信息，
    # key 与 base_url 那时还没解析完）。
    client = make_client(provider, api_key, base_url, factory=TTS_CLIENT_FACTORY)
    if provider_takes_base_url_for_instance(client):
        print(f"[api] provider={provider_label(client)} model={model} "
              f"base_url={base_url}", flush=True)
    else:
        print(f"[api] provider={provider_label(client)} model={model}", flush=True)

    # ── Build per-sentence speed / voice mapping ────────────────────
    # 每句动态语速：若段落指定了 speed 则覆盖全局 args.speed，
    # 没有指定 speed 的段落用全局 args.speed。voice 同理——不同段落
    # （比如开场用甲音色、正文用乙音色）可以分别指定 voice_id/voice_style。
    sentence_speeds = {}
    sentence_voices = {}  # index -> (voice_id, voice_style)
    sentence_speaker_labels = {}  # index -> 说话人标签（仅对话段有值；进 manifest 数据层，也是字幕上那枚行首标签的来源）
    for seg in seg_config:
        start_idx = seg["start"]
        end_idx = seg["end"]
        seg_speed = seg.get("speed")
        if seg_speed is not None:
            for si in range(start_idx, end_idx):
                sentence_speeds[si] = seg_speed
        seg_voice_id = seg.get("voice_id")
        seg_voice_style = seg.get("voice_style")
        # `is not None` 不是 `or`：空串是"这一段显式不要风格"的合法取值
        # （_tts 用它决定是否预填风格提示语），按真假判会静默退回全局 --voice-style。
        if seg_voice_id is not None or seg_voice_style is not None:
            for si in range(start_idx, end_idx):
                sentence_voices[si] = (
                    seg_voice_id or args.voice_id,
                    seg_voice_style if seg_voice_style is not None else args.voice_style,
                )
        # ── 双人对话（turns）：比 segment 更细的子区间，按句覆盖 ────
        # segment 级设置——同一段里 A/B 两人交替发言，各自用各自的音色。
        # build_from_structured.py 已把 speakers 解析成每个 turn 自带的
        # voice_id/voice_style，这里不需要再反查任何 speakers 字典。
        for turn in seg.get("turns", []):
            t_start, t_end = turn["start"], turn["end"]
            t_voice_id = turn.get("voice_id")
            t_voice_style = turn.get("voice_style")
            # label 由 build_from_structured 写成 "speakers 里的 label 或说话人
            # 名"，_source_schema 保证 speaker 非空，所以这里必然拿到非空标签。
            t_label = turn["label"]
            for si in range(t_start, t_end):
                if t_voice_id is not None or t_voice_style is not None:
                    base_id, base_style = sentence_voices.get(
                        si, (args.voice_id, args.voice_style))
                    sentence_voices[si] = (
                        t_voice_id or base_id,
                        t_voice_style if t_voice_style is not None else base_style,
                    )
                sentence_speaker_labels[si] = t_label

    # ── Generate TTS per sentence (parallel) ──────────────────────
    sentence_data = []
    failed = []
    cached_count = 0
    pending_tasks = []  # 待合成的句子任务

    # 第一遍：收集 resume 可跳过的缓存句子（直接加入 sentence_data），
    # 其余句子加入 pending_tasks 待并行处理。
    for i, sent_tts in enumerate(sentences):
        out_path = os.path.join(sentences_dir, f"s{i+1:03d}.wav")
        label = f"s{i+1:03d}/{len(sentences):03d}"
        sent_speed = sentence_speeds.get(i, args.speed)
        sent_voice_id, sent_voice_style = sentence_voices.get(i, (args.voice_id, args.voice_style))
        # 本次要合成的这一句（四条入队路径共用同一个形状，见下方 append）
        task = {"index": i, "text_tts": sent_tts, "out_path": out_path,
                "label": label, "speed": sent_speed,
                "voice_id": sent_voice_id, "voice_style": sent_voice_style}

        # Resume: 只有当内容指纹、时长、语速状态都能证明这段音频就是当前
        # 稿件时才跳过（判定全在 resolve_resume_state，这里只执行副作用）
        if args.resume and os.path.exists(out_path):
            facts = _observe_resume_state(out_path, ffmpeg_path, sent_tts,
                                          sent_voice_id, sent_voice_style,
                                          model, base_url)
            decision = resolve_resume_state(facts, sent_speed)
            if decision.action == "regen":
                # 失效成因由 decision.reason 报（regen 的六种成因与为什么不报
                # 只能靠猜，见 ResumeDecision.reason）。同时无条件清掉整份
                # sidecar，别把 .failed / 旧 .orig.wav 带进这次重合成。
                print(f"  [{label}] 缓存失效（{decision.reason}），重新合成",
                      flush=True)
                _drop_stale_cache(out_path)
                pending_tasks.append(task)
                continue
            restored = False
            # restore 成功前 decision.prev_speed 不可信：marker 不可读那条路上
            # 它是 resolve_resume_state 的推定值（applied = 1.0），不是实测。
            # 带着它进 apply_speed 会触发 refresh_backup——把当前这条语速未知的
            # WAV 盖回 .orig.wav，真原速备份就此销毁，之后每次换速都在错误基线
            # 上叠变速（1.5x 被当原速备份 → 再 atempo 1.5 = 2.25x）。还原成功
            # 后文件已回到原速，推定值才变成事实，届时才准传。
            reapply_prev = decision.prev_speed
            if decision.restore_first:
                try:
                    _restore_ok = apply_speed(ffmpeg_path, out_path, 1.0)
                except Exception as e:
                    # 与主合成路径同一口径（见 synthesize_one 的 speed-skip）：
                    # ffmpeg 缺失/写盘失败这类 OSError 不能让整轮 resume 裸栈
                    # 中断，降级成"还原失败"，下面按无 marker 的自愈路径走。
                    _restore_ok = False
                    print(f"  [{label}][warn] .orig.wav 还原原速异常"
                          f"（{type(e).__name__}: {e}）", file=sys.stderr)
                if _restore_ok:
                    restored = True
                    print(f"  [{label}] 无可用 .spd marker，已从 "
                          f".orig.wav 还原到原速，重新对齐语速", flush=True)
                else:
                    # 不覆盖备份（reapply_prev=None），apply_speed 改为直接拿
                    # .orig.wav 当变速源——目标语速仍从真原速施加，只是不再
                    # 走"先还原再变速"这一步。use_cached 时没有后续对齐，
                    # 音频保持现状（该分支不写 marker，见下方 if restored）。
                    reapply_prev = None
                    _tail = ("后续语速对齐改从 .orig.wav 直接施加"
                             if decision.action == "reapply"
                             else "音频保持现状")
                    print(f"  [{label}][warn] .orig.wav 还原原速失败；{_tail}",
                          file=sys.stderr)
            _replayed = restored
            if decision.action == "reapply":
                try:
                    _reapplied = apply_speed(ffmpeg_path, out_path,
                                             decision.apply_speed_to,
                                             prev_speed=reapply_prev)
                except Exception as e:
                    # 与主合成路径同一口径：异常也降级成"保持当前语速"，
                    # 不让整轮 resume 裸栈中断。
                    _reapplied = False
                    print(f"  [{label}][warn] atempo 重施加异常"
                          f"（{type(e).__name__}: {e}）；audio kept at "
                          f"previous speed", file=sys.stderr)
                if _reapplied:
                    # 标签必须跟"这一句本次到底动没动过音频文件"对齐（见下方 _tag）。
                    # apply_speed 有三种落点，只有最后一种没碰盘：
                    #   · 请求语速 ≠ 1 ⇒ 起 ffmpeg，tmp 经 os.replace 覆盖；
                    #   · 请求语速 == 1 且盘上有 .orig.wav ⇒ copy2 把原速盖回来
                    #     ——**不是空操作**：ffmpeg 没起，但文件整份被改写。这正是
                    #     "上一版 1.5x、这次跑 1.0x"的常态，报成纯复用会把排查
                    #     语速问题的人引向"这句没动过"；
                    #   · 请求语速 == 1、无备份、且 .spd 已声明盘上就是原速 ⇒ 真没动。
                    _moved = (needs_speed_change(decision.apply_speed_to)
                              or facts.orig_wav_exists
                              or (reapply_prev is not None
                                  and needs_speed_change(reapply_prev)))
                    _replayed = _replayed or _moved
                    if decision.write_spd is not None:
                        if not _write_spd(out_path, decision.write_spd, label,
                                          "下次 --resume 会重新对齐语速"):
                            # 补写失败却留着旧 marker = 盘上一份谎话：文件已在
                            # 新语速而 marker 说着旧值，下次 --resume 请求旧值
                            # 会直接判 use_cached，错语速静默上线。删掉它，落到
                            # "无 marker" 状态，让下次 resume 走还原+重施加的
                            # 自愈路径（resolve_resume_state 的 applied=None
                            # 分支：有 .orig.wav 就先还原再对齐）。
                            remove_if_exists(out_path + ".spd")
                    elif os.path.exists(out_path + ".spd"):
                        remove_if_exists(out_path + ".spd")
                else:
                    print(f"  [{label}][warn] atempo re-apply failed; "
                          f"audio kept at previous speed", file=sys.stderr)
                dur = measure_duration(ffmpeg_path, out_path)
                if not dur:
                    # 量不出长度 = 这段音频不能用（atempo 产物替换失败/写坏）。
                    # round(0.0, 3) 写进 manifest 会被契约层以"duration 必须是正
                    # 有限数值"整份拒收，而这一句已经跳过了合成流程——宁可排进本次
                    # 重合成队列（并清掉整份 sidecar 状态），也不留一份必坏的产物。
                    print(f"  [{label}][warn] 语速对齐后时长测量失败，"
                          f"改为重新合成这一句", file=sys.stderr)
                    _drop_stale_cache(out_path)
                    pending_tasks.append(task)
                    continue
            else:
                dur = facts.audio_duration
                if restored:
                    # 还原原速改写了 WAV，_observe 时量的旧时长已失效，必须
                    # 重测：沿用变速前的旧时长会让这句字幕时间轴整体错位。
                    # 重测失败 = 还原产物不能用，与 reapply 分支同一口径，
                    # 转入本次重合成队列，不进 cached。
                    _restored_dur = measure_duration(ffmpeg_path, out_path)
                    if not _restored_dur:
                        print(f"  [{label}][warn] 还原原速后时长测量失败，"
                              f"改为重新合成这一句", file=sys.stderr)
                        _drop_stale_cache(out_path)
                        pending_tasks.append(task)
                        continue
                    dur = _restored_dur
                    # 还原成功 = 音频已回到原速：用可读的 "1.0" marker 替换
                    # 那份坏的。只删不写会落到"无 marker 但 .orig.wav 还在"
                    # 的状态，resolve_resume_state 每次 --resume 都会再走一遍
                    # 还原+重测（幂等但白烧 ffmpeg）；显式 1.0 才能直接证明
                    # 缓存有效。
                    if not _write_spd(out_path, "1.0", label,
                                      "下次 --resume 会重复一次还原"):
                        remove_if_exists(out_path + ".spd")
            # 缺 .sha 已在 resolve_resume_state 判为 regen：不给归属不明的
            # 旧音频盖上当前文本的指纹（补写一次就把错位永久固化）。
            sd = _sentence_entry(i, sentences[i], out_path, dur,
                                  ffmpeg_path)
            if i in sentence_speaker_labels:
                sd["speaker"] = sentence_speaker_labels[i]
            if decision.synth_failed:
                sd["synth_failed"] = True
            sentence_data.append(sd)
            # 缓存命中的两种形态分开报：纯复用一句 ffmpeg 都没跑；而本次实际
            # 重放了音频（还原原速 / 重新施加变速）的一律打 (cached) 会掩盖
            # "这次其实重新处理过音频"，排查语速问题时误导。cached_count 口径
            # 保持"没花 TTS 额度"。
            # _replayed 只认"音频文件真的被改写"：restore 成功（.orig.wav 盖回
            # WAV）或 reapply 真的施加了 atempo。restore_first 本身不算——还原
            # 失败时文件原封不动；reapply 的 apply_speed_to=1.0 也不算，那是
            # 还原/空操作。双失败（还原失败 + atempo 失败）时音频保持现状，
            # 标签必须是 skip,cached 而不是 skip,respeed。
            _tag = "skip,respeed" if _replayed else "skip,cached"
            print(f"  [{label}][{_tag}] {dur:.2f}s", flush=True)
            cached_count += 1
            continue

        pending_tasks.append(task)

    # 进度预估按 pending_tasks 里的真实文本算平均字数：中文句长常 15-40 字，
    # 用固定的句均字数会偏短 2-3 倍。与静音兜底/estimate 同一套
    # DEFAULT_CHARS_PER_SEC 假设；+20% 重试余量。
    pending_count = len(pending_tasks)
    if pending_count:
        _mean_chars = sum(len(t["text_tts"]) for t in pending_tasks) / pending_count
    else:
        _mean_chars = 13.0  # 0 pending 时退回旧默认（也用于打日志可读性）
    est_per_sentence = round(_mean_chars / DEFAULT_CHARS_PER_SEC, 1)
    est_total = pending_count * est_per_sentence * 1.2 / args.workers
    print(f"[est] {pending_count} sentences to synthesize, "
          f"~{est_total:.0f}s with up to {args.workers} workers "
          f"(mean {_mean_chars:.1f} chars/sentence)", flush=True)

    # 并行 TTS 合成：用线程池并行调用 synth_sentence
    new_results = []
    if pending_tasks:
        # 不用 with：__exit__ 走的是 shutdown(wait=True)，Ctrl-C 之后会把已提交的
        # future 全部跑完才返回——200 句稿按了 Ctrl-C，终端像是卡住，而后台仍在
        # 一句句计费，用户以为退出了。显式持有 executor，在 finally 里
        # cancel_futures=True 撤掉还没开始的任务（cancel_futures 需要 3.9+，
        # 与 SKILL.md 声明的下限一致），正在跑的那几句放弃等待、随中断一起退出。
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
        try:
            futures = {executor.submit(_tts_worker, t, client, ffmpeg_path,
                                       model, args.api_timeout): t
                       for t in pending_tasks}
            done_count = 0
            for future in concurrent.futures.as_completed(futures):
                # worker 里的异常（apply_speed 的 subprocess 抛错、磁盘写失败…）
                # 默认会在 future.result() 重新抛出，整条管线中断：已付额度
                # 合成的其它句子连同 manifest 一起丢掉。这里把单次失败收敛回
                # "这一句失败"，交给下面的 on-fail 分支处理。
                try:
                    task, ok, speed_applied = future.result()
                except Exception as e:
                    task, ok, speed_applied = futures[future], False, False
                    print(f"    [{task['label']}][worker] 该句线程抛出未捕获异常："
                          f"{type(e).__name__}: {e}（按合成失败处理）",
                          file=sys.stderr, flush=True)
                done_count += 1
                idx = task["index"]
                sent_orig = sentences[idx]
                out_path = task["out_path"]
                label = task["label"]
                preview = _log_label(sent_orig)

                if ok and os.path.exists(out_path):
                    dur = measure_duration(ffmpeg_path, out_path)
                    if dur > 0:
                        # 落 sidecar：内容指纹 .sha（resume 校验用）+
                        # 已施加语速 .spd（下次 resume 不必重放 atempo）。
                        # 用带兜底的写入：磁盘满等 OSError 不能把这句已合成的
                        # 音频变成"丢句"。
                        _record_sentence_cache(
                            out_path, task["text_tts"], task["speed"],
                            speed_applied=speed_applied,
                            voice_id=task["voice_id"],
                            voice_style=task["voice_style"], model=model,
                            base_url=base_url)
                        sd = _sentence_entry(idx, sent_orig, out_path, dur,
                                              ffmpeg_path)
                        if idx in sentence_speaker_labels:
                            sd["speaker"] = sentence_speaker_labels[idx]
                        new_results.append(sd)
                        # 这次是真的合成成功了——如果这句之前被 --on-fail silence
                        # 兜底过、留了个 .failed marker（比如用户手动删了旧的静音
                        # mp3 之后重跑、这次没再失败），清掉 marker，不然下次
                        # resume 会把这个已经补录好的句子又标成 synth_failed。
                        # remove_if_exists 内部已判过存在性，外面不必再问一次
                        # ——两次判断之间还有个 TOCTOU 窗口。
                        remove_if_exists(out_path + _FAILED_SUFFIX)
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} -> {dur:.2f}s", flush=True)
                        continue

                if args.on_fail == "silence":
                    # 降级：该句反复失败（如触发内容审核）时不再直接丢弃，改为
                    # 生成一段静音占位，时长用 estimate_sentence_seconds 同款启发式
                    # （字数/语速）估算——没有真实语速可测，只能估，与
                    # DEFAULT_CHARS_PER_SEC 假设保持一致，除上该句实际 speed
                    # （estimate_sentence_seconds 的 speed 是除数，见 _timeline）。
                    fallback_dur = max(
                        estimate_sentence_seconds(sent_orig, DEFAULT_CHARS_PER_SEC, task["speed"]),
                        0.3,
                    )
                    try:
                        # 先清残留 sidecar 再落静音：本分支在 API 循环之外，
                        # synth_sentence 失败时没走到它的 _clear_stale_sidecars。
                        # 留着上一稿的 .orig.wav 时，本句 speed=1.0 又不写 .spd，
                        # 下次 --resume 会走 restore_first 用 apply_speed(1.0) 把
                        # 那份旧音频原样盖回静音位——旧稿复活、还带 .failed 标记。
                        #
                        # 清理失败就不兜底：落一份静音等于假装"这句已处理"，
                        # 而残留的 .orig.wav 会在用户之后改 --speed 时被 _audio
                        # 当原速源做 atempo，新字幕配上一稿的旧配音——比直接丢
                        # 这句更难查。宁可让这句走下面的 failed 分支被如实报出。
                        if not _clear_stale_sidecars(out_path):
                            print(f"    [{label}][warn] 上一轮 sidecar 清理失败"
                                  f"（{out_path}.orig.wav 残留），跳过静音兜底："
                                  f"落静音会让下次 --resume 把上一稿音频当原速源"
                                  f"做变速，新字幕配旧配音。删掉该句的 .orig.wav "
                                  f"后重跑即可恢复", file=sys.stderr)
                            failed.append(idx)
                            print(f"[TTS {done_count}/{pending_count}] {label} "
                                  f"{preview} [FAILED]", flush=True)
                            continue
                        generate_silence(ffmpeg_path, fallback_dur, out_path)
                        # 落一个 sidecar marker（跟已有的 .spd 速度 marker 同一套
                        # 模式），不然下次 --resume 时这句会走"文件已存在=缓存"
                        # 分支重建 sentence_data，那个分支不知道这个文件其实是
                        # 静音兜底——synth_failed 标记会悄悄从 manifest 里消失，
                        # 事后再也看不出这句需要人工补录。
                        #
                        # marker 写不成功时**不落 .sha/.spd**：静音占位与真实
                        # 配音在缓存里只差这一个 marker，缺了它下次 --resume 会
                        # 按"合成成功"复用这段静音，degraded 状态连带
                        # silence_fallback_count 一起从 manifest 里消失，
                        # run.py 的 degraded 闸门（exit 3）就此静默失效。不写
                        # 指纹只是让下次多试一次合成，代价远小于放走一段
                        # 假装正常的静音洞。
                        _marker_ok = True
                        try:
                            with open(out_path + _FAILED_SUFFIX, "w",
                                       encoding="utf-8"):
                                pass
                        except OSError as _e:
                            _marker_ok = False
                            print(f"    [{label}][warn] .failed marker 写入"
                                  f"失败（{_e}）：本次仍按静音兜底走完整时间轴，"
                                  f"但不写内容指纹——下次 --resume 会重新尝试"
                                  f"合成该句（缺 marker 的静音洞会被 run.py 的"
                                  f"degraded 闸门当成正常成片放行，宁可贵一次"
                                  f"也不放它过去）", file=sys.stderr)
                        if _marker_ok:
                            # 静音时长估算已除过 speed（estimate_sentence_seconds），
                            # 必须写 .spd marker 声明"已施加语速"——否则下次 --resume
                            # 会把这段静音再 atempo 一遍，占位时长被压缩到 2/3。
                            # .sha 内容指纹也一并落盘，跟正常合成句子的缓存语义一致。
                            # 用带兜底的写入：sidecar OSError 不该冒到下面的外层
                            # except——静音 WAV 已经在盘上，丢这句等于兜底失败。
                            _record_sentence_cache(
                                out_path, task["text_tts"], task["speed"],
                                voice_id=task["voice_id"],
                                voice_style=task["voice_style"], model=model,
                                base_url=base_url)
                        sd = {
                            "index": idx, "text": sent_orig,
                            "file": out_path, "duration": round(fallback_dur, 3),
                            "synth_failed": True,
                        }
                        # 静音兜底句不量发声区间：它本来就没有"发声"，写了
                        # 只会让字幕按一个不存在的说话区间推进。
                        if idx in sentence_speaker_labels:
                            sd["speaker"] = sentence_speaker_labels[idx]
                        new_results.append(sd)
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} [FAILED -> silence fallback "
                              f"~{fallback_dur:.2f}s, 建议事后人工补录]", flush=True)
                        continue
                    except Exception as e:
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} [FAILED，静音兜底也失败: {e}]", flush=True)
                        print(f"[warn] 句 {label}：--on-fail silence 承诺的"
                              f"“保留在时间轴与字幕位置”无法兑现，该句将从"
                              f"成片与字幕中完全丢失", file=sys.stderr)

                failed.append(idx)
                print(f"[TTS {done_count}/{pending_count}] {label} "
                      f"{preview} [FAILED]", flush=True)
        finally:
            # Ctrl-C / 任何异常离开上面的循环时，先撤掉排队中的 future，
            # 再不等它们跑完（wait=False）。否则已提交的句子会在用户以为
            # 已退出之后继续合成并计费。cancel_futures 在 3.9+ 可用
            #（与本技能声明的 Python 下限一致）。
            executor.shutdown(wait=False, cancel_futures=True)

    # 合并结果并按原始 index 排序，保证 sentence_data 顺序正确
    sentence_data.extend(new_results)
    sentence_data.sort(key=lambda s: s["index"])

    if not sentence_data:
        print("[error] All sentences failed", file=sys.stderr)
        sys.exit(1)
    if failed and args.on_fail == "abort":
        labels = [f + 1 for f in failed]
        print(
            f"\n[error] {len(failed)} TTS sentence(s) failed: {labels}. "
            "默认 --on-fail abort 会阻断管线，避免静默丢失内容。"
            "如需保留时间轴并显式进入 degraded 状态，请改用 --on-fail silence。",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    if failed:
        print(f"\n[warn] {len(failed)} sentence(s) lost: TTS failed and the "
              f"silence fallback also failed, these segments have no audio: "
              f"{[f+1 for f in failed]}",
              flush=True)
    silence_fallback_count = sum(1 for s in sentence_data if s.get("synth_failed"))
    if silence_fallback_count:
        print(f"\n[warn] {silence_fallback_count} 句无有效配音（TTS 失败后的"
              f"静音占位，含历史 run 缓存复用），成片对应位置为静音，建议核对 "
              f"timing_manifest.json 中 \"synth_failed\": true 的句子并考虑补录",
              flush=True)

    _finalize_audio_and_manifest(
        args, ffmpeg_path, sentence_data, source_data, seg_config,
        silence_fallback_count, len(sentences), cached_count,
        # 本次真正开口配音的音色集合（分段/对话稿会覆盖 --voice-id）。
        # 按 sentence_data 实产的 index 取，不是 range(len(sentences))：
        # 彻底丢失的句子压根没配音，把它的音色写进 manifest.voice_id 等于
        # 声明一件没发生的事（静音兜底句仍算数——它占住了时间轴上的位置）。
        voices_used={sentence_voices.get(s["index"], (args.voice_id, None))[0]
                     for s in sentence_data})


if __name__ == "__main__":
    main()
