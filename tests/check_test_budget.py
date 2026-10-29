#!/usr/bin/env python3
"""维护者工具：守住测试套件的增长（只依赖标准库）。

单元测试数从几十长到一千五，这个检查要拦的不是"多"，是**多到了哪一层**：
测试文件一旦变成"每轮 review 新建一个"，读者就再也找不到某个契约的测试在
哪、删了代码却留着测试也发现不了（绿着，但没锁任何东西）。

三道闸，各守一件真实发生过的事：

1. **总量**：用例数上限。红线到了不一定要删测试——但必须重新做一次
   "一条用例顶几条"的合并，或明确宣布抬线，而不是继续往上叠。
2. **单文件**：一个测试文件超过上限 = 它服务了不止一个决策时刻，该拆。
3. **以轮次编号命名**：`test_*.py` 里出现 `<数字>` 就报——那不是一个主题，
   是"某一轮 review 的收件箱"。它们当初记的账由下面的归属检查兜住。

另外量一次**缺陷锁的生卒**：docstring 里写了「修复前 / 此前 / 曾经 / 会红」
这类字样的用例，在它锁的那个行为回归时才有价值。一条这样的锁如果绑的函数
早就被删了（`hasattr(x, "y") == False` 那类），它就是一条永远绿的墓碑——
它证明不了任何事，只证明它自己没被人忘掉。数出来报出去，让读的人自己判。

用法：python tests/check_test_budget.py     # 超限时退出码 1
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# 用例数上限。这个数是量出来的：1533 条、全量 9 秒、平均每条约 6ms——时间不是
# 问题，**查找成本**才是（改一个模块要读哪个文件？）。定线在现有量上加 7%，
# 留出正常增量，同时逼"下一次 review 先合并再加"。
TOTAL_MAX_CASES = 1650

# 单文件上限。现存量最大的是 animate_timeline.py（110 条），它管着净化 + schema
# + 渲染 + 生成期门禁四件事；再往上就是"什么都往里塞"。120 条给一次正常增量。
FILE_MAX_CASES = 120

# 以轮次编号命名的测试文件（`test_occam_review_3.py`）：全部在 2026-10-26..29
# 的四天里按"第几轮"建出来的。它们占 66 条用例、锁的却是已经并入主干的判据。
ROUND_NAME = re.compile(r"^test_\w*_?\d+\.py$")

# 缺陷锁的措辞。命中即算"这条测试在替某个曾经坏过的行为站岗"。
DEFECT_MARK = re.compile(r"修复前|此前|曾经|回归|会红|不回来|又回来了|已删掉|不再")


def cases_in(path):
    with open(path, encoding="utf-8") as f:
        return len(re.findall(r"^\s+def test_\w+\(", f.read(), re.M))


def collect():
    rows = []
    for path in sorted(glob.glob(os.path.join(TESTS, "test_*.py"))):
        rows.append((cases_in(path), os.path.basename(path), path))
    return rows


def defect_locks(path):
    """docstring 里带「修复前」那类字样 —— 这些用例的存在理由是历史，不是契约。"""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    n = 0
    for m in re.finditer(r"def (test_\w+)\(self\):\s*(?:\"\"\"(.*?)\"\"\")?", src, re.S):
        if DEFECT_MARK.search(m.group(2) or ""):
            n += 1
    return n


def main():
    rows = collect()
    total = sum(r[0] for r in rows)
    bad = []

    if total > TOTAL_MAX_CASES:
        bad.append(f"用例总数 {total} 超过 {TOTAL_MAX_CASES} —— 先合并再加，"
                   "或明确抬线（并在这个文件里写清为什么）")

    for n, name, _ in rows:
        if n > FILE_MAX_CASES:
            bad.append(f"{name} {n} 条，超过单文件 {FILE_MAX_CASES} —— "
                       "按契约拆文件，别让它服务第二个决策时刻")

    for _, name, path in rows:
        if ROUND_NAME.match(name):
            bad.append(f"{name} 以轮次编号命名 —— 一个测试文件应该对着一个契约"
                       "（`svg-motion` / `animate-schema` / `render-backend`），"
                       "不是收某一轮 review 的账")

    locks = sum(defect_locks(p) for _, _, p in rows)
    for msg in bad:
        print(f"[test-budget] {msg}")
    print(f"\n用例 {total} / {TOTAL_MAX_CASES} · 文件 {len(rows)} 个 · "
          f"缺陷锁 {locks} 条（{locks * 100 // max(total, 1)}%）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
