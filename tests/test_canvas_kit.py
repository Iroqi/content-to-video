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
        # 判据调 check_svg 那一份，不在测试里再抄一份 0.98——这份源码当初
        # 就是"抄了一份阈值"才漂移的，测试自己也抄就会把漂移照抄一遍。
        for aspect in ("portrait", "landscape"):
            w, h = get_canvas(aspect)
            root = ET.fromstring(K.render_svg(K.validate_spec(spec_for(aspect))))
            for el in root.iter():
                if local(el.tag) == "rect":
                    self.assertFalse(check_svg.is_full_bleed(el, w, h),
                                     "画布页铺了满幅底板，模板那三层背景会被盖掉")

    def test_full_bleed_panel_is_refused_before_it_is_emitted(self):
        w, h = get_canvas("portrait")
        spec = {"elements": [{"kind": "panel", "x": 0, "y": 0, "w": w, "h": h}]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("铺满了整页", str(cm.exception))

    def test_almost_full_panel_is_refused_too(self):
        """差两成的 panel 同样盖死三层，且这条只有"真调共用判据"才拦得住。

        这一条同时钉住两件事：canvas_kit 的判据确实是共享的那份（0.98 宽高双阈值
        会让这张溜过去），以及判据本身按面积而不是宽高各别。前一版这里手抄了一个
        0.98 字面量，两处漂移过一次注释，而测试只测「恰好铺满」——换成自算的 0.999
        照样过，于是"判据只有一份"这条约定实际上没有任何测试在守。
        """
        w, h = get_canvas("portrait")
        spec = {"elements": [{"kind": "panel", "x": 0, "y": 0,
                               "w": round(w * 0.978), "h": h}]}
        with self.assertRaises(ValueError) as cm:
            K.render_svg(K.validate_spec(spec))
        self.assertIn("铺满了整页", str(cm.exception))
        # 缩进边距的 panel 是正当画法，不该被这条拦住
        spec = {"elements": [{"kind": "panel", "x": 60, "y": 60,
                               "w": round(w * 0.978), "h": round(h * 0.978)}]}
        K.render_svg(K.validate_spec(spec))

    def test_panel_gate_calls_the_shared_predicate_instead_of_recomputing_it(self):
        """结构门禁：canvas_kit 不许自己重算「满幅」，必须调 check_svg 那一份。

        行为测试有个盲区——把自算的阈值调得比共用判据更严，行为测试照样全绿
        （"恰好铺满"两边都拦），于是"判据只有一份"这条约定没人守。真出现过一次：
        canvas_kit 手抄 0.98 字面量，两处注释漂移过一次。
        """
        with open(os.path.join(H.SCRIPTS_DIR, "canvas_kit.py"), encoding="utf-8") as f:
            src = re.sub(r"#.*", "", f.read())        # 剥注释，注释里提判据不算调用
        self.assertIn("check_svg.is_full_bleed", src)
        # 满幅判据里出现画幅乘法 = 就是在本地重算一份
        self.assertNotRegex(src, r"\b\w+\s*[&*]=\s*[\w.]+\s*\*\s*0\.\d+",
                            "canvas_kit 里出现了本地阈值字面量：满幅判据只有 "
                            "check_svg.is_full_bleed 一份")

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

    def test_draw_ready_geometry_carries_pathlength(self):
        nodes = self.nodes()
        self.assertEqual(nodes["curve"].get("pathLength"), "1")
        self.assertEqual(nodes["underline"].get("pathLength"), "1")


class DirectorDraft(unittest.TestCase):
    def fragment(self, spec):
        return K.selfcheck_fragment(K.validate_spec(spec))

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
        """新特性要有脚手架出口：呼吸/脉动在 spec 里写一次，草稿原样带出。"""
        spec = {"elements": [
            {"kind": "circle", "id": "a", "cx": 100, "cy": 100, "r": 10,
             "repeat": 3, "yoyo": True},
            {"kind": "circle", "id": "b", "cx": 200, "cy": 100, "r": 10,
             "repeat": -1},
            {"kind": "circle", "id": "c", "cx": 300, "cy": 100, "r": 10,
             "repeat": 0},
        ]}
        steps = self.fragment(spec)["steps"]
        self.assertEqual(steps[0]["repeat"], 3)
        self.assertEqual(steps[0]["yoyo"], True)
        self.assertEqual(steps[1]["repeat"], -1)
        self.assertNotIn("yoyo", steps[1])
        # repeat:0 与缺省等价，不塞进去（与渲染端 _cycle_vars 同口径）
        self.assertNotIn("repeat", steps[2])
        # 真契约：带 repeat/yoyo 的草稿必须能直接过 images.json 的 director 校验
        validate_images_json({"seg7": {"src": "images/seg7.svg",
                                       "director": {"steps": steps}}})

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
        会漂的色（本仓历史上真漂过一次页底色，见 check_svg 的 PAGE_BG 注释）。
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


if __name__ == "__main__":
    unittest.main()
