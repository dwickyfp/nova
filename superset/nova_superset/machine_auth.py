"""Headless-browser sign-in for thumbnails and report screenshots.

Superset's default provider forges a Flask session cookie for the executor,
which Nova's request hook ignores: the only sign-in is a Nova BI handle. The
browser gets the handle of the session the task runs as (carried from the
request, see ``celery_hooks``) or, for scheduled work, a delegated one for the
executor (S5).
"""

from __future__ import annotations

from typing import Any

from superset.utils.machine_auth import MachineAuthProvider

from nova_superset import context
from nova_superset.app import BI_COOKIE
from nova_superset.connection import NovaSessionRequired, background_session


class NovaMachineAuthProvider(MachineAuthProvider):
    def get_cookies(self, user: Any | None) -> dict[str, str]:
        return self.get_auth_cookies(user)

    @staticmethod
    def get_auth_cookies(user: Any) -> dict[str, str]:
        """Used for screenshots and for the CSV and text data of reports."""
        handle = context.current_handle()
        if not handle:
            if context.needs_carry():
                raise NovaSessionRequired("The submitting Nova session has ended")
            handle = background_session(getattr(user, "username", None))
        return {BI_COOKIE: handle}
