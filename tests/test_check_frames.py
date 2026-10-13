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


if __name__ == "__main__":
    unittest.main()
