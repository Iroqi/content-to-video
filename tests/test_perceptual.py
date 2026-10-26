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
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:                     # 可选依赖：缺了整份跳过（见 SKILL.md「环境」）
    HAS_PIL = False

if HAS_PIL:
    import perceptual as P

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

        r = self._run([before, after], [3.9, 4.3])
        self.assertEqual([x for x in r["issues"] if x["kind"] == "switch_skew"], [])

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


if __name__ == "__main__":
    unittest.main()
