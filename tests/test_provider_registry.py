"""Provider 注册表契约：加一家提供方不该动 pipeline。

这套用例存在的理由：TTS 是整条链路唯一的付费外部依赖，也是唯一"换一家
就得改编排层"的地方。注册表把这件事收成"加一个类 + 加一行"，本文件钉住
三件事实——

① `make_client()` 按名字构造，未知名字是**可控报错**而不是 KeyError；
② `needs_api_key=False` 的提供方能绕过"没 key 就停"那道闸（否则本地/离线
   提供方永远进不来）；
③ pipeline 只在 `TTS_CLIENT_FACTORY` 这个接缝上取客户端——测试全量 patch
   它，而不是 patch 某个具体类名。
"""
import os
import sys
import unittest

import _helpers as H  # noqa: F401  仅副作用：把 scripts/ 放进 sys.path
import _tts as T
import pipeline as P


class FactoryContract(unittest.TestCase):
    def test_make_client_by_name(self):
        c = T.make_client("mimo", "k", "https://x.example/v1")
        self.assertEqual(c.url, "https://x.example/v1/chat/completions")
        self.assertEqual(T.provider_label(c), "mimo")
        self.assertTrue(T.provider_needs_api_key(c))

    def test_default_provider_is_registered(self):
        self.assertIn(T.DEFAULT_PROVIDER, T.PROVIDERS)
        self.assertEqual(T.DEFAULT_PROVIDER, T.PROVIDER_NAMES[0])

    def test_unknown_provider_is_a_value_error_not_a_keyerror(self):
        """未知名字要能被 `parser.error(...)` 吃下：ValueError，不是 KeyError。

        KeyError 会带着裸 traceback 穿到用户面前，而且提示里没有可选清单。
        """
        with self.assertRaises(ValueError) as cm:
            T.make_client("nope")
        msg = str(cm.exception)
        self.assertIn("nope", msg)
        for name in T.PROVIDER_NAMES:
            self.assertIn(name, msg)

    def test_injected_factory_wins(self):
        """factory 非 None 时完全接管构造，不碰注册表。"""
        sentinel = object()
        got = T.make_client("mimo", "k", "https://x", factory=lambda a, b: sentinel)
        self.assertIs(got, sentinel)

    def test_provider_without_base_url_does_not_fake_one(self):
        """不认 base_url 的实现不该被打出一条看着像真端点的日志。

        这里用一个临时注册项验证"注册表第三列确实生效"——真实提供方目前
        只有 mimo，但这一列是给下一个提供方预留的唯一开关。
        """
        class Local(T.BaseTtsProvider):
            label = "local"
            needs_api_key = False

            def __init__(self):
                self.calls = 0

            def synthesize(self, text, voice_id, voice_style, model, timeout):
                self.calls += 1
                return b"RIFF"

        T.PROVIDERS["_local_test"] = (Local, False)
        try:
            c = T.make_client("_local_test", "ignored-key", "https://ignored")
            self.assertEqual(c.calls, 0)
            self.assertEqual(T.provider_label(c), "local")
            self.assertFalse(T.provider_needs_api_key(c))
            self.assertFalse(T.provider_takes_base_url("_local_test"))
            # 实例反问也要认得它（按类名反查注册表）
            self.assertFalse(T.provider_takes_base_url_for_instance(c))
        finally:
            del T.PROVIDERS["_local_test"]

    def test_mimo_instance_still_reports_base_url(self):
        """反查失败一律按"认"处理：宁可多打一行端点，不要把真端点藏起来。"""
        c = T.make_client("mimo", "k", "https://x.example/v1")
        self.assertTrue(T.provider_takes_base_url_for_instance(c))


class PipelineSeam(unittest.TestCase):
    def test_pipeline_exposes_the_factory_seam(self):
        """pipeline 必须以模块级名字持有注入点。

        没有它，测试只能 patch `P.MimoTtsClient`——默认提供方一旦不是 MiMo，
        那些 patch 就在改一个没人用的符号，测试仍然全绿（假通过）。
        """
        self.assertTrue(hasattr(P, "TTS_CLIENT_FACTORY"))
        self.assertIsNone(P.TTS_CLIENT_FACTORY)

    def test_no_api_key_message_names_the_provider(self):
        """报错要点名是哪一家要 key，而不是笼统一句 No API key。

        换到 `needs_api_key=False` 的提供方时，这条路径不该再被触发——
        见 test_key_gate_is_skipped_for_keyless_provider。
        """
        src = os.path.join(H.TESTS_DIR, "..", "scripts", "pipeline.py")
        with open(src, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("No API key for provider", body)
        # 旧文案（不点名提供方）不该再出现，否则用户不知道要配哪家的 key
        self.assertNotIn('"[error] No API key. Use --api-key', body)

    def test_key_gate_is_skipped_for_keyless_provider(self):
        """`needs_api_key=False` 时 `api_key_for_provider` 返回空串且不报错。

        这是"本地/离线提供方能进链路"的唯一判据；把它写成 `not api_key`
        就会让所有免费提供方卡在门口。
        """
        class Local(T.BaseTtsProvider):
            needs_api_key = False

        args = type("A", (), {"api_key": None})()
        self.assertEqual(P.api_key_for_provider(args, {}, Local()), "")
        # 需要 key 的提供方仍按原口径返回合并后的值
        mimo = T.make_client("mimo", "k", "https://x.example/v1")
        self.assertEqual(P.api_key_for_provider(args, {"MIMO_API_KEY": "env-k"}, mimo),
                         "env-k")
        self.assertEqual(P.api_key_for_provider(args, {}, mimo), "")


if __name__ == "__main__":
    unittest.main()
