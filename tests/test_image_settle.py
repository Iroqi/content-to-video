"""素材入场「收拢落位」的行为锁。

容器层（框）与内容层（图本体）分开演之后，出现了一条新的越界通道：内容层一旦
离开 `scale:1`，任何 x/y 都可能让 cover 的图露出槽位背景——正是持续运镜那条
"位移必须落在推近让出来的盈余里"的同一条线，只是发生在入场那 0.8 秒里。

所以这份文件按第一性原理验三件事（不验实现细节）：

1. **不露底** —— |x| ≤ (scale−1)·槽位宽/2、|y| ≤ (scale−1)·槽位高/2，两个画幅
   各按自己的槽位盒量一遍；
2. **终点恒等于运镜起点** —— 收拢结束的那一刻必须正好是持续运镜开始的那一刻。
   早了是"收完了干等"，晚了是"还在收、运镜已经开始推"，两者在成片里都是速度突变；
3. **补间顺序** —— 收拢的 fromTo 必须排在运镜末尾那条钉住补间**之前**，让钉住
   补间的终值（scale:1,x:0,y:0）最后落地，否则 GSAP 建时间线时的预渲染会把
   `scale:1.06` 留在元素上（"切页第一秒图比后面大一圈"）。

外加两条结构锁：轮换档位连续四段不重样、不演的载体（video / 整页画布 / 接续页）
一条补间都不生成。
"""
import contextlib
import io
import math
import re
import unittest
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from html_renderer import generate_html, _unit_frac

# tl.fromTo("#img-seg-a > img",{scale:1.06,x:0,y:13.23},
#           {scale:1,x:0,y:0,duration:0.60,ease:"power3.out"},2.80)
# 终点恒为 {scale:1,x:0,y:0}（= 运镜起点），所以只收起点三个数与时刻/时长。
_SETTLE_RE = re.compile(
    r'tl\.fromTo\("#img-(?P<sid>[A-Za-z0-9_-]+) > img",'
    r'\{scale:(?P<scale>-?[\d.]+),x:(?P<x>-?[\d.]+),y:(?P<y>-?[\d.]+)\},'
    r'\{scale:1,x:0,y:0,duration:(?P<dur>[\d.]+),'
    r'ease:"(?P<ease>[a-z.0-9()]+)"\},(?P<at>[\d.]+)\)')
# 持续运镜（含钉住补间）：起点恒为 {1,0,0} 的那一批。
# **起点 scale 必须收进分组**：收拢补间的形状与它同型（都是
# `{scale,x,y}→{scale,x,y,duration,ease}`），只按 dur 过滤的话会把收拢自己算成
# 运镜，"收拢终点 == 运镜起点"就退化成"自己等于自己"的恒真断言。
_DRIFT_RE = re.compile(
    r'tl\.fromTo\("#img-(?P<sid>[A-Za-z0-9_-]+) > img",'
    r'\{scale:(?P<from>-?[\d.]+),x:-?[\d.]+,y:-?[\d.]+\},'
    r'\{scale:(?P<scale>-?[\d.]+),x:(?P<x>-?[\d.]+),y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.]+)"\},(?P<at>[\d.]+)\)')
# 容器层入场：tl.from("#img-seg-a",{opacity:0,y:40,duration:0.44,ease:"…"},2.60)
_FRAME_RE = re.compile(
    r'tl\.from\("#img-(?P<sid>[A-Za-z0-9_-]+)",\{opacity:0,y:-?[\d.]+,'
    r'duration:(?P<dur>[\d.]+),ease:"[^"]+"\},(?P<at>[\d.]+)\)')
_PIN_MAX_DUR = 0.02


def _four_slot_source():
    """四段全槽位版式的稿件（够四档轮换走完一轮）。"""
    src = H.sample_source()
    src["segments"] = [
        {"id": f"seg-{c}", "title": f"第{i + 1}件事", "tagline": "要点",
         "text": f"这是第{i + 1}段的第一句。这是第{i + 1}段的第二句，稍微长一点。"}
        for i, c in enumerate("abcd")
    ]
    return src


def render(aspect="portrait", dur=6.0, gap=0.4, source=None, images=None):
    m = H.make_manifest(source=source or _four_slot_source(), dur=dur, gap=gap)
    with contextlib.redirect_stderr(io.StringIO()):
        return m, generate_html(m, "audio/combined.wav",
                                images=images or H.sample_images(m),
                                aspect=aspect, theme="dark")


def settles(html):
    """html 里所有**真收拢**补间（滤掉运镜末尾那条恒等的钉住补间）。

    钉住补间形状与收拢一致（起点终点都是 {scale:1,x:0,y:0}），不按 dur 滤掉的话
    "哪几段有收拢"会每条多算一次，而"不演的载体不该有收拢"这类断言直接假阴性。
    """
    return [h for h in _SETTLE_RE.finditer(html) if float(h.group('dur')) > _PIN_MAX_DUR]


def drift_hits(html, sid=None):
    """真运镜补间（含钉住补间），滤掉形状同型的收拢补间——判据见 _DRIFT_RE。"""
    return [h for h in _DRIFT_RE.finditer(html)
            if (sid is None or h.group("sid") == sid)
            and abs(float(h.group("from")) - 1.0) < 1e-9]


class UnitFrac(unittest.TestCase):
    """比例钳制：所有"占某个上限的比例"共用一份（上限随画幅与段长变）。"""

    def test_clamps_to_unit_interval(self):
        self.assertEqual(_unit_frac(0.6), 0.6)
        self.assertEqual(_unit_frac(0), 0.0)
        self.assertEqual(_unit_frac(1), 1.0)
        self.assertEqual(_unit_frac(3.5), 1.0)    # 越过上限 = 贴住边沿，不是露底
        self.assertEqual(_unit_frac(-3.5), -1.0)
        self.assertEqual(_unit_frac(None), 0.0)
        self.assertEqual(_unit_frac("x"), 0.0)
        self.assertEqual(_unit_frac(float("nan")), 0.0)

    def test_keeps_the_sign(self):
        """符号即方向：收拢的档位表里 `-0.6` 就是"从左侧来"。

        早先这一层取了 abs，结果四档里写负号的那档被压成与正号同向——轮换只剩
        三种来向，"破除模板感"打折。所以符号必须活到生成端。
        """
        self.assertEqual(_unit_frac(-0.7), -0.7)
        self.assertLess(_unit_frac(-0.7), 0)


class NoBareEdge(unittest.TestCase):
    """不露底：位移必须落在放大让出来的盈余里。两个画幅各量一遍。"""

    def test_offsets_stay_inside_the_slack(self):
        for aspect in ("portrait", "landscape"):
            for s in settles(render(aspect=aspect)[1]):
                bw, bh = _template.get_image_box(aspect)
                span = float(s.group("scale")) - 1.0
                self.assertGreater(span, 0, f"{aspect}: 收拢必须放大，否则必然露底")
                self.assertLessEqual(
                    abs(float(s.group("x"))), span * bw / 2.0 + 1e-6,
                    f"{aspect}/{s.group('sid')}: 横向位移越过了推近盈余（露底）")
                self.assertLessEqual(
                    abs(float(s.group("y"))), span * bh / 2.0 + 1e-6,
                    f"{aspect}/{s.group('sid')}: 纵向位移越过了推近盈余（露底）")

    def test_offset_is_a_fraction_of_slack_not_pixels(self):
        """偏移是"占盈余的比例"——换画幅时槽位盒变了，比例不变，px 变。

        这是"绝不露底"的结构保证：写 px 的话一份在竖屏安全的输入换到横屏就可能
        越线，而比例在任意槽位几何下都 ≤ 1。
        """
        frac = {}
        for aspect in ("portrait", "landscape"):
            bw, bh = _template.get_image_box(aspect)
            for s in settles(render(aspect=aspect)[1]):
                span = float(s.group("scale")) - 1.0
                frac[(aspect, s.group("sid"))] = (
                    abs(float(s.group("x"))) / (span * bw / 2.0),
                    abs(float(s.group("y"))) / (span * bh / 2.0))
        for (asp, sid), f in frac.items():
            self.assertLessEqual(f[0], 1.0 + 1e-6, f"{asp}/{sid}: 横向用掉了超过全部盈余")
            self.assertLessEqual(f[1], 1.0 + 1e-6, f"{asp}/{sid}: 纵向用掉了超过全部盈余")
        # 同一段在两个画幅上用的是同一组比例（不是按 px 写死的第二份数）。
        # 反解自生成的 px 字面量（num_fmt 定点去尾零），故按 1e-4 比对。
        for sid in ("seg-a", "seg-b", "seg-c", "seg-d"):
            for i in (0, 1):
                self.assertAlmostEqual(frac[("portrait", sid)][i],
                                       frac[("landscape", sid)][i], places=4)

    def test_short_segment_still_safe(self):
        """短段吃入场预算压缩（k<1），收拢时长跟着缩，比例不受影响。"""
        _, html = render(dur=0.5)
        for s in settles(html):
            bw, bh = _template.get_image_box("portrait")
            span = float(s.group("scale")) - 1.0
            self.assertLessEqual(abs(float(s.group("x"))), span * bw / 2.0 + 1e-6)
            self.assertLessEqual(abs(float(s.group("y"))), span * bh / 2.0 + 1e-6)
            self.assertGreater(float(s.group("dur")), 0)


class HandoffToDrift(unittest.TestCase):
    """收拢终点 == 运镜起点，且框先落位。"""

    def test_settle_ends_exactly_where_drift_starts(self):
        _, html = render()
        for sid in ("seg-a", "seg-b", "seg-c", "seg-d"):
            st = next(x for x in settles(html) if x.group("sid") == sid)
            drifts = [x for x in drift_hits(html, sid)
                      if float(x.group("dur")) > _PIN_MAX_DUR]
            self.assertTrue(drifts, f"{sid}: 长段应当有持续运镜")
            first = min(drifts, key=lambda x: float(x.group("at")))
            end = float(st.group("at")) + float(st.group("dur"))
            self.assertAlmostEqual(end, float(first.group("at")), places=2,
                                   msg=f"{sid}: 收拢终点与运镜起点错位（成片里是速度突变）")

    def test_frame_lands_before_the_content_settles(self):
        """容器层（框）先落位——四样东西同时到位就只是一块东西浮上来。"""
        _, html = render()
        for sid in ("seg-a", "seg-b", "seg-c", "seg-d"):
            fr = next(x for x in _FRAME_RE.finditer(html) if x.group("sid") == sid)
            st = next(x for x in settles(html) if x.group("sid") == sid)
            self.assertLessEqual(float(fr.group("at")) + float(fr.group("dur")),
                                 float(st.group("at")) + float(st.group("dur")))

    def test_pin_is_the_last_tween_on_the_carrier(self):
        """钉住补间必须最后落地，否则预渲染把 scale>1 留在元素上。"""
        _, html = render()
        for sid in ("seg-a", "seg-b", "seg-c", "seg-d"):
            hits = drift_hits(html, sid)
            pins = [x for x in hits if float(x.group("dur")) <= _PIN_MAX_DUR]
            self.assertTrue(pins, f"{sid}: 运镜末尾应当有钉住补间")
            last = max(hits, key=lambda x: x.start())
            self.assertIn(last, pins,
                          msg=f"{sid}: 载体上最后一条补间不是钉住补间——"
                              "GSAP 建时间线时预渲染的终值会留在元素上")


class RhythmRotates(unittest.TestCase):
    """轮换：连续四段四个不同来向（与标题 rhythm / 运镜方向同源的理由）。"""

    def test_four_consecutive_segments_never_repeat(self):
        _, html = render()
        got = [(s.group("scale"), s.group("x"), s.group("y")) for s in settles(html)]
        self.assertEqual(len(got), 4)
        self.assertEqual(len(set(got)), 4, f"收拢档位重复了：{got}")

    def test_beats_come_from_both_sides(self):
        """轮换里必须有反向来向——符号在哪一层被 abs 吃掉，这条就挂。

        `_unit_frac` 曾经取 `abs`，四档里写负号的那档被压成与正号同向，"连续四段
        四个来向"退化成三种；而"档位不重样"那条断言（比的是 x/y 数值）看不出来，
        因为 17.15 与 14.7 本来就不等。
        """
        xs = [float(s.group("x")) for s in settles(render()[1])]
        self.assertTrue(any(x > 0 for x in xs), f"全部挤在同一侧: {xs}")
        self.assertTrue(any(x < 0 for x in xs), f"全部挤在同一侧: {xs}")

    def test_rotation_follows_ordinal_not_id(self):
        """档位跟着"第几张静态图"走，不跟着 id 走——换段序后同一段要换档。

        （用 id 哈希取档就是 Ken Burns 那边踩过的坑：长期均匀但局部扎堆，实测过
        连续六段撞同一侧。）
        """
        def scale_of(sid, source):
            _, html = render(source=source)
            return next(s.group("scale") for s in settles(html)
                        if s.group("sid") == sid)

        src = _four_slot_source()
        before = scale_of("seg-a", src)
        src["segments"] = list(reversed(src["segments"]))
        self.assertNotEqual(scale_of("seg-a", src), before)


class WhoDoesNotSettle(unittest.TestCase):
    """不该演的载体一条补间都不生成（与持续运镜同一份判据）。"""

    def test_video_has_no_settle(self):
        images = {"seg-a": {"src": "images/a.mp4", "type": "video"},
                  "seg-b": {"src": "images/b.png"}}
        _, html = render(images=images)
        self.assertEqual([s.group("sid") for s in settles(html)], ["seg-b"])

    def test_canvas_page_has_no_settle(self):
        """整页画布由导演逐拍演，再叠一层收拢是替作者加意图。"""
        m = H.make_manifest(source=H.sample_source(), dur=6.0)
        html = generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m), aspect="portrait",
                             theme="dark")
        # seg-b 是整页画布；seg-a 是槽位
        self.assertEqual([s.group("sid") for s in settles(html)], ["seg-a"])

    def test_keep_page_has_no_settle(self):
        """接续页搬的是上一页演完的终态，收拢就是 _stage_carry 要消灭的页界回弹。"""
        images = H.sample_images(H.make_manifest(source=_four_slot_source(), dur=6.0))
        images["seg-b"]["stage"] = "keep"
        _, html = render(images=images)
        self.assertNotIn("seg-b", [s.group("sid") for s in settles(html)])

    def test_scale_at_or_below_one_disables_the_layer(self):
        """scale ≤ 1 = 明确不要这层（而且必然露底），不该生成任何补间。

        用换掉模板对象的方式测而不是 `--set`：`settle.scale` 只是 **base**，rhythm
        逐项覆盖它——所以要验这条 guard 得先把 rhythm 摘掉，否则读到的永远是档位
        里那个 scale。
        """
        tpl = _template.load_template()
        ie = tpl["animation"]["imageEntrance"]
        ie.pop("rhythm", None)
        for v in (1.0, 0.9):
            ie["settle"]["scale"] = v
            with mock.patch("html_renderer.load_template", return_value=tpl):
                self.assertEqual(settles(render()[1]), [])


class Deterministic(unittest.TestCase):
    """同一份输入两次生成逐字节一致（收拢不能引入随机/墙钟）。"""

    def test_twice_is_identical(self):
        _, a = render()
        _, b = render()
        self.assertEqual(a, b)
        for s in settles(a):
            self.assertTrue(math.isfinite(float(s.group("x"))))
            self.assertTrue(math.isfinite(float(s.group("y"))))


if __name__ == "__main__":
    unittest.main()
