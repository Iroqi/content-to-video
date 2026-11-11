#!/usr/bin/env python3
"""有机档 `organic`（确定性不公度谐波 → 可复现的有机起伏）的契约与**产物级**判据。

与 `test_momentum.py` / `test_step.py` 同一条方法论：判据尽量从生成的 HTML 里
抠补间来读，不在 Python 侧另写一份"应该生成什么"的实现。唯一的例外是
`_organic.plan`——它是契约层、渲染端、接续烘焙共用的那份真源，测试拿它跟
产物**对账**，而不是拿它替产物背书。
"""
import contextlib
import io
import json
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _organic as O
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"axis": "xy", "amplitude": 12.0, "cycles": 1.6, "seed": 7}
DURATION = 0.8
FPS = 24
BLOB_SVG = ('<svg viewBox="0 0 980 735">'
            '<circle id="blob" cx="490" cy="360" r="40"/></svg>')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<rect id="blob" x="60" y="300" width="960" height="500"/>'
             "</svg>")
# 按形态抓：一条 organic 拍发射的是一组同 selector 的 tl.set，变量只有 x/y。
BLOB_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #blob)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def organic_images(cfg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": "#blob", "duration": DURATION,
            "organic": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg", "inline_svg": BLOB_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def blob_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in BLOB_SET.finditer(html)]


def step_with(**kw):
    return {"at": 0, "target": "#blob",
            "organic": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [O.state_at(t, p) for t in ts]
        self.assertEqual(forward, [O.state_at(t, p) for t in reversed(ts)][::-1])
        O.state_at(0.31, p)
        self.assertEqual(forward, [O.state_at(t, p) for t in ts])
        self.assertTrue(O.is_well_formed(p))
        self.assertEqual(O.diagnose(p), [])

    def test_endpoints_return_exactly_to_origin_with_zero_derivative(self):
        """端点窗口 W=sin²(πu)：起止精确回原位，且一阶导也是零。

        这是全仓库唯一「收尾态 = 起始态」的档——跨段接续没有残留姿态可折。
        """
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        for state in (O.start(p), O.landing(p)):
            self.assertEqual(state, {"x": 0.0, "y": 0.0})
        for _, v in O.frames(p):
            self.assertEqual(set(v), {"x", "y"})
        self.assertLessEqual(O.edge_derivative(p), O.EDGE_DERIVATIVE_EPS)
        # 端点之外的时刻被夹回端点，seek 跨过窗口不会突然冒出位移
        self.assertEqual(O.state_at(-1.0, p), O.start(p))
        self.assertEqual(O.state_at(DURATION + 1.0, p), O.landing(p))

    def test_signal_bounded_by_amplitude(self):
        """amplitude 是峰值上界：归一化谐波 |N| ≤ 1、窗口 W ≤ 1，不能吹出去。"""
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertLessEqual(O.max_deviation(p), 1.0)
        for _, v in O.frames(p):
            for x in v.values():
                self.assertLessEqual(abs(x), DEFAULT_CFG["amplitude"] + 1e-6)
        # 且确有起伏：不是一条恒为 0 的"没写"
        self.assertGreater(max(abs(x) for _, v in O.frames(p)
                               for x in v.values()), 1.0)

    def test_seed_is_deterministic_and_changes_the_curve(self):
        """同 seed 逐位相同、不同 seed 给出不同手感——确定性，不是随机源。"""
        a = O.plan(DEFAULT_CFG, DURATION, FPS)
        b = O.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertEqual(O.frames(a), O.frames(b))
        other = O.plan(dict(DEFAULT_CFG, seed=8), DURATION, FPS)
        self.assertNotEqual(O.frames(a), O.frames(other))
        # 相位只由 seed 与轴号纯算术算出，与调用次数 / 全局状态无关
        self.assertEqual(O._phases(7, 1), O._phases(7, 1))
        self.assertNotEqual(O._phases(7, 0), O._phases(7, 1))

    def test_frequencies_are_incommensurate_and_spectrum_decays(self):
        """不公度 = 永不精确重复；谱严格递减 = 高频层不许盖过主频。"""
        self.assertEqual(len(O.FREQ), O.HARMONICS)
        self.assertEqual(len(O.harmonic_spectrum()), O.HARMONICS)
        w = O.harmonic_spectrum()
        self.assertTrue(all(a > b for a, b in zip(w, w[1:])))
        self.assertAlmostEqual(sum(w), 1.0, places=9)
        self.assertGreaterEqual(O.incommensurate_gap(), O.INCOMMENSURATE_MIN)
        # 若频率比退化为有理数，判据必须翻红
        saved = O.FREQ
        try:
            O.FREQ = (1.0, 2.0, 3.0, 4.0)
            self.assertLess(O.incommensurate_gap(), O.INCOMMENSURATE_MIN)
        finally:
            O.FREQ = saved


class TrajectoryContract(unittest.TestCase):
    def test_two_axes_are_out_of_phase(self):
        """两轴同相会退化成过原点的直线，与 motion.md「两轴同缓动 = 直弦」同源。"""
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertGreaterEqual(O.trajectory_isotropy(p), O.MIN_ISOTROPY)
        # 人为把两轴相位并成同一组：各向同性比应精确塌到 0
        same = dict(p)
        same["phases"] = (p["phases"][0], p["phases"][0])
        self.assertAlmostEqual(O.trajectory_isotropy(same), 0.0, places=9)
        self.assertEqual(O.state_at(0.31, same)["x"],
                         O.state_at(0.31, same)["y"])
        self.assertTrue(any("双轴轨迹共线" in x for x in O.diagnose(same)))

    def test_single_axis_writes_only_that_transform(self):
        """单轴档只写一条 transform，不凭空多出第二个通道。"""
        for axis, key in (("x", "x"), ("y", "y")):
            with self.subTest(axis=axis):
                p = O.plan(dict(DEFAULT_CFG, axis=axis), DURATION, FPS)
                self.assertTrue(O.is_well_formed(p))
                for _, v in O.frames(p):
                    self.assertEqual(list(v), [key])
                self.assertEqual(set(O.landing(p)), {key})
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertEqual(set(O.landing(p)), {"x", "y"})


class BandwidthContract(unittest.TestCase):
    def test_frame_step_never_flickers(self):
        """逐帧位移有上限：短拍塞满周期会被生成期点名，而不是留给成片去闪。"""
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        self.assertLessEqual(O.max_frame_step(p), O.MAX_FRAME_STEP_RATIO)
        dense = O.plan(dict(DEFAULT_CFG, cycles=O.CYCLES_MAX), DURATION, FPS)
        self.assertFalse(O.is_well_formed(dense))
        self.assertTrue(any("逐帧位移过大" in x for x in O.diagnose(dense)))
        # 同一档配置放到长拍里就合法：密度是「每帧走多少」，不是 cycles 单独定
        long = O.plan(dict(DEFAULT_CFG, cycles=O.CYCLES_MAX), 2.0, FPS)
        self.assertTrue(O.is_well_formed(long))

    def test_sample_grid_is_deterministic_and_bounded(self):
        """采样网格：严格递增、覆盖首尾、条数落在与既有档共用的采样带里。"""
        p = O.plan(DEFAULT_CFG, DURATION, FPS)
        fr = O.frames(p)
        self.assertEqual(fr, O.frames(p))
        ts = [t for t, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(set(ts)), "同一 0.01s 格撞车必须去重")
        self.assertEqual(len(fr), O.frame_count(p) + 1)
        for dur, floor in ((0.2, O.SAMPLES_MIN), (20.0, O.SAMPLES_MAX)):
            q = O.plan(DEFAULT_CFG, dur, FPS)
            self.assertEqual(len(O.frames(q)), floor + 1)
        with self.assertRaises(ValueError):
            O.plan(DEFAULT_CFG, 0.05, FPS)


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 axis / 幅度 / 密度 / 种子逐个拒。"""
        bad = [
            {"axis": "z"}, {"axis": "X"}, {"amplitude": 0},
            {"amplitude": -1}, {"amplitude": O.AMPLITUDE_MAX + 1},
            {"amplitude": float("nan")}, {"cycles": 0.1},
            {"cycles": O.CYCLES_MAX + 0.1}, {"seed": -1},
            {"seed": 1.5}, {"seed": True}, {"seed": O.SEED_MAX + 1},
            {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                O.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"amplitude": O.AMPLITUDE_MAX},
                    {"cycles": O.CYCLES_MIN}, {"cycles": O.CYCLES_MAX},
                    {"seed": O.SEED_MIN}, {"seed": O.SEED_MAX}):
            with self.subTest(cfg=cfg):
                O.validate(dict(DEFAULT_CFG, **cfg))

    def test_organic_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -80}},
                      {"drag": {"chain": ["#blob", "#b"], "axis": "y",
                                "distance": -80}},
                      {"step": {"axis": "y", "distance": -80}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_yoyo_camera_rejected(self):
        """连续准周期轨迹不收错峰/重放；相机层归整页取景，收尾态由折层负责。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam",
                      "organic": dict(DEFAULT_CFG)})

    def test_default_html_has_no_organic_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(blob_sets(render()), [])
        for cfg in ({"axis": "z"}, {"amplitude": 0},
                    {"cycles": 99}, {"seed": 1.5}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=organic_images(cfg=cfg))
        # 合法但过密的组合由生成期自查拦下，且报错点名是哪条红线
        with self.assertRaisesRegex(ValueError, "逐帧位移过大"):
            render(images=organic_images(cfg={"cycles": O.CYCLES_MAX}))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐帧变量全部等于 `_organic.frames`。"""
        html = render(images=organic_images())
        got = blob_sets(html)
        want = O.frames(O.plan(DEFAULT_CFG, DURATION, FPS))
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel == "#img-seg-a #blob" for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wv), (_, gv, gt) in zip(want, got):
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gv, wv)
        self.assertEqual(got[0][1], {"x": 0.0, "y": 0.0})
        self.assertEqual(got[-1][1], {"x": 0.0, "y": 0.0})

    def test_two_aspects_and_repeatability(self):
        """两画幅各验一遍，且同一输入两次生成字节相同（seed 只在生成期读）。"""
        imgs = organic_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            got = blob_sets(html)
            self.assertEqual(len(got), len(O.frames(
                O.plan(DEFAULT_CFG, DURATION, FPS))))
            self.assertEqual(got[0][1], {"x": 0.0, "y": 0.0})

    def test_knobs_drive_product(self):
        """四个旋钮各自真的改产物，不是写了等于没写。"""
        base = [v for _, v, _ in blob_sets(render(images=organic_images()))]
        louder = [v for _, v, _ in blob_sets(render(images=organic_images(
            cfg={"amplitude": 24.0})))]
        self.assertGreater(max(abs(v["x"]) for v in louder),
                           max(abs(v["x"]) for v in base))
        other = [v for _, v, _ in blob_sets(render(images=organic_images(
            cfg={"seed": 8})))]
        self.assertNotEqual(base, other)
        faster = [v for _, v, _ in blob_sets(render(images=organic_images(
            cfg={"cycles": 2.5})))]
        self.assertNotEqual(base, faster)
        single = [v for _, v, _ in blob_sets(render(images=organic_images(
            cfg={"axis": "x"})))]
        self.assertTrue(all("y" not in v for v in single))
        self.assertTrue(O.is_well_formed(O.plan(
            dict(DEFAULT_CFG, axis="x"), DURATION, FPS)))


class CarryContract(unittest.TestCase):
    def test_carry_has_no_residual_pose(self):
        """接续页第 0 帧：收尾精确回原位，一个 transform 都不该折，也不出声。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        el = ET.fromstring(out).find(".//*[@id='%s']" % "blob")
        self.assertIsNotNone(el)
        self.assertFalse((el.get("transform") or "").strip())
        self.assertEqual(notes, [])

    def test_carry_explicitly_recognises_organic(self):
        """_settle_step 显式认这一拍（不靠「没有 set/to 所以恰好 no-op」的巧合）。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        self.assertEqual(root.find(".//*[@id='blob']").get("transform"), None)
