# 渲染参考

**双画幅**：portrait 是 1080×1440，landscape 是 1920×1080。画幅用 `--aspect` 选择，默认 portrait。版式分三档，管线一律按段落 `layout` 分派（见下「画面结构」）。段落之间统一方向擦除（wipe，见「动画」）。

## 主题

整期统一使用一个主题：

- `dark`：默认主题。它是近黑极客风，带终端绿网格与青绿点缀。它适合技术突破、发布、工程类选题。它也适合前沿 AI 与安全事件。
- `cream`：米白科技风。它适合教学、故事、历史类选题。它也适合人文与日常。

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

标题区、画布、verse 各自独立定位。这句只对**竖屏**成立。标题从一行变成两行时，只改变标题区内部排版。**画布的位置与尺寸不变，verse 也不跟着移动**。

横屏的标题与 verse 同处左文字栏这一个 flex 列（整列垂直居中）。标题折行时，verse 会随整列重新居中。两画幅的画布都不受标题影响。

- **portrait**：标题区在上，**定高但由模板派生**（`title.maxLines × title.fontSize × typography.titleLineHeight + tagline.marginTop + tagline.fontSize × tagline.lineHeight`）。改模板数字即跟着变，模板里不再存面积高度。标题区另有两行钳制，超出两行的部分裁掉。4:3 画布（980×735）居中，verse 钉底。标题区底边一旦压到画布顶边，生成期直接报 `[template] 竖屏标题区放不进` 错误。改版式把重叠暴露成失败，而不是出一帧重叠的片子。
- **landscape**：左文字栏（标题 + tagline）固定 613px 宽，右侧图栏 1067×800。**没有行数钳制**。标题随长度自由折行，字号由守卫降档。超过 16 字降到 54px。超过 22 字降到 48px。超过 16 字就报一条 `[warn]`。两档都报，不是只有 48px 那档。
- **整页画布（段落 `layout: "canvas"`，两画幅同一份规则）**：上面那三层塌成一层。配图拉满整个画面（1080×1440 / 1920×1080）。圆角、外发光、1px 内描边全部撤掉。标题层与句子流层由渲染器**不生成**。这不是 `display:none`：它不留死 DOM，也不留死补间目标。`check` 的文本普查也如实少两项。配图**不再生成任何入场补间**。wipe 揭开就要求画面到位。海报再自带淡入会演成两段式：先擦出空页，再浮出画面。整页就是画面。滑一下或淡一下都是穿帮。

  画布页**不要自画满幅底板**。这一页底下模板本来就画着三层。三层是主题渐变 `.bg`、只在页缘显形的网格 `.grid`、本段 accent 的氛围光 `::after`。画布上，这层氛围光换成近全屏的宽带柔光（见 `templates/composition.css` 画布块）。

  SVG 的透明处就是这三层在出景深。一张满幅 `<rect>` 会把三层整个盖掉，画面立刻退回一片死平。为什么留透明、什么时候确实该自铺一张、以及对比度门禁怎么跟着 `--theme` 变，见 `references/image_options.md`「不铺满幅底」。

  这一版把文字对比度从版式责任变成**画布作者的责任**。`Contrast` 门禁只数 HTML 文本，看不见 SVG 里的字。实测同一篇稿去掉那两层后，普查从 40 项降到 38 项。一张 `cover` 裁掉标题的画布照样 0 error、38/38 全过。

  门禁因此改在生成期**按文件**拦。该段必须有配图，口径与报错见 `references/writing.md` 段落 `layout`。SVG 的固有比例与图内 px 字号都要跟当前画幅对账。判据清单和实测翻车数字都写在 `references/image_options.md`「整页画布」。照片/视频不验比例只给知情 `[warn]` 这一档也写在那里。画那张图时按那份执行，这里不复述。

  想让它真的量一次图内文字，照 `references/image_options.md`「图内文字的对比度」的内联副本 + `check` 程序办。那份里也写了这条安全理由：交付 HTML 必须保持 `<img>`。

  `check_svg.py --layout canvas` 现在会按**主题渐变的最坏一档**给字面 hex 填充算一次对比度。画布档只 warn 不 error。压在作者自画的浅色局部底板上的字，按页底算出来的数对它不成立。但渐变底、`class` 里的色、局部底板上的字它都管不着。那一次真测仍然要做。

  管线在这里把底部进度条与段落底轨抬到 `z-index:2`。槽位版式里媒体够不到页底。画布拉满全屏后，一张铺到底的照片会把进度整个盖掉。实测删掉这条规则后，页底 24 行像素全是画布填充色。

opening / closing 默认是**纯文字 agenda 卡**，不配图：kicker（取自顶层 `opening_tagline` / `closing_tagline`）+ 大标题 + 行列表 + verse。这一页自己就是一个版式值 `layout: "agenda"`，由 pipeline 盖章。它不是这一段自己的文字，而是对全片其它段的投影。

作者要把它换成整页海报，用顶层 `opening_layout` / `closing_layout: "canvas"`。那一页就退化成内容段画布那条路：只剩一张满幅配图 + 进度条。标题层、句子流、章节行、`cta` 尾行连 DOM 都不生成。`check` 只数 HTML 文本，看不见这种丢失。画面上该有什么、读不读得清，全由那张图自己负责。

渲染器只按 `layout` 分派三档版式（`_segments.seg_layout()`），不再按 id 猜。漏盖 `layout` 不会报错也不会塌成槽位页：`seg_layout()` 按 id 兜回 agenda。

开屏/结尾行取哪些字段、行数上限，这两项**以 `references/writing.md` 为准**。`nameTrim` 字数的口径同样以它为准，含硬截断不补省略号、仅 CSS 补省略号的情况。标题长度守卫阈值与截断优先级也以它为准。

agenda 列是定高 flex 列，行列表紧跟题头排布（间距 16px）。万一仍被撑满，牺牲的是行列表尾部（`overflow:hidden` 裁切）。题头与句子流始终完整。agenda 标题两画幅都居左。

画面单位是**段落**。段内画面基本静止，只让字幕句子逐句切换与高亮。不要把本技能改成句子级重画的交互课件。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据：

- 段落入场默认是**方向擦除（wipe）**。新卡用一条 clip-path 从全遮蔽形状补到全覆盖形状，揭开整页。几何档时长与缓动取模板 `animation.segmentWipe`。
- `style` 全片统一一档，共四档。`line`（**默认**）是引导线档，见下。`vertical` 自下而上 inset 揭屏。`diagonal` 是斜向擦除，顶边 30% 斜度、左角先行。`circle` 从中心向外扩，71% = 圆心到角点的精确半径，任意画幅同值。写错档生成期直接报错。
- wipe 三档旧卡**不淡出**。新卡盖住旧卡，引擎窗口切走时旧卡随之消失。这里选 wipe，不选 cross-fade。两页互相透明度溶解，在成片里是凭空消失再出现的廉价信号。这是 PPT 观感，实测否掉了它。wipe 全程只揭一层不透明页，方向感来自遮盖本身。
- **整页画布段除外**（`layout: "canvas"` 一律硬切，理由与代价见本节「整页画布段」条）。
- **`line` 引导线转场（进度条立起来画下一页）**：道具必须是画面本来就有的元素。外来物读起来永远是贴纸在演。razor/pull 两版 SVG 刀具先后被否。画面里唯一自带方向感的运动体是底部进度条。它随朗读从左往右。`line` 档就让它续命。
- 转场时一条 accent 高亮线从页底脱开，向上扫。**线的下缘就是新卡 clip-path 的揭示边**。两者同窗同曲线，`_wipe_ease` 是单一口径。两处各读各的=漂移穿帮。新页因此像这条线画出来的。
- 线犁过旧页后，整层上移剥离。剥离由 `peelFrac` 屏高、`peelRotation` 逆旋、`peelTilt` 绕底边轴的 `rotationX` 透视后倒组成。`peelPerspective` 是焦距。底边轴即 `transformOrigin:"50% 100%"`。纸真正揭起来，不是图层平移。
- `peelEase` 取 power2.in。clip-path 在元素自身平面内先裁后变换，3D 不破坏揭开边。
- 光影补全另有一层。正在揭起的旧卡挂一层底缘暗边 `.peel-shade`。它是 `peelShadeFrac` 屏高的黑渐变。它的 opacity 与 peel 同窗拉起。折页线附近最暗，这是 Material elevation 做法。
- 这里不用 box-shadow。卡自身的 clip-path 会把外投影整层裁掉。后画的 `.bg` 子层又会盖住 inset 内阴影。
- 线的辉光下偏，偏移写在 `box-shadow` 的 y 分量。光只洒在刚画出的页面上。均匀四散是发光条。下洒才是光源在画。
- 动效经过精修。首版沿用几何档，被判成闪现不是引导。笔程走 `propLine.duration`，line 档比几何档长。`propLine.ease` 取 power2.inOut，走慢起—快行—慢收的书写节奏。
- 线形是彗尾渐变，两端渐隐、中心提亮一枚笔尖。硬条读不出笔触。扫到顶恰好缩没进边沿，这就是收笔。
- 线是新卡的末子，粗细是 `propLine.thickness` px，颜色走新卡的 `--seg-accent`。线随父卡 clip-path，只露出揭示边以下的部分。无需独立道具层，层叠天然正确。
- gap 把 wipe 钳到 0 的段，线与剥离都不生成（瞬间切）。
- 句间静音钳制时长。gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。wipe 按 `wipe = min(基准时长, gap)` 取值。几何档的基准是 `segmentWipe.duration`。line 档的基准是 `propLine.duration`。入场提前铺到 `win_start = s − wipe`。揭屏恰好在本段音频起点完成。line 档的线与剥离同窗同值。字幕在卡片内。转场早于上一句念完就开始。下一段文字因此压着还在朗读的句子。
- **揭屏能成立的前提是卡元素的 `data-duration` 覆盖整个入场窗口**：hyperframes 引擎按 `data-start`/`data-duration`
  硬切 clip 可见性，补间排在窗口外等于没写（这正是旧 cross-fade 版查出的 bug：边界帧全黑、那一刻 GSAP opacity 仍是 0.999）。窗口 `[win_start, next_start]` 与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐。末卡窗口铺到成片结束。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`，压在氛围光 `::after` 与内容之下）。透明卡做 wipe 时上一段文字会从新页底下透出，画面出现双影叠字（逐帧实拍复现）。
- **整页画布段（`layout: "canvas"`）不参与模板转场**。clips 预处理把它的 `wipe` 强制归零。它命中既有的瞬间切路径。引导线、旧页剥离、底缘暗边这三处都以 `wipe<=0` 为闸，自动不生成。
- 画布页在自己的**音频起点**整页出现。它不向后借擦除窗口。导演因此每一拍的时间锚点都与画面严格对齐。下一页直接盖住画布页，这就是它的退场。相邻的 slot/agenda 段转场原样保留。画布页作为上一页时，转场也不会剥走它。
- 把一张活 diagram 像纸一样掀起来，正是"演"要取代的翻页感。理由如下。擦除/剥离是**图层级**的翻页语言。导演逐拍揭示同一页内部元素，这是另一种叙事。两者叠在一起，只会让第一拍落在还没揭完的页上互相抢戏。画布段是一个连续镜头，不需要转场道具。
- 此规则按**版式**判定而非按擦除档，四档 `style` 下一律生效。
- 代价是硬的。切过去的那一瞬间，画布还是空的。导演从空场开始逐拍画。要消除这段空窗，就得让导演的第一拍尽早落子。
- 落子方式可以是 `at_time` 靠前的锚点。第一拍也可以就是整页底图/坐标轴。见 `references/image_options.md`「SVG 动画：两档」。
- **同一档硬切还给了另一种页**。clips 预处理也把 images.json 里写 `stage: "keep"` 的跨段接续页强制 `wipe=0`。这类页也不生成配图入场补间。它的起点是上一页演完的那幅画面。再演一次揭页就是穿帮。口径见 `references/image_options.md`「跨段场景延续」。
- title / image 按模板参数入场。tagline 无独立补间，随段落卡整体显隐。
  **CSS 不得给 GSAP 补间的元素声明 `transform`**（`#title-*` 的 scale、`#img-*` 的 y 滑入都写 inline transform，与样式表里的 `transform` 互斥，只能活一个）。要居中/位移，用绝对定位的 auto 外边距，别用 `translateX(-50%)`。`translateX(-50%)` 曾让竖屏配图的 y 滑入整条失效。实测过程见 `git log`。
- 底部 progress bar 与段落时间轴同步。
- verse 当前句用该段 accent 色高亮，附荧光笔式渐变下划线。字重不切换，避免横向跳动。cream 浅底下，高亮字与 agenda 序号自动改用同色相压暗一档的文本色（`--seg-accent-text`）。装饰氛围光/进度条仍用原色。
  切换在成片里是**瞬时**的。逐帧 seek 的渲染要求每一帧都等于时间线时刻。字幕的淡入/滚动补间因此不在 CSS 里，只由 `preview.js` 在人工预览分支注入。给 `.verse*` 加 `transition` 会把墙上时钟漏进成片。实测 seek 后计算样式停在过渡起点，句子流不跟着滚动。别加。
- 每段的 accent 会派生一组装饰，一律经 CSS `color-mix`，不引入新的色值令牌。装饰有四类。第一类是氛围光。槽位的氛围光在画面中央一小团。整页画布把它换成近全屏的宽带柔光，因为画布页的景深全靠它。第二类是配图槽位的外发光与 1px 内描边。第三类是 tagline 左侧刻度条。第四类是进度条辉光。选 accent 时注意它会染整帧氛围。槽位外发光的半径走 `--ctv-img-glow`（模板 `image.glow`）。颜色不写进 inline style。挂 `bare-media` 的 SVG 配图槽位不吃外发光与描边，理由见 `references/image_options.md` 的「不铺满幅底」一节。

## 版式真源

版式数值从 `scripts/_template.py` 派生 CSS 变量。这些数值包括画布尺寸、标题区、句子流。圆角与动画也在其中。配色由 `scripts/_theme.py` 派生。

新增视觉参数一律先加进这两个 py，再由 HTML renderer 注入 `--ctv-*` 变量。哪些常量允许留在 `templates/composition.css`，写在 CSS 自己的文件头与各块注释里。`@font-face`、`verse.clipPad` 这两处也有与模板值联动的代价，同样写在那里。

模板改动后直接重新生成 HTML，在浏览器打开 `hf-project/index.html` 逐段看一遍预览（版式问题在这一步发现最便宜）。

## 配图

- 内容段画布的尺寸与配图规格见 `references/image_options.md`。第 4 步的配图规则以它为准。素材一律 `cover` 铺满画布。
- 视频素材按 `loop muted autoplay playsinline` 播放。播放进度与段落时间轴**不做逐帧同步**。段落显示多久由时间轴决定，视频只是循环填充。比段落长的视频渲染只显示前段，生成时有 warn 提示。代价是同一秒的画面不保证可复现。`<video>` 走页面墙钟，多 worker 各自从 0 起播，抓到的帧可能不同。画面必须对上某句话时，改用剪好的静态poster序列，或者换段落。别指望视频帧同步。是否观感正确仍以预览和成片为准。
- 配图语义是否正确只能靠人眼判断（预览 + 成片），工具不会替代内容判断。
- **方式 C SVG 的「导演」（时间轴同步动画）**：images.json 给该段写 `director` 时启用。这张 SVG 先经 `scripts/_svg_sanitize.py` 净化，再**内联成活 DOM**。`director.steps` 按句起点展开成 GSAP 补间（见 image_options.md 方式 C「SVG 动画：两档」）。这与视频那条相反。导演是**逐帧可复现的属性补间**，挂在同一条时间线上。补间位置 = `seg.sentences[at].start_time`。seek 到同一时刻必然同一帧。正因为如此，内联时管线会剥掉 SMIL / CSS 动画这类墙钟运动。内联等于同源活节点，这是安全代价。这个代价由净化兜住：`<script>`/`on*`/`<foreignObject>`/外链一律删除。要时间轴同步的状态变化就用它。纯氛围循环仍走 `<img>`+墙钟。

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

`vendor/` 由 `gen_hyperframes.py` 在这一步自动装好。取用链、哈希钉固与取不到时的处置口径，见 SKILL.md「环境」的 GSAP 一段。

管线在生成 HTML 前先检查音频路径是否存在。manifest 中的绝对路径会复制到项目的 `audio/` 目录，项目外音频也一样。显式 `--audio` 遵循同一规则。

manifest 的音频路径失效时，管线回落到项目里上次暂存的 `audio/combined.wav`。这时会打 `[warn]`。管线实测该文件时长，与 manifest 时间轴对账。偏差超过 `max(1s, 2%)` 直接拒跑，拒绝用新字幕烧旧音轨。两处都没有才报错。管线不生成无声预览或成片。

### 2. 浏览器预览

预览行为与检查清单见 SKILL.md 第 5 步。这里只说边界。`preview.js` 只在真实浏览器里注入 UI。headless（渲染）走另一条判定，不会把控制条带进出片。headless 调试可在 URL 加 `?preview` 强制注入预览 UI。

### 3. 正式渲染

渲染直接跑 `run.py`，不带 `--until`，命令见 SKILL.md 第 5 步。`run.py` 会重新生成一次 HTML，再进入 render。渲染阶段内置文件稳定性等待器。Hyperframes/Node/Chrome 即使在 MP4 写完后没有及时退出，等待器也会等文件稳定。必要时等待器清理本次 render 的进程树。

### 4. 单段快渲（`--only`）

改完一页想立刻看成片效果时，不必重跑整条管线。`run.py --only seg3 ...` 复用上次 TTS 的 `timing_manifest.json` 与 `combined.wav`，**不重跑配音**。它把这一页切成子 manifest，再渲成项目目录里的 `preview_seg3.mp4`。

- **产出的都是 `preview_*` 前缀**：`preview_seg3.html` / `.wav` / `.manifest.json` / `.images.json` / `.report.json` / 成片。`index.html`、`out.mp4`、`production_report.json` 一个都不碰。实测跑完再核对，正式产物仍是上一次完整管线的结果。预览和定稿因此可以并排放。
- **窗口从下一句开口往回切**。这一页在片中的可见窗口一直铺到下一段开始说话。它不铺到本段最后一个字结束。切出来的音频边界与整片一致，成片边界也一致。这样才看得出下一句压上来的实际观感。
- **`stage: "keep"` 的接续链从链首渲**。`--only seg3` 遇到 seg3 接续 seg2 时，管线会自动多带一页。管线把 seg2 一起装进子 manifest。切片也从 seg2 的音频起点开始。否则预览片里只有半截画面，接续效果根本看不见。日志会打一条接续说明，写明带上了哪几页。
- **该拦的照拦**。没有 `timing_manifest.json`/配音 → exit 2。这一页缺配图 → exit 2，连 `--until html` 也拦。预览只服务定稿的页。**TTS 参数直接拒绝而不是静默忽略**（`--speed`/`--voice-id`/`--bgm` 等）。这一档不重跑配音。静默吃掉参数，等于让你以为预览片反映了你刚改的语速。`--dry-run` 与 `--until tts|images` 同样拒。
- 实测：21.7s 双页链、`--fps 12 --quality draft --workers 4` → 32s 出片（其中渲染 32.0s，含上面说的约 8.5s 固定开销）。比整片重渲便宜得多，但比 `--until html` + 浏览器预览贵。**先看版式再渲这一档**，别拿它当代替预览。

### 5. 透明底导出（`--alpha`，只认 mov）

`run.py --alpha --format mov` 出带 alpha 通道的 ProRes 4444 成片，用来叠在别的素材上（剪映/AE/直播贴片）。`--alpha` 只做画面侧的一件事。它给 `<html>` 挂 `ctv-alpha` 类。模板那三层底因此不画。

三层底是主题渐变 `.bg`、页缘网格 `.grid`、本段 accent 氛围光 `::after`。成片只剩内容层。内容层是标题、句子流、配图/导演层、进度条。选择器写法与两处决胜代价写在 `templates/composition.css` 的 `ctv-alpha` 段。

**容器只有 `mov` 认这件事**，`run.py` 在跑任何一步之前就把 `--alpha` 配 `mp4`/`webm` 的组合 exit 2：

- `mp4`：容器压根没有 alpha 通道，透明处渲成黑底。
- `webm`：**实测丢平面**。hyperframes 0.8.114 / Windows 会把透明页渲成不带 alpha 的 webm。渲染日志照样打 `"needsAlpha":true`。容器元数据照样写 `alpha_mode=1`。但成片逐帧 `pix_fmt=yuv420p`，透明处压成纯黑。**别信那两条声明，只信逐帧像素格式**。哪天渲染器修好了，判据就是下面这两条命令。实测通过再放开 `run.py` 里那条校验。

判据（本项目自己的产物实测，1080×1440 / 21.7s / 261 帧）：

```bash
# ① 逐帧像素格式：透明底必须有 a 平面。同一份 HTML：
#    mov  → 261 帧全是 yuva444p12le
#    webm → 261 帧全是 yuv420p（这一条就是丢平面的直接证据）
ffprobe -v error -select_streams v:0 -show_entries frame=pix_fmt -of csv=p=0 out.mov | sort | uniq -c

# ② alpha 值真的分布两端：抽一帧转 rgba 数一下。实测第 200 帧
#    alpha=0 占 83.9%（三层底）、alpha=255 占 3.7%（内容），中间是字形抗锯齿
ffmpeg -v error -i out.mov -vf "select=eq(n\,200)" -frames:v 1 -pix_fmt rgba f.png
```

`-show_entries stream=pix_fmt` 那种流级读法不能当证据。它报的是声明，与逐帧实测可以不一致。webm 那侧就是声明带 alpha、帧里没平面。最直观的一验是叠到纯色底上看：`ffmpeg -f lavfi -i color=magenta:s=1080x1440 -i f.png -filter_complex overlay out.png`。透明底正确时只有内容层挡住洋红，整片背景全是洋红。

代价是体积。同一条 21.7s 竖屏，mp4/webm 各 1.2MB，mov **185.7MB**（约 8.5MB/s，`--fps 24` 还要翻倍）。所以透明底是**交付格式**，不是迭代格式。迭代照旧 `--fps 12 --quality draft` 渲 mp4 看。定稿要叠轨了再单独出一版 mov。

`--only` 也吃这两面旗（`--only seg3 --alpha --format mov` 出 `preview_seg3.mov`）。单页试叠就靠它。

## 性能参数

- `fps`：默认 24。抓帧耗时与帧数严格线性，是唯一的一阶杠杆。快速看画面用 `--fps 12 --quality draft`。别拿定稿规格反复试。
- `workers`：默认 4，`run.py` 字面透传给 `hyperframes render --workers`，没有自动校准。8 之前有效，8 之后饱和。迭代片可显式 `--workers 8`。含视频素材或遇 V8 堆崩溃时降到 2。每个 worker 是一个独立 Chrome（约 256MB 常驻）。渲染明显偏慢时，先在 `out.render.log` 找 `Parallel capture timed out`。它会重抓全部帧并自动降 worker。别默认是稿件变长了。
- `quality`：`draft` / `standard` / `high`。draft 约省 15%，预览够用。
- **固定开销**：每调用一次渲染约白付 8.5s（与帧数无关），冷跑第一次还多约 6s。所以改一处就重渲一次很不划算：先 `--until html` + 快照看够，再整片渲一次。

## 官方校验命令（优先于手搓探针）

`npx -y hyperframes` 自带三个命令，覆盖本技能原先手工搭的 headless 探针：

```bash
npx -y hyperframes check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

- `check` 会真报问题。字体栈里出现未声明 `@font-face` 的家族名时判 **error**，规则名是 `font_family_without_font_face`。判定基于名字，不看解析结果。`composition.css` 因此为 CJK 兜底名 `Noto Sans CJK SC/JP` 也补了 `local()` 声明。同一处还有条静默代价。家族名必须是它字体映射表认识的写法。CSS 惯例名 `SFMono-Regular` 不在表内，表里认的是 `"SF Mono"`。命中不了只打一条 `[WARN] No deterministic font mapping`。那一族就拿不到注入的 `@font-face`。`_template.py` 的 `monoStack` 已按映射表写。改字体栈要对着这条 WARN 改。
- 一份干净产物的 `check` 基线（竖横各跑一次实测）：Lint 只有下面四条固定噪声，Runtime / Layout / Motion / Contrast 全 0 error。这四条已知可接受。`gsap_callback_dom_measurement` 对应 verse 滚动测量，那是懒缓存，也满足 seek 幂等，见 `templates/runtime.js` 注释。`nested_structure_needs_subcomposition` 每段一条。`timeline_track_too_dense` 是上一条的另一种说法。同一合成根下 7 段就是 7 个 timed element。`negative_z_index` 两画幅各有一条 `.seg-card::after`。不拆 sub-composition 是刻意的：单文件便于 `--until html` 后人工审阅，且分段渲染实测更慢（见上文「性能参数」）。`negative_z_index` 是误报。浏览器实测 `getComputedStyle(.seg-card).isolation` 为 `isolate`。氛围光确实压在卡片内容之下，也压在页面背景之上。检查器不认 `isolation` 建的层叠上下文。它自己给的 Fix 就是加 `isolation: isolate`。`composition.css` 已加。基线之外的新增条目一律当真读。
- Runtime 的 `clip_media_fit` 和 Layout 的 `clipped_text` **不在噪声之列**。前者是音频实际时长短于 `data-duration`。这时渲染把成片截到音频长度，字幕时间轴对不上。真实 pipeline 产物的 `total_duration` 就是量出来的音频时长，正常不该出现这条。手写或裁剪 manifest 时，它是 manifest 与音频不同步的唯一信号。实测把 `total_duration` 对齐音频后，该条消失。后者是自己的盒子把某行文本裁掉了。竖屏 agenda 行的 `nameTrim` 只能挡字数超限，挡不住半角/混排的实际字宽。Python 侧 warn 与它构成两道闸，各管一头，见 `references/writing.md`「开场/结尾专用顶层字段」。
- `snapshot --at` 精确取时刻帧，也会自动拼 contact sheet。它比手搓探针省事，也不会踩 vendor 相对路径的坑。手搓的做法是复制 HTML、注入 `tl.pause(t)`、再用 Chrome 跑 `--screenshot`。手搓探针只在两种情况作后备：需要同一页连取多帧，或 `snapshot` 不可用。
- 渲染前判断本机依赖齐不齐，用 `npx -y hyperframes doctor`。全绿就可以直接跑真渲染，不必止步于 HTML 预览。Chrome headless shell 由 Hyperframes 自己下载，缓存在 `~/.cache/hyperframes/chrome/`。FFmpeg 走第 3 步那条查找链。
