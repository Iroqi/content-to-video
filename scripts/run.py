#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把 TTS → 配图 → HTML → 渲染串成一条命令，内部按依赖顺序调用各步骤；
不改变任何子脚本的独立性，只做组合与默认值。

TTS / 生成 HTML 两步**进程内直调**子脚本的 main(argv)（参数校验只在子脚本
那份 parser 做一次，错误以 SystemExit 传回）；唯一的子进程是
`npx hyperframes render`，进程树与成片完整性归 _render_backend。

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
from _template import get_canvas, overrides  # noqa: E402  画幅/生效覆写（生产报告用）
from _timeline import (DEFAULT_FPS, DEFAULT_GAP, DEFAULT_SPEED,  # noqa: E402
                       duration_tolerance, validate_speed)
from _voices import list_voice_ids  # noqa: E402
from _segments import (FULL_BLEED_LAYOUTS, sids_needing_image,  # noqa: E402
                       seg_layout)
from _manifest_schema import load_timing_manifest, MANIFEST_FILENAME  # noqa: E402
from _images_schema import load_images_json  # noqa: E402
from _degraded import items as degraded_items  # noqa: E402  降级注册表（词汇/人话同源）
from _render_backend import (hyperframes_command,  # noqa: E402
                             build_render_command, run_check,
                             DEFAULT_MAX_WAIT, render_wait,
                             probe_video_duration, node_version_gate,
                             sample_snapshot_times, snapshot_frames)
from _script_utils import (setup_stdio, write_json_atomic,  # noqa: E402  重定向 UTF-8 + 报告原子写
                           guard_not_in_skill_dir,  # noqa: E402  产物落技能目录的守卫（与 pipeline/gen 共用同一实现）
                           media_verdict)  # noqa: E402  素材可用性判据（与 gen 同一份函数）
import perceptual  # noqa: E402  观感门禁（像素判据，见 --perceptual）
import _beats as beats  # noqa: E402  台词板核对（句序是否落在最终时间轴上）
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
    """本次 run 的真实墙钟时长。

    起止都用 `time.monotonic()`：量的是间隔，墙钟被 NTP 校正或休眠唤醒改动时
    会让"合计耗时"变成负数或凭空多出几十分钟——这份数字是排查"这次跑得正常
    不正常"的依据，不能自己就不可信。
    """
    return round(time.monotonic() - _RUN_T0, 1)


def _write_report(path):
    if _DRY_RUN:
        return
    # 降级明细在这里补齐，而不是在各 return 点各补一次：--until html /
    # --until images / --until tts 都提前返回，漏掉任何一处就会出现"报告说
    # 没事、manifest 说整片无配音"的自相矛盾（unvoiced_preview 正是这么漏的）。
    _backfill_degraded()
    try:
        _REPORT["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _REPORT["total_seconds"] = _total_seconds()
        # 原子写走 _script_utils.write_json_atomic：
        # production_report 被 Ctrl-C 打断在写一半时落截断 JSON，下次排查
        # "这次跑得正不正常"反而先崩在 json.load。
        write_json_atomic(path, _REPORT)
    except Exception as e:
        print(f"[run] 写制作报告失败（不影响主流程产出）：{e}", file=sys.stderr)


def _backfill_degraded():
    """manifest 已存在而报告还没记降级时，从 manifest 补一份。

    无副作用、幂等：manifest 不在（还没到 TTS 那一步）或已经记过就什么都不做。
    """
    if _REPORT.get("degraded"):
        return
    manifest = os.path.join(_REPORT_DIR or "", MANIFEST_FILENAME)
    if not os.path.isfile(manifest):
        return
    try:
        _tm = load_timing_manifest(manifest)
    except ValueError:
        return
    _REPORT["degraded"] = [{"type": t, "count": n, "detail": d}
                           for t, n, d in degraded_items(_tm)]


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
    if _REPORT.get("video"):
        v = _REPORT["video"]
        _got = "未测量" if v["seconds"] is None else f"{v['seconds']:.2f}s"
        print(f"  成片：{_got} / 时间轴 {v['expected_seconds']:.2f}s"
              f"（时长校验 {v['check']}）")
    if _REPORT["skipped"]:
        print("  跳过的步骤：" + "；".join(_REPORT["skipped"]))
    print("=" * 44)


def _run_step(fn, argv, step_name):
    """进程内直调子脚本入口 fn(argv)，退出语义与旧子进程调用一致。

    子脚本自己 parse_args（参数校验只那一份）；失败以 SystemExit 传回：非 0 记失败
    步骤、落制作报告、以同码退出。`sys.exit("消息")` 的字符串码在子进程里由解释器
    代打并归一为 1，进程内没人代打，这里补打。

    **ValueError / OSError 也必须接住**：入口只保证"错就 sys.exit"，但读 JSON、写盘、
    拉起子进程抛的是这两种——漏接时一次"路径写错"会变成裸栈退出且**不落报告**，
    而报告是"跑到哪一步、哪一步挂的"唯一线索。KeyboardInterrupt 不接（主动中断不算
    步骤失败）。
    """
    print(f"\n>>> {step_name} [in-process]: {' '.join(argv)}", flush=True)
    t0 = time.monotonic()
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
    # 只接 ValueError/OSError 时 TypeError / KeyError / RuntimeError 会裸栈穿透，
    # 而且**不落 production_report**——正是这段 docstring 要防的形态（"跑到哪一步"
    # 没留痕，重试只能从头）。KeyboardInterrupt / SystemExit 是 BaseException，捕不到。
    except Exception as e:
        code = 1
        print(f"[run] {step_name} 异常终止（{type(e).__name__}）：{e}",
              file=sys.stderr, flush=True)
    if code != 0:
        _REPORT["steps"].append(
            {"name": step_name, "seconds": round(time.monotonic() - t0, 1), "ok": False})
        _write_report(_report_path())
        print(f"[run] 步骤失败（退出码 {code}）：{step_name} {' '.join(argv)}",
              file=sys.stderr)
        sys.exit(code)
    _REPORT["steps"].append(
        {"name": step_name, "seconds": round(time.monotonic() - t0, 1), "ok": True})


def _report_path():
    """制作报告写到哪：out 目录（跟 timing_manifest.json 放一起），
    main() 在首个写报告点之前无条件赋 _REPORT_DIR。"""
    return os.path.join(_REPORT_DIR, _REPORT_FILE)


def _official_gate(project, command):
    """跑官方门禁 `hyperframes check`，返回该当真的 findings（[] = 通过）。

    渲染一次是分钟级，check 是十秒级且本来就要人手动跑一遍；把"跑"这件事收进
    run.py，定稿路径就不再依赖"记得去跑"。判读口径（哪些 warning 是噪声）在
    `_render_backend.check_verdict`，这里只管报人话。
    """
    try:
        findings, seconds = run_check(project, command=command)
    except RuntimeError as e:
        raise SystemExit(f"[run] {e}")
    blocked = any(sev == "error" for sev, _, _ in findings)
    _REPORT["steps"].append({"name": "官方门禁 check", "ok": not blocked,
                             "seconds": round(seconds, 1)})
    if findings:
        _REPORT["gate"] = [{"severity": s_, "rule": k, "message": m}
                           for s_, k, m in findings]
    return findings


def _run_perceptual(project, tm, aspect, command):
    """跑观感门禁，把判词写进制作报告并打到 stdout（只报不拦）。

    `--until html` / `check` / 定稿渲染三档共用这一份。三档都调它，是因为
    "在 html 与 render 分支各贴一段、漏了 check" 正是本轮要修的那种失效：
    漏贴不报错，只是那一档的判词凭空消失、报告里连一条 skipped 都没有。
    三档的帧都取自同一份已生成的 HTML，量的是编排本身。
    """
    _r = perceptual.run_gate(project, tm, os.path.join(project, "perceptual"),
                             aspect=aspect, command=command)
    _REPORT["perceptual"] = {k: v for k, v in _r.items() if k != "segs"}
    if _r.get("error"):
        _REPORT["skipped"].append(f"观感门禁（取帧失败：{_r['error'][:120]}）")
        print(f"[run][warn] 观感门禁未完成：{_r['error']}", file=sys.stderr)
        return
    _issues = _r["issues"]
    print(f"[run] 观感门禁：{_r['frames']} 帧，{len(_issues)} 条判词，"
          f"拼图 {_r['contact_sheet']}")
    for _it in _issues:
        print(f"[run][warn] {_it['kind']} @ {_it['seg']}: {_it['msg']}",
              file=sys.stderr)
    if not _issues:
        print("[run] 空页 / 死帧 / 换页时序三项都过——但判词只说明"
              "『没有明显坏』，好不好看仍要翻那张拼图。")


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
    """返回 (tm, all_sids, missing_keys, missing_files)。

    `tm` 是已校验的 manifest（主流程的降级检测直接复用，不在一次 run 里 load 两遍）；
    `all_sids` 是需要配图的段落全集（`_segments.needs_image`：内容段一律需要；
    opening/closing 默认是纯文字 agenda 卡不配图，换成 `*_layout:"canvas"` 后计入）。

    **两类缺图必须分开**：`missing_keys` 是"图还没画完"，`--until html` 警告放行；
    `missing_files` 是"映射指向坏路径"，`gen_hyperframes` 哪一步都会 fail-fast——
    混进"只警告"等于承诺一个兑现不了的继续。
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

    # 判定走 _script_utils.media_verdict——与 gen_hyperframes.validate_images_files
    # 同一个函数（不是"同一句注释"）：src 与 poster 都要查，只查 src 时覆盖率
    # 报告记"齐了"、下一步 gen 才以封面缺失 exit 1。
    def _unusable(src):
        return bool(media_verdict(proj_dir, src))

    def _entry_unusable(entry):
        # 并置对比（src 是一对图）：两项都要可用。只判第一项的话，右格图还没画
        # 完时覆盖率会报"齐全"，而 gen 下一步会以"第 1 格配图不存在" fail-fast
        # ——报告说齐了、出片失败，正是这个函数要消灭的那类分歧。
        src = entry.get("src")
        if isinstance(src, list):
            return any(_unusable(s) for s in src)
        return any(_unusable(entry[k])
                   for k in ("src", "poster") if entry.get(k))

    missing_files = [s for s in sids if s in mapping and _entry_unusable(mapping[s])]
    return manifest, sids, missing_keys, missing_files


# 默认 help 里不出现的参数：调参、低配兜底、以及"关掉某道闸"的逃生口。
#
# 判据是"第一次用要不要知道它"——不是"重不重要"。--workers 与 --allow-degraded
# 都很重要，但前者只在机器拖不动时才有意义，后者是明知有降级还要出片的例外路径；
# 把它们摆在 --source/--until 旁边，等于让每个新读者先读完 25 个旗标才能开始。
# 隐藏≠删除：仍然能解析、仍然进报告，只是要 --help-all 才列出来。
_HIDDEN_OPTIONS = frozenset({
    "--project", "--workers", "--low-memory-mode", "--no-low-memory-mode",
    "--allow-degraded", "--allow-best-effort", "--placeholder-missing",
    "--on-fail", "--allow-ease", "--allow-infinite-loop",
    "--voice-style", "--gap", "--check-frames", "--slot-no-image",
})


# 只作用于 TTS 步骤的旗标集中定义在这里；render 侧参数（--fps/--quality/--workers）
# 走 hyperframes 字面透传，不复制第二份默认值。
def _build_parser(show_all=False):
    parser = argparse.ArgumentParser(
        prog="run.py",
        usage="run.py --source <segments_source.json> [选项]",
        description="信源转视频一键编排（薄组合层）。默认只列常用参数，"
                    "调参与逃生口用 --help-all。",
        epilog="TTS 凭据不从本 CLI 传入，由 pipeline 从环境/用户级 .env 读取："
               "MIMO_API_KEY（必需）、MIMO_BASE_URL、MIMO_TTS_MODEL。"
               "完整取值与优先级见 pipeline.py --help 与 references/tts_pipeline.md。\n"
               "全部 25 个参数（渲染调参 / 低配兜底 / 四个 --allow-* 逃生口）"
               "见：run.py --help-all")
    parser.add_argument("--source", required=True, help="segments_source.json 路径")
    parser.add_argument("-o", "--output", default="audio_output",
                        help="音频/中间产物输出目录（默认 audio_output）")
    parser.add_argument("--project", default=None,
                        help="HTML/渲染项目目录（默认 <output 同级>/hf-project）")
    parser.add_argument("--until", choices=["tts", "images", "html", "check", "render"],
                        default="render",
                        help="跑到该步骤为止（默认 render；迭代时用 html："
                             "生成 HTML 即停，先看版式和配图再渲染；check：出 HTML "
                             "后跑官方门禁 hyperframes check，有 error 即停，不进渲染）")
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏）或 "
                             "landscape（1920×1080 横屏）")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS,
                        choices=[12, 24, 30, 60],
                        help="输出帧率（默认 24；12 是快速看画面的低规格迭代档）。"
                             "固定开销约 8.5s/次，冷跑再多 6s。"
                             "gen_hyperframes 自己还接受 1–240，这里只放行实测过的档位")
    parser.add_argument("--quality", default="looks",
                        choices=["draft", "looks", "standard", "high", "delivery"],
                        help="渲染质量（默认 looks）。五档是 Hyperframes CLI 自己的取值，"
                             "字面透传：looks = standard 编码 + CRF 16（CLI 的默认档，"
                             "定稿用这一档）；draft 约省 15%%，只够预览；delivery = high，"
                             "交付档。standard / high 是不带 CRF 覆写的裸档——同样写着"
                             "standard，looks 比它多一层 CRF 16，成片更清楚也更大，"
                             "所以默认选官方那一档而不是裸档")
    parser.add_argument("--workers", type=int, default=4,
                        help="渲染抓帧 worker 数（默认 4，每 worker 一个独立 Chrome）。"
                             "8 之前有效、之后饱和；低配机器遇 V8 堆崩溃或配图"
                             "体积大时降到 2")
    # 低内存档是官方 CLI 的旗标（--help：钉 1 worker + screenshot capture +
    # 跳过自动 worker 校准，默认按总内存 ≤8GB 自判）。低配机器非它不可——实测
    # 3.9GB/2 核机上不加就落在抓帧超时重抓。三态：不给 = 交给 CLI 自判，
    # 显式 --no-low-memory-mode 才能关掉它的自判。
    _lm = parser.add_mutually_exclusive_group()
    _lm.add_argument("--low-memory-mode", dest="low_memory", action="store_true",
                     default=None,
                     help="强制开 Hyperframes 低内存渲染档（worker 钉 1、screenshot "
                          "capture、跳过 worker 自动校准）；低配机器（≤8GB、/dev/shm "
                          "偏小）用它。会覆盖 --workers")
    _lm.add_argument("--no-low-memory-mode", dest="low_memory", action="store_false",
                     help="强制关低内存档（CLI 默认按总内存自判，≤8GB 会自动开）")
    parser.add_argument("--allow-degraded", action="store_true",
                        help="允许 TTS 静音兜底等降级产物继续渲染；默认把 degraded artifact 作为交付阻断")
    parser.add_argument("--allow-best-effort", action="store_true",
                        help="调试档：把渲染器的 --best-effort 打开（CLI 默认就是开）。"
                             "媒体缺失或抓帧未就绪也照出片，成片可能无声或缺帧。"
                             "默认关——本技能红线是不出无声成片；只在排查"
                             "\"某段媒体到底哪儿不对\"时用")
    parser.add_argument("--placeholder-missing", action="store_true",
                        help="预览档（只配 --until html）：给缺图的整页画布段写占位 SVG，"
                             "让 HTML 先出来看结构。默认不带——缺图的整页画布段照旧拦下"
                             "（没图就没画面）。占位图在预览之后必须换成真图，"
                             "带 --placeholder-missing 跑渲染直接报错")
    parser.add_argument("--on-fail", default="abort", choices=["abort", "silence"],
                        help="单句 TTS 失败的处理方式，透传给 pipeline（默认 abort："
                             "任何句子失败即整条管线退出；silence 把失败句降级为"
                             "静音占位并让 manifest 进入 degraded 状态，需配合"
                             "--allow-degraded 才能继续渲染）")
    parser.add_argument("--set", action="append", default=[], metavar="键=值",
                        help="临时改一个模板视觉参数（可重复），透传给 gen_hyperframes，"
                             "如 --set layout.vertical.title.fontSize=100。只在本次进程"
                             "生效、不落盘；定稿的取值请写回 scripts/_template.py")
    parser.add_argument("--allow-ease", action="store_true",
                        help="预览档（只配 --until html）：放行 GSAP core 之外的 ease 档名。"
                             "带它跑渲染会在入口报错——那些档在渲染里会静默退回缺省缓动")
    parser.add_argument("--allow-infinite-loop", action="store_true",
                        help="预览档（只配 --until html）：放行 images.json 的 repeat:-1。"
                             "无限循环把总时长折算成 1e10 量级，按时刻取帧/进度条/跨段接续"
                             "一起失效；带它跑渲染会在入口报错")
    parser.add_argument("--draft", action="store_true",
                        help="草稿档总开关：一次放行全部迭代逃生口（缺图/坏 src 降级并占位、"
                             "槽位比例与画布字号降 warn、ease 放行、渲染 best-effort）。"
                             "探索期随便折腾用；定稿交付请去掉（判据照旧全严，"
                             "production_report 会标记 draft 留痕）")
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
                        help=f"句间静音秒数（透传给 pipeline，时间轴与段落擦除时长的"
                             f"钳制上限都依赖它）；默认 {DEFAULT_GAP:g}")
    parser.add_argument("--dry-run", action="store_true",
                        help="只预检 TTS 链路（分句/段检测/稿件契约），不合成、"
                             "不写任何文件；--until 在 dry-run 下不生效（HTML/"
                             "渲染步骤整体跳过）")
    parser.add_argument("--slot-no-image", action="store_true",
                        help="降级档（没有生图能力时用）：槽位版式的段缺图不再空着"
                             "一个图槽，改为句子流铺满内容区，照旧能出成片。"
                             "只影响槽位版式——满幅页（canvas/plate）缺图没有图就没有"
                             "画面，照旧拦下。画面会明显素一些，报告点名放行的段")
    parser.add_argument("--perceptual", action="store_true",
                        help="跑观感门禁：按段取帧，量「图槽是不是空的 / 画面有没有"
                             "长时间不动 / 换页与旁白对不对得上」，并把 contact sheet "
                             "与判词写进 <project>/perceptual/。这是全仓唯一一道在看"
                             "画面的检查（其余门禁都看结构）。需要 Node + Chrome + "
                             "Pillow；只报不拦，判词进 production_report 的 "
                             "perceptual 字段")
    parser.add_argument("--check-frames", action="store_true",
                        help="用官方 `hyperframes snapshot` 在成片上取几帧（含首末帧，"
                             "拼一张 contact sheet）落进 hf-project/snapshots/，交付前"
                             "就能翻看成片长什么样。默认关闭；取帧失败只 warn 不阻断")
    if not show_all:
        # help=SUPPRESS 同时把它们从 usage 与列表里摘掉；解析行为一个字没变
        for _action in parser._actions:
            if _HIDDEN_OPTIONS & set(_action.option_strings):
                _action.help = argparse.SUPPRESS
    return parser



def main():
    setup_stdio()
    # --help-all 要在 argparse 之前拦：它和 --source 的 required 检查无关，
    # 也不该为了看文档被要求先给一份稿子。
    if "--help-all" in sys.argv[1:]:
        _build_parser(show_all=True).print_help()
        return
    global _REPORT_DIR, _REPORT, _DRY_RUN, _RUN_T0
    # 同一进程里重复调用 main()（import run 后直接调用的测试/嵌入式用法）
    # 时，模块级 _REPORT 不能累积上一次的 steps/skipped——每次都从空白
    # 报告开始。
    _REPORT = {"steps": [], "images": None, "skipped": [], "degraded": []}
    _DRY_RUN = False
    _RUN_T0 = time.monotonic()
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
    # 占位档只对预览有意义：带它渲染等于把占位图烧进成片，那是交付事故而不是
    # 迭代便利。与其在渲染跑到一半才发现，不如在入口说清。
    # --draft 例外：草稿档的成片本来就是"粗剪看节奏"，占位页/空位是草稿的
    # 组成部分，报告会留痕；定稿路径（不带 --draft）照旧拦。
    if args.placeholder_missing and args.until != "html" and not args.draft:
        parser.error("--placeholder-missing 只配 --until html（预览档）："
                     "占位图不能进成片。定稿时去掉这个旗标，按画幅补齐整页画布的配图")
    # 同一条理由：core 之外的 ease 在真正渲染时会静默退回缺省缓动，成片与预览
    # 看到的是两条不同的曲线。预览里挑档可以，交付前必须换回目录里的。
    if args.allow_ease and args.until != "html" and not args.draft:
        parser.error("--allow-ease 只配 --until html（预览档）：那些档名只影响预览，"
                     "渲染时 GSAP 会静默换成缺省缓动——成片和你看到的不一样。"
                     "定稿时去掉这个旗标，把 ease 换回 --help 里列出的档")
    # 无限循环同理，且更硬：它不只换一条曲线，它把整条时间线的总时长换成 1e10，
    # 于是"按时刻取帧核对"这件事本身失效——预览能看，成片却无从核对。
    if args.allow_infinite_loop and args.until != "html" and not args.draft:
        parser.error("--allow-infinite-loop 只配 --until html（预览档）：无限循环会把"
                     "GSAP 总时长折算成 1e10 量级，渲染出来的片长不可信、按时刻取帧也"
                     "不成立。定稿时把 repeat:-1 改成实际遍数")

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
        "workers": args.workers,
        "low_memory": args.low_memory,
        "speed": args.speed,
        "voice_id": args.voice_id,
        "on_fail": args.on_fail,
        # 调试档开了就必须在报告里留痕：成片可能无声或整段缺帧，事后翻报告
        # 要能一眼看出"这支片子是宽容渲染出来的"。
        "allow_best_effort": args.allow_best_effort,
    }
    if args.draft:
        # 草稿档总开关：报告记 draft=true，并把"哪些判据被放松"写进 skipped，
        # 翻报告能对出这一版不是按交付红线出的。
        _REPORT["draft"] = True

    # ── 第 2 步：TTS ──────────────────────────────────────────────
    # --resume 恒开（指纹缓存能证明一致才复用；要整重跑时直接调 pipeline.py，
    # 不加 --resume 即可）。
    tts_args = ["--source", args.source, "-o", out, "--resume"]
    # --speed / --voice-id 有 argparse 默认值也要永远透传：否则 resume 指纹
    # 依赖两边默认值恰好相同。
    tts_args += ["--speed", str(args.speed), "--voice-id", args.voice_id]
    # 空串是**显式声明**，不是"没给"：它表示"这一段不要任何风格指令"，而 pipeline
    # 里段/轮级的 voice_style 判据就是按这个口径写的（用 `is not None` 而非 `or`）。
    # 这里若按真值判，空串就被吃成"退回 pipeline 默认播报文案"——作者写下的"不要
    # 风格"被静默翻成"要默认风格"，两句意思相反且零报错。
    if args.voice_style is not None:
        tts_args += ["--voice-style", args.voice_style]
    if args.gap is not None:
        tts_args += ["--gap", str(args.gap)]
    # --on-fail 同理永远透传：silence 会让 manifest 进 degraded，那是下方拦截
    # 真正能触达的入口。
    tts_args += ["--on-fail", args.on_fail]
    if args.dry_run:
        tts_args.append("--dry-run")
    # ── 无 key 也允许出预览 ────────────────────────────────────────
    # 预览档（--until html / check）是纯本地编译，不产出成片、不花渲染成本；
    # 把这件事抵押给 TTS 账户余额是荒唐的。没有 key 时自动降级到 --no-tts：
    # 时间轴按字数估算，manifest 标 degraded，HTML 照出。定稿路径（不给
    # --until）不降级——那条路要交付成片，无声片不是成片，必须硬停。
    # --allow-degraded 不参与这个判断：它是"我接受降级产物"的显式声明，
    # 而这里问的是"有没有钱配音"。
    if args.until in ("html", "check") and not args.dry_run \
            and not pipeline.has_tts_key():
        print("\n[run] 未检测到 MIMO_API_KEY：TTS 跳过，按估算时长出预览"
              "（产物标记 degraded，不可直接渲染；补上 key 后重跑会自动重合成）",
              flush=True)
        tts_args.append("--no-tts")
        _REPORT.setdefault("skipped", []).append("TTS（无 API key，预览档按估算时长）")
    _run_step(pipeline.main, tts_args, "TTS")

    # ── 第 3 步：配图 ─────────────────────────────────────────────
    # 配图不由本脚本产出（第 3 步生图/画 SVG 落进 images/ 并写 images.json）。
    # run.py 只统计覆盖率，并且只在真要渲染时拦缺图。
    # 文件名与 pipeline 写侧同源（MANIFEST_FILENAME）：各拼一遍的话，改一侧
    # 就等于把"manifest 不存在"这条分支永久打开。
    manifest = os.path.join(out, MANIFEST_FILENAME)
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
        # images.json 有键但媒体文件不可用（不在盘上，或软链接/绝对路径越出项目目录）：
        # gen_hyperframes 对这类坏路径无条件 fail-fast，"只警告继续"在这里
        # 兑现不了——与其先打一句"继续生成 HTML"再让 HTML 步骤 exit 1，
        # 不如就地报清病因，并把还没映射的段一并列出来让人一次修完。
        print("[run] images.json 映射的媒体文件不可用（不存在，或通过软链接/绝对路径"
              "越出项目目录）："
              + ", ".join(missing_files) + "。\n"
              f"请补齐 {images_dir} 下的文件，或修正/删除对应映射条目。",
              file=sys.stderr)
        if missing_keys:
            print("[run] 另外这些段落还没有配图映射：" + ", ".join(missing_keys),
                  file=sys.stderr)
        if not args.draft:
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)
        # --draft：草稿期"先搭结构、图稍后画"的常态——src 还没落盘不该把整条
        # 管线卡死。放行并把同一个决定传给生成器（--draft 下 gen 会给画布段换
        # 占位、槽位段留空位），预览/成片里的空位/占位就是"还没画完"的诚实信号。
        print("[run][warn] 以上缺 src 在 --draft 下不阻断：本次继续，预览/成片里"
              "对应位置会空着或用占位（草稿档；定稿渲染必须补全后再跑）",
              file=sys.stderr)
        _REPORT["skipped"].append("缺 src 文件放行（--draft，预览/成片可能空位/裂图）")

    if missing_keys:
        # 只有真要出片才拦：--until html 是"图没画完先看版式"的迭代路径，
        # 同一份缺图清单在那里只降级成警告。missing 的口径见 _image_coverage。
        #
        # 唯一例外是画面主导的页（整页画布 / 图版，判据 _segments.is_full_bleed
        # 一份）：配图就是整个画面，gen_hyperframes 在生成期必然 exit 1
        # （canvas_layout_errors）。对它承诺"继续生成 HTML 供预览"是兑现不了的
        # 承诺——打印完这句紧接着就是一条 [error]，两条信息自相矛盾。所以这类段
        # 缺图不分 --until，与 missing_files 同样就地拦下并点名病因
        # （--draft/--placeholder-missing 是它的逃生口：占位 SVG 先把画面填上）。
        _layout_by_sid = {s["id"]: seg_layout(s) for s in _tm["segments"]}
        _canvas_missing = [s for s in missing_keys
                           if _layout_by_sid.get(s) in FULL_BLEED_LAYOUTS]
        # --slot-no-image：槽位段缺图时 gen 真的不画图槽（句子流铺满内容区），
        # 所以那些段不再算"待补图"。这里若照旧算进去，定稿路径会 exit 2 把这条
        # 降级路整条堵死——而 SKILL.md 正是把它当成"没有生图能力时怎么出片"来
        # 写的。满幅页（canvas/plate）缺图照旧拦：那两页没有图就没有画面。
        # 配图覆盖率仍按缺图记（_REPORT["images"] 已在上方定稿），不放行的是
        # 拦截，不是事实。
        if args.slot_no_image:
            _slot_missing = [s for s in missing_keys if s not in _canvas_missing]
            if _slot_missing:
                print("[run] --slot-no-image：" + ", ".join(_slot_missing)
                      + " 缺图不阻断——槽位段改由句子流铺满内容区（画面会素一些，"
                        "报告里点名这几段）。", file=sys.stderr)
            missing_keys = _canvas_missing
        if missing_keys:
            print("[run] 以下段落还没有定稿配图："
                  + ", ".join(missing_keys) + "。\n"
                  "请按第 3 步补图：ImageGen 生图、手绘 SVG 矢量示意图"
                  "，把文件放进 "
                  f"{images_dir}。\n"
                  f"然后在 {images_json} 写好各段映射后重跑本命令。",
                  file=sys.stderr)
        if _canvas_missing and (args.placeholder_missing or args.draft):
            print("[run] 其中 " + ", ".join(_canvas_missing)
                  + " 是满幅图版式（canvas/plate）且还没有配图：本次用占位 SVG 先把 HTML 生成出来"
                    "（只看结构用；--draft 跑渲染时成片里也会是占位页），"
                    "正式渲染前必须按当前画幅补真图。",
                  file=sys.stderr)
        elif _canvas_missing:
            print("[run] 其中 " + ", ".join(_canvas_missing)
                  + " 是满幅图版式（整页画布 layout: \"canvas\" 或图版 layout: "
                    "\"plate\"）：那一页的配图铺满整个画幅，缺图时连预览 HTML 都生成不出来"
                    "（不是「渲染前补齐即可」那一档），请先按当前画幅补图；"
                    "只想先看整体结构可加 --until html --placeholder-missing，"
                    "或直接 --draft。",
                  file=sys.stderr)
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)
        if missing_keys and args.until == "render" and not args.draft:
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)
        if missing_keys:
            print("[run] 继续生成 HTML 供预览（渲染前必须补齐上面这些段）。",
                  file=sys.stderr)

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
            # 降级句带指纹缓存，静音占位会被 --resume 一直复用——不放一句
            # 可执行的补录路径，定时任务/隔天重跑会永远卡在同一处。
            print("[run] 补录：删除 " + os.path.join(out, "sentences")
                  + " 下对应句子的 .wav 及 .sha/.spd/.failed sidecar，"
                  "下次带 --resume 重跑会自动重合成该句；"
                  "成功合成后 .failed marker 由管线自动清理。", file=sys.stderr)
            sys.exit(3)
        if args.allow_degraded:
            print(f"[warn] 已显式允许降级：{_why}，"
                  "生产报告将标记 degraded。", file=sys.stderr)
        else:
            print(f"[warn] HTML 预览继续生成，但 {_why}；"
                  "最终渲染必须显式加 --allow-degraded。", file=sys.stderr)

    # ── 台词板核对：句序对不对得上最终时间轴 ──────────────────────
    # 写稿时 beats 的 `on` 是整数句序，而句子是**重分句**后定下来的（_text 的分句
    # 规则与本段标题、对话轮次的摊平都会影响句数）。一旦句数变了，`on: 4` 就会
    # 落到一片空处——静默失效，成片里完全看不出来。所以核对必须排在 manifest 已
    # 校验之后、生成 HTML 之前：早一点拿不到最终句数，晚一点已经烧进产物。
    #
    # 越界是 error（会让整段台词板错位，成片看不出来），其余是 warn。
    _beats_errs, _beats_warns, _beats_total = beats.audit(
        _tm, load_images_json(images_json) if has_images else None)
    for _e in _beats_errs:
        print(f"[run][error] 台词板：{_e}", file=sys.stderr)
    for _w in _beats_warns:
        print(f"[run][warn] 台词板：{_w}", file=sys.stderr)
    _REPORT["beats"] = {"total": _beats_total,
                        "errors": len(_beats_errs), "warnings": len(_beats_warns)}
    if _beats_errs:
        _write_report(_report_path())
        print("[run] 台词板的句序对不上最终时间轴，未生成 HTML。"
              "按上面每条把 'on' 改到真实的那一句（或删掉这一拍）后重跑。",
              file=sys.stderr)
        sys.exit(2)

    # ── 第 4 步 a：生成 composition HTML ──────────────────────────
    html_args = ["-m", manifest, "-o", os.path.join(project, "index.html"),
                 "--aspect", args.aspect,
                 "--fps", str(args.fps)]
    if has_images:
        html_args += ["--images", images_json]
    # 占位档由上面的拦截逻辑放行；这里把同一个决定传给生成器，两处不各判一次。
    if args.placeholder_missing and args.until == "html":
        html_args.append("--placeholder-missing")
        _REPORT["skipped"].append("占位真图（--placeholder-missing，预览档）")
    # 草稿档总开关：透传 --draft，缺图/坏 src/比例/字号/ease 的放松都只由 gen
    # 那一份 parser 生效——run.py 不逐个复刻旗标，避免两处各判一次分家。
    if args.draft:
        html_args.append("--draft")
        _REPORT["skipped"].append(
            "草稿档（--draft：占位 / ease 放行 / 缺 src 降级 / 比例·对比度·字号降 warn）")
    # 视觉旋钮原样转给生成器：口径与取值校验只有 gen 那一份 parser，这里不复制。
    for pair in args.set or ():
        html_args += ["--set", pair]
    # 无图降级档：判据只有 gen 那一份（它决定槽位段出不画图槽），这里只透传，
    # 不在这里另判一次"哪些段算缺图"。
    if args.slot_no_image:
        html_args.append("--slot-no-image")
        _REPORT["skipped"].append(
            "槽位段缺图放行（--slot-no-image：句子流铺满内容区，画面更素）")
    if args.allow_ease:
        html_args.append("--allow-ease")
        _REPORT["skipped"].append("ease 钉固目录（--allow-ease，预览档）")
    if args.allow_infinite_loop:
        html_args.append("--allow-infinite-loop")
        _REPORT["skipped"].append("无限循环拒收（--allow-infinite-loop，预览档）")
    _run_step(gen_hyperframes.main, html_args, "生成 HTML")
    # 生效的视觉覆写进报告：--set 打错、写了个对本片无效的键，
    # 事后都能在 production_report 里核对，不用靠"我明明改了啊"。
    if overrides():
        _REPORT["params"]["template_overrides"] = dict(sorted(overrides().items()))

    # 不设自动版式检查：一条 Chrome 度量链实测一次 25-50s，换不来人工预览 3s
    # 就能给出的信息（--until html 就是给人工预览收口的）。

    # HTML 已生成；渲染复用同一次 Hyperframes 命令解析。
    _HF_COMMAND = hyperframes_command()

    if args.until == "html":
        _REPORT["skipped"].append("render（--until html）")
        # 预览档也能跑观感门禁：那时帧来自预览 HTML（无编码损失），量的是编排
        # 本身而不是编码质量——迭代期"这一页根本没内容"就该在这里被发现，
        # 而不是等渲染完一支片子。
        if args.perceptual:
            _run_perceptual(project, _tm, args.aspect, _HF_COMMAND)
        _write_report(_report_path())
        print(f"[run] 已生成 {os.path.join(project, 'index.html')}。"
              "先在浏览器里打开它确认版式和配图，定稿后去掉 --until 直接渲染。",
              file=sys.stderr)
        _print_report_summary()
        return

    # check 与 render 都要启动 Hyperframes CLI；先拦明确过旧的 Node，避免把
    # engines 不兼容伪装成 npx、语法或 Chrome 错误。
    _verdict, _node_msg = node_version_gate()
    if _verdict == "block":
        raise SystemExit(f"[run] {_node_msg}")
    if _verdict == "warn":
        print(f"[run][warn] {_node_msg}", file=sys.stderr)

    # ── 第 4 步 a'：官方门禁 ─────────────────────────────────────
    # 与 --until html 同一类"迭代档"：只跑 check，不进渲染。
    # 不接在 --until html 后面自动跑：那条路的意图是"3 秒出 HTML 先看一眼"，check
    # 要起 Chrome、十几秒。
    if args.until == "check":
        issues = _official_gate(project, _HF_COMMAND)
        _REPORT["skipped"].append("render（--until check）")
        # --perceptual 在这一档同样成立：HTML 已生成、帧取得出来，而 check 只判
        # "能不能渲"、判不了"页面上有东西没有"。写在落报告之前，判词才进得去。
        if args.perceptual:
            _run_perceptual(project, _tm, args.aspect, _HF_COMMAND)
        _write_report(_report_path())
        for sev, kind, msg in issues:
            print(f"[run][{sev}] {kind}: {msg}", file=sys.stderr)
        _print_report_summary()
        if any(sev == "error" for sev, _, _ in issues):
            raise SystemExit("[run] 官方门禁有 error，见上方条目；修完再跑渲染")
        return

    # ── 第 4 步 b：渲染 ───────────────────────────────────────────
    # 定稿路径自动跑一次官方门禁：error 拦在渲染前，未登记的新 warning 也点名。
    # 渲染是分钟级，这一步是十秒级；以前靠文档要求人手跑，漏跑就是拿成片换结论。
    for sev, kind, msg in _official_gate(project, _HF_COMMAND):
        print(f"[run][{sev}] 官方门禁 {kind}: {msg}", file=sys.stderr)
        if sev == "error":
            _write_report(_report_path())
            raise SystemExit("[run] 官方门禁有 error，未进渲染；修完再跑")


    out_video = os.path.join(project, "out.mp4")
    if args.low_memory and args.workers != 1:
        # CLI 的低内存档把 worker 钉成 1（它自己的 --help 写明）。这里不出声，
        # 用户会以为自己写的 --workers 生效了，然后对着"怎么这么慢"找半天原因。
        print(f"[run][warn] 已开 --low-memory-mode：Hyperframes 会把抓帧 worker 钉成 1，"
              f"本次的 --workers {args.workers} 不生效（低内存档靠它避免内存抖动）。",
              file=sys.stderr)
    # --draft 隐含 best-effort：粗剪档允许媒体缺失/抓帧未就绪也照出片，成片可能
    # 无声或缺帧——这正是"先看一版动起来"要的宽容；报告已记 draft 留痕。
    _best_effort = args.allow_best_effort or args.draft
    if _best_effort:
        _src = "（--draft 隐含）" if args.draft and not args.allow_best_effort else ""
        print(f"[run][warn] 已开 --allow-best-effort{_src}：媒体缺失或抓帧未就绪时"
              "渲染器照旧出片，成片可能无声或整段缺帧——这是调试档，不要拿去交付。",
              file=sys.stderr)
    hf_render = build_render_command(
        out_video, args.quality, args.fps, args.workers, command=_HF_COMMAND,
        low_memory=args.low_memory, allow_best_effort=_best_effort,
    )
    # 渲染等待上限跟着成片时长走。实测渲染耗时约为视频时长的 1.2–3 倍
    # （162s 片 190–466s，后者是抓帧超时重抓的极端），10 倍 + 10 分钟
    # 打底给出充足余量；默认硬上限（DEFAULT_MAX_WAIT）会把长片或 60fps/high
    # quality 的正常渲染误杀成"超时"。地板取自 render_wait 的默认值而不是再写
    # 一遍 1800——改默认值时这里跟着走。
    _render_cap = max(DEFAULT_MAX_WAIT, float(_tm["total_duration"]) * 10 + 600)
    t0_render = time.monotonic()
    try:
        render_wait(hf_render, out_video, cwd=project, max_wait=_render_cap)
    except SystemExit:
        _REPORT["steps"].append({"name": "渲染", "seconds": round(time.monotonic() - t0_render, 1), "ok": False})
        _write_report(_report_path())
        raise
    _REPORT["steps"].append({"name": "渲染", "seconds": round(time.monotonic() - t0_render, 1), "ok": True})
    # 走到这里成片必已存在且非空：_render_poll_loop 的所有 return 路径都以
    # "实测 size>0"为前提（自然退出量一次，强杀路径再过 _verify_killed_render
    # 的整容器解码），失败路径一律 SystemExit——不再补一遍 isfile/getsize。

    # ── 成片时长契约：演完了才算完成 ──────────────────────────────────
    # 容器完整证明不了内容完整。抓帧超时重抓、worker 崩、Chrome 提前退出都
    # 能产出一支 ffmpeg 全解码过得去、却比时间轴短一截的片子：后面几段整段
    # 不在片里，字幕、配图、导演节拍全跟着丢，而控制台照打"完成、成片：…"。
    # 这是整条管线唯一没有契约的一段——渲染之前的每一步都有校验拦着，渲染
    # 之后只剩"文件在且非空"。所以这里补最后一道：拿成片实测时长与 manifest
    # 的 total_duration 对账，容差取全仓共用的那份（_timeline.duration_tolerance
    # ——音频与 manifest 对账就是这一档），量不到（ffmpeg 不可用）只降级 warn，
    # 不阻断交付。
    _expected = float(_tm["total_duration"])
    _tol = duration_tolerance(_expected)
    _actual = probe_video_duration(out_video)
    _REPORT["video"] = {
        "path": out_video,
        "expected_seconds": round(_expected, 3),
        "seconds": None if _actual is None else round(_actual, 3),
        "tolerance_seconds": round(_tol, 3),
        "check": "skipped",
    }
    if _actual is None:
        print("[run][warn] 成片时长未校验（ffmpeg 不可用或读不出 Duration）："
              f"时间轴 {_expected:.2f}s，成片 {out_video}。\n"
              "      成片已生成，但「演完了没有」这一条没人证明——"
              "装好 ffmpeg 重跑本命令即可补上这道闸。", file=sys.stderr)
    elif _actual + _tol < _expected:
        _REPORT["video"]["check"] = "short"
        _write_report(_report_path())
        _print_report_summary()
        print(
            f"[run] 成片比时间轴短：实测 {_actual:.2f}s，时间轴 {_expected:.2f}s"
            f"（容差 {_tol:.2f}s）——后段内容可能整个不在片里。\n"
            "      常见成因：抓帧超时后重抓丢帧、worker 崩溃、Chrome 提前退出。\n"
            "      先看渲染日志（成片同目录的 out.render.log）里有没有"
            "`Parallel capture timed out`；有的话降 --workers 到 2 重渲。\n"
            "      确认画面确实完整、只是尾帧差这点时长时，重跑本命令再核一次。"
            f"成片已留在 {out_video}，未删除。", file=sys.stderr)
        sys.exit(4)
    elif _actual - _tol > _expected:
        _REPORT["video"]["check"] = "long"
        print(f"[run][warn] 成片比时间轴长：实测 {_actual:.2f}s，"
              f"时间轴 {_expected:.2f}s（容差 {_tol:.2f}s）。\n"
              "      画面内容完整，多出来的多半是末段音频尾巴或末帧余量；"
              "若片尾出现明显空帧，检查 manifest 的 total_duration 是否漏算了"
              "最后一段。", file=sys.stderr)
    else:
        _REPORT["video"]["check"] = "ok"

    # ── 第 4 步 c：取帧给人工过目（可选，--check-frames）──────────────
    # 画面对不对只有人眼判得准（黑屏/死平是"几十种真实坏画面"里的两种，自己写像素
    # 阈值只是把它猜一遍）。取帧这件事官方有：`hyperframes snapshot` 按时刻取 PNG
    # 并拼 contact-sheet，与渲染同一套 Chrome、同一套时间线 seek。这里只做编排：
    # 传给成片的时刻表，把产物路径写进报告。取帧失败只 warn——它是附加的查看手段，
    # 不是交付前提（成片本身已过时长契约）。
    if args.check_frames:
        snap_dir = os.path.join(project, "snapshots")
        _ts, _samp = sample_snapshot_times(float(_tm["total_duration"]))
        _snap, _why = snapshot_frames(project, out_video, _ts, snap_dir, command=_HF_COMMAND)
        _REPORT["video"]["snapshots"] = (
            {"dir": _snap, "times": _ts, "even_sample": _samp}
            if _snap else {"error": _why})
        if _snap:
            print(f"[run] 取帧 {len(_ts)} 张 + contact sheet：{_snap}"
                  + ("（时长超出逐秒取帧上限，已按整片均分）" if _samp else ""))
        else:
            print(f"[run][warn] 取帧未完成（{_why}）：成片已生成，但这一眼没人替你看。",
                  file=sys.stderr)

    # ── 观感门禁（--perceptual）：唯一一道在看画面的检查 ────────────────
    # 前面所有门禁都在看结构：official check 判"能不能渲"，成片时长契约判
    # "演完没有"，`--check-frames` 只是把帧递给人。而"这一页在屏幕上到底
    # 有没有东西"只有这一道在量。默认关（要 Node + Chrome + Pillow），
    # `--perceptual` 打开；只报不拦。
    if args.perceptual:
        _run_perceptual(project, _tm, args.aspect, _HF_COMMAND)

    print(f"\n[run] 完成。成片：{out_video}", flush=True)
    _write_report(_report_path())
    _print_report_summary()


if __name__ == "__main__":
    main()
