"""_svg_sanitize 的净化口径：墙钟动画定义、外链 url()、以及"注释里提一句"的误伤。

判据都落在 <style> 文本上，所以这里直接喂 CSS（_clean_style_text）——内联前的那层
结构化改写另由 test_canvas_kit / test_stage_carry 的端到端覆盖。
"""
import unittest

import _helpers as H                       # noqa: F401  sys.path 装配
import _svg_sanitize as S


def _clean(css):
    """过一遍 <style> 文本的净化，只返回产物（notes 是给人看的，这里不关心）。"""
    return S._clean_style_text(css, [])


class WallclockDefinitions(unittest.TestCase):
    def test_prefixed_keyframes_is_stripped_too(self):
        """厂商前缀的 @keyframes 同样是墙钟定义，存在性判定不能用裸子串。

        证伪：守卫写成 `"@keyframes" in css.lower()` 时，`@-webkit-keyframes` 整块留在
        <style> 里（实测）——_KEYFRAMES_AT 认得前缀、守卫认不得，于是"墙钟已剥净"
        那句 note 成了谎话。定义体虽是惰性的，但它正是这条不变量要兑现的对象。
        """
        out = _clean("@-webkit-keyframes spin{from{opacity:0}} .a{fill:red}")
        self.assertNotIn("keyframes", out.lower())
        self.assertIn(".a{fill:red}", out)

    def test_keyframes_named_in_a_comment_does_not_eat_the_next_rule(self):
        """注释里提一句 @keyframes，不能让后面那条真规则被当成规则体吃掉。

        证伪：不先剥注释时实测 `/* 用 @keyframes 做循环 */ .a{fill:red}` 里的 .a 被删
        ——括号配平从注释里的字样起跳，一路吃到 .a 的闭合花括号。作者只是写了句说明，
        产物却少一条样式，且没有任何提示。
        """
        out = _clean("/* 用 @keyframes 做循环 */ .a{fill:red} .b{fill:blue}")
        self.assertIn(".a{fill:red}", out)
        self.assertIn(".b{fill:blue}", out)

    def test_truncated_keyframes_keeps_the_rest(self):
        """残缺规则（括号没配平到 0）保留剩余文本：净化不越权替作者修坏 CSS。

        证伪：照原写法一路扫到末尾硬删，会把 @keyframes 之后**所有**内容一起吞掉，
        与 docstring 承诺的"保留剩余文本"正好相反。
        """
        out = _clean("@keyframes k{0%{opacity:0} .a{fill:red}")
        self.assertIn(".a{fill:red}", out)


class ExternalUrls(unittest.TestCase):
    def test_url_is_neutralized_whole_not_half(self):
        """外链 url() 要整颗换成 about:blank，只吃 scheme 那一截会留下半截 URL。

        证伪：替换串原先缺收尾的 `)`，产物是 `url(about:blank//evil.com/a.png)`
        （实测）——既不是合法 CSS，要拦的地址也还明明白白写在成片里。
        """
        out = _clean(".a{background:url(http://evil.com/a.png)}")
        self.assertNotIn("evil.com", out)
        self.assertIn("url(about:blank)", out)
        self.assertEqual(out.count("url("), out.count(")"), out)
