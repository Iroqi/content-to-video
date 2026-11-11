#!/usr/bin/env python3
"""透镜档的单一真源：第一次让「一个元素的位移方向」在这一拍之内改变。

本仓库此前把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度包络（`momentum`）、相位（`drag`）、时间量化（`step`）、
静止（`hold`）、序号锚点（`stagger` / `decode`）、单元素空间曲率（`arc`）、值域
不规则（`organic`）、时间叠加（`trail`）、距离→延迟的空间传播（`wave`）、曲线弧长
+切线的方向场（`flow`）。它们还有一条从没人写破的共同前提：**一个元素的位移方向
在整拍里是定死的**。`wave.radial` 的方向由一个固定震中给出（恒为从那一点向外），
`flow` 的方向由曲线给出但每个元素只走自己那一小段弧，`arc` 是单个元素两条轴的
合成——没有一个档里，同一个元素在 t=0.3s 和 t=0.8s 会被推向不同的方向。

2025/2026 的前沿 MG 里最显眼的一支正好在打这条前提：Apple 带起来的 liquid
glass / refraction sweep、Senate Media 2026 趋势里「mimics the smooth movements
of water, combining it with the visual reflections and refractions of glass」、
Motion Luma 的 liquid glassmorphism（refraction / dispersion / caustics）、AE 的
Displacement Map / Liquify 与 Cavalry 的 field driver。共同手感是**一枚透镜扫过画面，
同一批元素先被推开、随场心移动改变朝向、再干净地收回去**——不是一组元素逐个出现，
也不是一个扰动从固定中心扩散。

这一档补的是**方向的时间域**。几何由生成期从 SVG 里量出来，作者不写坐标：

```
L₀   = center(glass)                        透镜元素的包围盒中心（场心起点）
L₁   = center(to)                           终点元素的包围盒中心（场心终点）
R    = max(w,h)/2 of glass bbox            影响半径，唯一真源是透镜元素自己
cᵢ   = center(cells[i])                     每个元素的包围盒中心（图上真实元素）
L(t) = L₀ + (L₁ − L₀)·t/D                  场心匀速扫过（D = 拍长）
u(t) = sin²(πt/D)                          时间包络：u(0)=u(D)=0，且端点导数为 0
vᵢ(t)= cᵢ − L(t)                           元素到场心的瞬时向量
rᵢ(t)= |vᵢ(t)|,  qᵢ(t) = rᵢ(t)/R
K(q) = (1 − q²)²,  q∈[0,1)，否则 0        紧支撑 bulge 核：q=1 处值与导数精确归零
ûᵢ(t)= vᵢ(t)/rᵢ(t)                         **瞬时**方向单位向量，随场心旋转
Δᵢ(t)= amplitude · u(t) · K(qᵢ(t)) · ûᵢ(t)  bulge 向外 / pinch 向内
```

**六条设计决定，都是被成片纪律逼出来的**：

1. **几何一个字都不进契约**。`cells` 只写「哪些元素参与」，`glass` 与 `to` 只写
   「哪两个真实元素当量测锚点」，`amplitude` 是唯一的幅度旋钮。作者一旦能手写
   场心坐标或半径，那串数字就是几何的第二份真源——挪动图里一个点，成片里的
   折射仍停在旧位置，且零报错。与 `wave` 的「延迟不进契约」、`flow` 的「相位不
   进契约」是同一条纪律的第三次落地：**改图即改编排**。
2. **方向必须是 t 的函数**。这是本档存在的全部理由，也是它区别于 `wave.radial`
   的唯一硬判据：生成期取各元素在 t=0 与 t=D 的 û 夹角，要求至少一处 ≥
   `LENS_MIN_TURN_DEG`。所有元素恰好排在透镜轨迹那条直线上时方向恒不转，读成
   「一组被固定震中推开的点」——那就是 `wave`，按文件拦，不是参数越界。
3. **端点精确归零，不是"接近 0"**。`u(t)=sin²(πt/D)` 在 t=0/D 处解析值为 0，
   但浮点 `math.sin(math.pi)` 不是精确 0；`state_at` 对端点与拍外显式短路返回
   0.0，避免 `-0.0` 与 1e-32 级的残差写进产物。于是 `state_at(0) ==
   state_at(D) == 0`，**收尾态 = 起始态**，跨段接续没有残留姿态可折（但仍要
   显式认这一拍，见下）。这一点与 `wave` 的紧支撑脉冲同语义，与 `flow` 落在
   新位置相反。
4. **核在 R 处值与导数都归零**。`K(q)=(1−q²)²` 让影响半径边界不出现硬切：
   靠近边界的元素位移平滑趋零，圈外的元素精确 0（不是"很小"）。若用 `1−q`
   这类带尖角的核，场心扫过格子阵时边界上会读到一次速度突变，成片里是一闪。
5. **元素不许压在透镜轨迹上**。`û = v/|v|` 在 r→0 时未定义，而 `K(0)=1` 恰是
   峰值——一个正好在轨迹上的元素会被以最大幅度甩向一个算不出的方向。生成期
   按点到线段的解析距离逐个拦，要求 ≥ `max(1, 0.05·R)`，报错直接说"挪开多少"。
   与 `wave` 拦「格子与震中重合」同一类。
6. **生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step` /
   `organic` / `trail` / `wave` / `flow` 的老路：渲染端零脚本、零 `onUpdate`、
   零依赖，成片里是一串确定的数值，逐帧 `seek()` 下每一帧都是时间的纯函数。
   渲染端按位移场实时求值、或用 CSS `filter: url(#displacementMap)` 让浏览器
   逐帧重算，都会让第 N 帧与直接跳到第 N 帧对不上，且 SVG filter 的采样在
   不同 Chrome 版本间不保证一致（跨机逐帧一致靠 `render --docker`）。

**玻璃的观感不归这一档**：本档只负责确定性的位移场。液态玻璃的半透明、渐变、
高光与色散由 SVG 自己画（`<linearGradient>` / `<radialGradient>` / `<filter>`
经 `_svg_sanitize` 保留，见 `references/extensions.md`「四、正交判据」），或干脆
贴一段离线渲好的视频素材（`references/imagery.md`「两种形式的统一规则」）。
视觉质感是素材管线的活，档位不负责画风。

**几何口径**：包围盒走共享的 `_cam_crop._box`（与 `check_svg` 的空带/越界、
`_cam_crop` 的运镜取景、波场档量距离同一份），祖先的 translate/scale 由
`_wave.affine_to` 复合；旋转/斜切读不准就判废并出声，不猜一个中心。目标元素
内部不再折 transform——那要在这里复刻第二套仿射栈，与「真源不分家」相冲。

**跨段接续**：每个元素收尾都精确回 0，没有残留。`_stage_carry` 仍必须**显式认
这一拍**并返回空态，不能靠「这一拍没有 `set`/`to`、恰好 no-op」的巧合——
将来给这一档加任何终态，那条巧合就会变成页界处一次静默的闪。
"""
import math
import re
import xml.etree.ElementTree as ET

# 采样带、端点精度、最短拍长全部复用既有档的真源：它们是全仓库共用的工程判据，
# 写成第二份字面量就会改一处漏一处（test_shared_geometry 钉着这条纪律）。
from _drag import PRODUCT_EPS
from _organic import MIN_DURATION
from _cam_crop import CAM_ID, _box
from _svg_sanitize import _local
from _wave import affine_to  # noqa: E402  包围盒/仿射口径只有一份
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)
from _field import (clamp as _clamp, clean as _clean, frame_count,  # noqa: E402  采样骨架只有一份
                    sample_frames)

# ── 红线（工程判据，不进模板、不给 --set 改）──────────────────────────
# 参与元素数。1 个谈不上「一整片被折射」；多于 12 个在 24fps 下每帧 12 条
# 以上 tl.set，观众也数不出不同朝向。
LENS_CELLS_MIN = 2
LENS_CELLS_MAX = 12
# 「方向真的会转」的下限（度）：至少一个元素在 t=0 与 t=D 的 û 夹角。
# 与 _flow.FLOW_MIN_TURN_DEG 同量级、同一条理由（6° 在 24fps 下一帧读得出）。
LENS_MIN_TURN_DEG = 6.0
# 场心最短行程（SVG 单位）：小于这个量在 24fps 下一帧挪不到一像素，方向转不出来。
LENS_MIN_TRAVEL = 8.0
# 元素到透镜轨迹的最小净空占 R 的比例。û=v/|v| 在 r→0 未定义，而 K(0)=1 是峰值。
LENS_CLEARANCE_RATIO = 0.05
# 峰值位移下限（占 |amplitude| 的比例）：透镜一次都没扫到任何元素时，成片里
# 是一段静帧，这不能靠"旋钮取值范围"兜住——它由几何决定，生成期按帧实采判。
LENS_PEAK_FLOOR = 0.20
# 至少两个元素真被折射，否则是单元素位移而不是「场」。
LENS_ACTIVE_CELLS_MIN = 2
# 单元素活跃判据：峰值 ≥ 这个比例乘 |amplitude|。
LENS_ACTIVE_RATIO = 0.05

# bulge = 向外推（凸透镜放大感，缺省）；pinch = 向内吸（凹透镜）。
LENS_DEFAULT_MODE = "bulge"

_LENS_KEYS = frozenset({"cells", "glass", "to", "amplitude", "mode"})
# 只收单个 #id：离散 tl.set 没法给一组同 class 元素分别写值。
_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")
_MODES = frozenset({"bulge", "pinch"})


def validate(cfg, where="lens"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象、不读 SVG）。

    契约层（`_images_schema`）、渲染端（`html_renderer`）、生成期体检（`_animate`）、
    跨段接续烘焙（`_stage_carry`）调的是**同一个函数**：红线写在消费侧，`--set`
    或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变成一组
    不动的点。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _LENS_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_LENS_KEYS)}")
    cells = cfg.get("cells")
    if not isinstance(cells, list) or not (LENS_CELLS_MIN <= len(cells) <= LENS_CELLS_MAX):
        raise ValueError(f"{where}.cells 必须是 {LENS_CELLS_MIN}~{LENS_CELLS_MAX} 个 #id 的"
                         f"有序列表（图里画好的一组元素；实际: {cells!r}）")
    for sel in cells:
        if not isinstance(sel, str) or not _ID_RE.match(sel):
            raise ValueError(f"{where}.cells 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(cells)) != len(cells):
        raise ValueError(f"{where}.cells 不能有重复 id：同一元素领两份折射会被"
                         "后写的那条静默覆盖")
    glass = cfg.get("glass")
    if not isinstance(glass, str) or not _ID_RE.match(glass):
        raise ValueError(f"{where}.glass 必须是单个 #id（透镜元素，生成期从图上"
                         f"读它的包围盒中心与半径；实际: {glass!r}）")
    to = cfg.get("to")
    if not isinstance(to, str) or not _ID_RE.match(to):
        raise ValueError(f"{where}.to 必须是单个 #id（场心终点元素，生成期从图上"
                         f"读它的中心；实际: {to!r}）")
    if glass == to:
        raise ValueError(f"{where}.glass 与 {where}.to 不能是同一个元素：场心没有"
                         "行程，方向不会转，这一拍退化成一组同步脉动")
    for name, sel in (("glass", glass), ("to", to)):
        if sel in cells:
            raise ValueError(f"{where}.{name}={sel!r} 不能同时是 cells 成员："
                             "量测锚点不参与折射，且它离场心为 0 时方向未定义；"
                             "另画一个小元素当锚点")
        if sel == "#" + CAM_ID:
            raise ValueError(f"{where}.{name} 不能是 #{CAM_ID}：相机层归整页取景，"
                             "换一个图里真实的元素当锚点")
    if "#" + CAM_ID in cells:
        raise ValueError(f"{where}.cells 不能含 #{CAM_ID}：相机层归整页取景，"
                         "收尾姿态由折层负责；给每个元素单独命名")
    amp = cfg.get("amplitude")
    if not _finite(amp) or abs(float(amp)) < 1e-9:
        raise ValueError(f"{where}.amplitude 必须是非零的有限数（峰值位移，SVG 单位；"
                         f"实际: {amp!r}）")
    mode = cfg.get("mode", LENS_DEFAULT_MODE)
    if mode not in _MODES:
        raise ValueError(f"{where}.mode 必须是 'bulge' / 'pinch' 之一"
                         f"（实际: {mode!r}）")
    return {"cells": list(cells), "glass": glass, "to": to,
            "amplitude": float(amp), "mode": mode}


def _bbox_of(root, el, parent, where, sel):
    """元素在 viewBox 系里的包围盒 → (cx, cy, w, h)。

    与 `_wave._center` 同一份几何口径：读同一组图元、同一份祖先仿射，只是这里
    还要宽度/高度（透镜的影响半径从它量）。不另写一份「大概在哪」。
    """
    boxes = []
    for node in el.iter():
        tag = _local(node.tag)
        if tag in ("g", "svg", "a", "switch", "defs", "clippath", "mask"):
            continue
        b = _box(node, tag)
        if b is not None:
            boxes.append(b)
    if not boxes:
        raise ValueError(f"{where} 的 {sel} 在 SVG 里读不出几何包围盒（元素"
                         "要画成 rect / circle / ellipse / line / polygon / "
                         "polyline / path / text / image，或包住这些图元的一层 <g>）")
    tx, ty, sx, sy = affine_to(root, el, parent, where)
    x0 = min(b[0] for b in boxes) * sx + tx
    x1 = max(b[1] for b in boxes) * sx + tx
    y0 = min(b[2] for b in boxes) * sy + ty
    y1 = max(b[3] for b in boxes) * sy + ty
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, abs(x1 - x0), abs(y1 - y0))


def _segment_distance(c, a, b):
    """点 c 到线段 a→b 的解析距离（用来拦「元素压在透镜轨迹上」）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 0:
        return math.hypot(c[0] - a[0], c[1] - a[1])
    u = _clamp(((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / l2)
    return math.hypot(c[0] - (a[0] + u * dx), c[1] - (a[1] + u * dy))


def _angle_deg(v0, v1):
    n0, n1 = math.hypot(*v0), math.hypot(*v1)
    if n0 <= 1e-12 or n1 <= 1e-12:
        return 0.0
    cos = _clamp((v0[0] * v1[0] + v0[1] * v1[1]) / (n0 * n1), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def geometry(svg_text, cfg, where="lens"):
    """按文件量几何：返回场心两端 / 影响半径 / 各元素中心 / 方向转角。

    这是**唯一**把 SVG 坐标变成数字的地方：契约层、渲染端、生成期体检、跨段
    烘焙都不另写一份「大概在哪」。改图即改编排，判据在 test_lens.py 里从产物
    抠出来对账。
    """
    c = validate(cfg, where)
    if not isinstance(svg_text, str) or not svg_text.strip():
        raise ValueError(f"{where} 要从内联 SVG 读几何，但这一页没有 SVG 源文本"
                         "（透镜档只能写在 .svg 上）")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValueError(f"{where} 的 SVG 解析失败：{e}") from e
    if _local(root.tag) != "svg":
        raise ValueError(f"{where} 的 SVG 根标签不是 <svg>")
    parent = {child: p for p in root.iter() for child in p}
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    ids = [c["glass"], c["to"]] + c["cells"]
    missing = sorted({s for s in ids if s[1:] not in by_id})
    if missing:
        raise ValueError(f"{where} 里的 " + "、".join(missing) + " 在 SVG 里找不到"
                         "对应 id 的元素——选择器落空不会报错，只会让那一片区域"
                         "静默不动")
    gx, gy, gw, gh = _bbox_of(root, by_id[c["glass"][1:]], parent, where, c["glass"])
    tx, ty, _, _ = _bbox_of(root, by_id[c["to"][1:]], parent, where, c["to"])
    radius = max(gw, gh) / 2.0
    if radius <= 1e-9:
        raise ValueError(f"{where} 的 {c['glass']} 包围盒退化成一个点（R=0）："
                         "影响半径量不出来，透镜要画成有宽高的圆或矩形")
    L0, L1 = (gx, gy), (tx, ty)
    travel = math.hypot(L1[0] - L0[0], L1[1] - L0[1])
    if travel < LENS_MIN_TRAVEL:
        raise ValueError(f"{where} 的场心行程 {travel:.4f} 小于 {LENS_MIN_TRAVEL}: "
                         "两个锚点几乎重合，方向不会转；把 "
                         f"{c['to']} 画远一些")
    centers = [_bbox_of(root, by_id[s[1:]], parent, where, s)[:2]
               for s in c["cells"]]
    # 净空：û=v/|v| 在 r→0 未定义，而 K(0)=1 恰是峰值位移。
    clearance = max(1.0, LENS_CLEARANCE_RATIO * radius)
    for sel, ctr in zip(c["cells"], centers):
        d = _segment_distance(ctr, L0, L1)
        if d < clearance:
            raise ValueError(f"{where} 的 {sel} 离透镜轨迹只有 {d:.2f}（要求 ≥"
                             f"{clearance:.2f}）：场心扫过它时方向未定义，且正是"
                             "峰值位移；把这个元素挪开轨迹，或把透镜画到别处")
    # 方向必须真的会转：至少一个元素在场心两端读到的 û 夹角够大。全落在轨迹
    # 直线上时所有 û 平行于行程，读成「固定震中推开的一组点」= 又一条 wave。
    turns = [_angle_deg((ctr[0] - L0[0], ctr[1] - L0[1]),
                        (ctr[0] - L1[0], ctr[1] - L1[1]))
             for ctr in centers]
    if max(turns) < LENS_MIN_TURN_DEG:
        raise ValueError(f"{where} 的所有元素都不在透镜轨迹的侧面（最大方向转角"
                         f"{max(turns):.2f}° < {LENS_MIN_TURN_DEG}°）：方向不转就"
                         "不是折射场，是 `references/wave.md` 的 radial 档；"
                         "把元素排到轨迹两侧，或把锚点画到别处")
    return {"L0": L0, "L1": L1, "radius": radius, "travel": travel,
            "centers": centers, "turns": turns}


def plan(cfg, duration, fps, svg_text, target, where="lens"):
    """把一条 `lens` 拍编译成可采样的计划。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur, f = plan_bounds(duration, fps, where, min_duration=MIN_DURATION)
    if c["cells"][0] != target:
        raise ValueError(f"{where}.cells[0] 必须等于 target={target!r}：折射头就是"
                         "这一拍的目标，接续烘焙与按文件拦都只认 step 级 target"
                         f"（实际: {c['cells'][0]!r}）")
    g = geometry(svg_text, c, where)
    return {"cfg": c, "duration": dur, "fps": f, "target": target,
            "geometry": g, "selectors": list(c["cells"])}


def lens_center(t, p):
    """t 时刻的场心坐标（纯函数）。"""
    g = p["geometry"]
    u = _clamp(t / p["duration"])
    return (g["L0"][0] + (g["L1"][0] - g["L0"][0]) * u,
            g["L0"][1] + (g["L1"][1] - g["L0"][1]) * u)


def kernel(q):
    """紧支撑 bulge 核：q=1 处值与一阶导数都精确归零。"""
    if q >= 1.0:
        return 0.0
    s = 1.0 - q * q
    return s * s


def envelope(t, p):
    """时间包络 sin²(πt/D)，端点显式精确 0（math.sin(math.pi) 不是精确 0）。"""
    if t <= 0.0 or t >= p["duration"]:
        return 0.0
    return math.sin(math.pi * t / p["duration"]) ** 2


def state_at(t, p, i):
    """第 i 个元素在时刻 `t`（相对拍起点）的补间变量，**纯函数**。

    不读墙钟、不存上一帧：逐帧 `seek()` 下每一帧都由 `t` 独立算出。方向 ûᵢ(t)
    由**当前**场心算出，因此同一元素在拍内持续转向——这是本档与 `wave.radial`
    / `flow` 最本质的分界。
    """
    if not 0 <= i < len(p["selectors"]):
        raise ValueError(f"元素下标 {i} 越界（共 {len(p['selectors'])} 个）")
    t = _clamp(float(t), 0.0, p["duration"])
    u = envelope(t, p)
    if u == 0.0:
        return {"x": 0.0, "y": 0.0}
    g = p["geometry"]
    ctr = g["centers"][i]
    L = lens_center(t, p)
    vx, vy = ctr[0] - L[0], ctr[1] - L[1]
    r = math.hypot(vx, vy)
    k = kernel(r / g["radius"])
    if k == 0.0 or r <= 1e-12:
        return {"x": 0.0, "y": 0.0}
    sign = 1.0 if p["cfg"]["mode"] == "bulge" else -1.0
    a = p["cfg"]["amplitude"] * u * k * sign
    return {"x": _clean(a * vx / r), "y": _clean(a * vy / r)}


def peak_time(p, i):
    """第 i 个元素位移最大的时刻：由场心扫到它附近的时间与 u(t) 共同决定，
    生成期在 [0,D] 上实采（不是手算一个闭式——K 与 u 的乘积没有好用的解析解）。"""
    best_t, best_v = 0.0, -1.0
    for k in range(401):
        t = p["duration"] * k / 400
        s = state_at(t, p, i)
        mag = math.hypot(s["x"], s["y"])
        if mag > best_v:
            best_v, best_t = mag, t
    return best_t


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


def settle_state(cfg, where="lens"):
    """跨段接续的收尾态：空。

    u(0)=u(D)=0 让每个元素在 D 处精确回 0，没有残留姿态可折。这个函数仍要
    **显式存在**并被 `_stage_carry` 调用：靠「这一拍没有 set/to、恰好 no-op」
    的巧合，将来给这一档加任何终态时，页界就会静默闪一下。
    """
    validate(cfg, where)
    return []


def diagnose(p):
    """逐条列出没过的红线，给生成期报错用（空列表 = 全过）。

    判据全部从 `state_at` / `frames` / 几何反推，不复述配置——否则就成了
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
            bad.append("收尾没把折射收干净（跨段会留半截位移）")
        # 拍外必须精确 0（不是「很小」）；圈外元素同理
        for t in (-0.5, dur + 0.5):
            if any(abs(v) > PRODUCT_EPS for v in state_at(t, p, i).values()):
                bad.append("时间包络不是紧支撑（拍外还在动）")
    # 方向的时间性：至少一个元素在整拍里真的转过 LENS_MIN_TURN_DEG
    if max(g["turns"]) < LENS_MIN_TURN_DEG:
        bad.append("没有任何元素的位移方向随时间改变（退化成 wave.radial）")
    # 峰值与活跃元素数：透镜一次都没扫到 = 一整段静帧，几何决定，只能实采判
    peaks = []
    for i in range(n):
        def _mag(t, i=i):
            s = state_at(t, p, i)
            return math.hypot(s["x"], s["y"])
        peaks.append(max(_mag(dur * k / 200) for k in range(201)))
    if max(peaks) < LENS_PEAK_FLOOR * abs(c["amplitude"]):
        bad.append("透镜没扫到任何元素（峰值位移太小，成片是静帧）")
    if sum(1 for v in peaks if v >= LENS_ACTIVE_RATIO * abs(c["amplitude"])) \
            < LENS_ACTIVE_CELLS_MIN:
        bad.append("只有一个元素被折射（读成单元素位移，不是场）")
    # bulge / pinch 只换方向，不换幅度序列
    flipped = dict(c, mode="pinch" if c["mode"] == "bulge" else "bulge")
    fp = dict(p, cfg=flipped)
    for i in range(n):
        for t in (dur * 0.25, dur * 0.5, dur * 0.75):
            a, b = state_at(t, p, i), state_at(t, fp, i)
            if abs(a["x"] + b["x"]) > PRODUCT_EPS or abs(a["y"] + b["y"]) > PRODUCT_EPS:
                bad.append("bulge / pinch 不是精确反向")
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
