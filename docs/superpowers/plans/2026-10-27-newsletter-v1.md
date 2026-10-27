# Curated Personal Newsletter v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-user, self-hosted daily tech-reading page (7:00 ET) plus a one-line email (8:00 ET), fed by owner-chosen RSS/sitemap sources, ranked by rules, with clean feedback logging for future learned rankers.

**Architecture:** One Python package (`newsletter`) used by two processes: a FastAPI `web` app (public pages, owner-only API) and an APScheduler `worker` (hourly ingest + embed, 6:30 issue build, 8:00 email, nightly backup). Both share one SQLite file. A `tailscale` sidecar exposes `web` publicly with Funnel. Ingest is an adapter layer (RSS, Sitemap) feeding one shared pipeline.

**Tech Stack:** Python 3.12, uv, FastAPI + Jinja2 + plain JS, SQLAlchemy 2 + Alembic, httpx, feedparser, trafilatura, sentence-transformers (`intfloat/e5-base-v2`, CPU torch), itsdangerous, resend, APScheduler 3.x, pyarrow, PyYAML, pytest. Docker Compose + `tailscale/tailscale`.

**Spec:** `docs/design.md` (deferred work: `TODO.md`)

## Global Constraints

- No LLM calls anywhere (ranking, extraction, summarizing).
- No discussion scores/points: not fetched, stored, or shown.
- Time zone for every schedule and "today" computation: `America/New_York` (DST-aware).
- Embedding model: `intfloat/e5-base-v2`; input `passage: <title>\n<text>`; `max_seq_length = 512`; store model name with every vector.
- HTTP: browser `User-Agent`, obey `robots.txt`, one request at a time per domain with a delay (default 1.0 s).
- First run of a new source stores only the newest **5** posts; the rest are marked seen.
- Issue: at most **20** posts, at most **3 per source**, **2–3** unlabeled wildcard slots (only when there are more than 20 candidates), candidate window **7 days**, no padding.
- Reads are public; every write (thumbs, subscribe, unsubscribe) requires the owner (session cookie or `Authorization: Bearer <API_TOKEN>`).
- Clicks and page views are logged only for the owner.
- Backups: nightly, keep **7**.
- All datetimes stored as UTC; returned timezone-aware.

## Review Focus

1. Feed entries with missing, naive, or future dates → naive treated as UTC, future clamped to `now`, missing falls back to `first_seen_at` for the 7-day window. (Task 6: `test_published_at_normalization`)
2. Same article via Medium `?source=rss-…`, `utm_*`, or `www.` variants → one item. (Task 2: `test_canonicalize_medium_rss_param`; Task 6: `test_duplicate_via_two_sources_links_item`)
3. One source times out or serves HTML instead of a feed → `last_error` set on that source, other sources still ingest. (Task 6: `test_ingest_all_isolates_failures`)
4. Late evening ET and the November DST change → issue date and visible issue use ET, not UTC. (Task 8: `test_issue_date_for_dst_end`; Task 10: `test_index_before_go_live_shows_previous_issue`)
5. Build re-run for the same date (worker restart, manual CLI) → no duplicate issue or impressions; a failed issue is replaced by a retry. (Task 8: `test_build_issue_idempotent`, `test_build_issue_retries_failed`)

---

## File Structure

```
pyproject.toml  alembic.ini  .gitignore  .env.example  sources.yaml
Dockerfile  docker-compose.yml  tailscale/serve.json  README.md  docs/share-shortcut.md
migrations/env.py  migrations/versions/0001_initial.py
src/newsletter/
  config.py        Settings (env vars)
  db.py            engine/session factory, SQLite pragmas
  models.py        ORM tables, UTCDateTime type
  clock.py         ET helpers: issue_date_for, go_live_at
  http.py          PoliteClient (UA, robots, per-domain throttle)
  urls.py          canonicalize_url, normalize_title, dedup_key
  ingest/base.py   RawItem, SourceConfig, FetchResult, Adapter, discover_source, default_adapters
  ingest/extract.py  extract_page, looks_like_teaser, word_count
  ingest/rss.py    RssAdapter
  ingest/sitemap.py  SitemapAdapter, parse_sitemap
  ingest/pipeline.py store_raw_item, ingest_source, ingest_all
  ingest/sources.py  preview, subscribe, unsubscribe, seed_from_yaml
  embed.py         Embedder, passage_text, embed_pending
  rank/base.py     Candidate, Ranker
  rank/rules.py    RulesRanker
  rank/issue.py    get_candidates, compose_issue, build_issue, visible_issue
  feedback.py      record_thumb, current_thumbs, record_click, record_page_view
  auth.py          passwords, session tokens, link tokens, LoginLimiter, is_owner
  emailer.py       Sender, ResendSender, compose_daily_email, send_daily_email
  web/app.py  web/pages.py  web/api.py  web/templates/*.html  web/static/app.js
  worker.py        build_scheduler, job functions, main
  backup.py        backup_db
  export.py        export_parquet
  cli.py           `newsletter` command
tests/conftest.py  tests/fixtures/*  tests/test_*.py
```

Shared test fixtures in `tests/conftest.py` (created in Task 1, extended as needed): `session` (in-memory SQLite, `Base.metadata.create_all`), `now` (`datetime(2026, 10, 27, 10, 30, tzinfo=UTC)`), `make_source(**kw)`, `make_item(source, **kw)`.

---

### Task 1: Project scaffold, config, database models, migration

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py`, `src/newsletter/__init__.py`, `src/newsletter/config.py`, `src/newsletter/db.py`, `src/newsletter/models.py`, `tests/conftest.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings, env vars): `database_url: str = "sqlite:////data/newsletter.sqlite"`, `base_url`, `app_password_hash`, `session_secret`, `link_signing_secret`, `api_token`, `resend_api_key`, `email_from`, `email_to`, `embed_model: str = "intfloat/e5-base-v2"`, `backup_dir: str = "/data/backups"`. `get_settings() -> Settings` (cached).
  - `make_engine(url: str) -> Engine` (sets `journal_mode=WAL` for file DBs, `foreign_keys=ON`), `make_sessionmaker(engine) -> sessionmaker[Session]`.
  - `models.Base` and ORM classes, columns exactly as `docs/design.md` "Data model", plus:
    - `Source.config_key: str` (unique) — `"rss:<feed_url>"` or `"sitemap:<root><prefix>"`.
    - `Item.is_teaser: bool = False`; index on `Item.dedup_key`.
    - `ItemSource` PK `(item_id, source_id)`.
    - `Impression` unique `(issue_id, item_id)` and `(issue_id, slot)`.
    - Classes: `Source, Item, ItemSource, Embedding, Issue, Impression, Event, RankerModel`.
  - `UTCDateTime` TypeDecorator: stores naive UTC, returns aware UTC; used for every datetime column.

- [ ] **Step 1: `git init`, write `pyproject.toml`** (package `newsletter` under `src/`, script `newsletter = "newsletter.cli:main"`, deps from Tech Stack, dev deps `pytest`, `ruff`; torch from the PyTorch CPU index via `[tool.uv.sources]`). `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `*.sqlite*`, `data/`.

- [ ] **Step 2: Write failing tests** in `tests/test_models.py`:

```python
def test_datetime_roundtrip_is_aware_utc(session, make_source):
    src = make_source()
    session.commit(); session.expire_all()
    assert session.get(Source, src.id).created_at.tzinfo is not None

def test_canonical_url_unique(session, make_source, make_item):
    s = make_source()
    make_item(s, canonical_url="https://a.com/x")
    with pytest.raises(IntegrityError):
        make_item(s, canonical_url="https://a.com/x"); session.flush()

def test_alembic_upgrade_creates_all_tables(tmp_path):
    # run `alembic upgrade head` against sqlite:///tmp_path/t.sqlite
    assert set(inspect(engine).get_table_names()) >= {
        "sources", "items", "item_sources", "embeddings", "issues",
        "impressions", "events", "ranker_models"}

def test_file_db_uses_wal(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/w.sqlite")
    assert engine.connect().exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
```

- [ ] **Step 3: Run** `uv run pytest tests/test_models.py -v` — Expected: FAIL (imports missing).
- [ ] **Step 4: Implement** config, db, models, conftest fixtures; generate `0001_initial.py` with `alembic revision --autogenerate` and review it. `migrations/env.py` reads `Settings().database_url` unless `-x url=` / `sqlalchemy.url` is overridden.
- [ ] **Step 5: Run** `uv run pytest tests/test_models.py -v` — Expected: 4 passed.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: scaffold project, models, initial migration"` (includes existing `docs/`, `TODO.md`).

---

### Task 2: URL canonicalization and dedup keys

**Files:**
- Create: `src/newsletter/urls.py`
- Test: `tests/test_urls.py`

**Interfaces:**
- Produces:
  - `canonicalize_url(url: str, canonical_link: str | None = None) -> str`
  - `normalize_title(text: str) -> str`
  - `dedup_key(title: str, author: str | None) -> str | None`
  - `TRACKING_PARAMS: frozenset[str]` = `{"ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid"}` plus any key starting with `utm_`.

- [ ] **Step 1: Write failing tests:**

```python
def test_canonicalize_basic():
    assert canonicalize_url("https://www.Example.com/Post/?utm_source=x&id=3#frag") == "https://example.com/Post?id=3"

def test_canonicalize_medium_rss_param():
    assert canonicalize_url("https://medium.com/pinterest-engineering/a-post-abc123?source=rss----4c5a5f6279b6---4") \
        == "https://medium.com/pinterest-engineering/a-post-abc123"

def test_canonical_link_wins_and_is_resolved():
    assert canonicalize_url("https://x.com/c?utm_medium=1", "/a/b/") == "https://x.com/a/b"

def test_root_url():
    assert canonicalize_url("https://www.x.com/") == "https://x.com"

def test_dedup_key():
    assert dedup_key("Hello,  World!", "Jane Doe") == "hello world|jane doe"
    assert dedup_key("Hello", None) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_urls.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** with `urllib.parse`. Lowercase scheme and host, drop a leading `www.`, drop the fragment, drop tracking params (keep other params in order), strip trailing `/` from the path. Path case is kept. `normalize_title`: lowercase, remove punctuation, collapse whitespace.
- [ ] **Step 4: Run** — Expected: 5 passed.
- [ ] **Step 5: Commit** — `git commit -am "feat: url canonicalization and dedup keys"` (add new files).

---

### Task 3: Polite HTTP client

**Files:**
- Create: `src/newsletter/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Produces:
  - `BROWSER_UA: str` (a current desktop Chrome UA string).
  - `class RobotsDisallowed(Exception)`.
  - `class PoliteClient(transport: httpx.BaseTransport | None = None, min_interval: float = 1.0, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic, timeout: float = 20.0)` with `get(url: str, headers: dict | None = None) -> httpx.Response` (follows redirects, sends `BROWSER_UA` and a browser `Accept` header), and `close()`.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport` and a fake clock/sleep:
  - `test_sends_browser_user_agent` — handler asserts `request.headers["user-agent"] == BROWSER_UA`.
  - `test_robots_disallow_raises` — `robots.txt` = `User-agent: *\nDisallow: /private` → `get("https://a.com/private/x")` raises `RobotsDisallowed`.
  - `test_robots_404_allows_all` — `robots.txt` 404 → `get` returns 200.
  - `test_same_host_is_throttled` — two gets to `a.com` with the fake clock frozen → `sleep` called once with `1.0`.
  - `test_other_host_not_throttled` — `a.com` then `b.com` → `sleep` never called.
  - `test_robots_fetched_once_per_host` — 3 gets to `a.com` → the robots handler hit once.
- [ ] **Step 2: Run** `uv run pytest tests/test_http.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** with `urllib.robotparser.RobotFileParser.parse(lines)` cached per host. Robots is checked for user-agent `*`. A robots fetch error or 4xx means allow all.
- [ ] **Step 4: Run** — Expected: 6 passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: polite http client"`.

---

### Task 4: Adapter base and RSS adapter

**Files:**
- Create: `src/newsletter/ingest/__init__.py`, `src/newsletter/ingest/base.py`, `src/newsletter/ingest/extract.py`, `src/newsletter/ingest/rss.py`
- Test: `tests/test_rss.py`, `tests/test_extract.py`, fixtures `tests/fixtures/rss_full.xml` (content:encoded ≥200 words), `tests/fixtures/rss_summary.xml` (short summaries), `tests/fixtures/empty_feed.xml` (valid, 0 items), `tests/fixtures/blog_home.html` (has `<link rel="alternate" type="application/rss+xml" href="/feed.xml">`), `tests/fixtures/article.html`, `tests/fixtures/substack_paywalled.html`

**Interfaces:**
- Consumes: `PoliteClient.get` (Task 3).
- Produces (`ingest/base.py`):

```python
FIRST_RUN_LIMIT = 5

@dataclass
class RawItem:
    url: str; title: str; author: str | None; published_at: datetime | None
    text: str; canonical_link: str | None = None; is_teaser: bool = False

@dataclass
class SourceConfig:
    adapter: str; config_key: str; config: dict; title: str
    site_domain: str; preview_titles: list[str]

@dataclass
class FetchResult:
    items: list[RawItem]; state: dict

class Adapter(Protocol):
    name: str
    def discover(self, url: str, client: PoliteClient) -> SourceConfig | None: ...
    def fetch(self, config: dict, state: dict, client: PoliteClient) -> FetchResult: ...

def default_adapters() -> list[Adapter]          # [RssAdapter(), SitemapAdapter()]
def discover_source(url: str, client: PoliteClient, adapters: list[Adapter]) -> SourceConfig | None
```

  An empty `state` (`{}`) means first run. On first run the adapter returns at most `FIRST_RUN_LIMIT` newest items, and its returned state marks every listed entry as seen.
- Produces (`ingest/extract.py`):
  - `word_count(text: str) -> int`
  - `@dataclass Extracted(title, author, published_at, text, canonical_link, is_teaser)`
  - `extract_page(html: str, url: str) -> Extracted` — uses `trafilatura.bare_extraction(..., with_metadata=True)`; `canonical_link` comes from `<link rel="canonical">`.
  - `TEASER_MARKERS = ('"audience":"only_paid"', 'class="paywall', "Member-only story")`
  - `looks_like_teaser(html: str, text: str) -> bool` — True when any marker is present and `word_count(text) < 600`.
  - `fetch_and_extract(url: str, client: PoliteClient) -> tuple[str, Extracted]` — returns the final URL after redirects.
- Produces (`ingest/rss.py`): `class RssAdapter` (`name = "rss"`). `FULL_TEXT_MIN_WORDS = 200`. Config is `{"feed_url": ...}`. State is `{"etag", "modified", "seen": list[str]}`, with `seen` capped to the last 1000 entry ids.

- [ ] **Step 1: Write failing tests** (`MockTransport` serving fixtures):
  - `test_discover_url_is_feed` — URL serves `rss_full.xml` → `config == {"feed_url": url}` and `config_key == "rss:" + url`.
  - `test_discover_link_alternate` — home page `blog_home.html` → `feed_url == "https://blog.example.com/feed.xml"`.
  - `test_discover_substack_profile` — `https://substack.com/@pragmaticengineer` → tries `https://pragmaticengineer.substack.com/feed`; the final redirect target is stored as `feed_url`.
  - `test_discover_medium_pub` — `https://medium.com/pinterest-engineering` → `https://medium.com/feed/pinterest-engineering`.
  - `test_discover_common_path_from_article_url` — article URL with no alternate link; `https://site.com/feed` serves a feed → found.
  - `test_discover_rejects_empty_feed` — the only candidate is `empty_feed.xml` → `None`.
  - `test_discover_preview_titles` — `preview_titles` = the first 3 entry titles.
  - `test_fetch_first_run_limit` — a feed with 8 entries, `state={}` → 5 items (newest), and `len(result.state["seen"]) == 8`.
  - `test_fetch_skips_seen_and_sends_etag` — second fetch with the prior state → 0 items, and the request carries `If-None-Match`.
  - `test_fetch_304_returns_no_items`.
  - `test_fetch_uses_feed_full_text` — `rss_full.xml` → no page fetch made, and `text` has ≥200 words.
  - `test_fetch_short_summary_fetches_page` — `rss_summary.xml` → the page is fetched, and `text` comes from `article.html`.
  - `test_extract_marks_substack_teaser` (`test_extract.py`) — `substack_paywalled.html` → `is_teaser is True`; `article.html` → `False`.
- [ ] **Step 2: Run** `uv run pytest tests/test_rss.py tests/test_extract.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** `discover` tries the steps in the order given in `docs/design.md` "RSS/Atom adapter" and stops at the first candidate that parses with ≥1 entry (`feedparser.parse(response.content)`). Feed HTML is turned into text with `trafilatura.html2txt`. Entry id = `entry.id`, else `entry.link`. Dates come from `published_parsed`, else `updated_parsed`, as UTC.
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: adapter base and rss adapter"`.

---

### Task 5: Sitemap adapter

**Files:**
- Create: `src/newsletter/ingest/sitemap.py`
- Test: `tests/test_sitemap.py`, fixtures `tests/fixtures/sitemap_index.xml`, `tests/fixtures/sitemap_posts.xml` (8 `/engineering/` URLs with `<lastmod>`, plus `/engineering`, `/careers/x`, and `https://other.com/engineering/y`), `tests/fixtures/sitemap_posts.xml.gz`

**Interfaces:**
- Consumes: `PoliteClient`, `fetch_and_extract`, `RawItem`, `SourceConfig`, `FetchResult`, `FIRST_RUN_LIMIT`.
- Produces:
  - `parse_sitemap(data: bytes) -> tuple[list[str], list[tuple[str, datetime | None]]]` — (child sitemap URLs, page URLs with lastmod). Handles gzip by magic bytes.
  - `class SitemapAdapter` (`name = "sitemap"`). Config is `{"site": "https://www.anthropic.com", "sitemaps": [...], "prefix": "/engineering/"}`. `config_key = "sitemap:" + site + prefix`. `title = "<domain><prefix without trailing slash>"`, e.g. `"anthropic.com/engineering"`. State is `{"seen": list[str]}`.

- [ ] **Step 1: Write failing tests:**
  - `test_parse_sitemap_index_and_urlset` — index → 2 children; urlset → pairs with aware lastmod.
  - `test_parse_gzip` — the `.xml.gz` fixture parses the same as the plain one.
  - `test_discover_uses_path_prefix` — `https://www.anthropic.com/engineering` with robots `Sitemap:` → index → posts: `config["prefix"] == "/engineering/"`; only the 8 post URLs count (no listing page, no `/careers/`, no other host).
  - `test_discover_root_path_returns_none` — `https://www.anthropic.com/` → `None` (no whole-site subscriptions).
  - `test_discover_no_matching_urls_returns_none`.
  - `test_discover_falls_back_to_sitemap_xml` — robots has no `Sitemap:` line → `/sitemap.xml` is used.
  - `test_fetch_first_run_newest_5_by_lastmod` — 5 items with the newest lastmods; state `seen` has all 8; exactly 5 page fetches.
  - `test_fetch_new_url_only` — prior state with 8 seen plus a new URL in the sitemap → 1 item.
  - `test_fetch_page_error_not_marked_seen` — the page returns 500 → no item, and the URL is absent from `seen`.
  - `test_published_at_falls_back_to_lastmod` — the page has no date → `published_at` = the lastmod.
- [ ] **Step 2: Run** `uv run pytest tests/test_sitemap.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** with `xml.etree.ElementTree` (namespace-agnostic tag match). Index recursion goes to depth 3 at most. Host matching ignores `www.`. Sort order is lastmod desc, with missing lastmod last. Plan call: our own parser, not trafilatura's sitemap helper, so all requests go through `PoliteClient` (robots + throttle).
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: sitemap adapter"`.

---

### Task 6: Ingest pipeline and source management

**Files:**
- Create: `src/newsletter/ingest/pipeline.py`, `src/newsletter/ingest/sources.py`
- Test: `tests/test_pipeline.py`, `tests/test_sources.py`

**Interfaces:**
- Consumes: models (Task 1), `canonicalize_url`, `dedup_key` (Task 2), `Adapter`, `RawItem`, `discover_source` (Task 4), `word_count`.
- Produces:
  - `store_raw_item(session, source: Source, raw: RawItem, now: datetime) -> Item` — canonicalizes the URL and dedups by `canonical_url`, then by `dedup_key` (only when the author is present). It creates a new `Item` or reuses one, and always ensures an `ItemSource(item, source)` row. `published_at`: naive → UTC; later than `now` → `now`.
  - `ingest_source(session, source: Source, client, adapters: dict[str, Adapter], now) -> int` — the number of new items. It sets `state`, `last_fetched_at`, and `last_error=None`.
  - `ingest_all(session, client, adapters: dict[str, Adapter], now) -> dict[int, int | str]` — source id → new count, or an error string. Only active sources are read. An exception sets `source.last_error` (truncated to 500 chars) and the run continues. Commits per source.
  - `preview(url, client, adapters: list[Adapter]) -> SourceConfig | None`
  - `subscribe(session, url, client, adapters: list[Adapter], now) -> Source | None` — an existing `config_key` returns that source and sets it active again. The first ingest happens on the next hourly run (plan call).
  - `unsubscribe(session, source_id: int, now) -> bool` — soft (`status="unsubscribed"`, `unsubscribed_at=now`).
  - `seed_from_yaml(session, path: Path, client, adapters, now) -> list[tuple[str, Source | None]]` — YAML is `sources: [<url>, ...]`.

- [ ] **Step 1: Write failing tests** (fake adapters returning canned `FetchResult`s; no network):
  - `test_store_new_item_sets_fields` — `word_count`, `is_teaser`, `site_domain`, and `first_seen_at == now`.
  - `test_duplicate_via_two_sources_links_item` — the same article via `https://www.a.com/p?utm_source=x` (source A) and `https://a.com/p` (source B) → 1 item, 2 `item_sources` rows.
  - `test_cross_post_dedup_by_title_author` — different URLs with the same title and author → 1 item.
  - `test_no_author_no_title_dedup` — same title, no author, different URLs → 2 items.
  - `test_published_at_normalization` — naive → UTC aware; `now + 1 day` → `now`; `None` stays `None`.
  - `test_ingest_source_updates_state_and_timestamps`.
  - `test_ingest_all_isolates_failures` — adapter raises `httpx.ReadTimeout` for source A → `A.last_error` is set; B ingests its items; the result maps A → str and B → int.
  - `test_ingest_all_skips_unsubscribed`.
  - `test_subscribe_idempotent_and_reactivates` — subscribe, unsubscribe, subscribe the same URL → same id, `status == "active"`.
  - `test_subscribe_no_feed_returns_none`.
  - `test_seed_from_yaml` — 2 URLs, one undiscoverable → `[(url1, Source), (url2, None)]`.
- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py tests/test_sources.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: ingest pipeline and source management"`.

---

### Task 7: Embeddings

**Files:**
- Create: `src/newsletter/embed.py`
- Test: `tests/test_embed.py`

**Interfaces:**
- Consumes: `Item`, `Embedding`.
- Produces:
  - `passage_text(title: str, text: str) -> str` — `f"passage: {title}\n{text}"`.
  - `class Embedder(model_name: str, model=None)` — lazily loads `SentenceTransformer(model_name, device="cpu")` and sets `max_seq_length = 512`. `embed(texts: list[str]) -> np.ndarray` (float32, L2-normalized, shape `(n, dim)`).
  - `embed_pending(session, embedder: Embedder, batch_size: int = 32) -> int` — embeds items that have no `Embedding` row for `embedder.model_name`. Stores `vector = arr.astype("<f4").tobytes()` and `dim`. Returns the count.

- [ ] **Step 1: Write failing tests** with a fake model (an object with `encode(texts, normalize_embeddings=True) -> np.ndarray` of dim 4):
  - `test_passage_text_prefix` — `passage_text("T", "body") == "passage: T\nbody"`.
  - `test_embed_pending_only_missing` — 3 items, 1 already embedded → returns 2; the fake model saw texts starting with `"passage: "`.
  - `test_vector_roundtrip` — `np.frombuffer(row.vector, "<f4")` equals the fake output; `row.model == "intfloat/e5-base-v2"`.
  - `test_real_model_dim` — marked `@pytest.mark.slow` and skipped unless `RUN_SLOW=1`: the real model gives dim 768.
- [ ] **Step 2: Run** `uv run pytest tests/test_embed.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Register the `slow` marker in `pyproject.toml`.
- [ ] **Step 4: Run** — Expected: 3 passed, 1 skipped.
- [ ] **Step 5: Commit** — `git commit -m "feat: e5 embeddings"`.

---

### Task 8: Clock, rules ranker, issue builder

**Files:**
- Create: `src/newsletter/clock.py`, `src/newsletter/rank/__init__.py`, `src/newsletter/rank/base.py`, `src/newsletter/rank/rules.py`, `src/newsletter/rank/issue.py`
- Test: `tests/test_clock.py`, `tests/test_rules.py`, `tests/test_issue.py`

**Interfaces:**
- Consumes: models; `Event` thumb types (`thumb_up|thumb_down|thumb_clear`, latest per item wins).
- Produces:
  - `clock.ET = ZoneInfo("America/New_York")`, `issue_date_for(now: datetime) -> date`, `go_live_at(d: date) -> datetime` (07:00 ET, aware).
  - `rank/base.py`:

```python
@dataclass
class Candidate:
    item_id: int; source_id: int; author: str | None; word_count: int
    is_teaser: bool; age_hours: float; source_ups: int; source_downs: int; clicks: int
    def features(self) -> dict: ...   # every field except item_id

class Ranker(Protocol):
    version: str
    def score(self, candidates: list[Candidate]) -> list[float]: ...
```

  - `rank/rules.py`: `class RulesRanker` (`version = "rules-v1"`). Source term = `(ups + 1) / (ups + downs + 2)`. Length term favors substantial posts and is neutral when `is_teaser`. Recency decays with `age_hours`. Exact curve is the builder's call.
  - `rank/issue.py`:
    - `@dataclass Pick(item_id: int, slot: int, is_wildcard: bool, score: float, features: dict)`
    - `get_candidates(session, now) -> list[Candidate]` — items with no impression ever; `ref = published_at or first_seen_at`, and `ref >= now - 7 days`; at least one active source. `source_id` = the earliest `item_sources` row with an active source. Ups and downs are the current thumbs on that source's items. `clicks` = the owner click count.
    - `compose_issue(cands, scores, rng: random.Random, max_posts=20, per_source_cap=3) -> list[Pick]` — order by `(score desc, clicks desc)` and apply the cap. If ≤ `max_posts` pass, take all, with no wildcards. Otherwise `k = rng.choice([2, 3])`: take the top `max_posts - k`, sample `k` wildcards from the remaining cap-passing candidates, and insert each at `rng.randint(0, len(list))`. Slots are 1-based and contiguous.
    - `build_issue(session, issue_date: date, ranker: Ranker, rng, now) -> Issue` — an existing `ok` issue for the date is returned unchanged. An existing `failed` issue is deleted, then rebuilt. On success it writes `Issue(status="ok", ranker_version)` and one `Impression` per pick (`score` and `features`). On any exception it rolls back and stores `Issue(status="failed", error=str(e)[:500])`. It never raises.
    - `visible_issue(session, now) -> Issue | None` — the latest `ok` issue with `go_live_at(issue_date) <= now`.

- [ ] **Step 1: Write failing tests:**

```python
def test_issue_date_for_dst_end():
    assert issue_date_for(datetime(2026, 11, 1, 3, 30, tzinfo=UTC)) == date(2026, 10, 31)

def test_go_live_at_handles_dst():
    assert go_live_at(date(2026, 10, 30)).astimezone(UTC).hour == 11   # EDT
    assert go_live_at(date(2026, 11, 2)).astimezone(UTC).hour == 12    # EST
```

  `test_rules.py`:
  - `test_better_source_ranks_higher` — the same candidate with source `(5 up, 0 down)` beats `(0, 5)`.
  - `test_substantial_beats_tiny` — `word_count=1500` beats `120`.
  - `test_newer_beats_older` — `age_hours=2` beats `100`.
  - `test_teaser_length_neutral` — a teaser with 150 words scores the same length term as a teaser with 3000 words.

  `test_issue.py`:
  - `test_candidates_exclude_shown_old_and_unsubscribed` — previously shown, 8 days old, and unsubscribed-only items are excluded; an item with no `published_at` and `first_seen_at` 1 day ago is included.
  - `test_compose_small_pool_takes_all_no_wildcards` — 5 candidates → 5 picks, none wildcard, slots `1..5`.
  - `test_compose_per_source_cap` — 6 from one source and 2 from another → 5 picks.
  - `test_compose_large_pool_has_wildcards` — 40 candidates, `random.Random(1)` → 20 picks, 2–3 wildcards, all wildcards from outside the top `20 - k`, slots `1..20` unique.
  - `test_build_issue_writes_impressions` — impressions carry `features` with keys `{"source_id", "author", "word_count", "is_teaser", "age_hours", "source_ups", "source_downs", "clicks"}`.
  - `test_build_issue_idempotent` — two builds for the same date → 1 issue, same impression count.
  - `test_build_issue_failure_recorded` — a ranker that raises → `status == "failed"` and `error` is set.
  - `test_build_issue_retries_failed` — after a failure, a build with a good ranker → `status == "ok"`, and still one issue row for the date.
  - `test_build_issue_empty_pool_ok` — 0 candidates → `status == "ok"`, 0 impressions.
  - `test_visible_issue_before_and_after_go_live` — issues for 10-26 and 10-27; at 10-27 06:45 ET → the 10-26 issue; at 07:00 ET → the 10-27 issue.
- [ ] **Step 2: Run** `uv run pytest tests/test_clock.py tests/test_rules.py tests/test_issue.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: rules ranker and issue builder"`.

---

### Task 9: Auth and feedback recording

**Files:**
- Create: `src/newsletter/auth.py`, `src/newsletter/feedback.py`
- Test: `tests/test_auth.py`, `tests/test_feedback.py`

**Interfaces:**
- Consumes: models, `go_live_at`.
- Produces (`auth.py`):
  - `hash_password(pw: str) -> str` — `"scrypt$<salt_hex>$<hash_hex>"` (`hashlib.scrypt`, n=2**14, r=8, p=1, 16-byte salt). `verify_password(pw: str, stored: str) -> bool` (constant-time).
  - `SESSION_COOKIE = "nl_session"`, `SESSION_MAX_AGE = 180 * 24 * 3600`. `make_session_token(secret: str) -> str`, `check_session_token(token: str | None, secret: str) -> bool` (itsdangerous `TimestampSigner`, max age enforced).
  - `sign_link(impression_id: int, secret: str) -> str`, `unsign_link(token: str, secret: str) -> int | None`.
  - `class LoginLimiter(max_failures: int = 10, window: timedelta = timedelta(hours=1))` with `allowed(now) -> bool` and `record_failure(now)`. Plan call: it guards the public password form.
  - `is_owner(cookie: str | None, authorization: str | None, settings: Settings) -> bool` — a valid session cookie, or `Bearer <api_token>` (compared with `hmac.compare_digest`).
- Produces (`feedback.py`):
  - `record_thumb(session, impression_id: int, value: Literal["up", "down", "clear"], user_agent: str | None, now) -> str | None` — writes `Event(type="thumb_<value>", impression_id, item_id, issue_id)` and returns the new current value. Raises `LookupError` for an unknown impression and `ValueError` for a bad value.
  - `current_thumbs(session, item_ids: list[int]) -> dict[int, str]` — `"up"`/`"down"`; cleared items are omitted.
  - `SCANNER_UA_MARKERS = ("bot", "crawler", "spider", "preview", "safelinks", "proofpoint", "mimecast", "barracuda")`, `CLICK_GRACE = timedelta(seconds=30)`.
  - `record_click(session, impression_id: int, user_agent: str | None, now, owner: bool) -> Item | None` — returns the target item (`None` if unknown). Logs a `click` event only when `owner`, the UA is not a scanner, and `now >= go_live_at(issue_date) + CLICK_GRACE`.
  - `record_page_view(session, issue_id: int, user_agent: str | None, now) -> None` (the caller ensures the owner).

- [ ] **Step 1: Write failing tests:**
  - `test_password_roundtrip` — the right password verifies; the wrong one fails; two hashes of the same password differ.
  - `test_session_token_valid_and_expired` — use itsdangerous' `max_age` with a forged old timestamp, or patch time.
  - `test_link_token_tamper_rejected` — `unsign_link(sign_link(7, s)[:-1] + "x", s) is None`.
  - `test_login_limiter_blocks_after_10` — 10 failures → `allowed` is False; 61 min later → True.
  - `test_is_owner_cookie_or_bearer` — cookie only → True; `Bearer <api_token>` → True; wrong bearer → False; none → False.
  - `test_thumb_latest_wins` — up, down → `"down"`; then clear → omitted from `current_thumbs`.
  - `test_thumb_unknown_impression_raises`.
  - `test_click_logged_only_for_owner` — `owner=False` → 0 events, item still returned.
  - `test_click_scanner_ua_ignored` — UA `"Mozilla/5.0 (compatible; Proofpoint)"` → 0 events.
  - `test_click_within_grace_ignored` — `now = go_live + 10s` → 0 events; `+ 31s` → 1 event.
- [ ] **Step 2: Run** `uv run pytest tests/test_auth.py tests/test_feedback.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: auth and feedback recording"`.

---

### Task 10: Web app — pages, API, templates, JS

**Files:**
- Create: `src/newsletter/web/__init__.py`, `web/app.py`, `web/pages.py`, `web/api.py`, `web/templates/{base,issue,archive,sources,login}.html`, `web/static/app.js`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `visible_issue`, `current_thumbs`, `record_thumb`, `record_click`, `record_page_view`, auth helpers, `preview`, `subscribe`, `unsubscribe`, `PoliteClient`, `default_adapters`.
- Produces: `create_app(settings: Settings | None = None, session_factory=None, client: PoliteClient | None = None, adapters: list[Adapter] | None = None, now: Callable[[], datetime] | None = None) -> FastAPI` (stored on `app.state` for test injection). The run command is `uvicorn newsletter.web.app:create_app --factory`.
- Routes (exact): as listed in `docs/design.md` "Web". Additional rules:
  - Every `POST`/`DELETE` under `/api` requires the owner (401 otherwise) and `Content-Type: application/json` (415 otherwise; this is the CSRF guard, together with the `SameSite=Lax` cookie).
  - `POST /login` (form field `password`): on success it sets the cookie (`HttpOnly`, `Secure`, `SameSite=Lax`, max age `SESSION_MAX_AGE`) and returns 303 to `/`. A wrong password returns 401. When `LoginLimiter` blocks, it returns 429.
  - `GET /c/{token}`: a bad token returns 404. Otherwise `record_click(...)` runs and the route returns 302 to `item.url`.
  - Issue pages log `record_page_view` only for the owner.
  - Each post row shows the title (linked to `/c/<sign_link(impression.id)>`), the source title, the author, read time `max(1, ceil(word_count / 230))` min, and 👍/👎 buttons (`disabled` unless owner; `aria-pressed` reflects the current thumb). `app.js` POSTs `{impression_id, value}` and toggles to `clear` when the pressed button is clicked again.
  - `GET /sources`: lists active sources (title, domain, adapter, last fetched). The owner also sees `last_error`, the paste form (`app.js` → preview → Subscribe button → `POST /api/sources`), and Unsubscribe buttons.
  - `POST /api/sources/preview` → `{"found": true, "adapter", "title", "site_domain", "preview_titles"}` or `{"found": false, "message": "No feed found"}`. `POST /api/sources` → 201 with `{"id", "title", "message": "Subscribed to <title>"}`, or 422 with `{"message": "No feed found"}`. The text response is what the share shortcut shows.
  - `GET /healthz` → `{"ok": true}`.

- [ ] **Step 1: Write failing tests** (`fastapi.testclient.TestClient`, in-memory DB, fake adapters, fixed `now`):
  - `test_index_shows_visible_issue_titles`.
  - `test_index_before_go_live_shows_previous_issue` — `now` = 10-27 06:45 ET → the 10-26 titles.
  - `test_index_no_issue_yet` — 200 with a "No issue yet" message.
  - `test_thumb_buttons_disabled_when_logged_out` / `..._enabled_when_logged_in`.
  - `test_feedback_requires_owner` (401), `test_feedback_requires_json` (415), `test_feedback_ok` (200, `{"value": "up"}`).
  - `test_bearer_token_can_subscribe` — 201 and the source exists.
  - `test_subscribe_no_feed_422`.
  - `test_unsubscribe` — `DELETE /api/sources/{id}` → 200; the source is no longer listed on `/sources`.
  - `test_click_redirects_and_logs_only_owner` — anonymous → 302 and 0 events; owner → 302 and 1 event.
  - `test_click_bad_token_404`.
  - `test_login_flow` — wrong → 401; right → 303 with a `nl_session` cookie; the 11th wrong attempt → 429.
  - `test_issue_by_date_and_archive` — `/issue/2026-10-26` 200, `/issue/2020-01-01` 404; `/archive` lists dates newest first.
  - `test_sources_form_only_for_owner`.
  - `test_healthz`.
- [ ] **Step 2: Run** `uv run pytest tests/test_web.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Pages are server-rendered Jinja. Styling is the builder's call: readable on a phone, no CSS framework.
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Manual check:** run `uv run uvicorn newsletter.web.app:create_app --factory` with a temp `DATABASE_URL` and test env, insert a few fake items and call `build_issue` from `uv run python` (the CLI comes in Task 12), open `/`, `/sources`, and `/login` in a browser at phone width, and confirm the thumbs toggle without a reload.
- [ ] **Step 6: Commit** — `git commit -m "feat: web pages and api"`.

---

### Task 11: Daily email

**Files:**
- Create: `src/newsletter/emailer.py`
- Test: `tests/test_emailer.py`

**Interfaces:**
- Consumes: `Issue`, `Impression`, `Settings`.
- Produces:
  - `class Sender(Protocol): def send(self, to: str, subject: str, text: str) -> None`
  - `class ResendSender(api_key: str, from_addr: str)` — `resend.Emails.send({"from": from_addr, "to": [to], "subject": subject, "text": text})`.
  - `compose_daily_email(issue: Issue | None, n_posts: int, base_url: str) -> tuple[str, str]`
  - `send_daily_email(session, today: date, sender: Sender, settings: Settings) -> str` — returns the subject.

- [ ] **Step 1: Write failing tests** (fake sender records calls):

```python
@pytest.mark.parametrize("n,subject", [
    (18, "Today's 18 posts are ready"),
    (1, "Today's 1 post is ready"),
    (0, "No new posts today"),
])
def test_ok_subjects(n, subject): ...
    # body == f"{subject}: {base_url}/"

def test_failed_issue_email():   # subject "Today's issue failed", body contains issue.error
def test_missing_issue_email():  # issue None → "Today's issue failed", body contains "no issue was built"
def test_send_daily_email_uses_today_issue(session, ...):  # sender called once with settings.email_to
```

- [ ] **Step 2: Run** `uv run pytest tests/test_emailer.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Plan call: the subject for a 0-post OK issue is "No new posts today".
- [ ] **Step 4: Run** — Expected: all passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: daily email"`.

---

### Task 12: Worker, CLI, backup, export

**Files:**
- Create: `src/newsletter/worker.py`, `src/newsletter/cli.py`, `src/newsletter/backup.py`, `src/newsletter/export.py`
- Test: `tests/test_worker.py`, `tests/test_backup.py`, `tests/test_export.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `ingest_all`, `embed_pending`, `build_issue`, `RulesRanker`, `issue_date_for`, `send_daily_email`, `ResendSender`, `seed_from_yaml`, `hash_password`.
- Produces:
  - `backup_db(db_path: Path, backup_dir: Path, today: date, keep: int = 7) -> Path` — `sqlite3` `.backup()` to `newsletter-YYYY-MM-DD.sqlite`, then deletes the oldest files beyond `keep`.
  - `export_parquet(engine, out_dir: Path) -> list[Path]` — one file each for `items`, `impressions`, `events`, `embeddings`, `sources`. JSON columns become strings; `embeddings.vector` becomes `list<float32>`.
  - `worker.py`: job functions `ingest_job()`, `build_job()`, `email_job()`, `backup_job()`. Each opens its own session, logs exceptions, and never raises. `build_scheduler(scheduler=None) -> BlockingScheduler` with `CronTrigger(timezone=ET)`: ingest+embed at `minute=0` hourly, build `06:30`, email `08:00`, backup `03:15`. `main()` starts it.
  - `cli.py` `main(argv=None)`: subcommands `seed PATH`, `ingest`, `embed`, `build [--date YYYY-MM-DD]`, `email`, `backup`, `export OUT_DIR`, `hash-password` (reads with `getpass`, prints the hash).

- [ ] **Step 1: Write failing tests:**
  - `test_backup_keeps_7` — 9 runs over 9 dates → 7 files remain, and the newest is present; the backup opens and has the `items` table.
  - `test_export_roundtrip` — 2 items and 1 embedding (dim 4) → `pyarrow.parquet.read_table` row counts match, and the vector length is 4.
  - `test_scheduler_jobs_and_times` — `build_scheduler(BackgroundScheduler())` has 4 jobs; the build trigger's next fire time from `2026-10-27 06:00 ET` is `06:30 ET`; the email's is `08:00 ET`.
  - `test_job_swallows_errors` — patch `ingest_all` to raise → `ingest_job()` returns normally.
  - `test_cli_build_date` — `main(["build", "--date", "2026-10-27"])` with `DATABASE_URL` set to a temp DB → an issue row for that date.
- [ ] **Step 2: Run** `uv run pytest tests/test_worker.py tests/test_backup.py tests/test_export.py tests/test_cli.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole suite with `uv run pytest -v` — Expected: all passed (slow skipped).
- [ ] **Step 5: Commit** — `git commit -m "feat: worker, cli, backup, export"`.

---

### Task 13: Docker, Tailscale Funnel, seed sources, docs

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `tailscale/serve.json`, `.env.example`, `sources.yaml`, `README.md`, `docs/share-shortcut.md`

**Interfaces:**
- Consumes: `newsletter` CLI, `create_app`, `worker.main`.
- Produces:
  - `Dockerfile`: `python:3.12-slim`, `uv sync --frozen --no-dev` (CPU torch), `HF_HOME=/data/hf`.
  - `docker-compose.yml`: services `web` (`alembic upgrade head && uvicorn newsletter.web.app:create_app --factory --host 0.0.0.0 --port 8000`, healthcheck `/healthz`), `worker` (`python -m newsletter.worker`, `depends_on: web: condition: service_healthy`), and `tailscale` (`tailscale/tailscale`, `TS_AUTHKEY`, `TS_HOSTNAME=newsletter`, `TS_STATE_DIR=/var/lib/tailscale`, `TS_SERVE_CONFIG=/config/serve.json`, `TS_USERSPACE=false`, `/dev/net/tun`, `cap_add: [net_admin]`). Volumes: `data:/data` (web + worker), `ts-state:/var/lib/tailscale`. All services `env_file: .env`, `TZ=America/New_York`.
  - `tailscale/serve.json`:

```json
{
  "TCP": {"443": {"HTTPS": true}},
  "Web": {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {"Proxy": "http://web:8000"}}}},
  "AllowFunnel": {"${TS_CERT_DOMAIN}:443": true}
}
```

  - `.env.example`: every var in `docs/design.md` "Config", with `DATABASE_URL=sqlite:////data/newsletter.sqlite`.
  - `sources.yaml`: `sources:` listing the 11 RSS feed URLs from `docs/design.md` "Initial sources", plus `https://www.anthropic.com/engineering`, `https://www.goodfire.com/blog`, `https://www.uber.com/us/en/blog/engineering`.
  - `README.md`: deploy steps — Tailscale auth key, allowing Funnel in the tailnet policy (`nodeAttrs` `funnel`), `cp .env.example .env`, `newsletter hash-password`, `docker compose up -d`, `docker compose exec web newsletter seed sources.yaml`, setting `BASE_URL` to the node's `https://newsletter.<tailnet>.ts.net`, and restore-from-backup steps.
  - `docs/share-shortcut.md`: an iOS Shortcut (Receive URLs from Share Sheet → Get Contents of URL: POST `<BASE_URL>/api/sources`, JSON `{"url": Shortcut Input}`, header `Authorization: Bearer <API_TOKEN>` → Show Result `message`) and the Android "HTTP Shortcuts" equivalent.

- [ ] **Step 1: Write the files above.**
- [ ] **Step 2: Verify compose:** `docker compose config -q` — Expected: no output, exit 0. (If Docker is not installed locally, record that and run this on the server.)
- [ ] **Step 3: Verify image:** `docker compose build web` — Expected: success.
- [ ] **Step 4: Live seed smoke test** (real network, temp DB): `DATABASE_URL=sqlite:///$(mktemp -d)/s.sqlite uv run alembic upgrade head && uv run newsletter seed sources.yaml && uv run newsletter ingest` — Expected: every RSS source subscribes and ingests ≤5 items. Record which sitemap sources fail (Uber may block). Report the failures to the owner; do not add per-site workarounds.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: docker, tailscale funnel, seed sources, docs"`.
