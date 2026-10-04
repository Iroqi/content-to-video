"""导演节拍 ↔ 旁白对轴：解析收口、窗外 warn、--beat-report 的回归。

这里锁的是三件事：
1. 落点解析只有一份实现（_timeline.beat_positions），渲染端与门禁共用；
2. 落在本页可见窗口之外的节拍会 warn（那是"这一拍根本不演"，不是稍微偏）；
3. 句间静音/段尾静音**不报**——它是风格不是 bug，报出来就是噪声。
"""
import os
import tempfile
import unittest

import _helpers as H          # 必须最先导入：它把 scripts/ 挂上 sys.path
from _timeline import (beat_positions, beat_span, beat_cycles, ends_at_start,
                       _beat_spans_time)
import gen_hyperframes as G


def _sent(start, dur=1.0, text="句"):
    return {"start_time": start, "duration": dur, "text": text}


# 两段 × 两句：seg-a 说话 0–3.9s，seg-b 从 5.0s 起（seg-a 的右界就是它）
SEGS = [
    {"id": "seg-a", "sentences": [_sent(0.0, 1.9), _sent(2.4, 1.5)]},
    {"id": "seg-b", "sentences": [_sent(5.0, 2.0), _sent(7.5, 1.0)]},
]

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1440">'
       '<g id="a1"/><g id="a2"/><text id="t1">x</text></svg>')


class Resolve(unittest.TestCase):
    """beat_positions：与渲染端逐字同源的那份解析。"""

    def test_at_lands_on_sentence_start_and_frac_interpolates(self):
        steps = [{"at": 1, "target": "#a1"}, {"at": 1.5, "target": "#a2"}]
        got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)]
        # 第 2 句起 2.4；frac 0.5 = 句时长的一半（语速均匀的线性假设）
        self.assertEqual(got, [2.4, 3.15])

    def test_at_delay_adds(self):
        steps = [{"at": 0, "target": "#a1", "delay": 0.3}]
        self.assertEqual(beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)[0][0], 0.3)

    def test_at_time_numeric_is_relative_to_segment_start(self):
        steps = [{"at_time": 2.0, "target": "#a1"}]
        self.assertEqual(beat_positions(steps, SEGS[0]["sentences"], 7.23, 0.5)[0][0], 9.23)

    def test_relative_at_time_chains_from_previous_beat_end(self):
        # 第一条没有上一条 → 退到段起点；此后从 pos+dur 往前推（没补间的步不推进）
        steps = [{"at_time": 0.1, "target": "#a1", "duration": 0.7, "to": {"opacity": 1}},
                 {"at_time": "+0.25", "target": "#a2", "to": {"opacity": 1}},
                 {"at_time": "-0.1", "target": "#t1", "to": {"opacity": 1}}]
        got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 10.0, 0.5)]
        self.assertEqual(got, [10.1, 11.05, 11.45])

    def test_pure_set_does_not_advance_the_chain(self):
        steps = [{"at_time": 1.0, "target": "#a1", "set": {"opacity": 1}},
                 {"at_time": "+0.5", "target": "#a2"}]
        got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)]
        self.assertEqual(got, [1.0, 1.5])       # 1.0 而非 1.0+0.5+0.5
        # 只写 duration 而没有补间的步同样不推进：游标认的是"这一步真的占时间吗"，
        # 不是"作者写了个时长数字吗"。
        steps2 = [{"at_time": 1.0, "target": "#a1", "duration": 2.0, "set": {"opacity": 1}},
                  {"at_time": "+0.5", "target": "#a2"}]
        self.assertEqual([p for p, _ in beat_positions(steps2, SEGS[0]["sentences"],
                                                      0.0, 0.5)], [1.0, 1.5])

    def test_whole_segment_primitives_advance_by_their_duration(self):
        # morph/count/type 没有 from/to，但各自负责整段，游标照推进 dur
        for key, extra in (("morph", {"morph": {"from": "M0 0L1 1", "to": "M0 1L1 0"}}),
                           ("count", {"count": {"to": 10}}),
                           ("type", {"type": {}})):
            steps = [{"at_time": 0.0, "target": "#a1", "duration": 1.2, **extra},
                     {"at_time": "+0.1", "target": "#a2"}]
            got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)]
            self.assertEqual(got, [0.0, 1.3], key)
            self.assertTrue(_beat_spans_time(steps[0]), key)

    def test_default_duration_used_when_step_omits_it(self):
        steps = [{"at_time": 0.0, "target": "#a1", "to": {"opacity": 1}},
                 {"at_time": "+0.1", "target": "#a2"}]
        got = [d for _, d in beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)]
        self.assertEqual(got, [0.5, 0.5])
        self.assertEqual(beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)[1][0], 0.6)

    def test_at_beyond_sentence_count_raises(self):
        with self.assertRaises(ValueError) as cm:
            beat_positions([{"at": 2, "target": "#a1"}], SEGS[0]["sentences"], 0.0, 0.5)
        self.assertIn("越界", str(cm.exception))


class CycleSpan(unittest.TestCase):
    """`repeat` / `yoyo` 的跨度与收尾：三处判据（游标、出窗、接续）共用的那一份数。"""

    def test_span_and_cycles_match_gsap_repeat_semantics(self):
        # GSAP 的 repeat=N 是"再演 N 遍"，共 N+1 遍（实测 timeline.duration()=2.0 于 repeat:1）
        self.assertEqual(beat_cycles({"repeat": 3}), 4)
        self.assertEqual(beat_span({"repeat": 3}, 0.5), 2.0)
        self.assertEqual(beat_cycles({}), 1)
        self.assertEqual(beat_span({}, 0.5), 0.5)

    def test_infinite_repeat_has_no_span_and_no_cycle_count(self):
        self.assertIsNone(beat_cycles({"repeat": -1}))
        self.assertIsNone(beat_span({"repeat": -1}, 0.5))

    def test_yoyo_ends_back_at_start_only_for_even_cycles(self):
        # 实测 gsap@3.14.2：repeat:1+yoyo 收尾 v=0，repeat:2+yoyo 收尾 v=1
        self.assertTrue(ends_at_start({"repeat": 1, "yoyo": True}))
        self.assertFalse(ends_at_start({"repeat": 2, "yoyo": True}))
        self.assertFalse(ends_at_start({"repeat": 1}))       # 无 yoyo 每遍都停在 to
        self.assertFalse(ends_at_start({"yoyo": True}))      # 单遍无从来回
        self.assertIsNone(ends_at_start({"repeat": -1, "yoyo": True}))

    def test_relative_chain_advances_by_the_whole_loop(self):
        steps = [{"at_time": 0.1, "target": "#a1", "duration": 0.7,
                  "to": {"opacity": 1}, "repeat": 1},
                 {"at_time": "+0.25", "target": "#a2", "to": {"opacity": 1}}]
        got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 10.0, 0.5)]
        self.assertEqual(got, [10.1, 11.75])     # 10.1 + 0.7×2 + 0.25，不是 0.7×1

    def test_infinite_repeat_does_not_block_the_chain(self):
        """无限循环没有终点，游标退回"算它演了一遍"——后面的 "+N" 至少还有个落点。"""
        steps = [{"at_time": 0.0, "target": "#a1", "duration": 0.6,
                  "to": {"opacity": 1}, "repeat": -1},
                 {"at_time": "+0.4", "target": "#a2", "to": {"opacity": 1}}]
        got = [p for p, _ in beat_positions(steps, SEGS[0]["sentences"], 0.0, 0.5)]
        self.assertEqual(got, [0.0, 1.0])


class WindowWarningsRepeat(unittest.TestCase):
    """出窗判定必须拿"总跨度"，只按单遍 duration 判就是把门禁算的时刻差四倍。"""

    def _w(self, steps, next_start=5.0):
        return G._beat_window_warnings(steps, SEGS[0]["sentences"], 0.0,
                                       next_start, 0.5)

    def test_repeated_beat_cut_by_next_page_is_warned_with_its_loop_count(self):
        # 单遍只越过页界 0.1s，演 4 遍就是 6.6s—— warn 要说清它要演几遍
        warns = self._w([{"at_time": 4.6, "target": "#a1", "duration": 0.5,
                          "to": {"opacity": 1}, "repeat": 3}])
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("被切在半路", warns[0])
        self.assertIn("演 4 遍", warns[0])
        self.assertIn("6.60", warns[0])

    def test_infinite_repeat_is_not_reported_as_cut_in_half(self):
        """它本来就要被页界切掉，报出来是噪声；warn 留给"作者以为演完了"那一类。"""
        self.assertEqual(self._w([{"at_time": 4.6, "target": "#a1", "duration": 0.5,
                                   "to": {"opacity": 1}, "repeat": -1}]), [])


class Landing(unittest.TestCase):
    """五类落点：只有 before/after 是"看不见"。"""

    def classify(self, pos, seg=0, next_start=5.0):
        sents = SEGS[seg]["sentences"]
        start = sents[0]["start_time"]
        end = sents[-1]["start_time"] + sents[-1]["duration"]
        return G._beat_landing(pos, sents, start, end, next_start)[0]

    def test_inside_a_sentence(self):
        self.assertEqual(self.classify(2.4), "in")
        self.assertEqual(self.classify(3.9), "in")     # 末句结束点算句内（边界同闭区间）

    def test_between_sentences_is_gap_not_out(self):
        self.assertEqual(self.classify(2.2), "gap")

    def test_tail_silence_is_visible_so_not_out(self):
        # 4.5 > 末句结束 3.9，但下一页 5.0 才盖过来，这一拍照样演
        self.assertEqual(self.classify(4.5), "tail")

    def test_before_and_after(self):
        self.assertEqual(self.classify(-0.2), "before")
        self.assertEqual(self.classify(5.1), "after")

    def test_last_segment_has_no_right_bound(self):
        self.assertEqual(self.classify(30.0, next_start=None), "tail")


class WindowWarnings(unittest.TestCase):
    def _w(self, steps, seg=SEGS[0], next_start=5.0):
        return G._beat_window_warnings(steps, seg["sentences"],
                                       seg["sentences"][0]["start_time"],
                                       next_start, 0.5)

    def test_beat_after_next_page_starts_is_warned(self):
        warns = self._w([{"at_time": 5.6, "target": "#a1"}])
        self.assertEqual(len(warns), 1)
        self.assertIn("已被下一页盖住", warns[0])
        self.assertIn("改用 at", warns[0])            # 文案要给出路，不只报警

    def test_beat_before_this_page_exists_is_warned(self):
        warns = self._w([{"at_time": 0.0, "target": "#a1", "duration": 2.0},
                         {"at_time": "-3.0", "target": "#a2"}])
        self.assertEqual(len(warns), 1)
        self.assertIn("还没出现", warns[0])

    def test_gap_and_tail_beats_stay_silent(self):
        """压在静音上是风格不是 bug；把它报出去，就是在训练作者无视 warn。"""
        for pos in (2.2, 4.5, 0.1, 3.9):
            self.assertEqual(self._w([{"at_time": pos, "target": "#a1"}]), [], pos)

    def test_last_segment_never_warns_about_the_future(self):
        self.assertEqual(self._w([{"at_time": 40.0, "target": "#a1"}],
                                 next_start=None), [])

    def test_beat_cut_by_the_next_page_is_warned(self):
        """起点在窗内、终点越过下一段起点：这一拍被切在半路，终态从没出现过。"""
        warns = self._w([{"at_time": 4.6, "target": "#a1"}])      # 4.6 + 0.5 > 5.0
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("被切在半路", warns[0])
        self.assertIn("重演", warns[0])                            # 文案要给出路

    def test_keep_page_says_the_bake_carries_the_unseen_end(self):
        warns = G._beat_window_warnings([{"at_time": 4.6, "target": "#a1"}],
                                        SEGS[0]["sentences"], 0.0, 5.0, 0.5, keep=True)
        self.assertIn('stage:"keep"', warns[0])

    def test_beat_that_ends_exactly_at_the_boundary_stays_silent(self):
        self.assertEqual(self._w([{"at_time": 4.5, "target": "#a1"}]), [])

    def test_unresolved_at_is_not_double_reported(self):
        # at 越界由句序检查按步报 error，这里静默（避免同一件事两种口径）
        self.assertEqual(self._w([{"at": 9, "target": "#a1"}]), [])


class PrepareIntegration(unittest.TestCase):
    """warn 必须从生成期出，因为判据要 steps + manifest 两份输入。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = os.path.join(self.tmp.name, "images")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "seg-a.svg"), "w", encoding="utf-8") as f:
            f.write(SVG)

    def _prepare(self, at_time):
        images = {"seg-a": {"src": "images/seg-a.svg",
                            "director": {"steps": [
                                {"at_time": at_time, "target": "#a1",
                                 "to": {"opacity": 1}}]}}}
        return G.director_prepare(images, SEGS, self.tmp.name)

    def test_stale_at_time_surfaces_as_warn_not_error(self):
        errs, warns = self._prepare(5.6)        # 落在 seg-b 的旁白里
        self.assertEqual(errs, [])
        hits = [w for w in warns if "已被下一页盖住" in w]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("段落 'seg-a'", hits[0])

    def test_beat_inside_the_page_stays_silent(self):
        errs, warns = self._prepare(2.5)
        self.assertEqual(errs, [])
        self.assertEqual([w for w in warns if "落点" in w], [])


class Report(unittest.TestCase):
    def test_table_lists_each_beat_with_landing_and_tally(self):
        images = {"seg-a": {"src": "images/seg-a.svg", "director": {"steps": [
            {"at_time": 1.0, "target": "#a1", "to": {"opacity": 1}},
            {"at_time": 2.2, "target": "#a2", "to": {"opacity": 1}},
            {"at_time": 6.0, "target": "#t1", "to": {"opacity": 1}}]}}}
        lines = G.beat_report_lines(images, SEGS, 0.5)
        body = "\n".join(lines)
        self.assertIn("[beat] seg-a  旁白 0.00–3.90s（2 句），下一页 5.00s 起", body)
        self.assertIn("in     第1句", body)
        self.assertIn("gap    句间静音", body)
        self.assertIn("after  下一页起点之后（看不见）", body)
        self.assertIn("— 3 拍：in 1 · gap 1 · after 1", lines[-1])

    def test_segments_without_director_are_skipped(self):
        images = {"seg-a": {"src": "images/seg-a.svg"}, "seg-z": {"src": "x.svg"}}
        self.assertEqual(G.beat_report_lines(images, SEGS, 0.5), [])


if __name__ == "__main__":
    unittest.main()
