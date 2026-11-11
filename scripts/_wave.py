#!/usr/bin/env python3
"""波场档的单一真源：把「元素在图上的坐标」当成这一拍的输入。

已有档位把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度包络（`momentum`）、相位（`drag`）、时间量化（`step`）、
静止（`hold`）、时序锚点（`stagger` / `decode`）、空间曲率（`arc`）、值域不规则
（`organic`）、时间叠加（`trail`）。它们还有一条从没人写破的共同前提：**编排里
没有任何一个量是从「这些元素彼此离多远」读出来的**。`stagger` 的错峰由序号与
分配曲线给，`drag` 的相位由链序给，`arc` 的曲率是单个元素两条轴的事；一张图里
二十个点排成圆还是排成一列，对已有档位完全一样——延迟是作者排出来的，不是画
出来的。

2025/2026 的前沿 MG 有一支正好在打这条前提：dot-matrix / halftone ripple、
kinetic point-cloud wave（品牌页与数据叙事里最常见的一支），以及 AE Wave Warp
那类 displacement wave。它们的共同手感不是「一组元素逐个出现」，而是**一个扰动
从某处出发，按空间距离传过去，每个元素只在波前经过它的一小段时间里动一下，
然后回到原位**。GSAP 的 `stagger` 配 `grid:"auto"` + `from:"center"` 最接近，
但它按**网格行列号**分配 delay、每个元素演完整行程并留在终态、没有衰减也没有
波速——那是「按序号错峰」，不是「按距离传播」。

本档负责**空间传播域**，几何由生成期从 SVG 里量出来，作者不写坐标：

```
c₀   = center(origin)                       震中元素的包围盒中心（图上真实元素）
cᵢ   = center(cells[i])                     每个格子 likewise
rᵢ   = |cᵢ − c₀|                            欧氏距离，唯一真源是 SVG 几何
rₘ   = max rᵢ
τᵢ   = span · rᵢ / rₘ                       波前到达时刻，严格随距离单调
sᵢ(t)= (t − τᵢ) / width                     该格子的局部进度
E(s) = sin²(πs),  s∈(0,1)，否则 0            紧支撑脉冲：端点与端点导数精确归零
qᵢ   = (rᵢ − rₘᵢₙ) / (rₘ − rₘᵢₙ)            相对距离，最近格子 0、最远 1
Fᵢ   = exp(−falloff · qᵢ)                   距离衰减，最近精确 1、最远 e^−falloff
aᵢ(t)= amplitude · Fᵢ · E(sᵢ(t))            位移标量，radial 档再乘方向单位向量
```

衰减归一到**最近**而非原点：若写成 `exp(−falloff·rᵢ/rₘ)`，即使最近格子也会先
被削掉一截，`amplitude` 就不再是「峰值位移」而是「峰值位移再乘一个谁也算不准的
系数」——作者调旋钮时读不出画面会有多大幅度。归一之后 `amplitude` 就是这一拍
真正能达到的最大位移，判据也才有精确可钉的端点。

**四条设计决定，都是被成片纪律逼出来的**：

1. **延迟不进契约**。`cells` 只写「哪些元素参与」，`span` 只写「波前扫完整个场
   用多久」；τᵢ 由 rᵢ/rₘ 归一算出。作者一旦能手写每个元素的 delay，那串数字
   就是几何的第二份真源——挪动图里一个点，成片里的波前仍停在旧位置，且零报错。
   这也是本档与 `stagger` 最本质的分界：**波前不听序号的话**。产物里 `tl.set`
   的写入顺序是作者给的 `cells` 序，峰值时刻却只按距离排，两者不必一致。
2. **紧支撑脉冲，不是位移曲线**。`E` 在 `[τᵢ, τᵢ+width]` 之外精确为 0：波前没到
   时格子完全静止，过去之后精确回原位。于是 `state_at(0) == state_at(D) == 0`，
   **收尾态 = 起始态**，跨段接续没有残留姿态可折（但仍要显式认这一拍，见下）。
   `drag` 是全链重参数化后一起落定在 `distance`，`stagger` 是逐个留在终态，
   两者都不会回去——「波过无痕」是这一档独有的语义。
3. **归一到最远距离，不认 SVG 单位**。`τᵢ = span·rᵢ/rₘ` 让 `span` 成为纯时间量：
   竖屏 1080×1440 与横屏 1920×1080 的同一份编排，波前都在 `span` 秒扫完，
   不会因为换画幅把波速改掉。`rₘ` 由这一拍实际参与的格子决定，不是 viewBox 尺寸。
4. **波必须在拍内收干净**。`span + width ≤ duration` 是生成期硬红线：τᵢ 依赖几何，
   作者根本没法在写稿时预知最远那排格子什么时候演完。放宽这条，最后一排会在
   被切到下一段时还在位移，页界一次静默的「半截波」。

**生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step` /
`organic` / `trail` 的老路：渲染端零脚本、零 `onUpdate`、零依赖，成片里是一串
确定的数值，逐帧 `seek()` 下每一帧都是时间的纯函数。渲染端 `onUpdate` 按距离
实时算延迟、AE Echo 式带状态的位移场都会让第 N 帧与直接跳到第 N 帧对不上，
Python 侧判据也会退化成对着另一份 JS 自证（`decode.md` / `spring.md` 记过这笔账）。

**格子与震中必须是图里真实存在的元素**：离散 `tl.set` 没法凭空造 DOM，也不该在
渲染端克隆节点（同一张 SVG 内联两份时 id 重复、`url(#id)` 只认第一份，见
`references/motion.md`「跨段接续」）。`cells` 收一列有序 `#id`、`origin` 收一个
`#id`，生成期按文件逐个拦；落空一个不会报错，只会让那一片区域静默不动。

**几何口径**：包围盒走共享的 `_cam_crop._box`（与 `check_svg` 的空带/越界、
`_cam_crop` 的运镜取景同一份），祖先的 translate/scale 由 `decompose_transform`
复合；旋转/斜切读不准就判废并出声，不猜一个中心。目标元素内部不再折 transform
——那要在这里复刻第二套仿射栈，与「真源不分家」相冲，红线写进 `references/wave.md`。

**跨段接续**：每个格子收尾都精确回 0，没有残留。`_stage_carry` 仍必须**显式认
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
from _cam_crop import CAM_ID, decompose_transform, _box
from _svg_sanitize import _local
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)
from _field import (clamp as _clamp, clean as _clean, frame_count,  # noqa: E402  采样骨架只有一份
                    sample_frames)

# ── 红线（工程判据，不进模板、不给 --set 改）──────────────────────────
# 参与格子数。1 个谈不上「传」，多于 12 个在 24fps 下每帧 12 条以上 tl.set，
# 观众也数不出波前。
CELLS_MIN = 2
CELLS_MAX = 12
# 距离衰减强度上限。2.0 时最远格子的振幅只剩 e^-2≈0.135，再大远处等于没动；
# 下界 0 = 全场等幅（合法的「不衰减波」）。
FALLOFF_MAX = 2.0

DEFAULT_SPAN = 0.50      # 波前从震中扫到最远格子用 0.5s
DEFAULT_WIDTH = 0.34     # 单个格子被波前经过时动 0.34s
DEFAULT_FALLOFF = 0.45

_WAVE_KEYS = frozenset({"cells", "origin", "axis", "amplitude",
                        "span", "width", "falloff"})
# 只收单个 #id：一组同 class 的元素没法用离散 tl.set 分别写值。
_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")
_AXES = frozenset({"x", "y", "radial"})


def validate(cfg, where="wave"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象、不读 SVG）。

    契约层（`_images_schema`）、渲染端（`html_renderer`）、生成期体检（`_animate`）
    调的是**同一个函数**：红线写在消费侧，`--set` 或手改过的 images.json 把旋钮
    调坏时，生成期就炸，不留到成片里静默变成一组不动的点。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _WAVE_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_WAVE_KEYS)}")
    cells = cfg.get("cells")
    if not isinstance(cells, list) or not (CELLS_MIN <= len(cells) <= CELLS_MAX):
        raise ValueError(f"{where}.cells 必须是 {CELLS_MIN}~{CELLS_MAX} 个 #id 的"
                         f"有序列表（图里画好的一组格子；实际: {cells!r}）")
    for sel in cells:
        if not isinstance(sel, str) or not _ID_RE.match(sel):
            raise ValueError(f"{where}.cells 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(cells)) != len(cells):
        raise ValueError(f"{where}.cells 不能有重复 id：同一元素领两份波前会被"
                         "后写的那条静默覆盖")
    if "#" + CAM_ID in cells:
        raise ValueError(f"{where}.cells 不能含 #{CAM_ID}：相机层归整页取景，"
                         "收尾姿态由折层负责；给每个格子单独命名")
    origin = cfg.get("origin")
    if not isinstance(origin, str) or not _ID_RE.match(origin):
        raise ValueError(f"{where}.origin 必须是单个 #id（震中元素，生成期从图上"
                         f"读它的中心；实际: {origin!r}）")
    if origin in cells:
        raise ValueError(f"{where}.origin={origin!r} 不能同时是 cells 成员："
                         "震中与自己距离为 0，波前与 radial 方向都无从定义")
    if origin == "#" + CAM_ID:
        raise ValueError(f"{where}.origin 不能是 #{CAM_ID}：相机层归整页取景，"
                         "换一个图里真实的元素当震中")
    axis = cfg.get("axis")
    if axis not in _AXES:
        raise ValueError(f"{where}.axis 必须是 'x' / 'y' / 'radial' 之一"
                         f"（实际: {axis!r}）")
    amp = cfg.get("amplitude")
    if not _finite(amp) or abs(float(amp)) < 1e-9:
        raise ValueError(f"{where}.amplitude 必须是非零有限数（SVG 用户单位的"
                         f"峰值位移；实际: {amp!r}）")
    span = cfg.get("span", DEFAULT_SPAN)
    if not _finite(span) or float(span) <= 0.0:
        raise ValueError(f"{where}.span 必须是 >0 的秒数（波前扫完整个场用时；"
                         f"实际: {span!r}）")
    width = cfg.get("width", DEFAULT_WIDTH)
    if not _finite(width) or float(width) <= 0.0:
        raise ValueError(f"{where}.width 必须是 >0 的秒数（单个格子的脉冲时长；"
                         f"实际: {width!r}）")
    falloff = cfg.get("falloff", DEFAULT_FALLOFF)
    if not _finite(falloff) or not (0.0 <= float(falloff) <= FALLOFF_MAX):
        raise ValueError(f"{where}.falloff 必须在 [0, {FALLOFF_MAX:.1f}] 之间"
                         f"（距离衰减强度；实际: {falloff!r}）")
    return {"cells": list(cells), "origin": origin, "axis": axis,
            "amplitude": float(amp), "span": float(span),
            "width": float(width), "falloff": float(falloff)}


# ── 几何：唯一从 SVG 读出来的量 ────────────────────────────────────────

def _affine_to(root, el, parent, where):
    """从 root 累加到 el 的 translate/scale → (tx, ty, sx, sy)。

    与 `_cam_crop._walk` 同一份分解口径：读不懂的 transform（rotate/matrix/skew）
    直接判废，不猜。宁可让这一拍在生成期炸，也不能让波前对着一个错中心算。
    """
    chain = []
    cur = el
    while cur is not None and cur is not root:
        chain.append(cur)
        cur = parent.get(cur)
    tx = ty = 0.0
    sx = sy = 1.0
    for node in reversed(chain):
        dec = decompose_transform(node)
        if dec is None:
            raise ValueError(f"{where} 的几何读不出来：元素带 rotate / matrix / "
                             "skew 这类 transform，生成期不猜中心（把祖先变换"
                             "改成 translate/scale，或去掉）")
        ox, oy, kx, ky = dec
        tx, ty = tx + sx * ox, ty + sy * oy
        sx, sy = sx * kx, sy * ky
    return tx, ty, sx, sy


def _center(root, el, parent, where, sel):
    """元素在 viewBox 系里的包围盒中心。`<g>` 取自身与后代图元的并集。"""
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
    tx, ty, sx, sy = _affine_to(root, el, parent, where)
    x0 = min(b[0] for b in boxes) * sx + tx
    x1 = max(b[1] for b in boxes) * sx + tx
    y0 = min(b[2] for b in boxes) * sy + ty
    y1 = max(b[3] for b in boxes) * sy + ty
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


# 公开别名：包围盒中心与祖先仿射只有这一份口径。`_flow` 量「元素投影到曲线」
# 时读的是与波场档同一套坐标，不另写一份「大概在哪」。私有名保留给既有调用点。
affine_to = _affine_to
center_of = _center


def geometry(svg_text, cfg, where="wave"):
    """按文件量几何：返回 centers / 震中 / 距离 / 方向 / 最远距离。

    这是**唯一**把 SVG 坐标变成数字的地方：契约层、渲染端、生成期体检、跨段
    烘焙都不另写一份「大概在哪」。改图即改编排，判据在 test_wave.py 里从产物
    抠出来对账。
    """
    c = validate(cfg, where)
    if not isinstance(svg_text, str) or not svg_text.strip():
        raise ValueError(f"{where} 要从内联 SVG 读几何，但这一页没有 SVG 源文本"
                         "（波场档只能写在 .svg 上）")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValueError(f"{where} 的 SVG 解析失败：{e}") from e
    if _local(root.tag) != "svg":
        raise ValueError(f"{where} 的 SVG 根标签不是 <svg>")
    parent = {child: p for p in root.iter() for child in p}
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    missing = sorted({s for s in [c["origin"]] + c["cells"]
                      if s[1:] not in by_id})
    if missing:
        raise ValueError(f"{where} 里的 " + "、".join(missing) + " 在 SVG 里找不到"
                         "对应 id 的元素——选择器落空不会报错，只会让那一片区域"
                         "静默不动")
    origin = _center(root, by_id[c["origin"][1:]], parent, where, c["origin"])
    centers = [_center(root, by_id[s[1:]], parent, where, s) for s in c["cells"]]
    radii = [math.hypot(cx - origin[0], cy - origin[1]) for cx, cy in centers]
    r_max = max(radii)
    if r_max <= 1e-9:
        raise ValueError(f"{where} 的所有格子都与震中重合：波前无从传播")
    if min(radii) <= 1e-9:
        raise ValueError(f"{where} 有格子与震中重合（距离 0）：radial 档方向未定义，"
                         "且它的波前恒在 0 秒，读成一次整体脉动而不是传播")
    # 至少两个不同距离：所有格子等距时 τ 全相同，波退化成「一组同步脉动」。
    spread = max(radii) - min(radii)
    if spread <= 1e-6 * r_max:
        raise ValueError(f"{where} 的格子离震中全都一样远（r 跨度 {spread:.4f}）："
                         "这是一组同步脉动，不是波；把格子排成不同距离，或用 stagger")
    vectors = [(cx - origin[0], cy - origin[1]) for cx, cy in centers]
    return {"origin": origin, "centers": centers, "radii": radii,
            "r_max": r_max, "vectors": vectors}


def plan(cfg, duration, fps, svg_text, target, where="wave"):
    """把一条 `wave` 拍编译成可采样的计划。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur, f = plan_bounds(duration, fps, where, min_duration=MIN_DURATION)
    if c["cells"][0] != target:
        raise ValueError(f"{where}.cells[0] 必须等于 target={target!r}：波头就是"
                         "这一拍的目标，接续烘焙与按文件拦都只认 step 级 target"
                         f"（实际: {c['cells'][0]!r}）")
    frame = 1.0 / f
    if c["span"] < frame - 1e-12:
        raise ValueError(f"{where} 的 span={c['span']}s 小于一帧（1/{f:.0f}fps = "
                         f"{frame:.4f}s）：波前扫得比渲染帧还快，观众只看见"
                         "整场一起动，读不出传播")
    if c["width"] < frame - 1e-12:
        raise ValueError(f"{where} 的 width={c['width']}s 小于一帧：单个格子的"
                         "脉冲会落进同一个渲染帧里，静默少一拍")
    if c["span"] + c["width"] > dur + 1e-12:
        raise ValueError(f"{where} 的 span+width={c['span'] + c['width']:.2f}s "
                         f"超过拍长 {dur:.2f}s：最远那排格子还没回原位就被切到"
                         "下一段（调小 span/width 或加长这一拍）")
    g = geometry(svg_text, c, where)
    arrivals = [c["span"] * r / g["r_max"] for r in g["radii"]]
    r_min = min(g["radii"])
    r_span = g["r_max"] - r_min        # geometry 已拦住「所有格子等距」，必 >0
    falloffs = [math.exp(-c["falloff"] * (r - r_min) / r_span)
                for r in g["radii"]]
    return {"cfg": c, "duration": dur, "fps": f, "target": target,
            "geometry": g, "arrivals": arrivals, "falloffs": falloffs,
            "selectors": list(c["cells"])}


def wavefront(p, i):
    """第 i 个格子的波前到达时刻 τᵢ（秒，相对拍起点）。"""
    return p["arrivals"][i]


def peak_time(p, i):
    """第 i 个格子的峰值时刻 τᵢ + width/2：传播顺序由它读，不由 cells 序号读。"""
    return p["arrivals"][i] + p["cfg"]["width"] / 2.0


def state_at(t, p, i):
    """第 i 个格子在时刻 `t`（相对拍起点）的补间变量，**纯函数**。

    不读墙钟、不存上一帧：逐帧 `seek()` 下每一帧都由 `t` 独立算出。`E` 是紧支撑
    的 sin² 窗，端点与端点导数精确归零，所以波前未到 / 已过 / 拍外都是精确 0。
    """
    if not 0 <= i < len(p["selectors"]):
        raise ValueError(f"格子下标 {i} 越界（共 {len(p['selectors'])} 个）")
    c = p["cfg"]
    t = _clamp(float(t), 0.0, p["duration"])
    s = (t - p["arrivals"][i]) / c["width"]
    if s <= 0.0 or s >= 1.0:
        a = 0.0                      # 显式短路，避免浮点残差写进产物
    else:
        a = c["amplitude"] * p["falloffs"][i] * math.sin(math.pi * s) ** 2
    axis = c["axis"]
    if axis == "radial":
        dx, dy = p["geometry"]["vectors"][i]
        r = p["geometry"]["radii"][i]
        return {"x": _clean(a * dx / r), "y": _clean(a * dy / r)}
    return {axis: _clean(a)}


def frames(p):
    """生成期采样：`[(相对秒, 选择器, 补间变量), …]`，每帧每格子一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位；排序键是 (时刻, 格子下标)，不是 id 字面量——
    写入顺序是作者给的 `cells` 序（导演意图），不能被字典序洗掉。
    """
    return sample_frames(p, state_at)


def start(p, i=0):
    return state_at(0.0, p, i)


def landing(p, i=0):
    return state_at(p["duration"], p, i)


def settle_state(cfg, where="wave"):
    """跨段接续的收尾态：空。

    紧支撑脉冲让每个格子在 D 处精确回 0，没有残留姿态可折。这个函数仍要**显式
    存在**并被 `_stage_carry` 调用：靠「这一拍没有 set/to、恰好 no-op」的巧合，
    将来给波场档加任何终态时，页界就会静默闪一下。
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
            bad.append("段首有格子提前位移")
        if any(abs(v) > PRODUCT_EPS for v in landing(p, i).values()):
            bad.append("收尾没把波收干净（跨段会留半截波）")
        # 紧支撑：波前未到、波已过，位移都必须精确为 0
        tau = p["arrivals"][i]
        for t in (tau - 1e-6, tau + c["width"] + 1e-6):
            if 0.0 < t < dur and any(abs(v) > PRODUCT_EPS
                                     for v in state_at(t, p, i).values()):
                bad.append("脉冲窗不是紧支撑（波前之外还在动）")
    # 传播顺序由距离给：按 r 排序后到达时刻非降，且至少两个不同值
    order = sorted(range(n), key=lambda i: g["radii"][i])
    taus = [p["arrivals"][i] for i in order]
    if any(b < a - PRODUCT_EPS for a, b in zip(taus, taus[1:])):
        bad.append("波前到达时刻没有随距离递增（不是传播）")
    if taus[-1] - taus[0] <= PRODUCT_EPS:
        bad.append("所有格子的波前同时到达（退化成同步脉动）")
    # 峰值位移随距离衰减：近处不弱于远处
    peaks = [abs(c["amplitude"]) * p["falloffs"][i] for i in order]
    if any(b > a + PRODUCT_EPS for a, b in zip(peaks, peaks[1:])):
        bad.append("远端格子的峰值比近端更大（衰减写反了）")
    if abs(peaks[0] - abs(c["amplitude"])) > PRODUCT_EPS:
        bad.append("最近格子的峰值没等于 amplitude")
    # 波必须在拍内收干净；波前与脉冲都不得短于一帧
    if max(p["arrivals"]) + c["width"] > dur + PRODUCT_EPS:
        bad.append("波没在拍内收干净")
    frame = 1.0 / p["fps"]
    if c["span"] < frame - 1e-12:
        bad.append("span 小于一帧")
    if c["width"] < frame - 1e-12:
        bad.append("width 小于一帧")
    # 采样网格：条数 = (帧数+1)×格子数，时刻不撞进同一个 0.01s 格
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
