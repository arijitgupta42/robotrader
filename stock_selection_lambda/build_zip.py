"""
Build the stock-selection Lambda deployment zip.

    python stock_selection_lambda/build_zip.py [--out dist/stock-selection.zip]

Runs on any OS (no Docker): it downloads the *Linux* wheels for Python 3.11
straight from PyPI with pip's --platform option, so nothing native is built
locally.  The zip contains

  * the dependencies in requirements.txt (boto3 is NOT included — the Lambda
    runtime provides it; scipy is NOT included — it would break the size limit),
  * handler.py and email_report.py at the root (handler is `handler.handler`),
  * the lse_stock_analysis package, including sector_map.json, without the
    local-only tooling (main.py, model_loader.py, get_stock_data.py,
    universe_check.py, the return-projection agent, the data cache).

Lambda's limit is 250 MB unzipped; the build fails if it gets close.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKAGE = ROOT / "lse_stock_analysis"
ROOT_FILES = [HERE / "handler.py", HERE / "email_report.py"]

PYTHON_VERSION = "3.11"
PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64")
UNZIPPED_LIMIT_MB = 250
SAFETY_MARGIN_MB = 10

# paths inside lse_stock_analysis/ that stay out of the zip
PACKAGE_EXCLUDE = {
    "main.py", "model_loader.py", "get_stock_data.py", "data_cache.csv", "universe_check.py",
    "agents/return_projection_agent.py",
}
PRUNE_DIR_NAMES = {"tests", "test", "__pycache__"}


def install_dependencies(target: Path) -> None:
    """pip install the Linux wheels from requirements.txt into `target`."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--target", str(target),
           "--only-binary=:all:", "--python-version", PYTHON_VERSION, "--implementation", "cp",
           "-r", str(HERE / "requirements.txt")]
    for platform in PLATFORMS:
        cmd += ["--platform", platform]
    # the Microsoft Store Python defaults pip to --user, which pip refuses to combine with --target
    subprocess.run(cmd, check=True, env={**os.environ, "PIP_USER": "0"})


def prune(build: Path) -> None:
    """Drop test suites, bytecode and console scripts from the installed dependencies."""
    for path in sorted(build.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and path.name in PRUNE_DIR_NAMES and path != build:
            shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(build / "bin", ignore_errors=True)


def assemble(build: Path) -> None:
    """Add the handler modules and the lse_stock_analysis package to `build`."""
    for f in ROOT_FILES:
        shutil.copy2(f, build / f.name)
    for src in PACKAGE.rglob("*"):
        rel = src.relative_to(PACKAGE)
        if (not src.is_file() or "__pycache__" in rel.parts or rel.as_posix() in PACKAGE_EXCLUDE
                or src.suffix == ".pyc"):
            continue
        dest = build / "lse_stock_analysis" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def directory_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def write_zip(build: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(build.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(build).as_posix())


def build(out: Path, install: bool = True) -> dict:
    """Build the zip at `out`.  Returns sizes; raises if the unzipped size is too close to Lambda's limit."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "package"
        target.mkdir()
        if install:
            install_dependencies(target)
            prune(target)
        assemble(target)
        unzipped = directory_mb(target)
        if unzipped > UNZIPPED_LIMIT_MB - SAFETY_MARGIN_MB:
            raise SystemExit(f"Unzipped size {unzipped:.0f} MB is too close to Lambda's {UNZIPPED_LIMIT_MB} MB limit.")
        write_zip(target, out)
    return {"unzipped_mb": round(unzipped, 1), "zip_mb": round(out.stat().st_size / 1024 / 1024, 1)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "stock-selection.zip")
    args = parser.parse_args(argv)
    sizes = build(args.out)
    print(f"Built {args.out}: {sizes['zip_mb']} MB zipped, {sizes['unzipped_mb']} MB unzipped "
          f"(Lambda limit {UNZIPPED_LIMIT_MB} MB unzipped).")


if __name__ == "__main__":
    main()
