"""Command line entry point.

The daily loop:

    tube-auto doctor        is anything misconfigured?
    tube-auto status        what state is everything in, and what is next?
    tube-auto research      choose a topic and gather its primary sources
    tube-auto script        write it, with every number cited
    tube-auto narrate       synthesise the audio and learn the real timings
    tube-auto footage       pull NASA material for the timeline
    tube-auto diagrams      draw the figures NASA does not have
    tube-auto assemble      cut it together
    streamlit run review_app.py   watch it and approve
    tube-auto publish       upload as private
    tube-auto go-live       make it visible (nothing is measured until this)
    tube-auto sync-stats    pull view counts back in
    tube-auto report        the numbers that decide expand-or-stop

`tube-auto build` runs research through assemble in one go.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import config, db, paths

log = logging.getLogger(__name__)


def _setup_logging(verbose: bool) -> None:
    """Log to stderr and to a file.

    An unattended run that fails at 3am leaves nothing behind if stderr is the
    only sink.
    """
    paths.ensure_work_dirs()
    log_dir = paths.WORK_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        file_handler = logging.FileHandler(log_dir / "tube-auto.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        handlers.append(file_handler)
    except OSError:
        pass  # a read-only work dir must not stop the command

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(paths.ROOT / ".env")


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {number}")
    return number


def _report(name: str, result, *, errors_attr: str = "errors") -> int:
    """Print a stage result the same way for every stage."""
    fields = {
        f: getattr(result, f)
        for f in result.__slots__
        if f not in (errors_attr, "details", "topics")
    }
    summary = ", ".join(f"{k}={v}" for k, v in fields.items())
    print(f"{name}: {summary}")
    problems = getattr(result, errors_attr, [])
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if not problems else 1


# --- commands ----------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    db.init_db()
    print(f"database ready at {paths.db_path()}")
    problems = config.validate_all()
    if problems:
        print("\nconfiguration problems:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1
    arms = config.arms(include_disabled=True)
    print(f"{len(config.load_themes(include_disabled=True))} themes, {len(arms)} A/B arms:")
    for arm in arms:
        print(f"  {arm.key}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import render, run_checks

    checks = run_checks()
    print(render(checks))
    return 1 if any(not c.ok and c.blocking for c in checks) else 0


def cmd_status(args: argparse.Namespace) -> int:
    from .models import IDEA_STATUSES
    from .scoring import allocation

    with db.session() as conn:
        counts = db.status_counts(conn)
        private = len(db.private_posts(conn))
        live = len(db.measurable_posts(conn))
        pending = len(db.pending_reviews(conn))

    print("\nパイプラインの状態")
    print("=" * 62)
    for status in IDEA_STATUSES:
        if counts.get(status):
            print(f"  {status:<16} {counts[status]:>4}")
    print(f"  {'レビュー待ち':<16} {pending:>4}")
    print(f"  {'private のまま':<16} {private:>4}")
    print(f"  {'公開済み':<16} {live:>4}")

    print("\n次にやること")
    print("=" * 62)
    nexts = [
        ("researched", "tube-auto script", "台本待ち"),
        ("scripted", "tube-auto narrate", "音声待ち"),
        ("narrated", "tube-auto footage", "素材待ち"),
        ("sourced", "tube-auto diagrams && tube-auto assemble", "図解・合成待ち"),
        ("approved", "tube-auto publish", "投稿待ち"),
    ]
    printed = False
    for status, command, label in nexts:
        if counts.get(status):
            print(f"  {command:<42} ({counts[status]} 件 {label})")
            printed = True
    if pending:
        print(f"  {'streamlit run review_app.py':<42} ({pending} 件がレビュー待ち)")
        printed = True
    if private:
        print(f"  {'tube-auto go-live':<42} ({private} 件が private = 再生されない)")
        printed = True
    if live:
        print(f"  {'tube-auto sync-stats':<42} (毎日実行)")
        printed = True
    if not printed:
        print("  tube-auto research                         (キューが空です)")

    print("\n現在の配分")
    print("=" * 62)
    for key, share in sorted(allocation().items(), key=lambda kv: -kv[1]):
        print(f"  {key:<30} {share:>6.1%}")
    print()
    return 0


def cmd_themes(args: argparse.Namespace) -> int:
    from .scoring import allocation

    shares = allocation()
    for theme in config.load_themes(include_disabled=True):
        state = "on " if theme.enabled else "off"
        print(f"[{state}] {theme.id:<16} {theme.description}")
        for lang in theme.languages:
            key = f"{theme.id}:{lang}"
            print(f"        {key:<26} share={shares.get(key, 0.0):>6.1%}")
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    from .stages import research

    result = research.run(count=args.count, theme_id=args.theme, dry_run=args.dry_run)
    print(
        f"research: {result.created} planned, {result.duplicates} duplicates, "
        f"{result.failed} failed (LLM ${result.llm_cost_usd:.4f})"
    )
    for topic in result.topics:
        print(f"  [{topic['series_id']}] {topic['hook']}")
        for source in topic.get("sources", []):
            print(f"      [{source['ref']}] {source['title'][:64]}")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.created else 1


def cmd_script(args: argparse.Namespace) -> int:
    from .stages import script

    result = script.run(limit=args.limit, idea_id=args.idea, dry_run=args.dry_run)
    print(
        f"script: {result.written} written, {result.rejected} rejected "
        f"(LLM ${result.llm_cost_usd:.4f})"
    )
    for detail in result.details:
        print(f"  idea {detail['idea_id']}: {detail['chars']} chars (~{detail['chars'] / 400:.1f} min)")
        for index, hook in enumerate(detail.get("hooks", []), start=1):
            print(f"      冒頭案{index}: {hook}")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.written else 1


def cmd_narrate(args: argparse.Namespace) -> int:
    from .stages import narrate

    result = narrate.run(limit=args.limit, idea_id=args.idea, provider=args.provider)
    print(f"narrate: {result.narrated} narrated, {result.failed} failed, {result.chars:,} chars")
    for detail in result.details:
        print(f"  idea {detail['idea_id']}: {detail['duration_s'] / 60:.1f} min")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_footage(args: argparse.Namespace) -> int:
    from .stages import footage

    result = footage.run(limit=args.limit, idea_id=args.idea)
    print(
        f"footage: {result.sourced} sourced, {result.clips} clips, {result.stills} stills, "
        f"{result.covered_ratio:.0%} of the timeline covered"
    )
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_diagrams(args: argparse.Namespace) -> int:
    from .stages import diagrams

    result = diagrams.run(limit=args.limit, idea_id=args.idea)
    print(f"diagrams: {result.drawn} drawn across {result.ideas} idea(s), {result.failed} failed")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_assemble(args: argparse.Namespace) -> int:
    from .stages import assemble

    result = assemble.run(limit=args.limit, idea_id=args.idea)
    print(f"assemble: {result.assembled} assembled, {result.failed} failed")
    for detail in result.details:
        print(f"  idea {detail['idea_id']}: {detail['duration_s'] / 60:.1f} min -> {detail['path']}")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_thumbnails(args: argparse.Namespace) -> int:
    from .stages import thumbnails

    result = thumbnails.run(limit=args.limit, idea_id=args.idea)
    print(f"thumbnails: {result.made} 本ぶん作成, {result.failed} 失敗")
    for detail in result.details:
        print(f"  idea {detail['idea_id']}:")
        for path in detail["paths"]:
            print(f"    {path}")
    if result.made:
        print("  → review_app で3案を見比べ、YouTube Studio の Test & Compare にかけてください")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_build(args: argparse.Namespace) -> int:
    """Research through assemble, stopping at the first stage that produces nothing."""
    steps = [
        ("research", lambda: cmd_research(argparse.Namespace(count=1, theme=args.theme, dry_run=False))),
        ("script", lambda: cmd_script(argparse.Namespace(limit=1, idea=None, dry_run=False))),
        ("narrate", lambda: cmd_narrate(argparse.Namespace(limit=1, idea=None, provider=args.provider))),
        ("footage", lambda: cmd_footage(argparse.Namespace(limit=1, idea=None))),
        ("diagrams", lambda: cmd_diagrams(argparse.Namespace(limit=1, idea=None))),
        ("assemble", lambda: cmd_assemble(argparse.Namespace(limit=1, idea=None))),
    ]
    for name, step in steps:
        print(f"\n=== {name} ===")
        code = step()
        if code != 0:
            print(f"\nbuild stopped at {name}", file=sys.stderr)
            return code
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from .stages import publish

    result = publish.run(limit=args.limit, privacy=args.privacy, dry_run=args.dry_run)
    verb = "would upload" if args.dry_run else "uploaded"
    print(f"publish: {result.published} {verb}, {result.skipped} skipped, {result.failed} failed")
    for line in result.notes:
        print(f"  {line}")
    return 0 if result.failed == 0 else 1


def cmd_go_live(args: argparse.Namespace) -> int:
    from .stages import golive

    result = golive.run(limit=args.limit, dry_run=args.dry_run)
    verb = "would publish" if args.dry_run else "now public"
    print(f"go-live: {result.went_public} {verb}, {result.failed} failed")
    for line in result.notes:
        print(f"  {line}")
    return 0 if result.failed == 0 else 1


def cmd_sync_stats(args: argparse.Namespace) -> int:
    from .stages import analytics

    result = analytics.run()
    print(f"sync-stats: {result.updated} rows updated across {result.posts} public posts")
    if result.missed:
        print(f"  ! {len(result.missed)} measurement window(s) expired unrecorded:")
        for item in result.missed[:10]:
            print(f"    {item}")
        print("    run sync-stats daily; an expired window cannot be reconstructed")
    for problem in result.errors:
        print(f"  ! {problem}", file=sys.stderr)
    return 0 if not result.errors else 1


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render_report

    print(render_report())
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """Delete work files no database row refers to."""
    with db.session() as conn:
        keep = {row["path"] for row in conn.execute("SELECT path FROM assets WHERE path <> ''")}
        keep |= {row["path"] for row in conn.execute("SELECT path FROM renders")}
        keep |= {
            row["thumb_path"]
            for row in conn.execute("SELECT thumb_path FROM renders WHERE thumb_path IS NOT NULL")
        }
        keep |= {row["path"] for row in conn.execute("SELECT path FROM narrations")}
        keep |= {row["path"] for row in conn.execute("SELECT path FROM posts WHERE path <> ''")}

    removed, freed = 0, 0
    for directory in (paths.FOOTAGE_DIR, paths.STILLS_DIR, paths.DIAGRAMS_DIR,
                      paths.AUDIO_DIR, paths.RENDERS_DIR, paths.THUMBS_DIR):
        for path in directory.rglob("*"):
            if not path.is_file() or str(path) in keep:
                continue
            freed += path.stat().st_size
            if not args.dry_run:
                path.unlink()
            removed += 1
    verb = "would remove" if args.dry_run else "removed"
    print(f"gc: {verb} {removed} orphaned file(s), {freed / 1e6:.1f} MB")
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    from .youtube import authorize

    print(f"token written to {authorize(args.channel)}")
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tube-auto",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and validate config").set_defaults(func=cmd_init)
    sub.add_parser("doctor", help="diagnose setup problems before they cost anything").set_defaults(
        func=cmd_doctor
    )
    sub.add_parser("status", help="what state everything is in, and what to run next").set_defaults(
        func=cmd_status
    )
    sub.add_parser("themes", help="list themes and their arms' current share").set_defaults(
        func=cmd_themes
    )

    p = sub.add_parser("research", help="choose a topic and gather its primary sources")
    p.add_argument("--count", type=_positive, default=None)
    p.add_argument("--theme", help="force a theme instead of sampling")
    p.add_argument("--dry-run", action="store_true", help="print without saving")
    p.set_defaults(func=cmd_research)

    p = sub.add_parser("script", help="write the script, with every number cited")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_script)

    p = sub.add_parser("narrate", help="synthesise the narration")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None)
    p.add_argument("--provider", help="override the TTS provider (google, silent)")
    p.set_defaults(func=cmd_narrate)

    p = sub.add_parser("footage", help="pull NASA material for the timeline")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None)
    p.set_defaults(func=cmd_footage)

    p = sub.add_parser("diagrams", help="draw the figures NASA does not have")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None)
    p.set_defaults(func=cmd_diagrams)

    p = sub.add_parser("assemble", help="cut the video together")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None)
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser("thumbnails", help="draw the three thumbnails for A/B testing")
    p.add_argument("--limit", type=_positive, default=1)
    p.add_argument("--idea", type=_positive, default=None, help="redraw, even if they exist")
    p.set_defaults(func=cmd_thumbnails)

    p = sub.add_parser("build", help="research through assemble in one go")
    p.add_argument("--theme")
    p.add_argument("--provider")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("publish", help="upload approved videos to YouTube")
    p.add_argument("--limit", type=_positive, default=None)
    p.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("go-live", help="make uploads public (nothing is measured until this)")
    p.add_argument("--limit", type=_positive, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_go_live)

    sub.add_parser("sync-stats", help="pull view counts from YouTube").set_defaults(
        func=cmd_sync_stats
    )
    sub.add_parser("report", help="expand-or-stop decision numbers").set_defaults(func=cmd_report)

    p = sub.add_parser("gc", help="delete work files nothing refers to")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("auth", help="run the YouTube OAuth flow (needs a browser)")
    p.add_argument("--channel", required=True)
    p.set_defaults(func=cmd_auth)

    return parser


def _explain(exc: Exception) -> str | None:
    """Failures the operator can act on, mapped to a hint rather than a traceback."""
    from .budget import AlreadyRunning, BudgetExceeded
    from .db import MigrationRequired
    from .ffmpeg import FFmpegError, FFmpegMissing, FontMissing
    from .llm import NoToolCall, Truncated
    from .nasa import NasaError
    from .tts import TTSError
    from .youtube import YouTubeAuthError

    if isinstance(exc, (config.ConfigError, MigrationRequired)):
        return str(exc)
    if isinstance(exc, BudgetExceeded):
        return f"{exc}\nRun `tube-auto doctor` to see this month's spend."
    if isinstance(exc, AlreadyRunning):
        return str(exc)
    if isinstance(exc, (FFmpegMissing, FontMissing, TTSError)):
        return f"{exc}\nRun `tube-auto doctor` to check the rest of the setup."
    if isinstance(exc, FFmpegError):
        return f"video processing failed: {exc}"
    if isinstance(exc, NasaError):
        return f"NASA library unavailable: {exc}"
    if isinstance(exc, (Truncated, NoToolCall)):
        return str(exc)
    if isinstance(exc, YouTubeAuthError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return f"file not found: {exc}"
    if isinstance(exc, ValueError):
        return str(exc)
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    _load_dotenv()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - classified by _explain
        explanation = _explain(exc)
        if explanation is None:
            log.exception("unexpected failure in `%s`", args.command)
            print(
                f"error: unexpected {type(exc).__name__}: {exc}\n"
                f"A full traceback is in {paths.WORK_DIR / 'logs' / 'tube-auto.log'}",
                file=sys.stderr,
            )
            return 3
        log.debug("handled failure in `%s`", args.command, exc_info=True)
        print(f"error: {explanation}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
