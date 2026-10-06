#!/usr/bin/env python3
"""维护者工具：文档交叉引用 + 陈旧措辞双道检查（只依赖标准库）。

第一道「悬空章节引用」：形如 `SKILL.md「环境」`、`references/writing.md「字段规范」`
的引用，目标文件里必须有一个标题（# 开头的行）包含该章节名。扫描 SKILL.md、
references/*.md、scripts/*.py 与 remotion/src/**（references 前缀可省略，裸文件名
按 references/ → 根目录解析）。改标题、瘦身 SKILL.md 之后跑一次。

第二道「陈旧措辞」：本技能从 HTML+GSAP 后端迁到 Remotion 后，大量注释、文档、
docstring 还把**已删除的文件**当现役真源指路（"移植 html_renderer.generate_html
的 clips"、"见 gen_hyperframes.py"），另有一批已删除的参数名。手工清一遍很快就
漏，而且下次重构会重新长出来——所以做成机械拦截。

有意保留的**历史说明**（讲清"为什么是这个公式/为什么曾经不是这样"）走 ALLOW 例外，
例外必须逐条写出来，不靠模糊措辞开洞——豁免本身要可审查。

用法：python tests/check_docs.py          # 两道检查任一不过时退出码 1
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = re.compile(
    r"(SKILL\.md|references/[\w./]+\.md|[\w./]+\.md)`?"
    r"\s*的?\s*[（(]?\s*「([^」]+)」")

# ── 陈旧措辞禁用词 ─────────────────────────────────────────────────
# 每条都对应一个**确已删除**的东西：后端迁移删掉的文件、随之消失的参数名，以及
# 不再存在的运行时（GSAP）与交付物（HTML 页面）。判据不是"看着过时"，而是
# "这句话指的东西不存在了"——照着它去找会一无所获。
#
# GSAP / HTML 这一组收得比文件名那组更窄：只禁**把已移除的东西当成现役主语**的
# 说法（"GSAP 按时间求值"、"拼进 HTML 属性"）。讲命名沿革的那些不在禁列——
# `autoAlpha` 是 GSAP 专名、缓动档名照 GSAP core 抄、stagger 语义来自 GSAP，
# 它们指向的是代码里真实实现了的东西，不是找不到的东西。
BANNED = (
    "html_renderer", "gen_hyperframes", "_render_backend",
    "production_report", "index.html",
    "--allow-degraded", "--ctv-font",
    "snapshot", "hyperframes",
    # 运行时：改成 Remotion 自写求值器后，"GSAP 在跑"这句话就已经是假话
    "GSAP 补间", "GSAP 选择器", "GSAP 时间线", "GSAP 运行期", "GSAP 逐帧",
    "GSAP 会把", "GSAP 的主时间轴", "GSAP 缓存", "GSAP 根本不会",
    # 交付物：产物是 Remotion 工程，不再有一个 HTML 页面文件
    "HTML 标题层", "成片 HTML", "交付 HTML", "HTML 属性", "HTML 文本流",
    "HTML 生成", "HTML 项目目录", "HTML 预览",
    # 上面那个 HTML 页面自带的 CSS 类名与自定义属性。渲染端全走内联 style +
    # TS 常量，这些名字在仓库里一个都不存在——照着它们去找只能找到一段历史。
    "--seg-accent", "--theme", "bare-media", "seg-card", "peel-shade",
)

# 例外：文件相对路径 → 该文件内允许出现的子串（每个都要是"讲历史"而非"指现役"）。
ALLOW = {
    "references/tts_pipeline.md": ("--allow-degraded",),
    # 这个文件里必须逐字写出禁用词：它反过来测门禁本身（STALE 的边界）。
    # 用词元拼出来而不在此豁免，等于给门禁造假——真出现那行字时反而抓不到。
    "tests/test_check_tools.py": ("gen_hyperframes",),
}


def _rel(path):
    """仓库内相对路径，**一律用正斜杠**。

    这不只是为了打印好看：ALLOW 的键是手写的仓库内路径（"references/xxx.md"），
    拿 relpath 的结果直接去查表。Windows 上 relpath 给的是反斜杠，于是每一条例外
    都查不中——"讲历史"的措辞会被当成陈旧指路，门禁在 Windows 上恒红。CI 三个
    Windows 版本全挂过这个。路径是标识符，不该随平台变形态。
    """
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


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


def _sources():
    """参与扫描的文件：文档 + 脚本 + 渲染端源码（node_modules 已在 gitignore 里）。"""
    out = [os.path.join(ROOT, "SKILL.md"), os.path.join(ROOT, "README.md")]
    out += glob.glob(os.path.join(ROOT, "references", "*.md"))
    out += glob.glob(os.path.join(ROOT, "scripts", "*.py"))
    out += glob.glob(os.path.join(ROOT, "tests", "*.py"))
    out += [f for f in glob.glob(os.path.join(ROOT, "remotion", "src", "**", "*"),
                                 recursive=True) if os.path.isfile(f)]
    return [f for f in out if os.path.isfile(f)]


def _scan_stale(sources):
    # 本文件自己跳过：它必须逐字写出这些禁用词（词表与 docstring 示例），
    # 否则门禁会在自己身上开火，之后谁都不敢再加新词。
    self_path = os.path.abspath(__file__)
    bad = []
    for src in sorted(sources):
        if os.path.abspath(src) == self_path:
            continue
        rel = _rel(src)
        allowed = ALLOW.get(rel, ())
        for lineno, ln in enumerate(open(src, encoding="utf-8"), 1):
            for word in BANNED:
                if word in ln and not any(a in ln for a in allowed):
                    bad.append((rel, lineno, word, ln.strip()))
                    break
    return bad


def main():
    cache, bad = {}, []
    sources = _sources()
    for src in sorted(sources):
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for m in REF.finditer(text):
            target, name = _target_path(m.group(1)), re.sub(r"[`*]", "", m.group(2)).strip()
            if not os.path.isfile(target):
                bad.append((src, _rel(target), name, "目标文件不存在"))
                continue
            hs = cache.setdefault(target, headings(target))
            if not any(name in h for h in hs):
                bad.append((src, _rel(target), name, "没有标题包含该章节名"))
    for src, target, name, why in bad:
        print(f"[dangling] {_rel(src)}: {target}「{name}」 — {why}")
    print(f"{len(bad)} 条悬空引用")

    stale = _scan_stale(sources)
    for rel, lineno, word, line in stale:
        print(f"[stale] {rel}:{lineno} 命中已删除的 {word!r} —— {line}")
    print(f"{len(stale)} 条陈旧措辞")

    return 1 if (bad or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
