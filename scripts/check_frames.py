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

抽帧与像素读取都走 `ffmpeg`（`-ss T` 精确 seek + `-f rawvideo` 取像素）：
每个时间点一次精确 seek，抓"那一秒观众实际看到的那一帧"，不做关键帧对齐的
猜测（对齐猜错会抽到隔壁帧，等于测了个寂寞）。ffmpeg 是本技能环境里唯一要
额外装的依赖，探测与降级口径跟 run.py 的时长契约一致：量不到只降级，不把
"没法验证"当成"验证失败"。

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
_SAMPLE_W = 320             # 像素统计的降采样宽度（高按比例）：够判"死色"，不背全幅开销


def sample_timestamps(dur, every=_DEFAULT_EVERY, cap=_MAX_FRAMES):
    """挑抽帧时刻，返回 (时刻列表, 是否被上限放宽)。

    早先是"从 t=0 起每 `every` 秒加一个，加满 `cap` 个就停"——上限一命中，长片
    就只覆盖开头 `every×(cap-1)` 秒（默认 3×39 = 117s），而报告照样打"通过：
    40 帧全部过断言"。一支 10 分钟的片子有八成从没被看过一眼，这比不抽帧更糟：
    它把"抽查"伪装成了"全查"。上限是**成本**上限，不是覆盖范围的许可证。

    装不下就按整片均分（stride = dur / cap，首帧仍是 t=0），并让调用方把"只
    抽查了一部分、每多少秒一帧"写进报告——被抽查的范围必须和结论一起交付。
    """
    dur = float(dur)
    if not every or every <= 0:
        every = _DEFAULT_EVERY
    n = int(dur // every) + 1
    stride = every
    relaxed = False
    if n > cap:
        n = int(cap)
        stride = dur / n
        relaxed = True
    return [round(i * stride, 3) for i in range(n)], relaxed


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


def analyze_frame(png, ffmpeg_path=None):
    """单帧像素断言指标：非黑占比 + 量化颜色桶数。

    像素由 ffmpeg 解码成降采样后的原始 RGB（`-f rawvideo -pix_fmt rgb24`），
    Python 侧只做两条逐像素计数——不引 PIL/numpy。它们不是本技能承诺的环境
    （SKILL.md「环境」：标准库 + ffmpeg），靠它们这道闸在承诺环境下必然空转
    （import 失败 → 每帧 None → "一帧都没量到" → 跳过，而退出码还是 0）。

    降采样只影响精度、不影响判据：两条门槛（1% 亮像素 / 3 个颜色桶）量的是
    "整帧是不是一块死色"，不是"每个字清不清楚"。宽 `_SAMPLE_W`、高按比例，
    面积平均不会让一块亮区消失，只会让它变软——黑底白字的正常开场帧照样过。

    ffmpeg 不可用或解码失败返回 None，调用方按"这一帧量不到"降级。
    """
    ff = ffmpeg_path or _get_ffmpeg()
    if not ff:
        return None
    try:
        r = subprocess.run(
            [ff, "-v", "error", "-i", png,
             "-vf", f"scale={_SAMPLE_W}:-2", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"],
            capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    px = r.stdout
    n = len(px) // 3
    if n == 0:
        return None
    # 任一通道明显亮于黑就算"非黑"
    nonblack = sum(1 for i in range(0, n * 3, 3)
                   if max(px[i], px[i + 1], px[i + 2]) > 16)
    # 颜色桶数：量化到 8 档/通道（>>3，32 级）。>>5 会把暗部帧（值都在 32
    # 以下）整帧压进 (0,0,0) 一个桶——深色底 + 标题字的正常开场帧会被误判成
    # "死平"，所以量化粒度取能区分"黑底上一块亮色"的这档。
    buckets = {(px[i] >> 3, px[i + 1] >> 3, px[i + 2] >> 3)
               for i in range(0, n * 3, 3)}
    return {"nonblack_ratio": round(nonblack / n, 4),
            "color_buckets": len(buckets)}


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
        relaxed = False
    else:
        ts, relaxed = sample_timestamps(dur, every)

    failures, report = [], []
    # 超出成片的时刻抽不到帧（seek 到片尾之后 ffmpeg 给不出画面），必须**说出去**：
    # 静默把这些点丢掉、退回 [0.0]，等于把"你指定的时刻一帧都没看"报成"看过了、
    # 全过"——run.py 侧把它记成 check:ok，正是假守卫那一类。
    kept = [t for t in ts if t < dur - 1e-3]
    if not kept:
        report.append(
            f"[coverage] {'--timestamps 给的' if timestamps else '算出来的'}时刻"
            f"（{', '.join(f'{t:g}s' for t in ts[:5])}）全都在成片 {dur:.1f}s 之外，"
            f"只能退到首帧——{'' if timestamps else '这次'}抽帧覆盖不到你要看的时刻。"
            + ("请给成片范围内的 --timestamps。" if timestamps else ""))
        kept = [0.0]
    elif timestamps and len(kept) < len(ts):
        # 只对显式 --timestamps 出声：自动抽样的末点落在片尾是预期的（它本来就
        # 只是"抽查"），点名要的时刻被丢掉才是该说的那件事。
        report.append(
            f"[coverage] --timestamps 里有 {len(ts) - len(kept)} 个时刻超出成片 "
            f"{dur:.1f}s 已跳过（留在范围内的 {len(kept)} 个照抽）。")
    ts = kept
    if relaxed:
        # 采样被上限放宽过：必须把"抽了多少、覆盖到哪儿"和结论一起交出去，
        # 否则一句"通过"会让人以为整片都看过了。
        report.append(
            f"[coverage] 只抽查了 {len(ts)} 帧：{dur:.1f}s 按 --every {every:g}s 要 "
            f"{int(dur // every) + 1} 帧，超过 {_MAX_FRAMES} 帧上限，已按整片均分"
            f"（每 {dur / _MAX_FRAMES:.1f}s 一帧，覆盖到 {ts[-1]:.1f}s）。"
            f"要逐段细看就分段跑并给 --timestamps。")
    measured = 0
    with tempfile.TemporaryDirectory(prefix="check_frames_") as td:
        for i, t in enumerate(ts):
            png = os.path.join(td, f"f{i:03d}.png")
            ok = extract_frame(ff, video, t, png)
            metrics = analyze_frame(png, ffmpeg_path=ff) if ok else None
            line = f"[{t:7.2f}s]"
            if metrics is None:
                report.append(f"{line} 抽帧/分析失败：跳过（不判失败，但这条没人证明）")
                continue
            measured += 1
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
                    # OCR 降级只降级 OCR 那一条：不能 `continue`——那会把这一帧已经
                    # 量出来的黑屏/死平断言一起吞掉，--ocr 开着又没装 tesseract 时
                    # 整支全黑片会报"0 帧失败、通过"。
                    line += "  OCR 不可用（像素断言仍生效）"
                elif not text.strip():
                    if (t == 0.0
                            and metrics["nonblack_ratio"] >= min_bright
                            and metrics["color_buckets"] >= min_colors):
                        # 首帧 t=0 恰是开场擦除转场帧：整页从 clip-path inset(100%)
                        # 揭开，0 时刻全被裁剪，画面里没有任何文字是**预期**（像素
                        # 断言已过）。把它当字幕丢失误报，会让 --check-frames-ocr
                        # 对默认开场形态必然失败。字幕层真丢时 t>0 的帧照常无词、
                        # 照常失败——这里只豁免转场那一帧，门禁不削弱。
                        line += "  OCR（首帧转场帧，无词属预期，跳过判定）"
                    else:
                        probs.append("OCR 认不出任何词（字幕/大字版式疑似丢失）")
                else:
                    line += f"  OCR「{text.strip()[:24]}」"
            if probs:
                failures.append(f"{line}  ✗ " + "；".join(probs))
            else:
                line += "  ✓"
            report.append(line)
    if measured == 0:
        # 一帧都没量到（每帧抽帧/分析都失败）不是"全过"：返回 None 让调用方
        # 按"没量到"降级，与 ffmpeg 不可用同一口径。
        return None, report + [
            f"[coverage] {len(ts)} 帧一帧都没量到：跳过抽帧断言"
            "（ffmpeg 在但抽帧/解码失败时看上一行原因）"]
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
        # coverage 行不是一帧，但它是"这些帧覆盖了什么"的唯一说明，--quiet 也留着
        if args.quiet and "✗" not in line and "跳过" not in line \
                and "coverage" not in line:
            continue
        print(f"[check_frames] {line}")
    if failures:
        print(f"\n[check_frames] 失败 {len(failures)} 帧：成片画面断言未通过。"
              "先查渲染日志（成片同目录的 out.render.log）里有没有"
              "`Parallel capture timed out`；有的话降 --workers 重渲。"
              "确认是某一段有意全黑（如过场）时，用 --timestamps 绕过该段再核。",
              file=sys.stderr)
        return 1
    _lines = [l for l in report if "coverage" not in l]
    _missed = len([l for l in _lines if "失败：跳过" in l])
    print(f"[check_frames] 通过：{len(_lines) - _missed} 帧过断言"
          + (f"，{_missed} 帧没量到（不算通过，只是没人证明）" if _missed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
