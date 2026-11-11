"""L1 编译期扩展点的判据：显式注册 / 统一校验 / 确定性 / #id 正交。

核心判据只有一条：换画风后 `animate.steps` 一行不改，仍然逐拍打得中。
其余用例都是替它站岗的边界。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import _extensions  # noqa: E402
import _animate  # noqa: E402
import _svg_sanitize  # noqa: E402

SKETCH_DIR = os.path.join(ROOT, "scripts", "extensions", "sketch")

BASE_SVG = (
    '<svg viewBox="0 0 980 735" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="0" y="0" width="980" height="735" fill="#0b0e14"/>'
    '<rect id="bar-a" x="120" y="300" width="120" height="340" fill="#6ee7c8"/>'
    '<rect id="bar-b" x="320" y="200" width="120" height="440" fill="#8ab4ff"/>'
    '<circle id="dot-a" cx="700" cy="360" r="90" fill="#ffc978"/>'
    '<text id="label" x="120" y="120" font-size="34" fill="#e6ebf3"'
    ' font-family="sans-serif">季度营收</text>'
    '</svg>'
)

STEPS = {"steps": [
    {"at": 0, "target": "#bar-a", "to": {"opacity": 1, "y": 0}, "duration": 0.6},
    {"at": 1, "target": "#bar-b", "to": {"scale": 1.1}, "duration": 0.6},
    {"at": 2, "target": "#dot-a", "draw": True, "duration": 0.8},
]}


def _sketch():
    return _extensions.load_extension(SKETCH_DIR)


def _compile(preset="sketch", **kw):
    spec = {"svg": BASE_SVG, "preset": preset, "seed": 7, "intensity": 0.5}
    spec.update(kw)
    return _extensions.compile_extension(_sketch(), spec, {})


def _temp_extension(manifest, source):
    """造一份临时扩展包，返回目录路径（调用方负责清理）。"""
    d = tempfile.mkdtemp(prefix="ctv-ext-test-")
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    with open(os.path.join(d, "compile.py"), "w", encoding="utf-8") as f:
        f.write(source)
    return d


def _manifest(**overrides):
    m = {"name": "probe", "version": "1.0.0", "kind": "style-filter",
         "outputs": ["svg"], "dependencies": [], "provider": "test"}
    m.update(overrides)
    return m


class TestManifest(unittest.TestCase):

    def test_键集封闭多写一个键当场拒(self):
        d = _temp_extension(dict(_manifest(), typo=1),
                            "def compile(spec, ctx):\n    return {'svg': spec['svg']}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "未登记的键"):
                _extensions.load_extension(d)
        finally:
            shutil.rmtree(d)

    def test_缺必填键当场拒(self):
        m = _manifest()
        del m["outputs"]
        d = _temp_extension(m, "def compile(spec, ctx):\n    return {}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "缺必填键"):
                _extensions.load_extension(d)
        finally:
            shutil.rmtree(d)

    def test_kind与outputs白名单(self):
        for key, value, hint in (("kind", "runtime-plugin", "不在白名单"),
                                 ("outputs", ["js"], "取值在")):
            d = _temp_extension(_manifest(**{key: value}), "def compile(spec, ctx):\n    return {}\n")
            try:
                with self.assertRaisesRegex(_extensions.ExtensionError, hint):
                    _extensions.load_extension(d)
            finally:
                shutil.rmtree(d)

    def test_编译期第三方依赖未钉固一律拒收(self):
        d = _temp_extension(_manifest(dependencies=["roughjs"]),
                            "def compile(spec, ctx):\n    return {}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "钉固清单"):
                _extensions.load_extension(d)
        finally:
            shutil.rmtree(d)

    def test_显式注册目录不存在即拒不扫描(self):
        with self.assertRaisesRegex(_extensions.ExtensionError, "不是目录"):
            _extensions.load_extension(os.path.join(SKETCH_DIR, "..", "nope"))

    def test_产出键必须与manifest声明一致(self):
        d = _temp_extension(_manifest(),
                            "def compile(spec, ctx):\n    return {'svg': spec['svg'], 'steps': []}\n")
        try:
            ext = _extensions.load_extension(d)
            with self.assertRaisesRegex(_extensions.ExtensionError, "没声明"):
                _extensions.compile_extension(ext, {"svg": BASE_SVG})
        finally:
            shutil.rmtree(d)


class TestCompileTimeRedlines(unittest.TestCase):

    def test_禁用墙钟与随机模块(self):
        for mod in ("random", "time", "subprocess"):
            d = _temp_extension(_manifest(),
                                "import %s\ndef compile(spec, ctx):\n    return {'svg': spec['svg']}\n" % mod)
            try:
                with self.assertRaisesRegex(_extensions.ExtensionError, "禁用模块"):
                    _extensions.load_extension(d)
            finally:
                shutil.rmtree(d)

    def test_禁用动态执行与文件IO(self):
        d = _temp_extension(_manifest(),
                            "def compile(spec, ctx):\n    return {'svg': eval('1')}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "动态执行"):
                _extensions.load_extension(d)
        finally:
            shutil.rmtree(d)

    def test_禁用跨次全局状态(self):
        d = _temp_extension(_manifest(),
                            "def compile(spec, ctx):\n    global n\n    n = 1\n    return {'svg': spec['svg']}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "纯函数"):
                _extensions.load_extension(d)
        finally:
            shutil.rmtree(d)

    def test_模块级可变容器被拦纯tuple放行(self):
        bad = _temp_extension(_manifest(),
                              "CACHE = {}\ndef compile(spec, ctx):\n    return {'svg': spec['svg']}\n")
        good = _temp_extension(_manifest(),
                               "PRESETS = ('a', 'b')\ndef compile(spec, ctx):\n    return {'svg': spec['svg']}\n")
        try:
            with self.assertRaisesRegex(_extensions.ExtensionError, "可变容器"):
                _extensions.load_extension(bad)
            self.assertTrue(callable(_extensions.load_extension(good)["fn"]))
        finally:
            shutil.rmtree(bad)
            shutil.rmtree(good)


class TestDeterminism(unittest.TestCase):

    def test_同一spec两次编译逐字节一致(self):
        ext = _sketch()
        spec = {"svg": BASE_SVG, "preset": "rough", "seed": 42, "intensity": 0.8}
        self.assertEqual(_extensions._canonical(_extensions.assert_deterministic(ext, spec, {})),
                         _extensions._canonical(_extensions.compile_extension(ext, spec, {})))

    def test_三档preset都能编译且产出合法SVG(self):
        for preset in ("sketch", "rough", "pixel"):
            out = _compile(preset)
            clean, notes = _svg_sanitize.sanitize_svg_for_inline(out["svg"])
            self.assertIn("ctv-ext-sketch", clean)
            self.assertNotIn("<script", clean.lower())
            self.assertEqual([n for n in notes if "墙钟" in n], [])

    def test_换seed产物变但不改id集合(self):
        a = _compile(seed=1)["svg"]
        b = _compile(seed=99)["svg"]
        self.assertNotEqual(a, b, "seed 没进滤镜参数，画风档退化成常量")
        self.assertEqual(_extensions.svg_ids(a), _extensions.svg_ids(b))


class TestOrthogonality(unittest.TestCase):

    def test_换画风前后id集合完全一致(self):
        out = _compile()
        self.assertEqual(_extensions.svg_ids(BASE_SVG),
                         _extensions.svg_ids(out["svg"]))
        self.assertEqual(_extensions.svg_ids(BASE_SVG),
                         ["bar-a", "bar-b", "dot-a", "label"])

    def test_同一份steps在原图与画风图上都不报落空(self):
        for svg in (BASE_SVG, _compile()["svg"], _compile("pixel")["svg"]):
            errs, warns = _animate.check_animate(svg, STEPS, "seg-demo", full_bleed=True)
            self.assertEqual(errs, [], "换画风后 steps 打不中，正交判据破了")
            self.assertEqual(warns, [])

    def test_verify_output认净化与正交两条(self):
        ext = _sketch()
        errs, warns = _extensions.verify_output(ext, _compile(), base_svg=BASE_SVG)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_扩展偷改id集合按文件拦(self):
        d = _temp_extension(
            _manifest(),
            "def compile(spec, ctx):\n"
            "    return {'svg': spec['svg'].replace('id=\"bar-a\"', 'id=\"renamed\"')}\n")
        try:
            ext = _extensions.load_extension(d)
            out = _extensions.compile_extension(ext, {"svg": BASE_SVG})
            errs, _ = _extensions.verify_output(ext, out, base_svg=BASE_SVG)
            self.assertTrue(any("改了 #id 集合" in e for e in errs))
        finally:
            shutil.rmtree(d)

    def test_targets写错id当场报错(self):
        with self.assertRaises(Exception) as cm:
            _compile(targets=["#ghost"])
        self.assertIn("找不到", str(cm.exception))


class TestProvenance(unittest.TestCase):

    def test_输出落进技能目录被拦(self):
        # 回归：ExtensionError 继承 ValueError，曾被执行 commonpath 的
        # except ValueError 整句吞掉，守卫静默失守、产物写进源码目录。
        import extension
        with self.assertRaisesRegex(_extensions.ExtensionError, "技能目录"):
            extension._guard_output(
                os.path.join(ROOT, "scripts", "leak.svg"), "-o")

    def test_来源记账带版本与源码哈希(self):
        p = _extensions.provenance(_sketch())
        self.assertEqual(p["extension"], "sketch@1.0.0")
        self.assertEqual(len(p["sha256"]), 64)
        self.assertTrue(p["provider"], "provider 不能为空，否则产物回溯不到来源")
