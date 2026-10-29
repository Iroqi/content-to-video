"""参数演绎（`derive`）：表达式引擎、契约层、生成期体检、渲染四层回归。

产品判断写在 `scripts/_derive.py` 的模块注释里，这里只钉行为。四组判据各钉一件事：

- **表达式引擎**：能算的就是"算术 + 白名单函数"，别的一律拒——这条线一松，
  "信源不可信"就只剩一句口号。同时钉"Python 求值"与"JS 代码生成"同源一致，
  否则门禁算的和成片演的是两个东西。
- **契约层**：键集封闭、参数名、属性白名单、端点有限。
- **生成期**：落空 / 越界 / 出窗 / 起始帧对不上 / 与属性补间抢同一属性。
- **渲染**：作用域收窄、代理补间逐帧重算、页里不出现 `eval`。
"""
import math
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _derive as D
from _derive import (ExprError, compile_derive, compile_expr, format_value,
                     js_expr)
from _images_schema import validate_images_json
import gen_hyperframes as G
import html_renderer as HR


SEG_A_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" viewBox="0 0 980 735">'
    '<circle id="hub" cx="490" cy="367" r="20"/>'
    '<line id="arm" x1="490" y1="367" x2="750" y2="367"/>'
    '<circle id="tip" cx="750" cy="367" r="14"/>'
    "</svg>")


def _write_svg(tmp, sid, text=SEG_A_SVG):
    d = os.path.join(tmp, "images")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{sid}.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"images/{sid}.svg"


def _block(**over):
    """一个最小的、在 SEG_A_SVG 上成立的 animate 块（θ 转 90°：针尖沿圆弧走）。"""
    b = {
        "params": {"theta": 0},
        "steps": [{"at": 0, "param": "theta", "to": 90, "duration": 1.5}],
        "bind": [{"target": "#tip", "attr": "cx", "expr": "490 + 260*cos(theta*pi/180)"},
                 {"target": "#tip", "attr": "cy", "expr": "367 + 260*sin(theta*pi/180)"},
                 {"target": "#arm", "attr": "x2", "expr": "490 + 260*cos(theta*pi/180)"}],
    }
    b.update(over)
    return b


class Expr(unittest.TestCase):
    """表达式引擎：收什么、拒什么、怎么跨语言一致。"""

    def ev(self, src, **params):
        return compile_expr(src, params or {"x": 1})(
            {k: (1 if v is None else v) for k, v in params.items()} or {"x": 1})

    def test_arithmetic_and_precedence(self):
        self.assertEqual(self.ev("1 + 2*3"), 7)
        self.assertEqual(self.ev("(1 + 2)*3"), 9)
        self.assertEqual(self.ev("-3 + 1"), -2)
        self.assertEqual(self.ev("7 % 4"), 3)
        self.assertEqual(self.ev("2^3^2"), 512)      # 右结合，与数学写法一致
        self.assertEqual(self.ev("2**-1"), 0.5)

    def test_params_and_constants(self):
        self.assertAlmostEqual(self.ev("x*2", x=21), 42)
        self.assertAlmostEqual(self.ev("pi"), math.pi)
        self.assertAlmostEqual(self.ev("cos(0)"), 1.0)
        self.assertAlmostEqual(self.ev("tau/e"), math.tau / math.e)

    def test_exponent_carrot_means_pow(self):
        # `^` 在 Python 里是异或、在 JS 里不存在——两边都必须编译成乘方，否则
        # "门禁算的"和"成片演的"从第一个表达式就分家了。
        self.assertEqual(self.ev("2^10"), 1024)
        self.assertIn("Math.pow", js_expr("2^10", {"x": 1}))

    def test_rejected_names(self):
        for src in ("__import__('os')", "x.real", "[1,2]", "{'a':1}", "lambda: 1",
                    "x if x else 0", "x;y", "'a'+'b'", "open('/etc/passwd')",
                    "().__class__"):
            with self.subTest(src=src):
                with self.assertRaises(ExprError):
                    compile_expr(src, {"x": 1})

    def test_unknown_param_rejected(self):
        with self.assertRaises(ExprError) as cm:
            compile_expr("theta + 1", {"phi": 0})
        self.assertIn("theta", str(cm.exception))

    def test_unknown_function_rejected(self):
        with self.assertRaises(ExprError) as cm:
            compile_expr("random()", {"x": 0})
        self.assertIn("random", str(cm.exception))

    def test_length_cap(self):
        with self.assertRaises(ExprError) as cm:
            compile_expr("1+" * 200 + "1", {"x": 0})
        self.assertIn(str(D.EXPR_MAX_CHARS), str(cm.exception))

    def test_arbitrary_arity_checked(self):
        with self.assertRaises(ExprError):
            compile_expr("sin(1, 2)", {"x": 0})
        with self.assertRaises(ExprError):
            compile_expr("sin()", {"x": 0})

    def test_division_by_zero_is_runtime_not_compile_time(self):
        # 编译期不拒（0 是合法分母，只是这一帧算不出来），运行期报 ExprError
        fn = compile_expr("1/x", {"x": 0})
        with self.assertRaises(ExprError) as cm:
            fn({"x": 0})
        self.assertIn("除以 0", str(cm.exception))

    def test_js_and_python_agree(self):
        """同一个表达式，Python 求值与生成的 JS 代码在若干采样点必须同值。

        JS 那侧不真跑 JS：这里断言的是**生成的代码形态**（记号一致、`^` 是
        `Math.pow`、常量已折成字面量），真正的数值一致性由 `js_payload` 的
        参数表保证——它与 `compile_expr` 共用同一个 AST。
        """
        src = "490 + 260*cos(theta*pi/180) + 3^2"
        js = js_expr(src, {"theta": 0})
        for bad in ("eval", "theta.real", "__", "function"):
            self.assertNotIn(bad, js)
        self.assertIn("Math.cos", js)
        self.assertIn("Math.pow", js)
        self.assertIn(repr(float(490)).rstrip("0").rstrip(".") if False else "490",
                      js.replace(".0", ""))
        fn = compile_expr(src, {"theta": 0})
        self.assertAlmostEqual(fn({"theta": 0}), 490 + 260 + 9, places=6)
        self.assertAlmostEqual(fn({"theta": 90}), 490 + 0 + 9, places=6)

    def test_format_value(self):
        self.assertEqual(format_value(540.0), "540")
        self.assertEqual(format_value(539.99999), "540")
        self.assertEqual(format_value(-0.00001), "0")
        self.assertEqual(format_value(0.1 + 0.2), "0.3")


class Derived(unittest.TestCase):
    """编译后的块：补间累进、几何重算、逐帧幂等。"""

    def test_initial_geometry_matches_params(self):
        d = compile_derive(_block())
        g = dict({(sel, attr): v for sel, attr, v in d.geometry(d.env_at([0.0]))})
        self.assertAlmostEqual(g[("#tip", "cx")], 750.0, places=6)
        self.assertAlmostEqual(g[("#tip", "cy")], 367.0, places=6)

    def test_midpoint_is_real_geometry_not_chord(self):
        """θ=45° 时针尖到圆心的距离必须还是 260——插值走的是弦，会短一截。"""
        d = compile_derive(_block(ease="none"))
        g = dict({(sel, attr): v for sel, attr, v in d.geometry(d.env_at([0.5]))})
        dx = g[("#tip", "cx")] - 490
        dy = g[("#tip", "cy")] - 367
        self.assertAlmostEqual(math.hypot(dx, dy), 260.0, places=6)
        self.assertGreater(g[("#tip", "cx")], 490)   # 真的转过 45°
        self.assertGreater(g[("#tip", "cy")], 367)

    def test_second_step_continues_from_first(self):
        b = _block(steps=[{"at": 0, "param": "theta", "to": 45, "duration": 1,
                           "ease": "none"},
                          {"at": 1, "param": "theta", "to": 90, "duration": 1,
                           "ease": "none"}])
        d = compile_derive(b)
        e = d.env_at([1.0, 0.0])          # 第一拍演完、第二拍未开始
        self.assertAlmostEqual(e["theta"], 45.0)
        e = d.env_at([1.0, 1.0])
        self.assertAlmostEqual(e["theta"], 90.0)
        self.assertAlmostEqual(e["theta"], 90.0)

    def test_frame_idempotent(self):
        """同一时刻算两遍得到同一串属性——逐帧 seek 可复现的前提。"""
        d = compile_derive(_block())
        a = d.geometry(d.env_at([0.37]))
        b = d.geometry(d.env_at([0.37]))
        self.assertEqual(a, b)

    def test_out_of_order_evaluation_is_stable(self):
        d = compile_derive(_block())
        first = d.geometry(d.env_at([0.8]))
        d.geometry(d.env_at([0.1]))
        d.geometry(d.env_at([1.0]))
        self.assertEqual(d.geometry(d.env_at([0.8])), first)

    def test_non_finite_geometry_rejected(self):
        b = _block(bind=[{"target": "#tip", "attr": "cx", "expr": "1/(theta-0)"}])
        d = compile_derive(b)
        with self.assertRaises(ExprError):
            d.geometry(d.env_at([0.0]))

    def test_js_payload_scope_and_shape(self):
        d = compile_derive(_block())
        p = d.js_payload("#img-seg-a")
        self.assertEqual(p["binds"][0]["sel"], "#img-seg-a #tip")
        self.assertEqual(p["binds"][0]["attr"], "cx")
        self.assertEqual(p["params"], {"theta": 0})
        self.assertEqual(len(p["steps"]), 1)
        self.assertEqual(set(p["steps"][0]), {"p", "a", "b", "ease"})


class Schema(unittest.TestCase):
    """契约层：形状与取值。"""

    def _v(self, block, src="images/seg4.svg"):
        validate_images_json({"seg4": {"src": src, "animate": block}})

    def test_valid_block_accepted(self):
        self._v(_block())

    def test_non_svg_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self._v(_block(), src="images/seg4.png")
        self.assertIn("SVG", str(cm.exception))

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            self._v(_block(morph={}))
        with self.assertRaises(ValueError):
            self._v(_block(steps=[{"at": 0, "param": "theta", "to": 1,
                                   "target": "#tip"}]))

    def test_bad_param_name_rejected(self):
        with self.assertRaises(ValueError):
            self._v(_block(params={"1x": 0}))
        with self.assertRaises(ValueError):
            self._v(_block(params={"theta": "0"}))

    def test_param_must_be_declared(self):
        with self.assertRaises(ValueError) as cm:
            self._v(_block(steps=[{"at": 0, "param": "phi", "to": 1}]))
        self.assertIn("phi", str(cm.exception))

    def test_to_is_required(self):
        with self.assertRaises(ValueError) as cm:
            self._v(_block(steps=[{"at": 0, "param": "theta"}]))
        self.assertIn("to", str(cm.exception))

    def test_attr_whitelist(self):
        for attr in ("d", "points", "fill", "opacity", "onclick", None):
            with self.subTest(attr=attr):
                with self.assertRaises(ValueError):
                    self._v(_block(bind=[{"target": "#tip", "attr": attr,
                                          "expr": "theta"}]))

    def test_bad_target_rejected(self):
        with self.assertRaises(ValueError):
            self._v(_block(bind=[{"target": "#a .b", "attr": "x", "expr": "theta"}]))

    def test_bad_expr_rejected(self):
        with self.assertRaises(ValueError):
            self._v(_block(bind=[{"target": "#tip", "attr": "cx",
                                  "expr": "__import__('os')"}]))

    def test_empty_params_and_binds_rejected(self):
        with self.assertRaises(ValueError):
            self._v(_block(params={}))
        with self.assertRaises(ValueError):
            self._v(_block(bind=[]))

    def test_anchor_and_duration_rules_reused(self):
        """锚点/时长/缓动走属性补间那份判据：不许参数演绎变成偷偷放宽的后门。"""
        for step in ({"param": "theta", "to": 1},                       # 没锚点
                     {"at": 0, "at_time": 1, "param": "theta", "to": 1},
                     {"at": 0, "param": "theta", "to": 1, "duration": -1},
                     {"at": -1, "param": "theta", "to": 1},
                     {"at": 0, "param": "theta", "to": 1, "ease": "wat"},
                     {"at": 0, "param": "theta", "to": float("inf")}):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    self._v(_block(steps=[step]))

    def test_step_grammar_matches_tween(self):
        """同一条错误文案在两个语法下都指认同一处（探针借用了属性补间的判据）。"""
        with self.assertRaises(ValueError) as cm:
            self._v(_block(steps=[{"at": 0, "param": "theta", "to": 1, "duration": 0}]))
        msg = str(cm.exception)
        self.assertIn("seg4", msg)
        self.assertIn("steps[0]", msg)
        self.assertNotIn("images.json 的 images.json", msg)

    def test_compare_pane_derive_accepted(self):
        validate_images_json({"seg4": {
            "src": ["images/a.svg", "images/b.svg"],
            "panes": [{"animate": _block()}, {}]}})

    def test_compare_pane_derive_requires_svg(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {
                "src": ["images/a.png", "images/b.svg"],
                "panes": [{"animate": _block()}, {}]}})

    def test_compare_single_image_keys_still_ban_derive(self):
        # 单图键挂 derive 是早期写法，应当被拒（只有 panes[i] 里才有意义）
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {
                "src": ["images/a.svg", "images/b.svg"], "animate": _block()}})


class Prepare(unittest.TestCase):
    """生成期：净化内联、落空、越界、出窗、起始帧、抢属性。"""

    def _run(self, images, m=None):
        return G.animate_prepare(images, (m or H.make_manifest())["segments"], self.tmp)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_block_inlines_and_passes(self):
        src = _write_svg(self.tmp, "seg-a")
        images = {"seg-a": {"src": src, "animate": _block()}}
        errs, warns = self._run(images)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])
        self.assertIn('id="tip"', images["seg-a"]["inline_svg"])

    def test_missing_target_is_an_error(self):
        src = _write_svg(self.tmp, "seg-a")
        b = _block(bind=[{"target": "#ghost", "attr": "x", "expr": "theta"}])
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertEqual(len(errs), 1)
        self.assertIn("#ghost", errs[0])
        self.assertIn("静默", errs[0])

    def test_class_target_accepted(self):
        src = _write_svg(self.tmp, "seg-a",
                         SEG_A_SVG.replace('id="tip"', 'class="mark" id="tip"'))
        b = _block(bind=[{"target": ".mark", "attr": "r", "expr": "14 + theta/10"}])
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertEqual(errs, [])

    def test_at_out_of_range_is_an_error(self):
        src = _write_svg(self.tmp, "seg-a")
        b = _block(steps=[{"at": 9, "param": "theta", "to": 90}])
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertTrue(any("越界" in e for e in errs), errs)

    def test_beat_outside_window_warns(self):
        """at_time 落在下一段旁白之后：那一拍在成片里根本不演。"""
        m = H.make_manifest()
        seg = next(s for s in m["segments"] if s["id"] == "seg-a")
        late = float(seg["sentences"][0]["start_time"]) + 3600
        src = _write_svg(self.tmp, "seg-a")
        b = _block(steps=[{"at_time": late, "param": "theta", "to": 90}])
        _, warns = self._run({"seg-a": {"src": src, "animate": b}}, m)
        self.assertTrue(any("animate.steps[0]" in w for w in warns), warns)
        self.assertTrue(any("「theta」" in w for w in warns), warns)

    def test_start_frame_mismatch_warns(self):
        """SVG 里静态值是 800，而表达式在初值处算 750：第一帧会跳一下。"""
        svg = SEG_A_SVG.replace('id="tip" cx="750"', 'id="tip" cx="800"')
        src = _write_svg(self.tmp, "seg-a", svg)
        b = _block(bind=[{"target": "#tip", "attr": "cx",
                          "expr": "490 + 260*cos(theta*pi/180)"}])
        _, warns = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertTrue(any("跳" in w for w in warns), warns)

    def test_start_frame_match_is_silent(self):
        src = _write_svg(self.tmp, "seg-a")
        _, warns = self._run({"seg-a": {"src": src, "animate": _block()}})
        self.assertEqual(warns, [])

    def test_transform_bind_accepted_and_never_compared_to_static(self):
        """`transform` 是复合串，不该按"属性串相等"比——但绑定本身合法。"""
        src = _write_svg(self.tmp, "seg-a",
                         SEG_A_SVG.replace('id="hub" cx="490"',
                                           'id="hub" transform="rotate(0 490 367)" cx="490"'))
        b = _block(bind=[{"target": "#hub", "attr": "transform",
                          "expr": "0*theta + 0"}])
        errs, warns = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertEqual(errs, [])
        self.assertFalse(any("transform" in w for w in warns), warns)

    def test_conflict_with_a_property_step_is_an_error(self):
        """同一块 animate 里，属性补间与 bind 写同一个属性：先写的白写。"""
        src = _write_svg(self.tmp, "seg-a")
        b = _block()
        b["steps"] = b["steps"] + [{"at": 0, "target": "#tip", "to": {"cx": 600}}]
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertTrue(any("白写" in e for e in errs), errs)
        self.assertTrue(any("cx" in e for e in errs), errs)

    def test_conflict_through_set_is_an_error_too(self):
        src = _write_svg(self.tmp, "seg-a")
        b = _block()
        b["steps"] = b["steps"] + [{"at": 0, "target": "#tip", "set": {"cx": 600}}]
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertTrue(any("白写" in e for e in errs), errs)

    def test_different_attr_is_not_a_conflict(self):
        src = _write_svg(self.tmp, "seg-a")
        b = _block()
        b["steps"] = b["steps"] + [
            {"at": 0, "target": "#tip", "to": {"opacity": 1}},
            {"at": 0, "target": "#hub", "to": {"cx": 600}}]
        images = {"seg-a": {"src": src, "animate": b}}
        errs, _ = self._run(images)
        self.assertEqual(errs, [])

    def test_endpoint_blowup_is_an_error(self):
        """终值处除零：那一帧 onUpdate 一抛，后面的拍全部不演。"""
        src = _write_svg(self.tmp, "seg-a")
        b = _block(steps=[{"at": 0, "param": "theta", "to": -1}],
                   bind=[{"target": "#tip", "attr": "cx",
                          "expr": "490 + 260/(theta+1)"}])
        errs, _ = self._run({"seg-a": {"src": src, "animate": b}})
        self.assertTrue(any("算不出来" in e for e in errs), errs)

    def test_compare_pane_inlined(self):
        src = _write_svg(self.tmp, "seg-a")
        images = {"seg-a": {"src": [src, src],
                            "panes": [{"animate": _block()}, {}]}}
        errs, _ = self._run(images)
        self.assertEqual(errs, [])
        self.assertIn("inline_svg", images["seg-a"]["panes"][0])
        self.assertNotIn("inline_svg", images["seg-a"]["panes"][1])

    def test_compare_pane_conflict_stays_inside_its_pane(self):
        src = _write_svg(self.tmp, "seg-a")
        b = _block()
        b["steps"] = b["steps"] + [{"at": 0, "target": "#tip", "to": {"cy": 1}}]
        images = {"seg-a": {"src": [src, src], "panes": [{"animate": b}, {}]}}
        errs, _ = self._run(images)
        self.assertTrue(any("第 0 格" in e and "白写" in e for e in errs), errs)


class Renderer(unittest.TestCase):
    """渲染端：作用域、代理补间、页里不出现 eval。"""

    def _html(self, params=None, panes=None, layout=None):
        """`panes` 给了就换成一对图条目：单图那套键（含 animate）搬进 panes[i]。"""
        m = H.make_manifest()
        for seg in m["segments"]:
            if seg["id"] == "seg-a" and layout:
                seg["layout"] = layout
        block = params or _block()
        if panes is not None:
            entry = {"src": ["images/seg-a.svg", "images/seg-b.svg"], "panes": panes}
        else:
            entry = {"src": "images/seg-a.svg", "animate": block,
                     "inline_svg": '<svg viewBox="0 0 980 735"><g id="arm"/>'
                                   '<circle id="tip" cx="750" cy="367"/></svg>'}
        images = {"seg-a": entry, "seg-b": {"src": "images/seg-b.svg"}}
        return HR.generate_html(m, "audio/combined.wav", images=images,
                                aspect="portrait")

    def test_selector_scoped_to_segment(self):
        html = self._html()
        self.assertIn('"#img-seg-a #tip"', html)
        self.assertNotIn('"eval"', html)
        self.assertNotIn("eval(", html.replace("parseEase", ""))

    def test_one_proxy_tween_per_step(self):
        html = self._html()
        self.assertIn("var el=D.steps.map", html)
        self.assertIn("onUpdate:draw", html)
        m = H.make_manifest()
        seg = next(s for s in m["segments"] if s["id"] == "seg-a")
        t0 = round(seg["sentences"][0]["start_time"], 2)
        self.assertIn(f"}},{t0:.2f});", html)

    def test_no_derive_no_timeline(self):
        m = H.make_manifest()
        images = {"seg-a": {"src": "images/seg-a.svg"}, "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=images, aspect="portrait")
        self.assertNotIn("D.params", html)

    def test_compare_panes_scoped_per_pane(self):
        html = self._html(panes=[{"animate": _block()}, {}], layout="compare")
        self.assertIn('"#img-seg-a-0 #tip"', html)
        self.assertNotIn('"#img-seg-a-1 #tip"', html)

    def test_compare_pane_without_derive_emits_nothing(self):
        html = self._html(panes=[{}, {"animate": _block()}], layout="compare")
        self.assertIn('"#img-seg-a-1 #tip"', html)
        self.assertNotIn('"#img-seg-a-0 #tip"', html)

    def test_beats_accept_derive_as_motion(self):
        """台词板只问"有没有画面对应"，derive 也算——不然后台提醒是假警报。"""
        import _beats as B
        self.assertTrue(B._declares_motion({"s": {"animate": _block()}}, "s"))
        self.assertFalse(B._declares_motion({"s": {"animate": {"steps": []}}}, "s"))


if __name__ == "__main__":
    unittest.main()
