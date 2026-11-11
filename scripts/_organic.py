#!/usr/bin/env python3
"""有机档的单一真源：把「不规则」从时间域搬进值域，且不牺牲逐帧可复现。

本仓库此前把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度（`momentum`）、相位（`drag`）、静止（`hold`）、采样网格
（`step`）、时序（`stagger` / `decode`）。它们有一条**从没被打破过的共同前提**：
每一条都是「从 A 收敛到 B」的一次性、单调、完全解析的曲线。唯一与不规则沾边的
是 `step` 的 `w_j`，而 `_step.py` 说得很明白——「抖动只进时间域，不进位置域」。
于是**值域的不规则是空的**。

可 2025/2026 的前沿 MG 反复点名的恰好是值域：有机/流体运动（"Imperfection is
the new perfect"）、会呼吸的字重与基线编排、liquid blob 的准周期起伏。它们的共同
手感是「不像程序算出来的」——不完全重复、不严格对称、起止却仍要干净。这类运动
在这套体系里根本无法表达：`repeat`+`yoyo` 只能给一条**完全周期**的正弦，读完第一
遍就预告了后面每一帧；CSS `@keyframes` 与墙钟动画又被逐帧 seek 的纪律挡在门外。

难点不是「加个随机」，而是**逐帧可复现**：本技能明确禁 `random` / `time`
（见 `motion.md`「参数演绎」），任何带内部状态或墙钟的实现，第 N 帧与直接跳到
第 N 帧就对不上。这一档的解法是**确定性的不公度多频谐波**：

```
u = t / D
W(u) = sin²(πu)                          端点窗口：W(0)=W(1)=W′(0)=W′(1)=0
w_k = 1/k^DECAY, 归一化 Σw = 1           谱衰减：高频不许抢戏
F = (1, √2, √3, √5)                     两两之比无理 → 永不精确重复
φ_k(s, a) = 2π·frac((s+1)·α_k + a·γ)    整数种子 + 轴号 → 确定性相位
N(u; a) = Σ_k w_k·sin(2π·F_k·c·u + φ_k(s,a))
off(u; a) = A · W(u) · N(u; a)
```

`α_k = frac(√p_k)`（p = 2,3,5,7）是无理数的小数部分，`γ` 是黄金比共轭。相位
只由整数 `seed` 与轴号 `a` 经纯算术算出——没有 `random` 模块、没有全局状态、
没有墙钟，同 `seed` 在任何机器任何 python 上给出逐位相同的序列。

**三条设计决定，都是被成片纪律逼出来的**：

1. **端点窗口而不是淡入淡出**。`W=sin²(πu)` 让物体在段首段尾**精确回原位**，
   且一阶导也归零——不会「切过来先歪着」，也不会在页界处带着速度撞停。它是
   全仓库唯一**收尾态等于起始态**的档：跨段接续没有任何残留姿态可折，
   `_stage_carry` 天然连续，不会弹回。
2. **两轴必须异相**。`axis:"xy"` 时 x 取 `a=0`、y 取 `a=1` 两组不同相位。
   若同相，`(x,y) = off·(c,c)` 恒落在过原点的一条直线上，垂距恒为 0——
   那不是「有机游走」，是「沿斜线来回滑」，与 `motion.md`「位移走弧线」里
   「两轴同缓动 = 直弦」是同一个陷阱。判据用协方差的各向同性比钉住它。
3. **生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step`
   的老路：渲染端零脚本、零 `onUpdate`、零依赖。`seed` 是**生成期**读的，
   成片里只有一串确定的数值，逐帧 `seek()` 下每一帧都是时间的纯函数。
   用 GSAP 的 `RoughEase` / 运行时噪声函数都会在渲染端再存一份实现，
   Python 侧判据只能退化成对着另一份 JS 自证（`decode.md` / `spring.md`
   都记过这笔账）。

**与相邻档位的边界**：`step` 的不规则在**时间域**（哪帧换画），本档在**值域**
（这一帧偏多少）；`hold` 是单调的 2% 慢推（回答「有没有动」），本档是来回
起伏（回答「动得像不像活的」）；`momentum` 消费 `f′(p)` 做形变，`drag` 消费
相位差做链，本档消费的是一个与 `f(p)` 无关的独立信号——所以它与所有整段档
互斥，不叠在同一条 transform 通道上。

红线写在消费侧（`validate`）：契约层、渲染端、接续烘焙调的是同一个函数，
`--set` 或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默
变成一块抖动或一张不动的图。
"""
import math

# 采样带、产物精度、判据扫描密度全部与既有档共用同一份真源：写成第二份字面量
# 就会改一处漏一处，而且不报任何错（test_shared_geometry 钉着这条纪律）。
from _drag import PRODUCT_EPS, SCAN_SAMPLES
from _momentum import SAMPLES_MIN, SAMPLES_MAX
from _validate import is_finite_number as _finite  # noqa: E402  数值守卫只有一份真源（拒 NaN/Inf/超大整数）

# ── 红线（工程判据，不进模板、不给 --set 改）────────────────────────────
# 谐波层数。少于 3 层读不出「不重复」，多于 5 层在 24fps 下会读成高频噪声。
HARMONICS = 4
# 谱斜率：w_k = 1/k^DECAY。1.35 让第 2 层仍有 22% 权重（看得出不规则），
# 第 4 层只剩 9%（不会闪）。调到 3 以上就退化成单频，调到 0 就成白噪声。
DECAY = 1.35
# 不公度频率：两两之比都是无理数，信号在有限窗口内不会精确重复。
FREQ = (1.0, math.sqrt(2.0), math.sqrt(3.0), math.sqrt(5.0))
# 种子相位基：frac(√2), frac(√3), frac(√5), frac(√7)。无理数小数部分保证
# 不同 seed 不会撞出同一组相位，且纯 sqrt/floor 运算跨平台一致。
SEED_PHASE = tuple(math.sqrt(p) - math.floor(math.sqrt(p))
                   for p in (2, 3, 5, 7))
# 轴间相位偏移（黄金比共轭）。0 会让两轴同相 → 轨迹退化成直线。
AXIS_PHASE = 0.6180339887498949
# 幅度上限（SVG 用户单位）。980px 槽位的 6% 左右，再大主体会读成「飘走了」。
AMPLITUDE_MAX = 60.0
# 起伏密度：整段内主频走几个周期。低于 0.5 等于只涌动一次（看不出「呼吸」），
# 高于 4.0 在常见拍长里逐帧位移过大，读成抖动。
CYCLES_MIN = 0.5
CYCLES_MAX = 4.0
# 整数种子带。上限只为让「手改一个数字换一版手感」这件事有界，不是随机源。
SEED_MIN = 0
SEED_MAX = 9999
# 最短拍长：帧时刻按 0.01s 锁两位，n ≥ SAMPLES_MIN 时要求 D/n ≥ 0.01，
# 否则两个采样撞进同一个时刻格、GSAP 只认最后一条，产物静默少一帧。
MIN_DURATION = 0.12
# 判据红线：频率比与最近有理数 p/q（q ≤ 12）的距离下限。实测本组最小 0.00219。
INCOMMENSURATE_MIN = 0.001
# 双轴各向同性比下限（√λ2/λ1）。实测异相约 0.39，同相恒为 0.0。
MIN_ISOTROPY = 0.20
# 逐帧位移 / 幅度上限。实测最坏（4 周期、19 帧采样）约 0.66；超线读成闪。
MAX_FRAME_STEP_RATIO = 0.75
# 产物数值锁六位，与 _stage_carry._fmt / _drag.PRODUCT_EPS 同档。
PRODUCT_DECIMALS = 6
# 端点一阶导的判据窗口（秒）与容差（幅度比例）。
EDGE_DERIVATIVE_DT = 1e-3
EDGE_DERIVATIVE_EPS = 1e-4

DEFAULT_AMPLITUDE = 12.0
DEFAULT_CYCLES = 1.6
DEFAULT_SEED = 7
DEFAULT_AXIS = "xy"
_ORGANIC_KEYS = frozenset({"axis", "amplitude", "cycles", "seed"})
_AXES = frozenset({"x", "y", "xy"})


def _frac(x):
    return x - math.floor(x)


def validate(cfg, where="organic"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象）。

    契约层与渲染端调的是**同一个函数**：红线写在消费侧，`--set` 或手改过的
    images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变形。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _ORGANIC_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_ORGANIC_KEYS)}")
    axis = cfg.get("axis", DEFAULT_AXIS)
    if axis not in _AXES:
        raise ValueError(f"{where}.axis 必须是 'x' / 'y' / 'xy'（'xy' 两轴"
                         f"用不同相位，同相会退化成直线；实际: {axis!r}）")
    amp = cfg.get("amplitude", DEFAULT_AMPLITUDE)
    if not _finite(amp) or not (0.0 < float(amp) <= AMPLITUDE_MAX):
        raise ValueError(f"{where}.amplitude 必须是 (0, {AMPLITUDE_MAX:.0f}] 的"
                         f"有限数（SVG 用户单位的幅度上限；实际: {amp!r}）")
    cyc = cfg.get("cycles", DEFAULT_CYCLES)
    if not _finite(cyc) or not (CYCLES_MIN <= float(cyc) <= CYCLES_MAX):
        raise ValueError(f"{where}.cycles 必须在 {CYCLES_MIN}~{CYCLES_MAX} 之间"
                         f"（整段内主频走几个周期；实际: {cyc!r}）")
    seed = cfg.get("seed", DEFAULT_SEED)
    if (not isinstance(seed, int) or isinstance(seed, bool)
            or not (SEED_MIN <= seed <= SEED_MAX)):
        raise ValueError(f"{where}.seed 必须是 {SEED_MIN}~{SEED_MAX} 的整数"
                         f"（确定性相位种子，不是随机源；实际: {seed!r}）")
    return {"axis": axis, "amplitude": float(amp),
            "cycles": float(cyc), "seed": seed}


def harmonic_spectrum():
    """归一化的各层权重。严格递减 = 带宽受限，高层不许盖过主频。"""
    raw = [1.0 / (k ** DECAY) for k in range(1, HARMONICS + 1)]
    total = sum(raw)
    return tuple(w / total for w in raw)


def _phases(seed, axis_index):
    """一组确定性相位：纯算术、无 random、无状态、跨平台一致。"""
    return tuple(2.0 * math.pi * _frac((seed + 1) * SEED_PHASE[k]
                                       + axis_index * AXIS_PHASE)
                 for k in range(HARMONICS))


def freqs():
    """不公度频率表（对外只读，判据与文档共用这一份）。"""
    return FREQ


def plan(cfg, duration, fps, where="organic"):
    """把一条 `organic` 拍编译成一份可采样的计划。生成期与判据共用这一份。

    `duration` 是 step 级时长（秒），`fps` 决定采样密度。返回值的字段就是
    渲染端写 `tl.set` 需要的全部数值，不在两处各算一遍。
    """
    c = validate(cfg, where)
    dur = float(duration)
    if not math.isfinite(dur) or dur < MIN_DURATION:
        raise ValueError(f"{where} 的 duration 必须 ≥ {MIN_DURATION}s（帧时刻锁"
                         f"两位，短于此会让两个采样撞进同一格、静默少一帧；"
                         f"实际: {duration!r}）")
    f = float(fps)
    if not math.isfinite(f) or f <= 0:
        raise ValueError(f"{where} 的 fps 必须是正的有限数（实际: {fps!r}）")
    return {
        "cfg": c,
        "duration": dur,
        "fps": f,
        "spectrum": harmonic_spectrum(),
        "freqs": FREQ,
        "phases": (_phases(c["seed"], 0), _phases(c["seed"], 1)),
    }


def _noise(u, p, axis_index):
    """去窗的单位幅值信号：4 层不公度谐波的加权和，|N| ≤ 1。"""
    theta = 2.0 * math.pi * p["cfg"]["cycles"] * u
    ph = p["phases"][axis_index]
    return sum(p["spectrum"][k] * math.sin(p["freqs"][k] * theta + ph[k])
               for k in range(HARMONICS))


def state_at(t, p):
    """时刻 `t`（相对拍起点）的补间变量，**纯函数**：不读墙钟、不存上一帧。

    逐帧 `seek()` 下每一帧都由 `t` 独立算出，这是本档能进成片的前提——
    与 `params` 白名单禁 `random`、弹簧必须闭式解、`hold.scale_at` 同一条纪律。
    """
    c = p["cfg"]
    dur = p["duration"]
    u = 0.0 if t <= 0.0 else (1.0 if t >= dur else float(t) / dur)
    axis = c["axis"]
    keys = ("x", "y") if axis == "xy" else (axis,)
    # 显式短路端点：`sin(π·1)` 在浮点下是 1.2e-16 而不是 0，平方后留下
    # 1e-32 的残差。它小到判据看不见，却会让「端点精确回原位」这句承诺
    # 在产物里变成 `-0.0`、在跨段接续里变成一次无意义的浮点比较。
    if u <= 0.0 or u >= 1.0:
        return {k: 0.0 for k in keys}
    window = math.sin(math.pi * u) ** 2
    amp = c["amplitude"]
    if axis == "x":
        return {"x": amp * window * _noise(u, p, 0)}
    if axis == "y":
        return {"y": amp * window * _noise(u, p, 0)}
    return {"x": amp * window * _noise(u, p, 0),
            "y": amp * window * _noise(u, p, 1)}


def frame_count(p):
    """采样帧数。与 momentum / drag 共用同一条采样带，不在本模块另写一份。"""
    return max(SAMPLES_MIN, min(SAMPLES_MAX,
                                round(p["duration"] * p["fps"])))


def _clean(v):
    """锁六位并把 `-0.0` 归一成 `0.0`：否则端点那帧会写出 `-0.0`，
    逐条对账时与 0.0 不相等（json.dumps 也真的会吐 `-0.0`）。"""
    r = round(float(v), PRODUCT_DECIMALS)
    return 0.0 if r == 0.0 else r


def frames(p):
    """生成期采样：`[(相对秒, 补间变量), …]`，每帧一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位（与 `morph` / `momentum` 同一口径）；
    同一时刻撞在一起的样本保留最后一个——GSAP 在同一时刻按注册顺序执行。
    `MIN_DURATION` 已保证合法配置下不会真的发生碰撞，生成期再核一次。
    """
    n = frame_count(p)
    out = {}
    for i in range(n + 1):
        t = p["duration"] * i / n
        vars_ = state_at(t, p)
        out[round(t, 2)] = {k: _clean(v) for k, v in vars_.items()}
    return sorted(out.items())


def landing(p):
    """收尾态。端点窗口 W(1)=0，精确回原位——跨段接续没有姿态可折。"""
    return state_at(p["duration"], p)


def start(p):
    """起始态，与 `landing` 相等。"""
    return state_at(0.0, p)


def _series(p, key, n=SCAN_SAMPLES):
    return [state_at(p["duration"] * i / n, p)[key]
            for i in range(n + 1)]


def max_deviation(p):
    """峰值 |偏移| / amplitude：归一化后恒 ≤ 1（幅度是上界，不是承诺）。"""
    keys = ("x", "y") if p["cfg"]["axis"] == "xy" else (p["cfg"]["axis"],)
    peak = max(abs(v) for k in keys for v in _series(p, k))
    return peak / p["cfg"]["amplitude"]


def incommensurate_gap():
    """任意两层频率比与最近有理数 p/q（q ≤ 12）的最小距离。

    这是「永不精确重复」的结构性判据：比值若是有理数，信号走完公倍数个周期
    就会整段重播，`repeat`+`yoyo` 那种机械感又回来了。不读信号、不拟合，
    只读频率表本身——比在短窗口上做谱分析稳（试过：4 层谐波在 1~2 个周期
    的窗口里，单频拟合 R² 能到 0.98，判据根本分不开）。
    """
    best = float("inf")
    for i in range(len(FREQ)):
        for j in range(i + 1, len(FREQ)):
            ratio = FREQ[j] / FREQ[i]
            best = min(best, min(abs(ratio - q0 / q)
                                 for q in range(1, 13)
                                 for q0 in range(1, int(ratio * q) + 3)))
    return best


def trajectory_isotropy(p):
    """双轴轨迹的 √(λ2/λ1)：0 = 严格共线（两轴同相），1 = 各向同性圆。

    与 `motion.md`「位移走弧线」的弯曲度判据同源，但端点重合（都回原点）
    时「弦」退化成一个点，量不了垂距——改用协方差的两根之比。
    """
    if p["cfg"]["axis"] != "xy":
        return 0.0
    n = SCAN_SAMPLES
    xs, ys = _series(p, "x", n), _series(p, "y", n)
    mx, my = sum(xs) / (n + 1), sum(ys) / (n + 1)
    sxx = sum((x - mx) ** 2 for x in xs) / (n + 1)
    syy = sum((y - my) ** 2 for y in ys) / (n + 1)
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n + 1)) / (n + 1)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = math.sqrt(max(0.0, tr * tr / 4.0 - det))
    l1, l2 = tr / 2.0 + disc, tr / 2.0 - disc
    return math.sqrt(max(0.0, l2) / l1) if l1 > 0 else 0.0


def max_frame_step(p):
    """产物里相邻两帧的最大单轴位移 / amplitude。超线就是「闪」不是「呼吸」。"""
    fs = frames(p)
    keys = fs[0][1].keys()
    return max(abs(b[1][k] - a[1][k]) for a, b in zip(fs, fs[1:])
               for k in keys) / p["cfg"]["amplitude"]


def edge_derivative(p):
    """端点一阶导的数值上界（相对幅度）：W=sin²(πu) 在两端是二阶小量。"""
    dt = EDGE_DERIVATIVE_DT
    head = max(abs(v) for v in state_at(dt, p).values())
    tail = max(abs(v) for v in state_at(p["duration"] - dt, p).values())
    return max(head, tail) / p["cfg"]["amplitude"]


def diagnose(p):
    """逐条列出没过的红线，给生成期报错用（空列表 = 全过）。

    `is_well_formed` 只回答「能不能出片」，报错要回答「改哪一个旋钮」——
    把 `cycles` 拉到 4.0 又给一条 0.8s 的短拍时，只说「没过自查」等于没说。
    """
    c = p["cfg"]
    end, beginning = landing(p), start(p)
    bad = []
    if not all(abs(end[k]) <= PRODUCT_EPS and abs(beginning[k]) <= PRODUCT_EPS
               for k in end):
        bad.append("端点没精确回原位")
    if len(end) != (2 if c["axis"] == "xy" else 1):
        bad.append("补间变量条数与 axis 不符")
    if max_deviation(p) > 1.0 + PRODUCT_EPS:
        bad.append("峰值偏移越过 amplitude")
    if edge_derivative(p) > EDGE_DERIVATIVE_EPS:
        bad.append("端点一阶导没归零")
    if not all(a > b for a, b in zip(p["spectrum"], p["spectrum"][1:])):
        bad.append("谐波谱没有严格衰减")
    if incommensurate_gap() < INCOMMENSURATE_MIN:
        bad.append("频率比退化为有理数（会整段重复）")
    if max_frame_step(p) > MAX_FRAME_STEP_RATIO:
        bad.append("逐帧位移过大，读成抖动（调小 cycles 或加长这一拍）")
    if len(frames(p)) != frame_count(p) + 1:
        bad.append("帧时刻撞进同一个 0.01s 格")
    if c["axis"] == "xy" and trajectory_isotropy(p) < MIN_ISOTROPY:
        bad.append("双轴轨迹共线（两轴相位退化成同相）")
    return bad


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。

    全部从 `state_at` / `frames` 反推，不复述配置——否则判据就成了
    「配置说了什么」的自证。单轴档没有轨迹可量，`MIN_ISOTROPY` 那条跳过。
    """
    return not diagnose(p)
