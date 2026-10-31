"""Unit tests for nova_superset. Run inside the Nova Superset image:

    docker run --rm -v "$PWD/superset/tests:/app/nova-tests:ro" \
        --entrypoint python nova-superset:local -m unittest discover -s /app/nova-tests -v
"""

from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask
from flask_login import LoginManager, UserMixin
from nova_superset import app as nova_app
from nova_superset import bootstrap, client, connection, context, jinja, metrics, security, semantic_guard
from sqlalchemy.engine.url import make_url

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
        # Queries on Nova are timed for the BI metrics (Phase 5).
        self.assertEqual(params, {"connect_args": {"cursorclass": metrics.TimedCursor}})
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

    def test_only_the_nova_proxy_itself_is_accepted(self):
        context.bi_handle.set("handle-1")
        for other in (
            "starrocks://nova@nova-backend:8000/default_catalog",
            "mysql://nova@nova-backend:4406/default_catalog",
            "starrocks://nova@starrocks-fe:9030/default_catalog",
        ):
            with self.subTest(other), self.assertRaises(connection.NovaSessionRequired):
                connection.mutate_connection(make_url(other), {}, "alice", None, None)

    def test_every_catalog_nova_exposes_goes_through_the_proxy(self):
        token = client.DataToken(username="alice", token="tok", expires_in=300)
        context.bi_handle.set("handle-1")
        # Superset selects ``catalog.schema`` in the database part before the mutator.
        external = make_url("starrocks://nova@nova-backend:4406/iceberg.sales")
        with mock.patch.object(client, "data_token", return_value=token):
            uri, _ = connection.mutate_connection(external, {}, "alice", None, None)
        self.assertEqual((uri.database, uri.username), ("iceberg.sales", "alice"))

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

    def test_the_nova_database_shows_every_catalog_and_other_databases_are_disabled(self):
        nova = SimpleNamespace(database_name="Nova", extra="{}", get_extra=lambda: {})
        other = SimpleNamespace(database_name="Elsewhere", expose_in_sqllab=True,
                                allow_run_async=True, allow_file_upload=True)

        class Query:
            def __init__(self, rows):
                self.rows = rows

            def filter_by(self, **_):
                return self

            def one_or_none(self):
                return nova

            def all(self):
                return self.rows

        session = mock.Mock()
        session.query.return_value = Query([nova, other])
        nova.set_sqlalchemy_uri = mock.Mock()
        # The model needs an initialised app; the query above stands in for it.
        models = SimpleNamespace(Database=object)
        with mock.patch.dict("sys.modules", {"superset.models.core": models}):
            bootstrap.sync_database(session)
        self.assertEqual(json.loads(nova.extra), {"allow_multi_catalog": True})
        self.assertFalse(other.expose_in_sqllab or other.allow_run_async
                         or other.allow_file_upload)
        session.commit.assert_called_once()

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


class EventTests(unittest.TestCase):
    def test_changes_and_exports_are_audited_reads_are_not(self):
        from nova_superset import events

        app = Flask(__name__)
        with app.test_request_context("/api/v1/chart/", method="POST"):
            self.assertEqual(events.audited_action("ChartRestApi.post"), "BI_CHANGE")
            self.assertEqual(events.audited_action("DashboardRestApi.export"), "BI_EXPORT")
            self.assertEqual(events.audited_action("SqlLabRestApi.export_csv"), "BI_EXPORT")
            self.assertIsNone(events.audited_action("ChartRestApi.get"))
            self.assertIsNone(events.audited_action("ChartRestApi.data"))
        with app.test_request_context(
            "/api/v1/chart/data", method="POST", json={"result_format": "csv"}
        ):
            self.assertEqual(events.audited_action("ChartRestApi.data"), "BI_EXPORT")


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.sent: list[str] = []
        patcher = mock.patch.object(metrics._udp, "send", side_effect=self.sent.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_superset_stats_are_statsd_lines_in_milliseconds(self):
        stats = metrics.NovaStatsLogger()
        stats.incr("ChartRestApi.data.success")
        stats.timing("sqllab.query.time_pending", 0.25)
        self.assertEqual(
            self.sent,
            ["superset.ChartRestApi.data.success:1|c", "superset.sqllab.query.time_pending:250.000|ms"],
        )

    def test_requests_are_timed_with_bounded_tags_and_no_identity(self):
        flask_app = Flask(__name__)
        metrics.init_app(flask_app)
        flask_app.add_url_rule("/api/v1/chart/<int:pk>", "chart", lambda pk: ("", 500))
        flask_app.add_url_rule("/static/app.js", "asset", lambda: "")
        flask_app.add_url_rule("/superset/dashboard/<pk>/", "dash", lambda pk: "")
        client_ = flask_app.test_client()
        client_.get("/api/v1/chart/7?user=alice")
        client_.get("/static/app.js")
        client_.get("/superset/dashboard/12/")
        self.assertEqual(len(self.sent), 2)
        self.assertRegex(self.sent[0], r"^nova_bi_request:[0-9.]+\|ms\|#method:GET,status:5xx,area:chart$")
        self.assertTrue(self.sent[1].endswith("area:dashboard"))
        self.assertNotIn("alice", "".join(self.sent))

    def test_unknown_areas_collapse_into_other(self):
        self.assertEqual(metrics.area("/api/v1/whatever_new_thing/1"), "other")
        self.assertEqual(metrics.area("/sqllab/"), "sqllab")
        self.assertIsNone(metrics.area("/health"))


class TimedCursorTests(unittest.TestCase):
    def test_queries_are_timed_by_outcome(self):
        sent: list[str] = []
        cursor = metrics.TimedCursor.__new__(metrics.TimedCursor)
        with (
            mock.patch.object(metrics._udp, "send", side_effect=sent.append),
            mock.patch("pymysql.cursors.Cursor.execute", side_effect=[1, RuntimeError("denied")]),
        ):
            self.assertEqual(cursor.execute("SELECT 1"), 1)
            with self.assertRaises(RuntimeError):
                cursor.execute("SELECT secret")
        self.assertTrue(sent[0].endswith("|ms|#outcome:ok"))
        self.assertTrue(sent[1].endswith("|ms|#outcome:error"))
        self.assertNotIn("SELECT", "".join(sent))


class UdpTests(unittest.TestCase):
    def test_an_absent_exporter_drops_packets_and_retries_later(self):
        udp = metrics._Udp()
        with mock.patch.object(metrics.socket, "gethostbyname", side_effect=OSError) as lookup:
            udp.send("a:1|c")
            udp.send("b:1|c")
        self.assertEqual(lookup.call_count, 1)  # no lookup storm while it is down
        self.assertIsNone(udp._address)


class SemanticGuardTests(unittest.TestCase):
    SEMANTIC = {5}

    def setUp(self):
        flask_app = Flask(__name__)
        flask_app.before_request(semantic_guard.guard)

        @flask_app.route("/api/v1/dataset/", methods=["POST", "DELETE"])
        @flask_app.route("/api/v1/dataset/<int:pk>", methods=["PUT", "DELETE"])
        @flask_app.route("/api/v1/dataset/<int:pk>/metric/<int:metric>", methods=["DELETE"])
        @flask_app.route("/datasource/save/", methods=["POST"])
        def changed(**_):
            return "changed"

        self.client = flask_app.test_client()
        patcher = mock.patch.object(
            semantic_guard, "follows_semantic_view", side_effect=lambda pk: pk in self.SEMANTIC
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"NOVA_SUPERSET_SERVICE_TOKEN": "service-secret"})
        env.start()
        self.addCleanup(env.stop)

    def test_semantic_datasets_are_not_changed_in_superset(self):
        for method, path in (
            ("put", "/api/v1/dataset/5"),
            ("delete", "/api/v1/dataset/5"),
            ("delete", "/api/v1/dataset/5/metric/9"),
            ("delete", "/api/v1/dataset/?q=!(4,5)"),
        ):
            response = getattr(self.client, method)(path, json={"sql": "SELECT 2"})
            self.assertEqual(response.status_code, 403, path)
            self.assertIn("Semantic View", response.get_json()["message"])
        saved = self.client.post("/datasource/save/", data={"data": '{"id": 5}'})
        self.assertEqual(saved.status_code, 403)

    def test_other_datasets_stay_editable(self):
        self.assertEqual(self.client.put("/api/v1/dataset/4", json={"sql": "x"}).status_code, 200)
        self.assertEqual(self.client.delete("/api/v1/dataset/?q=!(3,4)").status_code, 200)

    def test_nova_sync_may_change_them(self):
        headers = {semantic_guard.SYNC_HEADER: "service-secret"}
        response = self.client.put("/api/v1/dataset/5", json={"sql": "x"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        wrong = {semantic_guard.SYNC_HEADER: "guess"}
        self.assertEqual(
            self.client.put("/api/v1/dataset/5", json={}, headers=wrong).status_code, 403
        )

    def test_a_dataset_cannot_claim_the_marker(self):
        body = {"table_name": "t", "extra": '{"nova_semantic_view": {"id": "x"}}'}
        self.assertEqual(self.client.post("/api/v1/dataset/", json=body).status_code, 403)
        self.assertEqual(
            self.client.put("/api/v1/dataset/4", json={"extra": body["extra"]}).status_code, 403
        )


class ImportTests(unittest.TestCase):
    def test_bundle_imports_do_not_exist(self):
        http = _flask().test_client()
        for kind in ("dashboard", "chart", "dataset", "database", "assets"):
            self.assertEqual(http.post(f"/api/v1/{kind}/import/").status_code, 404, kind)


DELEGATED = client.Identity("alice", "marketing", "NovaAuthor", 1, delegated=True)


class BackgroundTests(unittest.TestCase):
    def setUp(self) -> None:
        context.clear()
        self.addCleanup(context.clear)
        self.uri = make_url("starrocks://nova@nova-backend:4406/default_catalog")
        os.environ["NOVA_PROXY_HOST"] = "nova-backend"

    def test_a_web_request_without_a_session_is_never_delegated(self):
        with (
            mock.patch.object(client, "delegate") as delegate,
            self.assertRaises(connection.NovaSessionRequired),
        ):
            connection.mutate_connection(self.uri, {}, "alice", None, None)
        delegate.assert_not_called()

    def test_scheduled_work_is_delegated_to_the_owner(self):
        context.background.set(True)
        token = client.DataToken(username="alice", token="tok", expires_in=300)
        with (
            mock.patch.object(client, "delegate", return_value=("d-handle", DELEGATED)) as delegate,
            mock.patch.object(client, "data_token", return_value=token) as minted,
        ):
            uri, _ = connection.mutate_connection(self.uri, {}, "alice", None, None)
        delegate.assert_called_once_with("alice")
        minted.assert_called_once_with("d-handle")
        self.assertEqual((uri.username, uri.password), ("alice", "tok"))
        self.assertEqual(context.partition_key(), "nova:delegated:alice:marketing:1")

    def test_a_refused_delegation_refuses_the_connection(self):
        context.background.set(True)
        with (
            mock.patch.object(client, "delegate", side_effect=client.NovaUnauthorized),
            self.assertRaises(connection.NovaSessionRequired),
        ):
            connection.mutate_connection(self.uri, {}, "alice", None, None)

    def test_async_sql_lab_never_falls_back_to_delegation(self):
        context.background.set(True)
        context.carry_required.set(True)
        with (
            mock.patch.object(client, "delegate") as delegate,
            self.assertRaises(connection.NovaSessionRequired),
        ):
            connection.mutate_connection(self.uri, {}, "alice", None, None)
        delegate.assert_not_called()


class CeleryHookTests(unittest.TestCase):
    def setUp(self) -> None:
        from nova_superset import celery_hooks

        self.hooks = celery_hooks
        context.end_task()
        self.addCleanup(context.end_task)

    def test_the_submitting_session_travels_with_carried_tasks_only(self):
        context.adopt("h-1", IDENTITY)
        headers: dict = {}
        self.hooks.carry_session(sender="sql_lab.get_sql_results", headers=headers)
        self.assertEqual(headers, {"nova_bi_handle": "h-1", "nova_bi_state": "alice:finance:3"})
        scheduled: dict = {}
        self.hooks.carry_session(sender="reports.execute", headers=scheduled)
        self.assertEqual(scheduled, {})

    def test_a_carried_task_runs_as_its_session(self):
        task = SimpleNamespace(
            name="sql_lab.get_sql_results",
            request=SimpleNamespace(nova_bi_handle="h-1", nova_bi_state="alice:finance:3"),
        )
        with mock.patch.object(client, "identity", return_value=IDENTITY):
            self.hooks.adopt_session(task=task)
        self.assertTrue(context.in_background() and context.needs_carry())
        self.assertEqual(context.current_handle(), "h-1")
        self.assertEqual(context.partition_key(), "nova:session:alice:finance:3")
        # The task body runs in another context: the process keeps the session.
        context.clear()
        self.assertEqual(context.current_handle(), "h-1")
        self.hooks.release_session()
        self.assertIsNone(context.current_handle())

    def test_a_session_that_changed_role_does_not_run_the_task(self):
        task = SimpleNamespace(
            name="sql_lab.get_sql_results",
            request=SimpleNamespace(nova_bi_handle="h-1", nova_bi_state="alice:marketing:2"),
        )
        with mock.patch.object(client, "identity", return_value=IDENTITY):
            self.hooks.adopt_session(task=task)
        self.assertIsNone(context.current_handle())
        self.assertTrue(context.needs_carry())

    def test_an_ended_session_leaves_a_required_carry_without_identity(self):
        task = SimpleNamespace(
            name="sql_lab.get_sql_results", request=SimpleNamespace(nova_bi_handle="h-1")
        )
        with mock.patch.object(client, "identity", side_effect=client.NovaUnauthorized):
            self.hooks.adopt_session(task=task)
        self.assertIsNone(context.current_handle())
        self.assertTrue(context.needs_carry())

    def test_scheduled_tasks_ignore_any_handle(self):
        task = SimpleNamespace(
            name="reports.execute", request=SimpleNamespace(nova_bi_handle="h-1")
        )
        with mock.patch.object(client, "identity") as identity:
            self.hooks.adopt_session(task=task)
        identity.assert_not_called()
        self.assertIsNone(context.current_handle())
        self.assertTrue(context.in_background())


class MachineAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        context.clear()
        self.addCleanup(context.clear)

    def test_the_browser_signs_in_with_the_task_session_or_a_delegation(self):
        from nova_superset.machine_auth import NovaMachineAuthProvider

        provider = NovaMachineAuthProvider(None)
        context.bi_handle.set("h-1")
        self.assertEqual(
            provider.get_cookies(SimpleNamespace(username="alice")), {"nova_bi": "h-1"}
        )
        context.clear()
        context.background.set(True)
        with mock.patch.object(client, "delegate", return_value=("d-1", DELEGATED)):
            self.assertEqual(
                provider.get_cookies(SimpleNamespace(username="alice")), {"nova_bi": "d-1"}
            )


class PartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        context.clear()
        self.addCleanup(context.clear)

    def test_thumbnail_digests_differ_by_role_and_need_a_session(self):
        from nova_superset import thumbnails

        app = Flask(__name__)
        app.config["HASH_ALGORITHM"] = "sha256"
        app.config["HASH_ALGORITHM_FALLBACKS"] = []
        ctx = app.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        chart = SimpleNamespace(params='{"viz_type": "table"}')
        self.assertIsNone(thumbnails.chart_digest(chart, "current_user", "alice"))
        context.identity.set(IDENTITY)
        finance = thumbnails.chart_digest(chart, "current_user", "alice")
        context.identity.set(client.Identity("alice", "marketing", "NovaAuthor", 4))
        marketing = thumbnails.chart_digest(chart, "current_user", "alice")
        self.assertNotEqual(finance, marketing)
        dashboard = SimpleNamespace(
            id=1, charts=["a"], position_json="{}", css="", json_metadata="{}"
        )
        self.assertIsNotNone(thumbnails.dashboard_digest(dashboard, "current_user", "alice"))

    def test_cached_results_are_keyed_by_the_nova_session(self):
        with mock.patch(
            "superset.security.SupersetSecurityManager.get_rls_cache_key", return_value=[]
        ):
            manager = security.NovaSecurityManager.__new__(security.NovaSecurityManager)
            self.assertEqual(manager.get_rls_cache_key(None), ["nova:none"])
            context.identity.set(IDENTITY)
            key = manager.get_rls_cache_key(None)
        self.assertEqual(key, ["nova:session:alice:finance:3"])
