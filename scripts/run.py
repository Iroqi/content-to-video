#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把 TTS → 配图 → HTML → 渲染串成一条命令，内部按依赖顺序调用各步骤；
不改变任何子脚本的独立性，只做组合与默认值。

TTS / 生成 HTML 两步是**进程内直调**子脚本的 main(argv)（参数校验只在
子脚本自己那一份 parser 里做一次，错误以 SystemExit 传回、语义与旧的
子进程退出码一致）；全文件唯一的子进程是 `npx hyperframes render`，
由 _render_backend 负责进程树与成片完整性。

  python scripts/run.py --source segments_source.json -o audio_output --until html
  # ↑ 迭代用：跑到 HTML 为止，在 hf-project/ 打开 index.html 看版式和配图。
  python scripts/run.py --source segments_source.json -o audio_output
  # ↑ 定稿用：一路渲染到 hf-project/out.mp4。
"""
import argparse
import datetime
import os
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, SCRIPTS_DIR)
from _template import get_canvas  # noqa: E402  画幅 → 画布尺寸（生产报告 params.canvas 用）
from _timeline import DEFAULT_SPEED, validate_speed  # noqa: E402
from _voices import list_voice_ids  # noqa: E402
from _segments import sids_needing_image, seg_layout  # noqa: E402
from _manifest_schema import load_timing_manifest  # noqa: E402
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
# 制作报告的文件名（写进 out 目录，跟 timing_manifest.json 放一起）。
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
    带路径的退出——正式流程与生成 HTML 共用这一份口径。
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
    # 走 _load_manifest_or_exit：正式流程对坏 manifest 的报错同源。
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


# 只作用于 TTS 步骤的旗标集中定义在这里；render 侧参数（--fps/--quality/--workers）
# 走 hyperframes 字面透传，不复制第二份默认值。
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
    parser.add_argument("--workers", type=int, default=4,
                        help="渲染抓帧 worker 数（默认 4，每 worker 一个独立 Chrome）。"
                             "有效区间与实测饱和点见 references/rendering.md「性能参数」；"
                             "低配机器遇 V8 堆崩溃或配图体积大时建议降到 2")
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
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 pipeline --dry-run（不写文件）")
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

    # ── 入参校验：这两条看起来像 pipeline 的重复，其实都承重 ────────────
    # --speed 必定透传给 pipeline，两边文案也一致，但校验不能只留下游那份：
    # _REPORT["params"] 里就有这个值，而步骤失败时照样落 production_report.json
    # ——实测把 run.py 的校验删掉后 `--speed nan` 会写出 "speed": NaN，
    # Python 读得回、jq / JSON.parse 读不回。fail-fast 在前，报告里就永远只
    # 会有合法数值。
    # --workers 更是只有这一份：它不进 tts_cmd（run.py 的是渲染抓帧 worker 数，
    # pipeline 的同名旗标是 TTS 并发数），交给下游拦就是
    # `npx hyperframes render --workers 0` 的莫名失败。
    try:
        validate_speed(args.speed)
    except ValueError as e:
        parser.error(str(e))
    if args.workers < 1:
        parser.error("--workers 必须是正整数")

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
        "aspect": args.aspect,
        "canvas": "{}x{}".format(*get_canvas(args.aspect)),
        "fps": args.fps,
        "quality": args.quality,
        "speed": args.speed,
        "voice_id": args.voice_id,
        "on_fail": args.on_fail,
    }

    # ── 第 3 步：TTS ──────────────────────────────────────────────
    # --resume 恒开（指纹缓存能证明一致才复用；要整重跑时直接调 pipeline.py，
    # 不加 --resume 即可）。
    tts_args = ["--source", args.source, "-o", out, "--resume"]
    # --speed / --voice-id 都有 argparse 默认值，永远透传：显式写进子步骤参数，
    # 让 pipeline 的 resume 指纹与本次参数一致，不依赖两边默认值恰好相同。
    tts_args += ["--speed", str(args.speed), "--voice-id", args.voice_id]
    if args.voice_style:
        tts_args += ["--voice-style", args.voice_style]
    if args.gap is not None:
        tts_args += ["--gap", str(args.gap)]
    # 单句 TTS 失败的降级策略同样永远透传（同上一条规则）。silence 会把失败句
    # 降级为静音占位并让 manifest 进入 degraded 状态——那是 SKILL.md
    # 「降级显式化」规则与下方 degraded 拦截真正能触达的入口。
    tts_args += ["--on-fail", args.on_fail]
    if args.dry_run:
        tts_args.append("--dry-run")
    _run_step(pipeline.main, tts_args, "TTS")

    # ── 第 4 步：配图 ─────────────────────────────────────────────
    # 配图不由本脚本产出：agent 在第 4 步用 ImageGen 生图（方式 B）、
    # 手绘 SVG 矢量示意（方式 C），落进 images/ 并把映射写进 images.json。
    # run.py 只负责统计这份映射的覆盖率，并只在真要渲染时拦缺图
    # （--until html 只警告）。
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
              "（方式 C）；需要真实照片时由你"
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
                 "--aspect", args.aspect,
                 "--fps", str(args.fps)]
    if has_images:
        html_args += ["--images", images_json]
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
    out_video = os.path.join(project, "out.mp4")
    hf_render = build_render_command(
        out_video, args.quality, args.fps, args.workers, command=_HF_COMMAND,
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
