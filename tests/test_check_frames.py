"""check_frames：成片抽帧断言的回归（用 ffmpeg 合成 64×64 样片，秒级完成）。

契约要点：
- 黑屏帧（非黑占比不足）→ 失败；
- 死平帧（颜色桶不足）→ 失败；
- 有内容的样片 → 通过；
- ffmpeg/时长量不到 → 返回 (None, …) 降级，不冒充"画面失败"；
- CLI 退出码：0 通过 / 1 有失败 / 2 用法或读取错误。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import check_frames as CF  # noqa: E402


def _synth(name, lavfi_source):
    """ffmpeg 合成一段确定性样片，返回路径（容器 mp4 / yuv420p，兼容 h264）。

    lavfi_source 传完整源串（旧版 ffmpeg 的 lavfi 图解析：第一个选项跟 `=`，
    后续选项跟 `:`，两种拼法都会把 `=size` 或 `:s=` 误当过滤器名）。
    """
    p = os.path.join(_TMP.name, name)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", lavfi_source,
         "-pix_fmt", "yuv420p", "-an", p],
        capture_output=True, check=True, timeout=60)
    return p


_TMP = tempfile.TemporaryDirectory(prefix="check_frames_test_")


class FrameAssertion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.black = _synth("black.mp4", "color=c=black:size=64x64:duration=1.5")
            cls.solid = _synth("solid.mp4", "color=c=green:size=64x64:duration=1.5")
            cls.lively = _synth("lively.mp4", "testsrc2=size=64x64:duration=1.5")
        except (OSError, subprocess.CalledProcessError) as e:
            raise unittest.SkipTest(f"ffmpeg 不可用，跳过抽帧断言测试：{e}")

    def test_black_frame_fails_nonblack(self):
        fails, _ = CF.check_video(self.black, timestamps=[0.0])
        self.assertTrue(any("非黑" in f for f in fails), fails)

    def test_solid_frame_fails_color_buckets(self):
        # 绿底非黑占比够，但颜色桶只有 1 个：死平帧要暴露
        fails, _ = CF.check_video(self.solid, timestamps=[0.0], min_colors=20)
        self.assertTrue(any("颜色桶" in f for f in fails), fails)

    def test_lively_frame_passes(self):
        fails, report = CF.check_video(self.lively, timestamps=[0.0])
        self.assertEqual(fails, [], report)

    def test_first_frame_no_words_is_expected_when_pixels_pass(self):
        """P2：t=0 恰是开场擦除转场帧，画面里没有文字是预期。

        像素断言已过（非黑/颜色桶达标）时，首帧无词**豁免** OCR 判定——否则
        --check-frames-ocr 对默认开场形态必然失败，拦掉干净的成片。
        """
        with mock.patch.object(CF, "_ocr_text", return_value="  "):
            fails, report = CF.check_video(self.lively, timestamps=[0.0], ocr=True)
        self.assertEqual(fails, [], report)
        self.assertTrue(any("首帧转场帧" in l for l in report), report)

    def test_nonfirst_frame_without_words_still_fails(self):
        """P2 边界：豁免只给转场那一帧。t>0 无词（字幕层真丢）照常失败。"""
        with mock.patch.object(CF, "_ocr_text", return_value=""):
            fails, report = CF.check_video(self.lively, timestamps=[0.5], ocr=True)
        self.assertTrue(any("OCR 认不出任何词" in f for f in fails), fails)

    def test_ffmpeg_missing_degrades_not_fails(self):
        orig = CF._get_ffmpeg
        CF._get_ffmpeg = lambda: None
        try:
            fails, report = CF.check_video(self.lively)
            self.assertIsNone(fails)
            self.assertTrue(any("跳过" in l for l in report))
        finally:
            CF._get_ffmpeg = orig

    def test_missing_file_exit_2(self):
        self.assertEqual(CF.main(["no_such_file.mp4"]), 2)

    def test_bad_timestamps_exit_2(self):
        self.assertEqual(CF.main([self.black, "--timestamps", "abc"]), 2)

    def test_cli_black_exit_1(self):
        self.assertEqual(CF.main([self.black, "--timestamps", "0"]), 1)

    def test_cli_lively_exit_0(self):
        self.assertEqual(CF.main([self.lively, "--timestamps", "0", "--quiet"]), 0)


class Sampling(unittest.TestCase):
    """抽帧时刻的选取：上限命中后必须仍然覆盖整片。

    早先是"每 every 秒一帧、加满 40 个就停"，于是 600s 的片子只抽到 117s
    （覆盖 19.5%），后面八成从未被看过一眼，而报告照打"通过"。上限只能放宽
    **粒度**，不能缩短**范围**——这两条各有一条测试盯着。
    """

    @classmethod
    def setUpClass(cls):
        try:
            # 6s 样片：够长到能触发"every 装不下上限"，又只有几 KB
            cls.clip = _synth("sampling.mp4", "testsrc2=size=64x64:duration=6")
        except (OSError, subprocess.CalledProcessError) as e:
            raise unittest.SkipTest(f"ffmpeg 不可用，跳过采样测试：{e}")

    def test_long_film_still_reaches_the_end(self):
        ts, relaxed = CF.sample_timestamps(600.0, every=3.0)
        self.assertTrue(relaxed)
        self.assertGreater(max(ts), 0.9 * 600.0)
        self.assertLess(max(ts), 600.0)

    def test_short_film_keeps_requested_cadence(self):
        ts, relaxed = CF.sample_timestamps(37.5, every=3.0)
        self.assertFalse(relaxed)
        self.assertEqual(ts[:4], [0.0, 3.0, 6.0, 9.0])
        self.assertLess(max(ts), 37.5)

    def test_cap_is_a_cost_cap_not_a_range_cap(self):
        ts, relaxed = CF.sample_timestamps(10_000.0, every=1.0)
        self.assertTrue(relaxed)
        self.assertLessEqual(len(ts), CF._MAX_FRAMES)
        self.assertGreater(max(ts), 0.9 * 10_000.0)

    def test_first_frame_is_always_sampled(self):
        for dur in (0.5, 3.0, 37.5, 600.0):
            with self.subTest(dur=dur):
                self.assertEqual(CF.sample_timestamps(dur)[0][0], 0.0)

    def test_coverage_note_reaches_the_report(self):
        """被上限放宽过的那一次，必须把"只抽查了一部分"写进报告，别让"通过"
        冒充"整片都看过了"。"""
        fails, report = CF.check_video(self.clip, every=0.001)
        self.assertEqual(fails, [], report)
        self.assertTrue(any("coverage" in l for l in report), report)

    def test_short_film_report_has_no_coverage_note(self):
        _, report = CF.check_video(self.clip, every=3.0)
        self.assertFalse(any("coverage" in l for l in report), report)


class FrameMetrics(unittest.TestCase):
    """像素统计只走 ffmpeg + 标准库：PIL/numpy 不在本技能承诺的环境里。

    靠它们这道闸在承诺环境下必然空转（import 失败 → 每帧 None → "一帧都没
    量到" → 跳过，而退出码仍是 0）。所以这两条盯的是"换了实现之后判据有没有
    变"：纯色帧必须还是 1 个桶、黑帧必须还是 0% 非黑。
    """

    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise unittest.SkipTest(f"ffmpeg 不可用，跳过像素指标测试：{e}")

    @staticmethod
    def _frame(name, lavfi_source):
        """合成一段样片并抽出首帧 PNG，返回 PNG 路径（供 analyze_frame 直读）。"""
        mp4 = _synth(f"{name}.mp4", lavfi_source)
        png = os.path.join(_TMP.name, f"{name}_frame.png")
        subprocess.run(["ffmpeg", "-y", "-ss", "0", "-i", mp4,
                        "-frames:v", "1", "-q:v", "2", png],
                       capture_output=True, check=True, timeout=60)
        return png

    def test_flat_frame_is_one_bucket(self):
        """纯色帧（观感检查里最常见的"死平"形态）量化后必须只有一个桶。"""
        got = CF.analyze_frame(
            self._frame("solid", "color=c=green:size=64x64:duration=1"),
            ffmpeg_path="ffmpeg")
        self.assertIsNotNone(got)
        self.assertEqual(got["color_buckets"], 1)
        self.assertEqual(got["nonblack_ratio"], 1.0)

    def test_black_frame_is_zero_nonblack(self):
        got = CF.analyze_frame(
            self._frame("black", "color=c=black:size=64x64:duration=1"),
            ffmpeg_path="ffmpeg")
        self.assertIsNotNone(got)
        self.assertEqual(got["nonblack_ratio"], 0.0)
        self.assertEqual(got["color_buckets"], 1)

    def test_lively_frame_has_many_buckets(self):
        got = CF.analyze_frame(
            self._frame("lively", "testsrc2=size=64x64:duration=1"),
            ffmpeg_path="ffmpeg")
        self.assertIsNotNone(got)
        self.assertGreater(got["color_buckets"], CF._DEFAULT_MIN_COLORS)
        self.assertGreater(got["nonblack_ratio"], CF._DEFAULT_MIN_BRIGHT)

    def test_unreadable_frame_returns_none(self):
        """量不到必须返回 None（= "这一帧没人证明"），不能返回 0 冒充失败。"""
        self.assertIsNone(CF.analyze_frame(
            os.path.join(_TMP.name, "no_such.png"), ffmpeg_path="ffmpeg"))


if __name__ == "__main__":
    unittest.main()
