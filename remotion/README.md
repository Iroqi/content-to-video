# content-to-video — Remotion 渲染后端

`timing_manifest.json` 的另一条渲染通道：把 Python pipeline（`scripts/pipeline.py`
→ manifest）的产物交给 Remotion（React 组件按帧推导画面）渲染成 MP4，与
HTML+GSAP+hyperframes 后端共享同一份契约与同一套视觉语言。

## 架构

```text
source.json ──► scripts/pipeline.py ──► timing_manifest.json（契约不变）
                                          │
             scripts/gen_remotion_project.py（本后端桥）
              ├─ 校验 manifest（_manifest_schema 同一入口）
              ├─ 移植 html_renderer 的 clip 几何（wipe/win_start/vis/peel）
              ├─ agenda 行（开屏=各段标题+时长，结尾=takeaway+cta）
              ├─ 复制音频 → public/audio、配图 → public/images
              └─ 写 src/generated.ts（数据胶；组件脚手架不动）
                                          ▼
                remotion/（React 组件，静态脚手架）
              ├─ src/Video.tsx        主合成：根背景 + 按序堆叠的段落卡 + 音频
              ├─ src/components/Card.tsx      卡外壳：wipe 揭幕/引导线/旧卡剥离
              ├─ src/components/AgendaCard.tsx 开屏/结尾（含苹果風開場）
              ├─ src/components/SlotCard.tsx   内容段槽位版式（两画幅）
              ├─ src/components/CanvasCard.tsx 整页画布
              ├─ src/components/Verse.tsx      歌词式句子流
              ├─ src/components/ProgressBar.tsx
              ├─ src/theme.ts       主题/版式/动画数值（_theme.py/_template.py 的 TS 副本）
              └─ src/easing.ts      GSAP 缓动的 TS 移植
                                          ▼
              npx remotion render src/index.ts ContentToVideo out.mp4
```

**设计要点**：
- 组件全部按**绝对帧**推导画面（`useCurrentFrame` + manifest 时间轴），等价于
  GSAP 单条时间线逐帧 seek 的语义；卡窗口、wipe 几何、peel 都由生成器按
  `html_renderer.generate_html` 的同一条公式算好写进 `generated.ts`，渲染端
  不重推。
- **数据胶与脚手架分离**：`src/generated.ts`、`public/audio`、`public/images`
  是生成产物（gitignore），组件代码是静态检查进仓库的脚手架。换稿件只重跑
  生成器，不动组件。
- 苹果風開場（`opening_animation: "apple"`）与 HTML 版同参数（模糊 16px→0、
  缩放 1.06→1、行 stagger 0.12、光晕呼吸），编排锚在擦除起点 `win_start`。

## 用法

```bash
# 1. 老步骤不变：TTS → manifest
MIMO_API_KEY=... MIMO_BASE_URL=... python3 scripts/pipeline.py \
    --source source.json -o audio_output

# 2. 生成 Remotion 工程（默认 --out remotion；--images 可选，格式同 images.json）
python3 scripts/gen_remotion_project.py -m audio_output/timing_manifest.json \
    --images images.json --out remotion --aspect portrait --fps 24

# 3. 渲染（首次会自动下载 headless shell；显式指定浏览器：
#    npx remotion render ... --browser-executable $(which chromium)）
cd remotion && npx remotion render src/index.ts ContentToVideo out.mp4
```

预览（浏览器里逐帧拖动、可看 cue 时间轴）：

```bash
cd remotion && npx remotion studio src/index.ts
```

## 与 HTML 后端的差异（已知取舍）

| 维度 | HTML+GSAP+hyperframes | Remotion |
|---|---|---|
| 渲染器 | hyperframes（Chrome headless + 逐帧 seek） | @remotion/renderer（Chrome + 帧序列） |
| 动画 | GSAP 补间（时间线 seek） | React 逐帧插值（幂等、可推导） |
| 配图 | SVG 可净化内联 + director 时间轴动画 | `<img>` 静态引用；**director/stage:keep 暂未移植** |
| 检查 | `hyperframes check` / `snapshot` | Remotion 无对应门禁；靠组件确定性 + 抽帧人工看 |
| 数值 | Python 模板真源 → CSS 变量 | `theme.ts` TS 副本（**改视觉参数需双写**） |

其余（契约校验、wipe 几何、agenda 行、verse 滚动、进度条、苹果开场）逐条移植，
两后端对同一 manifest 应渲染出同构画面；差异若出现，以 `references/rendering.md`
为口径排查。

## 许可

Remotion 是**分许可**的：个人与 ≤3 人团队免费，更大规模的公司/组织需要购买
公司许可证（https://www.remotion.dev/license）。本目录只是工程脚手架，是否
在生产使用前请先对照贵方主体规模确认许可。
