#!/usr/bin/env python3
"""生成期扩展包工具：编译 → 确定性对账 → 统一校验 → 落标准产物。

它像 canvas_kit.py 一样是**主管线之外**的一步：产物是标准 `.svg` / `steps` /
合规素材路径，作者照旧把它们写进 `images.json`，`animate.steps` 一行不用改。
run.py 不认识这个工具，也不该认识——扩展包是显式的、可插拔的，不是隐式钩子。

    python scripts/extension.py \
        --extension scripts/extensions/sketch \
        --spec spec.json --base-svg images/base.svg \
        -o images/seg1.svg --report images/seg1.ext.json

产物落盘规则（补丁）：`svg` 直接写 SVG 文本；`steps` / `media` 写成单个 JSON
对象，键与契约层 `ALLOWED_OUTPUTS` 同名，可以直接贴进 `images.json`。原版只落
svg，`steps-compiler` / `asset-transcoder` 明明过了统一校验却被 CLI 挡在最后
一步，逼人手调 `_extensions.py` API——那一挡没有契约上的理由。

契约与三条红线见 references/extensions.md；校验真源是 scripts/_extensions.py。
Python 3.9 兼容，只用标准库。
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True

import _extensions  # noqa: E402  扩展契约的唯一真源

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guard_output(path, where):
    """输出不许落进技能目录：那是源码，不是制作产物（同 canvas_kit 的口径）。"""
    ap = os.path.abspath(path)
    # 注意：ExtensionError 继承 ValueError，不能在下面的 except 里 raise——
    # 会被同一句 except 吞掉，守卫静默失守（这正是本技能最该拦的那类坏）。
    try:
        common = os.path.commonpath([ap, SKILL_ROOT])
    except ValueError:
        common = None  # Windows 不同盘符，天然不落在技能目录
    if common == SKILL_ROOT:
        raise _extensions.ExtensionError(
            "%s 落在技能目录内：%s。先 cd 到项目目录再跑，"
            "产物和源码不能共用一个目录" % (where, ap))
    parent = os.path.dirname(ap)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    return ap


def _payload(ext, out):
    """把契约层认可的产物整理成可落盘的 (文本, 简述)。

    svg 是原样文本；steps/media 收进一个 JSON 对象——键名与 manifest.outputs
    对齐，作者复制 `steps` 那一坨就能贴进 images.json，不需要再手工包一层。
    """
    if "svg" in out:
        return out["svg"], "SVG"
    payload = {k: out[k] for k in ("steps", "media") if k in out}
    if not payload:
        raise _extensions.ExtensionError(
            "扩展 '%s' 没交出任何可落盘的产物" % ext["name"])
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "+".join(sorted(payload)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="生成期扩展包：把画风/素材在编译期交给外部工具")
    ap.add_argument("--extension", required=True,
                    help="扩展包目录（含 manifest.json 与 compile.py）——显式注册，不扫描")
    ap.add_argument("--spec", required=True, help="spec.json 路径")
    ap.add_argument("--base-svg", default=None,
                    help="基础 SVG 路径：注入 spec.svg，并作为 #id 正交对账的基准")
    ap.add_argument("-o", "--out", required=True,
                    help="输出路径：svg 直接落文本；steps/media 落 JSON 对象")
    ap.add_argument("--report", default=None,
                    help="可选：把来源记账（provider / version / sha256）写成 JSON")
    args = ap.parse_args(argv)

    try:
        out_path = _guard_output(args.out, "-o")
        if args.report:
            args.report = _guard_output(args.report, "--report")
        ext = _extensions.load_extension(args.extension)
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        if not isinstance(spec, dict):
            raise _extensions.ExtensionError("spec.json 顶层必须是对象")
        base_svg = None
        if args.base_svg:
            with open(args.base_svg, encoding="utf-8") as f:
                base_svg = f.read()
            spec["svg"] = base_svg
        # 两次编译逐字节一致，出错直接打回，不落任何产物。
        out = _extensions.assert_deterministic(ext, spec, {})
        errs, warns = _extensions.verify_output(ext, out, base_svg=base_svg)
        for w in warns:
            print("[warn] %s" % w, file=sys.stderr)
        if errs:
            for e in errs:
                print("[error] %s" % e, file=sys.stderr)
            return 1
        text, brief = _payload(ext, out)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        prov = _extensions.provenance(ext)
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(prov, f, ensure_ascii=False, indent=2, sort_keys=True)
        print("[ok] %s@%s → %s（%s，%d 字节，sha256 %s…）"
              % (ext["name"], ext["version"], out_path, brief,
                 len(text.encode("utf-8")), prov["sha256"][:12]))
        return 0
    except (_extensions.ExtensionError, ValueError, OSError) as exc:
        print("[error] %s" % exc, file=sys.stderr)
        return 1
    except Exception as exc:  # 扩展包自身抛的错也要打回人话，不吐 traceback
        print("[error] 扩展 '%s' 的 compile() 抛错：%s: %s"
              % (os.path.basename(os.path.abspath(args.extension)),
                 type(exc).__name__, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
