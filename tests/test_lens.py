#!/usr/bin/env python3
"""透镜档 `lens`（场心扫过 → 瞬时方向场 → 折射）的契约与**产物级**判据。

与 `test_wave.py` / `test_flow.py` 同一条方法论：判据尽量从生成的 HTML 里抠
补间来读，不在 Python 侧另写一份「应该生成什么」的实现。唯一的例外是
`_lens.plan`——它是契约层、渲染端、生成期体检、接续烘焙共用的那份真源，
测试拿它跟产物**对账**，而不是拿它替产物背书。

本档独有的三条判据：
- **方向是 t 的函数**：同一个元素在拍内的位移方向角必须真的转过 ≥ 6°。
  既有十三档里，方向都由配置或固定震中在段首一次定死，这条不可能成立。
- **改图即改编排**：场心两端、影响半径、元素中心全从 SVG 量出。挪动一个
  元素，产物里只有它的位移序列变；改透镜元素的包围盒，整组峰值一起变。
- **端点精确归零**：`u(t)=sin²(πt/D)` 与紧支撑核让每个元素收尾精确回原位，
  跨段接续无残留，但 `_stage_carry` 仍必须**显式认这一拍**。
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
import _lens as L
import _animate as A
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"cells": ["#d1", "#d2", "#d3", "#d4"], "glass": "#glass",
               "to": "#to", "amplitude": 60}
TARGET = "#d1"
DURATION = 1.2
FPS = 24
# 透镜是一枚画在图上的圆（r=100 → R=100），场心从它中心 (150,367) 匀速走到
# #to 的中心 (830,367)。四个元素分列轨迹上下各 30 单位：既满足净空 ≥5，
# 又都在 R 之内会被真扫到。cells 顺序故意不按 x 排（#d2 在 #d3 左边却排前），
# 用来钉住「写入顺序是导演意图，位移场只听几何的话」。
STAGE_SVG = ('<svg viewBox="0 0 980 735">'
             '<circle id="glass" cx="150" cy="367" r="100"/>'
             '<circle id="to" cx="830" cy="367" r="6"/>'
             '<rect id="d1" x="340" y="327" width="20" height="20"/>'
             '<rect id="d2" x="420" y="387" width="20" height="20"/>'
             '<rect id="d3" x="480" y="327" width="20" height="20"/>'
             '<rect id="d4" x="560" y="387" width="20" height="20"/>'
             "</svg>")
# 只挪 #d2（玻璃与两个锚点一字不改）：其余元素的位移序列必须一格不动。
MOVED_SVG = STAGE_SVG.replace('id="d2" x="420" y="387"',
                              'id="d2" x="400" y="320"')
# 只改透镜自己的半径：影响半径 R 是唯一的幅度场真源，整组峰值都跟着变。
RESHAPED_SVG = STAGE_SVG.replace('id="glass" cx="150" cy="367" r="100"',
                                 'id="glass" cx="150" cy="367" r="80"')
# #d1 正好压在场心轨迹上：û=v/|v| 未定义且 K(0)=1 恰是峰值，生成期必须按文件拦。
ON_TRACK_SVG = STAGE_SVG.replace('id="d1" x="340" y="327"',
                                 'id="d1" x="340" y="357"')
# 所有元素都远在轨迹延长线一侧：两端的 û 几乎平行，方向不转 → 这是 wave.radial。
FLAT_SVG = ('<svg viewBox="0 0 3000 735">'
            '<circle id="glass" cx="150" cy="367" r="100"/>'
            '<circle id="to" cx="830" cy="367" r="6"/>'
            '<rect id="d1" x="2000" y="327" width="20" height="20"/>'
            '<rect id="d2" x="2100" y="387" width="20" height="20"/>'
            '<rect id="d3" x="2200" y="327" width="20" height="20"/>'
            '<rect id="d4" x="2300" y="387" width="20" height="20"/>'
            "</svg>")
# 一个元素远在 R 之外：它整拍精确 0（紧支撑核边界外不是「很小」）。
FAR_SVG = STAGE_SVG.replace('id="d4" x="560" y="387"',
                            'id="d4" x="2000" y="327"')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<circle id="glass" cx="150" cy="367" r="100"/>'
             '<circle id="to" cx="830" cy="367" r="6"/>'
             '<rect id="d1" x="340" y="327" width="20" height="20"/>'
             '<rect id="d2" x="420" y="387" width="20" height="20"/>'
             '<rect id="d3" x="480" y="327" width="20" height="20"/>'
             '<rect id="d4" x="560" y="387" width="20" height="20"/>'
             "</svg>")
# 一条 lens 拍发射的是每帧每元素一条 tl.set，selector 各自带 #img-{sid} 作用域。
LENS_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #[A-Za-z_][\w-]*)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
OWN_SELECTORS = {"#img-seg-a " + s for s in DEFAULT_CFG["cells"]}


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def lens_images(cfg=None, svg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": TARGET, "duration": DURATION,
            "lens": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg",
                     "inline_svg": svg or STAGE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def lens_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in LENS_SET.finditer(html)
            if m.group(1) in OWN_SELECTORS]


def step_with(**kw):
    return {"at": 0, "target": TARGET, "lens": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})


def make_plan(cfg=None, svg=None):
    return L.plan(dict(DEFAULT_CFG, **(cfg or {})), DURATION, FPS,
                  svg or STAGE_SVG, TARGET)


def _angle(v):
    return math.degrees(math.atan2(v["y"], v["x"]))


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = make_plan()
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [L.state_at(t, p, 2) for t in ts]
        self.assertEqual(forward, [L.state_at(t, p, 2) for t in reversed(ts)][::-1])
        L.state_at(0.31, p, 1)
        self.assertEqual(forward, [L.state_at(t, p, 2) for t in ts])
        self.assertTrue(L.is_well_formed(p))
        self.assertEqual(L.diagnose(p), [])
        # 拍外夹回端点，seek 跨过窗口不会冒出一段凭空的折射
        self.assertEqual(L.state_at(-1.0, p, 2), L.state_at(0.0, p, 2))
        self.assertEqual(L.state_at(DURATION + 1.0, p, 2),
                         L.state_at(DURATION, p, 2))

    def test_displacement_direction_rotates_inside_one_beat(self):
        """本档的定义性判据：同一个元素的位移方向在这一拍之内改变。

        既有十三档的方向都在段首定死（wave.radial 由固定震中给出、flow 每个
        元素只走自己那一小段弧）。这里取全拍 41 个时刻，要求每个元素的方向角
        跨度 ≥ LENS_MIN_TURN_DEG，且位移不是同一向量的等比缩放。
        """
        p = make_plan()
        for i in range(len(p["selectors"])):
            with self.subTest(cell=p["selectors"][i]):
                live = [L.state_at(DURATION * k / 40, p, i) for k in range(41)]
                live = [v for v in live if math.hypot(v["x"], v["y"]) > 1.0]
                self.assertGreaterEqual(len(live), 4)
                angles = [_angle(v) for v in live]
                self.assertGreaterEqual(max(angles) - min(angles),
                                       L.LENS_MIN_TURN_DEG)
                # 不是同一方向上的等比缩放：相邻两个活帧的夹角也要有非零量
                cross = [abs(v0["x"] * v1["y"] - v0["y"] * v1["x"])
                         / (math.hypot(v0["x"], v0["y"])
                            * math.hypot(v1["x"], v1["y"]))
                         for v0, v1 in zip(live, live[1:])]
                self.assertGreater(max(cross), 0.05)

    def test_endpoints_return_exactly_to_rest(self):
        """u(0)=u(D)=0：段首段尾每个元素精确回原位，没有任何 -0.0 残差。"""
        p = make_plan()
        for i in range(len(p["selectors"])):
            for st in (L.start(p, i), L.landing(p, i)):
                self.assertTrue(all(v == 0.0 for v in st.values()), (i, st))
        fr = L.frames(p)
        for _t, sel, var in fr[:4] + fr[-4:]:
            self.assertTrue(all(v == 0.0 for v in var.values()), sel)
        # 真正要拦的是负零：json.dumps 会把它吐成 `-0.0`，与 `0.0` 逐条对账
        # 时不相等。用 copysign 判，别拿字符串子串判（`-0.000993` 会误报）。
        for _t, _sel, var in fr:
            for v in var.values():
                if v == 0.0:
                    self.assertGreater(math.copysign(1.0, v), 0.0)

    def test_kernel_envelope_compact_support_and_modes_are_exact_opposites(self):
        """K(q) 在 q=1 值与导数都归零；圈外精确 0；bulge / pinch 精确反向。"""
        p = make_plan()
        self.assertEqual(L.kernel(0.0), 1.0)
        self.assertEqual(L.kernel(1.0), 0.0)
        self.assertLess(L.kernel(0.999), 1e-5, "q=1 处一阶导数不归零会读成一闪")
        self.assertEqual(L.envelope(0.0, p), 0.0)
        self.assertEqual(L.envelope(DURATION, p), 0.0)
        self.assertAlmostEqual(L.envelope(DURATION / 2, p), 1.0, places=12)
        far = make_plan(svg=FAR_SVG)
        self.assertTrue(all(L.state_at(DURATION * k / 40, far, 3)
                            == {"x": 0.0, "y": 0.0} for k in range(41)),
                        "影响半径外的元素必须精确 0，不是「很小」")
        flipped = dict(p, cfg=dict(p["cfg"], mode="pinch"))
        for i in range(len(p["selectors"])):
            for t in (0.25, 0.5, 0.75):
                a, b = L.state_at(t, p, i), L.state_at(t, flipped, i)
                self.assertAlmostEqual(a["x"] + b["x"], 0.0, places=9)
                self.assertAlmostEqual(a["y"] + b["y"], 0.0, places=9)
        self.assertEqual(L.diagnose(L.plan(dict(DEFAULT_CFG, mode="pinch"),
                                           DURATION, FPS, STAGE_SVG, TARGET)), [])


class GeometryContract(unittest.TestCase):
    def test_geometry_comes_only_from_svg(self):
        """场心两端 / R / 元素中心全从 SVG 量出，契约里一个坐标都不写。"""
        p = make_plan()
        g = p["geometry"]
        self.assertEqual(g["L0"], (150.0, 367.0))
        self.assertEqual(g["L1"], (830.0, 367.0))
        self.assertEqual(g["radius"], 100.0)
        self.assertEqual(g["travel"], 680.0)
        self.assertEqual(g["centers"], [(350.0, 337.0), (430.0, 397.0),
                                        (490.0, 337.0), (570.0, 397.0)])
        self.assertEqual(p["selectors"], DEFAULT_CFG["cells"])
        self.assertGreaterEqual(max(g["turns"]), L.LENS_MIN_TURN_DEG)
        self.assertEqual(L.lens_center(0.0, p), g["L0"])
        self.assertEqual(L.lens_center(DURATION, p), g["L1"])
        self.assertEqual(L.lens_center(DURATION / 2, p),
                         ((150.0 + 830.0) / 2, 367.0))
        reshaped = make_plan(svg=RESHAPED_SVG)
        self.assertEqual(reshaped["geometry"]["radius"], 80.0)
        self.assertEqual(reshaped["geometry"]["L0"], g["L0"])

    def test_degenerate_layouts_are_rejected_at_generation_time(self):
        """方向不转 / 压在轨迹上 / 行程不够 / R=0 / 锚点落空，按文件拦。"""
        with self.assertRaisesRegex(ValueError, "方向转角|不在透镜轨迹的侧面"):
            L.geometry(FLAT_SVG, DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "离透镜轨迹"):
            L.geometry(ON_TRACK_SVG, DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "行程"):
            L.geometry(STAGE_SVG.replace('id="to" cx="830"',
                                         'id="to" cx="152"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "R=0|退化成一个点"):
            L.geometry(STAGE_SVG.replace('r="100"', 'r="0"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "找不到"):
            L.geometry(STAGE_SVG.replace('id="d2"', 'id="gone"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "找不到"):
            L.geometry(STAGE_SVG.replace('id="glass"', 'id="gone"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "不是 <svg>|解析失败"):
            L.geometry("<p>no svg</p>", DEFAULT_CFG)

    def test_health_check_rejects_dangling_lens_ids(self):
        """生成期静态体检也按文件量一次：锚点/元素落空不会静默少一片。"""
        errs, _ = A.check_animate(STAGE_SVG.replace('id="d2"', 'id="gone"'),
                                  {"steps": [step_with()]}, "seg-a",
                                  full_bleed=True)
        self.assertTrue(any("lens.cells" in e for e in errs), errs)
        errs2, _ = A.check_animate(ON_TRACK_SVG, {"steps": [step_with()]},
                                   "seg-a", full_bleed=True)
        self.assertTrue(any("离透镜轨迹" in e for e in errs2), errs2)

    def test_moving_one_cell_changes_only_its_product(self):
        """改图即改编排（一）：挪一个元素，产物里只有它的位移序列变。"""
        base, moved = make_plan(), make_plan(svg=MOVED_SVG)
        self.assertEqual(L.diagnose(moved), [])
        self.assertNotEqual(base["geometry"]["centers"][1],
                            moved["geometry"]["centers"][1])
        for i in (0, 2, 3):
            self.assertEqual([L.state_at(DURATION * k / 20, base, i)
                              for k in range(21)],
                             [L.state_at(DURATION * k / 20, moved, i)
                              for k in range(21)])
        a = lens_sets(render(images=lens_images()))
        b = lens_sets(render(images=lens_images(svg=MOVED_SVG)))
        self.assertEqual(len(a), len(b))
        untouched_a = [v for s, v, _ in a if s != "#img-seg-a #d2"]
        untouched_b = [v for s, v, _ in b if s != "#img-seg-a #d2"]
        self.assertEqual(untouched_a, untouched_b)
        self.assertNotEqual([v for s, v, _ in a if s == "#img-seg-a #d2"],
                            [v for s, v, _ in b if s == "#img-seg-a #d2"])

    def test_resizing_the_glass_changes_the_whole_field(self):
        """改图即改编排（二）：R 由透镜元素自己的包围盒量出，整组峰值跟着变。"""
        base, small = make_plan(), make_plan(svg=RESHAPED_SVG)
        self.assertEqual(L.diagnose(small), [])
        peaks_a = [max(math.hypot(*L.state_at(DURATION * k / 100, base, i).values())
                       for k in range(101)) for i in range(4)]
        peaks_b = [max(math.hypot(*L.state_at(DURATION * k / 100, small, i).values())
                       for k in range(101)) for i in range(4)]
        self.assertTrue(all(b < a for a, b in zip(peaks_a, peaks_b)),
                        (peaks_a, peaks_b))
        self.assertNotEqual(lens_sets(render(images=lens_images())),
                            lens_sets(render(images=lens_images(svg=RESHAPED_SVG))))


class GridContract(unittest.TestCase):
    def test_sample_grid_is_deterministic_and_bounded(self):
        """严格递增、覆盖首尾、条数 = (帧数+1)×元素数、写入顺序是 cells 序。"""
        p = make_plan()
        fr = L.frames(p)
        self.assertEqual(fr, L.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(ts))
        self.assertEqual([s for _, s, _ in fr[:4]], p["selectors"])
        self.assertEqual([s for _, s, _ in fr[-4:]], p["selectors"])
        self.assertEqual(set(fr[0][2]), {"x", "y"})
        self.assertEqual(len(fr), (L.frame_count(p) + 1) * 4)
        for t, sel, _ in fr:
            self.assertEqual(round(t, 2), t)
        for dur, floor in ((0.2, _field.SAMPLES_MIN), (20.0, _field.SAMPLES_MAX)):
            q = L.plan(DEFAULT_CFG, dur, FPS, STAGE_SVG, TARGET)
            self.assertEqual(len(L.frames(q)), (floor + 1) * 4)

    def test_too_short_a_beat_is_rejected(self):
        """帧时刻锁两位，短于此会让两帧撞进同一格、静默少一帧。"""
        with self.assertRaisesRegex(ValueError, "duration"):
            L.plan(DEFAULT_CFG, 0.05, FPS, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "fps"):
            L.plan(DEFAULT_CFG, DURATION, 0, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            L.plan(DEFAULT_CFG, DURATION, FPS, STAGE_SVG, "#d2")


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 cells / 锚点 / 幅度 / 旋钮逐个拒。"""
        bad = [
            {"cells": ["#d1"]}, {"cells": ["#d1"] * 13},
            {"cells": ["#d1", "#d1", "#d3", "#d4"]},
            {"cells": [".d1", "#d2", "#d3", "#d4"]}, {"cells": "nope"},
            {"cells": ["#d1", "#d2", "#d3", "#cam"]},
            {"glass": "#d1"}, {"to": "#d1"},
            {"glass": "#cam"}, {"to": "#cam"},
            {"glass": "nope"}, {"to": "nope"},
            {"glass": "#to"},
            {"amplitude": 0}, {"amplitude": float("nan")},
            {"mode": "squeeze"}, {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                L.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"mode": "pinch"}, {"amplitude": -60},
                    {"cells": ["#d1", "#d2"]}):
            with self.subTest(cfg=cfg):
                self.assertEqual(L.validate(dict(DEFAULT_CFG, **cfg))["mode"],
                                 cfg.get("mode", L.LENS_DEFAULT_MODE))

    def test_lens_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -80}},
                      {"drag": {"chain": ["#d1", "#d2"], "axis": "y",
                                "distance": -80}},
                      {"step": {"axis": "y", "distance": -80}},
                      {"organic": {"amplitude": 12}},
                      {"trail": {"ghosts": ["#d1", "#d2"], "axis": "x",
                                 "distance": 320}},
                      {"wave": {"cells": ["#d1"], "origin": "#to",
                                "amplitude": -40}},
                      {"flow": {"riders": ["#d1"], "path": "#to",
                                "distance": 180}},
                      {"swarm": {"cells": ["#d1", "#d2", "#d3"],
                                 "amplitude": 40}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_ease_camera_and_target_alignment_rejected(self):
        """一个场不按序号错峰、不重放、不吃 GSAP 缓动；相机层与折射头当场说清。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True},
                      {"ease": "power3.out"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam", "lens": dict(DEFAULT_CFG)})
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            contract({"at": 0, "target": "#d2", "lens": dict(DEFAULT_CFG)})
        ok = contract(step_with())
        self.assertIn("lens", ok["seg2"]["animate"]["steps"][0])

    def test_default_html_has_no_lens_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(lens_sets(render()), [])
        for cfg in ({"mode": "squeeze"}, {"cells": ["#d1"]},
                    {"amplitude": 0}, {"oops": 1}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=lens_images(cfg=cfg))
        with self.assertRaisesRegex(ValueError, "找不到"):
            render(images=lens_images(svg=STAGE_SVG.replace('id="d2"', 'id="gone"')))
        with self.assertRaisesRegex(ValueError, "离透镜轨迹"):
            render(images=lens_images(svg=ON_TRACK_SVG))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐元素选择器与逐帧变量全部等于 `_lens.frames`。"""
        html = render(images=lens_images())
        got = lens_sets(html)
        want = L.frames(make_plan())
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

    def test_two_aspects_repeatability_and_knobs_drive_product(self):
        """两画幅各验、两次生成字节相同；四个旋钮各自真的改产物。"""
        imgs = lens_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            self.assertEqual(len(lens_sets(html)), len(L.frames(make_plan())))
        base = [v for _, v, _ in lens_sets(render(images=lens_images()))]
        for cfg in ({"amplitude": 120}, {"mode": "pinch"},
                    {"cells": ["#d1", "#d2", "#d3"]}):
            with self.subTest(cfg=cfg):
                self.assertNotEqual(base, [v for _, v, _ in lens_sets(
                    render(images=lens_images(cfg=cfg)))])
        stronger = [v for _, v, _ in lens_sets(render(images=lens_images(
            cfg={"amplitude": 120})))]
        mag = lambda vs: max(math.hypot(v["x"], v["y"]) for v in vs)
        self.assertGreater(mag(stronger), mag(base))
        pinch = [v for _, v, _ in lens_sets(render(images=lens_images(
            cfg={"mode": "pinch"})))]
        self.assertTrue(all(abs(x["x"] + y["x"]) < 1e-9
                            and abs(x["y"] + y["y"]) < 1e-9
                            for x, y in zip(base, pinch)))


class CarryContract(unittest.TestCase):
    def test_carry_explicitly_recognises_lens_and_leaves_no_residual(self):
        """收尾 = 起始：_settle_step 显式认这一拍并返回空态，页界零残留。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        self.assertEqual(L.settle_state(DEFAULT_CFG), [])
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
