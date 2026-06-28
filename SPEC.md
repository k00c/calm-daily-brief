# Calm Daily Brief — Spec

A daily, calm, static news digest intended to replace habitual news scrolling.
Hosted on GitHub Pages, generated automatically once a day by a GitHub Actions
workflow. No accounts, no tracking, no engagement mechanics.

## Schedule

Two independent GitHub Actions workflows, each with its own
`workflow_dispatch`:

- **Content job** (`daily-brief.yml`) — runs at midnight AWST (16:47 UTC).
  Running well before the reader wakes absorbs GitHub's documented scheduler
  delay (which can run 10-60 minutes, worst at the top of the hour) without
  it mattering.
- **Audio job** (`daily-audio.yml`) — runs at 4:17am AWST (20:17 UTC), off
  the top-of-hour contention window. If it's late or fails, the text site is
  unaffected; that morning just has no new audio episode.

## Pipeline

### Content job

1. Fetch RSS from the 10 sources below.
2. Send candidate stories to Claude, which selects exactly 9 and rewrites the
   current-news ones into calm, declarative language.
3. Render a single static `index.html`, plus one internal page per news story
   under `stories/`.
4. Persist the selected/rewritten stories as `data/stories-YYYY-MM-DD.json`
   (the only thing the audio job reads — no re-selection).
5. Write a dated, standalone copy into `archive/YYYY-MM-DD/`, and rebuild
   `archive/index.html` (a flat list of past days, kept indefinitely).
6. Commit and push to `main`; GitHub Pages serves it directly.

### Audio job

1. Read back the current day's `data/stories-YYYY-MM-DD.json`. If it's not
   there yet (content job hasn't run or failed), exit cleanly — no audio
   that day, text site untouched.
2. Build a spoken script: full rewritten text for news stories; headline +
   first sentence + "available to read on the site" for long-form pieces.
3. Synthesize the script locally with Piper TTS (open-source, no account, no
   API key) inside the CI runner, downloading the voice model fresh each run.
4. Encode the WAV to MP3 via `ffmpeg`, save as `audio/YYYY-MM-DD.mp3`.
5. Prune any audio file older than 60 days, then rebuild `feed.xml` (a
   static podcast RSS feed, one `<item>`/`<enclosure>` per remaining day) —
   unlisted, not submitted to any podcast directory; the feed URL itself is
   the only access control.
6. Commit and push `audio/` and `feed.xml`.

### Both jobs

Each posts a completion ping (`ntfy.sh`, topic stored as the `NTFY_TOPIC`
secret) with `if: always()`, success or failure, as the actual "ready"
signal. GitHub's native failure email remains on as a free backstop.

## RSS sources

- ABC News Australia
- SMH
- Reuters World News
- RNZ New Zealand
- ANTARA News Indonesia (English)
- The Conversation AU (long-form)
- Aeon (long-form)
- Hakai Magazine (long-form)
- Nautilus (long-form)
- Delayed Gratification (long-form)

## Story selection (9 total)

- 2 Australian national
- 1 Western Australian / Perth local, if available; otherwise another
  Australian national story
- 1 New Zealand
- 1 Indonesian / Southeast Asian
- 1 international story with genuine relevance
- 1 science, environment, or culture story (current news, not long-form)
- 1-2 long-form pieces — always included, not a fallback
- If fewer than 7 stories meet criteria, remaining slots are filled from
  long-form pieces. If there aren't enough candidates at all, fewer than 9
  stories are published rather than inventing content.

Selection is weighted toward stories personally relevant to the reader's life
circumstances (home base, family and work ties across a few specific
countries/regions). The detailed weighting context lives only in a private
environment variable supplied to the generation step at runtime — it is not
committed to the repository in plain text.

## Rewriting rules (current-news stories only)

- A one-sentence calm teaser for the front-page card.
- A 3-4 paragraph (roughly 150-220 word) full rewrite for the story's
  internal page, expanding on the teaser.
- Threat-amplifying language stripped (crisis, chaos, slams, explosive,
  shocking, alarming, fears, warns, devastating, bombshell, etc.).
- No conflict casualties or graphic detail, crime specifics, political
  outrage framing, or economic fear framing — candidates primarily about
  these are not selected at all.
- Each story tagged `awareness` (no action needed) or `relevant` (worth
  following).
- Long-form pieces are not rewritten — original headline, standfirst,
  source, and link only, labelled "Long read."

## Page flow

- Front page: card grid (1 column mobile, 2+ desktop). News cards show
  topic label, teaser, source, tag, and link to an internal full-rewrite
  page. Long-form cards are visually distinct and link straight to the
  original article in a new tab. Every card also has two small "mark
  read" / "skip" links.
- Internal story page: full rewrite, source, tag, the same read/skip
  links, and a clearly separate link to the real original article.
- A daily abstract SVG banner (soft blurred shapes, muted palette,
  deterministic by date) appears in the header of both page types. No
  photos, no external image requests, no added API cost.
- Header shows site name, date, generation time, and story count only.
- Footer links to the archive (`archive/index.html`) and the audio feed
  (`feed.xml`).

## Read/skip signal

Each card's "mark read" / "skip" links are pre-filled GitHub Issue URLs
(`github.com/k00c/calm-daily-brief/issues/new?title=...&body=...&labels=...`)
— no backend, GitHub itself is the log. Requires being signed into GitHub in
mobile Safari and one explicit submit tap; there's no way to fire-and-forget
a write to GitHub from a static page without auth.

## Home screen

`apple-mobile-web-app-capable` and `apple-touch-icon` meta tags are present
so "Add to Home Screen" in iOS Safari launches full-screen with no address
bar. This does not cache anything offline — it's a live fetch each time,
same as any bookmark.

## Archive

`archive/YYYY-MM-DD/` holds a full standalone copy of that day's site
(index + story pages), kept indefinitely — these are a few KB each, no
storage pressure. `archive/index.html` is a flat, most-recent-first list of
available days, rebuilt on every content run.

## Audio

A daily spoken digest, generated by a separate job (see Schedule/Pipeline
above) using Piper TTS — free, open-source, runs inside CI, no account or
API key. Audio files live in `audio/YYYY-MM-DD.mp3`, bounded to a 60-day
retention window (audio is the only artifact type that needs bounding —
unbounded growth would eventually approach GitHub Pages' 1GB soft
site-size limit; HTML/JSON archive content is negligible by comparison).
`feed.xml` is a static podcast RSS feed pointing at whatever's currently
retained, subscribable from any standard podcast app via "subscribe by
URL." No dedicated New Zealand or Australian Piper voice exists — this was
an explicitly accepted tradeoff once accent stopped being a requirement.

## Hard constraints

- Static HTML/CSS only; no JavaScript beyond what responsive layout
  strictly requires (currently none — pure CSS grid).
- No images other than the generated SVG banner.
- No engagement metrics, related-stories, infinite scroll, navigation,
  search, author names, or timestamps on individual story cards.
- No personal information, names, file paths, or usernames anywhere in
  committed source or generated output.
- Site is marked `noindex, nofollow` (meta tag + `robots.txt`) to keep it
  out of search engines, since GitHub Pages on the free tier requires a
  public repository.

## Error handling

- A failed feed is skipped; failures are logged as an HTML comment at the
  bottom of the page for debugging.
- If fewer than 7 stories meet selection criteria, remaining slots are
  filled from long-form sources.
- If all feeds fail, or the rewrite step errors, a minimal "today's digest
  is unavailable" page is published instead, and no `data/stories-*.json`
  is written that day — the audio job correctly skips itself.
- If the audio job's voice model download, Piper synthesis, or `ffmpeg`
  encoding fails, it logs to stderr and exits without writing partial
  files; the text site and feed are untouched.

## Secrets

`ANTHROPIC_API_KEY`, `READER_CONTEXT` (content job), `NTFY_TOPIC`
(completion-notification topic name, used by both jobs). No TTS API key
exists since Piper runs locally in CI with no account.

## Known deviations from the original draft spec

- News cards link to the internal calm rewrite first, with a separate link
  to the original article, rather than linking straight out.
- A daily generative SVG banner was added (original draft specified no
  images).
- Generation time was added to the header (original draft specified date
  only).
- Rewriting model is Claude Haiku 4.5, chosen to offset the cost of the
  expanded per-story pages.
- Runs as two separate GitHub Actions workflows with a scoped personal
  access token used only for occasional manual edits, rather than a native
  GitHub connector (none was available in this environment) — the daily
  scheduled runs themselves never need a user-supplied token.
- Phase 2/3 additions beyond the original draft: per-story-page archive,
  Piper-based audio digest + podcast feed, `ntfy.sh` completion
  notifications, GitHub-Issue-based read/skip links, and iOS home-screen
  meta tags.
