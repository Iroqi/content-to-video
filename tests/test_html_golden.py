"""HTML 生成的快照测试 + 注入回归。

快照是"改动前后行为一致"的安全网：重构 html_renderer / 模板 / 主题时，HTML 不该变就不会变。
有意改视觉时更新快照：  UPDATE_GOLDEN=1 python -m unittest discover -s tests
"""
import contextlib
import io
import os
import unittest

import _helpers as H
from html_renderer import generate_html

CASES = {
    "portrait_dark": dict(aspect="portrait", theme="dark"),
    "landscape_cream": dict(aspect="landscape", theme="cream"),
}


def render(**kw):
    m = H.make_manifest()
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=H.sample_images(m), **kw)


class Golden(unittest.TestCase):
    def test_snapshots(self):
        update = os.environ.get("UPDATE_GOLDEN") == "1"
        os.makedirs(H.GOLDEN_DIR, exist_ok=True)
        for name, kw in CASES.items():
            with self.subTest(case=name):
                html = render(**kw)
                path = os.path.join(H.GOLDEN_DIR, name + ".html")
                if update or not os.path.exists(path):
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(html)
                    continue
                with open(path, encoding="utf-8", newline="") as f:
                    want = f.read()
                if html != want:
                    # 给一个能读的首处差异，而不是两整屏 HTML
                    i = next((k for k, (a, b) in enumerate(zip(html, want)) if a != b),
                             min(len(html), len(want)))
                    self.fail(f"{name}: HTML 与快照不一致（首处差异在第 {i} 字符）\n"
                              f"  now : …{html[max(0, i - 60):i + 80]!r}\n"
                              f"  gold: …{want[max(0, i - 60):i + 80]!r}\n"
                              "有意改动请 UPDATE_GOLDEN=1 重生成快照")

    def test_deterministic(self):
        self.assertEqual(render(**CASES["portrait_dark"]), render(**CASES["portrait_dark"]))


class Structure(unittest.TestCase):
    def test_unknown_aspect_rejected(self):
        with self.assertRaises(ValueError):
            render(aspect="square", theme="dark")

    def _render_with_style(self, style):
        import html_renderer as hr
        orig = hr.load_template

        def patched():
            tpl = orig()
            tpl["animation"]["segmentWipe"]["style"] = style
            return tpl

        hr.load_template = patched
        try:
            return render()
        finally:
            hr.load_template = orig

    def test_wipe_style_switches_geometry(self):
        for style, (frm, to) in {
            "diagonal": ("polygon(0% 100%, 100% 130%, 100% 230%, 0% 200%)",
                         "polygon(0% 0%, 100% 0%, 100% 100%, 0% 100%)"),
            "circle": ("circle(0% at 50% 50%)", "circle(71% at 50% 50%)"),
        }.items():
            with self.subTest(style=style):
                html = self._render_with_style(style)
                self.assertIn(f'clip-path:{frm}', html)
                self.assertIn(f'clipPath:"{to}"', html)
                self.assertNotIn("inset(100% 0 0 0)", html)

    def test_unknown_wipe_style_rejected(self):
        with self.assertRaises(ValueError):
            self._render_with_style("spiral")

    def test_line_led_reveal(self):
        html = render()  # line 已是模板默认档：直渲即引导线转场
        # 引导线 = 新页卡的末子（随父卡 clip-path 裁切，骑揭示边）
        self.assertIn('<div class="reveal-line" id="line-opening" '
                      'style="height:6px"></div>', html)
        self.assertIn('id="line-closing"', html)
        # 揭开几何同 vertical（inset 自下而上），线与其同窗同曲线；
        # line 档自带 0.40s 笔程 + power2.inOut 书写节奏（fixture gap=0.4 恰好容下）
        self.assertIn('clipPath:"inset(0% 0 0 0)",duration:0.40,'
                      'ease:"power2.inOut"},2.00)', html)
        # 线从页底扫到页顶（终位 -thickness：扫到顶恰好缩没进边沿）
        self.assertIn('tl.fromTo("#line-seg-a",{y:1440.0},{y:-6.0,'
                      'duration:0.40,ease:"power2.inOut"},2.00)', html)
        # 旧页被线犁起剥离（1440×0.30=432，power2.in 加速 + -4° 逆旋 + 绕底边
        # 轴 12° 透视后倒——纸"揭"起来，peelTilt/peelPerspective 全在配置里）
        self.assertIn('tl.to("#opening",{y:-432.0,rotation:-4,rotationX:12,'
                      'transformOrigin:"50% 100%",transformPerspective:1000,'
                      'duration:0.40,ease:"power2.in"},2.00)', html)
        # 首卡没有上一页可剥离，但自己的揭屏同样带线（从 t=0 扫起）
        self.assertIn('tl.fromTo("#line-opening",{y:1440.0},{y:-6.0,'
                      'duration:0.40,ease:"power2.inOut"},0.00)', html)
        self.assertEqual(html.count('rotation:-4'), 3)  # 剥离线数 = 换页次数
        # 底缘暗边挂在被揭的旧卡上（opening/seg-a/seg-b 共 3 张，closing 不被揭；
        # 1440×0.08=115px），opacity 与 peel 同窗同曲线拉起
        self.assertEqual(html.count('class="peel-shade"'), 3)
        self.assertIn('<div class="peel-shade" id="shade-opening" '
                      'style="height:115px"></div>', html)
        self.assertIn('tl.to("#shade-opening",{opacity:1,duration:0.40,'
                      'ease:"power2.in"},2.00)', html)
        # 几何档不生成引导线 / 剥离 / 暗边（CSS 规则常驻，查 DOM 特征串）
        plain = self._render_with_style("vertical")
        self.assertNotIn('id="line-', plain)
        self.assertNotIn('rotation:-4', plain)
        self.assertNotIn('id="shade-', plain)


class Injection(unittest.TestCase):
    """信源只是数据：稿件文本里的标记/脚本/占位符都不能变成可执行结构。"""

    def _render_with(self, title=None, text=None):
        s = H.sample_source()
        if title is not None:
            s["segments"][0]["title"] = title
        if text is not None:
            s["segments"][0]["text"] = text
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav", images=H.sample_images(m),
                                 aspect="portrait", theme="dark")

    def test_title_is_escaped(self):
        html = self._render_with(title='<script>alert("x")</script>')
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_sentence_text_cannot_break_out_of_js_string(self):
        html = self._render_with(text='他说"你好"</script><b>。再来一句话凑够长度。')
        self.assertNotIn("</script><b>", html)

    def test_placeholder_literal_in_script_is_inert(self):
        clean = self._render_with(text="普通的一句话，足够长一些。再来一句话凑够长度。")
        evil = self._render_with(text="稿件里写了 __CTV_GSAP__ 和 __CTV_CUES__ 这两个词。再来一句凑长度。")
        # 占位符是单遍替换：稿件里的同名文本不会被当成骨架占位符再次替换
        self.assertEqual(clean.count("<script"), evil.count("<script"))
        self.assertIn("__CTV_GSAP__", evil)


if __name__ == "__main__":
    unittest.main()
