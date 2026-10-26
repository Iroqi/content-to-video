"""三轮奥卡姆 review 的行为锁：每条都有"修复前会红"的反向验证。

与一轮/二轮同口径：锁的是**防线真的拦得住**，不是"函数返回了什么"。
本轮三条缺陷的共同形状是"**门看着在，其实只对一半输入开着**"：前置判据
把后面那个更强的能力悄悄收窄了，或者干脆是恒真/死代码。

1. check_svg 对比度：CSS 色名必须落到与等价 hex 同一条判据（此前色名整条漏判）；
2. _tts 响应体解码：非 UTF-8 的 body 报"不是 UTF-8 文本"，不许报成"地址不合法"
   ——UnicodeDecodeError 是 ValueError 子类，被上一层 except 接走了，诊断指向
   完全没错的地方，还把可能瞬态的网关故障钉成确定性失败；
3. 单一真源：恒真子条件与死参数——注释里的纪律得有机制兜着。
"""
import ast
import contextlib
import glob
import os
import shutil
import tempfile
import unittest
import urllib.request
from unittest import mock

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _render_backend as RB
import _tts as TTS
import _manifest_schema as MS
import check_svg as CS

NS = "http://www.w3.org/2000/svg"
SCRIPTS_DIR = os.path.join(H.SKILL_DIR, "scripts")


def _errors_for(fill):
    """一张只含 <text fill=…> 的 4:3 SVG 走槽位档门禁，返回 (errors, warns)。"""
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "t.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="%s" viewBox="0 0 800 600" width="800" '
                    'height="600"><text x="60" y="300" font-size="40" '
                    'fill="%s">章节一</text></svg>' % (NS, fill))
        return CS.check_file(p, "slot", "portrait", "dark")
    finally:
        import shutil
        shutil.rmtree(d, True)


class ContrastNamedColors(unittest.TestCase):
    """`check_svg` 的字面对比度：色名与 hex 必须同结论。"""

    def test_named_color_is_judged_like_its_hex(self):
        """修复前：写 navy 静默放行、写等价的 #000080 报 error（同一个颜色）。"""
        named, _ = _errors_for("navy")
        hexed, _ = _errors_for("#000080")
        self.assertTrue(any("对比度" in e for e in named),
                        f"navy 没被查对比度：{named}")
        self.assertTrue(any("对比度" in e for e in hexed))
        # 同一颜色两种写法结论必须一致
        self.assertEqual(any("对比度" in e for e in named),
                         any("对比度" in e for e in hexed))

    def test_every_dark_named_color_is_judged(self):
        """反向：整批深色名都要过判据，不是 navy 一个碰巧。"""
        for name in ("black", "maroon", "midnightblue", "indigo",
                     "purple", "darkgreen", "firebrick", "teal"):
            errs, warns = _errors_for(name)
            self.assertTrue(
                any("对比度" in m for m in errs + warns),
                f"色名 {name} 完全没过对比度判据：{errs}{warns}")

    def test_unparseable_fill_still_skips_the_check(self):
        """反向：解析不出颜色的 fill 仍按"不查"处理（不是把 none 当黑色）。"""
        errs, warns = _errors_for("none")
        self.assertFalse(any("对比度" in m for m in errs + warns))


class TtsBodyDecoding(unittest.TestCase):
    """响应体解码错误不许被误报成 URL 配错。"""

    @staticmethod
    def _resp(payload):
        class R:
            def __init__(self, data):
                self._d = data

            def read(self):
                return self._d

            def __enter__(self):
                return self

            def __exit__(self, *a):
                    return False
        return R(payload)

    def test_non_utf8_body_reports_the_body(self):
        """修复前：报的是"TTS 请求地址不合法（'utf-8' codec can't decode …）"。"""
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=self._resp(b"\xff\xfe\x00bad gateway")):
            c = TTS.MimoTtsClient("k", "https://api.example.com/v1")
            with self.assertRaises(TTS.BadAudioResponseError) as cm:
                c.synthesize("你好", "冰糖", "", "m", 30)
        self.assertIn("UTF-8", str(cm.exception))
        # 真正的病因是响应体；把人引去改一个没错的 --base-url 是最贵的那种误导
        self.assertNotIn("地址不合法", str(cm.exception))

    def test_unknown_url_type_still_reports_the_url(self):
        """反向：urlopen 真的抛 `unknown url type` 时，诊断仍指向地址。"""
        def boom(_req, timeout=None):
            raise ValueError("unknown url type: 'notaurl'")
        with mock.patch.object(urllib.request, "urlopen", side_effect=boom):
            c = TTS.MimoTtsClient("k", "notaurl")
            with self.assertRaises(TTS.BadAudioResponseError) as cm:
                c.synthesize("你好", "冰糖", "", "m", 30)
        self.assertIn("地址不合法", str(cm.exception))


class NoDeadGuards(unittest.TestCase):
    """恒真的守卫不是守卫。"""

    def test_voiced_is_guarded_by_the_positive_check_not_a_second_condition(self):
        """反向：`voiced <= 0` 时报错来自 positive 校验，不是 synth_failed 那条。

        修复前 synth_failed 那条挂着 `and float(s["voiced"]) > 0`——上面
        `_validate_finite_number(..., positive=True)` 已拒掉 <= 0，这条子条件
        结构性永假，看着在判"给没给正的发声长度"，其实什么都没判。
        """
        with self.assertRaises(ValueError) as cm:
            MS._validate_voiced_span(
                {"onset": 0.0, "voiced": 0.0, "duration": 2.0,
                 "synth_failed": True}, "sentences[0]")
        self.assertIn("voiced", str(cm.exception))
        self.assertNotIn("synth_failed", str(cm.exception))

    def test_synth_failed_sentence_is_rejected(self):
        """正向：静音兜底句带合法 voiced 仍被拒（判据只剩 synth_failed 一条）。"""
        with self.assertRaises(ValueError) as cm:
            MS._validate_voiced_span(
                {"onset": 0.1, "voiced": 1.0, "duration": 2.0,
                 "synth_failed": True}, "sentences[0]")
        self.assertIn("synth_failed", str(cm.exception))


class AttrKeySingleSource(unittest.TestCase):
    """GSAP attr 通道名：真源只有 `_images_schema.ATTR_KEY` 一处。"""

    def test_no_module_rewrites_the_literal(self):
        """反向：任何一处把 "attr" 写回 dict 键，这条就红。

        `_stage_carry` 的收尾态烘焙与 `html_renderer` 的补间发射各写一个字面量
        时，"契约层放行的形状"与"烘焙认得的形状"只是恰好相等——分家那天症状是
        stage:"keep" 接续页第 0 帧就坏，且生成期零报错。
        """
        offenders = []
        for path in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
            # _images_schema 是 ATTR_KEY 的登记处，它自己当然要写字面量；
            # _derive 的 "attr" 是 derive payload 的自有键（"绑到哪个 SVG
            # 属性"），与 GSAP 的 attr 通道同名不同义，不在此列。
            if os.path.basename(path) in ("_images_schema.py", "_derive.py"):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if (isinstance(key, ast.Constant) and key.value == "attr"):
                        offenders.append(
                            "%s:%d" % (os.path.basename(path), node.lineno))
        self.assertEqual([], offenders,
                         "GSAP attr 通道名必须走 _images_schema.ATTR_KEY")


class WallClockOnlyForWallClock(unittest.TestCase):
    """量间隔用 monotonic，比对文件 mtime 用墙钟：两种用途不许混。"""

    @staticmethod
    def _calls_wall_clock(func_name):
        """函数体里有没有出现 `time.time()`（墙钟）调用。"""
        path = os.path.join(SCRIPTS_DIR, "_render_backend.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == func_name)
        return any(isinstance(n, ast.Attribute) and n.attr == "time"
                   and isinstance(n.value, ast.Name)
                   and n.value.id == "time"
                   for n in ast.walk(node))

    def test_run_check_measures_with_monotonic(self):
        """修复前：run_check 用 time.time() 量间隔，墙钟被 NTP 校正就报负数。"""
        self.assertFalse(
            self._calls_wall_clock("run_check"),
            "run_check 量的是间隔，必须走 time.monotonic()")

    def test_snapshot_frames_still_compares_against_wall_clock(self):
        """反向：snapshot 比的是文件 mtime，那里**必须**用 time.time()。"""
        self.assertTrue(
            self._calls_wall_clock("snapshot_frames"),
            "snapshot_frames 拿 t0 跟文件 mtime 比，改 monotonic 就永远判不出新产物")


if __name__ == "__main__":
    unittest.main()
