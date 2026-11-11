"""契约层的数值守卫：NaN / Infinity / 装不进 float 的整数一律拒收。

第一性原理：这些值**进得来**。Python 的 `json.load` 默认接受 `NaN` / `Infinity`
字面量（不是标准 JSON，但 parse 默认不拦），于是 `{"scale": NaN}` 能一路穿过
images.json 的校验层；而 NaN 的**任何比较都为 False**（`nan < 0` 也是 False），
所以只写 `isinstance(v, (int, float))` 的守卫会把它整条放行。

放行之后它坏在哪：`_fmt(nan)` 把 "nan" 写进 style/transform，浏览器把整条声明
丢弃——"烘了等于没烘"，画面停在动画前的样子，而生成期、渲染日志与
`hyperframes check` 全都一声不吭。这类"看起来校验过了"的静默退化，正是契约层
该在入口挡掉的东西。

本轮补的两类（修复前全绿，一条都没锁住）：

- **大到 float 装不下的整数**。JSON 允许任意精度数字，`json.load` 会把 `10**400`
  原样留成 int，而 `float(v)` 转不动它抛 `OverflowError`。守卫自己炸掉时调用链
  只捕获 ValueError，整条 build traceback，报错现场丢了。八个动效档各自抄了一份
  `math.isfinite(float(v))`，只有契约层收敛到了 `_validate.is_finite_number`。
- **path d 里溢出成 inf 的坐标**。`parse_path` 用 `float()` 读 d，`1e309` 变成 inf
  却不报错；inf 进了 `_flow` 的采样点表，累计弧长 `length` 也是 inf，于是
  `1e-6 * length` 这条退化阈值变成 inf（有限跨度反被误判成「同一弧长」），
  `ends > length` 这条越界闸也被绕过——守卫失效且零报错。

本轮再补两类。上一轮把谓词改成了纯判定，但没查「调用谓词之前还有没有裸
`float()`」、也没查「补间变量这一层有没有调用谓词」：

- **补间变量表漏了有限性守卫**。`_validate_tween_leaf` 把 NaN / Infinity /
  `10**400` 当合法标量放行，而 `json.dumps` 会把前两个写成裸字面量（不是合法
  JSON，浏览器的 `JSON.parse` 直接抛错）、把 401 位整数读成 `Infinity`。结果
  是这一拍整条补间被丢弃，成片静默没动画，生成期与门禁全绿。`_validate_count`
  里早写着同一条判据，这里没跟上——又一处真源分家。
- **derive 的 duration 在数值校验之前就 `float()`**。`10**400` 抛
  `OverflowError`、`None`/`[]` 抛 `TypeError`，两者都不是 `ValueError`，只捕
  `ValueError` 的调用链整条 traceback，报错现场丢了。判据挪到探针之后即可：
  探针已经保证它是有限数，不必再转一次。
"""
import copy
import json
import math
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _animate
import _derive
import _drag
import _flow
import _lens
import _momentum
import _organic
import _step as STEP_MOD  # 不能叫 _step：本文件已有的 _step() helper 会把它遮掉
import _trail
import _wave
from _images_schema import validate_images_json
from _script_utils import num_fmt
from _validate import is_finite_number

BAD = (float("nan"), float("inf"), float("-inf"))
# -inf 会被"非负/≥0"那一顺带拦下（-inf < 0 为真），另外两个（NaN、+inf）只能靠
# 有限性守卫——NaN 的任何比较都为 False，范围检查对它一律放行。
NEEDS_FINITE_GUARD = (float("nan"), float("inf"))
# 合法 JSON 数字：json.load 保留成 int，float() 转不动。
HUGE = 10 ** 400

FLOW_CFG = {"riders": ["#d4", "#d1", "#d3", "#d2"], "path": "#track",
            "distance": 180, "direction": "forward", "taper": 0.45}
FLOW_D = "M 40 360 C 200 40 380 680 560 300 C 700 80 820 120 940 420"
FLOW_SVG = ('<svg viewBox="0 0 980 735"><path id="track" d="' + FLOW_D + '"/>'
            '<rect id="d3" x="90" y="272" width="20" height="20"/>'
            '<rect id="d1" x="324" y="375" width="20" height="20"/>'
            '<rect id="d4" x="581" y="245" width="20" height="20"/>'
            '<rect id="d2" x="824" y="213" width="20" height="20"/></svg>')
LENS_CFG = {"cells": ["#d1", "#d2", "#d3", "#d4"], "glass": "#glass",
            "to": "#to", "amplitude": 60}
LENS_SVG = ('<svg viewBox="0 0 980 735">'
            '<circle id="glass" cx="150" cy="367" r="100"/>'
            '<circle id="to" cx="830" cy="367" r="6"/>'
            '<rect id="d1" x="340" y="327" width="20" height="20"/>'
            '<rect id="d2" x="420" y="387" width="20" height="20"/>'
            '<rect id="d3" x="480" y="327" width="20" height="20"/>'
            '<rect id="d4" x="560" y="387" width="20" height="20"/></svg>')


def _step(**kw):
    """一条最小的 animate step（可覆盖任意字段）。

    给 at_time 时自动摘掉 at：契约层要求两个锚点二选一，两个都留着报的是另一条错。
    """
    step = {"at": 0, "target": "#a", "to": {"opacity": 1}}
    for k, v in kw.items():
        if k == "at_time":
            step.pop("at", None)
        step[k] = v
    return {"seg-a": {"src": "images/a.svg", "animate": {"steps": [step]}}}


class NumericGuardsRejectNonFinite(unittest.TestCase):
    """逐个钉住：那 8 处守卫对 NaN/±Infinity 必须与对负数一样反应。"""

    def _assert_rejected(self, entry, needle=None, *, bad=None):
        with self.assertRaises(ValueError) as cm:
            validate_images_json(entry)
        msg = str(cm.exception)
        if needle:
            self.assertIn(needle, msg)
        if bad in NEEDS_FINITE_GUARD:
            # 只被范围检查"顺带"拦下不算修好：NaN/+inf 的比较全是 False，
            # 消息里必须点明是有限性守卫接住的。
            self.assertIn("NaN", msg)

    def test_count_to_and_from(self):
        for bad in BAD:
            for key in ("to", "from"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(
                        {"seg-a": {"src": "images/a.svg", "animate": {"steps": [
                            {"at": 0, "target": "#a", "count": {key: bad}}]}}},
                        "必须是数字", bad=bad)

    def test_step_anchor_at(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(at=bad), bad=bad)

    def test_step_anchor_at_time(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(at_time=bad), "非负数字", bad=bad)

    def test_duration_and_delay(self):
        for bad in BAD:
            for key in ("duration", "delay"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(_step(**{key: bad}), "≥0", bad=bad)

    def test_stagger_number_form(self):
        for bad in BAD:
            with self.subTest(bad=bad):
                self._assert_rejected(_step(stagger=bad), "每个间隔秒", bad=bad)

    def test_stagger_object_form(self):
        for bad in BAD:
            for key in ("each", "amount"):
                with self.subTest(key=key, bad=bad):
                    self._assert_rejected(_step(stagger={key: bad}), "≥0")
            with self.subTest(key="from", bad=bad):
                # from 现在是有语义的锚点：NaN/Infinity 不是锚点名、也不是网格坐标
                self._assert_rejected(_step(stagger={"from": bad}),
                                      "锚点名或 ≥0 的整数网格坐标", bad=bad)

    def test_video_audio_opts(self):
        for bad in BAD:
            for opt in ("volume", "fade_in", "fade_out", "media_start"):
                with self.subTest(opt=opt, bad=bad):
                    self._assert_rejected(
                        {"seg-b": {"src": "images/b.mp4", opt: bad}},
                        "必须是数字", bad=bad)

    def test_finite_values_still_pass(self):
        """回归网：这道守卫不是"把数字全拒了"。"""
        validate_images_json({
            "seg-a": {"src": "images/a.svg", "animate": {"steps": [
                {"at": 0, "target": "#a", "duration": 1.5, "delay": 0,
                 "stagger": {"amount": 1, "from": "center"},
                 "to": {"opacity": 0}},
                {"at_time": "+0.5", "target": "#a", "count": {"to": 68.5}},
            ]}}})
        validate_images_json({"seg-b": {"src": "images/b.mp4", "volume": 0.25,
                                        "fade_in": 0.5}})


class NumFmtHasOneAnswerPerNumber(unittest.TestCase):
    """写进 CSS/SVG 的数值必须只有一个答案——同一个 0 不许有两种写法。"""

    def test_negative_underflow_is_zero(self):
        """`-0` 是合法值，但 transform 里它就是 0，留着只会给逐帧 diff 添噪声。"""
        self.assertEqual(num_fmt(-0.0001), "0")
        self.assertEqual(num_fmt(-0.0), "0")
        self.assertEqual(num_fmt(-0.0000001, 6), "0")

    def test_normal_values_keep_their_sign_and_digits(self):
        self.assertEqual(num_fmt(-1.5), "-1.5")
        self.assertEqual(num_fmt(1.0), "1")
        self.assertEqual(num_fmt(0.4 + 0.2), "0.6")


class HugeIntegerIsNotAFiniteNumber(unittest.TestCase):
    """修复前：`math.isfinite(float(10**400))` 抛 OverflowError，守卫自己先坏掉。"""

    def test_predicate_is_a_pure_judgement(self):
        """任何输入都只返回 True/False，绝不抛异常。"""
        for v in (HUGE, -HUGE, float("inf"), float("-inf"), float("nan"),
                  True, False, "1", None, [], {}):
            with self.subTest(v=repr(v)[:24]):
                self.assertFalse(is_finite_number(v))
        for v in (0, 1, -1, 1.5, 10 ** 30):
            with self.subTest(v=v):
                self.assertTrue(is_finite_number(v))

    def test_every_motion_module_delegates_to_the_one_predicate(self):
        """修复前：八个档各抄一份 `_finite`，修一处漏七处（真源分家）。"""
        for mod in (_drag, _flow, _lens, _momentum,
                    _organic, STEP_MOD, _trail, _wave):
            with self.subTest(mod=mod.__name__):
                self.assertIs(mod._finite, is_finite_number)

    def test_contract_layer_reports_it_instead_of_traceback(self):
        """超大整数得按「不合规」报，调用链只捕获 ValueError。"""
        for field in ("duration", "at", "delay", "at_time"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_images_json(_step(**{field: HUGE}))
        for key, cfg in (("flow", dict(FLOW_CFG, distance=HUGE)),
                         ("lens", dict(LENS_CFG, amplitude=HUGE))):
            step = {"at": 0, "target": "#d1", "duration": 1.2, key: cfg}
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_images_json(
                        {"seg-a": {"src": "images/a.svg",
                                   "animate": {"steps": [step]}}})

    def test_motion_validators_and_plans_reject_it(self):
        """各档 validate / plan 不再把 OverflowError 漏给调用方。"""
        for mod, cfg, key, svg, target in (
                (_flow, FLOW_CFG, "distance", FLOW_SVG, "#d4"),
                (_lens, LENS_CFG, "amplitude", LENS_SVG, "#d1")):
            with self.subTest(mod=mod.__name__, where="validate"):
                with self.assertRaises(ValueError):
                    mod.validate(dict(cfg, **{key: HUGE}))
            for field in ("duration", "fps"):
                kw = {"duration": 1.2, "fps": 24, field: HUGE}
                with self.subTest(mod=mod.__name__, where="plan/" + field):
                    with self.assertRaises(ValueError):
                        mod.plan(cfg, kw["duration"], kw["fps"], svg, target)


class FlowGeometryRejectsNonFinite(unittest.TestCase):
    """修复前：d 里的 1e309 读成 inf，弧长表静默污染、退化与越界判据一起失效。"""

    def test_overflow_coordinate_is_rejected_at_the_source(self):
        for d in ("M 40 360 L 1e309 360 L 940 420",
                  "M 40 360 C 1e309 40 380 680 560 300 "
                  "C 700 80 820 120 940 420",
                  "M 40 360 L -1e309 360 L 940 420"):
            svg = FLOW_SVG.replace('d="' + FLOW_D + '"', 'd="' + d + '"')
            with self.subTest(d=d[:26]):
                with self.assertRaises(ValueError) as cm:
                    _flow.geometry(svg, FLOW_CFG)
                self.assertIn("非有限", str(cm.exception))

    def test_arc_table_has_its_own_overflow_guard(self):
        """坐标本身有限、累加才溢出时，弧长表仍要出声（不靠上游兜底）。"""
        pts = [(0.0, 0.0), (1e308, 1e308), (0.0, 0.0)]
        self.assertFalse(math.isfinite(2 * math.hypot(1e308, 1e308)),
                         "本用例要真的构造出累加溢出，否则锁不住东西")
        with self.assertRaises(ValueError) as cm:
            _flow._arc_table(pts, "弧长表溢出用例")
        self.assertIn("非有限", str(cm.exception))


# ── 本轮：契约层自己的两处漏网 ────────────────────────────────────────

# 覆盖尽量多数值字段的合法 images.json：属性补间一份、参数演绎一份。
# 属性门禁拿它当底板，逐个数值叶子注入坏值——将来新增字段忘了守卫就会红。
def _rich_images():
    tween = {"seg-a": {"src": "images/a.svg", "animate": {"steps": [
        {"at": 0, "target": "#a", "to": {"opacity": 1, "attr": {"r": 5}},
         "from": {"opacity": 0}, "duration": 0.6, "delay": 0.1, "stagger": 0.05},
        {"at_time": 1.5, "target": "#b", "set": {"x": 10}},
        {"at": 2, "target": "#c", "count": {"to": 68.5, "from": 0, "decimals": 1}},
    ]}}}
    derive = {"seg-b": {"src": "images/b.svg", "animate": {
        "params": {"theta": 0.0},
        "bind": [{"target": "#tip", "attr": "cx", "expr": "theta"}],
        "steps": [{"at": 0, "param": "theta", "to": 90.0, "duration": 1.2}]}}}
    return [tween, derive]


def _numeric_leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _numeric_leaves(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _numeric_leaves(v, path + (i,))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path


def _put(obj, path, value):
    cur = obj
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


class TweenPayloadRejectsNonFinite(unittest.TestCase):
    """修复前：`_validate_tween_leaf` 把非有限数当合法标量放行，进 json.dumps。"""

    def test_non_finite_values_really_break_the_emitted_payload(self):
        """先自证守卫不是想象出来的：这些值经 json.dumps 后浏览器吃不下。"""
        def _reject(token):
            raise ValueError("非法 JSON 字面量: " + token)
        for v in BAD:
            with self.subTest(v=repr(v)):
                with self.assertRaises(ValueError):
                    json.loads(json.dumps({"opacity": v}), parse_constant=_reject)
        # 401 位整数是合法 JSON，但 JS 的 Number 读成 Infinity（float() 转不动）。
        with self.assertRaises(OverflowError):
            float(json.loads(json.dumps({"opacity": HUGE}))["opacity"])

    def test_to_from_set_leaves_reject_non_finite(self):
        for key in ("to", "from", "set"):
            for bad in BAD + (HUGE, -HUGE):
                with self.subTest(key=key, bad=repr(bad)[:24]):
                    entry = {"seg-a": {"src": "images/a.svg", "animate": {"steps": [
                        {"at": 0, "target": "#a", key: {"opacity": bad}}]}}}
                    with self.assertRaises(ValueError) as cm:
                        validate_images_json(entry)
                    self.assertIn("有限数", str(cm.exception))

    def test_nested_attr_and_array_are_guarded_too(self):
        """attr:{} 与数组是递归进去的，守卫必须跟着一起进去。"""
        for payload in ({"attr": {"r": float("nan")}},
                        {"attr": {"r": HUGE}},
                        {"x": [1, float("inf")]},
                        {"x": [1, [2, -HUGE]]}):
            with self.subTest(payload=repr(payload)[:40]):
                entry = {"seg-a": {"src": "images/a.svg", "animate": {"steps": [
                    {"at": 0, "target": "#a", "to": payload}]}}}
                with self.assertRaises(ValueError) as cm:
                    validate_images_json(entry)
                self.assertIn("有限数", str(cm.exception))

    def test_finite_payload_still_passes(self):
        """回归网：守卫不是把数字全拒了（含字符串/布尔/None/数组的既有语义）。"""
        validate_images_json({"seg-a": {"src": "images/a.svg", "animate": {"steps": [
            {"at": 0, "target": "#a", "to": {
                "opacity": 0.5, "attr": {"r": 1e30}, "fill": "#fff",
                "xs": [0, -2.5, 1e-9], "visible": True, "note": None}},
        ]}}})


class DeriveDurationNeverTracebacks(unittest.TestCase):
    """修复前：validate_derive 在数值校验之前裸 float(duration)。"""

    def _derive(self, duration):
        return {"seg-b": {"src": "images/b.svg", "animate": {
            "params": {"theta": 0.0},
            "bind": [{"target": "#tip", "attr": "cx", "expr": "theta"}],
            "steps": [{"at": 0, "param": "theta", "to": 90,
                       "duration": duration}]}}}

    def test_bad_duration_values_are_value_errors_with_field_location(self):
        """坏值只能得到带字段定位的 ValueError，不能 traceback、不能放行。"""
        for v in (HUGE, -HUGE, None, [], "abc", True) + BAD:
            with self.subTest(v=repr(v)[:24]):
                with self.assertRaises(ValueError) as cm:
                    validate_images_json(self._derive(v))
                self.assertIn("duration", str(cm.exception))
                self.assertIn("有限数字", str(cm.exception))

    def test_duration_zero_still_rejected_with_its_reason(self):
        """判据挪到探针之后，语义不能丢：0 仍按「瞬跳」报，不是被放行。"""
        with self.assertRaises(ValueError) as cm:
            validate_images_json(self._derive(0))
        self.assertIn("瞬跳", str(cm.exception))

    def test_generate_stage_rejects_it_too(self):
        """纵深：compile_derive 自己也不许把 OverflowError 漏给调用方。"""
        block = {"params": {"theta": 0}, "bind": [],
                 "steps": [{"at": 0, "param": "theta", "to": 90,
                            "duration": HUGE}]}
        with self.assertRaises(ValueError) as cm:
            _derive.compile_derive(block, 0.6, "生成期用例")
        self.assertIn("duration", str(cm.exception))


class AnimateHealthCheckNeverTracebacks(unittest.TestCase):
    """纵深：生成期体检对畸形 step 也只报 error，不 traceback。"""

    SVG = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<rect id="a" x="0" y="0" width="10" height="10" opacity="0"/></svg>')

    def test_malformed_steps_get_errors_not_tracebacks(self):
        cases = (("at=HUGE", {"at": HUGE, "target": "#a", "to": {"opacity": 1}}),
                 ("at_time=HUGE", {"at_time": HUGE, "target": "#a",
                                   "to": {"opacity": 1}}),
                 ("at=None", {"at": None, "target": "#a", "to": {"opacity": 1}}),
                 ("step=str", "oops"), ("step=None", None))
        for label, st in cases:
            with self.subTest(case=label):
                try:
                    errs, _ = _animate.check_animate(
                        self.SVG, {"steps": [st]}, "seg-a", full_bleed=False)
                except Exception as e:  # 任何异常都算回归（含 OverflowError）
                    self.fail(f"{label} 不该 traceback：{type(e).__name__}: {e}")
                self.assertIsInstance(errs, list)

    def test_non_dict_step_is_reported_by_index(self):
        errs, _ = _animate.check_animate(
            self.SVG, {"steps": ["oops"]}, "seg-a", full_bleed=False)
        self.assertTrue(any("steps[0]" in e for e in errs),
                        "绕过契约层的坏 step 要点名是哪一拍，不能静默跳过")


class EveryNumericLeafIsGuarded(unittest.TestCase):
    """属性级门禁：未来新增数值字段忘了守卫，这里会红。

    修复前它会红在两处：to/from/set 里的数值叶子被放行（没抛 ValueError），
    derive 的 duration 抛 OverflowError（不是 ValueError）。`assertRaises`
    一次把「静默放行」和「traceback」两种退化都挡在外面。
    """

    def test_every_numeric_leaf_in_images_json_is_guarded(self):
        total = 0
        for base in _rich_images():
            paths = list(_numeric_leaves(base))
            total += len(paths)
            for path in paths:
                for bad in (HUGE, float("nan"), float("inf"), float("-inf")):
                    data = copy.deepcopy(base)
                    _put(data, path, bad)
                    with self.subTest(path=".".join(map(str, path)),
                                      bad=repr(bad)[:20]):
                        with self.assertRaises(ValueError):
                            validate_images_json(data)
        self.assertGreaterEqual(
            total, 15, "底板要真的覆盖到足够多的数值字段，否则这道闸是空的")


if __name__ == "__main__":
    unittest.main()
