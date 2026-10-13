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
    """颜色桶计数：np.unique 打包必须与逐像素建 set 严格等价。

    向量化是 perf 改动，但它改的是判据的实现——两边数出不同的桶数就会让"死平
    帧"这条断言悄悄变松或变紧，所以这条不是可选项。
    """

    def setUp(self):
        try:
            from PIL import Image  # noqa: F401
            import numpy as np  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"PIL/numpy 不可用：{e}")

    def _naive_buckets(self, arr):
        import numpy as np
        q = (arr // 8).astype(np.uint8)
        return len({(r[0], r[1], r[2]) for r in q.reshape(-1, 3)})

    def test_vectorized_bucket_count_matches_naive(self):
        import numpy as np
        from PIL import Image
        rng = np.random.default_rng(7)
        arr = rng.integers(0, 256, size=(37, 51, 3), dtype=np.uint16)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "f.png")
            Image.fromarray(arr.astype(np.uint8), "RGB").save(p)
            got = self._analyze(p)
        self.assertIsNotNone(got)
        self.assertEqual(got["color_buckets"], self._naive_buckets(arr))

    def test_flat_frame_is_still_one_bucket(self):
        """纯色帧（观感检查里最常见的"死平"形态）量化后必须只有一个桶。"""
        import numpy as np
        from PIL import Image
        arr = np.full((16, 16, 3), 200, dtype=np.uint16)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "f.png")
            Image.fromarray(arr.astype(np.uint8), "RGB").save(p)
            got = self._analyze(p)
        self.assertEqual(got["color_buckets"], 1)

    def _analyze(self, path):
        return CF.analyze_frame(path)


if __name__ == "__main__":
    unittest.main()
