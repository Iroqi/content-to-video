"""_path_morph：同拓扑 path 解析、校验与线性插值的回归（纯函数，不碰渲染）。"""
import unittest

import _path_morph as P


class Parse(unittest.TestCase):
    def test_commands_and_numbers(self):
        self.assertEqual(P.parse_path("M 0 0 L 1 1", "x"),
                         [("M", [0.0, 0.0]), ("L", [1.0, 1.0])])

    def test_comma_and_implicit_and_relative(self):
        # 逗号分隔、隐式重复（h 后跟多组）、相对命令都保留原样大小写
        self.assertEqual(P.parse_path("M0,0 h10 10 v5", "x"),
                         [("M", [0.0, 0.0]), ("h", [10.0, 10.0]), ("v", [5.0])])

    def test_exponent_and_dot_forms(self):
        self.assertEqual(P.parse_path("M.5 1e2", "x"), [("M", [0.5, 100.0])])

    def test_malformed_rejected(self):
        for bad in ("", "   ", "L 0 0", "M 0 0 Q 1", "M 0 0 X 3", "M 0 0 !"):
            with self.assertRaises(ValueError, msg=bad):
                P.parse_path(bad, "x")


class Compat(unittest.TestCase):
    def test_same_topology_ok(self):
        P.check_morphable("M 0 0 L 1 1 L 2 2", "M 0 5 L 1 9 L 2 -3")

    def test_command_count_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            P.check_morphable("M 0 0 L 1 1", "M 0 0 L 1 1 L 2 2")

    def test_command_letter_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            P.check_morphable("M 0 0 L 1 1", "M 0 0 C 1 1 2 2 3 3")

    def test_relative_absolute_not_interchangeable(self):
        with self.assertRaises(ValueError):
            P.check_morphable("M 0 0 l 1 1", "M 0 0 L 1 1")


class Interp(unittest.TestCase):
    def test_endpoints_are_from_and_to(self):
        pf, pt = P.make_morph("M 0 0 L 1 1", "M 0 5 L 1 9")
        self.assertEqual(P.interp(pf, pt, 0), "M0 0 L1 1")
        self.assertEqual(P.interp(pf, pt, 1), "M0 5 L1 9")

    def test_midpoint_linear(self):
        pf, pt = P.make_morph("M 0 0 L 10 10", "M 0 4 L 10 20")
        self.assertEqual(P.interp(pf, pt, 0.5), "M0 2 L10 15")

    def test_format_trims_float_noise(self):
        pf, pt = P.make_morph("M 0 0", "M 1 3")
        d = P.interp(pf, pt, 1 / 3)
        self.assertNotIn(".", d.split()[1])          # 坐标被格式化，无 0.3333333 尾巴
        self.assertTrue(all(len(tok.split(".")[-1]) <= 3 for tok in d.split()[1:]))


if __name__ == "__main__":
    unittest.main()
