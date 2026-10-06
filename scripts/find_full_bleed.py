#!/usr/bin/env python3
"""找出 SVG 里该删的满幅底`<rect>`，给出行号、属性和原文。

为什么有这个：门禁只会说「铺了一张满幅底板」，但一张几百行的 SVG 里定位那张
rect 很费时间——它往往在文件开头，后面还跟着一堆注释。

**只读，不改文件。** 试过 `--fix` 自动注释掉那一行，实测产出**非法 XML**：
XML 注释里不能出现 `--` 或嵌套 `<!--`，而满幅 rect 恰恰常写成 `<!-- 背景 -->
<rect .../>` 这种组合，注释包注释立刻变 `not well-formed`。改文件这一步交给人：
按报出来的行号删掉那一行，本来就是一条 `rm` 的事，不值得让工具去冒险。

判据直接调 `check_svg.is_full_bleed`，不重算一份（判据只有那一份）。

用法：
    python scripts/find_full_bleed.py <SVG 路径或目录...>
    python scripts/find_full_bleed.py --json <路径...>     # 机器可读
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_svg  # noqa: E402

EXEMPT = check_svg.FULL_BLEED_ATTR
_EXEMPT_TRUE = ("1", "true", "yes")   # 与 check_svg 同一套真值


def _locate(lines, el, used):
    """把命中的 rect 映射回源码行号。

    必须锚在**含 `<rect` 的那一行**：单行内联写法（`<svg ...><rect .../>`）落在
    根节点那行的话，报出来的原文是 `<svg ...` ——行号对、原文错，等于没报。
    定位不到就返回 None（宁可不报行号，也不报错原文）。
    """
    key = [t for t in (el.get("x"), el.get("y"), el.get("width"), el.get("height"))
           if t and t != "0"]
    for i, ln in enumerate(lines, 1):
        if i in used or "<rect" not in ln:
            continue
        if key and all(t in ln for t in key):
            return i
    for i, ln in enumerate(lines, 1):
        if i not in used and "<rect" in ln:
            return i
    return None


def scan(path):
    """→ {exempt, note, plates:[{line, attrs, source}]}"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        root = ET.fromstring(src)
    except ET.ParseError as e:
        return {"exempt": False, "note": f"根节点解析失败：{e}", "plates": []}
    exempt = str(root.get(EXEMPT, "")).strip().lower() in _EXEMPT_TRUE
    vb_w, vb_h, _ = check_svg._root_size(root)
    if not (vb_w and vb_h):
        return {"exempt": exempt,
                "note": "读不出根节点尺寸（width/height/viewBox 都不是纯数字）",
                "plates": []}

    lines = src.splitlines()
    # 已声明豁免就整份不再报——与 check_svg 那边 `if full_bleed_ok: continue` 同口径。
    # 不在这里跳过的话，豁免过的文件仍会打出一串"[满幅]"，等于把豁免开关架空。
    if exempt:
        return {"exempt": True, "note": "", "plates": []}
    used, plates = set(), []
    for el in root.iter():
        if check_svg._local(el.tag) != "rect":
            continue
        if not check_svg.is_full_bleed(el, vb_w, vb_h):
            continue
        i = _locate(lines, el, used)
        used.add(i)
        ln = lines[i - 1].strip() if i else ""
        plates.append({
            "line": i,
            "attrs": " ".join(f'{k}={el.get(k)}' for k in
                              ("x", "y", "width", "height")
                              if el.get(k) is not None) or "(属性在 <style> 或父 <g> 上)",
            # 单行内联时整条都在一行上，只截 <rect 起始那一段
            "source": (ln[ln.index("<rect"):] if "<rect" in ln else ln)[:200],
        })
    return {"exempt": exempt, "note": "", "plates": plates}


def _expand(paths):
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                if f.lower().endswith(".svg"):
                    yield os.path.join(p, f)
        elif os.path.isfile(p):
            yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    out, bad = [], 0
    for t in _expand(a.paths):
        r = scan(t)
        r["path"] = os.path.relpath(t)
        out.append(r)
        if not r["exempt"] and r["plates"]:
            bad += 1
            if a.json:
                continue
            for p in r["plates"]:
                where = f"{r['path']}:{p['line']}" if p["line"] else r["path"]
                print(f"[满幅] {where}  {p['attrs']}")
                print(f"       {p['source'][:110]}")
            print(f"       → 删掉这一行，让模板的主题渐变 / 页缘网格 / accent 氛围光透出来。")
            print(f"       → 确实要自铺满幅底（浅色页、照片感场景）："
                  f"根 <svg> 上加 {EXEMPT}=\"1\"。")

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out:
            if r["exempt"]:
                print(f"[豁免] {r['path']}：已声明 {EXEMPT}，不再报")
            elif r["note"]:
                print(f"[?]   {r['path']}：{r['note']}")
            elif not r["plates"]:
                print(f"[ok]   {r['path']}")
        if bad:
            print(f"\n{bad} 个文件有满幅底板", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())