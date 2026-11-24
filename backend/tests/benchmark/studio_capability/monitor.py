"""Hold new benchmark turns while the engine containers are near their memory limit.

    uv run python -m tests.benchmark.studio_capability.monitor [--high 85 --low 75]

Every interval it reads ``docker stats`` for the StarRocks FE and BE containers,
appends a line to ``<out>/docker_stats.log`` and creates ``<out>/PAUSE`` when
either is above ``--high`` percent of its memory limit; the file is removed once
both are below ``--low``. The runner starts no new turn while the file exists.
"""

from __future__ import annotations

import argparse
import subprocess
import time

from tests.benchmark.studio_capability import settings

CONTAINERS = ("nova-starrocks-fe", "nova-starrocks-be")


def sample() -> dict[str, tuple[float, float]]:
    """Memory and CPU percent per watched container."""
    output = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemPerc}} {{.CPUPerc}}",
         *CONTAINERS], capture_output=True, text=True, timeout=60, check=False).stdout
    out: dict[str, tuple[float, float]] = {}
    for line in output.splitlines():
        name, mem, cpu = line.split()
        out[name] = (float(mem.rstrip("%")), float(cpu.rstrip("%")))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out")
    parser.add_argument("--high", type=float, default=85.0)
    parser.add_argument("--low", type=float, default=75.0)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    out = settings.out_dir(args.out)
    pause = out / "PAUSE"
    log = out / "docker_stats.log"
    while True:
        try:
            stats = sample()
        except (subprocess.SubprocessError, ValueError) as exc:
            stats = {}
            note = f"stats unavailable: {type(exc).__name__}"
        else:
            note = ""
        peak = max((mem for mem, _ in stats.values()), default=0.0)
        if peak >= args.high and not pause.exists():
            pause.write_text(f"memory {peak}%\n")
            note = "paused"
        elif pause.exists() and stats and peak < args.low:
            pause.unlink()
            note = "resumed"
        with log.open("a") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} "
                         + " ".join(f"{k}={m:.1f}%/{c:.0f}%cpu" for k, (m, c) in stats.items())
                         + (f" {note}" if note else "") + "\n")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
