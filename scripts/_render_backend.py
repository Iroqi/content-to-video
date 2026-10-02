"""Hyperframes 渲染后端：命令解析、进程管理、成片完整性。

本模块的全部职责：
  - 找到 hyperframes CLI（项目本地 node_modules → npx），并把命令解析成当前平台
    能直接执行的形态（含 Windows .cmd/.bat shim 的注入安全处理）；
  - 启动渲染、轮询成片、在成片稳定后不再死等 Node/Chrome 退出、
    失败/中断/超时时收掉整棵进程树并丢弃半截 mp4；
  - 提前杀掉渲染进程换来的"完成"要过 ffmpeg 全解码探测。

只依赖标准库与 `_script_utils` / `_audio`（后者仅在探测成片时懒加载）。
不认识稿件、manifest、制作报告——想换渲染后端，替换本模块即可。

对外接口（run.py 实际消费）：hyperframes_command / build_render_command /
render_wait。hyperframes_spec / resolve_command / fmt_cmd 是模块内部的命令解析
链（Windows shim 处理与日志），由 tests/test_render_backend.py 直接覆盖，不对外。
进程树清理（_try_kill_process_tree）只在本模块的失败/超时/中断路径里用，不对外。
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import remove_if_exists  # noqa: E402


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
                         command: List[str]) -> List[str]:
    base = list(command)
    cmd = base + ["render", "-o", output, "--quality", quality,
                  "--fps", str(fps), "--workers", str(workers)]
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


def render_wait(cmd, out_path, cwd=None, max_wait=1800.0):
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
                         "请确认 Node.js 已安装且 npx 在 PATH 上"
                         "（或在项目目录 npm install hyperframes 使用本地 CLI）。")
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
                    _try_kill_process_tree(proc)
                    _verify_killed_render(out_path)
                    return
        else:
            stable_count = 0
            last_size, last_mtime = -1, -1.0
        time.sleep(_POLL_INTERVAL_SECONDS)
