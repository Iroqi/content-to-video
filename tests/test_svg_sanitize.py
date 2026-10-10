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
    def _sanitize(self, markup):
        """过一遍整份 SVG 的净化器，返回 (产物, notes)。"""
        return S.sanitize_svg_for_inline(markup)

    def test_presentation_attribute_urls_are_neutralized_too(self):
        """呈现属性里的 url() 也是外链入口，不能只管 style 那一处。

        证伪：中和只写在 _clean_style_attr 上，于是 `fill="url(https://…)"`、
        `filter="url(data:…)"` 原样内联——内联后它是同源活 DOM，浏览器照样去取那个
        地址，`file:` 还能触及本机磁盘（产物 HTML 常以 file:// 打开）。这类写法在
        手绘 SVG 里不罕见（渐变/滤镜/marker 都往属性里放），漏了就是白做一层净化。
        内部的 `url(#id)` 必须照旧放行。
        """
        for attr in ("fill", "stroke", "filter", "mask", "clip-path", "marker-start"):
            with self.subTest(attr=attr):
                out, notes = self._sanitize(
                    f'<svg xmlns="{S.SVG_NS}"><rect {attr}="url(https://evil.com/x.png)"/></svg>')
                self.assertNotIn("evil.com", out, attr)
                self.assertIn("url(about:blank)", out, attr)
                self.assertTrue(any("已中和" in n for n in notes), attr)
        out, notes = self._sanitize(
            f'<svg xmlns="{S.SVG_NS}"><defs><linearGradient id="g"/></defs>'
            '<rect fill="url(#g)"/></svg>')
        self.assertIn('url(#g)', out)
        self.assertEqual(notes, [])

    def test_local_file_url_is_neutralized(self):
        """file: 与 http 同档：遍历本机磁盘的外链不该进成片。"""
        out, _ = self._sanitize(
            f'<svg xmlns="{S.SVG_NS}"><rect fill="url(file:///etc/passwd)"/></svg>')
        self.assertNotIn("/etc/passwd", out)

    def test_url_is_neutralized_whole_not_half(self):
        """外链 url() 要整颗换成 about:blank，只吃 scheme 那一截会留下半截 URL。

        证伪：替换串原先缺收尾的 `)`，产物是 `url(about:blank//evil.com/a.png)`
        （实测）——既不是合法 CSS，要拦的地址也还明明白白写在成片里。
        """
        out = _clean(".a{background:url(http://evil.com/a.png)}")
        self.assertNotIn("evil.com", out)
        self.assertIn("url(about:blank)", out)
        self.assertEqual(out.count("url("), out.count(")"), out)


class CssCustomProperties(unittest.TestCase):
    def test_custom_property_named_animation_is_not_a_wallclock(self):
        """CSS 自定义属性不是墙钟动画，不能被拦腰截断。

        证伪：判定原先是没有左边界的 `(animation|transition)(-[a-z]+)?:`，
        `--animation-duration:2s` 里 `animation-duration:` 照样命中，剥完之后留下一个
        孤零零的 `--`，而紧随其后的那条真声明被当成值一并吃掉——实测
        `.a{--animation-duration:2s;fill:red}` 变成 `.a{--fill:red}`，作者写的颜色
        在成片里静默消失。
        """
        out = _clean(".a{--animation-duration:2s;fill:red}")
        self.assertIn("--animation-duration:2s", out)
        self.assertIn("fill:red", out)

    def test_real_animation_is_still_stripped(self):
        """加边界不能把真动画放过：几种常见位置都要照样剥掉。

        行首、 inline style 的第一条、`;` 之后、`{` 之后、以及跨行的写法。
        """
        for css in ("animation:x 2s", "-webkit-animation:x 2s",
                    ".a{animation:x 2s}", ".a{fill:red;transition:all .3s}",
                    ".a{\n  animation:x 2s\n}"):
            with self.subTest(css=css.replace("\n", "\\n")):
                out = _clean(css)
                self.assertNotIn("animation", out.lower(), css)
                self.assertNotIn("transition", out.lower(), css)
