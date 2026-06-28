#!/usr/bin/env python3
"""
Calm Daily Brief — daily digest generator.

Two modes, selected with --mode:

  content (default) — fetches RSS, asks Claude to select/rewrite 9 stories,
    renders index.html + stories/, persists data/stories-YYYY-MM-DD.json,
    and writes a dated copy into archive/.

  audio — reads back that day's data/stories-YYYY-MM-DD.json (no
    re-selection), builds a spoken script, synthesizes it with Piper TTS,
    encodes to MP3 via ffmpeg, writes audio/YYYY-MM-DD.mp3, and updates the
    static podcast feed.xml (with bounded retention).

No personal information is read, used, or embedded anywhere in this
script or its output. Output is limited to: site name, date, story
count, topic labels, summaries, source names, tags, and links.
"""

import argparse
import html
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

UA = "Mozilla/5.0 (compatible; CalmDailyBriefBot/1.0; +https://github.com/)"
REPO_SLUG = "k00c/calm-daily-brief"
SITE_BASE_URL = "https://k00c.github.io/calm-daily-brief"

# source_id: (display_name, url, category, is_longform)
FEEDS = {
    "abc": ("ABC News Australia", "https://www.abc.net.au/news/feed/51120/rss.xml", "au", False),
    "smh": ("SMH", "https://www.smh.com.au/rss/feed.xml", "au", False),
    "reuters": ("Reuters World News", "https://feeds.reuters.com/reuters/worldNews", "international", False),
    "rnz": ("RNZ New Zealand", "https://www.rnz.co.nz/rss/national.xml", "nz", False),
    "antara": ("ANTARA News Indonesia", "https://en.antaranews.com/rss/latest-news.xml", "indonesia_sea", False),
    "conversation": ("The Conversation AU", "https://theconversation.com/au/feed", "longform", True),
    "aeon": ("Aeon", "https://aeon.co/feed.rss", "longform", True),
    "hakai": ("Hakai Magazine", "https://hakaimagazine.com/feed/", "longform", True),
    "nautilus": ("Nautilus", "https://nautil.us/feed/", "longform", True),
    "delayed_grat": ("Delayed Gratification", "https://www.slow-journalism.com/feed", "longform", True),
}

NEWS_RECENCY_DAYS = 3
LONGFORM_RECENCY_DAYS = 21
MAX_ITEMS_PER_SOURCE = 15

TAG_NEWS = {"awareness", "relevant"}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(REPO_ROOT, "index.html")
STORIES_DIR = os.path.join(REPO_ROOT, "stories")
DATA_DIR = os.path.join(REPO_ROOT, "data")
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
AUDIO_DIR = os.path.join(REPO_ROOT, "audio")
FEED_PATH = os.path.join(REPO_ROOT, "feed.xml")

AUDIO_RETENTION_DAYS = 60

HEAD_EXTRA = """<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Calm Brief">
<link rel="apple-touch-icon" href="/calm-daily-brief/apple-touch-icon.png">"""


def awst_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)


def date_key(dt):
    return dt.strftime("%Y-%m-%d")


def issue_link(action, label, story, date_str):
    """Pre-filled GitHub Issue link — the no-backend read/skip signal."""
    title = f"{action}: {label} — {date_str}"
    body_lines = [
        f"Date: {date_str}",
        f"Topic: {story.get('topic', '')}",
        f"Source: {story.get('source', '')}",
        f"Link: {story.get('link', '')}",
    ]
    body = "\n".join(body_lines)
    return (
        f"https://github.com/{REPO_SLUG}/issues/new"
        f"?title={quote(title)}&body={quote(body)}&labels={quote(action.lower())}"
    )


def strip_html(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_entry_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_feed(source_id, name, url, category, is_longform, failures):
    import feedparser

    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": UA})
        if parsed.bozo and not parsed.entries:
            raise RuntimeError(str(parsed.bozo_exception))
        if not parsed.entries:
            raise RuntimeError("no entries returned")

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=LONGFORM_RECENCY_DAYS if is_longform else NEWS_RECENCY_DAYS
        )
        items = []
        for entry in parsed.entries[: MAX_ITEMS_PER_SOURCE * 2]:
            link = entry.get("link", "").strip()
            title = strip_html(entry.get("title", "")).strip()
            if not link or not title:
                continue
            published = parse_entry_date(entry)
            if published and published < cutoff:
                continue
            summary = strip_html(entry.get("summary", "") or entry.get("description", ""))
            items.append(
                {
                    "source": name,
                    "category": category,
                    "is_longform": is_longform,
                    "title": title,
                    "summary": summary[:600],
                    "link": link,
                }
            )
            if len(items) >= MAX_ITEMS_PER_SOURCE:
                break

        if not items:
            raise RuntimeError("no recent entries within recency window")
        return items
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {exc}")
        return []


def fetch_all():
    failures = []
    all_items = []
    for source_id, (name, url, category, is_longform) in FEEDS.items():
        all_items.extend(fetch_feed(source_id, name, url, category, is_longform, failures))
    return all_items, failures


DEFAULT_READER_CONTEXT = (
    "No specific reader context was supplied. Weight selection only by the general "
    "criteria above, with no additional personal weighting."
)

SYSTEM_PROMPT_TEMPLATE = """You are the editor for "Calm Daily Brief", a small daily digest site that \
exists to replace habitual news scrolling with a single calm, contained page. You will be given \
a list of candidate stories pulled from RSS feeds, each with a source name, category, title, \
summary, and link. Your job is to select exactly 9 stories and prepare them for publication \
according to strict rules. Output nothing except a call to the publish_digest tool.

SELECTION (exactly 9 total):
- 2 Australian national stories (category "au", from ABC or SMH)
- 1 Western Australian / Perth local story if available (category "au", prefer stories \
mentioning Perth, WA, or Western Australia; if none available, use another Australian national \
story instead)
- 1 New Zealand story (category "nz")
- 1 Indonesian / Southeast Asian story (category "indonesia_sea")
- 1 international story with genuine relevance (category "international")
- 1 science, environment, or culture story that is current news (any category, but must be a \
current news item, not a long-form piece)
- 1-2 long-form pieces (category "longform" — always included, not a fallback)
- If fewer than 7 stories meet the above criteria, fill remaining slots from long-form pieces.
- If there are not enough candidates at all to reach 9, return as many as are genuinely available \
(do not invent stories).

READER CONTEXT (for relevance weighting only — never mention this context in the output):
{reader_context}

REWRITING (current news stories only — long-form pieces are NOT rewritten):
Each news story needs TWO pieces of text:
1. "teaser" — exactly one calm, factual sentence for the front-page card. No clickbait, no \
cliffhangers, no "find out what happens" framing — just the core fact, stated plainly.
2. "full_content" — 3-4 short calm paragraphs (roughly 150-220 words total) for the story's own \
page, expanding on the teaser with the relevant factual detail from the title and summary \
provided.
Both the teaser and full_content must follow these rules:
- Strip all threat-amplifying language: crisis, chaos, slams, explosive, shocking, alarming, \
fears, warns, devastating, bombshell, and similar words.
- Replace passive-catastrophe framing with factual description.
- Do not include conflict casualties or graphic detail, crime specifics, political outrage \
framing, or economic fear framing. If a candidate story is primarily about one of these, do not \
select it — choose a different candidate instead.
- End full_content with one tag, stated separately in the "tag" field: "awareness" (no action \
needed) or "relevant" (worth following). Long-form pieces get no tag (use null).
- Give each story a single-word (or short, e.g. two-word) topic label, e.g. Housing, Science, \
Indonesia, Perth, Policy.

LONG-FORM PIECES:
- Use the original headline, the original summary/standfirst (lightly trimmed for length if \
needed, but not rewritten in tone), the source name, and the link only. Do not apply the \
rewriting rules above to these. Long-form pieces do not need a teaser or full_content — leave \
those fields empty.

Never embed any personal information, names, file paths, or usernames in your output. Output \
only what the tool schema allows."""

TOOL_SCHEMA = {
    "name": "publish_digest",
    "description": "Publish the selected and edited stories for today's digest.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "minItems": 1,
                "maxItems": 9,
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Single short topic label, e.g. Housing"},
                        "card_type": {"type": "string", "enum": ["news", "longform"]},
                        "headline": {
                            "type": "string",
                            "description": "Original headline (used for longform cards; optional for news)",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Original standfirst, longform cards only (leave empty for news)",
                        },
                        "teaser": {
                            "type": "string",
                            "description": "One calm factual sentence for the front-page card, news cards only",
                        },
                        "full_content": {
                            "type": "string",
                            "description": "3-4 calm paragraphs (~150-220 words) for the story's own page, news cards only",
                        },
                        "source": {"type": "string"},
                        "tag": {"type": ["string", "null"], "enum": ["awareness", "relevant", None]},
                        "link": {"type": "string"},
                    },
                    "required": ["topic", "card_type", "source", "link"],
                },
            }
        },
        "required": ["stories"],
    },
}


def select_and_rewrite(candidates):
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    reader_context = os.environ.get("READER_CONTEXT", "").strip() or DEFAULT_READER_CONTEXT
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(reader_context=reader_context)
    payload = json.dumps(candidates, ensure_ascii=False)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=6000,
        system=system_prompt,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": "publish_digest"},
        messages=[
            {
                "role": "user",
                "content": f"Here are today's candidate stories as a JSON array:\n{payload}",
            }
        ],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "publish_digest":
            return block.input.get("stories", [])
    raise RuntimeError("model did not return a publish_digest tool call")


SHARED_CSS = """
  :root {
    --bg: #f6f4ef;
    --card-bg: #fffefc;
    --longform-bg: #f0ebe1;
    --text: #2c2a26;
    --muted: #8a8377;
    --muted-2: #aba493;
    --accent: #7d7256;
    --accent-soft: #ece6d8;
    --border: #e6e1d4;
    --shadow: 0 1px 2px rgba(40, 35, 20, 0.04), 0 4px 14px rgba(40, 35, 20, 0.05);
    --shadow-hover: 0 2px 4px rgba(40, 35, 20, 0.05), 0 10px 24px rgba(40, 35, 20, 0.08);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 0;
    background: var(--bg);
    background-image: radial-gradient(circle at 20% 0%, rgba(125, 114, 86, 0.05), transparent 55%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  header {
    max-width: 900px;
    margin: 0 auto;
    padding: 56px 24px 28px;
  }
  header .kicker {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    margin: 0 0 10px;
  }
  header h1 {
    font-size: 1.7rem;
    font-weight: 650;
    letter-spacing: -0.01em;
    margin: 0 0 10px;
    color: var(--text);
  }
  header p.meta {
    margin: 0;
    color: var(--muted);
    font-size: 0.92rem;
  }
  header .rule {
    margin-top: 26px;
    height: 1px;
    background: linear-gradient(to right, var(--border), transparent 85%);
  }
  .banner {
    display: block;
    width: 100%;
    height: auto;
    max-height: 130px;
    border-radius: 14px;
    margin-bottom: 22px;
  }
  main {
    max-width: 900px;
    margin: 0 auto;
    padding: 8px 24px 72px;
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr;
    gap: 18px;
  }
  @media (min-width: 720px) {
    .grid { grid-template-columns: 1fr 1fr; gap: 20px; }
  }
  .card {
    display: flex;
    flex-direction: column;
    background: var(--card-bg);
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
    border-radius: 12px;
    padding: 24px;
    text-decoration: none;
    color: var(--text);
    transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
  }
  .card:hover {
    box-shadow: var(--shadow-hover);
    transform: translateY(-1px);
    border-color: var(--muted-2);
  }
  .card.longform {
    background: var(--longform-bg);
    border-left: 3px solid var(--accent);
  }
  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }
  .topic {
    font-size: 0.72rem;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent);
  }
  .longread-badge {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    color: var(--accent);
    background: rgba(125, 114, 86, 0.1);
    border-radius: 999px;
    padding: 3px 10px;
  }
  .headline {
    font-weight: 600;
    margin: 0 0 8px;
    font-size: 1.04rem;
    letter-spacing: -0.005em;
  }
  .summary {
    margin: 0 0 16px;
    font-size: 0.96rem;
    color: var(--text);
    flex-grow: 1;
  }
  .card-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.8rem;
    color: var(--muted);
    padding-top: 14px;
    border-top: 1px solid var(--border);
  }
  .tag {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--accent);
    background: var(--accent-soft);
    border-radius: 999px;
    padding: 3px 10px;
  }
  .feedback-links {
    display: flex;
    gap: 10px;
  }
  .feedback-links a {
    font-size: 0.74rem;
    color: var(--muted);
    text-decoration: none;
    border-bottom: 1px dotted var(--muted-2);
  }
  .feedback-links a:hover {
    color: var(--accent);
  }
  .archive-note {
    margin-top: 36px;
    text-align: center;
    font-size: 0.82rem;
  }
  .archive-note a {
    color: var(--accent);
    text-decoration: none;
    border-bottom: 1px dotted var(--accent);
  }
  .story-page main {
    max-width: 620px;
    padding-top: 16px;
  }
  .story-page .back-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 32px;
    color: var(--muted);
    text-decoration: none;
    font-size: 0.86rem;
    transition: color 0.15s ease;
  }
  .story-page .back-link:hover {
    color: var(--accent);
  }
  .story-page .topic {
    display: block;
    margin-bottom: 14px;
  }
  .story-page .body-text {
    background: var(--card-bg);
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
    border-radius: 12px;
    padding: 28px;
  }
  .story-page .body-text p {
    margin: 0 0 14px;
    font-size: 1.04rem;
  }
  .story-page .body-text p:last-child {
    margin-bottom: 0;
  }
  .story-page .story-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 20px;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .story-page .original-link {
    display: inline-block;
    margin-top: 22px;
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--accent);
    text-decoration: none;
    background: var(--accent-soft);
    border-radius: 8px;
    padding: 11px 18px;
    transition: background 0.15s ease;
  }
  .story-page .original-link:hover {
    background: #e2dac5;
  }
  .footer-note {
    margin-top: 48px;
    text-align: center;
    color: var(--muted-2);
    font-size: 0.78rem;
  }
"""


def slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


BANNER_PALETTE = [
    "#e7ddc6",  # warm sand
    "#d7c9a8",  # deeper sand
    "#cdd5c4",  # soft sage
    "#c9d2d6",  # muted blue-grey
    "#e0c9b3",  # dusty clay
    "#d8c9d3",  # faint heather
    "#cfd8c0",  # pale moss
]


def render_banner_svg(seed_str):
    """A soft, blurred abstract banner, deterministic per day so it changes
    daily but always stays muted and calm — no photos, no external assets."""
    rng = random.Random(seed_str)
    width, height = 900, 160
    blobs = []
    for _ in range(4):
        cx = rng.uniform(width * 0.05, width * 0.95)
        cy = rng.uniform(height * 0.1, height * 0.9)
        r = rng.uniform(70, 130)
        color = rng.choice(BANNER_PALETTE)
        opacity = rng.uniform(0.45, 0.7)
        blobs.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{r:.0f}" fill="{color}" opacity="{opacity:.2f}" />')

    return f"""<svg class="banner" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" \
role="img" aria-label="A soft abstract gradient, for visual calm only">
  <defs>
    <filter id="banner-blur" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="36" />
    </filter>
  </defs>
  <rect width="{width}" height="{height}" fill="#f6f4ef" />
  <g filter="url(#banner-blur)">
    {''.join(blobs)}
  </g>
</svg>"""


def render_html(stories, failures, generated_at_awst, link_prefix=""):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    date_key_str = date_key(generated_at_awst)
    time_str = generated_at_awst.strftime("%-I:%M%p").lower() + " AWST"
    count = len(stories)
    banner_svg = render_banner_svg(date_key_str)

    cards_html = []
    for i, story in enumerate(stories):
        # Ensure story is a dictionary; parse if it's a string
        if isinstance(story, str):
            try:
                story = json.loads(story)
            except (json.JSONDecodeError, TypeError):
                failures.append(f"Story {i + 1}: invalid format, skipping")
                continue
        
        is_longform = story.get("card_type") == "longform"
        topic = html.escape(story.get("topic", ""))
        source = html.escape(story.get("source", ""))
        tag = story.get("tag")

        card_class = "card longform" if is_longform else "card"
        badge = '<span class="longread-badge">Long read</span>' if is_longform else ""
        tag_html = f'<span class="tag tag-{tag}">{tag}</span>' if tag in TAG_NEWS else ""

        if is_longform:
            link = html.escape(story.get("link", "#"), quote=True)
            target_attrs = 'target="_blank" rel="noopener noreferrer"'
            headline = html.escape(story.get("headline", ""))
            headline_html = f'<p class="headline">{headline}</p>' if headline else ""
            body = html.escape(story.get("summary", ""))
        else:
            link = f"{link_prefix}stories/story-{i + 1}.html"
            target_attrs = ""
            headline_html = ""
            body = html.escape(story.get("teaser", ""))

        label = story.get("headline") or story.get("topic", "story")
        read_link = html.escape(issue_link("Read", label, story, date_key_str), quote=True)
        skip_link = html.escape(issue_link("Skip", label, story, date_key_str), quote=True)
        feedback_html = (
            f'<span class="feedback-links">'
            f'<a href="{read_link}" target="_blank" rel="noopener noreferrer">mark read</a>'
            f'<a href="{skip_link}" target="_blank" rel="noopener noreferrer">skip</a>'
            f"</span>"
        )

        cards_html.append(
            f"""
        <div class="{card_class}">
        <a style="text-decoration:none;color:inherit;display:block;" href="{link}" {target_attrs}>
          <div class="card-top">
            <span class="topic">{topic}</span>
            {badge}
          </div>
          {headline_html}
          <p class="summary">{body}</p>
        </a>
          <div class="card-bottom">
            <span class="source">{source}</span>
            {tag_html}
            {feedback_html}
          </div>
        </div>"""
        )

    failures_comment = ""
    if failures:
        escaped = "\n".join(f"  - {html.escape(f)}" for f in failures)
        failures_comment = f"\n<!--\nFeed fetch issues for {date_str}:\n{escaped}\n-->\n"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{HEAD_EXTRA}
<title>Calm Daily Brief</title>
<style>{SHARED_CSS}</style>
</head>
<body>
<header>
  {banner_svg}
  <p class="kicker">Today's brief</p>
  <h1>Calm Daily Brief</h1>
  <p class="meta">{date_str} &middot; generated {time_str} &middot; {count} stories</p>
  <div class="rule"></div>
</header>
<main>
  <div class="grid">
    {''.join(cards_html)}
  </div>
  <p class="footer-note">That's everything for today.</p>
  <p class="archive-note"><a href="{link_prefix}archive/index.html">Past days</a> &middot; <a href="{link_prefix}feed.xml">Audio feed</a></p>
</main>
{failures_comment}</body>
</html>
"""


def render_story_page(story, generated_at_awst, index, date_key_str, back_href="../index.html"):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    banner_svg = render_banner_svg(date_key_str + "-story")
    topic = html.escape(story.get("topic", ""))
    source = html.escape(story.get("source", ""))
    link = html.escape(story.get("link", "#"), quote=True)
    tag = story.get("tag")
    tag_html = f'<span class="tag tag-{tag}">{tag}</span>' if tag in TAG_NEWS else ""

    label = story.get("headline") or story.get("topic", "story")
    read_link = html.escape(issue_link("Read", label, story, date_key_str), quote=True)
    skip_link = html.escape(issue_link("Skip", label, story, date_key_str), quote=True)
    feedback_html = (
        f'<span class="feedback-links">'
        f'<a href="{read_link}" target="_blank" rel="noopener noreferrer">mark read</a>'
        f'<a href="{skip_link}" target="_blank" rel="noopener noreferrer">skip</a>'
        f"</span>"
    )

    paragraphs = [p.strip() for p in re.split(r"\n+", story.get("full_content", "")) if p.strip()]
    body_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{HEAD_EXTRA}
<title>{topic} — Calm Daily Brief</title>
<style>{SHARED_CSS}</style>
</head>
<body class="story-page">
<main>
  {banner_svg}
  <a class="back-link" href="{back_href}">&larr; Back to Calm Daily Brief</a>
  <span class="topic">{topic}</span>
  <div class="body-text">
    {body_html}
  </div>
  <div class="story-meta">
    <span class="source">{source}</span>
    {tag_html}
    {feedback_html}
  </div>
  <a class="original-link" href="{link}" target="_blank" rel="noopener noreferrer">Read the original story &rarr;</a>
  <p class="footer-note" style="margin-top: 40px;">{date_str}</p>
</main>
</body>
</html>
"""


def render_unavailable_html(failures, generated_at_awst):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    escaped = "\n".join(f"  - {html.escape(f)}" for f in failures) if failures else "  (no detail available)"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{HEAD_EXTRA}
<title>Calm Daily Brief</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #faf9f6;
    color: #2b2b28;
    max-width: 600px;
    margin: 80px auto;
    padding: 0 24px;
    line-height: 1.6;
  }}
  h1 {{ font-size: 1.4rem; }}
</style>
</head>
<body>
<h1>Calm Daily Brief</h1>
<p>{date_str}</p>
<p>Today's digest is unavailable — all sources failed to respond. Check back tomorrow.</p>
</body>
</html>
<!--
Feed fetch issues for {date_str}:
{escaped}
-->
"""


def reset_stories_dir():
    if os.path.isdir(STORIES_DIR):
        for name in os.listdir(STORIES_DIR):
            if name.endswith(".html"):
                os.remove(os.path.join(STORIES_DIR, name))
    else:
        os.makedirs(STORIES_DIR, exist_ok=True)


def write_archive_day(stories, generated_at_awst, date_key_str):
    """Write a dated, standalone copy into archive/YYYY-MM-DD/ — kept
    indefinitely (small HTML/text only)."""
    day_dir = os.path.join(ARCHIVE_DIR, date_key_str)
    day_stories_dir = os.path.join(day_dir, "stories")
    os.makedirs(day_stories_dir, exist_ok=True)

    index_html = render_html(stories, [], generated_at_awst, link_prefix="../../")
    with open(os.path.join(day_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    for i, story in enumerate(stories):
        if story.get("card_type") == "longform":
            continue
        page = render_story_page(
            story, generated_at_awst, i, date_key_str, back_href="../index.html"
        )
        with open(os.path.join(day_stories_dir, f"story-{i + 1}.html"), "w", encoding="utf-8") as f:
            f.write(page)

    rebuild_archive_index()


def rebuild_archive_index():
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    days = sorted(
        (
            name
            for name in os.listdir(ARCHIVE_DIR)
            if os.path.isdir(os.path.join(ARCHIVE_DIR, name)) and re.match(r"^\d{4}-\d{2}-\d{2}$", name)
        ),
        reverse=True,
    )
    items = "\n".join(f'    <li><a href="{d}/index.html">{d}</a></li>' for d in days)
    output = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
{HEAD_EXTRA}
<title>Archive — Calm Daily Brief</title>
<style>{SHARED_CSS}</style>
</head>
<body class="story-page">
<main>
  <a class="back-link" href="../index.html">&larr; Back to Calm Daily Brief</a>
  <h1>Past days</h1>
  <ul style="list-style:none;padding:0;line-height:2.1;">
{items}
  </ul>
</main>
</body>
</html>
"""
    with open(os.path.join(ARCHIVE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(output)


def persist_stories_json(stories, failures, generated_at_awst, date_key_str):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "date": date_key_str,
        "generated_at": generated_at_awst.isoformat(),
        "stories": stories,
        "failures": failures,
    }
    path = os.path.join(DATA_DIR, f"stories-{date_key_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_content():
    candidates, failures = fetch_all()
    generated_at_awst = awst_now()
    date_key_str = date_key(generated_at_awst)

    if not candidates:
        output = render_unavailable_html(failures, generated_at_awst)
        reset_stories_dir()
        stories = []
    else:
        try:
            stories = select_and_rewrite(candidates)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Claude API error: {exc}")
            stories = []

        if not stories:
            output = render_unavailable_html(failures, generated_at_awst)
            reset_stories_dir()
        else:
            output = render_html(stories, failures, generated_at_awst)
            reset_stories_dir()
            for i, story in enumerate(stories):
                if story.get("card_type") == "longform":
                    continue
                page = render_story_page(story, generated_at_awst, i, date_key_str)
                path = os.path.join(STORIES_DIR, f"story-{i + 1}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(page)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    if stories:
        persist_stories_json(stories, failures, generated_at_awst, date_key_str)
        write_archive_day(stories, generated_at_awst, date_key_str)

    if failures:
        print("Feed/generation issues:", file=sys.stderr)
        for f_ in failures:
            print(f" - {f_}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Audio mode
# ---------------------------------------------------------------------------

PIPER_MODEL_ENV = "PIPER_MODEL_PATH"
PIPER_CONFIG_ENV = "PIPER_CONFIG_PATH"
PAUSE_SECONDS_BETWEEN_SEGMENTS = 1.2


def build_spoken_segments(stories, generated_at_awst):
    """One text segment per spoken unit: an intro, then one per story (title
    announced first, then the content), then a closing line. Kept as
    separate segments — rather than one block of text — so a real silence
    gap can be inserted between stories instead of relying on punctuation."""
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    segments = [f"Calm Daily Brief for {date_str}."]
    for story in stories:
        if story.get("card_type") == "longform":
            headline = story.get("headline", "").strip() or "Long read"
            summary = story.get("summary", "").strip()
            first_sentence = re.split(r"(?<=[.!?])\s+", summary)[0] if summary else ""
            text = f"{headline}. {first_sentence} This one is available to read on the site."
        else:
            topic = story.get("topic", "").strip() or "Story"
            body = story.get("full_content", "").strip() or story.get("teaser", "").strip()
            text = f"{topic}. {body}"
        segments.append(text)
    segments.append("That's everything for today.")
    return segments


def synthesize_segment_with_piper(text, wav_path):
    model_path = os.environ.get(PIPER_MODEL_ENV)
    if not model_path or not os.path.isfile(model_path):
        raise RuntimeError(f"Piper voice model not found (set {PIPER_MODEL_ENV})")
    config_path = os.environ.get(PIPER_CONFIG_ENV)

    cmd = ["python3", "-m", "piper", "-m", model_path, "-f", wav_path]
    if config_path and os.path.isfile(config_path):
        cmd += ["-c", config_path]

    result = subprocess.run(
        cmd, input=text, text=True, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"piper failed: {result.stderr.strip()[:500]}")
    if not os.path.isfile(wav_path):
        raise RuntimeError("piper did not produce a WAV file")


def concat_wavs_with_silence(wav_paths, pause_seconds, output_path):
    """Concatenate WAV files with a true silence gap between each — gives a
    reliable, controllable pause between stories regardless of how Piper
    handles sentence-level pausing."""
    import wave

    if not wav_paths:
        raise RuntimeError("no audio segments to concatenate")

    with wave.open(wav_paths[0], "rb") as w0:
        params = w0.getparams()
    silence_frames = int(pause_seconds * params.framerate)
    silence_bytes = b"\x00" * (silence_frames * params.nchannels * params.sampwidth)

    with wave.open(output_path, "wb") as out:
        out.setparams(params)
        for i, path in enumerate(wav_paths):
            with wave.open(path, "rb") as w:
                if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (
                    params.nchannels, params.sampwidth, params.framerate
                ):
                    raise RuntimeError(f"WAV format mismatch in {path}")
                out.writeframes(w.readframes(w.getnframes()))
            if i < len(wav_paths) - 1:
                out.writeframesraw(silence_bytes)


def encode_to_mp3(wav_path, mp3_path):
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "4", mp3_path],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-500:]}")
    if not os.path.isfile(mp3_path):
        raise RuntimeError("ffmpeg did not produce an MP3 file")


def prune_old_audio(failures):
    """Delete audio files older than the retention window and return the
    set of date keys that still have audio on disk."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    cutoff = awst_now() - timedelta(days=AUDIO_RETENTION_DAYS)
    kept = set()
    for name in os.listdir(AUDIO_DIR):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\.mp3$", name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(os.path.join(AUDIO_DIR, name))
            except OSError as exc:
                failures.append(f"Could not prune old audio {name}: {exc}")
        else:
            kept.add(m.group(1))
    return kept


def build_feed_xml(kept_dates):
    """Rebuild feed.xml from scratch from whatever MP3s currently exist in
    audio/ — simplest way to stay consistent with the retention prune."""
    items_xml = []
    for date_str in sorted(kept_dates, reverse=True):
        mp3_path = os.path.join(AUDIO_DIR, f"{date_str}.mp3")
        if not os.path.isfile(mp3_path):
            continue
        size = os.path.getsize(mp3_path)
        try:
            pub_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=4, minute=30, tzinfo=timezone(timedelta(hours=8))
            )
        except ValueError:
            continue
        pub_date = pub_dt.strftime("%a, %d %b %Y %H:%M:%S %z")
        url = f"{SITE_BASE_URL}/audio/{date_str}.mp3"
        items_xml.append(
            f"""    <item>
      <title>Calm Daily Brief — {date_str}</title>
      <description>The day's calm digest, read aloud.</description>
      <pubDate>{pub_date}</pubDate>
      <guid isPermaLink="false">calm-daily-brief-{date_str}</guid>
      <enclosure url="{xml_escape(url)}" length="{size}" type="audio/mpeg" />
    </item>"""
        )

    cover_url = f"{SITE_BASE_URL}/podcast-cover.png"
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Calm Daily Brief</title>
    <link>{SITE_BASE_URL}/</link>
    <description>A calm, daily spoken digest. Unlisted — not submitted to any podcast directory.</description>
    <language>en</language>
    <itunes:explicit>false</itunes:explicit>
    <itunes:author>Calm Daily Brief</itunes:author>
    <itunes:category text="News" />
    <itunes:image href="{xml_escape(cover_url)}" />
    <image>
      <url>{xml_escape(cover_url)}</url>
      <title>Calm Daily Brief</title>
      <link>{SITE_BASE_URL}/</link>
    </image>
{chr(10).join(items_xml)}
  </channel>
</rss>
"""
    with open(FEED_PATH, "w", encoding="utf-8") as f:
        f.write(feed)


def run_audio():
    generated_at_awst = awst_now()
    date_key_str = date_key(generated_at_awst)
    data_path = os.path.join(DATA_DIR, f"stories-{date_key_str}.json")

    if not os.path.isfile(data_path):
        print(f"No stories JSON for {date_key_str} yet ({data_path}) — content job "
              f"hasn't run or hasn't finished. Skipping audio cleanly.", file=sys.stderr)
        return

    with open(data_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    stories = payload.get("stories", [])
    if not stories:
        print(f"Stories JSON for {date_key_str} is empty. Skipping audio.", file=sys.stderr)
        return

    failures = []
    segments = build_spoken_segments(stories, generated_at_awst)

    os.makedirs(AUDIO_DIR, exist_ok=True)
    tmp_dir = f"/tmp/calm-brief-{date_key_str}-segments"
    os.makedirs(tmp_dir, exist_ok=True)
    combined_wav = f"/tmp/calm-brief-{date_key_str}.wav"
    mp3_path = os.path.join(AUDIO_DIR, f"{date_key_str}.mp3")

    try:
        segment_paths = []
        for i, segment_text in enumerate(segments):
            seg_path = os.path.join(tmp_dir, f"segment-{i:02d}.wav")
            synthesize_segment_with_piper(segment_text, seg_path)
            segment_paths.append(seg_path)

        concat_wavs_with_silence(segment_paths, PAUSE_SECONDS_BETWEEN_SEGMENTS, combined_wav)
        encode_to_mp3(combined_wav, mp3_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Audio generation failed: {exc}", file=sys.stderr)
        return
    finally:
        import shutil
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.isfile(combined_wav):
            os.remove(combined_wav)

    kept = prune_old_audio(failures)
    kept.add(date_key_str)
    build_feed_xml(kept)

    if failures:
        print("Audio housekeeping issues:", file=sys.stderr)
        for f_ in failures:
            print(f" - {f_}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Calm Daily Brief generator")
    parser.add_argument("--mode", choices=["content", "audio"], default="content")
    args = parser.parse_args()

    if args.mode == "audio":
        run_audio()
    else:
        run_content()


if __name__ == "__main__":
    main()
