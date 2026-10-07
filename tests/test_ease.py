"""_ease：缓动档目录与 morph 采样曲线。

`CurveMatchesPinnedGsap` 那批期望值是拿技能真正钉固的那份 `gsap.min.js`
（`gen_hyperframes.GSAP_SHA256`）在 Node 里 `gsap.parseEase(name)` 逐点打印出来的，
不是照文档推的公式。改 GSAP 版本或改这些曲线时，重跑一遍对拍再来更新这些数。
"""
import unittest

import _helpers  # noqa: F401  —— 把 scripts/ 挂进 sys.path
import _ease as E

# t = i/20 (i=0..20) 上的 GSAP 真值，来自钉固 bundle
GSAP_BOUNCE_OUT = [0, 0.018906, 0.075625, 0.170156, 0.3025, 0.472656, 0.680625,
                   0.926406, 0.91, 0.818906, 0.765625, 0.750156, 0.7725, 0.832656,
                   0.930625, 0.972656, 0.94, 0.945156, 0.988125, 0.984531, 1]
GSAP_ELASTIC_OUT = [0, 0.646447, 1.25, 1.353553, 1.125, 0.911612, 0.875, 0.955806,
                    1.03125, 1.044194, 1.015625, 0.988951, 0.984375, 0.994476,
                    1.003906, 1.005524, 1.001953, 0.998619, 0.998047, 0.999309, 1]
GSAP_ELASTIC_IN = [0, 0.000691, 0.001953, 0.001381, -0.001953, -0.005524, -0.003906,
                   0.005524, 0.015625, 0.011049, -0.015625, -0.044194, -0.03125,
                   0.044194, 0.125, 0.088388, -0.125, -0.353553, -0.25, 0.353553, 1]
GSAP_STEPS_4 = [0, 0, 0, 0, 0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5,
                0.75, 0.75, 0.75, 0.75, 1, 1, 1, 1, 1]
GSAP_STEPS_4_IMM = [0.25, 0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5, 0.5,
                    0.75, 0.75, 0.75, 0.75, 0.75, 1, 1, 1, 1, 1, 1]
# 裸 `elastic.inOut` 的 GSAP 真值：inOut 的缺省周期是 .45（GSAP `_configElastic`
# 里 `period || (type ? .3 : .45)`，type 为空走 .45），不是 .3。从钉固 bundle 的
# `Elastic.easeInOut`（即裸名注册那条）逐点打印。
GSAP_ELASTIC_INOUT_BARE = [0, 0.000977, 0.000339, -0.003671, -0.003906, 0.011969,
                           0.023939, -0.03125, -0.117462, 0.043412, 0.5, 0.956588,
                           1.117462, 1.03125, 0.976061, 0.988031, 1.003906,
                           1.003671, 0.999661, 0.999023, 1]


def _samples(f):
    return [round(f(i / 20), 6) for i in range(21)]


class Catalog(unittest.TestCase):
    def test_every_catalog_family_parses_with_three_directions(self):
        # 目录里的族名必须真的被钉固的 GSAP 解析得出；这条是"目录=事实"的锁
        for fam in ("power1", "power2", "power3", "power4", "quad", "cubic", "quart",
                    "quint", "strong", "sine", "expo", "circ", "back", "elastic",
                    "bounce", "linear", "power0"):
            for mod in ("in", "out", "inOut"):
                self.assertTrue(E.ease_ok(f"{fam}.{mod}"), f"{fam}.{mod}")

    def test_plugin_only_eases_are_rejected(self):
        # rough / slow / CustomEase / stepped 属于 EasePack 与插件，core dist 里没有。
        # 写了不会报错，GSAP 静默换缓动——所以目录不收，契约层当场拒。
        for bad in ("rough", "rough()", "slow", "slow(0.7,0.7,false)",
                    "CustomEase", "stepped(4)", "MorphSVG", "DrawSVG"):
            self.assertFalse(E.ease_ok(bad), bad)

    def test_none_takes_no_direction(self):
        self.assertTrue(E.ease_ok("none"))
        for bad in ("none.in", "none.out", "none.inOut"):
            self.assertFalse(E.ease_ok(bad), bad)      # 实测 GSAP 解析不出 none.in

    def test_steps_direction_needs_a_step_count(self):
        # 实测 gsap.parseEase：裸 `steps`、`steps(4)`、`steps(4).in` 都认，`steps.in` 不认
        for good in ("steps", "steps(4)", "steps(4).in", "steps(4,true).out"):
            self.assertTrue(E.ease_ok(good), good)
        for bad in ("steps.in", "steps.out", "steps.inOut"):
            self.assertFalse(E.ease_ok(bad), bad)
        with self.assertRaises(ValueError) as cm:
            E.validate_ease("steps.in", "steps[0] 的 ease")
        self.assertIn("档数", str(cm.exception))

    def test_malformed_and_non_string_rejected(self):
        for bad in ("", "  ", "power2..out", "back.out(", "po wer2", "power2.INOUT2x!",
                    None, 3, True):
            self.assertFalse(E.ease_ok(bad), repr(bad))

    def test_validate_ease_raises_with_catalog_hint(self):
        with self.assertRaises(ValueError) as cm:
            E.validate_ease("rough", "段落 'seg-a' 的 steps[0] 的 ease")
        msg = str(cm.exception)
        self.assertIn("rough", msg)
        self.assertIn("elastic", msg)          # 报错要给出可用清单，而不是只说"不行"
        self.assertEqual(E.validate_ease("bounce.out", "x"), "bounce.out")


class Split(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(E.split_ease("power2.out"), ("power2", "out", []))
        self.assertEqual(E.split_ease("back.out(1.7)"), ("back", "out", ["1.7"]))
        self.assertEqual(E.split_ease("elastic.inOut(1,0.3)"),
                         ("elastic", "inOut", ["1", "0.3"]))
        self.assertEqual(E.split_ease("steps(4,true)"), ("steps", "out", ["4", "true"]))

    def test_bare_name_defaults_to_out_like_gsap(self):
        # 实测 GSAP：裸 power2 与 power2.out 逐点相同
        self.assertEqual(E.split_ease("power2")[1], "out")

    def test_legacy_class_names(self):
        self.assertEqual(E.split_ease("Bounce.easeOut"), ("bounce", "out", []))
        self.assertEqual(E.split_ease("strong.out"), ("power4", "out", []))


class CurveMatchesPinnedGsap(unittest.TestCase):
    def _assert(self, name, gold):
        got = _samples(E.curve(name, "power2.out"))
        for t, (g, w) in enumerate(zip(got, gold)):
            self.assertAlmostEqual(g, w, places=3,
                                   msg=f"{name} 在 t={t/20:.2f} 与 GSAP 不一致（{g} vs {w}）")

    def test_bounce_all_directions(self):
        self._assert("bounce.out", GSAP_BOUNCE_OUT)
        self._assert("bounce.in", [round(1 - GSAP_BOUNCE_OUT[20 - i], 6) for i in range(21)])
        # bounce.in 的 GSAP 实测值正是 out 的反射，逐点核过

    def test_elastic_out_and_in(self):
        self._assert("elastic.out", GSAP_ELASTIC_OUT)
        self._assert("elastic.out(1,0.3)", GSAP_ELASTIC_OUT)
        self._assert("elastic.in", GSAP_ELASTIC_IN)

    def test_elastic_inout_bare_matches_pinned_gsap(self):
        # 裸 elastic.inOut 的缺省周期是 .45（GSAP 注册时 type 为空走 .45 分支）；
        # 采样器必须跟它一致，否则 morph 与同名补间的手感分家。
        self._assert("elastic.inOut", GSAP_ELASTIC_INOUT_BARE)
        self._assert("elastic.inOut(1,0.45)", GSAP_ELASTIC_INOUT_BARE)

    def test_elastic_amplitude_below_one_scales_period(self):
        # GSAP：amplitude<1 时 p1 钳到 1、周期按 1/振幅 放大（等价于 (1, p/a)），
        # 实测 (0.6,0.18)≡(1,0.3)。采样器照源码实现，这几对必须逐点相同。
        pairs = [("elastic.out(0.6,0.18)", "elastic.out(1,0.3)"),
                 ("elastic.in(0.5,0.4)", "elastic.in(1,0.8)"),
                 ("elastic.inOut(0.5,0.45)", "elastic.inOut(1,0.9)")]
        for a, b in pairs:
            fa, fb = E.curve(a, ""), E.curve(b, "")
            for i in range(101):
                self.assertAlmostEqual(fa(i / 100), fb(i / 100), places=9,
                                       msg=f"{a} 与 {b} 在 t={i/100:.2f} 分家")

    def test_steps_with_and_without_immediate(self):
        self._assert("steps(4)", GSAP_STEPS_4)
        self._assert("steps(4,true)", GSAP_STEPS_4_IMM)

    def test_existing_families_unchanged_by_the_rewrite(self):
        # 采样器换实现前后这些档逐点相同（回归锁）。expo 与 GSAP 差 ≤0.008 是**旧实现
        # 就有的**（GSAP 的 Expo 常数与标准式不同），故意不"顺手修"——修了会把已有
        # 项目里每条 morph 的中间形状整体挪位，那是视觉漂移不是缺陷修复。
        self.assertAlmostEqual(E.curve("power2.out", "")(0.35),
                               1 - 0.65 ** 3, places=9)
        self.assertAlmostEqual(E.curve("back.out", "")(0.05), 0.219409, places=4)
        self.assertAlmostEqual(E.curve("circ.inOut", "")(0.55), 0.717945, places=4)
        self.assertAlmostEqual(E.curve("expo.out", "")(0.5), 1 - 2 ** -5, places=9)


class CurveShape(unittest.TestCase):
    def test_endpoints_fixed_for_every_family(self):
        for fam in sorted(E.EASE_FAMILIES):
            if fam == "steps":
                fam = "steps(4)"
            if fam == "none":
                continue
            for mod in ("in", "out", "inOut"):
                f = E.curve(f"{fam}.{mod}", "")
                self.assertAlmostEqual(f(0), 0.0, places=6, msg=f"{fam}.{mod} 起点")
                self.assertAlmostEqual(f(1), 1.0, places=6, msg=f"{fam}.{mod} 终点")

    def test_monotone_for_non_overshoot_families(self):
        for fam in ("power2", "sine", "expo", "circ", "quad", "quint"):
            for mod in ("in", "out", "inOut"):
                vals = [E.curve(f"{fam}.{mod}", "")(i / 40) for i in range(41)]
                self.assertTrue(all(b >= a - 1e-12 for a, b in zip(vals, vals[1:])),
                                f"{fam}.{mod} 不该回头")

    def test_overshoot_families_actually_overshoot(self):
        # 补这两个族就是为了"过冲落定"的手感，采样曲线要真能越界
        self.assertGreater(max(E.curve("elastic.out", "")(i / 100) for i in range(101)), 1.2)
        self.assertGreater(max(E.curve("back.out", "")(i / 100) for i in range(101)), 1.05)

    def test_direction_is_not_symmetric_for_out_and_in(self):
        self.assertGreater(E.curve("power2.out", "")(0.5), 0.5)
        self.assertLess(E.curve("power2.in", "")(0.5), 0.5)

    def test_parameters_change_the_curve(self):
        # 单点会撞巧（elastic.out 与 elastic.out(1,0.6) 在 t=0.2 就同值），整条曲线比
        def spread(a, b):
            fa, fb = E.curve(a, ""), E.curve(b, "")
            return max(abs(fa(i / 100) - fb(i / 100)) for i in range(101))
        self.assertGreater(spread("back.out", "back.out(4)"), 0.1)
        self.assertGreater(spread("elastic.out", "elastic.out(1,0.6)"), 0.1)
        self.assertGreater(spread("steps(4)", "steps(8)"), 0.1)

    def test_default_from_template_when_name_missing(self):
        self.assertAlmostEqual(E.curve(None, "power2.out")(0.5),
                               E.curve("power2.out", "")(0.5), places=9)
        self.assertAlmostEqual(E.curve("", "none")(0.5), 0.5, places=9)

    def test_unknown_family_falls_back_to_linear_without_raising(self):
        # 契约层已经拦过，这里只是渲染端不被一条算不出的曲线打断整页
        f = E.curve("customEaseNope", "power2.out")
        self.assertAlmostEqual(f(0.4), 0.4, places=9)


class ElasticArgs(unittest.TestCase):
    """elastic 的两个参数：非正当非法写法拒掉，采样器兜底不崩。

    振幅进分母（`period / (amplitude < 1 ? amplitude : 1)`），0 在 Python 里是
    ZeroDivisionError、负数是负周期——GSAP 那边两个都不崩，只是产出一条谁也说不清
    的怪曲线且不报错。所以判据放在契约层：写了就必须 > 0。
    """

    def test_zero_and_negative_amplitude_rejected(self):
        for spec in ("elastic.out(0)", "elastic.out(-1)", "elastic.in(0)",
                     "elastic.inOut(0.0)"):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn("振幅", str(cm.exception))

    def test_zero_and_negative_period_rejected(self):
        for spec in ("elastic.out(1,0)", "elastic.out(1,-0.3)"):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn("周期", str(cm.exception))

    def test_legal_amplitude_below_one_still_allowed(self):
        # 振幅 <1 是 GSAP 的合法写法（钳到 1、周期按 1/振幅 放大），别误伤
        for spec in ("elastic.out(0.6,0.18)", "elastic.in(0.5,0.4)",
                     "elastic.inOut(0.5,0.45)"):
            self.assertEqual(E.validate_ease(spec, "ease"), spec)

    def test_bare_and_valid_pairs_pass(self):
        for spec in ("elastic.out", "elastic.in", "elastic.inOut",
                     "elastic.out(1,0.3)", "elastic.out(2)"):
            self.assertEqual(E.validate_ease(spec, "ease"), spec)

    def test_curve_survives_illegal_args_without_raising(self):
        # 渲染端最后一道兜底：绕过契约层的输入（手改过的 images.json）不该把整页
        # 渲染崩在除零上。回落到缺省振幅 1.0，与合法裸名同值。
        for spec in ("elastic.out(0)", "elastic.out(-1)", "elastic.out(1,-0.3)"):
            f = E.curve(spec, "none")
            self.assertAlmostEqual(f(0.5), E.curve("elastic.out", "none")(0.5),
                                   places=9, msg=spec)
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            self.assertTrue(-2.0 < E.curve("elastic.out(0)", "none")(t) < 2.0)

    def test_other_families_unaffected(self):
        # 只有 elastic 查参数：back 的过冲量、steps 的档数另有自己的口径
        for spec in ("back.out(0)", "back.out(-1)", "steps(4)"):
            self.assertEqual(E.validate_ease(spec, "ease"), spec)


if __name__ == "__main__":
    unittest.main()
