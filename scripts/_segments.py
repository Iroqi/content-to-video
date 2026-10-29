#!/usr/bin/env python3
"""段 id 与版式的唯一口径。

id 会被 gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP 选择器字符串，
版式决定渲染器分派到哪套 DOM——两者的判定只在这里写一份：
is_valid_sid / seg_layout / needs_image / sids_needing_image，
以及契约层的字段校验 _validate_sid / _validate_layout。
"""
import re

# 段落 id 的合法形态见 _SID_RE / SID_RULE。之所以要收口成一条正则而不是各处
# 宽松判断：id 会被 gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP
# 选择器字符串（tl.fromTo("#{sid}",...)）——手写 manifest 里带引号/点号/方
# 括号的 sid 轻则选择器匹配失败动画静默丢失，重则内联 <script> 整段
# SyntaxError、字幕同步与时间轴注册全部死亡且无报错。在契约层收口校验。
_SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

# 会上墙的文字：一行一个去向，行数与列宽都是死的，所以比口播稿多一道约束
# （不收分号，见 _validate._validate_display_text）。口播稿不在这张表里。
# 清单只此一份：`_source_schema`（写稿入口）与 `_manifest_schema`（手写
# manifest 入口）都从这儿取——此前 manifest 侧根本没走这条约束，手写 manifest
# 的 `title` 带分号时，同一句话在写稿入口被拒、在手写入口静默上墙。
ON_SCREEN_SEGMENT_KEYS = ("title", "tagline", "takeaway")
# 同一条规则的对外说法。报错文案在 3 个调用点（source/manifest 的
# _validate_sid、images.json key）各写一份汉字副本时
# 已经漂过三次（"下划线/连字符" vs "-/_"、有无"1–64
# 字符"）。规则本身仍以 _SID_RE 为准，这里只是给人看的那一句的唯一副本，
# 改正则必须同步这句。
SID_RULE = "字母开头，仅字母/数字/-/_，1–64 字符"

# ── 内容段落 id 约定──────────────────────────────────────────────
# 段 id 的前缀决定"这是内容段还是结构性页"；"哪些段落要配图"另有一档，由前缀
# 加版式共同决定，一律调 needs_image()（见下）。判断"是不是内容段"一律调
# is_content_sid()，不要再写一份 startswith（结构性页一律用 STRUCTURAL_SIDS
# 点名，别在各处写字面量）。漏掉一处的后果是静默改变配图覆盖率统计口径（把不该
# 算的算进去，或该配图的段落被跳过拦截）。
CONTENT_SID_PREFIX = "seg"


def is_valid_sid(sid):
    """sid 是否是合法段 id（规则见 SID_RULE）。

    这些 id 会拼进 HTML 属性、JS 对象键与 GSAP 选择器（见 _SID_RE 注释）。
    校验收口在契约层（_validate_sid / validate_images_json，即"契约先校验"
    的入口），html_renderer 直接信任已校验的数据，不再各处补一道。
    """
    return isinstance(sid, str) and bool(_SID_RE.match(sid))


def is_content_sid(sid):
    """该 sid 是否属于"内容段"（以 seg 前缀判定，排除 opening/closing）。

    注意这只回答"段 id 是不是内容段"，不再等于"这一页要不要配图"——结构性页
    换成整页画布后那一页的唯一画面就是配图。判配图一律走 needs_image()。
    """
    return (sid or "").startswith(CONTENT_SID_PREFIX)


def needs_image(seg):
    """该段是否计入"配图覆盖率 / 缺图拦截"。run.py 与 gen_hyperframes 共用这一条。

    内容段一律计入（槽位版式缺图只是少一块画面，仍然该报）；结构性页默认不计入
    （agenda 卡纯文字），但整页画布 / 图版那一页整页就是那张图，不计入的话
    覆盖率会报"全部命中"而 HTML 步骤必然 exit 1。两边各写一份口径的代价就是
    一个拦一个放，报告数字和实际能不能出片对不上。

    seg 是已过契约的 manifest 段（id 非空由 _manifest_schema 把守），直接下标；
    留 .get 兜底等于宣称"id 可能缺失"，而那种数据根本到不了这里。
    """
    return is_content_sid(seg["id"]) or is_full_bleed(seg)


def sids_needing_image(manifest):
    """manifest 里"需要配图"的段 id 列表（按段落顺序）。

    这是 needs_image() 的遍历封装，存在的理由只有一个：run.py 的覆盖率统计与
    gen_hyperframes 的缺图提示原先各抄一份同样的列表推导，并在注释里互相指认
    "同一口径"——那种口径靠人维持，改一边就漏一边（报告说图齐了而出片失败）。
    要不要因缺图而拦，仍归各自决定，这里只回答"该有哪些段有图"。
    """
    return [seg["id"] for seg in manifest.get("segments", [])
            if needs_image(seg)]


# 段落版式四档：不写 layout = 槽位版式（标题区 + 配图槽 + 句子流）；"canvas" =
# 整页画布（配图拉满全屏，标题层与句子流层都不渲染——画面全交给那张图，想要页底
# 一条字幕就写 subtitle:true，见 seg_subtitle）；"compare" = 并置对比（标题区照旧，
# 图槽一分为二，两张图各自按旁白入场 / 演进）；"agenda" = 开屏/结尾那张纯文字页。
# 只有 agenda 绑段 id：它是结构性页专属的投影卡（行来自全片其它段——作者能选这一
# 档，选不了它印什么，见 seg_layout）；canvas / compare 两段通用。
# 版式后果见 references/rendering.md「画面结构」，画布规格与选型判据见
# references/canvas.md「整页画布」与同文件「并置对比」「字幕层」。
LAYOUT_VALUES = ("canvas", "compare", "agenda")
# 已并入别处的版式取值 → 现在该写什么。这里只负责把话说明白，"认不认"由调用方
# 决定（见 _validate_layout 的 retired_ok 参数）。
RETIRED_LAYOUTS = {
    "plate": ('"plate" 已并入 "canvas"：版式按 canvas 渲染，字幕层默认打开（等价于 '
              'subtitle:true）——想彻底不要字幕就写 "subtitle": false'),
}

# 满幅版式：配图铺满整页，标题层与句子流层都不生成。渲染端据此把图槽盒拉到整幅、
# 并在 clips 预处理里把 wipe 归零（画面主导的页不参与翻页转场）。**只有这一份
# 判据**：html_renderer 的图槽几何、wipe 归零、字体图槽体检的分派、check_svg 的
# 框尺寸都从这里读——各处各写一份 `== "canvas"` 的代价是加新满幅档时漏掉某一处，
# 那一处就静默按 4:3 槽位量一张整页的图。
FULL_BLEED_LAYOUTS = ("canvas",)


def is_full_bleed(seg):
    """这一页的配图是不是铺满整幅（canvas）。"""
    return seg_layout(seg) in FULL_BLEED_LAYOUTS


def seg_subtitle(seg):
    """整页画布页底那条字幕要不要出现（默认要）。

    显式写的 `subtitle` 优先（false 才关）；没写时看历史写法——被并入 canvas 的
    `layout:"plate"` 那一页本来就带字幕，合并不能把它弄丢。
    """
    if seg.get("subtitle") is not None:
        return bool(seg["subtitle"])
    if seg.get("layout") == "plate":
        return True
    return seg_layout(seg) == "canvas"


# 满幅页上不那么上画面的字段：`title` / `tagline` 照旧写（它们是 agenda 投影与别的
# 版式的数据源），只是这一页不画——别因为画面上没有就删掉。
STRUCTURAL_SIDS = ("opening", "closing")

# 需要"一对图"的版式：images.json 里对应条目写成 {"src": ["左", "右"], ...}。
# 这个清单同时是"这条 entry 该按哪套校验"的唯一分派依据——_images_schema 校验
# 形状、html_renderer 装配、check_svg 量尺寸都从它读，不各抄一份。
PAIRED_LAYOUTS = ("compare",)


def seg_layout(seg):
    """渲染层的唯一版式分派入口：'slot' / 'canvas' / 'compare' / 'agenda'。

    历史取值 `plate` 在这里归一到 `canvas`：版式的差别本来就只有"要不要交出版面"，
    字幕是它的一个开关（`seg_subtitle`）而不是另一种页面。

    agenda 由 pipeline 自动盖进 manifest，所以正常数据里它已经在 `layout` 字段上；
    这里仍按 id 兜一档，是因为手写 manifest 是本技能支持的用法（见
    references/tts_pipeline.md），漏盖 layout 时该页仍该是 agenda——静默按槽位版式
    渲染会把章节列表挤成一句 verse，比报错难发现得多。
    """
    lay = seg.get("layout")
    if lay == "plate":
        return "canvas"
    if lay:
        return lay
    return "agenda" if seg["id"] in STRUCTURAL_SIDS else "slot"


def is_next_page_continuing(segs, i):
    """第 i 页之后紧接着的那一页，是不是**接着这一页的画面往下演**。

    判据两条：两页同一张 `src`，且第 i 页写了 `animate`（没写的页没有可接续的
    元素态，那张图就是一张静图）；**外加一条前置**：第 i 页是满幅版式。

    为什么前置是满幅版式：槽位页的构图归模板（标题区、配图槽、句子流三层），
    制作者的两页之间隔着一条擦除 + 配图入场的页界，这是**版式语义**而不是回弹；
    满幅页相反，它整页就是那张图、本来就不参与模板转场，页界回弹才真是缺陷。
    并且接续在槽位页上不可靠：同一张 SVG 内联两份，图内 id 在成片 DOM 里重复，
    后一份里 `#id` 解析成空集——那半页的补间一拍都不落地（`hyperframes@0.8.185`
    的 Chrome 里实测；烘焙口径见 `_stage_carry`）。

    三处消费者读同一份判据：渲染端（接续页不擦进来、也不重播入场）、生成期接续
    烘焙（第 i 页的补间要在页界处先结算成终态）、写稿门禁（第 i+1 页的台词板有
    没有落空）。`segs[i]` 要带 `src` / `animate` / `layout`（images.json 的条目与
    manifest 段各出一半，调用方负责合进来）；取不到就当"不接续"。
    """
    if i + 1 >= len(segs):
        return False
    here, nxt = segs[i], segs[i + 1]
    src = here.get("src")
    return (bool(src) and src == nxt.get("src")
            and is_full_bleed(here)
            and bool((here.get("animate") or {}).get("steps")))


def _validate_layout(value, where, *, content=False, retired_ok=True):
    """layout 取值校验（可选字段）。严格匹配：不做大小写归一、不 strip。
    放宽才是灾难——把 'Canvas' 猜成 'canvas' 猜错方向的那一段会静默按槽位版式
    渲染，画布自己画的标题和 HTML 标题层并排出现在同一帧。宁可在这里报错。

    两个显式值里只有 agenda 绑段 id：它是结构性页那张投影卡，内容段借用它等于在
    画面上印别人的目录。canvas / plate 反过来谁都能用，包括 opening/closing——
    那条路的代价（整页画布：标题层、句子流、章节列表全都不再上画面；图版：句子流
    与 tagline 不再上画面）由作者自愿承担，门禁只保证画面本身成立（有图、等比、
    字号够，见 gen_hyperframes.canvas_layout_errors）。
    """
    if value is None:
        return
    if isinstance(value, str) and value in RETIRED_LAYOUTS:
        # 历史取值：默认放行（老稿件不改也能跑，版式由 seg_layout 归一），但要
        # 说清楚现在该写什么——静默接受等于让两套写法永远并存。
        if retired_ok:
            return
        raise ValueError(f"{where} 的 layout={value!r}：{RETIRED_LAYOUTS[value]}")
    if not isinstance(value, str) or value not in LAYOUT_VALUES:
        raise ValueError(
            f"{where} 的 layout={value!r} 不是合法版式：只接受 "
            f"{'、'.join(repr(v) for v in LAYOUT_VALUES)}，或整个不写"
            "（不写 = 槽位版式：标题区 + 配图槽 + 句子流）")
    if content and value == "agenda":
        raise ValueError(
            f"{where} 写了 layout=\"agenda\"，但 agenda 只属于 opening/closing："
            "它的行是对全片其它段的投影（开屏=各段标题+时长，结尾=各段 takeaway），"
            "不是这一段自己的文字——内容段用它在画面上印别人的目录")


def _validate_sid(sid, where, seen):
    """段落 id 校验：合法字符集 + 跨段唯一。

    两个调用点都只在 id 这一键**存在**时才调进来（source 侧 id 可缺省，缺省即
    交给管线自动编号；manifest 侧 id 必填、先要求非空字符串）。收到 None/空串
    说明调用点写错了，直接走下面的报错——空串不是"没写"，写了空值要当场拒，
    否则同一份输入在两道门给出两个答案。
    """
    if not is_valid_sid(sid):
        raise ValueError(
            f"{where} 的 id={sid!r} 含非法字符或格式不对——id 的合法形态是"
            f"{SID_RULE}（如 seg1、opening），"
            f"因为它会被拼进 HTML 属性与 GSAP 选择器")
    if sid in seen:
        raise ValueError(f"{where} 的 id={sid!r} 与前面的段落重复"
                         f"（选择器会互相串台）")
    seen.add(sid)
