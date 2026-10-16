"""渲染后端：命令解析 + 进程管理 + 成片时长探测。用假进程（python -c）验证，不需要 Node / Chrome / ffmpeg。"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401
import _render_backend as RB


class Commands(unittest.TestCase):
    def test_build_render_command_shape(self):
        cmd = RB.build_render_command("o.mp4", "draft", 12, 4, [sys.executable, "hf"])
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[-10:], ["render", "-o", "o.mp4", "--quality", "draft",
                                     "--fps", "12", "--workers", "4",
                                     "--no-best-effort"])

    def test_build_render_command_has_no_format_flag(self):
        # 输出容器固定由 -o 后缀（mp4）决定：透明底/mov/webm 已从技能移除，
        # 不写 --format，渲染器按后缀推断容器。
        base = [sys.executable, "hf"]
        cmd = RB.build_render_command("o.mp4", "draft", 24, 4, base)
        self.assertEqual(cmd[len(base):],
                         ["render", "-o", "o.mp4",
                          "--quality", "draft", "--fps", "24", "--workers", "4",
                          "--no-best-effort"])
        self.assertNotIn("--format", cmd)

    def test_best_effort_is_always_off(self):
        """CLI 的 --best-effort 默认 true：媒体缺失/抓帧未就绪也照样出片。

        本技能红线是"音频缺失直接失败，不生成无声成片"，所以这个旗标恒关，
        且不与 low_memory 三态耦合——两档组合下都必须在。
        """
        base = [sys.executable, "hf"]
        for lm in (None, True, False):
            with self.subTest(low_memory=lm):
                cmd = RB.build_render_command("o.mp4", "draft", 24, 4, base,
                                              low_memory=lm)
                self.assertIn("--no-best-effort", cmd)
                self.assertNotIn("--best-effort", cmd)

    def test_low_memory_flag_is_tri_state(self):
        """低内存档三态：不给 = 交给 CLI 自判；给了就字面透传对应旗标。

        低配机器（≤8GB / /dev/shm 偏小）非它不可——此前 run.py 没有这个旗标，
        只能绕开 run.py 手工拼 `hyperframes render`，于是制作报告、时长闸、
        抽帧闸全套都跟着丢掉。
        """
        base = [sys.executable, "hf"]
        tail = ["render", "-o", "o.mp4", "--quality", "draft",
                "--fps", "24", "--workers", "4", "--no-best-effort"]
        self.assertEqual(
            RB.build_render_command("o.mp4", "draft", 24, 4, base)[len(base):], tail)
        self.assertEqual(
            RB.build_render_command("o.mp4", "draft", 24, 4, base,
                                    low_memory=True)[len(base):],
            tail + ["--low-memory-mode"])
        self.assertEqual(
            RB.build_render_command("o.mp4", "draft", 24, 4, base,
                                    low_memory=False)[len(base):],
            tail + ["--no-low-memory-mode"])

    def test_low_memory_does_not_silently_rewrite_workers(self):
        """CLI 的低内存档自己把 worker 钉成 1。这里不替它改 --workers——改了
        用户就看不见自己原本写的是什么，冲突由 run.py 在渲染前出声。"""
        cmd = RB.build_render_command("o.mp4", "draft", 24, 8,
                                      [sys.executable, "hf"], low_memory=True)
        self.assertIn("--workers", cmd)
        self.assertEqual(cmd[cmd.index("--workers") + 1], "8")
        self.assertIn("--low-memory-mode", cmd)

    def test_resolve_command_is_idempotent_for_strings(self):
        self.assertEqual(RB.resolve_command("cmd /c x"), "cmd /c x")
        self.assertEqual(RB.resolve_command([]), [])

    def test_unknown_executable_passes_through(self):
        self.assertEqual(RB.resolve_command(["definitely-not-a-binary-xyz", "a"]),
                         ["definitely-not-a-binary-xyz", "a"])

    def test_batch_cmdline_quotes_every_arg_and_blocks_injection(self):
        line = RB._cmdline_for_batch(r"C:\x\npx.cmd", ["-o", "a&b|c.mp4"])
        # 含 cmd 元字符的参数必须整体处于双引号内
        self.assertIn('"a&b|c.mp4"', line)
        self.assertTrue(line.endswith('"'))

    def test_spec_env_override(self):
        with mock.patch.dict(os.environ, {"CTV_HYPERFRAMES_PACKAGE": "hyperframes@9.9.9"}):
            self.assertEqual(RB.hyperframes_spec(), "hyperframes@9.9.9")
        with mock.patch.dict(os.environ, {"CTV_HYPERFRAMES_PACKAGE": "  "}):
            self.assertEqual(RB.hyperframes_spec(), "hyperframes")

    def test_fmt_cmd(self):
        self.assertEqual(RB.fmt_cmd(["a", "b"]), "a b")
        self.assertEqual(RB.fmt_cmd("a b"), "a b")

    def test_adaptive_grace_bounds(self):
        self.assertEqual(RB._adaptive_grace(0), RB._EXIT_GRACE_SECONDS)
        self.assertEqual(RB._adaptive_grace(10 ** 12), RB._GRACE_CAP_SECONDS)


def _py(code):
    return [sys.executable, "-c", code]


class RenderWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out.mp4")
        # 把轮询/宽限压到毫秒级，测试不必真等
        self._patches = [
            mock.patch.object(RB, "_POLL_INTERVAL_SECONDS", 0.05),
            mock.patch.object(RB, "_EXIT_GRACE_SECONDS", 0.2),
            mock.patch.object(RB, "_GRACE_CAP_SECONDS", 0.2),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_success_when_process_exits_zero_with_file(self):
        code = f"open({self.out!r},'wb').write(b'x'*100)"
        RB.render_wait(_py(code), self.out)
        self.assertTrue(os.path.getsize(self.out) > 0)
        self.assertTrue(os.path.isfile(os.path.splitext(self.out)[0] + ".render.log"))

    def test_nonzero_exit_raises_systemexit(self):
        with self.assertRaises(SystemExit) as cm:
            RB.render_wait(_py("import sys; sys.exit(3)"), self.out)
        self.assertIn("3", str(cm.exception))

    def test_exit_zero_without_file_is_failure(self):
        with self.assertRaises(SystemExit) as cm:
            RB.render_wait(_py("pass"), self.out)
        self.assertIn("未生成有效成片", str(cm.exception))

    def test_stable_file_then_hanging_process_is_killed(self):
        code = (f"import time; open({self.out!r},'wb').write(b'x'*100); time.sleep(60)")
        with mock.patch.object(RB, "_verify_killed_render", lambda p: None):
            RB.render_wait(_py(code), self.out, max_wait=30)
        self.assertTrue(os.path.getsize(self.out) > 0)

    def test_timeout_discards_partial_and_raises(self):
        code = f"import time; open({self.out!r},'wb').write(b'x'); time.sleep(60)"
        # 文件一直不稳定不可能（大小固定），所以用极小 max_wait 触发超时路径
        with mock.patch.object(RB, "_STABLE_SIZE_CHECKS", 10 ** 6):
            with self.assertRaises(SystemExit) as cm:
                RB.render_wait(_py(code), self.out, max_wait=0.5)
        self.assertIn("停止等待", str(cm.exception))
        self.assertFalse(os.path.exists(self.out), "超时必须丢弃半截成片")

    def test_missing_executable_is_human_readable(self):
        with self.assertRaises(SystemExit) as cm:
            RB.render_wait(["definitely-not-a-binary-xyz"], self.out)
        self.assertIn("无法启动", str(cm.exception))

    def test_old_output_is_removed_before_render(self):
        with open(self.out, "wb") as f:
            f.write(b"old")
        with self.assertRaises(SystemExit):
            RB.render_wait(_py("import sys; sys.exit(1)"), self.out)
        self.assertFalse(os.path.exists(self.out))


class ProbeVideoDuration(unittest.TestCase):
    """成片时长探测：值/降级/容错。

    probe_video_duration 是"渲染之后那道闸"的量具，所以它自己的失败必须
    是**降级**而不是新的失败点：ffmpeg 是可选依赖（SKILL.md「环境」），
    量不到就交回调用方去 warn，不能把一个"没装 ffmpeg"变成渲染失败。
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out.mp4")
        with open(self.out, "wb") as f:
            f.write(b"x" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_measured_seconds(self):
        with mock.patch("_audio.measure_duration", lambda ff, p: 12.5):
            self.assertEqual(RB.probe_video_duration(self.out, "ff"), 12.5)

    def test_zero_from_measure_is_treated_as_unmeasured(self):
        # measure_duration 以 0.0 表示"WAV 读不出 + ffmpeg 解析不到"，
        # probe 必须把它翻成 None（0 秒是个合法时长，不能当成没量到放行）
        with mock.patch("_audio.measure_duration", lambda ff, p: 0.0):
            self.assertIsNone(RB.probe_video_duration(self.out, "ff"))

    def test_missing_ffmpeg_degrades_to_none(self):
        err = io.StringIO()
        with mock.patch("_audio.measure_duration",
                        side_effect=OSError("No such file")), \
             contextlib.redirect_stderr(err):
            self.assertIsNone(RB.probe_video_duration(self.out, "ff"))
        self.assertIn("成片时长无法测量", err.getvalue())

    def test_unexpected_error_degrades_to_none(self):
        err = io.StringIO()
        with mock.patch("_audio.measure_duration",
                        side_effect=RuntimeError("boom")), \
             contextlib.redirect_stderr(err):
            self.assertIsNone(RB.probe_video_duration(self.out, "ff"))
        self.assertIn("成片时长测量失败", err.getvalue())

    @unittest.skipIf(os.name != "posix", "假 ffmpeg 用 POSIX shell 脚本")
    def test_end_to_end_parses_real_duration_line(self):
        # 真走一遍 `ffmpeg -i` 的 stderr 解析（Duration: 行），确认复用
        # measure_duration 那条路对 mp4 落到了 ffmpeg 分支而不是 WAV 分支。
        ff = os.path.join(self.tmp.name, "fakeff")
        with open(ff, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho "Duration: 00:00:12.50, start: 0.0" >&2\nexit 1\n')
        os.chmod(ff, 0o755)
        self.assertEqual(RB.probe_video_duration(self.out, ff), 12.5)


class NodeVersionGate(unittest.TestCase):
    """渲染前的 Node 版本闸。

    Hyperframes CLI 的 engines 写死 node>=22，而版本不够时这一侧看不出原因：
    npx/CLI 会以 Unsupported engine、语法错误、或卡在 Chrome 启动上的形态失败，
    run.py 只拿到一个非零退出码。所以版本必须在开跑前问——但只有**量到了**
    才配当门禁，量不到就退回旧路径（Popen 失败那条"请确认 Node.js 已安装"）。
    """
    @contextlib.contextmanager
    def _node(self, stdout=b"", side_effect=None):
        """把 `node --version` 换成给定输出（给了 side_effect 就改成抛异常）。"""
        with mock.patch.object(RB.shutil, "which", lambda _n: "/usr/bin/node"), \
             mock.patch.object(RB.subprocess, "run",
                               side_effect=side_effect or
                               (lambda *a, **k: mock.Mock(stdout=stdout, stderr=b""))):
            yield

    def test_recent_node_passes_silently(self):
        with self._node(b"v22.13.0"):
            self.assertEqual(RB.node_version_gate(), ("ok", ""))
        with self._node(b"v24.0.0\n"):
            self.assertEqual(RB.node_version_gate(), ("ok", ""))

    def test_old_node_blocks_with_the_required_version(self):
        with self._node(b"v18.20.4"):
            verdict, msg = RB.node_version_gate()
        self.assertEqual(verdict, "block")
        self.assertIn("v18", msg)
        self.assertIn(str(RB.MIN_NODE_MAJOR), msg)

    def test_no_node_only_warns(self):
        # 没装 node 时后面的 Popen 本来就会报人话；这里再阻断只是把同一件事
        # 说两遍，还可能误伤"node 不在 PATH 但环境里有 node"的机器。
        with mock.patch.object(RB.shutil, "which", lambda _n: None):
            verdict, msg = RB.node_version_gate()
        self.assertEqual(verdict, "warn")
        self.assertIn(str(RB.MIN_NODE_MAJOR), msg)

    def test_unreadable_version_only_warns(self):
        for out in (b"", b"node: internal error", b"built from source"):
            with self._node(out):
                self.assertEqual(RB.node_version_gate()[0], "warn")

    def test_node_call_failure_only_warns(self):
        with self._node(side_effect=OSError("exec failed")):
            self.assertEqual(RB.node_version_gate()[0], "warn")

    def test_boundary_is_inclusive(self):
        # ≥ 22 放行、21 拦下：差一个版本号的机器不该被放过去，也不该被误杀
        with self._node(b"v21.9.9"):
            self.assertEqual(RB.node_version_gate()[0], "block")
        with self._node(b"v22.0.0"):
            self.assertEqual(RB.node_version_gate()[0], "ok")


if __name__ == "__main__":
    unittest.main()
