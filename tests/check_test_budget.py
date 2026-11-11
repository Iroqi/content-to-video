#!/usr/bin/env python3
"""维护者工具：守住测试套件的增长（只依赖标准库）。

单元测试数从几十长到一千五，这个检查要拦的不是"多"，是**多到了哪一层**：
测试文件一旦变成"每轮 review 新建一个"，读者就再也找不到某个契约的测试在
哪、删了代码却留着测试也发现不了（绿着，但没锁任何东西）。

四道闸，各守一件真实发生过的事：

1. **总量**：用例数上限。红线到了不一定要删测试——但必须重新做一次
   "一条用例顶几条"的合并，或明确宣布抬线，而不是继续往上叠。
2. **单文件**：一个测试文件超过上限 = 它服务了不止一个决策时刻，该拆。
3. **以轮次编号命名**：`test_*.py` 里出现 `<数字>` 就报——那不是一个主题，
   是"某一轮 review 的收件箱"。它们当初记的账由下面的归属检查兜住。
4. **同一个类里同名用例**：`def test_x` 写两遍时 `unittest` 只收后一个，前一
   条静默不跑，而上面的总量按文本里的 `def` 数，还会把它算成两条。这是
   "绿着，但没锁住任何东西"最安静的一种：复制粘贴一次就抹掉一条契约锁。
   重复检测按类分组——不同类里同名 `test_x` 是正常写法，不算重复。

另外量一次**缺陷锁的生卒**：docstring 里写了「修复前 / 此前 / 曾经 / 会红」
这类字样的用例，在它锁的那个行为回归时才有价值。一条这样的锁如果绑的函数
早就被删了（`hasattr(x, "y") == False` 那类），它就是一条永远绿的墓碑——
它证明不了任何事，只证明它自己没被人忘掉。数出来报出去，让读的人自己判。

用法：python tests/check_test_budget.py     # 超限时退出码 1
"""
import ast
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# 用例数上限。这个数是量出来的：1533 条、全量 9 秒、平均每条约 6ms——时间不是
# 问题，**查找成本**才是（改一个模块要读哪个文件？）。定线在现有量上加 7%，
# 留出正常增量，同时逼"下一次 review 先合并再加"。
# 抬线记录：1650→1660（章节读数 test_chapter_hud.py：新能力落在版式、CSS 与
# 时间线三处，压到 4 条已是"一份产物验完"的极限）；1660→1680（解码式
# 揭示 type.decode：新能力落在契约层、渲染端 JS 与 canvas_kit 三处，判据必须是
# **产物级**——把发出去的脚本在 Node 里按进度重放，Python 再写一份噪声实现只会
# 变成两份互相对照的实现。所有用例合并进一个 test_type_decode.py，三轮迭代
# （基础档 / 锚点与语义单元 / 解码波）共用同一套重放 harness，其中一条专门把
# 单元素档与组档逐帧比对，钉住两套实现不各写一份）；1680→1700（受控
# 弹簧：新能力落在契约层、渲染端 JS 注册、生成期分色门禁三处，判据必须是
# **产物级**——把发出去的注册 JS 在 Node 里重放，Python 再写一份 JS 实现只会变成
# 两份互相对照。17 条全并进一个 test_spring_ease.py：5 条数学判据（端点/行程预算/
# 过冲/纯函数，全 ζ 带遍历压在一条里）、4 条契约、4 条产物、4 条分色。抬 20 是量过
# 的：1671 基线 + 17 = 1688，留 12 条给下一次正常增量）；1700→1725（惯性档
# momentum：新能力落在速度包络、契约层、生成期采样、跨段接续烘焙**四处**，
# 比弹簧多一处——接续页第 0 帧不认新档就会静默弹回原位，这条判据不能省。
# 18 条全并进一个 test_momentum.py，共用同一套「从产物抠 tl.set 序列」的
# harness：4 条数学（纯函数/端点/单峰/峰值对齐）、4 条守恒与落点、4 条契约、
# 4 条产物对账、2 条作用域与接续。抬 25 是量过的：1699 基线 + 18 = 1717，
# 留 8 条给下一次正常增量）；1725→1750（拖曳链 drag：新能力落在相位真源、
# 契约层、生成期采样、按文件拦链节点、跨段接续烘焙**五处**，比惯性档多一处——
# 链上每一节都得在接续页折出 translate，只折链头时链尾会静默弹回原位。
# 16 条全并进一个 test_drag.py，共用同一套「从产物抠 tl.set 序列」的 harness：
# 4 条数学（纯函数/端点/相位单调/阻尼衰减）、4 条契约、5 条产物对账、
# 3 条作用域与接续。抬 25 是量过的：1717 基线 + 16 = 1733，留 17 条给下一次
# 正常增量）；1750→1775（定格档 step：新能力落在唯帧网格真源、契约层、
# 生成期采样、跨段接续烘焙**四处**，比拖曳链少一处「按文件拦链节点」——
# 这一档没有额外选择器，落空 target 由 `_animate` 原有的通用判据兜住。
# 但它多一条别的档都没有的产物级判据：`tl.set` 的**条数**第一次等于唯帧数
# 而不是渲染帧数，必须逐条对账时刻与位移，还要钉住「两张唯帧撞进同一个
# 0.01s 格」这类静默少一张画的错。17 条全并进一个 test_step.py，共用同一套
# 「从产物抠 tl.set 序列」的 harness：4 条数学（纯函数/端点/网格单调/
# 时间抖动）、5 条契约、5 条产物对账、3 条作用域与接续。抬 25 是量过的：
# 1728 基线 + 17 = 1745，留 30 条给下一次正常增量）。本轮有机档 organic
# **未抬线**：新能力落在不公度谐波真源、契约层、生成期采样、跨段接续烘焙四处，
# 18 条全并进一个 test_organic.py（5 条信号、2 条轨迹、2 条带宽、4 条契约、
# 3 条产物对账、2 条作用域与接续，共用「从产物抠 tl.set 序列」的 harness）。
# 基线 1750 + 18 = 1768，仍在 1775 内，留 7 条给下一次正常增量；
# 1775→1800（残影档 trail：新能力落在时间回声真源、契约层、生成期采样、
# 按文件拦影子、跨段接续烘焙**五处**，比有机档多一处「按文件拦影子节点」——
# 影子落空不报错、只会静默短一截，这条判据不能省。17 条全并进一个
# test_trail.py，共用「从产物抠 tl.set 序列」的 harness：4 条信号（纯函数/
# 回声恒等式/端点/速度门与逐层衰减）、2 条轨迹（不重参数化/层序）、
# 2 条网格、4 条契约、3 条产物对账、2 条作用域与接续。抬 25 是量过的：
# 1768 基线 + 17 = 1785，留 15 条给下一次正常增量）。
# 1800→1825（波场档 wave：新能力落在**几何真源** `_wave.geometry`、契约层、
# 生成期体检按文件拦格子/震中、渲染端采样、跨段接续烘焙**五处**，与残影档同级；
# 且多一条别的档都没有的产物级判据「改图即改编排」——波前延迟由 SVG 里量出的
# 欧氏距离算，挪动一个格子，产物里它的 τ 必须跟着变而其余格子不动，这条不能省。
# 17 条全并进一个 test_wave.py，共用「从产物抠 tl.set 序列」的 harness：4 条信号
# （纯函数/紧支撑/端点/峰值衰减）、3 条传播（距离序/改图即改编排/作用域）、
# 2 条网格、4 条契约、3 条产物对账、1 条作用域与接续。抬 25 是量过的：
# 1785 基线 + 17 = 1802，已在 1800 之上，留 23 条给下一次正常增量）。
# 2026-11-09 L1 编译期扩展点：新增 20 条核心契约判据（键集封闭 / 显式注册 /
# 编译期红线 / 两次编译一致 / #id 正交 / 来源记账 / 技能目录守卫），全并进一个
# test_extensions.py。抬 20 是量过的：1802 + 20 = 1822，仍留 23 条给下一次正常
# 增量。扩展包自己的 fixture 对账不占这份预算（见 references/extensions.md），
# 所以这份额度只给核心侧契约，不给第 11 个动效档。下次超线仍默认先合并。
# 1845→1870（流线档 flow：新能力落在**曲线几何真源** `_flow.geometry`（path d
# 的弧长参数化 + 元素投影）、契约层、生成期体检按文件拦 riders/path、渲染端
# 采样、跨段接续烘焙**五处**，与波场档同级；但比它多一处——接续落点是由曲线
# 形状算出的几何量，不是常量，`_stage_carry` 必须读这一页的 SVG 才能逐元素
# 折 translate，只折流头会让链尾在页界静默弹回。18 条全并进一个
# test_flow.py，共用「从产物抠 tl.set 序列」的 harness：4 条信号（纯函数/
# 相对位移/包络/衰减）、4 条场（弧长序/切线方向/改图即改编排/几何红线）、
# 2 条网格、4 条契约、3 条产物对账、1 条接续。抬 25 是量过的：1827 基线
# + 18 = 1845，恰好顶到旧线；不抬线就等于把下一次正常增量的额度吃光，
# 与本文件「留余量、逼先合并再加」的初衷相反，故同前几次一样抬 25，
# 留 25 条给下一次正常增量）。
# 1870→1895（透镜档 lens：新能力落在**瞬时方向场真源** `_lens.geometry`
# （两个锚点量场心轨迹、透镜包围盒量影响半径、逐元素到线段的净空与两端
# 方向夹角）、契约层、生成期体检按文件拦 cells/glass/to、渲染端采样、
# 跨段接续烘焙**五处**，与流线档同级；但多一条别的档都没有的产物级判据：
# 「同一个元素的位移方向在这一拍之内真的转过 6°」，必须从 `state_at` 的
# 逐帧位移向量读出来，不能只复述配置。另有「改图即改编排」的两条——
# 挪一个元素只有它的位移序列变、改透镜包围盒整组峰值一起变。18 条全并进
# 一个 test_lens.py，共用「从产物抠 tl.set 序列」的 harness：4 条信号
# （纯函数/方向时变/端点/紧支撑与反向）、5 条几何（真源/退化按文件拦/
# 体检/改图即改编排×2）、2 条网格、4 条契约、2 条产物对账、1 条接续。
# 抬 25 是量过的：1845 基线 + 18 = 1863，旧线只剩 7 条余量，等同于把下一次
# 正常增量的额度吃光；与前几次同理由抬 25，留 32 条给下一次正常增量）。
# 1895→1920（群集档 swarm：新能力落在**闭式邻域场真源** `_swarm.field/geometry`
# （元素两两相对几何、boids 三规则加权、全体最远间距定 R）、契约层、生成期
# 体检按文件拦 cells、渲染端采样、跨段接续烘焙**五处**，与透镜档同级；但多一条
# 别的档都没有的产物级判据：**挪一个元素，至少另一个元素的位移序列也变**——
# 前面十四档「改图即改编排」断言的恰好是「其余元素一格不动」，这里必须反过来，
# 从 HTML 抠出的 tl.set 序列逐帧比对。另有 mode 三值不静默失效、离群活跃度
# 衰减两道自查。18 条全并进一个 test_swarm.py，共用「从产物抠 tl.set 序列」的
# harness：4 条信号、4 条耦合与几何、2 条网格、4 条契约、3 条产物对账、
# 1 条接续。抬 25 是量过的：1879 基线 + 18 = 1897，旧线已经穿透，与前几次
# 同理由抬 25，留 23 条给下一次正常增量）。
TOTAL_MAX_CASES = 1920

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


def duplicate_cases(path):
    """返回 [(文件, 类, 方法, 首次行, 重复行)]：同一个类里同名 `test_*` 方法。

    按类分组，只看类体里直接定义的方法：不同类里都叫 `test_x` 是正常写法，
    模块级辅助函数也不参与。用 ast 而不是正文正则——正则会把字符串素材里的
    `def test_` 也数进去，那恰好重演了"文本里有、进程里没跑"的错觉。
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    name = os.path.basename(path)
    found = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        first = {}
        for item in cls.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not item.name.startswith("test_"):
                continue
            if item.name in first:
                found.append((name, cls.name, item.name,
                              first[item.name], item.lineno))
            else:
                first[item.name] = item.lineno
    return found


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
                       "（`motion` / `animate-schema` / `render-backend`），"
                       "不是收某一轮 review 的账")

    for _, name, path in rows:
        for _, cls, meth, first, again in duplicate_cases(path):
            bad.append(f"{name} 的 {cls}.{meth} 在第 {first} / {again} 行重复定义 —— "
                       "unittest 只收集后一个，前一个永远不跑（复制粘贴会静默抹掉一条契约锁）")

    locks = sum(defect_locks(p) for _, _, p in rows)
    for msg in bad:
        print(f"[test-budget] {msg}")
    print(f"\n用例 {total} / {TOTAL_MAX_CASES} · 文件 {len(rows)} 个 · "
          f"缺陷锁 {locks} 条（{locks * 100 // max(total, 1)}%）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
