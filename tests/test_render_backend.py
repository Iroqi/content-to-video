"""渲染后端：命令解析 + 进程管理。用假进程（python -c）验证，不需要 Node / Chrome / ffmpeg。"""
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
        self.assertEqual(cmd[-9:], ["render", "-o", "o.mp4", "--quality", "draft",
                                    "--fps", "12", "--workers", "4"])

    def test_fmt_writes_the_flag_only_when_not_mp4(self):
        # 渲染器本来按 -o 后缀推断容器，mp4 是默认值：多写一面旗会把"默认命令"
        # 变成两条不同的命令行，快照与实测就分叉了。
        base = [sys.executable, "hf"]
        for fmt in (None, "mp4"):
            self.assertEqual(
                RB.build_render_command("o.mp4", "draft", 24, 4, base, fmt=fmt),
                RB.build_render_command("o.mp4", "draft", 24, 4, base))
        webm = RB.build_render_command("o.webm", "draft", 24, 4, base, fmt="webm")
        self.assertEqual(webm[webm.index("--format") + 1], "webm")
        # --format 紧跟 -o，且仍在 --quality 之前
        self.assertEqual(webm[len(base):],
                         ["render", "-o", "o.webm", "--format", "webm",
                          "--quality", "draft", "--fps", "24", "--workers", "4"])

    def test_composition_flag_sits_between_render_and_output(self):
        # hyperframes 把 render 后第一个非选项参数当项目目录，-c 放后面就换根了
        cmd = RB.build_render_command("p.html.mp4", "draft", 24, 4,
                                      [sys.executable, "hf"], composition="p.html")
        self.assertEqual(cmd[:5], [sys.executable, "hf", "render", "-c", "p.html"])

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


if __name__ == "__main__":
    unittest.main()
