# 第 3 步 · 动效判据

`references/rendering.md` 讲怎么实现（转场、入场、运镜、焦点衰减的落地细节）；本文件回答**该不该动、动多快**，以及**图里那个动怎么算出来**（`evolve` / `derive`）。

## 先写台词板，再写补间

动笔前先把**这一段在第几句旁白时画面变成什么**写成表（`segments_source.json` 的 `beats`）：

```json
{"id": "seg3", "title": "两个状态",
 "text": "先看左边这个状态。然后它变成了右边这个状态。合起来就是答案。",
 "beats": ["点亮左侧的状态", "把左侧压暗、点亮右侧", "两条并成一条"]}
```

简写时**下标即句序**；跳句用全写 `[{"on": 0, "change": "…"}, {"on": 2, "change": "…"}]`——两种写法不可混用（"第几拍"会有两个来源，契约层直接拒）。

- `on` 必填：说不上来第几句变，说明这一拍没想清楚。这个病没有门禁看得见——`check` 只证明能渲，时长契约只证明演完，`--perceptual` 只知道画面在动；句序还会被重分句打散，`run.py` 生成 HTML 前逐条核越界，exit 2。
- 台词板写意图，`images.json` 的 `director.steps` / `evolve.steps` 写实现：台词板里写属性名、时长、缓动会被拒（`change` 超 60 字即视为在实现层写东西），只声明不实现会被 `run.py` 点名 warn——那时它只是愿望清单，比不写更坏。

## 四职务：每条补间都要能说出它在替观众做什么

只说得出"好看"的那条，就是该删的那条（成片逐帧 seek，没有鼠标也没有 hover——看到"点了一下的手感"就是预览逻辑漏进时间线了）。

| 职务 | 回答 | 例子 | 反例 |
|---|---|---|---|
| 引导视线 | 现在该看哪里 | 焦点衰减五维、karaoke、入场后收拢 | 无休止漂浮/呼吸/旋转 |
| 展示变化 | 什么变成了什么 | `evolve`、`morph`、`count`、`draw`、`stage:"keep"` | 弹一下又弹回去 |
| 推进叙事 | 接下来发生了什么 | 段落 wipe、整页画布推镜、标题揭示边 | 与内容无关的花活 |
| 操作反馈 | 我刚那下生效没有 | **只在网页预览**（`preview.js`） | 把这份手感带进成片 |

## 三条减法

1. **一屏一件事**：先定"最想让人看懂的一件事"再排先后；说不清就说明内容还没定焦点。
2. **删掉与当前重点无关的干扰**：同屏同时动的不超过三处——多出来的不是丰富，是让眼睛没有落点。
3. **不一次铺满**：最想让人先看到的排最先，其余错峰；第 0 帧全部到位等于什么都没强调。

## 快慢、缓动按内容定

- **快慢看信息量**：只变一个元素 0.3~0.6s；要读完一段结构 1.0~1.6s。全片不套同一个时长。
- **落点看句读**：补间落在重音上，用 `at`（句序锚）跟 manifest 走；`at_time` 只留给不跟旁白的那一拍。
- **缓动看语义**：到位并停住用 `.out`；持续变化用 `sine.inOut`；`back`/`elastic`/`bounce` **只在"这件事本身是意外的"时**用——标题逐字弹、图标弹、数字弹一律是机械感的来源。

### 曲线的行程预算：`expo` 不是"高级"，它要够长的路

同一条曲线配不同行程读出来是两件事。下表是钉固 GSAP 的逐点实采（`_ease.curve`）。两条同源判据：**末段要慢**（走完 90% 所需 ≤ 全程 50%，54% 已读成匀速滑行）、**过冲要落在起点方向**（从下面上来再弹一下是"抖"不是"弹"）。

| 档 | 行程 2% 时 | 行程 50% 时 | 走完 90% 用了 | 用途 |
|---|---|---|---|---|
| `power4.out` | 9.6% | 96.9% | 37% 时长 | 大行程的重量感 |
| `power3.out` | 7.8% | 93.8% | 44% 时长 | 标题/图块落定（缺省） |
| `power2.out` | 5.4% | 87.5% | 54% 时长 | 小幅进出 |
| `sine.out` | 3.9% | 70.7% | 中段仍在动 | 持续变化 |
| `expo.out` | **12.9%** | **97.7%** | 23% 时长 | 只有长行程配它 |

> **硬判据**：补间的首帧位移（1/fps 秒内走过的距离）≥ **4px**，否则那条曲线在这条行程上等于不存在，观众只读到"淡入"。反算：0.5s@30fps，`expo.out` 要 ≥31px、`power3.out` 要 ≥20px。三条都由 `tests/test_motion_design.py` 按 GSAP 实采样钉住。

### 标题入场是「揭示边」，不是整块浮起

判据只有一条：**裁窗与字的起点、时长、缓动必须一致**。窗比字慢，收尾只剩一根线在爬；窗比字快，长标题先露半截；只做裁窗是擦除，只做位移是滑动——都不是"字被一条边推上来"。实现见 `references/rendering.md`「动画：实现要点」；开屏标题是同一招的逐字版，全片因此只有一种标题入场。

### 换句是滚动，不是换灯

成片里次数最多的动作不是转场也不是入场，是**换句**（图版页一句 2 秒，三分钟两百次）。它若是 `visibility` 的开关，全片其它每一处都有编排、唯独字幕在闪。

同一条边在两种版式上各落一次：句子流靠焦点五维 + 整列滚动（位移逐帧插值，滚动距离按实际行高），图版靠**整列滚过一个固定窗口**（位移恒等于槽高）。两条共用同一份口径——时长取 `subtitle.relay`、缓动取 `subtitle.scroll.ease`、锚点取新句开口。

判据三条：

1. **位移只写在列上**：行自己一条补间都没有。两句若各有各的边（各挂一条揭示边），交叠期两条位移在画面上互相穿过，读到的是"两句独立的字在飞"，不是"一条字幕在走"。
2. **首句不滚**：满幅页进场是硬切，第一帧字幕必须在位（同一条"满幅页第一帧必须完成态"的另一半）。
3. **边是容器的边，不需要 clip-path**：窗口固定、字滑过去。图版用 `overflow` 裁而不是裁窗——裁窗要在元素自身平面内先裁后变换，边跟着字一起动，就不是"固定的边"了。

为什么图版按**等高槽**滚而不按句子实际行高（句子流是按实际行高的）：图版字幕钉在页底，槽等高才能保证短句与长句的字停在同一个高度；按实际行高给槽，短句的字就停得比长句高，页底那行会随句长上下跳。代价是短句滚动时窗口里会经过一段空带（短句上方那一行的留白 + 槽隙）——那是两句之间的呼吸，比上下跳好看。

## 两种进场不可混用

槽位版式有"框"和"图"两层，才谈得上"框先落位、图在框里收拢"（读作镜头落到素材上）；满幅页没有框，给海报演入场等于让整页从下面浮上来。

| 页面 | 进场 |
|---|---|
| 槽位版式 | 擦除 → 框落位 → 图收拢（`scale 1.06→1`）→ 运镜 |
| 图版 / 整页画布 / 接续页 | **擦开即到位**（硬切），要演就在 SVG 里自己演 |

判据只有一处（`is_full_bleed_card`）；`FullBleedEntrance` 同时钉住"满幅页没有配图补间"与"槽位页照旧有"——只钉前一半，删掉全部入场也能通过。

## 别让物体替作者动

- **没有目的的漂浮**：氛围层只许低幅度、长周期、只改亮度或极慢位移的呼吸。
- **机械的逐字蹦出**：每个字都有自己的出生时刻，观众数得出字数。要表达"被推上来"就用一条连续揭示边（见上节）。
- **循环要在片长内收尾**：稳态循环不写 `repeat:-1`——GSAP 按 `1e10` 折算，`seek()`、预览进度条、按时刻取帧一起坏。遍数由生成期按片长算；契约层直接拒，只想试手感走 `--until html --allow-infinite-loop`。
- **别手抄同步**：三条补间各写 `duration` 保证"一起动"，抄错一条就脱节，而脱节只表现为"看着有点乱"。有连续量驱动时改用 `derive`（见 `references/director.md`「参数演绎」）。

## SVG 动画：两档

选哪档只看一件事——**画面要不要对上某句话**。

| 档 | 怎么走 | 与口播同步 | 用在哪 |
|---|---|---|---|
| 氛围循环 | CSS / SMIL 自走，`<img>` 载入 | 不能，逐帧也不可复现 | 只给氛围层（`check_svg.py` 会 warn） |
| 导演 | 净化内联 + GSAP 按句驱动 | 能，逐帧可复现 | 要画面跟上某句话的一切 |

**① 氛围循环**（墙钟，走 `<img>`）：CSS/`@keyframes`/SMIL 自走，**无法与口播同步**、逐帧不可复现。只用于氛围层，`check_svg.py` 会 warn。

**② 导演**（净化内联，opt-in）：写 `director` 的 SVG 先经 `_svg_sanitize` 净化（删 `<script>`/`on*`/`<foreignObject>`/带 scheme 的 href、外链 `url()` 换 `about:blank`、剥掉 SMIL 与 CSS animation），再由 GSAP 按句驱动——唯一允许内联进交付 HTML 的情形。

**怎么写那几条 step**：键表、六个够用的"演"、`evolve`、`derive` 全在 `references/director.md`——三张编排共用同一套锚点语义，写一次就够。样子长这样：

```json
{"seg4": {"src": "images/seg4.svg", "director": {"steps": [
  {"at": 0, "target": "#node-a", "set": {"opacity": 1}},
  {"at": 0.4, "target": "#curve", "draw": true, "duration": 1.2},
  {"at": 1, "target": "#cam", "to": {"scale": 2.2, "svgOrigin": "500 360"}, "duration": 1.5},
  {"at": 2, "target": ".row", "from": {"opacity": 0, "y": 16}, "to": {"opacity": 1, "y": 0}, "stagger": 0.12}
]}}}
```

### 跨段接续（`stage: "keep"`）

一个动作常一句话讲不完，而段落边界是硬的。给接续页写 `"stage": "keep"`，它的起点就是上一页演完的那幅画面。

**多数情况不用你写**（`_stage_carry.infer_stage_keep`）：同图 + 上一段有 `director` + 上一段末拍把相机停在非原位，判中会点名并说明怎么改回去；想让它独立重演，换开 `src` 或去掉末拍运镜。

机制：生成 HTML 时把上一页补间的收尾态烘焙进本页内联副本——相机姿态折成 `#cam` 内静态 `<g data-ctv-stage>`，元素级 `opacity`/`attr` 落成起始属性，`x`/`y` 折成 `translate` 前缀。**量连续性看柱子边沿，别拿整幅 PSNR**（页界本就整幅换色）。约束：两页同图、上一页必须有 `director`（接续页自己可不写）；元素级 `transform` 只接 `x`/`y`（`scale`/`rotation` 跳过并 warn）；`repeat`/`yoyo` 按收尾那头接；**接续页别给 `<defs>` 里的元素写补间**（id 重复，图内引用只认第一份）；相机原点由渲染器钉。接续页不走擦除、不生成配图入场（那正是"页界回弹"的本体）。

## 官方校验命令

前面是判断题，这里是执行题。**校验只证明没坏，证明不了该不该动。**

```bash
npx -y hyperframes@0.8.185 check <项目>        # lint + 五个浏览器阶段，一个门禁
npx -y hyperframes@0.8.185 lint <项目>         # 只跑静态阶段（不开浏览器），改 HTML 时用
npx -y hyperframes@0.8.185 snapshot <项目> --at 1,4,8 -o snap   # 取帧 + contact sheet
npx -y hyperframes@0.8.185 doctor              # 依赖体检（Chrome / ffmpeg）
```

`run.py` 已接上：`--until check` 跑到门禁为止；不给 `--until` 时渲染前自动跑，有 error 就停。

口径对着 **v0.8.185** 实测，判读在 `_render_backend.check_verdict`：error 一律当真；warning 只有 `GATE_NOISE_CODES` 之外的 code 当真（`GATE_STRICT_CODES` 的码即使只是 warning 也点名）；Lint 有 error 时点一条 `browser_stages_skipped`，且**浏览器五阶段整个不跑**——那五项显示 0 不是通过，是失明。阶段按结构认，CLI 加阶段不用改表。升级后先看 `--help`，并把噪声表与下表一起复核。

- 字体栈出现未声明 `@font-face` 的家族判 **error**（家族名要用映射表认的写法，认 `"SF Mono"`）。
- 干净基线：Lint 0 error、Runtime/Motion/Contrast 全 0、Layout 0 error。warning 条数随稿子规模走，只看类型：
  - 噪声（真源是 `GATE_NOISE_CODES`，`tests/check_docs.py` 逐码对账）：`gsap_callback_dom_measurement`、`timeline_track_too_dense`、`composition_file_too_large`、`nested_structure_needs_subcomposition`（每张卡一条）、`css_transition_used`、`overlapping_gsap_tweens`、`gsap_repeated_fromto_without_baseline`、`gsap_infinite_repeat`（开屏页）、`gsap_timeline_set_initial_hide`。
  - 误报：`negative_z_index`（恒 5 条 = CSS 里负 z-index 规则数；lint 静态扫 CSS，看不见已加的 `isolation`）。
  - 条数≈`5 + 卡片数 + 6`，别拿任何样本当固定值。`container_overflow` 不该出现（配图容器带 `data-layout-allow-overflow`），出现就是标记被拿掉或槽位塞了比例不对的 SVG。
- `text_occluded` 是"滚出窗口的行被静态判定"：三个豁免属性的继承规则不同——`data-layout-allow-occlusion` / `data-layout-allow-overlap` 只认元素自己身上，`data-layout-allow-overflow` 继承到子树（打在 `.verse` 一处）；同一属性 Layout 阶段用 `hasAttribute`、Contrast 阶段走 `closest()`。**调 `proseCoverageFloor` 优于层层打标**。横屏段落标题在入场附近会多出 info 级同码，`snapshot --at` 看一眼正常就忽略。
- 踩上不报错的几处：`validate`/`inspect`/`layout` 是废弃别名，用 `check`；`doctor --json` **恒退出 0**，当门禁必须读 `.ok`；要 warning 也阻断用 `check --strict`。
- 跨机逐帧一致用 `render --docker`；核对时间轴 `timeline --json`；预览服务 `preview --background`。`clip_media_fit`（音频短于 `data-duration`）与 `clipped_text` 不在噪声之列，都要当真。

### Craft 阶段：三条"模板自己会犯"的 warning

`craft` 不在噪声表里，它报的都是**成片质量**问题。**这三条是待办，不是噪声**：

| code | 它在说什么 | 模板为什么会有 | 怎么消 |
|---|---|---|---|
| `first_frame_empty` | 第 0 帧只有背景。**它就是社交平台的分享封面** | 开屏的标题/光斑/agenda 都从零态进场，`t=0` 什么都还没落定 | 开屏写 `opening_layout: "canvas"` 出静态海报；或给标题一条 `from` 起点非零的编排 |
| `line_unread` | 某句停留短于可读下限（约 `0.5s + 0.3s × 词数`，片尾末句另要 ≥2s） | 句子时长来自 TTS，短句配长片时它就是那么短 | 回写稿：短句并进相邻句，或拆段让单句变长（`writing.md`「一句多长」） |
| `text_below_floor` | 某处正文 < 28px（短边 2.6%，手机可读下限） | **模板自身**：`.ag-dur` 是 26px（竖屏）/24px（横屏） | 改 `_template.py` 的 `agenda.durSize` 到 ≥28，或该处只是装饰时打 `data-layout-ignore` |

`check` 不会因为它们失败（warning 不进退出码），但成片确实有毛病。它们会被 `run.py` 以 `unregistered:<code>` 点名。**别登记进 `GATE_NOISE_CODES` 来消停**——那等于承认"封面是黑的、字幕读不完"可以接受。
