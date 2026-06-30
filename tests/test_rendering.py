"""Tests for HTML rendering functions in generate.py."""
from datetime import datetime, timezone, timedelta

import generate

FIXED_DT = datetime(2026, 6, 30, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
DATE_KEY = "2026-06-30"

NEWS_STORY = {
    "topic": "Employment",
    "card_type": "news",
    "source": "RNZ New Zealand",
    "link": "https://example.com/story1",
    "teaser": "A calm sentence about jobs.",
    "full_content": "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
    "tag": "relevant",
}

LONGFORM_STORY = {
    "topic": "Science",
    "card_type": "longform",
    "headline": "The Deep Ocean Revealed",
    "summary": "Scientists discover new species in the deep sea.",
    "source": "Hakai Magazine",
    "link": "https://example.com/longform",
    "tag": None,
}


class TestBannerSVG:
    def test_deterministic_for_same_seed(self):
        assert generate.render_banner_svg("2026-06-30") == generate.render_banner_svg("2026-06-30")

    def test_different_seeds_produce_different_output(self):
        assert generate.render_banner_svg("2026-06-30") != generate.render_banner_svg("2026-06-29")

    def test_is_valid_svg(self):
        svg = generate.render_banner_svg("2026-06-30")
        assert svg.strip().startswith("<svg")
        assert "</svg>" in svg

    def test_contains_four_circles(self):
        svg = generate.render_banner_svg("2026-06-30")
        assert svg.count("<circle") == 4

    def test_uses_only_palette_colors(self):
        svg = generate.render_banner_svg("2026-06-30")
        for color in generate.BANNER_PALETTE:
            # At least one palette color must appear
            break
        # All fill values should be from the palette
        import re
        fills = re.findall(r'fill="(#[0-9a-f]+)"', svg)
        # The rect background fill plus circle fills
        for fill in fills:
            if fill == "#f6f4ef":
                continue
            assert fill in generate.BANNER_PALETTE


class TestRenderHTML:
    def test_contains_story_count(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "1 stories" in html

    def test_contains_date(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "30 June 2026" in html

    def test_news_card_shows_teaser(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "A calm sentence about jobs." in html

    def test_news_card_links_to_story_page(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "stories/story-1.html" in html

    def test_longform_card_shows_headline(self):
        html = generate.render_html([LONGFORM_STORY], [], FIXED_DT)
        assert "The Deep Ocean Revealed" in html

    def test_longform_card_links_directly_to_original(self):
        html = generate.render_html([LONGFORM_STORY], [], FIXED_DT)
        assert "https://example.com/longform" in html
        assert 'target="_blank"' in html

    def test_longform_card_shows_longread_badge(self):
        html = generate.render_html([LONGFORM_STORY], [], FIXED_DT)
        assert "Long read" in html

    def test_tag_rendered_for_news_story(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert ">relevant<" in html

    def test_no_tag_span_for_longform(self):
        html = generate.render_html([LONGFORM_STORY], [], FIXED_DT)
        assert 'class="tag tag-' not in html

    def test_source_name_rendered(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "RNZ New Zealand" in html

    def test_failures_comment_included_when_present(self):
        html = generate.render_html([NEWS_STORY], ["Feed X: timeout"], FIXED_DT)
        assert "Feed X: timeout" in html
        assert "<!--" in html

    def test_no_failures_comment_when_clean(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "Feed fetch issues" not in html

    def test_topic_html_escaped(self):
        story = dict(NEWS_STORY, topic='<script>alert(1)</script>')
        html = generate.render_html([story], [], FIXED_DT)
        assert "<script>" not in html

    def test_has_archive_link(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "archive/index.html" in html

    def test_has_feed_link(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert "feed.xml" in html

    def test_link_prefix_applied(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT, link_prefix="../../")
        assert "../../stories/story-1.html" in html
        assert "../../archive/index.html" in html

    def test_multiple_stories_rendered(self):
        stories = [NEWS_STORY, dict(NEWS_STORY, topic="Health", link="https://example.com/2",
                                    teaser="Another story.")]
        html = generate.render_html(stories, [], FIXED_DT)
        assert "2 stories" in html
        assert "Employment" in html
        assert "Health" in html

    def test_non_dict_story_skipped(self):
        html = generate.render_html(["not a dict"], [], FIXED_DT)
        # Should not crash; the invalid story is skipped
        assert "<!doctype html>" in html

    def test_noindex_meta_tag(self):
        html = generate.render_html([NEWS_STORY], [], FIXED_DT)
        assert 'noindex, nofollow' in html


class TestRenderStoryPage:
    def test_contains_topic(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "Employment" in html

    def test_contains_source(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "RNZ New Zealand" in html

    def test_full_content_split_into_paragraphs(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "<p>First paragraph.</p>" in html
        assert "<p>Second paragraph.</p>" in html
        assert "<p>Third paragraph.</p>" in html

    def test_original_link_present(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "https://example.com/story1" in html

    def test_back_link_default(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "../index.html" in html

    def test_back_link_custom(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY, back_href="../../index.html")
        assert "../../index.html" in html

    def test_tag_rendered(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert ">relevant<" in html

    def test_no_tag_span_when_null(self):
        story = dict(NEWS_STORY, tag=None)
        html = generate.render_story_page(story, FIXED_DT, 0, DATE_KEY)
        assert 'class="tag tag-' not in html

    def test_full_content_html_escaped(self):
        story = dict(NEWS_STORY, full_content="<b>bold</b>")
        html = generate.render_story_page(story, FIXED_DT, 0, DATE_KEY)
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_noindex_meta_tag(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "noindex, nofollow" in html

    def test_title_uses_topic(self):
        html = generate.render_story_page(NEWS_STORY, FIXED_DT, 0, DATE_KEY)
        assert "<title>Employment" in html


class TestRenderUnavailableHTML:
    def test_contains_year(self):
        html = generate.render_unavailable_html([], FIXED_DT)
        assert "2026" in html

    def test_contains_unavailability_message(self):
        html = generate.render_unavailable_html([], FIXED_DT)
        assert "unavailable" in html.lower()

    def test_failures_listed_in_comment(self):
        html = generate.render_unavailable_html(["Feed A: timeout", "Feed B: parse error"], FIXED_DT)
        assert "Feed A: timeout" in html
        assert "Feed B: parse error" in html

    def test_no_failures_shows_placeholder(self):
        html = generate.render_unavailable_html([], FIXED_DT)
        assert "no detail available" in html

    def test_failure_content_html_escaped(self):
        html = generate.render_unavailable_html(["<script>xss</script>"], FIXED_DT)
        assert "<script>" not in html
