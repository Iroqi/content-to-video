"""降级链路的端到端契约：TTS 单句失败 → 静音兜底 → manifest 进 degraded。

为什么单独一条集成测试：降级是本技能唯一"允许交付缺内容"的路径，每个
环节（on-fail 分支、.failed marker、synth_failed 字段、_degraded 注册表、
run.py 的 exit 3 阻断）此前各有一条注册表一致性测试，但**没有一条把它们
串起来跑通**。链条中任何一环改坏，单独看每条测试都是绿的。

用假 client（不联网）+ 真 ffmpeg（静音占位要真生成 wav 并被量时长）。
没有 ffmpeg 的环境整类 skip，不假装通过。
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  sys.path 装配
import pipeline as P
import _tts as T


def _have_ffmpeg():
    return shutil.which("ffmpeg") is not None


class _FakeClient:
    """按句子文本决定成败：文本里带 "坏" 的确定性失败，其余成功。

    成功时返回真 WAV 头 + 一点静音，够 ffmpeg 量出时长。
    """
    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice_id, voice_style, model, timeout):
        self.calls.append(text)
        if "坏" in text:
            raise T.BadAudioResponseError("端点返回了纯文本")
        return _tiny_wav(0.5)


def _tiny_wav(seconds=0.5, rate=8000):
    import io
    import struct
    import wave
    n = int(rate * seconds)
    frames = b"".join(struct.pack("<h", 0) for _ in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def _source_with_one_bad_sentence():
    src = {
        "title": "降级链路测试",
        "segments": [
            {"id": "seg-a", "title": "第一段", "text": "这句正常。第二句也正常。"},
            {"id": "seg-b", "title": "第二段", "text": "这句是坏的，会失败。最后一句正常。"},
        ],
    }
    return src


class DegradedChainIntegration(unittest.TestCase):
    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg 生成静音占位")
    def test_silence_fallback_produces_valid_degraded_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(_source_with_one_bad_sentence(), f, ensure_ascii=False)
            out = os.path.join(td, "out")

            client = _FakeClient()
            # --api-key 显式给：key 校验发生在 client 构造之前，
            # mock 掉 MimoTtsClient 也绕不过这一关。
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--on-fail", "silence", "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "MimoTtsClient", return_value=client), \
                    mock.patch.object(P.time, "sleep"):
                P.main()

            manifest_path = os.path.join(out, "timing_manifest.json")
            self.assertTrue(os.path.exists(manifest_path),
                            "降级也要产出 manifest——这是 run.py 后续步骤的唯一输入")
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)

            # 1. 状态位：降级必须显式，不允许悄悄交付
            self.assertEqual(manifest["status"], "degraded")
            self.assertEqual(manifest["degraded"]["tts_silence_fallback_count"], 1)

            # 2. 失败句仍占住时间轴位置（这是 on-fail silence 的全部承诺）。
            # 句数从分句器取真值而不是写死：分句规则改了（逗号切句之类）
            # 这条不该假失败——它要钉的是"没有句子消失"，不是"有几句"。
            from build_from_structured import build_parts
            expected, _ = build_parts(_source_with_one_bad_sentence())
            self.assertEqual(len(manifest["sentences"]), len(expected))
            self.assertEqual([s["text"] for s in manifest["sentences"]], expected)
            failed = [s for s in manifest["sentences"] if s.get("synth_failed")]
            self.assertEqual(len(failed), 1)
            self.assertIn("坏的", failed[0]["text"])
            self.assertGreater(failed[0]["duration"], 0)

            # 3. 时间轴仍自洽：句子不重叠、终点不超过 total_duration
            total = manifest["total_duration"]
            for s in manifest["sentences"]:
                self.assertLessEqual(s["start_time"] + s["duration"],
                                     total + 0.25)

            # 4. 落盘的 .failed marker 让下次 --resume 不会把静音当正常缓存。
            #    句级产物在 out/sentences/ 下，不是 out/ 根。
            sent_dir = os.path.join(out, "sentences")
            self.assertTrue(os.path.isdir(sent_dir), f"缺句级目录 {sent_dir}")
            markers = [n for n in os.listdir(sent_dir) if n.endswith(".failed")]
            self.assertEqual(len(markers), 1, f"应有 1 个 .failed marker，实际 {markers}")

            # 5. 契约层接受这份 manifest（封闭键集、值域都对）
            from _manifest_schema import validate_timing_manifest
            validate_timing_manifest(manifest)

    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg")
    def test_abort_is_the_default_and_blocks_the_run(self):
        """默认 abort：任何一句失败就整条管线退出，不产出 manifest。"""
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump(_source_with_one_bad_sentence(), f, ensure_ascii=False)
            out = os.path.join(td, "out")
            client = _FakeClient()
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "MimoTtsClient", return_value=client), \
                    mock.patch.object(P.time, "sleep"):
                with self.assertRaises(SystemExit) as cm:
                    P.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertFalse(
                os.path.exists(os.path.join(out, "timing_manifest.json")),
                "abort 下不该留下 manifest——留下半份会让 run.py 误以为 TTS 过了")

    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg")
    def test_stale_sidecar_blocks_silence_fallback(self):
        """清理 .orig.wav 失败时不落静音：否则下次 --resume 会拿旧稿变速。

        失败模式很隐蔽：本次看起来正常（句在、时长对），但用户之后改 --speed
        时 _audio 会把残留的 .orig.wav 当原速源做 atempo，新字幕配上一稿的
        旧配音。所以这句必须走 failed 分支被如实报出。
        """
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({
                    "title": "残留 sidecar 测试",
                    "segments": [
                        {"id": "seg-a", "title": "段一", "text": "这句是坏的。"},
                        {"id": "seg-b", "title": "段二", "text": "这句正常，能合成。"},
                    ],
                }, f, ensure_ascii=False)
            out = os.path.join(td, "out")
            os.makedirs(out, exist_ok=True)
            # 预置一个删不掉的 .orig.wav
            locked = os.path.join(out, "s000.orig.wav")
            with open(locked, "wb") as f:
                f.write(b"RIFFstale")

            client = _FakeClient()
            # --api-key 显式给：key 校验发生在 client 构造之前，
            # mock 掉 MimoTtsClient 也绕不过这一关。
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--on-fail", "silence", "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "MimoTtsClient", return_value=client), \
                    mock.patch.object(P, "_clear_stale_sidecars", return_value=False), \
                    mock.patch.object(P, "generate_silence") as gen_silence, \
                    mock.patch.object(P.time, "sleep"):
                P.main()
            # 全局 mock 了 _clear_stale_sidecars，所以整篇都不落静音；
            # 这条真正要钉的是"清理失败时不调 generate_silence"。
            gen_silence.assert_not_called()


if __name__ == "__main__":
    unittest.main()
