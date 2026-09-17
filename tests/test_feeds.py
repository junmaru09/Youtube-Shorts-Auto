"""Feed gathering: keyword matching and retry behaviour, no network."""

import urllib.error
from io import BytesIO

import pytest

from tube_auto import feeds


def test_phrase_queries_match_on_their_subject_words():
    match = feeds.keyword_matcher(["hurricane from space", "ice sheet Greenland", "aurora from ISS"])
    assert match("NASA satellites track Hurricane Erin across the Atlantic")
    assert match("Greenland's ice loss accelerates, new data show")
    assert not match("Artemis crew module arrives at the Cape")
    # "space" alone must not match, or every NASA item would
    assert not match("A new space telescope")


def test_short_and_stop_words_do_not_match():
    match = feeds.keyword_matcher(["ocean from space"])
    assert not match("A view from space")          # "from"/"space" are stopwords
    assert match("Ocean currents seen by satellite")
    assert feeds.keyword_matcher([])("anything")   # no queries: everything passes


ATOM_SAMPLE = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <entry>
    <id>oai:arXiv.org:2609.17675v1</id>
    <title>Evidence for an Extended Hydrogen Outflow on WASP-12 b</title>
    <link href="https://arxiv.org/abs/2609.17675" rel="alternate" type="text/html"/>
    <summary>arXiv:2609.17675v1 Announce Type: new  Abstract: Recent observations of atmospheric escape.</summary>
    <published>2026-09-17T00:00:00-04:00</published>
    <announce_type>new</announce_type>
    <dc:creator>Morgan Saidel, Shreyas Vissapragada</dc:creator>
  </entry>
  <entry>
    <id>oai:arXiv.org:2501.00001v3</id>
    <title>An old paper, revised</title>
    <link href="https://arxiv.org/abs/2501.00001" rel="alternate" type="text/html"/>
    <summary>arXiv:2501.00001v3 Announce Type: replace  Abstract: Nothing new.</summary>
    <published>2026-09-17T00:00:00-04:00</published>
    <announce_type>replace</announce_type>
  </entry>
</feed>"""


def test_arxiv_feed_fallback_parses_and_skips_replacements(monkeypatch):
    monkeypatch.setattr(feeds, "_fetch", lambda url, attempts=3: ATOM_SAMPLE)
    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    items = feeds.fetch_arxiv_feeds(["astro-ph.EP"], max_age_days=3650)
    assert [i.url for i in items] == ["https://arxiv.org/abs/2609.17675"]
    assert items[0].summary.startswith("Recent observations")
    assert items[0].authors.startswith("Morgan Saidel")
    assert items[0].kind == "arxiv"


def test_api_failure_falls_back_to_feeds(monkeypatch):
    import urllib.error

    def fetch(url, attempts=3):
        if "export.arxiv.org" in url:
            raise urllib.error.HTTPError(url, 406, "Not Acceptable", {}, None)
        return ATOM_SAMPLE

    monkeypatch.setattr(feeds, "_fetch", fetch)
    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    items = feeds.fetch_arxiv(["astro-ph.EP"], max_age_days=3650)
    assert len(items) == 1


def test_fetch_retries_on_406_then_succeeds(monkeypatch):
    calls = []

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 406, "Not Acceptable", {}, None)
        return Response(b"<ok/>")

    monkeypatch.setattr(feeds.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    assert feeds._fetch("http://example.test/feed") == b"<ok/>"
    assert len(calls) == 3


def test_fetch_gives_up_on_a_real_error(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(feeds.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        feeds._fetch("http://example.test/feed")
