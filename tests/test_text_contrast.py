"""CSS 里透明文字色的对比度下限：按主题页底最坏一档算，须过 4.5:1。

判据不另写一份 WCAG 公式：合成用 `_theme.mix`，对比度用 `_theme.contrast_ratio`，
页底候选色标用 `_theme.theme_bg_stops`，下限取 `ensure_text_contrast` 的
`target` 默认值。门禁、取色、这份测试共用同一份判据，才不会两边"恰好相等"。

覆盖 `color:color-mix(in srgb, var(--ctv-text-color) N%, transparent)` 这类写法：
文字色按 alpha 叠在页底渐变上，N 太小时读不清。装饰线、渐变、::before 不在此列，
它们不承担信息；混到 `var(--ctv-bg-color)` 的不透明实色也不在此列。
"""
import inspect
import os
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _theme as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "scripts", "_assets", "composition.css")

# 只认"文字色往 transparent 混"这一种写法，且限定 --ctv-text-color：
# 其它 color-mix 混的是 accent 或背景色，是装饰，不承担可读性契约。
TRANSPARENT_TEXT = re.compile(
    r"color:\s*color-mix\(in srgb,\s*var\(--ctv-text-color\)\s*(\d+)%\s*,\s*transparent\)"
)


def floor():
    """下限只此一份：ensure_text_contrast 的默认 target。"""
    return inspect.signature(T.ensure_text_contrast).parameters["target"].default


def worst(alpha, theme):
    """把 alpha 透明文字色合成到该主题每一档页底，取最坏对比度。"""
    text = T.get_theme_colors(theme)["text_color"]
    stops = T.theme_bg_stops(theme)
    return min(T.contrast_ratio(T.mix(bg, text, alpha), bg) for bg in stops)


def alphas():
    with open(CSS, encoding="utf-8") as f:
        return [int(m) / 100 for m in TRANSPARENT_TEXT.findall(f.read())]


class TransparentTextContrast(unittest.TestCase):
    """行尾时长等次要文字：淡可以，淡到读不清不行。"""

    def test_every_transparent_text_color_meets_floor(self):
        """CSS 里每一处透明文字色，对每个主题的最坏页底都过 4.5:1。"""
        found = alphas()
        self.assertTrue(found, "没匹配到透明文字色——正则或 CSS 写法变了，别让这条锁空转")
        for theme in T._THEMES:
            stops = T.theme_bg_stops(theme)
            self.assertTrue(stops, f"{theme} 主题的页底色标解析不出来")
            for a in found:
                with self.subTest(theme=theme, alpha=a):
                    self.assertGreaterEqual(
                        worst(a, theme), floor(),
                        f"{theme} 页底上 {a:.0%} 文字色对比度不足（{worst(a, theme):.3f}:1）")

    def test_secondary_hierarchy_is_not_flattened(self):
        """次要文字仍要明显弱于主文字：别拿 100% 实色把层级抹平。"""
        for theme in T._THEMES:
            with self.subTest(theme=theme):
                main = worst(1.0, theme)
                for a in alphas():
                    self.assertLess(worst(a, theme), main * 0.5,
                                    f"{theme} 上 {a:.0%} 已经亮到接近主文字，行尾信息会抢行名")

    def test_floor_is_the_shared_target(self):
        """下限来自 ensure_text_contrast 的默认值，不是这里另写的常数。"""
        self.assertEqual(floor(), 4.5)


if __name__ == "__main__":
    unittest.main()
