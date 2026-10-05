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


def _write_real_clip(path):
    """用 ffmpeg 写一段能被全解码探测通过的最小 mp4；没有 ffmpeg 退回占位字节。

    生成器的素材门禁（`validate_images_files`）会用 ffmpeg 全解码探测拦下截断
    与损坏的媒体——"扩展名对了就行"的占位字节在那道闸眼里就是损坏文件。测试用例
    要代表真实工作流，不该靠门禁失活才能过。
    """
    ff = None
    try:
        from _audio import get_ffmpeg, ffmpeg_usable
        cand = get_ffmpeg()
        if ffmpeg_usable(cand):
            ff = cand
    except Exception:
        ff = None
    if ff:
        import subprocess
        # 编码器按普适性排队挑第一个能用的：CI runner 通常是 libx264，本机
        # 这份自编译 ffmpeg 没有它（只有 mpeg4/libopenh264）。
        for codec in ("libx264", "mpeg4", "libopenh264"):
            try:
                r = subprocess.run(
                    [ff, "-v", "error", "-y", "-f", "lavfi",
                     "-i", "testsrc=size=64x48:rate=10:duration=1",
                     "-c:v", codec, "-pix_fmt", "yuv420p", path],
                    capture_output=True, timeout=30)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if r.returncode == 0 and os.path.isfile(path):
                return True
    with open(path, "wb") as f:
        f.write(b"\x00" * 1024)
    return False


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
    def test_generator_scaffolds_self_contained_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(), f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "--out", out, "--aspect", "portrait",
                      "--fps", "24"])
            # 自包含工程：npm 配置 + 组件源码 + 数据胶都在输出目录里
            for rel in ("package.json", "package-lock.json", "tsconfig.json",
                        "remotion.config.ts", "README.md", ".gitignore",
                        "src/index.ts", "src/Root.tsx", "src/Video.tsx",
                        "src/components/Card.tsx",
                        "src/components/AgendaCard.tsx",
                        "src/generated.ts"):
                self.assertTrue(
                    os.path.isfile(os.path.join(out, rel)),
                    f"脚手架缺失: {rel}")
            # 排除项不得进输出
            self.assertFalse(os.path.exists(os.path.join(out, "node_modules")),
                             "node_modules 不应被复制")
            self.assertFalse(any(
                n.startswith("out") for n in os.listdir(out)),
                "演示成片不应被复制")
            # generated.ts 是本次生成物（非脚手架里的旧数据胶）
            gen_ts = H.read_text(os.path.join(out, "src", "generated.ts"))
            self.assertIn("export const DATA", gen_ts)
            # 数据胶带生成头——说明是本次生成物，脚手架里可能存在的旧文件已被覆盖
            self.assertTrue(gen_ts.startswith("// GENERATED by scripts/"),
                            "数据胶必须带生成头（脚手架里的旧文件被覆盖）")

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
    # 真的 mp4，不是"扩展名对了就行"的占位字节：生成器这道素材门禁会用 ffmpeg
    # 全解码探测拦下截断/损坏的文件（`validate_images_files`），假字节过不去。
    # 没有 ffmpeg 时退回占位字节——门禁那时也会降级为仅查存在性（见其源码）。
    vid_path = os.path.join(imgs, "clip.mp4")
    _write_real_clip(vid_path)
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


class OutDirGuardAndScaffoldSafety(unittest.TestCase):
    """写盘边界：产物不得落进技能目录、脚手架不得无脑覆盖、素材不得只增不减。

    这三条曾经都没有：文档教的是 `-o` 而 CLI 只认 `--out`（照抄即报错）；脚手架
    用 copytree(dirs_exist_ok=True) 覆盖，用户改过的组件源码会被静默抹掉；
    public/ 只增不减，换稿件后旧素材还躺在工程里。
    """

    def test_dash_o_is_an_accepted_alias(self):
        """文档（SKILL.md / references/rendering.md）教的是 -o，CLI 必须认。"""
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(), f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "-o", out, "--aspect", "portrait", "--fps", "24"])
            self.assertTrue(os.path.isfile(os.path.join(out, "src", "generated.ts")),
                            "-o 应与 --out 等价")

    def test_out_inside_skill_dir_is_refused(self):
        """与 pipeline.py 同一道闸：产物落进技能目录会污染仓库。"""
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(), f, ensure_ascii=False)
            bad_out = os.path.join(H.SKILL_DIR, "_test_should_never_exist")
            try:
                with self.assertRaises(SystemExit) as cm:
                    gen_main(["-m", m_path, "-o", bad_out])
                self.assertNotEqual(cm.exception.code, 0)
            finally:
                # 闸门要是没拦住，用例自己把垃圾扫掉，别污染仓库工作区
                if os.path.isdir(bad_out):
                    shutil.rmtree(bad_out, ignore_errors=True)
                    self.fail(f"闸门没拦住：技能目录下被创建了 {bad_out}")

    def test_existing_scaffold_edits_are_preserved(self):
        """用户改过的组件源码不得被下一次生成静默覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(), f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "-o", out, "--aspect", "portrait", "--fps", "24"])
            card = os.path.join(out, "src", "components", "Card.tsx")
            self.assertTrue(os.path.isfile(card))
            with open(card, "a", encoding="utf-8") as f:
                f.write("\n// 我改过这一行\n")
            gen_main(["-m", m_path, "-o", out, "--aspect", "portrait", "--fps", "24"])
            with open(card, encoding="utf-8") as f:
                self.assertIn("我改过这一行", f.read(),
                              "第二次生成把用户改过的 Card.tsx 覆盖了")

    def test_stale_assets_pruned_but_inlined_svg_kept(self):
        """上一轮的素材要清掉，但**正在用的内联 SVG 绝不能删**。

        内联 SVG（director / stage:"keep"）在数据胶里是内联字符串、media 为 null，
        只按 media.src 判"有没有被引用"就会把正在用的那张图当遗留删掉——画面直接
        变成空白。这是本函数自己引入过的回归，用例钉住判据：既扫 media.src，也扫
        原始 entry 的 src/poster。
        """
        with tempfile.TemporaryDirectory() as tmp:
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(_director_source()), f, ensure_ascii=False)
            images_path = _director_images(tmp, H.make_manifest(_director_source()))
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "-o", out, "--images", images_path,
                      "--aspect", "portrait", "--fps", "24"])
            pub = os.path.join(out, "public", "images")
            kept = os.listdir(pub)
            self.assertTrue(any(n.endswith(".svg") for n in kept),
                            f"正在用的内联 SVG 被当遗留删了，public/images={kept}")
            # 塞一个没人引用的旧素材，下一次生成后应消失
            stale = os.path.join(pub, "old-unused.png")
            with open(stale, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            gen_main(["-m", m_path, "-o", out, "--images", images_path,
                      "--aspect", "portrait", "--fps", "24"])
            after = os.listdir(pub)
            self.assertNotIn("old-unused.png", after,
                             "上一轮没人引用的素材应被清掉（public/ 只增不减会越攒越多）")
            self.assertTrue(any(n.endswith(".svg") for n in after),
                            f"清理时把正在用的内联 SVG 一起删了：{after}")


class WipeStyleGuard(unittest.TestCase):
    """模板把 segmentWipe.style 改成非 line 档时必须 fail-fast。

    渲染端只实现了 line 档（引导线 + clip-path，ANIM.propLine）。模板改成别的值时
    clip 几何会算出一段没有对应渲染实现的 wipe——画面照出、与模板意图对不上，
    属于最难查的那类静默偏差。而且必须在**任何写盘之前**退出：否则素材已经复制进
    用户目录了才报错。
    """

    def test_non_line_wipe_style_rejected(self):
        tpl = load_template()
        tpl = json.loads(json.dumps(tpl))          # 深拷贝，不污染模块级缓存
        tpl["animation"]["segmentWipe"]["style"] = "fade"
        with self.assertRaises(ValueError) as cm:
            compute_clips(H.make_manifest(), tpl)
        self.assertIn("line", str(cm.exception))

    def test_guard_runs_before_any_write(self):
        """模板档位不对时，不得已经在输出目录里铺过素材。"""
        import gen_remotion_project as G
        real_tpl = G.load_template
        broken = json.loads(json.dumps(real_tpl()))
        broken["animation"]["segmentWipe"]["style"] = "fade"

        def fake_tpl():
            return broken
        G.load_template = fake_tpl
        try:
            with tempfile.TemporaryDirectory() as tmp:
                m_path = os.path.join(tmp, "manifest.json")
                with open(m_path, "w", encoding="utf-8") as f:
                    json.dump(H.make_manifest(), f, ensure_ascii=False)
                out = os.path.join(tmp, "proj")
                import io
                from contextlib import redirect_stderr
                buf = io.StringIO()
                with redirect_stderr(buf):
                    with self.assertRaises(SystemExit) as cm:
                        gen_main(["-m", m_path, "-o", out])
                self.assertEqual(cm.exception.code, 1)
                self.assertIn("line", buf.getvalue())
                self.assertFalse(os.path.isdir(out),
                                 "模板档位不对却已经写了输出目录（副作用不该早于校验）")
        finally:
            G.load_template = real_tpl


class MediaDataGlue(unittest.TestCase):
    """数据胶里只带渲染端真读的媒体字段。"""

    def test_browser_only_flags_not_written_to_data_glue(self):
        """autoplay/playsinline 逐帧渲染无意义，不该进数据胶装样子。"""
        with tempfile.TemporaryDirectory() as tmp:
            imgs = os.path.join(tmp, "imgs")
            os.makedirs(imgs)
            _write_real_clip(os.path.join(imgs, "clip.mp4"))
            src = H.sample_source()
            src["segments"] = [{"id": "seg-a", "title": "视频页", "tagline": "v",
                                "text": "这是视频页的第一句。这是视频页的第二句。"}]
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(H.make_manifest(src), f, ensure_ascii=False)
            images = {"seg-a": {"src": "imgs/clip.mp4", "type": "video",
                                "autoplay": False, "playsinline": False,
                                "loop": True, "muted": True}}
            images_path = os.path.join(tmp, "images.json")
            with open(images_path, "w", encoding="utf-8") as f:
                json.dump(images, f, ensure_ascii=False)
            out = os.path.join(tmp, "proj")
            gen_main(["-m", m_path, "-o", out, "--images", images_path,
                      "--aspect", "portrait", "--fps", "24"])
            gen_ts = H.read_text(os.path.join(out, "src", "generated.ts"))
            body = gen_ts[gen_ts.index("{"): gen_ts.rindex("}") + 1]
            data = json.loads(body)
            seg = next(s for s in data["segments"] if s["id"] == "seg-a")
            self.assertEqual(seg["imageMode"], "video")
            media = seg["media"]
            self.assertNotIn("autoplay", media,
                             "autoplay 是浏览器播放语义，逐帧渲染不读，别写进数据胶")
            self.assertNotIn("playsinline", media)
            # 渲染端真读的三个仍在
            for k in ("src", "loop", "muted"):
                self.assertIn(k, media)


if __name__ == "__main__":
    unittest.main()
