"""题头组入场编排：副标题行的行为锁。

副标题（`#tag-{sid}`）曾经是画面上**唯一一个从头到尾没进过时间线的元素**——
转场、标题、配图、字幕、背景、苹果风开场都各有一拍，唯独它从卡片第一帧就在
那儿、钉到被下一页盖走。这一层补上之后，题头组两行不再一起到位。

这份文件按第一性原理验四件事（不验实现细节）：

1. **错峰** —— 副标题的起点必须晚于标题的起点，且这个"晚多少"由标题时长推出，
   不是第二个可以独立飘走的数字；
2. **幅度小一号** —— 副标题是这一组的第二行，它永远不比标题动得多
   （`ampFrac ∈ [0,1]` 恒成立），且方向与标题同向（两层不打架）；
3. **幅度继承、曲线固定** —— 副标题的幅度与方向继承标题那一档（它属于这一组），
   速度不继承（逐帧实测：跟着 `expo.out` 走它只有 2 帧可见行程）；
4. **不演的页一条都不生成** —— 整页画布 / agenda 卡没有 `#tag-` 这个元素，补间
   指向不存在的元素等于死代码。

外加两条结构锁：短段压缩后错峰关系不变、两次生成逐字节一致。
"""
import contextlib
import io
import re
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from html_renderer import generate_html

# 标题入场是「揭示边」的两层（见 html_renderer._reveal_tween_lines）：
#   裁窗  tl.fromTo("#rev-title-seg-a",{clipPath:"inset(100% 0 0 0)"},
#                   {clipPath:"inset(0% 0 0 0)",duration:0.50,ease:"power4.out"},2.40)
#   字    tl.from("#title-seg-a",{y:44.00,duration:0.50,ease:"power4.out"},2.40)
# 两条同起点、同时长、同缓动——任何一处分家，成片里就是"擦除"或"滑动"，
# 不再是"字被一条边推上来"。所以这里把裁窗也一起匹配，交给
# `RevealWindow` 那一组逐条对账。
_TITLE_RE = re.compile(
    r'tl\.from\("#title-(?P<sid>[A-Za-z0-9_-]+)",\{y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
# tl.from("#tag-seg-a",{y:26.40,duration:0.38,ease:"power2.out"},2.62)
_TAG_RE = re.compile(
    r'tl\.from\("#tag-(?P<sid>[A-Za-z0-9_-]+)",\{y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
# 裁窗那一层：`title` / `tag` 两个 kind 各一条，与上面的字补间一一对应。
_REVEAL_RE = re.compile(
    r'tl\.fromTo\("#rev-(?P<kind>title|tag)-(?P<sid>[A-Za-z0-9_-]+)",'
    r'\{clipPath:"inset\(100% 0 0 0\)"\},\{clipPath:"inset\(0% 0 0 0\)",'
    r'duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')


def _slot_source(n=4):
    """n 段全槽位版式、每段都写了 tagline（够四档轮换走完一轮）。"""
    src = H.sample_source()
    src["segments"] = [
        {"id": f"seg-{c}", "title": f"第{i + 1}件事", "tagline": "要点",
         "text": f"这是第{i + 1}段的第一句。这是第{i + 1}段的第二句，稍微长一点。"}
        for i, c in enumerate("abcd"[:n])
    ]
    return src


def render(aspect="portrait", dur=6.0, gap=0.4, source=None, images=None):
    m = H.make_manifest(source=source or _slot_source(), dur=dur, gap=gap)
    with contextlib.redirect_stderr(io.StringIO()):
        return m, generate_html(m, "audio/combined.wav",
                                images=images or H.sample_images(m),
                                aspect=aspect, theme="dark")


def _pairs(html):
    """按 sid 配成 (标题补间, 副标题补间)；缺任一条的段不返回。"""
    titles = {m.group("sid"): m for m in _TITLE_RE.finditer(html)}
    tags = {m.group("sid"): m for m in _TAG_RE.finditer(html)}
    return [(titles[s], tags[s]) for s in titles if s in tags]


class Exists(unittest.TestCase):
    """写了 tagline 的槽位段就有一条副标题补间；没写就一条都没有。"""

    def test_slot_segments_get_one_each(self):
        _, html = render()
        self.assertEqual([m.group("sid") for m in _TAG_RE.finditer(html)],
                         ["seg-a", "seg-b", "seg-c", "seg-d"])

    def test_segment_without_tagline_has_none(self):
        src = _slot_source(2)
        src["segments"][1].pop("tagline")
        _, html = render(source=src)
        self.assertEqual([m.group("sid") for m in _TAG_RE.finditer(html)], ["seg-a"])

    def test_selector_points_at_a_real_element(self):
        """补间指向的元素必须在 DOM 里——指向不存在的元素是死代码，零报错。"""
        _, html = render()
        for m in _TAG_RE.finditer(html):
            self.assertIn(f'id="tag-{m.group("sid")}"', html)

    def test_selector_points_at_a_real_reveal_window(self):
        """每条字补间都配一条裁窗补间，且裁窗元素真的在 DOM 里。

        起点态的"看不见"由裁窗（`clip-path: inset(100%)`）负责，不再压 `opacity:0`：
        半透明的字在擦除窗里仍会露出半截，而裁窗是真的把它裁掉——这一层比透明度
        更彻底，也让"字从下方被推上来"这件事在起点那一刻成立。
        """
        _, html = render()
        for kind in ("title", "tag"):
            windows = {(m.group("kind"), m.group("sid"))
                       for m in _REVEAL_RE.finditer(html) if m.group("kind") == kind}
            self.assertTrue(windows, f"{kind} 一条裁窗都没有")
            for _, sid in windows:
                self.assertIn(f'id="rev-{kind}-{sid}"', html,
                              f"裁窗 #{kind}-{sid} 的补间指向一个不存在的元素")


class RevealWindow(unittest.TestCase):
    """揭示边：裁窗与字必须同起点、同时长、同缓动，缺一条就是另一种动效。"""

    def _pairs(self, html, kind):
        rx = {"title": _TITLE_RE, "tag": _TAG_RE}[kind]
        movers = {m.group("sid"): m for m in rx.finditer(html)}
        wins = {m.group("sid"): m
                for m in _REVEAL_RE.finditer(html) if m.group("kind") == kind}
        return [(movers[s], wins[s]) for s in movers if s in wins]

    def test_every_title_move_has_a_window(self):
        _, html = render()
        for kind in ("title", "tag"):
            pairs = self._pairs(html, kind)
            movers = {m.group("sid") for m in
                      ((_TITLE_RE if kind == "title" else _TAG_RE).finditer(html))}
            self.assertEqual({p[0].group("sid") for p in pairs}, movers,
                             f"{kind}: 有字在动却没有裁窗＝滑动，不是揭示")

    def test_window_and_text_share_one_beat(self):
        """三条必须相等：at / duration / ease。

        分家的两种后果（都在 `_reveal_tween_lines` 的 docstring 里）：窗比字慢＝
        收尾那几帧字已经站定、窗还在开（画面上什么都没变，只看到一层边界在爬）；
        窗比字快＝字还在走而窗已开满，长标题先露半截。
        """
        _, html = render()
        for kind in ("title", "tag"):
            for mv, win in self._pairs(html, kind):
                sid = mv.group("sid")
                self.assertAlmostEqual(float(win.group("at")),
                                       float(mv.group("at")), places=2,
                                       msg=f"{kind}/{sid}: 裁窗与字不同起点")
                self.assertEqual(win.group("dur"), mv.group("dur"),
                                 f"{kind}/{sid}: 裁窗与字时长不同")
                self.assertEqual(win.group("ease"), mv.group("ease"),
                                 f"{kind}/{sid}: 裁窗与字缓动不同")

    def test_window_is_full_closed_to_full_open(self):
        """窗的行程是"全裁 → 全开"：半开的窗会让字永远缺一角。"""
        _, html = render()
        for m in _REVEAL_RE.finditer(html):
            self.assertIn("inset(100% 0 0 0)", m.group(0))
            self.assertIn("inset(0% 0 0 0)", m.group(0))

    def test_canvas_page_has_no_window(self):
        """整页画布连标题层都不渲染，裁窗更不该有——补间指向不存在的元素是死代码。

        （反面：closing 是 agenda 卡，它**有**标题，所以有裁窗。只钉"整页画布没有"
        而不钉"closing 有"，一刀切删掉全部裁窗也能通过。）
        """
        m = H.make_manifest(source=H.sample_source(), dur=6.0)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect="portrait",
                                 theme="dark")
        sids = {m_.group("sid") for m_ in _REVEAL_RE.finditer(html)}
        self.assertNotIn("seg-b", sids, "整页画布段不该有标题裁窗")
        self.assertIn("closing", sids, "结尾 agenda 卡的标题照旧要有揭示边")

    def test_apple_opening_keeps_its_own_masks(self):
        """开屏走它自己的 `.t-mk` 逐字裁窗，不叠这一层——两套揭示边叠在同一行
        字上，起点会有两道边界在爬。"""
        m = H.make_manifest(source=H.sample_source(), dur=6.0)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect="portrait",
                                 theme="dark")
        self.assertIn("t-mk", html)
        self.assertNotIn('id="rev-title-opening"', html)


class Staggered(unittest.TestCase):
    """错峰：副标题晚于标题，且"晚多少"由标题时长推出。"""

    def test_tagline_starts_after_the_title(self):
        _, html = render()
        pairs = _pairs(html)
        self.assertEqual(len(pairs), 4)
        for t, g in pairs:
            self.assertGreater(float(g.group("at")), float(t.group("at")),
                               f"{t.group('sid')}: 副标题与标题同时起（一起到位）")

    def test_offset_is_derived_from_the_title_duration(self):
        """起点 = 标题起点 + 标题时长 × delayFrac——不是第二个能独立飘走的数。

        副标题的延迟若写成独立秒数，改标题时长时它就悄悄脱队：短段里"副标题比
        标题还早起"，长段里"隔了大半秒才跟上来"。写成标题时长的比例，
        错峰关系在任何段长、任何画幅下都成立。
        """
        frac = _template.load_template()["animation"]["titleEntrance"]["tagline"]["delayFrac"]
        _, html = render()
        for t, g in _pairs(html):
            want = float(t.group("at")) + float(t.group("dur")) * frac
            self.assertAlmostEqual(float(g.group("at")), want, places=2,
                                   msg=f"{t.group('sid')}: 副标题起点不是标题时长×delayFrac")

    def test_two_beats_overlap_rather_than_queue(self):
        """两拍交叠（delayFrac < 1）而不是首尾相接——排成队列是 PPT 的"逐条出现"。

        overlapping action（迪士尼）才有编排感：主线还没落定，附属层已经在跟进。
        """
        frac = _template.load_template()["animation"]["titleEntrance"]["tagline"]["delayFrac"]
        self.assertLess(frac, 1.0)
        self.assertGreater(frac, 0.0)
        _, html = render()
        for t, g in _pairs(html):
            self.assertLess(float(g.group("at")),
                            float(t.group("at")) + float(t.group("dur")),
                            f"{t.group('sid')}: 两拍首尾相接，没有交叠")

    def test_short_segment_keeps_the_order(self):
        """短段吃入场预算压缩（k<1），两层一起缩，错峰关系不变。"""
        _, html = render(dur=0.5)
        for t, g in _pairs(html):
            self.assertGreater(float(g.group("at")), float(t.group("at")))
            self.assertLess(float(g.group("dur")), float(t.group("dur")))


class SmallerAmplitude(unittest.TestCase):
    """幅度小一号：副标题永远不比标题动得多，且同向。"""

    def test_never_moves_more_than_the_title(self):
        for aspect in ("portrait", "landscape"):
            _, html = render(aspect=aspect)
            for t, g in _pairs(html):
                sid = t.group("sid")
                yt, yg = t.group("y"), g.group("y")
                if yt:
                    self.assertLessEqual(abs(float(yg or 0)), abs(float(yt)) + 1e-6,
                                         f"{aspect}/{sid}: 副标题行程超过了标题")

    def test_same_direction_as_the_title(self):
        """符号继承：标题从下方上来，副标题就不能往下沉——两层反向会读成打架。

        与第四轮"纵向一律与容器层同向"同一条理由。
        """
        _, html = render()
        for t, g in _pairs(html):
            yt, yg = t.group("y"), g.group("y")
            if yt and yg:
                self.assertEqual(float(yt) > 0, float(yg) > 0,
                                 f"{t.group('sid')}: 副标题与标题反向")

    def test_amp_above_one_clamps_to_the_title(self):
        """ampFrac 是"占标题幅度的比例"：越界贴住上限，不是让副标题抢戏。

        与运镜 panX/panY 同源——让越界**不可表达**，比生成期钳住并 warn 更可靠
        （钳制还会让模板作者以为自己写的数生效了）。
        """
        tpl = _template.load_template()
        for raw in (1.0, 1.5, 99):
            tpl["animation"]["titleEntrance"]["tagline"]["ampFrac"] = raw
            with mock.patch("html_renderer.load_template", return_value=tpl):
                _, html = render()
            for t, g in _pairs(html):
                yt, yg = t.group("y"), g.group("y")
                if yt:
                    self.assertAlmostEqual(abs(float(yg or 0)), abs(float(yt)), places=1,
                                           msg=f"ampFrac={raw}: 行程应当贴住标题那一档")

    def test_amp_zero_disables_the_layer(self):
        """ampFrac 0 = 明确不要这层（退回改动前的行为：副标题不进时间线）。"""
        tpl = _template.load_template()
        tpl["animation"]["titleEntrance"]["tagline"]["ampFrac"] = 0
        with mock.patch("html_renderer.load_template", return_value=tpl):
            _, html = render()
        self.assertEqual(list(_TAG_RE.finditer(html)), [])


class FixedCurve(unittest.TestCase):
    """副标题的曲线是固定的一条，不跟着标题那档跳。

    幅度与方向继承标题（证明它属于这一组），**速度**不继承——逐帧实测（Chromium，
    30fps 网格）：标题 rhythm 里有 `expo.out`，副标题跟过去之后 0→76% 只用了
    0.08s ≈ 2 帧，"错峰"在成片里读不出来；`back.out(1.7)` 那档还会让小字号冲过
    1 再弹回来。这两档对标题是好的，对"第二行小字"是抖动。
    """

    def test_ease_is_one_fixed_curve(self):
        want = _template.load_template()["animation"]["titleEntrance"]["tagline"]["ease"]
        _, html = render()
        got = {m.group("ease") for m in _TAG_RE.finditer(html)}
        self.assertEqual(got, {want},
                         "副标题的曲线应当只有一条（模板 titleEntrance.tagline.ease）")

    def test_ease_does_not_follow_the_title_rhythm(self):
        """标题四档 ease 在轮换，副标题不跟着换——跟换就会撞上 expo / back 那两档。

        （先断言标题确实在轮换，否则这条锁在 rhythm 被摘掉时是空的。）
        """
        _, html = render()
        self.assertGreater(len({m.group("ease") for m in _TITLE_RE.finditer(html)}), 1,
                           "标题档位应当在轮换——这条断言的前提不成立时锁是空的")
        want = _template.load_template()["animation"]["titleEntrance"]["tagline"]["ease"]
        for t, g in _pairs(html):
            self.assertEqual(g.group("ease"), want,
                             f"{t.group('sid')}: 副标题跟着标题那档换了曲线")


class WhoDoesNotGetOne(unittest.TestCase):
    """不该演的页一条都不生成（DOM 里没有 `#tag-` 这个元素）。"""

    def test_canvas_page_has_none(self):
        """整页画布连标题层都不渲染，副标题更不存在。"""
        m = H.make_manifest(source=H.sample_source(), dur=6.0)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect="portrait",
                                 theme="dark")
        # seg-b 是整页画布；seg-a 是槽位
        self.assertEqual([m_.group("sid") for m_ in _TAG_RE.finditer(html)], ["seg-a"])

    def test_agenda_page_has_none(self):
        """agenda 卡的副标题是 `.agenda-kicker`（画在 `.agenda-head` 里），
        不是 `#tag-{sid}`——给它补间等于指向一个永远不存在的元素。

        开屏的 kicker 由苹果风开场编排逐字演（`_apple_opening_lines`），不走这一层。
        """
        src = dict(H.sample_source(), opening_tagline="AI WEEKLY",
                   closing_tagline="下期预告")
        m = H.make_manifest(source=src, dur=6.0)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect="portrait",
                                 theme="dark")
        got = [m_.group("sid") for m_ in _TAG_RE.finditer(html)]
        self.assertNotIn("opening", got)
        self.assertNotIn("closing", got)

    def test_no_dead_tween_without_the_element(self):
        """每一条副标题补间的 sid，DOM 里都得有对应的 id——笼统地再兜一次。"""
        src = dict(H.sample_source(), opening_tagline="AI WEEKLY")
        m = H.make_manifest(source=src, dur=6.0)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect="portrait",
                                 theme="dark")
        for m_ in _TAG_RE.finditer(html):
            self.assertIn(f'id="tag-{m_.group("sid")}"', html)


class Deterministic(unittest.TestCase):
    """同一份输入两次生成逐字节一致（不能引入随机/墙钟）。"""

    def test_twice_is_identical(self):
        _, a = render()
        _, b = render()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
