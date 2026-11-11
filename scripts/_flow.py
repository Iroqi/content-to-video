#!/usr/bin/env python3
"""流线档的单一真源：第一次把「图上画的那条曲线」当成这一拍的运动场。

本仓库此前把一条运动能拆的域都拆完了：位置/形状（`to` / `morph` / `draw`）、
阻尼（`spring`）、速度包络（`momentum`）、相位（`drag`）、时间量化（`step`）、
静止（`hold`）、序号锚点（`stagger` / `decode`）、单元素空间曲率（`arc`）、值域
不规则（`organic`）、时间叠加（`trail`）、距离→延迟的空间传播（`wave`）。它们有
一条从没人写破的共同前提：**编排里没有任何一个量是从「一条画出来的曲线的形状」
读出来的**。`morph` 把 path 当成要被变形的**对象**，`params`+`bind` 让作者自己
写一条算术表达式算出几何（那是图之外的第二份真源），`arc` 是单个元素两条轴的
合成，`wave.radial` 读的是「元素到震中」的直线方向——恒为从一点向外辐射。

2025/2026 的前沿 MG 有一支正好在打这条前提：GSAP MotionPath / AE 的
"Create Nulls from Paths" 让一组物体沿一条**画好的曲线**走；Cavalry 一类节点式
工具把「场」交给几何而不是交给作者填的数字；2026 趋势里的 flowing organic
movements / liquid ribbon / optical-flow motion field / depth-driven staging，
共同手感都是「一整片元素被一条看不见的河流带着走，不同位置的元素朝不同方向
弯过去」，而不是「一组元素逐个出现」或「一个扰动从中心扩散」。

这一档补的是**曲线场域**：一组元素各自从自己在图上的位置出发，沿一条真实
`<path>` 的弧长前进，位移向量由曲线在它那个弧长处的形状给出。作者不写坐标、
不写每个元素的方向、不写延迟——**改曲线即改编排**：

```
P(s)  = path 的弧长参数化（生成期把 d 采样成折线，建 s → (x,y) 表）
cᵢ    = center(riders[i])                      元素包围盒中心（图上真实元素）
sᵢ    = argmin_s |P(s) − cᵢ|                   投影到曲线上的弧长坐标，唯一真源
L     = 曲线总弧长
qᵢ    = (sᵢ − s_min) / (s_max − s_min)         相对弧长，最近的 0、最远的 1
dᵢ    = distance · exp(−taper · qᵢ)            每个元素实际前进的弧长
u(t)  = 1 − (1 − t/D)³                        生成期算死的 .out 包络：起步就有位移、到位停住
Sᵢ(t) = sᵢ + sign · dᵢ · u(t)                 沿曲线走到的弧长处
Δᵢ(t) = P(Sᵢ(t)) − P(sᵢ)                      相对位移：t=0 精确 0，段首不跳
state = {"x": Δx, "y": Δy}                    逐帧 tl.set 的补间变量
```

**不把元素搬到曲线上**：`Δ` 是相对位移，元素从自己原来的中心出发，走的是曲线
那一整段的**平移副本**。若改成 `P(Sᵢ(t)) − cᵢ`（贴到曲线上），段首第一帧就会
把整组元素从画好的位置瞬移到曲线上——正是 `wave.md` 记过的「页界静默错」
在段内的版本。曲线是**位移场**，不是轨道。

**五条设计决定，都是被成片纪律逼出来的**：

1. **相位不进契约**。`riders` 只写「哪些元素参与」，`path` 只写「哪条曲线」；
   sᵢ 由投影算出。作者一旦能手写每个元素的 `delay` / `from` 位置，那串数字就是
   几何的第二份真源——改曲线的控制点，成片里各元素的起跑位置仍停在旧地方，
   且零报错。本档存在的全部理由就是消灭这份手写。
2. **曲线必须真的会转弯**。生成期按各 rider 的 sᵢ 取切线单位向量，要求至少两处
   方向不同（弦距 ≥ `FLOW_MIN_TURN_DEG` 对应的量）。全落在直线段上时所有 Δ 平行，
   读成「整块平移」而不是「流场」——这是**这一拍根本不是流**，不是参数越界，
   按文件拦。与波场档「格子全等距即同步脉动」那条判据同一类。
3. **流必须在曲线内走完**。`max(sᵢ + dᵢ) ≤ L`（backward 为 `min(sᵢ − dᵢ) ≥ 0`）
   是生成期硬红线：sᵢ 依赖几何，作者写稿时没法预知最靠后的元素离曲线终点还有
   多远。放宽这条，它会静默停在曲线末端，成片里是「流到一半卡住」。
4. **落点是一个几何量，不是常量**。与 `drag` 的「全链终点都是 distance」不同，
   每个 rider 的收尾位移是 `P(sᵢ + sign·dᵢ) − P(sᵢ)`，由曲线形状算出来。所以
   `_stage_carry` 必须**读这一页的 SVG** 才能把每个元素折到正确的位置，不能像
   `trail` / `drag` 那样只凭配置拼一个常量。这是本档独有的接续判据。
5. **生成期采样成离散 `tl.set`**，沿用 `morph` / `momentum` / `drag` / `step` /
   `organic` / `trail` / `wave` 的老路：渲染端零脚本、零 `onUpdate`、零依赖，成片
   里是一串确定的数值，逐帧 `seek()` 下每一帧都是时间的纯函数。渲染端用
   MotionPathPlugin 之类按时间实时求值、或带累计状态的位移场，都会让第 N 帧与
   直接跳到第 N 帧对不上，Python 侧判据也只能退化成对着另一份 JS 自证。

**不写 `rotation`（自动朝向）**：MotionPath 的 auto-orient 会把切线角变成元素的
旋转，但本仓库的接续烘焙只搬得动 translate（ `_stage_carry._carry_translate`），
绕元素 bbox 中心的 rotation 折不进内联副本；且 `references/motion.md`
「六种够用的演法」明确把整页旋转读作「页面坏了」。这一档因此只写 x/y：
**方向由位移向量给，不由朝向给**。要一个元素真的转过去，用拍级
`to:{"rotation":…}` 自己写。

**几何口径**：包围盒与祖先仿射复用 `_wave.center_of` / `_wave.affine_to`（与
`check_svg` 的空带/越界、`_cam_crop` 的运镜取景、波场档量距离同一份），读不懂的
rotate / matrix / skew 直接判废并出声，不猜一个中心。**rider 自身不许带
transform**：生成期写 `x`/`y` 会覆盖元素自带的平移，段首静默跳一下；把平移画进
`x`/`y` 属性或包一层 `<g>`。详见 `references/flow.md`。

**path 命令子集**：只认 `M L H V C Q Z`（大小写、隐式重复都收），弧长按每条曲线
固定 `FLOW_CURVE_SAMPLES` 段采样。`A / S / T` 明确拒并指路——椭圆弧要把 7 参
数转成采样点，等于在本模块里复刻第二套路径求值；换成 `C`/`Q` 或折线就能画同一
条曲线，不该为它把「几何只有一份真源」拆开。
"""
import bisect
import math
import re
import xml.etree.ElementTree as ET

# 采样带、端点精度、最短拍长全部复用既有档的真源：它们是全仓库共用的工程判据，
# 写成第二份字面量就会改一处漏一处（test_shared_geometry 钉着这条纪律）。
from _drag import PRODUCT_EPS
from _organic import MIN_DURATION
from _cam_crop import CAM_ID
from _svg_sanitize import _local
from _path_morph import parse_path
from _wave import affine_to, center_of  # noqa: E402  包围盒/仿射口径只有一份
from _validate import (is_finite_number as _finite, plan_bounds,  # noqa: E402  数值守卫 / plan 入口只有一份真源
                       plan_duration)
from _field import (clamp as _clamp, clean as _clean, frame_count,  # noqa: E402  采样骨架只有一份
                    sample_frames)

# ── 红线（工程判据，不进模板、不给 --set 改）──────────────────────────
# 参与元素数。1 个谈不上「一整片被带着走」；多于 12 个在 24fps 下每帧 12 条
# 以上 tl.set，观众也数不出不同方向的弯。
FLOW_RIDERS_MIN = 2
FLOW_RIDERS_MAX = 12
# 沿弧长的幅度衰减上限。2.0 时最靠后元素只走 e^-2≈13.5%，再大等于没动。
FLOW_TAPER_MAX = 2.0
# 最短前进弧长：小于这个量在 24fps 下一帧挪不到一像素，读成静帧。
FLOW_MIN_DISTANCE = 8.0
# 「曲线真的会转弯」的下限（度）：各 rider 处切线单位向量的最大弦距换算成角度。
FLOW_MIN_TURN_DEG = 6.0
# 每条 C/Q 曲线的采样段数。固定值而非自适应：逐帧可复现要求同一份 d 永远
# 得到同一张弧长表，自适应步长会随实现版本漂移。
FLOW_CURVE_SAMPLES = 24

FLOW_DEFAULT_DIRECTION = "forward"
FLOW_DEFAULT_TAPER = 0.0

_FLOW_KEYS = frozenset({"riders", "path", "distance", "direction", "taper"})
# 只收单个 #id：离散 tl.set 没法给一组同名元素分别写值。
_ID_RE = re.compile(r"^#[A-Za-z_][A-Za-z0-9_-]*$")
_DIRECTIONS = frozenset({"forward", "backward"})


def validate(cfg, where="flow"):
    """契约形状 + 红线一次验完，返回归一化后的配置（不改传入对象、不读 SVG）。

    契约层（`_images_schema`）、渲染端（`html_renderer`）、生成期体检（`_animate`）、
    跨段接续烘焙（`_stage_carry`）调的是**同一个函数**：红线写在消费侧，`--set`
    或手改过的 images.json 把旋钮调坏时，生成期就炸，不留到成片里静默变成一组
    不动的元素。
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"{where} 必须是对象")
    unknown = set(cfg) - _FLOW_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                         f"{sorted(_FLOW_KEYS)}")
    riders = cfg.get("riders")
    if not isinstance(riders, list) or not (FLOW_RIDERS_MIN <= len(riders) <= FLOW_RIDERS_MAX):
        raise ValueError(f"{where}.riders 必须是 {FLOW_RIDERS_MIN}~{FLOW_RIDERS_MAX} "
                         f"个 #id 的有序列表（图里画好的一组元素；实际: {riders!r}）")
    for sel in riders:
        if not isinstance(sel, str) or not _ID_RE.match(sel):
            raise ValueError(f"{where}.riders 成员必须是单个 #id（不能用 .class："
                             f"离散 tl.set 没法给一组同名元素分别写值；实际: {sel!r}）")
    if len(set(riders)) != len(riders):
        raise ValueError(f"{where}.riders 不能有重复 id：同一元素领两份位移会被"
                         "后写的那条静默覆盖")
    if "#" + CAM_ID in riders:
        raise ValueError(f"{where}.riders 不能含 #{CAM_ID}：相机层归整页取景，"
                         "给每个元素单独命名")
    path = cfg.get("path")
    if not isinstance(path, str) or not _ID_RE.match(path):
        raise ValueError(f"{where}.path 必须是单个 #id（图里一条真实的 <path>，"
                         f"生成期从它的 d 读几何；实际: {path!r}）")
    if path in riders:
        raise ValueError(f"{where}.path={path!r} 不能同时是 riders 成员：曲线是"
                         "位移场，不是被带着走的元素")
    if path == "#" + CAM_ID:
        raise ValueError(f"{where}.path 不能是 #{CAM_ID}：相机层归整页取景，"
                         "换一条图里真实的 <path> 当流线")
    distance = cfg.get("distance")
    if not _finite(distance) or float(distance) < FLOW_MIN_DISTANCE:
        raise ValueError(f"{where}.distance 必须是 ≥ {FLOW_MIN_DISTANCE:g} 的有限数"
                         f"（SVG 用户单位的前进弧长；实际: {distance!r}）")
    direction = cfg.get("direction", FLOW_DEFAULT_DIRECTION)
    if direction not in _DIRECTIONS:
        raise ValueError(f"{where}.direction 必须是 'forward' / 'backward' 之一"
                         f"（实际: {direction!r}）")
    taper = cfg.get("taper", FLOW_DEFAULT_TAPER)
    if not _finite(taper) or not (0.0 <= float(taper) <= FLOW_TAPER_MAX):
        raise ValueError(f"{where}.taper 必须在 [0, {FLOW_TAPER_MAX:.1f}] 之间"
                         f"（沿弧长的幅度衰减；实际: {taper!r}）")
    return {"riders": list(riders), "path": path,
            "distance": float(distance), "direction": direction,
            "taper": float(taper)}


# ── 曲线：唯一从 SVG 读出来的形状 ──────────────────────────────────────

def _polyline(d, where):
    """path d → viewBox 系里的采样点列；只认 M/L/H/V/C/Q/Z。

    复用 `_path_morph.parse_path` 的 tokenizer（隐式重复已被折进同一条命令的
    数值表），本函数只负责把命令折成**绝对坐标点**，不另写一份数字判据。
    """
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "Q": 4, "Z": 0}
    cmds = parse_path(d, where)
    pts = []
    cur = (0.0, 0.0)
    start = None

    def _put(p):
        # path d 里的 1e309 会被 float() 读成 inf，NaN/Infinity 同理。它们一旦进了
        # 采样点表，cum 里就出现 inf：下游 `1e-6 * length` 的退化阈值变成 inf
        # （有限跨度反被误判成「同一弧长」），`ends > length` 的越界闸也被绕过。
        # 这属于「守卫失效且零报错」，必须在几何真源的入口就地拦掉。
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            raise ValueError(f"{where} 的 path 含非有限坐标 {p!r}（d 里写了 1e309 "
                             "这类溢出数或 NaN/Infinity）：弧长表会被 inf 污染，"
                             "越界与退化判据全部失效，且生成期零报错")
        if not pts or abs(pts[-1][0] - p[0]) > 1e-12 or abs(pts[-1][1] - p[1]) > 1e-12:
            pts.append(p)

    for letter, nums in cmds:
        up = letter.upper()
        if up not in arity:
            raise ValueError(f"{where} 的 path 含 {letter!r} 命令：本档只收 "
                             "M/L/H/V/C/Q/Z（椭圆弧 A、平滑曲线 S/T 要把参数转成"
                             "采样点，等于在本模块里复刻第二套路径求值；把这段"
                             "改写成 C/Q 或折线就能画同一条曲线）")
        rel = letter.islower()
        a = arity[up]
        if up == "Z":
            if start is not None and (abs(cur[0] - start[0]) > 1e-12
                                      or abs(cur[1] - start[1]) > 1e-12):
                _put(start)
            cur = start or cur
            continue
        groups = [nums[i:i + a] for i in range(0, len(nums), a)]
        for gi, raw in enumerate(groups):
            # M 的第二组起是隐式 L（SVG 规范），大小写跟原命令走。相对命令的
            # 每个数值都相对**这一组开始时**的 cur（控制点也一样），先固定 base
            # 再整组换算，避免更新 cur 后把后面的数加成第二份偏移。
            kind = "L" if (up == "M" and gi > 0) else up
            bx, by = cur
            if kind in ("M", "L", "C", "Q"):
                g = [raw[k] + (bx if k % 2 == 0 else by) if rel else raw[k]
                     for k in range(len(raw))]
            elif kind == "H":
                g = [bx + raw[0] if rel else raw[0]]
            else:                                   # V
                g = [by + raw[0] if rel else raw[0]]
            if kind == "M":
                cur = (g[0], g[1])
                start = cur
                _put(cur)
            elif kind == "L":
                cur = (g[0], g[1])
                _put(cur)
            elif kind == "H":
                cur = (g[0], by)
                _put(cur)
            elif kind == "V":
                cur = (bx, g[0])
                _put(cur)
            elif kind in ("C", "Q"):
                p0 = cur
                if kind == "C":
                    c1, c2, p3 = (g[0], g[1]), (g[2], g[3]), (g[4], g[5])
                    for k in range(1, FLOW_CURVE_SAMPLES + 1):
                        t = k / FLOW_CURVE_SAMPLES
                        mt = 1.0 - t
                        x = (mt ** 3 * p0[0] + 3 * mt ** 2 * t * c1[0]
                             + 3 * mt * t ** 2 * c2[0] + t ** 3 * p3[0])
                        y = (mt ** 3 * p0[1] + 3 * mt ** 2 * t * c1[1]
                             + 3 * mt * t ** 2 * c2[1] + t ** 3 * p3[1])
                        _put((x, y))
                    cur = p3
                else:
                    c1, p2 = (g[0], g[1]), (g[2], g[3])
                    for k in range(1, FLOW_CURVE_SAMPLES + 1):
                        t = k / FLOW_CURVE_SAMPLES
                        mt = 1.0 - t
                        x = mt ** 2 * p0[0] + 2 * mt * t * c1[0] + t ** 2 * p2[0]
                        y = mt ** 2 * p0[1] + 2 * mt * t * c1[1] + t ** 2 * p2[1]
                        _put((x, y))
                    cur = p2
    if len(pts) < 2:
        raise ValueError(f"{where} 的 path 至少要画出两个不同的点（实际 {len(pts)} 个）："
                         "一条点线没有弧长，也就没有流")
    return pts


def _arc_table(pts, where):
    """采样点 → 累计弧长表（与 pts 等长）。"""
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    if not math.isfinite(cum[-1]):
        raise ValueError(f"{where} 的 path 弧长表溢出成非有限值：坐标数值过大，"
                         "请把曲线画回 viewBox 的正常量级")
    if cum[-1] <= 1e-9:
        raise ValueError(f"{where} 的 path 总弧长为 0：所有采样点重合，没有流")
    return cum


def _seg_at(g, s):
    """弧长 s 落在哪一段、段内比例是多少；返回 (段下标, 比例)。"""
    pts, cum = g["points"], g["cum"]
    s = _clamp(float(s), 0.0, g["length"])
    j = bisect.bisect_right(cum, s) - 1
    j = max(0, min(j, len(pts) - 2))
    seg = cum[j + 1] - cum[j]
    return j, (0.0 if seg <= 1e-12 else (s - cum[j]) / seg)


def point_at(g, s):
    """弧长 s 处的曲线坐标（折线段内线性插值）。"""
    j, t = _seg_at(g, s)
    a, b = g["points"][j], g["points"][j + 1]
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def tangent_at(g, s):
    """弧长 s 处的单位切线向量（取所在折线段的方向）。"""
    j, _ = _seg_at(g, s)
    pts = g["points"]
    for k in range(j, len(pts) - 1):
        dx = pts[k + 1][0] - pts[k][0]
        dy = pts[k + 1][1] - pts[k][1]
        n = math.hypot(dx, dy)
        if n > 1e-12:
            return (dx / n, dy / n)
    return (1.0, 0.0)


def _project(g, c):
    """元素中心投影到曲线上：返回该处的弧长坐标。

    对每一段取「点到线段」的最近点，取距离最小者。段下标按弧长表算，不按
    点序号排——曲线折返时两段可能一样近，但仍各自有自己的弧长。
    """
    pts, cum = g["points"], g["cum"]
    best_d2, best_s = None, 0.0
    for j, (a, b) in enumerate(zip(pts, pts[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        den = dx * dx + dy * dy
        t = 0.0 if den <= 1e-12 else _clamp(
            ((c[0] - a[0]) * dx + (c[1] - a[1]) * dy) / den)
        px, py = a[0] + t * dx, a[1] + t * dy
        d2 = (c[0] - px) ** 2 + (c[1] - py) ** 2
        if best_d2 is None or d2 < best_d2:
            best_d2, best_s = d2, cum[j] + t * math.hypot(dx, dy)
    return best_s


def geometry(svg_text, cfg, where="flow"):
    """按文件量几何：返回曲线弧长表 / 各元素的弧长坐标 / 切线 / 中心。

    这是**唯一**把 SVG 的 `d` 与元素坐标变成数字的地方：契约层、渲染端、
    生成期体检、跨段接续烘焙都不另写一份「大概在哪」。改曲线即改编排，
    判据在 test_flow.py 里从产物抠出来对账。
    """
    c = validate(cfg, where)
    if not isinstance(svg_text, str) or not svg_text.strip():
        raise ValueError(f"{where} 要从内联 SVG 读几何，但这一页没有 SVG 源文本"
                         "（流线档只能写在 .svg 上）")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as e:
        raise ValueError(f"{where} 的 SVG 解析失败：{e}") from e
    if _local(root.tag) != "svg":
        raise ValueError(f"{where} 的 SVG 根标签不是 <svg>")
    parent = {child: p for p in root.iter() for child in p}
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    missing = sorted({s for s in [c["path"]] + c["riders"]
                      if s[1:] not in by_id})
    if missing:
        raise ValueError(f"{where} 里的 " + "、".join(missing) + " 在 SVG 里找不到"
                         "对应 id 的元素——选择器落空不会报错，只会让那一片区域"
                         "静默不动")
    pe = by_id[c["path"][1:]]
    if _local(pe.tag) != "path" or not (pe.get("d") or "").strip():
        raise ValueError(f"{where}.path={c['path']!r} 必须指向一条带 d 的 <path>"
                         "（流线是画出来的曲线，不是矩形/圆或空壳）")
    raw_pts = _polyline(pe.get("d"), f"{where}.path={c['path']!r} 的 d")
    # 曲线自身的祖先 translate/scale 要折进来：path 与 rider 必须落在同一个
    # viewBox 系里，否则投影全错。旋转/斜切读不准就判废，不猜。
    tx, ty, sx, sy = affine_to(root, pe, parent, where)
    pts = [(x * sx + tx, y * sy + ty) for x, y in raw_pts]
    cum = _arc_table(pts, where)
    g = {"points": pts, "cum": cum, "length": cum[-1]}
    centers, arcs = [], []
    for sel in c["riders"]:
        el = by_id[sel[1:]]
        if (el.get("transform") or "").strip():
            raise ValueError(f"{where} 的 {sel} 自带 transform：生成期写 x/y 会"
                             "覆盖元素自己的平移，段首静默跳一下（把平移画进 "
                             "x/y 属性，或包一层 <g> 来承载 transform）")
        ci = center_of(root, el, parent, where, sel)
        centers.append(ci)
        arcs.append(_project(g, ci))
    g["centers"], g["arcs"] = centers, arcs
    if max(arcs) - min(arcs) <= 1e-6 * g["length"]:
        raise ValueError(f"{where} 的所有元素都落在曲线的同一个弧长上（s 跨度 "
                         f"{max(arcs) - min(arcs):.4f}）：这是一整块平移，不是流"
                         "——把元素沿曲线散开，或直接用 to")
    # 「真的会转弯」：各元素所在处的切线必须至少有两处方向不同。
    min_chord = 2.0 * math.sin(math.radians(FLOW_MIN_TURN_DEG) / 2.0)
    tangents = [tangent_at(g, s) for s in arcs]
    spread = max(math.hypot(u[0] - v[0], u[1] - v[1])
                 for u in tangents for v in tangents)
    if spread < min_chord:
        raise ValueError(f"{where} 的曲线在这些元素所在处没有转弯（切线方向差 "
                         f"{math.degrees(2 * math.asin(min(spread, 2.0) / 2.0)):.1f}° "
                         f"< {FLOW_MIN_TURN_DEG:g}°）：所有位移平行，读成整块平移"
                         "而不是流场；让 path 真的弯一次，或改用 to")
    g["tangents"] = tangents
    return g


def _distances(c, g, where):
    """各元素实际前进的弧长（taper 沿弧长衰减）与行进符号。"""
    s_min, s_max = min(g["arcs"]), max(g["arcs"])
    span = s_max - s_min
    q = [(s - s_min) / span for s in g["arcs"]]
    ds = [c["distance"] * math.exp(-c["taper"] * qi) for qi in q]
    sign = 1.0 if c["direction"] == "forward" else -1.0
    return ds, sign, q


def _check_fits(c, g, ds, sign, where):
    """流必须在曲线内走完：越界的那一头说清改哪个旋钮。"""
    ends = [s + sign * d for s, d in zip(g["arcs"], ds)]
    if sign > 0 and max(ends) > g["length"] + 1e-9:
        raise ValueError(f"{where} 的流走出了曲线：最靠后的元素要走到弧长 "
                         f"{max(ends):.1f}，整条 path 只有 {g['length']:.1f}——"
                         "缩短 distance、减小 taper、把 path 画长，或改用 "
                         "direction:'backward'")
    if sign < 0 and min(ends) < -1e-9:
        raise ValueError(f"{where} 的流走出了曲线起点：最靠前的元素要退到弧长 "
                         f"{min(ends):.1f}——缩短 distance、减小 taper，或改用 "
                         "direction:'forward'")
    return ends


def plan(cfg, duration, fps, svg_text, target, where="flow"):
    """把一条 `flow` 拍编译成可采样的计划。生成期与判据共用这一份。"""
    c = validate(cfg, where)
    dur, f = plan_bounds(duration, fps, where, min_duration=MIN_DURATION)
    if c["riders"][0] != target:
        raise ValueError(f"{where}.riders[0] 必须等于 target={target!r}：流头就是"
                         "这一拍的目标，接续烘焙与按文件拦都只认 step 级 target"
                         f"（实际: {c['riders'][0]!r}）")
    g = geometry(svg_text, c, where)
    ds, sign, q = _distances(c, g, where)
    ends = _check_fits(c, g, ds, sign, where)
    return {"cfg": c, "duration": dur, "fps": f, "target": target,
            "geometry": g, "distances": ds, "sign": sign, "q": q,
            "ends": ends, "selectors": list(c["riders"])}


def envelope(u):
    """生成期算死的 .out 包络：起步就有位移、到位导数归零，不收拍级 ease。

    E(0)=0、E(1)=1、E'(1)=0；比 sin² 那种两端都慢的脉冲更适合「被河流带着
    走然后停住」。写进 GSAP 缓动只会静默失效——产物是离散 tl.set，不是补间。
    """
    u = _clamp(float(u))
    return 1.0 - (1.0 - u) ** 3


def travel(p, t, i):
    """第 i 个元素在时刻 t（相对拍起点）沿曲线走过的弧长。"""
    return p["distances"][i] * envelope(_clamp(float(t), 0.0, p["duration"])
                                        / p["duration"])


def state_at(t, p, i):
    """第 i 个元素在时刻 `t` 的补间变量，**纯函数**。

    不读墙钟、不存上一帧：逐帧 `seek()` 下每一帧都由 `t` 独立算出。`Δ` 是
    相对位移（曲线段的平移副本），所以 t=0 精确为 0、段首不跳；t=D 落在一个
    由曲线形状算出来的新位置。
    """
    n = len(p["selectors"])
    if not 0 <= i < n:
        raise ValueError(f"元素下标 {i} 越界（共 {n} 个）")
    g = p["geometry"]
    s0 = g["arcs"][i]
    s = _clamp(s0 + p["sign"] * travel(p, t, i), 0.0, g["length"])
    a, b = point_at(g, s0), point_at(g, s)
    return {"x": _clean(b[0] - a[0]), "y": _clean(b[1] - a[1])}


def frames(p):
    """生成期采样：`[(相对秒, 选择器, 补间变量), …]`，每帧每元素一条 `tl.set`。

    时刻按 1/fps 网格取、精度锁两位；排序键是 (时刻, 元素下标)，不是 id 字面量——
    写入顺序是作者给的 `riders` 序（导演意图），不能被字典序洗掉。
    """
    return sample_frames(p, state_at)


def start(p, i=0):
    return state_at(0.0, p, i)


def landing(p, i=0):
    return state_at(p["duration"], p, i)


def settle_state(cfg, svg_text, target, where="flow 接续烘焙"):
    """跨段接续的收尾态：`[(选择器, 补间变量), …]`。

    **不需要 fps 与 duration**：包络 E(1)=1 恒成立，收尾位只由曲线形状与旋钮
    决定。但必须**读这一页的 SVG**——每个元素的落点 `P(sᵢ ± dᵢ) − P(sᵢ)` 是
    几何量，不是 `drag` / `trail` 那种能凭配置拼出来的常量。不认这一拍，接续页
    第 0 帧整组元素会弹回原位，且生成期零报错。
    """
    c = validate(cfg, where)
    if c["riders"][0] != target:
        raise ValueError(f"{where}: riders[0] 必须等于 target={target!r}")
    g = geometry(svg_text, c, where)
    ds, sign, _q = _distances(c, g, where)
    _check_fits(c, g, ds, sign, where)
    out = []
    for i, sel in enumerate(c["riders"]):
        s0 = g["arcs"][i]
        s = _clamp(s0 + sign * ds[i], 0.0, g["length"])
        a, b = point_at(g, s0), point_at(g, s)
        out.append((sel, {"x": _clean(b[0] - a[0]), "y": _clean(b[1] - a[1])}))
    return out


def diagnose(p):
    """逐条列出没过的红线，给生成期报错用（空列表 = 全过）。

    判据全部从 `state_at` / `frames` / 几何反推，不复述配置——否则就成了
    「配置说了什么」的自证。报错要回答「改哪一个旋钮」，不只是「没过自查」。
    """
    g, dur = p["geometry"], p["duration"]
    n = len(p["selectors"])
    bad = []
    for i in range(n):
        if any(abs(v) > PRODUCT_EPS for v in start(p, i).values()):
            bad.append("段首有元素提前位移（Δ 必须是相对位移）")
        if not any(abs(v) > PRODUCT_EPS for v in landing(p, i).values()):
            bad.append("收尾位移为 0（流没走出去）")
        # 沿弧长的进度严格不降：包络 E 单调，状态却因曲线折返可能回摆，
        # 所以这条只看 travel 本身，不看位移向量的模。
        prev = -1.0
        for k in range(13):
            v = travel(p, dur * k / 12.0, i)
            if v < prev - PRODUCT_EPS:
                bad.append("沿弧长的进度不是单调的")
                break
            prev = v
        if abs(travel(p, dur, i) - p["distances"][i]) > PRODUCT_EPS:
            bad.append("包络在收尾没走到 distance")
    # 方向场：至少两处切线不同（由几何反推，不看配置里的旋钮）
    min_chord = 2.0 * math.sin(math.radians(FLOW_MIN_TURN_DEG) / 2.0)
    spread = max(math.hypot(u[0] - v[0], u[1] - v[1])
                 for u in g["tangents"] for v in g["tangents"])
    if spread < min_chord:
        bad.append("各元素处的切线全平行（这一拍不是流场）")
    if max(g["arcs"]) - min(g["arcs"]) <= 1e-6 * g["length"]:
        bad.append("所有元素落在同一个弧长上")
    ends = [s + p["sign"] * d for s, d in zip(g["arcs"], p["distances"])]
    if max(ends) > g["length"] + PRODUCT_EPS or min(ends) < -PRODUCT_EPS:
        bad.append("流走出了曲线")
    # 第一帧（t=0）恒为 0；"首帧位移"指第一帧→第二帧那一步，取 t=D/n 那一组
    first_step = frames(p)[n:2 * n]
    if not any(abs(v) > PRODUCT_EPS for _t, _s, var in first_step
               for v in var.values()):
        bad.append("起步那一帧没有位移")
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
