"""Command line entry point.

Daily loop:

    shorts-auto doctor        is anything misconfigured?
    shorts-auto status        what state is everything in, and what is next?
    shorts-auto ideate        plan videos
    shorts-auto generate      turn plans into mp4 files (this costs money)
    shorts-auto postprocess   normalise, burn the title, cut a thumbnail
    streamlit run review_app.py   approve or reject
    shorts-auto publish       upload as private
    shorts-auto go-live       make them visible (nothing is measured until this)
    shorts-auto sync-stats    pull view counts back in
    shorts-auto report        the numbers that decide expand-or-stop
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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
        file_handler = logging.FileHandler(log_dir / "shorts-auto.log", encoding="utf-8")
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
    print(f"{len(config.load_series(include_disabled=True))} series, {len(arms)} A/B arms:")
    for arm in arms:
        print(f"  {arm.key}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import render, run_checks

    checks = run_checks()
    print(render(checks))
    return 1 if any(not c.ok and c.blocking for c in checks) else 0


def cmd_status(args: argparse.Namespace) -> int:
    from .scoring import allocation

    with db.session() as conn:
        counts = db.status_counts(conn)
        private = len(db.private_posts(conn))
        live = len(db.measurable_posts(conn))
        pending_review = len(db.pending_reviews(conn))

    print("\nパイプラインの状態")
    print("=" * 60)
    order = ["ideated", "generated", "post_processed", "approved", "rejected",
             "published", "live", "failed"]
    for status in order:
        if counts.get(status):
            print(f"  {status:<16} {counts[status]:>4}")
    print(f"  {'レビュー待ち':<16} {pending_review:>4}")
    print(f"  {'private のまま':<16} {private:>4}")
    print(f"  {'公開済み':<16} {live:>4}")

    print("\n次にやること")
    print("=" * 60)
    if counts.get("ideated"):
        print(f"  shorts-auto generate       ({counts['ideated']} 件の企画が生成待ち)")
    if counts.get("generated"):
        print(f"  shorts-auto postprocess    ({counts['generated']} 件が後処理待ち)")
    if pending_review:
        print(f"  streamlit run review_app.py ({pending_review} 件がレビュー待ち)")
    if counts.get("approved"):
        print(f"  shorts-auto publish        ({counts['approved']} 件が投稿待ち)")
    if private:
        print(f"  shorts-auto go-live        ({private} 件が private のまま = 再生されない)")
    if live:
        print("  shorts-auto sync-stats     (再生数を取り込む。毎日実行)")
    if not any([counts.get("ideated"), counts.get("generated"), pending_review,
                counts.get("approved"), private]):
        print("  shorts-auto ideate         (キューが空です)")

    print("\n現在の配分")
    print("=" * 60)
    for key, share in sorted(allocation().items(), key=lambda kv: -kv[1]):
        print(f"  {key:<28} {share:>6.1%}")
    print()
    return 0


def cmd_series(args: argparse.Namespace) -> int:
    from .scoring import allocation

    shares = allocation()
    for entry in config.load_series(include_disabled=True):
        state = "on " if entry.enabled else "off"
        print(f"[{state}] {entry.id:<20} {entry.description}")
        for lang in entry.languages:
            key = f"{entry.id}:{lang}"
            print(f"        {key:<26} share={shares.get(key, 0.0):>6.1%}")
    return 0


def cmd_ideate(args: argparse.Namespace) -> int:
    from .stages import ideate

    result = ideate.run(
        count=args.count, dry_run=args.dry_run, series_id=args.series, lang=args.lang
    )
    print(
        f"ideate: {result.inserted} new, {result.duplicates} duplicates dropped, "
        f"{result.failed} failed (LLM cost ${result.llm_cost_usd:.4f})"
    )
    for idea in result.ideas:
        print(f"  [{idea['series_id']}:{idea['lang']}] {idea['hook']}")
    for error in result.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if result.inserted else 1


def cmd_generate(args: argparse.Namespace) -> int:
    from .stages import generate

    if args.prompt or args.prompt_file:
        prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8").strip()
        output = Path(args.output or paths.ASSETS_DIR / "adhoc.mp4")
        cost = generate.generate_one(
            prompt, output, backend_name=args.backend, dry_run=args.dry_run
        )
        print(f"wrote {output} (${cost:.2f})")
        return 0

    result = generate.run(limit=args.limit, backend_name=args.backend, dry_run=args.dry_run)
    print(
        f"generate: {result.generated} ok, {result.failed} failed, "
        f"{result.skipped_budget} skipped for budget, ${result.spent_usd:.2f} spent"
    )
    for error in result.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


def cmd_postprocess(args: argparse.Namespace) -> int:
    from .stages import postprocess

    result = postprocess.run(limit=args.limit)
    print(f"postprocess: {result.processed} rendered, {result.failed} failed")
    for error in result.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if result.failed == 0 else 1


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
    for error in result.errors:
        print(f"  ! {error}", file=sys.stderr)
    return 0 if not result.errors else 1


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render_report

    print(render_report())
    return 0


def cmd_gc(args: argparse.Namespace) -> int:
    """Delete work files no database row refers to."""
    with db.session() as conn:
        keep = {row["path"] for row in conn.execute("SELECT path FROM generations WHERE path IS NOT NULL")}
        keep |= {row["path"] for row in conn.execute("SELECT path FROM renders")}
        keep |= {
            row["thumb_path"]
            for row in conn.execute("SELECT thumb_path FROM renders WHERE thumb_path IS NOT NULL")
        }
        keep |= {row["path"] for row in conn.execute("SELECT path FROM posts WHERE path <> ''")}

    removed, freed = 0, 0
    for directory in (paths.ASSETS_DIR, paths.RENDERS_DIR, paths.THUMBS_DIR):
        for path in directory.glob("*"):
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
        prog="shorts-auto",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and validate config").set_defaults(
        func=cmd_init
    )
    sub.add_parser("doctor", help="diagnose setup problems before they cost anything").set_defaults(
        func=cmd_doctor
    )
    sub.add_parser("status", help="what state everything is in, and what to run next").set_defaults(
        func=cmd_status
    )
    sub.add_parser("series", help="list series and their arms' current share").set_defaults(
        func=cmd_series
    )

    p_ideate = sub.add_parser("ideate", help="plan new videos")
    p_ideate.add_argument("--count", type=_positive, default=None, help="how many (default: settings)")
    p_ideate.add_argument("--series", help="force a single series instead of sampling")
    p_ideate.add_argument("--lang", help="with --series, which channel to target")
    p_ideate.add_argument("--dry-run", action="store_true", help="print ideas without saving")
    p_ideate.set_defaults(func=cmd_ideate)

    p_gen = sub.add_parser("generate", help="render planned ideas into mp4 (costs money)")
    p_gen.add_argument("--limit", type=_positive, default=3)
    p_gen.add_argument("--backend", help="override the configured backend (e.g. fake)")
    p_gen.add_argument(
        "--dry-run", action="store_true", help="use the fake backend, spend nothing (wins over --backend)"
    )
    p_gen.add_argument("--prompt", help="ad-hoc: generate one video from this prompt")
    p_gen.add_argument("--prompt-file", help="ad-hoc: read the prompt from a file")
    p_gen.add_argument("--output", help="ad-hoc: output path")
    p_gen.set_defaults(func=cmd_generate)

    p_post = sub.add_parser("postprocess", help="normalise, burn the title, cut a thumbnail")
    p_post.add_argument("--limit", type=_positive, default=10)
    p_post.set_defaults(func=cmd_postprocess)

    p_pub = sub.add_parser("publish", help="upload approved renders to YouTube")
    p_pub.add_argument("--limit", type=_positive, default=None)
    p_pub.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    p_pub.add_argument("--dry-run", action="store_true", help="show what would upload")
    p_pub.set_defaults(func=cmd_publish)

    p_live = sub.add_parser(
        "go-live", help="make uploaded videos public (nothing is measured until this)"
    )
    p_live.add_argument("--limit", type=_positive, default=None)
    p_live.add_argument("--dry-run", action="store_true")
    p_live.set_defaults(func=cmd_go_live)

    sub.add_parser("sync-stats", help="pull view counts from YouTube").set_defaults(
        func=cmd_sync_stats
    )
    sub.add_parser("report", help="expand-or-stop decision numbers").set_defaults(func=cmd_report)

    p_gc = sub.add_parser("gc", help="delete work files nothing refers to")
    p_gc.add_argument("--dry-run", action="store_true")
    p_gc.set_defaults(func=cmd_gc)

    p_auth = sub.add_parser("auth", help="run the YouTube OAuth flow (needs a browser)")
    p_auth.add_argument("--channel", required=True, help="channel id from config/channels.yaml")
    p_auth.set_defaults(func=cmd_auth)

    return parser


# Failures the operator can act on, mapped to a hint rather than a traceback.
def _explain(exc: Exception) -> str | None:
    from .budget import AlreadyRunning, BudgetExceeded
    from .db import MigrationRequired
    from .ffmpeg import FFmpegError, FFmpegMissing, FontMissing
    from .pricing import InvalidVideoRequest, UnknownPriceError
    from .youtube import YouTubeAuthError

    if isinstance(exc, (config.ConfigError, MigrationRequired)):
        return str(exc)
    if isinstance(exc, BudgetExceeded):
        return f"{exc}\nRun `shorts-auto doctor` to see this month's spend."
    if isinstance(exc, AlreadyRunning):
        return str(exc)
    if isinstance(exc, (FFmpegMissing, FontMissing)):
        return f"{exc}\nRun `shorts-auto doctor` to check the rest of the setup."
    if isinstance(exc, FFmpegError):
        return f"video processing failed: {exc}"
    if isinstance(exc, YouTubeAuthError):
        return str(exc)
    if isinstance(exc, (InvalidVideoRequest, UnknownPriceError)):
        return f"{exc}\nCheck the video block in config/settings.yaml."
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
                f"A full traceback is in {paths.WORK_DIR / 'logs' / 'shorts-auto.log'}",
                file=sys.stderr,
            )
            return 3
        log.debug("handled failure in `%s`", args.command, exc_info=True)
        print(f"error: {explanation}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
