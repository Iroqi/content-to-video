#!/usr/bin/env python3
"""为类型检查生成一份 remotion/src/generated.ts（CI 与本地共用）。

`generated.ts` 是数据胶，由 `gen_remotion_project.py` 按当次 manifest 产出、
不进仓库——所以 `npx tsc` 直接跑会报"找不到模块 ./generated"，类型检查形同
虚设。这份脚本用测试夹具的合法 manifest 走一遍**真实生成器**，产出形状与真出片
时逐字段一致的数据胶。

为什么不用手写的固定内容：数据胶的字段（winStart/peel/svg/director…）由生成器
决定，手写一份就等于给 tsc 验另一个形状——生成器改了字段而这里没跟上，CI 照样
绿。用真生成器则改一处就够。

用法：python tests/make_stub_generated.py [--out remotion/src/generated.ts]
"""
import argparse
import json
import os
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(TESTS_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import _helpers as H  # noqa: E402  —— 把 scripts/ 也挂进 sys.path
from gen_remotion_project import main as gen_main  # noqa: E402


def build(out_path):
    """把数据胶写到 out_path（用临时工程再搬过去：生成器的产物是一整个工程）。"""
    with tempfile.TemporaryDirectory() as tmp:
        m_path = os.path.join(tmp, "manifest.json")
        with open(m_path, "w", encoding="utf-8") as f:
            json.dump(H.make_manifest(), f, ensure_ascii=False)
        proj = os.path.join(tmp, "proj")
        # 不带 --images：纯文字版照样产出全部段落与 clip 几何，够 tsc 查全字段。
        gen_main(["-m", m_path, "-o", proj, "--aspect", "portrait", "--fps", "24"])
        src = os.path.join(proj, "src", "generated.ts")
        with open(src, encoding="utf-8") as f:
            text = f.read()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(SKILL_DIR, "remotion", "src",
                                                   "generated.ts"),
                    help="数据胶输出路径（默认 remotion/src/generated.ts）")
    args = ap.parse_args(argv)
    path = build(args.out)
    print(f"[OK] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
