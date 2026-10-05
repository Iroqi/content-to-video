"""双源对拍：scripts/_ease.py（契约层/生成期）↔ remotion/src/easing.ts（渲染端）。

**为什么需要这个测试**：缓动数学在仓库里有两份实现——Python 侧 `_ease.curve`
在生成期采样 morph、校验档名，TS 侧 `easing.ts` 在渲染端逐帧求值。历史上这两份
真的分过家：TS 的幂次表写成 `power1:1…power4:4`（正确是 `power1:2…power4:5`），
而 `power2.out` 正是**没写 ease 的 step 的默认值**——错一档，每一条没写 ease
的动画都跟着变形，且两边各自自洽，不出任何报错。

CI 只跑 Python，TS 侧本来零测试守护，所以这里从**源码**取常量 + 用 Node 直接
执行 TS 模块，把"两份实现必须逐点一致"变成 CI 会红的事实。Node 不可用时静默
跳过（不因为环境缺个运行时就让门禁变红），但本地/CI 有 Node 就一定跑。

三道断言：
1. 幂次表 `_POLY_EXP` 与 `POLY_EXP` 键值逐项相等（分家的第一现场）；
2. 目录内每个族 × 每个方向都能解析出曲线（TS 端少实现一个族 = 静默线性）；
3. 20 个代表档位 × 9 个采样点逐点对比两条曲线（公式漂移）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import _helpers  # noqa: F401  —— 把 scripts/ 挂进 sys.path
import _ease as E

REMOTION_SRC = os.path.join(_helpers.SKILL_DIR, "remotion", "src")
EASING_TS = os.path.join(REMOTION_SRC, "easing.ts")
POLY_EXP_TS = os.path.join(REMOTION_SRC, "easing.ts")

# 覆盖全部族与全部方向，另加三种参数化写法与两个旧名别名。
# 每档 × 9 个采样点 = 180 个数字要逐点对齐，公式改错一个系数就会红。
SPECS = [
    "none", "linear", "power0",
    "power1.in", "power1.out", "power1.inOut",
    "power2.in", "power2.out", "power2.inOut",
    "power3.out", "power3.inOut", "power4.out", "power4.in",
    "quad.out", "cubic.out", "quart.out", "quint.out",
    "sine.in", "sine.out", "sine.inOut",
    "expo.in", "expo.out", "expo.inOut",
    "circ.out", "circ.inOut",
    "back.in(2)", "back.out(1.7)", "back.inOut(3)",
    "bounce.out", "bounce.in", "bounce.inOut",
    "elastic.out(1,0.3)", "elastic.in", "elastic.inOut",
    "steps(4)", "steps(4).in", "steps(3,true)",
    "strong.out", "power2", "back.out",
]
SAMPLE_TS = [i / 8 for i in range(9)]


def _node():
    return shutil.which("node")


def _run_node(script, cwd=None):
    """跑一段 node 脚本，返回 stdout。失败时把 stderr 一起带上来（便于定位）。"""
    proc = subprocess.run([_node(), "--experimental-strip-types", "-e", script],
                          capture_output=True, text=True, cwd=cwd, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"node 执行失败:\n{proc.stderr.strip()[:2000]}")
    return proc.stdout


def _ts_module_script(body, entry):
    """拼一段 node 脚本：直接 import 仓库里的 TS 源码（Node 22 能剥类型）。"""
    url = "file://" + entry.replace("\\", "/")
    return f"const M = await import({json.dumps(url)});\n{body}"


def _ts_curve_samples():
    """在 Node 里跑 easing.ts，返回 {spec: [采样值]}。"""
    body = (
        'const out = {};\n'
        f'const specs = {json.dumps(SPECS)};\n'
        f'const ts = {json.dumps(SAMPLE_TS)};\n'
        'for (const s of specs) {\n'
        '  const f = M.easeByName(s);\n'
        '  out[s] = f === null ? null : ts.map((t) => f(t));\n'
        '}\n'
        'process.stdout.write(JSON.stringify(out));\n'
    )
    raw = _run_node(_ts_module_script(body, EASING_TS), cwd=os.path.dirname(EASING_TS))
    return json.loads(raw)


def _ts_poly_exp():
    """从 TS 源码抠出 POLY_EXP 的键值对（不执行代码也能查表，Node 缺失时仍可跑）。"""
    with open(POLY_EXP_TS, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"POLY_EXP[^=]*=\s*\{(.*?)\}", src, re.S)
    if not m:
        raise AssertionError("remotion/src/easing.ts 里找不到 POLY_EXP 表")
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", m.group(1))}


@unittest.skipIf(_node() is None, "需要 node（跑 remotion 渲染本来就要它）")
class Parity(unittest.TestCase):
    maxDiff = None

    def test_poly_exp_tables_identical(self):
        """幂次表必须逐项相同。这里分过家：TS 曾是 power1:1…power4:4。"""
        self.assertEqual(_ts_poly_exp(), dict(E._POLY_EXP),
                         "TS 的 POLY_EXP 与 Python 的 _ease._POLY_EXP 不一致——"
                         "幂次错一档，power2.out（默认 ease）整条曲线就跟着错")

    def test_every_catalog_spec_resolves_on_both_sides(self):
        """目录内每档在两边都要解得出曲线；TS 解不出 = 静默退化。"""
        got = _ts_curve_samples()
        ts_bad = []
        for spec in SPECS:
            E.validate_ease(spec, "测试档名")   # 契约层自己先认
            if got.get(spec) is None:
                ts_bad.append(spec)
        self.assertEqual(ts_bad, [],
                         f"这些档 Python 认、TS 的 easeByName 返回 null（会退化成"
                         f"静默线性）：{ts_bad}")

    def test_curves_agree_point_by_point(self):
        """20+ 档 × 9 个采样点逐点对齐（容差 5e-7：Python 侧按 6 位小数落盘）。"""
        got = _ts_curve_samples()
        drift = []
        for spec in SPECS:
            ts_vals = got.get(spec)
            if ts_vals is None:
                drift.append((spec, None, "TS 解析不出"))
                continue
            f = E.curve(spec, "power2.out")
            for t, ts_v in zip(SAMPLE_TS, ts_vals):
                py_v = f(t)
                if abs(py_v - ts_v) > 5e-7:
                    drift.append((spec, t, f"py={py_v!r} ts={ts_v!r} Δ={py_v - ts_v:.2e}"))
        self.assertEqual(drift, [], "两条缓动实现分家了：\n" + "\n".join(
            f"  {s} @t={t}: {why}" for s, t, why in drift))


class SourceHygiene(unittest.TestCase):
    """源码层的硬约束：与 Node 无关，任何环境都必须成立。"""

    def test_director_does_not_reimplement_easing(self):
        """求值器不得自带缓动表——那是分家的起点。"""
        with open(os.path.join(REMOTION_SRC, "components", "director.ts"),
                  encoding="utf-8") as f:
            src = f.read()
        # 旧的 powEase/backEase + EASE_REGISTRY 常量表
        self.assertNotRegex(src, r"\bEASE_REGISTRY\b",
                            "director.ts 出现了本地缓动表：缓动数学只能有 easing.ts 一处")
        self.assertNotRegex(src, r"\bpowEase\b|\bbackEase\b",
                            "director.ts 又自己实现了一份幂次/回弹缓动")
        # 幂次数组也不能重抄一份
        self.assertNotRegex(src, r"power1\s*:\s*\d",
                            "director.ts 抄了幂次表，幂次必须只来自 easing.POLY_EXP")

    def test_named_exports_come_from_the_single_implementation(self):
        """具名导出的那几档必须是 easeByName 派生，不能各写一条公式。"""
        with open(EASING_TS, encoding="utf-8") as f:
            src = f.read()
        # named() 必须真的从 easeByName 取曲线（而不是自己再写一条公式）
        m = re.search(r"const named = \(\s*spec: string\s*\)[^{]*\{(.*?)\n\};", src, re.S)
        self.assertIsNotNone(m, "easing.ts 里找不到 named() 工厂")
        self.assertIn("easeByName(spec)", m.group(1),
                      "named() 必须经 easeByName 取曲线，否则具名导出就是第二份实现")
        # 每个具名导出都要走 named("...")，不许出现裸箭头函数
        for name in ("power2Out", "power2In", "power2InOut", "power3Out",
                     "sineOut", "backOut"):
            self.assertRegex(src, rf"export const {name} = named\(",
                             f"{name} 应从唯一实现派生（export const {name} = named(...)）")
        # 幂次公式只允许出现在 gOf 的幂次族分支里一次
        self.assertEqual(src.count("Math.pow(t,"), 1,
                         "easing.ts 里幂次公式应只出现在 gOf 的幂次族分支一次")

    def test_unknown_ease_is_not_silently_linear(self):
        """解析不出必须返回 null，把决策交回调用方出声，不许静默当线性。"""
        with open(EASING_TS, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"easeByName[\s\S]{0,400}?return null",
                         "easeByName 解析不出时必须返回 null")
        self.assertNotRegex(
            src, r"easeByName[\s\S]{0,400}?\?\s*\(\s*t\s*\)\s*=>\s*t",
            "easeByName 不许在解析不出时静默回退线性（这正是旧 TS 幂次表分家时"
            "参数化档位无声变直线的形态）")

    def test_natural_opacity_zero_is_not_read_as_one(self):
        """自然态 opacity=0 必须读成 0。

        这里翻过车：`parseFloat(...) || 1` 把作者写的 `opacity="0"`（最常见的
        "先藏起来、节拍里淡入"写法）读成自然态 1，于是 from 0→to 1 的淡入在
        每一帧都算出 1，动画第一帧就跳到终值、整段静默失效。
        """
        with open(os.path.join(REMOTION_SRC, "components", "director.ts"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotRegex(
            src, r"parseFloat\([^)]*\.opacity\)\s*\|\|\s*1",
            "naturalFor 用 `|| 1` 读自然态 opacity：读到 0 会被当成假值兜成 1，"
            "淡入动画静默失效。必须显式判 isFinite")
        self.assertNotRegex(
            src, r"parseFloat\([^)]*\)\s*\|\|\s*[-\d.]+",
            "naturalFor 里的 `parseFloat(x) || 数字` 兜底会把合法的 0 读成兜底值"
            "（0 与『读不到』不是一回事），必须显式判 isFinite")


if __name__ == "__main__":
    unittest.main()
