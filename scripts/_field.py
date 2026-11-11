#!/usr/bin/env python3
"""生成期离散采样档的共用骨架。

各档（momentum / drag / step / organic / trail / wave / flow / lens / swarm）
都把「按 fps 采样成离散 tl.set」这套骨架各抄了一遍：数值钳位、六位小数归一
（含 -0.0 → 0.0）、采样帧数，以及**最核心的逐帧采样循环**（它曾在 wave /
flow / lens / swarm / trail 里各存一份，前四份逐字相同）。它们的注释甚至写着
「口径与 _trail / _wave 一致」——靠注释同步的拷贝，漏改一处就是逐帧对账失败。
这里收四个纯函数；`start` / `landing` / `is_well_formed` 是对本档 `state_at`
/ `diagnose` 的一行绑定，仍留在各档，抽走只会多一层间接。

常量沿用既有真源，不新开第二份：SAMPLES_* 在 _momentum，PRODUCT_DECIMALS 在
_organic。本模块被各档单向依赖，不反向 import 任何档。
"""
from _momentum import SAMPLES_MIN, SAMPLES_MAX  # noqa: F401  采样带真源
from _organic import PRODUCT_DECIMALS  # noqa: F401  锁位精度真源


def clamp(v, lo=0.0, hi=1.0):
    """把局部进度钳在 [lo, hi]。"""
    return lo if v < lo else (hi if v > hi else v)


def clean(v):
    """锁六位并把 -0.0 归一成 0.0。

    端点帧写出 -0.0 时 json.dumps 也真的会吐 "-0.0"，逐条对账与 0.0 不相等。
    """
    r = round(float(v), PRODUCT_DECIMALS)
    return 0.0 if r == 0.0 else r


def frame_count(p):
    """采样帧数：duration × fps，钳在共用采样带内。"""
    return max(SAMPLES_MIN, min(SAMPLES_MAX, round(p["duration"] * p["fps"])))


def sample_frames(p, state_at, *, transform=None):
    """逐帧采样成 `[(相对秒, 选择器, 补间变量), …]`，每帧每元素一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位；排序键是 (时刻, 元素下标)，不是 id 字面量
    ——写入顺序是作者给的元素序（导演意图），不能被字典序洗掉。两个样本撞进
    同一个 0.01s 格时保留最后一个：GSAP 在同一时刻按注册顺序执行，最后一条生效。

    `state_at(t, p, i)` 由各档提供，本模块不反向依赖任何档；`transform` 是
    可选的补间变量归一钩子（残影档用它锁六位精度）。采样口径改一次即可，
    不必再去五个档里各改一遍。
    """
    n = frame_count(p)
    acc = {}
    for k in range(n + 1):
        t = p["duration"] * k / n
        tr = round(t, 2)
        for i, sel in enumerate(p["selectors"]):
            v = state_at(t, p, i)
            acc[(tr, i)] = (sel, transform(v) if transform else v)
    return [(tr, sel, v) for (tr, _i), (sel, v) in sorted(acc.items())]
