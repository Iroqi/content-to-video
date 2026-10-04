"""方式 C SVG「导演」（时间轴同步动画）：净化、schema、渲染、生成期门禁的回归。"""
import os
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
import _svg_sanitize as SAN
from _images_schema import validate_images_json
import html_renderer as HR
import gen_hyperframes as G
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

    def test_repeat_and_yoyo_need_a_replayable_tween(self):
        for extra in ({"repeat": 2}, {"yoyo": True}):
            self._bad(dict({"at": 0, "target": "#a", "set": {"opacity": 1}}, **extra),
                      "没有可重放的补间")

    def test_repeat_zero_on_set_is_absent_repeat(self):
        self._ok({"at": 0, "target": "#a", "set": {"opacity": 1}, "repeat": 0})

    def test_yoyo_without_repeat_does_nothing(self):
        self._bad({"at": 0, "target": "#a", "to": {"opacity": 1}, "yoyo": True},
                  "repeat 缺省或 0")
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


class Renderer(unittest.TestCase):
    def _images(self):
        return {
            "seg-a": {"src": "images/seg-a.svg", "inline_svg": "<svg viewBox=\"0 0 980 735\"><g id=\"node\"/></svg>",
                      "director": {"steps": [
                          {"at": 0, "target": "#node", "to": {"opacity": 1}, "duration": 0.4},
                          {"at": 1, "target": "#node", "set": {"fill": "#f00"}}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }

    def test_inline_svg_and_scoped_tweens(self):
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", images=self._images(), aspect="portrait")
        self.assertIn('class="seg-image bare-media svg-inline" id="img-seg-a"', html)
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        t0 = round(seg_a["sentences"][0]["start_time"], 2)
        t1 = round(seg_a["sentences"][1]["start_time"], 2)
        # target 收到 #img-seg-a 作用域；缺省 ease 来自模板 animation.director
        self.assertIn(f'tl.to("#img-seg-a #node", '
                      f'{{"opacity": 1, "duration": 0.4, "ease": "power2.out"}}, {t0:.2f})', html)
        self.assertIn(f'tl.set("#img-seg-a #node", {{"fill": "#f00"}}, {t1:.2f})', html)
        # 普通 SVG（seg-b）仍是 <img>，没有内联
        self.assertIn('<img src="images/seg-b.svg"', html)

    def test_video_entry_renders_video_tag_with_playback_attrs(self):
        """视频素材出 <video> 而非 <img>：四个播放开关默认全开（渲染端按
        opts.get(flag, True) 取值），poster 只在给了的时候拼上去。
        段 b 是 canvas 段（缺配图会报错），两条都得给映射。"""
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", aspect="portrait", images={
            "seg-a": {"src": "images/seg-a.png"},
            "seg-b": {"src": "images/seg-b.mp4", "poster": "images/seg-b-cover.png"}})
        self.assertIn('<video id="vid-seg-b" src="images/seg-b.mp4"', html)
        self.assertIn('poster="images/seg-b-cover.png"', html)
        v = html.split('<video id="vid-seg-b"')[1].split("</video>")[0]
        # 缺省即开：四个开关都在
        for flag in ("loop", "muted", "autoplay", "playsinline"):
            self.assertIn(flag, v.split(">")[0], flag)
        # 静态图那段仍是 <img>，没被带成视频
        self.assertIn('<img src="images/seg-a.png"', html)

    def test_video_playback_flags_can_be_switched_off(self):
        """显式写 false 的开关不出现在标签里——渲染端按 opts.get(flag, True) 取值，
        属性在就是开。loop 关掉的视频只播一遍。"""
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", aspect="portrait", images={
            "seg-a": {"src": "images/seg-a.png"},
            "seg-b": {"src": "images/seg-b.mp4", "loop": False, "muted": False}})
        v = html.split('<video id="vid-seg-b"')[1].split("</video>")[0]
        head = v.split(">")[0]
        self.assertNotIn("loop", head)
        self.assertNotIn("muted", head)
        self.assertIn("autoplay", head)
        self.assertIn("playsinline", head)

    def test_gif_renders_as_img_not_video(self):
        """gif 是会自己动的静态图，走 <img>；别把它当视频塞进 <video>。
        断言只查标签（CSS 里有 .seg-image video 这类选择器字面量，整页找会误判）。"""
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", aspect="portrait", images={
            "seg-a": {"src": "images/seg-a.png"},
            "seg-b": {"src": "images/seg-b.gif"}})
        self.assertIn('<img src="images/seg-b.gif"', html)
        self.assertNotIn("<video id=", html)

    def test_explicit_type_overrides_extension(self):
        """type 显式指定优先于扩展名：.gif 写 type:"video" 就该出 <video>。"""
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", aspect="portrait", images={
            "seg-a": {"src": "images/seg-a.png"},
            "seg-b": {"src": "images/seg-b.gif", "type": "video"}})
        self.assertIn('<video id="vid-seg-b" src="images/seg-b.gif"', html)

    def test_at_out_of_range_raises(self):
        m = H.make_manifest()
        imgs = self._images()
        imgs["seg-a"]["director"]["steps"].append({"at": 99, "target": "#node", "set": {"opacity": 1}})
        with self.assertRaises(ValueError):
            HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")

    def test_fractional_at_draw_and_camera(self):
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)          # 段起点=第0句start
        d0 = seg_a["sentences"][0]["duration"]
        mid = round(s0 + 0.5 * d0, 2)                                # at=0.5 → 句内一半
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="cam"><path id="curve"/></g></svg>',
                      "director": {"steps": [
                          {"at": 0.5, "target": "#curve", "draw": True, "duration": 1.2},
                          {"at": 1, "target": "#cam", "to": {"scale": 2, "svgOrigin": "500 360"},
                           "duration": 1.5, "ease": "power2.inOut"}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        # draw：段起点收起描边（pathLength 归一），到 at 位置补到 0
        self.assertIn(f'tl.set("#img-seg-a #curve", '
                      f'{{"attr": {{"pathLength": 1}}, "strokeDasharray": 1, "strokeDashoffset": 1}}, {s0:.2f})', html)
        self.assertIn(f'tl.to("#img-seg-a #curve", '
                      f'{{"strokeDashoffset": 0, "duration": 1.2, "ease": "power2.out"}}, {mid:.2f})', html)
        # 相机：对 #cam 的 transform 补间，位置取整句起点（at=1）
        self.assertIn('#img-seg-a #cam', html)
        self.assertIn('"scale": 2, "svgOrigin": "500 360"', html)

    # ---- 缺省原点的钉值（GSAP 对没写原点的 SVG 绕 bbox 左上角，不是中心）----

    def _cam_html(self, steps, svg=None):
        m = H.make_manifest()
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": svg or ('<svg viewBox="0 0 980 735"><g id="cam">'
                                            '<rect id="node" x="100" y="100" width="200" '
                                            'height="200"/></g></svg>'),
                      "director": {"steps": steps}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        return HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")

    def test_camera_scale_without_origin_gets_the_content_center_pinned(self):
        # 唯一那块 rect 的内容中心 = (200, 200)；钉进去之后 GSAP 才和烘焙/warn 同一点
        html = self._cam_html([{"at": 1, "target": "#cam", "to": {"scale": 1.2}}])
        self.assertIn('"svgOrigin": "200 200"', html)

    def test_camera_set_and_to_payloads_are_pinned(self):
        for step in ({"at": 1, "target": "#cam", "set": {"scale": 1.2}},
                     {"at": 1, "target": "#cam", "to": {"scale": 1.2}},
                     {"at": 1, "target": "#cam", "from": {"scale": 0.8},
                      "to": {"scale": 1.2}}):
            self.assertIn('"svgOrigin": "200 200"', self._cam_html([step]))

    def test_from_only_camera_is_not_pinned(self):
        """`from` 的终值 = 元素原样：没有收尾位要对齐，也就没有会分家的原点（出画
        warn 按口径同样不看瞬态），所以这里不替作者凭空加一个数。"""
        self.assertNotIn("svgOrigin",
                         self._cam_html([{"at": 1, "target": "#cam", "from": {"scale": 1.2}}]))

    def test_pan_only_camera_is_left_unpinned(self):
        """只有平移时原点根本不参与，注入它是白加一个作者没写的数。"""
        html = self._cam_html([{"at": 1, "target": "#cam", "to": {"y": 40}}])
        self.assertNotIn("svgOrigin", html)

    def test_authors_own_origin_is_never_touched(self):
        html = self._cam_html([{"at": 1, "target": "#cam",
                                "to": {"scale": 1.2, "svgOrigin": "500 360"}}])
        self.assertIn('"svgOrigin": "500 360"', html)
        self.assertNotIn('"svgOrigin": "200 200"', html)

    def test_non_camera_steps_never_get_the_pin(self):
        html = self._cam_html([{"at": 1, "target": "#node", "to": {"scale": 1.2}}])
        self.assertNotIn("svgOrigin", html)

    def test_at_time_anchored_to_segment_start(self):
        # at_time 锚在段落音频起点(card.s)往后推，与句子边界无关；带 delay 再往后
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        pos = round(s0 + 1.0 + 0.25, 2)          # at_time=1.0 + delay=0.25
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="node"/></svg>',
                      "director": {"steps": [
                          {"at_time": 1.0, "delay": 0.25, "target": "#node",
                           "set": {"opacity": 0.5}}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn(f'tl.set("#img-seg-a #node", {{"opacity": 0.5}}, {pos:.2f})', html)

    def test_morph_bakes_keyframes(self):
        # morph 生成期烘焙成 fps×2+1 条 tl.set(attr:{d})，端点等于 from/to，位置从锚点铺满 duration
        import re
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 10 10"><path id="p"/></svg>',
                      "director": {"steps": [
                          {"at_time": 0.0, "target": "#p",
                           "morph": {"from": "M 0 0 L 1 1", "to": "M 0 5 L 1 9"},
                           "duration": 1.0}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait", fps=24)
        sets = re.findall(r'tl\.set\("#img-seg-a #p", \{"attr": \{"d": "([^"]+)"\}\}, ([\d.]+)\)', html)
        self.assertEqual(len(sets), 24 * 2 + 1)               # fps×2 采样 + 端点
        self.assertEqual(sets[0][0], "M0 0 L1 1")             # k=0 → from
        self.assertEqual(sets[-1][0], "M0 5 L1 9")            # k=1 → to
        self.assertEqual(sets[0][1], f"{s0:.2f}")            # 从锚点起
        self.assertEqual(sets[-1][1], f"{s0 + 1.0:.2f}")     # 铺满 duration
        # 缺省 ease=power2.out：u=0.5 处形状进度 k=.875（前载），不是线性的 .5
        self.assertEqual(sets[24][0], "M0 4.375 L1 8")

    def _morph_frames(self, step, fps=24):
        """真实跑一遍渲染，取回这条 morph 步烘焙出的 (页内时刻, 形状进度) 序列。

        进度从 d 的第一个 y 反解（from 的 y=0、to 的 y=5，插值对进度是线性的），所以断言
        读的是**成片里真会显示的那个形状**，不是采样公式的复刻。期望值一律实测自钉固的
        gsap@3.14.2（同 ease/repeat/yoyo 的代理补间，逐时刻 seek 取值）。
        """
        import re
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 10 10"><path id="p"/></svg>',
                      "director": {"steps": [
                          dict(step, target="#p",
                               morph={"from": "M 0 0 L 1 1", "to": "M 0 5 L 1 9"})]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs,
                                aspect="portrait", fps=fps)
        found = re.findall(
            r'tl\.set\("#img-seg-a #p", \{"attr": \{"d": "M0 (-?[\d.]+) L1 [^"]*"\}\}, ([\d.]+)\)',
            html)
        return [(round(float(t) - s0, 2), float(y) / 5.0) for y, t in found]

    def _assert_at(self, frames, expected):
        for t, want in expected:
            _near, got = min(frames, key=lambda f: abs(f[0] - t))
            self.assertAlmostEqual(got, want, places=3, msg=f"t={t}s 处形状 {got} ≠ {want}")

    # 这三张表是从钉固的那份 gsap.min.js 里逐时刻 seek 出来的真值，不是手算的
    GSAP_POWER2_OUT_R2 = [(0.0, 0.0), (0.5, 0.875), (1.0, 1.0), (1.5, 0.875),
                          (2.0, 1.0), (2.5, 0.875), (3.0, 1.0)]
    GSAP_POWER2_INOUT_R1_YOYO = [(0.0, 0.0), (0.25, 0.0625), (0.5, 0.5),
                                 (0.625, 0.789063), (0.75, 0.9375), (1.0, 1.0),
                                 (1.25, 0.9375), (1.5, 0.5), (1.75, 0.0625),
                                 (2.0, 0.0)]
    GSAP_BOUNCE_OUT_R2_YOYO = [(0.0, 0.0), (0.5, 0.765625), (1.0, 1.0),
                               (1.5, 0.765625), (2.0, 0.0), (2.5, 0.765625),
                               (3.0, 1.0)]

    def test_repeat_stretches_the_morph_span(self):
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "power2.out", "repeat": 2})
        self.assertEqual(len(frames), 3 * 24 * 2 + 1)     # 采样按总跨度，不是单遍
        self.assertAlmostEqual(frames[-1][0], 3.0, places=2)
        self._assert_at(frames, self.GSAP_POWER2_OUT_R2)

    def test_morph_cycle_boundary_is_the_previous_cycles_end(self):
        """遍与遍的分界：GSAP 在这一瞬间报的是上一遍演完的样子，下一遍才从 0 重放。

        采样按"这一遍的第 0 帧"处理的话，边界帧会凭空闪回起点。
        """
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "power2.out", "repeat": 2})
        for t in (1.0, 2.0, 3.0):
            self._assert_at(frames, [(t, 1.0)])

    def test_yoyo_morph_matches_pinned_gsap(self):
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "power2.inOut", "repeat": 1, "yoyo": True})
        self._assert_at(frames, self.GSAP_POWER2_INOUT_R1_YOYO)

    def test_yoyo_bounce_morph_matches_pinned_gsap(self):
        """倒放是"拿倒放的进度去查同一条缓动"（ease(1-p)），不是 1-ease(p)。

        bounce 那两条差别很大，正好把口径钉死。
        """
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "bounce.out", "repeat": 2, "yoyo": True})
        self._assert_at(frames, self.GSAP_BOUNCE_OUT_R2_YOYO)

    def test_yoyo_even_cycles_morph_lands_back_on_from(self):
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "bounce.out", "repeat": 1, "yoyo": True})
        self.assertEqual(frames[-1][1], 0.0)              # 收尾回 from
        self.assertEqual(frames[0][1], 0.0)

    def test_yoyo_odd_cycles_morph_lands_on_to(self):
        frames = self._morph_frames({"at_time": 0.0, "duration": 1.0,
                                     "ease": "bounce.out", "repeat": 2, "yoyo": True})
        self.assertEqual(frames[-1][1], 1.0)

    def test_repeat_and_yoyo_reach_the_tween_vars(self):
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="node"/></svg>',
                      "director": {"steps": [
                          {"at_time": 0.0, "target": "#node", "to": {"opacity": 1},
                           "duration": 0.4, "repeat": 3, "yoyo": True}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn(f'tl.to("#img-seg-a #node", {{"opacity": 1, "duration": 0.4, '
                      f'"ease": "power2.out", "repeat": 3, "yoyo": true}}, {s0:.2f})', html)

    def test_cycle_vars_absent_when_not_written(self):
        # 没写 repeat 就不该多出这两个 GSAP 变量（golden 与字节稳定的护栏）
        m = H.make_manifest()
        imgs = {"seg-a": {"src": "images/seg-a.svg",
                          "inline_svg": '<svg viewBox="0 0 980 735"><g id="node"/></svg>',
                          "director": {"steps": [
                              {"at_time": 0.0, "target": "#node",
                               "to": {"opacity": 1}, "duration": 0.4}]}},
                "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertNotIn('"repeat"', html)
        self.assertNotIn('"yoyo"', html)

    def test_draw_and_count_and_type_carry_repeat(self):
        m = H.make_manifest()
        imgs = {"seg-a": {"src": "images/seg-a.svg",
                          "inline_svg": ('<svg viewBox="0 0 980 735"><path id="c"/>'
                                         '<text id="n">0</text><text id="t">字</text></svg>'),
                          "director": {"steps": [
                              {"at_time": 0.0, "target": "#c", "draw": True,
                               "duration": 0.8, "repeat": 1, "yoyo": True},
                              {"at_time": 1.0, "target": "#n", "count": {"to": 10},
                               "duration": 0.8, "repeat": 2},
                              {"at_time": 2.0, "target": "#t", "type": {},
                               "duration": 0.8, "repeat": 1, "yoyo": True}]}},
                "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn('"strokeDashoffset": 0, "duration": 0.8, "ease": "power2.out", '
                      '"repeat": 1, "yoyo": true', html)
        self.assertIn('tl.to(p,{v:10.0,duration:0.8,ease:"power2.out",repeat:2,onUpdate:f}', html)
        self.assertIn('tl.to(p,{k:s.length,duration:0.8,ease:"power2.out",'
                      'repeat:1,yoyo:true,onUpdate:f}', html)

    def test_count_emits_proxy_tween(self):
        # count：渲染端生成代理补间 + onUpdate 写 textContent（seek 可复现）
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><text id="num"/></svg>',
                      "director": {"steps": [
                          {"at_time": 0.5, "target": "#num",
                           "count": {"to": 6.02, "decimals": 2}, "duration": 1.5}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn('document.querySelector("#img-seg-a #num")', html)
        self.assertIn("p.v.toFixed(2)", html)
        # 关键：补间目标值 / onUpdate / 锚点时刻都在
        self.assertIn('tl.to(p,{v:6.02,duration:1.5,ease:"power2.out",onUpdate:f},'
                      f'{s0 + 0.5:.2f})', html)
        # ASI 护栏：count 块以 `(function...)()` 开头，必须前导 `;`——否则紧跟无分号的
        # 补间行会被黏成 `tl.xxx(...)(function...)()`，运行期抛 "is not a function"、
        # 主时间轴中断（__timelines.main 永不注册）。回归 entropy demo 首帧空轴 bug。
        self.assertIn(';(function(){var e=document.querySelector', html)

    def test_type_emits_typewriter_tween(self):
        # type：渲染端读 <text> 现成文本、代理 p.k 从 0→长度、onUpdate 逐字切片写回
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><text id="cap">熵增原理</text></svg>',
                      "director": {"steps": [
                          {"at_time": 0.5, "target": "#cap", "type": {},
                           "duration": 1.2}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn('document.querySelector("#img-seg-a #cap")', html)
        self.assertIn("var s=e.textContent", html)
        self.assertIn("s.slice(0,Math.round(p.k))", html)
        # 关键：代理补间到文本长度、onUpdate、锚点时刻都在；前导 ; 断掉上一条补间行
        self.assertIn('tl.to(p,{k:s.length,duration:1.2,ease:"power2.out",onUpdate:f},'
                      f'{s0 + 0.5:.2f})', html)
        self.assertIn(';(function(){var e=document.querySelector("#img-seg-a #cap")', html)

    def test_stagger_injected_into_tween_vars(self):
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g class="mk"/></svg>',
                      "director": {"steps": [
                          {"at_time": 0.0, "target": ".mk", "from": {"opacity": 0},
                           "to": {"opacity": 1}, "duration": 0.4, "stagger": 0.15}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        # stagger 进 end-vars（fromTo 的第三个参数），class 目标作用域照旧收在 #img-seg-a 下
        self.assertIn('tl.fromTo("#img-seg-a .mk", {"opacity": 0}, '
                      '{"opacity": 1, "duration": 0.4, "ease": "power2.out", "stagger": 0.15}, '
                      f'{s0:.2f})', html)

    def test_relative_at_time_chains_from_previous_beat(self):
        # 首条绝对 at_time=1.0（补间 dur=0.5）→ 游标到 s0+1.5；次条 "+0.3" 接在其后
        m = H.make_manifest()
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        s0 = round(seg_a["sentences"][0]["start_time"], 2)
        imgs = {
            "seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": '<svg viewBox="0 0 980 735"><g id="a"/><g id="b"/></svg>',
                      "director": {"steps": [
                          {"at_time": 1.0, "target": "#a", "to": {"opacity": 1}, "duration": 0.5},
                          {"at_time": "+0.3", "target": "#b", "to": {"opacity": 1}, "duration": 0.5}]}},
            "seg-b": {"src": "images/seg-b.svg"},
        }
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertIn('{"opacity": 1, "duration": 0.5, "ease": "power2.out"}, '
                      + f'{s0 + 1.0:.2f})', html)
        self.assertIn('{"opacity": 1, "duration": 0.5, "ease": "power2.out"}, '
                      + f'{s0 + 1.8:.2f})', html)


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
