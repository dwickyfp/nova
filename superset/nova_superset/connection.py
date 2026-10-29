"""``DB_CONNECTION_MUTATOR``: every Nova connection carries a session token.

The "Nova" database URI stores no credential. Each connection gets the signed-in
principal as user and a data token as password; Nova's MySQL proxy verifies the
token, binds the connection to the session's active role and re-checks the
session before every command. Without a signed-in request there is no token,
and the connection is refused rather than made with any other identity.
"""

from __future__ import annotations

import os

from nova_superset import client, context


class NovaSessionRequired(Exception):
    """Nova data was requested outside a signed-in Nova request."""


def _proxy_host() -> str:
    return os.environ.get("NOVA_PROXY_HOST", "nova-backend")


def mutate_connection(uri, params, username, security_manager, source):
    del security_manager, source
    if uri.host != _proxy_host():
        raise NovaSessionRequired("Only the Nova database is available")
    handle = context.bi_handle.get()
    if not handle:
        raise NovaSessionRequired("Nova data needs a signed-in Nova session")
    token = context.data_token.get()
    if token is None:
        try:
            token = client.data_token(handle)
        except client.NovaUnauthorized:
            raise NovaSessionRequired("The Nova session has ended") from None
        context.data_token.set(token)
    if username and username != token.username:
        raise NovaSessionRequired("The Superset user does not match the Nova session")
    return uri.set(username=token.username, password=token.token), params
