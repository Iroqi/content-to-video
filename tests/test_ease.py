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
# 振幅 >1 的档：旧实现的 sin 相位写成 (t-1-s)，只在 asin(1/p1)=π/2（振幅≤1）时
# 与 GSAP 相等，振幅 >1 时整条曲线静默走偏（t=0.05 处旧值 -0.051 / GSAP +0.706）。
# 这几组是重写成 GSAP 源码式之后补的对拍锁。
GSAP_ELASTIC_OUT_2_1 = [0, 0.705968, 1.104528, 1.287606, 1.334565, 1.306186,
                       1.244537, 1.175808, 1.114193, 1.065685, 1.03125, 1.009188,
                       0.996733, 0.991012, 0.989545, 0.990432, 0.992358, 0.994506,
                       0.996431, 0.997947, 1]
GSAP_ELASTIC_IN_2_1 = [0, 0.002053, 0.003569, 0.005494, 0.007642, 0.009568,
                      0.010455, 0.008988, 0.003267, -0.009188, -0.03125, -0.065685,
                      -0.114193, -0.175808, -0.244537, -0.306186, -0.334565,
                      -0.287606, -0.104528, 0.294032, 1]
GSAP_ELASTIC_INOUT_2_1 = [0, 0.001784, 0.003821, 0.005228, 0.001633, -0.015625,
                          -0.057097, -0.122268, -0.167283, -0.052264, 0.5, 1.052264,
                          1.167283, 1.122268, 1.057097, 1.015625, 0.998367, 0.994772,
                          0.996179, 0.998216, 1]
GSAP_BACK_IN_1_7 = [0, -0.003912, -0.0143, -0.029138, -0.0464, -0.064062,
                    -0.0801, -0.092488, -0.0992, -0.098213, -0.0875, -0.065037,
                    -0.0288, 0.023238, 0.0931, 0.182813, 0.2944, 0.429887,
                    0.5913, 0.780662, 1]


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
        # 实测 gsap.parseEase：`steps(4)`、`steps(4).in`、`steps(4,true).out` 都认；
        # 裸 steps 在 GSAP 里是 config 对象不是函数（tween 直接 throw），steps.in 的键
        # 不存在、静默回落缺省缓动——都必须给档数
        for good in ("steps(4)", "steps(4).in", "steps(4,true).out", "steps(1)"):
            self.assertTrue(E.ease_ok(good), good)
        for bad in ("steps", "steps.in", "steps.out", "steps.inOut", "steps.out(4)", "steps()"):
            self.assertFalse(E.ease_ok(bad), bad)
        with self.assertRaises(ValueError) as cm:
            E.validate_ease("steps", "steps[0] 的 ease")
        self.assertIn("档数", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            E.validate_ease("steps.in", "steps[0] 的 ease")
        self.assertIn("档数", str(cm.exception))

    def test_args_before_direction_silently_drop_the_direction_in_gsap(self):
        # 拿钉固 bundle 实测：GSAP 的查找键是第一个 "(" 之前的全部内容，括号之后的
        # 字符整体丢弃——back(1.7).in 演的是 back.out(1.7)，elastic(1,0.3).inOut 演的
        # 是 elastic.out(1,0.3)。带方向的族收这种写法就是把"静默换缓动"放进门，拒。
        for bad in ("back(1.7).in", "back(1.7).inOut", "elastic(1,0.3).inOut",
                    "elastic(1,0.3).in", "back(0.5).out", "bounce(2).in"):
            self.assertFalse(E.ease_ok(bad), bad)
            with self.assertRaises(ValueError) as cm:
                E.validate_ease(bad, "steps[0] 的 ease")
            self.assertIn("方向", str(cm.exception))
        # steps 没有方向可言，写了也被丢，曲线层同样忽略 mod——与 GSAP 一致，放行
        for good in ("steps(4).in", "steps(4,true).out", "steps(4,false)"):
            self.assertTrue(E.ease_ok(good), good)

    def test_steps_with_args_after_direction_rejected(self):
        # steps.out(4) 的 GSAP 查找键是 "steps.out"，不存在 → 回落缺省缓动
        for bad in ("steps.out(4)", "steps.in(4)", "steps.inOut(4,true)"):
            self.assertFalse(E.ease_ok(bad), bad)

    def test_direction_spelling_is_case_sensitive_like_gsap(self):
        # .inOut 认、.inout 不认（后者 GSAP 返回 null，静默换缺省缓动）
        for good in ("power2.inOut", "back.in", "elastic.out"):
            self.assertTrue(E.ease_ok(good), good)
        for bad in ("power2.inout", "sine.both", "power2.INOUT", "power2.InOut"):
            self.assertFalse(E.ease_ok(bad), bad)

    def test_legacy_class_suffix_only_with_capitalized_family(self):
        # GSAP 注册表：Bounce.easeOut 有、bounce.easeOut 与 BOUNCE.easeOut 没有
        # （后者 parseEase 返回 null，静默换缺省缓动）
        for good in ("Bounce.easeOut", "Back.easeIn", "Power2.easeInOut"):
            self.assertTrue(E.ease_ok(good), good)
        for bad in ("power2.easeIn", "back.easeOut", "BOUNCE.easeOut", "ELASTIC.easeIn"):
            self.assertFalse(E.ease_ok(bad), bad)

    def test_bare_class_name_rejected(self):
        # 裸类名（Back/Power2/Strong）在 GSAP 里是 {easeIn,easeOut,easeInOut} 对象不是
        # 函数，补间直接抛错（实测 tween throw）；裸名只认小写族名
        for bad in ("Back", "Power2", "Strong", "BACK", "Power3", "Bounce"):
            self.assertFalse(E.ease_ok(bad), bad)
            with self.assertRaises(ValueError) as cm:
                E.validate_ease(bad, "steps[0] 的 ease")
            self.assertIn("裸类名", str(cm.exception))
        for good in ("back", "power2", "strong", "bounce"):
            self.assertTrue(E.ease_ok(good), good)

    def test_malformed_and_non_string_rejected(self):
        for bad in ("", "  ", "power2..out", "back.out(", "po wer2", "power2.INOUT2x!",
                    None, 3, True):
            self.assertFalse(E.ease_ok(bad), repr(bad))

    def test_empty_parens_rejected(self):
        # GSAP 实测：steps()/elastic.out() 出 NaN 曲线，back.out() 变成 power2.out
        for bad in ("steps()", "elastic.out()", "back.out()", "power2()"):
            self.assertFalse(E.ease_ok(bad), bad)
            with self.assertRaises(ValueError) as cm:
                E.validate_ease(bad, "steps[0] 的 ease")
            self.assertIn("空括号", str(cm.exception))

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

    def test_elastic_amplitude_above_one_matches_pinned_gsap(self):
        # 振幅 >1：GSAP 的 out(t)=1+p1·2^(-10t)·sin((t-s)·2π/per)，s=(per/2π)·asin(1/p1)。
        # 旧实现的相位 (t-1-s) 只在 asin(1/p1)=π/2 时碰巧相等，这三个方向全对不上。
        self._assert("elastic.out(2,1)", GSAP_ELASTIC_OUT_2_1)
        self._assert("elastic.in(2,1)", GSAP_ELASTIC_IN_2_1)
        self._assert("elastic.inOut(2,1)", GSAP_ELASTIC_INOUT_2_1)

    def test_back_in_matches_pinned_gsap(self):
        # back.in = t²((r+1)t-r)（GSAP Yb 的 in 分支 1-Tm(1-t) 展开同式）
        self._assert("back.in(1.7)", GSAP_BACK_IN_1_7)

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

    def test_infinite_amplitude_and_period_rejected(self):
        # GSAP 实测：elastic.out(Infinity) 出 Infinity/NaN 坐标（不崩、不报错）
        for spec in ("elastic.out(Infinity)", "elastic.out(1,Infinity)",
                     "elastic.in(inf)"):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn("有限", str(cm.exception))

    def test_non_numeric_args_rejected(self):
        # GSAP 实测：elastic.out(abc) 与 elastic.out(1,0.3) 逐点相同（解析不出配置
        # 就整串回落缺省值）、steps(abc)/back.out(abc) 直接出 NaN 曲线——两头都不响
        for spec, needle in (("elastic.out(abc)", "数字"), ("elastic.out(1,x)", "数字"),
                             ("steps(abc)", "数字"), ("steps(4,x)", "true/false/1/0"),
                             ("back.out(abc)", "数字")):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn(needle, str(cm.exception))

    def test_fractional_and_negative_step_counts_rejected(self):
        # GSAP 按原值当格数（4.5 就按 4.5 格跳）、steps(-2) 恒 0；采样器与预览同式
        # 才对得上，契约层只收 ≥1 的整数
        for spec in ("steps(4.5)", "steps(0)", "steps(-2)", "steps(1.5)"):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn("整数", str(cm.exception))

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
        # back 的过冲量认任何有限数（正负 GSAP 都照算，采样器同式）；steps(4) 合法
        for spec in ("back.out(0)", "back.out(-1)", "steps(4)"):
            self.assertEqual(E.validate_ease(spec, "ease"), spec)

    def test_families_without_args_reject_them(self):
        # GSAP 实测对这些族的参数静默忽略（power2(3) 与 power2 逐点相同）——作者以为
        # 改了次数，同一条静默退化，目录层拒
        for spec in ("power2(3)", "none(3)", "sine(2)", "bounce(2)", "quad(2)",
                     "strong(2)", "linear(2)", "power2.out(3)"):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn("不认参数", str(cm.exception))

    def test_too_many_args_rejected(self):
        for spec, needle in (("elastic.out(1,0.3,9)", "最多认 2 个参数"),
                             ("back.out(1.7,2)", "最多认 1 个参数"),
                             ("steps(4,true,false)", "最多认 2 个参数")):
            with self.assertRaises(ValueError, msg=spec) as cm:
                E.validate_ease(spec, "ease")
            self.assertIn(needle, str(cm.exception))

    def test_back_zero_and_negative_overshoot_still_allowed(self):
        # back 的过冲量 0/负数 GSAP 都照算（有限曲线），采样器同式——不是静默退化
        for spec in ("back.out(0)", "back.in(-1)", "back.inOut(0.5)"):
            self.assertEqual(E.validate_ease(spec, "ease"), spec)


class LenientCatalog(unittest.TestCase):
    """`--allow-ease` 的宽松档：目录外的档名放行，形状坑照旧拒。

    分界线就一条：**形状对不对**与**GSAP core 认不认**是两件事。形状错的写法连
    预览都演不对（参数被丢、补间抛错），放行它等于让作者看着一张撒谎的预览；而
    族名不在目录里只是"渲染时退回缺省缓动"，预览里挑档时放行它有价值。
    """

    def tearDown(self):
        E.allow_extra(False)

    def test_extra_family_rejected_by_default(self):
        with self.assertRaises(ValueError):
            E.validate_ease("rough", "ease")

    def test_extra_family_passes_when_lenient_and_says_why(self):
        import contextlib
        import io
        E.allow_extra(True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(E.validate_ease("rough.inOut", "ease"), "rough.inOut")
        self.assertIn("静默退回缺省缓动", err.getvalue())

    def test_broken_shape_is_still_rejected_when_lenient(self):
        E.allow_extra(True)
        for spec in ("back(1.7).in", "Back", "elastic.out()", "steps"):
            with self.assertRaises(ValueError, msg=spec):
                E.validate_ease(spec, "ease")

    def test_catalog_families_are_unaffected(self):
        E.allow_extra(True)
        self.assertEqual(E.validate_ease("power2.out", "ease"), "power2.out")


if __name__ == "__main__":
    unittest.main()
