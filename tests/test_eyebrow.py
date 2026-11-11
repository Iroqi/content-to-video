"""题头上沿的小字行（eyebrow）：把「小 → 大 → 正文」的读序钉死。

代码绘制的 motion graphics 的题头几乎没有例外地是三层的：一行小号宽字距的分类标签、
一行大字、然后才是正文。缺了最上面那一级，观众第一眼就撞上画面里最大的字——
大字与正文之间没有过渡，读起来是"一页一页的海报"，而不是一条连续的叙述。

这一层与 tagline 不是同一件事：tagline 是**这一页的副标题**（跟在标题下面，
与标题同属一句），kicker 是**这一页属于哪一类**（标题之外的语境）。两者可以
同时在场，读序是 分类 → 主句 → 注脚。

验四件事（结构，不验像素）：

1. **三层真的排成三层** —— DOM 里小字行在标题组之前，字号严格小于标题；
2. **它进时间线，且比标题先站定** —— 同起点、更短的时长（读序由时长说，
   不改标题既有的节奏）；
3. **只有内容段有** —— 画布页没有题头层，agenda 卡那行是 `.agenda-kicker`，
   两行都画会在开屏上叠出两条小字；
4. **超长当场拒稿** —— 那一行 nowrap + overflow:hidden，超出的字没有省略号、
   直接消失（标签补一个"…"会被读成"还有下文"）。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from _source_schema import validate_segments_source
from html_renderer import generate_html

_EYEBROW_TEXT_RE = re.compile(
    r'tl\.from\("#kick-(?P<sid>[A-Za-z0-9_-]+) \.kick-text",\{opacity:0,y:(?P<y>[\d.]+),'
    r'duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
_EYEBROW_RULE_RE = re.compile(
    r'tl\.from\("#kick-(?P<sid>[A-Za-z0-9_-]+) \.kick-rule",\{scaleX:0,'
    r'duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
_TITLE_RE = re.compile(
    r'tl\.from\("#title-(?P<sid>[A-Za-z0-9_-]+)",\{y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),ease:"[a-z.0-9()]+"\},(?P<at>[\d.]+)\)')


def _source(kicker="第一章 · 起跑线"):
    src = H.sample_source()
    if kicker is None:
        src["segments"][0].pop("kicker", None)
    else:
        src["segments"][0]["kicker"] = kicker
    return src


def render(aspect="portrait", source=None):
    m = H.make_manifest(source=source or _source(), dur=6.0)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m), aspect=aspect, theme="dark")


class ThreeTierReadOrder(unittest.TestCase):
    """小字在标题之前、字号比标题小——读序不是靠颜色猜的。"""

    def test_kicker_sits_above_the_title_and_is_smaller(self):
        html = render()
        i_kick = html.index('class="seg-kicker" id="kick-seg-a"')
        i_wrap = html.index('<div class="seg-title-wrap">')
        self.assertLess(i_kick, i_wrap, "小字行必须排在标题组之前")
        # 字号梯度读模板，不读产物（产物的字号可能被标题长度守卫降过档，
        # 拿它当基准会让这条断言随稿件长度漂）
        tpl = _template.load_template()
        vert = tpl["layout"]["vertical"]
        self.assertLess(vert["title"]["kicker"]["fontSize"],
                        vert["title"]["fontSize"])
        land = tpl["layout"]["landscape"]
        self.assertLess(land["title"]["kicker"]["fontSize"],
                        land["title"]["fontSize"])

    def test_numbers_lift_to_the_same_headline(self):
        """有 kicker 的页，右上角 `01/04` 抬到同一条抬头线（否则页顶读成斜的）。"""
        html = render()
        self.assertIn('data-kicker="1"', html)
        with open(H.SKILL_DIR + "/scripts/_assets/composition.css", encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".seg-card[data-kicker] .chapter-num{top:var(--ctv-kick-top)", css)


class EntersBeforeTheTitle(unittest.TestCase):
    """同起点、更短时长：屏幕上是"小字先落定，大字还在往下走"。"""

    def test_enters_on_the_title_beat_but_lands_first(self):
        """两画幅都进时间线，且与标题同起点、时长更短。

        同起点是硬约束：`card.s` 已是这一页的第一帧，再往前会落进上一页的
        擦除窗口（两条补间叠在一起读成抖动）。"小字先站定"只能靠**时长**说。
        小字若与标题同长，两者一起到位就是"一块东西浮上来"，读序随之消失。
        """
        hint = _template.load_template()["animation"]["titleEntrance"]["eyebrow"]
        for aspect in ("portrait", "landscape"):
            page = render(aspect=aspect)
            texts = [m for m in _EYEBROW_TEXT_RE.finditer(page) if m.group("sid") == "seg-a"]
            rules = [m for m in _EYEBROW_RULE_RE.finditer(page) if m.group("sid") == "seg-a"]
            self.assertTrue(texts, f"{aspect}: 小字没有进时间线")
            self.assertTrue(rules, f"{aspect}: 引线没有进时间线（不会从左侧长出来）")
            t = {m.group("sid"): m for m in _TITLE_RE.finditer(page)}["seg-a"]
            k = texts[0]
            self.assertAlmostEqual(float(k.group("at")), float(t.group("at")), places=2,
                                   msg=f"{aspect}: 小字与标题不同起点")
            self.assertLess(float(k.group("dur")), float(t.group("dur")),
                            f"{aspect}: 小字与标题同时到位，读序就没有了")
            # 位移是模板里的绝对像素：小字号经不起大行程，这一档不走标题的
            # 比例档（那会随标题四档行程在 13–25px 间跳）。
            self.assertAlmostEqual(float(k.group("y")), float(hint["rise"]), places=2)


class OnlyContentPages(unittest.TestCase):
    """画布页没有题头层；agenda 卡那行另有其人。"""

    def test_canvas_page_has_none(self):
        # 夹具里 seg-b 是整页画布；给它写 kicker 也不该画（标题层整条不生成）
        src = _source()
        src["segments"][1]["kicker"] = "不该出现的标签"
        html = render(source=src)
        self.assertNotIn('id="kick-seg-b"', html)

    def test_agenda_cards_and_fieldless_segments_have_none(self):
        """开屏/结尾那行是 `.agenda-kicker`（内容与版式都不同），不重复画。"""
        html = render()
        self.assertNotIn('class="seg-kicker" id="kick-opening"', html)
        self.assertNotIn('class="seg-kicker" id="kick-closing"', html)
        # 没写字段的稿子：一行都不生成，也不挂 data-kicker（编号留在原位置）
        plain = render(source=_source(kicker=None))
        self.assertNotIn('class="seg-kicker"', plain)
        self.assertNotIn('data-kicker="1"', plain)


class TooLongIsRejected(unittest.TestCase):
    """nowrap + overflow:hidden：超长的字直接消失，所以必须在契约层拒。"""

    def test_over_limit_and_newlines_are_both_rejected(self):
        for bad in ("这是一个远远超过十八个字上限的分类标签用来测试拒稿",
                    "第一行\n第二行"):
            with self.assertRaises(ValueError, msg=bad) as ctx:
                validate_segments_source(_source(kicker=bad))
            self.assertIn("kicker", str(ctx.exception))


class Deterministic(unittest.TestCase):
    def test_twice_is_identical(self):
        self.assertEqual(render(), render())
