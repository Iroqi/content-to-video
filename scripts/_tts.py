#!/usr/bin/env python3
"""TTS provider 协议层：管线与合成端点之间的唯一边界。

协议契约（提供方接入管线的全部表面）：
    client.synthesize(text, voice_id, voice_style, model, timeout) -> WAV 字节
    失败只有两类异常：TtsHttpError（非 2xx，带 status_code）与
    BadAudioResponseError（响应可读回但没有音频）；是否重试由
    is_non_retryable 统一裁决，pipeline 的重试/落盘/变速逻辑不感知
    具体提供方。

参数一律是管线的领域语汇（句子/音色/风格/模型），不是某家的请求体
形状——OpenAI chat/completions 的 messages 预填充与 audio 参数属于
MiMo 实现细节。第二个提供方到达时：在本文件（或新模块）加一个同
签名的类即可，synth_sentence 一行不动。
"""
import base64
import binascii
import json
import urllib.error
import urllib.request

# 默认模型名/base URL 只在这里写一次：--help 文案、"没配 key"的报错和实际请求
# 用的必须是同一个值，否则改了默认还在告诉用户旧的。
DEFAULT_MODEL = "mimo-v2.5-tts"
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"


class BadAudioResponseError(Exception):
    """TTS 响应里没有音频（chat.completions 返回了纯文本）。

    几乎总是 --base-url/--model 指向了不支持 audio 参数的网关或模型，换一句
    再试也是同样的响应，因此按确定性失败处理（判定见 is_non_retryable）。
    """


class TtsHttpError(Exception):
    """TTS 端点返回非 2xx。status_code 让 is_non_retryable 区分
    "重试也不会好"（400/401/…）与限流、网络抖动（仍走重试）。"""

    def __init__(self, status_code, detail):
        super().__init__(f"Error code: {status_code} - {detail}")
        self.status_code = status_code


class MimoTtsClient:
    """MiMo TTS 端点：协议契约的当前唯一实现。

    全管线唯一的外部网络接口，用标准库实现（urllib），让第 3 步与其余步骤一样
    零第三方依赖。请求体与 openai SDK 的 `chat.completions.create(model=,
    messages=, audio=)` 逐字段一致：POST {base_url}/chat/completions，
    Authorization: Bearer <key>，body {model, messages, audio}。
    """

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"

    def synthesize(self, text, voice_id, voice_style, model, timeout):
        """协议入口：一句文案 + 音色/风格/模型 → WAV 字节。

        voice_style 作为 user 消息预填充、正文挂 assistant 消息——MiMo 端点靠
        这种对话结构携带指令，属于实现细节，故从 synth_sentence 收进来。
        """
        messages = []
        if voice_style:
            messages.append({"role": "user", "content": voice_style})
        messages.append({"role": "assistant", "content": text})
        audio_params = {"format": "wav"}
        if voice_id:
            audio_params["voice"] = voice_id
        return self._audio_bytes(model, messages, audio_params, timeout)

    def _audio_bytes(self, model, messages, audio_params, timeout):
        """发起一次合成请求，返回 WAV 字节。timeout 是 socket 级读超时。"""
        body = json.dumps({"model": model, "messages": messages,
                           "audio": audio_params}).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TtsHttpError(e.code, e.read().decode("utf-8", "replace")[:300]) \
                from None
        except ValueError as e:
            # 200 但 body 不是 JSON（网关返回了 HTML 错误页之类）：
            # 端点配错，重试同一次请求不会变好，按确定性失败处理。
            raise BadAudioResponseError(
                f"TTS 响应不是合法 JSON（{e}）——检查 --base-url 是否指向 "
                "OpenAI 兼容的 chat/completions 端点") from None
        return _audio_from_response(payload)


def _audio_from_response(payload):
    """从 chat/completions 响应里取出 base64 音频并解码。

    显式检查结构而不是直接下钻 data：端点/模型配错时 message.audio 不存在，
    裸 KeyError 不带 status_code 会被归为可重试——每句白烧满 3 次 billable
    调用才放弃。这里转成确定性失败（见 BadAudioResponseError），首次即整句放弃。

    类型也要查：网关返回畸形 body（顶层是数组、choices[0] 是字符串、data 不是
    base64）时，下钻会抛 AttributeError/TypeError/binascii.Error。这些同样是
    确定性失败——同一个错配的端点重试 100 次还是那个响应，而每句要白烧 3 次
    计费调用。裸异常不带 status_code，is_non_retryable 会放它进重试队列。
    """
    if not isinstance(payload, dict):
        raise BadAudioResponseError(
            f"TTS 响应是 {type(payload).__name__} 而不是对象——检查 "
            f"--base-url 是否指向 OpenAI 兼容的 chat/completions 端点")
    choices = payload.get("choices") or []
    message = choices[0].get("message") if isinstance(choices, list) and choices \
        and isinstance(choices[0], dict) else None
    audio = message.get("audio") if isinstance(message, dict) else None
    data = audio.get("data") if isinstance(audio, dict) else None
    if not data:
        raise BadAudioResponseError(
            "TTS 响应不含音频（chat.completions 返回了纯文本）——"
            "检查 --model/--base-url 是否指向支持 audio 参数的"
            f" TTS 模型（默认 {DEFAULT_MODEL}），不要指向普通对话模型")
    try:
        return base64.b64decode(data)
    except (binascii.Error, TypeError, ValueError) as e:
        raise BadAudioResponseError(
            f"TTS 响应的 audio.data 不是合法 base64（{e}）——端点返回了"
            "非音频内容，检查 --base-url 是否指向支持 audio 参数的 TTS 模型"
        ) from None


def is_non_retryable(exc):
    """判断异常是否属于"重试也不会好"的确定性失败。

    带 status_code 的 TtsHttpError：400（参数/内容审核拒绝）、401/403（密钥
    错误/无权限）、404（模型不存在）、422（请求不合法）这类错误重试 N 次结果
    完全一样——每句烧满 3 次重试只会浪费额度和时间（100 句 × 无效 key = 300
    次无效调用 + 每句多等 6s），直接放弃。响应不含音频（BadAudioResponseError）
    同理：端点/模型配错了，换一句再试也是同样的纯文本响应。连接/超时/429 限流
    类不带 status_code 或带可重试码，仍走重试。
    """
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (400, 401, 403, 404, 422)
    return False
