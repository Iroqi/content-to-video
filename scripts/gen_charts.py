#!/usr/bin/env python3
"""Build Chart.js-backed chart entries for images.json."""
import argparse
import ast
import json
import math
import os
import sys

sys.dont_write_bytecode = True  # 导入同目录模块别往 scripts/__pycache__ 落 .pyc（技能目录不留制作残渣）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import setup_stdio, write_json_atomic, guard_not_in_skill_dir
from _contracts import (CHART_TYPES, is_valid_sid, load_images_json,
                        SID_RULE, _read_json_file)

# 公式求值的安全护栏：AST 白名单挡的是"能力"（不出网络/属性/下标/导入），
# 挡不住"规模"——`9**9**9` 每个节点都合法，却要算到宇宙热寂。Pow 结果一旦
# 溢出 IEEE754 就会是 inf，直接拦下来报错；指数本身也限幅，避免 int 幂
# 在 Python 里做任意精度大数乘法而长时间占住 CPU（外层 try 接不住"算得慢"，
# 只会表现为进程假死，而 charts.json 是 agent 手写的，笔误概率不低）。
_MAX_POW_EXPONENT = 64
# 指数限幅只挡住单层 **：`((((9**64)**64)**64)...)` 每层指数都合法，靠嵌套
# 滚出百万/十亿位大数，同样是"算得慢、进程假死"。限住每步整数中间值的位数
# （4096 位 ≈ 10^1233，画任何正经曲线都用不到），嵌套再深也在个位毫秒内失败。
_MAX_INT_BITS = 4096
# 表达式长度上限：采样要逐点求值最多 400 次，超长表达式是另一条白名单内
# DoS 路径；图表语义的表达式实测没有接近这个量级的。
_MAX_EXPR_CHARS = 400


def _guard_int_size(value, where):
    if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
        raise ValueError(f"{where}: 整数中间值超过 {_MAX_INT_BITS} 位上限"
                         "（表达式数值增长过快，检查 ** 嵌套）")
    return value

# 曲线表达式白名单（名字 / 单参函数 / 求值命名空间）——原路径散在
# _safe_curve_eval 局部，拆出校验函数后提为模块级常量。
_CURVE_ALLOWED_NAMES = {"x", "pi", "e", "sqrt", "sin", "cos", "tan",
                        "exp", "log", "log2", "log10", "abs"}
_CURVE_ALLOWED_FUNCS = {name: getattr(math, name)
                        for name in _CURVE_ALLOWED_NAMES - {"x", "pi", "e", "abs"}}
_CURVE_ALLOWED_FUNCS["abs"] = abs


def _curve_ns(x):
    return {"x": x, "pi": math.pi, "e": math.e, **_CURVE_ALLOWED_FUNCS}


def _number(value, where):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{where} must be finite")
    return value


def _validate_points(chart):
    labels = chart.get("labels") or []
    values = chart.get("values") or []
    if not labels or len(labels) != len(values):
        raise ValueError(
            f"chart {chart.get('id')!r}: labels and values must be "
            f"non-empty and have equal length")
    values = [_number(v, "values") for v in values]
    if chart["type"] == "pie" and sum(values) <= 0:
        raise ValueError(
            f"chart {chart.get('id')!r}: pie values must sum to > 0")
    return values


def _validate_curve_tree(tree, expr):
    """AST 白名单校验（只查"能力"，不求值）。

    从 _safe_curve_eval 拆出：语法错误/非法节点是确定性的，必须在采样循环
    之前 fail-fast 报清病因——放在逐点 try 里会被降级成 y=None，最终只报
    "没有有效数据点"，真正的错误原因（写错表达式）丢失。
    """
    # 调用位上的函数名（sqrt(x) 的 sqrt）不算"把函数当数值用"，先记下来
    _call_funcs = {id(n.func) for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load, ast.Add, ast.Sub,
                             ast.Mult, ast.Div, ast.Pow, ast.Mod,
                             ast.UAdd, ast.USub)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            continue
        if isinstance(node, ast.Name) and node.id in _CURVE_ALLOWED_NAMES:
            if (id(node) not in _call_funcs
                    and node.id in _CURVE_ALLOWED_FUNCS):
                # 写成裸 `sqrt` 或 `sqrt(sin)`：求值返回的是函数对象，逐点 try
                # 会把它降级成 y=None，最后只剩一句"整个采样区间没有有限值"，
                # 真正的病因（函数没带参数调用）丢失。确定性错误，采样前拦。
                raise ValueError(
                    f"expression {expr!r}: {node.id} 是函数，要写成调用形式"
                    f" {node.id}(x)（例：sqrt(x)）")
            continue
        if (isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Mod, ast.Div))
                and isinstance(node.right, ast.Constant)
                and node.right.value == 0):
            # 字面量除零/模零（x % 0、x / 0.0）是确定性坏数据：在采样前
            # fail-fast 报清病因。运行时除零（如 x % x 在 x=0 处）由
            # _eval_ast 的 Mod 分支兜住，其余落进采样循环的 except 后只会
            # 得到模糊的"没有有效数据点"，病因丢失。
            # 必须排在下面的通用 BinOp 放行分支之前——ast.walk 先 yield 父节点，
            # 先 continue 就等于把这条检查变成死代码。
            raise ValueError(
                f"expression {expr!r}: {'modulo' if isinstance(node.op, ast.Mod) else 'division'} by constant 0")
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub,
                                                               ast.Mult, ast.Div,
                                                               ast.Pow, ast.Mod)):
            continue
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            continue
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _CURVE_ALLOWED_FUNCS and len(node.args) == 1
                and not node.keywords):
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitXor):
            # 实测最常踩的写法迁移错误：把幂运算写成 `^`。Python 里那是按位
            # 异或，落到下面的通用分支只会报 "node BinOp is not allowed"，
            # 看不出病因。
            raise ValueError(
                f"expression {expr!r}: 幂运算要用 **，`^` 在 Python 里是按位异或"
                f"（例：200 * 0.5 ** (x / 5.5)）")
        raise ValueError(f"expression {expr!r}: node {type(node).__name__} is not allowed")


def _eval_ast(node, ns):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        value = ns[node.id]
        # sqrt/sin 这些白名单条目是函数：写成 `sqrt` 而不是 `sqrt(x)` 会返回
        # builtin_function_or_method，采样循环的 float(...) 抛 TypeError——它既
        # 不在逐点 except 里、也不在 main 的错误出口里，整条命令炸裸栈。
        if not isinstance(value, (int, float)):
            raise ValueError(f"{node.id} 是函数，要写成调用形式 {node.id}(x)")
        return value
    if isinstance(node, ast.UnaryOp):
        value = _eval_ast(node.operand, ns)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        a = _eval_ast(node.left, ns)
        b = _eval_ast(node.right, ns)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return _guard_int_size(a * b, "乘法")
        if isinstance(node.op, ast.Div):
            return a / b
        if isinstance(node.op, ast.Pow):
            # 指数限幅：负指数是合法的（x**-1 画双曲线），只拦量级。
            if abs(b) > _MAX_POW_EXPONENT:
                raise ValueError(
                    f"exponent {b} exceeds |{_MAX_POW_EXPONENT}| limit")
            try:
                result = a ** b
            except OverflowError:
                raise ValueError("power result overflows float range")
            if isinstance(result, float) and not math.isfinite(result):
                raise ValueError("power result is not finite")
            return _guard_int_size(result, "幂运算")
        if isinstance(node.op, ast.Mod):
            # 除零（含运行时求值 b==0，如 x % x 在 x=0 处）是确定性坏数据，
            # 显式转 ValueError 而非让 ZeroDivisionError 穿透：ZeroDivisionError
            # 虽是 ArithmeticError 子类会被采样循环捕获降级，但逐点全部降级后
            # 只剩模糊的"没有有限值"，病因丢失；字面量模零已在 _validate_curve_tree
            # 静态拦截，这里兜住动态形态。
            if b == 0:
                raise ValueError("modulo by zero in curve expression")
            return a % b
    if isinstance(node, ast.Call):
        return ns[node.func.id](_eval_ast(node.args[0], ns))
    raise ValueError("unsupported expression")


def _normalize(chart):
    if not isinstance(chart, dict) or not chart.get("id"):
        raise ValueError(f"chart must be an object with an id: {chart!r}")
    chart = dict(chart)
    cid = str(chart["id"])
    # 段 id 口径唯一来源是 _contracts（is_valid_sid 包住 _SID_RE；与
    # images.json key、HTML/GSAP 选择器同一规则），这里不再自带副本。
    if not is_valid_sid(cid):
        raise ValueError(f"chart id 非法: {cid!r}（{SID_RULE}）")
    kind = str(chart.get("type") or "").lower()
    if kind not in CHART_TYPES:
        raise ValueError(f"chart {cid!r}: type must be one of {sorted(CHART_TYPES)}")
    chart["type"] = kind
    if kind in {"bar", "line", "pie"}:
        chart["values"] = _validate_points(chart)
    elif kind == "scatter":
        points = chart.get("points") or chart.get("values") or []
        if not points:
            raise ValueError(f"chart {cid!r}: scatter requires non-empty points")
        normalized_points = []
        for i, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"chart {cid!r}: scatter point {i} must be [x, y]")
            normalized_points.append([_number(point[0], "scatter.x"),
                                      _number(point[1], "scatter.y")])
        chart["points"] = normalized_points
        chart.pop("values", None)
        chart.pop("labels", None)
    elif kind == "formula":
        if not str(chart.get("formula") or "").strip():
            raise ValueError(f"chart {cid!r}: formula requires non-empty formula")
    elif kind == "curve":
        curves = chart.get("curves") or []
        if not curves:
            raise ValueError(f"chart {cid!r}: curve requires non-empty curves")
        x_min = _number(chart.get("x_min", 0), "x_min")
        x_max = _number(chart.get("x_max", 10), "x_max")
        if x_max <= x_min:
            raise ValueError(f"chart {cid!r}: x_max must be greater than x_min")
        # 显式 null 不能走 int(None)：main() 只捕 OSError/ValueError，
        # TypeError 会以裸栈穿透。缺省值仍按 80 处理。
        raw_points = chart.get("points")
        if raw_points is None:
            raw_points = 80
        try:
            points = int(raw_points)
        except (TypeError, ValueError):
            raise ValueError(f"chart {cid!r}: points must be an integer")
        if not 2 <= points <= 400:
            raise ValueError(f"chart {cid!r}: points must be 2..400")
        datasets = []
        # 每曲线一次 parse + fail-fast 白名单校验（语法错误/非法节点是确定性的，
        # 放在逐点 try 里会被降级成 y=None，最终只报"没有有效数据点"、丢掉真正
        # 病因），校验过的 AST 直接复用给采样循环：逐点再 _safe_curve_eval 等于
        # 同一表达式 parse+walk 400 遍，且预检与求值各走一份逻辑，改一处漏一处。
        for idx, curve in enumerate(curves):
            expr = str(curve.get("expr") or "").strip()
            if not expr:
                raise ValueError(f"chart {cid!r}: curves[{idx}] missing expr")
            if len(expr) > _MAX_EXPR_CHARS:
                raise ValueError(
                    f"chart {cid!r}: curves[{idx}] 表达式长 {len(expr)} 字符，"
                    f"超过上限 {_MAX_EXPR_CHARS}——采样要逐点整式求值，超长表达式"
                    "会把构建拖成假死；请化简或拆成多条曲线")
            try:
                tree = ast.parse(expr, mode="eval")
                _validate_curve_tree(tree, expr)
            except SyntaxError as e:
                raise ValueError(
                    f"chart {cid!r}: curves[{idx}] 表达式语法错误: "
                    f"{expr!r}（{e}）") from e
            except RecursionError as e:
                raise ValueError(
                    f"chart {cid!r}: curves[{idx}] 表达式嵌套过深: "
                    f"{expr!r}") from e
            samples = []
            first_err = None
            for i in range(points):
                x = x_min + (x_max - x_min) * i / (points - 1)
                try:
                    y = float(_eval_ast(tree.body, _curve_ns(x)))
                except (ArithmeticError, ValueError, RecursionError) as e:
                    # OverflowError/ZeroDivisionError 都是 ArithmeticError 的
                    # 子类，不必点名。RecursionError 只可能来自逐点求值阶段的
                    # 超深 AST（校验阶段的嵌套深度守卫在 walk 里）；
                    # 该采样点放弃即可，其余点仍可成线。
                    if first_err is None:
                        first_err = str(e)
                    y = None
                samples.append({"x": x, "y": y}
                               if y is not None and math.isfinite(y)
                               else {"x": x, "y": None})
            if not any(p["y"] is not None for p in samples):
                # 带上首个求值报错：守卫（幂指数/位宽上限）在逐点 try 里被降级成
                # y=None，若不回传就只剩"没有有限值"，用户看不出自己是 ** 嵌套太深
                # 还是区间取错。
                raise ValueError(
                    f"chart {cid!r}: curves[{idx}]（expr={expr!r}）"
                    f"在整个采样区间上没有有限值"
                    + (f"；求值报错：{first_err}" if first_err else ""))
            datasets.append({
                "label": str(curve.get("label") or f"curve {idx + 1}"),
                "data": samples,
            })
        chart["curve_datasets"] = datasets
        chart.pop("curves", None)
    return chart


def build_images_map(data):
    charts = data.get("charts") if isinstance(data, dict) else None
    if not isinstance(charts, list) or not charts:
        raise ValueError("charts.json 的 'charts' 必须是非空数组")
    result = {}
    for chart in charts:
        normalized = _normalize(chart)
        cid = normalized["id"]
        if cid in result:
            raise ValueError(f"duplicate chart id: {cid}")
        result[cid] = {"type": "chart", "chart": normalized}
    return result


def merge_images_map(existing, charts_map):
    """把 chart 条目合并进既有 images.json 映射（不覆盖非 chart 条目）。

    整文件覆盖的旧语义在方式 C 与 A/B/D 混用时（同一期视频很常见）会把已定稿
    的真实照片/生图条目连同 provider/source_url 等 provenance 元数据一起静默
    清掉。合并语义：
      · 既有条目原样保留（含 provenance 字段）；
      · chart 条目新增；
      · 既有条目本身是 chart → 覆盖（与上次产物语义相等时就是幂等重跑，
        改了数值/标题后重跑则是"更新"）。chart 是 gen_charts 自己的命名空间，
        同一来源的更新不构成冲突；把它当冲突拦下来会逼着人去手改 images.json，
        而那份文件里正压着 A/B/D 路线不能丢的 provenance。
      · 同一个 key 上站着**非 chart** 条目（照片/生图/视频）→ 报错。
        两种来源争同一段配图必须人工裁决，不能默认任何一边赢。
    """
    merged = dict(existing or {})
    for sid, media in charts_map.items():
        prev = merged.get(sid)
        if prev is not None and prev != media and prev.get("type") != "chart":
            raise ValueError(
                f"chart id '{sid}' 与既有 images.json 条目冲突：该段已有"
                f"非图表配图（{json.dumps(prev, ensure_ascii=False)[:80]}…）。"
                f"两种来源争同一段配图需要人工裁决：请换一个 chart id，"
                f"或确认后先删除既有条目再重跑。")
        merged[sid] = media
    return merged


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(
        description="Generate Chart.js-backed images.json from charts.json")
    parser.add_argument("-i", "--input", required=True, help="charts.json 路径")
    parser.add_argument("-o", "--output", required=True, help="输出 images.json 路径")
    args = parser.parse_args()
    guard_not_in_skill_dir(("-o/--output", os.path.abspath(args.output)))
    try:
        data = _read_json_file(args.input)
        charts_map = build_images_map(data)
        # 合并写回（不再整文件覆盖）：目标文件已有条目（方式 A/B/D 配图
        # 及其 provenance 元数据）原样保留，只新增/幂等更新 chart 条目。
        existing = {}
        if os.path.exists(args.output):
            # 合并前先过同一份契约：images.json 由 gen_charts 与 A/B/D 路线共写，
            # 坏值（截断、顶层不是对象、裸字符串路径、null 条目）必须在这里就报对
            # 人——否则要么在 merge 里抛裸 AttributeError，要么被 [ok] 静默带着写回，
            # 等下游 gen_hyperframes 才炸，届时用户已以为是本次 chart 写坏了文件。
            # 缺文件/非 UTF-8/截断 JSON 三种读不出来的形态都由 load_images_json
            # 统一成带路径的 ValueError，本文件不再自己 open + json.load。
            try:
                existing = load_images_json(args.output)
            except ValueError as exc:
                print(f"[error] 既有 {args.output} 无法合并写回"
                      f"（请先修复或删除它）: {exc}", file=sys.stderr)
                return 1
        result = merge_images_map(existing, charts_map)
        # 走原子写：images.json 是下游 run.py/gen_hyperframes 的必读输入，
        # 写到一半被中断会留下截断 JSON，报错堆栈跟"上次写入没完成"这个
        # 真实原因毫无关系。全包其余落盘点都用同一套原子写，这里不再例外。
        write_json_atomic(args.output, result, indent=2)
        kept = len([k for k in existing if k not in charts_map])
        for sid, media in charts_map.items():
            print(f"[ok] {sid} ({media['chart']['type']}) -> {args.output}",
                  file=sys.stderr)
        if kept:
            print(f"[ok] 原样保留既有条目 {kept} 个（charts.json 未涉及的段）",
                  file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
