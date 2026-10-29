"""Unit tests for nova_superset. Run inside the Nova Superset image:

    docker run --rm -v "$PWD/superset/tests:/app/nova-tests:ro" \
        --entrypoint python nova-superset:local -m unittest discover -s /app/nova-tests -v
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask
from flask_login import LoginManager, UserMixin
from sqlalchemy.engine.url import make_url

from nova_superset import app as nova_app
from nova_superset import bootstrap, client, connection, context, jinja, security

IDENTITY = client.Identity(
    username="alice", active_role="finance", superset_role="NovaAuthor", security_context_version=3
)


class User(UserMixin):
    def __init__(self, user_id: str) -> None:
        self.id = user_id


def _flask() -> Flask:
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    manager = LoginManager(flask_app)
    manager.user_loader(lambda user_id: User(user_id))
    nova_app.init_app(flask_app)

    @flask_app.route("/superset/welcome/")
    def welcome():
        return "ok"

    @flask_app.route("/api/v1/chart/")
    def charts():
        return "charts"

    @flask_app.route("/static/app.js")
    def asset():
        return "asset"

    return flask_app


class SignInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _flask()
        self.http = self.app.test_client()
        self.sm = SimpleNamespace(sync_nova_user=mock.Mock(return_value=User("7")))
        patcher = mock.patch("superset.security_manager", self.sm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_page_without_cookie_goes_to_nova_launch(self):
        response = self.http.get("/superset/welcome/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].startswith("/bi-launch?next=/superset/welcome/")
        )

    def test_api_without_cookie_is_401(self):
        self.assertEqual(self.http.get("/api/v1/chart/").status_code, 401)

    def test_superset_credential_flows_do_not_exist(self):
        for path in ("/register/", "/api/v1/security/login", "/resetpassword/form"):
            self.assertEqual(self.http.post(path).status_code, 404, path)
        login = self.http.get("/login/?next=/dashboard/1/")
        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.headers["Location"], "/bi-launch?next=/dashboard/1/")

    def test_static_assets_need_no_identity(self):
        self.assertEqual(self.http.get("/static/app.js").status_code, 200)

    def test_valid_cookie_signs_the_mirror_user_in(self):
        self.http.set_cookie("nova_bi", "handle-1")
        seen = {}

        @self.app.route("/probe/")
        def probe():
            seen["handle"] = context.bi_handle.get()
            return "ok"

        with mock.patch.object(client, "identity", return_value=IDENTITY) as identity:
            response = self.http.get("/superset/welcome/")
            self.http.get("/probe/")
        self.assertEqual(response.status_code, 200)
        identity.assert_called_with("handle-1")
        self.sm.sync_nova_user.assert_called_with(IDENTITY)
        self.assertEqual(seen["handle"], "handle-1")
        # Teardown clears the session so the thread's next request starts empty.
        self.assertIsNone(context.bi_handle.get())

    def test_rejected_cookie_signs_out(self):
        self.http.set_cookie("nova_bi", "stale")
        with mock.patch.object(client, "identity", side_effect=client.NovaUnauthorized):
            self.assertEqual(self.http.get("/api/v1/chart/").status_code, 401)
        self.sm.sync_nova_user.assert_not_called()

    def test_nova_unavailable_fails_closed(self):
        self.http.set_cookie("nova_bi", "handle-1")
        with mock.patch.object(client, "identity", side_effect=client.NovaUnavailable("down")):
            self.assertEqual(self.http.get("/superset/welcome/").status_code, 503)


class ConnectionMutatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.uri = make_url("starrocks://nova@nova-backend:4406/default_catalog")
        os.environ["NOVA_PROXY_HOST"] = "nova-backend"

    def test_signed_in_request_uses_the_session_token(self):
        token = client.DataToken(username="alice", token="tok", expires_in=300)
        context.bi_handle.set("handle-1")
        # Superset runs SQL Lab in a nested request context: no flask.g there.
        with (
            self.app.test_request_context(),
            mock.patch.object(client, "data_token", return_value=token) as minted,
        ):
            uri, params = connection.mutate_connection(self.uri, {}, "alice", None, None)
            connection.mutate_connection(self.uri, {}, "alice", None, None)
        self.assertEqual((uri.username, uri.password), ("alice", "tok"))
        self.assertEqual(params, {})
        minted.assert_called_once()

    def tearDown(self) -> None:
        context.clear()

    def test_no_signed_in_session_is_refused(self):
        with self.assertRaises(connection.NovaSessionRequired):
            connection.mutate_connection(self.uri, {}, "alice", None, None)

    def test_other_hosts_are_refused(self):
        other = make_url("mysql://x@elsewhere:3306/db")
        context.bi_handle.set("handle-1")
        with self.assertRaises(connection.NovaSessionRequired):
            connection.mutate_connection(other, {}, "alice", None, None)

    def test_user_mismatch_is_refused(self):
        token = client.DataToken(username="bob", token="tok", expires_in=300)
        context.bi_handle.set("handle-1")
        with (
            mock.patch.object(client, "data_token", return_value=token),
            self.assertRaises(connection.NovaSessionRequired),
        ):
            connection.mutate_connection(self.uri, {}, "alice", None, None)


class SecurityTests(unittest.TestCase):
    def test_launch_url_stays_on_this_origin(self):
        flask_app = Flask(__name__)
        with flask_app.test_request_context("/", base_url="http://nova.test/bi"):
            self.assertEqual(
                security.launch_url("/bi/dashboard/1/"), "/bi-launch?next=/bi/dashboard/1/"
            )
            self.assertEqual(security.launch_url("//evil.example/"), "/bi-launch?next=/bi/")
            self.assertEqual(security.launch_url("https://evil.example/"), "/bi-launch?next=/bi/")
            self.assertEqual(security.launch_url("/settings"), "/bi-launch?next=/bi/")

    def test_mirror_user_gets_exactly_the_mapped_role(self):
        viewer, author = SimpleNamespace(name="NovaViewer"), SimpleNamespace(name="NovaAuthor")
        existing = SimpleNamespace(roles=[viewer], active=False, password="pbkdf2:sha256:x")
        sm = SimpleNamespace(
            find_role=lambda name: {"NovaViewer": viewer, "NovaAuthor": author}.get(name),
            find_user=mock.Mock(return_value=existing),
            update_user=mock.Mock(),
        )
        user = security.NovaSecurityManager.sync_nova_user(sm, IDENTITY)
        self.assertIs(user, existing)
        self.assertEqual(user.roles, [author])
        self.assertTrue(user.active)
        self.assertIsNone(user.password)
        sm.update_user.assert_called_once_with(existing)

    def test_new_mirror_user_has_no_password(self):
        author = SimpleNamespace(name="NovaAuthor")
        created = SimpleNamespace(roles=[author], active=True, password="pbkdf2:sha256:empty")
        sm = SimpleNamespace(
            find_role=lambda name: author,
            find_user=mock.Mock(return_value=None),
            add_user=mock.Mock(return_value=created),
            update_user=mock.Mock(),
        )
        user = security.NovaSecurityManager.sync_nova_user(sm, IDENTITY)
        self.assertIsNone(user.password)
        sm.update_user.assert_called_once_with(created)

    def test_unknown_superset_role_is_refused(self):
        bad = client.Identity("alice", "x", "Admin", 1)
        with self.assertRaises(ValueError):
            security.NovaSecurityManager.sync_nova_user(SimpleNamespace(), bad)


class BootstrapTests(unittest.TestCase):
    def test_nova_keeps_security_and_database_administration(self):
        for pair in [
            ("can_write", "Database"),
            ("can_upload", "Database"),
            ("can_read", "Row Level Security"),
            ("can_write", "RowLevelSecurity"),
            ("can_post", "Role"),
            ("can_grant_guest_token", "SecurityRestApi"),
            ("can_import_", "ImportExportRestApi"),
            ("can_add", "DynamicPlugin"),
        ]:
            self.assertFalse(bootstrap.allowed(*pair), pair)

    def test_analysis_permissions_remain(self):
        for pair in [
            ("can_read", "Database"),
            ("can_read", "Chart"),
            ("can_write", "Dashboard"),
            ("can_execute_sql_query", "SQLLab"),
        ]:
            self.assertTrue(bootstrap.allowed(*pair), pair)

    def test_database_uri_holds_no_credential(self):
        uri = make_url(bootstrap.nova_database_uri())
        self.assertIsNone(uri.password)
        self.assertEqual(uri.host, os.environ.get("NOVA_PROXY_HOST", "nova-backend"))


class JinjaTests(unittest.TestCase):
    def test_active_role_comes_from_the_signed_in_identity(self):
        self.assertEqual(jinja.nova_active_role(), "")
        context.identity.set(IDENTITY)
        try:
            self.assertEqual(jinja.nova_active_role(), "finance")
        finally:
            context.clear()


if __name__ == "__main__":
    unittest.main()
