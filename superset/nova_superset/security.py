"""Superset security manager: Nova owns sign-in, Superset mirrors the user.

There is no Superset login form, password, registration or reset. The login
route sends the browser to Nova, and :func:`nova_superset.app.sign_in` signs
the Nova principal in on every request. The Superset user row is a mirror
with no usable password, and its one role is recomputed from the session's
active Nova role each time, so a role switch in Nova takes effect on the next
request.
"""

from __future__ import annotations

from urllib.parse import quote

from flask import redirect, request
from flask_appbuilder import expose
from flask_appbuilder.security.views import AuthRemoteUserView
from superset.security import SupersetSecurityManager

from nova_superset.client import Identity

#: Nova's frontend route that opens a BI session, then returns to ``next``.
NOVA_LAUNCH_PATH = "/bi-launch"
NOVA_ROLES = ("NovaViewer", "NovaAuthor", "NovaAdmin")
_EMAIL_DOMAIN = "users.nova.invalid"


def launch_url(next_path: str | None = None) -> str:
    home = f"{request.script_root}/"
    target = next_path or home
    # Only paths inside Superset are valid return targets: no other origin and
    # no other Nova page can be reached through this redirect.
    if not target.startswith(home) or target.startswith("//"):
        target = home
    return f"{NOVA_LAUNCH_PATH}?next={quote(target, safe='/')}"


class NovaLoginView(AuthRemoteUserView):
    @expose("/login/")
    def login(self):
        return redirect(launch_url(request.args.get("next")))


class NovaSecurityManager(SupersetSecurityManager):
    authremoteuserview = NovaLoginView

    def sync_nova_user(self, identity: Identity):
        """Return the mirror user for ``identity``, created or corrected."""
        if identity.superset_role not in NOVA_ROLES:
            raise ValueError(f"Unknown Superset role {identity.superset_role!r}")
        role = self.find_role(identity.superset_role)
        if role is None:
            raise RuntimeError(f"Superset role {identity.superset_role} is not bootstrapped")
        user = self.find_user(username=identity.username)
        if user is None:
            user = self.add_user(
                username=identity.username,
                first_name=identity.username,
                last_name="",
                email=f"{identity.username}@{_EMAIL_DOMAIN}",
                role=role,
            )
            if not user:
                raise RuntimeError("Superset could not create the mirror user")
        # FAB stores a hash even for an empty password; a mirror user has none.
        roles = [r.name for r in user.roles]
        if user.password is not None or roles != [role.name] or not user.active:
            user.password = None
            user.roles = [role]
            user.active = True
            self.update_user(user)
        return user
