"""Director 生成期体检与净化内联。

生成器在写数据胶之前对 director 指令做的四类体检（at 越界/target落空/运镜裁切/
节拍出窗）、SVG 净化内联回填、stage:"keep" 跨段烘焙第二遍、媒体完整性校验与画布
档按文件门禁都在这里。渲染端（remotion/src/components/director.ts）只负责逐帧
求值，这里算的落点/烘焙副本/内联串是它共同的输入。
"""
import os
import re
import sys
import xml.etree.ElementTree as ET  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _template import load_template  # noqa: E402
from _segments import seg_layout  # noqa: E402
from _timeline import beat_positions, beat_span, beat_cycles  # noqa: E402
from _cam_crop import crop_warnings  # noqa: E402
from _svg_sanitize import sanitize_svg_for_inline  # noqa: E402
from _stage_carry import bake_settled_state, global_ref_leaks  # noqa: E402
from _images_schema import classify_media_path, is_svg_path  # noqa: E402
from _script_utils import is_inside  # noqa: E402
from _audio import get_ffmpeg, ffmpeg_usable, parse_duration  # noqa: E402


def validate_images_files(images, out_dir, seg_durs=None):
    """校验 images.json 引用的媒体文件存在且可解码。

    检测项（依赖缺失时优雅降级，只降强度不改行为）：
    - 存在性：所有类型（含 svg）；
    - 图片/视频可解码：ffmpeg 全解码探测（能同时发现 moov 缺失、头部
      损坏与尾部截断——下载中断的典型形态），并解析视频时长，比段落
      长时打"渲染只显示前段"提示（信息级，不阻断）；svg 是文本格式，
      ffmpeg 打不开，存在性校验已足够，跳过。ffmpeg 是渲染必需依赖，
      无需再引入 Pillow。

    返回 (missing, corrupt)：missing=[(sid, media_path)]、
    corrupt=[(sid, media_path, reason)]；相对 src 按 out_dir 解析，
    seg_durs=None 时跳过时长提示。是否 fail-fast 由调用方决定。
    """
    missing_imgs = []
    corrupt_imgs = []
    # 图片/视频完整性都用 ffmpeg 全解码探测（ffmpeg 是渲染必需依赖，
    # 无需再引入 Pillow）；找不到 ffmpeg 时降级为仅做存在性校验。
    # get_ffmpeg() 从不抛异常（末位回退是字面量 "ffmpeg"），所以"能不能用"
    # 只能问 ffmpeg_usable——靠 try/except 接一个不会发生的异常会让下面的
    # 降级警告永远打不出来，而"没有 ffmpeg"恰恰是它唯一要报的情况。
    _ffmpeg_probe = get_ffmpeg()
    if not ffmpeg_usable(_ffmpeg_probe):
        _ffmpeg_probe = None

    def _probe_media_ok(path):
        """ffmpeg 全解码探测，覆盖图片与视频。能同时发现 moov 缺失、
        头部损坏与尾部截断（下载中断的典型形态）。-v info 让 stderr
        携带 Duration 行，成功时顺带解析出视频时长（用于"视频比段落
        长会被截断"提示）。返回 (ok, reason, duration|None)。"""
        import subprocess as _sp
        try:
            r = _sp.run(
                [_ffmpeg_probe, "-v", "info", "-i", path,
                 "-f", "null", "-"],
                capture_output=True, timeout=15)
        except _sp.TimeoutExpired:
            return False, "probe timeout(15s)", None
        except OSError:
            # 兜底：调用方已用 ffmpeg_usable 预筛过，走到这里只剩"预筛之后
            # ffmpeg 才消失"这类竞态。当真报错会把"环境问题"说成"图片损坏"，
            # 所以退回存在性校验（文件存在在调用前已查过），不当损坏处理。
            return True, "", None
        err_text = (r.stderr or b"").decode("utf-8", "replace")
        if r.returncode != 0:
            return False, (err_text.strip()[-160:] or
                           f"exit {r.returncode}"), None
        return True, "", parse_duration(err_text)

    for sid, entry in images.items():
        # 上游 validate_images_json 已保证每条都是媒体对象、src 必填非空
        media_type = entry.get("type", "auto")
        media_path = entry["src"]
        # .mp4 路径若被当图片送 ffmpeg 会误报"损坏"，先按扩展名推断再分流
        if media_type == "auto":
            media_type = classify_media_path(media_path)
        # src 已由 validate_images_json 归一成相对项目根路径；即便绝对路径漏进来，
        # os.path.join 遇绝对第二参数直接返回它，下面 is_inside 兜住越界。
        p = os.path.join(out_dir, media_path)
        if not os.path.exists(p):
            missing_imgs.append((sid, media_path))
            continue
        if not is_inside(p, out_dir):
            corrupt_imgs.append((sid, media_path,
                                 "媒体路径通过软链接或绝对路径越出项目目录"))
            continue
        poster = entry.get("poster")
        # poster 的问题只记录、不提前 continue：同一条目的 src 若也有毛病，必须
        # 一起报出来（先 continue 会让人以为"只是封面缺了"，修完才发现视频本身
        # 还是坏的，白跑一轮）。
        if poster:
            poster_path = os.path.join(out_dir, poster)
            if not os.path.isfile(poster_path):
                missing_imgs.append((sid, poster))
            elif not is_inside(poster_path, out_dir):
                corrupt_imgs.append((sid, poster,
                                     "poster 路径通过软链接或绝对路径越出项目目录"))
        # 视频用 ffmpeg 解码探测（只查存在性不够：
        # 截断/损坏的 mp4 要到渲染时才炸，白烧一整轮渲染时间）
        if media_type == "video":
            if _ffmpeg_probe:
                ok, reason, vdur = _probe_media_ok(p)
                if not ok:
                    corrupt_imgs.append((sid, media_path,
                                         f"视频解码失败: {reason}"))
                elif vdur is not None:
                    seg_dur = (seg_durs or {}).get(sid, 0)
                    if seg_dur > 0 and vdur > seg_dur + 0.05:
                        print(f"[warn] 视频 {vdur:.1f}s 超过段落 "
                              f"'{sid}' 时长 {seg_dur:.1f}s，渲染只显示"
                              f"前 {seg_dur:.1f}s（尾部内容会被截断）。"
                              f"请剪短视频或换到更长的段落。",
                              file=sys.stderr)
            continue
        # svg 是文本格式，ffmpeg 打不开，存在性校验已足够
        if is_svg_path(p):
            continue
        # 栅格图（jpg/png/webp/gif…）用 ffmpeg 全解码探测损坏/截断——
        # ffmpeg 是渲染必需依赖，无需再引入 Pillow
        if _ffmpeg_probe:
            ok, reason, _dur = _probe_media_ok(p)
            if not ok:
                corrupt_imgs.append((sid, media_path,
                                     f"图片损坏或无法解码: {reason}"))
    if not _ffmpeg_probe and images:
        print("[warn] ffmpeg 不可用，跳过配图完整性（损坏/截断）校验，"
              "仅做了文件存在性校验（ffmpeg 是渲染必需依赖，正常环境"
              "不会出现此降级）", file=sys.stderr)
    return missing_imgs, corrupt_imgs


def _svg_intrinsic_size(path):
    """从 SVG 文件头取固有像素尺寸：优先根标签的 width/height（纯数字，
    带 % / em 的不算像素，直接不认），拿不到再退回 viewBox 的第 3/4 段。
    读不出返回 None（调用方按"没法验"报错，不静默放行）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError:
        return None
    tag = re.search(r"<svg\b[^>]*>", head, re.S)
    if not tag:
        return None
    tag = tag.group(0)

    def _px(name):
        # 允许 "980" 与 "980px" 两种写法：check_svg._parse_num 同样接受 px 后缀，
        # 两边口径一致，手绘 SVG 才不会一边过检一边被这里判"读不出尺寸"。
        # (?<![\w-]) 限定属性名左边界，否则 min-width/max-height 也会被当成
        # width/height 命中（check_svg 用 root.get("width") 天然不会）。
        m = re.search(rf'(?<![\w-]){name}="\s*([\d.]+)(?:\s*px)?\s*"', tag)
        return float(m.group(1)) if m else None

    sw, sh = _px("width"), _px("height")
    if sw and sh:
        return sw, sh
    vb = re.search(r'\bviewBox="\s*[-\d.]+[,\s]+[-\d.]+[,\s]+([\d.]+)[,\s]+([\d.]+)\s*"', tag)
    if vb:
        w, h = float(vb.group(1)), float(vb.group(2))
        if w and h:
            return w, h
    return None


CANVAS_MIN_FONT_PX = 26


def canvas_layout_errors(images, segments, out_dir, canvas_w, canvas_h):
    """整页画布（段落 layout: "canvas"）的配图体检，返回 (错误, 警告) 两组说明。

    四件事只有这里查得到（画布版式不生成标题层与句子流层，画面全靠那张图）：
    - 有配图：没图就只剩一条进度条空帧——error。
    - SVG 与当前画幅**等比**：槽位版式里 cover 多裁一点边只是留白变窄；画布
      版式的标题和文字就画在图内，比例一错就被整块裁到画面外。实测把 3:4 的
      画布塞进 16:9 段落，标题与 kicker 直接消失，而渲染器依旧
      0 error、Contrast 38/38 全过——门禁看不见 SVG 里的字，只能在这里按文件拦。
    - SVG 的**缩放档**：等比不等于能读。按 2560×1440 画的 16:9 塞进 1920 画幅，
      比例对、整张图却被缩到 0.75，图内 26px 折算后只剩 19.5px——低于
      `references/image_options.md`「文字与尺寸」的 26px 下限就是读不出来，
      这条也只有在这里量得到。
    - 文案下落：这一段的 N 句旁白不会出现在画面上，图内的 `<text>` 数量是唯一
      能对上的账——warn，因为"把要点画进图里"本来就没有机械判据。
    照片/视频不验比例与字号（它们没有"画在里面的字"，cover 裁边是正常取景），
    但整段无字是后果，照样给一条 warn 让人知情。
    """
    errs, warns = [], []
    for seg in segments:
        if seg_layout(seg) != "canvas":
            continue
        # id/src/sentences 都由契约把守（validate_timing_manifest、
        # validate_images_json），消费端直接下标，不再留永不可达的兜底
        sid = seg["id"]
        entry = images.get(sid)
        if not entry:
            errs.append(f"段落 '{sid}' 声明了 layout=\"canvas\"（整页画布），"
                        f"images.json 里却没有它的配图——画布版式不画标题层和"
                        f"句子流层，没有配图就什么都不剩")
            continue
        src = entry["src"]
        path = os.path.join(out_dir, src)
        n_sent = len(seg["sentences"])
        if not is_svg_path(src):
            warns.append(f"段落 '{sid}' 的整页画布配图是 {src}（不是 SVG）——"
                         f"画布版式不生成标题层与句子流层，照片/视频里也没有"
                         f"图内文字，这一整段画面上不会出现任何文字。"
                         "若这就是要的效果可忽略本条；想留标题就把这一段的要点"
                         "画成 SVG")
            continue
        size = _svg_intrinsic_size(path)
        if size is None:
            errs.append(f"段落 '{sid}' 的画布 {src} 读不出固有尺寸：根标签要有"
                        f"纯数字的 width/height（% 不算）或 viewBox，否则没法验比例")
            continue
        sw, sh = size
        if abs(sw * canvas_h - sh * canvas_w) > 0.002 * canvas_w * canvas_h:
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，与当前画幅 "
                        f"{canvas_w}×{canvas_h} 不等比——object-fit:cover 会把画在"
                        f"图内的标题整块裁到画面外，而 check 看不见这种丢失。"
                        f"按当前画幅重画这张 SVG（两画幅各出一版）")
            continue
        # 等比之后才算缩放：成片把 SVG 按 canvas_w/sw 放大或缩小，图内每个
        # 字号都乘这个系数。只有缩小的方向会跌破可读下限。
        scale = canvas_w / sw
        min_px, n_text = _svg_text_metrics(path)
        if min_px is not None and min_px * scale < CANVAS_MIN_FONT_PX:
            errs.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                        f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，图内最小"
                        f"字号 {min_px:g}px 折算后只有 {min_px * scale:.1f}px，低于 "
                        f"{CANVAS_MIN_FONT_PX}px 下限（成片压缩编码后读不出来）。"
                        f"按当前画幅原尺寸重画：竖屏 1080×1440、横屏 1920×1080")
        elif min_px is None and scale < 0.999:
            warns.append(f"段落 '{sid}' 的画布 {src} 是 {sw:g}×{sh:g}，铺进 "
                         f"{canvas_w}×{canvas_h} 会整体缩到 {scale:.2f} 倍，但文件里"
                         f"读不到 px 字号（写成 class / em / 相对单位了）——"
                         f"折算后是否还够 {CANVAS_MIN_FONT_PX}px 下限没人验得了，"
                         f"按画幅原尺寸画就不用猜")
        warns.append(f"段落 '{sid}' 整页画布：{n_sent} 句旁白不会出现在画面上"
                     f"（标题层与句子流层都不生成），这张 SVG 里有 {n_text} 个"
                     " <text>——请确认要点已经画进图内；句子越少越要确认")
    return errs, warns


def _svg_selectable_tokens(raw):
    """SVG 里可被 director target 命中的选择器集合：{'#id', ..., '.class', ...}。

    target 白名单允许 id 或 class（class 专为 stagger 一组元素开），存在性校验就得
    两类都收：一个 class 属性可能写多个空格分隔的类名，逐个拆进去。净化器保留 class
    （只摘 on*/外链 href），所以这里从源文本收的类名和内联后 DOM 上的一致。
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return set()
    toks = set()
    for el in root.iter():
        eid = el.get("id")
        if eid:
            toks.add("#" + eid)
        cls = el.get("class")
        if cls:
            for c in cls.split():
                toks.add("." + c)
    return toks


_BEAT_EPS = 0.005   # 落点已与时间轴同精度（round 2 位），这里只吸收浮点尾巴


def _director_default_duration():
    """模板 animation.director.duration——没写 duration 的 step 用它，相对链游标也用它。
    必须与渲染端取的是同一份，否则门禁算出的落点不是渲染器真正用的时刻。"""
    return float(load_template()["animation"]["director"]["duration"])


def _beat_anchor(step):
    """这一步的锚点写法原样打印，供 warn/报告指认是哪一步。"""
    if "at_time" in step:
        return "at_time=%s" % (step["at_time"],)
    return "at=%s" % (step["at"],)


def _beat_outside(pos, seg_start, next_start):
    """这一拍的起点是否在成片里根本看不见：是则返回 "before" / "after"，否则 None。

    段尾（末句说完到下一页盖过来之间）**不算窗外**：这一页还挂在屏幕上，落在那里的
    补间照样演。所以右界取"下一段起点"而不是"末句结束"——只有过了它才真的看不见。
    """
    if pos < seg_start - _BEAT_EPS:
        return "before"
    if next_start is not None and pos > next_start + _BEAT_EPS:
        return "after"
    return None


def _beat_window_warnings(steps, sentences, seg_start, next_start, dflt_dur, keep=False):
    """beat 落在本页可见窗口之外（before/after/被切半路）→ warn 文案。

    引擎按 data-start/data-duration 硬切 clip 可见性（实测排在窗口外的补间等于没写），
    所以这类落点不是"稍微偏"，是那一拍在成片里**根本不演**。warn 不 error：落点是作者
    写的，人可能就是要它压在下一页上。最后一段之后没有页盖过来，页面一直挂着，所以
    next_start 为 None 时不设右界（宁漏不误报）。解析与渲染端共用 beat_positions，
    门禁算的时刻与渲染器用的时刻不可能漂移。

    除了起点出窗，还报**起点在内、终点越界**的那一类：补间演到一半就被下一页盖过来，
    作者以为的终态在成片里从没出现过（`keep=True` 时更要紧——接续烘焙把时间轴折到最后
    一帧，它搬的正是那个没演到的终态）。这里只判时刻，不去算缓动走了几成：那要把 ease
    求值到页界，而"演到一半"本来就是该改节拍而不是该被精确复刻的东西。
    """
    try:
        beats = beat_positions(steps, sentences, round(seg_start, 2), dflt_dur)
    except ValueError:
        return []   # at 越界由 director_prepare 的句序检查按步报错，这里不重复
    out = []
    for i, ((pos, dur), step) in enumerate(zip(beats, steps)):
        kind = _beat_outside(pos, seg_start, next_start)
        anchor = _beat_anchor(step)
        if kind == "before":
            out.append(f"director.steps[{i}]（{step['target']}，{anchor}）落点 {pos:.2f}s "
                       f"比本段旁白起点 {seg_start:.2f}s 还早 {seg_start - pos:.2f}s——这一页"
                       "那时还没出现，补间排在 clip 窗口外等于没演")
        elif kind == "after":
            out.append(f"director.steps[{i}]（{step['target']}，{anchor}）落点 {pos:.2f}s 已过"
                       f"下一段旁白起点 {next_start:.2f}s——这一页那时已被下一页盖住，等于没演。"
                       "多半是 at_time 按旧配音手算后过期了：跟旁白的节拍改用 at（句序锚），"
                       "或把秒数调小")
        elif next_start is not None:
            # 跨度按 beat_span 算：写了 repeat 的这一拍要演好几遍，只按单遍 duration 判
            # 就是四倍误差——"演到一半被盖过来"的门禁必须拿成片里真正的那个大括号。
            # repeat:-1 没有终点，也不报：它本来就要被页界切掉，报它是训练作者忽略 warn。
            span = beat_span(step, dur)
            if span is not None and pos + span > next_start + _BEAT_EPS:
                n = beat_cycles(step)
                again = f"（演 {n} 遍、共 {span:.2f}s）" if n and n > 1 else ""
                out.append(f"director.steps[{i}]（{step['target']}，{anchor}）从 {pos:.2f}s 演到 "
                           f"{pos + span:.2f}s 才完{again}，而下一段旁白 {next_start:.2f}s 就把这一页盖"
                           f"过来——这一拍被切在半路，它要收的那个尾在成片里从没出现过（早 "
                           f"{pos + span - next_start:.2f}"
                           "s）。要么把 duration/delay/repeat 收紧到本页内，要么让它在接续页里重演一遍"
                           + ("；这一页写了 stage:\"keep\"，接续烘焙搬走的就是那个没演到的终态"
                              if keep else ""))
    return out


def next_speech_start(segments, sid):
    """紧邻其后的那一段旁白起点（绝对秒）；没有下一段返回 None。

    按 start_time 取"比本段晚的最近一段"，不靠列表相邻：manifest 的段序就是时间序，
    但孤儿配图/结构段可能让 sid 的邻居不是时间上的下一页。
    """
    mine = None
    for seg in segments:
        if seg["id"] == sid:
            mine = float(seg["sentences"][0]["start_time"])
            break
    if mine is None:
        return None
    cands = [float(s["sentences"][0]["start_time"]) for s in segments
             if float(s["sentences"][0]["start_time"]) > mine + _BEAT_EPS]
    return min(cands) if cands else None


def director_prepare(images, segments, out_dir):
    """方式 C SVG「导演」编排的生成期体检 + 净化内联，返回 (错误, 警告)。

    写了 director 的条目才走这条路：读盘 → 净化（_svg_sanitize 去掉 script/on*/
    外链/SMIL/墙钟动画，root 改成 cover 语义）→ 回填 entry["inline_svg"]，渲染端
    据此把 SVG 内联成活 DOM，Remotion 求值器才能逐帧驱动图内命名元素
     （见 remotion/src/components/director.ts）。五件事只有这里查得到：

    - at 越界：写了 steps[i].at 时，其整数部分必须 < 该段旁白句数（0 基句序）——越界会让
      渲染端 raise，这里提前按段落点名。用 at_time（段落绝对秒）的步骤没有句序可锚，跳过此检。
    - target 落空：steps[i].target 指的 id 必须在 SVG 里真实存在。落空不是报错
      而是**静默无动画**（选择器匹配不到任何元素），成片看着"没动"却全程零提示，
      所以按文件拦成 error。
    - 运镜裁切：`#cam` 里的内容被 scale/平移推到画幅**静止位之外**（见 _cam_crop），warn。
    - 节拍出窗：每一步的落点按 _timeline.beat_positions 解析成绝对秒，落在本页可见窗口
      之外（早于本段旁白 / 晚于下一段起点）warn——那是"这一拍根本不演"，`at_time` 过期
      最常见；起点在窗内、终点越过下一段起点的也 warn（那一拍被切在半路，终态从没出现过）。
    - 净化说明：删掉了哪些不安全/墙钟构造，按 warn 让人知情（决定性的代价写在明面）。
    - 跨段延续（stage:"keep"）：写完上面这些，再按 manifest 段序走第二遍，把上一页
      演完的画面烘焙成下一页的内联副本（见 _stage_carry_pass）。那一遍还要替接续页补做
      运镜自查——第一遍按盘上原图算的投影对接续页是假话（原图里没有上一页推近的几何）。

    非 SVG 挂 director 已由 _images_schema 在契约层拒掉，这里不重复。
    """
    errs, warns = [], []
    seg_by_id = {seg["id"]: seg for seg in segments}
    dflt_dur = _director_default_duration()   # 与渲染端同一份缺省，否则落点算错
    for sid, entry in (images or {}).items():
        director = entry.get("director")
        # 接续页：起点不是盘上原图，而是上一页演完的画面（见 _stage_carry_pass）。
        carried = entry.get("stage") == "keep"
        if not director:
            continue
        src = entry["src"]
        path = os.path.join(out_dir, src)
        if not os.path.isfile(path):
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 在 {out_dir} 下不存在")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except OSError as e:
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 读不了: {e}")
            continue
        try:
            markup, notes = sanitize_svg_for_inline(raw)
        except ValueError as e:
            errs.append(f"段落 '{sid}' 的 director 配图 {src} 无法净化内联: {e}")
            continue
        for n in notes:
            warns.append(f"段落 '{sid}' 的 director 配图 {src}：{n}")
        ids = _svg_selectable_tokens(raw)
        seg = seg_by_id.get(sid)
        n_sent = len(seg["sentences"]) if seg else 0
        for i, step in enumerate(director["steps"]):
            if seg is None:
                errs.append(f"段落 '{sid}' 写了 director，但 manifest 里没有这一段")
                break
            # at 才有句序越界可验；at_time 锚在段落绝对时间（含相对串），与旁白句数无关，跳过。
            if "at" in step:
                at = step["at"]
                if int(at) >= n_sent:
                    errs.append(f"段落 '{sid}' 的 director.steps[{i}] at={at} 越界："
                                f"该段只有 {n_sent} 句旁白（at 的整数部分=句序，最大 {n_sent - 1}）")
            target = step["target"]
            if target not in ids:
                errs.append(f"段落 '{sid}' 的 director.steps[{i}] target={target} 在 "
                            f"{src} 里找不到对应 id/class 的元素——选择器落空不会报错，只会"
                            "静默没有动画。给该元素补上这个 id/class，或改 target")
        entry["inline_svg"] = markup
        # 运镜裁切：源码看着坐标都在幅内，是 scale 把内容推出去的，只有这里同时握有
        # SVG 原文与 steps（判据与口径见 _cam_crop 模块头）。warn 不 error：途中出画是
        # 合法演法，这里只报"静止位看不见的内容"，而那也可能是作者故意的取舍。
        # 净化后的串就够用（sanitize 只改 root 的 cover 语义、剥脚本，viewBox 与几何不动）。
        # 接续页（stage:"keep"）跳过：起点不该是盘上原图而是上一页演完的画面，
        # 由 _stage_carry_pass 拿烘焙后的副本重算，免得两遍各报一套互相矛盾的越界。
        if not carried:
            for w in crop_warnings(markup, director.get("steps")):
                warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
        # 节拍↔旁白对轴：at_time 是按某一版配音手算的绝对秒，重配音/改句长/动 --gap
        # 之后它整体错位，而落在 clip 窗口外的那一拍是**静默不演**（引擎按
        # data-start/data-duration 硬切可见性）。这里同时握有 steps 与 manifest 句子，
        # 是整条链上唯一算得出落点的地方。warn 不 error：压在下一页可能正是要的效果。
        if seg:
            for w in _beat_window_warnings(director["steps"], seg["sentences"],
                                           float(seg["sentences"][0]["start_time"]),
                                           next_speech_start(segments, sid), dflt_dur,
                                           keep=carried):
                warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
    _stage_carry_pass(images, segments, dflt_dur, errs, warns)
    return errs, warns


def _director_beats(steps, seg, dflt_dur):
    """steps → [(绝对秒, 时长)]，锚在段落首句起点；解析不动时返回 None。

    只服务"按时刻结算收尾态"的排序需求（_stage_carry._ordered），所以 at 越界这类
    已经由别处按步报过的错，这里退回数组序兜底就好——再报一遍只会把同一条错误说两次。
    """
    try:
        return beat_positions(steps, seg["sentences"],
                              round(float(seg["sentences"][0]["start_time"]), 2),
                              dflt_dur)
    except (ValueError, KeyError, IndexError):
        return None


def _stage_carry_pass(images, segments, dflt_dur, errs, warns):
    """跨段场景延续的生成期第二遍：上一页"演完之后"的画面 = 下一页的起点。

    为什么要第二遍而不是就地做：下一页要的起点是**上一页烘焙之后**的副本（A→B→C 里 C
    必须拿到 A+B 的叠加），所以只能沿 manifest 段序推进；第一遍走的是 images.json 的
    键序，两者不保证一致，就地算会用到还没烘焙的前页。

    烘焙本身在 _stage_carry（口径与边界写在那个模块头）。这里只回答"接的是谁"：
    前一段存在吗、它内联得出来吗、两页是不是同一张图，以及接续页的运镜自查。
    """
    if not images:
        return
    for i, seg in enumerate(segments):
        sid = seg["id"]
        entry = images.get(sid)
        if not entry or entry.get("stage") != "keep":
            continue
        src = entry["src"]
        where = f"段落 '{sid}' 写了 stage:\"keep\""
        if i == 0:
            errs.append(f"{where}，但它是 manifest 里的第一段——接续的起点是上一页演完的"
                        "画面，第一页没有上一页。把 stage 挪到真正接续的那一页，或这一页"
                        "独立成图")
            continue
        prev = segments[i - 1]
        prev_entry = images.get(prev["id"]) or {}
        prev_markup = prev_entry.get("inline_svg")
        if not prev_markup:
            errs.append(f"{where}，但上一段 '{prev['id']}' 没有可接续的内联画面——它没写"
                        " director（写了才净化内联），或它的配图没读进来/没能内联。"
                        " 接续页要的是上一页演完的那幅活画面，<img> 那份没有可烘焙的元素态")
            continue
        if prev_entry.get("src") != src:
            errs.append(f"{where}，但它的 src={src!r} 和上一段 '{prev['id']}' 的 "
                        f"{prev_entry.get('src')!r} 不是同一张图——接续的前提是同一幅画"
                        " 跨页继续演。要换画面就别写 stage；要同一幅画就把两页的 src 对齐")
            continue
        prev_steps = (prev_entry.get("director") or {}).get("steps") or []
        try:
            markup, notes = bake_settled_state(
                prev_markup, prev_steps, _director_beats(prev_steps, prev, dflt_dur))
        except ValueError as e:
            errs.append(f"{where}，但烘焙上一页的收尾态失败: {e}")
            continue
        for n in notes:
            warns.append(f"段落 '{sid}' 接续自 '{prev['id']}'：{n}")
        entry["inline_svg"] = markup
        # 接续页的运镜自查拿**烘焙后**的副本来算：这一页的相机姿态是在上一页推近的画
        # 面之上再乘一层，只看盘上原图会把已经出画的元素当成"在幅内"。
        own = (entry.get("director") or {}).get("steps")
        for w in crop_warnings(markup, own):
            warns.append(f"段落 '{sid}' 的 director 配图 {src}：{w}")
        # step_i 不叫 i：外层 for i, seg 用的是段序，内层复用 i 会静默把它
        # 换成 step 下标——今天块内不读外层 i 所以无害，下一行加一句用 i 的
        # 代码就会拿到错的值。
        for step_i, sel in global_ref_leaks(markup, own):
            errs.append(f"{where}，但它的 director.steps[{step_i}]（target={sel}）打在了两份副本"
                        "共享的元素上——图里的 url(#id)/href=\"#id\" 按文档序只认第一份"
                        f"（= 上一段 '{prev['id']}' 那一幅），所以这一拍打在本页这份上，本页"
                        "没有任何图形会去读它。渐变/滤镜/marker 这类共享元素的补间请只在"
                        "前一段写，别在接续页重写")
    return


def _svg_text_metrics(path):
    """从 SVG 全文取 (最小 px 字号, <text> 个数)，读不到字号时返回 (None, n)。

    字号只认 px：`font-size="26"`、`font-size: 26px`、`font-size="26.5px"` 都算，
    em / % / pt 一律不认——相对单位要乘父级才知绝对值，这里没有布局引擎，硬猜
    会把"能读"和"读不准"混成一类，宁可交回调用方按"没法验"给 warn。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return None, 0
    sizes = [float(m.group(1)) for m in re.finditer(
        r'font-size\s*[=:]\s*["\']?\s*(\d+(?:\.\d+)?)\s*(?:px)?(?![\w.%])', body)]
    return (min(sizes) if sizes else None), len(re.findall(r"<text\b", body))
