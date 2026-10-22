"""本轮修的每一类"判据分家"，配一条**在修复前会红**的测试。

上一轮扫干净的是「常量字面量在两个模块各写一份」。同一类病往上长了一层：
**判据（函数）分家**——同一件事两份实现，而且注释往往白纸黑字写着"同一份
/同一口径"，于是后读代码的人连怀疑的理由都没有。下面每条都写明"修复前是
什么样"，锁的是那个错误行为不回来，不是锁某个数字。
"""
import ast
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: N999  把 scripts/ 放进 sys.path

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import _manifest_schema as MS  # noqa: E402
import _script_utils as SU  # noqa: E402
import _source_schema as SS  # noqa: E402
import _svg_sanitize as SZ  # noqa: E402
import _theme as TH  # noqa: E402
import check_svg as CS  # noqa: E402
import gen_hyperframes as GH  # noqa: E402

NS = "http://www.w3.org/2000/svg"

# manifest 的句子层是必填的（空列表直接拒），给一条最小合法句
_SENT = [{"index": 1, "text": "x", "start_time": 0.0, "duration": 1.0}]


def _source(name):
    with open(os.path.join(SCRIPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


def _namespace(name):
    """模块顶层能点到的名字：自己定义的 + import 进来的。

    "与 X 共用 sym" 说的是 X 也在用这个符号——它可能定义在第三个模块里、双方
    各自 import。只看 X 自己 def 了什么会把这种正当声明误判成谎话，而真正的
    谎话（点名一个对方既没定义也没导入的符号）照样抓得住。
    """
    tree = ast.parse(_source(name + '.py'))
    names = {n.name for n in tree.body if hasattr(n, 'name')}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name).split('.')[0] for a in node.names)
    return names


# ── 扫描器：注释自称"与 X 同一份/共用 sym"，sym 必须真在 X 里 ──────────
# 抓两种写法：`与 check_svg 同一份：_root_px_size`（裸符号）与
# `判据与 check_svg 共用 `_svg_sanitize.is_full_bleed_backdrop``（带模块前缀）。
_CLAIM = re.compile(
    r"(?:与|和)\s*([a-z_][a-z0-9_]*)\s*(?:的[^。\n；;]{0,6})?"
    r"(?:同一份|同一口径|共用|同一判据|同一实现|只有这一份|唯一真源|单一真源|同一来源)"
    r"\s*[：:（(，`]?\s*`?(_?[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)`?")


class VerdictClaimsMustResolve(unittest.TestCase):
    """自称"与某模块同一份"的注释，点名的符号必须在那个模块里真的存在。

    修复前：`gen_hyperframes._root_px_size` 的 docstring 写着
    "判据与 check_svg 同一份：_root_px_size"，而 `check_svg` 里压根没有这个
    名字（它叫 `_root_size`）。两份实现各写各的，gen 那份不校 width/height
    与 viewBox 的比例——同一张图两道门两个答案，注释还在担保。
    """

    def _claims(self):
        out = []
        for fn in sorted(os.listdir(SCRIPTS_DIR)):
            if not fn.endswith(".py"):
                continue
            src = _source(fn)
            for m in _CLAIM.finditer(src):
                peer, sym = m.group(1), m.group(2)
                if not os.path.exists(os.path.join(SCRIPTS_DIR, peer + '.py')):
                    continue
                parts = sym.split(".")
                mod = parts[-2] if len(parts) > 1 else peer
                if not os.path.exists(os.path.join(SCRIPTS_DIR, mod + ".py")):
                    continue
                line = src[:m.start()].count("\n") + 1
                out.append((fn, line, mod, parts[-1]))
        return out

    def test_every_named_symbol_exists_in_the_claimed_module(self):
        bad = [(f, ln, mod, sym) for f, ln, mod, sym in self._claims()
               if sym not in _namespace(mod)]
        self.assertEqual([], bad,
                         "注释自称与某模块共用这个符号，但那里根本没有它——"
                         "要么把判据真收成一份，要么把这句话改掉")

    def test_the_scanner_itself_sees_the_claims(self):
        """扫描器不能是空转：本轮修完后仍应扫到若干条真实声明。

        修复前那种"谎话真源"如果没人写，这条会红——那时要么删掉扫描器，
        要么说明注释里已经不再点名符号了。
        """
        self.assertGreaterEqual(len(self._claims()), 2,
                                "扫描器一条声明都没扫到，正则可能已经失配")

    def test_scanner_catches_a_planted_false_claim(self):
        """回归实测：把一句谎话植回源码，扫描器必须红。"""
        planted = ("    \"\"\"判据与 check_svg 同一份：_no_such_verdict。"
                   "\n    \"\"\"\n")
        src = _source("gen_hyperframes.py")
        self.assertNotIn("_no_such_verdict", src)
        try:
            with open(os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"), "w",
                      encoding="utf-8") as f:
                f.write(src.replace("def svg_background_warnings(", planted +
                                    "def svg_background_warnings("))
            bad = [(f, ln, m, s) for f, ln, m, s in self._claims()
                   if s not in _namespace(m)]
            self.assertTrue(any("_no_such_verdict" == s for _f, _l, _m, s in bad),
                            "植入的谎话没被扫出来")
        finally:
            with open(os.path.join(SCRIPTS_DIR, "gen_hyperframes.py"), "w",
                      encoding="utf-8") as f:
                f.write(src)


class RootSizeSingleSource(unittest.TestCase):
    """SVG 根标签的尺寸：固有尺寸与坐标系尺寸是**两回事**，各只有一份实现。

    修复前：`check_svg._root_size` 与 `gen_hyperframes._root_px_size` 各写一份，
    且两侧都把"固有像素尺寸"当成"viewBox 坐标系尺寸"喂给满幅底板判据。
    width="800" height="600" viewBox="0 0 400 300"（等比缩放，手写 SVG 最常见的
    写法）时，一张铺满整个 viewBox 的底板 rect 被判成只占一半——**两道门都一声
    不吭**，成片里就是一张铺满的死平底板（canvas.md 明令禁止的那种）。
    """

    SCALED = ('<svg xmlns="%s" width="800" height="600" viewBox="0 0 400 300">'
              '<rect x="0" y="0" width="400" height="300" fill="#101010"/></svg>' % NS)

    def test_both_gates_import_the_same_function(self):
        self.assertNotIn("def _root_size(", _source("check_svg.py"),
                         "check_svg 不许再自己写一份根尺寸判据")
        self.assertNotIn("def _root_px_size(", _source("gen_hyperframes.py"),
                         "gen_hyperframes 不许再自己写一份根尺寸判据")
        self.assertEqual(CS.root_px_size, SZ.root_px_size)
        self.assertEqual(GH.root_px_size, SZ.root_px_size)
        self.assertEqual(CS.root_viewbox_size, SZ.root_viewbox_size)

    def test_full_bleed_is_judged_in_viewbox_units_not_intrinsic_px(self):
        """同一张缩放写法的图，两道门必须都报"铺满幅底"。"""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.svg"), "w", encoding="utf-8") as f:
                f.write(self.SCALED)
            warns = GH.svg_background_warnings({"seg1": {"src": "a.svg"}},
                                               [{"id": "seg1"}], d)
            self.assertTrue(warns and "铺满" in warns[0],
                            "gen 侧漏报了铺满幅底：%s" % warns)
            errs, cwarns = CS.check_file(os.path.join(d, "a.svg"), "slot",
                                         "portrait", "dark")
            self.assertEqual([], errs)
            self.assertTrue(any("铺满" in w for w in cwarns),
                            "check_svg 侧漏报了铺满幅底：%s" % cwarns)

    def test_intrinsic_and_viewbox_are_two_different_numbers(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.SCALED)
        self.assertEqual(SZ.root_px_size(root)[:2], (800.0, 600.0))
        self.assertEqual(SZ.root_viewbox_size(root), (400.0, 300.0))

    def test_ratio_mismatch_note_reaches_the_generation_gate(self):
        """修复前：gen 那份只取尺寸不校比例，check_svg 报"比例不一致"、
        gen 一声不吭——同一张图两个答案。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.svg")
            with open(p, "w", encoding="utf-8") as f:
                f.write('<svg xmlns="%s" width="800" height="600" '
                        'viewBox="0 0 400 600"></svg>' % NS)
            w, h, note, vb_w, vb_h = GH._svg_intrinsic_size(p)
            self.assertEqual((w, h), (800.0, 600.0))
            self.assertEqual((vb_w, vb_h), (400.0, 600.0))
            self.assertTrue(note and "不一致" in note, note)
            errs, warns = GH.slot_svg_ratio_errors(
                {"seg1": {"src": "b.svg"}}, [{"id": "seg1"}], d, 980, 735)
            self.assertTrue(any("不一致" in x for x in warns),
                            "比例不一致没有出声：errs=%s warns=%s" % (errs, warns))


class MediaVerdictSingleSource(unittest.TestCase):
    """素材可用性（存在 / 越界）只有一份判据，`src` 与 `poster` 都归它判。

    修复前：`run.py._unusable` 与 `gen_hyperframes.validate_images_files` 各写
    一遍，run 那一份只查 `src` 不查 `poster`——覆盖率报告记"配图齐了"、
    `--until html` 放行，下一步 gen 才以"封面缺失" exit 1。而 run 的注释
    白纸黑字写着"与 validate_images_files 同一口径"。
    """

    def test_run_delegates_to_the_shared_verdict(self):
        run_src = _source("run.py")
        self.assertIn("media_verdict", run_src)
        self.assertNotIn("is_inside(", run_src,
                         "run.py 不许自己拼存在性+越界判据，走 media_verdict")
        self.assertNotIn("os.path.isfile", run_src.replace("os.path.isfile(", ""))

    def test_missing_poster_fails_in_both_gates(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "v.png"), "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            mapping = {"seg1": {"src": "v.png", "poster": "gone.png"}}
            # gen 侧
            missing, _corrupt = GH.validate_images_files(mapping, d)
            self.assertTrue(any("gone.png" in m[1] for m in missing),
                            "gen 侧没判封面缺失：%s" % missing)
            # run 侧：同一份判据，同一个答案
            self.assertEqual("missing", SU.media_verdict(d, "gone.png"))
            self.assertEqual("", SU.media_verdict(d, "v.png"))
            self.assertTrue(any(SU.media_verdict(d, mapping["seg1"][k])
                                for k in ("src", "poster")))

    def test_symlink_escape_is_escapes_not_missing(self):
        with tempfile.TemporaryDirectory() as d:
            outside = os.path.join(d, "outside.png")
            with open(outside, "wb") as f:
                f.write(b"\x00")
            proj = os.path.join(d, "proj")
            os.makedirs(proj)
            os.symlink(outside, os.path.join(proj, "escapee.png"))
            self.assertEqual("escapes", SU.media_verdict(proj, "escapee.png"))

    def test_filesystem_root_is_inside_itself(self):
        """parent 自带尾分隔符时祖先判据不能失效。

        修复前是 `startswith(parent_r + os.sep)`：parent=`/` 时拼出 `//`，
        任何 child 都不以它开头 → is_inside 一律 False。而 False 是**放行
        方向**（guard_not_in_skill_dir / media_verdict 都把它读成"没越界"），
        这条判据在根目录上整失效却不报错。
        """
        root = os.path.abspath(os.sep)
        self.assertTrue(SU.is_inside(os.path.join(root, "tmp"), root))
        self.assertTrue(SU.is_inside(root, root))
        # 相邻同名前缀不能算在里面（这是 startswith 家族的另一个经典坑）
        with tempfile.TemporaryDirectory() as d:
            parent = os.path.join(d, "proj")
            sibling = parent + "2"
            os.makedirs(parent)
            os.makedirs(sibling)
            self.assertTrue(SU.is_inside(os.path.join(parent, "a.png"), parent))
            self.assertFalse(SU.is_inside(os.path.join(sibling, "a.png"), parent))

    def test_unique_tmp_names_never_collide(self):
        """中转名唯一：并跑两个进程时，一方的异常清理不能删掉另一方的中转。

        修复前三处写盘各拼一个固定名（`<path>.tmp` / `<wav>.spd.tmp.wav` /
        `_concat_norm{i}.wav`），互踩后另一方的 `os.replace` 抛
        FileNotFoundError。
        """
        with tempfile.TemporaryDirectory() as d:
            names = {SU.unique_tmp(d, ".x.", ".tmp") for _ in range(50)}
            self.assertEqual(50, len(names))
            for n in names:
                self.assertTrue(os.path.isfile(n), "unique_tmp 要把文件建出来")
            # 必须落在目标目录里：跨文件系统 os.replace 会 EXDEV，原子性就断了
            self.assertEqual(d, os.path.dirname(next(iter(names))))
            # 后缀要保住（ffmpeg 按它推断容器格式）
            self.assertTrue(SU.unique_tmp(d, ".a.", ".wav").endswith(".wav"))

    @unittest.skipIf(os.name == "nt",
                     "umask 是 POSIX 语义：Windows 的 st_mode 恒合成 0o666（可写）、"
                     "os.chmod 也只读只读位，没有 0o666 & ~umask 可断言")
    def test_unique_tmp_inherits_the_umask_not_mkstemps_0600(self):
        """中转文件 os.replace 成成品后，成品不能带着 mkstemp 的 0600。

        修复前 index.html / timing_manifest / wav 一律 0600：换台机器、换个
        用户读产物就撞权限（HTML 要被浏览器读、wav 要被别的账号下的 ffmpeg 读）。
        mkstemp 之后按 0o666 & ~umask 补位——与 open() 新建文件同一个答案。
        Windows 没有这个语义（见 skip 理由），契约在那里无从断言。
        """
        with tempfile.TemporaryDirectory() as d:
            old = os.umask(0o022)
            try:
                tmp = SU.unique_tmp(d, ".p.", ".tmp")
            finally:
                os.umask(old)
            self.assertEqual(0o644, os.stat(tmp).st_mode & 0o777)

    def test_deeply_nested_json_is_a_valueerror_not_a_bare_recursionerror(self):
        """十万层嵌套的 JSON：json.load 抛 RecursionError，不是 ValueError 子类。

        修复前 read_json_file 只转 OSError/UnicodeDecodeError/JSONDecodeError，
        病态嵌套直接裸栈——而调用方（三个 schema 的入口）全是按 ValueError 接的，
        接不住的就是一段解释器栈而不是"这份 JSON 坏了"。

        向量分两路：真实深嵌套在 3.13 及以前真的触发 RecursionError 路径（那时
        这条向量同时是证伪）；3.14 起 json 的 C 扫描器改成迭代式，同样输入直接
        解析成功——触发不了就用 mock 把 RecursionError 直接灌进 json.load，
        钉住的是转换契约本身（该except 在 3.9–3.13 的 CI 矩阵上是活路径）。
        """
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "deep.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("[" * 100000 + "]" * 100000)
            try:
                SU.read_json_file(p)
            except ValueError:
                return                 # 真触发了 RecursionError 且已被转成人话
            # 解析器放行了这份输入（3.14 起 C 扫描器迭代式）：没有真路径可走，
            # 用 mock 把 RecursionError 直接灌进 json.load，钉住转换契约本身。
            with mock.patch.object(SU.json, "load",
                                   side_effect=RecursionError("maximum recursion "
                                                              "depth exceeded")):
                with self.assertRaises(ValueError) as cm:
                    SU.read_json_file(p)
            self.assertIn("嵌套", str(cm.exception))


class OnScreenTextContractShared(unittest.TestCase):
    """「上墙一行不收分号」这条约束，写稿入口与手写 manifest 入口同一份。

    修复前：`_source_schema` 对 title/tagline/takeaway/cta 走
    `_validate_display_text`，`_manifest_schema` 连这个函数都没导入——
    manifest 是文档明写"确需手写可按格式提供"的接口，正是手写最容易撞的地方。
    同一句带分号的标题：写稿时被拒，写进 manifest 就静默上墙，而 agenda 行宽
    是死的，第二件事在成片里被 nameTrim 悄悄裁掉。
    """

    def test_both_entrypoints_share_the_field_list(self):
        self.assertEqual(SS.ON_SCREEN_SEGMENT_KEYS, MS.ON_SCREEN_SEGMENT_KEYS)
        self.assertNotIn("ON_SCREEN_SEGMENT_KEYS = ", _source("_manifest_schema.py"),
                         "manifest 侧不许自己列一份上墙字段清单")

    def test_semicolon_rejected_in_both_entrypoints(self):
        src_seg = {"id": "seg1", "title": "甲；乙", "text": "口播稿不受这条约束"}
        with self.assertRaises(ValueError) as src_ctx:
            SS.validate_segments_source({"segments": [src_seg]})
        self.assertIn("分号", str(src_ctx.exception))
        # manifest 侧：title 走同一条约束
        with self.assertRaises(ValueError) as man_ctx:
            MS.validate_timing_manifest({"schema_version": 2, "status": "ok",
                                         "sentences": _SENT, "total_duration": 1.0,
                                         "segments": [{"id": "seg1",
                                                       "title": "甲；乙"}]})
        self.assertIn("分号", str(man_ctx.exception),
                      "手写 manifest 的上墙文字漏了这条约束")
        # 顶层 closing_cta 与 source 侧的 cta 同一条
        with self.assertRaises(ValueError) as cta_ctx:
            MS.validate_timing_manifest({"schema_version": 2, "status": "ok",
                                         "sentences": _SENT, "total_duration": 1.0,
                                         "closing_cta": "甲；乙"})
        self.assertIn("分号", str(cta_ctx.exception))


class CssColorTableSelfConsistent(unittest.TestCase):
    """色名表：`css_color_to_hex` 是对比度判据的地基，写错一个通道不报错。

    修复前：`aliceblue` 记成 f0fff8（绿蓝通道写反，应为 f0f8ff）、`oldlace`
    记成 fdf5e0（应为 fdf5e6）。它直接进 `ensure_text_contrast`：按错色算出来
    的对比度是假的，"够不够对比度"的判定跟着错，且全程无 warn。
    """

    def test_aliases_are_the_same_value(self):
        pairs = dict(re.findall(r"([a-z]+)-([0-9a-f]{6})", TH._CSS3_NAMES_SRC))
        for a, b in (("aqua", "cyan"), ("fuchsia", "magenta"), ("gray", "grey"),
                     ("darkgray", "darkgrey"), ("darkslategray", "darkslategrey"),
                     ("dimgray", "dimgrey"), ("lightgray", "lightgrey"),
                     ("lightslategray", "lightslategrey"),
                     ("slategray", "slategrey")):
            self.assertEqual(pairs[a], pairs[b], f"{a} 与 {b} 必须同值")

    def test_anchor_values_match_the_css3_spec(self):
        """锚点取"写错了一个通道就露馅"的那几个，不是随便挑的。"""
        for name, want in (("aliceblue", "#f0f8ff"), ("oldlace", "#fdf5e6"),
                           ("white", "#ffffff"), ("black", "#000000"),
                           ("rebeccapurple", "#663399"),
                           ("goldenrod", "#daa520")):
            self.assertEqual(want, TH.css_color_to_hex(name))


if __name__ == "__main__":
    unittest.main()
