#!/usr/bin/env python3
"""惯性档 `momentum`（预备 → 冲程 → 跟随）的契约与**产物级**判据。

与 `test_hold_drift.py` 同一条方法论：判据尽量从生成的 HTML 里抠补间来读，
不在 Python 侧另写一份"应该生成什么"的实现。唯一的例外是 `_momentum.plan`
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
import _momentum as M
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"axis": "y", "distance": -180, "ease": "sine.inOut",
               "anticipation": 0.12, "stretch": 0.18, "follow": 0.10}
DURATION = 0.8
FPS = 24
BALL_SVG = ('<svg viewBox="0 0 980 735">'
            '<circle id="ball" cx="490" cy="360" r="40"/></svg>')
# 按形态抓，不按 ease 名抓：一条 momentum 拍发射的是一组同 selector 的 tl.set，
# 与 morph 的采样序列、draw 的收起 set 都不重样（后两者带 attr:{} 嵌套）。
BALL_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #ball)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<rect id="ball" x="60" y="300" width="960" height="500"/>'
             "</svg>")


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def momentum_images(cfg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": "#ball", "duration": DURATION,
            "momentum": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg", "inline_svg": BALL_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def ball_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in BALL_SET.finditer(html)]


def step_with(**kw):
    return {"at": 0, "target": "#ball",
            "momentum": dict(DEFAULT_CFG, **kw)}


class VelocityContract(unittest.TestCase):
    def test_velocity_pure_bounded_single_peak(self):
        """速度包络：端点归零、单峰、峰值 1，且是 t 的纯函数。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        v = p["velocity"]
        self.assertEqual(v(0.0), 0.0)
        self.assertEqual(v(1.0), 0.0)
        vals = [v(i / 200) for i in range(201)]
        self.assertTrue(all(0.0 <= x <= 1.0 for x in vals))
        self.assertAlmostEqual(max(vals), 1.0, places=3)
        peak = vals.index(max(vals))
        self.assertEqual(vals[:peak + 1], sorted(vals[:peak + 1]))
        self.assertEqual(vals[peak:], sorted(vals[peak:], reverse=True))
        # 跳着求、倒着求，同一个 p 恒等
        for q in (0.13, 0.5, 0.77):
            self.assertEqual(v(q), v(q))
        vals_rev = [v(i / 200) for i in range(200, -1, -1)]
        self.assertEqual(vals_rev, list(reversed(vals)))

    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [M.state_at(t, p) for t in ts]
        self.assertEqual(forward, [M.state_at(t, p) for t in reversed(ts)][::-1])
        M.state_at(0.31, p)
        self.assertEqual(forward, [M.state_at(t, p) for t in ts])
        # 三段窗口几何：总长恰等于 duration，冲程占 66%
        self.assertAlmostEqual(p["anticipation_end"], DURATION * 0.18)
        self.assertAlmostEqual(p["drive_duration"], DURATION * 0.66)
        self.assertAlmostEqual(p["follow_duration"], DURATION * 0.16)
        self.assertAlmostEqual(p["duration"], DURATION)

    def test_families_keep_peak_in_drive(self):
        """换无过冲 inOut 族，速度峰值与形变峰值仍同时落在冲程中段。"""
        for fam in ("sine.inOut", "power2.inOut", "power3.inOut",
                    "power4.inOut", "expo.inOut", "circ.inOut",
                    "quad.inOut", "cubic.inOut", "strong.inOut"):
            with self.subTest(family=fam):
                p = M.plan(dict(DEFAULT_CFG, ease=fam), DURATION, FPS)
                pk = M.peak(p)
                self.assertGreaterEqual(pk["drive_frac"], 0.20)
                self.assertLessEqual(pk["drive_frac"], 0.80)
                self.assertLessEqual(abs(pk["peak_t"] - pk["velocity_t"]),
                                     2 / FPS)
                self.assertTrue(M.is_well_formed(p))


class ConservationContract(unittest.TestCase):
    def test_area_conserved_in_plan_and_product(self):
        """面积守恒两把尺：生成期严格、产物按截尾容差，不能混成一把。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertLessEqual(M.area_error(p), M.AREA_EPS)
        self.assertLessEqual(M.product_area_error(p), M.PRODUCT_AREA_EPS)
        for _, v in M.frames(p):
            self.assertAlmostEqual(v["scaleX"] * v["scaleY"], 1.0, delta=1e-5)
        # 反证：等比缩放（不守恒）在这一口径下必须显形
        bad = {"scaleX": 1.18, "scaleY": 1.18}
        self.assertGreater(abs(bad["scaleX"] * bad["scaleY"] - 1.0),
                           M.PRODUCT_AREA_EPS)

    def test_landing_exact_and_anticipation_returns_to_origin(self):
        """预备不改变落点；终点位移精确、尺寸精确回 1。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        start, end = M.state_at(0.0, p), M.landing(p)
        self.assertEqual(start, {"y": 0.0, "scaleX": 1.0, "scaleY": 1.0})
        self.assertAlmostEqual(end["y"], DEFAULT_CFG["distance"], places=9)
        self.assertAlmostEqual(end["scaleX"], 1.0, places=9)
        self.assertAlmostEqual(end["scaleY"], 1.0, places=9)
        # 预备段的位移是反向的，且预备末尾精确回到 0
        mid = M.state_at(p["anticipation_end"] / 2, p)
        self.assertGreater(mid["y"], 0)  # distance 为负 → 预备朝正方向
        self.assertAlmostEqual(M.state_at(p["anticipation_end"], p)["y"],
                               0.0, places=9)
        # 跟随段位移不再变化，只有形变在收
        self.assertEqual(M.state_at(p["drive_end"], p)["y"], end["y"])
        self.assertEqual(M.state_at(p["duration"] + 1.0, p), end)

    def test_axes_are_symmetric(self):
        """x / y 两档只是换轴，面积守恒与落点同一套口径。"""
        py = M.plan(DEFAULT_CFG, DURATION, FPS)
        px = M.plan(dict(DEFAULT_CFG, axis="x", distance=120), DURATION, FPS)
        self.assertIn("y", M.state_at(0.3, py))
        self.assertNotIn("y", M.state_at(0.3, px))
        self.assertIn("x", M.state_at(0.3, px))
        for p, axis, d in ((py, "y", -180), (px, "x", 120)):
            self.assertAlmostEqual(M.area_error(p), 0.0, delta=M.AREA_EPS)
            self.assertAlmostEqual(M.landing(p)[axis], d, places=9)
            self.assertTrue(M.is_well_formed(p))

    def test_well_formed_rejects_broken_envelopes(self):
        """自查能拦住"蓄力比主运动还夸张"，也能接受全 0 的纯位移档。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertTrue(M.is_well_formed(p))
        loud = M.plan(dict(DEFAULT_CFG, stretch=0.01, follow=0.30),
                      DURATION, FPS)
        self.assertFalse(M.is_well_formed(loud),
                         "跟随回压盖过冲程拉伸时，三段会被读成三次动作")
        plain = M.plan(dict(DEFAULT_CFG, anticipation=0, stretch=0, follow=0),
                       DURATION, FPS)
        self.assertTrue(M.is_well_formed(plain))
        for _, v in M.frames(plain):
            self.assertEqual((v["scaleX"], v["scaleY"]), (1.0, 1.0))


class SchemaContract(unittest.TestCase):
    def test_valid_snippet_passes_schema(self):
        """文档里那份示例真能过契约层（不是只在测试里合法）。"""
        snippet = {"seg2": {"src": "images/seg2.svg", "animate": {"steps": [
            {"at": 0, "target": "#ball", "duration": 0.8,
             "momentum": {"axis": "y", "distance": -180,
                          "ease": "sine.inOut", "anticipation": 0.12,
                          "stretch": 0.18, "follow": 0.10}}]}}}
        validate_images_json(snippet)
        # at_time 相对链也按整段推进（_WHOLE_BEAT_KEYS）
        from _timeline import beat_positions
        sent = [{"start_time": 2.0, "duration": 1.0}]
        steps = [{"at_time": "+0.5", "target": "#ball", "duration": 0.8,
                  "momentum": {"axis": "x", "distance": 10}},
                 {"at_time": "+0.2", "target": "#ball", "duration": 0.3,
                  "to": {"opacity": 1}}]
        pos = beat_positions(steps, sent, 1.0, 0.5)
        self.assertAlmostEqual(pos[1][0], pos[0][0] + 0.8 + 0.2, places=2)

    def test_momentum_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}}):
            with self.subTest(extra=extra):
                step = dict(step_with(), **extra)
                with self.assertRaises(ValueError):
                    validate_images_json({"seg2": {"src": "images/seg2.svg",
                                                   "animate": {"steps": [step]}}})

    def test_red_lines_at_consumption_side(self):
        """红线在消费侧：非法 axis / 行程 / 旋钮 / 曲线族逐个拒。"""
        bad = [
            {"axis": "z"}, {"axis": "x", "distance": 0},
            {"distance": float("nan")}, {"stretch": 0.5},
            {"anticipation": -0.1}, {"follow": 1.0},
            {"ease": "power3.out"}, {"ease": "back.inOut(1.7)"},
            {"ease": "spring(0.9)"}, {"ease": "steps(4)"},
            {"ease": "sine.in"}, {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                M.validate(dict(DEFAULT_CFG, **cfg))
        # 合法边界值放行
        for cfg in ({"stretch": 0.0}, {"stretch": M.STRETCH_MAX},
                    {"anticipation": M.ANTICIPATION_MAX},
                    {"follow": M.FOLLOW_MAX}):
            with self.subTest(cfg=cfg):
                M.validate(dict(DEFAULT_CFG, **cfg))
        # 相机层归折层，不认这一拍的收尾态 → 接续页会静默弹回原位
        with self.assertRaises(ValueError):
            validate_images_json({"seg2": {"src": "images/seg2.svg",
                                           "animate": {"steps": [
                                               {"at": 0, "target": "#cam",
                                                "momentum": dict(DEFAULT_CFG)}]}}})

    def test_stagger_repeat_yoyo_rejected(self):
        """三段是连续时间线，错峰与重放都该在契约层说不通。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True}):
            with self.subTest(extra=extra):
                step = dict(step_with(), **extra)
                with self.assertRaises(ValueError):
                    validate_images_json({"seg2": {"src": "images/seg2.svg",
                                                   "animate": {"steps": [step]}}})


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐帧变量全部等于 `_momentum.frames`。"""
        html = render(images=momentum_images())
        got = ball_sets(html)
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        want = M.frames(p)
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel == "#img-seg-a #ball" for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wv), (_, gv, gt) in zip(want, got):
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gv, wv)
        # 首末两帧：起点原位、终点精确落位且尺寸回 1
        self.assertEqual(got[0][1]["y"], 0.0)
        self.assertAlmostEqual(got[-1][1]["y"], -180.0, delta=1e-6)
        self.assertEqual((got[-1][1]["scaleX"], got[-1][1]["scaleY"]),
                         (1.0, 1.0))

    def test_both_aspects_and_deterministic(self):
        """两画幅各验；逐帧 seek 的前提是两次生成一字节不差。"""
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                html = render(images=momentum_images(), aspect=aspect)
                self.assertEqual(len(ball_sets(html)),
                                 len(M.frames(M.plan(DEFAULT_CFG, DURATION, FPS))))
                self.assertEqual(html, render(images=momentum_images(),
                                             aspect=aspect))

    def test_knobs_drive_product(self):
        """契约是唯一真源：改旋钮产物跟着走，渲染端没有硬编码第二份。"""
        base = ball_sets(render(images=momentum_images()))
        louder = ball_sets(render(images=momentum_images(
            cfg={"stretch": 0.30})))
        self.assertGreater(max(v["scaleX"] for _, v, _ in louder),
                           max(v["scaleX"] for _, v, _ in base))
        # 全 0 = 纯位移档：一条 scale 都不许变
        plain = ball_sets(render(images=momentum_images(
            cfg={"anticipation": 0, "stretch": 0, "follow": 0})))
        self.assertTrue(all(v["scaleX"] == 1.0 and v["scaleY"] == 1.0
                            for _, v, _ in plain))
        # 时长决定采样帧数，不是写死的常数（下限 12、上限 240 在 _momentum）
        fewer = ball_sets(render(images=momentum_images(duration=0.4)))
        more = ball_sets(render(images=momentum_images(duration=1.6)))
        self.assertGreater(len(more), len(fewer))
        self.assertNotIn("#ball", render())
        # x 轴档写 x 不写 y
        xs = ball_sets(render(images=momentum_images(
            cfg={"axis": "x", "distance": 120})))
        self.assertTrue(all("x" in v and "y" not in v for _, v, _ in xs))

    def test_sample_grid_is_deterministic_and_frame_bounded(self):
        """采样网格：严格递增、覆盖首尾、帧距不超过一帧；两次生成完全一致。"""
        p = M.plan(DEFAULT_CFG, DURATION, FPS)
        fr = M.frames(p)
        self.assertEqual(fr, M.frames(p))
        ts = [t for t, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(set(ts)), "同一时刻撞车必须去重，否则最后一帧会被覆盖")
        self.assertTrue(all(0.0 <= b - a <= 1.0 / FPS + 0.011
                            for a, b in zip(ts, ts[1:])))
        for dur, floor in ((0.2, M.SAMPLES_MIN), (20.0, M.SAMPLES_MAX)):
            q = M.plan(DEFAULT_CFG, dur, FPS)
            self.assertEqual(len(M.frames(q)), floor + 1)

    def test_default_html_has_no_momentum_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(ball_sets(render()), [])
        for cfg in ({"ease": "power3.out"}, {"distance": 0},
                    {"stretch": 0.9}, {"axis": "z"}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=momentum_images(cfg=cfg))


class CarryContract(unittest.TestCase):
    def test_settled_state_carries_translate(self):
        """接续页第 0 帧：收尾位移折成 translate，尺寸回 1 不用搬。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        steps = [step_with()]
        out, notes = SC.bake_settled_state(raw, steps)
        root = ET.fromstring(out)
        el = root.find(".//*[@id='%s']" % "ball")
        self.assertIsNotNone(el)
        self.assertIn("translate(0,-180)", el.get("transform", ""))
        self.assertEqual(notes, [])
        # x 轴同样精确
        out_x, _ = SC.bake_settled_state(raw, [step_with(axis="x", distance=120)])
        el_x = ET.fromstring(out_x).find(".//*[@id='%s']" % "ball")
        self.assertIn("translate(120,0)", el_x.get("transform", ""))

    def test_carry_does_not_leak_scale_and_no_notes(self):
        """只搬 x/y：不把 scaleX/scaleY 写进静态副本（否则与下一页自己演的打架）。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        self.assertNotIn("scale(", out)
        self.assertNotIn("scaleX", out)
        # 反证：不认这一拍的典型症状是元素留在原位（没有 transform）
        self.assertIn("transform=", out)


if __name__ == "__main__":
    unittest.main()
