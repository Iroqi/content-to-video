#!/usr/bin/env python3
"""Generate Hyperframes HTML composition from timing_manifest.json.

Reads the manifest (with optional segments grouping) and produces a complete
Hyperframes composition with GSAP animations, subtitle sync, and audio track.

Features:
- Verse subtitle sentence flow, title entrance / canvas slide-in / progress bar
  animations (portrait 1080x1440 / landscape 1920x1080, via --aspect)
- Image cards via --images (content segments are expected to have images;
  opening/closing are text-only agenda cards. A text-only fallback is
  allowed but explicitly warned about)

Optionally accepts an --images JSON file mapping segment IDs to image paths,
which will be embedded as the centered 4:3 canvas below the title.

Usage:
  python gen_hyperframes.py -m timing_manifest.json -o hf-project/index.html
  python gen_hyperframes.py -m timing_manifest.json -o hf-project/index.html --images hf-project/images.json

The manifest must contain:
  - sentences[]: {index, text, start_time, duration, speaker?}
    speaker（可选）：来自 build_from_structured.py 的 dialogue 段落。该字段
    只保留在 cue 数据层（speaker/spk 索引）——verse 两画幅都不在画面里渲染
    说话人标签，角色区分依靠语音与行文。绝大多数场景（单人独白）没有
    这个字段。
  - total_duration: 测量得到的音频总时长
  - segments[] (optional): {id, title, tagline, accent, sentences[]}
    If absent, sentences are auto-grouped into chunks of 5.

images.json format (all paths relative to the HTML output directory):
  {"seg1": {"src": "images/seg1-imo.png"}, "seg2": {"src": "images/seg2-gemini.png"}, ...}
  Each value is a media object with a required "src"; a bare string path is rejected.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request


# GSAP 是 HTML composition 生成阶段唯一必需的本地运行资产；它的获取与安装
# 逻辑直接归属本模块，不再套一层中间抽象。
GSAP_VERSION = "3.14.2"
_ASSET_MAGIC_MARKERS = (b"gsap",)
GSAP_CDN_URL = f"https://cdn.jsdelivr.net/npm/gsap@{GSAP_VERSION}/dist/gsap.min.js"
# 供应链钉固：这个 JS 会被引擎内的浏览器真实执行，CDN 版本又是不可变的，
# 所以把官方 dist 的 sha256/字节数钉死（校验口径见 _validate_gsap_payload）。
GSAP_SHA256 = "c174bfce53a729418d57a8ad8625e7247c793a22fef8e2851e3cfa3de9cd8280"
GSAP_BYTES = 72779
CHARTJS_VERSION = "4.5.1"
CHARTJS_CDN_URL = f"https://cdn.jsdelivr.net/npm/chart.js@{CHARTJS_VERSION}/dist/chart.umd.min.js"
CHARTJS_SHA384 = "jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ"
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "content-to-video", "vendor")
_CACHE_PATH = os.path.join(_CACHE_DIR, f"gsap-{GSAP_VERSION}.min.js")
# 离线内置副本（技能包 assets/ 下，逐字节 = 官方 dist）。文件名带版本号，
# 与 _BUNDLED_* 的推导和 assets/README.md 的记录保持一致。
_BUNDLED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_BUNDLED_GSAP = os.path.join(_BUNDLED_DIR, f"gsap-{GSAP_VERSION}.min.js")
_BUNDLED_CHARTJS = os.path.join(_BUNDLED_DIR, f"chartjs-{CHARTJS_VERSION}.umd.min.js")
_MIN_GSAP_BYTES = 1000
_MAX_VENDOR_BYTES = 8 * 1024 * 1024   # 下载响应体上限（两种资产共用）
_MIN_CHARTJS_BYTES = 10000


def _valid_file(path, minimum=_MIN_GSAP_BYTES):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < minimum:
            return False
        with open(path, "rb") as f:
            head = f.read(4096).lower()
        return any(marker in head for marker in _ASSET_MAGIC_MARKERS)
    except OSError:
        return False


def _valid_trusted_gsap(path):
    """Whether a canonical/default GSAP asset exactly matches the pinned bytes."""
    if not _valid_file(path) or os.path.getsize(path) != GSAP_BYTES:
        return False
    try:
        return sha256_file(path) == GSAP_SHA256
    except OSError:
        return False


def _validate_gsap_payload(data, url=None):
    """GSAP 资产校验：① 内容自检——≥1KB 的 HTML 错误页/注入脚本必须在
    这里被认出来；② 供应链钉固——默认 CDN 的 dist 是版本不可变资源，字节
    必须与钉固的 sha256 完全一致（疑似 CDN 污染/代理劫持时拒绝写缓存）。
    显式 URL 属于运维/测试自选来源，不套用默认钉固值。"""
    lowered = data[:4096].lower()
    if not any(marker in lowered for marker in _ASSET_MAGIC_MARKERS):
        raise ValueError("downloaded file does not look like GSAP")
    if url in (None, GSAP_CDN_URL):
        got = hashlib.sha256(data).hexdigest()
        if got != GSAP_SHA256:
            raise ValueError(
                f"GSAP {GSAP_VERSION} 内容与钉固的 sha256 不一致"
                f"（期望 {GSAP_SHA256[:16]}…，实际 {got[:16]}…）；"
                "疑似 CDN 被污染或代理劫持，已拒绝写入缓存")


def _validate_chartjs_payload(data, url=None):
    """Chart.js 资产校验：sha384（base64）完整性钉固。"""
    import base64
    got = base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
    if got != CHARTJS_SHA384:
        raise ValueError("Chart.js integrity check failed")


def _download_to_cache(cache_dir, cache_path, timeout=15, url=None, *,
                       asset="GSAP", min_bytes=None, max_bytes=None,
                       validate=None):
    """下载 vendor 资产到本地缓存（GSAP / Chart.js 共用唯一实现）。

    完整性校验由 validate(data, url) 注入（失败 raise，资产不落盘）；
    min/max 字节数采用当前资产验证契约；写入保持
    mkstemp → fsync → os.replace 的原子序，失败清理临时文件。
    """
    min_bytes = _MIN_GSAP_BYTES if min_bytes is None else min_bytes
    max_bytes = _MAX_VENDOR_BYTES if max_bytes is None else max_bytes
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "content-to-video"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
        if len(data) < min_bytes:
            raise ValueError(f"downloaded file too small ({len(data)} bytes)")
        if len(data) > max_bytes:
            raise ValueError(f"downloaded file too large ({len(data)} bytes)")
        if validate is not None:
            validate(data, url)
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


def _valid_chartjs(path):
    """Whether a Chart.js asset exactly matches the pinned official dist."""
    try:
        return (os.path.isfile(path)
                and os.path.getsize(path) > _MIN_CHARTJS_BYTES
                and _chartjs_sha384(path) == CHARTJS_SHA384)
    except OSError:
        return False


def _chartjs_sha384(path):
    import base64
    h = hashlib.sha384()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")

def _install_vendor(src, dest_path, verify, label):
    """把一份已校验过的源装进项目 vendor/：copy → 复查字节 → os.replace。

    拷贝后必须重算一遍：写盘被截断/磁盘满留下的半截文件若留在 dest，
    下次 ensure_* 的 dest 命中检查会把它当成好资产直接复用（GSAP 原来
    就有这道复查，Chart.js 没有——两条路径现在同一口径）。
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


def _vendor_sources(bundled, cache_path, check, label, explicit_url=False):
    """列出可离线（不联网）安装的源：内置副本 → 用户缓存。

    内置文件存在却过不了钉固校验时点名警告——技能包被改动或下载损坏是
    一件需要让人知道的事，静默回落缓存就成了一次无人察觉的降级。
    explicit_url 是运维自选来源，内置的 pinned 字节不是它要的东西。
    """
    sources = [] if explicit_url else [bundled]
    sources.append(cache_path)
    if not explicit_url and os.path.isfile(bundled) and not check(bundled):
        print(f"[warn] 技能包内置的 {label} 副本（{bundled}）与钉固字节不符，"
              "已跳过并回退用户缓存/CDN。多为技能包被改动或传输损坏；"
              "确认技能包来源后重新获取，或设置 CTV_ALLOW_NETWORK_ASSETS=1 "
              "从钉固 CDN 重新获取。", file=sys.stderr)
    return sources


def ensure_local_chartjs(project_dir, timeout=15, allow_network=False):
    project_dir = os.path.abspath(project_dir)
    cache_path = os.path.join(_CACHE_DIR, f"chartjs-{CHARTJS_VERSION}.umd.min.js")
    dest_path = os.path.join(project_dir, "vendor", "chart.umd.min.js")
    if _valid_chartjs(dest_path):
        return "vendor/chart.umd.min.js"
    # 内置 → 缓存 →（显式允许时）CDN，每一级都重新校验字节。
    for src in _vendor_sources(_BUNDLED_CHARTJS, cache_path,
                               _valid_chartjs, "Chart.js"):
        if _valid_chartjs(src) and _install_vendor(
                src, dest_path, _valid_chartjs, "Chart.js"):
            return "vendor/chart.umd.min.js"
    if not allow_network:
        return None
    if not _download_to_cache(
            _CACHE_DIR, cache_path, timeout=timeout, url=CHARTJS_CDN_URL,
            asset="Chart.js", min_bytes=_MIN_CHARTJS_BYTES,
            validate=_validate_chartjs_payload):
        return None
    if not _install_vendor(cache_path, dest_path, _valid_chartjs, "Chart.js"):
        return None
    return "vendor/chart.umd.min.js"


def ensure_local_gsap(project_dir, timeout=15, cache_dir=None, cache_path=None,
                      allow_network=False, url=None):
    """Ensure ``project_dir/vendor/gsap.min.js`` exists and return its relative path.

    默认路径只认与钉固字节完全一致的源（项目 vendor → 技能包内置 → 缓存 →
    CDN），所以旧缓存里被污染的历史下载不会被复用。显式 url 属于运维自选
    来源：跳过内置、只做内容自检，并且落到独立的缓存文件里，绝不写进默认
    缓存路径——否则一次自定义下载就能污染后续默认路径。
    """
    project_dir = os.path.abspath(project_dir)
    cache_dir = cache_dir or _CACHE_DIR
    cache_path = cache_path or _CACHE_PATH
    dest_dir = os.path.join(project_dir, "vendor")
    dest_path = os.path.join(dest_dir, "gsap.min.js")

    explicit_url = bool(url)
    check = _valid_file if explicit_url else _valid_trusted_gsap
    if not explicit_url and _valid_trusted_gsap(dest_path):
        return "vendor/gsap.min.js"

    # 显式 url 是运维自选来源，绝不能落进默认缓存路径——否则这次自定义下载
    # 会被后续默认路径当成钉固资产复用。给它一个按 url 哈希命名的缓存。
    if explicit_url and cache_path == _CACHE_PATH:
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        cache_path = os.path.join(cache_dir,
                                  f"gsap-{GSAP_VERSION}-custom-{cache_key}.min.js")

    for src in _vendor_sources(_BUNDLED_GSAP, cache_path, check,
                               "GSAP", explicit_url=explicit_url):
        if check(src) and _install_vendor(src, dest_path, check, "GSAP"):
            return "vendor/gsap.min.js"
    if not allow_network:
        return None
    if not _download_to_cache(cache_dir, cache_path, timeout=timeout,
                              url=url or GSAP_CDN_URL,
                              validate=_validate_gsap_payload):
        return None
    if not _install_vendor(cache_path, dest_path, check, "GSAP"):
        return None
    return "vendor/gsap.min.js"


# 主题配色 / 视觉模板 / HTML 组装已拆到独立模块（_theme / _template /
# html_renderer），本文件只留 CLI 与资产、媒体文件的落地校验。
sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _theme import list_theme_names  # noqa: E402
from _template import get_canvas  # noqa: E402
from _contracts import (load_timing_manifest, validate_images_json,  # noqa: E402
                        classify_media_path, is_content_sid, media_needs_chartjs)
from _script_utils import (setup_stdio, write_text_atomic, sha256_file,  # noqa: E402
                           guard_not_in_skill_dir, is_inside)
from _audio import get_ffmpeg, measure_duration, parse_duration  # noqa: E402

from html_renderer import (  # noqa: E402
    _segment_duration, generate_html, manifest_segments,
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


def _allow_network():
    """CTV_ALLOW_NETWORK_ASSETS 的统一判定（GSAP / Chart.js 共用一个开关）。"""
    return os.environ.get("CTV_ALLOW_NETWORK_ASSETS", "").strip().lower() in {"1", "true", "yes"}


def _warn_if_local_vendor_missing(label, src, out_dir):
    """显式 --gsap-src/--chartjs-src 给的是本地路径却找不到文件时提示（URL 不判）。

    这类脚本 404 不会让生成失败：页面里 `gsap` 未定义，整条时间线建不起来，
    而每个段落卡都带内联 opacity:0——没有 GSAP 把它抬起来，成片是**全黑**
    的无声空片，不是"停在首帧"；Chart.js 缺失则只有图表槽位空白。
    这些只在真正打开浏览器或渲染时才暴露。
    显式值允许指到项目外的本地文件，所以只 warn 不 fail。
    """
    if "://" in src or src.lower().startswith("data:"):
        return
    for base in (out_dir, os.getcwd()):
        if os.path.isfile(os.path.join(base, src)):
            return
    if os.path.isfile(src):
        return
    _effect = ("整条时间线建不起来，每个段落卡都停在内联 opacity:0——成片为全黑无声空片"
               if label == "GSAP" else
               "所有图表槽位空白（字幕/音频不受影响）")
    print(f"[warn] 显式传入的 {label} 脚本路径不存在（{src}，相对 HTML 输出目录 "
          f"{out_dir} 或当前目录都找不到）。HTML 仍会生成，但页面里 {label} 未定义、"
          f"{_effect}。请补上该文件，或去掉这个参数走 vendor/ 默认路径。",
          file=sys.stderr)


def _uncovered_content_sids(manifest, images):
    """manifest 中没有配图映射的『需要配图的段落』sid 列表。

    口径：只有内容段落（seg…）需要配图。开屏/结尾是纯文字 agenda
    卡，不配图（images.json 里若还留着这两个键，渲染器也会忽略）。
    与 run.py _image_coverage 同一口径。gen_hyperframes 对缺图只提示
    不拦截（run.py 一键编排有缺图拦截，分步执行保留显式 warn，
    避免把"漏配/没找到合适的图"误当"不需要图"）。
    """
    _segs = manifest_segments(manifest)
    _need = [seg.get("id", "") for seg in _segs if is_content_sid(seg.get("id"))]
    return [sid for sid in _need if sid and sid not in images]



def validate_images_files(images, out_dir, seg_durs=None):
    """校验 images.json 引用的媒体文件存在且可解码（本模块唯一实现）。

    检测项（依赖缺失时优雅降级，只降强度不改行为）：
    - 存在性：所有类型（含 svg）；
    - 图片/视频可解码：ffmpeg 全解码探测（能同时发现 moov 缺失、头部
      损坏与尾部截断——下载中断的典型形态），并解析视频时长，比段落
      长时打"渲染只显示前段"提示（信息级，不阻断）；svg 是文本格式，
      ffmpeg 打不开，存在性校验已足够，跳过。ffmpeg 是渲染必需依赖，
      无需再引入 Pillow。

    Args:
        images: images.json 映射（每条为含 src 的媒体对象）
        out_dir: HTML 输出目录（相对路径的解析基准）
        seg_durs: {segment_id: 段落时长秒}，视频截断提示用；None 跳过提示

    Returns:
        (missing, corrupt) 两个列表：missing=[(sid, media_path)]，
        corrupt=[(sid, media_path, reason)]。是否 fail-fast 由调用方决定
        （main() 对非空结果报错退出）。
    """
    missing_imgs = []
    corrupt_imgs = []
    # 图片/视频完整性都用 ffmpeg 全解码探测（ffmpeg 是渲染必需依赖，
    # 无需再引入 Pillow）；找不到 ffmpeg 时降级为仅做存在性校验。
    _ffmpeg_probe = None
    try:
        _ffmpeg_probe = get_ffmpeg()
    except Exception:
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
            # ffmpeg 可执行不存在（get_ffmpeg 找不到时返回字面
            # "ffmpeg" 兜底串，subprocess 抛 FileNotFoundError）——
            # 降级为存在性校验通过（文件存在在调用前已查过），
            # 不当损坏处理
            return True, "", None
        err_text = (r.stderr or b"").decode("utf-8", "replace")
        if r.returncode != 0:
            return False, (err_text.strip()[-160:] or
                           f"exit {r.returncode}"), None
        return True, "", parse_duration(err_text)

    for sid, entry in images.items():
        # 上游 validate_images_json 已保证每条都是媒体对象
        media_type = entry.get("type", "auto")
        if media_type == "chart":
            continue
        media_path = entry.get("src", "")
        # .mp4 路径若被当图片送 ffmpeg 会误报"损坏"，先按扩展名推断再分流
        if media_type in ("auto", None):
            media_type = classify_media_path(media_path)
        if not media_path:
            continue
        p = media_path if os.path.isabs(media_path) else os.path.join(out_dir, media_path)
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
            poster_path = poster if os.path.isabs(poster) else os.path.join(out_dir, poster)
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


def main():
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
                             "技能包内置副本 assets/gsap-<版本>.min.js → 用户缓存的"
                             "顺序安装，两处都没有才需要 CTV_ALLOW_NETWORK_ASSETS=1 "
                             "从钉固 CDN 获取。传显式值（URL 或相对路径）可覆盖，"
                             "但自定义源不做哈希钉固校验，可信度自负。")
    parser.add_argument("--chartjs-src", default=None,
                        help="Chart.js UMD script URL or local path（仅当 images.json 里"
                             "含 chart 类型时用到）。默认与 GSAP 同策略：优先用输出项目"
                             "的 vendor/chart.umd.min.js，缺失时依次取技能包内置副本与用户"
                             "缓存，两处都没有才需要 CTV_ALLOW_NETWORK_ASSETS=1 从 CDN 取。"
                             "传显式值（URL 或相对路径）可覆盖，同样不做哈希校验。")
    args = parser.parse_args()

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
                with open(args.images, 'r', encoding='utf-8-sig') as f:
                    images = validate_images_json(json.load(f))
            except ValueError as e:
                print(f"[error] {e}", file=sys.stderr)
                sys.exit(1)
            # 开屏/结尾是纯文字 agenda 卡，不消费配图：images.json 里的
            # opening/closing 键先弹出并提示（不参与缺图判定）。必须在引用
            # 完整性校验之前——否则指向缺失文件的这两个键会被 fail-fast 误拦，
            # 与"忽略并给出 [warn]"的文档口径矛盾。
            _agenda_keys = [k for k in ("opening", "closing") if k in images]
            if _agenda_keys:
                for k in _agenda_keys:
                    images.pop(k)
                print(f"[warn] images.json 的 {'/'.join(_agenda_keys)} 键被忽略："
                      "开屏/结尾是纯文字 agenda 版式，不配图；"
                      "建议从 images.json 移除这两个键（对应配图文件可一并清理）。",
                      file=sys.stderr)
            # 孤儿键：images.json 里还留着 manifest 中不存在的段 id（稿件
            # 删段/改名后忘了同步）——弹出并 warn，不进渲染器，也不参与
            # 下方缺图判定（指向已删除配图文件的旧键不该阻断本次生成）。
            _segs = manifest_segments(manifest)
            _known_sids = {seg.get("id", "") for seg in _segs}
            _orphan_keys = [k for k in images if k not in _known_sids]
            if _orphan_keys:
                for k in _orphan_keys:
                    images.pop(k)
                print(f"[warn] images.json 的 {'/'.join(_orphan_keys)} 键未匹配到 "
                      "manifest 中的任何段落，已忽略：这些段落不存在于当前稿件"
                      "（可能已删段或改过 id），建议连同对应配图文件一并清理。",
                      file=sys.stderr)
            # 引用完整性校验（fail-fast）：images.json 声明的图片若磁盘上
            # 不存在，渲染会静默产出空白裂图——典型场景是手动改了 images.json
            # 或替换图片改了扩展名，却忘了重跑本脚本重新生成 HTML。在生成
            # HTML 前就报错，避免把坏图渲染进成片。
            # 段落时长映射——视频配图比段落长时渲染只显示前段
            # （尾部被截断），要在这里就给出提示而不是等成片后才发现。
            # 复用上方 manifest_segments 的分组结果：裸读 manifest["segments"]
            # 在无 segments 的手写 manifest 下恒空，这条 [warn] 会永不触发。
            _seg_durs = {seg.get("id", ""): _segment_duration(seg)
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
    _uncovered = _uncovered_content_sids(manifest, images)
    if _uncovered:
        print(f"[warn] {len(_uncovered)} 个段落没有配图映射: "
              f"{', '.join(_uncovered)}——若是没找到合适的图或漏配，请按第 4 步"
              f"在 A/B/C/D 四条路线中选型补图（见 references/image_options.md）"
              f"后重跑；仅当段落内容性质确实不需要图时才保留无图。", file=sys.stderr)

    # Resolve GSAP src: explicit CLI value wins. Otherwise 项目 vendor → 技能包
    # 内置副本 → 用户缓存，三级都不命中才需要网络（CTV_ALLOW_NETWORK_ASSETS=1）。
    # 永不自动回落到远程 <script>：那会让渲染依赖网络。
    if args.gsap_src:
        gsap_src = args.gsap_src
        _warn_if_local_vendor_missing("GSAP", gsap_src, out_dir)
    else:
        gsap_src = ensure_local_gsap(out_dir, allow_network=_allow_network())
        if gsap_src is None:
            raise SystemExit(
                f"[error] 找不到可用的 GSAP：技能包内置副本（{_BUNDLED_GSAP}）与"
                "用户缓存都不存在或与钉固字节不符。技能包通常自带这个文件——"
                "若确实缺失，重新获取技能包；或设置 CTV_ALLOW_NETWORK_ASSETS=1 "
                "从钉固 CDN 获取一次；或用 --gsap-src 指向本地已有文件。"
            )

    has_chart = any(media_needs_chartjs(v) for v in images.values())
    chartjs_src = args.chartjs_src
    if chartjs_src:
        _warn_if_local_vendor_missing("Chart.js", chartjs_src, out_dir)
    elif has_chart:
        chartjs_src = ensure_local_chartjs(out_dir, allow_network=_allow_network())
        if chartjs_src is None:
            raise SystemExit(
                f"[error] 检测到 Chart.js 图表，但找不到可用的脚本：技能包内置副本"
                f"（{_BUNDLED_CHARTJS}）与用户缓存都不存在或与钉固字节不符。"
                "技能包通常自带这个文件——若确实缺失，重新获取技能包；或设置 "
                "CTV_ALLOW_NETWORK_ASSETS=1 从钉固 CDN 获取一次；或用 "
                "--chartjs-src 指向本地已有文件。"
            )

    # 画幅：portrait（默认，1080×1440 竖屏）/ landscape（1920×1080 横屏），
    # 单一画幅出单一 HTML；下游（get_canvas / generate_html）自带
    # portrait→vertical 归一化，画布尺寸随画幅从模板取。
    aspect = args.aspect
    w, h = get_canvas(aspect)

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
        if isinstance(audio_abs, str) and audio_abs:
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
                         width=w, height=h, gsap_src=gsap_src, chartjs_src=chartjs_src,
                         aspect=aspect, theme=args.theme, fps=args.fps)

    # 原子写：index.html 是渲染输入，写到一半被打断会留下半份 HTML——
    # render 会报莫名其妙的语法错，而不是"上次生成中断了，重跑"。
    write_text_atomic(args.output, html)

    # 复制预览脚本到 HTML 输出目录（index.html 引用同目录 preview.js）
    preview_js = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "preview.js")
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
        print("[warn] 未找到 scripts/preview.js，浏览器预览不可用"
              "（渲染不受影响）", file=sys.stderr)

    seg_count = len(manifest.get("segments", []))
    print(f"[OK] {args.output} ({len(html)} bytes)")
    print(f"     Duration: {manifest['total_duration']}s")
    print(f"     Segments: {seg_count if seg_count else 'auto-grouped'}")
    print(f"     Sentences: {len(manifest['sentences'])}")
    print(f"     Aspect: {aspect} ({w}x{h})")
    print(f"     Theme: {args.theme}")
    print(f"     FPS: {args.fps}")
    print(f"     Images: {len(images)}")
    print(f"     Audio src: {audio_src}")
    print(f"     GSAP src: {gsap_src}")


if __name__ == "__main__":
    main()
