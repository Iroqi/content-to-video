# content-to-video — Remotion 渲染后端

`timing_manifest.json` 的渲染通道：把 Python pipeline（`scripts/pipeline.py`
→ manifest）的产物交给 Remotion（React 组件按帧推导画面）渲染成 MP4。
契约（`_manifest_schema`）、版式真源（`_template.py` / `_theme.py`）与缓动
（`_ease.py`）都在 Python 侧，本目录只负责"按帧把画面演出来"。

## 架构

```text
source.json ──► scripts/pipeline.py ──► timing_manifest.json（契约不变）
                                          │
             scripts/gen_remotion_project.py（本后端桥）
              ├─ 校验 manifest（_manifest_schema 同一入口）
              ├─ 算 clip 几何（wipe/win_start/vis/peel）
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
              └─ src/easing.ts      缓动曲线（_ease.py 的 TS 逐点对拍版）
                                          ▼
              npx remotion render src/index.ts ContentToVideo out.mp4
```

**设计要点**：
- 组件全部按**绝对帧**推导画面（`useCurrentFrame` + manifest 时间轴）；卡
  窗口、wipe 几何、peel 都由生成器算好写进 `generated.ts`，渲染端不重推。
- **数据胶与脚手架分离**：`src/generated.ts`、`public/audio`、`public/images`
  是生成产物（gitignore），组件代码是静态检查进仓库的脚手架。换稿件只重跑
  生成器，不动组件。
- 苹果風開場（`opening_animation: "apple"`）的模糊/缩放/stagger/光晕呼吸编排
  锚在擦除起点 `win_start`。
- 组件只按百分比与 auto 外边距定位，不接画幅宽高 props——唯一按像素算的
  peel/引导线位移用画幅高，横向由 `left/right: 0` 拉满。

## 主题

配色与版式数值真源在 `scripts/_theme.py` / `scripts/_template.py`（Python）。
组件读不到 CSS 变量，所以这些数值在 `src/theme.ts` 有一份 TS 副本，两份
**要人同步**：

| Python 真源 | TS 副本 | 内容 |
|---|---|---|
| `_theme.py` | `THEME` / `rgba` / `mixColors` / `ensureTextContrast` | 配色、对比度保底 |
| `_template.py` `typography` | `FONT_STACK` / `MONO_STACK` / `TYPO` | 字体栈、字重、行高 |
| `_template.py` `layout.<画幅>` | `LAYOUTS` | 版式几何（两画幅） |
| `_template.py` `layout.landscape` | `LANDSCAPE_ONLY` | 横屏左文字栏（竖屏没有对应物） |
| `_template.py` `animation` | `ANIM` | 动画参数 |

改视觉参数时先改 Python 真源，再改 `theme.ts` 对应项。`tests/test_ease_parity.py`
盯的是缓动那一份双源（幂次表 + 逐点采样）；版式这份目前靠人同步——两侧值
对不上时 tsc 不会报、画面也不会坏，只会慢慢漂。

`theme.ts` 之外的组件里只允许写"两画幅同值、单值即终态"的观感常量（字重、
透明度、辉光浓度）；其余一律回 `theme.ts`。

## 用法

```bash
# 1. 老步骤不变：TTS → manifest
MIMO_API_KEY=... MIMO_BASE_URL=... python3 scripts/pipeline.py \
    --source source.json -o audio_output

# 2. 生成 Remotion 工程（-o / --out 二者等价；--images 可选，格式同 images.json）。
#    生成器会把本仓库 remotion/ 的静态脚手架（package.json/tsconfig/组件源码）
#    复制进输出目录，--out 即自包含可渲染工程。
#    脚手架**只补缺失**：已存在且与仓库版本不同的文件保留你的改动并打一条
#    [warn]；本次没引用到的旧素材会从 public/ 清掉。
python3 scripts/gen_remotion_project.py -m audio_output/timing_manifest.json \
    --images images.json -o remotion --aspect portrait --fps 24

# 3. 渲染（首次会自动下载 headless shell；显式指定浏览器：
#    npx remotion render ... --browser-executable $(which chromium)）
cd remotion && npx remotion render src/index.ts ContentToVideo out.mp4
```

预览（浏览器里逐帧拖动、可看 cue 时间轴）：

```bash
cd remotion && npx remotion studio src/index.ts
```

类型检查（改组件后跑；`generated.ts` 由上一步产出，不在仓库里）：

```bash
cd remotion && npx tsc --noEmit -p .
```

## 门禁

| 检查 | 什么时候跑 | 拦什么 |
|---|---|---|
| `_manifest_schema` / `_images_schema` / `_ease` | 读 manifest / images 时 | 字段形状、档名、相对路径越界 |
| `validate_images_files` / `canvas_layout_errors` | 生成期 | 素材缺失或损坏、整页画布空带/错比例 |
| `_line_only_guard` | 生成期，任何写盘之前 | 模板的 wipe 档位渲染端没实现 |
| `npx tsc --noEmit` | 改组件后 | 类型与字段 |
| `tests/test_ease_parity.py` | CI | 缓动两份实现分家（幂次表、方向翻转） |

`--out` 指向技能目录会被拒（与 `pipeline.py` 同一道闸）。生成器不替你判断
`status`/`degraded`——它照实渲染并把降级项打在 manifest 里，放不放行由你决定。

## 导演编排（director）的求值契约

生成器把 `images.json` 的 `director.steps` 展开成数据胶（节拍 `pos/dur/ease/
repeat/yoyo/stagger`、morph 关键帧采样 fps×2），组件 `src/components/director.ts`
按绝对帧把状态落到净化内联的 SVG DOM 上。确定性口径：

- **每帧从时间线起点重算**，不依赖上一帧的残留状态（幂等）。未开始的 step
  不写任何东西——清场统一在每帧开头做一次，否则同元素上更早那几拍的成果会被
  尚未开始的后来者抹掉；
- **at 锚点**：`beat_positions` 同一个函数（at 句序锚跟着 manifest 走、at_time
  段落绝对秒、delay 叠加）；
- **morph**：生成期采样 + 取"最近已到的关键帧"，逐帧可复现；yoyo 在奇数次遍序
  上倒放，repeat:-1 按时间周期求值（不存在"最后停在哪儿"）；
- **收尾值 endU**：yoyo 且总遍数为偶数时收尾落在**起点**（末遍是倒放），否则
  落在终点——与 `_timeline.ends_at_start` 同口径。`from` 收尾回自然态、
  `fromTo` 收尾落在 `fromVars`（不是自然态）；
- **count/type**：按帧重算 textContent（count 节拍前显示 from 值、type 节拍前
  为空串）；draw 在段落起点上 pathLength/dasharray、节拍内 1→0 拉描；
- **to/from/fromTo/set**：首次触碰时捕获元素自然态（computed style / transform
  属性 / attr 现值），to 从自然态插到目标、from 插回自然态；只增删自己写过的
  内联样式/属性，不碰作者原有样式。transform 按 SVG 语义合成（多个 translate
  累加、scale 相乘、rotate 累加、单参形态合法），只给一个轴时其余轴沿用自然值；
- **相机**：`cam_default_origin` 同函数算出 pin；求值器不用 CSS
  transform-origin/transform-box（实测该渲染器里 JS 赋这两者不可靠），而是把
  「绕 pin 缩放平移」显式展开成 `matrix()`：`e = x + ox(1−sx)`、`f = y + oy(1−sy)`
  （含旋转时用完整矩阵），与 `_stage_carry._fold_camera` 烘焙矩阵逐位同构——
  跨段 keep 接续按构造对齐，不依赖浏览器 CSS 语义。

已知取舍（确定性优先）：stagger 按索引近似（`random` 源在逐帧渲染下本身不
确定）；grid/from 的几何精确复刻需要布局引擎；**未收录的 ease 族不静默退化**——
求值器打一条 `console.warn` 并按 `power2.out` 演，契约层 `_ease` 会在更早的
地方 fail-fast。`images.json` 的 `autoplay`/`playsinline` 是浏览器播放语义，
契约层收下但渲染端不读；`poster` 照原生 `<video>` 透传。其余（契约校验、wipe
几何、agenda 行、verse 滚动、进度条、苹果开场）由生成器与组件共同保证；
排查口径以 `references/rendering.md` 为准。

## 许可

Remotion 是**分许可**的：个人与 ≤3 人团队免费，更大规模的公司/组织需要购买
公司许可证（https://www.remotion.dev/license）。本目录只是工程脚手架，是否
在生产使用前请先对照贵方主体规模确认许可。
