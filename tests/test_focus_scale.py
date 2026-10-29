"""焦点层级的第二个维度：尺寸（离焦距离同时决定亮度与缩放）。

动机：焦点衰减只做了一维——"远"只被表达
成"暗"，同一档里第 2 行和第 4 行一样远。前沿歌词 UI（Apple Music 逐行、Apple
Music Like Lyrics 的 spring）让层级同时落在透明度与缩放上。本轮补上尺寸那一维：
同一条几何曲线、另一个 ratio，走 transform 而不是 font-size（改字号会重排：折行
点变、offsetTop 变、覆盖层折到别处＝重影）。

这轮还顺手堵了一个覆盖层的坑：`--ctv-ved-dx` 用 getBoundingClientRect 量，而
覆盖层首次上屏几乎总在换句那一帧、正是 scale 最小的时候——不除回 scale，对话段
首句会少算 6%，覆盖层整体左移 4.1px。
"""
import pathlib
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as T

REPO = pathlib.Path(H.SKILL_DIR)
RUNTIME = (REPO / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
CSS = (REPO / "scripts" / "_assets" / "composition.css").read_text(encoding="utf-8")


def _render():
    import contextlib
    import io
    from html_renderer import generate_html
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m), aspect="portrait", theme="dark")


def _focus():
    return T.load_template()["subtitle"]["focus"]


class FocusScaleTemplate(unittest.TestCase):
    """尺寸那一档的真源在模板 subtitle.focus.scale。"""

    def test_scale_is_shallower_than_brightness(self):
        """尺寸衰减必须比亮度浅：字号差 6% 已经读得出来，再深就是"退场"变成"毁字"。

        亮度 ratio 0.726 意味着每远一档掉 27%；尺寸每远一档只掉 6%。
        """
        f = _focus()
        self.assertGreater(float(f["scale"]), float(f["ratio"]),
                           "尺寸那一档必须比亮度那一档浅（更接近 1）")

    def test_scale_is_in_range(self):
        s = float(_focus()["scale"])
        self.assertGreater(s, 0.0)
        self.assertLess(s, 1.0)

    def test_deepest_step_stays_readable(self):
        """最深的那一档（过去侧 d≤-2 = scale³）乘最小字号后仍要守住可读下限。

        这是"尺寸只封两档、亮度封三档"的判据：再多封一档就破线了。
        """
        tpl = T.load_template()
        s = float(_focus()["scale"])
        for aspect in ("vertical", "landscape"):
            fs = float(tpl["layout"][aspect]["subtitle"]["fontSize"])
            with self.subTest(aspect=aspect):
                self.assertGreaterEqual(fs * (s ** 3), float(T.MIN_READABLE_FONT_PX),
                                        f"{aspect} 字幕在最深档缩破可读下限")

    def test_one_more_step_would_break_it(self):
        """上一条的反证：封三档就会破线——所以它封在两档是算出来的，不是随手写的。"""
        tpl = T.load_template()
        s = float(_focus()["scale"])
        smallest = min(float(tpl["layout"][a]["subtitle"]["fontSize"])
                       for a in ("vertical", "landscape"))
        self.assertLess(smallest * (s ** 4), float(T.MIN_READABLE_FONT_PX))

    def test_renderer_injects_scale_var(self):
        html = _render()
        self.assertIn(f"--ctv-focus-scale:{_focus()['scale']}", html)

    def test_override_reaches_the_injected_var(self):
        for value in (0.95, 1):
            with self.subTest(value=value), T.override_layer(
                    [f"subtitle.focus.scale={value}"]):
                html = _render()
            self.assertIn(f"--ctv-focus-scale:{value};", html)

    def test_invalid_focus_overrides_fail_before_html(self):
        bad = (
            "subtitle.focus.future=0",
            "subtitle.focus.past=0.9",
            "subtitle.focus.ratio=1",
            "subtitle.focus.scale=0.9",
        )
        for assignment in bad:
            with self.subTest(assignment=assignment), T.override_layer([assignment]):
                with self.assertRaisesRegex(ValueError, r"subtitle|可读下限"):
                    _render()

    def test_unused_cap_is_not_exposed_as_an_override(self):
        with self.assertRaises(ValueError):
            T.put_override("subtitle.focus.cap=3")


class CssContract(unittest.TestCase):
    """CSS 侧：合成规则与缩放原点。"""

    def test_same_min_rule_as_brightness(self):
        """两个维度共用同一条合成规则：焦点档与接力值取小。"""
        self.assertIn("scale(min(var(--ctv-line-scale,1),var(--ctv-relay-scale,1)))", CSS)

    def test_origin_is_left_edge(self):
        """左对齐的字幕必须绕左缘缩放，绕中心会让行的左边界在换句时左右抖。"""
        self.assertIn("transform-origin:left center", CSS)

    def test_no_translate_on_the_line(self):
        """位移归 .verse-clip 独占；行上只允许 scale。"""
        line = next(l for l in CSS.splitlines() if l.startswith(".verse-line{"))
        self.assertIn("transform:scale(", line)
        self.assertNotIn("translate", line)


class RuntimeContract(unittest.TestCase):
    """运行时侧：尺寸查表、接力分量、覆盖层补偿。"""

    def test_table_reads_injected_var(self):
        self.assertIn('cs.getPropertyValue("--ctv-focus-scale")', RUNTIME)

    def test_fallback_matches_template(self):
        """老产物没有变量时回退的默认值，必须与模板同一个数。"""
        self.assertIn(f"sRaw : {_focus()['scale']}", RUNTIME)

    def test_scale_is_capped_at_two_steps(self):
        """尺寸封两档（亮度封三档）——两个维度各自的封顶不同，是有意的。"""
        self.assertRegex(RUNTIME, r"Math\.min\(Math\.abs\(d\), 2\)")

    def test_scale_written_and_removed_inline(self):
        self.assertIn('setProperty("--ctv-line-scale"', RUNTIME)
        self.assertIn('removeProperty("--ctv-line-scale")', RUNTIME)

    def test_relay_carries_a_scale_component(self):
        """接力期尺寸与亮度共用同一个 p：两个维度必须同一时刻到位。"""
        self.assertIn('setProperty("--ctv-relay-scale"', RUNTIME)
        self.assertIn('verseVarClear(L, "--ctv-relay-scale")', RUNTIME)

    def test_relay_scale_starts_from_the_future_step(self):
        """尺寸接力的起点是未来档（focusScale(1)），不是亮度那个 RELAY_FROM。

        照抄那个浮出起点会把新句先缩成一小团再撑开，读成形变而不是入场。
        """
        self.assertIn("const s0 = focusScale(1);", RUNTIME)

    def test_ved_origin_undoes_the_scale(self):
        """--ctv-ved-dx 要的是布局值，必须除回当前 scale（否则对话段首句重影）。

        除数是"变换后宽 ÷ 布局宽"——纯派生量，不新增状态。
        """
        self.assertIn("rEl.width / w", RUNTIME)
        self.assertIn("dx / s", RUNTIME)

    def test_emphasis_width_starts_at_the_first_glyph(self):
        """字宽进度必须减正文起点，不能把页面左边距算进句子总宽。"""
        self.assertIn("if (k === 1) xs.push(box.left)", RUNTIME)
        self.assertNotIn("const xs = [0]", RUNTIME)


if __name__ == "__main__":
    unittest.main()
