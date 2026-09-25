"""全仓共享的基础设施（从各入口脚本抽出的"只该有一份实现"的东西）。

包含三类，都不依赖 TTS / 网络 / 并发：

1. 文本处理：`split_sentences`（中文断句，build_from_structured 复用）、
   `split_subtitle_lines` / `split_subtitle_cues` / `subtitle_params_for`
   （字幕显示层切行，参数读 `_template.py` 内联版式数据的 subtitle 块）。
2. 落盘原语：`write_json_atomic` / `write_text_atomic`（先写 .tmp → fsync →
   os.replace）、`sha256_file` —— 全仓唯一实现，禁止各脚本再写一份。
3. 进程级设置：`setup_stdio`（Windows 重定向场景强制 UTF-8）、
   `guard_not_in_skill_dir`（产物不得落进技能目录的守卫）、`is_inside`。

拆成独立模块是为了让 build_from_structured 只依赖分句、不必连带 import
pipeline 的重量级依赖；路径守卫与 manifest 读写供全管线共用。
"""
import hashlib
import json
import os
import re
import sys

from _template import load_template

# 技能目录（scripts/ 的上一级）：制作产物一律不得落在这里——SKILL.md 的
# 路径约定要求产物写在用户项目目录，混进技能目录会污染仓库、多次制作串台。
# 放在共享模块是因为 pipeline / gen_hyperframes / run 三个入口都要拦同一件事，
# 各写一份必然漂移。
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 原子写（全仓唯一实现）────────────────────────────────────────
# 制作产物（timing_manifest / index.html / production_report）
# 被 Ctrl-C 或断电打断在写到一半时会留下截断文件：manifest 下次 --resume 直接
# 崩在 json.load，index.html 会让 render 报莫名其妙的语法错——堆栈都不
# 指向"上次中断了，重跑一遍就好"。先写 .tmp 再 replace，要么完整要么不存在。
# 统一到这里，避免某一处改权限/清理逻辑时其他处分叉。
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
    """文件内容的 sha256（全仓唯一实现）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
        # realpath 在 Windows 上会因超长路径/ELOOP 抛错。原来直接
        # return False 是**放行方向**的失败：guard_not_in_skill_dir 把 False
        # 读成"不在技能目录里"，媒体越界检查同理。退回 abspath——不归一软链
        # 接，但仍能解析 .. 与相对路径，比放弃判断好。
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
    拦截，四个写盘入口（run/pipeline/gen_hyperframes/gen_charts）共用。
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


# 中文终止符：。！？＋中文分号＋ASCII 分号＋换行（保持稳定的断句行为）。
# ASCII 的 .!? 由下方扫描逻辑带边界守卫地补充（见 split_sentences）。
_CN_TERMINATORS = "。！？\uFF1B;\n"

# 终止符后可并入当前句的收尾字符：连发标点（？！/。。——第二枚若另起片段
# 会因"游离标点"过滤整枚丢失）与闭引号/括号（” 被劈进下句句首会让字幕挂
# 孤立引号、TTS 引号配对错乱）。只收**闭**引号：开引号 “ 永远属于下一句。
_CN_TRAILERS = "。！？；”」』）】》'"

# 常见英文缩写词尾：句点即使后面跟着空白也不视为句子结束。全小写比对；
# 含内部点的形式（e.g / i.e / u.s）。维护原则：漏收一个缩写只是"少切一刀"
# （句子偏长、可被显示层切行兜住）；误收一个普通词会把完整句子劈成两半。
_EN_ABBREV_TAILS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "cf", "al", "fig", "no", "inc", "ltd", "co", "corp", "col", "gen",
    "sen", "rep", "rev", "hon", "univ", "dept", "est", "approx", "ave",
    "blvd", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "e.g", "i.e", "a.m", "p.m", "u.s", "u.k",
}
_EN_WORD_TAIL_RE = re.compile(r"[A-Za-z.]+$")


def _is_english_sentence_end(text, i):
    """text[i] 为 ASCII 句点时判断它是否终结一个句子。

    守卫全部朝"宁可少切、不可错切"的方向设计：
    1. 句点后不是空白/换行 → 不切（小数点 3.5、域名、文件名、
       "U.S-China" 这类连字符复合词都落在这类）；
    2. 省略号（前一个字符仍是句点）→ 不切；
    3. 点前单词命中缩写表（Mr./Dr./e.g./U.S.），或点前是单个 ASCII
       字母且再往前非字母数字（人名首字母 J.、缩写链 U.S. 的最后一个
       点）→ 不切。
    """
    n = len(text)
    j = i + 1
    if i >= 1 and text[i - 1] == ".":
        return False  # 省略号中段/尾点
    prev_ch = text[i - 1] if i >= 1 else ""
    if not prev_ch.isalnum():
        return False  # "(...)" 收尾括号点等孤立符号后不切
    if j < n:
        if not text[j].isspace():
            return False  # 小数点/URL/路径：句点后不是空白
        # 单字母尾（首字母 J. / 缩写链 U.S. 的最后一个点）
        if (i >= 2 and not text[i - 2].isalnum()
                and prev_ch.isascii() and prev_ch.isalpha()):
            return False
        # 缩写词表：取句点前连续字母/点组成的最长尾串比对
        m = _EN_WORD_TAIL_RE.search(text[max(0, i - 12):i])
        if m:
            tok = m.group().lower()
            if tok in _EN_ABBREV_TAILS or tok.lstrip(".") in _EN_ABBREV_TAILS:
                return False
        return True
    # 文本末尾的句点：已排除省略号与孤立符号，视为正常句子结束
    return True


def _join_fragment(head, tail, sep):
    """把断句片段接回前文，尽量还原原文的词间空白。

    `sep` 描述两片之间原文里的空白，三态：
      - "explicit"：本片段自带前导空白（`"Hi. Next"` 的那个空格）；
      - "inferred"：空白来自上一片段被 strip 掉的**尾部**（换行本身既是独立
        终止符又是空白，`"Multi\\n"` 切完后 `"line"` 前面什么也没留下）；
      - None：原文里两片紧挨着。
    "inferred" 只在两侧都是 ASCII 字母数字时补空格：中文稿里的换行多半只是
    排版折行，插空格会在字幕里凭空多出一个词间空隙；英文稿丢了分隔则会把
    `Multi` + `line` 念成 `Miline`。句末标点后的空格由最后一个分支兜住。
    两处接缝（短片段在 buf 内累加、buf 收尾接回上一句）必须共用本函数：
    漏掉收尾那处时 `"Welcome to the show." + "OK"` 会拼成 `"show.OK"`——
    TTS 连读、字幕也少一个空格。
    """
    if sep == "explicit":
        return head + " " + tail
    if (tail[0].isascii() and tail[0].isalnum()
            and (sep == "inferred"
                 and head[-1:].isascii() and head[-1:].isalnum()
                 or head[-1:] in ".!?;:")):
        return head + " " + tail
    return head + tail


def split_sentences(text):
    """Split script text into sentences by terminal punctuation.

    中文按 。！？（含中文分号 ；、ASCII 分号 ;、换行）切分；ASCII 的 .!?
    作为补充终止符带边界守卫地参与（. 需通过 _is_english_sentence_end，
    !? 需后随空白/文末）——中英混合稿里的英文句子不再整段粘成一个"句子"
    （单次 TTS 文本过长、韵律崩坏、45 字超长告警刷屏），而小数点、缩写、
    省略号、人名首字母均不会被误切。纯中文稿的行为保持现有断句契约。
    终止符后紧跟的连发标点与闭引号/括号（见 _CN_TRAILERS）并入当前句。
    """
    text = text.strip()
    if not text:
        return []
    parts = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in _CN_TERMINATORS:
            i += 1
            while i < n and text[i] in _CN_TRAILERS:
                i += 1
            parts.append(text[start:i])
            start = i
            continue
        if ch == ".":
            if _is_english_sentence_end(text, i):
                i += 1
                parts.append(text[start:i])
                start = i
                continue
        elif ch in "!?":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "" or nxt.isspace():
                i += 1
                parts.append(text[start:i])
                start = i
                continue
        i += 1
    if start < n:
        parts.append(text[start:])
    sentences = []
    # 上一片段（无论是否被下面的规则滤掉）是否以空白收尾
    prev_ended_ws = False
    for p in parts:
        s = p.strip()
        # 必须含至少一个 alnum 字符才是句子：纯标点片段（"。！？" 连发会被
        # _CN_TRAILERS 吸收成一枚片段）与纯 emoji 句（👍👍）对 TTS 都是
        # 空音频、对字幕是噪声，一并滤掉。句中夹带 emoji 不受影响。
        # 单字句（"大家好。好"的"好"）isalnum 为真，照常放行。
        if s and any(c.isalnum() for c in s):
            # 记录两片之间原本是否存在空白。后面的短句合并不能直接
            # `buf += s`，否则英文稿的 "Hi. Next" 会变成 "Hi.Next"，
            # 同时污染 TTS 发音和字幕文案。只保留一个分隔空格即可，
            # 不把换行/缩进原样带进 TTS。parts 是原文的连续划分，所以
            # 空白可能体现在本片段的**前导**、也可能是上一片段被 strip
            # 掉的**尾部**（换行属于后者），两种来源分开记录。
            leading_ws = bool(p[:len(p) - len(p.lstrip())])
            sep = ("explicit" if leading_ws
                   else "inferred" if prev_ended_ws else None)
            sentences.append((s, sep))
        prev_ended_ws = p != p.rstrip()
    # Merge very short fragments (< 5 chars) with the next sentence
    merged = []
    buf = ""
    # buf 首个片段前的分隔方式：收尾把 buf 接回 merged[-1] 时要用同一个
    # 边界信息（累加过程中 buf 会被覆盖，那个标志传不到收尾处）
    buf_sep = None
    for s, sep in sentences:
        if buf:
            buf = _join_fragment(buf, s, sep)
            if len(buf) >= 8:
                merged.append(buf)
                buf = ""
                buf_sep = None
        elif len(s) < 5:
            buf = s
            buf_sep = sep
        else:
            merged.append(s)
    if buf:
        if merged:
            merged[-1] = _join_fragment(merged[-1], buf, buf_sep)
        else:
            merged.append(buf)
    return merged


# 字幕二次切行（显示层）：固定宽度均衡切行。
# 断点优先级：标点 / 空格 > CJK 字符间；连续 ASCII 字母数字（英文词）保持
# 完整不劈开。每行尽量填满到 max_chars，保证等宽、且不交给 CSS 二次折行。
# 注意这仅是**显示层**排版辅助——TTS 断句仍只认终止标点（split_sentences），
# 句间停顿 --gap 按"每两句之间"插入，标点处切行不会引入额外停顿。


def _is_joiner(ch):
    """组合修饰符：ZWJ / 变体选择符 / emoji 肤色修饰符 / 组合变音符。

    它们依附前一个码点成字形，前后都不是合法断点——否则 👨‍‍👧
    会被字幕断行劈成两半（半截序列在多数字体里渲染成 tofu）。
    """
    o = ord(ch)
    return (o == 0x200D or 0xFE00 <= o <= 0xFE0F
            or 0x1F3FB <= o <= 0x1F3FF or 0x0300 <= o <= 0x036F)


def _is_break_after(text, idx):
    """能否在 text[idx] 之后断行（把 text[idx] 纳入当前行、于 idx+1 处断开）。

    断行规则：空白、非 ASCII 字符（CJK/全角宽字符）、ASCII 标点/符号 均
    可在其后断；连续 ASCII 字母/数字（英文词）仅在该词**结尾**（后一字符
    非字母数字）时才可断，从而整词不被劈开。
    """
    ch = text[idx]
    if ch.isspace():
        return True
    if _is_joiner(ch):
        return False
    nxt = text[idx + 1] if idx + 1 < len(text) else ""
    if nxt and _is_joiner(nxt):
        return False
    if not ch.isascii():
        return True
    if ch.isalnum():
        return not (nxt.isascii() and nxt.isalnum())  # 词尾才断
    return True  # 其余 ASCII 符号（标点等）可断


def _wrap_width(text, width):
    """固定宽度贪婪切行：每行尽量填满到 width，断点取窗口内最靠后的合法断点。

    保证 "".join(结果) == text（仅去掉首尾空白），不丢失任何字符（含空格），
    可直接用于需 join 校验 / 内容保真的场景。每行长度恒 <= width，杜绝整句
    交给 CSS 自然换行后溢出成多视觉行。
    """
    text = text.strip()
    if not text:
        return []
    n = len(text)
    if n <= width:
        return [text]
    lines = []
    start = 0
    while start < n:
        end = min(start + width, n)
        if end >= n:
            lines.append(text[start:n])
            break
        # 从 end 往回找最远的合法断点（优先填满，实现等宽）
        brk = -1
        for j in range(end, start, -1):
            if _is_break_after(text, j - 1):
                brk = j
                break
        if brk <= start:
            # 窗口内无断点（超长英文专名占满 width）→ 在 width 处硬切
            brk = start + width
        lines.append(text[start:brk])
        start = brk
    return lines


def split_subtitle_lines(text, max_chars, hard_cap):
    """把一句长字幕切成均衡字幕行（仅显示用）。

    固定宽度贪婪切行：每行尽量填满到 width = min(max_chars, hard_cap)，断点
    优先标点/空格，其次 CJK 字符间；连续 ASCII 字母数字（英文词）保持完整、
    不劈开；标点弱化处理。每行 <= width，杜绝整句交给 CSS 自然换行后溢出成
    多视觉行。

    Args:
        text: 单句文本（split_sentences 的一个元素）
        max_chars: 单行目标宽度（字符）；行尽量填满到此值
        hard_cap: 物理行上限（字符）；比 max_chars 小时直接收窄行宽

    Returns:
        list[str]: 1..n 行
    """
    text = text.strip()
    if not text:
        return []
    width = min(max_chars, hard_cap) if hard_cap else max_chars
    return [l for l in _wrap_width(text, width) if l]


def subtitle_params_for(aspect="vertical"):
    """按画幅给出字幕切分参数——单一权威来源。

    片内字幕切分（html_renderer.py 的 cue 构建）只从这一处取参数。数值定义在
    `_template.py` 内联版式数据的顶级 subtitle 块（JSON 定义、
    本函数只做画幅归一化与取数），改切行宽度只动模板文件。

    两画幅各有独立的 subtitle 块：竖屏 980px 物理宽/40px 字号（≈22 字/行），
    横屏左栏 613px/33px 字号（≈18 字/行）。未知画幅标识直接 KeyError 暴露。

    Returns:
        dict(max_chars=, hard_cap=, cue_max_lines=)
    """
    # portrait 归一化：竖屏 3:4 复用 vertical 家族标识（与 gen_hyperframes
    # 的归一化语义一致），CLI/调用方传 "portrait" 也拿到竖屏切行参数。
    if aspect == "portrait":
        aspect = "vertical"
    params = load_template()["subtitle"][aspect]
    return {"max_chars": params["maxChars"],
            "hard_cap": params["hardCap"],
            "cue_max_lines": params["cueMaxLines"]}


def split_subtitle_cues(text, max_chars, cue_max_lines, hard_cap):
    """把一句长字幕切成若干"cue 组"：每屏最多 cue_max_lines 行。

    机制是通用的行数分组；当前模板配置 cueMaxLines=99（verse 整句单
    cue，见 _template.py subtitle 块），实际每句恒为一组，"同屏两行 +
    调用方按字符占比切分时长"的分支只在把 cueMaxLines 调小时才会走到。
    内部先按行宽做标点均衡切行（保证超长句也能切到每行 <= max_chars，
    hard_cap 收窄行宽），再按 cue_max_lines 顺序分组。

    Returns:
        list[list[str]]: 每个元素是一屏的行列表（1..cue_max_lines 行）
    """
    lines = split_subtitle_lines(text, max_chars=max_chars, hard_cap=hard_cap)
    return [lines[i:i + cue_max_lines]
            for i in range(0, len(lines), cue_max_lines)]
