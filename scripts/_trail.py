#!/usr/bin/env python3
"""残影档的单一真源：把「一帧一张画」变成「一帧多张时间回声」。

已有档位把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度（`momentum`）、相位（`drag`）、静止（`hold`）、时间量化
（`step`）、时序（`stagger` / `decode`）、空间曲率（`arc`）、值域不规则
（`organic`）。它们还有一条从没人写破的共同前提：**每个时刻，一个主体只有
一个状态**。即使 `drag` 让一条链同时可见，链上每一节也是不同的真实元素、各自
演一条自己的曲线；即使 `step` 让一张画停 K 帧，那一帧里也只有一张画。

2025/2026 的前沿 MG 恰好在打这条前提：ACM MIG'25 的 *Trajectory-aware Smears
for Stylized 3D Animations* 把「沿轨迹拉长的中间画 / smear frames」当成正式的
运动语言（Spider-Verse、Arcane 那一支），2026 的 motion-trails 工作流把它写成
「复制图层若干份、每份错开 2~5 帧、逐层降透明度」，2025 的 *Time Echo /
ghosting* 又把它从技术瑕疵翻成叙事工具（时间回声、残像、长曝光字体）。它们的
共同手感不是「一个东西在动」，而是**同一个主体的几个过去时刻同时留在画面上**。
这套体系里根本写不出来：`repeat` 是顺序重放不是同时叠加，`stagger` 是不同元素
各自起跑，`drag` 是不同元素共享一条曲线。

本档负责**时间叠加域**，与 `step` 正好互为镜像：
`step` 把「一帧一张画」变成「一张画占几帧」；`trail` 把它变成「一帧里有多张
画」。两者都在时间域动手，但一个减少每帧的新信息、一个增加每帧的可见状态。

```
u   = t / D                                主体进度
f   = curve(ease)                          钉固的无过冲缓动，f(0)=0, f(1)=1
主体  x₀(t) = distance · f(u)
影子  uᵢ = clamp((t − i·lag)/D, 0, 1)        i = 1..K，**不重参数化**
      xᵢ(t) = distance · f(uᵢ)          ← xᵢ(t) = x₀(t − i·lag)  时间回声恒等式
W(u) = sin²(πu)                           端点窗口：W(0)=W(1)=W′(0)=W′(1)=0
G(u) = W(u)·f′(u) / max[W·f′]             速度门：端点精确归零，峰值归一到 1
      oᵢ(t) = decayⁱ · G(u)               整串共用一个速度门，层间只差 decayⁱ
```

`f′` 由中心差分取得（不另写一份解析曲线）；`G` 的峰值在生成期扫一次算死，
所以第一层影子最亮就是 `decay`（相对静态可见的主体 opacity=1），后面按
`decayⁱ` 递减。若让 `W` 与归一速度各自再乘一次，两个门会叠成 0.14 量级的
「几乎看不见的拖尾」——归一到峰值 1 才是「门」，不是两级衰减。

**三条设计决定，都是被成片纪律逼出来的**：

1. **故意不重参数化**。`drag` 必须把 `uᵢ` 除以 `(1−φᵢ)`，否则链尾永远停在半路、
   全链不能一起落定；残影恰好相反——它是**过去**，永远不该追上现在。若也做
   重参数化，`xᵢ(1)=distance`，落定那一帧仍有 K 张画叠在主体上，读成一坨实心
   影子而不是速度感。这一条是两档最本质的分界，也是唯一能让「收尾时影子全部
   隐去」成立的做法。
2. **速度门控整串、衰减只管层间**。`oᵢ` 里的 `G` 只由主体当前时刻决定：主体
   起步那一瞬 `uᵢ=0`，影子与主体重合、门为 0，本来也不该看见；主体跑起来门开到
   峰值，一串影子才拉开；主体落定时 `W` 与 `f′` 一起收零，影子同时收干净。若让
   每层各自读 `G(uᵢ)`，层间大小就不再只由 `decay` 决定，「逐层递减」这条判据会随
   ease 漂移。
3. **端点窗口而不是淡入淡出**。`W=sin²(πu)` 让 `oᵢ(0)=oᵢ(1)=0` **精确**成立：
   切过来不会先挂一串影子，页界处也不会把残影留给下一段。这一档是继 `organic`
   之后第二个「收尾态 = 起始态」对影子成立的档，但主体本身有终点位移，所以跨段
   接续仍需显式折一次（见下）。

**生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step` /
`organic` 的老路：渲染端零脚本、零 `onUpdate`、零依赖，`ease` 只在生成期读，
成片里是一串确定的数值，逐帧 `seek()` 下每一帧都是时间的纯函数。用 AE 的 Echo
效果、运行时 `onUpdate` 叠影或低通滤波都会让 Python 侧判据退化成对着另一份 JS
自证（`decode.md` / `spring.md` 都记过这笔账）。

**影子必须是图里真实存在的元素**：离散 `tl.set` 没法凭空造 DOM，也不该在渲染端
克隆节点（id 重复、`<defs>` 引用只认第一份，见 `_stage_carry` 模块头）。所以
`ghosts` 收一列有序 `#id`，由作者在图里画好同形副本并静态写 `opacity="0"`；
`_animate` 按文件逐个拦，落空一层就是「这条拖尾短了一截」而不是报错。

**跨段接续**：主体收尾精确落在 `distance`，影子的收尾可见度精确为 0。`_stage_carry`
必须显式认这一拍并**只给影子折 `opacity:0`**——不认的话，下一页第 0 帧会挂着
一串看不见但确实在那里的影子，本段若再写一条 trail，第一帧才把它们收掉，页界
处是一次静默的闪。这是本档独有的跨页错，与 `drag` 的「链尾弹回原位」同构。

红线写在消费侧（`validate`）：契约层、渲染端、接续烘焙调的是同一个函数，
`--set` 或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变成
一坨不动的复制品。
"""
import math
import re

# ease 白名单与采样带都直接复用既有档的真源：主运动与 drag 同样是「一条无过冲、
# 终点速度归零的 f(p)」，认得的族与方向没有理由分家；采样密度与产物精度更是
# 全仓库共用的同一条工程判据（test_shared_geometry 钉着这条纪律）。
from _ease import curve, split_ease, validate_ease, reject_overshoot
from _drag import (PRODUCT_EPS, SCAN_SAMPLES, DEFAULT_EASE,
                   _ALLOWED_FAMILIES, _ALLOWED_DIRECTIONS)
from _organic import MIN_DURATION
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)
from _field import (clamp as _clamp, clean as _clean, frame_count,  # noqa: E402  采样骨架只有一份
                    sample_frames)

# ── 红线（工程判据，不进模板、不给 --set 改）────────────────────────────
# 影子层数。前沿工作流的口径是 2~5 份副本；1 层读不出「一串」，多于 6 层在
# 24fps 下每帧 8 条以上的 tl.set，观众也数不出层次。
GHOSTS_MIN = 2
GHOSTS_MAX = 6
# 层间滞后上限（秒）。0.25s 在 24fps 是 6 帧，再大两层之间就断了，读成两个
# 先后独立的动作，不是一条拖尾。下界由 fps 反解（lag ≥ 1/fps），不写死在这儿。
LAG_MAX = 0.25
# 总滞后 / 拍长上限。lag·K 逼近 D 时，最后一层影子到落定还几乎没动过，
# 画面上是一串停在起点的复制品。
LAG_SPAN_MAX = 0.75
# 逐层可见度比上限。0.85 往上各层亮度太接近，一串影子读成一坨实心块；
# 下界不设 0（decay=0 是合法的「只有第一层」），但必须 > 0 才有「逐层」可言。
DECAY_MAX = 0.85
# MIN_DURATION 自 _organic 导入，DEFAULT_EASE 自 _drag 导入，采样骨架自 _field 导入：
# 它们是全仓库共用的同一条工程判据，写成第二份字面量就会改一处漏一处，
# 且 test_shared_geometry 会当场拦下。本档独有的红线只有上面这几条。
# 速度门的中心差分半窗。与扫描密度同量级，够稳且不引入第二套曲线实现。
SPEED_DT = 1.0 / 512.0

DEFAULT_LAG = 0.08
DEFAULT_DECAY = 0.62

_TRAIL_KEYS = frozenset({"ghosts", "axis", "distance", "lag", "decay", "ease"})
# 影子只认单个 #id：一组同 class 的元素没法用离散 tl.set 分别写值。
_GHOST_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")


def validate(cfg, where="trail"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象）。

    契约层与渲染端调的是**同一个函数**：红线写在消费侧，`--set` 或手改过的
    images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变形。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _TRAIL_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_TRAIL_KEYS)}")
    ghosts = cfg.get("ghosts")
    if not isinstance(ghosts, list) or not (GHOSTS_MIN <= len(ghosts) <= GHOSTS_MAX):
        raise ValueError(f"{where}.ghosts 必须是 {GHOSTS_MIN}~{GHOSTS_MAX} 个 #id 的"
                         f"有序列表（图里画好的同形影子副本；实际: {ghosts!r}）")
    for sel in ghosts:
        if not isinstance(sel, str) or not _GHOST_ID_RE.match(sel):
            raise ValueError(f"{where}.ghosts 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(ghosts)) != len(ghosts):
        raise ValueError(f"{where}.ghosts 不能有重复 id：同一元素领两份滞后会被"
                         "后写的那条静默覆盖")
    axis = cfg.get("axis")
    if axis not in ("x", "y"):
        raise ValueError(f"{where}.axis 必须是 'x' 或 'y'（实际: {axis!r}）")
    d = cfg.get("distance")
    if not _finite(d) or abs(float(d)) < 1e-9:
        raise ValueError(f"{where}.distance 必须是非零有限数（SVG 用户单位的"
                         f"主体行程；实际: {d!r}）")
    lag = cfg.get("lag", DEFAULT_LAG)
    if not _finite(lag) or not (0.0 < float(lag) <= LAG_MAX):
        raise ValueError(f"{where}.lag 必须在 (0, {LAG_MAX:.2f}] 秒之间（每层"
                         f"错开多久；实际: {lag!r}）")
    decay = cfg.get("decay", DEFAULT_DECAY)
    if not _finite(decay) or not (0.0 < float(decay) <= DECAY_MAX):
        raise ValueError(f"{where}.decay 必须在 (0, {DECAY_MAX:.2f}] 之间（逐层"
                         f"可见度比；实际: {decay!r}）")
    ease = str(cfg.get("ease") or DEFAULT_EASE)
    validate_ease(ease, f"{where}.ease")
    reject_overshoot(ease, f"{where}.ease")
    parts = split_ease(ease)
    if (parts is None or parts[0] not in _ALLOWED_FAMILIES
            or parts[1] not in _ALLOWED_DIRECTIONS):
        raise ValueError(
            f"{where}.ease={ease!r} 不是无过冲的 out / inOut 档：影子必须严格"
            "落在主体走过的轨迹上，过冲族会让影子跑到主体前面去（认得 "
            "power3.out / sine.inOut）")
    return {"ghosts": list(ghosts), "axis": axis, "distance": float(d),
            "lag": float(lag), "decay": float(decay), "ease": ease}


def _raw_speed(u, f):
    """`f` 在 u 处的中心差分斜率。端点自动收成单边差分，不引入状态。"""
    lo, hi = max(0.0, u - SPEED_DT), min(1.0, u + SPEED_DT)
    if hi <= lo:
        return 0.0
    return (float(f(hi)) - float(f(lo))) / (hi - lo)


def _core(cfg, duration, target, where="trail"):
    """与 fps 无关的轨迹内核。plan 与跨段接续共用这一份，不各写一遍公式。"""
    c = validate(cfg, where)
    dur = plan_duration(duration, where, min_duration=MIN_DURATION)
    f = curve(c["ease"], DEFAULT_EASE)
    # 速度门 G = W·f′ 的峰值。窗口与速度各自归一再相乘会叠成两级衰减，
    # 影子暗到看不见；这里只归一一次，G 的峰值恰为 1。
    peak = max(_window(k / SCAN_SAMPLES) * _raw_speed(k / SCAN_SAMPLES, f)
               for k in range(SCAN_SAMPLES + 1))
    if peak <= 0.0:
        raise ValueError(f"{where} 的 ease={c['ease']!r} 采不出速度门")
    return {"cfg": c, "duration": dur, "target": target, "curve": f,
            "gate_peak": peak,
            "selectors": [target] + list(c["ghosts"])}


def plan(cfg, duration, fps, target, where="trail"):
    """把一条 `trail` 拍编译成可采样的计划。生成期与判据共用这一份。"""
    p = _core(cfg, duration, target, where)
    _, f = plan_bounds(p["duration"], fps, where)
    p["fps"] = f
    c = p["cfg"]
    k = len(c["ghosts"])
    if c["lag"] < 1.0 / f - 1e-12:
        raise ValueError(f"{where} 的 lag={c['lag']}s 小于一帧（1/{f:.0f}fps = "
                         f"{1.0 / f:.4f}s）：相邻两层会撞进同一渲染帧，"
                         "残影读成一张叠影")
    if k * c["lag"] > LAG_SPAN_MAX * p["duration"] + 1e-12:
        raise ValueError(f"{where} 的总滞后 {k}×{c['lag']}s 超过拍长的 "
                         f"{LAG_SPAN_MAX:.0%}：最后一层影子到落定还几乎没动过，"
                         "读成一串停在起点的复制品（调小 lag 或加长这一拍）")
    return p


def _gate(u, p):
    """速度门 G(u) = W(u)·f′(u) / peak，端点精确 0、峰值 1。

    整串影子共用一个门：主体起步时影子与它重合、门为 0；跑到最快时门开到
    1；落定时 W 与 f′ 一起收零。层与层的亮度差只由 `decayⁱ` 给。
    """
    return _clamp(_window(u) * _raw_speed(u, p["curve"]) / p["gate_peak"])


def _window(u):
    """端点窗口 sin²(πu)：显式短路端点，避免浮点残差写进产物。"""
    if u <= 0.0 or u >= 1.0:
        return 0.0
    return math.sin(math.pi * u) ** 2


def state_at(t, p, i):
    """第 i 层在时刻 `t`（相对拍起点）的补间变量，**纯函数**。

    i=0 是主体（只写主轴位移，静态可见度由作者在图里给定）；i≥1 是影子，
    多写一条 opacity。不读墙钟、不存上一帧：逐帧 `seek()` 下每一帧都由 `t`
    独立算出，这是本档能进成片的前提。
    """
    if not 0 <= i < len(p["selectors"]):
        raise ValueError(f"层下标 {i} 越界（含主体共 {len(p['selectors'])} 层）")
    c = p["cfg"]
    axis, d, dur = c["axis"], c["distance"], p["duration"]
    # 先把时刻夹回这一拍，再算主体与影子的进度：只夹 u 与 uᵢ 的话，
    # t>D 时影子的 uᵢ 会被夹到 1（= 主体终点），与 t=D 那一帧收尾态不同——
    # 逐帧 seek 跨过窗口就会突然多走一截，正是本档要消灭的静默错。
    t = _clamp(float(t), 0.0, dur)
    u = t / dur
    if i == 0:
        return {axis: d * float(p["curve"](u))}
    u_i = _clamp((t - i * c["lag"]) / dur)
    opacity = (c["decay"] ** i) * _gate(u, p)
    return {axis: d * float(p["curve"](u_i)),
            "opacity": _clamp(opacity)}


def frames(p):
    """生成期采样：`[(相对秒, 选择器, 补间变量), …]`，每帧每层一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位；排序键是 (时刻, 层下标)，不是 id 字面量——
    层序是导演意图（主体在前、影子按滞后递增），不能被字典序洗掉。
    """
    return sample_frames(p, state_at,
                         transform=lambda v: {k: _clean(x) for k, x in v.items()})


def start(p, i=0):
    return state_at(0.0, p, i)


def landing(p, i=0):
    return state_at(p["duration"], p, i)


def settle_state(cfg, target, where="trail"):
    """跨段接续的收尾态：`[(选择器, 补间变量), …]`。

    不需要 fps 与 duration：主体终点就是 distance，影子在页界处**只折
    opacity:0**（它们不可见，位置由下一段自己的第一帧接管）。不认这一拍的
    话，接续页第 0 帧会挂着一串影子，直到本段第一条补间才收掉——页界一次
    静默的闪。
    """
    c = validate(cfg, where)
    out = [(target, {c["axis"]: float(c["distance"])})]
    out += [(sel, {"opacity": 0.0}) for sel in c["ghosts"]]
    return out


def echo_identity(p, i, t):
    """时间回声恒等式的残差：`xᵢ(t) − x₀(t − i·lag)`，恒为 0。"""
    axis = p["cfg"]["axis"]
    return abs(state_at(t, p, i)[axis]
               - state_at(t - i * p["cfg"]["lag"], p, 0)[axis])


def diagnose(p):
    """逐条列出没过的红线，给生成期报错用（空列表 = 全过）。

    判据全部从 `state_at` / `frames` 反推，不复述配置——否则就成了
    「配置说了什么」的自证。报错要回答「改哪一个旋钮」，不只是「没过自查」。
    """
    c = p["cfg"]
    axis, d, dur = c["axis"], c["distance"], p["duration"]
    k = len(c["ghosts"])
    bad = []
    if abs(start(p)[axis]) > PRODUCT_EPS:
        bad.append("主体起点没精确归零")
    if abs(landing(p)[axis] - d) > PRODUCT_EPS:
        bad.append("主体终点没精确落在 distance")
    for i in range(1, k + 1):
        if abs(start(p, i)["opacity"]) > PRODUCT_EPS:
            bad.append("段首有影子提前显形")
        if abs(landing(p, i)["opacity"]) > PRODUCT_EPS:
            bad.append("收尾没把影子收干净（跨段会留残影）")
    # 回声恒等式：影子只显示主体的过去，不能自己另走一条曲线
    for j in range(1, 41):
        t = dur * j / 40.0
        for i in range(1, k + 1):
            if echo_identity(p, i, t) > PRODUCT_EPS:
                bad.append("时间回声恒等式被破坏（影子不是主体的过去）")
                break
    # 逐层严格衰减：W·V 由主体共享，层间只能差 decayⁱ
    mid = dur * 0.5
    ops = [state_at(mid, p, i)["opacity"] for i in range(1, k + 1)]
    if not all(a > b + 1e-9 for a, b in zip(ops, ops[1:])):
        bad.append("逐层可见度没有严格递减（decay 写反了）")
    for i in range(2, k + 1):
        if abs(ops[i - 1] / ops[0] - c["decay"] ** (i - 1)) > 1e-6:
            bad.append("层间亮度比不等于 decay 的幂")
    # 影子永不超越主体、可见度有界
    for j in range(41):
        u = j / 40.0
        t = dur * u
        subj = state_at(t, p, 0)[axis]
        for i in range(1, k + 1):
            g = state_at(t, p, i)
            if (g[axis] - subj) * (1.0 if d > 0 else -1.0) > PRODUCT_EPS:
                bad.append("影子跑到主体前面（重参数化会犯这个错）")
            if not (0.0 <= g["opacity"] <= 1.0 + PRODUCT_EPS):
                bad.append("可见度越出 [0,1]")
    # 采样网格：条数 = (帧数+1)×(层数+1)，时刻不撞进同一个 0.01s 格
    fr = frames(p)
    if len(fr) != (frame_count(p) + 1) * (k + 1):
        bad.append("帧时刻撞进同一个 0.01s 格")
    ts = [t for t, _, _ in fr]
    if ts != sorted(ts) or ts[0] != 0.0:
        bad.append("采样网格没有严格递增")
    if c["lag"] < 1.0 / p["fps"] - 1e-12:
        bad.append("层间滞后小于一帧")
    if k * c["lag"] > LAG_SPAN_MAX * dur + 1e-12:
        bad.append("总滞后超过拍长的 75%")
    return bad


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。"""
    return not diagnose(p)
