"""视觉模板。模板数据直接内联为模块常量，支持竖屏 3:4（1080×1440）与
横屏 16:9（1920×1080）两种画幅，布局参数分别挂在 layout.vertical /
layout.landscape 下（canvas、subtitle 同理）。

注意：_TEMPLATE_JSON 是**严格 JSON**（json.loads 直解），内部一律不能写
`#` / `//` 注释——写了整份模板在模块加载时就崩。要给某个数值留说明，
就把话写到它唯一的消费者那一侧（版式几何 → templates/composition.css，
组装逻辑 → html_renderer.py）。
"""
import copy
import json

_TEMPLATE_PARSED = None


def load_template():
    """返回视觉模板配置的深拷贝（竖屏 vertical / 横屏 landscape 双画幅）。

    模板数据内嵌为常量，缺失/结构非法在模块加载即暴露（不静默回退）。
    调用方拿到的是深拷贝，修改不会污染缓存。
    """
    global _TEMPLATE_PARSED
    if _TEMPLATE_PARSED is None:
        _TEMPLATE_PARSED = json.loads(_TEMPLATE_JSON)
    return copy.deepcopy(_TEMPLATE_PARSED)


def get_canvas(aspect="vertical"):
    """按画幅返回画布尺寸 (width, height)。"portrait" 归一化为 vertical。"""
    if aspect == "portrait":
        aspect = "vertical"
    if aspect not in ("vertical", "landscape"):
        raise ValueError(f"[template] 未知画幅 {aspect!r}（可用: vertical/landscape）")
    canvas = load_template()["canvas"].get(aspect)
    if not isinstance(canvas, dict) or "width" not in canvas or "height" not in canvas:
        raise ValueError(f"[template] canvas.{aspect} 缺少 width/height")
    width = int(canvas["width"]); height = int(canvas["height"])
    if width <= 0 or height <= 0:
        raise ValueError(f"[template] canvas.{aspect} 必须为正整数")
    return width, height


_TEMPLATE_JSON = r'''
{
  "canvas": {
    "vertical": {
      "width": 1080,
      "height": 1440
    },
    "landscape": {
      "width": 1920,
      "height": 1080
    }
  },
  "layout": {
    "vertical": {
      "segCard": {
        "padding": "50px"
      },
      "verse": {
        "windowHeight": 280,
        "bottom": 50,
        "clipPad": 40,
        "linePad": 5,
        "lineHeight": 1.5,
        "activeRule": 0.12
      },
      "title": {
        "top": 80,
        "fontSize": 72,
        "maxLines": 2,
        "glow": 40
      },
      "tagline": {
        "fontSize": 32,
        "marginTop": 8,
        "lineHeight": 1.25,
        "indent": 16,
        "tickWidth": 8
      },
      "image": {
        "width": 980,
        "height": 735,
        "bottomGapToVerse": 50,
        "borderRadius": 12,
        "marginSide": 0,
        "glow": 40
      },
      "subtitle": {
        "fontSize": 40
      },
      "progressBar": {
        "height": 4
      },
      "grid": {
        "size": 60
      },
      "agenda": {
        "_meta": "开屏/结尾纯文字 agenda（竖屏）：全高 flex 列（题头在顶、agenda 紧跟题头、句子流锚底），上限 7 行。",
        "insetX": 72,
        "insetTop": 88,
        "insetBottom": 96,
        "titleSize": 84,
        "titleLineHeight": 1.1,
        "titleMarginTop": 18,
        "listMarginTop": 16,
        "kickerSize": 24,
        "idxSize": 30,
        "idxMinWidth": 52,
        "nameSize": 42,
        "durSize": 26,
        "rowGap": 10,
        "rowPad": 20,
        "verseMaxWidth": 936,
        "maxRows": 7,
        "nameTrim": 20,
        "titleGlow": 48
      }
    },
    "landscape": {
      "verse": {
        "windowHeight": 260,
        "clipPad": 40,
        "linePad": 5,
        "lineHeight": 1.45,
        "activeRule": 0.12
      },
      "title": {
        "fontSize": 62,
        "lineHeight": 1.15,
        "guardChars1": 16,
        "guardSize1": 54,
        "guardChars2": 22,
        "guardSize2": 48,
        "glow": 40
      },
      "tagline": {
        "fontSize": 28,
        "marginTop": 16,
        "lineHeight": 1.3,
        "indent": 22,
        "tickWidth": 8
      },
      "image": {
        "width": 1067,
        "height": 800,
        "borderRadius": 12,
        "glow": 40
      },
      "textCol": {
        "width": 613,
        "gap": 56
      },
      "margin": 96,
      "columnGap": 48,
      "subtitle": {
        "fontSize": 33
      },
      "progressBar": {
        "height": 5
      },
      "grid": {
        "size": 60
      },
      "agenda": {
        "_meta": "开屏/结尾纯文字 agenda（横屏）：全幅 flex 列，题头在顶、agenda 紧跟题头、句子流锚底 max-width 900，上限 7 行（与竖屏一致）——标题锁 1 行不预留第二行 + 紧行距（rowPad 8 / titleMarginTop 12）换出第 6/7 行预算，实测 kicker+单行标题+7 行+定高句子流不挤。insetX 96 / insetBottom 72（句子流下移），insetTop 单独收到 80：列顶比两侧再高 16px，整块空白让给下方行列表，行数吃满时行距仍有余量（代价是 agenda 卡顶不再与左右 96 对齐；内容段走 --ctv-l-margin，不受影响）。",
        "insetX": 96,
        "insetTop": 80,
        "insetBottom": 72,
        "titleSize": 96,
        "titleLineHeight": 1.12,
        "titleMarginTop": 12,
        "listMarginTop": 16,
        "kickerSize": 26,
        "idxSize": 34,
        "idxMinWidth": 60,
        "nameSize": 40,
        "durSize": 24,
        "rowGap": 6,
        "rowPad": 8,
        "verseMaxWidth": 900,
        "maxRows": 7,
        "nameTrim": 26,
        "titleGlow": 48
      }
    }
  },
  "animation": {
    "titleEntrance": {
      "type": "scale",
      "from": 0.5,
      "duration": 0.5,
      "ease": "back.out(1.7)"
    },
    "segmentFadeIn": {
      "duration": 0.3,
      "minDuration": 0.6,
      "ease": "power2.out"
    },
    "segmentFadeOut": {
      "duration": 0.3,
      "ease": "power2.in"
    },
    "firstSegmentFadeIn": {
      "duration": 0.4
    },
    "entranceBudget": {
      "_meta": "入场动效时长的段长归一化：factor = clamp(d / normSeconds, minFactor, 1.0)，短段压缩、长段维持原速（html_renderer 消费）。",
      "minFactor": 0.45,
      "normSeconds": 4.0
    },
    "imageEntrance": {
      "duration": 0.8,
      "ease": "power2.out",
      "startDelay": 0.2,
      "vert_y": 40
    }
  },
  "typography": {
    "fontFamily": "\"Microsoft YaHei\", \"PingFang SC\", \"Noto Sans SC\", \"Noto Sans CJK SC\", \"Noto Sans CJK JP\", sans-serif",
    "monoStack": "ui-monospace, SFMono-Regular, Consolas, \"Courier New\", monospace",
    "titleWeight": 900,
    "taglineWeight": 600,
    "titleLineHeight": 1.32,
    "titleTracking": "-0.02em"
  },
  "subtitle": {
    "_meta": "字幕切分参数（_script_utils.subtitle_params_for 消费）。maxChars=单行目标宽度（字符，竖屏物理容量 980px/40px≈24.5 字、保守取 22，横屏按左栏 613px/33px≈18 字）；hardCap=次要标点切不动时的字符级硬切上限；cueMaxLines=整句渲染故 99。",
    "vertical": {
      "maxChars": 22,
      "hardCap": 22,
      "cueMaxLines": 99
    },
    "landscape": {
      "maxChars": 18,
      "hardCap": 18,
      "cueMaxLines": 99
    }
  },
  "formulaCard": {
    "_meta": "C2 公式文本卡（chart type=formula，html_renderer 消费）。标题/公式字号与卡片内边距统一由模板控制。",
    "padding": 48,
    "titleSize": 30,
    "titleOpacity": 0.82,
    "titleMarginBottom": 28,
    "valueSize": 52
  },
  "chart": {
    "_meta": "Chart.js 视觉参数（html_renderer._build_chart_boot 编译成 options）。刻意没有 animation 项：图表在页面 load 时创建，自身补间走浏览器墙钟，与口播时间轴无关——渲染器逐帧 seek 时同一秒可能抓到半张图，所以一律关掉，入场观感交给段卡的 cross-fade。",
    "titleFontSize": 40,
    "legendFontSize": 30,
    "tickFontSize": 34,
    "axisTitleFontSize": 30,
    "axisTitlePaddingFactor": 0.7,
    "layoutPadding": 24,
    "curvePointRadius": 0,
    "curveBorderWidth": 5,
    "curveTension": 0.2,
    "scatterPointRadius": 8,
    "datasetBorderWidth": 3,
    "datasetTension": 0.25
  }
}
'''

