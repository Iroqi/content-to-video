"""SKILL.md「最小可跑路径」的示例必须真的能跑。

为什么单独立一条：这份 JSON 是**新用户/新模型看到的第一个东西**，它一坏，
"先跑通再看规则"这条入口就整段失效——而它坏起来毫无声响（JSON 仍然合法、
字段名只是拼错一个字母）。文档示例退化成"看起来对"的那天，读者要自己
再撞一遍当初试错撞出来的坑。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import _helpers as H

SKILL_MD = os.path.join(H.SKILL_DIR, "SKILL.md")


def _minimal_source_block():
    with open(SKILL_MD, encoding="utf-8") as f:
        doc = f.read()
    # 第一节（最小可跑路径）里的第一个 json 代码块就是它
    head = doc.split("## 这份文档怎么用")[0]
    m = re.search(r"```json\n(.*?)```", head, re.S)
    return m.group(1) if m else None


class MinimalPathExample(unittest.TestCase):
    def test_block_exists(self):
        self.assertIsNotNone(
            _minimal_source_block(),
            "SKILL.md 顶部「最小可跑路径」里找不到 json 代码块——"
            "那份稿件是新用户唯一的入口示例，不能删")

    def test_block_is_valid_json(self):
        json.loads(_minimal_source_block())

    def test_only_title_and_text_are_required(self):
        """示例自称"只有 title 与 text 是硬要求"：那就不能偷偷依赖别的字段。

        反过来钉：把示例裁到只剩 title/text 也必须过契约层。示例里多写的
        字段是"可选字段怎么用"的示范，不是"必须写"的暗示。
        """
        data = json.loads(_minimal_source_block())
        minimal = {"title": data["title"],
                   "segments": [{"title": s["title"], "text": s["text"]}
                                for s in data["segments"]]}
        sys.path.insert(0, os.path.join(H.SKILL_DIR, "scripts"))
        from _source_schema import load_segments_source
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(minimal, f, ensure_ascii=False)
            path = f.name
        try:
            load_segments_source(path)
        finally:
            os.unlink(path)

    def test_block_dry_runs_through_pipeline(self):
        """端到端：示例 + --dry-run 必须走完分句与段检测（不联网、不写文件）。"""
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "segments_source.json")
            with open(src, "w", encoding="utf-8") as f:
                f.write(_minimal_source_block())
            r = subprocess.run(
                [sys.executable, os.path.join(H.SCRIPTS_DIR, "run.py"),
                 "--source", src, "-o", os.path.join(td, "out"), "--dry-run"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120,
                env=dict(os.environ, MIMO_API_KEY="dust-dry-run"))
        self.assertEqual(r.returncode, 0,
                         "SKILL.md 的最小示例跑不通：\n" + (r.stderr or r.stdout))
        self.assertIn("dry-run", r.stdout)


if __name__ == "__main__":
    unittest.main()
