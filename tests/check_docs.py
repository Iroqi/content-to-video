#!/usr/bin/env python3
"""维护者工具：检查文档里的「章节」交叉引用是否真实存在（只依赖标准库）。

形如 `SKILL.md「环境」`、`references/writing.md「字段规范」` 的引用，目标文件里
必须有一个标题（# 开头的行）包含该章节名；另外扫描 scripts/*.py 与 templates/*
里的同类引用（references 前缀可省略，裸文件名按 references/ → 根目录解析）。
改标题、瘦身 SKILL.md 之后跑一次，能抓住"引用了已经不存在的章节"。

术语表锁：别称清单**内联**在这里（曾用 references/glossary.md 登记，术语表
文件已随"各文档一致化"删除——一致化的成果就是正文只用规范词，锁只保留防
回潮的扫描，不保留第二份文档）。别称出现在正文里就是术语漂移，一义一词
靠这个锁住。

顺带量一次 SKILL.md 的行数：它是每次调用必然整份进上下文的那一份，超出上限
说明细节又爬回主文档了，该下沉到 references/。

还有一道「官方门禁噪声表对账」：`_render_backend.GATE_NOISE_CODES` 是判读的真源，
`references/rendering.md` 是给人看的那份复制品。复制品会漂——代码登记了、文档没写，
读者就以为那是新错误；文档写了、代码漏了，那条会被当成 error 拦下来。两边逐码对账。

用法：python tests/check_docs.py          # 有悬空引用时退出码 1
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = re.compile(
    r"(SKILL\.md|references/[\w.]+\.md|[\w.]+\.md)`?"
    r"\s*的?\s*[（(]?\s*「([^」]+)」")

# references/ 的 H1 模板：`第 N 步 · 主题`（N ∈ 1..4，步骤文档都带这一步号）。
# 锁模板是为了不让下一份新文档自创「第 N 步深度参考：…」之类的第二套——步骤
# 文档必须共用同一种 H1 模板，读者一眼对得上"哪份是第几步的正文"。
H1_REF = re.compile(r"^# 第 [1-4] 步 · ")

# 术语表锁（内联，原 glossary.md 的别称列）：别称只允许出现在正文之外，其它
# 文件再用就是漂移。词边界匹配（前后都不是中文字符/字母数字）：「整页画布段」
# 不算用了别称「画布段」（后者是前者的简称，正文一律写完整形式）；单独出现
# 才报。规范词列（槽位/整页画布/导演档/句子流）是正文唯一允许的写法，
# 不需要在这里登记。
ALIASES = ("图栏", "配图框", "C 路线", "画布段", "canvas 段",
           "导演模式", "director 编排", "字幕带")


def _target_path(matched):
    """引用目标 → 仓库内路径：带前缀照用；裸文件名先试 references/ 再试根目录。"""
    matched = matched.strip("`")
    if matched.startswith("references/"):
        return os.path.join(ROOT, matched)
    if matched == "SKILL.md":
        return os.path.join(ROOT, matched)
    for base in ("references", ROOT):
        p = os.path.join(base, matched)
        if os.path.isfile(p):
            return p
    return os.path.join(ROOT, "references", matched)  # 都不存在：报"目标文件不存在"


def headings(path):
    with open(path, encoding="utf-8") as f:
        return [re.sub(r"[`*]", "", ln.lstrip("#").strip())
                for ln in f if ln.lstrip().startswith("#")]


def skill_md_lines():
    with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
        return sum(1 for _ in f)


# SKILL.md 是每次调用**必然整份进上下文**的那一份，所以它的重量直接等于起步
# 代价。细节属于 references/：路由表指过去，别在这里复述一遍。超线说明该下沉
# 细节，而不是把上限调大。
SKILL_MD_MAX_LINES = 150

# references/ 单文件的字节上限。这个数是量出来的，不是拍的：现在最大的一份是
# canvas.md 约 38KB，它按"第 3 步 · SVG"这一个主题组织（画 + 演两个连续环节，
# 读者同一人同一流程）；超线通常意味着一个文件又开始服务第二个决策时刻——该拆。
# 上限随真实内容形态校准过（36KB→44KB，第 3 步收敛后 SVG 画演合一）；它拦的是
# 注水与重新膨胀，不拦密度真实的大文件。
#
# 为什么只守字节、不守"哪句话该留"：内容臃肿是判断题，判断题写成规则就会变成
# 噪音源（实测一次全面剪除，agent 报出的21KB 候选里有过半不该剪——把"复述报错
# 文案"和"论证为什么"当成同一件事）。字节数是客观的，拦住的是"文件重新膨胀"
# 这个真实发生过的事，其余交给读文档的人判断。
REFERENCE_MAX_BYTES = 44_000


# 官方门禁噪声表的对账真源：代码里的码 → 文档里必须出现 `` `码` ``。
# 表在「官方校验命令」那一节，那一节连同噪声表一起在 motion.md（rendering.md 顶到
# 单文件上限后按主题下沉：验的是"怎么验动作"，与"该不该动"同一个决策时刻）。
NOISE_SOURCE = os.path.join("scripts", "_render_backend.py")
NOISE_DOC = os.path.join("references", "motion.md")


def noise_codes():
    """从 `_render_backend.py` 里抠出 GATE_NOISE_CODES 的键（不执行模块）。"""
    with open(os.path.join(ROOT, NOISE_SOURCE), encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^GATE_NOISE_CODES = \{(.*?)^\}", src, re.S | re.M)
    if not m:
        return None
    return set(re.findall(r'"([a-z0-9_]+)":', m.group(1)))


def noise_table_drift():
    """返回噪声表两边的差异说明（[] = 一致）。"""
    code = noise_codes()
    if code is None:
        return [(NOISE_SOURCE, "GATE_NOISE_CODES", "?", "读不到这张表")]
    with open(os.path.join(ROOT, NOISE_DOC), encoding="utf-8") as f:
        doc = f.read()
    documented = set(re.findall(r"`([a-z][a-z0-9_]{5,})`", doc))
    # 只查单向（代码 → 文档）：漏写的码在文档里根本不存在，读者会把它当新错误；
    # 反方向全是误报——文档里的反引号词还有路径名、字段名、工具名。
    return [(NOISE_SOURCE, NOISE_DOC, c, "代码登记了、文档没写")
            for c in sorted(code - documented)]


def main():
    cache, bad = {}, []
    sources = [os.path.join(ROOT, "SKILL.md")]
    sources += glob.glob(os.path.join(ROOT, "references", "*.md"))
    sources += glob.glob(os.path.join(ROOT, "scripts", "*.py"))
    sources += glob.glob(os.path.join(ROOT, "templates", "*"))
    for src in sorted(sources):
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for m in REF.finditer(text):
            target, name = _target_path(m.group(1)), re.sub(r"[`*]", "", m.group(2)).strip()
            if not os.path.isfile(target):
                bad.append((src, os.path.relpath(target, ROOT), name, "目标文件不存在"))
                continue
            hs = cache.setdefault(target, headings(target))
            if not any(name in h for h in hs):
                bad.append((src, os.path.relpath(target, ROOT), name, "没有标题包含该章节名"))
    # 术语表锁（内联 ALIASES）：别称出现在正文里就是漂移。
    # 词边界匹配（前后都不是中文字符/字母数字）：「整页画布段」不算用了别称
    # 「画布段」（后者是前者的简称，正文一律写完整形式）；单独出现才报。
    for alias in ALIASES:
        pat = re.compile(r"(?<![\w\u4e00-\u9fff])" + re.escape(alias) + r"(?![\w\u4e00-\u9fff])")
        for src in sorted(sources):
            with open(src, encoding="utf-8") as f:
                text = f.read()
            if pat.search(text):
                bad.append((src, "术语表锁", alias, "用了登记的别称，改回规范词"))
    # H1 模板锁：步骤文档的 H1 必须长成 `第 N 步 · 主题`
    for ref in sorted(glob.glob(os.path.join(ROOT, "references", "*.md"))):
        with open(ref, encoding="utf-8") as f:
            first = f.readline().strip()
        if not H1_REF.match(first):
            bad.append((ref, os.path.relpath(ref, ROOT), first,
                        "H1 不是 `第 N 步 · 主题` 模板，改标题或改检查"))
    bad += noise_table_drift()
    for src, target, name, why in bad:
        print(f"[dangling] {os.path.relpath(src, ROOT)}: {target}「{name}」 — {why}")
    n = skill_md_lines()
    fat = n > SKILL_MD_MAX_LINES
    if fat:
        print(f"[too-fat] SKILL.md {n} 行，超过 {SKILL_MD_MAX_LINES} 行上限 —— "
              "细节下沉到 references/，别在这里复述")
    fat_refs = []
    for ref in sorted(glob.glob(os.path.join(ROOT, "references", "*.md"))):
        size = os.path.getsize(ref)
        if size > REFERENCE_MAX_BYTES:
            fat_refs.append((os.path.relpath(ref, ROOT), size))
    for name, size in fat_refs:
        print(f"[too-fat] {name} {size} 字节，超过 {REFERENCE_MAX_BYTES} 上限 —— "
              "一个文件又服务了第二个决策时刻，该拆")
    print(f"\n{len(bad)} 条悬空引用")
    return 1 if (bad or fat or fat_refs) else 0


if __name__ == "__main__":
    sys.exit(main())
