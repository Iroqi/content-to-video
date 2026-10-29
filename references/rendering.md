# 第 4 步 · 渲染：预览、渲染与官方门禁

- **要跑/调渲染**：「HTML 预览与渲染」「渲染性能」；**版式与动画怎么落地**：「画面结构」「动画：实现要点」。
- **画面出了问题**：[troubleshooting.md](troubleshooting.md) 按症状查。**字幕跟着念**：[subtitles.md](subtitles.md)。**官方校验**：[motion.md](motion.md)「官方校验命令」；**"这一页屏幕上到底有没有东西"**：[perceptual.md](perceptual.md)——唯一在看画面的检查。

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

- **段落转场**：默认方向擦除（wipe），新卡 `clip-path` 从全遮蔽补到全覆盖（`animation.segmentWipe`），`style` 全片统一（`line` 默认 / `vertical` / `diagonal` / `circle` 71%），写错档生成期报错，旧卡不淡出。时长被句间静音钳制：`gap = 下一段 start − 本段 end`（默认 0.4s），`wipe = min(基准时长, gap)`，入场提前铺到 `win_start = s − wipe`；**卡的 `data-duration` 必须覆盖整个入场窗口**（`generate_html` 的 clips 预处理一次算齐），外层可见窗口是**半开区间**——补间结束态别压线，那一帧永不渲染，不报错，只是少一拍。卡必须不透明（自带 `.bg`/`.grid`，`z-index:-2`），透明卡做 wipe 会双影叠字。**满幅页（canvas）不参与模板转场**：`wipe` 强制归零走瞬间切，在自己的音频起点整页出现；切过去那瞬画布是空的，让导演第一拍尽早落子（全空起步生成期 warn）。`stage:"keep"` 接续页同样 `wipe=0`、不生成配图入场。
- **标题入场**：一条揭示边。字不透明、不缩放、只做位移；`.title-reveal` 的 `clip-path` 同步从 `inset(100% 0 0 0)` 开到 `inset(0%)`，两层同起点同时长同缓动（`html_renderer._reveal_tween_lines`）。四档轮换的只有行程与曲线（`titleEntrance.rhythm`：44px `power4.out` / 56px `power3.out` / 34px `power3.out` / 62px `expo.out`，按卡片序取模）；**没有缩放档**——左对齐文字块绕中心缩放时左缘一直在动。tagline 跟标题那一档进来，自带一枚裁窗。`.agenda-kicker` 开屏由苹果编排逐字演，结尾页跟标题同拍、从字距展开态整行收拢；两页都带行尾字距垫保证行宽不变。
- **配图入场（不做持续推近）**：两层——框先落位（容器层 `opacity`+`y`）、图本体收拢（`scale`/`x`/`y` 从 `settle` 收到 `{1,0,0}`）；**满幅页两层都不生成**。容器层那条 `y` 另配一条**同起点、同时长、异缓动**的 `x`（`imageEntrance.arc`）——两轴不同缓动时合成路径才是弧，同缓动时恒为直线（判据与"哪些地方不走弧"见 `references/motion.md`「位移走弧线」）。收拢 `duration` 不开放（终点由入场预算推出）。**收拢之后图不再动**：SVG 正文常贴画布边缘，推近会把 `cover` 之外的内容再放大 4%–8%、位移还会把边缘拖进槽位，边框附近的配文就看不见了。要动就给该段写 `animate` 在图里演（`references/svg-motion.md`）。收拢的 `panX/panY` 写"用掉多少盈余"的比例（`[0,1]`）——`cover` 已铺满，**scale < 1 或位移超出 `(s−1)×边长/2` 就是露底**，所以只许从 ≥1 收到 1。三类载体不收拢：`<video>`、写了 `animate` 的内联 SVG、满幅版式与 `stage:"keep"` 接续页。
- **字幕层换句**：一列滚过一条固定的边，一句一个槽高。位移只写在列容器 `#pl-{sid}` 上，**行自己一条补间都没有**；`tl.to` 一条管一句的"到来"，锚在新句开口（`start_time + onset`），时长取 `layout.<画幅>.subtitle.relay`、缓动取 `subtitle.scroll.ease`——与句子流换句同一套口径。**首句不滚**：满幅页进场是硬切，第一帧字幕必须在位。
- **CSS 不得给 GSAP 补间的元素声明 `transform`**（要位移用绝对定位 + auto 外边距）。已是生成期门禁：`gen_hyperframes` 拿 `composition.css` 里声明了 `transform` 的选择器与产物里实际补间的选择器求交，命中即 `error`（`_css_guard.py`），伪元素豁免。
- **句子流 / 焦点衰减 / 字幕时钟**：都在 `references/subtitles.md`——那里讲为什么这么插值、怎么自查；本文件只讲怎么把页面渲出来。

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

