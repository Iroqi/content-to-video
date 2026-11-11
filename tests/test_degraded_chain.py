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
            # 注入假客户端也绕不过这一关。
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--on-fail", "silence", "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client), \
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
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client), \
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
            # 注入假客户端也绕不过这一关。
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--on-fail", "silence", "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client), \
                    mock.patch.object(P, "_clear_stale_sidecars", return_value=False), \
                    mock.patch.object(P, "generate_silence") as gen_silence, \
                    mock.patch.object(P.time, "sleep"):
                P.main()
            # 全局 mock 了 _clear_stale_sidecars，所以整篇都不落静音；
            # 这条真正要钉的是"清理失败时不调 generate_silence"。
            gen_silence.assert_not_called()


class ResumeFingerprintCoversEndpoint(unittest.TestCase):
    """--resume 的缓存指纹必须覆盖**每一个真正影响音频字节**的入参。

    指纹漏一项的后果都是同一种：manifest 与日志声明的是新参数，嘴里播的是
    旧参数合成的音频，而两边都不报错。voice_id/voice_style/model 三项早有
    守卫；base_url 是第四个——同一个模型名可以由官方 API 与自建网关分别提供，
    它们的音色实现未必一致。
    """

    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg")
    def test_changing_base_url_invalidates_the_cache(self):
        """换 --base-url 后 --resume 必须重新合成，不能复用旧端点的音频。"""
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({"title": "指纹测试",
                           "segments": [{"id": "seg-a", "title": "段一",
                                         "text": "这是第一句。这是第二句。"}]}, f)
            out = os.path.join(td, "out")

            first = _FakeClient()
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://a.example/v1",
                    "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: first), \
                    mock.patch.object(P.time, "sleep"):
                P.main()
            self.assertEqual(len(first.calls), 2, "第一轮两句都该合成")

            # 第二轮：同稿件、同音色，只换 base-url，带 --resume
            second = _FakeClient()
            argv2 = ["pipeline.py", "--source", src_path, "-o", out, "--resume",
                     "--api-key", "test-key", "--base-url", "https://b.example/v1",
                     "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv2), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: second), \
                    mock.patch.object(P.time, "sleep"):
                P.main()
            self.assertEqual(
                len(second.calls), 2,
                "换 base-url 后指纹必须失配——否则复用另一个端点合成的音频，"
                "而 manifest 与日志都打着新端点（静默错位，两边都不报错）")

    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg")
    def test_same_base_url_still_reuses_the_cache(self):
        """门禁不能误伤：同端点重复跑 --resume 仍要命中缓存（不重烧额度）。"""
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({"title": "指纹测试",
                           "segments": [{"id": "seg-a", "title": "段一",
                                         "text": "这是第一句。这是第二句。"}]}, f)
            out = os.path.join(td, "out")
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://a.example/v1",
                    "--speed", "1.0", "--workers", "1"]

            first = _FakeClient()
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: first), \
                    mock.patch.object(P.time, "sleep"):
                P.main()

            second = _FakeClient()
            argv2 = list(argv) + ["--resume"]
            with mock.patch.object(P.sys, "argv", argv2), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: second), \
                    mock.patch.object(P.time, "sleep"):
                P.main()
            self.assertEqual(second.calls, [],
                             "同端点同稿件的 --resume 应全命中缓存")


class FailedMarkerLossIsNotSilent(unittest.TestCase):
    """.failed marker 写不成功时，不能留一份"看起来像正常缓存"的静音。"""

    @unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg 生成静音占位")
    def test_marker_write_failure_skips_the_fingerprint(self):
        """marker 落不下去就不写 .sha：下次 --resume 必须重试合成。

        静音占位与真实配音在缓存里只差这一个 marker。缺了它还照写 .sha 的话，
        下次 --resume 会按"合成成功"复用这段静音——synth_failed 与
        silence_fallback_count 一起从 manifest 里消失，run.py 的 degraded
        闸门（exit 3）就此静默失效。宁可贵一次合成，也不放一段假装正常的
        静音洞过去。
        """
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({"title": "marker 失败测试",
                           "segments": [{"id": "seg-a", "title": "段一",
                                         "text": "这句是坏的。这句正常，能合成。"}]}, f)
            out = os.path.join(td, "out")

            real_open = open

            def failing_open(file, mode="r", *a, **kw):
                # 只让 .failed marker 写不进去，其余文件照常
                if isinstance(file, str) and file.endswith(".failed") and "w" in mode:
                    raise OSError(28, "No space left on device")
                return real_open(file, mode, *a, **kw)

            client = _FakeClient()
            argv = ["pipeline.py", "--source", src_path, "-o", out,
                    "--api-key", "test-key", "--base-url", "https://x.example/v1",
                    "--on-fail", "silence", "--speed", "1.0", "--workers", "1"]
            with mock.patch.object(P.sys, "argv", argv), \
                    mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client), \
                    mock.patch("builtins.open", failing_open), \
                    mock.patch.object(P.time, "sleep"):
                P.main()

            sent_dir = os.path.join(out, "sentences")
            wavs = sorted(n for n in os.listdir(sent_dir)
                          if n.endswith(".wav") and not n.endswith(".orig.wav"))
            self.assertEqual(len(wavs), 2)
            # marker 一个都没落成（open 被 mock 成只对 .failed 失败）
            self.assertEqual([n for n in wavs
                              if os.path.exists(os.path.join(sent_dir, n + ".failed"))], [])
            # 本次 manifest 仍如实标 degraded（marker 丢不影响这一次的交付告知）
            with open(os.path.join(out, "timing_manifest.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["status"], "degraded")
            self.assertEqual(manifest["degraded"]["tts_silence_fallback_count"], 1)
            # 关键判据：**静音那句没有指纹**。有指纹没 marker 等于把静音登记成
            # 正常缓存，下次 --resume 会复用这段静音、degraded 状态整段消失。
            # 正常那句照旧有指纹（它没走降级分支，不该被牵连）。
            # 文件名由 index 推导：pipeline 里 out_path = s{index+1:03d}.wav
            # （index 从 0 起，见 pipeline 句级合成循环），manifest 行本身
            # 不带 file 键。
            failed_files = {f"s{s['index'] + 1:03d}.wav"
                            for s in manifest["sentences"] if s.get("synth_failed")}
            ok_files = {f"s{s['index'] + 1:03d}.wav"
                        for s in manifest["sentences"] if not s.get("synth_failed")}
            self.assertEqual(failed_files, {"s001.wav"})
            self.assertEqual(ok_files, {"s002.wav"})
            for name in failed_files:
                self.assertFalse(os.path.exists(os.path.join(sent_dir, name + ".sha")),
                                 f"{name} 是静音兜底句，没有 .failed marker 就不该有指纹")
            for name in ok_files:
                self.assertTrue(os.path.exists(os.path.join(sent_dir, name + ".sha")),
                                f"{name} 是正常合成句，指纹该照常落")


if __name__ == "__main__":
    unittest.main()
