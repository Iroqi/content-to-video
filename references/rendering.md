# 第 4 步 · 渲染：预览、渲染与官方门禁

- **要跑/调渲染**：「HTML 预览与渲染」「性能参数」。
- **画面出了问题**：「画面翻车速查表」按症状查；版式真源在「画面结构」与「动画」。
- **要跑官方校验**：「官方校验命令」——`check` 的已知噪声与必须当真的条目都在那里。

## 主题

整期一个主题，只在 `run.py` / `gen_hyperframes.py` 选，`dark` 默认（适合技术突破、发布、工程、前沿 AI、安全事件）。

## 画面结构

内容段（两画幅同构）：

```text
标题区（1 行优先，最多 2 行）
↓
固定画布（竖屏 4:3 居中；横屏左文右图双栏）
↓
verse 句子流（竖屏钉底；横屏排在左文字栏末、随栏垂直居中）
```

标题折行只改标题区内部排版：竖屏画布与 verse 不动；横屏 verse 随左栏重新居中。

- **portrait**：标题区定高（模板派生）、两行钳制，超出裁切；4:3 画布（980×735）居中，verse 钉底。标题区压到画布顶边时报 `[template] 竖屏标题区放不进`。
- **landscape**：左文字栏固定 613px 宽，右侧槽位 1067×800。**没有行数钳制**，守卫只降字号：超 16 字降到 54px、超 22 字降到 48px；超 16 字打 `[warn]`。
- **整页画布（`layout: "canvas"`，两画幅同一份规则）**：三层塌成一层——配图拉满（1080×1440 / 1920×1080，圆角/外发光/1px 内描边全撤），标题层与句子流层**不生成**；**不要自画满幅底板**（见 `references/canvas.md`「不铺满幅底」）。文字对比度是**画布作者的责任**：`Contrast` 门禁看不见 SVG 里的字，生成期按文件拦——该段必须有配图，SVG 比例与图内 px 字号跟画幅对账（见 `references/canvas.md`「整页画布」「图内文字的对比度」）。进度条与底轨抬到 `z-index:2`。

opening / closing 默认是不配图的**纯文字 agenda 卡**（`layout: "agenda"` 由 pipeline 盖章）。要换成整页海报用顶层 `opening_layout` / `closing_layout: "canvas"`，那一页只剩一张满幅配图 + 进度条。字段、行数上限、`nameTrim`、守卫阈值**以 `references/writing.md` 为准**。agenda 列撑满时牺牲行列表尾部。

画面单位是**段落**，段内基本静止，只让字幕句子逐句切换与高亮。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据：

- 段落入场默认**方向擦除（wipe）**：新卡用 clip-path 从全遮蔽补到全覆盖（时长与缓动取 `animation.segmentWipe`）。`style` 全片统一：`line`（**默认**）/ `vertical` / `diagonal` / `circle`（71%）；写错档生成期报错。旧卡**不淡出**。整页画布除外（硬切）。
- **`line` 引导线转场**：线是新卡末子，其下缘即新卡 clip-path 的揭示边；旧页随后整层剥离并挂 `.peel-shade`。`wipe` 钳到 0 时段线与剥离不生成。
- 时长被句间静音钳制：gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。`wipe = min(基准时长, gap)`，入场提前铺到 `win_start = s − wipe`。
- **揭屏的前提是卡元素的 `data-duration` 覆盖整个入场窗口**。窗口与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`）。透明卡做 wipe 会双影叠字。
- **整页画布不参与模板转场**：`wipe` 强制归零走"瞬间切"（引导线、剥离、暗边都以 `wipe<=0` 为闸不生成），它在自己的**音频起点**整页出现。按**版式**判定，四档 `style` 一律生效。切过去那瞬间画布是空的——让导演第一拍尽早落子（`at_time` 靠前锚点），见 `references/canvas.md`「SVG 动画：两档」。`stage: "keep"` 的跨段接续页同样 `wipe=0`、不生成配图入场补间（见 `references/canvas.md`「跨段接续」）。
- title / image 按模板参数入场；tagline 无独立补间，随段落卡整体显隐。**CSS 不得给 GSAP 补间的元素声明 `transform`**；要居中/位移用绝对定位的 auto 外边距。
- verse 当前句用该段 accent 色高亮（荧光笔式渐变下划线），字重不切换。对话段（`dialogue`）在**轮次切换的首句**行首挂说话人标签 `.verse-who`（同一人连说多句只挂第一句）；`speakers[].label` 有 6 字上限。字幕切换在成片里是**瞬时**的：淡入/滚动补间只由 `preview.js` 在人工预览分支注入，**别给 `.verse*` 加 `transition`**。
- 每段 accent 派生一组装饰（一律经 `color-mix`）：氛围光、槽位外发光与 1px 内描边（半径 `--ctv-img-glow`）、tagline 刻度条、进度条辉光——选 accent 时注意它染整帧氛围。`bare-media` 的 SVG 槽位不吃外发光与描边（见 `references/canvas.md`「不铺满幅底」）。

### 苹果风开场（开屏 agenda 卡默认强绑定）

开屏页（sid=`opening`）且为 agenda 版式时**默认强绑定**苹果风整页编排，无契约键、无需配置（`opening_animation` 键已随默认强绑定移除，写过旧键的稿件契约层会报"请删掉这个键"）。参数真源在 `_template.py` 的 `animation.opening.apple`。

- 整支舞按**可见窗口**（`[擦除起点, 被下一页盖住]`，不是口播段长）等比归一：收尾时刻由**标题字数与行数**共同决定（`标题字符波次尾 = stagger×(非空格字数−1) + duration`，`行内容滑入尾 = rows.content.delay + stagger×(行数−1) + duration`）；装得下走原速，装不下按 `可见窗口 ÷ 收尾时刻` 压缩，地板 0.5。
- 光晕主光呼吸与副光漂移的时长不参与压缩，压缩只落在淡入时长与起点。
- 装不下时打 `[warn]`（可见窗口、需要的时长、行数与字数），症状见「画面翻车速查表」。
- 锚点是**擦除起点 `win_start`** 而不是段起点。

## 版式真源

版式数值（画布尺寸、标题区、句子流、圆角、动画）从 `scripts/_template.py` 派生 CSS 变量，配色由 `scripts/_theme.py` 派生。新增视觉参数先加进这两个 py，再由 renderer 注入 `--ctv-*`；可留在 `templates/composition.css` 的常量写在 CSS 文件头与各块注释里。

模板改动后重新生成 HTML，在浏览器打开 `hf-project/index.html` 逐段看预览。

## 配图

- 画布尺寸与配图规格见 `references/imagery.md`（第 3 步规则以它为准）；素材一律 `cover` 铺满画布。
- 配图语义是否正确只能靠人眼判断（预览 + 成片）。
- **SVG 的「导演」**：images.json 写 `director` 时，SVG 经 `scripts/_svg_sanitize.py` 净化后**内联成活 DOM**，`director.steps` 按句起点展开成 GSAP 补间（位置 = `seg.sentences[at].start_time`，见 `canvas.md`「SVG 动画：两档」）；内联剥掉 SMIL / CSS 动画，净化删掉 `<script>`/`on*`/`<foreignObject>`/外链。要时间轴同步的状态变化用它，纯氛围循环走 `<img>`+墙钟。

## HTML 预览与渲染

### 1. 生成 HTML

```bash
python scripts/gen_hyperframes.py   -m audio_output/timing_manifest.json   -o hf-project/index.html   --images hf-project/images.json   --fps 24
```

`-o` 与 `--images` 的相对路径都相对 `-o` 所在目录。画幅与主题在这一步烧进 HTML（默认 portrait + dark，见 SKILL.md「常用参数」）。

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

`vendor/` 由 `gen_hyperframes.py` 自动装好（口径见 SKILL.md「环境」的 GSAP 一段）。音频路径在生成前做存在性检查：绝对路径或项目外音频复制到 `audio/`；失效时回落到上次暂存的 `audio/combined.wav`，打 `[warn]` 并对账时长，偏差超过 `max(1s, 2%)` 拒跑。

### 2. 浏览器预览

见 SKILL.md 第 4 步。边界：`preview.js` 只在真实浏览器注入 UI；headless 调试可加 `?preview` 强制注入。

### 3. 正式渲染

渲染直接跑 `run.py`（不带 `--until`，命令见 SKILL.md 第 4 步）：它会重新生成一次 HTML 再 render。

**渲染之后还有一道闸：成片时长对账**：落盘后拿 ffmpeg 量实际时长与 manifest 的 `total_duration` 对账，容差 `max(1s, 2%)`：短于容差**退出码 4** 拦下交付，长于容差或量不到只 `[warn]`。结果与实测秒数进 `production_report.json` 的 `video` 字段（`ok` / `short` / `long` / `skipped`）；**别把这条 warn 当噪音划走**。撞上 exit 4 照终端报错做。

## 性能参数

默认值与档位见 `run.py --help`（`--fps 24`、`--workers 4`、`--quality looks`），这里只写"什么时候该动它"：

- `fps`：抓帧耗时与帧数线性，唯一一阶杠杆。快速看画面用 `--fps 12 --quality draft`。
- `workers`：字面透传给 `hyperframes render`，无自动校准。8 之前有效、之后饱和；迭代片 `--workers 8`；V8 堆崩溃降到 2。偏慢时先在 `out.render.log` 找 `Parallel capture timed out`。
- `quality`：五档透传（`draft` / `looks` / `standard` / `high` / `delivery`）。`looks` 默认（standard + CRF 16）；`draft` 省约 15%、预览够用；`delivery` 是交付档；`standard`/`high` 是裸档。
- **固定开销**：每调用一次约白付 8.5s，冷跑第一次多约 6s。先 `--until html` + 快照看够，再整片渲一次。
- **低配机器（`--low-memory-mode`）**：CLI 把 worker 钉成 1（按总内存 ≤8GB 自判）：

  ```bash
  python scripts/run.py --source SOURCE -o OUT --quality draft --workers 1 --low-memory-mode
  ```

  `--workers` 与它冲突时 **CLI 赢**，`run.py` 打 `[warn]`；`--no-low-memory-mode` 关自判。**容器里自判读宿主机总内存、不是 cgroup 限额**——小限额容器跑在大内存宿主上不会自动进低内存档，要显式 `PRODUCER_LOW_MEMORY_MODE=1`（低内存档 = 1 worker + 截图抓帧 + 关自动校准）。Chrome 启动/协议超时另有 `--browser-timeout` / `--protocol-timeout`（`run.py` 不透传）。

## 官方校验命令（优先于手搓探针）

```bash
npx -y hyperframes check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

- `check` 会真报问题：字体栈出现未声明 `@font-face` 的家族名判 **error**（`font_family_without_font_face`）；家族名必须是映射表认识的写法（认 `"SF Mono"` 不认 `SFMono-Regular`），否则只打 `[WARN] No deterministic font mapping`。
- 干净产物的 `check` 基线：**Lint 12 条 warning 全是下面 6 类噪声，Runtime / Motion / Contrast 全 0，Layout 0 error**。看**类型**不看条数：
  - 噪声：`gsap_callback_dom_measurement`（1 条）、`nested_structure_needs_subcomposition`（**每段一条**）、`timeline_track_too_dense`（1 条）、`composition_file_too_large`（1 条）。
  - 误报：`negative_z_index`（**每条 `z-index:-1/-2` 规则一条**），Fix 是 `isolation: isolate`（已加）。
  - `gsap_infinite_repeat`（**开屏页才有**，1 条，默认强绑定）：噪声。别处的 `repeat:-1` 与无界 duration 都要当真。
  - **判据**：6 类之外的新增条目一律当真，**类型多出一类就要查**。Layout 的 `✗` 要当真，例外只有 `text_occluded`。
- `clip_media_fit`（Runtime）与 `clipped_text`（Layout）**不在噪声之列**：前者是音频短于 `data-duration`，手写/裁剪 manifest 时是"manifest 与音频不同步"的唯一信号；后者是文本被自己的盒子裁掉（`nameTrim` 只挡字数）。
- Layout 的 `text_occluded`：句子流滚出 `.verse-clip` 的行被裁掉但静态 rect 仍在原位，逐行量就判"文字藏在不透明元素下"。**做法**：给句子流里每个带文字的元素打 `data-layout-allow-occlusion` 等三个豁免属性——检查器只认元素自己身上的标记、**不继承祖先的**（模板已打在 `.verse` / `.verse-line` / `.verse-who`）。**再见到它**：先 `snapshot --at <时刻>` 看画面；真被遮就调版式；画面正常就是某层漏打了属性，按 `check` 报的选择器补那一层。
- `snapshot --at` 取时刻帧并自动拼 contact sheet；手搓探针只在需同页多帧或 `snapshot` 不可用时作后备。
- 渲染前用 `npx -y hyperframes doctor` 判依赖：全绿直接跑真渲染（Chrome 缓存在 `~/.cache/hyperframes/chrome/`）。
- **三条命令之外的口径**（CLI 写明、踩上不报错）：`validate` / `inspect` / `layout` 是**废弃别名**，用 `check`；`check` 会先跑 `lint`（迭代期单跑 lint、定稿再跑 check）；`doctor --json` **恒退出 0**——当门禁必须读 `.ok`（`hyperframes doctor --json | jq -e '.ok'`）；`check` 的退出码只由**持久** findings 决定。
- 要**跨机逐帧一致**用 `render --docker`（钉住 Chromium、字体集与编码器）；本机迭代不必付。核对时间轴用 `hyperframes timeline --json`。`npx hyperframes preview --background` 起预览服务：与渲染同一条 runtime。

## 画面翻车速查表

按**看到的症状**查，不按知识点查——出问题时你手里只有一个现象。每条给的是成因与去处，去处写成 `文件`「章节」+ 具体那一条，可直接跳过去。

| 看到的症状 | 成因 | 去处 |
| --- | --- | --- |
| 推镜时页底标签不见了、页顶标题贴着边 | `scale>1` 以 `svgOrigin` 为中心放大，离焦点越远位移越大。**留白要按"推完那一帧"算，不是按 SVG 源码算** | `canvas.md`「SVG 动画：两档」里「运镜 / 相机」条的「为什么」 |
| 推近的图形压到了旁边静止的标题或旁白上 | 出画 warn 只判"出画"，不判"撞字"；两者都没出画时无人拦 | `canvas.md`「SVG 动画：两档」里「运镜 / 相机」条的「机器兜不住的那件事」 |
| `keep` 接续页界上图形**位置**跳一下（尺寸不变） | 没写原点时 GSAP 绕 `#cam` bbox 左上角，那个角还跟着别的步的 `from` 瞬态飘 | `canvas.md`「跨段接续」里「相机绕的那一点是渲染器钉的」 |
| `keep` 接续页界上整幅换色，但图形其实接上了 | 每页 `--seg-accent` 不同，模板氛围光与进度条在切页那帧整幅换色。**要量连续性就按柱子边沿定位，别拿整幅 PSNR 当判据** | `canvas.md`「跨段接续」的实测段 |
| 图内标签在成片里看不见，全流程零报错 | `<img>` 载入的 SVG 里文字不属于 HTML 文本流，没有一道检查量它 | `canvas.md`「图内文字的对比度」里「没有任何一道检查会量它」 |
| 图内文字右缘出血（量出来的右缘超过上限） | `text-anchor="end"` 的落点要按量出来的字宽倒推，不是按字符数估 | `canvas.md`「整页画布」里「安全边距照页面走」 |
| 图里两行文字压在一起（`hyperframes check` 报 `content_overlap`） | 两条 y 只差十来个像素——`canvas_kit` 的柱标签固定落在 `base_y+32`，自己加的说明文字很容易撞上它。`check_svg` 查不到图内压字，只有 `check` 的 Layout 阶段能量 | `canvas.md`「画布几何」的末条 |
| `check` 报 Layout `text_occluded`，但快照里文字好好地在那儿 | 句子流滚出窗口的行 rect 仍在原位，属误报——豁免标记已由模板打在每一行上，见下 | `rendering.md`「官方校验命令」 |
| 压缩成片后轴线/网格/分隔线像是没画 | 色档太暗 + 线宽不足 3px 在压缩后被抹平 | `canvas.md`「整页画布的密度与层次」里「辅助线要敢用亮档」 |
| 暂停一帧像一页空白（中段整条空带） | 标题/主视觉/注脚没分成三段并用中景连起来 | `canvas.md`「整页画布的密度与层次」里「竖向不许有整条空带」 |
| 动画与旁白差 0.1s 量级 | 句内小数是线性猜的（**整数句序是准的**） | `canvas.md`「SVG 动画：两档」里 `at` 条的「整数句序是准的，小数是线性猜的」 |
| 浏览器里颜色分明、成片里发闷 | 图内最大档与最小档字号比值不足 2，观众分不出主次 | `canvas.md`「整页画布」里「图内可以有标题，字号按整页读」 |
| 整页画布在浏览器里是均匀一块死色 | 铺了满幅底板，把模板背景三层盖掉了 | `canvas.md`「不铺满幅底」里那条整页画布的说明 |
| 只有开头一秒是空的 | 整页画布没有转场遮，逐拍揭示意味着前 1 秒画面是空的 | `canvas.md`「整页画布的密度与层次」里谈导演页起始帧那条 |
| 成片很长，`--check-frames` 说"通过"却总觉得没看全 | 抽帧上限 40 帧：装不下 `every` 就**按整片均分**（不是只抽开头），报告里那条 `[coverage]` 写明实际每几秒一帧、覆盖到哪一秒。要逐段细看就分段跑并给 `--timestamps` | `scripts/check_frames.py` 的 `sample_timestamps` |
| 开了 `--low-memory-mode`，`--workers 4` 却像没生效 | 低内存档由 CLI 把 worker 钉成 1，它赢。`run.py` 会打一条 `[warn]` | `rendering.md`「性能参数」 |
| 成片里某一段画面是糊的、虚的 | 素材像素不够铺满它那一页的框：`cover` 只保证填满、不保证清楚，放大 2 倍以上细节就没了，而全流程零报错 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」的第二条规格 |
| 配图明明够清晰，生成期却报"不够大" | 按它实际落地的框判：整页画布按整幅（1080×1440 / 1920×1080），槽位按 4:3 那一块。同一张图在槽位够用、换成整页画布就不够 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」 |
| 开场动画最后几行像没出现 | 苹果风开场整支舞按可见窗口等比压缩；压到地板仍装不下时收尾会被下一页切走（生成期有 `[warn]`） | `rendering.md`「苹果风开场」 |
| 开场动画整体快了一拍，像被赶过 | 同上：窗口装不下就会压。写长 `opening`（说完主题 + 为什么值得看）就能回到原速 | `rendering.md`「苹果风开场」 |
