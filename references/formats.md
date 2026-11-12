# 第 1 步 · 体裁适配与能力边界

这份回答一件事：**拿到一类内容，不改代码现在能交到什么程度**。写稿之前读它，选错体裁比选错动效贵得多——第 1 步定的 `layout` / `beats` 决定后面三步有没有得救。

评级口径是「不改生成代码、按现有文档写法出片」的成品质量，不是「理论上能不能做」。依据只认仓库里真有的代码与文档，不认设想中的能力。

| 档 | 含义 |
|---|---|
| **A 原生** | 现成版式与动效档直接组合，效果由 skill 自己生成 |
| **B 贴素材** | 能出片，但画面效果在 skill 之外预渲染，skill 只负责排版、配音、时间轴 |
| **C 手工** | 能做，但要手写大量 SVG，且有一类结构能力缺失 |

## 一、八类逐项评估

| 体裁 | 档 | 现成能力（真源） | 边界 / 缺口 |
|---|---|---|---|
| **1 宣传片** | A 原生 | `opening` / `closing` agenda 卡、整页海报版式、`tagline` / `cta`、wipe 转场、标题裁窗揭示、逐句 `beats` 编排、`#cam` 运镜、字幕板 | 没有整片一条音乐轨：音频侧只有一条 TTS 口播轨，时间轴由句长驱动而非 BPM。`at_time` 与语音无绑定，重配音或改 `--gap` 会整体错位且不报错。适合「念稿 + 版式」，不适合卡点剪辑 |
| **2 数据动画** | A 原生 | `canvas_kit.py` 的 axis / bars / polyline / circle / rule / text / panel，`count` 数字滚动与 `odometer` 跨档进位，`draw` 描边自绘，`check_svg.py` 的数据图几何自查 | 没有饼图 / 散点 / 热力 / 桑基 kind，坐标与刻度要自己算。补 kind 是最便宜的一次扩容，但手写 SVG 也能过门禁 |
| **3 代码讲解** | C 手工 | SVG `text` 的逐字 / `chunk:"word"`、`decode` 逐行锁定、`stagger` 逐行亮出；`check_svg.py` 有等宽字符宽度估算（CJK 1em / 拉丁 0.55em） | 没有代码块版式、没有语法高亮、没有缩进保持。要做需补 code kind 与「源码 → SVG」离线转译，属编译期扩展，不是写稿技巧 |
| **4 三维** | B 贴素材 | `images.json` 认 `.mp4/.webm/.mov/.avi/.mkv`（`MEDIA_VIDEO_EXTS`）；仅有 `transformPerspective` 的 peel 翻页转场 | 没有 3D 场景、相机、光照。三维不是 skill 生成的。硬限制：`animate` 只对 `.svg` 生效，视频段不能同时做逐拍图内编排或标注 |
| **5 数学** | C 手工 | SVG `text` 放 Unicode 符号；`morph`（同拓扑）与参数演绎做函数 / 几何动画 | 没有 LaTeX / MathJax，没有分式、积分上下限、矩阵、多行对齐。简单公式与函数动画能做，公式密集型讲解不行 |
| **6 着色器** | B 贴素材 | 同三维：预渲染成合规视频素材接入；`media_start` / `fade_in` / `fade_out` / `duck` 全套素材旋钮 | 无 WebGL / GLSL / `<canvas>`，净化器禁外链、渲染必须离线可跑。PNG 序列得先合成视频；视频段不能叠 `animate` |
| **7 角色动画** | C 手工 | SVG 补间 + `morph` + `drag`（链尾跟随）+ `trail`（残影），适用「简笔角色 + 风格化位移」 | 没有骨骼、变形目标、口型同步。稍复杂的表演必须走第 4 项预渲染素材路线 |
| **8 网页导览** | B 贴素材 | 无浏览器滚动捕获入口；正解是离线逐帧录屏 → ffmpeg 固定帧率合成 → 按视频素材接入 | 标注与录屏不能同段混用（`animate` 与视频互斥）。要么把圈注烘进录屏本身，要么拆成相邻两段 |

## 二、三条跨体裁的结构性约束

这三条不是八个独立缺口，是同一处根因在不同体裁上的投影，别逐项去补。

1. **时间轴由 TTS 句长驱动**。`scripts/_timeline.py` 的 `at` 按句长插值；`at_time` 是绝对秒逃生口，与语音没有任何绑定。宣传片卡点、动态歌曲、代码演示的敲键节奏都撞在这条上。
2. **视频素材与逐拍编排互斥**。`scripts/_images_schema.py` 的 `_check_animate` 拒绝非 SVG 的 `src` 写 `animate`。三维、着色器、网页导览三项共享这条边界；要标注就把标注烘进素材，或拆段。
3. **画幅只有双分块**。`scripts/_template.py` 顶层按 `vertical` / `landscape` 分块，`--aspect` 只认 `portrait|landscape`，未知值直接报错。竖屏是 1080×1440（3:4），不是平台标准的 9:16。

## 三、B 档的统一落地路径

三维、着色器、网页导览三类共用同一条路径。可照抄的合规接入片段与生成它的命令都钉在这里，并接进官方门禁（`tests/check_image_snippets.py` 校验片段、`tests/check_examples.py` 校验整稿）——照抄一份过不了契约层的片段，比没有片段更糟。

**离线录制**（以网页导览为例；shader 与三维渲染同理，只要最终产物是一个固定帧率的 mp4）：逐帧截图到目录，固定帧数、固定帧率合成，时间轴完全确定、没有加载空转：

```bash
# 逐帧截图 → 25fps 合成；把加载等待与鼠标轨迹都烘进像素里
ffmpeg -framerate 25 -i frames/%04d.png -c:v libx264 -pix_fmt yuv420p hf-project/images/tour.mp4
```

**接入片段**（`hf-project/images.json`，键必须是真实段落 id；这里用 `seg2` 与本文档示例一致）：

```json
{
  "seg2": {"src": "images/tour.mp4", "muted": true, "loop": true,
           "media_start": 0.4, "fade_in": 0.3, "fade_out": 0.3,
           "provider": "chromium + playwright + ffmpeg",
           "source_url": "file://demo_site.html",
           "attribution": "本地示例页，离线逐帧录制"}
}
```

`muted:true` 是默认安全项：视频段一旦想起用原声当主体，必须显式写 `volume` 并配套 `duck:false`，否则原声会压着口播一起播且链上不报错。写好片段后管家 CLI 可以替你过一遍契约层，避免手抄字段名拼对一半：

```bash
python scripts/attach_media.py --images hf-project/images.json --segment seg2 \
  --src images/tour.mp4 --media-start 0.4 --loop --fade-in 0.3 --fade-out 0.3 \
  --provider "chromium + playwright + ffmpeg" \
  --source-url "file://demo_site.html" \
  --attribution "本地示例页，离线逐帧录制"
```

上面这条命令产出的条目与手写片段逐字段一致，`--dry-run` 可只打印不写盘。CLI 只生成/合并条目并在写盘前调用 `validate_images_json`；它不录制、不渲染，**不会**替视频段写 `animate`（写了也被契约层拒）。三维与着色器把 `--src` 换成自己的预渲染 mp4 即可，其余完全一致。

## 四、整稿示例（网页导览）

B 档的稿子按普通解说写就行：画面在 `images.json`，不要在这一步试图写 `beats` 去驱动素材内部。

```json
{
  "title": "仓库网页导览",
  "opening": "走一遍这个真实仓库的三个入口，看看一份信源是怎么变成视频的。",
  "segments": [
    {"id": "seg1", "title": "先看最新提交", "tagline": "实录",
     "text": "首屏最新提交是一次动效档的合并记录。往下看 README 的四步流水线，从写稿到出片。",
     "takeaway": "改动都有据可查"},
    {"id": "seg2", "title": "再看编排档位", "tagline": "实录",
     "text": "动效档位表里，每一档只答一件事：这一拍该怎么动。录屏会跟着这段旁白走。",
     "takeaway": "画面跟着旁白，不跟着节拍"}
  ],
  "closing": "三个入口走完，这条链路就清楚了。",
  "cta": "关注我们"
}
```

`--dry-run` 整稿可过；`seg2` 对应上面的 `images.json` 片段。画幅、字号、字幕跟随口播的验收见 `references/look.md`；科普推演片的六步结构另见 `references/science.md`（不在八类里，按自己的专项走）。

## 五、优先级

- **先做 A 档**（宣传片 / 数据动画）：不需要改代码，也是这条流水线的设计目标。
- **B 档照第三节抄**：三类共用一份片段；「素材接入」有过门禁的契约示例与管家 CLI，不是口头建议。
- **C 档暂不投入**：代码讲解 / 数学 / 角色动画的真缺口都在编译期（code kind、LaTeX → SVG 离线转译、骨骼），不是写稿技巧能绕过的；要做就先补一层离线转译，并同步登记到 `tests/check_examples.py`，别在稿子里硬扛。
- **想扩 9:16**：那是第二节第三条结构性约束，改动面是模板第三分块与全量 golden 基线，属于独立一轮，别和体裁适配混在一起做。
