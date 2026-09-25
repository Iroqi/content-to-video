# 第 3 步深度参考：TTS 管线参数、预置音色与 manifest 字段

本文件是 SKILL.md 第 3 步（运行 TTS 管线）的深度参考，主流程与示例命令见 SKILL.md。

## pipeline.py 参数说明

- `--api-key`：可选，默认从环境变量或 `~/.config/ai-video/.env` 读取 `MIMO_API_KEY`
- `--source segments_source.json`：结构化输入（必填）。逐段独立分句，直接产出带 segments 分组的 manifest
- `--voice-id`：预置音色 ID，见下表（默认 `冰糖`）
- `--voice-style`：自然语言风格描述，控制语气情绪（如"清晰沉稳的讲解风格，语速适中"）
- `--gap 0.4`：相邻两句之间的静音间隔（秒，默认 `0.4`）。拼接音频与 manifest 时间轴都会在句间插入这段静音
- `--speed 1.5`：**语速倍率**，默认 `1.5`（比正常快一半）。通过 ffmpeg `atempo` 对每句合成音频做精确变速，**与 TTS 模型自然语速无关、完全确定**。设 `1.0` 即原始语速。字幕时间轴会按变速后实测时长对齐，依然精准同步
- `--resume`：仅在文本、音色、模型、音频时长以及语速状态都可证明一致时跳过已有 WAV。判定标准是"**能不能证明**"而不是"有没有反证"，所以以下都算失效缓存、直接重合成：缺 `.sha` 内容指纹（被手删、或上次 sidecar 写入失败）、`.spd` 不可读且没有 `.orig.wav` 可回退、WAV 头部声明的样本数超出文件实际字节（上次写入被截断——这种文件 ffmpeg 仍量得出正常时长，只看时长检不出）。失效时终端打印 `缓存失效（原因）`，命中时打印 `[skip]`，一眼可辨为什么这句要重跑。日常反复调试同一份稿件时**始终建议加上**
- `--bgm bgm.mp3` / `--bgm-volume 0.15`：背景音乐（自动循环、混音；音量 0.0-1.0，默认 0.15）
- `--loudness -16`：响度归一化目标（LUFS）。默认不做归一化；设置后对最终拼接/混音产物做单遍 ffmpeg `loudnorm`，失败则沿用未归一化音频并记入 degraded 的 `loudness_norm_failed`
- `--model` / `--base-url`：TTS 模型名 / API base URL，一般不需要改，默认读 env
- `--dry-run`：仅断句 + 段落预览，不调 TTS、不写任何文件（验证稿件格式用，零额度消耗）；此模式下 `-o` 可省略
- `--workers 4`：并行 TTS 调用数（默认 4）。句子数多时能明显缩短总耗时；调大会更快触及 API 限速，遇到大量 `[retry]` 日志时调小
- `--api-timeout`：单次 TTS API 调用超时（默认 30s，必须是 `>0` 的有限秒数）。`0`/负数/`NaN` 在 argparse 阶段就被拒——它们会让每句白重试 3 次、最后只报"全部句子失败"，把矛头指向 API 而不是参数
- `--on-fail {abort,silence}`：单句 TTS 失败时的处理方式（默认 `abort`，任何句子失败都阻断管线，避免悄悄丢失内容）。`silence` 改为静音占位继续：时长按字数/语速启发式估算折算、落 `.failed` marker、manifest 对应句子带 `synth_failed: true`，不阻断整条视频，事后可定位补录

> **manifest 的降级字段**：`status` 只有 `ok` / `degraded` 两种，`degraded` 是明细对象，
> 目前会出现的键：
> - `tts_silence_fallback_count`：静音占位句数；
> - `tts_lost_sentence_count`：**连静音都没生成、已从成片里消失**的句数（按"应产出句数 − 实际句数"算）；
> - `segments_dropped`：整段没有任何可用音频、已从 manifest 剔除的段 id 列表（不是计数）；
> - `bgm_mix_failed` / `bgm_missing_file`：要了 BGM 但没混进成片（混音失败 / 文件不存在或消失）；
> - `loudness_norm_failed`：`--loudness` 要求没做到，成片响度未归一化；
> - `audio_shorter_than_timeline`：拼接/混音后的音频实测比时间轴终点短超过 250ms 的秒数，
>   片尾那几秒有字幕没声音（`total_duration` 仍按时间轴取值，保证契约自洽）。
>
> 只要 `degraded` 非空，`run.py` 正式渲染前就会停住（exit 3）要求显式 `--allow-degraded`，
> 并把原因念给人看（少几句配音、少一整段、没有 BGM 都不该被当成正常成片交付）。
> `--until html` 只警告不阻断，方便先看版式。

> **默认语速**：`--speed` 默认 `1.5`（日常调用可省略）。如需原始语速，显式传 `--speed 1.0`。
>
> 完整参数列表见 `python scripts/pipeline.py --help`——本文只列核心参数。

**拼接产物会跟时间轴对账**：`combined.wav` 实测总时长若比"逐句时长之和 + 句间静音"短过
0.25s（固定值，刻意比下游契约"最后一句不得超出 total_duration + 250ms"更严），管线直接
报错退出而不是继续。这道对账专门用来抓"ffmpeg 返回 0 但音频被悄悄截短"那一类静默损坏
（concat demuxer 遇到采样率/声道不一致的输入就会这样，格式已由 `concat_audio` 在拼接前
统一，正常稿件永远触发不了它；真损坏的量级是秒到几十秒，250ms 只吸收逐句舍入误差）。
真触发时按提示检查各句 WAV 的格式，确认无误后删掉 `combined.wav` 重跑（`--resume` 会复用已合成的句子）。

## 预置音色列表

> 音色 ID 列表的唯一权威来源是 `scripts/_contracts.py` 内嵌的 voice registry；下表补充性别与搭配建议，增删音色时与 `_contracts.py` 同步。

| voice-id | 语言 | 性别 | 适用场景 |
|----------|------|------|----------|
| `冰糖` | 中文 | 女 | 默认推荐，清晰自然，适合讲解播报 |
| `茉莉` | 中文 | 女 | 温柔娓娓道来，适合故事/科普 |
| `苏打` | 中文 | 男 | 中文男声，沉稳有力 |
| `白桦` | 中文 | 男 | 中文男声，低沉浑厚 |
| `Mia` | 英文 | 女 | 中文稿件里偶尔出现的英文术语/品牌名 |
| `Chloe` | 英文 | 女 | 中文稿件里偶尔出现的英文术语/品牌名 |
| `Milo` | 英文 | 男 | 中文稿件里偶尔出现的英文术语/品牌名，正式播报 |
| `Dean` | 英文 | 男 | 中文稿件里偶尔出现的英文术语/品牌名 |

> **中文内容推荐使用 `冰糖` 或 `苏打`**，中英混合发音更自然。`Milo`/`Chloe` 等英文音色在朗读中文时效果欠佳；这四个英文音色只用于中文稿件里的英文术语/品牌名朗读。
>
> **双人对话推荐搭配**：`茉莉`（女，温柔）+ `苏打`（男，沉稳）或 `冰糖`（女）+ `白桦`（男）——一男一女音色反差明显；说话人切换当前完全靠音色提示（画面不渲染说话人标签），听感上不容易混淆是谁在说话。避免选两个音域接近的同性别音色做对话。

## manifest 的 `segments` 字段格式

manifest 由 pipeline 从 `segments_source.json` 自动产出——**手写 manifest 少见，以 pipeline 产出为准**；确需手写/裁剪时按下述格式提供（契约层会校验，缺字段直接报错）：

```json
{
  "sentences": [
    {"index": 0, "text": "大家好，今天我们来看反向传播算法。", "start_time": 0.0, "duration": 3.2},
    {"index": 1, "text": "它是神经网络学习的核心机制。", "start_time": 3.6, "duration": 2.8}
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

- 顶层 `sentences`（非空列表）与数值 `total_duration`（ffmpeg 实测总时长）必填；每个句子对象含 `index`/`text`/`start_time`/`duration` 四个字段（对话段落的句子另有 `speaker`，TTS 失败降级为静音的句子带 `synth_failed: true`）
- `segments` 每段必须自带**非空** `sentences` 列表（分组渲染的数据源，缺失或为空会被契约校验直接拒绝）。段落的**版面**字段只有 `id`/`title`/`tagline`/`accent` 四个——段内不再有任何其它文字来源，画面上的正文全部来自句子流；其余出现的键（`speed`/`voice_id`/`voice_style`/`takeaway`/`turns`/`duration`）是语音与 agenda 的元数据，随段携带但不参与内容段排版（见下节"可选字段"）。`start`/`end` 句子索引只是 pipeline 的内部中间格式，最终 manifest 不含这两个字段

**可选字段**：
- `speed`（float）：段落级语速倍率，覆盖全局 `--speed`。例如开场/结尾用 `1.2`、正文段用 `1.5`。仅影响 TTS atempo 变速，不影响字幕时间轴精度
- `voice_id` / `voice_style`（string）：内容段级的音色覆盖，覆盖全局 `--voice-id`/`--voice-style`。注意 opening/closing 是管线自动造的结构性页，**不支持**这两个字段（顶层只有 `opening_speed`/`closing_speed`）；要换开场音色就用全局 `--voice-id`
- `turns`（数组）：仅对话段落出现，由 pipeline 从 `segments_source.json` 的 `dialogue` 自动产出（`{start, end, speaker, label, voice_id?, voice_style?}`，全局句子区间），手写 manifest 一般不需要自己拼
- `duration`（非负数值，可选）：段落时长的显式覆盖，`html_renderer` 优先读它、缺失时才按段内句子推算；省略即默认行为

`segments` 必须按时间轴顺序排列，并且恰好覆盖顶层每个 sentence index；缺句、重复句或乱序都会在 HTML 生成前直接拒绝。
