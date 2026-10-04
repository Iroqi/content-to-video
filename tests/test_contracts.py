import unittest

import _helpers as H
import _segments as SEG
import _timeline as TL
import _source_schema as SRC
import _manifest_schema as MAN
import _images_schema as IMG


def bad(fn, *a, contains=None):
    """断言校验函数抛错（契约层统一抛 ValueError/SystemExit 一类），并可核对报错里的关键词。"""
    try:
        fn(*a)
    except (ValueError, SystemExit, TypeError) as e:
        if contains:
            assert contains in str(e), f"报错里没有 {contains!r}: {e}"
        return
    raise AssertionError("应该被契约层拒绝，却通过了")


class SourceValidation(unittest.TestCase):
    def test_sample_source_ok(self):
        SRC.validate_segments_source(H.sample_source())

    def test_unknown_top_key_rejected(self):
        s = H.sample_source()
        s["closing_text"] = "拼错的结尾键"
        bad(SRC.validate_segments_source, s, contains="closing_text")

    def test_unknown_segment_key_rejected(self):
        s = H.sample_source()
        s["segments"][0]["_note"] = "备注"
        bad(SRC.validate_segments_source, s, contains="_note")

    def test_text_and_dialogue_are_exclusive(self):
        s = H.sample_source()
        s["segments"][0]["dialogue"] = [{"speaker": "a", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)

    def test_dialogue_needs_declared_speakers(self):
        s = H.sample_source()
        s["segments"][0].pop("text")
        s["segments"][0]["dialogue"] = [{"speaker": "host", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        SRC.validate_segments_source(s)

    def test_dialogue_speaker_must_be_declared(self):
        s = H.sample_source()
        s["segments"][0].pop("text")
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        s["segments"][0]["dialogue"] = [{"speaker": "ghost", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)

    def test_invalid_voice_rejected(self):
        s = H.sample_source()
        s["segments"][0]["voice_id"] = "不存在的音色"
        bad(SRC.validate_segments_source, s)

    def test_accent_whitelist(self):
        for ok in ("#fff", "#12ab34", "tomato"):
            s = H.sample_source()
            s["segments"][0]["accent"] = ok
            SRC.validate_segments_source(s)
        for evil in ("rgb(0,0,0)", "var(--x)", "red;}</style>", "tomatoo"):
            s = H.sample_source()
            s["segments"][0]["accent"] = evil
            bad(SRC.validate_segments_source, s)

    def test_content_segment_cannot_declare_agenda_layout(self):
        s = H.sample_source()
        s["segments"][0]["layout"] = "agenda"
        bad(SRC.validate_segments_source, s)

    def test_layout_is_case_sensitive(self):
        s = H.sample_source()
        s["segments"][0]["layout"] = "Canvas"
        bad(SRC.validate_segments_source, s)

    def test_segment_id_rules(self):
        for evil in ('seg"x', "seg.x", "seg x", "x1", "a" * 70):
            s = H.sample_source()
            s["segments"][0]["id"] = evil
            bad(SRC.validate_segments_source, s)

    def test_duplicate_ids_rejected(self):
        s = H.sample_source()
        s["segments"][1]["id"] = s["segments"][0]["id"]
        bad(SRC.validate_segments_source, s)

    # ── 上墙文字 vs 口播稿：分号只对上墙的一行报错 ─────────────────
    def test_top_level_on_screen_keys_reject_semicolon(self):
        for key in SRC.ON_SCREEN_TOP_KEYS:
            for semi in ("；", ";"):
                s = H.sample_source()
                s[key] = "半句话" + semi + "另外半句"
                bad(SRC.validate_segments_source, s, contains=key)

    def test_segment_on_screen_keys_reject_semicolon(self):
        for key in SRC.ON_SCREEN_SEGMENT_KEYS:
            s = H.sample_source()
            s["segments"][0][key] = "半句话；另外半句"
            bad(SRC.validate_segments_source, s, contains="分号")

    def test_cta_rejects_semicolon(self):
        s = H.sample_source()
        s["cta"] = "关注我们；下期见"
        bad(SRC.validate_segments_source, s, contains="cta")

    def test_narration_may_contain_semicolon(self):
        # 口播稿里分号是终止标点（见 _text.split_sentences），念出来两次停顿，
        # 不是"两件事挤一行"，所以门禁必须放它过去。
        s = H.sample_source()
        s["opening"] = "大家好；今天聊两件事。"
        s["closing"] = "谢谢观看;下期再见。"
        s["segments"][0]["text"] = "先讲背景；再讲进展。"
        SRC.validate_segments_source(s)
        s["segments"][0].pop("text")
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        s["segments"][0]["dialogue"] = [{"speaker": "host", "text": "是这样；你同意吗。"}]
        SRC.validate_segments_source(s)


class LayoutDispatch(unittest.TestCase):
    def test_seg_layout(self):
        self.assertEqual(SEG.seg_layout({"id": "seg1"}), "slot")
        self.assertEqual(SEG.seg_layout({"id": "seg1", "layout": "canvas"}), "canvas")
        # 结构性页漏盖 layout 时按 id 兜回 agenda，而不是塌成槽位页
        self.assertEqual(SEG.seg_layout({"id": "opening"}), "agenda")
        self.assertEqual(SEG.seg_layout({"id": "closing", "layout": "canvas"}), "canvas")

    def test_needs_image(self):
        self.assertTrue(SEG.needs_image({"id": "seg1"}))
        self.assertFalse(SEG.needs_image({"id": "opening", "layout": "agenda"}))
        self.assertTrue(SEG.needs_image({"id": "opening", "layout": "canvas"}))


class ManifestValidation(unittest.TestCase):
    def test_built_manifest_ok(self):
        MAN.validate_timing_manifest(H.make_manifest())

    def test_unknown_top_key_rejected(self):
        m = H.make_manifest()
        m["combined_audo"] = "x"
        bad(MAN.validate_timing_manifest, m, contains="combined_audo")

    def test_segment_typo_rejected(self):
        m = H.make_manifest()
        m["segments"][1]["take_away"] = "x"
        bad(MAN.validate_timing_manifest, m)

    def test_overlapping_sentences_rejected(self):
        m = H.make_manifest()
        m["sentences"][1]["start_time"] = 0.5
        for seg in m["segments"]:
            for s in seg["sentences"]:
                if s["index"] == 1:
                    s["start_time"] = 0.5
        bad(MAN.validate_timing_manifest, m)

    def test_segments_must_cover_every_sentence(self):
        m = H.make_manifest()
        m["segments"][-1]["sentences"] = m["segments"][-1]["sentences"][:-1]
        bad(MAN.validate_timing_manifest, m)

    def test_segment_sentence_must_match_top_level(self):
        m = H.make_manifest()
        m["segments"][0]["sentences"][0]["text"] = "被手改过的文本。"
        bad(MAN.validate_timing_manifest, m)

    def test_schema_version_pinned(self):
        m = H.make_manifest()
        m["schema_version"] = 3
        bad(MAN.validate_timing_manifest, m)


class ImagesJson(unittest.TestCase):
    def test_relative_paths_ok(self):
        IMG.validate_images_json({"seg-a": {"src": "images/seg-a.svg"}})

    def test_path_escape_rejected(self):
        for evil in ("../x.png", "/etc/passwd", "images/../../x.png", "C:\\x.png", "http://x/y.png"):
            bad(IMG.validate_images_json, {"seg-a": {"src": evil}})

    def test_bare_string_rejected(self):
        bad(IMG.validate_images_json, {"seg-a": "images/a.png"})

    def test_unknown_key_is_reported_not_rejected(self):
        # 设计如此：未知键只在渲染端丢弃时告警，不阻断；判定收口在 unknown_media_keys
        entry = {"src": "images/a.png", "scr": "typo", "alt": "x"}
        IMG.validate_images_json({"seg-a": entry})
        self.assertEqual(IMG.unknown_media_keys(entry), ["alt", "scr"])

    def test_video_and_gif_type_accepted(self):
        """video/gif 是合法 type：gif 与 image 同档，视频另走 <video> 分支。"""
        IMG.validate_images_json({"seg-a": {"src": "images/a.mp4", "type": "video"}})
        IMG.validate_images_json({"seg-a": {"src": "images/a.gif", "type": "gif"}})
        IMG.validate_images_json(
            {"seg-a": {"src": "images/a.mp4", "type": "video", "loop": True,
                       "muted": True, "autoplay": False, "playsinline": True,
                       "poster": "images/a.png"}})

    def test_still_removed_types_stay_rejected(self):
        """chart/formula 是真删掉的能力，误写时必须点名劝去手绘 SVG。"""
        bad(IMG.validate_images_json, {"seg-a": {"src": "images/a.png", "type": "chart"}},
            contains="手绘 SVG")
        bad(IMG.validate_images_json, {"seg-a": {"src": "images/a.png", "type": "formula"}},
            contains="手绘 SVG")

    def test_video_playback_flags_must_be_json_bool(self):
        """字符串 "false" 是真值——渲染端按 opts.get("loop", True) 取值，
        写成字符串会让"关掉循环"变成"永远循环"，所以契约层就拒。"""
        for flag in ("loop", "muted", "autoplay", "playsinline"):
            bad(IMG.validate_images_json,
                {"seg-a": {"src": "images/a.mp4", "type": "video", flag: "false"}},
                contains=flag)

    def test_poster_must_be_non_empty_string(self):
        """旧写法 `if poster:` 让空串 "" 与 None 同判，坏空串会原样拼进 HTML。"""
        bad(IMG.validate_images_json,
            {"seg-a": {"src": "images/a.mp4", "poster": ""}}, contains="poster")
        bad(IMG.validate_images_json,
            {"seg-a": {"src": "images/a.mp4", "poster": 7}}, contains="poster")

    def test_poster_shares_the_src_path_rules(self):
        for evil in ("../p.png", "/etc/passwd", "C:\\p.png"):
            bad(IMG.validate_images_json,
                {"seg-a": {"src": "images/a.mp4", "poster": evil}}, contains="poster")

    def test_classify_media_path_by_extension(self):
        for p, want in (("a.mp4", "video"), ("a.WEBM", "video"), ("a.mov", "video"),
                        ("a.mkv", "video"), ("a.avi", "video"),
                        ("a.gif", "gif"), ("a.png", "image"), ("a.svg", "image"),
                        ("a.jpg", "image"), ("a.webp", "image")):
            self.assertEqual(IMG.classify_media_path(p), want, p)
        # 显式 type 优先于扩展名
        self.assertEqual(IMG.classify_media_path("a.gif", "image"), "image")
        self.assertEqual(IMG.classify_media_path("a.png", "video"), "video")


class Speed(unittest.TestCase):
    def test_validate_speed(self):
        TL.validate_speed(1.5)
        for badv in (0, -1, float("nan"), float("inf")):
            bad(TL.validate_speed, badv)


if __name__ == "__main__":
    unittest.main()
