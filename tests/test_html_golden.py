"""HTML 生成的快照测试 + 注入回归。

快照是"改动前后行为一致"的安全网：重构 html_renderer / 模板 / 主题时，HTML 不该变就不会变。
有意改视觉时更新快照：  UPDATE_GOLDEN=1 python -m unittest discover -s tests
"""
import contextlib
import html as _htmlmod
import io
import os
import re
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
from html_renderer import generate_html

CASES = {
    "portrait_dark": dict(aspect="portrait", theme="dark"),
    "landscape_dark": dict(aspect="landscape", theme="dark"),
    # 并置对比：两格各自一条补间（`panes[i].animate`），两画幅各一份。
    # 单独立用例是因为这一版式的 DOM 与时间线作用域都另成一套（`#img-{sid}-{i}`），
    # 槽位版式的快照照不到它。
    "compare_portrait": dict(aspect="portrait", theme="dark", _case="compare"),
    "compare_landscape": dict(aspect="landscape", theme="dark", _case="compare"),
}


def _compare_source():
    """样例稿改一段成并置对比（段内两句，正好一格一拍）。"""
    src = H.sample_source()
    src["segments"][0]["layout"] = "compare"
    src["segments"][0]["text"] = "左边是常见写法。右边是这一版的写法。"
    return src


def _compare_images(manifest):
    imgs = H.sample_images(manifest)
    imgs["seg-a"] = {
        "src": ["images/seg-a-0.svg", "images/seg-a-1.svg"],
        "panes": [
            {"animate": {"steps": [{"at": 0, "target": "#a",
                                    "to": {"opacity": 0.4}}]}},
            {"animate": {"steps": [{"at": 1, "target": "#b",
                                     "to": {"opacity": 1}}]}},
        ],
    }
    return imgs


def render(**kw):
    case = kw.pop("_case", None)
    m = (H.make_manifest(_compare_source()) if case == "compare"
         else H.make_manifest())
    imgs = _compare_images(m) if case == "compare" else H.sample_images(m)
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav", images=imgs, **kw)


class Golden(unittest.TestCase):
    def test_snapshots(self):
        update = os.environ.get("UPDATE_GOLDEN") == "1"
        os.makedirs(H.GOLDEN_DIR, exist_ok=True)
        for name, kw in CASES.items():
            with self.subTest(case=name):
                html = render(**kw)
                path = os.path.join(H.GOLDEN_DIR, name + ".html")
                if update:
                    with open(path, "w", encoding="utf-8", newline="\n") as f:
                        f.write(html)
                    continue
                # 缺文件必须报错，不能静默新建后 pass：那样快照文件一次误删
                # 就会让这条测试变成"每次都比自己"的自证，而它正是重构时唯一的
                # 安全网。快照要更新时显式 UPDATE_GOLDEN=1。
                self.assertTrue(
                    os.path.exists(path),
                    f"缺快照 {path}——快照是重构的安全网，不该被静默重建。"
                    "有意更新请显式跑 UPDATE_GOLDEN=1")
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
    def test_no_aspect_scoped_geometry_in_css(self):
        """CSS 里不许存第二份"随画幅变"的观感常量。

        SKILL.md「视觉真源」那条契约：随画幅变、或要与模板数值联动的量，一律
        进 _template.py（经 --ctv-* 注入）。这条机械地把它钉住——扫 CSS 里
        剩下的每一条 [data-aspect] 规则：只允许**布局定位**类属性（left/top/
        width 这类"这段内容放哪"，两画幅结构本来就不同），出现**观感值**
        （gradient 几何、颜色、字号、圆角、阴影、透明度）就是第二份真源。

        真实踩过的坑：段落氛围光 .seg-card::after 的径向渐变几何
        （58%×42% 居中 vs 46%×52% 偏媒体区）在 CSS 里写了两份，改模板时它们
        不动——两画幅从此各自漂移，且没有任何报错。
        """
        # 这些属性天然随画幅变：它们是"东西放在哪"，不是"东西长什么样"。
        LAYOUT_PROPS = {
            "left", "right", "top", "bottom", "inset", "margin", "margin-top",
            "margin-bottom", "margin-left", "margin-right", "width", "height",
            "line-height", "line-clamp", "flex-direction", "align-items",
            "justify-content", "gap", "text-align", "text-wrap", "font-size",
            "position", "display", "grid-template", "order",
        }
        # 观感值：这些出现在 [data-aspect] 块里就是第二份真源。
        APPEARANCE = re.compile(
            r"(gradient\(|color-mix|#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|"
            r"box-shadow|text-shadow|border-radius|opacity|font-weight|"
            r"letter-spacing|filter|backdrop)", re.S)

        css_path = os.path.join(H.SKILL_DIR, "templates", "composition.css")
        with open(css_path, encoding="utf-8") as f:
            css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)

        offenders = []
        # 逐条取 [data-aspect...] 作用域内的声明块
        for m in re.finditer(r"\[data-aspect[^\]]*\][^{]*\{([^}]*)\}", css):
            for decl in m.group(1).split(";"):
                if ":" not in decl:
                    continue
                prop, value = decl.split(":", 1)
                prop, value = prop.strip(), value.strip()
                if not prop or not value:
                    continue
                # 全程走 var(--ctv-*) 的声明正是这条契约的达成形态，放行。
                if "var(--ctv-" in value:
                    continue
                if APPEARANCE.search(value) and prop not in LAYOUT_PROPS:
                    offenders.append(f"{prop}: {value[:70]}")
        self.assertEqual(
            offenders, [],
            "templates/composition.css 的 [data-aspect] 块里出现了观感值——"
            "这些随画幅变的量属于 scripts/_template.py（经 --ctv-* 注入），"
            "CSS 存第二份会让改模板不带着它们走：\n  " + "\n  ".join(offenders))

    def test_composition_id_has_one_source_of_truth(self):
        """合成 ID 只有一个真源：html_renderer.COMPOSITION_ID。

        根元素的 data-composition-id 与 runtime 注册时间线用的键**必须是同一个值**
        （hyperframes 契约）。它曾经在三处模板里各自硬编码 "main"：改一处漏两处
        时两者不匹配——只注册一条时间线时 runtime 仍能兜住绑定，两条以上则渲染
        **静默冻结在 t=0**（不报错，只是画面定格）。现在 composition.html 与
        runtime.js 走占位符注入；preview.js 不走装配，改为从根元素现读。
        """
        import html_renderer as hr
        tpl_dir = os.path.join(H.SKILL_DIR, "templates")

        # 走装配的两个模板：必须是占位符，不许再出现字面量 ID
        for name in ("composition.html", "runtime.js"):
            with open(os.path.join(tpl_dir, name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("__CTV_COMPOSITION_ID__", src,
                          f"{name} 没有走 __CTV_COMPOSITION_ID__ 占位符")
            self.assertNotIn('"main"', src,
                             f'{name} 残留硬编码的合成 ID "main"——它必须与 '
                             "data-composition-id 同源，不许各写一份")

        # preview.js 不参与装配，只能从根元素现读（它读的就是上面那个真源）
        with open(os.path.join(tpl_dir, "preview.js"), encoding="utf-8") as f:
            pv = f.read()
        self.assertNotIn("__timelines['main']", pv,
                         "preview.js 不应再硬编码合成 ID")
        self.assertIn("dataset.compositionId", pv,
                      "preview.js 必须从根元素的 data-composition-id 现读")

        # 产物：根 ID 与注册键都等于常量，且两者相等
        html = render()
        self.assertIn(f'data-composition-id="{hr.COMPOSITION_ID}"', html)
        self.assertIn(f'window.__timelines["{hr.COMPOSITION_ID}"] = tl;', html)

    def test_ambience_geometry_comes_from_template(self):
        """氛围光几何确实由模板注入，且两画幅取到各自的档。"""
        from _template import load_template
        tpl = load_template()
        v = tpl["layout"]["vertical"]["ambience"]
        h = tpl["layout"]["landscape"]["ambience"]
        # 竖屏那团居中、宽大于高；横屏偏媒体区中心（68% 50%）且高大于宽。
        self.assertEqual((v["cx"], v["cy"]), (50, 50))
        self.assertGreater(v["rx"], v["ry"])
        self.assertEqual((h["cx"], h["cy"]), (68, 50))
        self.assertGreater(h["ry"], h["rx"])
        for amb in (v, h, tpl["canvasAmbience"]):
            for key in ("rx", "ry", "cx", "cy", "alpha", "edge"):
                self.assertIn(key, amb)
        # 变量真的进了产物，且两画幅不同
        for name, aspect, want in (("portrait", "portrait", "58%"),
                                   ("landscape", "landscape", "46%")):
            html = render(aspect=aspect, theme="dark")
            self.assertIn(f"--ctv-amb-rx:{want}", html,
                          f"{aspect} 的氛围光横向半轴没注入成 {want}")

    def test_agenda_ambience_centre_is_right_in_both_aspects(self):
        """.agenda-card::after 不带画幅限定，两个画幅的 agenda 圆心都得填对。

        这条 CSS 曾经写成 [data-aspect="landscape"]，迁到模板时顺手去掉了限定符。
        但 agenda 卡（opening/closing）同时挂 .seg-card，两个选择器都命中它，
        去掉限定后竖屏的开场/收尾页也会开始用 agenda-cx/cy——而模板里竖屏那档
        一度照抄了横屏的 45，氛围光就在竖屏静默挪了位：没有任何报错，只有肉眼
        可见的偏移。守住"每个画幅的 agenda 圆心都不越界"。
        """
        from _template import load_template
        tpl = load_template()
        v = tpl["layout"]["vertical"]["ambience"]
        h = tpl["layout"]["landscape"]["ambience"]
        # 竖屏：agenda 与普通段同档（都是页面正中）。
        self.assertEqual((v["agendaCx"], v["agendaCy"]), (v["cx"], v["cy"]))
        # 横屏：agenda 圆心拨回文字重心，但仍应是"居中偏上"，不能跑到边角。
        self.assertEqual((h["agendaCx"], h["agendaCy"]), (50, 45))
        for amb in (v, h):
            for key in ("agendaCx", "agendaCy"):
                self.assertGreaterEqual(amb[key], 30,
                                        f"agenda 圆心 {key}={amb[key]} 跑得太偏")
                self.assertLessEqual(amb[key], 70)
        # 产物里两个画幅的 agenda 变量都要存在（曾经只注入横屏那档的语义）。
        for aspect, want in (("portrait", "--ctv-amb-agenda-cy:50%"),
                             ("landscape", "--ctv-amb-agenda-cy:45%")):
            self.assertIn(want, render(aspect=aspect, theme="dark"))

    def test_every_verse_line_carries_non_inherited_exemptions(self):
        """occlusion/overlap 必须逐行自带；overflow 只许在 .verse 上。

        两个属性的继承规则不同，别按一套规则打：
          · allow-occlusion / allow-overlap：检查器只认元素自己身上的标记，
            **不继承祖先**。缺了就量那行滚出窗口后仍在原位的 rect，判
            text_occluded 报 error（实测只打 .verse 时报 1 error，补上每行后
            0 error 且 warning 条数不变）。
          · allow-overflow：**继承到子树**（感知探针向上遍历祖先）。打在
            .verse（overflow:hidden 所在层）即覆盖每行，不必逐行重复——
            逐行打是冗余的，删掉后 check 的 Layout 输出逐条不变（7 段稿与
            12 句长段稿实测一致）。
        """
        import re
        for aspect in ("portrait", "landscape"):
            html = render(aspect=aspect, theme="dark")
            lines = re.findall(r'<div class="verse-line"[^>]*>', html)
            self.assertTrue(lines, f"{aspect} 没有渲染出任何 verse-line")
            for tag in lines:
                # 不继承的两个：每行必须自带
                for attr in ("data-layout-allow-overlap",
                             "data-layout-allow-occlusion"):
                    self.assertIn(attr, tag,
                                  f"{aspect} 有一行 verse-line 缺 {attr}：{tag[:90]}")
                # 继承的那个：行上不该有（冗余，且暗示了错误的继承规则）
                self.assertNotIn("data-layout-allow-overflow", tag,
                                 f"{aspect} 的 verse-line 上多余地写了 "
                                 "allow-overflow——它从 .verse 继承，逐行写是冗余")
            # 说话人标签同理：自带前两个，不写 overflow
            for tag in re.findall(r'<span class="verse-who"[^>]*>', html):
                for attr in ("data-layout-allow-overlap",
                             "data-layout-allow-occlusion"):
                    self.assertIn(attr, tag, f"verse-who 缺 {attr}")
                self.assertNotIn("data-layout-allow-overflow", tag,
                                 "verse-who 上多余地写了 allow-overflow")
            # .verse 容器三个齐全：overflow 从这里向下继承
            for tag in re.findall(r'<div class="verse" id="verse-[^"]*"[^>]*>', html):
                for attr in ("data-layout-allow-overflow",
                             "data-layout-allow-overlap",
                             "data-layout-allow-occlusion"):
                    self.assertIn(attr, tag, f".verse 容器缺 {attr}")

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
                # 卡片**自身**的揭屏补间不能用 vertical 的 inset 几何。
                # 不能简写成 `assertNotIn("inset(100% 0 0 0)")`：标题揭示边的裁窗
                # 正是同一串 inset（见 html_renderer._reveal_tween_lines），
                # 那是 `#rev-*` 的补间，与换页几何无关。
                self.assertEqual(
                    re.findall(r'tl\.fromTo\("#(?!rev-)[\w-]+",'
                               r'\{clipPath:"inset\(100% 0 0 0\)"', html), [])

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
        # 剥离线数：整页画布段（fixture 的 seg-b）入场走硬切、既不自己 peel 上一页、
        # 也不被下一页（closing）剥离——所以 4 段里只剩 opening 被 seg-a 揭走这 1 条。
        self.assertEqual(html.count('rotation:-4'), 1)  # 剥离线数 = 换页次数（画布段除外）
        # 底缘暗边只挂在被揭的旧卡上：现在只有 opening 被揭（seg-a/seg-b 因画布硬切
        # 不再被 peel，closing 是末页），1440×0.08=115px，opacity 与 peel 同窗同曲线拉起
        self.assertEqual(html.count('class="peel-shade"'), 1)
        self.assertIn('<div class="peel-shade" id="shade-opening" '
                      'style="height:115px"></div>', html)
        self.assertIn('tl.to("#shade-opening",{opacity:1,duration:0.40,'
                      'ease:"power2.in"},2.00)', html)
        # 几何档不生成引导线 / 剥离 / 暗边（CSS 规则常驻，查 DOM 特征串）
        plain = self._render_with_style("vertical")
        self.assertNotIn('id="line-', plain)
        self.assertNotIn('rotation:-4', plain)
        self.assertNotIn('id="shade-', plain)

    def test_canvas_card_hard_cuts_out_of_transitions(self):
        """整页画布段（fixture 的 seg-b）去模板转场：入场交给导演逐拍"演"。

        实现是把画布段的 wipe 归零，命中既有的"瞬间切"路径——引导线、旧页剥离、
        底缘暗边三处都以 wipe<=0 为闸自动不生成。这里锁住"归零"带来的四个可观察
        后果，以及"只作用于画布档、不动 slot/agenda"的边界。
        """
        html = render()  # line 档（模板默认）
        # 挂载即音频起点（7.20 = seg-b 首句 start_time），不再向后借一个擦除窗口；
        # 逐拍 at/at_time 的时间锚点因此与画面严格对齐（导演第一拍不会落在擦除中途）。
        self.assertIn('<div id="seg-b" class="clip seg-card seg-canvas" '
                      'data-start="7.20"', html)
        # clip-path 补间时长取 0.01（瞬间切：0 时长会被 GSAP 当作 set()，加载即渲染
        # 不裁剪态、预览首帧被画布页盖住；0.01s 不足一帧、视觉仍是瞬间出现），
        # 运动全部由 animate 时间线承担
        self.assertIn('tl.fromTo("#seg-b",{clipPath:"inset(100% 0 0 0)"},'
                      '{clipPath:"inset(0% 0 0 0)",duration:0.01,'
                      'ease:"power2.inOut"},7.20)', html)
        # 画布页自己没有引导线、不被剥走（末页 closing 直接盖住它），也不挂暗边
        self.assertNotIn('id="line-seg-b"', html)
        self.assertNotIn('id="shade-seg-b"', html)
        self.assertNotIn('tl.to("#seg-b",{y:-432', html)
        # 画布页作为"上一页"时同样不被下一页犁起剥离——掀走一张活 diagram 正是
        # "演"要取代的 PPT 翻页感
        self.assertNotIn('tl.to("#seg-a",{y:-432', html)
        # 边界：非画布档（agenda/slot）的转场原样保留，remove 不是全局开关
        self.assertIn('tl.to("#opening",{y:-432', html)
        self.assertIn('id="line-closing"', html)
        # 归零按版式判定而非按擦除档：几何档（vertical，自己的 0.28s + expo.out 曲线）
        # 下画布段同样是硬切（0.01s 瞬间切），相邻 slot 段的擦除不受影响
        vertical = self._render_with_style("vertical")
        self.assertIn('clipPath:"inset(0% 0 0 0)",duration:0.01,ease:"expo.out"},7.20)',
                      vertical)
        self.assertIn('clipPath:"inset(0% 0 0 0)",duration:0.28,ease:"expo.out"},2.12)',
                      vertical)

    def test_canvas_page_keeps_the_templates_three_background_layers(self):
        """C1：画布页的背景交回模板，所以这三层必须真的在。

        门禁现在会 warn"画布 SVG 别自铺满幅底板"，判据是模板本来就在这一页底下画好
        了 .bg 渐变 / .grid 网格 / ::after 氛围光。哪天有人把画布卡里"用不到的层"当死
        DOM 优化掉、或撤掉氛围光，那条 warn 就变成谎话，画面也退回一片死平。
        """
        html = render()
        # 画布卡（fixture 的 seg-b）与槽位卡一样带底两层——它们不是死层
        rest = html.split('id="seg-b"', 1)[1]
        head = rest[rest.index(">") + 1:][:160]
        self.assertIn('<div class="bg"></div>', head)
        self.assertIn('<div class="grid"></div>', head)
        # 画布专属氛围光：整页铺满时槽位那团小光撑不出景深，这里换成近全屏宽带柔光，
        # 覆盖要明显大于两档槽位光，且仍吃本段 accent。
        # 几何现在由 _template.py 的 canvasAmbience 经 --ctv-camb-* 注入，所以这条
        # 断言的是"变量在、值够大"——不是 CSS 里写死了哪几个数字（那是第二份真源）。
        wash = re.search(r'\[data-aspect\] \.seg-card\.seg-canvas::after\{background:'
                         r'radial-gradient\(var\(--ctv-camb-rx\) var\(--ctv-camb-ry\) at'
                         r'[^,]*,color-mix\(in srgb,var\(--seg-accent\)', html)
        self.assertIsNotNone(wash, "画布氛围光规则缺失或不再吃 --seg-accent")
        # 注入的值：近全屏宽带（>100% 宽、>60% 高）
        canvas_rx = int(re.search(r"--ctv-camb-rx:(\d+)%", html).group(1))
        canvas_ry = int(re.search(r"--ctv-camb-ry:(\d+)%", html).group(1))
        self.assertGreater(canvas_rx, 100)
        self.assertGreater(canvas_ry, 60)
        # 明显大于两档槽位光（竖 58/42、横 46/52），否则"换近全屏"这句注释是假的
        for aspect, rx, ry in (("vertical", 58, 42), ("landscape", 46, 52)):
            seg_rx = int(re.search(r"--ctv-amb-rx:(\d+)%", render(aspect=aspect, theme="dark")).group(1))
            seg_ry = int(re.search(r"--ctv-amb-ry:(\d+)%", render(aspect=aspect, theme="dark")).group(1))
            self.assertEqual((seg_rx, seg_ry), (rx, ry),
                             f"{aspect} 槽位氛围光几何与模板档位不符")
            self.assertGreater(canvas_rx, seg_rx)
            self.assertGreater(canvas_ry, seg_ry)
        # 补上的只是 background；content/inset/z-index:-1 由无属性限定的
        # .seg-card::after 提供（几何走 --ctv-amb-*），它一撤本条就变成不渲染的声明
        self.assertIn('.seg-card::after{content:"";position:absolute;inset:0;z-index:-1',
                      html)


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


class CliSuppliedSrcEscaping(unittest.TestCase):
    """CLI 传进来的路径落在属性上下文里，转义只走本模块的 esc()（= html.escape）。

    原先两处手写 `.replace("&","&amp;").replace('"',"&quot;")`——只转了 5 个字符
    里的 2 个，`<` `>` `'` 直接裸插进属性值。标准库现成的 html.escape 就在同一个
    文件里（esc()），手抄半边既漏又会在两处分叉。
    """

    def _render(self, gsap_src):
        m = H.make_manifest()
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m), gsap_src=gsap_src,
                                 aspect="portrait", theme="dark")

    def test_all_five_attribute_specials_are_escaped(self):
        html = self._render("vendor/gs&ap<'>\".min.js")
        self.assertIn("&amp;", html)
        self.assertIn("&lt;", html)
        self.assertIn("&gt;", html)
        self.assertIn("&quot;", html)
        self.assertIn("&#x27;", html)
        self.assertNotIn("gs&ap<'>\".min.js", html)

    def test_plain_url_like_src_is_untouched_by_escaping(self):
        """回归网：不能顺手把 :/? 也编码掉——CDN 地址必须是原样写进 src 的。"""
        src = "https://cdn.example.com/npm/gsap@3.13.0/dist/gsap.min.js"
        html = self._render(src)
        self.assertIn(f'src="{src}"', html)


class AgendaTitleShrink(unittest.TestCase):
    """agenda（开屏/结尾）标题按字数降字号：降到可读下限就停，并说出停在哪。

    标题长度在契约层没有上限（_validate_display_text 只禁分号），所以降字号这条
    路径必须自己设下限：算出来的字号低于图内文字同一条可读下限时，那行字在成片里
    就是读不出来的，而 warn 只报"降字号"、不报"降到了多少"，作者无从判断。
    """

    def _agenda_title_px(self, title, aspect="portrait"):
        s = H.sample_source()
        s["opening_title"] = title
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect=aspect, theme="dark")
        mo = re.search(r'id="title-opening"[^>]*font-size:(\d+)px', html)
        self.assertIsNotNone(mo, "开屏标题上没找到 font-size")
        return int(mo.group(1))

    def test_long_agenda_title_keeps_readable_font_size(self):
        """竖屏可用宽 936px、每行按 CJK 字宽≈字号 估：100 字标题会算出 18px、
        1900 字算出 0px（标题整条消失）。两者都低于 26px 的可读下限。"""
        from _template import MIN_READABLE_FONT_PX
        for n in (100, 1900):
            with self.subTest(chars=n):
                self.assertGreaterEqual(
                    self._agenda_title_px("长" * n), MIN_READABLE_FONT_PX)

    def test_short_agenda_title_keeps_template_size(self):
        """没触发降档时字号仍是模板值（回归网：钳制不能反过来压小正常标题）。"""
        self.assertEqual(self._agenda_title_px("本期内容"), 84)


class AppleOpening(unittest.TestCase):
    """开屏 agenda 卡**默认强绑定**的 v2 苹果风开场编排：主/副双光斑 + 标题逐字
    浮出 + kicker 拆字字距展开 + agenda 行两拍（编号先行、内容滑入），取代通用
    标题入场。作者不写任何键即生效（契约键已随默认强绑定移除）。"""

    def _render_apple(self):
        # 注意：这里刻意**不写** opening_animation——默认强绑定，无键即开启。
        s = dict(H.sample_source(), opening_tagline="AI WEEKLY")
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            return generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")

    def test_apple_opening_emits_choreography(self):
        html = self._render_apple()
        # 主光晕元素 + 副光斑元素 + 编排标记（无键也生成）
        self.assertIn('class="apple-halo" id="halo-opening"', html)
        self.assertIn('class="apple-glow" id="glow-opening"', html)
        self.assertIn('data-opening-anim="apple"', html)
        # 主光：淡入 + 呼吸（v2 参数：in 1.2 / breathe 1.3）。
        # 呼吸的遍数是**有限**的：无限循环会把 GSAP 总时长撑成 1e10 量级
        # （预览进度条 / 按时刻取帧全坏），遍数在生成期按片长折算。
        self.assertIn('tl.fromTo("#halo-opening",{opacity:0},{opacity:0.50', html)
        self.assertIn('duration:1.30,repeat:', html)       # 有限遍数（按片长折算）
        self.assertNotIn("repeat:-1,", html)                # 不再写无限循环
        # 副光：淡入 + x/y/scale 漂移（repeat:-1 yoyo，稳态循环）
        self.assertIn('tl.fromTo("#glow-opening",{opacity:0},{opacity:0.50', html)
        self.assertIn('x:90,y:60,scale:1.35,opacity:0.28,duration:2.60', html)
        # 标题"揭示边"：字在裁剪窗里被推上来（字本身不透明），整行从虚焦收紧。
        # 逐字 opacity 是"打字机"，这里锁住"边在走、字在站定"这个结构：
        # 外层 .t-mk 裁窗、内层 .t-in 只做位移，行级只补 filter 不补 opacity。
        self.assertIn('<span class="t-mk"><span class="t-ch"><span class="t-in">本</span>', html)
        self.assertIn('#opening .t-in",{y:28.56', html)
        self.assertIn('filter:"blur(9px)"', html)
        self.assertIn('tl.from("#opening .seg-title",{filter:"blur(9px)",'
                      'duration:0.80,ease:"power2.out"}', html)
        # 反证：标题这条不能再出现逐字 opacity（那是被替换掉的旧演法）
        self.assertNotIn('#opening .t-ch:not(.t-sp)",{opacity:0', html)
        # kicker：拆字 + 逐字 x 展开（letterSpacing 补间的 transform 等价式）
        self.assertIn('<span class="k-ch">A</span>', html)
        self.assertIn('<span class="k-ch">W</span>', html)
        self.assertIn('#opening .k-ch",{x:(i)=>i*3.36', html)
        # 门禁红线：reflow 属性（letterSpacing 等）不许进补间——它是 error，
        # 且 Lint 有 error 时 browser 五阶段整个被跳过（半副门禁失明）
        self.assertNotIn("letterSpacing", html)
        # agenda 行两拍：编号先行 scale+blur、行内容随后 x 滑入，同 stagger 波次
        self.assertIn('#opening .agenda-row .ag-idx', html)
        self.assertIn('#opening .agenda-row .ag-name', html)
        self.assertIn('stagger:0.12', html)

    def test_kicker_char_spread_step_matches_template(self):
        """逐字 x 的步长 = (tracking − rest) × 画幅 kicker 字号。

        这个等式就是"与 letter-spacing 补间逐像素等价"的代数形式：静态字距
        rest 下第 i 字左缘已位移 i×rest，再叠 x=i×(tracking−rest)，合成
        i×tracking——正是旧 letterSpacing from 态。步长算错（拿错字号、漏减
        rest）时展开量与收拢态对不上，舞跳到一半字距是歪的，而单看画面只是
        "有点不对劲"，必须有数字兜。
        """
        from _template import load_template
        a_k = load_template()["animation"]["opening"]["apple"]["kicker"]
        for aspect, size in (("portrait", 24), ("landscape", 26)):
            with self.subTest(aspect=aspect):
                s = dict(H.sample_source(), opening_tagline="AI WEEKLY")
                m = H.make_manifest(s)
                with contextlib.redirect_stderr(io.StringIO()):
                    html = generate_html(m, "audio/combined.wav",
                                         images=H.sample_images(m),
                                         aspect=aspect, theme="dark")
                step = round((a_k["tracking"] - a_k["rest"]) * size, 2)
                self.assertIn(f'#opening .k-ch",{{x:(i)=>i*{step:.2f}', html)

    def test_kicker_rest_tracking_matches_css(self):
        """config 的 rest 与 CSS 静态 letter-spacing 必须一致。

        逐字 x 的收拢态是 x=0，落点 = CSS 静态字距；rest 与 CSS 漂移的话，
        "收拢"落到的不是设计的那个字距，且没有任何报错。两个真源之间这条
        对账是唯一的绳子。
        """
        css_path = os.path.join(H.SKILL_DIR, "templates", "composition.css")
        with open(css_path, encoding="utf-8") as f:
            css = re.sub(r"/\*.*?\*/", "", f.read(), flags=re.S)
        m = re.search(r"\.agenda-kicker\{[^}]*letter-spacing:([\d.]+)em", css)
        self.assertIsNotNone(
            m, "composition.css 的 .agenda-kicker 没有静态 letter-spacing")
        from _template import load_template
        rest = load_template()["animation"]["opening"]["apple"]["kicker"]["rest"]
        self.assertEqual(float(m.group(1)), rest)

    def test_glow_fade_uses_its_own_duration_and_anchors_after_the_fade(self):
        """副光淡入时长是 glow.duration、漂移锚在淡入收尾——两个键曾互相冒充。

        证伪：淡入时长曾写 `gl["delay"]`（0.3s 一擦黑），`glow.duration: 2.2`
        整条是死配置；漂移锚又只加 delay，淡入换成真时长后漂移会抢在淡入
        还没演完时开始。正确形状：淡入 = 锚 t+delay、时长 duration（都乘 k），
        漂移锚 = t+(delay+duration)*k。
        """
        html = self._render_apple()
        # 淡入：0.30s 起、2.20s 演完（模板 glow.delay=0.3 / duration=2.2，k=1）
        self.assertIn(
            'tl.fromTo("#glow-opening",{opacity:0},{opacity:0.50,'
            'duration:2.20,ease:"sine.out"},0.30)', html)
        # 漂移：锚在淡入收尾 0.30+2.20=2.50，而不是淡入起点 0.30
        self.assertIn('ease:"sine.inOut"},2.50)', html)
        self.assertNotIn('ease:"sine.inOut"},0.30)', html)

    def test_opening_budget_counts_the_idx_tween(self):
        """开场预算的收尾公式要数**每一条真会生成**的补间，idx 那拍也是。

        证伪：公式曾只数字体 chars / kicker / rows.content，漏了 rows.idx。
        模板现值下 content 的收尾永远更晚所以看不出来；把 idx.duration 调大
        （编号演得比内容慢）时，旧公式算出的 need 偏小、k 虚高——编号还在演
        就被判"整支舞演完了"，压不压、压多少全凭巧合。
        """
        from types import SimpleNamespace

        import html_renderer as HR
        ap = {
            "title": {"chars": {"delay": 0, "stagger": 0, "duration": 0.1}},
            "kicker": {"delay": 0, "duration": 0.1},
            "rows": {"stagger": 0,
                     "idx": {"delay": 0, "duration": 2.0},
                     "content": {"delay": 0, "duration": 0.1}},
            "budget": {"minFactor": 0.5},
        }
        rc = SimpleNamespace(anim={"opening": {"apple": ap}})
        card = SimpleNamespace(agenda_row_count=3,
                               seg={"title": "题", "tagline": "t"}, vis=1.0)
        with contextlib.redirect_stderr(io.StringIO()):
            k = HR._apple_opening_budget(rc, card)
        # need = max(0.1, idx 2.0, 0.1, 0.1) = 2.0 → vis 1.0 只装得下一半，压到地板
        self.assertEqual(k, 0.5)

    def test_apple_opening_replaces_generic_title_entrance(self):
        html = self._render_apple()
        # 苹果编排取代通用 back.out 标题入场（两条补间同元素会打架）
        self.assertNotIn('tl.from("#title-opening"', html)
        # 非开屏卡照旧走通用入场（揭示边：只有位移、没有缩放）；closing 不带苹果
        # 编排。不断言具体档位（44 / 56 / 34 / 62px）：标题入场的行程按段序轮换
        # （见 _template.titleEntrance.rhythm），把一个档位写死进断言等于禁止轮换。
        self.assertIn('tl.from("#title-seg-a",{y:', html)
        self.assertNotIn('tl.from("#title-seg-a",{scale:', html)
        self.assertNotIn('tl.fromTo("#halo-closing"', html)
        self.assertNotIn('id="glow-closing"', html)

    def test_canvas_opening_has_no_text_choreography(self):
        """开屏页写 canvas 版式（整页海报）= 无文本编排：没有 halo/glow 元素、
        标题不拆字、编排不生成，通用标题入场也没有（画布页连标题层都不渲染）。

        默认强绑定只覆盖 agenda 版式——canvas 开屏页只有一张图，agenda 的 DOM
        都不生成，苹果风整支舞无处可演。
        """
        s = dict(H.sample_source(), opening_layout="canvas")
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")
        self.assertNotIn('class="apple-halo"', html)
        self.assertNotIn('class="apple-glow"', html)
        self.assertNotIn('class="t-ch"', html)
        self.assertNotIn('class="k-ch"', html)
        self.assertNotIn("data-opening-anim", html)
        self.assertNotIn('tl.from("#title-opening"', html)

    def test_apple_opening_title_escapes_per_char(self):
        """逐字拆 span 拆的是**原文**，每个字符各自转义再包：& / < / > 不能
        被拆散成字面文本。

        先 esc 整句再拆会把 &amp; 拆成 &、a、m、p、; 五个独立 span，浏览器
        不再把它当实体解析，标题 "Q&A" 会在画面上渲染成字面 "Q&amp;A"
        （上墙字段只禁分号，& 这类字符是合法输入，必须能正常上画面）。
        """
        s = dict(H.sample_source(), opening_title="Q&A <版本> 专场")
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")
        # 实体按字符整体包在单个 span 里（浏览器解析后仍是 & / < / >）
        self.assertIn('<span class="t-in">&amp;</span>', html)
        self.assertIn('<span class="t-in">&lt;</span>', html)
        self.assertIn('<span class="t-in">&gt;</span>', html)
        # 语义收口：把每个 span 的内容按 HTML 语义解码后拼接，必须还原出原文。
        # 老 bug（实体被拆散）的解码结果是字面 "Q&amp;A &lt;版本&gt; 专场"，
        # 与原文对不上——这一条拦的是"看着像转义了、渲染出来却错了"。
        spans = re.findall(r'<span class="t-in">(.*?)</span>', html)
        text = "".join(_htmlmod.unescape(s) for s in spans)
        self.assertEqual(text, "Q&A <版本> 专场")


def _apple_manifest(open_dur, n_rows, gap=0.4, seg_dur=2.0):
    """opening 口播 open_dur 秒 + n_rows 个内容段的 manifest。

    不走 _helpers.make_manifest：它给所有句子同一个 dur，而苹果开场的预算
    只在"开屏那段特别短"时才会被触发——那正是这条用例要摆出来的形状。
    行时间轴在这里手排，好让 opening 段的长度与内容段解耦。
    """
    def _seg(i, t):
        return {"id": f"seg{i}", "title": f"第{i}段", "layout": "slot",
                "sentences": [{"index": i, "text": f"第{i}段的第一句。",
                               "start_time": round(t, 3), "duration": seg_dur}]}

    segs = [{"id": "opening", "title": "开场", "tagline": "AI WEEKLY",
             "layout": "agenda",
             "sentences": [{"index": 0, "text": "大家好。",
                            "start_time": 0.0, "duration": open_dur}]}]
    t = open_dur + gap
    for i in range(1, n_rows + 1):
        segs.append(_seg(i, t))
        t += seg_dur + gap
    segs.append({"id": "closing", "title": "小结", "layout": "agenda",
                 "sentences": [{"index": 99, "text": "谢谢。",
                                "start_time": round(t, 3), "duration": seg_dur}]})
    return {"schema_version": 2, "status": "ok", "degraded": {}, "sentences": [],
            "total_duration": round(t + seg_dur, 3), "gap": gap,
            "voice_id": "冰糖", "segments": segs}


class LoopRepeat(unittest.TestCase):
    """氛围循环的遍数按片长折算（而不是 `repeat:-1`）。

    病根：GSAP 把 `repeat:-1` 按 `REPEAT_DELAY=1e10` 折总时长，整条时间线变成
    1e10 量级，`tl.pause()/seek()`、预览进度条、"按时刻取帧"三处同时坏掉。
    事后 `tl.duration(x)` **压不动**（setter 按子项时长比例缩放，1e10 的比例
    仍是 1e10——浏览器里量过），所以只能在生成期就不写无限。
    """

    def test_loop_repeat_tracks_film_span(self):
        from html_renderer import _loop_repeat
        # 20s 片 / 1.3s 一个周期 → 走 15 遍（15×1.3=19.5s，末遍收在片长之前）
        self.assertEqual(_loop_repeat(1.3, 20.0), 15)
        # 片长短于一个周期：仍要演一遍，否则这一页完全静止
        self.assertEqual(_loop_repeat(2.6, 1.0), 1)
        # 没有片长（单独构造 rc 的调用方）：退回一遍，不写无限
        self.assertEqual(_loop_repeat(1.3, 0), 1)
        # 非法周期不炸，退回一遍
        self.assertEqual(_loop_repeat(0, 20.0), 1)

    def test_longer_film_gets_more_cycles(self):
        from html_renderer import _loop_repeat
        self.assertLess(_loop_repeat(1.3, 20.0), _loop_repeat(1.3, 60.0))

    def test_rendered_html_has_no_infinite_loop(self):
        """整份 HTML 里不该再出现 `repeat:-1`：它是那三处坏掉的总开关。"""
        s = dict(H.sample_source(), opening_tagline="AI WEEKLY")
        m = H.make_manifest(s)
        with contextlib.redirect_stderr(io.StringIO()):
            html = generate_html(m, "audio/combined.wav",
                                 images=H.sample_images(m),
                                 aspect="portrait", theme="dark")
        # 只查补间：注释里出现这个写法是解释"为什么不用它"，不算违规
        self.assertNotIn("repeat:-1,", html)


class AppleOpeningBudget(unittest.TestCase):
    """苹果风开场按可见窗口等比归一：短开场装不下时不静默丢尾巴。

    开屏 agenda 卡默认强绑定这套编排（无契约键），这支舞的收尾时刻由**标题
    字数与 agenda 行数**共同决定（标题逐字 stagger 波次与最后一行内容滑入），
    而它能用的时长是开屏页的可见窗口。两者原本不挂钩，于是短开场会静默丢掉
    最后几行/几个字——那是在页面已被下一页盖住之后才落定的，日志一声不吭。
    """

    def _render(self, open_dur, n_rows, gap=0.4):
        m = _apple_manifest(open_dur, n_rows, gap=gap)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            html = generate_html(
                m, "audio/combined.wav", theme="dark", aspect="portrait",
                images={f"seg{i}": {"src": f"images/seg{i}.png"}
                        for i in range(1, n_rows + 1)})
        vis = float(re.search(
            r'<div id="opening" class="clip seg-card agenda-card"[^>]*'
            r'data-duration="([\d.]+)"', html).group(1))
        return html, vis, err.getvalue()

    @staticmethod
    def _k(html):
        """从写出的补间反解压缩系数（ag-name.duration 是模板 duration×k 的直读口）。"""
        dur = float(re.search(
            r'tl\.from\("#opening \.agenda-row \.ag-name",\{[^}]*duration:([\d.]+)',
            html).group(1))
        return round(dur / 0.55, 4)

    @staticmethod
    def _char_stagger(html):
        """标题揭示边的逐字错峰（`.t-in` 那条；旧版是 `.t-ch` 的 opacity 波次）。"""
        return float(re.search(
            r'#opening \.t-in",\{y:[\d.]+,duration:[\d.]+,'
            r'stagger:([\d.]+)', html).group(1))

    def test_enough_window_keeps_original_speed(self):
        """窗口装得下就原速——归一化不能变成"一律压缩"，那会让正常开场变味。"""
        html, vis, err = self._render(3.0, 2)
        self.assertEqual(self._k(html), 1.0)
        self.assertIn("duration:0.50,stagger:0.01", html)
        self.assertIn("ease:\"expo.out\"}", html)
        self.assertIn("stagger:0.12", html)
        self.assertNotIn("可见窗口", err)

    def test_short_window_compresses_every_stage_by_one_factor(self):
        """压缩是**等比**的：标题字符、kicker、行编号、行内容时长乘同一个系数。
        只有这样节奏比例不变——只压尾巴或只压错峰都读得出是"被赶过"。"""
        html, vis, err = self._render(0.8, 7)
        k = self._k(html)
        self.assertLess(k, 1.0)
        # 逐项对账：模板原值 → 写出的值（补间参数保留 2 位小数，比对留 0.01）
        for pat, orig in (
            (r'#opening \.t-in",\{y:[\d.]+,duration:([\d.]+)', 0.50),  # 标题揭示边
            (r'#opening \.agenda-kicker",\{opacity:0,'
             r'filter:"blur\(8px\)",duration:([\d.]+)', 0.8),     # kicker 整行
            (r'#opening \.k-ch",\{x:\(i\)=>i\*[\d.]+,'
             r'duration:([\d.]+)', 0.8),                          # kicker 逐字
            (r'#opening \.agenda-row \.ag-idx",\{opacity:0,scale:1\.25,'
             r'filter:"blur\(6px\)",duration:([\d.]+)', 0.45),    # 行编号
            (r'#opening \.agenda-row \.ag-name",\{opacity:0,x:10,'
             r'duration:([\d.]+)', 0.55),                         # 行内容
        ):
            got = float(re.search(pat, html).group(1))
            self.assertAlmostEqual(got, round(orig * k, 2), delta=0.011, msg=pat)

    def test_compressed_tail_fits_inside_window(self):
        """核心断言：压完之后的**最晚收尾**（标题字符波次 / 最后一行内容滑入）
        落在可见窗口内。这条守住的是"短开场不再静默丢尾巴"这句话本身。"""
        for open_dur, n_rows in ((1.0, 7), (1.2, 4), (1.5, 7), (1.0, 1)):
            with self.subTest(open_dur=open_dur, n_rows=n_rows):
                html, vis, err = self._render(open_dur, n_rows)
                k = self._k(html)
                stag_char = self._char_stagger(html)
                stag_row = float(re.search(
                    r'#opening \.agenda-row \.ag-name",\{[^}]*stagger:([\d.]+)',
                    html).group(1))
                # 标题"开场"= 2 字 → 字符波次收尾 = stagger×1 + duration；行收尾
                # = content.delay(0.3×k) + stagger×(n−1) + content.duration(0.55×k)
                tail = max(stag_char * 1 + 0.55 * k,
                           0.3 * k + stag_row * (n_rows - 1) + 0.55 * k)
                self.assertLessEqual(tail, vis + 0.02,
                                     f"{tail:.2f}s 收尾超出窗口 {vis:.2f}s（k={k}）")

    def test_floor_and_warning_when_even_compressed_it_cannot_fit(self):
        """压到地板还装不下时必须出声：地板 0.5 是"不再往下压"的承诺，
        剩下的缺口靠告警说清楚，并给出三条可操作的方向。"""
        html, vis, err = self._render(0.6, 7, gap=0.1)
        # duration 保留 2 位小数：0.55×0.5=0.275 写出 0.28，反解回来有 0.009 舍入
        self.assertAlmostEqual(self._k(html), 0.5, delta=0.02)
        self.assertIn("可见窗口", err)
        self.assertIn("地板", err)
        for advice in ("opening", "内容段数", "opening_layout"):
            self.assertIn(advice, err)

    def test_more_rows_need_more_room(self):
        """行数多 → 收尾晚 → 同样窗口下压缩得更狠。守住"预算由行数驱动"。"""
        k_one = self._k(self._render(1.0, 1)[0])
        k_seven = self._k(self._render(1.0, 7)[0])
        self.assertLess(k_seven, k_one)
        self.assertEqual(k_one, 1.0)

    def test_breathe_and_glow_drift_cycles_are_not_compressed(self):
        """主光呼吸与副光漂移的**时长**不参与压缩：它们是淡入之后的稳态循环，
        压它们等于让开场一直喘，而且它们不参与"舞演完没有"的判定。压缩只落
        在淡入与其起点。"""
        html, vis, err = self._render(1.0, 7)
        # 主光 breatheDur 1.3 / 副光 breatheDur 2.6 原样写出（周期不参与压缩）。
        # 遍数按片长折算（`_loop_repeat`），所以这里只锁周期，不锁遍数——
        # 遍数由 `test_loop_repeat_tracks_film_span` 单独覆盖。
        self.assertIn('duration:1.30,repeat:', html)
        self.assertIn('duration:2.60,repeat:', html)
        self.assertNotIn("repeat:-1,", html)
        # 呼吸起点跟着淡入一起压缩：起点 = 淡入时长(1.2×k)，不再落在 1.20
        start = float(re.search(r'repeat:\d+,yoyo:true,ease:"sine.inOut"\},([\d.]+)',
                                html).group(1))
        self.assertAlmostEqual(start, round(1.2 * self._k(html), 2), delta=0.011)

    def test_budget_ignores_kicker_when_absent(self):
        """没写 tagline 就没有 kicker 那条补间，收尾时刻也不能把它算进去。
        （否则一条不存在的补间会把预算算贵，害得整支舞被压。）"""
        m = _apple_manifest(2.0, 4)
        for sg in m["segments"]:
            if sg["id"] == "opening":
                sg.pop("tagline")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            html = generate_html(m, "audio/combined.wav", theme="dark",
                                 aspect="portrait",
                                 images={f"seg{i}": {"src": f"images/seg{i}.png"}
                                         for i in range(1, 5)})
        self.assertNotIn("#opening .agenda-kicker", html)
        # 有 kicker 时收尾由 kicker 支配；没它时由行支配，都装得下
        self.assertEqual(self._k(html), 1.0)

    def test_canvas_opening_gets_no_budget_at_all(self):
        """开屏页写 canvas 版式（整页海报）= 无文本编排，不受这套预算影响——
        默认强绑定只覆盖 agenda 版式，画布页连 halo/agenda 的 DOM 都不生成。
        两条路互不干涉。"""
        m = _apple_manifest(1.0, 7)
        for sg in m["segments"]:
            if sg["id"] == "opening":
                sg["layout"] = "canvas"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            html = generate_html(m, "audio/combined.wav", theme="dark",
                                 aspect="portrait",
                                 images={f"seg{i}": {"src": f"images/seg{i}.png"}
                                         for i in range(1, 8)}
                                 | {"opening": {"src": "images/opening.png"}})
        self.assertNotIn('class="apple-halo" id="halo-opening"', html)
        self.assertNotIn('class="apple-glow" id="glow-opening"', html)
        self.assertNotIn("data-opening-anim", html)
        self.assertNotIn("可见窗口", err)


class CanvasPageAndVideoAttrs(unittest.TestCase):
    """两处"能跑但结果是错的"的守卫：告警说假话、属性粘成一团。

    都属于"产物看着对、但作者被误导"的一类：一条内容为假的 warn 会把人引去
    改一段根本不存在的标题，一个缺空格的属性拼接则把 HTML 的合法性押在解析
    器的容错上。
    """

    def _canvas_manifest(self, title):
        s = H.sample_source()
        # seg-b 在样例里就是 layout:"canvas"
        s["segments"][1]["title"] = title
        return H.make_manifest(s)

    def test_canvas_page_does_not_warn_about_a_title_it_never_renders(self):
        """整页画布不生成标题层，横屏降字号的 warn 对它是一条假话。

        标题是作者在 SVG 里自己画的，与 _title_font_px 算出来的 48px 无关。
        照旧打印会让作者去改一段 DOM 里不存在的标题——而他真正该做的是把字
        画进图里。
        """
        long_title = "这是一个非常长的标题用来触发横屏降字号守卫逻辑"
        m = self._canvas_manifest(long_title)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            generate_html(m, "audio/combined.wav",
                          images=H.sample_images(m), aspect="landscape")
        self.assertNotIn("字号降为", err.getvalue())
        # 反向证明 warn 本身还有效：同一个长标题换到槽位段（seg-a）必须报
        s2 = H.sample_source()
        s2["segments"][0]["title"] = long_title
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            generate_html(H.make_manifest(s2), "audio/combined.wav",
                          images=H.sample_images(H.make_manifest(s2)),
                          aspect="landscape")
        self.assertIn("字号降为", err2.getvalue())

    def test_poster_and_audio_attrs_are_space_separated(self):
        """poster 与音轨属性之间必须有空格。

        `poster="…"data-has-audio="true"` 会被解析成一个畸形属性名。主流
        解析器会自行恢复，但拼接的正确性不该靠容错兜底。
        """
        m = self._canvas_manifest("标题")
        images = {"seg-b": {"src": "images/seg-b.mp4", "type": "video",
                            "muted": False, "poster": "images/cover.png"}}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            html = generate_html(m, "audio/combined.wav", images=images,
                                 aspect="portrait")
        self.assertIn('poster="images/cover.png"', html)
        self.assertIn('data-has-audio="true"', html)
        self.assertNotIn('"data-has-audio', html)   # 前一个属性的引号直接贴上来
        # 视频标签整体仍是合法属性串：poster 之后是空白再接 data-*
        tag = [l for l in html.splitlines() if 'id="vid-' in l]
        self.assertEqual(len(tag), 1, "应只有一个 <video> 标签")
        self.assertRegex(tag[0], r'poster="[^"]*"\s+data-')

    def test_canvas_card_does_not_prepare_layers_it_never_renders(self):
        """画布页的标题层/句子流/tagline 连 DOM 都不生成，_prepare_card 就不该白算。

        证伪：三个字段曾无条件计算——title_wrap 里还嵌着 `font-size:;`
        （title_size 的画布守卫值是空串），一串永不发射的坏 CSS；verse 行数
        随段长线性增长，全花在拼一串没人要的字符串上。死代码不该长得像坏
        CSS，白算的开销也不该随段长走。
        """
        from html_renderer import (_build_render_context, _normalize_images,
                                   _prepare_card)
        from _template import load_template
        s = H.sample_source()
        m = H.make_manifest(s)
        images = _normalize_images(H.sample_images(m))
        rc = _build_render_context(load_template(), "vertical", 1080, 1440,
                                   "dark", images)
        by_id = {seg["id"]: seg for seg in m["segments"]}
        clip = {"seg": by_id["seg-b"], "start": 7.2, "duration": 3.0,
                "wipe": 0.0, "win_start": 7.2, "vis": 3.0,
                "gets_peeled": False, "gets_line": False, "peel": None}
        with contextlib.redirect_stderr(io.StringIO()):
            card = _prepare_card(rc, clip)
        self.assertTrue(card.is_canvas)
        self.assertEqual(card.title_wrap, "")
        self.assertEqual(card.verse_html, "")
        self.assertEqual(card.tagline_html, "")
        # 反向证明不是"整个函数对画布页失灵"：同一条链上槽位段照旧算得出这些层
        clip2 = dict(clip, seg=by_id["seg-a"])
        with contextlib.redirect_stderr(io.StringIO()):
            slot = _prepare_card(rc, clip2)
        self.assertFalse(slot.is_canvas)
        self.assertIn("seg-title-wrap", slot.title_wrap)


class LineWipeGateSingleSource(unittest.TestCase):
    """「这一页挂不挂引导线」只能有一个判定处。

    分家就会静默错位：引导线元素在 `_reveal_line_html` 里注入、它的补间在
    `_line_timeline_lines` 里生成。闸门一旦在这两处各算一次而其中一处漏改，
    产物就是"DOM 里有那条线、但没有任何补间驱动它"——线永远停在初始位置
    （页底一条accent 横杠），画面照出、全流程零报错。

    这里的判据与 `_peel_shade_html` 同源：clips 阶段算出的 `gets_line` 是
    唯一真源，三个渲染函数一律读它，不许各自回看模板档位。
    """

    def _src(self):
        import html_renderer
        with open(html_renderer.__file__, encoding="utf-8") as f:
            return f.read()

    def test_gate_is_computed_once_in_clips(self):
        """闸门在 clips 阶段算一次并写进 clip，三处渲染只读不算。"""
        self.assertIn('"gets_line"', self._src())
        # 三个渲染函数里都不许再直接回看模板档位 + wipe
        for fn in ("_reveal_line_html", "_peel_shade_html", "_line_timeline_lines"):
            m = re.search(r"\ndef %s\(.*?\n(?=\ndef |\nclass |\Z)" % fn,
                          self._src(), re.S)
            self.assertIsNotNone(m, "%s 应仍存在" % fn)
            body = m.group(0)
            self.assertNotIn('segmentWipe"]["style"', body,
                             "%s 不该自己回看转场档位（闸门已下沉到 gets_line）" % fn)

    def test_line_element_and_its_tween_always_agree(self):
        """线元素与其补间必须同生共死——分家就是静默错位。"""
        for style in ("line", "vertical", "diagonal", "circle"):
            with self.subTest(style=style):
                html = self._render_with_style(style)
                has_el = 'id="line-' in html
                has_tween = "#line-" in html
                self.assertEqual(has_el, has_tween,
                                 "%s 档：线元素 %s 而补间 %s，两者必须同时存在"
                                 % (style, "有" if has_el else "无",
                                    "有" if has_tween else "无"))

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


if __name__ == "__main__":
    unittest.main()
