#!/usr/bin/env bash
# One-shot Superset setup: schema migrations, built-in roles, then Nova's
# roles and the single "Nova" database. Safe to run on every start.
set -euo pipefail

superset db upgrade
superset init
python -m nova_superset.bootstrap
