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
        --images audio_output/images.json --out remotion --aspect portrait --fps 24
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
from _images_schema import load_images_json, classify_media_path  # noqa: E402
from _timeline import beat_positions, beat_span, beat_cycles  # noqa: E402
from _path_morph import make_morph, interp as _morph_interp  # noqa: E402
from _ease import curve as ease_curve  # noqa: E402
from _cam_crop import cam_default_origin  # noqa: E402
from _director_prepare import director_prepare, next_speech_start  # noqa: E402


def segment_duration(seg):
    """段落时长（秒）：末句 end − 首句 start（与 html_renderer 同一口径）。"""
    sents = seg["sentences"]
    return ((sents[-1].get("start_time", 0) + sents[-1].get("duration", 0))
            - sents[0].get("start_time", 0))


def _fmt_mmss(seconds):
    """秒 → 'm:ss'（agenda 行右侧时长列，与 html_renderer 同一份）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def compute_clips(manifest, tpl, images=None):
    """逐条移植 html_renderer.generate_html 的 clips 预处理。

    同一段注释也搬过来：引擎（这里是 Remotion 的逐帧渲染）按窗口硬切卡片可见性，
    元素窗口必须精确覆盖"这张页在屏幕上"的全程——从擦除起点（本句音频起点 − 擦除
    时长）到下一页擦除完成把它盖住的时刻。
    """
    images = images or {}
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
        # 整页画布段：模板转场（擦除/引导线/剥离）一律退场，在自己的音频起点整页出现，
        # 运动全由 director 驱动。接续页（stage:"keep"）同理且更要紧：这一页的画面是
        # 上一页演完的样子，擦进来就是把同一幅画"翻页"了一次，正好抵消 _stage_carry
        # 烘焙的意义（口径与 html_renderer 的 clips 预处理逐条一致）。
        if (seg_layout(clip["seg"]) == "canvas"
                or (images.get(clip["seg"]["id"]) or {}).get("stage") == "keep"):
            clip["wipe"] = 0.0
        clip["win_start"] = round(max(0.0, s_i - clip["wipe"]), 2)
        win_end = clips[i + 1]["start"] if i + 1 < len(clips) else round(total_dur, 2)
        clip["vis"] = round(win_end - clip["win_start"], 2)
        # line 档的剥离挂在被犁走的旧卡上（上一页被下一页的揭开窗口推走）。
        # 上一页是画布段时也不剥（活 diagram 不翻页，见 html_renderer 同处注释）。
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


def _stage_media(src_path, out_dir, staged_names):
    """把媒体文件暂存进 out_dir/public/images，返回 public 相对路径。

    staged_names：{basename: 已占用的完整 public 相对路径}——同名不同内容的两个
    文件（images.json 两个键指向不同目录的同名图）不能互相覆盖，给后者加数字
    后缀；同名同内容直接复用（只比大小会把"同大小不同内容"的旧拷贝误当最新
    资产复用，所以逐字节比）。文件不存在/不可读返回 None（fail-fast 由调用方
    按 error 处理，渲染会静默出空白裂图/无声媒体）。"""
    if not os.path.isfile(src_path):
        return None
    dst_dir = os.path.join(out_dir, "public", "images")
    os.makedirs(dst_dir, exist_ok=True)
    base = os.path.basename(src_path)
    name = base
    k = 1
    while name in staged_names:
        occupied = staged_names[name]
        if _bytes_equal(src_path, os.path.join(out_dir, "public", occupied)):
            return occupied
        stem, ext = os.path.splitext(base)
        k += 1
        name = f"{stem}-{k}{ext}"
    dst = os.path.join(dst_dir, name)
    tmp = dst + ".tmp"
    try:
        shutil.copy2(src_path, tmp)
        os.replace(tmp, dst)
    except OSError as exc:
        raise SystemExit(f"[error] 媒体复制失败 {src_path} → {dst}: {exc}") from exc
    rel = f"images/{name}"
    staged_names[name] = rel
    return rel


def _director_data(entry, seg, dflt, fps):
    """把 images.json 的 director.steps 展开成组件可逐帧求值的数据胶。

    与 html_renderer._director_timeline_lines 同一条节拍解析（beat_positions，
    同一函数：at 跟着 manifest 句子走、at_time 是段落绝对秒、delay 叠加）、
    同一份缺省 duration/ease、同一套 morph 生成期采样（fps×2 关键帧 + 遍序
    奇偶折 yoyo，逐帧 seek 可复现、无运行时依赖）。count/type/draw/set/from/
    to/fromTo 只把校验过的标量搬进数据，组件端按帧求值（读的都是契约层过
    过的标量，不是信源回调）。
    """
    director = (entry or {}).get("director")
    steps = (director or {}).get("steps")
    if not steps:
        return None
    sentences = seg["sentences"]
    seg_start = round(float(sentences[0]["start_time"]), 2)
    beats = beat_positions(steps, sentences, seg_start, dflt["duration"])
    pin = cam_default_origin(entry.get("inline_svg"), steps)
    out = {"segStart": seg_start, "camOrigin": pin, "steps": []}
    for step, (pos, dur) in zip(steps, beats):
        common = {
            "target": step["target"],
            "pos": round(pos, 2),
            "dur": round(dur, 2),
            "ease": step.get("ease", dflt["ease"]),
            "repeat": int(step.get("repeat", 0)),
            "yoyo": bool(step.get("yoyo")),
            "stagger": step.get("stagger"),
        }
        if "morph" in step:
            pf, pt = make_morph(step["morph"]["from"], step["morph"]["to"])
            ease_fn = ease_curve(step.get("ease"), dflt["ease"])
            cycles = beat_cycles(step) or 1
            yoyo = bool(step.get("yoyo"))
            span = beat_span(step, dur)
            n = max(2, min(240, round(span * fps * 2)))
            keys = []
            for i in range(n + 1):
                u = i / n
                phase = u * cycles
                c = int(phase)
                p = phase - c
                if p == 0.0 and c > 0:
                    # 正好踩在遍与遍的分界：GSAP 在这一瞬间报的是上一遍的末尾
                    c -= 1
                    p = 1.0
                if yoyo and c % 2:
                    p = 1.0 - p
                keys.append({"t": round(pos + u * span, 2),
                             "d": _morph_interp(pf, pt, ease_fn(p))})
            out["steps"].append(dict(common, kind="morph", morphKeys=keys))
        elif "count" in step:
            cnt = step["count"]
            out["steps"].append(dict(
                common, kind="count",
                count={"from": cnt.get("from", 0), "to": cnt["to"],
                       "decimals": int(cnt.get("decimals", 0)),
                       "prefix": cnt.get("prefix", ""),
                       "suffix": cnt.get("suffix", "")}))
        elif "type" in step:
            out["steps"].append(dict(common, kind="type"))
        elif step.get("draw"):
            out["steps"].append(dict(common, kind="draw"))
        elif "set" in step:
            out["steps"].append(dict(common, kind="set", vars=step["set"]))
        elif "from" in step and "to" in step:
            out["steps"].append(dict(common, kind="fromTo",
                                     fromVars=step["from"], toVars=step["to"]))
        elif "to" in step:
            out["steps"].append(dict(common, kind="to", toVars=step["to"]))
        elif "from" in step:
            out["steps"].append(dict(common, kind="from", fromVars=step["from"]))
        else:
            continue   # 无补间动作的 step：无数据可求值，跳过
    return out if out["steps"] else None


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

    # ── 配图归一（移植 gen_hyperframes 的 images 处理：agenda 结构性页键弹出、
    #    孤儿键忽略、媒体文件复制进 public/images、director 体检+净化内联+keep 烘焙）─
    #    必须先于 clips 几何：keep 页要把 wipe 归零（compute_clips 读 stage）。
    images = {}
    images_dir = None
    if args.images:
        if not os.path.exists(args.images):
            print(f"[warn] --images 文件不存在: {args.images}，本次渲染将不带配图"
                  "（纯文字版兜底）", file=sys.stderr)
        else:
            images_dir = os.path.dirname(os.path.abspath(args.images))
            try:
                images = load_images_json(args.images)
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
            seg_by_id = {seg["id"]: seg for seg in manifest["segments"]}
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
            # 媒体文件暂存进 public/images（director_prepare 读的是 out_dir 下的
            # 相对路径；poster 与 src 同档处理），同名同内容复用、撞名不同内容报错
            _staged_names = {}
            for sid, entry in list(images.items()):
                for key in ("src", "poster"):
                    raw = entry.get(key)
                    if not raw:
                        continue
                    resolved = (raw if os.path.isabs(raw)
                                else os.path.join(images_dir, raw))
                    rel = _stage_media(resolved, args.out, _staged_names)
                    if rel is None:
                        print(f"[error] 段落 '{sid}' 的 {key}={raw!r} 找不到或不可读，"
                              "拒绝生成（渲染会静默出空白裂图/无声媒体）", file=sys.stderr)
                        sys.exit(1)
                    entry[key] = rel
            # director 体检 + 净化内联 + stage:"keep" 跨段烘焙（errs 即 fail-fast，
            # 与 gen_hyperframes 同一口径；warns 打印）。此调用会原地改写 entry
            # 的 inline_svg：普通 SVG 仍是 <img>，只有 director/keep 走净化内联。
            # 循环变量不能叫 w：外层 w,h = get_canvas(aspect) 是画布宽，被这句
            # 覆盖后 data["width"] 会变成警告文本（实测踩过）。
            dir_errs, dir_warns = director_prepare(
                images, manifest["segments"], os.path.join(args.out, "public"))
            for warn_ in dir_warns:
                print(f"[warn] {warn_}", file=sys.stderr)
            if dir_errs:
                for e in dir_errs:
                    print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)

    clips = compute_clips(manifest, tpl, images)

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
    dflt_director = tpl["animation"]["director"]
    for clip in clips:
        seg = clip["seg"]
        sid = seg["id"]
        layout = seg_layout(seg)
        entry = images.get(sid)
        media = None
        image_mode = None
        svg = None
        director = None
        if layout != "agenda" and entry:
            if classify_media_path(entry["src"], entry.get("type", "auto")) == "video":
                image_mode = "video"
                media = {
                    "src": entry["src"],
                    "poster": entry.get("poster") or None,
                    "loop": entry.get("loop", True),
                    "muted": entry.get("muted", True),
                    "autoplay": entry.get("autoplay", True),
                    "playsinline": entry.get("playsinline", True),
                }
            elif entry.get("inline_svg"):
                # director / stage:"keep"：净化内联 SVG（keep 页的 inline_svg 是
                # 上一页演完画面烘焙出的副本，见 _stage_carry）；导演编排按步
                # 采样成数据胶，组件只按帧求值（morph 与 HTML 一样在生成期采样）。
                image_mode = "svgInline"
                svg = entry["inline_svg"]
                director = _director_data(entry, seg, dflt_director, args.fps)
            else:
                image_mode = "img"
                media = {"src": entry["src"]}
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
            "imageMode": image_mode,
            "media": media,
            "svg": svg,
            "director": director,
            "keep": bool(entry and entry.get("stage") == "keep"),
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
    print(f"     Media: {sum(1 for s in segs_out if s['imageMode'])}"
          f"（其中 svgInline {sum(1 for s in segs_out if s['imageMode'] == 'svgInline')}"
          f" / video {sum(1 for s in segs_out if s['imageMode'] == 'video')}"
          f" / keep {sum(1 for s in segs_out if s['keep'])})")
    print(f"     渲染: cd {os.path.abspath(args.out)} && "
          "npx remotion render src/index.ts ContentToVideo out.mp4")


if __name__ == "__main__":
    main()
