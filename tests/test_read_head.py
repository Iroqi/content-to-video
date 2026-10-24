"""卡拉OK 读取头：**亚字精度**。

动机：句内进度 p 本身是连续量，但此前只用它的整数部分——k 取到整字之后，一个字要等
这一格走完才整字数。portrait golden 第 2 句（16 字、宽约 640px、2.4s 念完）30fps 逐帧
实测，改动前的三处塌陷：

  · 边缘位置：0.0 / 0.0 / 0.0 / 0.0 / **39.4** / 39.6 / 39.9 / 40.0 / **80.0** ——
    每四到六帧 Δedge = 0，第五帧把一个整字的宽度一次性走完；
  · 正在念的那个字：alpha **恒为 1.000**，从来没有处在中间的值；
  · 理论应有的位移是全句宽 ÷ 时长 ÷ 帧率 = 8.9px/帧，实测却是 0 与 40 交替。

前沿歌词 UI（AMLL 的逐字模式、Apple Music 的词级同步）的高亮边缘都是连续的：前者按词级
时间戳、后者按词的发声区间推进，**两者都没有"取整到整字"这一步**。本仓库的 TTS 只有句级
时间戳，"句内匀速"确实是唯一可用的假设——但匀速推不出取整，那层 floor 是自己加上去的。

改动后同一组帧的实测：Δedge 逐帧 8.4 / 8.6 / 8.8 / 8.9 / 9.0 / 9.1 / 9.1 / 9.1 / 9.0 /
8.9（窄字略快、宽字略慢，正是"按实测字宽分配行程"该有的样子），正在念的那个字 alpha 按
0.778 / 0.555 / 0.333 / 0.111 递降。

四条不能碰的边界（都在本文件钉住）：
  · **整数部分仍只在跨格那一帧全量刷**：O(n) 的覆盖层重写不能每帧发生。
  · **亚字那一位不进那道门**：它就是每帧都变的那一位，关进去就退回按字跳格。
  · **不许再存状态**：f 每帧由 (t, 实测字宽) 重推，连一个缓存字段都不许加。
  · **逐字进度不许挂 transition**：过渡会让高亮滞后于逐帧 seek 的时间轴
    （唯一例外是句尾收束那条 settled 规则）。
"""
import pathlib
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path

REPO = pathlib.Path(H.SKILL_DIR)
RUNTIME = (REPO / "templates" / "runtime.js").read_text(encoding="utf-8")
CSS = (REPO / "templates" / "composition.css").read_text(encoding="utf-8")


def _fn(name):
    """取出某个顶层函数的源码体（`const <name> = ...` 到下一个 `\\n};`）。"""
    marker = "const %s = " % name
    i = RUNTIME.index(marker)
    return RUNTIME[i:RUNTIME.index("\n};", i)]


def _update_body():
    return RUNTIME.split("const verseEmUpdate = ", 1)[1].split("\n};", 1)[0]


def _render():
    import contextlib
    import io
    from html_renderer import generate_html
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m),
                             aspect="portrait", theme="dark")


class SingleTruth(unittest.TestCase):
    """取格这件事只能有一个实现：整数 k 与零头 f 必须同一次算出来。"""

    def test_no_second_mapper(self):
        """不许再有第二个 `verseEmAt`：两处各自走一遍 xs，早晚走出两个答案。"""
        self.assertNotIn("verseEmAt", RUNTIME)

    def test_head_is_the_only_mapper(self):
        body = _update_body()
        self.assertEqual(len(re.findall(r"verseHeadAt\(", body)), 1,
                         "每帧只准推一次 x → (k, f)")
        self.assertEqual(len(re.findall(r"while \(k < c\.n", RUNTIME)), 1,
                         "xs 上的扫描只能有一份")

    def test_both_parts_are_used(self):
        """整数走跨格判据、零头走亚字写入，缺一个就退回改动前。"""
        body = _update_body()
        self.assertIn("const h = verseHeadAt(el, p);", body)
        self.assertIn("const k = h.k;", body)
        self.assertIn("verseEdgeSet(L, k, h.f)", body)


class FracContract(unittest.TestCase):
    """零头的三条退化：夹取、无量可插、零宽格。"""

    def test_clamped_to_unit(self):
        self.assertRegex(_fn("verseHeadAt"),
                         r"Math\.max\(0, Math\.min\(1, \(x - c\.xs\[k\]\) / w\)\)")

    def test_denominator_is_measured_width_not_char_count(self):
        """分母必须是**实测字宽**：中英混排、标点、折行处的格宽本来就不相等。"""
        src = _fn("verseHeadAt")
        self.assertIn("const w = c.xs[k + 1] - c.xs[k];", src)
        self.assertIn("/ w)", src)

    def test_zero_width_glyph_is_not_interpolated(self):
        """折行处那一格被累计最大值压成零宽：拿它当分母会得到 NaN 的 alpha。"""
        self.assertIn("if (!(w > 0)) return {k: k, f: 1};", _fn("verseHeadAt"))

    def test_without_measured_widths_the_frac_stays_zero(self):
        """没有实测字宽就**不许猜**零头：猜大了等于把还没念到的字先亮出来。"""
        self.assertIn("{k: Math.round(p * c.n), f: 0}", _fn("verseHeadAt"))

    def test_rounding_in_the_fallback_is_untouched(self):
        """退化路径仍按老口径 `Math.round`：给没有字宽可测的那些页面留着旧行为。"""
        self.assertIn("Math.round(p * c.n)", _fn("verseHeadAt"))


class EdgeWrite(unittest.TestCase):
    """每帧只重写**正在念的那一个字**。"""

    def test_outside_the_cross_gate(self):
        src = _update_body()
        i = src.index("verseVedApply(L, k);") + len("verseVedApply(L, k);")
        between = src[i:src.index("verseEdgeSet(L, k, h.f)")]
        # 跨过至少一个闭合花括号，才可能已经出了 `if (k !== L.lastK)` 那道门
        self.assertGreater(between.count("}"), between.count("{"),
                           "亚字写入被关进跨格那道门里了：门不跨就退回按字跳格")

    def test_cross_gate_still_guards_the_full_repaint(self):
        """整数部分的全量刷必须还在门里——删掉门就是每帧重写整句。"""
        src = _update_body()
        self.assertIn("if (k !== L.lastK) {", src)
        self.assertEqual(len(re.findall(r"verseVedApply\(", src)), 1)

    def test_settle_check_still_precedes_the_gate(self):
        """句尾收束的判据必须在跨格那道门之前（只有 p 满时才准，一进门就漏）。"""
        src = _update_body()
        self.assertLess(src.index("verseEmSettle(L)"), src.index("if (k !== L.lastK)"))

    def test_only_one_glyph_is_written(self):
        src = _fn("verseEdgeSet")
        self.assertEqual(len(re.findall(r"sp\.style\.opacity = v", src)), 1)
        self.assertIn("const sp = spans[j];", src)

    def test_whole_line_read_means_no_edge(self):
        """整句念完没有"正在念"的字：不判这一条，末尾会去写 map[n]（越界）。"""
        self.assertIn("if (k >= map.length) return;", _fn("verseEdgeSet"))

    def test_unmapped_glyph_is_skipped(self):
        self.assertIn("if (!(j >= 0) || j >= spans.length) return;", _fn("verseEdgeSet"))

    def test_alpha_is_one_minus_frac(self):
        """f=0 整格压着、f=1 整格让出：方向写反了就是"先亮没念到的那一端"。"""
        self.assertIn("const v = f >= 1 ? verseVedLit : (1 - f).toFixed(3);",
                      _fn("verseEdgeSet"))

    def test_value_unchanged_means_no_write(self):
        self.assertIn("if (sp.style.opacity !== v) sp.style.opacity = v;",
                      _fn("verseEdgeSet"))


class Purity(unittest.TestCase):
    """连续性：这一位是**每帧重推**出来的，不是攒出来的。"""

    def test_no_accumulation(self):
        """不许出现 `L.<x> +=` 这类累加：攒出来的值在缓存被清时就成了孤儿。"""
        self.assertIsNone(re.search(r"L\.\w+\s*\+=", RUNTIME),
                          "runtime.js 又新增了累加状态：字体就绪清缓存时会留下孤儿值")

    def test_no_transition_on_progress(self):
        """逐字进度不许挂 transition：唯一例外是句尾收束那条规则。"""
        rules = re.findall(r"[^\n]*transition:\s*opacity[^\n]*", CSS)
        self.assertTrue(rules, "CSS 里已经没有 opacity 过渡规则了？")
        for r in rules:
            self.assertIn("verse-ved-settled", r,
                          "给逐字进度加过渡会让高亮追不上逐帧 seek：%s" % r)


class Ships(unittest.TestCase):
    """改动真的进了产物：runtime.js 是内联进 index.html 的。"""

    def test_generated_html_carries_it(self):
        html = _render()
        self.assertIn("verseHeadAt", html)
        self.assertIn("verseEdgeSet", html)


if __name__ == "__main__":
    unittest.main()
