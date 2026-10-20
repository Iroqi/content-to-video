"""_svg_sanitize 的净化口径：墙钟动画定义、外链 url()、以及满幅底板判据。

判据都落在 <style> 文本上，所以这里直接喂 CSS（_clean_style_text）——内联前的那层
结构化改写另由 test_canvas_kit / test_stage_carry 的端到端覆盖。满幅底板判据是
"SVG 必须透明"那条规矩的唯一真源（references/canvas.md「不铺满幅底」），这里钉住
它认哪些写法、不误报哪些。
"""
import unittest
import xml.etree.ElementTree as ET

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


class ParserEquivalentView(unittest.TestCase):
    """判定层必须看见浏览器看见的那个值——净化与解析的同款预处理。

    三条都实证过绕过（Chromium/FireFox 均执行）：
      - XML 字符引用 &#9;/&#10;/&#13; 解析后落成制表/换行，URL 解析器会抹掉它们，
        scheme 正则不过——`java&#9;script:` 曾是漏网的 javascript:；
      - CSS 转义还原先于一切判定——`\\75rl(https://…)`、`\\68ttp://…` 曾是漏网的
        外链 url()，`\\61nimation:x 2s` 曾是漏网的墙钟声明；
      - CSS 注释在浏览器眼里不存在——`style="fill:red;/*c*/animation:x 1s"` 里
        那条 animation 曾是活的。
    """

    def _sanitize(self, markup):
        return S.sanitize_svg_for_inline(markup)

    def test_href_char_ref_tab_is_still_external(self):
        """字符引用解码出的制表/换行拦不住 scheme 判定：浏览器抹得，判定也要抹得。"""
        for raw in ("java&#9;script:alert(1)", "java&#10;script:alert(1)",
                    "java&#13;script:alert(1)"):
            with self.subTest(raw=raw):
                out, notes = self._sanitize(
                    f'<svg xmlns="{S.SVG_NS}"><a href="{raw}">x</a></svg>')
                self.assertNotIn("href", out)
                self.assertTrue(any("href" in n for n in notes), notes)
        # xlink:href 同一口径（_local 剥命名空间后一样进判定）
        out, _ = self._sanitize(
            f'<svg xmlns="{S.SVG_NS}" xmlns:xlink="{S.XLINK_NS}">'
            '<a xlink:href="java&#9;script:alert(1)">x</a></svg>')
        self.assertNotIn("href", out)
        # 内部片段与相对路径仍放行
        out, notes = self._sanitize(
            f'<svg xmlns="{S.SVG_NS}"><defs><rect id="r"/></defs>'
            '<use href="#r"/></svg>')
        self.assertIn('href="#r"', out)
        self.assertEqual(notes, [])

    def test_css_escape_cannot_hide_a_url(self):
        """转义还原先于 url() 判定：关键字与 scheme 都能被藏，也都要被还原。"""
        bs = chr(92)
        for where, markup in (
            ("style 属性藏 url( 关键字",
             f'<svg xmlns="{S.SVG_NS}"><rect style="fill:url({bs}75rl(https://evil.com/a.png))"/></svg>'),
            ("style 属性藏 scheme",
             f'<svg xmlns="{S.SVG_NS}"><rect style="fill:url({bs}68ttp://evil.com/a.png)"/></svg>'),
            ("<style> 文本藏 scheme",
             f'<svg xmlns="{S.SVG_NS}"><style>.a{{fill:url({bs}68ttp://evil.com/a.png)}}</style></svg>'),
            ("呈现属性藏 scheme",
             f'<svg xmlns="{S.SVG_NS}"><rect fill="url({bs}68ttp://evil.com/a.png)"/></svg>'),
        ):
            with self.subTest(where=where):
                out, notes = self._sanitize(markup)
                self.assertNotIn("evil.com", out)
                self.assertIn("url(about:blank)", out)
                self.assertTrue(any("已中和" in n for n in notes), notes)

    def test_css_escape_cannot_hide_a_wallclock_declaration(self):
        """`\\61nimation:x 2s` 在浏览器眼里就是 animation 声明。"""
        bs = chr(92)
        for where, markup in (
            ("style 属性",
             f'<svg xmlns="{S.SVG_NS}"><rect style="fill:red;{bs}61nimation:x 1s"/></svg>'),
            ("<style> 文本",
             f'<svg xmlns="{S.SVG_NS}"><style>.a{{fill:red;{bs}61nimation:x 1s}}</style></svg>'),
        ):
            with self.subTest(where=where):
                out, notes = self._sanitize(markup)
                self.assertNotIn("animation", out.lower())
                self.assertTrue(any("已剥离" in n for n in notes), notes)

    def test_comment_cannot_hide_a_wallclock_declaration_in_style_attr(self):
        """注释先剥：inline style 里 `/*c*/animation:x 1s` 曾是活的墙钟声明。"""
        out, notes = self._sanitize(
            f'<svg xmlns="{S.SVG_NS}"><rect style="fill:red;/*c*/animation:x 1s"/></svg>')
        self.assertNotIn("animation", out.lower())
        self.assertIn("fill:red", out)
        self.assertTrue(any("已剥离" in n for n in notes), notes)

    def test_comment_cannot_hide_a_url_in_style_text(self):
        """<style> 文本的注释后面同样不许挂外链 url()。"""
        out = _clean("/*c*/.a{fill:red;background:url(https://evil.com/a.png)}")
        self.assertNotIn("evil.com", out)
        self.assertIn("fill:red", out)

    def test_legitimate_escape_is_rewritten_to_what_the_browser_sees(self):
        """还原只发生在判定域：合法转义（`\\2014`）被改写成浏览器眼里的字面量。

        这条钉住"整文本还原"策略的代价：产物 CSS 里不再有转义序列——语义与浏览器
        解析结果一致（content:"—" 就是 content:"\\2014" 的解析结果），不是删除。
        """
        out = _clean('.a{content:"\\2014"}')
        self.assertEqual(out, '.a{content:"—"}')


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


_HEAD = ('<svg xmlns="%s" width="980" height="735" viewBox="0 0 980 735">' % S.SVG_NS)


def _backdrop(body):
    """一段 SVG 主体 → 它里面有没有被判为满幅底板的图元。"""
    root = ET.fromstring(_HEAD + body + "</svg>")
    vbw, vbh = S.root_viewbox_size(root)
    return any(S.is_full_bleed_backdrop(el, vbw, vbh)
               for el in root.iter() if el is not root)


class FullBleedBackdrop(unittest.TestCase):
    """满幅底板判据：三种等价写法都认，没有可见填充/没铺满的都不误报。

    修复前只认 `<rect>`：`<path d="M0 0 H980 V735 H0 Z">` 与
    `<polygon points="0,0 980,0 980,735 0,735">` 实测零 warn 通过，成片里照样
    一张死平底板（canvas.md 明令禁止的那种）。
    """

    def test_three_equivalent_writings_all_count(self):
        for body in ('<rect width="980" height="735" fill="#123"/>',
                     '<rect width="100%" height="100%"/>',
                     '<rect width="100%" height="99%"/>',
                     '<rect width="980" height="735" rx="24"/>',
                     '<g><rect width="980" height="735" fill="#123"/></g>',
                     '<rect width="980" height="735" fill="rebeccapurple"/>',
                     '<rect width="980" height="735" style="fill:#123"/>',
                     '<polygon points="0,0 980,0 980,735 0,735"/>',
                     '<path d="M0 0 H980 V735 H0 Z" fill="#123"/>',
                     '<path d="m0 0 h980 v735 h-980 z" fill="#123"/>',
                     '<path d="M0 0 H980 V735 H0" fill="#123"/>',
                     '<path d="M0 0 980 0 980 735 0 735 Z" fill="#123"/>',
                     '<path d="M0 0 980 0 980 735 0 735" fill="#123"/>'):
            with self.subTest(body=body):
                self.assertTrue(_backdrop(body), body)

    def test_invisible_fill_is_not_a_backdrop(self):
        """fill:none / opacity:0 没有可见填充：透明的满幅 rect 无害。"""
        for body in ('<rect width="980" height="735" fill="none"/>',
                     '<rect width="980" height="735" opacity="0"/>',
                     '<rect width="980" height="735" style="fill:none"/>',
                     '<path d="M0 0 H980 V735 H0 Z" fill="none"/>'):
            with self.subTest(body=body):
                self.assertFalse(_backdrop(body), body)

    def test_not_full_bleed_is_not_a_backdrop(self):
        for body in ('<rect width="980" height="367"/>',
                     '<rect width="950" height="735"/>',
                     '<rect width="50%" height="50%"/>',
                     '<path d="M0 0 L980 735"/>',
                     '<path d="M0 0 H490 V367 H0 Z"/>',
                     '<path d="M0 0 H980 V367 H0 Z"/>',
                     '<polygon points="0,0 490,0 490,367 0,367"/>',
                     '<text x="10" y="20">hi</text>'):
            with self.subTest(body=body):
                self.assertFalse(_backdrop(body), body)

    def test_full_bleed_is_judged_in_viewbox_units(self):
        """缩放写法（width/height 与 viewBox 差一个倍数）下铺满的底板照样认得出。"""
        root = ET.fromstring(
            '<svg xmlns="%s" width="800" height="600" viewBox="0 0 400 300">'
            '<rect width="400" height="300"/></svg>' % S.SVG_NS)
        vbw, vbh = S.root_viewbox_size(root)
        el = next(e for e in root.iter() if e is not root)
        self.assertTrue(S.is_full_bleed_backdrop(el, vbw, vbh))

    def test_multi_subpath_backdrop_is_not_halved(self):
        """多子路径 path：隐式闭合补的是**最后一条子路径**自己的起点。

        修复前按 segs[0]（第一条子路径的起点）补边，`M…Z M…Z` 这种每个子路径
        都已闭合的写法会凭空多出一条贯穿画面的斜边，even-odd 的奇偶被它翻转：
        真正铺满的多边形实测只算 ~0.50，底板判据**静默失效**（安全方向的判据
        出错不报错，只是不再拦）。
        """
        for body in ('<path d="M0 0 H980 V735 H0 Z M900 700 h20 v20 h-20 Z"/>',
                     '<path d="M0 0 H980 V360 H0 Z M0 375 H980 V735 H0 Z"/>',
                     # 最后一条子路径未闭合也要照样认（浏览器补的就是回它自己起点）
                     '<path d="M0 0 H980 V360 H0 Z M0 375 H980 V735 H0"/>'):
            with self.subTest(body=body):
                self.assertTrue(_backdrop(body), body)

    def test_inner_subpath_hole_is_still_not_a_backdrop(self):
        """两条同向子路径按 even-odd 是「挖空」，跟子路径是否闭合是两件事。

        这一条不是上面那个修复的回归护栏，而是防止它被改过头：把「如今认得出
        多子路径」顺势改成「多子路径一律算铺满」，这张 4% 挖空的图就会被放行。
        """
        self.assertFalse(_backdrop('<path d="M0 0 H980 V735 H0 Z M400 300 h200 v150 h-200"/>'))

    def test_malformed_d_is_not_a_backdrop(self):
        for d in ("M0 0 L", "M0 0 Hx", "garbage"):
            with self.subTest(d=d):
                self.assertFalse(_backdrop('<path d="%s"/>' % d))
