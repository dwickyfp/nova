"""Sample the BI services' CPU and memory, and record image sizes.

Usage (repository root)::

    python superset/bench/resources.py --minutes 10 --label idle --out raw/idle.json

Two memory views are recorded per container, because they answer different
questions:

* ``docker_mem_mib``: what ``docker stats`` reports (cgroup usage minus
  inactive file cache). It is what a memory limit is enforced against.
* ``rss_mib`` / ``pss_mib``: the sum over the container's processes of
  ``/proc/<pid>/smaps_rollup``. PSS splits shared pages fairly between
  prefork children, so it does not double-count copy-on-write memory.
* ``swap_pss_mib`` and ``footprint_mib`` (PSS + SwapPss): a VM under memory
  pressure (OrbStack, Docker Desktop) swaps idle pages out, so RSS alone can
  show a 200 MiB process as 20 MiB. The footprint is the comparable number.

CPU is ``docker stats`` CPU% (100 = one core) sampled every ``--interval``.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

CONTAINERS = ("nova-superset", "nova-superset-worker", "nova-superset-beat", "nova-superset-db")
IMAGES = ("nova-superset:local", "apache/superset:6.1.0", "postgres:16.10-alpine", "redis:7-alpine")

_PROC = (
    "for p in /proc/[0-9]*; do cat $p/smaps_rollup 2>/dev/null; done | "
    "awk '/^Rss:/{r+=$2} /^Pss:/{p+=$2} /^SwapPss:/{s+=$2} END{print r+0, p+0, s+0}'"
)


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _mib(text: str) -> float:
    value = text.strip()
    for unit, scale in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * scale
    return float(value)


def running(containers: tuple[str, ...]) -> list[str]:
    names = set(_run("docker", "ps", "--format", "{{.Names}}").split())
    return [name for name in containers if name in names]


def sample(containers: list[str]) -> dict[str, dict[str, float]]:
    stats = _run(
        "docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
        *containers,
    )
    out: dict[str, dict[str, float]] = {}
    for line in stats.strip().splitlines():
        name, cpu, mem = line.split("\t")
        out[name] = {"cpu_pct": float(cpu.rstrip("%")), "docker_mem_mib": _mib(mem.split("/")[0])}
    for name in containers:
        try:
            rss, pss, swap = _run("docker", "exec", name, "sh", "-c", _PROC).split()
            out[name] |= {
                "rss_mib": int(rss) / 1024, "pss_mib": int(pss) / 1024,
                "swap_pss_mib": int(swap) / 1024, "footprint_mib": (int(pss) + int(swap)) / 1024,
            }
        except (subprocess.CalledProcessError, ValueError):
            pass
    return out


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 2),
        "p95": round(p95, 2),
        "max": round(ordered[-1], 2),
    }


def image_sizes() -> dict[str, float | None]:
    sizes: dict[str, float | None] = {}
    for image in IMAGES:
        try:
            sizes[image] = round(int(_run("docker", "image", "inspect", image, "--format", "{{.Size}}")) / 1024**2, 1)
        except subprocess.CalledProcessError:
            sizes[image] = None
    for extra in ("nova-superset-jobs:local",):
        try:
            sizes[extra] = round(int(_run("docker", "image", "inspect", extra, "--format", "{{.Size}}")) / 1024**2, 1)
        except subprocess.CalledProcessError:
            pass
    return sizes


def environment() -> dict[str, object]:
    info = json.loads(_run("docker", "info", "--format", "{{json .}}"))
    host: dict[str, object] = {"platform": platform.platform(), "machine": platform.machine()}
    try:
        host["cpu"] = _run("sysctl", "-n", "machdep.cpu.brand_string").strip()
        host["host_mem_gib"] = round(int(_run("sysctl", "-n", "hw.memsize")) / 1024**3, 1)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    digests = {}
    for image in IMAGES[:1]:
        try:
            digests[image] = _run("docker", "image", "inspect", image, "--format", "{{.Id}}").strip()
        except subprocess.CalledProcessError:
            digests[image] = None
    return {
        "host": host,
        "docker": {
            "server_version": info.get("ServerVersion"),
            "vm_cpus": info.get("NCPU"),
            "vm_mem_gib": round(int(info.get("MemTotal", 0)) / 1024**3, 1),
            "os": info.get("OperatingSystem"),
        },
        "image_ids": digests,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--label", default="idle")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    containers = running(CONTAINERS)
    series: dict[str, list[dict[str, float]]] = {name: [] for name in containers}
    started = datetime.now(UTC)
    deadline = time.monotonic() + args.minutes * 60
    while time.monotonic() < deadline:
        tick = time.monotonic()
        for name, values in sample(containers).items():
            series[name].append(values)
        time.sleep(max(0.0, args.interval - (time.monotonic() - tick)))

    per_container = {}
    for name, rows in series.items():
        per_container[name] = {
            key: summary([row[key] for row in rows if key in row])
            for key in ("cpu_pct", "docker_mem_mib", "rss_mib", "pss_mib", "swap_pss_mib", "footprint_mib")
            if any(key in row for row in rows)
        }
    totals = {}
    for key in ("cpu_pct", "docker_mem_mib", "pss_mib", "footprint_mib"):
        count = min((len(rows) for rows in series.values()), default=0)
        totals[key] = summary(
            [sum(series[name][i].get(key, 0.0) for name in containers) for i in range(count)]
        ) if count else {}
    result = {
        "label": args.label,
        "started_at": started.isoformat(),
        "minutes": args.minutes,
        "interval_s": args.interval,
        "containers": containers,
        "per_container": per_container,
        "total": totals,
        "images_mib": image_sizes(),
        "environment": environment(),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"label": args.label, "total": totals}, indent=2))


if __name__ == "__main__":
    main()
