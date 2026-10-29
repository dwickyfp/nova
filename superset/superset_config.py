"""Superset configuration for Nova (docs/arch-17-superset.md).

Values come from the environment; secrets are never written here. Features
that would bypass Nova's security contract are configured off and are listed
with the rule that keeps them off.
"""

from __future__ import annotations

import os
from copy import deepcopy

import pymysql
from flask_appbuilder.security.manager import AUTH_REMOTE_USER

# Superset imports this module at the end of superset.config, so its defaults
# already exist and can be extended rather than copied.
from superset.config import TALISMAN_CONFIG as _DEFAULT_TALISMAN_CONFIG

from nova_superset.app import init_app
from nova_superset.connection import mutate_connection
from nova_superset.jinja import JINJA_CONTEXT_ADDONS as _NOVA_JINJA
from nova_superset.security import NovaSecurityManager


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


# Superset's MySQL-family engine specs read result column types from the
# ``MySQLdb`` module. PyMySQL is the only MySQL driver in this image and
# provides that module's interface.
pymysql.install_as_MySQLdb()

SECRET_KEY = _required("SUPERSET_SECRET_KEY")
SQLALCHEMY_DATABASE_URI = (
    "postgresql+psycopg2://"
    f"{os.environ.get('SUPERSET_DB_USER', 'superset')}:{_required('SUPERSET_DB_PASSWORD')}"
    f"@{os.environ.get('SUPERSET_DB_HOST', 'superset-db')}:5432/"
    f"{os.environ.get('SUPERSET_DB_NAME', 'superset')}"
)

# S1, S2: Nova signs users in on every request; Superset has no login form.
AUTH_TYPE = AUTH_REMOTE_USER
AUTH_USER_REGISTRATION = False
CUSTOM_SECURITY_MANAGER = NovaSecurityManager
FLASK_APP_MUTATOR = init_app

# S3: every connection to "Nova" carries the session's data token.
DB_CONNECTION_MUTATOR = mutate_connection
PREVENT_UNSAFE_DB_CONNECTIONS = True

JINJA_CONTEXT_ADDONS = dict(_NOVA_JINJA)

# Superset is served under /bi behind Nova's gateway or dev server.
ENABLE_PROXY_FIX = True
SESSION_COOKIE_NAME = "nova_bi_superset"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("SUPERSET_COOKIE_SECURE", "false").lower() == "true"

# S14: only Nova's own origin may frame Superset.
TALISMAN_ENABLED = True
TALISMAN_CONFIG = deepcopy(_DEFAULT_TALISMAN_CONFIG)
TALISMAN_CONFIG["content_security_policy"]["frame-ancestors"] = ["'self'"]
TALISMAN_CONFIG["frame_options"] = "SAMEORIGIN"
TALISMAN_CONFIG["session_cookie_secure"] = SESSION_COOKIE_SECURE

_REDIS = os.environ.get("SUPERSET_REDIS_URL", "redis://redis:6379")


def _redis_cache(db: int, prefix: str) -> dict:
    return {
        "CACHE_TYPE": "RedisCache",
        "CACHE_DEFAULT_TIMEOUT": 86400,
        "CACHE_KEY_PREFIX": prefix,
        "CACHE_REDIS_URL": f"{_REDIS}/{db}",
    }


CACHE_CONFIG = _redis_cache(3, "nova_bi_meta_")
FILTER_STATE_CACHE_CONFIG = _redis_cache(3, "nova_bi_filter_")
EXPLORE_FORM_DATA_CACHE_CONFIG = _redis_cache(3, "nova_bi_form_")
# S9: query results are not cached until cache keys carry the active role and
# security-context version (Phase 2 patch).
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}

FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    # S10: async execution runs without the interactive Nova session.
    "GLOBAL_ASYNC_QUERIES": False,
    # S5: background executors need delegated tokens (Phase 2).
    "ALERT_REPORTS": False,
    "THUMBNAILS": False,
    # S14: Nova users are signed in directly; guest tokens are not used.
    "EMBEDDED_SUPERSET": False,
    # S9: username-only partitioning is not enough; caching is off instead.
    "CACHE_IMPERSONATION": False,
}

SUPERSET_WEBSERVER_TIMEOUT = 300
SQLLAB_TIMEOUT = 300
