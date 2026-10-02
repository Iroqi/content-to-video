# 渲染参考

**双画幅**（portrait 1080×1440 / landscape 1920×1080，`--aspect` 选择，默认 portrait），版式三档、一律按段落 `layout` 分派（见下「画面结构」），段落之间统一方向擦除（wipe，见「动画」）。

## 主题

整期统一使用一个主题：

- `dark`：默认，近黑极客风（终端绿网格 + 青绿点缀）；适合技术突破、发布、工程、前沿 AI、安全事件。
- `cream`：米白科技风；适合教学、故事、历史、人文、日常。

命令行只在 `run.py` / `gen_hyperframes.py` 选择主题，不提供逐段主题切换。

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
- **整页画布（段落 `layout: "canvas"`，两画幅同一份规则）**：上面那三层塌成一层——配图拉满整个画面（1080×1440 / 1920×1080，圆角、外发光、1px 内描边全撤），标题层与句子流层由渲染器**不生成**（不是 `display:none`：不留死 DOM、不留死补间目标，`check` 的文本普查也如实少两项）。配图**不再生成任何入场补间**——wipe 揭开即要求画面到位，海报再自带淡入会演成"先擦出空页、再浮出画面"的两段式；整页就是画面，滑一下/淡一下都是穿帮。

  这一版把文字对比度从版式责任变成**画布作者的责任**：`Contrast` 门禁只数 HTML 文本，看不见 SVG 里的字（实测同一篇稿去掉那两层后普查 40 → 38 项；一张被 `cover` 裁掉标题的画布照样 0 error、38/38 全过）。所以门禁改在生成期**按文件**拦：该段必须有配图（否则只剩一条进度条空帧），SVG 的固有比例与图内 px 字号都要跟当前画幅对账——判据清单、实测翻车数字和"照片/视频不验比例只给知情 `[warn]`"这一档都写在 `references/image_options.md`「整页画布」，画那张图时按那份执行，这里不复述。想让它真的量一次图内文字，照 `image_options.md`「图内文字的对比度」的内联副本 + `check` 程序办（"交付 HTML 必须保持 `<img>`"的安全理由也写在那份里）。底部进度条与段落底轨在这里抬到 `z-index:2`：槽位版式里媒体够不到页底，画布拉满全屏后一张铺到底的照片会把进度整个盖掉（实测删掉这条规则，页底 24 行像素全是画布填充色）。

opening / closing 默认是**纯文字 agenda 卡**，不配图：kicker（取自顶层 `opening_tagline` / `closing_tagline`）+ 大标题 + 行列表 + verse。这一页自己就是一个版式值 `layout: "agenda"`，由 pipeline 盖章（它不是这一段自己的文字，而是对全片其它段的投影）；作者要把它换成整页海报，用顶层 `opening_layout` / `closing_layout: "canvas"`，那一页就退化成内容段画布那条路：只剩一张满幅配图 + 进度条，标题层、句子流、章节行、`cta` 尾行连 DOM 都不生成——而 `check` 只数 HTML 文本，看不见这种丢失，画面上该有什么、读不读得清，全由那张图自己负责。渲染器只按 `layout` 分派三档版式（`_segments.seg_layout()`），不再按 id 猜。漏盖 `layout` 不会报错也不会塌成槽位页：`seg_layout()` 按 id 兜回 agenda。开屏/结尾行取哪些字段、行数上限、`nameTrim` 字数（含硬截断不补省略号、仅 CSS 补省略号的情况）、标题长度守卫阈值与截断优先级**以 `references/writing.md` 为准**。agenda 列是定高 flex 列，行列表紧跟题头排布（间距 16px），万一仍被撑满，牺牲的是行列表尾部（`overflow:hidden` 裁切），题头与句子流始终完整。agenda 标题两画幅都居左。

画面单位是**段落**。段内画面基本静止，只让字幕句子逐句切换与高亮；不要把本技能改成句子级重画的交互课件。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据：

- 段落入场默认是**方向擦除（wipe）**：新卡用一条 clip-path 从全遮蔽形状补到全覆盖形状，揭开整页（模板 `animation.segmentWipe`，几何档 0.28s / expo.out）；`style` 全片统一一档——`line`（**默认**，引导线：见下）、`vertical`（自下而上 inset 揭屏）、`diagonal`（顶边 30% 斜度、左角先行的斜向擦除）、`circle`（中心向外，71% = 圆心到角点的精确半径，任意画幅同值）；写错档生成期直接报错。wipe 三档旧卡**不淡出**，被新卡盖住后随引擎窗口切走。选它而不是 cross-fade：两页互相透明度溶解在成片里是"凭空消失再出现"的廉价信号（PPT 观感，实测被否）；wipe 全程只揭一层不透明页，方向感来自遮盖本身。
- **`line` 引导线转场（进度条立起来画下一页）**：道具必须是画面本来就有的元素——外来物（razor/pull 两版 SVG 刀具，先后被否）读起来永远是"贴纸在演"。画面里唯一自带方向感的运动体是底部进度条（随朗读从左往右），`line` 档就让它续命：转场时一条 accent 高亮线从页底"脱开"向上扫，**线的下缘就是新卡 clip-path 的揭示边**（同窗同曲线，`_wipe_ease` 单一口径，两处各读各的=漂移穿帮），新页像被这条线画出来；旧页被线犁过之后整层上移剥离（`peelFrac` 屏高 + `peelRotation` 逆旋 + `peelTilt` 绕底边轴（`transformOrigin:"50% 100%"`）的 `rotationX` 透视后倒、`peelPerspective` 焦距，纸真正"揭"起来而非图层平移；`peelEase` power2.in）。clip-path 在元素自身平面内先裁后变换，3D 不破坏揭开边。光影补全：被揭旧卡挂一层底缘暗边 `.peel-shade`（`peelShadeFrac` 屏高的黑渐变，opacity 与 peel 同窗拉起——折页线附近最暗，Material elevation 做法；不用 box-shadow 是因为外投影会被卡自身 clip-path 整层裁掉、inset 内阴影又会被后画的 `.bg` 子层盖住）；线的辉光下偏（`box-shadow` y 偏移），光只洒在刚被画出的页面上——均匀四散是"发光条"，下洒才是"光源在画"。动效精修（首版 expo.out 0.28s 被裁"闪现不是引导"）：笔程走 `propLine.duration`（0.40s，几何档仍 0.28）+ `propLine.ease` power2.inOut 的书写节奏（慢起—快行—慢收），线形是两端渐隐、中心提亮一枚"笔尖"的彗尾渐变（硬条读不出笔触）；扫到顶恰好缩没进边沿即收笔。线是新卡的末子（`propLine.thickness` px，颜色走新卡的 `--seg-accent`），随父卡 clip-path 只露出揭示边以下部分——无需独立道具层，层叠天然正确。wipe 被 gap 钳到 0 的段线与剥离都不生成（瞬间切）。
- 时长被句间静音钳制：gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。`wipe = min(基准时长, gap)`（基准：几何档 `segmentWipe.duration`，line 档 `propLine.duration`），入场提前铺到 `win_start = s − wipe`，恰好在本段音频起点完成揭屏（line 档的线与剥离同窗同值）。字幕住在卡里，转场早于上一句念完就开始，等于下一段文字压着还在讲的话。
- **揭屏能成立的前提是卡元素的 `data-duration` 覆盖整个入场窗口**：hyperframes 引擎按 `data-start`/`data-duration`
  硬切 clip 可见性，补间排在窗口外等于没写（这正是旧 cross-fade 版查出的 bug：边界帧全黑、那一刻 GSAP opacity 仍是 0.999）。窗口 `[win_start, next_start]` 与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐；末卡窗口铺到成片结束。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`，压在氛围光 `::after` 与内容之下）。透明卡做 wipe 时上一段文字会从新页底下透出，画面出现双影叠字（逐帧实拍复现）。
- title / image 按模板参数入场；tagline 无独立补间，随段落卡整体显隐。
  **CSS 不得给 GSAP 补间的元素声明 `transform`**（`#title-*` 的 scale、`#img-*` 的 y 滑入都写 inline transform，与样式表里的 `transform` 互斥，只能活一个）。要居中/位移用绝对定位的 auto 外边距，别用 `translateX(-50%)`（曾因此让竖屏配图的 y 滑入整条失效，实测过程见 `git log`）。
- 底部 progress bar 与段落时间轴同步。
- verse 当前句用该段 accent 色高亮（附荧光笔式渐变下划线），字重不切换，避免横向跳动；cream 浅底下高亮字与 agenda 序号自动改用同色相压暗一档的文本色（`--seg-accent-text`），装饰氛围光/进度条仍用原色。
  切换在成片里是**瞬时**的：逐帧 seek 的渲染要求每一帧都等于时间线时刻，所以字幕的淡入/滚动补间不在 CSS 里，只由 `preview.js` 在人工预览分支注入。给 `.verse*` 加 `transition` 会把墙上时钟漏进成片（实测 seek 后计算样式停在过渡起点，句子流不跟着滚动），别加。
- 每段的 accent 会派生一组装饰（一律经 CSS `color-mix`，不引入新的色值令牌）：画面中央的氛围光、配图槽位的外发光与 1px 内描边、tagline 左侧刻度条、进度条辉光——选 accent 时注意它会染整帧氛围。槽位外发光的半径走 `--ctv-img-glow`（模板 `image.glow`），颜色不写进 inline style；挂 `bare-media` 的 SVG 配图槽位不吃外发光与描边，理由见 `references/image_options.md` 的「不铺满幅底」一节。

## 版式真源

画布尺寸、标题区、句子流、圆角、动画等版式数值都从 `scripts/_template.py` 派生 CSS 变量。新增视觉参数一律先加到 `_template.py`，再由 HTML renderer 注入 `--ctv-*` 变量。

模板改动后直接重新生成 HTML，在浏览器打开 `hf-project/index.html` 逐段看一遍预览（版式问题在这一步发现最便宜）。

## 配图

- 内容段画布：尺寸与配图规格见 `references/image_options.md`（第 4 步的配图规则以它为准）；素材一律 `cover` 铺满画布。
- 视频素材按 `loop muted autoplay playsinline` 播放，播放进度与段落时间轴**不做逐帧同步**：段落显示多久由时间轴决定，视频只是循环填充；比段落长的视频渲染只显示前段（生成时有 warn 提示）。代价是同一秒的画面不保证可复现——`<video>` 走页面墙钟，多 worker 各自从 0 起播，抓到的帧可能不同；对"必须对上某句话"的画面改用剪好的静态poster序列或换段落，别指望视频帧同步。是否观感正确仍以预览和成片为准。
- 配图语义是否正确只能靠人眼判断（预览 + 成片），工具不会替代内容判断。

## HTML 预览与渲染

### 1. 生成 HTML

```bash
python scripts/gen_hyperframes.py   -m audio_output/timing_manifest.json   -o hf-project/index.html   --images hf-project/images.json   --fps 24
```

画幅与主题在这一步烧进 HTML，渲染分辨率随之切换（默认 portrait + dark，见 SKILL.md「常用参数」）。

生成目录应至少包含：

```text
hf-project/
├── index.html
├── preview.js
├── vendor/
│   └── gsap.min.js
├── audio/
└── images/
```

`vendor/` 由 `gen_hyperframes.py` 在这一步自动装好——取用链、哈希钉固与取不到时的处置口径，见 SKILL.md「环境」的 GSAP 一段。

音频路径会在生成 HTML 前做存在性检查；manifest 中的绝对路径或项目外音频会复制到项目的 `audio/` 目录，显式 `--audio` 也遵循同一规则。manifest 的音频路径失效时会回落到项目里上次暂存的 `audio/combined.wav`——这会打 `[warn]` 并实测其时长与 manifest 时间轴对账，偏差超过 `max(1s, 2%)` 直接拒跑（拒绝用新字幕烧旧音轨）；两处都没有才报错，不生成无声预览或成片。

### 2. 浏览器预览

预览行为与检查清单见 SKILL.md 第 5 步；这里只说边界：`preview.js` 只在真实浏览器里注入 UI，headless（渲染）走的是另一条判定，不会把控制条带进出片；headless 调试可在 URL 加 `?preview` 强制注入预览 UI。

### 3. 正式渲染

渲染直接跑 `run.py`（不带 `--until`，命令见 SKILL.md 第 5 步）：`run.py` 会重新生成一次 HTML 再进入 render，渲染阶段内置文件稳定性等待器——Hyperframes/Node/Chrome 即使在 MP4 写完后没有及时退出，也会等待文件稳定并在必要时清理本次 render 的进程树。

## 性能参数

- `fps`：默认 24；抓帧耗时与帧数严格线性，是唯一的一阶杠杆。快速看画面用 `--fps 12 --quality draft`，别拿定稿规格反复试。
- `workers`：默认 4，`run.py` 字面透传给 `hyperframes render --workers`，没有自动校准。8 之前有效、8 之后饱和；迭代片可显式 `--workers 8`；含视频素材或遇 V8 堆崩溃时降到 2。每个 worker 是一个独立 Chrome（约 256MB 常驻）。渲染明显偏慢时先在 `out.render.log` 找 `Parallel capture timed out`（它会重抓全部帧并自动降 worker），别默认是稿件变长了。
- `quality`：`draft` / `standard` / `high`；draft 约省 15%，预览够用。
- `gpu`：默认关。增益只来自抓帧的 GPU 光栅化，编码阶段几乎不变，代价是成片体积约 +61%。
- **固定开销**：每调用一次渲染约白付 8.5s（与帧数无关），冷跑第一次还多约 6s。所以"改一处重渲一次"很不划算：先 `--until html` + 快照看够，再整片渲一次。

## 官方校验命令（优先于手搓探针）

`npx -y hyperframes` 自带三个命令，覆盖本技能原先手工搭的 headless 探针：

```bash
npx -y hyperframes check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

- `check` 会真报问题：字体栈里出现未声明 `@font-face` 的家族名判 **error**（`font_family_without_font_face`，判定基于名字而非解析结果）——`composition.css` 因此为 CJK 兜底名 `Noto Sans CJK SC/JP` 也补了 `local()` 声明。同一处还有条静默代价：家族名必须是它字体映射表认识的写法，CSS 惯例名 `SFMono-Regular` 不在表内（表里认 `"SF Mono"`），命中不了只打一条 `[WARN] No deterministic font mapping`，那一族就拿不到注入的 `@font-face`。`_template.py` 的 `monoStack` 已按映射表写，改字体栈要对着这条 WARN 改。
- 一份干净产物的 `check` 基线（竖横各跑一次实测）：Lint 只有下面四条固定噪声，Runtime / Layout / Motion / Contrast 全 0 error。四条已知可接受：`gsap_callback_dom_measurement`（verse 滚动测量是懒缓存 + seek 幂等，见 `templates/runtime.js` 注释）、`nested_structure_needs_subcomposition`（每段一条）、`timeline_track_too_dense`（同一合成根下 7 段就是 7 个 timed element，是上一条的另一种说法）、`negative_z_index`（两画幅各一条 `.seg-card::after`）。不拆 sub-composition 是刻意的：单文件便于 `--until html` 后人工审阅，且分段渲染实测更慢（见上文「性能参数」）。`negative_z_index` 是误报——浏览器实测 `getComputedStyle(.seg-card).isolation` 为 `isolate`，氛围光确实压在卡片内容之下、页面背景之上，检查器不认 `isolation` 建的层叠上下文，而它自己给的 Fix 就是"加 `isolation: isolate`"（`composition.css` 已加）。基线之外的新增条目一律当真读。
- Runtime 的 `clip_media_fit` 和 Layout 的 `clipped_text` **不在噪声之列**：前者是音频实际时长短于 `data-duration`（成片被截到音频长度、字幕时间轴对不上），真实 pipeline 产物的 `total_duration` 就是量出来的音频时长，正常不该出现，手写/裁剪 manifest 时它是"manifest 与音频不同步"的唯一信号（实测把 `total_duration` 对齐音频后该条消失）；后者是某行文本被自己的盒子裁掉，竖屏 agenda 行的 `nameTrim` 只能挡字数超限，挡不住半角/混排的实际字宽（Python 侧 warn 与它两道闸各管一头，见 `writing.md`）。
- `snapshot --at` 精确取时刻帧并自动拼 contact sheet，比"复制 HTML + 注入 `tl.pause(t)` + Chrome `--screenshot`"省事且不会踩 vendor 相对路径的坑；手搓探针只在需要同一页连取多帧、或 `snapshot` 不可用时作后备。
- 渲染前判断本机依赖齐不齐，用 `npx -y hyperframes doctor`：全绿就可以直接跑真渲染，不必止步于 HTML 预览（Chrome headless shell 由 Hyperframes 自己下载并缓存在 `~/.cache/hyperframes/chrome/`，FFmpeg 走第 3 步那条查找链）。
