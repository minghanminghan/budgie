# Newsletter

A daily page of tech posts from sources you pick. The page is ready at 7:00 a.m. US Eastern. An email with links to it and to each post goes out at the same time. Thumbs up/down train the next ranker. No LLMs.

Spec: `docs/design.md`. Deferred work: `TODO.md`.

## How it runs

On the mini PC, the same way as sparrow (`SPAR-15`): systemd services run the current release in `/opt/budgie/current`.

- `budgie-web`: the FastAPI app (pages + API) on `127.0.0.1:3419` (sparrow uses 3417, crow 3418). Caddy serves it to the tailnet only at `https://budgie.andrewminghanjiang.com`, through hawk.
- `budgie-worker`: on start (so on each deploy), subscribes to new `sources.yaml` entries; hourly ingest + embed, 06:30 issue build, 07:00 email, 03:15 backup (7 kept, in `/var/backups/budgie`).
- `budgie-predeploy`: run by each deploy. It backs up the DB, then runs `alembic upgrade head`.

Data is in `/var/lib/budgie` (the SQLite DB and the embedding model). Secrets are in `/etc/budgie/budgie.env`.

Every push to `main` runs the tests. [hawk](https://github.com/minghanminghan/hawk)'s release workflow tags a version from the conventional commits, and its runner on the mini PC deploys it (`hawk deploy budgie`, which runs `deploy/build.sh`). Pull requests check the title and run the tests.

## Deploy (mini PC)

Deploys go through [hawk](https://github.com/minghanminghan/hawk), installed at `/opt/hawk`. Its registry entry is `apps/budgie.env`. You also need `uv` installed system-wide:
`curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh`.

1. **Install the runner** (one time):

   ```sh
   gh api -X POST repos/minghanminghan/budgie/actions/runners/registration-token -q .token   # on your laptop
   sudo hawk runner https://github.com/minghanminghan/budgie <token>
   ```

2. **Set up the mini PC.** From a checkout of this repo on the mini PC:

   ```sh
   sudo hawk setup budgie <checkout>
   ```

   It is safe to re-run. It makes the user, dirs, units and Caddy site, then runs `deploy/hawk-setup.sh` (`/etc/budgie/*.env`, the `budgie` CLI, the uv dirs).
3. **Fill in `/etc/budgie/budgie.env`** (`sudo -e /etc/budgie/budgie.env`). Make the password hash on your laptop with `uv run newsletter hash-password`. Put it in `APP_PASSWORD_HASH` **inside single quotes** (it contains `$`). Fill in the three random secrets and the Resend values. The app does not start without them.
4. **Deploy.** Merge a `fix:` or `feat:` PR to `main`.
5. **Sources.** The worker subscribes to `sources.yaml` when it starts, so each deploy adds new entries. It does not bring back a source you unsubscribed in the web UI: run `sudo budgie seed sources.yaml` for that. To fetch now instead of at the top of the hour: `sudo budgie ingest`.

6. Open `https://budgie.andrewminghanjiang.com` from a device on your tailnet, log in, and check `/sources`.

Rollback: `sudo hawk rollback budgie [release]`. If a migration changed the DB, restore a backup first (below).

Phone share-sheet setup: `docs/share-shortcut.md`.

## Commands

On the mini PC: `sudo budgie <command>` (the newsletter CLI as the `budgie` user, with the services' settings, from the current release). On your laptop: `uv run newsletter <command>`.

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

4. Merge it: the deploy seeds it (and updates the rule on an existing source). Check `/sources`, and close the task with `sparrow update BUDGIE-7 --status done`.

## Restore from a backup

Backups are in `/var/backups/budgie/newsletter-YYYY-MM-DD.sqlite`.

```sh
sudo systemctl stop budgie-web budgie-worker
ls /var/backups/budgie
sudo install -o budgie -g budgie -m 0640 /var/backups/budgie/newsletter-YYYY-MM-DD.sqlite /var/lib/budgie/newsletter.sqlite
sudo rm -f /var/lib/budgie/newsletter.sqlite-wal /var/lib/budgie/newsletter.sqlite-shm
sudo systemctl start budgie-web budgie-worker
```

## Develop

```sh
uv sync
uv run pytest                 # RUN_SLOW=1 also loads the real e5 model
DATABASE_URL=sqlite:///./dev.sqlite uv run alembic upgrade head
DATABASE_URL=sqlite:///./dev.sqlite SESSION_SECRET=dev LINK_SIGNING_SECRET=dev \
  uv run uvicorn newsletter.web.app:create_app --factory --reload
```
