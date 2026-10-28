# TODO

## Launch (do first)

- **Deploy** — follow `README.md`: fill `.env` from `.env.example` (`TS_AUTHKEY`, Resend key, secrets; `APP_PASSWORD_HASH` from `newsletter hash-password`, in single quotes), `docker compose up -d`, `docker compose exec web newsletter seed sources.yaml`, set `BASE_URL` to the `ts.net` URL.
- Open decisions:
  - **Anthropic** (`anthropic.com/engineering`) — gives no posts: its `sitemap.xml` has no `/engineering/` URLs. Remove it from `sources.yaml`, or build a listing-page adapter (read post links from the index page; would also fix Uber).
  - **Uber** — subscribes via sitemap but yields category listing pages ("AI / ML | Uber Blog"), not posts. Keep or remove.
  - **Login limiter** — one global limit (10 failures/hour) lets a stranger block new logins for 1h; the cookie and API token still work. Keep, or limit per client IP (check how Funnel forwards the visitor's address first).

## v1 known gaps (from the final review)

- Title + author dedup has no time window, so a recurring title ("Monthly roundup") merges with the first one forever.
- No private-IP guard on URLs followed from feeds and sitemaps.
- No catch-up email if the worker restarts after 08:00 ET.
- `issues` table is missing from the Parquet export.
- Sessions can only be revoked by rotating `SESSION_SECRET`; the README doesn't say so.
- `backup_db` creates an empty DB when the path is wrong.
- Sources page shows times in UTC, not ET.

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
