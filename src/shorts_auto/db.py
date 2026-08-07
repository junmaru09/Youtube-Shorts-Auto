"""SQLite state. Every stage is idempotent and driven off the `status` column."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     TEXT    NOT NULL,
    hook_json     TEXT    NOT NULL,
    video_prompt  TEXT    NOT NULL,
    scene_summary TEXT    NOT NULL DEFAULT '',
    tags_json     TEXT    NOT NULL DEFAULT '{}',
    dedup_key     TEXT    NOT NULL UNIQUE,
    status        TEXT    NOT NULL DEFAULT 'ideated',
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- nullable: ad-hoc `generate --prompt` runs have no idea, but must still
    -- be billed to the ledger or the budget guard can be bypassed.
    idea_id     INTEGER REFERENCES ideas(id) ON DELETE CASCADE,
    lang        TEXT    NOT NULL DEFAULT 'src',
    path        TEXT    NOT NULL,
    backend     TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    duration_s  REAL    NOT NULL DEFAULT 0,
    cost_usd    REAL    NOT NULL DEFAULT 0,
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    decision    TEXT    NOT NULL,
    reason_tag  TEXT,
    note        TEXT,
    reviewed_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id         INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    channel_id       TEXT    NOT NULL,
    youtube_video_id TEXT    NOT NULL,
    title            TEXT    NOT NULL DEFAULT '',
    privacy          TEXT    NOT NULL DEFAULT 'private',
    published_at     TEXT    NOT NULL,
    UNIQUE (asset_id, channel_id)
);

CREATE TABLE IF NOT EXISTS stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    window       TEXT    NOT NULL,
    fetched_at   TEXT    NOT NULL,
    views        INTEGER NOT NULL DEFAULT 0,
    likes        INTEGER NOT NULL DEFAULT 0,
    avg_view_pct REAL    NOT NULL DEFAULT 0,
    UNIQUE (post_id, window)
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);
CREATE INDEX IF NOT EXISTS idx_assets_idea  ON assets(idea_id);
CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created_at);
"""


def connect() -> sqlite3.Connection:
    paths.ensure_work_dirs()
    conn = sqlite3.connect(paths.db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Open a connection with the schema guaranteed to exist."""
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- ideas -------------------------------------------------------------------


def insert_idea(
    conn: sqlite3.Connection,
    *,
    series_id: str,
    hook: dict[str, str],
    video_prompt: str,
    scene_summary: str,
    tags: dict[str, list[str]],
    dedup_key: str,
) -> int | None:
    """Insert an idea. Returns None when dedup_key already exists."""
    try:
        cursor = conn.execute(
            """
            INSERT INTO ideas
              (series_id, hook_json, video_prompt, scene_summary, tags_json, dedup_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series_id,
                json.dumps(hook, ensure_ascii=False),
                video_prompt,
                scene_summary,
                json.dumps(tags, ensure_ascii=False),
                dedup_key,
                now(),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return int(cursor.lastrowid)


def set_idea_status(conn: sqlite3.Connection, idea_id: int, status: str) -> None:
    conn.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))


def bump_attempts(conn: sqlite3.Connection, idea_id: int) -> int:
    conn.execute("UPDATE ideas SET attempts = attempts + 1 WHERE id = ?", (idea_id,))
    row = conn.execute("SELECT attempts FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    return int(row["attempts"]) if row else 0


def ideas_by_status(conn: sqlite3.Connection, status: str, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ideas WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
    ).fetchall()


def recent_ideas(conn: sqlite3.Connection, series_id: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ideas WHERE series_id = ? ORDER BY id DESC LIMIT ?",
        (series_id, limit),
    ).fetchall()


def dedup_keys(conn: sqlite3.Connection) -> set[str]:
    return {row["dedup_key"] for row in conn.execute("SELECT dedup_key FROM ideas")}


def recent_rejections(conn: sqlite3.Connection, series_id: str, limit: int) -> list[sqlite3.Row]:
    """Rejected ideas plus the reviewer's reason — fed back into the next ideate run."""
    return conn.execute(
        """
        SELECT i.scene_summary, i.hook_json, r.reason_tag, r.note
        FROM reviews r
        JOIN assets a ON a.id = r.asset_id
        JOIN ideas  i ON i.id = a.idea_id
        WHERE r.decision = 'reject' AND i.series_id = ?
        ORDER BY r.id DESC LIMIT ?
        """,
        (series_id, limit),
    ).fetchall()


# --- assets ------------------------------------------------------------------


def insert_asset(
    conn: sqlite3.Connection,
    *,
    idea_id: int | None,
    path: str,
    backend: str,
    model: str,
    duration_s: float,
    cost_usd: float,
    lang: str = "src",
    meta: dict[str, Any] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO assets (idea_id, lang, path, backend, model, duration_s, cost_usd, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            idea_id,
            lang,
            path,
            backend,
            model,
            duration_s,
            cost_usd,
            json.dumps(meta or {}, ensure_ascii=False),
            now(),
        ),
    )
    return int(cursor.lastrowid)


def assets_for_idea(conn: sqlite3.Connection, idea_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM assets WHERE idea_id = ? ORDER BY id", (idea_id,)).fetchall()


def spend_since(conn: sqlite3.Connection, iso_timestamp: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM assets WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()
    return float(row["total"])


# --- reviews -----------------------------------------------------------------


def insert_review(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    decision: str,
    reason_tag: str | None = None,
    note: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO reviews (asset_id, decision, reason_tag, note, reviewed_at) VALUES (?, ?, ?, ?, ?)",
        (asset_id, decision, reason_tag, note, now()),
    )
    return int(cursor.lastrowid)


def pending_review_assets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Post-processed assets that nobody has decided on yet."""
    return conn.execute(
        """
        SELECT a.*, i.series_id, i.hook_json, i.scene_summary, i.status AS idea_status
        FROM assets a
        JOIN ideas i ON i.id = a.idea_id
        LEFT JOIN reviews r ON r.asset_id = a.id
        WHERE i.status = 'post_processed' AND r.id IS NULL
        ORDER BY a.id
        """
    ).fetchall()


# --- posts & stats -----------------------------------------------------------


def insert_post(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    channel_id: str,
    youtube_video_id: str,
    title: str,
    privacy: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO posts (asset_id, channel_id, youtube_video_id, title, privacy, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (asset_id, channel_id, youtube_video_id, title, privacy, now()),
    )
    return int(cursor.lastrowid)


def posts_published_since(conn: sqlite3.Connection, iso_timestamp: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE published_at >= ?", (iso_timestamp,)
    ).fetchone()
    return int(row["n"])


def all_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*, a.idea_id, i.series_id
        FROM posts p
        JOIN assets a ON a.id = p.asset_id
        JOIN ideas  i ON i.id = a.idea_id
        ORDER BY p.id
        """
    ).fetchall()


def upsert_stats(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    window: str,
    views: int,
    likes: int,
    avg_view_pct: float,
) -> None:
    conn.execute(
        """
        INSERT INTO stats (post_id, window, fetched_at, views, likes, avg_view_pct)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id, window) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            views = excluded.views,
            likes = excluded.likes,
            avg_view_pct = excluded.avg_view_pct
        """,
        (post_id, window, now(), views, likes, avg_view_pct),
    )


def series_stats(conn: sqlite3.Connection, window: str = "72h") -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT i.series_id, s.views, s.avg_view_pct
        FROM stats s
        JOIN posts  p ON p.id = s.post_id
        JOIN assets a ON a.id = p.asset_id
        JOIN ideas  i ON i.id = a.idea_id
        WHERE s.window = ?
        """,
        (window,),
    ).fetchall()
