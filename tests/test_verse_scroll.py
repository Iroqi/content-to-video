"""焦点层级的第三个维度：位置（滚动）。

动机：前沿歌词 UI（Apple Music 逐行歌词、AMLL 的 LyricPlayer）的 spring 物理
同时管**垂直位置、缩放、透明度**三个属性。本仓库此前只做了两维（亮度、尺寸），
滚动那一维是硬跳：换句那一帧 `.verse-clip` 的 translateY 直接写死到新落位
（实测 0 → −70px 一帧到位），而同一次换句的亮度与尺寸都在接力窗口里柔和
到位——三个维度里只有位置这一维是硬的。

这一轮把滚动补上，落在 `.verse-clip` 的 translateY 上，与亮度、尺寸共用同
一个接力窗口（`--ctv-relay-s`）与同一个 p（起点 = 该句 cue 起点）。

三条纪律（与 verseRelay 同源，逐帧渲染逼出来的）：
  1. t 的纯函数：进度只由 (t − 本句 cue 起点) 算，不记"上一帧滚到哪"。
  2. 不新增任何缓存字段：起点与终点都由静态布局值（offsetTop）重推。
  3. 位移仍归 .verse-clip 独占：行上只允许 scale。
"""
import pathlib
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as _T

REPO = pathlib.Path(H.SKILL_DIR)
RUNTIME = (REPO / "templates" / "runtime.js").read_text(encoding="utf-8")
CSS = (REPO / "templates" / "composition.css").read_text(encoding="utf-8")
PREVIEW = (REPO / "templates" / "preview.js").read_text(encoding="utf-8")


def _render():
    import contextlib
    import io
    from html_renderer import generate_html
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m),
                             aspect="portrait", theme="dark")


class RuntimeContract(unittest.TestCase):
    """运行时侧：滚动插值的三条纪律。"""

    def test_scroll_is_written_every_frame(self):
        """滚动必须每帧写：只在换句那帧写一次＝硬跳。"""
        self.assertRegex(RUNTIME, r"verseScroll\(c, t, curT0, curSi\)")
        self.assertRegex(RUNTIME, r"verseScroll\(c, 1e9, tailT0, cueSi\)")

    def test_gap_branch_also_scrolls(self):
        """片头/片尾的 gap 分支也要收尾滚动，否则最后一帧会停在半路。"""
        self.assertIn("verseScroll(c, 1e9, tailT0, cueSi)", RUNTIME)

    def test_progress_is_a_function_of_t(self):
        """进度只由 (t − 本句 cue 起点) 算：乱序 seek 必须幂等。"""
        self.assertRegex(RUNTIME, r"const p = Math\.max\(0, Math\.min\(1, \(t - t0\) / RELAY_SECONDS\)\)")
        # 不许有"上一帧滚到哪"的缓存字段
        for name in (".scrollT", ".scrollFrom", ".scrollY", ".lastScroll"):
            self.assertNotIn(name, RUNTIME)

    def test_relay_window_is_shared(self):
        """滚动与亮度、尺寸共用同一个接力窗口——三个维度必须同一时刻到位。"""
        self.assertIn("const RELAY_SECONDS = parseFloat(", RUNTIME)
        self.assertIn('/ RELAY_SECONDS)', RUNTIME)

    def test_landing_is_derived_from_layout(self):
        """落位由静态布局值算出：offsetTop → translateY，缓存被清也能算。"""
        self.assertIn("__CTV_VERSE_CLIP__ - L.top", RUNTIME)
        self.assertIn("c.clipH <= c.winH ? 0", RUNTIME)

    def test_no_write_once_transform_in_verse_apply(self):
        """verseApply（换句才跑）里不许再写一次性落位——那正是硬跳的来源。"""
        start = RUNTIME.index("const verseApply = (si) => {")
        end = RUNTIME.index("\n};", start)
        body = RUNTIME[start:end]
        self.assertNotIn('c.clip.style.transform', body)
        self.assertIn("verseScroll", RUNTIME)

    def test_curve_comes_from_the_pinned_gsap(self):
        """曲线取钉固 GSAP 自己的档，不另抄公式：抄了就得靠测试盯两份相等，
        而 GSAP 若哪天不认这个档名会**静默**回落成缺省缓动。"""
        self.assertIn("gsap.parseEase(SCROLL_EASE_NAME)", RUNTIME)
        self.assertIn("typeof SCROLL_EASE !== \"function\"", RUNTIME)

    def test_overshoot_eases_are_not_used(self):
        """过冲档一律不用：文字滚动过冲会晕，落位本就该由 offsetTop 说了算。"""
        for bad in ('parseEase("back', 'parseEase("elastic', 'parseEase("bounce'):
            self.assertNotIn(bad, RUNTIME)


class CurveSingleSource(unittest.TestCase):
    """曲线档名只有一个真源：模板 subtitle.scroll.ease。

    抄一份的代价不是"多一行字"，而是**两边可以不同步**且没有任何报错——改了一处、
    成片里演的是另一条曲线，正是"演的不是你写的"那条无声退化。
    """

    def _ease(self):
        return str(_T.load_template()["subtitle"]["scroll"]["ease"]).strip()

    def test_template_carries_the_curve(self):
        self.assertTrue(self._ease())

    def test_runtime_fallback_matches_the_template(self):
        """运行时兜底值必须与模板同值：两边不同步＝改了一处、成片演的是另一条。"""
        self.assertIn(f'|| "{self._ease()}"', RUNTIME)

    def test_runtime_does_not_hardcode_a_curve(self):
        """档名不许写死在 runtime.js 里：真源在模板，运行时只读注入值。"""
        self.assertIn('getPropertyValue("--ctv-scroll-ease")', RUNTIME)

    def test_renderer_injects_the_curve(self):
        html = _render()
        self.assertIn(f"--ctv-scroll-ease:{self._ease()};", html)

    def test_override_reaches_the_injected_var(self):
        with _T.override_layer(['subtitle.scroll.ease="power2.out"']):
            html = _render()
        self.assertIn("--ctv-scroll-ease:power2.out;", html)

    def test_unknown_family_fails_before_html(self):
        """认不出来的档名必须 fail-fast：GSAP 会**静默**回落缺省缓动。"""
        with _T.override_layer(['subtitle.scroll.ease="rough"']):
            with self.assertRaises(ValueError):
                _render()

    def test_overshoot_family_is_rejected(self):
        """过冲族对滚动是穿帮（落位是精确值，冲过再回来＝甩出去再收回）。"""
        with _T.override_layer(['subtitle.scroll.ease="back.out(1.7)"']):
            with self.assertRaises(ValueError):
                _render()


class CssContract(unittest.TestCase):
    """CSS 侧：位移仍归 .verse-clip 独占，且不写 transition。"""

    def test_translate_stays_on_the_clip(self):
        line = next(l for l in CSS.splitlines() if l.startswith(".verse-line{"))
        self.assertNotIn("translate", line)

    def test_clip_has_no_transition(self):
        """成片是逐帧 seek 出来的，CSS 过渡按墙上时钟走、不受 GSAP 驱动。"""
        for line in CSS.splitlines():
            if line.startswith(".verse-clip"):
                self.assertNotIn("transition", line)


class PreviewContract(unittest.TestCase):
    """预览与成片同源：滚动由 runtime 插值，preview 不再补 transform 过渡。"""

    def test_preview_does_not_transition_the_clip(self):
        self.assertNotIn(".verse-clip{transition:transform", PREVIEW)
        self.assertNotIn(".verse-clip { transition: transform", PREVIEW)

    def test_preview_still_softens_line_opacity(self):
        """行上的淡入淡出过渡与滚动不冲突，保留。"""
        self.assertIn(".verse-line{transition:opacity", PREVIEW)


if __name__ == "__main__":
    unittest.main()
