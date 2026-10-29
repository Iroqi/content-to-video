# 第 3 步 · SVG 编排（`animate`）

本文件讲**怎么写那几条 step**。该不该动、动多快、缓动怎么选 → `references/motion.md`；图本身怎么画（几何、对比度、字号）→ `references/canvas.md`。

## 一条判据：写字还是不写

`images.json` 里某一段的 `animate` 就是这一页的编排。**不写 = 静图**：SVG 按 `<img>` 载入，内部脚本不执行，画面在整段里不动。写了就净化内联成活 DOM（下文称**导演档**），这是纯文字说明里的习惯叫法，不是另一个字段。

| 画面的变化要 | 写法 |
|---|---|
| 对上某句旁白（第 3 句说到时才变色） | 写 `animate`：净化内联成活 DOM，GSAP 按 `timing_manifest` 逐句驱动，逐帧可复现 |
| 自己走（CSS `@keyframes` / SMIL 氛围循环） | 不写 `animate`；墙钟动画与口播无关、逐帧不可复现，`check_svg.py` 会 warn，成片里不采用 |
| 完全不动 | 不写 `animate`。不需要为"这一页要静"另加开关——静就是没写 |

同一张图里"先静、后动"也只是一条 step 的事：把第一拍放在要动的那句话上。

## 契约

```json
{"seg4": {"src": "images/seg4.svg", "animate": {
  "steps": [
    {"at": 0, "target": "#node-a", "set": {"opacity": 1}},
    {"at": 0.4, "target": "#curve", "draw": true, "duration": 1.2},
    {"at": 1, "target": "#cam", "to": {"scale": 2.2, "svgOrigin": "500 360"}, "duration": 1.5},
    {"at": 2, "target": ".row", "from": {"opacity": 0, "y": 16},
     "to": {"opacity": 1, "y": 0}, "stagger": 0.12}
  ]
}}}
```

块级只认三个键：`steps`（必填、非空）、`params` 与 `bind`（一起写或一起省，写法见下面「参数演绎」）。写在 `panes[i]` 里的分格编排同一条契约，每格各写各的。

上面的例子里 `#cam` 那一拍是运镜，只属于满幅版式（`layout:"canvas"` / `"plate"`）；槽位页照抄会在生成期被拦（见「镜头：谁能调机位」）。

`steps` 里每一拍二选一：

| 这一拍要 | 写法 | 需要 |
|---|---|---|
| 改某个元素 | `target` + 补间变量（`to`/`from`/`set`/`draw`/`morph`/`count`/`type`） | 目标元素的 `#id` 或 `.class` |
| 推一个参数 | `param` + 数值 `to` | 块级 `params` + `bind` |

### 拍级键

| 键 | 规则 |
|---|---|
| `at` | 该段第几句（0 基，小数 = 句内偏移）。整数准、小数是按字长线性猜的（无词级时间戳，实测最大偏 1.2 字 ≈ 0.15s）；5s 以上的长句会放大到 1s 量级——拆句。要精确卡首拍用 `at_time` |
| `at_time` | 与 `at` 二选一（都写或都不写都报错）。数字 = 段内绝对秒；`"+0.5"` = 上一条之后 0.5 秒。**与语音无绑定**，改句长或动 `--gap` 会整体错位且不报错——只留给不跟旁白的拍 |
| `target` | 图内单个 `#id` 或 `.class`（作用域收到 `#img-<sid>`），不收逗号、后代、伪类。落空不会报错、只会静默没动画，所以生成期按文件拦成 error |
| `draw` | 描边自绘（目标必须带 `stroke`）。收起态要写在段起点，否则切页第一秒线先亮满再缩回去 |
| `morph` | 同拓扑 path 形变：命令序列与每命令数值个数一致，`from` 必须等于 SVG 里的 `d` |
| `set` / `to` / `from` | GSAP 补间变量，三者至少一个。只收 JSON 可序列化值；非 CSS 属性走 `attr:{}`（**必须是对象**，标量会在跨段烘焙时被当 CSS 写进 `style`）。禁一切 `on*` 回调 |
| `count` | 数字滚动（`to`/`from`/`decimals`/`prefix`/`suffix`），target 指 `<text>` |
| `type` | 逐字揭示，只认 `{}`，读 `<text>` 现成文本 |
| `stagger` | 配 `.class` 目标错峰揭示；只对有补间的拍生效 |
| `duration` / `ease` / `delay` | 缺省取模板 `animation.animate`。**只能写拍级**，塞进补间变量表会被拒 |
| `repeat` / `yoyo` | `repeat:N` = 再演 N 遍（`-1` 是 error：循环要在片长内收尾，遍数由生成期按片长算）；跨度按 `duration × 遍数` |

`param` 拍的可用键更窄：`at` / `at_time` / `param` / `from` / `to` / `duration` / `ease` / `delay`。

## 镜头：谁能调机位

只有**版面归作者的满幅版式**（`layout:"canvas"` / `layout:"plate"`）能写相机键（`svgOrigin` / `scale` / `rotation` / `x` / `y` 等）；槽位页与分格页的构图归模板，图内再推一次是两层意图打架，契约层直接 error。

要动的场景包进 `<g id="cam">`，补间它的 `scale` / `x` / `y`。未写 `svgOrigin` 时渲染器钉本页 `#cam` 并集的中心，运行期、接续烘焙、出画告警用的是同一个原点。`#cam` 里只放主视觉——标题与结论句留在外面，跟着推会把字推出画。

留白按**推完那一帧**算：生成期按收尾位把内容投影回画幅，出画打 warn（不上 error），但不判"撞字"。两层视差同法：背景 `scale:1.05`、主视觉 `scale:1.3`，同 `svgOrigin`。

## 六种够用的演法

- **运镜**：见上一节。整段只有一个连续动作时，一拍到底比拆成三拍稳。
- **自绘 / 数字 / 逐字**：`draw:true` + `stagger` 错峰，不必手写递增的 `at`；数字与逐字用 `count` / `type`。
- **自由场景**：`layout:"canvas"` 不生成标题层与句子流，整屏都是舞台——**第一拍要尽早落子**（0.1s 量级），否则切过来是一块空画布；晚于段起点也行，渲染器会在段起点给带 `from` 的目标前置一次 `tl.set`，不改节拍。
- **形变 / 立体感**：同拓扑 `morph` 表达 A 态到 B 态；立体感手算投影多边形再用 `morph` 串姿态——不上 three.js，产物必须仍是可解析的 SVG。
- **呼吸 / 聚光**：呼吸 = 一条拍配 `repeat`/`yoyo`、`duration` 0.4~0.7s、遍数写死；聚光压暗对照组比点亮主角省——观众要的是亮度差。
- **歪一点**：给元素 `rotation:-4`，别转相机（整页连文字一起斜读作"页面坏了"）。

## 参数演绎：一拍推一个量，几何自己算

属性补间是**属性级**的：三个元素一起动就写三条，同步全靠手抄，中间态还是插值——指针从 A 到 B 走弦，不走圆弧。场景里有**一个主参数**（把 θ 从 0 转到 2π）时，改用 `params` + `bind`，几何每帧按参数真值重算。

```json
{"seg2": {"src": "images/seg2.svg", "animate": {
  "params": {"theta": 0},
  "steps": [{"at": 0, "param": "theta", "to": 90, "duration": 1.6}],
  "bind": [{"target": "#tip", "attr": "cx", "expr": "490 + 260*cos(theta*pi/180)"}]}}}
```

| 键 | 规则 |
|---|---|
| `params` | 参数名 → 初值。名字要能直接当标识符（字母或下划线开头，≤32 字符）——它同时进表达式和 JS 变量表。**SVG 静态值要与初值对齐**（`#tip cx="750"` 而 `theta=0` 也算出 750），否则第一拍前画面会跳一下，生成期打 warn 报差值（`transform` 与 class 多元素目标不比对） |
| `steps` | 一拍只写"把哪个参数推到多少"，`duration` 必须 > 0（写 0 等于瞬跳，契约层拒）。**多拍累进**：后一拍的起点是前一拍的终值（写了 `from` 则以它为准） |
| `bind` | 一条绑定算**一个属性**。`attr` 只收几何属性：`x y cx cy r rx ry x1 y1 x2 y2 width height transform`；`d` / `points` 是复合几何，颜色与透明度走属性补间 |
多拍累进就是同一张图里接着写：后一拍的起点是前一拍的终值。

```json
{"seg2": {"src": "images/seg2.svg", "animate": {
  "params": {"theta": 0},
  "steps": [{"at": 0, "param": "theta", "to": 90, "duration": 1.6},
            {"at": 1, "param": "theta", "to": 180, "duration": 1.6}],
  "bind": [{"target": "#tip", "attr": "cx", "expr": "490 + 260*cos(theta*pi/180)"}]}}}
```

| `expr` | 算术 `+ - * / % ^ **`（`^` 乘方、右结合）、常量 `pi tau e`、函数 `sin cos tan asin acos atan sqrt abs floor ceil round exp log min max pow`、已声明的参数。**别的都不收**：没有属性访问、下标、lambda、字符串、`random` 或时间函数（逐帧 seek 下不确定的两帧就对不上）。上限 240 字符 |

实现口径：自写 tokenizer + 递归下降，**不用 `eval`**，生成期就编译成 JS 字面代码；渲染端是一条代理补间（每拍一个 0→1 的进度变量，`onUpdate` 重算全部绑定），属性只由「时刻 → 进度 → 参数 → 表达式」决定，无状态无缓存。因此 θ=45° 时尖到圆心仍是 260（走圆弧），而属性级补间走弦会短一截。

属性拍与参数拍**可以同段共存**（一个管出场、一个管连续演绎），但**同一属性不能两边都写**：两边都写时谁生效取决于脚本挂载顺序，生成期按 error 点名。

## 跨段接续（`stage: "keep"`）

一个动作常一句话讲不完，而段落边界是硬的。给接续页写 `"stage": "keep"`，它的起点就是上一页演完的那幅画面。

多数情况不用手写：`_stage_carry.infer_stage_keep` 判"同图 + 上一段有 `animate` + 上一段末拍把相机停在非原位"，判中会点名并说明怎么改回去。想让它独立重演，换开 `src` 或去掉末拍运镜。

机制：生成 HTML 时把上一页补间的收尾态烘焙进本页的内联副本——相机姿态折成 `#cam` 内静态 `<g data-ctv-stage>`，元素级 `opacity`/`attr` 落成起始属性，`x`/`y` 折成 `translate` 前缀。约束：

- 两页必须同图；上一页必须有 `animate`（接续页自己可以不写）。
- 元素级 `transform` 只接 `x`/`y`（`scale`/`rotation` 跳过并 warn）；`repeat`/`yoyo` 按收尾那头接。
- 接续页别给 `<defs>` 里的元素写补间：两份同名 id 在同一份 DOM 里，图内引用只认第一份。
- 接续页不走擦除、不生成配图入场——那正是"页界回弹"的本体。

量连续性看柱子边沿，别拿整幅 PSNR（页界本就整幅换色）。

## 体检：哪些错会当场拦住

契约层（`_images_schema`）判形状，读不到图；要读 SVG 的三条判据在生成期跑（`_animate.check_animate`）。

| 现象 | 级别 | 怎么消 |
|---|---|---|
| `animate` 挂在非 `.svg` 上 | error | 编排靠内联 SVG 的命名元素，图/视频没有可补间的目标 |
| `animate` 是空对象、`steps` 为空 | error | 不写 `animate` 就是静图；写了就至少给一拍 |
| 块级/拍级出现未知键 | error | 块级只认 `steps`/`params`/`bind`；拍级见上面的键表 |
| `params` 与 `bind` 只写一边 | error | 参数必须同时声明初值与谁读它 |
| 一拍同时写 `target` 与 `param`（或都不写） | error | 二选一 |
| `target` 在 SVG 里找不到 | error（生成期） | 拼错 id/class 不会报错、只会静默没动画，所以按文件拦 |
| 槽位页/分格页写了相机键 | error（生成期） | 换 `layout:"canvas"`，或删掉机位交给模板 |
| 图里没有带 id/class 的元素 | warn（生成期） | 编排无从落子：给元素命名 |
| 起始帧全空（所有可渲染元素写死 `opacity=0`，又没有一拍在段首点亮） | warn（生成期） | 结构底随页面到位，或把第一拍提到段首 |
| 拍点落在本页可见窗外 / 起点在窗内而终点越过下段 | warn（生成期） | 窗外等于没演，被切在半路等于没演完 |
| 参数初值与 SVG 静态值不一致 | warn（生成期） | 对齐，否则第一拍前画面跳一下 |
| 同属性被属性拍与 `bind` 同时写 | error（生成期） | 一条属性只留一个来源 |
| 参数端点算不出来（除零、定义域外） | error（生成期） | 那一帧 `onUpdate` 一抛，后面全不演 |

**觉得不同步**用 `python scripts/gen_hyperframes.py … --beat-report` 逐拍列绝对秒与踩点（`in` 句内 / `gap` 句间 / `tail` 段尾 / `before`·`after` 窗外），别改代码猜。

## 写完之后

`python scripts/check_svg.py images/` 只验图本身（比例、对比度、安全边距、墙钟动画）；`run.py --until html` 会把上面整张体检表跑一遍再出 HTML。整片是否真的动起来、动得对不对，用 `--perceptual` 看（见 `references/perceptual.md`）。
