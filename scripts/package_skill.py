#!/usr/bin/env python3
"""分发打包（维护者工具）：把技能目录剥掉开发物后打成 zip。

对外发布技能包最省事的办法是整仓拷贝——但 tests/、check_*.py 和本脚本都是
维护者专属：技能的使用者不会跑它们，它们只增大包装和"这里面好像有东西能跑"
的困惑。制作残渣与运行时产物（__pycache__、.env、audio_output/、
segments_source.json 等）同样不该进包。输出路径复用三个写盘入口同一道
守卫 guard_not_in_skill_dir：分发包也不能落在技能目录里。

用法：
    python scripts/package_skill.py                    # 默认写到技能目录同级的 content-to-video-<日期>.zip
    python scripts/package_skill.py -o D:/share/ctv.zip
"""
import argparse
import datetime
import fnmatch
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import SKILL_DIR, guard_not_in_skill_dir, setup_stdio  # noqa: E402

# 整目录不进包：开发物 + 制作产物 + 版本库自身
EXCLUDE_DIRS = {".git", ".venv", "__pycache__", "tests",
                "audio_output", "hf-project", "out", "snapshots"}
# 单文件不进包：密钥与按 .gitignore 约定散落在树里的制作中间物
EXCLUDE_NAMES = {".env", "candidates.json", "segments_source.json",
                 "timing_manifest.json"}
# scripts/ 下的维护者工具：check_* 与本脚本自己
EXCLUDE_BASENAMES = {"package_skill.py"}
EXCLUDE_PATTERNS = {"check_*.py"}

ARC_ROOT = os.path.basename(SKILL_DIR)


def _excluded_file(basename):
    return (basename in EXCLUDE_NAMES
            or basename in EXCLUDE_BASENAMES
            or any(fnmatch.fnmatch(basename, p) for p in EXCLUDE_PATTERNS))


def build_zip(out_path):
    """打包 SKILL_DIR 到 out_path，返回 (写入文件数, 字节数)。"""
    count, size = 0, 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(SKILL_DIR):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for name in sorted(filenames):
                if name.endswith(".pyc") or _excluded_file(name):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, SKILL_DIR).replace("\\", "/")
                zf.write(full, f"{ARC_ROOT}/{rel}")
                count += 1
                size += os.path.getsize(full)
    return count, size


def main():
    setup_stdio()
    parser = argparse.ArgumentParser(description="生成剥离开发物的技能分发包")
    parser.add_argument("-o", "--output", default=None,
                        help="zip 输出路径（默认 <技能目录同级>/content-to-video-<日期>.zip；"
                             "不得落在技能目录内）")
    args = parser.parse_args()
    out = os.path.abspath(args.output) if args.output else os.path.join(
        os.path.dirname(SKILL_DIR),
        f"{ARC_ROOT}-{datetime.date.today():%Y%m%d}.zip")
    guard_not_in_skill_dir(("分发包输出路径", out))
    if not out.lower().endswith(".zip"):
        raise SystemExit(f"[package] 输出路径必须是 .zip（收到: {out}）")
    count, size = build_zip(out)
    print(f"[package] {count} 个文件（未压缩 {size / 1024:.0f} KB）→ {out}")
    print(f"[package] 已排除：{'/'.join(sorted(EXCLUDE_DIRS))}，"
          "scripts/check_*.py、本脚本、.env 与制作中间物")


if __name__ == "__main__":
    main()
