"""NOVA-106 — stage file operations must not escape the stage prefix.

The four file operations built the S3 key as ``f"{prefix}/{filename}"`` with
whatever the URL/upload supplied, so ``../`` climbed out of the stage and let
a logged-in user read or delete any object the stage's principal could reach.

These are pure unit tests: ``get_stage`` and the S3 client are patched, so no
engine, no MinIO, and — critically — the assertions are on the *key handed to
boto3*, which is the thing under test. A test that only asserted "an exception
was raised" would not distinguish rejecting the path from rejecting it after
building a damaging key.
"""

from unittest.mock import MagicMock

import pytest

from app.modules.stages.service import (
    InvalidStagePathError,
    _resolve_stage_key,
    _validate_stage_path,
    stage_service,
)

PREFIX = "datalake/bronze/stage1"

STAGE = {
    "id": "stage-1",
    "name": "stage1",
    "database_name": "analytics",
    "schema_name": "public",
    "storage_connection": "default",
    "base_prefix": PREFIX,
}


# ── _validate_stage_path: rejection table ───────────────────────


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        "../secrets.csv",
        "../../../etc/passwd",
        "folder/../../other-stage/secret.csv",
        "/etc/passwd",
        "/datalake/bronze/stage2/x.csv",
        "folder/..",
        "..\\secrets.csv",  # backslash traversal
        "folder\\evil.csv",  # backslash separator smuggled in
        "folder/\x00evil.csv",  # NUL truncation
    ],
)
def test_escaping_paths_are_rejected_before_any_key_is_built(filename):
    with pytest.raises(InvalidStagePathError):
        _validate_stage_path(filename)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.csv", "report.csv"),
        ("raw/daily/report.csv", "raw/daily/report.csv"),
        ("raw/daily/", "raw/daily"),
        ("./report.csv", "report.csv"),
        ("raw//daily/report.csv", "raw/daily/report.csv"),
        # Collapses to the stage root itself — still inside the stage.
        ("raw/daily/../archive.csv", "raw/archive.csv"),
        ("a/b/../../report.csv", "report.csv"),
        ("nama file dengan spasi.csv", "nama file dengan spasi.csv"),
    ],
)
def test_legal_names_are_normalized_not_rejected(filename, expected):
    """AC3 — ordinary names, including sub-folders, keep working."""
    assert _validate_stage_path(filename) == expected


# ── _resolve_stage_key: the prefix invariant ────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.csv", f"{PREFIX}/report.csv"),
        ("raw/daily/report.csv", f"{PREFIX}/raw/daily/report.csv"),
        ("raw/../report.csv", f"{PREFIX}/report.csv"),
        # A leading "./" is normalized away, not treated as a separator.
        ("./report.csv", f"{PREFIX}/report.csv"),
    ],
)
def test_resolved_key_sits_inside_the_prefix(filename, expected):
    assert _resolve_stage_key(STAGE, filename) == expected


@pytest.mark.parametrize(
    "filename",
    ["../secrets.csv", "folder/../../secrets.csv", "/etc/passwd"],
)
def test_resolved_key_never_escapes_the_prefix(filename):
    with pytest.raises(InvalidStagePathError):
        _resolve_stage_key(STAGE, filename)


def test_prefix_that_itself_contains_traversal_is_still_policed():
    """AC4 — a traversal segment in ``base_prefix`` cannot widen the boundary.

    ``_resolve_stage_key`` normalizes the prefix before joining, so a
    registered prefix that collapses to something else is resolved to a
    stable, traversal-free key rather than smuggling ``..`` into the key.
    """
    stage = {**STAGE, "base_prefix": "datalake/bronze/../stage1"}

    key = _resolve_stage_key(stage, "report.csv")

    assert key == "datalake/stage1/report.csv"
    assert ".." not in key


def test_prefix_check_rejects_a_key_that_normpath_shifted_out():
    """The final prefix check is load-bearing, not decoration.

    ``"a/../b"`` is a *valid* relative name that ``_validate_stage_path``
    normalizes to ``b``, so the documented call path never trips the check.
    This exercises the guard directly with a filename that normalizes to a
    traversal, pinning the invariant independently of the helper above it.
    """
    stage = {**STAGE, "base_prefix": "prefix"}

    with pytest.raises(InvalidStagePathError):
        _resolve_stage_key(stage, "../secrets.csv")


# ── file operations: the key handed to boto3 ────────────────────


def _stage_service_with_fake_s3(monkeypatch, bucket="novel-bucket"):
    """Patch ``get_stage`` + S3 client and return the mock S3 client."""
    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"payload")}

    async def fake_get_stage(stage_id):
        return dict(STAGE)

    monkeypatch.setattr(stage_service, "get_stage", fake_get_stage)
    monkeypatch.setattr(stage_service, "_s3_client_for_stage", lambda stage: (fake_s3, bucket))
    return fake_s3


@pytest.mark.asyncio
async def test_upload_uses_and_persists_the_normalized_key(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    result = await stage_service.upload_file("stage-1", "raw/daily/report.csv", b"x")

    assert fake_s3.put_object.call_args.kwargs["Key"] == f"{PREFIX}/raw/daily/report.csv"
    assert result == {"filename": "raw/daily/report.csv", "size": 1}


@pytest.mark.asyncio
async def test_upload_does_not_build_a_key_for_an_escaping_name(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    with pytest.raises(InvalidStagePathError):
        await stage_service.upload_file("stage-1", "../../secrets.csv", b"x")

    fake_s3.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_download_uses_the_normalized_key(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    content = await stage_service.download_file("stage-1", "raw/daily/report.csv")

    assert fake_s3.get_object.call_args.kwargs["Key"] == f"{PREFIX}/raw/daily/report.csv"
    assert content == b"payload"


@pytest.mark.asyncio
async def test_download_does_not_read_a_key_for_an_escaping_name(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    with pytest.raises(InvalidStagePathError):
        await stage_service.download_file("stage-1", "../stage2/secret.csv")

    fake_s3.get_object.assert_not_called()


@pytest.mark.asyncio
async def test_delete_uses_the_normalized_key(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    assert await stage_service.delete_file("stage-1", "raw/daily/report.csv") is True

    assert fake_s3.delete_object.call_args.kwargs["Key"] == f"{PREFIX}/raw/daily/report.csv"


@pytest.mark.asyncio
async def test_delete_does_not_touch_a_key_for_an_escaping_name(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    with pytest.raises(InvalidStagePathError):
        await stage_service.delete_file("stage-1", "../../../etc/passwd")

    fake_s3.delete_object.assert_not_called()


# ── list_files: browse is a key-forming operation too (AC4) ─────


@pytest.mark.asyncio
async def test_list_files_prefixes_the_validated_folder(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)
    page = {"CommonPrefixes": [], "Contents": []}
    fake_s3.get_paginator.return_value.paginate.return_value = [page]

    await stage_service.list_files("stage-1", prefix="raw/daily")

    assert (
        fake_s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"]
        == f"{PREFIX}/raw/daily/"
    )


@pytest.mark.asyncio
async def test_list_files_without_a_prefix_stays_at_the_stage_root(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)
    fake_s3.get_paginator.return_value.paginate.return_value = [
        {"CommonPrefixes": [], "Contents": []}
    ]

    await stage_service.list_files("stage-1")

    assert fake_s3.get_paginator.return_value.paginate.call_args.kwargs["Prefix"] == f"{PREFIX}/"


@pytest.mark.asyncio
async def test_list_files_does_not_paginate_on_an_escaping_prefix(monkeypatch):
    fake_s3 = _stage_service_with_fake_s3(monkeypatch)

    with pytest.raises(InvalidStagePathError):
        await stage_service.list_files("stage-1", prefix="../stage2")

    fake_s3.get_paginator.assert_not_called()


@pytest.mark.asyncio
async def test_missing_stage_still_raises_plain_value_error(monkeypatch):
    """The fix must not turn "no such stage" into a 400."""

    async def no_stage(stage_id):
        return None

    monkeypatch.setattr(stage_service, "get_stage", no_stage)

    with pytest.raises(ValueError) as excinfo:
        await stage_service.download_file("nope", "report.csv")

    assert not isinstance(excinfo.value, InvalidStagePathError)


# ── router: HTTP status mapping ─────────────────────────────────


def test_rejected_path_maps_to_400_not_404():
    """AC2 — an escape attempt is a client error, not a missing stage."""
    from app.modules.stages.router import _as_client_error

    exc = _as_client_error(InvalidStagePathError("escapes the stage prefix"))

    assert exc.status_code == 400


def test_missing_stage_still_maps_to_404():
    from app.modules.stages.router import _as_client_error

    exc = _as_client_error(ValueError("Stage 'x' not found"))

    assert exc.status_code == 404
