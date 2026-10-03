#!/usr/bin/env python3
"""``--only`` 单段快渲的子集构造：从定稿 manifest 里切出一页（连同它的接续链），
把时间轴重基到 0，交回调用方去切音频、生成预览 HTML。

三条口径为什么长这样：

- **整条接续链都要带上**：``stage:"keep"`` 的起点是"上一页演完的画面"（见
  _stage_carry.py），只切请求页会让 _stage_carry_pass 报"没有可接续的内联画面"。
  快渲要复现的正是那条烘焙链，所以一路往前请到链首。
- **页窗口结束 = 下一页第一句的起点**：与 html_renderer 给 ``clip["vis"]`` 算
  win_end 同一口径（段间 gap 留在本页内），链不是全片最后一页时用它；是最后一页
  才用整片的尾部留白。节拍排在段尾时，预览和成片看到的是同一段留白。
- **sentence index 原样保留、只平移 start_time**：契约只要求 index 唯一且升序，
  保留原值能让预览产物和定稿 manifest 逐句对得上号（排查时不用换算）。

纯标准库、只读契约模块：不碰 TTS、不碰 ffmpeg、不碰渲染，全部可离线测。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# start_time 重基后的舍入位数：与 pipeline 写侧同一精度（round(x, 3)），
# 契约里段内句子与顶层句子的比对容差是 1e-6，两层必须引用同一个对象才不打架。
_TIME_ROUND = 3


def _span(seg):
    """(第一句起点, 最后一句终点) —— 原时间轴秒。"""
    ss = seg["sentences"]
    first = float(ss[0]["start_time"])
    last = float(ss[-1]["start_time"]) + float(ss[-1]["duration"])
    return first, last


def chain_sids(manifest, images, sid):
    """请求页连同它必需的接续前缀，按 manifest 顺序返回段 id 列表。

    往前走的唯一理由是 stage:"keep"；其余情况一律只切请求的那一页。
    """
    segs = manifest["segments"]
    order = [s["id"] for s in segs]
    if sid not in order:
        raise ValueError(f"段落 '{sid}' 不在 timing_manifest.json 里；"
                         "可选：" + "、".join(order))
    i = order.index(sid)
    chain = [sid]
    while (images or {}).get(order[i], {}).get("stage") == "keep":
        i -= 1
        if i < 0:
            raise ValueError(
                f"段落 '{order[0]}' 写了 stage: \"keep\"，但它就是 manifest 的第一段，"
                "没有前一段可接续——请先跑一次完整管线，或去掉这一页的 stage。")
        chain.insert(0, order[i])
    return chain


def build_subset(manifest, images, sid):
    """→ (子集 manifest, (t0, t1))：t0/t1 是**原时间轴**上的秒区间。

    返回的 manifest 不含 combined_audio（音频还没切出来），total_duration 先按
    t1-t0 填：调用方切完音频应拿实测时长覆盖它，这里是唯一一处"还没量过"的字段。
    """
    segs = manifest["segments"]
    by_id = {s["id"]: s for s in segs}
    order = [s["id"] for s in segs]
    chain = chain_sids(manifest, images, sid)
    last_pos = order.index(chain[-1])

    t0 = _span(by_id[chain[0]])[0]
    last_end = _span(by_id[chain[-1]])[1]
    if last_pos + 1 < len(order):
        # 后面还有页：本页在片中的窗口停在下一页开口说话之前
        t1 = _span(by_id[order[last_pos + 1]])[0]
    else:
        full_last_end = max(_span(s)[1] for s in segs)
        t1 = last_end + max(0.0, float(manifest["total_duration"]) - full_last_end)

    top = {s["index"]: s for s in manifest["sentences"]}
    wanted = set()
    for c in chain:
        wanted.update(s["index"] for s in by_id[c]["sentences"])
    rebased = []
    for idx in sorted(wanted):
        src = top.get(idx)
        if src is None:
            # 契约（segments 未覆盖全部句子 / 引用不存在的 index）正常会在
            # load_timing_manifest 就拦住；走到这里只可能是调用方绕过校验喂了
            # 手改的 dict —— 报清是哪一页哪一句，别抛 KeyError。
            raise ValueError(f"段落 '{chain[-1]}' 引用的 sentence index {idx} "
                             "不在 manifest['sentences'] 里")
        d = dict(src)
        d["start_time"] = round(float(src["start_time"]) - t0, _TIME_ROUND)
        rebased.append(d)
    by_index = {d["index"]: d for d in rebased}

    out_segs = []
    for c in chain:
        seg = dict(by_id[c])
        # 顶层与段内引用同一批对象：契约逐字段比对两层的时间轴，各重基一遍
        # 只要舍入差一个 ulp 就会被判"不一致"。
        seg["sentences"] = [by_index[s["index"]] for s in by_id[c]["sentences"]]
        out_segs.append(seg)

    subset = {
        "schema_version": manifest.get("schema_version", 2),
        "sentences": rebased,
        "segments": out_segs,
        "total_duration": round(t1 - t0, _TIME_ROUND),
    }
    for key in ("status", "degraded", "gap", "voice_id"):
        if key in manifest:
            subset[key] = manifest[key]
    if "closing" in chain and manifest.get("closing_cta") is not None:
        subset["closing_cta"] = manifest["closing_cta"]
    return subset, (t0, t1)


def subset_images(images, chain):
    """链上各页的 images.json 条目（浅拷贝，src 仍相对 HTML 所在目录）。"""
    return {sid: dict(images[sid]) for sid in chain if sid in (images or {})}
