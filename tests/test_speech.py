#!/usr/bin/env python3
"""句子的发声区间（onset / voiced）：解析、夹算、写进 manifest、契约边界。"""
import os
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _helpers import SKILL_DIR, make_manifest  # noqa: E402,F401

import _speech  # noqa: E402
import html_renderer  # noqa: E402
import _manifest_schema as MS  # noqa: E402


# ffmpeg silencedetect 在真实产物上的 stderr 片段（骨架与真机一致：每条一行、
# 行首是 filter 实例前缀，`silence_duration` 只出现在 end 行）。
_STDERR_LEAD_AND_TAIL = """[Parsed_silencedetect_0 @ 0x55d0] silence_start: 0
[Parsed_silencedetect_0 @ 0x55d0] silence_end: 0.372 | silence_duration: 0.372
[Parsed_silencedetect_0 @ 0x55d0] silence_start: 2.31
[Parsed_silencedetect_0 @ 0x55d0] silence_end: 2.5 | silence_duration: 0.19
"""

# 末尾静到文件结束：ffmpeg 只印 start，没有 end。
_STDERR_TAIL_OPEN = """[Parsed_silencedetect_0 @ 0x55d0] silence_start: 2.31
"""


class ParseSilences(unittest.TestCase):
    def test_pairs_in_order(self):
        self.assertEqual(_speech._parse_silences(_STDERR_LEAD_AND_TAIL),
                         [(0.0, 0.372), (2.31, 2.5)])

    def test_open_tail_has_none_end(self):
        # end 未知（静到文件结束）时记 None，由 voiced_span 按文件长度收口。
        self.assertEqual(_speech._parse_silences(_STDERR_TAIL_OPEN),
                         [(2.31, None)])

    def test_no_silence(self):
        self.assertEqual(_speech._parse_silences(""), [])
        self.assertEqual(_speech._parse_silences(None), [])

    def test_ignores_unrelated_lines(self):
        text = ("Input #0, wav, from 'a.wav':\n"
                "  Duration: 00:00:02.50, bitrate: 384 kb/s\n"
                "[Parsed_silencedetect_0 @ 0x1] silence_start: 1.0\n")
        self.assertEqual(_speech._parse_silences(text), [(1.0, None)])

    def test_dangling_start_then_new_start_is_not_merged(self):
        # 畸形输出里连着两条 start：第二段不套用第一条的起点。
        text = ("silence_start: 1.0\n"
                "silence_start: 3.0\n"
                "silence_end: 3.5 | silence_duration: 0.5\n")
        self.assertEqual(_speech._parse_silences(text), [(3.0, 3.5)])

    def test_two_trailing_silences_do_not_double_count(self):
        # 句尾有两段静音（中间夹 1 帧噪声）时，只按最后那段收尾。
        text = ("silence_start: 2.0\nsilence_end: 2.2\n"
                "silence_start: 2.4\nsilence_end: 2.5\n")
        self.assertEqual(_speech._parse_silences(text),
                         [(2.0, 2.2), (2.4, 2.5)])


class VoicedSpan(unittest.TestCase):
    def test_lead_and_tail_excluded(self):
        onset, voiced = _speech.voiced_span([(0.0, 0.4), (2.3, 2.5)], 2.5)
        self.assertEqual(onset, 0.4)
        self.assertEqual(voiced, 1.9)          # 2.5 − 0.4 − 0.2

    def test_onset_is_first_speech_not_every_silence(self):
        # 句中停顿（1.0~1.3）不计入 onset，也不从 voiced 里扣——那是在说话。
        onset, voiced = _speech.voiced_span([(0.0, 0.2), (1.0, 1.3)], 2.0)
        self.assertEqual(onset, 0.2)
        self.assertEqual(voiced, 1.8)

    def test_no_leading_silence_onset_zero(self):
        # 开口就是话（没有前导静音），句中一段停顿：onset=0，voiced=整块长度
        # （句中停顿仍是"在说话"，不该从分母里扣掉）。
        onset, voiced = _speech.voiced_span([(1.5, 1.8)], 2.0)
        self.assertEqual(onset, 0.0)
        self.assertEqual(voiced, 2.0)

    def test_open_tail_clamped_by_file_length(self):
        onset, voiced = _speech.voiced_span([(2.0, None)], 2.5)
        self.assertEqual(voiced, 2.0)

    def test_tail_silence_excluded(self):
        # 句尾静到文件结束（end=None）：只扣那一段，不把整块当发声。
        onset, voiced = _speech.voiced_span([(0.0, 0.2), (2.0, None)], 2.5)
        self.assertEqual(onset, 0.2)
        self.assertEqual(voiced, 1.8)

    def test_all_silent_is_not_measurable(self):
        # 整句静音（TTS 兜底、或测试桩里的全零 WAV）没有"发声区间"可言：
        # 量不出来 → 返回 None，调用方退回文件长度，manifest 仍合法。
        self.assertIsNone(_speech.voiced_span([(0.0, None)], 1.2))

    def test_remaining_room_is_the_ceiling(self):
        # 前导静音几乎吃掉整块（只剩 40ms）：voiced 不能取 100ms 的下限（那会
        # onset+voiced>duration，契约层整份拒收），只能贴着剩余空间。
        onset, voiced = _speech.voiced_span([(0.0, 0.16)], 0.2)
        self.assertEqual((onset, voiced), (0.16, 0.04))
        # 剩得连 100ms 都不够的整句静音：判"量不出来"，退回文件长度。
        self.assertIsNone(_speech.voiced_span([(0.0, None)], 0.06))

    def test_quantized_to_frame_grid(self):
        # 0.372/2.5 这类值必须落在 20ms 网格上，且 voiced 仍为 ≤ duration 的网格值。
        onset, voiced = _speech.voiced_span([(0.0, 0.372)], 2.5)
        for v in (onset, voiced):
            self.assertAlmostEqual(v / _speech._FRAME,
                                   round(v / _speech._FRAME), places=6)
        self.assertLessEqual(voiced, 2.5)

    def test_never_exceeds_duration(self):
        onset, voiced = _speech.voiced_span([(0.0, 0.1), (9.0, 12.0)], 1.0)
        self.assertLessEqual(onset + voiced, 1.0 + 1e-9)

    def test_zero_length_is_none(self):
        self.assertIsNone(_speech.voiced_span([], 0.0))


class MeasureOnRealWav(unittest.TestCase):
    """端到端：造一个"300ms 静音 + 音 + 300ms 静音"的 WAV，看量出来的边界。"""

    def _make(self, path, lead, tone, tail, rate=24000):
        import math
        import struct
        frames = []
        for _ in range(int(lead * rate)):
            frames.append(0)
        for i in range(int(tone * rate)):
            frames.append(int(8000 * math.sin(2 * math.pi * 220 * i / rate)))
        for _ in range(int(tail * rate)):
            frames.append(0)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(struct.pack("<%dh" % len(frames), *frames))

    def test_lead_and_tail(self):
        ff = None
        try:
            import shutil
            ff = shutil.which("ffmpeg")
        except Exception:
            pass
        if not ff:
            self.skipTest("没有 ffmpeg")
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.wav")
            self._make(p, 0.30, 1.0, 0.30)
            span = _speech.measure_voiced_span(ff, p, 1.6)
        self.assertIsNotNone(span)
        onset, voiced = span
        self.assertAlmostEqual(onset, 0.30, delta=0.03)
        self.assertAlmostEqual(voiced, 1.0, delta=0.05)

    def test_non_wav_gives_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.txt")
            with open(p, "w") as f:
                f.write("not audio")
            self.assertIsNone(_speech.measure_voiced_span("ffmpeg", p, 2.0))


class ManifestContract(unittest.TestCase):
    def _manifest_with(self, **fields):
        m = make_manifest()
        s = m["sentences"][0]
        s.update(fields)
        seg = m["segments"][0]["sentences"][0]
        seg.update(fields)                  # 段内副本同步，避免撞一致性校验
        return m

    def test_accepts_pair(self):
        m = self._manifest_with(onset=0.2, voiced=1.5)
        MS.validate_timing_manifest(m)

    def test_rejects_onset_without_voiced(self):
        with self.assertRaises(ValueError) as cm:
            MS.validate_timing_manifest(self._manifest_with(onset=0.2))
        self.assertIn("voiced", str(cm.exception))

    def test_rejects_voiced_without_onset(self):
        with self.assertRaises(ValueError) as cm:
            MS.validate_timing_manifest(self._manifest_with(voiced=1.5))
        self.assertIn("onset", str(cm.exception))

    def test_rejects_voiced_longer_than_duration(self):
        with self.assertRaises(ValueError) as cm:
            MS.validate_timing_manifest(self._manifest_with(onset=0.0, voiced=99.0))
        self.assertIn("duration", str(cm.exception))

    def test_rejects_negative_onset(self):
        with self.assertRaises(ValueError):
            MS.validate_timing_manifest(self._manifest_with(onset=-0.5, voiced=1.0))

    def test_rejects_span_on_silence_fallback(self):
        with self.assertRaises(ValueError) as cm:
            MS.validate_timing_manifest(
                self._manifest_with(onset=0.1, voiced=1.0, synth_failed=True))
        self.assertIn("synth_failed", str(cm.exception))

    def test_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            MS.validate_timing_manifest(
                self._manifest_with(onset="0.2", voiced=1.0))


class CueAndVerseUseOnset(unittest.TestCase):
    def _sents(self, **kw):
        base = {"index": 0, "text": "第一句。", "start_time": 0.0, "duration": 2.0}
        base.update(kw)
        return [base, {"index": 1, "text": "第二句。", "start_time": 2.4,
                       "duration": 2.0, "onset": 0.3, "voiced": 1.4}]

    def test_cue_starts_at_speech_onset(self):
        js = html_renderer._build_subtitle_cues(self._sents())
        self.assertIn("t:2.70", js)          # 2.4 + 0.3
        self.assertNotIn("si:1,dt:[2.40", js)

    def test_cue_without_onset_keeps_old_behaviour(self):
        js = html_renderer._build_subtitle_cues([
            {"index": 0, "text": "一句。", "start_time": 1.0, "duration": 2.0},
            {"index": 1, "text": "二句。", "start_time": 3.4, "duration": 2.0},
        ])
        self.assertIn("t:1.00", js)
        self.assertIn("t:3.40", js)


class RenderedHtmlCarriesSpan(unittest.TestCase):
    """端到端：manifest 带 onset/voiced 时，渲染出的行必须带上对应的属性。"""

    def _render(self, mutate):
        import contextlib
        import io
        from _helpers import sample_images
        m = make_manifest()
        for s in m["sentences"]:
            mutate(s)
        for seg in m["segments"]:
            for s in seg["sentences"]:
                mutate(s)
        MS.validate_timing_manifest(m)
        with contextlib.redirect_stderr(io.StringIO()):
            return html_renderer.generate_html(
                m, "audio/combined.wav", images=sample_images(m))

    def test_onset_moves_t0_and_voiced_is_written(self):
        def mutate(s):
            s["onset"], s["voiced"] = 0.25, 1.5
        html = self._render(mutate)
        self.assertIn('data-voiced="1.50"', html)
        # 有字幕行的句子（槽位版式）t0 都该后移到 start_time + 0.25；
        # canvas 段没有句子流 DOM，不在其中（样例里是第 3、4 句）。
        m = make_manifest()
        for s in m["sentences"]:
            if s["index"] in (3, 4):
                continue
            self.assertIn(f'data-t0="{round(s["start_time"] + 0.25, 2):.2f}"', html)

    def test_no_span_keeps_old_markup(self):
        html = self._render(lambda s: None)
        # runtime.js 源码里也有这个字面量（它读属性），所以只看字幕行本身。
        self.assertNotIn('class="verse-line" data-i="0" data-t0="0.00" data-t1="2.00" data-voiced',
                         html)


if __name__ == "__main__":
    unittest.main()
