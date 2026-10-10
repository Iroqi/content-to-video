"""配图像素够不够：头解析 / cover 放大倍率 / ffmpeg 视频尺寸解析 / 生成期告警。

这一组守的是一条此前**零报错**的静默退化：配图一律 `cover` 铺满画布，而
`cover` 只保证填满、不保证清楚——一张不够大的素材被放大成糊的，生成期、
`hyperframes check`、渲染日志全都一声不吭（它们查的是"图在不在、能不能解码"，
不是"够不够大"），只有成片里看得见。所以尺寸那一问必须在生成期出声。
"""
import io
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
from _media_size import (raster_size, cover_scale, upscale_note,
                         UPSCALE_WARN, UPSCALE_SEVERE)
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

    def test_gif(self):
        self.assertEqual(self._size(_gif(320, 240), ".gif"), (320, 240))

    def test_webp(self):
        self.assertEqual(self._size(_webp_vp8x(1024, 768), ".webp"), (1024, 768))

    def test_unknown_bytes_return_none(self):
        """认不出的文件返回 None（调用方按"没法验"处理，不猜、不报）。"""
        self.assertIsNone(self._size(b"not an image at all"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(raster_size("/nonexistent/nope.png"))


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


if __name__ == "__main__":
    unittest.main()
