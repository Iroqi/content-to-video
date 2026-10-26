# 第 3 步 · 动效判据：每一条补间在替观众做什么

`references/rendering.md` 讲"怎么实现"，本文件讲"**该不该动、动多快、动给谁看**"。
写代码之前先在这里过一遍；过不了就别写那条补间。

## 先写台词板，再写补间

动笔画任何一条补间之前，先在稿子里把**这一段的画面在第几句旁白时变成什么**写成
一张表（`segments_source.json` 的 `beats`）：

```json
{"id": "seg3", "title": "两个状态",
 "text": "先看左边这个状态。然后它变成了右边这个状态。合起来就是答案。",
 "beats": ["点亮左侧的状态", "把左侧压暗、点亮右侧", "两条并成一条"]}
```

简写时**下标就是句序**；要跳句（第 2 句不换画面）用全写：

```json
"beats": [{"on": 0, "change": "点亮左侧的状态"},
          {"on": 2, "change": "两条并成一条"}]
```

两种写法不可混用——混用时"第几拍"有两个来源，没写 `on` 的那几条会静默错位，
契约层直接拒。

这条表为什么必须先写：

- **`on` 是必填的**。说不上来这一拍在第几句变，说明这一拍还没想清楚——那正是
  后面"图停在第一态、旁白已经讲到第三态"的来源。这个病**没有任何门禁看得见**：
  `hyperframes check` 只证明能渲，时长契约只证明演完，`--perceptual` 只知道"画面在动"。
- **它会跟着时间轴校验**。`on` 是整数句序，而句子是重分句后定下来的；句数一变，
  `on: 4` 就落到空处。`run.py` 在 manifest 校验之后、生成 HTML 之前逐个核，
  越界直接 exit 2，不会被烧进产物。
- **它和实现分两层**。台词板写意图（"把两条线合成一条"），`images.json` 的
  `director.steps` / `evolve.steps` 写实现（哪条属性从多少补到多少，多长时间）。
  台词板里不要写属性名、时长、缓动——超过 60 字的 `change` 会被拒，因为那说明
  你在实现层写东西。

只声明不实现是这一层第二容易犯的错：台词板写了 3 拍，`images.json` 里一段补间
都没有。`run.py` 会点名（warn 不拦），因为整张表那时只是愿望清单，而它比不写
更坏——作者以为写了。

一张台词板 + 一条 `evolve` 的最小样子：

```json
// images.json
{"seg3": {"src": "images/seg3.svg",
          "evolve": {"steps": [
            {"at": 0, "target": "#left",  "to": {"opacity": 1}},
            {"at": 1, "target": "#left",  "to": {"opacity": 0.25}},
            {"at": 2, "target": "#link",  "to": {"opacity": 1}}]}}}
```

`at` 与 `on` 是同一个句序，写的时候逐条对上。两者的分工不是重复：`on` 进 manifest
给机器核，`at` 进 GSAP 给渲染器用。

## 每一条补间都要能说出它的职务

写动画之前先问一句：**这条补间在替观众做什么？** 答案只允许是下面四件之一；
说不出来的，就是该删掉的那一条。

| 职务 | 它回答的问题 | 本仓库里的例子 | 反例（别这么写） |
|---|---|---|---|
| **引导视线** | "现在该看哪里" | 焦点衰减的五维（亮度/尺寸/滚动/景深/换档）、karaoke 逐字点亮、配图入场后收拢到画幅中央 | 无休止的漂浮、呼吸、旋转——看了也不知道看哪儿 |
| **展示变化** | "什么变成了什么" | `evolve`（图内元素跟着旁白变）、`morph`、`count`、`draw`、`stage:"keep"` 接续 | 元素自己弹一下又弹回去（什么都没变） |
| **推进叙事** | "接下来发生了什么" | 段落 wipe 转场、整页画布的相机推镜、标题揭示边扫过 | 每隔几秒来一次与内容无关的"花活" |
| **操作反馈** | "我刚才那下有没有生效" | **只存在于网页预览**：按钮 hover/active、进度条拖拽跟手、预览的淡入（`preview.js`） | 把这些反馈带进成片（成片里没有"操作"这回事） |

**成片里没有操作反馈这一档**。渲染器逐帧 seek，没有鼠标也没有 hover；`preview.js`
里的一切过渡都只在人工预览分支注入，不进成片。看到成片里有"点了一下的手感"，
那就是把预览逻辑漏进时间线了。

## 三条减法

1. **一屏一件事**：每个镜头先明确"最想让人看懂的一件事"，再排先后顺序。写不清
   这一句，就先别写动画——那说明内容本身还没定焦点。
2. **删与当前重点无关的干扰**：同一屏同时动的东西**不超过三处**（焦点行、正在
   变的图内元素、一条背景）。多出来的不是"丰富"，是让眼睛没有落点。
3. **不一次铺满**：入场按"最想让人先看到的那件"排在最先，其余的往后错峰。所有
   东西在第 0 帧一起到位，等于什么都没强调。

## 官方校验命令（优先于手搓探针）

```bash
npx -y hyperframes@0.8.185 check <项目目录>          # lint + runtime + layout + motion + contrast，一个门禁
npx -y hyperframes@0.8.185 lint <项目目录>           # 只跑静态那一阶段（不开浏览器）：改 HTML 的迭代期用它
npx -y hyperframes@0.8.185 snapshot <项目目录> --at 1.0,4.0,8.0 -o snap   # 按时刻取 PNG + contact-sheet.jpg，约 12s
npx -y hyperframes@0.8.185 doctor                    # 渲染依赖体检（Chrome headless shell / ffmpeg）
```

`check` = lint + browser 五阶段（Runtime / Layout / Motion / Contrast / Craft），**迭代期跑 `lint`、定稿跑 `check`**。`lint` 默认只报 error/warning，`--verbose` 带 info 级，`--json` 给机器可读输出（errorCount / warningCount / infoCount + findings）。

**`run.py` 已经接上它，不用手敲**：出 HTML 后 `--until check` 档跑完门禁就停；不给 `--until` 时渲染前也会自动跑一次，有 error 直接 `[run][error] 官方门禁 …` 停住、不进渲染。判读口径在 `_render_backend.check_verdict`：error 一律当真，warning 只把 `GATE_NOISE_CODES`（预期噪声）之外的新 code 当真——`GATE_STRICT_CODES` 里的码即使只以 warning 出现也照样点名，Lint 有 error 时额外点一条 `browser_stages_skipped`。阶段按结构认（顶层带 `findings` 的块都判读），CLI 新增阶段不用改这张表。

本节口径对着 **v0.8.185** 实测过（竖屏 2 段 / 横屏 2 段 / 竖屏 6 段，findings 类型逐条一致）。升级后先看 `--help` 再照这里改写法，并把 `GATE_NOISE_CODES` 与下面这张噪声表一起复核。

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

### Craft 阶段：三条"模板自己会犯"的 warning

`craft` 阶段不属于上面那张噪声表，也不在 `GATE_NOISE_CODES` 里——它报的每一条都是**成片质量**问题，与"这页结构对不对"无关。当前模板会稳定触发其中三类，**它们不是噪声，是待办**：

| code | 它到底在说 | 模板为什么会有 | 怎么消 |
|---|---|---|---|
| `first_frame_empty` | 第 0 帧只有背景。**第 0 帧就是社交平台的分享封面**——观众在信息流里先看到它 | 开屏页的标题/光斑/agenda 行都从 `y`/`opacity` 的零态进场，`t=0` 那一刻什么都还没落定 | 开屏页写 `opening_layout: "canvas"` 出一张静态海报；或给开屏标题一条 `from` 起点非零的编排（苹果开场的 `filter:blur` 就是从"可见的虚焦"起，不是从空气里长出来） |
| `line_unread` | 某句字幕在画面上停留的时间**低于可读下限**（约 `0.5s + 0.3s × 词数`，片尾最后一句另要 ≥ 2s） | 句子时长来自 TTS 音频，**短句配长片**时它就那么短；模板只负责把它照实渲染 | 回到写稿：把短句并进相邻句、给这段加内容，或**拆段**让每段句子数变少而单句变长（`writing.md`「一句多长」） |
| `text_below_floor` | 某处正文渲染后小于 28px（短边的 2.6%，手机上的可读下限） | **模板自身**：agenda 行的时长标记 `.ag-dur` 在模板里是 26px（竖屏）/24px（横屏） | 改模板 `agenda.durSize`（`scripts/_template.py`）到 ≥28；或那处字确实只是装饰时打 `data-layout-ignore` |

三者的共同点：**`check` 不会因为它们失败**（都是 warning、不进退出码），但成片确实有毛病。判读顺序是"先看 `run.py` 有没有把它们当未登记码点名"——`GATE_NOISE_CODES` 之外的 code 会被以 `unregistered:<code>` 报出来，这三条都在其列，所以默认就会出声。**别把它们登记进 `GATE_NOISE_CODES` 来消停**：那等于承认"封面是黑的、字幕读不完"可以接受。

`line_unread` 的实测口径（竖屏 3 句稿）：一句 10 个词的旁白停 1.97s，下限 2.63s——差 0.66s 就是观众来不及读完的那 0.66s。片子总长由内容决定时，唯一的解是**让每句都更长**，不是让引擎跑慢点。
- 要**跨机逐帧一致**用 `render --docker`（钉住 Chromium、字体集与编码器）；本机迭代不必付。核对时间轴用 `hyperframes timeline --json`；起预览服务用 `npx hyperframes@0.8.185 preview --background`。

## 快慢、停顿、缓动按内容定

- **快慢看信息量**：一句话里只变一个元素 → 补间短（0.3~0.6s）；要观众读完一段
  结构 → 给足（1.0~1.6s）。全片不套同一个时长。
- **停顿看句读**：补间落在**句子的重音**上，而不是均匀撒开。落点用 `at`（句序
  锚）跟 manifest 走，`at_time` 只在没有旁白可锚的整页画布上用。
- **缓动看它要表达什么**：要"到位并停住"用 `expo.out` / `power3.out`（起手快、
  末端收慢，读作"推上来并站定"）；要"持续变化"用 `sine.inOut`；**弹一下（`back`
  / `elastic` / `bounce`）只在"这件事本身是意外的"时才用**——标题逐字弹、图标
  弹、数字弹，一律是机械感的来源。

### 曲线的行程预算：`expo` 不是"高级"，它要够长的路

上一条只说"要落定用 `.out`"，漏了一半：**同一条曲线配不同行程，读出来是两件事**。
以下都是从钉固 GSAP 逐点采样出来的实测值（`_ease.curve` 与 `gsap.parseEase` 逐点一致）：

| 档 | 走到行程 2% 时 | 走到 50% 时 | 走完 90% 行程用了 | 缺省用途 |
|---|---|---|---|---|
| `power4.out` | 9.6% | 96.9% | 37% 的时长 | 大行程的"重量感" |
| `power3.out` | 7.8% | 93.8% | 44% 的时长 | 标题/图块落定（缺省） |
| `power2.out` | 5.4% | 87.5% | 54% 的时长 | 小幅进场、退场 |
| `sine.out` | 3.9% | 70.7% | 中段仍在动 | 持续变化、来回摆 |
| `expo.out` | **12.9%** | **97.7%** | 23% 的时长 | 只有长行程才配它 |

读法：`expo.out` 在 0.1 秒内走完一半行程、余下 0.4 秒只走最后几个像素。它的前段
位移速度是 `power3.out` 的 1.65 倍，而末段几乎不可见。

**于是有一条硬判据**：

> 一条补间的**首帧位移**（1/fps 秒内走过的距离）必须 ≥ **4px**，否则那条曲线在这条
> 行程上等于不存在——屏幕上只有不透明度在变，观众读到的是"淡入"。

反算：0.5s 的档、30fps 下，`expo.out` 需要 **≥31px** 行程才过线；`power3.out` 只要
≥20px。模板里标题四档节奏就是按这条配的（`_template.titleEntrance.rhythm` 现为
40 / 34 / 56px，分别配 `power4.out` / `power3.out` / `power3.out`），
`tests/test_motion_design.py` 按 GSAP 实采样钉住，改模板里的数会当场红。

另两条同源的判据（同样有测试）：

- **末段要慢**：走完 90% 行程所用的时长 ≤ 全程的 50%。到 54%（`power2.out`）就已经
  是"匀速滑行"，落位没有重量。
- **位移档不过冲**：`back` / `elastic` / `bounce` 的过冲要**落在起点方向**才有意义
  （"从这里弹出来"）。标题从下面上来再弹一下是"抖"，不是"弹"；唯一的例外是缩放档
  （`scale 0.5 → 1` 配 `back.out`，"从小地方弹出来并站定"）。

## 两种进场不可混用：配图卡 vs 满幅页

同一页上有"框"和"图"两层，才有"框先落位、图在框里收拢"这套动作——它读作"镜头落到
素材上"。**满幅页（图版 / 整页画布）根本没有框**：图就是整页，给它演入场等于让一张
海报从下面浮上来，和这一页"我就是那张画"的语义相冲。所以：

| 页面 | 进场 |
|---|---|
| 槽位版式（标题区 + 配图槽 + 句子流） | 擦除 → 框落位（`opacity` + `y`）→ 图收拢（`scale 1.06 → 1`）→ 运镜 |
| 图版 / 整页画布 / 接续页 | **擦开即到位**（硬切）。要演就在 SVG 里自己演（运镜 / 自绘 / `derive`） |

判据在渲染器里只有一处（`is_full_bleed_card`），三档共用；`tests/test_motion_design.py`
的 `FullBleedEntrance` 同时钉住"满幅页没有配图补间"和"槽位页照旧有"——只钉前一半的话，
一刀切删掉所有配图入场也能通过。

## 别让物体替作者动

- **没有目的的漂浮/乱弹**：氛围层只允许**低幅度、长周期、只改亮度或极慢位移**
  的呼吸（见 `references/rendering.md`「苹果风开场」的双光斑）；幅度一旦能被"注意到"，它就在抢焦点。
- **机械的逐字蹦出**：逐字 `opacity 0→1` + 各自 y/scale/blur 是"每个字都有自己的
  出生时刻"，观众数得出字数。要表达"这句话被推上来"，用**一条连续的揭示边**
  （`.t-mk` 裁窗 + `.t-in` 只做位移 + 密集 stagger + `expo.out`），字本身不透明。
- **循环要在片长内收尾**：稳态循环（呼吸/漂移）不写 `repeat:-1`——GSAP 会把它按
  `1e10` 折算进总时长，`tl.pause()/seek()`、预览进度条、按时刻取帧三处一起坏掉
  （详见 `html_renderer._loop_repeat`）。遍数在生成期按片长算，屏幕被下一张卡
  盖住的那一刻正好是循环越过片长的那一帧。**images.json 里的 `repeat:-1` 现在
  契约层直接拒**（连同 canvas_kit 的 spec），只想先看手感走
  `run.py … --until html --allow-infinite-loop`；带它渲染会在入口被拦。
- **让多个元素"一起"动**：三条补间各写各的 `duration`/`ease`，靠手抄保证同步——
  抄错一条就脱节，而脱节在成片里只表现为"看着有点乱"。有一个**连续量**驱动它们时
  改用 `derive`（见 `references/canvas.md`「参数演绎」）：一拍只推参数，几何自己算。

## SVG 动画：两档（氛围循环 vs 时间轴同步的「导演」）

画完怎么让它动（几何怎么画在 `references/canvas.md`，本文件只管动的这一侧）。
选哪档只看一件事——**画面要不要对上某句话**：

**① 氛围循环（墙钟，默认，走 `<img>`）**：CSS `@keyframes` / SMIL 的流动虚线、呼吸光效、循环位移。按页面墙钟自走，**无法与口播时间轴同步**，逐帧渲染不保证可复现。只用于"一直在动、动到哪儿都行"的氛围层；什么都不用写，SVG 自带 CSS/SMIL 即可。`check_svg.py` 打一条 `[warn]` 提醒它不同步。

**② 时间轴同步的「导演」（净化内联，opt-in）**：要"随旁白逐句推进"的状态变化，就在 images.json 给这张 SVG 写 `director`。写了 `director` 的 SVG 会：

1. **净化后内联成活 DOM**（`scripts/_svg_sanitize.py`）：删 `<script>` / `on*` / `<foreignObject>` / 任何带 scheme 的 href / `@import`（带 scheme 的一律算外部资源，内部 `#id` 与相对路径放行）；**外链 `url()` 整颗换 `about:blank`**（`style=""` 与 `fill`/`stroke`/`filter`/`mask`/`clip-path`/`marker-*` 等呈现属性里都一样，内部 `url(#id)` 不碰）；**剥掉 SMIL 与 CSS animation/transition**——墙钟动画在逐帧 seek 里不可复现，导演档一律由补间驱动。这是唯一允许内联进交付 HTML 的情形。
2. **由 GSAP 按句驱动**：`director.steps` 每步挂在该段旁白某句起点，渲染器展开成时间线补间（属性补间，逐帧 seek 可复现）。

`director` 结构（键集封闭，写错在契约层报错）：

```json
{
  "seg4": {
    "src": "images/seg4.svg",
    "director": {
      "steps": [
        { "at": 0,   "target": "#node-a", "set": { "opacity": 1 } },
        { "at": 0.4, "target": "#curve",  "draw": true, "duration": 1.2, "ease": "power1.inOut" },
        { "at": 1,   "target": "#cam",    "to": { "scale": 2.2, "svgOrigin": "500 360" }, "duration": 1.5, "ease": "power2.inOut" },
        { "at": 2,   "target": "#bar",    "from": { "attr": { "height": 0 } }, "to": { "attr": { "height": 220 } } },
        { "at": 2,   "target": "#spark",  "morph": { "from": "M 0 60 L 40 60 L 80 60", "to": "M 0 60 L 40 10 L 80 60" }, "duration": 0.9 },
        { "at_time": 3.5, "target": "#caption", "from": { "opacity": 0 }, "to": { "opacity": 1 } },
        { "at_time": "+0.4", "target": ".row", "from": { "opacity": 0, "y": 16 }, "to": { "opacity": 1, "y": 0 }, "stagger": 0.12 },
        { "at_time": "+0.3", "target": "#bigNum", "count": { "from": 0, "to": 6.02, "decimals": 2, "suffix": "×10²³" }, "duration": 1.4 },
        { "at_time": "+0.2", "target": "#caption", "type": {}, "duration": 1.8, "ease": "none" }
      ]
    }
  }
}
```

- `at`：**该段内第几句旁白**（0 基句序，小数 = 句内偏移）。补间位置 = `sentences[⌊at⌋].start_time + (at−⌊at⌋)·duration`；句序越界（≥ 段句数）生成期报错。**整数句序是准的，小数是线性猜的**（TTS 无词级时间戳）：实测平均偏 0.2 字、最大 1.2 字（≈0.15s），唯一放大到 1s 量级的是超 ~5s 又带两三个标点的长句——写旁白时拆句。线性假设从**音频块开头**起算，要精确卡"这句第一拍"就写 `at_time: "<onset + 0.05>"`。确认某拍踩在哪个词上：`ffmpeg -hide_banner -i audio_output/sentences/s007.wav -af silencedetect=noise=-35dB:d=0.18 -f null -`，看 `silence_start` 落在第几个标点后。
- `at_time`：**与 `at` 二选一**（都写或都不写契约层报错）。从**该段音频起点**排：**数字** = 绝对秒；**相对串** `"+N"`/`"-N"` = 上一条 beat 结束后再 N 秒（首条退到段落起点；只有真占时间的步——有 `from`/`to`/`draw`，或 morph/count/type 这类负责整段的原语——才推进游标，纯 `set` 停在原地）。**它与语音没有任何绑定**（重配音/改句长/动 `--gap` 会整体错位且不报错）。**跟旁白走的节拍用 `at`；`at_time` 只留给真不跟旁白的那一拍**（相机推镜、纯节奏卡点）。
  生成期查三件事（`_timeline.beat_positions`，门禁算的落点就是真执行的那一拍）：① 落点在本页可见窗口外 → `[warn]`（窗外等于没演）；② 压在句间/段尾静音上不报（那是节奏）；③ 起点在窗内、终点越过下段起点 → `[warn]`（被切在半路，终态没出现；跨度按 `duration × 遍数` + `stagger` 算）。接续页上更要在窗内：烘焙搬的是收尾终态。
- `target`：图内**单个 id 或 class 选择器**（`#`/`.` 开头）。作用域自动收到该段容器 `#img-<sid>` 下，多段同名 id/class 不串台。**class 选择器专为 `stagger` 开**。逗号/后代/伪类不收。**target 必须真实存在**——落空不是报错而是静默没动画，由 `gen_hyperframes.py` 生成期按文件拦成 error。
- `draw`：可选布尔，描边自绘（"线自己长出来"）。目标必须是**有描边的形状**（path/line/polyline/带 stroke 的圆矩形）。收起态（`strokeDashoffset:1`）由渲染器写在**段起点**而不是这一拍——GSAP 会把补间终点值预渲染，写在这一拍会让切页第一秒整条线先亮满、到该画时反而往回缩。
- `morph`：同拓扑 path 形变（`{"from": "M…", "to": "M…"}`）。硬约束：`from`/`to` **同拓扑**（命令字母序列与每命令数值个数一致，只坐标不同）；target 必须是该 `<path>` 的 id；不能叠 `from`/`to`/`set`/`draw`。`from` 要和 SVG 里画好的 `d` 一致，否则第 0 帧跳变。
- `set` / `to` / `from`：GSAP 补间变量，`draw`/`morph`/`count` 之外至少要有一个。`set`=瞬时；`from`+`to`=fromTo；单独 `to`/`from` 各走一路。值只收 **JSON 可序列化**的（标量/数组/对象；非 CSS 属性如 `r`/`height`/`d` 走 `attr:{}`），**禁任何 `on*` 回调键**（信源不可信）。**`attr` 必须是对象**：标量/数组写法本页不报错，但跨段接续的烘焙只认对象，非对象会被当 CSS 属性写进 `style`，下一页起始画面从第一帧就是坏的。
- `count`：数字滚动（`{"to": 数, "from"?, "decimals"?, "prefix"?, "suffix"?}`）。`<text>` 数值从 `from`（缺省 0）缓动到 `to`。target 指 `<text>`；不能叠 `from`/`to`/`set`/`draw`/`morph`。**console 探针要写 `tl.seek(t, false)`**——GSAP `seek()` 默认 `suppressEvents=true` 会跳过 `onUpdate`，count/字幕高亮这类靠回调的会"看起来卡住"。
- `type`：打字机逐字揭示（只认空对象 `{}`）。运行时读 `<text>` 现成 `textContent` 作源串逐字揭示；"打什么字"由 SVG 决定，**无参数可配**，要改快慢用 step 级 `duration`/`ease`（匀速写 `"ease": "none"`）。target 指直接写文本的 `<text>`（带 `<tspan>` 排版的复杂文本会被压平）。不能叠其它补间原语，`stagger` 被契约层拒。
- `stagger`：可选，配 class 目标做一组元素错峰揭示。数字（间隔秒）或对象（GSAP 原生 `each`/`amount`/`from`/`grid`/`ease`）。只对有补间的 step 生效；`morph`/`count`/`type`/`draw` 各自负责整段，配 `stagger` 被拒。
- `duration` / `ease` / `delay`：可选。缺省取模板 `animation.director`（视觉真源单一数据源）。这三个旋钮连同 `stagger`/`repeat`/`yoyo` **只能写在 step 级**：塞进 `to`/`from`/`set` 补间变量里被契约层拒（会被覆盖掉或让门禁跨度与真实跨度分家）。
- `ease`：缓动档。**认不出的档当场报错**，照报错列出的合法族名/方向写法改即可。选档手感：缺省 `power2.out` 是"慢收"；物件**进场**用 `.in`、**退场/落定**用 `.out`、来回摆与推镜用 `.inOut`，要弹要跳用 `back`/`bounce`/`elastic`。目录外的档（`rough` 这类 EasePack 里的）只在 `--until html --allow-ease` 预览档放行，且渲染时会静默退回缺省缓动——只用来试手感，定稿换回目录里的。
- `repeat` / `yoyo`：让同一拍演好几遍。`repeat: N` = 再演 N 遍（共 N+1）；`yoyo: true` = 来回（偶数遍收在起点）。"呼吸"一条步即可。**`repeat: -1` 被契约层拒**（见下第 3 条）。规矩：
  - **跨度按遍数算**（`duration × 遍数`）：相对链游标、出窗 warn 右界、接续烘焙"最后停在哪儿"读同一个数（`_timeline.beat_span`）。
  - **只有真会"演"的步能配**（`from`/`to`/`draw`/`morph`/`count`/`type`）。纯 `set` 被拒；单写 `yoyo` 被拒（只演一遍 GSAP 不会回头）。
  - **`repeat: -1` 是 error，不是"能凑合用"**：GSAP 把无限循环按 `REPEAT_DELAY=1e10` 折算总时长，整条时间线从此是 1e10 量级——`tl.pause()/seek()`、预览进度条、跨段接续、按时刻取帧四件事一起坏，而它们正是核对成片的全部手段。**写实际遍数**（"呼吸"写 `repeat:5` 就够 3~4 秒），或走预览档 `--allow-infinite-loop`。这条也不进产物：`repeat:5` 的跨度是确定的，manifest 才核得出"演完没有"。
  - **`morph` 另外还不能配 `duration: 0`**（0 秒采不出中间态=瞬跳）；补间类步的 `duration: 0` 是"瞬时生效"，合法。
  - **跨段接续按收尾那一头搬**：见「跨段接续」的 `repeat`/`yoyo` 一条。

**觉得"动画和语音不太同步"时**：`gen_hyperframes.py … --beat-report` 逐拍列绝对秒与踩点（`in` 句内 / `gap` 句间静音 / `tail` 段尾 / `before`·`after` 窗外——窗外就是没在成片里演过的）。`in` 不等于准（句内 frac 是线性假设），怀疑踩错词用上面那条 `silencedetect` 命令定位，**别改代码**。预览另算：`preview.js` 时间线为主、音频跟随（差 >0.15s 才硬拉一次 `currentTime`），成片逐帧 seek 严格相等。

**十二个够用的"演"**（每招一行，写法见上面原语）：

- **逐句推进**：一步挂一句，`set`/`to` 把该讲到的节点点亮、卡片浮起。
- **运镜 / 相机（pan·zoom）**：把要动的场景包进 `<g id="cam">`，对它补间 `scale`/`x`/`y`（GSAP 写成 SVG `transform`，逐帧 seek 确定）。例：`{"at": 0, "target": "#cam", "to": {"scale": 2.2, "x": -300, "y": -120, "svgOrigin": "500 360"}, "duration": 1.8}`，再来一步 `scale: 1` 拉回全景。
  **焦点没写时渲染器替你钉一个**：注入本页 `#cam` 内容并集中心当 `svgOrigin`，于是运行期、跨段烘焙、出画 warn 三方同一原点；**写了 `svgOrigin` 的以你写的为准**，什么都不注入。
  **`#cam` 里只放主视觉。** 标题、结论句、公式带、图例条这类"文字带"留在 `#cam` **之外**（它们是给观众读的，不该跟着镜头跑）；要跟推镜就单独补一步与相机同步的补间。
  **放大位移离焦点越远越大**：实测 1080×1440 画布 `scale:1.16 svgOrigin:"540 660"` 把页底 y=1362 标签推到 1476（出画），页顶标题贴边。
  **机器兜住的部分**：生成期 `director_prepare` 按运镜**收尾位**把 `#cam` 内容投影回画幅，静止时整条出画或切在上下边缘外的文字打 `[warn]`（只认 `<g id="cam">`；几何在 `scripts/_cam_crop.py`）。停在 warn 不上 error——"静止位看不见"也可能是故意的。
  **机器兜不住的那件事**：只判"出画"不判"撞字"——推出去后可能正好压到 `#cam` 之外那行静止标题/旁白上。所以**留白按"推完那一帧"算，不是按 SVG 源码算**：把收尾位 `scale`/平移套一遍坐标，和静止元素基线比。
- **自绘 + 错峰（draw + stagger）**：`draw:true` 让曲线/坐标轴自己长出来；一组元素配 `stagger` 一条 step 逐个错峰揭示，不必手写递增 `at`。
- **数字滚动（count）**：`count:{from,to,decimals,suffix}` 让数值"讲到就数上去"。
- **逐字揭示（type）**：`type:{}` 把 `<text>` 现成整段文字逐字打出来；`"ease":"none"` 匀速。
- **首拍晚于段起点时，渲染器替你把起点态前置**：`_canvas_first_beat` 在段起点给每个带 `from`（或 `morph.from`）的目标补一次 `tl.set(sel, from, 段起点)`，不改你的节拍，只是把起点从第一拍移到段首。首拍本来就在 +0.2s 以内的不重复写；`to` 不参与。
- **自由场景（整页画布 × 导演）**：`layout:"canvas"` 不生成标题层/句子流层 DOM，整屏就是这张图 + 导演 = 一整个可编排舞台，什么时候出现用 `at_time` 按秒排。**第一拍要尽早落子**（0.1s 量级）：切过来的瞬间画布是空的，把底图/坐标轴/标题放最靠前那一拍（生成期按「起始帧全空」盯着）。
- **形变（morph·同拓扑）**：同一条 `<path>` 换 y 坐标长高、折线被拉直、A 态到 B 态——只要命令序列一致就能演，逐帧可复现、零依赖。
- **立体感（投影多边形 + morph，不是真 3D）**：等距或单点透视手算投影，同一物体 2–3 个姿态各写一条同拓扑 path 用 `morph` 串。不走 three.js：产物仍是可解析 SVG，各道门禁照常生效。真 3D 曲面转体做不了就别硬凹。
- **呼吸 / 脉动（repeat·yoyo）**：一条步配 `repeat`/`yoyo` 让光晕明灭几次；收尾停哪头由遍数说话（`yoyo`+偶数遍回起点）——同一数驱动游标、出窗 warn 与接续烘焙。`duration` 0.4–0.7s 的往复最像呼吸，1.5s 只会读成"还没演完"。**遍数写死**（`repeat:5` 而不是 `-1`）：无限循环换不来任何画面收益，只换掉"按时刻取帧"这条唯一的核对手段。
- **聚光（压暗对照组，不点亮主角）**：给其余那组 `to: {"opacity": 0.25}` 比给主角"亮起来"省——压暗才产生亮度差，且被压暗的留在原位不必重画。配 `stagger` 让一组 `.dim` 错峰暗下去。
- **两层视差**：背景层 `scale: 1.05`、主视觉 `scale: 1.3`，同一 `svgOrigin`，推近时两层错开速度。运镜自查只认 `#cam`，第二层不在视野里——推近幅度按**收尾位**自己套一遍坐标。
- **歪元素而不是歪相机**：要"被推翻/被审视"给元素一步 `rotation: -4`，别转相机（相机一转整页连文字带一起斜，读成"页面坏了"）。

### 跨段接续（images.json 的 `stage: "keep"`）：导演跨页接着演

一个舞台动作常一句话讲不完，而段落边界是硬的（每页各自内联、各自从 `opacity="0"` 起步）。给接续页写 `"stage": "keep"`，这一页的起点是**上一页演完的那幅画面**：

```json
{
  "seg2": {"src": "images/stage.svg", "director": {"steps": ["… 最后一拍是 #cam 推近 …"]}},
  "seg3": {"src": "images/stage.svg", "stage": "keep",
           "director": {"steps": ["… 在推近后的画面上继续演 …"]}}
}
```

**多数情况下这个字段不用你写**：生成期逐段判三条，全中就当这一页写了 `"stage": "keep"`（`_stage_carry.infer_stage_keep`）：

1. 本段 `src` 与上一段**指向同一张图**；
2. 上一段已经（因写了 `director` 而）净化内联——有可读的元素态；
3. 上一段末拍把相机停在**非原位**（推近/平移过，且收尾位可解析）。

判中会在生成日志里点出段名并说明怎么改回去。**要阻止推断**（想让这一页独立重演），任选其一即可：换开两页的 `src`，或去掉上一段末拍那条运镜——都比你手写一个字段更接近"我本来就想换画"。显式写了 `stage` 的段一律不动（包括写成非法值：契约层去报，推断不替作者改写）。

机制（口径在 `scripts/_stage_carry.py`）：生成 HTML 时按 manifest 段序把上一页所有补间的**收尾态**烘焙进这一页的内联副本——相机姿态折成 `#cam` 内新增静态 `<g data-ctv-stage transform="…">`（**不写进 `#cam` 自身 transform**），元素级 `opacity`/`attr` 落到节点起始属性，`x`/`y` 折成 `translate(dx,dy) 原串` 前缀。

**量连续性按柱子边沿定位，别拿整幅 PSNR 当判据**：页界两帧的配色本就整幅换色，PSNR 量出来的是配色差异不是没接上。

约束：

- **两页 `src` 必须指向同一张图**（不同路径无从烘焙）；由 `gen_hyperframes` 按段序判，判不过点名到段落的 error。这条同时是自动推断的第一条判据：同图 + 上一页相机停在非原位，机器就替你盖章。
- **上一页必须写了 `director`**（写了才净化内联，`<img>` 那份没有可读元素态）。接续页**自己可以不写**：一段演完、下一页只是"停在那幅画上说话"合法，steps 为空画面照样接续。
- **元素级 `transform` 只接 `x`/`y`**：平移跟原点无关，能精确折成外层平移。`scale`/`rotation`/`skew`/`xPercent` 跳过并 warn（要以元素 bbox 中心为原点折算，中心是"占位盒"估的，宁可不接也不悄悄挪位）；元素自带 `rotate`/`matrix`/`skew` 时连 `x`/`y` 也接不动，同样出声。
- **`repeat`/`yoyo` 按收尾那一头接**：`yoyo`+偶数遍回到起点，什么都不搬（这是对的）。`repeat: -1` 已进不了这一步——契约层先拒（它没有收尾态，接续也无从烘焙）。
- **接续页不要给 `<defs>` 里的元素写补间**（渐变/滤镜/marker/`<symbol>`，及被 `url(#id)`/`href="#id"` 引用的 id）：同一张 SVG 内联两份后 id 重复，图内引用按文档序只认第一份（= 上一页那幅），这一拍打在本页份上没人读。生成期按步报 error。要延续渐变/滤镜状态就在前一段写。
- **相机绕的那一点是渲染器钉的**（"页界不跳"的前提，不是可选项）：作者整页没写 `svgOrigin`/`transformOrigin` 时，渲染器给相机步注入**本页 `#cam` 内容并集中心**当 `svgOrigin`（`_cam_crop.cam_default_origin`），跨段烘焙与出画 warn 用同一个点。**写了原点的页面以你写的为准**。

接续页附带两处渲染端行为：**不走擦除**（wipe 强制归零，与整页画布同一条硬切路径），**不生成配图入场补间**（`#img-{sid}` 的淡入+上移正是"页界回弹"的本体）。
