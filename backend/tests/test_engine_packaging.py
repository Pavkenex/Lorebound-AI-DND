"""Offline smoke for the engine packaged into the backend (phase 2 P1).

The rebuilt engine (``engine/``, py3.13, stdlib-only) is installed into this venv
— dev: ``uv pip install --editable engine``; image: ``pip install /opt/engine`` —
and imported in-process (docs/INTEGRATION_PLAN.md §1). Nothing here touches the
network: building an adapter is pure construction, and the runtime key is an
in-memory argument only (plan §4).
"""
import tomllib
from pathlib import Path

import engine
import pytest
from engine.models import ProviderConfig
from engine.play import PlaySession
from engine.providers import registry
from engine.store import SCHEMA_PATH

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_KEY = "sk-runtime-only-not-persisted"


def test_engine_package_imports():
    assert engine.__version__ == "0.1.0"
    assert callable(PlaySession.start)
    assert callable(PlaySession.act)


def test_schema_sql_ships_with_the_wheel():
    # Store reads schema.sql on every connect; a non-editable install (the image
    # path) only ships it when pyproject declares it as package data.
    assert SCHEMA_PATH.is_file()
    assert "CREATE TABLE" in SCHEMA_PATH.read_text(encoding="utf-8")

    packaging = tomllib.loads(
        (REPO_ROOT / "engine" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert packaging["build-system"]["build-backend"] == "setuptools.build_meta"
    assert packaging["tool"]["setuptools"]["package-data"]["engine"] == ["store/schema.sql"]


def test_build_adapter_constructs_offline():
    cfg = ProviderConfig(
        name="openai",
        model="gpt-4o-mini",
        base_url="https://api.example.test/v1",
        api_mode="openai",
        timeout_s=5.0,
    )
    adapter = registry.build_adapter(cfg, api_key=RUNTIME_KEY)
    assert callable(adapter.complete)
    # Declared capabilities only — a canary probe would make a network call.
    assert adapter.capabilities().native_tools is True
    assert adapter.secrets() == (RUNTIME_KEY,)  # runtime input, held in memory


def test_capability_cache_key_has_no_key_material():
    cfg = ProviderConfig(
        name="openai",
        model="gpt-4o-mini",
        base_url="https://api.example.test/v1",
        api_mode="openai",
    )
    key = registry.cache_key(cfg)
    assert key == "openai|gpt-4o-mini|https://api.example.test/v1|openai"
    assert RUNTIME_KEY not in key


def test_build_adapter_rejects_unknown_api_mode():
    with pytest.raises(ValueError):
        registry.build_adapter(ProviderConfig(api_mode="carrier-pigeon"))
