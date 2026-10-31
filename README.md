# Newsletter

A daily page of tech posts from sources you pick. The page is ready at 7:00 a.m. US Eastern. A one-line email links to it at 8:00 a.m. Thumbs up/down train the next ranker. No LLMs.

Spec: `docs/design.md`. Deferred work: `TODO.md`.

## How it runs

`docker compose` starts three containers that share one volume (`data:/data`):

- `web`: the FastAPI app (pages + API). It runs `alembic upgrade head` on start.
- `worker`: hourly ingest + embed, 06:30 issue build, 08:00 email, 03:15 backup (7 kept).
- `tailscale`: joins your tailnet and serves `web` to the public internet with Funnel at `https://newsletter.<tailnet>.ts.net`. No DNS changes.

## Deploy (Ubuntu server on your tailnet)

1. **Make a Tailscale auth key.** Admin console → Settings → Keys → Generate auth key. Reusable is fine. Copy it.
2. **Allow Funnel for the node.** In the tailnet policy file, add a `funnel` node attribute, for example:

   ```json
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ]
   ```

   Also make sure MagicDNS and HTTPS certificates are on (Admin console → DNS).
3. **Configure.**

   ```sh
   cp .env.example .env
   docker compose build
   docker compose run --rm --no-deps web newsletter hash-password
   ```

   Put the printed hash in `APP_PASSWORD_HASH` **inside single quotes** (it contains `$`). Fill in `TS_AUTHKEY`, the three random secrets, and the Resend values.
4. **Start.**

   ```sh
   docker compose up -d
   docker compose logs -f tailscale   # wait for the node to come up
   ```

5. **Set `BASE_URL`.** The node's name is `https://newsletter.<tailnet>.ts.net` (see the Tailscale admin console). Put it in `.env` as `BASE_URL`, then run `docker compose up -d` again.
6. **Seed sources.**

   ```sh
   docker compose exec web newsletter seed sources.yaml
   docker compose exec web newsletter ingest     # optional: fetch now instead of at the top of the hour
   ```

7. Open `BASE_URL`, log in, and check `/sources`.

Phone share-sheet setup: `docs/share-shortcut.md`.

## Commands

Run inside a container: `docker compose exec web newsletter <command>`.

| Command | What it does |
|---|---|
| `seed PATH` | Subscribe to every URL in a YAML file (`sources: [url, ...]`; an entry can also be `{url, adapter, include, ticket}`) |
| `ingest` | Fetch new posts from all active sources |
| `embed` | Embed posts that have no vector yet |
| `build [--date YYYY-MM-DD]` | Build an issue (safe to re-run; replaces a failed one) |
| `email` | Send today's email now |
| `backup` | Write a backup now |
| `export OUT_DIR` | Write `items`, `impressions`, `events`, `embeddings`, `sources` to Parquet |
| `hash-password` | Print a password hash for `APP_PASSWORD_HASH` |
| `ticket URL... [--parent ID]` | Run on your laptop (`uv run newsletter ticket URL`). For each URL no adapter can read, file a Sparrow task with a probe report (in project `BUDGIE` by default; `--project`, `--parent BUDGIE-N`) and add the URL to `pending:` in `sources.yaml` |

## When no feed is found

1. Run `uv run newsletter ticket URL` on your laptop (it needs the `sparrow` CLI; set `SPARROW_BIN` if it is not on `PATH`). Use the URLs from `seed`'s `FAILED` lines or from a "no feed found" in the web UI.
2. The Sparrow task lists what each adapter found and the page's own links. The URL moves to `pending:` in `sources.yaml`, which `seed` skips.
3. Find a regex that only post links match. Move the entry to `sources:` with the rule, and keep the ticket:

   ```yaml
   - url: https://example.com/blog
     adapter: listing
     include: ^/blog/\d{4}/   # regex on the link path
     ticket: BUDGIE-7
   ```

4. Re-run `seed` (it updates the rule on an existing source), check `/sources`, and close the task with `sparrow update BUDGIE-7 --status done`.

## Restore from a backup

Backups are in the `data` volume at `/data/backups/newsletter-YYYY-MM-DD.sqlite`.

```sh
docker compose stop web worker
docker compose run --rm --no-deps web sh -c \
  'ls /data/backups && cp /data/backups/newsletter-YYYY-MM-DD.sqlite /data/newsletter.sqlite && rm -f /data/newsletter.sqlite-wal /data/newsletter.sqlite-shm'
docker compose start web worker
```

## Develop

```sh
uv sync
uv run pytest                 # RUN_SLOW=1 also loads the real e5 model
DATABASE_URL=sqlite:///./dev.sqlite uv run alembic upgrade head
DATABASE_URL=sqlite:///./dev.sqlite SESSION_SECRET=dev LINK_SIGNING_SECRET=dev \
  uv run uvicorn newsletter.web.app:create_app --factory --reload
```
