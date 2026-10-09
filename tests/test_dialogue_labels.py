"""对话段说话人标签：字幕上的轮次归属 + label 的上墙契约。

一段双人对话在成片里原本靠音色区分谁在说——静音播放、听障观看、或两个
音色选得相近时，观众手里只剩一串分不清轮次的字幕。这枚行首标签把"谁在说"
从声音里搬到画面上（与画布页"关键句画进图里"同源的取舍）。

它只挂在**轮次切换的首句**：同一说话人连着说三句不刷三遍抬头。既然上墙，
`speakers[].label` 就受上墙那三道管：单行、不收分号、字数有上限。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _source_schema as SRC
from html_renderer import generate_html


def bad(fn, *a, contains=None):
    """与 tests/test_contracts.py 同一个断言器：契约层必须报人话，不是裸 TypeError。"""
    try:
        fn(*a)
    except (ValueError, SystemExit) as e:
        if contains:
            assert contains in str(e), f"报错里没有 {contains!r}: {e}"
        return
    except TypeError as e:
        raise AssertionError(f"契约层抛了裸 TypeError（{e}）") from None
    raise AssertionError("应该被契约层拒绝，却通过了")


def render(aspect="portrait", source=None):
    m = H.make_manifest(source or H.sample_dialogue_source())
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m), aspect=aspect)


def verse_lines(html, sid="seg-a"):
    """取出某段 verse 的每一行（含行首可能的说话人标签）。

    非贪婪吃到 `</div></div>`（verse-clip + verse 两个闭合）正好停在句子流
    结束处：行与行之间是 `</div><div class="verse-line"`，行间不会出现连续
    两个闭合，所以这个边界不会提前截断最后一行。
    """
    m = re.search(r'<div class="verse" id="verse-%s".*?</div></div>' % sid,
                  html, re.S)
    assert m, f"找不到 {sid} 的 verse 块"
    return re.findall(r'<div class="verse-line"[^>]*>(.*?)</div>', m.group(0), re.S)


def who_of(line):
    """行首那枚说话人标签的文本；没有标签返回 None。"""
    m = re.match(r'<span class="verse-who"[^>]*>(.*?)</span>', line)
    return m.group(1) if m else None


class Render(unittest.TestCase):
    def test_label_only_on_first_sentence_of_each_turn(self):
        # 轮次：host(1句) → guide(2句) → host(1句)，共 4 句
        lines = verse_lines(render())
        self.assertEqual(len(lines), 4)
        # 同一说话人连着说的第二句：不再重复挂标签（行首不变成一列复读的抬头）
        self.assertEqual([who_of(x) for x in lines],
                         ["主播", "讲解", None, "主播"])

    def test_solo_segments_have_no_label(self):
        # 独白段没有 speaker，HTML 不该多出任何 who 节点
        # （CSS 里的 .verse-who 规则当然还在，断言的是实例）
        html = render(source=H.sample_source())
        self.assertNotIn('<span class="verse-who">', html)

    def test_both_aspects_render_the_label(self):
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                self.assertEqual(who_of(verse_lines(render(aspect))[0]), "主播")

    def test_who_color_var_is_per_segment(self):
        # 颜色是本段 accent 的文本安全副本，按段写在 .verse-clip 上，
        # CSS 只消费变量——CSS 里不许存第二份色值。
        html = render()
        self.assertIn('style="--ctv-verse-who-color:#ffd54f"', html)
        for aspect, font in (("portrait", "28px"), ("landscape", "24px")):
            with self.subTest(aspect=aspect):
                self.assertIn(f"--ctv-verse-who-font:{font}", render(aspect))

    def test_label_text_is_escaped(self):
        # label 进 HTML 必须走现有转义路径（信源不可信）。payload 特意压在
        # 字数上限之内，测的是转义而不是长度闸门。
        src = H.sample_dialogue_source()
        src["speakers"]["host"]["label"] = "<b>&"
        html = render(source=src)
        self.assertIn("&lt;b&gt;&amp;", html)
        self.assertNotIn("<b>&<", html)


class LabelContract(unittest.TestCase):
    """label 现在是上墙文字，所以也受上墙那三道管。"""

    def _src(self, **speaker_kwargs):
        src = H.sample_dialogue_source()
        src["speakers"]["host"].update(speaker_kwargs)
        return src

    def test_short_label_ok(self):
        SRC.validate_segments_source(self._src(label="主播"))

    def test_label_at_limit_ok(self):
        limit = SRC.SPEAKER_LABEL_MAX_CHARS
        SRC.validate_segments_source(self._src(label="主" * limit))

    def test_overlong_label_rejected(self):
        limit = SRC.SPEAKER_LABEL_MAX_CHARS
        bad(SRC.validate_segments_source, self._src(label="主" * (limit + 1)),
            contains="超过")

    def test_label_with_semicolon_rejected(self):
        bad(SRC.validate_segments_source, self._src(label="主播；讲解"),
            contains="分号")

    def test_label_with_newline_rejected(self):
        bad(SRC.validate_segments_source, self._src(label="主播\n讲解"),
            contains="单行")

    def test_fallback_speaker_name_is_checked_too(self):
        # 不写 label 时上墙的就是 speaker 名本身：只校验显式 label 等于给
        # 这条约束留了个绕过去的口子。key 起长了就必须显式给一枚短 label。
        src = H.sample_dialogue_source()
        src["speakers"] = {
            "interviewer": {"voice_id": "茉莉"},   # 11 字符，无 label
            "guide": {"voice_id": "苏打", "label": "讲解"},
        }
        for turn in src["segments"][0]["dialogue"]:
            if turn["speaker"] == "host":
                turn["speaker"] = "interviewer"
        bad(SRC.validate_segments_source, src, contains="interviewer")

    def test_short_label_rescues_a_long_speaker_key(self):
        src = H.sample_dialogue_source()
        src["speakers"] = {
            "interviewer": {"voice_id": "茉莉", "label": "提问"},
            "guide": {"voice_id": "苏打", "label": "讲解"},
        }
        for turn in src["segments"][0]["dialogue"]:
            if turn["speaker"] == "host":
                turn["speaker"] = "interviewer"
        SRC.validate_segments_source(src)
        # 上墙的是 label，不是那个长 key
        self.assertEqual(who_of(verse_lines(render(source=src))[0]), "提问")


if __name__ == "__main__":
    unittest.main()
