#!/usr/bin/env python3
"""Content-to-Video — 结构化输入（segments_source.json）→ 句子列表 + 段落分组。

pipeline.py --source 直接调用本模块的 build_parts()：逐段独立分句，跨段
短句合并结构上不可能发生，不需要任何跨段校验。

segments_source.json 的字段规范与完整示例见 `references/writing.md`；
本模块只负责按 _contracts 校验过的结构分组。
"""
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List

sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import split_sentences  # noqa: E402  复用同一份分句逻辑，杜绝两边漂移
from _theme import get_accent_palette, get_default_accent  # noqa: E402
from _contracts import OPENING_CLOSING_DEFAULT_SPEED  # noqa: E402

ACCENT_PALETTE = get_accent_palette()
DEFAULT_ACCENT = get_default_accent()

# 超长句提醒阈值：字幕二次切行（split_subtitle_lines）是显示层兜底，
# 念稿节奏的根治方式还是写稿时控制在一句一口气能念完的长度。
LONG_SENTENCE_CHARS = 45


# ── 内部数据结构 ────────────────────────────────────────────────
@dataclass
class Block:
    """_collect_blocks 的产物，描述一个待渲染段落的全部信息。

    turns 为空列表表示普通独白段落；非空表示"双人/多人对话"段落，每个 turn
    记录该轮在本段 sentences 中的局部区间 + 说话人（voice_id/voice_style
    已解析，pipeline.py 不需要再反查 speakers 字典）。
    """
    id: str
    title: str
    tagline: str
    accent: str
    sentences: List[str]
    extra: Dict = field(default_factory=dict)
    turns: List[Dict] = field(default_factory=list)


def _sentences_of(text):
    return split_sentences(text.strip())


def _collect_dialogue_sentences(dialogue, speakers, seg_index, seg_title):
    """把一个段落的 'dialogue'（多轮对话）拆成扁平句子列表 + 局部 turns。

    每一轮独立调用 split_sentences（同样杜绝"短句被并入下一轮"这类跨轮错位），
    turns 记录每一轮在本段内的局部句子区间 + 说话人信息（voice_id/voice_style
    在这里就近解析好，pipeline.py 不需要再反查 speakers 字典）。

    turn 的 speaker/text 非空由 _contracts.validate_segments_source 把关，这里
    不再重复拦；只留契约管不到的"分句后为空"——纯标点文案能过非空检查却分不出句。
    """
    sents = []
    turns = []  # {start, end, speaker, label, voice_id, voice_style} 局部区间（段内 0-based）
    for j, turn in enumerate(dialogue, 1):
        spk = turn.get("speaker")
        t_sents = _sentences_of((turn.get("text") or "").strip())
        if not t_sents:
            raise ValueError(f"第 {seg_index} 段（title={seg_title!r}）"
                              f"dialogue[{j}]（speaker={spk!r}）分句后为空，"
                              "请检查文本是否以终止标点（。！？）结尾")
        spk_cfg = speakers.get(spk, {})
        start = len(sents)
        sents.extend(t_sents)
        turns.append({
            "start": start, "end": len(sents), "speaker": spk,
            "label": spk_cfg.get("label") or spk,
            "voice_id": spk_cfg.get("voice_id"),
            "voice_style": spk_cfg.get("voice_style"),
        })
    return sents, turns


def _collect_blocks(source):
    """把结构化 source 按 开场 → 各段 → 结尾 的顺序组装成 Block 列表。"""
    blocks: List[Block] = []
    speakers = source.get("speakers") or {}

    # 开场/结尾的大标题：可配置，避免所有视频都顶着同样的默认标题。
    # 默认 "本期内容"/"小结" 是中性兜底，agent 写稿时应按视频主题显式设置
    # opening_title（例如数学视频设成 "欧拉恒等式"）。
    opening_title = (source.get("opening_title")
                     or source.get("title")
                     or "本期内容")
    closing_title = source.get("closing_title") or "小结"
    opening_tagline = (source.get("opening_tagline") or "").strip()
    closing_tagline = (source.get("closing_tagline") or "").strip()
    # 契约把显式 null 视同缺省（_validate_text 对 None 直接放行），这里
    # 用 `or ""` 兜底，否则 get 的默认值不生效、None.strip() 抛裸 AttributeError
    opening_text = (source.get("opening") or "").strip()
    if opening_text:
        sents = _sentences_of(opening_text)
        if not sents:
            raise ValueError("opening 分句后为空（纯标点/终止符文本），"
                             "请写成完整句子或删掉该字段")
        blocks.append(Block(
            id="opening",
            title=opening_title,
            tagline=opening_tagline,
            accent=DEFAULT_ACCENT,
            sentences=sents,
            # `or` 而非 get 默认值：契约把显式 null 视同缺省放行（_validate_
            # text/validate_speed 对 None 不报错），get(key, default) 会把
            # null 原样取出来让 None 一路流进 seg speed。
            extra={"speed": source.get("opening_speed")
                   or OPENING_CLOSING_DEFAULT_SPEED},
            turns=[],
        ))

    # segments 非空由 _contracts.validate_segments_source 把关（build_parts
    # 唯一入口 pipeline --source 先过它），这里不再重复拦。
    raw_segments = source.get("segments", [])

    # 段落 id：可选的显式 "id" 让配图键（images.json）与配图文件名在改稿/
    # 重排后仍然稳定；缺省退回按序号的 seg{n}。_contracts 已校验显式 id 的
    # 字符集与唯一性，这里只需处理"部分段有 id、部分没有"时的命名冲突。
    explicit_ids = {(s.get("id") or "").strip()
                    for s in raw_segments} - {""}
    for i, seg in enumerate(raw_segments, 1):
        title = seg.get("title", "")
        dialogue = seg.get("dialogue")
        turns_local = []
        if dialogue:
            sents, turns_local = _collect_dialogue_sentences(dialogue, speakers, i, title)
        else:
            # 'text' 非空同样由 _contracts 把关（与 dialogue 分支同一口径）
            text = (seg.get("text") or "").strip()
            sents = _sentences_of(text)
            if not sents:
                raise ValueError(f"第 {i} 段（title={title!r}）分句后为空，"
                                  f"请检查文本是否以终止标点（。！？）结尾")
        accent = seg.get("accent") or ACCENT_PALETTE[(i - 1) % len(ACCENT_PALETTE)]
        extra = {}
        if seg.get("speed") is not None:
            extra["speed"] = seg["speed"]
        # 段落级音色覆盖（可选字段，规范见 references/writing.md）：_contracts
        # 校验之后由这里透传给下游，pipeline.py 读 seg_config 后即可生效。
        if seg.get("voice_id") is not None:
            extra["voice_id"] = seg["voice_id"]
        if seg.get("voice_style") is not None:
            extra["voice_style"] = seg["voice_style"]
        # 结尾 agenda 要点总结用的"一句话结论"：缺省回退标题（renderer 处理）。
        if seg.get("takeaway") is not None:
            extra["takeaway"] = str(seg["takeaway"]).strip()
        # 缺省段落 id 用中性前缀 seg（配图键与文件名同源）。
        sid = (seg.get("id") or "").strip()
        if not sid:
            sid = f"seg{i}"
            if sid in explicit_ids:
                raise ValueError(f"第 {i} 段缺少显式 'id'，且缺省名 '{sid}' 与"
                                 "其它段落显式声明的 id 冲突——请为该段也显式设置 id")
        blocks.append(Block(
            id=sid,
            title=title,
            tagline=seg.get("tagline", ""),
            accent=accent,
            sentences=sents,
            extra=extra,
            turns=turns_local,
        ))

    closing_text = (source.get("closing") or "").strip()
    if closing_text:
        sents = _sentences_of(closing_text)
        if not sents:
            raise ValueError("closing 分句后为空（纯标点/终止符文本），"
                             "请写成完整句子或删掉该字段")
        blocks.append(Block(
            id="closing",
            title=closing_title,
            tagline=closing_tagline,
            accent=DEFAULT_ACCENT,
            sentences=sents,
            extra={"speed": source.get("closing_speed")
                   or OPENING_CLOSING_DEFAULT_SPEED},
            turns=[],
        ))
    return blocks


def _segments_from_blocks(blocks):
    """按顺序累加 start/end 生成 segments。"""
    segments = []
    cursor = 0
    for blk in blocks:
        start, end = cursor, cursor + len(blk.sentences)
        cursor = end
        seg_obj = {
            "id": blk.id, "title": blk.title, "tagline": blk.tagline,
            "accent": blk.accent, "start": start, "end": end,
        }
        seg_obj.update(blk.extra)
        if blk.turns:
            # 局部区间 -> 全局区间（加上本段的 start 偏移），供 pipeline.py
            # 按句覆盖 voice_id/voice_style + 记录说话人标签。
            seg_obj["turns"] = [
                {
                    "start": start + t["start"], "end": start + t["end"],
                    "speaker": t["speaker"], "label": t["label"],
                    **({"voice_id": t["voice_id"]} if t["voice_id"] else {}),
                    **({"voice_style": t["voice_style"]} if t["voice_style"] else {}),
                }
                for t in blk.turns
            ]
        segments.append(seg_obj)
    return segments


def build_parts(source):
    """把结构化 source 转成 (sentences, segments)，供 pipeline --source 使用。

    对每个段落**独立**分句（不拼接成整篇再重分句）——结构化输入下每段是
    独立字符串，跨段短句合并结构上不可能发生，没有对应的失败模式。

    段落语速：只有显式声明的 speed（以及 opening/closing 的默认 1.2）进
    segments；全局 --speed 由 pipeline 侧 `sentence_speeds.get(i, args.speed)`
    唯一兜底，这里不再复读一份。

    超长句（> LONG_SENTENCE_CHARS 字）只打 [warn] 不拦截：显示层会做次要
    标点切行兜底（_script_utils 的 split_subtitle_lines），但 45+ 字的
    一句话念出来也偏喘不过气，根治方式是写稿时拆成两句——warn 就是提醒
    agent 这么做。

    Returns:
        sentences: list[str]，按段落顺序排列的全部句子
        segments: 段落分组（id/title/tagline/accent/start/end/
                  可选 speed/voice_id/voice_style/turns）
    """
    blocks = _collect_blocks(source)
    sentences = []
    for blk in blocks:
        sentences.extend(blk.sentences)
    segments = _segments_from_blocks(blocks)
    for idx, s in enumerate(sentences, 1):
        if len(s) > LONG_SENTENCE_CHARS:
            print(f"[warn] 第 {idx} 句长达 {len(s)} 字（>{LONG_SENTENCE_CHARS}），"
                  f"念稿易喘不过气、字幕也需要切多行：{s[:24]}…"
                  "建议写稿时在逗号处拆成两句", file=sys.stderr)
    return sentences, segments
