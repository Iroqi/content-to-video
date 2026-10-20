# Content to Video · 信源转视频

把文章、报告、网页链接、笔记或粘贴的聊天记录交给 agent，拿回一支**带配音、逐句字幕、配图与动效的解说视频**（竖屏 3:4 / 横屏 16:9）。

## 怎么用

不用记命令和参数，说人话就行。一轮对话四步：

1. **写稿** — "把这篇文章做成视频" ＋（链接 / 文件路径 / 粘贴正文）
2. **排版** — "横屏，女声，节奏快一点，最后加个行动号召"。画幅、音色、语速、主题、每段讲多深都能随口提。
3. **看预览** — agent 给一个浏览器能直接打开的预览页，此时还没付渲染成本，随便改；改稿、换图、调结构都不重头再来，**已定稿的句子配音直接复用**。
4. **出片** — 你确认之后才渲染，交 MP4 ＋ 生产报告（各步耗时、配图覆盖率、降级项）。

## 准备工作与环境

- **`MIMO_API_KEY` 是硬闸门**：配音走外部付费 TTS，是整条链路唯一的付费外部依赖；没有 key 第 2 步直接停，即使全部命中缓存也不放行。
- **ffmpeg**（只要 `ffmpeg`）：音频处理
- **Node.js 22+**（+ `npx hyperframes`）：渲染成片
- Python 3.9+（脚本只用标准库，无 pip 步骤）

## 深入文档

| 文件 | 服务哪一步 |
| --- | --- |
|[SKILL.md](SKILL.md) | 工作流与红线，开头有路由表。深度参考在 `references/`|
| [writing.md](references/writing.md) | 第 1 步：字段规范、哪些段值得交给 canvas、内容类型写法 |
| [tts_pipeline.md](references/tts_pipeline.md) | 第 2 步：配音参数、音色表、manifest 字段 |
| [imagery.md](references/imagery.md) | 第 3 步选形式：ImageGen / SVG / `images.json` 落盘 |
| [canvas.md](references/canvas.md) | 第 3 步 SVG：画 + 演（几何、对比度、导演档、跨段） |
| [rendering.md](references/rendering.md) | 第 4 步：预览、渲染、官方门禁、画面翻车速查表 |


## 授权

MIT，见 [LICENSE](LICENSE)。
