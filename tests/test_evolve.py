"""槽位版式演进（`evolve`）：契约、生成期体检、渲染三层回归。

对应的产品判断写在 `scripts/_evolve.py` 的模块注释里，这里只钉行为：
同样的 steps 语法，与 director 的三条差别（不调机位 / 起点态兜底 /
按段独立），以及"要么整页交出版面，要么留在槽位"这个二选一。
"""
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _evolve as EV
from _images_schema import validate_images_json
import html_renderer as HR
import gen_hyperframes as G


SEG_A_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" viewBox="0 0 980 735">'
    '<g id="base"><rect x="80" y="80" width="820" height="200" fill="#dbe6f5"/></g>'
    '<circle id="dot" cx="200" cy="400" r="26"/>'
    '<text id="label" x="240" y="410">第一态</text>'
    "</svg>")


def _write_svg(tmp, sid, text):
    d = os.path.join(tmp, "images")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{sid}.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"images/{sid}.svg"


class Schema(unittest.TestCase):
    """契约层：与 director 共用 steps 语法，但边界不同。"""

    def _one_step(self, step):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg",
                                          "evolve": {"steps": [step]}}})

    def test_valid_evolve_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "evolve": {"steps": [
            {"at": 0, "target": "#base", "set": {"opacity": 1}},
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}, "duration": 0.6}]}}})

    def test_at_time_anchor_accepted(self):
        # 与 director 同一套锚点规则（含 at_time 绝对秒）
        validate_images_json({"seg4": {"src": "images/seg4.svg", "evolve": {"steps": [
            {"at_time": 2.5, "target": "#label", "from": {"opacity": 0},
             "to": {"opacity": 1}}]}}})

    def test_no_anchor_rejected(self):
        # 锚点规则是原样复用的，别让 evolve 变成一条偷偷放宽的后门
        self._one_step({"target": "#dot", "set": {"opacity": 1}})

    def test_at_and_at_time_mutually_exclusive(self):
        self._one_step({"at": 0, "at_time": 1.0, "target": "#dot", "set": {"opacity": 1}})

    def test_bad_target_rejected(self):
        self._one_step({"at": 0, "target": "#a .b", "set": {"opacity": 1}})

    def test_camera_keys_rejected(self):
        """镜头归模板：入场收拢 + Ken Burns 已经在推，作者再推就是两层意图打架。"""
        for key, val in (("svgOrigin", "500 360"), ("scale", 1.4),
                         ("xPercent", 10), ("yPercent", -10),
                         ("transformOrigin", "center")):
            with self.subTest(key=key):
                self._one_step({"at": 1, "target": "#dot", "to": {key: val}})

    def test_camera_key_rejected_in_from_and_set_too(self):
        # 三个载荷都要挡：只挡 `to` 就是留给作者一个绕过去的写法
        self._one_step({"at": 1, "target": "#dot", "from": {"scale": 1.4}})
        self._one_step({"at": 1, "target": "#dot", "set": {"scale": 1.4}})

    def test_evolve_and_director_mutually_exclusive(self):
        with self.assertRaises(ValueError) as cm:
            validate_images_json({"seg4": {"src": "images/seg4.svg",
                                           "evolve": {"steps": [
                                               {"at": 0, "target": "#dot",
                                                "set": {"opacity": 1}}]},
                                           "director": {"steps": [
                                               {"at": 0, "target": "#dot",
                                                "set": {"opacity": 1}}]}}})
        self.assertIn("evolve", str(cm.exception))

    def test_evolve_on_non_svg_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.png", "evolve": {
                "steps": [{"at": 0, "target": "#a", "set": {"opacity": 1}}]}}})


class CheckEvolve(unittest.TestCase):
    """生成期体检：只用源文本判得了的三件事。"""

    def test_clean_steps_pass(self):
        errs, warns = EV.check_evolve(SEG_A_SVG, [
            {"at": 0, "target": "#base", "set": {"opacity": 1}},
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}}], "seg-a")
        self.assertEqual((errs, warns), ([], []))

    def test_missing_target_is_an_error(self):
        errs, _ = EV.check_evolve(SEG_A_SVG, [
            {"at": 1, "target": "#nope", "set": {"opacity": 1}}], "seg-a")
        self.assertEqual(len(errs), 1)
        self.assertIn("#nope", errs[0])
        self.assertIn("seg-a", errs[0])

    def test_class_selector_is_a_valid_target(self):
        svg = SEG_A_SVG.replace('<circle id="dot"', '<circle class="mark dot" id="dot"')
        errs, _ = EV.check_evolve(svg, [
            {"at": 1, "target": ".mark", "to": {"opacity": 1}}], "seg-a")
        self.assertEqual(errs, [])

    def test_camera_key_flagged_even_if_schema_let_it_through(self):
        # 契约层已经挡了，这里再挡一次：gen 阶段直接吃 images.json 的路径不止一条
        errs, _ = EV.check_evolve(SEG_A_SVG, [
            {"at": 1, "target": "#dot", "to": {"scale": 1.4}}], "seg-a")
        self.assertEqual(len(errs), 1)
        self.assertIn("scale", errs[0])
        self.assertIn("director", errs[0])

    def test_all_hidden_first_frame_warns(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<rect id="a" opacity="0"/><rect id="b" style="opacity:0"/></svg>')
        _, warns = EV.check_evolve(svg, [
            {"at": 1.5, "target": "#a", "to": {"opacity": 1}}], "seg-a")
        self.assertEqual(len(warns), 1)
        self.assertIn("空框", warns[0])

    def test_hidden_but_lit_at_head_does_not_warn(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<rect id="a" opacity="0"/><rect id="b" opacity="0"/></svg>')
        _, warns = EV.check_evolve(svg, [
            {"at": 0, "target": "#a", "set": {"opacity": 1}}], "seg-a")
        self.assertEqual(warns, [])

    def test_visible_structure_does_not_warn(self):
        # 结构底本来就可见 = 落位那一帧不是空框，不该误报
        _, warns = EV.check_evolve(SEG_A_SVG, [
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}}], "seg-a")
        self.assertEqual(warns, [])

    def test_unparsable_svg_stays_silent(self):
        # 解析不了由净化那一步报，这里不重复刷屏
        self.assertEqual(EV.check_evolve("<svg", [{"at": 0, "target": "#a"}], "s"),
                         ([], []))


class Prepare(unittest.TestCase):
    """gen 阶段：净化内联 + 回填 + at 越界。"""

    def test_inline_svg_backfilled_and_steps_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _write_svg(tmp, "seg-a", SEG_A_SVG)
            images = {"seg-a": {"src": src, "evolve": {"steps": [
                {"at": 0, "target": "#base", "set": {"opacity": 1}}]}}}
            m = H.make_manifest()
            errs, warns = [], []
            G._evolve_prepare(images, m["segments"], tmp, None, errs, warns)
            self.assertEqual((errs, warns), ([], []))
            self.assertIn('id="dot"', images["seg-a"]["inline_svg"])

    def test_missing_target_fails_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _write_svg(tmp, "seg-a", SEG_A_SVG)
            images = {"seg-a": {"src": src, "evolve": {"steps": [
                {"at": 0, "target": "#ghost", "set": {"opacity": 1}}]}}}
            m = H.make_manifest()
            errs, warns = [], []
            G._evolve_prepare(images, m["segments"], tmp, None, errs, warns)
            self.assertTrue(any("#ghost" in e for e in errs))


class Renderer(unittest.TestCase):
    """槽位版式：内联、作用域都在，但不生成整页画布那一套。"""

    def _images(self, steps=None):
        return {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="base"/>'
                                    '<circle id="dot"/></svg>',
                      "evolve": {"steps": steps or [
                          {"at": 0, "target": "#base", "set": {"opacity": 1}},
                          {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"},
                           "duration": 0.6}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }

    def test_evolve_tweens_are_scoped_and_scheduled(self):
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", images=self._images(),
                                aspect="portrait")
        self.assertIn('class="seg-image bare-media svg-inline" id="img-seg-a"', html)
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        t0 = round(seg_a["sentences"][0]["start_time"], 2)
        t1 = round(seg_a["sentences"][1]["start_time"], 2)
        self.assertIn(f'tl.set("#img-seg-a #base", {{"opacity": 1}}, {t0:.2f})', html)
        self.assertIn(f'tl.to("#img-seg-a #dot", '
                      f'{{"fill": "#f59e0b", "duration": 0.6, "ease": "power2.out"}}'
                      f', {t1:.2f})', html)

    def test_slot_layout_keeps_title_and_verse_layers(self):
        # 这就是 evolve 存在的理由：演了图，字幕和标题都还在（director 那一档
        # 整页交出版面，这两层都不生成）
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", images=self._images(),
                                aspect="portrait")
        self.assertIn('class="seg-title"', html)
        self.assertIn('<div class="verse" id="verse-seg-a"', html)
        self.assertIn('<div class="verse-line"', html)

    def test_no_evolve_means_plain_img(self):
        m = H.make_manifest()
        images = {"seg-a": {"src": "images/seg-a.svg"}, "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=images, aspect="portrait")
        self.assertIn('<img src="images/seg-a.svg"', html)


if __name__ == "__main__":
    unittest.main()
