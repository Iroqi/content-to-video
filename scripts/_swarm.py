#!/usr/bin/env python3
"""群集档的单一真源：第一次让「元素之间互为源」，位移由两两相对几何算出来。

已有档位把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度包络（`momentum`）、相位（`drag`）、时间量化（`step`）、
静止（`hold`）、序号锚点（`stagger` / `decode`）、单元素空间曲率（`arc`）、值域
不规则（`organic`）、时间叠加（`trail`）、距离→延迟的空间传播（`wave`）、曲线弧长
+切线的方向场（`flow`）、方向的时间域（`lens`）。它们还有一条从没人写破的共同
前提：**驱动源是画面之外给定的**——一个震中、一条曲线、一枚透镜、一个序号、
一条作者手写的算术式。元素只是被动接受那个场；挪动图里一个元素，只有它自己
的位移序列会变，邻居一格不动（这正是 `wave` / `flow` / `lens`「改图即改编排」
判据的形状）。

2026 的前沿 MG 里最显眼的另一支正好在打这条前提：Generative Systems 里的
Boid flocking（Separation / Alignment / Cohesion 三规则产生 emergent behavior）、
Motion Luma 2026 趋势里的 procedural node-based animation 与「swarming data
nodes」、Videobacks 的粒子物理与实时交互、以及中文圈说的「从渲染什么到如何
交互 / 粒子从效果变成有行为的实体」。共同手感是**一整片点自己散开、互相避让、
绕着彼此盘旋，再干净地收回去**——不是一组元素逐个出现，不是一个扰动从固定
中心扩散，也不是一枚透镜扫过。

本档负责**相互作用的来源**。几何由生成期从 SVG 里量出来，作者不写坐标：

```
cᵢ   = center(cells[i])                     每个元素的包围盒中心（图上真实元素）
R    = maxᵢⱼ |cⱼ − cᵢ|                      影响半径：**全体元素自己**的最远间距
dᵢⱼ  = (cⱼ − cᵢ)/R,  qᵢⱼ = |dᵢⱼ| ∈ (0,1]  归一化相对位置，q=1 是最远那一对
K(q) = (1 − q²)²,  q∈[0,1)，否则 0         聚合/对齐核：q=1 处值与导数精确归零
S(q) = (1 − q)²                            分离核：近邻权重更陡，与 K 不共线
Wᵢ   = Σⱼ K(qᵢⱼ)                            i 的邻域权重和（活跃度真源）
sepᵢ = −Σⱼ S(qᵢⱼ)·ûᵢⱼ / |Nᵢ|              远离近邻（boids separation）
cohᵢ = Σⱼ K(qᵢⱼ)·dᵢⱼ / Wᵢ                 朝邻域加权质心（boids cohesion）
vorᵢ = rot90(cohᵢ)                          绕邻域质心盘旋（确定性版 alignment）
vᵢ   = w_sep·sepᵢ + w_coh·cohᵢ + w_vor·vorᵢ  按 mode 取三项权重
ûᵢ   = vᵢ/|vᵢ|                              **方向**由全体邻居共同决定
aᵢ   = min(1, Wᵢ/W_ref)                      活跃度：离群的元素动得少
u(t) = sin²(πt/D)                           时间包络：u(0)=u(D)=0，端点导数为 0
Δᵢ(t)= amplitude · u(t) · aᵢ · ûᵢ            端点精确回原位
```

**七条设计决定，都是被成片纪律逼出来的**：

1. **元素互为源，这是本档存在的全部理由**。`wave` / `flow` / `lens` 里每个元素的
   位移只依赖「它自己」到某个外部锚点的关系；这里 `sepᵢ` / `cohᵢ` / `vorᵢ`
   全部是对 `j≠i` 求和，且 `R` 由全体 centers 的最远间距给出。于是**挪动一个
   元素，至少另一个元素的位移序列也跟着变**——这条产物级判据与前面十四档的
   「只有它自己变」恰好相反，是本档唯一无法被别的档伪装的特征。
2. **几何一个字都不进契约**。`cells` 只写「哪些元素参与」，`amplitude` 是唯一
   的幅度旋钮。没有震中、没有曲线、没有透镜、没有半径、没有权重：三规则权重
   是成片纪律的一部分，不是作者该调的旋钮（调了就变成第二套物理）。与 `wave`
   的「延迟不进契约」、`flow` 的「相位不进契约」、`lens` 的「场心不进契约」
   是同一条纪律的第四次落地，且更彻底——**连场的中心都不存在**。
3. **没有状态、没有积分步**。真实的 boids 是逐帧迭代的速度积分，第 N 帧依赖
   第 N−1 帧，`seek()` 跳帧就对不上，这正是本仓库逐帧可复现的红线。这里把
   三规则写成**闭式的邻域场**：方向在生成期由图上静态几何一次算死，时间只通过
   `u(t)` 这一个纯函数缩放。于是 `state_at(t)` 不读墙钟、不存上一帧、不依赖
   调用顺序，正序/逆序/乱序求值得同一串数。
4. **方向必须真的散开**。至少两个活跃元素的位移方向夹角 ≥
   `SWARM_MIN_SPREAD_DEG`。所有元素挤成一团、或全部共线且同侧时，合成方向几乎
   平行，成片读成「整块平移」——那就是 `wave` 的同步脉动或一条 `to`，按文件拦。
5. **端点精确归零**。`u(t)=sin²(πt/D)` 在 t=0/D 处解析值为 0，但浮点
   `math.sin(math.pi)` 不是精确 0；`state_at` 对端点与拍外显式短路返回 0.0，
   并把负零归一。于是 `state_at(0) == state_at(D) == 0`，**收尾态 = 起始态**，
   跨段接续没有残留姿态可折（但仍要显式认这一拍，见下）。
6. **紧支撑与活跃度**。`K(1)=0` 且一阶导数归零：`R` 是最远间距，所以最远那一对
   之间**精确没有直接作用**（不是「很小」），边界不出现速度突变。`aᵢ` 让孤立/
   离群元素趋于不动，避免「一整片里有个点莫名其妙也在飘」。
7. **生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step` /
   `organic` / `trail` / `wave` / `flow` / `lens` 的老路：渲染端零脚本、零
   `onUpdate`、零依赖，成片里是一串确定的数值，逐帧 `seek()` 下每一帧都是时间
   的纯函数。渲染端按邻域场实时求值要跑一次 N² 物理迭代，既慢又不可复现。

**质感不归这一档**：半透明、发光、粒子拖影由 SVG 自己画（`<radialGradient>` /
`<filter>` 经 `_svg_sanitize` 保留），或贴一段离线渲好的视频素材。档位只负责
确定性的位移场，视觉质感是素材管线的活。

**几何口径**：包围盒/祖先仿射与波场档、透镜档共用同一份实现（直接取
`_lens._bbox_of`，它内部走 `_cam_crop._box` + `_wave.affine_to`），不另写第二套
「大概在哪」。带 rotate / matrix / skew 的祖先读不准就判废并出声，不猜中心。

**跨段接续**：每个元素收尾都精确回 0，没有残留。`_stage_carry` 仍必须**显式认
这一拍**并返回空态，不能靠「这一拍没有 `set`/`to`、恰好 no-op」的巧合——将来
给这一档加任何终态，那条巧合就会变成页界处一次静默的闪。
"""
import math
import re
import xml.etree.ElementTree as ET

# 采样带、端点精度、最短拍长全部复用既有档的真源：它们是全仓库共用的工程判据，
# 写成第二份字面量就会改一处漏一处（test_shared_geometry 钉着这条纪律）。
from _drag import PRODUCT_EPS
from _organic import MIN_DURATION
from _cam_crop import CAM_ID
from _svg_sanitize import _local
from _lens import _bbox_of  # noqa: E402  包围盒/仿射口径只有一份，见模块头
from _field import (clamp as _clamp, clean as _clean, frame_count,  # noqa: E402  采样骨架只有一份
                    sample_frames)
from _validate import plan_bounds  # noqa: E402  plan 的数值守卫只有一份真源

# ── 红线（工程判据，不进模板、不给 --set 改）──────────────────────────
# 参与元素数。2 个只能「互相推开」，谈不上群集，也排不出两个不同朝向；
# 多于 12 个在 24fps 下每帧 12 条以上 tl.set，观众也数不出各自的走向。
SWARM_CELLS_MIN = 3
SWARM_CELLS_MAX = 12
# 「方向真的散开」的下限（度）：至少两个活跃元素的位移方向夹角。
# 与 _flow.FLOW_MIN_TURN_DEG / _lens.LENS_MIN_TURN_DEG 同量级、同一条理由
# （20° 在 24fps 下连着几帧读得出，6° 只需「转了」、这里要「不是一个朝向」）。
SWARM_MIN_SPREAD_DEG = 20.0
# 任意两元素最小净空占 R 的比例。û=d/q 在 q→0 未定义，而分离核 S(0)=1 恰是峰值。
SWARM_MIN_SEPARATION_RATIO = 0.05
# 单元素活跃判据：活跃度 aᵢ ≥ 这个比例才算「在群里」。
SWARM_ACTIVE_RATIO = 0.05
# 至少这么多个元素活跃，否则是单元素位移而不是「群」。
SWARM_ACTIVE_CELLS_MIN = 2
# 峰值位移下限（占 |amplitude| 的比例）：整片都没动等于一整段静帧。
SWARM_PEAK_FLOOR = 0.05
# 方向归一与权重和的除零保护。
SWARM_EPS = 1e-9
# 活跃度参考权重和：Wᵢ 达到它就吃满 amplitude。取 2.0 是量过的：六个点散成
# 一片时每人的 W≈1.8~2.4，恰好吃满；只留一个点孤零零挂远处时 W<0.1，成片里
# 它几乎不动——「离群的成员不被群带着走」这条才有产物可验。
SWARM_ACTIVITY_REF = 2.0

# flock = 三规则合成（缺省）；scatter = 纯分离；vortex = 纯盘旋。
# 权重是成片纪律，不是旋钮：作者只选「画面读成什么」，不调物理常数。
SWARM_DEFAULT_MODE = "flock"
_MODE_WEIGHTS = {
    "flock": (1.0, 0.45, 0.65),
    "scatter": (1.0, 0.0, 0.0),
    "vortex": (0.0, 0.0, 1.0),
}

_SWARM_KEYS = frozenset({"cells", "amplitude", "mode"})
# 只收单个 #id：离散 tl.set 没法给一组同 class 元素分别写值。
_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(float(v))


def validate(cfg, where="swarm"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象、不读 SVG）。

    契约层（`_images_schema`）、渲染端（`html_renderer`）、生成期体检（`_animate`）、
    跨段接续烘焙（`_stage_carry`）调的是**同一个函数**：红线写在消费侧，`--set`
    或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变成一组
    不动的点。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _SWARM_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_SWARM_KEYS)}")
    cells = cfg.get("cells")
    if not isinstance(cells, list) or not (SWARM_CELLS_MIN <= len(cells) <= SWARM_CELLS_MAX):
        raise ValueError(f"{where}.cells 必须是 {SWARM_CELLS_MIN}~{SWARM_CELLS_MAX} 个 #id 的"
                         f"有序列表（图里画好的一组元素；实际: {cells!r}）")
    for sel in cells:
        if not isinstance(sel, str) or not _ID_RE.match(sel):
            raise ValueError(f"{where}.cells 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(cells)) != len(cells):
        raise ValueError(f"{where}.cells 不能有重复 id：同一元素领两份邻域力会被"
                         "后写的那条静默覆盖")
    if "#" + CAM_ID in cells:
        raise ValueError(f"{where}.cells 不能含 #{CAM_ID}：相机层归整页取景，"
                         "收尾姿态由折层负责；给每个元素单独命名")
    amp = cfg.get("amplitude")
    if not _finite(amp) or abs(float(amp)) < 1e-9:
        raise ValueError(f"{where}.amplitude 必须是非零的有限数（峰值位移，SVG 单位；"
                         f"实际: {amp!r}）")
    mode = cfg.get("mode", SWARM_DEFAULT_MODE)
    if mode not in _MODE_WEIGHTS:
        raise ValueError(f"{where}.mode 必须是 'flock' / 'scatter' / 'vortex' 之一"
                         f"（实际: {mode!r}）")
    return {"cells": list(cells), "amplitude": float(amp), "mode": mode}


def kernel(q):
    """聚合/对齐核 K(q)=(1−q²)²：q=1 处值与一阶导数都精确归零。"""
    if q >= 1.0:
        return 0.0
    s = 1.0 - q * q
    return s * s


def separation_kernel(q):
    """分离核 S(q)=(1−q)²：近邻更陡，q=1 处值与导数同样归零。"""
    if q >= 1.0:
        return 0.0
    s = 1.0 - q
    return s * s


def _angle_deg(v0, v1):
    n0, n1 = math.hypot(*v0), math.hypot(*v1)
    if n0 <= 1e-12 or n1 <= 1e-12:
        return 0.0
    cos = _clamp((v0[0] * v1[0] + v0[1] * v1[1]) / (n0 * n1), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def field(centers, radius, mode=SWARM_DEFAULT_MODE):
    """闭式邻域场：返回逐元素的 [(方向 x, 方向 y, 活跃度), …]。

    这是本档唯一读「元素之间」关系的地方，且**不读时间**——方向在生成期由图上
    静态几何一次算死，时间只经 `envelope` 缩放。没有速度状态、没有逐帧积分，
    所以 `seek()` 跳到任意一帧与顺序播放到那一帧得出同一个数。
    """
    if mode not in _MODE_WEIGHTS:
        raise ValueError(f"未知群集模式 {mode!r}")
    w_sep, w_coh, w_vor = _MODE_WEIGHTS[mode]
    R = float(radius)
    n = len(centers)
    out = []
    for i in range(n):
        sx = sy = cx = cy = 0.0
        W = 0.0
        neighbours = 0
        for j in range(n):
            if j == i:
                continue
            dx = (centers[j][0] - centers[i][0]) / R
            dy = (centers[j][1] - centers[i][1]) / R
            q = math.hypot(dx, dy)
            if q <= SWARM_EPS or q >= 1.0:
                continue          # q=1 是最远对：K(1)=S(1)=0，精确不直接作用
            ux, uy = dx / q, dy / q
            wc, ws = kernel(q), separation_kernel(q)
            sx -= ws * ux
            sy -= ws * uy
            cx += wc * dx
            cy += wc * dy
            W += wc
            neighbours += 1
        if W > SWARM_EPS:
            cx, cy = cx / W, cy / W
        else:
            cx = cy = 0.0
        if neighbours:
            sx, sy = sx / neighbours, sy / neighbours
        # rot90(coh)：逆时针切向，读成「绕邻域质心盘旋」而不是朝它聚拢
        vx = w_sep * sx + w_coh * cx - w_vor * cy
        vy = w_sep * sy + w_coh * cy + w_vor * cx
        mag = math.hypot(vx, vy)
        if mag <= SWARM_EPS:
            out.append((0.0, 0.0, 0.0))
            continue
        activity = _clamp(W / SWARM_ACTIVITY_REF)
        out.append((vx / mag, vy / mag, activity))
    return out


def spread_deg(fld):
    """活跃元素之间最大的位移方向夹角（度）。"""
    live = [v[:2] for v in fld if v[2] > SWARM_ACTIVE_RATIO]
    if len(live) < 2:
        return 0.0
    return max(_angle_deg(a, b)
               for k, a in enumerate(live) for b in live[k + 1:])


def geometry(svg_text, cfg, where="swarm"):
    """按文件量几何：返回元素中心 / 影响半径 / 邻域场 / 方向发散角。

    这是**唯一**把 SVG 坐标变成数字的地方：契约层、渲染端、生成期体检、跨段
    烘焙都不另写一份「大概在哪」。改图即改编排，且这里的「改图」比前面十四档
    更狠：挪一个元素，邻居的方向也变（判据在 test_swarm.py 里从产物抠出来对账）。
    """
    c = validate(cfg, where)
    if not isinstance(svg_text, str) or not svg_text.strip():
        raise ValueError(f"{where} 要从内联 SVG 读几何，但这一页没有 SVG 源文本"
                         "（群集档只能写在 .svg 上）")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValueError(f"{where} 的 SVG 解析失败：{e}") from e
    if _local(root.tag) != "svg":
        raise ValueError(f"{where} 的 SVG 根标签不是 <svg>")
    parent = {child: p for p in root.iter() for child in p}
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    missing = sorted({s for s in c["cells"] if s[1:] not in by_id})
    if missing:
        raise ValueError(f"{where} 里的 " + "、".join(missing) + " 在 SVG 里找不到"
                         "对应 id 的元素——选择器落空不会报错，只会让那一片区域"
                         "静默不动")
    centers = [_bbox_of(root, by_id[s[1:]], parent, where, s)[:2]
               for s in c["cells"]]
    R = 0.0
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            R = max(R, math.hypot(centers[j][0] - centers[i][0],
                                  centers[j][1] - centers[i][1]))
    if R <= 1e-9:
        raise ValueError(f"{where} 的所有元素中心重合（R=0）：量不出影响半径，"
                         "把这一组元素在图上散开")
    clearance = max(1.0, SWARM_MIN_SEPARATION_RATIO * R)
    for i in range(len(centers)):
        for j in range(i + 1, len(centers)):
            d = math.hypot(centers[j][0] - centers[i][0],
                           centers[j][1] - centers[i][1])
            if d < clearance:
                raise ValueError(f"{where} 的 {c['cells'][i]} 与 {c['cells'][j]} "
                                 f"只隔 {d:.2f}（要求 ≥{clearance:.2f}）：û=d/q "
                                 "在 q→0 未定义而分离核恰是峰值；把这两个元素挪开")
    fld = field(centers, R, c["mode"])
    spread = spread_deg(fld)
    if spread < SWARM_MIN_SPREAD_DEG:
        raise ValueError(f"{where} 的元素位移方向几乎平行（最大发散 {spread:.2f}° "
                         f"< {SWARM_MIN_SPREAD_DEG}°）：读成整块平移，不是群集；"
                         "把元素排成一片（别全挤在一条线同侧），或改用 "
                         "`references/wave.md` 的 radial 档")
    return {"centers": centers, "radius": R, "field": fld,
            "spread": spread, "clearance": clearance}


def plan(cfg, duration, fps, svg_text, target, where="swarm"):
    """把一条 `swarm` 拍编译成可采样的计划。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur, f = plan_bounds(duration, fps, where, min_duration=MIN_DURATION)
    if c["cells"][0] != target:
        raise ValueError(f"{where}.cells[0] 必须等于 target={target!r}：群头就是"
                         "这一拍的目标，接续烘焙与按文件拦都只认 step 级 target"
                         f"（实际: {c['cells'][0]!r}）")
    g = geometry(svg_text, c, where)
    return {"cfg": c, "duration": dur, "fps": f, "target": target,
            "geometry": g, "selectors": list(c["cells"])}


def envelope(t, p):
    """时间包络 sin²(πt/D)，端点显式精确 0（math.sin(math.pi) 不是精确 0）。"""
    if t <= 0.0 or t >= p["duration"]:
        return 0.0
    return math.sin(math.pi * t / p["duration"]) ** 2


def state_at(t, p, i):
    """第 i 个元素在时刻 `t`（相对拍起点）的补间变量，**纯函数**。

    不读墙钟、不存上一帧：`ûᵢ` 与 `aᵢ` 在生成期由图上静态几何算死，时间只经
    `u(t)` 缩放。逐帧 `seek()` 下每一帧都由 `t` 独立算出。
    """
    if not 0 <= i < len(p["selectors"]):
        raise ValueError(f"元素下标 {i} 越界（共 {len(p['selectors'])} 个）")
    t = _clamp(float(t), 0.0, p["duration"])
    u = envelope(t, p)
    if u == 0.0:
        return {"x": 0.0, "y": 0.0}
    dx, dy, activity = p["geometry"]["field"][i]
    if activity <= 0.0:
        return {"x": 0.0, "y": 0.0}
    a = p["cfg"]["amplitude"] * u * activity
    return {"x": _clean(a * dx), "y": _clean(a * dy)}


def peak_time(p, i=0):
    """峰值时刻：本档包络 `sin²(πt/D)` 与元素无关，闭式解就是 D/2。

    与 `_lens.peak_time` 的实采不同——那里峰值由「场心何时扫到元素」与包络
    共同决定；这里方向不随 t 变，没必要在 [0,D] 上实采。
    """
    if not 0 <= i < len(p["selectors"]):
        raise ValueError(f"元素下标 {i} 越界（共 {len(p['selectors'])} 个）")
    return p["duration"] / 2.0


def frames(p):
    """生成期采样：`[(相对秒, 选择器, 补间变量), …]`，每帧每元素一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位；排序键是 (时刻, 元素下标)，不是 id 字面量——
    写入顺序是作者给的 `cells` 序（导演意图），不能被字典序洗掉。
    """
    return sample_frames(p, state_at)


def start(p, i=0):
    return state_at(0.0, p, i)


def landing(p, i=0):
    return state_at(p["duration"], p, i)


def settle_state(cfg, where="swarm"):
    """跨段接续的收尾态：空。

    u(0)=u(D)=0 让每个元素在 D 处精确回 0，没有残留姿态可折。这个函数仍要
    **显式存在**并被 `_stage_carry` 调用：靠「这一拍没有 set/to、恰好 no-op」
    的巧合，将来给这一档加任何终态时，页界就会静默闪一下。
    """
    validate(cfg, where)
    return []


def diagnose(p):
    """逐条列出没过的红线，给生成期报错用（空列表 = 全过）。

    判据全部从 `state_at` / `frames` / 几何场反推，不复述配置——否则就成了
    「配置说了什么」的自证。报错要回答「改哪一个旋钮」，不只是「没过自查」。
    """
    c = p["cfg"]
    g = p["geometry"]
    dur = p["duration"]
    n = len(p["selectors"])
    bad = []
    for i in range(n):
        if any(abs(v) > PRODUCT_EPS for v in start(p, i).values()):
            bad.append("段首有元素提前位移")
        if any(abs(v) > PRODUCT_EPS for v in landing(p, i).values()):
            bad.append("收尾没把群集收干净（跨段会留半截位移）")
        for t in (-0.5, dur + 0.5):
            if any(abs(v) > PRODUCT_EPS for v in state_at(t, p, i).values()):
                bad.append("时间包络不是紧支撑（拍外还在动）")
        # numeric guards：写进产物的每一帧都必须是有限数
        for k in range(21):
            s = state_at(dur * k / 20, p, i)
            if not all(math.isfinite(v) for v in s.values()):
                bad.append("位移场算出非有限数")
                break
    if g["spread"] < SWARM_MIN_SPREAD_DEG:
        bad.append("元素位移方向几乎平行（退化成整块平移，不是群集）")
    # 峰值与活跃元素数：整片都没动 = 一整段静帧，由几何决定，只能实采判
    peaks = [max(math.hypot(*state_at(dur * k / 200, p, i).values())
                 for k in range(201)) for i in range(n)]
    if max(peaks) < SWARM_PEAK_FLOOR * abs(c["amplitude"]):
        bad.append("整片元素都没被邻域带动（峰值位移太小，成片是静帧）")
    if sum(1 for v in peaks if v >= SWARM_ACTIVE_RATIO * abs(c["amplitude"])) \
            < SWARM_ACTIVE_CELLS_MIN:
        bad.append("只有一个元素在动（读成单元素位移，不是群）")
    # 三种 mode 必须真的给出不同的场：否则 mode 是个静默失效的旋钮
    if c["mode"] == SWARM_DEFAULT_MODE:
        others = [m for m in _MODE_WEIGHTS if m != SWARM_DEFAULT_MODE]
        base = g["field"]
        for mode in others:
            alt = field(g["centers"], g["radius"], mode)
            if not any(_angle_deg(base[i][:2], alt[i][:2]) > 1e-6
                       for i in range(n) if base[i][2] > 0 and alt[i][2] > 0):
                bad.append(f"{mode} 与 {SWARM_DEFAULT_MODE} 的位移方向完全相同"
                           "（mode 旋钮静默失效）")
                break
    # 采样网格：条数 = (帧数+1)×元素数，时刻不撞进同一个 0.01s 格
    fr = frames(p)
    if len(fr) != (frame_count(p) + 1) * n:
        bad.append("帧时刻撞进同一个 0.01s 格")
    ts = [t for t, _, _ in fr]
    if ts != sorted(ts) or ts[0] != 0.0:
        bad.append("采样网格没有严格递增")
    return bad


def is_well_formed(p):
    """本档的自查，合成一个布尔给生成期和门禁共用。"""
    return not diagnose(p)
