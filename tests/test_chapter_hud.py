"""章节读数（`03/04` + 页底刻度排）：编号落在哪张卡、亮度说哪一章、长度说演到哪儿。

这是"观众随时知道自己在第几章"的唯一读数，四件事必须同时对：只有内容段编号、
刻度数与格宽从模板来、亮度（`--tick-on`）说"哪一章"、长度（`--tick-fill`）说
"这一章演到哪儿"。

点亮走**属性补间**而不是 class：GSAP 的 `className` 插件把 `+=on` 原样写进 class
属性（3.14.2 实测），`tl.call` 回调在 `seek()` 下不触发。两条都表现为"实时播放看
着对、逐帧截图全都没有"——所以这里断的是产物长什么样，不是"看着对不对"。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
from html_renderer import generate_html

THREE = {
    "title": "三章",
    "opening": "开场一句。",
    "opening_title": "本期内容",
    "segments": [
        {"id": "c1", "title": "第一章", "text": "第一段的话。"},
        {"id": "c2", "title": "第二章", "text": "第二段的话。"},
        {"id": "c3", "title": "第三章", "text": "第三段的话。"},
    ],
    "closing": "结束。",
    "closing_title": "小结",
}


def render(source=None, **kw):
    m = H.make_manifest(source or THREE)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m), **kw)


def timelines(html):
    """产物里渲染器生成的那段 JS（引用 `tl.` 的那块），runtime.js 不算。"""
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    return "\n".join(b for b in blocks if "tl." in b)


def card(html, sid):
    """某张卡的起始标签（属性区整段）。"""
    m = re.search(r'<div id="%s" class="[^"]*"[^>]*>' % re.escape(sid), html)
    return m.group(0) if m else ""


def tick_block(html, sid="c2"):
    """某张卡起始标签到题头之间的一小段（只夹着 bg / grid / 刻度）。"""
    tail = html.split('id="%s"' % sid, 1)[1]
    return tail[:tail.index("seg-title-wrap")]


class ChapterReadout(unittest.TestCase):
    """编号 + 刻度 + 两条读数的一次性读数：一段到底验完，分条只会重复建同一份产物。"""

    def test_numbered_cards_carry_the_whole_readout(self):
        html = render()
        for i, sid in enumerate(("c1", "c2", "c3"), start=1):
            self.assertIn(f'data-chapter="{i}" data-chapters="3"', card(html, sid))
            self.assertIn(f'<div class="chapter-num" id="cap-{sid}">0{i}/03</div>', html)
            # 编号是题头组的一员：跟标题一起进来、落位后不动（一条 from，不循环）
            self.assertIsNotNone(
                re.search(r'tl\.from\("#cap-%s",\{x:[\d.]+,opacity:0,'
                          r'duration:[\d.]+' % sid, html),
                f"{sid} 的编号没有入场补间")
        self.assertEqual(html.count('tl.from("#cap-c1"'), 1,
                         "编号入场只该有一条补间（落位后不动）")
        # 开屏不参与（封面不编号）；结尾页**挂刻度但不编号**——它把全片刻度亮满
        # 作收束，却没有"我这是第几章"可报（给它编号等于把封面算成一章）。
        self.assertNotIn("data-chapter", card(html, "opening"))
        self.assertNotIn('id="cap-opening"', html)
        self.assertIn('data-chapter="3" data-chapters="3"', card(html, "closing"))
        self.assertNotIn('id="cap-closing"', html)
        # 收束是**逐格**点亮（前 n-1 格 set、末格 fromTo），不是同一帧全亮：
        # "把全片数一遍"要靠先后关系读出来，一次全亮只是"这一页有个满格刻度"。
        close_sets = re.findall(
            r'tl\.set\("#closing \.chapter-tick\[data-ch=\'(\d)\'\]",'
            r'\{"--tick-past":1\},([\d.]+)\)', html)
        self.assertEqual([k for k, _ in close_sets], ["1", "2"],
                         "结尾页应逐格点亮前 n-1 格")
        ats = [float(t) for _, t in close_sets]
        self.assertLess(ats[0], ats[1], "收束是逐格亮，不是同一帧全亮")
        # `off` 只关读数：CSS 骨架仍在，DOM 上一枚编号、一格刻度都不生成
        off = render(dict(THREE, animation={"chapterIndex": {"mode": "off"}}))
        for frag in ('class="chapter-num"', 'class="chapter-tick"', 'data-chapter="'):
            self.assertNotIn(frag, off.split("<script", 1)[0])

        # 刻度数与格宽都从 n 来：n 格等分，留缝走模板（不在 CSS 里写死）
        seg = tick_block(html)
        self.assertEqual(re.findall(r'<i class="chapter-tick" data-ch="(\d)"', seg),
                         ["1", "2", "3"])
        m = re.search(r'data-ch="1" style="left:calc\(0\.0000% \+ ([\d.]+)px\);'
                      r'width:calc\(33\.3333% - ([\d.]+)px\)"', seg)
        self.assertIsNotNone(m, "刻度没有按 n 等分")
        gap, double = float(m.group(1)), float(m.group(2))
        self.assertGreater(gap, 0)
        self.assertAlmostEqual(double, gap * 2, places=3)

        # 亮度说"哪一章"：已过的格子写 `--tick-past`（中性白）、当前格 `--tick-on`
        # 从 0 升到 1（accent）、未来的格子无补间
        for k in (1, 2):
            self.assertIn('tl.set("#c3 .chapter-tick[data-ch=\'%d\']",'
                          '{"--tick-past":1}' % k, html)
        self.assertIn('tl.fromTo("#c3 .chapter-tick[data-ch=\'3\']",{"--tick-on":0},'
                      '{"--tick-on":1', html)
        self.assertNotIn(".chapter-tick[data-ch='4']", html)
        # 点亮只许走属性补间：classList 与 tl.call 在 `seek()` 下都不复现
        for line in timelines(html).splitlines():
            if "chapter-tick" in line:
                self.assertIn('"--tick-', line,
                              f"刻度走了属性之外的通道：{line.strip()[:80]}")

    def test_length_says_how_far_this_chapter_ran(self):
        """当前格的长度 = 本段播放进度（页内进度条在编号卡里被隐掉）。"""
        html = render()
        m = re.search(r'tl\.fromTo\("#c2 \.chapter-tick\[data-ch=\'2\'\]",'
                      r'\{"--tick-fill":0\},\{"--tick-fill":1,duration:([\d.]+),'
                      r'ease:"none"\},\s*([\d.]+)\)', html)
        self.assertIsNotNone(m, "当前格没有播放进度补间")
        dur, at = float(m.group(1)), float(m.group(2))
        head = card(html, "c2")
        start = float(re.search(r'data-start="([\d.]+)"', head).group(1))
        vis = float(re.search(r'data-duration="([\d.]+)"', head).group(1))
        self.assertLessEqual(at, start + vis)   # 起点落在卡的可见窗口之内
        self.assertLess(dur, vis)               # 不含入场那一截
        self.assertGreater(dur, 0)
        self.assertIn(".seg-card[data-chapter]>.seg-progress{display:none}", html)


if __name__ == "__main__":
    unittest.main()
