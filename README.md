# Content to Video · 信源转视频

把文章、报告、网页链接、笔记或粘贴的聊天记录交给 agent，拿回一支**带配音、逐句字幕、配图与动效的解说视频**（竖屏 3:4 / 横屏 16:9）。

## 怎么用

不用记命令和参数，说人话就行。一轮对话四步（与 [SKILL.md](SKILL.md)「核心工作流」的 1–4 步**一一对应**）：

1. **写稿** — "把这篇文章做成视频" ＋（链接 / 文件路径 / 粘贴正文）。agent 整理信源后产出结构化稿 `segments_source.json`；画幅、音色、语速、主题、每段讲多深都能在这一步随口提。
2. **配音（TTS）** — 按稿件逐句合成，产出时间轴 `timing_manifest.json`。音色、语速在这里落地。
3. **配图** — 每段决定"要不要演、观众该看到什么、素材从哪来"（ImageGen 生图 / 手绘 SVG）。
4. **预览与出片** — 先生成 HTML 预览页，浏览器直接打开；此时还没付渲染成本，随便改。**改稿、换图、调结构都不重头再来，已定稿的句子配音直接复用。** 你确认之后才渲染，交 MP4 ＋ 生产报告（各步耗时、配图覆盖率、降级项）。

前 3 步都可以说"先只到第 N 步"，agent 会在这一步停下让你看结果。

## 准备工作与环境

- **`MIMO_API_KEY` 只卡出片，不卡预览**：配音走外部付费 TTS，是整条链路唯一的付费外部依赖。没有 key 时 `--until html/check` 自动降级成无配音预览（时间轴按字数估算、产物标 `degraded`，用来反复看版式不花一分钱）；只有**要成片**的那条路径才会硬停——无声片不是成片。
- **ffmpeg**（只要 `ffmpeg`，且**要带 libx264**）：音频处理；渲染编码 mp4 也用它，缺编码器时渲染跑到最后一步才报 `✗ MP4 H.264 encoder unavailable`
- **Node.js 22+**（渲染默认使用 `hyperframes@0.8.185`；可用 `CTV_HYPERFRAMES_PACKAGE` 覆盖）＋ headless Chrome
- Python 3.9+（脚本只依赖标准库）
- **Pillow**（可选，建议装 `pip install pillow`）：用来判"配图够不够大、会被裁掉多少"。装不上时退回内置头解析，只认 PNG/JPEG/WebP/GIF，其余格式（AVIF/HEIC/TIFF…）会跳过这两道告警。

## 测试

```bash
cd tests && python3 -m unittest discover -s .     # 1497 条，约 24 秒（含真渲染一支片）
```

其中 `test_e2e_render.py` 是**唯一真的出一支 mp4 并看它一眼**的用例：造一份小项目 → 生成 HTML → `npx hyperframes render` → 断言文件存在、时长与时间轴对得上、抽出来的每一帧都不是纯色。它需要 Node 22+、headless Chrome 与带 libx264 的 ffmpeg；缺依赖时**跳过并打印原因**（`CTV_SKIP_E2E=1` 显式跳过，`CTV_E2E_REQUIRED=1` 缺依赖即失败）——CI 的 `e2e` job 就用后者，防止"全绿"再次只证明字符串拼得对。

**三种结果分得开**：缺依赖 → 跳过（说缺什么）；`420s` 内没渲完 → 跳过（说卡在哪一步：还在装浏览器 / 进了渲染但一帧没抓出来 / 抓帧太慢）；渲完但结果不对 → **失败**。第一版把超时当成失败报，实测读到的是 `ERROR: ... timed out after 900 seconds`，看着像"机器慢"，其实是 CLI 在 300s 时转去下载 `chrome-headless-shell` 而这台机器下不完——红点指错方向比没有红点更坏。

**头一次跑会慢**：CLI 自己装一次 `chrome-headless-shell`（约 120MB）进 `~/.cache/hyperframes/chrome/`，装好之后不再下载，本机实测整条用例 14s 出片。慢网装不下来就跳过安装、直接指一个现成的浏览器：

```bash
HYPERFRAMES_BROWSER_PATH=$(command -v google-chrome) \
  python3 -m unittest test_e2e_render -v
```



## 授权

MIT，见 [LICENSE](LICENSE)。
