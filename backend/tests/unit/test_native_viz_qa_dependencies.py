"""Release QA NDV-OSS-006: no gated grid or spreadsheet runtime in the frontend.

Native Dataviz replaced AG Grid and the paid Univer packages. This guard keeps
them, and their license-key validation, from coming back through the manifest,
the lockfile or a source import.
"""

import json
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[3] / "frontend"
GATED = re.compile(r"(?<![\w-])(@?ag-grid[\w./-]*|@univerjs/[\w.-]+|univer-pro[\w.-]*)", re.I)
LICENSE = re.compile(r"LicenseManager|setLicenseKey|licenseKey\s*[:=]|univer\.ai|"
                     r"ag-grid\.com/license", re.I)
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".json", ".html"}
SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


def _sources() -> list[Path]:
    roots = [FRONTEND / "src", FRONTEND / "index.html", FRONTEND / "vite.config.ts"]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(path for path in root.rglob("*")
                         if path.is_file() and path.suffix in SOURCE_SUFFIXES)
    return files


def test_the_frontend_tree_is_present():
    assert (FRONTEND / "package.json").is_file()
    assert (FRONTEND / "pnpm-lock.yaml").is_file()
    assert len(_sources()) > 100


def test_the_manifest_declares_no_gated_package():
    manifest = json.loads((FRONTEND / "package.json").read_text())
    declared = [name for section in SECTIONS for name in manifest.get(section, {})]
    assert declared
    assert [name for name in declared if GATED.search(name)] == []
    for key in ("overrides", "resolutions"):
        assert not [name for name in manifest.get(key, {}) if GATED.search(name)]
    assert not [name for name in manifest.get("pnpm", {}).get("overrides", {})
                if GATED.search(name)]


@pytest.mark.parametrize("name", ["pnpm-lock.yaml", "pnpm-workspace.yaml"])
def test_the_lockfile_resolves_no_gated_package(name):
    path = FRONTEND / name
    if not path.is_file():
        pytest.skip(f"{name} is not part of this checkout")
    text = path.read_text()
    assert GATED.findall(text) == []
    assert LICENSE.findall(text) == []


def test_no_source_imports_a_gated_package_or_validates_a_license():
    offenders = []
    for path in _sources():
        text = path.read_text(errors="ignore")
        if GATED.search(text) or LICENSE.search(text):
            offenders.append(str(path.relative_to(FRONTEND)))
    assert offenders == []


@pytest.mark.parametrize("text", [
    'import { AgGridReact } from "ag-grid-react"',
    "import 'ag-grid-community/styles/ag-grid.css'",
    'import { Univer } from "@univerjs/core"',
    'LicenseManager.setLicenseKey("x")',
])
def test_the_guard_recognizes_what_it_forbids(text):
    assert GATED.search(text) or LICENSE.search(text)
