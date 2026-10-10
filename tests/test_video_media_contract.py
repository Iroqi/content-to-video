"""视频音轨契约：data-has-audio 与增益/裁剪四件套的 schema + 渲染回归。

这一组守的是同一件事：**视频原声是唯一一条不经过第 3 步 TTS、却照样混进
成片的声音**。Hyperframes 的 ffmpeg 混音只读 `<audio>` 元素和声明了
`data-has-audio="true"` 的 `<video>`——不写就一条都不混。所以 images.json
里关掉 muted（用户明确要原声）而标签上没那句声明，成片就是"有画面、没
原声、零报错"。这类静默退化必须在契约层和渲染端两头堵住，不能留到成片里。
"""
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
from _images_schema import validate_images_json, MEDIA_VOLUME_MAX
import html_renderer as HR


def _video_html(opts, aspect="portrait"):
    """给 seg-b 挂一条视频、返回该 <video> 标签那一段（seg-a 给张图占位）。"""
    m = H.make_manifest()
    html = HR.generate_html(m, "audio/combined.wav", aspect=aspect, images={
        "seg-a": {"src": "images/seg-a.png"},
        "seg-b": dict({"src": "images/seg-b.mp4"}, **opts)})
    return html.split('<video id="vid-seg-b"')[1].split("</video>")[0]


def _attr_of(html, sid, name):
    """从生成结果里取某个元素的 data-start / data-duration。"""
    frag = html.split('<div id="%s"' % sid)[1]
    if name == "start":
        return frag.split("data-start=")[1].split()[0].strip('"')
    if name == "duration":
        return frag.split("data-duration=")[1].split()[0].strip('"')
    raise AssertionError(name)


class VideoVisibilityWindow(unittest.TestCase):
    """<video> 的可见窗口必须逐字等于它所在那张卡片的窗口。

    引擎给**每个**带 data-start 的元素按它自己的窗口独立判显隐
    （`applyTimedElementVisibility` + 逐帧 style.visibility），所以这一点与卡片
    不等不是"偏一点"，而是头尾各有一段：卡片还在画面上、媒体已被判出窗，画面上
    只剩一个空图槽。`<img>` 不带 data-start、继承祖先卡，两种素材必须同一口径。
    """

    def test_video_window_matches_its_card(self):
        for aspect in ("portrait", "landscape"):
            with self.subTest(aspect=aspect):
                m = H.make_manifest()
                html = HR.generate_html(m, "audio/combined.wav", aspect=aspect, images={
                    "seg-a": {"src": "images/seg-a.png"},
                    "seg-b": {"src": "images/seg-b.mp4"}})
                card_start = _attr_of(html, "seg-b", "start")
                card_dur = _attr_of(html, "seg-b", "duration")
                video = _video_html({}, aspect=aspect)
                self.assertIn(f'data-start="{card_start}"', video)
                self.assertIn(f'data-duration="{card_dur}"', video)

    def test_video_window_starts_with_the_wipe_not_the_first_sentence(self):
        """video 要跟着卡片一起从擦除起点开始（= 首句 − wipe），不是从首句开始。

        证伪：video 原先锚的是口播段长 [s, s+d]，比卡片窗口短一整个入场，那一页
        正在被揭开的那段屏幕上没有素材——画面先揭开一个空槽。
        """
        m = H.make_manifest()
        html = HR.generate_html(m, "audio/combined.wav", aspect="portrait", images={
            "seg-a": {"src": "images/seg-a.mp4"},
            "seg-b": {"src": "images/seg-b.png"}})
        video = html.split('<video id="vid-seg-a"')[1].split("</video>")[0]
        seg_a = next(s for s in m["segments"] if s["id"] == "seg-a")
        card_start = float(_attr_of(html, "seg-a", "start"))
        first_sentence = round(float(seg_a["sentences"][0]["start_time"]), 2)
        self.assertLess(card_start, first_sentence,
                        "卡片窗口应从擦除起点开始（比首句更早）")
        self.assertIn(f'data-start="{card_start:.2f}"', video)


class VideoAudioDeclaration(unittest.TestCase):
    """data-has-audio：要了原声就必须写，静音时又绝不能写。"""

    def test_muted_off_declares_has_audio(self):
        """关掉 muted 就是"我要原声"——必须有 data-has-audio="true"。

        缺了它 ffmpeg 混音根本不读这条轨：成片有画面没原声，而生成期、
        `hyperframes check`、渲染日志全都一声不吭。"""
        self.assertIn('data-has-audio="true"', _video_html({"muted": False}))

    def test_muted_default_never_declares_has_audio(self):
        """默认静音时**不能**写 data-has-audio：契约是二选一（静音 or 带音轨），
        两个都挂会让 lint 的 video_missing_muted 判据失效。"""
        tag = _video_html({})
        self.assertIn("muted", tag)
        self.assertNotIn("data-has-audio", tag)

    def test_muted_on_never_declares_has_audio(self):
        """显式 muted:true 同上，走的是同一条分支。"""
        tag = _video_html({"muted": True})
        self.assertIn("muted", tag)
        self.assertNotIn("data-has-audio", tag)

    def test_has_audio_warns_about_narration_overlap(self):
        """要了原声就要被告知它会和口播抢频谱：口播是 <audio id="main-audio">，
        两条轨都进混音器。跑完不报错、成片没法听——这条 warn 是唯一的知情口。"""
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            _video_html({"muted": False})
        self.assertIn("口播", buf.getvalue())
        self.assertIn("seg-b", buf.getvalue())

    def test_muted_video_stays_silent_in_logs(self):
        """静音视频不该刷那条叠加告警——它根本没有音轨，报警是纯噪音。"""
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            _video_html({})
        self.assertNotIn("口播", buf.getvalue())


class VideoGainAndTrimAttrs(unittest.TestCase):
    """volume / fade_in / fade_out / media_start → 对应的 data-* 属性。"""

    def test_volume_renders_data_volume(self):
        self.assertIn('data-volume="0.25"', _video_html({"volume": 0.25}))

    def test_fades_render_data_fade_attrs(self):
        tag = _video_html({"fade_in": 0.5, "fade_out": 1.5})
        self.assertIn('data-fade-in="0.5"', tag)
        self.assertIn('data-fade-out="1.5"', tag)

    def test_media_start_renders_data_media_start(self):
        """media_start 是 trim 起点：长视频不用被"只显示前段"截断。

        属性名只能是 data-media-start——官方契约里有两组读取者，音频混音器
        （喂 ffmpeg -ss 的那个）**只读** data-media-start；写成
        data-playback-start 会渲染出"裁过的画面配没裁过的声音"。
        """
        tag = _video_html({"media_start": 3})
        self.assertIn('data-media-start="3"', tag)

    def test_number_formatting_has_no_float_noise(self):
        """整数不带小数点、有限小数不带二进制余尾——同一份稿件两次生成必须
        字节一致，否则 diff 里全是假改动。"""
        self.assertIn('data-volume="2"', _video_html({"volume": 2}))
        self.assertIn('data-volume="0.1"', _video_html({"volume": 0.1}))

    def test_gain_opts_absent_when_not_given(self):
        """没写就不该凭空出现在标签上：默认值交给引擎，别在这里另立一份。"""
        tag = _video_html({})
        for attr in ("data-volume", "data-fade-in", "data-fade-out",
                     "data-media-start"):
            self.assertNotIn(attr, tag)

    def test_fades_longer_than_segment_warns(self):
        """淡入淡出之和超过片段时长会被引擎按比例压进去——写了一段淡变、实际
        几乎听不出来。只有渲染端知道片段多长，所以这句只能在这儿说。"""
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            _video_html({"fade_in": 99, "fade_out": 99})
        self.assertIn("fade_in+fade_out", buf.getvalue())


class VoiceoverDuck(unittest.TestCase):
    """视频原声的让路：默认那一版必须是不盖旁白的那一版。

    两条轨（口播 <audio> 与视频原声）默认都按 1.0 进混音器，所以"要了原声"
    的默认结果就是互相盖住——跑完不报错、成片没法听。过去这事的处置是一条
    `[warn]` 建议人自己写 volume：那句话没有改任何东西，成片照样没法听。
    现在改成脚本替他落一个让路默认，并说清压成多少、怎么改回去。
    """

    def _stderr_of(self, opts):
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            tag = _video_html(opts)
        return tag, buf.getvalue()

    def test_muted_off_gets_duck_default_without_author_asking(self):
        """没写 volume 就落让路默认增益——1.0 不该是默认。"""
        tag = _video_html({"muted": False})
        self.assertIn('data-volume="0.25"', tag)

    def test_duck_chain_present_by_default(self):
        """让路不止压响度：人声占住的那几档频段上还要开槽（data-fx-chain）。"""
        import html as _html
        import json
        import re
        tag = _video_html({"muted": False})
        m = re.search(r'data-fx-chain="([^"]*)"', tag)
        self.assertIsNotNone(m, "要了原声却没有让路 EQ")
        chain = json.loads(_html.unescape(m.group(1)))
        self.assertEqual(chain["version"], 1)
        freqs = [n["params"]["frequency"] for n in chain["nodes"]]
        self.assertTrue(freqs)
        # carve 的说明里明确写了：真正掩蔽人声的频段更高，按能量排会一直选到
        # 基频（160/250Hz），切那里只是把床铺削薄。让路得落在中高频。
        self.assertTrue(all(n["type"] == "peaking" for n in chain["nodes"]))
        self.assertTrue(all(f >= 1000 for f in freqs), freqs)
        self.assertTrue(all(n["params"]["gain"] < 0 for n in chain["nodes"]))

    def test_chain_json_is_html_escaped(self):
        """官方的 carve 脚本用 name="..." 正则找这些属性：JSON 的引号必须是
        &quot;，裸双引号会把整条属性截断。"""
        tag = _video_html({"muted": False})
        self.assertIn("&quot;version&quot;", tag)
        self.assertNotIn('data-fx-chain="{"', tag)

    def test_duck_false_drops_the_eq(self):
        """duck:false = 原声要当主体（采访原声、现场同期声），链必须撤掉。"""
        tag = _video_html({"muted": False, "duck": False})
        self.assertNotIn("data-fx-chain", tag)
        self.assertIn('data-volume="0.25"', tag)   # 响度兜底与 duck 是两件事

    def test_explicit_volume_wins(self):
        """写了 volume 就完全尊重，不再自动兜底——他要的响度只有他知道。"""
        tag, err = self._stderr_of({"muted": False, "volume": 1.0})
        self.assertIn('data-volume="1"', tag)
        self.assertNotIn("0.25", tag)
        self.assertNotIn("让路默认", err)

    def test_duck_is_announced_even_when_volume_is_explicit(self):
        """替人改了混音却一声不吭 = 另一类静默行为，哪怕方向是对的。
        写了 volume 的人也该知道自己身上还挂着一条 EQ。"""
        _tag, err = self._stderr_of({"muted": False, "volume": 0.5})
        self.assertIn("data-fx-chain", err)
        self.assertIn("duck:false", err)

    def test_duck_false_and_volume_stays_quiet(self):
        """两个旋钮都显式拧过 = 混音完全由人做主，不必再刷告警。"""
        _tag, err = self._stderr_of({"muted": False, "volume": 1.0,
                                     "duck": False})
        self.assertEqual(err, "")

    def test_duck_warn_says_how_to_take_control_back(self):
        """替他压下去就得说清怎么改回来，否则这个默认就是不可逆的。"""
        _tag, err = self._stderr_of({"muted": False})
        self.assertIn("0.25", err)
        self.assertIn("volume", err)
        self.assertIn("duck:false", err)

    def test_muted_video_gets_no_duck_attrs(self):
        """静音轨没有音轨可让：挂链和增益都是纯噪音（还会让 lint 的
        video_missing_muted 判据失效）。"""
        tag = _video_html({"duck": True})
        self.assertNotIn("data-fx-chain", tag)
        self.assertNotIn("data-volume", tag)


class DuckOptSchema(unittest.TestCase):
    """契约层：duck 与音轨四件套同一口径——只对 video 成立、必须是布尔。"""

    def _err(self, entry):
        with self.assertRaises(ValueError) as ctx:
            validate_images_json({"seg-b": entry})
        return str(ctx.exception)

    def test_accepts_duck_on_video(self):
        out = validate_images_json({"seg-b": {"src": "images/seg-b.mp4",
                                              "muted": False, "duck": False}})
        self.assertEqual(out["seg-b"]["duck"], False)

    def test_non_bool_rejected(self):
        """字符串 "false" 是真值：会让"关掉让路"变成"永远让路"，与
        loop/muted 那条是同一个坑。"""
        self.assertIn("必须是 JSON 布尔", self._err(
            {"src": "images/seg-b.mp4", "duck": "false"}))

    def test_non_video_rejected(self):
        """位图 / SVG / GIF 没有音轨可让，写了不会生效。"""
        for src in ("images/seg-b.png", "images/seg-b.svg", "images/seg-b.gif"):
            self.assertIn("不是视频", self._err({"src": src, "duck": True}), src)

    def test_explicit_type_video_allows_duck(self):
        validate_images_json({"seg-b": {"src": "images/seg-b.bin",
                                        "type": "video", "duck": False}})


class VideoAudioOptSchema(unittest.TestCase):
    """契约层：只挡"写了不会生效"的形态，取值范围照官方契约抄。"""

    def _err(self, entry):
        with self.assertRaises(ValueError) as ctx:
            validate_images_json({"seg-b": entry})
        return str(ctx.exception)

    def test_accepts_valid_video_audio_opts(self):
        out = validate_images_json({"seg-b": {
            "src": "images/seg-b.mp4", "muted": False, "volume": 0.25,
            "fade_in": 0.5, "fade_out": 1.0, "media_start": 2.5}})
        self.assertEqual(out["seg-b"]["volume"], 0.25)

    def test_volume_at_official_ceiling_is_allowed(self):
        """3.98 (+12 dB) 是官方契约的上限，不是随手取的数——按它判，别自创。"""
        validate_images_json({"seg-b": {"src": "images/seg-b.mp4",
                                        "volume": MEDIA_VOLUME_MAX}})

    def test_volume_above_ceiling_rejected(self):
        self.assertIn("超出范围", self._err(
            {"src": "images/seg-b.mp4", "volume": 4.0}))

    def test_negative_values_rejected(self):
        for opt in ("volume", "fade_in", "fade_out", "media_start"):
            self.assertIn("超出范围", self._err(
                {"src": "images/seg-b.mp4", opt: -0.1}), opt)

    def test_bool_rejected_for_numeric_opts(self):
        """布尔必须单独挡：isinstance(True, int) 为真，`"volume": true` 会溜过
        数值校验、被拼成 data-volume="True"——无效增益，症状正是"要了原声、
        成片没声音"，与本套契约要堵的是同一类静默退化。"""
        self.assertIn("必须是数字", self._err(
            {"src": "images/seg-b.mp4", "volume": True}))

    def test_string_rejected_for_numeric_opts(self):
        self.assertIn("必须是数字", self._err(
            {"src": "images/seg-b.mp4", "fade_in": "0.5"}))

    def test_opts_on_non_video_rejected(self):
        """写在位图 / SVG / GIF 上不会生效——没有音轨也没有可裁剪的时间轴。
        按 director/stage 的同一口径拒掉，不让它变成又一个"写了没反应"。
        """
        for src in ("images/seg-b.png", "images/seg-b.svg", "images/seg-b.gif"):
            self.assertIn("不是视频", self._err(
                {"src": src, "volume": 0.5}), src)

    def test_explicit_type_video_allows_opts(self):
        """显式 type:"video" 时不按扩展名判——同一条 classify_media_path 路径。"""
        validate_images_json({"seg-b": {"src": "images/seg-b.bin",
                                        "type": "video", "volume": 0.5}})

    def test_missing_src_still_reports_missing_src(self):
        """缺 src 时先报"缺少 src"这句更好懂的，不叠一句音轨取值的错。"""
        self.assertIn("缺少 'src'", self._err({"volume": 0.5}))


if __name__ == "__main__":
    unittest.main()
