#!/usr/bin/env python3
"""extension.py CLI 的落盘契约：三类产物都过完校验，就该都能交到作者手上。

守的是「命令行最后一步按产物类型落盘」这一件事，与 test_extensions.py 分开：
那边测 load / compile / verify 的 API 语义，这里测产物怎么落盘。

原版 CLI 在 verify_output 之后硬写了一句「首版 CLI 只落 svg 产物」，
steps-compiler 与 asset-transcoder 明明全绿却拿不到产物，只能手调
_extensions.py API。下面第一条用例在原版 CLI 上必然红——这道闸得先证明
自己会响，不能是永远绿的摆设。
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import extension  # noqa: E402

BASE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" id="root" class="art" '
    'width="980" height="735" preserveAspectRatio="xMinYMin meet" '
    'viewBox="0 0 980 735">'
    '<defs><linearGradient id="grad"><stop offset="0" stop-color="#6ee7c8"/>'
    '</linearGradient></defs>'
    '<rect id="bar-a" class="shape" x="10" y="10" width="100" height="100" '
    'fill="url(#grad)"/>'
    '</svg>'
)

STEPS_BODY = '''def compile(spec, ctx):
    return {"steps": [{"at": 0, "target": "#bar-a",
                       "to": {"opacity": 1}, "duration": 0.5}]}
'''
SVG_BODY = '''def compile(spec, ctx):
    return {"svg": spec["svg"].replace(
        "<rect ", '<script>alert(1)</script><rect onload="alert(1)" ', 1)}
'''
MEDIA_BODY = '''def compile(spec, ctx):
    return {"media": "images/clip.mp4"}
'''


def _make_ext(root, kind, outputs, body):
    """造一个最小扩展包：manifest.json + compile.py，键集按契约层要求。"""
    d = os.path.join(root, "ext")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "probe", "version": "1.0.0", "kind": kind,
                   "outputs": outputs, "dependencies": []}, f)
    with open(os.path.join(d, "compile.py"), "w", encoding="utf-8") as f:
        f.write(body)
    return d


def _run(ext, out):
    """在临时目录里跑一次 CLI，返回 (exit code, 合并后的输出)。"""
    spec = os.path.join(os.path.dirname(out), "spec.json")
    base = os.path.join(os.path.dirname(out), "base.svg")
    with open(spec, "w", encoding="utf-8") as f:
        json.dump({}, f)
    with open(base, "w", encoding="utf-8") as f:
        f.write(BASE_SVG)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = extension.main(["--extension", ext, "--spec", spec,
                             "--base-svg", base, "-o", out])
    return rc, buf.getvalue()


class CliOutputContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ctv-extcli-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_steps_产物落成可解析的JSON(self):
        """原版 CLI 会在这一步返回 1：steps 明明过了统一校验却拿不到产物。"""
        ext = _make_ext(self.tmp, "steps-compiler", ["steps"], STEPS_BODY)
        out = os.path.join(self.tmp, "out.steps.json")
        rc, log = _run(ext, out)
        self.assertEqual(rc, 0, log)
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(sorted(payload), ["steps"])
        self.assertEqual(payload["steps"][0]["target"], "#bar-a")

    def test_svg_产物仍按原样文本落盘(self):
        """svg 那条路一行没改：产物必须是 SVG 文本，不是 JSON 包装。"""
        ext = _make_ext(self.tmp, "style-filter", ["svg"], SVG_BODY)
        out = os.path.join(self.tmp, "out.svg")
        rc, log = _run(ext, out)
        self.assertEqual(rc, 0, log)
        with open(out, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
        self.assertTrue(text.startswith("<svg"), text[:120])
        self.assertNotIn('"svg":', text)
        # verify_output 的净化结果才是实际落盘字节，compile() 的原始 SVG 不能
        # 带着 script/onload 绕过最后一步写盘。
        self.assertNotIn(b"<script", raw.lower())
        self.assertNotIn(b"onload=", raw.lower())
        self.assertNotIn(b"alert(1)", raw.lower())
        # standalone 落盘保留作者身份、尺寸与内部引用；内联命名空间只能由
        # gen_hyperframes 后面那一次净化添加。
        self.assertIn('id="root"', text)
        self.assertIn('class="art"', text)
        self.assertIn('class="shape"', text)
        self.assertIn('width="980"', text)
        self.assertIn('height="735"', text)
        self.assertIn('preserveAspectRatio="xMinYMin meet"', text)
        self.assertIn('fill="url(#grad)"', text)
        self.assertNotIn("data-ctv-svg-scope", text)
        self.assertNotIn("ctv-svg-", text)

    def test_media_产物落成可解析的JSON(self):
        ext = _make_ext(self.tmp, "asset-transcoder", ["media"], MEDIA_BODY)
        out = os.path.join(self.tmp, "out.media.json")
        rc, log = _run(ext, out)
        self.assertEqual(rc, 0, log)
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["media"], "images/clip.mp4")


if __name__ == "__main__":
    unittest.main()
