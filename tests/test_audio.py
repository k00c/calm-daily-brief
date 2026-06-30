"""Tests for audio pipeline functions in generate.py."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import generate

FIXED_DT = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))

NEWS_STORY = {
    "topic": "Employment",
    "card_type": "news",
    "source": "RNZ New Zealand",
    "link": "https://example.com/story1",
    "teaser": "A calm sentence about jobs.",
    "full_content": "First paragraph. Second paragraph. Third paragraph.",
    "tag": "relevant",
}

LONGFORM_STORY = {
    "card_type": "longform",
    "headline": "The Deep Ocean Revealed",
    "summary": "Scientists have found new species. More details follow.",
    "source": "Hakai Magazine",
    "link": "https://example.com/longform",
    "tag": None,
}


class TestBuildSpokenSegments:
    def test_intro_contains_date(self):
        segments = generate.build_spoken_segments([NEWS_STORY], FIXED_DT)
        assert "30 June 2026" in segments[0]

    def test_outro_is_last_segment(self):
        segments = generate.build_spoken_segments([NEWS_STORY], FIXED_DT)
        assert "Thanks for listening" in segments[-1]

    def test_segment_count_with_two_stories(self):
        segments = generate.build_spoken_segments([NEWS_STORY, LONGFORM_STORY], FIXED_DT)
        assert len(segments) == 4  # intro + 2 stories + outro

    def test_segment_count_with_no_stories(self):
        segments = generate.build_spoken_segments([], FIXED_DT)
        assert len(segments) == 2  # intro + outro

    def test_news_story_segment_starts_with_topic(self):
        segments = generate.build_spoken_segments([NEWS_STORY], FIXED_DT)
        assert segments[1].startswith("Employment.")

    def test_news_story_segment_includes_full_content(self):
        segments = generate.build_spoken_segments([NEWS_STORY], FIXED_DT)
        assert "First paragraph." in segments[1]

    def test_longform_segment_uses_headline(self):
        segments = generate.build_spoken_segments([LONGFORM_STORY], FIXED_DT)
        assert "The Deep Ocean Revealed" in segments[1]

    def test_longform_segment_includes_first_sentence_of_summary(self):
        segments = generate.build_spoken_segments([LONGFORM_STORY], FIXED_DT)
        assert "Scientists have found new species." in segments[1]

    def test_longform_segment_includes_read_on_site(self):
        segments = generate.build_spoken_segments([LONGFORM_STORY], FIXED_DT)
        assert "available to read on the site" in segments[1]

    def test_single_story_rundown_format(self):
        segments = generate.build_spoken_segments([NEWS_STORY], FIXED_DT)
        assert "Today: Employment." in segments[0]

    def test_multi_story_rundown_uses_and(self):
        segments = generate.build_spoken_segments([NEWS_STORY, LONGFORM_STORY], FIXED_DT)
        assert "and" in segments[0]

    def test_no_stories_no_rundown(self):
        segments = generate.build_spoken_segments([], FIXED_DT)
        assert "Today:" not in segments[0]

    def test_skips_non_dict_stories(self):
        segments = generate.build_spoken_segments([NEWS_STORY, "not a dict", None], FIXED_DT)
        # Only the valid news story contributes a segment
        assert len(segments) == 3  # intro + 1 story + outro

    def test_longform_without_headline_uses_fallback(self):
        story = dict(LONGFORM_STORY, headline="")
        segments = generate.build_spoken_segments([story], FIXED_DT)
        assert "Long read" in segments[1]


class TestPruneOldAudio:
    def test_removes_files_beyond_retention_window(self, monkeypatch, tmp_path):
        old_date = (FIXED_DT - timedelta(days=65)).strftime("%Y-%m-%d")
        (tmp_path / f"{old_date}.mp3").write_bytes(b"old")
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "awst_now", lambda: FIXED_DT)

        kept = generate.prune_old_audio([])
        assert not (tmp_path / f"{old_date}.mp3").exists()
        assert old_date not in kept

    def test_keeps_files_within_retention_window(self, monkeypatch, tmp_path):
        recent_date = (FIXED_DT - timedelta(days=30)).strftime("%Y-%m-%d")
        (tmp_path / f"{recent_date}.mp3").write_bytes(b"recent")
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "awst_now", lambda: FIXED_DT)

        kept = generate.prune_old_audio([])
        assert (tmp_path / f"{recent_date}.mp3").exists()
        assert recent_date in kept

    def test_ignores_non_mp3_files(self, monkeypatch, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "awst_now", lambda: FIXED_DT)

        kept = generate.prune_old_audio([])
        assert len(kept) == 0
        assert (tmp_path / "notes.txt").exists()

    def test_file_one_day_inside_window_kept(self, monkeypatch, tmp_path):
        # A file 1 day inside the retention window is always kept
        safe_date = (FIXED_DT - timedelta(days=generate.AUDIO_RETENTION_DAYS - 1)).strftime("%Y-%m-%d")
        (tmp_path / f"{safe_date}.mp3").write_bytes(b"safe")
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "awst_now", lambda: FIXED_DT)

        kept = generate.prune_old_audio([])
        assert safe_date in kept

    def test_returns_set_of_kept_date_keys(self, monkeypatch, tmp_path):
        for d in ["2026-06-28", "2026-06-29", "2026-06-30"]:
            (tmp_path / f"{d}.mp3").write_bytes(b"data")
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "awst_now", lambda: FIXED_DT)

        kept = generate.prune_old_audio([])
        assert kept == {"2026-06-28", "2026-06-29", "2026-06-30"}


class TestBuildFeedXML:
    def test_produces_valid_rss(self, monkeypatch, tmp_path):
        dates = {"2026-06-28", "2026-06-29", "2026-06-30"}
        for d in dates:
            (tmp_path / f"{d}.mp3").write_bytes(b"\xff\xfb" * 100)
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        generate.build_feed_xml(dates)

        root = ET.fromstring(feed_path.read_text())
        assert root.tag == "rss"
        assert root.find("channel") is not None

    def test_one_item_per_date(self, monkeypatch, tmp_path):
        dates = {"2026-06-28", "2026-06-29", "2026-06-30"}
        for d in dates:
            (tmp_path / f"{d}.mp3").write_bytes(b"data")
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        generate.build_feed_xml(dates)

        root = ET.fromstring(feed_path.read_text())
        items = root.find("channel").findall("item")
        assert len(items) == 3

    def test_items_in_reverse_chronological_order(self, monkeypatch, tmp_path):
        dates = {"2026-06-28", "2026-06-29", "2026-06-30"}
        for d in dates:
            (tmp_path / f"{d}.mp3").write_bytes(b"data")
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        generate.build_feed_xml(dates)

        root = ET.fromstring(feed_path.read_text())
        items = root.find("channel").findall("item")
        titles = [item.find("title").text for item in items]
        assert "2026-06-30" in titles[0]
        assert "2026-06-28" in titles[-1]

    def test_empty_dates_produces_empty_feed(self, monkeypatch, tmp_path):
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        generate.build_feed_xml(set())

        root = ET.fromstring(feed_path.read_text())
        items = root.find("channel").findall("item")
        assert len(items) == 0

    def test_item_enclosure_has_correct_url(self, monkeypatch, tmp_path):
        (tmp_path / "2026-06-30.mp3").write_bytes(b"data")
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        generate.build_feed_xml({"2026-06-30"})

        content = feed_path.read_text()
        assert "audio/2026-06-30.mp3" in content

    def test_skips_date_without_mp3_file(self, monkeypatch, tmp_path):
        feed_path = tmp_path / "feed.xml"
        monkeypatch.setattr(generate, "AUDIO_DIR", str(tmp_path))
        monkeypatch.setattr(generate, "FEED_PATH", str(feed_path))

        # Pass a date but don't create the file
        generate.build_feed_xml({"2026-06-30"})

        root = ET.fromstring(feed_path.read_text())
        items = root.find("channel").findall("item")
        assert len(items) == 0
