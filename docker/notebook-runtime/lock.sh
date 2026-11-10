#!/usr/bin/env bash
# Resolve requirements.in into a universal, hash-pinned requirements.lock.
set -euo pipefail
cd "$(dirname "$0")"
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes \
  --no-header --output-file requirements.lock
