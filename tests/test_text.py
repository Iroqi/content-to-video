import contextlib
import io
import unittest

import _helpers as H  # noqa: F401
from _text import _quote_stack, split_sentences


class Split(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   \n "), [])

    def test_chinese_terminators(self):
        self.assertEqual(split_sentences("今天天气很好。明天也是晴天！后天会下雨吗？"),
                         ["今天天气很好。", "明天也是晴天！", "后天会下雨吗？"])

    def test_comma_does_not_split(self):
        self.assertEqual(len(split_sentences("先这样，再那样，最后收尾。")), 1)

    def test_decimal_and_abbrev_not_split(self):
        self.assertEqual(len(split_sentences("圆周率约等于3.14，不是3。")), 1)
        self.assertEqual(len(split_sentences("Mr. Smith lives in the U.S. now.")), 1)

    def test_english_sentences_split(self):
        self.assertEqual(len(split_sentences("This is one. This is two.")), 2)

    def test_short_fragments_merge(self):
        # 文档契约：不足 5 字的片段并入下一句，凑够 8 字才独立成行
        out = split_sentences("停。别动。")
        self.assertEqual(len(out), 1)
        self.assertEqual(len(split_sentences("停。往左边走三步再停下。")), 1)

    def test_five_char_sentence_stands_alone(self):
        out = split_sentences("就这么定了。接下来我们看第二件事。")
        self.assertEqual(len(out), 2)

    def test_newline_splits(self):
        self.assertEqual(len(split_sentences("第一行内容足够长一些\n第二行内容也足够长一些")), 2)


class QuoteOwnership(unittest.TestCase):
    """句末的 ” ’ " ' 归上一句，不能起下一句。

    判错的形状很难受：下一句以孤零零一个引号开头，TTS 把引号音节化、
    字幕第一格挂个孤立符号。老实现按"左边最近一枚同类引号"判，
    `张三说："好的。"然后走了。` 里收尾那枚的左边只有它自己那对的开头，
    于是被判成"开"，劈出 `'"然后走了。'`。
    """

    def test_closing_quote_closes_the_sentence(self):
        self.assertEqual(split_sentences('张三说："好的。"然后走了。'),
                         ['张三说："好的。"', '然后走了。'])

    def test_curly_quote_pair_same_way(self):
        self.assertEqual(split_sentences("张三说：‘好的。’然后走了。"),
                         ["张三说：‘好的。’", "然后走了。"])

    def test_every_sentence_quoting_itself(self):
        # 每句各打一对引号 ⇒ 引号总数是偶数。按"总数奇偶"判必错，按配对栈判才对。
        self.assertEqual(split_sentences('"现在开始。"他说完了。'),
                         ['"现在开始。"', '他说完了。'])
        self.assertEqual(split_sentences('"第一句话。"他说："第二句话。"'),
                         ['"第一句话。"', '他说："第二句话。"'])

    def test_nested_quotes_close_inside(self):
        # 内层收尾不该把后面的 `然后：“` 一起吞掉
        self.assertEqual(split_sentences("他说：“第一点。”然后：“第二点。”结束了。"),
                         ["他说：“第一点。”", "然后：“第二点。”结束了。"])

    def test_bracket_and_quote_mix(self):
        self.assertEqual(split_sentences("：（她说：“走吧。”）然后大家散了。"),
                         ["：（她说：“走吧。”）", "然后大家散了。"])

    def test_apostrophe_is_not_an_opener(self):
        # dogs' 里的 ' 是英文所有格，不该开启引号，也不该让后面一直算"在引号里"
        self.assertEqual(split_sentences("dogs' toys. Next one."),
                         ["dogs' toys.", "Next one."])

    def test_unpaired_curly_quote_still_absorbed(self):
        # 竖排/手打稿常漏开引号：句末这枚 ’ 左边没有 ‘，仍按收尾并进当前句，
        # 否则下一句首个字符就是孤零零一个右引号
        self.assertEqual(split_sentences("张三说：好的。’然后走了。"),
                         ["张三说：好的。’", "然后走了。"])

    def test_stack_depth_reports_top_of_stack(self):
        # stacks[i] 是 text[:i] 的栈快照；长度是 len+1
        self.assertEqual(_quote_stack('说："好'),
                         [(), (), (), ('"',), ('"',)])
        self.assertEqual(_quote_stack("说：“好"),
                         [(), (), (), ("“",), ("“",)])


# 两句样例稿件：一句 38 字（落在 32–45 的第二档），一句 56 字（超过 45 的逐句档）。
# 长度用 split_sentences 实测过，两串各自独立成句，不会被短片段合并规则吃掉。
MID_SENTENCE = "这一段里有一句中等长度的句子，它落在三十到四十五个字之间，适合提醒再断一句。"
LONG_SENTENCE = ("这一段里有一句很长的句子，它超过四十五个字，念完整句会喘不过气，"
                 "字幕也会折成好几行，所以必须逐句报警提醒改写。")


def _stderr_of(source):
    from build_from_structured import build_parts
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        build_parts(source)
    return err.getvalue()


class NarrationLengthTiers(unittest.TestCase):
    """口播稿句长两档：>45 字逐句 [warn]，32–45 字每段汇总一条 [hint]。

    两档都只出声不拦截，所以这里断言的是"提示出现了几次、报了哪一段"，
    不锁死文案措辞。
    """

    def test_short_source_is_silent(self):
        out = _stderr_of(H.sample_source())
        self.assertNotIn("[hint]", out)
        self.assertNotIn("[warn]", out)

    def test_mid_sentence_gets_one_aggregated_hint_per_block(self):
        s = H.sample_source()
        s["segments"][0]["text"] = MID_SENTENCE + MID_SENTENCE
        out = _stderr_of(s)
        self.assertEqual(out.count("[hint]"), 1, out)
        self.assertIn("seg-a", out)
        self.assertIn("2 句", out)
        self.assertNotIn("[warn]", out)

    def test_long_sentence_still_warns_per_sentence(self):
        s = H.sample_source()
        s["segments"][0]["text"] = LONG_SENTENCE + LONG_SENTENCE
        out = _stderr_of(s)
        self.assertEqual(out.count("[warn]"), 2, out)
        self.assertNotIn("[hint]", out)

    def test_two_tiers_do_not_double_count(self):
        s = H.sample_source()
        s["segments"][0]["text"] = MID_SENTENCE + LONG_SENTENCE
        out = _stderr_of(s)
        self.assertEqual(out.count("[warn]"), 1, out)
        self.assertEqual(out.count("[hint]"), 1, out)
        self.assertIn("1 句", out)


if __name__ == "__main__":
    unittest.main()
