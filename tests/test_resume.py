"""缓存状态机 resolve_resume_state：纯函数，逐条钉住"缺证明就当没缓存"的判定表。"""
import unittest

import _helpers as H  # noqa: F401
import _contracts as C
from pipeline import ResumeFacts, resolve_resume_state

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
            facts(spd_exists=True, spd_readable=True, spd_applied=C.speed_marker_value(1.5)), 1.5)
        self.assertEqual(d.action, "use_cached")

    def test_speed_changed_reapplies_from_backup(self):
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=True, spd_applied=C.speed_marker_value(1.2),
                  orig_wav_exists=True), 1.5)
        self.assertEqual(d.action, "reapply")
        self.assertEqual(d.apply_speed_to, 1.5)
        self.assertEqual(d.write_spd, C.speed_marker_value(1.5))

    def test_unreadable_marker_with_backup_restores_first(self):
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=False, orig_wav_exists=True), 1.5)
        self.assertEqual(d.action, "reapply")
        self.assertTrue(d.restore_first)

    def test_failed_marker_is_carried(self):
        marker = C.speed_marker_value(1.5)
        d = resolve_resume_state(
            facts(spd_exists=True, spd_readable=True, spd_applied=marker,
                  failed_marker_exists=True), 1.5)
        self.assertEqual(d.action, "use_cached")
        self.assertTrue(d.synth_failed)


if __name__ == "__main__":
    unittest.main()
