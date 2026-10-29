"""配图**不做**持续推近（Ken Burns）的行为锁。

这一层运镜被删掉了：SVG 的正文常常贴着画布边缘，推近把图放大 4%–8%、位移还会
把边缘拖进槽位，边框附近的配文就被裁掉——素材里明明写着、成片里看不见，而任何
门禁都量不出来（图确实铺满了）。

删除后必须成立的只有一件事：**载体上不存在贯穿整段的长补间**。本文件钉住它，
防止某个模板旋钮被接回来。
"""
import contextlib
import io
import re
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path
from _template import load_template
from html_renderer import generate_html

# 作用在图载体（`#img-{sid} > img`）上的补间：收拢与被删掉的推近都长这样。
_CARRIER_TWEEN_RE = re.compile(
    r'tl\.fromTo\("#img-[A-Za-z0-9_-]+(?:-\d)? > img",\{[^}]*\},\{([^}]*)\},([\d.]+)\}')


def render(images=None, aspect="portrait"):
    m = H.make_manifest(source=H.sample_source())
    with contextlib.redirect_stderr(io.StringIO()):
        return generate_html(m, "audio/combined.wav",
                             images=images or H.sample_images(m),
                             aspect=aspect, theme="dark")


class NoPersistentDrift(unittest.TestCase):
    def test_no_carrier_tween_outlives_the_entrance(self):
        """载体上每条补间都是入场收拢（终止于 scale:1，时长 ≤ 1s）。"""
        for aspect in ("portrait", "landscape"):
            html = render(aspect=aspect)
            for to, dur in _CARRIER_TWEEN_RE.findall(html):
                self.assertIn("scale:1,", to)
                self.assertLessEqual(float(dur), 1.0,
                                     f"{aspect}: 载体上出现了一条 {dur}s 的长补间"
                                     "——持续推近又被接回来了")

    def test_template_has_no_drift_knob(self):
        """模板里不该再有 drift：旋钮在而代码不读，就是"写进配置以为生效了"。"""
        self.assertNotIn("drift", load_template()["animation"]["imageEntrance"])


if __name__ == "__main__":
    unittest.main()
