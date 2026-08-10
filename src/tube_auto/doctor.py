"""Pre-flight diagnosis.

Every check here corresponds to a way the pipeline failed confusingly at least
once: a missing CJK font that silently burned empty boxes, an API key absent
until the moment money was about to be spent, `sync-stats` left unrun long enough
that its measurement windows expired.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import config, db, ffmpeg, paths
from .budget import month_start
from .pricing import PRICES_VERIFIED_ON, PRICING_DOC_URL

PRICE_STALE_AFTER_DAYS = 120


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    blocking: bool = True

    @property
    def mark(self) -> str:
        if self.ok:
            return "OK  "
        return "FAIL" if self.blocking else "WARN"


def _check_tools() -> list[Check]:
    checks = []
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        checks.append(
            Check(
                name=tool,
                ok=found is not None,
                detail=found or "not found",
                fix="sudo apt-get install -y ffmpeg",
            )
        )
    try:
        font = ffmpeg.find_font(config.load_settings().get("postprocess", {}).get("font_path"))
        checks.append(Check(name="CJK font", ok=True, detail=font))
    except (ffmpeg.FontMissing, config.ConfigError) as exc:
        checks.append(
            Check(
                name="CJK font",
                ok=False,
                detail=str(exc),
                fix="sudo apt-get install -y fonts-noto-cjk",
            )
        )
    return checks


def _check_keys() -> list[Check]:
    checks = []
    value = os.environ.get("ANTHROPIC_API_KEY")
    checks.append(
        Check(
            name="ANTHROPIC_API_KEY",
            ok=bool(value),
            detail="set (research and script)" if value else "missing (research and script)",
            fix="cp .env.example .env and fill it in",
        )
    )

    # Narration authenticates through Application Default Credentials rather
    # than a key in .env, so its absence looks different from a missing key.
    from .tts import TTSError

    try:
        from google.cloud import texttospeech  # noqa: F401

        installed = True
    except ImportError:
        installed = False

    if not installed:
        checks.append(
            Check(
                name="TTS",
                ok=False,
                detail="google-cloud-texttospeech is not installed",
                fix='pip install -e "."',
            )
        )
    else:
        creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        adc = Path.home() / ".config/gcloud/application_default_credentials.json"
        have = bool(creds) or adc.exists()
        checks.append(
            Check(
                name="TTS auth",
                ok=have,
                detail=(
                    f"service account: {creds}" if creds
                    else "application default credentials" if have
                    else "no Google Cloud credentials"
                ),
                fix="gcloud auth application-default login, or set "
                    "GOOGLE_APPLICATION_CREDENTIALS",
            )
        )
        assert TTSError  # imported for the error type the stage raises
    return checks


def _check_config() -> list[Check]:
    problems = config.validate_all()
    if problems:
        return [
            Check(name="config", ok=False, detail=problem, fix="edit the files under config/")
            for problem in problems
        ]
    themes = config.load_themes()
    arms = config.arms()
    with_footage = sum(1 for t in themes if t.allow_footage)
    return [
        Check(
            name="config",
            ok=True,
            detail=f"{len(themes)} themes, {len(arms)} A/B arms, "
                   f"{with_footage} allowed to use NASA video",
        )
    ]


def _check_tokens() -> list[Check]:
    checks: list[Check] = []
    try:
        channels = config.load_channels()["channels"]
    except config.ConfigError as exc:
        return [Check(name="channels.yaml", ok=False, detail=str(exc))]

    for channel in channels:
        path = Path(channel["token_path"])
        resolved = path if path.is_absolute() else paths.ROOT / path
        if not resolved.exists():
            checks.append(
                Check(
                    name=f"OAuth [{channel['id']}]",
                    ok=False,
                    detail=f"no token at {resolved}",
                    fix=f"run `tube-auto auth --channel {channel['id']}` on a machine "
                    "with a browser, then copy the JSON here",
                )
            )
            continue
        try:
            from google.oauth2.credentials import Credentials

            from .youtube import SCOPES

            creds = Credentials.from_authorized_user_file(str(resolved), SCOPES)
            if creds.valid:
                state = "valid"
            elif creds.refresh_token:
                state = "expired but refreshable"
            else:
                state = "expired with no refresh token"
            missing_scopes = sorted(set(SCOPES) - set(creds.scopes or []))
            ok = creds.valid or bool(creds.refresh_token)
            detail = state + (f"; missing scopes {missing_scopes}" if missing_scopes else "")
            checks.append(
                Check(
                    name=f"OAuth [{channel['id']}]",
                    ok=ok and not missing_scopes,
                    detail=detail,
                    fix=f"re-run `tube-auto auth --channel {channel['id']}`",
                )
            )
        except Exception as exc:  # noqa: BLE001 - any parse failure means unusable
            checks.append(
                Check(name=f"OAuth [{channel['id']}]", ok=False, detail=f"unreadable: {exc}")
            )
    return checks


def _check_budget() -> list[Check]:
    settings = config.load_settings().get("budget", {})
    limit = float(settings.get("monthly_usd", 0.0))
    with db.session() as conn:
        spend = db.spend_breakdown(conn, month_start())
    remaining = limit - spend["total"]
    return [
        Check(
            name="budget",
            ok=remaining > 0,
            detail=f"${spend['total']:.2f} of ${limit:.2f} used this month "
            f"(video ${spend['video']:.2f}, llm ${spend['llm']:.2f}); ${remaining:.2f} left",
            fix="raise budget.monthly_usd in config/settings.yaml",
            blocking=False,
        )
    ]


def _check_prices() -> list[Check]:
    verified = datetime.fromisoformat(PRICES_VERIFIED_ON).replace(tzinfo=UTC)
    age = (datetime.now(UTC) - verified).days
    stale = age > PRICE_STALE_AFTER_DAYS
    return [
        Check(
            name="price table",
            ok=not stale,
            detail=f"last verified {PRICES_VERIFIED_ON} ({age} days ago)",
            fix=f"re-check {PRICING_DOC_URL} and update src/tube_auto/pricing.py; "
            "a wrong price scales the real spending cap by the same factor",
            blocking=False,
        )
    ]


def _check_tts_budget() -> list[Check]:
    """How much of the free character allowance this month has used.

    Going over does not fail, it starts charging. A silent switch from free to
    paid is exactly the drift the budget guard exists to make visible.
    """
    from .budget import month_start
    from .tts import free_tier_state

    cfg = config.load_settings().get("tts", {})
    allowance = int(cfg.get("free_tier_chars_per_month", 1_000_000))
    warn_at = float(cfg.get("free_tier_warn_at", 0.8))

    with db.session() as conn:
        used = db.tts_chars_since(conn, month_start())

    ok, message = free_tier_state(used, allowance, warn_at)
    return [
        Check(
            name="TTS free tier",
            ok=ok,
            detail=message,
            fix="narration beyond the allowance is billed at $16-30 per million "
                "characters; lower pipeline.ideas_per_day or shorten the videos",
            blocking=False,
        )
    ]


def _check_nasa() -> list[Check]:
    """The material library needs no key, so this is purely a reachability check."""
    from .nasa import NasaError, search

    try:
        results = search("nebula", "image", page_size=10)
    except NasaError as exc:
        return [
            Check(
                name="NASA library",
                ok=False,
                detail=str(exc)[:120],
                fix="check network access to images-api.nasa.gov",
                blocking=False,
            )
        ]
    return [
        Check(
            name="NASA library",
            ok=bool(results),
            detail=f"reachable, {len(results)} cleared item(s) for a sample query",
            fix="the rights filter may be rejecting everything; run with -v to see why",
            blocking=False,
        )
    ]


def _check_pipeline_state() -> list[Check]:
    with db.session() as conn:
        counts = db.status_counts(conn)
        private = len(db.private_posts(conn))
        last_sync = db.last_run(conn, "sync-stats")

    checks = [
        Check(
            name="pipeline",
            ok=True,
            detail=", ".join(f"{status}={n}" for status, n in sorted(counts.items())) or "empty",
            blocking=False,
        )
    ]

    if private:
        checks.append(
            Check(
                name="visibility",
                ok=False,
                detail=f"{private} post(s) still private, earning no views",
                fix="tube-auto go-live",
                blocking=False,
            )
        )

    if counts.get("published") or counts.get("live"):
        if last_sync is None:
            checks.append(
                Check(
                    name="sync-stats",
                    ok=False,
                    detail="never run, so no measurements exist",
                    fix="tube-auto sync-stats",
                    blocking=False,
                )
            )
        else:
            age = datetime.now(UTC) - datetime.fromisoformat(last_sync["started_at"])
            stale = age > timedelta(days=2)
            checks.append(
                Check(
                    name="sync-stats",
                    ok=not stale,
                    detail=f"last run {age.days}d {age.seconds // 3600}h ago",
                    fix="run it daily — the 24h/72h measurement windows expire, and a "
                    "missed window cannot be reconstructed later",
                    blocking=False,
                )
            )
    return checks


def run_checks() -> list[Check]:
    checks: list[Check] = []
    for group in (
        _check_tools,
        _check_keys,
        _check_config,
        _check_tokens,
        _check_budget,
        _check_tts_budget,
        _check_prices,
        _check_nasa,
        _check_pipeline_state,
    ):
        try:
            checks.extend(group())
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the others
            checks.append(Check(name=group.__name__, ok=False, detail=f"check failed: {exc}"))
    return checks


def render(checks: list[Check]) -> str:
    width = max((len(c.name) for c in checks), default=10)
    lines = ["", "tube-auto doctor", "=" * 72]
    for check in checks:
        lines.append(f"[{check.mark}] {check.name:<{width}}  {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"{'':>{width + 9}}→ {check.fix}")
    blocking = [c for c in checks if not c.ok and c.blocking]
    warnings = [c for c in checks if not c.ok and not c.blocking]
    lines.append("=" * 72)
    if blocking:
        lines.append(f"{len(blocking)} blocking problem(s). Fix these before running the pipeline.")
    elif warnings:
        lines.append(f"No blocking problems. {len(warnings)} warning(s) above.")
    else:
        lines.append("All checks passed.")
    lines.append("")
    return "\n".join(lines)
