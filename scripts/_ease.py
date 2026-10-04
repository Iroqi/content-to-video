#!/usr/bin/env python3
"""缓动档：钉固的 GSAP core 里到底注册了哪些 ease，以及生成期采样用的同族曲线。

**为什么这个模块存在**：`ease` 是被拼进 GSAP 补间变量的字符串，认不出来的档 GSAP 不报错
（它退回缺省缓动），作者以为演的是"弹一下落定"，成片里是"平稳落定"。整页画布越排越多，
这种无声退化就是"动画看着不对但说不清哪儿不对"的主要来源。所以这里放两样东西：一份**档名
目录**（契约层按它 fail-fast）和一条**采样曲线**（morph 的中间态由生成期定义，因为 GSAP
不会对 `tl.set` 再缓动）。两者必须同源——目录放行而采样器不认的档，morph 会静默变线性。

**目录怎么来的**：不是照 GSAP 文档抄的，是拿技能实际钉固的那一份 `gsap.min.js`
（`gen_hyperframes.GSAP_SHA256`）在 Node 里逐个 `gsap.parseEase(name)` 试出来的。
所以 `rough` / `slow` / `stepped` 这些**不在目录里**：它们属于 EasePack 之类的插件文件，
core dist 里没有，写了就是无声退化。换 GSAP 版本时重跑一次对拍，别凭记忆改这张表。
"""
import math
import re

# GSAP 3.14.2 core dist 实测可解析的缓动族名（小写）。别名并入：strong=power4、
# quad/cubic/quart/quint = power1..power4 的旧名，power0 = linear。
EASE_FAMILIES = frozenset({
    "none", "linear", "power0", "power1", "power2", "power3", "power4",
    "quad", "cubic", "quart", "quint", "strong", "sine", "expo", "circ",
    "back", "elastic", "bounce", "steps",
})

# ease 串的形状：族名 [(参数)] [.方向] [(参数)]，两个位置都能带括号（GSAP 实测
# `steps(4).in`、`back.out(1.7)`、`elastic.inOut(1,0.3)` 都解析得出）。大小写与 GSAP
# 的旧类名写法（`Bounce.easeOut`）一起收。
_EASE_RE = re.compile(r"^([a-zA-Z0-9]+)(?:\(([^)]*)\))?(?:\.([a-zA-Z]+))?(?:\(([^)]*)\))?$")
_MODS = {"": "out", "in": "in", "out": "out", "inout": "inOut", "both": "inOut",
         "easein": "in", "easeout": "out", "easeinout": "inOut"}
# 旧名 → 采样器用的族名（GSAP 对这两个都解析，实测）
_FAMILY_ALIAS = {"strong": "power4"}

EASE_HELP = ("认得的档：" + "、".join(sorted(EASE_FAMILIES))
             + "；写法 power2.out / back.out(1.7) / elastic.out(1,0.3) / steps(4)，"
               "方向 .in/.out/.inOut，裸名按 .out。"
               "rough/slow/CustomEase 这类要 EasePack 或插件，core 里没有，写了会静默退化")


def split_ease(spec):
    """ease 串 → (族名, 方向, 参数列表)；形状不对返回 None。

    方向缺省按 GSAP 的语义补 `.out`（裸 `power2` 实测等于 `power2.out`）。参数原样留着
    字符串，由调用方按族解释——`steps(4)` 要整数、`elastic.out(1,0.3)` 是振幅与周期。
    """
    if not isinstance(spec, str):
        return None
    m = _EASE_RE.match(spec.strip())
    if not m:
        return None
    family = m.group(1).lower()
    mod = _MODS.get((m.group(3) or "").lower())
    if mod is None:
        return None
    raw = (m.group(4) or m.group(2) or "").strip()
    args = [a.strip() for a in raw.split(",") if a.strip()]
    return _FAMILY_ALIAS.get(family, family), mod, args


def ease_ok(spec):
    """这一档名 GSAP core 认不认（契约层的判据）。"""
    parts = split_ease(spec)
    if parts is None or parts[0] not in EASE_FAMILIES:
        return False
    # `none` 是 GSAP 的直通档，带方向就解析不出来（实测 none.in / none.out 全是 NO），
    # 而解析失败不报错、静默换成缺省缓动——所以这里也判它非法。
    if parts[0] == "none":
        return "." not in spec
    # `steps` 反过来：裸 `steps` 和 `steps(4).in` 都解析，唯独 `steps.in` 不（实测）——
    # 要方向就得给档数。同一条静默退化，同一个判据层挡掉。
    if parts[0] == "steps":
        return bool(parts[2]) or "." not in spec
    return True


def validate_ease(spec, where):
    """契约层校验：档名不在目录里就 fail-fast，绝不留成"成片里悄悄换成别的缓动"。"""
    if not isinstance(spec, str):
        raise ValueError(f"{where} 必须是字符串（实际: {spec!r}）")
    if ease_ok(spec):
        return spec
    parts = split_ease(spec)
    if parts is None:
        raise ValueError(f"{where} {spec!r} 不是合法缓动写法。{EASE_HELP}")
    if parts[0] not in EASE_FAMILIES:
        raise ValueError(f"{where} 的族名 {parts[0]!r} 不在钉固的 GSAP core 里。{EASE_HELP}")
    if parts[0] == "none":
        raise ValueError(f"{where} {spec!r}：none 是直通档，GSAP 不认它的方向后缀，写 \"none\" 就好")
    raise ValueError(f"{where} {spec!r}：steps 要带方向就必须给档数，写 \"steps(4).in\" 那样"
                     "（裸 steps.in 实测解析不出来，GSAP 会静默换成别的缓动）")


def _bounce_out(t):
    """标准弹跳（衰减在末端），与 GSAP Bounce.easeOut 逐点一致。"""
    n, d = 7.5625, 2.75
    if t < 1 / d:
        return n * t * t
    if t < 2 / d:
        t -= 1.5 / d
        return n * t * t + 0.75
    if t < 2.5 / d:
        t -= 2.25 / d
        return n * t * t + 0.9375
    t -= 2.625 / d
    return n * t * t + 0.984375


def _num(args, i, default):
    try:
        return float(args[i])
    except (IndexError, ValueError):
        return default


def curve(name, default):
    """给定 ease 名返回曲线 f:[0,1]→[0,1]，morph 生成期按它采样形状进度。

    `default` 取模板 `animation.director.ease`，让 morph 的缺省手性与兄弟补间一致。
    族名已由 `validate_ease` 在契约层过了一遍，这里仍留线性兜底（渲染端不该因为一条
    算不出的曲线抛断整页）。

    与 GSAP 的一致性是对拍出来的（见模块头）：同族同参数时逐点一致（含裸名缺省——
    裸 `elastic.inOut` 的缺省周期是 .45、`amplitude<1` 时周期按 1/振幅 放大，均照 GSAP
    `_configElastic` 源码实现，实测与钉固 bundle 逐点一致），`expo` 差在 GSAP 对端点的
    截断（≤0.008）。不追求逐字节同式——GSAP 不会对 `tl.set` 再缓动，这条曲线就是成片里的
    那一条。
    """
    parts = split_ease(name or default or "none")
    if parts is None:
        return lambda t: t
    family, mod, args = parts
    if family in ("none", "linear", "power0"):
        return lambda t: t

    if family == "steps":
        n = int(_num(args, 0, 4))
        if n < 1:
            return lambda t: t
        # GSAP 的 SteppedEase 把跳变放在 t=i/(n+1)（实测 steps(4) 在 0.2 就跳），
        # 不是直觉上的 floor(t·n)/n；带第二个参数（steps(4,true)）时整体提前一格。
        # 照实测写，morph 的停顿点才和同名补间那一拍对得上。
        if len(args) > 1 and args[1].lower() in ("true", "1"):
            return lambda t, n=n: 1.0 if t >= 1 else min(math.floor(t * n) + 1, n) / n
        return lambda t, n=n: 1.0 if t >= 1 else min(math.floor(t * (n + 1)), n) / n

    if family == "elastic":
        # GSAP `_configElastic`（钉固 3.14.2 源码逐行对过）：
        #   p1 = amplitude >= 1 ? amplitude : 1
        #   p2 = (period || (type ? .3 : .45)) / (amplitude < 1 ? amplitude : 1)
        # 缺省周期按方向走：in/out 是 .3，inOut 是 .45（裸 `elastic.inOut` 实测≡(1,0.45)，
        # 不是 (1,0.3)——GSAP 注册 inOut 时 type 为空，走了 .45 分支）；振幅不足 1 时 p1
        # 钳到 1、周期按 1/振幅 放大，等价于 (1, p/a)（实测 (0.6,0.18)≡(1,0.3)）。
        a = _num(args, 0, 1.0)
        default_p = 0.45 if mod == "inOut" else 0.3
        p = _num(args, 1, default_p)
        if not p or p != p:             # GSAP 的 `period || 缺省`：0/NaN 一律回落
            p = default_p
        p1 = a if a >= 1 else 1.0
        per = p / (a if a < 1 else 1.0)
        s = per * math.asin(1.0 / p1) / (2 * math.pi)

        def g(t, a=p1, p=per, s=s):
            if t <= 0:
                return 0.0
            if t >= 1:
                return 1.0
            return -a * (2 ** (10 * (t - 1))) * math.sin((t - 1 - s) * (2 * math.pi) / p)
    elif family == "bounce":
        g = lambda t: 0.0 if t <= 0 else (1.0 if t >= 1 else 1 - _bounce_out(1 - t))
    elif family.startswith("power"):
        # 次数来自族名尾巴的数字（power1=平方 … power4=五次），裸 power 按 power2 兜
        tail = family[5:]
        exp = (int(tail) if tail.isdigit() else int(_num(args, 0, 2))) + 1
        g = lambda t, e=exp: t ** e
    elif family in ("quad", "cubic", "quart", "quint"):
        exp = {"quad": 2, "cubic": 3, "quart": 4, "quint": 5}[family]
        g = lambda t, e=exp: t ** e
    elif family == "sine":
        g = lambda t: 1 - math.cos(t * math.pi / 2)
    elif family == "expo":
        g = lambda t: (2 ** (10 * (t - 1))) if t else 0.0
    elif family == "circ":
        g = lambda t: 1 - math.sqrt(max(0.0, 1 - t * t))
    elif family == "back":
        c1 = _num(args, 0, 1.70158)          # 过冲量；back.out(1.7) 与裸 back.out 同档
        c3 = c1 + 1
        g = lambda t, c1=c1, c3=c3: c3 * t ** 3 - c1 * t ** 2
    else:
        return lambda t: t

    if mod == "in":
        return g
    if mod == "out":
        return lambda t, g=g: 1 - g(1 - t)
    return lambda t, g=g: (0.5 * g(2 * t)) if t < 0.5 else (1 - 0.5 * g(2 - 2 * t))
