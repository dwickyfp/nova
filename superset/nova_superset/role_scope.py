"""Dataviz dashboards belong to the Nova role they were made under.

Product decision (docs/arch-19-studio-dataviz.md, D8): there is no sharing of
Dataviz dashboards. A dashboard is attached to the active Nova role of the
session that creates it, and everyone working under that role sees it; its
data still follows each viewer's own Ranger rows and masks.

This rides on Superset's own ``DASHBOARD_RBAC``: every Nova mirror user also
holds a permission-less Superset role named after its active Nova role, and a
new dashboard is published with that role. Its owners see it whatever their
active role.
"""

from __future__ import annotations

import logging
from typing import Any

from nova_superset import context

logger = logging.getLogger(__name__)

ROLE_PREFIX = "Nova role "


def role_name(active_role: str) -> str:
    """The Superset role standing for one Nova role (FAB names hold 64 chars)."""
    return (ROLE_PREFIX + active_role)[:64]


def _models() -> tuple[Any, Any]:
    from flask_appbuilder.security.sqla.models import Role
    from superset.models.dashboard import Dashboard

    return Role, Dashboard


def stamp_new_dashboards(session: Any, _flush_context: Any, _instances: Any) -> None:
    """Attach each dashboard created in this flush to the creator's Nova role."""
    Role, Dashboard = _models()  # noqa: N806
    created = [obj for obj in session.new if isinstance(obj, Dashboard) and not obj.roles]
    if not created:
        return
    identity = context.current_identity()
    if identity is None or not identity.active_role:
        return
    with session.no_autoflush:
        role = session.query(Role).filter_by(name=role_name(identity.active_role)).one_or_none()
    if role is None:
        logger.warning("No Superset role for Nova role %s", identity.active_role)
        return
    for dashboard in created:
        dashboard.roles = [role]
        dashboard.published = True


def init_app() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", stamp_new_dashboards):
        event.listen(Session, "before_flush", stamp_new_dashboards)
