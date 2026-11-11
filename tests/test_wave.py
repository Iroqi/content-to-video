#!/usr/bin/env python3
"""波场档 `wave`（几何距离 → 波前 → 紧支撑脉冲）的契约与**产物级**判据。

与 `test_momentum.py` / `test_drag.py` / `test_organic.py` / `test_trail.py` 同一条
方法论：判据尽量从生成的 HTML 里抠补间来读，不在 Python 侧另写一份「应该生成什么」
的实现。唯一的例外是 `_wave.plan`——它是契约层、渲染端、生成期体检、接续烘焙
共用的那份真源，测试拿它跟产物**对账**，而不是拿它替产物背书。

本档独有的一条判据是「改图即改编排」：把某个格子往震中挪，产物里它的波前到达
时刻必须跟着变，其余格子的不变。既有档位都不读 SVG 坐标，不可能有这条。
"""
import contextlib
import io
import json
import math
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _field  # 采样带与归一口径的真源
import _wave as W
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"cells": ["#d3", "#d2", "#d1", "#d4"], "origin": "#ep",
               "axis": "y", "amplitude": -40, "span": 0.5, "width": 0.34,
               "falloff": 0.45}
TARGET = "#d3"
DURATION = 1.2
FPS = 24
# 四个格子离震中远近不同，且 `cells` 的顺序**故意不按距离**（#d4 排在 #d1 之后，
# 却比它近）——用来钉住「波前听距离的话，不听序号的话」。
STAGE_SVG = ('<svg viewBox="0 0 980 735">'
             '<circle id="ep" cx="490" cy="367" r="4"/>'
             '<rect id="d1" x="100" y="100" width="20" height="20"/>'
             '<rect id="d2" x="300" y="200" width="20" height="20"/>'
             '<rect id="d3" x="450" y="350" width="20" height="20"/>'
             '<rect id="d4" x="700" y="500" width="20" height="20"/>'
             "</svg>")
# 只把 #d2 往远处挪（仍不是最远，r_max 不变）：其余格子的波前必须一格不动。
MOVED_SVG = STAGE_SVG.replace('id="d2" x="300" y="200"',
                              'id="d2" x="200" y="100"')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<circle id="ep" cx="490" cy="367" r="4"/>'
             '<rect id="d1" x="100" y="100" width="20" height="20"/>'
             '<rect id="d2" x="300" y="200" width="20" height="20"/>'
             '<rect id="d3" x="450" y="350" width="20" height="20"/>'
             '<rect id="d4" x="700" y="500" width="20" height="20"/>'
             "</svg>")
# 一条 wave 拍发射的是每帧每格子一条 tl.set，selector 各自带 #img-{sid} 作用域。
WAVE_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #[A-Za-z_][\w-]*)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
OWN_SELECTORS = {"#img-seg-a " + s for s in DEFAULT_CFG["cells"]}


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def wave_images(cfg=None, svg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": TARGET, "duration": DURATION,
            "wave": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg",
                     "inline_svg": svg or STAGE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def wave_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in WAVE_SET.finditer(html)
            if m.group(1) in OWN_SELECTORS]


def step_with(**kw):
    return {"at": 0, "target": TARGET, "wave": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})


def make_plan(cfg=None, svg=None):
    return W.plan(dict(DEFAULT_CFG, **(cfg or {})), DURATION, FPS,
                  svg or STAGE_SVG, TARGET)


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = make_plan()
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [W.state_at(t, p, 2) for t in ts]
        self.assertEqual(forward, [W.state_at(t, p, 2) for t in reversed(ts)][::-1])
        W.state_at(0.31, p, 1)
        self.assertEqual(forward, [W.state_at(t, p, 2) for t in ts])
        self.assertTrue(W.is_well_formed(p))
        self.assertEqual(W.diagnose(p), [])
        # 端点之外被夹回端点，seek 跨过窗口不会冒出一段凭空的波
        self.assertEqual(W.state_at(-1.0, p, 2), W.state_at(0.0, p, 2))
        self.assertEqual(W.state_at(DURATION + 1.0, p, 2),
                         W.state_at(DURATION, p, 2))

    def test_pulse_is_compactly_supported(self):
        """定义性判据：波前未到 / 波已过都精确为 0，窗内确实动过。"""
        p = make_plan()
        for i in range(len(p["selectors"])):
            tau = W.wavefront(p, i)
            for t in (tau - 1e-6, tau + DEFAULT_CFG["width"] + 1e-6):
                if 0.0 < t < DURATION:
                    self.assertTrue(all(v == 0.0 for v in W.state_at(t, p, i).values()),
                                    (i, t))
            peak = W.state_at(W.peak_time(p, i), p, i)
            self.assertGreater(abs(peak["y"]), 1.0)
        # 衰减只管幅度、不改窗口：falloff=0 时窗仍是紧支撑的
        q = make_plan({"falloff": 0})
        self.assertTrue(W.is_well_formed(q))

    def test_endpoints_return_exactly_to_rest(self):
        """E(0)=E(1)=0：段首段尾每个格子精确回原位，主体没有任何残留。"""
        p = make_plan()
        for i in range(len(p["selectors"])):
            for st in (W.start(p, i), W.landing(p, i)):
                self.assertTrue(all(v == 0.0 for v in st.values()), (i, st))
        fr = W.frames(p)
        for _t, sel, var in fr[:4] + fr[-4:]:
            self.assertTrue(all(v == 0.0 for v in var.values()), sel)

    def test_peak_amplitude_follows_distance_falloff(self):
        """归一到最近格子：最近峰值精确 = amplitude，远端按 exp(−falloff·q) 递减。"""
        p = make_plan()
        order = sorted(range(4), key=lambda i: p["geometry"]["radii"][i])
        peaks = [abs(W.state_at(W.peak_time(p, i), p, i)["y"]) for i in order]
        self.assertAlmostEqual(peaks[0], abs(DEFAULT_CFG["amplitude"]), places=6)
        self.assertTrue(all(a >= b - 1e-9 for a, b in zip(peaks, peaks[1:])))
        self.assertAlmostEqual(peaks[-1],
                               abs(DEFAULT_CFG["amplitude"])
                               * math.exp(-DEFAULT_CFG["falloff"]),
                               places=5)
        flat = make_plan({"falloff": 0})
        self.assertTrue(all(abs(W.state_at(W.peak_time(flat, i), flat, i)["y"])
                            == abs(DEFAULT_CFG["amplitude"]) for i in range(4)))


class PropagationContract(unittest.TestCase):
    def test_wavefront_and_peak_order_follow_distance_not_cell_order(self):
        """与 stagger 的分界：τ 与峰值时刻按距离排，与 cells 写入顺序无关。"""
        p = make_plan()
        taus = [W.wavefront(p, i) for i in range(4)]
        self.assertFalse(taus == sorted(taus),
                         "这组 cells 故意不按距离排，波前不该跟着序号走")
        order = sorted(range(4), key=lambda i: p["geometry"]["radii"][i])
        for key in (W.wavefront, W.peak_time):
            seq = [key(p, i) for i in order]
            self.assertTrue(all(b > a for a, b in zip(seq, seq[1:])))
        self.assertEqual(p["selectors"], DEFAULT_CFG["cells"],
                         "写入顺序仍是作者给的 cells 序")
        # radial 档的方向也是几何给的：每个格子沿「格子 − 震中」推开
        r = make_plan({"axis": "radial"})
        for i in range(4):
            var = W.state_at(W.peak_time(r, i), r, i)
            dx, dy = r["geometry"]["vectors"][i]
            self.assertTrue("x" in var and "y" in var)
            if abs(dx) > abs(dy):
                self.assertGreater(abs(var["x"]), abs(var["y"]))
            else:
                self.assertGreater(abs(var["y"]), abs(var["x"]))

    def test_changing_svg_geometry_changes_the_wavefront(self):
        """本档独有的产物级判据：改图即改编排，延迟没有第二份真源。"""
        base = make_plan()
        moved = make_plan(svg=MOVED_SVG)
        self.assertGreater(W.wavefront(moved, 1), W.wavefront(base, 1))
        for i in (0, 2, 3):
            self.assertAlmostEqual(W.wavefront(moved, i),
                                   W.wavefront(base, i), places=9)
        self.assertEqual(base["geometry"]["r_max"], moved["geometry"]["r_max"])
        # 产物里也真的变了：#d2 的峰值时刻后移，其余格子不变
        a = [v["y"] for _, v, _ in wave_sets(render(images=wave_images()))]
        b = [v["y"] for _, v, _ in wave_sets(render(images=wave_images(svg=MOVED_SVG)))]
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), len(b))

    def test_selectors_keep_author_order_and_scope(self):
        """每帧每格子一条，层序是导演意图，不能被字典序洗掉。"""
        p = make_plan()
        fr = W.frames(p)
        self.assertEqual([s for _, s, _ in fr[:4]], p["selectors"])
        self.assertEqual([s for _, s, _ in fr[-4:]], p["selectors"])
        self.assertEqual(set(fr[0][2]), {"y"})
        # 几何落空 / 等距退化 / 读不出的 transform：按文件拦
        with self.assertRaisesRegex(ValueError, "找不到"):
            W.geometry(STAGE_SVG.replace('id="d2"', 'id="gone"'), DEFAULT_CFG)
        ring = ('<svg viewBox="0 0 200 200"><circle id="ep" cx="100" cy="100" r="2"/>'
                '<rect id="d3" x="148" y="98" width="4" height="4"/>'
                '<rect id="d2" x="48" y="98" width="4" height="4"/>'
                '<rect id="d1" x="98" y="48" width="4" height="4"/>'
                '<rect id="d4" x="98" y="148" width="4" height="4"/></svg>')
        with self.assertRaisesRegex(ValueError, "等|同步脉动|一样远"):
            W.geometry(ring, DEFAULT_CFG)


class GridContract(unittest.TestCase):
    def test_sample_grid_is_deterministic_and_bounded(self):
        """采样网格：严格递增、覆盖首尾、条数 = (帧数+1)×格子数。"""
        p = make_plan()
        fr = W.frames(p)
        self.assertEqual(fr, W.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(ts))
        frame_ts = [t for t, sel, _ in fr if sel == TARGET]
        self.assertEqual(frame_ts, sorted(set(frame_ts)),
                         "同一 0.01s 格撞车必须去重")
        self.assertEqual(len(fr), (W.frame_count(p) + 1) * 4)
        # 短拍要把 span/width 一起收小，否则先撞上「波收干净」那条红线
        short = dict(DEFAULT_CFG, span=0.08, width=0.08)
        for dur, floor, cfg in ((0.2, _field.SAMPLES_MIN, short),
                                (20.0, _field.SAMPLES_MAX, DEFAULT_CFG)):
            q = W.plan(cfg, dur, FPS, STAGE_SVG, TARGET)
            self.assertEqual(len(W.frames(q)), (floor + 1) * 4)
        with self.assertRaises(ValueError):
            W.plan(DEFAULT_CFG, 0.05, FPS, STAGE_SVG, TARGET)

    def test_frame_floor_and_wave_must_finish_inside_the_beat(self):
        """span / width 不得小于一帧；span+width 不得越过拍长。"""
        with self.assertRaisesRegex(ValueError, "小于一帧"):
            W.plan(dict(DEFAULT_CFG, span=0.02), DURATION, FPS, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "小于一帧"):
            W.plan(dict(DEFAULT_CFG, width=0.02), DURATION, FPS, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "超过拍长"):
            W.plan(dict(DEFAULT_CFG, span=1.0), DURATION, FPS, STAGE_SVG, TARGET)
        q = W.plan(dict(DEFAULT_CFG, span=1.0), 2.0, FPS, STAGE_SVG, TARGET)
        self.assertTrue(W.is_well_formed(q))


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 cells / origin / axis / 幅度 / 旋钮逐个拒。"""
        bad = [
            {"cells": ["#d3"]}, {"cells": ["#d3"] * 13},
            {"cells": ["#d3", "#d3", "#d1", "#d4"]},
            {"cells": [".d3", "#d2", "#d1", "#d4"]}, {"cells": "nope"},
            {"cells": ["#d3", "#d2", "#d1", "#cam"]},
            {"origin": "#d3"}, {"origin": "#cam"}, {"origin": "nope"},
            {"axis": "z"}, {"axis": "X"},
            {"amplitude": 0}, {"amplitude": float("nan")},
            {"span": 0}, {"span": -0.1}, {"width": 0},
            {"falloff": -0.1}, {"falloff": W.FALLOFF_MAX + 0.1},
            {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                W.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"span": 0.9, "width": 0.2}, {"falloff": 0},
                    {"falloff": W.FALLOFF_MAX}, {"cells": ["#d3", "#d2"]},
                    {"axis": "x"}, {"axis": "radial"}):
            with self.subTest(cfg=cfg):
                W.validate(dict(DEFAULT_CFG, **cfg))

    def test_wave_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -80}},
                      {"drag": {"chain": ["#d3", "#d2"], "axis": "y",
                                "distance": -80}},
                      {"step": {"axis": "y", "distance": -80}},
                      {"organic": {"amplitude": 12}},
                      {"trail": {"ghosts": ["#d1", "#d2"], "axis": "x",
                                 "distance": 320}},
                      {"flow": {"riders": ["#d3", "#d2"], "path": "#ep",
                                "distance": 80}},
                      {"lens": {"cells": ["#d3", "#d2"], "glass": "#ep",
                                "to": "#d1", "amplitude": 40}},
                      {"swarm": {"cells": ["#d3", "#d2", "#d1", "#d4"],
                                 "amplitude": 40}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_ease_camera_and_self_origin_rejected(self):
        """一条波不按序号错峰、不重放、不吃 GSAP 缓动；相机层与自指震中当场说清。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True},
                      {"ease": "power3.out"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam", "wave": dict(DEFAULT_CFG)})
        with self.assertRaisesRegex(ValueError, "不能同时是 cells"):
            contract(step_with(origin="#d1"))
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            contract({"at": 0, "target": "#d2", "wave": dict(DEFAULT_CFG)})

    def test_default_html_has_no_wave_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(wave_sets(render()), [])
        for cfg in ({"axis": "z"}, {"cells": ["#d3"]},
                    {"falloff": 3.0}, {"width": 0.02}, {"span": 1.0}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=wave_images(cfg=cfg))
        with self.assertRaisesRegex(ValueError, "找不到"):
            render(images=wave_images(svg=STAGE_SVG.replace('id="d2"', 'id="gone"')))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐格选择器与逐帧变量全部等于 `_wave.frames`。"""
        html = render(images=wave_images())
        got = wave_sets(html)
        want = W.frames(make_plan())
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel in OWN_SELECTORS for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wsel, wv), (gsel, gv, gt) in zip(want, got):
            self.assertEqual(gsel, f"#img-seg-a {wsel}")
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gv, wv)
        self.assertTrue(all(v == 0.0 for v in got[0][1].values()))
        self.assertTrue(all(v == 0.0 for v in got[-1][1].values()))

    def test_two_aspects_and_repeatability(self):
        """两画幅各验一遍，且同一输入两次生成字节相同（几何只在生成期读）。"""
        imgs = wave_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            self.assertEqual(len(wave_sets(html)), len(W.frames(make_plan())))

    def test_knobs_drive_product(self):
        """七个旋钮各自真的改产物，不是写了等于没写。"""
        base = [v for _, v, _ in wave_sets(render(images=wave_images()))]
        stronger = [v for _, v, _ in wave_sets(render(images=wave_images(
            cfg={"amplitude": -80})))]
        self.assertGreater(max(abs(v["y"]) for v in stronger),
                           max(abs(v["y"]) for v in base))
        flat = [v for _, v, _ in wave_sets(render(images=wave_images(
            cfg={"falloff": 0})))]
        self.assertGreater(max(abs(v["y"]) for v in flat),
                           max(abs(v["y"]) for v in base))
        for cfg in ({"span": 0.7}, {"width": 0.5}, {"axis": "x"},
                    {"axis": "radial"}):
            with self.subTest(cfg=cfg):
                other = [v for _, v, _ in wave_sets(render(images=wave_images(cfg=cfg)))]
                self.assertNotEqual(base, other)
        radial = [v for _, v, _ in wave_sets(render(images=wave_images(
            cfg={"axis": "radial"})))]
        self.assertTrue(all("x" in v and "y" in v for v in radial))
        self.assertTrue(W.is_well_formed(make_plan({"axis": "radial"})))


class CarryContract(unittest.TestCase):
    def test_carry_explicitly_recognises_wave_and_leaves_no_residual(self):
        """收尾 = 起始：_settle_step 显式认这一拍并返回空态，页界零残留。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        self.assertEqual(W.settle_state(DEFAULT_CFG), [])
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        baked = ET.fromstring(out)
        for gid in ("d1", "d2", "d3", "d4"):
            with self.subTest(gid=gid):
                el = baked.find(".//*[@id='%s']" % gid)
                self.assertFalse((el.get("transform") or "").strip())
                self.assertNotIn("opacity:0", (el.get("style") or ""))


if __name__ == "__main__":
    unittest.main()
