"""Checks on files uploaded into a notebook's Workspace folder.

An upload is stored, never run: a ``.py`` or ``.ipynb`` file is only text in
the Workspace until the user chooses to run it. These checks keep names plain
and content honest about its type.
"""

from __future__ import annotations

import posixpath
import unicodedata

from app.core.exceptions import StorageError

#: Extensions a notebook folder accepts, with the size each may have.
ALLOWED = {
    ".csv": 200, ".tsv": 200, ".txt": 50, ".json": 100, ".jsonl": 200, ".ndjson": 200,
    ".parquet": 200, ".xlsx": 50, ".xls": 50, ".py": 5, ".sql": 5, ".md": 5,
    ".ipynb": 8, ".png": 20, ".jpg": 20, ".jpeg": 20, ".svg": 5,
}
_MB = 1024 * 1024
_TEXT_TYPES = {".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".py", ".sql", ".md",
               ".ipynb", ".svg"}
_SIGNATURES = {
    ".parquet": (b"PAR1",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".xlsx": (b"PK\x03\x04",),
    ".xls": (b"\xd0\xcf\x11\xe0",),
}


def validate_upload(name: str, data: bytes) -> str:
    """Return a safe file name for ``data`` or raise a 400/413."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    base = posixpath.basename(name.replace("\\", "/"))
    if not base or base in {".", ".."} or base != name.replace("\\", "/").split("/")[-1]:
        raise StorageError("Invalid file name", status_code=400)
    if base.startswith(".") or any(ord(ch) < 32 for ch in base) or len(base) > 200:
        raise StorageError("Invalid file name", status_code=400)
    if "/" in name or "\\" in name:
        raise StorageError("Upload one file at a time, without folders in its name",
                           status_code=400)
    extension = posixpath.splitext(base)[1].lower()
    limit = ALLOWED.get(extension)
    if limit is None:
        raise StorageError(
            "This file type cannot be added to a notebook. Use CSV, Parquet, JSON, Excel, "
            "text, Python, SQL or a notebook.",
            status_code=400,
        )
    if len(data) > limit * _MB:
        raise StorageError(f"{extension} files can be at most {limit} MB", status_code=413)
    signatures = _SIGNATURES.get(extension)
    if signatures and not any(data.startswith(sig) for sig in signatures):
        raise StorageError("The file's content does not match its type", status_code=400)
    if extension in _TEXT_TYPES:
        head = data[:4096]
        if b"\x00" in head:
            raise StorageError("The file's content does not match its type", status_code=400)
        if extension in {".csv", ".tsv", ".txt", ".json"} and head.lstrip()[:15].lower().startswith(
            (b"<!doctype html", b"<html")
        ):
            raise StorageError("The file's content does not match its type", status_code=400)
    return base
