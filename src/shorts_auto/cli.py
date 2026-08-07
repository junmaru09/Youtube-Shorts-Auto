"""Command line entry point.

    shorts-auto init          create the database
    shorts-auto series        list the A/B arms
    shorts-auto ideate        plan videos
    shorts-auto generate      turn plans into mp4 files
    shorts-auto postprocess   normalise audio, burn titles, cut thumbnails
    shorts-auto publish       upload approved assets
    shorts-auto sync-stats    pull view counts back in
    shorts-auto report        the numbers that decide expand-or-stop
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config, db, paths


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(paths.ROOT / ".env")


# --- commands ----------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    db.init_db()
    print(f"database ready at {paths.db_path()}")
    series = config.load_series(include_disabled=True)
    print(f"{len(series)} series found: {', '.join(s.id for s in series)}")
    return 0


def cmd_series(args: argparse.Namespace) -> int:
    from .scoring import allocation

    weights = allocation()
    for entry in config.load_series(include_disabled=True):
        state = "on " if entry.enabled else "off"
        share = weights.get(entry.id, 0.0)
        print(f"[{state}] {entry.id:<20} share={share:6.1%}  {entry.description}")
    return 0


def cmd_ideate(args: argparse.Namespace) -> int:
    from .stages import ideate

    result = ideate.run(count=args.count, dry_run=args.dry_run, series_id=args.series)
    print(
        f"ideate: {result.inserted} new, {result.duplicates} duplicates dropped, "
        f"{result.failed} failed"
    )
    for idea in result.ideas:
        print(f"  [{idea['series_id']}] {idea['hook'].get('ja') or idea['hook'].get('en')}")
    return 0 if result.inserted or args.dry_run else 1


def cmd_generate(args: argparse.Namespace) -> int:
    from .stages import generate

    if args.prompt or args.prompt_file:
        prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8").strip()
        output = Path(args.output or paths.ASSETS_DIR / "adhoc.mp4")
        generate.generate_one(prompt, output, backend_name=args.backend)
        print(f"wrote {output}")
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
    print(f"postprocess: {result.processed} assets rendered, {result.failed} failed")
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


def cmd_sync_stats(args: argparse.Namespace) -> int:
    from .stages import analytics

    result = analytics.run()
    print(f"sync-stats: {result.updated} rows updated across {result.posts} posts")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render_report

    print(render_report())
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    from .youtube import authorize

    path = authorize(args.channel)
    print(f"token written to {path}")
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shorts-auto", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and validate config").set_defaults(
        func=cmd_init
    )
    sub.add_parser("series", help="list A/B arms and their current share").set_defaults(
        func=cmd_series
    )

    p_ideate = sub.add_parser("ideate", help="plan new videos")
    p_ideate.add_argument("--count", type=int, default=None, help="how many ideas (default: settings)")
    p_ideate.add_argument("--series", help="force a single series instead of sampling")
    p_ideate.add_argument("--dry-run", action="store_true", help="print ideas without saving")
    p_ideate.set_defaults(func=cmd_ideate)

    p_gen = sub.add_parser("generate", help="render planned ideas into mp4")
    p_gen.add_argument("--limit", type=int, default=3)
    p_gen.add_argument("--backend", help="override the configured backend (e.g. fake)")
    p_gen.add_argument("--dry-run", action="store_true", help="use the fake backend, spend nothing")
    p_gen.add_argument("--prompt", help="ad-hoc: generate one video from this prompt")
    p_gen.add_argument("--prompt-file", help="ad-hoc: read the prompt from a file")
    p_gen.add_argument("--output", help="ad-hoc: output path")
    p_gen.set_defaults(func=cmd_generate)

    p_post = sub.add_parser("postprocess", help="normalise audio, burn titles, cut thumbnails")
    p_post.add_argument("--limit", type=int, default=10)
    p_post.set_defaults(func=cmd_postprocess)

    p_pub = sub.add_parser("publish", help="upload approved assets to YouTube")
    p_pub.add_argument("--limit", type=int, default=None)
    p_pub.add_argument("--privacy", choices=["private", "unlisted", "public"], default=None)
    p_pub.add_argument("--dry-run", action="store_true", help="show what would upload")
    p_pub.set_defaults(func=cmd_publish)

    sub.add_parser("sync-stats", help="pull view counts from YouTube Analytics").set_defaults(
        func=cmd_sync_stats
    )
    sub.add_parser("report", help="expand-or-stop decision numbers").set_defaults(func=cmd_report)

    p_auth = sub.add_parser("auth", help="run the YouTube OAuth flow (needs a browser)")
    p_auth.add_argument("--channel", required=True, help="channel id from config/channels.yaml")
    p_auth.set_defaults(func=cmd_auth)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    _load_dotenv()
    try:
        return args.func(args)
    except (config.ConfigError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
