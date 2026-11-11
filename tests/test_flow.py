#!/usr/bin/env python3
"""流线档 `flow`（画出来的 path → 弧长场 → 相对位移）的契约与**产物级**判据。

与 `test_wave.py` / `test_trail.py` 同一条方法论：判据尽量从生成的 HTML 里抠
补间来读，不在 Python 侧另写一份「应该生成什么」的实现。唯一的例外是
`_flow.plan`——它是契约层、渲染端、生成期体检、接续烘焙共用的那份真源，
测试拿它跟产物**对账**，而不是拿它替产物背书。

本档独有的两条判据：
- **改图即改编排**：挪动一个元素，它在曲线上的弧长坐标 sᵢ 跟着变，产物里
  只有它的位移序列变；改 path 的一个控制点，整组元素的轨迹一起变。相位没有
  第二份真源。
- **接续落点是几何量**：`_stage_carry` 必须读这一页的 SVG 才能逐元素折出
  translate，四个元素的落点互不相同，不是 `drag` 那种能凭配置拼出的常量。
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
import _flow as F
import _stage_carry as SC
import _animate as A
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"riders": ["#d4", "#d1", "#d3", "#d2"], "path": "#track",
               "distance": 180, "direction": "forward", "taper": 0.45}
TARGET = "#d4"
DURATION = 1.2
FPS = 24
# 一条真的会拐两次的 S 曲线；四个元素落在弧长约 8% / 30% / 58% / 82% 处，
# 切线角分别约 -41° / +37° / -54° / +48°——`riders` 顺序**故意不按弧长**
# （#d4 排第一却是第三个起跑），用来钉住「相位听几何的话，不听序号的话」。
PATH_D = "M 40 360 C 200 40 380 680 560 300 C 700 80 820 120 940 420"
STAGE_SVG = ('<svg viewBox="0 0 980 735">'
             '<path id="track" d="' + PATH_D + '"/>'
             '<rect id="d3" x="90" y="272" width="20" height="20"/>'
             '<rect id="d1" x="324" y="375" width="20" height="20"/>'
             '<rect id="d4" x="581" y="245" width="20" height="20"/>'
             '<rect id="d2" x="824" y="213" width="20" height="20"/>'
             "</svg>")
# 只把 #d1 往曲线前段挪（path 一个字不改）：其余元素的弧长与位移必须一格不动。
MOVED_SVG = STAGE_SVG.replace('id="d1" x="324" y="375"',
                              'id="d1" x="230" y="290"')
# 只改曲线的一个控制点：整条弧长表、每个元素的落点都得跟着变。
RESHAPED_SVG = STAGE_SVG.replace("C 200 40 380 680 560 300",
                                 "C 200 120 380 600 560 300")
# 不转弯的曲线：所有元素处的切线平行，这一拍根本不是流。
LINE_SVG = STAGE_SVG.replace('d="' + PATH_D + '"', 'd="M 40 360 L 940 360"')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<path id="track" d="' + PATH_D + '"/>'
             '<rect id="d3" x="90" y="272" width="20" height="20"/>'
             '<rect id="d1" x="324" y="375" width="20" height="20"/>'
             '<rect id="d4" x="581" y="245" width="20" height="20"/>'
             '<rect id="d2" x="824" y="213" width="20" height="20"/>'
             "</svg>")
FLOW_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #[A-Za-z_][\w-]*)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
OWN_SELECTORS = {"#img-seg-a " + s for s in DEFAULT_CFG["riders"]}


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def flow_images(cfg=None, svg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": TARGET, "duration": DURATION,
            "flow": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg",
                     "inline_svg": svg or STAGE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def flow_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in FLOW_SET.finditer(html)
            if m.group(1) in OWN_SELECTORS]


def step_with(**kw):
    return {"at": 0, "target": TARGET, "flow": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})


def make_plan(cfg=None, svg=None):
    return F.plan(dict(DEFAULT_CFG, **(cfg or {})), DURATION, FPS,
                  svg or STAGE_SVG, TARGET)


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = make_plan()
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [F.state_at(t, p, 2) for t in ts]
        self.assertEqual(forward, [F.state_at(t, p, 2) for t in reversed(ts)][::-1])
        F.state_at(0.31, p, 1)
        self.assertEqual(forward, [F.state_at(t, p, 2) for t in ts])
        self.assertTrue(F.is_well_formed(p))
        self.assertEqual(F.diagnose(p), [])
        # 端点之外被夹回端点，seek 跨过窗口不会冒出一段凭空的位移
        self.assertEqual(F.state_at(-1.0, p, 2), F.state_at(0.0, p, 2))
        self.assertEqual(F.state_at(DURATION + 1.0, p, 2),
                         F.state_at(DURATION, p, 2))

    def test_displacement_is_relative_not_snapped_to_path(self):
        """定义性判据：Δ(0)=0（段首不跳），且 Δ 不是把元素搬到曲线上。"""
        p = make_plan()
        g = p["geometry"]
        for i in range(len(p["selectors"])):
            st = F.start(p, i)
            self.assertTrue(all(v == 0.0 for v in st.values()), (i, st))
            # 元素中心与它在曲线上的投影点并不重合：若写成 P(S)-cᵢ，段首
            # 第一帧就会有一串非零位移，正是「段内静默错」。
            c = g["centers"][i]
            proj = F.point_at(g, g["arcs"][i])
            self.assertGreater(math.hypot(c[0] - proj[0], c[1] - proj[1]), 0.0)
        fr = F.frames(p)
        self.assertTrue(all(v == 0.0 for _t, _s, var in fr[:4]
                            for v in var.values()))

    def test_envelope_starts_moving_and_stops_cleanly(self):
        """E(0)=0、E(1)=1、起步有位移、到位导数归零；不收拍级 ease。"""
        self.assertEqual(F.envelope(0.0), 0.0)
        self.assertEqual(F.envelope(1.0), 1.0)
        self.assertGreater(F.envelope(0.01), 0.0)
        self.assertAlmostEqual(F.envelope(1.0) - F.envelope(0.99), 0.0, places=3)
        p = make_plan()
        for i in range(len(p["selectors"])):
            self.assertAlmostEqual(F.travel(p, 0.0, i), 0.0, places=9)
            self.assertAlmostEqual(F.travel(p, DURATION, i),
                                   p["distances"][i], places=9)
            seq = [F.travel(p, DURATION * k / 24.0, i) for k in range(25)]
            self.assertTrue(all(b > a for a, b in zip(seq, seq[1:])))
        # 包络是生成期算死的，写 ease 在契约层就拒
        with self.assertRaises(ValueError):
            contract(dict(step_with(), ease="power3.out"))

    def test_taper_and_distance_scale_each_rider(self):
        """沿弧长衰减：最近的元素精确走 distance，远端按 exp(-taper·q) 递减。"""
        p = make_plan()
        order = sorted(range(4), key=lambda i: p["geometry"]["arcs"][i])
        ds = [p["distances"][i] for i in order]
        self.assertAlmostEqual(ds[0], DEFAULT_CFG["distance"], places=6)
        self.assertTrue(all(a >= b - 1e-9 for a, b in zip(ds, ds[1:])))
        self.assertAlmostEqual(ds[-1],
                               DEFAULT_CFG["distance"]
                               * math.exp(-DEFAULT_CFG["taper"]), places=5)
        flat = make_plan({"taper": 0})
        self.assertTrue(all(abs(d - DEFAULT_CFG["distance"]) < 1e-9
                            for d in flat["distances"]))
        with self.assertRaises(ValueError):
            F.validate(dict(DEFAULT_CFG, taper=F.FLOW_TAPER_MAX + 0.1))


class FieldContract(unittest.TestCase):
    def test_arc_order_follows_geometry_not_rider_order(self):
        """与 stagger / drag 的分界：起跑弧长按投影排，与 riders 写入顺序无关。"""
        p = make_plan()
        arcs = p["geometry"]["arcs"]
        self.assertFalse(arcs == sorted(arcs),
                         "这组 riders 故意不按弧长排，相位不该跟着序号走")
        order = sorted(range(4), key=lambda i: arcs[i])
        self.assertTrue(all(arcs[i] < arcs[j] for i, j in zip(order, order[1:])))
        self.assertEqual(p["selectors"], DEFAULT_CFG["riders"],
                         "写入顺序仍是作者给的 riders 序")
        # 落点由曲线算：四个元素的收尾位移互不相同
        lands = [F.landing(p, i) for i in range(4)]
        self.assertEqual(len({(v["x"], v["y"]) for v in lands}), 4)

    def test_tangent_field_is_not_a_uniform_translation(self):
        """至少两处切线不同：全平行时这一拍根本不是流，按文件拦。"""
        p = make_plan()
        tangents = p["geometry"]["tangents"]
        spread = max(math.hypot(u[0] - v[0], u[1] - v[1])
                     for u in tangents for v in tangents)
        self.assertGreaterEqual(spread,
                                2 * math.sin(math.radians(F.FLOW_MIN_TURN_DEG) / 2))
        lands = [F.landing(p, i) for i in range(4)]
        angles = [math.degrees(math.atan2(v["y"], v["x"])) for v in lands]
        self.assertGreater(max(angles) - min(angles), 20.0)
        with self.assertRaisesRegex(ValueError, "没有转弯|整块平移"):
            F.geometry(LINE_SVG, DEFAULT_CFG)

    def test_changing_svg_geometry_changes_the_choreography(self):
        """本档独有的产物级判据：改图即改编排，相位没有第二份真源。"""
        base = make_plan()
        moved = make_plan(svg=MOVED_SVG)
        self.assertNotEqual(base["geometry"]["arcs"][1],
                            moved["geometry"]["arcs"][1])
        for i in (0, 2, 3):
            self.assertAlmostEqual(base["geometry"]["arcs"][i],
                                   moved["geometry"]["arcs"][i], places=9)
        self.assertEqual(base["geometry"]["length"], moved["geometry"]["length"])
        # 产物里只有 #d1 那一列变了
        def column(html, sel):
            return [var for s, var, _t in flow_sets(html) if s.endswith(sel)]
        a, b = render(images=flow_images()), render(images=flow_images(svg=MOVED_SVG))
        self.assertNotEqual(column(a, " #d1"), column(b, " #d1"))
        for sel in (" #d4", " #d3", " #d2"):
            self.assertEqual(column(a, sel), column(b, sel))
        # 改曲线的一个控制点：整组一起变
        reshaped = make_plan(svg=RESHAPED_SVG)
        self.assertNotEqual(base["geometry"]["length"],
                            reshaped["geometry"]["length"])
        self.assertNotEqual([F.landing(base, i) for i in range(4)],
                            [F.landing(reshaped, i) for i in range(4)])

    def test_geometry_red_lines_are_checked_per_file(self):
        """落空 / 非 path / 不收的命令 / 同弧长 / 自带 transform，生成期就说清。"""
        with self.assertRaisesRegex(ValueError, "找不到"):
            F.geometry(STAGE_SVG.replace('id="d1"', 'id="gone"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "带 d 的 <path>"):
            F.geometry(STAGE_SVG.replace('<path id="track"',
                                        '<rect id="track"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "只收 M/L/H/V/C/Q/Z"):
            F.geometry(STAGE_SVG.replace(PATH_D, "M 40 360 A 100 100 0 0 1 940 420"),
                       DEFAULT_CFG)
        same = (STAGE_SVG.replace('x="324" y="375"', 'x="90" y="272"')
                         .replace('x="581" y="245"', 'x="90" y="272"')
                         .replace('x="824" y="213"', 'x="90" y="272"'))
        with self.assertRaisesRegex(ValueError, "同一个弧长"):
            F.geometry(same, DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "自带 transform"):
            F.geometry(STAGE_SVG.replace('id="d1" x="324"',
                                        'id="d1" transform="translate(3,4)" x="324"'),
                       DEFAULT_CFG)
        # 相对命令与隐式重复要和绝对写法量出同一条曲线
        rel = "m 40 360 c 160 -320 340 320 520 -60 c 140 -220 260 -180 380 120"
        abs_pts = F._polyline(PATH_D, "abs")
        rel_pts = F._polyline(rel, "rel")
        self.assertEqual(len(abs_pts), len(rel_pts))
        for a, b in zip(abs_pts, rel_pts):
            self.assertAlmostEqual(a[0], b[0], places=6)
            self.assertAlmostEqual(a[1], b[1], places=6)
        # 生成期体检也按文件拦：落空的元素不会静默没动画
        errs, _ = A.check_animate(STAGE_SVG.replace('id="d1"', 'id="gone"'),
                                  {"steps": [step_with()]}, "seg-a",
                                  full_bleed=True)
        self.assertTrue(any("flow.riders" in e for e in errs))


class GridContract(unittest.TestCase):
    def test_sample_grid_is_deterministic_and_bounded(self):
        """采样网格：严格递增、覆盖首尾、条数 = (帧数+1)×元素数。"""
        p = make_plan()
        fr = F.frames(p)
        self.assertEqual(fr, F.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(ts))
        own = [t for t, sel, _ in fr if sel == TARGET]
        self.assertEqual(own, sorted(set(own)),
                         "同一 0.01s 格撞车必须去重")
        self.assertEqual(len(fr), (F.frame_count(p) + 1) * 4)
        for dur, floor in ((0.2, _field.SAMPLES_MIN), (20.0, _field.SAMPLES_MAX)):
            q = F.plan(DEFAULT_CFG, dur, FPS, STAGE_SVG, TARGET)
            self.assertEqual(len(F.frames(q)), (floor + 1) * 4)
        with self.assertRaises(ValueError):
            F.plan(DEFAULT_CFG, 0.05, FPS, STAGE_SVG, TARGET)

    def test_flow_must_finish_inside_the_path(self):
        """流不许被曲线末端截断；backward 也不许退出起点。"""
        with self.assertRaisesRegex(ValueError, "流走出了曲线"):
            F.plan(dict(DEFAULT_CFG, distance=900), DURATION, FPS,
                   STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "流走出了曲线起点"):
            F.plan(dict(DEFAULT_CFG, direction="backward", distance=180),
                   DURATION, FPS, STAGE_SVG, TARGET)
        back = F.plan(dict(DEFAULT_CFG, direction="backward", distance=50),
                      DURATION, FPS, STAGE_SVG, TARGET)
        self.assertTrue(F.is_well_formed(back))
        self.assertLess(F.landing(back, 0)["x"], 0.0)


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 riders / path / distance / 旋钮逐个拒。"""
        bad = [
            {"riders": ["#d4"]}, {"riders": ["#d4"] * 13},
            {"riders": ["#d4", "#d4", "#d1", "#d3"]},
            {"riders": [".d4", "#d1", "#d3", "#d2"]}, {"riders": "nope"},
            {"riders": ["#d4", "#d1", "#d3", "#cam"]},
            {"path": "#d4"}, {"path": "#cam"}, {"path": "nope"},
            {"path": ".track"},
            {"distance": 0}, {"distance": F.FLOW_MIN_DISTANCE - 1},
            {"distance": float("nan")},
            {"direction": "sideways"}, {"direction": "Forward"},
            {"taper": -0.1}, {"taper": F.FLOW_TAPER_MAX + 0.1},
            {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                F.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"distance": 260}, {"taper": 0},
                    {"taper": F.FLOW_TAPER_MAX}, {"riders": ["#d4", "#d1"]},
                    {"direction": "backward"}):
            with self.subTest(cfg=cfg):
                F.validate(dict(DEFAULT_CFG, **cfg))

    def test_flow_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -80}},
                      {"drag": {"chain": ["#d4", "#d1"], "axis": "y",
                                "distance": -80}},
                      {"step": {"axis": "y", "distance": -80}},
                      {"organic": {"amplitude": 12}},
                      {"trail": {"ghosts": ["#d1", "#d2"], "axis": "x",
                                 "distance": 320}},
                      {"wave": {"cells": ["#d4", "#d1"], "origin": "#d3",
                                "axis": "y", "amplitude": -36}},
                      {"lens": {"cells": ["#d4", "#d1"], "glass": "#track",
                                "to": "#d2", "amplitude": 40}},
                      {"swarm": {"cells": ["#d4", "#d1", "#d3", "#d2"],
                                 "amplitude": 40}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_ease_camera_and_self_path_rejected(self):
        """一条流不按序号错峰、不重放、不吃 GSAP 缓动；相机层与自指曲线当场说清。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True},
                      {"ease": "power3.out"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam", "flow": dict(DEFAULT_CFG)})
        with self.assertRaisesRegex(ValueError, "不能同时是 riders"):
            contract(step_with(path="#d1"))
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            contract({"at": 0, "target": "#d1", "flow": dict(DEFAULT_CFG)})

    def test_default_html_has_no_flow_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(flow_sets(render()), [])
        for cfg in ({"direction": "z"}, {"riders": ["#d4"]},
                    {"taper": 3.0}, {"distance": 4}, {"distance": 900}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=flow_images(cfg=cfg))
        with self.assertRaisesRegex(ValueError, "找不到"):
            render(images=flow_images(svg=STAGE_SVG.replace('id="d2"', 'id="gone"')))
        with self.assertRaises(ValueError):
            render(images=flow_images(svg=LINE_SVG))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐元素选择器与逐帧变量全部等于 `_flow.frames`。"""
        html = render(images=flow_images())
        got = flow_sets(html)
        want = F.frames(make_plan())
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel in OWN_SELECTORS for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wsel, wv), (gsel, gv, gt) in zip(want, got):
            self.assertEqual(gsel, f"#img-seg-a {wsel}")
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gv, wv)
        self.assertTrue(all(v == 0.0 for v in got[0][1].values()))
        self.assertTrue(any(abs(v) > 0.0 for v in got[-1][1].values()))

    def test_two_aspects_and_repeatability(self):
        """两画幅各验一遍，且同一输入两次生成字节相同（几何只在生成期读）。"""
        imgs = flow_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            self.assertEqual(len(flow_sets(html)), len(F.frames(make_plan())))

    def test_knobs_drive_product(self):
        """五个旋钮各自真的改产物，不是写了等于没写。"""
        base = [v for _, v, _ in flow_sets(render(images=flow_images()))]
        # backward 要缩短 distance：最靠前的元素离曲线起点只有约 99 个弧长单位，
        # 退 180 会撞上「流必须在曲线内走完」那条红线（GridContract 单独钉着）。
        for cfg in ({"distance": 260}, {"taper": 0},
                    {"direction": "backward", "distance": 50},
                    {"riders": ["#d4", "#d1", "#d3"]}):
            with self.subTest(cfg=cfg):
                other = [v for _, v, _ in flow_sets(render(images=flow_images(cfg=cfg)))]
                self.assertNotEqual(base, other)
        far = [v for _, v, _ in flow_sets(render(images=flow_images(
            cfg={"distance": 260})))]
        self.assertGreater(max(abs(v["x"]) + abs(v["y"]) for v in far),
                           max(abs(v["x"]) + abs(v["y"]) for v in base))
        self.assertTrue(F.is_well_formed(make_plan({"direction": "backward",
                                                    "distance": 50})))


class CarryContract(unittest.TestCase):
    def test_carry_folds_each_geometric_landing_not_a_constant(self):
        """收尾位是几何量：四个元素各折出自己的 translate，接续页零残留。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        expected = F.settle_state(DEFAULT_CFG, CARRY_SVG, TARGET)
        self.assertEqual(len(expected), 4)
        self.assertEqual(len({(v["x"], v["y"]) for _s, v in expected}), 4,
                         "落点必须互不相同：常量折叠会在页界把整组弹回原位")
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        baked = ET.fromstring(out)
        for sel, var in expected:
            with self.subTest(sel=sel):
                el = baked.find(".//*[@id='%s']" % sel[1:])
                tr = el.get("transform") or ""
                self.assertIn("translate(", tr)
                self.assertIn("%.6f" % var["x"], tr)
        # 没写 flow 的页不该凭空多出补间
        out2, notes2 = SC.bake_settled_state(
            raw, [{"at": 0, "target": "#d4", "to": {"opacity": 1}}])
        self.assertEqual(notes2, [])
        self.assertNotIn("translate(", out2)


if __name__ == "__main__":
    unittest.main()