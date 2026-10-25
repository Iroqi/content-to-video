#!/usr/bin/env python3
"""字段级校验原语：source/manifest/images 三份契约共用的单字段判定。

统一抛带 where 的 ValueError（"哪一层、哪个字段、实际收到什么"），
让坏输入在进 TTS / 进渲染之前就报对人。段 id 与版式的校验在
_segments（它们带着本技能特有的唯一性与分派语义）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _theme import is_safe_css_color  # noqa: E402  _theme 不反向依赖本模块


# 草稿位的约定前缀：`_` 开头的键是"写给自己看的"。
# 为什么要留这个口子：字段集封闭挡的是**拼错成另一个合法键的近邻**（takeaway →
# take_away 会静默退回标题），而不是挡"我暂时把一版文案贴在这儿"。没有草稿位时，
# 想留个 `_alt_title` 反复比稿就得先删掉它才能跑——迭代的代价是"改两次稿、删两次
# 字段"，于是人绕过契约（改成注释、塞进 text 里）——那才是真的静默丢。
# 代价是明确的一侧：`_` 开头的键**保证不被读**，所以它不可能是某个真字段的拼错
# （真字段一个都不以 _ 开头），也不会顺着 `.get()` 漏进产物。每次都会点名提醒。
DRAFT_PREFIX = "_"


def draft_keys(obj):
    """该层里以 `_` 开头的草稿键（升序）。"""
    return sorted(k for k in obj if str(k).startswith(DRAFT_PREFIX))


def _reject_unknown_keys(obj, allowed, where):
    """未知键拦截：一次报全所有未知键，并把本层合法清单一起给出。

    `_` 开头的键不算未知：它们是草稿位（见 DRAFT_PREFIX），照旧不生效，但会以
    `[notice]` 点名，避免"我写了它、以为它在起作用"。
    """
    unknown = sorted(str(k) for k in obj
                     if k not in allowed and not str(k).startswith(DRAFT_PREFIX))
    if unknown:
        raise ValueError(
            f"{where} 有未知字段 {'、'.join(repr(k) for k in unknown)}——"
            f"本层只认 {'、'.join(repr(k) for k in allowed)}。"
            "字段名拼错不会生效也不会被读出（整页可能悄悄少掉），请改名或删掉")
    drafts = draft_keys(obj)
    if drafts:
        print(f"[notice] {where} 的 {'、'.join(drafts)} 以 _ 开头，按草稿处理："
              f"整层都不生效（不进 TTS、不上画面）。要它上场就把键改成真字段名",
              file=sys.stderr)


def _validate_text(value, where, *, required=False):
    """Validate a user-facing text field before any downstream ``.strip()``/HTML use."""
    if value is None:
        if required:
            raise ValueError(f"{where} 必须是字符串")
        return
    if not isinstance(value, str):
        raise ValueError(f"{where} 必须是字符串（实际: {type(value).__name__}）")
    if required and not value.strip():
        raise ValueError(f"{where} 不能为空字符串")


# 上墙文字 = 会直接印在一行里的字段：卡片标题、小字、要点、结尾尾行。
def _validate_display_text(value, where):
    """上墙文字比口播稿多一条：一行只承载一个事实，所以不收分号。

    分号出现意味着两件事被挤进同一行。agenda 行与标题区的行数预算都是死的，
    第二件事要么被 nameTrim 裁掉，要么把整行压成小字号，两种都是静默丢信息，
    所以这里直接拒，让写稿的人当场改。
    口播稿（opening / closing / text / dialogue）不受这条限制：分句器把分号
    当终止标点，念出来本来就是两次停顿。
    """
    if value is None:
        return
    if isinstance(value, str) and ("；" in value or ";" in value):
        raise ValueError(
            f"{where} 里有分号：{value!r}。上墙的一行只放一个事实，"
            "把分号改成句号拆成两行，或者删掉后半句")


def is_finite_number(value):
    """有限数值判定（布尔不算数字）：三份契约的数值守卫共用这一个谓词。

    为什么必须连 NaN/±Infinity 一起拒：Python 的 json 解析默认接受这三个字面量
    （`{"at": NaN}` 不是标准 JSON，但 `json.load` 不拦），而 NaN 的**任何比较都为
    False**——`nan < 0` 亦然。只写 `isinstance(v, (int, float))` 的守卫会把它整条
    放行，下游再把 nan 格式化进 style/transform，浏览器整条丢弃：动画停在演之前的
    样子，而生成期、渲染日志、`hyperframes check` 全都一声不吭。
    """
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def _validate_finite_number(value, where, *, nonnegative=False, positive=False):
    """Validate numeric manifest fields without accepting booleans or NaN/Inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} 必须是数值（实际: {value!r}）")
    # 到这里类型已经是数字，剩下的只有 NaN/±Inf 一种可能——判据走
    # `is_finite_number`（三份契约共用那一个谓词），不再抄第二份 isfinite。
    if not is_finite_number(value):
        raise ValueError(f"{where} 必须是有限数值（实际: {value!r}）")
    if nonnegative and float(value) < 0:
        raise ValueError(f"{where} 必须大于等于 0（实际: {value!r}）")
    if positive and float(value) <= 0:
        raise ValueError(f"{where} 必须是正有限数值（实际: {value!r}）")


# accent 的合法形态见 _theme.is_safe_css_color（hex 或 CSS 标准颜色名），
# 白名单之外不进生成 HTML。
def _validate_accent(value, where):
    """accent 取值校验（可选字段）：必须是浏览器认得的颜色（hex 或 CSS 标准色名）。

    为什么必须白名单、放行哪两种形态、挡掉哪些字符，见 _theme.is_safe_css_color
    ——判定规则也在那里，此处不另写一份正则。
    """
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 的 'accent' 必须是非空字符串"
                         f"（如 #2dd4bf），实际: {value!r}")
    v = value.strip()
    if not is_safe_css_color(v):
        raise ValueError(
            f"{where} 的 accent={value!r} 不是合法颜色：只接受 #rgb / #rrggbb "
            f"十六进制（如 #2dd4bf）或浏览器标准色名（如 red、tomato）。"
            f"accent 会拼进成片 HTML，不接受 rgb()/var() 这类函数式写法")
