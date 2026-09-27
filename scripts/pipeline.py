#!/usr/bin/env python3
"""content-to-video TTS Pipeline：结构化写稿 → 逐句 TTS → 拼接音频 +
timing_manifest.json（字幕时间轴的唯一来源）。

参数与行为详见 SKILL.md 第 3 步与 references/tts_pipeline.md；
完整参数列表见 ``--help``。
"""
import argparse
import base64
import concurrent.futures
import hashlib
import math
import os
import random
import sys
import time


sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用户级 .env 路径
_USER_ENV_PATH = os.path.join(os.path.expanduser("~"), ".config", "ai-video", ".env")
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"

def _parse_env_file(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict。

    编码策略（先读字节再判别，避免"解码成功但内容是 mojibake"的误判链）：
    ① UTF-16 BOM（PowerShell 5.1 重定向 `>` 的产物）→ 按 utf-16 解；
    ② 严格 utf-8（含 BOM 的 utf-8-sig 一并覆盖）；
    ③ utf-8 解出 NUL 字节 → 多半是无 BOM 的 UTF-16（ASCII 文本在
       UTF-16 下每两字节一个 0x00，而 0x00 本身是合法 UTF-8，所以
       "utf-8 解码成功"不代表内容正确）——改试 utf-16-le/be；
    ④ utf-8 彻底失败 → gb18030（记事本 ANSI/GBK 保存的中文注释）。
    UnicodeDecodeError 是 ValueError 子类，不在这里接住就会从
    load_env()/get_key() 裸栈穿透；且报错必须指向"编码问题"——爆炸点
    若在 get_key() 内部，连 --dry-run 都会瘫痪，提示信息却是
    "没有 API key"，完全无法定位。
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
    text = None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            text = None
    if text is None:
        try:
            candidate = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            candidate = None
        if candidate is not None and "\x00" not in candidate:
            text = candidate
        elif candidate is not None:
            # utf-8 解码"成功"却满纸 NUL：无 BOM UTF-16 的典型形态
            for enc in ("utf-16-le", "utf-16-be"):
                try:
                    alt = raw.decode(enc)
                except UnicodeDecodeError:
                    continue
                if "\x00" not in alt:
                    text = alt
                    break
            if text is None:
                print(f"[warn] {path} 内容含 NUL 字节——多半是无 BOM 的 "
                      f"UTF-16 编码（PowerShell 5.1 重定向产物）。"
                      f"请用 UTF-8 重新保存该文件", file=sys.stderr)
                return result
        else:
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                print(f"[warn] 无法读取 {path}: 不是可识别的文本编码"
                      f"(尝试过 utf-8 / utf-16 / gb18030)。"
                      f"请用 UTF-8 重新保存该文件（记事本另存为右下角选 UTF-8）",
                      file=sys.stderr)
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
            if k.startswith("export "):
                k = k[len("export "):].strip()
            v = v.strip()
            # 先剥未加引号值的行内注释（KEY=value # 说明），只认"空格 + #"，
            # 紧贴的 #（色值、口令片段）不动；引号值取成对引号之间的内容，
            # 闭引号之后的注释自然丢弃。顺序不能反：`KEY="x" # 注释` 若先判
            # 成对引号会失败，剥完注释又把带引号的 "x" 交给 SDK → 401
            if v[:1] in ("\"", "'"):
                close = v.find(v[0], 1)
                if close != -1:
                    v = v[1:close]
            elif " #" in v:
                v = v.split(" #", 1)[0].rstrip()
            if k:
                result[k] = v
    return result


def load_env():
    """
    按优先级合并两级 .env 来源，返回合并后的 dict。
    高优先级的值覆盖低优先级：
      ~/.config/ai-video/.env < os.environ
    """
    merged = {}
    user_env = _parse_env_file(_USER_ENV_PATH)
    merged.update(user_env)

    # 最高优先：系统环境变量（只取非空值）
    for k, v in os.environ.items():
        if v:
            merged[k] = v

    return merged


def get_key(name, cli_value=None):
    """
    获取单个密钥值。

    优先级：cli_value > os.environ > ~/.config/ai-video/.env > None
    查找链复用 load_env（同一份优先级只实现一遍）。

    参数：
        name: 环境变量名（如 MIMO_API_KEY）
        cli_value: 命令行参数传入的值（最高优先）

    返回：
        密钥字符串，或 None（未找到）
    """
    if cli_value:
        return cli_value
    return load_env().get(name) or None


def resolve_model_config(cli_model, cli_base_url, model_env_name, default_model):
    """按 CLI > 环境变量/配置文件 > 默认值 解析模型名与 API base URL。

    与密钥一样走"环境变量 > ~/.config/ai-video/.env"两级查找：
      - model: cli_model > env[model_env_name] > default_model
      - base_url: cli_base_url > env[MIMO_BASE_URL] > DEFAULT_BASE_URL

    pipeline.py（TTS 模型）使用此函数，避免各脚本重复实现同一套解析逻辑。
    """
    env = load_env()
    model = cli_model or env.get(model_env_name) or default_model
    base_url = cli_base_url or env.get("MIMO_BASE_URL") or DEFAULT_BASE_URL
    return model, base_url

from _theme import get_default_accent  # noqa: E402
from _contracts import list_voice_ids  # noqa: E402
# 核心管线不反向依赖任何可选脚本：默认倍速与时长估算一律从 _contracts 取
from _contracts import (DEFAULT_SPEED, DEFAULT_GAP, load_segments_source,  # noqa: E402
                        DEFAULT_CHARS_PER_SEC, estimate_sentence_seconds,
                        is_content_sid, validate_speed, validate_timing_manifest,
                        needs_speed_change, speed_marker_value)
from _audio import get_ffmpeg, ffmpeg_usable  # noqa: E402
from _audio import (measure_duration, generate_silence, wav_data_consistent,  # noqa: E402
                    apply_speed, concat_audio, mix_bgm, apply_loudnorm,
                    _remove_quiet)  # 尽力删文件，不抛
from build_from_structured import build_parts  # noqa: E402
from _script_utils import (setup_stdio, guard_not_in_skill_dir,  # noqa: E402  重定向场景 UTF-8 + 产物路径守卫
                          write_json_atomic)

DEFAULT_ACCENT = get_default_accent()


# ===================================================================
# MiMo TTS 单句合成（原 _tts.py：仅本文件使用，归位回管线本体）
# ===================================================================

class BadAudioResponseError(Exception):
    """TTS 响应里没有音频（chat.completions 返回了纯文本）。

    几乎总是 --base-url/--model 指向了不支持 audio 参数的网关或模型，
    重试 N 次结果完全一样。按确定性失败处理：首次命中即整句放弃、不进
    重试循环（_is_non_retryable）——否则 100 句 × 每句 3 次重试 = 300 次
    billable 调用全部白烧。全句皆败时管线以 "All sentences failed" 退出；
    每句至多一次的探测调用是该端点错配下无法再避免的最小开销。
    """


def _is_non_retryable(exc):
    """判断异常是否属于"重试也不会好"的确定性失败。

    openai SDK 的 APIStatusError 及其子类都带 status_code 属性：
    400（参数/内容审核拒绝）、401/403（密钥错误/无权限）、404（模型不存在）、
    422（请求不合法）这类错误重试 N 次结果完全一样——每句烧满 3 次重试
    只会浪费额度和时间（100 句 × 无效 key = 300 次无效调用 + 每句多等 6s），
    直接放弃。响应不含音频（BadAudioResponseError）同理：端点/模型配错了，
    换一句再试也是同样的纯文本响应。连接/超时/429 限流类不带 status_code
    或带可重试码，仍走重试。
    """
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (400, 401, 403, 404, 422)
    return False


# 每句音频 (sNNN.wav) 的伴生文件后缀集合：.orig.wav 是变速前的原速备份，
# .spd/.spd.tmp.wav 是语速状态，.failed 是"本句放弃"标记。凡动一句的音频，
# 这些后缀都要跟着动（见 _clear_stale_sidecars / _drop_stale_cache）。
_SIDECAR_SUFFIXES = (".orig.wav", ".spd", ".spd.tmp.wav", ".failed")


def _clear_stale_sidecars(out_path):
    """新合成前清掉上一轮残留的变速/兜底 sidecar。

    这些文件属于上一次运行的稿件/参数：.orig.wav 是旧音频的原速备份，
    .spd/.spd.tmp.wav/.failed 是旧状态标记。新音频落盘后若不清理，
    apply_speed 会把旧 .orig.wav 当作原速源做变速，新音频被整体丢弃
    （改稿后不带 --resume 重跑即触发，成片念旧稿配新字幕）。

    返回是否全部清理干净（或本来就没有残留）。删除失败（Windows 下
    杀毒扫描/播放器占用文件）时先尝试改名隔离（加 .stale 后缀，隔离后
    不会再被任何流程按原名读到）；改名也失败才返回 False——调用方必须
    据此跳过变速，否则残留的旧 .orig.wav 会顶掉新合成的音频。
    """
    ok = True
    for suffix in _SIDECAR_SUFFIXES:
        p = out_path + suffix
        if not os.path.exists(p):
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


def synth_sentence(client, text, voice_id, voice_style, out_path,
                   ffmpeg_path, speed, model, api_timeout,
                   max_retries=3, sentence_label=""):
    """Call MiMo TTS API for a single sentence. Returns (ok, speed_applied).

    ok：TTS 音频是否成功落盘；speed_applied：atempo 变速是否落上
    （ok=True 而 speed_applied=False 时音频有效但仍是原速——调用方据此
    在 .spd 里写 1.0 这一事实，而非请求语速；写请求语速才会把原速音频
    永久钉成"已在目标速率"，见 _write_sentence_sidecars）。

    语速在合成后用 ffmpeg atempo 精确变速，与 TTS 模型的自然语速无关。
    """
    messages = []
    if voice_style:
        messages.append({"role": "user", "content": voice_style})
    messages.append({"role": "assistant", "content": text})

    audio_params = {"format": "wav"}
    if voice_id:
        audio_params["voice"] = voice_id

    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                audio=audio_params,
                timeout=api_timeout,
            )
            # 显式检查响应结构而不是直接下钻 .data：端点/模型配错时
            # message.audio 为 None，AttributeError 不带 status_code 会被
            # 归为可重试——每句白烧满 3 次 billable 调用才放弃。这里转成
            # 确定性失败（见 BadAudioResponseError），首次即整句放弃。
            audio_obj = getattr(completion.choices[0].message, "audio", None)
            audio_data = getattr(audio_obj, "data", None) if audio_obj else None
            if not audio_data:
                raise BadAudioResponseError(
                    "TTS 响应不含音频（chat.completions 返回了纯文本）——"
                    "检查 --model/--base-url 是否指向支持 audio 参数的"
                    " TTS 模型（默认 mimo-v2.5-tts），不要指向普通对话模型")
            audio_bytes = base64.b64decode(audio_data)
        except Exception as e:
            label = sentence_label or (text[:30] + "...")
            if _is_non_retryable(e):
                print(f"    [{label}][fatal] {e}（确定性失败，不重试）",
                      flush=True)
                # 失败即清掉 out_path：磁盘写满等异常可能留下半截 WAV，
                # 其 wave 头完整、下次 --resume 会把它当有效缓存跳过。
                _remove_quiet(out_path)
                return False, False
            print(f"    [{label}][retry {attempt+1}/{max_retries}] {e}",
                  flush=True)
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
            label = sentence_label or (text[:30] + "...")
            print(f"    [{label}][fatal] 音频写盘失败（{e}），不重试",
                  file=sys.stderr, flush=True)
            _remove_quiet(out_path)
            return False, False
        break  # API 成功、音频已落盘，跳出重试循环
    else:
        _remove_quiet(out_path)  # 理由同 fatal 分支：不留半截 WAV 给下次 resume
        return False, False

    # 新合成 = 全新内容：先清上一轮残留 sidecar（.sha 由调用方在 synth 成功后
    # 重写，不在此处动，理由见 _clear_stale_sidecars docstring）。清理失败时不能
    # 继续 apply_speed：残留的旧 .orig.wav 会被当作原速源做变速，新音频被整体
    # 丢弃。跳过变速只损失语速且可恢复——speed_applied=False 时调用方写
    # .spd=1.0 声明"当前确在原速"，下次 --resume 在缓存音频上重放 atempo
    # （见 _write_sentence_sidecars）。
    if not _clear_stale_sidecars(out_path):
        print(f"    [{sentence_label or text[:30] + '...'}][speed-skip] "
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
            label = sentence_label or (text[:30] + "...")
            print(f"    [{label}][speed-skip] atempo 变速失败，保留原始语速: {e}",
                  flush=True)
    return True, speed_applied


# ===================================================================
# Main pipeline
# ===================================================================


def _sentence_hash(text, voice_id=None, voice_style=None, model=None):
    """句子 TTS 输入（文本+音色+风格+模型）的内容指纹（resume 缓存键）。

    缓存 wav 文件名只有句序号（s005.wav），不含内容——改了第 5 句文案后
    带 --resume 重跑会复用旧音频，而 manifest 的 text 用新稿，配音与
    字幕从此错位。合成成功时把该句输入的 sha1 写进 s005.wav.sha sidecar，
    resume 时比对：不一致视为缓存失效，删掉重合成。
    指纹除文本外还覆盖 voice_id/voice_style/model——只含文本时
    换音色后带 --resume 重跑会静默复用旧音色音频（manifest 声明的
    是新音色、实际音频是旧音色，且无任何警告）。旧缓存（纯文本指纹）
    会统一失配重合成（MiMo TTS 不计费，只费时间不烧额度）。
    """
    payload = "\x1f".join([text, voice_id or "", voice_style or "", model or ""])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()

def _write_sentence_sidecars(out_path, text, speed, speed_applied=True,
                             voice_id=None, voice_style=None, model=None):
    """合成/兜底成功后统一落 sidecar：内容指纹 .sha + 已施加语速 .spd。

    .spd 记录的是"当前 WAV 确实处于哪个语速"（事实），不是"用户想要哪个语速"。
    speed_applied=False 表示本次 atempo 没落上（音频确凿仍在原速）——此时写
    显式 "1.0"：若只删 marker，下次 --resume 会落到"无 .spd 也无可恢复原速
    备份"的状态，resolve_resume_state 无法证明语速只能 regen，白白重烧一次
    TTS 额度；显式 1.0 会让 resume 判 needs_reapply，直接在缓存 WAV 上重放
    atempo。绝不能在此写请求语速（那才会把原速音频永久钉成"已在目标速率"）。
    """
    with open(out_path + ".sha", "w", encoding="utf-8") as f:
        f.write(_sentence_hash(text, voice_id, voice_style, model))
    if needs_speed_change(speed):
        if speed_applied:
            with open(out_path + ".spd", "w", encoding="utf-8") as f:
                f.write(str(speed_marker_value(speed)))
        else:
            with open(out_path + ".spd", "w", encoding="utf-8") as f:
                f.write("1.0")
    else:
        _remove_quiet(out_path + ".spd")

def _record_sentence_cache(out_path, text, speed, speed_applied=True,
                           voice_id=None, voice_style=None, model=None):
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
                                 model=model)
        return True
    except OSError as e:
        print(f"    [sidecar][warn] {os.path.basename(out_path)} 的 .sha/.spd "
              f"写入失败（{e}）：音频可用，但下次 --resume 会重合成这一句",
              file=sys.stderr, flush=True)
        return False

def _drop_stale_cache(out_path):
    """缓存判为失效（resolve_resume_state 返回 regen）时清掉旧音频及其全部
    sidecar，避免带着旧状态续跑。

    尽力而为：清不干净（Windows 下文件被杀毒扫描/播放器占用）也不阻断——
    调用方紧接着无条件把该句排进重合成队列，合成侧以覆盖写落盘，
    失效缓存不会被当成"文件存在=可用"。
    """
    # "" = wav 本体；.sha 只在这里清（新合成路径上 .sha 由合成成功后重写，
    # 不需要提前隔离，见 _clear_stale_sidecars 的调用注释）。
    for suffix in ("", ".sha") + _SIDECAR_SUFFIXES:
        p = out_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass




# ===================================================================
# Resume 决策（纯函数，不含文件 IO / ffmpeg 调用，可单测；
# 副作用留在 main 执行层）
# ===================================================================

class ResumeDecision:
    __slots__ = ("action", "apply_speed_to", "prev_speed", "write_spd",
                 "restore_first", "synth_failed", "reason")

    def __init__(self, action, apply_speed_to=None, prev_speed=None,
                 write_spd=None, restore_first=False, synth_failed=False,
                 reason=None):
        self.action = action
        self.apply_speed_to = apply_speed_to
        self.prev_speed = prev_speed
        self.write_spd = write_spd
        self.restore_first = restore_first
        self.synth_failed = synth_failed
        # 为什么判定缓存失效。regen 有六种成因（无指纹 / 指纹变了 / 文件没了
        # / 量不出时长 / 文件被截断 / 语速状态无法证明），对写稿人来说是完全
        # 不同的三件事（改稿？坏文件？换参数？）。不报出来就只能靠猜。
        self.reason = reason


class ResumeFacts:
    __slots__ = ("sha_exists", "sha_matches", "audio_exists", "audio_duration_ok",
                 "audio_duration", "audio_intact", "spd_exists", "spd_readable",
                 "spd_applied", "orig_wav_exists", "failed_marker_exists")

    def __init__(self, sha_exists=False, sha_matches=False, audio_exists=False,
                 audio_duration_ok=False, audio_duration=0.0, audio_intact=True,
                 spd_exists=False, spd_readable=False, spd_applied=None,
                 orig_wav_exists=False, failed_marker_exists=False):
        self.sha_exists = sha_exists
        self.sha_matches = sha_matches
        self.audio_exists = audio_exists
        self.audio_duration_ok = audio_duration_ok
        self.audio_duration = audio_duration
        self.audio_intact = audio_intact
        self.spd_exists = spd_exists
        self.spd_readable = spd_readable
        self.spd_applied = spd_applied
        self.orig_wav_exists = orig_wav_exists
        self.failed_marker_exists = failed_marker_exists


def resolve_resume_state(facts, requested_speed):
    # 内容指纹缺失 = 无法证明这段音频就是当前文本。旧写法是"只在 .sha 存在
    # 且不一致时才失效"，于是删掉 .sha（或上一次写入失败）后，改稿重跑会走
    # cached 分支、再顺手给旧音频补写一个新文本的 .sha（self-certify）——
    # 错位音频从此永久固化，且没有任何一处再报出来。缺证明就当没缓存。
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
            # 没有可读 marker、也没有原速备份时，无法证明当前 WAV
            # 已经处于 requested_speed。复用会让旧版本缓存静默带错语速；
            # 宁可重新合成，也不把错误音频配上“看似正确”的时间轴。
            return ResumeDecision("regen",
                                  reason="语速 marker 不可读且无原速备份，无法证明当前 WAV 的语速")
    if applied is None and facts.orig_wav_exists:
        # 旧版/人工清理可能只留下 .orig.wav 而没有 .spd marker。原速备份
        # 是唯一可证明的基线，先恢复它再按本次请求重新施加语速。
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

def _observe_resume_state(out_path, ffmpeg_path, text, voice_id, voice_style, model):
    sha_path, spd_path = out_path + ".sha", out_path + ".spd"
    sha_exists = os.path.exists(sha_path)
    sha_matches = False
    if sha_exists:
        try:
            with open(sha_path, encoding="utf-8") as f:
                cached = f.read().strip()
        except (OSError, UnicodeDecodeError):
            cached = ""
        sha_matches = (cached == _sentence_hash(text, voice_id, voice_style, model))
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
        failed_marker_exists=os.path.exists(out_path + ".failed"),
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
                        help="MiMo TTS API key (default: reads MIMO_API_KEY from .env)")
    parser.add_argument("--voice-id", default=list_voice_ids()[0],
                        choices=list_voice_ids(),
                        help=f"Voice ID（默认 {list_voice_ids()[0]}）；"
                             "各音色的语言/性别与搭配建议见 "
                             "references/tts_pipeline.md 的预置音色表")
    parser.add_argument("--voice-style",
                        default="专业新闻播报，语速适中，语气沉稳自信，中英文表达流畅自然",
                        help="Voice style description")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP,
                        help="Silence gap between sentences (seconds)")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help="Speech speed multiplier via ffmpeg atempo "
                             "(1.0=normal, 1.5=faster). Default follows "
                             "_contracts.DEFAULT_SPEED (single source).")
    parser.add_argument("--loudness", type=float, default=None,
                        help="响度归一化目标（LUFS，如 -16）。默认不做归一化；"
                             "设置后对最终音频做单遍 loudnorm")
    parser.add_argument("--resume", action="store_true",
                        help="仅当缓存 WAV 能被「证明」属于当前稿件时跳过重合成："
                             "内容指纹 .sha、可测时长、无截断、语速状态 .spd 全部"
                             "对上才算命中（命中打 [skip,cached]/[skip,respeed]，"
                             "失效打「缓存失效（原因）」）。判定口径见 "
                             "references/tts_pipeline.md")
    parser.add_argument("--bgm", default=None,
                        help="Background music file path (mp3/wav/ogg)")
    parser.add_argument("--bgm-volume", type=float, default=0.15,
                        help="BGM volume relative to voice (0.0-1.0, default 0.15)")
    parser.add_argument("--model", default=None,
                        help="TTS model name (default: MIMO_TTS_MODEL env var or "
                             "'mimo-v2.5-tts')")
    parser.add_argument("--base-url", default=None,
                        help="MiMo TTS API base URL (default: MIMO_BASE_URL env var "
                             "or 'https://api.xiaomimimo.com/v1')")
    parser.add_argument("--api-timeout", type=float, default=30.0,
                        help="单次 TTS API 调用超时（默认 30s）")

    parser.add_argument("--dry-run", action="store_true",
                        help="Only split sentences + detect segments; print preview "
                             "without calling TTS API or writing audio. Useful for "
                             "verifying script formatting without spending credits.")
    parser.add_argument("--workers", type=int, default=4,
                        help="并行 TTS 调用数（default 4）")
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
    # --loudness 直接拼进 ffmpeg 滤镜串，NaN/Inf 会产出非法滤镜再报一串
    # 迷惑性 stderr；跟 --bgm-volume 的处理对齐，提前拦下
    if args.loudness is not None and not math.isfinite(args.loudness):
        parser.error(f"--loudness 必须是有限数值（LUFS，收到 {args.loudness}）")
    # --bgm-volume 同样会拼进 ffmpeg 滤镜串（BGM 混音段），NaN/Inf
    # 在这里提前拦下——原路径拖到混音阶段才报错，TTS 额度已经白烧一遍
    if not math.isfinite(args.bgm_volume):
        parser.error(f"--bgm-volume 必须是有限数值（0.0-1.0，收到 {args.bgm_volume}）")
    if args.workers < 1:
        parser.error(f"--workers 至少为 1（收到 {args.workers}）")
    # --api-timeout 直接交给 HTTP 客户端当超时用：0 意味着"每次请求立刻超时"，
    # 负数/NaN 则由客户端报一串迷惑性错误——三种情况都会让每句白重试 3 次、
    # 整条管线在烧完时间后才报"全部句子失败"。
    if not math.isfinite(args.api_timeout) or args.api_timeout <= 0:
        parser.error(f"--api-timeout 必须是大于 0 的有限秒数（收到 {args.api_timeout}）")
    # --bgm 指向不存在的文件时提前警告并忽略，而不是静默跳过混音——
    # 用户以为加了 BGM，成片里却没有，排查起来非常绕。
    # 忽略的同时留一个标记：混音阶段压根没跑，走不到下面的
    # bgm_mix_failed 分支，"显式要了 BGM 却没有"这件事也必须进 degraded 明细。
    if args.bgm and not os.path.exists(args.bgm):
        print(f"[warn] --bgm 文件不存在，已忽略 BGM 混音：{args.bgm}",
              file=sys.stderr)
        args.bgm_missing_file = True
        args.bgm = None



def _finalize_audio_and_manifest(args, ffmpeg_path, sentence_data, source_data, seg_config,
                                 silence_fallback_count, total_sentences, cached_count,
                                 voices_used=None):
    # 降级明细：任何"用户显式要了、但这次没做到"的事都记在这里，最后统一
    # 翻成 status=degraded 交给 run.py 的 --allow-degraded 闸门。只打一行
    # 滚动过的 [warn] 就等于静默降级——链路照样跑通、成片照样出，没人会回头
    # 看警告，而响度没归一化、BGM 没混进去、少了一整段这些事实都已经丢了。
    _degraded = {}
    # ── Concatenate ────────────────────────────────────────────────
    print(f"\n[concat] {len(sentence_data)} clips (gap {args.gap}s)...", flush=True)
    audio_files = [s["file"] for s in sentence_data]
    combined_path = os.path.join(args.output, "combined.wav")
    concat_ok = concat_audio(ffmpeg_path, audio_files, args.gap, combined_path)

    if not concat_ok:
        print("[error] Audio concat failed", file=sys.stderr)
        sys.exit(1)

    total_dur = measure_duration(ffmpeg_path, combined_path)
    if not total_dur or total_dur <= 0:
        # 测量失败时 total_dur=0 仍能通过契约的数值校验，产出"合法但废掉"
        # 的 manifest（data-duration=0 的 composition 渲染出无声空片）——
        # 在这里拦下比让废品流到渲染端好
        print("[error] combined.wav 时长测量失败（0.0s）——ffmpeg 无法读取"
              "拼接产物？检查磁盘空间与 ffmpeg 可用性", file=sys.stderr)
        sys.exit(1)
    # 实测总时长必须跟"逐句时长之和 + 句间静音"对得上：ffmpeg 的 concat
    # demuxer 在输入格式不一致时会**返回 0** 却吐出错采样率、被截断的音频
    # （实测数字记在 _audio.concat_audio 的注释里），只看 returncode 检不出
    # 这类静默损坏。缺音频 = 字幕从某一刻起整体提前，成片照样能播完，
    # 所以宁可在这里停住让人查，也不要悄悄少一段。
    expected_total = (sum(sd["duration"] for sd in sentence_data)
                      + args.gap * max(0, len(sentence_data) - 1))
    drift = expected_total - total_dur
    # 容差与下游契约的"末句不得超出 total_duration + 250ms"同档——这一步放行、
    # 下游却拒收，等于 TTS 额度烧完才告诉用户产物不能用。真正的截断量级是秒到
    # 几十秒，250ms 足够吸收逐句 round(x,3) 的累计舍入与 concat 边界误差。
    if drift > 0.25:
        print(f"[error] 拼接产物比预期短 {drift:.2f}s（实测 {total_dur:.2f}s / "
              f"预期 {expected_total:.2f}s）——多半是句子音频格式不一致或写盘被"
              f"截断。检查 {combined_path} 与音频目录里各句 WAV 的采样率/声道；"
              "确认无误后可删掉 combined.wav 重跑（--resume 会复用已合成的句子）",
              file=sys.stderr)
        sys.exit(1)
    print(f"[done] Total audio: {total_dur:.2f}s", flush=True)

    # ── Calculate start times ──────────────────────────────────────
    # 提前到 BGM 混音之前算，因为混音后的 manifest 需要每句的 start_time。
    cumulative = 0.0
    for i, sd in enumerate(sentence_data):
        sd["start_time"] = round(cumulative, 3)
        cumulative += sd["duration"]
        if i < len(sentence_data) - 1:
            cumulative += args.gap

    # ── Optional BGM mix ───────────────────────────────────────────
    # 三种"要了 BGM 却没有"：文件在校验后被删（本 if 不进）、--bgm 校验时就
    # 不存在（上面已清空并打 bgm_missing_file）、混音本身失败（下面的
    # bgm_mix_failed）。三者都必须落到 degraded 明细，否则成片静音轨照常交付。
    if getattr(args, "bgm_missing_file", False):
        _degraded["bgm_missing_file"] = True
    if args.bgm and not os.path.exists(args.bgm):
        _degraded["bgm_missing_file"] = True
        print("[warn] --bgm 文件在校验后消失，跳过混音"
              "（已记入 manifest 的 degraded 明细）", file=sys.stderr, flush=True)
    if args.bgm and os.path.exists(args.bgm):
        # bgm_volume 直接插进 ffmpeg filter_complex 字符串："1,aecho" 这类值会
        # 注入任意滤镜，所以只取数值并 clamp 到 [0,1]（>1 会削波失真）。
        # 非数值/NaN/Inf 已在 argparse 阶段 parser.error 拦下（早于 TTS，不烧额度）。
        if args.bgm_volume < 0 or args.bgm_volume > 1:
            clamped = max(0.0, min(1.0, args.bgm_volume))
            print(f"[warn] --bgm-volume {args.bgm_volume} out of [0,1], "
                  f"clamped to {clamped}", file=sys.stderr)
            args.bgm_volume = clamped
        print(f"[bgm] Mixing {args.bgm} at volume {args.bgm_volume}...", flush=True)
        mixed_path = os.path.join(args.output, "combined_bgm.wav")
        if mix_bgm(ffmpeg_path, combined_path, args.bgm, args.bgm_volume, mixed_path):
            combined_path = mixed_path
            # 换了母带文件就得重新量长度：amix 的 duration=first 理论上跟人声等长，
            # 但"理论上"正是这条管线被 amix 静默截断教育过的地方（见 _audio.mix_bgm）。
            # 沿用人声长度会让 total_duration 与实际音频不符，而契约照样放行。
            mixed_dur = measure_duration(ffmpeg_path, mixed_path)
            if mixed_dur and mixed_dur > 0:
                total_dur = mixed_dur
            print(f"  [OK] {mixed_path}", flush=True)
        else:
            _degraded["bgm_mix_failed"] = True
            print("  [warn] BGM mix failed, using voice-only audio"
                  "（本次成片不含 BGM，已记入 manifest 的 degraded 明细）",
                  file=sys.stderr, flush=True)

    # ── Optional loudness normalization ────────────────────────────
    if args.loudness is not None:
        loud_path = os.path.join(args.output, "combined_loud.wav")
        if apply_loudnorm(ffmpeg_path, combined_path, loud_path, args.loudness):
            loud_dur = measure_duration(ffmpeg_path, loud_path)
            if loud_dur and loud_dur > 0:
                combined_path = loud_path
                total_dur = loud_dur
                print(f"  [loudness] normalized to {args.loudness} LUFS -> {loud_path}",
                      flush=True)
            else:
                _degraded["loudness_norm_failed"] = True
                # 换文件后测量失败会把 total_dur 置 0，契约层仍放行"合法但废掉"
                # 的 manifest——回退未归一化音频并保留原时长，比交给下游强校验好
                print("  [warn] loudness 产物时长测量失败，沿用未归一化音频"
                      "（响度未达标，已记入 manifest 的 degraded 明细）",
                      file=sys.stderr, flush=True)
        else:
            _degraded["loudness_norm_failed"] = True
            print("  [warn] loudness normalization failed, using un-normalized audio"
                  "（响度未达标，已记入 manifest 的 degraded 明细）",
                  file=sys.stderr, flush=True)

    # ── Build manifest ─────────────────────────────────────────────
    manifest_sentences = []
    for s in sentence_data:
        entry = {
            "index": s["index"],
            "text": s["text"],
            "start_time": s["start_time"],
            "duration": s["duration"],
        }
        if s.get("speaker"):
            entry["speaker"] = s["speaker"]  # 对话段：说话人标签只进 manifest 数据层，画面不渲染
        if s.get("synth_failed"):
            entry["synth_failed"] = True  # TTS 失败降级为静音占位（见 --on-fail）
        manifest_sentences.append(entry)

    # 成片长度必须罩住时间轴：音频比最后一句的结束时刻短一分，画面就会在字幕
    # 还没走完时提前结束。用写进 manifest 的（已 round 的）值算，与契约层
    # "最后一句不得超出 total_duration + 250ms" 校验的是同一个量。
    # 宁可让 composition 比音频长几十毫秒（末句字幕本来就要显示完），也不让下游
    # 按一条它自己必然违反的规则拒收这份 manifest。
    _timeline_end = max((s["start_time"] + s["duration"])
                        for s in manifest_sentences)
    # 0.005s 显示精度门槛：逐句 start/duration 都是 round(x,3) 的浮点累加，
    # 与实测值之间必然存在亚毫秒级噪声。用裸 `>` 判定时每次运行几乎都命中，
    # 打出来的却是"音频实测 5.48s 短于时间轴终点 5.48s"——两个数按 .2f
    # 格式化后相同，自相矛盾的日志比没有日志更糟。
    if _timeline_end - total_dur > 0.005:
        # 补长只能兜住"round 到毫秒后差一点点"（几十毫秒量级）。契约层允许
        # 最后一句超出 total_duration 至多 250ms，所以超出 250ms 就不是舍入
        # 问题，而是音频文件真被截断了——amix / loudnorm 都改写过母带。
        # 无条件按时间轴取值会把这种截断抹平成一份 status=ok 的 manifest：
        # 片尾几秒没声音，没人知道。记进 degraded 让 run.py 的闸门拦得住。
        _deficit = _timeline_end - total_dur
        if _deficit > 0.25:
            _degraded["audio_shorter_than_timeline"] = round(_deficit, 3)
            print(f"[duration][warn] 音频实测 {total_dur:.2f}s 比时间轴终点 "
                  f"{_timeline_end:.2f}s 短 {_deficit:.2f}s（超出 250ms 舍入裕量，"
                  f"多半是混音/响度归一化把文件截断了），total_duration 按时间轴"
                  f"取值，末尾 {_deficit:.2f}s 无音频，已记入 degraded 明细",
                  file=sys.stderr, flush=True)
        else:
            print(f"[duration] 音频实测 {total_dur:.2f}s 短于时间轴终点 "
                  f"{_timeline_end:.2f}s，total_duration 按时间轴取值", flush=True)
        total_dur = _timeline_end

    # 丢失句数按"应产出 − 实产出"算，而不是复用调用方的 failed 列表：
    # 每个 index 在 sentence_data 里恰好出现一次（缓存分支或合成分支各
    # append 一次），所以差额就是凭空消失的句子。这样任何一条"某句没进
    # manifest"的路径（含静音兜底自身也失败）都会把 status 翻成 degraded，
    # 而不是只剩一行滚动过的 [warn]，下游 run.py 的 --allow-degraded 闸门
    # 才有东西可拦。
    lost_count = max(0, total_sentences - len(sentence_data))
    if silence_fallback_count:
        _degraded["tts_silence_fallback_count"] = silence_fallback_count
    if lost_count:
        _degraded["tts_lost_sentence_count"] = lost_count
    _voices = sorted(voices_used or {args.voice_id})
    manifest = {
        "schema_version": 2,
        # status 在写盘前按 _degraded 统一复核（段落剔除发生在下面）。
        "status": "ok",
        "degraded": _degraded,
        "sentences": manifest_sentences,
        "total_duration": round(total_dur, 3),
        "gap": args.gap,
        # 本次真正用到的音色：对话稿/分段音色会让顶层 --voice-id 只代表"默认值"，
        # 照抄 args.voice_id 等于在 manifest 里声明一件不成立的事。单一音色时
        # 仍是那一个词，多音色时是逗号连接的清单。
        "voice_id": ",".join(_voices),
        "combined_audio": os.path.abspath(combined_path),
    }
    # 结尾 agenda 卡的至多一条可选尾行：行动号召或下期预告二选一，
    # 顶层字段透传进 manifest，renderer 拼在要点总结之后。
    _val = str(source_data.get("cta") or "").strip()
    if _val:
        manifest["closing_cta"] = _val

    # ── Optional segment grouping (seg_config loaded earlier) ─────
    if seg_config:
        grouped = []
        dropped = []
        for seg in seg_config:
            start_idx = seg["start"]   # 0-based sentence index (inclusive)
            end_idx = seg["end"]       # exclusive
            seg_sentences = [
                s for s in manifest_sentences
                if start_idx <= s["index"] < end_idx
            ]
            _seg_id = seg.get("id", f"seg{len(grouped)+1}")
            if not seg_sentences:
                # 该段所有句子都没产出音频（TTS 连续失败 + --on-fail abort，
                # 或段落本身被上游丢空）。空段落进 manifest 会被 _contracts
                # 的校验直接拒收（"缺少非空 sentences 列表"），
                # gen_hyperframes 随之退出——一次失败就让整条视频出不来。
                # 剔除并报对人，而不是写出一份下游必然拒收的 manifest。
                dropped.append(_seg_id)
                continue
            # tagline 兜底：只对 seg… 内容段落兜底为 "补充阅读"，避免画面
            # 缺字；opening/closing 是结构性段落，留空即不显示小标题，不塞
            # 通用标签。兜底文案必须是内容中立词（本技能信源不限于 AI 资讯），
            # 与 SKILL.md 字段说明保持一致。
            _tagline = seg.get("tagline", "")
            if not _tagline and is_content_sid(_seg_id):
                _tagline = "补充阅读"
            seg_out = {
                "id": _seg_id,
                "title": seg.get("title", ""),
                "tagline": _tagline,
                "accent": seg.get("accent", DEFAULT_ACCENT),
                "sentences": seg_sentences,
            }
            # 透传可选字段：speed（段落级语速）、voice_id/voice_style（段落级音色）。
            if seg.get("speed") is not None:
                seg_out["speed"] = seg["speed"]
            if seg.get("voice_id") is not None:
                seg_out["voice_id"] = seg["voice_id"]
            if seg.get("voice_style") is not None:
                seg_out["voice_style"] = seg["voice_style"]
            if seg.get("takeaway") is not None:
                seg_out["takeaway"] = seg["takeaway"]
            if seg.get("turns"):
                seg_out["turns"] = seg["turns"]
            grouped.append(seg_out)
        manifest["segments"] = grouped
        if dropped:
            # 整段消失是这行日志里最重的一种降级：稿子写了 8 段、画面只有 6 段。
            # 光打 [warn] 不够——下游只看 manifest 状态就会当正常片放行。
            _degraded["segments_dropped"] = list(dropped)
            # 静默剔除会让"我写了 8 段、成片只有 6 段"变成无解的困惑；
            # 报出被剔除的 sid 与原因，让失败可归因。
            print(f"\n[warn] {len(dropped)} 个段落没有任何可用音频，"
                  f"已从 manifest 剔除：{', '.join(dropped)}\n"
                  f"       常见原因是这几段 TTS 连续失败且 --on-fail abort"
                  f"（默认）——检查一下上面这些句子的 [fail] 日志，修掉后"
                  f"重跑即可自动补回；--on-fail silence 会生成静音占位、"
                  f"不会触发剔除。", file=sys.stderr, flush=True)

    # ── Write manifest ─────────────────────────────────────────────
    # 降级状态收口：上面任何一条"显式要了却没做到"都会把 status 翻成
    # degraded，run.py 的 --allow-degraded 闸门才有东西可拦。
    manifest["status"] = "degraded" if _degraded else "ok"
    if _degraded:
        print(f"[degraded] 本次产物含降级项：{_degraded}\n"
              "           明细见 timing_manifest.json 的 degraded 字段；"
              "默认会阻断正式渲染，确认接受后再加 --allow-degraded。",
              file=sys.stderr, flush=True)

    # 自证：写盘前按下游同一份契约校一遍。这些校验原本只在 run.py /
    # gen_hyperframes 加载时跑——于是 pipeline 报"[done] 成功"、额度也花了，
    # 用户却在几秒后收到一份"manifest 不合法"。产物不合格要在产出这一刻说。
    try:
        validate_timing_manifest(manifest)
    except ValueError as e:
        print(f"[error] 本次生成的 timing_manifest.json 不合格，已拒绝写盘：{e}\n"
              f"       音频中间产物仍保留在 {args.output}，修掉上面这条原因后重跑"
              "（不要手工改 manifest 绕过校验）。", file=sys.stderr)
        sys.exit(1)
    # 原子写：timing_manifest.json 是下游（gen_hyperframes / run.py 渲染）
    # 唯一的时间轴数据源，写到一半被 Ctrl-C 打断会留下一份
    # 截断的 JSON——下次 --resume 直接崩在 json.load，且堆栈完全不指向
    # "上次中断了，重跑一遍就好"。先写 .tmp 再 replace，要么完整要么不存在。
    manifest_path = os.path.join(args.output, "timing_manifest.json")
    write_json_atomic(manifest_path, manifest, indent=2)

    print(f"\n[manifest] {manifest_path}", flush=True)
    # total_sentences / cached_count 由调用方传入：它们都是 main() 的局部
    # 变量，这里若直接引用会在每次 TTS 成功走到这一行时崩 NameError
    # （该路径必须保持独立守卫，避免单行输入触发假死）。
    print(f"[stats] {len(sentence_data)}/{total_sentences} sentences OK "
          f"({cached_count} cached)", flush=True)
    print(f"[duration] {total_dur:.2f}s", flush=True)

def main():
    setup_stdio()
    parser = _build_parser()
    args = parser.parse_args()
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

    # ── Resolve API key ────────────────────────────────────────────
    api_key = get_key("MIMO_API_KEY", args.api_key)
    if not api_key and not args.dry_run:
        print("[error] No API key. Use --api-key or set MIMO_API_KEY in .env",
              file=sys.stderr)
        sys.exit(1)

    # ── Resolve model + base_url (CLI > env/config > default) ──────
    model, base_url = resolve_model_config(
        args.model, args.base_url, "MIMO_TTS_MODEL", "mimo-v2.5-tts")

    # ── Read script（--source 结构化输入，逐段独立分句）───────────────
    try:
        source_data = load_segments_source(args.source)
        sentences, seg_config = build_parts(source_data)
    except ValueError as e:
        print(f"[error] 结构化稿件无效：{e}", file=sys.stderr)
        sys.exit(1)
    if not sentences:
        print("[error] 结构化稿件分句为空，请检查 segments_source.json 的文本",
              file=sys.stderr)
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

    # ── Initialize OpenAI client ───────────────────────────────────
    try:
        from openai import OpenAI
    except ImportError as e:
        print(f"[error] TTS 步骤缺少第三方依赖 openai（{e}）。"
              "这是全管线唯一需要 pip 安装步骤：pip install openai；"
              "写稿、配图、生成 HTML、渲染都不受此影响。",
              file=sys.stderr)
        sys.exit(1)
    # max_retries=0：SDK 内部默认还会静默重试 2 次，叠加本模块自己的
    # 3 次应用层重试 = 单句最多 6 次 billable 请求。重试策略统一收口到
    # synth_sentence（带退避抖动），SDK 层关掉。
    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    print(f"[api] model={model} base_url={base_url}", flush=True)

    # ── Build per-sentence speed / voice mapping ────────────────────
    # 每句动态语速：若段落指定了 speed 则覆盖全局 args.speed，
    # 没有指定 speed 的段落用全局 args.speed。voice 同理——不同段落
    # （比如开场用甲音色、正文用乙音色）可以分别指定 voice_id/voice_style。
    sentence_speeds = {}
    sentence_voices = {}  # index -> (voice_id, voice_style)
    sentence_speaker_labels = {}  # index -> 说话人标签（仅对话段有值；只进 manifest 数据层）
    if seg_config:
        for seg in seg_config:
            start_idx = seg.get("start", 0)
            end_idx = seg.get("end", len(sentences))
            seg_speed = seg.get("speed")
            if seg_speed is not None:
                for si in range(start_idx, min(end_idx, len(sentences))):
                    sentence_speeds[si] = seg_speed
            seg_voice_id = seg.get("voice_id")
            seg_voice_style = seg.get("voice_style")
            if seg_voice_id or seg_voice_style:
                for si in range(start_idx, min(end_idx, len(sentences))):
                    sentence_voices[si] = (
                        seg_voice_id or args.voice_id,
                        seg_voice_style if seg_voice_style is not None else args.voice_style,
                    )
            # ── 双人对话（turns）：比 segment 更细的子区间，按句覆盖 ────
            # segment 级设置——同一段里 A/B 两人交替发言，各自用各自的音色。
            # build_from_structured.py 已把 speakers 解析成每个 turn 自带的
            # voice_id/voice_style，这里不需要再反查任何 speakers 字典。
            for turn in seg.get("turns", []):
                t_start, t_end = turn.get("start", start_idx), turn.get("end", end_idx)
                t_voice_id = turn.get("voice_id")
                t_voice_style = turn.get("voice_style")
                t_label = turn.get("label") or turn.get("speaker")
                for si in range(t_start, min(t_end, len(sentences))):
                    if t_voice_id or t_voice_style:
                        base_id, base_style = sentence_voices.get(
                            si, (args.voice_id, args.voice_style))
                        sentence_voices[si] = (
                            t_voice_id or base_id,
                            t_voice_style if t_voice_style is not None else base_style,
                        )
                    if t_label:
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

        # Resume: 只有当内容指纹、时长、语速状态都能证明这段音频就是当前
        # 稿件时才跳过（判定全在 resolve_resume_state，这里只执行副作用）
        if args.resume and os.path.exists(out_path):
            facts = _observe_resume_state(out_path, ffmpeg_path, sent_tts,
                                          sent_voice_id, sent_voice_style, model)
            decision = resolve_resume_state(facts, sent_speed)
            if decision.action == "regen":
                # 六种失效成因（无指纹 / 指纹变了 / 文件没了 / 量不出时长 /
                # 被截断 / 语速状态无法证明）对写稿人是不同的三件事：改稿、
                # 坏文件、换参数。不报出来只能靠猜；同时无条件清掉整份
                # sidecar，别把 .failed / 旧 .orig.wav 带进这次重合成。
                print(f"  [{label}] 缓存失效（{decision.reason}），重新合成",
                      flush=True)
                _drop_stale_cache(out_path)
                pending_tasks.append({
                    "index": i, "text_tts": sent_tts,
                    "out_path": out_path, "label": label, "speed": sent_speed,
                    "voice_id": sent_voice_id, "voice_style": sent_voice_style,
                })
                continue
            restored = False
            if decision.restore_first:
                if apply_speed(ffmpeg_path, out_path, 1.0):
                    restored = True
                    print(f"  [{label}] .spd marker 不可读，已从 "
                          f".orig.wav 还原到原速，重新对齐语速", flush=True)
                else:
                    print(f"  [{label}][warn] .orig.wav 还原原速失败，"
                          f"跳过语速对齐（音频保持现状）", file=sys.stderr)
            if decision.action == "reapply":
                if apply_speed(ffmpeg_path, out_path, decision.apply_speed_to,
                               prev_speed=decision.prev_speed):
                    if decision.write_spd is not None:
                        try:
                            with open(out_path + ".spd", "w", encoding="utf-8") as f:
                                f.write(str(decision.write_spd))
                        except OSError as e:
                            print(f"  [{label}][warn] .spd marker 写入"
                                  f"失败（{e}），下次 --resume 会重新对齐语速",
                                  file=sys.stderr)
                    elif os.path.exists(out_path + ".spd"):
                        _remove_quiet(out_path + ".spd")
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
                    pending_tasks.append({
                        "index": i, "text_tts": sent_tts,
                        "out_path": out_path, "label": label, "speed": sent_speed,
                        "voice_id": sent_voice_id, "voice_style": sent_voice_style,
                    })
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
                        pending_tasks.append({
                            "index": i, "text_tts": sent_tts,
                            "out_path": out_path, "label": label,
                            "speed": sent_speed,
                            "voice_id": sent_voice_id,
                            "voice_style": sent_voice_style,
                        })
                        continue
                    dur = _restored_dur
                    # 还原成功 = 音频已回到原速：用可读的 "1.0" marker 替换
                    # 那份坏的。只删不写会落到"无 marker 但 .orig.wav 还在"
                    # 的状态，resolve_resume_state 每次 --resume 都会再走一遍
                    # 还原+重测（幂等但白烧 ffmpeg）；显式 1.0 才能直接证明
                    # 缓存有效。
                    try:
                        with open(out_path + ".spd", "w", encoding="utf-8") as f:
                            f.write("1.0")
                    except OSError as e:
                        print(f"  [{label}][warn] .spd marker 写入"
                              f"失败（{e}），下次 --resume 会重复一次还原",
                              file=sys.stderr)
                        _remove_quiet(out_path + ".spd")
            # 缺 .sha 已在 resolve_resume_state 判为 regen：不给归属不明的
            # 旧音频盖上当前文本的指纹（补写一次就把错位永久固化）。
            sd = {
                "index": i, "text": sentences[i],
                "file": out_path, "duration": round(dur, 3)
            }
            if i in sentence_speaker_labels:
                sd["speaker"] = sentence_speaker_labels[i]
            if decision.synth_failed:
                sd["synth_failed"] = True
            sentence_data.append(sd)
            # 缓存命中的两种形态分开报：纯复用一句 ffmpeg 都没跑；而
            # restore_first/reapply 分支本次实际重放了 atempo（花了 ffmpeg
            # 时间、改写了 WAV），一律打 (cached) 会掩盖"这次其实重新处理过
            # 音频"，排查语速问题时误导。cached_count 口径保持"没花 TTS 额度"。
            _reprocessed = (decision.restore_first or restored
                            or decision.action == "reapply")
            _tag = "skip,respeed" if _reprocessed else "skip,cached"
            print(f"  [{label}][{_tag}] {dur:.2f}s", flush=True)
            cached_count += 1
            continue

        pending_tasks.append({
            "index": i, "text_tts": sent_tts,
            "out_path": out_path, "label": label, "speed": sent_speed,
            "voice_id": sent_voice_id, "voice_style": sent_voice_style,
        })

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
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
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
                preview = sent_orig[:30] + "..." if len(sent_orig) > 30 else sent_orig

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
                            voice_style=task["voice_style"], model=model)
                        sd = {
                            "index": idx, "text": sent_orig,
                            "file": out_path, "duration": round(dur, 3)
                        }
                        if idx in sentence_speaker_labels:
                            sd["speaker"] = sentence_speaker_labels[idx]
                        new_results.append(sd)
                        # 这次是真的合成成功了——如果这句之前被 --on-fail silence
                        # 兜底过、留了个 .failed marker（比如用户手动删了旧的静音
                        # mp3 之后重跑、这次没再失败），清掉 marker，不然下次
                        # resume 会把这个已经补录好的句子又标成 synth_failed。
                        stale_marker = out_path + ".failed"
                        if os.path.exists(stale_marker):
                            _remove_quiet(stale_marker)
                        print(f"[TTS {done_count}/{pending_count}] {label} "
                              f"{preview} -> {dur:.2f}s", flush=True)
                        continue

                if args.on_fail == "silence":
                    # 降级：该句反复失败（如触发内容审核）时不再直接丢弃，改为
                    # 生成一段静音占位，时长用 estimate_sentence_seconds 同款启发式
                    # （字数/语速）估算——没有真实语速可测，只能估，与
                    # DEFAULT_CHARS_PER_SEC 假设保持一致，乘上该句实际 speed。
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
                        if not _clear_stale_sidecars(out_path):
                            print(f"    [{label}][warn] 上一轮 sidecar 清理失败，"
                                  f"若残留 .orig.wav，下次 --resume 可能用它覆盖本句"
                                  f"静音（重启该句合成即可恢复）", file=sys.stderr)
                        generate_silence(ffmpeg_path, fallback_dur, out_path)
                        # 落一个 sidecar marker（跟已有的 .spd 速度 marker 同一套
                        # 模式），不然下次 --resume 时这句会走"文件已存在=缓存"
                        # 分支重建 sentence_data，那个分支不知道这个文件其实是
                        # 静音兜底——synth_failed 标记会悄悄从 manifest 里消失，
                        # 事后再也看不出这句需要人工补录。
                        try:
                            with open(out_path + ".failed", "w",
                                       encoding="utf-8"):
                                pass
                        except OSError as _e:
                            print(f"    [{label}][warn] .failed marker 写入"
                                  f"失败（{_e}），下次 --resume 可能漏标"
                                  f"synth_failed", file=sys.stderr)
                        # 静音时长估算已除过 speed（estimate_sentence_seconds），
                        # 必须写 .spd marker 声明"已施加语速"——否则下次 --resume
                        # 会把这段静音再 atempo 一遍，占位时长被压缩到 2/3。
                        # .sha 内容指纹也一并落盘，跟正常合成句子的缓存语义一致。
                        # 用带兜底的写入：sidecar OSError 不该冒到下面的外层
                        # except——静音 WAV 已经在盘上，丢这句等于兜底失败。
                        _record_sentence_cache(
                            out_path, task["text_tts"], task["speed"],
                            voice_id=task["voice_id"],
                            voice_style=task["voice_style"], model=model)
                        sd = {
                            "index": idx, "text": sent_orig,
                            "file": out_path, "duration": round(fallback_dur, 3),
                            "synth_failed": True,
                        }
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
