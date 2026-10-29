"""Per-request Nova sign-in, installed through ``FLASK_APP_MUTATOR``.

Every request that is not a static asset or health probe re-validates the
``nova_bi`` cookie with Nova. A missing or rejected cookie signs the mirror
user out: pages go to Nova's launch route, API calls get ``401``. If Nova
cannot answer, the request fails with ``503`` rather than continuing on a
stale identity.
"""

from __future__ import annotations

import logging

from flask import Flask, abort, jsonify, redirect, request
from flask_login import current_user, login_user, logout_user

from nova_superset import client, context
from nova_superset.security import launch_url

logger = logging.getLogger(__name__)

BI_COOKIE = "nova_bi"
_OPEN_PREFIXES = ("/static/", "/health", "/healthcheck", "/ping", "/logout/")
#: Superset's own credential flows. Nova is the only sign-in, so these do not
#: exist here: no password login (form or API), registration or reset.
_REMOVED_PREFIXES = (
    "/register/",
    "/api/v1/security/login",
    "/api/v1/security/refresh",
    "/resetpassword",
    "/resetmypassword",
    "/users/userinfo",
)


def _is_api(path: str) -> bool:
    return path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def _signed_out(path: str):
    if current_user.is_authenticated:
        logout_user()
    if _is_api(path):
        return jsonify({"message": "Sign in to Nova to use BI"}), 401
    return redirect(launch_url(request.script_root + request.full_path.rstrip("?")))


def sign_in():
    context.clear()
    path = request.path
    if path.startswith(_REMOVED_PREFIXES):
        abort(404)
    if path.startswith("/login"):
        return redirect(launch_url(request.args.get("next")))
    if path.startswith(_OPEN_PREFIXES):
        return None
    handle = request.cookies.get(BI_COOKIE, "")
    if not handle:
        return _signed_out(path)
    try:
        identity = client.identity(handle)
    except client.NovaUnauthorized:
        return _signed_out(path)
    except client.NovaUnavailable as exc:
        logger.error("Nova identity check failed: %s", exc)
        return jsonify({"message": "Nova is unavailable"}), 503

    from superset import security_manager

    user = security_manager.sync_nova_user(identity)
    if not current_user.is_authenticated or current_user.get_id() != user.get_id():
        login_user(user)
    context.bi_handle.set(handle)
    context.identity.set(identity)
    return None


def _undo_double_logo_prefix(app: Flask) -> None:
    """Superset 6.1.0 prefixes the theme logo with the app root on the server
    and again in the browser, which requests ``/bi/bi/static/...``. Keep the
    server-side value unprefixed so the browser adds the root once.
    """
    root = app.config.get("APPLICATION_ROOT") or "/"
    if root == "/":
        return
    for key in ("THEME_DEFAULT", "THEME_DARK"):
        token = (app.config.get(key) or {}).get("token", {})
        logo = token.get("brandLogoUrl", "")
        if logo.startswith(f"{root}/static/"):
            token["brandLogoUrl"] = logo[len(root) :]


def init_app(app: Flask) -> None:
    _undo_double_logo_prefix(app)
    app.before_request(sign_in)
    app.teardown_request(lambda _exc: context.clear())
