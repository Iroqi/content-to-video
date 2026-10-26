#!/usr/bin/env python3
"""`derive`：一条参数驱动的几何演绎——纯标准库，无第三方依赖。

## 要解决的那件事

`director` / `evolve` 的补间是**属性级**的：要变三个元素就写三条，各自写
`duration` / `ease` / 起点，靠作者保证它们"同一时刻、同一节奏"。3b1b 不是这么
做镜头的：一个场景有一个**主参数**（把 θ 从 0 转到 2π），所有几何都由它算出来，
所以"同步"是免费的。这个模块补的就是这一层：

    {"params": {"theta": 0},
     "steps":  [{"at": 1, "param": "theta", "to": 90, "duration": 1.2}],
     "bind":   [{"target": "#arm", "attr": "x2", "expr": "540 + 260*cos(theta*pi/180)"}]}

一拍只写参数，几何跟着走——而且**每一帧都按参数真值重算**，不是两条路径之间的
插值。圆心在圆上转走的是圆，不是弦：这是 `morph`（同拓扑插值）做不到的。

## 为什么不 eval

信源不可信是本技能的红线：表达式来自稿件，`eval` 即便清空 `__builtins__` 也能被
`().__class__.__bases__` 打穿。所以这里是自己写的 tokenizer + 递归下降，能算的东西
就是**算术 + 白名单一参函数**，多一个都没有。

## 输出形状

`compile_derive(block)` 一次编译，返回一个 `Derived`：

    d.value(name, t01)      # 某参数在进度 t01∈[0,1] 处的值（按它所属那一拍的起止与缓动）
    d.geometry(frame)       # frame = {参数名: 值} → [(selector, attr, 数值), ...]

两处调用方（渲染端的 GSAP 时间线、`check_svg` 的静态自查）读的是同一份编译结果，
所以"门禁算的"和"成片演的"不可能分家。
"""
import json
import math
import re

# 表达式里能用的名字。**只有这些**：多一个都要先在这里登记，并想清楚它在
# "逐帧 seek 必须可复现"这条约束下是否成立（所以没有 random、没有时间函数）。
_CONSTANTS = {"pi": math.pi, "tau": math.tau, "e": math.e}
_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sqrt": math.sqrt, "abs": abs, "floor": math.floor, "ceil": math.ceil,
    "round": round, "exp": math.exp, "log": math.log,
    "min": min, "max": max, "pow": pow,
}

# 参数名要能直接当标识符用（它出现在表达式里），也进 JS 的变量表。
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")

# 一条 `bind` 只能写**一个**属性，且必须是有几何意义的那几个。白名单而不是黑名单：
# `d` 与 `points` 是复合几何，按参数逐个重算是另一件事（要整条重写路径），
# `opacity` / `fill` 是样式层、不归几何演绎管（那是 director 的活）。
BIND_ATTRS = ("x", "y", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2",
              "width", "height", "transform")

# 单个表达式的长度上限。它不是防注入（解析器本身拒绝一切非算术），是防"把整段
# 几何写成一坨"——那种表达式没人读得懂，也就不可能被审。
EXPR_MAX_CHARS = 240

# token 化：数字 / 标识符 / 运算符。空白与逗号丢掉（只在函数多参时用逗号）。
_TOKEN_RE = re.compile(r"\s*(?:(\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
                       r"|([A-Za-z_][A-Za-z0-9_]*)"
                       r"|(\*\*|[-+*/%^(),]))")


class ExprError(ValueError):
    """表达式写错了。文案自带位置与"能用的东西"清单，作者不必回来读源码。"""


def tokenize(src, where="expr"):
    """→ [(kind, value)]，kind ∈ num / name / op。解析不了的字符当场报错。"""
    if not isinstance(src, str) or not src.strip():
        raise ExprError(f"{where} 必须是非空字符串表达式（如 \"540 + 260*cos(theta)\"）")
    if len(src) > EXPR_MAX_CHARS:
        raise ExprError(
            f"{where} 有 {len(src)} 个字符，超过 {EXPR_MAX_CHARS} 字上限。"
            "一条 bind 只该算一个属性值；要算整张图请拆成多条 bind，"
            "每条一句短式")
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            bad = src[pos:].lstrip()[:12]
            raise ExprError(
                f"{where} 在 {src[:pos].strip() and len(src[:pos].strip()) + 1 or 1} "
                f"字符处有不认识的东西 {bad!r}——只收数字、参数名、"
                f"+ - * / % ^ ** ( ) 与白名单函数（{'、'.join(sorted(_FUNCS))}）")
        num, name, op = m.groups()
        if num is not None:
            out.append(("num", float(num)))
        elif name is not None:
            out.append(("name", name))
        else:
            out.append(("op", op))
        pos = m.end()
    return out


class _Parser:
    """递归下降：expr := term (('+'|'-') term)*；term := unary (('*'|'/'|'%') unary)*；
    unary := ('-'|'+') unary | power；power := atom ('^'|'**' unary)?；
    atom := num | name '(' args ')' | name | '(' expr ')'。

    乘方右结合（`2^3^2` = 2^9，与数学写法一致，不是从左到右）。
    """

    def __init__(self, toks, params, where):
        self.toks, self.i, self.params, self.where = toks, 0, set(params), where

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, op):
        kind, val = self.take()
        if kind != "op" or val != op:
            raise ExprError(f"{self.where} 少了一个 {op!r}（读到 {val!r}）")

    def parse(self):
        node = self.expr()
        if self.i != len(self.toks):
            raise ExprError(f"{self.where} 在 {self.peek()[1]!r} 之后多出了内容"
                            "——一条 bind 只能是一个表达式")
        return node

    def expr(self):
        node = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.peek() in (("op", "*"), ("op", "/"), ("op", "%")):
            op = self.take()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            op = self.take()[1]
            inner = self.unary()
            return ("neg", inner) if op == "-" else inner
        return self.power()

    def power(self):
        node = self.atom()
        if self.peek() in (("op", "^"), ("op", "**")):
            self.take()
            # 右结合：指数位置再往下吃一个 unary（因此 2^-1 合法）
            return ("bin", "^", node, self.unary())
        return node

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return ("num", val)
        if kind == "name":
            if self.peek() == ("op", "("):
                if val not in _FUNCS:
                    raise ExprError(
                        f"{self.where} 用了不认识的函数 {val!r}——白名单只有 "
                        f"{'、'.join(sorted(_FUNCS))}（没有 random / 时间函数："
                        "成片逐帧 seek，任何非确定的东西都会让两帧对不上）")
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.expr())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.expr())
                self.expect(")")
                n_max = 2 if val in ("min", "max", "pow", "round", "log") else 1
                if not 1 <= len(args) <= n_max:
                    raise ExprError(
                        f"{self.where} 的 {val}() 收 1–{n_max} 个参数，实际给了 "
                        f"{len(args)} 个")
                return ("call", val, args)
            if val in _CONSTANTS:
                return ("num", _CONSTANTS[val])
            if val not in self.params:
                raise ExprError(
                    f"{self.where} 用了未声明的名字 {val!r}——表达式里只能出现 "
                    f"params 里声明的参数（{_names(self.params)}）、常量 "
                    f"{'、'.join(sorted(_CONSTANTS))} 与白名单函数")
            return ("param", val)
        if (kind, val) == ("op", "("):
            node = self.expr()
            self.expect(")")
            return node
        raise ExprError(f"{self.where} 在 {val!r} 处缺一个数值、参数名或括号")


def _names(params):
    return "、".join(sorted(params)) or "（未声明任何参数）"


def compile_expr(src, params, where="expr"):
    """表达式 → 闭包 `f({参数名: 值}) -> float`。编译期就把名字/函数/形状全查掉。"""
    node = _Parser(tokenize(src, where), params, where).parse()

    def ev(n, env):
        kind = n[0]
        if kind == "num":
            return n[1]
        if kind == "param":
            return env[n[1]]
        if kind == "neg":
            return -ev(n[1], env)
        if kind == "call":
            fn = _FUNCS[n[1]]
            try:
                return float(fn(*[ev(a, env) for a in n[2]]))
            except (ValueError, ZeroDivisionError, OverflowError) as e:
                raise ExprError(f"{where} 在 {n[1]}() 处算不出来：{e}")
        op, a, b = n[1], ev(n[2], env), ev(n[3], env)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0:
                raise ExprError(f"{where} 除以 0（分母那一项在这一帧取值为 0）")
            return a / b
        if op == "%":
            if b == 0:
                raise ExprError(f"{where} 对 0 取余")
            return math.fmod(a, b)
        try:
            return float(a ** b)
        except (OverflowError, ZeroDivisionError) as e:
            raise ExprError(f"{where} 乘方算不出来：{e}")

    return lambda env: float(ev(node, env))


# ── 同一棵树 → JS 表达式 ─────────────────────────────────────────────
# 渲染端不能 eval 一段来自稿件的字符串（那等于把信源注入面又开回来），所以表达式
# 在这里**编译成 JS 字面代码**：只含数字、参数名占位、算术运算符与白名单函数的
# 调用，没有一个字符来自稿件的原样拼贴（参数名已由 PARAM_NAME_RE 限定字符集）。
_JS_FUNC = {"sin": "Math.sin", "cos": "Math.cos", "tan": "Math.tan",
            "asin": "Math.asin", "acos": "Math.acos", "atan": "Math.atan",
            "sqrt": "Math.sqrt", "abs": "Math.abs", "floor": "Math.floor",
            "ceil": "Math.ceil", "round": "Math.round", "exp": "Math.exp",
            "log": "Math.log", "min": "Math.min", "max": "Math.max",
            "pow": "Math.pow"}


def js_expr(src, params, where="expr"):
    """表达式 → 可选的 JS 源码串（`e` 是环境对象：`e.theta`）。编译期同样全检。"""
    node = _Parser(tokenize(src, where), params, where).parse()

    def go(n):
        kind = n[0]
        if kind == "num":
            return repr(float(n[1]))
        if kind == "param":
            return f"e.{n[1]}"
        if kind == "neg":
            return f"(-{go(n[1])})"
        if kind == "call":
            return f"{_JS_FUNC[n[1]]}({','.join(go(a) for a in n[2])})"
        op, a, b = n[1], go(n[2]), go(n[3])
        if op == "^":            # JS 没有乘方运算符，一律走 Math.pow
            return f"Math.pow({a},{b})"
        return f"({a}{op}{b})"

    return go(node)


def js_number(v):
    """数值 → JS 字面量。**返回数字**不是字符串：写进 JSON 后由调用方按数字拼
    进 JS（属性值必须是有限数，契约层已保证，不需要 JSON 字符串再转一手）。"""
    return round(float(v), 4)


def js_css_attr_value(v):
    """数值 → 写进 `setAttribute` 的串（四位小数足够一个像素以下的精度）。"""
    return format_value(v)



def ease_progress(spec, default="power2.out"):
    """→ f(t01)∈[0,1]（可能过冲）。曲线真源在 `_ease.curve`，这里只补缺省。"""
    from _ease import curve
    return curve(spec, default)


class Derived:
    """编译后的 `derive` 块。所有方法都是纯函数（无状态、无缓存、乱序调用幂等）。"""

    def __init__(self, params, steps, binds, dflt_duration):
        self.params = dict(params)          # 初始值
        # steps 按参数归组：一个参数可以在不同句被推到不同值（多段演绎）。
        self._by_param = {}
        for st in steps:
            self._by_param.setdefault(st["param"], []).append(st)
        self.binds = binds                  # [(selector, attr, fn, src)]
        self._dflt = dflt_duration
        # 运行期只看这张表（见 `js_steps`）：每拍一条，起点已按"上一拍终值"补齐。
        self.tracks = []
        for name in self.params:
            cur = self.params[name]
            for k, st in enumerate(self._by_param.get(name, [])):
                a = st.get("from", cur)
                self.tracks.append({"p": name, "i": k, "a": a, "b": st["to"],
                                    "ease": st.get("ease", ""),
                                    "duration": st["span"]})
                cur = st["to"]

    def keys(self):
        return sorted(self.params)

    def span(self):
        """这个块需要的总时长（秒）：最长那一拍的起点 + 时长，供出窗自查。"""
        return max((b["span"] for s in self._by_param.values() for b in s),
                   default=0.0)

    def env_at(self, els):
        """`els` = 已过缓动的进度列表（每拍一个）→ 这一帧的 {参数名: 值}。

        多拍按**拍序累进**：第 k 拍的起点是第 k−1 拍的终值（写了 `from` 就以它为准），
        没有 `els` 的拍视为还没开始（进度 0）。
        """
        env = dict(self.params)
        for name in env:
            cur = env[name]
            for k, st in enumerate(self._by_param.get(name, [])):
                a = st.get("from", cur)
                b = st["to"]
                e = els[k] if k < len(els) else 0.0
                env[name] = a + (b - a) * e
                cur = b
        return env

    def geometry(self, env):
        """→ [(selector, attr, 数值)]，逐条算好。任何一个算不出来都抛 ExprError。"""
        out = []
        for sel, attr, fn, src in self.binds:
            v = fn(env)
            if not math.isfinite(v):
                raise ExprError(f"{src!r} 算出了 {v}——几何必须是有限数，"
                                "检查分母与乘方是否在这一帧越界")
            out.append((sel, attr, v))
        return out

    def js_payload(self, scope):
        """→ 渲染端内联脚本读的那份 JSON（见 `html_renderer._derive_timeline_lines`）。

        表达式在这里就编译成 JS 字面代码：渲染页里不出现 `eval`，也不出现任何来自
        稿件的原样字符串（参数名受 `PARAM_NAME_RE` 限定，选择器受 `_DIRECTOR_TARGET_RE`
        限定，属性名受 `BIND_ATTRS` 限定）。
        """
        return {
            "params": dict(self.params),
            "steps": [{"p": t["p"], "a": t["a"], "b": t["b"], "ease": t["ease"]}
                      for t in self.tracks],
            # 选择器在这里就拼成"整页作用域"的形式（与 director 的 target 同一条
            # 收窄规则：多段内联同名 id 不串台），渲染端不必再拼一次。
            "binds": [{"sel": f"{scope} {sel}", "attr": attr,
                       "js": js_expr(src, self.params)}
                      for sel, attr, _fn, src in self.binds],
        }


def compile_derive(block, dflt_duration=0.6, where="derive"):
    """校验过的 `derive` 块 → `Derived`（见 `_images_schema.validate_derive`）。"""
    params = block["params"]
    binds = []
    for i, b in enumerate(block["bind"]):
        fn = compile_expr(b["expr"], params,
                          f"{where}.bind[{i}]（{b['target']}.{b['attr']}）")
        binds.append((b["target"], b["attr"], fn, b["expr"]))
    steps = []
    for st in block["steps"]:
        steps.append(dict(st, span=float(st.get("duration", dflt_duration))))
    return Derived(params, steps, binds, dflt_duration)


def value_at(tracks, t01, dflt_ease="power2.out"):
    """单拍在进度 t01 处的参数值（供 `check_svg` 的自查用：它只问"端点处几何对不对"）。"""
    e = ease_progress(tracks["ease"] or dflt_ease, dflt_ease)(t01)
    return tracks["a"] + (tracks["b"] - tracks["a"]) * e


def format_value(v):
    """数值 → SVG 属性串。**不用** `num_fmt`：那个写给人读（会带千分位），
    属性值必须是纯数字。保留 4 位小数、去掉尾零（0.0001px 以下没有意义，
    却会让属性串长一倍）。"""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s
