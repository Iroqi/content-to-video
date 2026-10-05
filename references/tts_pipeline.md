# 第 3 步深度参考：TTS 管线参数、预置音色与 manifest 字段

本文件是 SKILL.md 第 3 步（运行 TTS 管线）的深度参考，主流程与示例命令见 SKILL.md。

## pipeline.py 参数说明

- `--api-key`：可选，默认从环境变量或 `~/.config/ai-video/.env` 读取 `MIMO_API_KEY`
- `--source segments_source.json`：结构化输入（必填）。逐段独立分句，直接产出带 segments 分组的 manifest
- `--voice-id`：预置音色 ID，见下表（默认 `冰糖`）。下表只写**听感与选型建议**（那是不查代码就得不出来的部分）；可用音色的**名单以 `python scripts/pipeline.py --help` 的 choices 为准**——它由 `_voices.list_voice_ids()` 现生成，跟着音色注册表走，本表只可能是它的滞后副本。
- `--voice-style`：自然语言风格描述，控制语气情绪（如"清晰沉稳的讲解风格，语速适中"）
- `--gap 0.4`：相邻两句之间的静音间隔（秒，默认 `0.4`）。拼接音频与 manifest 时间轴都会在句间插入这段静音
- `--speed 1.5`：**语速倍率**，默认 `1.5`（比正常快一半）。通过 ffmpeg `atempo` 对每句合成音频做精确变速，**与 TTS 模型自然语速无关、完全确定**。设 `1.0` 即原始语速。字幕时间轴会按变速后实测时长对齐，依然精准同步
- `--resume`：仅在文本、音色、模型、音频时长以及语速状态都可证明一致时跳过已有 WAV。判定标准是"**能不能证明**"而不是"有没有反证"，所以以下都算失效缓存、直接重合成：缺 `.sha` 内容指纹（被手删、或上次 sidecar 写入失败）、`.spd` 不可读且没有 `.orig.wav` 可回退、WAV 头部声明的样本数超出文件实际字节（上次写入被截断——这种文件 ffmpeg 仍量得出正常时长，只看时长检不出）。失效时终端打印 `缓存失效（原因）`，命中时按是否本次重放过变速分别打印 `[skip,cached]`（纯复用，一句 ffmpeg 都没跑）或 `[skip,respeed]`（本次重新施加了语速），一眼可辨为什么这句要重跑。日常反复调试同一份稿件时**始终建议加上**
- `--model` / `--base-url`：TTS 模型名 / API base URL，一般不需要改，默认读 env
- `--dry-run`：仅断句 + 段落预览，不调 TTS、不写任何文件（验证稿件格式用，零额度消耗）；此模式下 `-o` 可省略
- `--workers 4`：并行 TTS 调用数（默认 4）。句子数多时能明显缩短总耗时；调大会更快触及 API 限速，遇到大量 `[retry]` 日志时调小
- `--api-timeout`：单次 TTS API 调用超时（默认 30s，必须是 `>0` 的有限秒数）。`0`/负数/`NaN` 在 argparse 阶段就被拒——它们会让每句白重试 3 次、最后只报"全部句子失败"，把矛头指向 API 而不是参数
- `--on-fail {abort,silence}`：单句 TTS 失败时的处理方式（默认 `abort`，任何句子失败都阻断管线，避免悄悄丢失内容）。`silence` 改为静音占位继续：时长按字数/语速启发式估算折算、落 `.failed` marker、manifest 对应句子带 `synth_failed: true`，不阻断整条视频，事后可定位补录

> **manifest 的降级字段**：`status` 只有 `ok` / `degraded` 两种，`degraded` 是明细对象。
> 它的键是一份**封闭集合**，由 `scripts/_degraded.py` 的注册表 `KINDS` 派生（`_manifest_schema.DEGRADED_KEYS` 是它的别名），并在 `validate_timing_manifest`
> 拦截未知键（拼错的键会让制作报告静默少一条，只剩 `status` 拦交付却说不出拦的是哪一项）。
> 新增一档降级只改 `_degraded.py` 一处登记（加键名常量 + reader + 在 `KINDS` 里登记一行），`pipeline.py` 写入时用该常量（`D.<常量>`，拼错是 NameError，不会静默丢失）；
> 目前会出现的键：
> - `tts_silence_fallback_count`：静音占位句数；
> - `tts_lost_sentence_count`：**连静音都没生成、已从成片里消失**的句数（按"应产出句数 − 实际句数"算）；
> - `segments_dropped`：整段没有任何可用音频、已从 manifest 剔除的段 id 列表（不是计数）；
> - `audio_shorter_than_timeline`：拼接/混音后的音频实测比时间轴终点短超过 250ms 的秒数，
>   片尾那几秒有字幕没声音（`total_duration` 仍按时间轴取值，保证契约自洽）。
>
> 只要 `degraded` 非空，manifest 的 `status` 就是 `degraded`。生成器**不会替你放行，
> 也不会拦你**——旧的渲染编排那道 `--allow-degraded` 闸门已随 HTML 后端一起移除，
> 这条链路上没有可再开的开关。所以交付前自己看一眼 `status` 与 `degraded` 明细：
> 少几句配音、少一整段，都不该被当成正常成片交出去。
>
> **补录（解除粘性降级）**：静音占位句带内容指纹与 `.failed` marker，`--resume` 会一直复用
> 同一份静音——修好 TTS 后不清理，隔天重跑仍命中静音、`status` 仍是 `degraded`（定时任务场景尤甚）。
> 补录 = 删除该句缓存：删掉 `音频输出目录/sentences/` 下对应句子的 `.wav`（连同
> `.sha`/`.spd`/`.failed` sidecar），下次带 `--resume` 重跑即自动重合成；真合成成功后
> `.failed` marker 由管线自动清理。

完整参数列表见 `python scripts/pipeline.py --help`——本文只列核心参数。

**拼接产物会跟时间轴对账**：拼接本体是标准库 `wave` 逐帧串接（ffmpeg 只负责把
少数派格式重采样成多数派，格式已由 `concat_audio` 在拼接前统一），`combined.wav`
的总时长本应等于"逐句时长之和 + 句间静音"；若实测比它短过
0.25s（固定值，与下游契约"最后一句不得超出 total_duration + 250ms"同档——时间轴按逐句
时长+句间静音排布，两边是同一个条件），管线直接报错退出而不是继续。这道对账是
"某句 WAV 头部声明一个长度、实际只写了一半字节"（上次写入被截断——ffmpeg 仍量得出
名义时长，单看头部检不出）这类静默损坏的下游兜底；真损坏的量级是秒到几十秒，
250ms 只吸收逐句舍入误差。真触发时删掉 `combined.wav` 重跑——`--resume` 的状态机
会按头部/字节一致性把坏句判成失效缓存自动重合成，正常句子照常复用。

## 预置音色表

> 音色 ID 列表的权威来源是 `scripts/_voices.py` 的 voice registry；下表只补充性别与搭配建议。

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

manifest 由 pipeline 从 `segments_source.json` 自动产出——**手写 manifest 少见，以 pipeline 产出为准**；确需手写/裁剪时按下述格式提供（契约层会校验，**缺字段和多字段都直接报错**：顶层 / `segments[i]` / 句子对象三层的字段集都封闭，清单在 `_manifest_schema.MANIFEST_TOP_KEYS` / `MANIFEST_SEGMENT_KEYS` / `MANIFEST_SENTENCE_KEYS`。`takeaway` 拼成 `take_away` 会让结尾那一行悄悄退回标题、`layout` 拼错会让那页默默回到槽位版式、`combined_audio` 拼错读侧直接兜默认值，三类都不出声）：

```json
{
  "schema_version": 2,
  "status": "ok",
  "degraded": {},
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

- 顶层 `schema_version`（只能是 `2`）、`status`（`ok` / `degraded`）、`sentences`（非空列表）、`segments`（非空列表）与数值 `total_duration`（ffmpeg 实测总时长）必填；`degraded` 只在 `status` 为 `degraded` 时必须是非空对象。每个句子对象含 `index`/`text`/`start_time`/`duration` 四个字段（对话段落的句子另有 `speaker`，TTS 失败降级为静音的句子带 `synth_failed: true`）
- 顶层 `closing_cta`（可选，单行字符串）：结尾 agenda 卡的 `→` 尾行，pipeline 从稿件顶层 `cta` 原样透传；手写 manifest 要有这行就直接写。它只在存在 `closing` 段、且那一页是 agenda 版式时才有地方画（`closing_layout: "canvas"` 时它随 agenda 卡一起不生成）；两种"无处可画"（没有 closing 段 / 结尾页换成了整页海报）生成阶段都打同一条 `[warn] manifest 有 closing_cta，但本次没有 agenda 版式的结尾页可承载它`
- `segments` 每段必须自带**非空** `sentences` 列表（分组渲染的数据源，缺失或为空会被契约校验直接拒绝）。段落的**版面**字段只有 `id`/`title`/`tagline`/`accent` 四个——段内不再有任何其它文字来源，画面上的正文全部来自句子流；其余出现的键（`layout`/`speed`/`voice_id`/`voice_style`/`takeaway`/`turns`）是版式、语音与 agenda 的元数据，随段携带但不参与内容段排版（见下节"可选字段"）。`layout` 只在显式时才出现：opening/closing 由 pipeline 必盖——默认 `"agenda"`，稿件写了顶层 `opening_layout` / `closing_layout: "canvas"` 就改盖 `"canvas"`；内容段只在作者声明时带 `"canvas"`（不带 = 槽位版式）。`start`/`end` 句子索引只是 pipeline 的内部中间格式，最终 manifest 不含这两个字段

**可选字段**：
- `speed`（float）：段落级语速倍率，覆盖全局 `--speed`。例如开场/结尾用 `1.2`、正文段用 `1.5`。仅影响 TTS atempo 变速，不影响字幕时间轴精度
- `voice_id` / `voice_style`（string）：内容段级的音色覆盖，覆盖全局 `--voice-id`/`--voice-style`；opening/closing 不支持这两个字段（规则见 `references/writing.md` 的段落级字段一节）
- `turns`（数组）：仅对话段落出现，由 pipeline 从 `segments_source.json` 的 `dialogue` 自动产出（`{start, end, speaker, label, voice_id?, voice_style?}`，全局句子区间），手写 manifest 一般不需要自己拼

`segments` 必须按时间轴顺序排列，并且恰好覆盖顶层每个 sentence index；缺句、重复句或乱序都会在 HTML 生成前直接拒绝。手写时另外两条同样会被拒收，别等报错才发现：相邻两句的 `[start_time, start_time+duration)` 区间不得重叠（正常管线里句间至少隔 `--gap`，重叠说明时间轴被手改坏），以及段内句子的 `text` / `start_time` / `duration` 必须与顶层同 index 的句子逐字段一致。
