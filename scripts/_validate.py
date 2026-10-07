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


def _reject_unknown_keys(obj, allowed, where):
    """未知键拦截：一次报全所有未知键，并把本层合法清单一起给出。"""
    unknown = sorted(str(k) for k in obj if k not in allowed)
    if unknown:
        raise ValueError(
            f"{where} 有未知字段 {'、'.join(repr(k) for k in unknown)}——"
            f"本层只认 {'、'.join(repr(k) for k in allowed)}。"
            "字段名拼错不会生效也不会被读出（整页可能悄悄少掉），请改名或删掉")


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


def _validate_finite_number(value, where, *, nonnegative=False, positive=False):
    """Validate numeric manifest fields without accepting booleans or NaN/Inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} 必须是数值（实际: {value!r}）")
    if not math.isfinite(float(value)):
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
