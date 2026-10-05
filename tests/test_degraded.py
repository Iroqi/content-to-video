import os
import re
import unittest

import _helpers as H  # noqa: F401
import _degraded as D
import _manifest_schema as MAN


class RegistryConsistency(unittest.TestCase):
    def test_keys_derive_from_kinds(self):
        self.assertEqual(D.KEYS, tuple(k.key for k in D.KINDS))
        self.assertEqual(MAN.DEGRADED_KEYS, D.KEYS)

    def test_no_duplicate_keys_or_types(self):
        self.assertEqual(len(set(D.KEYS)), len(D.KEYS))
        types = [k.type for k in D.KINDS]
        self.assertEqual(len(set(types)), len(types))

    def test_every_constant_is_registered(self):
        consts = {v for n, v in vars(D).items() if n.isupper() and isinstance(v, str)}
        self.assertEqual(consts, set(D.KEYS))

    def test_pipeline_writes_only_registered_constants(self):
        src = H.read_text(os.path.join(H.SCRIPTS_DIR, "pipeline.py"))
        # 变量在 _finalize 拆分后叫 degraded（历史名 _degraded），模式两个都认：
        # 守护的是"写入必须走 D.<常量>、键必须登记"，不是某个局部变量名。
        used = set(re.findall(r"degraded\[D\.([A-Z_]+)\]", src))
        self.assertTrue(used, "pipeline 应通过 D.<常量> 写降级键")
        for name in used:
            self.assertTrue(hasattr(D, name), f"pipeline 用了未定义的 D.{name}")
        # 反向：注册的每一档至少被 pipeline 写过一次
        registered = {n for n, v in vars(D).items() if n.isupper() and isinstance(v, str)}
        self.assertEqual(used, registered, "有登记却没人写的降级键，或有写入却未登记")

    def test_no_literal_degraded_keys_left_in_pipeline(self):
        src = H.read_text(os.path.join(H.SCRIPTS_DIR, "pipeline.py"))
        self.assertIsNone(re.search(r'degraded\["', src), "pipeline 里不该再用字面键写降级")


class Items(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(D.items({"sentences": []}), [])

    def test_each_kind_reads(self):
        tm = {
            "sentences": [{"synth_failed": True}, {}],
            "degraded": {
                D.LOST_SENTENCE_COUNT: 2, D.SEGMENTS_DROPPED: ["seg2", "seg3"],
                D.AUDIO_SHORTER_THAN_TIMELINE: 0.8,
            },
        }
        got = {t: (n, msg) for t, n, msg in D.items(tm)}
        self.assertEqual(set(got), {k.type for k in D.KINDS})
        self.assertEqual(got["tts_silence_fallback"][0], 1)
        self.assertEqual(got["tts_lost_sentences"][0], 2)
        self.assertEqual(got["segments_dropped"][0], 2)
        self.assertIn("seg2", got["segments_dropped"][1])
        self.assertEqual(got["audio_shorter_than_timeline"][0], 0.8)

    def test_order_follows_kinds(self):
        tm = {"sentences": [], "degraded": {k: True for k in D.KEYS}}
        tm["degraded"][D.SEGMENTS_DROPPED] = ["x"]
        tm["degraded"][D.AUDIO_SHORTER_THAN_TIMELINE] = 1.0
        tm["degraded"][D.LOST_SENTENCE_COUNT] = 1
        types = [t for t, _, _ in D.items(tm)]
        order = [k.type for k in D.KINDS]
        self.assertEqual(types, [t for t in order if t in types])

    def test_corrupted_values_tolerated(self):
        tm = {"sentences": [], "degraded": {D.LOST_SENTENCE_COUNT: "oops",
                                            D.AUDIO_SHORTER_THAN_TIMELINE: "x",
                                            D.SEGMENTS_DROPPED: "not-a-list"}}
        self.assertEqual(D.items(tm), [])


class ManifestRejectsUnknownKey(unittest.TestCase):
    def test_unknown_degraded_key_rejected(self):
        m = H.make_manifest()
        m["status"] = "degraded"
        m["degraded"] = {"tts_silence_fallbak": True}  # 拼错
        with self.assertRaises(Exception) as cm:
            MAN.validate_timing_manifest(m)
        self.assertIn("tts_silence_fallbak", str(cm.exception))

    def test_known_degraded_key_accepted(self):
        m = H.make_manifest()
        m["status"] = "degraded"
        m["degraded"] = {D.LOST_SENTENCE_COUNT: 1}
        MAN.validate_timing_manifest(m)


if __name__ == "__main__":
    unittest.main()
