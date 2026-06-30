"""Tests for RSS feed fetching logic in generate.py."""
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# feedparser may not be installed in the test environment; inject a stub so
# patch("feedparser.parse", ...) can resolve the module before calling fetch_feed.
if "feedparser" not in sys.modules:
    sys.modules["feedparser"] = MagicMock()

import generate


def _entry(title="Story Title", link="https://example.com/story",
           summary="A summary.", days_ago=0):
    pub_dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "title": title,
        "link": link,
        "summary": summary,
        "published_parsed": time.gmtime(pub_dt.timestamp()),
    }


def _parsed(entries, bozo=False):
    m = MagicMock()
    m.bozo = bozo
    m.bozo_exception = Exception("bad xml") if bozo else None
    m.entries = entries
    return m


def test_fetch_feed_success():
    failures = []
    with patch("feedparser.parse", return_value=_parsed([_entry(), _entry(link="https://example.com/2")])):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) == 2
    assert items[0]["source"] == "ABC News"
    assert items[0]["category"] == "au"
    assert failures == []


def test_fetch_feed_filters_old_news_entries():
    entries = [
        _entry(title="Recent", days_ago=1),
        _entry(title="Old", link="https://example.com/old", days_ago=10),
    ]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) == 1
    assert items[0]["title"] == "Recent"


def test_fetch_feed_longform_uses_wider_recency_window():
    entries = [_entry(title="Old Long Read", days_ago=15)]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("aeon", "Aeon", "https://example.com/feed", "longform", True, failures)
    assert len(items) == 1
    assert items[0]["title"] == "Old Long Read"


def test_fetch_feed_longform_still_filters_very_old():
    entries = [_entry(title="Ancient", days_ago=30)]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("aeon", "Aeon", "https://example.com/feed", "longform", True, failures)
    assert items == []
    assert len(failures) == 1


def test_fetch_feed_no_entries_logs_failure():
    failures = []
    with patch("feedparser.parse", return_value=_parsed([])):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert items == []
    assert len(failures) == 1
    assert "ABC News" in failures[0]


def test_fetch_feed_skips_entry_without_link():
    entries = [
        {"title": "No Link", "link": "", "summary": "x", "published_parsed": None},
        _entry(title="Has Link"),
    ]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) == 1
    assert items[0]["title"] == "Has Link"


def test_fetch_feed_skips_entry_without_title():
    entries = [
        {"title": "", "link": "https://example.com/1", "summary": "x", "published_parsed": None},
        _entry(title="Has Title"),
    ]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) == 1
    assert items[0]["title"] == "Has Title"


def test_fetch_feed_truncates_summary_to_300():
    entries = [_entry(summary="x" * 500)]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items[0]["summary"]) <= 300


def test_fetch_feed_respects_max_items_per_source():
    entries = [_entry(title=f"Story {i}", link=f"https://example.com/{i}") for i in range(20)]
    failures = []
    with patch("feedparser.parse", return_value=_parsed(entries)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) <= generate.MAX_ITEMS_PER_SOURCE


def test_fetch_feed_network_error_logs_failure():
    failures = []
    with patch("feedparser.parse", side_effect=Exception("connection timeout")):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert items == []
    assert len(failures) == 1
    assert "ABC News" in failures[0]


def test_fetch_feed_bozo_with_no_entries_logs_failure():
    failures = []
    with patch("feedparser.parse", return_value=_parsed([], bozo=True)):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert items == []
    assert len(failures) == 1


def test_fetch_feed_sets_is_longform_field():
    failures = []
    with patch("feedparser.parse", return_value=_parsed([_entry()])):
        items = generate.fetch_feed("aeon", "Aeon", "https://example.com/feed", "longform", True, failures)
    assert items[0]["is_longform"] is True


def test_fetch_feed_entry_without_publication_date_included():
    # Entries with no date should not be filtered out
    entry = {"title": "Undated Story", "link": "https://example.com/undated", "summary": "x"}
    failures = []
    with patch("feedparser.parse", return_value=_parsed([entry])):
        items = generate.fetch_feed("abc", "ABC News", "https://example.com/feed", "au", False, failures)
    assert len(items) == 1
