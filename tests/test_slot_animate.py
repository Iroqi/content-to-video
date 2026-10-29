"""槽位版式的图内编排（`animate` 落在非满幅页上）：契约、生成期体检、渲染三层回归。

同一块 `animate` 写进槽位页时只有三条差别，而这三条都不由键名区分、由版式推出：
镜头归模板（不调机位）、标题层与句子流层照旧生成、起始态取图上的静态值。
"""
import contextlib
import io
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _animate as AN
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
    """契约层：槽位页与满幅页写的是同一块 animate，边界由版式推。"""

    def _one_step(self, step):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg",
                                           "animate": {"steps": [step]}}})

    def test_valid_animate_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
            {"at": 0, "target": "#base", "set": {"opacity": 1}},
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}, "duration": 0.6}]}}})

    def test_at_time_anchor_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
            {"at_time": 2.5, "target": "#label", "from": {"opacity": 0},
             "to": {"opacity": 1}}]}}})

    def test_no_anchor_rejected(self):
        self._one_step({"target": "#dot", "set": {"opacity": 1}})

    def test_at_and_at_time_mutually_exclusive(self):
        self._one_step({"at": 0, "at_time": 1.0, "target": "#dot", "set": {"opacity": 1}})

    def test_bad_target_rejected(self):
        self._one_step({"at": 0, "target": "#a .b", "set": {"opacity": 1}})

    def test_legacy_contract_names_point_at_the_rename(self):
        """旧写法（director / evolve / derive）不静默忽略：它们是"改了没生效"的头号来源。"""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            validate_images_json({"seg1": {"src": "images/a.svg",
                                           "director": {"steps": []}}})
        msg = buf.getvalue()
        self.assertIn("旧写法", msg)
        self.assertIn("animate", msg)

    def test_animate_on_non_svg_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.png", "animate": {
                "steps": [{"at": 0, "target": "#a", "set": {"opacity": 1}}]}}})

    def test_empty_animate_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {}}})

    def test_tween_and_param_steps_share_one_block(self):
        """同段共存：一拍管出场、一组管连续演绎，两种拍记在同一块 steps 里。"""
        validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {
            "steps": [{"at": 0, "target": "#base", "set": {"opacity": 1}},
                      {"at": 1, "param": "theta", "to": 90, "duration": 1.2}],
            "params": {"theta": 0},
            "bind": [{"target": "#dot", "attr": "cx",
                      "expr": "200 + 100*cos(theta*pi/180)"}]}}})

    def test_params_without_a_param_step_rejected(self):
        """声明了参数却没有任何一拍推它：几何不会变，等于写了一段死配置。"""
        with self.assertRaises(ValueError) as cm:
            validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {
                "steps": [{"at": 0, "target": "#base", "set": {"opacity": 1}}],
                "params": {"theta": 0},
                "bind": [{"target": "#dot", "attr": "cx", "expr": "200 + theta"}]}}})
        self.assertIn("param", str(cm.exception))


class CheckAnimate(unittest.TestCase):
    """生成期体检：只用源文本判得了的几件事（相机那条按 full_bleed 决定）。"""

    def test_clean_steps_pass(self):
        errs, warns = AN.check_animate(SEG_A_SVG, {"steps": [
            {"at": 0, "target": "#base", "set": {"opacity": 1}},
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual((errs, warns), ([], []))

    def test_missing_target_is_an_error(self):
        errs, _ = AN.check_animate(SEG_A_SVG, {"steps": [
            {"at": 1, "target": "#nope", "set": {"opacity": 1}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(len(errs), 1)
        self.assertIn("#nope", errs[0])
        self.assertIn("seg-a", errs[0])

    def test_class_selector_is_a_valid_target(self):
        svg = SEG_A_SVG.replace('<circle id="dot"', '<circle class="mark dot" id="dot"')
        errs, _ = AN.check_animate(svg, {"steps": [
            {"at": 1, "target": ".mark", "to": {"opacity": 1}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(errs, [])

    def test_camera_key_flagged_on_slot_layout(self):
        # 契约层也挡（那里读得到版式），这里再挡一次：gen 阶段直接吃
        # images.json 的路径不止一条
        errs, _ = AN.check_animate(SEG_A_SVG, {"steps": [
            {"at": 1, "target": "#dot", "to": {"scale": 1.4}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(len(errs), 1)
        self.assertIn("scale", errs[0])
        self.assertIn("canvas", errs[0])

    def test_camera_key_on_full_bleed_is_fine(self):
        svg = SEG_A_SVG.replace("<g id=\"base\">", "<g id=\"cam\"><g id=\"base\">") \
            .replace("</g>\n<circle", "</g></g>\n<circle")
        errs, _ = AN.check_animate(svg, {"steps": [
            {"at": 1, "target": "#cam", "to": {"scale": 1.4}}]}, "seg-a",
            full_bleed=True)
        self.assertEqual(errs, [])

    def test_all_hidden_first_frame_warns(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<rect id="a" opacity="0"/><rect id="b" style="opacity:0"/></svg>')
        _, warns = AN.check_animate(svg, {"steps": [
            {"at": 1.5, "target": "#a", "to": {"opacity": 1}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(len(warns), 1)
        self.assertIn("空框", warns[0])

    def test_hidden_but_lit_at_head_does_not_warn(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<rect id="a" opacity="0"/><rect id="b" opacity="0"/></svg>')
        _, warns = AN.check_animate(svg, {"steps": [
            {"at": 0, "target": "#a", "set": {"opacity": 1}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(warns, [])

    def test_visible_structure_does_not_warn(self):
        # 结构底本来就可见 = 落位那一帧不是空框，不该误报
        _, warns = AN.check_animate(SEG_A_SVG, {"steps": [
            {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"}}]}, "seg-a",
            full_bleed=False)
        self.assertEqual(warns, [])

    def test_unparsable_svg_stays_silent(self):
        # 解析不了由净化那一步报，这里不重复刷屏
        self.assertEqual(
            AN.check_animate("<svg", {"steps": [{"at": 0, "target": "#a"}]}, "s",
                             full_bleed=False), ([], []))


class Prepare(unittest.TestCase):
    """gen 阶段：净化内联 + 回填 + at 越界。"""

    def test_inline_svg_backfilled_and_steps_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _write_svg(tmp, "seg-a", SEG_A_SVG)
            images = {"seg-a": {"src": src, "animate": {"steps": [
                {"at": 0, "target": "#base", "set": {"opacity": 1}}]}}}
            m = H.make_manifest()
            errs, warns = G.animate_prepare(images, m["segments"], tmp)
            self.assertEqual((errs, warns), ([], []))
            self.assertIn('id="dot"', images["seg-a"]["inline_svg"])

    def test_missing_target_fails_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = _write_svg(tmp, "seg-a", SEG_A_SVG)
            images = {"seg-a": {"src": src, "animate": {"steps": [
                {"at": 0, "target": "#ghost", "set": {"opacity": 1}}]}}}
            m = H.make_manifest()
            errs, _warns = G.animate_prepare(images, m["segments"], tmp)
            self.assertTrue(any("#ghost" in e for e in errs))


    def test_mixed_tween_and_param_steps_survive_prepare(self):
        """同一块 animate 里两种拍：内联一次、两种都留着（编译器只吃 param 那几条）。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = _write_svg(tmp, "seg-a", SEG_A_SVG)
            images = {"seg-a": {"src": src, "animate": {
                "steps": [{"at": 0, "target": "#base", "set": {"opacity": 1}},
                          {"at": 1, "param": "theta", "to": 90, "duration": 1.2}],
                "params": {"theta": 0},
                "bind": [{"target": "#dot", "attr": "cx",
                          "expr": "200 + 100*cos(theta*pi/180)"}]}}}
            m = H.make_manifest()
            errs, _warns = G.animate_prepare(images, m["segments"], tmp)
            self.assertEqual(errs, [])
            self.assertIn("inline_svg", images["seg-a"])


class Renderer(unittest.TestCase):
    """槽位版式：内联、作用域都在，但不生成整页画布那一套。"""

    def _images(self, steps=None):
        return {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="base"/>'
                                    '<circle id="dot"/></svg>',
                      "animate": {"steps": steps or [
                          {"at": 0, "target": "#base", "set": {"opacity": 1}},
                          {"at": 1, "target": "#dot", "to": {"fill": "#f59e0b"},
                           "duration": 0.6}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }

    def test_tweens_are_scoped_and_scheduled(self):
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
        # 槽位页演了图，字幕和标题都还在（满幅页才整页交出版面，这两层不生成）
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", images=self._images(),
                                aspect="portrait")
        self.assertIn('class="seg-title"', html)
        self.assertIn('<div class="verse" id="verse-seg-a"', html)
        self.assertIn('<div class="verse-line"', html)

    def test_tween_and_param_lines_coexist(self):
        """一块 animate 里两种拍各出一条时间线：属性补间 + 参数代理补间。"""
        m = H.make_manifest()
        images = self._images()
        images["seg-a"]["animate"] = {
            "steps": [{"at": 0, "target": "#base", "set": {"opacity": 1}},
                      {"at": 1, "param": "theta", "to": 90, "duration": 1.2}],
            "params": {"theta": 0},
            "bind": [{"target": "#dot", "attr": "cx",
                      "expr": "200 + 100*cos(theta*pi/180)"}]}
        html = HR.generate_html(m, "audio/combined.wav", images=images,
                                aspect="portrait")
        self.assertIn('tl.set("#img-seg-a #base"', html)
        self.assertIn("onUpdate:draw", html)      # 参数拍 → 代理补间

    def test_no_animate_means_plain_img(self):
        m = H.make_manifest()
        images = {"seg-a": {"src": "images/seg-a.svg"}, "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=images, aspect="portrait")
        self.assertIn('<img src="images/seg-a.svg"', html)


if __name__ == "__main__":
    unittest.main()
