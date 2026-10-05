"""Remotion 后端桥（scripts/gen_remotion_project.py）的单元测试。

重点是与 HTML 后端（html_renderer.generate_html 的 clips 预处理）的**几何一致性**
——两后端对同一 manifest 必须算出同一套 wipe/win_start/vis/peel，否则换渲染后端
时转场错位；以及 agenda 行/生成产物形状的确定性。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402
from gen_remotion_project import (  # noqa: E402
    build_agenda_rows, compute_clips, main as gen_main,
)
from _template import load_template  # noqa: E402
from _manifest_schema import load_timing_manifest  # noqa: E402


def _clips(manifest):
    return compute_clips(manifest, load_template())


class ClipGeometryParity(unittest.TestCase):
    """compute_clips 与 html_renderer 的 clips 预处理逐条对齐。"""

    def test_first_card_wipe_is_propline_duration(self):
        m = H.make_manifest()
        clips = _clips(m)
        # line 档（默认）：首卡 prev_end=None → wipe = propLine.duration = 0.40
        self.assertEqual(clips[0]["wipe"], 0.40)
        self.assertEqual(clips[0]["win_start"], 0.0)

    def test_wipe_clamped_by_gap(self):
        from _segments import seg_layout
        m = H.make_manifest()
        clips = _clips(m)
        # 样本 gap=0.4：非 canvas 的后续段 wipe = min(0.40, 0.4) = 0.40
        for clip in clips[1:]:
            if seg_layout(clip["seg"]) != "canvas":
                self.assertEqual(clip["wipe"], 0.40)
        # win_start = s − wipe
        self.assertEqual(
            clips[1]["win_start"],
            round(clips[1]["start"] - clips[1]["wipe"], 2))

    def test_canvas_wipe_zero_and_hard_cut_at_own_start(self):
        m = H.make_manifest()
        clips = _clips(m)
        canvas = next(c for c in clips if c["seg"]["id"] == "seg-b")
        closing = next(c for c in clips if c["seg"]["id"] == "closing")
        self.assertEqual(canvas["wipe"], 0.0)
        self.assertEqual(canvas["win_start"], canvas["start"])
        # 中间卡的窗口到下一卡起点为止（closing 起点 12.0），不是 total
        self.assertEqual(canvas["vis"], round(closing["start"] - canvas["start"], 2))

    def test_last_card_window_reaches_total_duration(self):
        m = H.make_manifest()
        clips = _clips(m)
        last = clips[-1]
        self.assertEqual(round(last["win_start"] + last["vis"], 2),
                         round(m["total_duration"], 2))

    def test_peel_targets_previous_non_canvas_card(self):
        m = H.make_manifest()
        clips = _clips(m)
        # seg-a（slot）被下一张非 canvas 卡揭幕窗口推走：peel 挂在 seg-a 的
        # clip 上、指向 opening；canvas 段自己不生成 peel、也不剥别人。
        peel = next(c for c in clips if c["peel"])
        self.assertEqual(peel["seg"]["id"], "seg-a")
        self.assertEqual(peel["peel"]["sid"], "opening")
        self.assertEqual(peel["peel"]["wipe"], peel["wipe"])
        # 被剥的旧卡要挂底缘暗边
        old = next(c for c in clips if c["seg"]["id"] == "opening")
        self.assertTrue(old["gets_peeled"])


class AgendaRows(unittest.TestCase):
    def _rows(self, manifest, seg_id):
        return build_agenda_rows(
            seg_id, _clips(manifest), manifest,
            load_template()["layout"]["vertical"]["agenda"],
            warn=lambda *a, **k: None)

    def test_opening_rows_list_content_segments_with_duration(self):
        m = H.make_manifest()
        rows, tail = self._rows(m, "opening")
        self.assertEqual([r[1] for r in rows], ["第一件事", "第二件事"])
        self.assertEqual(rows[0][2], "0:04")
        self.assertFalse(tail)

    def test_closing_rows_fallback_to_title_and_cta_tail(self):
        m = H.make_manifest()
        rows, tail = self._rows(m, "closing")
        # 样本 takeaway 齐备；无 takeaway 时回退标题
        self.assertEqual(rows[0][1], "记住第一件事")
        self.assertEqual(rows[1][1], "第二件事")
        self.assertEqual(tail[0][0], "→")
        self.assertEqual(tail[0][1], "关注我们")

    def test_max_rows_drops_tail_first_then_rows(self):
        m = H.make_manifest()
        # 手工造一个 9 段的稿子（maxRows=7）：正文 9 条 + cta → 先丢 cta，再裁 2 条
        src = H.sample_source()
        src["segments"] = [
            {"id": f"seg{i}", "title": f"第{i}件事",
             "text": f"第{i}段的第一句。第{i}段的第二句。"}
            for i in range(1, 10)]
        src["cta"] = "关注我们"
        rows, tail = self._rows(H.make_manifest(src), "closing")
        self.assertEqual(len(rows), 7)
        self.assertFalse(tail)
        self.assertEqual(rows[-1][1], "第7件事")


class GeneratedProjectShape(unittest.TestCase):
    def test_generator_writes_parseable_generated_ts(self):
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(), f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "--out", out, "--aspect", "portrait",
                      "--fps", "24"])
            gen_ts = H.read_text(os.path.join(out, "src", "generated.ts"))
            self.assertIn("export const DATA", gen_ts)
            # 数据胶可独立解析回 JSON（字符串是 JSON 直出的 TS 字面量）
            body = gen_ts[gen_ts.index("{") : gen_ts.rindex("}") + 1]
            data = json.loads(body)
            self.assertEqual(data["fps"], 24)
            self.assertEqual(data["width"], 1080)
            self.assertEqual(data["height"], 1440)
            self.assertEqual(len(data["segments"]), 4)
            opening = next(s for s in data["segments"] if s["id"] == "opening")
            self.assertEqual(opening["layout"], "agenda")
            # clip 几何与 compute_clips 同源
            self.assertIn("winStart", opening)
            # 手写生成产物可再进契约校验（数据胶无损）
            from _manifest_schema import validate_timing_manifest
            manifest_from_data = {
                "schema_version": 2, "status": "ok", "degraded": {},
                "sentences": [], "total_duration": data["totalDuration"],
                "gap": 0.4, "segments": [],
            }
            for s in data["segments"]:
                seg_entry = {
                    "id": s["id"], "title": s["title"],
                    "tagline": s["tagline"] or None,
                    "accent": s["accent"],
                    "sentences": [
                        {"index": x["index"], "text": x["text"],
                         "start_time": x["startTime"], "duration": x["duration"]}
                        for x in s["sentences"]],
                }
                # 契约里 slot 版式 = 不写 layout 键（生成器数据胶归一成 slot）
                if s["layout"] != "slot":
                    seg_entry["layout"] = s["layout"]
                manifest_from_data["segments"].append(seg_entry)
            manifest_from_data["sentences"] = [
                s for seg in data["segments"] for s in [
                    {"index": x["index"], "text": x["text"],
                     "start_time": x["startTime"], "duration": x["duration"]}
                    for x in seg["sentences"]]]
            validate_timing_manifest(manifest_from_data)  # 不抛即通过


if __name__ == "__main__":
    unittest.main()
