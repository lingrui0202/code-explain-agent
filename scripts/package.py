"""打包脚本：将项目打包为 zip 归档。

用法：
    python scripts/package.py --name code-explain-agent

特性：
- 自动排除 .env、__pycache__、.venv、.git 等敏感或无用文件；
- 打包前自动运行单元测试，未通过时提示（可用 --skip-tests 跳过）；
- 输出体积超过阈值时给出提示。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".idea", ".vscode",
    "node_modules", "dist", "build", ".pytest_cache", ".mypy_cache",
    "runs", ".streamlit",
}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip", ".rar", ".7z"}

MAX_BYTES = 200 * 1024 * 1024  # 体积提示阈值


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        if path.name in EXCLUDE_FILES or path.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        yield path


def run_tests() -> bool:
    print(">> 运行单元测试…")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = (result.stdout or result.stderr).strip().splitlines()[-3:]
    print("\n".join(tail))
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="将项目打包为 zip")
    parser.add_argument("--name", default="code-explain-agent", help="输出文件名（不含扩展名）")
    parser.add_argument("--output", default=None, help="输出目录，默认项目根目录")
    parser.add_argument("--skip-tests", action="store_true", help="跳过测试")
    args = parser.parse_args()

    if not args.skip_tests and not run_tests():
        print("!! 单元测试未通过，仍要打包请加 --skip-tests")
        return 1

    out_dir = Path(args.output) if args.output else ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{args.name}.zip"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in iter_files(ROOT):
            if path == zip_path:
                continue
            zf.write(path, path.relative_to(ROOT).as_posix())
            count += 1

    size = zip_path.stat().st_size
    print(f"\n>> 已打包 {count} 个文件 -> {zip_path}")
    print(f">> 体积 {size / 1024 / 1024:.2f} MB" + ("" if size < MAX_BYTES else "（!! 超过 200MB）"))
    print(">> 已排除 .env 与缓存文件，使用者需自行配置 API Key 或使用 --offline 模式")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
