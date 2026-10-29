"""动效设计的三条判据：行程预算、满幅页的进场语义、CSS 与 GSAP 抢 transform。

这一组锁的不是"某段 HTML 长什么样"（那是 golden 的事），而是**设计规则本身**——
模板里换一个数、渲染器换一个条件、CSS 里多一行 `transform`，只要破了规则，这里就红。

三组用例各自对应一组动效设计规则：

- `TitleRhythmBudget`：标题四档节奏里，"首帧位移 ≥ 4px"与"末段占比 ≥ 90%"。
  旧表第二档 `y:22 + expo.out` 首帧只走 2.8px——屏幕上读到的是一条纯淡入，
  那一档等于不存在，而它恰恰是模板里最"讲究"的一档。
- `FullBleedEntrance`：满幅页（图版 / 整页画布）不生成配图入场补间。
  图版根本没有"配图框"，给它"框先落位、图在框里收拢"是在替作者演他不想演的动作。
- `CssTransformLeak`：CSS 声明 transform 的元素同时被 GSAP 补间 = 静默失效。
  门禁扫的是**产物里实际补间到的选择器**，不是模板的猜。
- `FrameArcPath` / `FrameArcInProduct`：位移走弧线——容器入场的两条轴各配一条缓动。
  两轴**同缓动**时合成路径恒为直线（弯曲度解析式恒等于 0），写了副轴也等于没写，
  所以判据量的是"离弦的最大距离"而不是"有没有第二条补间"。
- `EntranceLadder` / `EntranceLadderInProduct`：同一屏的多层入场有先后——层序按读序、
  相邻层错开 ≥2 帧、后一拍压在前一拍里。三处（容器入场 / 内容层收拢 / 持续运镜）
  的起点是**同一个阶梯**，改一个 `step` 三处一起动。
"""
import contextlib
import io
import math
import os
import re
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _ease as E
import _template as T
from html_renderer import generate_html, _cycle_pick

FPS = 30
# 样式表的真实路径。必须走 os.path 拼绝对路径：Windows 上
# `"D:/a/x/scripts/_template.py" + "/…"` 会被当成字面量目录名（`_template.py`
# 被当作一个**文件夹**），`..` 不参与归一，open 直接 ENOENT。
# `_assets/` 与 `_template.py` 同在 scripts/ 下，所以只往上取一层目录。
TEMPLATE_CSS = os.path.join(os.path.dirname(os.path.abspath(T.__file__)),
                            "_assets", "composition.css")
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
        self.css = TEMPLATE_CSS
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

    def test_sibling_class_is_not_the_same_element(self):
        """.seg 与 .seg-title 是两个类：前者是后者的子串，但不是同一个元素。

        按子串判会把清白产物判红，而这道门禁一红就是 exit 1——判词的取舍是
        "宁可漏报"，保守只到"选择器链的后缀"。
        """
        self.assertEqual(
            self.leaks(self.css + "\n.seg{transform:translateZ(0)}\n", self.html), [])

    def test_descendant_of_tweened_chain_is_reported(self):
        """`#a .b` 命中 `.b` 的那批元素——后缀相同，必须报。"""
        from _css_guard import _matches
        self.assertTrue(_matches("#seg-a .seg-title", ".seg-title"))
        self.assertTrue(_matches(".seg-title", "#seg-a .seg-title"))
        self.assertFalse(_matches("#seg-a .seg-title", "#seg-a"))  # 祖先与后代不相交

    def test_prefixed_transform_declaration_is_seen(self):
        """`-webkit-transform` 同样往元素上写 transform，漏认等于门禁开天窗。"""
        got = self.leaks(self.css + "\n.seg-title{-webkit-transform:translateZ(0)}\n",
                         self.html)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][0], ".seg-title")

    def test_line_wrapped_selector_list_is_seen(self):
        """选择器跨行写在逗号之后：`.a,\\n.b{…}` 里的两段都得认，不只最后一行那一段。"""
        got = self.leaks(self.css + "\n.seg-title,\n#seg-a{transform:translateZ(0)}\n",
                         self.html)
        self.assertEqual(sorted(c for c, *_ in got), ["#seg-a", ".seg-title"])


# ── 弧线：一条位移的两条轴 ────────────────────────────────────────────────
# 判据的刻度与「首帧位移 ≥ 4px」同源，都是 30fps 下"这一帧看得出吗"的分界：
# 4px 约是一条 1px 描边的四倍；弯曲度占弦长 10% 是"读得出弯"与"读成直线"的分界。
ARC_MIN_BOW_PX = 4.0
ARC_MIN_BOW_FRAC = 0.10
# 副轴幅度 / 主轴幅度。下界保证弯曲度够，上界保证它还是"沿这条轴进场"——
# 超过 0.45 读成"从斜下方飞进来"，主轴就不再承担来向。
ARC_AMP_RATIO = (0.20, 0.45)
# 弧顶（离弦最远的那一帧）落在行程中段：贴到端点上是折线（两段动作首尾相接），
# 不是一条弧。
ARC_APEX = (0.20, 0.80)


def _arc_metrics(main_amp, sub_amp, ease_main, ease_sub, n=2000):
    """双轴位移合成的路径：弯曲度（px / 占弦长比例）与弧顶时刻。

    两轴同起点同时长、各配一条缓动，路径是

        P(t) = (A·(1 − f_main(t)),  B·(1 − f_sub(t)))      终点在 (0,0)

    两轴**同缓动**时 f_main ≡ f_sub，P 恒在「起点→终点」那条弦上，垂距恒为 0——
    "写了副轴"和"没写副轴"在这一个数上分得开，改档位表时骗不过去。
    """
    f_main = E.curve(ease_main, "none")
    f_sub = E.curve(ease_sub, "none")
    A, B = float(main_amp), float(sub_amp)
    chord = math.hypot(A, B)
    if chord <= 0.0:
        return 0.0, 0.0, 0.0
    best, apex = 0.0, 0.0
    for i in range(n + 1):
        t = i / n
        px = A * (1.0 - f_main(t))
        py = B * (1.0 - f_sub(t))
        # 点到「原点 → (A, B)」这条弦的垂距
        d = abs(px * B - py * A) / chord
        if d > best:
            best, apex = d, t
    return best, best / chord, apex


class FrameArcPath(unittest.TestCase):
    """配图容器入场走弧线：两条轴各配一条缓动，不是沿一条轴直来直去。

    对应 `references/motion.md`「位移走弧线」那三条判据。四档轮换的只有**弧向**，
    幅度和曲线是同一套——弧向不一样，连续四张图才不会朝同一侧弯。
    """

    def setUp(self):
        self.a_img = T.load_template()["animation"]["imageEntrance"]
        self.arc = self.a_img["arc"]
        self.rhythm = self.arc.get("rhythm") or [{"x": self.arc["x"]}]

    def _tiers(self):
        """逐档取到的副轴行程：幅度只有一个真源，轮换只换方向。"""
        amp = float(self.arc.get("x") or 0.0)
        return [(i, amp * float(_cycle_pick({"dir": 1.0}, self.arc.get("rhythm"),
                                            i).get("dir") or 1.0))
                for i in range(len(self.rhythm))]

    def test_sub_axis_ease_differs_from_main_axis(self):
        """两轴同缓动 = 直线：写了 `arc` 也等于没写。

        这一条是整套判据的地基——弯曲度的解析式里两轴缓动相同时恒为 0，
        所以"改了缓动名却没改曲线族"这类改动在这里就红了。
        """
        self.assertNotEqual(
            self.arc["ease"], self.a_img["ease"],
            "副轴与主轴用了同一条缓动：合成路径退化成直线，弯曲度恒为 0")
        bow, frac, _ = _arc_metrics(self.a_img["vert_y"], 16.0,
                                    self.a_img["ease"], self.a_img["ease"])
        self.assertAlmostEqual(bow, 0.0, places=9,
                               msg="同缓动的双轴位移必须是直线（这一条是判据的自检）")

    def test_every_arc_tier_bends_the_path(self):
        """每一档的弯曲度 ≥ 4px 且 ≥ 弦长的 10%；否则那条弧等于不存在。"""
        for i, x in self._tiers():
            bow, frac, _ = _arc_metrics(self.a_img["vert_y"], x,
                                        self.a_img["ease"], self.arc["ease"])
            self.assertGreaterEqual(
                bow, ARC_MIN_BOW_PX,
                f"第 {i} 档（x={x}）弯曲度只有 {bow:.1f}px：屏幕上读到的是一条竖直直线，"
                f"加大副轴幅度或换一条与主轴差得更开的曲线")
            self.assertGreaterEqual(
                frac, ARC_MIN_BOW_FRAC,
                f"第 {i} 档（x={x}）弯曲度只占弦长 {frac:.1%}：副轴太小，弧读不出来")

    def test_every_arc_tier_apex_is_in_the_middle(self):
        """弧顶落在行程中段：贴到端点上是折线，不是弧。"""
        for i, x in self._tiers():
            _, _, apex = _arc_metrics(self.a_img["vert_y"], x,
                                      self.a_img["ease"], self.arc["ease"])
            self.assertTrue(
                ARC_APEX[0] <= apex <= ARC_APEX[1],
                f"第 {i} 档（x={x}）弧顶在 t={apex:.2f}：贴着起点或终点说明两轴在"
                f"首尾各走各的，那是两段动作接起来，不是一条弧")

    def test_sub_axis_amplitude_ratio_in_band(self):
        """副轴幅度 / 主轴幅度 ∈ [0.20, 0.45]：太小读不出弯，太大读成斜向飞入。"""
        main = abs(float(self.a_img["vert_y"]))
        for i, x in self._tiers():
            ratio = abs(x) / main if main else 0.0
            self.assertTrue(
                ARC_AMP_RATIO[0] <= ratio <= ARC_AMP_RATIO[1],
                f"第 {i} 档副轴幅度占比 {ratio:.2f} 越界（x={x}, 主轴={main}）："
                f"小于 {ARC_AMP_RATIO[0]} 弯曲度不够，大于 {ARC_AMP_RATIO[1]} "
                f"'从下面上来'就变成'从斜下方飞进来'")

    def test_arc_direction_rotates(self):
        """弧向必须左右都有：四张图全朝同一侧弯，等于全片有一个固定的歪。"""
        signs = {1 if x > 0 else (-1 if x < 0 else 0) for _, x in self._tiers()}
        self.assertEqual(len(signs), 2,
                         f"四档弧向只有 {signs} 一种：连续几张图朝同一侧弧，"
                         f"读出来是模板的歪，不是每张图各自的来向")


class FrameArcInProduct(unittest.TestCase):
    """产物那一头：两条轴真的落在同一个锚上，且该不演的页面不演。"""

    def _html(self, layout=None):
        src = H.sample_source()
        if layout:
            src["segments"][0]["layout"] = layout
        m = H.make_manifest(src)
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")

    def _frame_tweens(self, html, sid="seg-a"):
        return [ln.strip() for ln in html.splitlines()
                if ln.strip().startswith(f'tl.from("#img-{sid}"')]

    def test_two_axes_share_one_anchor(self):
        """主轴与副轴同起点、同时长——分开写就是两段动作，不是一条弧。"""
        got = self._frame_tweens(self._html())
        self.assertEqual(len(got), 2, f"容器入场应当有两条补间（主轴 + 副轴），实到 {got}")
        self.assertTrue(any("y:40" in ln for ln in got), f"少了主轴 y：{got}")
        self.assertTrue(any("x:" in ln for ln in got), f"少了副轴 x：{got}")
        anchor = {ln.rsplit("},", 1)[-1] for ln in got}
        self.assertEqual(len(anchor), 1, f"两条轴锚在不同时刻 {anchor}：那是折线不是弧")
        dur = {re.search(r"duration:([\d.]+)", ln).group(1) for ln in got}
        self.assertEqual(len(dur), 1, f"两条轴时长不同 {dur}：收尾会分家")

    def test_arc_ease_is_not_the_main_ease_in_product(self):
        got = self._frame_tweens(self._html())
        eases = {re.search(r'ease:"([^"]+)"', ln).group(1) for ln in got}
        self.assertEqual(len(eases), 2, f"产物里两轴用了同一条缓动 {eases}：路径是直线")

    def test_full_bleed_has_no_arc(self):
        """满幅页不演配图入场，弧也跟着没有——判据不能只砍主轴留副轴。"""
        self.assertEqual(self._frame_tweens(self._html("plate")), [])

    def test_zero_amplitude_turns_the_arc_off(self):
        """`arc.x = 0` 是"明确不要这层"，不该退化成一条竖直直线。

        这条同时钉住"幅度只有一个真源"：幅度若也被 `rhythm` 分档，`arc.x=0` 就只
        改掉一半（方向档里还写着幅度），旋钮就成了一个骗人的旋钮。
        """
        with T.override_layer(["animation.imageEntrance.arc.x=0"]):
            got = self._frame_tweens(self._html())
        self.assertEqual(len(got), 1, f"幅度为 0 时副轴不该出现：{got}")
        self.assertIn("y:40", got[0])


# ── 入场阶梯：一屏几层，就有几个先后 ────────────────────────────────────────
# 前沿动效的同一屏从来不是"一起到位"：Framer 的 staggerChildren、Material 的
# staggered container transform、迪士尼十二条里的 Overlapping Action，说的是同一件
# 事——层与层之间有先后。同时开始的两层在观众眼里是**一层**。
#
# 刻度与「首帧位移 ≥ 4px」同源，都是 30fps 下"这一帧看得出吗"：2 帧（66ms）是
# "先后"与"同时"的分界，0.30s 之上就不再是错峰、是 PPT 的逐条出现。
LADDER_MIN_STEP_FRAMES = 2
LADDER_MAX_STEP = 0.30
# 补间行的形状：`tl.from("#img-seg-a",{opacity:0,y:40,duration:0.44,ease:"…"},2.76)`
# fromTo 多一组 `{…}`，所以第三组是可选的；`[^}]*` 够用——补间里不会出现嵌套花括号。
_TWEEN_RE = re.compile(
    r'tl\.(?:from|fromTo|to)\("([^"]+)",\{([^}]*)\}(?:,\{([^}]*)\})?,([\d.]+)\)')


def _tweens(html):
    """产物里所有补间：选择器 / 起点 / 时长。判据只认**真正写进时间线的数**。"""
    out = []
    for m in _TWEEN_RE.finditer(html):
        vars_to = m.group(3) or m.group(2)
        dm = re.search(r"duration:([\d.]+)", vars_to)
        out.append({"sel": m.group(1), "at": float(m.group(4)),
                    "dur": float(dm.group(1)) if dm else 0.0,
                    "vars": vars_to})
    return out


def _ladder_layers(html, sid):
    """这一页的入场层，按**读序**排：题头组·标题 → 题头组·副标题 → 画面·配图。

    只取每层最早那一拍（每层可能有多条补间：标题是裁窗 + 位移两条，配图是主轴 +
    副轴两条）。缺的层不补位——图版没有画面层，阶梯就只有题头组，那是对的。
    """
    tw = _tweens(html)
    want = ((f"#rev-title-{sid}", f"#title-{sid}"),
            (f"#rev-tag-{sid}", f"#tag-{sid}"),
            (f"#img-{sid}", f"#cmp-{sid}"))
    layers = []
    for group in want:
        hits = [t for t in tw if t["sel"] in group]
        if hits:
            layers.append(min(hits, key=lambda t: t["at"]))
    return layers


def _ladder_verdicts(layers, fps=FPS):
    """阶梯的三条判据，返回违反项（空 = 合格）。

    三条各拦一种"以为错峰了其实没有"：
      间隔 < 2 帧   —— 打印出来差 25ms，观众看到的是同时（改版前的实况）
      间隔 > 0.30s  —— 不是错峰，是逐条出现
      后一层起点 ≥ 前一层终点 —— 首尾相接，Overlapping Action 反过来写着
    """
    bad = []
    min_step = LADDER_MIN_STEP_FRAMES / float(fps)
    for i in range(1, len(layers)):
        prev, cur = layers[i - 1], layers[i]
        step = cur["at"] - prev["at"]
        if step < min_step - 1e-9:
            bad.append(f"第 {i} 层只比上一层晚 {step * 1000:.0f}ms（< {min_step * 1000:.0f}ms"
                       f" = {LADDER_MIN_STEP_FRAMES} 帧）：观众读到的是同一拍")
        if step > LADDER_MAX_STEP + 1e-9:
            bad.append(f"第 {i} 层比上一层晚 {step:.2f}s（> {LADDER_MAX_STEP}s）："
                       f"那是逐条出现，不是错峰")
        if cur["at"] >= prev["at"] + prev["dur"] - 1e-9:
            bad.append(f"第 {i} 层（{cur['at']:.2f}）起于上一层收尾"
                       f"（{prev['at'] + prev['dur']:.2f}）之后：首尾相接就不是一串动作")
    return bad


class EntranceLadder(unittest.TestCase):
    """同屏多层入场的先后：层序按读序，相邻层错开一档，且后一拍压在前一拍里。"""

    def _html(self, layout=None, dur=2.0):
        src = H.sample_source()
        if layout:
            src["segments"][0]["layout"] = layout
        m = H.make_manifest(src, dur=dur)
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")

    def test_default_template_passes_the_ladder(self):
        got = _ladder_verdicts(_ladder_layers(self._html(), "seg-a"))
        self.assertEqual(got, [], f"默认模板的入场阶梯不合格：{got}")

    def test_layer_order_follows_the_reading_order(self):
        """标题 → 副标题 → 配图。配图是另一层（画面），排在整个题头组之后。

        改版前配图（0.20s）比副标题（0.225s）早 25ms——不到一帧，画面抢在题头组
        第二行前面到场，把题头组两行拆散了。
        """
        at = [round(x["at"], 3) for x in _ladder_layers(self._html(), "seg-a")]
        self.assertEqual(len(at), 3, f"槽位页应当有三层入场，实到 {at}")
        self.assertEqual(at, sorted(at), f"层序不是读序：{at}")

    def test_ladder_catches_a_sub_frame_gap(self):
        """判据自己要能红：把步距写成 1 帧，同一套函数必须报出来。"""
        layers = _ladder_layers(self._html(), "seg-a")
        layers[-1] = dict(layers[-1], at=layers[-2]["at"] + 1.0 / FPS)
        self.assertTrue(_ladder_verdicts(layers),
                        "1 帧的间隔被判据放过：这条判据等于没写")

    def test_ladder_catches_tail_to_tail(self):
        """后一拍压在前一拍里；首尾相接（PPT 的逐条出现）要被报出来。"""
        layers = _ladder_layers(self._html(), "seg-a")
        layers[-1] = dict(layers[-1], at=layers[-2]["at"] + layers[-2]["dur"] + 0.2)
        self.assertTrue(_ladder_verdicts(layers), "首尾相接被判据放过")

    def test_full_bleed_has_no_picture_rung(self):
        """满幅页没有画面层：那一页第一帧就必须是完成态。

        图版与整页画布都不生成 HTML 标题层（图版的标题画进图里、画布的标题由
        作者在 SVG 里画），也不生成配图入场——两种"没有"都该被认出来，否则把
        "全空"读成"题头组正常"也是错的。
        """
        for layout in ("plate", "canvas"):
            self.assertEqual(_ladder_layers(self._html(layout), "seg-a"), [],
                             f"{layout} 页不该有任何入场阶梯层")
            tw = _tweens(self._html(layout))
            self.assertFalse([t for t in tw if t["sel"].startswith(("#img-", "#cmp-"))],
                             f"{layout} 页出现了配图入场")

    def test_step_is_not_compressed_away_on_a_short_segment(self):
        """步距不随段长压缩：它是"看得出先后"的感知下限，压缩等于把这一层关掉。"""
        short = _ladder_layers(self._html(dur=1.0), "seg-a")
        self.assertEqual(len(short), 3, f"短段仍应有三层：{short}")
        gap = short[-1]["at"] - short[-2]["at"]
        self.assertGreaterEqual(
            gap, LADDER_MIN_STEP_FRAMES / FPS - 1e-9,
            f"短段上画面层只比副标题晚 {gap * 1000:.0f}ms：阶梯被段长压缩掉了")

    def test_one_knob_moves_both_consumers(self):
        """步距只有一个真源：容器入场与内容层收拢两处一起动。

        两处各算一份起点的老样子是"改了 knob 只有一处在动"，成片里读作框已经到位、
        图还在旧时刻收。
        """
        def times():
            tw = _tweens(self._html())
            settle = min((t for t in tw if t["sel"] == "#img-seg-a > img"),
                         key=lambda t: t["at"])
            frame = min((t for t in tw if t["sel"] == "#img-seg-a"),
                        key=lambda t: t["at"])
            return {"frame": frame["at"], "settle": settle["at"]}

        base = times()
        with T.override_layer(["animation.entranceLadder.step=0.24"]):
            got = times()
        for key in ("frame", "settle"):
            self.assertAlmostEqual(got[key] - base[key], 0.24 - 0.14, places=2,
                                   msg=f"{key} 没跟着步距走：阶梯在两处不是同一个数")


class EntranceLadderInProduct(unittest.TestCase):
    """golden 那一头：四个画幅/版式样本里的阶梯同样成立。"""

    def test_every_golden_passes_the_ladder(self):
        for name in sorted(os.listdir(H.GOLDEN_DIR)):
            if not name.endswith(".html"):
                continue
            with open(os.path.join(H.GOLDEN_DIR, name), encoding="utf-8") as fh:
                html = fh.read()
            layers = _ladder_layers(html, "seg-a")
            self.assertTrue(layers, f"{name}: 没解析到入场层")
            self.assertEqual(_ladder_verdicts(layers), [],
                             f"{name}: 入场阶梯不合格 {[round(x['at'], 2) for x in layers]}")
