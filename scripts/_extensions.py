"""扩展包运行时：显式注册 / 统一校验 / 确定性判据。

这一层是 L1「编译期扩展点」的唯一真源。它刻意**不进主管线**：扩展包是生成期
工具，产出标准 SVG / steps / 合规素材，作者照旧把它们写进 `images.json`。
`run.py` / `gen_hyperframes.py` / `html_renderer.py` 一行都不用改，渲染端也永远
不会执行第三方代码——GSAP 仍是唯一被浏览器执行的 JS。

三条红线（与渲染端同源，只是把确定性要求延伸到了编译期）：

1. **显式注册**：没有自动扫描、没有插件发现。装了一个扩展包不会隐式改变任何
   产物；作者必须在命令里写出它的路径，manifest 与源码的 sha256 记进产物来源。
2. **统一校验**：扩展输出走与手写 SVG 完全相同的净化与门禁，不设豁免分支。
   尤其 `#id` 集合必须前后一致——这是「换画风不动一行 animate.steps」的机器判据。
3. **确定性**：同一 (spec, ctx) 两次编译必须逐字节一致。随机、墙钟、网络、
   子进程在编译期一律禁用（AST 层拦），对账兜住漏网的。

Python 3.9 兼容，只用标准库；不写 pyc（`exec` 隔离命名空间而非 importlib 缓存）。
"""

import ast
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET

MANIFEST_NAME = "manifest.json"
COMPILE_NAME = "compile.py"

# manifest 封闭键集。多写一个键当场报错，不静默忽略——与 images.json 同一口径。
MANIFEST_KEYS = frozenset({
    "name", "version", "kind", "outputs", "dependencies", "provider",
})
REQUIRED_MANIFEST_KEYS = ("name", "version", "kind", "outputs")

# 首版只认这三种产出意图。asset-transcoder 交视频素材，steps-compiler 交编排，
# style-filter 交 SVG 画风后处理。三者共享同一份契约，但校验分支不同。
ALLOWED_KINDS = frozenset({"style-filter", "asset-transcoder", "steps-compiler"})
ALLOWED_OUTPUTS = frozenset({"svg", "steps", "media"})

# 编译期禁用的顶层模块。理由不是"这些库不好"，是它们会让同一份稿件两次生成
# 不一致，或把不可信输入带进生成期进程。os 不禁（os.path 是纯函数），但子进程
# 与网络入口全封。
FORBIDDEN_MODULES = frozenset({
    "random", "secrets", "time", "datetime", "urllib", "urllib2", "requests",
    "socket", "http", "ftplib", "smtplib", "subprocess", "multiprocessing",
    "asyncio", "threading", "webbrowser", "ctypes",
})
FORBIDDEN_CALLS = frozenset({"eval", "exec", "__import__", "compile", "open"})

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

# 视频素材扩展名与 _images_schema.MEDIA_VIDEO_EXTS 对齐（不 import 它，避免
# 扩展运行时反向依赖契约层；这份表只用于"扩展说它交了 media"时的粗判）。
MEDIA_EXTS = frozenset({
    ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif",
    ".mp4", ".webm", ".mov", ".avi", ".mkv",
})


class ExtensionError(ValueError):
    """扩展包不合规。消息里带 where，方便生成期直接打回作者。"""


def _where(name):
    return "扩展包 '%s'" % name if name else "扩展包"


# 定义性子树：里面的 id 只供 url(#…) 内部引用，不是 animate 的寻址目标。
# 画风扩展往 <defs> 塞滤镜定义是合规的，不该被正交判据当成"多了一个 id"。
_DEFINITIONAL_TAGS = frozenset({
    "defs", "filter", "pattern", "mask", "clippath", "marker", "symbol",
    "lineargradient", "radialgradient", "style", "title", "desc",
})


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def svg_ids(svg_text):
    """所有可被 animate 寻址的 id 的有序集合（跳过定义性子树）。

    正交判据的真源：编排按 #id 寻址，画风不能增删改作者的身份。滤镜/
    pattern/渐变这些纯内部引用不算——它们不是 target。
    """
    root = ET.fromstring(svg_text)

    def walk(el, inside_def=False):
        if not inside_def and el.get("id") is not None:
            yield el.get("id")
        blocked = inside_def or _local(el.tag).lower() in _DEFINITIONAL_TAGS
        for child in el:
            for found in walk(child, blocked):
                yield found

    return sorted(walk(root))


def _read_manifest(path):
    """读并校验 manifest.json，返回 (dict, where)。"""
    mpath = os.path.join(path, MANIFEST_NAME)
    if not os.path.isfile(mpath):
        raise ExtensionError("找不到 %s（扩展包目录必须有它）" % MANIFEST_NAME)
    with open(mpath, encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except ValueError as exc:
            raise ExtensionError("%s 不是合法 JSON：%s" % (MANIFEST_NAME, exc))
    if not isinstance(raw, dict):
        raise ExtensionError("%s 顶层必须是对象" % MANIFEST_NAME)
    extra = sorted(set(raw) - MANIFEST_KEYS)
    if extra:
        raise ExtensionError(
            "%s 含未登记的键 %s——键集封闭，多写的键不生效也不该被静默吞掉"
            % (MANIFEST_NAME, "、".join(extra)))
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in raw:
            raise ExtensionError("%s 缺必填键 %r" % (MANIFEST_NAME, key))

    name = raw["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ExtensionError("name 必须匹配 %s（实际: %r）" % (_NAME_RE.pattern, name))
    ver = raw["version"]
    if not isinstance(ver, str) or not _VERSION_RE.match(ver):
        raise ExtensionError("version 必须是 x.y.z（实际: %r）" % (ver,))
    if raw["kind"] not in ALLOWED_KINDS:
        raise ExtensionError(
            "kind %r 不在白名单里（可用: %s）"
            % (raw["kind"], "、".join(sorted(ALLOWED_KINDS))))
    outputs = raw["outputs"]
    if (not isinstance(outputs, list) or not outputs
            or any(not isinstance(o, str) or o not in ALLOWED_OUTPUTS
                   for o in outputs)
            or len(set(outputs)) != len(outputs)):
        raise ExtensionError(
            "outputs 必须是非空、不重复、取值在 %s 里的数组"
            % "、".join(sorted(ALLOWED_OUTPUTS)))
    deps = raw.get("dependencies", [])
    if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
        raise ExtensionError("dependencies 必须是字符串数组")
    if deps:
        # 首版只声明、不装载第三方依赖：编译期依赖必须钉版本才能保确定性，
        # 没钉之前宁可拒收，也不让"同稿件两次不一致"从渲染端蔓延到编译端。
        raise ExtensionError(
            "首版扩展包只许用标准库：dependencies 必须为空（实际: %s）。"
            "要用第三方库先补依赖钉固清单，否则两次编译可能不一致" % "、".join(deps))
    provider = raw.get("provider", "")
    if not isinstance(provider, str):
        raise ExtensionError("provider 必须是字符串（素材/来源记账）")
    return raw, _where(name)


def _check_source(src, where):
    """AST 层拦墙钟 / 随机 / 网络 / 子进程 / 动态执行。"""
    tree = ast.parse(src, filename=COMPILE_NAME)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    raise ExtensionError(
                        "%s 的 %s 导入了禁用模块 %r：编译期不许读墙钟/随机/网络/子进程"
                        % (where, COMPILE_NAME, top))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in FORBIDDEN_MODULES:
                    raise ExtensionError(
                        "%s 的 %s 从禁用模块 %r 导入：编译期不许读墙钟/随机/网络/子进程"
                        % (where, COMPILE_NAME, top))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                raise ExtensionError(
                    "%s 的 %s 调用了 %s()：生成期不收动态执行与文件 IO，"
                    "产物请用返回值交回"
                    % (where, COMPILE_NAME, node.func.id))
    # 全局可变状态会让第二次 compile() 带上第一次的残留。只拦真正危险的两类：
    # compile() 里写全局、以及模块级的可变容器常量；纯 tuple/字符串常量放行。
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ExtensionError(
                "%s 的 %s 用了 global/nonlocal：compile() 必须是纯函数，"
                "不许跨次携带状态" % (where, COMPILE_NAME))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            mutable = isinstance(value, (ast.List, ast.Dict, ast.Set,
                                         ast.ListComp, ast.DictComp, ast.SetComp))
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper() and mutable:
                    raise ExtensionError(
                        "%s 的 %s 定义了模块级可变容器 %s：它会污染第二次编译，"
                        "把表放进 compile() 内部或换成 tuple" % (where, COMPILE_NAME, t.id))


def load_extension(path):
    """显式加载一个扩展包目录。不扫描、不发现、不缓存跨包状态。

    返回 dict：name / version / kind / outputs / provider / digest / fn / path。
    digest 是 manifest + compile.py 的 sha256，供产物来源记账（同一产物能回溯
    到哪一份扩展代码）。
    """
    if not isinstance(path, str) or not os.path.isdir(path):
        raise ExtensionError("扩展包路径不是目录：%r" % (path,))
    manifest, where = _read_manifest(path)
    cpath = os.path.join(path, COMPILE_NAME)
    if not os.path.isfile(cpath):
        raise ExtensionError("%s 缺 %s" % (where, COMPILE_NAME))
    with open(cpath, "rb") as f:
        source_bytes = f.read()
    src = source_bytes.decode("utf-8")
    try:
        _check_source(src, where)
    except SyntaxError as exc:
        raise ExtensionError("%s 的 %s 有语法错误：%s" % (where, COMPILE_NAME, exc))

    # 隔离命名空间：不进 sys.modules、不写 pyc、扩展之间互不看见对方的全局。
    namespace = {"__name__": "ctv_ext_" + manifest["name"], "__builtins__": __builtins__}
    code = compile(src, cpath, "exec")
    exec(code, namespace)  # noqa: S102  源码已过 AST 白名单，产物只经返回值交回
    fn = namespace.get("compile")
    if not callable(fn):
        raise ExtensionError(
            "%s 的 %s 必须定义可调用的 compile(spec, ctx)" % (where, COMPILE_NAME))

    with open(os.path.join(path, MANIFEST_NAME), "rb") as f:
        mbytes = f.read()
    digest = hashlib.sha256(mbytes + b"\0" + source_bytes).hexdigest()
    return {
        "name": manifest["name"], "version": manifest["version"],
        "kind": manifest["kind"], "outputs": frozenset(manifest["outputs"]),
        "provider": manifest.get("provider", ""), "digest": digest,
        "fn": fn, "path": os.path.abspath(path),
    }


def compile_extension(ext, spec, ctx=None):
    """调一次 compile(spec, ctx)，校验返回的形状与 manifest.outputs 对齐。"""
    if ctx is None:
        ctx = {}
    if not isinstance(spec, dict):
        raise ExtensionError("%s 的 spec 必须是对象" % _where(ext["name"]))
    if not isinstance(ctx, dict):
        raise ExtensionError("%s 的 ctx 必须是对象" % _where(ext["name"]))
    out = ext["fn"](spec, ctx)
    if not isinstance(out, dict):
        raise ExtensionError(
            "%s 的 compile() 必须返回对象（实际: %s）"
            % (_where(ext["name"]), type(out).__name__))
    extra = sorted(set(out) - ALLOWED_OUTPUTS)
    if extra:
        raise ExtensionError(
            "%s 返回了未登记的产物 %s" % (_where(ext["name"]), "、".join(extra)))
    undeclared = sorted(set(out) - set(ext["outputs"]))
    if undeclared:
        raise ExtensionError(
            "%s 返回了 manifest.outputs 没声明的产物 %s——声明与产出必须一致"
            % (_where(ext["name"]), "、".join(undeclared)))
    for key, value in out.items():
        if key == "svg":
            if not isinstance(value, str) or not value.strip():
                raise ExtensionError("%s 的 svg 必须是非空字符串" % _where(ext["name"]))
        elif key == "steps":
            if not isinstance(value, list):
                raise ExtensionError("%s 的 steps 必须是数组" % _where(ext["name"]))
        elif key == "media":
            if not isinstance(value, str) or not value.strip():
                raise ExtensionError("%s 的 media 必须是非空字符串" % _where(ext["name"]))
    return out


def _canonical(obj):
    """确定性对账的口径：JSON 规范化后再比字节，dict 顺序不算差异。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def assert_deterministic(ext, spec, ctx=None):
    """同一 (spec, ctx) 两次编译必须逐字节一致。随机/墙钟/全局状态都栽在这。"""
    if ctx is None:
        ctx = {}
    a = compile_extension(ext, spec, ctx)
    b = compile_extension(ext, spec, ctx)
    if _canonical(a) != _canonical(b):
        raise ExtensionError(
            "%s 两次编译不一致：compile() 不是纯函数。检查随机、墙钟、"
            "网络、模块级可变状态" % _where(ext["name"]))
    return a


def verify_output(ext, out, base_svg=None):
    """统一校验：扩展输出走与手写 SVG 同一套净化与门禁，返回 (errs, warns)。

    base_svg 给了就做正交判据——扩展前后的 `#id` 集合必须完全一致。这是
    「换画风不动一行 animate.steps」的机器判据：编排按 #id 寻址，画风不能
    偷偷改名、加壳、删节点。
    """
    errs, warns = [], []
    where = _where(ext["name"])

    if "svg" in out:
        try:
            import _svg_sanitize
            clean, notes = _svg_sanitize.sanitize_svg_for_inline(out["svg"])
            if "<script" in clean.lower() or "<foreignobject" in clean.lower():
                errs.append("%s 的 svg 净化后仍含脚本宿主" % where)
            for note in notes:
                if "墙钟" in note or "SMIL" in note:
                    errs.append("%s 的 svg 含墙钟动画：逐帧 seek 下不可复现" % where)
        except Exception as exc:  # 净化器自身对坏 SVG 抛 ValueError，照单打回
            errs.append("%s 的 svg 过不了内联净化：%s" % (where, exc))
            clean = None
        if clean is not None and base_svg is not None:
            try:
                before, after = svg_ids(base_svg), svg_ids(clean)
            except ET.ParseError as exc:
                errs.append("%s 的 svg 解析失败：%s" % (where, exc))
            else:
                if before != after:
                    errs.append(
                        "%s 改了 #id 集合（前 %d 个 / 后 %d 个）：编排按 #id 寻址，"
                        "扩展不许改身份，否则 animate.steps 会静默落空"
                        % (where, len(before), len(after)))
        elif clean is not None and base_svg is None:
            warns.append("%s 没给 base_svg，跳过 #id 正交对账" % where)

    if "steps" in out:
        try:
            import _animate
            if base_svg is None:
                errs.append("%s 交了 steps 却没给 base_svg，无法跑落空 target 判据"
                            % where)
            else:
                e, w = _animate.check_animate(
                    base_svg, {"steps": out["steps"]}, ext["name"], full_bleed=True)
                errs.extend("%s 的 steps: %s" % (where, x) for x in e)
                warns.extend("%s 的 steps: %s" % (where, x) for x in w)
        except Exception as exc:
            errs.append("%s 的 steps 过不了生成期体检：%s" % (where, exc))

    if "media" in out:
        path = out["media"]
        if os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            errs.append("%s 的 media 路径越出项目根：%r" % (where, path))
        if os.path.splitext(path.lower())[1] not in MEDIA_EXTS:
            errs.append("%s 的 media 扩展名不在合规素材表里：%r" % (where, path))

    return errs, warns


def provenance(ext):
    """产物来源记账：与 images.json 的 provider/license 同一意图。"""
    return {
        "provider": ext["provider"] or ("ctv-ext:" + ext["name"]),
        "extension": "%s@%s" % (ext["name"], ext["version"]),
        "sha256": ext["digest"],
    }
