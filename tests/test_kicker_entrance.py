"""题头组第一行（agenda kicker）的入场与它的两个结构约束。

第五轮把副标题补进了时间线，但 `agenda-kicker`（开屏 / 结尾那行"AI WEEKLY"式的
小标题）只活在苹果风开场里——非开屏的 agenda 卡（典型是结尾）里它没有 id、没有
补间，从卡片第一帧就站在那儿。这是同一个毛病在另一种版式上的那一半。

这份文件按第一性原理验五件事（不验实现细节）：

1. **进时间线了** —— 结尾页的 kicker 有一条补间，起点晚于标题（错峰交叠）；
2. **落位是精确的** —— `fromTo` 收到 `x:0`，不是 `from`（`from` 的起点若被
   半像素吸附，落位会留残值，小标题比别的行"糊一点"）；
3. **开屏不重复演** —— 开屏那行由苹果编排逐字演，不能再挂整行补间（两套补间
   互相覆盖，且它会在擦除窗里先整行亮一下）；
4. **行宽不变** —— 起点态比落位态宽 `(n−1)×span`，容器是 `overflow:hidden`，
   所以每一行 kicker 都要带字距垫（`data-kern` + `--ctv-ag-kern-pad` 恒等）；
5. **不演的页一条都不生成** —— 没有 tagline 的卡、整页画布、槽位内容段都没有。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from html_renderer import generate_html

# tl.fromTo("#kicker-closing",{opacity:0,x:2.016},{opacity:1,x:0,duration:0.45,ease:"power2.out"},32.15)
_KICKER_RE = re.compile(
    r'tl\.fromTo\("#kicker-(?P<sid>[A-Za-z0-9_-]+)",\{opacity:0,x:(?P<x>[\d.]+)\},'
    r'\{opacity:1,x:0,duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.0-9()]+)"\},'
    r'(?P<at>[\d.]+)\)')
# 兜底形态（没有字距可展开时只淡入）：tl.from("#kicker-closing",{opacity:0,...}
_KICKER_FADE_RE = re.compile(
    r'tl\.from\("#kicker-(?P<sid>[A-Za-z0-9_-]+)",\{opacity:0,duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
# 标题入场改走揭示边之后只剩位移（没有 scale），见 test_tagline_entrance 的同名正则。
_TITLE_RE = re.compile(
    r'tl\.from\("#title-(?P<sid>[A-Za-z0-9_-]+)",\{y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')


def _source(**kw):
    return dict(H.sample_source(), opening_tagline="AI WEEKLY",
                closing_tagline="下期预告", **kw)


def render(aspect="portrait", dur=6.0, source=None):
    m = H.make_manifest(source=source or _source(), dur=dur)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m), aspect=aspect, theme="dark")


def _kick(sid, html):
    """某一页 kicker 的补间（fromTo 优先，退回纯淡入）；没有则 None。"""
    for m in _KICKER_RE.finditer(html):
        if m.group("sid") == sid:
            return m
    for m in _KICKER_FADE_RE.finditer(html):
        if m.group("sid") == sid:
            return m
    return None


class EntersTheTimeline(unittest.TestCase):
    """结尾页的 kicker 进时间线了，并且跟着标题同一拍。"""

    def test_closing_kicker_has_a_tween(self):
        for aspect in ("portrait", "landscape"):
            html = render(aspect=aspect)
            m = _kick("closing", html)
            self.assertIsNotNone(m, f"{aspect}: 结尾 kicker 没有任何补间（没进时间线）")

    def test_tween_points_at_a_real_element(self):
        html = render()
        for m in list(_KICKER_RE.finditer(html)) + list(_KICKER_FADE_RE.finditer(html)):
            self.assertIn(f'id="kicker-{m.group("sid")}"', html,
                          f"{m.group('sid')}: 补间指向的元素在 DOM 里不存在")

    def test_starts_after_the_title_and_overlaps_it(self):
        """先标题、后 kicker，两拍交叠——排成队列是 PPT 的"逐条出现"。"""
        html = render()
        titles = {m.group("sid"): m for m in _TITLE_RE.finditer(html)}
        m = _kick("closing", html)
        t = titles["closing"]
        self.assertGreater(float(m.group("at")), float(t.group("at")))
        self.assertLess(float(m.group("at")), float(t.group("at")) + float(t.group("dur")))

    def test_offset_is_derived_from_the_title(self):
        """起点 = 标题起点 + 标题时长 × kicker.delayFrac，不是第二个独立的数。"""
        frac = _template.load_template()["animation"]["titleEntrance"]["kicker"]["delayFrac"]
        html = render()
        t = {m.group("sid"): m for m in _TITLE_RE.finditer(html)}["closing"]
        m = _kick("closing", html)
        self.assertAlmostEqual(float(m.group("at")),
                               float(t.group("at")) + float(t.group("dur")) * frac,
                               places=2)

    def test_ease_is_the_one_template_curve(self):
        want = _template.load_template()["animation"]["titleEntrance"]["kicker"]["ease"]
        html = render()
        self.assertEqual(_kick("closing", html).group("ease"), want)


class LandsExactly(unittest.TestCase):
    """落位必须是精确的 `x:0`（`fromTo`），起点位移是展开量的比例。"""

    def test_uses_from_to_with_exact_landing(self):
        html = render()
        self.assertIsNotNone(_kick("closing", html), "应当走 fromTo（精确落位）形态")
        self.assertIn('{opacity:1,x:0,', _kick("closing", html).group(0))

    def test_offset_is_a_fraction_of_the_tracking_span(self):
        """起点位移 = span × easeFrac，span = (tracking−rest) × kickerSize。

        写成比例而不是绝对 px：kicker 字号随画幅变（24 / 26），写死一个 px 数
        换画幅就不是"同一种展开量"了——与副标题的 ampFrac 同源。
        """
        tpl = _template.load_template()
        hint = tpl["animation"]["titleEntrance"]["kicker"]
        apple = tpl["animation"]["opening"]["apple"]["kicker"]
        size = tpl["layout"]["vertical"]["agenda"]["kickerSize"]
        span = size * (apple["tracking"] - apple["rest"])
        html = render()
        self.assertAlmostEqual(float(_kick("closing", html).group("x")),
                               round(span * hint["easeFrac"], 3), places=2)

    def test_zero_frac_turns_the_layer_off(self):
        tpl = _template.load_template()
        tpl["animation"]["titleEntrance"]["kicker"]["easeFrac"] = 0
        from unittest import mock
        with mock.patch("html_renderer.load_template", return_value=tpl):
            html = render()
        self.assertIsNone(_kick("closing", html))


class OpeningIsNotDoubled(unittest.TestCase):
    """开屏那行由苹果编排逐字演，不能再挂整行补间。"""

    def test_opening_kicker_has_no_row_tween(self):
        html = render()
        self.assertIsNone(_kick("opening", html),
                          "开屏 kicker 被两套补间同时驱动（整行 + 逐字）")
        # 苹果编排本身还在（逐字 x 那一条）
        self.assertIn('#opening .k-ch",{x:(i)=>i*', html)


class RowWidthIsStable(unittest.TestCase):
    """字距展开期间行宽不变：每一行 kicker 都带字距垫，垫宽与单字展开量恒等。"""

    def test_every_kicker_is_padded(self):
        html = render()
        # 开屏那行先挂 id 再挂垫（属性顺序由渲染器决定），只数 data 标记。
        self.assertEqual(html.count('data-kern="1"'), 2,
                         "开屏与结尾两行 kicker 都应当带字距垫")

    def test_pad_equals_the_per_char_span(self):
        tpl = _template.load_template()
        apple = tpl["animation"]["opening"]["apple"]["kicker"]
        for aspect, block in (("portrait", "vertical"), ("landscape", "landscape")):
            size = tpl["layout"][block]["agenda"]["kickerSize"]
            html = render(aspect=aspect)
            self.assertIn(
                f"--ctv-ag-kern-pad:{size * (apple['tracking'] - apple['rest']):.2f}px;",
                html, f"{aspect}: 垫宽与单字展开量不一致")

    def test_css_consumes_the_token(self):
        """CSS 里必须真的读那个令牌——算对了但不挂，等于没有。"""
        with open(H.SKILL_DIR + "/templates/composition.css", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".agenda-kicker[data-kern]{padding-right:var(--ctv-ag-kern-pad)}", css)


class PagesThatDoNotPlay(unittest.TestCase):
    """不该演的页一条都不生成。"""

    def test_closing_without_tagline(self):
        src = _source()
        src.pop("closing_tagline")
        html = render(source=src)
        self.assertIsNone(_kick("closing", html))
        self.assertNotIn('id="kicker-closing"', html)

    def test_slot_segments_use_the_tagline_layer_instead(self):
        """内容段的副标题是 `.tagline`（`#tag-{sid}`），不是 kicker。"""
        html = render()
        self.assertNotIn('id="kicker-seg-a"', html)

    def test_canvas_closing_has_no_kicker(self):
        html = render(source=_source(closing_layout="canvas"))
        self.assertIsNone(_kick("closing", html))
        self.assertNotIn('id="kicker-closing"', html)


class Deterministic(unittest.TestCase):
    def test_twice_is_identical(self):
        self.assertEqual(render(), render())


if __name__ == "__main__":
    unittest.main()
