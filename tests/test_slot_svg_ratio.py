"""槽位版式里 SVG 配图的比例失配必须被拦下（slot_svg_ratio_errors）。

为什么这条要单独测：画布版式那张SVG 按**画幅**判比例（canvas_layout_errors），
槽位版式那张按**配图框**判——同一张 1080×1440 的图，在画布段是对的，放进槽位
段就是错的。两条判据各管一段版式，谁也替代不了谁，所以这里的用例必须成对写：
一张图在两种版式下给出相反的结论，才算真的在测"按版式选对判据"。

门禁只有这一道能拦：hyperframes check 对比例失配只报 container_overflow
（"元素超出裁切容器"），读者看不出该改根节点尺寸还是改内容。
"""
import os
import sys
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as G
from _template import get_canvas, get_image_box


def _seg(lid, layout=None):
    # layout=None 即"整个不写"——槽位版式在真实链路里就是不写这个键（写 'card'
    # 会被 manifest 校验挡下）。这里跟着真实形状走，别用链路里不存在的值。
    seg = {"id": lid, "sentences": [
        {"index": 1, "text": "x", "start_time": 0.0, "duration": 1.0}]}
    if layout:
        seg["layout"] = layout
    return seg


def _write_svg(directory, name, w, h):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" '
                'viewBox="0 0 {} {}"><rect width="{}" height="{}" fill="#fff"/>'
                '</svg>'.format(w, h, w, h, w, h))
    return name


class SlotSvgRatio(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.box = get_image_box("portrait")
        self.canvas = get_canvas("portrait")

    def test_canvas_sized_svg_is_rejected_in_slot_layout(self):
        """按画幅出的图放进槽位段 = 错，报错要指名该画幅的槽位尺寸。

        这条是本模块存在的理由：canvas_kit --help 那份"可照抄"示例出的就是
        画幅尺寸，作者照抄去做槽位段就踩在这里。
        """
        name = _write_svg(self.dir, "canvas.svg", *self.canvas)
        errs = G.slot_svg_ratio_errors(
            {"seg1": {"src": name}}, [_seg("seg1")], self.dir, *self.box)
        self.assertEqual(1, len(errs))
        self.assertIn("980", errs[0])          # 直接给出正确尺寸，不让人回去查
        self.assertIn("735", errs[0])
        self.assertIn("cover", errs[0])        # 说清后果
        self.assertIn("seg1", errs[0])         # 点名哪一段

    def test_slot_sized_svg_passes(self):
        """按槽位框尺寸出的图，槽位段里不该被拦。"""
        name = _write_svg(self.dir, "slot.svg", *self.box)
        self.assertEqual([], G.slot_svg_ratio_errors(
            {"seg1": {"src": name}}, [_seg("seg1")], self.dir, *self.box))

    def test_same_svg_passes_in_canvas_layout(self):
        """同一张画幅尺寸的图在画布段里是对的——两条判据各管一段版式。

        证伪的意义：如果这条红了，说明 slot_svg_ratio_errors 越界去管了画布段
        （它必须 continue 掉那部分，否则会对同一张图给两遍错，作者要改两处）。
        """
        name = _write_svg(self.dir, "canvas.svg", *self.canvas)
        self.assertEqual([], G.slot_svg_ratio_errors(
            {"seg1": {"src": name}}, [_seg("seg1", layout="canvas")],
            self.dir, *self.box))

    def test_landscape_slot_uses_landscape_box(self):
        """判据读的是"当前画幅的槽位框"，不是写死的 980×735。

        素材用画幅尺寸（1080×1440）而不是竖屏槽位尺寸：两个槽位框虽然不一样大，
        比例都是 4:3，拿竖屏槽位图配横屏框本就不该报，拿它当失配素材等于
        拿一张合规的图去要求报错。
        """
        name = _write_svg(self.dir, "canvas.svg", *get_canvas("portrait"))
        errs = G.slot_svg_ratio_errors(
            {"seg1": {"src": name}}, [_seg("seg1")], self.dir,
            *get_image_box("landscape"))
        self.assertEqual(1, len(errs))
        self.assertIn("{}×{}".format(*get_image_box("landscape")), errs[0])
        self.assertNotIn("980", errs[0])

    def test_raster_image_is_not_judged_on_ratio(self):
        """照片/视频不查比例——那张图里没有"画在里面的字"，失配只是取景裁边。"""
        jpg = os.path.join(self.dir, "p.jpg")
        with open(jpg, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        self.assertEqual([], G.slot_svg_ratio_errors(
            {"seg1": {"src": "p.jpg"}}, [_seg("seg1")], self.dir, *self.box))

    def test_unreadable_size_reported_not_silently_passed(self):
        """读不出固有尺寸要报出来，不能静默放行——那正是"没法验"的状态。"""
        name = "noviewbox.svg"
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="100%" '
                    'height="100%"><rect/></svg>')
        errs = G.slot_svg_ratio_errors(
            {"seg1": {"src": name}}, [_seg("seg1")], self.dir, *self.box)
        self.assertEqual(1, len(errs))
        self.assertIn("读不出固有尺寸", errs[0])

    def test_entry_without_image_is_left_to_other_gates(self):
        """没配图的段落不由这条报（覆盖率提示与引用完整性校验各有出处）。"""
        self.assertEqual([], G.slot_svg_ratio_errors(
            {}, [_seg("seg1")], self.dir, *self.box))


class EpilogWarnsAboutSlotLayout(unittest.TestCase):
    def test_canvas_kit_help_says_the_sample_is_canvas_only(self):
        """canvas_kit 的示例只适用画布版式——这句必须在 --help 里。

        为什么：模块头写了「整页画布那页的脚手架」，但模块头不在作者眼前。
        照抄示例去做槽位段会拿到画幅尺寸的图，而那句"只配 canvas"没人提醒。
        """
        import canvas_kit
        epilog = canvas_kit._epilog()
        self.assertIn("980", epilog)   # 直接给尺寸，而不是让作者去查模板
        self.assertIn("canvas", epilog)


if __name__ == "__main__":
    unittest.main()