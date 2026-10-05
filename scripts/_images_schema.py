#!/usr/bin/env python3
"""images.json 与媒体路径的契约。

配图由 agent/人工产出、渲染端按这份映射消费；坏路径会让 HTML 静默产出
空白裂图，所以引用完整性在 load_images_json / validate_images_json
fail-fast，路径语义（禁绝对路径/越出项目根）由 validate_relative_project_path
统一收口。媒体类型判定（扩展名 → image/video/gif）也在这里，与
Remotion 渲染端的媒体分支共用。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import read_json_file  # noqa: E402
from _segments import SID_RULE, is_valid_sid  # noqa: E402
from _path_morph import check_morphable  # noqa: E402
from _ease import validate_ease  # noqa: E402


def validate_relative_project_path(src, where):
    """Reject absolute/traversal paths and normalize separator semantics."""
    if not isinstance(src, str) or not src.strip():
        raise ValueError(f"{where} 必须是非空字符串")
    norm = src.replace("\\", "/")
    if "\x00" in norm:
        raise ValueError(f"{where} 不得包含 NUL 字节")
    # 注意：Windows 的 ntpath.isabs("/tmp/a.png") 返回 False——盘符缺省的
    # POSIX 绝对路径会被误判成相对路径，下游 abspath 再解析到 <当前盘>:\tmp。
    # 所以任何前导 "/"（含 UNC 的 "//"）都必须显式拦掉，不能只靠 isabs。
    if (os.path.isabs(norm) or norm.startswith("/")
            or re.match(r"^[A-Za-z]:/", norm)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", norm)):
        raise ValueError(f"{where} 禁止使用绝对路径：{src!r}")
    depth = 0
    for part in (p for p in norm.split("/") if p not in ("", ".")):
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ValueError(f"{where} 越过项目根目录：{src!r}")
        else:
            depth += 1
    return norm

# images.json 条目里渲染端**真正会读**的键。
# src/type/poster/loop/muted/autoplay/playsinline → Remotion 渲染端的媒体分支；
# 其余五个是 provenance 记账字段，画面不读、但按 SKILL.md 要求留存。
# 之外的键（`position`/`fit`/`alt` 这类凭空发明的写法）过去会被静默丢掉：
# "我明明写了 alt，画面上什么都没有"变成无解的困惑。判定收口在这一个函数，
# warn 只在丢键的那一处（渲染端
# ._normalize_images）打；生成器/管线的预检走 validate_images_json。
MEDIA_ENTRY_KEYS = frozenset({
    "src", "type", "poster", "loop", "muted", "autoplay", "playsinline",
    "source_url", "license", "attribution", "query", "provider",
    # director：方式 C SVG 的"时间轴同步动画"编排（见 references/image_options.md
    # 方式 C「SVG 动画：两档」）。写了它这张 SVG 才走净化内联，否则仍是 <img>。
    "director",
    # stage："keep" = 跨段场景延续（persistent stage，见 _stage_carry）。写在**接续页**
    # 上：本页画面从上一页演完的样子起跑。只有这一个取值，所以是枚举而不是布尔——
    # 将来要"只搬相机不搬元素态"这类档位时不必把 keep:true 的语义改掉。
    "stage",
    # inline_svg：**内部键**，由 _director_prepare 在读盘净化后回填，渲染端消费。
    # 它进 MEDIA_ENTRY_KEYS 只是为了不被 unknown_media_keys 误报；用户手写在
    # images.json 里会被 validate_images_json 直接拒（见下），否则等于开了一个
    # "绕过净化、把任意字符串塞进成片 DOM"的注入口。
    "inline_svg",
})

# 扩展名 → 媒体类型。gif 与 image 同档（都走 <img>，语义上 gif 就是一张会自己
# 动的静态图），单列出来只为让 images.json 能显式写 type:"gif" 而不被拒。
MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def is_svg_path(path):
    """这条路径是不是 SVG（"能不能被净化内联、有没有图内命名元素"那一问）。

    生成端有三处各自问过它（缺图探测跳过、画布段必须 SVG、渲染端加 bare-media
    类），契约层还有两处（director / stage 只挂在 SVG 上）。五处各写一遍
    endswith/splitext 就是五份真源：将来认一个 .svgz、或把大小写规则改一下，
    只改到的一处会和其他四处分家，症状是"契约说能挂 director、渲染端却不内联"。
    """
    return os.path.splitext(str(path).lower())[1] == ".svg"


def classify_media_path(path, explicit_type="auto"):
    """根据文件扩展名和显式 type 判断媒体类型。

    Returns:
        "video" | "image" | "gif"
    """
    if explicit_type != "auto":
        return explicit_type
    ext = os.path.splitext(path.lower())[1]
    if ext in MEDIA_VIDEO_EXTS:
        return "video"
    if ext == ".gif":
        return "gif"
    return "image"

# director 的 target 只认单个 id / class 选择器，且字符集受限——它会被拼进 GSAP
# 选择器字符串（`#img-{sid} {target}`），带引号/空格/逗号/伪类的写法轻则选择器失配、
# 动画静默丢失，重则破坏脚本。与段 id 同一条收口思路（见 _segments._SID_RE）。
# class 选择器（`.marker`）专为 stagger 开：一条 step 命中一组同类元素、按递增
# delay 逐个演，省掉"三条标记写三个 step"。仍只允许 `#`/`.` 前缀 + 单个标识符，
# 逗号/后代/伪类一律不进（那会把"一条 step 打一个简单选择器"的约束拆掉）。
_DIRECTOR_TARGET_RE = re.compile(r"^([#.])[A-Za-z_][A-Za-z0-9_-]*$")
_DIRECTOR_STEP_KEYS = frozenset({"at", "at_time", "target", "from", "to", "set",
                                 "draw", "morph", "count", "type", "stagger",
                                 "duration", "ease", "delay", "repeat", "yoyo"})
# 写在 from/to/set **里面**的旋钮：一律拒，指回 step 级。理由是这些键渲染端会自己覆盖或
# 根本不读——`ease` 会被 step 级顶掉（写了等于没写），`duration`/`delay` 却会被 GSAP 真的
# 吃掉，于是补间实际跨度与门禁拿去判窗的那个数分家（`beat_positions` 只读 step 级）。
# 一处两个真源正是最坏形状：门禁的判词就成了谎话。
_PAYLOAD_CONTROL_KEYS = frozenset({"duration", "ease", "delay", "stagger",
                                   "repeat", "yoyo"})
# at_time 的相对写法：以 "+0.5" / "-0.2" 出现，含义是"上一条 beat 结束之后再过
# 这么多秒"（首条则从段落音频起点算）。让整页画布的自由时间线能顺次链接节奏，
# 不必每步手算绝对秒——重配音后绝对秒会整体漂移，相对链则跟着上一条走。
_RELATIVE_AT_TIME_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")


def _validate_tween_vars(vars_, where):
    """GSAP 补间变量：只允许 JSON 可序列化的值，禁 on* 回调键与控制旋钮。

    这些字典会被 json.dumps 成 JS 对象字面量注入内联脚本——JSON 转义挡住了
    字符串注入，但回调键（onStart/onUpdate/…）本身不是注入而是"在渲染页里
    执行任意 JS 逻辑"的入口，且信源不可信（SKILL.md 核心规则），一律不收。
    允许嵌套 dict（GSAP 的 attr:{} 等非 CSS 属性走它），逐层校验叶子是标量。

    控制旋钮（_PAYLOAD_CONTROL_KEYS）判在这里而不是调用点判一次：调用点只认顶层，
    `{"to": {"attr": {"duration": 2}}}` 就漏过去，把 duration="2" 写进 SVG 元素——
    递归的这一层才是"每一层都不许有"的正确位置。
    """
    if not isinstance(vars_, dict):
        raise ValueError(f"{where} 必须是对象（GSAP 补间变量）")
    clash = sorted(_PAYLOAD_CONTROL_KEYS & set(vars_))
    if clash:
        raise ValueError(
            f"{where} 里写了 {clash}——这些旋钮只在 step 级有定义，"
            "放在补间变量里要么被渲染端覆盖、要么让补间的真实跨度与门禁算的"
            "那个数分家。把它们提到这一 step 上")
    for k, v in vars_.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError(f"{where} 的键必须是非空字符串（实际: {k!r}）")
        if k.lower().startswith("on"):
            raise ValueError(
                f"{where} 含回调键 {k!r}——director 只接受可序列化的补间属性，"
                "不接受 onStart/onUpdate/onComplete 这类回调（信源不可信，"
                "回调等于在渲染页执行任意逻辑）")
        _validate_tween_leaf(v, f"{where} 的 {k!r}")


def _validate_tween_leaf(v, where):
    if v is None or isinstance(v, (bool, int, float, str)):
        return
    if isinstance(v, dict):
        _validate_tween_vars(v, where)  # 递归：键同样禁 on*
        return
    if isinstance(v, (list, tuple)):
        for item in v:
            _validate_tween_leaf(item, where)
        return
    raise ValueError(f"{where} 的值必须是 JSON 标量/数组/对象（实际: {type(v).__name__}）")


def _validate_morph(morph, where):
    """director 步骤的 morph：一条同拓扑 path 的形变（from→to 只换坐标）。

    同拓扑（命令字母序列 + 每条数值个数一致）在这里就 fail-fast：不同拓扑到生成期
    只能报错、且人早已把 JSON 写完，报错点越靠契约层越好。真·形变核见 _path_morph。
    """
    if not isinstance(morph, dict):
        raise ValueError(f"{where} 必须是对象 {{\"from\": path_d, \"to\": path_d}}（实际: {type(morph).__name__}）")
    unknown = set(morph) - {"from", "to"}
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 'from' / 'to'")
    for nm in ("from", "to"):
        v = morph.get(nm)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{where} 的 {nm} 必须是非空字符串 path d（实际: {v!r}）")
    check_morphable(morph["from"], morph["to"])
    return morph


def _validate_count(count, where):
    """director 步骤的 count：一个 <text> 的数字滚动（from→to 随时间缓动）。

    渲染端把它展开成"代理对象补间 + onUpdate 写 textContent"——onUpdate 是这里
    生成的、只读我们校验过的标量，不是信源塞进来的回调（那类仍被 _validate_tween_vars
    一律拒）。字段全可选除 to：decimals 控制小数位，prefix/suffix 裹住数字（如
    "≈" 前缀、"×10²³" 后缀）。
    """
    if not isinstance(count, dict):
        raise ValueError(f"{where} 必须是对象 {{\"to\": 数字, ...}}（实际: {type(count).__name__}）")
    unknown = set(count) - {"to", "from", "decimals", "prefix", "suffix"}
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 to / from / decimals / prefix / suffix")
    for nm in ("to", "from"):
        if nm in count:
            v = count[nm]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{where} 的 {nm} 必须是数字（实际: {v!r}）")
    if "to" not in count:
        raise ValueError(f"{where} 必须有 to（滚动到的目标值）")
    dec = count.get("decimals", 0)
    if isinstance(dec, bool) or not isinstance(dec, int) or not 0 <= dec <= 10:
        raise ValueError(f"{where} 的 decimals 必须是 0–10 的整数（实际: {dec!r}）")
    for nm in ("prefix", "suffix"):
        v = count.get(nm)
        if v is not None and not isinstance(v, str):
            raise ValueError(f"{where} 的 {nm} 必须是字符串（实际: {v!r}）")
    return count


def _validate_type(typ, where):
    """director 步骤的 type：打字机逐字揭示——把 <text> 里现成的整段文本按进度
    逐字写回。参数刻意只留空对象 `{}`：写多少字取决于 SVG 里那条 <text> 的文本长度
    （渲染端运行时读 textContent），节奏由 step 级 duration/ease 控制，没有别的旋钮
    要校。想改揭示哪段文字，直接改 SVG 的 <text> 内容即可，不必在这里配。
    """
    if not isinstance(typ, dict):
        raise ValueError(f"{where} 必须是空对象 {{}}（节奏用 step 级 duration/ease 调；"
                         f"实际: {type(typ).__name__}）")
    if typ:
        raise ValueError(f"{where} 只认空对象 {{}}，未知键 {sorted(typ)}——"
                         "字数取自 <text> 本身，快慢用 duration/ease")
    return typ


def _validate_stagger(stagger, where):
    """director 步骤的 stagger：一条 step 命中一组元素时，逐个之间的时间间隔。

    收两种写法：数字（= 每个间隔秒数，GSAP 的 stagger:0.1），或对象（GSAP 原生
    stagger 配置：each / amount / from / grid / ease）。值仍走标量/数组校验、禁 on*。
    """
    if isinstance(stagger, bool) or not isinstance(stagger, (int, float, dict)):
        raise ValueError(f"{where} 必须是数字（每个间隔秒）或对象（GSAP stagger 配置）"
                         f"（实际: {stagger!r}）")
    if isinstance(stagger, (int, float)):
        if stagger < 0:
            raise ValueError(f"{where} 为数字时必须 ≥0（实际: {stagger!r}）")
        return stagger
    unknown = set(stagger) - {"each", "amount", "from", "grid", "ease"}
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}"
                         "——只认 each / amount / from / grid / ease")
    for k, v in stagger.items():
        if k == "grid":
            if (not isinstance(v, (list, tuple)) or len(v) != 2
                    or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in v)):
                raise ValueError(f"{where} 的 grid 必须是两个 ≥1 的整数 [列, 行]（实际: {v!r}）")
        elif k == "from":
            if not (isinstance(v, str) or (isinstance(v, (int, float)) and not isinstance(v, bool))):
                raise ValueError(f"{where} 的 from 必须是字符串或数字（实际: {v!r}）")
        elif k == "ease":
            validate_ease(v, f"{where} 的 ease")
        else:  # each / amount
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"{where} 的 {k} 必须是 ≥0 的数字（实际: {v!r}）")
    return stagger


def _validate_director(key, dirval):
    """校验 images.json 条目的 director 编排块（时间轴同步的 SVG 动画）。"""
    if not isinstance(dirval, dict):
        raise ValueError(f"images.json 的 '{key}' 的 director 必须是对象")
    unknown = set(dirval) - {"steps"}
    if unknown:
        raise ValueError(
            f"images.json 的 '{key}' 的 director 含未知键 {sorted(unknown)}——只认 'steps'")
    steps = dirval.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(
            f"images.json 的 '{key}' 的 director.steps 必须是非空数组"
            "（每一步 = 在某句旁白时对某个 SVG 元素补间）")
    for i, step in enumerate(steps):
        where = f"images.json 的 '{key}' 的 director.steps[{i}]"
        if not isinstance(step, dict):
            raise ValueError(f"{where} 必须是对象")
        unknown = set(step) - _DIRECTOR_STEP_KEYS
        if unknown:
            raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                             f"{sorted(_DIRECTOR_STEP_KEYS)}")
        # 锚点二选一：at（句序）或 at_time（段落绝对秒）。两者都写或都不写都是歧义，
        # 一律拒。at_time 让导演档脱离"句子分桶"：整页画布的自由编排可以直接按秒排
        # 补间，不必迁就旁白句边界（见 references/image_options.md「SVG 动画：两档」）。
        has_at, has_time = "at" in step, "at_time" in step
        if has_at == has_time:
            raise ValueError(
                f"{where} 的锚点必须是 at / at_time 二选一："
                "at=该段第几句旁白（0 基，小数=句内偏移，如 1.5=第 2 句念到一半），"
                "at_time=从该段音频起点的绝对秒数（旁白无关的自由时间线）"
                f"（实际: 写了 {sorted(k for k in ('at', 'at_time') if k in step) or '两者皆无'}）")
        if has_at:
            at = step["at"]
            if not isinstance(at, (int, float)) or isinstance(at, bool) or at < 0:
                raise ValueError(
                    f"{where} 的 at 必须是非负数（该段内第几句旁白，0 基；小数=句内偏移，"
                    f"如 1.5=第 2 句念到一半）（实际: {at!r}）")
        else:
            at_time = step["at_time"]
            # 绝对秒（数字，≥0）或相对链（字符串 "+0.5"/"-0.2" = 上一条 beat 结束后
            # 再这么多秒，首条从段落起点算）。相对写法让画布自由时间线顺次排节奏，
            # 重配音后不必逐条改绝对秒。
            if isinstance(at_time, str):
                if not _RELATIVE_AT_TIME_RE.match(at_time):
                    raise ValueError(
                        f"{where} 的 at_time 字符串必须是相对写法 \"+秒\" / \"-秒\""
                        f"（上一条 beat 结束后的偏移；首条从段落起点算）（实际: {at_time!r}）")
            elif (not isinstance(at_time, (int, float)) or isinstance(at_time, bool)
                    or at_time < 0):
                raise ValueError(
                    f"{where} 的 at_time 必须是非负数字（从该段音频起点算的秒数）"
                    f"或相对串 \"+秒\"（实际: {at_time!r}）")
        target = step.get("target")
        if not isinstance(target, str) or not _DIRECTOR_TARGET_RE.match(target):
            raise ValueError(
                f"{where} 的 target 必须是单个 id 或 class 选择器"
                "（# 或 . 开头，后接字母/数字/-/_，字母或下划线起头）"
                f"——它会被拼进 GSAP 选择器（实际: {target!r}）")
        draw = step.get("draw", False)
        if not isinstance(draw, bool):
            raise ValueError(f"{where} 的 draw 必须是 JSON 布尔 true/false（实际: {draw!r}）")
        kinds = [k for k in ("from", "to", "set") if k in step]
        morph = step.get("morph")
        count = step.get("count")
        typ = step.get("type")
        if typ is not None:
            # type 也是独立"演"：整段负责把一个 <text> 的现成文本逐字打出来，不能再叠
            # 任何别的驱动语义（补间变量/描边/形变/数字滚动）——两套同时写同一元素会打架。
            if kinds or draw or morph is not None or count is not None:
                raise ValueError(
                    f"{where} 写了 type，就不能再写 from / to / set / draw / morph / count"
                    "——type 自己负责整段逐字揭示")
            _validate_type(typ, f"{where} 的 type")
        elif count is not None:
            # count 也是一种独立"演"：整段负责一个 <text> 的数字滚动，不能再叠
            # from/to/set/draw/morph——那会同时驱动两套语义。
            if kinds or draw or morph is not None:
                raise ValueError(
                    f"{where} 写了 count，就不能再写 from / to / set / draw / morph"
                    "——count 自己负责整段数字滚动")
            _validate_count(count, f"{where} 的 count")
        elif morph is not None:
            # morph 是一种独立的"演"：整段负责一条同拓扑 path 的形变，不能再叠
            # from/to/set（补间变量）或 draw（描边生长）——那会同时驱动两套语义。
            if kinds or draw:
                raise ValueError(
                    f"{where} 写了 morph，就不能再写 from / to / set / draw"
                    "——morph 自己负责整段形变")
            _validate_morph(morph, f"{where} 的 morph")
        else:
            if not kinds and not draw:
                raise ValueError(
                    f"{where} 至少要有一个 from / to / set，或写 draw:true 做自绘、"
                    "morph 做 path 形变、count 做数字滚动、type 做逐字揭示"
                    "（补间起止/瞬时赋值/描边生长/形状互变/数值递增/打字机）")
            for k in kinds:
                # 控制旋钮（duration/ease/…）的拦截在 _validate_tween_vars 里，跟着
                # 它一起递归——这里只查顶层的话，attr:{} 那类嵌套层就是漏网。
                _validate_tween_vars(step[k], f"{where} 的 {k}")
        # stagger 只对"命中一组元素、逐帧补间"的 step 有意义（from/to/fromTo/set）。
        # morph/count/type 各自负责整段、draw 是单条描边，渲染端会静默忽略 stagger——
        # 与 repeat/yoyo 挂在纯 set 上同一类"写了等于没写"，契约层直接拒，别留成
        # 作者以为在错峰、成片里全齐步的静默失效。
        if "stagger" in step:
            _validate_stagger(step["stagger"], f"{where} 的 stagger")
            if not kinds:
                raise ValueError(
                    f"{where} 写了 stagger 却没有 from / to / set 补间——stagger 只对"
                    "命中一组元素的逐帧补间有意义，morph / count / type / draw 各自负责"
                    "整段，渲染端会忽略它。要么去掉 stagger，要么改写成 from/to/set 补间")
        for k in ("duration", "delay"):
            if k in step:
                v = step[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
                    raise ValueError(f"{where} 的 {k} 必须是 ≥0 的数字（实际: {v!r}）")
        if "ease" in step:
            validate_ease(step["ease"], f"{where} 的 ease")
        # repeat / yoyo：这一拍演完再演几遍、以及来回（GSAP 的同名补间变量）。整数而非
        # 小数是故意的：GSAP 吃 0.5 这种"半遍"，而跨段接续与出画自查都要按"最后停在
        # 哪一头"说话，半遍收尾在两者里都是猜。
        if "repeat" in step:
            v = step["repeat"]
            if isinstance(v, bool) or not isinstance(v, int) or v < -1:
                raise ValueError(f"{where} 的 repeat 必须是 ≥-1 的整数（-1=无限循环，"
                                 f"0=只演一遍；实际: {v!r}）")
        if "yoyo" in step and not isinstance(step["yoyo"], bool):
            raise ValueError(f"{where} 的 yoyo 必须是 JSON 布尔 true/false"
                             f"（实际: {step['yoyo']!r}）")
        # 反复必须挂在真会"演"的步上。纯 set 是瞬时赋值：门禁按"瞬时"推进相对链的游标，
        # 而 GSAP 会把带 repeat 的 set 当 0 秒补间重放，于是报出来的落点是假时刻；yoyo
        # 在单遍上更是直接被忽略。静默失效的那一类宁可在契约层就说不通。
        replayable = bool(draw or morph is not None or count is not None or typ is not None
                          or "from" in step or "to" in step)
        if (step.get("repeat") or step.get("yoyo")) and not replayable:
            raise ValueError(
                f"{where} 写了 repeat / yoyo 却没有可重放的补间（from / to / draw / morph /"
                " count / type 一个都没有）——纯 set 是瞬时赋值，重放它在成片里看不出区别，"
                "只会让门禁算的落点和渲染端用的落点分家")
        if step.get("yoyo") and not step.get("repeat"):
            raise ValueError(
                f"{where} 写了 yoyo 但 repeat 缺省或 0——只演一遍时 GSAP 根本不会回头。"
                "要来回就写 repeat:1（奇数遍收尾在 to，偶数遍收尾回起点）")
        # morph 的形变是生成期采样成离散关键帧的：无限循环没有"最后一帧"可采，采样器只能
        # 猜。补间/count/type 走运行期，GSAP 按时间解析求值，无限循环反而是确定的。
        if morph is not None and step.get("repeat", 0) < 0:
            raise ValueError(
                f"{where} 的 morph 不能配 repeat:-1——形变是生成期按帧率采样成离散关键帧的，"
                "无限循环没有终点可采。要循环就写明确遍数（repeat:N）")
    return dirval



def unknown_media_keys(entry):
    """该 images.json 条目里渲染端不会读的键（升序列表）。"""
    return sorted(set(entry) - MEDIA_ENTRY_KEYS)


def validate_images_json(data):
    """images.json：{segment_id: 媒体对象}。

    两种写法：
        {"seg1": {"src": "images/seg1.png"},
         "seg2": {"src": "images/seg2.svg", "director": {...}},
         "seg3": {"src": "images/seg3.mp4", "type": "video", "loop": true,
                  "muted": true, "autoplay": true, "poster": "...png"}}
      "type" 可选（auto=按扩展名判断；image/video/gif 显式指定），其余字段均为
      可选（静态图只需 src）。不接受裸字符串路径。gif 也走 <img>，与 image 同档。
    """
    if not isinstance(data, dict):
        raise ValueError("images.json 顶层必须是对象 {segment_id: 媒体对象}")
    for key, value in data.items():
        # key 与 manifest 段 id 同一口径（is_valid_sid 包住 _SID_RE）：这些键会拼进 HTML 的
        # id/选择器链路，"seg 3"、"seg.png" 之类在渲染端静默失联。
        if not is_valid_sid(key):
            raise ValueError(
                f"images.json 的段落 key 必须是合法段 id"
                f"（{SID_RULE}；实际: {key!r}）")
        if isinstance(value, str):
            raise ValueError(
                f"images.json 的 '{key}' 必须是媒体对象（如 {{\"src\": \"images/{key}.png\"}}），"
                f"不接受裸字符串路径")
        if isinstance(value, dict):
            src = value.get("src")
            # type 先查、src 后查：写着 {"type": "chart"} 的旧稿件本来就没有
            # src，先查 src 会报"缺少 src"，把人往错的方向引。
            _t = value.get("type")
            if _t is not None and _t not in ("auto", "image", "video", "gif"):
                raise ValueError(
                    f"images.json 的 '{key}' 的 'type' 必须是 auto/image/video/gif"
                    f"（实际: {_t!r}）"
                    + ("——图表 / 公式卡已从本技能移除，数据图和公式改走手绘 SVG"
                       "（需要整页表达时给段落 layout: \"canvas\"）"
                       if _t in ("chart", "formula") else ""))
            # poster 与 src 同一校验口径：禁绝对路径/越出项目根。键存在就必须是
            # 非空字符串——旧写法 `if poster:` 让空串 "" 与 None 同判，坏空串原样
            if "poster" in value:
                poster = value["poster"]
                if not isinstance(poster, str) or not poster:
                    raise ValueError(
                        f"images.json 的 '{key}' 的 poster 必须是非空字符串"
                        f"（不用封面就删掉这个键；实际: {poster!r}）")
                value["poster"] = validate_relative_project_path(
                    poster, f"images.json 的 '{key}' 的 poster")
            # video 播放开关必须是 JSON 布尔：渲染端按 `opts.get("loop", True)`
            # 取值，字符串 "false" 是真值——写成字符串会让"关掉循环"变成"永远循环"。
            for _flag in ("loop", "muted", "autoplay", "playsinline"):
                if _flag in value and not isinstance(value[_flag], bool):
                    raise ValueError(
                        f"images.json 的 '{key}' 的 {_flag} 必须是 JSON 布尔 true/false"
                        f"（实际: {value[_flag]!r}）")
            if "src" not in value:
                raise ValueError(f"images.json 的 '{key}' 对象格式缺少 'src' 字段（媒体路径）")
        else:
            raise ValueError(
                f"images.json 的 '{key}' 必须是媒体对象（实际: {type(value).__name__}）")
        # 上面 dict 分支已拦缺键；非字符串（数字、列表这类手写 manifest 才会给）
        # 在这里点名，空串交给 validate_relative_project_path 的统一判定。
        if not isinstance(src, str):
            raise ValueError(f"images.json 的 '{key}' 的 src 必须是字符串")
        # 回写归一值：Windows 手写的 images\seg1.png 若原样流到渲染端
        # 会被 quote() 成 images%5Cseg1.png，跨平台即坏图。
        value["src"] = validate_relative_project_path(
            src, f"images.json 的 '{key}' 的 src")
        # provenance 记账字段必须是字符串。
        for field in ("source_url", "license", "attribution", "query", "provider"):
            if field in value and not isinstance(value[field], str):
                raise ValueError(f"images.json 的 '{key}' 的 {field} 必须是字符串")

        # inline_svg 是 _director_prepare 净化回填的内部键，绝不允许用户在
        # images.json 里手写——手写等于绕过 _svg_sanitize 把任意字符串塞进
        # 成片 DOM（内联 SVG 是同源活节点）。
        if "inline_svg" in value:
            raise ValueError(
                f"images.json 的 '{key}' 不得手写 'inline_svg'——它是生成期净化"
                "SVG 后回填的内部键；要时间轴同步动画请写 'director'")
        # director：方式 C SVG 的时间轴同步动画。只允许挂在 .svg 上（其它素材
        # 没有可被 GSAP 逐帧驱动的图内命名元素）。
        if "director" in value:
            if not is_svg_path(src):
                raise ValueError(
                    f"images.json 的 '{key}' 写了 director，但 src={src!r} 不是 SVG"
                    "——director 靠净化后内联的 SVG 命名元素做补间，只对 .svg 生效")
            _validate_director(key, value["director"])
        # stage:"keep"：跨段场景延续（把上一段演完的画面当作本页的起点，见 _stage_carry）。
        # 契约层只挡"写了不会生效"的形态；"接的是谁"（前一段存在吗、是不是同一张图）只有
        # 读得到 manifest 的 _director_prepare 判得了，那边按段落点名报错。
        # 不要求本页自己写 director：一段演完、下一页只是"停在那幅画上说话"是合法演法，
        # 那种页面 steps 为空，画面照常接续（inline_svg 由接续烘焙给出，不靠本页 steps）。
        if "stage" in value:
            if value["stage"] != "keep":
                raise ValueError(
                    f"images.json 的 '{key}' 的 stage 只认字符串 \"keep\""
                    f"（实际: {value['stage']!r}）——keep=接着上一页演，不写=每页独立成片")
            if not is_svg_path(src):
                raise ValueError(
                    f"images.json 的 '{key}' 写了 stage，但 src={src!r} 不是 SVG"
                    "——接续靠把上一页的收尾态烘焙进同一张 SVG 的内联副本，只对 .svg 生效")

    return data


def load_images_json(path):
    """读取并校验 images.json（读取失败统一成带路径的 ValueError）。"""
    return validate_images_json(read_json_file(path))
