"""视觉模板。模板数据直接内联为模块常量，支持竖屏 3:4（1080×1440）与
横屏 16:9（1920×1080）两种画幅，布局参数分别挂在 layout.vertical /
layout.landscape 下（顶层只有 canvas 与 layout 按画幅分块）。

注意：_TEMPLATE_JSON 是**严格 JSON**（json.loads 直解），内部一律不能写
`#` / `//` 注释——写了整份模板在模块加载时就崩。要给某个数值留说明，
就把话写到它唯一的消费者那一侧（版式几何 → _assets/composition.css，
组装逻辑 → html_renderer.py）。
"""
import contextlib
import copy
import functools
import json

# 图内文字的可读下限（px，成片折算后的最终字号）。单一真源：check_svg 的静态自查、
# gen_hyperframes 的画布页自查、html_renderer 的 agenda 长标题降字号都判这一条。
# 三处各写一份 26 的后果是"改了 check_svg 的数、另两处仍按旧数判"，作者按提示把字
# 调到能读，另一页照样被判超标——三道门给三个答案，没法照着改。
MIN_READABLE_FONT_PX = 26

# 焦点景深的糊掉线（em，相对该行自己的字号）：离焦最深那一档的模糊半径不得越过它。
# 与 MIN_READABLE_FONT_PX 是同一类判据的两个方向——那一条管"缩到读不出来"，这一条
# 管"虚到读不出来"。CJK 正文（字重 600）的笔画宽度约字号的 1/14 ≈ 0.07em，高斯半径
# 逼近笔画宽度时相邻笔画就并成一片，所以线画在它之前。
# 运行时固定封两档，于是这条线在生成期被翻译成"subtitle.focus.blur 的两倍不得越过
# 它"——**只登记线，不登记档数**：档数由线推出来（floor(0.05 / 0.02) = 2），改了 blur
# 却忘了改档数是可能的，改了 blur 却让两档越过线会被生成期拦下。
FOCUS_BLUR_MAX_EM = 0.05

# ── 没有布局引擎时的两条几何口径 ────────────────────────────────────
# 静态自查（`check_svg` 的越界/空带、`_cam_crop` 的运镜切边、`gen_hyperframes`
# 的配图比例）都在没有浏览器的前提下量 SVG，所以"这块图元占哪儿"只能是近似式。
# 近似式必须由一处定义：此前`<text>` 的竖向占位在四个地方各写一遍 `y-fs, y+fs*0.3`，
# 而读不到 font-size 时的兜底字号一边是 MIN_READABLE_FONT_PX（26）、另一边是字面量
# 17.0——同一张图两道门算出差 9px 的占位盒，一道报越界、一道说没事。
# 比例容差同样两档并存过（0.01 与 0.002，差 5 倍）：check_svg 放过的图被
# gen_hyperframes 判 error，"照着自查改完还是过不去"。
TEXT_ASCENT_RATIO = 1.0     # 基线上方算一个字身
TEXT_DESCENDER_RATIO = 0.3  # 基线下方留一点下伸部（g/y/j 的尾巴）
# 等比判据的相对容差：cover 靠裁边填满框，失配多少就裁掉多少，取严的那一档
# （原 gen_hyperframes 的 0.002）——自查工具与最终门禁必须给同一个答案。
SVG_ASPECT_TOL = 0.002


def svg_text_band(y, font_size=None):
    """`<text>` 在 y 轴上的占位区间 (y0, y1)：基线上方一个字身、下方一点下伸部。

    font_size 读不出（写成 class / em / 相对单位）时按 MIN_READABLE_FONT_PX 兜底——
    取**能读的下限**而不是随便一个数：兜底越保守，占位盒越大，"越界/被裁"越容易
    报出来；报了可以确认是误报，漏了就是成片里真的少一行字。
    """
    fs = font_size if font_size else MIN_READABLE_FONT_PX
    return (float(y) - fs * TEXT_ASCENT_RATIO, float(y) + fs * TEXT_DESCENDER_RATIO)


def same_aspect(w, h, box):
    """SVG 固有尺寸 (w,h) 与目标框 box=(bw,bh) 是否等比（容差 SVG_ASPECT_TOL）。

    两边都按"交叉相乘"写，避免浮点除法写法不一带来的第二重口径差：
    |w·bh − h·bw| / (bw·bh) ≤ tol。
    """
    bw, bh = float(box[0]), float(box[1])
    if not (w and h and bw and bh):
        return False
    return abs(float(w) * bh - float(h) * bw) <= SVG_ASPECT_TOL * bw * bh


@functools.lru_cache(maxsize=1)
def _template_parsed():
    return json.loads(_TEMPLATE_JSON)


# --set 的覆写层：只在 CLI 这一次进程里生效，不落盘、不改常量。
# 进程级（而不是挂在 load_template 的参数上）是刻意的——模板的消费面有 5 个模块
# 共十几个调用点（canvas_kit / check_svg / html_renderer / gen_hyperframes /
# _source_schema），要求人一路把覆写传下去，必然有一处漏传：预览看着改了、渲染
# 没改，或者反过来。覆写只该有一个入口和一个出口。
# 键是先查过的叶子路径（见 put_override），消费侧读到的一定是合法值。
_OVERRIDES = {}


def _leaves(obj, prefix):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else k)
    else:
        yield prefix, obj


def _scalar_kind(value):
    """标量的粗类：数字 / 布尔 / 字符串。

    int 与 float 归同一类（`0` 和 `0.35` 都是"数字"，写 `0` 不该被判成类型不符）；
    bool 单列——它在 Python 里是 int 的子类，但塞进数字位会让 float(True) 静默
    变成 1.0。
    """
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, (int, float)):
        return "数字"
    if isinstance(value, str):
        return "字符串"
    return type(value).__name__


def put_override(assignment):
    """把一条 `a.b.c=值` 记进覆写层。路径必须是模板里现成的叶子，值必须是 JSON 标量。

    两条都当场报错，因为两种写错的后果都是"以为改了、其实没改"：路径拼错时
    消费侧 `["fontFamilyy"]` 会 KeyError（报在几层之外、看不出是 CLI 拼错），
    值写成 `bold` 这种裸词则会在 CSS 里悄悄地变成非法声明。想加新键请先改
    `_TEMPLATE_JSON`——那是模板唯一的真源，CLI 只搬已有的数。
    """
    path, sep, raw = assignment.partition("=")
    path = path.strip()
    if not sep or not path:
        raise ValueError(f"--set 要写成 键=值（实际: {assignment!r}），如 --set "
                         f"animation.animate.duration=0.8")
    leaves = dict(_leaves(_template_parsed(), ""))
    if path not in leaves:
        # 候选按"路径段重叠"排，而不是子串包含：拼错通常是段内错一个字母
        # （duratoin），子串匹配在那里一条都命中不了。
        want = path.lower()
        head = want.rsplit(".", 1)[0]

        def distance(candidate):
            # 公共前缀越长越像；再按长度差收尾（同目录下挑最接近的那条）
            k = candidate.lower()
            i = 0
            while i < min(len(k), len(want)) and k[i] == want[i]:
                i += 1
            return (-i, abs(len(k) - len(want)), k)

        pool = [k for k in leaves if k.lower().startswith(head)] or leaves
        near = sorted(pool, key=distance)[:3]
        raise ValueError(
            f"--set 的键 {path!r} 不在模板里。"
            + (f"你是想写 {' / '.join(near)}？" if near else
               "可用键见 scripts/_template.py 的 _TEMPLATE_JSON（只认叶子路径）"))
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError(f"--set {path}={raw} 的值读不出来——照 JSON 写："
                         f"数字 0.8、布尔 true/false、字符串要带引号 \"…\"")
    old = leaves[path]
    if isinstance(value, (dict, list)) or value is None:
        raise ValueError(f"--set {path}={raw} 只能改标量（数字/布尔/字符串）——"
                         f"整块替换是改模板，请直接改 scripts/_template.py 的 "
                         f"_TEMPLATE_JSON")
    # 类型换了也是"以为改了其实没改"：bool 塞进数字位（`relay=true`）时
    # float(True) 会静默变成 1.0，写进 CSS 的却是 `True` —— 运行期 parseFloat 读
    # 不出来、回落兜底，成片里演的是第三个值。
    # int/float 互通（`0` 与 `0.35` 是同一类），bool 单列：JSON 里它是 int 的子类，
    # 但语义上是另一回事。
    if _scalar_kind(value) != _scalar_kind(old):
        raise ValueError(
            f"--set {path}={raw} 的类型对不上：模板里是 {_scalar_kind(old)}，"
            f"给的是 {_scalar_kind(value)}（照 JSON 写：数字 0.8、布尔 true、"
            f"字符串要带引号 \"…\"）")
    _OVERRIDES[path] = value


def overrides():
    """本次进程生效的覆写（只读副本，给报告与提示用）。"""
    return dict(_OVERRIDES)


@contextlib.contextmanager
def override_layer(pairs):
    """`with override_layer(["a.b=1"])`：整块进出，跨库调用（generate_html）时用它。

    用入口把 CLI 的 --set 记进覆写层是另一条路（run.py 那种一个进程跑完的）。
    两条路口径只能有一份实现，所以 both 最终都落到 `_OVERRIDES`。
    """
    saved = dict(_OVERRIDES)
    try:
        for pair in pairs or ():
            put_override(pair)
        yield overrides()
    finally:
        _OVERRIDES.clear()
        _OVERRIDES.update(saved)


def load_template():
    """返回视觉模板配置的深拷贝（竖屏 vertical / 横屏 landscape 双画幅）。

    模板数据内嵌为常量，缺失/结构非法在模块加载即暴露（不静默回退）。
    调用方拿到的是深拷贝，修改不会污染缓存；`override_layer` 里读到的已含覆写。
    """
    tpl = copy.deepcopy(_template_parsed())
    for path, value in _OVERRIDES.items():
        node = tpl
        *head, last = path.split(".")
        for k in head:
            node = node[k]
        node[last] = value
    return tpl



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


def get_image_box(aspect):
    """按画幅返回**槽位框**尺寸 (width, height)——4:3 的那一块。

    与 get_canvas 是两件事：整页画布（`layout: "canvas"`）铺的是整个画幅，走
    get_canvas；槽位版式的配图只占中间那一块 4:3，尺寸挂在 layout 下。"素材够
    不够大"要按它实际被铺进的那个框来判，所以这两个数都得有单一取值口。
    """
    box = load_template()["layout"][normalize_aspect(aspect)]["image"]
    return int(box["width"]), int(box["height"])


def seg_card_pad_x():
    """竖屏卡片（layout.vertical.segCard）的**左右**内边距（px，float）。

    CSS padding 简写的横向值：3 值=上/左右/下、2 值=上下/左右，只有一个值时四边
    共用。只管竖屏——横屏是弹性列，没有 segCard.padding 这个键（`html_renderer`
    分画幅几何那段的注释记着为什么不给它放 0 值占位）。

    两处消费同一个数：`html_renderer` 拿它算画布宽并与 `image.width` 对账（模板
    漂移就在这里拦），`check_svg` 拿它当 SVG 的安全边距。修复前两边各写一份
    `.split()[1] if len(parts) >= 2 else parts[0]` 的解析，`check_svg` 只靠注释
    声称"与 html_renderer 读同一键同一口径"——模板改写法时只有一边跟着变，于是
    "安全边距"和"真实内容盒"悄悄分家。check_svg 是第 3 步唯一的机器门禁，它算
    错的后果不是报错而是**放行错的图**。
    """
    raw = load_template()["layout"]["vertical"]["segCard"]["padding"]
    parts = raw.split()
    if not parts:
        raise ValueError("[template] layout.vertical.segCard.padding 不能为空")
    try:
        return float((parts[1] if len(parts) >= 2 else parts[0]).replace("px", ""))
    except ValueError as e:
        raise ValueError(
            "[template] layout.vertical.segCard.padding 必须是合法 CSS 长度") from e


# layout.vertical.image.compareGap：并置对比（layout:"compare"）两格之间的缝隙。
# 不能借 marginSide（图槽离页面左右边的外边距，竖屏为 0）也不能借
# bottomGapToVerse（图槽离句子流的距离）——两者都不是"两格之间"。缝隙太小
# 两格读成"一张图被划了一刀"，太大又让每格变窄、图变小。
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
        "shadePadRight": "-10px",
        "shade": "block",
        "clipPad": 40,
        "linePad": 5,
        "lineHeight": 1.5,
        "activeRule": 0.12,
        "whoFontSize": 28,
        "whoGap": 12
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
        "glow": 40,
        "compareGap": 14
      },
      "subtitle": {
        "fontSize": 40,
        "relay": 0.32
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
        "alpha": 12,
        "edge": 78,
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
        "shadePadRight": "0px",
        "shade": "none",
        "clipPad": 40,
        "linePad": 5,
        "lineHeight": 1.45,
        "activeRule": 0.12,
        "whoFontSize": 24,
        "whoGap": 10
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
        "glow": 40,
        "compareGap": 14
      },
      "textCol": {
        "width": 613,
        "gap": 56
      },
      "margin": 96,
      "columnGap": 48,
      "subtitle": {
        "fontSize": 33,
        "relay": 0.32
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
        "alpha": 11,
        "edge": 78,
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
  "subtitle": {
    "focus": {
      "future": 0.62,
      "past": 0.45,
      "ratio": 0.726,
      "scale": 0.94,
      "blur": 0.02
    },
    "scroll": {
      "ease": "sine.out"
    },
    "relay": {
      "from": 0.35
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
  "plate": {
    "vertical": {
      "captionInsetX": 64,
      "captionInsetBottom": 84,
      "captionMaxLines": 2,
      "captionFontSize": 42,
      "captionLineHeight": 1.34,
      "captionWeight": 600,
      "captionScrimHeight": 300,
      "captionSlotGapFrac": 0.35
    },
    "landscape": {
      "captionInsetX": 96,
      "captionInsetBottom": 64,
      "captionMaxLines": 2,
      "captionFontSize": 36,
      "captionLineHeight": 1.34,
      "captionWeight": 600,
      "captionScrimHeight": 220,
      "captionSlotGapFrac": 0.35
    }
  },
  "animation": {
    "titleEntrance": {
      "duration": 0.5,
      "ease": "power3.out",
      "rhythm": [
        {"y": 44, "ease": "power4.out"},
        {"y": 56, "ease": "power3.out"},
        {"y": 34, "ease": "power3.out"},
        {"y": 62, "ease": "expo.out"}
      ],
      "tagline": {
        "delayFrac": 0.45,
        "durationFrac": 0.75,
        "ampFrac": 0.6,
        "ease": "power2.out"
      },
      "kicker": {
        "delayFrac": 0.3,
        "durationFrac": 0.9,
        "easeFrac": 0.6,
        "ease": "power2.out"
      }
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
    "entranceLadder": {
      "step": 0.14
    },
    "staggerDistribute": {
      "ease": "power2.out"
    },
    "imageEntrance": {
      "duration": 0.8,
      "ease": "power2.out",
      "vert_y": 40,
      "arc": {
        "x": 16,
        "ease": "sine.inOut",
        "rhythm": [
          {"dir": 1},
          {"dir": -1}
        ]
      },
      "frameFrac": 0.55,
      "settle": {
        "startFrac": 0.25,
        "scale": 1.06,
        "panX": 0.0,
        "panY": 0.6,
        "ease": "power2.out"
      },
      "rhythm": [
        {"scale": 1.06, "panX": 0.0, "panY": 0.6, "ease": "power2.out"},
        {"scale": 1.05, "panX": 0.7, "panY": 0.15, "ease": "power2.out"},
        {"scale": 1.08, "panX": 0.1, "panY": 0.1, "ease": "power1.out"},
        {"scale": 1.05, "panX": -0.6, "panY": 0.25, "ease": "power3.out"}
      ]
    },
    "animate": {
      "duration": 0.5,
      "ease": "power2.out"
    },
    "opening": {
      "apple": {
        "title": {
          "chars": {
            "delay": 0.0,
            "stagger": 0.04,
            "duration": 0.55,
            "y": 18,
            "scale": 1.04,
            "blur": 12,
            "ease": "power3.out",
            "mask": {
              "window": 0.05,
              "duration": 0.5,
              "blur": 9,
              "rise": 0.34,
              "ease": "expo.out"
            }
          }
        },
        "kicker": {
          "blur": 8,
          "tracking": 0.28,
          "rest": 0.14,
          "delay": 0.35,
          "duration": 0.8,
          "ease": "power2.out"
        },
        "rows": {
          "idx": {
            "delay": 0.15,
            "duration": 0.45,
            "scale": 1.25,
            "blur": 6,
            "ease": "power2.out"
          },
          "content": {
            "delay": 0.3,
            "duration": 0.55,
            "x": 10,
            "ease": "power2.out"
          },
          "window": 0.3
        },
        "halo": {
          "in": 1.2,
          "opacity": 0.5,
          "breatheTo": 0.32,
          "breatheDur": 1.3,
          "glow": {
            "delay": 0.3,
            "duration": 2.2,
            "x": 90,
            "y": 60,
            "scale": 1.35,
            "opacity": 0.5,
            "breatheTo": 0.28,
            "breatheDur": 2.6
          }
        },
        "budget": {
          "minFactor": 0.5
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

