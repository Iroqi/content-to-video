# 第 4 步 · 渲染：预览、输出与官方门禁

版式、动画落地、字幕怎么动、画面到底有没有东西 → `look.md`（第 4 步的"长什么样"那一半）。本文件只答另一半：**怎么把它变成一个文件、跑多快、用什么验**。

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
