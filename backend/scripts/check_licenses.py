"""Fail when the installed Python tree or the backend source needs a non-open license.

Two checks, run against the environment ``uv sync --locked`` produced:

* every installed distribution (direct and transitive, development tools
  included) declares a license on the allowlist below, or is a named,
  reviewed exception;
* no dependency is a known commercial or open-core package, and no backend
  source uses a license-key or trial API, so no required feature depends on
  an enterprise key, a trial or a source-available license.

Usage (from backend/):
    uv run python scripts/check_licenses.py [--report path.json]

Standard library only. Exit status 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: SPDX identifiers that may appear anywhere in the tree.
ALLOWED = frozenset({
    "0BSD", "Apache-2.0", "BlueOak-1.0.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0",
    "CNRI-Python", "ISC", "MIT", "MIT-CMU", "MPL-2.0", "PSF-2.0", "Python-2.0",
    "Unlicense", "Zlib",
})
#: Allowed after ``WITH``.
EXCEPTIONS = frozenset({"LLVM-exception"})
#: A license accepted only for the named packages, with the review that accepted it.
REVIEWED = {
    "LGPL-3.0-only": ({"psycopg", "psycopg-binary", "psycopg-pool"},
                      "PostgreSQL driver used unmodified as a separate library; "
                      "LGPL allows linking from non-LGPL code."),
}
#: Free-text License fields and trove classifiers, mapped to SPDX.
ALIASES = {
    "mit license": "MIT", "mit": "MIT", "mit style": "MIT", "apache 2.0": "Apache-2.0",
    "apache license": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "apache software license": "Apache-2.0",
    "apache software license 2.0": "Apache-2.0", "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause", "bsd 3-clause license": "BSD-3-Clause",
    "2-clause bsd": "BSD-2-Clause", "isc license (iscl)": "ISC",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "python software foundation license": "PSF-2.0",
}
#: Licenses that are never acceptable, even as a reviewed exception.
DENIED = re.compile(
    r"BUSL|Business Source|SSPL|Elastic|Commons Clause|PolyForm|Proprietary|Commercial|"
    r"UNLICENSED|AGPL|(?<!L)GPL|CC-BY-NC|Confluent", re.IGNORECASE)
#: Commercial or open-core packages whose features need a key, trial or paid license.
COMMERCIAL_PACKAGES = re.compile(
    r"^(highcharts|plotly-enterprise|dash-enterprise|anychart|fusioncharts|"
    r"ag-grid|handsontable|streamlit-aggrid|syncfusion|devexpress)", re.IGNORECASE)
LICENSE_KEY_API = re.compile(
    r"set_?license_?key|LicenseManager|register_?license|license_?key\s*=", re.IGNORECASE)


def _single(text: str) -> str | None:
    """Map one free-text License field or classifier to SPDX, if recognisable."""
    value = text.strip()
    if value in ALLOWED or value in REVIEWED:
        return value
    return ALIASES.get(value.lower())


def declared(metadata) -> str | None:
    """The distribution's license as an SPDX expression, or None when unknown."""
    expression = metadata.get("License-Expression")
    if expression:
        return expression.strip()
    field = (metadata.get("License") or "").strip()
    if field:
        # A short SPDX expression, a known name, or full text titled with one.
        first = field.splitlines()[0].strip()
        known = {*ALLOWED, *REVIEWED, *EXCEPTIONS, "AND", "OR", "WITH", "(", ")"}
        if "\n" not in field and all(token in known for token in _tokens(field)):
            return field
        mapped = _single(first)
        if mapped:
            return mapped
    classifiers = [item.split(" :: ")[-1] for item in metadata.get_all("Classifier") or []
                   if item.startswith("License :: OSI Approved")]
    mapped = [_single(item) for item in classifiers]
    if mapped and all(mapped):
        return " OR ".join(sorted(set(filter(None, mapped))))
    return None


def _tokens(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[A-Za-z0-9.+-]+", expression)


def accepted(expression: str, package: str) -> bool:
    """Evaluate an SPDX expression: OR needs one accepted side, AND needs both."""
    tokens = _tokens(expression)
    position = 0

    def term() -> bool:
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "(":
            value = either()
            position += 1  # ")"
        else:
            value = token in ALLOWED or (
                token in REVIEWED and package.lower() in REVIEWED[token][0])
        if position < len(tokens) and tokens[position] == "WITH":
            value = value and tokens[position + 1] in EXCEPTIONS
            position += 2
        return value

    def both() -> bool:
        nonlocal position
        value = term()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            value = term() and value
        return value

    def either() -> bool:
        nonlocal position
        value = both()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            value = both() or value
        return value

    try:
        return either() and position == len(tokens)
    except IndexError:
        return False


def project_name() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    match = re.search(r'^name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    own = project_name().lower()
    findings: list[str] = []
    licenses: dict[str, str] = {}
    exceptions: dict[str, str] = {}
    for distribution in distributions():
        name = distribution.metadata["Name"]
        if not name or name.lower() == own:
            continue
        label = f"{name}=={distribution.version}"
        expression = declared(distribution.metadata)
        if COMMERCIAL_PACKAGES.match(name):
            findings.append(f"{label}: commercial or open-core package")
        if expression is None:
            findings.append(f"{label}: no recognisable license metadata")
            continue
        licenses[label] = expression
        if DENIED.search(expression) and not any(
                token in REVIEWED and name.lower() in REVIEWED[token][0]
                for token in _tokens(expression)):
            findings.append(f"{label}: prohibited license {expression}")
        elif not accepted(expression, name):
            findings.append(f"{label}: license {expression} is not on the allowlist")
        for token in _tokens(expression):
            if token in REVIEWED and name.lower() in REVIEWED[token][0]:
                exceptions[label] = f"{token}: {REVIEWED[token][1]}"
    for path in sorted((ROOT / "app").rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if LICENSE_KEY_API.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{number}: license-key API")
    report = {
        "ecosystem": "pypi", "python": sys.version.split()[0],
        "distributions": len(licenses), "findings": findings,
        "reviewed_exceptions": exceptions, "licenses": dict(sorted(licenses.items())),
        "result": "FAIL" if findings else "PASS",
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    for finding in findings:
        print(f"FAIL {finding}")
    print(f"{report['result']}: {len(licenses)} Python distributions checked, "
          f"{len(exceptions)} reviewed exceptions, {len(findings)} findings")
    raise SystemExit(1 if findings else 0)


if __name__ == "__main__":
    main()
