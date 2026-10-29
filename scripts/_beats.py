"""台词板：每个视觉事件必须绑定它讲的那句话。

## 为什么需要它

仓库原有的动效机制全是**手写补间**：`animate.steps` 的每一步都
要作者自己写 `at`（句序）或 `at_time`（绝对秒）。这带来两个病，而且都不出声：

1. **漏绑**。一张演三态的图，作者只写了前两拍，第三拍"图还在第一个状态、旁白已经
   讲到第三个"——没有任何门禁看得见。`hyperframes check` 只证明能渲，时长契约只
   证明演完，`perceptual` 只知道"画面在动"。
2. **错绑**。`at_time` 按某一版配音手算，改了句长/重配音后整体漂移，同样零报错。

台词板的做法是把"这一拍说什么、画面变成什么"从补间的**实现**里提出来，单列成一张
对照表：

```json
"beats": [
  {"on": 0, "change": "把 a 压暗、点亮 b"},
  {"on": 2, "change": "两条线合成一条"}
]
```

`on` 是**必填**的句序——这是整张表存在的意义。没有 `on` 的视觉事件属于"我知道要
变，但说不上来在第几句变"，那正是上面两种病的来源。

## 分工：台词板管意图，其余管实现

| 层 | 回答什么 | 载体 |
|---|---|---|
| 台词板 `beats` | **这一拍说什么、画面该变成什么**（人话） | segments_source.json |
| `animate.steps` | 具体哪条属性从多少补到多少 | images.json |
| `perceptual` | 成片里到底有没有变 | 取帧像素判据 |

三者不互相替代：台词板是**意图层**，可以被机器逐个核对；steps 是**实现层**，
灵活但表达不了"意图"；perceptual 是**验收层**，能证伪但说不出该改哪儿。

## 为什么挂在 segments_source.json 而不是 images.json

`on` 的整数是**句序**，句序在写稿时就定下来了（分句规则见 `_text.split_sentences`）。
挂在写稿侧，作者写第几句的时候顺手就把这一拍记上；挂在 images.json 则要回头数
句子，而那时稿子可能已经改过。images.json 那边仍有 `shot`（可选的验收点），
两处分工见 `_images_schema`。
"""
import sys

# 一句台词带一拍以上的终止标点（。！？…；）时，观众读到的是"两个结论"。
# 这一句念完要 3-6 秒，画面却只能对上其中一句——三拍以上的说明是这一条。
MANY_SENTENCE_PUNCT = 3

# 一条 `change` 的字数上限。它是**意图备忘**不是字幕：超过这个长度作者多半在
# 描述实现（"把 g#a 的 opacity 从 1 补到 0.3 用 power2.out 历时 0.8 秒"），
# 那是 steps 的活。
CHANGE_MAX_CHARS = 60

BEAT_KEYS = ("on", "change")


def validate_beats(raw, where):
    """校验一段的 `beats`，返回**归一后**的列表（每条 {on:int, change:str}）。

    `where` 只用于报错定位（如 `segments[2]（title='熵'）的 'beats'`）。

    可写两种形状：
    - **简写**（推荐）：该段第 n 拍 → 一句人话。`["把两条线合成一条", "推到第三态"]`
      的下标就是句序，等价于 `[{"on": 0, ...}, {"on": 1, ...}]`。写稿侧最常见的
      是"顺着一路演下去"，简写把句序省成了缩进本身。
    - **全写**：`[{"on": 0, "change": "..."}, {"on": 2, "change": "..."}]`。
      要跳过某句（第 2 句不换画面）时必须用这个——简写没有"空位"的表达。

    两种形状**不可混用**：一半下标一半 `on` 时，"第几拍"有两个互相矛盾的来源，
    静默挑一个就是让另一批数字错位。混用直接报错。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"{where} 必须是数组（每一条是一拍视觉事件），"
            '简写形如 ["把两条线合成一条", "推到第三态"]，'
            '全写形如 [{"on": 0, "change": "把两条线合成一条"}]')
    if not raw:
        return []

    kinds = {_kind_of(item) for item in raw}
    if len(kinds) > 1:
        bad = sorted(k for k in kinds if k is not None)
        raise ValueError(
            f"{where} 混用了两种写法（{'/'.join(bad)}）。"
            "简写（直接写字符串，下标即句序）与全写（{on, change}，可跳句）"
            "只能选一种：混用时\"第几拍\"有两个来源，没写 on 的那几条会静默错位。"
            "要跳句就全部改成全写。")

    out = []
    for i, item in enumerate(raw):
        kind = _kind_of(item)
        if kind is None:
            raise ValueError(
                f"{where}[{i}] 必须是字符串（简写）或 {{on, change}} 对象（全写），"
                f"实际是 {type(item).__name__}")
        if kind == "short":
            out.append(_check_change(item, i, where, on=i))
        else:
            out.append(_check_full(item, i, where))

    seen = {}
    for i, beat in enumerate(out):
        if beat["on"] in seen:
            raise ValueError(
                f"{where} 的句序 {beat['on']} 出现了两次"
                f"（第 {seen[beat['on']]} 条与第 {i} 条）：一句旁白只能绑一拍——"
                "同一句里要变两处，写成一条 change 用顿号并列，"
                "真正的先后顺序交给 animate.steps")
        seen[beat["on"]] = i
    return out


def _kind_of(item):
    if isinstance(item, str):
        return "short"
    if isinstance(item, dict):
        return "full"
    return None


def _check_change(value, index, where, on):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{where}[{index}] 的 change 必须是非空字符串："
            "写的是\"观众这一拍该看到什么变了\"，不是属性名")
    text = value.strip()
    if len(text) > CHANGE_MAX_CHARS:
        raise ValueError(
            f"{where}[{index}] 的 change 有 {len(text)} 字，超过 {CHANGE_MAX_CHARS} 字上限。"
            "这里写意图（\"把两条线合成一条\"），不要写实现"
            "（属性、时长、缓动属于 images.json 的 animate）")
    return {"on": on, "change": text}


def _check_full(item, index, where):
    from _validate import _reject_unknown_keys

    _reject_unknown_keys(item, BEAT_KEYS, f"{where}[{index}]")
    if "on" not in item:
        raise ValueError(
            f"{where}[{index}] 缺少 'on'（第几句旁白时画面变）。"
            "台词板的全部意义就是把这个数字写出来——"
            "说不上来在第几句变，说明这一拍还没想清楚，"
            "先回去看稿子哪一句在讲这件事")
    on = item["on"]
    if isinstance(on, bool) or not isinstance(on, int):
        raise ValueError(
            f"{where}[{index}] 的 'on' 必须是整数（0 = 本段第一句），"
            f"实际是 {type(on).__name__}。句内偏移留给 animate 的 at=0.5，"
            "台词板只认整句")
    if on < 0:
        raise ValueError(f"{where}[{index}] 的 'on'={on} 不能是负数（0 = 本段第一句）")
    return _check_change(item.get("change"), index, where, on)


def sentence_terminal_count(text):
    """一句话里有几个终止标点（= 读者会读成几个结论）。

    只数句末类标点，不分英文句号里的小数点（`3.14` 不是两个结论）——判据是
    "后面跟不跟空白/结尾"，小数点后面是数字，不算。分号与省略号算：它们都是
    语义断点，一句里出现两个以上说明这一句在讲两件事。
    """
    terminators = "。！？；…!?;."
    n = 0
    n_chars = len(text)
    for i, ch in enumerate(text):
        if ch not in terminators:
            continue
        prev = text[i - 1] if i > 0 else ""
        nxt = text[i + 1] if i + 1 < n_chars else ""
        # 英文句号夹在两个数字之间是小数点（3.14），不是句末。
        # 只看右边不够：`版本 2. 下一步` 这种"序号 + 句号"右边是空格，
        # 那是真的句末，该数。
        if ch == "." and prev.isdigit() and nxt.isdigit():
            continue
        n += 1
    return n


def audit(manifest, images=None):
    """按**已定稿的时间轴**核对台词板，返回 (errors, warnings, note)。

    manifest 是校验过的 timing_manifest；`segments[].sentences[].text` 就是最终
    念出来的那些句子（重分句后的真源）。

    `images=None` 时不核对"有没有实现"（调用方没给 images.json 就只能闭嘴，
    报一条无凭据的 warn 比不报更糟）。

    核对三件事：

    1. **句序越界**（error）：`on` 指到该段不存在的句子。写稿时节拍按原稿数，
       重分句后句数变少，`on: 5` 会落到一片空处——静默失效，成片里看不出。
    2. **只声明不实现**（warn）：台词板说有这一拍，而 `images.json` 里这一段
       一条 `animate` 都没有。台词板写成愿望清单是最容易发生的事，
       而它比不写更坏：作者以为写了。
    3. **一句两拍**（warn）：`on` 指向的那句话里有 3 个以上终止标点。这一句要念
       3-6 秒，画面只能对上其中一句，观众会看到"图先变了、话还没讲完"。
    """
    errs, warns = [], []
    total = 0
    for seg in manifest.get("segments") or []:
        sid = seg.get("id") or "?"
        beats = seg.get("beats")
        if not beats:
            continue
        sents = seg.get("sentences") or []
        total += len(beats)
        n = len(sents)
        for i, beat in enumerate(beats):
            on = beat["on"]
            if on >= n:
                errs.append(
                    f"段落 '{sid}' 的 beats[{i}] on={on} 越界：这一段的最终时间轴上"
                    f"只有 {n} 句旁白（句序从 0 起，最大 {n - 1 if n else '无'}）。"
                    "重分句后句数变少时最容易撞上这条——要么把 on 改到真实的"
                    "那一句，要么删掉这一拍")
                continue
            text = sents[on].get("text") or ""
            cnt = sentence_terminal_count(text)
            if cnt >= MANY_SENTENCE_PUNCT:
                warns.append(
                    f"段落 '{sid}' 的 beats[{i}] 绑在第 {on} 句，而那一句里有 "
                    f"{cnt} 个终止标点（{text[:20]}…）："
                    "一句念 3-6 秒，画面只能对上其中一个结论。"
                    "在句末断开把这句拆成两句，再把 on 指到该指的那一句")
        # 台词板声明了拍、该段却一条补间都没有：整张表就是愿望清单。这是台词板
        # 最容易退化成的东西，而且比不写更坏——作者以为写了。判据只看"有没有"，
        # 不看"对不对"：具体哪条属性从多少补到多少，由 images.json 的契约把关。
        if images is not None and not _declares_motion(images, sid):
            warns.append(
                f"段落 '{sid}' 写了 {len(beats)} 拍，但 images.json 里这一段没有"
                f"任何 animate 补间——「{beats[0]['change'][:24]}」只写在"
                "台词板上，画面不会动。台词板是意图层，实现要落到 images.json 的 "
                "animate.steps；这一段不想演就把 beats 删掉")
    return errs, warns, total


def count(manifest):
    """全片台词板总拍数（0 = 这一稿没写台词板）。生产报告里的数字用它。"""
    return sum(len(seg.get("beats") or [])
               for seg in manifest.get("segments") or [])

def _declares_motion(images, sid):
    """images.json 里这一段声明了补间没有。

    只认 `animate`（图内编排：属性补间与参数推进同一个字段）。`stage: keep` 只搬上一
    页的收尾态、本身不产生新的拍，所以不算——写了 keep 而没有 steps 的段落，台词板照样
    落空。
    """
    entry = images.get(sid)
    if not isinstance(entry, dict):
        return False
    return bool((entry.get("animate") or {}).get("steps"))


