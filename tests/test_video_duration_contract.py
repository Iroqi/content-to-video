"""成片时长契约：渲染之后那道闸。

成片"能播"不等于"演完了"。抓帧超时重抓、worker 崩、Chrome 提前退出都能
产出一支容器完好、ffmpeg 全解码也过得去、却比时间轴短一截的片子——后几段
整段不在片里，而 run.py 照打"完成"。这道闸在渲染之后补上最后一句：成片
实测时长必须跟 manifest 的 total_duration 对得上。

本文件只测闸门本身（退出码、报告字段、降级路径），量具
`probe_video_duration` 的解析与容错在 tests/test_render_backend.py。
"""
import os
import unittest

from test_run_orchestration import Harness


class DurationGate(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.h.make_images_complete()
        self.expected = float(self.h.manifest["total_duration"])

    def test_matching_duration_passes(self):
        self.h.video_duration = self.expected
        out, err = self.h.invoke([])
        self.assertIn("完成", out)
        rep = self.h.report()
        self.assertEqual(rep["video"]["check"], "ok")
        self.assertAlmostEqual(rep["video"]["seconds"], self.expected, places=2)
        self.assertAlmostEqual(rep["video"]["expected_seconds"], self.expected, places=2)
        # 容差沿用全仓既有的 max(1s, 2%)（音频与 manifest 对账那一档）
        self.assertAlmostEqual(rep["video"]["tolerance_seconds"],
                               max(1.0, self.expected * 0.02), places=2)
        self.assertEqual(self.h.probe_calls,
                         [os.path.join(self.h.project, "out.mp4")])

    def test_within_tolerance_passes(self):
        # 帧量化（24fps 下末帧最多差 1/24 s）不该被当成截断
        self.h.video_duration = self.expected - 0.04
        self.h.invoke([])
        self.assertEqual(self.h.report()["video"]["check"], "ok")

    def test_short_video_blocks_delivery(self):
        # 少了整整一段的量：后段内容可能整个不在片里
        self.h.video_duration = self.expected - 5.0
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke([])
        self.assertEqual(cm.exception.code, 4,
                         "成片短于时间轴必须拦下，不能当成正常成片交付")
        err = self.h.last_err
        self.assertIn("成片比时间轴短", err)
        # 病因与去处要给人指路，而不是只扔一句失败
        self.assertIn("Parallel capture timed out", err)
        rep = self.h.report()
        self.assertEqual(rep["video"]["check"], "short")
        self.assertAlmostEqual(rep["video"]["seconds"], self.expected - 5.0, places=2)

    def test_short_video_keeps_the_file(self):
        # 拦下的是"交付"，不是"文件"：成片可能只是短了一点、人眼看一眼就
        # 能判断，删掉反而让人无从查证（与超时/中断的半截废片不同，那种
        # 才是容器都没写好的废片，由 render_wait 丢弃）。
        self.h.video_duration = self.expected - 5.0
        with self.assertRaises(SystemExit):
            self.h.invoke([])
        self.assertTrue(os.path.isfile(os.path.join(self.h.project, "out.mp4")))

    def test_long_video_only_warns(self):
        # 长出来的多半是末段音频尾巴或末帧余量，画面信息没丢——不拦
        self.h.video_duration = self.expected + 5.0
        out, err = self.h.invoke([])
        self.assertIn("完成", out)
        self.assertIn("成片比时间轴长", err)
        self.assertEqual(self.h.report()["video"]["check"], "long")

    def test_unmeasurable_degrades_to_warn(self):
        # ffmpeg 是可选依赖：量不到就明说"这条没人证明"，不阻断交付
        self.h.video_duration = None
        out, err = self.h.invoke([])
        self.assertIn("完成", out)
        self.assertIn("成片时长未校验", err)
        rep = self.h.report()
        self.assertEqual(rep["video"]["check"], "skipped")
        self.assertIsNone(rep["video"]["seconds"])

    def test_until_html_does_not_probe(self):
        # 迭代档不渲染，也就没有成片可量
        self.h.video_duration = self.expected
        self.h.invoke(["--until", "html"])
        self.assertEqual(self.h.probe_calls, [])
        self.assertNotIn("video", self.h.report())


if __name__ == "__main__":
    unittest.main()
