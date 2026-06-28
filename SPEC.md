# Calm Daily Brief — Spec

A daily, calm, static news digest intended to replace habitual news scrolling.
Hosted on GitHub Pages, generated automatically once a day by a GitHub Actions
workflow. No accounts, no tracking, no engagement mechanics.

## Schedule

Runs daily at 4am AWST (8pm UTC) via a GitHub Actions cron trigger, plus
manual `workflow_dispatch`.

## Pipeline

1. Fetch RSS from the 10 sources below.
2. Send candidate stories to Claude, which selects exactly 9 and rewrites the
   current-news ones into calm, declarative language.
3. Render a single static `index.html`, plus one internal page per news story
   under `stories/`.
4. Commit and push to `main`; GitHub Pages serves it directly.

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
  original article in a new tab.
- Internal story page: full rewrite, source, tag, and a clearly separate
  link to the real original article.
- A daily abstract SVG banner (soft blurred shapes, muted palette,
  deterministic by date) appears in the header of both page types. No
  photos, no external image requests, no added API cost.
- Header shows site name, date, generation time, and story count only.

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
  is unavailable" page is published instead.

## Known deviations from the original draft spec

- News cards link to the internal calm rewrite first, with a separate link
  to the original article, rather than linking straight out.
- A daily generative SVG banner was added (original draft specified no
  images).
- Generation time was added to the header (original draft specified date
  only).
- Rewriting model is Claude Haiku 4.5, chosen to offset the cost of the
  expanded per-story pages.
- Runs as a GitHub Actions workflow with a scoped personal access token,
  rather than a native GitHub connector (none was available in this
  environment).
