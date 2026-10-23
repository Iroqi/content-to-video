"""焦点层级的第四个维度：景深（模糊）。

动机：焦点衰减此前做了三维——亮度（未来 0.62 / 已念 0.45）、尺寸（0.94）、位置
（逐帧插值的滚动）。前沿歌词 UI（AMLL 的 setEnableBlur、Jellyfin 那支"梯度模糊"）
让离焦的行**更虚**：前三维合起来表达的是强弱，只有模糊表达的是"不在焦平面上"。

两条与前面几维不同的设计，这里都钉住：
  · 模糊是**加法**（每远一档加 blur em），不是亮度/尺寸那条乘法曲线——高斯半径
    随离焦距离线性增长，照抄 ratio 会得到"越远越不虚"。
  · 滤镜挂在**运行时合成的整条值**上（--ctv-line-filter），不在 CSS 里做 max()
    合成——blur(0em) 也是 filter，实测它把字形换成另一套栅格化（同一句 40px 白字
    边缘拉普拉斯和 925410 → 994512）；当前行是画面上最该锐利的那一句，不能为一个
    0 半径的滤镜把它的字形换一遍。
"""
import pathlib
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as T

REPO = pathlib.Path(H.SKILL_DIR)
RUNTIME = (REPO / "templates" / "runtime.js").read_text(encoding="utf-8")
CSS = (REPO / "templates" / "composition.css").read_text(encoding="utf-8")


def _render():
    import contextlib
    import io
    from html_renderer import generate_html
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m), aspect="portrait", theme="dark")


def _focus():
    return T.load_template()["subtitle"]["focus"]


class FocusBlurTemplate(unittest.TestCase):
    """景深那一档的真源在模板 subtitle.focus.blur。"""

    def test_blur_is_small_against_the_font(self):
        """每档模糊量必须远小于字号：0.05em（糊掉线）≈ 笔画宽度的一半。

        CJK 正文（字重 600）笔画宽度约字号的 1/14 ≈ 0.07em，半径逼近它相邻笔画
        就并成一片。
        """
        b = float(_focus()["blur"])
        self.assertGreater(b, 0.0)
        self.assertLess(b, float(T.FOCUS_BLUR_MAX_EM))

    def test_deepest_step_stays_under_the_blur_ceiling(self):
        """最深的那一档（封顶 = 2 档）不得越过糊掉线——"远"不能变成"毁了"。"""
        self.assertLessEqual(2 * float(_focus()["blur"]), float(T.FOCUS_BLUR_MAX_EM))

    def test_one_more_step_would_break_it(self):
        """上一条的反证：封三档就越线——所以它封在两档是算出来的，不是随手写的。"""
        self.assertGreater(3 * float(_focus()["blur"]), float(T.FOCUS_BLUR_MAX_EM))

    def test_renderer_injects_blur_var(self):
        html = _render()
        self.assertIn(f"--ctv-focus-blur:{_focus()['blur']}", html)

    def test_override_reaches_the_injected_var(self):
        for value in (0.015, 0):
            with self.subTest(value=value), T.override_layer(
                    [f"subtitle.focus.blur={value}"]):
                html = _render()
            self.assertIn(f"--ctv-focus-blur:{value};", html)

    def test_blur_over_the_ceiling_is_rejected(self):
        """封顶档会越过糊掉线的值在生成期就拒，不等渲染出来才发现糊了。"""
        for value in (0.03, 0.1):
            with self.subTest(value=value), T.override_layer(
                    [f"subtitle.focus.blur={value}"]):
                with self.assertRaisesRegex(ValueError, r"糊掉线"):
                    _render()

    def test_negative_blur_is_rejected(self):
        with T.override_layer(["subtitle.focus.blur=-0.01"]):
            with self.assertRaisesRegex(ValueError, r"越界"):
                _render()

    def test_zero_blur_is_the_only_off_switch(self):
        """0 = 关掉景深（回到三维），是合法写法——其余非正值都拦。"""
        self.assertIn('("blur", _focus_blur, 0 <= _focus_blur)',
                      (REPO / "scripts" / "html_renderer.py").read_text(encoding="utf-8"))


class CssContract(unittest.TestCase):
    """CSS 侧：滤镜挂在运行时合成的整条值上，合成不在这里做。"""

    def test_filter_hangs_on_the_composed_value(self):
        """var 缺省即 none：不需要模糊的行一个 filter 都不该有。"""
        self.assertIn("filter:var(--ctv-line-filter,none)", CSS)

    def test_composition_is_not_done_in_css(self):
        """CSS 里的 max() 合成会把 blur(0em) 也变成 filter——实测它换字形栅格化。

        同一句 40px 白字：blur(0em) 与 none 的边缘拉普拉斯和 925410 → 994512、
        单像素最大差 80；对照 transform:scale(1) 与 none 逐像素相同。所以"不需要
        模糊"必须是 filter:none，合成挪到 runtime.js 的 verseBlurSet。
        """
        line = next(l for l in CSS.splitlines() if l.startswith(".verse-line{"))
        self.assertNotIn("blur(max(", line)
        self.assertNotIn("--ctv-line-blur", line)
        self.assertNotIn("--ctv-relay-blur", line)

    def test_no_layout_affecting_property_was_used(self):
        """模糊不许动布局盒：折行点变 = 滚动锚点漂移 + 覆盖层重影。"""
        line = next(l for l in CSS.splitlines() if l.startswith(".verse-line{"))
        self.assertIn("filter:", line)
        self.assertNotIn("font-size:var(--ctv-line", line)


class RuntimeContract(unittest.TestCase):
    """运行时侧：查表、合成、地板。"""

    def test_table_reads_injected_var(self):
        self.assertIn('cs.getPropertyValue("--ctv-focus-blur")', RUNTIME)

    def test_fallback_matches_template(self):
        """老产物没有变量时回退的默认值，必须与模板同一个数。"""
        self.assertIn(f"bRaw : {_focus()['blur']}", RUNTIME)

    def test_it_is_an_additive_curve(self):
        """每远一档**加**一档，不是乘 ratio——乘法会得到"越远越不虚"。"""
        self.assertRegex(RUNTIME, r"return b \* Math\.min\(Math\.abs\(d\) \+ \(d < 0 \? 1 : 0\), FOCUS_BLUR_RANK_CAP\)")
        self.assertNotIn("Math.pow(T.blur", RUNTIME)

    def test_cap_is_two_steps(self):
        """封顶档数钉死在 2：生成期那条糊掉线（FOCUS_BLUR_MAX_EM ÷ blur）推出来的。"""
        self.assertIn("const FOCUS_BLUR_RANK_CAP = 2;", RUNTIME)

    def test_filter_is_written_and_removed_inline(self):
        self.assertIn('setProperty("--ctv-line-filter"', RUNTIME)
        self.assertIn('removeProperty("--ctv-line-filter")', RUNTIME)

    def test_zero_blur_means_no_filter_at_all(self):
        """不需要模糊必须是 filter:none——blur(0em) 也是 filter，会换字形栅格化。"""
        self.assertRegex(RUNTIME, r"if \(!\(b > FOCUS_BLUR_MIN_EM\)\)")

    def test_floor_hides_the_filter_toggle(self):
        """地板 0.01em：接力的末段撤滤镜，让栅格化切换被残存的模糊盖住。"""
        self.assertIn("const FOCUS_BLUR_MIN_EM = 0.01;", RUNTIME)

    def test_writes_are_gated_on_change(self):
        """值不变就不写：verseRelay 每帧都算这一位，稳态不该每帧触发样式重算。"""
        self.assertIn('getPropertyValue("--ctv-line-filter") !== v', RUNTIME)

    def test_apply_and_relay_both_go_through_one_composer(self):
        """焦点档（换句才变）与接力档（每帧）取大者，合成只有 verseBlurSet 一处。"""
        self.assertIn("verseBlurSet(L, act ? focusBlur(di - si) : 0)", RUNTIME)
        self.assertRegex(
            RUNTIME,
            r"verseBlurSet\(L, Math\.max\(at\(focusBlur\), float \? b0 \* \(1 - e\) : 0\)\)")

    def test_relay_starts_from_the_future_step(self):
        """接力从未来档的模糊量收到 0：与亮度/尺寸共用同一个 p，同一时刻到位。"""
        self.assertIn("const b0 = focusTable().blur;", RUNTIME)
        self.assertIn("float ? b0 * (1 - e) : 0", RUNTIME)

    def test_no_new_cache_field(self):
        """不新增缓存字段：模糊量由 (d, 模板) 重推，缓存被字体就绪清掉后照样算得出。"""
        for name in (".blurT", ".lastBlur", ".relayBlur"):
            self.assertNotIn(name, RUNTIME)


if __name__ == "__main__":
    unittest.main()
