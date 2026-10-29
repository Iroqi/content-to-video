#!/usr/bin/env python3
"""文档示例里出现的 images.json 片段，能不能被契约层接住。

`check_examples.py` 守的是"文档示例跑得通"，但它只覆盖了 spec 类示例（canvas_kit
的 spec、writing.md 的稿件）。`images.json` 的片段此前一条都不在它的清单里——而
`animate` 这类概念页最容易写出的错，恰恰是"字段名拼对了一半、
结构差一层"，那种错不会让任何示例跑不通（因为根本没有示例），却让照抄的读者在
第一次生成时撞墙。

这里按锚点切出文档里的 ```json 围栏块，直接喂 `_images_schema.validate_images_json`。
围栏里的键必须是**真段落 id**：用 `seg4` 这类占位名会被"manifest 里没有这一段"
一类的后续检查绕开，而这里只做纯契约校验，所以占位名也成立——但为了示例能直接抄进
自己的 images.json，统一用 `seg2`（与 canvas.md 其余示例一致）。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from _images_schema import validate_images_json  # noqa: E402

# (文档相对路径, 锚点串, 说明)
SNIPPETS = [
    (os.path.join("references", "motion.md"),
     '"target": "#node-a"', "motion.md「契约」的完整示例"),
    (os.path.join("references", "motion.md"),
     '"param": "theta"', "motion.md「参数演绎」的最小示例"),
    (os.path.join("references", "motion.md"),
     '"param": "theta", "to": 180', "motion.md「参数演绎」的多拍累进示例"),
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


def main():
    bad = 0
    for rel, anchor, label in SNIPPETS:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            text = f.read()
        try:
            spec = json.loads(_fenced_block(text, anchor))
        except (LookupError, ValueError) as exc:
            bad += 1
            print("[example-broken] %s：%s（%s）" % (rel, exc, label))
            continue
        try:
            validate_images_json(spec)
        except ValueError as exc:
            bad += 1
            print("[example-broken] %s：契约层不接（%s）：%s" % (rel, label, exc))
            continue
        print("[ok] %s 的 %s 过契约层" % (rel, label))
    print("\n%d 个 images.json 片段，%d 个过不去" % (len(SNIPPETS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
