#!/usr/bin/env python3
"""timing_manifest.json → Hyperframes composition HTML（html_renderer 的 CLI 壳）。

画幅/主题选择与 vendor 资产装载链见 SKILL.md 第 4 步与 references/rendering.md；
images.json 字段见 references/imagery.md；完整参数列表见 ``--help``。
"""
import argparse
import filecmp
import hashlib
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET


# GSAP 是 HTML composition 生成阶段唯一必需的本地运行资产。
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


def ensure_local_gsap(project_dir):
    """把钉固字节的 GSAP dist 装进输出项目，返回项目内相对路径（失败 None）。

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
    dest_rel = _DEFAULT_GSAP_SRC  # 引用名与 html_renderer 的默认 src 同一份
    dest_path = os.path.join(os.path.abspath(project_dir), "vendor", "gsap.min.js")
    if _valid_trusted_gsap(dest_path):
        return dest_rel
    if _valid_trusted_gsap(_CACHE_PATH) and _install_vendor(
            _CACHE_PATH, dest_path, _valid_trusted_gsap, "GSAP"):
        return dest_rel
    if not _download_to_cache(_CACHE_PATH, GSAP_CDN_URL, asset="GSAP",
                              validate=_validate_gsap_payload):
        return None
    if not _install_vendor(_CACHE_PATH, dest_path, _valid_trusted_gsap, "GSAP"):
        return None
    return dest_rel


sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _template import (MIN_READABLE_FONT_PX, get_canvas, get_image_box,  # noqa: E402
                       load_template, same_aspect)
from _manifest_schema import load_timing_manifest  # noqa: E402
from _images_schema import (load_images_json, classify_media_path,  # noqa: E402
                            is_svg_path)
from _segments import (sids_needing_image, seg_layout,  # noqa: E402
                       STRUCTURAL_SIDS)
from _script_utils import (setup_stdio, write_text_atomic, sha256_file,  # noqa: E402
                           is_inside, guard_not_in_skill_dir)
from _audio import (ffmpeg_usable, get_ffmpeg, measure_duration,  # noqa: E402
                    parse_duration, parse_video_size, COMBINED_AUDIO_NAME)
from _media_size import raster_size, upscale_note  # noqa: E402
from _svg_sanitize import (_CSS_COMMENT, _local, _parse_num, _style_map,  # noqa: E402
                           is_full_bleed_rect, sanitize_svg_for_inline)
from _cam_crop import crop_warnings  # noqa: E402
from _stage_carry import bake_settled_state, global_ref_leaks  # noqa: E402
from _timeline import (DEFAULT_FPS, beat_positions, beat_span,  # noqa: E402
                       beat_cycles, duration_tolerance)

from html_renderer import (  # noqa: E402
    segment_duration, TEMPLATES_DIR, generate_html, _DEFAULT_GSAP_SRC,
)


def _file_identical(path_a, path_b):
    """两文件内容是否一致；目标缺失/不可读按不一致处理（触发重新搬运）。

    音频自动拷贝的去重判定：只比大小会把"同大小不同内容"的旧拷贝误当
    最新音频复用（换稿后 combined.wav 同名同大小是可能的）。
    """
    if not os.path.exists(path_b):
        return False
    try:
        return filecmp.cmp(path_a, path_b, shallow=False)
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
    if os.path.islink(dst) or not _file_identical(audio_path, dst):
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

    查找基准只有 HTML 输出目录：`<script src="…">` 由浏览器从 index.html 所在
    目录解析。旧实现把运行命令时的 CWD 也当合法基准，于是"文件恰好在 CWD 下"
    时这条守卫闭嘴，而 out_dir 里并没有它——假守卫承诺了"文件在"，成片却是全黑
    无声空片，比不提示更难查。绝对路径由 os.path.join 直接返回它，一并覆盖。
    """
    if "://" in src or src.lower().startswith("data:"):
        return
    if os.path.isfile(os.path.join(out_dir, src)):
        return
    print(f"[warn] 显式传入的 GSAP 脚本路径不存在（{src}，相对 HTML 输出目录 "
          f"{out_dir} 找不到）。HTML 仍会生成，但页面里 gsap 未定义、"
          "整条时间线建不起来，每个段落卡都停在内联 opacity:0——成片为全黑无声空片。"
          "请补上该文件，或去掉这个参数走 vendor/ 默认路径。",
          file=sys.stderr)


def _warn_if_upscaled(sid, media_path, box, size):
    """素材铺进它那一页的框要被放大时打一条 `[warn]`（只提示，不拦）。

    这一问此前没人管：配图一律 `cover` 铺满画布，而 `cover` 只管填满、不管清
    楚——一张不够大的图会被悄悄放大成糊的，生成期、`hyperframes check`、渲染
    日志全都不出声，只有成片里看得见。所以这里拿固有尺寸跟框比一次。

    不拦是因为"糊不糊"还有主观成分（一张柔和的渐变背景放大 1.4 倍没人看得出来），
    把整轮生成拦在半路不值当；要的是知情 + 可执行去处，判据与阈值在 `_media_size`。
    box 或 size 缺失（不知道框多大 / 读不出尺寸）就静默——没得比就不比，
    拿猜的数报警比不报更糟。
    """
    note = upscale_note(media_path, size, box)
    if note:
        print(f"[warn] 段落 '{sid}' 的配图不够大：{note}", file=sys.stderr)


def validate_images_files(images, out_dir, seg_durs=None, seg_boxes=None):
    """校验 images.json 引用的媒体文件存在且可解码。

    检测项（依赖缺失时优雅降级，只降强度不改行为）：
    - 存在性：所有类型（含 svg）；
    - 图片/视频可解码：ffmpeg 全解码探测（能同时发现 moov 缺失、头部
      损坏与尾部截断——下载中断的典型形态），并解析视频时长，比段落
      长时打"渲染只显示前段"提示（信息级，不阻断）；svg 是文本格式，
      ffmpeg 打不开，存在性校验已足够，跳过。ffmpeg 是渲染必需依赖，
      无需再引入 Pillow。
    - **像素够不够铺满**：栅格图读文件头、视频读 ffmpeg 的流信息，拿固有
      尺寸跟它要被 `cover` 铺进的那个框比——放大超过阈值就打一条 `[warn]`
      点名哪一段、素材多大、要放大几倍。这是此前零报错的一类静默退化：
      糊掉的那一帧在生成期、在 `hyperframes check` 里、在渲染日志里都不
      存在，只有成片看得见。判据与阈值在 `_media_size`。svg 不验（矢量）。

    返回 (missing, corrupt)：missing=[(sid, media_path)]、
    corrupt=[(sid, media_path, reason)]；相对 src 按 out_dir 解析，
    seg_durs=None 时跳过时长提示、seg_boxes=None 时跳过尺寸提示。
    是否 fail-fast 由调用方决定（尺寸这一项只提示，永远不拦）。
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
        携带 Duration 行与流信息，成功时顺带解析出视频时长（用于"视频
        比段落长会被截断"提示）与像素尺寸（用于"够不够铺满"提示）。
        返回 (ok, reason, duration|None, size|None)。"""
        import subprocess as _sp
        try:
            r = _sp.run(
                [_ffmpeg_probe, "-v", "info", "-i", path,
                 "-f", "null", "-"],
                capture_output=True, timeout=15)
        except _sp.TimeoutExpired:
            return False, "probe timeout(15s)", None, None
        except OSError:
            # 兜底：调用方已用 ffmpeg_usable 预筛过，走到这里只剩"预筛之后
            # ffmpeg 才消失"这类竞态。当真报错会把"环境问题"说成"图片损坏"，
            # 所以退回存在性校验（文件存在在调用前已查过），不当损坏处理。
            return True, "", None, None
        err_text = (r.stderr or b"").decode("utf-8", "replace")
        if r.returncode != 0:
            return False, (err_text.strip()[-160:] or
                           f"exit {r.returncode}"), None, None
        return True, "", parse_duration(err_text), parse_video_size(err_text)

    for sid, entry in images.items():
        # 上游 validate_images_json 已保证每条都是媒体对象、src 必填非空
        media_type = entry.get("type", "auto")
        media_path = entry["src"]
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
            _size = None
            if _ffmpeg_probe:
                ok, reason, vdur, _size = _probe_media_ok(p)
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
            _warn_if_upscaled(sid, media_path, (seg_boxes or {}).get(sid), _size)
            continue
        # svg 是文本格式，ffmpeg 打不开，存在性校验已足够；矢量也不验尺寸
        # （缩放不失真，它另有比例与字号两道门禁）
        if is_svg_path(p):
            continue
        # 栅格图（jpg/png/webp/gif…）用 ffmpeg 全解码探测损坏/截断——
        # ffmpeg 是渲染必需依赖，无需再引入 Pillow
        # 尺寸只在"知道框多大"时才读：没框就不比，也就不用为它开一次文件
        _size = raster_size(p) if (seg_boxes or {}).get(sid) else None
        if _ffmpeg_probe:
            ok, reason, _dur, _probe_size = _probe_media_ok(p)
            if not ok:
                corrupt_imgs.append((sid, media_path,
                                     f"图片损坏或无法解码: {reason}"))
            # 头解析读不出的格式（avif/heic 之类）退回 ffmpeg 的流信息
            _size = _size or _probe_size
        _warn_if_upscaled(sid, media_path, (seg_boxes or {}).get(sid), _size)
    if not _ffmpeg_probe and images:
        print("[warn] ffmpeg 不可用，跳过配图完整性（损坏/截断）校验，"
              "仅做了文件存在性校验（ffmpeg 是渲染必需依赖，正常环境"
              "不会出现此降级）", file=sys.stderr)
    return missing_imgs, corrupt_imgs


def _svg_intrinsic_size(path):
    """从 SVG 根标签取固有像素尺寸（判据与 check_svg 同一份：_root_px_size）。
    读不出/解析失败返回 None（调用方按"没法验"报错，不静默放行）。

    按 ElementTree 解析而不是读文件头正则：注释里放一句 `<svg width="10" …>`
    曾让正则版把 10×10 当成固有尺寸（实测），比例校验于是对着一个假尺寸开灯；
    根标签落在头 8192 字节之外时（大注释/前置处理指令）又只能返 None 误报
    "读不出"。结构化解析让注释与位置都无从作伪，顺带与 _root_px_size 收成
    同一份实现（判据只留一处）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            root = ET.fromstring(f.read())
    except (OSError, ET.ParseError):
        return None
    if _local(root.tag) != "svg":
        return None
    return _root_px_size(root)


def slot_svg_ratio_errors(images, segments, out_dir, box_w, box_h):
    """槽位版式配图里 SVG 的比例失配，返回一条说明（无错时为空列表）。

    为什么槽位版式也要查：槽位框是 4:3、画幅是 3:4，SVG 塞进去走 cover。
    比例对不上时 cover 会**切掉边**去填满框——图里画在边缘上的文字就跟着消失，
    而作者拿到的 HTML 里它还在。这与画布版式那条同源（都问"图内的字会不会被
    cover 吃掉"），只是槽位版的判据尺寸不同：画布版式对画幅（1080×1440），
    槽位版对槽位框（980×735）。

    实测的四例（1080×1440 整页画布 / 490×368 小图 / 800×800 方图 / 1600×600
    横图铺进 980×735 框）说明失配的代价是**裁边**不是"撑破画幅"——槽位框本身
    有框高限制，撑不破；所以别拿"画面溢出"当这里的症状说，那是错的诊断。

    `hyperframes check` 对这类失配只报 container_overflow（元素超出裁切容器），
    读者看不出该改根节点尺寸还是改内容；所以按文件在这里拦，并直接给出该画
    幅的槽位尺寸。

    栅格图（照片/视频）不查比例：那张图里没有"画在里面的字"，比例失配只是
    取景裁边，ImageGen 素材那条路本来就是靠 cover 取景的（见 imagery.md）。
    """
    errs = []
    for seg in segments:
        if seg_layout(seg) == "canvas":
            continue          # 画布版式由 canvas_layout_errors 按画幅判，判据不同
        sid = seg["id"]
        entry = images.get(sid)
        if not entry:
            continue          # 缺配图另有出处（覆盖率提示 / 引用完整性校验）
        src = entry["src"]
        if not is_svg_path(src):
            continue
        size = _svg_intrinsic_size(os.path.join(out_dir, src))
        if size is None:
            errs.append(f"段落 '{sid}' 的配图 {src} 读不出固有尺寸：根标签要有"
                        f"纯数字的 width/height（% 不算）或 viewBox，否则没法验比例")
            continue
        sw, sh = size
        # 判据走 _template.same_aspect：check_svg 的自查与这里的生成期门禁是同一
        # 个问题（"cover 会不会裁掉贴边的字"），容差只有一份（SVG_ASPECT_TOL）。
        if not same_aspect(sw, sh, (box_w, box_h)):
            errs.append(
                f"段落 '{sid}' 的配图 {src} 是 {sw:g}×{sh:g}，与槽位框 "
                f"{box_w}×{box_h}（4:3）不等比——object-fit:cover 会切掉边去填满"
                f"这个框，画在图边缘上的字跟着消失（宽高各差 "
                f"{abs(sw / box_w - 1) * 100:.0f}% / "
                f"{abs(sh / box_h - 1) * 100:.0f}%）。hyperframes check 对这种"
                f"失配只说元素超出容器，看不出该改哪。按 {box_w}×{box_h} 重画"
                f"这张 SVG")
    return errs


def svg_background_warnings(images, segments, out_dir):
    """满幅背景 <rect> 的生成期体检：槽位与整页画布的所有 SVG 配图都查。

    SVG 应当**透明**、让页面渐变与模板三层透出来（references/canvas.md
    「不铺满幅底」），但设计/生图工具常给文件带一张铺满 viewBox 的底色 rect——
    槽位版式会在页面上形成一圈色差框，整页画布把模板三层整个盖掉退回一片死平。
    `check_svg.py` 只在作者主动自查时跑；这一遍保证不跑自查、直接 `gen_hyperframes`
    也会按段落出声。判据与 check_svg 共用 `_svg_sanitize.is_full_bleed_rect`
    （单一真源）。warn 不 error：确实要自画满幅底（浅色页 / 照片感场景）是合法
    取舍，有意为之可忽略。同一文件被 keep 链里多个段落引用时按路径只报一次。
    """
    seen = set()
    warns = []
    for seg in segments:
        sid = seg["id"]
        entry = images.get(sid)
        if not entry or not is_svg_path(entry["src"]):
            continue
        path = os.path.join(out_dir, entry["src"])
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        if _local(root.tag) != "svg":
            continue
        size = _root_px_size(root)
        if size is None:
            continue
        vb_w, vb_h = size
        rects = [el for el in root.iter()
                 if is_full_bleed_rect(el, vb_w, vb_h)]
        if not rects:
            continue
        if seg_layout(seg) == "canvas":
            warns.append(
                f"段落 '{sid}' 的整页画布配图 {entry['src']} 铺了一张满幅底板 "
                f"<rect>：模板本来就在这一页底下画好了三层（主题渐变 / 只在页缘"
                f"显形的网格 / 本段 accent 氛围光），满幅 rect 把三层整个盖掉，"
                f"画布页就退回一片死平。删掉它让三层透出来（见 "
                f"references/canvas.md「不铺满幅底」）。确实要把整页换成另一种"
                f"底色时可忽略本条")
        else:
            warns.append(
                f"段落 '{sid}' 的配图 {entry['src']} 带一张铺满 viewBox 的背景 "
                f"<rect>——槽位 SVG 不要铺满幅底（会在页面上形成一圈色差框）；"
                f"SVG 应保持透明，让页面渐变与该段 accent 氛围光透出来（见 "
                f"references/canvas.md「不铺满幅底」）")
    return warns


def _root_px_size(root):
    """根标签的固有像素尺寸：(宽, 高)。优先纯数字 width/height（带 % 的不认），
    拿不到退回 viewBox 第 3/4 段；都读不出返回 None。"""
    w, h = _parse_num(root.get("width")), _parse_num(root.get("height"))
    if w and h:
        return w, h
    vb = re.split(r"[,\s]+", (root.get("viewBox") or "").strip())
    if len(vb) == 4:
        try:
            vw, vh = float(vb[2]), float(vb[3])
        except ValueError:
            return None
        if vw and vh:
            return vw, vh
    return None


_FRAME0_HIDDEN_TAGS = frozenset({"defs", "title", "desc", "metadata", "style"})


def _is_zero_opacity(v):
    """静态 opacity 值是否等于 0（"0" / "0.0" / "0e0" 都算）。"""
    if v is None:
        return False
    m = re.match(r"^\s*([-+]?\d*\.?\d+)\s*$", str(v).strip())
    return bool(m) and float(m.group(1)) == 0.0


def _svg_frame0_visibility(raw):
    """静态 SVG 第 0 帧的可见性账 → (可见渲染元素数, 渲染元素总数)。

    导演档的内容元素按约定以 `opacity=0` 起步、逐拍点亮；结构底不写 opacity、
    和页面同时到位。若**所有**可渲染元素（`<defs>` 之外、非 title/desc/
    metadata/style）都从隐藏起步（自身或任一祖先 `opacity=0` / `visibility:hidden`
    / `display:none`），这一页开头就是整片空白。visibility 本可被子元素覆盖，
    保守取"任一层隐藏即隐藏"——宁可多报一条，也不让空白开场静默出货。
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return 0, 0
    if _local(root.tag) != "svg":
        return 0, 0
    parent = {c: p for p in root.iter() for c in p}
    # <defs> 子树整体不参与：它只被 <use> 间接引用，本身不渲染。
    defs_inside = set()
    for el in root.iter():
        cur = parent.get(el)
        while cur is not None:
            if _local(cur.tag) == "defs":
                defs_inside.add(el)
                break
            cur = parent.get(cur)
    n_vis = n_all = 0
    for el in root.iter():
        if el is root or el in defs_inside or _local(el.tag) in _FRAME0_HIDDEN_TAGS:
            continue
        n_all += 1
        hidden = False
        cur = el
        while cur is not None:
            sm = _style_map(cur)
            if (_is_zero_opacity(sm.get("opacity", cur.get("opacity")))
                    or (sm.get("visibility", cur.get("visibility")) or "").strip().lower()
                    in ("hidden", "collapse")
                    or (sm.get("display", cur.get("display")) or "").strip().lower()
                    == "none"):
                hidden = True
                break
            cur = parent.get(cur)
        if not hidden:
            n_vis += 1
    return n_vis, n_all


def canvas_layout_errors(images, segments, out_dir, canvas_w, canvas_h):
    """整页画布（段落 layout: "canvas"）的配图体检，返回 (错误, 警告) 两组说明。

    四件事只有这里查得到（画布版式不生成标题层与句子流层，画面全靠那张图）：
    - 有配图：没图就只剩一条进度条空帧——error。
    - SVG 与当前画幅**等比**：比例一错，cover 就要切边去填满框，画在图边缘的
      字（画布版式的标题/kicker、槽位版式的边注）随之消失。实测把 3:4 的画布
      塞进 16:9 段落，标题与 kicker 直接消失，而 hyperframes check 依旧
      0 error、Contrast 全过——门禁看不见 SVG 里的字，只能在这里按文件拦。
      槽位版式的同类失配走 slot_svg_ratio_errors（那边对的是槽位框尺寸）。
    - SVG 的**缩放档**：等比不等于能读。按 2560×1440 画的 16:9 塞进 1920 画幅，
      比例对、整张图却被缩到 0.75，图内 26px 折算后只剩 19.5px——低于
      `references/canvas.md`「文字与尺寸」的 26px 下限就是读不出来，
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
        # id/src/sentences 都由契约把守（validate_timing_manifest、
        # validate_images_json），消费端直接下标，不再留永不可达的兜底
        sid = seg["id"]
        entry = images.get(sid)
        if not entry:
            errs.append(f"段落 '{sid}' 声明了 layout=\"canvas\"（整页画布），"
                        f"images.json 里却没有它的配图——画布版式不画标题层和"
                        f"句子流层，没有配图就什么都不剩")
            continue
        src = entry["src"]
        path = os.path.join(out_dir, src)
        n_sent = len(seg["sentences"])
        if not is_svg_path(src):
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
        if not same_aspect(sw, sh, (canvas_w, canvas_h)):
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，与当前画幅 "
                        f"{canvas_w}×{canvas_h} 不等比——object-fit:cover 会把画在"
                        f"图内的标题整块裁到画面外，而 check 看不见这种丢失。"
                        f"按当前画幅重画这张 SVG（两画幅各出一版）")
            continue
        # 等比之后才算缩放：成片把 SVG 按 canvas_w/sw 放大或缩小，图内每个
        # 字号都乘这个系数。只有缩小的方向会跌破可读下限。
        scale = canvas_w / sw
        min_px, n_text = _svg_text_metrics(path)
        if min_px is not None and min_px * scale < MIN_READABLE_FONT_PX:
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                        f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，图内最小"
                        f"字号 {min_px:g}px 折算后只有 {min_px * scale:.1f}px，低于 "
                        f"{MIN_READABLE_FONT_PX}px 下限（成片压缩编码后读不出来）。"
                        f"按当前画幅原尺寸重画：竖屏 1080×1440、横屏 1920×1080")
        elif min_px is None and scale < 0.999:
            warns.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                         f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，但文件里"
                         f"读不到 px 字号（写成 class / em / 相对单位了）——"
                         f"折算后是否还够 {MIN_READABLE_FONT_PX}px 下限没人验得了，"
                         f"按画幅原尺寸画就不用猜")
        warns.append(f"段落 '{sid}' 整页画布：{n_sent} 句旁白不会出现在画面上"
                     f"（标题层与句子流层都不生成），这张 SVG 里有 {n_text} 个"
                     " <text>——请确认要点已经画进图内；句子越少越要确认")
    return errs, warns


def _svg_selectable_tokens(raw):
    """SVG 里可被 director target 命中的选择器集合：{'#id', ..., '.class', ...}。

    target 白名单允许 id 或 class（class 专为 stagger 一组元素开），存在性校验就得
    两类都收：一个 class 属性可能写多个空格分隔的类名，逐个拆进去。净化器保留 class
    （只摘 on*/外链 href），所以这里从源文本收的类名和内联后 DOM 上的一致。
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return set()
    toks = set()
    for el in root.iter():
        eid = el.get("id")
        if eid:
            toks.add("#" + eid)
        cls = el.get("class")
        if cls:
            for c in cls.split():
                toks.add("." + c)
    return toks


# ── 导演节拍 ↔ 旁白对轴───────────────────────────────────────────
# 落点解析在 _timeline.beat_positions（与渲染端同一函数），这里只负责"把落点
# 说成人话"：门禁要的窗外判定、以及 --beat-report 要的每拍落句表。之所以放在
# 生成期而不是 check_svg：判据需要 manifest 的句子时间，而 check_svg 只看单个 SVG。

_BEAT_EPS = 0.005   # 落点已与时间轴同精度（round 2 位），这里只吸收浮点尾巴


def _director_default_duration():
    """模板 animation.director.duration——没写 duration 的 step 用它，相对链游标也用它。
    必须与渲染端取的是同一份，否则门禁算出的落点不是渲染器真正用的时刻。"""
    return float(load_template()["animation"]["director"]["duration"])


def _beat_anchor(step):
    """这一步的锚点写法原样打印，供 warn/报告指认是哪一步。"""
    if "at_time" in step:
        return "at_time=%s" % (step["at_time"],)
    return "at=%s" % (step["at"],)


def _beat_landing(pos, sentences, seg_start, seg_end, next_start):
    """beat 落在旁白的哪儿：返回 (类别, 说明)。before/after 两类就是门禁要报的"窗外"。

    段尾（末句说完到下一页盖过来之间）**不算窗外**：这一页还挂在屏幕上，落在那里的
    补间照样演。所以右界取"下一段起点"而不是"末句结束"——只有过了它才真的看不见。
    """
    for i, s in enumerate(sentences):
        st = float(s["start_time"])
        if st - _BEAT_EPS <= pos <= st + float(s.get("duration", 0.0)) + _BEAT_EPS:
            return "in", "第%d句" % (i + 1)
    if pos < seg_start - _BEAT_EPS:
        return "before", "本段旁白之前（看不见）"
    if next_start is not None and pos > next_start + _BEAT_EPS:
        return "after", "下一页起点之后（看不见）"
    if pos > seg_end + _BEAT_EPS:
        return "tail", "段尾静音（页面仍在）"
    for i in range(len(sentences) - 1):
        a = float(sentences[i]["start_time"]) + float(sentences[i].get("duration", 0.0))
        b = float(sentences[i + 1]["start_time"])
        if a < pos < b:
            return "gap", "句间静音（第%d句末 %.2f→第%d句起 %.2f）" % (i + 1, a, i + 2, b)
    return "gap", "静音"


def _stagger_extra(step):
    """这一拍写了 stagger 时，收尾**至少**还要往后推多少秒。

    GSAP 的 stagger 是"一组元素逐个错开"：`stagger: 0.1` 命中 N 个元素，最后一个的
    起点就是 pos+0.1×(N-1)，整拍收尾也跟着后移。N 只有渲染端展开 DOM 才知道，这里
    算不出精确值——但下界是确定的：**只要有第二个元素，就至少多一个间隔**。出窗判定
    用下界，宁可早报也不能漏报。

    `amount` 是 GSAP 的另一套写法（总分摊时长，与元素数无关），它是精确值，直接用。
    """
    st = step.get("stagger")
    if isinstance(st, bool) or st is None:
        return 0.0
    if isinstance(st, (int, float)):
        return max(float(st), 0.0)
    if isinstance(st, dict):
        v = st.get("amount", st.get("each"))
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return 0.0
        return max(float(v), 0.0)
    return 0.0


def _beat_window_warnings(steps, sentences, seg_start, next_start, dflt_dur, keep=False):
    """beat 落在本页可见窗口之外（before/after/被切半路）→ warn 文案。

    引擎按 data-start/data-duration 硬切 clip 可见性（实测排在窗口外的补间等于没写），
    所以这类落点不是"稍微偏"，是那一拍在成片里**根本不演**。warn 不 error：落点是作者
    写的，人可能就是要它压在下一页上。最后一段之后没有页盖过来，页面一直挂着，所以
    next_start 为 None 时不设右界（宁漏不误报）。解析与渲染端共用 beat_positions，
    门禁算的时刻与渲染器用的时刻不可能漂移。

    除了起点出窗，还报**起点在内、终点越界**的那一类：补间演到一半就被下一页盖过来，
    作者以为的终态在成片里从没出现过（`keep=True` 时更要紧——接续烘焙把时间轴折到最后
    一帧，它搬的正是那个没演到的终态）。这里只判时刻，不去算缓动走了几成：那要把 ease
    求值到页界，而"演到一半"本来就是该改节拍而不是该被精确复刻的东西。
    """
    try:
        beats = beat_positions(steps, sentences, round(seg_start, 2), dflt_dur)
    except ValueError:
        return []   # at 越界由 director_prepare 的句序检查按步报错，这里不重复
    seg_end = float(sentences[-1]["start_time"]) + float(sentences[-1].get("duration", 0.0))
    out = []
    for i, ((pos, dur), step) in enumerate(zip(beats, steps)):
        kind, _where = _beat_landing(pos, sentences, seg_start, seg_end, next_start)
        anchor = _beat_anchor(step)
        if kind == "before":
            out.append(f"director.steps[{i}]（{step['target']}，{anchor}）落点 {pos:.2f}s "
                       f"比本段旁白起点 {seg_start:.2f}s 还早 {seg_start - pos:.2f}s——这一页"
                       "那时还没出现，补间排在 clip 窗口外等于没演")
        elif kind == "after":
            out.append(f"director.steps[{i}]（{step['target']}，{anchor}）落点 {pos:.2f}s 已过"
                       f"下一段旁白起点 {next_start:.2f}s——这一页那时已被下一页盖住，等于没演。"
                       "多半是 at_time 按旧配音手算后过期了：跟旁白的节拍改用 at（句序锚），"
                       "或把秒数调小")
        elif next_start is not None:
            # 跨度按 beat_span 算：写了 repeat 的这一拍要演好几遍，只按单遍 duration 判
            # 就是四倍误差——"演到一半被盖过来"的门禁必须拿成片里真正的那个大括号。
            # repeat:-1 没有终点，也不报：它本来就要被页界切掉，报它是训练作者忽略 warn。
            # stagger 另加：一组元素逐个错开，最后那个的收尾比 span 更晚（见
            # _stagger_extra——拿不到元素数，按"至少多一个间隔"的下界算）。
            span = beat_span(step, dur)
            extra = _stagger_extra(step)
            total = None if span is None else span + extra
            if total is not None and pos + total > next_start + _BEAT_EPS:
                n = beat_cycles(step)
                again = f"（演 {n} 遍、共 {span:.2f}s）" if n and n > 1 else ""
                staggered = (f"，stagger 还要再推 {extra:.2f}s（收到 {pos + total:.2f}s）"
                             if extra else "")
                out.append(f"director.steps[{i}]（{step['target']}，{anchor}）从 {pos:.2f}s 演到 "
                           f"{pos + span:.2f}s 才完{again}{staggered}，而下一段旁白 {next_start:.2f}s 就把这一页盖"
                           f"过来——这一拍被切在半路，它要收的那个尾在成片里从没出现过（早 "
                           f"{pos + total - next_start:.2f}"
                           "s）。要么把 duration/delay/repeat 收紧到本页内，要么让它在接续页里重演一遍"
                           + ("；这一页写了 stage:\"keep\"，接续烘焙搬走的就是那个没演到的终态"
                              if keep else ""))
    return out


def _zorder_warnings(markup, steps):
    """GSAP 揭示顺序 ↔ SVG 叠压顺序的自证（Anatomy 的"次序规则"）。

    导演的 reveal 顺序就是作者声明的意图：后亮的元素应当叠在先亮的上面——"现在看
    这里"要浮在已经可见的内容之上，垫在旧内容下面的是结构底，不是新内容。SVG 没有
    z-index，叠压顺序 = DOM 顺序（后写者在上）。于是机械核对：凡两个**都被步骤揭示**
    的内容元素、且包围盒相交，若"先亮的反而写在 DOM 后面"，就是揭示顺序与叠压顺序
    相反——后亮的那个元素在它最该显眼的那一刻被压在旧内容底下。warn 不 error：包围
    盒相交是保守近似（不相交也可能同盒），且"把新内容刻意垫在旧内容下面"的取舍存在
    （如提示框垫在主体底下）。只查步骤揭示过的元素：结构底没有揭示顺序，不参与；
    class 选择器可命中多个元素，判据要单元素 id 才成立，跳过。
    """
    from check_svg import _shape_bounds
    order = {}                       # target id → 最早揭示的步骤下标
    for i, step in enumerate(steps):
        t = (step.get("target") or "").strip()
        if t.startswith("#") and len(t) > 1 and t[1:] not in order:
            order[t[1:]] = i
    if len(order) < 2:
        return []
    try:
        root = ET.fromstring(markup)
    except ET.ParseError:
        return []
    vb_w = vb_h = None
    parts = (root.get("viewBox") or "").replace(",", " ").split()
    if len(parts) == 4:
        try:
            vb_w, vb_h = float(parts[2]), float(parts[3])
        except ValueError:
            pass
    parent = {c: p for p in root.iter() for c in p}
    dom = []
    for el in root.iter():
        if el.get("id"):
            dom.append(el)
    pos = {el.get("id"): j for j, el in enumerate(dom)}
    ids = [i for i in order if i in pos]
    by_id = {el.get("id"): el for el in dom}

    def bounds_of(eid):
        el = by_id.get(eid)
        if el is None:
            return None
        cur = el
        while cur is not None:
            if cur.get("transform"):
                return None      # 静态算不出 transform 后的最终位置，跳过
            cur = parent.get(cur)
        tag = el.tag.rsplit("}", 1)[-1]
        return _shape_bounds(el, tag, vb_w, vb_h)

    warns = []
    for k in range(len(ids)):
        for j in range(k + 1, len(ids)):
            a, b = ids[k], ids[j]
            if order[a] == order[b]:
                continue          # 同一拍揭示，没有先后可言
            ba, bb = bounds_of(a), bounds_of(b)
            if not ba or not bb:
                continue
            if ba[2] <= bb[0] or bb[2] <= ba[0] or ba[3] <= bb[1] or bb[3] <= ba[1]:
                continue          # 包围盒不相交：叠压顺序无从生效
            earlier, later = (a, b) if order[a] < order[b] else (b, a)
            if pos[earlier] > pos[later]:
                warns.append(
                    f"z 序与揭示顺序相反：#{earlier} 在第 {order[earlier]} 步先亮，"
                    f"却写在 #{later}（第 {order[later]} 步后亮）的 DOM 后面——两者在"
                    f"画面上重叠，先亮的会把后亮的盖住。SVG 的叠压顺序 = DOM 顺序，"
                    f"后亮的内容要写在后面（上面）；把 #{earlier} 挪到 #{later} 前面，"
                    f"或确认这是故意垫底")
    return warns


def next_speech_start(segments, sid):
    """紧邻其后的那一段旁白起点（绝对秒）；没有下一段返回 None。

    按 start_time 取"比本段晚的最近一段"，不靠列表相邻：manifest 的段序就是时间序，
    但孤儿配图/结构段可能让 sid 的邻居不是时间上的下一页。
    """
    mine = None
    for seg in segments:
        if seg["id"] == sid:
            mine = float(seg["sentences"][0]["start_time"])
            break
    if mine is None:
        return None
    cands = [float(s["sentences"][0]["start_time"]) for s in segments
             if float(s["sentences"][0]["start_time"]) > mine + _BEAT_EPS]
    return min(cands) if cands else None


def beat_report_lines(images, segments, dflt_dur):
    """每个导演段一张对轴表（作者用 --beat-report 索取，默认不打，免得盖过 warn）。

    它回答"每一拍到底踩在话的哪儿"：in=句内、gap=句间静音、tail=段尾静音、
    before/after=窗外（就是门禁 warn 的那两类）。**in 也不等于准**：句内 frac 是
    "语速均匀"的线性假设，实测一个标点停顿就能让它偏 0.2–1s——这里给的是机械能判的
    那一层，词级对轴要么听一遍，要么换带 word timestamps 的配音。
    """
    seg_by_id = {seg["id"]: seg for seg in segments}
    lines = []
    for sid in (images or {}):
        director = (images[sid] or {}).get("director")
        seg = seg_by_id.get(sid)
        if not director or not seg:
            continue
        sents = seg["sentences"]
        seg_start = float(sents[0]["start_time"])
        seg_end = float(sents[-1]["start_time"]) + float(sents[-1].get("duration", 0.0))
        nxt = next_speech_start(segments, sid)
        try:
            beats = beat_positions(director["steps"], sents, round(seg_start, 2), dflt_dur)
        except ValueError as e:
            lines.append(f"[beat] {sid}: 解析中断 — {e}")
            continue
        lines.append(f"[beat] {sid}  旁白 {seg_start:.2f}–{seg_end:.2f}s（{len(sents)} 句）"
                     + (f"，下一页 {nxt:.2f}s 起" if nxt is not None else "，末段无右界"))
        tally = {}
        for (pos, _dur), step in zip(beats, director["steps"]):
            kind, where = _beat_landing(pos, sents, seg_start, seg_end, nxt)
            n = beat_cycles(step)
            if n is None:
                where += "（无限循环，没有收尾）"
            elif n > 1:
                where += f"（演 {n} 遍）"
            tally[kind] = tally.get(kind, 0) + 1
            lines.append("       %-12s %8.2fs  %-6s %-30s %s" % (
                step["target"], pos, kind, where, _beat_anchor(step)))
        lines.append("       — %d 拍：%s" % (
            len(beats), " · ".join("%s %d" % (k, tally[k]) for k in
                                   ("in", "gap", "tail", "before", "after") if tally.get(k))))
    return lines


def director_prepare(images, segments, out_dir):
    """SVG「导演」编排的生成期体检 + 净化内联，返回 (错误, 警告)。

    写了 director 的条目才走这条路：读盘 → 净化（_svg_sanitize 去掉 script/on*/
    外链/SMIL/墙钟动画，root 改成 cover 语义）→ 回填 entry["inline_svg"]，渲染端
    据此把 SVG 内联成活 DOM，GSAP 才能逐帧驱动图内命名元素（见 html_renderer
    ._director_timeline_lines）。五件事只有这里查得到：

    - at 越界：写了 steps[i].at 时，其整数部分必须 < 该段旁白句数（0 基句序）——越界会让
      渲染端 raise，这里提前按段落点名。用 at_time（段落绝对秒）的步骤没有句序可锚，跳过此检。
    - target 落空：steps[i].target 指的 id 必须在 SVG 里真实存在。落空不是报错
      而是**静默无动画**（选择器匹配不到任何元素），成片看着"没动"却全程零提示，
      所以按文件拦成 error。
    - 运镜裁切：`#cam` 里的内容被 scale/平移推到画幅**静止位之外**（见 _cam_crop），warn。
    - 节拍出窗：每一步的落点按 _timeline.beat_positions 解析成绝对秒，落在本页可见窗口
      之外（早于本段旁白 / 晚于下一段起点）warn——那是"这一拍根本不演"，`at_time` 过期
      最常见；起点在窗内、终点越过下一段起点的也 warn（那一拍被切在半路，终态从没出现过）。
      逐拍踩在哪句可以用 `--beat-report` 打表看。
    - 净化说明：删掉了哪些不安全/墙钟构造，按 warn 让人知情（决定性的代价写在明面）。
    - 跨段延续（stage:"keep"）：写完上面这些，再按 manifest 段序走第二遍，把上一页
      演完的画面烘焙成下一页的内联副本（见 _stage_carry_pass）。那一遍还要替接续页补做
      运镜自查——第一遍按盘上原图算的投影对接续页是假话（原图里没有上一页推近的几何）。

    非 SVG 挂 director 已由 _images_schema 在契约层拒掉，这里不重复。
    """
    errs, warns = [], []
    seg_by_id = {seg["id"]: seg for seg in segments}
    dflt_dur = _director_default_duration()   # 与渲染端同一份缺省，否则落点算错
    for sid, entry in (images or {}).items():
        director = entry.get("director")
        # 接续页：起点不是盘上原图，而是上一页演完的画面（见 _stage_carry_pass）。
        carried = entry.get("stage") == "keep"
        if not director:
            continue
        src = entry["src"]
        path = os.path.join(out_dir, src)
        if not os.path.isfile(path):
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 在 {out_dir} 下不存在")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as e:
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 读不了: {e}")
            continue
        try:
            markup, notes = sanitize_svg_for_inline(raw)
        except ValueError as e:
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 无法净化内联: {e}")
            continue
        for n in notes:
            warns.append(f"段落 '{sid}' 的 director 配图 {src}：{n}")
        ids = _svg_selectable_tokens(raw)
        seg = seg_by_id.get(sid)
        n_sent = len(seg["sentences"]) if seg else 0
        for i, step in enumerate(director["steps"]):
            if seg is None:
                errs.append(f"段落 '{sid}' 写了 director，但 manifest 里没有这一段")
                break
            # at 才有句序越界可验；at_time 锚在段落绝对时间（含相对串），与旁白句数无关，跳过。
            if "at" in step:
                at = step["at"]
                if int(at) >= n_sent:
                    errs.append(f"段落 '{sid}' 的 director.steps[{i}] at={at} 越界："
                                f"该段只有 {n_sent} 句旁白（at 的整数部分=句序，最大 {n_sent - 1}）")
            target = step["target"]
            if target not in ids:
                errs.append(f"段落 '{sid}' 的 director.steps[{i}] target={target} 在 "
                            f"{src} 里找不到对应 id/class 的元素——选择器落空不会报错，只会"
                            "静默没有动画。给该元素补上这个 id/class，或改 target")
        entry["inline_svg"] = markup
        # 起始帧钩子：只查整页画布 × 导演。画布页不生成标题层与句子流层，开头整片
        # 空白时观众对着空画面不知所以然；槽位页有标题和句子流兜底，不需要这条。
        # 接续页（stage:"keep"）跳过：它的起点是上一页演完的画面，不是盘上原图。
        # 判据只读静态可见性（见 _svg_frame0_visibility 的"宁可多报"口径）：
        # 导演约定"内容 opacity=0 起步、结构底同时到位"，全隐藏起步 = 开场没有钩子。
        if not carried and seg is not None and seg_layout(seg) == "canvas":
            n_vis, n_all = _svg_frame0_visibility(raw)
            if n_all and n_vis == 0:
                warns.append(
                    f"段落 '{sid}' 的整页画布导演档起始帧全空：SVG 里 {n_all} 个可渲染"
                    f"元素全部以 opacity=0 / visibility:hidden 起步，第一拍落子之前"
                    f"整页是空白（画布页没有标题层也没有字幕层）。开场要留钩子：把"
                    f"结构底（坐标轴/外框/标题）不写进 director.steps、让它和页面同时"
                    f"到位，或把第一拍 set 在页起点（见 references/canvas.md"
                    f"「整页画布的密度与层次」的起始帧一条）")
        # 运镜裁切：源码看着坐标都在幅内，是 scale 把内容推出去的，只有这里同时握有
        # SVG 原文与 steps（判据与口径见 _cam_crop 模块头）。warn 不 error：途中出画是
        # 合法演法，这里只报"静止位看不见的内容"，而那也可能是作者故意的取舍。
        # 净化后的串就够用（sanitize 只改 root 的 cover 语义、剥脚本，viewBox 与几何不动）。
        # 接续页（stage:"keep"）跳过：起点不该是盘上原图而是上一页演完的画面，
        # 由 _stage_carry_pass 拿烘焙后的副本重算，免得两遍各报一套互相矛盾的越界。
        if not carried:
            for w in crop_warnings(markup, director.get("steps")):
                warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
        # z 序自证：SVG 叠压顺序 = DOM 顺序，而导演揭示顺序是作者声明的意图——
        # 后亮的元素要叠在先亮的上面。两个都被步骤揭示的内容元素包围盒相交时，
        # 若先亮的反而写在 DOM 后面，成片里它会把后亮的盖住（后亮的在它最该显眼
        # 的那一刻被压在旧内容底下）。warn 不 error：包围盒相交是保守近似，故意
        # 垫底的取舍也存在。接续页（stage:"keep"）跳过：起点是上一页演完的画面，
        # 盘上原图的 DOM 顺序对接续页的可见层是假话。
        if not carried:
            for w in _zorder_warnings(markup, director.get("steps")):
                warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
        # 节拍↔旁白对轴：at_time 是按某一版配音手算的绝对秒，重配音/改句长/动 --gap
        # 之后它整体错位，而落在 clip 窗口外的那一拍是**静默不演**（引擎按
        # data-start/data-duration 硬切可见性）。这里同时握有 steps 与 manifest 句子，
        # 是整条链上唯一算得出落点的地方。warn 不 error：压在下一页可能正是要的效果。
        if seg:
            for w in _beat_window_warnings(director["steps"], seg["sentences"],
                                           float(seg["sentences"][0]["start_time"]),
                                           next_speech_start(segments, sid), dflt_dur,
                                           keep=carried):
                warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
    _stage_carry_pass(images, segments, dflt_dur, errs, warns)
    return errs, warns


def _director_beats(steps, seg, dflt_dur):
    """steps → [(绝对秒, 时长)]，锚在段落首句起点；解析不动时返回 None。

    只服务"按时刻结算收尾态"的排序需求（_stage_carry._ordered），所以 at 越界这类
    已经由别处按步报过的错，这里退回数组序兜底就好——再报一遍只会把同一条错误说两次。
    """
    try:
        return beat_positions(steps, seg["sentences"],
                              round(float(seg["sentences"][0]["start_time"]), 2),
                              dflt_dur)
    except (ValueError, KeyError, IndexError):
        return None


def _stage_carry_pass(images, segments, dflt_dur, errs, warns):
    """跨段场景延续的生成期第二遍：上一页"演完之后"的画面 = 下一页的起点。

    为什么要第二遍而不是就地做：下一页要的起点是**上一页烘焙之后**的副本（A→B→C 里 C
    必须拿到 A+B 的叠加），所以只能沿 manifest 段序推进；第一遍走的是 images.json 的
    键序，两者不保证一致，就地算会用到还没烘焙的前页。

    烘焙本身在 _stage_carry（口径与边界写在那个模块头）。这里只回答"接的是谁"：
    前一段存在吗、它内联得出来吗、两页是不是同一张图，以及接续页的运镜自查。
    """
    if not images:
        return
    for i, seg in enumerate(segments):
        sid = seg["id"]
        entry = images.get(sid)
        if not entry or entry.get("stage") != "keep":
            continue
        src = entry["src"]
        where = f"段落 '{sid}' 写了 stage:\"keep\""
        if i == 0:
            errs.append(f"{where}，但它是 manifest 里的第一段——接续的起点是上一页演完的"
                        "画面，第一页没有上一页。把 stage 挪到真正接续的那一页，或这一页"
                        "独立成图")
            continue
        prev = segments[i - 1]
        prev_entry = images.get(prev["id"]) or {}
        prev_markup = prev_entry.get("inline_svg")
        if not prev_markup:
            errs.append(f"{where}，但上一段 '{prev['id']}' 没有可接续的内联画面——它没写"
                        " director（写了才净化内联），或它的配图没读进来/没能内联。"
                        " 接续页要的是上一页演完的那幅活画面，<img> 那份没有可烘焙的元素态")
            continue
        if prev_entry.get("src") != src:
            errs.append(f"{where}，但它的 src={src!r} 和上一段 '{prev['id']}' 的 "
                        f"{prev_entry.get('src')!r} 不是同一张图——接续的前提是同一幅画"
                        " 跨页继续演。要换画面就别写 stage；要同一幅画就把两页的 src 对齐")
            continue
        prev_steps = (prev_entry.get("director") or {}).get("steps") or []
        try:
            markup, notes = bake_settled_state(
                prev_markup, prev_steps, _director_beats(prev_steps, prev, dflt_dur))
        except ValueError as e:
            errs.append(f"{where}，但烘焙上一页的收尾态失败: {e}")
            continue
        for n in notes:
            warns.append(f"段落 '{sid}' 接续自 '{prev['id']}'：{n}")
        entry["inline_svg"] = markup
        # 接续页的运镜自查拿**烘焙后**的副本来算：这一页的相机姿态是在上一页推近的画
        # 面之上再乘一层，只看盘上原图会把已经出画的元素当成"在幅内"。
        own = (entry.get("director") or {}).get("steps")
        for w in crop_warnings(markup, own):
            warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
        # step_i 不叫 i：外层 for i, seg 用的是段序，内层复用 i 会静默把它
        # 换成 step 下标——今天块内不读外层 i 所以无害，下一行加一句用 i 的
        # 代码就会拿到错的值。
        for step_i, sel in global_ref_leaks(markup, own):
            errs.append(f"{where}，但它的 director.steps[{step_i}]（target={sel}）打在了两份副本"
                        "共享的元素上——图里的 url(#id)/href=\"#id\" 按文档序只认第一份"
                        f"（= 上一段 '{prev['id']}' 那一幅），所以这一拍打在本页这份上，本页"
                        "没有任何图形会去读它。渐变/滤镜/marker 这类共享元素的补间请只在"
                        "前一段写，别在接续页重写")
    return


def _svg_text_metrics(path):
    """从 SVG 全文取 (最小 px 字号, <text> 个数)，读不到字号时返回 (None, n)。

    字号只认 px：`font-size="26"`、`font-size: 26px`、`font-size="26.5px"` 都算，
    em / % / pt 一律不认——相对单位要乘父级才知绝对值，这里没有布局引擎，硬猜
    会把"能读"和"读不准"混成一类，宁可交回调用方按"没法验"给 warn。

    按 ElementTree 遍历而不是全文正则：注释里提一句 `font-size` 或 `<text>` 不该
    被当成真字号/真文本（实测正则版两者都中招——注释能把最小字号抬到 99px、
    把文本个数吹大，折算可读性于是对着一份假账判）。字号三个来路都收：呈现
    属性、inline style、`<style>` 规则（后者先剥 CSS 注释再扫，注释里的
    font-size 不是声明）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            root = ET.fromstring(f.read())
    except (OSError, ET.ParseError):
        return None, 0
    sizes = []
    n_text = 0
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "text":
            n_text += 1
        if tag == "style":
            css = _CSS_COMMENT.sub("", el.text or "")
            sizes += [float(m.group(1)) for m in re.finditer(
                r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(?:px)?(?![%\w.])", css)]
            continue
        m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:px)?", (el.get("font-size") or "").strip())
        if m:
            sizes.append(float(m.group(1)))
            continue
        st = el.get("style")
        if st:
            m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)\s*(?:px)?(?![%\w.])", st)
            if m:
                sizes.append(float(m.group(1)))
    return (min(sizes) if sizes else None), n_text


def _audio_candidates(path, out_dir, manifest_dir=None):
    """音频查找候选：绝对路径原样；相对路径依次试 项目根 → 当前工作目录 →
    manifest 所在目录（第三档只在读 manifest 的 combined_audio 时加入）。"""
    if os.path.isabs(path):
        return [path]
    cands = [os.path.join(out_dir, path), os.path.abspath(path)]
    if manifest_dir:
        cands.append(os.path.join(manifest_dir, path))
    return cands


def audio_fallback_check(audio_path, want_total):
    """回落的音轨能不能被证明属于这条时间轴：放行返回 None，否则返回拒跑理由。

    manifest 的 `combined_audio` 指不到文件时，main() 会静默复用项目里上次暂存的
    `audio/combined.wav`。那是"上一次的配音"，未必是这一条稿子的——成片会有声、字幕
    也会对，只是两者说的不是同一件事，交付前没有任何环节会再提一次。所以复用之前
    必须拿实测时长跟 manifest 时间轴对账。

    对账**测不出**（`measure_duration` 返回 0.0）不是"对过了、一致"，而是这次对账根本
    没发生：非 WAV 且 ffmpeg 不可用、或 WAV 本身损坏（wave 模块解析失败）都会走到
    这里。放行它等于把上一版的判断（"对得上"）变成永远成立——所以按拒跑处理，宁可
    让用户重跑 TTS。

    `want_total` 为空（manifest 没写总时长）时无从对账，返回 None：那条路径本来就没有
    可比较的基准，差别只在 warn 已经打过。
    """
    if want_total in (None, "", 0):
        return None
    try:
        want = float(want_total)
    except (TypeError, ValueError):
        return None
    if want <= 0:
        return None
    got = measure_duration(get_ffmpeg(), audio_path)   # 0.0 = 测不出
    if not got:
        return (f"回落用的 {audio_path} 测不出时长（ffmpeg 不可用，或这个文件不是"
                "能解码的音频）——无法证明它属于当前这份 manifest"
                f"（时间轴 {want:.2f}s）。请重跑 TTS，或显式 --audio 指定这条稿子的"
                "配音；把 ffmpeg 装回来也可，装好后会重新对账。")
    if abs(got - want) > duration_tolerance(want):
        return (f"回落音频实测 {got:.2f}s 与 manifest 时间轴 {want:.2f}s 相差过大——"
                "这是另一条时间轴的旧配音，拒绝用新字幕烧旧音轨。"
                "请重跑 TTS，或显式 --audio。")
    return None


def _epilog():
    """--help 的尾巴：一条能照抄的命令 + 一份实测跑通的 images.json。

    只放「看例子比读文字快」的东西：路径基准（纯靠猜猜不出——`-o`/`--images`
    的相对路径都相对**当前工作目录**，而 images.json 里各条的 src 相对 -o 所在
    目录，两者不是同一个基准），以及三种素材条目各自的完整形状。

    这里刻意**不列**「容易写错的几处」：那些撞一次错就全知道，而且报错比
    任何清单都具体（写错 volume 越界时报错带出合法区间与这点的含义）。
    在 --help 里抄一份取值表只会让它看起来像需要背的规则。
    """
    return "\n".join([
        "",
        "分步执行时的两步（run.py 一条命令跑完整条管线）：",
        "",
        "  python scripts/gen_hyperframes.py \\",
        "      -m audio_output/timing_manifest.json \\",
        "      -o hf-project/index.html \\",
        "      --images hf-project/images.json \\",
        "      --aspect portrait --fps 24",
        "",
        "（-o 与 --images 的相对路径都相对当前工作目录；images.json 里各条的",
        " src 才相对 -o 所在目录）",
        "",
        "可直接照抄的 images.json（实测跑通）：",
        "",
        "  {",
        '    "seg1": {"src": "images/seg1.png",',
        '              "provider": "imagegen", "license": "CC0"},',
        '    "seg2": {"src": "images/seg2.svg",',
        '              "director": {"steps": [',
        '                 {"target": "#bar-1", "at": 0, "to": {"height": 320}},',
        '                 {"target": "#bar-2", "at": 1, "to": {"height": 480}}]}},',
        '    "seg3": {"src": "images/seg3.gif"}',
        "  }",
        "",
    ])


def main(argv=None):
    """argv=None 走 sys.argv；run.py 进程内直调时传入参数列表，
    参数校验只有本文件这一份 parser，run.py 不再复制。"""
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="Generate Hyperframes composition from timing manifest",
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-m", "--manifest", required=True,
                        help="Path to timing_manifest.json")
    parser.add_argument("-o", "--output", required=True,
                        help="Output HTML file path")
    parser.add_argument("--audio", default=None,
                        help="Audio src path in HTML (default: auto-detect from manifest)")
    parser.add_argument("--images", default=None,
                        help="Path to images.json (maps segment ID -> image path relative to HTML)")
    parser.add_argument("--aspect", default="portrait",
                        choices=["portrait", "landscape"],
                        help="画幅：portrait（默认，1080×1440 竖屏 3:4）或 "
                             "landscape（1920×1080 横屏 16:9）。两画幅共用同一套"
                             "组件版式，数值各取 _template.py 对应块。")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS,
                        help="输出帧率（写入 HTML 的 data-fps；渲染时可用 --fps 覆盖，"
                             "默认 24；本技能推荐 12/24/30/60——12 仅用于快速预览片，"
                             "Hyperframes 当前支持更广的 FPS 范围）")
    parser.add_argument("--gsap-src", default=None,
                        help="GSAP script URL or local path. 默认取输出项目的 "
                             "vendor/gsap.min.js（须与钉固字节完全一致）；缺失时按"
                             "用户缓存 → 钉固 CDN 的顺序安装（下载体过 sha256 校验"
                             "才落盘）。传显式值（URL 或相对路径）可覆盖，但自定义源"
                             "不做哈希钉固校验，可信度自负。")
    parser.add_argument("--beat-report", action="store_true",
                        help="打印每个导演段每一拍落在旁白哪句/句间静音/段尾/窗外的对轴表"
                             "（排查动画与语音不同步用；只打表，不改变生成结果）。")
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
            _seg_by_id = {seg["id"]: seg for seg in _segs}
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
            _seg_durs = {seg["id"]: segment_duration(seg)
                         for seg in _segs}
            # 每个段落的配图要被铺进多大的框：整页画布铺整个画幅，槽位版式只
            # 铺中间那块 4:3——"素材够不够大"只能按它实际落地的框判，按画幅
            # 判会把槽位那一档整批漏报（槽位比画幅小得多，同样的图在槽位里
            # 是够用的）。
            _box_slot = get_image_box(args.aspect)
            _box_full = get_canvas(args.aspect)
            _seg_boxes = {seg["id"]: (_box_full if seg_layout(seg) == "canvas"
                                      else _box_slot)
                          for seg in _segs}
            missing_imgs, corrupt_imgs = validate_images_files(
                images, out_dir, _seg_durs, _seg_boxes)
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
    _uncovered = [sid for sid in sids_needing_image(manifest) if sid not in images]
    if _uncovered:
        print(f"[warn] {len(_uncovered)} 个段落没有配图映射: "
              f"{', '.join(_uncovered)}——若是没找到合适的图或漏配，请按第 3 步"
              f"在两种形式（ImageGen / SVG）中选型补图（见 references/imagery.md）"
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
    # 槽位版式那张 SVG 的比例（另一条判据，见 slot_svg_ratio_errors）。它拦的是
    # "cover 切掉图边缘上的字"，与画布版式那条各管一段版式，互不覆盖，两边都收齐再退。
    _slot_errs = slot_svg_ratio_errors(
        images, manifest["segments"], out_dir, *get_image_box(args.aspect))
    # 满幅背景 <rect>：SVG 应透明、让模板三层透出来（判据与 check_svg 同一份，
    # 见 svg_background_warnings）。不跑自查、直接生成也按段落出声；warn 不 error，
    # 有意为之（浅色页/照片感整页）可忽略。
    _bg_warns = svg_background_warnings(images, manifest["segments"], out_dir)
    for _w in _canvas_warns + _bg_warns:
        print(f"[warn] {_w}", file=sys.stderr)
    if _canvas_errs or _slot_errs:
        for _e in _canvas_errs + _slot_errs:
            print(f"[error] {_e}", file=sys.stderr)
        sys.exit(1)

    # 导演（时间轴同步 SVG 动画）的生成期体检 + 净化内联：必须在 generate_html
    # 之前跑，它把净化后的 SVG 回填进 entry["inline_svg"]，渲染端才知道这张要内联。
    _dir_errs, _dir_warns = director_prepare(images, manifest["segments"], out_dir)
    for _w in _dir_warns:
        print(f"[warn] {_w}", file=sys.stderr)
    if args.beat_report:
        # 对轴表打在 warn 之后、失败退出之前：报的正是"哪一拍没踩在话上"，
        # 越界这类错误发生时它最有价值，所以不能被 exit 挡在前面。
        for _line in beat_report_lines(images, manifest["segments"],
                                       _director_default_duration()):
            print(_line, flush=True)
    if _dir_errs:
        for _e in _dir_errs:
            print(f"[error] {_e}", file=sys.stderr)
        sys.exit(1)

    if args.audio:
        audio_candidates = _audio_candidates(args.audio, out_dir)
        audio_path = next((p for p in audio_candidates if os.path.isfile(p)), None)
        if audio_path is None:
            raise SystemExit(
                f"[error] --audio 指定的音频不存在: {args.audio}\n"
                f"已检查: {', '.join(os.path.abspath(p) for p in audio_candidates)}")
        audio_src = _stage_audio_file(audio_path, out_dir)
    else:
        audio_abs = manifest.get("combined_audio", "")
        if audio_abs:
            audio_candidates = _audio_candidates(
                audio_abs, out_dir,
                os.path.dirname(os.path.abspath(args.manifest)))
            audio_path = next((p for p in audio_candidates
                               if os.path.isfile(p)), None)
        else:
            audio_path = None
        fell_back = audio_path is None
        # 回落名与 pipeline 写侧同源（_audio.COMBINED_AUDIO_NAME）：它落在
        # audio/ 下（_stage_audio_file 暂存进项目目录时按 basename 存），两边各
        # 拼一份的话改名只改一侧，回落路径就永远找不到、报错文案写着不存在的文件。
        _default_rel = os.path.join("audio", COMBINED_AUDIO_NAME)
        if audio_path is None:
            default_audio = os.path.join(out_dir, _default_rel)
            audio_path = default_audio if os.path.isfile(default_audio) else None
        if audio_path is None:
            raise SystemExit(
                "[error] manifest 未提供可用音频，且默认路径不存在: "
                f"{os.path.join(out_dir, _default_rel)}。"
                "请重跑 TTS，或显式传入 --audio <文件路径>。")
        if fell_back:
            # 走到这里 = manifest 的音频路径不存在（OUTPUT 被清过/搬走过）
            # 或 manifest 根本没写音频，静默复用项目里上次暂存的
            # audio/combined.wav——新字幕配旧配音会安静烧进成片。
            # 实测时长与 manifest 时间轴对不上就直接拒跑，别赌。
            print(f"[warn] manifest 的 combined_audio 不可用，回落到上次暂存的 "
                  f"{audio_path}——若这是另一条稿子的旧配音，重跑 TTS 或显式 "
                  f"--audio 指定。", file=sys.stderr, flush=True)
            _why = audio_fallback_check(audio_path, manifest.get("total_duration"))
            if _why:
                raise SystemExit(f"[error] {_why}")
        audio_src = _stage_audio_file(audio_path, out_dir)

    html = generate_html(manifest, audio_src,
                         images=images,
                         width=w, height=h, gsap_src=gsap_src,
                         aspect=aspect, fps=args.fps)

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
    print(f"     FPS: {args.fps}")
    print(f"     Images: {len(images)}")
    print(f"     Audio src: {audio_src}")
    print(f"     GSAP src: {gsap_src}")


if __name__ == "__main__":
    main()
