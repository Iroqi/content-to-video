"""主入口（gen_hyperframes）的契约下沉测试。

两块判据：--help 里的示例必须真能跑；images.json 里互不依赖的错必须一次报全。
（为什么是这两条、以及错代码是怎么被选出来的，见 scripts/_images_schema.py
validate_images_json 与 gen_hyperframes._epilog 的注释。）
"""
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
        """示例里的 director.steps 得过 director 校验（不是只过 images 那层）。

        这条分开写是因为 director 的键集封闭且不认"看名字就知道"的键
        （锚点是 at/at_time 不是 anchor、目标是 target 不是 selector）。
        images.json 那层只查"是不是 svg"，steps 内部的错它一个都不管。
        """
        data = self._images_block()
        self.assertIn("director", data["seg2"])
        self.assertEqual(data["seg2"]["stage"], "keep")
        # 抠出来的那份必须真的走到了 director 校验：把 steps 换成一个明显
        # 非法的形态，若仍通过说明 director 根本没被调用（测试自身在骗人）
        broken = json.loads(json.dumps(data))
        broken["seg2"]["director"]["steps"] = [{"target": "#bar-1", "at": 0,
                                                "anchor": 0}]
        self.assertIn("anchor", err_of(broken))


class ImagesJsonReportsEverythingAtOnce(unittest.TestCase):
    """一份images.json 里多处互不依赖的错，必须同一轮报全。"""

    def test_unrelated_entry_errors_are_all_reported(self):
        """三段各错一处：三段之间互不依赖，报错里三段都要点名。"""
        err = err_of({
            "seg1": {"src": "images/a.png", "muted": "true"},
            "seg 2": {"src": "images/b.png"},
            "seg3": "images/c.png",
        })
        self.assertIn("3 处问题", err)
        self.assertIn("'seg1'", err)
        self.assertIn("合法段 id", err)   # seg 2：key 里有空格
        self.assertIn("'seg3'", err)
        self.assertIn("裸字符串路径", err)

    def test_same_entry_playback_and_animation_errors_are_all_reported(self):
        """同一段里 muted 写错 + director 挂在 PNG 上——两处互不依赖，都要报。"""
        err = err_of({
            "seg1": {"src": "images/a.png", "muted": "true",
                     "director": {"steps": "no"}},
        })
        self.assertIn("muted", err)
        self.assertIn("director", err)
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
            "src": "images/a.png", "poster": "", "source_url": 3, "license": 5,
        }})
        self.assertIn("poster", err)
        self.assertIn("source_url", err)
        self.assertIn("license", err)
        self.assertIn("3 处问题", err)

    def test_stage_and_director_errors_are_not_masked_by_each_other(self):
        """director 与 stage 都挂在 PNG 上时两条都要报（互不依赖）。"""
        err = err_of({"seg1": {
            "src": "images/a.png", "director": {"steps": []}, "stage": "carry",
        }})
        self.assertIn("director", err)
        self.assertIn("stage", err)

    def test_one_group_error_does_not_hide_another_group(self):
        """type 组错了不能遮住播放组、路径组、动画组的错。"""
        err = err_of({"seg1": {
            "src": "images/a.mp4", "type": "chart", "muted": "true",
            "source_url": 3, "stage": "carry",
        }})
        for frag in ("'type'", "muted", "source_url", "stage"):
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
            "seg2": {"src": "images/b.png", "loop": "no"},
        })
        self.assertIn("合法段 id", err)
        self.assertIn("'seg2'", err)
        self.assertIn("2 处问题", err)

    def test_video_opts_on_entry_without_src_reports_missing_src_not_crash(self):
        """src 缺失时写了音轨键：报"缺少 src"，不报"不是视频"也不抛异常。

        钉住一条隐式契约：_is_video_src 假定 src 是字符串（它的前提由_validate_entry
        的早退保证）。哪天有人把调用挪到早退之前，这里就会变成裸 TypeError。
        """
        err = err_of({"seg1": {"volume": 1.0, "duck": True}})
        self.assertIn("缺少 'src'", err)
        self.assertNotIn("不是视频", err)
        self.assertNotIn("TypeError", err)

    def test_valid_entries_still_pass_and_get_normalized(self):
        """重构没改正常路径：合法条目照过，且 src 仍被归一回正斜杠。"""
        data = validate_images_json({
            "seg1": {"src": "images\\a.png"},
            "seg2": {"src": "images/b.svg",
                     "director": {"steps": [{"target": "#t", "at": 0,
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