"""`count` / `type` 与同一 <text> 里的旁支内容：整段替换会连单位一起抹掉。

`count` 与 `type` 都靠 `textContent = …` 写入，那是**整条子树替换**——同一
`<text>` 里的 `<tspan>`（最常见的是"万""%""个月"这类小一号的单位）会在第一帧
之前就被抹掉：渲染端那条 `f()` 在建时间线时无条件调一次，连 t=0 都已经抹过。

真机取帧确认过（1080×1440、to=1286）：
  错写法 `<text id="num">0<tspan> 万</tspan></text>` → 148 / 916 / 1263
  对写法 `<text><tspan id="num">0</tspan><tspan> 万</tspan></text>` → 148 万 / 916 万 / 1263 万

判据只能放在生成期：契约层手上只有选择器字符串，读不到图里的树形；渲染端也
改不成对的（改 .firstChild.nodeValue 会留下 <tspan>、却丢掉数字与单位之间的空格，
同样错且更难发现）。所以放在 `animate_prepare`——它同时握有 SVG 原文与 steps。

拆出独立文件而不是并入 test_animate_timeline：后者已到单文件 120 条的上限
（`tests/check_test_budget.py` 的契约），这一类判据也不服务同一个决策时刻。
"""
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as G


def _write_svg(tmp, sid, text):
    d = os.path.join(tmp, "images")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{sid}.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"images/{sid}.svg"


class CountEatsLabel(unittest.TestCase):
    """count / type 是整段文本替换，会把同一 <text> 里的 <tspan> 单位一起抹掉。

    这是实测的静默失效：`<text id="num">0<tspan> 万</tspan></text>` 上写 count，
    成片滚出 "1263"，画面上的"万"从第一帧起就不在——量级差 10⁴，而生成期全绿。
    渲染端那条 `f()` 在建时间线时无条件调用一次（见 html_renderer 的 count 分支），
    连 t=0 都已经被抹过，所以这不是"演到中途丢了个后缀"，是整句字没了。

    判据只能放在生成期：契约层拿到的只是选择器字符串，读不到图里的树形。
    """

    SEG = [{"id": "seg-a", "layout": "canvas",
            "sentences": [{"index": 0, "start_time": 0.0, "duration": 2.0}]}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, inner, steps):
        rel = _write_svg(
            self.tmp.name, "seg-a",
            '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440" '
            'viewBox="0 0 1080 1440">' + inner + "</svg>")
        images = {"seg-a": {"src": rel, "animate": {"steps": steps}}}
        return G.animate_prepare(images, self.SEG, self.tmp.name)

    def test_unit_tspan_in_same_text_is_rejected(self):
        """数字与单位挤在同一条 <text> 里：单位会没。"""
        errs, _ = self._run(
            '<text id="num" x="10" y="10">0<tspan font-size="12"> 万</tspan></text>',
            [{"at": 0, "target": "#num", "count": {"from": 0, "to": 1286}}])
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("<tspan>", errs[0])
        self.assertIn("#num", errs[0])
        # 报错必须给出解法，否则作者只知道错、不知道往哪改
        self.assertIn('<tspan id="', errs[0])

    def test_tspan_target_inside_label_is_accepted(self):
        """推荐写法：数字单独包一层 <tspan id>，单位那段留着不动 —— 放行。

        判据必须放行它自己建议的解法，否则作者无路可走（这一条最初就漏了：
        把命中元素自身持有的数字文本也当成"会被抹掉的旁支"，推荐写法照样被拦）。
        """
        errs, _ = self._run(
            '<text x="10" y="10"><tspan id="num">0</tspan>'
            '<tspan font-size="12"> 万</tspan></text>',
            [{"at": 0, "target": "#num", "count": {"from": 0, "to": 1286}}])
        self.assertEqual(errs, [])

    def test_plain_text_still_accepted(self):
        """没有子节点的普通 <text>：本来就是 count 的标准用法，不该有噪音。"""
        errs, _ = self._run(
            '<text id="num" x="10" y="10">0</text>',
            [{"at": 0, "target": "#num", "count": {"to": 99}}])
        self.assertEqual(errs, [])

    def test_type_is_judged_the_same_way(self):
        """type 走同一条 textContent 赋值，同一条判据。"""
        errs, _ = self._run(
            '<text id="cap" x="10" y="10">一句话<tspan>*</tspan></text>',
            [{"at": 0, "target": "#cap", "type": {}}])
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("type", errs[0])

    def test_class_target_is_judged_too(self):
        """class 选择器（契约层允许）同样要查：命中多条就逐条算。"""
        errs, _ = self._run(
            '<text class="k" x="10" y="10">1<tspan>%</tspan></text>'
            '<text class="k" x="10" y="60">2</text>',
            [{"at": 0, "target": ".k", "count": {"to": 50}}])
        self.assertEqual(len(errs), 1, errs)

    def test_missing_target_not_double_reported(self):
        """选择器落空由 check_animate 报 target 存在性，这里不能再报一条。"""
        errs, _ = self._run(
            '<text id="other" x="10" y="10">0</text>',
            [{"at": 0, "target": "#ghost", "count": {"to": 5}}])
        self.assertEqual(len([e for e in errs if "整段替换" in e]), 0, errs)
        self.assertTrue(any("找不到" in e for e in errs), errs)

    def test_other_kinds_do_not_trip_this_check(self):
        """只有 count / type 是整段替换；属性补间碰字不碰树，单位照旧在。"""
        errs, _ = self._run(
            '<text id="num" x="10" y="10">0<tspan> 万</tspan></text>',
            [{"at": 0, "target": "#num", "to": {"opacity": 1}}])
        self.assertEqual([e for e in errs if "整段替换" in e], [])


if __name__ == "__main__":
    unittest.main()
