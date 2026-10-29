#!/usr/bin/env python3
"""并置对比版式（layout:"compare"）的契约与装配。

这一版式的风险全是"静默失联"：容器 id 换了名（`#img-{sid}` → `#img-{sid}-0/1`
与 `#cmp-{sid}`），任何一处仍按旧名选就会打到 0 个元素，GSAP 不报错、成片里
那一页只是"没动"。所以测试分两组：契约挡住写错，装配挡住失联。
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
from _images_schema import validate_images_json  # noqa: E402
from _segments import LAYOUT_VALUES, _validate_layout  # noqa: E402
from html_renderer import generate_html  # noqa: E402

PAIR = ["images/a.svg", "images/b.svg"]


def _src():
    s = H.sample_source()
    s["segments"][0]["layout"] = "compare"
    s["segments"][0]["text"] = "左边是常见写法。右边是这一版的写法。"
    return s


def _render(images, aspect="portrait", src=None):
    m = H.make_manifest(src or _src())
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=images, aspect=aspect,
                             theme="dark")


class TestLayoutValue(unittest.TestCase):
    def test_compare_is_a_valid_content_layout(self):
        self.assertIn("compare", LAYOUT_VALUES)
        _validate_layout("compare", "seg-a", content=True)   # 不抛

    def test_agenda_still_structural_only(self):
        with self.assertRaises(ValueError):
            _validate_layout("agenda", "seg-a", content=True)


class TestPairContract(unittest.TestCase):
    def test_pair_src_is_accepted_and_panes_default(self):
        got = validate_images_json({"seg-a": {"src": list(PAIR)}})
        self.assertEqual(got["seg-a"]["panes"], [{}, {}])

    def test_single_image_src_on_compare_shaped_entry_is_rejected(self):
        # src 必须是**一对**：一项或三项都拦，否则另一格没有图。
        for bad in ([PAIR[0]], PAIR + [PAIR[0]]):
            with self.subTest(n=len(bad)):
                with self.assertRaises(ValueError) as cm:
                    validate_images_json({"seg-a": {"src": bad}})
                self.assertIn("正好 2 项", str(cm.exception))

    def test_single_image_keys_are_rejected_on_a_pair(self):
        # 单图那套键（director/type/stage…）在一对图上是"写了不生效"，
        # 这一类静默丢弃正是契约层要拦的。
        for key, val in (("director", {"steps": [{"at": 0, "target": "#a",
                                                 "to": {"opacity": 1}}]}),
                         ("type", "image"), ("stage", "keep")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as cm:
                    validate_images_json({"seg-a": {"src": list(PAIR), key: val}})
                self.assertIn(key, str(cm.exception))
                self.assertIn("panes", str(cm.exception))

    def test_video_pane_is_rejected(self):
        with self.assertRaises(ValueError) as cm:
            validate_images_json({"seg-a": {"src": ["images/a.svg", "images/b.mp4"]}})
        self.assertIn("只收静态图与 SVG", str(cm.exception))

    def test_panes_accept_director_and_evolve_per_side(self):
        got = validate_images_json({"seg-a": {
            "src": list(PAIR),
            "panes": [{"evolve": {"steps": [{"at": 0, "target": "#a",
                                             "to": {"opacity": 0.4}}]}},
                      {"director": {"steps": [{"at": 1, "target": "#b",
                                               "to": {"opacity": 1}}]}}]}})
        self.assertIn("evolve", got["seg-a"]["panes"][0])
        self.assertIn("director", got["seg-a"]["panes"][1])

    def test_panes_must_be_two(self):
        for bad in ([{}], [{}, {}, {}], {"a": 1}):
            with self.subTest(panes=bad):
                with self.assertRaises(ValueError) as cm:
                    validate_images_json({"seg-a": {"src": list(PAIR),
                                                    "panes": bad}})
                self.assertIn("2 项", str(cm.exception))

    def test_unknown_pane_key_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg-a": {"src": list(PAIR),
                                            "panes": [{"bogus": 1}, {}]}})


class TestAssembly(unittest.TestCase):
    """装配：两个格位、各自的 id、补间作用域各归各的。"""

    def _html(self, panes=None):
        return _render({"seg-a": {"src": list(PAIR), "panes": panes or [{}, {}]},
                        "seg-b": {"src": "images/seg-b.svg"}})

    def test_two_panes_with_distinct_ids(self):
        html = self._html()
        self.assertIn('id="cmp-seg-a"', html)
        self.assertIn('id="img-seg-a-0"', html)
        self.assertIn('id="img-seg-a-1"', html)
        # 两格各一张图，左 0 右 1
        self.assertLess(html.index('src="images/a.svg"'), html.index('src="images/b.svg"'))

    def test_title_layer_still_present(self):
        # 与整页画布不同：并置对比保留标题层与句子流层。
        html = self._html()
        self.assertIn('id="title-seg-a"', html)
        self.assertIn('id="verse-seg-a"', html)

    def test_entrance_targets_the_compare_container(self):
        # 照抄 `#img-seg-a` 会选到 0 个元素（容器叫 cmp-seg-a）——这条钉住它。
        html = self._html()
        self.assertIn('tl.from("#cmp-seg-a"', html)
        self.assertNotIn('tl.from("#img-seg-a"', html)

    def test_pane_tweens_are_scoped_per_side(self):
        html = _render({
            "seg-a": {"src": list(PAIR), "panes": [
                {"evolve": {"steps": [{"at": 0, "target": "#dot",
                                       "to": {"opacity": 0.4}}]}},
                {"director": {"steps": [{"at": 1, "target": "#dot",
                                         "to": {"opacity": 1}}]}}]},
            "seg-b": {"src": "images/seg-b.svg"}})
        # 两格都写了同名 target（#dot），必须各打各的格——否则右边那条覆盖左边。
        self.assertIn('#img-seg-a-0 #dot', html)
        self.assertIn('#img-seg-a-1 #dot', html)
        self.assertNotIn('"#img-seg-a #dot"', html)

    def test_missing_pair_raises_instead_of_empty_slot(self):
        # 单图映射喂给 compare 段：必须当场报错，不能渲染出空槽。
        with self.assertRaises(ValueError) as cm:
            _render({"seg-a": {"src": "images/a.svg"},
                     "seg-b": {"src": "images/seg-b.svg"}})
        self.assertIn("compare", str(cm.exception))

    def test_landscape_also_works(self):
        html = _render({"seg-a": {"src": list(PAIR)},
                        "seg-b": {"src": "images/seg-b.svg"}}, aspect="landscape")
        self.assertIn('id="cmp-seg-a"', html)


if __name__ == "__main__":
    unittest.main()
