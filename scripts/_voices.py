#!/usr/bin/env python3
"""Voice registry（内嵌）：MiMo TTS 预置音色。

CLI choices 与 source/manifest 契约校验的唯一权威来源，增删音色只改这里。
"""

# 元组：顺序就是 --help 与报错文案里的展示顺序；校验用的 `in` 只有 8 项，
# 不值得为它再维护一份 set。
VOICE_IDS = ("冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean")


def list_voice_ids():
    """全部预置音色 ID（按展示顺序）。"""
    return list(VOICE_IDS)


def is_valid_voice_id(voice_id):
    """voice_id 是否属于预置音色。"""
    return voice_id in VOICE_IDS
