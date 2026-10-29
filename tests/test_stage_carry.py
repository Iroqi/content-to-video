"""跨段场景延续（persistent stage）：收尾态烘焙、相机折层、生成期接续门禁的回归。"""
import contextlib
import io
import re
import unittest
import xml.etree.ElementTree as ET

import _helpers as H                       # noqa: F401  sys.path 装配
import _svg_sanitize as SAN
import _stage_carry as SC
import _cam_crop as CC
import gen_hyperframes as G
from _cam_crop import cam_content_center, cam_geometry, crop_warnings, decompose_transform
from _images_schema import MEDIA_ENTRY_KEYS, validate_images_json
from test_animate_timeline import _write_svg

NS = "http://www.w3.org/2000/svg"

STAGE_SVG = (
    '<svg xmlns="%s" viewBox="0 0 1080 1440">'
    '<g id="cam">'
    '<rect id="plate" x="60" y="300" width="960" height="500" opacity="0"/>'
    '<path id="line" d="M 100 200 L 300 200"/>'
    '<text id="num" x="200" y="900">0</text>'
    "</g></svg>")

# 两份副本共享的元素：<defs> 里的渐变、被 <use href> 引用的图元
DEFS_SVG = (
    '<svg xmlns="%s" viewBox="0 0 1080 1440">'
    '<defs><linearGradient id="warm"><stop offset="0" stop-color="#f00"/>'
    "</linearGradient></defs>"
    '<g id="cam">'
    '<rect id="plate" x="60" y="300" width="960" height="500" fill="url(#warm)"/>'
    '<g id="icon"><circle cx="80" cy="80" r="8"/></g>'
    '<use href="#icon"/>'
    "</g></svg>")


def _bake(steps, markup=None, beats=None):
    markup, _ = SAN.sanitize_svg_for_inline(markup or STAGE_SVG)
    return SC.bake_settled_state(markup, steps, beats)


def _find(markup, el_id):
    root = ET.fromstring(markup)
    return root.find(".//*[@id='%s']" % el_id)


class SettleElementState(unittest.TestCase):
    def test_to_and_set_become_inline_style_and_attribute(self):
        out, notes = _bake([
            {"target": "#plate", "to": {"opacity": 1}},
            {"target": "#plate", "set": {"attr": {"fill": "#f00"}, "strokeWidth": 4}},
        ])
        plate = _find(out, "plate")
        self.assertIn("opacity:1", plate.get("style"))
        self.assertIn("stroke-width:4", plate.get("style"))   # 驼峰必须转成 CSS 认的名字
        self.assertEqual(plate.get("fill"), "#f00")           # attr:{} 走 attribute
        self.assertEqual(notes, [])

    def test_from_only_is_no_op(self):
        """`from` 的结束态 = 演之前的样子，而"演之前"已经是上一段烘焙的结果。"""
        out, notes = _bake([{"target": "#plate", "from": {"opacity": 0}}])
        self.assertNotIn("style", _find(out, "plate").attrib)
        self.assertEqual(notes, [])

    def test_later_beat_wins_even_if_written_earlier_in_array(self):
        """结算顺序按**时刻**、不按数组序：数组第 1 条可以在第 2 条之后演完。"""
        out, _ = _bake([{"target": "#plate", "to": {"opacity": 0.2}},
                        {"target": "#plate", "to": {"opacity": 0.9}}],
                       beats=[(5.0, 0.6), (1.0, 0.6)])
        self.assertIn("opacity:0.2", _find(out, "plate").get("style"))

    def test_relative_css_value_resolves_against_current(self):
        out, _ = _bake([{"target": "#plate", "to": {"opacity": "+=0.5"}}])
        # 盘上写的是 opacity="0"（attribute），GSAP 补的是它自己的组件值：这里以
        # attribute 为基值，0 + 0.5 = 0.5。
        self.assertIn("opacity:0.5", _find(out, "plate").get("style"))

    def test_draw_settles_to_fully_stroked(self):
        out, _ = _bake([{"target": "#line", "draw": True}])
        line = _find(out, "line")
        self.assertEqual(line.get("pathLength"), "1")
        self.assertIn("stroke-dasharray:1", line.get("style"))
        self.assertIn("stroke-dashoffset:0", line.get("style"))

    def test_morph_and_count_settle_to_their_end_values(self):
        out, _ = _bake([
            {"target": "#line", "morph": {"from": "M 100 200 L 300 200",
                                          "to": "M 100 400 L 300 400"}},
            {"target": "#num", "count": {"to": 68, "decimals": 1, "suffix": "%"}},
        ])
        self.assertEqual(_find(out, "line").get("d"), "M 100 400 L 300 400")
        self.assertEqual(_find(out, "num").text, "68.0%")

    def test_count_clears_children_because_that_is_what_textcontent_does(self):
        """运行期写的是 `e.textContent = …`：它连带清掉 <tspan>（和它们的尾文本）。

        只 set el.text 会把原文留在树上，接续页上就多出一截从没演过的字。
        """
        svg = STAGE_SVG.replace('<text id="num" x="200" y="900">0</text>',
                                '<text id="num" x="200" y="900">0<tspan font-size="12">个月'
                                "</tspan>旧字</text>")
        out, _ = _bake([{"target": "#num", "count": {"to": 68}}], markup=svg)
        num = _find(out, "num")
        self.assertEqual(num.text, "68")
        self.assertEqual(list(num), [])

    def test_type_is_not_baked_because_it_ends_where_it_started(self):
        """打字机的结束态 = 整段原文本还在原地：不搬，也不该搬。"""
        out, _ = _bake([{"target": "#num", "type": {}}])
        self.assertEqual(_find(out, "num").text, "0")

    def test_element_translate_is_carried_as_outer_translate(self):
        """x/y 是纯平移：折成元素 transform 的外层平移，与运行期演到的位置一字不差。"""
        out, notes = _bake([{"target": "#plate", "to": {"opacity": 1, "x": 40, "y": -15}}])
        plate = _find(out, "plate")
        self.assertEqual(plate.get("transform"), "translate(40,-15)")
        self.assertIn("opacity:1", plate.get("style"))
        self.assertEqual(notes, [])          # 平移不再属于"搬不动"那一类

    def test_carried_translate_is_a_delta_not_the_absolute_value(self):
        """作者自己写了 translate(10,20)：终值 100 只补差 90，基值留给本段自己的补间。"""
        svg = STAGE_SVG.replace('<rect id="plate" ',
                                '<rect id="plate" transform="translate(10,20)" ')
        out, _ = _bake([{"target": "#plate", "to": {"x": 100}}], markup=svg)
        plate = _find(out, "plate")
        self.assertEqual(plate.get("transform"), "translate(90,0) translate(10,20)")
        self.assertEqual(decompose_transform(plate)[:2], (100.0, 20.0))

    def test_relative_and_absolute_translate_settle_in_time_order(self):
        """结算跟时刻走：+=30 先演、绝对 100 后演，落点必须是 100 而不是 130。"""
        out, _ = _bake([{"target": "#plate", "to": {"x": "+=30"}},
                        {"target": "#plate", "to": {"x": 100}}],
                       beats=[(1.0, 0.5), (2.0, 0.5)])
        self.assertEqual(decompose_transform(_find(out, "plate"))[:2], (100.0, 0.0))

    def test_translate_bails_when_the_baseline_is_unreadable(self):
        """元素自己带 rotate：平移基值拆不出来，宁可出声也不悄悄挪位。"""
        svg = STAGE_SVG.replace('<rect id="plate" ',
                                '<rect id="plate" transform="rotate(30)" ')
        out, notes = _bake([{"target": "#plate", "to": {"x": 40}}], markup=svg)
        self.assertEqual(_find(out, "plate").get("transform"), "rotate(30)")
        self.assertTrue(any("x" in n and "transform" in n for n in notes), notes)

    def test_element_transform_is_skipped_and_said_out_loud(self):
        """跳过 + 出声，而不是静默挪位：见 _stage_carry 模块头那条"搬运的边界"。"""
        out, notes = _bake([{"target": "#plate", "to": {"opacity": 1, "scale": 2}}])
        plate = _find(out, "plate")
        self.assertIn("opacity:1", plate.get("style"))
        self.assertNotIn("transform", plate.attrib)
        self.assertTrue(any("transform" in n for n in notes), notes)

    def test_attribute_values_are_escaped_by_the_serializer(self):
        """烘焙新写了一条 f-string 之外的注入通道：值来自校验过的标量，但转义必须由
        序列化器兜住，不能靠"作者不会写尖括号"。"""
        out, _ = _bake([{"target": "#plate", "set": {"attr": {"data-x": '"><script>'}}}])
        self.assertNotIn("<script>", out)
        self.assertIn(_find(out, "plate").get("data-x"), ['"><script>'])

    def test_auto_alpha_expands_like_gsap_does(self):
        out, _ = _bake([{"target": "#plate", "to": {"autoAlpha": 0}}])
        style = _find(out, "plate").get("style")
        self.assertIn("opacity:0", style)
        self.assertIn("visibility:hidden", style)

    def test_auto_alpha_relative_amount_starts_from_the_computed_base(self):
        """autoAlpha 的相对量也按计算值起算，与 opacity 那条同一口径。

        证伪：基值原先无条件按 1.0 起算，于是 opacity="0.2" 的元素上
        `autoAlpha:"+=0.5"` 烘焙出 1、`opacity:"+=0.5"` 却烘焙出 0.7（实测）——同一
        元素的两条等价写法烘焙出两个值，接续页就在页界静默跳一下，正是本模块要消灭
        的那个阶跃。
        """
        svg = ('<svg xmlns="%s" viewBox="0 0 1080 1440">'
               '<rect id="plate" x="60" y="300" width="960" height="500" opacity="0.2"/>'
               "</svg>") % NS
        for prop in ("autoAlpha", "opacity"):
            with self.subTest(prop=prop):
                out, _ = _bake([{"target": "#plate", "to": {prop: "+=0.5"}}],
                               markup=svg)
                self.assertIn("opacity:0.7", _find(out, "plate").get("style"))
        # autoAlpha 还要顺带把 visibility 带上：0.7 > 0，所以是 visible。
        out, _ = _bake([{"target": "#plate", "to": {"autoAlpha": "+=0.5"}}], markup=svg)
        self.assertIn("visibility:visible", _find(out, "plate").get("style"))

    def test_relative_css_value_on_virgin_element_uses_css_initial_value(self):
        """virgin 元素（全身读不到基值）按 **CSS 规范初值**起算，不是 0。

        证伪：opacity 的 CSS 初值是 1。原先 resolve_num 的 None 兜底按 0 起算，
        于是 virgin 元素上 `opacity:"+=0.5"` 烘焙出 0.5，而运行期浏览器按计算值
        1+0.5 演到 1.5 被 CSSOM 钳到 1——接续页从"比上一段更暗的半透明"静默跳成
        "完全不透明"，正是本模块要消灭的页界阶跃。
        """
        svg = ('<svg xmlns="%s" viewBox="0 0 1080 1440">'
               '<rect id="plate" x="60" y="300" width="960" height="500"/>'
               "</svg>") % NS
        out, _ = _bake([{"target": "#plate", "to": {"opacity": "+=0.5"}}], markup=svg)
        self.assertIn("opacity:1.5", _find(out, "plate").get("style"))
        # 初值为 0 的属性（fill-opacity 之外的 fill 等）不在表里，仍按 0 起算。
        out, _ = _bake([{"target": "#plate", "to": {"fill-opacity": "+=0.5"}}],
                       markup=svg)
        self.assertIn("fill-opacity:1.5", _find(out, "plate").get("style"))


class FoldCamera(unittest.TestCase):
    def test_resting_pose_becomes_one_static_layer(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": "*=1.2", "y": "+=100"}}])
        cam = _find(out, "cam")
        stage = list(cam)[0]
        self.assertEqual(stage.tag, f"{{{NS}}}g")
        self.assertIn("translate(", stage.get("transform"))
        self.assertIn("scale(1.200000)", stage.get("transform"))
        self.assertEqual([c.get("id") for c in list(stage)], ["plate", "line", "num"])
        self.assertTrue(any("相机收尾位" in n for n in notes), notes)

    def test_identity_pose_adds_no_layer(self):
        out, notes = _bake([{"target": "#cam", "to": {"opacity": 1}}])
        self.assertEqual(len(list(_find(out, "cam"))), 3)   # 没有多包一层
        self.assertEqual(notes, [])

    def test_chain_composes_layer_by_layer(self):
        """每一环各包一层（深度 = 链长），`_cam_crop` 沿树把各层乘起来。

        所以链式 A→B→C 里 C 拿到的是 A+B 的叠加，不需要"把两段平移合并成一个数"
        那种会把 rotate/matrix 读成 None 的写法。
        """
        one, _ = _bake([{"target": "#cam", "to": {"y": 200}}])
        two, _ = SC.bake_settled_state(one, [{"target": "#cam", "to": {"y": 300}}])
        outer, inner = _find(two, "cam")[0], _find(two, "cam")[0][0]
        self.assertIn("translate(0.0000,300.0000)", outer.get("transform"))
        self.assertIn("translate(0.0000,200.0000)", inner.get("transform"))
        num_box = [b[1] for b in cam_geometry(ET.fromstring(two))[2] if b[0] == "#num"][0]
        # 883（旧）→ 874：`<text id="num">` 没写 font-size，占位盒的兜底字号从
        # _cam_crop 自己那个字面量 17.0 换成 _template.svg_text_band 的
        # MIN_READABLE_FONT_PX（26）——与 check_svg 的密度/越界自查同一口径。
        # 两层平移 200+300 都吃进去了：900 − 26 + 500。
        self.assertAlmostEqual(num_box[2], 874.0 + 500.0, places=3)

    def test_baked_frame_is_what_crop_warnings_sees(self):
        """同一拍运镜，接在推近过的画面之后就出画了——只看盘上原图会报"一切在幅内"。"""
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        carried, _ = _bake([{"target": "#cam", "to": {"y": 200}}])
        own = [{"target": "#cam", "to": {"y": 400}}]
        self.assertEqual(crop_warnings(raw, own), [])
        warns = crop_warnings(carried, own)
        self.assertTrue(any("#num" in w and "下边" in w for w in warns), warns)


class CamStepNonPoseKeys(unittest.TestCase):
    """相机步（target="#cam"）上**非姿态**的那几键也要搬进接续页。

    证伪：`_settle_step` 早先对 `#cam` 整条 early-return（注释写"相机走
    _fold_camera，不进元素通道"）。可运行期 GSAP 是把 to/set 里的每条属性写到
    `#cam` 这个元素上的——整台相机一起淡出（`opacity`）、改个 attr 就是这么写的。
    那些键于是被静默丢掉：接续页从全亮的画面起跑，作者却"确实演了"。
    """

    def test_opacity_and_attr_on_camera_step_are_carried(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2, "opacity": 0.4,
                                                      "attr": {"data-role": "dim"}}}])
        cam = _find(out, "cam")
        self.assertIn("opacity:0.4", cam.get("style"))
        self.assertEqual(cam.get("data-role"), "dim")
        # 只有折层那一条说明，不得出现"搬不动"（这两键过去是被静默丢掉的）
        self.assertEqual([n for n in notes if "搬不动" in n], [], notes)

    def test_pose_keys_still_go_to_the_fold_not_the_style(self):
        """姿态键（scale/x/y + 绕点）仍由 _fold_camera 折进子层，不落到 #cam 上。

        折在子层是刻意的（见模块头）：折到 #cam 自身，这层推近对本段的原点计算
        就是隐形的，链越长飘得越远。
        """
        out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2, "y": 200}}])
        cam = _find(out, "cam")
        self.assertIsNone(cam.get("style"), "姿态键不该被写成 #cam 的 style")
        self.assertIn("scale(1.200000)", cam[0].get("transform"))
        self.assertTrue(any("相机收尾位" in n for n in notes), notes)

    def test_camera_step_transform_keys_still_reported_not_silently_dropped(self):
        """写不了的那几键仍要出声：rotation 要以元素自身 bbox 为原点折算，
        相机折层只认 scale/x/y，搬不了就得说。"""
        _out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2,
                                                       "rotation": 30}}])
        self.assertTrue(any("rotation" in n for n in notes), notes)


class CameraFoldRefusals(unittest.TestCase):
    """折不折得动都必须**出声**：折层是静默搬运，悄悄少折一层就是下一页整体错位。

    两种折不动：① 姿态里有静态几何读不出的分量（`scale:"/=0"` → 运行期
    Infinity）；② 绕的那一点在动这棵树之前就量不出来（子树里有读不懂的
    transform）。后者早先会退而"按当前树现算"——那棵树已经被结算改过，量出
    的点与渲染器当时注入的 svgOrigin 不是同一个，折进去正是这次要消灭的错位。
    """

    # 子树里有 rotate：_walk 读不懂就整棵跳过 → 占位盒算不出 → 绕点量不出
    _UNREADABLE = STAGE_SVG.replace(
        '<g id="cam">', '<g id="cam"><g transform="rotate(30 500 500)">'
    ).replace("</g></svg>", "</g></g></svg>")

    def _folded(self, markup):
        return any(el.get(SC.STAGE_ATTR) for el in _find(markup, "cam").iter())

    def test_unresolvable_pose_is_not_folded_and_says_so(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": "/=0"}}])
        self.assertFalse(self._folded(out), "不该折（手上那不是收尾位）")
        self.assertTrue(any("读不出" in n for n in notes), notes)

    def test_unmeasurable_origin_is_not_folded_and_says_so(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2}}],
                           markup=self._UNREADABLE)
        self.assertFalse(self._folded(out), "量不出绕点就不该折")
        self.assertTrue(any("量不出" in n for n in notes), notes)

    def test_measurable_camera_still_folds_with_one_note(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2}}])
        self.assertTrue(self._folded(out), "该折的那一层没折")
        e, f, s = _stage_pose(out)
        self.assertEqual(notes, ["相机收尾位已折进本层（scale={:g}、平移=({:g}, {:g})）"
                                 .format(s, e, f)])


class RepeatAndYoyoCarry(unittest.TestCase):
    """`repeat` / `yoyo` 的收尾态：接续烘焙按"最后真的停在哪一头"搬。"""

    def test_yoyo_even_cycles_is_not_carried(self):
        # 呼吸一拍：偶数遍收在起点，下一页接手时它本来就是那个样子，没什么可搬
        out, notes = _bake([{"target": "#plate", "to": {"opacity": 1},
                             "repeat": 1, "yoyo": True}])
        self.assertNotIn("style", _find(out, "plate").attrib)
        self.assertEqual(notes, [])

    def test_yoyo_odd_cycles_carries_the_target(self):
        out, _ = _bake([{"target": "#plate", "to": {"opacity": 1},
                         "repeat": 2, "yoyo": True}])
        self.assertIn("opacity:1", _find(out, "plate").get("style"))

    def test_repeat_without_yoyo_carries_the_target(self):
        out, _ = _bake([{"target": "#plate", "to": {"opacity": 1}, "repeat": 5}])
        self.assertIn("opacity:1", _find(out, "plate").get("style"))

    def test_infinite_repeat_is_skipped_and_named(self):
        """永远演不完就没有"最后停在哪儿"：跳过必须出声，静默少搬一样查不出来。"""
        out, notes = _bake([{"target": "#plate", "to": {"opacity": 1}, "repeat": -1}])
        self.assertNotIn("style", _find(out, "plate").attrib)
        self.assertTrue(any("repeat:-1" in n and "#plate" in n for n in notes), notes)

    def test_infinite_camera_step_blocks_the_whole_fold(self):
        """一条永不停下的相机步就让整台相机的收尾位不作数：宁可不折，也不折编出来的姿态。"""
        steps = [{"target": "#cam", "to": {"scale": "*=1.2"}},
                 {"target": "#cam", "to": {"y": "+=80"}, "repeat": -1, "yoyo": True}]
        out, notes = _bake(steps)
        self.assertNotIn("data-ctv-stage", out)
        self.assertTrue(any("repeat:-1" in n and "#cam" in n for n in notes), notes)

    def test_non_cam_infinite_loop_no_longer_claims_camera_skips(self):
        """实测翻车回归：repeat:-1 打在非相机元素上，相机照常折，告警不得自相矛盾。

        demo 里 #dot1 无限循环 + #cam 收尾推近，旧文案同时打印"相机…一律不折"和
        "相机收尾位已折进本层"——_fold_camera 只认 target=="#cam" 的步，非相机
        无限循环根本不进它的结算，旧那句是假话。
        """
        steps = [{"target": "#plate", "to": {"opacity": 0.5}, "duration": 0.6,
                  "repeat": -1},
                 {"target": "#cam", "to": {"scale": 1.2, "x": -20}, "duration": 1.0}]
        out, notes = _bake(steps)
        # 相机照常折了：data-ctv-stage 层在，且只有元素终态不搬
        self.assertIn('data-ctv-stage="1"', out)
        joined = " ".join(notes)
        self.assertIn("#plate", joined)
        self.assertNotIn("一律不折", joined)
        self.assertNotIn("相机只要有一条这样的步", joined)
        # 相机步单独出声的那条只会在相机自己无限循环时出现——这里没有
        self.assertFalse(any("相机永不停下" in n for n in notes), notes)

    def test_cam_infinite_loop_note_says_camera_not_folded(self):
        """相机步无限循环：明确出声"相机不折"（旧版这层静默，作者以为镜头会带进下一页）。"""
        out, notes = _bake([{"target": "#cam", "to": {"scale": 1.2},
                             "duration": 0.6, "repeat": -1}])
        self.assertNotIn("data-ctv-stage", out)
        self.assertTrue(any("相机永不停下" in n and "repeat:-1" in n for n in notes), notes)

    def test_camera_that_yoys_back_is_not_folded(self):
        out, notes = _bake([{"target": "#cam", "to": {"scale": "*=1.2", "y": "+=80"},
                             "repeat": 1, "yoyo": True}])
        self.assertNotIn("data-ctv-stage", out)
        self.assertEqual(notes, [])


def _stage_pose(markup):
    """读出折进来的那层 `translate(e,f) scale(s)` → (e, f, s)。"""
    m = re.search(r"translate\(([-\d.]+),([-\d.]+)\) scale\(([-\d.]+)\)", markup)
    if not m:
        raise AssertionError("没有折进相机层：" + markup[:400])
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


class OriginSharedWithRenderer(unittest.TestCase):
    """烘焙用的原点必须就是渲染器钉给 GSAP 的那一个。

    钉之前两边各算各的（GSAP 实际绕 bbox 左上角、还跟着别的步的 from 瞬态飘），
    三段链真渲染量到页界上 24~36px 的纯平移阶跃。见 `_cam_crop.cam_default_origin`。
    """

    def test_fold_uses_the_origin_the_renderer_pins(self):
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        steps = [{"target": "#plate", "to": {"x": 500}},       # 结算会把内容并集挪走
                 {"target": "#cam", "to": {"scale": 1.2}}]
        cx, cy = [float(v) for v in CC.cam_default_origin(raw, steps).split()]
        out, _ = SC.bake_settled_state(raw, steps)
        e, f, s = _stage_pose(out)
        self.assertAlmostEqual(e, (1 - s) * cx, places=3)
        self.assertAlmostEqual(f, (1 - s) * cy, places=3)

    def test_the_origin_is_measured_before_the_settles_move_content(self):
        """这条不是形式检查：结算后那棵树的内容中心**确实**飘了。"""
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        steps = [{"target": "#plate", "to": {"x": 500}},
                 {"target": "#cam", "to": {"scale": 1.2}}]
        cx, _ = [float(v) for v in CC.cam_default_origin(raw, steps).split()]
        out, _ = SC.bake_settled_state(raw, steps)
        self.assertNotAlmostEqual(cx, cam_content_center(ET.fromstring(out))[0], places=0)

    def test_explicit_svgOrigin_wins_and_nothing_is_invented(self):
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        steps = [{"target": "#cam", "to": {"scale": 1.2, "svgOrigin": "540 660"}}]
        self.assertIsNone(CC.cam_default_origin(raw, steps))
        e, f, s = _stage_pose(SC.bake_settled_state(raw, steps)[0])
        self.assertAlmostEqual(e, (1 - s) * 540, places=3)
        self.assertAlmostEqual(f, (1 - s) * 660, places=3)

    def test_pan_only_camera_needs_no_origin(self):
        """只有平移时原点不参与：钉它纯属多余，也更吵。"""
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        self.assertIsNone(CC.cam_default_origin(raw, [{"target": "#cam", "to": {"y": 120}}]))

    def test_page_without_a_camera_gets_no_pin(self):
        raw, _ = SAN.sanitize_svg_for_inline(STAGE_SVG)
        self.assertIsNone(CC.cam_default_origin(raw, [{"target": "#plate", "to": {"x": 5}}]))


def _two_page_stage(tmp, svg=STAGE_SVG, steps_a=None, steps_b=None, src_b=None):
    """写一份"两页同一张图"的 images.json，跑 animate_prepare，返回 (images, errs, warns)。

    两页都是满幅版式（canvas）：场景跨页接续只在版面归作者的页上有意义。接续不用
    写任何字段——同图 + 上一页有编排就成立（见 `_segments.is_next_page_continuing`）。
    """
    rel = _write_svg(tmp, "stage", svg)
    images = {
        "seg-a": {"src": rel, "animate": {"steps": steps_a or [
            {"at": 0, "target": "#plate", "to": {"opacity": 1}}]}},
        "seg-b": {"src": src_b or rel},
    }
    if steps_b:
        images["seg-b"]["animate"] = {"steps": steps_b}
    segs = [{"id": "seg-a", "layout": "canvas",
             "sentences": [{"index": 0, "start_time": 0.0, "duration": 2.0}]},
            {"id": "seg-b", "layout": "canvas",
             "sentences": [{"index": 1, "start_time": 2.4, "duration": 2.0}]}]
    errs, warns = G.animate_prepare(images, segs, tmp)
    return images, errs, warns


class PrepareCarriesState(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_next_page_starts_from_previous_page_end_state(self):
        images, errs, _ = _two_page_stage(self.tmp.name)
        self.assertEqual(errs, [])
        carried = images["seg-b"]["inline_svg"]
        self.assertIn("opacity:1", _find(carried, "plate").get("style"))
        # 本页自己文件里那句 opacity="0" 不再说话（attribute 还在，但 style 压过它）
        self.assertEqual(_find(carried, "plate").get("opacity"), "0")

    def test_page_without_its_own_steps_still_gets_a_carried_canvas(self):
        """"上一段演完、这一段就停在那幅画上说话"是合法演法。"""
        images, errs, _ = _two_page_stage(self.tmp.name, steps_b=None)
        self.assertEqual(errs, [])
        self.assertIn("inline_svg", images["seg-b"])

    def test_carried_page_crop_check_uses_the_carried_frame(self):
        _, errs, warns = _two_page_stage(
            self.tmp.name,
            steps_a=[{"at": 0, "target": "#cam", "to": {"y": 200}}],
            steps_b=[{"at": 0, "target": "#cam", "to": {"y": 400}}])
        self.assertEqual(errs, [])
        crop = [w for w in warns if "运镜收尾位" in w]
        # 这一拍单独看不越界（见 FoldCamera 那条同名单元测试），只在接续帧上才越界
        self.assertEqual(len(crop), 1, warns)
        self.assertIn("段落 'seg-b'", crop[0])
        self.assertIn("#num", crop[0])

    def test_different_src_is_not_carried(self):
        """换开 src = 各画一份 = 独立成片，不是错误（接续判据自动不成立）。"""
        other = _write_svg(self.tmp.name, "other", STAGE_SVG)
        images, errs, _ = _two_page_stage(self.tmp.name, src_b=other)
        self.assertEqual(errs, [])
        self.assertEqual(images["seg-b"].get("inline_svg"), None)
        self.assertNotEqual(images["seg-a"]["inline_svg"], None)

    def test_previous_page_without_animate_is_not_carried(self):
        """上一页是静图（没写 animate）：没有可搬的元素态，本页照常从原图起跑。"""
        rel = _write_svg(self.tmp.name, "stage", STAGE_SVG)
        images = {"seg-a": {"src": rel}, "seg-b": {"src": rel}}
        segs = [{"id": "seg-a", "sentences": [{"index": 0, "start_time": 0.0}]},
                {"id": "seg-b", "sentences": [{"index": 1, "start_time": 2.4}]}]
        errs, _ = G.animate_prepare(images, segs, self.tmp.name)
        self.assertEqual(errs, [])
        self.assertIsNone(images["seg-b"].get("inline_svg"))


class SharedRefGate(unittest.TestCase):
    """接续页给"两份副本共享的元素"写补间 = 那一拍不生效，按 error 拦下来。

    同一张 SVG 内联两份后图内 id 重复，而图里的 url(#id) / href="#id" 按文档序只认第一份
    （= 上一页那一幅）：本页的补间打在自己那份渐变上，本页的图形一个都不读它。以前这只
    写成模块头的一句作者须知——实测缺陷不当须知卖，所以它是门禁。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _errs(self, steps_b):
        _, errs, _ = _two_page_stage(self.tmp.name, svg=DEFS_SVG, steps_b=steps_b)
        return [e for e in errs if "共享的元素" in e]

    def test_gradient_inside_defs_is_an_error(self):
        errs = self._errs([{"at": 0, "target": "#warm", "to": {"opacity": 0}}])
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("steps[0]", errs[0])
        self.assertIn("#warm", errs[0])

    def test_id_referenced_by_use_is_an_error_too(self):
        self.assertEqual(len(self._errs([{"at": 0, "target": "#icon",
                                          "to": {"opacity": 0}}])), 1)

    def test_ordinary_content_is_not_flagged(self):
        self.assertEqual(self._errs([{"at": 0, "target": "#plate",
                                      "to": {"opacity": 1}}]), [])

    def test_previous_page_may_animate_its_own_defs(self):
        """只有接续页会中：前页是文档里的第一份，图里的引用读的就是它。"""
        _, errs, _ = _two_page_stage(self.tmp.name, svg=DEFS_SVG, steps_a=[
            {"at": 0, "target": "#warm", "to": {"opacity": 0}}])
        self.assertEqual(errs, [])


class StageKeyIsGone(unittest.TestCase):
    """`stage` 已删除：同图 + 上一页有编排就是接续，没有选项可写。

    删它的理由不是"少一个字段"，是它曾经是全场唯一"字面没写就一定是错的"的键。
    留着它当"可选开关"，作者就得先知道它存在、再判断要不要写——而正确的默认行为
    完全由稿子结构决定。现在写了它只会得到一条指路的提示，不是一个静默的选项。
    """

    def test_stage_is_not_an_entry_key(self):
        self.assertNotIn("stage", MEDIA_ENTRY_KEYS)

    def test_writing_stage_gets_a_hint_not_a_shrug(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            validate_images_json({"seg-a": {"src": "images/a.svg", "stage": "keep"}})
        msg = buf.getvalue()
        self.assertIn("已删除", msg)
        self.assertIn("同一张 src", msg)
        self.assertIn("src 换开", msg)


class CarriedPageIsNotReintroduced(unittest.TestCase):
    """接续页不再"当新的一页演一遍"：不擦进来，配图也不再淡入上移。

    烘焙只保证**画面**连续；只要这一页还带 0.40s 擦除 + 配图 40px 滑入，页界处
    就照样看得见回弹——而那正是跨段接续存在的理由。所以这两处听同一面旗。
    """

    def _html(self, carry):
        from html_renderer import generate_html
        m = H.make_manifest()
        images = H.sample_images(m)
        # 造一对相邻的接续页：样稿的 seg-b / closing 都改成整页画布（画面主导的页
        # 才谈得上"同一幅画跨页继续演"）。seg-b 挂编排，closing 用它那张时就是
        # 接续页，用自己那张就是独立成片。
        for seg in m["segments"]:
            if seg["id"] in ("seg-b", "closing"):
                seg["layout"] = "canvas"
        own = images["seg-b"]["src"]
        images["seg-b"]["animate"] = {"steps": [{"at": 0, "target": "#x",
                                                "to": {"opacity": 1}}]}
        images["closing"] = {"src": own if carry else "images/closing.svg"}
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav", images=images,
                                 aspect="portrait", theme="dark")

    def test_keep_page_hard_cuts_and_its_picture_is_already_in_place(self):
        html = self._html(True)
        # 挂载即音频起点（7.20 = 首句 start_time），擦除时长取 0.01（瞬间切，
        # 见 html_renderer._card_timeline_lines 的注释：0 时长会被 GSAP 视作
        # set() 而让预览首帧提前显形）
        self.assertIn('id="closing" class="clip seg-card seg-canvas" data-start="12.00"',
                      html)
        self.assertIn('tl.fromTo("#closing",{clipPath:"inset(100% 0 0 0)"},'
                      '{clipPath:"inset(0% 0 0 0)",duration:0.01,'
                      'ease:"power2.inOut"},12.00)', html)
        # 引导线整条不生成（DOM 与补间都没有），配图卡自己的滑入也撤掉
        self.assertNotIn('id="line-closing"', html)
        self.assertNotIn('tl.from("#img-closing"', html)
        # 进度条照常：它是这一段的时长，不是"新页感"
        self.assertIn('tl.to("#prog-closing",{width:"100%"', html)

    def test_without_the_carry_the_page_is_its_own_picture(self):
        """换开 src = 独立成片：这一页用自己那张图，不被当成上一页的续页。"""
        html = self._html(False)
        self.assertIn("images/closing.svg", html)


class CarryPagesIsStructural(unittest.TestCase):
    """接续页由结构决定，没有开关（`_segments.is_next_page_continuing`）。

    锁三件事：
    1. **同图 + 上一页有 animate** 就接续——这是唯一的成因，不需要作者写任何字段；
    2. 换开 `src` / 上一页没编排 → 不接续（反悔方式是改稿，不是关开关）；
    3. 生成期（`_stage_carry_pass`）与渲染端（`_is_keep_page`）看到同一个答案——
       两边各判一次的话，就会出现"烘了却照样擦进来"。
    """

    SAME = "images/stage.svg"

    def _segs(self, *, src_b=None, animate_a=True, n=2):
        a = {"id": "seg-a", "src": self.SAME}
        if animate_a:
            a["animate"] = {"steps": [{"at": 0, "target": "#plate",
                                       "to": {"opacity": 1}}]}
        b = {"id": "seg-b", "src": src_b or self.SAME}
        return [a, b][:n]

    def _carry(self, **kw):
        return SC.carry_pages({"seg-a": {}, "seg-b": {}}, self._segs(**kw))

    def test_same_picture_after_an_animated_page_is_carried(self):
        self.assertEqual(self._carry(), {"seg-b": "seg-a"})

    def test_camera_pose_is_irrelevant(self):
        """判据不读相机姿态：同图 + 有编排就接续，回原位也一样（画面本来就一样）。"""
        segs = self._segs()
        segs[0]["animate"] = {"steps": [{"at": 0, "target": "#plate",
                                         "to": {"opacity": 1}}]}
        self.assertEqual(SC.carry_pages({"seg-a": {}, "seg-b": {}}, segs),
                         {"seg-b": "seg-a"})

    def test_different_src_is_not_carried(self):
        self.assertEqual(self._carry(src_b="images/other.svg"), {})

    def test_previous_page_without_animate_is_not_carried(self):
        self.assertEqual(self._carry(animate_a=False), {})

    def test_first_page_never_carries_and_last_page_never_carries(self):
        self.assertEqual(self._carry(n=1), {})

    def test_page_without_a_media_entry_is_not_carried(self):
        self.assertEqual(SC.carry_pages({"seg-a": {}}, self._segs()), {})


class CarryPagesMatchesRenderer(unittest.TestCase):
    """生成期认的接续页，渲染端必须**也**认：否则烘了却照样擦进来。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_carried_page_hard_cuts_with_no_field_written(self):
        from html_renderer import generate_html
        _write_svg(self.tmp.name, "stage", STAGE_SVG)
        # 句子必须带 text、段必须带 title：渲染端 `_verse_html` / `title_wrap`
        # 直接按这两个键取值，只给骨架会在这里 KeyError
        segs = [{"id": "seg-a", "title": "甲", "layout": "canvas",
                 "sentences": [{"index": 0, "start_time": 0.0,
                                "duration": 2.0, "text": "第一句。"}]},
                {"id": "seg-b", "title": "乙", "layout": "canvas",
                 "sentences": [{"index": 1, "start_time": 2.4,
                                "duration": 2.0, "text": "第二句。"}]}]
        images = {
            "seg-a": {"src": "images/stage.svg", "inline_svg": "<svg/>",
                      "animate": {"steps": [
                          {"at": 0, "target": "#cam", "to": {"scale": 1.4}}]}},
            "seg-b": {"src": "images/stage.svg"},
        }
        m = {
            "total_duration": 4.8,
            "sentences": [
                {"index": 0, "start_time": 0.0, "duration": 2.0, "text": "第一句。",
                 "seg": "seg-a"},
                {"index": 1, "start_time": 2.4, "duration": 2.0, "text": "第二句。",
                 "seg": "seg-b"},
            ],
            "segments": segs,
        }
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav", images=images)
        self.assertNotIn('tl.from("#img-seg-b"', html)


class TargetsResolveQuotedIds(unittest.TestCase):
    """`#id` 选择器不许拼 XPath 谓词：SVG 的 id 是自由文本，可以合法含引号。"""

    def test_id_with_single_quote_resolves_instead_of_crashing(self):
        markup = ('<svg xmlns="%s"><g id="cam">'
                  '<rect id="a\'b" x="0" y="0" width="10" height="10"/>'
                  '</g></svg>' % NS)
        root = ET.fromstring(markup)
        # 拼成 [@id='a'b] 时谓词被那个引号截断 → SyntaxError（调用方只接
        # ParseError/ValueError，于是整次生成裸栈中断，报错里看不出是 SVG 里的
        # 一个引号）。
        self.assertRaises(SyntaxError, root.find, ".//*[@id='%s']" % "a'b")
        self.assertEqual([el.get("id") for el in SC._targets(root, "#a'b")], ["a'b"])

    def test_bake_survives_quoted_id(self):
        steps = [{"at": 0, "target": "#a'b", "to": {"opacity": 1}}]
        out, _notes = SC.bake_settled_state(markup=(
            '<svg xmlns="%s"><g id="cam">'
            '<rect id="a\'b" x="0" y="0" width="10" height="10" opacity="0"/>'
            '</g></svg>' % NS), steps=steps)
        self.assertIn("opacity:1", out)


if __name__ == "__main__":
    unittest.main()
