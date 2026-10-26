"""四轮奥卡姆 review 的行为锁：这一轮对着**远端新合入的模块**查。

前几轮在 `test_occam_review.py`（一轮/二轮）与 `test_occam_review_3.py`
（check_svg 色名、TTS 解码归类、恒真条件、ATTR_KEY/常量收敛）里。本文件
锁的是远端那次大合入（perceptual / _evolve / gen_hyperframes 媒体探测）
里查出的静默失效。

每条用例都写成"**修复前会红**"的方向：修好后判据必须**变**（该报的报出来、
该崩的别崩、该出声的出声）。否则这条锁就是在给失效行为盖章——那正是本仓库
前两轮反复踩的坑：一个恒不触发的判据，配上一条"断言它不触发"的测试，
两边一起绿了很久。
"""
import ast
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import gen_hyperframes as G          # noqa: E402
import perceptual as P               # noqa: E402
import _evolve as EV                 # noqa: E402
import _images_schema as IMS         # noqa: E402

try:
    from PIL import Image, ImageDraw  # noqa: E402
    HAS_PIL = True
except ImportError:                  # pragma: no cover - 缺 Pillow 时跳过
    HAS_PIL = False


# ── gen_hyperframes：视频探测的 NameError ─────────────────────────────
class VideoProbeDoesNotCrash(unittest.TestCase):
    """`_probe_one` 是顶层函数，取不到嵌套作用域里的名字。

    修复前：`_media_start` 嵌在 `validate_images_files` 里，而调用它的
    `_probe_one` 是**另一个顶层函数**——于是任何一条可解码的视频走到
    `video_span_verdict(..., _media_start(entry))` 都是 NameError。
    症状不是"判据没报"，是整条 `validate_images_files` 崩：视频素材这条路
    从来没被跑通过。
    """

    def _run_probe(self, media_start, seg_dur=8.0):
        tmp = tempfile.mkdtemp(prefix="occam4-vid-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = os.path.join(tmp, "proj")
        os.makedirs(out)
        with open(os.path.join(out, "a.mp4"), "wb") as fh:
            fh.write(b"\x00" * 64)          # 存在即可，解码由 mock 顶掉

        done = mock.Mock(returncode=0,
                         stdout=b"",
                         stderr=b"  Duration: 00:00:01.00, start: 0.000000\n")
        with mock.patch.object(G, "get_ffmpeg", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(G, "ffmpeg_usable", return_value=True), \
             mock.patch("subprocess.run", return_value=done):
            images = {"s1": {"src": "a.mp4", "type": "video",
                             "media_start": media_start}}
            return G.validate_images_files(images, out, {"s1": seg_dur})

    def test_video_entry_does_not_raise(self):
        """正向：一条视频媒体必须走得完（修复前是 NameError）。"""
        missing, corrupt = self._run_probe(0.0)
        self.assertEqual(missing, [])
        self.assertEqual(corrupt, [])

    def test_short_video_is_reported(self):
        """反向：视频比段落短时判据要真的报出来（不是静默通过）。

        段 8s、视频 1s、media_start 0 ⇒ 只剩 1s，不够 ⇒ 该报。
        """
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self._run_probe(0.0)
        self.assertIn("不够段落", err.getvalue())

    def test_media_start_is_module_level(self):
        """起点读取函数必须在模块级（顶层函数能取到的那一层）。"""
        self.assertTrue(hasattr(G, "entry_media_start"),
                        "entry_media_start 必须是模块级函数")
        self.assertEqual(G.entry_media_start({"media_start": 2.5}), 2.5)
        self.assertEqual(G.entry_media_start({}), 0.0)


# ── perceptual：换页时序判据恒不触发 ───────────────────────────────
@unittest.skipUnless(HAS_PIL, "观感门禁要吃像素，缺 Pillow 跑不了")
class SwitchSkewIsAlive(unittest.TestCase):
    """`switch_skew` 判据此前**恒不触发**，等于没有这道门禁。

    原因：候选先用同一条容差筛过一遍（`|段起点 - t| <= 采样间隔 + 容差`），
    再判 `|偏移| > 采样间隔 + 容差`——筛完剩下的条条都落在容差内，判据恒假。
    而"画面提前翻页、旁白还没开始"是肉眼一眼能看出来的毛病。
    """

    W, H = 320, 240
    SLOT = (30, 90, 180, 120)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="occam4-perc-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._n = 0

    def _frame(self, painter):
        self._n += 1
        p = os.path.join(self.tmp, "f%02d.png" % self._n)
        img = Image.new("RGB", (self.W, self.H), (12, 16, 24))
        painter(ImageDraw.Draw(img))
        img.save(p)
        return p

    def _analyze(self, painters, times, segs):
        frames = [{"t": t, "path": self._frame(p)}
                  for t, p in zip(times, painters)]
        old = P.slot_rect
        P.slot_rect = lambda aspect: (self.SLOT, (self.W, self.H))
        try:
            return P.analyze(frames, segs)
        finally:
            P.slot_rect = old

    @staticmethod
    def _before(d):
        d.rectangle((20, 20, 300, 80), fill=(200, 200, 200))

    @staticmethod
    def _after(d):
        d.rectangle((0, 0, 320, 240), fill=(90, 120, 150))   # 整屏换色 = 换页
        d.rectangle((20, 20, 300, 80), fill=(200, 200, 200))

    def test_page_switch_ahead_of_voice_is_flagged(self):
        """画面在 4.3s 翻页、下一段旁白 8.0s 才开始 ⇒ 判"声画不同步"。

        修复前：判据恒假，永远不报。
        """
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 0.0, "duration": 4.0}]},
                {"id": "seg2", "layout": None,
                 "sentences": [{"start_time": 8.0, "duration": 4.0}]}]
        r = self._analyze([self._before, self._after], [3.9, 4.3], segs)
        kinds = {i["kind"] for i in r["issues"]}
        self.assertIn("switch_skew", kinds, r["issues"])

    def test_switch_within_sampling_gap_is_not_flagged(self):
        """反向：换页读数只偏一个采样间隔时**不许**报（那是网格误差）。"""
        segs = [{"id": "seg1", "layout": None,
                 "sentences": [{"start_time": 0.0, "duration": 4.0}]},
                {"id": "seg2", "layout": None,
                 "sentences": [{"start_time": 4.0, "duration": 8.0}]}]
        r = self._analyze([self._before, self._after], [3.9, 4.3], segs)
        kinds = {i["kind"] for i in r["issues"]}
        self.assertNotIn("switch_skew", kinds, r["issues"])


# ── perceptual：脏 manifest 与模板缺失 ───────────────────────────────
class GateSurvivesBadInput(unittest.TestCase):
    """`run_gate` 的契约是"不阻断、不 sys.exit"——它不能往外抛。"""

    def test_dirty_start_time_returns_error(self):
        """start_time 是非数字字符串时返回 error，而不是抛 ValueError。

        修复前：只接 (KeyError, TypeError)，而 `float("abc")` 抛的是
        ValueError——一个脏 manifest 能把整个编排层带崩。
        """
        manifest = {"segments": [{"id": "s1", "sentences": [
            {"start_time": "abc", "duration": 1.0}]}], "sentences": []}
        out = P.run_gate("no-such-project", manifest,
                         tempfile.mkdtemp(prefix="occam4-gate-"))
        self.assertIn("error", out)

    def test_template_failure_is_loud(self):
        """模板读不出时必须**出声**：静默返回 None 会让「画布稀薄」整条消失。"""
        import _template
        with mock.patch.object(_template, "load_template",
                               side_effect=OSError("模板不在")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                box, _canvas = P.slot_rect("portrait")
        self.assertIsNone(box)
        self.assertIn("图槽矩形", err.getvalue(),
                      "读不出模板必须打 warn：否则排查时看到的是「没报」"
                      "而不是「报不了」")


# ── _evolve：起始帧全空守卫从未生效 ─────────────────────────────────
class EvolveHiddenGuard(unittest.TestCase):
    """「起始帧全空」这道守卫此前**结构性不可达**。

    - painter 收全部元素、hidden 只收有 id 的 ⇒ 任何无 id 的装饰件都能让
      `len(hidden) == len(painter)` 不成立；
    - `<defs>` 的子元素（linearGradient 等）进了 painter 却永远没有
      opacity=0，而真实 SVG 几乎都带 `<defs>`；
    - 「没声明 opacity」被 `or 0` 当成隐藏，于是**可见**的页也被判成全空。
    """

    SVG_WITH_DEFS = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<defs><linearGradient id="g"/></defs>'
        '<rect id="a" opacity="0"/><rect id="b" style="opacity:0"/></svg>')
    SVG_VISIBLE = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect id="a"/><rect id="b"/></svg>')

    def test_defs_does_not_silence_the_guard(self):
        """带 <defs> 的真实 SVG 仍要报（修复前：defs 里的渐变让判据永不成立）。"""
        _, warns = EV.check_evolve(self.SVG_WITH_DEFS, [
            {"at_time": 2.5, "target": "#a", "to": {"opacity": 1}}], "seg-a")
        self.assertTrue(any("空框" in w for w in warns), warns)

    def test_at_time_anchor_is_not_treated_as_early(self):
        """`at_time=2.5` 不在段首 ⇒ 不算"段首点亮"（修复前恒算早、守卫沉默）。"""
        _, warns = EV.check_evolve(self.SVG_WITH_DEFS, [
            {"at_time": 2.5, "target": "#a", "to": {"opacity": 1}}], "seg-a")
        self.assertTrue(warns)

    def test_visible_structure_is_not_called_hidden(self):
        """反向：没写 opacity 的元素是**可见**的，不许判成全空。

        修复前 `(_declared_opacity(el) or 0) <= 0` 把"没声明"当成 0。
        """
        _, warns = EV.check_evolve(self.SVG_VISIBLE, [
            {"at": 1.5, "target": "#a", "to": {"fill": "#f59e0b"}}], "seg-a")
        self.assertEqual(warns, [])

    def test_head_beat_still_counts_as_early(self):
        """反向：`at=0`（第 0 句 = 段首）点亮时不该报。"""
        _, warns = EV.check_evolve(self.SVG_WITH_DEFS, [
            {"at": 0, "target": "#a", "set": {"opacity": 1}}], "seg-a")
        self.assertEqual(warns, [])

    def test_camera_keys_come_from_one_place(self):
        """相机键清单只有一份（契约层那份含 rotation，此前本地抄漏了）。"""
        self.assertEqual(set(EV.CAMERA_KEYS), set(IMS._EVOLVE_CAMERA_KEYS))
        self.assertIn("rotation", EV.CAMERA_KEYS)


# ── 单一真源：相机键不许各抄一份 ─────────────────────────────────────
class NoDuplicatedConstants(unittest.TestCase):
    """抄一遍的东西迟早分家——这里用 AST 把"同一份知识"钉在一处。"""

    def test_evolve_does_not_redefine_camera_keys(self):
        path = os.path.join(os.path.dirname(G.__file__), "_evolve.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        redef = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 for t in n.targets
                 if isinstance(t, ast.Name) and t.id == "CAMERA_KEYS"]
        self.assertEqual([], redef,
                         "CAMERA_KEYS 的真源在 _images_schema，_evolve 不另抄")


if __name__ == "__main__":
    unittest.main()
