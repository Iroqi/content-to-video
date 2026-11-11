"""渲染后端：命令解析 + 进程管理 + 成片时长探测。用假进程（python -c）验证，不需要 Node / Chrome / ffmpeg。"""
import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401
import _render_backend as RB
import perceptual as P

# v0.8.185 `check --json` 实测的六个阶段名。只用来**构造**假报告——判读那一侧
# 按结构认阶段（带 findings 的顶层块），不认名字，所以这张表不必跟着判读改。
V0185_STAGES = ("lint", "runtime", "layout", "motion", "contrast", "craft")


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

    def test_allow_best_effort_is_an_explicit_opt_in(self):
        """红线留一个显式逃生口：默认关，显式开时换成 CLI 的 --best-effort。

        修复前这条红线是硬编码的，run.py 没有开关——调试某段媒体时想看整片
        节奏只能绕开 run.py 手跑 CLI，连 HTML 重新生成与成片时长对账一起丢掉。
        两档互斥、且不能同时出现（CLI 对冲突旗标的取值没有承诺）。
        """
        base = [sys.executable, "hf"]
        on = RB.build_render_command("o.mp4", "draft", 24, 4, base,
                                     allow_best_effort=True)
        self.assertIn("--best-effort", on)
        self.assertNotIn("--no-best-effort", on)
        off = RB.build_render_command("o.mp4", "draft", 24, 4, base,
                                      allow_best_effort=False)
        self.assertIn("--no-best-effort", off)
        self.assertNotIn("--best-effort", off)

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
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(RB.hyperframes_spec(), RB.DEFAULT_HYPERFRAMES_SPEC)
        self.assertEqual(RB.DEFAULT_HYPERFRAMES_SPEC, "hyperframes@0.8.185")
        with mock.patch.dict(os.environ,
                             {"CTV_HYPERFRAMES_PACKAGE": "hyperframes@9.9.9"}, clear=True):
            self.assertEqual(RB.hyperframes_spec(), "hyperframes@9.9.9")
        with mock.patch.dict(os.environ, {"CTV_HYPERFRAMES_PACKAGE": "  "}, clear=True):
            self.assertEqual(RB.hyperframes_spec(), RB.DEFAULT_HYPERFRAMES_SPEC)

    def test_command_uses_the_selected_spec(self):
        """命令必须运行已验证的 pin，不能被项目本地的其它版本替换。"""
        with mock.patch.dict(os.environ,
                             {"CTV_HYPERFRAMES_PACKAGE": "hyperframes@9.9.9"}, clear=True), \
             mock.patch.object(RB.shutil, "which", return_value="/usr/bin/npx"):
            self.assertEqual(RB.hyperframes_command(),
                             ["/usr/bin/npx", "--yes", "hyperframes@9.9.9"])

    def test_fmt_cmd(self):
        self.assertEqual(RB.fmt_cmd(["a", "b"]), "a b")
        self.assertEqual(RB.fmt_cmd("a b"), "a b")

    def test_adaptive_grace_bounds(self):
        self.assertEqual(RB._adaptive_grace(0), RB._EXIT_GRACE_SECONDS)
        self.assertEqual(RB._adaptive_grace(10 ** 12), RB._GRACE_CAP_SECONDS)


def _py(code):
    return [sys.executable, "-c", code]


class WallClockOnlyForWallClock(unittest.TestCase):
    """量间隔用 monotonic，比对文件 mtime 用墙钟：两种用途不许混。"""

    @staticmethod
    def _calls_wall_clock(func_name):
        """函数体里有没有出现 `time.time()`（墙钟）调用。"""
        path = RB.__file__
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == func_name)
        return any(isinstance(n, ast.Attribute) and n.attr == "time"
                   and isinstance(n.value, ast.Name)
                   and n.value.id == "time"
                   for n in ast.walk(node))

    def test_run_check_measures_with_monotonic(self):
        """修复前：run_check 用 time.time() 量间隔，墙钟被 NTP 校正就报负数。"""
        self.assertFalse(
            self._calls_wall_clock("run_check"),
            "run_check 量的是间隔，必须走 time.monotonic()")

    def test_snapshot_frames_still_compares_against_wall_clock(self):
        """反向：snapshot 比的是文件 mtime，那里**必须**用 time.time()。"""
        self.assertTrue(
            self._calls_wall_clock("snapshot_frames"),
            "snapshot_frames 拿 t0 跟文件 mtime 比，改 monotonic 就永远判不出新产物")


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


_SCRIPTS2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")


class HyperframesPackageHasOneSource(unittest.TestCase):
    """钉固的版本号在仓库里被抄了四份，其中三份是退化的复制品。

    修复前：`run.py` 与 `perceptual.py` 各写一遍
    `os.environ.get("CTV_HYPERFRAMES_PACKAGE", "hyperframes@0.8.185")`——
    硬编码的第二默认值会随 `_render_backend.DEFAULT_HYPERFRAMES_SPEC` 漂移，
    而且 `CTV_HYPERFRAMES_PACKAGE=""`（空串是真实的误配形态）在这里**不会**
    回落，直接把空串当包名递给 npx。`_render_backend.hyperframes_spec()`
    用 `.strip() or DEFAULT` 处理了这件事，另外三处没有。
    """

    def test_pinned_version_appears_once_in_scripts(self):
        spec = RB.DEFAULT_HYPERFRAMES_SPEC
        for name in sorted(os.listdir(_SCRIPTS2)):
            if not name.endswith(".py"):
                continue
            text = open(os.path.join(_SCRIPTS2, name), encoding="utf-8").read()
            hits = text.count(spec)
            if name == "_render_backend.py":
                continue
            self.assertEqual(hits, 0,
                             f"{name} 里硬编码了 {spec}：包名只有 "
                             f"_render_backend.hyperframes_spec() 一个真源")

    def test_empty_env_var_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"CTV_HYPERFRAMES_PACKAGE": "   "}):
            self.assertEqual(RB.hyperframes_spec(),
                             RB.DEFAULT_HYPERFRAMES_SPEC,
                             "空/空白的包名必须回落，不能把空串递给 npx")

    def test_perceptual_cli_default_uses_the_same_source(self):
        import argparse
        ap = argparse.ArgumentParser()
        # 与 perceptual.main 里 --package 同一形态：只验默认值来源
        with mock.patch.dict(os.environ,
                             {"CTV_HYPERFRAMES_PACKAGE": "hyperframes@9.9.9"}):
            self.assertEqual(P.hyperframes_spec(), "hyperframes@9.9.9")
        with mock.patch.dict(os.environ, {"CTV_HYPERFRAMES_PACKAGE": ""}):
            self.assertEqual(P.hyperframes_spec(),
                             RB.DEFAULT_HYPERFRAMES_SPEC)
        del ap


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


class FfmpegRenderGate(unittest.TestCase):
    """渲染前的 ffmpeg 版本闸。

    hyperframes 0.8.185 的音频封装用 setts bsf 的 `duration` 选项（ffmpeg ≥ 5.0
    才有，4.x 只有 ts/pts/dts）。版本不够时渲染不报"版本低"，只在混流阶段丢
    `Option 'duration' not found`——与 Node 版本闸同一种"已知前提别等失败才猜"。
    只认渲染侧 CLI 会用的那个二进制（HYPERFRAMES_FFMPEG_PATH 优先，其次 PATH）。
    """
    SETTS_OK = b"Bit stream filter setts\nsetts_bsf AVOptions:\n  -ts ...\n  -duration ...\n  -pts ...\n"
    SETTS_OLD = b"Bit stream filter setts\nsetts_bsf AVOptions:\n  -ts ...\n  -pts ...\n  -dts ...\n"

    def _run(self, outputs):
        """把 subprocess.run 换成按 args 给结果的函数。

        outputs: {关键字片段: (stdout, stderr, returncode)}；`-h bsf=setts` 与
        `-version` 分两条 mock，别让一条 stdio 同时伺候两个探测。returncode
        默认 0，探测判读按它区分"查不到"（非零/空输出）与"确认不支持"。
        """
        def fake_run(args, *a, **k):
            joined = " ".join(args)
            for key, (out, err, rc) in outputs.items():
                if key in joined:
                    return mock.Mock(stdout=out, stderr=err, returncode=rc)
            raise AssertionError(f"未预期的 ffmpeg 调用: {args}")
        return mock.patch.object(RB.subprocess, "run", side_effect=fake_run)

    def _which(self, path="/usr/bin/ffmpeg"):
        return mock.patch.object(RB.shutil, "which", lambda _n: path)

    def test_modern_ffmpeg_passes_silently(self):
        with self._which(), self._run({
            "bsf=setts": (b"", self.SETTS_OK, 0),
            "-version": (b"ffmpeg version 6.1.1", b"", 0),
        }):
            self.assertEqual(RB.ffmpeg_render_gate(), ("ok", ""))

    def test_old_ffmpeg_blocks_with_the_required_version(self):
        with self._which(), self._run({
            "bsf=setts": (b"", self.SETTS_OLD, 0),
            "-version": (b"ffmpeg version 4.4.2", b"", 0),
        }):
            verdict, msg = RB.ffmpeg_render_gate()
        self.assertEqual(verdict, "block")
        self.assertIn("4.4.2", msg)
        self.assertIn(str(RB.MIN_FFMPEG_RENDER_MAJOR), msg)

    def test_no_ffmpeg_only_warns(self):
        # 没装 ffmpeg 时 Popen 本就会报人话；这里再阻断只是说两遍
        with self._which(None):
            verdict, msg = RB.ffmpeg_render_gate()
        self.assertEqual(verdict, "warn")
        self.assertIn(str(RB.MIN_FFMPEG_RENDER_MAJOR), msg)

    def test_probe_failure_only_warns(self):
        # 探测抛异常 ≠ "确认不支持"，按查不到处理，不误杀
        with self._which(), \
             mock.patch.object(RB.subprocess, "run",
                               side_effect=OSError("exec failed")):
            self.assertEqual(RB.ffmpeg_render_gate()[0], "warn")

    def test_unreadable_bsf_output_only_warns(self):
        # 命令没跑成（非零退出 / 空输出）都是"查不到"，不是"确认不支持"
        for out, rc in ((b"", 0), (b"", 1), (b"no such bsf", 1)):
            with self._which(), self._run({"bsf=setts": (b"", out, rc)}):
                self.assertEqual(RB.ffmpeg_render_gate()[0], "warn")

    def test_explicit_ffmpeg_path_wins(self):
        # HYPERFRAMES_FFMPEG_PATH 是 CLI 的覆盖开关，探测必须认它不认 PATH
        with mock.patch.dict(os.environ, {"HYPERFRAMES_FFMPEG_PATH": "/opt/ff6/ffmpeg"}), \
             self._run({
                 "bsf=setts": (b"", self.SETTS_OK, 0),
                 "-version": (b"ffmpeg version 6.1.1", b"", 0),
             }):
            self.assertEqual(RB.ffmpeg_render_gate(), ("ok", ""))

    def test_boundary_is_inclusive(self):
        # setts 带 duration 即放行，不带即拦下；版本号只进报错文案不进判定
        with self._which(), self._run({"bsf=setts": (b"", self.SETTS_OK, 0)}):
            self.assertEqual(RB.ffmpeg_render_gate()[0], "ok")
        with self._which(), self._run({
            "bsf=setts": (b"", self.SETTS_OLD, 0),
            "-version": (b"ffmpeg version 4.4.2", b"", 0),
        }):
            self.assertEqual(RB.ffmpeg_render_gate()[0], "block")


if __name__ == "__main__":
    unittest.main()


def _report(stage, severity, code):
    return {"ok": True, stage: {"findings": [
        {"severity": severity, "code": code, "message": "m"}]}}


class GateStrictCodes(unittest.TestCase):
    """`GATE_STRICT_CODES` 是"必须当真"，不是"登记过就放过"。"""

    def test_strict_code_surfaces_as_warning(self):
        """`clipped_text` 以 warning 出现时也要点名。

        修复前 `known_codes` = 噪声 ∪ 严格码，于是它整条静默——而
        `references/rendering.md` 明写"clip_media_fit 与 clipped_text 不在噪声之列"。
        """
        for code in ("clipped_text", "clip_media_fit", "low_contrast",
                     "missing_asset", "runtime_error"):
            with self.subTest(code=code):
                verdict = RB.check_verdict(_report("layout", "warning", code))
                self.assertTrue(verdict, f"{code} 不该被静默")
                self.assertEqual(verdict[0][1], code)

    def test_noise_code_stays_silent(self):
        """反向：噪声码仍要静默，别把闸门改成"什么都报"。"""
        code = next(iter(RB.GATE_NOISE_CODES))
        self.assertEqual(RB.check_verdict(_report("lint", "warning", code)), [])

    def test_unregistered_warning_still_surfaces(self):
        """反向：未登记的新码照样点名（CLI 加了新检查不能被吞）。"""
        v = RB.check_verdict(_report("lint", "warning", "brand_new_code"))
        self.assertEqual([c for _, c, _ in v], ["unregistered:brand_new_code"])


class OfficialGate(unittest.TestCase):
    """`hyperframes check --json` 的判读：哪些 finding 该拦、哪些是登记过的噪声。"""

    def _report(self, **stages):
        body = {"ok": True, "browserSkipped": False}
        for name in V0185_STAGES:
            body[name] = {"ok": True, "errorCount": 0, "warningCount": 0, "findings": []}
        for name, findings in stages.items():
            errs = sum(1 for f in findings if f["severity"] == "error")
            warns = sum(1 for f in findings if f["severity"] == "warning")
            body[name] = {"ok": not errs, "errorCount": errs,
                          "warningCount": warns, "findings": findings}
            if errs:
                body["ok"] = False
        return body

    def test_build_check_command_shape(self):
        cmd = RB.build_check_command("proj", [sys.executable, "hf"])
        self.assertEqual(cmd[-3:], ["check", "proj", "--json"])

    def test_clean_report_passes(self):
        self.assertEqual(RB.check_verdict(self._report()), [])

    def test_registered_noise_does_not_block(self):
        """负 z-index / 嵌套结构 / 时间线密度这些在每份产物上都报到吐：放过。"""
        rep = self._report(lint=[
            {"code": "negative_z_index", "severity": "warning"},
            {"code": "nested_structure_needs_subcomposition", "severity": "warning"},
            {"code": "timeline_track_too_dense", "severity": "warning"},
        ])
        self.assertEqual(RB.check_verdict(rep), [])

    def test_error_blocks(self):
        rep = self._report(lint=[{"code": "font_family_without_font_face",
                                  "severity": "error", "message": "x"}])
        verdict = RB.check_verdict(rep)
        self.assertEqual([(s, c) for s, c, _ in verdict],
                         [("error", "font_family_without_font_face")])

    def test_new_code_is_reported_not_swallowed(self):
        """CLI 升级后多出来的检查必须点名——否则"全绿"是自动失效的承诺。"""
        rep = self._report(lint=[{"code": "brand_new_rule", "severity": "warning"}])
        self.assertEqual([c for _, c, _ in RB.check_verdict(rep)],
                         ["unregistered:brand_new_rule"])

    def test_craft_findings_are_not_dropped(self):
        """v0.8.185 新增的 craft 阶段与其它阶段走同一份判读。"""
        rep = self._report(craft=[{"code": "craft_new_rule", "severity": "warning"}])
        self.assertEqual([c for _, c, _ in RB.check_verdict(rep)],
                         ["unregistered:craft_new_rule"])

    def test_stage_unknown_to_us_is_not_dropped(self):
        """CLI 升级后多出来的**整个阶段**必须照样判读。

        阶段若按名字认，新阶段会整段失明——它里面的 error 一条都不报，check
        照样显示全绿。那比"新 code 被当噪声放过"更彻底，是门禁自己失效。
        """
        rep = self._report(audio=[{"code": "clip_media_fit", "severity": "error",
                                   "message": "x"}])
        self.assertEqual([(s, c) for s, c, _ in RB.check_verdict(rep)],
                         [("error", "clip_media_fit")])

    def test_blocks_without_findings_are_not_stages(self):
        """hdr / snapshots / saved / _meta 都是顶层 dict，但不带 findings。"""
        rep = self._report()
        rep["hdr"] = {"autoPromotion": None, "inspection": "available"}
        rep["snapshots"] = {"enabled": False, "files": [], "times": []}
        rep["saved"] = {"id": "x", "report": "/tmp/x.json"}
        rep["_meta"] = {"version": "0.8.185"}
        self.assertEqual(RB.check_verdict(rep), [])

    def test_lint_error_means_browser_stages_did_not_run(self):
        """Lint 有 error 时 browser 五阶段被整段跳过：那些 0 不是"通过"。"""
        rep = self._report(lint=[{"code": "external_script", "severity": "error"}])
        rep["browserSkipped"] = True
        codes = [c for _, c, _ in RB.check_verdict(rep)]
        self.assertIn("browser_stages_skipped", codes)

    def test_unreadable_output_is_an_error(self):
        self.assertEqual(RB.check_verdict(None)[0][0], "error")
        self.assertEqual(RB.check_verdict({"whatever": 1})[0][0], "error")


class CheckOutputParsing(unittest.TestCase):
    """`check --json` 的 stdout 里抠报告：进度行在前在后都不能坏判读。"""

    def test_report_with_trailing_progress_line_is_not_lost(self):
        """证伪：报告后面夹一行进度输出时，loads 整段抛异常 → 假 check_output error。

        假的"输出读不懂"会让人去查根本不存在的渲染问题，而真报告明明是全绿。
        """
        raw = ('[hyperframes] lint done\n{"ok": true, "lint": {"findings": []}}\n'
               "[hyperframes] all stages done\n")
        self.assertEqual(RB._first_json_object(raw), {"ok": True, "lint": {"findings": []}})

    def test_progress_line_with_braces_does_not_fake_the_report(self):
        """进度行自己也可能带花括号：从第一个 { 硬解析会读到半截对象。"""
        raw = 'starting {module}\n{"ok": false}\ntail\n'
        self.assertEqual(RB._first_json_object(raw), {"ok": False})

    def test_no_json_at_all_returns_none(self):
        self.assertIsNone(RB._first_json_object("no braces here\n"))
        self.assertIsNone(RB._first_json_object(""))

    def test_non_object_json_is_skipped(self):
        self.assertIsNone(RB._first_json_object('"just a string"\n[1,2]\n'))

    def test_run_check_end_to_end_with_noisy_stdout(self):
        report = {"ok": True, "browserSkipped": False}
        for name in V0185_STAGES:
            report[name] = {"ok": True, "errorCount": 0, "warningCount": 0,
                            "findings": []}
        raw = (b"[cli] resolving spec\n"
               + json.dumps(report).encode()
               + b"\n[cli] done\n")
        with mock.patch.object(RB.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=raw, stderr=b"")), \
             mock.patch.object(RB, "build_check_command",
                               lambda p, command=None: _py("pass")):
            findings, _sec = RB.run_check("proj")
        self.assertEqual(findings, [])


class SnapshotKeepsTheEndFrame(unittest.TestCase):
    """`--check-frames` 承诺"含首末帧"，而 `--no-end` 把末帧关掉了。

    官方 `--end` 默认 true（会额外补一张时间线末帧）。修复前命令里硬带
    `--no-end`，末帧永远不在产物里；修复方式是**删掉那一行**而不是自己
    猜一个 `dur - eps` 时刻。
    """

    def test_command_does_not_opt_out_of_the_end_frame(self):
        seen = {}

        def run(args, **kw):
            seen["args"] = args
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(RB.subprocess, "run", run), \
                    mock.patch.object(RB, "_newest_mtime", lambda d: 1e18):
                _snap, why = RB.snapshot_frames(
                    "proj", "out.mp4", [0.0, 3.5],
                    os.path.join(tmp, "frames"),
                    command=["npx", "--yes", "hyperframes"])
        self.assertEqual(why, "")
        self.assertNotIn("--no-end", seen["args"],
                         "--no-end 会关掉官方默认给的那张末帧")
        self.assertIn("--at", seen["args"])


class SnapshotCommandAssembly(unittest.TestCase):
    """取帧命令：子命令参数必须在 resolve **之后**仍进得了命令行。"""

    def _run_snapshot(self, args, tmp, returncode=0):
        out_dir = os.path.join(tmp, "frames")
        with mock.patch.object(RB.subprocess, "run",
                               lambda *a, **k: mock.Mock(
                                   returncode=returncode, stdout=b"", stderr=b"")), \
             mock.patch.object(RB, "_newest_mtime", lambda d: 1e18):
            return RB.snapshot_frames("proj", "out.mp4", [0.0, 3.5], out_dir,
                                      command=args)

    def test_snapshot_params_are_attached_after_the_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            _snap, why = self._run_snapshot([sys.executable, "hf"], tmp)
            self.assertEqual(why, "")

    @unittest.skipIf(os.name == "nt", "模拟 Windows 分支，真 Windows 上走真路径")
    def test_windows_batch_wrap_keeps_snapshot_args_inside_the_body(self):
        """证伪：先 resolve 再拼参数时，--against/--at 落在 cmd.exe 自己的 argv 里。

        Windows 上 npx 解析成 npx.cmd，resolve_command 把整条命令包进
        `cmd /c "…"` 字符串。参数拼在字符串**后面**就进了 /c 命令体之外——
        取帧按默认值跑，contact-sheet 里的帧全不对。
        """
        with tempfile.TemporaryDirectory() as tmp:
            run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=b"",
                                                   stderr=b""))
            with mock.patch.object(RB.os, "name", "nt"), \
                 mock.patch.object(RB.shutil, "which",
                                   lambda n: r"C:\fake\npx.cmd" if n == "npx" else None), \
                 mock.patch.object(RB.subprocess, "run", run), \
                 mock.patch.object(RB, "_newest_mtime", lambda d: 1e18):
                _snap, why = RB.snapshot_frames(
                    "proj", "out.mp4", [0.0, 3.5],
                    os.path.join(tmp, "frames"),
                    command=["npx", "--yes", "hyperframes@0.8.185"])
            self.assertEqual(why, "")
            # Popen 收到的是一条 cmd /c 字符串：snapshot 子命令与全部参数都必须在
            # /c 的命令体里，而不是字符串尾部的额外 argv。
            self.assertIsInstance(run.call_args[0][0], str)
            line = run.call_args[0][0]
            self.assertIn("/c", line)
            # `"0,3.5"` 同时是命令体里最后一个参数：末帧由官方 `--end`（默认 true）
            # 补，命令行里不再有 --no-end，收尾引号必须紧贴它。
            for token in ('"snapshot"', '"proj"', '"--against"', '"out.mp4"',
                          '"--at"', '"0,3.5"'):
                self.assertIn(token, line)
            # 字符串收尾于命令体引号：后面不再拖任何参数
            self.assertTrue(line.endswith('"'))


class SlotRectSurvivesBadTemplate(unittest.TestCase):
    """读不出模板要**出声**，不能静默返回 None 让「画布稀薄」整条消失。

    修复前 `slot_rect` 在 `load_template` 抛 OSError 时直接 `return None, None`：
    排查时看到的是「没报」而不是「报不了」，整条稀薄判据跟着人间蒸发。
    """

    def test_template_failure_is_loud(self):
        import _template
        with mock.patch.object(_template, "load_template",
                               side_effect=OSError("模板不在")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                box, _canvas = P.slot_rect("portrait")
        self.assertIsNone(box)
        self.assertIn("图槽矩形", err.getvalue(),
                      "读不出模板必须打 warn：否则排查时看到的是「没报」"
                      "而不是「报不了」")


class ProcessTreeCleanup(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX 分支：taskkill 路径在 Windows 上")
    def test_already_gone_process_is_not_a_failure(self):
        """证伪：超时判定与子进程退出之间的竞态里 getpgid 抛 ProcessLookupError。

        进程已经没了却打印"进程组清理失败、渲染子进程可能残留"——一次吓人的假
        警报，真正的残留反而混在里面没人信。
        """
        err = io.StringIO()
        with mock.patch.object(RB.os, "getpgid",
                               side_effect=ProcessLookupError("no such process")), \
             contextlib.redirect_stderr(err):
            RB._try_kill_process_tree(mock.Mock(pid=123))
        self.assertEqual(err.getvalue(), "")
