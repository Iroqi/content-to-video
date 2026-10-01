#!/usr/bin/env python3
"""Voice registry（内嵌）：MiMo TTS 预置音色。

CLI choices 与 source/manifest 契约校验的唯一权威来源，增删音色只改这里。
"""

VOICE_IDS = ["冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean"]


def list_voice_ids():
    """返回全部预置音色 ID（副本），供 CLI --voice-id 的 choices 动态生成。"""
    return list(VOICE_IDS)


def is_valid_voice_id(voice_id):
    """判断 voice_id 是否属于预置音色。"""
    return voice_id in VOICE_IDS
