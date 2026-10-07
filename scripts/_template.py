"""视觉模板。模板数据直接内联为模块常量，支持竖屏 3:4（1080×1440）与
横屏 16:9（1920×1080）两种画幅，布局参数分别挂在 layout.vertical /
layout.landscape 下（顶层只有 canvas 与 layout 按画幅分块）。

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


def normalize_aspect(aspect):
    """画幅归一化的唯一口径："portrait" 是面向 CLI/调用方的别名，模板键名是
    vertical。未知画幅直接报错——静默兜到竖屏会让横屏稿件出竖屏尺寸。"""
    if aspect == "portrait":
        aspect = "vertical"
    if aspect not in ("vertical", "landscape"):
        raise ValueError(f"[template] 未知画幅 {aspect!r}（可用: portrait/vertical、landscape）")
    return aspect


def get_canvas(aspect):
    """按画幅返回画布尺寸 (width, height)。"""
    canvas = load_template()["canvas"][normalize_aspect(aspect)]
    return int(canvas["width"]), int(canvas["height"])


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
        "fontSize": 64,
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
      "ambience": {
        "rx": 58,
        "ry": 42,
        "cx": 50,
        "cy": 50,
        "alpha": 15,
        "edge": 72,
        "agendaCx": 50,
        "agendaCy": 50
      },
      "agenda": {
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
      "ambience": {
        "rx": 46,
        "ry": 52,
        "cx": 68,
        "cy": 50,
        "alpha": 13,
        "edge": 70,
        "agendaCx": 50,
        "agendaCy": 45
      },
      "agenda": {
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
  "canvasAmbience": {
    "rx": 135,
    "ry": 85,
    "cx": 50,
    "cy": 42,
    "alpha": 13,
    "edge": 82
  },
  "animation": {
    "titleEntrance": {
      "from": 0.5,
      "duration": 0.5,
      "ease": "back.out(1.7)"
    },
    "segmentWipe": {
      "style": "line",
      "duration": 0.28,
      "ease": "expo.out"
    },
    "propLine": {
      "thickness": 6,
      "duration": 0.40,
      "ease": "power2.inOut",
      "peelFrac": 0.30,
      "peelRotation": -4,
      "peelTilt": 12,
      "peelPerspective": 1000,
      "peelShadeFrac": 0.08,
      "peelEase": "power2.in"
    },
    "entranceBudget": {
      "minFactor": 0.45,
      "normSeconds": 4.0
    },
    "imageEntrance": {
      "duration": 0.8,
      "ease": "power2.out",
      "startDelay": 0.2,
      "vert_y": 40
    },
    "director": {
      "duration": 0.5,
      "ease": "power2.out"
    },
    "opening": {
      "apple": {
        "title": {
          "blur": 16,
          "scale": 1.06,
          "duration": 1.6,
          "ease": "power3.out"
        },
        "kicker": {
          "blur": 8,
          "delay": 0.4,
          "duration": 1.2,
          "ease": "power2.out"
        },
        "rows": {
          "delay": 0.7,
          "duration": 0.7,
          "stagger": 0.12,
          "y": 24,
          "ease": "power2.out"
        },
        "halo": {
          "in": 1.6,
          "opacity": 0.55,
          "breatheTo": 0.35,
          "breatheDur": 1.2
        }
      }
    }
  },
  "typography": {
    "fontFamily": "\"Microsoft YaHei\", \"PingFang SC\", \"Noto Sans SC\", \"Noto Sans CJK SC\", \"Noto Sans CJK JP\", sans-serif",
    "monoStack": "ui-monospace, \"SF Mono\", Consolas, \"Courier New\", monospace",
    "titleWeight": 900,
    "taglineWeight": 600,
    "titleLineHeight": 1.45,
    "titleTracking": "-0.02em"
  }
}
'''

