#!/usr/bin/env python3
"""群集档 `swarm`（元素互为源 → 闭式邻域场 → 散开又收回）的契约与**产物级**判据。

与 `test_wave.py` / `test_flow.py` / `test_lens.py` 同一条方法论：判据尽量从生成的
HTML 里抠补间来读，不在 Python 侧另写一份「应该生成什么」的实现。唯一的例外是
`_swarm.plan`——它是契约层、渲染端、生成期体检、接续烘焙共用的那份真源，测试拿它
跟产物**对账**，而不是拿它替产物背书。

本档独有的三条判据：
- **元素互为源**：挪动一个元素，产物里**至少另一个元素**的位移序列也变。前面
  十四档的「改图即改编排」断言的恰好是「其余元素一格不动」，这里必须反过来。
- **没有状态**：方向由图上静态几何闭式算出，时间只经 sin² 窗口缩放；正序/逆序/
  乱序求值一致，峰值时刻是 D/2 的闭式解，逐帧 seek 可复现。
- **端点精确归零**：`u(t)=sin²(πt/D)` 让每个元素收尾精确回原位，跨段接续无残留，
  但 `_stage_carry` 仍必须**显式认这一拍**。
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
import _swarm as S
import _animate as A
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"cells": ["#d1", "#d2", "#d3", "#d4", "#d5", "#d6"],
               "amplitude": 60}
TARGET = "#d1"
DURATION = 1.2
FPS = 24
# 六个点散成一片，谁也不是锚点：没有震中、没有曲线、没有透镜。几何全从这六个
# rect 的包围盒中心量出来。cells 顺序故意不按 x 排（#d2 在 #d3 左边却排前），
# 用来钉住「写入顺序是导演意图，邻域场只听几何的话」。
CENTERS = [(300, 300), (500, 260), (680, 340),
           (360, 500), (560, 560), (760, 520)]
STAGE_SVG = ('<svg viewBox="0 0 980 735">'
             + ''.join(f'<rect id="d{i+1}" x="{x-10}" y="{y-10}" '
                       'width="20" height="20"/>'
                       for i, (x, y) in enumerate(CENTERS))
             + "</svg>")
# 只挪 #d2：其余五个元素的邻域里都有它，它们的位移方向必须跟着变。
MOVED_SVG = STAGE_SVG.replace('id="d2" x="490" y="250"',
                              'id="d2" x="530" y="300"')
# 把 #d6 甩到远处：它与所有人的 q→1，邻域权重 W→0，活跃度该显著衰减。
FAR_SVG = STAGE_SVG.replace('id="d6" x="750" y="510"',
                            'id="d6" x="2000" y="290"')
# 两个元素完全重合：û=d/q 未定义而分离核恰是峰值，生成期按文件拦。
OVERLAP_SVG = STAGE_SVG.replace('id="d2" x="490" y="250"',
                                'id="d2" x="290" y="290"')
# 所有中心重合：量不出 R。
COINCIDENT_SVG = STAGE_SVG
for _i, (_x, _y) in enumerate(CENTERS[1:], start=2):
    COINCIDENT_SVG = COINCIDENT_SVG.replace(
        f'id="d{_i}" x="{_x-10}" y="{_y-10}"', 'id="d%d" x="290" y="290"' % _i)
# 一个元素读不出几何包围盒（空 <g>）。
BAD_BOX_SVG = STAGE_SVG.replace(
    '<rect id="d2" x="490" y="250" width="20" height="20"/>',
    '<g id="d2"></g>')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             + ''.join(f'<rect id="d{i+1}" x="{x-10}" y="{y-10}" '
                       'width="20" height="20"/>'
                       for i, (x, y) in enumerate(CENTERS))
             + "</svg>")
# 一条 swarm 拍发射的是每帧每元素一条 tl.set，selector 各自带 #img-{sid} 作用域。
SWARM_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #[A-Za-z_][\w-]*)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
OWN_SELECTORS = {"#img-seg-a " + s for s in DEFAULT_CFG["cells"]}


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def swarm_images(cfg=None, svg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": TARGET, "duration": DURATION,
            "swarm": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg",
                     "inline_svg": svg or STAGE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def swarm_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in SWARM_SET.finditer(html)
            if m.group(1) in OWN_SELECTORS]


def step_with(**kw):
    return {"at": 0, "target": TARGET, "swarm": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})
def make_plan(cfg=None, svg=None):
    return S.plan(dict(DEFAULT_CFG, **(cfg or {})), DURATION, FPS,
                  svg or STAGE_SVG, TARGET)


def _angle(v):
    return math.degrees(math.atan2(v["y"], v["x"]))


def _series(p, i, n=97):
    return [S.state_at(DURATION * k / n, p, i) for k in range(n + 1)]


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = make_plan()
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [S.state_at(t, p, 2) for t in ts]
        self.assertEqual(forward, [S.state_at(t, p, 2) for t in reversed(ts)][::-1])
        S.state_at(0.31, p, 1)
        self.assertEqual(forward, [S.state_at(t, p, 2) for t in ts])
        self.assertTrue(S.is_well_formed(p))
        self.assertEqual(S.diagnose(p), [])
        self.assertEqual(S.state_at(-1.0, p, 2), S.state_at(0.0, p, 2))
        self.assertEqual(S.state_at(DURATION + 1.0, p, 2),
                         S.state_at(DURATION, p, 2))

    def test_endpoints_return_exactly_to_rest_and_peak_is_closed_form(self):
        """u(0)=u(D)=0：端点精确回原位、无 -0.0；峰值时刻是 D/2 的闭式解。"""
        p = make_plan()
        for i in range(len(p["selectors"])):
            for st in (S.start(p, i), S.landing(p, i)):
                self.assertTrue(all(v == 0.0 for v in st.values()), (i, st))
        fr = S.frames(p)
        for _t, _sel, var in fr[:6] + fr[-6:]:
            self.assertTrue(all(v == 0.0 for v in var.values()))
        for _t, _sel, var in fr:
            for v in var.values():
                if v == 0.0:
                    self.assertGreater(math.copysign(1.0, v), 0.0)
        for i in range(len(p["selectors"])):
            with self.subTest(cell=p["selectors"][i]):
                self.assertEqual(S.peak_time(p, i), DURATION / 2.0)
                peak = math.hypot(*S.state_at(DURATION / 2.0, p, i).values())
                for k in range(41):
                    s = S.state_at(DURATION * k / 40, p, i)
                    self.assertLessEqual(math.hypot(s["x"], s["y"]),
                                         peak + 1e-6)

    def test_kernels_and_envelope_are_compactly_supported(self):
        """K(1)=S(1)=0 且一阶导数归零；包络端点精确 0、中点 1。"""
        self.assertEqual(S.kernel(0.0), 1.0)
        self.assertEqual(S.kernel(1.0), 0.0)
        self.assertLess(S.kernel(0.999), 1e-5)
        self.assertEqual(S.separation_kernel(1.0), 0.0)
        self.assertEqual(S.separation_kernel(0.0), 1.0)
        self.assertLess(S.separation_kernel(0.999), 1e-5)
        p = make_plan()
        self.assertEqual(S.envelope(0.0, p), 0.0)
        self.assertEqual(S.envelope(DURATION, p), 0.0)
        self.assertAlmostEqual(S.envelope(DURATION / 2, p), 1.0, places=12)

    def test_modes_give_distinct_directions(self):
        """三个 mode 逐帧给不同方向，且各自都过生成期自查（旋钮不静默失效）。"""
        plans = {m: make_plan(cfg={"mode": m}) for m in ("flock", "scatter", "vortex")}
        for m, p in plans.items():
            with self.subTest(mode=m):
                self.assertEqual(S.diagnose(p), [])
        half = DURATION / 2
        for a, b in (("flock", "scatter"), ("flock", "vortex"),
                     ("scatter", "vortex")):
            with self.subTest(pair=(a, b)):
                diffs = [_angle(S.state_at(half, plans[a], i)) -
                         _angle(S.state_at(half, plans[b], i))
                         for i in range(len(DEFAULT_CFG["cells"]))]
                self.assertTrue(any(abs((d + 180) % 360 - 180) > 1.0 for d in diffs),
                                diffs)


class CouplingContract(unittest.TestCase):
    def test_moving_one_cell_changes_another_cells_product(self):
        """本档定义性判据：挪一个元素，至少另一个元素的位移序列也变。

        前面十四档的同类断言是「其余元素一格不动」；群集档里元素互为源，
        必须反过来，且要从 HTML 抠出的 tl.set 序列逐帧对账。
        """
        base, moved = make_plan(), make_plan(svg=MOVED_SVG)
        self.assertEqual(S.diagnose(moved), [])
        self.assertNotEqual(base["geometry"]["centers"][1],
                            moved["geometry"]["centers"][1])
        changed = [i for i in range(6) if _series(base, i) != _series(moved, i)]
        self.assertGreaterEqual(len(set(changed) - {1}), 1, changed)
        a = swarm_sets(render(images=swarm_images()))
        b = swarm_sets(render(images=swarm_images(svg=MOVED_SVG)))
        self.assertEqual(len(a), len(b))
        for sel in ("#img-seg-a #d1", "#img-seg-a #d3"):
            with self.subTest(sel=sel):
                self.assertNotEqual([v for s, v, _ in a if s == sel],
                                    [v for s, v, _ in b if s == sel])

    def test_radius_comes_only_from_all_centers(self):
        """R 是全体元素的最远间距，改一个点整组活跃度也跟着变。"""
        p = make_plan()
        g = p["geometry"]
        self.assertEqual(g["centers"], CENTERS)
        want = max(math.hypot(CENTERS[j][0] - CENTERS[i][0],
                              CENTERS[j][1] - CENTERS[i][1])
                   for i in range(6) for j in range(i + 1, 6))
        self.assertAlmostEqual(g["radius"], want, places=9)
        self.assertGreaterEqual(g["spread"], S.SWARM_MIN_SPREAD_DEG)
        far = make_plan(svg=FAR_SVG)
        self.assertGreater(far["geometry"]["radius"], g["radius"])
        self.assertNotEqual([v[2] for v in g["field"]],
                            [v[2] for v in far["geometry"]["field"]])

    def test_degenerate_layouts_are_rejected_at_generation_time(self):
        """重合 / 净空不足 / 落空 / 非 SVG / 读不出包围盒，按文件拦。"""
        with self.assertRaisesRegex(ValueError, "R=0|所有元素中心重合"):
            S.geometry(COINCIDENT_SVG, DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "只隔"):
            S.geometry(OVERLAP_SVG, DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "找不到"):
            S.geometry(STAGE_SVG.replace('id="d2"', 'id="gone"'), DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "不是 <svg>|解析失败"):
            S.geometry("<p>no svg</p>", DEFAULT_CFG)
        with self.assertRaisesRegex(ValueError, "读不出几何包围盒"):
            S.geometry(BAD_BOX_SVG, DEFAULT_CFG)

    def test_health_check_rejects_dangling_and_degenerate_svg(self):
        """生成期静态体检也按文件量一次：元素落空、挤成一团不会静默少一片。"""
        errs, _ = A.check_animate(STAGE_SVG.replace('id="d2"', 'id="gone"'),
                                  {"steps": [step_with()]}, "seg-a",
                                  full_bleed=True)
        self.assertTrue(any("swarm.cells" in e for e in errs), errs)
        errs2, _ = A.check_animate(OVERLAP_SVG, {"steps": [step_with()]},
                                   "seg-a", full_bleed=True)
        self.assertTrue(any("只隔" in e for e in errs2), errs2)


class GridContract(unittest.TestCase):
    def test_sample_grid_is_deterministic_and_bounded(self):
        """严格递增、覆盖首尾、条数 = (帧数+1)×元素数、写入顺序是 cells 序。"""
        p = make_plan()
        fr = S.frames(p)
        self.assertEqual(fr, S.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(ts))
        self.assertEqual([s for _, s, _ in fr[:6]], p["selectors"])
        self.assertEqual([s for _, s, _ in fr[-6:]], p["selectors"])
        self.assertEqual(set(fr[0][2]), {"x", "y"})
        self.assertEqual(len(fr), (S.frame_count(p) + 1) * 6)
        for t, _sel, _ in fr:
            self.assertEqual(round(t, 2), t)
        for dur, floor in ((0.2, _field.SAMPLES_MIN), (20.0, _field.SAMPLES_MAX)):
            q = S.plan(DEFAULT_CFG, dur, FPS, STAGE_SVG, TARGET)
            self.assertEqual(len(S.frames(q)), (floor + 1) * 6)

    def test_too_short_a_beat_is_rejected(self):
        """帧时刻锁两位，短于此会让两帧撞进同一格、静默少一帧。"""
        with self.assertRaisesRegex(ValueError, "duration"):
            S.plan(DEFAULT_CFG, 0.05, FPS, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "fps"):
            S.plan(DEFAULT_CFG, DURATION, 0, STAGE_SVG, TARGET)
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            S.plan(DEFAULT_CFG, DURATION, FPS, STAGE_SVG, "#d2")


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 cells / 幅度 / mode 逐个拒。"""
        bad = [
            {"cells": ["#d1", "#d2"]}, {"cells": ["#d1"] * 13},
            {"cells": ["#d1", "#d1", "#d3"]},
            {"cells": [".d1", "#d2", "#d3"]}, {"cells": "nope"},
            {"cells": ["#d1", "#d2", "#cam"]},
            {"amplitude": 0}, {"amplitude": float("nan")},
            {"amplitude": float("inf")},
            {"mode": "school"}, {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                S.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"mode": "scatter"}, {"mode": "vortex"},
                    {"amplitude": -60}, {"cells": ["#d1", "#d2", "#d3"]}):
            with self.subTest(cfg=cfg):
                self.assertEqual(S.validate(dict(DEFAULT_CFG, **cfg))["mode"],
                                 cfg.get("mode", S.SWARM_DEFAULT_MODE))

    def test_swarm_is_exclusive_with_other_kinds(self):
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
                      {"wave": {"cells": ["#d1"], "origin": "#d2",
                                "amplitude": -40}},
                      {"flow": {"riders": ["#d1"], "path": "#d2",
                                "distance": 180}},
                      {"lens": {"cells": ["#d1", "#d2"], "glass": "#d3",
                                "to": "#d4", "amplitude": 40}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_ease_camera_and_target_alignment_rejected(self):
        """一个场不按序号错峰、不重放、不吃 GSAP 缓动；相机层与群头当场说清。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True},
                      {"ease": "power3.out"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam", "swarm": dict(DEFAULT_CFG)})
        with self.assertRaisesRegex(ValueError, "必须等于 target"):
            contract({"at": 0, "target": "#d2", "swarm": dict(DEFAULT_CFG)})
        ok = contract(step_with())
        self.assertIn("swarm", ok["seg2"]["animate"]["steps"][0])

    def test_default_html_has_no_swarm_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(swarm_sets(render()), [])
        for cfg in ({"mode": "school"}, {"cells": ["#d1", "#d2"]},
                    {"amplitude": 0}, {"oops": 1}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=swarm_images(cfg=cfg))
        with self.assertRaisesRegex(ValueError, "找不到"):
            render(images=swarm_images(svg=STAGE_SVG.replace('id="d2"', 'id="gone"')))
        with self.assertRaisesRegex(ValueError, "只隔"):
            render(images=swarm_images(svg=OVERLAP_SVG))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐元素选择器与逐帧变量全部等于 `_swarm.frames`。"""
        html = render(images=swarm_images())
        got = swarm_sets(html)
        want = S.frames(make_plan())
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
        """两画幅各验、两次生成字节相同；三个旋钮各自真的改产物。"""
        imgs = swarm_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            self.assertEqual(len(swarm_sets(html)), len(S.frames(make_plan())))
        base = [v for _, v, _ in swarm_sets(render(images=swarm_images()))]
        for cfg in ({"amplitude": 120}, {"mode": "vortex"},
                    {"cells": ["#d1", "#d2", "#d3"]}):
            with self.subTest(cfg=cfg):
                self.assertNotEqual(base, [v for _, v, _ in swarm_sets(
                    render(images=swarm_images(cfg=cfg)))])
        stronger = [v for _, v, _ in swarm_sets(render(images=swarm_images(
            cfg={"amplitude": 120})))]
        mag = lambda vs: max(math.hypot(v["x"], v["y"]) for v in vs)
        self.assertGreater(mag(stronger), mag(base))

    def test_activity_fades_for_an_outlier(self):
        """离群元素不被群带着走：活跃度与峰值位移都显著衰减，但自查仍通过。"""
        p = make_plan(svg=FAR_SVG)
        self.assertEqual(S.diagnose(p), [])
        acts = [v[2] for v in p["geometry"]["field"]]
        self.assertLess(acts[5], 0.2 * max(acts[:5]), acts)
        peaks = [max(math.hypot(*S.state_at(DURATION * k / 100, p, i).values())
                     for k in range(101)) for i in range(6)]
        self.assertLess(peaks[5], 0.2 * max(peaks[:5]), peaks)


class CarryContract(unittest.TestCase):
    def test_carry_explicitly_recognises_swarm_and_leaves_no_residual(self):
        """收尾 = 起始：_settle_step 显式认这一拍并返回空态，页界零残留。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        self.assertEqual(S.settle_state(DEFAULT_CFG), [])
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        baked = ET.fromstring(out)
        for i in range(1, 7):
            with self.subTest(gid="d%d" % i):
                el = baked.find(".//*[@id='d%d']" % i)
                self.assertFalse((el.get("transform") or "").strip())
                self.assertNotIn("opacity:0", (el.get("style") or ""))


if __name__ == "__main__":
    unittest.main()
