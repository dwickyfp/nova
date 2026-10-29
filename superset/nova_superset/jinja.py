"""Jinja additions for SQL Lab and virtual datasets."""

from __future__ import annotations

from nova_superset import context


def nova_active_role() -> str:
    """The active Nova role the current query runs under."""
    identity = context.identity.get()
    return identity.active_role if identity else ""


JINJA_CONTEXT_ADDONS = {"nova_active_role": nova_active_role}
