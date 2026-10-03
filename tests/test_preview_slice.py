"""--only 的子集构造：切页、重基时间轴、带出 stage:"keep" 接续链。

纯函数测试，不碰 ffmpeg / 渲染。页面窗口口径（结束 = 下一页开口说话之前）
与 html_renderer 的 clip["vis"] 同源，这里把它钉住。
"""
import copy
import unittest

import _helpers as H
from _manifest_schema import validate_timing_manifest
from _preview_slice import build_subset, chain_sids, subset_images


def _manifest():
    m = H.make_manifest(H.sample_source())
    # opening [0,2) seg-a [2.4,6.8) seg-b [7.2,11.6) closing [12,14)，gap 0.4
    return m


def _images(**kw):
    base = {sid: {"src": f"images/{sid}.svg"}
            for sid in ("opening", "seg-a", "seg-b", "closing")}
    for sid, extra in kw.items():
        base[sid].update(extra)
    return base


class ChainSids(unittest.TestCase):
    def test_plain_page_is_a_chain_of_one(self):
        self.assertEqual(chain_sids(_manifest(), _images(), "seg-a"), ["seg-a"])

    def test_keep_pulls_predecessors_in_order(self):
        m = _manifest()
        imgs = _images(**{"seg-a": {"stage": "keep"}, "seg-b": {"stage": "keep"}})
        self.assertEqual(chain_sids(m, imgs, "seg-b"),
                         ["opening", "seg-a", "seg-b"])

    def test_unknown_sid_lists_what_exists(self):
        with self.assertRaises(ValueError) as cm:
            chain_sids(_manifest(), _images(), "seg-z")
        self.assertIn("seg-a", str(cm.exception))

    def test_first_page_declaring_keep_has_nothing_to_carry(self):
        with self.assertRaises(ValueError) as cm:
            chain_sids(_manifest(), _images(opening={"stage": "keep"}), "opening")
        self.assertIn("第一段", str(cm.exception))

    def test_missing_images_mapping_does_not_crash_the_walk(self):
        self.assertEqual(chain_sids(_manifest(), {}, "seg-a"), ["seg-a"])


class BuildSubset(unittest.TestCase):
    def test_middle_page_window_stops_at_next_speech(self):
        _subset, (t0, t1) = build_subset(_manifest(), _images(), "seg-a")
        self.assertEqual((t0, t1), (2.4, 7.2))

    def test_last_page_window_keeps_the_films_trailing_pad(self):
        m = _manifest()
        m["total_duration"] = 15.0          # 片尾多留 1s
        _subset, (t0, t1) = build_subset(m, _images(), "closing")
        self.assertEqual((t0, t1), (12.0, 15.0))

    def test_times_rebase_but_indices_stay(self):
        subset, (t0, _t1) = build_subset(_manifest(), _images(), "seg-a")
        self.assertEqual([(s["index"], s["start_time"]) for s in subset["sentences"]],
                         [(1, 0.0), (2, 2.4)])
        # 段内与顶层引用同一批对象：契约逐字段比对两层时间轴，各算一遍迟早打架
        self.assertIs(subset["segments"][0]["sentences"][1], subset["sentences"][1])

    def test_subset_passes_its_own_contract(self):
        subset, (t0, t1) = build_subset(_manifest(), _images(), "seg-a")
        subset["total_duration"] = round(t1 - t0, 3)
        self.assertIs(validate_timing_manifest(subset), subset)

    def test_chain_subset_covers_every_pulled_page(self):
        m = _manifest()
        subset, (t0, t1) = build_subset(
            m, _images(**{"seg-b": {"stage": "keep"}}), "seg-b")
        self.assertEqual([s["id"] for s in subset["segments"]], ["seg-a", "seg-b"])
        self.assertEqual((t0, t1), (2.4, 12.0))
        subset["total_duration"] = round(t1 - t0, 3)
        validate_timing_manifest(subset)

    def test_closing_cta_only_travels_with_the_closing_page(self):
        m = _manifest()
        self.assertIn("closing_cta", build_subset(m, _images(), "closing")[0])
        self.assertNotIn("closing_cta", build_subset(m, _images(), "seg-a")[0])

    def test_degraded_flags_come_along(self):
        m = _manifest()
        m["status"] = "degraded"
        m["degraded"] = {"tts_silence_fallback": {"count": 1}}
        subset, _ = build_subset(m, _images(), "seg-a")
        self.assertEqual(subset["status"], "degraded")
        self.assertEqual(subset["degraded"], m["degraded"])

    def test_other_segment_fields_survive(self):
        m = _manifest()
        subset, _ = build_subset(m, _images(), "seg-b")
        seg = subset["segments"][0]
        self.assertEqual(seg["layout"], "canvas")
        self.assertEqual(seg["title"], copy.deepcopy(m["segments"][2]["title"]))


class SubsetImages(unittest.TestCase):
    def test_only_chain_entries_survive(self):
        imgs = _images(**{"seg-b": {"stage": "keep"}})
        out = subset_images(imgs, ["seg-a", "seg-b"])
        self.assertEqual(sorted(out), ["seg-a", "seg-b"])
        self.assertEqual(out["seg-b"]["src"], "images/seg-b.svg")

    def test_entries_absent_from_images_json_are_skipped(self):
        self.assertEqual(subset_images({}, ["seg-a"]), {})

    def test_copied_entries_do_not_alias_the_original(self):
        imgs = _images()
        out = subset_images(imgs, ["seg-a"])
        out["seg-a"]["src"] = "changed.svg"
        self.assertEqual(imgs["seg-a"]["src"], "images/seg-a.svg")


if __name__ == "__main__":
    unittest.main()
