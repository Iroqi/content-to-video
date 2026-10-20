#!/usr/bin/env python3
"""检查文档里的示例是不是真能跑。

起因是一次机制审计。审计里agent 建议砍掉 `canvas_kit.py` 的四种图形，理由是
"用得少"。我去看它到底怎么被文档教会的——结果是：**872 行的能力，文档里只有一行
60 字列举，七种 kind 一个参数示例都没有**。

于是我照那份文档从零写一张竖屏柱状图，撞了 9 次错：

| 次| 撞到的 | 说明文档缺了什么 |
|---|---|---|
| 1 | `text` 的未知字段 `'size'`、`'text'` | 键名不是 `text` 是 `content` |
| 2 | `bars` 的未知字段 `x/y/w/h/values/labels` | 它的结构完全不同 |
| 3 | `axis` 的 `orient='v'` 非法 | 只接受 `'x'` / `'y'` |
| 4 | `ticks` 必须是数组 | 不是整数 |
| 5 | `bars[1]` 必须是对象 `{x,w,h,label?}` | 不是数字数组 |
| 6 | 内容元素缺 id | 有两类元素，id 要求不同 |
| 7 | `from`/`to` 重合，轴长 0 | 它们是像素坐标不是数据值 |

报错文案每次都列出了合法清单——**机制本身设计得很好**，差的是"作者第一次就撞对"。
所以本检查器守的不是示例的格式，是"示例还能不能跑"。示例退化成"看起来对"的那天，
读者要自己再撞一遍这 9 次。

用法：python tests/check_examples.py           # 有示例跑不通时退出码 1
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (文档相对路径, 锚点字符串, 命令构造)
#   锚点是示例在文档里的定位串——用它切出 ```json 围栏块。
EXAMPLES = [
    {
        "doc": os.path.join("references", "canvas.md"),
        "anchor": '"aspect": "portrait"',
        "argv": ["scripts/canvas_kit.py", "--spec", "{spec}", "-o", "{out}", "--no-check"],
        # --no-check：示例没在画布底部铺满，空带 warn 会让 canvas_kit 以 exit 1
        # 收场。那是构图选择，不是示例错——这里只验"这份 spec 造得出 SVG"。
        "must_render": True,
    },
    # writing.md 的三份稿件示例：真实文本、无占位符，dry-run 必须整稿通过
    # （分句/字段契约/上墙分号/对话轮次都过一遍）。锚点各自唯一。
    {
        "doc": os.path.join("references", "writing.md"),
        "anchor": '"speaker": "host"',
        "argv": ["scripts/run.py", "--source", "{spec}", "--dry-run"],
    },
    {
        "doc": os.path.join("references", "writing.md"),
        "anchor": '"opening": "大家好，这是我这一周的工作播报。"',
        "argv": ["scripts/run.py", "--source", "{spec}", "--dry-run"],
    },
    {
        "doc": os.path.join("references", "writing.md"),
        "anchor": '"title": "营收同比增长"',
        "argv": ["scripts/run.py", "--source", "{spec}", "--dry-run"],
    },
]


def _fenced_block(text, anchor):
    fence = "```json\n"
    i = text.find(anchor)
    if i < 0:
        raise LookupError("找不到锚点 %s" % anchor)
    start = text.rfind(fence, 0, i) + len(fence)
    if start < len(fence):
        raise LookupError("锚点 %s 不在任何 ```json 围栏里" % anchor)
    m = re.search(r"\n[ \t]*```", text[start:])
    if not m:
        raise ValueError("围栏没闭合")
    return text[start:start + m.start()]


def check_one(spec_conf):
    doc = os.path.join(ROOT, spec_conf["doc"])
    with open(doc, encoding="utf-8") as f:
        text = f.read()
    block = _fenced_block(text, spec_conf["anchor"])
    try:
        spec = json.loads(block)
    except ValueError as exc:
        return "JSON 解析失败：%s" % exc

    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "spec.json")
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        out = os.path.join(tmp, "out.svg")
        # argv里的脚本路径按仓库根解析——cwd 是临时目录，相对路径会找不到
        argv = [a.replace("scripts/", os.path.join(ROOT, "scripts") + "/", 1)
                if a.startswith("scripts/") else a
                for a in spec_conf["argv"]]
        argv = [a.format(spec=sp, out=out) for a in argv]
        r = subprocess.run([sys.executable] + argv, capture_output=True,
                           text=True, cwd=tmp)
        if r.returncode != 0:
            return "跑不通（exit %d）：%s" % (r.returncode, (r.stderr or "").strip()[-400:])
        if spec_conf.get("must_render"):
            if not os.path.isfile(out):
                return "跑通了但没产出 SVG"
            with open(out, encoding="utf-8") as f:
                svg = f.read()
            if "<svg" not in svg:
                return "产物里没有 <svg>"
            # 示例里写了却没落地的元素，等于没有示例
            for el in spec.get("elements", []):
                eid = el.get("id")
                if eid and ('id="%s"' % eid) not in svg:
                    return "元素 %s 没出现在产出的 SVG 里" % eid
    return None


def main():
    bad = 0
    for conf in EXAMPLES:
        try:
            why = check_one(conf)
        except (LookupError, ValueError) as exc:
            why = str(exc)
        rel = os.path.relpath(os.path.join(ROOT, conf["doc"]), ROOT)
        if why:
            bad += 1
            print("[example-broken] %s 的示例：%s" % (rel, why))
        else:
            print("[ok] %s 的示例跑得通" % rel)
    print("\n%d 个示例，%d 个跑不通" % (len(EXAMPLES), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
