# 第 3 步 · 扩展包：把画风与素材在编译期交给外部工具

**这份是给扩展作者看的。** 使用者只是想要某种画面、说不出该叫什么——那部分看
`references/imagery.md` 开头那一节，不必读这份。

**这一档不进主管线。** 扩展包是生成期工具，产出仍是标准 `.svg` / `steps` / 合规素材，
作者照旧把它们写进 `images.json`。`run.py`、`gen_hyperframes.py`、`html_renderer.py`
一行都不用改，渲染端永远不会执行第三方代码。装了包不会自动生效；用不用、用哪一个
由执行者按使用者描述的画面定，**不需要使用者自己选**。

> 判据只有一句：**换画风，不动一行 `animate.steps`**。做不到这条的扩展，耦合错了层。

## 一、这一层解决什么

skill 的甜区是「一份不可信的信源 → 确定性 HTML composition」的编译与质检，
不是「更全的动画库」。GSAP 与 Hyperframes 各自负责时间线执行与逐帧出片，
往渲染端塞第三方库只会撞上逐帧 `seek()` 可复现这条红线。

所以扩展点开在**编译期**：库在管线外把活干完，把结果交给 skill。这样
`vendor/gsap.min.js` 的供应链钉固不动、净化面不破，而第三方库可以随便换、
随便升级——它们根本不进成片 HTML。

三种产出意图，共享同一份契约，走不同校验分支：

| `kind` | 交什么 | 典型来源 | 谁消费 |
| --- | --- | --- | --- |
| `style-filter` | `.svg` | 素描 / 像素 / 手绘抖动后处理 | `images.json` 的 `src`，`animate` 照常挂 |
| `asset-transcoder` | 合规素材路径 | 3D / shader / 网页录屏离线渲成的视频 | `images.json` 的 `src`，走 `<video>` 那条路 |
| `steps-compiler` | `steps` 数组 | Lottie 关键帧、CSV 时序、LaTeX → SVG 的编排草稿 | `images.json` 的 `animate.steps` |

## 二、契约：一个目录，三样东西

```
<扩展包目录>/
  manifest.json   # 声明：名字 / 版本 / 意图 / 产出 / 依赖 / 来源
  compile.py      # 纯函数 compile(spec, ctx) -> {"svg"?, "steps"?, "media"?}
  fixture/        # 一组 输入 → 期望输出，逐字节对账
```

### `manifest.json`（键集封闭）

| 键 | 规则 |
| --- | --- |
| `name` | 必填，`^[a-z][a-z0-9_-]{0,31}$` |
| `version` | 必填，`x.y.z` |
| `kind` | 必填，只收上表三种 |
| `outputs` | 必填，非空、不重复，取值在 `svg` / `steps` / `media` 里 |
| `dependencies` | 可选，字符串数组，**首版必须为空** |
| `provider` | 可选，来源记账 |

多写一个键当场报错，不静默忽略——与 `images.json` 同一口径。`dependencies`
非空时拒收：编译期第三方库没钉版本之前，「同一份稿件两次生成不一致」会从
渲染端蔓延到编译端，那是把确定性红线自己拆了。要用第三方库，先补依赖钉固清单。

### `compile.py`（纯函数）

签名固定为 `compile(spec, ctx)`，返回对象，键必须出现在 `manifest.outputs` 里。
`spec` 是作者写的 JSON 对象（键集由扩展包自己定），`ctx` 首版为空对象，留作
将来传画幅与主题的入口。

编译期禁用的东西由 `scripts/_extensions.py` 在 AST 层拦，不是靠自觉：

- 不许导入 `random` / `time` / `datetime` / `urllib` / `requests` / `socket` /
  `subprocess` / `threading` 等——墙钟、随机、网络、子进程、并发。
- 不许调 `eval` / `exec` / `__import__` / `compile` / `open`。产物用返回值交回，
  扩展自己不写文件。
- 不许用 `global` / `nonlocal`，不许定义模块级可变容器常量。第二次编译不能
  带上第一次的残留。

`seed` 这类需要「看起来随机」的量由作者显式写进 `spec`，同一份 spec 两次编译
必须逐字节一致（`assert_deterministic` 对账）。这与 `canvas_kit.py` 的
`roughness` 是同一条思路：确定性抖动，不是真随机。

### `fixture/`

一组 `spec.json` + 期望产物。扩展包自己跑对账，不占核心的测试预算——
**把复杂度外移**，而不是往核心堆第 11 个档位。

## 三、三条红线

**① 显式注册。** 没有自动扫描、没有插件发现。命令里必须写出 `--extension <目录>`；
装了一个包不会隐式改变任何产物。加载时把 `manifest.json` 与 `compile.py` 的
sha256 记进来源（`provenance()`），产物能回溯到哪一份扩展代码。

**② 统一校验。** 扩展输出走与手写 SVG 完全相同的净化与门禁，不设豁免分支：
`svg` 过 `_svg_sanitize.sanitize_svg_for_inline`（剥 script / 墙钟标签，保留
`<filter>` / `<pattern>` / `<mask>`），`steps` 过 `_animate.check_animate`
（落空 `target` 按文件拦），`media` 过路径与扩展名粗判。

**③ 确定性。** 两次编译逐字节一致；净化后不许残留脚本宿主或墙钟动画。
扩展包出错了按人话打回，不吐 traceback、不落半成品文件。

## 四、正交判据：换画风不动 `animate.steps`

编排按 `#id` 寻址，所以画风层与编排层之间唯一的接口就是 **`#id` 集合**。
`verify_output()` 给了 `base_svg` 就会对账：扩展前后的**可寻址** id 集合必须
完全一致，不增、不减、不改名。`<defs>` / `<filter>` / `<pattern>` / 渐变这些
定义性子树里的 id 只供 `url(#…)` 内部引用，不是 `animate` 的目标，不计入对账
——所以画风扩展往 defs 里塞滤镜定义是合规的。少了这条判据，一个「顺手给
元素包一层 `<g>` 并改了 id」的扩展就会让所有补间静默落空，而生成期本来全绿。

内置参考扩展 `sketch` 因此只做三件事：往 `<defs>` 塞纯 SVG 滤镜定义、
给目标元素加 `filter="url(#ctv-ext-sketch)"`（像素档另加 `shape-rendering`）、
序列化返回。**几何坐标、id、`<text>` 内容一律不动。** 内部片段引用净化器不拦，
渲染端仍是离线可跑的活 DOM。

## 五、内置参考扩展 `sketch`

三档 preset，全部是纯 SVG filter，无外链、无脚本、无墙钟：

| `preset` | 做法 | 读感 |
| --- | --- | --- |
| `sketch` | `feTurbulence` + 小尺度 `feDisplacementMap` | 手绘描边抖动 |
| `rough` | 更密的湍流 + 更大位移 | 粗描 / 速写 |
| `pixel` | `feComponentTransfer` 色阶量化 + `crispEdges` | 海报 / 像素块 |

使用者侧那几句人话与这三档的对应写在 `references/imagery.md` 开头那一节；这里只留一张映射，
换底层实现时改这一处，使用者文档一个字不动：

| 使用者会这么说 | 落到哪档 |
| --- | --- |
| 「纸面 / 版画 / 干净的手绘线」 | `sketch` |
| 「像手画的 / 铅笔草图 / 别太规整」 | `rough` |
| 「像老游戏 / 8-bit / 马赛克感」 | `pixel` |

可照抄的 `spec.json`：

```json
{
  "preset": "sketch",
  "seed": 7,
  "intensity": 0.5,
  "targets": ["#bar-a", "#bar-b"]
}
```

`targets` 省略时覆盖所有带 id 的图形元素（自动跳过 `<text>` / `<defs>` /
结构底）；写错一个 id 当场报错，不会静默少画一片。`intensity` 收 0~1，
`seed` 收 0~9999 的整数。

在项目目录里跑（**不要 cd 到技能目录**，输出落进源码目录会被守卫拦）：

```bash
python /path/to/content-to-video/scripts/extension.py \
  --extension /path/to/content-to-video/scripts/extensions/sketch \
  --spec spec.json --base-svg images/base.svg \
  -o images/seg1.svg --report images/seg1.ext.json
```

`-o` 按产物类型落盘：`svg` 写标准 `.svg` 文本；`steps` / `media` 写成单个
JSON 对象，键名与 `manifest.outputs` 对齐，可以直接把 `steps` 那一坨贴进
`images.json`。三类产物都先过确定性对账与统一校验，不合规的不落半成品。
接下来照旧写 `images.json`：

```json
{"seg1": {"src": "images/seg1.svg", "animate": {"steps": [
  {"at": 0, "target": "#bar-a", "to": {"opacity": 1}, "duration": 0.6}
]}}}
```

`--report` 记的是 `provider` / `extension` / `sha256`，与 `images.json` 的
`provider` / `license` 同一意图，供回溯；不写也照样出图。

## 六、被否掉的四个方案

| 方案 | 为什么不收 |
| --- | --- |
| 运行时注入第三方 JS / 全局插件钩子 | 一举破坏 GSAP 供应链钉固、逐帧 seek 确定性、净化面三条红线；对离线 MP4 产物没有收益 |
| 自动扫描 `extensions/` 目录并全部加载 | 同一份稿件在不同机器上会因为多装一个包而产出不同，直接撞确定性红线 |
| 让扩展包直接改 `images.json` 契约层 | 封闭键集是核心护城河；扩展应该产出标准产物，不是扩张契约面 |
| 首版就允许 `dependencies` 非空 | 编译期依赖没钉版本 → 两次生成不一致。先补钉固清单再谈生态 |

## 七、边界与门禁

- **L0 素材级**（已有）：外部渲染器直接产出 `.mp4/.webm/.svg` 交给 `images.json`。
  3D / shader / 网页录屏走这条，不需要扩展包。离线渲好的公式图也走这条：
  交回前要剥掉工具自带的墙钟 metadata（两次渲染字节不同 = 确定性红线破），
  并把只有运行时才解析得开的引用（如 `<use href="#字形">`）展开成自包含的
  `<path>`。产物必须是标准、自足、净化器能直接放行的素材，渲染端不认识那个
  离线工具，也不需要认识。
- **L1 编译期**（本档）：产出标准产物，主管线零改动。
- **L2 运行时**：明确不做，见上表。

`check_svg.py` 的静态自查、`hyperframes check` 的 lint 与浏览器阶段都在下游，
扩展产物与手写 SVG 走同一条路，不另设验收通道。核心侧新增的判据在
`tests/test_extensions.py`：键集封闭、显式加载、两次编译一致、`#id` 正交、
净化器剥墙钟留滤镜、坏扩展按文件拦。

术语口径：槽位 / 整页画布 / 导演档 / 句子流。`references/motion.md「契约」`
管 `animate` 的写法，`references/canvas.md` 管 SVG 怎么画，
`references/imagery.md` 管配图从哪来；本档只管「画完的图在编译期被谁加工过」。
