"""多个脚本共用的进程与落盘基础设施，只依赖标准库。

- 落盘：`write_json_atomic` / `write_text_atomic`（唯一中转名 → fsync → os.replace）、
  `sha256_file`；读取：`read_json_file`（三种读不出来统一成带路径的 ValueError）。
- 进程与路径：`setup_stdio`（Windows 重定向强制 UTF-8）、`guard_not_in_skill_dir`、
  `is_inside`、`SKILL_DIR`。
- 文件清理：`remove_if_exists`（尽力删，删不掉也不吭声）。
- 确定性伪随机：`seeded_wobble`（同一个 key 派生第 i 个 [-1,1] 抖动，多处的
  "看似随机实则逐字节可复现"共用这一份）。

文本切分（TTS 断句）不在这里——那是带领域规则的 `_text.py`，两者消费者
与改动时机都不同。
"""
import hashlib
import json
import os
import re
import sys
import tempfile

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
    # 见 unique_tmp：中转名必须唯一（并发互踩）且落在目标同目录（要不跨文件
    # 系统 replace 失败，原子性断了）。后缀保留 .tmp：中断后残留的中转文件
    # 一眼认得出。
    tmp = unique_tmp(directory, "." + os.path.basename(str(path)) + ".", ".tmp")
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
    """原子写 JSON：写唯一中转名 → fsync → os.replace 覆盖。

    newline="\\n"：仓库 .gitattributes 钉的是 LF，而 Windows 上 open 默认把
    "\\n" 翻成 CRLF——产物会带着一套仓库里没有的行尾，diff 与 golden 比对都
    被它污染。写盘这一处统一钉死，调用方不必各自记得传。
    """
    def _write(tmp):
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
    _atomic_replace(path, _write)


def write_text_atomic(path, text):
    """原子写文本（HTML / SVG 等）：写唯一中转名 → fsync → os.replace 覆盖。"""
    def _write(tmp):
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    _atomic_replace(path, _write)


def unique_tmp(directory, prefix, suffix=".tmp"):
    """directory 下的一个**唯一**中转文件路径（已建空文件，用后自己 os.replace）。

    三类写盘的共用一步：本文件的原子写、vendor 安装、ffmpeg 变速产物。固定名
    的代价是并发互踩——两个 run 并跑同一目录（如双画幅）时，一方异常清理的
    `os.remove` 会删掉另一方正在写的同名中转，另一方随即 `os.replace` 抛
    FileNotFoundError。mkstemp 给唯一名；中转还必须落在**目标同目录**，否则
    `os.replace` 跨文件系统 errno EXDEV，原子性这条保证就断了。
    """
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
    os.close(fd)
    return tmp


def num_fmt(v, nd=3):
    """数值得写成 CSS/SVG 能吃的样子：定点 nd 位再去尾零，只留一处答案。

    三条规则各管一件事：
      - 去尾零：`1.0` → `"1"`，产物可读、diff 稳定；
      - 吃掉浮点尾巴：`0.4+0.2` 的 `0.6000000000000001` 原样进属性既难读也没更准；
      - bool 单独转 `"1"/"0"`：走 int 分支会写成 `1.000`，进 transform 是错的。

    nd 之所以是参数而不是拆成两份函数：path 的 `d` 串坐标三位小数绰绰有余
    （帧间位移早已小于 1px），而 style/transform 上的值会跨多步叠加误差，需要
    六位。让精度成为一次调用的选择，而不是再抄袭一个实现。

    >>> num_fmt(1.0)
    '1'
    >>> num_fmt(0.4 + 0.2)
    '0.6'
    >>> num_fmt(True)
    '1'
    >>> num_fmt(0.0001, 6)
    '0.0001'
    """
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-") else "0"
    return str(v)


# 纯数字串（可带指数、不带单位）的判据只有这一份：path 的畸形 `d` 校验与
# style 值"纯数字才认"用的是同一条规则，两处各声明一份就会在改判据时只改一边——
# 症状是同一份数值在一个环节被认、在另一个环节被当成字符串。
NUMERIC_ONLY = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


def sha256_file(path):
    """文件内容的 sha256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json_file(path):
    """读一份用户/上游写出的 JSON 文件，把三种读不出来统一成带路径的 ValueError。

    OSError 不是 ValueError 子类：不转的话调用方的 ``except ValueError`` 接不住，
    路径打错就抛裸栈——而路径打错是最常见的用户输入错误。

    utf-8-sig：Windows 记事本 / PowerShell `Set-Content -Encoding UTF8` 存出来
    的合法 JSON 带 BOM，纯 utf-8 解码会让 json 抛 "Unexpected UTF-8 BOM"，把一份
    没写错的稿子判成语法错误。非 UTF-8（GBK/UTF-16 无 BOM）仍然失败，但转成点名
    文件 + 给出"另存为 UTF-8"的可执行指令，而不是裸的编解码报错。
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except OSError as e:
        raise ValueError(f"无法读取 {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise ValueError(
            f"{path} 不是 UTF-8 编码的 JSON（{e}）——请另存为 UTF-8 后重试；"
            "Windows 记事本/PowerShell 保存时选 UTF-8（不带 BOM 也行）") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} 不是合法 JSON: {e}") from e
    return data


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

    祖先判定走 `commonpath` 而不是 `<parent>/` 前缀：`startswith(parent + sep)`
    在 parent 自己带尾分隔符时拼出 `//`（根目录 parent=`/`）或 `C:\\\\`，任何
    child 都不以它开头——判据在根目录上整失效，而 False 是**放行方向**（调用方
    `guard_not_in_skill_dir` / `media_verdict` 把 False 读成"没逃出去"）。
    """
    child_r = os.path.normcase(_norm_path(child))
    parent_r = os.path.normcase(_norm_path(parent))
    try:
        return os.path.commonpath([child_r, parent_r]) == parent_r
    except ValueError:
        # Windows 跨盘（C: 与 D:）没有共同祖先，commonpath 抛错：不在同一棵树
        # 里就是不在内部，不存在"也许在里面"的第三种答案。
        return False


def media_verdict(project_dir, rel):
    """素材 `rel`（相对项目根）的可用性判定：""（可用）/ "missing" / "escapes"。

    真源只此一份：`run.py` 的配图覆盖率与 `--until html` 拦截、以及
    `gen_hyperframes.validate_images_files` 的 fail-fast 都调它。此前两处各写
    一遍，run 那一份只查 `src` 不查 `poster`——覆盖率报告记"齐了"，下一步
    gen 才以"封面缺失" exit 1，而 run 的注释还写着"与 validate_images_files
    同一口径"。与 gen_hyperframes 共用 media_verdict，poster 与 src 都归这一份
    判，谁都不许再抄。
    """
    p = os.path.join(project_dir, rel)
    if not os.path.exists(p):
        return "missing"
    if not is_inside(p, project_dir):
        return "escapes"
    return ""


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


def seeded_wobble(seed_key, i):
    """seed_key 派生的第 i 个 [-1, 1] 抖动值（同一个输入重跑逐字节一致）。

    手绘抖动（canvas_kit 的 roughness）与 Ken Burns 的取景方向（html_renderer
    的 imageEntrance.drift）都要"看起来随机、实则可复现"这一件事——渲染期的确定性
    契约禁的是墙钟与未播种随机，所以要从同一个 seed 出一批抖动时只能用这一类
    无状态派生。

    为什么是 sha256 而不是 random.Random(seed)：后者要持一个实例，"第 i 个值"
    取决于前面取过几个，调用顺序一改动结果就变；sha256(f"{key}:{i}") 无状态、
    逐点独立，改一个元素不影响别的元素的抖动。这里连 random 模块都不 import，
    天然满足那条契约。
    """
    h = hashlib.sha256("{}:{}".format(seed_key, i).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / float(1 << 64) * 2.0 - 1.0
