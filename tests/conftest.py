from __future__ import annotations

import pytest

from shorts_auto import config, db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the whole package at a throwaway SQLite file."""
    monkeypatch.setenv("SHORTS_AUTO_DB", str(tmp_path / "test.db"))
    db.init_db()
    with db.session() as conn:
        yield conn


@pytest.fixture(autouse=True)
def clear_config_cache():
    config.reset_cache()
    yield
    config.reset_cache()
