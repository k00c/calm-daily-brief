#!/usr/bin/env python3
"""
Calm Daily Brief — daily digest generator.

Fetches RSS feeds, selects 9 stories per the editorial rules below, asks
Claude to rewrite the current-news stories in a calm, declarative tone,
and renders a single static index.html.

No personal information is read, used, or embedded anywhere in this
script or its output. Output is limited to: site name, date, story
count, topic labels, summaries, source names, tags, and links.
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
from anthropic import Anthropic

UA = "Mozilla/5.0 (compatible; CalmDailyBriefBot/1.0; +https://github.com/)"

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


SYSTEM_PROMPT = """You are the editor for "Calm Daily Brief", a small daily digest site that \
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
[reader context — removed from history, now stored only as a private secret]

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
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    payload = json.dumps(candidates, ensure_ascii=False)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=6000,
        system=SYSTEM_PROMPT,
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


def render_html(stories, failures, generated_at_awst):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    time_str = generated_at_awst.strftime("%-I:%M%p").lower() + " AWST"
    count = len(stories)

    cards_html = []
    for i, story in enumerate(stories):
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
            link = f"stories/story-{i + 1}.html"
            target_attrs = ""
            headline_html = ""
            body = html.escape(story.get("teaser", ""))

        cards_html.append(
            f"""
        <a class="{card_class}" href="{link}" {target_attrs}>
          <div class="card-top">
            <span class="topic">{topic}</span>
            {badge}
          </div>
          {headline_html}
          <p class="summary">{body}</p>
          <div class="card-bottom">
            <span class="source">{source}</span>
            {tag_html}
          </div>
        </a>"""
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
<title>Calm Daily Brief</title>
<style>{SHARED_CSS}</style>
</head>
<body>
<header>
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
</main>
{failures_comment}</body>
</html>
"""


def render_story_page(story, generated_at_awst):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    topic = html.escape(story.get("topic", ""))
    source = html.escape(story.get("source", ""))
    link = html.escape(story.get("link", "#"), quote=True)
    tag = story.get("tag")
    tag_html = f'<span class="tag tag-{tag}">{tag}</span>' if tag in TAG_NEWS else ""

    paragraphs = [p.strip() for p in re.split(r"\n+", story.get("full_content", "")) if p.strip()]
    body_html = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{topic} — Calm Daily Brief</title>
<style>{SHARED_CSS}</style>
</head>
<body class="story-page">
<main>
  <a class="back-link" href="../index.html">&larr; Back to Calm Daily Brief</a>
  <span class="topic">{topic}</span>
  <div class="body-text">
    {body_html}
  </div>
  <div class="story-meta">
    <span class="source">{source}</span>
    {tag_html}
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


def main():
    candidates, failures = fetch_all()
    generated_at_awst = datetime.now(timezone.utc) + timedelta(hours=8)

    if not candidates:
        output = render_unavailable_html(failures, generated_at_awst)
        reset_stories_dir()
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
                page = render_story_page(story, generated_at_awst)
                path = os.path.join(STORIES_DIR, f"story-{i + 1}.html")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(page)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    if failures:
        print("Feed/generation issues:", file=sys.stderr)
        for f_ in failures:
            print(f" - {f_}", file=sys.stderr)


if __name__ == "__main__":
    main()
