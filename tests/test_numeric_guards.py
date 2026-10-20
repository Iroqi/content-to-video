"""契约层的数值守卫：NaN / Infinity 一律拒收。

第一性原理：这些值**进得来**。Python 的 `json.load` 默认接受 `NaN` / `Infinity`
字面量（不是标准 JSON，但 parse 默认不拦），于是 `{"scale": NaN}` 能一路穿过
images.json 的校验层；而 NaN 的**任何比较都为 False**（`nan < 0` 也是 False），
所以只写 `isinstance(v, (int, float))` 的守卫会把它整条放行。

放行之后它坏在哪：`_fmt(nan)` 把 "nan" 写进 style/transform，浏览器把整条声明
丢弃——"烘了等于没烘"，画面停在动画前的样子，而生成期、渲染日志与
`hyperframes check` 全都一声不吭。这类"看起来校验过了"的静默退化，正是契约层
该在入口挡掉的东西。
"""
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
from _images_schema import validate_images_json

BAD = (float("nan"), float("inf"), float("-inf"))
# -inf 会被"非负/≥0"那一顺带拦下（-inf < 0 为真），另外两个（NaN、+inf）只能靠
# 有限性守卫——NaN 的任何比较都为 False，范围检查对它一律放行。
NEEDS_FINITE_GUARD = (float("nan"), float("inf"))


def _step(**kw):
    """一条最小的 director step（可覆盖任意字段）。

    给 at_time 时自动摘掉 at：契约层要求两个锚点二选一，两个都留着报的是另一条错。
    """
    step = {"at": 0, "target": "#a", "to": {"opacity": 1}}
    for k, v in kw.items():
        if k == "at_time":
            step.pop("at", None)
        step[k] = v
    return {"seg-a": {"src": "images/a.svg", "director": {"steps": [step]}}}


class NumericGuardsRejectNonFinite(unittest.TestCase):
    """逐个钉住：那 8 处守卫对 NaN/±Infinity 必须与对负数一样反应。"""

    def _assert_rejected(self, entry, needle=None, *, bad=None):
        with self.assertRaises(ValueError) as cm:
            validate_images_json(entry)
        msg = str(cm.exception)
        if needle:
            self.assertIn(needle, msg)
        if bad in NEEDS_FINITE_GUARD:
            # 只被范围检查"顺带"拦下不算修好：NaN/+inf 的比较全是 False，
            # 消息里必须点明是有限性守卫接住的。
            self.assertIn("NaN", msg)

    def test_count_to_and_from(self):
        for bad in BAD:
            for key in ("to", "from"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(
                        {"seg-a": {"src": "images/a.svg", "director": {"steps": [
                            {"at": 0, "target": "#a", "count": {key: bad}}]}}},
                        "必须是数字", bad=bad)

    def test_step_anchor_at(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(at=bad), bad=bad)

    def test_step_anchor_at_time(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(at_time=bad), "非负数字", bad=bad)

    def test_duration_and_delay(self):
        for bad in BAD:
            for key in ("duration", "delay"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(_step(**{key: bad}), "≥0", bad=bad)

    def test_stagger_number_form(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(stagger=bad), "每个间隔秒", bad=bad)

    def test_stagger_object_form(self):
        for bad in BAD:
            for key in ("each", "amount"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(_step(stagger={key: bad}), "≥0")
            with self.subTest(key="from", bad=bad):
                self._assert_rejected(_step(stagger={"from": bad}),
                                      "字符串或有限数字", bad=bad)

    def test_video_audio_opts(self):
        for bad in BAD:
            for opt in ("volume", "fade_in", "fade_out", "media_start"):
                with self.subTest(opt=opt, bad=bad):
                    self._assert_rejected(
                        {"seg-b": {"src": "images/b.mp4", opt: bad}},
                        "必须是数字", bad=bad)

    def test_finite_values_still_pass(self):
        """回归网：这道守卫不是"把数字全拒了"。"""
        validate_images_json({
            "seg-a": {"src": "images/a.svg", "director": {"steps": [
                {"at": 0, "target": "#a", "duration": 1.5, "delay": 0,
                 "stagger": {"each": 0.2, "from": "center", "amount": 1},
                 "to": {"opacity": 0}},
                {"at_time": "+0.5", "target": "#a", "count": {"to": 68.5}},
            ]}}})
        validate_images_json({"seg-b": {"src": "images/b.mp4", "volume": 0.25,
                                        "fade_in": 0.5}})


if __name__ == "__main__":
    unittest.main()
