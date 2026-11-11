"""karaoke-align：把外部字级时间戳编译成精确 at_time 的 animate.steps。

补的是「字跟着唱的时间进出」里差的那一截。主管线其实有完整的卡拉OK逐字
高亮（scripts/_assets/runtime.js 的 verseEmUpdate / verseVedApply），但它按
**字宽加权的句内匀速**推进——因为 TTS 只回整句音频、没有词级时间戳。runtime.js
的注释自己记着这个偏差：平均 0.2 字、长句能到 1s 量级。

这个扩展不改渲染端、不改 manifest 键集：把强制对齐（forced alignment）或字级
LRC 给出的每个字的真实时刻，编译成一条条独立的 step。每个字在自己那一刻
亮起，而不是被平均分配。产物是标准 steps，作者照旧写进 images.json，主管线
零改动、确定性红线不破。

纯函数：不读墙钟 / 随机 / 文件 / 网络，同一 (spec, ctx) 两次编译逐字节一致。
Python 3.9 兼容，只用标准库。
"""
import re

_LRC_TAG = re.compile(r"\[(\d{1,2}):(\d{2}(?:\.\d+)?)\]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _fail(msg):
    raise ValueError(msg)


def _parse_lrc(lrc):
    """字级 LRC：每个 [mm:ss.xx] 后面跟且只跟一个字符。"""
    if not isinstance(lrc, str):
        _fail("spec.lrc 必须是字符串")
    tags = list(_LRC_TAG.finditer(lrc))
    if not tags:
        _fail("spec.lrc 里一个 [mm:ss.xx] 标记都没有")
    pairs = []
    for i, m in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(lrc)
        chunk = lrc[m.end():end].strip()
        if len(chunk) != 1:
            _fail("字级 LRC 要求每个时间戳后跟且只跟一个字（第 %d 段是 %r）"
                  % (i + 1, chunk))
        seconds = int(m.group(1)) * 60 + float(m.group(2))
        pairs.append((seconds, chunk))
    return pairs


def compile(spec, ctx):  # noqa: A001 —— 契约要求的函数名，遮蔽内置 compile 是有意的
    text = spec.get("text")
    if not isinstance(text, str) or not text.strip():
        _fail("spec.text 必须是非空字符串（与 SVG 里逐字上屏的原文一致）")
    text = text.strip()

    lrc = spec.get("lrc")
    onsets = spec.get("onsets")
    if (lrc is None) == (onsets is None):
        _fail("spec 必须且只能在 lrc / onsets 里给一种字级时刻")

    if lrc is not None:
        pairs = _parse_lrc(lrc)
        joined = "".join(ch for _, ch in pairs)
        if joined != text:
            _fail("LRC 拼出来的字与 spec.text 不一致（%r ≠ %r）" % (joined, text))
        times = [round(t, 3) for t, _ in pairs]
    else:
        if not isinstance(onsets, list) or len(onsets) != len(text):
            _fail("spec.onsets 必须是与 spec.text 等长的数组（%d 字）" % len(text))
        times = []
        for i, t in enumerate(onsets):
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not t >= 0:
                _fail("onsets[%d] 必须是非负秒数（实际 %r）" % (i, t))
            times.append(round(float(t), 3))

    for i in range(len(times) - 1):
        if times[i] > times[i + 1] + 1e-9:
            _fail("字级时刻必须单调递增：第 %d 字 %.3fs 晚于第 %d 字 %.3fs"
                  % (i + 1, times[i], i + 2, times[i + 1]))

    prefix = spec.get("prefix", "k")
    cls = spec.get("class", "kch")
    dim = spec.get("dim", "#5b6478")
    lit = spec.get("lit", "#f8d66d")
    for label, ident in (("prefix", prefix), ("class", cls)):
        if not isinstance(ident, str) or not _IDENT.match(ident):
            _fail("spec.%s 必须是简单标识符（实际 %r）" % (label, ident))
    for label, color in (("dim", dim), ("lit", lit)):
        if not isinstance(color, str) or not _HEX.match(color):
            _fail("spec.%s 必须是 #RRGGBB（实际 %r）" % (label, color))

    # 先统一压暗，再逐字点亮。第 0 条挂在段起点，避免首字之前整行就已经是亮色。
    steps = [{"at_time": 0, "target": "." + cls,
              "set": {"fill": dim}, "duration": 0.01}]
    for i, t in enumerate(times):
        steps.append({"at_time": t,
                      "target": "#" + prefix + str(i),
                      "set": {"fill": lit}, "duration": 0.12})
    return {"steps": steps}
