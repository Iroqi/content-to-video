#!/usr/bin/env python3
"""karaoke-align 的 fixture 自对账：spec → 期望 steps，且两次编译一致。

扩展包自己跑对账，不占核心测试预算（见 references/extensions.md 的 fixture
一节）。跑法：

    python scripts/extensions/karaoke-align/fixture/verify.py

只做两件事：产物必须逐字节等于 expected_steps.json；同一 spec 编译两次必须
一致。前者钉住「字级时刻没有被偷偷改回均分」，后者钉住纯函数。
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _load():
    path = os.path.join(PKG, "compile.py")
    loader = importlib.util.spec_from_file_location("ctv_karaoke_align", path)
    mod = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(mod)
    return mod


def _read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    mod = _load()
    spec = _read("spec.json")
    expected = _read("expected_steps.json")
    a = mod.compile(spec, {})
    b = mod.compile(spec, {})
    if a != expected:
        print("[error] 产物与 fixture 期望不一致")
        print("  实际:", json.dumps(a, ensure_ascii=False)[:300])
        return 1
    if a != b:
        print("[error] 两次编译不一致：compile() 不是纯函数")
        return 1
    times = [s["at_time"] for s in a["steps"][1:]]
    if times != sorted(times) or len(set(times)) != len(times):
        print("[error] 字级时刻不是严格递增：", times)
        return 1
    print("[ok] karaoke-align fixture 对账通过（%d 条 step，时刻 %s）"
          % (len(a["steps"]), times))
    return 0


if __name__ == "__main__":
    sys.exit(main())
