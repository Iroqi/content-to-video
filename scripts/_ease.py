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
import sys

# GSAP 3.14.2 core dist 实测可解析的缓动族名（小写）。别名并入：strong=power4、
# quad/cubic/quart/quint = power1..power4 的旧名，power0 = linear。
EASE_FAMILIES = frozenset({
    "none", "linear", "power0", "power1", "power2", "power3", "power4",
    "quad", "cubic", "quart", "quint", "strong", "sine", "expo", "circ",
    "back", "elastic", "bounce", "steps",
})

# ease 串的形状：族名 [方向前的参数] [.方向] [方向后的参数]。两个位置都能带括号，
# 但**语义完全不同**——GSAP 3.14.2 的字符串解析器（钉固 bundle 的 Rb 源码）查找键是
# 第一个 "(" 之前的全部内容，括号之后的字符整体丢弃：实测 `back(1.7).in` 演的是
# back.out(1.7)（方向被静默丢掉），`steps.out(4)` 的键 "steps.out" 不存在、直接回落
# 缺省缓动。所以合法形状只有：
#   fam[.dir]            back / back.in / Bounce.easeOut
#   fam.dir(args)        back.out(1.7) / elastic.inOut(1,0.3)
#   steps(args)[.dir]    steps(4) / steps(4,true).out（steps 没有方向，写了也被丢）
# fam(args).dir 对带方向的族是静默丢方向的坑，split_ease 直接拒（见下）。
_EASE_RE = re.compile(r"^([a-zA-Z0-9]+)(?:\(([^)]*)\))?(?:\.([a-zA-Z]+))?(?:\(([^)]*)\))?$")
# 两套命名各认各的方向后缀（GSAP 注册表如此，拿钉固 bundle 实测）：
#   小写族名 + .in/.out/.inOut   power2.in 认、Power2.in 不认（后者回落缺省缓动）
#   规范类名 + .easeIn/.easeOut/.easeInOut   Bounce.easeOut 认、bounce.easeOut 不认
# 方向按**大小写精确**匹配：.inOut 认、.inout 不认（后者同样回落缺省缓动）。
_DIRS = {"in": "in", "out": "out", "inOut": "inOut"}
_OLD_DIRS = {"easeIn": "in", "easeOut": "out", "easeInOut": "inOut"}
# 小写族名 → GSAP 注册的规范类名（旧类名写法只认这一套精确拼写：Back 认、BACK 不认）
_CANONICAL = {"linear": "Linear", "power0": "Power0", "power1": "Power1",
              "power2": "Power2", "power3": "Power3", "power4": "Power4",
              "quad": "Quad", "cubic": "Cubic", "quart": "Quart", "quint": "Quint",
              "strong": "Strong", "sine": "Sine", "expo": "Expo", "circ": "Circ",
              "back": "Back", "elastic": "Elastic", "bounce": "Bounce",
              "steps": "SteppedEase"}
# 旧名 → 采样器用的族名（GSAP 对这两个都解析，实测）
_FAMILY_ALIAS = {"strong": "power4"}

# 多项式族次数表：power1=平方 … power4=五次；quad/cubic/quart/quint 是同一族
# 的旧名（GSAP 对两套拼法都解析，见 EASE_FAMILIES）。裸 power 不在放行目录里，
# 由 curve() 按 power2 的语义兜。这张表是两条旧分支的合流——它们算的是同一个 t**exp。
_POLY_EXP = {"power1": 2, "power2": 3, "power3": 4, "power4": 5,
             "quad": 2, "cubic": 3, "quart": 4, "quint": 5}

EASE_HELP = ("认得的档：" + "、".join(sorted(EASE_FAMILIES))
             + "；写法 power2.out / back.out(1.7) / elastic.inOut(1,0.3) / steps(4)，"
               "方向 .in/.out/.inOut，裸名按 .out，参数写在方向后面。"
               "rough/slow/CustomEase 这类要 EasePack 或插件，core 里没有，写了会静默退化")


def split_ease(spec):
    """ease 串 → (族名, 方向, 参数列表)；形状不对（或 GSAP 会静默改义的）返回 None。

    方向缺省按 GSAP 的语义补 `.out`（裸 `power2` 实测等于 `power2.out`）。参数原样留着
    字符串，由调用方按族解释——`steps(4)` 要整数、`elastic.out(1,0.3)` 是振幅与周期。

    位置与命名约束照 GSAP 的查找键语义执行（见 _EASE_RE 上方注释）：
      - 参数在方向前又带方向（`back(1.7).in`）→ None：GSAP 丢掉方向按裸名演，
        带方向的族不能收；steps 例外（它没有方向，曲线层同样忽略 mod）。
      - 参数在方向后又是 steps（`steps.out(4)`）→ None：GSAP 的键 "steps.out"
        不存在，直接回落缺省缓动。
      - 两个位置都有参数 → None：GSAP 只认第一个 "(" 之前的键，形状本身就是坑。
      - 裸类名（`Back` / `Power2`）→ None：GSAP 注册表里它是 {easeIn,easeOut,
        easeInOut} 对象不是函数，补间直接抛错（实测 tween throw）。
    """
    if not isinstance(spec, str):
        return None
    m = _EASE_RE.match(spec.strip())
    if not m:
        return None
    family, pre_args, dir_raw, post_args = m.groups()
    fam = _FAMILY_ALIAS.get(family.lower(), family.lower())
    if pre_args is not None and post_args is not None:
        return None
    if dir_raw is None:
        # 裸名只认小写拼写：规范类名（Back/Power2）在 GSAP 里是配置对象不是函数，
        # 别的拼法（BACK/Power3）键不存在——两种都让补间抛错或静默回落，拒。
        # 判据用**原串**的小写比，不过别名表：裸 strong 是合法写法（≡ power4.out）。
        if family != family.lower():
            return None
        mod = "out"                    # 裸名 GSAP 按 .out 认（实测）
    elif dir_raw in _DIRS:
        if family != family.lower():   # 现代后缀只认小写族名（Power2.in 解析不出）
            return None
        mod = _DIRS[dir_raw]
    elif _CANONICAL.get(family.lower()) == family and dir_raw in _OLD_DIRS:
        mod = _OLD_DIRS[dir_raw]       # 旧类名后缀只认规范类名（bounce.easeOut 解析不出）
    else:
        return None
    if pre_args is not None:
        if dir_raw is not None and fam != "steps":
            return None                # fam(args).dir：GSAP 静默丢方向
    elif post_args is not None and fam == "steps":
        return None                    # steps.dir(args)：GSAP 的键不存在
    raw = (post_args if post_args is not None else pre_args or "").strip()
    args = [a.strip() for a in raw.split(",") if a.strip()]
    return fam, mod, args


# 宽松档：只为预览放行 core 之外的档名（`--allow-ease`）。
# 语义是"这条档名认得了，但渲染时它不会按你写的演"——GSAP 对未注册的 ease 不报错，
# 静默退回缺省缓动。所以宽松档只放行**目录里没有的族名**，形状本身是坑的写法
# （参数写在方向前、裸类名、空括号）照旧拒：那些连预览都对不上。
# 未知族名不设别名表，所以它只认小写写法：`ROUGH_EASE` 是 GSAP 里 CustomEase 那种
# 配置对象的写法，不是补间变量能吃的字符串。
_ALLOW_EXTRA = {"on": False}


def allow_extra(on=True):
    """开/关宽松档。只影响 `validate_ease`，采样器与渲染端不读它。"""
    _ALLOW_EXTRA["on"] = bool(on)


def _extra_family(spec):
    """宽松档下这条串的族名（形状合法、但不在钉固目录里）；否则 None。"""
    if not _ALLOW_EXTRA["on"]:
        return None
    parts = split_ease(spec)
    if parts is not None and parts[0] not in EASE_FAMILIES and parts[0] != "none":
        return parts[0]
    return None


def ease_ok(spec):
    """这一档名 GSAP core 认不认（契约层的判据）。宽松档下额外放行未知族名。"""
    parts = split_ease(spec)
    if parts is None or parts[0] not in EASE_FAMILIES:
        return False
    # 空括号（`steps()` / `elastic.out()`）GSAP 实测出 NaN 曲线或悄悄回落缺省缓动
    # （back.out() 逐点等于 power2.out），形状合法但语义是坑，目录层一并拒。
    if "()" in spec:
        return False
    # `none` 是 GSAP 的直通档，带方向就解析不出来（实测 none.in / none.out 全是 NO），
    # 而解析失败不报错、静默换成缺省缓动——所以这里也判它非法。
    if parts[0] == "none":
        return "." not in spec
    # `steps` 没有方向可言：裸 `steps` 在 GSAP 里是 config 对象不是函数，补间直接
    # 抛错（实测 tween throw）；`steps.in` 的键不存在、静默回落缺省缓动——必须给档数。
    # `steps(4).in` 那种方向本族就忽略，与曲线层一致，放行。
    if parts[0] == "steps":
        return bool(parts[2])
    return True


def _elastic_arg_bad(value, what, spec, where):
    """elastic 的参数是否非法（≤0 / NaN / Infinity）→ 人话报错；合法返回 None。

    GSAP 的 `_configElastic` 里振幅与周期都当正数用：振幅进分母
    （`period / (amplitude < 1 ? amplitude : 1)`），JS 里 0 出 Infinity、负数出负周期、
    Infinity 出 Infinity/NaN 坐标，都不是崩——而是成片里一条谁也说不清理的怪曲线，
    且不报任何错。这正是本模块要挡的那一类"无声退化"，所以在契约层就当非法写法拒掉，
    别等采样器除零。

    只有**写了**的参数才判；没写的走缺省（1.0 / .3 或 .45），不在这里挑刺。
    """
    if value != value or value <= 0 or math.isinf(value):
        raise ValueError(
            f"{where} {spec!r}：elastic 的{what}必须是 > 0 的有限数（实际 {value}）。"
            f"振幅进分母，0 会除零、负数会得到负周期、Infinity 出 NaN 坐标——三种都"
            f"不报错，只是成片里那条曲线不再是你写的那个弹性。{EASE_HELP}")
    return None


# 族 → 参数形状表（GSAP 3.14.2 实测，拿钉固 bundle 逐个 parseEase 对的）：
#   elastic  振幅、周期：进分母/asin 的正数（0 除零、负数负周期、Infinity 出 NaN、
#            "abc" 整串回落缺省值——四种都不响，契约层全拒）
#   steps    档数（GSAP 按原值当格数，4.5 就按 4.5 格跳；非整数让预览采样器与成片
#            说的不是同一件事，只收整数）+ 可选第二参（JS truthiness：任何非空值
#            都整体提前一格，实测 steps(4,false) 与 steps(4,true) 同曲线）
#   back     过冲量（有限数；正负 GSAP 都照算，采样器同式）
# 其余族不认参数：写了 GSAP 静默忽略（power2(3) 与 power2 逐点相同，实测）——
# 作者以为改了次数，同一条静默退化，目录层一并拒。
_FAMILY_ARGS = {"elastic": ("num>0", "num>0"),
                "steps": ("steps_n", "flag"),
                "back": ("num",)}
_ELASTIC_WHAT = ("振幅（第一个参数）", "周期（第二个参数）")


def _arg_num(raw, what, spec, where):
    """参数字符串 → float；不是数字就拒。

    GSAP 对解析不出的参数不响：`elastic.out(abc)` 与 `elastic.out(1,0.3)` 逐点相同
    （实测）——配置没解析出来就整串回落缺省值，作者写的和成片演的不是一回事。
    """
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{where} {spec!r}：{what}必须是数字（实际 {raw!r}）。"
                         f"非数字参数 GSAP 不报错，整串回落缺省缓动。{EASE_HELP}")


def _check_family_args(family, args, spec, where):
    """按族问清参数形状：数字/整数/布尔查表，多写少写写错一律拒。"""
    shape = _FAMILY_ARGS.get(family)
    if shape is None:
        if args:
            raise ValueError(
                f"{where} {spec!r}：{family} 族不认参数（实际 {len(args)} 个）——写了"
                f"也被 GSAP 静默忽略，写的和演的不是一回事。{EASE_HELP}")
        return
    if len(args) > len(shape):
        raise ValueError(
            f"{where} {spec!r}：{family} 最多认 {len(shape)} 个参数（实际 "
            f"{len(args)} 个）。{EASE_HELP}")
    for i, (raw, kind) in enumerate(zip(args, shape)):
        if kind == "num>0":
            _elastic_arg_bad(_arg_num(raw, _ELASTIC_WHAT[i], spec, where),
                             _ELASTIC_WHAT[i], spec, where)
        elif kind == "num":
            v = _arg_num(raw, "过冲量（第一个参数）", spec, where)
            # NaN 与 Infinity 都要拒：`float("NaN")` 解析得出来，于是 NaN 一路
            # 过了契约层，`curve()` 整条返回 nan，morph 采样产出字面 d 串
            # `Mnan nan Lnan nan`（实测）——浏览器整条丢弃形变，全程零报错。
            # isinf 只挡一半的那个洞就是这么开的。
            if v != v or math.isinf(v):
                raise ValueError(
                    f"{where} {spec!r}：back 的过冲量必须是有限数（实际 {raw!r}）——"
                    f"NaN / Infinity 在 GSAP 里出 NaN 坐标。{EASE_HELP}")
        elif kind == "steps_n":
            # 非数字 GSAP 直接出 NaN 曲线；小数档数按原值跳（4.5 格），采样器与
            # 预览同式才对得上——契约层只收 ≥1 的整数。
            v = _arg_num(raw, "档数（第一个参数）", spec, where)
            if v != v or math.isinf(v) or v < 1 or v != int(v):
                raise ValueError(
                    f"{where} {spec!r}：steps 的档数必须是 ≥1 的整数（实际 {raw!r}）。"
                    f"非数字出 NaN 曲线、小数按原值当格数——两种都不响。{EASE_HELP}")
        elif kind == "flag":
            if raw.strip().lower() not in ("true", "false", "1", "0"):
                raise ValueError(
                    f"{where} {spec!r}：steps 的第二个参数只能是 true/false/1/0"
                    f"（实际 {raw!r}）——GSAP 按 JS truthiness 认它，任何非空串都等于 "
                    f"true，写别的串成片与预览会对不上。{EASE_HELP}")


# 会**越界**的族：曲线在到达 1 之前先冲过 1（back 的过冲量、elastic 的振幅）
# 或冲过之后回弹（bounce 的尾段上下折返）。位移类动画里这是致命的——落位是
# 一个精确值（按 offsetTop 算出来的 translateY），冲过意味着内容先越出落位
# 再回来，位移越大越明显。实测过冲量：back.out(1.7) 峰值 1.0998、
# elastic.out(1,0.5) 峰值 1.2044。
OVERSHOOT_FAMILIES = frozenset({"back", "elastic", "bounce"})


def reject_overshoot(spec, where):
    """位移类动画不许用过冲族：冲过落位再回来＝内容先甩出去再收回。

    与 `validate_ease` 分开：那一层判的是"GSAP 认不认这个档名"（通用），
    这一层判的是"这条曲线**适合**这条动画"（按用途挑）。同一个 `back.out(1.7)`
    用在标题入场是好手感，用在字幕滚动落位是穿帮。
    """
    parts = split_ease(spec)
    if parts is None or parts[0] not in OVERSHOOT_FAMILIES:
        return spec
    raise ValueError(
        f"{where} {spec!r} 是过冲族（{parts[0]}）：位移类动画的落位是一个精确值，"
        f"过冲会先冲出落位再回来（实测 back.out(1.7) 峰值 1.0998、"
        f"elastic.out(1,0.5) 峰值 1.2044）。换一条不过冲的档，"
        f"如 sine.out / power2.out / expo.out。")


def validate_ease(spec, where):
    """契约层校验：档名不在目录里就 fail-fast，绝不留成"成片里悄悄换成别的缓动"。

    档名之外还要查参数形状：非数字参数 GSAP 静默回落缺省值、"abc" 与 `elastic.out(0)`
    这类直接出 NaN 坐标，而 `ease_ok` 只看族名方向看不出参数好坏——按
    `_FAMILY_ARGS` 的表一次问清（见 `_check_family_args`）。
    """
    if not isinstance(spec, str):
        raise ValueError(f"{where} 必须是字符串（实际: {spec!r}）")
    extra = _extra_family(spec)
    if extra:
        # 宽松档：族名不认，但形状合法 → 放行 + 当场说清楚它会演成什么。
        # 只在这一处出声，因为这里离作者写的那一行最近。
        print(f"[warn] {where} {spec!r}：族名 {extra!r} 不在钉固的 GSAP core 里，"
              f"预览能过，但真正渲染时 GSAP 会**静默退回缺省缓动**——成片里这条"
              f"曲线不是你写的那个。定稿前从认得的档里挑一个（--set 可临时改）。",
              file=sys.stderr)
        return spec
    if not ease_ok(spec):
        if "()" in spec:
            raise ValueError(f"{where} {spec!r}：空括号没有参数——GSAP 实测对空参数的"
                             f"解析结果是 NaN 曲线或悄悄回落缺省缓动（elastic.out() 出 "
                             f"NaN、back.out() 变成 power2.out）。{EASE_HELP}")
        s = spec.strip()
        m = _EASE_RE.match(s)
        if (m and m.group(2) is not None and m.group(3) is not None
                and m.group(1).lower() != "steps"):
            raise ValueError(
                f"{where} {spec!r}：参数写在方向前面，GSAP 解析时会把方向整体丢掉"
                f"（实测 back(1.7).in 演出来是 back.out(1.7)）——参数要写在方向后面，"
                f"如 {m.group(1)}.{m.group(3)}({m.group(2)})。{EASE_HELP}")
        if (m and m.group(3) is None and m.group(1) != m.group(1).lower()
                and m.group(1).lower() in EASE_FAMILIES):
            raise ValueError(
                f"{where} {spec!r}：裸类名写法在 GSAP 里是 {{easeIn,easeOut,easeInOut}} "
                f"配置对象、不是函数，补间直接抛错（实测 tween throw）——旧类名要带后缀写 "
                f"{m.group(1)}.easeOut，或用小写族名 {m.group(1).lower()}。{EASE_HELP}")
        parts = split_ease(spec)
        if parts is None:
            # 形状就不对（裸类名/大小写/位置坑）。族名不在目录里的先说族名——那才是
            # 作者第一件要改的事（RoughEase / CustomEase 都走这条）。
            if m and _FAMILY_ALIAS.get(m.group(1).lower(), m.group(1).lower()) not in EASE_FAMILIES:
                raise ValueError(
                    f"{where} 的族名 {m.group(1).lower()!r} 不在钉固的 GSAP core 里。{EASE_HELP}")
            raise ValueError(f"{where} {spec!r} 不是合法缓动写法。{EASE_HELP}")
        if parts[0] not in EASE_FAMILIES:
            raise ValueError(f"{where} 的族名 {parts[0]!r} 不在钉固的 GSAP core 里。{EASE_HELP}")
        if parts[0] == "none":
            raise ValueError(f"{where} {spec!r}：none 是直通档，GSAP 不认它的方向后缀，写 \"none\" 就好")
        if parts[0] == "steps":
            raise ValueError(f"{where} {spec!r}：steps 必须给档数——裸 steps 在 GSAP 里是 "
                             f"config 对象不是函数，补间直接抛错（实测 tween throw），"
                             f"steps.in 的键不存在、静默回落缺省缓动。写 \"steps(4)\"。{EASE_HELP}")
        raise ValueError(f"{where} {spec!r} 不是合法缓动写法。{EASE_HELP}")
    parts = split_ease(spec)
    _check_family_args(parts[0], parts[2], spec, where)
    return spec


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
    源码实现，实测与钉固 bundle 逐点一致）。唯一例外是 `expo`（≤0.008 的系统偏差）：
    GSAP 的 Expo 不是标准式（两端各混了一项），**故意不修**——修了会把已有项目里每条
    morph 的中间形状整体挪位，那是视觉漂移不是缺陷修复（test_ease 有回归锁）。
    """
    parts = split_ease(name or default or "none")
    if parts is None:
        return lambda t: t
    family, mod, args = parts
    if family in ("none", "linear", "power0"):
        return lambda t: t

    if family == "steps":
        # GSAP SteppedEase（钉固 3.14.2 源码逐行对过）：格数按原值用（4.5 就按
        # 4.5 格跳，不截断），跳变点放在 t=i/(n+1)（实测 steps(4) 在 0.2 就跳）；
        # 第二个参数是 JS truthiness——任何非空值都整体提前一格（实测 steps(4,false)
        # 与 steps(4,true) 同曲线）；裸 steps 缺省 1 格（config 的 void 0 分支）。
        n = _num(args, 0, 1)
        # 与 elastic 那个兜底同一条理由：契约层挡的是常规路径，这里挡的是绕过它
        # 的输入（手改过的 images.json、老产物重放）。档数非有限时 `math.floor`
        # 会抛 "cannot convert float NaN to integer"（实测），而本函数 docstring
        # 承诺的是线性兜底——一条算不出的曲线不该把整页渲染崩在取整上。
        if not (n >= 1) or math.isinf(n):
            return lambda t: t
        if len(args) > 1:
            return lambda t, n=n: 1.0 if t >= 1 else min(math.floor(t * n) + 1, n) / n
        return lambda t, n=n: 1.0 if t >= 1 else min(math.floor(t * (n + 1)), n) / n

    if family == "elastic":
        # GSAP ElasticEase（钉固 3.14.2 的 Xb 源码逐行对过）：
        #   p1  = amplitude >= 1 ? amplitude : 1
        #   per = (period || (inOut ? .45 : .3)) / (amplitude < 1 ? amplitude : 1)
        #   s   = (per / 2π) · asin(1/p1)
        #   out(t) = 1 + p1·2^(-10t)·sin((t-s)·2π/per)
        #   in  = 1 - out(1-t)；inOut 走下面的通用补套（与 GSAP 的 Wb(out) 等价）
        # 缺省周期按方向走：in/out 是 .3，inOut 是 .45（裸 `elastic.inOut` 实测≡(1,0.45)，
        # 不是 (1,0.3)——GSAP 注册 inOut 时 type 为空，走了 .45 分支）；振幅不足 1 时 p1
        # 钳到 1、周期按 1/振幅 放大，等价于 (1, p/a)（实测 (0.6,0.18)≡(1,0.3)）。
        # 旧实现把 sin 的相位写成 (t-1-s)，只在 asin(1/p1)=π/2（振幅≤1）时与 GSAP 相等，
        # 振幅 >1 时整条曲线静默走偏——实测 elastic.out(2,1) 在 t=0.05：旧值 -0.051，
        # GSAP +0.706。按源码式重写，三个方向才对得上。
        a = _num(args, 0, 1.0)
        # 渲染端最后一道兜底：契约层已按「振幅/周期必须 > 0 且有限」拒过非法写法，这里挡的是
        # 绕过契约层的输入（手改过的 images.json、老产物重放）——morph 采样不该因为一条
        # 算不出的曲线把整页渲染崩在除零上。判据与契约层同一条（≤0 / NaN / Infinity
        # 都当没写），回落值就是缺省振幅：GSAP 里 `elastic.out(0)` 走的是 Infinity 周期，
        # 同样是垃圾曲线，回落到 1.0 至少是"认得出的弹性"，且不会让这一页变成 NaN 坐标。
        if a != a or a <= 0 or math.isinf(a):
            a = 1.0
        default_p = 0.45 if mod == "inOut" else 0.3
        p = _num(args, 1, default_p)
        if p != p or p <= 0 or math.isinf(p):   # GSAP 的 `period || 缺省`：0/NaN/负数一律回落
            p = default_p
        p1 = a if a >= 1 else 1.0
        per = p / (a if a < 1 else 1.0)
        s = per * math.asin(1.0 / p1) / (2 * math.pi)

        def _out(t, p1=p1, per=per, s=s):
            if t <= 0:
                return 0.0
            if t >= 1:
                return 1.0
            return 1 + p1 * (2 ** (-10 * t)) * math.sin((t - s) * (2 * math.pi) / per)

        def g(t, out=_out):
            if t <= 0:
                return 0.0
            if t >= 1:
                return 1.0
            return 1 - out(1 - t)
    elif family == "bounce":
        g = lambda t: 0.0 if t <= 0 else (1.0 if t >= 1 else 1 - _bounce_out(1 - t))
    elif family.startswith("power") or family in _POLY_EXP:
        # 次数：目录内的族直接查表；裸 power（不在放行目录，这里是渲染端最后一道
        # 兜底）取第一个参数当次数、缺省按 power2 的语义（exp=3）。
        exp = _POLY_EXP[family] if family in _POLY_EXP else (
            (int(family[5:]) if family[5:].isdigit() else int(_num(args, 0, 2))) + 1)
        g = lambda t, e=exp: t ** e
    elif family == "sine":
        g = lambda t: 1 - math.cos(t * math.pi / 2)
    elif family == "expo":
        g = lambda t: (2 ** (10 * (t - 1))) if t else 0.0
    elif family == "circ":
        g = lambda t: 1 - math.sqrt(max(0.0, 1 - t * t))
    elif family == "back":
        c1 = _num(args, 0, 1.70158)          # 过冲量；back.out(1.7) 与裸 back.out 同档
        # 与 elastic / steps 同一条兜底：过冲量非有限时整条曲线是 nan，morph 采样
        # 会产出 `Mnan nan Lnan nan` 这样的 d 串被浏览器整条丢弃——silent 比崩更
        # 难查，所以在这里就回落到裸 back 的缺省过冲量。
        if c1 != c1 or math.isinf(c1):
            c1 = 1.70158
        c3 = c1 + 1
        g = lambda t, c1=c1, c3=c3: c3 * t ** 3 - c1 * t ** 2
    else:
        return lambda t: t

    if mod == "in":
        return g
    if mod == "out":
        return lambda t, g=g: 1 - g(1 - t)
    return lambda t, g=g: (0.5 * g(2 * t)) if t < 0.5 else (1 - 0.5 * g(2 - 2 * t))
