#!/usr/bin/env python3
"""SVG path 的「同拓扑」线性插值——导演档 morph 的数学核，纯标准库、无第三方依赖。

为什么收在这里而不是交给运行时：本技能的成片是**逐帧 seek**（渲染器把 GSAP 时间线
每一帧 seek 到某个时刻），所以任何运动必须是"时间 t 的纯函数"才可复现。墙钟动画、
运行时回调、外部 morph 库（flubber/MorphSVG）要么不可复现、要么加依赖，都不进交付
链路。这里的做法是：生成期把 morph 按渲染帧率**采样成离散的 d 关键帧**，逐帧 seek
时 GSAP 只是取"最近一个已到的 set"——形状随时间是确定的、可 diff 的、跨平台一致的。

代价写在明面：**只支持同拓扑 path**——起止两条 path 必须命令字母序列一致、每条命令
的数值个数一致，只有坐标不同（这正是"柱子长高 / 折线拉直 / 端点滑动 / 矩形压扁"这类
解说高频形变；任意两个不相干形状的互转需要外部库，不在本模块范围）。

对外三函数：
    check_morphable(from_d, to_d) -> None   # 契约层 fail-fast：不是同拓扑就 raise
    make_morph(from_d, to_d) -> (pf, pt)     # 解析一次，供多次插值
    interp(pf, pt, k) -> str                 # k∈[0,1] 线性插回 d 串

外加一个搭同一套 tokenizer 的几何小工具（morph 之外唯一的 path 语义复用点）：
    y_span(d) -> (min_y, max_y) | None      # path 的竖向占位跨度，给 check_svg 的画布密度自查
"""
import re

# path 命令字母 → 每条命令的数值个数（Z 闭合无参数；A 椭圆弧 7 个）。
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
          "Q": 4, "T": 2, "A": 7, "Z": 0}

# 切 token：命令字母，或一个浮点数（带符号 / 小数 / 科学计数；".5" 与 "1.5.5"
# 这类 SVG 省略写法也能被 \d+\.?\d* | \.\d+ 正确分词）。
_TOKEN = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]"
    r"|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


from _script_utils import NUMERIC_ONLY, num_fmt  # noqa: E402  纯数字判据与数值格式化同源


def parse_path(d, where):
    """SVG path d 串 → 有序的 [(命令字母, [float,...]), ...]；畸形输入 raise ValueError。

    以原样保留命令字母的大小写（相对/绝对混用是合法的，插值时按各自数值处理即可，
    无需先归一为绝对坐标）。隐式重复（"L 1 2 3 4" = 两段 L）自然并进同一条命令的
    数值列表——只要起止两条写成同样的分块，逐坐标 zip 插值就对齐。
    """
    if not isinstance(d, str):
        raise ValueError(f"{where} 必须是字符串 path d（实际: {type(d).__name__}）")
    toks = _TOKEN.findall(d)
    # 丢掉分隔符（逗号/空白）后，token 必须严丝合缝拼回原文——否则夹了非法字符。
    if "".join(toks) != re.sub(r"[,\s]", "", d):
        raise ValueError(f"{where} 含无法解析的字符或断裂的数字：{d!r}")
    if not toks:
        raise ValueError(f"{where} 是空 path：{d!r}")
    if toks[0].upper() not in ("M", "L", "H", "V", "C", "S", "Q", "T", "A", "Z"):
        raise ValueError(f"{where} 必须以 path 命令字母开头（实际首 token: {toks[0]!r}）")
    if toks[0].upper() != "M":
        raise ValueError(f"{where} 需以 M/m 起始（path 规范），实际: {toks[0]!r}")
    out = []
    letter = None
    nums = []

    def _flush():
        if letter is None:
            return
        a = _ARITY[letter.upper()]
        if a and (not nums or len(nums) % a):
            if not nums:
                # 空参数命令："M"、"M0 0 L"、"M0 0 Z Q" 这类退化 path 此前会
                # 因 0 % a == 0 直接放行——过了同拓扑契约、interp 又原样吐回，
                # 成片里 GSAP 拿到的是浏览器不认的 d，动画静默丢失且无报错。
                # SVG 里只有 Z 允许不带参数（闭合并回子路径起点），其余命令
                # 至少要一组数值。
                raise ValueError(
                    f"{where} 的命令 {letter!r} 一个数值都没有——只有 Z 允许"
                    "不带参数（M/L/H/V/C/S/Q/T/A 都至少要一组数值）")
            raise ValueError(
                f"{where} 的命令 {letter!r} 有 {len(nums)} 个数值，不是 {a} 的整数倍")
        out.append((letter, list(nums)))
        nums.clear()

    for tk in toks:
        if tk.upper() in _ARITY and not NUMERIC_ONLY.match(tk):
            _flush()
            letter = tk
        else:
            nums.append(float(tk))
    _flush()
    return out


def check_morphable(from_d, to_d):
    """起止 path 是否同拓扑（命令字母序列 + 每条命令数值个数一致）；不是则 raise。"""
    pf = parse_path(from_d, "morph.from")
    pt = parse_path(to_d, "morph.to")
    if len(pf) != len(pt):
        raise ValueError(
            f"morph 起止命令数不同（{len(pf)} vs {len(pt)}）——只支持同拓扑 path，"
            f"from={from_d!r} 与 to={to_d!r} 结构必须一致")
    for (lf, nf), (lt, nt) in zip(pf, pt):
        if lf != lt or len(nf) != len(nt):
            raise ValueError(
                f"morph 命令不匹配：{lf!r}({len(nf)} 值) vs {lt!r}({len(nt)} 值）"
                "——同拓扑要求命令字母与每条数值个数逐一相同，只有坐标可变")


def make_morph(from_d, to_d):
    """解析并校验一次，返回 (parsed_from, parsed_to) 供多次 interp（避免重复解析）。"""
    pf = parse_path(from_d, "morph.from")
    pt = parse_path(to_d, "morph.to")
    if len(pf) != len(pt) or any(
            lf != lt or len(nf) != len(nt)
            for (lf, nf), (lt, nt) in zip(pf, pt)):
        check_morphable(from_d, to_d)  # 触发与契约层同文案的报错
    return pf, pt


def interp(pf, pt, k):
    """按 k∈[0,1] 线性插回一条 d 串。pf/pt 由 make_morph 得到（已保证同拓扑）。"""
    parts = []
    for (letter, nf), (_, nt) in zip(pf, pt):
        vals = [a + (b - a) * k for a, b in zip(nf, nt)]
        parts.append(letter + " ".join(num_fmt(v) for v in vals) if vals else letter)
    return " ".join(parts)


# 命令参数组里"哪些下标是 y"（H 不带新 y；V 的唯一数值就是 y；A 只取终点，
# 它的控制点无法由这 7 个参数线性读出）。控制点（C/S/Q 的中间点）一并计入：
# 这是"占位跨度"不是"精确 bbox"，宁可高估——高估只会少打一条 warn，低估会误报。
_Y_INDEX = {"M": (1,), "L": (1,), "H": (), "V": (0,), "C": (1, 3, 5),
            "S": (1, 3), "Q": (1, 3), "T": (1,), "A": (6,), "Z": ()}
# 同一条 path 投到 x 轴（V 不带新 x，H 的唯一数值就是 x）。
_X_INDEX = {"M": (0,), "L": (0,), "H": (0,), "V": (), "C": (0, 2, 4),
            "S": (0, 2), "Q": (0, 2), "T": (0,), "A": (5,), "Z": ()}


def _axis_span(d, index):
    """path d 投到某一轴：按命令语义走光标，相对命令累加当前点。"""
    cx = cy = sx = sy = 0.0
    pts = []
    take_y = index is _Y_INDEX
    for letter, nums in parse_path(d, "path d"):
        up = letter.upper()
        rel = letter.islower()
        arity = _ARITY[up]
        for i in range(0, len(nums), arity or 1):
            g = nums[i:i + arity]
            for j in index[up]:
                pts.append((cy if take_y else cx) + g[j] if rel else g[j])
            if up == "H":
                cx = cx + g[0] if rel else g[0]
            elif up == "V":
                cy = cy + g[0] if rel else g[0]
            else:
                dx, dy = g[-2], g[-1]
                if rel:
                    cx += dx
                    cy += dy
                else:
                    cx, cy = dx, dy
            if up == "M" and i == 0:
                sx, sy = cx, cy         # 子路径起点只认首个坐标对；后续隐式 L 对不重置
        if up == "Z":
            cx, cy = sx, sy          # 闭合把光标送回本子路径起点
    return (min(pts), max(pts)) if pts else None


def y_span(d):
    """path d → (最小 y, 最大 y)：按命令语义走光标，相对命令累加当前点。

    给 `check_svg.py` 的整页画布密度自查用（把图元投到 y 轴上找空带，不需要精确
    bbox）。纯 Z / 空 path 这类读不出 y 的返回 None，由调用方按"未知"处理。
    """
    return _axis_span(d, _Y_INDEX)


def x_span(d):
    """path d → (最小 x, 最大 x)：与 y_span 共用同一套光标走法。

    给 `scripts/_cam_crop.py` 用：运镜裁切只看 y 轴看不见"整条被推到画面右边之外"，
    两轴都得投影。
    """
    return _axis_span(d, _X_INDEX)


def _walk(d):
    """path d → (点序列, 线段序列, 是否含 Z)。单一光标走法，端点与线段共用。

    点序列 = 每个命令终点（含 M 的落点）；线段序列 = 逐命令的直线段近似
    （曲线取起点→终点，闭合补回段）。给 check_svg 的端点落点检查用：端点
    语义（相对命令累加、H/V 只动单轴、Z 送回子路径起点）不在这里写两份。

    末位是**最后一条子路径**的起点：多子路径 path 只有拿着它才能正确地给自己
    补闭合边——`M0 0 H100 V100 H0 Z M90 90 h5 v5 h-5 Z` 的两个子路径各自都已闭合，
    按"第一条子路径的起点"补线会凭空多一条 (90,90)→(0,0) 的斜边。
    """
    cmds = parse_path(d, "path d")
    pts = []
    segs = []
    cx = cy = sx = sy = 0.0
    prev = None
    closed = False
    for letter, nums in cmds:
        up = letter.upper()
        rel = letter.islower()
        if up == "Z":
            closed = True
            if prev is not None and (prev[0] != sx or prev[1] != sy):
                segs.append((prev[0], prev[1], sx, sy))
            cx, cy = sx, sy
            prev = (sx, sy)
            pts.append((cx, cy))
            continue
        arity = _ARITY[up]
        for i in range(0, len(nums), arity or 1):
            g = nums[i:i + arity]
            if up == "H":
                nx, ny = (cx + g[0], cy) if rel else (g[0], cy)
            elif up == "V":
                nx, ny = (cx, cy + g[0]) if rel else (cx, g[0])
            elif up == "A":
                nx = cx + g[5] if rel else g[5]
                ny = cy + g[6] if rel else g[6]
            else:                       # M / L / C / S / Q / T：末两位是终点
                nx = cx + g[-2] if rel else g[-2]
                ny = cy + g[-1] if rel else g[-1]
            if up == "M" and i == 0:
                prev = None             # 首个坐标对是移动不画线；后续坐标对是隐式 L（SVG 规范），照画
            if prev is not None:
                segs.append((prev[0], prev[1], nx, ny))
            prev = (nx, ny)
            cx, cy = nx, ny
            pts.append((cx, cy))
            if up == "M" and i == 0:
                sx, sy = cx, cy         # 子路径起点只认首个坐标对；后续隐式 L 对不重置
    return pts, segs, closed, (sx, sy)


def path_endpoints(d):
    """开 path 的首尾绝对坐标；含 Z（闭合）返回 None。

    给 `scripts/check_svg.py` 的端点落点检查用。Z 出现即视为闭合——闭合轮廓
    的端点根在自己的轮廓上，不需要查悬空。
    """
    if not isinstance(d, str):
        raise ValueError("path d 必须是字符串")
    if re.search(r"[zZ]", d):
        return None
    pts, _, _, _ = _walk(d)
    return (pts[0], pts[-1]) if pts else None


def path_last_subpath_start(d):
    """最后一条子路径的起点绝对坐标（多子路径 path 补隐式闭合边时用）。

    `path_segments` 丢掉了子路径归属：只看它得到的线段列表，分不出「最后一条
    子路径自己闭没闭合」——而浏览器的隐式闭合补的是**末点→它自己那条子路径的
    起点**，不是回到第一条子路径的起点。这条补齐信息的 API 只有这一份，别在
    调用侧用正则去 d 里找最后一个 M（`d` 里命令大小写的歧义 parse_path 已经
    判干净了，在这儿再判一次就是第二份规则）。
    """
    if not isinstance(d, str):
        raise ValueError("path d 必须是字符串")
    return _walk(d)[3]


def path_segments(d):
    """path → 直线段序列 [(x1,y1,x2,y2), ...]（曲线按起点→终点近似，闭合补回段）。

    给 `scripts/check_svg.py` 端点落点检查当"落点目标"：另一条线/轮廓的边可以
    根住端点。近似只影响"少根住"（安全方向），不会把悬空误判成根住。
    """
    if not isinstance(d, str):
        raise ValueError("path d 必须是字符串")
    _, segs, _, _ = _walk(d)
    return segs
