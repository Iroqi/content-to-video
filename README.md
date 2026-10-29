# Content to Video · 信源转视频

把文章、报告、网页链接、笔记或一段聊天记录交给 agent，取回一支带配音、逐句字幕、配图与动效的解说视频（竖屏 3:4 / 横屏 16:9）。

本文只回答三件事：**准备什么环境**、**怎么跑通**、**出了问题去哪查**。字段规范与写法不在本文，见文末的文档索引。

## 1. 它是什么

一条四步流水线，每一步都有可交付的中间产物：

| 步 | 输入 | 产出 | 谁在做 |
| --- | --- | --- | --- |
| 1 写稿 | 信源（链接 / 文件 / 正文） | `segments_source.json` | agent 整理 |
| 2 配音 | 稿件 | `timing_manifest.json`（逐句时间轴）＋ WAV | MiMo TTS |
| 3 配图 | 稿件 ＋ 时间轴 | `images.json` ＋ `images/*.svg\|png\|mp4` | ImageGen 或手绘 SVG |
| 4 出片 | 以上全部 | `hf-project/index.html` → `out.mp4` ＋ 生产报告 | 本地脚本 ＋ Hyperframes |

第 1–3 步都可中途停下（"先只到第 N 步"），agent 在这一步交结果让你看。第 4 步先出 HTML，浏览器直接打开预览——**此时还没有渲染成本**，改稿、换图、调结构都不重头再来，已定稿句子的配音走缓存复用。确认之后才渲染。

## 2. 环境

| 依赖 | 用途 | 缺了会怎样 |
| --- | --- | --- |
| Python 3.9+ | 全部脚本（**只依赖标准库**） | — |
| **`MIMO_API_KEY`** | 配音（外部付费 TTS，整条链唯一的付费依赖） | 出片路径硬停在第 2 步；`--until html/check` 自动降级为无配音预览，见下节 |
| **ffmpeg**（**要带 libx264**） | 音频处理、时长测量、编码 mp4 | 第 2 步失败；缺编码器时渲染跑到最后一跳才报 `✗ MP4 H.264 encoder unavailable` |
| **Node.js 22+** ＋ headless Chrome | 仅第 4 步渲染（默认 `hyperframes@0.8.185`） | 出不了 MP4，前三步照常 |
| Pillow（可选，建议装） | 判"配图够不够大、会被裁掉多少" | 退回内置头解析，只认 PNG/JPEG/WebP/GIF；AVIF/HEIC/TIFF 跳过这两道告警 |

```bash
pip install -r requirements.txt          # 只有可选的 Pillow
export MIMO_API_KEY=...                  # 或 ~/.config/ai-video/.env，优先级：命令行 > 环境变量 > 该文件
```

**`MIMO_API_KEY` 只卡出片，不卡预览。** 没有 key 时 `--until html/check` 自动走 `--no-tts`：时间轴按字数估算、产物标 `degraded`，用来反复看版式不花一分钱。只有要成片的那条路径才硬停——**无声片不是成片**。

其它环境变量：`MIMO_TTS_MODEL`（默认 `mimo-v2.5-tts`）、`MIMO_BASE_URL`、`CTV_HYPERFRAMES_PACKAGE`（覆盖渲染 CLI 的包名/版本）。

GSAP 不随技能包分发：生成时依次从项目 `vendor/`、`~/.cache/content-to-video/vendor/`、CDN 取用（sha256 钉固）。**新机器首次生成需联网**，之后离线；取不到 CDN 用 `--gsap-src` 指本地文件。

## 3. 跑通最小一例

一份最小的稿件——只有 `title` 与 `text` 是硬要求：

```json
{"title": "本期标题", "opening": "一句开场白，说清主题和为什么值得看。",
 "segments": [
   {"id": "seg1", "title": "第一个要点", "text": "第一句。第二句。"},
   {"id": "seg2", "title": "第二个要点", "text": "第一句。第二句。"}],
 "closing": "一句收束。", "cta": "关注我们"}
```

```bash
python scripts/run.py --source segments_source.json -o audio_output --until html
# 没有 key 也能跑：自动降级成无配音预览。打开 hf-project/index.html 看版式。
```

预览满意再补配图与动效，然后去掉 `--until` 出片。**前三次迭代不要碰 `layout`、配图、`animate`**：默认槽位版式最稳，模板自带入场动画，先把链路跑通。

五个停止点（`--until` 的全部取值）：

```bash
python scripts/run.py --source S -o OUT --until tts      # 只配音
python scripts/run.py --source S -o OUT --until images   # 到配图
python scripts/run.py --source S -o OUT --until html     # 出 HTML 即停（迭代期用这个）
python scripts/run.py --source S -o OUT --until check    # 只过官方门禁，不进渲染
python scripts/run.py --source S -o OUT                  # 定稿：TTS → 配图 → HTML → render
```

常用参数只认一处真源：`python scripts/run.py --help`（调参与逃生口在 `--help-all`）。唯一容易误解的一条：`--workers` 在 `run.py` 里是**渲染抓帧**并行度，在 `pipeline.py` 里是 **TTS 并发**数。

## 4. 每一步的坑

**第 1 步 写稿**。7 段上限（≤6 段才放得下 `cta`）；标题一行优先，竖屏超两行直接裁掉。同时想清两件事：哪几段交给 `canvas`（整页一张 SVG 按旁白逐拍演）、哪几段的图要跟着旁白变（在该段写 `beats`）。两者答错都不会报错，只会难看。

**第 2 步 配音**。默认单句失败即 abort；`--on-fail silence` 才允许静音兜底，且渲染必须再加 `--allow-degraded`。改了文案仍走缓存，`--resume` 只重合成变更句。

**第 3 步 配图**。只有 SVG 能进导演档，ImageGen 的图只能当静态素材。**画 SVG 是最容易静默出错的一环**——画错了没有任何报错，只有 `check_svg.py` 能替你发现几何、对比度、字号、数据图的问题。动笔前先读 `references/canvas.md`。

**第 4 步 出片**。`run.py` 在渲染前自动跑 `hyperframes check`，有 error 直接停。注意：**Lint 有 error 时 browser 五阶段整个不跑，全 0 项不是通过、是失明**。日志里的 `[warn]` 要逐条处理（行数截断、配图不够大、视频不够这一段长……）。

## 5. 验证

```bash
# 结构门禁：文档交叉引用、数字契约、示例可跑、images 片段过契约层
python tests/check_docs.py && python tests/check_numbers.py
python tests/check_examples.py && python tests/check_image_snippets.py

# 测试套件（约 8 秒，不含真渲染）
cd tests && python3 -m unittest discover -s .

# 真的出一支 mp4 并看它一眼（需 Node 22+ / headless Chrome / 带 libx264 的 ffmpeg）
cd tests && python3 -m unittest test_e2e_render -v
```

`test_e2e_render.py` 是**唯一真的出片并看画面**的用例，其余全部用假进程替掉了 Node、Chrome 和 ffmpeg。它的三种结果刻意分得开：缺依赖 → 跳过并说缺什么；渲不出来 → 跳过并说卡在哪一步（装浏览器 / 一帧没抓出 / 抓帧太慢）；渲完但结果不对 → **失败**。CI 的 `e2e` job 用 `CTV_E2E_REQUIRED=1`，缺依赖即失败——防止"全绿"再次只证明字符串拼得对。

首次运行会由 CLI 装一次 `chrome-headless-shell`（约 120MB）到 `~/.cache/hyperframes/chrome/`，之后不再下载。慢网装不下来就指一个现成浏览器：

```bash
HYPERFRAMES_BROWSER_PATH=$(command -v google-chrome) \
  python3 -m unittest test_e2e_render -v
```

**测试按契约组织，不按轮次**：文件名说明"改这个模块该读哪一份"，缺陷锁与反向断言写在同一契约里，不另开"某轮 review"文件。`python tests/check_test_budget.py` 守三道闸——用例总量、单文件条数、不许出现以轮次编号命名的文件；超线先合并再加，或明确抬线并写清理由。

## 6. 目录

```
scripts/     全部 Python（只依赖标准库）
  run.py         编排层：TTS → 配图 → HTML → check → render
  pipeline.py    第 2 步：逐句配音与时间轴
  gen_hyperframes.py  第 3–4 步：配图校验、项目生成
  html_renderer.py    稿件时间轴 → 成片 HTML（含 GSAP 时间线）
  perceptual.py       观感门禁：取帧、量画面、出 contact sheet（交付第 3 道）
  check_svg.py        画好的 SVG 过一遍几何/对比度/字号/数据自查
  _template.py        视觉真源：版式、字体、动画参数的**唯一**来源
  _theme.py           配色真源
templates/   结构与选择器（CSS + 运行时/预览 JS），不放观感常量
references/  分步文档，见下节
tests/       契约测试 ＋ 五道检查脚本
```

**三条不许破的规则**：随画幅变的观感常量写 `_template.py`，别在 CSS 里存第二份；配色写 `_theme.py`，别散在各处；`--ctv-*` 令牌的注入与消费两侧必须对得上（`var()` 取不到值时是静默失效，页面照样生成、门禁照样全绿——由 `test_every_consumed_token_is_injected` 钉住）。

## 7. 文档索引

按"你此刻在做什么"取一份，不必通读：

| 你正要做的事 / 症状 | 读这一份 |
| --- | --- |
| 第 1 步写稿、字段报错、字数上墙；顺带定"演什么"（`layout` / `beats`） | [`references/writing.md`](references/writing.md) |
| 第 2 步配音、音色表、降级行为 | [`references/tts_pipeline.md`](references/tts_pipeline.md) |
| 第 3a 步选形式、素材落 `images.json`、生图崩字 | [`references/imagery.md`](references/imagery.md) |
| 第 3b 步画 SVG：几何、对比度、字号、数据自查 | [`references/canvas.md`](references/canvas.md) |
| 第 3b 步把台词板落成 `animate`：该不该动、动多快、镜头归谁 | [`references/motion.md`](references/motion.md) |
| 第 4 步预览、渲染、调性能、官方门禁 | [`references/rendering.md`](references/rendering.md) |
| 第 4 步版式、动画落地、字幕怎么跟着念 | [`references/look.md`](references/look.md) |
| 画面空 / 不动 / 换页没东西 | [`references/troubleshooting.md`](references/troubleshooting.md) |
| 能不能交付（四道验收）、"这一页在屏幕上到底有没有东西" | [`look.md`](references/look.md)「交付前四道」 |

`SKILL.md` 是给 agent 的入口与核心规则，不是给人读的教程。

## 授权

MIT，见 [LICENSE](LICENSE)。
