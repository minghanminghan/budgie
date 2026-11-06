#!/usr/bin/env bash
# Run by `hawk deploy` in the new release dir, with VERSION and OPT set.
set -euo pipefail
# Keep uv's Python and cache out of the runner's home: the services run with ProtectHome.
# releases, python and uv-cache share one filesystem, so uv hardlinks between releases.
export UV_PYTHON_INSTALL_DIR=$OPT/python UV_CACHE_DIR=$OPT/uv-cache UV_COMPILE_BYTECODE=1
sed -i "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
uv sync --frozen --no-dev --no-editable
