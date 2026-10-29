"""主入口（gen_hyperframes）的契约下沉测试。

两块判据：--help 里的示例必须真能跑；images.json 里互不依赖的错必须一次报全。
（为什么是这两条、以及错代码是怎么被选出来的，见 scripts/_images_schema.py
validate_images_json 与 gen_hyperframes._epilog 的注释。）
"""
import contextlib
import io
import json
import re
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as G
from _images_schema import validate_images_json


def err_of(data):
    """跑validate_images_json 并返回它的报错文本（没报错就fail）。

    刻意**不捕 TypeError**：契约层的职责就是把"字段类型写错"报成人话，
    一条裸 TypeError 意味着某个坏值漏到了校验之外、要到下游才崩栈。把它
    当成"通过"过一次，字段名拼错与类型写错这两类真实缺陷都会被静默放过
    （沿用 tests/test_contracts.py 的同名助手，同一条判据）。
    """
    try:
        validate_images_json(data)
    except ValueError as e:
        return str(e)
    raise AssertionError("应该被契约层拒绝，却通过了：{}".format(data))


class EpilogSampleRuns(unittest.TestCase):
    """--help 里那份示例必须真能跑通。"""

    def _images_block(self):
        epilog = G._epilog()
        # 从 epilog 里抠出那段 images.json：它是缩进过的 JSON 块
        block = re.search(r"  (\{\n(?:.*\n)*?  \})", epilog)
        self.assertIsNotNone(block, "--help 里没有可抠出的 images.json 示例")
        return json.loads(block.group(1))

    def test_epilog_images_json_validates(self):
        data = self._images_block()
        # 真喂给契约层：它自己写错一处（少了 src、布尔写成字符串、stage 拼错…）
        # 报错就在教人怎么写错
        out = validate_images_json(json.loads(json.dumps(data)))
        self.assertEqual(sorted(out), sorted(data))

    def test_epilog_example_does_not_get_silently_dropped(self):
        """示例里的 animate.steps 得过 animate 校验（不是只过 images 那层）。

        这条分开写是因为 animate 的键集封闭且不认"看名字就知道"的键
        （锚点是 at/at_time 不是 anchor、目标是 target 不是 selector）。
        images.json 那层只查"是不是 svg"，steps 内部的错它一个都不管。
        """
        data = self._images_block()
        self.assertIn("animate", data["seg2"])
        # 示例就是三种素材条目（图 / SVG 导演档 / GIF）的形状：曾挂过
        # stage:"keep"，而 keep 是跨页接续标记、接的必须是同一张图的上一页
        # 导演档——照抄那份在生成期直接报"上一段没有可接续的内联画面"，
        # "实测跑通"成了谎话（端到端回归见 test_check_tools.EpilogExampleRuns）。
        self.assertNotIn("stage", data["seg2"])
        # 抠出来的那份必须真的走到了 animate 校验：把 steps 换成一个明显
        # 非法的形态，若仍通过说明 animate 根本没被调用（测试自身在骗人）
        broken = json.loads(json.dumps(data))
        broken["seg2"]["animate"]["steps"] = [{"target": "#bar-1", "at": 0,
                                                "anchor": 0}]
        self.assertIn("anchor", err_of(broken))


class ImagesJsonReportsEverythingAtOnce(unittest.TestCase):
    """一份images.json 里多处互不依赖的错，必须同一轮报全。"""

    def test_unrelated_entry_errors_are_all_reported(self):
        """三段各错一处：三段之间互不依赖，报错里三段都要点名。"""
        err = err_of({
            # src 用 mp4：这条要的是"muted 类型写错"这一处，换成 png 会连带
            # 触发 video-only 门禁（poster/loop/muted/autoplay/playsinline 只对
            # video 生效），把计数变成 4 而掩盖这里想钉的"三段各一处"
            "seg1": {"src": "images/a.mp4", "muted": "true"},
            "seg 2": {"src": "images/b.png"},
            "seg3": "images/c.png",
        })
        self.assertIn("3 处问题", err)
        self.assertIn("'seg1'", err)
        self.assertIn("合法段 id", err)   # seg 2：key 里有空格
        self.assertIn("'seg3'", err)
        self.assertIn("裸字符串路径", err)

    def test_same_entry_playback_and_animation_errors_are_all_reported(self):
        """同一段里 muted 写错 + animate 挂在 PNG 上——两处互不依赖，都要报。"""
        err = err_of({
            "seg1": {"src": "images/a.mp4", "muted": "true",
                     "animate": {"steps": "no"}},
        })
        self.assertIn("muted", err)
        self.assertIn("animate", err)
        self.assertIn("2 处问题", err)

    def test_every_audio_opt_key_is_reported_not_just_the_first(self):
        """volume / fade_in / fade_out / media_start 四个键互不依赖，一次报全。"""
        err = err_of({"seg1": {
            "src": "images/a.mp4",
            "volume": 5, "fade_in": -1, "fade_out": -2, "media_start": "x",
        }})
        for key in ("volume", "fade_in", "fade_out", "media_start"):
            self.assertIn(key, err)
        self.assertIn("4 处问题", err)

    def test_every_playback_flag_is_reported_not_just_the_first(self):
        """loop / muted / autoplay / playsinline 四个开关互不依赖，一次报全。"""
        err = err_of({"seg1": {
            "src": "images/a.mp4",
            "loop": "no", "muted": "true", "autoplay": 1, "playsinline": "0",
        }})
        for flag in ("loop", "muted", "autoplay", "playsinline"):
            self.assertIn(flag, err)
        self.assertIn("4 处问题", err)

    def test_provenance_and_poster_errors_are_not_masked_by_each_other(self):
        """poster 写错不该遮住 provenance 字段的错（两组互不依赖）。"""
        err = err_of({"seg1": {
            "src": "images/a.mp4", "poster": "", "source_url": 3, "license": 5,
        }})
        self.assertIn("poster", err)
        self.assertIn("source_url", err)
        self.assertIn("license", err)
        self.assertIn("3 处问题", err)

    def test_animate_error_is_not_masked_by_a_stale_key(self):
        """写了已删除的 stage 又写活动画：活动的错照报，旧键只出提示。

        旧键的提示走 stderr（不阻断），所以它得在**报错之前**就打得出来——两条
        都是"作者改这一处时该看见的话"，不该因为另一处先抛就消失。
        """
        buf = io.StringIO()
        data = {"seg1": {"src": "images/a.png", "animate": {"steps": []},
                         "stage": "carry"}}
        with contextlib.redirect_stderr(buf):
            with self.assertRaises(ValueError) as ctx:
                validate_images_json(data)
        self.assertIn("animate", str(ctx.exception))

    def test_one_group_error_does_not_hide_another_group(self):
        """type 组错了不能遮住播放组、路径组。"""
        err = err_of({"seg1": {
            "src": "images/a.mp4", "type": "chart", "muted": "true",
            "source_url": 3,
        }})
        for frag in ("'type'", "muted", "source_url"):
            self.assertIn(frag, err)

    def test_dependent_checks_stay_fail_fast_instead_of_crashing(self):
        """src 缺失时不许把整段其余的错一起吞掉，也不许抛 AttributeError。

        两种失败都是真的：吞掉的话，作者补完 src 重跑才发现还有下一处，
        而且开头那句"有 1 处问题"会让他以为补完就干净了；抛异常的话，他看到
        的是一条从没见过的栈。
        """
        err = err_of({"seg1": {"muted": "true", "loop": "no"}})
        self.assertIn("缺少 'src'", err)
        self.assertIn("muted", err)
        self.assertIn("loop", err)

    def test_bad_key_stops_only_that_entry(self):
        """key 不合法的那段只报它自己那一条，不牵连别的段。

        每条错都要引用这个 key 才能点名给人看，key 都不合法了就没法点名——
        所以那段整段跳过是对的，但只是它那段。
        """
        err = err_of({
            "seg 1": {"src": "images/a.png", "muted": "true"},
            "seg2": {"src": "images/b.mp4", "loop": "no"},
        })
        self.assertIn("合法段 id", err)
        self.assertIn("'seg2'", err)
        self.assertIn("2 处问题", err)

    def test_video_opts_on_entry_without_src_reports_missing_src_not_crash(self):
        """src 缺失时写了音轨键：报"缺少 src"，不报"不是视频"也不抛异常。

        钉住一条隐式契约：_is_video_src 假定 src 是字符串（它的前提由_validate_entry
        的早退保证）。哪天有人把调用挪到早退之前，这里就会变成裸 TypeError。
        """
        err = err_of({"seg1": {"volume": 1.0, "duck": True, "poster": "images/p.png",
                               "muted": True}})
        self.assertIn("缺少 'src'", err)
        self.assertNotIn("不是视频", err)
        self.assertNotIn("TypeError", err)

    def test_video_only_opts_on_bitmap_are_rejected_not_silently_dropped(self):
        """poster / loop / muted / autoplay / playsinline 写在非 video 上要拒。

        渲染端的 <img> 分支只输出 src（html_renderer._media_html），这几个键写在
        ImageGen 图/SVG/GIF 上会被静默丢掉——而它们都在 MEDIA_ENTRY_KEYS 里，连
        unknown-key 提示都不会打。口径必须与音轨四件套（_check_audio_opts）
        一致：写了不会生效就拒，别让作者从报错文案里学到两套规则。
        """
        for key, val in (("poster", "images/c.png"), ("loop", False),
                         ("muted", False), ("autoplay", False),
                         ("playsinline", False)):
            for src, typ in (("images/a.png", None), ("images/a.svg", None),
                             ("images/a.gif", None)):
                entry = {"src": src, key: val}
                if typ:
                    entry["type"] = typ
                err = err_of({"seg1": entry})
                self.assertIn(f"写了 {key}", err)
                self.assertIn("不是视频", err)
        # 视频上照过：同一条门禁不能误伤合法写法
        ok = validate_images_json({"seg1": {
            "src": "images/a.mp4", "poster": "images/c.png", "loop": True,
            "muted": True, "autoplay": True, "playsinline": True}})
        self.assertEqual(ok["seg1"]["poster"], "images/c.png")

    def test_valid_entries_still_pass_and_get_normalized(self):
        """重构没改正常路径：合法条目照过，且 src 仍被归一回正斜杠。"""
        data = validate_images_json({
            "seg1": {"src": "images\\a.png"},
            "seg2": {"src": "images/b.svg",
                     "animate": {"steps": [{"target": "#t", "at": 0,
                                             "to": {"opacity": 1}}]},
                     "stage": "keep"},
            "seg3": {"src": "images/c.mp4", "type": "video", "muted": True,
                     "loop": True, "volume": 1.0, "fade_in": 0.3,
                     "poster": "images/c.png"},
        })
        self.assertEqual(data["seg1"]["src"], "images/a.png")
        self.assertEqual(data["seg3"]["type"], "video")


if __name__ == "__main__":
    unittest.main()