#!/bin/sh
set -eu

# Local compose secrets are generated at container start and never written to
# the image or repository. Production deployments must inject stable values.
if [ -z "${SECRET_KEY:-}" ]; then
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
  export SECRET_KEY
fi
if [ -z "${FERNET_KEY:-}" ]; then
  FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  export FERNET_KEY
fi

exec "$@"
