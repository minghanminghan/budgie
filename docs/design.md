# Curated Personal Newsletter — Design

Status: draft, 2026-10-27 · Tracks Sparrow task `SPAR-18`; feed tickets go to project `BUDGIE`

## Intent

Replace the TLDR newsletter with a daily page of tech posts from sources the owner picks. TLDR has some good pieces, but most items are skipped because **the idea isn't interesting** or **it obviously isn't substantial**. This app fixes that by reading only from chosen sources and learning the owner's taste from thumbs up/down.

One user (the owner). Success = the ideas on the page are interesting. Failure = the page fills with uninteresting ideas and the owner goes back to TLDR.

## Goals

- A daily page of 15–20 fresh posts, ready by **7:00am US Eastern**.
- A one-line email at **8:00am US Eastern** linking to the page.
- Thumbs up/down on each post, saved in place (no page change).
- Add a source from a phone by pasting a URL (form or share sheet). Unsubscribe from the sources page.
- Rank posts with a model that learns from thumbs. **No LLMs anywhere.**
- Log feedback in a clean, reusable layout that can train other recommenders later.
- Ingest text sources of many kinds (blogs, Substacks, Medium, company eng blogs, forums) through an extensible adapter layer.

## Non-goals (v1)

- Story grouping / clustering. Each post stands alone.
- Discussion scores / points. Not fetched, not stored, not shown.
- Aggregators (engineering.fyi, engineeringblogs.xyz). See `TODO.md`.
- Email-only newsletters (IMAP inbox). Later.
- RSSHub, paid page-to-feed services, per-site custom scrapers.
- Source discovery / recommendations. See `TODO.md`.
- Audio, video, podcasts. Text only.
- Multiple users.

## Anti-goals

- Using an LLM for ranking, summarizing, or extraction.
- A feed that drifts into one narrow topic (wildcard slots guard against this).
- Filler: on a slow day the issue is shorter; it is not padded to 15.
- Strangers polluting the training data.

## Constraints

- Python, Dockerized, on the owner's Ubuntu server.
- The Ubuntu server sits on the owner's **Tailscale tailnet**. All networking uses Tailscale. The app is exposed publicly with **Tailscale Funnel** at the server's `https://<machine>.<tailnet>.ts.net` name (Funnel does not serve custom domains). The owner's Vercel/Next.js domain is not used for hosting; it is only the Resend sending domain.
- Email via **Resend**, already set up on the owner's domain.
- Embeddings: **`intfloat/e5-base-v2`**, run locally on CPU (768-dim, 512-token limit, document text must be prefixed with `passage: `).

## Architecture

`docker compose` with three containers sharing one data volume:

| Container | Job |
|---|---|
| `web` | FastAPI app: pages + API |
| `worker` | APScheduler: hourly ingest, issue build, email |
| `tailscale` | `tailscale/tailscale` sidecar; Funnel (HTTPS, public) to `web` via `TS_SERVE_CONFIG` |

Storage: one SQLite file (WAL mode) on the shared volume. SQLAlchemy models, Alembic migrations. The embedding model cache also lives on the volume.

Libraries: `feedparser`, `httpx`, `trafilatura` (main text, author, date, and sitemap reading), `sentence-transformers`, `scikit-learn`, `resend`.

## Ingest

### Adapter layer

Each source type is an adapter with two operations:

- `discover(url) -> SourceConfig | None` — can this adapter read this URL? If yes, return the config (feed URL, path prefix, title, etc.).
- `fetch(source) -> list[RawItem]` — return new raw posts since the last run. The adapter keeps its own per-source state (e.g. ETag/Last-Modified for RSS, seen-URL set for sitemaps).

Adapters are registered in a list and tried in order. New types (RSSHub, IMAP, aggregators) plug in without changing the pipeline. v1 ships two:

**RSS/Atom adapter.** `discover` tries, in order:
1. If the URL itself is a feed, use it.
2. `<link rel="alternate" type="application/rss+xml|atom+xml">` on the page.
3. Known patterns: Substack (`substack.com/@name` → `name.substack.com/feed`, which may redirect to a custom domain), Medium (`medium.com/<pub>` → `medium.com/feed/<pub>`).
4. Common paths on the URL path and on the site root: `/feed`, `/feed/`, `/rss`, `/rss.xml`, `/feed.xml`, `/atom.xml`, `/index.xml`.

A candidate counts only if it parses as a feed with ≥1 entry (engineering.fyi's feed is valid XML but empty).

**Sitemap adapter.** Used when no feed is found. `discover` reads `robots.txt` and `/sitemap.xml`, follows sitemap indexes, and uses the **pasted URL's path as the prefix filter** (e.g. `https://www.uber.com/us/en/blog/engineering` → keep URLs under `/us/en/blog/engineering/`). `fetch` diffs the current URL set against the seen set; new URLs become raw posts whose page is then fetched for title/author/date/text.

**Listing adapter.** Used when neither a feed nor the sitemap works. `discover` fetches the pasted URL (the blog index) and reads its post links: links in `nav`/`header`/`footer`/menus, to other hosts, to the index itself or its parents, to pagination, or to the same index in another locale are dropped; if any links sit in `<article>` cards, only those are kept. Each post's date comes from its card on the index (the post page is the fallback). `fetch` diffs the links against the seen set. It sees only the first page of the index, so it is for blogs that post less than a page between hourly runs. `sources.yaml` can pin an adapter (`{url, adapter}`) when an earlier adapter would match the wrong pages, and give the listing adapter a per-site `include` regex on link paths. URLs no adapter can read go to a `pending:` list with a Sparrow ticket (`newsletter ticket`; see the README).

### Fetching rules

- Send a normal browser `User-Agent`. Netflix, Lyft, Pinterest (Medium), and Uber return 403/406 to default clients.
- Obey `robots.txt`. One request at a time per domain, with a delay between requests.
- Prefer full text from the feed (`content:encoded`) when it is long enough; otherwise fetch the page and extract with `trafilatura`.
- Record `last_fetched_at` and `last_error` on each source. A failing source does not stop the run.

### Shared pipeline

Every raw post goes: **canonicalize URL → dedup → extract text → embed → store.**

**Canonicalize:** follow redirects; use the page's `<link rel="canonical">` if present; strip tracking params (`utm_*`, `ref`, `source`, etc.); lowercase host; drop `www.`; drop trailing `/`.

**Dedup:**
1. Canonical URL is the unique key on `items`.
2. Cross-posts (same article at two URLs, e.g. Medium + own domain): same normalized title + same author → same item.
3. A duplicate does not create a new item; it adds a row to `item_sources`.

**Embed:** `passage: <title>\n<text>`, truncated to 512 tokens. Store the model name with each vector so items can be re-embedded with another model later.

### First run of a new source

Store only the **newest 5** posts as items. Record the rest as seen (adapter state) so they are never ingested. This keeps day one from being flooded (Anthropic's sitemap alone has ~585 URLs).

## Data model

Append-only where possible. Tables:

- **`sources`** — `id, adapter (rss|sitemap|listing), input_url, config (JSON: feed_url / sitemap_url / path_prefix …), title, site_domain, state (JSON, adapter-owned), status (active|unsubscribed), created_at, unsubscribed_at, last_fetched_at, last_error`. Unsubscribe is soft; history is kept.
- **`items`** — `id, canonical_url (unique), url, title, author, site_domain, published_at (nullable), first_seen_at, text, word_count, dedup_key`.
- **`item_sources`** — `item_id, source_id, seen_at, raw_url`. Every source that delivered an item.
- **`embeddings`** — `item_id, model, dim, vector (blob), created_at`. PK `(item_id, model)`.
- **`issues`** — `id, issue_date (unique), built_at, status (ok|failed), ranker_version, error`.
- **`impressions`** — `id, issue_id, item_id, slot, is_wildcard, score, features (JSON snapshot of non-embedding features the ranker saw)`. Written when the issue is frozen.
- **`events`** — `id, type (click|thumb_up|thumb_down|thumb_clear|page_view), impression_id (nullable), item_id (nullable), issue_id (nullable), created_at, user_agent`. A thumb's current value = the latest thumb event for that item.
- **`ranker_models`** — `version, trained_at, n_labels, metrics (JSON), artifact_path`.

**Export:** a script writes `items`, `impressions`, `events` (and `embeddings`, `sources`) to Parquet for training other models.

## Ranking

**Candidates:** items not shown in any earlier issue, published (or first seen, if no date) within the last **7 days**.

**Features:** the e5 embedding (topic), source, author, `log(word_count)`, age in hours at build time.

**v1 ranker (rules only):** a simple score from source track record (smoothed thumbs ratio per source, neutral at start), a length score that favors substantial posts, and recency decay. Clicks are only a tiebreaker. v1 still computes and stores embeddings and logs all features, so the next phase can train on day-one data.

**Next phase — learned model (not in v1):** scikit-learn logistic regression on the features above. Labels: thumb up = 1, thumb down = 0. There is **no minimum-thumbs gate**; this phase is blocked only by v1 being built. Tracked in `TODO.md`. The ranker sits behind an interface (`score(candidates) -> scores`, plus a `ranker_version`) so the learned model drops in without other changes.

**Expected volume:** low at first; the owner will add many sources by hand early on. Short issues are accepted.

**Teasers / paywalls:** posts whose available text is a teaser (paid Substack, members-only Medium) get `is_teaser = true` on `items`. For these, the length score and `log(word_count)` are set to neutral, not low. No logged-in fetching in v1.

**Issue composition:**
- The ranker **orders**; it does not apply a quality cutoff in v1. The issue is the top 20 candidates, or all of them if there are fewer. "No filler" means no padding with old or already-shown posts.
- At most **3 posts per source** per issue.
- **2–3 wildcard slots**: picked at random from candidates outside the top picks, placed at random slots, **not labeled on the page**, flagged `is_wildcard` in `impressions`.

## Schedule (America/New_York, DST-aware)

| Time | Job |
|---|---|
| Hourly | Ingest all active sources |
| 6:30 | Build and freeze today's issue; write impressions |
| 7:00 | Page shows today's issue (until then, `/` shows the latest frozen issue) |
| 8:00 | Send email |

## Web

### Pages (public, no login)

- `GET /` — today's issue. Each post: title (links via `/c/…`), source, author, read time, 👍 👎 (buttons visible but inactive unless logged in). Article text is not shown.
- `GET /issue/{date}` — a past issue.
- `GET /archive` — list of past issues.
- `GET /sources` — list of sources; when logged in, also the paste form and an Unsubscribe button on each.
- `GET /login`, `POST /login`, `POST /logout`.

### Clicks

`GET /c/{token}` — token is an HMAC-signed impression id. Always redirects to the article. Logs a `click` event **only if the request carries the owner's session cookie**. Drop clicks that arrive within seconds of the issue going live, and from known link-scanner user-agents.

### API (owner only)

Auth: signed session cookie (from password login) or `Authorization: Bearer <API_TOKEN>` (for the phone share shortcut).

- `POST /api/feedback` — `{impression_id, value: up|down|clear}`. Called by page JS; no reload.
- `POST /api/sources/preview` — `{url}` → runs adapters' `discover`, returns the found config + the last 3 post titles, or "no feed found".
- `POST /api/sources` — `{url}` → discover and subscribe. The share shortcut calls this directly and shows the result text; no separate confirm step.
- `DELETE /api/sources/{id}` — soft unsubscribe.
- `GET /healthz`.

### Phone share shortcut

An iOS Shortcut (or Android "HTTP Shortcuts" app) on the share sheet: takes the shared URL, `POST`s it to `/api/sources` with the bearer token, shows the response. The repo includes setup instructions.

## Email

Resend, sent at 8:00am ET to the owner.

- Issue OK: subject like "Today's 18 posts are ready", body = one line + link to `/`.
- Issue failed: "Today's issue failed" + a short error.

## Config (env vars)

`BASE_URL`, `TS_AUTHKEY`, `APP_PASSWORD_HASH`, `SESSION_SECRET`, `LINK_SIGNING_SECRET`, `API_TOKEN`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_TO`, `TZ=America/New_York`.

## Deployment

- No DNS changes. The `tailscale` sidecar joins the tailnet with an auth key (`TS_AUTHKEY`) and Funnels `web` on 443. Funnel must be allowed for the node in the tailnet policy file. `BASE_URL` = the node's `ts.net` URL.
- `docker compose up -d` on the server. Volumes hold SQLite, model cache, and Tailscale state.
- Nightly SQLite backup (`.backup`), kept 7 days, on the same server. Off-server backup is in `TODO.md`.

## Initial sources

Seeded from a `sources.yaml` file. Feed URLs below were checked on 2026-10-27.

| Source | Adapter | URL |
|---|---|---|
| Pragmatic Engineer | rss | `https://newsletter.pragmaticengineer.com/feed` |
| Redwood Research | rss | `https://blog.redwoodresearch.org/feed` |
| Ramp Builders | rss | `https://builders.ramp.com/feed.xml` |
| Stripe | rss | `https://stripe.dev/blog/feed` (whole Stripe dev blog, not only engineering) |
| Netflix Tech Blog | rss | `https://netflixtechblog.com/feed` |
| Cloudflare | rss | `https://blog.cloudflare.com/rss/` |
| Tailscale | rss | `https://tailscale.com/blog/index.xml` |
| Slack Engineering | rss | `https://slack.engineering/feed/` |
| Spotify Engineering | rss | `https://engineering.atspotify.com/feed` |
| Pinterest Engineering | rss | `https://medium.com/feed/pinterest-engineering` |
| Lyft Engineering | rss | `https://eng.lyft.com/feed` |
| Anthropic Engineering | listing | `https://www.anthropic.com/engineering` (posts live under `/research/`, so the sitemap prefix finds none) |
| Goodfire | sitemap | `https://www.goodfire.com` prefix `/blog/` |
| Uber Engineering | listing | `https://www.uber.com/us/en/blog/engineering/` (the sitemap prefix finds only category pages) |

## Decided by the owner

Page + one-line email (not email-only). 7am page / 8am email ET. 15–20 posts max; short issues accepted. Rules-only ranker in v1; learned model is the next phase with no thumbs gate. Tailscale for all networking. Password auth; reads public, writes gated. No LLMs. No points. No story groups. Sitemap adapter as its own extensible layer. Aggregators, discovery, and ranker follow-ups deferred to `TODO.md`. `e5-base-v2` embeddings. Docker + Python on Ubuntu. Resend.

## Calls made by Claude (owner to check)

FastAPI + Jinja + plain JS; SQLite; APScheduler worker; Tailscale Funnel sidecar on the `ts.net` name (public reads need Funnel, and Funnel can't use a custom domain); logistic regression for the next phase; no ranker quality cutoff; teaser flag; 7-day candidate window; max 3 per source; unlabeled wildcards; impressions written at freeze time; clicks logged only when logged in; newest-5 backfill; shortcut subscribes without a confirm step; nightly backups.

## Left to the builder

Exact cold-start formula and length curve; scanner user-agent list and the "too soon" click window; politeness delays; tracking-param list; page styling; test approach (adapters should be testable against saved fixtures); project layout.
