#!/usr/bin/env python3
"""校验「代码里的数字」与「文档里写的数字」说的是同一件事。

第一性原理：文档里的数字是**代码常量的复制品**。复制品必然漂移——有人改了常量、
忘了改文档，于是两个人拿着不同的阈值做决策，而且谁也看不出来。靠人记得对齐是不
可行的，所以把这件事变成机器保证。

锁法是双向的，两头都堵：
  1. 代码这边改了、文档没改→ `expect` 与代码里的值不符，报错；
  2. 文档这边删了/改了这句说法、代码没动 → `doc` 正则在文档里匹配不到，报错。

两种取代码值的方式：
  ("const", 文件, 常量名)——顶层 `NAME = <字面量>` 赋值；
  ("json",  文件, 点分路径)——文件里那段内嵌 JSON 模板（`_template.py` 的
  `_TEMPLATE_JSON`）里逐层下钻，如 "layout.vertical.agenda.maxRows"。

为什么模板要用 "json" 而不是 "dict"：`_template.py` 把版式装在一个 r'''...''' 字符串里
再 json.loads（为了保住 CSS 里的花括号不被 f-string 吃掉），源码里并不存在字面量
dict。顺着真实的存储形式读，才不会读到一份和运行时不同的数据。

登记标准：只登记**决策阈值**——改了它，作者的写法就得跟着变的那种数字（几何尺寸、
告警阈值、字长上限、时间容差、版式钳制）。实测出来的观感数值（哪档字号好看、PSNR
多少算接上了）不登记：那些是经验，改版式时连代码一起改，不构成"文档说了不算数"的
风险。登记标准宽一点就会退化成"数字对不上的噪音源"，那就该关掉了。

docs 为空列表的条目（契约已下沉到脚本、文档不再复述那个数）：只锁代码那一头。
这不是"放松成一半"，而是下沉之后的正确形态——数字仍然有唯一真源（代码常量），
而它的传达方式变成了脚本报错，不再是文档里的一句话。写空列表时 why 要说清
"现在由谁告诉作者"，否则后来人会以为这里漏填了。
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# id, 取值方式, 期望值, 必须命中的文档与正则, 为什么它重要
FACTS = [
    (
        "canvas-slot-vertical",
        ("json", "scripts/_template.py", "layout.vertical.image.width"), 980,
        [("references/canvas.md", r"\| 槽位（不写 `layout`，默认） \| 980×735 \| 1067×800 \|"),
         ("references/imagery.md", r"竖屏 980×735")],
        "槽位图栏宽度；作者按这个尺寸出图，差一截就被 cover 裁掉",
    ),
    (
        "canvas-slot-vertical-height",
        ("json", "scripts/_template.py", "layout.vertical.image.height"), 735,
        [("references/canvas.md", r"980[×x]735")],
        "同上，高度",
    ),
    (
        "canvas-slot-landscape",
        ("json", "scripts/_template.py", "layout.landscape.image.width"), 1067,
        [("references/canvas.md", r"\| 槽位（不写 `layout`，默认） \| 980×735 \| 1067×800 \|"),
         ("references/imagery.md", r"横屏右侧图栏 1067×800"),
         ("references/rendering.md", r"右侧图栏 1067×800")],
        "横屏图栏宽度；改版式必须同步改文档里所有手写示例",
    ),
    (
        "canvas-slot-landscape-height",
        ("json", "scripts/_template.py", "layout.landscape.image.height"), 800,
        [("references/canvas.md", r"1067[×x]800")],
        "同上，高度",
    ),
    (
        "canvas-full-vertical",
        ("json", "scripts/_template.py", "canvas.vertical.width"), 1080,
        [("references/canvas.md", r"1080[×x]1440"),
         ("references/imagery.md", r"1080[×x]1440"),
         ("SKILL.md", r"1080[×x]1440")],
        "整页画布竖屏原生宽度；照错尺寸画会被 check_svg 判比例不符",
    ),
    (
        "canvas-full-vertical-height",
        ("json", "scripts/_template.py", "canvas.vertical.height"), 1440,
        [("references/canvas.md", r"1080[×x]1440")],
        "同上，高度",
    ),
    (
        "canvas-full-landscape",
        ("json", "scripts/_template.py", "canvas.landscape.width"), 1920,
        [("references/canvas.md", r"1920[×x]1080"),
         ("SKILL.md", r"1920[×x]1080")],
        "整页画布横屏宽度",
    ),
    (
        "canvas-full-landscape-height",
        ("json", "scripts/_template.py", "canvas.landscape.height"), 1080,
        [("references/canvas.md", r"1920[×x]1080")],
        "同上，高度",
    ),
    (
        "upscale-warn",
        ("const", "scripts/_media_size.py", "UPSCALE_WARN"), 1.25,
        [("references/imagery.md", r"1\.25\s*倍")],
        "位图放大告警阈值：作者据此判断素材够不够糊",
    ),
    (
        "upscale-severe",
        ("const", "scripts/_media_size.py", "UPSCALE_SEVERE"), 2.0,
        [("references/imagery.md", r"2\.0\s*倍")],
        "严重档阈值；缺这一档说明少说了一级严重性",
    ),
    (
        "min-font-px",
        ("const", "scripts/check_svg.py", "MIN_PX"), 26,
        [("references/canvas.md", r"\*\*26px 是下限\*\*")],
        "图内字号下限的权威定义句；作者照此定字号，改了它所有字号建议都要重算",
    ),
    (
        "empty-band",
        ("const", "scripts/check_svg.py", "MAX_EMPTY_BAND"), 0.25,
        [("references/canvas.md", r"25%\s*页高")],
        "整页画布横向空带阈值（0.25 → 文档写百分比）；调它等于改密度自查法",
    ),
    (
        "speaker-label-max",
        ("const", "scripts/_source_schema.py", "SPEAKER_LABEL_MAX_CHARS"), 6,
        [("references/writing.md", r"最多\s*6\s*字"),
         ("references/rendering.md", r"6\s*字上限")],
        "说话人标签上墙字长上限；超了契约层直接报错，作者必须知道这个数",
    ),
    (
        "media-duck-volume",
        ("const", "scripts/_images_schema.py", "MEDIA_DUCK_VOLUME"), 0.25,
        [("references/imagery.md", r"让路默认\s*0\.25"),
         ("references/rendering.md", r"让路默认\s*0\.25")],
        "素材没写 volume 时的默认增益；两处症状表都引用这个数",
    ),
    (
        "media-volume-max",
        ("const", "scripts/_images_schema.py", "MEDIA_VOLUME_MAX"), 3.98,
        [],
        "volume 取值上限（官方契约 +12 dB）；写高了 schema 直接拒。"
        "契约已下沉：范围由 _images_schema 的越界报错当场带出"
        "（`_VIDEO_AUDIO_OPTS` 里存着上下限与含义），文档不再复述这个数",
    ),
    (
        "default-speed",
        ("const", "scripts/_timeline.py", "DEFAULT_SPEED"), 1.5,
        [("references/tts_pipeline.md", r"默认\s*`?1\.5")],
        "全局语速默认；写稿时不看代码就按这个填 speed",
    ),
    (
        "opening-closing-speed",
        ("const", "scripts/_timeline.py", "OPENING_CLOSING_DEFAULT_SPEED"), 1.2,
        [("references/tts_pipeline.md", r"开场/结尾用\s*`?1\.2")],
        "开屏/结尾建议语速；与正文默认值不同，是一条独立建议",
    ),
    (
        "default-gap",
        ("const", "scripts/_timeline.py", "DEFAULT_GAP"), 0.4,
        [("references/tts_pipeline.md", r"默认\s*`?0\.4"),
         ("references/rendering.md", r"默认\s*0\.4\s*s")],
        "句间静音默认；直接影响转场宽度",
    ),
    (
        "timeline-tolerance",
        ("const", "scripts/_timeline.py", "TIMELINE_TOLERANCE"), 0.25,
        [("references/tts_pipeline.md", r"250ms")],
        "末句不得超出 total_duration 的容差（0.25s → 文档写 250ms）",
    ),
    (
        "duration-tolerance-floor",
        ("const", "scripts/_timeline.py", "DURATION_TOLERANCE_FLOOR_S"), 1.0,
        [("SKILL.md", r"max\(1s"),
         ("references/rendering.md", r"max\(1s, 2%\)")],
        "实测长度对账的绝对容差下限：短片按绝对量放行，不按 2% 放大到不足一秒",
    ),
    (
        "duration-tolerance-ratio",
        ("const", "scripts/_timeline.py", "DURATION_TOLERANCE_RATIO"), 0.02,
        [("SKILL.md", r"2%\)"),
         ("references/rendering.md", r"max\(1s, 2%\)")],
        "同上，相对档：长片按比例放宽。音频回落闸与成片时长闸共用这一档，"
        "两处各写一个数就会漂移",
    ),
    (
        "agenda-max-rows",
        ("json", "scripts/_template.py", "layout.vertical.agenda.maxRows"), 7,
        [("references/writing.md", r"7\s*行")],
        "开屏/结尾卡总行数硬上限；两画幅同值",
    ),
    (
        "agenda-name-trim-portrait",
        ("json", "scripts/_template.py", "layout.vertical.agenda.nameTrim"), 20,
        [("references/writing.md", r"竖屏\s*20\s*字")],
        "agenda 行名竖屏截断字数；超了直接不上画面且无省略号",
    ),
    (
        "agenda-name-trim-landscape",
        ("json", "scripts/_template.py", "layout.landscape.agenda.nameTrim"), 26,
        [("references/writing.md", r"横屏\s*26\s*字")],
        "同上，横屏",
    ),
    (
        "long-sentence-warn",
        ("const", "scripts/build_from_structured.py", "LONG_SENTENCE_CHARS"), 45,
        [("references/writing.md", r"45\s*字")],
        "逐句告警的字长阈值",
    ),
    (
        "min-node-major",
        ("const", "scripts/_render_backend.py", "MIN_NODE_MAJOR"), 22,
        [("SKILL.md", r"Node\.js\s*22\+")],
        "渲染前置条件：Hyperframes CLI 的 engines 写死 node>=22，低了要在开跑前拦下",
    ),
    (
        "long-sentence-hint",
        ("const", "scripts/build_from_structured.py", "LONG_SENTENCE_HINT_CHARS"), 32,
        [("references/writing.md", r"32[–-]45\s*字")],
        "段落级 hint 的字长下界",
    ),
]


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _const(rel, name):
    """取顶层赋值 `NAME = <字面量>` 的值。"""
    tree = ast.parse(_read(rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise KeyError("%s 里找不到常量 %s" % (rel, name))


def _json(rel, path):
    """在文件内嵌的 JSON 模板里按点分路径下钻，如 layout.vertical.agenda.maxRows。"""
    tree = ast.parse(_read(rel))
    blob = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            try:
                candidate = json.loads(node.value.value)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                blob = candidate
                break
    if blob is None:
        raise KeyError("%s 里找不到内嵌 JSON 模板" % rel)
    cur = blob
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError("%s 的 JSON 模板里找不到路径 %s 的一环 %s" % (rel, path, key))
        cur = cur[key]
    return cur


def read_code_value(spec):
    kind = spec[0]
    if kind == "const":
        return _const(spec[1], spec[2])
    if kind == "json":
        return _json(spec[1], spec[2])
    raise ValueError("未知的取值方式 %r" % (kind,))


def main():
    problems = []
    for fid, spec, expect, docs, why in FACTS:
        # 第一头锁：代码实际值必须等于登记的期望值
        try:
            actual = read_code_value(spec)
        except (KeyError, ValueError, SyntaxError) as exc:
            problems.append("[code-missing] %s：读不到代码里的值（%s）" % (fid, exc))
            continue
        if actual != expect:
            problems.append(
                "[code-drift] %s：%s = %r，但文档按 %r 写着。改常量就要同步改文档"
                % (fid, " ".join(spec[1:]), actual, expect)
            )
            continue
        # 第二头锁：文档必须还留着这句说法
        for rel, pattern in docs:
            try:
                text = _read(rel)
            except OSError as exc:
                problems.append("[doc-missing] %s：读不到 %s（%s）" % (fid, rel, exc))
                continue
            if not re.search(pattern, text):
                problems.append(
                    "[doc-drift] %s：%s 里找不到 /%s/（%s）"
                    % (fid, rel, pattern, why)
                )

    for line in problems:
        print(line)
    if problems:
        print("\n数字契约漂移 %d 处。改代码就改文档，别只改一边。" % len(problems))
        return 1
    locked_docs = sum(1 for f in FACTS if f[3])
    sunk = len(FACTS) - locked_docs
    print("数字契约一致：%d 条代码常量全部等于登记值（%d 条另锁文档说法，%d 条契约已下沉、"
          "由脚本报错当场带出）。" % (len(FACTS), locked_docs, sunk))
    return 0


if __name__ == "__main__":
    sys.exit(main())
