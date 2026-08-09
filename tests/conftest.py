from __future__ import annotations

import shutil

import pytest

from tube_auto import config, db, paths, reading

# Every directory the pipeline writes into. Kept in one place so a new stage
# adding a directory cannot quietly start writing into the real work/ during tests.
WORK_SUBDIRS = ("FOOTAGE_DIR", "STILLS_DIR", "DIAGRAMS_DIR", "AUDIO_DIR", "RENDERS_DIR", "THUMBS_DIR")


@pytest.fixture(autouse=True)
def temp_work(tmp_path, monkeypatch):
    """Redirect every work path at a throwaway directory.

    Autouse because a test that quietly writes into the real work/ can leave a
    lock file or an orphaned render behind and change the next test's outcome.
    """
    work = tmp_path / "work"
    monkeypatch.setattr(paths, "WORK_DIR", work)
    for name in WORK_SUBDIRS:
        monkeypatch.setattr(paths, name, work / name.removesuffix("_DIR").lower())
    monkeypatch.setenv("TUBE_AUTO_DB", str(work / "test.db"))
    paths.ensure_work_dirs()
    return work


@pytest.fixture
def temp_db():
    """An open connection to a fresh database."""
    db.init_db()
    with db.session() as conn:
        yield conn


@pytest.fixture(autouse=True)
def clear_caches():
    config.reset_cache()
    reading.reset_cache()
    yield
    config.reset_cache()
    reading.reset_cache()


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
