#!/usr/bin/env python3
"""媒体像素尺寸：问 Pillow 要，没有 Pillow 就退回读文件头（不解码）。

配图一律 `cover` 铺满画布，而 `cover` 只保证填满、不保证够清楚——生成期、
`hyperframes check`、渲染日志全都查不到"够不够大"。本模块补这两问：

1. **放大几倍**：素材固有像素尺寸比它要铺进去的框，算出 `cover` 的倍率，超阈值出声。
2. **裁掉多少**：cover 裁的是比例不一致的部分（不是 contain 留黑边），
   ImageGen 三档里没有 4:3，按竖屏比例出图铺进 4:3 槽会裁掉一半画面。

两条判据互相独立（放大倍率 × 裁切占比），各响各的。**判据与阈值只收在本模块**，
别在调用点再写一份 `max(w0/w1, w0/h1)`——两处各算一遍会一个报警一个不报。

**矢量不验**：SVG 缩放不失真，另有比例与字号两道门禁
（`gen_hyperframes.canvas_layout_errors`）。

**Pillow 是主路径**：手写头解析只认 PNG/JPEG/WebP/GIF 四种，而"读不出"在这里
等于静默放行——AVIF/HEIC/JPEG XL/TIFF 会绕过全部判据。Pillow 一条 `Image.open`
覆盖全部格式，读惰性头不解码像素。没有 Pillow 才退回头解析。

**尺寸是"显示尺寸"**：手机竖拍的照片常把像素横着存（400×300）、再把 EXIF
`Orientation` 写成 6/8 让浏览器转 90° 显示。`<img>` 按 EXIF 转，所以真正铺进
槽位的是 300×400——若照存储尺寸算，一张"会被裁掉 44%"的竖图会被算成同比例、
一声不吭。两条路都要把 5–8 的宽高交换（Pillow 问 `getexif`，头解析读 APP1 的
IFD0 0x0112）；只交换尺寸，不做像素旋转——这里从不解码。
"""
import struct
from fractions import Fraction

try:  # 主路径：Pillow（可选依赖，见模块说明）
    from PIL import Image as _PILImage
except Exception:  # noqa: BLE001  装了但加载失败（缺 libjpeg 之类）同样退回头解析
    _PILImage = None

# cover 铺满的放大倍率阈值。
# 1.0 是"刚好铺满"；1.25 以下留作不报——出图端常见的 1024×768、1024×1024 落在
# 0.95~1.1 之间，把它们也报出来只会把真正的告警淹没（实测一批素材里这类占比不低）。
UPSCALE_WARN = 1.25
# 超过这一档是"明显糊"：素材面积不到画布的 1/4，压缩编码之后细节基本没了。
UPSCALE_SEVERE = 2.0

# 头解析要读的前缀长度。JPEG 的 SOF 段紧跟在 APPn/EXIF 之后，实测 64KB 足够
# 覆盖正常文件的全部前置段；读不到就当"读不出尺寸"（不报，不猜）。
_HEADER_READ = 65536

# 比例失配的裁切告警阈值：cover 铺满时被裁掉的**面积占比**超过这一档就出声。
# 20% 卡在 ImageGen 三档与 4:3 槽位的真实差距之间——最接近的 1536×1024 只裁
# 11%（推荐档，不吵），方图 1024×1024 裁 25%、竖图 1024×1536 裁 50%（误选档，
# 必须喊）。裁切占比与分辨率无关（同比例放大裁一样多），所以这条和 UPSCALE_*
# 各管一件事：UPSCALE 问"糊不糊"，这条问"构图丢多少"。
CROP_WARN = 0.20


def _be16(buf, off):
    return struct.unpack_from(">H", buf, off)[0]


def _be32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


def _le16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def _png_size(buf):
    """PNG：IHDR 块里两个大端 u32（宽在前）。"""
    # 24 = 8 签名 + 4 长度 + 4 "IHDR" + 8 宽高。截断文件走到这里 unpack_from
    # 会抛 struct.error，而调用方契约是"读不出返回 None"（崩溃不是降级）。
    if len(buf) < 24 or buf[:8] != b"\x89PNG\r\n\x1a\n" or buf[12:16] != b"IHDR":
        return None
    return _be32(buf, 16), _be32(buf, 20)


def _gif_size(buf):
    """GIF：逻辑屏描述符里两个小端 u16（宽在前）。"""
    if len(buf) < 10 or buf[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return _le16(buf, 6), _le16(buf, 8)


def _exif_orientation(payload):
    """APP1 段载荷里的 EXIF `Orientation`（1–8）；不是 EXIF、读不出、值非法返回 None。

    只认 IFD0 的 0x0112（SHORT）。5–8 是"显示时转 90°/270°"（含镜像），像素被
    横着存——调用方据此交换宽高。所有长度都先查再 unpack：截断的 APP1 是正常文件
    （相机在段末截断过），不该让整张图变成"读不出"。
    """
    if len(payload) < 14 or payload[:6] != b"Exif\x00\x00":
        return None
    tiff = payload[6:]
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return None
    try:
        if struct.unpack_from(endian + "H", tiff, 2)[0] != 42:
            return None
        off = struct.unpack_from(endian + "I", tiff, 4)[0]
        if off + 2 > len(tiff):
            return None
        count = struct.unpack_from(endian + "H", tiff, off)[0]
        for i in range(count):
            entry = off + 2 + i * 12
            if entry + 12 > len(tiff):
                return None
            if struct.unpack_from(endian + "H", tiff, entry)[0] != 0x0112:
                continue
            if struct.unpack_from(endian + "H", tiff, entry + 2)[0] != 3:
                return None       # Orientation 按规范是 SHORT；别的类型不猜
            val = struct.unpack_from(endian + "H", tiff, entry + 8)[0]
            return val if 1 <= val <= 8 else None
    except struct.error:
        return None
    return None


def _jpeg_size(buf):
    """JPEG：扫段找 SOFn；尺寸按 EXIF Orientation 换算成显示宽高。

    只认 0xC0~0xCF 里除 C4（DHT）、C8（JPG）、CC（DAC）以外的帧起始标记，
    其余（APPn / DQT / EXIF…）按段长跳过。渐进式 JPEG 的 SOF2 同样命中。
    """
    if buf[:2] != b"\xff\xd8":
        return None
    pos = 2
    n = len(buf)
    orientation = 1
    # `<= n` 不是 `< n`：SOF 要读到 buf[pos+8]，差一个字节就会把正好落在
    # 读块末端的 SOF 判成"读不出"——而"读不出"在这模块里等于静默放行。
    while pos + 9 <= n:
        if buf[pos] != 0xFF:
            # 不是段边界：扫到下一个 0xFF（熵编码数据段里不会有 FF 后跟 0x00
            # 以外的序列，靠这个吃掉它们）
            pos += 1
            continue
        marker = buf[pos + 1]
        if marker == 0xFF:
            # 填充字节：JPEG 允许一个标记前挂任意多个 FF。当成"带长度的段"去跳
            # 会拿填充字节后的数据当段长，一步迈到十万八千里外，整张合法的图
            # 被判成"读不出"。
            pos += 1
            continue
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2          # 无长度字段的独立标记
            continue
        if marker in (0xC4, 0xC8, 0xCC) or not (0xC0 <= marker <= 0xCF):
            if marker == 0xE1:
                # APP1 里可能是 EXIF（也可能只是 XMP）：载荷不认就保持原方向。
                seg_end = pos + 2 + _be16(buf, pos + 2)
                if seg_end <= n:
                    got = _exif_orientation(buf[pos + 4:seg_end])
                    if got is not None:
                        orientation = got
            pos += 2 + _be16(buf, pos + 2)
            continue
        w, h = _be16(buf, pos + 7), _be16(buf, pos + 5)   # SOF：高在前
        return (h, w) if orientation in (5, 6, 7, 8) else (w, h)
    return None


def _webp_size(buf):
    """WebP：RIFF 容器，按 VP8 / VP8L / VP8X 三种块分别取。

    - VP8（有损）：3 字节帧头 + 3 字节同步码之后两个 14 位小端整数；
    - VP8L（无损）：1 字节签名之后连着两个 14 位（按位流读，都要 +1）；
    - VP8X（扩展）：1 字节标志 + 3 字节保留，之后两个 24 位小端（都要 +1）。
    """
    if buf[:4] != b"RIFF" or buf[8:12] != b"WEBP":
        return None
    kind = buf[12:16]
    if kind == b"VP8 " and len(buf) >= 30:
        return _le16(buf, 26) & 0x3FFF, _le16(buf, 28) & 0x3FFF
    if kind == b"VP8L" and len(buf) >= 25:
        bits = int.from_bytes(buf[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X" and len(buf) >= 30:
        return (int.from_bytes(buf[24:27], "little") + 1,
                int.from_bytes(buf[27:30], "little") + 1)
    return None


# 扩展名 → 解析器。只收渲染端真会遇到的栅格格式；SVG 不在此列（矢量不验）。
# 没有 Pillow 时这是唯一路径：读不出的格式（avif/heic/jxl…）当"没法验"，
# 有 Pillow 时它们走 _pil_size，不会走到这里。
_PARSERS = (_png_size, _jpeg_size, _webp_size, _gif_size)


def _pil_size(path):
    """问 Pillow 要**显示尺寸**；没装、打不开、返回非正数都返回 None。

    `Image.size` 是存储尺寸，不替 EXIF 转。手机竖拍图（存储 400×300、Orientation=6）
    在浏览器里显示成 300×400，照存储尺寸算会把"裁掉 44%"算成"同比例"——一条该响的
    告警静默消失。这里只交换宽高，不解码像素（`Image.open` 仍是惰性头）。
    """
    if _PILImage is None:
        return None
    try:
        # with 打开：不 close 会留着文件句柄，Windows 上随后删临时目录会失败
        with _PILImage.open(path) as im:
            w, h = im.size
            try:
                orientation = int(im.getexif().get(0x0112, 1) or 1)
            except Exception:  # noqa: BLE001  EXIF 坏了按"没写方向"处理
                orientation = 1
    except Exception:  # noqa: BLE001  Pillow 的异常类型按格式各不相同，一律"读不出"
        return None
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    return (int(w), int(h)) if w > 0 and h > 0 else None


def raster_size(path):
    """栅格图的**显示**像素尺寸 (w, h)（EXIF Orientation 已应用）；读不出返回 None。

    Pillow 先答（覆盖全部格式），没有 Pillow 才按头解析四种常见格式。
    不解码、不校验——"图是不是坏的"由 `gen_hyperframes` 的 ffmpeg 全解码探测
    管，这里只回答"它在屏幕上占多少像素"。调用方对 None 按"没法验"处理（不报，不猜）。
    """
    got = _pil_size(path)
    if got:
        return got
    try:
        with open(path, "rb") as f:
            buf = f.read(_HEADER_READ)
    except OSError:
        return None
    if not buf:
        return None
    for parse in _PARSERS:
        # 任何一个头解析抛异常都按"这一个格式读不出"处理，而不是让整步崩掉：
        # 契约是"读不出返回 None"，畸形/截断的素材不该掀掉生成。
        try:
            got = parse(buf)
        except Exception:  # noqa: BLE001
            continue
        if got and got[0] > 0 and got[1] > 0:
            return int(got[0]), int(got[1])
    return None


def _fit_ratios(box, size):
    """两个方向的适配比 (bw/sw, bh/sh)；比不了返回 (None, None)。

    cover 的放大倍率与被裁占比都从这两个比算出来，前置校验也就只该有一份：
    各写一遍 `min(...) <= 0` 的代价是改一边漏一边，一个函数悄悄开始接受 (0,0)。
    """
    if not box or not size:
        return None, None
    if min(box[0], box[1], size[0], size[1]) <= 0:
        return None, None   # 任一边为 0/负 → 没有可比的比例（(0,0) 这种哨兵值也是）
    return (float(box[0]) / float(size[0]), float(box[1]) / float(size[1]))


def cover_scale(box, size):
    """`object-fit: cover` 把 size 铺进 box 时的放大倍率（1.0 = 刚好铺满）。

    cover 取两个方向的较大者：一边刚好贴住，另一边溢出被裁。小于 1 表示素材
    比框大（缩小，不糊）。box 或 size 缺失返回 None——没得比就不比。
    """
    rw, rh = _fit_ratios(box, size)
    if rw is None:
        return None
    return max(rw, rh)


def upscale_note(src, size, box):
    """放大倍率超阈值时的人话说明；不用出声返回 None。

    两档措辞不同：刚过阈值只是"会软一点"，过 UPSCALE_SEVERE 是"糊到细节没了"。
    都给可执行的去处（换够大的图，或改走 SVG 矢量）——只说"图太小"等于没说。
    """
    scale = cover_scale(box, size)
    if scale is None or scale <= UPSCALE_WARN:
        return None
    sw, sh = int(size[0]), int(size[1])
    bw, bh = int(box[0]), int(box[1])
    _severe = scale > UPSCALE_SEVERE
    return (f"{src} 只有 {sw}×{sh} 像素，铺进 {bw}×{bh} 要放大 {scale:.2f} 倍"
            + ("（糊到细节基本没了）——" if _severe else "（会软一点）——")
            + f"换成 ≥ {bw}×{bh} 的素材"
            + ("，或改走手绘 SVG（矢量放大不失真）。"
               if _severe else "；内容是数据/结构就改走手绘 SVG。"))


def cover_crop_fraction(box, size):
    """`object-fit: cover` 铺满时被裁掉的**面积占比**（0 = 同比例不裁）。

    公式：cover 先把图缩到"一边刚好贴住框"，另一边溢出。设两个方向的适配比为
    r_w = bw/sw、r_h = bh/sh，缩放取较大者，于是保留面积占比 = min/max——
    例如 1536×1024 进 980×735：min/max = (980/1536)/(735/1024) ≈ 0.889，裁掉
    11.1%。占比与绝对分辨率无关（同比例放大裁得一样多），纯粹量构图丢多少。

    box 或 size 缺失、任一边为 0/负 返回 None——没得比就不比。
    """
    r_w, r_h = _fit_ratios(box, size)
    if r_w is None:
        return None
    return 1.0 - min(r_w, r_h) / max(r_w, r_h)


def _box_ratio(bw, bh):
    """目标框比例：化简后的 (分子, 分母) 与人话标签，**只从 box 自己算**。

    这条 warn 同时服务两种框：槽位版式的 4:3 图槽（`get_image_box`），以及
    整页画布版式铺的整幅画幅（`get_canvas`，竖屏 3:4 / 横屏 16:9）。把框
    写成"4:3 横版"只对第一种成立；满幅段会收到一句既是假描述、又是错建议
    的告警——见 `aspect_note` 的说明。`limit_denominator(16)` 把按整数像素
    取整过的 1067×800 收敛回 4/3，而不是报一个没人认得的 1067:800。
    """
    f = Fraction(int(bw), int(bh)).limit_denominator(16)
    orient = "横版" if bw > bh else ("竖版" if bw < bh else "方版")
    return (f.numerator, f.denominator), f"{f.numerator}:{f.denominator} {orient}"


def aspect_note(src, size, box):
    """素材比例与目标框比例不一致、裁掉面积超阈值时的人话说明；不出声返回 None。

    必须讲清三件事，缺一件这条 warn 就白打：
    1. **是裁边不是留黑边**——渲染端是 `object-fit: cover`，作者按"contain 会
       容纳"的预期出竖图，成片里看到的是上下被切掉的构图；
    2. **裁掉的是哪一块由 cover 定，不由作者定**——框居中、图居中，溢出的部分
       上下/左右对称裁掉，想保住主体只能入库前自己裁（去水印同一步）；
    3. **说的是这一个框**——`box` 可能是 4:3 图槽，也可能是整幅画幅。此前
       两边都写成"槽位是 … 的 4:3 横版"并推荐 1536×1024：一张 1536×1024
       铺进 1080×1440 的满幅竖版会被左右裁掉 50%，而告警一边报这 50%、
       一边把同一张图当解法推荐。描述与去处都得跟着 box 走，别替调用方猜。
    """
    crop = cover_crop_fraction(box, size)
    if crop is None or crop <= CROP_WARN:
        return None
    sw, sh = int(size[0]), int(size[1])
    bw, bh = int(box[0]), int(box[1])
    src_ratio = sw / sh
    box_ratio = bw / bh
    if abs(src_ratio - 1.0) <= 0.05:
        shape = "方图"
    elif src_ratio < box_ratio:
        shape = "竖图"
    else:
        shape = "宽图"
    # 图相对更窄更高 → 缩放后宽先贴住、高溢出 → 裁上下；反之裁左右。
    side = "上下" if src_ratio < box_ratio else "左右"
    pair, label = _box_ratio(bw, bh)
    # 1536×1024 是能力端三档里离 4:3 最近的一档，只对 4:3 图槽成立。满幅
    # 画幅没有对应的三档，照它出图只会裁得更多——那里给同比例出图的去处。
    if pair == (4, 3):
        advice = "出图选最接近 4:3 的档位（如 1536×1024）"
    else:
        advice = (f"出图按目标框的 {pair[0]}:{pair[1]} 比例来"
                  f"（如 {bw}×{bh} 或更大的同比例素材），别照 4:3 槽位的档位出")
    return (f"{src} 是 {shape} {sw}×{sh}，目标框是 {bw}×{bh} 的 {label}——"
            f"渲染端 cover 会**{side}裁掉约 {crop * 100:.0f}% 的画面**"
            f"（居中对称裁边，不是 contain 留黑边）。"
            f"{advice}，或入库前自己裁成 {bw}:{bh} 保住主体")
