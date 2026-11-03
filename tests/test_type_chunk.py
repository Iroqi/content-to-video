"""`type` 的按单元跳格档（`chunk:"word"`）：一次露出一个语义单元，不在字与字之间切。

缺省的 `type` 每 tick 写回一个字符。字一多就露馅——观众数得出字数，也看得出"机器在
匀速敲"（`references/look.md`「动画：实现要点」把这条列为常见犯法）。按单元跳格的读感
是"一组一组念出来"：观众读到的是短语，节奏交给 step 级 ease，而不是被字数决定。

判据落在产物上：切分与跳格都是渲染端生成的 JS（运行时读 `<text>` 的 textContent 现切），
所以测试从产物 HTML 里取出那段脚本、在 Node 里按进度重放 `onUpdate`，比对每一帧露出的
文本。要证的命题只有三条：

- 每一帧露出的都是**若干个完整单元**（不在词内、不在字间断开）；
- 单元数来自文本本身（拉丁整词、CJK 两字、空白标点附着到前一组），不是按字数除；
- 进度到 1 时是全文，且中间不出现回缩。

用 Node 而不是在 Python 里重写切分：判据要证明"发出去的那段脚本真的这样切"，Python 再写
一份切分规则就变成两份实现互相对照——它们一起错的时候测试照样绿。

用例条数是合并过的：各种文本的切分共用 `_probe`，一条用例覆盖多组输入（"一条用例顶几条"，
见 `check_test_budget.py` 的口径）。
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

# 包一层壳：假节点、`tl.to` 只记账，按 0→N 的进度重放 onUpdate，收每帧文本。
# 文本必须在 eval **之前**写进节点——脚本 build 期会先读一次 textContent 再清空它。
_HARNESS = """
const fs = require("fs");
const body = fs.readFileSync(process.argv[2], "utf8");
const text = process.argv[3], frames = +process.argv[4];
var rec = null, node = {textContent: ""};
var document = {querySelector: function () { return node; }};
node.textContent = text;
eval("var tl={to:function(p,v,at){rec={p:p,v:v,at:at};}};\\n" + body);
const N = rec.v.g, cb = rec.v.onUpdate, out = [];
for (var i = 0; i <= frames; i++) {
  rec.p.g = N * i / frames;
  cb();
  out.push(node.textContent);
}
console.log(JSON.stringify({N: N, seq: out, at: rec.at,
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


@unittest.skipUnless(NODE, "需要 node 才能重放发出去的脚本")
class TypeChunkProduct(unittest.TestCase):
    """产物级判据：单元边界、切分规则、收尾。"""

    def _render(self, typ, text, target="#cap"):
        m = H.make_manifest()
        imgs = {"seg-a": {"src": "images/seg-a.svg",
                          "inline_svg": f'<svg viewBox="0 0 980 735">'
                                        f'<text id="cap">{text}</text></svg>',
                          "animate": {"steps": [
                              {"at_time": 0.0, "target": target, "type": typ,
                               "duration": 1.0, "ease": "none"}]}},
                "seg-b": {"src": "images/seg-b.svg"}}
        # generate_html 会往 stderr 打编排 warn（与本判据无关），别让它淹掉测试输出
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        return _type_script(html, target=target)

    def _probe(self, typ, text, frames=40):
        js = self._render(typ, text)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "case.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        h = os.path.join(tmp, "h.js")
        with open(h, "w", encoding="utf-8") as f:
            f.write(_HARNESS)
        r = subprocess.run([NODE, h, p, text, str(frames)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_cjk_units_land_on_pair_boundaries(self):
        """CJK 两字一组：任意进度都停在组边界上，6/4/2 字分别是 4/2/1 组。"""
        text = "增长的不是速度"
        out = self._probe({"chunk": "word"}, text)
        self.assertEqual(out["N"], 4, out)              # 6 字 / 两字一组
        allowed = {"", "增长", "增长的不", "增长的不是速", text}
        self.assertEqual(set(out["seq"]), allowed, out["seq"])
        # 2 字只剩 1 组（等同整句直出，文档劝退短句用这一档）；4 字是 2 组
        self.assertEqual(self._probe({"chunk": "word"}, "短句")["N"], 1)
        self.assertEqual(self._probe({"chunk": "word"}, "一切照旧")["N"], 2)

    def test_latin_words_stay_whole_and_absorb_spaces(self):
        """英文整词不拆（不出 `don` / `3.`），空白并进前一组（不出裸 `Three`）。"""
        text = "don't e-mail me at 3.5%"
        out = self._probe({"chunk": "word"}, text)
        seq = out["seq"]
        self.assertNotIn("don", seq)
        self.assertNotIn("don'", seq)
        self.assertNotIn("3.", seq)
        self.assertEqual(out["N"], 5, out)              # don't / e-mail / me / at / 3.5%
        self.assertEqual(seq[-1], text)
        spaced = self._probe({"chunk": "word"}, "Three quarters of teams")["seq"]
        self.assertIn("Three ", spaced)
        self.assertNotIn("Three", spaced)

    def test_progress_is_monotone_and_keeps_step_timing(self):
        """进度单调增则露出的长度不减；锚点/时长/曲线仍取 step 级（换档只换"露多少"）。"""
        text = "从 9% 到 10%：增长的不是速度"
        out = self._probe({"chunk": "word"}, text)
        lens = [len(s) for s in out["seq"]]
        self.assertEqual(lens, sorted(lens), out["seq"])
        self.assertEqual(out["seq"][0], "", "build 期必须把文本收成空串")
        self.assertEqual(out["seq"][-1], text)
        self.assertEqual(out["dur"], 1.0, out)
        self.assertEqual(out["ease"], "none", out)
        self.assertIsInstance(out["at"], (int, float), out)

    def test_two_chunks_emit_different_proxies(self):
        """两档的代理量不同（k=字数 / g=组数）：换档不能把缺省档也换成跳格。"""
        word = self._render({"chunk": "word"}, "一切照旧")
        self.assertIn("p={g:0}", word)
        self.assertNotIn("p.k", word)
        char = self._render({}, "一切照旧")
        self.assertIn("s.slice(0,Math.round(p.k))", char)
        self.assertNotIn("G=[],i=0", char)


class TypeChunkContract(unittest.TestCase):
    """契约与产物不能各说各的：chunk 只被 type 认。"""

    def test_chunk_is_a_type_inner_key_only(self):
        """写在 step 级就是未知键（生成期拒）；写在 canvas 节点上要透传进 type。"""
        from _images_schema import validate_images_json
        with self.assertRaises(ValueError):
            validate_images_json({"seg4": {"src": "images/seg4.svg", "animate": {"steps": [
                {"at": 0, "target": "#c", "type": {}, "chunk": "word"}]}}})
        import canvas_kit as CK
        spec = {"elements": [
            {"kind": "text", "id": "lede", "x": 120, "y": 400, "tier": "body",
             "content": "增长的不是速度", "type": True, "chunk": "word"}]}
        by = {s["target"]: s for s in CK.selfcheck_fragment(CK.validate_spec(spec))["steps"]}
        self.assertEqual(by["#lede"]["type"], {"chunk": "word"})


if __name__ == "__main__":
    unittest.main()
