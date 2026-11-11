#!/usr/bin/env python3
"""hold 编排的单一真源：静止窗口、2% 慢推，以及「这一帧主视觉在不在动」的口径。

代码绘制的 motion graphics 最容易被略过的一层不是曲线，是**时间结构**：快切之后
要留得住，hold 期间又不能真死。已有档位把"入场落位"做得很细（行程预算、弧线、
阶梯、快慢/疏密、跨段接续），但落位之后到被下一页盖住之前，主视觉**一帧都不动**——
`look.md` 原来那句「收拢之后图不再动」就是当时的设计决定。它换来边缘安全，也留下
`perceptual.dead_seconds` 抱怨的那一段：旁白还在讲，画面已成静帧。

这一模块不生成画面，只回答三件事，让生成期与判据用同一把尺：

1. `plan()`：**哪一段该静、哪一段该慢推**。静止窗口跟在入场收尾之后，慢推从静止
   窗口末尾起、到可见窗口末尾前留一帧收住；窗口不够长就整段不慢推（短段宁可全静，
   也不要一条被切掉一半的推镜）。
2. `scale_at()`：任意时刻的主视觉 scale，是 `t` 的**纯函数**。逐帧 seek 下不能靠
   上一帧的状态算这一帧——这条理由与 `params` 白名单禁 `random`、弹簧必须闭式解
   是同一条。
3. `frames()`：把逐帧变化量摊开，给"这一帧有没有在动"一个可测口径。

**为什么口径不是「同时在动的元素数」**：慢推期间永远只有一个元素在动（主视觉），
数元素区分不出"入场"和"hold"，也区分不出"静止"和"几乎没动"。所以量的是**主视觉的
单帧变化档**：入场首帧 ≥4px（换算到 980px 槽位约 0.0041 的 scale 差），hold 慢推
峰值 ≤ 它的 1/4，静止窗口恒为 0。三档用同一个量、同一把尺，才能被门禁钉住。

`1/4` 不是拍的，是反解出来的：24fps、2% 幅度的 `sine.inOut` 峰值速度是
`amp·(π/2)/(duration·fps)`，要求它 ≤ `4px/980px/4` 得 `duration ≥ 1.28s`。
**这个下限不写进模板，由判据算**——早先一版把它定成"比入场慢一个数量级"（1/10），
结果连默认那份 3.12s 的保持段都过不了（需要 3.21s），慢推整层被自己的判据关掉：
把"慢"当成"几乎不动"，读到的仍是一张静帧。

数值约束放在这里而不是模板里：模板只放"观感旋钮"（静止多久、推多少、哪条曲线），
红线（静止窗口 300~600ms、幅度 ≤2%、末帧留白）是工程判据，不该被 `--set` 顺手改掉。
"""
import math

from _ease import curve, split_ease

# 静止窗口的合法带（秒）。揭示前一拍落定后要留 300~600ms 让眼睛读清那一屏；
# 短于 300ms 读成"没停"，长于 600ms 又变成 dead beat。
STILL_MIN_SECONDS = 0.30
STILL_MAX_SECONDS = 0.60

# 慢推幅度上限。槽位 980px 宽，放大 2% 只从两侧各裁掉 1%（约 9.8px）；
# `look.md` 量过 4%~8% 的持续推近会把 SVG 边缘的配文推出槽位，2% 是留在安全
# 边距内的那一档。写成上限而不是"建议值"：再大就不是慢推，是又一次入场。
DRIFT_SCALE_MAX = 0.02

# 可见窗口是半开区间：补间结束态压在窗口右端时那一帧永不渲染，不报错，只是
# 少一拍。留一帧让终态真的落到画面上（与 `clips` 那次预处理同一条口径）。
TAIL_GUARD_FRAMES = 1

# 慢推的**峰值单帧变化**相对入场首帧的分母：≤ 1/4 才算"慢推"而不是"入场"。
# 4 是按默认帧率量出来的（见模块头）：写 10 会把 3.12s 的默认保持段也判掉。
MIN_DRIFT_RATIO = 4

# 慢推允许用的缓动族：必须是无过冲的连续变化档，且带 inOut 方向。
# 过冲族（back/elastic/bounce）、弹簧、steps 都不许进 hold——hold 没有"落位"
# 可看，冲过去再收回来只会被读成画面抖了一下。
_ALLOWED_FAMILIES = frozenset({
    "none", "linear", "power0", "power1", "power2", "power3", "power4",
    "quad", "cubic", "quart", "quint", "strong", "sine", "expo", "circ",
})


def validate(cfg):
    """模板里的 `animation.hold` 过一遍红线，坏值在生成期就炸，不留到成片。

    返回归一化后的配置 dict（不改传入对象）。`scale=0` 是**明确关掉这一层**，
    合法；其它越界值一律 ValueError——`--set` 能把观感旋钮调坏，所以护栏必须
    在消费侧，而不是指望写文档的人记得。
    """
    if not isinstance(cfg, dict):
        raise ValueError("animation.hold 必须是对象")
    still = float(cfg.get("stillSeconds", 0.0))
    if not (STILL_MIN_SECONDS <= still <= STILL_MAX_SECONDS):
        raise ValueError(
            f"animation.hold.stillSeconds={still:.3f}s 不在 "
            f"{STILL_MIN_SECONDS:.2f}~{STILL_MAX_SECONDS:.2f}s 的静止窗口带内："
            "太短读不出停顿，太长就是 dead beat")
    amp = float(cfg.get("scale", 0.0))
    if not (0.0 <= amp <= DRIFT_SCALE_MAX):
        raise ValueError(
            f"animation.hold.scale={amp:.4f} 越界（0~{DRIFT_SCALE_MAX:.2f}）："
            "超过 2% 会把 cover 之外的配文推出槽位；写 0 是关掉这一层")
    min_drift = float(cfg.get("minDriftSeconds", 0.0))
    if min_drift <= 0:
        raise ValueError("animation.hold.minDriftSeconds 必须 > 0：不留最短窗口"
                         "就会出现一条被切在半路的推镜")
    ease = str(cfg.get("ease") or "")
    parts = split_ease(ease)
    if (parts is None or parts[0] not in _ALLOWED_FAMILIES
            or parts[1] != "inOut"):
        raise ValueError(
            f"animation.hold.ease={ease!r} 不是无过冲的 inOut 档：hold 是持续变化，"
            "不许用过冲族、弹簧或 steps（认得的写法如 sine.inOut / power2.inOut）")
    return {"stillSeconds": still, "scale": amp,
            "minDriftSeconds": min_drift, "ease": ease}


def plan(settle_at, window_end, cfg, fps):
    """入场收尾 → 可见窗口末尾，切出「静止」与「慢推」两段。

    `settle_at` / `window_end` 都是**绝对秒**（相对片头），与 GSAP 时间线锚点同口径：
    前者是 `_entrance_lead` 的 `card.s + lead`，后者是 `card.win_start + card.vis`。
    两处各算一份窗口就会漂：慢推起手落在收拢还没演完的地方，读成"一次没停稳的
    二次入场"。

    窗口不够时返回 `None`——**整段不慢推**。下限取两者较大：模板声明的
    `minDriftSeconds`（作者能调的"别切半条推镜"），以及从"峰值单帧变化 ≤ 入场
    首帧 / 4"反解出来的 `min_drift_duration`（判据给的"别把慢推演成入场"）。
    后者随帧率与幅度走，写死一个秒数就会在换帧率时静默失守。短段里一条 0.1s
    的推镜只有两三帧，观众读到的不是"活着"，是"卡了一下"；宁可让它安静停住。

    返回 dict 的字段就是补间要写的全部数值，生成期不再另算第二份。
    """
    c = validate(cfg)
    if c["scale"] <= 0.0:
        return None
    fps = max(1.0, float(fps))
    tail = TAIL_GUARD_FRAMES / fps
    drift_start = float(settle_at) + c["stillSeconds"]
    drift_end = float(window_end) - tail
    duration = drift_end - drift_start
    need = max(c["minDriftSeconds"],
               min_drift_duration(c["scale"], fps, c["ease"]))
    if duration < need:
        return None
    return {
        "settle_at": float(settle_at),
        "still_start": float(settle_at),
        "still_end": drift_start,
        "drift_start": drift_start,
        "drift_end": drift_end,
        "duration": duration,
        "scale": c["scale"],
        "ease": c["ease"],
        "tail": tail,
        "min_duration": need,
    }


def scale_at(t, hold, ease_fn=None):
    """t 时刻主视觉的 scale，**纯函数**：不读墙钟、不存上一帧、无累计状态。

    三段闭式：静止窗口恒 1；慢推窗口 `1 + amp·f(p)`（f 走 `_ease.curve`，与仓库
    其余判据对同一条 GSAP 曲线）；窗口之后恒 `1 + amp`，让末态稳定落到收尾帧，
    而不是在片尾又滑一下。
    """
    if hold is None:
        return 1.0
    t = float(t)
    if t <= hold["drift_start"]:
        return 1.0
    if t >= hold["drift_end"]:
        return 1.0 + hold["scale"]
    f = ease_fn or curve(hold["ease"], "sine.inOut")
    p = (t - hold["drift_start"]) / hold["duration"]
    return 1.0 + hold["scale"] * float(f(max(0.0, min(1.0, p))))


def frames(hold, fps, ease_fn=None):
    """逐帧摊开主视觉的变化量——「这一帧有没有在动」的可测口径。

    返回 `{"still": n, "drift": n, "max_step": float, "mean_step": float}`：
    从入场收尾走到慢推收尾，每帧取 `|scale(t+1/fps) − scale(t)|`。静止窗口必须
    一帧都不动；慢推窗口的**最大单帧**要远小于入场首帧（980px 槽位上 4px ≈
    0.0041 的 scale 差），否则它就不是慢推而是又一次入场。

    `hold=None`（短段 / 关掉这一层）时返回全 0：旧行为在口径里是"整段静帧"，
    正好用来做反证——判据要能同时证明"该静的地方静了"和"该动的地方真动了"。
    """
    step_t = 1.0 / max(1.0, float(fps))
    if hold is None:
        return {"still": 0, "drift": 0, "max_step": 0.0, "mean_step": 0.0}
    f = ease_fn or curve(hold["ease"], "sine.inOut")
    t = hold["still_start"]
    still_n = drift_n = still_violations = 0
    steps = []
    while t + step_t <= hold["drift_end"] + 1e-9:
        delta = abs(scale_at(t + step_t, hold, f) - scale_at(t, hold, f))
        # 跨在静止窗口末尾的那一帧**归慢推**：它的终点已经在动了。按起点归类
        # 会把这条正常帧记成"静止窗口没空出来"，判据恒红（实测 1 条假阳性）。
        if t + step_t <= hold["drift_start"] + 1e-9:
            still_n += 1
            if delta > 1e-12:
                still_violations += 1
        else:
            drift_n += 1
            steps.append(delta)
        t += step_t
    active = [s for s in steps if s > 0]
    return {
        "still": still_n,
        "drift": drift_n,
        "max_step": max(steps, default=0.0),
        "mean_step": (sum(active) / len(active)) if active else 0.0,
        "still_violations": still_violations,
    }


# 入场首帧位移的硬判据（px）→ 980px 槽位上的 scale 差。与 `motion.md`
# 「曲线的行程预算」同一个数，别在这里另立一把尺。
ENTRY_FIRST_FRAME_PX = 4.0
SLOT_WIDTH_PX = 980.0


def entry_step_scale():
    """入场首帧 4px 折算成 scale 差，给"慢推不是入场"那道判据当分母。"""
    return ENTRY_FIRST_FRAME_PX / SLOT_WIDTH_PX


def _ease_slope(ease):
    """`max f'(p)` 的数值上界（512 段割线取最大），对钉固的 `_ease.curve` 采样。

    `sine.inOut` 解析值是 π/2；这里仍走采样器而不是写死 π/2，是为了让模板换成
    别的合法 `inOut` 档时，上界与成片用的是同一条曲线——两份实现互相对照，
    一起错的时候判据照样绿。
    """
    f = curve(ease, "sine.inOut")
    n = 512
    return max(abs(f((i + 1) / n) - f(i / n)) * n for i in range(n))


def min_drift_duration(amp, fps, ease):
    """从"峰值单帧变化 ≤ 入场首帧 / 4"反解出的**最短慢推窗口**（秒）。

    `amp·max f'/(duration·fps) ≤ (4px/980px)/R`  →
    `duration ≥ amp·max f'·R/(fps·(4/980))`。

    这就是 `plan()` 里那个不写进模板的下限：改幅度、改帧率、改缓动，它自己
    跟着变；写死 0.30s 的下限只管"别切半条推镜"，管不了"别把慢推演成入场"。
    """
    return (float(amp) * _ease_slope(ease) * MIN_DRIFT_RATIO
            / (max(1.0, float(fps)) * entry_step_scale()))


def max_step_bound(hold, fps):
    """慢推单帧变化量的解析上界：`amp · max f'(p) / (duration · fps)`。

    解析上界而不是抽样最大值——抽样只证明采到的那些帧，上界证明整段。
    """
    if hold is None:
        return 0.0
    return (hold["scale"] * _ease_slope(hold["ease"])
            / (hold["duration"] * max(1.0, float(fps))))


def is_slow_enough(hold, fps):
    """慢推峰值是否真的 ≤ 入场首帧的 1/MIN_DRIFT_RATIO（且有限）。"""
    bound = max_step_bound(hold, fps)
    return bound <= entry_step_scale() / MIN_DRIFT_RATIO + 1e-12 and math.isfinite(bound)
