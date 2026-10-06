"""门禁：remotion/src/theme.generated.ts 必须是 Python 视觉真源的机械投影。

**本文件取代的是 `test_theme_parity.py`（341 行）。** 那份测试的假设是"TS 侧是
手工副本，靠逐字段对拍盯着别漂"。假设已经不成立了——TS 常量由
`scripts/gen_theme_ts.py` 从 `_template.py` + `_theme.py` 生成，抄写这件事不再
由人做，就不需要 341 行去证明它没抄错。

从"比对两个独立维护的实体"退到"验证一次生成的产物"：重跑一遍生成器，把结果
与仓库里的文件逐字节比。这条断言比原来那 20 条对拍**更强**——原来
`test_every_segment_field_matches` 只比数值，`test_landscape_only_matches_template`
只比那三块，而"生成物里混进了一段手写的东西"这类漂移比值比不出来。

保留下来的另外两类检查，它们与"是否双写"无关，原本混在 parity 里只是搭车：

- **组件纪律**：`Verse.tsx` 不许把 `verse.activeRule` 写死成字面量。这是本轮
  review 实际抓到的静默失效（模板两画幅都是 0.12，硬编码看不出来，改模板就哑）。
- **生成物不得回潮**：ease 字符串与 segmentWipe 曾是"改了不影响画面"的死字段
  （组件调的是 `easing.ts` 的具名导出），生成器已不再产出它们；这里钉住。
"""
import os
import re
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import gen_theme_ts  # noqa: E402

GENERATED = os.path.join(ROOT, "remotion", "src", "theme.generated.ts")
THEME_TS = os.path.join(ROOT, "remotion", "src", "theme.ts")
VERSE_TSX = os.path.join(ROOT, "remotion", "src", "components", "Verse.tsx")


class GeneratedFileIsUpToDate(unittest.TestCase):
    """仓库里的生成物必须就是当前真源生成出来的那份。"""

    def test_committed_file_matches_generator_output(self):
        self.assertTrue(os.path.isfile(GENERATED),
                        f"生成物不存在：{GENERATED}；"
                        f"跑 python scripts/gen_theme_ts.py")
        with open(GENERATED, encoding="utf-8") as f:
            committed = f.read()
        fresh = gen_theme_ts.build_ts()
        if committed != fresh:
            self.fail(_first_diff(committed, fresh))

    def test_check_mode_agrees_with_build(self):
        """`--check` 这条 CLI 路径自己也要能用：它跑在 hook / CI 里，
        build_ts() 绿不代表 --check 的分支没写错。"""
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "gen_theme_ts.py"), "--check"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(proc.returncode, 0,
                         f"--check 退出码 {proc.returncode}：\n{proc.stderr}")

    def test_generation_is_deterministic(self):
        """同样输入必得同样字节——否则门禁会在两次连跑之间自己变红。"""
        self.assertEqual(gen_theme_ts.build_ts(), gen_theme_ts.build_ts())

    def test_output_is_valid_typescript_object_literals(self):
        """生成物必须能被 TS 当模块解析：7 个 `export const X = ...;`，
        且没有 JSON 残留的引号键（`"foo":`）——键要还原成裸标识符才对。"""
        with open(GENERATED, encoding="utf-8") as f:
            src = f.read()
        # 值可能是对象（{）也可能是字符串（字体栈），两种都算常量
        names = re.findall(r"^export const ([A-Z_]+) = (?:\{|\")", src, re.M)
        self.assertEqual(
            sorted(names),
            sorted(["THEME", "TYPO", "FONT_STACK", "MONO_STACK", "LAYOUTS",
                    "LANDSCAPE_ONLY", "ANIM"]),
            f"导出常量对不上：{names}")
        self.assertNotRegex(src, r'^\s*"[A-Za-z_$][\w$]*":',
                            "生成物里还有 JSON 风格的引号键——键位还原失效了")
        self.assertNotRegex(src, r"\bnull\b|\btrue\b|\bfalse\b",
                            "生成物里出现了 JSON 字面量（null/true/false）")


class DeadFieldsStayGone(unittest.TestCase):
    """「改了不会有任何画面变化」的字段不许回潮。

    判据不是"组件没读"，而是"改它画面不变"：组件调的是 `easing.ts` 的具名导出，
    ANIM 里那份 `ease: "power2.out"` 字符串副本没有任何读取点，改了等于没改——
    两处都以为自己在管，不如一处都没有。
    """

    def test_anim_carries_no_ease_strings(self):
        with open(GENERATED, encoding="utf-8") as f:
            src = f.read()
        found = re.findall(r'\bease(?:Str)?:\s*"', src)
        self.assertEqual(found, [],
                         f"ANIM 里又有 ease 字符串副本 {len(found)} 处——组件读的是 "
                         f"easing.ts 的具名导出，这些改了不影响画面")

    def test_anim_has_no_segment_wipe_or_director(self):
        """渲染端只实现了 line 档（生成期 _line_only_guard 挡住其它档），
        director 是生成期烘进 SVG 的值。留副本就是与模板分家的温床。"""
        with open(GENERATED, encoding="utf-8") as f:
            src = f.read()
        for key in ("segmentWipe", "director"):
            self.assertNotIn(key, src,
                             f"生成物里出现了 {key}——渲染端不读它，留着只会分家")

    def test_theme_ts_does_not_reintroduce_copies(self):
        """theme.ts 只留类型与颜色数学；任何数据常量重新手抄回来就是双写复活。

        正则只认 `export const X = {`：重导出的 `export {ANIM, ...}` 与
        `export type ...` 不匹配，`export const LAYOUTS = LAYOUTS_RAW` 的值是
        标识符也不是 `{`——所以不需要先把 re-export 行剔出去（早先剔了，反而把
        真正要抓的 `export const TYPO = {` 一起吃掉了，门禁成了摆设）。
        """
        with open(THEME_TS, encoding="utf-8") as f:
            src = f.read()
        copied = re.findall(r"^export const ([A-Z_]+)[^=]*= \{", src, re.M)
        self.assertEqual(copied, [],
                         f"theme.ts 里又手抄了数据常量 {copied}——数值只该在 "
                         f"theme.generated.ts 里")


class ComponentDiscipline(unittest.TestCase):
    """组件侧的纪律：可调参数不许写死成字面量。"""

    # 注释里可以随便提 activeRule 与 0.12em（那段注释本来就在解释这段代码），
    # 所以先剥掉注释再判断——旧门禁的 assertRegex(src, "activeRule") 会被注释
    # 里的同名词满足，等于没断言"真的读了字段"。
    _COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

    def test_active_rule_is_not_hardcoded_in_verse(self):
        """当前句高亮规则厚度必须读 v.activeRule。

        这条单列出来：它是本轮 review 实际抓到的分家（模板两画幅都定义 0.12，
        Verse.tsx 曾硬编码 "0.12em"）。当前两画幅同值所以看不出画面差异，但改模板
        不动组件就是一次静默失效。
        """
        with open(VERSE_TSX, encoding="utf-8") as f:
            raw = f.read()
        code = self._COMMENTS.sub("", raw)
        # 厚度是唯一以 em 为单位的 height（版式里的 height 都是 px 数值，如
        # `height: winH`），所以锚在 "em" 上就不会抓错行。
        thick = re.search(r"height:\s*([^,\n]*em[^,\n]*),", code)
        self.assertIsNotNone(thick, "Verse.tsx 里找不到以 em 为单位的高亮规则高度")
        self.assertIn("activeRule", thick.group(1),
                      f"Verse.tsx 的高亮规则厚度写成了字面量 {thick.group(1)!r}"
                      f"——改模板的 verse.activeRule 不会生效。应当读 v.activeRule。")


def _first_diff(committed, fresh):
    """逐行比出第一处不同，并说清是哪一侧的什么。"""
    a = committed.splitlines()
    b = fresh.splitlines()
    for i in range(max(len(a), len(b))):
        la = a[i] if i < len(a) else "<文件到此结束>"
        lb = b[i] if i < len(b) else "<文件到此结束>"
        if la != lb:
            return (f"{os.path.relpath(GENERATED, ROOT)} 与真源分家了"
                    f"（第 {i + 1} 行）：\n"
                    f"  仓库里的：{la}\n"
                    f"  现在生成的：{lb}\n"
                    f"改了 _template.py / _theme.py 之后要跑：\n"
                    f"  python scripts/gen_theme_ts.py")
    return "内容相同但长度不同（不可能，防御性）"


if __name__ == "__main__":
    unittest.main()