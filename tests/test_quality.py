"""The quality gate: scored from the timeline, no ffmpeg."""

from tube_auto import quality


def _entry(start, end, display="あ" * 20, ops=None):
    return {"start_s": start, "end_s": end, "display": display, "ops": ops if ops is not None else [{"op": "hold"}]}


def _lively(minutes=10, every=6.0):
    n = int(minutes * 60 / every)
    return [_entry(i * every, (i + 1) * every, ops=[{"op": "label", "text": "x"}]) for i in range(n)]


def test_a_lively_video_passes():
    report = quality.score(_lively())
    assert report.ok, report.problems
    assert report.changes_per_minute >= 9


def test_the_first_render_would_fail():
    static = [_entry(i * 6, (i + 1) * 6) for i in range(200)]     # 20 minutes, nothing moves
    report = quality.score(static)
    assert not report.ok
    assert any("動かなすぎる" in p for p in report.problems)
    assert any("静止" in p for p in report.problems)
    assert report.max_static_seconds == 1200


def test_the_longest_hold_is_located():
    timeline = _lively(4)
    timeline += [_entry(240 + i * 6, 246 + i * 6) for i in range(10)]   # a minute of nothing at 4:00
    timeline += _lively(2)
    for e in timeline[-20:]:
        e["start_s"] += 300; e["end_s"] += 300
    report = quality.score(timeline)
    assert report.max_static_seconds >= 60 and abs(report.static_at - 234) < 7
    assert any("4.0 分あたり" in p or "3.9 分あたり" in p for p in report.problems)


def test_subtitles_and_dropped_visuals_count():
    timeline = _lively()
    for e in timeline[:10]:
        e["display"] = "あ" * 70                                    # three rows
    report = quality.score(timeline, dropped_visuals=9)
    assert any("3行" in p for p in report.problems)
    assert any("描けなかった" in p for p in report.problems)


def test_thresholds_come_from_settings():
    report = quality.score(_lively(), thresholds={"min_changes_per_minute": 50})
    assert not report.ok


def test_describe_reads_as_a_report():
    text = quality.score(_lively()).describe()
    assert "回/分" in text and "基準を満たしている" in text
