#!/usr/bin/env python3
"""段 id 与版式的唯一口径。

id 会被 gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP 选择器字符串，
版式决定渲染器分派到哪套 DOM——两者的判定只在这里写一份：
is_valid_sid / seg_layout / needs_image / sids_needing_image，
以及契约层的字段校验 _validate_sid / _validate_layout。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 段落 id 的合法形态见 _SID_RE / SID_RULE。之所以要收口成一条正则而不是各处
# 宽松判断：id 会被 gen_hyperframes 直接拼进 HTML 的 id=/class= 属性和 GSAP
# 选择器字符串（tl.fromTo("#{sid}",...)）——手写 manifest 里带引号/点号/方
# 括号的 sid 轻则选择器匹配失败动画静默丢失，重则内联 <script> 整段
# SyntaxError、字幕同步与时间轴注册全部死亡且无报错。在契约层收口校验。
_SID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# 同一条规则的对外说法。报错文案在 4 个调用点（source/manifest 的
# _validate_sid、images.json key、html_renderer 的两道兜底）各写一份汉字副本时
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
    CLI 路径由 _validate_sid/validate_images_json 强制；库调用方直接传 dict
    给 html_renderer 时也用它兜底，避免"单点依赖 CLI 校验"。
    """
    return isinstance(sid, str) and bool(_SID_RE.match(sid))


def is_content_sid(sid):
    """该 sid 是否属于"需要配图的内容段落"（排除 opening/closing）。

    注意这只回答"段 id 是不是内容段"，不再等于"这一页要不要配图"——结构性页
    换成整页画布后那一页的唯一画面就是配图。判配图一律走 needs_image()。
    """
    return (sid or "").startswith(CONTENT_SID_PREFIX)


def needs_image(seg):
    """该段是否计入"配图覆盖率 / 缺图拦截"。run.py 与 gen_hyperframes 共用这一条。

    内容段一律计入（槽位版式缺图只是少一块画面，仍然该报）；结构性页默认不计入
    （agenda 卡纯文字），但 layout:"canvas" 那一页整页就是那张图，不计入的话
    覆盖率会报"全部命中"而 HTML 步骤必然 exit 1。两边各写一份口径的代价就是
    一个拦一个放，报告数字和实际能不能出片对不上。
    """
    return is_content_sid(seg.get("id")) or seg_layout(seg) == "canvas"


def sids_needing_image(manifest):
    """manifest 里"需要配图"的段 id 列表（按段落顺序，跳过空 id）。

    这是 needs_image() 的遍历封装，存在的理由只有一个：run.py 的覆盖率统计与
    gen_hyperframes 的缺图提示原先各抄一份同样的列表推导，并在注释里互相指认
    "同一口径"——那种口径靠人维持，改一边就漏一边（报告说图齐了而出片失败）。
    要不要因缺图而拦，仍归各自决定，这里只回答"该有哪些段有图"。
    """
    return [sid for seg in manifest.get("segments", [])
            if (sid := seg.get("id", "")) and needs_image(seg)]


# 段落版式三档：不写 layout = 槽位版式（标题区 + 配图槽 + 句子流）；"canvas" =
# 整页画布（配图拉满全屏，HTML 的标题层与句子流层都不渲染，标题与文字由画布自己
# 画）；"agenda" = 开屏/结尾那张纯文字页。只有 agenda 绑段 id：它是结构性页专属
# 的投影卡（行来自全片其它段——作者能选这一档，选不了它印什么，见 seg_layout）；
# canvas 两段通用——内容段用它换整页画布，结构性页用它把整页让给一张海报。
# 版式后果见 references/rendering.md「画面结构」，画布规格与选型判据见
# references/image_options.md「整页画布」。
LAYOUT_VALUES = ("canvas", "agenda")
STRUCTURAL_SIDS = ("opening", "closing")


def seg_layout(seg):
    """渲染层的唯一版式分派入口：'slot' / 'canvas' / 'agenda'。

    agenda 由 pipeline 自动盖进 manifest，所以正常数据里它已经在 `layout` 字段上；
    这里仍按 id 兜一档，是因为手写 manifest 是本技能支持的用法（见
    references/tts_pipeline.md），漏盖 layout 时该页仍该是 agenda——静默按槽位版式
    渲染会把章节列表挤成一句 verse，比报错难发现得多。
    """
    lay = seg.get("layout")
    if lay:
        return lay
    return "agenda" if (seg.get("id") or "") in STRUCTURAL_SIDS else "slot"


def _validate_layout(value, where, *, content=False):
    """layout 取值校验（可选字段）。严格匹配：不做大小写归一、不 strip。
    放宽才是灾难——把 'Canvas' 猜成 'canvas' 猜错方向的那一段会静默按槽位版式
    渲染，画布自己画的标题和 HTML 标题层并排出现在同一帧。宁可在这里报错。

    两个显式值里只有 agenda 绑段 id：它是结构性页那张投影卡，内容段借用它等于在
    画面上印别人的目录。canvas 反过来谁都能用，包括 opening/closing——那条路的
    代价（标题层、句子流、章节列表全都不再上画面）由作者自愿承担，门禁只保证
    画布本身成立（有图、等比、字号够，见 gen_hyperframes.canvas_layout_errors）。
    """
    if value is None:
        return
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

    两个调用点都只在 id 非空白时才调进来（source 侧跳过缺省/空白 id，
    manifest 侧先要求非空字符串），这里不再兼容 None/空串——收到它们
    说明调用点写错了，直接走下面的报错。
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
