"""双源对拍：scripts/_template.py + _theme.py（Python 视觉真源）↔ remotion/src/theme.ts（TS 副本）。

**为什么需要这个测试**：Remotion 组件读不到 CSS 变量，所以 Python 模板的版式
数值在 `theme.ts` 里**手工复刻**了一份。这份复刻会悄悄分家：模板改了、TS 没改，
两边各自自洽、不报任何错，出片时才发现字号/间距不对。本轮 review 就抓到一处
既有的分家——`verse.activeRule`（当前句高亮规则厚度）在模板里两画幅都定义了
0.12，Verse.tsx 却把它硬编码成字符串 `"0.12em"`，字段名在 TS 侧根本不存在。

**对拍口径**：不逐字段要求结构相同（两侧的组织方式本就不同：Python 把横屏专属
的 textCol/margin 平铺在 layout.landscape 下，TS 收进 LANDSCAPE_ONLY），而是
**逐个验证有真源关系的具体数值相等**：

- LAYOUTS.<画幅>.<段> 里两侧同名同义的字段（verse/title/tagline/image/
  subtitle/progressBar/grid/ambience/agenda）必须逐项相等；
- image.top 与 Python 的 image.bottomGapToVerse 是同一几何的两种表达
  （top = 画幅高 − verse.bottom − verse.windowHeight − image.height −
  bottomGapToVerse），单独验算，不要求键名相同；
- LANDSCAPE_ONLY 的 textCol/margin/titleGuards 必须等于 Python 的
  layout.landscape.textCol / margin / title.guard*四元组；
- THEME 的颜色与 TYPO 的字重/行高对 _theme.py 的同名真源。

Node 不可用时退化为"从 TS 源码抠字面量"（见 _ts_theme_fallback），仍能挡住
改数值忘同步；Node 在时走运行时对象，额外能挡住"字段名对不上"。
"""
import json
import os
import re
import shutil
import subprocess
import unittest

import _helpers  # noqa: F401  —— 把 scripts/ 挂进 sys.path
from _template import get_canvas, load_template

REMOTION_SRC = os.path.join(_helpers.SKILL_DIR, "remotion", "src")
THEME_TS = os.path.join(REMOTION_SRC, "theme.ts")

# LAYOUTS 里两侧同名同义的段。segCard / columnGap 不在其中：
#   - segCard.padding 只作用于 HTML 版的卡片外壳，Remotion 的 Card 用 clip-path
#     揭幕，没有 padding 概念（见 components/Card.tsx）；
#   - columnGap 是冗余键：96+613+48+1067+96 == 1920 恰好等于画幅宽，几何已被
#     margin/textCol.width/image.width 唯一确定，单独验算（见 test_column_gap_*）。
SEGMENTS = ("verse", "title", "tagline", "image", "subtitle",
            "progressBar", "grid", "ambience", "agenda")

# title.maxLines / title.top：横屏模板没有（横屏标题不设行数上限、位置由
# 左文字栏 flex 居中决定），TS 用 0 / 0 占位。0 = "不限"，与模板的"不设限"同义。
HORIZONTAL_ONLY_ZERO = {("landscape", "title", "maxLines"),
                        ("landscape", "title", "top"),
                        ("landscape", "verse", "bottom"),
                        ("landscape", "image", "top"),
                        ("landscape", "image", "marginSide")}


def _node():
    return shutil.which("node")


def _py_layout():
    return load_template()["layout"]


def _ts_theme_runtime():
    """在 Node 里 import theme.ts，返回其顶层导出（真实运行时对象）。"""
    url = "file://" + THEME_TS.replace("\\", "/")
    script = (
        f"const M = await import({json.dumps(url)});\n"
        "process.stdout.write(JSON.stringify({"
        "LAYOUTS: M.LAYOUTS, LANDSCAPE_ONLY: M.LANDSCAPE_ONLY, "
        "FONT_STACK: M.FONT_STACK, MONO_STACK: M.MONO_STACK, "
        "ANIM: M.ANIM, THEME: M.THEME, TYPO: M.TYPO}));\n"
    )
    proc = subprocess.run([_node(), "--experimental-strip-types", "-e", script],
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"node 执行 theme.ts 失败:\n{proc.stderr.strip()[:2000]}")
    return json.loads(proc.stdout)


_NUM = r"(-?\d+(?:\.\d+)?)"


def _ts_theme_fallback():
    """Node 不可用时从 TS 源码抠字面量：只够验数值，够挡住"改一边忘改另一边"。"""
    with open(THEME_TS, encoding="utf-8") as f:
        src = f.read()

    def obj_after(marker):
        i = src.index(marker)
        j = src.index("\n};", i)
        return src[i:j]

    def fields(body):
        out = {}
        for seg in re.finditer(r"(\w+):\s*\{([^{}]*)\}", body):
            d = {}
            for kv in re.finditer(r"(\w+):\s*" + _NUM, seg.group(2)):
                d[kv.group(1)] = float(kv.group(2))
            out[seg.group(1)] = d
        return out

    layouts = {}
    lay_src = obj_after("export const LAYOUTS")
    for m in re.finditer(r"(vertical|landscape):\s*\{", lay_src):
        start = lay_src.index("{", m.start())
        depth, j = 0, start
        while j < len(lay_src):
            if lay_src[j] == "{":
                depth += 1
            elif lay_src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        layouts[m.group(1)] = fields(lay_src[start:j + 1])

    lo_src = obj_after("export const LANDSCAPE_ONLY")
    lo = {}
    m = re.search(r"textCol:\s*\{([^}]*)\}", lo_src)
    if m:
        lo["textCol"] = {k: float(v) for k, v in
                         re.findall(r"(\w+):\s*" + _NUM, m.group(1))}
    m = re.search(r"margin:\s*" + _NUM, lo_src)
    if m:
        lo["margin"] = float(m.group(1))
    lo["titleGuards"] = [{"chars": float(c), "size": float(s)} for c, s in
                         re.findall(r"chars:\s*" + _NUM + r",\s*size:\s*" + _NUM,
                                    lo_src)]
    return {"LAYOUTS": layouts, "LANDSCAPE_ONLY": lo}


def _num_eq(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol


class LayoutParity(unittest.TestCase):
    """布局数值：Python 模板与 theme.ts 必须一致。"""

    def setUp(self):
        self.py = _py_layout()
        self.ts = (_ts_theme_runtime() if _node() else _ts_theme_fallback())

    def test_every_segment_field_matches(self):
        """同名段里两侧都有的字段必须逐项相等。"""
        bad = []
        for aspect in ("vertical", "landscape"):
            for seg in SEGMENTS:
                p, t = self.py[aspect].get(seg, {}), self.ts["LAYOUTS"][aspect].get(seg, {})
                for key in sorted(set(p) & set(t)):
                    if (aspect, seg, key) in HORIZONTAL_ONLY_ZERO:
                        continue
                    if isinstance(p[key], (int, float)) and _num_eq(p[key], t[key]):
                        continue
                    if p[key] == t[key]:
                        continue
                    bad.append(f"layout.{aspect}.{seg}.{key}: "
                               f"py={p[key]!r} ts={t[key]!r}")
        self.assertEqual(bad, [], "Python 模板与 theme.ts 分家了：\n  " + "\n  ".join(bad))

    def test_no_segment_missing_on_ts_side(self):
        """TS 侧不能少一整个段——那说明组件要用的参数在真源里被删了/改名了。"""
        missing = [f"layout.{a}.{s}" for a in ("vertical", "landscape")
                   for s in SEGMENTS if s not in self.ts["LAYOUTS"][a]]
        self.assertEqual(missing, [], f"theme.ts 缺这些段：{missing}")

    def test_vertical_image_top_equals_bottom_gap_geometry(self):
        """TS 的 image.top 与 Python 的 image.bottomGapToVerse 是同一几何的两种表达。

        Python 从下往上排（图像底到 verse 顶留 bottomGapToVerse），TS 从上往下给
        top。两者必须算出同一个数，否则图像与句流之间会出现意料外的空隙或重叠。
        """
        lay = self.py["vertical"]
        _, canvas_h = get_canvas("vertical")
        expect = (canvas_h - lay["verse"]["bottom"] - lay["verse"]["windowHeight"]
                  - lay["image"]["height"] - lay["image"]["bottomGapToVerse"])
        got = self.ts["LAYOUTS"]["vertical"]["image"]["top"]
        self.assertTrue(_num_eq(got, expect),
                        f"竖屏 image.top={got} 与按 Python 几何算出的 {expect} 不等")

    def test_column_gap_is_consistent_not_duplicated(self):
        """横屏 columnGap 是冗余键：几何已被 margin/textCol/image 唯一确定。

        要求等式成立：margin*2 + textCol.width + columnGap + image.width == 画幅宽。
        不成立说明 Python 模板自己内部矛盾，TS 抄哪边都会错。
        """
        lay = self.py["landscape"]
        canvas_w, _ = get_canvas("landscape")
        total = (lay["margin"] * 2 + lay["textCol"]["width"]
                 + lay["columnGap"] + lay["image"]["width"])
        self.assertTrue(_num_eq(total, canvas_w),
                        f"横屏 2×margin + textCol.width + columnGap + image.width "
                        f"= {total}，与画幅宽 {canvas_w} 不等——Python 模板自身矛盾")

    def test_landscape_only_matches_template(self):
        """LANDSCAPE_ONLY 三个字段必须等于模板里对应的横屏专属键。"""
        lay = self.py["landscape"]
        lo = self.ts["LANDSCAPE_ONLY"]
        self.assertEqual(
            {k: float(v) for k, v in lo["textCol"].items()},
            {k: float(v) for k, v in lay["textCol"].items()},
            "LANDSCAPE_ONLY.textCol 与模板 layout.landscape.textCol 分家了")
        self.assertTrue(_num_eq(lo["margin"], lay["margin"]),
                        f"LANDSCAPE_ONLY.margin={lo['margin']} ≠ 模板 {lay['margin']}")
        self.assertEqual(
            [{"chars": float(g["chars"]), "size": float(g["size"])} for g in lo["titleGuards"]],
            [{"chars": float(lay["title"]["guardChars1"]), "size": float(lay["title"]["guardSize1"])},
             {"chars": float(lay["title"]["guardChars2"]), "size": float(lay["title"]["guardSize2"])}],
            "LANDSCAPE_ONLY.titleGuards 与模板 title.guard* 四元组分家了")

    def test_active_rule_is_not_hardcoded_in_verse(self):
        """当前句高亮规则厚度必须读 v.activeRule，不能在组件里写死字面量。

        这条单列出来：它是本轮review 实际抓到的分家（模板两画幅都定义 0.12，
        Verse.tsx 硬编码 "0.12em"，TS 侧连字段都没有）。当前两画幅同值所以看不出
        画面差异，但改模板不动 TS 就是一次静默失效——与幂次表分家同一类错误。
        """
        verse_src = os.path.join(REMOTION_SRC, "components", "Verse.tsx")
        with open(verse_src, encoding="utf-8") as f:
            src = f.read()
        self.assertNotRegex(
            src, r'height:\s*["\']0?\.\d+em["\']',
            "Verse.tsx 把当前句高亮规则厚度写死了——改模板的 verse.activeRule "
            "不会生效。应当读 v.activeRule。")
        self.assertRegex(src, r"activeRule", "Verse.tsx 应读 v.activeRule")

    def test_verse_active_rule_present_on_both_aspects(self):
        lay = self.py["vertical"]["verse"]
        for aspect in ("vertical", "landscape"):
            self.assertIn("activeRule", lay,
                          f"模板 layout.{aspect}.verse 缺 activeRule")
            self.assertIn("activeRule", self.ts["LAYOUTS"][aspect]["verse"],
                          f"theme.ts 的 {aspect}.verse 缺 activeRule")


class AnimParity(unittest.TestCase):
    """动画参数：_template.py 的 animation 块 ↔ theme.ts 的 ANIM。

    结构差异（对拍口径，理由写在每条后面）：
    - TS 的 `apple` 扁平自 Python 的 `opening.apple`（同一份参数，只少一层包装）；
    - `imageEntrance.vertY` 对 `vert_y`（TS 侧统一 camelCase）；
    - Python 的 `segmentWipe` 在 TS 侧**故意没有**：渲染端只实现了 line 档，
      非 line 档由生成期 `_line_only_guard` 在写盘前挡住（见 gen_remotion_project.py），
      留一份没人读的副本才是分家温床；
    - Python 的 `director`（steps 的缺省 duration/ease）在 TS 侧没有：它是**生成期**
      的值，由 canvas_kit 烘进 SVG 属性，渲染端从 DOM 读到已烘好的结果；
    - TS 的 `canvasAmbience`（canvas 档第三组氛围光）Python 侧无对应键，是渲染端
      专属观感值，只此一处使用（Card.tsx），无真源可对拍故不纳入。
    """

    # TS 键 → Python 键（只列需要改名的；None = Python 侧无对应真源，不对拍）
    MAP = {
        "titleEntrance": "titleEntrance",
        "entranceBudget": "entranceBudget",
        "imageEntrance": "imageEntrance",
        "propLine": "propLine",
        "apple": ("opening", "apple"),
    }

    def setUp(self):
        if _node() is None:
            self.skipTest("需要 node")
        self.py = load_template()["animation"]
        self.ts = _ts_theme_runtime()["ANIM"]

    def test_anim_fields_match(self):
        bad = []
        for ts_key, py_key in self.MAP.items():
            p = self.py
            for part in (py_key if isinstance(py_key, tuple) else (py_key,)):
                p = p[part]
            t = self.ts[ts_key]
            # 键名风格差异：Python vert_y → TS vertY。以 Python 键为准去 TS 侧找。
            rename = {"vert_y": "vertY"} if ts_key == "imageEntrance" else {}
            for py_field in sorted(p):
                ts_field = rename.get(py_field, py_field)
                a, b = p[py_field], t.get(ts_field)
                same = _num_eq(a, b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a == b
                if not same:
                    bad.append(f"animation.{ts_key}.{py_field}: py={a!r} ts={b!r}")
        self.assertEqual(bad, [], "animation 与 ANIM 分家了：\n  " + "\n  ".join(bad))

    def test_segment_wipe_stays_absent_on_ts_side(self):
        """TS 侧不得出现 segmentWipe 副本：只实现了 line 档，留着就是分家温床。"""
        self.assertNotIn("segmentWipe", self.ts,
                         "theme.ts 出现了 ANIM.segmentWipe——渲染端只实现 line 档，"
                         "这组参数（含无人读的 ease）留副本会与模板分家")

    def test_all_anim_keys_accounted_for(self):
        """ANIM 的每个顶层键必须在 MAP 或 ALLOW_TS 里登记——新加参数不许漏对拍。"""
        allow_ts = {"canvasAmbience"}  # 渲染端专属观感值，见类 docstring
        unknown = sorted(set(self.ts) - set(self.MAP) - allow_ts)
        self.assertEqual(unknown, [],
                         f"ANIM 新增了这些键但门禁不知道它们的对拍口径：{unknown}；"
                         f"请在 MAP（对模板）或 ALLOW_TS（渲染端专属）里登记")


class ThemeParity(unittest.TestCase):
    """颜色与排版常量：_theme.py / _template.py 的 typography 块 ↔ theme.ts。"""

    def setUp(self):
        if _node() is None:
            self.skipTest("需要 node")
        from _theme import get_theme_colors, theme_bg_stops
        self.ts = _ts_theme_runtime()
        self.colors = get_theme_colors("dark")
        self.stops = theme_bg_stops("dark")
        self.typo = load_template()["typography"]

    def test_text_and_grid_colors_match(self):
        self.assertEqual(self.ts["THEME"]["textColor"].lower(),
                         self.colors["text_color"].lower(),
                         "正文色分家了（THEME.textColor vs _theme 的 text_color）")
        self.assertEqual(self.ts["THEME"]["gridColor"], self.colors["grid_color"],
                         "网格色分家了（THEME.gridColor vs _theme 的 grid_color）")

    def test_bg_gradient_matches(self):
        self.assertEqual(self.ts["THEME"]["bgGradient"], self.colors["bg_gradient"],
                         "背景渐变分家了（THEME.bgGradient vs _theme 的 bg_gradient）")

    def test_bg_gradient_stops_match(self):
        """色标必须与背景渐变里抠出来的完全一致——对比度保底按最坏一档算
        （ensureTextContrast），少一个色标就会漏掉那块底色。"""
        self.assertEqual([s.lower() for s in self.ts["THEME"]["bgStops"]],
                         [s.lower() for s in self.stops],
                         "背景色标分家了；ensureTextContrast 会漏算某块底色")

    def test_font_stacks_match(self):
        self.assertEqual(self.ts["FONT_STACK"], self.typo["fontFamily"],
                         "正文字体栈分家了")
        self.assertEqual(self.ts["MONO_STACK"], self.typo["monoStack"],
                         "等宽字体栈分家了")

    def test_typography_constants_match(self):
        for key in ("titleWeight", "taglineWeight", "titleLineHeight", "titleTracking"):
            py, ts = self.typo[key], self.ts["TYPO"][key]
            same = _num_eq(py, ts) if isinstance(py, (int, float)) else py == ts
            self.assertTrue(same, f"TYPO.{key}={ts!r} ≠模板 typography 的 {py!r}")


if __name__ == "__main__":
    unittest.main()
