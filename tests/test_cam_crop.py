"""`_cam_crop`：运镜收尾位把内容推出画幅的自查（真实踩坑的回归锁）。

口径都写在 `_cam_crop` 模块头，这里逐条锁住：只查收尾位、`from` 是瞬态、缺省原点用
`#cam` 内容并集中心（渲染器钉给 GSAP 的那一点，不是 viewBox 中心）、rotate/matrix 子树跳过、
满幅底板豁免、文字只查上下切边。
"""
import unittest

import _helpers as H  # noqa: F401  sys.path 装配
from _cam_crop import cam_resting_pose, crop_warnings

_ROOT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">')
_PUSH = [{"target": "#cam", "at_time": 2.0, "to": {"scale": 1.16, "svgOrigin": "540 660"}}]


def _svg(body, root=_ROOT):
    return root + body + "</svg>"


class RestingPose(unittest.TestCase):
    def test_no_cam_step_means_nothing_to_check(self):
        self.assertIsNone(cam_resting_pose([{"target": "#node", "to": {"opacity": 1}}]))
        self.assertEqual(crop_warnings(_svg('<g id="cam"><rect y="1362" width="9" height="9"/></g>'),
                                       [{"target": "#node", "to": {"opacity": 1}}]), [])

    def test_to_and_set_are_absolute_last_one_wins(self):
        pose = cam_resting_pose([{"target": "#cam", "to": {"scale": 1.4}},
                                 {"target": "#cam", "to": {"scale": 1.0, "x": 12}}])
        self.assertEqual((pose["scale"], pose["x"]), (1.0, 12.0))

    def test_from_is_a_transient_start_state_not_the_resting_pose(self):
        # 入场滑动的写法：GSAP 从这些值动回原值，静止位 = 补间前，不是画外那个值
        pose = cam_resting_pose([{"target": "#cam", "from": {"scale": 0.4, "x": -900}}])
        self.assertEqual((pose["scale"], pose["x"], pose["y"]), (1.0, 0.0, 0.0))

    def test_scale_is_absolute_not_multiplied_across_steps(self):
        pose = cam_resting_pose([{"target": "#cam", "to": {"scale": 1.2}},
                                 {"target": "#cam", "set": {"scale": 0.9}}])
        self.assertEqual(pose["scale"], 0.9)


class Crop(unittest.TestCase):
    def _band_in_cam(self):
        return _svg('<g id="cam">'
                    '<rect id="panel" x="60" y="300" width="960" height="700"/>'
                    '<rect id="bottomband" x="80" y="1362" width="920" height="60"/>'
                    '</g>')

    def test_catches_the_bug_we_actually_hit(self):
        """scale:1.16 / svgOrigin:540 660 把 y=1362 推到 ≈1477（>1440）：底栏整条消失。"""
        warns = crop_warnings(self._band_in_cam(), _PUSH)
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("#bottomband", warns[0])
        self.assertIn("下边", warns[0])
        self.assertNotIn("#panel", warns[0])   # 中景仍在幅内，不该被牵连

    def test_same_camera_is_fine_once_the_band_lives_outside_cam(self):
        """这就是 demo 修法本身：相机数字一字不改，把文字带挪出 #cam 就不该报。"""
        body = ('<g id="cam"><rect id="panel" x="60" y="300" width="960" height="700"/></g>'
                '<rect id="bottomband" x="80" y="1362" width="920" height="60"/>')
        self.assertEqual(crop_warnings(_svg(body), _PUSH), [])

    def test_horizontal_push_is_caught_too(self):
        warns = crop_warnings(self._band_in_cam(),
                              [{"target": "#cam", "to": {"x": 1400}}])
        self.assertTrue(any("右边" in w and "#bottomband" in w for w in warns), warns)

    def test_mild_camera_reports_nothing(self):
        self.assertEqual(crop_warnings(self._band_in_cam(),
                                       [{"target": "#cam", "to": {"scale": 1.02,
                                                                  "svgOrigin": "540 660"}}]), [])

    def test_fallback_origin_is_the_point_the_renderer_pins(self):
        """作者没写 svgOrigin 时，这里的缺省原点是**渲染器钉进去的那一点**（`#cam` 内容
        并集中心，见 `_cam_crop.cam_default_origin`），不是 viewBox 中心。

        注意这条**不是**在描述 GSAP 的缺省行为——实测 GSAP 绕的是 `#cam` bbox 的左上角，
        而且那个角还跟着别的步的 `from` 瞬态飘，所以拿它当"缺省"是靠不住的；正因为如此
        渲染期才要显式写 svgOrigin，两边才共用同一个点。

        两条都放大 1.9×，但原点不同 → 被推出去的是**不同**的元素。要是这里退化成
        "没有 svgOrigin 就当 (0,0)"，第一组会一条不报（顶部元素留在幅内）。
        """
        body = _svg('<g id="cam">'
                    '<rect id="top" x="400" y="100" width="200" height="60"/>'
                    '<rect id="bot" x="400" y="1300" width="200" height="60"/></g>')
        no_origin = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.9}}])
        self.assertIn("#top", " ".join(no_origin))
        pinned = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.9,
                                                               "svgOrigin": "540 0"}}])
        labels = " ".join(pinned)
        self.assertIn("#bot", labels)
        self.assertNotIn("#top", labels)

    def test_nested_translate_composes_before_projecting(self):
        body = _svg('<g id="cam"><g id="shift" transform="translate(0,120)">'
                    '<path id="curve" d="M 40 1330 L 900 1330"/></g></g>')
        warns = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.16,
                                                              "svgOrigin": "540 660"}}])
        self.assertTrue(any("#curve" in w for w in warns), warns)

    def test_rotate_subtree_is_skipped_not_guessed(self):
        """读不准就不报：这条是**故意的漏**，换来的是其余各条可信。"""
        body = _svg('<g id="cam"><g transform="rotate(24 540 720)">'
                    '<rect id="band" x="80" y="1362" width="920" height="60"/></g></g>')
        self.assertEqual(crop_warnings(body, _PUSH), [])

    def test_full_bleed_plate_is_exempt(self):
        """底板本来就该溢出画幅；同一段里非满幅的元素照报（证明豁免不是空转）。"""
        body = _svg('<g id="cam">'
                    '<rect id="plate" x="0" y="0" width="1080" height="1440"/>'
                    '<rect id="chip" x="80" y="1380" width="120" height="40"/></g>')
        warns = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.5,
                                                              "svgOrigin": "540 0"}}])
        self.assertTrue(all("#plate" not in w for w in warns), warns)
        self.assertTrue(any("#chip" in w for w in warns), warns)

    def test_text_half_cut_off_the_bottom_edge_warns(self):
        """半切比整条消失更难发现，而它不满足"完全在幅外"，所以单列一条。"""
        body = _svg('<g id="cam"><text id="cap" x="90" y="1420" font-size="60">结论</text></g>')
        warns = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.03,
                                                              "svgOrigin": "540 0"}}])
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("切在画幅下边", warns[0])

    def test_svg_origin_percent_is_frame_relative(self):
        """`svgOrigin:"50% 50%"` = viewBox 中心（540,720），不是元素自身中心。

        与下一条成对：同一个内容、同一个 scale，钉在页心会把底栏推出去，而读不懂的
        原点串退到内容并集中心（也就是渲染器给没写原点的相机钉的那一点），放大就原地不动。
        """
        body = _svg('<g id="cam"><rect id="chip" x="80" y="1380" width="120" height="40"/></g>')
        pinned = crop_warnings(body, [{"target": "#cam", "to": {"scale": 1.5,
                                                               "svgOrigin": "50% 50%"}}])
        self.assertTrue(any("#chip" in w for w in pinned), pinned)

    def test_unparsable_origin_falls_back_instead_of_guessing(self):
        body = _svg('<g id="cam"><rect id="chip" x="80" y="1380" width="120" height="40"/></g>')
        self.assertEqual(crop_warnings(body, [{"target": "#cam",
                                               "to": {"scale": 1.5, "svgOrigin": "banana"}}]), [])

    def test_unreadable_input_never_raises(self):
        for bad in ("", "<svg><rect/></svg>", "not xml at all",
                    _svg('<g id="cam"><rect id="r"/></g>',
                         root='<svg xmlns="http://www.w3.org/2000/svg" viewBox="50% 0 1080 1440">')):
            self.assertEqual(crop_warnings(bad, _PUSH), [])


if __name__ == "__main__":
    unittest.main()
