# 第 2 步 · TTS：管线参数、预置音色与 manifest 字段

命令见 SKILL.md 第 2 步；参数以 `python scripts/pipeline.py --help` 为准。

## pipeline.py 参数

参数、默认值与语义的**唯一真源是 `python scripts/pipeline.py --help`**（每条 help 都带语义）。写稿/调参时记住三个默认值与四个 help 会说的连带：

- 三个默认值：全局语速默认 `1.5`（`--speed`）；开场/结尾用 `1.2`（段级 `speed` 覆盖）；句间静音默认 `0.4`（`--gap`）。
- 四个连带：`--gap` 同时决定转场宽度（见 `references/look.md`「动画：实现要点」）；`--speed` 变速后字幕按实测时长对齐；`--resume` 日常调试建议始终加（缓存失效条件在 help 里）；`--on-fail silence` 的句子带 `synth_failed`，正式渲染须 `--allow-degraded`（见下节）。

## 降级与补录

`manifest.status` 只取 `ok` / `degraded`；`degraded` 是封闭键集对象（由 `scripts/_degraded.py` 的 `KINDS` 派生，新增降级只改那一处）：

- `tts_silence_fallback_count`：静音占位句数。
- `tts_lost_sentence_count`：连静音都没有、已从成片消失的句数（应产出 − 实际）。
- `segments_dropped`：整段无可用音频、被剔除的段 id 列表。
- `audio_shorter_than_timeline`：拼接/混音后音频比时间轴终点短超过 250ms 的秒数（片尾有字幕没声音；`total_duration` 仍按时间轴取值）。

`degraded` 非空时 `run.py` 正式渲染前停住（exit 3），须显式 `--allow-degraded`；`--until html` 只警告。

**补录**：静音占位句带 `.failed` marker，`--resume` 会一直复用同一份静音。补录 = 删该句 `sentences/` 下的 `.wav`（连同 `.sha`/`.spd`/`.failed`），重跑 `--resume` 自动重合成；成功后 marker 自动清理。

**拼接产物与时间轴对账**：`combined.wav` 总时长应等于"逐句时长之和 + 句间静音"，实测短过 250ms 直接报错退出（兜住 WAV 头声明长度、实际只写一半的截断）。触发就删 `combined.wav` 重跑。

## 预置音色表

音色 ID 权威来源是 `scripts/_voices.py` 的 registry；下表只补性别与搭配建议。

| voice-id | 语言 | 性别 | 适用场景 |
|----------|------|------|----------|
| `冰糖` | 中文 | 女 | 默认推荐，清晰自然，适合讲解播报 |
| `茉莉` | 中文 | 女 | 温柔娓娓道来，适合故事/科普 |
| `苏打` | 中文 | 男 | 沉稳有力 |
| `白桦` | 中文 | 男 | 低沉浑厚 |
| `Mia` | 英文 | 女 | 中文稿件里的英文术语/品牌名 |
| `Chloe` | 英文 | 女 | 同上 |
| `Milo` | 英文 | 男 | 同上，正式播报 |
| `Dean` | 英文 | 男 | 同上 |

中文内容推荐 `冰糖`/`苏打`（中英混读更自然）；英文音色读中文欠佳，只用于英文术语。双人对话推荐一男一女反差组合（`茉莉`+`苏打` 或 `冰糖`+`白桦`），避免音域接近的同性别组合。

## manifest 的 segments 字段

manifest 由 pipeline 自动产出，手写少见；确需手写/裁剪时按此格式。字段集封闭（`_manifest_schema.MANIFEST_TOP_KEYS` / `SEGMENT_KEYS` / `SENTENCE_KEYS`），缺字段和多字段都直接报错：

```json
{
  "schema_version": 2,
  "status": "ok",
  "degraded": {},
  "sentences": [
    {"index": 0, "text": "大家好，今天我们来看反向传播算法。", "start_time": 0.0, "duration": 3.2, "onset": 0.18, "voiced": 2.74},
    {"index": 1, "text": "它是神经网络学习的核心机制。", "start_time": 3.6, "duration": 2.8, "onset": 0.14, "voiced": 2.31}
  ],
  "total_duration": 6.4,
  "gap": 0.4,
  "voice_id": "冰糖",
  "combined_audio": "<项目目录>/audio_output/combined.wav",
  "segments": [
    {
      "id": "seg1",
      "title": "反向传播是什么",
      "tagline": "深度学习基础",
      "accent": "#2dd4bf",
      "sentences": [
        {"index": 0, "text": "大家好，今天我们来看反向传播算法。", "start_time": 0.0, "duration": 3.2},
        {"index": 1, "text": "它是神经网络学习的核心机制。", "start_time": 3.6, "duration": 2.8}
      ]
    }
  ]
}
```

- 顶层必填：`schema_version`（只能 `2`）、`status`（`ok`/`degraded`）、`sentences`、`segments`（均非空）、数值 `total_duration`；`degraded` 仅 `status=degraded` 时必为非空对象。句子对象含 `index`/`text`/`start_time`/`duration`（对话段另有 `speaker`，降级句带 `synth_failed`）。
- 其余字段见下面几段；`onset`/`voiced` 单独写在「发声区间」一节。
- `closing_cta`（可选，单行字符串）：结尾 agenda 卡的 `→` 尾行，pipeline 从稿件顶层 `cta` 原样透传；无 agenda 版式结尾页可承载时打 `[warn] manifest 有 closing_cta，但本次没有 agenda 版式的结尾页可承载它`。
- 每段必须自带**非空** `sentences`（分组渲染的数据源）；段内版面字段只有 `id`/`title`/`tagline`/`accent`，其余（`layout`/`speed`/`voice_id`/`voice_style`/`takeaway`/`turns`）是元数据、随段携带但不参与排版。`layout` 只在显式时才出现：opening/closing 由 pipeline 必盖（默认 `"agenda"`，稿件写 `opening_layout`/`closing_layout:"canvas"` 时改盖 `"canvas"`）。
- 可选：`speed`（段级语速倍率，覆盖全局，例如开场/结尾用 `1.2`、正文段用 `1.5`，仅影响 atempo 变速，不影响字幕时间轴精度）；`voice_id`/`voice_style`（段级音色覆盖；opening/closing 不支持）；`turns`（对话段自动产出，`{start, end, speaker, label, voice_id?, voice_style?}`）。
- `segments` 必须按时间轴顺序排列、恰好覆盖顶层每个 sentence index；缺句/重复/乱序直接拒。相邻两句区间不得重叠（正常至少隔 `--gap`，重叠说明时间轴被手改坏）；段内句子与顶层同 index 句子逐字段一致。

## 发声区间（onset / voiced）

`duration` 是**音频块**长度，里面含着句首吸气与句尾拖音。下游真正需要的是"这个人在说这话"的那一段，所以 pipeline 另量两个字段：

| 字段 | 含义 | 取值 |
| --- | --- | --- |
| `onset` | 音频块开头到**真正开口**的秒数 | `≥ 0` |
| `voiced` | 实际发声长度（首尾静默之外的部分，含句中换气停顿） | `> 0`，且 `onset + voiced ≤ duration` |

量法：`ffmpeg silencedetect=noise=-35dB:d=0.05`（`scripts/_speech.py`），边界对齐到它的 20ms 扫描网格。50ms 是最短静音——齿音/爆破间隙（<40ms）不算停顿，句尾换气（>100ms）算。−35dB 是实测 −30/−35/−40/−45 检出完全一致的中间值。

它们被谁读：

- **字幕换句**：cue 起点 = `start_time + onset`，人在开口那一刻才换字幕（否则字幕比话早半拍，页末句在没人说话时还亮着）。
- **词级强调（卡拉OK）**：进度分母 = `voiced`，念完那一刻正好 100%（`references/look.md`「动画：实现要点」）。

三条边界都是契约层直接报错（不是 warn）：只写一个、`onset + voiced > duration`、给 `synth_failed` 的静音兜底句写 `voiced`——读出来都是"说半句"，而产物看起来正常。

量不出来（整句静音、非 WAV）就不写这对字段，下游退回按文件长度计时；**不记 degraded**——只影响精度，不值得让正式渲染被 `--allow-degraded` 拦下。

手改 `start_time`/`duration` 时把这两个字段一起删掉或改对，否则会撞上面的边界。
