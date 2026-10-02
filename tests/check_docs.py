#!/usr/bin/env python3
"""维护者工具：检查文档里的「章节」交叉引用是否真实存在（只依赖标准库）。

形如 `SKILL.md「环境」`、`references/writing.md「字段规范」` 的引用，目标文件里
必须有一个标题（# 开头的行）包含该章节名；另外扫描 scripts/*.py 里的同类引用。
改标题、瘦身 SKILL.md 之后跑一次，能抓住"引用了已经不存在的章节"。

用法：python tests/check_docs.py          # 有悬空引用时退出码 1
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = re.compile(r"(SKILL\.md|references/[\w.]+\.md)`?\s*[（(]?\s*「([^」]+)」")


def headings(path):
    with open(path, encoding="utf-8") as f:
        return [re.sub(r"[`*]", "", ln.lstrip("#").strip())
                for ln in f if ln.lstrip().startswith("#")]


def main():
    cache, bad = {}, []
    sources = [os.path.join(ROOT, "SKILL.md")]
    sources += glob.glob(os.path.join(ROOT, "references", "*.md"))
    sources += glob.glob(os.path.join(ROOT, "scripts", "*.py"))
    for src in sorted(sources):
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for m in REF.finditer(text):
            target, name = m.group(1), re.sub(r"[`*]", "", m.group(2)).strip()
            tpath = os.path.join(ROOT, target)
            if not os.path.isfile(tpath):
                bad.append((src, target, name, "目标文件不存在"))
                continue
            hs = cache.setdefault(tpath, headings(tpath))
            if not any(name in h for h in hs):
                bad.append((src, target, name, "没有标题包含该章节名"))
    for src, target, name, why in bad:
        print(f"[dangling] {os.path.relpath(src, ROOT)}: {target}「{name}」 — {why}")
    print(f"\n{len(bad)} 条悬空引用")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
