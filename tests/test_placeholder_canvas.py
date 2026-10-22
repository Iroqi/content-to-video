"""预览档占位（--placeholder-missing）：整页画布缺图不再把整个预览卡死。

修复前：整页画布段缺图 → run.py 直接 exit 2（连 `--until html` 都不放行），
理由是"没图就没画面"。理由成立，但代价是把"先看结构"这条路也封死了——
brainstorm 时最想先看的就是编排与节奏，此时图往往还没画。

现在给一条显式预览档：写一张按画幅原尺寸、写着段标题的占位 SVG，让 HTML
先出来；正片路径一步没松（不带旗标照旧拦、带旗标跑渲染在入口就报错）。
"""
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  # 仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as G
from _template import MIN_READABLE_FONT_PX


def _seg(sid, layout="canvas", title="季度营收"):
    return {"id": sid, "layout": layout, "title": title, "sentences": []}


class PlaceholderFillsOnlyMissingCanvas(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _run(self, images, segments, w=1080, h=1440):
        filled = G.materialize_missing_canvas_placeholders(
            images, segments, self.dir, w, h)
        return filled

    def test_missing_canvas_gets_a_placeholder(self):
        images = {}
        filled = self._run(images, [_seg("seg1")])
        self.assertEqual(filled, ["seg1"])
        self.assertIn("seg1", images)
        path = os.path.join(self.dir, images["seg1"]["src"])
        self.assertTrue(os.path.isfile(path))

    def test_placeholder_passes_the_same_gate_it_stands_in_for(self):
        """占位图自己必须过 canvas_layout_errors：等比、字号不下限。

        否则补完照旧 exit 1，"能看结构"仍是空话。三条判据分开断言，
        失败时能直接看出是哪一条破了。
        """
        for w, h in ((1080, 1440), (1920, 1080)):
            with self.subTest(canvas=(w, h)):
                images = {}
                segs = [_seg("seg1", title="一季度营收同比增长")]
                self._run(images, segs, w, h)
                errs, _warns = G.canvas_layout_errors(images, segs, self.dir, w, h)
                self.assertEqual(errs, [])
                src = images["seg1"]["src"]
                size = G._svg_intrinsic_size(os.path.join(self.dir, src))
                self.assertEqual(size[:2], (w, h))
                min_px, n_text = G._svg_text_metrics(os.path.join(self.dir, src))
                self.assertGreaterEqual(min_px, MIN_READABLE_FONT_PX)
                self.assertGreater(n_text, 0, "占位牌上得有字，否则这一页是空的")

    def test_slot_segments_are_left_alone(self):
        """槽位版式缺图不补：那两层的标题与句子流照常生成，预览已经能看结构。"""
        images = {}
        filled = self._run(images, [_seg("seg1", layout=None)])
        self.assertEqual(filled, [])
        self.assertEqual(images, {})

    def test_existing_mapping_is_never_overwritten(self):
        images = {"seg1": {"src": "images/real.svg"}}
        filled = self._run(images, [_seg("seg1")])
        self.assertEqual(filled, [])
        self.assertEqual(images["seg1"]["src"], "images/real.svg")

    def test_draft_overwrites_broken_src_with_placeholder(self):
        """草稿档（missing_sids 给定）：src 指向坏路径的也换占位——草稿要的是
        先看结构，坏 src 与"还没画"一样都算"还没画完"。定稿路径（不给
        missing_sids）照旧不动既有映射（上一条锁的就是这条红线）。"""
        images = {"seg1": {"src": "images/nope.svg"}}
        filled = G.materialize_missing_canvas_placeholders(
            images, [_seg("seg1")], self.dir, 1080, 1440,
            missing_sids={"seg1"})
        self.assertEqual(filled, ["seg1"])
        self.assertIn(".placeholder.svg", images["seg1"]["src"])
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, images["seg1"]["src"])))

    def test_draft_missing_sids_skips_other_segments(self):
        """missing_sids 只补列出的画布段：槽位段与未列出的画布段都不动。"""
        images = {"seg1": {"src": "images/nope.svg"}, "seg2": {"src": "images/ok.svg"}}
        filled = G.materialize_missing_canvas_placeholders(
            images, [_seg("seg1"), _seg("seg2")], self.dir, 1080, 1440,
            missing_sids={"seg1"})
        self.assertEqual(filled, ["seg1"])
        self.assertEqual(images["seg2"]["src"], "images/ok.svg")

    def test_title_is_escaped_into_the_svg(self):
        """标题是信源文本（不可信）：进 SVG 必须转义，不能拼出 `<`/`&`。"""
        images = {}
        self._run(images, [_seg("seg1", title='营收 <30 亿 & "回暖"')])
        with open(os.path.join(self.dir, images["seg1"]["src"]),
                  encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("<30", raw)
        self.assertIn("&lt;30", raw)
        self.assertIn("&amp;", raw)


class DraftPlaceholderOnlyForMissingSrc(unittest.TestCase):
    """草稿档占位只认 **src 缺失**：poster 缺失不换占位（真图还在盘上）。

    missing_imgs 里混着 poster 缺失的条目（封面文件不在，video 条目才有）。
    旧代码把整个 sid 集合交给占位：poster 缺失的画布段其整份映射
    （src/poster/type）被占位覆盖——草稿预览丢了作者已经画完的图。
    """

    def test_poster_missing_does_not_trigger_placeholder(self):
        seg = _seg("seg1")
        images = {"seg1": {"src": "images/real.mp4", "type": "video",
                           "poster": "images/nope.png"}}
        missing = [("seg1", "images/nope.png")]        # 缺的是 poster，不是 src
        sids = G._canvas_sids_missing_src(missing, images, {"seg1": seg})
        self.assertEqual(sids, set())
        # 交给占位函数也不该动它（missing_sids 为空 = 非草稿路径的红线）
        filled = G.materialize_missing_canvas_placeholders(
            images, [seg], self._dir(), 1080, 1440, missing_sids=sids)
        self.assertEqual(filled, [])
        self.assertEqual(images["seg1"]["src"], "images/real.mp4")
        self.assertEqual(images["seg1"]["type"], "video")

    def test_src_missing_still_triggers_placeholder(self):
        seg = _seg("seg1")
        images = {"seg1": {"src": "images/nope.svg", "type": "image"}}
        missing = [("seg1", "images/nope.svg")]
        sids = G._canvas_sids_missing_src(missing, images, {"seg1": seg})
        self.assertEqual(sids, {"seg1"})

    def test_slot_segment_missing_src_is_excluded(self):
        seg = _seg("seg1", layout=None)
        images = {"seg1": {"src": "images/nope.svg"}}
        sids = G._canvas_sids_missing_src(
            [("seg1", "images/nope.svg")], images, {"seg1": seg})
        self.assertEqual(sids, set())

    def _dir(self):
        if not hasattr(self, "_tmp"):
            self._tmp = tempfile.TemporaryDirectory()
            self.addCleanup(self._tmp.cleanup)
        return self._tmp.name


if __name__ == "__main__":
    unittest.main()
