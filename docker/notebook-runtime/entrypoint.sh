#!/bin/sh
# Starts the notebook's Jupyter server. OpenSandbox's execd (injected by the
# server) drives kernels through it at $JUPYTER_HOST with $JUPYTER_TOKEN, both
# set per sandbox by Nova. The server listens on loopback only.
set -eu
: "${JUPYTER_TOKEN:?JUPYTER_TOKEN is required}"
mkdir -p /workspace /tmp/nova
cd /workspace
exec python -m jupyter_server \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port="${JUPYTER_PORT:-8888}" \
  --IdentityProvider.token="${JUPYTER_TOKEN}" \
  --ServerApp.open_browser=False \
  --ServerApp.root_dir=/workspace \
  --ServerApp.allow_remote_access=False \
  --no-browser
