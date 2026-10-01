#!/usr/bin/env python3
"""timing_manifest.json → Hyperframes composition HTML（html_renderer 的 CLI 壳）。

画幅/主题选择与 vendor 资产装载链见 SKILL.md 第 5 步与 references/rendering.md；
images.json 字段见 references/image_options.md；完整参数列表见 ``--help``。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request


# GSAP 是 HTML composition 生成阶段唯一必需的本地运行资产；它的获取与安装
# 逻辑直接归属本模块，不再套一层中间抽象。
GSAP_VERSION = "3.14.2"
GSAP_CDN_URL = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"
# 供应链钉固：这个 JS 会被引擎内的浏览器真实执行，CDN 版本又是不可变的，
# 所以把官方 dist 的 sha256 钉死——哈希一致即逐字节相同，是唯一判定口径。
GSAP_SHA256 = "c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280"
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "content-to-video", "vendor")
_CACHE_PATH = os.path.join(_CACHE_DIR, f"gsap-{GSAP_VERSION}.min.js")
_MAX_VENDOR_BYTES = 8 * 1024 * 1024   # 下载响应体上限


def _valid_trusted_gsap(path):
    """该文件的字节是否与钉固的官方 GSAP dist 完全一致（sha256）。"""
    try:
        return os.path.isfile(path) and sha256_file(path) == GSAP_SHA256
    except OSError:
        return False


def _validate_gsap_payload(data):
    """GSAP 下载体校验：供应链钉固——CDN dist 是版本不可变资源，字节必须与
    钉固的 sha256 完全一致（疑似 CDN 污染/代理劫持/错误页时拒绝写缓存）。
    默认下载源只有钉固 CDN 一个，没有"自选来源不套钉固"的豁免分支。"""
    got = hashlib.sha256(data).hexdigest()
    if got != GSAP_SHA256:
        raise ValueError(
            f"GSAP {GSAP_VERSION} 内容与钉固的 sha256 不一致"
            f"（期望 {GSAP_SHA256[:16]}…，实际 {got[:16]}…）；"
            "疑似 CDN 被污染或代理劫持，已拒绝写入缓存")


def _download_to_cache(cache_path, url, *, asset, validate):
    """下载 vendor 资产到用户缓存。

    完整性校验由 validate(data) 注入（失败 raise，资产不落盘）；写入保持
    mkstemp → fsync → os.replace 的原子序，失败清理临时文件。
    """
    cache_dir = os.path.dirname(cache_path)
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "content-to-video"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read(_MAX_VENDOR_BYTES + 1)
        if len(data) > _MAX_VENDOR_BYTES:
            raise ValueError(f"downloaded file too large ({len(data)} bytes)")
        validate(data)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cache_path)
        return True
    except Exception as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        # 诊断必须走 stderr：这是全包唯一一条"下载失败"细节（网络超时/DNS/
        # 校验失败等原因只在这行可见），函数本身仍返回 False 走静默降级路径。
        print(f"[warn] {asset} 本地缓存下载失败：{exc}", file=sys.stderr)
        return False


def _install_vendor(src, dest_path, verify, label):
    """把一份已校验过的源装进项目 vendor/：copy → 复查字节 → os.replace。

    拷贝后必须重算一遍：写盘被截断/磁盘满留下的半截文件若留在 dest，
    下次 ensure_* 的 dest 命中检查会把它当成好资产直接复用。
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(dest_path),
                       "." + os.path.basename(dest_path) + ".tmp")
    try:
        shutil.copyfile(src, tmp)
        if not verify(tmp):
            raise OSError("拷贝后字节校验与钉固值不一致")
        os.replace(tmp, dest_path)
    except (OSError, ValueError) as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        print(f"[warn] 无法安装 {label} 到 {dest_path}: {exc}", file=sys.stderr)
        return False
    return True


def _ensure_vendor(project_dir, dest_rel, cache_path, cdn_url, verify,
                   validate, label):
    """把钉固字节的第三方脚本装进输出项目，返回项目内相对路径（失败 None）。

    取用链与 SKILL.md「环境」GSAP 一段同一条：**输出项目 vendor/ → 用户
    缓存 → 钉固 CDN**，每一级都重算哈希——缓存或项目里被污染、截断的历史副本
    不会被复用，CDN 响应体校验不过就不落盘。技能包不再内置这个 dist：
    二进制副本要求"换版本必须同时改文件名 + 钉固哈希 + README"，
    而下载路径本来就有同一套哈希校验，内置副本只是把同一条链多养一级。

    永不把远程 URL 直接写进 <script src>：渲染必须离线可跑，联网只发生在
    "把字节落到本地"这一步。自定义源不走这条链——CLI --gsap-src
    直接写进 HTML 引用（可信度自负，见 --help），本模块从未有过"下载任意
    URL"的入口。
    """
    project_dir = os.path.abspath(project_dir)
    dest_path = os.path.join(project_dir, *dest_rel.split("/"))
    if verify(dest_path):
        return dest_rel
    if verify(cache_path) and _install_vendor(cache_path, dest_path, verify,
                                              label):
        return dest_rel
    if not _download_to_cache(cache_path, cdn_url, asset=label,
                              validate=validate):
        return None
    if not _install_vendor(cache_path, dest_path, verify, label):
        return None
    return dest_rel


def ensure_local_gsap(project_dir):
    """Ensure ``project_dir/vendor/gsap.min.js`` exists and return its relative path."""
    return _ensure_vendor(project_dir, "vendor/gsap.min.js", _CACHE_PATH,
                          GSAP_CDN_URL, _valid_trusted_gsap,
                          _validate_gsap_payload, "GSAP")


# 主题配色 / 视觉模板 / HTML 组装已拆到独立模块（_theme / _template /
# html_renderer），本文件只留 CLI 与资产、媒体文件的落地校验。
sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _theme import list_theme_names  # noqa: E402
from _template import get_canvas  # noqa: E402
from _contracts import (load_timing_manifest, load_images_json,  # noqa: E402
                        classify_media_path, sids_needing_image, seg_layout,
                        STRUCTURAL_SIDS)
from _script_utils import (setup_stdio, write_text_atomic, sha256_file,  # noqa: E402
                           guard_not_in_skill_dir, is_inside)
from _audio import ffmpeg_usable, get_ffmpeg, measure_duration, parse_duration  # noqa: E402

from html_renderer import (  # noqa: E402
    segment_duration, TEMPLATES_DIR, generate_html,
)


def _file_identical(path_a, path_b):
    """两文件内容是否一致（大小不同直接 False，否则按 1MB 分块哈希比较）。

    音频自动拷贝的去重判定：只比大小会把"同大小不同内容"的旧拷贝误当
    最新音频复用（换稿后 combined.wav 同名同大小是可能的）。
    """
    if not os.path.exists(path_b):
        return False
    if os.path.getsize(path_a) != os.path.getsize(path_b):
        return False
    # 统一使用 _script_utils 的 sha256 内容指纹。
    try:
        return sha256_file(path_a) == sha256_file(path_b)
    except OSError:
        return False


def _stage_audio_file(audio_path, out_dir):
    """把音频变成项目根内的稳定相对路径，并拒绝无效源文件。

    Hyperframes 的资源路径不能越出 HTML 项目根。manifest 通常给绝对路径，
    而显式 ``--audio`` 又可能来自另一个目录；统一在这里处理，避免两条
    分支一条能播、一条静默引用失效路径。软链接指向项目外时也会被复制进来。
    """
    audio_path = os.path.abspath(audio_path)
    out_dir = os.path.abspath(out_dir)
    if not os.path.isfile(audio_path):
        raise SystemExit(f"[error] 音频文件不存在或不可读: {audio_path}")
    if is_inside(audio_path, out_dir):
        return os.path.relpath(audio_path, out_dir).replace("\\", "/")
    audio_dir = os.path.join(out_dir, "audio")
    try:
        os.makedirs(audio_dir, exist_ok=True)
    except OSError as exc:
        raise SystemExit(f"[error] 无法创建音频目录 {audio_dir}: {exc}") from exc
    dst = os.path.join(audio_dir, os.path.basename(audio_path))
    # 目标若是指向项目外的软链接，不能把它当成"同一文件"跳过复制；
    # 用临时文件 + replace 替换链接本身，避免写穿链接目标。
    if (os.path.islink(dst)
            or (os.path.abspath(audio_path) != os.path.abspath(dst)
                and not _file_identical(audio_path, dst))):
        # 临时名用 mkstemp 而非固定的 dst+".tmp"：两个 run 并跑同一项目目录
        # （如双画幅）时固定名会互相踩掉对方的中转文件。
        fd, tmp = tempfile.mkstemp(dir=audio_dir, prefix=".stage-", suffix=".tmp")
        os.close(fd)
        try:
            shutil.copy2(audio_path, tmp)
            os.replace(tmp, dst)
            print(f"[audio] 音频已暂存到项目目录: {dst}", file=sys.stderr)
        except OSError as exc:
            # 磁盘满/源不可读/目标被占用：让调用方拿到 [error] 而不是裸栈，
            # 更不能继续生成引用 audio/ 却无文件的 HTML。
            raise SystemExit(f"[error] 音频复制失败 {audio_path} → {dst}: {exc}") from exc
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return "audio/" + os.path.basename(dst)


def _warn_if_local_vendor_missing(src, out_dir):
    """显式 --gsap-src 给的是本地路径却找不到文件时提示（URL 不判）。

    GSAP 404 不会让生成失败：页面里 `gsap` 未定义，整条时间线建不起来，
    而每个段落卡都带内联 opacity:0——没有 GSAP 把它抬起来，成片是**全黑**
    的无声空片，不是"停在首帧"。这些只在真正打开浏览器或渲染时才暴露。
    显式值允许指到项目外的本地文件，所以只 warn 不 fail。
    """
    if "://" in src or src.lower().startswith("data:"):
        return
    for base in (out_dir, os.getcwd()):
        if os.path.isfile(os.path.join(base, src)):
            return
    if os.path.isfile(src):
        return
    print(f"[warn] 显式传入的 GSAP 脚本路径不存在（{src}，相对 HTML 输出目录 "
          f"{out_dir} 或当前目录都找不到）。HTML 仍会生成，但页面里 gsap 未定义、"
          "整条时间线建不起来，每个段落卡都停在内联 opacity:0——成片为全黑无声空片。"
          "请补上该文件，或去掉这个参数走 vendor/ 默认路径。",
          file=sys.stderr)


def _uncovered_image_sids(manifest, images):
    """manifest 中没有配图映射的『需要配图的段落』sid 列表。

    口径收口在 _contracts.needs_image：内容段一律算，结构性页只在换成整页画布时
    算（agenda 卡纯文字，那一页的 images 键上面就被弹掉了）。遍历封装同样收口在
    _contracts.sids_needing_image，与 run.py _image_coverage 共用一条口径。
    gen_hyperframes 对缺图只提示不拦截（run.py 一键编排有缺图拦截，分步执行保留
    显式 warn，避免把"漏配/没找到合适的图"误当"不需要图"）。
    画布段这里会多报一条 warn，权威判据是下面 canvas_layout_errors 的 error。
    """
    return [sid for sid in sids_needing_image(manifest) if sid not in images]


def validate_images_files(images, out_dir, seg_durs=None):
    """校验 images.json 引用的媒体文件存在且可解码。

    检测项（依赖缺失时优雅降级，只降强度不改行为）：
    - 存在性：所有类型（含 svg）；
    - 图片/视频可解码：ffmpeg 全解码探测（能同时发现 moov 缺失、头部
      损坏与尾部截断——下载中断的典型形态），并解析视频时长，比段落
      长时打"渲染只显示前段"提示（信息级，不阻断）；svg 是文本格式，
      ffmpeg 打不开，存在性校验已足够，跳过。ffmpeg 是渲染必需依赖，
      无需再引入 Pillow。

    返回 (missing, corrupt)：missing=[(sid, media_path)]、
    corrupt=[(sid, media_path, reason)]；相对 src 按 out_dir 解析，
    seg_durs=None 时跳过时长提示。是否 fail-fast 由调用方决定。
    """
    missing_imgs = []
    corrupt_imgs = []
    # 图片/视频完整性都用 ffmpeg 全解码探测（ffmpeg 是渲染必需依赖，
    # 无需再引入 Pillow）；找不到 ffmpeg 时降级为仅做存在性校验。
    # get_ffmpeg() 从不抛异常（末位回退是字面量 "ffmpeg"），所以"能不能用"
    # 只能问 ffmpeg_usable——靠 try/except 接一个不会发生的异常会让下面的
    # 降级警告永远打不出来，而"没有 ffmpeg"恰恰是它唯一要报的情况。
    _ffmpeg_probe = get_ffmpeg()
    if not ffmpeg_usable(_ffmpeg_probe):
        _ffmpeg_probe = None

    def _probe_media_ok(path):
        """ffmpeg 全解码探测，覆盖图片与视频。能同时发现 moov 缺失、
        头部损坏与尾部截断（下载中断的典型形态）。-v info 让 stderr
        携带 Duration 行，成功时顺带解析出视频时长（用于"视频比段落
        长会被截断"提示）。返回 (ok, reason, duration|None)。"""
        import subprocess as _sp
        try:
            r = _sp.run(
                [_ffmpeg_probe, "-v", "info", "-i", path,
                 "-f", "null", "-"],
                capture_output=True, timeout=15)
        except _sp.TimeoutExpired:
            return False, "probe timeout(15s)", None
        except OSError:
            # 兜底：调用方已用 ffmpeg_usable 预筛过，走到这里只剩"预筛之后
            # ffmpeg 才消失"这类竞态。当真报错会把"环境问题"说成"图片损坏"，
            # 所以退回存在性校验（文件存在在调用前已查过），不当损坏处理。
            return True, "", None
        err_text = (r.stderr or b"").decode("utf-8", "replace")
        if r.returncode != 0:
            return False, (err_text.strip()[-160:] or
                           f"exit {r.returncode}"), None
        return True, "", parse_duration(err_text)

    for sid, entry in images.items():
        # 上游 validate_images_json 已保证每条都是媒体对象
        media_type = entry.get("type", "auto")
        media_path = entry.get("src", "")
        # .mp4 路径若被当图片送 ffmpeg 会误报"损坏"，先按扩展名推断再分流
        if media_type == "auto":
            media_type = classify_media_path(media_path)
        # src 已由 validate_images_json 归一成相对项目根路径；即便绝对路径漏进来，
        # os.path.join 遇绝对第二参数直接返回它，下面 is_inside 兜住越界。
        p = os.path.join(out_dir, media_path)
        if not os.path.exists(p):
            missing_imgs.append((sid, media_path))
            continue
        if not is_inside(p, out_dir):
            corrupt_imgs.append((sid, media_path,
                                 "媒体路径通过软链接或绝对路径越出 HTML 项目目录"))
            continue
        poster = entry.get("poster")
        # poster 的问题只记录、不提前 continue：同一条目的 src 若也有毛病，必须
        # 一起报出来（先 continue 会让人以为"只是封面缺了"，修完才发现视频本身
        # 还是坏的，白跑一轮）。
        if poster:
            poster_path = os.path.join(out_dir, poster)
            if not os.path.isfile(poster_path):
                missing_imgs.append((sid, poster))
            elif not is_inside(poster_path, out_dir):
                corrupt_imgs.append((sid, poster,
                                     "poster 路径通过软链接或绝对路径越出 HTML 项目目录"))
        # 视频用 ffmpeg 解码探测（只查存在性不够：
        # 截断/损坏的 mp4 要到渲染时才炸，白烧一整轮渲染时间）
        if media_type == "video":
            if _ffmpeg_probe:
                ok, reason, vdur = _probe_media_ok(p)
                if not ok:
                    corrupt_imgs.append((sid, media_path,
                                         f"视频解码失败: {reason}"))
                elif vdur is not None:
                    seg_dur = (seg_durs or {}).get(sid, 0)
                    if seg_dur > 0 and vdur > seg_dur + 0.05:
                        print(f"[warn] 视频 {vdur:.1f}s 超过段落 "
                              f"'{sid}' 时长 {seg_dur:.1f}s，渲染只显示"
                              f"前 {seg_dur:.1f}s（尾部内容会被截断）。"
                              f"请剪短视频或换到更长的段落。",
                              file=sys.stderr)
            continue
        # svg 是文本格式，ffmpeg 打不开，存在性校验已足够
        if os.path.splitext(p.lower())[1] == ".svg":
            continue
        # 栅格图（jpg/png/webp…）用 ffmpeg 全解码探测损坏/截断——
        # ffmpeg 是渲染必需依赖，无需再引入 Pillow
        if _ffmpeg_probe:
            ok, reason, _dur = _probe_media_ok(p)
            if not ok:
                corrupt_imgs.append((sid, media_path,
                                     f"图片损坏或无法解码: {reason}"))
    if not _ffmpeg_probe and images:
        print("[warn] ffmpeg 不可用，跳过配图完整性（损坏/截断）校验，"
              "仅做了文件存在性校验（ffmpeg 是渲染必需依赖，正常环境"
              "不会出现此降级）", file=sys.stderr)
    return missing_imgs, corrupt_imgs


def _svg_intrinsic_size(path):
    """从 SVG 文件头取固有像素尺寸：优先根标签的 width/height（纯数字，
    带 % / em 的不算像素，直接不认），拿不到再退回 viewBox 的第 3/4 段。
    读不出返回 None（调用方按"没法验"报错，不静默放行）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return None
    tag = re.search(r"<svg\b[^>]*>", head, re.S)
    if not tag:
        return None
    tag = tag.group(0)

    def _px(name):
        m = re.search(rf'\b{name}="\s*([\d.]+)\s*"', tag)
        return float(m.group(1)) if m else None

    sw, sh = _px("width"), _px("height")
    if sw and sh:
        return sw, sh
    vb = re.search(r'\bviewBox="\s*[-\d.]+[,\s]+[-\d.]+[,\s]+([\d.]+)[,\s]+([\d.]+)\s*"', tag)
    if vb:
        w, h = float(vb.group(1)), float(vb.group(2))
        if w and h:
            return w, h
    return None


# 图内文字可读下限（px）：与 references/image_options.md「文字与尺寸」的 26px
# 同一条线，那边是写给画图的约定，这里是门禁端的复读。
CANVAS_MIN_FONT_PX = 26


def canvas_layout_errors(images, segments, out_dir, canvas_w, canvas_h):
    """整页画布（段落 layout: "canvas"）的配图体检，返回 (错误, 警告) 两组说明。

    四件事只有这里查得到（画布版式不生成标题层与句子流层，画面全靠那张图）：
    - 有配图：没图就只剩一条进度条空帧——error。
    - SVG 与当前画幅**等比**：槽位版式里 cover 多裁一点边只是留白变窄；画布
      版式的标题和文字就画在图内，比例一错就被整块裁到画面外。实测把 3:4 的
      画布塞进 16:9 段落，标题与 kicker 直接消失，而 hyperframes check 依旧
      0 error、Contrast 38/38 全过——门禁看不见 SVG 里的字，只能在这里按文件拦。
    - SVG 的**缩放档**：等比不等于能读。按 2560×1440 画的 16:9 塞进 1920 画幅，
      比例对、整张图却被缩到 0.75，图内 26px 折算后只剩 19.5px——低于
      `references/image_options.md`「文字与尺寸」的 26px 下限就是读不出来，
      这条也只有在这里量得到。
    - 文案下落：这一段的 N 句旁白不会出现在画面上，图内的 `<text>` 数量是唯一
      能对上的账——warn，因为"把要点画进图里"本来就没有机械判据。
    照片/视频不验比例与字号（它们没有"画在里面的字"，cover 裁边是正常取景），
    但整段无字是后果，照样给一条 warn 让人知情。
    """
    errs, warns = [], []
    for seg in segments:
        if seg_layout(seg) != "canvas":
            continue
        sid = seg.get("id", "?")
        entry = images.get(sid)
        if not entry:
            errs.append(f"段落 '{sid}' 声明了 layout=\"canvas\"（整页画布），"
                        f"images.json 里却没有它的配图——画布版式不画标题层和"
                        f"句子流层，没有配图就什么都不剩")
            continue
        src = entry.get("src", "")
        path = os.path.join(out_dir, src)
        n_sent = len(seg.get("sentences") or [])
        if os.path.splitext(src.lower())[1] != ".svg":
            warns.append(f"段落 '{sid}' 的整页画布配图是 {src}（不是 SVG）——"
                         f"画布版式不生成标题层与句子流层，照片/视频里也没有"
                         f"图内文字，这一整段画面上不会出现任何文字。"
                         "若这就是要的效果可忽略本条；想留标题就把这一段的要点"
                         "画成 SVG")
            continue
        size = _svg_intrinsic_size(path)
        if size is None:
            errs.append(f"段落 '{sid}' 的画布 {src} 读不出固有尺寸：根标签要有"
                        f"纯数字的 width/height（% 不算）或 viewBox，否则没法验比例")
            continue
        sw, sh = size
        if abs(sw * canvas_h - sh * canvas_w) > 0.002 * canvas_w * canvas_h:
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，与当前画幅 "
                        f"{canvas_w}×{canvas_h} 不等比——object-fit:cover 会把画在"
                        f"图内的标题整块裁到画面外，而 check 看不见这种丢失。"
                        f"按当前画幅重画这张 SVG（两画幅各出一版）")
            continue
        # 等比之后才算缩放：成片把 SVG 按 canvas_w/sw 放大或缩小，图内每个
        # 字号都乘这个系数。只有缩小的方向会跌破可读下限。
        scale = canvas_w / sw
        min_px, n_text = _svg_text_metrics(path)
        if min_px is not None and min_px * scale < CANVAS_MIN_FONT_PX:
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                        f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，图内最小"
                        f"字号 {min_px:g}px 折算后只有 {min_px * scale:.1f}px，低于 "
                        f"{CANVAS_MIN_FONT_PX}px 下限（成片压缩编码后读不出来）。"
                        f"按当前画幅原尺寸重画：竖屏 1080×1440、横屏 1920×1080")
        elif min_px is None and scale < 0.999:
            warns.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                         f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，但文件里"
                         f"读不到 px 字号（写成 class / em / 相对单位了）——"
                         f"折算后是否还够 {CANVAS_MIN_FONT_PX}px 下限没人验得了，"
                         f"按画幅原尺寸画就不用猜")
        warns.append(f"段落 '{sid}' 整页画布：{n_sent} 句旁白不会出现在画面上"
                     f"（标题层与句子流层都不生成），这张 SVG 里有 {n_text} 个"
                     " <text>——请确认要点已经画进图内；句子越少越要确认")
    return errs, warns


def _svg_text_metrics(path):
    """从 SVG 全文取 (最小 px 字号, <text> 个数)，读不到字号时返回 (None, n)。

    字号只认 px：`font-size="26"`、`font-size: 26px`、`font-size="26.5px"` 都算，
    em / % / pt 一律不认——相对单位要乘父级才知绝对值，这里没有布局引擎，硬猜
    会把"能读"和"读不准"混成一类，宁可交回调用方按"没法验"给 warn。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return None, 0
    sizes = [float(m.group(1)) for m in re.finditer(
        r'font-size\s*[=:]\s*["\']?\s*(\d+(?:\.\d+)?)\s*(?:px)?(?![\w.%])', body)]
    return (min(sizes) if sizes else None), len(re.findall(r"<text\b", body))


def main(argv=None):
    """argv=None 走 sys.argv；run.py 进程内直调时传入参数列表，
    参数校验只有本文件这一份 parser，run.py 不再复制。"""
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="Generate Hyperframes composition from timing manifest"
    )
    parser.add_argument("-m", "--manifest", required=True,
                        help="Path to timing_manifest.json")
    parser.add_argument("-o", "--output", required=True,
                        help="Output HTML file path")
    parser.add_argument("--audio", default=None,
                        help="Audio src path in HTML (default: auto-detect from manifest)")
    parser.add_argument("--images", default=None,
                        help="Path to images.json (maps segment ID -> image path relative to HTML)")
    parser.add_argument("--theme", default="dark",
                        choices=list_theme_names(),
                        help="主题配色 (背景/网格/文字/配图底板)，默认 dark（深色科技风："
                             "近黑渐变背景 + 绿色网格线 + 近白字）。可选主题见 "
                             "`_theme.py` 内嵌的主题注册表；如何按内容基调选主题见 "
                             "references/rendering.md「主题」一节")
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏 3:4）或 "
                             "landscape（1920×1080 横屏 16:9）。两画幅共用同一套"
                             "组件版式，数值各取 _template.py 对应块。")
    parser.add_argument("--fps", type=int, default=24,
                        help="输出帧率（写入 HTML 的 data-fps；渲染时可用 --fps 覆盖，"
                             "默认 24；本技能推荐 12/24/30/60——12 仅用于快速预览片，"
                             "Hyperframes 当前支持更广的 FPS 范围）")
    parser.add_argument("--gsap-src", default=None,
                        help="GSAP script URL or local path. 默认取输出项目的 "
                             "vendor/gsap.min.js（须与钉固字节完全一致）；缺失时按"
                             "用户缓存 → 钉固 CDN 的顺序安装（下载体过 sha256 校验"
                             "才落盘）。传显式值（URL 或相对路径）可覆盖，但自定义源"
                             "不做哈希钉固校验，可信度自负。")
    args = parser.parse_args(argv)

    # 产物路径守卫：HTML 与它引用的音频/配图都写进 -o 所在目录，落在技能
    # 目录里会污染仓库（分步执行绕开 run.py 时这道守卫是唯一拦截）
    guard_not_in_skill_dir(("-o/--output 所在目录",
                            os.path.dirname(os.path.abspath(args.output))))
    # HTML 输出目录：vendor/、audio/、images.json 的相对路径都以它为基准，
    # 只在这里推导一次。
    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."

    # Hyperframes 当前支持 1–240；本技能默认/推荐 12/24/30/60（12 只用于
    # 快速预览，见 references/rendering.md），其他整数
    # 不再伪装成上游限制，直接交给同一渲染器。
    if not 1 <= args.fps <= 240:
        parser.error(f"--fps 必须在 1–240 之间，收到: {args.fps}")

    try:
        manifest = load_timing_manifest(args.manifest)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # Load images mapping
    images = {}
    if args.images:
        if os.path.exists(args.images):
            try:
                images = load_images_json(args.images)
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
            # 结构性页默认是纯文字 agenda 卡，不消费配图：images.json 里 agenda
            # 版式的那一两页先弹出并提示（不参与缺图判定）。必须在引用完整性校验
            # 之前——否则指向缺失文件的这两个键会被 fail-fast 误拦，与"忽略并给出
            # [warn]"的文档口径矛盾。声明 layout:"canvas" 的结构性页是例外：整页
            # 画布只有那张图，弹出它就等于把这一页渲染成一帧空背景。
            _segs = manifest["segments"]
            _seg_by_id = {seg.get("id", ""): seg for seg in _segs}
            _agenda_keys = [k for k in STRUCTURAL_SIDS
                            if k in images and k in _seg_by_id
                            and seg_layout(_seg_by_id[k]) == "agenda"]
            if _agenda_keys:
                for k in _agenda_keys:
                    images.pop(k)
                print(f"[warn] images.json 的 {'/'.join(_agenda_keys)} 键被忽略："
                      "该结构性页是纯文字 agenda 版式，不配图；想让开屏/结尾整页出"
                      "海报请写 opening_layout / closing_layout: \"canvas\"，"
                      "否则建议从 images.json 移除上述键（配图文件可一并清理）。",
                      file=sys.stderr)
            # 孤儿键：images.json 里还留着 manifest 中不存在的段 id（稿件
            # 删段/改名后忘了同步）——弹出并 warn，不进渲染器，也不参与
            # 下方缺图判定（指向已删除配图文件的旧键不该阻断本次生成）。
            _known_sids = set(_seg_by_id)
            _orphan_keys = [k for k in images if k not in _known_sids]
            if _orphan_keys:
                for k in _orphan_keys:
                    images.pop(k)
                print(f"[warn] images.json 的 {'/'.join(_orphan_keys)} 键未匹配到 "
                      "manifest 中的任何段落，已忽略：这些段落不存在于当前稿件"
                      "（可能已删段或改过 id），建议连同对应配图文件一并清理。",
                      file=sys.stderr)
            # 引用完整性校验（fail-fast）：images.json 声明的图片若磁盘上不存在，
            # 渲染会静默产出空白裂图——典型场景是改了 images.json 或替换图片改了
            # 扩展名，却忘了重跑本脚本重新生成 HTML。
            # 段落时长映射——视频配图比段落长时渲染只显示前段（尾部被截断），
            # 要在这里就给出提示而不是等成片后才发现。
            # 复用上方 _segs（契约已保证 manifest["segments"] 非空）：段 id 的
            # 唯一来源，与渲染器读的是同一份分组。
            _seg_durs = {seg.get("id", ""): segment_duration(seg)
                         for seg in _segs}
            missing_imgs, corrupt_imgs = validate_images_files(
                images, out_dir, _seg_durs)
            if missing_imgs or corrupt_imgs:
                for sid, rel in missing_imgs:
                    print(f"[error] 配图引用缺失: segment '{sid}' -> "
                          f"'{rel}' 在 {out_dir} 下不存在。", file=sys.stderr)
                for sid, rel, reason in corrupt_imgs:
                    print(f"[error] 配图文件已损坏/无法解码: segment '{sid}' -> "
                          f"'{rel}'（{reason}）。", file=sys.stderr)
                if missing_imgs:
                    print("[error] 请检查 images.json 与实际图片文件是否一致"
                          "（常见原因：改了图片扩展名/替换图片后未重跑 "
                          "gen_hyperframes.py 重新生成 HTML）。", file=sys.stderr)
                if corrupt_imgs:
                    print("[error] 请重新下载/生成对应图片后再重跑本脚本"
                          "（常见原因：下载中途网络中断、磁盘写满导致文件"
                          "截断）。", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"[warn] --images 文件不存在: {args.images}，本次渲染将不带配图"
                  f"（纯文字版兜底）。请确认路径是否正确（示例："
                  f"--images hf-project/images.json）。", file=sys.stderr)

    # 配图覆盖率提示（只提示不阻断）：内容段落（seg…）没有配图映射
    # 时提示。静默无图会让 agent 把"漏配/没找到合适的图"误当"不需要图"——
    # run.py 一键编排会在交付前拦截；分步执行保留显式 warn，提醒调用方补图
    # 或确认纯文字兜底。
    _uncovered = _uncovered_image_sids(manifest, images)
    if _uncovered:
        print(f"[warn] {len(_uncovered)} 个段落没有配图映射: "
              f"{', '.join(_uncovered)}——若是没找到合适的图或漏配，请按第 4 步"
              f"在 A/B/C/D 四条路线中选型补图（见 references/image_options.md）"
              f"后重跑；仅当段落内容性质确实不需要图时才保留无图。", file=sys.stderr)

    # GSAP 取用：显式 --gsap-src 直接写进 HTML 引用（不校验），否则走
    # ensure_local_gsap 的钉固取用链（链路与"永不写远程 URL"见其 docstring）。
    if args.gsap_src:
        gsap_src = args.gsap_src
        _warn_if_local_vendor_missing(gsap_src, out_dir)
    else:
        gsap_src = ensure_local_gsap(out_dir)
        if gsap_src is None:
            raise SystemExit(
                f"[error] 找不到可用的 GSAP：用户缓存（{_CACHE_PATH}）没有合格"
                f"副本，从钉固 CDN 取也失败（原因见上一行 [warn]）。"
                "联网受限的机器上先手动放一份官方 dist 到该缓存路径，"
                "或用 --gsap-src 指向本地已有文件。"
            )

    # 画幅：portrait（默认，1080×1440 竖屏）/ landscape（1920×1080 横屏），
    # 单一画幅出单一 HTML；下游（get_canvas / generate_html）自带
    # portrait→vertical 归一化，画布尺寸随画幅从模板取。
    aspect = args.aspect
    w, h = get_canvas(aspect)

    # 整页画布的配图体检（为什么只能在这里查，见 canvas_layout_errors）
    _canvas_errs, _canvas_warns = canvas_layout_errors(
        images, manifest["segments"], out_dir, w, h)
    for _w in _canvas_warns:
        print(f"[warn] {_w}", file=sys.stderr)
    if _canvas_errs:
        for _e in _canvas_errs:
            print(f"[error] {_e}", file=sys.stderr)
        sys.exit(1)

    if args.audio:
        requested_audio = args.audio
        audio_candidates = ([requested_audio] if os.path.isabs(requested_audio)
                            else [os.path.join(out_dir, requested_audio),
                                  os.path.abspath(requested_audio)])
        audio_path = next((p for p in audio_candidates if os.path.isfile(p)), None)
        if audio_path is None:
            raise SystemExit(
                f"[error] --audio 指定的音频不存在: {requested_audio}\n"
                f"已检查: {', '.join(os.path.abspath(p) for p in audio_candidates)}")
        audio_src = _stage_audio_file(audio_path, out_dir)
    else:
        audio_abs = manifest.get("combined_audio", "")
        if audio_abs:
            audio_candidates = ([audio_abs] if os.path.isabs(audio_abs)
                                else [os.path.join(out_dir, audio_abs),
                                      os.path.abspath(audio_abs),
                                      os.path.join(
                                          os.path.dirname(
                                              os.path.abspath(args.manifest)),
                                          audio_abs)])
            audio_path = next((p for p in audio_candidates if os.path.isfile(p)), None)
        else:
            audio_path = None
        fell_back = audio_path is None
        if audio_path is None:
            default_audio = os.path.join(out_dir, "audio", "combined.wav")
            audio_path = default_audio if os.path.isfile(default_audio) else None
        if audio_path is None:
            raise SystemExit(
                "[error] manifest 未提供可用音频，且默认路径不存在: "
                f"{os.path.join(out_dir, 'audio', 'combined.wav')}。"
                "请重跑 TTS，或显式传入 --audio <文件路径>。")
        if fell_back:
            # 走到这里 = manifest 的音频路径不存在（OUTPUT 被清过/搬走过）
            # 或 manifest 根本没写音频，静默复用项目里上次暂存的
            # audio/combined.wav——新字幕配旧配音会安静烧进成片。
            # 实测时长与 manifest 时间轴对不上就直接拒跑，别赌。
            print(f"[warn] manifest 的 combined_audio 不可用，回落到上次暂存的 "
                  f"{audio_path}——若这是另一条稿子的旧配音，重跑 TTS 或显式 "
                  f"--audio 指定。", file=sys.stderr, flush=True)
            _want = manifest.get("total_duration")
            _got = measure_duration(get_ffmpeg(), audio_path)  # 0.0=测不出
            if (_want and _got
                    and abs(_got - float(_want)) > max(1.0, 0.02 * float(_want))):
                raise SystemExit(
                    f"[error] 回落音频实测 {_got:.2f}s 与 manifest 时间轴 "
                    f"{_want:.2f}s 相差过大——这是另一条时间轴的旧配音，"
                    "拒绝用新字幕烧旧音轨。请重跑 TTS，或显式 --audio。")
        audio_src = _stage_audio_file(audio_path, out_dir)

    html = generate_html(manifest, audio_src,
                         images=images,
                         width=w, height=h, gsap_src=gsap_src,
                         aspect=aspect, theme=args.theme, fps=args.fps)

    # 原子写：index.html 是渲染输入，写到一半被打断会留下半份 HTML——
    # render 会报莫名其妙的语法错，而不是"上次生成中断了，重跑"。
    write_text_atomic(args.output, html)

    # 复制预览脚本到 HTML 输出目录（index.html 引用同目录 preview.js）
    # 路径复用 html_renderer.TEMPLATES_DIR：模板资产的位置只认一处，别在这再拼一遍
    preview_js = str(TEMPLATES_DIR / "preview.js")
    if os.path.isfile(preview_js):
        _dst_preview = os.path.join(out_dir, "preview.js")
        # Windows 上 copy2 会沿用源文件的只读属性，而 copy 到已存在的只读文件
        # 直接 PermissionError：输出目录里的 preview.js 可能被 git 检出或上一次
        # 生成带成只读。两端都 chmod 一次，首次生成与重跑走同一条路径。
        if os.path.exists(_dst_preview):
            os.chmod(_dst_preview, 0o644)
        shutil.copy2(preview_js, _dst_preview)
        os.chmod(_dst_preview, 0o644)
    else:
        print("[warn] 未找到 templates/preview.js，浏览器预览不可用"
              "（渲染不受影响）", file=sys.stderr)

    seg_count = len(manifest["segments"])
    print(f"[OK] {args.output} ({len(html)} bytes)")
    print(f"     Duration: {manifest['total_duration']}s")
    print(f"     Segments: {seg_count}")
    print(f"     Sentences: {len(manifest['sentences'])}")
    print(f"     Aspect: {aspect} ({w}x{h})")
    print(f"     Theme: {args.theme}")
    print(f"     FPS: {args.fps}")
    print(f"     Images: {len(images)}")
    print(f"     Audio src: {audio_src}")
    print(f"     GSAP src: {gsap_src}")


if __name__ == "__main__":
    main()
