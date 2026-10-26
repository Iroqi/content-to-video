# 第 4 步 · 观感门禁：这一页在屏幕上到底有没有东西

`rendering.md` 里那些门禁看的都是**结构**：`lint` 管确定性，`Layout` 量盒子，
`Contrast` 算 CSS 里的色，`check_svg` 量源码坐标。它们加在一起仍然答不出一个
最简单的问题：

> **这一页在屏幕上的那十几秒里，观众到底看到了什么？**

`motion.md`「官方校验命令」管"能不能渲"，成片时长契约管"演完没有"，
`--check-frames` 只是把帧递给人。本文件管第三条：**画面里有没有东西、有没有
替旁白动过、换页对不对得上**。

## 怎么跑

```bash
# 编排层（推荐）：渲染完顺带跑，判词进 production_report 的 perceptual 字段
python scripts/run.py --source S -o OUT --perceptual

# 迭代期：刚出 HTML 就跑，量的是编排本身（没有编码损失）
python scripts/run.py --source S -o OUT --until html --perceptual

# 单跑：复用已有帧（CI 缓存），或当场取帧
python scripts/perceptual.py -m OUT/timing_manifest.json --frames hf-project/perceptual/frames
python scripts/perceptual.py -m OUT/timing_manifest.json --project hf-project --aspect portrait
```

需要 Node + Chrome（取帧）+ Pillow（读像素）。产物两份，都在
`<project>/perceptual/`：

- `contact-sheet.jpg` —— 每段若干帧拼一张，**这张要人看**；
- `perceptual_report.json` —— 每段的采样数、覆盖率区间、图槽墨水占比，加逐条判词。

**只报不拦**：默认退出 0。`perceptual.py --strict` 才以退出码 5 结束（CI 交付档）。

## 四条判据

阈值都在 `scripts/perceptual.py` 顶部，且**每一档都标了实测依据**：

| 判词 | 量什么 | 阈值 | 典型成因 |
| --- | --- | --- | --- |
| `canvas_thin` | 槽位版式**图槽**内"算得上内容"的像素占比（框内取色差 6），取这一段的**最高值** | < 2.5% | 配图只有一行字 + 一条细线（实测 1.7%）；合格折线图 5.0%–5.8%。`check_svg`/`Layout` 都判不出"图里没有东西" |
| `empty_page` | 整页画布段的整帧覆盖率 | < 0.6% | 画布页没有内容，或占位图留在交付路径上 |
| `dead_seconds` | 相邻采样之间的变化率，连成区间 | < 1.2% 且 ≥ 3s | 这一页只有 Ken Burns 在爬（6%/s 的推近在 2s 间隔上只变 0.2–0.4%）；旁白讲了三件事，画面一件都没变 |
| `switch_skew` | 换页读数与段落口播起点的偏移，**已扣掉采样间隔** | > 0.5s | manifest 时间轴被手改过；重配音后 `--gap` 与旧时间轴不同步 |

## 两条刻意不做的判据

- **全流程覆盖率不判"空页"**：模板给每一页铺了 `.bg`/`.grid`，覆盖率恒在 95%
  以上，拿它判空页只会得到"每页都有内容"的假判决（实测过）。要判空只能判
  **图槽那一块**——这也是 `slot_rect` 要从 `_template.py` 的几何现算、而不是
  写死像素的原因。
- **agenda 卡不判死帧**：开屏/结尾本来就是"停在那里让人读"的一屏静态信息，
  动了反而干扰。死帧只针对讲解段。

## 怎么读判词

判词指向的是**内容决策**，不是参数：

- `dead_seconds` 连成一大片 → 这段的图该跟着旁白变（`canvas.md`「槽位版式的演进」
  的 `evolve`），或者这一段本来就该拆成两页（拆页比加动画便宜，也更像 3b1b
  的"一次一屏一件事"）。
- `canvas_thin` → 图的信息量撑不住 4:3 槽位。重画（主视觉占页宽 ≥70%，
  见 `canvas.md`「整页画布的密度与层次」），或者让这半句话交给 `evolve` 演，
  或者干脆撤掉配图走纯文字页（空图槽比没有图槽更糟）。
- `switch_skew` → 时间轴与画面分家。先看是不是采样网格的读数（判词里已经
  扣掉了），再看 manifest 是不是被手改过。

判词只证明**没有明显坏**。好不好看、像不像一支"视频"而不是"配了音的幻灯片"，
仍然只能翻那张 contact sheet——这一步没有任何机器能替。
