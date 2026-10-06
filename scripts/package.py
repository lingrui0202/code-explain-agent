"""作业提交打包脚本：生成「学号姓名.zip」。

用法：
    python scripts/package.py --id 10086 --name 张三

特性：
- 自动排除 .env、__pycache__、.venv、.git 等敏感/无用文件；
- 打包前自动跑一遍单元测试，测试不过会提示（可用 --skip-tests 跳过）；
- 输出体积检查（作业要求 < 200MB）。
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

MAX_BYTES = 200 * 1024 * 1024  # 作业要求 < 200MB


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
    parser = argparse.ArgumentParser(description="打包作业为 学号姓名.zip")
    parser.add_argument("--id", required=True, help="学号，如 10086")
    parser.add_argument("--name", required=True, help="姓名，如 张三")
    parser.add_argument("--output", default=None, help="输出目录，默认项目根目录")
    parser.add_argument("--skip-tests", action="store_true", help="跳过测试")
    args = parser.parse_args()

    if not args.skip_tests and not run_tests():
        print("!! 单元测试未通过，仍要打包请加 --skip-tests")
        return 1

    out_dir = Path(args.output) if args.output else ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{args.id}{args.name}.zip"

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in iter_files(ROOT):
            if path == zip_path:
                continue
            zf.write(path, path.relative_to(ROOT).as_posix())
            count += 1

    size = zip_path.stat().st_size
    print(f"\n>> 已打包 {count} 个文件 -> {zip_path}")
    print(f">> 体积 {size / 1024 / 1024:.2f} MB" + ("（符合 <200MB 要求）" if size < MAX_BYTES else "（!! 超过 200MB）"))

    if (ROOT / ".env").exists():
        print(">> 注意：.env 已被排除，评审者需自行配置 API Key 或使用 --offline 模式")
    else:
        print(">> 提示：未发现 .env，建议评审者使用 --offline 模式体验完整 Agent 链路")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
