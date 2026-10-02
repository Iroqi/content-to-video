"""concat_audio 的标准库串接路径：同格式拼接全程不碰 ffmpeg（ffmpeg_path=None 传进来了就不能被用到）。"""
import os
import tempfile
import unittest
import wave

import _helpers as H  # noqa: F401
from _audio import concat_audio


def _mk(path, rate, channels, frames):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * (frames * channels))


class Concat(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def p(self, name):
        return os.path.join(self.dir.name, name)

    def test_two_sentences_with_gap(self):
        a, b, out = (self.p(n) for n in ("a.wav", "b.wav", "combined.wav"))
        _mk(a, 24000, 1, 4800)
        _mk(b, 24000, 1, 7200)
        self.assertTrue(concat_audio(None, [a, b], 0.4, out))
        with wave.open(out, "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.getsampwidth(), 2)
            # gap 整帧精度：0.4s × 24000 = 9600，无量化漂移
            self.assertEqual(w.getnframes(), 4800 + 9600 + 7200)

    def test_no_gap_is_pure_sum(self):
        a, b, out = (self.p(n) for n in ("a.wav", "b.wav", "combined.wav"))
        _mk(a, 44100, 2, 1000)
        _mk(b, 44100, 2, 1500)
        self.assertTrue(concat_audio(None, [a, b], 0.0, out))
        with wave.open(out, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getframerate()), (2, 44100))
            self.assertEqual(w.getnframes(), 2500)

    def test_single_file_has_no_trailing_silence(self):
        a, out = self.p("a.wav"), self.p("combined.wav")
        _mk(a, 24000, 1, 3000)
        self.assertTrue(concat_audio(None, [a], 0.4, out))
        with wave.open(out, "rb") as w:
            self.assertEqual(w.getnframes(), 3000)

    def test_empty_list_fails(self):
        self.assertFalse(concat_audio(None, [], 0.4, self.p("combined.wav")))


if __name__ == "__main__":
    unittest.main()
