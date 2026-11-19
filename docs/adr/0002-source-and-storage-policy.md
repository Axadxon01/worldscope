# ADR 0002: Source and storage policy

Status: accepted, 2026-11-20

## Decision

1. Every source lives in `config/sources.yaml` with its type, publisher country,
   language, ownership, licence, storage policy and review state.
2. A source is collected only when `enabled: true`, which requires `review: approved`:
   its terms have been read, `terms_url` recorded and its ownership confirmed. Sources
   with an open licence (public domain, Open Government Licence, CC BY, GDELT's terms)
   are approved on that basis.
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

## Consequences

- `scripts/check_feeds.py` re-verifies every feed (status, format, freshness, robots.txt)
  and fails if an enabled source breaks. Ingestion in Milestone 3 records the same
  checks per run as source health.
- Ownership values are displayed as facts, so a `null` ownership is shown as "not yet
  confirmed" rather than guessed.
