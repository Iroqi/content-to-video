"""六轮奥卡姆 review 的行为锁：这一轮对着**"闸门挂在错误的分支上"**查。

前几轮在 `test_occam_review.py`（一轮/二轮）、`_3`、`_4`、
`_5`（"承诺了但没生效"）里。本文件锁的是同一族的失效，形状稍有不同：

> 闸门本身写对了、也真的能跑，但它只挂在 `--until html` 与定稿渲染两处，
> 而 `--until check` 那一档同样具备"闸门能跑"的全部前提（HTML 已生成、
> 时间轴在手上），却整条分支里根本没有调用点。

以及同一轮的另一种形状：**承诺写在了编排层的参数节里，旗标却只存在于
下游脚本**——编排层根本没有这个旗标，那条路径从来没有被人走过。

配套清理（死代码与重复判据）也在这里钉住，防止它们以"将来可能用得上"
的名义爬回来。
"""
import os
import re
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _beats as B
import _css_guard as CG
import _derive as DV
import _media_size as MS
import run as R
import _render_backend as RB
from test_run_orchestration import Harness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_gate(project, tm, out_dir, aspect=None, command=None):
    """观感门禁的假产物：帧数/判词/拼图三样都在，没有 error。

    只用来证明"这一档真的调到了门禁"——真门禁要 Node + Chrome + Pillow，
    本文件不覆盖那条链路（`tests/test_perceptual.py` 与 e2e 才是它的地盘）。
    """
    _fake_gate.calls.append({"project": project, "aspect": aspect,
                             "out_dir": out_dir})
    return {"frames": 4, "issues": [], "contact_sheet": "sheet.png",
            "segs": []}


_fake_gate.calls = []


class CheckUntilRunsPerceptual(unittest.TestCase):
    """`--until check --perceptual`：HTML 已生成，观感门禁却整条分支没有调用点。

    修复前：`--perceptual` 只有两个落点（`args.until == "html"` 与定稿渲染），
    `--until check` 分支里只有官方门禁 → 落报告 → return。于是这一档的判词
    凭空消失，报告里连一条 skipped 都没有，而 `--perceptual` 的 help 写的是
    "跑观感门禁…判词进 production_report 的 perceptual 字段"。
    """

    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        self.h.make_images_complete()
        _fake_gate.calls = []

    def _invoke(self, argv):
        with mock.patch.object(R.perceptual, "run_gate", _fake_gate), \
                mock.patch.object(R, "node_version_gate", lambda: ("ok", "")):
            return self.h.invoke(argv)

    def test_check_until_runs_the_gate(self):
        self._invoke(["--until", "check", "--perceptual"])
        self.assertEqual(len(_fake_gate.calls), 1,
                         "--until check --perceptual 没有跑观感门禁")

    def test_check_until_writes_verdict_into_report(self):
        self._invoke(["--until", "check", "--perceptual"])
        self.assertEqual(self.h.report()["perceptual"]["frames"], 4)

    def test_without_the_flag_the_gate_stays_off(self):
        self._invoke(["--until", "check"])
        self.assertEqual(_fake_gate.calls, [])
        self.assertNotIn("perceptual", self.h.report())

    def test_html_and_check_share_one_implementation(self):
        """两档的判词必须同源：各贴一段正是本轮失效的成因。"""
        self._invoke(["--until", "html", "--perceptual"])
        html_report = self.h.report()["perceptual"]
        _fake_gate.calls = []
        self._invoke(["--until", "check", "--perceptual"])
        self.assertEqual(html_report, self.h.report()["perceptual"])


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


class SlotNoImageReachesTheOrchestrator(unittest.TestCase):
    """`--slot-no-image` 写在 run.py 的参数节里，旗标却只在 gen_hyperframes。

    修复前：`run.py --slot-no-image` 直接被 argparse 拒；就算手工分步调
    gen，编排层的缺图闸也会在 `--until render` 下 exit 2，把这条"没有生图
    能力也能出片"的路整条堵死。
    """

    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.cleanup)
        # 只给整页画布段（seg-b）落图：槽位段 seg-a 缺图。
        os.makedirs(os.path.join(self.h.project, "images"), exist_ok=True)
        with open(os.path.join(self.h.project, "images", "seg-b.svg"),
                  "w", encoding="utf-8") as f:
            f.write("<svg/>")
        from _script_utils import write_json_atomic
        write_json_atomic(os.path.join(self.h.project, "images.json"),
                          {"seg-b": {"src": "images/seg-b.svg"}})

    def test_flag_is_parseable(self):
        args = R._build_parser(show_all=True).parse_args(
            ["--source", "x.json", "--slot-no-image"])
        self.assertTrue(args.slot_no_image)

    def test_flag_reaches_the_html_step(self):
        self.h.invoke(["--until", "html", "--slot-no-image"])
        self.assertIn("--slot-no-image", self.h.gen_argv)

    def test_slot_missing_no_longer_blocks_delivery(self):
        with mock.patch.object(R, "node_version_gate", lambda: ("ok", "")):
            self.h.invoke(["--slot-no-image"])
        rep = self.h.report()
        self.assertEqual(rep["images"]["missing"], 1,
                         "缺图的事实要照实记，放行的是拦截不是统计")
        self.assertTrue(any("slot-no-image" in s for s in rep["skipped"]),
                        f"放行没在报告里留痕：{rep['skipped']}")

    def test_without_the_flag_slot_missing_still_blocks(self):
        with mock.patch.object(R, "node_version_gate", lambda: ("ok", "")):
            with self.assertRaises(SystemExit) as cm:
                self.h.invoke([])
        self.assertEqual(cm.exception.code, 2)


class DeadCodeStaysOut(unittest.TestCase):
    """本轮清掉的死代码：它们在仓库里零调用方，靠"将来可能用得上"续命。

    `_derive.span()` 尤其典型——docstring 说"起点 + 时长"，函数体只取了
    duration。契约与实现不符却没人发现，正是因为它一次都没被调用过。
    """

    def test_derive_unused_helpers_are_gone(self):
        for name in ("js_number", "js_css_attr_value", "ease_progress",
                     "value_at"):
            self.assertFalse(hasattr(DV, name), f"_derive.{name} 又回来了")

    def test_derive_unused_methods_are_gone(self):
        for name in ("keys", "span"):
            self.assertFalse(hasattr(DV.Derived, name),
                             f"Derived.{name} 又回来了")

    def test_beats_duplicate_printer_is_gone(self):
        """`print_audit` 全仓零调用方：run.py 自己又写了一遍同样的打印循环。"""
        self.assertFalse(hasattr(B, "print_audit"))


class SingleJudgment(unittest.TestCase):
    """同一条判据只写一份：两处各写一遍的那几处。"""

    def test_svg_guard_first_branch_was_subsumed(self):
        """`"svg" in sel.split()` 恒被 `\\bsvg\\b` 包含——留着只是噪音。

        这条把"包含关系"本身钉住：哪天有人把 `\bsvg\b` 放宽（比如只认
        `^svg`），这条会红，提醒他第一条分支不再冗余、得真回来了。
        """
        corpus = ["svg", "#img-x svg", "svg .a", ".svg-icon", ".svg", "svgx",
                  "#a>svg", "svg,a", "[data-x] svg", "SVG", "#A>SVG", "Svg"]
        for sel in corpus:
            if "svg" in sel.lower().split():
                self.assertIsNotNone(
                    re.search(r"\bsvg\b", sel, re.I),
                    f"{sel!r} 命中了被删掉的第一条分支，留下的那条却没命中"
                    "——两条并不等价，删错了")
        for sel in ("svg", "#img-x svg", "svg .a", "#a>svg", "svg,a",
                    "SVG", "#A>SVG"):
            self.assertIsNone(CG.transform_tween_kind(sel))

    def test_transform_declaration_has_one_regex(self):
        """`transform_rules` 里"这条声明是不是 transform"只认一份正则。"""
        with open(os.path.join(ROOT, "scripts", "_css_guard.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("^\\s*transform\\s*:", src)

    def test_cover_geometry_shares_one_guard(self):
        for degenerate in (((0, 0), (10, 10)), ((10, 10), (0, 0)),
                           (None, (10, 10)), ((10, 10), None),
                           ((-1, 10), (10, 10))):
            self.assertIsNone(MS.cover_scale(*degenerate))
            self.assertIsNone(MS.cover_crop_fraction(*degenerate))
        self.assertAlmostEqual(MS.cover_scale((100, 100), (50, 50)), 2.0)
        self.assertAlmostEqual(MS.cover_crop_fraction((100, 100), (50, 50)), 0.0)

    def test_margin_entry_point_is_single(self):
        self.assertFalse(hasattr(__import__("check_svg"), "_margin_pair"))
        self.assertEqual(set(__import__("check_svg").safe_margins()),
                         {"portrait", "landscape"})


if __name__ == "__main__":
    unittest.main()
