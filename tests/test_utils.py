"""Tests for pure utility functions in generate.py."""
import time
from datetime import datetime, timezone, timedelta

import generate


def test_strip_html_removes_tags():
    assert generate.strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_unescapes_entities():
    assert generate.strip_html("A &amp; B &lt;3&gt;") == "A & B <3>"


def test_strip_html_normalizes_whitespace():
    assert generate.strip_html("  foo   bar  ") == "foo bar"


def test_strip_html_empty_string():
    assert generate.strip_html("") == ""


def test_strip_html_none():
    assert generate.strip_html(None) == ""


def test_strip_html_nested_tags():
    assert generate.strip_html("<div><p>Text <em>here</em></p></div>") == "Text here"


def test_date_key_format():
    dt = datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert generate.date_key(dt) == "2026-06-30"


def test_date_key_zero_padded():
    dt = datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert generate.date_key(dt) == "2026-01-05"


def test_awst_now_is_utc_plus_8():
    now_utc = datetime.now(timezone.utc)
    awst = generate.awst_now()
    diff = (awst - now_utc).total_seconds()
    assert abs(diff - 28800) < 5


def test_slugify_basic():
    assert generate.slugify("Hello World", "fallback") == "hello-world"


def test_slugify_special_chars():
    assert generate.slugify("A & B!", "fallback") == "a-b"


def test_slugify_empty_uses_fallback():
    assert generate.slugify("", "fallback") == "fallback"


def test_slugify_only_punctuation_uses_fallback():
    assert generate.slugify("!!!", "fallback") == "fallback"


def test_parse_entry_date_published():
    struct = time.strptime("2026-06-30", "%Y-%m-%d")
    dt = generate.parse_entry_date({"published_parsed": struct})
    assert dt is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 30
    assert dt.tzinfo == timezone.utc


def test_parse_entry_date_falls_back_to_updated():
    struct = time.strptime("2026-06-29", "%Y-%m-%d")
    dt = generate.parse_entry_date({"updated_parsed": struct})
    assert dt is not None
    assert dt.day == 29


def test_parse_entry_date_missing():
    assert generate.parse_entry_date({}) is None


def test_parse_entry_date_prefers_published_over_updated():
    pub = time.strptime("2026-06-30", "%Y-%m-%d")
    upd = time.strptime("2026-06-29", "%Y-%m-%d")
    dt = generate.parse_entry_date({"published_parsed": pub, "updated_parsed": upd})
    assert dt.day == 30
