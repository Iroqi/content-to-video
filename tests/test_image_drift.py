"""配图持续运镜（Ken Burns）的行为锁。

这一层运镜是**唯一**管"整段画面还在不在动"的补间，所以它值得单独一份测试，而不
是夹进 golden 快照里——快照只告诉你"变了"，这份文件告诉你"它没有在没人注意的时
候越过 cover 语义那条线"。

三条约束都是从 cover 语义直接推出来的（理由见 `html_renderer._image_drift_lines`），
所以**每条都按第一性原理验，不验实现细节**：

1. 只推近不拉远 —— scale < 1 就是槽位边缘露页背景（不是"图小了"，是露底）；
2. 位移不超出推近让出来的盈余 —— 同上，而且这条最容易在"换个画幅 / 改个槽位
   尺寸 / 调个速率"时被静默越线，所以两个画幅都按各自的槽位盒各量一遍；
3. 匀速 —— 这一层不该有脾气，任何 ease 都会把它读成有意图的镜头调度。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template
from html_renderer import generate_html

# 生成的漂移补间（按呼吸节奏分段，每段都是独立的 fromTo）：
#   tl.fromTo("#img-seg-a > img",{scale:…,x:…,y:…},{scale:…,x:…,y:…,
#             duration:…,ease:"none"},…)
# 首段的起点恒为 {scale:1,x:0,y:0}，后续段的起点是上一段的终值——所以正则的
# 起点部分要照收三个数，不能再写死 scale:1。断言"整段推到多少"用 last_drift。
# 用命名组：起点/终点各三个数，按位置数组组号极易在增删字段时错位。
#
# **末段的钉住补间不算运镜**：`_image_drift_lines` 在最后追加一条 0.01s 的
# {1,0,0}→{1,0,0}，作用是把 GSAP 建时间线时预渲染的终点值收回来（理由见该函数
# 的注释）。它形状与运镜补间一致，所以按 `dur` 过滤掉——否则"整段推到多少"会读到
# 一条恒等补间，所有断言都会看着像退化了。
_PIN_MAX_DUR = 0.02
_DRIFT_RE = re.compile(
    r'tl\.fromTo\("#img-(?P<sid>[A-Za-z0-9_-]+) > img",'
    r'\{scale:-?[\d.]+,x:-?[\d.]+,y:-?[\d.]+\},'
    r'\{scale:(?P<scale>-?[\d.]+),x:(?P<x>-?[\d.]+),y:(?P<y>-?[\d.]+),'
    r'duration:(?P<dur>[\d.]+),ease:"(?P<ease>[a-z.]+)"\},(?P<at>[\d.]+)\)')


def drift_hits(html):
    """html 里所有**真运镜**补间（滤掉末段的钉住补间）。"""
    return [h for h in _DRIFT_RE.finditer(html) if float(h.group('dur')) > _PIN_MAX_DUR]
# 配图入场补间：tl.from("#img-seg-a",{opacity:0,y:40,duration:0.80,…},2.60)
_ENTRANCE_RE = re.compile(
    r'tl\.from\("#img-([A-Za-z0-9_-]+)",\{opacity:0,y:(-?[\d.]+),'
    r'duration:([\d.]+),ease:"[^"]+"\},([\d.]+)\)')
_VIDEO_TAG = re.compile(r'<video id="vid-([A-Za-z0-9_-]+)"')


def render(images=None, aspect="portrait", dur=2.0, gap=0.4, source=None):
    """渲染样例稿件，返回 (manifest, html)。不关心的 stderr 一律重定向掉。"""
    m = H.make_manifest(source=source or H.sample_source(), dur=dur, gap=gap)
    with contextlib.redirect_stderr(io.StringIO()):
        return m, generate_html(m, "audio/combined.wav",
                                images=images or H.sample_images(m),
                                aspect=aspect, theme="dark")


def seg_span(manifest, sid):
    """段落的口播 [起点, 段长]——补间能用的全部时间，漂移不得越过它。"""
    seg = next(x for x in manifest["segments"] if x["id"] == sid)
    ss = seg["sentences"]
    s = min(x["start_time"] for x in ss)
    return s, max(x["start_time"] + x["duration"] for x in ss) - s


def _replace_seg_a(**opts):
    """把 seg-a 换成指定媒体；seg-b 是整页画布（canvas 段缺图会 raise），必须留着。"""
    return {"seg-a": opts, "seg-b": {"src": "images/seg-b.svg"}}


def last_drift(hits, sid):
    """该段**最后一段**运镜补间（= 整段的终值）。

    运镜是按呼吸节奏分段的（推 / 缓 / 推），每段都是独立的 fromTo。断言"这一段
    最终推到多少、位移多大"时该看最后那段；首段的起点恒为 (1, 0, 0)。
    hits 可以是 html 字符串，也可以是已经匹配好的 Match 列表。
    """
    if isinstance(hits, str):
        hits = drift_hits(hits)
    own = [h for h in hits if h.group('sid') == sid]
    return own[-1] if own else None


class Carriers(unittest.TestCase):
    """哪些载体该漂先把……不，先把"哪些不该漂"钉死——它们的运动各有主人。"""

    def test_static_images_drift(self):
        for src in ("images/seg-a.svg", "images/seg-a.png", "images/seg-a.gif"):
            with self.subTest(src=src):
                html = render(images=_replace_seg_a(src=src))[1]
                self.assertTrue(_DRIFT_RE.search(html),
                                f"{src} 属于静态图档，它是这一层的全部动机")

    def test_video_does_not_drift(self):
        html = render(images=_replace_seg_a(src="images/seg-a.mp4"))[1]
        self.assertTrue(_VIDEO_TAG.search(html), "这条用例没真的出 <video>")
        self.assertFalse(_DRIFT_RE.search(html),
                         "video 每秒 24 帧自己在放，再叠一层运镜是运动冗余")

    def test_directed_inline_svg_does_not_drift(self):
        # images.json 写了 director 时 gen_hyperframes 回填 inline_svg，那份 SVG
        # 的画面变化由作者手写的 steps 负责，不该由整幅运镜再叠一层意图。
        html = render(images=_replace_seg_a(
            src="images/seg-a.svg", inline_svg="<svg></svg>"))[1]
        self.assertFalse(_DRIFT_RE.search(html))

    def test_canvas_page_never_drifts(self):
        # 整页画布 props 铺整个画幅，它自己就是画面；它的运动归导演档。
        m, html = render()
        sids = {hit.group('sid') for hit in drift_hits(html)}
        self.assertIn("seg-a", sids, "这条用例需要一个照常漂的内容段做对照")
        self.assertNotIn("seg-b", sids, "整页画布段不该漂")

    def test_agenda_cards_never_drift(self):
        # 开屏/结尾是纯文字投影卡，压根没有 .seg-image。
        html = render()[1]
        sids = {hit.group('sid') for hit in drift_hits(html)}
        self.assertNotIn("opening", sids)
        self.assertNotIn("closing", sids)


class CoverContract(unittest.TestCase):
    """cover 语义那条线：只推近，位移不越盈余，且不允许有任何脾气。"""

    def test_scale_never_drops_below_one(self):
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                hits = list(drift_hits(render(aspect=aspect)[1]))
                self.assertTrue(hits, "这个画幅一条漂移都没生成，这条测试会自证")
                for hit in hits:
                    self.assertGreaterEqual(
                        float(hit.group('scale')), 1.0,
                        f"{hit.group('sid')} 的 scale 小于 1：object-fit:cover 下任何"
                        "缩小都会让槽位边缘露出页背景（不是图小了，是露底）")

    def test_pan_stays_inside_the_slack_that_zoom_created(self):
        """位移不得超过推近让出来的盈余——两个画幅按各自的槽位盒量一遍。"""
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                bw, bh = _template.get_image_box(aspect)
                hits = list(drift_hits(render(aspect=aspect)[1]))
                self.assertTrue(hits, "这个画幅一条漂移都没生成，这条测试会自证")
                for hit in hits:
                    scale = float(hit.group('scale'))
                    # 放大 s 倍后单侧各多出 (s−1)/2 × 槽位边长：GSAP 默认以元素
                    # 中心为原点，两侧对称。
                    slack_x = (scale - 1.0) * bw / 2.0
                    slack_y = (scale - 1.0) * bh / 2.0
                    self.assertLessEqual(abs(float(hit.group('x'))), slack_x + 1e-9,
                                         f"{hit.group('sid')} 的 x 越过盈余就是露底")
                    self.assertLessEqual(abs(float(hit.group('y'))), slack_y + 1e-9,
                                         f"{hit.group('sid')} 的 y 越过盈余就是露底")

    def test_ease_is_linear(self):
        """这一层不该有脾气：它一加速，就变成有意图的镜头调度了。"""
        hits = list(drift_hits(render()[1]))
        self.assertTrue(hits)
        eases = {hit.group('ease') for hit in hits}
        self.assertEqual({"none"}, eases)


class Timing(unittest.TestCase):
    def test_drift_starts_after_the_entrance_has_landed(self):
        """起点必须是入场补间收尾的时刻——不要"一边滑进来一边放大"。"""
        html = render()[1]
        ent = {hit.group(1): (float(hit.group(4)), float(hit.group(3)))
               for hit in _ENTRANCE_RE.finditer(html)}
        hits = list(drift_hits(html))
        self.assertTrue(hits)
        for hit in hits:
            sid = hit.group('sid')
            self.assertIn(sid, ent, f"{sid} 漂了却没有入场补间？")
            ent_start, ent_dur = ent[sid]
            self.assertGreaterEqual(float(hit.group('at')), ent_start + ent_dur - 1e-9,
                                    f"{sid} 的漂移起点早于入场收尾")

    def test_drift_ends_with_the_narration_not_beyond_it(self):
        """跨度不得超过本段口播——越过就是演到下一页的地盘（那边马上被擦走）。"""
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                m, html = render(aspect=aspect)
                for hit in drift_hits(html):
                    sid = hit.group('sid')
                    s, d = seg_span(m, sid)
                    self.assertLessEqual(float(hit.group('at')) + float(hit.group('dur')),
                                         s + d + 1e-9,
                                         f"{sid} 的漂移越过了本段口播收尾")

    def test_short_segment_does_not_drift(self):
        """短段漂不动——还没展开就被切走，观众只读到一次抖动。"""
        html = render(dur=0.6, gap=0.2)[1]
        self.assertFalse(_DRIFT_RE.search(html),
                         "可用时间短于 minSeconds 时该整条不生成")

    def test_long_segment_stops_at_the_scale_ceiling(self):
        """封顶必须真的生效——不封的话长段会把图推到看不清。"""
        hits = list(drift_hits(render(dur=10.0, gap=0.4)[1]))
        self.assertTrue(hits)
        for hit in hits:
            self.assertLessEqual(float(hit.group('scale')), 1.08 + 1e-9,
                                 "长段落的终态必须停在 maxScale 上")


class Aim(unittest.TestCase):
    """取景方向：必须逐段不同（否则就是能被看穿的模板），且必须可复现。"""

    def _six_content_segments(self):
        """六段都有图的稿件——样例只有一段内容段 + 一段画布，量不出轮换。"""
        src = H.sample_source()
        base = src["segments"][0]
        segs = [dict(base)]
        for i in range(1, 6):
            seg = dict(base)
            seg["id"] = "seg-" + chr(ord("a") + i)
            seg["title"] = f"第{i + 1}件事"
            segs.append(seg)
        # 整页画布那段一并带上做对照（它不参与轮换）——但样例里它的 id 正是
        # seg-b，原样追加会与上面生成的内容段同名，两张卡抢同一个 #img-seg-b。
        canvas_seg = dict(src["segments"][1])
        canvas_seg["id"] = "seg-z"
        segs.append(canvas_seg)
        src["segments"] = segs
        return src

    def test_direction_rotates_between_segments(self):
        """所有段都往同一侧漂 = 一种能被读出来的模式，这里把它钉住。"""
        src = self._six_content_segments()
        m = H.make_manifest(source=src)
        imgs = {sid: {"src": f"images/{sid}.svg"} for sid in H.sample_images(m)}
        hits = {h.group('sid'): (float(h.group(3)), float(h.group(4)))
                for h in drift_hits(render(source=src, images=imgs)[1])}
        self.assertGreaterEqual(len(hits), 6, "这条测试需要六段能漂的静态图")
        signs = {1 if y >= 0 else -1 for _, y in hits.values()}
        self.assertEqual({-1, 1}, signs,
                         f"六段全漂向同一侧 {hits}——连看几段观众会读到"
                         "「它是个模板」而不是每段各有取景")
        self.assertNotIn("seg-z", hits, "整页画布段不参与轮换")

    def test_direction_is_reproducible(self):
        """方向按段落 id 派生（不是 random）：同一稿件重跑必须逐字节一致。"""
        self.assertEqual(render()[1], render()[1])


class PerSegmentOverride(unittest.TestCase):
    """images.json 的 drift 字段：逐段覆盖模板（逐项替换，不是整块替换）。"""

    def test_false_turns_it_off_for_that_segment_only(self):
        images = _replace_seg_a(src="images/seg-a.png", drift=False)
        html = render(images=images)[1]
        self.assertFalse(_DRIFT_RE.search(html),
                         "drift:false 这一段就不该再生成运镜补间")
        # 别的段照旧（seg-b 是整页画布，本来就不漂；这里只确认关掉没波及生成）
        self.assertIn("seg-a", html)

    def test_partial_override_keeps_the_other_numbers(self):
        """只写 ratePerSec 时不该顺带把 pan 归零——那等于用"改快一点"换掉了取景。"""
        base = list(drift_hits(render(dur=10.0)[1]))
        fast = list(drift_hits(render(
            dur=10.0, images=_replace_seg_a(src="images/seg-a.png",
                                            drift={"ratePerSec": 0.05}))[1]))
        self.assertTrue(base and fast)
        b = last_drift(base, "seg-a")
        f = last_drift(fast, "seg-a")
        # 推得更快 → 更早顶到封顶，但 x/y 仍非零（pan 没被清掉）
        self.assertAlmostEqual(float(f.group('scale')), 1.08, delta=1e-6)
        self.assertTrue(float(b.group('x')) != 0.0 or float(b.group('y')) != 0.0)
        self.assertTrue(float(f.group('x')) != 0.0 or float(f.group('y')) != 0.0)

    def test_pan_override_respects_the_cover_slack(self):
        """覆盖值同样不得把图拖出槽位：位移仍以推近盈余为上限。"""
        images = _replace_seg_a(src="images/seg-a.png",
                                drift={"panX": 1.0, "panY": 1.0})
        for aspect in ("portrait", "landscape"):
            m, html = render(images=images, aspect=aspect, dur=10.0)
            bw, bh = _template.get_image_box(aspect)
            hit = last_drift(html, "seg-a")
            scale = float(hit.group('scale'))
            self.assertLessEqual(abs(float(hit.group('x'))), (scale - 1) * bw / 2 + 1e-6)
            self.assertLessEqual(abs(float(hit.group('y'))), (scale - 1) * bh / 2 + 1e-6)

    def test_drift_field_survives_the_contract(self):
        """覆盖值要能通过 images.json 的契约（否则渲染端根本收不到）。"""
        import _images_schema as S
        S.validate_images_json({"seg-a": {"src": "images/seg-a.png",
                                          "drift": {"maxScale": 1.05}}})
        S.validate_images_json({"seg-a": {"src": "images/seg-a.png", "drift": False}})
        with self.assertRaises(ValueError):
            S.validate_images_json({"seg-a": {"src": "images/seg-a.png",
                                              "drift": {"panY": 1.5}}})


class SeekSafety(unittest.TestCase):
    def test_fromTo_keeps_random_seek_idempotent(self):
        """逐帧 seek 是乱序的：fromTo 给绝对值，乱序回放结果一致。

        `to()` 的起点是"读取那一刻的当前值"，顺序一乱就把它拿成一个半途姿态。
        """
        html = render()[1]
        hits = list(drift_hits(html))
        self.assertTrue(hits)
        for hit in hits:
            self.assertIn("{scale:1,x:0,y:0}", hit.group(0))
        self.assertNotIn('tl.to("#img-seg-a > img"', html)

    def test_render_is_deterministic(self):
        self.assertEqual(render()[1], render()[1])

    def test_image_container_declares_its_overflow_on_purpose(self):
        """配图容器必须自带 overflow 豁免，两个画幅都要。

        运镜与入场收拢全程以 `scale≥1` 铺满槽位，子元素 bbox 每帧都比容器大几
        像素——`hyperframes check` 的 Layout 阶段会按"元素超出裁切容器"每段报一
        条 `container_overflow`。这是设计，所以容器上挂 `data-layout-allow-overflow`
        （理由见 `_render_image`）。丢了这个标记不会报错，只会让门禁多出一堆假
        告警、把真的（SVG 比例失配）埋掉。
        """
        for aspect in ("portrait", "landscape"):
            html = render(aspect=aspect)[1]
            for sid in ("seg-a", "seg-b"):
                tag = re.search(r'<div class="seg-image[^"]*" id="img-%s"([^>]*)>' % sid, html)
                self.assertIsNotNone(tag, f"{aspect}：找不到 {sid} 的配图容器")
                self.assertIn("data-layout-allow-overflow", tag.group(1),
                              f"{aspect}：{sid} 的配图容器丢了 overflow 豁免")


if __name__ == "__main__":
    unittest.main()
