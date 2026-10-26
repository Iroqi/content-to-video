"""缓存状态机 resolve_resume_state：纯函数，逐条钉住"缺证明就当没缓存"的判定表。"""
import contextlib
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
                   mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client),
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


class RandomizedSpeedTmpIsStillCleanedUp(unittest.TestCase):
    """变速中转改名（并行不互踩）之后，sidecar 清理还得认得出它。

    中转名从固定的 `<wav>.spd.tmp.wav` 换成带 mkstemp 随机串的名字，是为了
    两个进程变同一批句子时不互相踩掉对方的中转文件。代价是一条**精确路径**
    的清理再也扫不到它——留下的残留在下次合成时被当成上一轮的语速状态。
    所以清理按 glob 扫 `<wav>.spd.*.tmp.wav`。
    """

    def test_sidecar_sweep_catches_randomized_speed_tmp(self):
        from _script_utils import unique_tmp
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "s001.wav")
            survivors = {}
            for suffix in P._SIDECAR_SUFFIXES:
                if "*" in suffix:                      # 变速中转：带随机串的名
                    p = unique_tmp(td, os.path.basename(out_path) + ".spd.",
                                   ".tmp.wav")
                else:
                    p = out_path + suffix
                    open(p, "wb").close()
                self.assertTrue(os.path.exists(p))
                survivors[suffix] = p
            P._clear_stale_sidecars(out_path)
            for suffix, p in survivors.items():
                self.assertFalse(os.path.exists(p),
                                 f"{suffix} 没被清掉（中转名一变就漏）: {p}")

    def test_body_survives_the_sweep(self):
        """globbing 不能顺手把句子 wav 本体也删了——它是 --resume 的缓存。"""
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "s001.wav")
            with open(out_path, "wb") as f:
                f.write(b"RIFF")
            P._clear_stale_sidecars(out_path)
            self.assertTrue(os.path.exists(out_path))

    def test_isolated_stale_sidecars_are_swept_too(self):
        """删除被占用时改名隔离出的 `.stale` 残渣，锁释放后的下一轮要收走。

        修复前隔离即终点：`_SIDECAR_SUFFIXES` 与 `_drop_stale_cache` 的 glob
        都匹配不到 `.stale` 后缀，残渣只进不出、永久留在输出目录。注意隔离名是
        `<片段>.stale`（`s001.wav.orig.wav.stale`），glob 必须并上每个片段的
        变体而不是单加一个 `.stale` 条目。
        """
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "s001.wav")
            stale = [out_path + s + ".stale"
                     for s in (".orig.wav", ".spd", ".failed")]
            for p in stale:
                open(p, "wb").close()
            # 新合成路径的清理
            self.assertTrue(P._clear_stale_sidecars(out_path))
            for p in stale:
                self.assertFalse(os.path.exists(p), f".stale 残渣没被扫掉: {p}")
            # 缓存失效路径的清空（_drop_stale_cache）同样要收
            for p in stale:
                open(p, "wb").close()
            P._drop_stale_cache(out_path)
            for p in stale:
                self.assertFalse(os.path.exists(p),
                                 f"_drop_stale_cache 没扫 .stale: {p}")


@unittest.skipUnless(_have_ffmpeg(), "需要 ffmpeg 执行 atempo")
class ResumeSpeedExceptionDoesNotCrash(unittest.TestCase):
    """resume 的语速重施加抛异常（ffmpeg 没了/盘满）时必须降级，不能裸栈中断。

    主合成路径早在 synthesize_one 里把 apply_speed 的异常收敛成 speed-skip；
    resume 分支原先裸调——同一台机器上换了个语速重跑，ffmpeg 一出问题整轮
    resume 带着栈trace 死掉，而主路径同样的失败只是保留原速。
    """

    SPEED = 1.5

    def _run(self, out, src_path, client, resume, speed, apply_speed=None):
        argv = ["pipeline.py", "--source", src_path, "-o", out,
                "--api-key", "test-key", "--base-url", "https://x.example/v1",
                "--speed", str(speed), "--workers", "1"]
        if resume:
            argv.append("--resume")
        patches = [mock.patch.object(P.sys, "argv", argv),
                   mock.patch.object(P, "TTS_CLIENT_FACTORY", lambda *a, **k: client),
                   mock.patch.object(P.time, "sleep")]
        if apply_speed is not None:
            patches.append(mock.patch.object(P, "apply_speed", apply_speed))
        with patches[0], patches[1], patches[2]:
            if apply_speed is not None:
                with patches[3]:
                    P.main()
            else:
                P.main()

    def test_reapply_exception_keeps_previous_speed_audio(self):
        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "src.json")
            with open(src_path, "w", encoding="utf-8") as f:
                json.dump({"segments": [{"id": "seg-a", "title": "x",
                                         "text": "只有一句话。"}]}, f,
                          ensure_ascii=False)
            out = os.path.join(td, "out")
            client = _FakeClient()
            # 首跑 1.5x：落下 .orig.wav + .spd=1.5
            self._run(out, src_path, client, resume=False, speed=self.SPEED)
            wav = os.path.join(out, "sentences", "s001.wav")
            with open(wav, "rb") as f:
                before = f.read()

            # 二跑 --resume 请求 2.0x：reapply 分支的 apply_speed 抛 OSError
            def boom(*a, **k):
                raise OSError("ffmpeg 不见了（模拟）")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self._run(out, src_path, client, resume=True, speed=2.0,
                          apply_speed=boom)
            # 1. 不崩：main() 正常返回（旧代码在这里裸栈中断）
            # 2. 出声：异常点名，不是静默保持
            self.assertIn("atempo", err.getvalue())
            # 3. 音频保持 1.5x 原样（≈0.33s），没有半截产物
            self.assertAlmostEqual(_tiny_wav_dur(wav), 0.33, delta=0.05)
            # 4. 没重烧 TTS
            self.assertEqual(len(client.calls), 1)
            # 5. marker 仍是事实值 1.5（文件在 1.5x，marker 不许说谎）
            with open(wav + ".spd", encoding="utf-8") as f:
                self.assertEqual(f.read(), str(TL.speed_marker_value(self.SPEED)))
            with open(wav, "rb") as f:
                self.assertEqual(before, f.read())


if __name__ == "__main__":
    unittest.main()
