#!/usr/bin/env python3
"""手绘 SVG 配图的静态自查（只依赖标准库）。

管线对槽位版式的 SVG 只校验"文件存在"，对整页画布只校验比例与 px 字号。
`references/image_options.md` 里那几条"没有门禁替你查"的硬约束，这里补一道
**尽力而为**的静态检查——它能抓明显的错，抓不到的（文字实际溢出、数据图比例
是否画对）仍然要自己看图。

用法：
    python scripts/check_svg.py images/seg1.svg images/seg2.svg
    python scripts/check_svg.py images/                       # 目录：检查其中全部 .svg
    python scripts/check_svg.py images/seg3.svg --layout canvas --aspect landscape
    python scripts/check_svg.py images/ --theme cream

检查项（error 让退出码为 1，warn 只提示）：
  - 根节点：纯数字 width/height 或 viewBox；槽位版式必须 4:3，整页画布必须与
    画幅等比（竖屏 1080×1440、横屏 1920×1080）                          [error]
  - 安全：<script>、on* 事件属性、<foreignObject>、外链资源（http/https）、
    DOCTYPE/ENTITY                                                      [error]
  - 字号：按"画布宽 ÷ SVG 宽"折算后的最终 px 低于 26px                   [warn]
    写成 em/%/class 读不出绝对值的文字数量                              [warn]
  - 对比度：文字用字面 hex 填充时，对页面底色算 WCAG 对比度；低于 3:1 为
    error，低于 4.5:1 为 warn；背景深蓝族（#0c1320/#16233a/#1a2536）当文字
    色为 error。槽位版式才查（整页画布常自带底板，底色未知，只提示）
  - 透明度：文字带 opacity / fill-opacity < 0.8                          [warn]
  - 槽位版式铺满 viewBox 的背景 <rect>（应不铺满幅底）                   [warn]
  - 含中文却没有任何 font-family（<img> 载入读不到页面字体）             [warn]
  - 整页画布：字面 x 坐标落进安全边距（竖屏 50px / 横屏 96px）           [warn]
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

from _theme import (contrast_ratio, css_color_to_hex, list_theme_names,
                    theme_bg_stops, DEFAULT_THEME)  # WCAG 数学与 hex 归一化不在这里重抄

SLOT_W, SLOT_H = 980, 735
CANVAS = {"portrait": (1080, 1440), "landscape": (1920, 1080)}
SAFE_MARGIN = {"portrait": 50, "landscape": 96}
MIN_PX = 26
RATIO_TOL = 0.01
BG_FAMILY = {"#0c1320", "#16233a", "#1a2536"}
# 整页画布的文字真正落在主题背景渐变的中段（45% 位），底色从 _theme 注册表
# 派生而不是抄一份字面量——上一版手抄的 dark 底色是旧主题遗留，与真实页底
# 已漂移，对比度门禁一直在拿不存在的颜色当基准。
PAGE_BG = {t: theme_bg_stops(t)[1] for t in list_theme_names()}

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CJK = re.compile(r"[\u3400-\u9fff]")
_NUM = re.compile(r"^\s*([-+]?\d*\.?\d+)\s*(px)?\s*$")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _style_map(el):
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _prop(el, name):
    """属性优先取 style，其次取同名表现属性。"""
    sm = _style_map(el)
    return sm.get(name, el.get(name))


def _parse_num(v):
    if v is None:
        return None
    m = _NUM.match(str(v))
    return float(m.group(1)) if m else None


def _root_size(root):
    """返回 (宽, 高, 说明)；读不出纯数字尺寸则宽高为 None。"""
    w, h = _parse_num(root.get("width")), _parse_num(root.get("height"))
    vb = root.get("viewBox")
    vbw = vbh = None
    if vb:
        parts = re.split(r"[,\s]+", vb.strip())
        if len(parts) == 4:
            try:
                vbw, vbh = float(parts[2]), float(parts[3])
            except ValueError:
                pass
    if w and h:
        if vbw and vbh and abs(w / h - vbw / vbh) > RATIO_TOL:
            return w, h, f"width/height 比例 {w / h:.3f} 与 viewBox 比例 {vbw / vbh:.3f} 不一致"
        return w, h, ""
    if vbw and vbh:
        return vbw, vbh, ""
    return None, None, ""


def check_file(path, layout, aspect, theme):
    errors, warns = [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return [f"读不了文件: {e}"], []

    # 先拦实体声明再解析，避免实体膨胀；图库素材极少带 DOCTYPE
    if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        return ["含 DOCTYPE/ENTITY 声明，拒绝解析（实体膨胀风险）；请删掉这些声明"], []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return [f"XML 解析失败: {e}"], []
    if _local(root.tag) != "svg":
        return ["根节点不是 <svg>"], []

    # ── 根节点几何 ──
    w, h, note = _root_size(root)
    if note:
        warns.append(note)
    if not w or not h:
        errors.append("根标签读不出纯数字 width/height 或 viewBox（% / em 不算），没法验比例")
        scale = None
    else:
        if layout == "canvas":
            cw, ch_ = CANVAS[aspect]
            target, label = cw / ch_, f"{cw}×{ch_}（{aspect} 整页画布）"
        else:
            cw, ch_ = SLOT_W, SLOT_H
            target, label = 4 / 3, "4:3（槽位版式，建议 980×735）"
        if abs(w / h - target) / target > RATIO_TOL:
            errors.append(f"比例 {w:g}×{h:g}（{w / h:.3f}）与目标 {label} 不等比；"
                          f"cover 会裁掉贴边内容，请改根节点尺寸后重排，别缩内容凑比例")
        scale = cw / w

    # ── 遍历元素 ──
    text_nodes = 0
    unknown_size = 0
    min_eff = None
    has_font_family = False
    has_cjk = False
    parent = {c: p for p in root.iter() for c in p}

    def inherited(el, name):
        cur = el
        while cur is not None:
            v = _prop(cur, name)
            if v is not None:
                return v
            cur = parent.get(cur)
        return None

    def has_transform_ancestor(el):
        cur = el
        while cur is not None:
            if cur.get("transform"):
                return True
            cur = parent.get(cur)
        return False

    vb_w, vb_h = w, h
    for el in root.iter():
        tag = _local(el.tag)

        if tag == "script":
            errors.append("含 <script>（内联后会在页面里执行）")
        if tag == "foreignObject":
            errors.append("含 <foreignObject>（渲染不可控，且可嵌入 HTML）")
        for k, v in el.attrib.items():
            lk = _local(k).lower()
            if lk.startswith("on"):
                errors.append(f"<{tag}> 带事件属性 {lk}")
            if lk == "href" and v.strip().lower().startswith(("http://", "https://", "//")):
                errors.append(f"<{tag}> 引用外链资源 {v[:60]}")
        if tag == "style" and el.text and re.search(r"@import|url\(\s*['\"]?https?:", el.text, re.I):
            errors.append("<style> 里有 @import 或外链 url()")
        if el.get("style") and re.search(r"url\(\s*['\"]?https?:", el.get("style"), re.I):
            errors.append(f"<{tag}> 的 style 里有外链 url()")

        if tag == "text":
            text_nodes += 1
            content = "".join(el.itertext())
            if _CJK.search(content):
                has_cjk = True

            # 字号
            fs = inherited(el, "font-size")
            px = _parse_num(fs) if fs else None
            if px is None:
                unknown_size += 1
            elif scale is not None:
                eff = px * scale
                min_eff = eff if min_eff is None else min(min_eff, eff)

            # 颜色与对比度（槽位版式才有确定的页面底色）
            fill = inherited(el, "fill")
            fh = css_color_to_hex(fill) if fill and _HEX.match(fill.strip()) else None
            if fh and layout == "slot":
                if fh in BG_FAMILY:
                    errors.append(f"文字 {content.strip()[:12]!r} 用了背景色族 {fh} 当填充色（瞎字）")
                else:
                    c = contrast_ratio(fh, PAGE_BG[theme])
                    if c < 3:
                        errors.append(f"文字 {content.strip()[:12]!r} 填充 {fh} 对 {theme} 页底对比度仅 {c:.1f}:1（<3:1）")
                    elif c < 4.5:
                        warns.append(f"文字 {content.strip()[:12]!r} 填充 {fh} 对 {theme} 页底对比度 {c:.1f}:1（<4.5:1），"
                                     f"还会被 accent 辉光再吃一档")
            op = inherited(el, "opacity")
            fop = inherited(el, "fill-opacity")
            for name, val in (("opacity", op), ("fill-opacity", fop)):
                n = _parse_num(val) if val else None
                if n is not None and n < 0.8:
                    warns.append(f"文字 {content.strip()[:12]!r} 带 {name}={n:g}，会真实吃掉对比度；换更暗的实色")

            # 整页画布安全边距（只看字面 x、且祖先无 transform 时才可信）
            if layout == "canvas" and scale is not None and not has_transform_ancestor(el):
                xv = _parse_num(el.get("x"))
                if xv is not None:
                    anchor = inherited(el, "text-anchor") or "start"
                    m = SAFE_MARGIN[aspect]
                    cw = CANVAS[aspect][0]
                    px_x = xv * scale
                    if anchor == "start" and px_x < m:
                        warns.append(f"文字 {content.strip()[:12]!r} 起点 x≈{px_x:.0f}px 进了左安全边距（{m}px）")
                    if anchor == "end" and px_x > cw - m:
                        warns.append(f"文字 {content.strip()[:12]!r} 终点 x≈{px_x:.0f}px 进了右安全边距（{cw - m}px）")

        if el.get("font-family") or _style_map(el).get("font-family"):
            has_font_family = True
        if tag == "style" and el.text and "font-family" in el.text:
            has_font_family = True

        # 满幅背景 rect
        if layout == "slot" and tag == "rect" and vb_w and vb_h:
            rw = _parse_num(el.get("width")) if not str(el.get("width", "")).endswith("%") else None
            rh = _parse_num(el.get("height")) if not str(el.get("height", "")).endswith("%") else None
            pct = str(el.get("width", "")) == "100%" and str(el.get("height", "")) == "100%"
            if pct or (rw and rh and rw >= vb_w * 0.98 and rh >= vb_h * 0.98
                       and (_parse_num(el.get("x")) or 0) <= 1 and (_parse_num(el.get("y")) or 0) <= 1):
                warns.append("疑似铺满 viewBox 的背景 <rect>：槽位 SVG 不要铺满幅底（会在页面上形成一圈色差框）")

    if min_eff is not None and min_eff < MIN_PX:
        warns.append(f"折算后最小字号约 {min_eff:.0f}px（<{MIN_PX}px 下限），两画幅缩放+压缩后读不出来")
    if unknown_size:
        warns.append(f"{unknown_size} 个 <text> 的字号不是 px 数值（em/%/class/缺省），读不出绝对值，请自己按画幅复核")
    if has_cjk and not has_font_family:
        warns.append("含中文但没有任何 font-family：<img> 载入的 SVG 读不到页面字体，请在 <style> 里自带中文无衬线字体栈")
    if layout == "canvas":
        warns.append("整页画布：图内对比度与文字是否溢出无法静态判定，请用 image_options.md「图内文字的对比度」"
                     "的内联副本 + `hyperframes check` 量一次")
    return errors, warns


def collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(".svg"):
                    files.append(os.path.join(p, name))
        else:
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser(description="手绘 SVG 配图静态自查（尽力而为，不替代人眼复核）")
    ap.add_argument("paths", nargs="+", help="SVG 文件或目录")
    ap.add_argument("--layout", choices=["slot", "canvas"], default="slot",
                    help="slot = 4:3 槽位配图（默认）；canvas = 整页画布")
    ap.add_argument("--aspect", choices=list(CANVAS), default="portrait",
                    help="画幅（仅 --layout canvas 用到，默认 portrait）")
    ap.add_argument("--theme", choices=list(PAGE_BG), default=DEFAULT_THEME,
                    help=f"按哪个主题的页底色算对比度（默认 {DEFAULT_THEME}）")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        print("[error] 没找到任何 .svg 文件", file=sys.stderr)
        return 2
    n_err = n_warn = 0
    for f in files:
        errors, warns = check_file(f, args.layout, args.aspect, args.theme)
        status = "FAIL" if errors else ("WARN" if warns else "OK")
        print(f"[{status}] {f}")
        for e in dict.fromkeys(errors):
            print(f"    error: {e}")
        for w in dict.fromkeys(warns):
            print(f"    warn:  {w}")
        n_err += len(set(errors))
        n_warn += len(set(warns))
    print(f"\n{len(files)} 个文件，{n_err} 条 error，{n_warn} 条 warn")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
