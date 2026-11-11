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
    '<svg xmlns="http://www.w3.org/2000/svg" width="980" height="735" '
    'viewBox="0 0 980 735">'
    '<rect id="bar-a" x="10" y="10" width="100" height="100" fill="#6ee7c8"/>'
    '</svg>'
)

STEPS_BODY = '''def compile(spec, ctx):
    return {"steps": [{"at": 0, "target": "#bar-a",
                       "to": {"opacity": 1}, "duration": 0.5}]}
'''
SVG_BODY = '''def compile(spec, ctx):
    return {"svg": spec["svg"]}
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
        with open(out, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("<svg"), text[:120])
        self.assertNotIn('"svg":', text)

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
