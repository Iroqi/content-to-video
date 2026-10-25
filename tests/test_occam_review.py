"""一轮奥卡姆 review 的行为锁：每条都有"修复前会红"的反向验证。

这里的每一条都是**同一类缺陷**：代码看起来在防一件事，实际那条防线从来没生效过
（或者只在注释里生效）。锁的是"防线真的拦得住"，不是"函数返回了什么"。

按缺陷分组，每组一条正向 + 一条反向：
1. 官方门禁：严格码以 warning 出现时要出声（此前被并进噪声放行集合）；
2. JPEG 尺寸：SOF 恰好落在读块末端要读得出（差一字节 = 静默放行两道告警）；
3. 静音探测：ffmpeg 非零退出不能当成"量到了"（此前只看 stderr 在不在）；
4. SVG 实体：注释里的 `<!DOCTYPE` 不该报废整份图，真实体声明照样拒；
5. 中转文件：不许动进程级 umask（TTS 线程池里跑，会连坐别的线程）；
6. WAV 帧数：读不出一律按"无帧"，不得退回 `getsize > 44`；
7. 收束地板：常量必须落在 0.05s 网格上，否则是个够不着的假阈值；
8. ease 目录：`none` 不再被一条恒真的条件特殊对待；
9. CSS 初值表：漏掉的属性名会让相对写法按 0 起算。
"""
import os
import subprocess
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _cam_crop as CC
import _ease as EZ
import _render_backend as RB
import _speech as SP
import _stage_carry as SC
import _svg_sanitize as SAN
import check_svg as CS
from _media_size import _jpeg_size
from html_renderer import SETTLE_MIN_SECONDS

NS = "http://www.w3.org/2000/svg"


def _report(stage, severity, code):
    return {"ok": True, stage: {"findings": [
        {"severity": severity, "code": code, "message": "m"}]}}


class GateStrictCodes(unittest.TestCase):
    """`GATE_STRICT_CODES` 是"必须当真"，不是"登记过就放过"。"""

    def test_strict_code_surfaces_as_warning(self):
        """`clipped_text` 以 warning 出现时也要点名。

        修复前 `known_codes` = 噪声 ∪ 严格码，于是它整条静默——而
        `references/rendering.md` 明写"clip_media_fit 与 clipped_text 不在噪声之列"。
        """
        for code in ("clipped_text", "clip_media_fit", "low_contrast",
                     "missing_asset", "runtime_error"):
            with self.subTest(code=code):
                verdict = RB.check_verdict(_report("layout", "warning", code))
                self.assertTrue(verdict, f"{code} 不该被静默")
                self.assertEqual(verdict[0][1], code)

    def test_noise_code_stays_silent(self):
        """反向：噪声码仍要静默，别把闸门改成"什么都报"。"""
        code = next(iter(RB.GATE_NOISE_CODES))
        self.assertEqual(RB.check_verdict(_report("lint", "warning", code)), [])

    def test_unregistered_warning_still_surfaces(self):
        """反向：未登记的新码照样点名（CLI 加了新检查不能被吞）。"""
        v = RB.check_verdict(_report("lint", "warning", "brand_new_code"))
        self.assertEqual([c for _, c, _ in v], ["unregistered:brand_new_code"])


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
        self.assertEqual(_jpeg_size(buf), (480, 640))

    def test_garbage_still_returns_none(self):
        """反向：不是 JPEG 头照样读不出，放宽条件没把坏输入放进来。"""
        self.assertIsNone(_jpeg_size(b"\x89PNG\r\n\x1a\n" + bytes(20)))
        self.assertIsNone(_jpeg_size(b"\xff\xd8" + bytes(4)))


class SilenceDetectReturnCode(unittest.TestCase):
    def test_ffmpeg_failure_is_not_a_measurement(self):
        """ffmpeg 非零退出必须返回 None，不是"整段都在说话"。

        修复前只看 `stderr is None`：ffmpeg 起不来 / 文件打不开时 stderr 照样有
        内容（一版错误），`_parse_silences` 一个 silence_* 都找不到，于是
        `voiced_span` 回报 (0.0, 文件全长)——与真正的成功测量不可分辨，
        连"量不出来"那句 warn 都不打。
        """
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "a.wav")
            with wave.open(wav, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            self.assertIsNone(SP.measure_voiced_span("/bin/false", wav, 1.0))

    def test_missing_binary_returns_none(self):
        """反向：ffmpeg 不存在（OSError）同样按"量不出来"，不落一个假区间。"""
        self.assertIsNone(SP.measure_voiced_span(
            os.path.join(tempfile.gettempdir(), "no-such-ffmpeg-xyz"), "x.wav", 1.0))


def _check_svg_text(raw, tmpdir=None):
    """把一段 SVG 源文本交给 check_svg，返回 (errors, warns)。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw)
        return CS.check_file(p, "slot", "portrait", "dark")


class SvgEntityGuard(unittest.TestCase):
    def test_doctype_inside_a_comment_is_not_an_entity_declaration(self):
        """注释里的 `<!DOCTYPE` 不该报废整份图。

        修复前 `re.search(r"<!DOCTYPE|<!ENTITY", raw)` 扫全文：注释、`<desc>`
        文本、属性值里的同名串一律命中，一份干净的 SVG 就此被拒。
        """
        raw = ('<svg xmlns="%s" viewBox="0 0 100 100">'
               '<!-- <!DOCTYPE svg> -->'
               '<rect x="0" y="0" width="100" height="100"/></svg>') % NS
        errs, _ = _check_svg_text(raw)
        self.assertFalse(any("DOCTYPE" in e or "ENTITY" in e for e in errs), errs)
        markup, _ = SAN.sanitize_svg_for_inline(raw)
        self.assertIn("rect", markup)

    def test_real_entity_declaration_still_rejected(self):
        """反向：真声明照样拒——这条守卫是为了挡实体膨胀，不能一起删掉。"""
        evil = ('<!DOCTYPE svg [<!ENTITY a "AAAAAAAAAA">]>'
                '<svg xmlns="%s"><desc>&a;</desc></svg>') % NS
        errs, _ = _check_svg_text(evil)
        self.assertTrue(any("ENTITY" in e for e in errs), errs)
        with self.assertRaises(ValueError):
            SAN.sanitize_svg_for_inline(evil)


class UniqueTmpUmask(unittest.TestCase):
    def test_unique_tmp_does_not_touch_process_umask(self):
        """`unique_tmp` 不许动进程级 umask。

        修复前 `os.umask(0)` 再写回：那是**进程级**全局量，而调用方在线程池里跑
        （pipeline 的 TTS 并发），两个线程重叠期间落盘的文件会拿到 0777。
        旧实现自己会写回原值，所以"跑完再读 umask"看不出问题——要盯的是
        **它有没有调过**。
        """
        import _script_utils as SU
        calls = []
        real = os.umask

        def spy(mask):
            calls.append(mask)
            return real(mask)

        os.umask = spy
        try:
            with tempfile.TemporaryDirectory() as td:
                SU.unique_tmp(td, ".x.", ".tmp")
        finally:
            os.umask = real
        self.assertEqual(calls, [], "unique_tmp 不该读/写进程级 umask")

    def test_unique_tmp_stays_readable(self):
        """反向：不按 umask 折算也不能退回 mkstemp 的 0600（成品要能被浏览器读）。"""
        from _script_utils import unique_tmp
        with tempfile.TemporaryDirectory() as td:
            p = unique_tmp(td, ".y.", ".wav")
            self.assertNotEqual(os.stat(p).st_mode & 0o777, 0o600)


class WavFrameFallback(unittest.TestCase):
    def test_unreadable_file_is_not_judged_as_having_frames(self):
        """wave 读不出一律按"无帧"，不得退回 `getsize > 44`。

        那条尺寸判据正是本函数被发明来取代的（0 帧产物带 LIST/INFO 块约 78 字节，
        会穿过 ">44" 被判成成功，混进 concat 后整条时间轴悄悄前移）。
        """
        from _audio import _wav_has_frames
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "bad.wav")
            with open(bad, "wb") as f:
                f.write(b"RIFF" + bytes(200))     # 尺寸远大于 44，但不是可读 WAV
            self.assertFalse(_wav_has_frames(bad))

    def test_real_wav_with_frames_passes(self):
        """反向：真有帧的 WAV 照样认，收紧没把好产物一起挡掉。"""
        from _audio import _wav_has_frames
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "ok.wav")
            with wave.open(wav, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 2400)
            self.assertTrue(_wav_has_frames(wav))


class SettleFloorOnGrid(unittest.TestCase):
    def test_floor_is_reachable_on_the_quantisation_grid(self):
        """地板常量必须落在 0.05s 网格上。

        写 0.08 时 `d >= 0.08` 与 `d >= 0.10` 完全等价（d 只可能是 0、0.05、0.10…），
        于是注释里"30fps 两帧"那条阈值结构性不可达。
        """
        self.assertEqual(SETTLE_MIN_SECONDS * 20, int(SETTLE_MIN_SECONDS * 20))


class EaseCatalogue(unittest.TestCase):
    def test_none_is_not_an_extra_family(self):
        """`none` 在目录里，宽松档下不该被当"目录外的新族"。"""
        EZ._ALLOW_EXTRA["on"] = True
        try:
            self.assertIsNone(EZ._extra_family("none"))
            self.assertEqual(EZ._extra_family("wiggle"), "wiggle")
        finally:
            EZ._ALLOW_EXTRA["on"] = False


class CssInitialValueTable(unittest.TestCase):
    def test_stop_opacity_relative_bake_uses_css_initial_value(self):
        """漏掉的属性名会让相对写法按 0 起算（stop-opacity 的初值是 1）。

        修复前 `{"stopOpacity": "+=0.5"}` 烘出 0.5，运行期按 1+0.5 钳到 1——
        接续页页界静默跳变，且 notes 一声不响。
        """
        svg = ('<svg xmlns="%s" viewBox="0 0 1080 1440">'
               '<stop id="s1" offset="0"/>'
               "</svg>") % NS
        out, _ = SC.bake_settled_state(
            svg, [{"target": "#s1", "to": {"stop-opacity": "+=0.5"}}])
        el = next(e for e in ET.fromstring(out).iter() if e.get("id") == "s1")
        self.assertIn("stop-opacity:1.5", el.get("style"))

    def test_zero_initial_property_still_starts_at_zero(self):
        """反向：初值确实是 0 的属性仍按 0 起算，补表没把整张表改成非零。"""
        svg = ('<svg xmlns="%s" viewBox="0 0 1080 1440">'
               '<rect id="r1" x="10" y="10" width="20" height="20"/>'
               "</svg>") % NS
        out, _ = SC.bake_settled_state(
            svg, [{"target": "#r1", "to": {"strokeDashoffset": "+=4"}}])
        el = next(e for e in ET.fromstring(out).iter() if e.get("id") == "r1")
        self.assertIn("stroke-dashoffset:4", el.get("style"))


if __name__ == "__main__":
    unittest.main()
