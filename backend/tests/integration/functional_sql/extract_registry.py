"""Capture builtin definitions from an explicitly selected isolated FE container."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from .engine_overloads import REGISTRY_PATH, definition_key
from .inventory import ENGINE_COMMIT, SOURCE_ROOT


def command(arguments: list[str], *, timeout: int = 60) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout


def extract(container: str, source: Path, destination: Path) -> None:
    pin = json.loads(REGISTRY_PATH.read_text())
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_digest != pin["function_set_sha256"]:
        raise RuntimeError("FunctionSet source differs from the researched engine pin")
    inspection = json.loads(command(["docker", "inspect", container]))[0]
    labels = inspection.get("Config", {}).get("Labels", {}) or {}
    project = labels.get("com.docker.compose.project", "")
    if "test" not in project:
        raise RuntimeError(
            "Registry extraction requires an explicitly selected test Compose project"
        )
    inspector = Path(__file__).with_name("DumpFunctions.java")
    directory = "/tmp/nova-registry-" + uuid4().hex
    command(["docker", "exec", container, "mkdir", directory])
    try:
        command(
            ["docker", "cp", str(inspector), container + ":" + directory + "/DumpFunctions.java"]
        )
        command(
            [
                "docker",
                "exec",
                container,
                "javac",
                "-J-Xmx64m",
                "-classpath",
                directory,
                directory + "/DumpFunctions.java",
            ]
        )
        raw = command(
            [
                "docker",
                "exec",
                container,
                "java",
                "-Xmx128m",
                "-cp",
                directory + ":/opt/starrocks/fe/lib/*",
                "DumpFunctions",
            ]
        )
        rows = json.loads(raw)
        unique = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) - {
                "name",
                "kind",
                "fid",
                "arguments",
                "return_type",
                "varargs",
                "visible",
                "state_descriptor",
                "catalog",
            }:
                raise RuntimeError("Unexpected builtin definition fields")
            key = definition_key(row)
            unique[key] = {**row, "logical_key": key}
        jars = ["fe-core-4.1.4.jar", "fe-type-4.1.4.jar", "fe-parser-4.1.4.jar"]
        output = command(
            [
                "docker",
                "exec",
                container,
                "sha256sum",
                *["/opt/starrocks/fe/lib/" + name for name in jars],
            ]
        )
        checksums = {
            line.split()[1].rsplit("/", 1)[-1]: line.split()[0] for line in output.splitlines()
        }
        if checksums != pin["jar_sha256"]:
            raise RuntimeError("FE jars differ from the researched engine pin")
        document = {
            "engine_commit": ENGINE_COMMIT,
            "engine_version": "4.1.4-4a9848e",
            "source": SOURCE_ROOT
            + "/fe/fe-core/src/main/java/com/starrocks/catalog/FunctionSet.java",
            "function_set_sha256": source_digest,
            "inspector_sha256": hashlib.sha256(inspector.read_bytes()).hexdigest(),
            "extraction": (
                "FunctionSet.init() in the pinned FE image; immutable builtin definitions only"
            ),
            "jar_sha256": checksums,
            "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "records": sorted(unique.values(), key=lambda row: (row["name"], row["logical_key"])),
        }
        destination.write_text(json.dumps(document, indent=2) + "\n")
        print(f"Captured {len(unique)} builtin definitions to {destination}")
    finally:
        command(["docker", "exec", container, "rm", "-rf", directory])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--function-set-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    extract(args.container, args.function_set_source, args.output)


if __name__ == "__main__":
    main()
