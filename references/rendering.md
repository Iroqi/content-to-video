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
- **揭屏的前提是卡元素的 `data-duration` 覆盖整个入场窗口**（窗口与 `wipe`/`win_start` 在 `generate_html` 的 clips 预处理里一次算齐）。外层 hyperframes 的可见窗口 `[data-start, data-start + data-duration)` 是**半开区间**，所以补间的**结束态别正好压在末端**：压线那一帧永不渲染（不报错，只是动画少一拍）。窗口末端按 `段时长 + gap + wipe` 算。
- 卡必须是不透明层：每卡自带 `.bg`/`.grid`（`z-index:-2`）。透明卡做 wipe 会双影叠字。
- **整页画布不参与模板转场**：`wipe` 强制归零走"瞬间切"（引导线、剥离、暗边都以 `wipe<=0` 为闸不生成），它在自己的**音频起点**整页出现；按**版式**判定，四档 `style` 一律生效。切过去那瞬间画布是空的——让导演第一拍尽早落子，见 `references/canvas.md`「SVG 动画：两档」；开头全空会被生成期打 `[warn]`「起始帧全空」。`stage:"keep"` 接续页同样 `wipe=0`、不生成配图入场补间（见 `canvas.md`「跨段接续」）。
- title / image 按模板参数入场。标题入场**按段序轮换节奏**（`_template.titleEntrance.rhythm` 四档：`back.out` 弹一下 / `expo.out` 滑上来 / `power3.out` 淡入 / `power2.out` 大落差落定，按**卡片序**取模，不是按会漂的配图序）；第 0 段仍是 `scale:0.5` 那一档。tagline 跟着标题那一档进来（幅度小一号、起点错峰）。
  **题头组第一行** `.agenda-kicker`：开屏由苹果编排逐字演；**结尾页**跟标题同拍、从字距展开态整行收拢（`animation.titleEntrance.kicker`）。两页的 kicker 都带一枚行尾字距垫，保证展开期间行宽不变、最后一字不被裁。
  image 入场是**两层**（框先落位、图本体收拢，见「配图」settle 一条），之后是贯穿整段的缓慢运镜（Ken Burns）——一段落在画面上的 8~20 秒里，一直在动的只有它和句子流。
  **CSS 不得给 GSAP 补间的元素声明 `transform`**；要居中/位移用绝对定位的 auto 外边距。
- verse 当前句用该段 accent 色高亮（荧光笔式渐变下划线），字重不切换。对话段（`dialogue`）在**轮次切换的首句**行首挂说话人标签 `.verse-who`（同一人连说多句只挂第一句）。字幕的**换色与透明度**在成片里是瞬时的：淡入补间只由 `preview.js` 在人工预览分支注入，**别给 `.verse*` 加 `transition`**；而**滚动位移**与接力窗口内的**景深**是逐帧插值的（见下「焦点层级的第三个维度」「第四个维度」），不是瞬时。
- **词级强调（karaoke）**：当前句按句内进度从左往右点亮，未念到的半句压暗。它只由「时刻 + 开口时刻 `data-t0` + 发声长度 `data-voiced` + 实测字宽」决定，不含"亮到第几个"的状态，所以逐帧乱序 seek 也幂等；换句时刻仍由 cue 硬切。TTS 没有词级时间戳，句内匀速是唯一可用的假设。
  **进度分母是发声长度，不是音频块长度**：manifest 的 `voiced`（`references/tts_pipeline.md`「发声区间」）扣掉了句首吸气与句尾拖音，念完那一刻进度正好 100%，不会"最后几个字先亮完、再干等"。老 manifest 无该字段退回 `data-t1`，行为与从前逐字节一致。同理 cue 起点是 `start_time + onset`，字幕在**开口那一刻**才换。
  结构：正文是**一个裸文本节点**，行尾一个空的 `.verse-em` 挂点 + 一层 `.verse-ved` 覆盖物。覆盖物生成期写进**整句原文**（`data-text`）、与正文共用同一套行盒与字体声明，所以逐字重合；运行时只把已念字的 alpha 归 0，未念字保持**实色**盖住 accent。三条不能碰：正文不能包 span（含文字的叶子会被 Layout 逐字报 `text_occluded`）；覆盖物不能只写 `slice(k)`（折行点前移＝重影）；不能用半透明压暗（整句本身就是 accent，浅色盖不住）。

  **念完之后的收束（settle）**：一句念完时整句仍是全亮 accent，与下一句刚亮的第一个字**同色同重**——画面上有两个焦点。句尾把整句逐字交还给"已过去"那一档，焦点让给下一句。**逐字错峰**而不是整句一起压暗（后者在 0.3s 内同时变灰＝闪烁）：第 i 个字延迟 `i × (时长 ÷ 字数)`。
  时长只有一个数 `--ctv-ved-settle`，生成期按「本句念完 → 下一次换句」的留白算出（`_ved_settle_seconds`），扣掉换句接力 0.32s，封顶 0.6s、地板 0.08s，按 0.05s 网格**向下**取整。留白为 0（首尾相接的稿件、无 `voiced` 的老 manifest）→ 变量写 0、CSS 规则整条失效；页末句后面跟转场，同样不演。运行时只读这一个数。
- **焦点衰减（focus falloff）**：句子流**亮度**按「离当前句多远」衰减（`d` 为句序差，未来侧正、过去侧负）：未来 `d=1→0.62`、`d=2→0.45`、`d≥3→0.327`；过去整条提前一档 `d=-1→0.45`、`d=-2→0.327`、`d≤-3→0.237`。曲线真源在模板 `subtitle.focus`（future 0.62 / past 0.45 / ratio 0.726），经 `--ctv-focus-*` 注入，`--set subtitle.focus.*` 可临时改；运行时展开成查表、|d| 封顶第三档。`composition.css` 里的 0.62 / 0.45 只是运行时未接管时的兜底。
- **焦点层级的第二个维度：尺寸**：远处行同时**更小**。同一条几何曲线、另一个 ratio：模板 `subtitle.focus.scale` 默认 `0.94`，即 `d=1→0.94`、`d=2→0.8836` 封住（过去侧同样提前一档：`d=-1→0.8836`、`d≤-2→0.8306`）。
  - **尺寸只封两档、亮度封三档**，由各自的失效边界定：最深档是过去侧 `d≤-2` 的 `0.94³=0.8306`——横屏字幕 33px 缩到 27.4px，仍在 26px 可读下限之上；再封一档（0.94⁴ → 25.8px）就破线。
  - **只走 `transform: scale()`，不改 `font-size`**：改字号会重排（折行点、`offsetTop`、覆盖层折行全变＝重影）；`transform` 不进布局，滚动公式与逐字命中测试一字不改。**只写 `scale`、不写 `translate`**：位移归 `.verse-clip` 独占，行上再叠位移会把行推离锚点。
  - `transform-origin` 锚**左缘**（`left center`）：字幕左对齐，绕中心缩放会让行的左边界在换句时左右抖。
  - 合成规则与亮度**完全同一条**：`transform:scale(min(var(--ctv-line-scale,1),var(--ctv-relay-scale,1)))`，焦点档与接力值谁更"远"听谁的。接力期间尺寸从**未来档**（0.94）长到 1，与亮度共用同一个 `p`、同一时刻到位（亮度那个 0.35 起点是"从更深处浮出"的换句信号，尺寸照抄会把新句先缩成一小团再撑开，读成形变）。
  - 覆盖层的 `--ctv-ved-dx`（说话人标签让开的那段行内前缀）在量取时**必须除回当前 scale**：它用 `getBoundingClientRect` 量，而覆盖层首次上屏几乎总在换句那一帧、正是 `scale` 最小的时候；不除则对话段首句少算 6%，覆盖层整体左移 4.1px＝可见的横向重影。
- **焦点层级的第三个维度：位置（滚动）**：行不是"啪地换掉"的，是被**推着上去**的——只做亮度和尺寸则整块字幕在换句瞬间硬跳（实测一帧内 `translateY` 从 0 跳到 −70px）。这一维由 `runtime.js` 的 `verseScroll` 每帧插值，落在 `.verse-clip` 的 `translateY` 上（位移仍归 `.verse-clip` 独占，行上只允许 `scale`）。
  - **与亮度、尺寸共用同一个接力窗口**（`--ctv-relay-s`，默认 0.32 秒）和同一个 `p`（起点 = 该句 cue 起点）：三个维度同一时刻到位，"焦点"不能在途中分裂成"先滚、再亮"两个动作。
  - **曲线取钉固 GSAP 自己的档**：运行时用 `gsap.parseEase` 取用、不另抄一份公式（抄了就得靠测试盯两份相等，且 GSAP 不认这个档名时会**静默**回落缺省缓动）。档名真源是模板 `subtitle.scroll.ease`（默认 `sine.out`），生成期经 `--ctv-scroll-ease` 注入，运行时只读注入值。`--set subtitle.scroll.ease=...` 可临时换档；**认不出来的档名与过冲族（`back` / `elastic` / `bounce`）在生成期就 fail-fast**——落位由 `offsetTop` 说了算，冲过再回来＝甩出去再收回。
  - **落位仍由静态布局值算**：起点 = 同段上一句的落位，终点 = 当前句的落位（`offsetTop` 推出的 `translateY`），两者都从**布局值**重推，**不新增任何缓存字段**——缓存被字体就绪清掉后照样算得出（`缓存可以清，状态必须能从 t 重推`）。
  - **纯函数、幂等**：进度只由 `(t − 本句 cue 起点)` 算，不记"上一帧滚到哪"，乱序 seek 也幂等。
  - **预览与成片同源**：`preview.js` 因此**不再**给 `.verse-clip` 补 `transform` 过渡——同一段位移被两个时钟驱动，预览会拖尾、逐帧抓帧会读到半路值。
- **焦点层级的第四个维度：景深（模糊）**：离焦行同时**更虚**——前三维表达的是**强弱**，只有模糊表达"**不在焦平面上**"。真源是模板 `subtitle.focus.blur`（默认 `0.02`），**单位是 em**（相对该行自己的字号，两画幅按各自字号自动缩放，不存第二份）；每远一档**加**一个档而不是乘 ratio——高斯半径随离焦距离线性增长，照抄乘法会得到"越远越不虚"。过去侧与亮度/尺寸同一种错位（提前一档），`d=-1` 就是封顶值。
  - **封两档（与尺寸同档数、比亮度少一档）**：最深模糊 `2 × blur` 不得越过 `FOCUS_BLUR_MAX_EM`（`0.05em`，CJK 正文字重 600 的笔画宽度约 `0.07em`，半径逼近笔画宽度时相邻笔画并成一片）。生成期拦（`html_renderer`），`--set subtitle.focus.blur=0.03` 会被拒。
  - **滤镜挂在运行时合成的整条值上**（`--ctv-line-filter`），**不在 CSS 里做 max() 合成**：`blur(0em)` 也是 filter，实测把字形换成另一套栅格化（同一句 40px 白字，边缘拉普拉斯和 925410 → 994512、单像素最大差 80），而**当前行**是画面上最该锐利的那一句。所以"不需要模糊"必须是 `filter:none`（var 缺省即 none）；地板 `0.01em`（≈ 40px 字幕的 0.4px）在 `runtime.js` 的 `verseBlurSet`：模糊量已不可见时收到地板就撤滤镜，让撤滤镜那次栅格化切换被残存模糊盖住。
  - `--set subtitle.focus.blur=0` 关掉这一维（回到三维），`0` 是唯一合法的"不要它"写法。
- **换句接力（crossfade slide）**：新一句从 `--ctv-relay:0.35` 在 `--ctv-relay-s`（模板 `layout.<画幅>.subtitle.relay`，默认 0.32 秒）内推到 1，旧一句同时按 `past` 压到 0.45。CSS 写成**取小** `.verse-line{opacity:min(var(--ctv-line-op,.62),var(--ctv-relay,1))}`，两套状态共存、谁更暗听谁的。
  起点是**该行所属 cue 的起点**（不是滚动那一刻），往回 seek 才正确；行进入 `past` 后不再参与接力。接力**不存任何状态**，起点逐帧由当前 cue 传入——曾经把起点缓存进 verse 缓存对象，字体就绪清缓存一起带走，开场句整句卡在 0.35。教训：**缓存可以清，状态必须能从 t 重推**。
- **字幕链路的时钟**：截图档只把 `tl.time()` 拨到帧时刻，`onUpdate` 只在 seek **跨过 tween 边界**时触发。所以 `runtime.js` 铺一条零长 tween 的**时钟轨**，在**每个帧网格**上打点（只在 cue 起止打点会让一句之内只有两帧更新，成片里整句一直亮、书签冻住），`onUpdate` 与 `gsap.ticker` 双路喂同一个 `verseSync` 互为保险丝。**别把字幕状态改动只写在"cue 下标变化"分支里**；也别拿 `window.__HF_EXPORT_RENDER_SEEK_CONFIG` 决定时钟轨铺不铺密——它的可见时机取决于渲染器注入顺序，关掉会静默退化成"只有 cue 边界"，预览里看不出来、只在成片里暴露。

- 每段 accent 派生氛围光、槽位外发光、tagline 刻度条与进度条辉光（一律经 `color-mix`）——**选 accent 时注意它染整帧氛围**。`bare-media` 的 SVG 槽位不吃外发光与描边（见 `references/canvas.md`「不铺满幅底」）。
- **背景网格是中性灰、不带色相**（`_theme.grid_color`）：颜色叙事归每段氛围光，网格只负责"这一页有结构"。浓度按**成片对比度**定在页底 1.20:1——**改这个值请量成片，不要看浏览器预览**（预览是 sRGB 满精度，差 0.1 在屏幕上"看得出纹理"，导出就被抹平）。
- **句子流暗垫（仅竖屏）**：竖屏字幕钉在页底、正压氛围光最亮的一带，`.verse::before` 垫一层从透明渐隐到 90% 页面底色的片子。横屏不垫（`layout.<画幅>.verse.shade` 关掉）：定宽弹性列里整块宽垫垂中会读成"悬浮的一块板"。垫层要求 `.verse` 自己是层叠上下文（`isolation`），否则会掉到 `.bg/.grid` 底下、一像素都不显。

### 苹果风开场（开屏 agenda 卡默认强绑定）

开屏页（sid=`opening`）且为 agenda 版式时**默认强绑定**苹果风整页编排，无契约键、无需配置（`opening_animation` 键已随默认强绑定移除，写过旧键的稿件契约层会报"请删掉这个键"）。参数真源在 `_template.py` 的 `animation.opening.apple`。

- 整支舞按**可见窗口**（`[擦除起点, 被下一页盖住]`，不是口播段长）等比归一：收尾时刻由**标题字数与行数**共同决定（`标题字符波次尾 = stagger×(非空格字数−1) + duration`，`行内容滑入尾 = rows.content.delay + stagger×(行数−1) + duration`）；装得下走原速，装不下按 `可见窗口 ÷ 收尾时刻` 压缩，地板 0.5。
- 光晕主光呼吸与副光漂移的时长不参与压缩，压缩只落在淡入时长与起点。
- 装不下时打 `[warn]`（可见窗口、需要的时长、行数与字数），症状见「画面翻车速查表」。
- 锚点是**擦除起点 `win_start`** 而不是段起点。

## 配图

- 画布尺寸与配图规格见 `references/imagery.md`（第 3 步规则以它为准）；素材一律 `cover` 铺满画布。模板改动后重新生成 HTML，打开 `hf-project/index.html` 逐段看预览。
- 配图语义是否正确只能靠人眼判断（预览 + 成片）。
- **静态图的入场「收拢落位」（settle）**：从**略大、略偏**收拢到精确位置——框（`opacity` + `y`，占入场预算的 `frameFrac`）先落位，图本体（`scale` + `x/y`）随后在框里收拢，终点恒为 `{scale:1,x:0,y:0}`，正好是下面 Ken Burns 的起点。
  **旋钮 `animation.imageEntrance.settle`**：`scale` 起点放大倍数、`panX`/`panY` **最多用掉多少盈余**（[-1,1]，**符号即来向**，写 `-0.6` 就是"从左侧来"；比例而非 px，换画幅、改槽位都不可能露底）、`startFrac`（起点占入场预算的比例）、ease。**`duration` 不开放**：终点必须由入场预算推出，否则就是"没收完就开始推"或"收完了干等"。
  **来向按段序轮换**（`imageEntrance.rhythm` 四档：纵 / 右 / 纯收焦 / 左，按"第几张静态图"取模）。纵向一律与框同向，反向会读成两层在打架。
  **收拢补间必须排在运镜的钉住补间之前**：它的起点 `scale>1` 若最后落地，成片里就是"切页第一秒图比后面大一圈"。
- **静态图的持续运镜（Ken Burns）**：静态图（`<img>` 那一档，含 svg/png/gif）入场落定后整段剩余时间缓慢推近并微移。
  **旋钮在 `_template.py` 的 `animation.imageEntrance.drift`**（`ratePerSec` 每秒推近多少、`maxScale` 终态封顶、`panX`/`panY` **最多用掉多少盈余**[0,1]、`minSeconds` 短段门槛、`breathe` 呼吸档）。删掉整个 `drift` 块即关闭。
  **长段落是"呼吸"而不是匀速**：`breathe:{periodSeconds, depthFrac}` 把整段剩余时间切成 3–6 小段，推进速度"快 / 缓 / 快"地起伏（`depthFrac` 是缓段吃掉多少进度，上限 0.5）。段时长短于两个周期时自动退回单段匀速——8 秒以内的段切三段只会读成卡顿。
  **取景方向不写在模板里**：**相邻两张静态图必漂向相反一侧**，四张一轮回走遍四个象限；幅度按段落 id 派生（与 `canvas_kit` 的手绘抖动共用同一份 `seeded_wobble`）——同一稿件重跑逐字节一致。
  **三条约束**：`object-fit:cover` 已铺满槽位，**scale < 1 就会在槽位边缘露出页背景**（是露底，不是"图小了"），所以只允许单调推近、最小值恒为 1；位移必须落在推近让出的盈余里（放大 s 倍单侧多 `(s−1)/2 × 槽位边长`，所以写比例不写 px）；**ease 恒为 `none`**，不开放成旋钮。呼吸档的进度系数**单调不减但增量不均**。
  **段尾必须补一条 0.01s 的"钉住"补间**（`_image_drift_lines` 的 `pin`，放在语句序列**最后**）。GSAP 建时间线时会把每条 `fromTo` 的终点值预渲染一遍，最后一段的终点（例 scale 1.06）在 `t=0` 就写在元素上——成片里"切页第一秒图比后面大一圈，1.0s 又缩回去"。终点值按语句顺序互相覆盖，所以钉住只能排最后。
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
  npx -y hyperframes@0.8.185 benchmark <项目目录> --runs 3 --json
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
npx -y hyperframes@0.8.185 check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes@0.8.185 lint <项目目录>           # 只跑静态那一阶段（不开浏览器）：改 HTML 的迭代期用它
npx -y hyperframes@0.8.185 snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes@0.8.185 doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

`check` = lint + browser 五阶段（Runtime / Layout / Motion / Contrast / Craft），**迭代期跑 `lint`、定稿跑 `check`**。`lint` 默认只报 error/warning，`--verbose` 带 info 级，`--json` 给机器可读输出（errorCount / warningCount / infoCount + findings）。

**`run.py` 已经接上它，不用手敲**：出 HTML 后 `--until check` 档跑完门禁就停；不给 `--until` 时渲染前也会自动跑一次，有 error 直接 `[run][error] 官方门禁 …` 停住、不进渲染。判读口径在 `_render_backend.check_verdict`：error 一律当真，warning 只把 `GATE_FINDING_CODES` 之外的新 code 当真，Lint 有 error 时额外点一条 `browser_stages_skipped`。阶段按结构认（顶层带 `findings` 的块都判读），CLI 新增阶段不用改这张表。

本节口径对着 **v0.8.185** 实测过（竖屏 2 段 / 横屏 2 段 / 竖屏 6 段，findings 类型逐条一致）。升级后先看 `--help` 再照这里改写法，并把 `GATE_FINDING_CODES` 与下面这张噪声表一起复核。

- 字体栈出现未声明 `@font-face` 的家族名判 **error**（`font_family_without_font_face`）；家族名要用映射表认识的写法（认 `"SF Mono"` 不认 `SFMono-Regular`），否则只打 `[WARN] No deterministic font mapping`。
- **Lint 有 error ⇒ browser 五阶段整个不跑**（`shouldBlockRender(strictErrors=true,…)` 直接返回空结果）。所以 **Runtime/Layout/Motion/Contrast/Craft 显示 0 项不是通过，是失明**。判读顺序永远是先看 Lint 0 error；`check` 退出码由**持久** findings 决定，browser 没跑时也可能非 0，别以为是渲染炸了。
- 干净产物的 `check` 基线：**Lint 0 error**，Runtime / Motion / Contrast 全 0，**Layout 0 error**。warning 条数**随稿子规模走**，只看类型不看条数：
  - 噪声（**全表以 `scripts/_render_backend.py` 的 `GATE_NOISE_CODES` 为准**，`tests/check_docs.py` 逐码对账，改一边不改另一边会报错）：
    `gsap_callback_dom_measurement`（补间回调里量字宽）、`timeline_track_too_dense`（字幕时钟轨按帧打点）、`composition_file_too_large`（HTML 够大才有）、`nested_structure_needs_subcomposition`（**每张卡一条**：opening + 内容段 + closing）、`css_transition_used`（模板兜底 CSS 的过渡，运行时由 GSAP 接管）、`overlapping_gsap_tweens`（入场与运镜在交接处碰同一属性）、`gsap_repeated_fromto_without_baseline`（同一 `fromTo` 先后落在不同元素上）、`gsap_infinite_repeat`（**开屏页才有**，默认强绑定）、`gsap_timeline_set_initial_hide`（开场前显式藏层）。
  - 误报：`negative_z_index`（**恒 5 条**＝ CSS 里负 z-index 规则的条数：`.seg-card::after`、`.apple-halo`、`.apple-glow`、`.seg-card>.bg/.grid`、`.verse::before`），Fix 是 `isolation: isolate`（已加在各层的直接父元素上，但 lint 是静态扫 CSS、不看 isolation，所以照样报）。
  - 于是条数≈`5 + 卡片数 + 6`（candidate 与整条链实测：竖屏 3 卡 14 条、横屏 3 卡 14 条，类型逐条相同）；**条数随卡片数走，别拿任何一个样本当固定值**。
  - `container_overflow` **不该再出现**：入场收拢（settle 起手 `scale` 1.05~1.08）与 Ken Burns 全程以 `scale≥1` 铺满槽位，子元素 bbox 每帧都比容器大几像素——配图容器 `#img-*` 带 `data-layout-allow-overflow` 把这条豁免掉。真出现就是**有人拿掉了那个标记**或**槽位里塞了比例不对的 SVG**（后者由生成期槽位比例护栏拦，见 `references/imagery.md`）。
  - **判据**：`GATE_NOISE_CODES` 之外的新增条目一律当真（`run.py` 会以 `unregistered:<code>` 点名）。**类型多出一类就要查**——条数随卡片数变，类型本该恒定。Layout 的 `✗` 要当真，例外只有 `text_occluded`。
- `clip_media_fit`（Runtime）与 `clipped_text`（Layout）**不在噪声之列**：前者是音频短于 `data-duration`，手写/裁剪 manifest 时是"manifest 与音频不同步"的唯一信号；后者是文本被自己的盒子裁掉（`nameTrim` 只挡字数）。
- Layout 的 `text_occluded`：句子流滚出窗口的行被裁掉但静态 rect 仍在原位，判成"文字藏在不透明元素下"。做法是打豁免属性，而**三个属性的继承规则不同，别按一套打**：
  - `data-layout-allow-occlusion` / `data-layout-allow-overlap`：只认元素**自己身上**、**不继承祖先**——`.verse-line` 与 `.verse-who` 每层都得自带。
  - `data-layout-allow-overflow`：**继承到子树**，打在 `.verse`（`overflow:hidden` 所在层）一处即可。别撤 `overflow`：撤掉后长段滚动会刷出一批 info 级 `text_box_overflow`，随段落长度线性增长。
  - **同一属性在两个阶段规则不同**：Layout 阶段（`layout-audit`）用元素自己身上的 `hasAttribute`，豁免就打报出来的那一层；Contrast 阶段（`contrast-audit`）走 `closest()`，打祖先即可。补 `overflow` 对 Layout 的 `text_occluded` 不管用。
  - **调 `proseCoverageFloor` 优于层层打标**：成句长文本要被盖住超过 15% 才报，可压：`--layout "proseCoverageFloor=0.05"`。单词级短 label 不受保护，沾到就算。
  - **再见到 `text_occluded`**：先 `snapshot --at <时刻>` 看画面；真被遮就调版式，画面正常就是某层漏打了豁免，按 `check` 报的选择器补。
  - **横屏的段落标题**（`#title-*`）会在**段落入场附近**多出 info 级 `text_occluded`（竖屏没有）：检查器恰在擦除进行中采样。`snapshot --at` 看标题好好在那儿就是这条，info 不进退出码，确认画面正常即可忽略。
- `snapshot --at` 取时刻帧并自动拼 contact sheet；手搓探针只在需同页多帧或 `snapshot` 不可用时作后备。
- 渲染前用 `npx -y hyperframes@0.8.185 doctor` 判依赖：全绿直接跑真渲染（Chrome 缓存在 `~/.cache/hyperframes/chrome/`）。
- **CLI 写明、踩上不报错的几条**：`validate` / `inspect` / `layout` 都是**废弃别名**，用 `check`；`doctor --json` **恒退出 0**，当门禁必须读 `.ok`（`hyperframes doctor --json | jq -e '.ok'`）；`doctor` 不支持 `--strict`，要 warning 也阻断的是 `check --strict`。
- 要**跨机逐帧一致**用 `render --docker`（钉住 Chromium、字体集与编码器）；本机迭代不必付。核对时间轴用 `hyperframes timeline --json`；起预览服务用 `npx hyperframes@0.8.185 preview --background`。

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
| `check` 的 Runtime/Layout/Motion/Contrast/Craft 全是空结果 | Lint 有 error，browser 五阶段被 `shouldBlockRender` 整个跳过。先修 Lint 到 0 error 再判后面五阶段 | `rendering.md`「官方校验命令」 |
| 压缩成片后轴线/网格/分隔线像是没画 | 色档太暗 + 线宽不足 3px 在压缩后被抹平 | `canvas.md`「整页画布的密度与层次」里「辅助线要敢用亮档」 |
| 整张画面发闷、像一片渐变加一块图 | 背景网格太暗被整体 alpha 再合成一次（模板 `_theme.grid_color`），或氛围光椭圆边界还看得出来（`ambience.alpha`/`edge`） | 本节「画面结构」的氛围光与网格两条（改前成片对比度只有 1.08:1） |
| 暂停一帧像一页空白（中段整条空带） | 标题/主视觉/注脚没分成三段并用中景连起来 | `canvas.md`「整页画布的密度与层次」里「竖向不许有整条空带」 |
| 念完一句之后它还整句亮着、和下一句抢焦点 | 没有收束：句子流的 accent 只在换句那帧被硬切走 | `rendering.md`「动画」里「词级强调」的「念完之后的收束」一条（要不要演、演多久由生成期按句尾留白一次算完） |
| 换句那一瞬间整块字幕硬跳一下（亮度、尺寸都是柔的，只有位置是硬的） | 滚动那一维没插值：`.verse-clip` 的 `translateY` 只在换句时写一次落位 | `rendering.md`「动画」里「焦点层级的第三个维度：位置（滚动）」 |
| 离焦的字幕行糊成一团、读不出字，或整片字幕像没对上焦 | 景深档开太大（最深模糊 = 2 × `subtitle.focus.blur`）；先用 `--set subtitle.focus.blur=0` 关掉对照，确认就是它 | `rendering.md`「动画」里「焦点层级的第四个维度：景深（模糊）」 |
| 动画与旁白差 0.1s 量级 | 句内小数是线性猜的（**整数句序是准的**） | `canvas.md`「SVG 动画：两档」里 `at` 条的「整数句序是准的，小数是线性猜的」 |
| 浏览器里颜色分明、成片里发闷 | 图内最大档与最小档字号比值不足 2，观众分不出主次 | `canvas.md`「整页画布」里「图内可以有标题，字号按整页读」 |
| 整页画布在浏览器里是均匀一块死色 | 铺了满幅底板，把模板背景三层盖掉了 | `canvas.md`「不铺满幅底」里那条整页画布的说明（生成期也会按段落 `[warn]`，判据与自查同一份） |
| 只有开头一秒是空的 | 整页画布没有转场遮，逐拍揭示意味着前 1 秒画面是空的 | `canvas.md`「整页画布的密度与层次」里谈导演页起始帧那条（全空起步会被生成期 `[warn]`「起始帧全空」拦出来） |
| 开了 `--low-memory-mode`，`--workers 4` 却像没生效 | 低内存档由 CLI 把 worker 钉成 1，它赢。`run.py` 会打一条 `[warn]` | `rendering.md`「性能参数」 |
| 成片里某一段画面是糊的、虚的 | 素材像素不够铺满它那一页的框：`cover` 只保证填满、不保证清楚，放大 2 倍以上细节就没了，而全流程零报错 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」的第二条规格 |
| 配图明明够清晰，生成期却报"不够大" | 按它实际落地的框判：整页画布按整幅（1080×1440 / 1920×1080），槽位按 4:3 那一块。同一张图在槽位够用、换成整页画布就不够 | `imagery.md`「两种形式的统一规则」里「统一使用同一个内容画布」 |
| 开场动画最后几行像没出现 | 苹果风开场整支舞按可见窗口等比压缩；压到地板仍装不下时收尾会被下一页切走（生成期有 `[warn]`） | `rendering.md`「苹果风开场」 |
| 开场动画整体快了一拍，像被赶过 | 同上：窗口装不下就会压。写长 `opening`（说完主题 + 为什么值得看）就能回到原速 | `rendering.md`「苹果风开场」 |
