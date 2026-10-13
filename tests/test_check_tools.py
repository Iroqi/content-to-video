import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import _helpers as H  # noqa: F401  sys.path 装配
import check_svg
import check_docs
import check_numbers
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

    def test_file_and_data_scheme_hrefs_are_errors(self):
        """写图门禁与净化器同一口径：file:/data: 这类 scheme 也是外部资源。"""
        for bad in ('file:///etc/passwd', 'data:text/plain,abc',
                    'javascript:alert(1)'):
            with self.subTest(href=bad):
                errs, _ = self.run_check(
                    GOOD.replace("</svg>", f'<image href="{bad}"/></svg>'))
                self.assertTrue(any("外链" in e for e in errs), errs)

    def test_fragment_and_relative_hrefs_are_allowed(self):
        errs, _ = self.run_check(
            GOOD.replace("</svg>", '<use href="#t"/><image href="images/p.png"/></svg>'))
        self.assertFalse(any("外链" in e for e in errs), errs)

    def test_css_url_file_scheme_is_error(self):
        errs, _ = self.run_check(
            GOOD.replace("</svg>",
                         '<style>.a{background:url(file:///etc/motd)}</style></svg>'))
        self.assertTrue(any("外链 url()" in e for e in errs), errs)

    def test_entity_declaration_refused(self):
        errs, _ = self.run_check('<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertTrue(errs)

    def test_background_family_as_text_fill(self):
        errs, _ = self.run_check(GOOD.replace("#dbe6f5", "#1a2536"))
        self.assertTrue(any("背景色族" in e for e in errs))

    def test_small_font_warns(self):
        _, warns = self.run_check(GOOD.replace('font-size="36"', 'font-size="18"'))
        self.assertTrue(any("26px" in w for w in warns))

    def test_slot_font_scale_follows_the_aspects_own_box(self):
        """槽位字号折算必须按**当前画幅的框**算，不能一律用竖屏 980。

        横屏图栏是 1067×800（_template.layout.landscape.image），拿 980 去折
        会把 26px 算成 24px——一张本来达标的图被误报"小于 26px 下限"，且建议
        尺寸恒写竖屏的 980×735。同一张 1067×800、26px 的图在横屏必须不报，
        换成 980×735 的竖屏框才该报。
        """
        body = ('<svg xmlns="http://www.w3.org/2000/svg" width="1067" height="800">'
                '<style>text{font-family:"Microsoft YaHei",sans-serif}</style>'
                '<text x="40" y="100" font-size="26" fill="#dbe6f5">标签</text></svg>')
        _, landscape_warns = self.run_check(body, layout="slot", aspect="landscape")
        self.assertEqual([w for w in landscape_warns if "26px" in w], [])
        # 同一张图按竖屏框判（980/1067 ≈ 0.918）就该报——证明警告不是关掉了
        _, portrait_warns = self.run_check(body, layout="slot", aspect="portrait")
        self.assertTrue(any("26px" in w for w in portrait_warns))

    def test_slot_ratio_hint_names_the_aspects_own_box(self):
        """比例不符的报错文案要点名当前画幅的槽位尺寸。"""
        body = ('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="800">'
                '<text x="40" y="100" font-size="36" fill="#dbe6f5">标签</text></svg>')
        errs, _ = self.run_check(body, layout="slot", aspect="landscape")
        self.assertTrue(any("1067×800" in e for e in errs), errs)

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

    # ── 图元越出画布（error）───────────────────────────────────

    def test_shape_out_of_bounds_is_error(self):
        # rect 右缘伸出画布 40px：cover 按框裁切，贴边内容必被裁掉
        errs, _ = self.run_check(GOOD.replace(
            "</svg>", '<rect x="940" y="20" width="80" height="40" fill="#fff"/></svg>'))
        self.assertTrue(any("越出画布" in e for e in errs), errs)

    def test_in_bounds_shape_passes(self):
        errs, _ = self.run_check(GOOD.replace(
            "</svg>", '<rect x="20" y="600" width="120" height="40" fill="#fff"/></svg>'))
        self.assertFalse(any("越出画布" in e for e in errs), errs)

    def test_circle_and_polygon_oob_are_errors(self):
        for body in ('<circle cx="990" cy="60" r="40" fill="#fff"/>',
                     '<polygon points="0,700 980,700 1100,800" fill="#fff"/>'):
            with self.subTest(body=body):
                errs, _ = self.run_check(GOOD.replace("</svg>", body + "</svg>"))
                self.assertTrue(any("越出画布" in e for e in errs), errs)

    def test_path_oob_uses_real_span(self):
        # path 越界按 x_span/y_span 的真实跨度（含曲线控制点）判，不靠目测
        errs, _ = self.run_check(GOOD.replace(
            "</svg>", '<path d="M0 700 C 300 300 600 300 1000 200" stroke="#fff"/></svg>'))
        self.assertTrue(any("越出画布" in e for e in errs), errs)

    def test_text_oob_vertical_and_anchor(self):
        errs, _ = self.run_check(GOOD.replace('y="100"', 'y="730"'))
        self.assertTrue(any("越出画布" in e for e in errs), errs)
        errs, _ = self.run_check(GOOD.replace('x="40"', 'x="-20"'))
        self.assertTrue(any("画布左缘" in e for e in errs), errs)

    def test_full_bleed_plate_is_not_oob(self):
        # 满幅背景 rect 本来就是铺满，不算越界（比例检查已另行把关）
        errs, _ = self.run_check(GOOD.replace(
            "</svg>", '<rect width="980" height="735" fill="#0b1020"/></svg>'))
        self.assertFalse(any("越出画布" in e for e in errs), errs)

    # ── 线条端点落点（warn）：线要结束在某处 ───────────────────

    def test_loose_line_end_warns(self):
        _, warns = self.run_check(GOOD.replace(
            "</svg>", '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/></svg>'))
        self.assertTrue(any("端点悬空" in w for w in warns), warns)

    def test_line_ending_on_another_stroke_passes(self):
        # 一条线接到另一条线的端点上，两头都根住了
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/>'
            '<line x1="400" y1="500" x2="400" y2="300" stroke="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_line_ending_on_dot_or_ring_passes(self):
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/>'
            '<circle cx="400" cy="500" r="3" fill="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)
        # 大圆的圆周带也算根住：端点在圆周附近（圆心 (600,500) r=30，端点取 (630,500)）
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="630" y2="500" stroke="#fff"/>'
            '<circle cx="600" cy="500" r="30" fill="none" stroke="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_line_ending_on_text_passes(self):
        _, warns = self.run_check(GOOD.replace(
            "</svg>", '<line x1="40" y1="100" x2="40" y2="200" stroke="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_closed_and_fill_only_shapes_not_checked(self):
        # 闭合多边形端点根在自己轮廓上；无描边的 fill path 不是"线"
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<polygon points="100,100 200,100 150,200" fill="#fff" stroke="#fff"/>'
            '<path d="M100 300 L300 300 L200 400" fill="#123456"/>'
            '<path d="M100 500 L300 500 L200 600 Z" stroke="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_relative_path_endpoints_resolve_absolutely(self):
        # 相对命令的端点也要走光标算成绝对坐标，再判是否悬空
        _, warns = self.run_check(GOOD.replace(
            "</svg>", '<path d="M100 400 l200 0" stroke="#fff"/></svg>'))
        self.assertTrue(any("端点悬空" in w for w in warns), warns)
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<path d="M100 400 l200 0" stroke="#fff"/>'
            '<path d="M300 400 v100" stroke="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_filled_disc_roots_an_endpoint_at_its_center(self):
        # 实心大圆盘：线画进圆里就算根住（折线终点落在圆心的常见画法）
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<polyline points="100,500 400,500" stroke="#fff"/>'
            '<circle cx="400" cy="500" r="20" fill="#fff"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)
        # 同尺寸但只描边不填充：圆心不算根住，圆周才算
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<polyline points="100,500 400,500" stroke="#fff"/>'
            '<circle cx="400" cy="500" r="20" fill="none" stroke="#fff"/></svg>'))
        self.assertTrue(any("端点悬空" in w for w in warns), warns)

    def test_data_free_ends_skips_the_stroke_check(self):
        # 有意悬空的装饰线（canvas_kit 的 rule 分隔线/下划线）用 data-free-ends 声明豁免
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff" data-free-ends="1"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)


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


class CheckSvgCommandLine(unittest.TestCase):
    """`python scripts/check_svg.py` 的退出码：2=没找到文件，1=有 error，0=干净。

    它是第 4 步引用的生产工具（SKILL.md 明确要留着），退出码是它被编排进脚本时
    唯一能读的信号——没人测过，改坏了也不知道。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["check_svg.py"] + list(argv)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = check_svg.main()
        return rc, out.getvalue(), err.getvalue()

    def test_clean_file_is_zero(self):
        p = write(self.tmp.name, "a.svg", GOOD)
        rc, out, _err = self._main([p])
        self.assertEqual(rc, 0, out)
        self.assertIn("[OK]", out)

    def test_file_with_errors_is_one(self):
        p = write(self.tmp.name, "b.svg", GOOD.replace(
            'height="735" viewBox="0 0 980 735"', 'height="980" viewBox="0 0 980 980"'))
        rc, out, _err = self._main([p])
        self.assertEqual(rc, 1, out)
        self.assertIn("[FAIL]", out)
        self.assertIn("1 条 error", out)

    def test_directory_expands_svg_only(self):
        write(self.tmp.name, "a.svg", GOOD)
        write(self.tmp.name, "notes.txt", "not an svg")
        rc, out, _err = self._main([self.tmp.name])
        self.assertEqual(rc, 0, out)
        self.assertIn("1 个文件", out)     # .txt 不该被当 SVG 读

    def test_nothing_found_is_two(self):
        empty = os.path.join(self.tmp.name, "nope")
        os.makedirs(empty, exist_ok=True)
        rc, _out, err = self._main([empty])
        self.assertEqual(rc, 2)
        self.assertIn("没找到", err)


class DocRefChecker(unittest.TestCase):
    def test_skill_docs_have_no_dangling_section_refs(self):
        self.assertEqual(check_docs.main(), 0)

    def test_target_path_resolves_bare_and_prefixed_forms(self):
        # 裸文件名按 references/ → 根目录解析；带前缀照用；反引号不参与路径
        self.assertTrue(os.path.isfile(check_docs._target_path("canvas.md")))
        self.assertTrue(os.path.isfile(check_docs._target_path("SKILL.md`")))
        self.assertTrue(os.path.isfile(check_docs._target_path("references/writing.md")))
        self.assertFalse(os.path.isfile(check_docs._target_path("nope.md")))


class DocExampleChecker(unittest.TestCase):
    """文档里的 spec 示例必须真能跑 canvas_kit。

    理由写在 tests/check_examples.py 的模块头：那份示例是从零试错试出来的（撞过
    9 次），示例跑不通就等于让读者再撞一遍。这里只调main()，不另写一份实现——
    同一个断言写两遍，改一处忘另一处就是漏网。
    """

    def test_doc_examples_run(self):
        import check_examples
        self.assertEqual(check_examples.main(), 0)


class FontFloorSingleSource(unittest.TestCase):
    """图内文字可读下限只有一份：三个判它的地方必须读同一个对象。

    26px 此前在 check_svg（MIN_PX）与 gen_hyperframes（CANVAS_MIN_FONT_PX）
    各抄一份。改一边只会让作者"按提示调字号、另一页照样被判超标"，三道门三个答案。
    """

    def test_all_three_read_the_template_constant(self):
        import _template
        self.assertEqual(_template.MIN_READABLE_FONT_PX, 26)
        for mod in (check_svg, gen_hyperframes):
            with self.subTest(mod=mod.__name__):
                self.assertIs(mod.MIN_READABLE_FONT_PX,
                              _template.MIN_READABLE_FONT_PX)

    def test_no_module_redefines_its_own_floor(self):
        """有人再抄一份 `= 26` 时这条会红：门禁只锁真源，抄件是抄件查不出来的。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for rel in ("scripts/check_svg.py", "scripts/gen_hyperframes.py",
                    "scripts/html_renderer.py"):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                src = f.read()
            with self.subTest(rel=rel):
                self.assertNotIn("MIN_PX = ", src)
                self.assertNotIn("FONT_PX = 26", src)


class NumberChecker(unittest.TestCase):

    def test_doc_numbers_match_code(self):
        self.assertEqual(check_numbers.main(), 0)
    def test_every_fact_reads_a_real_code_value(self):
        # 登记错路径的检查器会「因为读不到值而永远失败」或者更糟——被临时放宽后
        # 变成永远绿。所以逐条断言它真能从代码里取到那个数。
        for fid, spec, expect, _docs, _why in check_numbers.FACTS:
            with self.subTest(fact=fid):
                self.assertEqual(check_numbers.read_code_value(spec), expect)

    def test_facts_are_unique(self):
        # 同一条事实登记两次 = 第二条永远被第一条的命中掩盖，等于没检查。
        ids = [f[0] for f in check_numbers.FACTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_references_stay_under_byte_cap(self):
        # 逐份断言留出余量，而不是只靠 main() 那个总闸——总闸响的时候已经胖完了。
        for path in sorted(os.listdir(os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "references"))):
            if not path.endswith(".md"):
                continue
            size = os.path.getsize(os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "references", path))
            with self.subTest(ref=path):
                self.assertLessEqual(size, check_docs.REFERENCE_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
