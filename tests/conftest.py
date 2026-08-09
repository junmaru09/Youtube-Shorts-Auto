from __future__ import annotations

import shutil

import pytest

from shorts_auto import config, db, paths


@pytest.fixture(autouse=True)
def temp_work(tmp_path, monkeypatch):
    """Redirect every work path at a throwaway directory.

    Autouse because a test that quietly writes into the real work/ can leave a
    lock file or an orphaned render behind and change the next test's outcome.
    """
    work = tmp_path / "work"
    monkeypatch.setattr(paths, "WORK_DIR", work)
    monkeypatch.setattr(paths, "ASSETS_DIR", work / "assets")
    monkeypatch.setattr(paths, "RENDERS_DIR", work / "renders")
    monkeypatch.setattr(paths, "THUMBS_DIR", work / "thumbs")
    monkeypatch.setenv("SHORTS_AUTO_DB", str(work / "test.db"))
    paths.ensure_work_dirs()
    return work


@pytest.fixture
def temp_db():
    """An open connection to a fresh database."""
    db.init_db()
    with db.session() as conn:
        yield conn


@pytest.fixture(autouse=True)
def clear_config_cache():
    config.reset_cache()
    yield
    config.reset_cache()


@pytest.fixture
def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
