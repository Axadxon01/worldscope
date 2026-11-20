# ADR 0002: Source and storage policy

Status: accepted, 2026-11-20

## Decision

1. Every source lives in `backend/config/sources.yaml` with its type, publisher country,
   language, ownership, licence, storage policy and review state.
2. A source is collected only when `enabled: true`, which requires `review: approved`:
   its terms have been read, `terms_url` and `licence` recorded and its ownership
   confirmed. The config loader and a database constraint both enforce this. A source
   is approved when its terms allow showing headline, feed summary and link in another
   product, commercial use included, or its content carries an open licence (public
   domain, Open Government Licence, CC BY, GDELT's terms). It is rejected when the terms
   limit use to personal reading or require the publisher's written permission; it can
   be reconsidered once permission is granted in writing. Each decision is recorded in
   `review_note` with the date the terms were read.
3. We store headline, feed-provided summary, link, publication and retrieval times, and
   derived metadata (language, entities, places, categories). We never store full
   article text, never fetch content behind a paywall or login, and honour robots.txt.
4. Every item in the product links to its original. AI summaries are written from
   headlines, summaries and metadata, and are labelled as analysis.
5. The MVP uses only sources that allow use in a deployed product: GDELT, curated
   RSS/Atom feeds, official statements and ReliefWeb. Commercial news APIs whose free
   tiers forbid production or commercial use (NewsAPI.org, GNews, the Guardian developer
   key) are not used. ACLED is excluded until it grants written permission, because its
   terms restrict use with AI systems and redistribution of raw data.
6. Source quality has no single credibility score. The product shows observable facts:
   primary or secondary source, ownership and state affiliation, published corrections
   policy, and how often a source is corroborated by independent outlets.

## Verification labels

Assigned by fixed rules, shown on every event with the rule that produced them:

1. Confirmed by cited source: a primary source (the government or body concerned) states it.
2. Multiple-source reporting: two or more publishers with different owners report it.
3. Single-source reporting: one publisher reports it.
4. Disputed: a party or another source contradicts it.
5. Unverified: only unnamed or social-media sourcing is cited.
6. Analysis: written by the AI analyst or a report; never presented as reporting.

## Review, 20 November 2026

Of the 43 configured sources: 6 approved and enabled (GDELT, UK FCDO, the Kremlin's
English feed, CISA, US EIA, Ukrinform), 14 rejected (their terms limit use to personal
reading or need written permission, including Kun.uz, Gazeta.uz, 24.kg, AKIpress,
Asia-Plus, TASS, Al Jazeera and SCMP) and 23 still pending because their terms pages
could not be read automatically (BBC, NPR and France 24 block automated reading, among
others). Reporting from rejected and pending outlets still reaches WorldScope through
GDELT, as metadata and links.

## Consequences

- `scripts/check_feeds.py` re-verifies every feed (status, format, freshness, robots.txt)
  and fails if an enabled source breaks. Ingestion in Milestone 3 records the same
  checks per run as source health.
- Ownership values are displayed as facts, so a `null` ownership is shown as "not yet
  confirmed" rather than guessed.
