"""「受控弹簧」：带承接初速的闭式二阶阶跃响应（spring / spring(ζ)）。

仓库此前所有落位曲线都是多项式衰减（power*.out / expo.out）：首帧速度靠放大 y
硬凑，收尾没有质量。这一档补的是"带着上一拍的速度继续走、在落点上稳住"，而且
必须是**闭式**的——与 params 白名单禁 random / 时间函数同一条理由：逐帧 seek 下
靠帧间状态算出来的第 N 帧，和直接跳到第 N 帧会对不上。

要证的命题只有六条，见 references/spring.md「判据落在哪」：

- **端点精确**：f(0)=0、f(1)=1 是精确值，不是"接近"。末帧差 0.4% 在页界处就是回弹；
- **行程预算同尺**：新档不豁免仓库既有的首帧 ≥4px / 走完 90% ≤50% 时长。这条同时
  证明了为什么必须是"承接初速"——标准阶跃响应 x'(0)=0 四档里三档过不了；
- **过冲受控**：峰值 ≤5% 且只上穿一次，ζ=0.70 那种 9.2% 的卡通弹簧进不来；
- **纯函数**：JS 侧无墙钟、无累计状态，同一 p 重放两次恒等；
- **注册完备性**：spring 不在钉固的 GSAP core 里，产物里每个 spec 都必须精确注册，
  绝不注册一个会吞掉参数的裸名兜底；
- **分色纪律**：ζ<1 不许补间透明度与颜色（位置/尺寸可以）。

产物级判据照 test_type_decode.py 的办法：把产物里那段注册 JS 抠出来在 Node 里
stub 掉 gsap.registerEase 后重放，逐点比对 Python 曲线。Python 里再写一份 JS 实现
只会变成两份互相对照的实现，一起错的时候测试照样绿。
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _ease as E
from html_renderer import generate_html, _check_spring_color_usage

NODE = shutil.which("node")

FPS = 30
MIN_FIRST_FRAME_PX = 4.0     # 与 test_motion_design.py 同一条分界
TAIL_SHARE = 0.90
TAIL_MAX = 0.50
TRAVEL_PX = 40.0
DURATION = 0.5
ZETAS = (0.80, 0.85, 0.90, 0.95, 1.00)

# 在 Node 里重放产物注册块：不加载 GSAP，只把它需要的 registerEase 打桩，
# 于是"发出去的那段脚本真的这样算"能被直接读到，而测试不依赖网络取 GSAP。
_HARNESS = """
const fs = require("fs");
const body = fs.readFileSync(process.argv[2], "utf8");
var REG = {};
var gsap = {registerEase: function (n, f) { REG[n] = f; }};
eval(body);
var specs = JSON.parse(process.argv[3]);
var grid = JSON.parse(process.argv[4]);
var out = {};
for (var i = 0; i < specs.length; i++) {
  var f = REG[specs[i]];
  out[specs[i]] = f ? grid.map(function (p) { return f(p); }) : null;
}
process.stdout.write(JSON.stringify(out));
"""

_REGISTRY_RE = re.compile(r";\(function\(\)\{\nvar EPS=.*?\n\}\)\(\);", re.S)
_EASE_IN_PRODUCT_RE = re.compile(
    r"""["']?ease["']?\s*:\s*["']([^"']*spring[^"']*)["']""")
_REGISTERED_RE = re.compile(r'gsap\.registerEase\(("(?:[^"\\]|\\.)*"), ctvSpring\(')


def _spring_images(steps):
    m = H.make_manifest()
    imgs = H.sample_images(m)
    key = list(imgs)[0]
    imgs[key] = {"src": "images/%s.svg" % key, "animate": {"steps": steps}}
    return imgs


def _render(steps=None):
    m = H.make_manifest()
    imgs = (_spring_images(steps) if steps is not None else H.sample_images(m))
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs)


def _registry(html):
    m = _REGISTRY_RE.search(html)
    return m.group(0) if m else ""


def _replay(registry_js, specs, grid):
    """在 Node 里跑产物注册块，返回 {spec: [每个 p 的值]}。"""
    with tempfile.TemporaryDirectory() as d:
        reg = os.path.join(d, "registry.js")
        harness = os.path.join(d, "harness.js")
        with open(reg, "w", encoding="utf-8") as f:
            f.write(registry_js)
        with open(harness, "w", encoding="utf-8") as f:
            f.write(_HARNESS)
        r = subprocess.run(
            [NODE, harness, reg, json.dumps(specs), json.dumps(grid)],
            capture_output=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise AssertionError("Node 重放失败：%s" % r.stderr)
        return json.loads(r.stdout)


def _tail_frac(f):
    for i in range(1, 1001):
        if f(i / 1000.0) >= TAIL_SHARE:
            return i / 1000.0
    return 1.0


class SpringPhysics(unittest.TestCase):
    """数学判据：弹簧必须能进仓库已有的那张行程预算表。"""

    def test_endpoints_are_exact_for_every_zeta(self):
        # 末帧不是"趋近 1"，是精确 1：页界处差一点就是一次看得见的回弹。
        for z in ZETAS:
            with self.subTest(zeta=z):
                f = E.spring_curve(z)
                self.assertEqual(f(0.0), 0.0)
                self.assertEqual(f(1.0), 1.0)
                for spec in ("spring", "spring(%g)" % z):
                    if E.spring_zeta(spec) != z:
                        continue
                    self.assertEqual(E.curve(spec, "none")(0.0), 0.0)
                    self.assertEqual(E.curve(spec, "none")(1.0), 1.0)

    def test_first_frame_clears_the_4px_budget(self):
        # 0.5s @30fps、40px 行程。标准阶跃响应（初速 0）在这里只有 2~4px，
        # 这正是"承接初速"必须存在的理由，见 references/spring.md 第一节实采。
        p = 1.0 / FPS / DURATION
        for z in ZETAS:
            with self.subTest(zeta=z):
                px = TRAVEL_PX * E.spring_curve(z)(p)
                self.assertGreaterEqual(px, MIN_FIRST_FRAME_PX,
                                        "ζ=%g 首帧只走 %.2fpx" % (z, px))

    def test_tail_share_clears_the_50pct_budget(self):
        for z in ZETAS:
            with self.subTest(zeta=z):
                self.assertLessEqual(_tail_frac(E.spring_curve(z)), TAIL_MAX,
                                     "ζ=%g 后一半时间还在走最后 10%%" % z)

    def test_overshoot_within_budget_and_single_peak(self):
        for z in ZETAS:
            with self.subTest(zeta=z):
                f = E.spring_curve(z)
                vals = [f(i / 1000.0) for i in range(1001)]
                peak = max(vals)
                self.assertLessEqual(peak, 1.0 + E.SPRING_OVERSHOOT_MAX)
                crossings = sum(1 for a, b in zip(vals, vals[1:]) if a <= 1.0 < b)
                self.assertLessEqual(crossings, 1, "过冲不该来回折返")
        # 硬钉下界的实采值，防止"改了初速系数、判据跟着一起漂"。
        self.assertAlmostEqual(E.spring_overshoot(0.80), 0.0332, places=3)
        self.assertEqual(E.spring_overshoot(1.00), 0.0)

    def test_curve_is_a_pure_function(self):
        # 调用顺序不影响结果：不需要先算 0..N-1 才能得到第 N 帧。
        for z in ZETAS:
            with self.subTest(zeta=z):
                f = E.spring_curve(z)
                grid = [i / 37.0 for i in range(38)]
                first = [f(p) for p in grid]
                for p in reversed(grid):
                    f(p)
                self.assertEqual(first, [f(p) for p in grid])
                self.assertEqual(f(0.37), f(0.37))


class SpringContract(unittest.TestCase):
    """契约层：形状不对就在生成期 fail-fast，别留到成片里静默退化。"""

    def test_zeta_band_and_shape(self):
        ok = ("spring", "spring(0.8)", "spring(0.85)", "spring(1)", "spring(1.00)")
        bad = ("spring(0.79)", "spring(0.5)", "spring(0.7)", "spring(1.01)",
               "spring(abc)", "spring(0.8,2)", "spring()", "spring.out",
               "spring.in", "Spring", "spring(NaN)", "spring(Infinity)")
        for spec in ok:
            with self.subTest(accept=spec):
                E.validate_ease(spec, "[t]")
        for spec in bad:
            with self.subTest(reject=spec):
                with self.assertRaises(ValueError):
                    E.validate_ease(spec, "[t]")

    def test_extension_family_is_not_in_core_catalog(self):
        # EASE_FAMILIES 的语义是"钉固 GSAP core 里有哪些档"，不能掺进自注册档；
        # 宽松档也不该把正式支持的 spring 当成"未知族名"warn 成静默退化。
        self.assertNotIn("spring", E.EASE_FAMILIES)
        self.assertIn("spring", E.EXTENSION_FAMILIES)
        for on in (False, True):
            E.allow_extra(on)
            with self.subTest(allow_extra=on):
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    E.validate_ease("spring(0.85)", "[t]")
                self.assertEqual(err.getvalue(), "")
        E.allow_extra(False)

    def test_reject_overshoot_guards_precise_landing(self):
        E.reject_overshoot("spring", "[t]")           # ζ=1 零过冲
        E.reject_overshoot("power2.out", "[t]")
        with self.assertRaises(ValueError):
            E.reject_overshoot("spring(0.85)", "[t]")

    def test_curve_falls_back_linearly_on_broken_input(self):
        # 绕过契约层（手改过的 images.json / 老产物重放）不该把整页崩在曲线上。
        f = E.curve("spring(0.1)", "none")
        self.assertAlmostEqual(f(0.25), 0.25)
        self.assertAlmostEqual(f(1.0), 1.0)


class SpringInProduct(unittest.TestCase):
    """产物级：注册完备性 + 与 Python 曲线同式。"""

    def test_default_product_injects_nothing(self):
        # 默认一个字节都不加，四份 golden 才不会被一次"新增能力"整体挪位。
        html = _render()
        self.assertNotIn("gsap.registerEase", html)
        self.assertNotIn("ctvSpring", html)

    def test_every_spring_spec_is_registered_exactly(self):
        steps = [
            {"at": 0, "target": "#box", "to": {"x": 24, "y": -40},
             "ease": "spring(0.85)", "duration": 0.8},
            {"at": 1, "target": "#label", "to": {"scale": 1.04},
             "ease": "spring", "duration": 0.5},
        ]
        html = _render(steps)
        in_product = set(_EASE_IN_PRODUCT_RE.findall(html))
        registered = {json.loads(s) for s in _REGISTERED_RE.findall(html)}
        self.assertEqual(in_product, {"spring(0.85)", "spring"})
        self.assertEqual(registered, in_product)
        self.assertEqual(html.count("gsap.registerEase("), len(in_product))
        # 不注册兜底裸名：只用一个 spec 时，另一个不该被悄悄解析成别的函数。
        one = _render([steps[0]])
        self.assertEqual({json.loads(s) for s in _REGISTERED_RE.findall(one)},
                         {"spring(0.85)"})
        self.assertNotIn('gsap.registerEase("spring",', one)

    def test_registry_has_no_wallclock_or_accumulated_state(self):
        reg = _registry(_render([{"at": 0, "target": "#a", "to": {"y": 0},
                                  "ease": "spring(0.85)", "duration": 0.8}]))
        self.assertTrue(reg)
        for banned in ("Math.random", "Date(", "Date.now", "performance",
                       "setTimeout", "setInterval", "+=", "window."):
            self.assertNotIn(banned, reg, "注册代码里不该出现 %s" % banned)
        # 归一化靠 raw(p)/raw(1)，不是每帧把上一帧的速度累加起来。
        self.assertIn("var end=raw(1)", reg)
        self.assertIn("return raw(p)/end", reg)

    @unittest.skipIf(NODE is None, "没装 Node，产物级重放跳过")
    def test_registry_js_matches_python_curve_in_node(self):
        reg = _registry(_render([
            {"at": 0, "target": "#box", "to": {"y": 0},
             "ease": "spring(0.85)", "duration": 0.8},
            {"at": 1, "target": "#label", "to": {"scale": 1.04},
             "ease": "spring", "duration": 0.5},
        ]))
        specs = ["spring", "spring(0.85)", "spring(0.8)"]
        grid = [i / 40.0 for i in range(41)]
        got = _replay(reg, specs, grid)
        # 没用到的 spec 没有兜底注册——parseEase 拿不到函数，而不是拿到 ζ=1 那条。
        self.assertIsNone(got["spring(0.8)"])
        for spec in specs[:2]:
            z = E.spring_zeta(spec)
            want = [E.spring_curve(z)(p) for p in grid]
            for p, a, b in zip(grid, got[spec], want):
                self.assertAlmostEqual(a, b, places=9,
                                       msg="%s 在 p=%.3f 两侧不一致" % (spec, p))


class SpringColorDiscipline(unittest.TestCase):
    """分色红线：位置与尺寸可以弹，透明度与颜色永不弹。"""

    def test_color_tween_with_underdamped_spring_is_rejected(self):
        # 颜色必须写在补间变量里（to/from/set，attr 里的 SVG 颜色也算）；
        # 直接摆到 step 顶层是契约层自己会拒的另一种错，不归这道门禁管。
        cases = (
            {"to": {"opacity": 1}},
            {"to": {"fill": "#f59e0b"}},
            {"to": {"attr": {"stroke": "#fff"}}},
            {"set": {"backgroundColor": "#000"}},
        )
        for vars_ in cases:
            with self.subTest(vars=vars_):
                step = {"at": 0, "target": "#a", "ease": "spring(0.85)",
                        "duration": 0.8}
                step.update(vars_)
                with self.assertRaisesRegex(ValueError, "透明度与颜色不许过冲"):
                    _render([step])

    def test_position_tween_and_critical_color_are_allowed(self):
        ok = _render([
            {"at": 0, "target": "#a", "to": {"y": 0}, "from": {"y": 48},
             "ease": "spring(0.85)", "duration": 0.8},
            {"at": 1, "target": "#b", "to": {"opacity": 1},
             "ease": "spring", "duration": 0.5},
        ])
        self.assertIn("gsap.registerEase", ok)

    def test_missing_step_ease_uses_the_template_default(self):
        # 不写 ease 的拍走模板缺省；缺省若是 ζ<1 的弹簧，分色门禁照样要拦。
        imgs = _spring_images([{"at": 0, "target": "#a",
                                "to": {"opacity": 1}, "duration": 0.8}])
        _check_spring_color_usage(imgs, "spring")
        with self.assertRaises(ValueError):
            _check_spring_color_usage(imgs, "spring(0.85)")

    def test_panes_are_checked_too(self):
        imgs = _spring_images([])
        key = list(imgs)[0]
        imgs[key]["panes"] = [{"animate": {"steps": [
            {"at": 0, "target": "#a", "to": {"opacity": 1},
             "ease": "spring(0.85)", "duration": 0.8}]}}]
        with self.assertRaises(ValueError):
            _check_spring_color_usage(imgs, "power2.out")


if __name__ == "__main__":
    unittest.main()
