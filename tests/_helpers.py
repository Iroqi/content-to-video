"""测试共用：把 scripts/ 放进 sys.path，并提供确定性的样例稿件 / manifest 构造器。"""
import copy
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(TESTS_DIR)
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
GOLDEN_DIR = os.path.join(TESTS_DIR, "golden")
sys.dont_write_bytecode = True
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

SAMPLE_SOURCE = {
    "title": "测试视频",
    "opening": "大家好，今天聊两件事。",
    "opening_title": "本期内容",
    "segments": [
        {"id": "seg-a", "title": "第一件事", "tagline": "背景", "takeaway": "记住第一件事",
         "text": "这是第一段的第一句。这是第一段的第二句，稍微长一点点。"},
        {"id": "seg-b", "title": "第二件事", "tagline": "进展", "accent": "#f59e0b",
         "layout": "canvas", "text": "这一段是整页画布。它没有字幕。"},
    ],
    "closing": "谢谢观看，下期再见。",
    "closing_title": "小结",
    "cta": "关注我们",
}

# 对话段样例：两个说话人、三段轮次（A → B → A），B 那一轮能分出两句
# ——用来覆盖"同一说话人连着说多句时不再重复挂标签"这条规则。
SAMPLE_DIALOGUE_SOURCE = {
    "title": "对谈测试",
    "speakers": {
        "host": {"voice_id": "茉莉", "label": "主播"},
        "guide": {"voice_id": "苏打", "label": "讲解"},
    },
    "segments": [
        {"id": "seg-a", "title": "反向传播", "tagline": "深度学习",
         "dialogue": [
             {"speaker": "host", "text": "这听起来很复杂，能简单说说吗？"},
             {"speaker": "guide", "text": "简单说，就是把误差往回传。一层层告诉参数怎么调。"},
             {"speaker": "host", "text": "那代价是什么？"},
         ]},
    ],
}


def sample_source():
    return copy.deepcopy(SAMPLE_SOURCE)


def sample_dialogue_source():
    return copy.deepcopy(SAMPLE_DIALOGUE_SOURCE)


def make_manifest(source=None, dur=2.0, gap=0.4):
    """用 build_parts 的真实输出拼一份合法的 timing_manifest（不碰 TTS / ffmpeg）。

    对话段会按 pipeline 的真实口径把说话人标签落进句子（turns 的局部区间 →
    句子级 `speaker`）——渲染端靠它挂字幕上的说话人标签，测试里少了这一步
    就测不到那条路径。
    """
    from build_from_structured import build_parts
    source = source or sample_source()
    sentences, segments = build_parts(source)
    flat, t = [], 0.0
    for i, text in enumerate(sentences):
        flat.append({"index": i, "text": text, "start_time": round(t, 3), "duration": dur})
        t += dur + gap
    total = round(t - gap, 3)
    segs = []
    for seg in segments:
        s = {k: v for k, v in seg.items() if k not in ("start", "end")}
        s["sentences"] = [dict(x) for x in flat[seg["start"]:seg["end"]]]
        # 与 pipeline 的 sentence_speaker_labels 同一口径：turn 的 label 落到
        # 它覆盖的每一句上（不写 label 时 build_from_structured 已回退成
        # speaker 名，这里照搬，不再自己兜一次）。
        for tn in seg.get("turns") or []:
            for i in range(tn["start"], tn["end"]):
                if 0 <= i < len(flat):
                    s["sentences"][i - seg["start"]]["speaker"] = tn["label"]
                    flat[i]["speaker"] = tn["label"]
        segs.append(s)
    manifest = {
        "schema_version": 2, "status": "ok", "degraded": {},
        "sentences": flat, "total_duration": total, "gap": gap,
        "voice_id": "冰糖", "segments": segs,
    }
    if source.get("cta"):
        manifest["closing_cta"] = source["cta"]
    return manifest


def sample_images(manifest):
    """给每个需要配图的段落一个占位映射（生成 HTML 只读路径，不读文件）。"""
    from _segments import sids_needing_image
    return {sid: {"src": f"images/{sid}.svg"} for sid in sids_needing_image(manifest)}


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()
