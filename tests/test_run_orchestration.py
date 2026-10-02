"""run.py 编排契约：进程内直调语义、闸门与制作报告。

pipeline / gen_hyperframes / render_wait 在这里都被换成假入口——本文件测
的是编排层本身：flag 透传、--until 边界、缺图/降级闸门、退出码传播、
production_report 落盘。真实 TTS 与渲染不在测试范围（那是各自模块的题）。
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

import _helpers as H
import _degraded as D
import run as R
from _script_utils import write_json_atomic


class Harness:
    """一个临时项目目录 + 三个假步骤。步骤只记录 argv / 落最小产物。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="ctv-run-")
        self.out = os.path.join(self.tmp, "audio_out")
        self.project = os.path.join(self.tmp, "hf-project")
        self.source = os.path.join(self.tmp, "segments_source.json")
        write_json_atomic(self.source, H.sample_source())
        self.manifest = H.make_manifest()
        self.tts_argv = None
        self.gen_argv = None
        self.render_calls = []
        self.last_out = self.last_err = ""

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_pipeline(self, argv):
        self.tts_argv = list(argv)
        if "--dry-run" in argv:  # 真 pipeline 的 dry-run 什么都不写
            return
        os.makedirs(self.out, exist_ok=True)
        write_json_atomic(os.path.join(self.out, "timing_manifest.json"),
                          self.manifest)

    def fake_gen(self, argv):
        self.gen_argv = list(argv)
        html = argv[argv.index("-o") + 1]
        os.makedirs(os.path.dirname(html), exist_ok=True)
        with open(html, "w", encoding="utf-8") as f:
            f.write("<html>fake</html>")

    def fake_render(self, cmd, out_path, cwd=None, max_wait=None):
        self.render_calls.append({"cmd": cmd, "out": out_path,
                                  "cwd": cwd, "max_wait": max_wait})
        with open(out_path, "wb") as f:
            f.write(b"x" * 100)

    def make_images_complete(self):
        """按 manifest 需要配图的段 id 落齐映射与文件。"""
        from _segments import sids_needing_image
        sids = sids_needing_image(self.manifest)
        os.makedirs(os.path.join(self.project, "images"), exist_ok=True)
        mapping = {}
        for sid in sids:
            with open(os.path.join(self.project, "images", f"{sid}.svg"),
                      "w", encoding="utf-8") as f:
                f.write("<svg/>")
            mapping[sid] = {"src": f"images/{sid}.svg"}
        os.makedirs(self.project, exist_ok=True)
        write_json_atomic(os.path.join(self.project, "images.json"), mapping)

    def invoke(self, extra_argv, pipeline_main=None):
        """跑一次 run.main()，返回 (stdout, stderr)；SystemExit 原样上抛。

        抛出路径上 last_out/last_err 也已写好（finally 在 redirect 生效期间
        取值），失败传播类测试才能断言到报错文本。
        """
        argv = ["run.py", "--source", self.source, "-o", self.out,
                "--project", self.project] + list(extra_argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(R.pipeline, "main",
                                  pipeline_main or self.fake_pipeline),
                mock.patch.object(R.gen_hyperframes, "main", self.fake_gen),
                mock.patch.object(R, "render_wait", self.fake_render),
                mock.patch.object(R, "hyperframes_command", lambda p: ["fakehf"]),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                stack.enter_context(p)
            try:
                R.main()
            finally:
                self.last_out, self.last_err = out.getvalue(), err.getvalue()
        return self.last_out, self.last_err

    def report(self):
        with open(os.path.join(self.out, "production_report.json"),
                  encoding="utf-8") as f:
            return json.load(f)


class FlagPassthrough(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)

    def test_defaults_always_sent(self):
        self.h.invoke(["--until", "tts"])
        self.assertIn("--resume", self.h.tts_argv)
        self.assertIn("--speed", self.h.tts_argv)
        self.assertIn("--voice-id", self.h.tts_argv)
        self.assertIn("--on-fail", self.h.tts_argv)
        # 显式给出才透传：不给由 pipeline 用自己的默认值
        for opt in ("--gap", "--bgm", "--bgm-volume", "--voice-style",
                    "--loudness"):
            self.assertNotIn(opt, self.h.tts_argv)

    def test_no_resume_omits_flag(self):
        self.h.invoke(["--until", "tts", "--no-resume"])
        self.assertNotIn("--resume", self.h.tts_argv)

    def test_optional_flags_forward(self):
        self.h.invoke(["--until", "tts", "--gap", "0.7", "--bgm", "b.mp3",
                       "--bgm-volume", "0.2", "--voice-style", "轻快",
                       "--loudness", "-16"])
        for pair in (("--gap", "0.7"), ("--bgm", "b.mp3"),
                     ("--bgm-volume", "0.2"), ("--voice-style", "轻快"),
                     # argparse type=float 后 str() 透传：-16 → "-16.0"
                     # （与拆分前的旧拼接命令同一形态，非本次改动）
                     ("--loudness", "-16.0")):
            self.assertIn(pair[0], self.h.tts_argv)
            self.assertEqual(self.h.tts_argv[self.h.tts_argv.index(pair[0]) + 1],
                             pair[1])

    def test_bgm_volume_without_bgm_rejected_before_tts(self):
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "tts", "--bgm-volume", "0.2"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIsNone(self.h.tts_argv, "校验必须先于 TTS 步骤")

    def test_nan_speed_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "tts", "--speed", "nan"])
        self.assertEqual(cm.exception.code, 2)
        self.assertFalse(os.path.isdir(self.h.out), "fail-fast 早于任何产物目录")

    def test_dry_run_nothing_written(self):
        self.h.invoke(["--dry-run"])
        self.assertIn("--dry-run", self.h.tts_argv)
        self.assertFalse(os.path.exists(
            os.path.join(self.h.out, "production_report.json")))

    def test_until_tts_writes_report(self):
        self.h.invoke(["--until", "tts"])
        rep = self.h.report()
        self.assertEqual([s["name"] for s in rep["steps"]], ["TTS"])
        self.assertTrue(rep["skipped"])
        self.assertEqual(rep["params"]["canvas"], "1080x1440")


class Gates(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)

    def test_missing_images_block_render_exit2(self):
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke([])
        self.assertEqual(cm.exception.code, 2)
        self.assertIsNone(self.h.gen_argv, "缺图必须拦在生成 HTML 之前")

    def test_until_images_exits_zero_before_gate(self):
        out, _ = self.h.invoke(["--until", "images"])
        self.assertIn("跳过的步骤", out)
        self.assertIsNone(self.h.gen_argv)

    def test_until_html_warns_but_generates(self):
        _, err = self.h.invoke(["--until", "html"])
        self.assertIn("还没有定稿配图", err)
        self.assertIn("继续生成 HTML", err)
        self.assertTrue(os.path.isfile(os.path.join(self.h.project, "index.html")))

    def test_missing_files_block_even_for_until_html(self):
        # images.json 有键但文件不在：映射指向坏路径，不是"图没画完"
        os.makedirs(self.h.project, exist_ok=True)
        write_json_atomic(os.path.join(self.h.project, "images.json"),
                          {"seg-a": {"src": "images/nope.svg"}})
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "html"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIsNone(self.h.gen_argv)

    def _degrade(self):
        self.h.make_images_complete()
        self.h.manifest["status"] = "degraded"
        self.h.manifest["degraded"] = {D.BGM_MIX_FAILED: True}

    def test_degraded_blocks_render_exit3(self):
        self._degrade()
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke([])
        self.assertEqual(cm.exception.code, 3)

    def test_degraded_allowed_until_html_marks_report(self):
        self._degrade()
        self.h.invoke(["--until", "html", "--allow-degraded"])
        rep = self.h.report()
        self.assertEqual(rep["degraded"][0]["type"], "bgm_mix_failed")


class FailurePropagation(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)

    def test_step_exit_code_propagates_and_report_records_failure(self):
        def boom(argv):
            raise SystemExit(7)
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "tts"], pipeline_main=boom)
        self.assertEqual(cm.exception.code, 7)
        rep = self.h.report()
        self.assertFalse(rep["steps"][0]["ok"])

    def test_string_systemexit_prints_message_and_becomes_1(self):
        def msg_exit(argv):
            raise SystemExit("人话报错")
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "tts"], pipeline_main=msg_exit)
        self.assertEqual(cm.exception.code, 1)
        # 子进程时代由解释器代打的 sys.exit("消息")，进程内由 _run_step 补打
        self.assertIn("人话报错", self.h.last_err)

    def test_broken_manifest_reported_once_in_human_words(self):
        # manifest 只在覆盖率统计那一处加载：坏文件在那里就报成人话，
        # 不再往下走。曾经这里 load+全量校验两遍，第二遍的错误分支已删。
        def write_broken(argv):
            os.makedirs(self.h.out, exist_ok=True)
            with open(os.path.join(self.h.out, "timing_manifest.json"), "w",
                      encoding="utf-8") as f:
                f.write('{"segments": [')     # 上次中断写入留下的截断 JSON
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "html"], pipeline_main=write_broken)
        # 进程内 code 就是那句人话（解释器代打时才映射成退出码 1）
        self.assertIn("timing_manifest.json 无法读取", str(cm.exception.code))


class HappyPath(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)

    def test_full_render_flow(self):
        self.h.make_images_complete()
        out, _ = self.h.invoke([])
        self.assertIn("完成", out)
        rep = self.h.report()
        self.assertEqual([s["name"] for s in rep["steps"]],
                         ["TTS", "生成 HTML", "渲染"])
        self.assertTrue(all(s["ok"] for s in rep["steps"]))
        call = self.h.render_calls[0]
        self.assertEqual(os.path.basename(call["out"]), "out.mp4")
        self.assertGreaterEqual(call["max_wait"], 1800.0)
        self.assertIn("fakehf", call["cmd"])


class ArchitecturePin(unittest.TestCase):
    """P1 决策固化：run.py 不再用自己的 subprocess 调自家脚本。"""

    def test_run_has_no_self_subprocess(self):
        src = H.read_text(os.path.join(H.SCRIPTS_DIR, "run.py"))
        self.assertNotIn("subprocess", src,
                         "run.py 只应编排进程内步骤；唯一的子进程在 _render_backend")
        self.assertNotIn("sys.executable", src,
                         "TTS/HTML 步骤参数列表里不该再出现解释器路径")
        self.assertIn("_run_step(pipeline.main", src)
        self.assertIn("_run_step(gen_hyperframes.main", src)


if __name__ == "__main__":
    unittest.main()
