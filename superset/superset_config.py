"""Superset configuration for Nova (docs/arch-17-superset.md).

Values come from the environment; secrets are never written here. Features
that would bypass Nova's security contract are configured off and are listed
with the rule that keeps them off.
"""

from __future__ import annotations

import os
from copy import deepcopy

import nova_superset.celery_hooks  # noqa: F401 - registers the session carry
import nova_superset.metrics  # noqa: F401 - registers Celery task timings
import pymysql
import redis
from cachelib.redis import RedisCache
from celery.schedules import crontab
from flask_appbuilder.security.manager import AUTH_REMOTE_USER
from nova_superset.app import init_app
from nova_superset.connection import mutate_connection
from nova_superset.events import NovaEventLogger
from nova_superset.jinja import JINJA_CONTEXT_ADDONS as _NOVA_JINJA
from nova_superset.metrics import NovaStatsLogger
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

# Request, query and Celery metrics go to the observability stack's statsd
# exporter (SUPERSET_STATSD_HOST); dropped when it is not running.
STATS_LOGGER = NovaStatsLogger()

# Nova's look (DESIGN.md): brick-red primary, Inter, the console's radius and
# surfaces. Nova sets the light or dark mode of a framed Superset.
#
# Every surface, text, border and status token is pinned to Nova's values in
# frontend/src/styles/theme.css rather than left to Ant Design's derivation,
# which tinted the dark surfaces navy and darkened brick red to an unreadable
# text color. Brick red stays the fill of primary actions; as text on dark
# surfaces Nova uses its --accent-foreground (#ff8274), never --primary.
APP_NAME = "Nova BI"
_NOVA_TOKENS = {
    "colorPrimary": "#d04738",
    "fontFamily": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "borderRadius": 10,
    "brandAppName": "Nova BI",
}
_NOVA_LIGHT_TOKENS = {
    # Text and links: --foreground, --muted-foreground, --primary / --accent-foreground.
    "colorTextBase": "#1e293b",
    "colorText": "#1e293b",
    "colorTextSecondary": "#475569",
    "colorTextTertiary": "#64748b",
    "colorTextQuaternary": "#94a3b8",
    "colorTextPlaceholder": "#64748b",
    "colorLink": "#c13f31",
    "colorLinkHover": "#a53126",
    "colorLinkActive": "#a53126",
    "colorPrimaryText": "#a53126",
    "colorPrimaryTextHover": "#c13f31",
    # Superset labels its secondary buttons in colorPrimary on colorPrimaryBg;
    # Nova's #d04738 reads 4.08:1 there. #c34032 on #fff5f3 clears 4.5:1 as
    # that label (hover included) and under white text on solid buttons.
    "colorPrimary": "#c34032",
    "colorPrimaryBg": "#fff5f3",
    "colorPrimaryBgHover": "#fff0ed",
    "colorPrimaryBorder": "#f6c2b9",
    # Surfaces: --background, --surface-1, --muted, --border.
    "colorBgBase": "#ffffff",
    "colorBgLayout": "#f8fafc",
    "colorBgContainer": "#ffffff",
    "colorBgElevated": "#ffffff",
    "colorFillAlter": "#f8fafc",
    "colorFillSecondary": "#f1f5f9",
    "colorFillTertiary": "#f1f5f9",
    "colorFillQuaternary": "#f8fafc",
    "colorBorder": "#e2e8f0",
    # One hairline, Nova's --border, so Superset's rules meet Nova's at one shade.
    "colorBorderSecondary": "#e2e8f0",
    "colorSplit": "#e2e8f0",
    # Status: --success, --warning, --destructive, --info.
    "colorSuccess": "#14a89a",
    "colorWarning": "#e89b17",
    "colorError": "#dc2626",
    "colorInfo": "#4f7ee8",
    # As text, status uses Nova's -strong variants (AA on white and on /10 tints).
    "colorSuccessText": "#107c70",
    "colorWarningText": "#986411",
    "colorErrorText": "#b91c1c",
    "colorInfoText": "#4369c4",
}
_NOVA_DARK_TOKENS = {
    "colorTextBase": "#f7f8fa",
    "colorText": "#f7f8fa",
    "colorTextSecondary": "#c2cbd6",
    "colorTextTertiary": "#9ca8b8",
    "colorTextQuaternary": "#6b7787",
    "colorTextPlaceholder": "#7d8999",
    "colorLink": "#ff8274",
    "colorLinkHover": "#ffa197",
    "colorLinkActive": "#ff8274",
    "colorPrimaryText": "#ff8274",
    "colorPrimaryTextHover": "#ffa197",
    # Superset paints its secondary buttons' label in colorPrimary itself, and
    # the dark algorithm darkens colorPrimary for solid fills. #e05a4c is the
    # brick that clears 4.5:1 both ways: as that label on colorPrimaryBg and
    # under white text once darkened.
    "colorPrimary": "#e05a4c",
    "colorPrimaryBg": "#221514",
    "colorPrimaryBgHover": "#2b1917",
    "colorPrimaryBorder": "#5c2a24",
    # Surfaces: --background, --surface-1/2/3, --popover, --border.
    "colorBgBase": "#0b0f14",
    "colorBgLayout": "#0b0f14",
    "colorBgContainer": "#11161d",
    "colorBgElevated": "#171d26",
    "colorBgSpotlight": "#202833",
    "colorFillAlter": "#0e131a",
    "colorFillSecondary": "#1b222c",
    "colorFillTertiary": "#171d26",
    "colorFillQuaternary": "#11161d",
    "colorBorder": "#28313d",
    "colorBorderSecondary": "#28313d",
    "colorSplit": "#28313d",
    "colorSuccess": "#2cc6b6",
    "colorWarning": "#f0ad3d",
    "colorError": "#ff6467",
    "colorInfo": "#7398ed",
    "colorSuccessText": "#2cc6b6",
    "colorWarningText": "#f0ad3d",
    "colorErrorText": "#ff6467",
    "colorInfoText": "#7398ed",
    "colorEditorSelection": "#3a2f17",
}
# Charts draw series in Nova's categorical palette (frontend/src/styles/theme.css
# --chart-1 to --chart-9), so a chart reads the same in Studio's answers, News
# and BI. It is Superset's default; a chart that names another scheme keeps it.
NOVA_CATEGORICAL_COLORS = [
    "#f05a47",
    "#14a89a",
    "#e89b17",
    "#4f7ee8",
    "#d94f86",
    "#a78bfa",
    "#f472b6",
    "#38bdf8",
    "#34d399",
]
EXTRA_CATEGORICAL_COLOR_SCHEMES = [
    {
        "id": "novaCategorical",
        "label": "Nova",
        "description": "Nova's chart palette",
        "isDefault": True,
        "colors": NOVA_CATEGORICAL_COLORS,
    }
]
THEME_DEFAULT = deepcopy(_DEFAULT_THEME)
THEME_DEFAULT.setdefault("token", {}).update({**_NOVA_TOKENS, **_NOVA_LIGHT_TOKENS})
THEME_DARK = deepcopy(_DEFAULT_THEME_DARK) if _DEFAULT_THEME_DARK else None
if THEME_DARK is not None:
    THEME_DARK.setdefault("token", {}).update({**_NOVA_TOKENS, **_NOVA_DARK_TOKENS})
    # The selected top-menu item otherwise takes the darkened brick as text.
    THEME_DARK["components"] = {
        "Menu": {
            "itemSelectedColor": "#ff8274",
            "horizontalItemSelectedColor": "#ff8274",
            "itemHoverColor": "#ffa197",
            "horizontalItemHoverColor": "#ffa197",
        },
    }

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
    # Each tick expires before the next one is due. With the worker down, beat
    # would otherwise pile stale ticks into the shared Redis (measured: 1 323
    # queued in 22 hours on 2026-11-08), and a restarted worker would replay
    # every one of them.
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": crontab(minute="*"),
            "options": {"expires": 55},
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=0, hour=0),
            "options": {"expires": 3600},
        },
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
    # D8: a dashboard belongs to the Nova role it was made under (role_scope).
    "DASHBOARD_RBAC": True,
}

SUPERSET_WEBSERVER_TIMEOUT = 300
SQLLAB_TIMEOUT = 300
