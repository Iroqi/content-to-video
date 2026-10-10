#!/usr/bin/env python3
"""媒体像素尺寸：只读文件头，不引 Pillow / 不解码。

存在的理由是一条**此前无人拦的静默退化**：配图一律 `cover` 铺满画布（见
`references/image_options.md`「统一使用同一个内容画布」），`cover` 只保证填满，
不保证够清楚——一张 800×600 的照片塞进横屏 1067×800 的图栏要放大 1.33 倍，
成片里那一段就是糊的，而生成期、`hyperframes check`、渲染日志**全都一声不吭**
（门禁查的是"图在不在、能不能解码"，不是"够不够大"）。

所以这里补上尺寸那一问：拿素材的固有像素尺寸跟它要被铺进去的那个框比，算出
`cover` 到底要放大几倍，超过阈值就出声。判据与阈值收口在本模块，别在调用点
再写一份 `max(w0/w1, w0/h1)`——两处各算一遍就会一个报警一个不报。

**矢量不验**：SVG 缩放不失真，它另有比例与字号两道门禁（见
`gen_hyperframes.canvas_layout_errors`），这里一律跳过。
"""
import struct

# cover 铺满的放大倍率阈值。
# 1.0 是"刚好铺满"；1.25 以下留作不报——出图端常见的 1024×768、1024×1024 落在
# 0.95~1.1 之间，把它们也报出来只会把真正的告警淹没（实测一批素材里这类占比不低）。
UPSCALE_WARN = 1.25
# 超过这一档是"明显糊"：素材面积不到画布的 1/4，压缩编码之后细节基本没了。
UPSCALE_SEVERE = 2.0

# 头解析要读的前缀长度。JPEG 的 SOF 段紧跟在 APPn/EXIF 之后，实测 64KB 足够
# 覆盖正常文件的全部前置段；读不到就当"读不出尺寸"（不报，不猜）。
_HEADER_READ = 65536


def _be16(buf, off):
    return struct.unpack_from(">H", buf, off)[0]


def _be32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


def _le16(buf, off):
    return struct.unpack_from("<H", buf, off)[0]


def _png_size(buf):
    """PNG：IHDR 块里两个大端 u32（宽在前）。"""
    if buf[:8] != b"\x89PNG\r\n\x1a\n" or buf[12:16] != b"IHDR":
        return None
    return _be32(buf, 16), _be32(buf, 20)


def _gif_size(buf):
    """GIF：逻辑屏描述符里两个小端 u16（宽在前）。"""
    if buf[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return _le16(buf, 6), _le16(buf, 8)


def _jpeg_size(buf):
    """JPEG：扫段找 SOFn。

    只认 0xC0~0xCF 里除 C4（DHT）、C8（JPG）、CC（DAC）以外的帧起始标记，
    其余（APPn / DQT / EXIF…）按段长跳过。渐进式 JPEG 的 SOF2 同样命中。
    """
    if buf[:2] != b"\xff\xd8":
        return None
    pos = 2
    n = len(buf)
    while pos + 9 < n:
        if buf[pos] != 0xFF:
            # 不是段边界：扫到下一个 0xFF（熵编码数据段里不会有 FF 后跟 0x00
            # 以外的序列，靠这个吃掉它们）
            pos += 1
            continue
        marker = buf[pos + 1]
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2          # 无长度字段的独立标记
            continue
        if marker in (0xC4, 0xC8, 0xCC) or not (0xC0 <= marker <= 0xCF):
            pos += 2 + _be16(buf, pos + 2)
            continue
        return _be16(buf, pos + 7), _be16(buf, pos + 5)   # SOF：高在前
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


# 扩展名 → 解析器。只收渲染端真会遇到的栅格格式；SVG 不在此列（矢量不验），
# 其余（.avif/.heic 之类）浏览器未必认、ffmpeg 兜得住，读不出来就当"没法验"。
_PARSERS = (_png_size, _jpeg_size, _webp_size, _gif_size)


def raster_size(path):
    """栅格图的固有像素尺寸 (w, h)；读不出返回 None。

    不解码、不校验——"图是不是坏的"由 `gen_hyperframes.validate_images_files`
    的 ffmpeg 全解码探测管，这里只回答"它有多少像素"。四种格式按顺序试，
    命中即返回；都不命中返回 None，调用方按"没法验"处理（不报，不猜）。
    """
    try:
        with open(path, "rb") as f:
            buf = f.read(_HEADER_READ)
    except OSError:
        return None
    if not buf:
        return None
    for parse in _PARSERS:
        got = parse(buf)
        if got and got[0] > 0 and got[1] > 0:
            return int(got[0]), int(got[1])
    return None


def cover_scale(box, size):
    """`object-fit: cover` 把 size 铺进 box 时的放大倍率（1.0 = 刚好铺满）。

    cover 取两个方向的较大者：一边刚好贴住，另一边溢出被裁。小于 1 表示素材
    比框大（缩小，不糊）。box 或 size 缺失返回 None——没得比就不比。
    """
    if not box or not size:
        return None
    if min(box[0], box[1], size[0], size[1]) <= 0:
        return None   # 任一边为 0/负 → 没有可比的比例（(0,0) 这种哨兵值也是）

    return max(float(box[0]) / float(size[0]), float(box[1]) / float(size[1]))


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
            + ("，或改走方式 C 手绘 SVG（矢量放大不失真）。"
               if _severe else "；内容是数据/结构就改走方式 C 手绘 SVG。"))
