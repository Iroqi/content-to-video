"""视觉模板的临时覆写层（CLI `--set`）。

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
        self.assertEqual(T.load_template()["animation"]["animate"]["duration"], 0.5)
        T.put_override("animation.animate.duration=0.9")
        self.assertEqual(T.load_template()["animation"]["animate"]["duration"], 0.9)

    def test_unknown_path_is_named_with_the_nearest_keys(self):
        with self.assertRaises(ValueError) as cm:
            T.put_override("animation.animate.duratoin=1")
        msg = str(cm.exception)
        self.assertIn("animation.animate.duratoin", msg)
        self.assertIn("animation.animate.duration", msg)  # 最近的候选要列出来

    def test_block_replacement_is_refused(self):
        with self.assertRaises(ValueError):
            T.put_override('animation.animate={"duration":1}')
        with self.assertRaises(ValueError):
            T.put_override("animation.animate.duration=null")

    def test_bare_word_is_not_a_json_scalar(self):
        with self.assertRaises(ValueError) as cm:
            T.put_override("typography.titleWeight=bold")
        self.assertIn("JSON", str(cm.exception))

    def test_scalar_kind_must_match_the_template_leaf(self):
        """类型换掉同样是"以为改了其实没改"。

        bool 塞进数字位最隐蔽：`float(True)` 静默变成 1.0，而写进 CSS 的是
        `True` —— 运行期 parseFloat 读不出来、回落兜底，成片里演的是第三个值。
        """
        self.assertEqual(T._scalar_kind(0), T._scalar_kind(0.35))  # int/float 互通
        self.assertNotEqual(T._scalar_kind(True), T._scalar_kind(1))
        with self.assertRaises(ValueError) as cm:
            T.put_override("subtitle.relay.from=true")
        self.assertIn("类型对不上", str(cm.exception))
        with self.assertRaises(ValueError):
            T.put_override('typography.titleWeight="600"')  # 字符串进数字位
        T.put_override("subtitle.relay.from=0")  # 数字写 0 是合法的

    def test_layer_is_scoped(self):
        with T.override_layer(["animation.animate.duration=0.7"]):
            self.assertEqual(T.load_template()["animation"]["animate"]["duration"], 0.7)
        self.assertEqual(T.load_template()["animation"]["animate"]["duration"], 0.5)


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
        T.put_override("animation.animate.duration=0.77")
        self.assertEqual(HR.load_template()["animation"]["animate"]["duration"], 0.77)


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
