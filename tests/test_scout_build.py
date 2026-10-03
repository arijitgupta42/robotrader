import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOUT = ROOT / "financial_market_news_analyzer"
# loaded under its own name: stock_selection_lambda also has a build_zip.py
_spec = importlib.util.spec_from_file_location("scout_build_zip", SCOUT / "build_zip.py")
scout_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scout_build)


def test_assemble_ships_the_scout_modules_and_not_the_build_script(tmp_path):
    scout_build.assemble(tmp_path)
    files = {p.name for p in tmp_path.iterdir()}
    assert {"lambda_handler.py", "config.py", "sector_scout.py", "llm_analyzer.py", "reddit_analyzer.py", "consolidator.py",
            "news_fetcher.py", "reddit_fetcher.py", "sgmllib.py"} <= files
    assert "build_zip.py" not in files


def test_shims_never_overwrite_what_pip_installed(tmp_path):
    (tmp_path / "six.py").write_text("PIP VERSION")
    (tmp_path / "typing_extensions.py").write_text("PIP VERSION")
    scout_build.assemble(tmp_path)
    assert (tmp_path / "six.py").read_text() == "PIP VERSION" and (tmp_path / "typing_extensions.py").read_text() == "PIP VERSION"
    assert (tmp_path / "sgmllib.py").read_text() != "PIP VERSION"          # sgmllib3k has no wheel: always from the repo


def test_every_requirement_is_pinned_exactly():
    lines = [l.strip() for l in (SCOUT / "requirements-lambda.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert lines and all("==" in l for l in lines)
    assert not any(l.lower().startswith("sgmllib3k") for l in lines)


def test_prune_and_zip_round_trip(tmp_path):
    for d in ["pkg/tests", "pkg/__pycache__", "bin", "pkg/core"]:
        (tmp_path / "b" / d).mkdir(parents=True)
        (tmp_path / "b" / d / "f.py").write_text("x")
    scout_build.prune(tmp_path / "b")
    assert {p.relative_to(tmp_path / "b").as_posix() for p in (tmp_path / "b").rglob("f.py")} == {"pkg/core/f.py"}
    out = tmp_path / "out" / "x.zip"
    scout_build.write_zip(tmp_path / "b", out)
    assert zipfile.ZipFile(out).namelist() == ["pkg/core/f.py"]


def test_zip_size_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(scout_build, "directory_mb", lambda p: 245.0)
    monkeypatch.setattr(scout_build, "install_dependencies", lambda t: None)
    with pytest.raises(SystemExit, match="too close"):
        scout_build.build(tmp_path / "x.zip")


def test_crlf_and_lf_checkouts_build_identical_files(tmp_path, monkeypatch):
    """git on Windows leaves a mix of CRLF and LF files; the zip must not depend on it."""
    lf, crlf = tmp_path / "lf", tmp_path / "crlf"
    for d, text in ((lf, b"a = 1\nb = 2\n"), (crlf, b"a = 1\r\nb = 2\r\n")):
        d.mkdir()
        (d / "lambda_handler.py").write_bytes(text)
    outputs = []
    for name, src in (("out_lf", lf), ("out_crlf", crlf)):
        monkeypatch.setattr(scout_build, "HERE", src)
        out = tmp_path / name
        out.mkdir()
        scout_build.assemble(out)
        outputs.append((out / "lambda_handler.py").read_bytes())
    assert outputs[0] == outputs[1] == b"a = 1\nb = 2\n"


def test_binary_files_are_copied_untouched(tmp_path):
    src = tmp_path / "blob.bin"
    src.write_bytes(b"\x00\r\n\xff")
    dest = tmp_path / "out" / "blob.bin"
    scout_build.copy_normalised(src, dest)
    assert dest.read_bytes() == b"\x00\r\n\xff"
