import ast
import contextlib
import glob
import io
import os
import re
import subprocess
import sys
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path（本文件随后 import 的脚本模块需要它）
import _animate
import _segments as SEG
import _timeline as TL
import _source_schema as SRC
import _manifest_schema as MAN
import _images_schema as IMG


def bad(fn, *a, contains=None):
    """断言校验函数抛错（契约层统一抛 ValueError/SystemExit 一类），并可核对报错里的关键词。

    刻意**不捕 TypeError**：契约层的职责就是把"字段类型写错"报成人话，
    一条裸 TypeError 意味着某个坏值漏到了校验之外、要到下游才崩栈。把它
    当成"通过"过一次，字段名拼错与类型写错这两类真实缺陷都会被静默放过。
    """
    try:
        fn(*a)
    except (ValueError, SystemExit) as e:
        if contains:
            assert contains in str(e), f"报错里没有 {contains!r}: {e}"
        return
    except TypeError as e:
        raise AssertionError(
            f"契约层抛了裸 TypeError（{e}）——该报成人话而不是让调用方崩栈。"
            "这说明该字段没被校验到") from None
    raise AssertionError("应该被契约层拒绝，却通过了")


class SourceValidation(unittest.TestCase):
    def test_sample_source_ok(self):
        SRC.validate_segments_source(H.sample_source())

    def test_unknown_top_key_rejected(self):
        s = H.sample_source()
        s["closing_text"] = "拼错的结尾键"
        bad(SRC.validate_segments_source, s, contains="closing_text")

    def test_opening_animation_key_is_removed(self):
        """开屏页默认强绑定苹果风开场：`opening_animation` 键已从契约移除。

        写了旧键 = 报错并带指引（而不是泛化的"未知键"）——字段集封闭时多出的
        键本来就报错，这里把报错信息写成"默认强绑定"的迁移指引。
        """
        bad(SRC.validate_segments_source,
            dict(H.sample_source(), opening_animation="apple"),
            contains="默认强绑定")
        bad(SRC.validate_segments_source,
            dict(H.sample_source(), opening_animation="glow"),
            contains="opening_animation")
        bad(SRC.validate_segments_source,
            dict(H.sample_source(), opening_animation=True),
            contains="opening_animation")
        # 旧键 + canvas 组合同样被拦（键已不存在，写 canvas 只有版式生效）
        bad(SRC.validate_segments_source,
            dict(H.sample_source(), opening_layout="canvas",
                 opening_animation="apple"),
            contains="opening_animation")

    def test_opening_animation_still_ok_on_agenda_layout(self):
        """显式写 agenda（=缺省那一档）不受这条限制：那一页确实是 agenda 卡。

        旧键 + 显式 agenda 仍被拦（键已移除）；这条保留是为了守住"agenda 版式
        本身合法"——报错来自旧键、不是来自版式。
        """
        bad(SRC.validate_segments_source,
            dict(H.sample_source(), opening_layout="agenda",
                 opening_animation="apple"),
            contains="opening_animation")

    def test_unknown_segment_key_rejected(self):
        s = H.sample_source()
        s["segments"][0]["note"] = "备注"
        bad(SRC.validate_segments_source, s, contains="note")

    def test_underscore_key_is_a_draft_slot_not_a_typo(self):
        """`_` 开头的键按草稿放行（打一条 [notice]），但它**不进产物**。

        拼错成合法键的近邻（takeaway → take_away）才是这条契约要挡的；而"我有
        一版备选文案想先放着"是个正当需求，没有草稿位时人只能绕过契约（塞进正文、
        改成注释），那才是真的静默丢信息。两条一起守：放行 + 保证不被读。
        """
        import contextlib
        import io

        s = H.sample_source()
        s["segments"][0]["_alt_title"] = "备选标题"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            SRC.validate_segments_source(s)
        self.assertIn("_alt_title", err.getvalue())
        self.assertIn("草稿", err.getvalue())

        # 草稿键不可能是真字段的拼错：真字段一个都不以 _ 开头
        for key in SRC.SEGMENT_KEYS:
            self.assertFalse(key.startswith("_"), f"真字段 {key} 撞上了草稿位命名")

    def test_text_and_dialogue_are_exclusive(self):
        s = H.sample_source()
        s["segments"][0]["dialogue"] = [{"speaker": "a", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)

    def test_whitespace_text_with_dialogue_is_not_silently_ignored(self):
        """纯空白 text 单独存在时报"缺正文"，配上 dialogue 却整字段被静默忽略。

        修复前 `text: "  "` + 合法 dialogue 能过校验（has_text 为假、dialogue
        兜底）——同一输入两种答案，"写了等于没写"：作者以为在写独白，其实走的是
        对话分支，那句空白永远到不了配音。
        """
        s = H.sample_source()
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        s["segments"][0]["text"] = "   "
        s["segments"][0]["dialogue"] = [{"speaker": "host", "text": "你好。"}]
        bad(SRC.validate_segments_source, s, contains="纯空白")

    def test_dialogue_needs_declared_speakers(self):
        s = H.sample_source()
        s["segments"][0].pop("text")
        s["segments"][0]["dialogue"] = [{"speaker": "host", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        SRC.validate_segments_source(s)

    def test_dialogue_speaker_must_be_declared(self):
        s = H.sample_source()
        s["segments"][0].pop("text")
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        s["segments"][0]["dialogue"] = [{"speaker": "ghost", "text": "你好。"}]
        bad(SRC.validate_segments_source, s)

    def test_invalid_voice_rejected(self):
        s = H.sample_source()
        s["segments"][0]["voice_id"] = "不存在的音色"
        bad(SRC.validate_segments_source, s)

    def test_accent_whitelist(self):
        for ok in ("#fff", "#12ab34", "tomato"):
            s = H.sample_source()
            s["segments"][0]["accent"] = ok
            SRC.validate_segments_source(s)
        for evil in ("rgb(0,0,0)", "var(--x)", "red;}</style>", "tomatoo"):
            s = H.sample_source()
            s["segments"][0]["accent"] = evil
            bad(SRC.validate_segments_source, s)

    def test_content_segment_cannot_declare_agenda_layout(self):
        s = H.sample_source()
        s["segments"][0]["layout"] = "agenda"
        bad(SRC.validate_segments_source, s)

    def test_layout_is_case_sensitive(self):
        s = H.sample_source()
        s["segments"][0]["layout"] = "Canvas"
        bad(SRC.validate_segments_source, s)

    def test_segment_id_rules(self):
        for evil in ('seg"x', "seg.x", "seg x", "x1", "a" * 70):
            s = H.sample_source()
            s["segments"][0]["id"] = evil
            bad(SRC.validate_segments_source, s)

    def test_duplicate_ids_rejected(self):
        s = H.sample_source()
        s["segments"][1]["id"] = s["segments"][0]["id"]
        bad(SRC.validate_segments_source, s)

    # ── 上墙文字 vs 口播稿：分号只对上墙的一行报错 ─────────────────
    def test_top_level_on_screen_keys_reject_semicolon(self):
        for key in SRC.ON_SCREEN_TOP_KEYS:
            for semi in ("；", ";"):
                s = H.sample_source()
                s[key] = "半句话" + semi + "另外半句"
                bad(SRC.validate_segments_source, s, contains=key)

    def test_segment_on_screen_keys_reject_semicolon(self):
        for key in SRC.ON_SCREEN_SEGMENT_KEYS:
            s = H.sample_source()
            s["segments"][0][key] = "半句话；另外半句"
            bad(SRC.validate_segments_source, s, contains="分号")

    def test_cta_rejects_semicolon(self):
        s = H.sample_source()
        s["cta"] = "关注我们；下期见"
        bad(SRC.validate_segments_source, s, contains="cta")

    def test_narration_may_contain_semicolon(self):
        # 口播稿里分号是终止标点（见 _text.split_sentences），念出来两次停顿，
        # 不是"两件事挤一行"，所以门禁必须放它过去。
        s = H.sample_source()
        s["opening"] = "大家好；今天聊两件事。"
        s["closing"] = "谢谢观看;下期再见。"
        s["segments"][0]["text"] = "先讲背景；再讲进展。"
        SRC.validate_segments_source(s)
        s["segments"][0].pop("text")
        s["speakers"] = {"host": {"voice_id": "茉莉"}}
        s["segments"][0]["dialogue"] = [{"speaker": "host", "text": "是这样；你同意吗。"}]
        SRC.validate_segments_source(s)


class LayoutDispatch(unittest.TestCase):
    def test_seg_layout(self):
        self.assertEqual(SEG.seg_layout({"id": "seg1"}), "slot")
        self.assertEqual(SEG.seg_layout({"id": "seg1", "layout": "canvas"}), "canvas")
        # 结构性页漏盖 layout 时按 id 兜回 agenda，而不是塌成槽位页
        self.assertEqual(SEG.seg_layout({"id": "opening"}), "agenda")
        self.assertEqual(SEG.seg_layout({"id": "closing", "layout": "canvas"}), "canvas")

    def test_needs_image(self):
        self.assertTrue(SEG.needs_image({"id": "seg1"}))
        self.assertFalse(SEG.needs_image({"id": "opening", "layout": "agenda"}))
        self.assertTrue(SEG.needs_image({"id": "opening", "layout": "canvas"}))


class ManifestValidation(unittest.TestCase):
    def test_built_manifest_ok(self):
        MAN.validate_timing_manifest(H.make_manifest())

    def test_manifest_has_no_opening_animation_key(self):
        """开屏页默认强绑定：manifest 不再透传 opening_animation 键。

        build_parts 不再把 source 的旧键带进 manifest；手写 manifest 硬塞时，
        契约层给的是**迁移指引**而不是泛化的"未知字段"——与写稿入口
        （_source_schema）同一条口径：那条指引必须排在 _reject_unknown_keys
        之前，否则 reject 先报"未知字段"，作者永远拿不到"该删这个键"的说明。
        """
        m = H.make_manifest(dict(H.sample_source(), opening_animation="apple"))
        opening = next(s for s in m["segments"] if s["id"] == "opening")
        self.assertNotIn("opening_animation", opening)
        MAN.validate_timing_manifest(m)
        # 手写 manifest 硬塞旧键：任何段都要拿到那句迁移指引
        for sid in ("opening", "seg-a", "closing"):
            with self.subTest(sid=sid):
                m2 = H.make_manifest()
                self._seg(m2, sid)["opening_animation"] = "apple"
                bad(MAN.validate_timing_manifest, m2, contains="默认强绑定")
        # 旧值非法也一样走未知字段（值校验分支已随键一起移除）
        m3 = H.make_manifest()
        self._seg(m3, "opening")["opening_animation"] = "glow"
        bad(MAN.validate_timing_manifest, m3, contains="opening_animation")

    def _seg(self, m, sid="seg-a"):
        return next(s for s in m["segments"] if s["id"] == sid)

    def test_segment_speed_value_validated(self):
        """speed 在封闭键集里，值也一起封——否则 "fast" 一路活到 apply_speed 才炸。"""
        for value, contains in (("fast", "speed"), (0, "speed"), (-1.5, "speed"),
                                (float("nan"), "speed"), (True, "speed")):
            with self.subTest(value=value):
                m = H.make_manifest()
                self._seg(m)["speed"] = value
                bad(MAN.validate_timing_manifest, m, contains=contains)
        # 合法值放行
        m = H.make_manifest()
        self._seg(m)["speed"] = 1.5
        MAN.validate_timing_manifest(m)

    def test_segment_voice_style_must_be_string(self):
        m = H.make_manifest()
        self._seg(m)["voice_style"] = 123
        bad(MAN.validate_timing_manifest, m, contains="voice_style")
        m = H.make_manifest()
        self._seg(m)["voice_style"] = "沉稳讲解"
        MAN.validate_timing_manifest(m)

    def test_turns_shape_validated(self):
        """turns 是人工回查用的数据层，不进 HTML——拼错键/类型只会静默忽略。"""
        def with_turns(turn):
            m = H.make_manifest()
            self._seg(m)["turns"] = [turn]
            return m
        ok = {"start": 0, "end": 1, "speaker": "host", "label": "主播",
              "voice_id": "茉莉"}
        MAN.validate_timing_manifest(with_turns(ok))
        # voice_style / label 缺省合法（producer 只在说话人配了才写 voice_style）
        MAN.validate_timing_manifest(with_turns({"start": 0, "end": 1,
                                                  "speaker": "host"}))
        for turn, contains in (
            (5, "turns"),
            (["x"], "turns"),
            ({"start": 0, "speler": "host"}, "speler"),      # 键拼错
            ({"start": "x"}, "start"),                        # 类型错
            ({"start": -1}, "start"),                         # 负数
            ({"start": 5, "end": 2}, "end"),                  # 区间倒挂
            ({"start": 5.0, "end": 2.0}, "end"),              # 区间倒挂（浮点写法）
            ({"start": 0, "speaker": 7}, "speaker"),
        ):
            with self.subTest(turn=turn):
                bad(MAN.validate_timing_manifest, with_turns(turn), contains=contains)
        # 浮点写法的合法区间照放：0.0 也是 int 之外的同一条通路。
        MAN.validate_timing_manifest(with_turns({"start": 0.0, "end": 1.0}))

    def test_unknown_top_key_rejected(self):
        m = H.make_manifest()
        m["combined_audo"] = "x"
        bad(MAN.validate_timing_manifest, m, contains="combined_audo")

    def test_segment_typo_rejected(self):
        m = H.make_manifest()
        m["segments"][1]["take_away"] = "x"
        bad(MAN.validate_timing_manifest, m)

    def test_overlapping_sentences_rejected(self):
        m = H.make_manifest()
        m["sentences"][1]["start_time"] = 0.5
        for seg in m["segments"]:
            for s in seg["sentences"]:
                if s["index"] == 1:
                    s["start_time"] = 0.5
        bad(MAN.validate_timing_manifest, m)

    def test_segments_must_cover_every_sentence(self):
        m = H.make_manifest()
        m["segments"][-1]["sentences"] = m["segments"][-1]["sentences"][:-1]
        bad(MAN.validate_timing_manifest, m)

    def test_segment_sentence_must_match_top_level(self):
        m = H.make_manifest()
        m["segments"][0]["sentences"][0]["text"] = "被手改过的文本。"
        bad(MAN.validate_timing_manifest, m)

    def test_schema_version_pinned(self):
        m = H.make_manifest()
        m["schema_version"] = 3
        bad(MAN.validate_timing_manifest, m)

    def test_schema_version_must_be_the_integer_two(self):
        """`2.0 != 2` 为 False：浮点写法的版本号修复前会悄悄放行。

        版本号是给人与 diff 看的声明，2 与 2.0 在 JSON/YAML 来回转换时互相变体；
        口径收严成"int 2"一种答案，手写成 2.0 时得到的是人话报错而不是放行。
        """
        m = H.make_manifest()
        m["schema_version"] = 2.0
        bad(MAN.validate_timing_manifest, m, contains="schema_version")
        # 字符串 "2" 同样拒（!= 比较下它本来就被拒，这里钉住不放松）
        m["schema_version"] = "2"
        bad(MAN.validate_timing_manifest, m, contains="schema_version")


class ImagesJson(unittest.TestCase):
    def test_relative_paths_ok(self):
        IMG.validate_images_json({"seg-a": {"src": "images/seg-a.svg"}})

    def test_path_escape_rejected(self):
        for evil in ("../x.png", "/etc/passwd", "images/../../x.png", "C:\\x.png", "http://x/y.png"):
            bad(IMG.validate_images_json, {"seg-a": {"src": evil}})

    def test_windows_dot_variants_escape_rejected(self):
        """Win32 剥掉每个组件的尾部点与空格：`.. `、`...`、`.... ` 在 Windows 上
        都是 `..`（Linux 下它们是普通目录名，所以这个洞只在 Windows 逃得掉）。
        契约层按 Windows 口径归一后再判深度，合法名（`v1.` → `v1`）不受影响。
        """
        for evil in (".. ", "...", ".... ", "images/.../../x.png",
                     "images/.. /../x.png", "images/..\\..\\x.png"):
            bad(IMG.validate_images_json, {"seg-a": {"src": evil}},
                contains="越过项目根目录")
        # 只是以点结尾的合法文件名、以及平衡穿越（归一后仍在项目内）照旧放行
        IMG.validate_images_json({"seg-a": {"src": "images/v1./a.png"}})
        IMG.validate_images_json({"seg-a": {"src": "images/.../x.png"}})

    def test_bare_string_rejected(self):
        bad(IMG.validate_images_json, {"seg-a": "images/a.png"})

    def test_unknown_key_is_reported_not_rejected(self):
        # 设计如此：未知键在预检（validate_images_json）打 [warn] 但不阻断；
        # 判定收口在 unknown_media_keys
        entry = {"src": "images/a.png", "scr": "typo", "alt": "x"}
        IMG.validate_images_json({"seg-a": entry})
        self.assertEqual(IMG.unknown_media_keys(entry), ["alt", "scr"])

    def test_video_and_gif_type_accepted(self):
        """video/gif 是合法 type：gif 与 image 同档，视频另走 <video> 分支。"""
        IMG.validate_images_json({"seg-a": {"src": "images/a.mp4", "type": "video"}})
        IMG.validate_images_json({"seg-a": {"src": "images/a.gif", "type": "gif"}})
        IMG.validate_images_json(
            {"seg-a": {"src": "images/a.mp4", "type": "video", "loop": True,
                       "muted": True, "autoplay": False, "playsinline": True,
                       "poster": "images/a.png"}})

    def test_explicit_null_type_is_normalized_to_absent(self):
        """显式 "type": null 必须被抹成"键不存在"，不能原样流到渲染端。

        null 与缺键在 Python 里都读到 None，但下游两种消费口径相反：
        _is_video_src 写 `value.get("type") or "auto"`（null 当缺省），
        html_renderer._media_html 写 `media.get("type", "auto")`（null 当显式值
        ——键在场时默认值根本不生效）——classify_media_path 见 None != "auto"
        会原样返回 None，一条 mp4 配 "type": null 就此静默变成 <img>。
        """
        entry = {"src": "images/a.mp4", "type": None}
        IMG.validate_images_json({"seg-a": entry})
        self.assertNotIn("type", entry)
        # 渲染端 _media_html 的调用形状：键已不在场，回到扩展名判定
        self.assertEqual(
            IMG.classify_media_path("images/a.mp4", entry.get("type", "auto")),
            "video")

    def test_still_removed_types_stay_rejected(self):
        """chart/formula 是真删掉的能力，误写时必须点名劝去手绘 SVG。"""
        bad(IMG.validate_images_json, {"seg-a": {"src": "images/a.png", "type": "chart"}},
            contains="手绘 SVG")
        bad(IMG.validate_images_json, {"seg-a": {"src": "images/a.png", "type": "formula"}},
            contains="手绘 SVG")

    def test_video_playback_flags_must_be_json_bool(self):
        """字符串 "false" 是真值——渲染端按 opts.get("loop", True) 取值，
        写成字符串会让"关掉循环"变成"永远循环"，所以契约层就拒。"""
        for flag in ("loop", "muted", "autoplay", "playsinline"):
            bad(IMG.validate_images_json,
                {"seg-a": {"src": "images/a.mp4", "type": "video", flag: "false"}},
                contains=flag)

    def test_poster_must_be_non_empty_string(self):
        """旧写法 `if poster:` 让空串 "" 与 None 同判，坏空串会原样拼进 HTML。"""
        bad(IMG.validate_images_json,
            {"seg-a": {"src": "images/a.mp4", "poster": ""}}, contains="poster")
        bad(IMG.validate_images_json,
            {"seg-a": {"src": "images/a.mp4", "poster": 7}}, contains="poster")

    def test_poster_shares_the_src_path_rules(self):
        for evil in ("../p.png", "/etc/passwd", "C:\\p.png"):
            bad(IMG.validate_images_json,
                {"seg-a": {"src": "images/a.mp4", "poster": evil}}, contains="poster")

    def test_classify_media_path_by_extension(self):
        for p, want in (("a.mp4", "video"), ("a.WEBM", "video"), ("a.mov", "video"),
                        ("a.mkv", "video"), ("a.avi", "video"),
                        ("a.gif", "gif"), ("a.png", "image"), ("a.svg", "image"),
                        ("a.jpg", "image"), ("a.webp", "image")):
            self.assertEqual(IMG.classify_media_path(p), want, p)
        # 显式 type 优先于扩展名
        self.assertEqual(IMG.classify_media_path("a.gif", "image"), "image")
        self.assertEqual(IMG.classify_media_path("a.png", "video"), "video")


class Speed(unittest.TestCase):
    def test_validate_speed(self):
        TL.validate_speed(1.5)
        for badv in (0, -1, float("nan"), float("inf")):
            bad(TL.validate_speed, badv)


class ConstantsHaveOneOwner(unittest.TestCase):
    """抄一遍的东西迟早分家——用 AST 把"同一份知识"钉在一处。

    行文里写着"同一份"的两份常量，比两份不说话的常量更危险：读代码的人
    连怀疑的理由都没有。
    """

    def test_camera_keys_have_exactly_one_definition(self):
        """CAMERA_KEYS 的真源只在 `_animate`，别处一个都不许再抄。"""
        here = os.path.dirname(_animate.__file__)
        owners = []
        for name in sorted(os.listdir(here)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(here, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            if any(isinstance(t, ast.Name) and t.id == "CAMERA_KEYS"
                   for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   for t in n.targets):
                owners.append(name)
        self.assertEqual(["_animate.py"], owners)


_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts")


class AttrKeySingleSource(unittest.TestCase):
    """GSAP attr 通道名：真源只有 `_images_schema.ATTR_KEY` 一处。"""

    def test_no_module_rewrites_the_literal(self):
        """反向：任何一处把 "attr" 写回 dict 键，这条就红。

        `_stage_carry` 的收尾态烘焙与 `html_renderer` 的补间发射各写一个字面量
        时，"契约层放行的形状"与"烘焙认得的形状"只是恰好相等——分家那天症状是
        stage:"keep" 接续页第 0 帧就坏，且生成期零报错。
        """
        offenders = []
        for path in glob.glob(os.path.join(_SCRIPTS_DIR, "*.py")):
            # _images_schema 是 ATTR_KEY 的登记处，它自己当然要写字面量；
            # _derive 的 "attr" 是 derive payload 的自有键（"绑到哪个 SVG
            # 属性"），与 GSAP 的 attr 通道同名不同义，不在此列。
            if os.path.basename(path) in ("_images_schema.py", "_derive.py"):
                continue
            tree = ast.parse(open(path, encoding="utf-8").read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key in node.keys:
                    if (isinstance(key, ast.Constant) and key.value == "attr"):
                        offenders.append(
                            "%s:%d" % (os.path.basename(path), node.lineno))
        self.assertEqual([], offenders,
                         "GSAP attr 通道名必须走 _images_schema.ATTR_KEY")


import _css_guard
import _media_size
import check_svg as _check_svg

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SingleJudgment(unittest.TestCase):
    """同一条判据只写一份：两处各写一遍的那几处。"""

    def test_svg_guard_first_branch_was_subsumed(self):
        """`"svg" in sel.split()` 恒被 `\\bsvg\\b` 包含——留着只是噪音。

        这条把"包含关系"本身钉住：哪天有人把 `\bsvg\b` 放宽（比如只认
        `^svg`），这条会红，提醒他第一条分支不再冗余、得真回来了。
        """
        corpus = ["svg", "#img-x svg", "svg .a", ".svg-icon", ".svg", "svgx",
                  "#a>svg", "svg,a", "[data-x] svg", "SVG", "#A>SVG", "Svg"]
        for sel in corpus:
            if "svg" in sel.lower().split():
                self.assertIsNotNone(
                    re.search(r"\bsvg\b", sel, re.I),
                    f"{sel!r} 命中了被删掉的第一条分支，留下的那条却没命中"
                    "——两条并不等价，删错了")
        for sel in ("svg", "#img-x svg", "svg .a", "#a>svg", "svg,a",
                    "SVG", "#A>SVG"):
            self.assertIsNone(_css_guard.transform_tween_kind(sel))

    def test_transform_declaration_has_one_regex(self):
        """`transform_rules` 里"这条声明是不是 transform"只认一份正则。"""
        with open(_css_guard.__file__,
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("^\\s*transform\\s*:", src)

    def test_cover_geometry_shares_one_guard(self):
        for degenerate in (((0, 0), (10, 10)), ((10, 10), (0, 0)),
                           (None, (10, 10)), ((10, 10), None),
                           ((-1, 10), (10, 10))):
            self.assertIsNone(_media_size.cover_scale(*degenerate))
            self.assertIsNone(_media_size.cover_crop_fraction(*degenerate))
        self.assertAlmostEqual(_media_size.cover_scale((100, 100), (50, 50)), 2.0)
        self.assertAlmostEqual(_media_size.cover_crop_fraction((100, 100), (50, 50)), 0.0)

    def test_margin_entry_point_is_single(self):
        self.assertFalse(hasattr(_check_svg, "_margin_pair"))
        self.assertEqual(set(_check_svg.safe_margins()),
                         {"portrait", "landscape"})


class ContractSinking(unittest.TestCase):
    """契约下沉：写错的那一刻脚本就要说清"该改成什么"，而不是只说"哪里错了"。"""

    def _stderr_of(self, fn, *a):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            fn(*a)
        return err.getvalue()

    def test_library_modules_refuse_to_run_as_scripts(self):
        """库模块被当脚本误传时必须 exit非零并说清正确入口。

        证伪：这两个模块没有 main()/argparse，直接运行是静默 exit 0、一个字都不
        打印、也不产出任何文件。调用方（人或agent）看到的是"没报错"，于是以为稿子
        过了——静默成功比报错更难查，这是全仓最危险的一种失败。

        stderr 合并进 stdout 再断言：Windows 上 capture_output 的 stderr 偶发是
        None（拿不到独立管道），分成两个流断言会在那边变成 TypeError 而不是
        一条清楚的失败。合并成一条流，两平台同一条判据。
        """
        for mod in ("build_from_structured.py", "html_renderer.py"):
            with self.subTest(mod=mod):
                p = subprocess.run(
                    [sys.executable, os.path.join(H.SCRIPTS_DIR, mod), "--help"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", timeout=60)
                out = p.stdout or ""
                self.assertNotEqual(p.returncode, 0, out)
                self.assertIn("[error]", out)
                self.assertIn("库模块", out)

    def test_agenda_overflow_warns_at_writing_time(self):
        """内容段数超 agenda 行预算时，写稿阶段就要 warn（不必等渲完配音）。

        证伪：截断只发生在渲染层，作者拿到 warn 时配音已渲完一条片子——贵到没人
        愿意回头改。阈值直接读模板 maxRows（不重抄一份），超了给"压段数或合并
        要点"的改法。
        """
        from _template import load_template
        max_rows = int(load_template()["layout"]["vertical"]["agenda"]["maxRows"])
        src = H.sample_source()
        src["segments"] = [
            {"id": "seg%d" % i, "title": "第%d段" % i, "text": "正文。"}
            for i in range(1, max_rows + 3)
        ]
        out = self._stderr_of(SRC.validate_segments_source, src)
        self.assertIn("agenda", out)
        self.assertIn(str(max_rows), out)
        self.assertIn("合并要点", out)

        quiet = self._stderr_of(SRC.validate_segments_source, H.sample_source())
        self.assertNotIn("agenda", quiet)

    def _mix_source(self, n_segments, n_canvas, **extra):
        """造一份 n_segments 个内容段、其中 n_canvas 段写 layout:"canvas" 的稿子。"""
        src = H.sample_source()
        src["segments"] = [
            {"id": "seg%d" % i, "title": "第%d段" % i, "text": "正文。",
             **({"layout": "canvas"} if i <= n_canvas else {})}
            for i in range(1, n_segments + 1)
        ]
        src.update(extra)
        return src

    def test_canvas_mix_warns_when_full_bleed_never_appears(self):
        """内容段够多却一段整页画布都没有时，写稿阶段就要提醒。

        证伪：版式 100% 由稿件侧决定（不写 layout = 槽位），代码里没有配平机制。
        不写 layout 又"最稳"，于是整页画布在成片里几乎不出场——那档版式形同虚设，
        而作者写完稿看不到这件事。这条提醒把出镜率变成写稿那一刻能看见的事实。
        """
        from _segments import CANVAS_MIN_SEGMENTS, CANVAS_TRIGGER_SEGMENTS
        out = self._stderr_of(SRC.validate_segments_source,
                              self._mix_source(CANVAS_TRIGGER_SEGMENTS, 0))
        self.assertIn("整页画布", out)
        self.assertIn(str(CANVAS_MIN_SEGMENTS), out)
        self.assertIn("layout:", out)   # 给出可操作的改法，不只是数落作者

    def test_canvas_mix_stays_quiet_on_a_legit_draft(self):
        """该安静的两种稿子必须安静：配额够了，或稿子本来就短。

        这条判据自己得会闭——否则它就是一条永远在叫的噪音，噪音的下场是被无视，
        等于没有判据。
        """
        from _segments import CANVAS_MIN_SEGMENTS, CANVAS_TRIGGER_SEGMENTS
        got = self._stderr_of(SRC.validate_segments_source,
                              self._mix_source(CANVAS_TRIGGER_SEGMENTS,
                                               CANVAS_MIN_SEGMENTS))
        self.assertNotIn("整页画布", got)
        short = self._stderr_of(SRC.validate_segments_source,
                                 self._mix_source(CANVAS_TRIGGER_SEGMENTS - 2, 0))
        self.assertNotIn("整页画布", short)

    def test_canvas_mix_counts_a_poster_opening_as_full_bleed(self):
        """开屏写成整页海报时，那一页就是整页画布——不该再提醒"你没写整页画布"。

        海报开屏的稿子拿到的若是"你没用整页画布"，是荒唐的提醒：它明明用了。
        """
        from _segments import CANVAS_MIN_SEGMENTS, CANVAS_TRIGGER_SEGMENTS
        src = self._mix_source(CANVAS_TRIGGER_SEGMENTS, 0)
        src["opening_layout"] = "canvas"
        out = self._stderr_of(SRC.validate_segments_source, src)
        self.assertNotIn("整页画布只有", out)


if __name__ == "__main__":
    unittest.main()
