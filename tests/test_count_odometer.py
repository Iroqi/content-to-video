"""`count` 的进位档（`odometer`）：数字滚动要读作"走表"，不是"整片数字同时换"。

缺省的 count 是"一个数从 a 补到 b、每一位同时滚"：`9 → 10` 那半秒个位从 9 滑向 0、
同时十位那一格从无到有。真机取帧看过，"本月 9% → 10%"的进位帧像误帧——两个互不相干
的变化同时发生，读者得自己算才知道这是"涨了"。

里程表档把它拆成"低位走满一轮、上一位才进一格"。判据只能落在产物上：这条曲线是渲染端
生成的 JS（逐帧读 `p.v` 算文本），不是生成期采样，所以测试读 HTML 里的脚本、在 Node 里
按帧重放，比对每一帧印出来的字符串——与真机同一套语义（GAP 的 onUpdate 逐帧 seek）。

为什么要 Node 而不是纯 Python 重算：判据要证明的是"发出去的这段脚本真的这样滚"，不是
"Python 里另写一份公式算得对"。脚本里的浮点取整（`+1e-9`）、取位顺序、零位抑制都在
JS 里，Python 重写一遍就变成两份实现互相对照。
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import html_renderer as HR

NODE = shutil.which("node")

# 把发出去的脚本包一层壳：`document.querySelector` 给一个假节点，`tl.to` 只记账，
# 然后按 0→1 的进度逐帧重放 onUpdate，把每帧的 textContent 收回来。
_HARNESS = """
const fs = require("fs");
const body = fs.readFileSync(process.argv[2], "utf8");
const from = +process.argv[3], to = +process.argv[4], frames = +process.argv[5];
var rec = null, node = {textContent: ""};
var document = {querySelector: function () { return node; }};
eval("var tl={to:function(p,v,at){rec={p:p,v:v,at:at};}};\\n" + body);
const cb = rec.v.onUpdate, done = rec.v.onComplete, out = [];
for (var i = 0; i <= frames; i++) {
  rec.p.v = from + (to - from) * i / frames;
  cb();
  out.push(node.textContent);
}
if (done) { done(); out.push("#" + node.textContent); }
console.log(JSON.stringify(out));
"""


def _count_script(html, sid="seg-a"):
    """从产物 HTML 里抠出这一段的 count 脚本块（`;(function(){…})();`）。"""
    scope = f'"#img-{sid} #num"'
    for line in html.splitlines():
        # 产物里这些补间行带两格缩进（都在同一个脚本模板的缩进里）
        line = line.strip()
        if line.startswith(";(function(){") and scope in line:
            return line
    raise AssertionError(f"产物里找不到 {scope} 的 count 脚本")


@unittest.skipUnless(NODE, "需要 node 才能重放发出去的脚本")
class OdometerCarry(unittest.TestCase):
    """产物级判据：进位顺序、零位抑制、收尾钉值。"""

    def _render(self, count, inline='<svg viewBox="0 0 980 735"><text id="num">0</text></svg>'):
        m = H.make_manifest()
        imgs = {"seg-a": {"src": "images/seg-a.svg", "inline_svg": inline,
                          "animate": {"steps": [
                              {"at_time": 0.0, "target": "#num", "count": count,
                               "duration": 1.0, "ease": "none"}]}},
                "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        return _count_script(html)

    def _frames(self, count, frames=20):
        js = self._render(count)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        p = os.path.join(tmp, "case.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(js)
        h = os.path.join(tmp, "h.js")
        with open(h, "w", encoding="utf-8") as f:
            f.write(_HARNESS)
        r = subprocess.run([NODE, h, p, str(count.get("from", 0)), str(count["to"]),
                            str(frames)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout or "")

    def test_ten_place_waits_for_units(self):
        """9 → 10：个位走满之后十位才翻，中途不会出现"1"打头的数。"""
        seq = self._frames({"from": 9, "to": 10, "odometer": True})
        body = seq[:-1]                      # 最后一格是 onComplete 的收尾钉值
        self.assertEqual(body[0], "9", body)
        self.assertTrue(all(s == "9" for s in body[:-1]), body)
        self.assertEqual(body[-1], "10", body)   # 终点那一帧才是进位后的写法
        self.assertEqual(seq[-1], "#10", seq)    # 收尾钉值也是 10

    def test_no_stray_leading_zero_when_crossing_digits(self):
        """0 → 1286：走到 148 时印 "148"，不是 "0148"；终点是 1286。"""
        seq = self._frames({"from": 0, "to": 1286, "odometer": True}, frames=60)
        body = seq[:-1]
        self.assertEqual(body[0], "0", body)
        for s in body:
            self.assertFalse(s.startswith("0") and len(s) > 1, f"高位挂了 0：{s}")
        self.assertEqual(body[-1], "1286", body)

    def test_small_move_never_touches_higher_places(self):
        """10.4 → 10.6：只有末位在动，十位与个位一位都不许翻。"""
        seq = self._frames({"from": 10.4, "to": 10.6, "decimals": 1, "odometer": True})
        body = seq[:-1]
        self.assertTrue(all(s.startswith("10.") for s in body), body)
        self.assertEqual(body[-1], "10.6", body)
        self.assertGreater(len({s for s in body}), 1, "末位根本没动，等同没演")

    def test_carry_step_is_monotone_inside_a_place(self):
        """每一位只往上走、不退：7 → 9 的个位序列是 7,7,8,8,…,9。"""
        seq = self._frames({"from": 7, "to": 9, "odometer": True}, frames=8)
        digits = [int(s) for s in seq[:-1]]
        self.assertEqual(digits, sorted(digits), digits)
        self.assertEqual(digits[0], 7, digits)
        self.assertEqual(digits[-1], 9, digits)

    def test_completion_pins_exact_end_value(self):
        """收尾钉值必须是 to 的写法：缓动停在 9.999 时末位不许差一格。"""
        seq = self._frames({"from": 6.02, "to": 6.09, "decimals": 2, "odometer": True},
                           frames=15)
        self.assertEqual(seq[-1], "#6.09", seq)

    def test_odometer_off_keeps_whole_number_roll(self):
        """不写 odometer 就走原来的整片滚动：判据不能把缺省档也改成走表。"""
        js = self._render({"from": 9, "to": 10})
        self.assertIn("p.v.toFixed(0)", js)
        self.assertNotIn("odometer", js)


class CountContractShape(unittest.TestCase):
    """契约与产物不能各说各的：`odometer` 只被 count 认，别的原语写它就是未知键。"""

    def test_odometer_only_on_count(self):
        m = H.make_manifest()
        imgs = {"seg-a": {"src": "images/seg-a.svg",
                          "inline_svg": '<svg viewBox="0 0 980 735"><g id="n"/></svg>',
                          "animate": {"steps": [
                              {"at_time": 0.0, "target": "#n", "odometer": True,
                               "to": {"opacity": 1}}]}},
                "seg-b": {"src": "images/seg-b.svg"}}
        html = HR.generate_html(m, "audio/combined.wav", images=imgs, aspect="portrait")
        self.assertNotIn("odometer", html)


if __name__ == "__main__":
    unittest.main()
