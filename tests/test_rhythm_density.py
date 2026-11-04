"""入场的两维节奏：**快慢**（每一拍演多久）与**疏密**（各拍隔多远）。

前沿 MG 的节奏不是"所有元素用同一套时长"。同一份稿子里，2 秒的段要干脆利落，
8 秒的段要慢慢铺开——观众读到的是"这一页的呼吸和那一页不同"，而不是全片一条
匀速的传送带。这一层把它拆成两个**互不干涉**的旋钮：

- **快慢** = 每一拍**演多久** → `entranceBudget`（`_entrance_k`，动的是时长）
- **疏密** = 各拍**隔多远** → `entranceLadder`（步距，动的是间隔）

**为什么要分成两维**：若间隔也跟着拍长等比缩放，那一页里各层的相对位置与整体
时长同比伸缩，看起来只是"整体快一点/慢一点"——密度没变，观众读到的仍是均匀
节奏。所以两条曲线必须分开走，且**改一个不许动另一个**。

四组用例各锁一条：

- `TempoAndDensityAreSeparate`：两个旋钮各管一维（改拍长不动间隔，反之亦然）——
  焊在一起就成了"一个旋钮两种效果"，作者调不准。
- `DensityOpensUpOnLongSegments`：长段的间隔比中段宽；短段的间隔**不收窄**（感知
  下限，见 `tests/test_motion_design.py` 的
  `EntranceLadder.test_step_is_not_compressed_away_on_a_short_segment`）。
- `TempoIsCappedAtTheCalmCeiling`：拍长到 `calmSeconds` 收顶——再长的段也不该
  慢成"卡住"。
- `EveryLongSegmentStillPassesTheLadder`：拉宽之后仍满足既有阶梯判据（间隔
  ≥2 帧、≤0.30s、后一拍压在前一拍里）。

阶梯的解析与阈值**复用** `tests/test_motion_design.py` 的那一份（同一套判据不许
有两套数），本文件只加"随段长怎么变"这一层。
"""
import contextlib
import io
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _template as T
import test_motion_design as M
from html_renderer import generate_html

FPS = 30
# 阶梯的既有判据（与 test_motion_design 同一份口径）：
# "先后"与"同时"的分界是 2 帧；之上 0.30s 就是逐条出现，一页 PPT。
LADDER_MIN_STEP_FRAMES = 2
LADDER_MAX_STEP = 0.30


def _render(dur=2.0):
    """一份稿子，`dur` 是**每句**的秒数（`make_manifest` 的口径）。

    段长 = 该段句数 × dur + 句间 gap，所以同一句数下 `dur` 就是段长的旋钮。
    """
    m = H.make_manifest(H.sample_source(), dur=dur)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=H.sample_images(m),
                             aspect="portrait", theme="dark")


def _layers(dur):
    """这一页的三层入场：`[(起点, 时长), …]`，按时间排（读序）。"""
    return [(x["at"], x["dur"]) for x in M._ladder_layers(_render(dur), "seg-a")]


class TempoAndDensityAreSeparate(unittest.TestCase):
    """两个旋钮各管一维：改一个不许动另一个。"""

    def test_changing_the_tempo_leaves_the_gap_alone(self):
        """改拍长（`entranceBudget`）不许动间隔。

        两维焊在一起的形态是：作者只想让长段慢一点，间隔也跟着变宽——那一页
        从"慢一点"变成"散开"，两件不同的事一起发生，调不准也说不清。
        """
        base = _layers(3.0)
        gap_base = base[-1][0] - base[-2][0]
        with T.override_layer(["animation.entranceBudget.normSeconds=8.0"]):
            got = _layers(3.0)
        self.assertAlmostEqual(got[-1][0] - got[-2][0], gap_base, places=3,
                               msg="改拍长把间隔也动了：两维焊在一起了")

    def test_changing_the_density_leaves_the_beat_alone(self):
        """改间隔（`entranceLadder`）不许动拍长。"""
        base = _layers(3.0)
        with T.override_layer(["animation.entranceLadder.spread=0.0"]):
            got = _layers(3.0)
        self.assertAlmostEqual(got[0][1], base[0][1], places=3,
                               msg="改间隔把拍长也动了：两维焊在一起了")


class DensityOpensUpOnLongSegments(unittest.TestCase):
    """长段更疏，短段不收窄。"""

    def test_long_segments_spread_the_layers_further_apart(self):
        """同一份稿子，只改段长：长段上画面层比副标题晚得更多。

        这是"疏密"读得出来的唯一形态——间隔的**相对宽度**变了。若只是整体时长
        变了而间隔比例不变，观众读到的是"快一点/慢一点"，那是快慢那一维。
        """
        mid = _layers(1.8)
        long_ = _layers(6.0)
        self.assertEqual(len(mid), 3, f"中段应当有三层：{mid}")
        self.assertEqual(len(long_), 3, f"长段应当有三层：{long_}")
        gap_mid = mid[-1][0] - mid[-2][0]
        gap_long = long_[-1][0] - long_[-2][0]
        self.assertGreater(gap_long, gap_mid,
                           f"长段的间隔没有比中段宽：{gap_mid:.3f} → {gap_long:.3f}")

    def test_short_segments_keep_the_base_gap(self):
        """短段不收窄间隔：它是"看得出先后"的感知下限。

        压到一帧以内等于把这一层关掉——短段本来就拍子短（那一维已经压紧了），
        密度已经够了；只有长段才是需要"拉开"的那一端。
        """
        short = _layers(0.4)
        self.assertEqual(len(short), 3, f"短段仍应有三层：{short}")
        gap = short[-1][0] - short[-2][0]
        self.assertGreaterEqual(gap, LADDER_MIN_STEP_FRAMES / FPS - 1e-9,
                                f"短段上画面层只比副标题晚 {gap * 1000:.0f}ms："
                                "间隔被段长收窄了")


class TempoIsCappedAtTheCalmCeiling(unittest.TestCase):
    """拍长到 `calmSeconds` 收顶。"""

    def test_a_very_long_segment_stops_accelerating(self):
        """20 秒的段与 12 秒的段拍长相同——再长也不该慢成"卡住"。

        观众对"入场演了多久"的容忍随段长涨，但只在某一段区间内涨。一个 40 秒
        的段落让标题慢慢走两秒是"卡住"，不是"从容"。
        """
        calm = _layers(6.0)
        longer = _layers(10.0)
        self.assertAlmostEqual(longer[0][1], calm[0][1], places=3,
                               msg="超过 calmSeconds 之后拍长还在涨：节奏成了段长的函数")


class EveryLongSegmentStillPassesTheLadder(unittest.TestCase):
    """拉宽之后仍满足既有阶梯判据。"""

    def test_the_widest_gap_is_still_inside_the_bounds(self):
        """最宽的那一跳仍在 [2 帧, 0.30s] 里，且后一拍压在前一拍里。

        这条是本层的安全网：疏密放宽了间隔，若放宽过头就变成"逐条出现"——
        一页 PPT。判据与 `tests/test_motion_design.py` 同一套阈值。
        """
        for dur in (0.4, 1.0, 1.8, 3.0, 6.0, 10.0):
            layers = M._ladder_layers(_render(dur), "seg-a")
            self.assertEqual(len(layers), 3, f"dur={dur}: 应当有三层 {layers}")
            for i in range(1, len(layers)):
                prev, cur = layers[i - 1], layers[i]
                step = cur["at"] - prev["at"]
                self.assertGreaterEqual(step, LADDER_MIN_STEP_FRAMES / FPS - 1e-9,
                                        f"dur={dur}: 第 {i} 层只晚 {step * 1000:.0f}ms")
                self.assertLessEqual(step, LADDER_MAX_STEP + 1e-9,
                                     f"dur={dur}: 第 {i} 层晚 {step:.2f}s，是逐条出现")
                self.assertLess(cur["at"], prev["at"] + prev["dur"] - 1e-9,
                                f"dur={dur}: 第 {i} 层起于上一层收尾之后")
