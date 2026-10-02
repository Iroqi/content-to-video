#!/usr/bin/env python3
"""维护者工具：检查文档里的「章节」交叉引用是否真实存在（只依赖标准库）。

形如 `SKILL.md「环境」`、`references/writing.md「字段规范」` 的引用，目标文件里
必须有一个标题（# 开头的行）包含该章节名；另外扫描 scripts/*.py 与 templates/*
里的同类引用（references 前缀可省略，裸文件名按 references/ → 根目录解析）。
改标题、瘦身 SKILL.md 之后跑一次，能抓住"引用了已经不存在的章节"。

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
    for src, target, name, why in bad:
        print(f"[dangling] {os.path.relpath(src, ROOT)}: {target}「{name}」 — {why}")
    print(f"\n{len(bad)} 条悬空引用")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
