#!/usr/bin/env python3
"""timing_manifest.json → Remotion 渲染工程（html_renderer 的 Remotion 版桥）。

与 gen_hyperframes.py 同一条数据流、同一份契约（_manifest_schema 校验）与同一套
clip 计时（wipe/win_start/vis/peel 逐条移植自 html_renderer.generate_html 的 clips
预处理，见 references/rendering.md「动画」）。差别只在渲染后端：这里不产出
HTML+GSAP，而是把 manifest + 已算好的 clip 几何 + 资产路径写进 remotion/src/
generated.ts，再按需把音频/配图复制进 remotion/public/。remotion/ 里的 React
组件按绝对帧推导画面（等价于 GSAP 单条时间线的逐帧 seek），组件代码本身是
静态脚手架，生成期只换数据胶（generated.ts）。

用法示例：
    python scripts/gen_remotion_project.py -m audio_output/timing_manifest.json \
        --images hf-project/images.json --out remotion --aspect portrait --fps 24
    cd remotion && npx remotion render src/index.ts ContentToVideo out.mp4

完整参数见 --help。
"""
import argparse
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _manifest_schema import load_timing_manifest  # noqa: E402
from _segments import is_content_sid, seg_layout, STRUCTURAL_SIDS  # noqa: E402
from _template import get_canvas, load_template, normalize_aspect  # noqa: E402
from _script_utils import setup_stdio, write_text_atomic  # noqa: E402


def segment_duration(seg):
    """段落时长（秒）：末句 end − 首句 start（与 html_renderer 同一口径）。"""
    sents = seg["sentences"]
    return ((sents[-1].get("start_time", 0) + sents[-1].get("duration", 0))
            - sents[0].get("start_time", 0))


def _fmt_mmss(seconds):
    """秒 → 'm:ss'（agenda 行右侧时长列，与 html_renderer 同一份）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def compute_clips(manifest, tpl):
    """逐条移植 html_renderer.generate_html 的 clips 预处理。

    同一段注释也搬过来：引擎（这里是 Remotion 的逐帧渲染）按窗口硬切卡片可见性，
    元素窗口必须精确覆盖"这张页在屏幕上"的全程——从擦除起点（本句音频起点 − 擦除
    时长）到下一页擦除完成把它盖住的时刻。
    """
    total_dur = float(manifest["total_duration"])
    segments = manifest["segments"]
    _is_line = tpl["animation"]["segmentWipe"]["style"] == "line"
    _wd = (tpl["animation"]["propLine"]["duration"] if _is_line
           else tpl["animation"]["segmentWipe"]["duration"])
    clips = []
    for seg in segments:
        start = round(float(seg["sentences"][0]["start_time"]), 2)
        clips.append({
            "seg": seg,
            "start": start,
            "duration": round(segment_duration(seg), 2),
            "gets_peeled": False,
        })
    for i, clip in enumerate(clips):
        s_i = clip["start"]
        prev_end = (clips[i - 1]["start"] + clips[i - 1]["duration"]) if i else None
        clip["wipe"] = _wd if prev_end is None else round(
            min(_wd, max(0.0, s_i - prev_end)), 2)
        # 整页画布段：模板转场（擦除/引导线/剥离）一律退场，在自己的音频起点整页出现。
        if seg_layout(clip["seg"]) == "canvas":
            clip["wipe"] = 0.0
        clip["win_start"] = round(max(0.0, s_i - clip["wipe"]), 2)
        win_end = clips[i + 1]["start"] if i + 1 < len(clips) else round(total_dur, 2)
        clip["vis"] = round(win_end - clip["win_start"], 2)
        # line 档的剥离挂在被犁走的旧卡上（上一页被下一页的揭开窗口推走）。
        clip["peel"] = None
        if (_is_line and i and clip["wipe"] > 0
                and seg_layout(clips[i - 1]["seg"]) != "canvas"):
            prev_sid = clips[i - 1]["seg"]["id"]
            clip["peel"] = {"sid": prev_sid, "wipe": clip["wipe"]}
            clips[i - 1]["gets_peeled"] = True
    return clips


def build_agenda_rows(seg_id, clips, manifest, ag, *, warn):
    """开屏/结尾 agenda 行（移植 html_renderer._agenda_rows，口径与 warn 同步）。"""
    trim = int(ag["nameTrim"])
    content_clips = [c for c in clips if is_content_sid(c["seg"]["id"])]

    def _cell(raw, sid):
        text = str(raw or "").strip()
        if len(text) > trim:
            warn(f"agenda 行（{sid}）文本 {len(text)} 字超出上限 {trim} 字，"
                 f"尾部 {len(text) - trim} 字已截断且画面没有省略号：{text[trim:]}\n"
                 "       写稿时把要点压进一句")
        return text[:trim]

    tail_rows = []
    if seg_id == "opening":
        rows = [(f"{n:02d}", _cell(c["seg"]["title"], c["seg"]["id"]),
                 _fmt_mmss(c["duration"]))
                for n, c in enumerate(content_clips, 1)]
    else:
        rows = [(f"{n:02d}",
                 _cell(c["seg"].get("takeaway") or c["seg"]["title"],
                       c["seg"]["id"]), "")
                for n, c in enumerate(content_clips, 1)]
        cta = str(manifest.get("closing_cta") or "").strip()
        if cta:
            tail_rows.append(("→", _cell(cta, "closing_cta"), ""))
    max_rows = int(ag["maxRows"])
    if tail_rows and len(rows) + len(tail_rows) > max_rows:
        warn(f"agenda（{seg_id}）正文 {len(rows)} 条 + cta 尾行超出上限 "
             f"{max_rows} 行——要点优先，cta 尾行不上画面")
        tail_rows = []
    if len(rows) > max_rows:
        dropped = len(rows) - max_rows
        warn(f"agenda（{seg_id}）正文 {len(rows)} 条超出上限 {max_rows} 行，"
             f"尾部 {dropped} 条不上画面")
        rows = rows[:max_rows]
    return rows, tail_rows


def _stage_asset(src_path, out_dir, subdir):
    """把资产复制进 out_dir/<subdir>，返回 public 相对路径（Remotion 的静态资源
    一律放 public/ 下，组件用相对 src 引用）。同名同内容直接复用（只比大小会把
    "同大小不同内容"的旧拷贝误当最新资产复用，所以逐字节比）。"""
    src_path = os.path.abspath(src_path)
    if not os.path.isfile(src_path):
        raise SystemExit(f"[error] 资产文件不存在或不可读: {src_path}")
    dst_dir = os.path.join(out_dir, "public", subdir)
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src_path))
    if not (os.path.isfile(dst) and _bytes_equal(src_path, dst)):
        tmp = dst + ".tmp"
        try:
            shutil.copy2(src_path, tmp)
            os.replace(tmp, dst)
        except OSError as exc:
            raise SystemExit(
                f"[error] 资产复制失败 {src_path} → {dst}: {exc}") from exc
    return f"{subdir}/{os.path.basename(dst)}"


def _bytes_equal(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def main(argv=None):
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="Generate Remotion render project from timing manifest"
    )
    parser.add_argument("-m", "--manifest", required=True,
                        help="Path to timing_manifest.json")
    parser.add_argument("--out", default="remotion",
                        help="Output Remotion project directory (default: ./remotion)")
    parser.add_argument("--images", default=None,
                        help="Path to images.json (maps segment ID -> image path)")
    parser.add_argument("--audio", default=None,
                        help="Audio file path (default: auto-detect from manifest)")
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏 3:4）或 "
                             "landscape（1920×1080 横屏 16:9）。")
    parser.add_argument("--fps", type=int, default=24,
                        help="输出帧率（写入 generated.ts；默认 24）")
    args = parser.parse_args(argv)

    if not 1 <= args.fps <= 240:
        parser.error(f"--fps 必须在 1–240 之间，收到: {args.fps}")

    try:
        manifest = load_timing_manifest(args.manifest)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    tpl = load_template()
    aspect = normalize_aspect(args.aspect)
    w, h = get_canvas(aspect)
    total_dur = float(manifest["total_duration"])
    clips = compute_clips(manifest, tpl)

    # ── 配图归一（移植 gen_hyperframes 的 images 处理：agenda 结构性页键弹出、
    #    孤儿键忽略、文件复制进 public/images）──────────────────────────────
    images = {}
    images_dir = None
    if args.images:
        if not os.path.exists(args.images):
            print(f"[warn] --images 文件不存在: {args.images}，本次渲染将不带配图"
                  "（纯文字版兜底）", file=sys.stderr)
        else:
            images_dir = os.path.dirname(os.path.abspath(args.images))
            with open(args.images, encoding="utf-8") as f:
                images = json.load(f)
            seg_by_id = {c["seg"]["id"]: c["seg"] for c in clips}
            # agenda 结构性页不消费配图
            for k in STRUCTURAL_SIDS:
                if k in images and k in seg_by_id \
                        and seg_layout(seg_by_id[k]) == "agenda":
                    images.pop(k)
            # 孤儿键
            for k in [k for k in images if k not in seg_by_id]:
                print(f"[warn] images.json 的 {k} 键未匹配到 manifest 中的任何段落，"
                      "已忽略", file=sys.stderr)
                images.pop(k)

    audio_src = None
    if args.audio:
        audio_src = _stage_asset(args.audio, args.out, "audio")
    else:
        audio_abs = manifest.get("combined_audio")
        if audio_abs and os.path.isfile(audio_abs):
            audio_src = _stage_asset(audio_abs, args.out, "audio")
        else:
            print(f"[warn] manifest 的 combined_audio 不可用（{audio_abs!r}），"
                  "成片将无声；请显式 --audio 指定", file=sys.stderr)

    # ── 段数据 → generated.ts 数据胶 ────────────────────────────────────────
    segs_out = []
    for clip in clips:
        seg = clip["seg"]
        sid = seg["id"]
        layout = seg_layout(seg)
        image = None
        if layout != "agenda" and sid in images:
            src = images[sid]
            # images.json 允许裸字符串路径或 {src: ...} 对象
            src_path = src if isinstance(src, str) else (src or {}).get("src")
            # 相对路径按 images.json 所在目录解析（与 gen_hyperframes 按 HTML
            # 输出目录解析同一惯例：资产路径是"相对清单文件"写的）；非绝对路径
            # 且已存在于 public/ 下 = 上一次生成复制的相对路径，直接复用。
            if src_path:
                if (not os.path.isabs(src_path)
                        and os.path.isfile(os.path.join(args.out, "public", src_path))):
                    image = src_path
                else:
                    resolved = (src_path if os.path.isabs(src_path)
                                else os.path.join(images_dir or ".", src_path))
                    if os.path.isfile(resolved):
                        image = _stage_asset(resolved, args.out, "images")
                    else:
                        print(f"[warn] 段落 '{sid}' 的配图 {src_path!r} 找不到，该段将无图",
                              file=sys.stderr)
        seg_out = {
            "id": sid,
            "title": seg.get("title", ""),
            "tagline": seg.get("tagline") or None,
            "accent": seg.get("accent") or "#2dd4bf",
            "layout": layout,
            "openingAnimation": seg.get("opening_animation") or None,
            "sentences": [
                {"index": s.get("index", -1), "text": s["text"],
                 "startTime": float(s["start_time"]),
                 "duration": float(s["duration"])}
                for s in seg["sentences"]
            ],
            "start": clip["start"],
            "duration": clip["duration"],
            "wipe": clip["wipe"],
            "winStart": clip["win_start"],
            "vis": clip["vis"],
            "peel": clip["peel"],
            "image": image,
            "rows": [],
            "tail": None,
        }
        if layout == "agenda":
            rows, tail = build_agenda_rows(
                sid, clips, manifest, tpl["layout"][aspect]["agenda"], warn=print)
            seg_out["rows"] = [{"idx": r[0], "name": r[1], "dur": r[2] or None}
                               for r in rows]
            if tail:
                seg_out["tail"] = {"idx": tail[0][0], "name": tail[0][1],
                                   "dur": None}
        segs_out.append(seg_out)

    data = {
        "fps": args.fps, "width": w, "height": h,
        "totalDuration": total_dur,
        "audioSrc": audio_src,
        "segments": segs_out,
    }

    gen_path = os.path.join(args.out, "src", "generated.ts")
    os.makedirs(os.path.dirname(gen_path), exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    ts = (
        "// GENERATED by scripts/gen_remotion_project.py — 手工改动会被下一次\n"
        "// 生成覆盖。数据胶 = manifest + clip 几何 + 资产相对路径；组件代码\n"
        "// 是静态脚手架，两者分开，见 remotion/README.md。\n"
        f"export const DATA = {body} as const;\n"
        "export type GeneratedData = typeof DATA;\n"
    )
    write_text_atomic(gen_path, ts)

    print(f"[OK] {gen_path}")
    print(f"     Duration: {total_dur}s @ {args.fps}fps")
    print(f"     Aspect: {aspect} ({w}x{h})")
    print(f"     Segments: {len(segs_out)}")
    print(f"     Audio: {audio_src or '无（成片将无声）'}")
    print(f"     Images: {sum(1 for s in segs_out if s['image'])}")
    print(f"     渲染: cd {os.path.abspath(args.out)} && "
          "npx remotion render src/index.ts ContentToVideo out.mp4")


if __name__ == "__main__":
    main()
