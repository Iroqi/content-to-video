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
- `StaggerDistribute` / `StaggerDistributeInProduct`：一组元素的错峰不是逐项等差，
  而是"揭示窗口 × 分配曲线"（GSAP 原生的 `stagger:{amount, ease}`）。判据按 GSAP
  逐点实测的口径算出每一项的出发延迟，再按产物里真正写出的 `amount` 复核。
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

    def test_full_bleed_has_no_image_entrance(self):
        """图版与整页画布都不演配图入场。

        图版根本没有"配图框"，给它"框先落位、图在框里收拢"是在替作者演他不想演
        的动作；整页画布的进场只有硬切与作者在 SVG 里自己演两种。
        """
        for layout in ("plate", "canvas"):
            html = self._html(layout)
            self.assertNotIn('tl.from("#img-seg-a"', html,
                             f"{layout} 页仍在演配图入场：满幅页没有'配图框'")
            self.assertNotIn('tl.fromTo("#img-seg-a > img",{scale:1.06', html,
                             f"{layout} 页仍在演收拢（Ken Burns 起手）")

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
# fromTo 多一组 `{…}`，所以第三组是可选的。组内可以出现一层嵌套花括号
# （错峰写成 `stagger:{amount:…,ease:…}` 之后才有这件事），所以用非贪婪的
# `.*?`：先试着在第一个 `}` 收口，收不住（后面不是 `,时刻)`）就继续往后找。
# 写成 `[^}]*` 会让带错峰的那几条补间**整条匹配不上**——按照中间的补间名字
# 做判据那一天，它们会被静默跳过：那不是通过，是失明。
_TWEEN_RE = re.compile(
    r'tl\.(?:from|fromTo|to)\("([^"]+)",\{(.*?)\}(?:,\{(.*?)\})?,([\d.]+)\)')


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

    def test_ladder_catches_a_broken_ladder(self):
        """判据自己要能红：两种坏形状都必须被报出来。

        - 间隔压到 1 帧 —— 观众读到的是同一拍；
        - 首尾相接 —— 那是逐条出现，不是错峰。
        """
        layers = _ladder_layers(self._html(), "seg-a")
        sub = dict(layers[-1], at=layers[-2]["at"] + 1.0 / FPS)
        self.assertTrue(_ladder_verdicts(layers[:-1] + [sub]),
                        "1 帧的间隔被判据放过：这条判据等于没写")
        tail = dict(layers[-1],
                    at=layers[-2]["at"] + layers[-2]["dur"] + 0.2)
        self.assertTrue(_ladder_verdicts(layers[:-1] + [tail]),
                        "首尾相接被判据放过")

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


# ── 错峰的分配：揭示窗口 × 曲线，不是逐项等差 ──────────────────────────────────
# 前沿揭示的边不是一个匀速的节拍器。Apple 发布会大标题、Framer 的 staggered
# container transform、现代 UI 的列表入场，顺序都是"先疏后密"：最先几个各自站定让
# 人读出来，后面的连成一条线扫过收尾。GSAP 原生就有这一枚旋钮——
# `stagger:{amount: W, ease: "…"}`，`amount` 是**整组的揭示窗口**、`ease` 把这一个
# 窗口按曲线分给各项。两条书面性质的判据：
#
#   1. 相邻间隔不得等差：等差的边是匀速推进，那是扫描线、不是被推上来的字；
#   2. 揭示快慢不得由项数决定：写"每项隔多久"时总时长 = 步长 ×（项数−1），同一个
#      模板在 4 字标题上是 0.036s、12 字上是 0.132s。
#
# 下方 `_distribute_delays` 的口径是对着钉固的 gsap-3.14.2 实测出来的（沙箱里
# 加载那份 bundle 跑 `gsap.utils.distribute({amount:0.4,ease:"power2.out"})`，5 项返回
# [0,.231,.350,.394,.400]、12 项仍以 .400 收尾），不是照文档抄的。
DISTRIBUTE_GAP_RATIO = 2.0     # 最疏/最密的相邻间隔比；等差恒为 1.0


def _distribute_delays(window, ease, n):
    """GSAP `stagger:{amount:W,ease:…}` 的逐项出发延迟：`W × f(i/(n−1))`。"""
    if n <= 1:
        return [0.0]
    f = E.curve(ease, "none")
    return [window * f(i / float(n - 1)) for i in range(n)]


def _gaps(delays):
    return [b - a for a, b in zip(delays, delays[1:])]


class StaggerDistribute(unittest.TestCase):
    """一组元素的错峰：**窗口 + 分配曲线**，两者都只有一个真源。"""

    def setUp(self):
        _anim = T.load_template()["animation"]
        self.ease = _anim["staggerDistribute"]["ease"]
        self.apple = _anim["opening"]["apple"]

    def test_ease_is_in_the_pinned_catalog(self):
        """分配曲线必须能解析：GSAP 认不出的档不报错、直接退回缺省缓动——
        作者以为"先疏后密"，成片里是等差，而那正是这一层要消灭的东西。"""
        self.assertIsNotNone(E.split_ease(self.ease),
                             f"staggerDistribute.ease={self.ease!r} 不在钉固 GSAP 的目录里")

    def test_steps_are_not_evenly_spaced(self):
        """最疏的那个间隔至少是最密的两倍：等于 1 就是匀速推进的节拍器。"""
        gaps = _gaps(_distribute_delays(1.0, self.ease, 12))
        ratio = max(gaps) / min(gaps)
        self.assertGreaterEqual(
            ratio, DISTRIBUTE_GAP_RATIO,
            f"相邻间隔只差 {ratio:.2f} 倍（{self.ease}）：那条边是匀速的，"
            f"读出来是扫描线而不是揭示")

    def test_steps_decay_so_the_front_of_the_line_can_be_read(self):
        """先疏后密、单调递减：开头的几个各自站定，末尾连成一片扫过。

        反过来（`power*.in`）是"开头几乎同时、末尾越拖越开"——同一句话的后半截
        被逐个甩出来，而观众正等着念它。
        """
        gaps = _gaps(_distribute_delays(1.0, self.ease, 12))
        self.assertEqual(gaps, sorted(gaps, reverse=True),
                         f"间隔不是单调递减：{[round(g, 4) for g in gaps]}")
        self.assertGreater(gaps[0], gaps[-1] * DISTRIBUTE_GAP_RATIO)

    def test_window_does_not_grow_with_item_count(self):
        """最后一项的出发延迟恒为窗口本身：揭示快慢不由标题有几个字决定。

        换成等差写法就不是这样了：同样留着"第一个间隔"，12 项的跨度会变成
        （项数−1）×它，字数一改揭示节奏就跟着改。
        """
        for n in (2, 6, 12, 40):
            with self.subTest(n=n):
                out = _distribute_delays(0.4, self.ease, n)
                self.assertAlmostEqual(out[0], 0.0, places=9)
                self.assertAlmostEqual(out[-1], 0.4, places=9,
                                       msg=f"{n} 项的揭示跨度不等于窗口")

    def test_even_steps_are_reachable_by_turning_the_ease_off(self):
        """曲线写成 `none` 就退回等差——"这一层关得掉"，旋钮不是单向门。"""
        gaps = _gaps(_distribute_delays(0.5, "none", 5))
        self.assertEqual(len(gaps), 4)
        self.assertAlmostEqual(gaps[0], 0.125, places=6)
        self.assertGreater(max(gaps) - min(gaps), -1e-9)
        self.assertLess(max(gaps) - min(gaps), 1e-9)

    def test_template_declares_windows_not_per_item_steps(self):
        """模板里不得再出现"每项一格"的错峰：两组都写 `window`。两层各留一份
        口径的下场是"改了窗口只有一处在动"——正是 `entranceLadder.step` 之前
        那三条补间各算一份起点的同一件事。"""
        for where, group in (("rows", self.apple["rows"]),
                             ("title.chars.mask", self.apple["title"]["chars"]["mask"])):
            self.assertNotIn("stagger", group, f"{where} 还留着逐项 stagger")
            self.assertGreater(group.get("window") or 0.0, 0.0,
                               f"{where} 没写揭示窗口")


class StaggerDistributeInProduct(unittest.TestCase):
    """产物那一头：写进时间线的错峰同样是"窗口 + 曲线"，且窗口不随字数动。"""

    def _html(self, opening_title="本期内容"):
        src = H.sample_source()
        src["opening_title"] = opening_title
        m = H.make_manifest(src)
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")

    @staticmethod
    def _stagger_amount(html, sel):
        m = re.search(r'tl\.from\("' + sel + r'",\{.*?'
                      r'stagger:\{amount:([\d.]+),ease:"([^"]+)"\}', html)
        return (float(m.group(1)), m.group(2)) if m else (None, None)

    def test_timeline_carries_the_window_and_the_curve(self):
        """ `.t-in`（逐字揭示边）与 `.ag-name`（行列表）两条都写出窗口 + 曲线，
        且不再是单个数字（`stagger:0.01` 那种写法会随字数放大揭示跨度）。"""
        html = self._html()
        for sel in (r'#opening \.t-in', r'#opening \.agenda-row \.ag-name'):
            amount, ease = self._stagger_amount(html, sel)
            self.assertIsNotNone(amount, f"{sel} 没写出错峰窗口")
            self.assertEqual(ease, "power2.out", f"{sel} 的分配曲线不是共享的那一枚")
            self.assertGreater(amount, 0.0, f"{sel} 的窗口是 0")
        self.assertNotIn("stagger:0.", html)

    def test_title_length_does_not_change_the_reveal(self):
        """标题 2 个字与 14 个字，最后一个字的出发延迟相同。

        改之前这个延迟 = 步长 ×（字数−1）：同一份模板在长标题上把揭示拖慢 13
        倍，而"这一页的揭示多久"是这一页的属性，不是标题有几个字。
        """
        short = self._stagger_amount(self._html("开场"), r'#opening \.t-in')
        long_ = self._stagger_amount(self._html("本期我们要聊的十四个字"),
                                     r'#opening \.t-in')
        self.assertEqual(short, long_,
                         f"揭示窗口跟着字数走了：{short} vs {long_}")

    def test_turning_the_shared_curve_off_shows_up_in_the_product(self):
        """共享旋钮一动，两组错峰同时变——它们在产物里读同一枚 `staggerDistribute.ease`。"""
        with T.override_layer(['animation.staggerDistribute.ease="none"']):
            html = self._html()
        for sel in (r'#opening \.t-in', r'#opening \.agenda-row \.ag-name'):
            _, ease = self._stagger_amount(html, sel)
            self.assertEqual(ease, "none", f"{sel} 没跟着共享旋钮走")


def _anchor_delays(window, ease, n, anchor="start"):
    """`stagger:{amount, ease, from}` 的逐项出发延迟——GSAP `distribute` 的口径。

    GSAP 先按锚点算每一项到锚点的**距离**，把距离归一化，再用 ease 摊到窗口上：
    `delay_i = W × f((d_i − min d) / (max d − min d))`。`start` 时 `d_i = i`，
    退化成 `_distribute_delays`；`center` 在 2 项上 max == min，整组同时出发
    （GSAP 除零前先判等，这里照抄——那条退化由下面的用例钉住）。

    数字 `anchor` 走另一条路（GSAP 的 axis 分支）：单行时位置从**末尾**倒数，
    `d_i = |n − 1 − T − i|`，归一化分母是 `max d = n − 1 − T`，所以窗口会被压短。
    实测 n=5、W=.4、ease=none：`T=1` → [.1333, 0, .1333, .2667, .4]；
    `T=1.5` → [.2, 0, 0, .2, .4]（第 2、3 项同帧）。见 `NumericFrom`。
    """
    if n <= 1:
        return [0.0]
    if not isinstance(anchor, str):
        # 轴分支：位置按"从末尾倒数"编成 `s = n − 1 − i`，再减 `T`；等价于对
        # 第 ⌊T⌋+1 项取距离 `|i − T|`，但分母是 `max|s − T| = n − 1 − T`，
        # 所以整组窗口被压短（实测 n=5、T=1 → 窗口只剩 0.3）。
        # 越界坐标夹到末项（实测 n=5、from=4/6 → [.4,.3,.2,.1,0]）。
        idx = min(anchor, n - 1)
        d = [abs(i - idx) for i in range(n)]
        hi = max(d)
        if hi <= 0:
            return [0.0] * n
        f = E.curve(ease, "none")
        return [window * f(x / hi) for x in d]
    if anchor == "center":
        d = [abs(i - (n - 1) / 2.0) for i in range(n)]
    elif anchor == "end":
        d = [float(n - 1 - i) for i in range(n)]
    elif anchor == "edges":
        d = [float(min(i, n - 1 - i)) for i in range(n)]
    else:
        d = [float(i) for i in range(n)]
    lo, hi = min(d), max(d)
    if hi <= lo:
        # 距离全相等时 GSAP 的退化不是一种：`center` 的两项各距中点 0.5，归一化后
        # 都是 0（窗口塌掉、同时出发）；`edges` 的两项**都算端点**、距离全 0，
        # 实测整组被推到窗口末尾（[.4, .4]，时间线 0.5s→0.9s）。照抄一个"都归零"
        # 会让门禁以为这一组立刻开始，而屏幕上先空一整个窗口。
        return [window] * n if anchor == "edges" else [0.0] * n
    f = E.curve(ease, "none")
    return [window * f((x - lo) / (hi - lo)) for x in d]


class StaggerAnchor(unittest.TestCase):
    """错峰的第三维：`from` —— 这一组从哪一头起，先后就归谁。

    真值是钉固 gsap@3.14.2 的 `gsap.utils.distribute` 在 Chrome 里逐项采出来的
    （n=7、amount=0.4、ease=power2.out）。写成表是因为这几行同时钉住三件事：
    **窗口不变**（最晚那项恒为 0.4）、**按到锚点的距离单调**、**端点是 0 与 W**。
    """

    WINDOW = 0.4
    EASE = "power2.out"
    PINNED = {
        "start": [0.0, 0.1685, 0.2815, 0.35, 0.3852, 0.3981, 0.4],
        "center": [0.4, 0.3852, 0.2815, 0.0, 0.2815, 0.3852, 0.4],
        "end": [0.4, 0.3981, 0.3852, 0.35, 0.2815, 0.1685, 0.0],
        "edges": [0.0, 0.2815, 0.3852, 0.4, 0.3852, 0.2815, 0.0],
    }

    def test_matches_pinned_gsap_for_every_anchor(self):
        for anchor, want in self.PINNED.items():
            with self.subTest(anchor=anchor):
                got = _anchor_delays(self.WINDOW, self.EASE, 7, anchor)
                for a, b in zip(got, want):
                    self.assertAlmostEqual(a, b, places=3,
                                           msg=f"{anchor}: {[round(x,3) for x in got]}")

    def test_window_survives_the_anchor(self):
        """锚点只改"谁先动"，不改"这一组演多久"：最后一项恒落在窗口上。

        这是上一节那条判据在第二维上的延续——换锚点若把跨度拉长，作者为这一页
        排的节奏（以及"演完没有"的预算）就跟着锚点漂了。
        """
        for anchor in ("start", "center", "end", "edges"):
            for n in (2, 3, 6, 7, 12):
                if n == 2 and anchor in ("center", "edges"):
                    continue      # 两项没有"中间"与"两端"之分，窗口整个塌掉（见下一条）
                with self.subTest(anchor=anchor, n=n):
                    d = _anchor_delays(self.WINDOW, self.EASE, n, anchor)
                    self.assertAlmostEqual(max(d), self.WINDOW, places=9)
                    self.assertAlmostEqual(min(d), 0.0, places=9)

    def test_order_is_monotone_in_distance_from_the_anchor(self):
        """离锚点越近越先动——锚点的全部语义都在这一条上。"""
        for anchor in ("center", "end", "edges"):
            d = _anchor_delays(self.WINDOW, self.EASE, 7, anchor)
            dist = ([abs(i - 3) for i in range(7)] if anchor == "center" else
                    [6 - i for i in range(7)] if anchor == "end" else
                    [min(i, 6 - i) for i in range(7)])
            pairs = sorted(zip(dist, d))
            self.assertEqual([p[1] for p in pairs], sorted(p[1] for p in pairs),
                             f"{anchor}: 先后没跟着距离走——{pairs}")

    def test_center_puts_both_ends_on_the_full_window(self):
        """`center` 的两端落在整窗口上、中项才是 0——"从中间长出来"要按这个读。

        实测 n=7、W=.4、ease=power2.out：[.4, .3852, .2815, 0, .2815, .3852, .4]。
        中心项距离 0，端点距离 `(n−1)/2` 归一化到 1，所以两端各走满一个窗口，
        读起来比 `start` 慢一倍。文档必须这么写，否则作者会以为锚点只改先后。
        """
        d = _anchor_delays(self.WINDOW, self.EASE, 7, "center")
        self.assertAlmostEqual(d[3], 0.0, places=9)
        self.assertAlmostEqual(d[0], self.WINDOW, places=9)
        self.assertAlmostEqual(d[-1], self.WINDOW, places=9)
        self.assertEqual(d[:3], list(reversed(d[4:])), "中心锚点不对称")

    def test_edges_converge_on_the_middle(self):
        """6 项（没有正中项）：两端先起、中间两项最后落，且左右对称。"""
        d = _anchor_delays(self.WINDOW, self.EASE, 6, "edges")
        self.assertAlmostEqual(d[0], 0.0, places=9)
        self.assertAlmostEqual(d[-1], 0.0, places=9)
        self.assertAlmostEqual(d[2], self.WINDOW, places=9)
        self.assertAlmostEqual(d[3], self.WINDOW, places=9)
        self.assertEqual([round(x, 6) for x in d], [round(x, 6) for x in reversed(d)])

    def test_two_items_have_no_middle(self):
        """两项的 `center` / `edges` 都"同时出发"，但落在窗口的两端——不是一个时刻。

        真值是钉固 gsap@3.14.2 实测（W=.4、ease=none，n=2）：
        - `center` → [0, 0]：两项距中点各 0.5，归一化后都 0，时间线 0.5s；
        - `edges`  → [.4, .4]：两项都算端点、距离全 0，GSAP 把整组推到窗口末尾，
          时间线 0.9s——屏幕上先空一整个窗口再一起出现。

        修复前把两者都写成 [0, 0]：`edges` 那条会让作者以为这一组立刻开始（实际
        晚 `amount`），与文档"两项没有中间"的读法也对不上。
        """
        self.assertEqual(_anchor_delays(self.WINDOW, self.EASE, 2, "center"),
                         [0.0, 0.0], "2 项 center 应当退化成同时出发")
        self.assertEqual(_anchor_delays(self.WINDOW, self.EASE, 2, "edges"),
                         [self.WINDOW, self.WINDOW],
                         "2 项 edges 是同时出发，但整组被推到窗口末尾")
    def test_default_anchor_is_reading_order(self):
        """不写 from 与写 start 完全一致：缺省行为不变。"""
        self.assertEqual(_anchor_delays(self.WINDOW, self.EASE, 9),
                         _anchor_delays(self.WINDOW, self.EASE, 9, "start"))
        from _images_schema import STAGGER_ANCHORS
        self.assertEqual(set(STAGGER_ANCHORS), set(self.PINNED),
                         "锚点名只有四枚，且与契约层共用同一份白名单")


class NumericFrom(unittest.TestCase):
    """数字 `from` 不是"项号锚点"，是**网格坐标**：起点从末尾倒数、窗口随之缩短。

    实测（钉固 gsap@3.14.2、W=0.4、ease=none、n=5）：
    - `from:0`   → [0, .1, .2, .3, .4]（与 start 等价）
    - `from:1`   → [.1333, 0, .1333, .2667, .4]（第 2 项单独先动，窗口 0.3）
    - `from:1.5` → [.2, 0, 0, .2, .4]（第 2、3 项**同一帧**）
    - `from:2`   → [.4, .2, 0, .2, .4]（中项先动，与 center 同形）
    旧文档把 `from:1` 写成"从第 3 项起往两边扩散"——方向反了，已按实测改正。
    """

    W = 0.4

    def _delays(self, anchor):
        return _anchor_delays(self.W, "none", 5, anchor)

    def test_zero_equals_reading_order(self):
        self.assertEqual(self._delays(0), self._delays("start"))

    def test_index_starts_from_the_tail_not_the_head(self):
        d = self._delays(1)
        for got, want in zip(d, [0.1333, 0.0, 0.1333, 0.2667, 0.4]):
            self.assertAlmostEqual(got, want, places=3, msg=f"{d}")
        self.assertAlmostEqual(d[1], 0.0, places=9, msg="第 2 项应当是唯一起点")

    def test_out_of_range_index_clamps_to_the_last_item(self):
        """坐标越界夹到末项：与 `end` 等价，不是"整组同时出发"（实测 n=5、from=4/6）。"""
        tail = _anchor_delays(self.W, "none", 5, "end")
        for f in (4, 6, 99):
            self.assertEqual(_anchor_delays(self.W, "none", 5, f), tail)

    def test_fractional_index_collapses_neighbours_onto_one_frame(self):
        """小数 from 的代价：相邻两项同帧，整组窗口同时被压短（不是"非单调"）。"""
        d = self._delays(1.5)
        self.assertAlmostEqual(d[1], d[2], places=9, msg="相邻两项没能同帧")
        self.assertAlmostEqual(max(d), self.W, places=9, msg="最晚一项仍落在窗口上")
        self.assertEqual(d[0], d[3], msg="两侧对称项应当同帧（起点在第 2、3 项之间）")


class StaggerAnchorInProduct(unittest.TestCase):
    """产物那一头：`from` 要真的写进时间线，不能只停在稿件里。"""

    def test_anchor_reaches_the_timeline(self):
        import tempfile
        import os
        import html_renderer as HR

        src = H.sample_source()
        # 第二段改成槽位版式：画布段缺图会直接 error，这条用例只想看时间线
        src["segments"][1].pop("layout", None)
        m = H.make_manifest(src)
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "images")
            os.makedirs(d)
            with open(os.path.join(d, "seg-a.svg"), "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" width="980" '
                        'height="735" viewBox="0 0 980 735">'
                        + "".join(f'<circle id="n{i}" class="node" cx="{i*40}" cy="40" r="8"/>'
                                  for i in range(5))
                        + "</svg>")
            imgs = {"seg-a": {"src": "images/seg-a.svg", "animate": {"steps": [
                {"at": 0, "target": ".node", "from": {"opacity": 0},
                 "to": {"opacity": 1},
                 "stagger": {"amount": 0.36, "ease": "power2.out", "from": "center"}}]}}}
            with contextlib.redirect_stderr(io.StringIO()):
                html = HR.generate_html(m, "audio/combined.wav", images=imgs,
                                        aspect="portrait", theme="dark")
        self.assertIn('"from": "center"', html,
                      "锚点没进时间线：稿件里写了 from，成片里那组元素照旧按读序演")


# ── 方向感知转场：两页朝同一个方向相对运动 ───────────────────────────────────
# 只有新页自下而上揭开时，换页读作"新的一页盖上来"——观众读不到"我们在往下走"。
# 前沿 MG 的换页从来是**两页的相对运动**：新页推进、旧页退场，同一窗口、同一方向、
# 同一曲线；相邻两次换页方向相反，序列才读得出"一阵一阵往前翻"。
#
# 三条判据：
#
#   1. 方向恒相反：第 i 次换页的揭示边从 from 那一侧来，i+1 次从另一侧来；
#   2. 同步到帧：新页的 fromTo 与旧页的 to 同起点、同时长、同曲线——错一帧读作
#      "先盖后走"，两段动作首尾相接（与 `motion.md`「弧顶贴着端点」同一条毛病）；
#   3. 旧页停在 `hold`（0.92）：走满 100% 会留一帧页底，走不满会在页界那帧把两页
#      的揭示边错开成一道缝。
#
# 只在几何档生效：`line` 档的旧页退场是 peel（同一件事的手写版），瞬时切没有运动。
WIPE_HOLD_MIN = 0.85          # 低于它就停在半路，观众会看清"旧页还在"两级台阶
WIPE_HOLD_MAX = 1.0 - 0.01    # 走满 = 页界那一帧是页底


def _render_geometric(style, aspect="portrait"):
    """按指定擦除档出一次 HTML（`override_layer` 与 CLI `--set` 同一条路）。"""
    m = H.make_manifest()
    with T.override_layer([f'animation.segmentWipe.style="{style}"']):
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), aspect=aspect)


def _wipe_tweens(html):
    """从产物里抽出**卡片级**换页补间：新页揭开与旧页退场。

    只认"第一段变量里只有 clipPath、目标不是 `rev-*/img-*/plate-*`"的那些：
    标题裁窗、配图框同样是 clipPath 补间，但它们不叫换页（混进来"每次换页一次
    退场"的账就永远对不上）。揭开那一条的属性键可能是 `clipPath`（偶数页）或
    `"clip-path"`（奇数页，显式键走另一条 CSS 通道，见 `_card_wipe_alt_style`）。
    """
    out = []
    call = re.compile(r'tl\.(fromTo|to)\(\s*"#([^"]+)"\s*,\s*')
    for m in call.finditer(html):
        kind, sid = m.group(1), m.group(2)
        if re.match(r"(rev-|img-|plate-)", sid):
            continue
        # 以本次调用的参数为窗口，避免跨行匹配
        seg = html[m.end():m.end() + 400]
        args = []
        depth, cur = 0, ""
        for ch in seg:
            if ch == "{" and depth == 0 and args:
                pass
            if depth == 0 and ch in ",)":
                args.append(cur)
                cur = ""
                if ch == ")":
                    break
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            cur += ch
        if len(args) < 2:
            continue
        first = args[0]
        if "clipPath" not in first.replace("clip-path", "clipPath"):
            continue
        if kind == "to":
            body = first
            at = args[1]
        else:
            body = args[1]
            at = args[2] if len(args) > 2 else ""
        d = re.search(r"duration:([\d.]+)", body)
        e = re.search(r'ease:"([^"]+)"', body)
        if not (d and e and at):
            continue
        out.append({"kind": kind, "sid": sid, "duration": float(d.group(1)),
                    "ease": e.group(1), "at": float(at)})
    return out


class TransitionDirection(unittest.TestCase):
    def setUp(self):
        self.tpl = T.load_template()["animation"]["segmentWipe"]
        self.hold = float(self.tpl["hold"])

    def test_hold_leaves_exactly_one_sliver_for_the_handover(self):
        """`hold` 只能落在"停半路"与"走满"之间：两端都是看得见的坏。"""
        self.assertGreaterEqual(self.hold, WIPE_HOLD_MIN,
                                "旧页只走到一半就停，页界前会看到两页压成一条缝")
        self.assertLess(self.hold, WIPE_HOLD_MAX,
                        "hold=1 等于旧页先消失、新页再出现，中间一帧页底")

    def test_alternate_reverses_the_geometry(self):
        """反向档是**同一张表的反写**，不是另抄一份几何。"""
        import html_renderer as hr
        for frm, to in hr._WIPES.values():
            self.assertEqual(hr._wipe_reverse(hr._wipe_reverse(frm)), frm,
                             f"{frm} 反两次回不到原值")
            if frm.startswith("inset") or frm.startswith("polygon"):
                self.assertNotEqual(hr._wipe_reverse(frm), frm,
                                    f"{frm} 的反向档与正档相同：方向不会换")

    def test_reverse_table_covers_every_registered_shape(self):
        """`_WIPES` 里每一档的 from/to 都必须能反写——新增几何不许有盲区。"""
        import html_renderer as hr
        for style, (frm, to) in hr._WIPES.items():
            for shape in (frm, to):
                hr._wipe_reverse(shape)          # 认不出就会抛

    def test_inset_reverse_starts_from_the_opposite_edge(self):
        """`inset(100% 0 0 0)`（自下而上）的反向档必须自页顶遮下来。"""
        import html_renderer as hr
        rev = hr._wipe_reverse("inset(100% 0 0 0)")
        self.assertEqual(rev, "inset(0 100% 0 0)",
                         "反向档没换到另一条边：两页会从同一侧来")


class TransitionDirectionInProduct(unittest.TestCase):
    """产物那一头：换页补间真的成对出现、同步、方向交替。"""

    def test_every_geometric_style_hands_over_directionally(self):
        for style in ("vertical", "diagonal", "circle"):
            with self.subTest(style=style):
                tw = _wipe_tweens(_render_geometric(style))
                opens = [t for t in tw if t["kind"] == "fromTo"]
                closes = [t for t in tw if t["kind"] == "to"]
                n_hard_cuts = sum(1 for t in opens if t["duration"] <= 0.02)
                # 画布页（瞬时切）没有擦除补间、也不参与反向下场，所以按"有擦除
                # 的那几次换页"记账，而不是按段数
                self.assertGreaterEqual(len(opens), 2, f"{style}: 换页补间太少")
                # 开屏页从"没有页面"里擦出来，它前面那一页不是卡片：退场条数 =
                # 揭开条数 − 1（开屏那一次）− 画布页硬切的次数
                self.assertEqual(len(closes) + n_hard_cuts + 1, len(opens),
                                 f"{style}: 每次换页应有且只有一次旧页退场")

    def test_handover_is_frame_synchronised(self):
        """退场与新页揭开：同起点、同时长、同曲线。"""
        for style in ("vertical", "diagonal", "circle"):
            with self.subTest(style=style):
                tw = _wipe_tweens(_render_geometric(style))
                opens = [t for t in tw if t["kind"] == "fromTo"]
                closes = [t for t in tw if t["kind"] == "to"]
                n_hard_cuts = sum(1 for t in opens if t["duration"] <= 0.02)
                self.assertTrue(closes, f"{style}: 一条退场都没有")
                pairs = 0
                for c in closes:
                    # 同一页可以多次登场（重演），配上的是**同一时刻**开场的那一条
                    # 揭开是**下一页**的，退场挂在**上一页**上：按时刻配对
                    o = [t for t in opens if abs(t["at"] - c["at"]) < 1e-6]
                    self.assertEqual(len(o), 1,
                                     f"{c['sid']}: {c['at']:.2f}s 退场没有同刻的新页揭开")
                    pairs += 1
                    o = o[0]
                    self.assertAlmostEqual(o["duration"], c["duration"], places=2,
                                           msg=f"{c['sid']}: 退场时长方差")
                    self.assertEqual(o["ease"], c["ease"],
                                     f"{c['sid']}: 两页各走一条曲线，读作脱节")
                self.assertGreater(pairs, 0, f"{style}: 一对揭开/退场都没配上")

    def test_line_style_keeps_the_peel_as_its_only_exit(self):
        """`line` 档不生成反向下场：它的旧页退场是 peel，两套同屏是两种节奏打架。"""
        tw = _wipe_tweens(_render_geometric("line"))
        self.assertEqual([t for t in tw if t["kind"] == "to"], [],
                         "line 档同时排了 peel 与反向下场")

    def test_alt_off_restores_single_direction(self):
        """`segmentWipe.alt=false` 是全片同向的开关：退场整层不生成。

        覆写走 `override_layer`（与 CLI `--set` 同一条路），不是改 `load_template`
        的返回值——渲染器读的是 `load_template` 本身，改返回值什么也没改。
        """
        m = H.make_manifest()
        with T.override_layer(['animation.segmentWipe.style="vertical"',
                               "animation.segmentWipe.alt=false"]):
            with contextlib.redirect_stderr(io.StringIO()):
                html = generate_html(m, "audio/combined.wav",
                                     images=H.sample_images(m))
        self.assertEqual([t for t in _wipe_tweens(html) if t["kind"] == "to"], [],
                         "alt=false 仍在生成反向下场")
