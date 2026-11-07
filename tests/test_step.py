#!/usr/bin/env python3
"""定格档 `step`（唯帧网格 → 时间抖动 → 左连续阶梯）的契约与**产物级**判据。

与 `test_drag.py` 同一条方法论：判据尽量从生成的 HTML 里抠补间来读，不在
Python 侧另写一份"应该生成什么"的实现。唯一的例外是 `_step.plan`——它是
契约层、渲染端、接续烘焙共用的那份真源，测试拿它跟产物**对账**，而不是
拿它替产物背书。这一档多一条它独有的对账：产物里 `tl.set` 的**条数**就是
唯帧数，不是渲染帧数，所以「一张画停几帧」第一次能被直接量出来。
"""
import contextlib
import io
import json
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _animate as AN
import _step as S
import _stage_carry as SC
from _images_schema import validate_images_json
from _timeline import beat_positions
from html_renderer import generate_html
from _svg_sanitize import sanitize_svg_for_inline

DEFAULT_CFG = {"axis": "y", "distance": -160, "ease": "power3.out",
               "every": 2, "timing": 0.18, "wavelength": 7}
DURATION = 1.2
FPS = 24
TILE_SVG = ('<svg viewBox="0 0 980 735">'
            '<rect id="tile" x="390" y="300" width="200" height="160"/></svg>')
CARRY_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
             '<rect id="tile" x="440" y="300" width="200" height="200"/>'
             "</svg>")
# 按形态抓，不按 ease 名抓：一条 step 拍发射的是目标元素上一组 tl.set，
# 变量表只有 x 或 y（无嵌套），与 morph 的采样序列、drag 的链成员三元组不重样。
STEP_SET = re.compile(
    r'tl\.set\("(#img-[\w-]+ #tile)",\s*(\{[^{}]*\}),\s*([\d.]+)\)')


def render(images=None, manifest=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def step_images(cfg=None, **step_extra):
    imgs = H.sample_images(H.make_manifest())
    step = {"at": 0, "target": "#tile", "duration": DURATION,
            "step": dict(DEFAULT_CFG, **(cfg or {}))}
    step.update(step_extra)
    imgs["seg-a"] = {"src": "images/seg-a.svg", "inline_svg": TILE_SVG,
                     "animate": {"steps": [step]}}
    return imgs


def tile_sets(html):
    return [(m.group(1), json.loads(m.group(2)), float(m.group(3)))
            for m in STEP_SET.finditer(html)]


def step_with(**kw):
    return {"at": 0, "target": "#tile", "step": dict(DEFAULT_CFG, **kw)}


def spec(step):
    return {"seg2": {"src": "images/seg2.svg",
                     "animate": {"steps": [step]}}}


class FrameContract(unittest.TestCase):
    def test_state_is_pure_function_of_time(self):
        """逐帧 seek 的前提：state_at 不存上一帧、不读墙钟，台阶内恒定。"""
        p = S.plan(DEFAULT_CFG, DURATION, FPS)
        ts = [DURATION * i / 97 for i in range(98)]
        forward = [S.state_at(t, p) for t in ts]
        self.assertEqual(forward, [S.state_at(t, p) for t in reversed(ts)][::-1])
        S.state_at(0.31, p)
        self.assertEqual(forward, [S.state_at(t, p) for t in ts])
        # 左连续：同一台阶里任意两个时刻给的是同一张画
        for j in range(p["m"]):
            a, b = p["times"][j], p["times"][j + 1]
            self.assertEqual(S.state_at(a + 1e-6, p),
                             S.state_at((a + b) / 2, p))
            self.assertEqual(S.state_at((a + b) / 2, p),
                             S.state_at(b - 1e-6, p))

    def test_first_and_last_frame_are_exact(self):
        """首帧原位、末帧精确落位，且按产物的六位精度也精确。"""
        p = S.plan(DEFAULT_CFG, DURATION, FPS)
        fr = S.frames(p)
        self.assertAlmostEqual(fr[0][0], 0.0, places=6)
        self.assertEqual(fr[0][1], {"y": 0.0})
        self.assertAlmostEqual(fr[-1][0], DURATION, places=6)
        self.assertEqual(fr[-1][1], {"y": -160.0})
        self.assertEqual(S.landing(p), {"y": -160.0})
        self.assertTrue(S.is_well_formed(p))
        # 负零不许进产物：distance 为负时 d×0.0 是 -0.0
        self.assertNotIn("-0.0", json.dumps(fr[0][1]))

    def test_grid_is_strictly_increasing_and_frame_bounded(self):
        """唯帧网格：严格递增、两位小数后不撞格、间隔不越过 K+2·timing 帧。"""
        p = S.plan(DEFAULT_CFG, DURATION, FPS)
        ts = p["times"]
        self.assertEqual(ts[0], 0.0)
        self.assertEqual(ts[-1], DURATION)
        self.assertEqual(len(ts), S.unique_frames(p))
        self.assertTrue(all(b > a for a, b in zip(ts, ts[1:])))
        self.assertEqual(len({round(t, 2) for t in ts}), len(ts))
        self.assertAlmostEqual(S.nominal_step(p), 2 / FPS, places=9)
        self.assertTrue(all(
            b - a <= (2 + 2 * DEFAULT_CFG["timing"]) / FPS + S.TIME_GRID + 1e-9
            for a, b in zip(ts, ts[1:])))
        for dur, k, want in ((1.2, 2, 16), (1.2, 3, 11), (1.2, 4, 9)):
            q = S.plan(dict(DEFAULT_CFG, every=k), dur, FPS)
            self.assertEqual(S.unique_frames(q), want)
            self.assertEqual(len(S.frames(q)), want)
            self.assertTrue(S.is_well_formed(q))

    def test_timing_jitter_is_bounded_deterministic_and_dies_at_edges(self):
        """双频不规则核：端点归零、幅度有界、确实进了时刻、纯等距档合法。"""
        p = S.plan(DEFAULT_CFG, DURATION, FPS)
        w = p["weights"]
        self.assertAlmostEqual(w[0], 0.0, places=9)
        self.assertAlmostEqual(w[-1], 0.0, places=9)
        self.assertTrue(all(abs(v) <= 1.0 + 1e-9 for v in w))
        devs = [S.timing_deviation(p, j) for j in range(len(p["times"]))]
        self.assertTrue(max(abs(v) for v in devs)
                        <= DEFAULT_CFG["timing"] / FPS + 1e-9)
        self.assertTrue(any(v > 1e-9 for v in devs)
                        and any(v < -1e-9 for v in devs))
        self.assertAlmostEqual(devs[0], 0.0, places=9)
        self.assertAlmostEqual(devs[-1], 0.0, places=9)
        # 抖动只动时刻、不动位置：同一 every 下两组 ratio 逐项相等
        flat = S.plan(dict(DEFAULT_CFG, timing=0.0), DURATION, FPS)
        self.assertEqual(p["ratio"], flat["ratio"])
        self.assertTrue(S.is_well_formed(flat))
        self.assertTrue(all(S.timing_deviation(flat, j) == 0.0
                            for j in range(len(flat["times"]))))
        self.assertEqual(S.plan(DEFAULT_CFG, DURATION, FPS)["times"],
                         p["times"])


class SchemaContract(unittest.TestCase):
    def test_documented_snippet_passes_schema_and_whole_beat_cursor(self):
        """文档里那份示例真能过契约层（不是只在测试里合法）。"""
        snippet = {"seg2": {"src": "images/seg2.svg", "animate": {"steps": [
            {"at": 0, "target": "#tile", "duration": 1.2,
             "step": dict(DEFAULT_CFG)}]}}}
        validate_images_json(snippet)
        # 从文档里真的抠出围栏再验一次，锚点是示例里的 "every": 2
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "references", "step.md"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"every": 2', text)
        # at_time 相对链按整段推进（_WHOLE_BEAT_KEYS 含 step）
        sent = [{"start_time": 2.0, "duration": 1.0}]
        steps = [{"at_time": "+0.5", "target": "#tile", "duration": 0.8,
                  "step": dict(DEFAULT_CFG)},
                 {"at_time": "+0.2", "target": "#tile", "duration": 0.3,
                  "to": {"opacity": 1}}]
        pos = beat_positions(steps, sent, 1.0, 0.5)
        self.assertAlmostEqual(pos[1][0], pos[0][0] + 0.8 + 0.2, places=2)

    def test_step_is_exclusive_with_other_kinds(self):
        """两条时间线抢同一条 transform 通道，契约层必须拦。"""
        for extra in ({"to": {"opacity": 1}}, {"from": {"y": 10}},
                      {"set": {"opacity": 1}}, {"draw": True},
                      {"morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 2 2"}},
                      {"count": {"to": 5}}, {"type": {"chunk": "word"}},
                      {"momentum": {"axis": "y", "distance": -20}},
                      {"drag": {"chain": ["#tile", "#head"],
                                "axis": "y", "distance": -20}}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    validate_images_json(spec(dict(step_with(), **extra)))
        # 反向：别的档写在前，step 也不能静默搭车
        for head in ({"type": {"chunk": "word"}},
                     {"momentum": {"axis": "y", "distance": -20}},
                     {"drag": {"chain": ["#tile", "#head"],
                               "axis": "y", "distance": -20}}):
            with self.subTest(head=head):
                with self.assertRaises(ValueError):
                    validate_images_json(spec(dict(
                        {"at": 0, "target": "#tile"}, **head,
                        step=dict(DEFAULT_CFG))))

    def test_red_lines_at_consumption_side(self):
        """红线在消费侧：非法旋钮逐个拒，边界值放行。"""
        bad = [
            {"axis": "z"}, {"axis": "x", "distance": 0},
            {"distance": float("nan")}, {"every": 1}, {"every": 5},
            {"every": 2.5}, {"timing": 0.9}, {"timing": -0.1},
            {"wavelength": 2}, {"wavelength": 65},
            {"ease": "none"}, {"ease": "linear"}, {"ease": "power3.in"},
            {"ease": "back.inOut(1.7)"}, {"ease": "spring(0.9)"},
            {"ease": "steps(4)"}, {"oops": 1},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                S.validate(dict(DEFAULT_CFG, **cfg))
        for cfg in ({"every": 2}, {"every": S.EVERY_MAX},
                    {"timing": 0.0}, {"timing": S.TIMING_MAX},
                    {"wavelength": S.WAVELENGTH_MIN},
                    {"wavelength": S.WAVELENGTH_MAX},
                    {"ease": "sine.inOut"}):
            with self.subTest(cfg=cfg):
                S.validate(dict(DEFAULT_CFG, **cfg))
        with self.assertRaises(ValueError):
            validate_images_json(spec(dict(step_with(), target="#cam")))

    def test_stagger_repeat_yoyo_rejected_and_degenerate_is_legal(self):
        """唯帧网格自带落定，错峰与重放都该在契约层说不通。"""
        for extra in ({"stagger": 0.12}, {"repeat": 2}, {"yoyo": True}):
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    validate_images_json(spec(dict(step_with(), **extra)))
        # every=2 + timing=0 的纯等距档合法：没有不规则可钉，硬判会误杀
        plain = S.plan(dict(DEFAULT_CFG, timing=0.0), DURATION, FPS)
        self.assertTrue(S.is_well_formed(plain))
        self.assertEqual(plain["weights"], [0.0] * S.unique_frames(plain))
        self.assertEqual([v for _, v in S.frames(plain)][0], {"y": 0.0})

    def test_too_short_or_too_long_beat_fails_at_plan(self):
        """唯帧太少是瞬跳、太多会撑爆产物，两种都在生成期拦。"""
        with self.assertRaises(ValueError):
            S.plan(DEFAULT_CFG, 0.05, FPS)
        with self.assertRaises(ValueError):
            S.plan(DEFAULT_CFG, 0.0, FPS)
        with self.assertRaises(ValueError):
            S.plan(DEFAULT_CFG, 40.0, FPS)
        self.assertTrue(S.is_well_formed(S.plan(DEFAULT_CFG, 0.2, FPS)))


class ProductContract(unittest.TestCase):
    def test_set_sequence_matches_plan_frame_by_frame(self):
        """产物级对账：条数、逐条时刻与变量全部等于 `_step.frames`。"""
        html = render(images=step_images())
        got = tile_sets(html)
        p = S.plan(DEFAULT_CFG, DURATION, FPS)
        want = S.frames(p)
        self.assertEqual(len(got), len(want))
        self.assertEqual(len(got), S.unique_frames(p))
        self.assertTrue(all(sel.startswith("#img-seg-a #")
                            for sel, _, _ in got),
                        "补间必须收在 #img-{sid} 作用域里，不能打到别页同名元素")
        pos = got[0][2]
        for (wt, wv), (gsel, gv, gt) in zip(want, got):
            self.assertAlmostEqual(gt - pos, wt, delta=0.011)
            self.assertEqual(gsel, "#img-seg-a #tile")
            self.assertEqual(gv, wv)
        self.assertEqual(got[0][1], {"y": 0.0})
        self.assertEqual(got[-1][1], {"y": -160.0})

    def test_unique_frames_not_render_frames(self):
        """这一档不是每帧一条：条数由 every 定，且相邻时刻约 K/F。"""
        for k, want in ((2, 16), (3, 11), (4, 9)):
            with self.subTest(every=k):
                got = tile_sets(render(images=step_images(
                    cfg={"every": k, "timing": 0.0})))
                self.assertEqual(len(got), want)
                self.assertLess(len(got), DURATION * FPS)
                gaps = [b[2] - a[2] for a, b in zip(got, got[1:])]
                self.assertTrue(all(
                    abs(g - k / FPS) < 0.011 for g in gaps[:-1]), gaps)

    def test_both_aspects_and_deterministic(self):
        """两画幅各验；逐帧 seek 的前提是两次生成一字节不差。"""
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                html = render(images=step_images(), aspect=aspect)
                self.assertEqual(len(tile_sets(html)), S.unique_frames(
                    S.plan(DEFAULT_CFG, DURATION, FPS)))
                self.assertEqual(html, render(images=step_images(),
                                             aspect=aspect))

    def test_knobs_drive_product(self):
        """契约是唯一真源：改旋钮产物跟着走，渲染端没有硬编码第二份。"""
        base = tile_sets(render(images=step_images()))
        # 位移与轴
        far = tile_sets(render(images=step_images(cfg={"distance": -240})))
        self.assertEqual(far[-1][1]["y"], -240.0)
        xs = tile_sets(render(images=step_images(
            cfg={"axis": "x", "distance": 120})))
        self.assertTrue(all("x" in v and "y" not in v for _, v, _ in xs))
        self.assertEqual(xs[-1][1]["x"], 120.0)
        # 时间抖动真的进了时刻，改波长也跟着变
        flat = tile_sets(render(images=step_images(cfg={"timing": 0.0})))
        self.assertEqual([v for _, v, _ in base], [v for _, v, _ in flat])
        self.assertNotEqual([t for _, _, t in base], [t for _, _, t in flat])
        other = tile_sets(render(images=step_images(cfg={"wavelength": 13})))
        self.assertNotEqual([t for _, _, t in base],
                            [t for _, _, t in other])
        # every 改条数（不是只改数值）
        self.assertLess(len(tile_sets(render(images=step_images(
            cfg={"every": 4})))), len(base))

    def test_default_html_has_no_step_and_invalid_fails_fast(self):
        """没写这一拍的页零新增补间；绕过契约层的坏值在生成期就炸。"""
        self.assertEqual(tile_sets(render()), [])
        for cfg in ({"ease": "none"}, {"distance": 0}, {"every": 9},
                    {"timing": 9}, {"axis": "z"}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                render(images=step_images(cfg=cfg))
        with self.assertRaises(ValueError):
            render(images=step_images(duration=0.01))
        self.assertNotIn("#tile", render())


class CarryContract(unittest.TestCase):
    def test_settled_state_carries_translate(self):
        """接续页第 0 帧：最后一张唯帧折成 translate，两轴都精确。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        root = ET.fromstring(out)
        self.assertEqual(notes, [])
        el = root.find(".//*[@id='tile']")
        self.assertIsNotNone(el)
        self.assertIn("translate(0,-160)", el.get("transform", ""))
        out_x, _ = SC.bake_settled_state(
            raw, [step_with(axis="x", distance=120)])
        el_x = ET.fromstring(out_x).find(".//*[@id='tile']")
        self.assertIn("translate(120,0)", el_x.get("transform", ""))

    def test_carry_does_not_leak_scale(self):
        """step 不带形变：静态副本里不许出现 scale。"""
        raw, _ = sanitize_svg_for_inline(CARRY_SVG)
        out, notes = SC.bake_settled_state(raw, [step_with()])
        self.assertEqual(notes, [])
        self.assertNotIn("scale(", out)
        self.assertNotIn("scaleX", out)
        self.assertIn("translate(0,-160)", out)

    def test_missing_target_fails_at_generation(self):
        """按文件拦：目标落空不会报错、只会静默没有动画。"""
        block = {"steps": [dict(step_with(), target="#gone")]}
        errs, _ = AN.check_animate(CARRY_SVG, block, "seg2", full_bleed=False)
        self.assertTrue(any("#gone" in e for e in errs), errs)
        errs2, _ = AN.check_animate(
            CARRY_SVG, {"steps": [step_with()]}, "seg2", full_bleed=True)
        self.assertEqual(errs2, [])


if __name__ == "__main__":
    unittest.main()
