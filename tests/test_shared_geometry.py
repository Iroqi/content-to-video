"""本轮复审修的每一条，配一条**在修复前会红**的测试。

每条注释写明"修复前它是什么样"——测试要锁的是那个错误行为不再回来，不是锁
当前的某个数字。
"""
import inspect
import os
import sys
import unittest

import _helpers as H  # noqa: N999  把 scripts/ 放进 sys.path

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import _cam_crop as CC  # noqa: E402
import _images_schema as IS  # noqa: E402
import _render_backend as RB  # noqa: E402
import _stage_carry as SC  # noqa: E402
import _template as T  # noqa: E402
import check_frames as CF  # noqa: E402
import check_svg as CS  # noqa: E402
import gen_hyperframes as GH  # noqa: E402

NS = "http://www.w3.org/2000/svg"


def _svg(body, w=1080, h=1440):
    return f'<svg xmlns="{NS}" viewBox="0 0 {w} {h}">{body}</svg>'


class TextBandSingleSource(unittest.TestCase):
    """`<text>` 的竖向占位：三道门必须同一口径。

    修复前：`check_svg._ink_band` 读不到 font-size 时兜 26（MIN_READABLE_FONT_PX），
    `_cam_crop` 兜 17.0，而 `check_svg._text_oob` 干脆 `return None`（不判）。同一份
    没写字号的 SVG：空带自查按 34px 高算它、运镜自查按 22px 算它、越界自查当它不存在。
    """

    def test_cam_crop_and_check_svg_fallback_to_same_font_size(self):
        svg = _svg('<text id="t" x="100" y="900">字</text>')
        # check_svg 的空带自查路径
        root = __import__("xml.etree.ElementTree", fromlist=["ET"]).fromstring(svg)
        band_check = CS._ink_band(root[0], "text", lambda el, k: el.get(k), 1080, 1440)
        # _cam_crop 的运镜自查路径
        band_cam = CC.cam_geometry(__import__("xml.etree.ElementTree", fromlist=["ET"]).fromstring(
            _svg('<g id="cam"><text id="t" x="100" y="900">字</text></g>')))[2]
        cam_y = [b[1] for b in band_cam if b[0] == "#t"][0]
        self.assertEqual(band_check, T.svg_text_band(900))
        self.assertAlmostEqual(cam_y[2], band_check[0], places=6)
        self.assertAlmostEqual(cam_y[3], band_check[1], places=6)
        # 兜底字号就是可读下限，不是某个随手写的数
        self.assertEqual(band_check, (900 - T.MIN_READABLE_FONT_PX,
                                      900 + T.MIN_READABLE_FONT_PX * T.TEXT_DESCENDER_RATIO))

    def test_text_out_of_bounds_no_longer_skips_when_font_size_unreadable(self):
        """修复前：字号写在 <style> 的 class 里时 `_text_oob` 直接 return None，
        于是同一份图里两个 y=3000 的文字，inline 字号的那个报越界、class 字号的
        那个一声不吭。"""
        ET = __import__("xml.etree.ElementTree", fromlist=["ET"])
        svg = _svg('<text id="a" x="100" y="3000">越界的字</text>')
        el = ET.fromstring(svg)[0]
        msg = CS._text_oob(el, lambda e, k: e.get(k), 1080, 1440)
        self.assertIsNotNone(msg, "读不到字号也要按可读下限判越界，不能跳过")
        self.assertIn("读不出", msg)   # 并且说清这个数是估的


class AspectToleranceSingleSource(unittest.TestCase):
    """「SVG 与框等比」的容差只有一份。

    修复前：`check_svg.RATIO_TOL = 0.01`，而 `gen_hyperframes` 的两处门禁写死
    `0.002 * box_w * box_h`——差 5 倍。作者照 `check_svg` 的"通过"改完图，生成期
    照样被拦，两条报错文案还不一样。
    """

    def test_check_svg_and_gen_agree_on_one_size(self):
        # 1000×1330 与 1080×1440：相对失配 0.27% —— 旧 check_svg(1%) 放行、gen 拦下
        w, h = 1000, 1330
        self.assertTrue(CS.same_aspect(w, h, (1080, 1440)) is False)
        self.assertEqual(CS.same_aspect, T.same_aspect)
        self.assertEqual(GH.same_aspect, T.same_aspect)

    def test_slot_and_canvas_errors_use_the_same_predicate(self):
        """gen 的两处门禁与 check_svg 的自查对同一张图给同一答案。"""
        w, h = 1000, 1330          # 0.27% 失配
        errs = GH.canvas_layout_errors(
            {"seg": {"src": "a.svg"}}, [{"id": "seg", "layout": "canvas", "sentences": []}],
            os.path.dirname(os.path.abspath(__file__)), 1080, 1440)[0]
        self.assertTrue(errs, "0.27% 失配要在生成期拦下")
        # check_svg 的自查对同一张图也不能说"通过"
        self.assertFalse(T.same_aspect(w, h, (1080, 1440)))


class CrossModuleKeysSingleSource(unittest.TestCase):
    """跨模块契约键/阈值只有一份字面量。

    上一轮修的是"合成 ID 三处硬编码"。同一类病没修完：下面每条在修复前都是
    **两处各写一个字面量**，靠注释约定"两边必须一致"——而注释不会拦人。
    值相等的断言本身很弱（短字符串会被 intern，`is` 也相等），所以每条都配
    一句源码级断言：常量只能在一处被**赋值**。
    """

    def test_attr_channel_key_comes_from_the_contract_layer(self):
        """修复前：`_images_schema._ATTR_KEY = "attr"` 与
        `_stage_carry._ATTR_KEY = "attr"` 是两个独立字面量，前者的注释写着
        "两处必须指同一个东西"却没有任何机制 enforce。分家的症状是
        `stage:"keep"` 接续页第 0 帧就坏，且生成期零报错。"""
        self.assertEqual(SC.ATTR_KEY, IS.ATTR_KEY)
        # 烘焙层不许再自己声明一份：真源在契约层（什么形状放行由它说了算）。
        self.assertNotIn("ATTR_KEY = ", inspect.getsource(SC),
                         "_stage_carry 不得自己声明 ATTR_KEY，从 _images_schema 导入")

    def test_stage_attr_is_not_relitigated_in_warning_text(self):
        """修复前：`STAGE_ATTR = "data-ctv-stage"` 定义了，但三条告警文案里写死
        `<g data-ctv-stage>`。改常量名时文案不动，作者被指向一个不存在的属性名
        ——而这几条正是"折层没发生"唯一的出口。

        用**行为**验而不是源码计数：字面量藏在长字符串内部，源码里根本不出现
        `"data-ctv-stage"` 这个带引号的子串，计数那条断言永远绿（已实测）。
        这里改常量、跑真路径、看文案跟着不跟着变。
        """
        # 相机步 repeat:-1 → 走"本页不折 <g …>"那条告警
        svg = _svg('<g id="cam"><rect id="r" x="10" y="10" width="100" height="100"/></g>')
        steps = [{"target": "#cam", "to": {"scale": 2}, "repeat": -1}]
        _, baseline = SC.bake_settled_state(svg, steps)
        self.assertTrue(any("本页不折" in n for n in baseline), baseline)
        _orig = SC.STAGE_ATTR
        try:
            SC.STAGE_ATTR = "data-ctv-stage-SENTINEL"
            _, patched = SC.bake_settled_state(svg, steps)
        finally:
            SC.STAGE_ATTR = _orig
        self.assertTrue(any("data-ctv-stage-SENTINEL" in n for n in patched),
                        "告警文案写死了属性名，没引用 STAGE_ATTR：%s" % patched)
        # 上面只跑到三条告警里的一条；这条源码断言把另外两条一起兜住：`<g …>` 这种
        # 写法只剩模块头注释里那一处说明，文案里一律 f-string 引用。
        self.assertEqual(inspect.getsource(SC).count("<g data-ctv-stage>"), 1,
                         "还有告警文案在写死 <g data-ctv-stage>")

    def test_check_svg_boxes_are_the_template_accessors(self):
        """修复前：`check_svg` 用 `_TPL["canvas"]["vertical"]` 下标自己取，
        `gen_hyperframes` 走 `get_canvas` / `get_image_box`——两套取值口，键名还是
        "portrait" 与 "vertical" 两种叫法。模板挪键时一边跟着变、一边不动。"""
        for a in ("portrait", "landscape"):
            self.assertEqual(CS.CANVAS[a], T.get_canvas(a))
            self.assertEqual(CS.SLOT[a], T.get_image_box(a))
        self.assertNotIn('["canvas"]', inspect.getsource(CS),
                         "check_svg 不得自己下标取 canvas，走 get_canvas")

    def test_safe_margin_shares_the_renderer_padding_parse(self):
        """修复前：`check_svg._safe_margins` 自己 `.split()` 解 padding 简写，
        注释声称"与 html_renderer 读同一键同一口径"。模板把 `50px` 写成
        `40px 50px` 时只有 html_renderer 跟着变，"安全边距"与真实内容盒分家。"""
        self.assertEqual(CS.SAFE_MARGIN["portrait"], int(T.seg_card_pad_x()))
        # 两边现在都调同一个函数：改解析口径只需动 _template 一处
        self.assertTrue(hasattr(T, "seg_card_pad_x"))

    def test_render_cap_floor_is_the_shared_default(self):
        """修复前：`render_wait(max_wait=1800.0)` 与 `run.py` 的
        `max(1800.0, ...)` 各写一个 1800。改默认值时 run.py 的地板不动，
        注释里"默认 1800s 硬上限"就成了谎话。"""
        self.assertEqual(RB.DEFAULT_MAX_WAIT, 1800.0)
        # 不 import run.py（它是 CLI 入口，导入有 argv/stdio 副作用）：直接读源码
        with open(os.path.join(SCRIPTS_DIR, "run.py"), encoding="utf-8") as _f:
            run_src = _f.read()
        self.assertIn("DEFAULT_MAX_WAIT", run_src,
                      "run.py 的等待上限地板要取自 _render_backend，不能重抄数字")
        self.assertNotIn("max(1800.0", run_src, "run.py 里不该再有 1800.0 字面量")


class NoDuplicatedCrossModuleConstants(unittest.TestCase):
    """跨模块同名同值的模块级常量，一个都不留。

    上一轮修了"合成 ID 三处硬编码"，本轮又修了 `attr` 键名、`data-ctv-stage`、
    渲染超时 1800、padding 解析——**每次都是靠人读代码发现的**。这条把发现动作
    自动化：顶层 `NAME = 字面量` 若在两个以上模块各写一份，就是同一语义各自维护，
    改一处漏一处且不报错。`from x import Y` 不是赋值，导入方不算重复。
    """

    # 同名但确实是两个独立概念的常量（各自演化，不是同一语义的两份拷贝）。
    ALLOWED = frozenset()

    def test_no_constant_is_declared_in_two_modules(self):
        import ast
        import collections
        seen = collections.defaultdict(set)
        for fn in os.listdir(SCRIPTS_DIR):
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS_DIR, fn), encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fn)
            for node in tree.body:
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    name = t.id
                    if not (name.isupper()
                            or (name.startswith("_") and name[1:2].isupper())):
                        continue
                    if name in self.ALLOWED:
                        continue
                    try:
                        val = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError, TypeError):
                        continue
                    if isinstance(val, (str, int, float, bool)) or (
                            isinstance(val, (tuple, list, frozenset, set))
                            and len(val) <= 12):
                        seen[(name, repr(val))].add(fn)
        dupes = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
        self.assertEqual({}, dupes,
                         "这些常量在多个模块各写了一份字面量——收成单一真源（导入），"
                         "否则改一处漏一处且不报错")


class EmptyBandNoFalseAlarm(unittest.TestCase):
    """空带自查不能被越界图元污染。

    修复前：`if b > a` 判的是**裁剪前**的值，一个 y=3000 的文字通过判定后被裁成
    (2940, 1440) 的逆序区间混进合并，实测打出"空带占页高 189%"的假告警——它盖掉的
    正是真正该看的构图问题。
    """

    def test_out_of_page_band_does_not_become_reversed_span(self):
        bands = [(2940, 3018), (100, 1200)]      # 前者整个在页外
        y0, y1 = CS._largest_empty_band(bands, 1440)
        self.assertLessEqual(y1 - y0, 1440)      # 页数不会超过页高
        self.assertGreaterEqual(y0, 0.0)
        self.assertLessEqual(y1, 1440.0)


class CheckFramesGuards(unittest.TestCase):
    """抽帧断言的三条假守卫。"""

    def _stub(self, metrics=None, ocr=None):
        CF._get_ffmpeg = lambda: "ffmpeg"
        CF._probe_duration = lambda ff, v: 10.0
        CF.extract_frame = lambda ff, v, t, p: True
        CF.analyze_frame = (lambda p: None) if metrics is None else (lambda p: dict(metrics))
        CF._ocr_text = (lambda p: None) if ocr is None else (lambda p: ocr)

    def test_ocr_unavailable_does_not_swallow_pixel_assertions(self):
        """修复前：`--ocr` 且 tesseract 缺失时那一帧直接 `continue`，已经量出来的
        黑屏/死平断言一起被吞掉——一支全黑片报"0 帧失败、通过"。"""
        black = {"nonblack_ratio": 0.0, "color_buckets": 1}
        self._stub(metrics=black, ocr=None)
        without_ocr = CF.check_video("x.mp4", every=5, ocr=False)[0]
        with_ocr = CF.check_video("x.mp4", every=5, ocr=True)[0]
        self.assertEqual(len(without_ocr), 2)
        self.assertEqual(len(with_ocr), len(without_ocr),
                         "OCR 降级只能降级 OCR 那一条，不能吞掉像素断言")

    def test_nothing_measured_is_not_a_pass(self):
        """修复前：PIL/numpy 缺失时每帧 `continue`，failures 恒为空，main 打印
        '通过：N 帧全部过断言'、run.py 记 check:ok——把"没查"说成"查过了"。"""
        self._stub(metrics=None)
        failures, report = CF.check_video("x.mp4", every=5)
        self.assertIsNone(failures, "一帧都没量到必须按'没量到'降级，不是空列表")
        self.assertTrue(any("一帧都没量到" in l for l in report), report)

    def test_timestamps_outside_the_film_are_reported(self):
        """修复前：`--timestamps 10,20` 被 `or [0.0]` 静默换成首帧，结论写
        '1 帧全部过断言'——用户点名的两个时刻一帧都没看。"""
        self._stub(metrics={"nonblack_ratio": 0.5, "color_buckets": 9})
        _f, report = CF.check_video("x.mp4", timestamps=[10, 20])
        self.assertTrue(any("成片 10.0s 之外" in l for l in report), report)


if __name__ == "__main__":
    unittest.main()
