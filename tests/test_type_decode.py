"""Opus 5.5 式「解码揭示」：整行先在场、逐格锁定成真字（type.decode）。

缺省的 `type` 是前缀渐长（从空串长出来），只有"有 / 无"两态。解码档多出
**未定态**：结论/数字在旁白揭晓前先以占位字符在场，锁定的那一格就是揭晓的
那一瞬——这是「推进叙事」，不是装饰（见 references/decode.md）。

判据落在产物上：脚本是渲染端生成的，所以从产物 HTML 里抠出那段 JS，在 Node
里按进度重放 `onUpdate` 比对每一帧的文本。要证的命题只有四条：

- **逐帧可复现**：同一进度重放两次给出同一行字；产物里没有 Math.random /
  Date / performance —— 墙钟一进来，逐帧 seek 的两帧就对不上；
- **锁定不回退**：已锁定的前缀恒等于原文前缀，只有后面的格子还留着噪声；
- **整行不重排**：字符数恒等、每格宽/窄类别不变、空白原样（文字盒左缘站定）；
- **末帧钉死**：onComplete 之后是原文全文。

用 Node 而不是在 Python 里重写噪声哈希：判据要证明"发出去的那段脚本真的这样
算"，Python 再写一份就变成两份实现互相对照——它们一起错的时候测试照样绿。

用例条数是合并过的：多组文本共用 `_probe`，一条用例覆盖多个样本。
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import html_renderer as HR

NODE = shutil.which("node")

# 假节点 + 假 tl.to：按 0→L 的进度重放 onUpdate 两遍（确定性），再调 onComplete。
# 文本必须在 eval **之前**写进节点——脚本 build 期会先读一次 textContent。
_HARNESS = """
const fs = require("fs");
const body = fs.readFileSync(process.argv[2], "utf8");
const text = fs.readFileSync(process.argv[3], "utf8");
const frames = +process.argv[4];
var rec = null, node = {textContent: ""};
var document = {querySelector: function () { return node; }};
node.textContent = text;
eval("var tl={to:function(p,v,at){rec={p:p,v:v,at:at};}};\\n" + body);
const L = rec.v.k, cb = rec.v.onUpdate, done = rec.v.onComplete;
const play = function () {
  const out = [];
  for (var i = 0; i <= frames; i++) {
    rec.p.k = L * i / frames;
    cb();
    out.push(node.textContent);
  }
  return out;
};
const seq = play(), again = play();
if (done) done();
console.log(JSON.stringify({L: L, seq: seq, again: again,
                            last: node.textContent, at: rec.at,
                            dur: rec.v.duration, ease: rec.v.ease}));
"""


def _type_script(html, sid="seg-a", target="#cap"):
    """从产物 HTML 里抠出这一段的 type 脚本块（`;(function(){…})();`）。"""
    scope = f'"#img-{sid} {target}"'
    for line in html.splitlines():
        line = line.strip()
        if line.startswith(";(function(){") and scope in line:
            return line
    raise AssertionError(f"产物里找不到 {scope} 的 type 脚本")


def _render_type(typ, text, target="#cap"):
    """生成一份带 type 编排的 HTML，抠出目标 <text> 的那段脚本。"""
    m = H.make_manifest()
    imgs = {"seg-a": {"src": "images/seg-a.svg",
                      "inline_svg": f'<svg viewBox="0 0 980 735">'
                                    f'<text id="cap">{text}</text></svg>',
                      "animate": {"steps": [
                          {"at_time": 0.0, "target": target, "type": typ,
                           "duration": 1.0, "ease": "none"}]}},
            "seg-b": {"src": "images/seg-b.svg"}}
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
    return _type_script(html, target=target)


@unittest.skipUnless(NODE, "需要 node 才能重放发出去的脚本")
class TypeDecodeProduct(unittest.TestCase):
    """产物级判据：可复现、锁定不回退、不重排、末帧钉死。"""

    def _render(self, typ, text, target="#cap"):
        return _render_type(typ, text, target)

    def _probe(self, typ, text, frames=40):
        js = self._render(typ, text)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "case.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        # 文本走**文件**而不是 argv：Windows 按系统代码页编码 argv，中文会被拒。
        t = os.path.join(tmp, "text.txt")
        with open(t, "w", encoding="utf-8") as f:
            f.write(text)
        h = os.path.join(tmp, "h.js")
        with open(h, "w", encoding="utf-8") as f:
            f.write(_HARNESS)
        r = subprocess.run([NODE, h, p, t, str(frames)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout or "")

    def test_no_wallclock_and_noise_alphabet_is_injected(self):
        """产物不许有墙钟/随机源，且噪声字母表与末帧钉死都在脚本里。"""
        js = self._render({"decode": True}, "营收增长 128%")
        for banned in ("Math.random", "Date", "performance", "setTimeout", "setInterval"):
            self.assertNotIn(banned, js, f"解码档混进了 {banned}：逐帧 seek 会两帧对不上")
        self.assertIn("onComplete:function(){e.textContent=s;}", js)
        self.assertIn("var A=", js)
        self.assertIn(',B="\\u3007', js)   # 宽栏（全角）确实注入了，不是只发了窄栏
        self.assertIn("onUpdate:f", js)

    def test_locked_prefix_never_changes_and_replay_is_identical(self):
        """已锁定的前缀恒为原文前缀；同一进度重放两次逐帧一致。"""
        text = "结论：增长 128%"
        out = self._probe({"decode": True}, text)
        L, seq, again = out["L"], out["seq"], out["again"]
        self.assertEqual(L, len(text), out)
        self.assertEqual(seq, again, "同一进度重放两次不一致——噪声里混了墙钟")
        self.assertNotEqual(seq[0], text, "build 期必须先把整行换成噪声态")
        # n 随进度单调不减，而每一帧的前 n 格都恒为原文前缀——两件合起来就是
        # "锁定不回退"。不另用最长公共前缀去反推锁定数：噪声字符碰巧等于原文
        # 那一格时，反推值会虚高，测的就不是实现而是巧合了。
        for i, frame in enumerate(seq):
            n = min(L, (L * i) // (len(seq) - 1))
            self.assertEqual(frame[:n], text[:n],
                             f"第 {i} 帧已锁定的前缀回退了：{frame!r}")

    def test_noise_keeps_length_width_and_whitespace(self):
        """字符数恒等、宽/窄类别不变、空白原样——整行在解码途中不重排。"""
        text = "AI WEEKLY 增长 3.5 倍"
        out = self._probe({"decode": True}, text)
        L = len(text)
        for frame in out["seq"]:
            self.assertEqual(len(frame), L, f"字符数变了：{frame!r}")
            for i, ch in enumerate(frame):
                src = text[i]
                if ord(src) <= 32:
                    self.assertEqual(ch, src, f"第 {i} 格空白被噪声化了")
                else:
                    self.assertEqual(ord(ch) >= 0x2e80, ord(src) >= 0x2e80,
                                     f"第 {i} 格宽度类别变了：{src!r} → {ch!r}")

    def test_last_frame_is_exact_source_and_step_timing_survives(self):
        """onComplete 与 p.k=L 都给出全文；锚点/时长/曲线仍取 step 级。"""
        text = "从 9% 到 10%"
        out = self._probe({"decode": True}, text)
        self.assertEqual(out["last"], text, "onComplete 没把末帧钉回原文")
        self.assertEqual(out["seq"][-1], text, "p.k=L 那一帧就该是全文")
        self.assertEqual(out["dur"], 1.0, out)
        self.assertEqual(out["ease"], "none", out)
        self.assertIsInstance(out["at"], (int, float), out)


class TypeDecodeContract(unittest.TestCase):
    """契约与产物不能各说各的：decode 只被 type 认，且这一轮不与 word 混写。"""

    def test_decode_is_type_inner_key_and_other_chunks_untouched(self):
        from _images_schema import validate_images_json
        # 写在 step 级就是未知键
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
                {"at": 0, "target": "#c", "type": {}, "decode": True}]}}})
        # 非布尔、与 word 混写、未知键
        for typ in ({"decode": 1}, {"decode": True, "chunk": "word"},
                    {"decode": True, "from": "end"}):
            with self.subTest(typ=typ), self.assertRaises(ValueError):
                validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
                    {"at": 0, "target": "#c", "type": typ}]}}})
        # 缺省档与 word 档的产物不含解码脚本
        for typ in ({}, {"chunk": "word"}):
            with self.subTest(typ=typ):
                js = _render_type(typ, "一切照旧")
                self.assertNotIn("onComplete:function(){e.textContent=s;}", js)

    def test_canvas_kit_forwards_decode_as_node_level_switch(self):
        import canvas_kit as CK
        spec = {"elements": [
            {"kind": "text", "id": "lede", "x": 120, "y": 400, "tier": "body",
             "content": "增长的不是速度", "type": True, "decode": True}]}
        by = {s["target"]: s for s in CK.selfcheck_fragment(CK.validate_spec(spec))["steps"]}
        self.assertEqual(by["#lede"]["type"], {"decode": True})


if __name__ == "__main__":
    unittest.main()
