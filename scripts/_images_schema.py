#!/usr/bin/env python3
"""images.json 与媒体路径的契约。

坏路径会让 HTML 静默产出裂图，所以引用完整性在 load_images_json /
validate_images_json fail-fast；路径语义由 validate_relative_project_path 收口。
媒体类型判定（扩展名 → image/video/gif）也在这里，与 html_renderer 共用。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import read_json_file  # noqa: E402
from _segments import SID_RULE, is_valid_sid  # noqa: E402
from _path_morph import check_morphable  # noqa: E402
from _ease import validate_ease  # noqa: E402
from _validate import (_reject_unknown_keys,  # noqa: E402  未知键拦截（panes 分格也走同一份）
                       is_finite_number)  # noqa: E402  数值守卫的单一谓词（拒 NaN/Inf）


def validate_relative_project_path(src, where):
    """Reject absolute/traversal paths and normalize separator semantics."""
    if not isinstance(src, str) or not src.strip():
        raise ValueError(f"{where} 必须是非空字符串")
    norm = src.replace("\\", "/")
    if "\x00" in norm:
        raise ValueError(f"{where} 不得包含 NUL 字节")
    # 任何前导 "/" 都要显式拦：ntpath.isabs("/tmp/a") 返回 False（盘符缺省），
    # 下游 abspath 会解析到 <当前盘>:\tmp。
    if (os.path.isabs(norm) or norm.startswith("/")
            or re.match(r"^[A-Za-z]:/", norm)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", norm)):
        raise ValueError(f"{where} 禁止使用绝对路径：{src!r}")
    depth = 0
    for part in (p for p in norm.split("/") if p not in ("", ".")):
        # Win32 路径规范化会剥掉每个组件的**尾部点与空格**：`.. `、`...`、
        # `.... ` 在 Windows 上都被解析成 `..`（Linux 下它们是普通目录名，
        # 所以只在 Windows 上逃得掉、本测试全绿会掩盖它）。按 Windows 口径归一
        # 后再比：合法文件名（`v1.` 归一为 `v1`）不受影响。
        if part.rstrip(" .") == ".." or (
                part.rstrip(" .") == "" and len(part) >= 2
                and set(part) <= {".", " "}):
            depth -= 1
            if depth < 0:
                raise ValueError(f"{where} 越过项目根目录：{src!r}")
        else:
            depth += 1
    return norm

# 槽位版式 evolve 不接受相机属性：镜头归模板（入场收拢 + Ken Burns 运镜），
# 作者再推一层就是两层意图打架，画面读成"图在自己乱动"。要整页推镜请走 director
# 的整页画布。真源在这里，_evolve.check_evolve 从它读，不另抄一份。
_EVOLVE_CAMERA_KEYS = frozenset({"svgOrigin", "transformOrigin", "scale",
                                 "xPercent", "yPercent", "rotation"})

# images.json 条目里渲染端**真正会读**的键。之外的键一律 warn 点名
# （`position`/`fit`/`alt` 这类凭空发明的写法不该被静默丢掉）。
# warn 只在预检（validate_images_json）打；html_renderer._normalize_images
# 要求入参已过本预检，不重复打。
# 并置对比（layout "compare"）每格的键集：只有两档补间与三项出处。刻意不给
# 播放/音频/type——两格都是静态图或 SVG（理由见 _check_pair）。
PANE_KEYS = ("director", "evolve", "derive", "source_url", "license", "attribution")

MEDIA_ENTRY_KEYS = frozenset({
    "src", "type", "poster", "loop", "muted", "autoplay", "playsinline",
    "source_url", "license", "attribution", "query", "provider",
    # director / derive：写了才走净化内联（见 references/motion.md「SVG 动画：两档」）。
    "director",
    # evolve：槽位版式里的"同一元素演进"（见 references/director.md「槽位版式的演进」）。
    # 与 director 同一套 steps 写法，但不接管整页编排、不调机位（镜头归模板）。
    "evolve",
    # derive：一条参数驱动的几何演绎（见 references/director.md「参数演绎」）。一拍只写
    # "参数推到多少"，图内几何由表达式逐帧重算——同步与中间态都是真几何，不靠抄参数。
    "derive",
    # stage："keep" = 本页从上一页演完的样子起跑（见 _stage_carry）。单取值枚举
    # 而非布尔：将来加"只搬相机"这类档位不必改 keep:true 的语义。
    "stage",
    # panes：并置对比（layout "compare"）的两格声明（见 _check_pair）。
    "panes",
    # inline_svg：**内部键**，由 gen_hyperframes 净化后回填。进这个集合只为不被
    # unknown_media_keys 误报；用户手写会被 validate_images_json 直接拒（否则等于
    # 开了"绕过净化、把任意字符串塞进 DOM"的注入口）。
    "inline_svg",
    # 视频音轨四件套：前三个管响度与进出，media_start 管从第几秒起播。只对
    # video 成立，写在非 video 上按"写了不会生效"拒掉（口径同 director/stage）。
    # <video> 的音轨是唯一不经过 TTS 却混进成片的声音（见 html_renderer._media_html），
    # 没增益旋钮就等于原声压着口播一起播，且链上没有任何报错。
    "volume", "fade_in", "fade_out", "media_start",
    # duck：让路开关，默认开。volume 压整条轨的响度，duck 只压人声占住的频段。
    # 要原声当主体（采访、同期声）显式写 duck:false。
    "duck",
    # drift：逐段覆盖静态图运镜（见 html_renderer._image_drift_lines）。
    # false = 这段完全不动；对象 = 只改写的几项，其余走模板 drift。
    "drift",
})

# Hyperframes 的音轨增益上限（+12dB），抄自官方 html-schema 契约，不另立数。
MEDIA_VOLUME_MAX = 3.98

# 关掉 muted 却没写 volume 时的默认增益（见 html_renderer._video_audio_attrs）。
# 0.25 是实测可听下沿（0.15~0.3 旁白仍清楚）；1.0 作默认等于默认交付一支旁白
# 被压住的片子。显式写 volume 就不再兜底。
MEDIA_DUCK_VOLUME = 0.25

# 取值范围：(下限, 上限, 文案)。None = 不封顶。"淡入比整段还长"不是取值越界，
# 而是与片段时长的关系，交给渲染端 warn。
_VIDEO_AUDIO_OPTS = {
    "volume": (0.0, MEDIA_VOLUME_MAX, "增益倍数，1 = 0 dB"),
    "fade_in": (0.0, None, "淡入秒数"),
    "fade_out": (0.0, None, "淡出秒数"),
    "media_start": (0.0, None, "从源片第几秒起播"),
}

# gif 与 image 同档（都走 <img>），单列只为让 images.json 能显式写 type:"gif"。
MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def is_svg_path(path):
    """这条路径是不是 SVG。五处（缺图探测/整页画布/renderer bare-media/director/
    stage）共用这一份：各写一遍 splitext 时，改了大小写规则只会改到其中一处，
    症状是"契约说能挂 director、渲染端却不内联"。
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
# GSAP 的 attr 是"SVG 属性名 → 值"的集合（d / pathLength / cx …），与 CSS 通道
# 并列。这是**跨模块真源**：接续烘焙（`_stage_carry`）从这里导入同一个键名。修复
# 前两处各写一个 "attr" 字面量，只靠注释约定"两处必须指同一个东西"——但没有任何
# 机制 enforce；一旦分家，"契约层放行的形状"和"烘焙认得的形状"就不是一回事，症状
# 是 stage:"keep" 接续页第 0 帧就坏且生成期零报错。跨模块相等由
# `tests/test_shared_geometry.py` 钉住。
ATTR_KEY = "attr"

# 无限循环（repeat:-1）的放行开关：与 `_ease.allow_extra` 同一个形状，只由
# `gen_hyperframes --allow-infinite-loop`（预览档）在进程内打开。**契约层默认拒收**，
# 因为它换不来任何画面收益，只换来一条 1e10 量级的总时长——而"按时刻取帧"是核对
# 成片的唯一手段。开关只活在当前进程，绝不落进产物。
_ALLOW_INFINITE = {"on": False}


def allow_infinite_loop(on=True):
    """开/关无限循环的宽松档。只影响 `_validate_repeat_yoyo`。"""
    _ALLOW_INFINITE["on"] = bool(on)


# at_time 的相对写法：以 "+0.5" / "-0.2" 出现，含义是"上一条 beat 结束之后再过
# 这么多秒"（首条则从段落音频起点算）。让整页画布的自由时间线能顺次链接节奏，
# 不必每步手算绝对秒——重配音后绝对秒会整体漂移，相对链则跟着上一条走。
_RELATIVE_AT_TIME_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")


def _validate_tween_vars(vars_, where):
    """GSAP 补间变量：只允许 JSON 可序列化的值，禁 on* 回调键与控制旋钮。

    这些字典会被 json.dumps 注入内联脚本：JSON 转义挡住了字符串注入，但 on*
    回调键等于在渲染页里执行任意 JS，一律不收。允许嵌套 dict（attr:{}），逐层
    校验叶子是标量。

    控制旋钮（_PAYLOAD_CONTROL_KEYS）必须在这一层判：调用点只认顶层，
    `{"to": {"attr": {"duration": 2}}}` 会漏过去。
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
        if k == ATTR_KEY and not isinstance(v, dict):
            # GSAP 的 attr 是"SVG 属性名 → 值"的一集合。标量与数组在渲染端不报错
            # （补间静默无效），真正的坏在跨段接续：烘焙 _settle_payload 只认 dict，
            # 非 dict 掉进 CSS 通道，把 style="attr:5" 写死在下一页的起始画面上——
            # 本页看着没事，接续页第 0 帧就是坏的。
            raise ValueError(
                f"{where} 的 'attr' 必须是对象（SVG 属性名 → 值，如 "
                f"{{\"d\": \"M0 0L1 1\"}}）（实际: {v!r}）——非对象写法不会当场报错，"
                "只会在 stage:\"keep\" 的接续烘焙里被当成 CSS 属性写进 style，"
                "让下一页的起始画面从第一帧就是坏的")
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

    同拓扑（命令字母序列 + 每条数值个数一致）在契约层就拦，别等生成期。核见
    _path_morph。
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

    渲染端展开成"代理对象补间 + onUpdate 写 textContent"——那个 onUpdate 是我们
    生成的，不是信源塞进来的回调。除 to 外全可选：decimals 控制小数位，
    prefix/suffix 裹数字。
    """
    if not isinstance(count, dict):
        raise ValueError(f"{where} 必须是对象 {{\"to\": 数字, ...}}（实际: {type(count).__name__}）")
    unknown = set(count) - {"to", "from", "decimals", "prefix", "suffix"}
    if unknown:
        raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 to / from / decimals / prefix / suffix")
    for nm in ("to", "from"):
        if nm in count:
            v = count[nm]
            if not is_finite_number(v):
                raise ValueError(f"{where} 的 {nm} 必须是数字且有限"
                                 f"（NaN/Infinity 写进补间，浏览器会整条丢弃，实际: {v!r}）")
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
    逐字写回。只收空对象 `{}`：字数取自那条 <text>（运行时读 textContent），节奏
    走 step 级 duration/ease。
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

    收数字（每个间隔秒数）或对象（GSAP stagger 配置：each/amount/from/grid/ease）。
    值走标量/数组校验、禁 on*。
    """
    if not isinstance(stagger, dict):
        if not is_finite_number(stagger):
            raise ValueError(f"{where} 必须是数字（每个间隔秒）或对象（GSAP stagger 配置）"
                             f"，且数字必须有限（NaN/Infinity 不算数；实际: {stagger!r}）")
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
            if not (isinstance(v, str) or is_finite_number(v)):
                raise ValueError(f"{where} 的 from 必须是字符串或有限数字"
                                 f"（NaN/Infinity 不算数；实际: {v!r}）")
        elif k == "ease":
            validate_ease(v, f"{where} 的 ease")
        else:  # each / amount
            if not is_finite_number(v) or v < 0:
                raise ValueError(f"{where} 的 {k} 必须是 ≥0 的有限数字"
                                 f"（NaN/Infinity 不算数；实际: {v!r}）")
    return stagger


def _validate_repeat_yoyo(step, where):
    """repeat / yoyo 的取值判据——**唯一真源**。

    images.json 的 `director.steps[]` 与 canvas_kit 的 spec 元素共用这一份（各自
    只另加上下文约束）。两处各写一遍会在改判据那天分家：一处放行、一处拒收，
    报错文案还一模一样。

    repeat 只收整数：GSAP 吃 0.5 这种半遍，而跨段接续与出画自查都要说"最后停在
    哪一头"，半遍收尾在两者里都是猜。

    **`repeat:-1`（无限循环）在这里就拒**，不再"运行时按片长折算"。GSAP 把无限循环
    按 `REPEAT_DELAY=1e10` 折算总时长，整条时间线从此变成 1e10 量级，
    `tl.pause()/seek()`、预览进度条、按时刻取帧三处一起坏掉——而那三处正是"成片与
    预览是否一致"的唯一证据链。这不是"少写一个旋钮"，是换来一个**可证明的时长**：
    每一拍的总跨度都写死在数据里，manifest 就能核"演完没有"。
    无限循环的正当需求（呼吸 / 漂移）由模板自己的稳态层承担，那里按片长折算遍数
    （`html_renderer._loop_repeat`），不经过作者的手。要循环几次就写几次，
    渲染端另有 `--allow-infinite-loop` 给"先看手感"的预览档。
    """
    if "repeat" in step:
        v = step["repeat"]
        if isinstance(v, bool) or not isinstance(v, int) or v < -1:
            raise ValueError(f"{where} 的 repeat 必须是 ≥-1 的整数（-1=无限循环，"
                             f"0=只演一遍；实际: {v!r}）")
        if v == -1 and not _ALLOW_INFINITE["on"]:
            raise ValueError(
                f"{where} 写了 repeat:-1（无限循环）：GSAP 会按 1e10 折算整条时间线的"
                "总时长，按时刻取帧、预览进度条、跨段接续三处一起失效，而这三处正是"
                "核对成片的唯一手段。写成实际遍数（如 repeat:5）；只想先看手感可以走"
                "预览档 --until html --allow-infinite-loop（带它渲染会在入口被拒）")
    if "yoyo" in step:
        if not isinstance(step["yoyo"], bool):
            raise ValueError(f"{where} 的 yoyo 必须是 JSON 布尔 true/false"
                             f"（实际: {step['yoyo']!r}）")
        # 只在 yoyo 真的开着时要求 repeat：显式写 `yoyo:false` 是合法且与缺省
        # 等价的写法，按"键存在"问就会把一句没问题的声明判成错。
        if step["yoyo"] and not step.get("repeat"):
            raise ValueError(f"{where} 写了 yoyo 但 repeat 缺省或 0——只演一遍时 GSAP "
                             "根本不会回头。要来回就写 repeat:1（奇数遍收尾在 to，"
                             "偶数遍收尾回起点）")


def _validate_director(key, dirval, what="director"):
    """校验 images.json 条目的编排块（时间轴同步的 SVG 动画）。

    `what` 只换报错里的名字：evolve 与 director 共用同一套 steps 语法，两条路
    的锚点/target/互斥规则必须一模一样，复制一份是下一处漂移的起点。
    """
    if not isinstance(dirval, dict):
        raise ValueError(f"images.json 的 '{key}' 的 {what} 必须是对象")
    unknown = set(dirval) - {"steps"}
    if unknown:
        raise ValueError(
            f"images.json 的 '{key}' 的 {what} 含未知键 {sorted(unknown)}——只认 'steps'")
    steps = dirval.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(
            f"images.json 的 '{key}' 的 {what}.steps 必须是非空数组"
            "（每一步 = 在某句旁白时对某个 SVG 元素补间）")
    for i, step in enumerate(steps):
        where = f"images.json 的 '{key}' 的 {what}.steps[{i}]"
        if not isinstance(step, dict):
            raise ValueError(f"{where} 必须是对象")
        unknown = set(step) - _DIRECTOR_STEP_KEYS
        if unknown:
            raise ValueError(f"{where} 含未知键 {sorted(unknown)}——只认 "
                             f"{sorted(_DIRECTOR_STEP_KEYS)}")
        # 锚点二选一：at（句序）或 at_time（段落绝对秒）。两者都写或都不写都是歧义，
        # 一律拒。at_time 让导演档脱离"句子分桶"：整页画布的自由编排可以直接按秒排
        # 补间，不必迁就旁白句边界（见 references/motion.md「SVG 动画：两档」）。
        has_at, has_time = "at" in step, "at_time" in step
        if has_at == has_time:
            raise ValueError(
                f"{where} 的锚点必须是 at / at_time 二选一："
                "at=该段第几句旁白（0 基，小数=句内偏移，如 1.5=第 2 句念到一半），"
                "at_time=从该段音频起点的绝对秒数（旁白无关的自由时间线）"
                f"（实际: 写了 {sorted(k for k in ('at', 'at_time') if k in step) or '两者皆无'}）")
        if has_at:
            at = step["at"]
            if not is_finite_number(at) or at < 0:
                raise ValueError(
                    f"{where} 的 at 必须是非负数（该段内第几句旁白，0 基；小数=句内偏移，"
                    f"如 1.5=第 2 句念到一半；NaN/Infinity 不算数）（实际: {at!r}）")
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
            elif not is_finite_number(at_time) or at_time < 0:
                raise ValueError(
                    f"{where} 的 at_time 必须是非负数字（从该段音频起点算的秒数，"
                    f"NaN/Infinity 不算数）或相对串 \"+秒\"（实际: {at_time!r}）")
        target = step.get("target")
        if not isinstance(target, str) or not _DIRECTOR_TARGET_RE.match(target):
            raise ValueError(
                f"{where} 的 target 必须是单个 id 或 class 选择器"
                "（# 或 . 开头，后接字母/数字/-/_，字母或下划线起头）"
                f"——它会被拼进 GSAP 选择器（实际: {target!r}）")
        draw = step.get("draw", False)
        if not isinstance(draw, bool):
            raise ValueError(f"{where} 的 draw 必须是 JSON 布尔 true/false（实际: {draw!r}）")
        kinds = [k for k in ("from", "to", "set") if k in step
                 and isinstance(step[k], dict)]
        if not kinds and "to" in step:
            # derive 的单步借用本函数做锚点/duration/ease 校验：它的 `to` 是**数字**
            # （参数推到多少），不是补间变量。判据不能因此放宽（`to: {}` 依旧非法），
            # 所以这里只认"数值型 to"这一种借用，其余原样往下走。
            kinds = ["to"] if is_finite_number(step["to"]) else []
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
                if not is_finite_number(v) or v < 0:
                    raise ValueError(f"{where} 的 {k} 必须是 ≥0 的有限数字"
                                     f"（NaN/Infinity 不算数；实际: {v!r}）")
        if "ease" in step:
            validate_ease(step["ease"], f"{where} 的 ease")
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
        # repeat / yoyo：这一拍演完再演几遍、以及来回（GSAP 的同名补间变量）。
        # 判据在 _validate_repeat_yoyo（与 canvas_kit 的 spec 共用一份）。
        # 排在"有没有可重放的补间"之后：那条说的是这一拍根本不会演，比"yoyo 缺
        # repeat"更靠近病因，先说它。
        _validate_repeat_yoyo(step, where)
        # morph 的形变是生成期采样成离散关键帧的：无限循环没有"最后一帧"可采，采样器只能
        # 猜。补间/count/type 走运行期，GSAP 按时间解析求值，无限循环反而是确定的。
        if morph is not None and step.get("repeat", 0) < 0:
            raise ValueError(
                f"{where} 的 morph 不能配 repeat:-1——形变是生成期按帧率采样成离散关键帧的，"
                "无限循环没有终点可采。要循环就写明确遍数（repeat:N）")
        # morph 的形变是采样出来的：`duration: 0` 时采样器按 max(2, 0×fps) 采两个关键帧，
        # 全落在同一时刻——成片里不是"很快的形变"，是**瞬跳**，而作者看到的仍是自己写的
        # 那条曲线。duration 缺省（模板 director.duration）不在此列：那是没写，不是写了 0。
        if morph is not None and "duration" in step and float(step["duration"]) <= 0:
            raise ValueError(
                f"{where} 写了 morph 却给 duration={step['duration']}——形变是生成期"
                "按 duration×fps 采样成离散关键帧的，0 秒采不出中间态，成片里这一拍"
                "会退化成瞬跳。给一个 >0 的 duration，或删掉让它走模板缺省")
    return dirval



def unknown_media_keys(entry):
    """该 images.json 条目里渲染端不会读的键（升序列表）。"""
    return sorted(set(entry) - MEDIA_ENTRY_KEYS)


def validate_images_json(data):
    """images.json：{segment_id: 媒体对象}。

    两种写法：
        {"seg1": {"src": "images/seg1.png"},
         "seg2": {"src": "images/seg2.svg", "director": {...}},
         "seg3": {"src": "images/seg3.gif"}}
      "type" 可选（auto=按扩展名；image/video/gif 显式指定），其余字段可选
      （静态图只需 src），不接受裸字符串路径。

    只校验结构，不读像素，因此**不要求图片不透明**：透明 PNG/WEBP 与 SVG 等价，
    透明处会透出主题渐变与 accent 辉光。别为"防盖背景"加 opacity 校验——那会误伤
    合法的透明前景图（信源问题见 imagery.md「透明通道」）。
    """
    if not isinstance(data, dict):
        raise ValueError("images.json 顶层必须是对象 {segment_id: 媒体对象}")
    # 分组判据是「互不依赖」：段与段之间互不依赖，按段收集，段内按组收集。
    errs = []
    for key, value in data.items():
        _validate_entry(key, value, errs)
    if errs:
        raise ValueError("images.json 有{} 处问题：\n{}".format(
            len(errs), "\n".join(errs)))
    # 未知键在**预检**点名叫出，与渲染端丢键路径同一判据但只打一次。不阻断：
    # 封闭字段集只对 source JSON 强制，images.json 保留宽松。
    for key, value in data.items():
        if isinstance(value, dict):
            unknown = unknown_media_keys(value)
            if unknown:
                print(f"[warn] images.json 的 '{key}' 含渲染端不读的字段："
                      f"{', '.join(unknown)}——它们不会出现在画面上。条目只认："
                      f"{', '.join(sorted(MEDIA_ENTRY_KEYS))}；"
                      "写错了就删掉。"
                      "想改构图请改稿件或 SVG 本体，images.json 只管映射与播放属性",
                      file=sys.stderr)
    return data


def _collect(errs, what):
    """跑一项检查，把它的 ValueError 收进 errs 而不是中断整段。

    只收 ValueError（TypeError 之类是代码 bug）。按行摊平：一组检查可能一次报
    多条，不摊平开头那句"N 处问题"会少数落。
    """
    try:
        what()
    except ValueError as e:
        errs.extend(str(e).splitlines())


def _validate_entry(key, value, errs):
    """校验 images.json 的一条 {段 id: 媒体对象}，把错收进 errs。"""
    # key 与 manifest 段 id 同一口径（is_valid_sid 包住 _SID_RE）：这些键会拼进 HTML 的
    # id/选择器链路，"seg 3"、"seg.png" 之类在渲染端静默失联。
    if not is_valid_sid(key):
        errs.append(
            f"images.json 的段落 key 必须是合法段 id"
            f"（{SID_RULE}；实际: {key!r}）")
        return  # key 不合法就没法把后面的错点名给人看（每条错都要引用这个 key）
    if isinstance(value, str):
        errs.append(
            f"images.json 的 '{key}' 必须是媒体对象（如 {{\"src\": \"images/{key}.png\"}}），"
            f"不接受裸字符串路径")
        return
    if not isinstance(value, dict):
        errs.append(
            f"images.json 的 '{key}' 必须是媒体对象（实际: {type(value).__name__}）")
        return
    src = value.get("src")
    # 一对图（并置对比版式）：src 写成 ["左", "右"]，两个位各自可以挂 director /
    # evolve。它与单图条目是两套形状，判据也分开——先判这一支再落到单图那套，
    # 免得"src 不是字符串"那句把一对图判成错误。
    if isinstance(src, list):
        _collect(errs, lambda: _check_pair(key, value, src))
        return
    # type 组先报src 缺失/类型，之后各组只在 src 是字符串时才查路径与媒体类型——
    # 早退会把同段里其它键的错一起吞掉（比只报一条更糟：改完 src 重跑才知道
    # 还有下一处）。四个播放开关不依赖 src，缺 src 也要照报。
    _collect(errs, lambda: _check_type(key, value, src))
    _collect(errs, lambda: _check_flags(key, value))
    _collect(errs, lambda: _check_provenance(key, value))
    if "inline_svg" in value:
        errs.append(
            f"images.json 的 '{key}' 不得手写 'inline_svg'——它是生成期净化"
            "SVG 后回填的内部键；要时间轴同步动画请写 'director'")
    if not isinstance(src, str):
        return
    _collect(errs, lambda: _check_video_only_opts(key, value, src))
    _collect(errs, lambda: _check_audio_opts(key, value, src))
    _collect(errs, lambda: _check_duck(key, value, src))
    _collect(errs, lambda: _check_poster(key, value))
    _collect(errs, lambda: _normalize_src(key, value, src))
    _collect(errs, lambda: _check_derive(key, value, src))
    _collect(errs, lambda: _check_director(key, value, src))
    _collect(errs, lambda: _check_evolve(key, value, src))
    _collect(errs, lambda: _check_stage(key, value, src))
    _collect(errs, lambda: _check_drift(key, value))


def _check_pair(key, value, src):
    """并置对比版式的条目：`src` 是一对图，`panes` 可选。

    形状：

        {"seg3": {"layout_compare": true,
                  "src": ["images/before.svg", "images/after.svg"],
                  "panes": [{"director": {...}}, {"director": {...}}]}}

    为什么允许 `panes` 而不是把两套补间塞进一个 `director`：两张图各自有自己
    的元素 id（`#a`、`#b` 往往重名），合成一份 steps 就分不清这一条打的是哪一格。
    分格声明是唯一不会歧义的写法。

    刻意**不接受视频**：两段视频同屏时各自的音轨、各自的循环相位、各自的
    duck 让路会互相打架，而这一版式要的是"并排看清两个状态的差别"——动态的
    并置反而让人分不清哪边在动。要动画就用 SVG（panes 里的 director/evolve 够用）。
    """
    if len(src) != 2:
        raise ValueError(
            f"images.json 的 '{key}' 的 src 是一对图（并置对比版式），必须正好 2 项"
            f"（左 / 右），实际 {len(src)} 项")
    for i, item in enumerate(src):
        if not isinstance(item, str):
            raise ValueError(
                f"images.json 的 '{key}' 的 src[{i}] 必须是字符串路径"
                f"（实际: {type(item).__name__}）")
        if _is_video_src(value, item):
            raise ValueError(
                f"images.json 的 '{key}' 的 src[{i}]={item!r} 是视频：并置对比"
                "只收静态图与 SVG。两段视频同屏时各自的音轨、循环相位、duck "
                "让路会互相打架，动态并置也让人分不清哪边在动——"
                "要两份都动就画 SVG，用 panes 里的 director/evolve 分别驱动")
        # 路径归一（越界/绝对路径）与单图同一条判据，回写成两边各自一项。
        src[i] = validate_relative_project_path(
            item, f"images.json 的 '{key}' 的 src[{i}]")
    # 单图专属的键在这一支上都不成立：type/poster/播放开关/音频路由按
    # 单条媒体算，一对图没有"这一条的 type"这回事。
    for _opt in ("type", "poster", "loop", "muted", "autoplay", "playsinline",
                 "volume", "fade_in", "fade_out", "media_start", "duck",
                 "source_url", "license", "attribution", "query", "provider",
                 "drift", "stage", "director", "evolve", "derive"):
        if _opt in value:
            raise ValueError(
                f"images.json 的 '{key}' 是并置对比（src 为一对图），不接受 "
                f"'{_opt}'（那是单图条目的键）。"
                "两格各自的补间写进 panes[0] / panes[1]，"
                "两格各自的出处写进 panes[i] 的同名键；整页的运镜与接续在这一版式"
                "上不适用")
    panes = value.get("panes")
    if panes is None:
        value["panes"] = _default_panes(key)
        return
    if not isinstance(panes, list) or len(panes) != 2:
        raise ValueError(
            f"images.json 的 '{key}' 的 'panes' 必须是 2 项的数组"
            "（[左格, 右格]，每格 {\"director\"?: …, \"evolve\"?: …}）")
    for i, pane in enumerate(panes):
        if pane is None:
            panes[i] = {}
            continue
        if not isinstance(pane, dict):
            raise ValueError(
                f"images.json 的 '{key}' 的 panes[{i}] 必须是对象"
                f"（实际: {type(pane).__name__}）")
        _reject_unknown_keys(pane, PANE_KEYS, f"images.json 的 '{key}' 的 panes[{i}]")
        for _opt in ("director", "evolve"):
            if _opt in pane:
                what = _opt
                _validate_director(what, pane[_opt], what=what)
        if "derive" in pane:
            if not is_svg_path(src[i]):
                raise ValueError(
                    f"images.json 的 '{key}' 的 panes[{i}] 写了 derive，但那一格 "
                    f"src={src[i]!r} 不是 SVG——derive 只对 .svg 生效")
            validate_derive(f"{key} 的 panes[{i}]", pane["derive"])
        # 两格各自的出处：写进 pane 才不会被上面的"单图键"拦下，而它确实
        # 是逐格的事实（左边是实拍、右边是示意图，出处本来就不一样）。
        for _opt in ("source_url", "license", "attribution"):
            if _opt in pane and not isinstance(pane[_opt], str):
                raise ValueError(
                    f"images.json 的 '{key}' 的 panes[{i}].{_opt} 必须是字符串")


def _default_panes(key):
    """没写 panes 时给两格各一份空声明（= 两张静态图，各自不动）。"""
    return [{}, {}]


def _check_video_only_opts(key, value, src):
    """poster 与四个播放开关：只对 video 成立，写在非 video 上一律拒。

    与 `_check_audio_opts` 同一口径：渲染端 `<img>` 分支只输出 `src`（见
    `html_renderer._media_html`），这些键写在非视频上会被静默丢弃——它们都在
    MEDIA_ENTRY_KEYS 里，连 unknown-key 提示都没有。调用方保证 src 是字符串。
    """
    if _is_video_src(value, src):
        return
    _errs = []
    for _opt in ("poster", "loop", "muted", "autoplay", "playsinline"):
        if _opt in value:
            _errs.append(
                f"images.json 的 '{key}' 写了 {_opt}，但 src={src!r} 不是视频"
                f"（type={(value.get('type') or 'auto')!r}）——{_opt} 只对 video"
                "生效，ImageGen 图 / SVG / GIF 走 <img>，这些键会被静默丢掉")
    if _errs:
        raise ValueError("\n".join(_errs))


def _normalize_src(key, value, src):
    """src 路径归一后回写（顺带把越界/绝对路径挡掉）。

    回写是必需的：Windows 手写的 images\\seg1.png 若原样流到渲染端会被
    quote() 成 images%5Cseg1.png，跨平台即坏图。
    """
    value["src"] = validate_relative_project_path(
        src, f"images.json 的 '{key}' 的 src")


def _check_type(key, value, src):
    """type 的合法取值 + src 必须在场且是字符串。"""
    # type 先查、src 后查：写着 {"type": "chart"} 的旧稿件本来就没有
    # src，先查 src 会报"缺少 src"，把人往错的方向引。
    _t = value.get("type")
    if _t is None:
        # 显式 null 与"键不存在"在 Python 里都读到 None，但下游两种消费口径
        # 相反：_is_video_src 写 `value.get("type") or "auto"` 把 null 当缺省，
        # html_renderer._media_html 写 `media.get("type", "auto")` 把 null 当
        # 显式值——classify_media_path 见 None != "auto" 会原样返回 None，
        # 于是一条 mp4 配 "type": null 静默变成 <img>（契约层放行、渲染端
        # 坏图）。契约层统一样子：抹掉 null，让可选字段真的只有缺省一种形态
        # （与 _normalize_src 的回写归一同一模式）。
        value.pop("type", None)
    elif _t not in ("auto", "image", "video", "gif"):
        raise ValueError(
            f"images.json 的 '{key}' 的 'type' 必须是 auto/image/video/gif"
            f"（实际: {_t!r}）"
            + ("——图表 / 公式卡已从本技能移除，数据图和公式改走手绘 SVG"
               "（需要整页表达时给段落 layout: \"canvas\"）"
               if _t in ("chart", "formula") else ""))
    if "src" not in value:
        raise ValueError(f"images.json 的 '{key}' 对象格式缺少 'src' 字段（媒体路径）")
    # 非字符串（数字、列表这类手写 manifest 才会给）在这里点名；空串交给
    # validate_relative_project_path 的统一判定。
    if not isinstance(src, str):
        raise ValueError(f"images.json 的 '{key}' 的 src 必须是字符串")


def _is_video_src(value, src):
    """src 是不是视频（type 缺省即auto 时按扩展名判，与 classify_media_path
    同一条路）。调用方保证 src 是字符串。"""
    return classify_media_path(src, value.get("type") or "auto") == "video"


def _check_flags(key, value):
    """loop / muted / autoplay / playsinline：四个键互不依赖，逐个报。"""
    # 播放开关必须是 JSON 布尔：渲染端按 `opts.get("loop", True)`
    # 取值，字符串 "false" 是真值——写成字符串会让"关掉循环"变成"永远循环"。
    _errs = []
    for _flag in ("loop", "muted", "autoplay", "playsinline"):
        if _flag in value and not isinstance(value[_flag], bool):
            _errs.append(
                f"images.json 的 '{key}' 的 {_flag} 必须是 JSON 布尔 true/false"
                f"（实际: {value[_flag]!r}）")
    if _errs:
        raise ValueError("\n".join(_errs))


def _check_audio_opts(key, value, src):
    """视频音轨四件套：只对 video 成立，且每个键的值域互不依赖。

    ImageGen 图/SVG/GIF 没有音轨也没有可 trim 的时间轴，写了不会生效 = 静默丢键，
    按契约层统一口径拒。

    调用方保证 src 是字符串。
    """
    _errs = []
    for _opt, (_lo, _hi, _hint) in _VIDEO_AUDIO_OPTS.items():
        if _opt not in value:
            continue
        if not _is_video_src(value, src):
            _errs.append(
                f"images.json 的 '{key}' 写了 {_opt}，但 src={src!r} 不是视频"
                f"（type={(value.get('type') or 'auto')!r}）——{_opt} 只对 video 生效，"
                "ImageGen 图 / SVG / GIF 没有音轨也没有可裁剪的时间轴")
            continue  # 不是视频时值域无从谈起，别再叠一句"必须是数字"
        _v = value[_opt]
        # 布尔必须单独挡：isinstance(True, int) 为真，`"volume": true`
        # 会溜过数值校验、被渲染端拼成 data-volume="True"——那是个无效
        # 增益，症状是"要了原声、成片却没声音"，与本套契约要堵的正是
        # 同一类静默退化。与 loop/muted 的布尔口径保持一致。
        if not is_finite_number(_v):
            _errs.append(
                f"images.json 的 '{key}' 的 {_opt} 必须是数字且有限"
                f"（{_hint}；NaN/Infinity 不算数；实际: {_v!r}）")
            continue
        if _v < _lo or (_hi is not None and float(_v) > _hi):
            _bound = (f"{_lo} ~ {_hi}" if _hi is not None else f"≥ {_lo}")
            _errs.append(
                f"images.json 的 '{key}' 的 {_opt} 超出范围（{_bound}）"
                f"（{_hint}；实际: {_v!r}）")
    if _errs:
        raise ValueError("\n".join(_errs))


def _check_duck(key, value, src):
    """duck 与四件套同一口径：只对 video 成立，且必须是 JSON 布尔。

    （渲染端按 `opts.get("duck", True)` 取值，字符串 "false" 是真值，
    会让"关掉让路"变成"永远让路"，与 loop/muted 那条同一个坑。）
    """
    if "duck" not in value:
        return
    _errs = []
    if not _is_video_src(value, src):
        _errs.append(
            f"images.json 的 '{key}' 写了 duck，但 src={src!r} 不是视频"
            f"（type={(value.get('type') or 'auto')!r}）——duck 只对 video 生效，"
            "ImageGen 图 / SVG / GIF 没有音轨可让")
    if not isinstance(value["duck"], bool):
        _errs.append(
            f"images.json 的 '{key}' 的 duck 必须是 JSON 布尔 true/false"
            f"（实际: {value['duck']!r}）")
    if _errs:
        raise ValueError("\n".join(_errs))


def _check_poster(key, value):
    """poster 与 src 同一校验口径：禁绝对路径/越出项目根。键存在就必须是
    非空字符串——旧写法 `if poster:` 让空串"" 与 None 同判，坏空串原样。"""
    if "poster" not in value:
        return
    poster = value["poster"]
    if not isinstance(poster, str) or not poster:
        raise ValueError(
            f"images.json 的 '{key}' 的 poster 必须是非空字符串"
            f"（不用封面就删掉这个键；实际: {poster!r}）")
    value["poster"] = validate_relative_project_path(
        poster, f"images.json 的 '{key}' 的 poster")


def _check_provenance(key, value):
    """provenance 记账字段必须是字符串。"""
    _errs = []
    for field in ("source_url", "license", "attribution", "query", "provider"):
        if field in value and not isinstance(value[field], str):
            _errs.append(f"images.json 的 '{key}' 的 {field} 必须是字符串")
    if _errs:
        raise ValueError("\n".join(_errs))


def _check_director(key, value, src):
    """director：SVG 的时间轴同步动画。只允许挂在 .svg 上（其它素材
    没有可被 GSAP 逐帧驱动的图内命名元素）。"""
    if "director" not in value:
        return
    if not is_svg_path(src):
        raise ValueError(
            f"images.json 的 '{key}' 写了 director，但 src={src!r} 不是 SVG"
            "——director 靠净化后内联的 SVG 命名元素做补间，只对 .svg 生效")
    _validate_director(key, value["director"])


DERIVE_STEP_KEYS = frozenset({"at", "at_time", "param", "from", "to",
                              "duration", "ease", "delay"})
DERIVE_BIND_KEYS = ("target", "attr", "expr")


def validate_derive(key, block):
    """`derive`：一条参数驱动的几何演绎（见 `_derive` 与 director.md「参数演绎」）。

    与 director 的分界：director 写"什么时候哪个元素变成什么"（属性级，一个元素
    一条）；derive 写"这个参数走到多少，几何自己算"（参数级，一拍驱动一组元素）。
    两者可以同段共存（一个管出场、一个管连续演绎），但**同一个属性不能同时被
    两者写**——那条冲突要连读 SVG 才知道，放在 gen_hyperframes 判。

    这里只做形状与取值：键集封闭、参数名合法、初值是有限数、表达式能编译、target
    形态与 director 同一条正则（它也进 GSAP 选择器）、step 的锚点/时长/缓动与
    director 同一套判据（复用 `_validate_director` 的单步检查会要求 target，
    所以锚点与时长这几条单独走一遍同名函数，判据只有一份）。
    """
    from _derive import (BIND_ATTRS, PARAM_NAME_RE, ExprError, compile_expr)
    where = f"images.json 的 '{key}' 的 derive"
    _reject_unknown_keys(block, ("params", "steps", "bind"), where)
    params = block.get("params")
    if not isinstance(params, dict) or not params:
        raise ValueError(
            f"{where}.params 必须是非空对象（参数名 → 初始值），"
            '如 {"theta": 0}——没有参数就没有可演绎的东西')
    for name, v in params.items():
        if not isinstance(name, str) or not PARAM_NAME_RE.match(name):
            raise ValueError(
                f"{where}.params 的参数名 {name!r} 不合法：它要出现在表达式里，"
                "只收字母或下划线开头、字母/数字/下划线、≤32 字符")
        if not _finite(v):
            raise ValueError(f"{where}.params.{name} 的初值必须是有限数字"
                             f"（NaN/Infinity 不算数；实际: {v!r}）")
    steps = block.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{where}.steps 必须是非空数组（每一步推一个参数）")
    for i, st in enumerate(steps):
        w = f"{where}.steps[{i}]"
        if not isinstance(st, dict):
            raise ValueError(f"{w} 必须是对象")
        _reject_unknown_keys(st, DERIVE_STEP_KEYS, w)
        if ("at" in st) == ("at_time" in st):
            raise ValueError(
                f"{w} 的锚点必须是 at / at_time 二选一（at=该段第几句旁白，"
                "at_time=从该段音频起点的秒数或相对串 \"+0.5\"）")
        anchor = {k: st[k] for k in ("at", "at_time", "duration", "ease", "delay")
                  if k in st}
        anchor["to"] = st.get("to")
        # duration 的判据在这里比 director 严一档，理由不是口味：参数演绎的每一帧都是
        # 由"进度 × 表达式"**重算**出来的，没有中间态可言——duration 缺省走模板值，写 0
        # 则整段演绎退化成一帧瞬跳，而作者写它的意图几乎必然是"没有时长"而不是"我要瞬跳"
        # （要瞬跳就把 to 写进 params 初值，或干脆别写这一步）。
        if "duration" in st and float(anchor["duration"]) == 0:
            raise ValueError(
                f"{w} 的 duration=0——参数演绎是按进度逐帧重算几何的，时长 0 会让"
                f"整段演绎退化成一次瞬跳；要瞬跳就直接把终值写进 params 初值，"
                f"要演就给它一个 >0 的时长（省略则走模板缺省）")
        _validate_director_probe(anchor, w)
        pname = st.get("param")
        if pname not in params:
            raise ValueError(
                f"{w} 的 param 必须是 params 里声明过的参数名"
                f"（{'、'.join(sorted(params))}），实际: {pname!r}")
        if "to" not in st:
            raise ValueError(f"{w} 缺 'to'（这一步要把 {pname} 推到多少）——"
                             "参数演绎必须给终点")
        for k in ("to", "from"):
            if k in st and not _finite(st[k]):
                raise ValueError(f"{w} 的 {k} 必须是有限数字（实际: {st[k]!r}）")
    binds = block.get("bind")
    if not isinstance(binds, list) or not binds:
        raise ValueError(
            f"{where}.bind 必须是非空数组——没有绑定的参数不会改变画面，"
            '一条绑定形如 {"target": "#arm", "attr": "x2", "expr": "540 + 260*cos(theta)"}')
    for i, b in enumerate(binds):
        w = f"{where}.bind[{i}]"
        if not isinstance(b, dict):
            raise ValueError(f"{w} 必须是对象")
        _reject_unknown_keys(b, DERIVE_BIND_KEYS, w)
        target = b.get("target")
        if not isinstance(target, str) or not _DIRECTOR_TARGET_RE.match(target):
            raise ValueError(
                f"{w} 的 target 必须是单个 id 或 class 选择器（# 或 . 开头），"
                f"与 director 同一条约束（实际: {target!r}）")
        if b.get("attr") not in BIND_ATTRS:
            raise ValueError(
                f"{w} 的 attr 只收几何属性 {'/'.join(BIND_ATTRS)}（实际: "
                f"{b.get('attr')!r}）。`d` / `points` 这类复合几何要整条重写路径，"
                "不属于参数演绎；颜色与透明度归 director")
        try:
            compile_expr(b.get("expr"), params, f"{w}（{target}.{b['attr']}）")
        except ExprError as e:
            raise ValueError(str(e))
    return block


def _finite(v):
    return is_finite_number(v) and not isinstance(v, bool)


def _validate_director_probe(step, where):
    """复用 director 单步里"与 target 无关"的那几条判据（锚点、duration、ease）。

    为什么能复用：这些规则一旦分家，同一个 `at: 4` 在 director 里被拒、在 derive
    里被放行，作者会以为后者更宽松——而渲染端拿同一份 `beat_positions` 解析，它只
    认一份口径。做法是把单步塞进一个**只含这些键**的探针副本，走 director 那份
    校验（has_at / at_time / duration / delay / ease 全部由它判），报错文案前缀换回
    本层。`to` 由调用方保证"参数 + 数值"，探针里补一个数值占位当补间变量。
    """
    probe = dict(step, target="#probe")
    # 补间变量给一个真属性，`duration` / `ease` 才走得到它们那几条判据
    # （`{}` 会先撞"至少要有一个 from / to / set"）。
    if "to" in probe:
        probe["to"] = {"opacity": 1}
    try:
        _validate_director("?", {"steps": [probe]}, what="?")
    except ValueError as e:
        raise ValueError(str(e).replace("images.json 的 '?' 的 ?.steps[0]", where))


def _check_derive(key, value, src):
    """derive：参数驱动的几何演绎。与 director 一样只对 SVG 生效（要净化内联）。"""
    if "derive" not in value:
        return
    if not is_svg_path(src):
        raise ValueError(
            f"images.json 的 '{key}' 写了 derive，但 src={src!r} 不是 SVG"
            "——derive 逐帧改写图内元素的几何属性，只对 .svg 生效")
    validate_derive(key, value["derive"])


def _check_evolve(key, value, src):
    """evolve：槽位版式里"讲到哪儿、图里的元素就变到哪儿"（见 _evolve）。

    与 director 的差别只在语义边界，语法是同一套：steps 的校验**原样复用**
    `_validate_director`，于是三条锚点规则（at / at_time 二选一、at 非负、
    target 是单个 id/class）两边永远一致。evolve 只额外管两件：

    - **不能与 director 同段**：一个是整页编排、一个是槽位内演进，同时写就是
      两个作者抢同一张图（画面上表现为机位和内容各按各的节拍走）。
    - **不再调机位**：`svgOrigin` / `scale` 这类相机写法归 director；槽位里的
      镜头由模板的入场收拢与 Ken Burns 负责，作者再推一层就是两层意图打架。
      这条在生成期按 SVG 内容再查一遍（见 `_evolve.check_evolve`），这里先把
      最明显的错误挡在契约层。
    """
    if "evolve" not in value:
        return
    errs = []
    if "director" in value:
        errs.append(
            f"images.json 的 '{key}' 同时写了 director 和 evolve——前者是整页画布"
            "编排（标题层与句子流层都不生成），后者是槽位版式里的图内演进。"
            "二选一：要整页跟着旁白演用 director，只想让图里的元素跟着变用 evolve")
    if not is_svg_path(src):
        errs.append(
            f"images.json 的 '{key}' 写了 evolve，但 src={src!r} 不是 SVG"
            "——evolve 靠净化后内联的 SVG 命名元素做补间，只对 .svg 生效")
    if errs:
        raise ValueError("\n".join(errs))
    _validate_director(key, value["evolve"], what="evolve")
    for i, step in enumerate(value["evolve"]["steps"]):
        for bucket in ("to", "from", "set"):
            vars_ = step.get(bucket) or {}
            cam = sorted(k for k in vars_ if k in _EVOLVE_CAMERA_KEYS)
            if cam:
                raise ValueError(
                    f"images.json 的 '{key}' 的 evolve.steps[{i}] 的 {bucket} 写了 "
                    f"{cam}：槽位版式的镜头归模板（入场收拢 + 运镜），图内不再调机位。"
                    "要让整页跟着旁白推镜，把这段换成 layout:\"canvas\" 写 director")


def _check_stage(key, value, src):
    """stage:"keep"：跨段场景延续（把上一段演完的画面当作本页的起点，见 _stage_carry）。

    只挡"写了不会生效"的形态；"接的是谁"（前一段存在吗、是不是同一张图）只有读得到
    manifest 的 gen_hyperframes 判得了。本页可以不写 director：steps 为空的页面照样
    接续（inline_svg 由接续烘焙给出）。
    """
    if "stage" not in value:
        return
    _errs = []
    if value["stage"] != "keep":
        _errs.append(
            f"images.json 的 '{key}' 的 stage 只认字符串 \"keep\""
            f"（实际: {value['stage']!r}）——keep=接着上一页演，不写=每页独立成片")
    if not is_svg_path(src):
        _errs.append(
            f"images.json 的 '{key}' 写了 stage，但 src={src!r} 不是 SVG"
            "——接续靠把上一页的收尾态烘焙进同一张 SVG 的内联副本，只对 .svg 生效")
    if _errs:
        raise ValueError("\n".join(_errs))


_DRIFT_KEYS = {"ratePerSec": (0.0, None, "每秒推近的比例（0.006 = 每 10 秒推 6%）"),
               "maxScale": (1.0, None, "推近封顶倍数（1.08 = 最多放大 8%）"),
               "panX": (0.0, 1.0, "横向用掉多少推近盈余（1 = 边缘刚好贴住槽位边）"),
               "panY": (0.0, 1.0, "纵向用掉多少推近盈余")}


def _check_drift(key, value):
    """drift：逐段覆盖静态图的运镜。false 关掉，或只写要改的那几个数。

    区间与模板同一套语义（见 html_renderer._image_drift_lines 的三条约束）：
    速率与 pan 非负、pan ≤ 1——pan > 1 不是"再推远"，而是把图边缘拖进槽位（露底）。
    """
    if "drift" not in value:
        return
    raw = value["drift"]
    if raw is False:
        return
    if not isinstance(raw, dict):
        raise ValueError(
            f"images.json 的 '{key}' 的 drift 只认 false（这一段不要运镜）或对象"
            f"（要改的那几项），实际: {raw!r}")
    unknown = sorted(set(raw) - set(_DRIFT_KEYS))
    if unknown:
        raise ValueError(
            f"images.json 的 '{key}' 的 drift 含未知键 {unknown}——只认 "
            f"{'、'.join(_DRIFT_KEYS)}")
    for name, val in raw.items():
        lo, hi, why = _DRIFT_KEYS[name]
        if not is_finite_number(val):
            raise ValueError(f"images.json 的 '{key}' 的 drift.{name} 必须是数值"
                             f"（NaN/Infinity 不算数；实际: {val!r}）")
        if float(val) < lo or (hi is not None and float(val) > hi):
            bound = f"不小于 {lo:g}" if hi is None else f"在 {lo:g}~{hi:g} 之间"
            raise ValueError(f"images.json 的 '{key}' 的 drift.{name} 要{bound}"
                             f"（{why}；实际: {val!r}）")


def load_images_json(path):
    """读取并校验 images.json（读取失败统一成带路径的 ValueError）。"""
    return validate_images_json(read_json_file(path))
