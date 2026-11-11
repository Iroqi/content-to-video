#!/usr/bin/env python3
"""拖曳档的单一真源：把「相位」从一条主运动里取出来，变成一条会传下去的波。

本仓库此前把**一个元素**的运动做透了：行程预算、两轴异缓动的弧线、入场阶梯、
受控弹簧、hold 慢推，以及惯性档 `momentum` 的预备/挤压拉伸/跟随。它们共同的上限
是**一拍只演一个主体**；要一组元素一起动，唯一的办法是 `stagger`——可 stagger
是「每个元素各自完整演一遍」：按窗口把起跑时刻分开，每个元素从自己的起点走到
自己的终点，观众读到的是「逐个出现」，不是「一条被带动的链」。

动画十二原则里还空着的一格恰好是 **Overlapping Action / Drag（重叠动作 / 拖曳）**：
主体先动，附属部件晚半拍跟上，能量沿链逐节耗散，最后**一起落定**。2026 年前沿
MG 里的果冻链、柔性尾迹、级联跟随都是这一档，它们与 stagger 的差别不是「错峰
多少」，而是**所有元素共享同一条位置曲线、同一个终点**——错的是相位，不是行程。

这一档因此不新增第二种位移语义：仍是一条 `f(p)`，仍落在 `distance`。多出来的
只有两件事，都由**闭式**给出、都是 `t` 的纯函数：

```
φ_i = lag · i/(N−1)                       链序相位，0 → lag
u_i(p) = clamp((p − φ_i)/(1 − φ_i), 0, 1) 局部进度（重参数化，见下）
a_i = overshoot · decay^i                 阻尼幅度，逐节耗散
h(u) = sin(2πu)·(1−u)²                    过冲核：h(0)=h(1)=h′(1)=0
s_i(t) = D · [ f(u_i(t/dur)) + a_i·h(u_i(t/dur)) ]
```

**重参数化不是修饰**：若只写成 `f(p − φ_i)`，末端元素在 `p=1` 时只走到
`f(1−φ) < 1`，链条最后一节永远停在半路；除以 `(1−φ_i)` 后 `u_i(1)=1`，全链在
同一拍终点精确落位，`s_i(1)=D` 对每一个 i 都成立。

`h` 的双根 `(1−u)²` 保证落地时过冲与其一阶导**同时归零**——否则末端元素是带着
速度撞停的，读成「抽了一下」。前半周为正（越过落位）、后半周为负（回弹），配合
`decay^i` 就是一条只传一次、能量递减的欠阻尼波，不需要积分、不需要保存上一帧。

**为什么不写成 GSAP 的 stagger 或渲染端 onUpdate**：stagger 的 delay 不进逐帧采样，
判据没法从产物里抠出来；低通滤波式阻尼链靠累计状态，逐帧 `seek()` 下第 N 帧与
直接跳到第 N 帧对不上，而 Python 侧的判据会退化成对着另一份 JS 实现自证
（`decode.md` / `spring.md` 都记过这笔账）。这里沿用 `morph` / `momentum` 的老路：
**生成期按帧率采样成离散 `tl.set`**，渲染端零脚本、零回调。

**链序必须是显式的**：`target` 写 `.class` 时 DOM 顺序不是语义，而离散 `tl.set`
没法给同一选择器的不同命中元素写不同数值。所以 `drag.chain` 收一列有序的 `#id`，
链头必须等于 step 级 `target`；链序错了就是导演意图错了，契约层应该当场说清。

红线写在消费侧（`validate`），契约层、渲染端、接续烘焙调的是同一个函数——
`--set` 或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变形。
"""
import math
import re

from _ease import curve, split_ease, validate_ease, reject_overshoot
# 生成期采样密度是两档共用的同一条工程判据（每帧一条 tl.set、下限看得见、
# 上限防产物膨胀）。写成两份字面量就会改一处漏一处，且不报任何错。
from _momentum import SAMPLES_MIN, SAMPLES_MAX
from _validate import is_finite_number as _finite  # noqa: E402  数值守卫只有一份真源（拒 NaN/Inf/超大整数）

# ── 红线（工程判据，不进模板、不给 --set 改）────────────────────────────
# 链尾的最大相位滞后 / 总时长。超过 1/3 时链首已落定、链尾还没起步，读到的是
# 两个先后独立的动作，不是一条链。
LAG_MAX = 0.35
# 链首过冲幅度 / 行程。与 momentum 的 STRETCH_MAX 同量级：再大就抢了主运动的戏。
OVERSHOOT_MAX = 0.30
# 阻尼系数 [0,1]：1 = 全链同幅度（无衰减），0 = 只有链首过冲。
DECAY_MAX = 1.0
# 链长带：短于 2 不成链，长于 8 则每帧 9 条以上的 tl.set，且观众数不出链序。
CHAIN_MIN = 2
CHAIN_MAX = 8
# 采样帧数带从 _momentum 导入（见文件头）：两档共用，不在本模块另写一份。
# 主运动缺省曲线：到位停住用 .out（motion.md「快慢、缓动按内容定」）。
DEFAULT_EASE = "power3.out"
# 只收无过冲且**终点速度归零**的族与方向：`none`/`linear` 的 f′(1)≠0 会让全链
# 撞停；`.in` 档起点慢终点最快，与 h 的「先过冲后回弹」叠在一起读成两次动作。
_ALLOWED_FAMILIES = frozenset({
    "power1", "power2", "power3", "power4", "quad", "cubic", "quart",
    "quint", "strong", "sine", "expo", "circ",
})
_ALLOWED_DIRECTIONS = frozenset({"out", "inOut"})
_DRAG_KEYS = frozenset({"chain", "axis", "distance", "ease",
                        "lag", "overshoot", "decay"})
# 链成员只认单个 #id：一组同 class 的元素没法用离散 tl.set 分别写值。
_CHAIN_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")
# 判据的扫描密度。与 momentum.peak 同一量级，够钉住过冲峰值与半程时刻。
SCAN_SAMPLES = 512
# 产物写进 HTML 时数值锁六位（与 _stage_carry._fmt 同档），端点按这把尺判精确。
PRODUCT_EPS = 1e-6
DEFAULT_LAG = 0.22
DEFAULT_OVERSHOOT = 0.0
DEFAULT_DECAY = 0.72


def validate(cfg, where="drag"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象）。"""
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _DRAG_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_DRAG_KEYS)}")
    chain = cfg.get("chain")
    if not isinstance(chain, list) or not (CHAIN_MIN <= len(chain) <= CHAIN_MAX):
        raise ValueError(f"{where}.chain 必须是 {CHAIN_MIN}~{CHAIN_MAX} 个 #id 的"
                         f"有序列表（链序就是导演意图；实际: {chain!r}）")
    for sel in chain:
        if not isinstance(sel, str) or not _CHAIN_ID_RE.match(sel):
            raise ValueError(f"{where}.chain 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(chain)) != len(chain):
        raise ValueError(f"{where}.chain 不能有重复 id：同一元素领两次相位会"
                         "被后写的那条静默覆盖")
    axis = cfg.get("axis")
    if axis not in ("x", "y"):
        raise ValueError(f"{where}.axis 必须是 'x' 或 'y'（实际: {axis!r}）")
    d = cfg.get("distance")
    if not _finite(d) or abs(float(d)) < 1e-9:
        raise ValueError(f"{where}.distance 必须是非零有限数（SVG 用户单位的"
                         f"主轴位移；实际: {d!r}）")
    ease = str(cfg.get("ease") or DEFAULT_EASE)
    validate_ease(ease, f"{where}.ease")
    reject_overshoot(ease, f"{where}.ease")
    parts = split_ease(ease)
    if (parts is None or parts[0] not in _ALLOWED_FAMILIES
            or parts[1] not in _ALLOWED_DIRECTIONS):
        raise ValueError(
            f"{where}.ease={ease!r} 不是无过冲的 out / inOut 档：主运动必须"
            "终点速度归零（none / linear / .in 会让全链撞停），过冲族与弹簧的"
            "位移会反号，与阻尼过冲叠成两次动作（认得 power3.out / sine.inOut）")
    out = {"chain": list(chain), "axis": axis, "distance": float(d), "ease": ease}
    for name, limit, default in (("lag", LAG_MAX, DEFAULT_LAG),
                                 ("overshoot", OVERSHOOT_MAX, DEFAULT_OVERSHOOT),
                                 ("decay", DECAY_MAX, DEFAULT_DECAY)):
        v = cfg.get(name, default)
        if not _finite(v) or not (0.0 <= float(v) <= limit):
            raise ValueError(f"{where}.{name}={v!r} 越界（0~{limit:.2f}）："
                             f"{name} 是比例，不是像素")
        out[name] = float(v)
    return out


def _kernel(u):
    """过冲核：端点与终点导数全为 0，前半周过冲、后半周回弹。"""
    return math.sin(2.0 * math.pi * u) * (1.0 - u) ** 2


def _local_progress(q, phi):
    """`p → u_i`：相位平移 + 重参数化，保证 `u_i(1) = 1`。"""
    if q <= phi:
        return 0.0
    if q >= 1.0:
        return 1.0
    return (q - phi) / (1.0 - phi)


def plan(cfg, duration, fps, where="drag"):
    """把一条 `drag` 拍编译成链上每个元素的相位与幅度。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur = float(duration)
    if not math.isfinite(dur) or dur <= 0:
        raise ValueError(f"{where} 的 duration 必须 > 0（实际: {duration!r}）")
    n = len(c["chain"])
    phases = [c["lag"] * i / (n - 1) for i in range(n)]
    amps = [c["overshoot"] * c["decay"] ** i for i in range(n)]
    return {
        "cfg": c,
        "duration": dur,
        "curve": curve(c["ease"], DEFAULT_EASE),
        "phases": phases,
        "amps": amps,
        "fps": max(1.0, float(fps)),
    }


def state_at(t, p, i):
    """链上第 i 个元素在时刻 `t`（相对拍起点）的补间变量，**纯函数**。

    不读墙钟、不存上一帧：逐帧 `seek()` 下每一帧都由 `t` 独立算出，这是本档
    能进成片的前提（与 `_momentum.state_at`、`_hold.scale_at` 同一条纪律）。
    """
    if not 0 <= i < len(p["phases"]):
        raise ValueError(f"链下标 {i} 越界（链长 {len(p['phases'])}）")
    q = min(1.0, max(0.0, float(t) / p["duration"]))
    u = _local_progress(q, p["phases"][i])
    ratio = float(p["curve"](u)) + p["amps"][i] * _kernel(u)
    return {p["cfg"]["axis"]: p["cfg"]["distance"] * ratio}


def frames(p):
    """生成期采样：`[(相对秒, 链成员 id, 补间变量), …]`，每帧每条一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位（与 `morph` / `momentum` 同一口径）；同一
    时刻撞在一起的样本保留最后一个——GSAP 在同一时刻按注册顺序执行，最后一条
    生效。排序键是 (时刻, 链下标)，不是 id 字面量：链序是导演意图，不能被字典序
    洗掉。
    """
    n = max(SAMPLES_MIN, min(SAMPLES_MAX, round(p["duration"] * p["fps"])))
    acc = {}
    for k in range(n + 1):
        t = p["duration"] * k / n
        tr = round(t, 2)
        for i, sel in enumerate(p["cfg"]["chain"]):
            vars_ = {key: round(float(v), 6)
                     for key, v in state_at(t, p, i).items()}
            acc[(tr, i)] = (sel, vars_)
    return [(tr, sel, vars_)
            for (tr, _i), (sel, vars_) in sorted(acc.items())]


def landing(p, i):
    """第 i 个元素的收尾态。跨段接续只搬 x/y，全链终点都是 `distance`。"""
    return state_at(p["duration"], p, i)


def crossing(p, i, frac=0.5):
    """第 i 个元素首次走到 `frac` 行程的时刻（秒），按行程比扫描，不带符号歧义。"""
    target = frac
    for k in range(SCAN_SAMPLES + 1):
        q = k / SCAN_SAMPLES
        ratio = state_at(q * p["duration"], p, i)[p["cfg"]["axis"]] \
            / p["cfg"]["distance"]
        if ratio >= target:
            return q * p["duration"]
    return p["duration"]


def deviation(p, i):
    """第 i 个元素偏离主运动曲线的最大量（SVG 用户单位），用来量阻尼衰减。

    扫描走**局部进度 u 的均匀网格**，不是全局时间 q 的：相位把每一节的 u
    区间压成不同长度，按 q 扫时各节采到的过冲峰位置不同，`decay=1`（全链
    同幅）会被读出 1e-5 级的假衰减，生成期自查会把一条合法的拍拦掉。由 u
    反推 q 再交给 `state_at`，判据读的仍是真实状态，不是另写一份解析式。
    """
    axis = p["cfg"]["axis"]
    d = p["cfg"]["distance"]
    phi = p["phases"][i]
    if p["amps"][i] == 0.0:
        # 过冲幅度为 0 时 s_i ≡ D·f(u)，偏离量解析上恒为 0；继续扫描只会把
        # 两次浮点求值之间的 1 ulp 残差读成「有阻尼」，判据就成了谎话。
        return 0.0
    worst = 0.0
    for k in range(SCAN_SAMPLES + 1):
        u = k / SCAN_SAMPLES
        q = phi + (1.0 - phi) * u     # _local_progress 的逆
        v = state_at(q * p["duration"], p, i)[axis]
        base = d * float(p["curve"](u))
        worst = max(worst, abs(v - base))
    return worst


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。

    `lag=0` / `overshoot=0` 的纯位移档全链同步，没有「相位递增」与「能量衰减」
    可钉，那两条对它无意义——硬判会把一条完全合法的拍在生成期拦掉（与
    `_momentum.is_well_formed` 对 `stretch=0` 的处理同一条理由）。
    """
    axis = p["cfg"]["axis"]
    d = p["cfg"]["distance"]
    cfg = p["cfg"]
    checks = []
    for i in range(len(p["phases"])):
        start = state_at(0.0, p, i)
        end = landing(p, i)
        checks += [
            abs(start[axis]) < 1e-9,
            abs(end[axis] - d) < 1e-6,
            # 产物是六位截尾的，端点也必须按这把尺精确，否则接续页会漂
            abs(round(end[axis], 6) - round(d, 6)) <= PRODUCT_EPS,
        ]
    if cfg["lag"] > 0:
        ts = [crossing(p, i) for i in range(len(p["phases"]))]
        checks += [
            all(b >= a - 1e-9 for a, b in zip(ts, ts[1:])),
            ts[-1] - ts[0] >= 0.5 / p["fps"],
        ]
    if cfg["overshoot"] > 0:
        devs = [deviation(p, i) for i in range(len(p["phases"]))]
        checks += [
            all(b <= a + 1e-6 for a, b in zip(devs, devs[1:])),
            max(devs) <= cfg["overshoot"] * abs(d) + 1e-6,
        ]
    return all(checks)
