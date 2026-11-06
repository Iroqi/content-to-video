#!/usr/bin/env python3
"""拖曳档 `drag`（相位链 → 阻尼过冲 → 全链同刻落定）的契约与**产物级**判据。

与 `test_momentum.py` 同一条方法论：判据尽量从生成的 HTML 里抠补间来读，
不在 Python 侧另写一份"应该生成什么"的实现。唯一的例外是 `_drag.plan`
——它是契约层、渲染端、接续烘焙共用的那份真源，测试拿它跟产物**对账**，
而不是拿它替产物背书。
"""
import contextlib
import io
import json
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _animate as AN
import _drag as D
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

CHAIN = ["#head", "#mid", "#tail"]
DEFAULT_CFG = {"chain": CHAIN, "axis": "y", "distance": -160,
               "ease": "power3.out", "lag": 0.22,
               "overshoot": 0.12, "decay": 0.72}
DURATION = 1.2
FPS = 24
CHAIN_SVG = ('<svg viewBox="0 0 980 735">'
             '<circle id="head" cx="300" cy="360" r="34"/>'
             '<circle id="mid" cx="490" cy="360" r="34"/>'
             '<circle id="tail" cx="680" cy="360" r="34"/></svg>')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<rect id="head" x="60" y="300" width="200" height="200"/>'
             '<rect id="mid" x="440" y="300" width="200" height="200"/>'
             '<rect id="tail" x="820" y="300" width="200" height="200"/>'
             "</svg>")
# 按形态抓，不按 ease 名抓：一条 drag 拍发射的是每个链成员一组同刻 tl.set，
# 变量表只有 x 或 y（无嵌套），与 morph 的采样序列、momentum 的 scale 三键都不重样。
DRAG_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #(?:head|mid|tail))",\s*(\{[^{}]*\}),\s*([\d.]+)\)')


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def drag_images(cfg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": "#head", "duration": DURATION,
            "drag": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg", "inline_svg": CHAIN_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def chain_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in DRAG_SET.finditer(html)]


def step_with(**kw):
    return {"at": 0, "target": "#head", "drag": dict(DEFAULT_CFG, **kw)}


class PhaseContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟。"""
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [D.state_at(t, p, 1) for t in ts]
        self.assertEqual(forward, [D.state_at(t, p, 1) for t in reversed(ts)][::-1])
        D.state_at(0.31, p, 2)
        self.assertEqual(forward, [D.state_at(t, p, 1) for t in ts])
        # 相位与幅度都由链序算，不另写一份
        self.assertAlmostEqual(p["phases"][0], 0.0)
        self.assertAlmostEqual(p["phases"][-1], 0.22)
        self.assertAlmostEqual(p["amps"][1], 0.12 * 0.72)
        self.assertAlmostEqual(p["duration"], DURATION)

    def test_all_members_land_exactly_and_start_at_zero(self):
        """重参数化的全部意义：链尾不能停在半路，全链同刻精确落定。"""
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        for i in range(len(CHAIN)):
            with self.subTest(i=i):
                self.assertAlmostEqual(D.state_at(0.0, p, i)["y"], 0.0)
                end = D.landing(p, i)
                self.assertAlmostEqual(end["y"], -160.0, places=6)
                self.assertAlmostEqual(round(end["y"], 6),
                                       round(-160.0, 6), delta=D.PRODUCT_EPS)
        self.assertTrue(D.is_well_formed(p))
        # 各家族都过：换曲线不许把落点换掉
        for fam in ("power3.out", "sine.inOut", "expo.out", "circ.inOut",
                    "power2.inOut", "strong.out"):
            with self.subTest(family=fam):
                q = D.plan(dict(DEFAULT_CFG, ease=fam), DURATION, FPS)
                self.assertTrue(D.is_well_formed(q))
                for i in range(len(CHAIN)):
                    self.assertAlmostEqual(D.landing(q, i)["y"], -160.0,
                                           places=6)

    def test_phase_is_monotone_along_chain(self):
        """波沿链往后传：半程时刻随链序严格后移。"""
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        ts = [D.crossing(p, i) for i in range(len(CHAIN))]
        self.assertEqual(ts, sorted(ts))
        self.assertGreater(ts[-1] - ts[0], 0.5 / FPS)
        self.assertTrue(all(b > a for a, b in zip(ts, ts[1:])))
        # 相位跨度落在 lag 带里，不越界
        self.assertLess(ts[-1], DURATION * (0.22 + 0.2))
        # lag=0 是全链同步，判据不硬要"递增"（合法的纯位移档）
        flat = D.plan(dict(DEFAULT_CFG, lag=0.0, overshoot=0.0),
                      DURATION, FPS)
        self.assertTrue(D.is_well_formed(flat))
        self.assertEqual(len({round(D.crossing(flat, i), 6)
                              for i in range(len(CHAIN))}), 1)

    def test_damping_decays_and_overshoot_bounded(self):
        """能量逐节耗散：偏离量随 i 不增，且不盖过主运动。"""
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        devs = [D.deviation(p, i) for i in range(len(CHAIN))]
        self.assertTrue(all(b <= a + 1e-6 for a, b in zip(devs, devs[1:])))
        self.assertLessEqual(max(devs),
                             DEFAULT_CFG["overshoot"] * 160 + 1e-6)
        self.assertGreater(devs[0], 0.0)
        # overshoot=0：全链无过冲，单调走到落点
        plain = D.plan(dict(DEFAULT_CFG, overshoot=0.0), DURATION, FPS)
        self.assertEqual([D.deviation(plain, i) for i in range(len(CHAIN))],
                         [0.0, 0.0, 0.0])
        self.assertTrue(D.is_well_formed(plain))
        # decay=1：全链同幅，仍不许递增
        even = D.plan(dict(DEFAULT_CFG, decay=1.0), DURATION, FPS)
        self.assertAlmostEqual(D.deviation(even, 0),
                               D.deviation(even, 2), places=6)
        self.assertTrue(D.is_well_formed(even))


class SchemaContract(unittest.TestCase):
    def test_documented_snippet_passes_schema_and_whole_beat_cursor(self):
        """文档里那份示例真能过契约层（不是只在测试里合法）。"""
        snippet = {"seg2": {"src": "images/seg2.svg", "animate": {"steps": [
            {"at": 0, "target": "#head", "duration": 1.2,
             "drag": {"chain": ["#head", "#mid", "#tail"],
                      "axis": "y", "distance": -160, "ease": "power3.out",
                      "lag": 0.22, "overshoot": 0.12, "decay": 0.72}}]}}}
        validate_images_json(snippet)
        # at_time 相对链按整段推进（_WHOLE_BEAT_KEYS）
        from _timeline import beat_positions
        sent = [{"start_time": 2.0, "duration": 1.0}]
        steps = [{"at_time": "+0.5", "target": "#head", "duration": 0.8,
                  "drag": {"chain": CHAIN, "axis": "x", "distance": 10}},
                 {"at_time": "+0.2", "target": "#head", "duration": 0.3,
                  "to": {"opacity": 1}}]
        pos = beat_positions(steps, sent, 1.0, 0.5)
        self.assertAlmostEqual(pos[1][0], pos[0][0] + 0.8 + 0.2, places=2)

    def test_drag_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -20}}):
            with self.subTest(extra=extra):
                step = dict(step_with(), **extra)
                with self.assertRaises(ValueError):
                    validate_images_json({"seg2": {"src": "images/seg2.svg",
                                                   "animate": {"steps": [step]}}})

    def test_red_lines_at_consumption_side_and_chain_head(self):
        """红线在消费侧：非法旋钮逐个拒，链头与相机层另有两条。"""
        bad = [
            {"axis": "z"}, {"axis": "x", "distance": 0},
            {"distance": float("nan")}, {"lag": 0.9}, {"lag": -0.1},
            {"overshoot": 0.5}, {"decay": 1.2}, {"decay": -0.1},
            {"ease": "none"}, {"ease": "linear"}, {"ease": "power3.in"},
            {"ease": "back.inOut(1.7)"}, {"ease": "spring(0.9)"},
            {"ease": "steps(4)"}, {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                D.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"lag": 0.0}, {"lag": D.LAG_MAX},
                    {"overshoot": 0.0}, {"overshoot": D.OVERSHOOT_MAX},
                    {"decay": 0.0}, {"decay": D.DECAY_MAX}):
            with self.subTest(cfg=cfg):
                D.validate(dict(DEFAULT_CFG, **cfg))
        # chain 形状：长度、.class、重复、链头
        for chain in (["#head"], ["#head"] * 9, ["#head", ".node"],
                      ["#head", "#head"], ["#mid", "#head"]):
            with self.subTest(chain=chain), self.assertRaises(ValueError):
                validate_images_json({"seg2": {"src": "images/seg2.svg",
                                               "animate": {"steps": [step_with(
                                                   chain=chain)]}}})
        with self.assertRaises(ValueError):
            validate_images_json({"seg2": {"src": "images/seg2.svg",
                                           "animate": {"steps": [step_with(
                                               chain=["#head", "#cam"])]}}})

    def test_stagger_repeat_yoyo_rejected_and_degenerate_is_legal(self):
        """链序、相位、落定都自带，错峰与重放都该在契约层说不通。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True}):
            with self.subTest(extra=extra):
                step = dict(step_with(), **extra)
                with self.assertRaises(ValueError):
                    validate_images_json({"seg2": {"src": "images/seg2.svg",
                                                   "animate": {"steps": [step]}}})
        # 全 0 的纯位移档合法：没有相位与阻尼可钉，硬判会误杀
        plain = D.plan(dict(DEFAULT_CFG, lag=0.0, overshoot=0.0),
                       DURATION, FPS)
        self.assertTrue(D.is_well_formed(plain))
        fr = D.frames(plain)
        # 同一时刻三节位移完全相同 = 全链同步，相位没偷偷生效
        self.assertTrue(all(fr[3 * k][2] == fr[3 * k + 1][2] == fr[3 * k + 2][2]
                            for k in range(len(fr) // 3)))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐帧变量全部等于 `_drag.frames`。"""
        html = render(images=drag_images())
        got = chain_sets(html)
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        want = D.frames(p)
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel.startswith("#img-seg-a #")
                            for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wsel, wv), (gsel, gv, gt) in zip(want, got):
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gsel, "#img-seg-a " + wsel)
            self.assertEqual(gv, wv)
        # 首帧全链原位、末帧全链精确落位
        self.assertEqual(len(got[:3]), 3)
        self.assertTrue(all(v["y"] == 0.0 for _, v, _ in got[:3]))
        self.assertTrue(all(v["y"] == -160.0 for _, v, _ in got[-3:]))
        # 同一时刻链上越靠后位移越小（波还在传）
        mid = [v["y"] for _, v, _ in got[3 * 15:3 * 15 + 3]]
        self.assertTrue(mid[0] < mid[1] < mid[2] < 0)

    def test_both_aspects_and_deterministic(self):
        """两画幅各验；逐帧 seek 的前提是两次生成一字节不差。"""
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                html = render(images=drag_images(), aspect=aspect)
                self.assertEqual(len(chain_sets(html)),
                                 len(D.frames(D.plan(DEFAULT_CFG, DURATION, FPS))))
                self.assertEqual(html, render(images=drag_images(),
                                             aspect=aspect))

    def test_knobs_drive_product(self):
        """契约是唯一真源：改旋钮产物跟着走，渲染端没有硬编码第二份。"""
        base = chain_sets(render(images=drag_images()))
        wider = chain_sets(render(images=drag_images(cfg={"lag": 0.35})))
        # lag 真的进了产物：同一帧的链尾位移不同，不是只读了配置
        tail_base = [v["y"] for sel, v, _ in base if sel.endswith("#tail")]
        tail_wide = [v["y"] for sel, v, _ in wider if sel.endswith("#tail")]
        self.assertEqual(len(tail_base), len(tail_wide))
        self.assertNotEqual(tail_base, tail_wide)
        # 相位：链尾第一帧仍为 0，但半程时刻后移
        p0 = D.plan(DEFAULT_CFG, DURATION, FPS)
        p1 = D.plan(dict(DEFAULT_CFG, lag=0.35), DURATION, FPS)
        self.assertGreater(D.crossing(p1, 2), D.crossing(p0, 2))
        # overshoot=0：全链不过冲（|y| 不超过 |distance|）
        plain = chain_sets(render(images=drag_images(cfg={"overshoot": 0.0})))
        self.assertTrue(all(-160.0 - 1e-6 <= v["y"] <= 1e-6
                            for _, v, _ in plain))
        # 行程与轴
        far = chain_sets(render(images=drag_images(cfg={"distance": -240})))
        self.assertAlmostEqual(far[-1][1]["y"], -240.0, delta=1e-6)
        xs = chain_sets(render(images=drag_images(cfg={"axis": "x",
                                                      "distance": 120})))
        self.assertTrue(all("x" in v and "y" not in v for _, v, _ in xs))
        self.assertAlmostEqual(xs[-1][1]["x"], 120.0, delta=1e-6)
        self.assertNotIn("#head", render())

    def test_sample_grid_is_deterministic_and_frame_bounded(self):
        """采样网格：严格递增、覆盖首尾、帧距不超过一帧；同刻按链序。"""
        p = D.plan(DEFAULT_CFG, DURATION, FPS)
        fr = D.frames(p)
        self.assertEqual(fr, D.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(len(ts), len(set(ts)) * len(CHAIN))
        distinct = sorted(set(ts))
        self.assertTrue(all(0.0 <= b - a <= 1.0 / FPS + 0.011
                            for a, b in zip(distinct, distinct[1:])))
        # 同一时刻的链序不能被字典序洗掉
        first = [sel for t, sel, _ in fr if t == ts[0]]
        self.assertEqual(first, CHAIN)
        for dur, floor in ((0.2, D.SAMPLES_MIN), (20.0, D.SAMPLES_MAX)):
            q = D.plan(DEFAULT_CFG, dur, FPS)
            self.assertEqual(len(D.frames(q)), (floor + 1) * len(CHAIN))

    def test_default_html_has_no_drag_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(chain_sets(render()), [])
        for cfg in ({"ease": "none"}, {"distance": 0}, {"lag": 0.9},
                    {"overshoot": 0.9}, {"axis": "z"}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=drag_images(cfg=cfg))


class CarryContract(unittest.TestCase):
    def test_settled_state_carries_translate_for_every_member(self):
        """接续页第 0 帧：链上每一节都折成 translate，不只链头。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        root = ET.fromstring(out)
        self.assertEqual(notes, [])
        for sel in CHAIN:
            with self.subTest(sel=sel):
                el = root.find(".//*[@id='%s']" % sel[1:])
                self.assertIsNotNone(el)
                self.assertIn("translate(0,-160)", el.get("transform", ""))
        # x 轴同样精确
        out_x, _ = SC.bake_settled_state(
            raw, [step_with(axis="x", distance=120)])
        for sel in CHAIN:
            el = ET.fromstring(out_x).find(".//*[@id='%s']" % sel[1:])
            self.assertIn("translate(120,0)", el.get("transform", ""))

    def test_missing_chain_node_fails_at_generation(self):
        """按文件拦：链上一节落空不会报错、只会静默少一段跟随波。"""
        chain = ["#head", "#mid", "#gone"]
        block = {"steps": [step_with(chain=chain)]}
        errs, _ = AN.check_animate(CARRY_SVG, block, "seg2", full_bleed=False)
        self.assertTrue(any("#gone" in e for e in errs), errs)
        # 链头落空由原来的 target 判据兜住
        errs2, _ = AN.check_animate(
            CARRY_SVG, {"steps": [{"at": 0, "target": "#head",
                                   "drag": dict(DEFAULT_CFG,
                                                chain=["#head", "#mid"])}]},
            "seg2", full_bleed=True)
        self.assertEqual(errs2, [])
        # 满幅画布也拦（chain 校验在机位检查之前）
        errs3, _ = AN.check_animate(CARRY_SVG, block, "seg2", full_bleed=True)
        self.assertTrue(any("#gone" in e for e in errs3), errs3)

    def test_carry_does_not_leak_scale(self):
        """drag 不带形变：静态副本里不许出现 scale，三节都得有 transform。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        self.assertNotIn("scale(", out)
        self.assertNotIn("scaleX", out)
        self.assertEqual(out.count("translate(0,-160)"), len(CHAIN))


if __name__ == "__main__":
    unittest.main()
