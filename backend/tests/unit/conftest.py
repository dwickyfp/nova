"""Unit-suite configuration.

NOVA-108 made ``FERNET_KEY`` (and ``SECRET_KEY``) required: the app will not
build a cipher or sign a token without them. The unit suite never runs the
application lifespan, so it does not inherit the fixtures in ``tests/conftest``
— this sets the two keys once per session so tests that exercise encryption use
a real, fixed key instead of the removed per-process fallback.

Only the unit suite is affected; integration tests configure their own keys via
``tests/conftest.py``.
"""

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings

#: Fixed for the whole run so ciphertext written by one test is readable by the
#: next (a per-test key would hide the cross-process bug this issue is about).
UNIT_TEST_FERNET_KEY = Fernet.generate_key().decode()
UNIT_TEST_SECRET_KEY = "unit-test-signing-key-not-for-deployment"


@pytest.fixture(autouse=True, scope="session")
def _required_secrets():
    settings.FERNET_KEY = UNIT_TEST_FERNET_KEY
    settings.SECRET_KEY = UNIT_TEST_SECRET_KEY

    # Both crypto modules cache their Fernet; drop any instance built before the
    # override from an earlier import/collection order.
    from app.common import crypto as crypto_module

    crypto_module._fernet = None

    from app.core import security as security_module

    security_module._fernet = None
    yield
