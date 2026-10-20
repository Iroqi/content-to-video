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
- **整页画布（`layout: "canvas"`，两画幅同一份规则）**：三层塌成一层——配图拉满整幅（圆角/外发光/1px 内描边全撤），标题层与句子流层**不生成**；**不要自画满幅底板**（见 `references/canvas.md`「不铺满幅底」）。文字对比度是**画布作者的责任**：`Contrast` 门禁看不见 SVG 里的字，生成期按文件拦——该段必须有配图，SVG 比例与图内 px 字号跟画幅对账（见 `references/canvas.md`「整页画布」「图内文字的对比度」）。进度条与底轨抬到 `z-index:2`。

opening / closing 默认是不配图的**纯文字 agenda 卡**（`layout: "agenda"` 由 pipeline 盖章）。要换成整页海报用顶层 `opening_layout` / `closing_layout: "canvas"`，那一页只剩一张满幅配图 + 进度条。字段、行数上限、`nameTrim`、守卫阈值**以 `references/writing.md` 为准**。

## 动画

动画参数全部来自 `_template.py` 的 `animation` 数据：

- 段落入场默认**方向擦除（wipe）**：新卡用 clip-path 从全遮蔽补到全覆盖（时长与缓动取 `animation.segmentWipe`）。`style` 全片统一：`line`（**默认**）/ `vertical` / `diagonal` / `circle`（71%）；写错档生成期报错。旧卡**不淡出**。整页画布除外（硬切）。
- **`line` 引导线转场**：线是新卡末子，其下缘即新卡 clip-path 的揭示边；旧页随后整层剥离并挂 `.peel-shade`。`wipe` 钳到 0 时段线与剥离不生成。
- 时长被句间静音钳制：gap = 下一段 start − 本段 end（`--gap`，默认 0.4s）。`wipe = min(基准时长, gap)`，入场提前铺到 `win_start = s − wipe`。
- **揭屏的前提是卡元素的 `data-duration` 覆盖整个入场窗口**。窗口与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐。再往外一层是 hyperframes 自己的可见窗口 `[data-start, data-start + data-duration)`——**半开区间**，卡在 `start + duration` 那一刻就被隐藏。所以补间的**结束态要落在窗口内、别正好压在末端**：压线那一帧永不渲染（不报错，只是动画像少了一拍）。窗口末端按 `段时长 + gap + wipe` 算，不是按可见窗口算。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`）。透明卡做 wipe 会双影叠字。
- **整页画布不参与模板转场**：`wipe` 强制归零走"瞬间切"（引导线、剥离、暗边都以 `wipe<=0` 为闸不生成），它在自己的**音频起点**整页出现。按**版式**判定，四档 `style` 一律生效。切过去那瞬间画布是空的——让导演第一拍尽早落子（`at_time` 靠前锚点），见 `references/canvas.md`「SVG 动画：两档」；开头整片空白会被生成期按段落打 `[warn]`「起始帧全空」（见 `references/canvas.md`「整页画布的密度与层次」的起始帧一条）。`stage: "keep"` 的跨段接续页同样 `wipe=0`、不生成配图入场补间（见 `references/canvas.md`「跨段接续」）。
- title / image 按模板参数入场；标题入场**按段序轮换节奏**（`_template.titleEntrance.rhythm` 四档：`back.out` 弹一下 / `expo.out` 滑上来 / `power3.out` 淡入 / `power2.out` 大落差落定，按**卡片序**取模而不是按会漂的配图序）。第 0 段仍是 `scale:0.5` 那一档，老产物首段行为不变。tagline 跟着标题那一档进来（幅度小一号、起点错峰）。**题头组的第一行**`.agenda-kicker`（开屏/结尾那行小标题）只有开屏由苹果编排逐字演；**结尾页**它跟标题同拍、从字距展开态整行收拢（`animation.titleEntrance.kicker`），两页的 kicker 都带一枚行尾字距垫，保证展开期间行宽不变、最后一个字不被裁。**CSS 不得给 GSAP 补间的元素声明 `transform`**；要居中/位移用绝对定位的 auto 外边距。image 入场是**两层**（框先落位、图本体收拢，见「配图」的 settle 一条），之后还有一层贯穿整段的缓慢运镜（Ken Burns）——**一段落在画面上的那 8~20 秒里一直在动的只有它和句子流**。
- verse 当前句用该段 accent 色高亮（荧光笔式渐变下划线），字重不切换。对话段（`dialogue`）在**轮次切换的首句**行首挂说话人标签 `.verse-who`（同一人连说多句只挂第一句）。字幕切换在成片里是**瞬时**的：淡入/滚动补间只由 `preview.js` 在人工预览分支注入，**别给 `.verse*` 加 `transition`**。
- **词级强调（karaoke）**：当前句按句内进度从左往右点亮，未念到的半句压暗——像人跟着念。它只由「时刻 + 该句的开口时刻 `data-t0` + 发声长度 `data-voiced` + 实测字宽」决定，不含任何"亮到第几个"的状态，所以逐帧乱序 seek 也幂等。TTS 没有词级时间戳，句内按匀速推进是唯一可用的假设（与导演档 `at` 的小数偏移同一条口径）；它只决定"哪几个字先亮"，字幕**换句**时刻仍由 cue 硬切。
  **进度分母是发声长度，不是音频块长度**：manifest 的 `voiced`（`references/tts_pipeline.md`「发声区间」）扣掉了句首吸气与句尾拖音，念完那一刻进度正好落到 100%，不会"最后一个词先亮完、再干等几百毫秒"。没有该字段的老 manifest 退回 `data-t1`，行为与从前逐字节一致。同理 cue 起点后移到 `start_time + onset`：字幕在**开口那一刻**才换，而不是音频块一开始就换。做法上正文保持**一个裸文本节点**、行尾放一个空的 `.verse-em` 挂点与一层 `.verse-ved` 覆盖物。覆盖物在生成期就写进**整句原文**（`data-text`），与正文共用同一套行盒与字体声明，因此逐字重合；运行时只按"正文第 k 个字已念"把覆盖层对应字的 alpha 归 0，未念字保持**实色**（文本色按 0.62 混到底色上）盖住 accent。两条硬约束：正文不能被 span 包住（拆成含文字的叶子会被 Layout 门禁逐字报 `text_occluded`），覆盖层不能只写 `slice(k)`（前 k 字被拿掉后折行点前移，跨行时每个字按缺失前缀逐渐错开＝重影），也不能用半透明去压暗（活动行正文整句是 accent，半透明浅色盖不住，实测成片里整句仍全亮）。

  **念完之后的收束（settle）**：一句念完那一刻，整句仍是全亮 accent，而它与下一句刚亮起的第一个字**同色同重**——画面上同时有两个"现在的焦点"。所以句尾做一次回落：把整句逐字交还给"已过去"那一档，焦点让给下一句（Apple Music 逐行歌词、Spotify 同步歌词、卡拉OK 的 bar 收尾都在做这件事）。做法不是整句一起压暗（一整句在 0.3s 里同时变灰＝闪烁），而是**逐字错峰**：第 i 个字延迟 `i × (时长 ÷ 字数)`，笔画像被一支笔从前往后盖过去，收在句尾。
  时长只有一个数：`--ctv-ved-settle`，由渲染器在**生成期**按「本句念完 → 下一次换句」那段留白算出（`_ved_settle_seconds`），扣掉换句接力 0.32s；封顶 0.6s、地板 0.08s（不足两帧的回落只是闪一下），并按 0.05s 网格**向下**取整（窗口不够时宁可少演，也不把最后几个字挤进下一句已开口的那帧）。留白为 0（首尾相接的稿件、无 `voiced` 的老 manifest）→ 变量写 0、CSS 规则整条失效，成片与改动前逐字节一致；页末句后面跟的是转场、整句 accent 会被擦走，同样不演。运行时只读这一个数，不做第二次判断。
- **焦点衰减（focus falloff）**：句子流的亮度按「离当前句多远」衰减，不再是三档阶跃——0.62 的未念墙里，渐变本身就是路标（Apple Music 逐行歌词、Spotify 同步歌词都按离焦距离沉底）。未来侧 `d=1→0.62`、`d=2→0.45`、`d≥3→0.327` 封住；过去侧整条提前一档：`d=-1→0.45`、`d=-2→0.327`、`d≤-3→0.237`。曲线真源在模板 `subtitle.focus`（future 0.62 / past 0.45 / ratio 0.726——0.45 ≈ 0.62×0.726，「已念」本来就是「未念」沿同一条曲线多走的一档，两侧在 0.45 / 0.327 处无缝拼接），经 `--ctv-focus-*` 注入，`--set subtitle.focus.*` 可临时改；运行时读一次展开成查表、|d| 封顶第三档。当前行与被盖住的页撤内联、交还 CSS 的 active/base 档；composition.css 里的 0.62 / 0.45 从此只是运行时未接管时的兜底。
- **换句接力（crossfade slide）**：新一句浮起时不是硬跳到位，而是从 `--ctv-relay:0.35` 在 0.32 秒内推到 1；旧一句同时按 `past` 压到 0.45。CSS 侧写成**取小**——`.verse-line{opacity:min(var(--ctv-line-op,.62),var(--ctv-relay,1))}`——两套状态（active/past 与接力）因此可以共存，谁更暗听谁的，运行时不需要知道"现在是不是正在接力"。接力起点是**该行所属 cue 的起点**（不是滚动那一刻），所以往回 seek 时同样正确；行一旦进入 `past` 就不再参与接力（否则旧句会在后半段又亮一次）。接力**不存任何状态**：起点逐帧由当前 cue 传入、行上内联值的归属看行本身——曾经把起点缓存在 verse 缓存对象上，字体就绪清缓存把它一起带走，开场句整句卡在接力起点 0.35（比未念档还暗）。教训：**缓存可以清，状态必须能从 t 重推**。
- **字幕链路的时钟**：截图档逐帧渲染只把 `tl.time()` 拨到帧时刻，`onUpdate` 只在 seek **跨过某个 tween 边界**时才触发。所以 `runtime.js` 自己铺一条零长 tween 的**时钟轨**，在**每个帧网格**上都打点（不是只在 cue 起止打点——那样一句之内只有两帧会更新，成片里表现为整句一直是亮的、书签冻住），`onUpdate` 与 `gsap.ticker` 双路喂同一个 `verseSync`，两条互为保险丝。**别把字幕状态改动只写在"cue 下标变化"分支里**。也别拿渲染开关（`window.__HF_EXPORT_RENDER_SEEK_CONFIG`）来决定时钟轨铺不铺密：它的可见时机取决于渲染器注入顺序，关掉时会静默退化成"只有 cue 边界"，而这个退化在预览里看不出来、只在成片里暴露。

- 每段 accent 派生氛围光、槽位外发光、tagline 刻度条与进度条辉光（一律经 `color-mix`）——**选 accent 时注意它染整帧氛围**。`bare-media` 的 SVG 槽位不吃外发光与描边（见 `references/canvas.md`「不铺满幅底」）。
- **背景网格是中性灰、不承担颜色叙事**（`_theme.grid_color`）：纹理色只管"这一页有结构"，"这一段是什么颜色"是氛围光的活，所以网格不带色相——否则会和每段 accent 抢语义。它的浓度按**成片对比度**定在页底 1.20:1。**改这个值请量成片，不要看浏览器预览**——预览是 sRGB 满精度显示，差个 0.1 在屏幕上"看得出有纹理"，导出就被抹平。
- **句子流暗垫（仅竖屏）**：竖屏字幕钉在页底、正压在氛围光最亮的一带，所以 `.verse::before` 在字之后垫一层从透明渐隐到 90% 页面底色的片子，把字身后的纹理压暗。横屏不垫（`_template.layout.<画幅>.verse.shade` 关掉）：左栏是定宽弹性列，整块宽垫垂中时读成"这里垫了一块悬浮的板"。垫层必须让 `.verse` 自己是层叠上下文（`isolation`），否则它会掉进卡片那个层叠上下文的最底层、钻到 `.bg/.grid` 底下去，一像素都不显。

### 苹果风开场（开屏 agenda 卡默认强绑定）

开屏页（sid=`opening`）且为 agenda 版式时**默认强绑定**苹果风整页编排，无契约键、无需配置（`opening_animation` 键已随默认强绑定移除，写过旧键的稿件契约层会报"请删掉这个键"）。参数真源在 `_template.py` 的 `animation.opening.apple`。

- 整支舞按**可见窗口**（`[擦除起点, 被下一页盖住]`，不是口播段长）等比归一：收尾时刻由**标题字数与行数**共同决定（`标题字符波次尾 = stagger×(非空格字数−1) + duration`，`行内容滑入尾 = rows.content.delay + stagger×(行数−1) + duration`）；装得下走原速，装不下按 `可见窗口 ÷ 收尾时刻` 压缩，地板 0.5。
- 光晕主光呼吸与副光漂移的时长不参与压缩，压缩只落在淡入时长与起点。
- 装不下时打 `[warn]`（可见窗口、需要的时长、行数与字数），症状见「画面翻车速查表」。
- 锚点是**擦除起点 `win_start`** 而不是段起点。

## 配图

- 画布尺寸与配图规格见 `references/imagery.md`（第 3 步规则以它为准）；素材一律 `cover` 铺满画布。模板改动后重新生成 HTML，打开 `hf-project/index.html` 逐段看预览。
- 配图语义是否正确只能靠人眼判断（预览 + 成片）。
- **静态图的入场「收拢落位」（settle）**：素材不是"滑进来"，而是从**略大、略偏**收拢到精确位置——框（`opacity` + `y`，占入场预算的 `frameFrac`）先落位，图本体（`scale` + `x/y`）随后在框里收拢，终点恒为 `{scale:1,x:0,y:0}`，正好是下面那条 Ken Burns 的起点。
  **旋钮在 `_template.py` 的 `animation.imageEntrance.settle`**：`scale` 起点放大倍数、`panX`/`panY` **最多用掉多少盈余**（[-1,1]，**符号即来向**——写 `-0.6` 就是"从左侧来"；比例而非 px，所以换画幅、改槽位都不可能露底）、`startFrac` 起点占入场预算的比例、ease。**`duration` 不开放**：终点必须由入场预算推出，否则就会出现"还没收完、运镜已经开始推"的重叠或"收完了干等"的空档，两者在成片里都是速度突变。
  **来向按段序轮换**（`imageEntrance.rhythm` 四档：纵 / 右 / 纯收焦 / 左，按"第几张静态图"取模，与标题 rhythm 同构）。纵向一律与框同向：反向会读成"框往上走、图往下沉"两层在打架。
  **收拢补间必须排在运镜的钉住补间之前**（理由见下面 Ken Burns 那条 pin）：它的起点 `scale>1` 若最后落地，成片里就是"切页第一秒图比后面大一圈"。
- **静态图的持续运镜（Ken Burns）**：静态图（`<img>` 那一档，含 svg/png/gif）入场落定后整段剩余时间缓慢推近并微移。
  **旋钮在 `_template.py` 的 `animation.imageEntrance.drift`**（`ratePerSec` 每秒推近多少、`maxScale` 终态封顶、`panX`/`panY` **最多用掉多少盈余**[0,1]、`minSeconds` 短段门槛、`breathe` 呼吸档）。删掉整个 `drift` 块即关闭。
  **长段落是"呼吸"而不是匀速**：`breathe:{periodSeconds, depthFrac}` 把整段剩余时间切成 3–6 小段，推进速度"快 / 缓 / 快"地起伏（`depthFrac` 是缓段吃掉多少进度，上限 0.5）。段时长短于两个周期时自动退回单段匀速——8 秒以内的段切三段只会读成卡顿。
  **取景方向不写在模板里**：**相邻两张静态图必漂向相反一侧**，四张一轮回走遍四个象限；幅度按段落 id 派生（与 `canvas_kit` 的手绘抖动共用同一份 `seeded_wobble`）——同一稿件重跑逐字节一致。
  **三条约束**：`object-fit:cover` 已把图铺满槽位，**任何 scale < 1 都会让槽位边缘露出页背景**（不是"图小了"，是露底），所以只允许单调推近、且要"来回"就得让最小值仍是 1；位移必须落在推近让出来的盈余里（放大 s 倍后单侧各多 `(s−1)/2 × 槽位边长`，所以位移写成比例而非绝对 px）；**ease 恒为 `none`** 且不开放成旋钮。呼吸档的进度系数**单调不减但增量不均**——回退会让观众以为画面在缩小，加速会让运镜读成有意图的镜头调度，两者都不行。
  **段尾必须补一条 0.01s 的"钉住"补间**（`_image_drift_lines` 的 `pin`，位置在语句序列**最后**）。GSAP 建时间线时会把每条 `fromTo` 的**终点值**预渲染一遍，于是最后一段的终点（例：scale 1.06 / x 3.859）在 `t=0` 就已经写在元素上——成片里是"切页第一秒图比后面大一圈，到 1.0s 又缩回去"。终点值按**语句顺序**互相覆盖，所以钉住必须排在最后；排在前面治不了（它自己也会被后面的终点值盖掉）。
  **三类载体不漂**：`<video>`（它自己每秒 24 帧在放，叠运镜是运动冗余）、写了 `director` 的内联 SVG（那段作者已手写过编排，别替他叠第二层意图）、整页画布与 `stage:"keep"` 接续页（与它们跳过配图入场补间同一口径）。
- **SVG 的「导演」**：images.json 写 `director` 时 SVG 净化后内联成活 DOM、`director.steps` 按句起点展开成 GSAP 补间——写法与净化清单见 `references/canvas.md`「SVG 动画：两档」。要时间轴同步的状态变化用它，纯氛围循环走 `<img>`+墙钟（配图整体仍会吃上面那条 Ken Burns）。

## HTML 预览与渲染

### 1. 生成 HTML

```bash
python scripts/gen_hyperframes.py   -m audio_output/timing_manifest.json   -o hf-project/index.html   --images hf-project/images.json   --fps 24
```

`-o` 与 `--images` 的相对路径都相对**当前工作目录**；images.json 里各条的 `src` 才相对 `-o` 所在目录。画幅与主题在这一步烧进 HTML（默认 portrait + dark，见 SKILL.md「常用参数」）。

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

`run.py` 默认带 `--no-best-effort`：CLI 的 `--best-effort` **默认开**——媒体缺失或抓帧未就绪也照样出片，只打一条 warning。开着它出得了无声成片（上游只证明"音频文件在"，证明不了"Chrome 侧加载成功"），与本技能红线冲突，所以默认关。撞上这个渲染失败先按终端报错修媒体（缺文件补上、格式怪转码）；只想先看整片节奏、不在乎那一段缺帧，加 `--allow-best-effort`（调试档，成片别拿去交付）。

逃生口：`--draft` 一次全开（占位 / ease / 缺 src 降级 / 比例·对比度·字号降 warn / best-effort），report 记 `draft: true`。口径见 SKILL.md「常用参数」。

## 视觉参数临时改

`--set 键=值`（可重复）改一个模板视觉参数，`--drift off|subtle|boring` 换全片运镜档——**都只在本次进程生效**。定下来的数值写回 `scripts/_template.py`，否则下次重跑就没了。

## 性能参数

默认值与档位见 `run.py --help`（`--fps 24`、`--workers 4`、`--quality looks`），这里只写"什么时候该动它"：

- **`fps` / `workers` / `quality` 的最优组合别猜，让机器量**——官方 `benchmark` 自己变这几个变量跑几组，给耗时与体积对比：

  ```bash
  npx -y hyperframes benchmark <项目目录> --runs 3 --json
  ```

  下面那几条（如 workers 饱和点）是本机量出来的经验，**换机器、换稿件规模就该重跑一次**；要把这次的结果喂给编排，`--json` 的输出直接可读。
- `fps`：抓帧耗时与帧数线性，唯一一阶杠杆。快速看画面用 `--fps 12 --quality draft`。
- `workers`：字面透传给 `hyperframes render`。CLI 不给时默认是 `auto`（自己按机器校准），但 `run.py` **恒显式传 `--workers`**——走本技能就永远拿不到那份自动校准，要多少显式写。8 之前有效、之后饱和；迭代片 `--workers 8`；V8 堆崩溃降到 2。偏慢时先在 `out.render.log` 找 `Parallel capture timed out`。
- `quality`：五档透传（`draft` / `looks` / `standard` / `high` / `delivery`），底层其实只有 draft / standard / high 三档：`looks` = standard + CRF 16（默认），**`delivery` 就是 high**（同一档，不是更高质量）。`draft` 省约 15%、预览够用。
- **固定开销**：每调用一次约白付 8.5s，冷跑第一次多约 6s。先 `--until html` + 快照看够，再整片渲一次。
- **低配机器（`--low-memory-mode`）**：CLI 把 worker 钉成 1（按总内存 ≤8GB 自判）：

  ```bash
  python scripts/run.py --source SOURCE -o OUT --quality draft --workers 1 --low-memory-mode
  ```

  `--workers` 与它冲突时 **CLI 赢**，`run.py` 打 `[warn]`；`--no-low-memory-mode` 关自判。**容器里自判读宿主机总内存、不是 cgroup 限额**——小限额容器跑在大内存宿主上不会自动进低内存档，要显式 `PRODUCER_LOW_MEMORY_MODE=1`（低内存档 = worker 钉 1 + 截图抓帧）。Chrome 启动/协议超时另有 `--browser-timeout` / `--protocol-timeout`（`run.py` 不透传）。

## 官方校验命令（优先于手搓探针）

```bash
npx -y hyperframes check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes lint <项目目录>           # 只跑静态那一阶段（不开浏览器）：改 HTML 的迭代期用它
npx -y hyperframes snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

`check` 是 lint 加 browser 四阶段，所以**迭代期跑 `lint`、定稿跑 `check`**——改一行 HTML 只为确认 lint 干净时，不必付后面四阶段的浏览器开销。`lint` 默认只报 error/warning，`--verbose` 才带上 info 级（如"依赖外部脚本"那条），`--json` 给机器可读输出（errorCount / warningCount / infoCount + findings）。

本节口径对着 **v0.8.162** 实测过（三场景：竖屏 2 段 / 横屏 2 段 / 竖屏 6 段，findings 类型逐条一致）。CLI 迭代快，升级后先看 `--help` 再照这里改写法。

- `check` 会真报问题：字体栈出现未声明 `@font-face` 的家族名判 **error**（`font_family_without_font_face`）；家族名必须是映射表认识的写法（认 `"SF Mono"` 不认 `SFMono-Regular`），否则只打 `[WARN] No deterministic font mapping`。
- **Lint 有 error ⇒ browser 四阶段整个不跑**（`shouldBlockRender(strictErrors=true,…)`：`totalErrors>0` 直接 `emptyBrowserResult()`，输出"Browser session never ran — 以下是空占位，不是干净通过"）。所以 **Runtime/Layout/Motion/Contrast 显示 `0 issues across 0 sample(s)` / `0/0 text checks` 不是通过，是失明**——那四个 0 是"没量"，不是"量过没问题"。判读顺序：先看 Lint 是不是 0 error，再谈后面四阶段；`check` 的退出码只由**持久** findings 决定，browser 没跑时 exit code 也可能非 0（Lint 的 error 是持久的），别看到非 0 就以为是渲染炸了。二进制复现过：同一产物删掉一条 `letterSpacing` 补间（Lint 转 0 error），browser 阶段立即开跑。
- 干净产物的 `check` 基线：**Lint 0 error**，Runtime / Motion / Contrast 全 0，**Layout 0 error**。warning 条数**随稿子规模走**，只看类型不看条数：
  - 噪声：`gsap_callback_dom_measurement`（1）、`timeline_track_too_dense`（1）、`composition_file_too_large`（0~1，HTML 够大才有）、`nested_structure_needs_subcomposition`（**每张卡一条**：opening + 内容段 + closing）。
  - 误报：`negative_z_index`（**恒 5 条**＝ CSS 里负 z-index 规则的条数：`.seg-card::after`、`.apple-halo`、`.apple-glow`、`.seg-card>.bg/.grid`、`.verse::before`），Fix 是 `isolation: isolate`（已加在各层的直接父元素上，但 lint 是静态扫 CSS、不看 isolation，所以照样报）。
  - `gsap_infinite_repeat`（**开屏页才有**，1 条，默认强绑定）：噪声。别处的 `repeat:-1` 与无界 duration 都要当真。
  - 于是条数≈`5 + 卡片数 + 2~3`——**条数随卡片数走，别拿任何一个样本当固定值**。
  - **判据**：6 类之外的新增条目一律当真，**类型多出一类就要查**（类型在 8 种稿子／两画幅的实测里恒定，条数才是变的）。Layout 的 `✗` 要当真，例外只有 `text_occluded`。
- `clip_media_fit`（Runtime）与 `clipped_text`（Layout）**不在噪声之列**：前者是音频短于 `data-duration`，手写/裁剪 manifest 时是"manifest 与音频不同步"的唯一信号；后者是文本被自己的盒子裁掉（`nameTrim` 只挡字数）。
- Layout 的 `text_occluded`：句子流滚出窗口的行被裁掉但静态 rect 仍在原位，逐行量就判"文字藏在不透明元素下"。**做法**：给句子流每个带文字的元素打豁免属性，但**三个属性的继承规则不同，别按一套打**：
  - `data-layout-allow-occlusion` / `data-layout-allow-overlap`：检查器只认元素**自己身上**的标记、**不继承祖先**——`.verse-line` 与 `.verse-who` 每层都得自带，漏一层就报那一层。
  - `data-layout-allow-overflow`：**继承到子树**（感知探针向上遍历祖先），打在 `.verse`（`overflow:hidden` 所在层）一个地方就覆盖整棵子树，逐行重复写是冗余的（实测删掉行上那份，Layout 输出逐条不变）。
  - `overflow` 别撤：撤掉后长段滚动会刷出一批 info 级 `text_box_overflow`，它随段落长度线性增长、把真正要看的清单淹掉。
  - **同一个属性在两个阶段规则不同，别按一套写**：上面说的是 **Layout 阶段**（`layout-audit`，判 `text_occluded`/overlap 用的是元素**自己身上**的 `hasAttribute`）；**Contrast 阶段**（`contrast-audit`）的 `occlusion` 走的是 `closest()`，打任一祖先上都覆盖整棵子树。所以只用两句话：Layout 要豁免就打在报出来的那一层，Contrast 要豁免打祖先层即可；补 `overflow` 对 Layout 的 `text_occluded` 不管用。
  - **调 `proseCoverageFloor` 优于层层打标**：成句/成段的长文本（`text_occluded` 判据里叫 prose）要被盖住超过 15% 才报，这个后验门限可以压——`npx -y hyperframes check <项目目录> --layout "proseCoverageFloor=0.05"`。原子标签（单词级的短 label）不受它保护，**沾到就算**。
  - **再见到 `text_occluded`**：先 `snapshot --at <时刻>` 看画面；真被遮就调版式；画面正常就是某层漏打了 `occlusion`/`overlap`，按 `check` 报的选择器补那一层。
  - **横屏的段落标题**（`#title-*`）会在**段落入场附近**多出 info 级 `text_occluded`（竖屏没有）：检查器恰在擦除进行中采样。`snapshot --at` 看标题好好在那儿就是这条，info 不进退出码，确认画面正常即可忽略。
- `snapshot --at` 取时刻帧并自动拼 contact sheet；手搓探针只在需同页多帧或 `snapshot` 不可用时作后备。
- 渲染前用 `npx -y hyperframes doctor` 判依赖：全绿直接跑真渲染（Chrome 缓存在 `~/.cache/hyperframes/chrome/`）。
- **三条命令之外的口径**（CLI 写明、踩上不报错）：`validate` / `inspect` / `layout` 三个都是**废弃别名**（官方明示"still work but are deprecated"，改用 `check`），`check` = lint + runtime + layout + motion + contrast 一次跑完；`check` 会先跑 `lint`（迭代期单跑 lint、定稿再跑 check）；`doctor --json` **恒退出 0**——当门禁必须读 `.ok`（`hyperframes doctor --json | jq -e '.ok'`）；`check` 的退出码只由**持久** findings 决定；要 warning 也卡住交付，给 `--strict`。
- 要**跨机逐帧一致**用 `render --docker`（钉住 Chromium、字体集与编码器）；本机迭代不必付。核对时间轴用 `hyperframes timeline --json`；起预览服务用 `npx hyperframes preview --background`。

## 画面翻车速查表

按**看到的症状**查，不按知识点查——出问题时你手里只有一个现象。每条给的是成因与去处，去处写成 `文件`「章节」+ 具体那一条，可直接跳过去。

| 看到的症状 | 成因 | 去处 |
| --- | --- | --- |
| 推镜时页底标签不见了、页顶标题贴着边 | `scale>1` 以 `svgOrigin` 为中心放大，离焦点越远位移越大。**留白要按"推完那一帧"算，不是按 SVG 源码算** | `canvas.md`「SVG 动画：两档」里「运镜 / 相机」条的「放大位移离焦点越远越大」 |
| 推近的图形压到了旁边静止的标题或旁白上 | 出画 warn 只判"出画"，不判"撞字"；两者都没出画时无人拦 | `canvas.md`「SVG 动画：两档」里「运镜 / 相机」条的「机器兜不住的那件事」 |
| `keep` 接续页界上图形**位置**跳一下（尺寸不变） | 没写原点时 GSAP 绕 `#cam` bbox 左上角，那个角还跟着别的步的 `from` 瞬态飘 | `canvas.md`「跨段接续」里「相机绕的那一点是渲染器钉的」 |
| `keep` 接续页界上整幅换色，但图形其实接上了 | 每页 `--seg-accent` 不同，模板氛围光与进度条在切页那帧整幅换色。**要量连续性就按柱子边沿定位，别拿整幅 PSNR 当判据** | `canvas.md`「跨段接续」的实测段 |
| 图内标签在成片里看不见，全流程零报错 | `<img>` 载入的 SVG 里文字不属于 HTML 文本流，没有一道检查量它 | `canvas.md`「图内文字的对比度」里「没有任何一道检查会量它」 |
| 图内文字右缘出血（量出来的右缘超过上限） | `text-anchor="end"` 的落点要按量出来的字宽倒推，不是按字符数估 | `canvas.md`「整页画布」里「安全边距照页面走」 |
| 图里两行文字压在一起（`hyperframes check` 报 `content_overlap`） | 两条 y 只差十来个像素——`canvas_kit` 的柱标签固定落在 `base_y+32`，自己加的说明文字很容易撞上它。`check_svg` 查不到图内压字，只有 `check` 的 Layout 阶段能量 | `canvas.md`「画布几何」的末条 |
| `check` 报 Layout `text_occluded`，但快照里文字好好地在那儿 | 句子流滚出窗口的行 rect 仍在原位，属误报——豁免标记已由模板打在每一行上，见下 |
| `check` 的 Runtime/Layout/Motion/Contrast 全是 `0 issues across 0 sample(s)` / `0/0 text checks` 空占位 | Lint 有 error，browser 四阶段被 `shouldBlockRender` 整个跳过——那些 0 是"没量"不是"量过"。先修 Lint 到 0 error 再判后面四阶段 | `rendering.md`「官方校验命令」 |
| 压缩成片后轴线/网格/分隔线像是没画 | 色档太暗 + 线宽不足 3px 在压缩后被抹平 | `canvas.md`「整页画布的密度与层次」里「辅助线要敢用亮档」 |
| 整张画面发闷、像一片渐变加一块图 | 背景网格太暗被整体 alpha 再合成一次（模板 `_theme.grid_color`），或氛围光椭圆边界还看得出来（`ambience.alpha`/`edge`） | 本节「画面结构」的氛围光与网格两条（改前成片对比度只有 1.08:1） |
| 暂停一帧像一页空白（中段整条空带） | 标题/主视觉/注脚没分成三段并用中景连起来 | `canvas.md`「整页画布的密度与层次」里「竖向不许有整条空带」 |
| 念完一句之后它还整句亮着、和下一句抢焦点 | 没有收束：句子流的 accent 只在换句那帧被硬切走 | `rendering.md`「动画」里「词级强调」的「念完之后的收束」一条（要不要演、演多久由生成期按句尾留白一次算完） |
| 动画与旁白差 0.1s 量级 | 句内小数是线性猜的（**整数句序是准的**） | `canvas.md`「SVG 动画：两档」里 `at` 条的「整数句序是准的，小数是线性猜的」 |
| 浏览器里颜色分明、成片里发闷 | 图内最大档与最小档字号比值不足 2，观众分不出主次 | `canvas.md`「整页画布」里「图内可以有标题，字号按整页读」 |
| 整页画布在浏览器里是均匀一块死色 | 铺了满幅底板，把模板背景三层盖掉了 | `canvas.md`「不铺满幅底」里那条整页画布的说明（生成期也会按段落 `[warn]`，判据与自查同一份） |
| 只有开头一秒是空的 | 整页画布没有转场遮，逐拍揭示意味着前 1 秒画面是空的 | `canvas.md`「整页画布的密度与层次」里谈导演页起始帧那条（全空起步会被生成期 `[warn]`「起始帧全空」拦出来） |
| 成片很长，`--check-frames` 说"通过"却总觉得没看全 | 抽帧上限 40 帧：装不下 `every` 就**按整片均分**（不是只抽开头），报告里那条 `[coverage]` 写明实际每几秒一帧、覆盖到哪一秒。要逐段细看就分段跑并给 `--timestamps` | `scripts/check_frames.py` 的 `sample_timestamps` |
| 开了 `--low-memory-mode`，`--workers 4` 却像没生效 | 低内存档由 CLI 把 worker 钉成 1，它赢。`run.py` 会打一条 `[warn]` | `rendering.md`「性能参数」 |
| 成片里某一段画面是糊的、虚的 | 素材像素不够铺满它那一页的框：`cover` 只保证填满、不保证清楚，放大 2 倍以上细节就没了，而全流程零报错 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」的第二条规格 |
| 配图明明够清晰，生成期却报"不够大" | 按它实际落地的框判：整页画布按整幅（1080×1440 / 1920×1080），槽位按 4:3 那一块。同一张图在槽位够用、换成整页画布就不够 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」 |
| 开场动画最后几行像没出现 | 苹果风开场整支舞按可见窗口等比压缩；压到地板仍装不下时收尾会被下一页切走（生成期有 `[warn]`） | `rendering.md`「苹果风开场」 |
| 开场动画整体快了一拍，像被赶过 | 同上：窗口装不下就会压。写长 `opening`（说完主题 + 为什么值得看）就能回到原速 | `rendering.md`「苹果风开场」 |
