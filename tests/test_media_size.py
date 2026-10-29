"""配图像素够不够：头解析 / cover 放大倍率 / cover 裁切占比 / ffmpeg 视频尺寸解析 / 生成期告警。

这一组守的是两条此前**零报错**的静默退化：配图一律 `cover` 铺满画布，而
`cover` 只管填满、不管清楚——一张不够大的素材被放大成糊的；比例不一致的素材
被裁掉一大块（cover 裁边，不是 contain 留黑边；ImageGen 又没有 4:3 档）。两件事
在生成期、`hyperframes check`、渲染日志里全都一声不吭（它们查的是"图在不在、
能不能解码"），只有成片里看得见。所以这两问必须在生成期出声。
"""
import contextlib
import io
import os
import shutil
import struct
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
from _media_size import (raster_size, cover_scale, upscale_note,
                         cover_crop_fraction, aspect_note,
                         UPSCALE_WARN, UPSCALE_SEVERE, CROP_WARN,
                         _jpeg_size as raster_jpeg_size)
from _audio import parse_video_size
from _template import get_image_box, get_canvas
import gen_hyperframes as GH


def _png(w, h):
    """最小 PNG 头：签名 + IHDR 的两个大端 u32（后面不补足真实数据也够读尺寸）。"""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00")


def _jpeg(w, h):
    """最小 JPEG：SOI + 一个 APP0 段 + SOF0（高在前，宽在后）。"""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x01\x01\x00" \
        + struct.pack(">HH", 1, 1) + b"\x00\x00"
    sof0 = (b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", h, w) + b"\x03" + b"\x01\x11\x00" * 3)
    return b"\xff\xd8" + app0 + sof0


def _gif(w, h):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00"


def _webp_vp8x(w, h):
    """扩展型 WebP：VP8X 块里两个 24 位小端（存的是尺寸-1）。"""
    def u24(v):
        return (v - 1).to_bytes(3, "little")
    body = b"\x00" + b"\x00\x00\x00" + u24(w) + u24(h)
    return (b"RIFF" + struct.pack("<I", 12 + len(body)) + b"WEBP" + b"VP8X"
            + struct.pack("<I", len(body)) + body)


class RasterHeaderParsing(unittest.TestCase):
    """只读文件头取像素尺寸——不引 Pillow、不解码（CI 上没有图像库）。"""

    def _size(self, data, suffix=".png"):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f" + suffix)
            with open(p, "wb") as f:
                f.write(data)
            return raster_size(p)

    def test_png(self):
        self.assertEqual(self._size(_png(800, 600)), (800, 600))

    def test_jpeg(self):
        self.assertEqual(self._size(_jpeg(1920, 1080), ".jpg"), (1920, 1080))

    def test_jpeg_progressive_sof2(self):
        """渐进式 JPEG 的帧起始标记是 0xC2，同样要认。"""
        data = _jpeg(640, 480).replace(b"\xff\xc0", b"\xff\xc2")
        self.assertEqual(self._size(data, ".jpg"), (640, 480))

    def test_jpeg_with_fill_bytes(self):
        """标记前的 0xFF 填充字节（规范允许一个标记前挂任意多个）不是段长。

        修复前把填充字节后的两个字节当段长读，一步迈出缓冲区，一张合法的图被
        判成"读不出"——而"读不出"在这条校验链里是静默放行（不报也不猜）。
        直接测解析单元：走 raster_size 会被 Pillow 主路径掩盖（它认得填充字节）。
        """
        import _media_size as M
        base = _jpeg(1920, 1080)
        self.assertEqual(M._jpeg_size(b"\xff\xd8" + b"\xff\xff\xff" + base[2:]),
                         (1920, 1080))

    def test_gif(self):
        self.assertEqual(self._size(_gif(320, 240), ".gif"), (320, 240))

    def test_truncated_headers_return_none_instead_of_raising(self):
        """截断的头必须返回 None，不能抛。

        契约是"读不出尺寸 → None"（调用方按"没法验"处理）。struct.unpack_from
        越界抛的是 struct.error，那会掀掉整个生成步骤——降级变成了崩溃。
        走解析单元：raster_size 的 Pillow 主路径会先把它接住，掩盖这条。
        """
        import _media_size as M
        for parse, buf in (
            (M._png_size, b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR"
             + struct.pack(">I", 980)),           # 20 字节：只有宽度，没有高度
            (M._gif_size, b"GIF89a\xd4\x03"),     # 8 字节：只有宽度
        ):
            self.assertIsNone(parse(buf))
        # 没人接住的那道兜底：解析单元自己抛了，raster_size 仍只返回 None
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.png")
            with open(p, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"IHDR")
            self.assertIsNone(raster_size(p))

    def test_webp(self):
        self.assertEqual(self._size(_webp_vp8x(1024, 768), ".webp"), (1024, 768))

    def test_unknown_bytes_return_none(self):
        """认不出的文件返回 None（调用方按"没法验"处理，不猜、不报）。"""
        self.assertIsNone(self._size(b"not an image at all"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(raster_size("/nonexistent/nope.png"))


class PillowAndFallback(unittest.TestCase):
    """尺寸读取两条路径：Pillow 主路径（覆盖全部格式）与无 Pillow 时的头解析兜底。

    这里改的是一条**覆盖面**契约：手写头解析只认 PNG/JPEG/WebP/GIF，"读不出"在本
    模块等于**静默放行**——AVIF/TIFF 这类浏览器与 ffmpeg 都认的素材会绕过"像素够
    不够、被裁多少"两道告警。Pillow 一条 `Image.open` 把它们全部收进来；兜底路径仍
    要保住，否则"装不上依赖"会静默退化成"整段校验失效"。
    """

    def setUp(self):
        import _media_size as M
        self.M = M
        # 每条用例后恢复真实可用性，别把"假装没装"泄漏给别的用例
        self.addCleanup(setattr, M, "_PILImage", M.__dict__["_PILImage"])

    def _write(self, fmt, size, suffix):
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001
            self.skipTest("本机没有 Pillow，主路径无从测起（降级路径另有用例）")
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = os.path.join(d, "f" + suffix)
        Image.new("RGB", size, (10, 20, 30)).save(p, format=fmt)
        return p

    def test_pillow_reads_formats_the_header_parser_does_not(self):
        """AVIF/TIFF：内置头解析一个都不认，Pillow 全认得。"""
        for fmt, suffix in (("AVIF", ".avif"), ("TIFF", ".tiff")):
            with self.subTest(fmt=fmt):
                p = self._write(fmt, (1024, 768), suffix)
                self.assertEqual(self.M.raster_size(p), (1024, 768))

    def test_without_pillow_the_builtin_headers_still_answer(self):
        """把 Pillow 摘掉：PNG/JPEG 照旧答得出，格式外的一如既往返回 None。"""
        self.M._PILImage = None
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = os.path.join(d, "a.png")
        with open(p, "wb") as f:
            f.write(_png(800, 600))
        self.assertEqual(self.M.raster_size(p), (800, 600))
        p2 = os.path.join(d, "a.tiff")
        with open(p2, "wb") as f:
            f.write(b"II*\x00\x08\x00\x00\x00")     # TIFF 头，内置解析不认
        self.assertIsNone(self.M.raster_size(p2))

    def test_pillow_failure_falls_back_instead_of_raising(self):
        """Pillow 在、但文件它打不开（截断/伪造）时，必须降级而不是抛异常。

        伪造一个 PNG 头（尺寸可读、像素数据不存在）：Pillow 报 truncated，
        内置头解析仍能给出尺寸——校验不能因为"解码器不高兴"就整段失效。
        """
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = os.path.join(d, "fake.png")
        with open(p, "wb") as f:
            f.write(_png(640, 480))          # 只有 33 字节头
        self.assertEqual(self.M.raster_size(p), (640, 480))


class VideoSizeFromFfmpeg(unittest.TestCase):
    """视频尺寸只能问 ffmpeg（与时长同一条路：那次全解码探测已经在跑了）。"""

    def test_parses_stream_line(self):
        err = ("Input #0, mov,mp4, from 'a.mp4':\n"
               "  Stream #0:0[0x1](eng): Video: h264 (avc1), 1920x1080, "
               "SAR 1:1 DAR 16:9, 24 fps\n"
               "  Stream #0:1(eng): Audio: aac (mp4a), 24000 Hz, mono\n")
        self.assertEqual(parse_video_size(err), (1920, 1080))

    def test_takes_encoded_pixels_not_display_ratio(self):
        """DAR 里也有一个 `16:9`，但那不是像素——取流信息行的第一个 WxH。"""
        err = "Stream #0:0: Video: h264, 1280x720 [SAR 1:1 DAR 16:9]\n"
        self.assertEqual(parse_video_size(err), (1280, 720))

    def test_audio_only_returns_none(self):
        self.assertIsNone(parse_video_size(
            "Stream #0:0: Audio: aac, 24000 Hz, mono\n"))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_video_size(""))


class CoverScale(unittest.TestCase):
    """cover 铺满的放大倍率：1.0 = 刚好铺满，>1 = 被放大（会糊）。"""

    def test_exact_fit_is_one(self):
        self.assertAlmostEqual(cover_scale((980, 735), (980, 735)), 1.0)

    def test_takes_the_larger_axis(self):
        """cover 取两个方向的较大者：宽要先撑满，高这边即使已经够也不再放大。"""
        self.assertAlmostEqual(cover_scale((980, 735), (490, 735)), 2.0)
        self.assertAlmostEqual(cover_scale((980, 735), (1960, 735)), 1.0)

    def test_bigger_source_is_below_one(self):
        self.assertAlmostEqual(cover_scale((980, 735), (1960, 1470)), 0.5)

    def test_missing_inputs_return_none(self):
        for box, size in (((0, 0), (10, 10)), ((10, 10), None),
                          (None, (10, 10)), ((10, 10), (0, 10))):
            self.assertIsNone(cover_scale(box, size), (box, size))


class UpscaleNote(unittest.TestCase):
    """阈值分两档，且必须给出可执行的去处。"""

    def test_silent_when_source_is_big_enough(self):
        self.assertIsNone(upscale_note("a.png", (980, 735), (980, 735)))
        self.assertIsNone(upscale_note("a.png", (1024, 768), (980, 735)))

    def test_silent_below_warn_threshold(self):
        """1.25 以下不报：出图端常见的 1024 档落在这一带，报出来只会淹没真告警。"""
        scale = UPSCALE_WARN - 0.01
        size = (int(980 / scale), 735)
        self.assertIsNone(upscale_note("a.png", size, (980, 735)))

    def test_warns_above_threshold(self):
        note = upscale_note("a.png", (400, 300), (980, 735))
        self.assertIsNotNone(note)
        self.assertIn("400×300", note)
        self.assertIn("980×735", note)
        self.assertIn("放大", note)

    def test_severe_wording_above_severe_threshold(self):
        """超过 UPSCALE_SEVERE 是"细节基本没了"，措辞要比"会软一点"硬。"""
        size = (int(980 / (UPSCALE_SEVERE + 0.5)), 735)
        note = upscale_note("a.png", size, (980, 735))
        self.assertIn("细节基本没了", note)

    def test_always_offers_a_way_out(self):
        """只说"图太小"等于没说：要么换够大的素材，要么改走矢量。"""
        for size in ((400, 300), (100, 100)):
            note = upscale_note("a.png", size, (980, 735))
            self.assertIn("SVG", note, size)

    def test_silent_when_size_unknown(self):
        self.assertIsNone(upscale_note("a.png", None, (980, 735)))


class CoverCropFraction(unittest.TestCase):
    """cover 裁掉的面积占比：0 = 同比例不裁，与分辨率无关（同比例放大裁一样多）。"""

    def test_same_aspect_is_zero(self):
        # 用 4:3 的整数倍（980:735 = 1920:1440 = 480:360 精确同比例）
        self.assertAlmostEqual(cover_crop_fraction((980, 735), (1920, 1440)), 0.0)
        self.assertAlmostEqual(cover_crop_fraction((980, 735), (480, 360)), 0.0)

    def test_recommended_tier_crops_about_eleven_percent(self):
        """1536×1024 是离 4:3 最近的档位：只裁 ~11%，低于阈值不该出声。"""
        crop = cover_crop_fraction((980, 735), (1536, 1024))
        self.assertAlmostEqual(crop, 1 - (980 / 1536) / (735 / 1024), places=6)
        self.assertLess(crop, CROP_WARN)

    def test_square_and_portrait_tiers(self):
        self.assertAlmostEqual(cover_crop_fraction((980, 735), (1024, 1024)), 0.25)
        self.assertAlmostEqual(cover_crop_fraction((980, 735), (1024, 1536)), 0.5)

    def test_landscape_slot_too(self):
        """横屏槽位同判：1067×800 按 4:3 取整过，1024×1536 进去同样裁一半。"""
        self.assertAlmostEqual(cover_crop_fraction((1067, 800), (1024, 1536)),
                               0.5, places=3)

    def test_resolution_does_not_matter(self):
        """占比只由比例定：放大一倍的同比例图裁得一样多。"""
        self.assertAlmostEqual(cover_crop_fraction((980, 735), (2048, 2048)),
                               cover_crop_fraction((980, 735), (1024, 1024)))

    def test_missing_inputs_return_none(self):
        for box, size in (((0, 0), (10, 10)), ((10, 10), None),
                          (None, (10, 10)), ((10, 10), (0, 10))):
            self.assertIsNone(cover_crop_fraction(box, size), (box, size))


class AspectNote(unittest.TestCase):
    """比例失配要出声，且必须说清"裁边不是黑边"和去处。"""

    def test_silent_when_aspect_matches(self):
        self.assertIsNone(aspect_note("a.png", (1536, 1152), (980, 735)))

    def test_silent_below_crop_threshold(self):
        """推荐档位只裁 ~11%：低于 CROP_WARN 不报，免得淹没真告警。"""
        self.assertIsNone(aspect_note("a.png", (1536, 1024), (980, 735)))

    def test_portrait_tier_warns_hard(self):
        """"按竖屏比例出图"是真实踩过的坑：裁一半，必须喊。"""
        note = aspect_note("a.png", (1024, 1536), (980, 735))
        self.assertIsNotNone(note)
        self.assertIn("1024×1536", note)
        self.assertIn("竖图", note)
        self.assertIn("50%", note)

    def test_says_crop_not_black_bars(self):
        """作者按 contain 预期出的图，看到成片会以为渲染坏了——必须点破。"""
        note = aspect_note("a.png", (1024, 1536), (980, 735))
        self.assertIn("不是 contain 留黑边", note)

    def test_square_tier_warns(self):
        note = aspect_note("a.png", (1024, 1024), (980, 735))
        self.assertIn("25%", note)
        self.assertIn("方图", note)

    def test_names_which_sides_get_cropped(self):
        """方图/竖图进 4:3 是**高溢出裁上下**，宽图才裁左右（方向曾写反过）。"""
        note = aspect_note("a.png", (1024, 1024), (980, 735))
        self.assertIn("上下", note)
        note = aspect_note("a.png", (1024, 1536), (980, 735))
        self.assertIn("上下", note)
        note = aspect_note("a.png", (1920, 1080), (980, 735))
        self.assertIn("宽图", note)
        self.assertIn("左右", note)

    def test_offers_a_way_out(self):
        """只说"比例不对"等于没说：换档位，或入库前自己裁。"""
        note = aspect_note("a.png", (1024, 1536), (980, 735))
        self.assertIn("1536×1024", note)
        self.assertIn("入库前", note)

    def test_silent_when_size_unknown(self):
        self.assertIsNone(aspect_note("a.png", None, (980, 735)))


class ImageBoxFromTemplate(unittest.TestCase):
    """槽位框与画幅是两件事——按画幅判会把槽位那一档整批漏报。"""

    def test_portrait_slot_box(self):
        self.assertEqual(get_image_box("portrait"), (980, 735))

    def test_landscape_slot_box(self):
        self.assertEqual(get_image_box("landscape"), (1067, 800))

    def test_slot_box_is_smaller_than_frame(self):
        """槽位只占中间那块 4:3：同样的图在槽位里够用、铺满幅却不够。"""
        for aspect in ("portrait", "landscape"):
            bw, bh = get_image_box(aspect)
            fw, fh = get_canvas(aspect)
            self.assertLess(bw, fw, aspect)
            self.assertLess(bh, fh, aspect)


class UpscaleWarnAtGeneration(unittest.TestCase):
    """生成期真的会把这一条打出来（端到端，落真实文件走 validate_images_files）。"""

    def _run(self, fname, data, box):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, fname), "wb") as f:
                f.write(data)
            buf = io.StringIO()
            with redirect_stderr(buf):
                GH.validate_images_files({"seg-a": {"src": fname}}, d,
                                         seg_boxes={"seg-a": box})
            return buf.getvalue()

    def test_small_raster_warns(self):
        out = self._run("seg-a.png", _png(400, 300), (1067, 800))
        self.assertIn("seg-a", out)
        self.assertIn("不够大", out)
        self.assertIn("放大", out)

    def test_big_enough_raster_stays_silent(self):
        # 只断言"没有尺寸告警"：CI runner 上没有可用 ffmpeg 时，同一段还会打
        # 一条"跳过完整性校验"的降级提示，那是另一件事，不该被这条用例判死。
        out = self._run("seg-a.png", _png(1920, 1440), (1067, 800))
        self.assertNotIn("不够大", out)

    def test_svg_never_checked(self):
        """矢量放大不失真：SVG 另有比例与字号两道门禁，这里一律不验。"""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "seg-a.svg"), "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" '
                        'width="80" height="60"></svg>')
            buf = io.StringIO()
            with redirect_stderr(buf):
                GH.validate_images_files({"seg-a": {"src": "seg-a.svg"}}, d,
                                         seg_boxes={"seg-a": (1920, 1080)})
            self.assertNotIn("不够大", buf.getvalue())

    def test_no_box_means_no_check(self):
        """不知道框多大就不比——拿猜的数报警比不报更糟。"""
        out = self._run("seg-a.png", _png(400, 300), None)
        self.assertNotIn("不够大", out)


class CropWarnAtGeneration(unittest.TestCase):
    """生成期真的会把比例失念打出来（端到端，落真实文件走 validate_images_files）。"""

    def _run(self, fname, data, box):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, fname), "wb") as f:
                f.write(data)
            buf = io.StringIO()
            with redirect_stderr(buf):
                GH.validate_images_files({"seg-a": {"src": fname}}, d,
                                         seg_boxes={"seg-a": box})
            return buf.getvalue()

    def test_portrait_raster_warns_crop(self):
        """"按竖屏比例出图"：铺进 4:3 槽裁一半，生成期必须喊。"""
        out = self._run("seg-a.png", _png(1024, 1536), (980, 735))
        self.assertIn("seg-a", out)
        self.assertIn("比例不贴合", out)
        self.assertIn("50%", out)
        self.assertIn("不是 contain 留黑边", out)

    def test_recommended_tier_stays_silent(self):
        """1536×1024 只裁 ~11%、还要缩小：两条 warn 都不该响。"""
        out = self._run("seg-a.png", _png(1536, 1024), (980, 735))
        self.assertNotIn("比例不贴合", out)
        self.assertNotIn("不够大", out)

    def test_svg_never_crop_checked(self):
        """矢量不验尺寸也不验裁切：它另有比例与字号两道门禁。"""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "seg-a.svg"), "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" '
                        'width="1024" height="1536"></svg>')
            buf = io.StringIO()
            with redirect_stderr(buf):
                GH.validate_images_files({"seg-a": {"src": "seg-a.svg"}}, d,
                                         seg_boxes={"seg-a": (980, 735)})
            self.assertNotIn("比例不贴合", buf.getvalue())



class JpegSizeAtBufferEnd(unittest.TestCase):
    def test_sof_at_exact_end_of_buffer_is_read(self):
        """SOF 正好落在读块末端时要读得出。

        修复前 `while pos + 9 < n` 差一个字节：SOF 段的前 9 字节就含齐了宽高，
        它落在读块末端时 `pos + 9 == n`，旧条件判否 → 返回 None。而"读不出"在这
        个模块里等于**静默放行**"像素够不够 / 会被裁掉多少"两道告警。
        """
        sof = (b"\xff\xc0"                      # SOF0 标记
               + (0x0011).to_bytes(2, "big")    # 段长（解析器不校验，够占位即可）
               + b"\x08"                        # 采样精度
               + (640).to_bytes(2, "big")       # 高在前
               + (480).to_bytes(2, "big"))      # 宽
        # APP0 段长写 2 = 只有长度字段、没有载荷（跳过时 pos += 2 + 2）
        buf = b"\xff\xd8" + b"\xff\xe0\x00\x02" + sof
        self.assertEqual(len(buf), 6 + 9)       # SOF 的 9 字节正好是读块末端
        self.assertEqual(raster_jpeg_size(buf), (480, 640))

    def test_garbage_still_returns_none(self):
        """反向：不是 JPEG 头照样读不出，放宽条件没把坏输入放进来。"""
        self.assertIsNone(raster_jpeg_size(b"\x89PNG\r\n\x1a\n" + bytes(20)))
        self.assertIsNone(raster_jpeg_size(b"\xff\xd8" + bytes(4)))


class VideoSpanProbe(unittest.TestCase):
    """视频素材的时长探测：走得完，且短了要说话。

    起点读取器必须是模块级——它嵌在 `validate_images_files` 里、却由另一个
    顶层函数调用时，任何一条可解码的视频都是 NameError，整条校验崩掉。
    """

    def _run_probe(self, media_start, seg_dur=8.0):
        tmp = tempfile.mkdtemp(prefix="occam4-vid-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = os.path.join(tmp, "proj")
        os.makedirs(out)
        with open(os.path.join(out, "a.mp4"), "wb") as fh:
            fh.write(b"\x00" * 64)          # 存在即可，解码由 mock 顶掉

        done = mock.Mock(returncode=0,
                         stdout=b"",
                         stderr=b"  Duration: 00:00:01.00, start: 0.000000\n")
        with mock.patch.object(GH, "get_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(GH, "ffmpeg_usable", return_value=True), \
             mock.patch("subprocess.run", return_value=done):
            images = {"s1": {"src": "a.mp4", "type": "video",
                             "media_start": media_start}}
            return GH.validate_images_files(images, out, {"s1": seg_dur})

    def test_video_entry_does_not_raise(self):
        """正向：一条视频媒体必须走得完（修复前是 NameError）。"""
        missing, corrupt = self._run_probe(0.0)
        self.assertEqual(missing, [])
        self.assertEqual(corrupt, [])

    def test_short_video_is_reported(self):
        """反向：视频比段落短时判据要真的报出来（不是静默通过）。

        段 8s、视频 1s、media_start 0 ⇒ 只剩 1s，不够 ⇒ 该报。
        """
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._run_probe(0.0)
        self.assertIn("不够段落", err.getvalue())

    def test_media_start_is_module_level(self):
        """起点读取函数必须在模块级（顶层函数能取到的那一层）。"""
        self.assertTrue(hasattr(GH, "entry_media_start"),
                        "entry_media_start 必须是模块级函数")
        self.assertEqual(GH.entry_media_start({"media_start": 2.5}), 2.5)
        self.assertEqual(GH.entry_media_start({}), 0.0)


if __name__ == "__main__":
    unittest.main()
