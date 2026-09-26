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
from _contracts import CHART_TYPES, is_valid_sid  # 段 id 与图表类型的口径单一来源

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
        raise ValueError(f"chart id 非法: {cid!r}（须字母开头，"
                         f"仅字母/数字/-/_，1–64 字符）")
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
            for i in range(points):
                x = x_min + (x_max - x_min) * i / (points - 1)
                try:
                    y = float(_eval_ast(tree.body, _curve_ns(x)))
                except (ArithmeticError, ValueError, OverflowError,
                        ZeroDivisionError, RecursionError):
                    # RecursionError 只可能来自逐点求值阶段的超深 AST
                    # （校验阶段的嵌套深度守卫在 walk 里）；该采样点放弃即可，
                    # 其余点仍可成线。
                    y = None
                samples.append({"x": x, "y": y}
                               if y is not None and math.isfinite(y)
                               else {"x": x, "y": None})
            if not any(p["y"] is not None for p in samples):
                raise ValueError(
                    f"chart {cid!r}: curves[{idx}]（expr={expr!r}）"
                    f"在整个采样区间上没有有限值")
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

    既有行为是整文件覆盖：同一期视频里方式 C 与方式 A/B/D 混用时
    （image_options.md——"三种方式同一期视频里混用很常见"），后跑
    gen_charts 会把已定稿的真实照片/生图条目连同 provider/source_url 等
    provenance 元数据一起静默清掉，与 SKILL.md"恢复素材时不得覆盖这些
    元数据"的规则矛盾。合并语义：
      · 既有条目原样保留（含 provenance 字段）；
      · chart 条目新增；
      · 与上一次 gen_charts 产物语义相等（dict 比较，键序/缩进无关）的
        chart 条目 → 幂等覆盖；
      · 同一个 key 上既有非 chart 条目（或内容不同的 chart）→ 报错。
        两种来源争同一段配图必须人工裁决，不能默认任何一边赢。
    """
    merged = dict(existing or {})
    for sid, media in charts_map.items():
        if sid in merged and merged[sid] != media:
            raise ValueError(
                f"chart id '{sid}' 与既有 images.json 条目冲突：该段已有"
                f"配图映射（{json.dumps(merged[sid], ensure_ascii=False)[:80]}…）。"
                f"请换一个 chart id，或确认后先删除既有条目再重跑。")
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
        with open(args.input, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        charts_map = build_images_map(data)
        # 合并写回（不再整文件覆盖）：目标文件已有条目（方式 A/B/D 配图
        # 及其 provenance 元数据）原样保留，只新增/幂等更新 chart 条目。
        existing = {}
        if os.path.exists(args.output):
            try:
                with open(args.output, "r", encoding="utf-8-sig") as f:
                    existing = json.load(f)
            except (OSError, ValueError) as exc:
                # （json.JSONDecodeError 是 ValueError 子类，无需单列）
                print(f"[error] 目标 {args.output} 无法读取，拒绝合并写回"
                      f"（文件可能被截断，请先修复或删除它）: {exc}",
                      file=sys.stderr)
                return 1
            if not isinstance(existing, dict):
                print(f"[error] 目标 {args.output} 顶层不是 JSON 对象，"
                      f"拒绝合并写回（images.json 需为 {{segment_id: 映射}}）",
                      file=sys.stderr)
                return 1
        result = merge_images_map(existing, charts_map)
        # 走原子写：images.json 是下游 run.py/gen_hyperframes 的必读输入，
        # 写到一半被中断会留下截断 JSON，报错堆栈跟"上次写入没完成"这个
        # 真实原因毫无关系。全包其余落盘点都用同一套原子写，这里不再例外。
        write_json_atomic(args.output, result, indent=2)
        kept = len(existing)
        for sid, media in charts_map.items():
            print(f"[ok] {sid} ({media['chart']['type']}) -> {args.output}",
                  file=sys.stderr)
        if kept:
            print(f"[ok] 保留既有条目 {kept} 个（合并写回，不覆盖）",
                  file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
