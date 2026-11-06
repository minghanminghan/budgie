#!/usr/bin/env bash
# Run as root by `hawk setup budgie <checkout>`, after it makes $DATA and $OPT.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ETC=/etc/budgie

die() { echo "error: $*" >&2; exit 1; }

# hawk deploy runs uv as hawk-runner; the venvs it builds must not point under a home dir (ProtectHome).
uv_bin=$(command -v uv || true)
[[ -n $uv_bin ]] || die "uv not found. Install it system-wide: curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh"
uv_bin=$(readlink -f "$uv_bin")
case $uv_bin in /home/* | /root/*) die "uv at $uv_bin is under a home dir. Install it system-wide (UV_INSTALL_DIR=/usr/local/bin)" ;; esac

install -d -o budgie -g budgie -m 0750 "$DATA/hf"
install -d -o hawk-runner -g hawk-runner -m 0755 "$OPT/python" "$OPT/uv-cache"

install -d -m 0750 -g budgie "$ETC"
install -m 0644 "$HERE/paths.env" "$ETC/paths.env"
if [[ ! -f $ETC/budgie.env ]]; then
  install -m 0640 -g budgie "$HERE/../.env.example" "$ETC/budgie.env"
  echo "    wrote $ETC/budgie.env from .env.example; fill it in (see README)"
fi
if grep -q REPLACE_ME "$ETC/budgie.env"; then echo "warning: $ETC/budgie.env still has REPLACE_ME values" >&2; fi

install -m 0755 "$HERE/budgie.sh" /usr/local/bin/budgie
