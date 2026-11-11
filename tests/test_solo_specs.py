"""一步一种「演」的契约：规格表是唯一真源，契约层与渲染层不许分家。

旧实现把「哪几种写法互斥、每档拒绝哪些拍级旋钮」分别抄在 15 段 elif 里，
每加一档要在六处各补一行；禁止清单是逐轮追加的历史快照，彼此不一致。
这份测试把「规则只有一份、且两层对得上」钉在规格表上，而不是钉在某轮文案上。
"""
import itertools
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _images_schema as IMG
import _field
import _flow
import _lens
import _swarm
import _trail
import _wave
from html_renderer import _SOLO_RENDERERS

SPECS = IMG._SOLO_SPECS
KEYS = [s["key"] for s in SPECS]
# 键集里除锚点 / 补间 / 旋钮之外的，就是「独立演」的全集
CONTROL_KEYS = frozenset({"at", "at_time", "target", "from", "to", "set",
                          "draw", "stagger", "duration", "ease", "delay",
                          "repeat", "yoyo"})
SPEC_FIELDS = frozenset({"key", "validate", "reason", "no_stagger",
                         "no_repeat", "no_ease", "no_cam", "first",
                         "no_self_in", "no_cam_in"})


def _err(**kw):
    """走一次单步校验，返回报错文案；没报错返回 None。"""
    step = {"at": 0, "target": "#a"}
    step.update(kw)
    try:
        IMG._validate_steps("k", {"steps": [step]}, what="animate")
    except ValueError as e:
        return str(e)
    return None


class SoloSpecContract(unittest.TestCase):
    def test_every_declared_solo_key_has_a_spec(self):
        """键集里新增一种「演」而没进规格表时，这条立刻红。"""
        declared = IMG._DIRECTOR_STEP_KEYS - CONTROL_KEYS
        self.assertEqual(set(KEYS), declared)
        self.assertEqual(len(KEYS), len(set(KEYS)))
        self.assertEqual(set(IMG._SOLO_SPEC_BY_KEY), set(KEYS))

    def test_spec_rows_are_complete(self):
        """每行字段齐全且类型正确：漏一个字段不该靠 .get() 静默当 None。"""
        for s in SPECS:
            with self.subTest(key=s["key"]):
                self.assertEqual(set(s), SPEC_FIELDS)
                self.assertTrue(callable(s["validate"]))
                self.assertIsInstance(s["reason"], str)
                for flag in ("no_stagger", "no_repeat", "no_ease", "no_cam"):
                    self.assertIsInstance(s[flag], bool)

    def test_schema_and_renderer_agree_on_sampled_kinds(self):
        """生成期采样档（拒拍级 ease 的那些）必须正好是渲染层分派表里的那些。"""
        sampled = {s["key"] for s in SPECS if s["no_ease"]}
        rendered = {row[0] for row in _SOLO_RENDERERS}
        self.assertEqual(sampled, rendered)
        self.assertEqual(len(rendered), len(_SOLO_RENDERERS))

    def test_any_two_solo_kinds_are_mutually_exclusive(self):
        """12 档两两组合全拒，不看 elif 顺序。"""
        for a, b in itertools.combinations(KEYS, 2):
            with self.subTest(a=a, b=b):
                msg = _err(**{a: {}, b: {}})
                self.assertIsNotNone(msg)
                self.assertIn("一步只能有一种", msg)

    def test_solo_kind_plus_tween_is_rejected(self):
        """独立演与 from/to/set/draw 也不能共存。"""
        for k in KEYS:
            with self.subTest(key=k):
                msg = _err(**{k: {}, "to": {"opacity": 1}})
                self.assertIsNotNone(msg)
                self.assertIn("一步只能有一种", msg)

    def test_conflict_error_lists_only_what_was_written(self):
        """报错不再是一份会自相矛盾的禁止清单（flow 曾把自己列进禁止项）。"""
        msg = _err(flow={}, wave={})
        self.assertIn("flow", msg)
        self.assertIn("wave", msg)
        self.assertNotIn("就不能再写", msg)
        self.assertEqual(msg.count("flow"), 1)
        self.assertEqual(msg.count("wave"), 1)

    def test_every_sampled_kind_rejects_step_ease(self):
        """九档生成期采样档一致拒绝拍级 ease：老五档不再留静默失效口子。"""
        for s in SPECS:
            if not s["no_ease"]:
                continue
            with self.subTest(key=s["key"]):
                msg = _err(**{s["key"]: {}, "ease": "power2.out"})
                self.assertIsNotNone(msg)
                self.assertIn("不收拍级 ease", msg)

    def test_every_sampled_kind_rejects_stagger_and_replay(self):
        """错峰与重放对采样档同样是「写了等于没写」，逐档一致拒绝。"""
        for s in SPECS:
            if not s["no_stagger"]:
                continue
            with self.subTest(key=s["key"], knob="stagger"):
                msg = _err(**{s["key"]: {}, "stagger": 0.1})
                self.assertIn("不收 stagger", msg)
            with self.subTest(key=s["key"], knob="repeat"):
                msg = _err(**{s["key"]: {}, "repeat": 2})
                self.assertIn("不收 repeat", msg)

    def test_sampled_kinds_share_the_same_red_lines(self):
        """生成期采样档的红线集合必须一致：漏一档就是静默失效口子。"""
        sampled = [s for s in SPECS if s["no_ease"]]
        self.assertEqual(len(sampled), 9)
        for s in sampled:
            with self.subTest(key=s["key"]):
                self.assertTrue(s["no_stagger"])
                self.assertTrue(s["no_repeat"])
                self.assertTrue(s["no_cam"])

    def test_sampled_kinds_share_one_frame_grid(self):
        """钳位 / 归一 / 采样帧数在各档都指向同一份实现，不许再抄回去。"""
        for mod in (_trail, _wave, _flow, _lens, _swarm):
            with self.subTest(module=mod.__name__):
                self.assertIs(mod._clamp, _field.clamp)
                self.assertIs(mod._clean, _field.clean)
                self.assertIs(mod.frame_count, _field.frame_count)

    def test_derive_probe_still_shares_step_rules(self):
        """参数演绎借用的锚点/时长/缓动判据仍走同一份单步校验。"""
        self.assertIsNone(
            IMG._validate_steps_probe(
                {"at": 0, "to": 1, "duration": 0.5, "ease": "power2.out"},
                "probe"))
        with self.assertRaises(ValueError):
            IMG._validate_steps_probe({"at": 0, "to": 1, "duration": -1},
                                      "probe")


if __name__ == "__main__":
    unittest.main()
