#!/usr/bin/env bash
# One-time install of the GitHub Actions runner that runs the release workflow's deploy job.
# Run as root after deploy/setup.sh (which creates budgie-runner), with a fresh registration token:
#   gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token -q .token
#   sudo RUNNER_TOKEN=<token> REPO_URL=https://github.com/<owner>/<repo> deploy/runner.sh
# Runners are per repo, so this is a second runner next to sparrow's, with its own user and label.
set -euo pipefail

VERSION=2.338.2
SHA256=baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40
DIR=/home/budgie-runner/actions-runner

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo $0)"
: "${RUNNER_TOKEN:?set RUNNER_TOKEN}"
: "${REPO_URL:?set REPO_URL=https://github.com/<owner>/<repo>}"
id -u budgie-runner >/dev/null 2>&1 || die "run deploy/setup.sh first"
[[ -e $DIR/.runner ]] && { echo "runner already configured in $DIR"; exit 0; }

tarball=actions-runner-linux-x64-$VERSION.tar.gz
runuser -u budgie-runner -- mkdir -p "$DIR"
cd "$DIR"
runuser -u budgie-runner -- curl -fsSL -o "$tarball" "https://github.com/actions/runner/releases/download/v$VERSION/$tarball"
echo "$SHA256  $tarball" | sha256sum -c -
runuser -u budgie-runner -- tar -xzf "$tarball"
rm "$tarball"
runuser -u budgie-runner -- ./config.sh --unattended --url "$REPO_URL" --token "$RUNNER_TOKEN" \
  --name "$(hostname)-budgie" --labels budgie --no-default-labels --replace
./svc.sh install budgie-runner
./svc.sh start
