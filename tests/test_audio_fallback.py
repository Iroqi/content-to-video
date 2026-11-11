"""回落音轨的对账：回落到上次暂存的 combined.wav 之前，必须证明它属于这条时间轴。

`gen_hyperframes.main()` 在 manifest 的 combined_audio 指不到文件时会复用项目里
上次暂存的 audio/combined.wav。那是"上一次的配音"，未必是这一条稿子的——成片有声、
字幕也对，只是两者说的不是同一件事，交付前没有任何环节会再提一次。所以对账是唯一
的闸门，而这个闸门自己不能悄无声息地失效。
"""
import os
import tempfile
import unittest
import wave
from unittest import mock

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as G


def _write_wav(path, seconds, rate=24000):
    """一段静音 WAV：时长由帧数/帧率决定，是 measure_duration 认的精确路径。"""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(round(seconds * rate)))


class FallbackCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = os.path.join(self.tmp.name, "combined.wav")
        _write_wav(self.wav, 30.0)

    def test_matching_duration_passes(self):
        self.assertIsNone(G.audio_fallback_check(self.wav, 30.0))
        # 2% 容差内也放行：拼接与估算的尾差不该逼用户重跑 TTS
        self.assertIsNone(G.audio_fallback_check(self.wav, 30.4))

    def test_another_timelines_audio_is_refused(self):
        why = G.audio_fallback_check(self.wav, 90.0)
        self.assertIsNotNone(why)
        self.assertIn("另一条时间轴", why)
        self.assertIn("重跑 TTS", why)

    def test_unmeasurable_audio_is_refused_not_silently_accepted(self):
        """测不出时长 = 这次对账根本没发生，不能当成"对过了、一致"。

        证伪（改回 `if _want and _got` 之后实测）：ffmpeg 不在 PATH、或暂存目录里
        那个文件不是音频时，这里返回 None，旧配音配新字幕直接烧进成片，且交付前
        没有任何提示。
        """
        with mock.patch.object(G, "measure_duration", return_value=0.0):
            why = G.audio_fallback_check(self.wav, 30.0)
        self.assertIsNotNone(why)
        self.assertIn("测不出时长", why)
        self.assertIn("30.00s", why)

    def test_nothing_to_compare_against_passes(self):
        """manifest 没写总时长时无从对账——那条路径的提醒已经由 warn 打过。"""
        for want in (None, "", 0, 0.0):
            self.assertIsNone(G.audio_fallback_check(self.wav, want), repr(want))


if __name__ == "__main__":
    unittest.main()
