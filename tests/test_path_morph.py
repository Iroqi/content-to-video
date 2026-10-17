"""_path_morph：同拓扑 path 解析、校验与线性插值的回归（纯函数，不碰渲染）。"""
import unittest

import _helpers as H  # noqa: F401  sys.path 装配（scripts/），本文件可独立运行
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
        # 空参数命令（"M"/"M0 0 L"/"M0 0 Z Q"…）与缺参/非法字符同一档：
        # SVG 里只有 Z 允许不带参数，其余命令空参放行会让 interp 原样吐回
        # 浏览器不认的 d，动画静默丢失。
        for bad in ("", "   ", "L 0 0", "M 0 0 Q 1", "M 0 0 X 3", "M 0 0 !",
                    "M", "M 0 0 L", "M 0 0 Z Q", "M 0 0 C", "M 0 0 L 1 1 H",
                    "M 0 0 S", "M 0 0 T", "M 0 0 A"):
            with self.assertRaises(ValueError, msg=bad):
                P.parse_path(bad, "x")

    def test_z_may_stand_alone(self):
        # Z 是唯一允许不带参数的命令：闭合回子路径起点
        self.assertEqual(P.parse_path("M 0 0 Z", "x"), [("M", [0.0, 0.0]), ("Z", [])])


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


class Endpoints(unittest.TestCase):
    """path_endpoints：开 path 的首尾绝对坐标（给 check_svg 端点落点用）。"""

    def test_absolute_first_last(self):
        self.assertEqual(P.path_endpoints("M 10 20 L 30 40 C 1 2 3 4 50 60"),
                         ((10.0, 20.0), (50.0, 60.0)))

    def test_relative_cursor_walk(self):
        self.assertEqual(P.path_endpoints("M 10 20 l 5 0 h 3 v 4"), ((10.0, 20.0), (18.0, 24.0)))

    def test_arc_endpoint_is_last_two_args(self):
        self.assertEqual(P.path_endpoints("M 0 0 A 5 5 0 0 1 9 9"), ((0.0, 0.0), (9.0, 9.0)))

    def test_any_z_means_closed(self):
        self.assertIsNone(P.path_endpoints("M 0 0 L 10 0 Z"))
        self.assertIsNone(P.path_endpoints("M 0 0 L 10 0 L 0 10 z"))
        self.assertIsNone(P.path_endpoints("M 0 0 L 10 0 Z M 20 0 L 30 0"))

    def test_single_m_is_degenerate_but_readable(self):
        self.assertEqual(P.path_endpoints("M 4 5"), ((4.0, 5.0), (4.0, 5.0)))

    def test_non_string_rejected(self):
        with self.assertRaises(ValueError):
            P.path_endpoints(123)


class Segments(unittest.TestCase):
    """path_segments：直线段序列（曲线近似、闭合补回段）。"""

    def test_lines_and_moves(self):
        self.assertEqual(P.path_segments("M 0 0 L 10 0 L 10 10"),
                         [(0.0, 0.0, 10.0, 0.0), (10.0, 0.0, 10.0, 10.0)])

    def test_relative_and_single_axis(self):
        self.assertEqual(P.path_segments("M 10 20 h 5 v 4"),
                         [(10.0, 20.0, 15.0, 20.0), (15.0, 20.0, 15.0, 24.0)])

    def test_curve_approximated_by_endpoints(self):
        self.assertEqual(P.path_segments("M 0 0 C 5 5 5 5 10 0"),
                         [(0.0, 0.0, 10.0, 0.0)])

    def test_closed_adds_return_segment(self):
        self.assertEqual(P.path_segments("M 0 0 L 10 0 Z"),
                         [(0.0, 0.0, 10.0, 0.0), (10.0, 0.0, 0.0, 0.0)])

    def test_move_breaks_connection(self):
        # M 移动不画线：子路径起点不连到上一段终点
        self.assertEqual(P.path_segments("M 0 0 L 10 0 M 20 20 L 30 20"),
                         [(0.0, 0.0, 10.0, 0.0), (20.0, 20.0, 30.0, 20.0)])

    def test_non_string_rejected(self):
        with self.assertRaises(ValueError):
            P.path_segments(None)


if __name__ == "__main__":
    unittest.main()
