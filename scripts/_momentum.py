#!/usr/bin/env python3
"""惯性档的单一真源：把「速度」从缓动曲线里取出来，变成可见的动效层。

已有档位把**位置**做透了：行程预算（首帧 ≥4px）、弧线（两轴异缓动）、入场阶梯、
快慢/疏密、受控弹簧、hold 慢推。但每一条补间写的都是「从哪到哪」——成片里物体
是被程序挪过去的，观众读不到发力、重量和落地的那一下。2026 年前沿 MG 与动画
十二原则反复点名的三件事，恰好都是**速度域**的：

1. **Anticipation（预备）**：主运动之前朝反方向蓄一下力；
2. **Squash & Stretch（挤压拉伸）**：形变跟着瞬时速度走，且**面积守恒**；
3. **Follow-through（跟随）**：主体落位后，形变晚半拍才收干净。

它们不是三条新补间，是**同一个速度包络的三种读法**。速度包络在这里取
`_ease.curve` 的一阶导——仓库早就把它逐点实采过、还用它钉了「行程预算」，
速度一直是一阶量，只是从来没被消费。本模块补上这一步，并且不再引入第二个
真源：曲线仍来自 `_ease`，红线仍只有一份。

**为什么不写成 GSAP 的 ease 扩展档**：ease 只能改位置曲线，改不了形状；而把
形变写进渲染端 `onUpdate` 又要多一份 JS 实现，Python 侧的判据就退化成「对着
另一份实现自证」（`decode.md`/`spring.md` 都记过这笔账）。这里走 `morph` 那条
老路：**生成期按帧率采样成离散 `tl.set` 关键帧**，渲染端零脚本、零 onUpdate、
逐帧 `seek()` 下每一帧都是时间的纯函数。

三段窗口（总长恰等于 step 级 `duration`，与 `_timeline.beat_positions` 同口径）：

```
0 ───────── a ───────────── a+d ──────── dur
  预备(蓄力)      冲程(位移)      跟随(回压)
  −A·sin(πu)      D·f(p)           D（不动）
  k=−0.35·s·sin   k=s·v(p)         k=−follow·sin(πu)
```

`v(p) = f'(p)/max f'` 是归一速度包络；`k` 是沿运动轴的拉伸比，
`scale_along = 1+k`、`scale_across = 1/(1+k)`，两者乘积恒为 **1**——
这是 squash & stretch 的第一红线：形变只能重分配面积，不能把物体吹胖。

**只收无过冲的 `inOut` 档**：`inOut` 的 `f'(0)=f'(1)=0`，速度包络两端自然归零，
形变在段首段尾连续。换 `.out` 档（`power3.out` 起点速度最大）会让 `k` 从预备段
末尾的 0 一步跳到峰值，读成「抽了一下」而不是「冲出去」。过冲族（back/elastic/
bounce）与弹簧的速度会反号，拉伸变成挤压，语义就翻了。
"""
import math

from _ease import curve, split_ease, validate_ease, reject_overshoot
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)

# ── 红线（工程判据，不进模板、不给 --set 改）────────────────────────────
# 预备幅度 / 行程：超过 1/3 就不是「蓄力」，是一次看得见的反向运动。
ANTICIPATION_MAX = 0.35
# 峰值拉伸比：30% 已接近矢量图形能接受的上限，再大圆角与字形会读成变形字。
STRETCH_MAX = 0.30
# 落位回压峰值：与拉伸同量级，超过它落地就读成「弹了两下」。
FOLLOW_MAX = 0.30
# 两段时长占总时长的比例。冲程因此固定占 66%，不会因旋钮调大而挤没。
ANTICIPATION_TIME_FRAC = 0.18
FOLLOW_TIME_FRAC = 0.16
# 预备段的压扁量 = 峰值拉伸的这个比例：蓄力是「压下去」，不是「拉长」。
ANTICIPATION_SQUASH = 0.35
# 采样帧数带：每帧一条 tl.set，下限保证短拍也看得出三段，上限防止长拍产物膨胀。
SAMPLES_MIN = 12
SAMPLES_MAX = 240
# 速度包络的割线采样数。与 `_hold._ease_slope` 同一量级，够钉住峰值位置。
SLOPE_SAMPLES = 512
AREA_EPS = 1e-9
# 产物写进 HTML 时数值锁六位（`_stage_carry._fmt` 同档），`scaleX·scaleY` 会带上
# 截尾误差。生成期判严格守恒，产物判这一档——两把尺不能混成一把，否则要么
# 把正确实现判红，要么把真不守恒的形变放进成片。
PRODUCT_AREA_EPS = 1e-5
DEFAULT_EASE = "sine.inOut"

# 允许的无过冲族。与 `_hold._ALLOWED_FAMILIES` 同一份口径，方向另锁 inOut。
_ALLOWED_FAMILIES = frozenset({
    "none", "linear", "power0", "power1", "power2", "power3", "power4",
    "quad", "cubic", "quart", "quint", "strong", "sine", "expo", "circ",
})
_MOMENTUM_KEYS = frozenset({"axis", "distance", "ease",
                            "anticipation", "stretch", "follow"})


def validate(cfg, where="momentum"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象）。

    契约层与渲染端调的是**同一个函数**：红线写在消费侧，`--set` 或手改过的
    images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变形。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _MOMENTUM_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_MOMENTUM_KEYS)}")
    axis = cfg.get("axis")
    if axis not in ("x", "y"):
        raise ValueError(f"{where}.axis 必须是 'x' 或 'y'（形变沿这一轴，"
                         f"面积守恒由另一轴补；实际: {axis!r}）")
    d = cfg.get("distance")
    if not _finite(d) or abs(float(d)) < 1e-9:
        raise ValueError(f"{where}.distance 必须是非零有限数（SVG 用户单位的"
                         f"主轴位移；实际: {d!r}）")
    ease = str(cfg.get("ease") or DEFAULT_EASE)
    validate_ease(ease, f"{where}.ease")
    reject_overshoot(ease, f"{where}.ease")
    parts = split_ease(ease)
    if (parts is None or parts[0] not in _ALLOWED_FAMILIES
            or parts[1] != "inOut"):
        raise ValueError(
            f"{where}.ease={ease!r} 不是无过冲的 inOut 档：速度包络两端必须归零，"
            "否则形变在段首段尾跳一下；过冲族与弹簧的速度会反号，拉伸读成挤压"
            "（认得的写法如 sine.inOut / power2.inOut）")
    names = (("anticipation", ANTICIPATION_MAX),
             ("stretch", STRETCH_MAX),
             ("follow", FOLLOW_MAX))
    out = {"axis": axis, "distance": float(d), "ease": ease}
    for name, limit in names:
        v = cfg.get(name, 0.0)
        if not _finite(v) or not (0.0 <= float(v) <= limit):
            raise ValueError(f"{where}.{name}={v!r} 越界（0~{limit:.2f}）："
                             f"{name} 是比例，不是像素")
        out[name] = float(v)
    return out


def _velocity_pack(ease):
    """`(f, v)`：位置曲线与归一速度包络，都是 `[0,1] → [0,1]` 的纯函数。

    `v` 取 `_ease.curve` 的**中心差分**导数，峰值恰好 1。不用割线数组：割线在
    区间起点带着 O(1/n) 的正斜率，`inOut` 档 `f′(0)=0` 被它读成 0.003，于是
    形变在段首不是从 0 起步（端点连续这条红线就形同虚设）。端点显式归零——
    这不是数值修饰，`inOut` 档的解析导数在两端本来就是 0。

    不写解析闭式（比如 `sine.inOut` 的 `π/2·sin(πp)`）：换族换参数时判据与成片
    必须走同一条曲线，另写一份就是两份互相对照的实现（`_hold._ease_slope`
    是同一条理由）。
    """
    f = curve(ease, DEFAULT_EASE)
    n = SLOPE_SAMPLES
    h = 1.0 / (2 * n)

    def deriv(p):
        if p <= 0.0 or p >= 1.0:
            return 0.0
        lo, hi = max(0.0, p - h), min(1.0, p + h)
        return (f(hi) - f(lo)) / (hi - lo)

    vmax = max(deriv(i / n) for i in range(n + 1))
    if not math.isfinite(vmax) or vmax <= 0:
        # validate 已把曲线族收窄到无过冲 inOut；这里是绕过契约层输入的最后兜底，
        # 与 `curve()` 自己「算不出就线性」的承诺对齐，不让整页渲染崩在这里。
        return (lambda t: t), (lambda p: 4.0 * p * (1.0 - p))

    def v(p):
        p = max(0.0, min(1.0, float(p)))
        if p <= 0.0 or p >= 1.0:
            return 0.0
        return min(1.0, max(0.0, deriv(p)) / vmax)

    return f, v


def plan(cfg, duration, fps, where="momentum"):
    """把一条 `momentum` 拍编译成三段窗口。生成期与判据共用这一份。

    `duration` 是 step 级时长（秒），`fps` 决定采样密度。返回值的字段就是
    渲染端写 `tl.set` 需要的全部数值，不在两处各算一遍。
    """
    c = validate(cfg, where)
    dur, fps_val = plan_bounds(duration, fps, where, allow_equal_min=False, fps_floor=1.0)
    a = dur * ANTICIPATION_TIME_FRAC
    fol = dur * FOLLOW_TIME_FRAC
    f, v = _velocity_pack(c["ease"])
    return {
        "cfg": c,
        "duration": dur,
        "anticipation_end": a,
        "drive_end": a + (dur - a - fol),
        "follow_end": dur,
        "drive_duration": dur - a - fol,
        "follow_duration": fol,
        "curve": f,
        "velocity": v,
        "fps": fps_val,
    }


def state_at(t, p):
    """时刻 `t`（相对拍起点）的补间变量，**纯函数**：不读墙钟、不存上一帧。

    逐帧 `seek()` 下每一帧都由 `t` 独立算出，这是本档能进成片的前提——
    与 `params` 白名单禁 `random`、弹簧必须闭式解、`hold.scale_at` 同一条纪律。
    """
    c = p["cfg"]
    axis = c["axis"]
    d = c["distance"]
    t = float(t)
    a = p["anticipation_end"]
    drive_end = p["drive_end"]
    if t <= 0.0 or t >= p["duration"]:
        disp = 0.0 if t <= 0.0 else d
        k = 0.0
    elif t < a:
        u = t / a
        disp = -math.copysign(c["anticipation"] * abs(d), d) * math.sin(math.pi * u)
        k = -ANTICIPATION_SQUASH * c["stretch"] * math.sin(math.pi * u)
    elif t < drive_end:
        q = (t - a) / p["drive_duration"]
        disp = d * float(p["curve"](q))
        k = c["stretch"] * float(p["velocity"](q))
    else:
        u = (t - drive_end) / p["follow_duration"]
        disp = d
        k = -c["follow"] * math.sin(math.pi * u)
    along = 1.0 + k
    across = 1.0 / along
    if axis == "x":
        return {"x": disp, "scaleX": along, "scaleY": across}
    return {"y": disp, "scaleX": across, "scaleY": along}


def frames(p):
    """生成期采样：`[(相对秒, 补间变量), …]`，每帧一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位（与 `morph` 的采样同一口径）；同一时刻
    撞在一起的样本保留最后一个——GSAP 在同一时刻按注册顺序执行，最后一条生效。
    """
    n = max(SAMPLES_MIN, min(SAMPLES_MAX, round(p["duration"] * p["fps"])))
    out = {}
    for i in range(n + 1):
        t = p["duration"] * i / n
        vars_ = state_at(t, p)
        out[round(t, 2)] = {k: round(float(v), 6) for k, v in vars_.items()}
    return sorted(out.items())


def area_error(p):
    """生成期 `max |scaleX·scaleY − 1|`：面积守恒的严格偏差。形变不许把物体吹胖。"""
    n = 256
    return max((abs(state_at(p["duration"] * i / n, p)["scaleX"]
                    * state_at(p["duration"] * i / n, p)["scaleY"] - 1.0)
                for i in range(n + 1)), default=0.0)


def product_area_error(p):
    """产物里那些 `tl.set` 的面积偏差（数值已截尾，容差另立一档）。"""
    return max((abs(v["scaleX"] * v["scaleY"] - 1.0)
                for _, v in frames(p)), default=0.0)


def peak(p):
    """峰值读数，给判据用：`拉伸峰值 / 速度峰值 / 预备与跟随段的形变量`。

    数值全部从 `state_at` 反推，不复述配置——否则判据就成了「配置说了什么」的
    自证。形变峰值必须落在**冲程**里且在中段（20%~80%），且比预备、跟随两段
    的形变量都大；否则「蓄力」或「落地」比主运动还夸张，读者会把三段读成三次动作。
    """
    n = 512
    drive = [(p["anticipation_end"] + p["drive_duration"] * i / n,
              state_at(p["anticipation_end"] + p["drive_duration"] * i / n, p))
             for i in range(n + 1)]
    axis = p["cfg"]["axis"]
    along_key = "scaleX" if axis == "x" else "scaleY"
    kt, kv = max(drive, key=lambda pair: pair[1][along_key])
    vt, _ = max(drive, key=lambda pair: p["velocity"](
        (pair[0] - p["anticipation_end"]) / p["drive_duration"]))
    # 峰值位置按**速度包络**读，不按形变峰值读：全 0 的纯位移档 k≡0，
    # `max(k)` 会返回第一个采样点，拿它算位置等于凭空造一个"峰值在起点"。
    frac = (vt - p["anticipation_end"]) / p["drive_duration"]

    def deviation(lo, hi):
        return max(abs(state_at(lo + (hi - lo) * i / n, p)[along_key] - 1.0)
                   for i in range(n + 1))

    return {
        "peak_t": kt, "peak_k": kv[along_key] - 1.0, "velocity_t": vt,
        "drive_frac": frac,
        "anticipation_k": deviation(0.0, p["anticipation_end"]),
        "follow_k": deviation(p["drive_end"], p["duration"]),
    }


def landing(p):
    """收尾态。跨段接续只搬 `x`/`y`，尺寸必须精确回到 1。"""
    return state_at(p["duration"], p)


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。

    `stretch=0` 的纯位移档没有形变峰值，"峰值落在中段 / 与速度峰值对齐"两
    条对它无意义——硬判会把一条完全合法的拍在生成期拦掉。其余判据照旧。
    """
    pk = peak(p)
    end = landing(p)
    cfg = p["cfg"]
    axis = cfg["axis"]
    checks = [
        area_error(p) <= AREA_EPS,
        product_area_error(p) <= PRODUCT_AREA_EPS,
        pk["peak_k"] + 1e-9 >= pk["anticipation_k"],
        pk["peak_k"] + 1e-9 >= pk["follow_k"],
        abs(end[axis] - cfg["distance"]) < 1e-6,
        abs(end["scaleX"] - 1.0) < 1e-6,
        abs(end["scaleY"] - 1.0) < 1e-6,
    ]
    if cfg["stretch"] > 0:
        checks += [
            0.20 <= pk["drive_frac"] <= 0.80,
            # 形变峰值与速度峰值是同一个量：两者错开就说明渲染端另写了一份包络
            abs(pk["peak_t"] - pk["velocity_t"]) <= 2 / p["fps"],
        ]
    return all(checks)
