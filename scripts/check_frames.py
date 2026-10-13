#!/usr/bin/env python3
"""成片抽帧断言：渲染完的 mp4 不是"全黑 / 死平 / 整段空帧"的空片。

Anatomy 的验证工作流叫「渲染便宜，猜是贵的」：成片能播、时长对得上，都证明
不了画面里真的长出了内容——抓帧超时重抓、worker 崩、Chrome 提前退出可能产
出一支时长刚好、画面整段全黑的片子，而控制台照打"完成"。这里在成片落地后
直接抽帧量画面，不猜：

  - 非黑占比：帧里亮度超过阈值的像素比例 ≥ min_bright。全黑/近黑帧当场暴露；
  - 色彩多样度：量化后颜色桶数 ≥ min_colors。单色死平帧（一块纯色盖满）暴露；
  - 可选 --ocr：装了 tesseract 才跑整帧识别，断言至少认出一个词。字幕层/大字
    版式整段丢失的片子会暴露；没装 tesseract 只降级为像素断言，不算失败。

抽帧用 `ffmpeg -ss T -i … -frames:v 1`：每个时间点一次精确 seek，抓"那一秒
观众实际看到的那一帧"，不做关键帧对齐的猜测（对齐猜错会抽到隔壁帧，等于测
了个寂寞）。ffmpeg 是本技能的可选依赖，探测与降级口径跟 run.py 的时长契约
一致：量不到只降级，不把"没法验证"当成"验证失败"。

用法：
  python scripts/check_frames.py 成片.mp4 [--every 3] [--min-bright 0.01]
      [--min-colors 3] [--ocr] [--quiet]

退出码：0=全部断言通过；1=有帧没通过（逐帧列出）；2=参数/读取错误。
"""
import argparse
import os
import subprocess
import sys
import tempfile

_DEFAULT_EVERY = 3.0        # 每隔几秒抽一帧（首帧 t=0 必抽）
_DEFAULT_MIN_BRIGHT = 0.01  # 非黑像素占比下限：1% 亮像素都没有 ≈ 黑屏
_DEFAULT_MIN_COLORS = 3     # 量化颜色桶数下限：一两种颜色 ≈ 死平
_MAX_FRAMES = 40            # 抽帧上限：长片也控制在几十秒量级，不把验证拖垮


def _get_ffmpeg():
    try:
        from _audio import get_ffmpeg
        return get_ffmpeg()
    except Exception:
        return None


def _probe_duration(ff, video):
    """成片时长（秒）；量不到返回 None。复用仓库同一份时长解析，不新写正则。"""
    try:
        from _render_backend import probe_video_duration
        return probe_video_duration(video, ffmpeg_path=ff)
    except Exception:
        return None


def extract_frame(ff, video, t, out_png):
    """抽 t 秒处的一帧到 out_png；失败抛 subprocess 异常/返回 False。"""
    try:
        r = subprocess.run(
            [ff, "-y", "-ss", f"{t:.3f}", "-i", video,
             "-frames:v", "1", "-q:v", "2", out_png],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and os.path.isfile(out_png) and os.path.getsize(out_png) > 0


def analyze_frame(png):
    """单帧像素断言指标：非黑占比 + 量化颜色桶数（尺寸取帧图实际像素）。

    依赖 PIL/numpy——它们不是本技能承诺的环境，import 失败时返回 None，
    调用方按"这一帧量不到"降级（不把缺依赖当成画面失败）。
    """
    try:
        from PIL import Image
        import numpy as np
    except Exception:
        return None
    try:
        img = Image.open(png).convert("RGB")
        arr = np.asarray(img, dtype=np.uint16)
    except Exception:
        return None
    if arr.shape[0] * arr.shape[1] == 0:
        return None
    mx = arr.max(axis=2)
    nonblack = float((mx > 16).mean())          # 任一通道明显亮于黑就算"非黑"
    # 颜色桶数：量化到 8 档/通道（//8，32 级）。//32 会把暗部帧（值都在
    # 32 以下）整帧压进 (0,0,0) 一个桶——深色底 + 标题字的正常开场帧会被
    # 误判成"死平"，所以量化粒度取能区分"黑底上一块亮色"的这档。
    buckets = set()
    q = (arr // 8).astype(np.uint8)
    for row in q.reshape(-1, 3):
        buckets.add((row[0], row[1], row[2]))
    return {"nonblack_ratio": round(nonblack, 4), "color_buckets": len(buckets)}


def check_video(video, every=_DEFAULT_EVERY, min_bright=_DEFAULT_MIN_BRIGHT,
                min_colors=_DEFAULT_MIN_COLORS, ocr=False, quiet=False,
                timestamps=None):
    """对成片逐帧断言，返回 (failures, report)。

    failures 是非空列表即未通过；report 是给人类看的逐帧明细。ffmpeg 不可用
    或时长量不到时返回 (None, [])——调用方按"没量到"降级（run.py 的时长契约
    同一口径），绝不把"验证不可用"冒充成"画面失败"。
    """
    ff = _get_ffmpeg()
    if not ff:
        return None, ["ffmpeg 不可用：跳过抽帧断言（装好 ffmpeg 后自动生效）"]
    dur = _probe_duration(ff, video)
    if dur is None:
        return None, ["成片时长量不到：跳过抽帧断言（无法决定抽哪些时间点）"]

    if timestamps:
        ts = [float(t) for t in timestamps]
    else:
        n = min(int(dur // every) + 1, _MAX_FRAMES)
        ts = [round(i * every, 3) for i in range(n)]
    ts = [t for t in ts if t < dur - 1e-3] or [0.0]

    failures, report = [], []
    with tempfile.TemporaryDirectory(prefix="check_frames_") as td:
        for i, t in enumerate(ts):
            png = os.path.join(td, f"f{i:03d}.png")
            ok = extract_frame(ff, video, t, png)
            metrics = analyze_frame(png) if ok else None
            line = f"[{t:7.2f}s]"
            if metrics is None:
                report.append(f"{line} 抽帧/分析失败：跳过（不判失败，但这条没人证明）")
                continue
            line += (f" 非黑 {metrics['nonblack_ratio']*100:5.1f}%  "
                     f"颜色桶 {metrics['color_buckets']}")
            probs = []
            if metrics["nonblack_ratio"] < min_bright:
                probs.append(f"非黑 {metrics['nonblack_ratio']*100:.1f}% < "
                             f"{min_bright*100:.1f}%（疑似黑屏/整段空帧）")
            if metrics["color_buckets"] < min_colors:
                probs.append(f"颜色桶 {metrics['color_buckets']} < {min_colors}（疑似死平/纯色帧）")
            if ocr:
                text = _ocr_text(png)
                if text is None:
                    report.append(line + "  OCR 不可用：跳过（像素断言仍生效）")
                    continue
                if not text.strip():
                    probs.append("OCR 认不出任何词（字幕/大字版式疑似丢失）")
                else:
                    line += f"  OCR「{text.strip()[:24]}」"
            if probs:
                failures.append(f"{line}  ✗ " + "；".join(probs))
            else:
                line += "  ✓"
            report.append(line)
    return failures, report


def _ocr_text(png):
    """整帧 OCR；tesseract 缺失/识别失败返回 None（降级，不算失败）。"""
    try:
        r = subprocess.run(["tesseract", png, "-", "-l", "chi_sim+eng"],
                           capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description="成片抽帧断言：画面不是黑屏/死平/空帧")
    ap.add_argument("video", help="成片 mp4 路径")
    ap.add_argument("--every", type=float, default=_DEFAULT_EVERY,
                    help=f"每隔几秒抽一帧（默认 {_DEFAULT_EVERY:g}）")
    ap.add_argument("--timestamps", default=None,
                    help="显式抽帧时间点，逗号分隔（覆盖 --every）")
    ap.add_argument("--min-bright", type=float, default=_DEFAULT_MIN_BRIGHT,
                    help=f"非黑像素占比下限（默认 {_DEFAULT_MIN_BRIGHT:g}）")
    ap.add_argument("--min-colors", type=int, default=_DEFAULT_MIN_COLORS,
                    help=f"量化颜色桶数下限（默认 {_DEFAULT_MIN_COLORS}）")
    ap.add_argument("--ocr", action="store_true", help="额外跑整帧 OCR，断言至少认出一个词")
    ap.add_argument("--quiet", action="store_true", help="只输出失败帧")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.video):
        print(f"[check_frames] 找不到成片：{args.video}", file=sys.stderr)
        return 2
    ts = None
    if args.timestamps:
        try:
            ts = [float(x) for x in args.timestamps.split(",")]
        except ValueError:
            print("[check_frames] --timestamps 必须是逗号分隔的数字", file=sys.stderr)
            return 2

    failures, report = check_video(
        args.video, every=args.every, min_bright=args.min_bright,
        min_colors=args.min_colors, ocr=args.ocr, quiet=args.quiet,
        timestamps=ts)
    if failures is None:
        for line in report:
            print(f"[check_frames][skip] {line}")
        return 0
    for line in report:
        if args.quiet and "✗" not in line and "跳过" not in line:
            continue
        print(f"[check_frames] {line}")
    if failures:
        print(f"\n[check_frames] 失败 {len(failures)} 帧：成片画面断言未通过。"
              "先查渲染日志（成片同目录的 out.render.log）里有没有"
              "`Parallel capture timed out`；有的话降 --workers 重渲。"
              "确认是某一段有意全黑（如过场）时，用 --timestamps 绕过该段再核。",
              file=sys.stderr)
        return 1
    print(f"[check_frames] 通过：{len(report)} 帧全部过断言")
    return 0


if __name__ == "__main__":
    sys.exit(main())
