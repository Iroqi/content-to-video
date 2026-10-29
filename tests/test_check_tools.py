import contextlib
import io
import json
import os
import re
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
from _images_schema import validate_images_json


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

    def test_tab_obfuscated_scheme_href_is_error(self):
        """字符引用混淆的 scheme 与净化器同一口径：`java&#9;script:` 在 XML
        解析后落成 `java\tscript:`，裸正则看不见、浏览器看得见——自查若另写
        一份正则就会在这里放行，而净化层照样删 href（两道门两个答案）。
        只测字符引用、不测字面制表符：XML 属性值归一化把字面 tab 规范成空格
        （天然惰性），保留 tab 的唯一写法就是 &#9;/&#10;/&#13; 字符引用。
        """
        for bad in ('java&#9;script:alert(1)', 'java&#10;script:alert(1)',
                    'java&#13;script:alert(1)'):
            with self.subTest(href=bad):
                errs, _ = self.run_check(
                    GOOD.replace("</svg>", f'<image href="{bad}"/></svg>'))
                self.assertTrue(any("外链" in e for e in errs), errs)

    def test_css_url_check_uses_sanitizer_preprocessing(self):
        """<style> 外链判据与净化器同一份预处理：CSS 转义（`\68ttp:`）与引号后
        带空格的写法在浏览器眼里都是外链，必须报；注释包住的反而不报（净化层先
        剥注释，那一串根本不进成片）。
        """
        bs = chr(92)
        errs, _ = self.run_check(
            GOOD.replace("</svg>",
                         f'<style>.a{{background:url({bs}68ttp://evil.com/x.png)}}</style></svg>'))
        self.assertTrue(any("外链 url()" in e for e in errs), errs)
        errs, _ = self.run_check(
            GOOD.replace("</svg>",
                         '<style>.a{background:url( "https://evil.com/x.png" )}</style></svg>'))
        self.assertTrue(any("外链 url()" in e for e in errs), errs)
        errs, _ = self.run_check(
            GOOD.replace("</svg>",
                         '<style>/* url(https://evil.com/x.png) */ .a{fill:red}</style></svg>'))
        self.assertFalse(any("外链 url()" in e for e in errs), errs)

    def test_css_url_file_scheme_is_error(self):
        errs, _ = self.run_check(
            GOOD.replace("</svg>",
                         '<style>.a{background:url(file:///etc/motd)}</style></svg>'))
        self.assertTrue(any("外链 url()" in e for e in errs), errs)

    def test_entity_declaration_refused(self):
        errs, _ = self.run_check('<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertTrue(errs)

    def test_background_family_as_text_fill(self):
        """旧主题遗留的深蓝填充仍必须是 error。

        早先这条走"背景色族"字面量判定；族集与对比度判据重复（族内色对当前
        dark stops 的对比度全部 <1.3:1）且字面量已漂移，删除后由同一条对比度
        判据接管——回归钉住：换判据不换结论。
        """
        errs, _ = self.run_check(GOOD.replace("#dbe6f5", "#1a2536"))
        self.assertTrue(any("对比度" in e for e in errs), errs)

    def test_small_font_warns(self):
        _, warns = self.run_check(GOOD.replace('font-size="36"', 'font-size="18"'))
        self.assertTrue(any("26px" in w for w in warns))

    def test_slot_font_scale_follows_the_aspects_own_box(self):
        """槽位字号折算必须按**当前画幅的框**算，不能一律用竖屏 980。

        横屏槽位是 1067×800（_template.layout.landscape.image），拿 980 去折
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

    def test_canvas_opacity_zero_is_the_start_state(self):
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
        # 槽位那条各说各的理由，措辞不共用：画布专属的"氛围光"不出现在槽位 warn 里
        _, sw = self.run_check(self._canvas_svg(body), layout="slot")
        self.assertTrue(any("色差框" in w for w in sw))
        self.assertEqual([w for w in sw if "氛围光" in w], [])

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

    def test_text_oob_judged_in_viewbox_units(self):
        """缩放写法（width/height 与 viewBox 差一个倍数）：文字坐标写在 viewBox
        系里，越界判定必须按 viewBox 量——拿固有像素尺寸去比会把贴边被裁掉的字
        放行（此前 y=330 落在 300 高的 viewBox 之外，却因 h=600 而零 error）。
        """
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" '
               'viewBox="0 0 400 300"><style>text{font-family:"s"}</style>'
               '<text x="200" y="330" font-size="20" fill="#ffffff">越界</text></svg>')
        errs, _ = self.run_check(svg)
        self.assertTrue(any("越出画布" in e for e in errs), errs)

    def test_full_bleed_plate_is_not_oob(self):
        # 满幅背景 rect 本来就是铺满，不算越界（比例检查已另行把关）
        errs, _ = self.run_check(GOOD.replace(
            "</svg>", '<rect width="980" height="735" fill="#0b1020"/></svg>'))
        self.assertFalse(any("越出画布" in e for e in errs), errs)

    # ── 草稿档（--draft）：三条"只有定稿才有资格判死"的判据松开 ────

    def test_draft_downgrades_slot_ratio_to_warn(self):
        # 槽位是 cover 取景，比例错了代价只是边缘字被裁：草稿期画个大概比例
        # 不该被拦，定稿自查（不带 --draft）照旧 error
        svg = GOOD.replace('height="735" viewBox="0 0 980 735"',
                           'height="980" viewBox="0 0 980 980"')
        errs, _ = self.run_check(svg)
        self.assertTrue(any("不等比" in e for e in errs), errs)
        errs, warns = check_svg.check_file(
            write(self.tmp.name, "a.svg", svg), "slot", "portrait", "dark",
            draft=True)
        self.assertFalse(any("不等比" in e for e in errs), errs)
        self.assertTrue(any("草稿档降为提示" in w for w in warns), warns)

    def test_draft_downgrades_slot_contrast_to_warn(self):
        # 槽位字真落在页底渐变上，<3:1 定稿必拦；草稿颜色未定，先降提示不挡探索
        svg = GOOD.replace("#dbe6f5", "#1a2536")
        errs, _ = self.run_check(svg)
        self.assertTrue(any("对比度" in e for e in errs), errs)
        errs, warns = check_svg.check_file(
            write(self.tmp.name, "a.svg", svg), "slot", "portrait", "dark",
            draft=True)
        self.assertFalse(errs, errs)
        self.assertTrue(any("草稿档降为提示" in w for w in warns), warns)

    def test_draft_silences_loose_ends(self):
        # 端点悬空是 craft 品味（"线要结束在某处"）：探索期的草图满屏悬线，
        # 草稿档整条跳过；定稿自查照旧逐条点名
        svg = GOOD.replace(
            "</svg>", '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/></svg>')
        _, warns = self.run_check(svg)
        self.assertTrue(any("端点悬空" in w for w in warns), warns)
        _, warns = check_svg.check_file(
            write(self.tmp.name, "a.svg", svg), "slot", "portrait", "dark",
            draft=True)
        # 精确匹配真悬空告警（"端点悬空：" 带冒号）；草稿档的降级点名提示里也
        # 会出现"端点悬空"字样，不能拿裸词匹配
        self.assertFalse(any("端点悬空：" in w for w in warns), warns)
        # 草稿档必须留痕：报告里有点名"降级了哪些判据"
        self.assertTrue(any("草稿档（--draft）" in w for w in warns), warns)

    # ── 线条端点落点（warn）：线要结束在某处 ───────────────────

    def test_loose_line_end_warns(self):
        _, warns = self.run_check(GOOD.replace(
            "</svg>", '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/></svg>'))
        self.assertTrue(any("端点悬空" in w for w in warns), warns)

    def test_loose_end_tolerance_is_measured_in_viewbox_units(self):
        """端点容差必须按 viewBox 短边算，不能按根标签的 width/height。

        缩放写法（width="1960" height="1470" viewBox="0 0 980 735"）下，图元坐标
        仍是 viewBox 系；拿像素短边算会把容差放大一倍，落在中间的悬空端点静默
        漏报。同一次几何，只换根标签的 px 尺寸不该改变判定。
        """
        # 端点 (400,500) 距另一条竖线 4 单位：viewBox 容差 max(2,735×0.004)=2.94
        # → 该报；放大后的 5.88 → 曾被吞掉。
        body = ('<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/>'
                '<line x1="404" y1="500" x2="404" y2="300" stroke="#fff"/></svg>')
        for tag in ('<svg xmlns="http://www.w3.org/2000/svg" width="980" '
                    'height="735" viewBox="0 0 980 735">',
                    '<svg xmlns="http://www.w3.org/2000/svg" width="1960" '
                    'height="1470" viewBox="0 0 980 735">'):
            with self.subTest(tag=tag):
                svg = (tag + '<style>text{font-family:"Microsoft YaHei",sans-serif}'
                       '</style>'
                       '<text x="40" y="100" font-size="36" fill="#dbe6f5">反向传播'
                       '</text>' + body)
                _, warns = self.run_check(svg)
                self.assertTrue(any("端点悬空" in w for w in warns), warns)

    def test_cjk_punctuation_counts_as_full_width_in_text_boxes(self):
        """字宽粗估要把中文标点算成等宽。

        只认汉字区间（U+3400–U+9FFF）时「，。」「」和假名按 0.55em 算，中文句子的
        估算宽度系统性**偏小**——偏小是危险方向：端点落在文字上的判断会漏报。
        """
        # 15 字里 2 个是标点。旧区间把它们按 0.55em 算：13×1.0 + 2×0.55 = 14.1em
        # （507.6px）；按等宽算是 15em（540px）。差出来的 32px 足以让落在句末的
        # 端点掉出文字盒——端点悬空误报。
        line = "梯度下降，一步一步走到最低点。"
        self.assertEqual(check_svg.est_text_width(line, 36), 15 * 36)
        self.assertLess(check_svg.est_text_width(line, 36) - 36 * 2 * 0.45,  # 旧算法
                        check_svg.est_text_width(line, 36))
        for ch in ("，", "。", "、", "！", "（", "）", "あ", "ア", "　"):
            self.assertTrue(check_svg._is_wide(ch), ch)
        for ch in ("a", "1", ".", " "):
            self.assertFalse(check_svg._is_wide(ch), ch)

    def test_east_asian_width_covers_hangul_and_cjk_ext(self):
        """手写汉字区间漏掉的码位，标准库要认得（少估宽度是危险方向）。"""
        for ch in ("한", "글", "\U00020000", "豈"):   # 韩文音节 / CJK 扩展 B / 兼容表意文字
            self.assertTrue(check_svg._is_wide(ch), ch)
        # 半角假名是真半角：旧区间把它算成全角（多估，反方向）
        self.assertFalse(check_svg._is_wide("ｱ"))

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

    def test_stroked_malformed_path_does_not_crash(self):
        # 带 stroke 的坏 d 曾让 check_file 崩在端点落点收集处的裸 traceback
        # （无 stroke 的 path 走不到那一步，原有测试没盖住）；与 _shape_bounds
        # 同一口径：跳过该 path 的几何判定，不能把悬空判定建立在读不懂的几何上。
        errs, warns = self.run_check(GOOD.replace(
            "</svg>", '<path d="M150 800 Q 90" stroke="#fff"/></svg>'))
        self.assertIsInstance(errs, list)
        self.assertIsInstance(warns, list)
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

    # ── 本轮帕累托修复的证伪 ──────────────────────────────

    def test_line_ending_on_filled_path_edge_passes(self):
        """证伪：填充态 path 的轮廓也是合法落点，此前只收 stroke_on 的 path。

        线接进色块边缘正是 craft 规则说的"结束在某处"；旧代码把填充 path 排除在
        segs 外，这条线被误报"端点悬空"——误报训练人忽略 warn，比漏报更贵。
        """
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="200" y2="500" stroke="#fff"/>'
            '<path d="M200 500 L400 500 L400 600 Z" fill="#123456"/></svg>'))
        self.assertFalse(any("端点悬空" in w for w in warns), warns)

    def test_dangling_warn_reports_both_endpoints(self):
        """证伪：两端都悬空时只报 ends[0]，第二个坐标要修完重跑才看得见。"""
        _, warns = self.run_check(GOOD.replace(
            "</svg>",
            '<line x1="100" y1="500" x2="400" y2="500" stroke="#fff"/></svg>'))
        w = next(w for w in warns if "端点悬空：" in w)
        self.assertIn("(100, 500)", w)
        self.assertIn("(400, 500)", w)

    def test_commented_out_animation_is_not_a_wallclock(self):
        """证伪：注释里藏的 `; animation:` 字样被误报成墙钟动画。

        净化层先剥注释再判（_clean_style_text），注释里的假声明根本不进成片；
        自查拿原始文本搜则命中——作者画一张静态图也吃一条假 warn，且会去改一个
        已经被删掉的东西。误报训练人忽略 warn，比漏报更贵。
        """
        svg = GOOD.replace(
            'sans-serif}</style>',
            'sans-serif;/*; animation: x 1s */}</style>')
        _, warns = self.run_check(svg)
        self.assertFalse(any("墙钟" in w for w in warns), warns)

    def test_animation_hidden_behind_a_comment_gap_is_caught(self):
        """证伪（反方向）：`color:red;/*c*/animation:…` 逃过裸文本判定。

        注释隔在 `;` 与声明之间时，lookbehind  adjacency 断了；净化层剥掉注释后
        这条 animation 声明**会被删掉**（成片里静默变化），自查却报"通过"——
        报错与净化各说一套。两条必须同源：与净化器共用同一套预处理。
        """
        svg = GOOD.replace(
            "<text ", '<rect width="4" height="4" '
            'style="color:red;/*c*/animation:spin 1s"/><text ')
        _, warns = self.run_check(svg)
        self.assertTrue(any("墙钟" in w for w in warns), warns)

    def test_css_escaped_animation_is_a_wallclock(self):
        """证伪：`\\61 nimation`（CSS 转义的 animation）逃过裸文本判定——
        净化层还原转义后删得掉，自查却报"通过"，报错与净化各说一套。"""
        svg = GOOD.replace(
            "<text ", '<rect width="4" height="4" style="\\61 nimation: spin 1s'
            ' linear infinite"/><text ')
        _, warns = self.run_check(svg)
        self.assertTrue(any("墙钟" in w for w in warns), warns)

    def test_scale_uses_viewbox_width_not_px_width(self):
        """证伪：scale = cw/w 拿固有宽度当分母，缩放写法下折算字号少算一半。

        width="1960" viewBox="0 0 980"：36px 字号折成槽位的 eff 应是 36×(980/980)=36
        （slot 宽 980），旧代码按 1960 折算成 18——"两画幅缩放后读不出来"的 warn
        该在 24px 以下才响，18px 却会被误判成达标（漏 warn）。
        """
        body = ('<text x="40" y="300" font-size="30" fill="#dbe6f5">小字</text>'
                '</svg>')
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1960" '
               'height="1470" viewBox="0 0 980 735">'
               '<style>text{font-family:"Microsoft YaHei",sans-serif}</style>'
               '<text x="40" y="100" font-size="36" fill="#dbe6f5">反向传播'
               '</text>' + body)
        # slot portrait 宽 980、viewBox 宽 980：30px 折后仍是 30px（>24 下限，
        # 不该有字号 warn）；旧代码折成 15px，会误报"折算后最小字号约 15px"。
        _, warns = self.run_check(svg)
        self.assertFalse(any("折算后最小字号" in w for w in warns), warns)


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

    def test_draft_aggregates_full_bleed_warns(self):
        # 满幅底板：定稿自查逐条点名（两条元素两条 warn）；草稿档合并成一条
        # 汇总并留痕，探索期铺底色试构图不被逐条打断
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
               'viewBox="0 0 1080 1440">'
               '<rect width="1080" height="1440" fill="#0b1020"/>'
               '<rect width="1080" height="1440" fill="#0b1020"/></svg>')
        p = write(self.tmp.name, "c.svg", svg)
        _, warns = check_svg.check_file(p, "canvas", "portrait", "dark")
        self.assertEqual(
            len([w for w in warns
                 if "疑似铺满 viewBox 的满幅底板" in w
                 or "整页画布铺了一张满幅底板" in w]), 2, "定稿逐条点名")
        _, warns = check_svg.check_file(p, "canvas", "portrait", "dark",
                                        draft=True)
        fb = [w for w in warns if "合并为这一条" in w]
        self.assertEqual(len(fb), 1, "草稿档合并成一条")
        self.assertTrue(any("草稿档（--draft）" in w for w in warns), warns)

    def test_band_judged_in_viewbox_units(self):
        """缩放写法（2160×2880 / viewBox 1080×1440）：空带判据量的是 viewBox 系。
        此前拿固有像素尺寸当页高，把 y∈[20,1400] 的真空带算成 y≈1440–2880、
        只占 50%（实际 96%）——位置和占比全错，作者对着提示找不到该补的地方。
        """
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="2160" height="2880" '
               'viewBox="0 0 1080 1440">'
               '<rect x="0" y="0" width="1080" height="20" fill="#fff"/>'
               '<rect x="0" y="1400" width="1080" height="40" fill="#fff"/></svg>')
        p = write(self.tmp.name, "c.svg", svg)
        _, warns = check_svg.check_file(p, "canvas", "portrait", "dark")
        got = [w for w in warns if "空带" in w]
        self.assertEqual(len(got), 1)
        self.assertIn("y≈20–1400", got[0])
        self.assertIn("占页高 96%", got[0])

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
        self.assertEqual(self.size('width="980px" height="735px"'),
                         (980.0, 735.0, "", 980.0, 735.0))

    def test_other_unit_and_prefixed_attr_not_misread(self):
        self.assertIsNone(self.size('width="50%" height="50%"'))
        # min-width 不该被当成 width（check_svg 用 root.get("width") 天然不会）
        self.assertEqual(self.size('min-width="7px" width="980" height="735"'),
                         (980.0, 735.0, "", 980.0, 735.0))

    def test_viewbox_size_comes_back_alongside_the_intrinsic_one(self):
        """第 4/5 个返回值是坐标系尺寸：缩放写法下它与固有尺寸是两码事。

        字号/坐标写在 viewBox 那一套里，折算分母必须是它（拿固有宽度折算会少算
        一半）；没有 viewBox 时退固有尺寸——两者本就合一。
        """
        self.assertEqual(self.size('width="2160" height="2880" viewBox="0 0 1080 1440"'),
                         (2160.0, 2880.0, "", 1080.0, 1440.0))

    def test_comment_injection_cannot_forge_a_size(self):
        """注释里放一句 `<svg width="10" height="10">`：读文件头的正则版实测
        把它当成固有尺寸（返 10×10），比例校验于是对着一个假尺寸开灯。
        ET 解析不过注释，注入无从作伪。
        """
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('<!-- <svg width="10" height="10"> -->\n'
                    '<svg xmlns="http://www.w3.org/2000/svg" width="1080" '
                    'height="1440" viewBox="0 0 1080 1440"></svg>')
        self.assertEqual(gen_hyperframes._svg_intrinsic_size(self.path),
                         (1080.0, 1440.0, "", 1080.0, 1440.0))

    def test_root_beyond_the_head_slice_is_not_a_false_negative(self):
        """根标签落在头 8192 字节之外（大注释/前置处理指令）：读头版实测返
        None，把一张好图误报成"读不出固有尺寸"。整文解析与位置无关。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("<!-- " + "x" * 9000 + " -->\n"
                    '<svg xmlns="http://www.w3.org/2000/svg" width="980" '
                    'height="735"></svg>')
        self.assertEqual(gen_hyperframes._svg_intrinsic_size(self.path),
                         (980.0, 735.0, "", 980.0, 735.0))


class SvgTextMetrics(unittest.TestCase):
    """_svg_text_metrics：注释不许抬高最小字号、不许吹大 <text> 个数。

    全文正则版实测两处中招：`<!-- font-size="99" -->` 把最小字号抬到 99px
    （折算可读性对着一份假账判），注释里的 `<text>` 幽灵计进个数。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "a.svg")

    def metrics(self, body):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" '
                    'viewBox="0 0 10 10">' + body + "</svg>")
        return gen_hyperframes._svg_text_metrics(self.path)

    def test_comment_injection_cannot_move_the_numbers(self):
        got = self.metrics(
            '<!-- font-size="99" --><text id="a" x="0" y="0" font-size="26">甲</text>'
            '<text id="b" x="0" y="1" style="font-size: 18px">乙</text>'
            '<!-- <text>幽灵</text> -->')
        self.assertEqual(got, (18.0, 2))

    def test_three_font_size_channels_are_collected(self):
        """呈现属性、inline style、<style> 规则三个来路都收（后者先剥注释）。"""
        got = self.metrics(
            '<style>/* font-size:99px; */ .c{font-size:22px}</style>'
            '<text id="a" x="0" y="0" class="c" font-size="30">甲</text>'
            '<text id="b" x="0" y="1" style="font-size: 18.5px">乙</text>')
        self.assertEqual(got, (18.5, 2))

    def test_relative_units_stay_unreadable(self):
        """em / % 一律不认：没有布局引擎，硬猜会把"读不准"混成"能读"。"""
        self.assertEqual(self.metrics(
            '<text id="a" x="0" y="0" font-size="1.2em">甲</text>'
            '<text id="b" x="0" y="1" font-size="150%">乙</text>'), (None, 2))

    def test_broken_svg_reports_nothing_rather_than_crashing(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("<svg><unclosed>")
        self.assertEqual(gen_hyperframes._svg_text_metrics(self.path), (None, 0))


class EpilogExampleRuns(unittest.TestCase):
    """--help 尾巴那份"实测跑通"的 images.json：声明就得被测试兜住。

    证伪：seg2 曾挂 `stage:"keep"`，而 seg1 是不写 animate 的 PNG——生成期
    直接报"上一段没有可接续的内联画面"，照抄命令第一步就红。stage:keep 是
    跨页接续标记（接的必须是同一张图的上一页导演档），不是素材条目的一种
    形状，不放进"三种素材条目"的示例里。
    """

    def test_epilog_example_passes_schema_and_animate_prepare(self):
        epi = gen_hyperframes._epilog()
        block = epi.split("可直接照抄的 images.json（实测跑通）：", 1)[1]
        images = json.loads(block[block.index("{"):block.rindex("}") + 1])
        # 契约层：三种条目形状都过 images.json 校验
        validate_images_json(images)
        # 生成期：真文件 + 真 manifest 形状，animate_prepare 零 error
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "images"))
        with open(os.path.join(tmp.name, "images", "seg1.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with open(os.path.join(tmp.name, "images", "seg2.svg"), "w",
                  encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="1080" '
                    'height="1440" viewBox="0 0 1080 1440">'
                    '<rect id="bar-1" x="0" y="0" width="10" height="10"/>'
                    '<rect id="bar-2" x="0" y="0" width="10" height="10"/></svg>')
        with open(os.path.join(tmp.name, "images", "seg3.gif"), "wb") as f:
            f.write(b"GIF89a" + b"\x00" * 32)
        segments = [
            {"id": "seg1", "layout": "slot",
             "sentences": [{"start_time": 0.0, "end_time": 1.0},
                           {"start_time": 1.0, "end_time": 2.0}]},
            {"id": "seg2", "layout": "slot",
             "sentences": [{"start_time": 2.0, "end_time": 3.0},
                           {"start_time": 3.0, "end_time": 4.0}]},
            {"id": "seg3", "layout": "slot",
             "sentences": [{"start_time": 4.0, "end_time": 5.0}]},
        ]
        errs, _warns = gen_hyperframes.animate_prepare(images, segments, tmp.name)
        self.assertEqual(errs, [])


class CheckSvgCommandLine(unittest.TestCase):
    """`python scripts/check_svg.py` 的退出码：2=没找到文件，1=有 error，0=干净。

    它是第 3 步引用的生产工具（SKILL.md 明确要留着），退出码是它被编排进脚本时
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

    def test_animate_timeliney_expands_svg_only(self):
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


class LayoutAuto(unittest.TestCase):
    """`--layout auto`：混用目录必须一次跑完，且不再把另一类文件误判成 error。

    起因是实测复现的一条摩擦：一个 `images/` 里同时放着槽位图（980×735）和整页
    画布（1080×1440），`--layout slot` 把画布报成比例 error、`--layout canvas`
    把槽位报成比例 error——两类文件都是对的，错的只是"一次只能选一档"。于是混用
    目录必须跑两遍，每一次都带着一批假 FAIL。
    """

    SLOT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
                'viewBox="0 0 980 735"><style>text{font-family:sans-serif}</style>'
                '<text x="40" y="100" font-size="36" fill="#dbe6f5">槽位</text></svg>')
    CANVAS_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
                  'viewBox="0 0 1080 1440"><style>text{font-family:sans-serif}</style>'
                  '<text x="90" y="150" font-size="64" fill="#dbe6f5">整页</text>'
                  '<rect x="90" y="900" width="900" height="400" fill="#1e293b"/></svg>')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["check_svg.py"] + list(argv)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = check_svg.main()
        return rc, out.getvalue(), err.getvalue()

    def test_mixed_directory_passes_in_one_run(self):
        write(self.tmp.name, "slot.svg", self.SLOT_SVG)
        write(self.tmp.name, "canvas.svg", self.CANVAS_SVG)
        rc, out, _err = self._main([self.tmp.name, "--layout", "auto"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(re.findall(r"\[FAIL\]", out)), 0, out)
        self.assertIn("auto → slot", out)
        self.assertIn("auto → canvas", out)

    def test_forced_layout_still_false_reports_the_other_kind(self):
        """同一份混用目录在旧口径下必然互相误报——留着这条，防止 auto 哪天退化成
        "干脆什么都不判"。"""
        write(self.tmp.name, "slot.svg", self.SLOT_SVG)
        write(self.tmp.name, "canvas.svg", self.CANVAS_SVG)
        rc, out, _err = self._main([self.tmp.name, "--layout", "slot"])
        self.assertEqual(rc, 1, out)
        self.assertIn("canvas.svg", out.split("[FAIL]")[1].split("\n")[0])

    def test_auto_pins_aspect_for_full_canvas(self):
        """整页画布的比例自带画幅信息：--aspect 写反时按图本身的比例定画幅，
        不拿另一档的框（安全边距、折算字号）去量它。"""
        p = write(self.tmp.name, "c.svg", self.CANVAS_SVG)
        box = {}
        check_svg.check_file(p, "auto", "landscape", "dark", resolved=box)
        self.assertEqual(box["layout"], "canvas")
        self.assertEqual(box["aspect"], "portrait")

    def test_auto_keeps_declared_aspect_for_slots(self):
        """槽位两画幅都是 4:3，比例分辨不出——画幅是作者声明的事，不替他改。"""
        p = write(self.tmp.name, "s.svg", self.SLOT_SVG)
        box = {}
        check_svg.check_file(p, "auto", "landscape", "dark", resolved=box)
        self.assertEqual(box["layout"], "slot")
        self.assertEqual(box["aspect"], "landscape")

    def test_uninferable_ratio_errors_without_a_second_ratio_error(self):
        """推断不出就是推断不出：一条点名比例的 error，不叠第二句"目标 4:3"
        （第二句会把"不知道按哪档查"说成"槽位图画错了"）。"""
        bad = ('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1000" '
               'viewBox="0 0 900 1000"><style>text{font-family:sans-serif}</style>'
               '<text x="40" y="100" font-size="36" fill="#dbe6f5">x</text></svg>')
        p = write(self.tmp.name, "b.svg", bad)
        errs, _ = check_svg.check_file(p, "auto", "portrait", "dark")
        self.assertEqual(len([e for e in errs if "推断不出" in e]), 1, errs)
        self.assertEqual(len([e for e in errs if "不等比" in e]), 0, errs)

    def test_explicit_layout_is_untouched(self):
        """auto 只解 auto：显式给档位时必须原样走老路径（含那条比例 error）。"""
        p = write(self.tmp.name, "b.svg", self.CANVAS_SVG)
        errs, _ = check_svg.check_file(p, "slot", "portrait", "dark")
        self.assertTrue(any("不等比" in e for e in errs), errs)


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


class ImageSnippetChecker(unittest.TestCase):
    """文档里的 images.json 片段必须过契约层。

    这条此前**没有接线**：`check_image_snippets.py` 只在人手跑时才存在，示例跟着
    文档搬家（`director.md` → `svg-motion.md`）后它静默失败了两条，而所有测试照旧
    全绿。断言的写法与上面两个检查器同一条：只调 main()。
    """

    def test_doc_image_snippets_validate(self):
        import check_image_snippets
        self.assertEqual(check_image_snippets.main(), 0)


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
