#!/usr/bin/env python3
"""整页画布的字幕层换句：一列滚过一条**固定的边**，一句一步。

整页画布 + `subtitle:true` 是"满幅图 + 页底一句字幕"，一句 2 秒——换句因此是
成片里**次数最多的动作**（3 分钟口播两百次，而段落转场只有七八次）。它曾经是
`visibility` 的开关：旧句啪一下灭、新句啪一下亮，整页其它每一处都有编排，唯独
字幕是换灯。

现在换成滚动：`.plate-clip` 是一个固定高度的窗口，全部句子在 `.plate-lines` 里
排成一列、每句占一个等高槽，换句时**只动整列**，位移恒等于槽高。边是容器的边，
不需要 clip-path——与句子流（`.verse-clip`）同一套机制。

这份文件验的是判据，不是实现细节：

1. **一步 = 一个槽高** —— 位移恒等于槽高、方向恒向上，末句之后不再滚；
2. **首句不滚** —— 满幅页进场是硬切，第一帧字幕必须在位；
3. **口径从句子流借** —— 时长读 `subtitle.relay`、缓动读 `subtitle.scroll.ease`，
   锚点是新句开口（`start_time + onset`），都与 `.verse` 同源；
4. **位移只写在列上** —— 行自己一条补间都没有，两句若各有各的位移，交叠期两条
   位移会在画面上互相穿过。
"""
import contextlib
import io
import re
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from html_renderer import _plate_slot_px, generate_html

# tl.to("#pl-seg-a",{y:-132.3,duration:0.32,ease:"sine.out"},4.80)
_SCROLL_RE = re.compile(
    r'tl\.to\("#pl-(?P<sid>[A-Za-z0-9_-]+)",\{y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')

_ASPECT_KEY = {"portrait": "vertical", "landscape": "landscape"}


def _src(text=None, layout="canvas"):
    s = H.sample_source()
    s["segments"][0]["layout"] = layout
    if text:
        s["segments"][0]["text"] = text
    return s


def _manifest(src=None, onset=None):
    m = H.make_manifest(src or _src())
    if onset is not None:
        # 句表有两份（全局平表与段内副本，make_manifest 复制过），两处都要写，
        # 否则"开口"这个口径在 DOM 与时间线上会取到两个值。
        for sent in m["sentences"]:
            sent["onset"] = onset
        for seg in m["segments"]:
            for sent in seg["sentences"]:
                sent["onset"] = onset
    return m


def _render(src=None, aspect="portrait", tpl=None, onset=None):
    m = _manifest(src, onset=onset)
    imgs = H.sample_images(m)
    patch = (mock.patch("html_renderer.load_template", return_value=tpl) if tpl
             else contextlib.nullcontext())
    with patch:
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav", images=imgs,
                                 aspect=aspect, theme="dark")


def _tweens(html):
    """只收起拍段（seg-a）那条列的滚动，且按时刻排序。

    样稿里的 seg-b 也是满幅页、也有字幕层，它自己那一条滚动会混进来；末句之后
    "不再滚"的判据只能对着同一段的那几条算。
    """
    found = [m for m in _SCROLL_RE.finditer(html) if m.group("sid") == "seg-a"]
    return sorted(found, key=lambda m: float(m.group("at")))


def _slot(aspect="portrait", tpl=None):
    tpl = tpl or _template.load_template()
    return _plate_slot_px(tpl["plate"][_ASPECT_KEY[aspect]])


class StepDistance(unittest.TestCase):
    """一步 = 一个槽高。槽高是滚动距离与窗口高度的**唯一真源**。"""

    def setUp(self):
        self.html = _render(_src("这是第一句内容。这是第二句内容。这是第三句内容。"))
        self.tweens = _tweens(self.html)

    def test_one_step_per_sentence_after_the_first(self):
        self.assertEqual(len(self.tweens), 2)           # 三句两步

    def test_step_equals_the_slot(self):
        slot = _slot()
        for i, m in enumerate(self.tweens, start=1):
            self.assertAlmostEqual(float(m.group("y")), -i * slot, places=1,
                                   msg=f"第 {i} 步的位移应当等于 i × 槽高")

    def test_distance_is_always_upward_and_growing(self):
        ys = [float(m.group("y")) for m in _tweens(_render(
            _src("这是第一句内容。这是第二句内容。这是第三句内容。这是第四句内容。这是第五句内容。")))]
        self.assertEqual(len(ys), 4)
        for a, b in zip(ys, ys[1:]):
            self.assertLess(b, a, "位移必须单调向上（负方向），回弹就是抖")

    def test_no_step_beyond_the_last_sentence(self):
        slot = _slot()
        self.assertLessEqual(abs(float(self.tweens[-1].group("y"))), 2 * slot + 0.05,
                             "末句之后不再滚：最后一步停在末句那一格")

    def test_slot_follows_the_template(self):
        """改了槽隙，位移与窗口一起跟着变——两处各推一遍公式就会有一处落后。"""
        tpl = _template.load_template()
        tpl["plate"]["vertical"]["captionSlotGapFrac"] = 0.8
        html = _render(_src("这是第一句内容。这是第二句内容。"), tpl=tpl)
        want = _slot(tpl=tpl)
        for m in _tweens(html):
            self.assertAlmostEqual(float(m.group("y")), -want, places=1)
        self.assertIn(f"--ctv-pl-slot:{want}px;", html)

    def test_window_height_is_the_same_number(self):
        """窗口高度与位移同读 --ctv-pl-slot：槽高只有一个真源。"""
        slot = _slot()
        self.assertIn(f"--ctv-pl-slot:{slot}px;", self.html)
        self.assertIn(
            "[data-aspect] .seg-canvas .plate-clip{position:relative;"
            "height:var(--ctv-pl-slot);overflow:hidden}", self.html)
        self.assertIn("[data-aspect] .seg-canvas .plate-slot{position:relative;"
                      "height:var(--ctv-pl-slot)}", self.html)

    def test_landscape_has_its_own_slot(self):
        html = _render(_src("这是第一句内容。这是第二句内容。"), aspect="landscape")
        want = _slot("landscape")
        self.assertIn(f"--ctv-pl-slot:{want}px;", html)
        for m in _tweens(html):
            self.assertAlmostEqual(float(m.group("y")), -want, places=1)


class FirstSentenceStandsStill(unittest.TestCase):
    """首句不滚：满幅页进场是硬切，第一帧字幕必须在位。"""

    def test_first_sentence_has_no_step(self):
        html = _render(_src("这是第一句内容。这是第二句内容。这是第三句内容。"), onset=0.2)
        at = [float(m.group("at")) for m in _tweens(html)]
        # 样稿带开场句，这三句开口在 2.6 / 5.0 / 7.4（dur 2.0 + gap 0.4 + onset 0.2）
        self.assertEqual([round(a, 2) for a in at], [5.0, 7.4])
        self.assertNotIn(2.6, at, "首句开口那一刻不该有滚动")

    def test_single_sentence_plate_does_not_scroll(self):
        self.assertEqual(_tweens(_render(_src("这是唯一的一句内容。"))), [])


class BorrowedFromTheVerse(unittest.TestCase):
    """换句的口径从句子流借：两种版式的换句手感不各存一份。"""

    def test_duration_is_the_verse_relay(self):
        want = _template.load_template()["layout"]["vertical"]["subtitle"]["relay"]
        for m in _tweens(_render(_src("这是第一句内容。这是第二句内容。"))):
            self.assertAlmostEqual(float(m.group("dur")), float(want), places=2)

    def test_ease_is_the_verse_scroll_ease(self):
        want = _template.load_template()["subtitle"]["scroll"]["ease"]
        got = {m.group("ease") for m in _tweens(_render(_src("这是第一句内容。这是第二句内容。")))}
        self.assertEqual(got, {want})

    def test_relay_zero_disables_the_scroll(self):
        """relay 0 = 明确不要接力，滚动跟着一起关（与句子流同一条闸门）。"""
        tpl = _template.load_template()
        tpl["layout"]["vertical"]["subtitle"]["relay"] = 0
        self.assertEqual(_tweens(_render(_src("这是第一句内容。这是第二句内容。"), tpl=tpl)), [])

    def test_anchored_at_the_onset_not_the_block_start(self):
        """锚点是开口（start_time + onset），不是音频块起点——早半拍就是字幕先走。"""
        html = _render(_src("这是第一句内容。这是第二句内容。"), onset=0.25)
        m = _tweens(html)[0]
        # 第二句（首句不滚）：start 4.8 + onset 0.25
        self.assertAlmostEqual(float(m.group("at")), 5.05, places=2)
        self.assertIn('data-t0="5.05"', html)


class OnlyTheColumnMoves(unittest.TestCase):
    """位移只写在列容器上；行自己一条补间都没有。"""

    def setUp(self):
        self.html = _render(_src("这是第一句内容。这是第二句内容。这是第三句内容。"))

    def test_lines_have_no_tween(self):
        for ln in self.html.split("\n"):
            if "plate-line" in ln:
                self.assertFalse(ln.strip().startswith("tl."),
                                 f"行不该进时间线：{ln.strip()}")

    def test_column_is_the_only_target(self):
        sids = {m.group("sid") for m in _tweens(self.html)}
        self.assertEqual(sids, {"seg-a"})

    def test_column_has_no_css_transform(self):
        """GSAP 独占位移那一维：CSS 再声明 transform 就是 _css_guard 那条硬约束。"""
        for ln in self.html.split("\n"):
            if ".plate-lines{" in ln:
                self.assertNotIn("transform", ln)

    def test_column_holds_one_slot_per_sentence(self):
        card = self.html[self.html.index('id="seg-a"'):self.html.index('id="seg-b"')]
        self.assertEqual(card.count('class="plate-slot"'), 3)
        # 每一格里一句：列是 N 格，不是一摞叠在一起的 N 行
        self.assertEqual(card.count('class="plate-line"'), 3)


class OnlyWhereTheSubtitleLayerExists(unittest.TestCase):
    """滚动是字幕层的事：没有这一层的页没有这个窗口，也不该有这条补间。"""

    def test_slot_segment_has_no_scroll(self):
        self.assertEqual(_tweens(_render(_src(layout=None))), [])

    def test_subtitle_off_has_no_scroll(self):
        src = _src("这是第一句内容。这是第二句内容。")
        src["segments"][0]["subtitle"] = False
        self.assertEqual(_tweens(_render(src)), [])

    def test_retired_plate_layout_still_scrolls(self):
        """`layout:"plate"` 的老稿件：版式并入 canvas，字幕层不能被合并弄丢。"""
        html = _render(_src("这是第一句内容。这是第二句内容。", layout="plate"))
        self.assertEqual(len(_tweens(html)), 1)
        self.assertIn('class="plate-caption"', html)


class Determinism(unittest.TestCase):
    def test_two_generations_are_byte_identical(self):
        a = _render(_src("这是第一句内容。这是第二句内容。这是第三句内容。"))
        b = _render(_src("这是第一句内容。这是第二句内容。这是第三句内容。"))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
