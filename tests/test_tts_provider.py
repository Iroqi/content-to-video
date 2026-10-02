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
            content = (open(out, "rb").read()
                       if os.path.exists(out) else None)
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


if __name__ == "__main__":
    unittest.main()
