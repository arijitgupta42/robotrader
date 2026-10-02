import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "financial_market_news_analyzer"))

import llm_analyzer  # noqa: E402


@pytest.fixture(autouse=True)
def no_ssm_and_clean_cache(monkeypatch):
    """Make the SSM lookup fail (as it does locally) and reset the key cache."""
    monkeypatch.setitem(sys.modules, "boto3", None)          # `import boto3` raises ImportError
    monkeypatch.setattr(llm_analyzer, "_cached_api_key", None)


def test_falls_back_to_environment_variable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-or-test-key  ")
    assert llm_analyzer._load_api_key() == "sk-or-test-key"
    assert llm_analyzer._get_headers()["Authorization"] == "Bearer sk-or-test-key"


def test_raises_when_neither_source_has_a_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="OPENROUTER_API_KEY"):
        llm_analyzer._load_api_key()


def test_blank_environment_variable_counts_as_missing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    with pytest.raises(EnvironmentError):
        llm_analyzer._load_api_key()
