import unittest

import _helpers as H  # noqa: F401
from _text import split_sentences


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


if __name__ == "__main__":
    unittest.main()
