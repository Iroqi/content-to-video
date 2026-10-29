"""焦点层级的第五维：时间（换档过程）。

动机：前四维（亮度 / 尺寸 / 位置 / 景深）都只是**档位表**——离焦距离 d → 一个静态
值。但"档位何时生效"此前只做了一半：换句窗口内**所有非 past 行共用一枚**从 0.35 起
的全局淡入系数，而变成 past 的行由 verseApply 一次性写死。于是每次换句真正的运动
只有"新当前行浮出"这一个，其余三类的实测（portrait golden，跨 t=4.78 → 4.80）：

  · 刚变成 past 的那行：一帧内 opacity 1 → .45、scale 1 → .8836、filter none →
    blur(1.6px)——**退场是硬的**；
  · 只是往前挪一档的行（d: 2→1）：.45 → **.35** → .452 → .62，中间比两端都暗——
    被全局淡入的起点压下去再涨回来；
  · 离焦距离**根本没变**的行：.45 → **.35** → .45——纯闪烁，它本来该一动不动。

前沿歌词 UI（Apple Music 逐行歌词、AMLL 的 LyricPlayer、Spotify 同步歌词）里所有行
共享同一套 spring 物理：换句是整块歌词各行**同时**柔和挪位——新行上浮放大变亮、旧行
继续上浮缩小褪色、更远的行同步递降，没有一行跳档，也没有一行会先暗下去再亮回来。
d 不是"档位号"，而是**连续场里的一个坐标**。

所以这一轮把档位表升级成连续场：换句窗口内每一行都从「上一句时的档」插值到「这一句
时的档」，与滚动共用同一条曲线、同一个 p、同一个窗口——三个维度同一时刻到位。

两条不能碰的边界（都在本文件钉住）：
  · **入场浮出只叠给当前行**。它读的是"焦点换了地方"这个事件；其余行只是挪一档，
    跟着一起从低处浮起会让整墙字幕每次换句都闪一下（就是上面第三类）。
  · **不得再存任何状态**。dOld 由同段前一句推出，起点仍是 verseTick 逐帧传进来的
    cue 起点（继电器 / relayT 那次事故的教训）。
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
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m),
                             aspect="portrait", theme="dark")


def _relay_src():
    """verseRelay 函数体：这一轮的所有判据都在它身上。"""
    i = RUNTIME.index("const verseRelay = (c, t, t0, si) => {")
    return RUNTIME[i:RUNTIME.index("\n};", i)]


def _relay_from():
    return float(T.load_template()["subtitle"]["relay"]["from"])


class TemplateContract(unittest.TestCase):
    """入场浮出的起点是真源在模板的叶子，不是 runtime 里的字面量。"""

    def test_leaf_exists(self):
        """`subtitle.relay.from` 必须是模板里的现成叶子，--set 才认。"""
        self.assertEqual(_relay_from(), 0.35)

    def test_renderer_injects_the_var(self):
        html = _render()
        self.assertIn(f"--ctv-relay-from:{_relay_from()};", html)

    def test_override_reaches_the_injected_var(self):
        for value in (0.2, 0):
            with self.subTest(value=value), T.override_layer(
                    [f"subtitle.relay.from={value}"]):
                html = _render()
            self.assertIn(f"--ctv-relay-from:{value};", html)

    def test_out_of_range_is_rejected(self):
        """上界卡在 1 之下：写成 1 等于"当前行不浮出"，那就没有换句信号了。"""
        for value in (-0.01, 1, 2):
            with self.subTest(value=value), T.override_layer(
                    [f"subtitle.relay.from={value}"]):
                with self.assertRaisesRegex(ValueError, r"越界"):
                    _render()


class RelaySecondsContract(unittest.TestCase):
    """接力窗口 `--ctv-relay-s`：生成端与运行端读同一个数，0 是合法取值。"""

    def _relay_s(self):
        return float(T.load_template()["layout"]["vertical"]["subtitle"]["relay"])

    def test_renderer_injects_a_number_the_runtime_can_parse(self):
        """写进 CSS 的必须是 parseFloat 读得出的字面量。

        bool 走 float() 会变成 1.0，直接拼进 CSS 却是 `True`——运行期读不出来、
        回落兜底，两端各跑一个数。
        """
        html = _render()
        self.assertRegex(html, r"--ctv-relay-s:[0-9.]+;")
        self.assertNotIn("--ctv-relay-s:True", html)

    def test_zero_reaches_the_var(self):
        """relay=0（不接力）不能被 `||` 兜底吃成默认值。"""
        with T.override_layer(["layout.vertical.subtitle.relay=0"]):
            html = _render()
        self.assertIn("--ctv-relay-s:0;", html)

    def test_out_of_range_is_rejected(self):
        for value in (-0.01, 3.5):
            with self.subTest(value=value), T.override_layer(
                    [f"layout.vertical.subtitle.relay={value}"]):
                with self.assertRaisesRegex(ValueError, r"越界"):
                    _render()

    def test_runtime_uses_is_finite_not_or(self):
        self.assertIn("isFinite(_relaySecRaw)", RUNTIME)
        self.assertNotIn(")) || 0.32;", RUNTIME)


class RankContract(unittest.TestCase):
    """档位必须有**数值**取法，插值才有两端。"""

    def test_numeric_rank_helpers_exist(self):
        """数值版（focusOp/focusScale/focusBlur）与内联版（verseFocus*）并存。"""
        for name in ("const focusOp = (d) =>", "const focusScale = (d) =>",
                     "const focusBlur = (d) =>"):
            self.assertIn(name, RUNTIME)

    def test_zero_distance_is_full(self):
        """d=0（当前行）的数值档位必须是 1，插值才有"走到满亮"这个终点。"""
        self.assertIn("if (d === 0) return 1;", RUNTIME)

    def test_inline_form_still_steps_aside_for_zero(self):
        """内联写法保持 d=0 → ""（撤内联交还 CSS 的 active 档）。"""
        self.assertIn('const verseFocusOp = (d) => (d === 0 ? "" : focusOp(d).toFixed(3));',
                      RUNTIME)
        self.assertIn(
            'const verseFocusScale = (d) => (d === 0 ? "" : focusScale(d).toFixed(4));',
            RUNTIME)


class RuntimeContract(unittest.TestCase):
    """运行时侧：逐行插值、入场只给当前行、不存状态。"""

    def test_every_line_interpolates_including_past(self):
        """退场不再是硬跳：past 行**一起**走插值（旧代码 `!past` 把它们排除）。"""
        src = _relay_src()
        self.assertNotIn("!past", src)
        self.assertNotIn("past", src.split("const _RELAY_VARS")[0].split("const verseRelay")[1]
                         if "const _RELAY_VARS" in src else src)

    def test_interpolation_runs_from_the_previous_cue(self):
        """每一行都从「上一句时的档」走到「这一句时的档」——换档是过程。"""
        src = _relay_src()
        self.assertIn("const dNew = di - si;", src)
        self.assertIn("const dOld = prev ? di - _idx(prev) : dNew;", src)

    def test_previous_line_is_derived_not_stored(self):
        """上一句由布局与 si 推出，不新增缓存字段。"""
        self.assertIn("const versePrev = (c, si) => {", RUNTIME)

    def test_relay_shares_the_scroll_curve(self):
        """与滚动共用同一条曲线：三个维度同一时刻到位，焦点不能在途中分裂。"""
        src = _relay_src()
        self.assertIn("SCROLL_EASE(", src)

    def test_entrance_is_only_layered_on_the_current_line(self):
        """入场浮出只叠给当前行——其余行只是挪一档，不该跟着一起浮起。"""
        src = _relay_src()
        self.assertIn("const float = di === si && p < 1;", src)
        self.assertNotIn("const on = mine && (p < 1) && !past;", RUNTIME)

    def test_window_end_settles_on_the_steady_rank(self):
        """p ≥ 1 时 e 归一，插值结果就是稳态档位（与 verseApply 写死的同一个数）。"""
        src = _relay_src()
        self.assertIn("const e = p >= 1 ? 1 : SCROLL_EASE(p);", src)

    def test_other_verses_clear_their_inline_ranks(self):
        """被盖住的页整表不参与：撤内联，交还 CSS 的 active/past/base 档。"""
        src = _relay_src()
        self.assertIn("if (!mine) { verseInlineClear(L); continue; }", src)
        self.assertIn("const verseInlineClear = (L) => {", RUNTIME)

    def test_no_new_cache_field(self):
        """不新增缓存字段：缓存可以清，状态必须能从 t 重推。"""
        for name in (".rankT", ".lastRank", ".relayRank", ".prevSi", ".relayT"):
            self.assertNotIn(name, RUNTIME)

    def test_writes_are_gated_on_change(self):
        """每帧都写这几位，稳态时值不变就不写，别白触发样式重算。"""
        self.assertIn("const verseVarSet = (L, key, s) => {", RUNTIME)
        self.assertIn("getPropertyValue(key) !== s", RUNTIME)


if __name__ == "__main__":
    unittest.main()
