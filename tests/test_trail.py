#!/usr/bin/env python3
"""残影档 `trail`（时间回声 → 一帧里多张画）的契约与**产物级**判据。

与 `test_momentum.py` / `test_drag.py` / `test_organic.py` 同一条方法论：判据尽量
从生成的 HTML 里抠补间来读，不在 Python 侧另写一份「应该生成什么」的实现。
唯一的例外是 `_trail.plan`——它是契约层、渲染端、接续烘焙共用的那份真源，
测试拿它跟产物**对账**，而不是拿它替产物背书。
"""
import contextlib
import io
import json
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _field  # 采样带与归一口径的真源
import _trail as T
import _stage_carry as SC
from _images_schema import validate_images_json
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"ghosts": ["#g1", "#g2", "#g3"], "axis": "x",
               "distance": 320, "lag": 0.08, "decay": 0.62,
               "ease": "power3.out"}
TARGET = "#hero"
DURATION = 0.8
FPS = 24
STAGE_SVG = ('<svg viewBox="0 0 980 735">'
             '<rect id="hero" x="10" y="300" width="40" height="40"/>'
             '<rect id="g1" x="10" y="300" width="40" height="40" opacity="0"/>'
             '<rect id="g2" x="10" y="300" width="40" height="40" opacity="0"/>'
             '<rect id="g3" x="10" y="300" width="40" height="40" opacity="0"/>'
             "</svg>")
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<rect id="hero" x="10" y="300" width="40" height="40"/>'
             '<rect id="g1" x="10" y="300" width="40" height="40"/>'
             '<rect id="g2" x="10" y="300" width="40" height="40"/>'
             '<rect id="g3" x="10" y="300" width="40" height="40"/>'
             "</svg>")
# 按形态抓：一条 trail 拍发射的是每帧每层一条 tl.set，selector 各自带 #img-{sid} 作用域。
TRAIL_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #[A-Za-z_][\w-]*)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')
OWN_SELECTORS = {"#img-seg-a #hero", "#img-seg-a #g1",
                 "#img-seg-a #g2", "#img-seg-a #g3"}


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def trail_images(cfg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": TARGET, "duration": DURATION,
            "trail": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg", "inline_svg": STAGE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def trail_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in TRAIL_SET.finditer(html)
            if m.group(1) in OWN_SELECTORS]


def step_with(**kw):
    return {"at": 0, "target": TARGET,
            "trail": dict(DEFAULT_CFG, **kw)}


def contract(step):
    return validate_images_json({"seg2": {"src": "images/seg2.svg",
                                          "animate": {"steps": [step]}}})


def make_plan(cfg=None):
    return T.plan(dict(DEFAULT_CFG, **(cfg or {})), DURATION, FPS, TARGET)


class SignalContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟、不依赖调用顺序。"""
        p = make_plan()
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [T.state_at(t, p, 2) for t in ts]
        self.assertEqual(forward, [T.state_at(t, p, 2) for t in reversed(ts)][::-1])
        T.state_at(0.31, p, 1)
        self.assertEqual(forward, [T.state_at(t, p, 2) for t in ts])
        self.assertTrue(T.is_well_formed(p))
        self.assertEqual(T.diagnose(p), [])
        # 端点之外被夹回端点，seek 跨过窗口不会冒出一段凭空的拖尾
        self.assertEqual(T.state_at(-1.0, p, 2), T.state_at(0.0, p, 2))
        self.assertEqual(T.state_at(DURATION + 1.0, p, 2),
                         T.state_at(DURATION, p, 2))

    def test_ghosts_are_exactly_the_subjects_past(self):
        """定义性判据：xᵢ(t) = x₀(t − i·lag)，残差恒为 0，不是拟合出来的像。"""
        p = make_plan()
        for j in range(41):
            t = DURATION * j / 40.0
            for i in range(1, len(p["cfg"]["ghosts"]) + 1):
                self.assertAlmostEqual(T.echo_identity(p, i, t), 0.0, places=9)
        # 换个 lag 与 ease 仍成立：它是结构性事实，不是某一组参数的巧合
        for cfg in ({"lag": 0.16}, {"ease": "sine.inOut"}, {"axis": "y"}):
            with self.subTest(cfg=cfg):
                q = make_plan(cfg)
                for i in range(1, 4):
                    self.assertLess(T.echo_identity(q, i, 0.53), 1e-9)

    def test_endpoints_have_no_ghosts(self):
        """W=sin²(πu)：段首段尾影子精确隐去，主体精确落在 distance。"""
        p = make_plan()
        self.assertEqual(T.start(p), {"x": 0.0})
        self.assertEqual(T.landing(p), {"x": float(DEFAULT_CFG["distance"])})
        for i in range(1, 4):
            self.assertEqual(T.start(p, i)["opacity"], 0.0)
            self.assertEqual(T.landing(p, i)["opacity"], 0.0)
        fr = T.frames(p)
        self.assertEqual(fr[0][2], {"x": 0.0})
        for sel, var in ((s, v) for t, s, v in fr if t == fr[-1][0]):
            self.assertEqual(var.get("opacity", 0.0), 0.0, sel)

    def test_speed_gate_and_layer_decay(self):
        """整串共用一个速度门（峰值归一到 1），层间亮度只差 decayⁱ。"""
        p = make_plan()
        mid = DURATION * 0.5
        ops = [T.state_at(mid, p, i)["opacity"] for i in range(1, 4)]
        self.assertTrue(all(a > b > 0 for a, b in zip(ops, ops[1:])))
        for i, o in enumerate(ops, start=1):
            self.assertAlmostEqual(o / ops[0],
                                   DEFAULT_CFG["decay"] ** (i - 1), places=6)
        peak = max(v.get("opacity", 0.0) for _, _, v in T.frames(p))
        self.assertAlmostEqual(peak, DEFAULT_CFG["decay"], delta=0.01)
        self.assertLessEqual(peak, 1.0)
        # 门由主体速度决定：起步与落定都关着
        self.assertEqual(T._gate(0.0, p), 0.0)
        self.assertEqual(T._gate(1.0, p), 0.0)


class TrajectoryContract(unittest.TestCase):
    def test_ghosts_never_catch_up_with_the_subject(self):
        """与 drag 的分界：残影是过去，故意不重参数化，永不追上现在。"""
        p = make_plan()
        d = p["cfg"]["distance"]
        # 重参数化会让 xᵢ(D)=distance；这里收尾时影子仍严格落在主体身后
        for i in range(1, 4):
            g = T.landing(p, i)
            self.assertLess(g["x"], d)
            self.assertEqual(g["opacity"], 0.0)
        for j in range(41):
            t = DURATION * j / 40.0
            subj = T.state_at(t, p, 0)["x"]
            for i in range(1, 4):
                self.assertLessEqual(T.state_at(t, p, i)["x"], subj + 1e-9)

    def test_selectors_keep_subject_first_and_ghost_order(self):
        """层序是导演意图：主体在前，影子按滞后递增，不能被字典序洗掉。"""
        p = make_plan()
        self.assertEqual(p["selectors"], [TARGET, "#g1", "#g2", "#g3"])
        fr = T.frames(p)
        self.assertEqual([s for _, s, _ in fr[:4]], p["selectors"])
        self.assertEqual([s for _, s, _ in fr[-4:]], p["selectors"])
        # 主体只写主轴位移，影子多写一条 opacity
        self.assertEqual(set(fr[1][2]), {"x", "opacity"})
        self.assertEqual(set(fr[0][2]), {"x"})


class GridContract(unittest.TestCase):
    def test_sample_grid_is_deterministic_and_bounded(self):
        """采样网格：严格递增、覆盖首尾、条数 = (帧数+1)×(层数+1)。"""
        p = make_plan()
        fr = T.frames(p)
        self.assertEqual(fr, T.frames(p))
        ts = [t for t, _, _ in fr]
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], DURATION, places=2)
        self.assertEqual(ts, sorted(ts), "同一层的时刻必须严格递增")
        # 每帧有 4 条（主体 + 3 层影子）：帧时刻本身不能撞进同一个 0.01s 格
        frame_ts = [t for t, sel, _ in fr if sel == TARGET]
        self.assertEqual(frame_ts, sorted(set(frame_ts)),
                         "同一 0.01s 格撞车必须去重")
        self.assertEqual(len(fr), (T.frame_count(p) + 1) * 4)
        # 短拍要把 lag 一起收小，否则会先撞上「总滞后 ≤ 拍长 75%」那条红线
        for dur, floor, cfg in ((0.2, _field.SAMPLES_MIN,
                                 dict(DEFAULT_CFG, lag=0.045)),
                                (20.0, _field.SAMPLES_MAX, DEFAULT_CFG)):
            q = T.plan(cfg, dur, FPS, TARGET)
            self.assertEqual(len(T.frames(q)), (floor + 1) * 4)
        with self.assertRaises(ValueError):
            T.plan(DEFAULT_CFG, 0.05, FPS, TARGET)

    def test_lag_span_and_frame_floor_are_red_lines(self):
        """层间滞后不得小于一帧，总滞后不得吃掉大半个拍长。"""
        with self.assertRaisesRegex(ValueError, "小于一帧"):
            T.plan(dict(DEFAULT_CFG, lag=0.02), DURATION, FPS, TARGET)
        with self.assertRaisesRegex(ValueError, "总滞后"):
            T.plan(dict(DEFAULT_CFG, lag=0.25), DURATION, FPS, TARGET)
        # 同一档配置放进长拍就合法：判据量的是「占这一拍的多少」
        q = T.plan(dict(DEFAULT_CFG, lag=0.25), 2.0, FPS, TARGET)
        self.assertTrue(T.is_well_formed(q))


class SchemaContract(unittest.TestCase):
    def test_red_lines_at_consumption_side(self):
        """红线写在消费侧：非法 ghosts / axis / 距离 / lag / decay / ease 逐个拒。"""
        bad = [
            {"ghosts": ["#g1"]}, {"ghosts": ["#g1"] * 7},
            {"ghosts": ["#g1", "#g1"]}, {"ghosts": [".g1", "#g2"]},
            {"ghosts": "nope"}, {"axis": "z"}, {"axis": "X"},
            {"distance": 0}, {"distance": float("nan")},
            {"lag": 0}, {"lag": -0.1}, {"lag": T.LAG_MAX + 0.01},
            {"decay": 0}, {"decay": T.DECAY_MAX + 0.01},
            {"ease": "back.out"}, {"ease": "linear"}, {"ease": "power3.in"},
            {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                T.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"lag": T.LAG_MAX}, {"decay": T.DECAY_MAX},
                    {"ghosts": ["#a", "#b"]},
                    {"ghosts": ["#a", "#b", "#c", "#d", "#e", "#f"]},
                    {"ease": "sine.inOut"}):
            with self.subTest(cfg=cfg):
                T.validate(dict(DEFAULT_CFG, **cfg))

    def test_trail_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -80}},
                      {"drag": {"chain": ["#hero", "#g1"], "axis": "y",
                                "distance": -80}},
                      {"step": {"axis": "y", "distance": -80}},
                      {"organic": {"amplitude": 12}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))

    def test_stagger_repeat_yoyo_camera_and_self_ghost_rejected(self):
        """一条拖尾不拆、不重放；相机层与「把自己列进影子」当场说清。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                contract(dict(step_with(), **extra))
        with self.assertRaises(ValueError):
            contract({"at": 0, "target": "#cam", "trail": dict(DEFAULT_CFG)})
        with self.assertRaisesRegex(ValueError, "不能含 target"):
            contract(step_with(ghosts=["#hero", "#g2"]))
        with self.assertRaisesRegex(ValueError, "不能含 #cam"):
            contract(step_with(ghosts=["#g1", "#cam"]))

    def test_default_html_has_no_trail_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(trail_sets(render()), [])
        for cfg in ({"axis": "z"}, {"ghosts": ["#g1"]},
                    {"decay": 0.9}, {"lag": 0.02}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=trail_images(cfg=cfg))
        with self.assertRaisesRegex(ValueError, "总滞后"):
            render(images=trail_images(cfg={"lag": 0.25}))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：锚点、帧数、逐层选择器与逐帧变量全部等于 `_trail.frames`。"""
        html = render(images=trail_images())
        got = trail_sets(html)
        want = T.frames(make_plan())
        self.assertEqual(len(got), len(want))
        self.assertTrue(all(sel in OWN_SELECTORS for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wsel, wv), (gsel, gv, gt) in zip(want, got):
            self.assertEqual(gsel, f"#img-seg-a {wsel}")
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gv, wv)
        self.assertEqual(got[0][1], {"x": 0.0})
        self.assertEqual(got[-1][1].get("opacity"), 0.0)

    def test_two_aspects_and_repeatability(self):
        """两画幅各验一遍，且同一输入两次生成字节相同（ease 只在生成期读）。"""
        imgs = trail_images()
        with contextlib.redirect_stderr(io.StringIO()):
            a = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="portrait")
            b = generate_html(H.make_manifest(), "audio/combined.wav",
                              images=imgs, aspect="landscape")
            again = generate_html(H.make_manifest(), "audio/combined.wav",
                                  images=imgs, aspect="portrait")
        self.assertEqual(a, again)
        for html in (a, b):
            self.assertEqual(len(trail_sets(html)), len(T.frames(make_plan())))

    def test_knobs_drive_product(self):
        """五个旋钮各自真的改产物，不是写了等于没写。"""
        base = [v for _, v, _ in trail_sets(render(images=trail_images()))]
        farther = [v for _, v, _ in trail_sets(render(images=trail_images(
            cfg={"distance": 500})))]
        self.assertGreater(max(v["x"] for v in farther),
                           max(v["x"] for v in base))
        dimmer = [v for _, v, _ in trail_sets(render(images=trail_images(
            cfg={"decay": 0.3})))]
        self.assertLess(max(v.get("opacity", 0) for v in dimmer),
                        max(v.get("opacity", 0) for v in base))
        slower = [v for _, v, _ in trail_sets(render(images=trail_images(
            cfg={"lag": 0.16})))]
        self.assertNotEqual(base, slower)
        other_ease = [v for _, v, _ in trail_sets(render(images=trail_images(
            cfg={"ease": "sine.inOut"})))]
        self.assertNotEqual(base, other_ease)
        single = [v for _, v, _ in trail_sets(render(images=trail_images(
            cfg={"axis": "y"})))]
        self.assertTrue(all("x" not in v and "y" in v for v in single))
        self.assertTrue(T.is_well_formed(make_plan({"axis": "y"})))


class CarryContract(unittest.TestCase):
    def test_carry_leaves_no_ghost_on_the_next_page(self):
        """接续页第 0 帧：主体折到 distance，每个影子 style 里精确 opacity:0。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        root = ET.fromstring(out)
        hero = root.find(".//*[@id='hero']")
        self.assertEqual(hero.get("transform"), "translate(320,0)")
        for gid in ("g1", "g2", "g3"):
            with self.subTest(gid=gid):
                el = root.find(".//*[@id='%s']" % gid)
                self.assertIn("opacity:0", (el.get("style") or "").replace(" ", ""))
                self.assertFalse((el.get("transform") or "").strip())

    def test_carry_explicitly_recognises_trail(self):
        """_settle_step 显式认这一拍（不靠「没有 set/to 所以恰好 no-op」的巧合）。"""
        root = ET.fromstring(CARRY_SVG)
        transforms, misfires = SC._settle_step(root, step_with())
        self.assertEqual((transforms, misfires), ([], []))
        self.assertEqual(
            [(sel, payload) for sel, payload
             in T.settle_state(DEFAULT_CFG, TARGET)],
            [(TARGET, {"x": 320.0})]
            + [(s, {"opacity": 0.0}) for s in DEFAULT_CFG["ghosts"]])


if __name__ == "__main__":
    unittest.main()
