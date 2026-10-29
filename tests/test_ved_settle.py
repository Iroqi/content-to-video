"""词级强调「念完之后的收束」（verse settle）的行为锁。

动机：一句念完的那一刻，整句仍是全亮 accent，而它与下一句刚亮起的第一个字**同色
同重**——画面上同时有两个"现在的焦点"。前沿的歌词/字幕（Apple Music、Spotify、
卡拉OK bar 收尾）都在句尾做一次回落，把焦点让给下一句。

这份文件只验**口径**，不验像素（像素靠 exp 里的逐帧截图看）：

1. **时长只从留白里来** —— 留白 = 下一句开口 − 本句念完 − 换句接力。留白为 0
   或负 → 不演（写 0，CSS 规则整条失效，成片与改动前逐字节一致）；
2. **接力时长只有一个真源** —— 模板 `layout.<画幅>.subtitle.relay`，生成期算留白
   时传入、运行期从 `--ctv-relay-s` 读回；runtime.js 里不许再出现数字字面量；
3. **上下限** —— 封顶 `SETTLE_MAX_SECONDS`（留白富余时不把收束拉成慢动作）、
   地板 `SETTLE_MIN_SECONDS`（不足两帧的回落只是闪一下）；
4. **向下取整** —— 窗口不够时宁可少演，也不能把最后几个字挤进下一句已开口的那帧；
5. **页末句不演** —— 它后面跟的是转场，整句 accent 会随页面被擦走。
"""
import pathlib
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
import _template as T
from html_renderer import (
    SETTLE_MAX_SECONDS,
    SETTLE_MIN_SECONDS,
    _ved_settle_seconds,
)

RELAY_SECONDS = T.load_template()["layout"]["vertical"]["subtitle"]["relay"]

REPO = H.SKILL_DIR
SKILL = pathlib.Path(REPO)


def _sent(idx, start, dur, onset=None, voiced=None, text="一二三四五六七八九十"):
    s = {"index": idx, "start_time": start, "duration": dur, "text": text}
    if onset is not None:
        s["onset"] = onset
    if voiced is not None:
        s["voiced"] = voiced
    return s


class SettleSeconds(unittest.TestCase):
    def test_no_voiced_is_never_settled(self):
        """没有 voiced（老 cue / 手写 manifest）：运行时退回 t1，念完 = cue 终点。

        这时留白恒为负（还要扣掉接力），必须不演——否则线上一堆老 manifest 会
        突然多出一个白送的动画。
        """
        sents = [_sent(0, 0.0, 2.0), _sent(1, 2.0, 2.0)]
        self.assertEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.0)

    def test_butted_sentences_never_settle(self):
        """首尾相接（start 差 = duration）且 voiced 撑满整块：留白为 0 → 不演。"""
        sents = [_sent(0, 0.0, 2.0, 0.0, 2.0), _sent(1, 2.0, 2.0)]
        self.assertEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.0)

    def test_gap_maps_to_duration(self):
        """留白 0.35s（扣掉 0.32 接力剩 0.03）、留白 1.05s（扣完 0.73，封顶 0.6）。"""
        # 第 0 句 0.15 起念、念 1.40 → 1.55 念完；下一句 1.90 开口 → 留白 0.35
        sents = [_sent(0, 0.0, 2.0, 0.15, 1.40), _sent(1, 1.90, 2.0)]
        # 0.35 − 0.32 = 0.03 < 地板 0.08 → 退化为不演
        self.assertEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.0)
        # 留白拉到 0.60：0.60 − 0.32 = 0.28 → 0.25（向下取整到 0.05 网格）
        sents = [_sent(0, 0.0, 2.0, 0.15, 1.40), _sent(1, 2.15, 2.0)]
        self.assertAlmostEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.25, places=6)

    def test_capped(self):
        """留白大幅富余时封顶：不把收束拉成慢动作。"""
        sents = [_sent(0, 0.0, 5.0, 0.0, 1.0), _sent(1, 5.0, 2.0)]
        self.assertAlmostEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), SETTLE_MAX_SECONDS, places=6)

    def test_floor(self):
        """落到地板附近仍要演；落不到就严格不演（一次闪烁比没有更糟）。"""
        # 留白 = 0.15；0.15 − 0.32 < 0 → 0
        sents = [_sent(0, 0.0, 2.0, 0.0, 1.85), _sent(1, 2.0, 2.0)]
        self.assertEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.0)
        # 留白 = 0.49 → 0.49 − 0.32 = 0.17 → 向下取整到 0.05 网格 = 0.15，演
        sents = [_sent(0, 0.0, 2.0, 0.0, 1.51), _sent(1, 2.0, 2.0)]
        self.assertAlmostEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.15, places=6)
        # 留白 = 0.39 → 0.07 → 网格 0.05 < 地板 0.08 → 不演
        sents = [_sent(0, 0.0, 2.0, 0.0, 1.61), _sent(1, 2.0, 2.0)]
        self.assertEqual(_ved_settle_seconds(sents, {1}, RELAY_SECONDS), 0.0)

    def test_floor_rounding_down(self):
        """窗口 0.28 必须给 0.25，不能给 0.30：多出来的 20ms 会落在下一句里。"""
        sents = [_sent(0, 0.0, 2.0, 0.15, 1.40), _sent(1, 2.15, 2.0)]
        got = _ved_settle_seconds(sents, {1}, RELAY_SECONDS)
        self.assertLessEqual(got, 0.28)
        self.assertAlmostEqual(got, 0.25, places=6)

    def test_page_last_never_settles(self):
        """页末句后面是转场，整句 accent 会被擦走 → 不演。"""
        sents = [_sent(0, 0.0, 5.0, 0.0, 1.0), _sent(1, 5.0, 2.0)]
        self.assertEqual(_ved_settle_seconds(sents, {0, 1}, RELAY_SECONDS), 0.0)

    def test_min_over_all_interior_sentences(self):
        """一页里多句时取**最小**的那段留白：任何一句收不完都会和下一句撞车。"""
        # 第 0 句留白很大（封顶 0.6），第 1 句只有 0.28 → 全页按 0.25
        sents = [
            _sent(0, 0.0, 3.0, 0.0, 1.0),
            _sent(1, 3.0, 2.0, 0.0, 1.20),
            _sent(2, 4.80, 2.0),
        ]
        self.assertAlmostEqual(_ved_settle_seconds(sents, {2}, RELAY_SECONDS), 0.25, places=6)


class RuntimeContract(unittest.TestCase):
    """生成期与运行期的两份抄写必须相等。"""

    def test_runtime_reads_relay_from_the_template(self):
        """运行期不许再抄一份数字：接力时长从 --ctv-relay-s 读，与生成期同一真源。"""
        src = (SKILL / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn('getPropertyValue("--ctv-relay-s")', src)
        self.assertIsNone(re.search(r"^const RELAY_SECONDS = [\d.]+;", src, re.MULTILINE),
                          "runtime.js 里又出现了写死的 RELAY_SECONDS")

    def test_template_carries_relay_for_both_aspects(self):
        tpl = T.load_template()
        for asp in ("vertical", "landscape"):
            self.assertGreater(float(tpl["layout"][asp]["subtitle"]["relay"]), 0)

    def test_settle_var_is_read_from_root(self):
        """运行期只读一个 CSS 变量、不做第二次判断（"要不要演"只在生成期决定）。"""
        src = (SKILL / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn('getPropertyValue("--ctv-ved-settle")', src)
        self.assertIn("const verseEmSettle", src)
        self.assertIn("verse-ved-settled", src)

    def test_settle_check_precedes_the_cross_gate(self):
        """收束判据必须写在"跨格才刷"那道门（`k !== L.lastK`）**之前**。

        k 是取整后的字序号，最后不足一个字的行程里 k 已经停在 n 不动；门一关，这条
        判据就被整个跳过，收束永远不触发且没有任何报错（踩过）。
        """
        src = (SKILL / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
        body = src.split("const verseEmUpdate = ", 1)[1].split("\n};", 1)[0]
        self.assertIn("verseEmSettle(L)", body)
        self.assertLess(body.index("verseEmSettle(L)"), body.index("L.lastK = k"))

    def test_verse_sync_does_not_trust_wall_clock(self):
        """`verseSync` 不许拿 `gsap.globalTimeline.time()` 当时钟锚。

        那是**墙钟**：页面打开后一直在涨。只要它碰巧 ≥ 当前 seek 到的 t 就会被当成
        帧时刻——短时间线的中后段（墙钟已超过片长）字幕完全按墙钟跑，seek 到 1.00
        却按整句念完画（实测基线版本 inline 全是 "0"，卡拉OK在最需要它的时候整段
        失效）。成片里渲染器 seek 的时刻远大于墙钟所以不显形，只在预览/短片里翻车，
        极难归因。

        用"源码里不许出现"来锁：一旦有人为了修某个次序问题把它加回来，症状会以
        "卡拉OK偶发失效"的形式复现，而不是以测试失败的形式。
        """
        src = (SKILL / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
        body = src.split("const verseSync = ", 1)[1].split("\n};", 1)[0]
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.strip().startswith("//"))
        self.assertNotIn("globalTimeline", code)

    def test_settle_fires_exactly_at_voiced_end(self):
        """触发时刻是 `t0 + voiced`（念完那一刻），不是 cue 终点 `t1`。

        `t1` 是"下一句开口"，两者之间的留白正是收束要占的那段。早于 `t0+voiced`
        触发会把逐字回落盖在还没念到的字上。
        """
        src = (SKILL / "scripts" / "_assets" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn(
            "const verseEmEnd = (L) => (L.voiced > 0 ? Math.min(L.t1, L.t0 + L.voiced) : L.t1);",
            src)


class RenderContract(unittest.TestCase):
    """生成期那个数必须真的落到页面 :root 上（否则运行时读到 0、整条静默失效）。"""

    def _html(self, sents):
        from html_renderer import generate_html
        manifest = {
            "schema_version": 2, "status": "ok", "degraded": {},
            "sentences": sents,
            "total_duration": 6.0, "gap": 0.3, "voice_id": "冰糖",
            "segments": [{"id": "seg-a", "title": "标题", "tagline": "",
                          "accent": "#4fc3f7", "sentences": sents}],
        }
        return generate_html(manifest, "audio/combined.wav", images={},
                             aspect="portrait", theme="dark",
                             gsap_src="gsap.min.js")

    def test_variable_written_when_settle_applies(self):
        sents = [_sent(0, 0.0, 2.0, 0.15, 1.40), _sent(1, 2.15, 2.0)]
        html = self._html(sents)
        self.assertIn("--ctv-ved-settle:0.25s;", html)

    def test_variable_stays_zero_when_no_gap(self):
        """首尾相接的稿件：变量必须停在 0（= 不演），而不是被漏写成非 0。"""
        sents = [_sent(0, 0.0, 2.0), _sent(1, 2.0, 2.0)]
        html = self._html(sents)
        self.assertIn("--ctv-ved-settle:0.00s;", html)
        self.assertNotIn("--ctv-ved-settle:0.25s;", html)


if __name__ == "__main__":
    unittest.main()
