#!/usr/bin/env python3
"""图版版式（layout:"plate"）的契约、装配与门禁。

图版是"图主导 + 一层最小的文字"：满幅主视觉 + 标题 + **一句**字幕。它的风险
与并置对比同源——**静默失联**：字幕层换了类名（`.verse` → `.plate-caption`），
句子流的运行时按 `.verse` 找元素，找不到就什么也不报，成片里这一页的字幕永远是
空的。所以这里的测试分四组：

- 契约：layout 取值、缺图拦、结构性页能不能借用；
- 装配：图槽拉满整幅、句子流不生成、tagline 不生成、标题层照常生成；
- 时间线：**不参与擦除转场**（与整页画布同一档），且不生成配图收拢/运镜；
- 门禁：字幕层两行以上的句子被 `canvas_layout_errors` 点名（图版不滚）。
"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _helpers as H  # noqa: E402
from _segments import (FULL_BLEED_LAYOUTS, LAYOUT_VALUES, _validate_layout,  # noqa: E402
                       is_full_bleed, needs_image, seg_layout)


def _src(text=None, title=None, layout="plate"):
    s = H.sample_source()
    s["segments"][0]["layout"] = layout
    if text:
        s["segments"][0]["text"] = text
    if title:
        s["segments"][0]["title"] = title
    return s


def _render(aspect="portrait", src=None, images=None):
    from html_renderer import generate_html
    m = H.make_manifest(src or _src())
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, aspect=aspect,
                             theme="dark")


class TestLayoutValue(unittest.TestCase):
    def test_plate_is_a_valid_layout(self):
        self.assertIn("plate", LAYOUT_VALUES)
        _validate_layout("plate", "seg-a", content=True)     # 不抛

    def test_plate_is_full_bleed(self):
        self.assertIn("plate", FULL_BLEED_LAYOUTS)
        self.assertTrue(is_full_bleed({"id": "seg-a", "layout": "plate"}))
        self.assertFalse(is_full_bleed({"id": "seg-a"}))

    def test_plate_is_not_agenda(self):
        """图版不是投影卡：它不印别人的目录，所以内容段可以借，结构页也可以。"""
        _validate_layout("plate", "seg-a", content=True)
        self.assertEqual(seg_layout({"id": "opening", "layout": "plate"}), "plate")

    def test_plate_counts_toward_image_coverage(self):
        """结构性页换图版后整页就是那张图：不计入覆盖率会报"全部命中"却出不了片。"""
        self.assertTrue(needs_image({"id": "opening", "layout": "plate"}))
        self.assertFalse(needs_image({"id": "opening"}))


class TestAssembly(unittest.TestCase):
    def setUp(self):
        self.html = _render()
        self.card = self.html[self.html.index('id="seg-a"'):
                              self.html.index('id="seg-b"')]

    def test_full_bleed_image_slot(self):
        """图槽盒子与整页画布同一套几何：CSS 那条规则必须同时命中两块。"""
        self.assertIn('[data-aspect] .seg-plate .seg-image{', self.html)
        self.assertIn("seg-card seg-plate", self.card)

    def test_caption_layer_not_verse(self):
        """句子流层（`.verse`）不生成——生成器那套滚动/焦点衰减在图版上是错的。"""
        self.assertNotIn('class="verse"', self.card)
        self.assertIn('class="plate-caption"', self.card)
        # 每一句各占一行，运行时只显示当前那句
        self.assertEqual(self.card.count('class="plate-line"'), 2)

    def test_no_tagline(self):
        """图版的文字层只有标题 + 一句字幕：tagline 不入 DOM。"""
        self.assertNotIn('id="tag-seg-a"', self.card)

    def test_title_layer_present(self):
        self.assertIn('id="title-seg-a"', self.card)
        self.assertIn('class="plate-head"', self.card)

    def test_plate_title_has_no_line_clamp(self):
        """图版标题压在图上、没有定高标题区：不设钳制（与槽位竖屏相反）。"""
        self.assertNotIn("-webkit-line-clamp:none", self.card)

    def test_landscape_renders_too(self):
        html = _render(aspect="landscape")
        self.assertIn('data-aspect="landscape"', html)
        self.assertIn('class="plate-caption"', html)

    def test_missing_image_is_refused(self):
        """缺图时拒绝**装配**而不是出一帧只有标题的空页（gen 期还有一道门）。"""
        from html_renderer import generate_html
        m = H.make_manifest(_src())
        with self.assertRaises(ValueError) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                generate_html(m, "audio/combined.wav", images={}, aspect="portrait")
        self.assertIn("plate", str(cm.exception))


class TestTimeline(unittest.TestCase):
    def setUp(self):
        self.html = _render()

    def test_no_wipe_transition(self):
        """图版是画面主导的页：硬切（0.01s 真 tween），不参与翻页擦除。"""
        line = [ln for ln in self.html.split("\n")
                if 'tl.fromTo("#seg-a"' in ln][0]
        self.assertIn("duration:0.01", line)

    def test_no_ken_burns(self):
        """配图不漂：画面主导的页，运镜交给作者（director / evolve）。"""
        drift = [ln for ln in self.html.split("\n")
                 if 'tl.fromTo("#img-seg-a > img"' in ln]
        self.assertEqual(drift, [])

    def test_image_entrance_skipped(self):
        """揭开即在位：不出现"擦出空页、再浮出图"的两段式。"""
        entrance = [ln for ln in self.html.split("\n")
                    if 'tl.from("#img-seg-a"' in ln]
        self.assertEqual(entrance, [])

    def test_title_still_enters(self):
        """标题是 HTML 文字层，照常按段序轮换节奏入场。"""
        self.assertTrue(any('tl.from("#title-seg-a"' in ln
                            for ln in self.html.split("\n")))


class TestRuntimeHook(unittest.TestCase):
    def setUp(self):
        self.html = _render()

    def test_runtime_syncs_plate_caption(self):
        """运行时必须真有那条归位逻辑，否则一摞行全叠在屏幕上。"""
        self.assertIn("const plateSync", self.html)
        self.assertIn("plateSync(tt)", self.html)
        self.assertIn(".plate-line.plate-on", self.html)


class TestGate(unittest.TestCase):
    """图版不滚：一句超过两行会被 CSS 钳掉，屏幕上就是半句。生成期必须点名。"""

    def _errors(self, text, title="短标题"):
        from gen_hyperframes import _plate_overlap_errors
        m = H.make_manifest(_src(text=text, title=title))
        # 开场段永远排在最前（且是 agenda），换过 layout 的那段是 seg-a——按 id 取，
        # 不按位置取，否则量到的是开场那 11 个字，什么都不会报。
        seg = [s for s in m["segments"] if s["id"] == "seg-a"][0]
        return _plate_overlap_errors([seg], 1080, 1440, "vertical")

    def test_long_caption_is_named(self):
        # per_line = (1080-128)//42 = 22；三行 = 67 字以上
        errs = self._errors("这" * 70)
        self.assertTrue(errs)
        self.assertIn("captionMaxLines", errs[0])

    def test_two_lines_pass(self):
        self.assertEqual(self._errors("这" * 40), [])

    def test_template_has_plate_block(self):
        from _template import load_template
        tpl = load_template()["plate"]
        for aspect in ("vertical", "landscape"):
            for key in ("titleSize", "captionFontSize", "captionMaxLines",
                        "captionInsetX", "captionInsetBottom"):
                self.assertIn(key, tpl[aspect], f"{aspect}.{key}")


if __name__ == "__main__":
    unittest.main()
