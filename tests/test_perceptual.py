#!/usr/bin/env python3
"""观感门禁（`scripts/perceptual.py`）的判据测试。

这一层是**像素判据**，最容易出两种病，两种都用手工造的合成帧钉住：

1. **判不出来**（漏报）：合成一张"图槽里只有一行小字"的帧，它必须被抓成
   `canvas_thin`——这正是实测踩到的那个画面（一行 36px 字的 SVG 当配图，
   插槽 980×735，10 秒里图槽基本是空的，而 `check_svg`/`lint`/`Layout` 全绿）。
2. **乱报**（误报）：合格的折线图、agenda 卡的静止、运镜期间的缓慢变化
   都不许判。误报比漏报更致命——一道总在喊的门禁会被关掉。

合成帧不依赖 hyperframes/Chrome，CI 里秒级跑完。真实取帧那一路由
`run.py --check-frames` 的集成测试覆盖（需要 Node + Chrome）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest
from unittest import mock

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:                     # 可选依赖：缺了整份跳过（见 SKILL.md「环境」）
    HAS_PIL = False

if HAS_PIL:
    import perceptual as P

import _animate as EV

# 竖屏页底色（与模板 dark 主题的渐变中段接近，四条判据都不依赖精确色值）
BG = (10, 17, 24)
FG = (200, 225, 245)
W, H = 540, 720                          # 半尺寸即可，判据全是比例
SLOT = (25, 144, 490, 368)               # 竖屏槽位框（半尺寸）

SEGS = [
    {"id": "seg1", "layout": None,
     "sentences": [{"start_time": 0.0, "duration": 6.0}]},
    {"id": "seg2", "layout": "canvas",
     "sentences": [{"start_time": 6.0, "duration": 6.0}]},
]


def _frame(path, painter):
    img = Image.new("RGB", (W, H), BG)
    painter(img, ImageDraw.Draw(img))
    img.save(path)
    return path


def _title(img, d):
    d.text((25, 30), "标题一行", fill=FG)


def _rich_slot(img, d):
    """合格配图：大图区 + 粗线 + 刻度线，墨水占比 ~5%。"""
    x, y, w, h = SLOT
    d.rectangle((x + 30, y + 30, x + w - 30, y + h - 30), outline=FG, width=3)
    d.line((x + 40, y + h - 60, x + 160, y + 60, x + 300, y + h - 80, x + w - 50, y + 50),
           fill=(77, 208, 225), width=6)
    for i in range(5):
        d.text((x + 40, y + 60 + i * 40), f"{i}.0", fill=FG)


def _thin_slot(img, d):
    """实测踩到的画面：图槽里只有一行小字 + 一条细线。"""
    x, y, w, h = SLOT
    d.text((x + 30, y + h // 2), "数的扩张：为什么要引入负数", fill=FG)
    d.line((x + 30, y + h // 2 + 20, x + w - 30, y + h // 2 + 20), fill=FG, width=2)


def _verse(img, d, n=2):
    for i in range(n):
        d.text((25, H - 120 + i * 26), "字幕一句在这里。", fill=(150, 175, 200))


class TestPerceptual(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="perc-test-")
        self._n = 0

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, painter):
        self._n += 1
        return _frame(os.path.join(self.tmp, f"frame-{self._n:03d}-at-0s.png"), painter)

    def _run(self, painters, times, segs=SEGS):
        frames = [{"t": t, "path": self._mk(p)} for t, p in zip(times, painters)]
        # 让配图框跟着合成帧的尺寸走（半尺寸）
        old = P.slot_rect
        P.slot_rect = lambda aspect: (SLOT, (W, H))
        try:
            return P.analyze(frames, segs)
        finally:
            P.slot_rect = old

    # ── 抓得住 ────────────────────────────────────────────────
    def test_thin_slot_is_flagged(self):
        """图槽里只有一行小字 → canvas_thin。这是本次迭代的起点，最不能漏。"""
        def paint(img, d):
            _title(img, d); _thin_slot(img, d); _verse(img, d)
        r = self._run([paint] * 4, [0.6, 2.6, 4.6, 5.6])
        kinds = {i["kind"] for i in r["issues"]}
        self.assertIn("canvas_thin", kinds, r["issues"])

    def test_empty_canvas_page_is_flagged(self):
        """整页画布段什么都没有 → empty_page。"""
        def paint(img, d):
            pass                              # 纯背景
        r = self._run([paint] * 4, [6.6, 8.6, 10.6, 11.6])
        kinds = {i["kind"] for i in r["issues"]}
        self.assertIn("empty_page", kinds, r["issues"])

    def test_dead_seconds_is_flagged(self):
        """讲解段 6 秒画面不动 → dead_seconds。"""
        def paint(img, d):
            _title(img, d); _rich_slot(img, d); _verse(img, d)
        r = self._run([paint] * 4, [0.6, 2.6, 4.6, 5.6])
        kinds = {i["kind"] for i in r["issues"]}
        self.assertIn("dead_seconds", kinds, r["issues"])

    # ── 不乱报 ────────────────────────────────────────────────
    def test_rich_slot_passes_ink(self):
        """合格配图不许被判稀薄——这是最容易被误伤的一张。"""
        def paint(img, d):
            _title(img, d); _rich_slot(img, d); _verse(img, d)
        r = self._run([paint] * 4, [0.6, 2.6, 4.6, 5.6])
        self.assertNotIn("canvas_thin", {i["kind"] for i in r["issues"]},
                         r["issues"])

    def test_agenda_stillness_is_not_dead(self):
        """开屏/结尾投影卡本来就是一屏静态信息，不许判死帧。"""
        segs = [{"id": "opening", "layout": "agenda",
                 "sentences": [{"start_time": 0.0, "duration": 6.0}]}]

        def paint(img, d):
            for i in range(5):
                d.text((25, 120 + i * 40), f"0{i + 1} 章节标题", fill=FG)
        r = self._run([paint] * 4, [0.6, 2.6, 4.6, 5.6], segs=segs)
        self.assertNotIn("dead_seconds", {i["kind"] for i in r["issues"]},
                         r["issues"])

    def test_moving_content_is_not_dead(self):
        """画面自己在变（运镜推进）时不许判死帧。"""
        def make(i):
            def paint(img, d):
                _title(img, d)
                w = 300 + i * 40
                d.rectangle((25, 200, 25 + w, 380), fill=FG)
                _verse(img, d)
            return paint
        r = self._run([make(i) for i in range(4)], [0.6, 2.6, 4.6, 5.6])
        self.assertNotIn("dead_seconds", {i["kind"] for i in r["issues"]},
                         r["issues"])

    def test_switch_skew_respects_sampling_gap(self):
        """换页读数最多只能偏一个采样间隔——不许把网格误差判成声画不同步。"""
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 0.0, "duration": 4.0}]},
                {"id": "seg2", "layout": None,
                 "sentences": [{"start_time": 4.0, "duration": 8.0}]}]

        def before(img, d):
            _title(img, d); _rich_slot(img, d); _verse(img, d)

        def after(img, d):
            img.paste((90, 120, 150), (0, 0, W, H))   # 整屏换色 = 换页
            _title(img, d); _verse(img, d)

        # segs 必须传进去：不传就落到模块默认段（seg2 从 6.0s 起），那时
        # 4.3s 的换页相对 6.0s 偏了 1.7s，本就该报——测试却会以为自己在
        # 验"网格误差不许判成不同步"。判据此前恒不触发，这个漏传才没暴露。
        r = self._run([before, after], [3.9, 4.3], segs=segs)
        self.assertEqual([x for x in r["issues"] if x["kind"] == "switch_skew"], [])

    # ── 严重度与退出码：--strict 不许被"节奏舒缓"的片子打红 ─────────
    def test_every_issue_carries_severity(self):
        """判词自带 severity，且是登记表里的取值——不在报告里留一个空位。"""
        def paint(img, d):
            _title(img, d); _thin_slot(img, d); _verse(img, d)
        r = self._run([paint] * 4, [0.6, 2.6, 4.6, 5.6])
        self.assertTrue(r["issues"])
        for it in r["issues"]:
            self.assertIn(it["severity"], (P.ISSUE_ERROR, P.ISSUE_WARN), it)

    def test_strict_gate_counts_errors_only(self):
        """`--strict` 的判据是 error 档，不是"有任何判词"。

        warn 档现在没有判据在用，所以这里自己造一条假的来验分流——但
        **不能**拿一个真实判据的名字当假数据（前一版用 `temporal_budget`，
        那是登记了却从不发射的 kind，等于拿假数据证明真契约）。
        """
        warn_only = [dict(P._issue("_x", "seg1", "假 warn"), severity=P.ISSUE_WARN)]
        err = [P._issue("canvas_thin", "seg1", "真 error")]
        self.assertEqual(P._error_issues(warn_only), [])
        self.assertEqual([i["kind"] for i in P._error_issues(warn_only + err)],
                         ["canvas_thin"])
        # 未登记的判定按 error：新判据要人当真，不能因为忘了登记就静默放行
        self.assertEqual(len(P._error_issues([{"kind": "brand_new"}])), 1)

    def test_every_registered_kind_is_emitted(self):
        """登记表里的每个 kind 都必须有代码真的会发射它。

        反过来的漏洞更隐蔽：登记了、文档写了、测试造了假数据验分流——
        一切看着正常，唯独生产里没有这条判据。读者和 agent 都会以为它在。
        """
        import inspect
        src = inspect.getsource(P)
        for kind in P.ISSUE_SEVERITY:
            # 必须出现在 _issue(...) 的调用位置，而不是只出现在登记表里
            self.assertRegex(
                src, r'_issue\(\s*\n?\s*"' + kind + r'"',
                f"{kind} 只登记了、没有任何一处 _issue 发射它")

    def test_switch_before_first_segment_is_not_skew(self):
        """开屏页还挂着时的换页读数不许算到第一段头上。

        读数早于首段口播起点时，最近 span 恒为第一段、偏移是一大截负数，旧判据
        会写成"第一段声画不同步"——而那一刻第一段一秒都还没开始。开屏页的
        agenda 就是这种"先挂着"的页（结构性页不带 sentences，不在 spans 里）。
        """
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 5.0, "duration": 3.0}]}]

        def opening(img, d):
            _title(img, d); _rich_slot(img, d); _verse(img, d)

        def first(img, d):
            img.paste((90, 120, 150), (0, 0, W, H))
            _title(img, d); _verse(img, d)

        # 1.0s 开屏页取样，2.4s 换成第一段（第一段 5.0s 才开口）：偏移 2.6s
        # 远大于 1.4s 采样间隔 + 0.5s 容差，旧判据必然报 seg1 声画不同步。
        r = self._run([opening, first, first, first], [1.0, 2.4, 6.0, 7.4],
                      segs=segs)
        self.assertEqual(
            [x for x in r["issues"]
             if x["kind"] == "switch_skew" and x["seg"] == "seg1"], [],
            r["issues"])

    def test_contact_sheet_written(self):
        """拼图要落盘且格数够——交付前那一遍人工看的正是它。"""
        def paint(img, d):
            _title(img, d); _rich_slot(img, d)
        frames = [{"t": t, "path": self._mk(paint)} for t in (0.6, 2.6, 4.6)]
        out = os.path.join(self.tmp, "sheet.jpg")
        self.assertTrue(P.contact_sheet(frames, out))
        im = Image.open(out)
        self.assertGreaterEqual(im.size[0], 360 * 3)
        self.assertGreater(im.size[1], 100)



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


class TestGateIsolation(unittest.TestCase):
    """取帧层的失败不许把整个编排层带崩，也不许把旧帧当成新帧。

    这两条都不看像素，只钉 run_gate/_snapshot 的**失败语义**：
    `run_gate` 的契约是"不阻断、不 sys.exit"，而成片已渲染完之后才抛
    SystemExit，在日志里长得像渲染失败。
    """

    def test_missing_pillow_returns_error_not_systemexit(self):
        """缺 Pillow → 返回 {"error": ...}，而不是 SystemExit。"""
        import builtins
        tmp = tempfile.mkdtemp(prefix="perc-nopil-")
        try:
            _frame(os.path.join(tmp, "frame-00-at-1.00s.png"),
                   lambda img, d: _title(img, d))
            man = {"segments": [{"id": "seg1",
                                 "sentences": [{"start_time": 1.0, "duration": 3.0}]}]}
            real = builtins.__import__

            def guard(name, *a, **k):
                if name == "PIL" or name.startswith("PIL."):
                    raise ImportError("No module named 'PIL'")
                return real(name, *a, **k)

            builtins.__import__ = guard
            try:
                # 帧目录固定是 <out_dir>/frames，且要**逐点对齐**采样时刻才会
                # 被复用（复用逻辑见 run_gate）——用它在逻辑上等价于 --frames。
                fdir = os.path.join(tmp, "out", "frames")
                os.makedirs(fdir)
                for t in P.sample_times(man["segments"])["seg1"]:
                    _frame(os.path.join(fdir, f"frame-00-at-{t:.2f}s.png"),
                           lambda img, d: _title(img, d))
                r = P.run_gate("", man, os.path.join(tmp, "out"))
            finally:
                builtins.__import__ = real
            self.assertIn("error", r)
            self.assertIn("Pillow", r["error"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_snapshot_failure_is_not_masked_by_old_frames(self):
        """snapshot 非 0 退出、目录里还躺着上一次的帧 → 必须报错，不许拿旧帧充数。"""
        old = os.path.join(tempfile.mkdtemp(prefix="perc-old-"), "p")
        try:
            os.makedirs(old)
            _frame(os.path.join(old, "frame-00-at-1.00s.png"),
                   lambda img, d: _title(img, d))
            with self.assertRaises(RuntimeError):
                P._snapshot(".", [1.0], old,
                            [sys.executable, "-c", "import sys;sys.exit(3)"])
            self.assertFalse(os.path.isdir(old),
                             "取帧前必须先清空目录，否则旧帧会被当成这次的产出")
        finally:
            import shutil
            shutil.rmtree(os.path.dirname(old), ignore_errors=True)


@unittest.skipUnless(HAS_PIL, "观感门禁要吃像素，缺 Pillow 跑不了")
class SwitchSkewIsAlive(unittest.TestCase):
    """`switch_skew` 判据此前**恒不触发**，等于没有这道门禁。

    原因：候选先用同一条容差筛过一遍（`|段起点 - t| <= 采样间隔 + 容差`），
    再判 `|偏移| > 采样间隔 + 容差`——筛完剩下的条条都落在容差内，判据恒假。
    而"画面提前翻页、旁白还没开始"是肉眼一眼能看出来的毛病。
    """

    W, H = 320, 240
    SLOT = (30, 90, 180, 120)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="occam4-perc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._n = 0

    def _frame(self, painter):
        self._n += 1
        p = os.path.join(self.tmp, "f%02d.png" % self._n)
        img = Image.new("RGB", (self.W, self.H), (12, 16, 24))
        painter(ImageDraw.Draw(img))
        img.save(p)
        return p

    def _analyze(self, painters, times, segs):
        frames = [{"t": t, "path": self._frame(p)}
                  for t, p in zip(times, painters)]
        old = P.slot_rect
        P.slot_rect = lambda aspect: (self.SLOT, (self.W, self.H))
        try:
            return P.analyze(frames, segs)
        finally:
            P.slot_rect = old

    @staticmethod
    def _before(d):
        d.rectangle((20, 20, 300, 80), fill=(200, 200, 200))

    @staticmethod
    def _after(d):
        d.rectangle((0, 0, 320, 240), fill=(90, 120, 150))   # 整屏换色 = 换页
        d.rectangle((20, 20, 300, 80), fill=(200, 200, 200))

    def test_page_switch_ahead_of_voice_is_flagged(self):
        """画面在 4.3s 翻页、下一段旁白 8.0s 才开始 ⇒ 判"声画不同步"。

        修复前：判据恒假，永远不报。
        """
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 0.0, "duration": 4.0}]},
                {"id": "seg2", "layout": None,
                 "sentences": [{"start_time": 8.0, "duration": 4.0}]}]
        r = self._analyze([self._before, self._after], [3.9, 4.3], segs)
        kinds = {i["kind"] for i in r["issues"]}
        self.assertIn("switch_skew", kinds, r["issues"])

    def test_switch_within_sampling_gap_is_not_flagged(self):
        """反向：换页读数只偏一个采样间隔时**不许**报（那是网格误差）。"""
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 0.0, "duration": 4.0}]},
                {"id": "seg2", "layout": None,
                 "sentences": [{"start_time": 4.0, "duration": 8.0}]}]
        r = self._analyze([self._before, self._after], [3.9, 4.3], segs)
        kinds = {i["kind"] for i in r["issues"]}
        self.assertNotIn("switch_skew", kinds, r["issues"])


class EvolveHiddenGuard(unittest.TestCase):
    """「起始帧全空」这道守卫此前**结构性不可达**。

    - painter 收全部元素、hidden 只收有 id 的 ⇒ 任何无 id 的装饰件都能让
      `len(hidden) == len(painter)` 不成立；
    - `<defs>` 的子元素（linearGradient 等）进了 painter 却永远没有
      opacity=0，而真实 SVG 几乎都带 `<defs>`；
    - 「没声明 opacity」被 `or 0` 当成隐藏，于是**可见**的页也被判成全空。
    """

    SVG_WITH_DEFS = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<defs><linearGradient id="g"/></defs>'
        '<rect id="a" opacity="0"/><rect id="b" style="opacity:0"/></svg>')
    SVG_VISIBLE = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect id="a"/><rect id="b"/></svg>')

    def test_defs_does_not_silence_the_guard(self):
        """带 <defs> 的真实 SVG 仍要报（修复前：defs 里的渐变让判据永不成立）。"""
        _, warns = EV.check_animate(self.SVG_WITH_DEFS, {"steps": [
            {"at_time": 2.5, "target": "#a", "to": {"opacity": 1}}]}, "seg-a", full_bleed=False)
        self.assertTrue(any("空框" in w for w in warns), warns)

    def test_at_time_anchor_is_not_treated_as_early(self):
        """`at_time=2.5` 不在段首 ⇒ 不算"段首点亮"（修复前恒算早、守卫沉默）。"""
        _, warns = EV.check_animate(self.SVG_WITH_DEFS, {"steps": [
            {"at_time": 2.5, "target": "#a", "to": {"opacity": 1}}]}, "seg-a", full_bleed=False)
        self.assertTrue(warns)

    def test_visible_structure_is_not_called_hidden(self):
        """反向：没写 opacity 的元素是**可见**的，不许判成全空。

        修复前 `(_declared_opacity(el) or 0) <= 0` 把"没声明"当成 0。
        """
        _, warns = EV.check_animate(self.SVG_VISIBLE, {"steps": [
            {"at": 1.5, "target": "#a", "to": {"fill": "#f59e0b"}}]}, "seg-a", full_bleed=False)
        self.assertEqual(warns, [])

    def test_head_beat_still_counts_as_early(self):
        """反向：`at=0`（第 0 句 = 段首）点亮时不该报。"""
        _, warns = EV.check_animate(self.SVG_WITH_DEFS, {"steps": [
            {"at": 0, "target": "#a", "set": {"opacity": 1}}]}, "seg-a", full_bleed=False)
        self.assertEqual(warns, [])

    def test_camera_keys_come_from_one_place(self):
        """相机键清单只有一份（在 `_animate`，契约层与渲染端都读它）。"""
        import _images_schema as _ims
        self.assertIn("rotation", EV.CAMERA_KEYS)
        self.assertFalse(hasattr(_ims, "_EVOLVE_CAMERA_KEYS"),
                         "契约层不该再留一份相机键清单")


if __name__ == "__main__":
    unittest.main()
