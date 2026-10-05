# 渲染参考

**双画幅**（portrait 1080×1440 / landscape 1920×1080，`--aspect` 选择，默认 portrait），版式三档、一律按段落 `layout` 分派（见下「画面结构」），段落之间统一方向擦除（wipe，见「动画」）。

## 主题

整期统一使用一个主题：

- `dark`：默认，近黑极客风（终端绿网格 + 青绿点缀）；适合技术突破、发布、工程、前沿 AI、安全事件。

配色在 `scripts/_theme.py` 单一真源；生成器把它烘焙进 `src/theme.ts`，Remotion 组件只消费这份 TS 副本（改视觉参数需双写，见 `remotion/README.md`「主题」）。

## 画面结构

内容段（两画幅同构，均为「标题 + 画布 + verse 句子流」）：

```text
标题区（1 行优先，最多 2 行）
↓
固定画布（竖屏 4:3 居中；横屏左文右图双栏）
↓
verse 句子流（竖屏钉底；横屏排在左文字栏末、随栏垂直居中）
```

标题区、画布、verse 各自独立定位——这句只对**竖屏**成立：标题从一行变成两行时，只改变标题区内部排版，**画布的位置与尺寸不变，verse 也不跟着移动**。横屏的标题与 verse 同处左文字栏这一个 flex 列（整列垂直居中），标题折行时 verse 会随整列重新居中；两画幅的画布都不受标题影响。

- **portrait**：标题区在上、**定高但由模板派生**（`title.maxLines × title.fontSize × typography.titleLineHeight + tagline.marginTop + tagline.fontSize × tagline.lineHeight`，改模板数字即跟着变，模板里不再存面积高度）+ 两行钳制，超过两行的部分被裁切；4:3 画布（980×735）居中，verse 钉底。标题区底边一旦压到画布顶边，生成期直接 `[template] 竖屏标题区放不进` 报错——改版式把重叠暴露成失败，而不是出一帧重叠的片子。
- **landscape**：左文字栏（标题 + tagline）固定 613px 宽，右侧图栏 1067×800。**没有行数钳制**——标题随长度自由折行，守卫只降字号：超过 16 字降到 54px、超过 22 字降到 48px；只要超过 16 字就打一条 `[warn]`（两档都报，不是只有 48px 那档）。
- **整页画布（段落 `layout: "canvas"`，两画幅同一份规则）**：上面那三层塌成一层——配图拉满整个画面（1080×1440 / 1920×1080，圆角、外发光、1px 内描边全撤），标题层与句子流层由渲染器**不生成**（不是 `display:none`：不留死 DOM、不留死补间目标）。配图**不再生成任何入场补间**——wipe 揭开即要求画面到位，海报再自带淡入会演成"先擦出空页、再浮出画面"的两段式；整页就是画面，滑一下/淡一下都是穿帮。

  画布页**不要自画满幅底板**：这一页底下模板本来就画着三层——主题渐变 `.bg`、只在页缘显形的网格 `.grid`、本段 accent 的氛围光（画布上换成近全屏的宽带柔光），SVG 的透明处就是这三层在出景深；一张满幅 `<rect>` 会把三层整个盖掉，画面立刻退回一片死平。为什么留透明、什么时候确实该自铺一张，见 `references/image_options.md`「不铺满幅底」。

  这一版把文字对比度从版式责任变成**画布作者的责任**：渲染器不数 SVG 里的字（一张被 `cover` 裁掉标题的画布照样渲染成功）。所以门禁改在生成期**按文件**拦：该段必须有配图（口径与报错见 `references/writing.md` 段落 `layout`），SVG 的固有比例与图内 px 字号都要跟当前画幅对账——判据清单、实测翻车数字和"照片/视频不验比例只给知情 `[warn]`"这一档都写在 `references/image_options.md`「整页画布」，画那张图时按那份执行，这里不复述。`check_svg.py --layout canvas` 会按**主题渐变的最坏一档**给字面 hex 填充算一次对比度（画布档只 warn 不 error：压在作者自画的浅色局部底板上的字，按页底算出来的数对它不成立），但渐变底、`class` 里的色、局部底板上的字它都管不着。底部进度条与段落底轨抬到更上层：槽位版式里媒体够不到页底，画布拉满全屏后一张铺到底的照片会把进度整个盖掉（实测删掉这条规则，页底 24 行像素全是画布填充色）。

opening / closing 默认是**纯文字 agenda 卡**，不配图：kicker（取自顶层 `opening_tagline` / `closing_tagline`）+ 大标题 + 行列表 + verse。这一页自己就是一个版式值 `layout: "agenda"`，由 pipeline 盖章（它不是这一段自己的文字，而是对全片其它段的投影）；作者要把它换成整页海报，用顶层 `opening_layout` / `closing_layout: "canvas"`，那一页就退化成内容段画布那条路：只剩一张满幅配图 + 进度条，标题层、句子流、章节行、`cta` 尾行连 DOM 都不生成。渲染器只按 `layout` 分派三档版式（`_segments.seg_layout()`），不再按 id 猜。漏盖 `layout` 不会报错也不会塌成槽位页：`seg_layout()` 按 id 兜回 agenda。开屏/结尾行取哪些字段、行数上限、`nameTrim` 字数（含硬截断不补省略号、仅 CSS 补省略号的情况）、标题长度守卫阈值与截断优先级**以 `references/writing.md` 为准**。agenda 列是定高 flex 列，行列表紧跟题头排布（间距 16px），万一仍被撑满，牺牲的是行列表尾部（裁切），题头与句子流始终完整。agenda 标题两画幅都居左。

画面单位是**段落**。段内画面基本静止，只让字幕句子逐句切换与高亮；不要把本技能改成句子级重画的交互课件。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据，生成器把它烘焙进数据胶；Remotion 组件按绝对帧从数据胶推导演出（同一 manifest 每次都应渲染出同一个画面）：

- 段落入场默认是**方向擦除（wipe）**：新卡用一条 clip-path 从全遮蔽形状补到全覆盖形状，揭开整页。**揭幕档只有 `line` 一档**（引导线：见下）。模板 `animation.segmentWipe.style` 写成 `"line"` 以外的任何值都会在生成期 fail-fast（`_line_only_guard`）——渲染端只实现了这一档，改档要先补 `remotion/src/theme.ts` 的 `ANIM` 与组件，别只改模板。wipe 档旧卡**不淡出**，被新卡盖住后随 clip 窗口切走。选它而不是 cross-fade：两页互相透明度溶解在成片里是"凭空消失再出现"的廉价信号（PPT 观感，实测被否）；wipe 全程只揭一层不透明页，方向感来自遮盖本身。**整页画布段除外**（`layout: "canvas"` 一律硬切，理由与代价见本节「整页画布段」条）。
- **`line` 引导线转场（进度条立起来画下一页）**：道具必须是画面本来就有的元素——外来物（razor/pull 两版 SVG 刀具，先后被否）读起来永远是"贴纸在演"。画面里唯一自带方向感的运动体是底部进度条（随朗读从左往右），`line` 档就让它续命：转场时一条 accent 高亮线从页底"脱开"向上扫，**线的下缘就是新卡 clip-path 的揭示边**（同窗同曲线，`_wipe_ease` 单一口径），新页像被这条线画出来；旧页被线犁过之后整层上移剥离（`peelFrac` 屏高 + `peelRotation` 逆旋 + `peelTilt` 绕底边轴的 `rotationX` 透视后倒、`peelPerspective` 焦距，纸真正"揭"起来而非图层平移；`peelEase` power2.in）。clip-path 在元素自身平面内先裁后变换，3D 不破坏揭开边。光影补全：被揭旧卡挂一层底缘暗边 `.peel-shade`（`peelShadeFrac` 屏高的黑渐变，opacity 与 peel 同窗拉起——折页线附近最暗，Material elevation 做法）；线的辉光下偏，光只洒在刚被画出的页面上——均匀四散是"发光条"，下洒才是"光源在画"。动效精修：笔程走 `propLine.duration`（line 档比几何档长）+ `propLine.ease` power2.inOut 的书写节奏（慢起—快行—慢收），线形是两端渐隐、中心提亮一枚"笔尖"的彗尾渐变；扫到顶恰好缩没进边沿即收笔。线是新卡的末子（`propLine.thickness` px，颜色走新卡的 `--seg-accent`），随父卡 clip-path 只露出揭示边以下部分——无需独立道具层，层叠天然正确。wipe 被 gap 钳到 0 的段线与剥离都不生成（瞬间切）。
- 时长被句间静音钳制：gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。`wipe = min(propLine.duration, gap)`，入场提前铺到 `winStart = s − wipe`，恰好在本段音频起点完成揭屏（线与剥离同窗同值）。字幕住在卡里，转场早于上一句念完就开始，等于下一段文字压着还在讲的话。
- **揭屏能成立的前提是 clip 的可见窗口覆盖整个入场窗口**：数据胶按 `data-start`/`data-duration` 语义硬切 clip 可见性（`compute_clips` 在生成期一次算齐 `wipe`/`winStart`/`vis`/`peel`），补间排在窗口外等于没写（这正是旧 cross-fade 版查出的 bug：边界帧全黑）。窗口 `[winStart, next_start]` 与 `wipe`/`winStart` 在生成器一次算齐；末卡窗口铺到成片结束。（数据胶里的字段名是 `winStart`，见 SKILL.md「输出契约」；本文件正文写作 `win_start` 只是同一几何的 Python 侧变量名。）
- 卡必须是不透明层：每卡自带背景三层（压在氛围光与内容之下）。透明卡做 wipe 时上一段文字会从新页底下透出，画面出现双影叠字（逐帧实拍复现）。
- **整页画布段（`layout: "canvas"`）不参与模板转场**：它的 `wipe` 在 clips 预处理里被强制归零，命中既有的"瞬间切"路径——引导线、旧页剥离、底缘暗边三处都以 `wipe<=0` 为闸自动不生成，画布页在自己的**音频起点**整页出现（不向后借擦除窗口，因此导演每一拍的时间锚点都与画面严格对齐），退场则被下一页直接盖住；相邻的 slot/agenda 段转场原样保留（画布页作为"上一页"时也不被剥走——把一张活 diagram 像纸一样掀起来正是"演"要取代的翻页感）。理由：擦除/剥离是**图层级**的翻页语言，与导演逐拍揭示同一页内部元素是两种叙事，叠在一起只会让第一拍落在还没揭完的页上互相抢戏；画布段是"一个连续镜头"，不需要转场道具。此规则按**版式**判定而非按擦除档，唯一的 `style` 下同样生效。代价是硬的：切过去的那一瞬间画布还是空的（导演从空场开始逐拍画），要消除这段空窗就得让导演的第一拍尽早落子（`at_time` 靠前的锚点、或第一拍即为整页底图/坐标轴）——见 `references/image_options.md`「SVG 动画：两档」。**同一档硬切还给了另一种页**：images.json 写 `stage: "keep"` 的跨段接续页也被强制 `wipe=0`、也不生成配图入场补间，因为它的起点是上一页演完的那幅画面，再演一次揭页就是穿帮（口径见 `references/image_options.md`「跨段场景延续」）。
- title / image 按模板参数入场；tagline 无独立补间，随段落卡整体显隐。
  **求值器用 inline transform（`matrix()`）写补间几何**——样式表里对同一元素声明 `transform` 会与 inline 互斥（只能活一个），组件里统一走 `remotion/src/components/director.ts` 的 matrix 求值，别另加 CSS transform。
- 底部 progress bar 与段落时间轴同步。
- verse 当前句用该段 accent 色高亮（附荧光笔式渐变下划线），字重不切换，避免横向跳动。切换在成片里是**瞬时**的：逐帧 seek 的渲染要求每一帧都等于时间线时刻，所以字幕的淡入/滚动补间不在 CSS 里。给 `.verse*` 加 `transition` 会把墙上时钟漏进成片（实测 seek 后计算样式停在过渡起点，句子流不跟着滚动），别加。
- 每段的 accent 会派生一组装饰：氛围光（槽位在画面中央一小团，整页画布换成近全屏的宽带柔光，因为画布页的景深全靠它）、配图槽位的外发光与 1px 内描边、tagline 左侧刻度条、进度条辉光——选 accent 时注意它会染整帧氛围。槽位外发光的半径走模板 `image.glow`（渲染端现算，不再经 CSS 变量）；挂 `bare-media` 的 SVG 配图槽位不吃外发光与描边，理由见 `references/image_options.md` 的「不铺满幅底」一节。

## 版式真源

画布尺寸、标题区、句子流、圆角、动画等版式数值都从 `scripts/_template.py` 派生，配色由 `scripts/_theme.py` 派生；生成器把它们烘焙进 `remotion/src/theme.ts` 与数据胶（`src/generated.ts`），Remotion 组件只消费这两份产物。新增视觉参数一律先加进这两个 py，再同步 `remotion/src/theme.ts` 的 TS 副本；哪些常量允许留在模板/组件里（以及两处与模板值的联动代价）写在各自文件头与块注释里。

模板改动后重跑生成器，抽帧目检关键帧（版式问题在这一步发现最便宜）。

## 配图

- 内容段画布：尺寸与配图规格见 `references/image_options.md`（第 4 步的配图规则以它为准）；素材一律 `cover` 铺满画布。
- 配图语义是否正确只能靠人眼判断（抽帧 + 成片），工具不会替代内容判断。
- **方式 C SVG 的「导演」（时间轴同步动画）**：images.json 给该段写 `director` 时，这张 SVG 经 `scripts/_svg_sanitize.py` 净化后**内联成活 DOM**，`director.steps` 由 `_director_prepare.py` 生成期展开成数据胶（节拍锚 `beat_positions`、morph 采样、count/type/draw 参数），Remotion 求值器按绝对帧落到 DOM（见 `remotion/src/components/director.ts` 与 `remotion/README.md`「导演编排（director）的求值契约」）。导演是**逐帧可复现的属性补间**，seek 到同一时刻必然同一帧，正因为如此内联时会剥掉 SMIL / CSS 动画这类墙钟运动。安全代价（内联=同源活节点）由净化兜住：`<script>`/`on*`/`<foreignObject>`/外链一律删除。要时间轴同步的状态变化用它，纯氛围循环仍走 `<img>`+墙钟。

## 生成与渲染

### 1. 生成 Remotion 工程

```bash
python scripts/gen_remotion_project.py -m audio_output/timing_manifest.json \
    --images audio_output/images.json -o remotion --aspect portrait --fps 24
```

画幅与主题在这一步烧进数据胶，渲染分辨率随之切换（默认 portrait + dark，见 SKILL.md「常用参数」）。生成目录应至少包含：

```text
remotion/
├── src/
│   ├── generated.ts      # 数据胶：fps/画幅/总时长/音频/逐段 wipe/winStart/vis/director/keep/媒体
│   ├── index.ts / Root.tsx / Video.tsx / theme.ts / easing.ts
│   └── components/       # Card/AgendaCard/SlotCard/CanvasCard/DirectorImage/MediaBox/director.ts …
├── public/               # audio/ 与 images/（静态资源，组件经 staticFile() 引用）
└── package.json / remotion.config.ts
```

音频路径会在生成前做存在性检查；manifest 中的绝对路径或项目外音频会复制到 `public/audio/`。manifest 的音频路径失效时会回落到项目里上次暂存的 `audio/combined.wav`——这会打 `[warn]` 并实测其时长与 manifest 时间轴对账，偏差超过 `max(1s, 2%)` 直接拒跑（拒绝用新字幕烧旧音轨）；两处都没有才报错，不生成无声成片。

### 2. 检查

渲染前做两级检查（渲染阶段本身没有自动版式检查）：

```bash
cd remotion
npx tsc --noEmit -p .                          # 类型
npx remotion compositions src/index.ts          # 合成与时长（ContentToVideo 24fps 1080×1440 599 帧 = 24.96s）
```

交付前用 `ffmpeg -ss <帧/24> -i out.mp4 -frames:v 1 <帧>.png` 抽帧目检：开场、每段首帧/落定帧、跨段接续边界（`stage:"keep"` 的烘焙取景与上一页落定帧一致）、视频页、结尾。导演页的节拍与运镜判据见 `references/image_options.md`「SVG 动画：两档」。

### 3. 正式渲染

```bash
cd remotion
npx remotion render src/index.ts ContentToVideo out.mp4 --codec h264 --crf 28 --concurrency 2
```

## 性能参数

- `fps`：默认 24；抓帧耗时与帧数严格线性，是唯一的一阶杠杆。快速看画面用 `--fps 12` 或抽关键帧，别拿定稿规格反复试。
- `--concurrency`：并行渲染进程数。**本机 2 核必须给 2**（给 4 会被拒）。每进程是一个独立浏览器实例。
- `--crf`：x264 质量档，默认 28（预览档 28，定稿可收紧到 23 附近）；`--codec h264` 输出 mp4。
- **固定开销**：每次调用渲染要起浏览器与合成（约 8–15s，与帧数无关）；冷跑第一次还多一次 headless shell 下载（约 86MB）。所以"改一处重渲一次"很不划算：先抽帧目检够看，再整片渲一次。

## 官方校验命令（优先于手搓探针）

Remotion 后端没有独立于渲染的"官方门禁"CLI；合成本身即校验，交付前用下面两条命令把渲染依赖体检与组件类型这两件事做掉：

```bash
cd remotion
npx remotion compositions src/index.ts          # 合成与总时长体检
npx tsc --noEmit -p .                           # 组件类型（数据胶与组件契约失配在此暴露）
```

- 生成期门禁（对图内文字、运镜、节拍、跨段接续的按文件检查）在 `scripts/_director_prepare.py` 与 `scripts/check_svg.py`——它们报的 `[error]`/`[warn]` 都要当真处理：`[error]` 一定出不了片（fail-fast），`[warn]` 是"画面可能不符合预期"（运镜出画、节拍窗外、跨段折返）。
- 要按时刻看画面：渲染后 `ffmpeg -ss <秒> -i out.mp4 -frames:v 1 <帧>.png` 抽帧，逐段核对（比在浏览器里滚时间轴诚实得多）。
- 遇到"数据胶与渲染不一致"的怀疑，以 `src/generated.ts` 的字段与 `remotion/README.md`「导演编排（director）的求值契约」对拍；差异若出现，以本文档为口径排查。
