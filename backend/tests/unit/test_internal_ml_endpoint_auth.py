"""Security regression tests for the internal ML predict endpoint (NOVA-90).

Finding #1 (Critical) of security review NOVA-87: ``POST
/api/v1/internal/ml/predict`` had no authentication and no caller restriction,
while ``create_app`` mounts it unconditionally and the documented run command
binds ``0.0.0.0``. Any network-reachable client could run inference on any
registered model with an arbitrary alias, bypassing authentication and RBAC.

These tests exercise the real routers through ``TestClient`` and assert the
HTTP response, so they fail on the pre-fix tree for the reported reason —
the unauthenticated request returned 200 instead of being rejected.

The app is assembled by hand (routers + dependency overrides) rather than via
``create_app`` so no engine, Redis or MinIO is required, matching the pattern
of ``test_explain_credential_leak.py``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.ml_engine.internal_auth import INTERNAL_TOKEN_HEADER

INTERNAL_PREDICT = "/api/v1/internal/ml/predict"
ML_PREDICT = "/api/v1/ml/predict"
SECRET = "test-internal-token-placeholder"

REQUEST_BODY = {"model_alias": "churn_model", "features": {"age": 34}}
PREDICTION = {
    "model_alias": "churn_model",
    "model_name": "churn_model",
    "prediction": "churned",
    "probability": None,
    "model_version": 1,
}


class _Peer:
    """Stand-in for the ASGI scope's ``client`` so we can pick the peer IP."""

    def __init__(self, host: str):
        self.host = host


class _PeerInjector:
    """Set ``scope["client"]`` so the loopback gate sees a chosen address.

    ``TestClient`` reports the peer as ``testclient``, which is neither a
    loopback address nor a trusted proxy — perfect as the "arbitrary network
    client" default, but we need to vary it to prove the gate keys on the peer.
    """

    def __init__(self, app: FastAPI, host: str):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope)
            scope["client"] = (_Peer(self.host).host, 12345)
        await self.app(scope, receive, send)


@pytest.fixture
def predict_app(monkeypatch):
    """Real ML routers with only the predict service and config stubbed."""
    from app.core.config import settings
    from app.modules.ml_engine import service as service_module
    from app.modules.ml_engine.router import router as ml_router

    async def fake_predict(model_alias, features):
        return {**PREDICTION, "model_alias": model_alias}

    monkeypatch.setattr(settings, "NOVA_INTERNAL_TOKEN", SECRET)
    monkeypatch.setattr(service_module.ml_engine_service, "predict", fake_predict)

    app = FastAPI()
    from app.modules.ml_engine.internal_router import router as internal_router

    app.include_router(ml_router, prefix="/api/v1/ml")
    app.include_router(internal_router, prefix="/api/v1/internal/ml")
    return app


def _client(app: FastAPI, peer: str = "203.0.113.9") -> TestClient:
    return TestClient(_PeerInjector(app, peer), raise_server_exceptions=False)


class TestUnauthenticatedCallerIsRejected:
    """Acceptance criterion 1: a non-loopback caller with no token is refused."""

    def test_non_loopback_without_token_is_rejected(self, predict_app):
        # The reported hole: this returned 200 on the pre-fix tree.
        with _client(predict_app) as client:
            resp = client.post(INTERNAL_PREDICT, json=REQUEST_BODY)

        assert resp.status_code == 403, resp.text
        assert "prediction" not in resp.text

    def test_non_loopback_with_wrong_token_is_rejected(self, predict_app):
        with _client(predict_app) as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: "not-the-token"},
            )

        assert resp.status_code == 403, resp.text

    def test_loopback_without_token_is_rejected(self, predict_app):
        """Loopback alone is not enough — the shared secret is still required."""
        with _client(predict_app, peer="127.0.0.1") as client:
            resp = client.post(INTERNAL_PREDICT, json=REQUEST_BODY)

        assert resp.status_code == 401, resp.text

    def test_loopback_with_wrong_token_is_rejected(self, predict_app):
        with _client(predict_app, peer="127.0.0.1") as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: "wrong"},
            )

        assert resp.status_code == 401, resp.text


class TestUnconfiguredSecretFailsClosed:
    """A deployment without the secret must not degrade to open access."""

    def test_unset_token_rejects_loopback(self, predict_app, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "NOVA_INTERNAL_TOKEN", "")
        with _client(predict_app, peer="127.0.0.1") as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: ""},
            )

        assert resp.status_code in (401, 503), resp.text
        assert "prediction" not in resp.text


class TestTrustedCallerIsAccepted:
    """Acceptance criterion 2: the authenticated UDF call path still works."""

    def test_loopback_with_valid_token_succeeds(self, predict_app):
        with _client(predict_app, peer="127.0.0.1") as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: SECRET},
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["prediction"] == "churned"

    def test_trusted_proxy_with_valid_token_succeeds(self, predict_app, monkeypatch):
        """Docker BE traffic forwarded by a trusted proxy is allowed in.

        The UDF runs inside the StarRocks BE container and reaches Nova through
        a proxy on the host, so its peer is the proxy, not loopback.
        """
        from app.core.config import settings

        monkeypatch.setattr(settings, "NOVA_INTERNAL_TRUSTED_PROXY", "203.0.113.9")
        with _client(predict_app, peer="203.0.113.9") as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: SECRET},
            )

        assert resp.status_code == 200, resp.text

    def test_authenticated_public_route_still_works(self, predict_app):
        """The redundant-but-authenticated route is untouched by this change."""
        from app.core import deps as deps_module

        async def fake_current_user():
            return {
                "username": "analyst",
                "session_id": "sess",
                "roles": [],
                "active_role": None,
                "encrypted_password": "enc",
            }

        predict_app.dependency_overrides[
            deps_module.get_current_user
        ] = fake_current_user
        with _client(predict_app) as client:
            resp = client.post(ML_PREDICT, json=REQUEST_BODY)

        assert resp.status_code == 200, resp.text
        # The override is scoped to this app fixture; drop it to be explicit.
        predict_app.dependency_overrides.pop(deps_module.get_current_user, None)

    def test_public_route_still_requires_auth(self, predict_app):
        with _client(predict_app) as client:
            resp = client.post(ML_PREDICT, json=REQUEST_BODY)

        assert resp.status_code in (401, 403), resp.text


class TestCallerAddressGate:
    """The loopback rule is enforced in code, not by deployment docs (AC 3)."""

    @pytest.mark.parametrize(
        ("peer", "expected_status"),
        [
            ("127.0.0.1", 200),
            ("127.0.0.53", 200),
            ("::1", 200),
            ("localhost", 200),
            ("10.0.0.5", 403),
            ("192.168.1.20", 403),
            ("203.0.113.9", 403),
        ],
    )
    def test_loopback_classes_only(self, predict_app, peer, expected_status):
        with _client(predict_app, peer=peer) as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: SECRET},
            )

        assert resp.status_code == expected_status, f"{peer}: {resp.text}"

    def test_missing_peer_is_not_loopback(self, predict_app):
        """A request with no client address must not be treated as local."""

        class _NoPeer:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    scope = dict(scope)
                    scope["client"] = None
                await self.app(scope, receive, send)

        with TestClient(_NoPeer(predict_app), raise_server_exceptions=False) as client:
            resp = client.post(
                INTERNAL_PREDICT,
                json=REQUEST_BODY,
                headers={INTERNAL_TOKEN_HEADER: SECRET},
            )

        assert resp.status_code == 403, resp.text


class TestAppWiring:
    """Guards against the route being re-exposed or re-added unauthenticated."""

    def test_real_app_exposes_only_the_internal_route(self):
        """``create_app`` mounts the internal predict route once, under /internal."""
        from app.main import create_app

        app = create_app()
        paths = set(app.openapi()["paths"])

        assert "/api/v1/internal/ml/predict" in paths
        assert "/api/v1/ml/internal/predict" not in paths
        assert "/api/v1/ml/predict" in paths, "the authenticated route must remain"

    def test_no_unauthenticated_internal_route_under_ml(self):
        from app.modules.ml_engine.router import router

        paths = {route.path for route in router.routes}
        assert "/internal/predict" not in paths

    def test_internal_route_declares_dependency(self):
        from app.modules.ml_engine.internal_auth import require_internal_caller
        from app.modules.ml_engine.internal_router import router

        routes = {route.path: route for route in router.routes}
        route = routes["/predict"]
        dependency_calls = {dep.call for dep in route.dependant.dependencies}
        assert require_internal_caller in dependency_calls
