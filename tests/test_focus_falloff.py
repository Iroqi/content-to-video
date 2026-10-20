"""句子流的焦点衰减（focus falloff）与接力状态的"可重推"契约。

动机（.brainstorm/2026-10-24-focus-falloff.md）：三档阶跃（1.0 / 0.45 / 0.62）
在同档内一视同仁，0.62 的未念墙里没有渐变可循。前沿歌词 UI（Apple Music 逐行、
Spotify 同步）都按离焦距离衰减。曲线不是新发明：0.62 × 0.726 ≈ 0.45，"已念"
本来就是"未念"沿同一条曲线多走的一步——本轮把它延长并封在第三档。

这轮还修了一个真 bug：换句接力的 relayT / relaySet 缓存在 verse 缓存对象上，
document.fonts.ready 清缓存重建时把它们一起带走了，行上已写出的内联
--ctv-relay 成了孤儿——开场句整句卡在 0.35（比未念档还暗）。修法是把接力做成
完全可从 t 重推的纯函数，这份文件锁住"不再有那类状态"的口径。
"""
import pathlib
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as T

REPO = pathlib.Path(H.SKILL_DIR)
RUNTIME = (REPO / "templates" / "runtime.js").read_text(encoding="utf-8")


def _render():
    import contextlib
    import io
    from html_renderer import generate_html
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m), aspect="portrait", theme="dark")


class FocusTemplate(unittest.TestCase):
    """三个数的真源在模板 subtitle.focus；文档与运行时都抄它。"""

    def test_curve_is_one_curve(self):
        """0.45 必须≈ 0.62 × ratio：两侧拼接处无缝是设计，不是巧合。"""
        f = T.load_template()["subtitle"]["focus"]
        self.assertAlmostEqual(f["future"] * f["ratio"], f["past"], delta=0.005)

    def test_values_are_in_range(self):
        f = T.load_template()["subtitle"]["focus"]
        for key in ("future", "past", "ratio"):
            self.assertGreater(f[key], 0.0, key)
            self.assertLess(f[key], 1.0, key)
        self.assertGreaterEqual(int(f["cap"]), 2)

    def test_past_is_dimmer_than_future(self):
        """念过的比没念的退得更快：同距下过去侧永远更暗。"""
        f = T.load_template()["subtitle"]["focus"]
        self.assertLess(f["past"], f["future"])

    def test_renderer_injects_focus_vars(self):
        html = _render()
        f = T.load_template()["subtitle"]["focus"]
        self.assertIn(f"--ctv-focus-future:{f['future']}", html)
        self.assertIn(f"--ctv-focus-past:{f['past']}", html)
        self.assertIn(f"--ctv-focus-ratio:{f['ratio']}", html)

    def test_override_reaches_the_injected_vars(self):
        """读得到就改得到：--set 改 focus，注入值跟着走。"""
        with T.override_layer(["subtitle.focus.past=0.5"]):
            html = _render()
        self.assertIn("--ctv-focus-past:0.5;", html)


class RuntimeContract(unittest.TestCase):
    """运行时侧：衰减查表 + 接力状态可重推。"""

    def test_focus_table_reads_injected_vars(self):
        for var in ("--ctv-focus-future", "--ctv-focus-past", "--ctv-focus-ratio"):
            self.assertIn(f'cs.getPropertyValue("{var}")', RUNTIME)

    def test_focus_fallback_matches_template(self):
        """老产物没有变量时回退的默认值，必须与模板同一组数（两份抄写同口径）。"""
        f = T.load_template()["subtitle"]["focus"]
        self.assertIn(f"fRaw : {f['future']}", RUNTIME)
        self.assertIn(f"pRaw : {f['past']}", RUNTIME)

    def test_focus_writes_inline_var_in_verse_apply(self):
        self.assertIn('setProperty("--ctv-line-op"', RUNTIME)
        self.assertIn('removeProperty("--ctv-line-op")', RUNTIME)

    def test_cap_is_bounded(self):
        """|d| 封顶查表（表长 4 = 1 + 三档衰减），不随句数增长。"""
        self.assertRegex(RUNTIME, r"Math\.min\(Math\.abs\(d\), 3\)")

    def test_relay_keeps_no_state(self):
        """接力不得在缓存对象上存任何状态：relayT / relaySet 都是这次事故的名字。"""
        self.assertNotIn(".relayT", RUNTIME)  # 注释里的教训提名字可以，代码里不许再有
        self.assertNotIn("relaySet", RUNTIME)
        # 内联值的归属看行本身，不另记布尔
        self.assertIn('getPropertyValue("--ctv-relay")', RUNTIME)

    def test_relay_start_comes_from_the_cue(self):
        """接力起点 = 当前 cue 的 t，由 verseTick 逐帧传入，不从缓存读。"""
        self.assertRegex(RUNTIME, r"verseRelay\(c, t, curT0, curSi\)")
        self.assertRegex(RUNTIME, r"verseRelay\(c, 1e9, tailT0, cueSi\)")

    def test_relay_is_gated_to_the_active_verse(self):
        """删掉 relayT 之后，"只扫活跃句所在 verse"改成显式判断，不许丢。"""
        self.assertIn(".some((L) => parseInt(L.el.getAttribute(\"data-i\"), 10) === si)", RUNTIME)

    def test_ved_clear_has_no_early_return(self):
        """verseEmClear 不得因 lastK === -1 早退：缓存重建后行上可能还挂着
        上一份缓存时代的类，早退会让它们永远没人摘。"""
        self.assertNotIn("if (L.lastK === -1) return;", RUNTIME)


if __name__ == "__main__":
    unittest.main()
