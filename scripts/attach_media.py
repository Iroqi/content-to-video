#!/usr/bin/env python3
"""预渲染视频素材接入：把离线录屏 / 三维 / shader 产物接成合规 images.json 条目。

这是**主管线之外**的一步（同 canvas_kit.py / extension.py）：产物是一份标准
images.json，run.py 不认识这个工具，也不该认识。三维、着色器、网页导览三类
体裁共用这条路径——画面在 skill 之外预渲染成固定帧率的 mp4/webm，skill 只负责
排版、配音与时间轴。评级与三条结构性边界见 references/formats.md 第三节。

    python scripts/attach_media.py --images hf-project/images.json \
        --segment seg2 --src images/tour.mp4 \
        --media-start 0.4 --loop --fade-in 0.3 --fade-out 0.3

两条刻意收窄的边界，都由契约层当场拦下而不是留到渲染时才静默失效：

1. `src` 必须是项目内相对路径且是视频扩展名（MEDIA_VIDEO_EXTS 是唯一真源）；
2. 视频段**不能**挂 `animate`——`_images_schema._check_animate` 只认 .svg，
   要逐拍图内编排就把标注烘进素材，或改用 SVG。工具不会替你写 animate。

写盘前整份 images.json 会过 `validate_images_json`，所以字段名拼错、布尔写成
字符串、NaN/Inf 数值都在这一步报错。Python 3.9 兼容，只用标准库。
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _images_schema import (MEDIA_VIDEO_EXTS,  # noqa: E402
                             classify_media_path, validate_images_json,
                             validate_relative_project_path)
from _segments import is_valid_sid  # noqa: E402
from _validate import is_finite_number  # noqa: E402

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 条目里能通过契约层的可选键：布尔 / 数值 / 字符串三组分开，避免任一组的
# 形态校验漏到另一组。type 不写（auto 按扩展名判定），animate 永不生成。
_BOOL_KEYS = ("muted", "loop", "duck")
_NUM_KEYS = ("media_start", "fade_in", "fade_out", "volume")
_STR_KEYS = ("poster", "provider", "source_url", "attribution", "license")


def _guard_output(path, where):
    """产物不许落进技能目录：那是源码，不是制作产物（同 extension.py 口径）。"""
    ap = os.path.abspath(path)
    try:
        common = os.path.commonpath([ap, SKILL_ROOT])
    except ValueError:
        common = None  # Windows 不同盘符，天然不落在技能目录
    if common == SKILL_ROOT:
        raise ValueError("%s 落在技能目录内：%s。先 cd 到项目目录再跑，"
                         "产物和源码不能共用一个目录" % (where, ap))
    parent = os.path.dirname(ap)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    return ap


def build_video_entry(src, **opts):
    """造一条合规的视频素材条目（不写盘、不读素材内容）。

    opts 只收 _BOOL_KEYS / _NUM_KEYS / _STR_KEYS 里的键，其余一律拒——
    手写 images.json 时「字段名拼对一半」不会让任何示例跑不通，只会让渲染
    端静静丢掉一个旋钮。这里把键集封闭住，再用契约层收口取值。
    """
    unknown = sorted(set(opts) - set(_BOOL_KEYS + _NUM_KEYS + _STR_KEYS))
    if unknown:
        raise ValueError("不认识的素材旋钮 %s——只认 %s" %
                         (unknown, ", ".join(_BOOL_KEYS + _NUM_KEYS + _STR_KEYS)))
    rel = validate_relative_project_path(src, "src")
    if classify_media_path(rel, "auto") != "video":
        raise ValueError("src=%r 不是视频素材——只认 %s；三维 / shader / "
                         "网页导览请先把离线渲染产物合成单个视频文件；SVG 请直接写 "
                         "images.json 并挂 animate（本工具只接 B 档预渲染视频）" %
                         (rel, "/".join(sorted(MEDIA_VIDEO_EXTS))))

    entry = {"src": rel, "muted": bool(opts.get("muted", True))}
    for key in _BOOL_KEYS:
        if key in opts:
            if not isinstance(opts[key], bool):
                raise ValueError("%s 必须是 JSON 布尔（写了不会生效的旋钮比"
                                 "漏写更难查）" % key)
            entry[key] = opts[key]
    for key in _NUM_KEYS:
        if key in opts:
            if not is_finite_number(opts[key]):
                raise ValueError("%s 必须是有限数（NaN/±Inf/超大整数会让"
                                 "json.dump 写出非法 JSON）" % key)
            entry[key] = opts[key]
    for key in _STR_KEYS:
        if key in opts:
            if not isinstance(opts[key], str) or not opts[key].strip():
                raise ValueError("%s 必须是非空字符串" % key)
            entry[key] = opts[key]

    # 最后一步交给唯一的契约真源：字段集、值域、video-only 限制都在这里判。
    validate_images_json({"seg1": entry})
    return entry


def attach(images_path, segment, entry, force=False, dry_run=False):
    """把条目合并进 images.json；返回合并后的整份数据（dry_run 时不写盘）。

    - 目标段已存在时默认拒绝覆盖，`force=True` 才允许；
    - 已存在的段挂着 `animate` 时一律拒（哪怕 force）——视频段不能逐拍编排，
      这正是 B 档体裁的硬边界，静默替换会让作者以为标注还在；
    - 合并后整份过契约层，别让工具成为绕开校验的后门。
    """
    if not is_valid_sid(segment):
        raise ValueError("段 id %r 不合法（规则见 _segments.SID_RULE）" % segment)
    if not isinstance(entry, dict):
        raise ValueError("entry 必须是媒体条目对象")

    ap = os.path.abspath(images_path)
    data = {}
    if os.path.isfile(ap):
        with open(ap, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("images.json 顶层必须是对象 {segment_id: 媒体对象}")
    if segment in data and not force and not dry_run:
        raise ValueError("段 %r 已有条目（src=%r）——要替换请显式加 --force" %
                         (segment, (data[segment] or {}).get("src")
                          if isinstance(data[segment], dict) else data[segment]))
    old = data.get(segment)
    if isinstance(old, dict) and "animate" in old:
        raise ValueError("段 %r 原本挂着 animate，而视频素材只能逐帧播放："
                         "animate 只对 .svg 生效。把标注烘进素材本身，"
                         "或拆成相邻两段，不要替换掉这一段" % segment)

    merged = dict(data)
    merged[segment] = entry
    validate_images_json(merged)
    if dry_run:
        return merged
    out = _guard_output(ap, "images.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, out)
    return merged


def _parser():
    p = argparse.ArgumentParser(
        description="把预渲染视频素材接成合规 images.json 条目（三维 / shader / 网页导览共用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""例：
  python scripts/attach_media.py --images hf-project/images.json \\
      --segment seg2 --src images/tour.mp4 \\
      --media-start 0.4 --loop --fade-in 0.3 --fade-out 0.3

产出的条目（写盘前过 validate_images_json）：
  "seg2": {"src": "images/tour.mp4", "muted": true, "loop": true,
           "media_start": 0.4, "fade_in": 0.3, "fade_out": 0.3}

--dry-run 只打印不写盘。视频段不能挂 animate；契约与边界见 references/formats.md。
""")
    p.add_argument("--images", required=True, help="目标 images.json 路径（不存在则新建）")
    p.add_argument("--segment", required=True, help="真实段落 id，如 seg2")
    p.add_argument("--src", required=True, help="项目内相对路径，指向 .mp4/.webm/.mov/.avi/.mkv")
    p.add_argument("--media-start", type=float, help="从源片第几秒起播（裁掉录制开头的等待）")
    p.add_argument("--fade-in", type=float, help="淡入秒数")
    p.add_argument("--fade-out", type=float, help="淡出秒数")
    p.add_argument("--loop", action="store_true", help="源片比这一段短时循环播放")
    p.add_argument("--unmuted", action="store_true",
                   help="保留视频原声（默认静音；开原声要自己核 volume 与 duck）")
    p.add_argument("--volume", type=float, help="原声增益倍数，1 = 0 dB")
    p.add_argument("--no-duck", action="store_true", help="关掉人声频段让路 EQ")
    p.add_argument("--poster", help="视频未起播时的封面图（项目内相对路径）")
    p.add_argument("--provider", help="素材出处：生成它的工具链")
    p.add_argument("--source-url", help="信源地址或本地快照标识")
    p.add_argument("--attribution", help="署名 / 授权说明")
    p.add_argument("--license", help="素材许可证")
    p.add_argument("--force", action="store_true", help="允许覆盖该段已有条目")
    p.add_argument("--dry-run", action="store_true", help="只打印合并结果，不写盘")
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    opts = {"muted": not args.unmuted}
    for name, key in (("media_start", "media_start"), ("fade_in", "fade_in"),
                      ("fade_out", "fade_out"), ("volume", "volume")):
        val = getattr(args, name)
        if val is not None:
            opts[key] = val
    if args.loop:
        opts["loop"] = True
    if args.no_duck:
        opts["duck"] = False
    for key in _STR_KEYS:
        val = getattr(args, key, None)
        if val:
            opts[key] = val
    try:
        entry = build_video_entry(args.src, **opts)
        merged = attach(args.images, args.segment, entry,
                        force=args.force, dry_run=args.dry_run)
    except (ValueError, OSError) as exc:
        print("[error] %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps({args.segment: merged[args.segment]},
                     ensure_ascii=False, indent=2))
    print("[notice] %s：段 %s 已%s（视频段不写 animate，标注请烘进素材或拆段）" %
          (args.images, args.segment, "预览" if args.dry_run else "写入"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
