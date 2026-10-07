"""concat_audio 的两条路径：同格式走标准库串接（ffmpeg_path=None 传进来就不能被用到），
混采样率/混声道先经 ffmpeg 归一再串接。"""
import os
import shutil
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


class MixedSampleRates(unittest.TestCase):
    """混采样率/混声道必须先归一再串接——这段代码修过一个真实的静默损坏。

    注释里记着那个坑：44100 与 24000 直接拼，wave 头写的是 44100，短边的
    字节按 44100 解读出来只有原长的 0.545 倍——产物能播、时长短一大截，
    全流程零报错。所以这里用真 ffmpeg 跑一遍，钉住"归一后的总帧数对得上"。
    没有 ffmpeg 的环境整类 skip，不假装通过。
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def p(self, name):
        return os.path.join(self.dir.name, name)

    def _duration(self, path):
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg 做格式归一")
    def test_mixed_rates_normalized_to_majority(self):
        # 2 句 1.0s：多数派是 24000/单声道，那句 44100/立体声要被归一到它
        a, b, out = (self.p(n) for n in ("a.wav", "b.wav", "combined.wav"))
        _mk(a, 24000, 1, 24000)
        _mk(b, 44100, 2, 44100)
        self.assertTrue(concat_audio("ffmpeg", [a, b], 0.5, out))
        with wave.open(out, "rb") as w:
            self.assertEqual(w.getframerate(), 24000)
            self.assertEqual(w.getnchannels(), 1)
        # 1.0 + 0.5(gap) + 1.0 = 2.5s。归一错的话这句会缩到 0.545s，
        # 断言的是这个总长——正是当初静默损坏的那个量。
        self.assertAlmostEqual(self._duration(out), 2.5, delta=0.05)

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg 做格式归一")
    def test_normalized_temp_files_are_cleaned_up(self):
        """归一化中间产物不能留在输出目录：它们是 _concat_norm*.wav 残渣。"""
        a, b, out = (self.p(n) for n in ("a.wav", "b.wav", "combined.wav"))
        _mk(a, 24000, 1, 12000)
        _mk(b, 44100, 2, 44100)
        self.assertTrue(concat_audio("ffmpeg", [a, b], 0.2, out))
        leftovers = [n for n in os.listdir(self.dir.name)
                     if n.startswith("_concat_norm")]
        self.assertEqual(leftovers, [], f"归一中间产物没清掉：{leftovers}")

    @unittest.skipUnless(shutil.which("ffmpeg"), "需要 ffmpeg 做格式归一")
    def test_normalization_failure_is_not_silent(self):
        """输入坏掉时归一必须失败并返回 False，不能拼出一份半截产物。"""
        a, b, out = (self.p(n) for n in ("a.wav", "b.wav", "combined.wav"))
        _mk(a, 24000, 1, 12000)
        with open(b, "wb") as f:
            f.write(b"RIFFnot-a-real-wav-file")
        self.assertFalse(concat_audio("ffmpeg", [a, b], 0.2, out))
        self.assertFalse(os.path.exists(out),
                         "归一失败却留下了 combined.wav——会被当成有效成片缓存")


if __name__ == "__main__":
    unittest.main()
