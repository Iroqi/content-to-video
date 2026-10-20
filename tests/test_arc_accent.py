"""情绪色温弧：未显式写 accent 的段沿一条弧取色，而不是每段随机换一支。

这里锁三件事：
1. 弧的**形状**：首段偏冷、中段最暖、尾段回冷；相邻两段必须读得出区别
   （同色 = 那两页读成一段）；"颜色只表达不同"和"颜色表达进度"是两种片子，
   这条测试就是两者的分界线。
2. **退化不崩**：0 / 1 段、色板退化时拿得到合法值。
3. **显式 accent 不被弧挤走**，也不把弧拉长（作者写了色就不能再被改）。
"""
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _theme as T
from build_from_structured import build_parts


def _lum(hex_color):
    return T.relative_luminance(hex_color)


def _close(a, b):
    """两支色"读得出不一样"的下限：sRGB 通道至少差 6（≈ 2.3% 色阶）。"""
    pa, pb = T._hex_channels(a), T._hex_channels(b)
    return max(abs(x - y) for x, y in zip(pa, pb)) >= 6


class ArcShape(unittest.TestCase):
    def test_positions_are_in_range_and_monotonic(self):
        for count in range(1, 12):
            pos = T.arc_accent_indices(count)
            self.assertEqual(len(pos), count, count)
            self.assertEqual(pos, sorted(pos), f"count={count} 不单调：{pos}")
            self.assertEqual(pos[0], 0.0, f"count={count} 首段不在弧首：{pos}")

    def test_adjacent_segments_read_as_different_tones(self):
        for count in range(2, 12):
            cols = [T.arc_accent(p) for p in T.arc_accent_indices(count)]
            for a, b in zip(cols, cols[1:]):
                self.assertTrue(_close(a, b), f"count={count} 相邻太像：{a} {b}")

    def test_most_light_lands_mid_arc(self):
        """足够长的片子，最亮那一支落在中段（不在首尾）——它是"峰值段"的信号。

        五段以下例外：那时峰值落在收尾段也算对（冷开场 → … → 峰值收束），
        把峰值硬塞进中间会让结尾变成一条平淡的冷色。
        """
        for count in (5, 6, 7, 8, 9, 10):
            pos = T.arc_accent_indices(count)
            lums = [_lum(T.arc_accent(p)) for p in pos]
            # 平手容差：浮点误差可能让"中间"与"端点"差不到千分之二，
            # 只要求**有一支接近峰值的落在中间**，判据是"峰值在不在两端"。
            near = [i for i, v in enumerate(lums) if v >= max(lums) - 0.002]
            self.assertTrue(any(0 < i < len(lums) - 1 for i in near),
                            f"count={count} 峰值落在首尾：{pos} lum={lums}")

    def test_short_form_ends_on_the_peak(self):
        pos = T.arc_accent_indices(3)
        self.assertEqual(_lum(T.arc_accent(pos[-1])),
                         max(_lum(T.arc_accent(p)) for p in pos))

    def test_six_steps_land_exactly_on_the_cycle(self):
        self.assertEqual([T.arc_accent(p) for p in T.arc_accent_indices(6)],
                         [T.get_accent_palette()[i] for i in T._ARC_CYCLE])


class ArcAccent(unittest.TestCase):
    def test_integer_position_is_exact_palette_color(self):
        pal = T.get_accent_palette()
        for k, idx in enumerate(T._ARC_CYCLE):
            self.assertEqual(T.arc_accent(float(k)), pal[idx])

    def test_midpoint_mixes_like_color_mix(self):
        got = T.arc_accent(0.5)
        a = T._hex_channels(T.get_accent_palette()[T._ARC_CYCLE[0]])
        b = T._hex_channels(T.get_accent_palette()[T._ARC_CYCLE[1]])
        self.assertEqual(
            T._hex_channels(got),
            tuple(int(round(x + (y - x) * 0.5)) for x, y in zip(a, b)))

    def test_out_of_range_is_clamped_not_wrapped(self):
        pal = T.get_accent_palette()
        self.assertEqual(T.arc_accent(99), pal[T._ARC_CYCLE[-1]])
        self.assertEqual(T.arc_accent(-3), pal[T._ARC_CYCLE[0]])


class ArcDegenerate(unittest.TestCase):
    def test_zero_and_one(self):
        self.assertEqual(T.arc_accent_indices(0), [])
        self.assertEqual(T.arc_accent_indices(1), [0.0])

    def test_cycle_of_one_does_not_divide_by_zero(self):
        saved = T._ARC_CYCLE
        try:
            T._ARC_CYCLE = (2,)
            self.assertEqual(T.arc_accent_indices(4), [0.0] * 4)
            self.assertEqual(T.arc_accent(0), T.get_accent_palette()[2])
        finally:
            T._ARC_CYCLE = saved


class ArcInBuild(unittest.TestCase):
    """build_parts 里的接线：弧按"没写 accent 的段"排，显式色原样保留。"""

    def _src(self, accents):
        return {"segments": [
            {"title": "段%d" % i, "text": "这是第%d段的正文内容。" % i,
             **({"accent": a} if a else {})}
            for i, a in enumerate(accents, 1)
        ]}

    def test_explicit_accent_wins_and_unset_ones_share_the_arc(self):
        _, segs = build_parts(self._src([None, "#123456", None]))
        self.assertEqual(segs[1]["accent"], "#123456")
        pos = T.arc_accent_indices(2)
        self.assertEqual(segs[0]["accent"], T.arc_accent(pos[0]))
        self.assertEqual(segs[2]["accent"], T.arc_accent(pos[1]))

    def test_all_unset_follows_arc_order(self):
        _, segs = build_parts(self._src([None] * 3))
        self.assertEqual([s["accent"] for s in segs],
                         [T.arc_accent(p) for p in T.arc_accent_indices(3)])

    def test_structural_pages_keep_default_accent(self):
        src = self._src([None, None])
        src["opening"] = "开场一句。"
        src["closing"] = "结尾一句。"
        _, segs = build_parts(src)
        by_id = {s["id"]: s for s in segs}
        self.assertEqual(by_id["opening"]["accent"], T.get_default_accent())
        self.assertEqual(by_id["closing"]["accent"], T.get_default_accent())

    def test_generated_accent_is_contract_safe(self):
        """弧产出的每一支都必须能通过契约层的 accent 白名单。"""
        from _validate import _validate_accent
        _, segs = build_parts(self._src([None] * 9))
        for s in segs:
            _validate_accent(s["accent"], "accent")


if __name__ == "__main__":
    unittest.main()
