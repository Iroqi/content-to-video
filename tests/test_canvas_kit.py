"""scripts/canvas_kit.py 的测试：画布几何来自模板、初始态与 director 草稿配对、产物过 check_svg 门禁。

样例 spec 的坐标按画幅**比例**算（不是抄一份像素数），这样同一份构图能在竖屏与横屏
各出一份原生分辨率的画布页——也顺手证明了尺寸真源确实跟着 get_canvas 走。
"""
import contextlib
import io
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
import _svg_sanitize
import canvas_kit as K
import check_svg
from _images_schema import validate_images_json
from _script_utils import seeded_wobble
from _template import get_canvas, load_template

TEMPLATES = load_template()
DIRECTOR_DEFAULT = TEMPLATES["animation"]["director"]


def local(tag):
    return tag.rsplit("}", 1)[-1]


def actionable(warns):
    """滤掉 check_svg 对画布档**无条件**打的那条知情提示。

    它对任何 canvas SVG 都打（内容是"我查了什么、还剩什么查不到"），删不掉也不该删；
    本模块要判的是"有没有可执行的问题"，所以只滤这一条，其余 warn 一律算没过门禁。
    """
    return [w for w in warns if K._STANDING_ADVISORY not in w]


def spec_for(aspect):
    """一张"结构底 + 逐拍内容"的画布页：竖屏横屏都能过门禁的那份构图。"""
    w, h = get_canvas(aspect)
    return {
        "aspect": aspect,
        "elements": [
            {"kind": "panel", "id": "panel", "role": "structure", "x": w * 0.06,
             "y": h * 0.22, "w": w * 0.88, "h": h * 0.48},
            {"kind": "axis", "id": "axis-x", "role": "structure", "orient": "x",
             "at": h * 0.66, "from": w * 0.16, "to": w * 0.90, "label_side": "below",
             "ticks": [{"v": 0, "label": "0"}, {"v": 50, "label": "50"},
                       {"v": 100, "label": "100", "unit": "%"}]},
            {"kind": "axis", "id": "axis-y", "role": "structure", "orient": "y",
             "at": w * 0.16, "from": h * 0.66, "to": h * 0.26, "label_side": "left",
             "ticks": [{"v": 0, "label": "0"}, {"v": 25, "label": "25"},
                       {"v": 50, "label": "50"}, {"v": 75, "label": "75"},
                       {"v": 100, "label": "100"}]},
            {"kind": "text", "id": "ctitle", "role": "structure", "x": w * 0.06,
             "y": h * 0.10, "tier": "title", "content": "复利的斜坡"},
            {"kind": "text", "id": "kicker", "x": w * 0.06, "y": h * 0.17,
             "tier": "label", "fill": "dim", "content": "30 天能差出多少"},
            {"kind": "polyline", "id": "curve", "draw": True,
             "points": [[w * 0.16, h * 0.62], [w * 0.42, h * 0.50],
                        [w * 0.66, h * 0.34], [w * 0.90, h * 0.28]]},
            {"kind": "circle", "id": "dot", "cx": w * 0.90, "cy": h * 0.28,
             "r": w * 0.014},
            {"kind": "bars", "id": "bars", "base_y": h * 0.86, "fill": "accent",
             "bars": [{"x": w * 0.60, "w": w * 0.05, "h": h * 0.06, "label": "去年"},
                      {"x": w * 0.72, "w": w * 0.05, "h": h * 0.10, "label": "今年"}]},
            {"kind": "text", "id": "bignum", "x": w * 0.06, "y": h * 0.80, "tier": "title",
             "fill": "accent", "count": {"to": 13.5, "from": 1, "decimals": 1, "suffix": "×"}},
            {"kind": "text", "id": "motto", "x": w * 0.06, "y": h * 0.955, "tier": "body",
             "content": "每天多 1%，三十天后是十三倍半", "type": True},
            {"kind": "rule", "id": "underline", "x1": w * 0.06, "y1": h * 0.98,
             "x2": w * 0.45, "y2": h * 0.98, "draw": True},
        ],
    }


def write_json(dirpath, name, data):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p


def run_cli(args):
    """跑 main()，把 stdout（director 草稿）与 stderr（人类可读的账）分开收回来。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = K.main(args)
    return rc, out.getvalue(), err.getvalue()


class CanvasGeometry(unittest.TestCase):
    def test_root_size_is_the_template_canvas_not_a_copied_number(self):
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                svg = K.render_svg(K.validate_spec(spec_for(aspect)))
                root = ET.fromstring(svg)
                w, h = get_canvas(aspect)
                self.assertEqual(root.get("width"), str(w))
                self.assertEqual(root.get("height"), str(h))
                self.assertEqual(root.get("viewBox"), "0 0 {} {}".format(w, h))

    def test_font_tiers_come_from_template_layout(self):
        svg = K.render_svg(K.validate_spec(spec_for("portrait")))
        tiers = TEMPLATES["layout"]["vertical"]
        sizes = {el.get("id"): el.get("font-size") for el in ET.fromstring(svg).iter()
                 if local(el.tag) == "text" and el.get("id")}
        self.assertEqual(sizes["ctitle"], str(tiers["title"]["fontSize"]))
        self.assertEqual(sizes["motto"], str(tiers["subtitle"]["fontSize"]))
        self.assertEqual(sizes["kicker"], str(tiers["tagline"]["fontSize"]))

    def test_svg_survives_the_inline_sanitizer(self):
        """写了 director 的画布页交付时是净化内联的活 DOM：脚手架的产物必须能过那一步。"""
        svg = K.render_svg(K.validate_spec(spec_for("portrait")))
        markup, notes = _svg_sanitize.sanitize_svg_for_inline(svg)
        self.assertEqual(notes, [])
        self.assertIn('id="curve"', markup)

    def test_context_carries_no_note_collector(self):
        """取值上下文里不留第二套"攒 SVG 注释"的机制。

        曾有个 `nodes` 参数把 `ctx["note"]` 变成注释收集器，后来注释改由
        `_emit_axis` 以 (node, note) 返回值交出——`ctx["note"]` 再无读取点
        （调用方也不传 nodes），键留着就是一份没人消费的隐式契约。
        """
        spec = K.validate_spec(spec_for("portrait"))
        self.assertNotIn("note", K._context(spec))


class LineVisibilityNotes(unittest.TestCase):
    """_line_visibility_notes 的"脱弧"判据：照抄本段 accent（含沿弧混色）不误报，另挑一支仍报。"""

    def off_arc_notes(self, accent):
        spec = {"aspect": "portrait", "theme": "dark", "accent": accent,
                "elements": [{"kind": "text", "id": "t", "x": 10, "y": 10,
                              "tier": "title", "content": "标题"}]}
        validated = K.validate_spec(spec)
        return [n for n in K._line_visibility_notes(validated, K._context(validated))
                if "脱出全片" in n]

    def test_arc_mixed_accent_is_not_reported_off_arc(self):
        """段数多于色板档位时，arc_accent 的中间档是沿弧两支的混色——照抄它不是"另挑一支"。

        references/canvas.md 要求画布 spec 照抄本段 accent，旧代码按
        `accent not in get_accent_palette()` 判，每次生成都吃一条冤枉 hint
        （hint 训练人忽略提示，比漏报更贵）。
        """
        for mixed in ("#a6c497", "#4dcee5", "#c4bf7e", "#ffc64e", "#faaf65", "#f06087"):
            with self.subTest(accent=mixed):
                self.assertEqual(self.off_arc_notes(mixed), [])

    def test_genuinely_off_arc_accent_still_hints(self):
        """判据放松的是"弧上混色"，不是"随便挑"——真脱弧的颜色照旧出声。"""
        self.assertTrue(self.off_arc_notes("#123456"))

    def test_default_accent_written_uppercase_is_not_reported(self):
        """默认强调色按设计不在弧上（显式豁免）；写成大写 hex 与色板小写是同一支。"""
        validated = K.validate_spec(spec_for("portrait"))
        raw = dict(validated, accent="#2DD4BF")
        notes = [n for n in K._line_visibility_notes(raw, K._context(validated))
                 if "脱出全片" in n]
        self.assertEqual(notes, [])


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write_svg(self, spec, theme="dark"):
        p = os.path.join(self.tmp.name, "seg1.svg")
        # theme 必须走 validate_spec：画布页的取色就是照主题派生的，
        # 用默认主题渲染再按别的主题判对比度，量的是一页根本不存在的颜色。
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(K.render_svg(K.validate_spec(spec, theme=theme)))
        return p

    def test_generated_page_passes_canvas_gate_for_both_aspects(self):
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                p = self._write_svg(spec_for(aspect))
                errors, warns = check_svg.check_file(p, "canvas", aspect, "dark")
                self.assertEqual(errors, [])
                self.assertEqual(actionable(warns), [])

    def test_no_full_bleed_rect_is_ever_emitted(self):
        for aspect in ("portrait", "landscape"):
            w, h = get_canvas(aspect)
            root = ET.fromstring(K.render_svg(K.validate_spec(spec_for(aspect))))
            for el in root.iter():
                if local(el.tag) == "rect":
                    self.assertFalse(float(el.get("width")) >= w * 0.98
                                     and float(el.get("height")) >= h * 0.98,
                                     "画布页铺了满幅底板，模板那三层背景会被盖掉")

    def test_full_bleed_panel_is_refused_before_it_is_emitted(self):
        w, h = get_canvas("portrait")
        spec = {"elements": [{"kind": "panel", "x": 0, "y": 0, "w": w, "h": h}]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("铺满了整页", str(cm.exception))

    def test_invisible_full_bleed_panel_is_allowed(self):
        """铺满整页但**不盖任何东西**的 panel 不该被拒：`fill:"none"` / `opacity:0`
        挡不住模板那三层背景，"铺满"这条警告的病因（盖死背景）根本不成立。

        判据此前只比几何，于是脚手架拒了一份 check_svg 本来会放行的合法 spec
        ——同一份几何两道门两个答案，而作者看不出自己错在"少写了个 fill"。
        """
        w, h = get_canvas("portrait")
        for extra in ({"fill": "none"}, {"opacity": 0}):
            with self.subTest(extra=extra):
                spec = {"elements": [dict({"kind": "panel", "x": 0, "y": 0,
                                           "w": w, "h": h}, **extra)]}
                K.render_svg(K.validate_spec(spec))   # 不抛即可
        # 真会盖住背景的照旧拦下
        spec = {"elements": [{"kind": "panel", "x": 0, "y": 0, "w": w, "h": h,
                              "opacity": 0.5}]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("铺满了整页", str(cm.exception))

    def test_sparse_page_fails_the_gate_with_the_empty_band(self):
        """只有上 1/4 有东西的页就是"暂停是空页"的形状：脚手架要拦，不能默默交出去。"""
        spec = {"elements": [
            {"kind": "text", "id": "t", "x": 90, "y": 200, "tier": "title",
             "role": "structure", "content": "只有一行标题"},
        ]}
        p = os.path.join(self.tmp.name, "sparse.svg")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(K.render_svg(K.validate_spec(spec)))
        errors, warns = K.lint_file(p, "portrait", "dark")
        self.assertEqual(errors, [])
        self.assertTrue(any("空带" in w for w in warns), warns)


class RolesAndInitialState(unittest.TestCase):
    def nodes(self, aspect="portrait"):
        root = ET.fromstring(K.render_svg(K.validate_spec(spec_for(aspect))))
        return {el.get("id"): el for el in root.iter() if el.get("id")}

    def test_structure_is_visible_in_frame_zero(self):
        # 结构底不进 steps，也就不该被写成需要点亮的样子：切页那一瞬间它必须在
        self.assertIsNone(self.nodes()["panel"].get("opacity"))
        self.assertIsNone(self.nodes()["ctitle"].get("opacity"))
        self.assertIsNone(self.nodes()["axis-x"].get("opacity"))

    def test_content_is_written_hidden_and_grouped_nodes_inherit(self):
        nodes = self.nodes()
        self.assertEqual(nodes["kicker"].get("opacity"), "0")
        self.assertEqual(nodes["bars"].get("opacity"), "0")
        self.assertEqual(nodes["dot"].get("opacity"), "0")

    def test_draw_count_type_are_not_hidden_because_nothing_would_lift_them(self):
        # 这三种演的起始态由渲染端负责（dash 收起 / 落 from 值 / 清空文本）；
        # 再套一层 opacity=0 就永远不会亮。
        nodes = self.nodes()
        self.assertIsNone(nodes["curve"].get("opacity"))
        self.assertIsNone(nodes["underline"].get("opacity"))
        self.assertIsNone(nodes["bignum"].get("opacity"))
        self.assertIsNone(nodes["motto"].get("opacity"))

    def test_count_node_initial_text_is_the_from_rendering(self):
        self.assertEqual(self.nodes()["bignum"].text, "1.0×")

    def test_axis_tick_labels_carry_a_class_for_stagger(self):
        root = ET.fromstring(K.render_svg(K.validate_spec(spec_for("portrait"))))
        ticks = [el for el in root.iter() if el.get("class") == "tick"]
        self.assertEqual(len(ticks), 8)          # 两个轴各自的刻度
        self.assertEqual([t.text for t in ticks if t.text].count("100%"), 1)

    def test_tick_marks_point_at_their_labels(self):
        """刻度线要往标签那一侧伸，四个组合都得自查一遍。

        证伪：原先 x 轴恒画 `at→at+tick_len`（朝下）、y 轴恒画 `at→at-tick_len`（朝左），
        只有 `label_side` 落在默认那一侧才对。写 `above` / `right` 时标签悬在轴的
        一侧、刻度朝另一侧伸，两者之间隔着一根不该在那儿的短线。
        """
        def tick_end(orient, side):
            el = {"kind": "axis", "id": "a", "role": "structure", "orient": orient,
                  "at": 900, "from": 100, "to": 1000, "label_side": side,
                  "ticks": [{"v": 0, "label": "0"}, {"v": 50, "label": "50"}]}
            root = ET.fromstring(K.render_svg(K.validate_spec({"aspect": "portrait",
                                                               "elements": [el]})))
            lines = [n for n in root.iter() if local(n.tag) == "line"]
            text = next(n for n in root.iter() if local(n.tag) == "text")
            mark = lines[1]          # lines[0] 是轴线本身
            if orient == "x":
                return float(mark.get("y2")), float(text.get("y"))
            return float(mark.get("x2")), float(text.get("x"))

        for orient, side in (("x", "below"), ("x", "above"),
                             ("y", "left"), ("y", "right")):
            with self.subTest(orient=orient, side=side):
                end, label = tick_end(orient, side)
                # 标签与刻度末端必须落在轴的同一侧（轴本身在 900）
                self.assertEqual(end < 900, label < 900,
                                 f"刻度末端 {end} 与标签 {label} 分处轴的两侧")

    def test_draw_ready_geometry_carries_pathlength(self):
        nodes = self.nodes()
        self.assertEqual(nodes["curve"].get("pathLength"), "1")
        self.assertEqual(nodes["underline"].get("pathLength"), "1")


class Roughness(unittest.TestCase):
    """手绘旋钮：roughness 只抖图形坐标；同 spec 重跑逐字节一致（确定性契约）。

    三条不变量各由测试钉住：
      - roughness=0 与不写这个键**逐字节一致**（改造前的行为原样保留）；
      - 抖动量级 ≤ roughness × 该画幅线宽（幅度真源是模板线宽，不是私设 px）；
      - 坐标轴与文字不收这个键（标尺与文字在两种渲染里逐字节一致）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    # 吃 roughness 的只有图形五兄弟；axis 是数据标尺、text 抖了不成字，两者都拒收
    ROUGH_KINDS = ("panel", "polyline", "bars", "circle", "rule")

    def _spec(self, rough=None):
        """七种 kind 各来一个；rough=None 表示整个 spec 不写这个键。"""
        els = [
            {"kind": "panel", "id": "panel", "x": 80, "y": 300, "w": 920, "h": 700},
            {"kind": "axis", "id": "ax", "orient": "y", "at": 940, "from": 400,
             "to": 940, "ticks": [{"v": 400, "label": "8"}, {"v": 940, "label": "0"}]},
            {"kind": "polyline", "id": "curve", "draw": True,
             "points": [[160, 900], [480, 600], [800, 750]]},
            {"kind": "bars", "id": "bars", "base_y": 940,
             "bars": [{"x": 160, "w": 120, "h": 210, "label": "Q1"},
                      {"x": 320, "w": 120, "h": 350, "label": "Q2"}]},
            {"kind": "circle", "id": "c", "cx": 540, "cy": 700, "r": 120},
            {"kind": "rule", "id": "r", "x1": 80, "y1": 1000, "x2": 1000, "y2": 1000},
            {"kind": "text", "id": "t", "x": 80, "y": 200, "content": "季度营收"},
        ]
        if rough is not None:
            for el in els:
                if el["kind"] in self.ROUGH_KINDS:
                    el["roughness"] = rough
        return {"aspect": "portrait", "elements": els}

    def _render(self, rough=None):
        return K.render_svg(K.validate_spec(self._spec(rough)))

    def _geometry(self, svg):
        """产出 SVG 里所有可比数值：{文档序路径: {属性: 值}}，points 拆成逐点。

        两份渲染的元素结构逐一对应（roughness 不动 tag、不动顺序），所以能按路径
        逐个属性比抖动幅度。
        """
        out = {}
        for idx, el in enumerate(ET.fromstring(svg).iter()):
            vals = {}
            for k, v in el.attrib.items():
                try:
                    vals[k] = float(v)
                except ValueError:
                    if k == "points":
                        vals[k] = [float(n) for n in re.split(r"[,\s]+", v)]
            out["{}[{}]".format(local(el.tag), idx)] = vals
        return out

    def test_zero_roughness_is_byte_identical_to_absent(self):
        """0 = 不抖 = 加这个键之前的行为：向后兼容不是"差不多"，是逐字节。"""
        self.assertEqual(self._render(0), self._render(None))

    def test_same_spec_renders_identically(self):
        """确定性：同一份 spec 重跑逐字节一致（渲染期契约禁未播种随机）。"""
        self.assertEqual(self._render(2), self._render(2))

    def test_wobble_stays_within_roughness_times_line_width(self):
        """幅度真源是画幅线宽：每个数值属性的位移都 ≤ roughness × line_w。"""
        amp = 2 * K.LINE_W["portrait"]
        rough, plain = self._geometry(self._render(2)), self._geometry(self._render(None))
        self.assertEqual(list(rough), list(plain))
        for path, vals in rough.items():
            # data-free-ends 是端点豁免标记、不是几何：只有被抖的折线会多出它
            self.assertEqual(set(vals) - set(plain[path]) - {"data-free-ends"},
                             set(), path)
            for key, want in vals.items():
                got = plain[path].get(key)
                if got is None:
                    continue
                if isinstance(want, list):
                    self.assertEqual(len(got), len(want), path)
                    for a, b in zip(got, want):
                        self.assertLessEqual(abs(a - b), amp + 1e-3, f"{path} 的 {key}")
                else:
                    self.assertLessEqual(abs(got - want), amp + 1e-3, f"{path} 的 {key}")

    def test_scale_and_text_never_wobble(self):
        """标尺与文字不收这个键：axis 整棵子树、所有 text（刻度与柱标签也在内）
        在两种渲染里逐一相同。"""
        rroot, proot = ET.fromstring(self._render(2)), ET.fromstring(self._render(None))

        def axis_of(root):
            return next(n for n in root.iter() if n.get("id") == "ax")
        self.assertEqual(ET.tostring(axis_of(rroot)), ET.tostring(axis_of(proot)))
        rtexts = [n for n in rroot.iter() if local(n.tag) == "text"]
        ptexts = [n for n in proot.iter() if local(n.tag) == "text"]
        self.assertEqual(len(rtexts), len(ptexts))
        for ra, pa in zip(rtexts, ptexts):
            self.assertEqual(ra.attrib, pa.attrib, ra.text)

    def test_bars_keep_their_feet_on_the_baseline(self):
        """柱底始终钉在 base_y：站着是数据语义，抖的只有 x / 宽 / 高。"""
        base = 940
        for el in ET.fromstring(self._render(2)).iter():
            if el.get("class") != "bar":
                continue
            self.assertAlmostEqual(float(el.get("y")) + float(el.get("height")), base,
                                   delta=1e-9)

    def test_different_ids_get_different_wobble(self):
        """种子绑元素身份：同几何不同 id，抖出来的样子不一样（同页不重样）。"""
        els = [{"kind": "circle", "id": "a", "cx": 500, "cy": 700, "r": 60},
               {"kind": "circle", "id": "b", "cx": 500, "cy": 700, "r": 60}]
        for el in els:
            el["roughness"] = 2
        root = ET.fromstring(K.render_svg(K.validate_spec({"elements": els})))
        circles = [n for n in root.iter() if local(n.tag) == "circle"]
        self.assertNotEqual(circles[0].get("cx"), circles[1].get("cx"))

    def test_id_less_structure_elements_wobble_independently(self):
        """无 id 的结构底按 kind#序号做种子：同页两个 panel 的抖动不重样。"""
        els = [{"kind": "panel", "x": 80, "y": 300, "w": 400, "h": 300},
               {"kind": "panel", "x": 80, "y": 700, "w": 400, "h": 300}]
        for el in els:
            el["roughness"] = 2
        root = ET.fromstring(K.render_svg(K.validate_spec({"elements": els})))
        rects = [n for n in root.iter() if local(n.tag) == "rect"]
        self.assertNotEqual(rects[0].get("x"), rects[1].get("x"))

    def test_sizes_stay_positive_under_max_roughness(self):
        """极小尺寸也抖不成非法的 rect：宽度抖到 0/负 是一张坏图。"""
        els = [{"kind": "bars", "id": "tiny", "base_y": 940, "roughness": 10,
                "bars": [{"x": 160, "w": 6, "h": 6}]}]
        root = ET.fromstring(K.render_svg(K.validate_spec({"elements": els})))
        rect = next(n for n in root.iter() if n.get("class") == "bar")
        self.assertGreater(float(rect.get("width")), 0)
        self.assertGreater(float(rect.get("height")), 0)

    def test_roughness_over_max_is_refused(self):
        for bad in (11, -1):
            with self.subTest(roughness=bad):
                with self.assertRaises(ValueError) as cm:
                    K.validate_spec(self._spec(bad))
                self.assertIn("roughness", str(cm.exception))

    def test_render_layer_refuses_over_max_too(self):
        """render_svg 也被测试与其它入口直接调用：校验层之外它自己那道也拦。"""
        spec = K.validate_spec(self._spec(None))
        spec["elements"][0]["roughness"] = 11
        with self.assertRaises(ValueError) as cm:
            K.render_svg(spec)
        self.assertIn("上限", str(cm.exception))

    def test_text_refuses_roughness(self):
        """字抖一下就不成字：text 写这个键当场拒，不静默忽略。"""
        spec = self._spec(None)
        spec["elements"][-1]["roughness"] = 2
        with self.assertRaises(ValueError) as cm:
            K.validate_spec(spec)
        self.assertIn("不吃 roughness", str(cm.exception))

    def test_axis_refuses_roughness(self):
        """轴线是数据标尺：刻度与标签全钉在它上面，整根不抖，写这个键当场拒。"""
        spec = self._spec(None)
        spec["elements"][1]["roughness"] = 2
        with self.assertRaises(ValueError) as cm:
            K.validate_spec(spec)
        self.assertIn("不吃 roughness", str(cm.exception))

    def test_roughness_never_reaches_the_director_draft(self):
        """草稿按 #id 寻址、与坐标无关：加不加 roughness，steps 一模一样。"""
        draft = K.selfcheck_fragment(K.validate_spec(self._spec(2)))
        self.assertEqual(draft, K.selfcheck_fragment(K.validate_spec(self._spec(None))))
        for step in draft["steps"]:
            self.assertNotIn("roughness", step)

    def test_full_bleed_is_judged_on_the_jittered_size(self):
        """spec 里差一口气的 panel，抖到阈值外也要拒——门禁量的是落盘的属性。"""
        w, h = get_canvas("portrait")
        # 种子不碰运气：seeded_wobble 是确定函数，直接找一颗 x/y 都贴在边上（≤1px）、
        # 而 w/h 都往大推（>0.9）的——四个条件同时满足，这一颗就一定触发。
        seed = next(s for s in map(str, range(20000))
                    if abs(seeded_wobble(s, "x")) < 0.125 and abs(seeded_wobble(s, "y")) < 0.125
                    and seeded_wobble(s, "w") > 0.9 and seeded_wobble(s, "h") > 0.9)
        ctx = dict(K._context(K.validate_spec({"elements": [
            {"kind": "panel", "x": 0, "y": 0, "w": 100, "h": 100}]})),
            seed=seed, amp=8.0)
        edge = {"kind": "panel", "x": 0, "y": 0,
                "w": w * _svg_sanitize.FULL_BLEED_FRAC - 3,
                "h": h * _svg_sanitize.FULL_BLEED_FRAC - 3}
        with self.assertRaises(ValueError) as cm:
            K._emit_panel(edge, ctx, "spec 的 elements[1]")
        self.assertIn("铺满了整页", str(cm.exception))
        # 阈值内充裕的 panel 抖满也不误伤（判据没有敏感到把正常面板拦下）
        node = K._emit_panel(dict(edge, w=w * 0.8, h=h * 0.8), ctx, "spec 的 elements[1]")
        self.assertEqual(node["tag"], "rect")

    def test_rough_page_still_passes_the_canvas_gate(self):
        """手绘页也是画布页：抖完之后照样过 check_svg，门禁不因风格豁免。"""
        spec = spec_for("portrait")
        for rough in (2, 4):
            with self.subTest(roughness=rough):
                for el in spec["elements"]:
                    if el["kind"] in self.ROUGH_KINDS:
                        el["roughness"] = rough
                p = os.path.join(self.tmp.name, "rough%d.svg" % rough)
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(K.render_svg(K.validate_spec(spec)))
                errors, warns = check_svg.check_file(p, "canvas", "portrait", "dark")
                self.assertEqual(errors, [])
                self.assertEqual(actionable(warns), [])


class DirectorDraft(unittest.TestCase):
    def fragment(self, spec, sentences=None):
        return K.selfcheck_fragment(K.validate_spec(spec), sentences=sentences)

    def test_auto_beats_never_land_before_a_pinned_beat(self):
        """给了句数时，钉拍之后的自动拍不能回绕到它前面。

        回绕（`cursor % sentences`）的初衷是"元素多于句子时不越界"，但钉拍已经把
        游标推到 `beat+1`，再取模会被拉回句首——作者钉的那一拍反倒成了本段的倒数
        几拍（实测 3 句稿 beat=2 之后算出 at=0.5/1.5）。钉过拍就顶到末句为止：
        同样不越界，且不推翻作者排的顺序。
        """
        spec = {"elements": [
            {"kind": "circle", "id": "a", "cx": 100, "cy": 100, "r": 10, "beat": 2},
            {"kind": "circle", "id": "b", "cx": 200, "cy": 100, "r": 10},
            {"kind": "circle", "id": "c", "cx": 300, "cy": 100, "r": 10},
        ]}
        self.assertEqual([s["at"] for s in self.fragment(spec, sentences=3)["steps"]],
                         [2, 2, 2])
        # 没钉过拍时回绕照旧：元素多于句子也不会把 at 顶到句序之外
        many = {"elements": [
            {"kind": "circle", "id": "c%d" % i, "cx": 100 + 40 * i, "cy": 100, "r": 10}
            for i in range(5)]}
        self.assertEqual([s["at"] for s in self.fragment(many, sentences=3)["steps"]],
                         [0, 1, 2, 0, 1])

    def test_draft_is_a_valid_images_json_director_block(self):
        frag = self.fragment(spec_for("portrait"))
        # 真契约：套成 images.json 的一条条目过 validate_images_json（不猜它的形状）
        validate_images_json({"seg7": {"src": "images/seg7.svg", "director": frag}})
        targets = [s["target"] for s in frag["steps"]]
        self.assertEqual(targets, ["#kicker", "#curve", "#dot", "#bars", "#bignum",
                                   "#motto", "#underline"])

    def test_structure_elements_get_no_step(self):
        frag = self.fragment(spec_for("portrait"))
        self.assertFalse([s for s in frag["steps"] if s["target"] in
                          ("#panel", "#axis-x", "#axis-y", "#ctitle")])

    def test_anchors_are_sentence_positions_never_absolute_seconds(self):
        frag = self.fragment(spec_for("portrait"))
        self.assertEqual([s["at"] for s in frag["steps"]], [0, 1, 2, 3, 4, 5, 6])
        self.assertFalse([s for s in frag["steps"] if "at_time" in s])

    def test_primitive_steps_take_their_own_shape(self):
        by = {s["target"]: s for s in self.fragment(spec_for("portrait"))["steps"]}
        self.assertEqual(by["#curve"], {"at": 1, "target": "#curve", "draw": True})
        self.assertEqual(by["#bignum"]["count"], {"to": 13.5, "from": 1, "decimals": 1,
                                                  "suffix": "×"})
        self.assertEqual(by["#motto"]["type"], {})
        self.assertEqual(by["#kicker"]["from"], {"opacity": 0})
        self.assertEqual(by["#kicker"]["to"], {"opacity": 1})

    def test_content_panel_with_explicit_opacity_stays_legal_xml(self):
        """panel 显式 opacity + 第 0 帧藏身：两处 opacity 合成一处，不许重复属性。

        证伪：起始态原先**追加** `opacity="0"`，作者写了 0.5 时产物是
        `opacity="0.5" opacity="0"`——重复属性，整张 SVG 不是合法 XML
        （ET.ParseError: duplicate attribute），内联 / check_svg / 净化全路读不了。
        帧 0 归零是导演约定（_starts_hidden），落定到几成透明是作者意图
        （ELEMENT_KEYS 里就 panel 吃这个键）：SVG 起始属性归零、草稿那拍 to 带
        作者那档，谁也不盖谁。
        """
        spec = {"elements": [
            {"kind": "panel", "id": "pp", "role": "content", "x": 80, "y": 300,
             "w": 400, "h": 300, "opacity": 0.5},
        ]}
        # 修复前这里直接 ParseError：重复属性使整张 SVG 不成 XML
        root = ET.fromstring(K.render_svg(K.validate_spec(spec)))
        node = next(n for n in root.iter() if n.get("id") == "pp")
        self.assertEqual(node.get("opacity"), "0")
        frag = self.fragment(spec)
        by = {s["target"]: s for s in frag["steps"]}
        self.assertEqual(by["#pp"]["from"], {"opacity": 0})
        self.assertEqual(by["#pp"]["to"], {"opacity": 0.5})
        # 真契约：带显式 opacity 的草稿必须能直接过 images.json 的 director 校验
        validate_images_json({"seg7": {"src": "images/seg7.svg", "director": frag}})

    def test_content_panel_with_zero_opacity_is_refused(self):
        """内容元素 opacity:0 = 那一拍补间 0→0：永远不亮，且成片看着像"演过了"。"""
        spec = {"elements": [
            {"kind": "panel", "id": "pp", "role": "content", "x": 80, "y": 300,
             "w": 400, "h": 300, "opacity": 0},
        ]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("永远不亮", str(cm.exception))

    def test_default_duration_and_ease_are_left_out_of_the_draft(self):
        spec = spec_for("portrait")
        spec["elements"] = [e for e in spec["elements"] if e.get("kind") == "circle"]
        spec["elements"].append({"kind": "text", "id": "late", "x": 100, "y": 100,
                                 "content": "同一页的另一拍", "beat": 3,
                                 "duration": DIRECTOR_DEFAULT["duration"],
                                 "ease": DIRECTOR_DEFAULT["ease"]})
        spec["elements"].append({"kind": "text", "id": "slow", "x": 100, "y": 200,
                                 "content": "慢半拍", "beat": 4, "duration": 1.8,
                                 "ease": "power3.inOut", "stagger": 0.12})
        steps = self.fragment(spec)["steps"]
        self.assertNotIn("duration", steps[1])
        self.assertNotIn("ease", steps[1])
        self.assertEqual(steps[2]["duration"], 1.8)
        self.assertEqual(steps[2]["ease"], "power3.inOut")
        self.assertEqual(steps[2]["stagger"], 0.12)

    def test_explicit_beat_pins_elements_together_and_moves_the_cursor(self):
        spec = {"elements": [
            {"kind": "circle", "id": "a", "cx": 100, "cy": 100, "r": 10},
            {"kind": "circle", "id": "b", "cx": 200, "cy": 100, "r": 10, "beat": 0},
            {"kind": "circle", "id": "c", "cx": 300, "cy": 100, "r": 10},
            {"kind": "circle", "id": "d", "cx": 400, "cy": 900, "r": 10, "beat": 9},
            {"kind": "circle", "id": "e", "cx": 500, "cy": 900, "r": 10},
        ]}
        self.assertEqual([s["at"] for s in self.fragment(spec)["steps"]],
                         [0, 0, 1, 9, 10])

    def test_draft_is_empty_when_page_has_no_content(self):
        spec = {"elements": [{"kind": "panel", "x": 60, "y": 60, "w": 400, "h": 400}]}
        self.assertEqual(self.fragment(spec), {"steps": []})

    def test_repeat_and_yoyo_are_carried_into_the_draft(self):
        """新特性要有脚手架出口：呼吸/脉动在 spec 里写一次，草稿原样带出。

        脚手架自己也不收 `repeat:-1`：它和 images.json 走的是同一份判据
        （`_validate_repeat_yoyo`），无限循环会把 GSAP 总时长折算成 1e10、按时刻取帧
        这件事本身失效。这里钉住的是"同一条判据在脚手架侧也生效"——两侧口径分家的话，
        作者在 canvas_kit 里试好的循环写进 images.json 才被拒，中间那一步白做。
        """
        spec = {"elements": [
            {"kind": "circle", "id": "a", "cx": 100, "cy": 100, "r": 10,
             "repeat": 3, "yoyo": True},
            {"kind": "circle", "id": "b", "cx": 200, "cy": 100, "r": 10,
             "repeat": 5},
            {"kind": "circle", "id": "c", "cx": 300, "cy": 100, "r": 10,
             "repeat": 0},
        ]}
        steps = self.fragment(spec)["steps"]
        self.assertEqual(steps[0]["repeat"], 3)
        self.assertEqual(steps[0]["yoyo"], True)
        self.assertEqual(steps[1]["repeat"], 5)
        self.assertNotIn("yoyo", steps[1])
        # repeat:0 与缺省等价，不塞进去（与渲染端 _cycle_vars 同口径）
        self.assertNotIn("repeat", steps[2])
        # 真契约：带 repeat/yoyo 的草稿必须能直接过 images.json 的 director 校验
        validate_images_json({"seg7": {"src": "images/seg7.svg",
                                       "director": {"steps": steps}}})
        # 同一份判据在脚手架侧也拒无限循环
        with self.assertRaises(ValueError) as cm:
            self.fragment({"elements": [{"kind": "circle", "id": "d", "cx": 400,
                                         "cy": 100, "r": 10, "repeat": -1}]})
        self.assertIn("repeat:-1", str(cm.exception))

    def test_repeat_on_structure_element_is_refused(self):
        """结构底不进 steps，写了等于没写；又没有"哪一拍"可反复，当场拒。"""
        spec = {"elements": [{"kind": "panel", "x": 60, "y": 60, "w": 400, "h": 400,
                              "role": "structure", "repeat": 2}]}
        with self.assertRaises(ValueError) as cm:
            K.validate_spec(spec)
        self.assertIn("不是内容元素", str(cm.exception))

    def test_yoyo_without_repeat_is_refused(self):
        spec = {"elements": [{"kind": "circle", "id": "a", "cx": 100, "cy": 100,
                              "r": 10, "yoyo": True}]}
        with self.assertRaises(ValueError) as cm:
            K.validate_spec(spec)
        self.assertIn("yoyo", str(cm.exception))

    def test_repeat_must_be_an_integer(self):
        spec = {"elements": [{"kind": "circle", "id": "a", "cx": 100, "cy": 100,
                              "r": 10, "repeat": 1.5}]}
        with self.assertRaises(ValueError) as cm:
            K.validate_spec(spec)
        self.assertIn("≥-1 的整数", str(cm.exception))


class SpecContract(unittest.TestCase):
    def test_unknown_top_level_key_is_named(self):
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"aspect": "portrait", "elementz": []})
        self.assertIn("'elementz'", str(cm.exception))

    def test_unknown_element_key_is_named(self):
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [{"kind": "circle", "id": "c", "cx": 1, "cy": 1,
                                           "r": 1, "radious": 2}]})
        self.assertIn("'radious'", str(cm.exception))

    def test_unknown_kind_lists_the_known_ones(self):
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [{"kind": "donut"}]})
        msg = str(cm.exception)
        self.assertIn("donut", msg)
        for kind in K.ELEMENT_KEYS:
            self.assertIn(kind, msg)

    def test_content_element_without_id_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec({"elements": [
                {"kind": "circle", "cx": 100, "cy": 100, "r": 10}]}))
        self.assertIn("id", str(cm.exception))

    def test_duplicate_and_selector_hostile_ids_are_refused(self):
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [
                {"kind": "circle", "id": "a", "cx": 1, "cy": 1, "r": 1},
                {"kind": "circle", "id": "a", "cx": 2, "cy": 2, "r": 1}]})
        self.assertIn("重复", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [{"kind": "circle", "id": "a b", "cx": 1,
                                           "cy": 1, "r": 1}]})
        self.assertIn("选择器", str(cm.exception))

    def test_count_and_content_are_mutually_exclusive(self):
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [{"kind": "text", "id": "n", "x": 1, "y": 1,
                                           "content": "13×", "count": {"to": 13}}]})
        self.assertIn("count", str(cm.exception))

    def test_bad_orient_and_label_side_pairing_is_refused(self):
        base = {"kind": "axis", "id": "a", "orient": "x", "at": 100, "from": 0,
                "to": 100, "ticks": [{"v": 0, "label": "0"}, {"v": 1, "label": "1"}]}
        with self.assertRaises(ValueError) as cm:
            K.validate_spec({"elements": [dict(base, label_side="left")]})
        self.assertIn("label_side", str(cm.exception))

    def test_degenerate_tick_scale_is_refused(self):
        spec = {"elements": [{"kind": "axis", "id": "a", "orient": "x", "at": 100,
                              "from": 0, "to": 100,
                              "ticks": [{"v": 5, "label": "5"}]}]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("标尺", str(cm.exception))

    def test_unknown_theme_and_aspect_are_refused(self):
        with self.assertRaises(ValueError):
            K.validate_spec({"elements": [{"kind": "circle", "id": "c", "cx": 1,
                                           "cy": 1, "r": 1}]}, theme="solar")
        with self.assertRaises(ValueError):
            K.validate_spec({"elements": [{"kind": "circle", "id": "c", "cx": 1,
                                           "cy": 1, "r": 1}]}, aspect="square")

    def test_colors_are_derived_not_typed(self):
        """脚手架的取色全部从主题/accent 派生：源码里出现字面 hex 就说明有人抄了一份
        会漂的色（本仓历史上真漂过一次页底色，此后颜色基准一律从 _theme 注册表派生）。
        只认真正成形的十六进制色，报错文案里的"#rgb / #rrggbb"这类占位写法不算。
        """
        with open(os.path.join(H.SCRIPTS_DIR, "canvas_kit.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertListEqual(re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", src), [])


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = write_json(self.tmp.name, "spec.json", spec_for("portrait"))
        self.out = os.path.join(self.tmp.name, "seg1.svg")
        self.steps = os.path.join(self.tmp.name, "steps.json")

    def test_writes_svg_and_steps_and_prints_the_draft(self):
        rc, out, err = run_cli(["--spec", self.spec, "--aspect", "portrait",
                                "-o", self.out,
                                "--emit-steps", self.steps])
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(self.out))
        with open(self.steps, encoding="utf-8") as f:
            frag = json.load(f)
        self.assertEqual(frag, json.loads(out))
        # 草稿落盘了就得能直接进 images.json——这是它唯一的用途
        validate_images_json({"seg1": {"src": "images/seg1.svg", "director": frag}})
        self.assertIn("4 个结构底", err)
        self.assertIn("7 拍内容", err)
        self.assertIn("已写", err)

    def test_cli_aspect_beats_the_spec(self):
        rc, _out, err = run_cli(["--spec", self.spec, "--aspect", "landscape",
                                 "-o", self.out])
        # 横屏的页高只有 1080：竖屏坐标那份构图照搬过来必然在底部留空带，退出码该是 1
        with open(self.out, encoding="utf-8") as f:
            self.assertIn("画布 1920×1080", f.read())
        self.assertEqual(rc, 1, err)

    def test_no_check_flag_skips_the_gate(self):
        rc, _out, err = run_cli(["--spec", self.spec, "-o", self.out,
                                 "--aspect", "landscape", "--no-check"])
        self.assertEqual(rc, 0, err)

    def test_writing_into_skill_dir_is_refused(self):
        """配图也是产物：从技能目录照抄命令不该把 SVG / 草稿写进去。

        本模块是第四个写盘出口（另三个在 pipeline / gen_hyperframes / run），守卫
        必须一起补上——否则 `cd` 到技能目录跑 `-o images/seg1.svg` 就把图落进了
        技能目录，和音频/HTML 一样污染仓库、多次制作串台。
        """
        from _script_utils import SKILL_DIR
        cwd = os.getcwd()
        os.chdir(SKILL_DIR)
        self.addCleanup(os.chdir, cwd)
        rel = os.path.join("images", "guard-probe.svg")
        with self.assertRaises(SystemExit) as cm:
            run_cli(["--spec", self.spec, "-o", rel, "--no-check"])
        self.assertIn("技能目录", str(cm.exception))
        self.assertIn("输出 SVG", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(SKILL_DIR, rel)))

    def test_emit_steps_into_skill_dir_is_named_too(self):
        """第二个出口同样要点名：只拦 -o 的话，草稿照样写进技能目录。"""
        from _script_utils import SKILL_DIR
        steps = os.path.join(SKILL_DIR, "guard-steps.json")
        with self.assertRaises(SystemExit) as cm:
            run_cli(["--spec", self.spec, "-o", self.out, "--no-check",
                     "--emit-steps", steps])
        self.assertIn("director 草稿", str(cm.exception))
        self.assertFalse(os.path.exists(steps))

    def test_bad_spec_is_an_error_not_a_traceback(self):
        bad = write_json(self.tmp.name, "bad.json", {"elements": [{"kind": "nope"}]})
        rc, _out, err = run_cli(["--spec", bad, "-o", self.out])
        self.assertEqual(rc, 2)
        self.assertIn("[error]", err)

    def test_auto_beats_wrap_so_the_draft_is_always_usable(self):
        """内容元素多于旁白句子时，自动拍按句数回绕，草稿仍能直接生成。

        这是实测翻过的车：3 句段配 5 个内容元素，自动拍一路排到 at=4，而 at 的整数部分
        是句序——生成期按 error 把整条管线拒掉（"at=4 越界：该段只有 3 句旁白"），
        脚手架自己印出来的草稿却过不了自己下游的契约。
        """
        spec = write_json(self.tmp.name, "wrap.json", {
            "elements": [{"kind": "text", "id": "t", "x": 100, "y": 120,
                          "tier": "title", "content": "标题", "role": "structure"},
                         {"kind": "circle", "id": "a", "cx": 200, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "b", "cx": 400, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "c", "cx": 600, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "d", "cx": 800, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "e", "cx": 900, "cy": 500, "r": 40}]})
        out = os.path.join(self.tmp.name, "wrap.svg")
        steps = os.path.join(self.tmp.name, "wrap_steps.json")
        rc, _o, _err = run_cli(["--spec", spec, "-o", out, "--emit-steps", steps,
                                "--sentences", "3", "--no-check"])
        self.assertEqual(rc, 0)
        with open(steps, encoding="utf-8") as f:
            frag = json.load(f)
        ats = [s["at"] for s in frag["steps"]]
        # 5 个内容元素、3 句旁白：游标回绕，且每一拍都落在句序范围内
        #（at 的整数部分就是句序，越界的那一拍会被生成期整条拒掉）。
        self.assertEqual(ats, [0, 1, 2, 0, 1])
        self.assertTrue(all(a < 3 for a in ats), ats)

    def test_pinned_beat_beyond_the_narration_is_warned(self):
        """显式钉拍越界要照实告警——那是作者意图，与自动拍的回绕不是一回事。"""
        spec = write_json(self.tmp.name, "pinned.json", {
            "elements": [{"kind": "text", "id": "t", "x": 100, "y": 120,
                          "tier": "title", "content": "标题", "role": "structure"},
                         {"kind": "circle", "id": "c", "cx": 200, "cy": 600, "r": 40,
                          "beat": 7}]})
        out = os.path.join(self.tmp.name, "pinned.svg")
        rc, _o, err = run_cli(["--spec", spec, "-o", out, "--sentences", "2",
                               "--no-check"])
        self.assertIn("越界", err)
        self.assertIn("已写", err)
        self.assertIn("[ok]", err)
        self.assertEqual(rc, 0)

    def test_without_sentences_auto_beats_are_not_wrapped(self):
        """不给 --sentences 时脚本不替作者猜段里有几句，保持原先的顺序排拍。"""
        spec = write_json(self.tmp.name, "nosent.json", {
            "elements": [{"kind": "text", "id": "t", "x": 100, "y": 120,
                          "tier": "title", "content": "标题", "role": "structure"},
                         {"kind": "circle", "id": "a", "cx": 200, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "b", "cx": 400, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "c", "cx": 600, "cy": 500, "r": 40}]})
        out = os.path.join(self.tmp.name, "nosent.svg")
        steps = os.path.join(self.tmp.name, "nosent_steps.json")
        rc, _o, _err = run_cli(["--spec", spec, "-o", out, "--emit-steps", steps,
                                "--no-check"])
        self.assertEqual(rc, 0)
        with open(steps, encoding="utf-8") as f:
            frag = json.load(f)
        self.assertEqual([s["at"] for s in frag["steps"]], [0, 1, 2])

    def test_missing_sentences_hints_at_overflow_risk(self):
        """不给 --sentences 且自动拍不止一拍时，提示作者按句数回绕（实测摩擦：
        内容元素多于旁白句数时生成期整条 error 拒，提前出声让写稿阶段就对好）。"""
        spec = write_json(self.tmp.name, "hint.json", {
            "elements": [{"kind": "text", "id": "t", "x": 100, "y": 120,
                          "tier": "title", "content": "标题", "role": "structure"},
                         {"kind": "circle", "id": "a", "cx": 200, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "b", "cx": 400, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "c", "cx": 600, "cy": 500, "r": 40}]})
        out = os.path.join(self.tmp.name, "hint.svg")
        rc, _o, err = run_cli(["--spec", spec, "-o", out, "--no-check"])
        self.assertEqual(rc, 0)
        self.assertIn("[hint] 未给 --sentences", err)
        self.assertIn("给上 --sentences N", err)
        # 给了 --sentences 就不该再提示（自动拍已按句数回绕）。
        rc2, _o2, err2 = run_cli(["--spec", spec, "-o", out, "--sentences", "2",
                                  "--no-check"])
        self.assertEqual(rc2, 0)
        self.assertNotIn("未给 --sentences", err2)

    def test_non_positive_sentences_is_rejected_in_plain_words(self):
        """--sentences 不是正整数要报成人话 + exit 2，不是 Python 栈、也不是静默退化。

        证伪（去掉 main() 那条校验后实测）：
          - `--sentences=-3`：契约校验抛 ValueError 裸栈（"at 必须是非负数（实际: -2）"），
            rc=1——脚手架自己印的草稿，报错却不说是哪条参数错了；
          - `--sentences=0`：不报错，但自动拍静默退化成不回绕（3 个元素排到 at=0..2 是
            巧合，5 个元素就排到 at=4），与"给了句数就按句数回绕"的口径相反，那份草稿
            会被生成期整条 error 拒掉。
        """
        spec = write_json(self.tmp.name, "negsent.json", {
            "elements": [{"kind": "circle", "id": "a", "cx": 200, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "b", "cx": 400, "cy": 500, "r": 40},
                         {"kind": "circle", "id": "c", "cx": 600, "cy": 500, "r": 40}]})
        out = os.path.join(self.tmp.name, "negsent.svg")
        for bad in ("-3", "0"):
            with self.subTest(sentences=bad):
                rc, _o, err = run_cli(["--spec", spec, "-o", out,
                                       "--sentences=" + bad, "--no-check"])
                self.assertEqual(rc, 2, err)
                self.assertIn("--sentences", err)
                self.assertIn(bad, err)
                self.assertNotIn("Traceback", err)
        # 校验不过就不该落产物：留着上一轮的 SVG，作者会以为这轮也出了图。
        self.assertFalse(os.path.exists(out))

    def test_unsatisfiable_director_draft_reports_in_our_voice(self):
        """spec 合法但草稿过不了真契约时，报的是一句 [error] + 退出码 2。

        证伪：selfcheck 原先罩在 spec 的 try 外面。spec 里给 polyline 同时写 `draw` 与
        `stagger`（`draw` 那条支路不产 from/to），selfcheck 抛 ValueError 逃到顶层：
        控制台吐一段裸 traceback、退出码不是本模块的 2，而且 SVG 也没写盘——
        三个后果一起来，作者完全不知道是自己的 spec 写炸了。
        """
        spec = write_json(self.tmp.name, "badspec.json", {"elements": [
            {"kind": "polyline", "id": "curve", "role": "content", "draw": True,
             "stagger": 0.12, "points": [[100, 100], [200, 200]]}]})
        out = os.path.join(self.tmp.name, "badspec.svg")
        rc, _o, err = run_cli(["--spec", spec, "-o", out, "--no-check"])
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", err)
        self.assertIn("[error]", err)


class ContractSinking(unittest.TestCase):
    """契约下沉：作者写错时，脚本要当场把正确写法给出来，而不是让人回头翻文档。

    这一组的共同判据是"错误信息里有没有可执行的下一步"。只有"哪里错了"没有
    "该改成什么"，等于把文档的负担原样留在人身上——那正是这一轮要减掉的东西。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _err_of(self, elements, extra=()):
        spec = write_json(self.tmp.name, "s.json", {"elements": elements})
        out = os.path.join(self.tmp.name, "s.svg")
        rc, _o, err = run_cli(["--spec", spec, "-o", out, "--no-check", *extra])
        self.assertEqual(rc, 2, err)
        return err

    def test_unknown_kind_prints_all_seven_minimal_snippets(self):
        """kind 拼错时把七种kind 的最小可跑片段全列出来。

        证伪：原报错只给"只接受 axis、bars、…"，作者仍不知道 bars 该长什么样——
        而这正是照文档从零画最高频的一撞（bars 吃 base_y 加数组，不吃 x/y/w/h）。
        """
        err = self._err_of([{"kind": "line", "x1": 1, "y1": 2, "x2": 3, "y2": 4}])
        for kind in K.ELEMENT_KEYS:
            self.assertIn(kind, err)
            self.assertIn(K.MINIMAL[kind], err)
        self.assertIn('"base_y"', err)  # bars 的正确形状，不只是名字

    def test_enum_error_says_what_each_value_does(self):
        """枚举取值报错附"取值 → 实际效果"，tier 还要带两画幅的真实 px。"""
        err = self._err_of([{"kind": "text", "id": "t", "x": 1, "y": 2,
                             "content": "x", "tier": "huge"}])
        self.assertIn("'title'", err)
        self.assertIn("大标题", err)
        # px 取自 TIERS（模板真源），不是抄一份数：这里直接跟 TIERS 对，
        # 两画幅并排（竖屏/横屏），所以断言的是整段而不只是竖屏那个数
        self.assertIn("{}/{}px".format(K.TIERS["portrait"]["title"],
                                       K.TIERS["landscape"]["title"]), err)

    def test_all_bad_elements_reported_in_one_run(self):
        """多个元素各错一处时一次报全，不必改一轮跑一轮。"""
        err = self._err_of([
            {"kind": "text", "x": 1, "y": 2, "content": "a", "fill": "#zzzzzz"},
            {"kind": "text", "id": "b", "x": 1, "y": 2, "content": "b", "tier": "huge"},
            {"kind": "circle", "id": "c", "cx": 1, "cy": 2, "r": 3, "fill": "nope"},
        ])
        self.assertIn("elements[1]", err)
        self.assertIn("elements[2]", err)
        self.assertIn("elements[3]", err)
        self.assertIn("3 个元素不合格", err)

    def test_missing_id_and_bad_color_are_reported_together(self):
        """缺 id 与颜色错必须同一轮都报出来——它们原来互相遮蔽。

        证伪：缺 id 原先在 render_svg（渲染层）报，而颜色在渲染层更早的_emit 里报，
        于是颜色错先抛、缺 id 被吞；作者补完 id 重跑才看到颜色错，白跑一轮。
        遮蔽是聚合报错最实际的一种失败：不是漏报，是"报了你没看见"。
        """
        err = self._err_of([{"kind": "text", "x": 1, "y": 2, "content": "a",
                             "fill": "#zzzzzz"}])
        self.assertIn("缺 id", err)
        self.assertIn("#zzzzzz", err)

    def test_help_carries_a_runnable_spec(self):
        """--help 里那份完整 spec 必须真能跑通（否则等于给了一份假处方）。"""
        epilog = K._epilog()
        # 从 epilog 里抠出那段 spec：它是缩进过的 JSON 块
        block = re.search(r"  (\{\n(?:.*\n)*?  \})", epilog)
        self.assertIsNotNone(block, epilog)
        data = json.loads(block.group(1))
        spec = K.validate_spec(data)
        svg = K.render_svg(spec)
        root = ET.fromstring(svg)
        self.assertEqual(root.get("width"), str(get_canvas("portrait")[0]))
        # --help 里那份的每个 id 都得真在SVG 里——少一个就说明处方本身是坏的
        for el in data["elements"]:
            self.assertIn(el["id"], svg)

    def test_minimal_snippets_all_validate(self):
        """七种 kind 的最小片段每一条都得过校验（含内容元素的 id）。

        为什么这条不能省：MINIMAL 是"照文档从零画"的入口，它自己写错一份，
        报错信息就在教人怎么写错——比不写还坏。所以每一条都真跑一遍 validate_spec，
        而不是靠人眼扫一遍觉得对。

        片段**按原样**跑，测试不给它补任何键：--help 里那行标注的是"最小可跑
        片段"，作者照抄就该跑得通。早先这里替内容元素补了 id，于是片段本身缺
        id 这件事永远测不出来——守卫替被守卫的东西打了补丁。
        """
        for kind, snippet in K.MINIMAL.items():
            with self.subTest(kind=kind):
                el = json.loads(snippet)
                spec = K.validate_spec({"elements": [el]})
                svg = K.render_svg(spec)   # 渲染层也走一遍：片段要的是"能跑"
                self.assertIn("<svg", svg)


if __name__ == "__main__":
    unittest.main()
