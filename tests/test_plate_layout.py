#!/usr/bin/env python3
"""整页画布（layout:"canvas"）的契约、装配、时间线与门禁。

画布的代价是**一个字都不上画面**——所以它的字幕层（`subtitle:true`）是唯一
可选的一层文字：一屏一句、钉在页底、换句靠滚动。它的风险与并置对比同源——
**静默失联**：字幕层换了类名（`.verse` → `.plate-caption`），句子流的运行时按
`.verse` 找元素，找不到就什么也不报，成片里这一页的字幕永远是空的。所以这里的
测试分四组：

- 契约：layout 取值（含并入 canvas 的 `plate`）、缺图拦、结构性页能不能借用；
- 装配：图槽拉满整幅、句子流不生成、tagline 与标题层都不生成、字幕层按
  `subtitle` 开关出现；
- 时间线：**不参与擦除转场**，且不生成配图收拢/运镜；
- 门禁：字幕超过两行的句子被 `_plate_overlap_errors` 点名（字幕层不钳句，超了
  就是半句）。
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
                       is_full_bleed, needs_image, seg_layout, seg_subtitle)


def _src(text=None, title=None, layout="canvas"):
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
    def test_canvas_is_the_only_full_bleed_layout(self):
        self.assertEqual(FULL_BLEED_LAYOUTS, ("canvas",))
        self.assertTrue(is_full_bleed({"id": "seg-a", "layout": "canvas"}))
        self.assertFalse(is_full_bleed({"id": "seg-a"}))

    def test_retired_plate_normalizes_to_canvas(self):
        """`plate` 已并入 canvas：版式归一，字幕层靠 seg_subtitle 补回来。"""
        self.assertNotIn("plate", LAYOUT_VALUES)
        self.assertEqual(seg_layout({"id": "seg-a", "layout": "plate"}), "canvas")
        self.assertTrue(seg_subtitle({"id": "seg-a", "layout": "plate"}))

    def test_plate_is_refused_in_the_source(self):
        """稿件侧不吃历史取值：报错里要说出"已并入 canvas、字幕怎么写"。"""
        with self.assertRaises(ValueError) as cm:
            _validate_layout("plate", "seg-a", content=True, retired_ok=False)
        msg = str(cm.exception)
        self.assertIn("canvas", msg)
        self.assertIn("subtitle", msg)

    def test_subtitle_switch(self):
        """字幕层默认开；写 false 才关——它是画布的一个开关，不是另一种版式。"""
        self.assertTrue(seg_subtitle({"id": "seg-a", "layout": "canvas"}))
        self.assertFalse(seg_subtitle({"id": "seg-a", "layout": "canvas",
                                       "subtitle": False}))
        # 槽位页没有字幕层：这个键对它的版式不生效（渲染端只在画布页上问它）
        self.assertEqual(seg_layout({"id": "seg-a", "subtitle": True}), "slot")

    def test_canvas_is_not_agenda(self):
        """画布不是投影卡：它不印别人的目录，所以内容段可以借，结构页也可以。"""
        _validate_layout("canvas", "seg-a", content=True)
        self.assertEqual(seg_layout({"id": "opening", "layout": "canvas"}), "canvas")

    def test_canvas_counts_toward_image_coverage(self):
        """结构性页换画布后整页就是那张图：不计入覆盖率会报"全部命中"却出不了片。"""
        self.assertTrue(needs_image({"id": "opening", "layout": "canvas"}))
        self.assertFalse(needs_image({"id": "opening"}))


class TestAssembly(unittest.TestCase):
    def setUp(self):
        self.html = _render()
        self.card = self.html[self.html.index('id="seg-a"'):
                              self.html.index('id="seg-b"')]

    def test_full_bleed_image_slot(self):
        """图槽拉满整幅：槽位那套几何（圆角/外发光/内描边）在画布页一条都不生效。"""
        self.assertIn('[data-aspect] .seg-canvas .seg-image{', self.html)
        self.assertIn("seg-card seg-canvas", self.card)

    def test_caption_layer_not_verse(self):
        """句子流层（`.verse`）不生成——画布不排一墙句子在场上等。"""
        self.assertNotIn('class="verse"', self.card)
        self.assertIn('class="plate-caption"', self.card)
        # 每一句各占一格：列（.plate-lines）里 N 个等高槽，槽里一句
        self.assertEqual(self.card.count('class="plate-slot"'), 2)
        self.assertEqual(self.card.count('class="plate-line"'), 2)
        self.assertIn('id="pl-seg-a"', self.card)

    def test_no_tagline(self):
        """字幕层只有一句话：tagline 不进 DOM。"""
        self.assertNotIn('id="tag-seg-a"', self.card)

    def test_no_title_layer(self):
        """标题层连 DOM 都不生成：页面上的字只有页底那一条字幕。"""
        self.assertNotIn('id="title-seg-a"', self.card)
        self.assertNotIn("seg-title", self.card)

    def test_subtitle_off_leaves_no_text_layer(self):
        """subtitle:false = 屏幕上只有一个字都没有的那张图。"""
        src = _src()
        src["segments"][0]["subtitle"] = False
        html = _render(src=src)
        card = html[html.index('id="seg-a"'):html.index('id="seg-b"')]
        self.assertNotIn('class="plate-caption"', card)

    def test_landscape_renders_too(self):
        html = _render(aspect="landscape")
        self.assertIn('data-aspect="landscape"', html)
        self.assertIn('class="plate-caption"', html)

    def test_missing_image_is_refused(self):
        """缺图时拒绝**装配**而不是出一帧只有进度条的空页（gen 期还有一道门）。"""
        from html_renderer import generate_html
        m = H.make_manifest(_src())
        with self.assertRaises(ValueError) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                generate_html(m, "audio/combined.wav", images={}, aspect="portrait")
        self.assertIn("canvas", str(cm.exception))


class TestTimeline(unittest.TestCase):
    def setUp(self):
        self.html = _render()

    def test_no_wipe_transition(self):
        """图版是画面主导的页：硬切（0.01s 真 tween），不参与翻页擦除。"""
        line = [ln for ln in self.html.split("\n")
                if 'tl.fromTo("#seg-a"' in ln][0]
        self.assertIn("duration:0.01", line)

    def test_no_ken_burns(self):
        """配图不漂：画面主导的页，运镜交给作者（`animate` 的相机键）。"""
        drift = [ln for ln in self.html.split("\n")
                 if 'tl.fromTo("#img-seg-a > img"' in ln]
        self.assertEqual(drift, [])

    def test_image_entrance_skipped(self):
        """揭开即在位：不出现"擦出空页、再浮出图"的两段式。"""
        entrance = [ln for ln in self.html.split("\n")
                    if 'tl.from("#img-seg-a"' in ln]
        self.assertEqual(entrance, [])

    def test_no_title_entrance(self):
        """没有标题层，就没有标题入场：`#title-*` 一条补间都不该有。"""
        self.assertFalse(any('tl.from("#title-seg-a"' in ln
                             for ln in self.html.split("\n")))


class TestRuntimeHook(unittest.TestCase):
    """运行时在图版字幕上只管颜色，不管"哪一句在屏幕上"。

    "哪一句在屏幕上"归时间线（列位移）与窗口（overflow）。运行时再插一手
    visibility，位移还在路上的那几帧就会被"还没到开口"给藏掉——滚动读起来
    变成"先空一下、再啪一下"。
    """

    def setUp(self):
        self.html = _render()

    def test_runtime_syncs_plate_caption(self):
        self.assertIn("const plateSync", self.html)
        self.assertIn("plateSync(tt)", self.html)
        self.assertIn(".plate-line.plate-accent", self.html)

    def test_visibility_switch_is_gone(self):
        """换灯那一条必须消失：.plate-on 回来一次，滚动就被它切掉半截。"""
        self.assertNotIn("plate-on", self.html)


class TestGate(unittest.TestCase):
    """字幕层不钳句：一句超过两行会被 CSS 钳掉，屏幕上就是半句。生成期必须点名。"""

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

    def test_template_has_caption_block(self):
        """字幕层的几何只剩这几颗旋钮：标题那一批随图版一起撤掉了。"""
        from _template import load_template
        tpl = load_template()["plate"]
        for aspect in ("vertical", "landscape"):
            for key in ("captionFontSize", "captionMaxLines",
                        "captionInsetX", "captionInsetBottom"):
                self.assertIn(key, tpl[aspect], f"{aspect}.{key}")
            for gone in ("titleSize", "titleInsetTop", "titleScrimHeight"):
                self.assertNotIn(gone, tpl[aspect],
                                 f"{aspect}.{gone} 已随图版撤掉")


if __name__ == "__main__":
    unittest.main()
