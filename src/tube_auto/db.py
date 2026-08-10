"""SQLite state.

Three invariants shape this schema, and the first two exist because violating
them cost real money or real channel standing in an earlier version:

1. **Spend ledgers are append-only.** `generations` and `llm_calls` record money
   that has already left the account. Nothing deletes from them. An earlier
   version stored spend on the same row as the video file, so the review UI's
   regenerate button erased $8 of real spend from the budget guard's view.

2. **One idea produces at most one upload.** `posts` is keyed `UNIQUE(idea_id)`,
   so a crash between "uploaded" and "recorded" cannot cause a second upload of
   the same idea. Duplicate uploads are how a channel gets flagged for reused
   content, and that penalty applies to the whole channel.

3. **Every factual claim traces to a source.** `sources` holds the primary
   references collected during research, each with a short ref like `S1`. The
   script must cite them, and a script containing an uncited number is rejected
   before anything is narrated. This is what separates the channel from the
   "AI-rewrote-a-summary" content YouTube demonetised in early 2026.

Derived artifacts (`scripts`, `narrations`, `assets`, `renders`) are freely
replaceable — regenerating them is cheap and local.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from . import paths

SCHEMA_VERSION = 2

SCHEMA = """
-- One planned video.
CREATE TABLE IF NOT EXISTS ideas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     TEXT    NOT NULL,   -- theme id, e.g. black_holes
    -- The target channel. One idea serves one channel: posting the same video
    -- to two channels is reused content, and the penalty is channel-wide.
    lang          TEXT    NOT NULL,
    hook          TEXT    NOT NULL,   -- the video title
    scene_summary TEXT    NOT NULL DEFAULT '',  -- the angle, used for dedup
    why_now       TEXT    NOT NULL DEFAULT '',  -- which observation prompted it
    -- What research decided each chapter covers. The brand supplies the shape;
    -- this fills in the content, and the script stage reads both.
    plan_json     TEXT    NOT NULL DEFAULT '[]',
    tags_json     TEXT    NOT NULL DEFAULT '[]',
    dedup_key     TEXT    NOT NULL UNIQUE,
    status        TEXT    NOT NULL DEFAULT 'ideated',
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);

-- Primary references gathered during research. `ref` is the citation key the
-- script must use (S1, S2, ...). Deleting an idea drops its sources with it.
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id      INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    ref          TEXT    NOT NULL,
    kind         TEXT    NOT NULL,     -- nasa | jpl | arxiv
    url          TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    published_at TEXT,
    summary      TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL,
    UNIQUE (idea_id, ref)
);

-- The script. Two texts on purpose: `display_text` is what the subtitles show,
-- `spoken_text` has numbers, units and proper nouns opened into kana because
-- Google's Japanese TTS mis-reads them otherwise.
CREATE TABLE IF NOT EXISTS scripts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id       INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    chapters_json TEXT    NOT NULL,   -- [{title, lines:[{speaker, display, spoken, refs}]}]
    hooks_json    TEXT    NOT NULL DEFAULT '[]',  -- 3 opening variants to compare
    char_count    INTEGER NOT NULL DEFAULT 0,
    model         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    UNIQUE (idea_id)
);

-- Synthesised narration, with the timecode of every chapter so the assembler
-- can cut visuals to the audio rather than guessing.
CREATE TABLE IF NOT EXISTS narrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id       INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    path          TEXT    NOT NULL,
    duration_s    REAL    NOT NULL DEFAULT 0,
    chars         INTEGER NOT NULL DEFAULT 0,
    timeline_json TEXT    NOT NULL DEFAULT '[]',  -- [{chapter, start_s, end_s}]
    voices_json   TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL,
    UNIQUE (idea_id)
);

-- Visual material. `credit` and `license_ok` exist because NASA's library mixes
-- in third-party copyrighted work, which must never reach an upload.
CREATE TABLE IF NOT EXISTS assets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id    INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,   -- footage | still | diagram | concept | thumb
    path       TEXT    NOT NULL,
    chapter    INTEGER,
    order_idx  INTEGER NOT NULL DEFAULT 0,
    duration_s REAL    NOT NULL DEFAULT 0,
    source_url TEXT    NOT NULL DEFAULT '',
    nasa_id    TEXT,
    credit     TEXT    NOT NULL DEFAULT '',
    license_ok INTEGER NOT NULL DEFAULT 0,
    meta_json  TEXT    NOT NULL DEFAULT '{}',
    created_at TEXT    NOT NULL
);

-- APPEND-ONLY spend ledger for video generation. One row per paid API call.
-- idea_id is nullable so ad-hoc `generate --prompt` runs are still billed here.
CREATE TABLE IF NOT EXISTS generations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id     INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    path        TEXT,
    backend     TEXT    NOT NULL,
    model       TEXT    NOT NULL,
    duration_s  REAL    NOT NULL DEFAULT 0,
    cost_usd    REAL    NOT NULL DEFAULT 0,
    outcome     TEXT    NOT NULL DEFAULT 'ok',
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL
);

-- APPEND-ONLY spend ledger for the ideation model.
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose       TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);

-- Derived, replaceable artifact: the upload-ready file for one idea.
CREATE TABLE IF NOT EXISTS renders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id       INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    path          TEXT    NOT NULL,
    thumb_path    TEXT,
    duration_s    REAL    NOT NULL DEFAULT 0,
    -- Description-ready "00:00 chapter" lines, generated from the narration
    -- timeline. Chapters are what keeps a 20-minute video watchable.
    chapters_text TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    UNIQUE (idea_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id     INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    decision    TEXT    NOT NULL,
    reason_tag  TEXT,
    note        TEXT,
    checks_json TEXT    NOT NULL DEFAULT '{}',
    reviewed_at TEXT    NOT NULL,
    UNIQUE (idea_id)
);

-- UNIQUE(idea_id) is the structural guard against double uploads.
CREATE TABLE IF NOT EXISTS posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id          INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    channel_id       TEXT    NOT NULL,
    youtube_video_id TEXT    NOT NULL,
    title            TEXT    NOT NULL DEFAULT '',
    path             TEXT    NOT NULL DEFAULT '',
    privacy          TEXT    NOT NULL DEFAULT 'private',
    published_at     TEXT    NOT NULL,
    -- Measurement starts when a video becomes visible, not when it was uploaded.
    went_public_at   TEXT,
    created_at       TEXT    NOT NULL,
    UNIQUE (idea_id)
);

CREATE TABLE IF NOT EXISTS stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    window       TEXT    NOT NULL,
    fetched_at   TEXT    NOT NULL,
    views        INTEGER NOT NULL DEFAULT 0,
    likes        INTEGER NOT NULL DEFAULT 0,
    -- NULL means "not measured". 0.0 means "measured, nobody watched".
    -- Conflating the two made unmeasured videos score as 100% retention.
    avg_view_pct REAL,
    UNIQUE (post_id, window)
);

-- Bookkeeping for operator-facing warnings (e.g. sync-stats gone stale).
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    command    TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ok         INTEGER NOT NULL DEFAULT 1,
    detail     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ideas_status      ON ideas(status);
CREATE INDEX IF NOT EXISTS idx_ideas_series      ON ideas(series_id, lang);
CREATE INDEX IF NOT EXISTS idx_sources_idea      ON sources(idea_id);
CREATE INDEX IF NOT EXISTS idx_assets_idea       ON assets(idea_id, kind);
CREATE INDEX IF NOT EXISTS idx_gen_created       ON generations(created_at);
CREATE INDEX IF NOT EXISTS idx_gen_idea          ON generations(idea_id);
CREATE INDEX IF NOT EXISTS idx_llm_created       ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_command      ON runs(command, started_at);
"""

# Tables whose *contents* would be lost by rebuilding. Checked before any
# destructive migration so a database with real history is never silently reset.
DATA_TABLES = ("ideas", "generations", "llm_calls", "posts")


class MigrationRequired(RuntimeError):
    """The database predates the current schema and holds data."""


def connect() -> sqlite3.Connection:
    paths.ensure_work_dirs()
    conn = sqlite3.connect(paths.db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _drop_all_tables(conn: sqlite3.Connection) -> None:
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    conn.execute("PRAGMA foreign_keys = OFF")
    for name in names:
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute("PRAGMA foreign_keys = ON")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Migrate if needed, then guarantee every table exists.

    Migration runs *before* the CREATE statements on purpose. `CREATE TABLE IF
    NOT EXISTS` silently leaves an existing table's old column layout in place,
    so creating first would hide exactly the mismatch this is meant to catch.

    Earlier versions modelled a different product (8-second generated clips, not
    narrated long-form). There is no honest way to map that history onto this
    schema, so a database holding real rows is refused rather than reshaped.
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])

    if version > SCHEMA_VERSION:
        raise MigrationRequired(
            f"database is at schema version {version} but this code understands "
            f"{SCHEMA_VERSION}. Upgrade tube-auto or point TUBE_AUTO_DB elsewhere."
        )

    if version < SCHEMA_VERSION:
        held = {t: _row_count(conn, t) for t in DATA_TABLES}
        if any(held.values()):
            detail = ", ".join(f"{t}={n}" for t, n in held.items() if n)
            raise MigrationRequired(
                f"this database is at schema version {version} and holds data ({detail}). "
                f"Version {SCHEMA_VERSION} models narrated long-form video, which the older "
                "rows cannot describe, so there is no honest automatic migration. Move the "
                f"file aside (mv {paths.db_path()} {paths.db_path()}.v{version}) and run "
                "`tube-auto init` to start clean, keeping the old file as a record of "
                "past spend."
            )
        _drop_all_tables(conn)

    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def init_db() -> None:
    conn = connect()
    try:
        ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Open a connection, ensure the schema, and commit on clean exit.

    Stages that cause external side effects (paid API calls, uploads) must call
    `conn.commit()` themselves right after each one. The commit here is only a
    backstop for read-mostly work; relying on it alone means a crash mid-run
    discards records of money already spent or videos already public.
    """
    conn = connect()
    try:
        ensure_schema(conn)
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
    lang: str,
    hook: str,
    scene_summary: str,
    tags: list[str],
    dedup_key: str,
    why_now: str = "",
    plan: list[dict[str, Any]] | None = None,
) -> int | None:
    """Insert an idea. Returns None when dedup_key already exists."""
    try:
        cursor = conn.execute(
            """
            INSERT INTO ideas
              (series_id, lang, hook, scene_summary, why_now, plan_json,
               tags_json, dedup_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                series_id,
                lang,
                hook,
                scene_summary,
                why_now,
                json.dumps(plan or [], ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                dedup_key,
                now(),
            ),
        )
    except sqlite3.IntegrityError:
        return None
    return int(cursor.lastrowid)


def get_idea(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()


def set_idea_status(conn: sqlite3.Connection, idea_id: int, status: str) -> None:
    from .models import IDEA_STATUSES

    if status not in IDEA_STATUSES:
        raise ValueError(f"unknown idea status {status!r} (known: {IDEA_STATUSES})")
    conn.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))


def bump_attempts(conn: sqlite3.Connection, idea_id: int) -> int:
    """Record that an attempt finished. Returns the new count."""
    conn.execute("UPDATE ideas SET attempts = attempts + 1 WHERE id = ?", (idea_id,))
    row = conn.execute("SELECT attempts FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    return int(row["attempts"]) if row else 0


def reset_attempts(conn: sqlite3.Connection, idea_id: int) -> None:
    conn.execute("UPDATE ideas SET attempts = 0 WHERE id = ?", (idea_id,))


def update_hook(conn: sqlite3.Connection, idea_id: int, hook: str) -> None:
    conn.execute("UPDATE ideas SET hook = ? WHERE id = ?", (hook, idea_id))


def ideas_by_status(conn: sqlite3.Connection, status: str, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM ideas WHERE status = ? ORDER BY id LIMIT ?", (status, limit)
    ).fetchall()


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["status"]: int(row["n"])
        for row in conn.execute("SELECT status, COUNT(*) AS n FROM ideas GROUP BY status")
    }


def recent_ideas(
    conn: sqlite3.Connection, series_id: str, limit: int, lang: str | None = None
) -> list[sqlite3.Row]:
    if lang is None:
        return conn.execute(
            "SELECT * FROM ideas WHERE series_id = ? ORDER BY id DESC LIMIT ?",
            (series_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM ideas WHERE series_id = ? AND lang = ? ORDER BY id DESC LIMIT ?",
        (series_id, lang, limit),
    ).fetchall()


def idea_counts_by_arm(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """(series_id, lang) -> how many ideas exist. Drives the sampler."""
    return {
        (row["series_id"], row["lang"]): int(row["n"])
        for row in conn.execute(
            "SELECT series_id, lang, COUNT(*) AS n FROM ideas GROUP BY series_id, lang"
        )
    }


def recent_rejections(conn: sqlite3.Connection, series_id: str, limit: int) -> list[sqlite3.Row]:
    """Rejected ideas with the reviewer's reason — fed into the next ideate run."""
    return conn.execute(
        """
        SELECT i.scene_summary, i.hook, r.reason_tag, r.note
        FROM reviews r
        JOIN ideas i ON i.id = r.idea_id
        WHERE r.decision = 'reject' AND i.series_id = ?
        ORDER BY r.id DESC LIMIT ?
        """,
        (series_id, limit),
    ).fetchall()


# --- generations (append-only spend ledger) ----------------------------------


def insert_generation(
    conn: sqlite3.Connection,
    *,
    idea_id: int | None,
    path: str | None,
    backend: str,
    model: str,
    duration_s: float,
    cost_usd: float,
    outcome: str = "ok",
    meta: dict[str, Any] | None = None,
) -> int:
    """Record a generation attempt and its cost. Never deleted."""
    cursor = conn.execute(
        """
        INSERT INTO generations
          (idea_id, path, backend, model, duration_s, cost_usd, outcome, meta_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            idea_id,
            path,
            backend,
            model,
            duration_s,
            cost_usd,
            outcome,
            json.dumps(meta or {}, ensure_ascii=False),
            now(),
        ),
    )
    return int(cursor.lastrowid)


def latest_generation(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM generations WHERE idea_id = ? AND outcome = 'ok' ORDER BY id DESC LIMIT 1",
        (idea_id,),
    ).fetchone()


def insert_llm_call(
    conn: sqlite3.Connection,
    *,
    purpose: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (purpose, model, input_tokens, output_tokens, cost_usd, now()),
    )
    return int(cursor.lastrowid)


def spend_since(conn: sqlite3.Connection, iso_timestamp: str) -> float:
    """Every dollar this project has spent since a point in time."""
    video = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM generations WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()["total"]
    llm = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM llm_calls WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()["total"]
    return float(video) + float(llm)


def spend_breakdown(conn: sqlite3.Connection, iso_timestamp: str) -> dict[str, float]:
    video = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS t FROM generations WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()["t"]
    llm = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS t FROM llm_calls WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()["t"]
    return {"video": float(video), "llm": float(llm), "total": float(video) + float(llm)}


def series_costs(conn: sqlite3.Connection) -> dict[tuple[str, str], float]:
    """(series_id, lang) -> total generation spend, including rejected ideas."""
    return {
        (row["series_id"], row["lang"]): float(row["spent"])
        for row in conn.execute(
            """
            SELECT i.series_id, i.lang, COALESCE(SUM(g.cost_usd), 0) AS spent
            FROM generations g
            JOIN ideas i ON i.id = g.idea_id
            GROUP BY i.series_id, i.lang
            """
        )
    }


def adhoc_spend(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS t FROM generations WHERE idea_id IS NULL"
    ).fetchone()
    return float(row["t"])


# --- sources (the citation trail) --------------------------------------------


def replace_sources(conn: sqlite3.Connection, idea_id: int, sources: list[dict[str, Any]]) -> None:
    """Set the primary references for an idea, replacing any earlier set.

    Refs are assigned here (S1, S2, ...) so the script generator and the citation
    check agree on the same keys.
    """
    conn.execute("DELETE FROM sources WHERE idea_id = ?", (idea_id,))
    for index, source in enumerate(sources, start=1):
        conn.execute(
            """
            INSERT INTO sources (idea_id, ref, kind, url, title, published_at, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea_id,
                source.get("ref") or f"S{index}",
                source["kind"],
                source["url"],
                source["title"],
                source.get("published_at"),
                source.get("summary", ""),
                now(),
            ),
        )


def get_sources(conn: sqlite3.Connection, idea_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sources WHERE idea_id = ? ORDER BY id", (idea_id,)
    ).fetchall()


def known_source_urls(conn: sqlite3.Connection, limit: int = 500) -> set[str]:
    """URLs already used, so research does not rebuild yesterday's video."""
    return {
        row["url"]
        for row in conn.execute("SELECT url FROM sources ORDER BY id DESC LIMIT ?", (limit,))
    }


# --- scripts -----------------------------------------------------------------


def upsert_script(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    chapters: list[dict[str, Any]],
    hooks: list[str],
    char_count: int,
    model: str,
) -> int:
    conn.execute(
        """
        INSERT INTO scripts (idea_id, chapters_json, hooks_json, char_count, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(idea_id) DO UPDATE SET
            chapters_json = excluded.chapters_json,
            hooks_json    = excluded.hooks_json,
            char_count    = excluded.char_count,
            model         = excluded.model,
            created_at    = excluded.created_at
        """,
        (
            idea_id,
            json.dumps(chapters, ensure_ascii=False),
            json.dumps(hooks, ensure_ascii=False),
            char_count,
            model,
            now(),
        ),
    )
    return int(conn.execute("SELECT id FROM scripts WHERE idea_id = ?", (idea_id,)).fetchone()["id"])


def get_script(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM scripts WHERE idea_id = ?", (idea_id,)).fetchone()


def recent_script_summaries(conn: sqlite3.Connection, series_id: str, limit: int) -> list[str]:
    return [
        row["scene_summary"]
        for row in conn.execute(
            "SELECT scene_summary FROM ideas WHERE series_id = ? ORDER BY id DESC LIMIT ?",
            (series_id, limit),
        )
        if row["scene_summary"]
    ]


# --- narrations --------------------------------------------------------------


def upsert_narration(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    path: str,
    duration_s: float,
    chars: int,
    timeline: list[dict[str, Any]],
    voices: dict[str, str],
) -> int:
    conn.execute(
        """
        INSERT INTO narrations
          (idea_id, path, duration_s, chars, timeline_json, voices_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idea_id) DO UPDATE SET
            path          = excluded.path,
            duration_s    = excluded.duration_s,
            chars         = excluded.chars,
            timeline_json = excluded.timeline_json,
            voices_json   = excluded.voices_json,
            created_at    = excluded.created_at
        """,
        (
            idea_id,
            path,
            duration_s,
            chars,
            json.dumps(timeline, ensure_ascii=False),
            json.dumps(voices, ensure_ascii=False),
            now(),
        ),
    )
    return int(
        conn.execute("SELECT id FROM narrations WHERE idea_id = ?", (idea_id,)).fetchone()["id"]
    )


def get_narration(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM narrations WHERE idea_id = ?", (idea_id,)).fetchone()


def tts_chars_since(conn: sqlite3.Connection, iso_timestamp: str) -> int:
    """Characters synthesised this period, to watch the free-tier ceiling."""
    row = conn.execute(
        "SELECT COALESCE(SUM(chars), 0) AS n FROM narrations WHERE created_at >= ?",
        (iso_timestamp,),
    ).fetchone()
    return int(row["n"])


# --- assets (visual material) ------------------------------------------------


def replace_assets(
    conn: sqlite3.Connection, idea_id: int, kind: str, assets: list[dict[str, Any]]
) -> None:
    """Set the assets of one kind for an idea, replacing any earlier set."""
    conn.execute("DELETE FROM assets WHERE idea_id = ? AND kind = ?", (idea_id, kind))
    for index, asset in enumerate(assets):
        conn.execute(
            """
            INSERT INTO assets
              (idea_id, kind, path, chapter, order_idx, duration_s, source_url,
               nasa_id, credit, license_ok, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea_id,
                kind,
                asset["path"],
                asset.get("chapter"),
                asset.get("order_idx", index),
                asset.get("duration_s", 0.0),
                asset.get("source_url", ""),
                asset.get("nasa_id"),
                asset.get("credit", ""),
                1 if asset.get("license_ok", False) else 0,
                json.dumps(asset.get("meta", {}), ensure_ascii=False),
                now(),
            ),
        )


def get_assets(
    conn: sqlite3.Connection, idea_id: int, kind: str | None = None
) -> list[sqlite3.Row]:
    if kind is None:
        return conn.execute(
            "SELECT * FROM assets WHERE idea_id = ? ORDER BY chapter, order_idx", (idea_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM assets WHERE idea_id = ? AND kind = ? ORDER BY chapter, order_idx",
        (idea_id, kind),
    ).fetchall()


def set_asset_path(conn: sqlite3.Connection, asset_id: int, path: str, **fields: Any) -> None:
    """Fill in a slot that was reserved before its file existed.

    Diagram slots are planned alongside footage so the visual mix is decided
    once, then rendered later; this is how the later stage claims them.
    """
    columns = ["path = ?"]
    values: list[Any] = [path]
    for key in ("duration_s", "credit", "license_ok", "source_url"):
        if key in fields:
            columns.append(f"{key} = ?")
            values.append(int(fields[key]) if key == "license_ok" else fields[key])
    if "meta" in fields:
        columns.append("meta_json = ?")
        values.append(json.dumps(fields["meta"], ensure_ascii=False))
    values.append(asset_id)
    conn.execute(f"UPDATE assets SET {', '.join(columns)} WHERE id = ?", values)


def unfilled_assets(conn: sqlite3.Connection, idea_id: int, kind: str) -> list[sqlite3.Row]:
    """Reserved slots of one kind that still have no file."""
    return conn.execute(
        "SELECT * FROM assets WHERE idea_id = ? AND kind = ? AND path = '' ORDER BY chapter, order_idx",
        (idea_id, kind),
    ).fetchall()


def unlicensed_assets(conn: sqlite3.Connection, idea_id: int) -> list[sqlite3.Row]:
    """Assets not cleared for use. Publishing with any of these is a rights risk."""
    return conn.execute(
        "SELECT * FROM assets WHERE idea_id = ? AND license_ok = 0", (idea_id,)
    ).fetchall()


# --- renders (derived, replaceable) ------------------------------------------


def upsert_render(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    path: str,
    thumb_path: str | None,
    duration_s: float = 0.0,
    chapters_text: str = "",
) -> int:
    """Replace the render for an idea. Safe to re-run after a partial failure."""
    conn.execute(
        """
        INSERT INTO renders (idea_id, path, thumb_path, duration_s, chapters_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(idea_id) DO UPDATE SET
            path          = excluded.path,
            thumb_path    = excluded.thumb_path,
            duration_s    = excluded.duration_s,
            chapters_text = excluded.chapters_text,
            created_at    = excluded.created_at
        """,
        (idea_id, path, thumb_path, duration_s, chapters_text, now()),
    )
    row = conn.execute("SELECT id FROM renders WHERE idea_id = ?", (idea_id,)).fetchone()
    return int(row["id"])


def get_render(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM renders WHERE idea_id = ?", (idea_id,)).fetchone()


def delete_render(conn: sqlite3.Connection, idea_id: int) -> None:
    """Drop a derived render. Spend ledgers are untouched by design."""
    if conn.execute("SELECT 1 FROM posts WHERE idea_id = ?", (idea_id,)).fetchone():
        raise ValueError(
            f"idea {idea_id} has already been uploaded; its render must not be replaced"
        )
    conn.execute("DELETE FROM renders WHERE idea_id = ?", (idea_id,))


# --- reviews -----------------------------------------------------------------


def insert_review(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    decision: str,
    reason_tag: str | None = None,
    note: str | None = None,
    checks: dict[str, bool] | None = None,
) -> None:
    """Record a human decision. Re-deciding overwrites the previous call."""
    conn.execute(
        """
        INSERT INTO reviews (idea_id, decision, reason_tag, note, checks_json, reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(idea_id) DO UPDATE SET
            decision    = excluded.decision,
            reason_tag  = excluded.reason_tag,
            note        = excluded.note,
            checks_json = excluded.checks_json,
            reviewed_at = excluded.reviewed_at
        """,
        (idea_id, decision, reason_tag, note, json.dumps(checks or {}), now()),
    )


def delete_review(conn: sqlite3.Connection, idea_id: int) -> None:
    conn.execute("DELETE FROM reviews WHERE idea_id = ?", (idea_id,))


def pending_reviews(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Assembled ideas that nobody has decided on yet.

    The status is `assembled`, not the `post_processed` this once looked for.
    That status no longer exists — `set_idea_status` rejects it — so the queue was
    permanently empty and nothing could ever be approved.
    """
    return conn.execute(
        """
        SELECT i.*, r.path AS render_path, r.thumb_path, r.duration_s, r.chapters_text
        FROM ideas i
        JOIN renders r ON r.idea_id = i.id
        LEFT JOIN reviews v ON v.idea_id = i.id
        WHERE i.status = 'assembled' AND v.id IS NULL
        ORDER BY i.id
        """
    ).fetchall()


def set_render_thumb(conn: sqlite3.Connection, idea_id: int, thumb_path: str) -> None:
    """Point a render at the thumbnail that ships with its upload."""
    conn.execute("UPDATE renders SET thumb_path = ? WHERE idea_id = ?", (thumb_path, idea_id))


def ideas_needing_thumbnails(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rendered ideas with no thumbnail yet.

    Without one YouTube picks a frame at random, and a random frame from a
    twenty-minute explainer is usually a starfield with no text on it.
    """
    return conn.execute(
        """
        SELECT i.*, r.path AS render_path, r.thumb_path
        FROM ideas i
        JOIN renders r ON r.idea_id = i.id
        WHERE r.thumb_path IS NULL
          AND i.status IN ('assembled', 'approved')
        ORDER BY i.id
        """
    ).fetchall()


def render_for(conn: sqlite3.Connection, idea_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM renders WHERE idea_id = ?", (idea_id,)).fetchone()


# --- posts & stats -----------------------------------------------------------


def insert_post(
    conn: sqlite3.Connection,
    *,
    idea_id: int,
    channel_id: str,
    youtube_video_id: str,
    title: str,
    path: str,
    privacy: str,
) -> int:
    """Record an upload. UNIQUE(idea_id) makes a second upload impossible."""
    cursor = conn.execute(
        """
        INSERT INTO posts
          (idea_id, channel_id, youtube_video_id, title, path, privacy, published_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (idea_id, channel_id, youtube_video_id, title, path, privacy, now(), now()),
    )
    return int(cursor.lastrowid)


def mark_public(conn: sqlite3.Connection, post_id: int) -> None:
    conn.execute(
        "UPDATE posts SET privacy = 'public', went_public_at = ? WHERE id = ?",
        (now(), post_id),
    )


def publishable_ideas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Approved ideas with a render and no post yet."""
    return conn.execute(
        """
        SELECT i.*, r.path AS render_path, r.thumb_path, r.chapters_text
        FROM ideas i
        JOIN renders r ON r.idea_id = i.id
        WHERE i.status = 'approved'
          AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.idea_id = i.id)
        ORDER BY i.attempts, i.id
        """
    ).fetchall()


def private_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Uploaded but not yet visible. Until these go public they earn no views."""
    return conn.execute(
        """
        SELECT p.*, i.series_id, i.lang
        FROM posts p
        JOIN ideas i ON i.id = p.idea_id
        WHERE p.privacy <> 'public'
        ORDER BY p.id
        """
    ).fetchall()


def posts_created_since(conn: sqlite3.Connection, iso_timestamp: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM posts WHERE created_at >= ?", (iso_timestamp,)
    ).fetchone()
    return int(row["n"])


def measurable_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Public posts. Private ones cannot accumulate views, so measuring them is noise."""
    return conn.execute(
        """
        SELECT p.*, i.series_id, i.lang
        FROM posts p
        JOIN ideas i ON i.id = p.idea_id
        WHERE p.privacy = 'public' AND p.went_public_at IS NOT NULL
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
    avg_view_pct: float | None,
) -> None:
    conn.execute(
        """
        INSERT INTO stats (post_id, window, fetched_at, views, likes, avg_view_pct)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(post_id, window) DO UPDATE SET
            fetched_at   = excluded.fetched_at,
            views        = excluded.views,
            likes        = excluded.likes,
            avg_view_pct = excluded.avg_view_pct
        """,
        (post_id, window, now(), views, likes, avg_view_pct),
    )


def recorded_windows(conn: sqlite3.Connection) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for row in conn.execute("SELECT post_id, window FROM stats"):
        out.setdefault(int(row["post_id"]), set()).add(row["window"])
    return out


def arm_stats(conn: sqlite3.Connection, window: str) -> list[sqlite3.Row]:
    """Per-video measurements for one window, tagged with their A/B arm."""
    return conn.execute(
        """
        SELECT i.series_id, i.lang, s.views, s.avg_view_pct
        FROM stats s
        JOIN posts p ON p.id = s.post_id
        JOIN ideas i ON i.id = p.idea_id
        WHERE s.window = ?
        """,
        (window,),
    ).fetchall()


# --- run bookkeeping ---------------------------------------------------------


def record_run(conn: sqlite3.Connection, command: str, ok: bool, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO runs (command, started_at, ok, detail) VALUES (?, ?, ?, ?)",
        (command, now(), 1 if ok else 0, detail),
    )


def last_run(conn: sqlite3.Connection, command: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE command = ? ORDER BY id DESC LIMIT 1", (command,)
    ).fetchone()
