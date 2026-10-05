"""方式 C SVG「导演」（时间轴同步动画）：净化、schema、渲染、生成期门禁的回归。"""
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
import _svg_sanitize as SAN
from _images_schema import validate_images_json
import _director_prepare as G
import check_svg


def _write_svg(tmp, sid, text):
    d = os.path.join(tmp, "images")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{sid}.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return f"images/{sid}.svg"


SVG_WITH_ALL = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" viewBox="0 0 980 735">'
    '<style>.a{animation:pulse 2s infinite}@keyframes pulse{from{opacity:0}to{opacity:1}}'
    '.b{fill:#dbe6f5}</style>'
    '<g id="node" opacity="0"><rect width="10" height="10" onclick="steal()">'
    '<animate attributeName="opacity" to="1" dur="2s"/></rect></g>'
    '<image href="https://evil/x.png"/>'
    '<script>document.title="PWNED"</script>'
    '<foreignObject width="10" height="10"><div/></foreignObject>'
    "</svg>")


class Sanitize(unittest.TestCase):
    def test_strips_scripts_handlers_and_externals(self):
        markup, notes = SAN.sanitize_svg_for_inline(SVG_WITH_ALL)
        self.assertNotIn("<script>", markup)
        self.assertNotIn("PWNED", markup)
        self.assertNotIn("onclick", markup)
        self.assertNotIn("foreignObject", markup)
        self.assertNotIn("https://evil", markup)          # 外链 href 已删
        self.assertNotIn("<animate", markup)              # SMIL 墙钟动画已剥
        self.assertNotIn("animation:", markup)            # CSS 动画已剥
        self.assertNotIn("@keyframes", markup)            # 墙钟动画的定义也剥净
        self.assertNotIn("pulse", markup)
        self.assertIn(".b{fill:#dbe6f5}", markup)         # 非墙钟样式保留
        self.assertTrue(notes)

    def test_root_rewritten_for_cover_with_single_xmlns(self):
        markup, _ = SAN.sanitize_svg_for_inline(SVG_WITH_ALL)
        self.assertEqual(markup.count("xmlns=\"http://www.w3.org/2000/svg\""), 1)
        self.assertIn('width="100%"', markup)
        self.assertIn('height="100%"', markup)
        self.assertIn('preserveAspectRatio="xMidYMid slice"', markup)

    def test_local_file_and_data_scheme_hrefs_are_stripped(self):
        """净化器对外链的判定按"任何 scheme"收口：file:/data: 等与 http 同级
        （产物 HTML 常以 file:// 打开，file: 引用会触及本机磁盘）。"""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
               'viewBox="0 0 980 735">'
               '<image href="file:///etc/passwd" x="0" y="0" width="10" height="10"/>'
               '<image href="data:image/svg+xml,&lt;svg onload=alert(1)&gt;" x="0" y="0" '
               'width="10" height="10"/>'
               '<a href="blob:https://evil/x"><rect width="10" height="10"/></a>'
               "</svg>")
        markup, notes = SAN.sanitize_svg_for_inline(svg)
        self.assertNotIn("file://", markup)
        self.assertNotIn("data:image", markup)
        self.assertNotIn("blob:", markup)
        self.assertIn("外链", "".join(notes))

    def test_fragment_and_relative_hrefs_are_kept(self):
        """内部片段（<use href="#id">）与相对路径是合法引用，净化器不能误伤。"""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
               'viewBox="0 0 980 735">'
               '<defs><circle id="c" r="5" fill="#fff"/></defs>'
               '<use href="#c" x="10" y="10"/>'
               '<image href="images/photo.png" x="0" y="0" width="20" height="20"/>'
               "</svg>")
        markup, notes = SAN.sanitize_svg_for_inline(svg)
        self.assertIn('href="#c"', markup)
        self.assertIn('href="images/photo.png"', markup)
        self.assertEqual([n for n in notes if "href" in n], [])

    def test_css_url_file_scheme_is_neutralized(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
               'viewBox="0 0 980 735">'
               '<style>.a{background:url(file:///etc/motd)}</style>'
               "<rect width=\"10\" height=\"10\" style=\"fill:url(data:image/png;base64,AAAA)\"/>"
               "</svg>")
        markup, notes = SAN.sanitize_svg_for_inline(svg)
        self.assertNotIn("file://", markup)
        self.assertNotIn("data:image", markup)
        self.assertIn("url()", "".join(notes))
        self.assertIn('viewBox="0 0 980 735"', markup)    # 几何 viewBox 保留

    def test_keeps_ids_for_director_targets(self):
        markup, _ = SAN.sanitize_svg_for_inline(SVG_WITH_ALL)
        self.assertIn('id="node"', markup)

    def test_entity_declaration_refused(self):
        with self.assertRaises(ValueError):
            SAN.sanitize_svg_for_inline('<!DOCTYPE svg [<!ENTITY a "b">]>'
                                        '<svg xmlns="http://www.w3.org/2000/svg"/>')

    def test_non_svg_root_refused(self):
        with self.assertRaises(ValueError):
            SAN.sanitize_svg_for_inline('<html><body/></html>')


class Schema(unittest.TestCase):
    def test_valid_director_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 0, "target": "#a", "set": {"opacity": 1}},
            {"at": 1, "target": "#b", "to": {"strokeDashoffset": 0, "attr": {"r": 5}},
             "duration": 0.6, "ease": "power2.out"}]}}})

    def test_fractional_at_and_draw_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 1.5, "target": "#curve", "draw": True},
            {"at": 2, "target": "#cam", "to": {"scale": 2, "svgOrigin": "500 360"}}]}}})

    def test_at_time_anchor_accepted(self):
        # at_time 与 at 二选一：整条 steps 混用两种锚点也合法（自由场景按秒编排）
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 0, "target": "#node", "set": {"opacity": 1}},
            {"at_time": 3.5, "target": "#caption", "from": {"opacity": 0}, "to": {"opacity": 1}}]}}})

    def test_at_and_at_time_mutually_exclusive(self):
        self._one_step({"at": 0, "at_time": 1.0, "target": "#a", "set": {"opacity": 1}})

    def test_no_anchor_rejected(self):
        self._one_step({"target": "#a", "set": {"opacity": 1}})

    def test_negative_and_bool_at_time_rejected(self):
        self._one_step({"at_time": -0.5, "target": "#a", "set": {"opacity": 1}})
        self._one_step({"at_time": True, "target": "#a", "set": {"opacity": 1}})

    def test_morph_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 1, "target": "#p", "morph": {"from": "M 0 0 L 1 1", "to": "M 0 5 L 1 9"},
             "duration": 1.0}]}}})

    def test_morph_topology_mismatch_rejected(self):
        self._one_step({"at": 0, "target": "#p",
                        "morph": {"from": "M 0 0 L 1 1", "to": "M 0 0 L 1 1 L 2 2"}})

    def test_morph_with_other_kinds_rejected(self):
        self._one_step({"at": 0, "target": "#p",
                        "morph": {"from": "M 0 0", "to": "M 0 1"}, "set": {"opacity": 1}})

    def test_morph_missing_to_rejected(self):
        self._one_step({"at": 0, "target": "#p", "morph": {"from": "M 0 0"}})

    def _one_step(self, step):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg",
                                          "director": {"steps": [step]}}})

    def test_director_on_non_svg_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.png", "director": {
                "steps": [{"at": 0, "target": "#a", "set": {"opacity": 1}}]}}})

    def test_handwritten_inline_svg_rejected(self):
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg", "inline_svg": "<svg/>"}})

    def test_callback_key_rejected(self):
        self._one_step({"at": 0, "target": "#a", "to": {"onStart": "x"}})

    def test_bad_target_rejected(self):
        self._one_step({"at": 0, "target": "#a .b", "set": {"opacity": 1}})

    def test_negative_and_bool_at_rejected(self):
        self._one_step({"at": -1, "target": "#a", "set": {"opacity": 1}})
        self._one_step({"at": True, "target": "#a", "set": {"opacity": 1}})

    def test_missing_tween_vars_rejected(self):
        self._one_step({"at": 0, "target": "#a"})

    def test_unknown_step_key_rejected(self):
        self._one_step({"at": 0, "target": "#a", "set": {"opacity": 1}, "when": 1})

    def test_class_target_accepted(self):
        # class 选择器（.marker）合法——stagger 一组元素靠它
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 0, "target": ".marker", "from": {"opacity": 0}, "to": {"opacity": 1},
             "stagger": 0.15}]}}})

    def test_relative_at_time_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at_time": 1.0, "target": "#a", "to": {"opacity": 1}},
            {"at_time": "+0.3", "target": "#b", "to": {"opacity": 1}},
            {"at_time": "-0.1", "target": "#c", "set": {"opacity": 0}}]}}})

    def test_relative_at_time_bad_string_rejected(self):
        self._one_step({"at_time": "0.5", "target": "#a", "set": {"opacity": 1}})   # 缺符号
        self._one_step({"at_time": "+x", "target": "#a", "set": {"opacity": 1}})
        self._one_step({"at_time": "+1s", "target": "#a", "set": {"opacity": 1}})

    def test_count_accepted(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at_time": 0.5, "target": "#num",
             "count": {"from": 0, "to": 6.02, "decimals": 2, "suffix": "×10²³"},
             "duration": 1.5}]}}})

    def test_count_with_other_kinds_rejected(self):
        self._one_step({"at": 0, "target": "#n", "count": {"to": 5}, "to": {"opacity": 1}})

    def test_count_missing_to_rejected(self):
        self._one_step({"at": 0, "target": "#n", "count": {"from": 1}})

    def test_count_bad_decimals_rejected(self):
        self._one_step({"at": 0, "target": "#n", "count": {"to": 5, "decimals": 99}})
        self._one_step({"at": 0, "target": "#n", "count": {"to": 5, "decimals": True}})

    def test_type_accepted(self):
        # 打字机：type 只认空对象 {}，快慢用 step 级 duration/ease（这里给了 duration）
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at_time": 0.5, "target": "#cap", "type": {}, "duration": 1.2}]}}})

    def test_type_with_count_rejected(self):
        self._one_step({"at": 0, "target": "#c", "type": {}, "count": {"to": 5}})

    def test_type_with_other_kinds_rejected(self):
        self._one_step({"at": 0, "target": "#c", "type": {}, "to": {"opacity": 1}})

    def test_type_non_empty_object_rejected(self):
        self._one_step({"at": 0, "target": "#c", "type": {"speed": 2}})

    def test_type_non_object_rejected(self):
        self._one_step({"at": 0, "target": "#c", "type": True})
        self._one_step({"at": 0, "target": "#c", "type": "熵"})

    def test_stagger_accepted_number_and_object(self):
        validate_images_json({"seg4": {"src": "images/seg4.svg", "director": {"steps": [
            {"at": 0, "target": ".a", "to": {"opacity": 1}, "stagger": 0.1},
            {"at": 1, "target": ".b", "to": {"opacity": 1},
             "stagger": {"each": 0.2, "from": "center"}}]}}})

    def test_stagger_bad_rejected(self):
        self._one_step({"at": 0, "target": ".a", "to": {"opacity": 1}, "stagger": -0.1})
        self._one_step({"at": 0, "target": ".a", "to": {"opacity": 1}, "stagger": True})
        self._one_step({"at": 0, "target": ".a", "to": {"opacity": 1},
                        "stagger": {"each": 0.2, "bogus": 1}})


class TweenKnobContract(unittest.TestCase):
    """step 级旋钮的唯一位置 + 缓动档名必须是钉固的 GSAP 真认的那些。

    两类静默失效在这一层挡掉：写了却不起作用（缓动认不出→退化成线性、旋钮塞在补间
    变量里→被渲染端覆盖），和写了却演不完（repeat 挂在瞬时 set 上、yoyo 只有一遍）。
    """

    def _ok(self, step):
        validate_images_json({"seg4": {"src": "images/seg4.svg",
                                      "director": {"steps": [step]}}})

    def _bad(self, step, *needles):
        with self.assertRaises(ValueError) as cm:
            self._ok(step)
        for n in needles:
            self.assertIn(n, str(cm.exception), str(cm.exception))

    def test_catalog_families_accepted_in_all_directions(self):
        # 与钉固 GSAP 的 gsap.parseEase 对拍过：这些族名 × 三个方向全部解析得出
        from _ease import EASE_FAMILIES
        for fam in sorted(EASE_FAMILIES - {"none", "steps"}):
            for mod in ("", ".in", ".out", ".inOut"):
                self._ok({"at": 0, "target": "#a", "to": {"opacity": 1},
                          "ease": fam + mod})

    def test_steps_only_takes_a_direction_with_counts(self):
        # 实测 gsap.parseEase：`steps`、`steps(4).in` 认，`steps.in` 不认（静默换缓动）
        for good in ("steps", "steps(4)", "steps(4).in", "steps(4,true).out"):
            self._ok({"at": 0, "target": "#a", "to": {"opacity": 1}, "ease": good})
        for bad in ("steps.in", "steps.out", "steps.inOut"):
            self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "ease": bad},
                      "steps 要带方向就必须给档数")

    def test_plugin_only_ease_rejected(self):
        # 实测钉固的 gsap@3.14.2 核心包解析不了这些（rough/slow/stepped 都不认）
        for bad in ("rough", "slow", "custom", "stepped(4)", "RoughEase", "morph"):
            self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "ease": bad},
                      "不在钉固的 GSAP core 里")

    def test_none_takes_no_direction(self):
        for bad in ("none.in", "none.out", "none.inOut"):
            self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "ease": bad},
                      "none 是直通档")

    def test_parameterized_ease_accepted(self):
        # 参数写在方向前后都认（back.out(1.7) / elastic(1,0.3).inOut）
        for good in ("back.out(1.7)", "elastic.inOut(1,0.3)", "steps(4,true)",
                    "power3.in", "Bounce.easeOut", "strong.out"):
            self._ok({"at": 0, "target": "#a", "to": {"opacity": 1}, "ease": good})

    def test_stagger_ease_uses_the_same_catalog(self):
        self._bad({"at": 0, "target": ".a", "to": {"opacity": 1},
                   "stagger": {"each": 0.2, "ease": "rough"}}, "不在钉固的 GSAP core 里")

    def test_control_knob_inside_payload_rejected(self):
        # 塞在补间变量里的旋钮要么被覆盖、要么让门禁算的跨度分家，只能拒
        for knob in ("duration", "ease", "delay", "stagger", "repeat", "yoyo"):
            self._bad({"at": 0, "target": "#a", "to": {"opacity": 1, knob: 2}},
                      "只在 step 级有定义")

    def test_control_knob_inside_nested_payload_rejected(self):
        """嵌套层（GSAP 的 attr:{}）同样不许有旋钮——拦截跟着递归走。

        证伪：clash 判定原先只在调用点查一次顶层，`{"to": {"attr": {"duration": 2}}}`
        整条漏过去，于是 duration="2" 被当成 SVG 属性写进元素——"每层的键都查"
        这条口径只对一层成立。
        """
        for knob in ("duration", "ease", "delay", "stagger", "repeat", "yoyo"):
            self._bad({"at": 0, "target": "#a", "to": {"attr": {knob: 2}}},
                      "只在 step 级有定义")
        # 嵌套层里的真属性照放：attr 那一层本来就是给 SVG 属性用的。
        self._ok({"at": 0, "target": "#a", "to": {"attr": {"width": 10}}})

    def test_repeat_and_yoyo_need_a_replayable_tween(self):
        for extra in ({"repeat": 2}, {"yoyo": True}):
            self._bad(dict({"at": 0, "target": "#a", "set": {"opacity": 1}}, **extra),
                      "没有可重放的补间")

    def test_repeat_zero_on_set_is_absent_repeat(self):
        self._ok({"at": 0, "target": "#a", "set": {"opacity": 1}, "repeat": 0})

    def test_yoyo_without_repeat_does_nothing(self):
        self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "yoyo": True},
                  "repeat 缺省或 0")

    def test_stagger_without_a_from_to_set_tween_is_rejected(self):
        """stagger 只对逐帧补间有意义；morph/count/type/draw 各自负责整段，渲染端会
        静默忽略——与 repeat/yoyo 挂纯 set 同一类"写了等于没写"，契约层直接拒。"""
        for extra in ({"draw": True}, {"count": {"from": 0, "to": 5}},
                      {"type": {}}, {"morph": {"from": "M0 0 L1 0 L1 1 Z",
                                               "to": "M0 0 L2 0 L2 1 Z"}}):
            self._bad(dict({"at": 0, "target": "#a", "stagger": 0.12}, **extra),
                      "却没有 from / to / set 补间")
        # 带 from/to/set 的照放：错峰揭示本来就靠它
        for step in ({"at": 0, "target": ".a", "to": {"opacity": 1}, "stagger": 0.12},
                     {"at": 0, "target": ".a", "from": {"opacity": 0},
                      "to": {"opacity": 1}, "stagger": {"each": 0.2, "from": "start"}},
                     {"at": 0, "target": ".a", "set": {"opacity": 1}, "stagger": 0.1}):
            self._ok(step)
        self._ok({"at": 0, "target": "#a", "to": {"opacity": 1}, "yoyo": False})

    def test_repeat_must_be_an_integer_ge_minus_one(self):
        for bad in (1.5, -2, True, "3"):
            self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "repeat": bad},
                      "repeat 必须是")

    def test_repeats_accepted_on_every_replayable_primitive(self):
        steps = [{"at": 0, "target": "#a", "draw": True, "repeat": 1, "yoyo": True},
                 {"at": 0, "target": "#n", "count": {"to": 5}, "repeat": -1},
                 {"at": 0, "target": "#t", "type": {}, "repeat": 2, "yoyo": True},
                 {"at": 0, "target": "#p", "morph": {"from": "M 0 0 L 1 1",
                                                     "to": "M 0 5 L 1 9"}, "repeat": 3}]
        for step in steps:
            self._ok(step)

    def test_infinite_repeat_rejected_for_morph_only(self):
        """morph 是生成期采样：没有终点就无从采样。运行期的补间/count/type 相反是确定的。"""
        self._bad({"at": 0, "target": "#p", "morph": {"from": "M 0 0 L 1 1",
                                                      "to": "M 0 5 L 1 9"},
                   "repeat": -1}, "没有终点可采")


class DirectorPrepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_attaches_inline_and_warns_on_stripped(self):
        rel = _write_svg(self.tmp.name, "seg-a", SVG_WITH_ALL)
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at": 0, "target": "#node", "to": {"opacity": 1}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, warns = G.director_prepare(images, segs, self.tmp.name)
        self.assertEqual(errs, [])
        self.assertIn("inline_svg", images["seg-a"])
        self.assertNotIn("<script>", images["seg-a"]["inline_svg"])
        self.assertTrue(any("SMIL" in w or "script" in w for w in warns))

    def test_missing_target_id_is_error(self):
        rel = _write_svg(self.tmp.name, "seg-a", SVG_WITH_ALL)
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at": 0, "target": "#ghost", "to": {"opacity": 1}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, _ = G.director_prepare(images, segs, self.tmp.name)
        self.assertTrue(any("找不到对应 id" in e for e in errs))

    def test_camera_crop_warns_here_not_in_check_svg(self):
        """裁切的判据是 SVG 原文 **加上** director steps，只有这一层同时握有两者。

        `check_svg.py` 读单文件、看不到 images.json 的运镜参数，所以这类 bug 必须由
        prepare 报；反过来 prepare 也不该把它升级成 error——途中出画是合法演法，
        静止位看不见某样东西也可能是作者故意的取舍。
        """
        rel = _write_svg(self.tmp.name, "seg-a",
                         '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
                         '<g id="cam"><rect id="band" x="80" y="1362" width="920" '
                         'height="60"/></g></svg>')
        images = {"seg-a": {"src": rel, "director": {"steps": [
            {"at": 0, "target": "#cam", "to": {"scale": 1.16, "svgOrigin": "540 660"}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, warns = G.director_prepare(images, segs, self.tmp.name)
        self.assertEqual(errs, [])
        crop = [w for w in warns if "运镜收尾位" in w]
        self.assertEqual(len(crop), 1, warns)
        self.assertIn("#band", crop[0])

    def test_camera_resting_inside_frame_stays_silent(self):
        """噪声检查：同一份稿子把文字带挪出 #cam，相机数字一字不改 → 一条不报。"""
        rel = _write_svg(self.tmp.name, "seg-a",
                         '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
                         '<g id="cam"><rect id="panel" x="60" y="300" width="960" '
                         'height="700"/></g>'
                         '<rect id="band" x="80" y="1362" width="920" height="60"/></svg>')
        images = {"seg-a": {"src": rel, "director": {"steps": [
            {"at": 0, "target": "#cam", "to": {"scale": 1.16, "svgOrigin": "540 660"}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        _, warns = G.director_prepare(images, segs, self.tmp.name)
        self.assertEqual([w for w in warns if "运镜收尾位" in w], [])

    def test_class_target_present_passes(self):
        rel = _write_svg(self.tmp.name, "seg-a",
                         '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 980 735">'
                         '<g class="mk a"/><g class="b mk"/></svg>')
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at": 0, "target": ".mk", "to": {"opacity": 1}, "stagger": 0.1}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, _ = G.director_prepare(images, segs, self.tmp.name)
        self.assertEqual(errs, [])

    def test_class_target_missing_is_error(self):
        rel = _write_svg(self.tmp.name, "seg-a",
                         '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 980 735">'
                         '<g class="mk"/></svg>')
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at": 0, "target": ".ghost", "to": {"opacity": 1}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, _ = G.director_prepare(images, segs, self.tmp.name)
        self.assertTrue(any("找不到对应 id/class" in e for e in errs))

    def test_at_out_of_range_is_error(self):
        rel = _write_svg(self.tmp.name, "seg-a", SVG_WITH_ALL)
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at": 5, "target": "#node", "to": {"opacity": 1}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, _ = G.director_prepare(images, segs, self.tmp.name)
        self.assertTrue(any("越界" in e for e in errs))

    def test_at_time_skips_sentence_range_check(self):
        # 只有一句旁白的段用 at_time 锚点：没有句序可锚，不该报越界，target 命中即通过
        rel = _write_svg(self.tmp.name, "seg-a", SVG_WITH_ALL)
        images = {"seg-a": {"src": rel, "director": {
            "steps": [{"at_time": 3.0, "target": "#node", "to": {"opacity": 1}}]}}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]}]
        errs, _ = G.director_prepare(images, segs, self.tmp.name)
        self.assertEqual([e for e in errs if "越界" in e], [])
        self.assertIn("inline_svg", images["seg-a"])


class CheckSvgWallclock(unittest.TestCase):
    def test_wallclock_animation_warns_director_hint(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
               'viewBox="0 0 980 735"><circle cx="10" cy="10" r="5" fill="#fff">'
               '<animate attributeName="r" to="20" dur="2s"/></circle></svg>')
        p = _write_svg(d.name, "w", svg)
        errs, warns = check_svg.check_file(os.path.join(d.name, p), "slot", "portrait", "dark")
        self.assertEqual([e for e in errs if "墙钟" in e], [])
        self.assertTrue(any("墙钟动画" in w and "director" in w for w in warns))


if __name__ == "__main__":
    unittest.main()
