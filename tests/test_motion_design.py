"""动效设计的三条判据：行程预算、满幅页的进场语义、CSS 与 GSAP 抢 transform。

这一组锁的不是"某段 HTML 长什么样"（那是 golden 的事），而是**设计规则本身**——
模板里换一个数、渲染器换一个条件、CSS 里多一行 `transform`，只要破了规则，这里就红。

三组用例各自对应 `.brainstorm/2026-10-26-motion.md` 里的一节：

- `TitleRhythmBudget`：标题四档节奏里，"首帧位移 ≥ 4px"与"末段占比 ≥ 90%"。
  旧表第二档 `y:22 + expo.out` 首帧只走 2.8px——屏幕上读到的是一条纯淡入，
  那一档等于不存在，而它恰恰是模板里最"讲究"的一档。
- `FullBleedEntrance`：满幅页（图版 / 整页画布）不生成配图入场补间。
  图版根本没有"配图框"，给它"框先落位、图在框里收拢"是在替作者演他不想演的动作。
- `CssTransformLeak`：CSS 声明 transform 的元素同时被 GSAP 补间 = 静默失效。
  门禁扫的是**产物里实际补间到的选择器**，不是模板的猜。
"""
import contextlib
import io
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _ease as E
import _template as T
from html_renderer import generate_html

FPS = 30
# 30fps 下"这一帧的位移看得出吗"的分界：4px 约为画面上一条 1px 描边的 4 倍，
# 也是本仓库标题最小可读字号（26px）的 1/6 字身。低于它，位移会被读成抗锯齿变化。
MIN_FIRST_FRAME_PX = 4.0
# "收比放重"：走完 90% 行程所用的时间占比。power3.out 实测 0.44、power4.out 0.37、
# power2.out 0.54——分界定在 0.50：后一半时间只走最后 10% 的行程，才读得出"落定"；
# 到 0.54 就已经是"匀速滑行"，落位没有重量。
TAIL_SHARE = 0.90
TAIL_MAX = 0.50


def _progress_at(ease, t):
    return E.curve(ease, "none")(t)


def _first_frame_px(rh, duration):
    """这一档在第一帧（1/FPS 秒）走了多少像素。"""
    return abs(float(rh.get("y") or 0.0)) * _progress_at(rh["ease"], 1.0 / FPS / duration)


def _tail_frac(ease):
    """走完 TAIL_SHARE 行程所用的时间占比（越小 = 越"收"）。"""
    f = E.curve(ease, "none")
    for i in range(1, 1000):
        t = i / 1000.0
        if f(t) >= TAIL_SHARE:
            return t
    return 1.0


class TitleRhythmBudget(unittest.TestCase):
    def setUp(self):
        self.anim = T.load_template()["animation"]["titleEntrance"]
        self.rhythm = self.anim["rhythm"]

    def test_every_rhythm_tier_moves_visibly_on_the_first_frame(self):
        """每一档的首帧位移 ≥ 4px；否则那条曲线在这条行程上等于淡入。"""
        for i, rh in enumerate(self.rhythm):
            if not rh.get("y"):
                continue          # 第 0 档是缩放档（scale 0.5），不是位移档
            px = _first_frame_px(rh, self.anim["duration"])
            self.assertGreaterEqual(
                px, MIN_FIRST_FRAME_PX,
                f"第 {i} 档（y={rh['y']}, ease={rh['ease']}）首帧只走 {px:.1f}px："
                f"观众看到的是一条淡入，位移档名存实亡。加大 y 或换一条前段更"
                f"陡的曲线（power2/3/4、circ）")

    def test_every_rhythm_tier_settles_early(self):
        """末段要慢：走完 90% 行程不超过全程的一半（`TAIL_MAX`）。

        第 0 档是**签名字幕**：从 0.5 缩放到 1 配 `back.out(1.7)`，过冲是它的意图
        （"从很小的地方弹出来并站定"），不是落位抖动——过冲族的禁令只管后三档
        的位移档。真正要禁的是"标题从下面上来还弹一下"，那是机械感。
        """
        for i, rh in enumerate(self.rhythm):
            tf = _tail_frac(rh["ease"])
            self.assertLessEqual(tf, TAIL_MAX,
                                 f"第 {i} 档（{rh['ease']}）到 {tf:.0%} 才走完 90% 行程，"
                                 f"收得太晚——落位会读成'还在飘'")
            if i == len(self.rhythm) - 1 or rh.get("y"):
                self.assertNotIn(E.split_ease(rh["ease"])[0],
                                 {"back", "elastic", "bounce"},
                                 f"第 {i} 档用了过冲族：标题落位后回弹是抖动不是弹性")

    def test_expo_family_only_on_long_travel(self):
        """`expo` / `power4` 是"前 10% 走掉一半"的档，短行程配它等于没动。

        这条把"别再犯"钉成断言：反过来也成立——行程 ≥100px 时它才够看，
        所以真要用它，`y` 必须够大。
        """
        for i, rh in enumerate(self.rhythm):
            fam = E.split_ease(rh["ease"])[0]
            if fam in ("expo", "power4", "strong"):
                self.assertGreaterEqual(
                    abs(float(rh.get("y") or 0.0)), 30.0,
                    f"第 {i} 档的 {rh['ease']} 配 {rh.get('y')}px 行程太短")


class FullBleedEntrance(unittest.TestCase):
    """满幅页不演配图入场：图版 / 整页画布 / 接续页都不该"浮上来"。"""

    def _html(self, layout=None, keep=False):
        src = H.sample_source()
        if layout:
            src["segments"][0]["layout"] = layout
        m = H.make_manifest(src)
        imgs = H.sample_images(m)
        if keep:
            imgs["seg-a"]["stage"] = "keep"
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav", images=imgs,
                                 aspect="portrait", theme="dark")

    def test_plate_has_no_image_entrance(self):
        html = self._html("plate")
        self.assertNotIn('tl.from("#img-seg-a"', html,
                         "图版页仍在演配图入场：图版没有'配图框'，"
                         "框落位+图收拢读成'海报浮上来'")
        self.assertNotIn('tl.fromTo("#img-seg-a > img",{scale:1.06', html,
                         "图版页仍在演收拢（Ken Burns 起手）：满幅页的进场只有硬切"
                         "与作者在 SVG 里自己演两种")

    def test_canvas_has_no_image_entrance(self):
        html = self._html("canvas")
        self.assertNotIn('tl.from("#img-seg-a"', html)

    def test_slot_layout_still_has_image_entrance(self):
        """反面：槽位版式照旧要演——判据不能一刀切掉所有配图入场。"""
        html = self._html()
        self.assertIn('tl.from("#img-seg-a"', html)
        self.assertIn('tl.fromTo("#img-seg-a > img",{scale:1.06', html)


class CssTransformLeak(unittest.TestCase):
    """CSS 与 GSAP 抢 transform：模板自带的样式表必须干净，注入一条必须抓到。"""

    def setUp(self):
        from _css_guard import css_transform_leaks
        self.leaks = css_transform_leaks
        self.css = (T.__file__.rsplit("/", 1)[0] + "/../templates/composition.css")
        with open(self.css, encoding="utf-8") as f:
            self.css = f.read()
        src = H.sample_source()
        m = H.make_manifest(src)
        with contextlib.redirect_stderr(io.StringIO()):
            self.html = generate_html(m, "audio/combined.wav",
                                      images=H.sample_images(m),
                                      aspect="portrait", theme="dark")

    def test_template_stylesheet_is_clean(self):
        self.assertEqual(self.leaks(self.css, self.html), [],
                         "模板样式表给被补间的元素声明了 transform——"
                         "那一页的补间会静默失效")

    def test_injected_css_transform_is_caught(self):
        got = self.leaks(self.css + "\n.seg-title{transform:translateZ(0)}\n", self.html)
        self.assertEqual(len(got), 1, got)
        css_sel, decl, tween_sel, fn = got[0]
        self.assertEqual(css_sel, ".seg-title")
        self.assertIn("translateZ", decl)
        self.assertIn("title", tween_sel)
        self.assertIn(fn, ("from", "fromTo", "to", "set"))

    def test_pseudo_element_transform_is_allowed(self):
        """伪元素不是 DOM 节点，GSAP 够不着——模板里 `.tagline::before` 那一条合法。"""
        self.assertEqual(self.leaks(self.css + "\n.seg-title::before{transform:none}\n",
                                    self.html), [])

    def test_unrelated_selector_is_not_reported(self):
        self.assertEqual(self.leaks(self.css + "\n.img{transform:none}\n", self.html), [])
