#!/usr/bin/env python3
"""TTS provider 协议层：管线与合成端点之间的唯一边界。

协议契约（提供方接入管线的全部表面）：
    client.synthesize(text, voice_id, voice_style, model, timeout) -> WAV 字节
    失败分三类：TtsHttpError（非 2xx，带 status_code）、
    BadAudioResponseError（响应可读回但没有音频/不是合法音频）、
    网络级异常（连接失败/DNS/读超时，urllib 原样上抛不带 status_code）；
    是否重试由 is_non_retryable 统一裁决（前两类确定性失败，网络类可重试），
    pipeline 的重试/落盘/变速逻辑不感知具体提供方。

    参数一律是管线的领域语汇（句子/音色/风格/模型），不是某家的请求体
    形状——OpenAI chat/completions 的 messages 预填充与 audio 参数属于
    MiMo 实现细节。

第二个提供方到达时：在本文件加一个同签名的类 + 在 PROVIDERS 注册一行，
`make_client()` 按名字取用，pipeline 一行不动。接口面刻意只有
`synthesize` 一个方法（不是抽象基类）：测试里的 Fake 客户端、以及将来
接 edge-tts / 本地模型这类"根本没有 base_url"的实现，都不必继承任何东西。
"""
import base64
import binascii
import json
import re
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

    全管线唯一的外部网络接口，用标准库实现（urllib），让第 2 步与其余步骤一样
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
        try:
            req = urllib.request.Request(
                self.url, data=body, method="POST",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"})
            # Request() 构造与 urlopen 都可能对畸形 URL 抛 ValueError
            # （`unknown url type`）：构造阶段就抛，所以两者必须在同一段 try 里。
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            # 响应体单独兜一层：连接已在断的边缘时 `e.read()` 自己会抛（超时/
            # ConnectionReset），直接写在 raise 里会把 `e.code` 一起吞掉——
            # 401 这类确定性失败退化成"不带 status_code 的网络错误"，每句白烧满
            # 3 次 billable 调用才放弃。状态码是分类的依据，必须先保住。
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                detail = "（响应体读不出来）"
            raise TtsHttpError(e.code, detail) from None
        except ValueError as e:
            # urlopen 自己抛的 ValueError（典型是 `unknown url type`——base-url
            # 根本不是个 URL）。这与"200 但 body 不是 JSON"是两回事：请求根本
            # 没发出去。端点配错是确定性失败（重试 3 次还是那个 URL），必须收敛
            # 成 BadAudioResponseError——以裸 ValueError 的身份会被
            # is_non_retryable 放行，每句白跑三轮重试。
            raise BadAudioResponseError(
                f"TTS 请求地址不合法（{e}）——检查 --base-url 的写法"
                "（应是 http(s)://… 的完整地址）") from None
        # 解码**单起一层，绝不放进上面那段 try**：UnicodeDecodeError 是 ValueError
        # 的子类，混进去会被"地址不合法"那条接住。于是网关/代理回了压缩或二进制的
        # 错误页时，报错指向 --base-url（而它是对的），并把一次可能瞬态的网关故障
        # 钉成确定性失败——100 句里第 1 句就整条放弃。
        try:
            raw_body = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise BadAudioResponseError(
                f"TTS 响应不是 UTF-8 文本（{e}）——多半是网关/代理塞回了压缩或"
                "二进制的错误页，检查 --base-url 是否直连支持 audio 参数的端点"
            ) from None
        # JSON 解析留在 urlopen 的 try **之外**：urlopen 的 ValueError 上面已接住，
        # 这里只剩响应的解析。200 但 body 不是 JSON（网关返回 HTML 错误页之类）
        # 也是端点配错：重试同一次请求不会变好，按确定性失败处理。
        try:
            payload = json.loads(raw_body)
        except ValueError as e:
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

    base64 必须**严格**解码（validate=True）：宽松模式会把字母表外的字符**静默
    丢弃**——`b64decode("!!!!")` 返回 b'' 而不抛错，`b64decode("!!!!AAAA")`
    返回 3 字节。于是"端点回了垃圾"这条确定性失败被解码成一个 0 字节 WAV，
    直到下游 measure_duration 量出 0 才降级：失败分类与病因脱节，本函数
    docstring 承诺的"当场报出端点配错"永远打不出来。空白（换行/空格）是合法
    传输噪声，先剥掉再验。
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
        # 先剥空白再严格校验：RFC 2045 允许 base64 带换行，而 validate=True 会把
        # 它当非法字符。剥掉后 "!!!!" 这类垃圾才会被 binascii.Error 抓住。
        compact = re.sub(r"\s+", "", data) if isinstance(data, str) else data
        if not compact:
            # 纯空白 data：剥完之后是空串，而空串是合法 base64（解出 b''），
            # validate=True 不拦。不在这里挡，"没有音频"就变成一个 0 字节
            # "WAV" 落盘，直到下游 measure_duration 量出 0 才失败——失败分类
            # 与病因脱节，正是本函数 docstring 要避免的那个形态。
            raise BadAudioResponseError(
                "TTS 响应的 audio.data 是空白——端点返回了空音频，检查 "
                "--base-url/--model 是否指向支持 audio 参数的 TTS 模型"
                f"（默认 {DEFAULT_MODEL}）")
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, TypeError, ValueError) as e:
        raise BadAudioResponseError(
            f"TTS 响应的 audio.data 不是合法 base64（{e}）——端点返回了"
            "非音频内容，检查 --base-url 是否指向支持 audio 参数的 TTS 模型"
        ) from None


def is_non_retryable(exc):
    """判断异常是否属于"重试也不会好"的确定性失败。

    带 status_code 的 TtsHttpError：**4xx 一律确定性**，只有 408（请求超时）与
    429（限流）例外——那两个是"同一个请求再来一次可能就成了"。400（参数/内容
    审核拒绝）、401/403（密钥错/无权限）、404（模型不存在）、410/413/431
    （资源没了/请求太大/头太大）这类重试 N 次结果完全一样——每句烧满 3 次重试
    只会浪费额度和时间（100 句 × 无效 key = 300 次无效调用 + 每句多等 6s），
    直接放弃。

    判据写成"4xx 减两个例外"而不是枚举具体码：枚举表每遇到一个没列进来的
    确定性码（405/410/413/431…）就要回头补一次，漏一天就白烧一天额度；
    而"客户端错误不该重试"是 HTTP 自己的语义，例外只有两个。
    响应不含音频（BadAudioResponseError）同理：端点/模型配错了，换一句再试
    也是同样的纯文本响应。连接/超时类不带 status_code，仍走重试。
    """
    if isinstance(exc, BadAudioResponseError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status not in (408, 429)
    return False


# ===================================================================
# Provider 注册表：管线只通过 make_client() 拿到一个"synthesize 可调用"的对象
# ===================================================================
#
# 为什么要有这一层：TTS 是整条链路唯一的付费外部依赖，也是唯一"换一家就
# 得改 pipeline"的地方。之前 `client = MimoTtsClient(...)` 直接写在 main()
# 里，等于把"必须买 MiMo"编码进了编排层——想在别的环境验证链路（或换一家
# 供应商）只能改代码。收成注册表之后，加一家 = 加一个类 + 加一行。

class BaseTtsProvider:
    """提供方的可选基类：只提供三样**非必需**的东西。

    刻意不是 abc.ABC：`synthesize` 的签名就是全部契约（见文件头），
    强制继承只会让测试里的 Fake 与本应零依赖的本地实现被迫依赖本模块。
    继承它的唯一收益是 `label` / `needs_api_key` 有默认值。
    """

    #: 产出的音频格式（管线按 WAV 处理：wave 读帧数、ffmpeg atempo）。
    audio_format = "wav"

    #: 是否需要 API key。本地/离线提供方返回 False，管线的"没 key 就停"
    #: 那道闸只对返回 True 的提供方生效。
    needs_api_key = True

    #: 日志与 production_report 里显示的名字。
    label = "base"

    def synthesize(self, text, voice_id, voice_style, model, timeout):
        raise NotImplementedError


class MimoTtsProvider(MimoTtsClient, BaseTtsProvider):
    """MiMo 端点（默认提供方）。

    只做两件事：补上 label 与 needs_api_key（客户端本身不关心这些），
    并让类名与其它提供方对齐。已有测试仍在 patch `MimoTtsClient`，
    继承关系保持向后兼容。
    """

    label = "mimo"
    needs_api_key = True


#: 名字 → (构造类, 该提供方是否认 base_url 这个参数)。
#: 第三项不是装饰：本地提供方没有 base_url，硬塞一个进去只会在日志里
#: 打出一个"看着像在用某个端点"的假地址。
PROVIDERS = {
    "mimo": (MimoTtsProvider, True),
}

#: `--tts-provider` 的取值顺序（= --help 里的展示顺序）。
PROVIDER_NAMES = tuple(PROVIDERS)

#: 缺省提供方。与 DEFAULT_MODEL / DEFAULT_BASE_URL 同源同家：换默认提供方
#: 必须连着换默认模型，否则拿 MiMo 的模型名去打别人家的端点。
DEFAULT_PROVIDER = "mimo"


def make_client(provider=None, api_key=None, base_url=None, factory=None):
    """按名字造一个提供方实例。未知名字抛 ValueError（由调用方转成 CLI 报错）。

    `factory` 是可注入的构造钩子（签名 `(api_key, base_url) -> client`）：
    测试用它塞假客户端，pipeline 把它当成一个模块级名字 patch
    （`pipeline.TTS_CLIENT_FACTORY`）。没有这个钩子，"换掉网络层"就只能靠
    `patch.object(pipeline, "MimoTtsClient")`——那是拿类名当接缝，一旦默认
    提供方不是 MiMo，测试仍在 patch 一个根本没被用到的符号，**照样全绿**。

    返回的对象只保证有 `synthesize`；`label` / `needs_api_key` 用 getattr
    带默认值读，第三方实现不必继承 BaseTtsProvider。
    """
    if factory is not None:
        return factory(api_key, base_url)
    name = provider or DEFAULT_PROVIDER
    entry = PROVIDERS.get(name)
    if entry is None:
        raise ValueError(
            "未知的 TTS 提供方 '%s'；可选：%s（新增提供方见 scripts/_tts.py "
            "的 PROVIDERS）" % (name, " / ".join(PROVIDER_NAMES)))
    cls, takes_base_url = entry
    if takes_base_url:
        return cls(api_key, base_url)
    return cls()


def provider_label(client):
    """日志用：提供方名字。第三方实现没写 label 时退回类名。"""
    return getattr(client, "label", None) or type(client).__name__


def provider_needs_api_key(client):
    """`--tts-provider` 是否需要 key。默认 True（宁可多要一次，不要静默不带鉴权）。"""
    return getattr(client, "needs_api_key", True)


def provider_takes_base_url(name=None):
    """该提供方认不认 base_url（决定日志里要不要打出端点地址）。"""
    entry = PROVIDERS.get(name or DEFAULT_PROVIDER)
    return bool(entry and entry[1])


def provider_takes_base_url_for_instance(client):
    """按实例反查：从类名找注册表里的那一行。找不到按"认"处理。

    宁可多打一行 base_url，也不要因为反查失败而把真实端点藏起来——
    排障时"日志里没有端点"比"多打了一个默认端点"更难受。
    """
    for _name, (cls, takes) in PROVIDERS.items():
        if isinstance(client, cls):
            return takes
    return True
