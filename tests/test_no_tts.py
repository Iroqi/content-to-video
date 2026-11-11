"""无配音档（`--no-tts`）的契约：预览不该被付费账户挡在门口。

这条路径存在的理由是"把稿子编译成 HTML 是纯本地 CPU 工作"。它一旦坏了，
坏法都很安静：预览页照出，只是每一句的时长都是错的、或者 manifest 自称
"干净"、或者补上 key 之后 `--resume` 把静音认成真音频。所以这里盯四件事：

1. **不查 key、不发请求**——没有 key 也要能跑完；
2. **产物自称 degraded**——不然降级闸门形同虚设；
3. **时长按字数估算**，句间 gap 与 start_time 累加与真合成同一条公式；
4. **边车清干净**——无配音档留下的 `.sha`/`.spd`/`.failed` 会让补 key 后
   的复跑误判缓存。

`has_tts_key()` 另有一条：它必须与 `main()` 的闸门读同一处合并结果——两个
人各写一套"从哪读 key"，就会出现"配了用户级 .env 却被判成没 key"。
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _degraded as D
import pipeline as P
from _script_utils import write_json_atomic


def _silence(_ffmpeg, _dur, path):
    """替身：不调 ffmpeg，只把文件造出来（预览页的 <audio> 只在乎文件在）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF....WAVE")


class NoTtsRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctv-nott-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "audio_out")
        self.source = os.path.join(self.tmp, "segments_source.json")
        write_json_atomic(self.source, H.sample_source())

    def _run(self, extra=()):
        argv = ["pipeline.py", "--source", self.source, "-o", self.out,
                "--gap", "0.4", "--no-tts"] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(P, "generate_silence", _silence), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            P.main()
        return out.getvalue(), err.getvalue()

    def _manifest(self):
        with open(os.path.join(self.out, "timing_manifest.json"),
                  encoding="utf-8") as f:
            return json.load(f)

    def test_runs_without_any_key(self):
        """没有任何 provider key 也必须跑通——这正是这条路径存在的理由。"""
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("MIMO_API_KEY", "CTV_TTS_PROVIDER")}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch.object(P, "load_env", lambda: {}):
            self._run()
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, "timing_manifest.json")))

    def test_never_calls_the_tts_api(self):
        """判别式替身：只要管线去造客户端，就等于它准备打网络。"""
        calls = []

        def factory(_key, _base):
            calls.append(1)
            return mock.Mock()

        with mock.patch.object(P, "TTS_CLIENT_FACTORY", factory):
            self._run()
        self.assertFalse(calls, "无配音档不许碰 TTS 端点（连客户端都不该造）")

    def test_manifest_declares_itself_degraded(self):
        self._run()
        tm = self._manifest()
        self.assertEqual(tm["status"], "degraded")
        self.assertTrue(tm["degraded"].get(D.UNVOICED_PREVIEW),
                        "没有 unvoiced_preview 这个键，降级闸门就拦不住它")
        self.assertTrue(all(s.get("synth_failed") for s in tm["sentences"]))

    def test_durations_are_estimated_from_text(self):
        """时长按字数/语速估算，且 start_time 与 gap 的累加口径与真合成一致。"""
        self._run()
        tm = self._manifest()
        gap = tm["gap"]
        # 真源就是 build_parts 给出的段表：开屏/结尾段的 1.2 语速是它写进去的
        from build_from_structured import build_parts
        _sentences, seg_config = build_parts(H.sample_source())
        cursor = 0.0
        for s in tm["sentences"]:
            self.assertAlmostEqual(s["start_time"], cursor, places=3)
            self.assertGreater(s["duration"], 0)
            # 全局默认语速也要从段表外那一份取：开屏/结尾段有自己的 1.2
            speed = P._sentence_speed_for(seg_config, s["index"], P.DEFAULT_SPEED)
            self.assertAlmostEqual(
                s["duration"],
                round(max(0.05, P.estimate_sentence_seconds(
                    s["text"], P.DEFAULT_CHARS_PER_SEC, speed)), 3),
                places=3)
            cursor += s["duration"] + gap
        self.assertAlmostEqual(tm["total_duration"],
                               round(cursor - gap, 3), places=3)

    def test_stale_sidecars_are_removed(self):
        """无配音档把 wav 换成了静音，边车必须一起清掉，否则补 key 后 --resume
        会把静音当成"同一段音频，指纹未变"，整片永远无声。"""
        sdir = os.path.join(self.out, "sentences")
        os.makedirs(sdir, exist_ok=True)
        for name in ("s001.wav.sha", "s001.wav.spd", "s001.wav" + P._FAILED_SUFFIX):
            with open(os.path.join(sdir, name), "w", encoding="utf-8") as f:
                f.write("stale")
        self._run()
        left = sorted(os.listdir(sdir))
        self.assertFalse([n for n in left if n.endswith((".sha", ".spd",
                                                          P._FAILED_SUFFIX))],
                         f"边车没清干净：{left}")

    def test_requires_a_source(self):
        """缺 --source 时由 argparse 的必填项统一拦下（exit 2，带用法）。"""
        argv = ["pipeline.py", "-o", self.out, "--no-tts"]
        err = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                P.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--source", err.getvalue())


class HasTtsKey(unittest.TestCase):
    """`has_tts_key()` 与 `main()` 的闸门必须读同一处合并结果。"""

    def test_reads_the_merged_env_not_just_os_environ(self):
        """用户级 .env 里有 key 时必须算"有"——只看 os.environ 会把
        "配了 .env 却被判成没 key"这种哑巴故障放回来。"""
        with mock.patch.object(P, "load_env",
                               lambda: {"MIMO_API_KEY": "user-level-key"}), \
                mock.patch.object(P, "make_client",
                                  lambda provider, key, base: object()):
            self.assertTrue(P.has_tts_key())

    def test_empty_key_is_absent(self):
        with mock.patch.object(P, "load_env", lambda: {}), \
                mock.patch.object(P, "make_client",
                                  lambda provider, key, base: object()):
            self.assertFalse(P.has_tts_key())

    def test_unknown_provider_is_not_a_key(self):
        """provider 名写错时 make_client 抛 ValueError——这里要答"没有 key"，
        不能把异常漏给 run.py（它只想知道能不能配音）。"""
        with mock.patch.object(P, "load_env",
                               lambda: {"MIMO_API_KEY": "k",
                                        "CTV_TTS_PROVIDER": "not-a-provider"}):
            self.assertFalse(P.has_tts_key())


if __name__ == "__main__":
    unittest.main()
