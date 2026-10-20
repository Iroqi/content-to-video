"""视觉模板的临时覆写层（CLI `--set` / `--drift`）。

覆写层只有两条契约值得锁：

1. **只改已存在的叶子**。路径拼错、整块替换、值写成裸词，三种错法当场报错——
   它们共同的后果是"以为改了、其实没改"（消费侧 KeyError 报在几层之外，或者
   非法值悄悄变成一条被浏览器丢掉的 CSS 声明）。
2. **读得到就改得到**。覆写层如果只对一部分消费者生效，就会出现"生成按新值、
   门禁按旧值"，而作者照着改的正是门禁那一份——两道门给了两个答案。
"""
import contextlib
import io
import unittest

import _helpers  # noqa: F401  —— 把 scripts/ 挂进 sys.path
import _template as T


class OverrideContract(unittest.TestCase):
    def tearDown(self):
        T._OVERRIDES.clear()

    def test_override_reaches_load_template(self):
        self.assertEqual(T.load_template()["animation"]["director"]["duration"], 0.5)
        T.put_override("animation.director.duration=0.9")
        self.assertEqual(T.load_template()["animation"]["director"]["duration"], 0.9)

    def test_unknown_path_is_named_with_the_nearest_keys(self):
        with self.assertRaises(ValueError) as cm:
            T.put_override("animation.direktor.duration=1")
        msg = str(cm.exception)
        self.assertIn("animation.direktor.duration", msg)
        self.assertIn("animation.director.duration", msg)  # 最近的候选要列出来

    def test_block_replacement_is_refused(self):
        with self.assertRaises(ValueError):
            T.put_override('animation.director={"duration":1}')
        with self.assertRaises(ValueError):
            T.put_override("animation.director.duration=null")

    def test_bare_word_is_not_a_json_scalar(self):
        with self.assertRaises(ValueError) as cm:
            T.put_override("typography.titleWeight=bold")
        self.assertIn("JSON", str(cm.exception))

    def test_layer_is_scoped(self):
        with T.override_layer(["animation.director.duration=0.7"]):
            self.assertEqual(T.load_template()["animation"]["director"]["duration"], 0.7)
        self.assertEqual(T.load_template()["animation"]["director"]["duration"], 0.5)


class ConsumersSeeTheOverride(unittest.TestCase):
    """覆写要对每个读模板的模块都生效，不能只有渲染端看得见。"""

    def tearDown(self):
        T._OVERRIDES.clear()

    def test_canvas_kit_refreshes_its_dimension_tables(self):
        import canvas_kit as CK
        before = CK.TIERS["portrait"]["title"]
        try:
            T.put_override("layout.vertical.title.fontSize=%d" % (before + 6))
            CK._apply_template()
            self.assertEqual(CK.TIERS["portrait"]["title"], before + 6)
        finally:
            T._OVERRIDES.clear()
            CK._apply_template()

    def test_svg_gate_reads_the_current_template(self):
        """安全边距是函数不是 import 期常量——否则 --set 改了边距、门禁还用旧的。"""
        import check_svg as CS
        self.assertEqual(CS.safe_margins()["landscape"],
                         T.load_template()["layout"]["landscape"]["margin"])

    def test_html_renderer_sees_it(self):
        import html_renderer as HR
        T.put_override("animation.director.duration=0.77")
        self.assertEqual(HR.load_template()["animation"]["director"]["duration"], 0.77)


class DriftPresets(unittest.TestCase):
    """运镜三档：两端都得在"不露底"的安全区里，off 必须真的是 0。"""

    def tearDown(self):
        T._OVERRIDES.clear()

    def test_off_means_no_motion(self):
        import gen_hyperframes as G
        preset = G.DRIFT_PRESETS["off"]
        self.assertEqual(preset["ratePerSec"], 0.0)
        self.assertEqual(preset["panX"], 0.0)
        self.assertEqual(preset["panY"], 0.0)

    def test_every_preset_stays_inside_the_cover_slack(self):
        import gen_hyperframes as G
        for name, preset in G.DRIFT_PRESETS.items():
            with self.subTest(preset=name):
                self.assertGreaterEqual(preset["maxScale"], 1.0)
                self.assertLess(preset["maxScale"], 1.2,
                                "推得太近，一张 4:3 的图会被裁成特写")
                self.assertLessEqual(preset["panX"], 1.0)
                self.assertLessEqual(preset["panY"], 1.0)

    def test_preset_becomes_template_overrides(self):
        import gen_hyperframes as G

        class _Args:
            set = []
            drift = "boring"

        class _Parser:
            def error(self, msg):  # pragma: no cover - 不该走到
                raise AssertionError(msg)

        with contextlib.redirect_stderr(io.StringIO()):
            G._init_visual_knobs(_Parser(), _Args())
        drift = T.load_template()["animation"]["imageEntrance"]["drift"]
        self.assertEqual(drift["ratePerSec"], G.DRIFT_PRESETS["boring"]["ratePerSec"])
        self.assertEqual(drift["maxScale"], G.DRIFT_PRESETS["boring"]["maxScale"])


class PlaceholderFontStack(unittest.TestCase):
    def test_placeholder_uses_the_template_font_stack(self):
        """占位图的字体栈从模板派生——手抄一份就会在模板换字体时留在旧值。"""
        import gen_hyperframes as G
        svg = G._canvas_placeholder_svg("标题", 1080, 1440)
        for name in ("Microsoft YaHei", "PingFang SC"):
            self.assertIn(name, svg)
        self.assertIn("sans-serif", svg)


if __name__ == "__main__":
    unittest.main()
