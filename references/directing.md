# 第 4 步 · 怎么演：导演编排与跨段接续

`references/canvas.md` 讲**怎么画**，本文件讲**画完怎么让它动**——`director` 原语、两档动画取舍、下一页怎么接着上一页演。只做静态示意图的不用读本文件。

> 原语清单长，按需读：`at` / `at_time` 是时间锚（必读）；`draw` / `count` / `type` / `morph` / `stagger` 按内容挑；运镜（`<g id="cam">`）与 `stage:"keep"` 是独立能力，用到再读。

## SVG 动画：两档（氛围循环 vs 时间轴同步的「导演」）

选哪档只看一件事——**画面要不要对上某句话**：

**① 氛围循环（墙钟，默认，走 `<img>`）**：CSS `@keyframes` / SMIL 的流动虚线、呼吸光效、循环位移。按页面墙钟自走，**无法与口播时间轴同步**，逐帧渲染不保证可复现。只用于"一直在动、动到哪儿都行"的氛围层；什么都不用写，SVG 自带 CSS/SMIL 即可。`check_svg.py` 打一条 `[warn]` 提醒它不同步。

**② 时间轴同步的「导演」（净化内联，opt-in）**：要"随旁白逐句推进"的状态变化，就在 images.json 给这张 SVG 写 `director`。写了 `director` 的 SVG 会：

1. **净化后内联成活 DOM**（`scripts/_svg_sanitize.py`）：删 `<script>` / `on*` / `<foreignObject>` / 任何带 scheme 的 href（`http(s):`/`javascript:`/`file:`/`data:`/`blob:` 一律算外部资源——产物常以 `file://` 打开，`file:` 会触及本机磁盘；内部 `#id` 与相对路径放行）/ `@import`；**外链 `url()` 整颗换 `about:blank`**（`style=""` 和 `fill`/`stroke`/`filter`/`mask`/`clip-path`/`marker-*` 等呈现属性里都一样；内部 `url(#id)` 不碰）；**剥掉 SMIL 与 CSS animation/transition**（墙钟动画在逐帧 seek 里不可复现，导演档一律由补间驱动）。净化让内联回到 `<img>` 的安全水位——**这是唯一允许内联进交付 HTML 的情形**。
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

- `at`：**该段内第几句旁白**（0 基句序，支持小数 = 句内偏移）。补间位置 = `sentences[⌊at⌋].start_time + (at−⌊at⌋)·duration`；句序越界（≥ 段句数）生成期报错。
  **整数句序是准的，小数是线性猜的。** `start_time` 是算出来的不是估的；小数假设句内语速均匀，而 TTS 在标点处插停顿（接口只回整段音频、无词级时间戳）。实测 22 拍全小数锚平均偏 0.2 个字、最大 1.2 个字（≈0.15s）；**唯一会放大到 1s 量级的是长句**（超 ~5s 又带两三个标点）——规矩是写旁白时就拆句，别给渲染端加停顿估计。要现场确认某拍踩在哪个词上：`ffmpeg -hide_banner -i audio_output/sentences/s007.wav -af silencedetect=noise=-35dB:d=0.18 -f null -`，看 `silence_start` 落在句文本第几个标点后。
- `at_time`：**与 `at` 二选一**（都写或都不写契约层报错）。从**该段音频起点**排：**数字** = 绝对秒；**相对串** `"+N"`/`"-N"` = 上一条 beat 结束后再 N 秒（首条退到段落起点；只有真占时间的步——有 `from`/`to`/`draw`，或 morph/count/type 这类负责整段的原语——才推进游标，纯 `set` 停在原地）。**它与语音没有任何绑定，这是"动画和语音不同步"最常见的来源**：绝对秒按某一版配音手算，重配音/改句长/动 `--gap` 都会整体错位且不报错。**所以：跟旁白走的节拍用 `at`；`at_time` 只留给真不跟旁白的那一拍**（相机推镜、纯节奏卡点）。
  生成期查三件事（解析函数 `_timeline.beat_positions` 与渲染端共用，门禁算的落点就是真执行的那一拍）：① 落在本页可见窗口之外（早于本段起点或晚于下段起点）→ `[warn]`（引擎按 `data-start`/`data-duration` 硬切可见性，窗外补间等于没演）；② 压在句间/段尾静音上不报（那是节奏不是 bug）；③ 起点在窗内、终点越过下段起点 → `[warn]`（被切在半路，终态从没出现过；跨度按 `duration × 遍数` + `stagger` 展开算）。落在 `stage:"keep"` 接续页上更要紧：接续烘焙把时间轴折到最后一帧，搬走的正是那个没演到的终态。
- `target`：图内**单个 id 或 class 选择器**（`#`/`.` 开头）。作用域自动收到该段容器 `#img-<sid>` 下，多段同名 id/class 不串台。**class 选择器专为 `stagger` 开**。逗号/后代/伪类不收。**target 必须真实存在**——落空不是报错而是静默没动画，`gen_hyperframes.py` 生成期按文件拦成 error。
- `draw`：可选布尔，描边自绘。渲染端用 `pathLength="1"` 归一化 path 长度（不需量 `getTotalLength`），起点收边（`stroke-dasharray:1; stroke-dashoffset:1`），`at` 到时补 `strokeDashoffset` 到 0，"线自己长出来"。目标必须是**有描边的形状**（path/line/polyline/带 stroke 的圆矩形）。
- `morph`：同拓扑 path 形变（`{"from": "M…", "to": "M…"}`）。生成期按渲染帧率 2 倍把中间态采样成 `tl.set(attr:{d})` 关键帧——逐帧可复现、零依赖。硬约束：`from`/`to` **同拓扑**（命令字母序列与每命令数值个数一致，只坐标不同）；target 必须是该 `<path>` 的 id；不能叠 `from`/`to`/`set`/`draw`。`from` 要和 SVG 里画好的 `d` 一致，否则第 0 帧跳变。
- `set` / `to` / `from`：GSAP 补间变量，`draw`/`morph`/`count` 之外至少要有一个。`set`=瞬时；`from`+`to`=fromTo；单独 `to`/`from` 各走一路。值只收 **JSON 可序列化**的（标量/数组/对象；非 CSS 属性如 `r`/`height`/`d` 走 `attr:{}`），**禁任何 `on*` 回调键**（信源不可信）。**`attr` 必须是对象**：标量/数组写法本页不报错，但跨段接续的烘焙只认对象，非对象会被当 CSS 属性写进 `style`，下一页起始画面从第一帧就是坏的。
- `count`：数字滚动（`{"to": 数, "from"?, "decimals"?, "prefix"?, "suffix"?}`）。`<text>` 数值从 `from`（缺省 0）缓动到 `to`。target 指 `<text>`；不能叠 `from`/`to`/`set`/`draw`/`morph`。实现是渲染端生成的代理补间 + `onUpdate` 写 `textContent`（技能自己的、只读校验过的回调，与被禁的信源 `on*` 不是一回事），故 seek 可复现。**console 探针要写 `tl.seek(t, false)`**——GSAP `seek()` 默认 `suppressEvents=true` 会跳过 `onUpdate`，count/字幕高亮这类靠回调的会"看起来卡住"。
- `type`：打字机逐字揭示（只认空对象 `{}`）。运行时读 `<text>` 现成 `textContent` 作源串，`onUpdate` 写 `slice(0, 已打字数)`。"打什么字"由 SVG 决定，**无参数可配**；要改快慢用 step 级 `duration`/`ease`（想匀速写 `"ease": "none"`）。target 指直接写文本的 `<text>`（带 `<tspan>` 排版的复杂文本会被压平）。不能叠其它补间原语，`stagger` 被契约层拒。
- `stagger`：可选，配 class 目标做一组元素错峰揭示。数字（间隔秒）或对象（GSAP 原生 `each`/`amount`/`from`/`grid`/`ease`）。只对有补间的 step 生效；`morph`/`count`/`type`/`draw` 各自负责整段，配 `stagger` 被拒。
- `duration` / `ease` / `delay`：可选。缺省取模板 `animation.director`（视觉真源单一数据源）。这三个旋钮连同 `stagger`/`repeat`/`yoyo` **只能写在 step 级**：塞进 `to`/`from`/`set` 补间变量里被契约层拒（会被覆盖掉或让门禁跨度与真实跨度分家）。
- `ease`：缓动档。**认不出的档当场报错**，报错列出全部合法族名/方向写法/四种例外，照报错改即可。选档手感：缺省 `power2.out` 是"慢收"；物件**进场**用 `.in`、**退场/落定**用 `.out`、来回摆与推镜用 `.inOut`，要弹要跳用 `back`/`bounce`/`elastic`。
- `repeat` / `yoyo`：让同一拍演好几遍。`repeat: N` = 再演 N 遍（共 N+1）；`repeat: -1` = 无限循环，演到被下一页盖过来；`yoyo: true` = 来回（偶数遍收在起点）。"呼吸"一条步即可。规矩：
  - **跨度按遍数算**（`duration × 遍数`）：相对链游标、出窗 warn 右界、接续烘焙"最后停在哪儿"读同一个数（`_timeline.beat_span`）。
  - **只有真会"演"的步能配**（`from`/`to`/`draw`/`morph`/`count`/`type`）。纯 `set` 被拒；单写 `yoyo` 被拒（只演一遍 GSAP 不会回头）。
  - **`morph` 不能配 `repeat: -1`**（采样没有终点）也不能配 `duration: 0`（0 秒采不出中间态=瞬跳）；补间类步的 `duration: 0` 是"瞬时生效"，合法。
  - **跨段接续按收尾那一头搬**：见「跨段接续」的 `repeat`/`yoyo` 一条。

**觉得"动画和语音不太同步"时**：`gen_hyperframes.py … --beat-report` 逐拍列绝对秒与踩点（`in` 句内 / `gap` 句间静音 / `tail` 段尾 / `before`·`after` 窗外——窗外那两类就是没在成片里演过的）。`in` 不等于准（句内 frac 是线性假设，偏差量级见上面 `at` 条），怀疑某拍踩错词用那条 `silencedetect` 命令定位，**别改代码**。预览里的偏差另算：`preview.js` 是时间线为主、音频跟随（差 >0.15s 才硬拉一次 `currentTime`），成片是逐帧 seek，严格相等。

**十二个够用的"演"**（每招一行，写法见上面原语）：

- **逐句推进**：一步挂一句，`set`/`to` 把该讲到的节点点亮、卡片浮起。
- **运镜 / 相机（pan·zoom）**：把要动的场景包进 `<g id="cam">`，对它补间 `scale`/`x`/`y`（GSAP 写成 SVG `transform`，逐帧 seek 确定）。例：`{"at": 0, "target": "#cam", "to": {"scale": 2.2, "x": -300, "y": -120, "svgOrigin": "500 360"}, "duration": 1.8}`，再来一步 `scale: 1` 拉回全景。
  **焦点没写时渲染器替你钉一个**：注入本页 `#cam` 内容并集中心当 `svgOrigin`，于是运行期、跨段烘焙、出画 warn 三方同一原点；**写了 `svgOrigin` 的以你写的为准**，什么都不注入。
  **`#cam` 里只放主视觉。** 标题、结论句、公式带、图例条这类"文字带"留在 `#cam` **之外**（它们是给观众读的，不该跟着镜头跑）；要跟推镜就单独补一步与相机同步的补间。
  **为什么**：`scale>1` 以 `svgOrigin` 为中心放大，离焦点越远位移越大。实测 1080×1440 画布 `scale:1.16 svgOrigin:"540 660"` 把页底 y=1362 标签推到 1476（出画），页顶标题贴边。
  **机器兜住的部分**：生成期 `director_prepare` 按运镜**收尾位**把 `#cam` 内容投影回画幅，静止时整条出画或切在上下边缘外的文字打 `[warn]`（几何在 `scripts/_cam_crop.py`：只看收尾位；只认 `<g id="cam">`；祖先带 rotate/matrix 的子树跳过；满幅底板豁免）。停在 warn 不上 error，因为"静止位看不见"也可能是故意的。
  **机器兜不住的那件事**：只判"出画"不判"撞字"——推出去后可能正好压到 `#cam` 之外那行静止标题/旁白上。所以**留白按"推完那一帧"算，不是按 SVG 源码算**：把收尾位 `scale`/平移套一遍坐标，和静止元素基线比。
- **自绘 + 错峰（draw + stagger）**：`draw:true` 让曲线/坐标轴自己长出来；一组元素配 `stagger` 一条 step 逐个错峰揭示，不必手写递增 `at`。
- **数字滚动（count）**：`count:{from,to,decimals,suffix}` 让数值"讲到就数上去"。
- **逐字揭示（type）**：`type:{}` 把 `<text>` 现成整段文字逐字打出来；`"ease":"none"` 匀速。
- **自由场景（整页画布 × 导演）**：`layout:"canvas"` 连标题层/句子流层 DOM 都不生成，整屏就是这张图 + 导演 = 一整个可编排舞台。要什么时候出现用 `at_time` 按秒排。**第一拍要尽早落子**——切过来的瞬间画布是空的（导演从 opacity 0 起步），把底图/坐标轴/标题放最靠前的一拍（0.1s 量级，与 `canvas.md`「整页画布的密度与层次」同一口径）。
- **形变（morph·同拓扑）**：同一条 `<path>` 换 y 坐标长高、折线被拉直、A 态到 B 态——只要命令序列一致就能演，逐帧可复现、零依赖。
- **立体感（投影多边形 + morph，不是真 3D）**：等距（`x=(u−v)·cos30°`、`y=(u+v)·sin30°−w`）或单点透视手算投影，同一物体 2–3 个姿态各写一条同拓扑 path 用 `morph` 串。"立方体翻面""坐标轴倾斜"本质是逐点 reshape。走这条路而不是 three.js：产物仍是可解析的 SVG，字号/对比度/空带/运镜门禁照常生效。真 3D 曲面转体做不了就别硬凹——"两个姿态 + 一次 morph"比"让它转起来"便宜。
- **呼吸 / 脉动（repeat·yoyo）**：一条步配 `repeat`/`yoyo` 让光晕明灭几次；收尾停哪头由遍数说话（`yoyo`+偶数遍回起点），同一数驱动相对链游标、出窗 warn 与接续烘焙。`duration` 0.4–0.7s 的往复最像呼吸，1.5s 只会读成"还没演完"。
- **聚光（压暗对照组，不点亮主角）**：给其余那组 `to: {"opacity": 0.25}` 比给主角"亮起来"省——压暗才产生亮度差，且被压暗的留在原位不必重画。配 `stagger` 让一组 `.dim` 错峰暗下去。
- **两层视差**：背景层 `scale: 1.05`、主视觉 `scale: 1.3`，同一 `svgOrigin`，推近时两层错开速度。运镜自查只认 `#cam`，第二层不在视野里——推近幅度按**收尾位**自己套一遍坐标。
- **歪元素而不是歪相机**：要"被推翻/被审视"给元素一步 `rotation: -4`，别转相机（相机一转整页连文字带一起斜，读成"页面坏了"）。

## 跨段接续（images.json 的 `stage: "keep"`）：下一页接着上一页演

一个舞台动作常一句话讲不完，但段落边界是硬的：每页各自内联、各自从 `opacity="0"` 起步，"推进局部"演完下一页切回去就是**回弹**。给接续页写 `"stage": "keep"`，这一页的起点是**上一页演完的那幅画面**：

```json
{
  "seg2": {"src": "images/stage.svg", "director": {"steps": ["… 最后一拍是 #cam 推近 …"]}},
  "seg3": {"src": "images/stage.svg", "stage": "keep",
           "director": {"steps": ["… 在推近后的画面上继续演 …"]}}
}
```

机制（口径在 `scripts/_stage_carry.py`）：生成 HTML 时按 manifest 段序再走一遍，把上一页所有补间的**收尾态**烘焙进这一页的内联副本——相机姿态折成 `#cam` 内新增的静态 `<g data-ctv-stage transform="…">`（**不写进 `#cam` 自身 transform**，否则本页导演补间踩在改写过的起点上），元素级 `opacity`/`attr` 落到节点起始属性，`x`/`y` 折成 `translate(dx,dy) 原串` 前缀（原串一字不动留作本段补间基值）。

**量连续性按柱子边沿定位，别拿整幅 PSNR 当判据**：三段链实测页界那两帧配图区完全重合（边沿一字不差），但每页 `--seg-accent` 不同，模板氛围光与进度条在切页那帧整幅换色，整幅 PSNR 停在 26 dB 是配色差异不是没接上。

约束：

- **两页 `src` 必须指向同一张图**（不同路径无从烘焙）；由 `gen_hyperframes` 按段序判，判不过点名到段落的 error。
- **上一页必须写了 `director`**（写了才净化内联，`<img>` 那份没有可读元素态）。接续页**自己可以不写**：一段演完、下一页只是"停在那幅画上说话"合法，steps 为空画面照样接续。
- **元素级 `transform` 只接 `x`/`y`**：平移跟原点无关，能精确折成外层平移。`scale`/`rotation`/`skew`/`xPercent` 跳过并 warn（要以元素 bbox 中心为原点折算，中心是"占位盒"估的，宁可不接也不悄悄挪位）；元素自带 `rotate`/`matrix`/`skew` 时连 `x`/`y` 也接不动，同样出声。
- **`repeat`/`yoyo` 按收尾那一头接**：`yoyo`+偶数遍回到起点，什么都不搬（这是对的）；`repeat: -1` **没有收尾态**，不搬并点名到 target 出声。相机上有无限循环，整台相机的收尾位不作数（不折 `<g data-ctv-stage>`）。
- **接续页不要给 `<defs>` 里的元素写补间**（渐变/滤镜/marker/`<symbol>`，及被 `url(#id)`/`href="#id"` 引用的 id）：同一张 SVG 内联两份后 id 重复，图内引用按文档序只认第一份（= 上一页那幅），这一拍打在本页份上没人读。生成期按步报 error。要延续渐变/滤镜状态就在前一段写。
- **相机绕的那一点是渲染器钉的**（"页界不跳"的前提，不是可选项）：作者整页没写 `svgOrigin`/`transformOrigin` 时，渲染器给相机步注入**本页 `#cam` 内容并集中心**当 `svgOrigin`（`_cam_crop.cam_default_origin`），跨段烘焙与出画 warn 用同一个点。不钉死的话 GSAP 绕 `#cam` bbox **左上角**、还跟着别的步的 `from` 瞬态飘（一个 `from:{x:-420}` 就能把 bbox.x 从 90 拽到 −40），页界上留一次纯平移阶跃（三段链实测 24~36px）。**写了原点的页面以你写的为准**。

接续页附带两处渲染端行为：**不走擦除**（wipe 强制归零，与整页画布同一条硬切路径），**不生成配图入场补间**（`#img-{sid}` 的淡入+上移正是"页界回弹"的本体）。
