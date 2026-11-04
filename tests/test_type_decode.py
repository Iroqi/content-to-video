"""Opus 5.5 式「解码揭示」：整行先在场、逐格/逐组锁定成真字（type.decode）。

缺省的 `type` 是前缀渐长（从空串长出来），只有"有 / 无"两态。解码档多出
**未定态**：结论/数字在旁白揭晓前先以占位字符在场，锁定的那一格就是揭晓的
那一瞬——这是「推进叙事」，不是装饰（见 references/decode.md）。

判据落在产物上：脚本是渲染端生成的，所以从产物 HTML 里抠出那段 JS，在 Node
里按进度重放 `onUpdate` 比对每一帧的文本。要证的命题只有六条：

- **逐帧可复现**：同一进度重放两次给出同一行字；产物里没有 Math.random /
  Date / performance —— 墙钟一进来，逐帧 seek 的两帧就对不上；
- **锁定不回退**：已锁定的前缀恒等于原文前缀，只有后面的格子还留着噪声；
- **整行不重排**：字符数恒等、每格宽/窄类别不变、空白原样（文字盒左缘站定）；
- **末帧钉死**：onComplete 之后是原文全文；
- **锚点定序**：`from` 四枚锚点各是 0..N-1 的排列，顺序由锚点唯一确定、
  与 `stagger.from` 同源白名单，不依赖 JS 引擎的排序稳定性；
- **整组锁定**：与 `chunk:"word"` 同写时一次锁一整个语义单元，组边界与
  按单元跳格档共用同一份切分。

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
// 只插桩取产物运行时算出的「单元边界 G」与「锁定顺序 O」，不重写任何一条规则：
// 判据要证明的是发出去的那段脚本真的这样分组、这样排序，而不是两份实现互相对照。
const probe = body
  .replace("var N=G.length;", "globalThis.__G=G.slice();var N=G.length;")
  .replace("var own=[];", "globalThis.__O=O.slice();var own=[];");
eval("var tl={to:function(p,v,at){rec={p:p,v:v,at:at};}};\\n" + probe);
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
                            dur: rec.v.duration, ease: rec.v.ease,
                            G: globalThis.__G || null, O: globalThis.__O || null}));
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

    def test_anchor_end_locks_backwards_from_the_conclusion(self):
        """from:"end" 时结论先定、锁定边从右往左卷，不是 start 换个名字。"""
        text = "结论：增长 128%"
        js = self._render({"decode": {"from": "end"}}, text)
        self.assertIn('F="end"', js, "锚点没进产物，渲染端仍会按读序锁")
        out = self._probe({"decode": {"from": "end"}}, text)
        L, seq = out["L"], out["seq"]
        self.assertEqual(L, len(text))
        self.assertEqual(seq, out["again"], "锚点排序不能依赖引擎的排序稳定性")
        self.assertEqual(out["last"], text)
        for i, frame in enumerate(seq):
            n = min(L, (L * i) // (len(seq) - 1))
            self.assertEqual(frame[L - n:], text[L - n:],
                             f"第 {i} 帧已锁定的后缀回退了：{frame!r}")
        mid = seq[len(seq) // 2]
        self.assertNotEqual(mid[:len(text) // 2], text[:len(text) // 2],
                            "中间帧的前半段该还是未定态")

    def test_anchor_whitelist_is_a_deterministic_permutation(self):
        """四枚锚点都是 0..N-1 的排列，顺序由锚点唯一确定、与 stagger 同源。"""
        text = "净值 12.5 亿"
        keys = {
            "start": lambda k, N: k,
            "end": lambda k, N: N - 1 - k,
            "center": lambda k, N: abs(k - (N - 1) / 2),
            "edges": lambda k, N: min(k, N - 1 - k),
        }
        for anchor, key in keys.items():
            with self.subTest(anchor=anchor):
                out = self._probe({"decode": {"from": anchor}}, text)
                O, N = out["O"], out["L"]
                self.assertEqual(sorted(O), list(range(N)), "锁定顺序必须不重不漏")
                self.assertEqual(O, sorted(range(N), key=lambda k: (key(k, N), k)),
                                 f"from:{anchor} 的锁定顺序不对")
                self.assertEqual(out["G"], list(range(1, N + 1)),
                                 "缺省档仍是一格一单元")
                self.assertEqual(out["G"][-1], len(text), "切分必须盖住整行")
                self.assertEqual(out["seq"], out["again"], "同一进度重放不一致")
                self.assertEqual(out["last"], text)

    def test_word_chunk_locks_a_whole_unit_at_once(self):
        """chunk:"word" 与 decode 同写：一次锁一整组，组边界与按单元跳格档共用。"""
        text = "AI WEEKLY 增长 3.5 倍"
        split = "var isW=function(c){var u=c.charCodeAt(0);"
        self.assertIn(split, self._render({"decode": True, "chunk": "word"}, text),
                      "解码档没用共用切分，会与 word 档各切各的")
        self.assertIn(split, self._render({"chunk": "word"}, text))
        out = self._probe({"decode": True, "chunk": "word"}, text, frames=60)
        G, O, N, seq = out["G"], out["O"], out["L"], out["seq"]
        self.assertLess(N, len(text), "按单元锁定不该还是一个字符一单元")
        self.assertEqual(G[-1], len(text), "切分必须盖住整行")
        self.assertEqual(sorted(O), list(range(N)))
        self.assertNotEqual(seq[0], text, "build 期整行得先以噪声态在场")
        self.assertEqual(out["last"], text)
        self.assertEqual(seq, out["again"])
        # 已锁定的单元必须**整组**等于原文：不逐格比对组内的半截状态，也不从
        # 噪声里反推锁定数（噪声碰巧等于原字会把反推值抬虚）。
        for i, frame in enumerate(seq):
            n = min(N, (N * i) // (len(seq) - 1))
            for t in range(n):
                k = O[t]
                a = G[k - 1] if k else 0
                self.assertEqual(frame[a:G[k]], text[a:G[k]],
                                 f"第 {i} 帧第 {k} 组没整组锁住：{frame!r}")


class TypeDecodeContract(unittest.TestCase):
    """契约与产物不能各说各的：decode 只被 type 认，两个旋钮正交。"""

    @staticmethod
    def _validate(typ):
        from _images_schema import validate_images_json
        return validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
            {"at": 0, "target": "#c", "type": typ}]}}})

    def test_decode_is_type_inner_key_and_two_orthogonal_knobs(self):
        from _images_schema import validate_images_json
        # 写在 step 级就是未知键
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
                {"at": 0, "target": "#c", "type": {}, "decode": True}]}}})
        # 形状不对：非布尔非对象、type 级塞 from、对象里塞未知键/空对象/坏锚点
        for typ in ({"decode": 1}, {"decode": True, "from": "end"},
                    {"decode": {"from": "end", "x": 1}}, {"decode": {}},
                    {"decode": {"from": "north"}}):
            with self.subTest(typ=typ), self.assertRaises(ValueError):
                self._validate(typ)
        # chunk 管"一次锁多少"、from 管"从哪头先锁"，两者同写合法
        for typ in ({"decode": True, "chunk": "word"},
                    {"decode": {"from": "end"}},
                    {"decode": {"from": "center"}, "chunk": "word"},
                    {"decode": False}):
            with self.subTest(typ=typ):
                self._validate(typ)
        # 缺省档与 word 档的产物不含解码脚本
        for typ in ({}, {"chunk": "word"}):
            with self.subTest(typ=typ):
                js = _render_type(typ, "一切照旧")
                self.assertNotIn("onComplete:function(){e.textContent=s;}", js)

    def test_canvas_kit_forwards_decode_as_node_level_switch(self):
        import canvas_kit as CK

        def steps(**knobs):
            el = {"kind": "text", "id": "lede", "x": 120, "y": 400,
                  "tier": "body", "content": "增长的不是速度", "type": True}
            el.update(knobs)
            by = {s["target"]: s for s in
                  CK.selfcheck_fragment(CK.validate_spec({"elements": [el]}))["steps"]}
            return by["#lede"]["type"]

        self.assertEqual(steps(decode=True), {"decode": True})
        self.assertEqual(steps(decode={"from": "end"}, chunk="word"),
                         {"chunk": "word", "decode": {"from": "end"}})
        with self.assertRaises(ValueError):
            steps(decode={"from": "north"})


if __name__ == "__main__":
    unittest.main()
