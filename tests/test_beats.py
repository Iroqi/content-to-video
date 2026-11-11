#!/usr/bin/env python3
"""台词板（_beats）的判据测试。

这一层的价值全在"抓得住"与"不乱报"之间：抓不住就等于没有，乱报就会被关掉。
所以每条判据都配一对——一个该抓的现场，一个不该抓的现场。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

from _beats import (audit, count, sentence_terminal_count,  # noqa: E402
                    validate_beats)


def _manifest(segments):
    """最小可用 manifest 形状（只放 _beats 会读的那几个键）。"""
    return {"segments": segments}


def _seg(sid, texts, beats=None):
    sg = {"id": sid,
          "sentences": [{"index": i, "text": t, "start_time": 0.0,
                         "duration": 1.0} for i, t in enumerate(texts)]}
    if beats is not None:
        sg["beats"] = beats
    return sg


class TestShape(unittest.TestCase):
    """形状校验：简写 / 全写 / 混用 / 坏值。"""

    def test_short_form_index_is_sentence_order(self):
        got = validate_beats(["把两条线合成一条", "推到第三态"], "seg1 的 beats")
        self.assertEqual([b["on"] for b in got], [0, 1])
        self.assertEqual(got[1]["change"], "推到第三态")

    def test_full_form_can_skip_a_sentence(self):
        got = validate_beats([{"on": 0, "change": "点亮 a"},
                              {"on": 3, "change": "推到第三态"}], "seg1 的 beats")
        self.assertEqual([b["on"] for b in got], [0, 3])

    def test_mixing_the_two_forms_is_rejected(self):
        # 混用时"第几拍"有两个来源，没写 on 的那几条会静默错位。
        with self.assertRaises(ValueError) as cm:
            validate_beats(["点亮 a", {"on": 3, "change": "推到第三态"}], "seg1 的 beats")
        self.assertIn("混用", str(cm.exception))

    def test_bad_item_type_is_reported_before_mixing(self):
        """修复前：`["点亮 a", 5]` 报"混用了两种写法（short）"。

        病因是第 2 条是数字，但 kinds 里 None 也凑一个数，len>1 先命中混用
        分支并 raise，那条逐项定位的"必须是字符串或 {on, change} 对象"永远
        执行不到——作者照"混用"的提示去统一写法，改不掉真病因。
        """
        for bad in (5, None, [], True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    validate_beats(["点亮 a", bad], "seg1 的 beats")
                msg = str(cm.exception)
                self.assertNotIn("混用", msg)
                self.assertIn("beats[1]", msg)
                self.assertIn("实际是", msg)

    def test_missing_on_is_rejected_with_a_reason(self):
        with self.assertRaises(ValueError) as cm:
            validate_beats([{"change": "点亮 a"}], "seg1 的 beats")
        msg = str(cm.exception)
        self.assertIn("'on'", msg)
        self.assertIn("第几句", msg)

    def test_negative_and_fractional_on_rejected(self):
        with self.assertRaises(ValueError):
            validate_beats([{"on": -1, "change": "x"}], "seg1 的 beats")
        with self.assertRaises(ValueError):
            validate_beats([{"on": 0.5, "change": "x"}], "seg1 的 beats")

    def test_duplicate_on_rejected(self):
        with self.assertRaises(ValueError) as cm:
            validate_beats([{"on": 1, "change": "a"}, {"on": 1, "change": "b"}],
                           "seg1 的 beats")
        self.assertIn("两次", str(cm.exception))

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            validate_beats([{"on": 0, "change": "a", "at": 1.5}], "seg1 的 beats")

    def test_change_must_be_short_and_human(self):
        with self.assertRaises(ValueError):
            validate_beats(["   "], "seg1 的 beats")
        # 长到 60 字以上说明在写实现（属性/时长/缓动），那是 steps 的活
        with self.assertRaises(ValueError) as cm:
            validate_beats(["把 a 的 opacity 从 1 补到 0.3，用 power2.out，历时 0.8 秒，"
                            "同时把 b 的 y 从 100 移到 0，再给 c 加一层高斯模糊"],
                           "seg1 的 beats")
        self.assertIn("意图", str(cm.exception))

    def test_empty_and_none_are_no_beats(self):
        self.assertEqual(validate_beats(None, "x"), [])
        self.assertEqual(validate_beats([], "x"), [])


class TestTerminalCount(unittest.TestCase):
    def test_decimal_point_is_not_a_sentence_end(self):
        # 3.14 不是两个结论。英文句号在数字之间是小数点。
        self.assertEqual(sentence_terminal_count("圆周率约等于 3.14 左右"), 0)

    def test_exclamation_and_semicolon_count(self):
        self.assertEqual(sentence_terminal_count("看这里！它变了；再变一次"), 2)

    def test_english_sentence_end_counts(self):
        self.assertEqual(sentence_terminal_count("It moved. Then it stopped."), 2)


class TestAudit(unittest.TestCase):
    """按最终时间轴核对：越界要抓，一句话讲两件事要提。"""

    def test_valid_beats_pass_silently(self):
        errs, warns, total = audit(_manifest([
            _seg("seg1", ["第一句", "第二句", "第三句"],
                 beats=[{"on": 0, "change": "点亮 a"}, {"on": 2, "change": "推到第三态"}])]))
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])
        self.assertEqual(total, 2)
        self.assertEqual(count(_manifest([
            _seg("seg1", ["a"], beats=[{"on": 0, "change": "x"}])])), 1)

    def test_on_past_the_last_sentence_is_an_error(self):
        # 原稿 5 句、重分句后 3 句：on=4 落到空处，成片里完全看不出来。
        errs, warns, _ = audit(_manifest([
            _seg("seg1", ["一", "二", "三"],
                 beats=[{"on": 4, "change": "推到第五态"}])]))
        self.assertEqual(len(errs), 1)
        self.assertIn("越界", errs[0])
        self.assertIn("只有 3 句", errs[0])

    def test_sentence_carrying_two_conclusions_is_flagged(self):
        errs, warns, _ = audit(_manifest([
            _seg("seg1", ["它先左移。然后翻转。最后合并。", "收尾"],
                 beats=[{"on": 0, "change": "合成一条"}])]))
        self.assertEqual(errs, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("终止标点", warns[0])

    def test_declared_beat_without_any_tween_is_flagged(self):
        # 台词板写成愿望清单：比不写更坏，因为作者以为写了。
        m = _manifest([_seg("seg1", ["一", "二"],
                            beats=[{"on": 0, "change": "把两条线合成一条"}])])
        errs, warns, _ = audit(m, images={"seg1": {"src": "images/seg1.svg"}})
        self.assertEqual(errs, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("画面不会动", warns[0])

    def test_declared_beat_with_animate_is_not_flagged(self):
        m = _manifest([_seg("seg1", ["一", "二"],
                            beats=[{"on": 1, "change": "把两条线合成一条"}])])
        images = {"seg1": {"src": "images/seg1.svg",
                           "animate": {"steps": [{"at": 1, "target": "#a",
                                                   "to": {"opacity": 0}}]}}}
        errs, warns, _ = audit(m, images=images)
        self.assertEqual((errs, warns), ([], []))

    def test_slot_animate_also_counts_as_motion(self):
        m = _manifest([_seg("seg1", ["一"], beats=[{"on": 0, "change": "压暗 a"}])])
        images = {"seg1": {"src": "images/seg1.svg",
                           "animate": {"steps": [{"at": 0, "target": "#a",
                                                  "to": {"opacity": 0.3}}]}}}
        errs, warns, _ = audit(m, images=images)
        self.assertEqual((errs, warns), ([], []))

    def test_a_carried_page_alone_does_not_count_as_motion(self):
        # 接续只搬上一页的收尾态，本身不产生新的拍：这一段的台词板照样落空。
        m = _manifest([_seg("seg1", ["一"], beats=[{"on": 0, "change": "接着演"}])])
        images = {"seg1": {"src": "images/seg1.svg"}}
        _errs, warns, _ = audit(m, images=images)
        self.assertEqual(len(warns), 1)

    def test_without_images_json_the_motion_check_stays_silent(self):
        # 拿不到 images.json 就闭嘴：报一条无凭据的 warn 比不报更糟。
        m = _manifest([_seg("seg1", ["一"], beats=[{"on": 0, "change": "动一下"}])])
        errs, warns, _ = audit(m)
        self.assertEqual((errs, warns), ([], []))

    def test_segments_without_beats_are_ignored(self):
        errs, warns, total = audit(_manifest([_seg("seg1", ["一", "二"])]))
        self.assertEqual((errs, warns, total), ([], [], 0))


import _beats



class BeatsDuplicatePrinterStaysOut(unittest.TestCase):
    """`print_audit` 全仓零调用方：run.py 自己又写了一遍同样的打印循环。"""

    def test_beats_duplicate_printer_is_gone(self):
        self.assertFalse(hasattr(_beats, "print_audit"))


if __name__ == "__main__":
    unittest.main()
