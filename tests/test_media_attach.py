"""预渲染视频素材接入的契约：条目形状、边界拒绝、合并写盘与 CLI 出口。

B 档三类体裁（三维 / 着色器 / 网页导览）共用这一条路径。这里的判据不是
「工具能拼出 JSON」，而是它不能成为绕开 `validate_images_json` 的后门：
字段名拼错一半、NaN 写进 JSON、把挂着 animate 的段静默换成视频，这三种
都是生成期零报错、渲染时才看不出东西的静默退化。
"""
import contextlib
import io
import json
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import attach_media as M
from _images_schema import validate_images_json


class EntryContract(unittest.TestCase):
    def test_default_entry_passes_contract_and_stays_minimal(self):
        """默认条目必须过契约层，且只写真正生效的键（不代写 animate / type）。"""
        entry = M.build_video_entry("images/tour.mp4")
        self.assertEqual(entry, {"src": "images/tour.mp4", "muted": True})
        self.assertEqual(validate_images_json({"seg2": entry})["seg2"]["src"],
                         "images/tour.mp4")
        self.assertNotIn("animate", entry)
        self.assertNotIn("type", entry)

    def test_rejects_non_video_and_escaping_paths(self):
        """非视频扩展名与逃出项目根的路径都在写盘之前被拒。"""
        for src in ("images/tour.png", "images/tour.svg", "/tmp/tour.mp4",
                    "../tour.mp4", "C:/tmp/tour.mp4"):
            with self.subTest(src=src):
                with self.assertRaises(ValueError):
                    M.build_video_entry(src)

    def test_rejects_non_finite_numbers_and_loose_types(self):
        """NaN/超大整数不进 JSON，布尔与未知旋钮不接受「差不多」的写法。"""
        for val in (float("nan"), float("inf"), 10 ** 400):
            with self.subTest(media_start=val):
                with self.assertRaises(ValueError):
                    M.build_video_entry("images/tour.mp4", media_start=val)
        with self.assertRaises(ValueError):
            M.build_video_entry("images/tour.mp4", loop="true")
        with self.assertRaises(ValueError):
            M.build_video_entry("images/tour.mp4", anchor=0)


class AttachContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "images.json")

    def test_merge_preserves_other_segments_and_refuses_silent_overwrite(self):
        """合并只改目标段；同段重复接入必须显式 --force，不能悄悄换素材。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"seg1": {"src": "images/cover.png"}}, f)
        entry = M.build_video_entry("images/tour.mp4", loop=True, media_start=0.4)
        merged = M.attach(self.path, "seg2", entry)
        self.assertEqual(merged["seg1"], {"src": "images/cover.png"})
        self.assertEqual(merged["seg2"]["media_start"], 0.4)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), merged)
        with self.assertRaises(ValueError) as ctx:
            M.attach(self.path, "seg2", entry)
        self.assertIn("--force", str(ctx.exception))
        M.attach(self.path, "seg2", M.build_video_entry("images/other.webm"),
                 force=True)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["seg2"]["src"], "images/other.webm")

    def test_refuses_to_replace_an_animated_segment(self):
        """原本挂 animate 的段一律不换成视频：标注会静默消失，force 也不放行。"""
        animated = {"src": "images/chart.svg", "animate": {
            "steps": [{"at": 0, "target": "#node-a", "set": {"opacity": 1}}]}}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"seg2": animated}, f)
        with open(self.path, encoding="utf-8") as f:
            validate_images_json(json.load(f))  # 证伪：这份编排本身是合法的
        with self.assertRaises(ValueError) as ctx:
            M.attach(self.path, "seg2",
                     M.build_video_entry("images/tour.mp4"), force=True)
        self.assertIn("animate", str(ctx.exception))
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["seg2"], animated)

    def test_cli_help_and_dry_run_do_not_write(self):
        """--help 是正经出口；--dry-run 只预览，落盘那次才写合规 JSON。"""
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                M.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        argv = ["--images", self.path, "--segment", "seg2",
                "--src", "images/tour.mp4", "--media-start", "0.4",
                "--loop", "--dry-run"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(argv), 0)
        self.assertFalse(os.path.isfile(self.path))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(argv[:-1]), 0)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(data["seg2"]["loop"])
        self.assertEqual(data["seg2"]["media_start"], 0.4)
        validate_images_json(data)


if __name__ == "__main__":
    unittest.main()
