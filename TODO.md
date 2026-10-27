# TODO

## Aggregators (deferred from v1)

- **engineering.fyi** — `https://www.engineering.fyi/feed.xml` exists but was empty (0 items) on 2026-10-27.
- **engineeringblogs.xyz** — not a post feed; publishes an OPML list of ~406 blog feeds at `https://engineeringblogs.xyz/engblogs.opml`.
- Open questions:
  - Treat aggregators as their own adapter type: resolve each post to the original URL, credit the original site/author, record the "via" source.
  - One-time OPML import vs. manual adds from the list.
  - Use aggregator sightings to suggest new sources ("seen 4× via aggregators, you liked 3 — subscribe?").

## Ranker follow-ups (deferred from v1)

- **Next phase: learned ranker.** Logistic regression on embedding + source + author + length + age, trained on thumbs. No minimum-thumbs gate; starts once v1 is built.
- Stronger models trained on the logged dataset (e.g. LightGBM, learning-to-rank).
- Position-bias correction using the logged impression slot.
- Smarter exploration for wildcard slots (e.g. bandits) instead of random picks.
- Use clicks as a training signal, not just a tiebreaker.
- Topic diversity within an issue, so one topic can't fill the page.
- Offline evaluation: replay past issues against a new model before switching to it.
- Story grouping / clustering of posts about the same story.

## Ops (deferred from v1)

- Off-server backup of the SQLite file (feedback data is meant to be reusable for training).

## Discoverability (deferred from v1)

- Main risk to the product: the feed runs out of interesting ideas because the source set is small and fixed.
- Find and suggest new sources/authors outside current subscriptions (aggregators, outbound links from liked posts, etc.).
