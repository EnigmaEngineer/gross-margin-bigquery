"""Reads and writes the committed measurement files, and refuses a wall time.

A figure this repo publishes has to reproduce on a machine that is not this one. A
count does. An elapsed time does not, so no measurement file here is allowed to
carry one, and the refusal lives in the writer rather than in a check that runs
later. A check that runs later catches the file after it is committed.

The rule is enforced by key name rather than by type, because the thing that goes
wrong is somebody adding `build_seconds` beside a row count and nobody noticing
that one of the two is a property of the machine.
"""

from __future__ import annotations

import json
from pathlib import Path

# Substrings that mean a key is reporting elapsed time. Matched case insensitively
# against every key at every depth. `days` is deliberately absent, because a lag in
# whole days is a property of the calendar and reproduces exactly.
TIME_KEY_PARTS = (
    "_ms",
    "msec",
    "millis",
    "second",
    "minute",
    "hour",
    "elapsed",
    "duration",
    "wall",
    "runtime",
    "timing",
    "latency",
)


def wall_time_keys(payload, prefix: str = "") -> list[str]:
    """Dotted paths of every key that reads as elapsed time."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if any(part in lowered for part in TIME_KEY_PARTS):
                found.append(path)
            found.extend(wall_time_keys(value, path))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            found.extend(wall_time_keys(value, f"{prefix}[{index}]"))
    return found


def write(path: Path, payload: dict) -> None:
    offenders = wall_time_keys(payload)
    if offenders:
        raise ValueError(
            "a measurement file may not carry an elapsed time, found "
            f"{offenders}. Publish a count or a whole number of days instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"no measurement file at {path}")
    return json.loads(path.read_text(encoding="utf-8"))
