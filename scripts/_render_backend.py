"""Hyperframes 渲染后端：命令解析、进程管理、成片完整性。

本模块的全部职责：
  - 用 npx 启动钉固的 hyperframes CLI，并把命令解析成当前平台
    能直接执行的形态（含 Windows .cmd/.bat shim 的注入安全处理）；
  - 启动渲染、轮询成片、在成片稳定后不再死等 Node/Chrome 退出、
    失败/中断/超时时收掉整棵进程树并丢弃半截 mp4；
  - 提前杀掉渲染进程换来的"完成"要过 ffmpeg 全解码探测。

只依赖标准库与 `_script_utils` / `_audio`（后者仅在探测成片时懒加载）。
不认识稿件、manifest、制作报告——想换渲染后端，替换本模块即可。

对外接口（run.py 实际消费）：hyperframes_command / build_render_command /
render_wait / probe_video_duration / snapshot_frames。hyperframes_spec / resolve_command / fmt_cmd 是模块内部的命令解析
链（Windows shim 处理与日志），由 tests/test_render_backend.py 直接覆盖，不对外。
进程树清理（_try_kill_process_tree）只在本模块的失败/超时/中断路径里用，不对外。
成片容器固定 mp4（由 -o 后缀决定），透明底/mov/webm 已从本技能移除。
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import remove_if_exists  # noqa: E402


DEFAULT_HYPERFRAMES_SPEC = "hyperframes@0.8.185"


def hyperframes_spec() -> str:
    return (os.environ.get("CTV_HYPERFRAMES_PACKAGE", "").strip()
            or DEFAULT_HYPERFRAMES_SPEC)


def hyperframes_command(project: Optional[str] = None) -> list[str]:
    # 版本相关门禁只对 DEFAULT_HYPERFRAMES_SPEC 验证过；始终交给 npx 按 spec 解析。
    npx = shutil.which("npx")
    return [npx or "npx", "--yes", hyperframes_spec()]


# Hyperframes CLI 的 package.json 写着 `"engines": {"node": ">=22"}`。低于它的
# Node 不会在 run.py 这一侧报"版本不够"——它只看到 npx/CLI 以各种形态失败：
# npm 的 Unsupported engine、CLI 的语法错误、或者卡在 Chrome 启动上。版本是
# **渲染前就已知的前提**，不是渲染失败的产物，所以开跑前问一次。
MIN_NODE_MAJOR = 22


def node_major_version():
    """本机 `node` 的主版本号；没装 node、或输出读不懂时返回 None。

    返回 None 一律按"查不到"处理（调用方只 warn）：PATH 上没有 node 时后面
    的 Popen 本来就会报"请确认 Node.js 已安装"，这里再拦一次只是把同一件事
    说两遍，还会把"node 不在 PATH 但有别的 node"这类环境误杀。
    """
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    # node 恒以 `v22.13.0` 起头；nvm/自定义构建偶有前缀噪声，所以只搜第一个
    # 三段式版本号，不要求它顶行。
    m = re.search(r"v?(\d+)\.\d+\.\d+", out)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def node_version_gate():
    """渲染前的 Node 版本闸，返回 (判定, 说给人听的话)。

    判定为 "ok"（够，或查不到且无需多说）/ "warn"（查不到，交给旧路径报错）/
    "block"（明确不够，必须拦下）。只有"明确不够"才阻断：拿版本当门禁的前提
    是量到了版本，没量到就当门禁等于拿未知当已知。
    """
    major = node_major_version()
    if major is None:
        return ("warn",
                f"查不到 Node 版本（`node --version` 不可用）：Hyperframes 要求 Node "
                f"≥ {MIN_NODE_MAJOR}，版本不够会在 CLI 侧以难懂的形态失败。")
    if major < MIN_NODE_MAJOR:
        return ("block",
                f"本机 Node 是 v{major}，Hyperframes CLI 要求 Node ≥ {MIN_NODE_MAJOR}"
                f"（它的 package.json 里 engines 写死这一条）。旧版 Node 上渲染通常"
                f"不在这一步失败，而是表现为 npx 报 Unsupported engine、CLI 报语法"
                f"错误、或卡在 Chrome 启动上——先看 `hyperframes doctor` 确认依赖，"
                f"再决定升 Node 还是钉一个兼容的 CLI 版本"
                f"（CTV_HYPERFRAMES_PACKAGE=hyperframes@<版本>）。")
    return ("ok", "")


def _resolve_npx_shim(shim_path):
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    node_exe = os.path.join(shim_dir, "node.exe")
    npx_cli = os.path.join(shim_dir, "node_modules", "npm", "bin", "npx-cli.js")
    if os.path.isfile(node_exe) and os.path.isfile(npx_cli):
        return [node_exe, npx_cli]
    return None


def _cmdline_for_batch(exe, args):
    """给 .cmd/.bat 手工拼一条 cmd.exe /c 命令行（字符串形式）。

    **不能返回列表**：cmd 会把整条命令行再解析一遍，而 list2cmdline 只给含空格的
    参数加引号——像 `x&y` 这种不含空格却含 cmd 元字符的路径会被拆成第二条命令。
    所以每个参数都包引号、再整体包一层外层引号（cmd /c 剥首尾引号的既定规则），
    Popen 收到字符串时按原样写命令行、不再二次转义。

    残留面：引号内的 `%%` 仍会展开（只影响路径恰好含环境变量名），不构成命令
    拆分注入；含双引号的参数不支持（NTFS 本就禁止文件名含 `"`，本函数只传路径）。
    """
    def q(a):
        return '"' + str(a).replace('"', '""') + '"'
    body = " ".join([q(exe)] + [q(a) for a in args])
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    return f'{comspec} /c "{body}"'


def fmt_cmd(resolved):
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
                         command: List[str],
                         low_memory: Optional[bool] = None,
                         allow_best_effort: bool = False) -> List[str]:
    """拼一条 `hyperframes render`。恒出 mp4，不传 `--format`。

    两个旗标有默认值，别去掉：

    - **默认 `--no-best-effort`**：CLI 的 `--best-effort` 默认**开**，语义是"媒体
      缺失也出片、只给一条 warning"。它拦不住"文件在但 Chrome 侧没就绪"，而那正是
      无声成片的来源。`allow_best_effort`（run.py 的 `--allow-best-effort`）是显式
      逃生口，开了之后调用方必须把"可能无声/缺帧"记进报告。
    - **`low_memory` 三态**：`None` = 不提旗标，交给 CLI 按总内存自判；`True`/`False`
      显式传。低内存档会把 worker 钉成 1，与 `--workers N>1` 冲突时由 CLI 吃掉 N，
      拼命令这里不替它改（冲突由 run.py 在渲染前出声）。
    """
    base = list(command)
    cmd = base + ["render"]
    cmd += ["-o", output]
    cmd += ["--quality", quality,
            "--fps", str(fps), "--workers", str(workers)]
    cmd += ["--best-effort" if allow_best_effort else "--no-best-effort"]
    if low_memory is not None:
        cmd += ["--low-memory-mode" if low_memory else "--no-low-memory-mode"]
    # 参数拼齐后再解析：.cmd 兜底路径返回的是整条命令行字符串，先 resolve
    # 再加参数会把参数丢在字符串外面（等于丢进 cmd 的重解析）。
    return resolve_command(cmd)


# 官方门禁（v0.8.185）已登记的 finding 码：预期噪声静默放过、明确错误保留；
# 未登记 warning 仍会点名，避免 CLI 升级后新增检查被默认吞掉。
# ⚠ 升级 CLI 后这张表要跟着 `hyperframes check <项目> --json` 的实际输出复核；
# 与 references/rendering.md「官方校验命令」那张噪声表由 tests/check_docs.py 对账。
GATE_STAGES = ("lint", "runtime", "layout", "motion", "contrast", "craft")
GATE_NOISE_CODES = {
    # lint：本模板结构决定必然出现，不是待修问题
    "composition_file_too_large": "HTML 体量随稿子规模走",
    "nested_structure_needs_subcomposition": "每张卡一条",
    "timeline_track_too_dense": "字幕时钟轨按帧打点",
    "negative_z_index": "各层靠负 z-index 垫底，isolation 是静态扫描看不见的",
    "gsap_callback_dom_measurement": "补间回调里读 DOM 量字宽",
    "gsap_infinite_repeat": "开屏页的常驻循环",
    "gsap_repeated_fromto_without_baseline": "同一 fromTo 先后落在不同元素上",
    "overlapping_gsap_tweens": "入场与运镜在交接处碰同一属性",
    "css_transition_used": "模板兜底 CSS 的过渡，运行时由 GSAP 接管",
    "gsap_timeline_set_initial_hide": "开场前显式藏层，避免首帧闪一下",
}
# 必须当真的码（其余集合外的码也一样当真）：列在这里是为了让"哪些是静默过错"
# 有据可查，别让下一个人把它们当噪声一起放过。
GATE_STRICT_CODES = frozenset({
    "clip_media_fit", "missing_asset", "runtime_error", "clipped_text",
    "content_overlap", "font_family_without_font_face", "non_deterministic_math",
    "external_script", "missing_composition_duration", "missing_locator",
    "low_contrast",
})
GATE_FINDING_CODES = frozenset(GATE_NOISE_CODES) | GATE_STRICT_CODES


def build_check_command(project, command=None):
    """拼一条 `hyperframes check --json`（lint + runtime + layout + motion + contrast）。"""
    base = list(command) if command else hyperframes_command(project)
    return resolve_command(base + ["check", project, "--json"])


def check_verdict(report, known_codes=GATE_FINDING_CODES):
    """`hyperframes check --json` 的输出 → [(严重度, code, 说明)]，只留该当真的条目。

    判读口径：
    - **error 一律当真**；`ok: false` 但拿不到 error 也当真（不能报通过）。
    - warning：已登记的 noise 放过，**集合外的新 code 当真**——CLI 加了新检查。
    - **Lint 有 error ⇒ browser 五阶段整个没跑**（CLI 的 shouldBlockRender），
      那些 0 是"没量"不是"通过"。此时显式点一条，别让人误读成全绿。
    """
    if not isinstance(report, dict) or "ok" not in report:
        return [("error", "check_output", "check --json 的输出读不懂（不是 JSON 对象）")]
    findings = []
    for stage in GATE_STAGES:
        body = report.get(stage)
        if not isinstance(body, dict):
            continue
        for f in body.get("findings") or []:
            sev = str(f.get("severity", "")).lower()
            code = f.get("code") or "?"
            msg = f.get("message") or ""
            if sev == "error":
                findings.append(("error", code, msg))
            elif sev == "warning" and code not in known_codes:
                findings.append(("warning", "unregistered:" + code, msg))
    if not report.get("ok") and not any(s == "error" for s, _, _ in findings):
        findings.append(("error", "check_not_ok", "check 返回 ok=false，但没有可定位的 error"))
    if (report.get("lint") or {}).get("errorCount") and report.get("browserSkipped"):
        findings.append(("warning", "browser_stages_skipped",
                         "Lint 有 error，browser 五阶段整个没跑——那些 0 是"
                         "「没量」不是「通过」。先修 Lint 再判后面五阶段"))
    return findings


_EXIT_GRACE_SECONDS = 12.0
_GRACE_PER_MB = 0.15
_GRACE_CAP_SECONDS = 90.0
_POLL_INTERVAL_SECONDS = 2.0
_STABLE_SIZE_CHECKS = 2


def run_check(project, command=None, timeout=1800):
    """跑官方门禁 `hyperframes check --json`，返回 (该当真的 findings, 秒数)。

    进程在这里起、不散在 run.py：run.py 只做编排（架构上它不碰 subprocess），
    起 CLI 的地方全在本题。
    """
    args = build_check_command(project, command=command)
    t0 = time.time()
    try:
        r = subprocess.run(args, cwd=project, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"官方门禁跑不起来（{type(e).__name__}: {e}）")
    raw = (r.stdout or b"").decode("utf-8", "replace")
    # --json 把整份报告打到 stdout，前后可能夹着 CLI 的进度行：从第一个 { 起解析。
    start = raw.find("{")
    try:
        report = json.loads(raw[start:]) if start >= 0 else None
    except ValueError:
        report = None
    return check_verdict(report), time.time() - t0


def _adaptive_grace(size_bytes):
    return max(_EXIT_GRACE_SECONDS, min(_GRACE_CAP_SECONDS,
                              size_bytes / (1024.0 * 1024.0) * _GRACE_PER_MB))


def _print_render_log_tail(log_path):
    if not log_path or not os.path.isfile(log_path):
        return
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return
    print(f"[run] 渲染日志末尾（完整日志: {log_path}）:", file=sys.stderr)
    for line in lines[-25:]:
        print("    " + line.rstrip(), file=sys.stderr)


def _try_kill_process_tree(proc):
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
    remove_if_exists(out_path)


def _probe_file_size(path):
    """轮询用文件大小：不存在或被并发删改（Windows 上杀毒扫描会短暂
    锁文件，exists→getsize 之间有竞态窗口）时返回 None，绝不让
    OSError 冒进轮询循环——那会跳过 _try_kill_process_tree，留下
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
            # 取开头而非仓内通用的末尾切片：探测只解码不处理，真正的病因
            # （半截 mp4 是 `moov atom not found`）在第一行，末尾只剩
            # "Error opening input files: ..." 这种通用包装。
            f"[run] 渲染进程被提前结束时成片不完整（容器未正常收尾）：{err[:200]}\n"
            "已丢弃废片，请重跑渲染。")


def probe_video_duration(out_path, ffmpeg_path=None):
    """成片实际时长（秒）；量不出来返回 None（调用方按"没量到"处理）。

    为什么渲染完还要再量一次：成片"能播"不等于"演完了"。抓帧超时重抓、
    worker 崩、Chrome 提前退出都可能产出一支容器完好、全解码也过得去，却
    比时间轴短一大截的片子——后几段整段不在片里，而 run.py 照打"完成"。
    这一条过去没有闸：`_verify_killed_render` 只证明容器没被写坏，证明不了
    长度。量长度复用 `_audio.measure_duration`（WAV 优先那条路对 mp4 直接
    落空，自然走到 `ffmpeg -i` 的 Duration 解析），不新写一份正则。

    ffmpeg 不可用 / 解析失败一律返回 None 而不是抛：ffmpeg 是本技能的可选
    依赖（SKILL.md「环境」），探测降级与既有口径一致——降级只 warn，不阻断。
    """
    try:
        from _audio import get_ffmpeg, measure_duration
    except Exception:  # 模块不可用时也不该让"量时长"变成新的失败点
        return None
    try:
        ff = ffmpeg_path or get_ffmpeg()
        d = measure_duration(ff, out_path)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[run][warn] 成片时长无法测量（ffmpeg 不可用：{e}）",
              file=sys.stderr)
        return None
    except Exception as e:
        print(f"[run][warn] 成片时长测量失败（{type(e).__name__}: {e}）",
              file=sys.stderr)
        return None
    # measure_duration 以 0.0 表示"没量出来"（WAV 读不出 + ffmpeg 解析不到）
    return float(d) if d and d > 0 else None


# 渲染等待的默认硬上限（秒）。`run.py` 拿它当"跟着成片时长走"的地板：改这个值
# 两处必须一起动，否则 run.py 的地板还停在旧数、注释里的"默认 1800s"就成了谎话。
DEFAULT_MAX_WAIT = 1800.0


def render_wait(cmd, out_path, cwd=None, max_wait=DEFAULT_MAX_WAIT):
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

    # cmd 已由 build_render_command 在参数拼齐后 resolve 过，这里不再二次解析
    print(f"\n>>> {fmt_cmd(cmd)}", flush=True)
    print(f"[run] 渲染日志: {log_path}", file=sys.stderr)
    log_fh = None
    proc = None
    try:
        log_fh = open(log_path, "wb")
    except OSError as e:
        raise SystemExit(f"[run] 无法写渲染日志 {log_path}：{e}")
    try:
        # start_new_session 仅 POSIX 可用；Windows 上会裸抛 ValueError。
        # Windows 的进程树清理交给 _try_kill_process_tree 的 taskkill /T /F。
        popen_kwargs = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, cwd=cwd,
                                stdout=log_fh, stderr=log_fh,
                                **popen_kwargs)
        # resolve_command 找不到可执行文件时 Popen 会裸抛 FileNotFoundError；
        # 与 _run 同一口径报人话并退出。注意这条 SystemExit 必须穿过下面的
        # finally 关掉 log_fh 才能抛——Popen 失败即抛若不关句柄，Windows 上
        # 日志文件会被一直锁住。
        try:
            _render_poll_loop(proc, out_path, log_path, max_wait)
        except BaseException:
            # Ctrl-C（及任何异常退出）也必须先收进程树再抛：POSIX 上渲染进程
            # 在独立会话（start_new_session），SIGINT 不会传导给它，留下就是
            # 孤儿 Node/Chrome 继续吃 CPU；被提前掐断的半截 mp4 更不能当交付物
            # 留在盘上。poll() 已退出（渲染自身失败的路径）就不再 taskkill——
            # 对一个不存在的 PID 报"清理失败"是纯噪音。
            if proc.poll() is None:
                _try_kill_process_tree(proc)
            _discard_partial_render(out_path)
            raise
    except (FileNotFoundError, OSError) as e:
        raise SystemExit(f"[run] 无法启动渲染命令 {fmt_cmd(cmd)}：{e}\n"
                         "请确认 Node.js 已安装且 npx 在 PATH 上。")
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass


def _render_poll_loop(proc, out_path, log_path, max_wait):
    """轮询成片直到"完成或必须停止等待"；清理与 discard 由调用方兜底。

    计时一律走 `time.monotonic()`：`max_wait` 与 grace 窗口量的是**间隔**，
    墙钟被 NTP 校正、休眠唤醒或人工改过时会跳——向前一跳就把一次跑了半小时
    的正常渲染判成超时并杀掉进程树，向后一跳则永不超时。
    """
    t_start = time.monotonic()
    last_size = -1
    last_mtime = -1.0
    stable_count = 0
    grace_deadline = None
    while True:
        elapsed = time.monotonic() - t_start
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
                    grace_deadline = time.monotonic() + _adaptive_grace(size)
                elif time.monotonic() >= grace_deadline:
                    _try_kill_process_tree(proc)
                    _verify_killed_render(out_path)
                    return
        else:
            stable_count = 0
            last_size, last_mtime = -1, -1.0
        time.sleep(_POLL_INTERVAL_SECONDS)


# ── 取帧：交付前让人眼过一遍成片 ────────────────────────────────────────
# 取帧不自己抽：官方 `hyperframes snapshot` 与渲染用同一套 Chrome、同一套时间线
# seek，还会顺手拼一张 contact sheet。本模块只决定"看哪几帧"并把命令拼对。
SNAPSHOT_MAX_FRAMES = 8


def sample_snapshot_times(duration, cap=SNAPSHOT_MAX_FRAMES):
    """成片时长 → 取帧时刻表（首帧必取，其余按整片均分），返回 (时刻列表, 是否被上限截断)。

    首帧单独留在表里：成片第一秒最常见的问题（开场擦除盖住整页、字体没等到就抓帧）
    只在 t=0 附近看得见。其余时刻均分到整片，**不踩末帧**（末帧多是转场/空档）。
    """
    dur = float(duration)
    if dur <= 0:
        return [0.0], False
    n = int(cap) - 1
    times, t, truncated = [0.0], 1.0, False
    while t < dur - 1.0 and len(times) < n:
        times.append(round(t, 2))
        t += 1.0
    if t < dur - 1.0:
        # 一秒一帧装不下：改成按整片均分，否则只看得到开头几秒
        truncated = True
        step = dur / cap
        times = [round(i * step, 2) for i in range(cap)]
    return times, truncated


def _newest_mtime(directory):
    newest = None
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                m = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            newest = m if newest is None or m > newest else newest
    return newest


def snapshot_frames(project, video_path, times, out_dir, command=None):
    """在成片上取帧，返回 (产物路径或 None, 说明)。

    产物路径 = 含 contact-sheet 的目录（官方给的那张拼图是"一眼看全片"的入口）。
    失败一律返回 (None, 人话)，由调用方决定只 warn。
    """
    cmd = list(command) if command else hyperframes_command(project)
    cmd = resolve_command(cmd) if isinstance(cmd, list) else cmd
    base = [cmd] if isinstance(cmd, str) else list(cmd)
    args = base + ["snapshot", project, "-o", out_dir,
                   "--against", video_path,
                   "--at", ",".join(f"{t:g}" for t in times),
                   "--no-end"]
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    try:
        r = subprocess.run(args, cwd=project, capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"取帧命令执行失败：{type(e).__name__}: {e}"
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        return None, f"hyperframes snapshot 退出码 {r.returncode}：{err[-300:]}"
    newest = _newest_mtime(out_dir)
    if newest is None or newest < t0 - 5:
        return None, f"{out_dir} 下没有新产物（命令成功但没写出帧）"
    return os.path.abspath(out_dir), ""
