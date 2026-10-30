"""Superset configuration for Nova (docs/arch-17-superset.md).

Values come from the environment; secrets are never written here. Features
that would bypass Nova's security contract are configured off and are listed
with the rule that keeps them off.
"""

from __future__ import annotations

import os
from copy import deepcopy

import nova_superset.celery_hooks  # noqa: F401 - registers the session carry
import pymysql
import redis
from cachelib.redis import RedisCache
from celery.schedules import crontab
from flask_appbuilder.security.manager import AUTH_REMOTE_USER
from nova_superset.app import init_app
from nova_superset.connection import mutate_connection
from nova_superset.events import NovaEventLogger
from nova_superset.jinja import JINJA_CONTEXT_ADDONS as _NOVA_JINJA
from nova_superset.security import NovaSecurityManager
from nova_superset.thumbnails import chart_digest, dashboard_digest

# Superset imports this module at the end of superset.config, so its defaults
# already exist and can be extended rather than copied.
from superset.config import TALISMAN_CONFIG as _DEFAULT_TALISMAN_CONFIG
from superset.config import THEME_DARK as _DEFAULT_THEME_DARK
from superset.config import THEME_DEFAULT as _DEFAULT_THEME


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

# S12: governed actions are also written to Nova's audit log.
EVENT_LOGGER = NovaEventLogger()

# Nova's look (DESIGN.md): brick-red primary, Inter, the console's radius and
# dark surface. Nova sets the light or dark mode of a framed Superset.
APP_NAME = "Nova BI"
_NOVA_TOKENS = {
    "colorPrimary": "#d04738",
    "colorLink": "#d04738",
    "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "borderRadius": 10,
    "brandAppName": "Nova BI",
}
THEME_DEFAULT = deepcopy(_DEFAULT_THEME)
THEME_DEFAULT.setdefault("token", {}).update(_NOVA_TOKENS)
THEME_DARK = deepcopy(_DEFAULT_THEME_DARK) if _DEFAULT_THEME_DARK else None
if THEME_DARK is not None:
    THEME_DARK.setdefault("token", {}).update({**_NOVA_TOKENS, "colorBgBase": "#0b0f14"})

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
# S9: query results are cached per Nova principal, active role and context
# version (``NovaSecurityManager.get_rls_cache_key``).
DATA_CACHE_CONFIG = _redis_cache(4, "nova_bi_data_")
THUMBNAIL_CACHE_CONFIG = _redis_cache(4, "nova_bi_thumb_")
THUMBNAIL_CHART_DIGEST_FUNC = chart_digest
THUMBNAIL_DASHBOARD_DIGEST_FUNC = dashboard_digest

# Background work (S5, S10): Celery on Redis, async SQL Lab results in Redis.
RESULTS_BACKEND = RedisCache(
    host=redis.Redis.from_url(f"{_REDIS}/5"), key_prefix="nova_bi_results_"
)


class CeleryConfig:
    broker_url = f"{_REDIS}/6"
    result_backend = f"{_REDIS}/6"
    imports = (
        "superset.sql_lab",
        "superset.tasks.scheduler",
        "superset.tasks.thumbnails",
        "superset.tasks.cache",
    )
    worker_prefetch_multiplier = 1
    task_acks_late = False
    beat_schedule = {
        "reports.scheduler": {"task": "reports.scheduler", "schedule": crontab(minute="*")},
        "reports.prune_log": {"task": "reports.prune_log", "schedule": crontab(minute=0, hour=0)},
    }


CELERY_CONFIG = CeleryConfig

# Scheduled work runs as the owner (S5); thumbnails as the requesting session.
WEBDRIVER_BASEURL = os.environ.get("SUPERSET_WEBDRIVER_BASEURL", "http://superset:8088/bi/")
WEBDRIVER_BASEURL_USER_FRIENDLY = os.environ.get("NOVA_PUBLIC_BI_URL", "http://localhost:5173/bi/")
MACHINE_AUTH_PROVIDER_CLASS = "nova_superset.machine_auth.NovaMachineAuthProvider"
# Without a mail server, reports and alerts run and are logged but not sent.
SMTP_HOST = os.environ.get("SUPERSET_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SUPERSET_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SUPERSET_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SUPERSET_SMTP_PASSWORD", "")
SMTP_MAIL_FROM = os.environ.get("SUPERSET_SMTP_MAIL_FROM", "nova-bi@localhost")
ALERT_REPORTS_NOTIFICATION_DRY_RUN = not SMTP_HOST

FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ALERT_REPORTS": True,
    "THUMBNAILS": True,
    "PLAYWRIGHT_REPORTS_AND_THUMBNAILS": True,
    # Chart loads stay synchronous: each runs in the viewer's own request.
    "GLOBAL_ASYNC_QUERIES": False,
    # S14: Nova users are signed in directly; guest tokens are not used.
    "EMBEDDED_SUPERSET": False,
    # The Nova partition in get_rls_cache_key already covers the principal.
    "CACHE_IMPERSONATION": False,
}

SUPERSET_WEBSERVER_TIMEOUT = 300
SQLLAB_TIMEOUT = 300
