"""Build the checksum-pinned PgBouncer used by the disposable PG acceptance lane."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "1.26.1"
SHA256 = "00886a2994b4844cd37110970df777772cfc42a74c400522c82fba509a3f42d9"
URL = (
    "https://github.com/pgbouncer/pgbouncer/releases/download/"
    "pgbouncer_1_26_1/pgbouncer-1.26.1.tar.gz"
)


def build(destination: Path, *, openssl_prefix: Path | None = None) -> None:
    if openssl_prefix is not None and not openssl_prefix.is_dir():
        raise ValueError("OpenSSL prefix must be an existing directory")
    configure = "--with-openssl" if openssl_prefix is None else f"--with-openssl={openssl_prefix}"
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="nova-pooler-source-") as temporary:
        with urllib.request.urlopen(URL, timeout=30) as response:
            archive = response.read(8 * 1024 * 1024 + 1)
        if len(archive) > 8 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != SHA256:
            raise RuntimeError("PgBouncer source checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            source.extractall(temporary, filter="data")
        directory = Path(temporary) / f"pgbouncer-{VERSION}"
        with (destination / "build.log").open("wb") as log:
            subprocess.run(
                ["./configure", configure], cwd=directory, stdout=log,
                stderr=subprocess.STDOUT, check=True, timeout=180,
            )
            subprocess.run(
                ["make", "-j2", "pgbouncer"], cwd=directory, stdout=log,
                stderr=subprocess.STDOUT, check=True, timeout=180,
            )
        shutil.copy2(directory / "pgbouncer", destination / "pgbouncer")
    (destination / "source.json").write_text(json.dumps({
        "version": VERSION, "url": URL, "sha256": SHA256,
    }, indent=2) + "\n")
    print(f"Built checksum-verified PgBouncer {VERSION}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--openssl-prefix", type=Path)
    arguments = parser.parse_args()
    build(arguments.destination.resolve(), openssl_prefix=arguments.openssl_prefix)
