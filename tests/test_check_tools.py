import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  sys.path 装配
import check_svg
import check_docs
import _director_prepare as _DP


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

    def test_canvas_needs_matching_aspect(self):
        errs, _ = self.run_check(GOOD, layout="canvas", aspect="landscape")
        self.assertTrue(any("不等比" in e for e in errs))

    def test_canvas_downscale_is_reflected_in_font_size(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="2560" height="1440" viewBox="0 0 2560 1440">'
               '<text x="200" y="100" font-size="28" font-family="sans-serif" fill="#fff">x</text></svg>')
        errs, warns = self.run_check(svg, layout="canvas", aspect="landscape")
        self.assertEqual(errs, [])
        self.assertTrue(any("26px" in w for w in warns))   # 28 × 0.75 = 21

    def _canvas_svg(self, body, root_attrs=""):
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
                f'viewBox="0 0 1080 1440"{root_attrs}>' + body + '</svg>')

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
        # 模板本来就在画布页底下画了渐变/网格/氛围光三层，满幅 rect 把它们整个盖掉。
        # 这条从 warn 升成 error：warn 不改退出码，检查器在流水线里就成了摆设，
        # 而"SVG 融不进背景"正是本该被拦下的失败。
        body = ('<rect width="1080" height="1440" fill="#0b1020"/>'
                '<circle cx="540" cy="700" r="300" fill="#38bdf8"/>'
                '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>')
        errs, warns = self.run_check(self._canvas_svg(body), layout="canvas", aspect="portrait")
        got = [e for e in errs if "满幅底板" in e]
        self.assertEqual(len(got), 1)
        self.assertIn("氛围光", got[0])
        self.assertIn("data-ctv-full-bleed", got[0])     # 错误信息要直接给出出路
        self.assertEqual([w for w in warns if "满幅底板" in w], [])
        # 槽位那条各说各的理由，措辞不共用
        se, _ = self.run_check(self._canvas_svg(body), layout="slot")
        self.assertTrue(any("色差框" in e for e in se))
        self.assertEqual([e for e in se if "满幅底板" in e], [])
        # 局部底板（真的需要的那种）不该被牵连
        errs, _ = self.run_check(
            self._canvas_svg('<rect x="140" y="300" width="800" height="500" fill="#16233a"/>'
                             '<text x="200" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>'),
            layout="canvas", aspect="portrait")
        self.assertEqual([e for e in errs if "满幅" in e], [])

    def test_almost_full_bleed_is_still_full_bleed(self):
        """97.8% 宽的底板在画面上和满幅没区别，必须照样拦下。

        判据原来要求宽和高各自越过 0.98，这张 1056×1440（宽 97.8%、高 100%）就
        能静默溜过去——模板三层被盖掉 97.8%，剩下的 2.2% 只够让页缘露条细网格，
        层次一点都回不来。现在按面积判，这类"差一点点"一并归到满幅。
        """
        errs, _ = self.run_check(
            self._canvas_svg('<rect x="0" y="0" width="1056" height="1440" fill="#0b1020"/>'
                             '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>'),
            layout="canvas", aspect="portrait")
        self.assertTrue(any("满幅底板" in e for e in errs), errs)
        # 百分比写法与面积同判
        errs, _ = self.run_check(
            self._canvas_svg('<rect width="100%" height="100%" fill="#0b1020"/>'
                             '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>'),
            layout="canvas", aspect="portrait")
        self.assertTrue(any("满幅底板" in e for e in errs), errs)
        # 贴边才算满幅：同样大的 rect 缩进边距就是局部底板，正当画法
        errs, _ = self.run_check(
            self._canvas_svg('<rect x="60" y="60" width="960" height="1320" fill="#16233a"/>'
                             '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>'),
            layout="canvas", aspect="portrait")
        self.assertEqual([e for e in errs if "满幅" in e], [])

    def test_full_bleed_opt_out_is_explicit_and_still_visible(self):
        """豁免开关：`data-ctv-full-bleed="1"` 放行，但留一条 warn 说明代价。

        没有开关的话，满幅底升 error 就等于把"确实该换底色"的场景逼到删掉正确的
        东西，门禁下一轮必然被绕开——而绕开的成本远低于这里多留一条 warn。
        假值（`0` / `false` / 空 / 乱写）不算豁免：属性在但没给 1，等于没写。
        """
        body = ('<rect width="1080" height="1440" fill="#0b1020"/>'
                '<text x="90" y="1300" font-size="40" font-family="s" fill="#e5e7eb">ok</text>')
        for val, exempt in (("1", True), ("true", True), ("yes", True), ("TRUE", True),
                            ("0", False), ("false", False), ("", False), ("maybe", False)):
            with self.subTest(value=val):
                errs, warns = self.run_check(
                    self._canvas_svg(body, f' {check_svg.FULL_BLEED_ATTR}="{val}"'),
                    layout="canvas", aspect="portrait")
                got = [e for e in errs if "满幅底板" in e]
                self.assertEqual(bool(got), not exempt, val)
                self.assertEqual(bool([w for w in warns if check_svg.FULL_BLEED_ATTR in w]),
                                 exempt, val)


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
    """_director_prepare 读 SVG 根尺寸：门禁端要与 check_svg 同一口径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "a.svg")

    def size(self, attrs):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" ' + attrs + "></svg>")
        return _DP._svg_intrinsic_size(self.path)

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


# 抓**函数对象**而不是模块属性：Linux 上 `posixpath is os.path`、Windows 上
# `ntpath is os.path`——平台总有一半的 path 模块就是被 `_as_windows` 打补丁的那个
# 模块。所以模拟函数里写 `ntpath.relpath(...)`（原实现）在真 Windows 上就是自己调
# 自己（CI 三个用例 RecursionError），写 `posixpath.relpath(...)` 在 Linux 上同样
# 会撞上补丁。抓住函数对象本身，两套 path 模块的属性替换都碰不到它。
_POSIX_RELPATH = __import__("posixpath").relpath


def _windows_relpath(path, start=None):
    """模拟 Windows 的 relpath 输出：相对路径 + 反斜杠分隔符。"""
    rel = _POSIX_RELPATH(str(path).replace("\\", "/"),
                         str(start or ".").replace("\\", "/"))
    return rel.replace("/", "\\")


class StaleScanPathShape(unittest.TestCase):
    """门禁自己的路径形状不能随平台变。

    这不是洁癖：ALLOW 的键是手写的仓库内路径（"references/xxx.md"），拿
    relpath 的结果直接去查表。Windows 上 relpath 给反斜杠，于是每一条例外都
    查不中——"讲历史"的措辞被当成陈旧指路，门禁在 Windows 上恒红
    （CI 的 3.9/3.12/3.14 全挂在 test_skill_docs_have_no_dangling_section_refs）。
    Linux 全绿，所以这个洞在本地看不见，只能靠模拟 Windows 抓住。

    模拟的**不是** `_rel` 的返回值（那等于把修复前的行为又塞回去，测的就不是
    修复了），而是 Windows 环境本身：relpath 吐反斜杠、分隔符是 `\`。然后断言
    `_rel` 仍然归一化成正斜杠。
    """

    def _as_windows(self):
        """把 check_docs 看到的路径环境换成 Windows 的，返回还原器。"""
        real_relpath = check_docs.os.path.relpath
        real_sep = check_docs.os.sep

        check_docs.os.path.relpath = _windows_relpath
        check_docs.os.sep = "\\"

        def restore():
            check_docs.os.path.relpath = real_relpath
            check_docs.os.sep = real_sep

        # 补丁打在**全局** os 上（check_docs.os 就是 os 模块）：用例自己 finally
        # 里也会调一次，这里再兜一层——断言先于 finally 抛错时，反斜杠环境不会
        # 泄漏给后面的用例（那种串扰在 CI 上表现为"只有某个顺序才红"）。
        self.addCleanup(restore)
        return restore

    def test_rel_normalises_windows_separators(self):
        restore = self._as_windows()
        try:
            src = os.path.join(check_docs.ROOT, "references", "tts_pipeline.md")
            self.assertEqual(check_docs._rel(src), "references/tts_pipeline.md")
        finally:
            restore()

    def test_allow_lookup_survives_on_windows_paths(self):
        # 整个 _scan_stale 在 Windows 路径环境下跑：ALLOW 例外仍须命中
        restore = self._as_windows()
        try:
            stale = check_docs._scan_stale(check_docs._sources())
        finally:
            restore()
        offenders = [s for s in stale if s[0].replace("\\", "/")
                     == "references/tts_pipeline.md"]
        self.assertEqual(offenders, [], "ALLOW 例外在 Windows 路径下没生效")

    def test_every_allow_key_is_a_repo_relative_posix_path(self):
        # 键写错的话门禁不会报错，只会静默失效——所以正向断言键能落到真实文件
        for key in check_docs.ALLOW:
            self.assertNotIn("\\", key, f"ALLOW 键不该用反斜杠：{key}")
            self.assertTrue(
                os.path.isfile(os.path.join(check_docs.ROOT, key)),
                f"ALLOW 键指向的文件不存在：{key}")

    def test_windows_relpath_stub_is_not_self_recursive(self):
        """钉住上一条：模拟函数一旦走 `os.path.relpath`（Windows 上 ntpath 就是
        它）就会无限递归，而这个洞在 Linux 上永远看不见。"""
        real = check_docs.os.path.relpath

        def boom(*a, **k):
            raise AssertionError("模拟函数不该再去调 os.path.relpath")

        check_docs.os.path.relpath = boom
        try:
            self.assertEqual(_windows_relpath("/repo/references/x.md", "/repo"),
                             "references\\x.md")
        finally:
            check_docs.os.path.relpath = real

    def test_stale_scan_still_flags_a_planted_word(self):
        # 反向确认：归一化没把门禁改瞎。真造一个含禁用词的临时文件喂给它。
        # 必须建在 ROOT 下：Windows 的 relpath 跨盘符会抛 ValueError（runner 的
        # 临时目录在 C:、仓库在 D:），而门禁扫的本来就是仓库内文件——喂仓库外的
        # 路径不是这一层要处理的事。
        p = os.path.join(check_docs.ROOT, "tests", "_planted_tmp.md")
        self.addCleanup(lambda: os.path.isfile(p) and os.remove(p))
        with open(p, "w", encoding="utf-8") as f:
            f.write("这里的指路已失效：见 gen_hyperframes 生成\n")
        stale = check_docs._scan_stale([p])
        self.assertEqual([s[2] for s in stale], ["gen_hyperframes"])


if __name__ == "__main__":
    unittest.main()
