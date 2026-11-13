"""Write a private PG17/TLS test configuration; never start or stop containers."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

import yaml
from psycopg.conninfo import make_conninfo

PG_IMAGE = (
    "postgres:17-bookworm@sha256:665dc7b37ac52a17e298511414c21d23ffa4e6a6ef6225b3e5d59c7afb35e994"
)


def prepare(directory: Path, *, port: int = 55432) -> None:
    directory = directory.resolve()
    repository = Path(__file__).resolve().parents[3]
    if directory.is_relative_to(repository):
        raise ValueError("Fixture credentials must be outside the repository")
    if not 1024 <= port <= 65535:
        raise ValueError("Fixture port must be an unprivileged TCP port")
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    password = secrets.token_urlsafe(32)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{password}")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "7",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,DNS:nova-metadata-db,IP:127.0.0.1",
            "-keyout",
            str(directory / "server.key"),
            "-out",
            str(directory / "server.crt"),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    (directory / "server.key").chmod(0o600)
    configuration = {
        "services": {
            "nova-metadata-db": {
                "image": PG_IMAGE,
                "environment": {
                    "POSTGRES_USER": "nova_fixture_owner",
                    "POSTGRES_PASSWORD": password,
                    "POSTGRES_DB": "nova_control",
                    "POSTGRES_INITDB_ARGS": "--data-checksums",
                },
                "ports": [f"127.0.0.1:{port}:5432"],
                "cpus": 1,
                "mem_limit": "512m",
                "entrypoint": [
                    "bash",
                    "-ceu",
                    "install -d -o postgres -g postgres -m 0700 /run/nova-tls; "
                    "install -o postgres -g postgres -m 0600 /certs/server.key /run/nova-tls/key; "
                    "install -o postgres -g postgres -m 0644 /certs/server.crt /run/nova-tls/cert; "
                    "exec docker-entrypoint.sh postgres -c ssl=on "
                    "-c ssl_key_file=/run/nova-tls/key -c ssl_cert_file=/run/nova-tls/cert",
                ],
                "volumes": [
                    "nova_control_pg:/var/lib/postgresql/data",
                    f"{directory}/server.key:/certs/server.key:ro",
                    f"{directory}/server.crt:/certs/server.crt:ro",
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U nova_fixture_owner -d nova_control"],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                },
                "logging": {"driver": "json-file", "options": {"max-size": "5m", "max-file": "2"}},
            },
        },
        "volumes": {"nova_control_pg": {}},
    }
    dsn = make_conninfo(
        host="localhost",
        port=str(port),
        dbname="nova_control",
        user="nova_fixture_owner",
        password=password,
        sslmode="verify-full",
        sslrootcert=str(directory / "server.crt"),
    )
    for name, content in (
        ("compose.yaml", yaml.safe_dump(configuration, sort_keys=False)),
        ("connection.json", json.dumps({"admin_dsn": dsn}) + "\n"),
    ):
        with (directory / name).open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=55432)
    args = parser.parse_args()
    prepare(args.directory, port=args.port)
