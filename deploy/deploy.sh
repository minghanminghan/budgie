#!/usr/bin/env bash
# Deploys a checkout to the mini PC. Run by the release workflow's deploy job as budgie-runner:
#   VERSION=1.2.3 deploy/deploy.sh
# Builds into /opt/budgie/releases/<version>-<run>, backs up and migrates the DB, switches /opt/budgie/current,
# restarts budgie-web and budgie-worker, and checks /api/version. Keeps the newest 3 releases.
# Rollback: sudo ln -sfn <old release> /opt/budgie/current && sudo systemctl restart budgie-web budgie-worker
# (if a migration changed the DB, first restore a /var/backups/budgie copy with both services stopped).
set -euo pipefail

OPT=/opt/budgie
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${VERSION:?set VERSION}"
R=$OPT/releases/$VERSION-${GITHUB_RUN_ID:-$(date +%s)}
# Keep uv's Python and cache out of the runner's home: the services run with ProtectHome.
export UV_PYTHON_INSTALL_DIR=$OPT/python UV_CACHE_DIR=$OPT/uv-cache UV_COMPILE_BYTECODE=1

step() { echo "==> $*"; }

step "build: $R"
mkdir -p "$R"
git -C "$SRC" archive HEAD | tar -x -C "$R"
cd "$R"
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
uv sync --frozen --no-dev --no-editable

step "backup + migrate"
ln -sfn "$R" $OPT/next
sudo systemctl start budgie-predeploy

step "switch + restart"
ln -sfn "$R" $OPT/current
sudo systemctl restart budgie-web
sudo systemctl restart budgie-worker

step "health check"
port=$(systemctl show -P Environment budgie-web | tr ' ' '\n' | sed -n 's/^PORT=//p')
for _ in $(seq 1 30); do
  got=$(curl -fsS "http://127.0.0.1:$port/api/version" 2>/dev/null \
    | "$R/.venv/bin/python" -c 'import json, sys; print(json.load(sys.stdin)["version"])' 2>/dev/null || true)
  [[ $got == "$VERSION" ]] && break
  sleep 1
done
[[ $got == "$VERSION" ]] || { echo "error: /api/version is '$got', expected $VERSION. See journalctl -u budgie-web" >&2; exit 1; }
systemctl is-active --quiet budgie-worker || { echo "error: budgie-worker is not running. See journalctl -u budgie-worker" >&2; exit 1; }

step "prune releases"
cd $OPT/releases
ls -1t | tail -n +4 | while read -r old; do
  [[ $OPT/releases/$old == "$(readlink -f $OPT/current)" ]] || rm -rf "$old"
done
echo "deployed $VERSION"
