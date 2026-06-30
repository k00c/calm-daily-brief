"""Tests for JSON persistence and archive functions in generate.py."""
import json
import os
from datetime import datetime, timezone, timedelta

import generate

FIXED_DT = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
DATE_KEY = "2026-06-30"

SAMPLE_STORIES = [
    {
        "topic": "Employment",
        "card_type": "news",
        "source": "RNZ New Zealand",
        "link": "https://example.com/1",
        "teaser": "A calm story.",
        "full_content": "Content here.",
        "tag": "relevant",
    }
]


class TestPersistStoriesJSON:
    def test_creates_json_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json(SAMPLE_STORIES, [], FIXED_DT, DATE_KEY)
        assert (tmp_path / f"stories-{DATE_KEY}.json").exists()

    def test_creates_directory_if_missing(self, monkeypatch, tmp_path):
        new_dir = str(tmp_path / "nested" / "data")
        monkeypatch.setattr(generate, "DATA_DIR", new_dir)
        generate.persist_stories_json(SAMPLE_STORIES, [], FIXED_DT, DATE_KEY)
        assert os.path.exists(os.path.join(new_dir, f"stories-{DATE_KEY}.json"))

    def test_json_contains_date(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json(SAMPLE_STORIES, [], FIXED_DT, DATE_KEY)
        data = json.loads((tmp_path / f"stories-{DATE_KEY}.json").read_text())
        assert data["date"] == DATE_KEY

    def test_json_contains_stories(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json(SAMPLE_STORIES, [], FIXED_DT, DATE_KEY)
        data = json.loads((tmp_path / f"stories-{DATE_KEY}.json").read_text())
        assert len(data["stories"]) == 1
        assert data["stories"][0]["topic"] == "Employment"

    def test_json_contains_failures(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json(SAMPLE_STORIES, ["Feed X: timeout"], FIXED_DT, DATE_KEY)
        data = json.loads((tmp_path / f"stories-{DATE_KEY}.json").read_text())
        assert "Feed X: timeout" in data["failures"]

    def test_json_contains_generated_at(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json(SAMPLE_STORIES, [], FIXED_DT, DATE_KEY)
        data = json.loads((tmp_path / f"stories-{DATE_KEY}.json").read_text())
        assert "generated_at" in data
        assert "2026-06-30" in data["generated_at"]

    def test_json_valid_utf8(self, monkeypatch, tmp_path):
        story = dict(SAMPLE_STORIES[0], teaser="Café story with accénts.")
        monkeypatch.setattr(generate, "DATA_DIR", str(tmp_path))
        generate.persist_stories_json([story], [], FIXED_DT, DATE_KEY)
        data = json.loads((tmp_path / f"stories-{DATE_KEY}.json").read_bytes().decode("utf-8"))
        assert "Café" in data["stories"][0]["teaser"]


class TestWriteArchiveDay:
    def test_creates_archive_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(tmp_path / "archive"))
        generate.write_archive_day(SAMPLE_STORIES, FIXED_DT, DATE_KEY)
        assert (tmp_path / "archive" / DATE_KEY).is_dir()

    def test_creates_index_html(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(tmp_path / "archive"))
        generate.write_archive_day(SAMPLE_STORIES, FIXED_DT, DATE_KEY)
        assert (tmp_path / "archive" / DATE_KEY / "index.html").exists()

    def test_creates_story_pages_for_news(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(tmp_path / "archive"))
        generate.write_archive_day(SAMPLE_STORIES, FIXED_DT, DATE_KEY)
        assert (tmp_path / "archive" / DATE_KEY / "stories" / "story-1.html").exists()

    def test_no_story_page_for_longform(self, monkeypatch, tmp_path):
        longform = {
            "topic": "Science",
            "card_type": "longform",
            "headline": "Long Read",
            "summary": "A great piece.",
            "source": "Aeon",
            "link": "https://example.com/longform",
        }
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(tmp_path / "archive"))
        generate.write_archive_day([longform], FIXED_DT, DATE_KEY)
        assert not (tmp_path / "archive" / DATE_KEY / "stories" / "story-1.html").exists()

    def test_archive_index_lists_day(self, monkeypatch, tmp_path):
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(tmp_path / "archive"))
        generate.write_archive_day(SAMPLE_STORIES, FIXED_DT, DATE_KEY)
        index = (tmp_path / "archive" / "index.html").read_text()
        assert DATE_KEY in index


class TestRebuildArchiveIndex:
    def test_lists_days_in_reverse_order(self, monkeypatch, tmp_path):
        archive = tmp_path / "archive"
        for day in ["2026-06-28", "2026-06-29", "2026-06-30"]:
            (archive / day).mkdir(parents=True)
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(archive))
        generate.rebuild_archive_index()
        content = (archive / "index.html").read_text()
        idx_30 = content.index("2026-06-30")
        idx_28 = content.index("2026-06-28")
        assert idx_30 < idx_28  # most recent first

    def test_ignores_non_date_subdirs(self, monkeypatch, tmp_path):
        archive = tmp_path / "archive"
        (archive / "2026-06-30").mkdir(parents=True)
        (archive / "misc-dir").mkdir()
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(archive))
        generate.rebuild_archive_index()
        content = (archive / "index.html").read_text()
        assert "misc-dir" not in content

    def test_each_day_links_to_index(self, monkeypatch, tmp_path):
        archive = tmp_path / "archive"
        (archive / "2026-06-30").mkdir(parents=True)
        monkeypatch.setattr(generate, "ARCHIVE_DIR", str(archive))
        generate.rebuild_archive_index()
        content = (archive / "index.html").read_text()
        assert "2026-06-30/index.html" in content
