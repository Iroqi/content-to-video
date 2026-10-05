#!/usr/bin/env python3
"""timing_manifest.json → Remotion 渲染工程（数据胶生成器）。

读 timing_manifest.json（契约由 _manifest_schema 校验），算好每段的 clip 几何
（wipe/win_start/vis/peel 口径见 references/rendering.md「动画」），写进
remotion/src/generated.ts，并把音频/配图复制进 remotion/public/。remotion/ 里的
React 组件是静态脚手架，按绝对帧推导画面（每帧从时间线起点重算），生成期只换
数据胶。

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
from _script_utils import (setup_stdio, write_text_atomic,  # noqa: E402
                           guard_not_in_skill_dir)
from _images_schema import load_images_json, classify_media_path  # noqa: E402
from _timeline import beat_positions, beat_span, beat_cycles  # noqa: E402
from _path_morph import make_morph, interp as _morph_interp  # noqa: E402
from _ease import curve as ease_curve  # noqa: E402
from _cam_crop import cam_default_origin  # noqa: E402
from _director_prepare import (director_prepare,  # noqa: E402
                               canvas_layout_errors, validate_images_files)


def segment_duration(seg):
    """段落时长（秒）：末句 end − 首句 start。"""
    sents = seg["sentences"]
    return ((sents[-1].get("start_time", 0) + sents[-1].get("duration", 0))
            - sents[0].get("start_time", 0))


def _fmt_mmss(seconds):
    """秒 → 'm:ss'（agenda 行右侧时长列）。"""
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def _line_only_guard(tpl):
    """渲染端只实现了 line 档揭幕（引导线 + clip-path，ANIM.propLine）。

    模板把 segmentWipe.style 改成别的值时，clip 几何会算出一段没有对应渲染
    实现的 wipe——画面照出，但与模板意图对不上，属于"静默出片"。所以在任何
    写盘之前就 fail-fast，把话说明白：改档要先补渲染端。
    """
    if tpl["animation"]["segmentWipe"]["style"] != "line":
        raise ValueError(
            "[template] animation.segmentWipe.style 只支持 \"line\"（渲染端目前"
            "只实现了这一档）。改档需要先在 remotion/src/theme.ts 的 ANIM 里"
            "补上对应实现，别只改模板。")


def compute_clips(manifest, tpl, images=None):
    """逐条算 clips 预处理：每段的可见窗口、揭幕时长、剥离归属。

    同一段注释也搬过来：渲染引擎（本后端是 Remotion 逐帧）按窗口硬切卡片可见性，
    元素窗口必须精确覆盖"这张页在屏幕上"的全程——从擦除起点（本句音频起点 − 擦除
    时长）到下一页擦除完成把它盖住的时刻。
    """
    images = images or {}
    total_dur = float(manifest["total_duration"])
    segments = manifest["segments"]
    _line_only_guard(tpl)
    _wd = tpl["animation"]["propLine"]["duration"]
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
        # 烘焙的意义。
        if (seg_layout(clip["seg"]) == "canvas"
                or (images.get(clip["seg"]["id"]) or {}).get("stage") == "keep"):
            clip["wipe"] = 0.0
        clip["win_start"] = round(max(0.0, s_i - clip["wipe"]), 2)
        win_end = clips[i + 1]["start"] if i + 1 < len(clips) else round(total_dur, 2)
        clip["vis"] = round(win_end - clip["win_start"], 2)
        # line 档的剥离挂在被犁走的旧卡上（上一页被下一页的揭开窗口推走）。
        # 上一页是画布段时也不剥：活 diagram 不翻页。
        clip["peel"] = None
        if (i and clip["wipe"] > 0
                and seg_layout(clips[i - 1]["seg"]) != "canvas"):
            prev_sid = clips[i - 1]["seg"]["id"]
            clip["peel"] = {"sid": prev_sid, "wipe": clip["wipe"]}
            clips[i - 1]["gets_peeled"] = True
    return clips


def build_agenda_rows(seg_id, clips, manifest, ag, *, warn):
    """开屏/结尾 agenda 行（口径与 warn 同步）。"""
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


_SCAFFOLD_SRC = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "remotion")


def _scaffold_project(out_dir):
    """把仓库 remotion/ 的静态工程文件复制进输出目录，使 --out 成为
    自包含可渲染工程（npm install 后即可 npx remotion render）。

    复制：package.json / package-lock.json / tsconfig.json / remotion.config.ts /
    README.md / .gitignore / src/ 全部组件源码。
    排除：node_modules、演示成片 out*.mp4、src/generated.ts（由本次生成物覆盖）、
    public/（由生成器按本次素材重建，避免带进旧工程残留）。

    脚手架源与生成器同仓库（../remotion）；生成产物只覆盖数据胶与素材，
    组件代码是静态脚手架，两者分开。

    **不覆盖已存在且与脚手架不同的文件**：用户在 -o 目录里改过组件源码是本分
    （脚手架是起点不是终点），一把 copytree 盖回去等于把人家的活儿静默删掉。
    这类文件按 [warn] 报出来让人自己处置，与上面的约定一致。
    """
    if not os.path.isdir(_SCAFFOLD_SRC):
        raise RuntimeError(f"找不到 Remotion 脚手架源: {_SCAFFOLD_SRC}")
    os.makedirs(out_dir, exist_ok=True)
    skipped = []
    _copy_scaffold(_SCAFFOLD_SRC, out_dir, out_dir, skipped, top=True)
    for rel in sorted(set(skipped)):
        print(f"[warn] {rel} 与脚手架不同，已保留你的版本（想整体重置就把这个"
              "-o 目录删掉重生成）", file=sys.stderr)


# 脚手架侧的产物/依赖不进输出目录：node_modules 由用户在 -o 里 npm install，
# public 由生成器按本次素材重建，out*.mp4 是上一次的成片。
_KEEP_OUT_OF_OUTPUT = ("node_modules", "public")
# src 下由生成器产出的数据胶，不是脚手架的一部分
_GENERATED = "generated.ts"


def _prune_stale_assets(out_dir, data, images):
    """删掉 public/ 下本次数据胶没引用到的素材，返回被清理的相对路径列表。

    多次迭代会在 public 里攒下一堆不再被任何段落引用的旧图：它们不出现在画面
    上，却照样进 bundle、照样花打包时间。用户手放的素材不属于"本次生成的产物"，
    但那也属于"下一次生成时会被删"的一类——这条行为写在 remotion/README.md 里。
    """
    keep = {"images": set(), "audio": set()}
    for entry in images.values():
        # 注意：inline SVG（director / keep 档）在数据胶里是**内联字符串**，
        # media 为 null——只看 media.src 会把在用的 .svg 当成遗留素材删掉。
        for key in ("src", "poster"):
            v = entry.get(key)
            if v:
                keep["images"].add(os.path.basename(v))
    for seg in data["segments"]:
        media = seg.get("media") or {}
        for key in ("src", "poster"):
            v = media.get(key)
            if v:
                keep["images"].add(os.path.basename(v))
    if data.get("audioSrc"):
        keep["audio"].add(os.path.basename(data["audioSrc"]))

    removed = []
    for sub, names in keep.items():
        d = os.path.join(out_dir, "public", sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name in names:
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            try:
                os.remove(p)
            except OSError:
                continue
            removed.append(f"{sub}/{name}")
    return removed


def _differs(dst, src):
    """目标已存在且与源不同——别覆盖用户在 -o 里改过的东西。"""
    if not os.path.exists(dst):
        return False
    try:
        with open(src, "rb") as fs, open(dst, "rb") as fd:
            return fs.read() != fd.read()
    except OSError:
        return False


def _copy_scaffold(src, dst, out_root, skipped, top=False):
    """把脚手架 src 复制进 dst：**只补缺失**，已存在且不同的记进 skipped。

    整棵树都是"缺了才补"：连同 out_root 里用户自建的文件一并放过——删除用户
    文件的活儿不该由生成器做。
    """
    os.makedirs(dst, exist_ok=True)
    for n in sorted(os.listdir(src)):
        if top and (n in _KEEP_OUT_OF_OUTPUT
                    or (n.startswith("out") and n.endswith((".mp4", ".webm")))):
            continue
        if not top and n == _GENERATED:
            continue
        s2, d2 = os.path.join(src, n), os.path.join(dst, n)
        if os.path.isdir(s2):
            _copy_scaffold(s2, d2, out_root, skipped)
        elif _differs(d2, s2):
            skipped.append(os.path.relpath(d2, out_root))
        else:
            shutil.copy2(s2, d2)


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

    与渲染端同一套节拍解析（beat_positions：at 跟着 manifest 句子走、
    at_time 是段落绝对秒、delay 叠加）、同一份缺省 duration/ease、同一套 morph
    生成期采样（fps×2 关键帧 + 遍序奇偶折 yoyo，逐帧可复现、无运行时依赖）。count/type/draw/set/from/
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
    parser.add_argument("-o", "--out", default="remotion",
                        help="Output Remotion project directory (default: "
                             "./remotion)。-o 与 pipeline.py 同形状：两个入口都是"
                             "-o，从文档里拷命令不会有一条 unrecognized")
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

    # 产物不得落进技能目录（与 pipeline.py 同一道闸，见 SKILL.md 安全边界）：
    # 这一道闸必须在**任何写盘之前**跑——否则"这里不符合规范"的代价是用户的
    # 输出目录已经被铺了一层脚手架。
    out_abs = os.path.abspath(args.out)
    guard_not_in_skill_dir(("--out", out_abs))

    # 读 manifest / images / director 体检全在这一段：失败要能在**动手改工程
    # 之前**退出去。副作用（复制脚手架、写数据胶、清理素材）统一放在最后。
    try:
        manifest = load_timing_manifest(args.manifest)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    tpl = load_template()
    aspect = normalize_aspect(args.aspect)
    w, h = get_canvas(aspect)
    total_dur = float(manifest["total_duration"])

    # 模板自检要在**任何写盘之前**：wipe 档位不匹配时退出，而不是先复制了
    # 素材再报错（下面 _stage_asset 一跑，用户目录就已被铺过文件了）。
    # 这里只验模板本身（clips 真几何要等 images 归一完，keep 页的 wipe 归零
    # 依赖 stage，见下面第二次 compute_clips）。
    try:
        _line_only_guard(tpl)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # ── 配图归一（agenda 结构性页键弹出、孤儿键忽略、媒体文件复制进
    #    public/images、director 体检+净化内联+keep 烘焙）──
    #    必须先于 clips 几何：keep 页要把 wipe 归零（compute_clips 读 stage）。
    images = {}
    images_dir = None
    public_dir = os.path.join(args.out, "public")
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
            # warns 打印）。此调用会原地改写 entry
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

            # ── 素材门禁：这两道闸是本支路唯一能拦住"整页空白 / 图元被裁掉"的
            #    地方——不拦的话，缺图与坏图就会一路静默进成片。
            missing, corrupt = validate_images_files(images, public_dir)
            for sid, path in missing:
                print(f"[error] 段落 '{sid}' 的 {path} 在工程里找不到（--images "
                      "指到了别处？）——拒绝生成，渲染会静默出空白裂图",
                      file=sys.stderr)
            for sid, path, why in corrupt:
                print(f"[error] 段落 '{sid}' 的 {path} {why}——拒绝生成，"
                      "渲染阶段才炸就白烧一整轮", file=sys.stderr)
            if missing or corrupt:
                sys.exit(1)

    # ── 画布段体检（无图 / 比例不对 / 字号被缩到读不出来）──
    #    独立于 --images：整页画布没有配图就是**整页空白**，与槽位版式"没图还有
    #    标题和字幕"不是一回事。不传 --images 时这条同样要拦，否则一次忘给参数
    #    就换来一段静默的空白成片（SKILL.md 的 fail-fast 是针对"缺图"这件事，
    #    不是针对"传了 images.json"这个动作）。
    c_errs, c_warns = canvas_layout_errors(
        images, manifest["segments"], public_dir, w, h)
    for warn_ in c_warns:
        print(f"[warn] {warn_}", file=sys.stderr)
    if c_errs:
        for e in c_errs:
            print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # keep 页要在 wipe 归零后才算得对（compute_clips 读 stage），所以带 images 重算。
    clips = compute_clips(manifest, tpl, images)

    audio_src = None
    if args.audio:
        audio_src = _stage_asset(args.audio, args.out, "audio")
    else:
        audio_abs = manifest.get("combined_audio")
        if audio_abs and os.path.isfile(audio_abs):
            audio_src = _stage_asset(audio_abs, args.out, "audio")
        elif audio_abs:
            # manifest 点了名却读不到：这不是"作者没要音频"，是缓存被清了 /
            # 目录被挪了。成片照样出，只是全程无声——那是最贵的一类废品，
            # 因为要等到渲染完才看得出来。所以这里 fail-fast（SKILL.md 安全
            # 边界：音频缺失直接失败，不生成无声成片）。
            print(f"[error] manifest 的 combined_audio 指向的文件不存在或不可读："
                  f"{audio_abs!r}——拒绝生成无声成片；确认后重跑 pipeline，或用 "
                  "--audio 显式指定。", file=sys.stderr)
            sys.exit(1)
        else:
            # manifest 本身不带音频字段：手写 manifest 做画面检查的合法用法，
            # 出声提醒但不拦。
            print("[warn] manifest 没有 combined_audio 字段，成片将无声；"
                  "要配音就先跑 pipeline.py，或用 --audio 显式指定",
                  file=sys.stderr)

    # ── 段数据 → generated.ts 数据胶 ────────────────────────────────────────
    #    cta 尾行只有"存在 closing 段且它是 agenda 版式"时才有地方画；另外两种
    #    情形（没有 closing 段 / 结尾页换成整页画布）它在画面上静默消失，作者
    #    只能从成片里发现。这里按 tts_pipeline.md 的承诺出声（不拦：cta 是可选
    #    装饰，不是成片能不能用的问题）。
    if str(manifest.get("closing_cta") or "").strip():
        closing = next((s for s in manifest["segments"]
                        if s["id"] == "closing"), None)
        if closing is None or seg_layout(closing) != "agenda":
            print("[warn] manifest 有 closing_cta，但本次没有 agenda 版式的结尾页"
                  "可承载它（没有 closing 段，或结尾页是整页画布）——这行不会出现在"
                  "画面上", file=sys.stderr)

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
                # autoplay/playsinline 不往下带：契约层仍收下这两个键（既有
                # images.json 照旧过校验），但逐帧渲染里没有"自动播放"这回事，
                # 传下去只会让人以为渲染端会读它。
                media = {
                    "src": entry["src"],
                    "poster": entry.get("poster") or None,
                    "loop": entry.get("loop", True),
                    "muted": entry.get("muted", True),
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

    # ── 到这一步为止全是"算"：上面任何一处 sys.exit 都不会动工程一个字节。
    #    从这儿开始才有副作用（脚手架 → 数据胶 → 素材清理）。
    try:
        _scaffold_project(args.out)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

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

    # public/ 按本次素材重建（脚手架不动它）：上一次迭代留下的图会一直躺在
    # bundle 里，看着像"我删了这张图它怎么还在"。
    pruned = _prune_stale_assets(args.out, data, images)

    print(f"[OK] {gen_path}")
    print(f"     Duration: {total_dur}s @ {args.fps}fps")
    print(f"     Aspect: {aspect} ({w}x{h})")
    print(f"     Segments: {len(segs_out)}")
    print(f"     Audio: {audio_src or '无（成片将无声）'}")
    if pruned:
        print(f"     清理遗留素材 {len(pruned)} 个（本次数据胶没引用）："
              f"{'、'.join(pruned[:5])}"
              f"{'…' if len(pruned) > 5 else ''}")
    print(f"     Media: {sum(1 for s in segs_out if s['imageMode'])}"
          f"（其中 svgInline {sum(1 for s in segs_out if s['imageMode'] == 'svgInline')}"
          f" / video {sum(1 for s in segs_out if s['imageMode'] == 'video')}"
          f" / keep {sum(1 for s in segs_out if s['keep'])})")
    print(f"     渲染: cd {os.path.abspath(args.out)} && "
          "npx remotion render src/index.ts ContentToVideo out.mp4")


if __name__ == "__main__":
    main()
