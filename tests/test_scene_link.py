"""页界「场景相连」的行为锁。

前沿 MG 的页界不是"翻页"——旧的一边退场，新的里边**已经在演**：两个场景在
同一段时间里交叠，观众的视线始终有主体可看。

按第一性原理验三件事：

1. **不留主体真空** —— 新页的画布带被揭盖擦开的那一刻，里面必须有主体。
   此前配图容器的入场补间带 `opacity:0` 门控，等于让画面在揭盖**之后**再出现
   一次；页界处于是留下一段"新页已露、主体还没到"的真空（实测 0.46~0.70s，
   像翻过一页空纸）。现在画面在场交给页面自己的 clip-path 揭盖承担。
2. **交叠而非接力** —— 旧页退场与新页揭盖锚在同一个 `win_start`：一个场景
   还在走，另一个已经在演，而不是一个演完另一个才上。
3. **落位仍在读序之后** —— 拆掉 presence 门控不等于放开编排顺序：画面虽然随
   揭盖就在场，但"框落到槽位"这件事仍必须排在题头组之后（见
   `tests/test_motion_design.py` 的 `EntranceLadder`）。

第 1 条是这一层真正的判据；第 2、3 条是它不能被实现歪掉的护栏。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
from html_renderer import generate_html

# 页面揭盖：tl.fromTo("#seg-b",{clipPath:"inset(100% 0 0 0)"},
#                    {clipPath:"inset(0% 0 0 0)",duration:0.40,ease:"…"},6.40)
# 奇数页写的是连字符那一键（`"clip-path"`），两条都要认。
_REVEAL_RE = re.compile(
    r'tl\.fromTo\("#(?P<sid>[A-Za-z0-9_-]+)",\{(?:clipPath|"clip-path")'
    r':"inset\(100% 0 0 0\)"\},\{"?clip-?[Pp]ath"?:"inset\(0% 0 0 0\)",'
    r'duration:(?P<dur>[\d.]+),ease:"[^"]+"\},(?P<at>[\d.]+)\)')
# 旧页退场（line 档的 peel）：tl.to("#seg-a",{y:-432.0,rotation:-4,…},6.40)
_EXIT_RE = re.compile(
    r'tl\.to\("#(?P<sid>[A-Za-z0-9_-]+)",\{y:-[\d.]+,rotation:-?[\d.]+,'
    r'rotationX:[\d.]+,transformOrigin:"50% 100%",'
    r'transformPerspective:[\d.]+,duration:(?P<dur>[\d.]+),'
    r'ease:"[^"]+"\},(?P<at>[\d.]+)\)')
# 配图容器落位：tl.from("#img-seg-a",{y:40,duration:0.33,ease:"…"},3.71)
# （compare 版式写 `#cmp-…`）
_FRAME_RE = re.compile(
    r'tl\.from\("#(?P<sel>img|cmp)-(?P<sid>[A-Za-z0-9_-]+)",\{(?P<vars>[^}]*)\},'
    r'(?P<at>[\d.]+)\)')
# 副标题入场：tl.fromTo("#rev-tag-seg-a",{clipPath:…},…,3.57)
_TAG_RE = re.compile(r'tl\.fromTo\("#rev-tag-(?P<sid>[A-Za-z0-9_-]+)"')


def _source():
    """三段全槽位版式（够看出页界交叠）。"""
    src = H.sample_source()
    src["segments"] = [
        {"id": f"seg-{c}", "title": f"第{i + 1}件事", "tagline": "要点",
         "text": f"这是第{i + 1}段的第一句。这是第{i + 1}段的第二句，稍微长一点。"}
        for i, c in enumerate("abc")
    ]
    return src


def render():
    m = H.make_manifest(source=_source(), dur=6.0, gap=0.4)
    with contextlib.redirect_stderr(io.StringIO()):
        html = generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m),
                             aspect="portrait", theme="dark")
    return html


class SubjectNeverVacuum(unittest.TestCase):
    """页界处画布带不许出现"新页已露、主体还没到"的空槽。"""

    def test_presence_is_carried_by_the_reveal_not_by_the_entrance(self):
        """配图容器的落位补间不得带 opacity 门控，且落位不早于揭盖。

        门控 = "画面要等这条补间才出现"。而这条补间的起点在题头组之后
        （读序红线），于是揭盖已经擦开画布带、画面却还没到——空槽就是这么来的。
        画面在场由页面自己的揭盖承担，落位只管"框从下方走到位"。

        两条一起量：揭盖之前画面不许抢跑（页面还没擦开就浮出海报 = 抢读序），
        揭盖之后画面必须已经在场（这才是"场景相连"）。
        """
        html = render()
        gated = [m.group("sid") for m in _FRAME_RE.finditer(html)
                 if "opacity" in m.group("vars")]
        self.assertEqual(
            gated, [],
            "配图落位补间又带上了 opacity 门控：页界处会重新出现主体真空"
            "（画面在场应交给页面揭盖，见 references/motion.md「场景相连」）")

        reveals = {m.group("sid"): float(m.group("at"))
                   for m in _REVEAL_RE.finditer(html)}
        self.assertTrue(reveals, "没找到任何页面揭盖补间：判据失效了")
        frames = {m.group("sid"): float(m.group("at"))
                  for m in _FRAME_RE.finditer(html)}
        self.assertTrue(frames, "没找到任何配图落位补间：判据失效了")
        for sid, at in frames.items():
            self.assertGreaterEqual(
                at, reveals.get(sid, 0.0) - 1e-6,
                f"{sid}: 画面在页面揭盖之前就落位了（{at} < {reveals.get(sid)}）")


class ScenesOverlap(unittest.TestCase):
    """旧页退场与新页揭盖锚在同一点：交叠，不是接力。"""

    def test_exit_and_reveal_share_one_anchor(self):
        """每一次旧页退场，都必须与**另一页**的揭盖锚在同一点。

        退场与揭盖是同一次交接的两半：锚点分开就意味着两个场景没交叠——
        旧的一边演完走了，新的那边才开演，中间那段是空的。
        """
        html = render()
        reveals = [(m.group("sid"), float(m.group("at")))
                   for m in _REVEAL_RE.finditer(html)]
        exits = [(m.group("sid"), float(m.group("at")))
                 for m in _EXIT_RE.finditer(html)]
        self.assertTrue(reveals, "没找到页面揭盖补间：判据失效了")
        self.assertTrue(exits, "没找到旧页退场补间：判据失效了")
        for sid, at in exits:
            partners = [r for r, t in reveals if r != sid and abs(t - at) < 1e-6]
            self.assertTrue(
                partners,
                f"{sid} 在 {at}s 退场，但没有另一页在同一时刻揭盖："
                f"两个场景没有交叠，中间会留下一段空档")


class LandingStillFollowsReadingOrder(unittest.TestCase):
    """拆掉 presence 门控不等于放开编排顺序。"""

    def test_picture_lands_after_the_tagline(self):
        html = render()
        tags = {m.group("sid"): html.index(m.group(0))
                for m in _TAG_RE.finditer(html)}
        frames = [(m.group("sid"), float(m.group("at")))
                  for m in _FRAME_RE.finditer(html)]
        self.assertTrue(frames, "没找到配图落位补间：判据失效了")
        for sid, at in frames:
            self.assertIn(sid, tags, f"{sid}: 找不到它的副标题入场补间")
            tag_at = float(re.search(
                r'tl\.fromTo\("#rev-tag-' + re.escape(sid) +
                r'".*?,([\d.]+)\)', html[tags[sid]:], re.S).group(1))
            self.assertGreater(
                at, tag_at,
                f"{sid}: 配图落位（{at}）抢在副标题（{tag_at}）前面，"
                f"读序红线被拆开了")
