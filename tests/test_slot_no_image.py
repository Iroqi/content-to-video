"""无图降级档（`--slot-no-image`）：没生图能力时也能出一版能看的成片。

背景：槽位版式把画面一分为二——上半是图槽、下半是句子流。宿主没有生图能力
（或这一段确实不该配图）时，原本的产物是"底下一块空灰框"：既不是图，也不像
留白，读起来像渲染失败。这一档撤掉图槽、把腾出来的空间交给句子流。

三条必须守住的东西（都真的错过）：
  1. **只有槽位段挂这个档**。agenda 卡（开屏/结尾）的 `has_image` 被
     `and not is_agenda` 强制成 False——判据只写 `not has_image` 的话，
     开屏/结尾页会挂上 `text-only`（而它们本来就不出图槽、布局也不该变），
     真正缺图的内容段反而一个都挂不上。
  2. **只有没图的段才挂**。有图的段照旧出图槽；这一档不许把作者画好的图
     变成"不显示"。
  3. **满幅页不受影响**。canvas/plate 缺图在生成期就已拦下，它们没有
     "图槽腾出来的那块"，铺满规则对它们是一整套错的几何。

另外两条几何约束来自 `slot_verse_geometry`：
  · 放宽后的窗口高不能顶进标题带（宁可留白，也不压标题）；
  · 横屏不放宽（左栏文档流，放大只会让字幕在栏里垂出去）。
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import wave

import _helpers as H
from html_renderer import generate_html, slot_verse_geometry
from _template import load_template


def _render(images=None, **kw):
    """样例稿件渲染；images 缺省时**只给画布段配图**，让槽位段真的缺图。"""
    m = H.make_manifest()
    if images is None:
        images = {"seg-b": {"src": "images/seg-b.svg"}}
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        html = generate_html(m, "audio/combined.wav", images=images,
                             aspect=kw.pop("aspect", "portrait"),
                             theme="dark", **kw)
    return html, err.getvalue()


def _cards(html):
    """卡片 id → 类名串（只取 .seg-card，按出现顺序）。"""
    out = {}
    for mo in re.finditer(r'<div id="([^"]+)" class="([^"]*seg-card[^"]*)"', html):
        out[mo.group(1)] = mo.group(2)
    return out


def _verse_h(html):
    return int(re.search(r"--ctv-verse-h:(\d+)px", html).group(1))


class OnlySlotCardsWearTheClass(unittest.TestCase):
    def test_agenda_cards_never_take_the_degraded_class(self):
        """开屏/结尾页不出图槽，也不该被这一档改写布局。

        证伪：判据曾写成 `not has_image`，而 agenda 卡的 has_image 恒为
        False（上面那条 `and not is_agenda`）——夹具里所有段都有图时，
        `text-only` 只落在两张 agenda 卡上，槽位卡一张都没挂上。
        """
        html, _ = _render(images=H.sample_images(H.make_manifest()),
                          slot_no_image=True)
        cards = _cards(html)
        self.assertIn("opening", cards)
        for sid in ("opening", "closing"):
            self.assertNotIn("text-only", cards[sid],
                             f"{sid} 是 agenda 卡（不出图槽），不该挂降级类")

    def test_missing_slot_segment_gets_the_class(self):
        html, _ = _render(slot_no_image=True)
        cards = _cards(html)
        self.assertIn("text-only", cards["seg-a"],
                      "缺图的槽位段 seg-a 必须挂上降级类（句子流要铺进图槽那块）")

    def test_segment_with_image_keeps_its_image_slot(self):
        """有图的段照旧出图槽——这一档不许把作者画好的图变成"不显示"。"""
        both = H.sample_images(H.make_manifest())
        html, _ = _render(images=both, slot_no_image=True)
        self.assertNotIn("text-only", _cards(html)["seg-a"])
        self.assertIn('id="img-seg-a"', html)

    def test_full_bleed_pages_are_untouched(self):
        """canvas 页不受这一档影响：既不加类，也不进铺满规则。"""
        both = H.sample_images(H.make_manifest())
        html, _ = _render(images=both, slot_no_image=True)
        self.assertNotIn("text-only", _cards(html)["seg-b"])
        self.assertIn("seg-canvas", _cards(html)["seg-b"])

    def test_class_absent_without_the_flag(self):
        """默认档一个 text-only 都不该有（CSS 规则常驻，但没人穿）。"""
        html, _ = _render()
        self.assertNotIn("text-only", html.split("<style", 1)[1].split("</style>", 1)[1])


class Geometry(unittest.TestCase):
    def test_portrait_window_grows_into_the_image_slot(self):
        plain, _ = _render()
        degraded, _ = _render(slot_no_image=True)
        self.assertLess(_verse_h(plain), _verse_h(degraded),
                        "竖屏降级档的句子流窗口没有放大——图槽那块白撤了")

    def test_landscape_window_is_not_enlarged(self):
        """横屏是左栏文档流：放大窗口只会让字幕在栏里垂出边界。"""
        plain, _ = _render(aspect="landscape")
        degraded, _ = _render(aspect="landscape", slot_no_image=True)
        self.assertEqual(_verse_h(plain), _verse_h(degraded))

    def test_window_never_reaches_into_the_title_band(self):
        """放宽后的窗口 + 顶部起排，必须仍落在内容区里（不许压标题）。

        判据用几何本身而不是肉眼：内容区上沿 = 标题带底边，下沿 = 画布高 −
        卡片下 padding；窗口高不得超过这一段。
        """
        tpl = load_template()
        lay = tpl["layout"]["vertical"]
        h, _bottom = slot_verse_geometry(tpl, "vertical", True)
        title = lay["title"]
        tagline = lay["tagline"]
        title_area = (title["maxLines"] * title["fontSize"]
                      * tpl["typography"]["titleLineHeight"]
                      + tagline["marginTop"]
                      + tagline["fontSize"] * tagline["lineHeight"])
        content_top = title["top"] + title_area
        content_h = tpl["canvas"]["vertical"]["height"] - content_top
        self.assertLessEqual(h, content_h,
                             "句子流窗口长过了内容区——会顶进标题带")

    def test_tight_canvas_falls_back_to_the_original_window(self):
        """腾出来的空间装不下更大窗口时回原值：宁可留白，不要压标题。"""
        tpl = load_template()
        plain_h, _ = slot_verse_geometry(tpl, "vertical", False)
        tpl["canvas"]["vertical"]["height"] = 600   # 画布矮到放不下
        h, _ = slot_verse_geometry(tpl, "vertical", True)
        self.assertEqual(h, plain_h)


class DegradedCssReachesTheComposition(unittest.TestCase):
    """铺满规则必须真的进了产物 CSS，且是**拉伸**而非居中。

    两种写法都试过，观感差一个档：
      · `height:var(--ctv-verse-h)` 单独用——.verse 仍钉底，内容却从窗口顶
        往下排，字幕跑到页面上半截、下面空一大片；
      · 加 `justify-content:center`——句子被摆到页面腰线，像海报不像"接着
        标题往下念"。
    定稿是 top/bottom 同时写（绝对定位下并存 = 拉伸）+ flex-start。
    """

    def test_rules_are_present(self):
        html, _ = _render(slot_no_image=True)
        style = html.split("<style", 1)[1].split("</style>", 1)[0]
        self.assertIn('.seg-card.text-only .verse{top:var(--ctv-v-img-top);'
                      'bottom:var(--ctv-v-verse-bottom);height:auto', style)
        self.assertIn('.seg-card.text-only .verse>.verse-clip{width:100%}', style)
        self.assertIn('.seg-card.text-only .verse::before{content:none}', style)

    def test_verse_starts_from_the_top_not_the_middle(self):
        html, _ = _render(slot_no_image=True)
        rule = re.search(r'\.seg-card\.text-only \.verse\{([^}]*)\}', html).group(1)
        self.assertIn("justify-content:flex-start", rule,
                      "字幕要贴着标题带往下排，不能居中到页面腰线")
        self.assertNotIn("justify-content:center", rule)


class NoImageNotice(unittest.TestCase):
    """降级档要在日志里交代一句：这一档不等于"忘了配图"。

    两条消息都由 CLI 层（gen_hyperframes.main）打印，不在 generate_html 里——
    所以这里走真入口跑一遍，断言的正是作者在终端里看到的那两行。
    """

    def _run_cli(self, *extra):
        import gen_hyperframes as GH
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        m = H.make_manifest()
        man = os.path.join(tmp.name, "timing_manifest.json")
        with open(man, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        # 给个空音频：缺音频是 [error] 并直接退出，会盖掉这里要断言的两条消息
        os.makedirs(os.path.join(tmp.name, "audio"), exist_ok=True)
        with wave.open(os.path.join(tmp.name, "audio", "combined.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(b"\x00\x00" * int(22050 * m["total_duration"]))
        imgs = os.path.join(tmp.name, "images.json")
        # 只给画布段配图 → 槽位段 seg-a 真的缺图。这张图必须真存在：
        # images.json 指向不存在的文件是 error 并 exit，跟这里要断言的无关。
        os.makedirs(os.path.join(tmp.name, "images"), exist_ok=True)
        with open(os.path.join(tmp.name, "images", "seg-b.svg"), "w",
                  encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        with open(imgs, "w", encoding="utf-8") as f:
            json.dump({"seg-b": {"src": "images/seg-b.svg"}}, f)
        out = os.path.join(tmp.name, "index.html")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            GH.main(["-m", man, "-o", out, "--images", imgs,
                     "--gsap-src", "vendor/gsap.min.js"] + list(extra))
        return buf.getvalue()

    def test_notice_names_the_degraded_segments(self):
        err = self._run_cli("--slot-no-image")
        self.assertIn("[notice]", err)
        self.assertIn("slot-no-image", err)
        self.assertIn("seg-a", err)

    def test_missing_image_still_warns(self):
        """撤掉图槽不等于沉默：缺图本身仍要 warn（作者可能只是漏配）。"""
        err = self._run_cli("--slot-no-image")
        self.assertIn("[warn]", err)
        self.assertIn("没有配图映射", err)

    def test_no_notice_without_the_flag(self):
        err = self._run_cli()
        self.assertNotIn("slot-no-image", err)


if __name__ == "__main__":
    unittest.main()
