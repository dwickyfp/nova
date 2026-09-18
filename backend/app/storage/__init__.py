"""Storage abstraction layer — stage storage providers and secret providers.

This package holds the parts of storage access that are *not* the SQL dialect:
currently the secret-reference layer (``novacore.storage.secrets``), which lets
a storage connection name a credential in an external secret store instead of
carrying the value in ``nova.yaml``.

Credentials themselves never live here — only *references* to them and the
adapters that resolve a reference into a value at the moment it is needed.
See ``app.storage.secrets`` for the resolution rules and the fail-closed
contract.
"""

from app.storage.secrets import (
    AwsSecretsManagerProvider,
    SecretProvider,
    SecretResolutionError,
    resolve_secret_reference,
)

__all__ = [
    "AwsSecretsManagerProvider",
    "SecretProvider",
    "SecretResolutionError",
    "resolve_secret_reference",
]
