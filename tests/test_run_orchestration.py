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

    def _drop_image_key(self, sid):
        """把某个段的配图映射从 images.json 里去掉（保留其余段）。"""
        self.h.make_images_complete()
        path = os.path.join(self.h.project, "images.json")
        with open(path, encoding="utf-8") as f:
            mapping = json.load(f)
        mapping.pop(sid, None)
        write_json_atomic(path, mapping)

    def test_until_html_warns_but_generates(self):
        # 只缺槽位段的图：那一页少了配图但仍能出 HTML 看版式，缺图降级成警告
        self._drop_image_key("seg-a")
        _, err = self.h.invoke(["--until", "html"])
        self.assertIn("还没有定稿配图", err)
        self.assertIn("继续生成 HTML", err)
        self.assertTrue(os.path.isfile(os.path.join(self.h.project, "index.html")))

    def test_until_html_canvas_missing_blocks(self):
        # 整页画布缺图：那一页没有标题层也没有句子流层，配图是唯一画面，
        # gen_hyperframes 在生成期必然拦下——run.py 不该先承诺"继续生成
        # HTML 供预览"再让下一步失败，所以这里同样 exit 2 并点名画布段。
        self._drop_image_key("seg-b")  # seg-b 是 layout:"canvas"
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "html"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("整页画布", self.h.last_err)
        self.assertIsNone(self.h.gen_argv, "画布段缺图不能进 HTML 步骤")

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
        # 进程内直调的行为凭证：假入口是 patch 在 pipeline.main /
        # gen_hyperframes.main 上的，argv 被记下来就说明 run.py 走的是模块属性
        # 而不是子进程（改成 shell 出去跑，这两个就还是 None）。
        self.assertIsNotNone(self.h.tts_argv)
        self.assertIsNotNone(self.h.gen_argv)
        call = self.h.render_calls[0]
        self.assertEqual(os.path.basename(call["out"]), "out.mp4")
        self.assertGreaterEqual(call["max_wait"], 1800.0)
        self.assertIn("fakehf", call["cmd"])


class OnlyPreview(unittest.TestCase):
    """--only 单段快渲：复用假 TTS 产物，走独立分支，正式产物一个都不碰。"""

    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        os.makedirs(os.path.join(self.h.out, "audio"), exist_ok=True)
        with open(os.path.join(self.h.out, "audio", "combined.wav"), "wb") as f:
            f.write(b"RIFF")
        self.h.make_images_complete()
        self.write_manifest(self.h.manifest)
        self.trimmed = []

    def write_manifest(self, manifest):
        write_json_atomic(os.path.join(self.h.out, "timing_manifest.json"),
                          manifest)

    def _trim(self, ffmpeg, src, dst, start, duration):
        self.trimmed.append({"src": src, "dst": dst, "start": start,
                             "duration": duration})
        with open(dst, "wb") as f:
            f.write(b"RIFF")

    def invoke(self, sid, extra=()):
        # ffmpeg 三件套全换掉：本文件测编排，切片与量时长是 _audio 自己的题。
        with mock.patch.object(R, "trim_audio", self._trim), \
             mock.patch.object(R, "get_ffmpeg", lambda: "fakeffmpeg"), \
             mock.patch.object(R, "measure_duration",
                               lambda *a: self.trimmed[-1]["duration"]):
            return self.h.invoke(["--only", sid] + list(extra))

    def _preview(self, name):
        return os.path.join(self.h.project, name)

    def test_tts_never_reruns_and_official_outputs_untouched(self):
        self.invoke("seg-a")
        self.assertIsNone(self.h.tts_argv, "--only 复用上次配音，不重跑 TTS")
        call = self.h.render_calls[0]
        self.assertEqual(os.path.basename(call["out"]), "preview_seg-a.mp4")
        # -c 必须紧跟 render：hyperframes 把第一个非选项参数当项目目录
        self.assertEqual(call["cmd"][call["cmd"].index("-c") + 1],
                         "preview_seg-a.html")
        self.assertFalse(os.path.exists(self._preview("index.html")))
        self.assertFalse(os.path.exists(self._preview("out.mp4")))
        self.assertFalse(os.path.exists(os.path.join(
            self.h.out, "production_report.json")), "预览不能覆盖正式生产报告")
        self.assertTrue(os.path.exists(self._preview("preview_seg-a.report.json")))

    def test_subset_manifest_and_images_feed_the_html_step(self):
        self.invoke("seg-a")
        sub_manifest = self._preview("preview_seg-a.manifest.json")
        self.assertEqual(self.h.gen_argv[self.h.gen_argv.index("-m") + 1],
                         sub_manifest)
        with open(sub_manifest, encoding="utf-8") as f:
            subset = json.load(f)
        self.assertEqual([s["id"] for s in subset["segments"]], ["seg-a"])
        self.assertEqual([s["start_time"] for s in subset["sentences"]],
                         [0.0, 2.4])
        with open(self._preview("preview_seg-a.images.json"),
                  encoding="utf-8") as f:
            self.assertEqual(list(json.load(f)), ["seg-a"])

    def test_window_cuts_from_next_speech_backwards(self):
        # seg-a 播到 6.8s，但这一页在片中的窗口一直留到 seg-b 开口（7.2s）
        self.invoke("seg-a")
        cut = self.trimmed[0]
        self.assertEqual((cut["start"], cut["duration"]), (2.4, 4.8))
        self.assertTrue(cut["src"].endswith(os.path.join("audio", "combined.wav")))

    def test_carry_chain_is_rendered_from_its_head(self):
        path = os.path.join(self.h.project, "images.json")
        with open(path, encoding="utf-8") as f:
            mapping = json.load(f)
        mapping["seg-b"]["stage"] = "keep"
        mapping["seg-b"]["src"] = mapping["seg-a"]["src"]
        write_json_atomic(path, mapping)
        out, _ = self.invoke("seg-b")
        self.assertIn("接续", out)
        self.assertEqual(self.trimmed[0]["start"], 2.4, "切片起点要退到链首")
        with open(self._preview("preview_seg-b.manifest.json"),
                  encoding="utf-8") as f:
            self.assertEqual([s["id"] for s in json.load(f)["segments"]],
                             ["seg-a", "seg-b"])

    def test_missing_tts_product_blocks(self):
        os.remove(os.path.join(self.h.out, "timing_manifest.json"))
        with self.assertRaises(SystemExit) as cm:
            self.invoke("seg-a")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("timing_manifest.json", self.h.last_err)

    def test_tts_flags_are_rejected_not_ignored(self):
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--only", "seg-a", "--speed", "1.2"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--speed", self.h.last_err)

    def test_dry_run_and_early_until_are_rejected(self):
        for extra in (["--dry-run"], ["--until", "tts"], ["--until", "images"]):
            with self.assertRaises(SystemExit):
                self.h.invoke(["--only", "seg-a"] + extra)

    def test_missing_image_blocks_even_with_until_html(self):
        path = os.path.join(self.h.project, "images.json")
        with open(path, encoding="utf-8") as f:
            mapping = json.load(f)
        mapping.pop("seg-a")
        write_json_atomic(path, mapping)
        with self.assertRaises(SystemExit) as cm:
            self.invoke("seg-a", ["--until", "html"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIsNone(self.h.gen_argv, "预览只服务定稿的页")

    def test_alpha_container_applies_to_the_preview_too(self):
        # 预览片同样要能出透明底：AE 里叠一版单页动画就靠这条
        _, _ = self.invoke("seg-a", ["--format", "mov", "--alpha"])
        call = self.h.render_calls[0]
        self.assertEqual(os.path.basename(call["out"]), "preview_seg-a.mov")
        self.assertEqual(call["cmd"][call["cmd"].index("--format") + 1], "mov")
        self.assertIn("--alpha", self.h.gen_argv)

    def test_degraded_product_warns_but_ships_the_preview(self):
        self.h.manifest["status"] = "degraded"
        self.h.manifest["degraded"] = {D.BGM_MIX_FAILED: True}
        self.write_manifest(self.h.manifest)
        _, err = self.invoke("seg-a", ["--until", "html"])
        self.assertIn("degraded", err)
        self.assertIsNotNone(self.h.gen_argv)
        with open(self._preview("preview_seg-a.report.json"),
                  encoding="utf-8") as f:
            self.assertEqual([d["type"] for d in json.load(f)["degraded"]],
                             [D.BGM_MIX_FAILED])


class FormatAndAlpha(unittest.TestCase):
    """--format / --alpha：容器透传 + 透明底的入参校验。

    透明本身由 hyperframes 与 CSS 保证（各自有题），这里只锁编排层的两件事：
    --alpha 只允许配 mov（实测另两个容器都拿不到 alpha 平面，交付一片黑底），
    以及两面旗各自落到哪一步。
    """

    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.h.make_images_complete()

    def test_alpha_with_mp4_is_rejected_before_any_step(self):
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--alpha"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("mov", self.h.last_err)
        self.assertIsNone(self.h.tts_argv, "入参校验必须早于 TTS")

    def test_alpha_with_webm_is_rejected_too(self):
        # 2026-10 实测：hyperframes 渲 webm 时不落 alpha 平面，透明处压成纯黑。
        # 拦下比让交付方自己发现黑底便宜。
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--format", "webm", "--alpha"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("丢平面", self.h.last_err)
        self.assertIsNone(self.h.tts_argv)

    def test_mov_plus_alpha_reaches_both_steps(self):
        self.h.invoke(["--format", "mov", "--alpha"])
        self.assertIn("--alpha", self.h.gen_argv)
        call = self.h.render_calls[0]
        self.assertEqual(os.path.basename(call["out"]), "out.mov")
        self.assertEqual(call["cmd"][call["cmd"].index("--format") + 1], "mov")
        rep = self.h.report()
        self.assertEqual(rep["params"]["format"], "mov")
        self.assertTrue(rep["params"]["alpha"])

    def test_default_flow_sends_neither_flag(self):
        self.h.invoke([])
        self.assertNotIn("--alpha", self.h.gen_argv)
        self.assertNotIn("--format", self.h.render_calls[0]["cmd"])

    def test_format_only_gives_opaque_video_in_that_container(self):
        # 只换容器不关底：--alpha 是画面开关，不是容器开关，两者互不隐含
        self.h.invoke(["--format", "webm"])
        self.assertNotIn("--alpha", self.h.gen_argv)


class ArchitecturePin(unittest.TestCase):
    """P1 决策固化：run.py 不再用自己的 subprocess 调自家脚本。"""

    def test_run_has_no_self_subprocess(self):
        src = H.read_text(os.path.join(H.SCRIPTS_DIR, "run.py"))
        self.assertNotIn("subprocess", src,
                         "run.py 只应编排进程内步骤；唯一的子进程在 _render_backend")
        self.assertNotIn("sys.executable", src,
                         "TTS/HTML 步骤参数列表里不该再出现解释器路径")


if __name__ == "__main__":
    unittest.main()
