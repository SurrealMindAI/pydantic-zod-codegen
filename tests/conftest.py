"""Shared pytest fixtures for pydantic-zod-codegen tests.

Conventions:
    - goldenfile fixtures live under `tests/goldenfile/fixtures/` (committed)
    - expected outputs under `tests/goldenfile/expected/` (committed, regenerable)
    - regeneration command: `make goldenfile-regenerate` (TODO: add Makefile)
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the committed Pydantic model fixtures."""
    return Path(__file__).parent / "goldenfile" / "fixtures"


@pytest.fixture(scope="session")
def expected_dir() -> Path:
    """Path to the committed expected outputs (TS + Zod)."""
    return Path(__file__).parent / "goldenfile" / "expected"
