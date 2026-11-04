#!/usr/bin/env python3
"""hold 编排（静止窗口 + 2% 慢推）的契约与**产物级**判据。

判据全部从生成的 HTML 里抠补间来读，不在 Python 这边再写一份"应该生成什么"
的实现：两份实现互相对照，一起错的时候测试照样绿。唯一的例外是 `_hold.plan`
——它是模板与渲染端共用的那份真源，测试拿它跟产物**对账**，而不是拿它替产物
背书（见 `ProductMatchesPlan`）。
"""
import contextlib
import io
import re
import unittest
from contextlib import contextmanager

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _hold
import html_renderer as hr
from _template import load_template
from html_renderer import generate_html

# 慢推是唯一一条 `tl.to("#img-... > img", {scale: ...})`：收拢是 fromTo、
# 容器落位是 from，形态不重样。按形态抓，别按 ease 名抓。
DRIFT = re.compile(
    r'tl\.to\("(#[^"]+ > img)",\{scale:([\d.]+),duration:([\d.]+),'
    r'ease:"([^"]+)"\},([\d.]+)\)')
# 收拢补间的终点 = 入场收尾（静止窗口的起点），用来独立反解 settle_at。
SETTLE = re.compile(
    r'tl\.fromTo\("(#[^"]+ > img)",\{[^}]*\},'
    r'\{scale:1,x:0,y:0,duration:([\d.]+),ease:"[^"]+"\},([\d.]+)\)')
CARD_START = re.compile(r'<div id="([\w-]+)" class="clip[^"]*"[^>]*data-start="([\d.]+)"')


def render(manifest=None, images=None, **kw):
    m = manifest if manifest is not None else H.make_manifest()
    imgs = images if images is not None else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


def drifts(html):
    return [{"sel": m.group(1), "scale": float(m.group(2)),
             "duration": float(m.group(3)), "ease": m.group(4),
             "at": float(m.group(5))} for m in DRIFT.finditer(html)]


@contextmanager
def hold_override(**overrides):
    """只改本次进程内的 `animation.hold`，验证模板是唯一真源。"""
    def patched():
        tpl = load_template()
        tpl["animation"]["hold"].update(overrides)
        return tpl

    orig = hr.load_template
    hr.load_template = patched
    try:
        yield
    finally:
        hr.load_template = orig


def compare_case():
    """并置对比：第 0 格静态图，第 1 格带 animate 的内联 SVG。

    `inline_svg` 是 `gen_hyperframes.animate_prepare` 在生成期读盘净化后**回填**
    的内部键（见 `_normalize_images` 的注释），渲染端 `_pane_carriers` 只认它：
    写了 `animate` 但没回填的格子在真实流水线里不存在，fixture 手搓这一步是
    沿用 `test_animate_timeline.Renderer._images` 的既有做法，免得为了测一条
    补间去写临时 SVG 文件。
    """
    s = H.sample_source()
    s["segments"][0]["layout"] = "compare"
    s["segments"][0]["text"] = "左边是常见写法。右边是这一版的写法。"
    m = H.make_manifest(s)
    images = H.sample_images(m)
    images["seg-a"] = {
        "src": ["images/seg-a-0.svg", "images/seg-a-1.svg"],
        "panes": [{},
                  {"animate": {"steps": [
                      {"at": 1, "target": "#b", "to": {"opacity": 1}}]},
                   "inline_svg": '<svg viewBox="0 0 980 735"><g id="b"/></svg>'}],
    }
    return m, images


class PlanContract(unittest.TestCase):
    def test_windows_tail_and_short_segment(self):
        """静止 / 慢推 / 末帧留白的几何，以及短段与关掉旋钮时的整段跳过。"""
        cfg = load_template()["animation"]["hold"]
        p = _hold.plan(3.0, 8.0, cfg, 24)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p["still_start"], 3.0)
        self.assertAlmostEqual(p["still_end"], 3.45)
        self.assertAlmostEqual(p["drift_start"], 3.45)
        self.assertAlmostEqual(p["tail"], 1 / 24)
        self.assertAlmostEqual(p["drift_end"], 8.0 - 1 / 24)
        self.assertAlmostEqual(p["duration"], p["drift_end"] - p["drift_start"])
        # 下限不写死在模板里：由幅度/帧率/缓动反解，且确实高于 minDriftSeconds
        self.assertGreater(p["min_duration"], cfg["minDriftSeconds"])
        self.assertAlmostEqual(
            p["min_duration"],
            _hold.min_drift_duration(cfg["scale"], 24, cfg["ease"]))
        # 余量不足下限 → 整段不慢推（不是"生成一条被切一半的推镜"）
        self.assertIsNone(_hold.plan(3.0, 3.45 + 1.0, cfg, 24))
        # scale=0 是明确关掉这一层
        self.assertIsNone(_hold.plan(3.0, 8.0, dict(cfg, scale=0), 24))
        # 红线在消费侧：越界值在生成期就炸，不留到成片里静默裁边
        for bad in (dict(cfg, stillSeconds=0.90), dict(cfg, stillSeconds=0.10),
                    dict(cfg, scale=0.05), dict(cfg, minDriftSeconds=0),
                    dict(cfg, ease="back.out(1.7)"), dict(cfg, ease="power3.out"),
                    dict(cfg, ease="spring(0.85)")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _hold.plan(3.0, 8.0, bad, 24)


class CurveContract(unittest.TestCase):
    def test_pure_monotone_and_bounded(self):
        """t 的纯函数：端点精确、静止段恒 1、单调、与调用顺序无关、慢得下来。"""
        cfg = load_template()["animation"]["hold"]
        p = _hold.plan(3.0, 8.0, cfg, 24)
        self.assertEqual(_hold.scale_at(0.0, p), 1.0)
        self.assertEqual(_hold.scale_at(p["still_end"] - 0.01, p), 1.0)
        self.assertEqual(_hold.scale_at(p["drift_start"], p), 1.0)
        self.assertEqual(_hold.scale_at(p["drift_end"] + 1.0, p), 1.02)
        # 静止窗口逐帧都是 1（最后一帧恰好踩在 0.45s 上，不越进慢推窗口）；
        # 慢推窗口单调不减且处处落在 [1, 1.02]
        n_still = int(cfg["stillSeconds"] * 24)
        ts = [p["still_start"] + i / 24 for i in range(1, n_still + 1)]
        self.assertTrue(all(t <= p["still_end"] + 1e-9 for t in ts))
        self.assertTrue(all(_hold.scale_at(t, p) == 1.0 for t in ts))
        vals = [_hold.scale_at(p["drift_start"] + i / 240, p) for i in range(0, 1000)]
        self.assertEqual(vals, sorted(vals))
        self.assertTrue(all(1.0 <= v <= 1.02 for v in vals))
        # 纯函数：跳着求、倒着求，同一个 t 恒等
        self.assertEqual(_hold.scale_at(p["drift_start"] + 1.0, p),
                         _hold.scale_at(p["drift_start"] + 1.0, p))
        _hold.scale_at(p["drift_end"] - 0.1, p)
        self.assertEqual(_hold.scale_at(p["drift_start"] + 1.0, p),
                         _hold.scale_at(p["drift_start"] + 1.0, p))
        # 慢推峰值必须 ≤ 入场首帧 4px 折算值的 1/4
        self.assertTrue(_hold.is_slow_enough(p, 24))
        self.assertLessEqual(_hold.max_step_bound(p, 24),
                             _hold.entry_step_scale() / _hold.MIN_DRIFT_RATIO)
        # 反证：把幅度/窗口改到"入场级"，判据必须红
        fast = dict(p, scale=0.08, duration=0.3)
        self.assertFalse(_hold.is_slow_enough(fast, 24))


class ActivityContract(unittest.TestCase):
    def test_activity_separates_still_drift_and_old_behaviour(self):
        """逐帧口径要能同时证明：该静的静、该动的动、旧行为整段是静帧。"""
        cfg = load_template()["animation"]["hold"]
        p = _hold.plan(3.0, 8.0, cfg, 24)
        f = _hold.frames(p, 24)
        self.assertGreater(f["still"], 0)
        self.assertGreater(f["drift"], 0)
        self.assertEqual(f["still_violations"], 0,
                         "静止窗口里出现了变化：慢推起手压进了留白")
        self.assertGreater(f["max_step"], 0)
        self.assertGreater(f["mean_step"], 0)
        self.assertLessEqual(f["max_step"],
                             _hold.entry_step_scale() / _hold.MIN_DRIFT_RATIO + 1e-9)
        # 反证：旧行为（落位后不生成慢推）在这个口径里是整段 0 变化。
        # 没有这条，"画面确实在动"就只是一句没有对照的断言。
        old = _hold.frames(None, 24)
        self.assertEqual((old["still"], old["drift"], old["max_step"]),
                         (0, 0, 0.0))
        # 跨界帧归慢推：静止帧数 = floor(still / 帧长)，不多也不少
        self.assertEqual(f["still"], int(cfg["stillSeconds"] * 24))


class ProductMatchesPlan(unittest.TestCase):
    def test_tween_matches_plan_in_both_aspects_and_is_deterministic(self):
        """产物级对账：补间的锚点/时长/幅度/缓动逐项等于 `_hold.plan`。"""
        cfg = load_template()["animation"]["hold"]
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                html = render(aspect=aspect)
                got = drifts(html)
                self.assertEqual(len(got), 1, f"{aspect} 应只有一条慢推补间")
                d = got[0]
                # 两个独立读数：收拢终点（入场收尾）与下一页 data-start（窗口末尾）
                settle = SETTLE.search(html)
                self.assertIsNotNone(settle, "找不到收拢补间，无法独立反解入场收尾")
                settle_at = float(settle.group(3)) + float(settle.group(2))
                starts = [(sid, float(t)) for sid, t in CARD_START.findall(html)]
                after = [t for sid, t in starts if t > settle_at + 0.01]
                self.assertTrue(after, "找不到下一页的起点作为可见窗口末尾")
                window_end = min(after)
                p = _hold.plan(settle_at, window_end, cfg, 24)
                self.assertIsNotNone(p)
                self.assertAlmostEqual(d["at"], p["drift_start"], delta=0.02)
                self.assertAlmostEqual(d["duration"], p["duration"], delta=0.02)
                self.assertAlmostEqual(d["scale"], 1 + p["scale"], delta=1e-6)
                self.assertEqual(d["ease"], p["ease"])
                self.assertEqual(d["sel"], "#img-seg-a > img")
                # 末帧留白：补间终点不能压到窗口右端（半开区间那一帧永不渲染）
                self.assertLess(d["at"] + d["duration"], window_end)
                # 确定性：逐帧 seek 的前提，两次生成必须一个字节不差
                self.assertEqual(html, render(aspect=aspect))


class ScopeContract(unittest.TestCase):
    def test_only_still_carriers_and_no_disallowed_properties(self):
        """作用域与收拢同集合；补间只写 scale；短段/画布/agenda 一条都不生成。"""
        html = render()
        ds = drifts(html)
        self.assertEqual({d["sel"] for d in ds}, {"#img-seg-a > img"})
        # 整页画布（seg-b）、开屏/结尾 agenda 卡都不该成为慢推目标
        for forbidden in ("#img-seg-b", "#img-opening", "#img-closing"):
            self.assertNotIn(f'tl.to("{forbidden}', html)
        # 只动合成层：不碰重排属性、不碰透明度与颜色（分色纪律）
        for raw in (m.group(0) for m in DRIFT.finditer(html)):
            for prop in ("opacity", "width", "height", "color", "x:", "y:"):
                self.assertNotIn(prop, raw)
        # 并置对比：只有静态图那一格有慢推，写了 animate 的内联 SVG 那格没有
        cm, ci = compare_case()
        comp_html = render(manifest=cm, images=ci)
        self.assertEqual({d["sel"] for d in drifts(comp_html)},
                         {"#img-seg-a-0 > img"})
        # 那格确实内联成活 DOM（不是靠"选择器碰巧落空"混过去的）
        self.assertIn('id="img-seg-a-1"', comp_html)
        self.assertIn('<g id="b"/>', comp_html)
        # 短段：hold 窗口不够反解下限 → 整段不慢推
        short = H.make_manifest(dur=0.5)
        self.assertEqual(drifts(render(manifest=short)), [])


class TemplateIsTheOnlySource(unittest.TestCase):
    def test_knobs_drive_product_and_bad_values_fail_fast(self):
        """改模板产物必须跟着走；`scale=0` 关得掉；越界值在生成期就拦。"""
        base = drifts(render())
        self.assertEqual(len(base), 1)
        # 静止窗口拉长 0.15s → 慢推锚点后移同样多、时长相应缩短
        with hold_override(stillSeconds=0.60):
            longer = drifts(render())
        self.assertAlmostEqual(longer[0]["at"] - base[0]["at"], 0.15, delta=0.02)
        self.assertLess(longer[0]["duration"], base[0]["duration"])
        # 幅度与缓动都取模板值，不是硬编码在渲染器里
        with hold_override(scale=0.01, ease="power2.inOut"):
            tuned = drifts(render())[0]
        self.assertAlmostEqual(tuned["scale"], 1.01)
        self.assertEqual(tuned["ease"], "power2.inOut")
        # 明确关掉这一层：一条都不生成
        with hold_override(scale=0):
            self.assertEqual(drifts(render()), [])
        # 消费侧护栏：模板被 --set 调坏时生成期直接炸
        for bad in ({"stillSeconds": 0.90}, {"scale": 0.05},
                    {"ease": "back.out(1.7)"}):
            with self.subTest(bad=bad), hold_override(**bad):
                with self.assertRaises(ValueError):
                    render()


if __name__ == "__main__":
    unittest.main()
