"""``DB_CONNECTION_MUTATOR``: every Nova connection carries a session token.

The "Nova" database URI stores no credential. Each connection gets the signed-in
principal as user and a data token as password; Nova's MySQL proxy verifies the
token, binds the connection to the session's active role and re-checks the
session before every command. Without a signed-in request there is no token,
and the connection is refused rather than made with any other identity.
"""

from __future__ import annotations

import os

from nova_superset import client, context, metrics


class NovaSessionRequired(Exception):
    """Nova data was requested outside a signed-in Nova request."""


def background_session(username: str | None) -> str:
    """Delegate to ``username`` for this background run and remember it."""
    if not username:
        raise NovaSessionRequired("Background BI work needs an owner")
    try:
        handle, identity = client.delegate(username)
    except client.NovaUnauthorized:
        raise NovaSessionRequired(f"Nova does not allow background BI work as {username}") from None
    context.adopt(handle, identity)
    return handle


def _proxy_host() -> str:
    return os.environ.get("NOVA_PROXY_HOST", "nova-backend")


def _proxy_port() -> int:
    return int(os.environ.get("NOVA_PROXY_PORT", "4406"))


def is_nova_proxy(uri) -> bool:
    """The connection is Nova's MySQL proxy: StarRocks dialect, proxy host and port.

    The database part is Superset's ``catalog.schema`` choice and is left alone:
    every catalog Nova exposes is reached through the same proxy, as the viewer.
    """
    return (
        uri.drivername.split("+", 1)[0] == "starrocks"
        and uri.host == _proxy_host()
        and (uri.port or 9030) == _proxy_port()
    )


def mutate_connection(uri, params, username, security_manager, source):
    del security_manager, source
    if not is_nova_proxy(uri):
        raise NovaSessionRequired("Only the Nova database is available")
    handle = context.current_handle()
    if not handle and context.in_background() and not context.needs_carry():
        handle = background_session(username)
    if not handle:
        raise NovaSessionRequired("Nova data needs a signed-in Nova session")
    token = context.current_token()
    if token is None:
        try:
            token = client.data_token(handle)
        except client.NovaUnauthorized:
            raise NovaSessionRequired("The Nova session has ended") from None
        context.remember_token(token)
    if username and username != token.username:
        raise NovaSessionRequired("The Superset user does not match the Nova session")
    connect_args = {**(params.get("connect_args") or {}), "cursorclass": metrics.TimedCursor}
    return uri.set(username=token.username, password=token.token), {
        **params,
        "connect_args": connect_args,
    }
