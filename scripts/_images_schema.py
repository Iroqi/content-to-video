#!/usr/bin/env python3
"""images.json 与媒体路径的契约。

配图由 agent/人工产出、渲染端按这份映射消费；坏路径会让 HTML 静默产出
空白裂图，所以引用完整性在 load_images_json / validate_images_json
fail-fast，路径语义（禁绝对路径/越出项目根）由 validate_relative_project_path
统一收口。媒体类型判定（扩展名 → image/video/gif）也在这里，与
html_renderer 的媒体分支共用。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import read_json_file  # noqa: E402
from _segments import SID_RULE, is_valid_sid  # noqa: E402


def validate_relative_project_path(src, where="media path"):
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
# src/type/poster/loop/muted/autoplay/playsinline → html_renderer 的媒体分支；
# 其余五个是 provenance 记账字段，画面不读、但按 SKILL.md 要求留存。
# 之外的键（`position`/`fit`/`alt` 这类凭空发明的写法）过去会被静默丢掉：
# "我明明写了 alt，画面上什么都没有"变成无解的困惑。判定收口在这一个函数，
# warn 只在丢键的那一处（html_renderer
# ._normalize_images）打；run.py/gen_hyperframes 的预检走 validate_images_json。
MEDIA_ENTRY_KEYS = frozenset({
    "src", "type", "poster", "loop", "muted", "autoplay", "playsinline",
    "source_url", "license", "attribution", "query", "provider",
})


def unknown_media_keys(entry):
    """该 images.json 条目里渲染端不会读的键（升序列表）。"""
    return sorted(set(entry) - MEDIA_ENTRY_KEYS)


def validate_images_json(data):
    """images.json：{segment_id: 媒体对象}。

    唯一写法（图片/视频统一）：
        {"seg1": {"src": "images/seg1.png"},
         "seg2": {"src": "images/seg2.mp4", "type": "video", "loop": true,
                  "muted": true, "autoplay": true, "poster": "...png"}}
      "type" 可选（auto=按扩展名判断；image/video/gif 显式指定），其余字段均为
      可选（静态图只需 src）。不接受裸字符串路径。
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
            if "src" not in value:
                raise ValueError(f"images.json 的 '{key}' 对象格式缺少 'src' 字段（媒体路径）")
            if not value["src"]:
                raise ValueError(f"images.json 的 '{key}' 的 'src' 不能为空")
        else:
            raise ValueError(
                f"images.json 的 '{key}' 必须是媒体对象（实际: {type(value).__name__}）")
        # 走到这里 src 必然存在且非空（上面 dict 分支已拦缺键与空值）。
        if not isinstance(src, str) or not src:
            raise ValueError(f"images.json 的 '{key}' 的 src 必须是非空字符串")
        # 回写归一值：Windows 手写的 images\seg1.png 若原样流到渲染端
        # 会被 quote() 成 images%5Cseg1.png，跨平台即坏图。
        value["src"] = validate_relative_project_path(
            src, f"images.json 的 '{key}' 的 src")
        # poster 与 src 同一校验口径：禁绝对路径/越出项目根。键存在就必须是
        # 非空字符串——旧写法 `if poster:` 让空串 "" 与 None 同判，坏空串原样
        # 流到渲染端当封面路径用。
        if "poster" in value:
            poster = value["poster"]
            if not isinstance(poster, str) or not poster:
                raise ValueError(
                    f"images.json 的 '{key}' 的 poster 必须是非空字符串"
                    f"（不用封面就删掉这个键；实际: {poster!r}）")
            value["poster"] = validate_relative_project_path(
                poster, f"images.json 的 '{key}' 的 poster")
        # video 播放开关必须是 JSON 布尔：渲染端按 `opts.get("loop", True)`
        # 判真值，字符串 "false" 是 truthy，会画出意外的循环/静音行为。
        for field in ("loop", "muted", "autoplay", "playsinline"):
            if field in value and not isinstance(value[field], bool):
                raise ValueError(
                    f"images.json 的 '{key}' 的 {field} 必须是 JSON 布尔"
                    f"true/false（实际: {value[field]!r}）")
        for field in ("source_url", "license", "attribution", "query", "provider"):
            if field in value and not isinstance(value[field], str):
                raise ValueError(f"images.json 的 '{key}' 的 {field} 必须是字符串")

    return data


def load_images_json(path):
    """读取并校验 images.json（读取失败统一成带路径的 ValueError）。"""
    return validate_images_json(read_json_file(path))


MEDIA_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


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
