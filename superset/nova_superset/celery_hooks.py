"""Which Nova session a Superset Celery task runs as.

Work a signed-in request starts on someone's behalf carries that request's BI
handle in a task header and runs as that session, so its active role and
security-context version still apply (S3, S10). Async SQL Lab must: when its
session has ended or changed before the worker picks it up, the query is
refused rather than run under another role. Thumbnails carry the session when
there is one. Scheduled work (alerts, reports, cache warmup) carries nothing
and is delegated to the owner under their default role (S5).

The handle crosses Redis, Superset's broker, on the internal network only; it
is the same short-lived, session-bound value the browser's cookie holds.
"""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import before_task_publish, task_postrun, task_prerun

from nova_superset import client, context

logger = logging.getLogger(__name__)

HANDLE_HEADER = "nova_bi_handle"
#: The session's state when the task was submitted: a task runs only while
#: the session still has the same active role and security-context version.
STATE_HEADER = "nova_bi_state"
#: Tasks that must run as the session that submitted them.
CARRY_REQUIRED = frozenset({"sql_lab.get_sql_results"})
#: Tasks that run as the requesting session when there is one.
CARRY_OPTIONAL = frozenset(
    {
        "cache_chart_thumbnail",
        "cache_dashboard_thumbnail",
        "cache_dashboard_screenshot",
        "load_chart_data_into_cache",
        "load_explore_json_into_cache",
    }
)


@before_task_publish.connect
def carry_session(sender: str | None = None, headers: dict | None = None, **_: Any) -> None:
    handle = context.current_handle()
    identity = context.current_identity()
    if handle and identity and headers is not None and sender in CARRY_REQUIRED | CARRY_OPTIONAL:
        headers[HANDLE_HEADER] = handle
        headers[STATE_HEADER] = _state(identity)


@task_prerun.connect
def adopt_session(task: Any = None, **_: Any) -> None:
    name = getattr(task, "name", "")
    context.start_task(required=name in CARRY_REQUIRED)
    request = getattr(task, "request", None)
    handle = getattr(request, HANDLE_HEADER, None)
    if not handle or name not in CARRY_REQUIRED | CARRY_OPTIONAL:
        return
    try:
        identity = client.identity(handle)
    except (client.NovaUnauthorized, client.NovaUnavailable) as exc:
        # A required carry then fails in the connection mutator; an optional
        # one falls back to delegation.
        logger.warning(
            "Task %s: its Nova session is no longer usable (%s)", name, type(exc).__name__
        )
        return
    if _state(identity) != getattr(request, STATE_HEADER, None):
        logger.warning("Task %s: its Nova session changed role since it was submitted", name)
        return
    context.adopt(handle, identity)


@task_postrun.connect
def release_session(**_: Any) -> None:
    context.end_task()


def _state(identity: client.Identity) -> str:
    return f"{identity.username}:{identity.active_role}:{identity.security_context_version}"
