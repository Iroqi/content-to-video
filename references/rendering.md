# 渲染参考

**双画幅**（portrait 1080×1440 / landscape 1920×1080，`--aspect` 选择，默认 portrait），版式三档、一律按段落 `layout` 分派（见下「画面结构」），段落之间统一方向擦除（wipe，见「动画」）。

## 主题

整期统一使用一个主题：

- `dark`：默认，近黑极客风（终端绿网格 + 青绿点缀）；适合技术突破、发布、工程、前沿 AI、安全事件。

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

  画布页**不要自画满幅底板**：这一页底下模板本来就画着三层——主题渐变 `.bg`、只在页缘显形的网格 `.grid`、本段 accent 的氛围光 `::after`（画布上换成近全屏的宽带柔光，见 `templates/composition.css` 画布块），SVG 的透明处就是这三层在出景深；一张满幅 `<rect>` 会把三层整个盖掉，画面立刻退回一片死平。为什么留透明、什么时候确实该自铺一张，见 `references/image_options.md`「不铺满幅底」。

  这一版把文字对比度从版式责任变成**画布作者的责任**：`Contrast` 门禁只数 HTML 文本，看不见 SVG 里的字（实测同一篇稿去掉那两层后普查 40 → 38 项；一张被 `cover` 裁掉标题的画布照样 0 error、38/38 全过）。所以门禁改在生成期**按文件**拦：该段必须有配图（口径与报错见 `references/writing.md` 段落 `layout`），SVG 的固有比例与图内 px 字号都要跟当前画幅对账——判据清单、实测翻车数字和"照片/视频不验比例只给知情 `[warn]`"这一档都写在 `references/image_options.md`「整页画布」，画那张图时按那份执行，这里不复述。想让它真的量一次图内文字，照 `references/image_options.md`「图内文字的对比度」的内联副本 + `check` 程序办（"交付 HTML 必须保持 `<img>`"的安全理由也写在那份里）。`check_svg.py --layout canvas` 现在会按**主题渐变的最坏一档**给字面 hex 填充算一次对比度（画布档只 warn 不 error：压在作者自画的浅色局部底板上的字，按页底算出来的数对它不成立），但渐变底、`class` 里的色、局部底板上的字它都管不着，那一次真测仍然要做。底部进度条与段落底轨在这里抬到 `z-index:2`：槽位版式里媒体够不到页底，画布拉满全屏后一张铺到底的照片会把进度整个盖掉（实测删掉这条规则，页底 24 行像素全是画布填充色）。

opening / closing 默认是**纯文字 agenda 卡**，不配图：kicker（取自顶层 `opening_tagline` / `closing_tagline`）+ 大标题 + 行列表 + verse。这一页自己就是一个版式值 `layout: "agenda"`，由 pipeline 盖章（它不是这一段自己的文字，而是对全片其它段的投影）；作者要把它换成整页海报，用顶层 `opening_layout` / `closing_layout: "canvas"`，那一页就退化成内容段画布那条路：只剩一张满幅配图 + 进度条，标题层、句子流、章节行、`cta` 尾行连 DOM 都不生成——而 `check` 只数 HTML 文本，看不见这种丢失，画面上该有什么、读不读得清，全由那张图自己负责。渲染器只按 `layout` 分派三档版式（`_segments.seg_layout()`），不再按 id 猜。漏盖 `layout` 不会报错也不会塌成槽位页：`seg_layout()` 按 id 兜回 agenda。开屏/结尾行取哪些字段、行数上限、`nameTrim` 字数（含硬截断不补省略号、仅 CSS 补省略号的情况）、标题长度守卫阈值与截断优先级**以 `references/writing.md` 为准**。agenda 列是定高 flex 列，行列表紧跟题头排布（间距 16px），万一仍被撑满，牺牲的是行列表尾部（`overflow:hidden` 裁切），题头与句子流始终完整。agenda 标题两画幅都居左。

画面单位是**段落**。段内画面基本静止，只让字幕句子逐句切换与高亮；不要把本技能改成句子级重画的交互课件。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据：

- 段落入场默认是**方向擦除（wipe）**：新卡用一条 clip-path 从全遮蔽形状补到全覆盖形状，揭开整页（几何档时长与缓动取模板 `animation.segmentWipe`）；`style` 全片统一一档——`line`（**默认**，引导线：见下）、`vertical`（自下而上 inset 揭屏）、`diagonal`（顶边 30% 斜度、左角先行的斜向擦除）、`circle`（中心向外，71% = 圆心到角点的精确半径，任意画幅同值）；写错档生成期直接报错。wipe 三档旧卡**不淡出**，被新卡盖住后随引擎窗口切走。选它而不是 cross-fade：两页互相透明度溶解在成片里是"凭空消失再出现"的廉价信号（PPT 观感，实测被否）；wipe 全程只揭一层不透明页，方向感来自遮盖本身。**整页画布段除外**（`layout: "canvas"` 一律硬切，理由与代价见本节「整页画布段」条）。
- **`line` 引导线转场（进度条立起来画下一页）**：道具必须是画面本来就有的元素——外来物（razor/pull 两版 SVG 刀具，先后被否）读起来永远是"贴纸在演"。画面里唯一自带方向感的运动体是底部进度条（随朗读从左往右），`line` 档就让它续命：转场时一条 accent 高亮线从页底"脱开"向上扫，**线的下缘就是新卡 clip-path 的揭示边**（同窗同曲线，`_wipe_ease` 单一口径，两处各读各的=漂移穿帮），新页像被这条线画出来；旧页被线犁过之后整层上移剥离（`peelFrac` 屏高 + `peelRotation` 逆旋 + `peelTilt` 绕底边轴（`transformOrigin:"50% 100%"`）的 `rotationX` 透视后倒、`peelPerspective` 焦距，纸真正"揭"起来而非图层平移；`peelEase` power2.in）。clip-path 在元素自身平面内先裁后变换，3D 不破坏揭开边。光影补全：被揭旧卡挂一层底缘暗边 `.peel-shade`（`peelShadeFrac` 屏高的黑渐变，opacity 与 peel 同窗拉起——折页线附近最暗，Material elevation 做法；不用 box-shadow 是因为外投影会被卡自身 clip-path 整层裁掉、inset 内阴影又会被后画的 `.bg` 子层盖住）；线的辉光下偏（`box-shadow` y 偏移），光只洒在刚被画出的页面上——均匀四散是"发光条"，下洒才是"光源在画"。动效精修（首版沿用几何档被裁"闪现不是引导"）：笔程走 `propLine.duration`（line 档比几何档长）+ `propLine.ease` power2.inOut 的书写节奏（慢起—快行—慢收），线形是两端渐隐、中心提亮一枚"笔尖"的彗尾渐变（硬条读不出笔触）；扫到顶恰好缩没进边沿即收笔。线是新卡的末子（`propLine.thickness` px，颜色走新卡的 `--seg-accent`），随父卡 clip-path 只露出揭示边以下部分——无需独立道具层，层叠天然正确。wipe 被 gap 钳到 0 的段线与剥离都不生成（瞬间切）。
- 时长被句间静音钳制：gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。`wipe = min(基准时长, gap)`（基准：几何档 `segmentWipe.duration`，line 档 `propLine.duration`），入场提前铺到 `win_start = s − wipe`，恰好在本段音频起点完成揭屏（line 档的线与剥离同窗同值）。字幕住在卡里，转场早于上一句念完就开始，等于下一段文字压着还在讲的话。
- **揭屏能成立的前提是卡元素的 `data-duration` 覆盖整个入场窗口**：hyperframes 引擎按 `data-start`/`data-duration`
  硬切 clip 可见性，补间排在窗口外等于没写（这正是旧 cross-fade 版查出的 bug：边界帧全黑、那一刻 GSAP opacity 仍是 0.999）。窗口 `[win_start, next_start]` 与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐；末卡窗口铺到成片结束。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`，压在氛围光 `::after` 与内容之下）。透明卡做 wipe 时上一段文字会从新页底下透出，画面出现双影叠字（逐帧实拍复现）。
- **整页画布段（`layout: "canvas"`）不参与模板转场**：它的 `wipe` 在 clips 预处理里被强制归零，命中既有的"瞬间切"路径——引导线、旧页剥离、底缘暗边三处都以 `wipe<=0` 为闸自动不生成，画布页在自己的**音频起点**整页出现（不向后借擦除窗口，因此导演每一拍的时间锚点都与画面严格对齐），退场则被下一页直接盖住；相邻的 slot/agenda 段转场原样保留（画布页作为"上一页"时也不被剥走——把一张活 diagram 像纸一样掀起来正是"演"要取代的翻页感）。理由：擦除/剥离是**图层级**的翻页语言，与导演逐拍揭示同一页内部元素是两种叙事，叠在一起只会让第一拍落在还没揭完的页上互相抢戏；画布段是"一个连续镜头"，不需要转场道具。此规则按**版式**判定而非按擦除档，四档 `style` 下一律生效。代价是硬的：切过去的那一瞬间画布还是空的（导演从空场开始逐拍画），要消除这段空窗就得让导演的第一拍尽早落子（`at_time` 靠前的锚点、或第一拍即为整页底图/坐标轴）——见 `references/image_options.md`「SVG 动画：两档」。**同一档硬切还给了另一种页**：images.json 写 `stage: "keep"` 的跨段接续页也被强制 `wipe=0`、也不生成配图入场补间，因为它的起点是上一页演完的那幅画面，再演一次揭页就是穿帮（口径见 `references/image_options.md`「跨段场景延续」）。
- title / image 按模板参数入场；tagline 无独立补间，随段落卡整体显隐。
  **CSS 不得给 GSAP 补间的元素声明 `transform`**（`#title-*` 的 scale、`#img-*` 的 y 滑入都写 inline transform，与样式表里的 `transform` 互斥，只能活一个）。要居中/位移用绝对定位的 auto 外边距，别用 `translateX(-50%)`（曾因此让竖屏配图的 y 滑入整条失效，实测过程见 `git log`）。
- 底部 progress bar 与段落时间轴同步。
- verse 当前句用该段 accent 色高亮（附荧光笔式渐变下划线），字重不切换，避免横向跳动。
  切换在成片里是**瞬时**的：逐帧 seek 的渲染要求每一帧都等于时间线时刻，所以字幕的淡入/滚动补间不在 CSS 里，只由 `preview.js` 在人工预览分支注入。给 `.verse*` 加 `transition` 会把墙上时钟漏进成片（实测 seek 后计算样式停在过渡起点，句子流不跟着滚动），别加。
- 每段的 accent 会派生一组装饰（一律经 CSS `color-mix`，不引入新的色值令牌）：氛围光（槽位在画面中央一小团，整页画布换成近全屏的宽带柔光，因为画布页的景深全靠它）、配图槽位的外发光与 1px 内描边、tagline 左侧刻度条、进度条辉光——选 accent 时注意它会染整帧氛围。槽位外发光的半径走 `--ctv-img-glow`（模板 `image.glow`），颜色不写进 inline style；挂 `bare-media` 的 SVG 配图槽位不吃外发光与描边，理由见 `references/image_options.md` 的「不铺满幅底」一节。

### 蘋果風開場（`opening_animation: "apple"`）

开屏 agenda 卡的整页编排，取代通用标题入场（两条补间同元素会打架）。取值与写稿口径见 `references/writing.md` 的 `opening_animation`，参数真源在 `_template.py` 的 `animation.opening.apple`。

- **整支舞按开屏页的可见窗口等比归一**。收尾时刻由 agenda 行数决定（`rows.delay + stagger×(行数−1) + duration`：7 行 2.12s、1 行 1.40s），而它能用的时长是**可见窗口**（`[擦除起点, 被下一页盖住]`，不是口播段长——短开场段里两者差着一整个 gap）。装得下原速，装不下按 `可见窗口 ÷ 收尾时刻` 等比压缩，地板 0.5（`animation.opening.apple.budget.minFactor`）：压到一半就停手，再压就成闪烁。节奏比例不变，所以压缩后仍是一支舞，不是一个被赶过的动画。
- **光晕呼吸的时长不参与压缩**。它是淡入之后的稳态循环（`repeat:-1 yoyo`），压它等于让开场一直喘，而且它不参与"舞演完没有"的判定——压缩只落在淡入时长与呼吸起点上。
- **地板仍装不下时打 `[warn]`**，点名可见窗口、需要的时长与行数。症状与去处见 `references/image_options.md` 的「已知翻车速查表」。
- 锚点是**擦除起点 `win_start`** 而不是段起点：页面从 `clip-path` 里被擦开的同时内容就在演化。标题若等擦完再出现，会先"完整亮 0.28s 再跳回模糊起点"。

## 版式真源

画布尺寸、标题区、句子流、圆角、动画等版式数值都从 `scripts/_template.py` 派生 CSS 变量，配色由 `scripts/_theme.py` 派生。新增视觉参数一律先加进这两个 py，再由 HTML renderer 注入 `--ctv-*` 变量；哪些常量允许留在 `templates/composition.css`（以及 `@font-face`、`verse.clipPad` 这两处与模板值的联动代价）写在 CSS 自己的文件头与各块注释里。

模板改动后直接重新生成 HTML，在浏览器打开 `hf-project/index.html` 逐段看一遍预览（版式问题在这一步发现最便宜）。

## 配图

- 内容段画布：尺寸与配图规格见 `references/image_options.md`（第 4 步的配图规则以它为准）；素材一律 `cover` 铺满画布。
- 配图语义是否正确只能靠人眼判断（预览 + 成片），工具不会替代内容判断。
- **方式 C SVG 的「导演」（时间轴同步动画）**：images.json 给该段写 `director` 时，这张 SVG 经 `scripts/_svg_sanitize.py` 净化后**内联成活 DOM**，`director.steps` 按句起点展开成 GSAP 补间（见 image_options.md 方式 C「SVG 动画：两档」）。导演是**逐帧可复现的属性补间**（挂在同一条时间线上，位置 = `seg.sentences[at].start_time`），seek 到同一时刻必然同一帧，正因为如此内联时会剥掉 SMIL / CSS 动画这类墙钟运动。安全代价（内联=同源活节点）由净化兜住：`<script>`/`on*`/`<foreignObject>`/外链一律删除。要时间轴同步的状态变化用它，纯氛围循环仍走 `<img>`+墙钟。

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
- `workers`：默认 4，`run.py` 字面透传给 `hyperframes render --workers`，没有自动校准。8 之前有效、8 之后饱和；迭代片可显式 `--workers 8`；遇 V8 堆崩溃时降到 2。每个 worker 是一个独立 Chrome（约 256MB 常驻）。渲染明显偏慢时先在 `out.render.log` 找 `Parallel capture timed out`（它会重抓全部帧并自动降 worker），别默认是稿件变长了。
- `quality`：`draft` / `standard` / `high`；draft 约省 15%，预览够用。
- **固定开销**：每调用一次渲染约白付 8.5s（与帧数无关），冷跑第一次还多约 6s。所以"改一处重渲一次"很不划算：先 `--until html` + 快照看够，再整片渲一次。

## 官方校验命令（优先于手搓探针）

`npx -y hyperframes` 自带三个命令，覆盖本技能原先手工搭的 headless 探针：

```bash
npx -y hyperframes check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

- `check` 会真报问题：字体栈里出现未声明 `@font-face` 的家族名判 **error**（`font_family_without_font_face`，判定基于名字而非解析结果）——`composition.css` 因此为 CJK 兜底名 `Noto Sans CJK SC/JP` 也补了 `local()` 声明。同一处还有条静默代价：家族名必须是它字体映射表认识的写法，CSS 惯例名 `SFMono-Regular` 不在表内（表里认 `"SF Mono"`），命中不了只打一条 `[WARN] No deterministic font mapping`，那一族就拿不到注入的 `@font-face`。`_template.py` 的 `monoStack` 已按映射表写，改字体栈要对着这条 WARN 改。
- 一份干净产物的 `check` 基线（7 段稿竖屏实测，2026-10）：**Lint 12 条 warning 全部是下面这 5 类噪声，Runtime / Motion / Contrast 全 0，Layout 0 error**。看的是**类型**不是条数——其中两条按段数增长，写死条数会自己吓自己：
  - `gsap_callback_dom_measurement`（1 条）：verse 滚动测量是懒缓存 + seek 幂等，见 `templates/runtime.js` 注释。
  - `nested_structure_needs_subcomposition`（**每段一条**，7 段 = 7 条）：不拆 sub-composition 是刻意的——单文件便于 `--until html` 后人工审阅，且分段渲染实测更慢（见上文「性能参数」）。
  - `timeline_track_too_dense`（1 条）：同一合成根下 7 段就是 7 个 timed element，是上一条的另一种说法。
  - `composition_file_too_large`（1 条）：单文件行数超阈，是上面两条的单文件取舍的代价，同源。
  - `negative_z_index`（**每条 `z-index:-1/-2` 规则一条**）：氛围光 `.seg-card::after` + 背景两层 `.seg-card>.bg,.grid`。`negative_z_index` 是误报——浏览器实测 `getComputedStyle(.seg-card).isolation` 为 `isolate`，氛围光确实压在卡片内容之下、页面背景之上，检查器不认 `isolation` 建的层叠上下文，而它自己给的 Fix 就是"加 `isolation: isolate`"（`composition.css` 已加）。
  - **判据**：这 5 类之外的任何新增条目一律当真读。条数对不上不用慌（段数变了就变），**类型多出一类就要查**。反过来，Layout 段出现的 `✗` 要当真——它量的是真实版面重叠，`check_svg` 查不到图内两行文字压字（见 `image_options.md`「画布几何」末条）；已知唯一的例外是 `text_occluded`，见下条。
- Runtime 的 `clip_media_fit` 和 Layout 的 `clipped_text` **不在噪声之列**：前者是音频实际时长短于 `data-duration`（成片被截到音频长度、字幕时间轴对不上），真实 pipeline 产物的 `total_duration` 就是量出来的音频时长，正常不该出现，手写/裁剪 manifest 时它是"manifest 与音频不同步"的唯一信号（实测把 `total_duration` 对齐音频后该条消失）；后者是某行文本被自己的盒子裁掉，竖屏 agenda 行的 `nameTrim` 只能挡字数超限，挡不住半角/混排的实际字宽（Python 侧 warn 与它两道闸各管一头，见 `references/writing.md`「开场/结尾专用顶层字段」）。
- Layout 的 `text_occluded` **曾经误报，现在不会再报**：句子流滚出 `.verse-clip` 窗口的行，视觉上被 `overflow:hidden` 裁掉，但静态 DOM rect 仍在原位，逐行量它就会判"文字藏在不透明元素下"。豁免靠 `data-layout-allow-occlusion` 等三个属性，而**检查器只认元素自己身上的标记、不继承祖先的**——原先只打在 `.verse` 上，检查器照样去量它下面的每一行。模板已在**每一行 `.verse-line`** 上也打上（`html_renderer.py`），实测 7 段竖屏稿：只打 `.verse` 时报 1 error，每行补齐后 0 error 且 warning 条数不变。**再见到 `text_occluded` 先别当版面 bug**——先用 `snapshot --at <时刻>` 看画面：文字真被遮（配图压住句子流）就调版式，画面正常则是这个漏判回来了。
- `snapshot --at` 精确取时刻帧并自动拼 contact sheet，比"复制 HTML + 注入 `tl.pause(t)` + Chrome `--screenshot`"省事且不会踩 vendor 相对路径的坑；手搓探针只在需要同一页连取多帧、或 `snapshot` 不可用时作后备。
- 渲染前判断本机依赖齐不齐，用 `npx -y hyperframes doctor`：全绿就可以直接跑真渲染，不必止步于 HTML 预览（Chrome headless shell 由 Hyperframes 自己下载并缓存在 `~/.cache/hyperframes/chrome/`，FFmpeg 走第 3 步那条查找链）。
