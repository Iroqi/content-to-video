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

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
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
        self.probe_calls = []
        self.last_out = self.last_err = ""
        # 假成片时长（秒）；None = 量不到（ffmpeg 不可用那条降级路径）。
        # 渲染之后 run.py 会拿它跟 manifest 的 total_duration 对账，测试靠
        # 这个注入点覆盖那道闸——不注入会去调真 ffmpeg（CI 上没有）。
        self.video_duration = None

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

    def fake_probe(self, out_path, ffmpeg_path=None):
        self.probe_calls.append(out_path)
        return self.video_duration

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
                mock.patch.object(R, "probe_video_duration", self.fake_probe),
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
        for opt in ("--gap", "--voice-style"):
            self.assertNotIn(opt, self.h.tts_argv)

    def test_optional_flags_forward(self):
        self.h.invoke(["--until", "tts", "--gap", "0.7", "--voice-style", "轻快"])
        for pair in (("--gap", "0.7"), ("--voice-style", "轻快")):
            self.assertIn(pair[0], self.h.tts_argv)
            self.assertEqual(self.h.tts_argv[self.h.tts_argv.index(pair[0]) + 1],
                             pair[1])

    def test_gap_help_default_matches_the_single_source(self):
        """--gap 的 help 不许自己抄一份默认值：DEFAULT_GAP 一改，help 就漂。

        证伪：原先这句写死"默认 0.4"，而真源是 _timeline.DEFAULT_GAP——隔壁 --speed
        用 f-string 引 DEFAULT_SPEED，这一条却是抄下来的字面量，改默认值时只有
        --speed 跟着变。
        """
        from _timeline import DEFAULT_GAP
        self.assertIn("默认 {:g}".format(DEFAULT_GAP), R._build_parser().format_help())

    def test_fps_default_has_one_source_across_three_entrypoints(self):
        """默认帧率只有一份：run --fps / gen --fps / generate_html(fps=)。

        证伪：三处曾各写一份字面量 24，改 _timeline.DEFAULT_FPS 只改到一处——
        HTML 的 data-fps、渲染命令的 --fps 与库调用方的默认互相漂移，而三者
        都不报错（fps 漂 1 帧看不出来，成片只是整体快/慢一点）。
        """
        import inspect
        from _timeline import DEFAULT_FPS
        from html_renderer import generate_html
        self.assertEqual(DEFAULT_FPS, 24)
        self.assertEqual(
            R._build_parser().parse_args(["--source", "s.json"]).fps, DEFAULT_FPS)
        self.assertEqual(inspect.signature(generate_html).parameters["fps"].default,
                         DEFAULT_FPS)
        # gen 的 parser 建在 main() 里（没有独立的 _build_parser 可调），按源码钉住：
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "scripts", "gen_hyperframes.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"--fps", type=int, default=DEFAULT_FPS', src)
        self.assertNotIn('"--fps", type=int, default=24', src)

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

    def test_until_html_canvas_missing_with_placeholder_previews(self):
        """显式预览档：整页画布缺图时放行 HTML，并把同一个决定传给生成器。

        默认那一档（上一条）照旧拦——这里锁的是"拦得住"与"想先看结构时有路
        可走"两件事同时成立，而不是用放宽换掉拦截。
        """
        self._drop_image_key("seg-b")  # seg-b 是 layout:"canvas"
        _, err = self.h.invoke(["--until", "html", "--placeholder-missing"])
        self.assertTrue(os.path.isfile(os.path.join(self.h.project, "index.html")))
        self.assertIn("占位 SVG", err)
        self.assertIn("--placeholder-missing", self.h.gen_argv)

    def test_placeholder_is_rejected_outside_the_preview_tier(self):
        """带占位旗标跑渲染 = 要把占位图烧进成片，入口直接报错（argparse exit 2）。"""
        self._drop_image_key("seg-b")
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--placeholder-missing"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("只配 --until html", self.h.last_err)
        self.assertIsNone(self.h.gen_argv)

    def test_placeholder_flag_not_forwarded_when_not_previewing(self):
        """没给旗标时生成器不该收到它：两处各判一次就会分家。"""
        self.h.make_images_complete()
        self.h.invoke(["--until", "html"])
        self.assertNotIn("--placeholder-missing", self.h.gen_argv)

    def test_visual_knobs_reach_the_generator(self):
        """--set / --drift 原样转给生成器：取值校验只有 gen 那份 parser 一份。"""
        self.h.make_images_complete()
        self.h.invoke(["--until", "html",
                       "--set", "animation.director.duration=0.9",
                       "--set", "layout.vertical.title.fontSize=72",
                       "--drift", "off"])
        argv = self.h.gen_argv
        i = argv.index("--set")
        self.assertEqual(argv[i + 1], "animation.director.duration=0.9")
        self.assertEqual(argv.count("--set"), 2, "可重复的旗标要全部转过去")
        self.assertEqual(argv[argv.index("--drift") + 1], "off")

    def test_visual_knobs_absent_unless_asked(self):
        self.h.make_images_complete()
        self.h.invoke(["--until", "html"])
        self.assertNotIn("--set", self.h.gen_argv)
        self.assertNotIn("--drift", self.h.gen_argv)

    def test_allow_ease_is_preview_only(self):
        """core 之外的 ease 渲染时会静默退回缺省——带它渲染等于交一支不是你要的片。"""
        self.h.make_images_complete()
        _, _ = self.h.invoke(["--until", "html", "--allow-ease"])
        self.assertIn("--allow-ease", self.h.gen_argv)

        self.h.gen_argv = None
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--allow-ease"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("只配 --until html", self.h.last_err)
        self.assertIsNone(self.h.gen_argv)

    def test_allow_ease_is_recorded_as_a_debug_tier(self):
        self.h.make_images_complete()
        self.h.invoke(["--until", "html", "--allow-ease"])
        self.assertIn("ease 钉固目录（--allow-ease，预览档）",
                      self.h.report()["skipped"])

    def test_missing_files_block_even_for_until_html(self):
        # images.json 有键但文件不在：映射指向坏路径，不是"图没画完"
        os.makedirs(self.h.project, exist_ok=True)
        write_json_atomic(os.path.join(self.h.project, "images.json"),
                          {"seg-a": {"src": "images/nope.svg"}})
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "html"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIsNone(self.h.gen_argv)

    def test_symlink_out_of_project_blocks_at_gate(self):
        """软链接指向项目外：run.py 的缺图闸门必须与 gen 同一口径。

        证伪：run.py 原先只按 os.path.isfile 判（它跟随软链接），于是把这种条目
        算成"图在"，覆盖率报告记 matched、闸门放行；下一步 gen_hyperframes 用
        realpath 判"越出 HTML 项目目录"再 fail-fast。同一份文件两道门两个答案，
        作者先看到一份乐观的覆盖率，再撞上另一道门的报错。
        """
        self.h.make_images_complete()
        outside = os.path.join(self.h.tmp, "outside.svg")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("<svg/>")
        link = os.path.join(self.h.project, "images", "seg-a.svg")
        os.remove(link)
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"本机不能建软链接（Windows 未开开发者模式？）: {exc}")
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke(["--until", "html"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("越出", self.h.last_err)
        self.assertIsNone(self.h.gen_argv)

    # ── 草稿档（--draft）：迭代逃生口的总开关，交付路径照旧全严 ────

    def test_draft_allows_missing_files_at_html(self):
        """草稿档："src 还没落盘"不卡死 --until html——先看版式，坏位置留空/占位。

        此前 missing_files 连 --until html 都 exit 2 且无逃生口：草稿期"先搭
        结构、图稍后画"的常态被整条卡死。--draft 放行并把同一个决定传给生成器
        （gen 的 --draft 会给画布段换占位、槽位段留空位）。
        """
        os.makedirs(self.h.project, exist_ok=True)
        write_json_atomic(os.path.join(self.h.project, "images.json"),
                          {"seg-a": {"src": "images/nope.svg"}})
        _, err = self.h.invoke(["--until", "html", "--draft"])
        self.assertTrue(os.path.isfile(os.path.join(self.h.project, "index.html")))
        self.assertIn("--draft", self.h.gen_argv)
        self.assertIn("缺 src", err)
        self.assertTrue(any("草稿档" in s for s in self.h.report()["skipped"]))

    def test_draft_allows_missing_files_at_render(self):
        """草稿档粗剪：缺 src 也照常出片（渲染 best-effort 隐含），报告留痕。"""
        os.makedirs(self.h.project, exist_ok=True)
        write_json_atomic(os.path.join(self.h.project, "images.json"),
                          {"seg-a": {"src": "images/nope.svg"}})
        self.h.invoke(["--draft"])
        self.assertEqual(len(self.h.render_calls), 1)
        self.assertTrue(self.h.report().get("draft"))

    def test_draft_opens_placeholder_and_ease_at_render(self):
        """--draft 是占位/ease 的总开关：带它跑渲染不再被入口 parser.error 拦。

        定稿路径（不带 --draft）照旧拦——上两条 test_placeholder_is_rejected_
        outside_the_preview_tier / test_allow_ease_is_preview_only 锁的就是
        "拦得住"；这里锁的是"想先看一版动起来时有路可走"。
        """
        self._drop_image_key("seg-b")  # seg-b 是 layout:"canvas"
        self.h.invoke(["--draft"])
        self.assertEqual(len(self.h.render_calls), 1, "草稿档照常出粗剪")
        self.assertIn("--draft", self.h.gen_argv)
        self.assertIn("占位", self.h.last_err)

    def test_draft_reports_marker(self):
        self.h.make_images_complete()
        _, _ = self.h.invoke(["--until", "html", "--draft"])
        rep = self.h.report()
        self.assertTrue(rep.get("draft"))
        self.assertTrue(any("草稿档（--draft" in s for s in rep["skipped"]))

    def _degrade(self):
        self.h.make_images_complete()
        self.h.manifest["status"] = "degraded"
        self.h.manifest["degraded"] = {D.LOST_SENTENCE_COUNT: 1}

    def test_degraded_blocks_render_exit3(self):
        self._degrade()
        with self.assertRaises(SystemExit) as cm:
            self.h.invoke([])
        self.assertEqual(cm.exception.code, 3)
        # 阻断信息必须附上可执行的补录路径——降级句带指纹缓存，会被
        # --resume 一直复用，没有这条提示，隔天重跑仍会卡在同一处静音。
        self.assertIn("补录", self.h.last_err)
        self.assertIn(os.path.join(self.h.out, "sentences"), self.h.last_err)

    def test_degraded_allowed_until_html_marks_report(self):
        self._degrade()
        self.h.invoke(["--until", "html", "--allow-degraded"])
        rep = self.h.report()
        self.assertEqual(rep["degraded"][0]["type"], "tts_lost_sentences")


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

    def test_unexpected_exception_from_a_step_still_lands_a_report(self):
        """子脚本抛 ValueError / OSError 时也要按失败步骤收尾。

        证伪（去掉 _run_step 那两条 except 之后实测）：一次普通的"路径写错"变成裸栈
        退出，且**不落** production_report——"这次跑到哪一步、哪一步挂的"这条唯一的
        排查线索也没了，与 docstring 承诺的"失败也落盘"不符。
        """
        for exc, label in ((ValueError("稿件里少了个字段"), "ValueError"),
                           (OSError(28, "磁盘满了"), "OSError")):
            with self.subTest(exc=label):
                def boom(argv, _e=exc):
                    raise _e
                with self.assertRaises(SystemExit) as cm:
                    self.h.invoke(["--until", "tts"], pipeline_main=boom)
                self.assertEqual(cm.exception.code, 1)
                rep = self.h.report()
                self.assertFalse(rep["steps"][0]["ok"])
                self.assertIn(label, self.h.last_err)
                self.assertIn(str(exc), self.h.last_err)

    def test_keyboard_interrupt_is_not_recorded_as_a_failed_step(self):
        """Ctrl-C 是用户主动中断，不该被当成"这一步渲染坏了"写进报告。"""
        def interrupted(argv):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.h.invoke(["--until", "tts"], pipeline_main=interrupted)
        self.assertFalse(os.path.exists(
            os.path.join(self.h.out, "production_report.json")))

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


class ArchitecturePin(unittest.TestCase):
    """P1 决策固化：run.py 不再用自己的 subprocess 调自家脚本。"""

    def test_run_has_no_self_subprocess(self):
        src = H.read_text(os.path.join(H.SCRIPTS_DIR, "run.py"))
        self.assertNotIn("subprocess", src,
                         "run.py 只应编排进程内步骤；唯一的子进程在 _render_backend")
        self.assertNotIn("sys.executable", src,
                         "TTS/HTML 步骤参数列表里不该再出现解释器路径")


class CheckFramesFlag(unittest.TestCase):
    """--check-frames 编排契约：默认关、量不到降级、断言失败阻断 exit 5。

    抽帧断言是渲染后的一道附加闸：不开绝不碰成片；开了且断言失败必须阻断
    交付（黑屏/死平/空帧的片子不该照打"完成"）；检查器自己出 bug 只降级，
    不让附加闸砸掉整条渲染。
    """

    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.h.make_images_complete()
        self.h.video_duration = self.h.manifest["total_duration"]

    def test_off_by_default_never_calls_checker(self):
        import check_frames
        with mock.patch.object(check_frames, "check_video",
                               return_value=([], [])) as cv:
            out, _ = self.h.invoke([])
        cv.assert_not_called()
        self.assertNotIn("frames", self.h.report()["video"])

    def test_flag_runs_checker_and_reports_ok(self):
        import check_frames
        with mock.patch.object(check_frames, "check_video",
                               return_value=([], ["[0.00s] ✓"])) as cv:
            out, _ = self.h.invoke(["--check-frames"])
        cv.assert_called_once()
        self.assertIn("完成", out)
        self.assertEqual(self.h.report()["video"]["frames"]["check"], "ok")

    def test_failure_blocks_exit5(self):
        import check_frames
        with mock.patch.object(check_frames, "check_video",
                               return_value=(["[0.00s] ✗ 非黑 0.0%"],
                                             ["[0.00s] ✗ 非黑 0.0%"])):
            with self.assertRaises(SystemExit) as cm:
                self.h.invoke(["--check-frames"])
        self.assertEqual(cm.exception.code, 5)
        self.assertEqual(self.h.report()["video"]["frames"]["check"], "failed")
        self.assertIn("抽帧断言失败", self.h.last_err)

    def test_checker_exception_degrades_not_blocks(self):
        import check_frames
        with mock.patch.object(check_frames, "check_video",
                               side_effect=RuntimeError("boom")):
            out, _ = self.h.invoke(["--check-frames"])
        self.assertIn("完成", out)
        self.assertEqual(self.h.report()["video"]["frames"]["check"], "error")

    def test_ocr_flag_forwarded(self):
        import check_frames
        with mock.patch.object(check_frames, "check_video",
                               return_value=([], [])) as cv:
            self.h.invoke(["--check-frames", "--check-frames-ocr"])
        self.assertTrue(cv.call_args.kwargs.get("ocr"))


if __name__ == "__main__":
    unittest.main()
