#!/usr/bin/env python3
"""维护者工具：`subprocess` 读子进程输出时必须显式指定编码（只依赖标准库）。

**这条检查拦的是一个只在 Windows 上现形的失败。**

`subprocess.run(..., text=True)` 不带 `encoding=` 时，Python 按**本机 locale 编码**
解码子进程的字节流：中文 Windows 是 cp936，CI 的 Windows runner 是 cp1252。
而我们读的三样东西——Node 的 stdout、ffmpeg 的 stderr、Python 子脚本的输出——
**一律是 UTF-8**。于是只要输出里有一个非 ASCII 字符（中文路径、中文报错、
中文字幕），解码就抛 `UnicodeDecodeError`。

实测（CI Windows 全红，Linux 全绿）：

    File ".../subprocess.py", line 1614, in _readerthread
        buffer.append(fh.read())
    UnicodeDecodeError: 'charmap' codec can't decode byte 0x8d in position 146

更阴的是**失败现场会变形**：异常发生在子进程读取线程里，`r.stdout` 拿到的是
`None`，于是调用方的报错变成 `TypeError: the JSON object must be str, bytes
or bytearray, not NoneType`——指向的是"解析 JSON 失败"，与真正的病因
（编码）隔了三层。纯 ASCII 的用例恰好躲过，同一文件里的中文用例全挂，
读起来像是"就那几条用例有问题"。

**因此**：凡是 `text=True`（或 `universal_newlines=True`）的调用，一律显式写
`encoding="utf-8", errors="replace"`。这与 `scripts/_audio.py`、
`scripts/_speech.py`、`scripts/perceptual.py` 早已采用的口径一致——那几处是
踩过之后补上的，这里是把同一条规矩补齐到全仓，而不是发明新规矩。

`errors="replace"` 而不是 `errors="strict"`：解码失败不该让整条链路裸栈退出，
坏字节替成 U+FFFD 已经足够让人看见"这里有非 ASCII 输出"。

用法：python tests/check_subprocess_encoding.py    # 有裸 text=True 时退出码 1
"""
import glob
import io
import os
import re
import sys
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# subprocess.run / Popen / check_output / call 的调用头
_CALL_RE = re.compile(r"subprocess\.(run|Popen|check_output|call)\s*\(")
# 文本模式的两条写法
_TEXT_RE = re.compile(r"\b(text|universal_newlines)\s*=\s*True\b")
# 显式编码
_ENC_RE = re.compile(r"\bencoding\s*=")

# 行内豁免标记：这一行**故意**留着裸 `text=True`（反例素材），检查器放过。
# 测试文件里必须用它标出"这段是给检查器吃的"，否则素材本身就成了违规。
SKIP_MARK = "# ctv-allow-bare-text"

# 不扫的目录
SKIP_DIRS = {".git", "__pycache__", "node_modules", "vendor", ".venv"}

# 本文件自己写着 `text=True` 的**说明文字**（不是真调用），扫到它是自找麻烦。
SELF = os.path.basename(__file__)


def _strip_strings(src):
    """把字符串字面量挖掉（换成长度相同的空白），只留真代码。

    为什么必须挖：测试文件里存着"反例素材"——一段长得像 `subprocess.run(...)`
    的**字符串**，用来喂给检查器证明它抓得到。不挖的话，检查器把素材当成了
    真调用，于是"自证能抓"的那条用例永远红。

    用 `tokenize` 而不是正则：正则分不清 `'don\'t'` 里的转义，也分不清三引号
    跨行；tokenize 是解释器自己的口径。挖的时候按 token 的**精确行列区间**把
    源文本涂白（不是拼字符串），行号与列号才都对得上。

    两处踩过的坑，改的时候别再踩回去：

    - tokenize 的行号是 **1-based**，`lines` / `offsets` 是 **0-based**。曾经直接
      拿 `r` 当索引，每个字符串都涂到了**下一行**：素材没涂白（误报），下一行的
      真调用反倒被抹掉 `text=True`（漏报——Windows 上的真违规就这么被静默放过）。
      上界写成 `min(r1, len(lines)-1)` 还会整个漏掉最后一行。
    - Python 3.12 起 f-string 不再是单个 STRING token，而是
      FSTRING_START / MIDDLE / END 三段。只看 STRING 的话，3.9/3.11 涂白、
      3.12/3.13/3.14 不涂白，同一份代码在 CI 矩阵里三个答案。这里把
      FSTRING_START→FSTRING_END 整段当一个字符串涂白，对齐旧版口径。
    """
    lines = src.splitlines(keepends=True)
    offsets = []
    pos = 0
    for l in lines:
        offsets.append(pos)
        pos += len(l)
    offsets.append(pos)          # 末行之后的位置
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # 片段代码（测试里喂进来的）不一定是完整模块：退回正则粗略挖一遍
        return re.sub(r"'[^'\n]*'|\"[^\"\n]*\"",
                      lambda m: " " * len(m.group(0)), src)
    # 收集要涂白的区间。STRING 一整块；3.12+ 的 f-string 由 START/MIDDLE/END
    # 三段组成，按 START→END 的整段收，行为与 3.11 的单个 STRING 一致。
    spans = []
    f_start = None
    for tok in toks:
        name = tokenize.tok_name.get(tok.type, "")
        if tok.type == tokenize.STRING:
            spans.append((tok.start, tok.end))
        elif name == "FSTRING_START":
            f_start = tok.start
        elif name == "FSTRING_END" and f_start is not None:
            spans.append((f_start, tok.end))
            f_start = None

    chars = list(src)
    for (r0, c0), (r1, c1) in spans:
        for row in range(r0, r1 + 1):
            idx = row - 1                 # 1-based 行号 → 0-based 下标
            if idx >= len(lines):
                break
            base = offsets[idx]
            start = c0 if row == r0 else 0
            end = c1 if row == r1 else len(lines[idx])
            for i in range(start, min(end, len(lines[idx]))):
                if chars[base + i] != "\n":
                    chars[base + i] = " "
    return "".join(chars)


def _sources():
    out = []
    for pat in ("scripts/**/*.py", "tests/**/*.py", "*.py"):
        for path in glob.glob(os.path.join(ROOT, pat), recursive=True):
            rel = os.path.relpath(path, ROOT)
            if any(part in SKIP_DIRS for part in rel.split(os.sep)):
                continue
            if os.path.basename(path) == SELF:
                continue
            out.append(path)
    return sorted(set(out))


def _call_text(path, src, honor_marks=True):
    """这个文件里所有"文本模式但没写 encoding"的子进程调用。

    `honor_marks=False` 时连豁免标记也不认——检查器自己的用例要用它证明
    "抓得到"：素材里带着标记时是**故意**的反例，不带标记时是真的漏写。

    扫之前先挖掉字符串字面量（见 `_strip_strings`）：写在字符串里的
    `subprocess.run(...)` 是**素材**，不是调用。
    """
    bad = []
    src = _strip_strings(src)
    for m in _CALL_RE.finditer(src):
        # 取到这一处调用的右括号（按括号配平，跳过字符串里的括号）
        depth = 0
        end = None
        in_str = None
        i = m.end() - 1
        while i < len(src):
            ch = src[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch == "#":
                # 注释里的引号（`don't`、示例片段）不该开字符串状态：跨行书写的
                # 调用参数里只要有一行带这种引号，右括号就会被吞掉 → 漏报。
                # 真字符串已在 _strip_strings 里涂白，这里遇 # 一律跳到行尾。
                while i < len(src) and src[i] != "\n":
                    i += 1
                continue
            elif ch in "\"'":
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            continue
        call = src[m.start():end + 1]
        if not _TEXT_RE.search(call):
            continue
        if _ENC_RE.search(call):
            continue
        # 这一行（含调用跨越的多行）带着豁免标记 = 故意写的反例素材
        if honor_marks:
            span = src.count("\n", 0, m.start()) + 1
            span_end = src.count("\n", 0, end) + 1
            if any(SKIP_MARK in ln
                   for ln in src.splitlines()[span - 1:span_end]):
                continue
        line = src.count("\n", 0, m.start()) + 1
        bad.append((path, line, " ".join(call.split())[:90]))
    return bad


def main():
    bad = []
    for path in _sources():
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for p, line, snippet in _call_text(path, src):
            bad.append((p, line, snippet))
    for path, line, snippet in bad:
        print(f"[encoding] {os.path.relpath(path, ROOT)}:{line} —— "
              f"text=True 却没有 encoding=：{snippet}\n"
              f"             改成 encoding=\"utf-8\", errors=\"replace\""
              f"（Windows 上 locale 是 cp936/cp1252，UTF-8 输出会直接抛 "
              f"UnicodeDecodeError）")
    print(f"\n{len(bad)} 处裸 text=True")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
