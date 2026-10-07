"""TTS provider 协议层契约：_tts.py 的请求体形状、重试裁决与
synth_sentence 只经协议参数驱动 client（不感知提供方细节）。
"""
import base64
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  sys.path 装配
import _tts as T
import pipeline as P


def _audio_payload(data=b"RIFFfake"):
    return {"choices": [{"message": {"audio": {
        "data": base64.b64encode(data).decode()}}}]}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class MimoRequestShape(unittest.TestCase):
    def _capture(self, payload, text="你好。", voice_id="茉莉",
                 voice_style=None, model="m", timeout=30):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        client = T.MimoTtsClient("k", "https://x.example/v1/")
        with mock.patch.object(T.urllib.request, "urlopen", fake_urlopen):
            audio = client.synthesize(text, voice_id, voice_style, model, timeout)
        return captured, audio

    def test_body_matches_openai_chat_completions(self):
        captured, audio = self._capture(_audio_payload())
        self.assertEqual(captured["url"], "https://x.example/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k")
        body = captured["body"]
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["audio"], {"format": "wav", "voice": "茉莉"})
        # 正文挂 assistant 预填充；无 voice_style 时只有一条消息
        self.assertEqual(body["messages"], [{"role": "assistant", "content": "你好。"}])
        self.assertEqual(captured["timeout"], 30)
        self.assertEqual(audio, b"RIFFfake")

    def test_voice_style_prepended_as_user_message(self):
        captured, _ = self._capture(_audio_payload(), voice_style="轻松一些")
        msgs = captured["body"]["messages"]
        self.assertEqual(msgs[0], {"role": "user", "content": "轻松一些"})
        self.assertEqual(msgs[1]["role"], "assistant")

    def test_no_voice_omits_voice_param(self):
        captured, _ = self._capture(_audio_payload(), voice_id=None)
        self.assertEqual(captured["body"]["audio"], {"format": "wav"})

    def test_response_without_audio_is_deterministic_failure(self):
        client = T.MimoTtsClient("k", "https://x.example/v1")
        with mock.patch.object(T.urllib.request, "urlopen",
                               lambda req, timeout=None: FakeResponse(
                                   json.dumps({"choices": [{"message": {"content": "纯文本"}}]}).encode())):
            with self.assertRaises(T.BadAudioResponseError) as cm:
                client.synthesize("你好。", "茉莉", None, "m", 30)
        self.assertTrue(T.is_non_retryable(cm.exception))

    def test_malformed_payloads_are_deterministic_failures(self):
        """畸形 body 必须收敛成 BadAudioResponseError，不能漏成裸异常。

        裸的 AttributeError/TypeError/binascii.Error 不带 status_code，
        is_non_retryable 会放它进重试队列——同一个错配的端点重试 3 次结果
        一模一样，白烧 3 次计费调用。逐个 case 钉住这里的类型防线。
        """
        cases = {
            "顶层是数组": ["a", "b"],
            "顶层是字符串": "plain text",
            "顶层是数字": 42,
            "choices 是字符串": {"choices": "x"},
            "choices[0] 是字符串": {"choices": ["hello"]},
            "choices[0] 是 null": {"choices": [None]},
            "data 是整数": {"choices": [{"message": {"audio": {"data": 123}}}]},
            "data 不是 base64": {"choices": [{"message": {"audio": {"data": "@@not-b64@@"}}}]},
            "data 是列表": {"choices": [{"message": {"audio": {"data": ["a"]}}}]},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(T.BadAudioResponseError) as cm:
                    T._audio_from_response(payload)
                self.assertTrue(T.is_non_retryable(cm.exception),
                                f"{name} 应判为确定性失败（不该进重试队列）")

    def test_valid_payload_still_decodes(self):
        """类型防线不能误伤正常响应。"""
        self.assertEqual(T._audio_from_response(_audio_payload(b"RIFFx")), b"RIFFx")
        # 空 choices 是"端点返回了空结果"，同样没有音频
        with self.assertRaises(T.BadAudioResponseError):
            T._audio_from_response({"choices": []})
        with self.assertRaises(T.BadAudioResponseError):
            T._audio_from_response({})


class RetryVerdict(unittest.TestCase):
    def test_status_code_table(self):
        for code in (400, 401, 403, 404, 422):
            self.assertTrue(T.is_non_retryable(T.TtsHttpError(code, "x")), code)
        for code in (429, 500, 503):
            self.assertFalse(T.is_non_retryable(T.TtsHttpError(code, "x")), code)

    def test_network_errors_are_retryable(self):
        self.assertFalse(T.is_non_retryable(ConnectionResetError("boom")))
        self.assertFalse(T.is_non_retryable(ValueError("no status")))


class SynthSentenceThroughProtocol(unittest.TestCase):
    """synth_sentence 只传领域参数给 client.synthesize，落盘/重试/失败清理归管线。"""

    class RecordingClient:
        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = []

        def synthesize(self, text, voice_id, voice_style, model, timeout):
            self.calls.append((text, voice_id, voice_style, model, timeout))
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    def _run(self, outcomes, speed=1.0, max_retries=3):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "s001.wav")
            client = self.RecordingClient(outcomes)
            with mock.patch.object(P.time, "sleep"):
                ok, applied = P.synth_sentence(
                    client, "你好。", "茉莉", None, out,
                    ffmpeg_path="ffmpeg", speed=speed, model="m",
                    api_timeout=30, max_retries=max_retries)
            content = None
            if os.path.exists(out):
                with open(out, "rb") as f:
                    content = f.read()
        return ok, applied, client, content

    def test_success_writes_audio_and_passes_domain_args(self):
        ok, applied, client, content = self._run([b"RIFFfake"])
        self.assertEqual((ok, applied), (True, True))
        self.assertEqual(client.calls, [("你好。", "茉莉", None, "m", 30)])
        self.assertEqual(content, b"RIFFfake")

    def test_non_retryable_fails_first_attempt_and_leaves_no_file(self):
        ok, applied, client, content = self._run([T.TtsHttpError(401, "bad key")])
        self.assertEqual((ok, applied), (False, False))
        self.assertEqual(len(client.calls), 1)
        self.assertIsNone(content)

    def test_retryable_exhausts_retries_then_gives_up(self):
        ok, applied, client, content = self._run(
            [ConnectionResetError("x")] * 3 + [], max_retries=3)
        self.assertFalse(ok)
        self.assertEqual(len(client.calls), 3)
        self.assertIsNone(content)

    def test_last_failure_logged_as_giveup_not_retry(self):
        """最后一次失败要说"放弃"，不能还打 retry 3/3。

        打 retry 会让人以为后面还排着一次重试，排障时去找并不存在的第 4 次，
        真正的原因（已经重试到头了）被这条误导盖住。
        """
        import io as _io
        import contextlib as _cl
        buf = _io.StringIO()
        with _cl.redirect_stdout(buf):
            ok, _, client, _ = self._run([ConnectionResetError("boom")] * 3,
                                        max_retries=3)
        self.assertFalse(ok)
        log = buf.getvalue()
        self.assertIn("[retry 1/3]", log)
        self.assertIn("[retry 2/3]", log)
        self.assertIn("[giveup]", log)
        self.assertNotIn("[retry 3/3]", log,
                         "最后一次失败仍打 retry N/N，会让人以为还有下一次")


if __name__ == "__main__":
    unittest.main()
