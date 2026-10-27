#!/usr/bin/env python3
"""观感门禁：把「成片长什么样」变成机器能判的东西。

## 为什么需要它

本仓库原有的门禁全都在看**结构**：`lint` 看 HTML 里有没有非确定性的东西，
`Layout` 量盒子的位置，`Contrast` 算 CSS 里能读到的颜色，`check_svg` 量
SVG 源码里的坐标。这三四道加起来仍然回答不了一个最简单的问题：

> **这一页在屏幕上的那十几秒里，观众到底看到了什么？**

实测（竖屏 4 段，`--until html` 之后 `snapshot --at` 逐段取帧）出过一串
「全流程零报错、成片不能看」的画面：

- 段落没有配图时，标题与句子流之间是一整块**空画布占位**——`Layout` 认为
  一切正常（没有元素重叠、没有溢出），因为那条空带里**本来就没有元素**；
- 整页画布段只有占位框，而生成期那条 warn 说的是"旁白不会出现在画面上"，
  作者读到的却是"我确认过了"；
- 一张图在段落里 12 秒不动，观众读到的信息量 = 一张图；没有任何一道门禁
  会问"这一段到底变化了几次"。

这几条的共同点：**错误只在像素上，不在结构上**。所以这一层刻意从像素出发：
取帧 → 看帧 → 给出可复核的判词。它不替代 `check`，它是 `check` 之后
唯一还在看画面的那一道。

## 判据

四条，全部只看像素与时间轴，不看源码（源码怎么写的与屏幕上是什么无关）。
**全流程覆盖率**刻意不用：模板给每一页铺了 `.bg`/`.grid` 两层，覆盖率恒在
95% 以上，拿它判空页只会得出"每一页都有内容"的假判决（实测过）。

1. **画布稀薄**（canvas_thin，槽位版式）：**配图区域**（由插槽矩形框定，
   不靠猜）里"看得出在变化"的像素占比低于 `SLOT_INK` 就报。实测踩过：
   一张只写了一行 36px 字的 SVG 当配图，插槽 980×735 里就那一行字，
   占约 1.5%，而 `check_svg` / `lint` / `Layout` 三道全绿——图里"没有东西"
   不是这三位能看出来的。整页画布段不看这条：它的设计就是大片留白 + 少量字，
   按同一条线判会全是误报。
2. **空页**（画面主导的页：整页画布 / 图版）：这类页面的画面就是全部（图版只多
   两条窄带上的字），覆盖率低于 `EMPTY_COVERAGE` 判空页——观众在这十几秒里
   什么都没有。图版**不按槽位框**量稀薄：它的图铺满整幅，按 4:3 槽位框去量只会
   量到图中间那一块，比例失真。
3. **死帧连片**：相邻两次采样之间画面几乎不动，就是一段"死帧"。连起来超过
   `DEAD_SECONDS` 就报。1.2%（`MOTION_MIN`）是"一页十几秒只有一条运镜在爬"
   的量级（实测 Ken Burns 6%/s 的推近在 2 秒间隔上只变 0.2–0.4%），比它小
   基本等于没动。**这条才是 3b1b 差距的量化版**：视频的"视频性"= 每秒的视觉
   事件数，而原仓库对此没有任何测量。
4. **换页时序**：换页（`SWITCH_RATIO` 以上像素翻新）时刻与段落口播起点的
   偏移超过 `SWITCH_TOLERANCE` 秒，就是"声画不同步"。原仓库只有导演拍点的
   `beat_report` 能看见这件事，而它只覆盖写了 `director` 的段。

采样刻意跳过句子起点 ±0.25s：换句那一帧字幕在动，会掩盖"画面没动"。

## 与 `--until check` 的分工

`check` 判"能不能渲染"，本门禁判"渲染出来是不是观众想看的那一版"。
`check` 的 error 会挡住渲染，本门禁只在**交付前**要求人工看一遍 contact sheet：
它能指出"这里 8 秒没动"，但"这 8 秒该不该动"只能由人答。所以默认不阻断，
只有 `--strict` 才把 error 变成非零退出。

## 帧的来源

两条：`--frames`（已有 PNG 目录，CI 里可以缓存复用）或现取（调
`hyperframes snapshot`，需要 Node + Chrome）。取帧命令与口径见
`references/motion.md`「官方校验命令」——本模块只是把它的产物变成判词。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# 版式判据的唯一真源在 _segments（scripts/ 已由 run_gate 的调用方放进
# sys.path）；这里只借用"哪些版式是满幅图"这一件事，不另抄一份 ("canvas","plate")。
from _segments import FULL_BLEED_LAYOUTS, is_full_bleed  # noqa: E402

# ── 判据（全部是"成片可读性"的直接翻译，不是手感）─────────────────────
# 整页画布段的"空页"下限：低于它判空页。0.6% 相当于 1080×1440 上一块约
# 96×96 的实心块——一个图标的量级。比这还空，画面上真正什么都没有。
EMPTY_COVERAGE = 0.006
# 槽位版式配图区的"稀薄"下限：图槽里不到 2.5% 的像素与背景有差别，就等于
# 图槽里没有东西。**这个数是量出来的，不是拍的**——实测三张：
#   合格折线图（大图区 + 粗线 + 刻度）5.0%–5.8%
#   只有一行 36px 字 + 一条细线      1.7%
#   空占位框（虚线 + 一枚小牌子）     ≈1.0%
# 线画在 1.7% 与 5.0% 之间偏保守的一侧：宁可漏报"还算能看"，也不要把合格的
# 极简示意图（一个跨页箭头、一条时间轴）判成空。差值上限（CONTENT_DELTA）也要
# 跟着它看——暗图上的深色线条在 delta=10 下会整体消失，所以框内取 delta=6。
SLOT_INK = 0.025
# 图槽内判定"有墨水"的阈值：比全帧松一档（6 而不是 10）。图自带的底往往
# 与页底色接近，线又细，取 10 会把细线整条滤掉（实测同一张折线图 delta=10
# 量出 3.7%、delta=6 量出 4.9%）。
INK_DELTA = 6
# 像素被判"有内容"的阈值（与背景色的通道差）。取 10 是为了滤掉编码噪声与
# 渐变底那 1-2 级的抖动；压在它之下的字在手机上本来也读不出来。
CONTENT_DELTA = 10
# 两次采样之间"动了"的像素占比下限：低于它 → 这两帧之间没有发生任何视觉事件。
# 1.2% ≈ 一页运镜在爬（Ken Burns 6%/s 在 2 秒间隔上只有 0.2–0.4%），
# 也远高于压缩噪声。这一条同时用于"换页"判据（见下面的 SWITCH_RATIO）。
MOTION_MIN = 0.012
# 换了"大半屏"的像素占比高于它 → 这是一次换页，不是页内演进。
SWITCH_RATIO = 0.35
# 死帧判据：连续不动超过这么多秒就报。3 秒是"一句话说完还没等到任何画面
# 变化"的下限；3b1b 式的讲解在 3 秒里至少有一次视觉事件。
DEAD_SECONDS = 3.0
# 换页时刻与句子时间轴的容差：超过就是声画不同步。0.5s 是"看出来了"的量级。
SWITCH_TOLERANCE = 0.5
# 每段最少采样数（段很短时按段长均分，但至少要这么多点才判得动"死帧"）。
MIN_SAMPLES_PER_SEG = 3

# 判词的严重度。**只有** `temporal_budget` 是 warn 档：它量的是"这一页一共
# 变了多少次"，而变了多少次该不该变是创作决定（一段 25 秒的推导，画面慢慢
# 推一个参数，本来就该是低事件密度）。其余四条量的都是"这一页坏了"：图槽里
# 没东西、整页是空的、`--project` 指定的帧实际上一帧没取到、声画对不上。
# `--strict` 只让 error 档判非零退出；把它写成"有任何判词就 5"会让一份合格
# 但节奏舒缓的片子过不了 CI——那时人会做的不是改画面，是删掉 --strict。
ISSUE_ERROR = "error"
ISSUE_WARN = "warn"
ISSUE_SEVERITY = {
    "canvas_thin": ISSUE_ERROR,
    "empty_page": ISSUE_ERROR,
    "dead_seconds": ISSUE_ERROR,
    "switch_skew": ISSUE_ERROR,
    "temporal_budget": ISSUE_WARN,
    # 取帧/读帧层面的问题不是"画面坏了"，是"这一眼没看成"
    "no_frames": ISSUE_ERROR,
}


def severity(kind):
    """判词类型 → 严重度；未登记的按 error（新判据默认要人当真）。"""
    return ISSUE_SEVERITY.get(kind, ISSUE_ERROR)


def _error_issues(issues):
    return [it for it in issues if severity(it.get("kind")) == ISSUE_ERROR]


class PillowMissing(RuntimeError):
    """缺 Pillow。**不能是 SystemExit**：本模块的编排入口 `run_gate` 承诺
    "不阻断、不 sys.exit"，而 SystemExit 不是 Exception 子类，run.py 里
    `except Exception` 接不住它——成片已渲染完、报告已写一半，进程却以
    "Pillow 没装"的退出码收场，读起来像渲染失败。RuntimeError 能被现有
    `except RuntimeError` 接成一条 `{"error": ...}` 判词，走"门禁未完成"那条路。
    """


def _load_pil():
    """Pillow 是可选依赖（见 SKILL.md「环境」）。缺了就说清楚缺什么、怎么装。"""
    try:
        from PIL import Image, ImageChops  # noqa: F401
        return Image, ImageChops
    except ImportError:
        raise PillowMissing(
            "[error] 观感门禁需要 Pillow（它要吃帧的像素）：pip install pillow。"
            "本门禁是可选的一道验收，缺依赖不影响出片。") from None


def sample_times(segments):
    """按段生成采样时刻。

    每段取「起点 + 若干等分点 + 终点前一点点」：等分点保证段中段的死帧不会被
    漏掉，终点前那一点是"这一页最后一次变动之后还剩下多少静止"的证据。
    整页画布段（layout=canvas）没有字幕可读，采样更密——那里每一秒的静止
    都是纯空白。

    采样避开句子起点 ±0.25s：换句那一帧字幕在动，会掩盖"画面没动"这件事。
    """
    out = {}
    for seg in segments:
        sid = seg["id"]
        sents = seg.get("sentences") or []
        if not sents:
            continue
        t0 = float(sents[0]["start_time"])
        last = sents[-1]
        t1 = float(last["start_time"]) + float(last["duration"])
        span = max(0.0, t1 - t0)
        # 画面主导的页（整页画布 / 图版）加密：整页画布连字幕都没有，静一秒就是
        # 空一秒；图版只有一条句字幕在换，同样经不起长静止
        step = 1.5 if is_full_bleed(seg) else 2.0
        n = max(MIN_SAMPLES_PER_SEG, int(span // step) + 1)
        # 起点后让 0.6s 起来（擦除与入场补间都在这段里），末点收在终点前 0.4s
        lo = t0 + 0.6
        hi = max(lo + 0.1, t1 - 0.4)
        marks = [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]
        cues = {round(float(x["start_time"]), 2) for x in sents}
        kept = []
        for m in marks:
            if any(abs(m - c) < 0.25 for c in cues):
                m = round(m + 0.3, 2)
            if m not in kept:
                kept.append(m)
        out[sid] = kept
    return out


def _read_frame(path):
    Image, _ = _load_pil()
    return Image.open(path).convert("RGB")


def _bg_color(img):
    """页底色：取四角与四条边中位点的中位数。

    不取"众数"是因为深色主题的底是渐变，众数会被某一条色带吃掉；四角加边中
    九点取中位数，对"角落恰好有个元素"这种情况也稳。
    """
    w, h = img.size
    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
           (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2),
           (w // 2, h // 2)]
    px = [img.getpixel(p) for p in pts]
    med = tuple(sorted(c[i] for c in px)[len(px) // 2] for i in range(3))
    return med


def _mask(img, bg, delta=CONTENT_DELTA):
    """内容掩膜：与页底色差超过 delta 的像素。返回 PIL 单通道图（255=有内容）。"""
    Image, ImageChops = _load_pil()
    solid = Image.new("RGB", img.size, bg)
    d = ImageChops.difference(img, solid).convert("L")
    return d.point(lambda v: 255 if v > delta else 0)


def _ratio(mask):
    hist = mask.histogram()
    total = mask.size[0] * mask.size[1]
    return hist[255] / total if total else 0.0


def _diff_ratio(a, b, delta=CONTENT_DELTA):
    """两张帧之间"变了"的像素占比。"""
    _, ImageChops = _load_pil()
    d = ImageChops.difference(a, b).convert("L")
    m = d.point(lambda v: 255 if v > delta else 0)
    return _ratio(m)


def _issue(kind, sid, msg, **extra):
    """一条判词。`severity` 是派生量，不让调用方自己传：传了就会有两处各说一套。"""
    d = {"kind": kind, "severity": severity(kind), "seg": sid, "msg": msg}
    d.update(extra)
    return d


def slot_rect(aspect):
    """槽位版式里**图槽**在整帧上的位置（左, 上, 宽, 高，px）。

    为什么要把这个矩形写下来而不是"整帧一起看"：图槽外的标题、tagline、
    句子流、进度条一直都占着面积，整帧覆盖率被它们撑到 95% 以上，图槽里是空
    的还是满的根本量不出来。这一条是"图里到底有没有东西"唯一量得准的口径。

    几何真源是 `_template.py` 的 `layout.<画幅>`（`image` 的宽高 + 卡内边距 +
    标题区高度）；这里只做**同一份模板的换算**，不抄任何像素常量：

    - 竖屏：卡内左右各 `segCard.padding`，图框 `image.width × image.height`，
      横向居中（`image.marginSide` 为 0 时两轴同宽，不用再算）；纵向从标题区
      下沿起算——标题区 = `title.top` + 行高 × 行数 + tagline（含 marginTop）。
    - 横屏：左栏 `textCol.width` + `columnGap` 之后是图框，纵向居中
      （`margin` 上下对称）。

    读不出模板（Pillow 之外还要读模板，缺了就退回整帧）时返回全帧矩形。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _template import get_canvas, load_template
        tpl = load_template()
        cw, ch = get_canvas(aspect)
        L = tpl["layout"]["vertical" if aspect in ("portrait", "vertical") else "landscape"]
        iw, ih = int(L["image"]["width"]), int(L["image"]["height"])
        if aspect in ("portrait", "vertical"):
            pad = float(str(L["segCard"]["padding"]).split()[0].rstrip("px"))
            # 标题区高度：title.top（上边距）+ 两行标题（maxLines 是钳制上限，
            # 留满量的余量）+ tagline 一行 → 取"最坏情况"当图框上界，
            # 这样框只会框小、不会把标题算进配图区（框小了只会低估墨水）。
            t_fs = float(L["title"]["fontSize"])
            lines = int(L["title"].get("maxLines", 2))
            tag = float(L["tagline"]["fontSize"]) * float(L["tagline"]["lineHeight"]) \
                + float(L["tagline"]["marginTop"])
            top = float(L["title"]["top"]) + t_fs * 1.25 * lines + tag
            return (int(pad), int(top), iw, ih), (cw, ch)
        left = float(L["margin"]) + float(L["textCol"]["width"]) + float(L["textCol"]["gap"])
        top = (ch - ih) / 2
        return (int(left), int(top), iw, ih), (cw, ch)
    except (KeyError, TypeError, ValueError, OSError) as e:
        # 读不出模板**必须出声**：这里返回 (None, None) 会让上面 ink 全为 None，
        # 「画布稀薄」判据整条静默消失——排查时看到的是"没报"，而不是"报不了"。
        # 反过来接 Exception 更糟：它会把"代码写错"（AttributeError/NameError）
        # 也吞成"模板不对"，于是门禁永远绿、问题永远查不到。
        print(f"[warn] 观感门禁读不出 {aspect} 的图槽矩形"
              f"（{type(e).__name__}: {e}）——本轮跳过「画布稀薄」判据",
              file=sys.stderr)
        return None, None


def _ink_in_box(img, box):
    """图槽内的"墨水占比"：框内像素与背景差超过阈值的比例。

    **背景从框内自己估**，不取整帧页底色：图里往往自带深色底（示意图、照片），
    拿页底色当基准会把整块图都算成"有内容"，也可能反过来——图底比页底亮一点，
    整块被算成内容。框内四角中位数是最稳的"这张图自己的底"。
    """
    left, top, w, h = box
    crop = img.crop((left, top, min(left + w, img.size[0]), min(top + h, img.size[1])))
    if crop.size[0] < 8 or crop.size[1] < 8:
        return None
    ww, hh = crop.size
    pts = [(2, 2), (ww - 3, 2), (2, hh - 3), (ww - 3, hh - 3), (ww // 2, hh // 2)]
    px = [crop.getpixel(p) for p in pts]
    local_bg = tuple(sorted(c[i] for c in px)[len(px) // 2] for i in range(3))
    return _ratio(_mask(crop, local_bg, INK_DELTA))


def analyze(frames, segments, aspect="portrait"):
    """帧序列 → 判词。

    Args:
        frames: [{"t": 秒, "path": 帧 PNG 路径}]，按 t 升序。
        segments: manifest 的 segments（带 sentences 时间轴与 layout）。
        aspect: 画幅，决定图槽的位置。

    Returns:
        dict：issues（判词列表）、segs（每段统计）、changes（逐次采样的变化率）。
    """
    issues = []
    if not frames:
        return {"issues": [_issue("no_frames", None, "没有取到任何帧")],
                "segs": {}, "changes": []}
    frames = sorted(frames, key=lambda f: f["t"])
    imgs = [_read_frame(f["path"]) for f in frames]
    bg = _bg_color(imgs[0])
    box, _canvas = slot_rect(aspect)

    cover = [_ratio(_mask(im, bg)) for im in imgs]
    ink = [_ink_in_box(im, box) if box else None for im in imgs]
    # 段归属：按 manifest 的段区间
    spans = []
    for seg in segments:
        sents = seg.get("sentences") or []
        if not sents:
            continue
        t0 = float(sents[0]["start_time"])
        # 段**终点**按下一句/下一段的起点算，不是按"末句起点 + 时长"：两者之差
        # 正是句间 gap（默认 0.4s），按后者算会在两段之间留一条缝，缝里的换页
        # 读数就落到"谁都不属"上、被判成相邻段的错。
        nxt = [float(x["start_time"]) for x in sents[1:]]
        t1 = nxt[0] if nxt else float(sents[-1]["start_time"]) + float(sents[-1]["duration"])
        spans.append((seg["id"], seg.get("layout"), t0, t1))

    def owner(t):
        for sid, layout, t0, t1 in spans:
            if t0 - 0.35 <= t < t1 + 0.2:
                return sid, layout
        return None, None

    per_seg = {}
    for f, c, k in zip(frames, cover, ink):
        sid, layout = owner(f["t"])
        if sid is None:
            continue
        st = per_seg.setdefault(sid, {"layout": layout, "cover": [], "ink": [],
                                      "times": []})
        st["cover"].append(c)
        st["ink"].append(k)
        st["times"].append(f["t"])

    # 1) 画布稀薄（槽位版式） / 空页（满幅版式）
    for sid, st in per_seg.items():
        if st["layout"] in FULL_BLEED_LAYOUTS:
            empty = [t for t, c in zip(st["times"], st["cover"]) if c < EMPTY_COVERAGE]
            if empty:
                # 两档满幅版的空法不同：整页画布连标题层都不生成，空就是真的一片
                # 空；图版至少还压着标题与一句字幕，所以"空"说的是那张满幅图没
                # 撑起来——判据用同一个覆盖率下限，判词各说各的原因。
                why = ("整页画布段没有字幕层也没有标题层，空就是真的一片空。"
                       if st["layout"] == "canvas" else
                       "图版段那张图没撑起整幅（标题与一句字幕再满也只是几行字），"
                       "画面读起来仍是一页空的。")
                issues.append(_issue(
                    "empty_page", sid,
                    f"{len(empty)}/{len(st['times'])} 个采样点是空页"
                    f"（覆盖最高 {max(st['cover']) * 100:.2f}%，下限 "
                    f"{EMPTY_COVERAGE * 100:.1f}%）——{why}", times=empty))
            continue
        inks = [k for k in st["ink"] if k is not None]
        if not inks:
            continue
        # 判"稀薄"看这一段的**最佳状态**（框里最多有多少东西），不看平均值：
        # 一张合格但留白很大的示意图，均值会被"大图区之外的空"拉下去，
        # 而观众看到的是"这张图到底有没有东西"——那是最大值回答的问题。
        worst = max(inks)
        if worst < SLOT_INK:
            issues.append(_issue(
                "canvas_thin", sid,
                f"图槽里最多只有 {worst * 100:.2f}% 的像素算得上内容（下限 "
                f"{SLOT_INK * 100:.1f}%，{len(st['times'])} 个采样点里没有一个超过）"
                f"——图槽里基本没有东西。这一页剩下的时间只是一块空画布，观众只"
                f"看得到标题和字幕。", ink_max=round(worst, 4)))

    # 2) 死帧连片 / 3) 换屏
    changes = []
    for i in range(1, len(frames)):
        r = _diff_ratio(imgs[i - 1], imgs[i])
        changes.append({"t0": frames[i - 1]["t"], "t1": frames[i]["t"],
                        "ratio": round(r, 5)})
    runs = []
    for c in changes:
        if c["ratio"] >= MOTION_MIN:
            continue
        if runs and abs(runs[-1][1] - c["t0"]) < 1e-6:
            runs[-1][1] = c["t1"]
        else:
            runs.append([c["t0"], c["t1"]])
    for a, b in runs:
        if b - a >= DEAD_SECONDS:
            sid, layout = owner((a + b) / 2)
            # 开屏/结尾这种"停在那里让人读"的投影卡不判死帧：它本来就是一屏
            # 静态信息（章节列表、要点总结），"动了"反而是干扰。死帧判据针对的是
            # **讲解段**——那里每 3 秒都该有一件新事情发生。
            if layout == "agenda":
                continue
            issues.append(_issue(
                "dead_seconds", sid,
                f"{a:.1f}s–{b:.1f}s 共 {b - a:.1f}s 画面几乎不动（相邻采样变化 "
                f"< {MOTION_MIN * 100:.1f}%）——这段只能是字幕在推进，画面没有"
                f"替旁白做任何事。每 {DEAD_SECONDS:g}s 至少该有一次看得见的"
                f"视觉事件。",
                t0=a, t1=b, layout=layout))

    # 4) 换页时序
    for c in changes:
        if c["ratio"] <= SWITCH_RATIO:
            continue
        t = c["t1"]
        if not spans:
            continue
        gap = t - c["t0"]
        # 容差 = 采样网格 + 渲染起点：帧是**采样时刻**拍的，一次换页的真实发生
        # 时刻落在 (上一次采样, 这一次采样] 之间，读数天然带一个"采样间隔"的
        # 正偏。所以判据是 `|off| > 采样间隔 + SWITCH_TOLERANCE`，而不是硬容差
        # ——否则采样越稀、判词越多，而那读的是网格不是片子。
        #
        # 候选必须是**全部** spans：先按同一条容差筛一遍再判，筛完剩下的条条
        # 都落在容差内，`abs(off) > 容差` 恒假、整条永不触发——这道门禁等于
        # 不存在，而成片里换页与旁白错开两三秒是能一眼看出来的。
        near = min(spans, key=lambda s: abs(s[2] - t))
        off = t - near[2]
        # "这次换页属于谁"的判据是**这一页从下一段口播起点起才算数**：读数 t
        # 早于任何一个段起点时（开屏页在容器里先挂着、第一段还没开播），
        # `near` 一定是第一段，`off` 是个大负值，判词会写成"第一段声画不同步"
        # ——而画面上此刻是开屏页，第一段一秒都还没开始。两端都把读数钳回
        # [首段起点, 末段起点]，只有"页内错位"才可能是真的声画不同步。
        if spans and not (spans[0][2] <= t <= spans[-1][2]):
            near, off = None, None
        if near is not None and abs(off) > gap + SWITCH_TOLERANCE:
            issues.append(_issue(
                "switch_skew", near[0],
                f"换页读数在 {t:.2f}s（上一次采样 {c['t0']:.2f}s），而该段口播从 "
                f"{near[2]:.2f}s 开始——即便按采样间隔 {gap:.1f}s 全算进去，偏移仍有 "
                f"{abs(off) - gap:.2f}s 说不通，画面与旁白不同步。",
                t=t, seg_start=near[2]))

    return {
        "issues": issues,
        "segs": {sid: {"layout": st["layout"],
                       "samples": len(st["times"]),
                       "cover_min": round(min(st["cover"]), 5),
                       "cover_max": round(max(st["cover"]), 5),
                       "ink_min": (round(min([k for k in st["ink"] if k is not None]), 4)
                                   if any(k is not None for k in st["ink"]) else None),
                       "times": st["times"]}
                 for sid, st in per_seg.items()},
        "changes": changes,
        "bg": bg,
        "slot_box": box,
    }


def contact_sheet(frames, out_path, cols=4, thumb=(360, 480)):
    """拼 contact sheet：一格一帧，左上角标时刻。给人（和模型）一眼看全片。"""
    Image, _ = _load_pil()
    from PIL import ImageDraw
    if not frames:
        return None
    frames = sorted(frames, key=lambda f: f["t"])
    rows = (len(frames) + cols - 1) // cols
    tw, th = thumb
    sheet = Image.new("RGB", (cols * tw, rows * th), (16, 18, 22))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        im = _read_frame(f["path"])
        im.thumbnail((tw, th - 22))
        x = (i % cols) * tw
        y = (i // cols) * th
        sheet.paste(im, (x + (tw - im.size[0]) // 2, y + 22))
        draw.text((x + 8, y + 5), f"{f['t']:.1f}s", fill=(235, 240, 246))
    sheet.save(out_path, quality=88)
    return out_path


def _scan_frames(dirpath):
    """目录里的帧 → [(t, path)]，按名字里的时刻排序（snapshot 的命名是
    `frame-NN-at-XX.XXs.png`）。同时兼容 `<目录>/snapshots/` 这种上一层的布局。"""
    pairs = []
    for root, _dirs, files in os.walk(dirpath):
        for name in files:
            m = re.match(r"frame-\d+-at-([\d.]+)s\.png$", name)
            if m:
                pairs.append((float(m.group(1)), os.path.join(root, name)))
    pairs.sort()
    return pairs


def _snapshot(project, times, outdir, command):
    """调官方 snapshot 取帧。返回 [(t, path)]；失败时抛出带原因的 RuntimeError。

    `command` 是 hyperframes 的命令前缀（`run.py` 传它自己那一份，含项目内
    的 CLI 路径与钉固版本）；单跑本脚本时用默认的 `npx -y <包名>`。
    """
    if not times:
        return []
    ts = ",".join(f"{t:.2f}" for t in times)
    cmd = list(command) + ["snapshot", project, "--at", ts, "-o", outdir]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        raise RuntimeError("找不到 npx：本门禁要 Node.js 才能取帧（见 SKILL.md「环境」）")
    except subprocess.TimeoutExpired:
        raise RuntimeError("hyperframes snapshot 超时（900s）")
    # 先清空：上一次跑留下的帧如果还在，`_scan_frames` 会把**旧帧**当成这次
    # 的产出收下——判词看着有、量的是上一次的编排，而且没有任何一处会报错。
    # 官方 snapshot 默认就清目录，这里再清一次是防止 CLI 换语义 / --keep 类
    # 参数被将来接进来。
    shutil.rmtree(outdir, ignore_errors=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-2000:])
    pairs = _scan_frames(outdir)
    if not pairs:
        raise RuntimeError(f"snapshot 没有产出任何帧：{(proc.stdout or '')[-800:]}")
    return pairs


def run_gate(project, manifest, out_dir, aspect="portrait", command=None):
    """给编排层用的入口：取帧 → 判词 → 写报告与拼图。**不阻断**、不 sys.exit。

    `run.py` 在成片渲染完之后调它（那时才有一支真片子），也可以调在 HTML 之后
    （那时看的是预览，没有编码损失，判据更干净）。

    Returns:
        dict：{"frames": n, "issues": [...], "contact_sheet": path, "report": path,
               "error": 说明（取帧失败时）}
    """
    try:
        times = sample_times(manifest["segments"])
    except (KeyError, TypeError, ValueError) as e:
        # ValueError 不能漏：段里的 start_time/duration 走 float() 转换，
        # manifest 被手改成非数字字符串时抛的是它。漏了这条，一个脏 manifest
        # 能把整个编排层（run.py）带崩，而本函数的契约是"不阻断、不 sys.exit"。
        return {"error": f"manifest 里读不出时间段：{e}"}
    all_t = sorted({t for ts in times.values() for t in ts})
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(out_dir, exist_ok=True)
    # 复用上一次的帧：同一份 HTML 再判一遍不该再付一次取帧的钱（十几秒到一分钟）。
    # 判据还是当场的判据，所以"改判据→重判"这条迭代路仍然走得通。
    pairs = _scan_frames(frames_dir) if os.path.isdir(frames_dir) else []
    if [round(t, 2) for t, _ in pairs] != [round(t, 2) for t in all_t]:
        cmd = command or ["npx", "-y", os.environ.get("CTV_HYPERFRAMES_PACKAGE",
                                                      "hyperframes@0.8.185")]
        try:
            pairs = _snapshot(project, all_t, frames_dir, cmd)
        except RuntimeError as e:
            return {"error": str(e)}
    frames = [{"t": t, "path": p} for t, p in pairs]
    try:
        result = analyze(frames, manifest["segments"], aspect=aspect)
        sheet = contact_sheet(frames, os.path.join(out_dir, "contact-sheet.jpg"))
    except PillowMissing as e:
        # 帧已经取到了，判不了只是缺依赖：返回 error 让编排层记"门禁未完成"，
        # 不要把它升级成一次失败退出（见 PillowMissing 的 docstring）。
        return {"error": str(e)}
    report = {"manifest": manifest.get("_path"),
              "aspect": aspect, "slot_box": result.get("slot_box"),
              "frames": len(pairs), "contact_sheet": sheet,
              "issues": result["issues"],
              "segs": result["segs"],
              "changes": result["changes"]}
    rp = os.path.join(out_dir, "perceptual_report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return {"frames": len(pairs), "issues": result["issues"],
            "contact_sheet": sheet, "report": rp, "segs": result["segs"]}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="观感门禁：按段取帧，量空页/死帧/换页时序，产出可复核的判词与 contact sheet",
        epilog="帧来源二选一：--frames <目录>（已有 PNG，CI 可缓存）或 --project "
               "<hf-project>（当场调 hyperframes snapshot 取帧）。不替代 "
               "`hyperframes check`——check 判能不能渲，本门禁判渲出来对不对。")
    ap.add_argument("-m", "--manifest", required=True, help="timing_manifest.json")
    ap.add_argument("--aspect", default="portrait", choices=["portrait", "landscape"],
                    help="画幅（决定图槽的位置，默认 portrait；须与生成 HTML 时的 --aspect 一致）")
    ap.add_argument("--frames", default=None, help="已有帧目录（含 frame-N-at-Xs.png）")
    ap.add_argument("--project", default=None, help="HTML 项目目录（当场取帧）")
    ap.add_argument("-o", "--out", default=None, help="报告输出目录（默认 <帧目录>/perceptual）")
    ap.add_argument("--step", type=float, default=None,
                    help="采样步长（秒）；默认按段长自适应，整页画布段更密")
    ap.add_argument("--package", default=os.environ.get("CTV_HYPERFRAMES_PACKAGE",
                                                        "hyperframes@0.8.185"),
                    help="取帧用的 hyperframes 包（默认与渲染同版本）")
    ap.add_argument("--strict", action="store_true",
                    help="有 error 级判词时以退出码 5 结束（交付前用）；默认只报不拦")
    ap.add_argument("--json", action="store_true", help="同时把判词以 JSON 打到 stdout")
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    segments = manifest["segments"]

    try:
        if args.frames:
            outdir = args.frames
            pairs = _scan_frames(outdir)
            if not pairs:
                raise RuntimeError(f"{outdir} 里没有 frame-N-at-Xs.png")
        elif args.project:
            times = sample_times(segments)
            all_t = sorted({t for ts in times.values() for t in ts})
            if args.step:
                all_t = [all_t[0] + i * args.step
                         for i in range(int((all_t[-1] - all_t[0]) / args.step) + 1)]
            # 帧落到 --out 目录下：取一次帧十几秒到一分钟，而"判词不对→改判据
            # →再判一遍"是常态，留着它才能免掉重复取帧（--frames 直接复用）。
            _out = args.out or os.path.join(args.project, "perceptual")
            os.makedirs(_out, exist_ok=True)
            pairs = _snapshot(args.project, all_t, os.path.join(_out, "frames"),
                              ["npx", "-y", args.package])
        else:
            ap.error("要 --frames 或 --project 之一")
    except RuntimeError as e:
        print(f"[error] 取帧失败：{e}", file=sys.stderr)
        return 6

    if args.project and len(pairs) < len(all_t):
        # snapshot 少给帧不会自报失败：它按能抓到的时刻交差，于是"20 个采样点
        # 只回来 3 帧"在报告里长得像"整片只有 3 个采样点"，死帧与换页两条判据
        # 因为样本不够而静默失效——判词越少，越像通过。
        print(f"[perceptual][warn] 采样 {len(all_t)} 个时刻，只取回 {len(pairs)} 帧"
              f"——死帧与换页时序两条判据的覆盖会变稀，别把『无判词』读成通过。",
              file=sys.stderr)

    result = analyze([{"t": t, "path": p} for t, p in pairs], segments,
                     aspect=args.aspect)
    out = args.out or os.path.join(args.frames or args.project, "perceptual")
    os.makedirs(out, exist_ok=True)
    sheet = contact_sheet([{"t": t, "path": p} for t, p in pairs],
                          os.path.join(out, "contact-sheet.jpg"))

    report = {"manifest": os.path.abspath(args.manifest),
              "aspect": args.aspect,
              "slot_box": result.get("slot_box"),
              "frames": len(pairs),
              "contact_sheet": sheet,
              "issues": result["issues"],
              "segs": result["segs"]}
    with open(os.path.join(out, "perceptual_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"[perceptual] {len(pairs)} 帧 → {out}")
    for sid, st in result["segs"].items():
        print(f"  {sid:<8} samples={st['samples']:<3} cover={st['cover_min'] * 100:5.2f}%"
              f"–{st['cover_max'] * 100:5.2f}%  layout={st['layout'] or 'slot'}")
    if result["issues"]:
        for it in result["issues"]:
            print(f"[warn] {it['kind']} @ {it['seg']}: {it['msg']}")
    else:
        print("[perceptual] 无判词：空页、死帧、换页时序三项都过")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    # --strict 只让 error 档判失败（口径见 ISSUE_SEVERITY）：把 warn 也算进来，
    # 一份合格但节奏舒缓的片子会被 CI 判红，而那时人做的不是改画面、是删参数。
    errs = _error_issues(result["issues"])
    if args.strict and errs:
        print(f"[perceptual][strict] {len(errs)} 条 error 级判词（另有 "
              f"{len(result['issues']) - len(errs)} 条 warn）", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
