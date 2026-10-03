import os
import tempfile
import unittest

import _helpers as H
import check_svg
import check_docs
import gen_hyperframes


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


GOOD = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" viewBox="0 0 980 735">'
        '<style>text{font-family:"Microsoft YaHei",sans-serif}</style>'
        '<text x="40" y="100" font-size="36" fill="#dbe6f5">反向传播</text></svg>')


class SvgChecker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, text, layout="slot", aspect="portrait", theme="dark"):
        return check_svg.check_file(write(self.tmp.name, "a.svg", text), layout, aspect, theme)

    def test_good_slot_svg(self):
        self.assertEqual(self.run_check(GOOD), ([], []))

    def test_wrong_ratio_is_error(self):
        errs, _ = self.run_check(GOOD.replace('height="735" viewBox="0 0 980 735"', 'height="980" viewBox="0 0 980 980"'))
        self.assertTrue(any("不等比" in e for e in errs))

    def test_script_and_external_href_are_errors(self):
        errs, _ = self.run_check(GOOD.replace("</svg>", '<script>1</script><image href="https://x/y.png"/></svg>'))
        self.assertTrue(any("<script>" in e for e in errs))
        self.assertTrue(any("外链" in e for e in errs))

    def test_entity_declaration_refused(self):
        errs, _ = self.run_check('<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertTrue(errs)

    def test_background_family_as_text_fill(self):
        errs, _ = self.run_check(GOOD.replace("#dbe6f5", "#1a2536"))
        self.assertTrue(any("背景色族" in e for e in errs))

    def test_background_family_not_checked_on_light_page(self):
        # cream 页底下深蓝族恰是推荐正文色系，家族判定只剩假阳性
        errs, _ = self.run_check(GOOD.replace("#dbe6f5", "#1a2536"), theme="cream")
        self.assertFalse(any("背景色族" in e for e in errs))

    def test_theme_mixup_caught(self):
        errs, _ = self.run_check(GOOD, theme="cream")
        self.assertTrue(any("对比度" in e for e in errs))

    def test_small_font_warns(self):
        _, warns = self.run_check(GOOD.replace('font-size="36"', 'font-size="18"'))
        self.assertTrue(any("26px" in w for w in warns))

    def test_canvas_needs_matching_aspect(self):
        errs, _ = self.run_check(GOOD, layout="canvas", aspect="landscape")
        self.assertTrue(any("不等比" in e for e in errs))

    def test_canvas_downscale_is_reflected_in_font_size(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="2560" height="1440" viewBox="0 0 2560 1440">'
               '<text x="200" y="100" font-size="28" font-family="sans-serif" fill="#fff">x</text></svg>')
        errs, warns = self.run_check(svg, layout="canvas", aspect="landscape")
        self.assertEqual(errs, [])
        self.assertTrue(any("26px" in w for w in warns))   # 28 × 0.75 = 21

    def _canvas_svg(self, body):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
                'viewBox="0 0 1080 1440">' + body + '</svg>')

    def _opacity_warns(self, body, layout):
        _, warns = self.run_check(self._canvas_svg(body), layout=layout, aspect="portrait")
        return [w for w in warns if "吃掉对比度" in w]

    def test_canvas_opacity_zero_is_the_directors_start_state(self):
        # 导演页每个元素都从 opacity=0 逐拍点亮；照旧报就是每页十来条噪声，
        # 把真正那条（半透明）淹掉
        body = '<text x="90" y="150" font-size="64" font-family="s" opacity="0">熵</text>'
        self.assertEqual(self._opacity_warns(body, "canvas"), [])
        self.assertEqual(len(self._opacity_warns(body.replace('opacity="0"', 'opacity="0.4"'),
                                                 "canvas")), 1)

    def test_slot_opacity_zero_still_warns(self):
        # 槽位版式没有逐拍点亮这回事，看不见就是写错了
        body = '<text x="90" y="150" font-size="64" font-family="s" opacity="0">熵</text>'
        self.assertEqual(len(self._opacity_warns(body, "slot")), 1)

    def test_canvas_low_contrast_text_warns_but_never_errors(self):
        # 画布上的字可能压着自己画的浅色局部底板，按页底算出来的数对它不成立，
        # 所以这条只能提示；槽位底色确定，同一支色照样判 error
        body = '<text x="90" y="150" font-size="64" font-family="s" fill="#334155">t</text>'
        errs, warns = self.run_check(self._canvas_svg(body), layout="canvas", aspect="portrait")
        self.assertEqual([e for e in errs if "对比度" in e], [])
        self.assertTrue(any("对比度" in w and "局部底板" in w for w in warns))
        errs, _ = self.run_check(self._canvas_svg(body), layout="slot")
        self.assertTrue(any("对比度" in e for e in errs))

    def test_canvas_full_bleed_plate_hides_the_templates_three_layers(self):
        # 模板本来就在画布页底下画了渐变/网格/氛围光三层，满幅 rect 把它们整个盖掉
        body = ('<rect width="1080" height="1440" fill="#0b1020"/>'
                '<circle cx="540" cy="700" r="300" fill="#38bdf8"/>'
                '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>')
        _, warns = self.run_check(self._canvas_svg(body), layout="canvas", aspect="portrait")
        got = [w for w in warns if "满幅底板" in w]
        self.assertEqual(len(got), 1)
        self.assertIn("氛围光", got[0])
        # 槽位那条各说各的理由，措辞不共用
        _, sw = self.run_check(self._canvas_svg(body), layout="slot")
        self.assertTrue(any("色差框" in w for w in sw))
        self.assertEqual([w for w in sw if "满幅底板" in w], [])


class CanvasDensity(unittest.TestCase):
    """整页画布的"整条横向空带"warn：画布页没有模板兜构图，暂停是空页就得说。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, body, layout="canvas"):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
               'viewBox="0 0 1080 1440">' + body + '</svg>')
        p = write(self.tmp.name, "c.svg", svg)
        _, warns = check_svg.check_file(p, layout, "portrait", "dark")
        return [w for w in warns if "空带" in w]

    def test_empty_band_warns(self):
        # 实拍复现的形状：顶部两行标题、中间一条轴，y≈240–800 横着什么都没有
        got = self.check('<rect width="1080" height="1440" fill="#0b1020"/>'
                         '<text x="90" y="150" font-size="64" font-family="s">t</text>'
                         '<path d="M150 800 L930 800" stroke="#64748b" stroke-width="5"/>')
        self.assertEqual(len(got), 1)
        self.assertIn("占页高", got[0])

    def test_spread_page_passes(self):
        self.assertEqual(self.check(
            '<rect x="90" y="120" width="900" height="120" fill="#16233a"/>'
            '<path d="M90 300 L990 700" stroke="#64748b" stroke-width="4"/>'
            '<circle cx="540" cy="900" r="120" fill="#38bdf8"/>'
            '<text x="90" y="1300" font-size="40" font-family="s">ok</text>'), [])

    def test_full_bleed_plate_is_not_ink(self):
        # 底板铺满整页是画布的正当画法，但它不能替内容把空带填成"有东西"
        self.assertEqual(len(self.check(
            '<rect width="1080" height="1440" fill="#0b1020"/>'
            '<text x="90" y="150" font-size="64" font-family="s">t</text>')), 1)

    def test_relative_path_counts_whole_span(self):
        # 相对命令要按光标累加：读成绝对坐标会把这条 100→1300 的长 path 当成只到 600
        self.assertEqual(self.check(
            '<path d="M540 100 l 0 600 l 0 600 z" stroke="#64748b" fill="none"/>'
            '<text x="90" y="1400" font-size="40" font-family="s">ok</text>'), [])

    def test_malformed_path_does_not_crash(self):
        self.assertEqual(len(self.check(
            '<path d="M150 800 Q 90"/>'
            '<text x="90" y="150" font-size="64" font-family="s">t</text>')), 1)

    def test_slot_layout_unaffected(self):
        # 槽位版式有模板网格兜构图，这条只管画布
        self.assertEqual(self.check(
            '<text x="40" y="100" font-size="64" font-family="s">t</text>',
            layout="slot"), [])

    def test_band_merging_and_page_edges(self):
        self.assertEqual(check_svg._largest_empty_band([(0, 10), (5, 20), (100, 120)], 200),
                         (20.0, 100.0))
        self.assertEqual(check_svg._largest_empty_band([], 200), (0.0, 200.0))


class SvgIntrinsicSize(unittest.TestCase):
    """gen_hyperframes 读 SVG 根尺寸：门禁端要与 check_svg 同一口径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "a.svg")

    def size(self, attrs):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" ' + attrs + "></svg>")
        return gen_hyperframes._svg_intrinsic_size(self.path)

    def test_px_suffix_accepted_like_check_svg(self):
        self.assertEqual(self.size('width="980px" height="735px"'), (980.0, 735.0))

    def test_other_unit_and_prefixed_attr_not_misread(self):
        self.assertIsNone(self.size('width="50%" height="50%"'))
        # min-width 不该被当成 width（check_svg 用 root.get("width") 天然不会）
        self.assertEqual(self.size('min-width="7px" width="980" height="735"'),
                         (980.0, 735.0))


class SvgIntrinsicSize(unittest.TestCase):
    """gen_hyperframes 读 SVG 根尺寸：门禁端要与 check_svg 同一口径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "a.svg")

    def size(self, attrs):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" ' + attrs + "></svg>")
        return gen_hyperframes._svg_intrinsic_size(self.path)

    def test_px_suffix_accepted_like_check_svg(self):
        self.assertEqual(self.size('width="980px" height="735px"'), (980.0, 735.0))

    def test_other_unit_and_prefixed_attr_not_misread(self):
        self.assertIsNone(self.size('width="50%" height="50%"'))
        # min-width 不该被当成 width（check_svg 用 root.get("width") 天然不会）
        self.assertEqual(self.size('min-width="7px" width="980" height="735"'),
                         (980.0, 735.0))


class DocRefChecker(unittest.TestCase):
    def test_skill_docs_have_no_dangling_section_refs(self):
        self.assertEqual(check_docs.main(), 0)

    def test_target_path_resolves_bare_and_prefixed_forms(self):
        # 裸文件名按 references/ → 根目录解析；带前缀照用；反引号不参与路径
        self.assertTrue(os.path.isfile(check_docs._target_path("image_options.md")))
        self.assertTrue(os.path.isfile(check_docs._target_path("SKILL.md`")))
        self.assertTrue(os.path.isfile(check_docs._target_path("references/writing.md")))
        self.assertFalse(os.path.isfile(check_docs._target_path("nope.md")))


if __name__ == "__main__":
    unittest.main()
