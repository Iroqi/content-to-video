"""端到端出片验收：真的渲染一支 mp4，并真的看一眼那支片子。

## 为什么这条用例必须存在

仓库原有的 1479 条用例 7 秒跑完、全部通过，而它们**一条都没有碰过**
HTML → 帧 → MP4 这一段：`test_render_backend.py` 用假进程验证命令拼接，
CI 里没装 Node、没装 Chrome。结果是"全绿"只证明了字符串拼得对。

出片链路上真正的失败长这样——每一环都发生在别人的代码里，本地门禁看不见：

- HTML 生成了，但 GSAP 没加载 → 整片静止，`check` 的 lint 照样过；
- 渲染跑起来了，但段与段之间的空档一直是第 0 帧 → 观众看到一页空白；
- 容器写出来了，却比时间轴短一截 → 后两段整段不在片里；
- 第 0 帧就是分享封面，而它是一片纯背景色。

所以这里不看源码，只看三样东西：**文件存不存在、时长对不对、像素有没有东西**。

## 怎么跑

    python -m unittest tests.test_e2e_render -v          # 默认执行
    CTV_SKIP_E2E=1 python -m unittest discover -s tests  # 显式跳过

依赖（Node 22+ / headless Chrome / ffmpeg）任一缺失时**跳过并打印原因**，
不伪装成通过：CI 的 e2e job 会把 `CTV_E2E_REQUIRED=1` 打开，缺依赖直接失败
——"悄悄跳过"正是这个仓库过去一直在自我欺骗的方式。

## 超时为什么必须是一条单独的分类

第一版只给 `subprocess.run(timeout=900)`。实测踩到的却是：CLI 在 300s 时
自己转去装 `chrome-headless-shell`（那条下载在这个网络里连不上），剩下
600s 全花在等一个永远不会完成的安装上；`TimeoutExpired` 一抛，用例报的是
`ERROR: ... timed out after 900 seconds`，读的人第一反应是"这台机器太慢"，
而真相是"这个环境根本没有可用的浏览器"。同一段时间里另外 1494 条用例
是绿的，唯一的红点却指向了错误的方向——**比没有这条用例更坏**。

所以现在把"没在时限内跑完"与"跑完了但结果不对"分开报：
超时是**环境问题**（跳过 + 一句怎么办），渲染失败才是**产品问题**（失败）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers as H  # noqa: F401  副作用：把 scripts/ 放进 sys.path

SKIP_ENV = "CTV_SKIP_E2E"
REQUIRED_ENV = "CTV_E2E_REQUIRED"
# 短片：够长到能覆盖 4 个段落 + 段间转场，够短到 CI 一分钟内跑完。
SENTENCE_SECONDS = 1.6
GAP_SECONDS = 0.4
FPS = 12
# 渲染时限。这台机器上 11.6s / 12fps 的片子实测 ~40s 出片（含一次
# 浏览器预热），10 倍余量足够；超过它就是卡在环境准备上，不是片子太慢。
RENDER_TIMEOUT = 420

# 首帧墨迹下限：整帧只有背景色时墨迹占比 ≈0。0.004 是"至少有一行字"。
FIRST_FRAME_MIN_INK = 0.004


def _missing_deps():
    """返回缺的依赖清单（空列表 = 环境齐备）。"""
    import _render_backend as RB
    miss = []
    if shutil.which("node") is None:
        miss.append("node")
    elif RB.node_major_version() and RB.node_major_version() < RB.MIN_NODE_MAJOR:
        miss.append(f"node>={RB.MIN_NODE_MAJOR}（实际 {RB.node_major_version()}）")
    if shutil.which("npx") is None:
        miss.append("npx")
    if not _has_headless_browser():
        miss.append("headless Chrome（或用 HYPERFRAMES_BROWSER_PATH 指定）")
    try:
        from _audio import ffmpeg_usable, get_ffmpeg
        if not ffmpeg_usable(get_ffmpeg()):
            miss.append("ffmpeg")
    except Exception:
        miss.append("ffmpeg")
    if not _has_h264_encoder():
        miss.append("带 libx264 的 ffmpeg")
    return miss


def _has_headless_browser():
    """系统 Chrome / chromium / 显式指定的 headless shell 任一个可用即可。"""
    explicit = os.environ.get("HYPERFRAMES_BROWSER_PATH", "").strip()
    if explicit and os.path.exists(explicit):
        return True
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome-headless-shell"):
        if shutil.which(name):
            return True
    return False


def _has_h264_encoder():
    """CLI 用它出 mp4；只有 h264_nvenc/openh264 时真渲染会在最后一跳失败。"""
    try:
        from _audio import get_ffmpeg
        r = subprocess.run([get_ffmpeg(), "-hide_banner", "-encoders"],
                           capture_output=True, timeout=20)
        return b"libx264" in (r.stdout or b"")
    except Exception:
        return False


def _build_fixture(root):
    """在 root 下造一份能直接渲染的项目：manifest + 静音音频 + 图片 + HTML。

    刻意**不走 TTS**：这条用例验收的是渲染与画面，不是配音。用
    `generate_silence` 造与时间轴等长的音频，用 Pillow 画几张有内容的图，
    让"画面里有没有东西"这个判据不被"图是空的"污染。
    """
    from PIL import Image, ImageDraw
    from _audio import generate_silence, get_ffmpeg
    import gen_hyperframes  # noqa: E402
    from _segments import sids_needing_image

    manifest = H.make_manifest(dur=SENTENCE_SECONDS, gap=GAP_SECONDS)
    out = os.path.join(root, "audio_output")
    project = os.path.join(root, "hf-project")
    os.makedirs(os.path.join(project, "images"), exist_ok=True)
    audio_path = os.path.join(out, "combined.wav")
    os.makedirs(out, exist_ok=True)
    generate_silence(get_ffmpeg(), manifest["total_duration"] + 0.2, audio_path)
    manifest["combined_audio"] = audio_path

    images = {}
    width, height = 1080, 1080
    for sid in sids_needing_image(manifest):
        im = Image.new("RGB", (width, height), (24, 32, 56))
        d = ImageDraw.Draw(im)
        # 几条粗线 + 一个大圆：占据足够像素，避免被"画面是否稀薄"误判
        for i in range(6):
            y = 160 + i * 130
            d.rectangle([120, y, width - 120, y + 60], fill=(90 + i * 20, 150, 210))
        d.ellipse([width // 2 - 180, height // 2 - 180,
                   width // 2 + 180, height // 2 + 180],
                  outline=(240, 210, 120), width=36)
        rel = f"images/{sid}.png"
        im.save(os.path.join(project, rel))
        images[sid] = {"src": rel}
    with open(os.path.join(project, "images.json"), "w", encoding="utf-8") as f:
        json.dump(images, f, ensure_ascii=False)

    manifest_path = os.path.join(out, "timing_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    # 走 gen 的真实 CLI（与 run.py 同一条入口）：音频暂存、GSAP vendor 安装、
    # CSS 抢 transform 那条门禁全都在里面，绕开它们就不算 e2e。
    gen_hyperframes.main(["-m", manifest_path,
                          "-o", os.path.join(project, "index.html"),
                          "--images", os.path.join(project, "images.json"),
                          "--aspect", "portrait", "--fps", str(FPS)])
    return project, manifest


def _render(project, out_path, timeout=RENDER_TIMEOUT):
    """真跑一次 hyperframes render。

    返回 (ok, log, timeout)；`timeout=True` 时前两项无意义——那是"时限内没
    跑完"，调用方必须按环境问题处理，不能报成产品失败（见模块 docstring）。

    子进程整套操作（起进程 + 限时 + 收尾）都在一个 daemon 线程里，主线程
    只 join 它 `timeout + 30` 秒。理由是实测出来的：

    - `subprocess.run(timeout=)` 只杀 npx，它拉起的 node/Chrome 还握着
      管道写端，读端永远等不到 EOF——25s 的时限挂到 5 分钟没返回；
    - 于是改成输出落文件 + 只发信号不收尸，可 `proc.wait()`/`communicate()`
      只要碰上不响应信号的成员（chromium zygote）就永远回不来；`poll()`
      只查直接子进程，不碰进程组，因此不会挂——用它轮询判死活。放在工作
      线程里只是第二道保险：真挂住的是**那个线程**，主线程照样按时往下走。

    残留进程不收拾：本用例只读，临时目录由系统清；收拾它们才是那个会挂
    住的动作。
    """
    import signal
    import threading
    import time
    import _render_backend as RB
    cmd = RB.build_render_command(out_path, "draft", FPS, 2,
                                  RB.hyperframes_command())
    log_path = os.path.join(os.path.dirname(out_path) or ".", "render.log")
    state = {"ok": False, "timeout": True}

    def _work():
        kw = {"start_new_session": True} if os.name != "nt" else {}
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(cmd, cwd=project, stdout=log,
                                    stderr=subprocess.STDOUT, **kw)
            deadline = time.time() + timeout
            while proc.poll() is None:      # wait() 会顺手收掉全组、被拖住
                if time.time() > deadline:
                    break
                time.sleep(0.25)
            if proc.poll() is None:
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                else:
                    subprocess.run(["taskkill", "/PID", str(proc.pid),
                                    "/T", "/F"], capture_output=True)
                return                    # state 保持 timeout=True
            state["ok"] = proc.returncode == 0 and os.path.exists(out_path)
        state["timeout"] = False

    work = threading.Thread(target=_work, daemon=True)
    work.start()
    work.join(timeout + 30)               # 多 30s 给"起进程 + 落盘"的抖动
    with open(log_path, "rb") as f:
        tail = f.read().decode("utf-8", "replace")[-2200:]
    return state["ok"], tail, state["timeout"]


def _timeout_hint(log):
    """超时时给一句人话：卡在装浏览器、卡在抓帧，还是单纯慢。"""
    if "chrome-headless-shell" in log or "Waiting for another hyperframes" in log:
        return ("卡在下载/安装 chrome-headless-shell（约 120MB，慢网十几分钟）："
                "这台机器连不上 storage.googleapis.com，或有残留进程占着安装锁。"
                "装好一次就落进 ~/.cache/hyperframes/chrome/，之后不再下载；"
                "装不上就设 HYPERFRAMES_BROWSER_PATH 指向已有的 chrome-headless-shell"
                "（或 $(which google-chrome)）跳过安装。")
    if "Rendering" in log and "Capturing" not in log:
        return ("CLI 已经进了渲染阶段但一帧都没抓出来：浏览器起不来"
                "（跑一次 npx hyperframes doctor 看 Chrome 探测结果），"
                "或 --workers 撞上内存上限被反复重启。")
    if "Capturing" in log or "frame" in log:
        return "已经开始抓帧但没跑完：机器太慢，或 --workers 撞上内存上限。"
    return "CLI 连第一帧都没走到：先手动跑一次 npx hyperframes doctor 看依赖。"


def _gray_frame(path, size=(160, 200)):
    from PIL import Image
    return Image.open(path).convert("L").resize(size)


def _ink_ratio(im):
    """墨迹占比：与背景色（左上角取样）差异显著的像素比例。"""
    px = list(im.getdata())
    bg = px[0]
    n = sum(1 for p in px if abs(p - bg) > 18)
    return n / len(px)


class RenderEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get(SKIP_ENV):
            raise unittest.SkipTest(f"{SKIP_ENV} 已设置")
        miss = _missing_deps()
        if miss:
            msg = "端到端渲染缺少依赖：" + "、".join(miss)
            if os.environ.get(REQUIRED_ENV):
                raise AssertionError(msg + "（CTV_E2E_REQUIRED=1 要求必须跑）")
            raise unittest.SkipTest(msg + f"（设 {REQUIRED_ENV}=1 可强制失败）")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.project, cls.manifest = _build_fixture(cls.tmp.name)
        cls.video = os.path.join(cls.tmp.name, "out.mp4")
        cls.ok, cls.log, cls.timed_out = _render(cls.project, cls.video)
        if cls.timed_out:
            tmp = cls.tmp
            cls.tmp = None
            tmp.cleanup()          # 半截项目留着没意义，还占几百 MB 图片
            msg = (f"渲染 {RENDER_TIMEOUT}s 未完成（环境问题，不是产物问题）。"
                   + _timeout_hint(cls.log))
            if os.environ.get(REQUIRED_ENV):
                raise AssertionError(msg + f"\n--- 日志尾 ---\n{cls.log}")
            raise unittest.SkipTest(msg)

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_01_renders_a_video(self):
        """渲染出文件。少了这一条，后面三条都只是在断言空气。"""
        self.assertTrue(self.ok, f"渲染失败：\n{self.log}")
        self.assertGreater(os.path.getsize(self.video), 20 * 1024,
                           "成片不到 20KB，多半只写了一帧或空容器")

    def test_02_duration_matches_timeline(self):
        """成片时长 = 时间轴时长（容差与 run.py 的交付闸门同一口径）。"""
        from _render_backend import probe_video_duration
        from _timeline import duration_tolerance
        got = probe_video_duration(self.video)
        self.assertIsNotNone(got, "量不出成片时长：容器坏或没有视频流")
        expect = self.manifest["total_duration"]
        tol = duration_tolerance(expect)
        self.assertLess(abs(got - expect), tol,
                        f"成片 {got:.2f}s 与时间轴 {expect:.2f}s 相差超过 "
                        f"{tol:.2f}s——多半是抓帧中断、后段没渲进去")

    def test_03_frames_are_not_black_or_blank(self):
        """逐段取帧：每一帧都得有东西（不是纯背景色）、且整片得有变化。

        这一条是**唯一**会在 CI 里看画面像素的判据。它不看"好不好看"
        （那要人翻 contact sheet），只回答两个机器能回答的问题：
        有没有一页是空的？整片有没有动过？
        """
        import _render_backend as RB
        times = [round(self.manifest["segments"][0]["sentences"][0]["start_time"]
                       + SENTENCE_SECONDS * 0.6, 2)]
        for seg in self.manifest["segments"][1:]:
            first = seg["sentences"][0]
            times.append(round(float(first["start_time"]) + SENTENCE_SECONDS * 0.6, 2))
        out_dir = os.path.join(self.tmp.name, "frames")
        got, why = RB.snapshot_frames(self.project, self.video, times, out_dir)
        self.assertIsNotNone(got, f"取帧失败：{why}")
        pngs = sorted(p for p in _walk(got)
                      if p.lower().endswith((".png", ".jpg", ".jpeg")))
        self.assertGreaterEqual(len(pngs), len(times),
                                f"要 {len(times)} 帧，只取到 {len(pngs)} 张")
        inks = [_ink_ratio(_gray_frame(p)) for p in pngs]
        blank = [p for p, k in zip(pngs, inks) if k < FIRST_FRAME_MIN_INK]
        self.assertFalse(blank, "以下帧几乎没有内容（面积 >99% 是同一种颜色）：\n"
                         + "\n".join(blank) + f"\n墨迹占比：{[round(i, 4) for i in inks]}")
        self.assertGreater(max(inks) - min(inks), 0.004,
                           f"每一帧的墨迹占比几乎一样（{[round(i, 4) for i in inks]}），"
                           "整片可能根本没动——GSAP 时间线没跑起来")

    def test_04_perceptual_gate_actually_sees_frames(self):
        """观感门禁的**成功路径**：取帧 → 判词 → contact sheet，一条都不能空。

        这条用例存在的理由是一个真实的失明期：`_snapshot` 把"先清空帧目录"
        写在了 `subprocess.run` 之后，于是官方 snapshot 刚写出的帧被自己删掉，
        `--perceptual` 恒抛"没有产出任何帧"——contact sheet、空页、死帧、换页
        时序这三条判词**从未执行过**。而 1500+ 条单测全绿：它们用 mock 顶掉了
        subprocess，只覆盖了 returncode != 0 的失败路径，成功路径无人测。

        所以这里必须真跑一次，且断言的是"帧真的落盘了"——不是"没报错"。
        """
        import _render_backend as RB
        import perceptual as P
        out_dir = os.path.join(self.tmp.name, "perceptual")
        res = P.run_gate(self.project, self.manifest, out_dir,
                         aspect="portrait", command=RB.hyperframes_command())
        self.assertNotIn("error", res, f"观感门禁没跑完：{res.get('error')}")
        frames = list(_walk(os.path.join(out_dir, "frames")))
        self.assertGreaterEqual(len(frames), len(self.manifest["segments"]),
                                f"只取到 {len(frames)} 帧，少于段落数——"
                                "判据看着在跑，其实量的是空气")
        self.assertGreater(res.get("frames", 0), 0)
        self.assertTrue(res.get("contact_sheet") and
                        os.path.getsize(res["contact_sheet"]) > 1024,
                        "没有产出 contact sheet（判词没法被人复核）")
        # 这份 fixture 是"画面合格"的参照：不要求零判词，但不允许"帧都取不到"
        # 这种结构性空转被记成通过。
        self.assertIsInstance(res.get("issues"), list)


def _walk(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield os.path.join(dirpath, name)


if __name__ == "__main__":
    unittest.main()
