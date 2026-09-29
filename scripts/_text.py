"""文本切分：把稿件切成 TTS 句。

朗读层：`split_sentences` 按终止标点断句（中文 。！？；、ASCII .!? 带边界
守卫），供 pipeline / build_from_structured 逐句合成。

字幕不再有"显示层切行"这一步：verse 把整句渲染成一条静态行、由 CSS 自然折行，
cue 数组只带时间与段内句序（见 html_renderer._build_subtitle_cues）。断句只认
终止标点，句间停顿由 `--gap` 决定。本模块不依赖任何其他模块，不碰磁盘、进程与
网络。
"""
import re

# 中文终止符：。！？＋中文分号＋ASCII 分号＋换行（保持稳定的断句行为）。
# ASCII 的 .!? 由下方扫描逻辑带边界守卫地补充（见 split_sentences）。
_CN_TERMINATORS = "。！？\uFF1B;\n"

# 终止符后可并入当前句的收尾字符：连发标点（？！/。。——第二枚若另起片段
# 会因"游离标点"过滤整枚丢失）与闭引号/括号（” 被劈进下句句首会让字幕挂
# 孤立引号、TTS 引号配对错乱）。只收**闭**引号：开引号 “ 永远属于下一句。
_CN_TRAILERS = "。！？；”」』）】》'"

# 常见英文缩写词尾：句点即使后面跟着空白也不视为句子结束。全小写比对；
# 含内部点的形式（e.g / i.e / u.s）。维护原则：漏收一个缩写只是"少切一刀"
# （句子偏长，念稿喘不过气、build_from_structured 会打超长句 warn）；误收一个
# 普通词会把完整句子劈成两半。
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
