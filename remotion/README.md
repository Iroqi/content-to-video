# content-to-video — Remotion 渲染后端

`timing_manifest.json` 的另一条渲染通道：把 Python pipeline（`scripts/pipeline.py`
→ manifest）的产物交给 Remotion（React 组件按帧推导画面）渲染成 MP4，与
HTML+GSAP+hyperframes 后端共享同一份契约与同一套视觉语言。

## 架构
## 主题

配色与版式数值真源在 `scripts/_theme.py` / `scripts/_template.py`（Python）；
生成器把两画幅的 token 烘焙进 `src/theme.ts`，组件只消费这份 TS 副本。
改视觉参数需双写：先改 Python 真源，再同步 `src/theme.ts`（差异表见下）。


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
| 配图 | SVG 可净化内联 + director 时间轴动画 | 净化内联 SVG + 逐帧 director 求值（`DirectorImage`）；静态图/gif 走 `<img>`、视频走 `<Video>`（loop/muted/poster） |
| 跨段接续 | stage:"keep" 烘焙上一页演完画面 | 同口径：生成器 `_stage_carry` 烘焙进 `svg`，keep 页 wipe=0 + 入场补间退场 |
| 检查 | `hyperframes check` / `snapshot` | Remotion 无对应门禁；靠组件确定性 + 生成器 fail-fast + 抽帧人工看 |
| 数值 | Python 模板真源 → CSS 变量 | `theme.ts` TS 副本（**改视觉参数需双写**） |

### 导演编排（director）的求值契约

生成器把 `images.json` 的 `director.steps` 展开成数据胶（节拍 `pos/dur/ease/
repeat/yoyo/stagger`、morph 关键帧采样 fps×2），组件 `src/components/director.ts`
按绝对帧把状态落到净化内联的 SVG DOM 上。与 GSAP 的确定性对拍口径：

- **at 锚点**：`beat_positions` 同一个函数（at 句序锚跟着 manifest 走、at_time
  段落绝对秒、delay 叠加）——两后端同一份节拍；
- **morph**：生成期采样 + seek 取"最近已到的关键帧"，逐帧可复现，无运行时
  依赖；yoyo 在奇数次遍序上倒放、repeat:-1 按时间周期求值（不存在"最后停在
  哪儿"）；
- **count/type**：按帧重算 textContent（count 节拍前显示 from 值、type 节拍前
  为空串，与 HTML 的 build f() 行为一致）；draw 在段落起点上
  pathLength/dasharray、节拍内 1→0 拉描；
- **to/from/fromTo/set**：首次触碰时捕获元素自然态（computed style / transform
  属性 / attr 现值），to 从自然态插到目标、from 插回自然态（GSAP current-value
  语义）；只增删自己写过的内联样式/属性，不碰作者原有样式；
- **#cam 相机钉**：`cam_default_origin` 同函数算出 pin；求值器不用 CSS
  transform-origin/transform-box（实测该渲染器里 JS 赋这两者不可靠），而是把
  「绕 pin 缩放平移」显式展开成 `matrix()`：`e = x + ox(1−sx)`、`f = y + oy(1−sy)`
  （含旋转时用完整矩阵），与 `_stage_carry._fold_camera` 烘焙矩阵逐位同构——
  跨段 keep 接续按构造对齐，不依赖浏览器 CSS 语义。

已知取舍（确定性优先）：GSAP 的 `random` stagger 源在逐帧渲染下本身不确定，
这里按 "start" 处理；grid/from 的几何精确复刻需要布局引擎，按索引近似网格；
未收录的 ease 族回退线性；autoplay/playsinline 是浏览器语义、逐帧渲染无意义，
视频只保留 loop/muted/poster。其余（契约校验、wipe 几何、agenda 行、verse
滚动、进度条、苹果开场）逐条移植；两后端对同一 manifest 应渲染出同构画面，
差异若出现，以 `references/rendering.md` 为口径排查。

## 许可

Remotion 是**分许可**的：个人与 ≤3 人团队免费，更大规模的公司/组织需要购买
公司许可证（https://www.remotion.dev/license）。本目录只是工程脚手架，是否
在生产使用前请先对照贵方主体规模确认许可。
