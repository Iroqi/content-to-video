"""生成期只提示不拦的守卫：找不到就出声，但绝不能对"其实能找到"的情况闭嘴。

这一组守的是**假守卫**——提示函数在一种常见布局下找不到文件却不出声，作者据此
以为路径没问题，直到成片出来才发现。GSAP 那一道尤其贵：GSAP 404 不会让生成失败，
页面里 `gsap` 未定义 → 整条时间线建不起来 → 每段卡都停在内联 opacity:0 → 成片是
**全黑的无声空片**，不是"停在首帧"，排查时几乎猜不到是缺一个脚本。
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import gen_hyperframes as GH


class GsapSrcGuard(unittest.TestCase):
    """--gsap-src 给的是本地相对路径时，只按**浏览器真正会用的那条路径**判存在。

    `<script src="…">` 是从 HTML 文件所在目录（输出目录）解析的，不是从运行命令时
    的当前目录。旧实现把 CWD 也当合法查找基准，于是"文件恰好在 CWD 下"时一声不吭，
    而 out_dir 里并没有它——假守卫承诺了"文件在"，成片却是全黑无声空片。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)
        self.out = os.path.join(self.tmp.name, "hf-project")
        self.cwd_only = os.path.join(self.tmp.name, "elsewhere")
        os.makedirs(self.out)
        os.makedirs(self.cwd_only)

    def _warned(self, src):
        buf = io.StringIO()
        with redirect_stderr(buf):
            GH._warn_if_local_vendor_missing(src, self.out)
        return buf.getvalue()

    def test_file_only_in_cwd_still_warns(self):
        os.chdir(self.cwd_only)
        with open("gsap.min.js", "w", encoding="utf-8") as f:
            f.write("// gsap")
        out = self._warned("gsap.min.js")
        self.assertIn("gsap.min.js", out)
        self.assertIn("全黑", out)

    def test_file_in_output_dir_is_silent(self):
        with open(os.path.join(self.out, "gsap.min.js"), "w", encoding="utf-8") as f:
            f.write("// gsap")
        self.assertEqual(self._warned("gsap.min.js"), "")

    def test_absolute_path_is_checked_as_given(self):
        target = os.path.join(self.cwd_only, "vendor", "gsap.min.js")
        os.makedirs(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as f:
            f.write("// gsap")
        self.assertEqual(self._warned(target), "")

    def test_url_and_data_uri_are_not_file_checked(self):
        self.assertEqual(self._warned("https://cdn.example/gsap.min.js"), "")
        self.assertEqual(self._warned("data:text/javascript,void 0"), "")


if __name__ == "__main__":
    unittest.main()
