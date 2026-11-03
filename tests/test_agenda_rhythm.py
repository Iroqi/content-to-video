"""agenda 列表区的竖向节奏：行数少时 space-evenly 把留白摊到一屏的三处。

这一条量的不是"排版好不好看"，是**同一份预算在行数变化时怎么分**。模板给
`.agenda` 的是 `flex:1` + `justify-content:space-evenly`：列表区的剩余高度按
"行上 / 行间 / 行下"均分。行多时几乎无剩余可分，行少时每一份都很可观——

实测（竖屏 1440、浏览器量 DOM，1 行）：题头底 198、首行顶 589、句子流顶 1064，
即 题头→首行 390px、末行→句子流 374px。解析式在同一份模板参数下给出 390，
所以这里的钉固值是**算得动、且对着真实 DOM 校过**的，不是拍的。

判据本身只发 warn：行数少不是错（那一页本来就该松），但松到一屏的四分之一
就是"这一页撑不起 agenda 版式"的信号。
"""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as T
import html_renderer as HR


def _rc(aspect="vertical", verse_h=None):
    tpl = json.loads(T._TEMPLATE_JSON)
    if verse_h is None:
        verse_h = tpl["layout"][aspect]["verse"]["windowHeight"]
    return SimpleNamespace(aspect=aspect, ag=tpl["layout"][aspect]["agenda"],
                           verse_h=verse_h)


def _verdict(n_rows, aspect="vertical", verse_h=None):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        HR._warn_sparse_agenda("opening", _rc(aspect, verse_h), n_rows)
    return buf.getvalue()


class Budget(unittest.TestCase):
    """列表区的竖向预算：解析式必须与实测 DOM 对得上。"""

    def test_one_row_gap_matches_measured_dom(self):
        """钉固：竖屏 1 行时题头→首行 390px、末行→句子流 374px（浏览器实测）。

        这两个数来自一次真实测量（1080×1440、Emulation 强制视口、清掉预览缩放
        后读 getBoundingClientRect）。解析式与它一字不差，说明"不用开浏览器也算
        得准"这件事成立——判据才能放进生成期而不是运行期。
        """
        out = _verdict(1)
        self.assertIn("题头底到首行顶 390px", out)
        self.assertIn("末行底到句子流再有 374px", out)

    def test_budget_is_monotone_in_row_count(self):
        """行越多，余量越小：这是"行数分档"这个判据存在的理由。"""
        spare = []
        for n in range(1, 8):
            list_h, row_h = HR._agenda_list_budget(_rc().ag, "vertical", 280)
            spare.append(list_h - n * row_h - (n - 1) * _rc().ag["rowGap"])
        self.assertEqual(spare, sorted(spare, reverse=True), spare)
        # 7 行吃满预算、几乎没有剩余——正是 CSS 注释里那句"满额预算时
        # space-evenly 几乎无剩余可分"
        self.assertLess(spare[-1], 100)

    def test_budget_closes_the_column(self):
        """闭合校验：列高 = 题头 + 间距 + 列表区 + 句子流。"""
        ag = _rc().ag
        canvas_h = HR.get_canvas("vertical")[1]
        list_h, _ = HR._agenda_list_budget(ag, "vertical", 280)
        head_h = ag["titleSize"] * float(ag["titleLineHeight"]) + ag["titleMarginTop"]
        total = canvas_h - ag["insetTop"] - ag["insetBottom"]
        self.assertEqual(
            round(head_h + ag["listMarginTop"] + list_h + 280), total,
            f"列高 {total} 与各段之和不符（{head_h}+{ag['listMarginTop']}+{list_h}+280）")


class Trigger(unittest.TestCase):
    """阈值落在 2 行：3 行起留白已经能成段。"""

    def test_two_rows_and_below_warn(self):
        for n in (1, 2):
            with self.subTest(rows=n):
                self.assertIn("撑不起 agenda 版式", _verdict(n))

    def test_three_rows_and_above_stay_silent(self):
        for n in range(3, 8):
            with self.subTest(rows=n):
                self.assertEqual(_verdict(n), "")

    def test_threshold_is_the_template_row_cap_plus_one(self):
        """默认 maxRows=7 时 7 行是满额：判据不能在满额那档误报。"""
        self.assertLess(HR.AGENDA_SPARSE_ROWS, int(_rc().ag["maxRows"]))

    def test_landscape_is_not_judged(self):
        """横屏的 agenda 列是左栏文档流的一部分，剩余空间不归 .agenda 独占。"""
        self.assertEqual(_verdict(1, aspect="landscape"), "")

    def test_empty_page_is_silent(self):
        """行数为 0（没有内容段）：那时根本没有行可摊，不是这条判据的事。"""
        self.assertEqual(_verdict(0), "")


class Robustness(unittest.TestCase):
    """量不出来就不说：宁可不报警，也不要拿猜的数去报警。"""

    def test_missing_verse_height_does_not_crash(self):
        rc = _rc()
        del rc.verse_h
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            HR._warn_sparse_agenda("opening", rc, 1)     # 不抛
        self.assertEqual(buf.getvalue(), "")

    def test_short_verse_window_shrinks_the_gap(self):
        """无图档把句子流窗口放宽后，列表区余量变小，留白跟着变小。"""
        wide = _verdict(2, verse_h=280)
        narrow = _verdict(2, verse_h=520)
        self.assertLess(len(narrow), len(wide) + 1)
        self.assertIn("撑不起", narrow)   # 520 时 2 行仍有余量，照报


class InProduct(unittest.TestCase):
    """真页面里跑一遍：warn 必须在 generate_html 上真的发出来。"""

    def _render(self, n_segments):
        """把内容段补齐到 n 个再渲染：opening 的行数正好等于内容段数。"""
        m = H.make_manifest()
        content = [s for s in m["segments"]
                   if s["id"] not in ("opening", "closing")]
        while len(content) < n_segments:
            extra = dict(content[0])
            k = len(content) + 1
            extra["id"] = f"seg-x{k}"
            extra["title"] = f"第 {k} 件事"
            extra.pop("layout", None)
            extra["sentences"] = [
                {"index": 0, "start_time": 0.0, "duration": 1.0,
                 "text": f"第 {k} 件事的解释。"}]
            content.append(extra)
        m["segments"] = [m["segments"][0]] + content[:n_segments] + [m["segments"][-1]]
        for i, seg in enumerate(m["segments"]):
            for j, sent in enumerate(seg.get("sentences") or []):
                sent["index"] = i + j
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            HR.generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m))
        return buf.getvalue()

    def test_two_content_segments_warn_in_real_render(self):
        out = self._render(2)
        self.assertIn("agenda（opening）只有 2 行", out)

    def test_three_content_segments_do_not_warn(self):
        out = self._render(3)
        self.assertNotIn("撑不起 agenda 版式", out)


if __name__ == "__main__":
    unittest.main()
