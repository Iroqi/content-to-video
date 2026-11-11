#!/usr/bin/env python3
"""定格档的单一真源：把「一张画停几帧」从渲染工程参数变成导演意图。

2026 年前沿 MG 有一股明确的回潮（见 references/step.md「一、对标」）：不再追求
120Hz 下的丝滑，而是**故意只画 12 / 8 张唯帧每秒**（animating on twos / on threes），
配上每帧时刻的细微不规则，复刻定格动画与手绘的有机节奏。它不是「降帧率」——
成片仍是 24fps 逐帧渲染，变的是**画面多久才换一张**。

已有档位把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度（`momentum`）、相位（`drag`）、静止（`hold`）、时序
（`stagger` / `decode`）。它们共同的前提是**连续、光滑、按满帧采样**：
`_momentum.frames` / `_drag.frames` 都按 `duration × fps` 生成一条 `tl.set`，
采样密度只是防产物膨胀的内部工程判据，作者碰不到。于是「这一拍只画几张」
在这套体系里根本无法表达——**采样域是空的**。

这一档不新增第十种位移语义：仍是一条 `f(p)`，仍落在 `distance`。多出来的
只有**时间轴上的一层量化**，全部闭式、全部是 `t` 的纯函数：

```
K = every                              每 K 个渲染帧才换一张画（on twos = 2）
M = ceil(D·F/K)                         台阶数 → M+1 张唯帧
q_j = min(j·K/(D·F), 1)                名义进度（不受抖动影响，q_M = 1）
w_j = normalize(raw_j − raw_0)·(1−j/M)²  双频不规则核，w_0 = w_M = 0
τ_j = min(j·K/F + timing·w_j/F, D)     每张画的显示时刻（抖动单位是"帧"）
s_j = D_amp · f(q_j)                    位置只认名义进度
```

三条设计决定，都是被成片纪律逼出来的：

1. **左连续阶梯，不是取整**。`state_at(t)` 取满足 `τ_j ≤ t` 的最大 j；台阶之间
   画面自己保持，不需要写第二份 set，也不需要渲染端补间。`t=0` 就是第 0 张，
   `t=D` 精确落在 `f(1)=1`——多写一条「补到终点」的 set 才是真的画蛇添足。
2. **抖动只进时间域，不进位置域**。位置若也抖，台阶本就少（M+1 常常只有十几张），
   落位前一张反向就是整段最显眼的错，且会与 `hold` 的活动度判据打架；前沿
   说的 "slight timing variations" 本来就是**什么时候换画**的不规则，不是
   「这一张画歪了多少」。`w_0 = w_M = 0` 让首尾时刻不漂，端点仍精确。
3. **不用 GSAP 的 `steps()` / `snap` / 运行期回调**。那些都要在渲染端再存一份
   实现，Python 侧判据只能退化成对着另一份 JS 自证（`decode.md` / `spring.md`
   记过这笔账）。沿用 `morph` / `momentum` / `drag` 的老路：**生成期把 M+1 张
   唯帧摊成离散 `tl.set`**，渲染端零脚本、零 `onUpdate`、逐帧 `seek()` 可复现。

与 `drag` 的关键差别是产物形态：`drag` 每帧写一条、条数由 `SAMPLES_MIN/MAX`
钳住；这一档**每 K 帧才写一条**，产物里能直接数出唯帧数——判据因此第一次
可以量「画面多久才换」，而不是只能量「每帧在哪儿」。这也是对仓库
「逐帧可复现」纪律的一次压力测试：把采样网格交到作者手里之后，仍要保证
同一 `t` 永远给同一张画。

红线写在消费侧（`validate`），契约层、渲染端、接续烘焙调的是同一个函数——
`--set` 或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里
静默变成一张不动的图。
"""
import bisect
import math

from _ease import curve, split_ease, validate_ease, reject_overshoot
# 「无过冲且终点速度归零」的曲线族、缺省曲线、产物精度，全部与拖曳档共用
# 同一份真源：两档都要求落位时速度与过冲同时归零、都把数值锁六位，各写
# 一份字面量就会改一处漏一处，而且不报错（`test_shared_geometry` 钉着）。
from _drag import (DEFAULT_EASE, PRODUCT_EPS,
                   _ALLOWED_FAMILIES as SETTLING_FAMILIES,
                   _ALLOWED_DIRECTIONS as SETTLING_DIRECTIONS)
# 两档的产物都是离散 tl.set，条数上限必须同档。step 的台阶数天然是
# 满帧数/every，不会比 momentum 的满帧采样更膨胀；直接复用常量，不在
# 本模块另写一份字面量。
from _momentum import SAMPLES_MAX as MAX_STEPS
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)

# ── 红线（工程判据，不进模板、不给 --set 改）──────────────────────────
# 每步帧数：1 就是满帧（没有定格可谈），>4 时 24fps 下只剩 6 张/秒，
# 观众读到的是逐张幻灯片而非「有节奏的卡」。前沿只做 on twos / threes，
# 这里留一档 fours 给强调拍。
EVERY_MIN = 2
EVERY_MAX = 4
ALLOWED_EVERY = frozenset({2, 3, 4})
# 最少唯帧数：2 张就是瞬跳，3 张才有「起 — 中 — 落」的定格节奏。
MIN_UNIQUE_FRAMES = 3
# 时间抖动上限（单位：渲染帧）。相邻两张的间隔是 K 帧，抖动差值最多
# 2×TIMING_MAX = 0.7 帧 < K ≥ 2 帧，所以 τ_j 必然严格递增——这条上界不是
# 审美，是单调性的充要条件。
TIMING_MAX = 0.35
# 不规则核的双频波长（单位：台阶数）。短于 3 会退化成逐帧交替的高频噪声，
# 长于 64 在常见拍长里根本走不完半个周期，等于没抖。
WAVELENGTH_MIN = 3
WAVELENGTH_MAX = 64
# 主运动缺省曲线 `DEFAULT_EASE` 与产物精度 `PRODUCT_EPS` 从 `_drag` 导入
# （见文件头），本模块不另写一份字面量。
DEFAULT_EVERY = 2
DEFAULT_TIMING = 0.18
DEFAULT_WAVELENGTH = 7
# 时刻也锁两位（html_renderer 用 round(pos + t, 2)）。两张唯帧若被压进
# 同一个 0.01 秒格，GSAP 只认最后一条，成片里就凭空少一张——高帧率下
# 这是真会发生的静默错，由 is_well_formed 在生成期拦住。
TIME_GRID = 0.01

_STEP_KEYS = frozenset({"axis", "distance", "ease", "every",
                        "timing", "wavelength"})


def validate(cfg, where="step"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象）。"""
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _STEP_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_STEP_KEYS)}")
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
    if (parts is None or parts[0] not in SETTLING_FAMILIES
            or parts[1] not in SETTLING_DIRECTIONS):
        raise ValueError(
            f"{where}.ease={ease!r} 不是无过冲的 out / inOut 档：定格档的"
            "台阶本来就少，带过冲或终点速度不归零的曲线会让最后两张画读成"
            "一次回弹（认得 power3.out / sine.inOut）")
    every = cfg.get("every", DEFAULT_EVERY)
    if not _finite(every) or float(every) not in ALLOWED_EVERY:
        raise ValueError(f"{where}.every={every!r} 越界：只认 "
                         f"{sorted(ALLOWED_EVERY)}（每几帧换一张画；1 是满帧、"
                         "没有定格可谈，>4 是幻灯片）")
    timing = cfg.get("timing", DEFAULT_TIMING)
    if not _finite(timing) or not (0.0 <= float(timing) <= TIMING_MAX):
        raise ValueError(f"{where}.timing={timing!r} 越界（0~{TIMING_MAX:.2f} "
                         "渲染帧）：再大就破坏显示时刻的严格递增")
    lam = cfg.get("wavelength", DEFAULT_WAVELENGTH)
    if not _finite(lam) or not (WAVELENGTH_MIN <= float(lam)
                                <= WAVELENGTH_MAX):
        raise ValueError(f"{where}.wavelength={lam!r} 越界"
                         f"（{WAVELENGTH_MIN}~{WAVELENGTH_MAX} 个台阶）")
    return {"axis": axis, "distance": float(d), "ease": ease,
            "every": int(every), "timing": float(timing),
            "wavelength": float(lam)}


def _weights(m, wavelength):
    """双频不规则核：w_0 = w_M = 0、|w_j| ≤ 1，且形状不是一条纯正弦。

    减去 `raw_0` 是为了让第 0 张的时刻不漂（否则首帧会晚/早于段首，接续
    页第 0 帧对不上）；乘 `(1−j/M)²` 让落位处抖动与它的一阶导同时归零
    ——与 `_drag._kernel` 同一条纪律，只不过那一档管的是位移、这一档管的
    是时刻。按 peak 归一才敢让 `timing` 直接当幅度上界用。
    """
    raw = [0.72 * math.sin(2.0 * math.pi * j / wavelength)
           + 0.28 * math.sin(2.0 * math.pi * j / (0.41 * wavelength) + 1.3)
           for j in range(m + 1)]
    base = raw[0]
    peak = max(abs(r - base) for r in raw)
    if peak < 1e-12:
        return [0.0] * (m + 1)
    return [((r - base) / peak) * (1.0 - j / m) ** 2
            for j, r in enumerate(raw)]


def plan(cfg, duration, fps, where="step"):
    """把一条 `step` 拍编译成 M+1 张唯帧的时刻与位置比。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur, f = plan_bounds(duration, fps, where, allow_equal_min=False, fps_floor=1.0)
    k = c["every"]
    m = math.ceil(dur * f / k)
    if m + 1 < MIN_UNIQUE_FRAMES:
        raise ValueError(
            f"{where} 的 duration={dur}s × fps={f:g} / every={k} 只采得出 "
            f"{m + 1} 张唯帧（至少 {MIN_UNIQUE_FRAMES} 张）：这一拍会退化成瞬跳，"
            "要么拉长 duration，要么改用普通 to 补间")
    if m > MAX_STEPS:
        raise ValueError(
            f"{where} 会写出 {m + 1} 条 tl.set（台阶上限 {MAX_STEPS}）：定格档"
            "不该用来演长镜头，缩短 duration 或改用普通 to 补间")
    # timing=0 的纯等距档连核都不必算：留一列 0 让判据能一眼看出「这一拍
    # 没有不规则」，而不是拿着一列非零权重去解释「它们恰好没被用上」。
    weights = (_weights(m, c["wavelength"]) if c["timing"] > 0
               else [0.0] * (m + 1))
    # 名义网格末帧夹到 dur：`dur` 很少正好是 K/F 的整数倍，末台阶本来就是余数。
    # 再给后面每张留一格 0.01 秒（产物时刻锁两位），否则余量很小时一次正抖动
    # 会把倒数第二张顶到 dur，与末张撞成同一时刻——GSAP 只认后写的那条，
    # 成片里凭空少一张画。
    times = [min(min(j * k / f, dur) + c["timing"] * weights[j] / f,
                 dur - (m - j) * TIME_GRID)
             for j in range(m + 1)]
    # 端点由核（w_0 = w_M = 0）与上式保证，这里再钉一次是对浮点残差的显式
    # 承诺：接续烘焙与判据都拿 times[0] / times[-1] 当段首与落位时刻。
    times[0] = 0.0
    times[-1] = dur
    q = [min(j * k / (dur * f), 1.0) for j in range(m + 1)]
    fn = curve(c["ease"], DEFAULT_EASE)
    return {"cfg": c, "duration": dur, "fps": f, "m": m,
            "times": times, "weights": weights, "q": q,
            "ratio": [float(fn(v)) for v in q]}


def state_at(t, p):
    """时刻 `t`（相对拍起点）显示的补间变量，**纯函数**。

    左连续阶梯：取 `τ_j ≤ t` 的最大 j。不读墙钟、不存上一帧——逐帧
    `seek()` 下每一帧都由 `t` 独立算出，这是本档能进成片的前提
    （与 `_momentum.state_at` / `_drag.state_at` 同一条纪律）。
    """
    j = bisect.bisect_right(p["times"], float(t)) - 1
    if j < 0:
        j = 0
    if j > p["m"]:
        j = p["m"]
    return {p["cfg"]["axis"]: p["cfg"]["distance"] * p["ratio"][j]}


def frames(p):
    """生成期采样：`[(相对秒, 补间变量), …]`，**每张唯帧一条** `tl.set`。

    与 momentum / drag 相反：那里是「每帧一条」、条数被钳在 SAMPLES_MIN/MAX；
    这里是「每 K 帧一条」、条数就是导演写的 `every`。台阶之间不写任何
    补间，GSAP 自然保持上一张——这正是 on twos 的画面。
    """
    out = []
    for j, t in enumerate(p["times"]):
        # `+ 0.0` 把首帧的 −0.0 收成 0.0：distance 为负时 `d × 0.0` 是 −0.0，
        # 写进 JSON 就是 `-0.0`，产物里凭空多一个负号没任何好处。
        value = round(p["cfg"]["distance"] * p["ratio"][j], 6) + 0.0
        out.append((round(t, 6), {p["cfg"]["axis"]: value}))
    return out


def unique_frames(p):
    """这一拍有几张唯帧（M+1）。产物里能直接数出来的那个量。"""
    return p["m"] + 1


def nominal_step(p):
    """名义台阶间隔（秒）：K/F。抖动是围绕它的偏移，不是另一个步长。"""
    return p["cfg"]["every"] / p["fps"]


def timing_deviation(p, j):
    """第 j 张相对名义网格的偏移（秒），用来量「手绘不规则」确实进了产物。

    基准是 `min(j·K/F, dur)`：末张的名义时刻已经是 dur（时长余数），把它
    当 `j·K/F` 会把余数读成一次巨大的负抖动，判据就成了谎话。
    """
    nominal = min(j * p["cfg"]["every"] / p["fps"], p["duration"])
    return p["times"][j] - nominal


def landing(p):
    """收尾态。跨段接续只搬 x/y，终点精确是 `distance`。"""
    return state_at(p["duration"], p)


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。

    除了端点与单调，还钉住一条这一档独有的静默错：时刻锁两位后若两张
    唯帧撞进同一个 0.01 秒格，GSAP 只认后写的那条，成片里凭空少一张画，
    而 HTML 与门禁都照样全绿（与 README 记的「全 0 项不是通过、是失明」
    同一类）。`timing=0` 的纯等距档没有不规则可钉，那条对它无意义——
    与 `_drag.is_well_formed` 对 `overshoot=0` 的处理同一条理由。
    """
    axis = p["cfg"]["axis"]
    d = p["cfg"]["distance"]
    ts = p["times"]
    r = p["ratio"]
    checks = [
        ts[0] == 0.0,
        ts[-1] == p["duration"],
        all(b > a for a, b in zip(ts, ts[1:])),
        # 上界：抖动最多让相邻两张拉开 2·timing/F；末台阶是时长余数，只会更短。
        all(b - a <= (p["cfg"]["every"] + 2 * p["cfg"]["timing"])
            / p["fps"] + TIME_GRID + 1e-9
            for a, b in zip(ts, ts[1:])),
        len({round(t, 2) for t in ts}) == len(ts),
        all(abs(w) <= 1.0 + 1e-9 for w in p["weights"]),
        abs(p["weights"][0]) < 1e-9 and abs(p["weights"][-1]) < 1e-9,
        all(b >= a - 1e-9 for a, b in zip(r, r[1:])),
        abs(r[0]) < 1e-9,
        abs(r[-1] - 1.0) < 1e-9,
        abs(state_at(0.0, p)[axis]) < 1e-9,
        abs(landing(p)[axis] - d) < 1e-6,
        abs(round(landing(p)[axis], 6) - round(d, 6)) <= PRODUCT_EPS,
        len(frames(p)) == unique_frames(p),
    ]
    if p["cfg"]["timing"] > 0 and any(abs(w) > 1e-9 for w in p["weights"]):
        checks += [
            max(abs(timing_deviation(p, j)) for j in range(len(ts)))
            <= p["cfg"]["timing"] / p["fps"] + 1e-9,
            any(abs(timing_deviation(p, j)) > 1e-9
                for j in range(1, len(ts) - 1)),
        ]
    return all(checks)
