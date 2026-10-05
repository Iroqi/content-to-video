"""生成器的三道"静默废品"闸门。

三条都是同一个失败模式：**跑完不报错，成片却是废品**——空白页、无声片、凭空
消失的 cta 行。它们的共同点是"发现得最晚"：要等渲染完、看完成片才知道，而那时
烧掉的是一整轮渲染。所以这三条必须由生成器在**写盘之前**拦下或出声。

每组用例都配一条对照（合法输入照样 exit 0），免得把闸门焊死。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402
from gen_remotion_project import main as gen_main  # noqa: E402


class CanvasSegmentNeedsAnImage(unittest.TestCase):
    """整页画布没有配图 = 整页空白。不传 --images 也必须拦。"""

    def _run(self, extra):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = H.make_manifest()
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            with redirect_stderr(buf):
                code = 0
                try:
                    gen_main(["-m", m_path, "-o", os.path.join(tmp, "proj")]
                             + extra)
                except SystemExit as e:
                    code = e.code or 0
        return code, buf.getvalue()

    def test_canvas_without_images_is_refused(self):
        """--images 根本没传，画布段照样要被点名拦下。"""
        code, err = self._run([])
        self.assertNotEqual(code, 0, "画布段缺图必须 fail-fast")
        self.assertIn("canvas", err)
        self.assertIn("seg-b", err)

    def test_slot_only_source_without_images_still_generates(self):
        """闸门只针对画布段：纯槽位稿件没图照样出片（标题层与句子流层还在）。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = H.sample_source()
            for s in src["segments"]:
                s.pop("layout", None)      # 全部退回槽位版式
            manifest = H.make_manifest(src)
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            self.assertEqual(
                gen_main(["-m", m_path, "-o", os.path.join(tmp, "proj")]), None)

    def test_canvas_with_image_passes(self):
        """给了图就照常生成——闸门只针对"缺图"这一件事。"""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = H.make_manifest()
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            img = H.write_placeholder_images(manifest, tmp)
            self.assertEqual(gen_main(["-m", m_path, "-o",
                                       os.path.join(tmp, "proj"), "--images", img]),
                             None)


class DeclaredAudioMustExist(unittest.TestCase):
    """manifest 点了名的音频读不到 → 拒绝出无声片。"""

    def _run(self, combined_audio):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = H.make_manifest()
            if combined_audio is not None:
                manifest["combined_audio"] = combined_audio
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            img = H.write_placeholder_images(manifest, tmp)
            with redirect_stderr(buf):
                code = 0
                try:
                    gen_main(["-m", m_path, "-o", os.path.join(tmp, "proj"),
                              "--images", img])
                except SystemExit as e:
                    code = e.code or 0
        return code, buf.getvalue()

    def test_declared_but_missing_audio_is_refused(self):
        code, err = self._run("audio/combined.wav")
        self.assertNotEqual(code, 0, "声明了音频却读不到，不能默默出无声片")
        self.assertIn("combined_audio", err)

    def test_absent_audio_field_only_warns(self):
        """manifest 本身不带音频字段 = 手写稿做画面检查，出声提醒但不拦。"""
        code, err = self._run(None)
        self.assertEqual(code, 0)
        self.assertIn("无声", err)


class OrphanClosingCtaSpeaksUp(unittest.TestCase):
    """cta 尾行无处可画时，作者得在生成日志里看到，而不是在成片里发现。"""

    def _run(self, mutate):
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = H.make_manifest()
            mutate(manifest)
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            img = H.write_placeholder_images(manifest, tmp)
            with redirect_stderr(buf):
                gen_main(["-m", m_path, "-o", os.path.join(tmp, "proj"),
                          "--images", img])
        return buf.getvalue()

    def test_cta_without_closing_page_warns(self):
        """稿件没有结尾页时（manifest 里就没有 closing 段），cta 无处可画。"""
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            src = H.sample_source()
            del src["closing"]          # 没有结尾页 → 不会有 closing 段
            manifest = H.make_manifest(src)
            manifest["closing_cta"] = "关注我们"
            m_path = os.path.join(tmp, "manifest.json")
            with open(m_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False)
            img = H.write_placeholder_images(manifest, tmp)
            with redirect_stderr(buf):
                gen_main(["-m", m_path, "-o", os.path.join(tmp, "proj"),
                          "--images", img])
        self.assertIn("closing_cta", buf.getvalue())

    def test_cta_on_canvas_closing_warns(self):
        def closing_to_canvas(m):
            for s in m["segments"]:
                if s["id"] == "closing":
                    s["layout"] = "canvas"
            m["closing_cta"] = "关注我们"

        err = self._run(closing_to_canvas)
        self.assertIn("closing_cta", err)

    def test_cta_with_agenda_closing_is_silent(self):
        """有地方画就别刷屏——warn 只报"丢了"，不报"画上了"。"""
        def keep(m):
            m["closing_cta"] = "关注我们"

        err = self._run(keep)
        self.assertNotIn("closing_cta", err)


if __name__ == "__main__":
    unittest.main()
