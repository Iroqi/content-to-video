# 第 4 步 · 渲染：预览、渲染与官方门禁

- **要跑/调渲染**：「HTML 预览与渲染」「渲染性能」；**版式与动画怎么落地**：「画面结构」「动画：实现要点」。
- **画面出了问题**：[troubleshooting.md](troubleshooting.md) 按症状查。**字幕跟着念**：[subtitles.md](subtitles.md)。**"这一页屏幕上到底有没有东西"**：[perceptual.md](perceptual.md)——唯一在看画面的检查。

## 主题

整期一个主题，只在 `run.py` / `gen_hyperframes.py` 选，`dark` 默认。

## 画面结构

```text
标题区（1 行优先，最多 2 行）
↓
固定画布（竖屏 4:3 居中；横屏左文右图双栏）
↓
verse 句子流（竖屏钉底；横屏排在左文字栏末、随栏垂直居中）
```

- **portrait**：标题区定高、两行钳制，4:3 画布（980×735）居中，verse 钉底；标题区压到画布顶边报 `[template] 竖屏标题区放不进`。**landscape**：左栏固定 613px、右侧槽位 1067×800；**没有行数钳制**，守卫只降字号（超 16 字降到 54px、超 22 字降到 48px，超 16 字打 `[warn]`）。
- **整页画布（`layout: "canvas"`）**：三层塌成两层——配图拉满整幅，标题层与句子流层**不生成**；页底那一条是**字幕层**（`subtitle:true`，默认开，写 false 才关）。**不要自画满幅底板**，文字对比度是画布作者的责任——`Contrast` 门禁看不见 SVG 里的字，生成期按文件拦（见 `references/canvas.md`）。

  ```text
  满幅主视觉（作者画的整页）
  ↓
  一句字幕（页底 + 底部 scrim，随旁白换句；subtitle:false 时没有这一层）
  ```

  换句由时间线驱动（`_plate_scroll_lines`）：句子排成一列、每句占一个**等高槽**，换句时整列上移一个槽高，字滑过 `.plate-clip` 那条固定的边；运行时（`plateSync`）只决定哪一句吃 accent。槽高（`(钳制行数 + 槽隙) × 行高 × 字号`）既是滚动一步的距离、也是窗口高度，真源只有 `_plate_slot_px` 一份。字幕超过 `captionMaxLines`（2 行）判 `error` 而不是缩字号。几何真源是 `_template.py` 顶层 `plate.<画幅>`，CSS 只放选择器。写稿与留白约束见 `references/canvas.md`「字幕层」。

opening / closing 默认是不配图的**纯文字 agenda 卡**。换整页海报用顶层 `opening_layout` / `closing_layout: "canvas"`。字段与行数上限**以 `references/writing.md` 为准**。

### 苹果风开场（开屏 agenda 卡默认强绑定）

开屏页为 agenda 版式时默认强绑定苹果风整页编排，无契约键（`opening_animation` 已移除，写过旧键会报错）。参数真源 `animation.opening.apple`。

- 整支舞按**可见窗口**（`[擦除起点, 被下一页盖住]`）等比归一：收尾时刻由标题字数与行数共同决定；装不下按 `可见窗口 ÷ 收尾时刻` 压缩（地板 0.5）并打 `[warn]`。光晕呼吸与漂移的时长不参与压缩，压缩只落在淡入时长与起点；锚点是**擦除起点 `win_start`**，不是段起点。

### 氛围与底色

- 每段 accent 派生氛围光、槽位外发光、tagline 刻度条与进度条辉光（一律经 `color-mix`）——**选 accent 时注意它染整帧**；`bare-media` 的 SVG 槽位不吃外发光与描边。**背景网格是中性灰、不带色相**，浓度按**成片对比度**定在页底 1.20:1——改这个值请量成片，别信浏览器预览。**句子流暗垫（仅竖屏）**：`.verse::before` 垫一层渐隐到底色的片子，横屏不垫（整块宽垫垂中会读成悬浮的板）；垫层要求 `.verse` 是层叠上下文（`isolation`），否则掉到 `.bg/.grid` 底下。

## 配图

画布尺寸与配图规格见 `references/imagery.md`；素材一律 `cover` 铺满，语义只能靠人眼看。入场见下节「动画：实现要点」。

## 动画：实现要点

参数全部来自 `_template.py` 的 `animation`。**该不该动、动多快**看 `references/motion.md`「四职务」；这里只讲怎么落地。

- **段落转场**：默认方向擦除（wipe），新卡 `clip-path` 从全遮蔽补到全覆盖（`animation.segmentWipe`），`style` 全片统一（`line` 默认 / `vertical` / `diagonal` / `circle` 71%），写错档生成期报错，旧卡不淡出。时长被句间静音钳制：`gap = 下一段 start − 本段 end`（默认 0.4s），`wipe = min(基准时长, gap)`，入场提前铺到 `win_start = s − wipe`；**卡的 `data-duration` 必须覆盖整个入场窗口**（`generate_html` 的 clips 预处理一次算齐），外层可见窗口是**半开区间**——补间结束态别压线，那一帧永不渲染，不报错，只是少一拍。卡必须不透明（自带 `.bg`/`.grid`，`z-index:-2`），透明卡做 wipe 会双影叠字。**满幅页（canvas）不参与模板转场**：`wipe` 强制归零走瞬间切，在自己的音频起点整页出现；切过去那瞬画布是空的，让导演第一拍尽早落子（全空起步生成期 warn）。接续页（满幅页、与上一页同图、上一页有编排）同样 `wipe=0`、不生成配图入场。
- **标题入场**：一条揭示边。字不透明、不缩放、只做位移；`.title-reveal` 的 `clip-path` 同步从 `inset(100% 0 0 0)` 开到 `inset(0%)`，两层同起点同时长同缓动（`html_renderer._reveal_tween_lines`）。四档轮换的只有行程与曲线（`titleEntrance.rhythm`：44px `power4.out` / 56px `power3.out` / 34px `power3.out` / 62px `expo.out`，按卡片序取模）；**没有缩放档**——左对齐文字块绕中心缩放时左缘一直在动。tagline 跟标题那一档进来，自带一枚裁窗。`.agenda-kicker` 开屏由苹果编排逐字演，结尾页跟标题同拍、从字距展开态整行收拢；两页都带行尾字距垫保证行宽不变。
- **配图入场（不做持续推近）**：两层——框先落位（容器层 `opacity`+`y`）、图本体收拢（`scale`/`x`/`y` 从 `settle` 收到 `{1,0,0}`）；**满幅页两层都不生成**。容器层那条 `y` 另配一条**同起点、同时长、异缓动**的 `x`（`imageEntrance.arc`）——两轴不同缓动时合成路径才是弧，同缓动时恒为直线（判据与"哪些地方不走弧"见 `references/motion.md`「位移走弧线」）。收拢 `duration` 不开放（终点由入场预算推出）。**收拢之后图不再动**：SVG 正文常贴画布边缘，推近会把 `cover` 之外的内容再放大 4%–8%、位移还会把边缘拖进槽位，边框附近的配文就看不见了。要动就给该段写 `animate` 在图里演（`references/motion.md`「六种够用的演法」）。收拢的 `panX/panY` 写"用掉多少盈余"的比例（`[0,1]`）——`cover` 已铺满，**scale < 1 或位移超出 `(s−1)×边长/2` 就是露底**，所以只许从 ≥1 收到 1。三类载体不收拢：`<video>`、写了 `animate` 的内联 SVG、满幅版式与接续页。
- **字幕层换句**：一列滚过一条固定的边，一句一个槽高。位移只写在列容器 `#pl-{sid}` 上，**行自己一条补间都没有**；`tl.to` 一条管一句的"到来"，锚在新句开口（`start_time + onset`），时长取 `layout.<画幅>.subtitle.relay`、缓动取 `subtitle.scroll.ease`——与句子流换句同一套口径。**首句不滚**：满幅页进场是硬切，第一帧字幕必须在位。
- **CSS 不得给 GSAP 补间的元素声明 `transform`**（要位移用绝对定位 + auto 外边距）。已是生成期门禁：`gen_hyperframes` 拿 `composition.css` 里声明了 `transform` 的选择器与产物里实际补间的选择器求交，命中即 `error`（`_css_guard.py`），伪元素豁免。
- **句子流 / 焦点衰减 / 字幕时钟**：都在 `references/subtitles.md`——那里讲为什么这么插值、怎么自查；本文件只讲怎么把页面渲出来。

### 三条换版式时会坏的（都不需要你写）

- **换句是滚动，不是换灯**：成片里次数最多的动作不是转场也不是入场，是换句（一句 2 秒，三分钟两百次）。它若是 `visibility` 的开关，全片其它每一处都有编排、唯独字幕在闪。句子流与字幕层两个实现共用同一份滚动口径（时长 `subtitle.relay`、缓动 `subtitle.scroll.ease`、锚点新句开口，见上一节最后一条）。**首句不滚**（满幅页进场是硬切，第一帧字幕必须在位）；字幕层按**等高槽**滚而非实际行高——槽等高短句长句的字才停在同一高度，代价是短句滚过时窗口里有段留白，那是两句之间的呼吸，比上下跳好看。
- **标题入场是「揭示边」，不是整块浮起**：判据只有一条——**裁窗与字的起点、时长、缓动必须一致**。窗比字慢，收尾只剩一根线在爬；窗比字快，长标题先露半截；只做裁窗是擦除，只做位移是滑动——都不是"字被一条边推上来"。开屏标题是同一招的逐字版，全片因此只有一种标题入场。
- **两种进场不可混用**：槽位版式有"框"和"图"两层，才谈得上"框先落位、图在框里收拢"；满幅页没有框，给它演入场等于让整页从下面浮上来。判据只有一处（`is_full_bleed_card`）。

  | 页面 | 进场 |
  |---|---|
  | 槽位版式 | 擦除 → 框落位 → 图收拢（`scale 1.06→1`） |
  | 整页画布（含接续页） | **擦开即到位**（硬切），要演就在 SVG 里自己演 |

- **别让物体替作者动**，四个常见犯法：目的不明的漂浮（氛围层只许低幅度、长周期、只改亮度或极慢位移）；机械的逐字蹦出（观众数得出字数）；**稳态循环写 `repeat:-1`**（GSAP 按 `1e10` 折算，`seek()`、预览进度条、按时刻取帧一起坏——遍数由生成期按片长算，契约层直接拒，只想试手感走 `--until html --allow-infinite-loop`）；**手抄同步**（三条补间各写 `duration` 求"一起动"，抄错一条就脱节，而脱节只表现为"看着有点乱"）。

## HTML 预览与渲染

### 1. 生成 HTML

```bash
python scripts/gen_hyperframes.py -m audio_output/timing_manifest.json \
  -o hf-project/index.html --images hf-project/images.json --fps 24
```

`-o` 与 `--images` 的相对路径都相对**当前工作目录**；images.json 里各条 `src` 才相对 `-o` 所在目录。画幅与主题在这一步烧进 HTML。生成目录应包含 `index.html` / `preview.js` / `vendor/gsap.min.js` / `audio/` / `images/`。音频无效时回落到上次暂存的 `audio/combined.wav` 并打 `[warn]`，偏差超 `max(1s, 2%)` 拒跑。

### 2. 浏览器预览

见 SKILL.md 第 4 步。`preview.js` 只在真实浏览器注入 UI；headless 调试加 `?preview` 强制注入。

### 3. 正式渲染

直接跑 `run.py`（不带 `--until`）：它会重新生成 HTML 再 render。

落盘后还有一道**成片时长对账**：量实际时长与 `total_duration` 比，容差 `max(1s, 2%)`，短于容差**退出码 4** 拦下交付，超出或量不到只 warn。结果进 `production_report.json` 的 `video` 字段。

`run.py` 默认 `--no-best-effort`：CLI 的 `--best-effort` 默认开，能出无声成片，与本技能红线冲突。只想看节奏、不在乎缺帧才加 `--allow-best-effort`（调试档，别交付）。逃生口 `--draft` 一次全开（占位 / ease / 缺 src 降级 / 各类降 warn / best-effort），report 记 `draft: true`。

## 渲染性能：什么时候该动这几个参数

默认值见 `run.py --help-all`（`--fps 24`、`--workers 4`、`--quality looks`）。`--set 键=值`（可重复）改一个模板视觉参数——**只在本次进程生效**，定下来的值写回 `scripts/_template.py`。

- **最优组合别猜，让机器量**：`npx -y hyperframes@0.8.185 benchmark <项目> --runs 3 --json`。下面的经验值换机器、换稿件规模就该重跑。
- `fps`：抓帧耗时与帧数线性，唯一一阶杠杆。快速看画面用 `--fps 12 --quality draft`。
- `workers`：`run.py` **恒显式传**，拿不到 CLI 的自动校准。8 之前有效、之后饱和；迭代片 `--workers 8`；V8 堆崩溃降到 2。偏慢时先在 `out.render.log` 找 `Parallel capture timed out`。
- `quality`：五档透传，底层只有三档——`looks` = standard + CRF 16（默认），**`delivery` 就是 high**；`draft` 省约 15%。
- **固定开销**：每调用一次约白付 8.5s，冷跑多约 6s。先 `--until html` 看够再整片渲一次。
- **低配机器**：`--low-memory-mode` 把 worker 钉成 1（总内存 ≤8GB 自判），与 `--workers` 冲突时 CLI 赢。**容器读的是宿主机总内存**，小限额容器要显式 `PRODUCER_LOW_MEMORY_MODE=true`（CLI 的环境变量，取值是 `true`/`false`，写 `1` 不认）。Chrome 超时另有 `--browser-timeout` / `--protocol-timeout`（`run.py` 不透传）。

## 官方校验命令

`motion.md` 是判断题（该不该动、动多快），这里是执行题。**校验只证明没坏，证明不了该不该动。**

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
