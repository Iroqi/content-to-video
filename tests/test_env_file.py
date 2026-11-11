"""`.env` 解析：编码判别 + 引号/注释/百分号转义的取值口径。不需要 Node / ffmpeg。"""
import io
import os
import tempfile
import unittest
from unittest import mock

import _helpers as H  # noqa: F401
import pipeline as P


def _parse(raw_bytes):
    with tempfile.NamedTemporaryFile("wb", suffix=".env", delete=False) as f:
        f.write(raw_bytes)
        path = f.name
    try:
        return P._parse_env_file(path)
    finally:
        os.unlink(path)


class EnvEncoding(unittest.TestCase):
    def test_utf8_with_bom(self):
        self.assertEqual(_parse(b"\xef\xbb\xbfKEY=v\n"), {"KEY": "v"})

    def test_utf16_with_bom_powershell_redirect(self):
        self.assertEqual(_parse("KEY=v\n注释=中\n".encode("utf-16")), {"KEY": "v", "注释": "中"})

    def test_gb18030_typo_saved_as_ansi(self):
        self.assertEqual(_parse("KEY=v\n# 中文注释\n".encode("gb18030")), {"KEY": "v"})

    def test_unreadable_bytes_warn_not_crash(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(_parse(b"KEY=\xff\xfd\xf0\x00bad"), {})

    def test_bomless_utf16_warns_about_nul(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(_parse("KEY=v\n".encode("utf-16-le")), {})
        self.assertIn("NUL", err.getvalue())


class EnvValues(unittest.TestCase):
    def test_export_prefix_and_quotes(self):
        got = _parse(b'export A=1\nB="x y"\nC=\'q z\'\n')
        self.assertEqual(got, {"A": "1", "B": "x y", "C": "q z"})

    def test_inline_comment_only_after_space(self):
        got = _parse(b"KEY=v # note\nCOLOR=#f59e0b\n")
        self.assertEqual(got, {"KEY": "v", "COLOR": "#f59e0b"})

    def test_unbalanced_quote_kept_verbatim(self):
        self.assertEqual(_parse(b'KEY="abc\n'), {"KEY": '"abc'})

    def test_percent_encoded_token_is_decoded(self):
        # token 常在浏览器地址栏里被百分号转义过，原样拿去请求就是 401
        self.assertEqual(_parse(b"TOKEN=sk-abc%2Bdef%3D\n"), {"TOKEN": "sk-abc+def="})

    def test_blank_and_comment_lines_ignored(self):
        self.assertEqual(_parse(b"\n  \n# c\nA=1\n"), {"A": "1"})


if __name__ == "__main__":
    unittest.main()
