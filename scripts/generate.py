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
- Rewrite each news story in 3-4 calm, declarative sentences based on its title and summary.
- Strip all threat-amplifying language: crisis, chaos, slams, explosive, shocking, alarming, \
fears, warns, devastating, bombshell, and similar words.
- Replace passive-catastrophe framing with factual description.
- Do not include conflict casualties or graphic detail, crime specifics, political outrage \
framing, or economic fear framing. If a candidate story is primarily about one of these, do not \
select it — choose a different candidate instead.
- End the editorial judgement with one tag: "awareness" (no action needed) or "relevant" (worth \
following). Long-form pieces get no tag (use null).
- Give each story a single-word (or short, e.g. two-word) topic label, e.g. Housing, Science, \
Indonesia, Perth, Policy.

LONG-FORM PIECES:
- Use the original headline, the original summary/standfirst (lightly trimmed for length if \
needed, but not rewritten in tone), the source name, and the link only. Do not apply the \
rewriting rules above to these.

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
                            "description": "Rewritten 3-4 sentence summary for news, or original standfirst for longform",
                        },
                        "source": {"type": "string"},
                        "tag": {"type": ["string", "null"], "enum": ["awareness", "relevant", None]},
                        "link": {"type": "string"},
                    },
                    "required": ["topic", "card_type", "summary", "source", "link"],
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
        model="claude-sonnet-4-6",
        max_tokens=4000,
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


def render_html(stories, failures, generated_at_awst):
    date_str = generated_at_awst.strftime("%A, %d %B %Y")
    count = len(stories)

    cards_html = []
    for story in stories:
        is_longform = story.get("card_type") == "longform"
        topic = html.escape(story.get("topic", ""))
        source = html.escape(story.get("source", ""))
        link = html.escape(story.get("link", "#"), quote=True)
        summary = html.escape(story.get("summary", ""))
        headline = html.escape(story.get("headline", "")) if story.get("headline") else ""
        tag = story.get("tag")

        card_class = "card longform" if is_longform else "card"
        badge = '<span class="longread-badge">Long read</span>' if is_longform else ""
        tag_html = f'<span class="tag tag-{tag}">[{tag}]</span>' if tag in TAG_NEWS else ""
        headline_html = f'<p class="headline">{headline}</p>' if headline else ""

        cards_html.append(
            f"""
        <a class="{card_class}" href="{link}" target="_blank" rel="noopener noreferrer">
          <div class="card-top">
            <span class="topic">{topic}</span>
            {badge}
          </div>
          {headline_html}
          <p class="summary">{summary}</p>
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
<style>
  :root {{
    --bg: #faf9f6;
    --card-bg: #ffffff;
    --longform-bg: #f5f3ee;
    --text: #2b2b28;
    --muted: #767066;
    --accent: #8a8170;
    --border: #e4e1d8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.5;
  }}
  header {{
    max-width: 880px;
    margin: 0 auto;
    padding: 48px 24px 24px;
  }}
  header h1 {{
    font-size: 1.5rem;
    font-weight: 600;
    margin: 0 0 6px;
    letter-spacing: 0.01em;
  }}
  header p {{
    margin: 0;
    color: var(--muted);
    font-size: 0.95rem;
  }}
  main {{
    max-width: 880px;
    margin: 0 auto;
    padding: 0 24px 64px;
  }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 20px;
  }}
  @media (min-width: 720px) {{
    .grid {{ grid-template-columns: 1fr 1fr; }}
  }}
  .card {{
    display: block;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 22px;
    text-decoration: none;
    color: var(--text);
  }}
  .card.longform {{
    background: var(--longform-bg);
    border-left: 3px solid var(--accent);
  }}
  .card-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }}
  .topic {{
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--accent);
  }}
  .longread-badge {{
    font-size: 0.7rem;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 2px 9px;
  }}
  .headline {{
    font-weight: 600;
    margin: 0 0 8px;
    font-size: 1.02rem;
  }}
  .summary {{
    margin: 0 0 14px;
    font-size: 0.96rem;
    color: var(--text);
  }}
  .card-bottom {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 0.82rem;
    color: var(--muted);
  }}
  .tag {{
    font-size: 0.78rem;
    color: var(--muted);
  }}
</style>
</head>
<body>
<header>
  <h1>Calm Daily Brief</h1>
  <p>{date_str} &middot; {count} stories</p>
</header>
<main>
  <div class="grid">
    {''.join(cards_html)}
  </div>
</main>
{failures_comment}</body>
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


def main():
    candidates, failures = fetch_all()
    generated_at_awst = datetime.now(timezone.utc) + timedelta(hours=8)

    if not candidates:
        output = render_unavailable_html(failures, generated_at_awst)
    else:
        try:
            stories = select_and_rewrite(candidates)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Claude API error: {exc}")
            stories = []

        if not stories:
            output = render_unavailable_html(failures, generated_at_awst)
        else:
            output = render_html(stories, failures, generated_at_awst)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    if failures:
        print("Feed/generation issues:", file=sys.stderr)
        for f_ in failures:
            print(f" - {f_}", file=sys.stderr)


if __name__ == "__main__":
    main()
