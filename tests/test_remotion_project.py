"""Remotion 后端桥（scripts/gen_remotion_project.py）的单元测试。

重点是 clips 预处理（compute_clips）的**几何确定性**——wipe/win_start/vis/peel
与渲染端逐帧求值必须同构，转场才不会错位；以及 agenda 行/生成产物形状的确定性。
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
    """compute_clips 的 clip 几何逐条对齐渲染端口径。"""

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
            # clip 几何口径与渲染端同源
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


DIRECTOR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<g id="cam" transform="translate(0,0) scale(1)">
<rect id="box" x="10" y="10" width="80" height="80" fill="#2dd4bf"/>
<path id="shape" d="M10 10 L90 10 L90 90 L10 90 Z" fill="none" stroke="#fff"/>
<text id="num" x="50" y="50" font-size="20" text-anchor="middle">0</text>
</g>
<script>window.x=1</script>
</svg>"""


def _director_source():
    """带 director/keep/video 的样本稿件：seg-a 槽位导演页 → seg-b 接续 →
    seg-c 视频槽位。"""
    src = H.sample_source()
    src["segments"] = [
        {"id": "seg-a", "title": "导演页", "tagline": "director",
         "text": "这是导演页的第一句。这是导演页的第二句。"},
        {"id": "seg-b", "title": "接续页", "tagline": "keep",
         "text": "这是接续页的第三句。这是接续页的第四句。"},
        {"id": "seg-c", "title": "视频页", "tagline": "video",
         "text": "这是视频页的第五句。"},
    ]
    return src


def _director_images(tmp, manifest):
    """写 SVG/视频文件 + images.json：seg-a 导演、seg-b stage:keep（同图）、
    seg-c 视频。返回 images.json 绝对路径。"""
    imgs = os.path.join(tmp, "imgs")
    os.makedirs(imgs, exist_ok=True)
    svg_path = os.path.join(imgs, "scene.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(DIRECTOR_SVG)
    # 假 mp4：契约只认扩展名/路径，生成期不读内容（director_prepare 只读 SVG）
    vid_path = os.path.join(imgs, "clip.mp4")
    with open(vid_path, "wb") as f:
        f.write(b"not-a-real-mp4")
    segs = {seg["id"]: seg for seg in manifest["segments"]}
    images = {
        "seg-a": {"src": "imgs/scene.svg", "director": {
            "steps": [
                {"target": "#cam", "to": {"scale": 2}, "at": 0,
                 "duration": 0.6, "ease": "power2.out"},
                {"target": "#num", "count": {"from": 0, "to": 42},
                 "at": 0, "duration": 0.9},
                {"target": "#box", "from": {"opacity": 0}, "at": 1,
                 "duration": 0.4},
                {"target": "#shape", "draw": True, "at": 0, "duration": 0.8},
                {"target": "#shape", "morph": {
                    "from": "M10 10 L90 10 L90 90 L10 90 Z",
                    "to": "M20 20 L80 20 L80 80 L20 80 Z"},
                 "at": 1, "duration": 0.5},
            ]}},
        "seg-b": {"src": "imgs/scene.svg", "stage": "keep"},
        "seg-c": {"src": "imgs/clip.mp4", "type": "video",
                  "loop": False, "muted": False},
    }
    if segs.get("seg-b"):
        images["seg-b"].setdefault("director", {"steps": [
            {"target": "#box", "set": {"fill": "#f59e0b"}, "at": 0}]})
    images_path = os.path.join(tmp, "images.json")
    with open(images_path, "w", encoding="utf-8") as f:
        json.dump(images, f, ensure_ascii=False)
    return images_path


class DirectorKeepMedia(unittest.TestCase):
    """director 数据胶 / stage:"keep" 烘焙 / video 媒体的生成端测试。"""

    def _gen(self, tmp, with_images=True):
        m_path = os.path.join(tmp, "manifest.json")
        with open(m_path, "w", encoding="utf-8") as f:
            json.dump(H.make_manifest(_director_source()), f, ensure_ascii=False)
        out = os.path.join(tmp, "proj")
        argv = ["-m", m_path, "--out", out, "--aspect", "portrait", "--fps", "24"]
        if with_images:
            argv += ["--images", _director_images(tmp, H.make_manifest(_director_source()))]
        gen_main(argv)
        gen_ts = H.read_text(os.path.join(out, "src", "generated.ts"))
        body = gen_ts[gen_ts.index("{") : gen_ts.rindex("}") + 1]
        return json.loads(body), out

    def test_director_steps_expanded_with_beats_and_morph_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = self._gen(tmp)
            # 回归：images 的 director 体检 warn 循环不得覆盖画布宽（实测踩过
            # `for w in dir_warns` 把 data["width"] 写成警告文本）
            self.assertEqual(data["width"], 1080)
            self.assertEqual(data["height"], 1440)
            seg = next(s for s in data["segments"] if s["id"] == "seg-a")
            self.assertEqual(seg["imageMode"], "svgInline")
            self.assertIsNone(seg["media"])
            self.assertTrue(seg["svg"].startswith("<svg"))
            self.assertNotIn("script", seg["svg"].lower())
            d = seg["director"]
            self.assertEqual(d["segStart"], seg["start"])
            # 5 个 step 全部展开；morph 关键帧 ≥ fps×2×dur
            kinds = [s["kind"] for s in d["steps"]]
            self.assertEqual(kinds, ["to", "count", "from", "draw", "morph"])
            to_step = next(s for s in d["steps"] if s["kind"] == "to")
            self.assertEqual(to_step["ease"], "power2.out")
            self.assertEqual(to_step["dur"], 0.6)
            self.assertEqual(to_step["repeat"], 0)
            self.assertEqual(to_step["toVars"], {"scale": 2})
            count_step = next(s for s in d["steps"] if s["kind"] == "count")
            self.assertEqual(count_step["count"],
                             {"from": 0, "to": 42, "decimals": 0,
                              "prefix": "", "suffix": ""})
            morph = next(s for s in d["steps"] if s["kind"] == "morph")
            # 0.5s × 24fps × 2 = 24 关键帧（max(2, min(240, 24))）
            self.assertEqual(len(morph["morphKeys"]), 25)
            self.assertEqual(morph["morphKeys"][0]["d"], "M10 10 L90 10 L90 90 L10 90 Z")
            self.assertNotEqual(morph["morphKeys"][-1]["d"],
                                morph["morphKeys"][0]["d"])
            # 节拍锚 at=0/at=1 都在段内；morph 挂在第二句（at=1）
            self.assertGreaterEqual(morph["pos"], seg["sentences"][1]["startTime"])
            self.assertLess(morph["pos"], seg["sentences"][1]["startTime"]
                            + seg["sentences"][1]["duration"])

    def test_keep_page_baked_and_wipe_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = self._gen(tmp)
            seg_b = next(s for s in data["segments"] if s["id"] == "seg-b")
            self.assertTrue(seg_b["keep"])
            self.assertEqual(seg_b["imageMode"], "svgInline")
            self.assertEqual(seg_b["wipe"], 0.0)
            self.assertEqual(seg_b["winStart"], seg_b["start"])
            # 烘焙副本：保留了上一页的收尾态（相机 scale(2) 折进本层 transform、
            # morph 终点、count 终值 42、draw 拉完）
            baked = seg_b["svg"]
            self.assertIn("scale(2", baked)
            self.assertIn("translate(-50", baked)
            self.assertIn(">42<", baked)
            self.assertIn("M20 20 L80 20 L80 80 L20 80 Z", baked)
            # 接续页自己的 director 步骤也带进来（set fill）
            self.assertEqual(seg_b["director"]["steps"][0]["kind"], "set")
            # 上一页的 wipe 不受影响
            seg_a = next(s for s in data["segments"] if s["id"] == "seg-a")
            self.assertGreater(seg_a["wipe"], 0.0)

    def test_video_media_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, out = self._gen(tmp)
            seg_c = next(s for s in data["segments"] if s["id"] == "seg-c")
            self.assertEqual(seg_c["imageMode"], "video")
            self.assertEqual(seg_c["media"]["loop"], False)
            self.assertEqual(seg_c["media"]["muted"], False)
            self.assertTrue(seg_c["media"]["src"].startswith("images/"))
            # 视频文件已复制进 public/images
            self.assertTrue(os.path.isfile(
                os.path.join(out, "public", seg_c["media"]["src"])))

    def test_keep_requires_previous_inlineable_page(self):
        # 前一段没写 director（无法内联）→ 生成器 fail-fast
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(_director_source()), f, ensure_ascii=False)
            imgs = os.path.join(tmp, "imgs")
            os.makedirs(imgs, exist_ok=True)
            for name in ("scene.svg", "plain.svg"):
                with open(os.path.join(imgs, name), "w", encoding="utf-8") as f:
                    f.write(DIRECTOR_SVG.replace("<script>window.x=1</script>", ""))
            images = {
                "seg-a": {"src": "imgs/scene.svg"},   # 无 director
                "seg-b": {"src": "imgs/scene.svg", "stage": "keep"},
            }
            images_path = os.path.join(tmp, "images.json")
            with open(images_path, "w", encoding="utf-8") as f:
                json.dump(images, f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    gen_main(["-m", m_path, "--out", out, "--images", images_path])
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("没有可接续的内联画面", buf.getvalue())

    def test_missing_media_fails_fast(self):
        # 配图路径不存在 → error 退出（渲染会静默出空白裂图）
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(_director_source()), f, ensure_ascii=False)
            images = {"seg-a": {"src": "images/nope.svg"}}
            images_path = os.path.join(tmp, "images.json")
            with open(images_path, "w", encoding="utf-8") as f:
                json.dump(images, f, ensure_ascii=False)
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                with self.assertRaises(SystemExit):
                    gen_main(["-m", m_path, "--out", os.path.join(tmp, "proj"),
                              "--images", images_path])
            self.assertIn("找不到或不可读", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
