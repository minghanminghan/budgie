#!/usr/bin/env bash
# Installed to /usr/local/bin/budgie by deploy/setup.sh. Runs the newsletter CLI as the budgie user with
# the services' environment, from the current release (so `seed sources.yaml` reads the deployed file):
#   sudo budgie seed sources.yaml
#   sudo budgie backup
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "error: run as root (sudo budgie $*)" >&2; exit 1; }
exec systemd-run --quiet --pty --wait --collect --uid=budgie --gid=budgie \
  -p WorkingDirectory=/opt/budgie/current \
  -p EnvironmentFile=/etc/budgie/paths.env -p EnvironmentFile=/etc/budgie/budgie.env \
  /opt/budgie/current/.venv/bin/newsletter "$@"
