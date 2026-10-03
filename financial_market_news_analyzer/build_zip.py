"""
Build the sector-scout Lambda deployment zip.

    python financial_market_news_analyzer/build_zip.py [--out dist/scout.zip]

Runs on any OS (no Docker): downloads the *Linux* wheels for Python 3.11 straight from PyPI with pip's
--platform option, so nothing native is built locally.  The zip contains the pinned dependencies in
requirements-lambda.txt and the scout's own modules at the root (handler is `lambda_handler.handler`).
The three vendored shims (six.py, sgmllib.py, typing_extensions.py) are only added when pip did not
install the module itself (sgmllib3k has no wheel, so sgmllib.py always comes from here).

Terraform (infra/scout.tf) deploys the result.  Lambda's limit is 250 MB unzipped.
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

PYTHON_VERSION = "3.11"
PLATFORMS = ("manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64")
UNZIPPED_LIMIT_MB = 250
SAFETY_MARGIN_MB = 10

SHIMS = {"six.py", "sgmllib.py", "typing_extensions.py"}
NOT_SHIPPED = {"build_zip.py"}                         # build tooling, not part of the function
PRUNE_DIR_NAMES = {"tests", "test", "__pycache__"}


def install_dependencies(target: Path) -> None:
    """pip install the pinned Linux wheels from requirements-lambda.txt into `target` (no dependency resolution)."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--upgrade", "--target", str(target),
           "--only-binary=:all:", "--python-version", PYTHON_VERSION, "--implementation", "cp",
           "-r", str(HERE / "requirements-lambda.txt")]
    for platform in PLATFORMS:
        cmd += ["--platform", platform]
    # the Microsoft Store Python defaults pip to --user, which pip refuses to combine with --target
    subprocess.run(cmd, check=True, env={**os.environ, "PIP_USER": "0"})


def prune(build: Path) -> None:
    for path in sorted(build.rglob("*"), key=lambda p: -len(p.parts)):
        if path.is_dir() and path.name in PRUNE_DIR_NAMES and path != build:
            shutil.rmtree(path, ignore_errors=True)
    shutil.rmtree(build / "bin", ignore_errors=True)
    for record in build.glob("*.dist-info/RECORD"):          # pip writes build-specific paths here; not used at run time
        record.unlink()


def assemble(build: Path) -> None:
    """Add the scout's modules to `build`; shims only where pip did not provide the module."""
    for src in sorted(HERE.glob("*.py")):
        if src.name in NOT_SHIPPED or (src.name in SHIMS and (build / src.name).exists()):
            continue
        shutil.copy2(src, build / src.name)


def directory_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def write_zip(build: Path, out: Path) -> None:
    """Deterministic zip (sorted entries, fixed timestamps), so rebuilding unchanged code gives the same hash and
    Terraform sees no code change."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(build.rglob("*")):
            if f.is_file():
                info = zipfile.ZipInfo(f.relative_to(build).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o755 if f.stat().st_mode & 0o111 else 0o644) << 16
                zf.writestr(info, f.read_bytes())


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
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "scout.zip")
    args = parser.parse_args(argv)
    sizes = build(args.out)
    print(f"Built {args.out}: {sizes['zip_mb']} MB zipped, {sizes['unzipped_mb']} MB unzipped "
          f"(Lambda limit {UNZIPPED_LIMIT_MB} MB unzipped).")


if __name__ == "__main__":
    main()
