#!/usr/bin/env python3
"""Content-to-Video — 一键编排（可选，薄组合层）。

把 TTS → 配图 → HTML → 渲染串成一条命令，内部按依赖顺序调用各子脚本；
不改变任何子脚本的独立性，只做组合与默认值。

  python scripts/run.py --source segments_source.json -o audio_output --until html
  # ↑ 迭代用：跑到 HTML 为止，在 hf-project/ 打开 index.html 或 preview 看版式和配图。
  python scripts/run.py --source segments_source.json -o audio_output
  # ↑ 定稿用：一路渲染到 hf-project/out.mp4。

参数清单不在这里维护（与 SKILL.md「一键编排」、各子脚本 --help 三处重复必然
漂移）：单项参数的语义与默认值以 ``--help`` 为准，编排行为与迭代节奏见 SKILL.md。
"""
import argparse
import datetime
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（与 guard_not_in_skill_dir 同一条纪律：技能目录不留制作残渣）
sys.path.insert(0, SCRIPTS_DIR)
from _theme import list_theme_names  # noqa: E402  --theme choices 与 _theme 内嵌注册表同步
from _template import get_canvas  # noqa: E402  画幅 → 画布尺寸（生产报告 params.canvas 用）
from _contracts import (DEFAULT_SPEED, list_voice_ids,  # noqa: E402
                        validate_speed, load_timing_manifest,
                        validate_images_json)
from _contracts import is_content_sid  # noqa: E402  内容段 id 前缀约定的单一来源（配图覆盖率统计）
from _audio import _remove_quiet  # noqa: E402  全仓唯一"尽力删文件"实现（定义在 _audio）
from html_renderer import manifest_segments  # noqa: E402  段落分组的唯一口径（segments 缺失时按兜底规则分组）
import _script_utils as _su  # noqa: E402
from _script_utils import (setup_stdio,  # noqa: E402  重定向场景 stdout 强制 UTF-8
                           guard_not_in_skill_dir)  # noqa: E402  产物落技能目录的守卫（与 pipeline/gen 共用同一实现）

# ── Hyperframes / render runtime：只服务 run.py，直接内聚。 ──
def hyperframes_spec() -> str:
    return os.environ.get("CTV_HYPERFRAMES_PACKAGE", "hyperframes").strip() or "hyperframes"


def _resolve_local_hyperframes(project: Optional[str]) -> Optional[str]:
    local = str((Path(project or os.getcwd()).resolve() / "node_modules" / ".bin" / "hyperframes"))
    # Windows 的 npm shim 是 .cmd，优先它；POSIX 下 .bin/hyperframes 本身可执行。
    candidates = [local + ".cmd", local, "hyperframes.cmd", "hyperframes"] if os.name == "nt" \
        else [local, "hyperframes"]
    for candidate in candidates:
        resolved = shutil.which(candidate) if not os.path.isabs(candidate) else candidate
        if resolved and (os.path.isfile(resolved) or shutil.which(resolved)):
            return resolved
    return None


def hyperframes_command(project: Optional[str] = None) -> list[str]:
    local = _resolve_local_hyperframes(project)
    if local:
        return [local]
    npx = shutil.which("npx")
    return [npx or "npx", "--yes", hyperframes_spec()]


def _resolve_npx_shim(shim_path):
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    node_exe = os.path.join(shim_dir, "node.exe")
    npx_cli = os.path.join(shim_dir, "node_modules", "npm", "bin", "npx-cli.js")
    if os.path.isfile(node_exe) and os.path.isfile(npx_cli):
        return [node_exe, npx_cli]
    return None


def _cmdline_for_batch(exe, args):
    """给 .cmd/.bat 手工拼一条 cmd.exe /c 命令行（字符串形式）。

    不能返回 [cmd.exe, /c] + args 列表：cmd 会把整条命令行**再解析一遍**，
    而 list2cmdline 只给含空格的参数加引号——不含空格却含 `&`/`|` 的路径
    （`-o "x&y"` 目录名）会被拆成第二条命令，实测可注入。
    双引号内的 cmd 元字符是字面量，因此每个参数都包引号、再整体包一层
    外层引号（cmd /c 的既定规则：引号数 >2 时剥掉首尾引号再执行）。
    Popen 收到字符串时按原样写命令行、不再二次转义，正好绕开 list2cmdline。
    残留面：%% 展开在 cmd 引号内仍会发生——这只影响路径里恰好含现有环境变量
    名的写法，不构成命令拆分注入。含双引号的参数不被本函数支持（`""` 的
    转义语义在 cmd 与 MSVCRT 之间不一致）：NTFS 本就禁止文件名含 `"`，
    本函数只用于传路径，无需处理该形态。
    """
    def q(a):
        return '"' + str(a).replace('"', '""') + '"'
    body = " ".join([q(exe)] + [q(a) for a in args])
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    return f'{comspec} /c "{body}"'


def _fmt_cmd(resolved):
    """打印用：resolve_command 的返回值现在可能是列表或命令行字符串。"""
    return resolved if isinstance(resolved, str) else " ".join(resolved)


def resolve_command(cmd):
    if not cmd:
        return cmd
    if isinstance(cmd, str):
        return cmd  # 已解析成命令行字符串（_cmdline_for_batch），幂等直返
    exe = shutil.which(cmd[0])
    if not exe:
        return cmd
    if os.name == "nt" and not exe.lower().endswith((".exe", ".cmd", ".bat")):
        for ext in (".exe", ".cmd", ".bat"):
            alt = shutil.which(cmd[0] + ext)
            if alt:
                exe = alt
                break
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        if exe.lower().endswith(".cmd"):
            direct = _resolve_npx_shim(exe)
            if direct is not None:
                return direct + cmd[1:]
        return _cmdline_for_batch(exe, cmd[1:])
    return [exe] + cmd[1:]


def build_render_command(output: str, quality: str, fps: int, workers: int,
                         gpu: bool = False,
                         command: Optional[List[str]] = None) -> List[str]:
    base = list(command or hyperframes_command())
    cmd = base + ["render", "-o", output, "--quality", quality,
                  "--fps", str(fps), "--workers", str(workers)]
    if gpu:
        cmd.append("--gpu")
    # 参数拼齐后再解析：.cmd 兜底路径返回的是整条命令行字符串，先 resolve
    # 再加参数会把参数丢在字符串外面（等于丢进 cmd 的重解析）。
    return resolve_command(cmd)


_EXIT_GRACE_SECONDS = 12.0
_GRACE_PER_MB = 0.15
_GRACE_CAP_SECONDS = 90.0
_POLL_INTERVAL_SECONDS = 2.0
_STABLE_SIZE_CHECKS = 2


def _adaptive_grace(size_bytes):
    return max(_EXIT_GRACE_SECONDS, min(_GRACE_CAP_SECONDS,
                              size_bytes / (1024.0 * 1024.0) * _GRACE_PER_MB))


def _print_render_log_tail(log_path, max_lines=25):
    if not log_path or not os.path.isfile(log_path):
        return
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    print(f"[run] 渲染日志末尾（完整日志: {log_path}）:", file=sys.stderr)
    for line in lines[-max_lines:]:
        print("    " + line.rstrip(), file=sys.stderr)


def try_kill_process_tree(proc):
    """尽力杀掉渲染进程树。失败必须出声：孤儿 Chrome/Node 在后台继续吃
    CPU/内存，下一次渲染会更快崩，而用户看不到任何线索。"""
    if os.name == "nt":
        try:
            r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", "replace").strip()
                print(f"[run][warn] taskkill 清理渲染进程树失败"
                      f"（exit {r.returncode}）：{err[:200]}", file=sys.stderr)
        except Exception as e:
            print(f"[run][warn] taskkill 不可用（{e}），渲染子进程可能残留",
                  file=sys.stderr)
        try:
            proc.wait(timeout=5)  # 回收，避免僵尸句柄
        except Exception:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)
        except Exception as e:
            print(f"[run][warn] 进程组清理失败（{e}），渲染子进程可能残留",
                  file=sys.stderr)


def _discard_partial_render(out_path):
    """失败/超时路径清掉半截 out.mp4：残片大小 >0，下次有人直接取走
    out.mp4 或只看"文件存在且非空"就会把废片当交付物。"""
    _remove_quiet(out_path)


def _probe_file_size(path):
    """轮询用文件大小：不存在或被并发删改（Windows 上杀毒扫描会短暂
    锁文件，exists→getsize 之间有竞态窗口）时返回 None，绝不让
    OSError 冒进轮询循环——那会跳过 try_kill_process_tree，留下
    孤儿 Node/Chrome 进程树。"""
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _verify_killed_render(out_path):
    """抢在渲染进程自然退出前 kill 掉进程树换来的"完成"，必须过一遍
    ffmpeg 全解码探测。MP4 的 moov 箱常在收尾才写，编码器被中途杀掉会留下
    大小可观却放不开的废片——同仓 WAV 有 wav_data_consistent、配图有 ffmpeg
    probe 自证，成片不该是例外。ffmpeg 不可用时退回旧行为（只 warn），
    与 gen_hyperframes 的媒体探测降级口径一致。"""
    try:
        from _audio import get_ffmpeg
        ff = get_ffmpeg()
        r = subprocess.run([ff, "-v", "error", "-i", out_path,
                            "-f", "null", "-"],
                           capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[run][warn] 成片完整性无法验证（ffmpeg 不可用：{e}）",
              file=sys.stderr)
        return
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        _discard_partial_render(out_path)
        raise SystemExit(
            f"[run] 渲染进程被提前结束时成片不完整（容器未正常收尾）：{err[-200:]}\n"
            "已丢弃废片，请重跑渲染。")


def _render_wait(cmd, out_path, cwd=None, max_wait=1800.0):
    """启动 Hyperframes render；输出文件稳定后不再死等 Node/Chrome 退出。"""
    log_path = os.path.splitext(os.path.abspath(out_path))[0] + ".render.log"
    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    if os.path.exists(out_path):
        try:
            os.remove(out_path)
        except OSError as e:
            raise SystemExit(f"[run] 无法删除旧成片 {out_path}：{e}")
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass

    resolved_cmd = resolve_command(cmd)
    print(f"\n>>> {_fmt_cmd(resolved_cmd)}", flush=True)
    print(f"[run] 渲染日志: {log_path}", file=sys.stderr)
    log_fh = None
    proc = None
    try:
        log_fh = open(log_path, "wb")
    except OSError as e:
        raise SystemExit(f"[run] 无法写渲染日志 {log_path}：{e}")
    try:
        # start_new_session 仅 POSIX 可用；Windows 上会裸抛 ValueError。
        # Windows 的进程树清理交给 try_kill_process_tree 的 taskkill /T /F。
        popen_kwargs = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(resolved_cmd, cwd=cwd,
                                stdout=log_fh, stderr=log_fh,
                                **popen_kwargs)
    except (FileNotFoundError, OSError) as e:
        # resolve_command 找不到可执行文件时会把原命令（如 "npx"）直接透传，
        # Popen 在这里裸抛 FileNotFoundError；与 _run 同一口径报人话并退出。
        raise SystemExit(f"[run] 无法启动渲染命令 {_fmt_cmd(cmd)}：{e}\n"
                         "请确认 Node.js 已安装且 npx 在 PATH 上"
                         "（或在项目目录 npm install hyperframes 使用本地 CLI）。")
    try:
        _render_poll_loop(proc, out_path, log_path, max_wait)
    except BaseException:
        # Ctrl-C（及任何异常退出）也必须先收进程树再抛：POSIX 上渲染进程
        # 在独立会话（start_new_session），SIGINT 不会传导给它，留下就是
        # 孤儿 Node/Chrome 继续吃 CPU；被提前掐断的半截 mp4 更不能当交付物
        # 留在盘上。poll() 已退出（渲染自身失败的路径）就不再 taskkill——
        # 对一个不存在的 PID 报"清理失败"是纯噪音。
        if proc.poll() is None:
            try_kill_process_tree(proc)
        _discard_partial_render(out_path)
        raise
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass


def _render_poll_loop(proc, out_path, log_path, max_wait):
    """轮询成片直到"完成或必须停止等待"；清理与 discard 由调用方兜底。"""
    t_start = time.time()
    last_size = -1
    last_mtime = -1.0
    stable_count = 0
    grace_deadline = None
    while True:
        elapsed = time.time() - t_start
        if elapsed > max_wait:
            _print_render_log_tail(log_path)
            raise SystemExit(f"[run] 渲染超过 {max_wait:.0f}s，已停止等待。")

        ret = proc.poll()
        if ret is not None:
            if ret != 0:
                _print_render_log_tail(log_path)
                raise SystemExit(f"[run] Hyperframes 渲染失败，退出码 {ret}")
            size = _probe_file_size(out_path)
            if size is not None and size > 0:
                return
            _print_render_log_tail(log_path)
            raise SystemExit("[run] Hyperframes 退出成功，但未生成有效成片")

        size = _probe_file_size(out_path)
        if size is not None:
            try:
                mtime = os.path.getmtime(out_path)
            except OSError:
                mtime = -1.0
            if size > 0 and size == last_size and mtime == last_mtime:
                stable_count += 1
            else:
                stable_count = 0
                grace_deadline = None
            last_size, last_mtime = size, mtime
            if stable_count >= _STABLE_SIZE_CHECKS:
                if grace_deadline is None:
                    grace_deadline = time.time() + _adaptive_grace(size)
                elif time.time() >= grace_deadline:
                    try_kill_process_tree(proc)
                    _verify_killed_render(out_path)
                    return
        else:
            stable_count = 0
            last_size, last_mtime = -1, -1.0
        time.sleep(_POLL_INTERVAL_SECONDS)


# ── 制作报告：一次 run.py 跑完后，各步骤耗时/配图情况/跳过了什么散落在各
# 步骤自己的 stdout 里，人工翻起来很累。这里不改变任何步骤本身的行为，只是
# 在旁路记一份轻量流水账，跑完打印小结 + 写一份 production_report.json，
# 方便"这次跑得正不正常"一眼判断（例如：TTS 花了很久是不是网络问题、配图
# 是不是大面积没搜到而不是稿件写少了）。任何一步失败退出前也会尽量把已记录
# 的部分写盘，不指望"必须跑到最后才有报告"。
_REPORT = {"steps": [], "images": None, "skipped": [], "degraded": []}
# 制作报告的 out 目录：main() 一解析出 -o/--output 就赋值
_REPORT_DIR = None
# --dry-run 承诺"不写任何文件"（见参数 help），旁路制作报告也不能例外
# ——失败路径同样只打印不落盘。main() 解析完参数就置 True。
_DRY_RUN = False
# 整个 run 的起始时刻：total_seconds 用真实墙钟，而不是把各步骤耗时直接
# 相加（TTS 与配图并行时两步各记各的全程，相加会大于真实墙钟）。
_RUN_T0 = None


def _total_seconds():
    """本次 run 的真实墙钟时长；_RUN_T0 未赋值时退回各步骤耗时之和。"""
    if _RUN_T0:
        return round(time.time() - _RUN_T0, 1)
    return round(sum(s["seconds"] for s in _REPORT["steps"]), 1)


def _write_report(path):
    if _DRY_RUN:
        return
    try:
        _REPORT["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _REPORT["total_seconds"] = _total_seconds()
        # 原子写统一走 _script_utils.write_json_atomic（全仓唯一实现）：
        # production_report 被 Ctrl-C 打断在写一半时落截断 JSON，下次排查
        # "这次跑得正不正常"反而先崩在 json.load。
        _su.write_json_atomic(path, _REPORT)
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


def _script(name):
    return os.path.join(SCRIPTS_DIR, name)


def _run(cmd, step_name=None):
    """跑一条子命令，失败即记报告并退出。

    子进程与 run.py 保持同一进程组，Ctrl-C 直接传给它；渲染步骤另有
    _render_wait 负责在成片写完后收掉残留的 Node/Chrome 进程树。
    """
    resolved = resolve_command(cmd)
    print(f"\n>>> {_fmt_cmd(resolved)}", flush=True)
    t0 = time.time()
    try:
        ret = subprocess.run(resolved).returncode
    except (FileNotFoundError, OSError) as e:
        # 命令本身起不来（可执行文件不存在/无权限等）：按步骤失败处理并
        # 报对人，不再裸抛 traceback
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（无法启动命令 {_fmt_cmd(cmd)}）：{e}",
              file=sys.stderr)
        sys.exit(1)
    if ret != 0:
        if step_name:
            _REPORT["steps"].append(
                {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": False})
            _write_report(_report_path())
        print(f"[run] 步骤失败（退出码 {ret}）：{_fmt_cmd(resolved)}",
              file=sys.stderr)
        sys.exit(ret)
    if step_name:
        _REPORT["steps"].append(
            {"name": step_name, "seconds": round(time.time() - t0, 1), "ok": True})


def _report_path():
    """制作报告写到哪：优先 out 目录（跟 timing_manifest.json 放一起），
    -o/--output 还没解析出来（极早期失败）时退回当前目录。"""
    return os.path.join(_REPORT_DIR, "production_report.json") if _REPORT_DIR \
        else "production_report.json"


def _image_coverage(manifest_path, images_json):
    """返回 (all_sids, missing_keys, missing_files)：manifest 中需要配图的
    段落 sid 全集，其中 images.json 还没有映射键的（missing_keys），以及
    有键但媒体文件不在盘上的（missing_files）。

    两类必须分开：missing_keys 只是"图还没画完"，--until html 可以警告放行；
    missing_files 是"映射指向坏路径"，gen_hyperframes 无论跑到哪一步都会
    fail-fast——把它混进"只警告"里等于承诺一个根本兑现不了的继续（实测
    先打印"继续生成 HTML"、下一步整体 exit 1）。

    口径：只有内容段落（seg…）需要配图，判定收口在 _contracts.is_content_sid。
    开屏/结尾是纯文字 agenda 卡（章节罗列/要点总结），两画幅都不配图。
    """
    # 走 _contracts 的加载器而不是裸 json.load：上一轮若被中断在写 manifest
    # 的半途，文件是截断的 JSON，裸 load 会抛 JSONDecodeError traceback，
    # 用户看到的堆栈跟"TTS 产物坏了、该重跑"这个真实原因毫无关系。
    # load_timing_manifest 把缺字段/坏结构报成一句人话（ValueError），这里
    # 转成人话退出——与下方 images.json 及主流程降级检测同一口径。
    try:
        manifest = load_timing_manifest(manifest_path)
    except ValueError as e:
        raise SystemExit(
            f"[run] timing_manifest.json 无法读取（{manifest_path}）：{e}\n"
            "文件可能被上次中断的写入截断或结构不合法；"
            "请重跑 TTS 步骤重新生成后再来。")
    # manifest_segments 而非裸读 segments：契约允许手写/裁剪的 manifest
    # 不带 segments 字段（渲染端按兜底规则自动分组）。只读 manifest["segments"]
    # 会让 sids 恒空——缺图拦截静默失效，与画面实际用的段 id 漂移。
    sids = [seg.get("id", "") for seg in manifest_segments(manifest)
            if is_content_sid(seg.get("id"))]
    if not sids:
        return [], [], []
    if not os.path.isfile(images_json):
        return sids, sids, []
    # 与上方 load_timing_manifest 同一口径：images.json 也可能是上次写入
    # 被中断的截断 JSON——裸 json.load 会抛 JSONDecodeError traceback，与
    # "该补图/该重写映射"这个真实原因毫无关系。统一转成人话退出。
    try:
        with open(images_json, "r", encoding="utf-8-sig") as f:
            mapping = validate_images_json(json.load(f))
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"[run] images.json 无法读取（{images_json}）：{e}\n"
            "文件可能被上次中断的写入截断，或不是合法 JSON；"
            "修复/重写该文件（gen_charts.py 或手动）后重跑本命令。")
    missing_keys = [s for s in sids if s not in mapping]
    proj_dir = os.path.dirname(os.path.abspath(images_json))

    def _media_present(entry):
        # chart 条目与 gen_hyperframes 同一口径：图形由 Chart.js 在渲染端
        # 现画，即便附带 src（契约允许）也不做存在性检查——否则这里 exit 2
        # 拦掉的恰好是 HTML/render 本来完全正常的条目。
        if isinstance(entry, dict) and entry.get("type") == "chart":
            return True
        src = entry.get("src") if isinstance(entry, dict) else None
        if not src:
            return True
        return os.path.isfile(os.path.join(proj_dir, src))
    missing_files = [s for s in sids
                     if s in mapping and not _media_present(mapping[s])]
    return sids, missing_keys, missing_files


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
    parser.add_argument("--theme", default="dark", choices=list_theme_names())
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏）或 "
                             "landscape（1920×1080 横屏）")
    parser.add_argument("--fps", type=int, default=24, choices=[12, 24, 30, 60],
                        help="输出帧率（默认 24；12 是快速看画面的低规格迭代档）。"
                             "实测档位与耗时见 references/rendering.md「性能参数」。"
                             "gen_hyperframes 本身接受 1–240，非法值直通渲染端只会以更隐晦的报错失败")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"],
                        help="渲染质量（默认 standard）")
    parser.add_argument("--workers", type=int, default=4,
                        help="渲染抓帧 worker 数（默认 4，每 worker 一个独立 Chrome）。"
                             "有效区间与实测饱和点见 references/rendering.md「性能参数」；"
                             "低配机器遇 V8 堆崩溃或含视频配图时建议降到 2")
    parser.add_argument("--gpu", action="store_true",
                        help="透传 --gpu 给 Hyperframes 渲染（实测增益来自抓帧光栅化"
                             "而非硬件编码，且成片体积明显变大；见 rendering.md）")
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
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 pipeline --dry-run（不写文件）")
    parser.add_argument("--loudness", type=float, default=None,
                        help="对最终音频做响度归一化（LUFS，透传给 pipeline.py "
                             "--loudness）。平台有响度要求时用（如 -16）；"
                             "默认不做归一化。开启后成片音频为 combined_loud.wav")
    return parser



def main():
    setup_stdio()
    global _REPORT_DIR, _REPORT, _DRY_RUN, _RUN_T0
    # 同一进程里重复调用 main()（import run 后直接调用的测试/嵌入式用法）
    # 时，模块级 _REPORT 不能累积上一次的 steps/skipped——每次都从空白
    # 报告开始。
    _REPORT = {"steps": [], "images": None, "skipped": [], "degraded": []}
    _DRY_RUN = False
    _RUN_T0 = time.time()
    parser = _build_parser()
    args = parser.parse_args()
    _DRY_RUN = args.dry_run

    try:
        # --speed 有默认值（DEFAULT_SPEED），条件判断只会让"是否校验"看起来可变
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
        "theme": args.theme,
        "aspect": args.aspect,
        "canvas": "{}x{}".format(*get_canvas(args.aspect)),
        "fps": args.fps,
        "quality": args.quality,
        "speed": args.speed,
        "voice_id": args.voice_id,
        "loudness": args.loudness,
        "on_fail": args.on_fail,
    }

    # ── 第 3 步：TTS ──────────────────────────────────────────────
    tts_cmd = [sys.executable, _script("pipeline.py"),
               "--source", args.source, "-o", out]
    if not args.no_resume:
        tts_cmd.append("--resume")
    # --speed / --voice-id 都有 argparse 默认值，永远透传：显式写进子进程命令，
    # 让 pipeline 的 resume 指纹与本次参数一致，不依赖两边默认值恰好相同。
    tts_cmd += ["--speed", str(args.speed), "--voice-id", args.voice_id]
    if args.loudness is not None:
        # 响度归一化在 pipeline 末端对拼接后的人声轨执行（可选混入 BGM 之后），
        # 归一化后的音频由 manifest 的 combined_audio 指路，下游 HTML/检查
        # 都从 manifest 取，不在这里硬编码文件名。
        if not math.isfinite(args.loudness):
            parser.error(f"--loudness 必须是有限数值（LUFS，收到 {args.loudness}）")
        tts_cmd += ["--loudness", str(args.loudness)]
    if args.on_fail != "abort":
        # 单句 TTS 失败的降级策略（默认 abort=整条管线失败退出）。silence
        # 会把失败句降级为静音占位并让 manifest 进入 degraded 状态——那是
        # SKILL.md「降级显式化」规则与下方 degraded 拦截真正能触达的入口。
        tts_cmd += ["--on-fail", args.on_fail]
    if args.dry_run:
        tts_cmd.append("--dry-run")
    _run(tts_cmd, step_name="TTS")

    # ── 第 4 步：配图 ─────────────────────────────────────────────
    # 配图不由本脚本产出：agent 在第 4 步用 ImageGen 生图（方式 B）、
    # gen_charts.py 画图表（方式 C）、或 VideoGen/手动放置视频素材（方式 D），
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
    all_sids, missing_keys, missing_files = _image_coverage(manifest, images_json)
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

    if missing_files:
        # images.json 有键但媒体文件不在盘上（图被删过/路径写错）：
        # gen_hyperframes 对这类坏路径无条件 fail-fast，"只警告继续"在这里
        # 兑现不了——与其先打一句"继续生成 HTML"再让 HTML 步骤 exit 1，
        # 不如就地报清病因，并把还没映射的段一并列出来让人一次修完。
        images_dir = os.path.join(project, "images")
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
        # 同一份缺图清单在那里只降级成警告。missing 只含内容段。
        images_dir = os.path.join(project, "images")
        print("[run] 以下段落还没有定稿配图："
              + ", ".join(missing_keys) + "。\n"
              "请按第 4 步补图：ImageGen 生图（方式 B）、gen_charts.py 图表"
              "（方式 C）、或 VideoGen 生成/手动放置视频素材（方式 D）；需要真实照片时由你"
              "自己联网检索并下载到 "
              f"{images_dir}。\n"
              f"然后在 {images_json} 写好各段映射后重跑本命令。",
              file=sys.stderr)
        if args.until != "render":
            print("[run] 继续生成 HTML 供预览（渲染前必须补齐上面这些段）。",
                  file=sys.stderr)
        else:
            _write_report(_report_path())
            _print_report_summary()
            sys.exit(2)

    # TTS 的 silence fallback 不是“渲染成功”就能掩盖的降级状态。默认阻断
    # 交付；显式 --allow-degraded 才允许继续，且 production_report 会保留
    # 可机器读取的 degraded 标记。
    # manifest 走 _contracts 的加载器而不是裸 json.load：与上方 _image_coverage
    # 同一口径。降级检测本身不允许静默失败模式——旧路径把读坏的 manifest
    # 静默按"无降级"放行渲染，与"降级显式化"规则直接冲突；这里坏文件要
    # 报人话并退出，让用户重跑 TTS。
    try:
        _tm = load_timing_manifest(manifest)
    except ValueError as e:
        _write_report(_report_path())
        print(f"[run] timing_manifest.json 无法读取：{e}\n"
              "文件可能被上次中断的写入截断或结构不合法；"
              "请重跑 TTS 步骤重新生成后再来。", file=sys.stderr)
        sys.exit(1)
    _degraded_tts = sum(1 for _s in _tm.get("sentences", [])
                        if _s.get("synth_failed"))
    # 兜底也没兜住的句子压根不在 sentences 里（--on-fail silence 下 TTS 与
    # 静音双双失败），只看 synth_failed 会让"少了几句配音和字幕"的产物按
    # 正常成片放行。以 manifest 的 status 为准，明细计数缺失也不漏拦。
    _deg = _tm.get("degraded") or {}
    try:
        _degraded_lost = int(_deg.get("tts_lost_sentence_count") or 0)
    except (TypeError, ValueError):
        _degraded_lost = 0
    # pipeline 写进 degraded 的其余项（BGM 混音/响度归一化失败、段落被剔除）。
    # 这些不影响"内容在不在"，但影响"交付的是不是用户要的那一版"：--bgm 传了
    # 却没混进 BGM、--loudness 传了却没过响度，都属于必须让人先看见的差异。
    _flagged = []
    for _k, _label in (("bgm_mix_failed", "BGM 混音失败（成片为纯人声）"),
                       ("bgm_missing_file", "--bgm 文件不存在，成片未混 BGM"),
                       ("loudness_norm_failed", "响度归一化失败（响度未达标）")):
        if _deg.get(_k):
            _flagged.append(_label)
    # segments_dropped 是 pipeline 写下的被剔除 sid 列表（不是计数）
    _dropped = _deg.get("segments_dropped") or []
    if isinstance(_dropped, list) and _dropped:
        _flagged.append(f"{len(_dropped)} 个段落因没有任何可用音频被剔除"
                        f"（{', '.join(str(s) for s in _dropped)}）")
    # 音频比时间轴短：末尾那几秒画面有字幕没声音，是交付级差异
    _short = _deg.get("audio_shorter_than_timeline")
    if isinstance(_short, (int, float)) and _short > 0:
        _flagged.append(f"音频比时间轴终点短 {_short:.2f}s（片尾无配音）")
    if (_tm.get("status") == "degraded" or _degraded_tts or _degraded_lost
            or _flagged):
        if _degraded_tts:
            _REPORT["degraded"].append({"type": "tts_silence_fallback", "count": _degraded_tts})
        if _degraded_lost:
            _REPORT["degraded"].append({"type": "tts_lost_sentences", "count": _degraded_lost})
        for _k in ("bgm_mix_failed", "bgm_missing_file", "loudness_norm_failed"):
            if _deg.get(_k):
                _REPORT["degraded"].append({"type": _k, "count": 1})
        if isinstance(_dropped, list) and _dropped:
            _REPORT["degraded"].append({"type": "segments_dropped",
                                        "count": len(_dropped)})
        if isinstance(_short, (int, float)) and _short > 0:
            _REPORT["degraded"].append({"type": "audio_shorter_than_timeline",
                                        "count": round(_short, 2)})
        _why = "、".join(x for x in (
            f"{_degraded_tts} 句 TTS 失败并使用静音兜底" if _degraded_tts else "",
            f"{_degraded_lost} 句完全丢失（连静音占位都没生成）" if _degraded_lost else "",
        ) + tuple(_flagged) if x) or "manifest 标记为 degraded"
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
    html_cmd = [sys.executable, _script("gen_hyperframes.py"),
                "-m", manifest, "-o", os.path.join(project, "index.html"),
                "--theme", args.theme,
                "--aspect", args.aspect,
                "--fps", str(args.fps)]
    if has_images:
        html_cmd += ["--images", images_json]
    _run(html_cmd, step_name="生成 HTML")

    # 版式正确性只靠构造期约束（模板骨架 + _template 版式数据 + _contracts
    # 白名单）加渲染前那一遍人工预览：一条 Chrome 度量链实测一次 25-50s，
    # 换不来人工预览 3s 就能给出的信息。所以这里只提供 --until html 收口，
    # 不设自动检查步骤。

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
    out_mp4 = os.path.join(project, "out.mp4")
    hf_render = build_render_command(
        out_mp4, args.quality, args.fps, args.workers,
        gpu=args.gpu, command=_HF_COMMAND,
    )
    # 渲染等待上限跟着成片时长走。实测渲染耗时约为视频时长的 1.2–3 倍
    # （162s 片 190–466s，后者是抓帧超时重抓的极端），10 倍 + 10 分钟
    # 打底给出充足余量；默认 1800s 硬上限会把长片或 60fps/high quality
    # 的正常渲染误杀成"超时"。
    _render_cap = max(1800.0, float(_tm.get("total_duration") or 0) * 10 + 600)
    t0_render = time.time()
    try:
        _render_wait(hf_render, out_mp4, cwd=project, max_wait=_render_cap)
    except SystemExit:
        _REPORT["steps"].append({"name": "渲染", "seconds": round(time.time() - t0_render, 1), "ok": False})
        _write_report(_report_path())
        raise
    _REPORT["steps"].append({"name": "渲染", "seconds": round(time.time() - t0_render, 1), "ok": True})
    # 走到这里成片必已存在且非空：_render_poll_loop 的所有 return 路径都以
    # "实测 size>0"为前提（自然退出量一次，强杀路径再过 _verify_killed_render
    # 的整容器解码），失败路径一律 SystemExit。不再补一遍 isfile/getsize——
    # 本文件为此专门写了 _probe_file_size 来躲 exists→getsize 的竞态窗口。

    print(f"\n[run] 完成。成片：{out_mp4}", flush=True)
    _write_report(_report_path())
    _print_report_summary()


if __name__ == "__main__":
    main()
