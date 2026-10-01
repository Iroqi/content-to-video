"""多个脚本共用的进程与落盘基础设施，只依赖标准库。

- 落盘：`write_json_atomic` / `write_text_atomic`（.tmp → fsync → os.replace）、
  `sha256_file`。
- 进程与路径：`setup_stdio`（Windows 重定向强制 UTF-8）、`guard_not_in_skill_dir`、
  `is_inside`、`SKILL_DIR`。
- 文件清理：`remove_if_exists`（尽力删，删不掉也不吭声）。

文本切分（TTS 断句）不在这里——那是带领域规则的 `_text.py`，两者消费者
与改动时机都不同。
"""
import hashlib
import json
import os
import sys

# 技能目录（scripts/ 的上一级）：制作产物一律不得落在这里——混进技能目录会污染
# 仓库、多次制作串台。三个入口（pipeline / gen_hyperframes / run）共用这一处判定。
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 原子写────────────────────────────────────────────────────────
# 制作产物（timing_manifest / index.html / production_report）被 Ctrl-C 或断电
# 打断在写到一半时会留下截断文件：manifest 下次 --resume 直接崩在 json.load，
# index.html 让 render 报莫名其妙的语法错——堆栈都不指向"上次中断了，重跑就好"。
# 先写 .tmp 再 replace，要么完整要么不存在；三处产物共用这一个实现。
def _atomic_replace(path, write_fn):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = str(path) + ".tmp"
    try:
        write_fn(tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data, indent=2):
    """原子写 JSON：写 <path>.tmp → fsync → os.replace 覆盖。"""
    def _write(tmp):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
    _atomic_replace(path, _write)


def write_text_atomic(path, text):
    """原子写文本（HTML 等）：写 <path>.tmp → fsync → os.replace 覆盖。"""
    def _write(tmp):
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    _atomic_replace(path, _write)


def sha256_file(path):
    """文件内容的 sha256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def remove_if_exists(path):
    """尽力删文件（失败清理用，删不掉也不吭声）。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def setup_stdio():
    """stdout/stderr 强制 UTF-8 输出（errors=replace），入口脚本 main() 第一行调用。

    Windows 下 stdout 被重定向/进管道时，Python 按 locale 编码写流（中文系统
    cp936）——pipeline 把用户稿件原文打进 stdout，稿件含 emoji 或任何该编码
    表示不了的字符时，print 到一半裸栈 UnicodeEncodeError，而此时 TTS 已经烧
    掉一半额度。交互控制台因 PEP 528 本就是 UTF-8 不受影响；测试环境替换过
    的假流没有 reconfigure 方法时静默跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _norm_path(path):
    try:
        return os.path.realpath(path)
    except OSError:
        # realpath 在 Windows 上会因超长路径/ELOOP 抛错。不能直接 return False：
        # guard_not_in_skill_dir 把 False 读成"不在技能目录里"，是**放行方向**的
        # 失败。退回 abspath——不归一软链接，但仍能解析 .. 与相对路径。
        return os.path.abspath(path)


def is_inside(child, parent):
    """child 是否位于 parent 目录内（realpath 归一化，软链接也能判对）。

    Windows 上路径大小写不敏感，而守卫的目标路径往往还不存在——realpath 对
    不存在的路径原样返回（保留输入大小写），大小写敏感的 startswith 会被
    `C:\\USERS\\...` 之类的写法绕过。normcase 在 POSIX 上是 no-op，在
    Windows 上统一小写并归一分隔符。
    """
    child_r = os.path.normcase(_norm_path(child))
    parent_r = os.path.normcase(_norm_path(parent))
    return child_r == parent_r or child_r.startswith(parent_r + os.sep)


def guard_not_in_skill_dir(*labeled_paths):
    """产物路径落在技能目录内时 fail-fast（(标签, 路径) 成对传入）。

    -o/--output 之类是相对 CWD 解析的，而文档示例命令用的正是相对路径
    （`-o audio_output`）——从技能目录照抄就会把产物建在技能目录里，正好
    踩中"不要在技能目录内生成任何文件"的禁令。这道守卫把约定变成机械
    拦截，三个写盘入口（run/pipeline/gen_hyperframes）共用。
    """
    offenders = [(label, p) for label, p in labeled_paths if is_inside(p, SKILL_DIR)]
    if not offenders:
        return
    lines = "\n".join(f"  · {label} -> {p}" for label, p in offenders)
    raise SystemExit(
        f"[guard] 制作产物不能写在技能目录内（{SKILL_DIR}）：\n{lines}\n"
        f"产物混进技能目录会污染技能仓库，也容易在多次制作之间串台。\n"
        "请 cd 到你的项目目录后重跑（用脚本绝对路径调用即可），"
        "或用 -o/--project 显式指定技能目录之外的绝对路径。")
