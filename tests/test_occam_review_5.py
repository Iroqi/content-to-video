"""五轮奥卡姆 review 的行为锁：这一轮对着**"承诺了但没生效"的闸门**查。

前几轮在 `test_occam_review.py`（一轮/二轮）、`test_occam_review_3.py`
（check_svg 色名、TTS 解码归类、恒真条件）、`test_occam_review_4.py`
（远端大合入里的静默失效）里。本文件锁的是四条同一形状的失效：

> 文档/代码承诺了一道闸门，闸门自己也写对了，但**输入从来没有送到它手上**，
> 或者**产物在交到它手上之前就被自己删了**。

这四条的共同点是没有一条会报错——全仓 1500+ 用例全绿，而四条功能全是死的。
所以这里的每条用例都写成"**修复前会红**"的方向，且方向必须是"判据真的变了"，
不能是"断言旧行为继续成立"（前两轮踩过的坑：恒不触发的判据 + 一条断言它
不触发的测试，两边一起绿了很久）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import perceptual as P      # noqa: E402
import pipeline as PL       # noqa: E402
import _beats as B          # noqa: E402
import _render_backend as RB  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def _source_text(name):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
        return fh.read()


# ── 台词板：manifest 里得真有 beats，句序闸才不是空转 ──────────────────
class BeatsReachesManifest(unittest.TestCase):
    """`_group_segments` 逐字段搬可选键，漏了 `beats`。

    修复前：source 里写了 beats → `build_from_structured` 收进 `Block.extra`
    → `pipeline` 分组时**没有这个键** → manifest 里没有 beats → `run.py` 的
    `_beats.audit` 读到的恒为 None → SKILL.md 承诺的"句序越界 exit 2"永不触发，
    `production_report.beats` 恒为 `{total:0, errors:0, warnings:0}`。
    而作者数出的句序与重分句后的句数天然不一致，这正是那道闸存在的理由。
    """

    def _group(self, seg):
        sents = [{"index": 0, "text": "甲。", "start_time": 0.0, "duration": 1.0},
                 {"index": 1, "text": "乙。", "start_time": 1.0, "duration": 1.0}]
        return PL._group_segments([seg], sents, {})[0]

    def test_beats_is_carried_into_manifest_segment(self):
        seg = {"id": "seg1", "title": "标题", "tagline": "补充阅读",
               "accent": "amber", "start": 0, "end": 2,
               "beats": [{"on": 0, "change": "开场"}]}
        self.assertEqual(self._group(seg).get("beats"),
                         [{"on": 0, "change": "开场"}])

    def test_passthrough_list_names_every_key_manifest_schema_accepts(self):
        """搬运清单是**枚举式**的：漏一个键不报错。所以清单本身要有锁。

        拿 `_manifest_schema` 认的段落级可选键对账——那边加了一个透传字段，
        这边没跟上，这条先红（而不是等某个作者写了 beats 才发现闸是空的）。
        """
        import _manifest_schema as MS
        schema = _source_text("_manifest_schema.py")
        accepted = set()
        for key in ("speed", "layout", "voice_id", "voice_style", "takeaway",
                    "turns", "beats"):
            if key in schema:
                accepted.add(key)
        self.assertTrue(accepted, "_manifest_schema 里找不到已知的可选段落键，"
                                  "这条对账本身失效了")
        for key in sorted(accepted):
            self.assertIn(key, PL.PASSTHROUGH_SEG_KEYS,
                          f"_manifest_schema 认 '{key}'，但 pipeline 没搬它")

    def test_out_of_range_beat_is_reported_once_beats_survive(self):
        """端到端语义：manifest 里带着越界 on 时，audit 必须报出来。"""
        manifest = {"segments": [{
            "id": "seg1", "title": "标题",
            "sentences": [{"index": 0, "text": "甲。", "start_time": 0.0,
                           "duration": 1.0}],
            "beats": [{"on": 7, "change": "越界拍"}],
        }]}
        # images.json 缺映射时 audit 会先报"没有导演拍点"，那条也是 error——
        # 这里只关心"越界被看见"：给一段空的 images 映射把它挡掉。
        errs, warns, total = B.audit(manifest, {"seg1": {"animate": []}})
        self.assertEqual(total, 1)
        self.assertTrue(errs, "beats 进了 manifest，越界就必须被 audit 报出来")
        self.assertTrue(any("越界" in e for e in errs), errs)


# ── 观感门禁：先把目录清空，再取帧 ────────────────────────────────────
class SnapshotKeepsTheFramesItJustTook(unittest.TestCase):
    """`_snapshot` 把 `rmtree(outdir)` 写在了 `subprocess.run` **之后**。

    修复前：官方 snapshot 成功写出帧 → 目录立刻被自己清空 → `_scan_frames`
    扫到空 → 抛"snapshot 没有产出任何帧"。于是 `--perceptual` 与
    `perceptual.py --project` 恒走"门禁未完成"分支：contact sheet、空页、
    死帧、换页时序这三条判词**从不执行**。SKILL.md 称它是"唯一一道在看画面的
    检查"，实际永远失明。注释写的是"先清空"，位置却反了。
    """

    def _write_frames(self, outdir, times):
        """模拟 hyperframes snapshot：在 run 被调用时产出帧。"""
        os.makedirs(outdir, exist_ok=True)
        for i, t in enumerate(times):
            with open(os.path.join(outdir, f"frame-{i}-at-{t:.2f}s.png"),
                      "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def test_frames_written_by_snapshot_survive(self):
        tmp = tempfile.mkdtemp(prefix="occam5-snap-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        outdir = os.path.join(tmp, "frames")

        def fake_run(cmd, **kw):
            self._write_frames(outdir, [1.0, 2.0])
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(P.subprocess, "run", side_effect=fake_run):
            pairs = P._snapshot("/tmp/nonexistent-project", [1.0, 2.0], outdir,
                                ["npx", "--yes", "hyperframes"])
        self.assertEqual([t for t, _ in pairs], [1.0, 2.0])
        self.assertEqual(len(pairs), 2)

    def test_stale_frames_are_cleared_before_the_run(self):
        """另一半契约：上一次的旧帧不能被当成这次的产出收下。"""
        tmp = tempfile.mkdtemp(prefix="occam5-stale-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        outdir = os.path.join(tmp, "frames")
        self._write_frames(outdir, [9.0])          # 上一次留下的

        def fake_run(cmd, **kw):
            self._write_frames(outdir, [1.0, 2.0])
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(P.subprocess, "run", side_effect=fake_run):
            pairs = P._snapshot("/tmp/nonexistent-project", [1.0, 2.0], outdir,
                                ["npx", "--yes", "hyperframes"])
        self.assertNotIn(9.0, [t for t, _ in pairs],
                         "旧帧混进了这一次的判据，量的就不是这次的编排了")

    def test_nonzero_exit_still_raises(self):
        """清空提前了，失败路径不能顺手被抹平。"""
        tmp = tempfile.mkdtemp(prefix="occam5-fail-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(P.subprocess, "run",
                               return_value=mock.Mock(returncode=1, stdout="",
                                                      stderr="boom")):
            with self.assertRaises(RuntimeError) as cm:
                P._snapshot("/tmp/p", [1.0], os.path.join(tmp, "frames"),
                            ["npx", "--yes", "hyperframes"])
        self.assertIn("boom", str(cm.exception))


# ── 取帧命令：与渲染侧同一条解析链 ────────────────────────────────────
class SnapshotCommandIsResolved(unittest.TestCase):
    """`_snapshot` 直接 `subprocess.run(["npx", ...])`，绕开了 `resolve_command`。

    修复前：Windows 上 npm 装的是 `npx.cmd`，CreateProcess 不执行 .cmd/.bat
    → FileNotFoundError → 报"找不到 npx"（Node 装了也报没装）。同仓
    `_render_backend.snapshot_frames` 走的是解析过的命令，两边对不上。
    """

    def test_command_goes_through_resolve_command(self):
        tmp = tempfile.mkdtemp(prefix="occam5-cmd-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        outdir = os.path.join(tmp, "frames")
        seen = {}

        def fake_resolve(cmd):
            seen["cmd"] = list(cmd)
            return ["/opt/node/npx"] + list(cmd)[1:]

        def fake_run(cmd, **kw):
            seen["run"] = list(cmd)
            os.makedirs(outdir, exist_ok=True)
            with open(os.path.join(outdir, "frame-0-at-1.00s.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(P, "resolve_command", side_effect=fake_resolve), \
                mock.patch.object(P.subprocess, "run", side_effect=fake_run):
            P._snapshot("/tmp/p", [1.0], outdir, ["npx", "--yes", "pkg"])
        # 交给 resolve_command 的必须是**拼完子命令参数**的整条命令：Windows 上
        # resolve 会把命令包进 `cmd /c "…"`，先解析再接参数，那些参数就落在
        # cmd.exe 自己的 argv 里、进不了 /c 的命令体（--at/-o 整批丢失）。
        self.assertEqual(seen.get("cmd"),
                         ["npx", "--yes", "pkg", "snapshot", "/tmp/p",
                          "--at", "1.00", "-o", outdir],
                         "_snapshot 没有把整条命令交给 resolve_command 解析")
        self.assertEqual(seen["run"][0], "/opt/node/npx")


# ── hyperframes 包名：只有一份真源 ────────────────────────────────────
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
        for name in sorted(os.listdir(SCRIPTS)):
            if not name.endswith(".py"):
                continue
            text = _source_text(name)
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


if __name__ == "__main__":
    unittest.main()
