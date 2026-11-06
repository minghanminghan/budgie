#!/usr/bin/env bash
# One-time (and safe to re-run) mini PC setup for budgie, the newsletter. Run as root from a repo checkout:
#   sudo [PORT=3419] [CADDY_SETUP=/opt/sparrow/current/deploy/caddy/setup.sh] deploy/setup.sh
# Creates the budgie and budgie-runner users and dirs, /etc/budgie/{paths,budgie}.env, budgie-web.service on
# 127.0.0.1:$PORT, budgie-worker.service and budgie-predeploy.service, the `budgie` CLI wrapper, lets
# budgie-runner deploy (see deploy/runner.sh), and serves https://budgie.<domain> to the tailnet with
# sparrow's Caddy script. Re-run it after the first deploy: the Caddy step needs the app answering.
set -euo pipefail

UNIT=/etc/systemd/system/budgie-web.service
DATA=/var/lib/budgie
BACKUPS=/var/backups/budgie
OPT=/opt/budgie
ETC=/etc/budgie
UNITS=(budgie-web.service budgie-worker.service budgie-predeploy.service)
CADDY_SETUP=${CADDY_SETUP:-/opt/sparrow/current/deploy/caddy/setup.sh}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

die() { echo "error: $*" >&2; exit 1; }
step() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run as root (sudo $0)"
for cmd in systemctl systemd-run ss tailscale install useradd visudo; do
  command -v "$cmd" >/dev/null || die "$cmd not found"
done
[[ -x $CADDY_SETUP ]] || die "no Caddy script at $CADDY_SETUP (deploy sparrow first, or set CADDY_SETUP)"

# Keep the port from a previous run unless PORT is given.
PORT=${PORT:-$(sed -n 's/^Environment=PORT=//p' "$UNIT" 2>/dev/null || true)}
PORT=${PORT:-3419}  # sparrow is on 3417, crow on 3418

# deploy/deploy.sh runs uv as budgie-runner; the venvs it builds must not point under a home dir (ProtectHome).
UV_BIN=$(command -v uv || true)
[[ -n $UV_BIN ]] || die "uv not found. Install it system-wide: curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh"
UV_BIN=$(readlink -f "$UV_BIN")
case $UV_BIN in /home/* | /root/*) die "uv at $UV_BIN is under a home dir. Install it system-wide (UV_INSTALL_DIR=/usr/local/bin)" ;; esac

step "user: budgie"
if ! id -u budgie >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin budgie
fi

step "user: budgie-runner"
if ! id -u budgie-runner >/dev/null 2>&1; then
  useradd --system --user-group --create-home --home-dir /home/budgie-runner --shell /bin/bash budgie-runner
fi
systemctl=$(command -v systemctl)
sudoers=$(printf '%s\n' "budgie-runner ALL=(root) NOPASSWD: $systemctl start budgie-predeploy, $systemctl restart budgie-web, $systemctl restart budgie-worker")
if [[ $(cat /etc/sudoers.d/budgie-runner 2>/dev/null) != "$sudoers" ]]; then
  printf '%s\n' "$sudoers" >/etc/sudoers.d/budgie-runner.tmp
  visudo -cqf /etc/sudoers.d/budgie-runner.tmp || die "bad sudoers rule"
  chmod 0440 /etc/sudoers.d/budgie-runner.tmp
  mv /etc/sudoers.d/budgie-runner.tmp /etc/sudoers.d/budgie-runner
fi

step "dirs: $DATA $BACKUPS $OPT/releases"
install -d -o budgie -g budgie -m 0750 "$DATA" "$DATA/hf" "$BACKUPS"
# releases, the uv-managed Python and the uv cache share one filesystem, so uv hardlinks between releases
install -d -o budgie-runner -g budgie-runner -m 0755 "$OPT" "$OPT/releases" "$OPT/python" "$OPT/uv-cache"

step "config: $ETC/paths.env $ETC/budgie.env"
install -d -m 0750 -g budgie "$ETC"
install -m 0644 "$HERE/paths.env" "$ETC/paths.env"
if [[ ! -f $ETC/budgie.env ]]; then
  install -m 0640 -g budgie "$HERE/../.env.example" "$ETC/budgie.env"
  echo "    wrote $ETC/budgie.env from .env.example; fill it in (see README)"
fi
grep -q REPLACE_ME "$ETC/budgie.env" && echo "warning: $ETC/budgie.env still has REPLACE_ME values" >&2

step "cli: /usr/local/bin/budgie"
install -m 0755 "$HERE/budgie.sh" /usr/local/bin/budgie

step "port: 127.0.0.1:$PORT"
pids=$(ss -Hltnp "sport = :$PORT" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)
for pid in $pids; do
  owner=$(ps -o user= -p "$pid" | tr -d ' ')
  [[ $owner == budgie ]] || die "port $PORT is used by pid $pid ($owner). Pick another: PORT=<port> $0"
done
if [[ -z $pids ]] && ss -Hltn "sport = :$PORT" | grep -q .; then
  die "port $PORT is in use. Pick another: PORT=<port> $0"
fi

step "systemd: ${UNITS[*]}"
changed=()
for unit in "${UNITS[@]}"; do
  rendered=$(sed -e "s|@PORT@|$PORT|" "$HERE/$unit")
  dest=/etc/systemd/system/$unit
  if [[ ! -f $dest ]] || [[ $(cat "$dest") != "$rendered" ]]; then
    printf '%s\n' "$rendered" >"$dest"
    changed+=("$unit")
  fi
done
[[ ${#changed[@]} -eq 0 ]] || systemctl daemon-reload
systemctl enable budgie-web budgie-worker >/dev/null
if [[ -e $OPT/current/.venv/bin/uvicorn ]]; then
  for svc in budgie-web budgie-worker; do
    if [[ " ${changed[*]} " == *" $svc.service "* ]] || ! systemctl is-active --quiet "$svc"; then systemctl restart "$svc"; fi
    systemctl is-active --quiet "$svc" || echo "warning: $svc did not start; see journalctl -u $svc" >&2
  done
else
  echo "    $OPT/current does not exist yet; the services start after the first deploy"
fi

URL=https://budgie.${DOMAIN:-andrewminghanjiang.com}
# sparrow's script ends with a health check of the site, which fails until a release is running.
if [[ -e $OPT/current/.venv/bin/uvicorn ]]; then
  "$CADDY_SETUP" budgie "127.0.0.1:$PORT"
else
  echo "==> caddy: skipped until the first deploy; then re-run: sudo $0"
fi

cat <<EOF

Done. budgie will be at $URL (tailnet only; local: http://127.0.0.1:$PORT).

Fill in $ETC/budgie.env if you have not: BASE_URL=$URL, the secrets and the Resend values.
  Password hash, on your laptop: uv run newsletter hash-password. The app does not start without these.

Deploys (CD): install the GitHub runner once, then each release on main deploys itself:
  sudo RUNNER_TOKEN=<token> REPO_URL=https://github.com/<owner>/<repo> deploy/runner.sh
  token: gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token -q .token
  First deploy: re-run the deploy job of the latest release workflow run.

After the first deploy, the worker has subscribed to sources.yaml. To fetch now instead of at the top of the hour:
  sudo budgie ingest

Backups: the worker writes one nightly into $BACKUPS (7 kept). Run one now: sudo budgie backup
EOF
