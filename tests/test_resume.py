"""缓存状态机 resolve_resume_state：纯函数，逐条钉住"缺证明就当没缓存"的判定表。"""
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import wave
from unittest import mock

import _helpers as H  # noqa: F401
import _audio
import _timeline as TL
import pipeline as P
from pipeline import ResumeFacts, resolve_resume_state


def _have_ffmpeg():
    return shutil.which("ffmpeg") is not None

OK = dict(sha_exists=True, sha_matches=True, audio_exists=True,
          audio_duration_ok=True, audio_duration=2.0, audio_intact=True)


def facts(**kw):
    d = dict(OK)
    d.update(kw)
    return ResumeFacts(**d)


class Regen(unittest.TestCase):
    def check(self, f, speed=1.0):
        d = resolve_resume_state(f, speed)
        self.assertEqual(d.action, "regen", d.reason)
        self.assertTrue(d.reason)

    def test_missing_sha(self):
        self.check(facts(sha_exists=False))

    def test_sha_mismatch(self):
        self.check(facts(sha_matches=False))

    def test_audio_missing(self):
        self.check(facts(audio_exists=False))

    def test_duration_unmeasurable(self):
        self.check(facts(audio_duration_ok=False))

    def test_truncated_wav(self):
        self.check(facts(audio_intact=False))

    def test_unreadable_marker_without_backup(self):
        self.check(facts(spd_exists=True, spd_readable=False, orig_wav_exists=False), speed=1.5)

    def test_no_marker_no_backup_but_speed_requested(self):
        self.check(facts(spd_exists=False), speed=1.5)


class Reuse(unittest.TestCase):
    def test_speed_one_without_marker_is_noop_reapply(self):
        # 现状（characterization）：速度 1.0 且没有 .spd 时判 reapply 而非 use_cached，
        # 执行侧会多做一次无意义的 apply_speed + 时长重测。不是错误，是可优化的多余一步。
        d = resolve_resume_state(facts(), 1.0)
        self.assertEqual(d.action, "reapply")
        self.assertIsNone(d.write_spd)

    def test_marker_matches_requested_speed(self):
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=True, spd_applied=TL.speed_marker_value(1.5)), 1.5)
        self.assertEqual(d.action, "use_cached")

    def test_speed_changed_reapplies_from_backup(self):
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=True, spd_applied=TL.speed_marker_value(1.2),
                  orig_wav_exists=True), 1.5)
        self.assertEqual(d.action, "reapply")
        self.assertEqual(d.apply_speed_to, 1.5)
        self.assertEqual(d.write_spd, TL.speed_marker_value(1.5))

    def test_unreadable_marker_with_backup_restores_first(self):
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=False, orig_wav_exists=True), 1.5)
        self.assertEqual(d.action, "reapply")
        self.assertTrue(d.restore_first)

    def test_failed_marker_is_carried(self):
        marker = TL.speed_marker_value(1.5)
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=True, spd_applied=marker,
                  failed_marker_exists=True), 1.5)
        self.assertEqual(d.action, "use_cached")
        self.assertTrue(d.synth_failed)


class _FakeClient:
    """不联网的 TTS：恒返回同一段 0.5s 静音 WAV（够 atempo 与量时长）。"""

    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice_id, voice_style, model, timeout):
        self.calls.append(text)
        n = int(8000 * 0.5)
        frames = b"".join(struct.pack("<h", 0) for _ in range(n))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(frames)
        return buf.getvalue()


def _tiny_wav_dur(path):
    return _audio._wav_duration(path)


@unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg 执行 atempo")
class RestoreFailureKeepsBackupUsable(unittest.TestCase):
    """还原失败后的级联：推定语速不许流入 reapply，真原速备份不许被覆盖。

    实证过的坏链：.spd 被写坏（不可读）+ .orig.wav 还在 →
    resolve_resume_state 推定 applied=1.0 并令 restore_first；还原
    （copy2）失败后旧代码 fall-through 到 reapply 仍带着这个推定值，
    apply_speed 的 refresh_backup 把当前这条语速未知的 WAV 盖回
    .orig.wav——真原速备份销毁，之后每次换速都在错误基线上叠变速
    （1.5x 盖回备份 → 再 atempo 1.5 = 2.25x，且全程零报错）。
    """

    SPEED = 1.5

    def _run(self, out, src_path, client, resume, copy2=None):
        argv = ["pipeline.py", "--source", src_path, "-o", out,
                "--api-key", "test-key", "--base-url", "https://x.example/v1",
                "--speed", str(self.SPEED), "--workers", "1"]
        if resume:
            argv.append("--resume")
        patches = [mock.patch.object(P.sys, "argv", argv),
                   mock.patch.object(P, "MimoTtsClient", return_value=client),
                   mock.patch.object(P.time, "sleep")]
        if copy2 is not None:
            patches.append(mock.patch.object(shutil, "copy2", copy2))
        with patches[0], patches[1], patches[2]:
            if copy2 is not None:
                with patches[3]:
                    P.main()
            else:
                P.main()

    def test_restore_failure_never_overwrites_the_true_backup(self):
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({"segments": [{"id": "seg-a", "title": "x",
                                         "text": "只有一句话。"}]}, f,
                          ensure_ascii=False)
            out = os.path.join(td, "out")
            client = _FakeClient()

            # 第一跑：1.5x 合成，落下 .orig.wav（0.5s 原速）+ .spd=1.5 + .sha
            self._run(out, src_path, client, resume=False)
            wav = os.path.join(out, "sentences", "s001.wav")
            orig = wav + ".orig.wav"
            self.assertTrue(os.path.exists(orig), "首跑应留下原速备份")
            with open(orig, "rb") as f:
                orig_bytes = f.read()
            self.assertAlmostEqual(_tiny_wav_dur(orig), 0.5, places=2)
            self.assertEqual(len(client.calls), 1)

            # 把 .spd 写坏：marker 不可读 + 备份还在 → 推定 applied=1.0 +
            # restore_first（判定表见 resolve_resume_state）
            with open(wav + ".spd", "w", encoding="utf-8") as f:
                f.write("not-a-number")

            # 第二跑 --resume：令还原那一步的 copy2 失败（模拟 Windows 文件被占），
            # 记录全部 copy2 调用——修复后 reapply 不准再碰备份，只该有失败那一次。
            copy_calls = []
            real_copy2 = shutil.copy2

            def flaky_copy2(src, dst):
                copy_calls.append((os.path.basename(src), os.path.basename(dst)))
                if len(copy_calls) == 1:
                    raise OSError("文件被播放器占用（模拟还原失败）")
                return real_copy2(src, dst)

            self._run(out, src_path, client, resume=True, copy2=flaky_copy2)

            # 1. 真原速备份逐字节未动（坏链会把它盖成 1.5x 音频）
            with open(orig, "rb") as f:
                self.assertEqual(f.read(), orig_bytes,
                             ".orig.wav 被覆盖——推定语速流进了 refresh_backup")
            self.assertAlmostEqual(_tiny_wav_dur(orig), 0.5, places=2)
            # 2. 只发生过失败那一次 copy2：reapply 没有覆盖备份
            self.assertEqual(len(copy_calls), 1, copy_calls)
            # 3. WAV 是 1.5x 的原速产物（≈0.33s），不是叠倍后的 2.25x（≈0.23s）
            dur = _tiny_wav_dur(wav)
            self.assertGreater(dur, 0.29, f"语速被叠倍：{dur:.3f}s")
            self.assertLess(dur, 0.38, f"语速不对：{dur:.3f}s")
            # 4. marker 补写成事实值（旧值/坏值都不许留）
            with open(wav + ".spd", encoding="utf-8") as f:
                self.assertEqual(f.read(), str(TL.speed_marker_value(self.SPEED)))
            # 5. 缓存命中：没有重烧 TTS
            self.assertEqual(len(client.calls), 1)

            # 第三跑 --resume（不打补丁）：marker 可读且等于请求语速 →
            # use_cached，一句 ffmpeg 都不跑，音频与调用次数原封不动
            with open(wav, "rb") as f:
                before = f.read()
            self._run(out, src_path, client, resume=True)
            with open(wav, "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
