#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把 TTS → 配图 → HTML → 渲染串成一条命令，内部按依赖顺序调用各步骤；
不改变任何子脚本的独立性，只做组合与默认值。

TTS / 生成 HTML 两步是**进程内直调**子脚本的 main(argv)（参数校验只在
子脚本自己那一份 parser 里做一次，错误以 SystemExit 传回、语义与旧的
子进程退出码一致）；全文件唯一的子进程是 `npx hyperframes render`，
由 _render_backend 负责进程树与成片完整性。

  python scripts/run.py --source segments_source.json -o audio_output --until html
  # ↑ 迭代用：跑到 HTML 为止，在 hf-project/ 打开 index.html 或 preview 看版式和配图。
  python scripts/run.py --source segments_source.json -o audio_output
  # ↑ 定稿用：一路渲染到 hf-project/out.mp4。
"""
import argparse
import datetime
import math
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, SCRIPTS_DIR)
from _theme import list_theme_names, DEFAULT_THEME  # noqa: E402  --theme choices 与 _theme 内嵌注册表同步
from _template import get_canvas  # noqa: E402  画幅 → 画布尺寸（生产报告 params.canvas 用）
from _timeline import DEFAULT_SPEED, validate_speed  # noqa: E402
from _voices import list_voice_ids  # noqa: E402
from _segments import sids_needing_image, seg_layout  # noqa: E402
from _manifest_schema import (load_timing_manifest,  # noqa: E402
                              validate_timing_manifest)
from _preview_slice import chain_sids, build_subset, subset_images  # noqa: E402
from _audio import get_ffmpeg, measure_duration, trim_audio  # noqa: E402
from _images_schema import load_images_json  # noqa: E402
from _degraded import items as degraded_items  # noqa: E402  降级注册表（词汇/人话同源）
from _render_backend import (hyperframes_command,  # noqa: E402
                             build_render_command, render_wait)
from _script_utils import (setup_stdio, write_json_atomic,  # noqa: E402  重定向 UTF-8 + 报告原子写
                           guard_not_in_skill_dir)  # noqa: E402  产物落技能目录的守卫（与 pipeline/gen 共用同一实现）
import pipeline  # noqa: E402  进程内直调 TTS 步骤
import gen_hyperframes  # noqa: E402  进程内直调 HTML 生成步骤

# ── 制作报告：一次 run.py 跑完后，各步骤耗时/配图情况/跳过了什么散落在各步骤
# 自己的 stdout 里，人工翻起来很累。这里不改任何步骤的行为，只在旁路记一份轻量
# 流水账，跑完打印小结 + 写 production_report.json，让"这次跑得正不正常"一眼可
# 判。任何一步失败退出前也会把已记录的部分写盘。
_REPORT = {"steps": [], "images": None, "skipped": [], "degraded": []}
# 制作报告的 out 目录：main() 一解析出 -o/--output 就赋值
_REPORT_DIR = None
# 制作报告的文件名。正式流程是 production_report.json；--only 预览换成
# preview_<段id>.report.json —— 预览片不该把上一次正式跑的报告覆盖掉
# （main() 里无条件赋值）。
_REPORT_FILE = "production_report.json"
# --dry-run 承诺"不写任何文件"（见参数 help），旁路制作报告也不能例外
# ——失败路径同样只打印不落盘。main() 解析完参数就置 True。
_DRY_RUN = False
# 整个 run 的起始时刻：total_seconds 用真实墙钟，而不是把各步骤耗时直接
# 相加（TTS 与配图并行时两步各记各的全程，相加会大于真实墙钟）。
# main() 第二时间无条件赋值。
_RUN_T0 = None


def _total_seconds():
    """本次 run 的真实墙钟时长。"""
    return round(time.time() - _RUN_T0, 1)


def _write_report(path):
    if _DRY_RUN:
        return
    try:
        _REPORT["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _REPORT["total_seconds"] = _total_seconds()
        # 原子写走 _script_utils.write_json_atomic：
        # production_report 被 Ctrl-C 打断在写一半时落截断 JSON，下次排查
        # "这次跑得正不正常"反而先崩在 json.load。
        write_json_atomic(path, _REPORT)
    except Exception as e:
        print(f"[run] 写制作报告失败（不影响主流程产出）：{e}", file=sys.stderr)


def _print_report_summary():
    print("\n" + "=" * 44)
    print("[run] 本次制作报告")
    print("=" * 44)
    for s in _REPORT["steps"]:
        tag = "OK  " if s["ok"] else "FAIL"
        print(f"  [{tag}] {s['name']:<28} {s['seconds']:>7.1f}s")
    total = _total_seconds()
    print(f"  {'合计':<34} {total:>7.1f}s")
    if _REPORT.get("degraded"):
        print("  降级：" + "; ".join(
            f"{d.get('type')}={d.get('count', '?')}" for d in _REPORT["degraded"]))
    if _REPORT["images"] is not None:
        img = _REPORT["images"]
        print(f"  配图：{img['matched']}/{img['total']} 条内容已定稿"
              + (f"，{img['missing']} 条待人工审阅/兜底" if img.get("missing") else ""))
    if _REPORT["skipped"]:
        print("  跳过的步骤：" + "；".join(_REPORT["skipped"]))
    print("=" * 44)


def _run_step(fn, argv, step_name):
    """进程内直调子脚本入口 fn(argv)，退出语义与旧子进程调用一致。

    子脚本自己 parse_args——参数校验只有那一份，run.py 不再复制；
    失败以 SystemExit 传回：非 0 记失败步骤、落制作报告、以同码退出。
    sys.exit("消息") 这种字符串码在子进程里由解释器代打并归一为 1，
    进程内没人代打，这里补打。
    """
    print(f"\n>>> {step_name} [in-process]: {' '.join(argv)}", flush=True)
    t0 = time.time()
    code = 0
    try:
        fn(argv)
    except SystemExit as e:
        if e.code is None:
            code = 0
        elif isinstance(e.code, int):
            code = e.code
        else:
            code = 1
            print(str(e.code), file=sys.stderr, flush=True)
    if code != 0:
        _REPORT["steps"].append(
            {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
        _write_report(_report_path())
        print(f"[run] 步骤失败（退出码 {code}）：{step_name} {' '.join(argv)}",
              file=sys.stderr)
        sys.exit(code)
    _REPORT["steps"].append(
        {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": True})


def _report_path():
    """制作报告写到哪：out 目录（跟 timing_manifest.json 放一起），
    main() 在首个写报告点之前无条件赋 _REPORT_DIR。"""
    return os.path.join(_REPORT_DIR, _REPORT_FILE)


def _load_manifest_or_exit(manifest_path):
    """读并校验 timing_manifest.json，坏文件报成人话而不是 traceback。

    走 _manifest_schema 的加载器而不是裸 json.load：上一轮若被中断在写 manifest
    的半途，文件是截断的 JSON，裸 load 会抛 JSONDecodeError traceback，
    用户看到的堆栈跟"TTS 产物坏了、该重跑"这个真实原因毫无关系。
    load_timing_manifest 把缺字段/坏结构报成一句人话（ValueError），这里转成
    带路径的退出——正式流程与 --only 预览共用这一份口径。
    """
    try:
        return load_timing_manifest(manifest_path)
    except ValueError as e:
        raise SystemExit(
            f"[run] timing_manifest.json 无法读取（{manifest_path}）：{e}\n"
            "文件可能被上次中断的写入截断或结构不合法；"
            "请重跑 TTS 步骤重新生成后再来。")


def _image_coverage(manifest_path, images_json):
    """返回 (tm, all_sids, missing_keys, missing_files)：已校验的 manifest 对象
    （主流程的降级检测直接复用，同一份文件不在一次 run 里 load+全量校验两遍），
    manifest 中需要配图的段落 sid 全集，其中 images.json 还没有映射键的（missing_keys），以及
    有键但媒体文件不在盘上的（missing_files）。

    两类必须分开：missing_keys 只是"图还没画完"，--until html 可以警告放行；
    missing_files 是"映射指向坏路径"，gen_hyperframes 无论跑到哪一步都会
    fail-fast——把它混进"只警告"里等于承诺一个根本兑现不了的继续（实测
    先打印"继续生成 HTML"、下一步整体 exit 1）。

    口径收口在 _segments.needs_image：内容段一律需要配图；开屏/结尾默认是纯文字
    agenda 卡（章节罗列/要点总结），两画幅都不配图，但那一页换成整页画布
    （opening_layout / closing_layout: "canvas"）后，配图就是它唯一的画面，同样计入。
    """
    # 走 _load_manifest_or_exit：正式流程与 --only 预览对坏 manifest 的报错同源。
    manifest = _load_manifest_or_exit(manifest_path)
    # 段 id 的唯一来源：契约已把 manifest["segments"] 钉成非空列表，渲染端读的
    # 也是同一份；这里另推一套分组只会让缺图拦截与画面段 id 漂移。口径走
    # _segments.sids_needing_image（与 gen_hyperframes 的缺图提示同一函数）。
    sids = sids_needing_image(manifest)
    if not sids:
        return manifest, [], [], []
    if not os.path.isfile(images_json):
        return manifest, sids, sids, []
    # 读不出来的三种原因（不存在/不是 UTF-8/语法错误）已由 load_images_json 统一
    # 成点名文件的 ValueError；这里只补一句"该补图/该重写映射"的行动指引——
    # 上次写入被中断留下的截断 JSON，裸 json.load 的 traceback 指不到真实原因。
    try:
        mapping = load_images_json(images_json)
    except ValueError as e:
        raise SystemExit(
            "[run] images.json 读不出来——文件可能被上次中断的写入截断，或不是"
            "合法 JSON；修复或手写该文件后重跑本命令。"
            f"\n{e}")
    missing_keys = [s for s in sids if s not in mapping]
    proj_dir = os.path.dirname(os.path.abspath(images_json))

    missing_files = [s for s in sids
                     if s in mapping and not os.path.isfile(
                         os.path.join(proj_dir, mapping[s]["src"]))]
    return manifest, sids, missing_keys, missing_files


# 只作用于 TTS 步骤的旗标：--only 复用上次配音产物，一个都不消费它们。
_TTS_ONLY_FLAGS = ("speed", "voice_id", "voice_style", "gap", "bgm",
                   "bgm_volume", "loudness", "on_fail", "no_resume")


def _run_preview(args, out, project):
    """``--only <段id>``：把一页（连同 stage:"keep" 的接续前缀）单独渲成预览片。

    与正式流程的三条边界，都是刻意的：

    1. **不跑 TTS**。读现成的 timing_manifest.json，按段窗口用 ffmpeg 切一小段
       音频、时间轴整体重基到 0（_preview_slice）。改配音就没有"快"可言了。
    2. **正式产物一个都不碰**：index.html / out.mp4 / production_report.json
       原地不动，预览件全部以 ``preview_<段id>.`` 前缀落在项目目录里。用前缀而不
       是子目录，是因为 images.json 的 src 相对 HTML 所在目录 —— 预览 HTML 必须
       住在项目根，否则得连配图目录一起复制一份。
    3. **缺图一律拦**，不吃正式流程"--until html 警告放行"那条例外：预览片的用途
       就是"看这一页定稿后怎么动"，画布页缺图连 HTML 都出不来，警告只会浪费一次
       往返。
    """
    global _REPORT_DIR, _REPORT_FILE
    sid = args.only
    manifest_path = os.path.join(out, "timing_manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"[run] --only 复用上次 TTS 的产物，但 {manifest_path} 不存在。\n"
              "请先不带 --only 跑一次管线（--until tts 就够）。", file=sys.stderr)
        sys.exit(2)
    full = _load_manifest_or_exit(manifest_path)
    images_path = os.path.join(project, "images.json")
    images = {}
    if os.path.isfile(images_path):
        try:
            images = load_images_json(images_path)
        except ValueError as e:
            raise SystemExit(f"[run] --only：images.json 读不出来（{e}）——"
                             "预览要看的就是这一页的配图与动画，请先修好该文件。")
    try:
        chain = chain_sids(full, images, sid)
        subset, (t0, t1) = build_subset(full, images, sid)
    except ValueError as e:
        print(f"[run] --only {sid}：{e}", file=sys.stderr)
        sys.exit(2)
    if len(chain) > 1:
        print(f"[run] {sid} 由 {' → '.join(chain[:-1])} 接续而来"
              f"（stage: \"keep\"），预览片从链首 {chain[0]} 一起渲。")
    if full.get("status") == "degraded":
        print("[run][warn] 本次预览用的是 status=degraded 的配音产物"
              "（正式渲染会拦住它）。", file=sys.stderr)

    # 音频切片。候选顺序直接借 gen_hyperframes 那份实现：绝对路径 / 项目根 /
    # cwd / manifest 目录，两边各写一套相对路径规则迟早漂移。
    src_audio = full.get("combined_audio") or os.path.join("audio", "combined.wav")
    cands = gen_hyperframes._audio_candidates(src_audio, project, out)
    audio_abs = next((p for p in cands if os.path.isfile(p)), None)
    if audio_abs is None:
        print("[run] --only 要切音频，但 " + src_audio + " 找不到（找过："
              + "、".join(os.path.abspath(p) for p in cands)
              + "）。请先重跑 TTS 步骤。", file=sys.stderr)
        sys.exit(2)
    need = round(t1 - t0, 3)
    preview_wav = os.path.join(project, f"preview_{sid}.wav")
    try:
        trim_audio(get_ffmpeg(), audio_abs, preview_wav, t0, need)
    except (RuntimeError, OSError) as e:
        print(f"[run] --only 切音频失败：{e}", file=sys.stderr)
        sys.exit(2)
    # total_duration 以实测为准（契约注释就写着"应为 ffmpeg 实测总时长"）；
    # 测量失败返回 0.0，退回按窗口算的 need，不让一个量不出来的时长卡住预览。
    got = measure_duration(get_ffmpeg(), preview_wav)
    subset["total_duration"] = round(max(got, need), 3)
    if got and got + 0.05 < need:
        print(f"[run][warn] 切出的音频实测 {got:.2f}s，比这一页的时间窗口 {need:.2f}s"
              f" 短（源音频末尾本就到不了 {t1:.2f}s）——预览片尾部会静音。",
              file=sys.stderr)
    subset["combined_audio"] = os.path.abspath(preview_wav)

    sub_manifest = os.path.join(project, f"preview_{sid}.manifest.json")
    sub_images = os.path.join(project, f"preview_{sid}.images.json")
    try:
        validate_timing_manifest(subset)
    except ValueError as e:
        print(f"[run] --only 切出的子集 manifest 没过自身契约：{e}\n"
              "这是重基逻辑的 bug，请带着这行报错反馈（正式流程不受影响）。",
              file=sys.stderr)
        sys.exit(2)
    write_json_atomic(sub_manifest, subset)
    write_json_atomic(sub_images, subset_images(images, chain))

    # 预览报告写进项目目录、独立文件名：--run_step 的失败落盘也走这条路，
    # 于是上一次正式跑的生产报告绝不会被一次预览覆盖掉。
    _REPORT_DIR = project
    _REPORT_FILE = f"preview_{sid}.report.json"
    _REPORT["params"] = {k: v for k, v in _REPORT["params"].items()
                         if k not in _TTS_ONLY_FLAGS}
    _REPORT["params"].update({"only": sid, "preview_window": [round(t0, 3),
                             round(t0 + need, 3)], "chain": chain})

    _tm, all_sids, missing_keys, missing_files = _image_coverage(sub_manifest,
                                                                 sub_images)
    _missing = sorted(set(missing_keys) | set(missing_files))
    _REPORT["images"] = {"total": len(all_sids),
                         "matched": len(all_sids) - len(_missing),
                         "missing": len(_missing)}
    if _missing:
        print("[run] 预览只服务定稿的页，以下段落缺配图："
              + "、".join(_missing)
              + f"\n请补图并写进 {images_path} 后重跑本命令。", file=sys.stderr)
        _write_report(_report_path())
        _print_report_summary()
        sys.exit(2)
    # 降级项照常记进预览报告（可见性），但不拦：预览片不是交付物。
    _deg = degraded_items(_tm)
    if _deg or _tm.get("status") == "degraded":
        _REPORT["degraded"].extend({"type": t, "count": c} for t, c, _w in _deg)

    html_out = os.path.join(project, f"preview_{sid}.html")
    html_args = ["-m", sub_manifest, "-o", html_out, "--theme", args.theme,
                 "--aspect", args.aspect, "--fps", str(args.fps),
                 "--images", sub_images]
    if args.alpha:
        html_args.append("--alpha")
    _run_step(gen_hyperframes.main, html_args, "生成预览 HTML")

    if args.until == "html":
        _REPORT["skipped"].append("render（--only --until html）")
        _write_report(_report_path())
        print(f"[run] 已生成预览 {html_out}。去掉 --until html 重跑本命令即可出片。",
              file=sys.stderr)
        _print_report_summary()
        return

    out_preview = os.path.join(project, f"preview_{sid}.{args.fmt}")
    hf_render = build_render_command(
        out_preview, args.quality, args.fps, args.workers,
        command=hyperframes_command(project),
        composition=os.path.basename(html_out),
        fmt=args.fmt)
    t0_render = time.time()
    try:
        render_wait(hf_render, out_preview, cwd=project,
                    max_wait=max(600.0, subset["total_duration"] * 10 + 600))
    except SystemExit:
        _REPORT["steps"].append({"name": "渲染预览", "seconds":
                                 round(time.time() - t0_render, 1), "ok": False})
        _write_report(_report_path())
        raise
    _REPORT["steps"].append({"name": "渲染预览", "seconds":
                             round(time.time() - t0_render, 1), "ok": True})
    print(f"\n[run] 预览完成：{out_preview}（段 {sid}，{need:.1f}s）\n"
          "正式产物未受影响：index.html / out.mp4 仍是上一次完整管线的结果。")
    _write_report(_report_path())
    _print_report_summary()


def _build_parser():
    parser = argparse.ArgumentParser(description="信源转视频一键编排（薄组合层）")
    parser.add_argument("--source", required=True, help="segments_source.json 路径")
    parser.add_argument("-o", "--output", default="audio_output",
                        help="音频/中间产物输出目录（默认 audio_output）")
    parser.add_argument("--project", default=None,
                        help="HTML/渲染项目目录（默认 <output 同级>/hf-project）")
    parser.add_argument("--until", choices=["tts", "images", "html", "render"],
                        default="render",
                        help="跑到该步骤为止（默认 render；迭代时用 html："
                             "生成 HTML 即停，先看版式和配图再渲染）")
    parser.add_argument("--no-resume", action="store_true", help="TTS 不用 --resume")
    parser.add_argument("--only", default=None, metavar="SEG_ID",
                        help="单段快渲：只把这一页（连同它 stage:\"keep\" 的接续前缀）渲成"
                             "预览片 preview_<SEG_ID>.mp4。复用上次 TTS 的 "
                             "timing_manifest.json 与配音，不重跑配音，也不碰 "
                             "index.html / out.mp4 / production_report.json。"
                             "改稿件、语速、音色或任何 TTS 参数时去掉本旗标跑完整管线")
    parser.add_argument("--theme", default=DEFAULT_THEME, choices=list_theme_names())
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏）或 "
                             "landscape（1920×1080 横屏）")
    parser.add_argument("--fps", type=int, default=24, choices=[12, 24, 30, 60],
                        help="输出帧率（默认 24；12 是快速看画面的低规格迭代档）。"
                             "实测档位与耗时见 references/rendering.md「性能参数」。"
                             "gen_hyperframes 自己还接受 1–240，这里只放行实测过的档位")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"],
                        help="渲染质量（默认 standard）")
    parser.add_argument("--format", dest="fmt", default="mp4",
                        choices=["mp4", "webm", "mov"],
                        help="成片容器（透传 hyperframes render --format，默认 mp4）。"
                             "要透明底只有 mov 认（ProRes 4444，实测逐帧带 alpha 平面）；"
                             "webm 本技能实测渲出来是黑底不透明，见 references/rendering.md"
                             "「透明底导出」。gif/hls/"
                             "png-sequence 本编排层没接，需要就直接调 npx hyperframes render")
    parser.add_argument("--alpha", action="store_true",
                        help="透明底导出：生成 HTML 时挂 ctv-alpha 类，页面渐变/网格/"
                             "段落氛围光三层不画，成片只剩内容层。"
                             "必须配 --format mov——mp4 没有 alpha 通道，webm 实测丢平面")
    parser.add_argument("--workers", type=int, default=4,
                        help="渲染抓帧 worker 数（默认 4，每 worker 一个独立 Chrome）。"
                             "有效区间与实测饱和点见 references/rendering.md「性能参数」；"
                             "低配机器遇 V8 堆崩溃或含视频配图时建议降到 2")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="允许 TTS 静音兜底等降级产物继续渲染；默认把 degraded artifact 作为交付阻断")
    parser.add_argument("--on-fail", default="abort", choices=["abort", "silence"],
                        help="单句 TTS 失败的处理方式，透传给 pipeline（默认 abort："
                             "任何句子失败即整条管线退出；silence 把失败句降级为"
                             "静音占位并让 manifest 进入 degraded 状态，需配合"
                             "--allow-degraded 才能继续渲染）")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                        help=f"语速倍率（默认 {DEFAULT_SPEED:g}，透传给 pipeline）")
    parser.add_argument("--voice-id", default=list_voice_ids()[0], choices=list_voice_ids(),
                        help="音色 ID（默认使用预置音色列表第一项，透传给 pipeline）")
    # 下面四个只在显式给出时才透传：不给就由 pipeline 用自己的默认值，run.py
    # 不复制第二份默认值（两处各写一份必然漂移）。--voice-style 参与句子音频
    # 指纹，改动会让受影响句子在 --resume 下重新合成。
    parser.add_argument("--voice-style", default=None,
                        help="自然语言风格描述（透传给 pipeline，控制语气情绪）；"
                             "默认沿用 pipeline 的播报风格文案")
    parser.add_argument("--gap", type=float, default=None,
                        help="句间静音秒数（透传给 pipeline，时间轴与段落擦除时长的"
                             "钳制上限都依赖它）；默认 0.4")
    parser.add_argument("--bgm", default=None,
                        help="背景音乐文件（透传给 pipeline，自动循环混音）")
    parser.add_argument("--bgm-volume", type=float, default=None,
                        help="BGM 相对人声音量（0.0-1.0），需配合 --bgm")
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 pipeline --dry-run（不写文件）")
    parser.add_argument("--loudness", type=float, default=None,
                        help="对最终音频做响度归一化（LUFS，透传给 pipeline.py "
                             "--loudness）。平台有响度要求时用（如 -16）；"
                             "默认不做归一化。开启后成片改用归一化音轨，"
                             "路径由 manifest 的 combined_audio 指路")
    return parser



def main():
    setup_stdio()
    global _REPORT_DIR, _REPORT, _REPORT_FILE, _DRY_RUN, _RUN_T0
    # 同一进程里重复调用 main()（import run 后直接调用的测试/嵌入式用法）
    # 时，模块级 _REPORT 不能累积上一次的 steps/skipped——每次都从空白
    # 报告开始。
    _REPORT = {"steps": [], "images": None, "skipped": [], "degraded": []}
    _DRY_RUN = False
    _REPORT_FILE = "production_report.json"
    _RUN_T0 = time.time()
    parser = _build_parser()
    args = parser.parse_args()
    _DRY_RUN = args.dry_run

    # ── 入参校验：这三条看起来像 pipeline 的重复，其实都承重 ────────────
    # --speed / --loudness 必定透传给 pipeline，两边文案也一致，但校验不能只留
    # 下游那份：_REPORT["params"] 里就有这两个值，而步骤失败时照样落
    # production_report.json——实测把 run.py 的校验删掉后 `--speed nan` 会写出
    # "speed": NaN，Python 读得回、jq / JSON.parse 读不回。fail-fast 在前，报告
    # 里就永远只会有合法数值。
    # --workers 更是只有这一份：它不进 tts_cmd（run.py 的是渲染抓帧 worker 数，
    # pipeline 的同名旗标是 TTS 并发数），交给下游拦就是
    # `npx hyperframes render --workers 0` 的莫名失败。
    try:
        validate_speed(args.speed)
    except ValueError as e:
        parser.error(str(e))
    if args.loudness is not None and not math.isfinite(args.loudness):
        parser.error(f"--loudness 必须是有限数值（LUFS，收到 {args.loudness}）")
    if args.workers < 1:
        parser.error("--workers 必须是正整数")
    # 只在显式要透明底时才当这件事：--alpha 单独出现意味着用户不知道成片会不会
    # 真的带 alpha，报一句比给他一片黑底便宜得多。
    # 为什么 webm 也算错的那一侧：实测（hyperframes 0.8.114 / Windows）透明页
    # 渲成 webm 后容器里根本没有 alpha 平面——ffprobe 逐帧报 pix_fmt=yuv420p，
    # 把透明处压成纯黑，只有那条 alpha_mode=1 元数据在说谎；同一份 HTML 渲 mov
    # 逐帧报 yuva444p12le、透明处 alpha=0。所以只认 mov（ProRes 4444）。
    # 哪天渲染器修好了 webm，放开这一行即可（判据就是上面那两条 ffprobe 命令）。
    if args.alpha and args.fmt != "mov":
        parser.error("--alpha 只有 --format mov 拿得到透明底（ProRes 4444，实测逐帧 "
                     "yuva444p12le、透明处 alpha=0）："
                     + ("mp4 容器不带 alpha 通道，透明处会渲成黑底"
                        if args.fmt == "mp4" else
                        "webm 在本机实测丢平面（容器只报 yuv420p，透明处压成纯黑，"
                        "alpha_mode=1 那行元数据不作数）")
                     + "。要透明底就改 --format mov；只要一个小体积的不透明容器，"
                       "去掉 --alpha 用 webm 没问题。")

    # ── --only 的入参冲突：这一档走的是独立分支（见 _run_preview），
    # 上面三条通用校验照样适用。
    if args.only:
        # --only 不重跑 TTS：任何配音参数都没有落点。静默忽略等于让用户以为
        # 预览片反映了他刚改的 --speed。判"有没有显式给出"用 argparse 默认值
        # 比对，显式传了与默认值相同的数字会漏判——但那次漏判不改变行为。
        _ignored = [f"--{k.replace('_', '-')}" for k in _TTS_ONLY_FLAGS
                    if getattr(args, k) != parser.get_default(k)]
        if _ignored:
            parser.error("--only 复用上次 TTS 的定稿产物、不重跑配音，"
                         + "、".join(_ignored)
                         + " 只能落在完整管线上：要按这些参数出片请去掉 --only")
        if args.dry_run:
            parser.error("--only 会切音频并生成预览 HTML，与 --dry-run 的"
                         "不写任何文件相互矛盾")
        if args.until in ("tts", "images"):
            parser.error(f"--only 配 --until {args.until} 没有意义："
                         "预览的边界只有 html 与 render")

    out = os.path.abspath(args.output)
    project = os.path.abspath(args.project) if args.project else \
        os.path.join(os.path.dirname(out) or ".", "hf-project")
    # 两画幅共用同一个项目目录（--aspect 只影响生成的 HTML）。
    # --dry-run 承诺不写任何文件，不需要拦产物路径
    if not args.dry_run:
        guard_not_in_skill_dir(("-o/--output", out), ("--project", project))
    _REPORT_DIR = out
    _REPORT["source"] = os.path.abspath(args.source)
    _REPORT["output"] = out
    _REPORT["project"] = project
    _REPORT["started_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _REPORT["params"] = {
        "theme": args.theme,
        "aspect": args.aspect,
        "canvas": "{}x{}".format(*get_canvas(args.aspect)),
        "fps": args.fps,
        "quality": args.quality,
        "format": args.fmt,
        "alpha": bool(args.alpha),
        "speed": args.speed,
        "voice_id": args.voice_id,
        "loudness": args.loudness,
        "on_fail": args.on_fail,
    }

    # ── --only：在第 3 步之前分叉出去，跑完直接收工 ─────────────────
    if args.only:
        _run_preview(args, out, project)
        return

    # ── 第 3 步：TTS ──────────────────────────────────────────────
    tts_args = ["--source", args.source, "-o", out]
    if not args.no_resume:
        tts_args.append("--resume")
    # --speed / --voice-id 都有 argparse 默认值，永远透传：显式写进子步骤参数，
    # 让 pipeline 的 resume 指纹与本次参数一致，不依赖两边默认值恰好相同。
    tts_args += ["--speed", str(args.speed), "--voice-id", args.voice_id]
    if args.voice_style:
        tts_args += ["--voice-style", args.voice_style]
    if args.gap is not None:
        tts_args += ["--gap", str(args.gap)]
    if args.bgm:
        tts_args += ["--bgm", args.bgm]
        if args.bgm_volume is not None:
            tts_args += ["--bgm-volume", str(args.bgm_volume)]
    elif args.bgm_volume is not None:
        parser.error("--bgm-volume 只在给了 --bgm 时才有意义")
    if args.loudness is not None:
        # 响度归一化在 pipeline 末端对拼接后的人声轨执行（可选混入 BGM 之后），
        # 归一化后的音频由 manifest 的 combined_audio 指路，下游 HTML/检查
        # 都从 manifest 取，不在这里硬编码文件名。
        tts_args += ["--loudness", str(args.loudness)]
    # 单句 TTS 失败的降级策略同样永远透传（同上一条规则）。silence 会把失败句
    # 降级为静音占位并让 manifest 进入 degraded 状态——那是 SKILL.md
    # 「降级显式化」规则与下方 degraded 拦截真正能触达的入口。
    tts_args += ["--on-fail", args.on_fail]
    if args.dry_run:
        tts_args.append("--dry-run")
    _run_step(pipeline.main, tts_args, "TTS")

    # ── 第 4 步：配图 ─────────────────────────────────────────────
    # 配图不由本脚本产出：agent 在第 4 步用 ImageGen 生图（方式 B）、
    # 手绘 SVG 矢量示意（方式 C）、或 VideoGen/手动放置视频素材（方式 D），
    # 落进 images/ 并把映射写进 images.json。run.py 只负责统计这份映射
    # 的覆盖率，并只在真要渲染时拦缺图（--until html 只警告）。
    manifest = os.path.join(out, "timing_manifest.json")
    images_json = os.path.join(project, "images.json")
    has_images = os.path.isfile(images_json)

    if args.dry_run or args.until == "tts":
        if args.until == "tts":
            _REPORT["skipped"].append("html/render（--until tts）")
        # dry-run 承诺"不写任何文件"：_write_report 内部短路不落盘，只打
        # 印摘要（非 dry-run 的 --until tts 仍是写报告 + 打印）。
        _write_report(_report_path())
        _print_report_summary()
        return

    # 覆盖率统计：images.json 存在就按其映射算缺图；不存在时 _image_coverage
    # 会把全部内容段落都报成缺图（不能因为文件不存在就跳过统计——那样
    # missing 恒空、缺图拦截形同虚设，会静默渲染出无图成片）。
    # dry-run / --until tts 已在上方提前 return，走到这里必然要统计。
    _tm, all_sids, missing_keys, missing_files = _image_coverage(manifest, images_json)
    _missing_union = sorted(set(missing_keys) | set(missing_files))
    _REPORT["images"] = {
        "total": len(all_sids), "matched": len(all_sids) - len(_missing_union),
        "missing": len(_missing_union)}

    # --until images：配图阶段到此为止即正常结束（exit 0）。必须放在缺图拦截
    # 之前——调试意图是"看看覆盖率如何"，被 exit 2 劫持就和"要渲染到底但缺图"
    # 混成了同一种失败。
    if args.until == "images":
        _REPORT["skipped"].append("html/render（--until images）")
        _write_report(_report_path())
        _print_report_summary()
        return

    images_dir = os.path.join(project, "images")
    if missing_files:
        # images.json 有键但媒体文件不在盘上（图被删过/路径写错）：
        # gen_hyperframes 对这类坏路径无条件 fail-fast，"只警告继续"在这里
        # 兑现不了——与其先打一句"继续生成 HTML"再让 HTML 步骤 exit 1，
        # 不如就地报清病因，并把还没映射的段一并列出来让人一次修完。
        print("[run] images.json 引用了不存在的媒体文件："
              + ", ".join(missing_files) + "。\n"
              f"请补齐 {images_dir} 下的文件，或修正/删除对应映射条目。",
              file=sys.stderr)
        if missing_keys:
            print("[run] 另外这些段落还没有配图映射：" + ", ".join(missing_keys),
                  file=sys.stderr)
        _write_report(_report_path())
        _print_report_summary()
        sys.exit(2)

    if missing_keys:
        # 只有真要出片才拦：--until html 是"图没画完先看版式"的迭代路径，
        # 同一份缺图清单在那里只降级成警告。missing 的口径见 _image_coverage。
        #
        # 唯一例外是整页画布：那一页没有标题层也没有句子流层，配图就是整个
        # 画面，gen_hyperframes 在生成期必然 exit 1（canvas_layout_errors）。
        # 对它承诺"继续生成 HTML 供预览"是兑现不了的承诺——打印完这句紧接着
        # 就是一条 [error]，两条信息自相矛盾。所以画布段缺图不分 --until，
        # 与 missing_files 同样就地拦下并点名病因。
        _layout_by_sid = {s["id"]: seg_layout(s) for s in _tm["segments"]}
        _canvas_missing = [s for s in missing_keys
                           if _layout_by_sid.get(s) == "canvas"]
        print("[run] 以下段落还没有定稿配图："
              + ", ".join(missing_keys) + "。\n"
              "请按第 4 步补图：ImageGen 生图（方式 B）、手绘 SVG 矢量示意图"
              "（方式 C）、或 VideoGen 生成/手动放置视频素材（方式 D）；需要真实照片时由你"
              "自己联网检索并下载到 "
              f"{images_dir}。\n"
              f"然后在 {images_json} 写好各段映射后重跑本命令。",
              file=sys.stderr)
        if _canvas_missing:
            print("[run] 其中 " + ", ".join(_canvas_missing)
                  + " 是整页画布（layout: \"canvas\"）：该页不生成标题层与句子流层，"
                    "配图就是它唯一的画面，缺图时连预览 HTML 都生成不出来"
                    "（不是「渲染前补齐即可」那一档），请先按当前画幅补图。",
                  file=sys.stderr)
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)
        if args.until != "render":
            print("[run] 继续生成 HTML 供预览（渲染前必须补齐上面这些段）。",
                  file=sys.stderr)
        else:
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)

    # TTS 的 silence fallback 不是“渲染成功”就能掩盖的降级状态。默认阻断交付；
    # 显式 --allow-degraded 才允许继续，且 production_report 会保留可机器读取的
    # degraded 标记。manifest 复用 _image_coverage 那次已校验的加载——同一次 run
    # 里文件不会被别人改写，再 load+全量校验一遍只是把坏文件报错推晚一步。
    _deg_items = degraded_items(_tm)
    # status 是 pipeline 写下的总旗标：明细一条都没读出来（键被写坏或整个漏写）
    # 时照样拦，"以 manifest 的 status 为准"这条兜底不依赖下面的逐项计数。
    if _deg_items or _tm.get("status") == "degraded":
        _REPORT["degraded"].extend({"type": t, "count": c} for t, c, _w in _deg_items)
        _why = "、".join(w for _t, _c, w in _deg_items) or "manifest 标记为 degraded"
        # --until html 是迭代预览边界：允许先看版式，但不能把这个成功
        # 状态误解成可交付成片。真正 render 仍要求显式 --allow-degraded。
        if not args.allow_degraded and args.until == "render":
            _write_report(_report_path())
            print(f"[run] 检测到 {_why}，"
                  "为避免把降级产物误当成正常成片，默认停止。"
                  "需要有意发布时再加 --allow-degraded。", file=sys.stderr)
            sys.exit(3)
        if args.allow_degraded:
            print(f"[warn] 已显式允许降级：{_why}，"
                  "生产报告将标记 degraded。", file=sys.stderr)
        else:
            print(f"[warn] HTML 预览继续生成，但 {_why}；"
                  "最终渲染必须显式加 --allow-degraded。", file=sys.stderr)

    # ── 第 5 步 a：生成 composition HTML ──────────────────────────
    html_args = ["-m", manifest, "-o", os.path.join(project, "index.html"),
                 "--theme", args.theme,
                 "--aspect", args.aspect,
                 "--fps", str(args.fps)]
    if has_images:
        html_args += ["--images", images_json]
    if args.alpha:
        html_args.append("--alpha")
    _run_step(gen_hyperframes.main, html_args, "生成 HTML")

    # 不设自动版式检查：一条 Chrome 度量链实测一次 25-50s，换不来人工预览 3s
    # 就能给出的信息（--until html 就是给人工预览收口的）。

    # HTML 已生成；渲染复用同一次 Hyperframes 命令解析。
    _HF_COMMAND = hyperframes_command(project)

    if args.until == "html":
        _REPORT["skipped"].append("render（--until html）")
        _write_report(_report_path())
        print(f"[run] 已生成 {os.path.join(project, 'index.html')}。"
              "先在浏览器里打开它确认版式和配图，定稿后去掉 --until 直接渲染。",
              file=sys.stderr)
        _print_report_summary()
        return

    # ── 第 5 步 b：渲染 ───────────────────────────────────────────
    out_video = os.path.join(project, f"out.{args.fmt}")
    hf_render = build_render_command(
        out_video, args.quality, args.fps, args.workers, command=_HF_COMMAND,
        fmt=args.fmt,
    )
    # 渲染等待上限跟着成片时长走。实测渲染耗时约为视频时长的 1.2–3 倍
    # （162s 片 190–466s，后者是抓帧超时重抓的极端），10 倍 + 10 分钟
    # 打底给出充足余量；默认 1800s 硬上限会把长片或 60fps/high quality
    # 的正常渲染误杀成"超时"。
    _render_cap = max(1800.0, float(_tm["total_duration"]) * 10 + 600)
    t0_render = time.time()
    try:
        render_wait(hf_render, out_video, cwd=project, max_wait=_render_cap)
    except SystemExit:
        _REPORT["steps"].append({"name": "渲染", "seconds": round(time.time() - t0_render, 1), "ok": False})
        _write_report(_report_path())
        raise
    _REPORT["steps"].append({"name": "渲染", "seconds": round(time.time() - t0_render, 1), "ok": True})
    # 走到这里成片必已存在且非空：_render_poll_loop 的所有 return 路径都以
    # "实测 size>0"为前提（自然退出量一次，强杀路径再过 _verify_killed_render
    # 的整容器解码），失败路径一律 SystemExit——不再补一遍 isfile/getsize。

    print(f"\n[run] 完成。成片：{out_video}", flush=True)
    _write_report(_report_path())
    _print_report_summary()


if __name__ == "__main__":
    main()
