#!/usr/bin/env python3
"""Log NVMe SMART wear and alert on runaway writes.

Reads `smartctl -j -A -H <device>`, appends a snapshot to a JSONL
history file, then compares against history to compute a 24h write
delta and raise alerts when thresholds are crossed.

Exit code: 0 = OK, 1 = WARN, 2 = ERROR. Alert lines are emitted on
stdout as JSON objects so downstream (cron MAILTO, Telegram wrapper,
MC) can parse them without re-running smartctl.

Run daily via cron:
    0 6 * * * root /usr/bin/python3 /home/enrico/pi-cluster/scripts/nvme_wear_log.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

# NVMe spec: one "data unit" = 1000 * 512 bytes = 512,000 bytes.
BYTES_PER_DATA_UNIT = 512_000

# Thresholds. WARN_DELTA_BYTES is ~1.5x the 17 GB/day lifetime average
# observed on heavy's KY3100-512G — trips on sustained regressions but
# not routine days.
WARN_DELTA_BYTES = 25 * 1024**3
ERROR_PCT_USED = 80


@dataclass(frozen=True)
class Snapshot:
    ts: datetime
    device: str
    data_units_written: int
    percentage_used: int
    media_errors: int
    critical_warning: int
    smart_passed: bool
    temperature: Optional[int]

    @property
    def bytes_written(self) -> int:
        return self.data_units_written * BYTES_PER_DATA_UNIT

    def to_row(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "device": self.device,
            "data_units_written": self.data_units_written,
            "percentage_used": self.percentage_used,
            "media_errors": self.media_errors,
            "critical_warning": self.critical_warning,
            "smart_passed": self.smart_passed,
            "temperature": self.temperature,
        }

    @classmethod
    def from_smartctl_json(cls, payload: dict, device: str, ts: datetime) -> "Snapshot":
        log = payload.get("nvme_smart_health_information_log", {})
        status = payload.get("smart_status", {})
        return cls(
            ts=ts,
            device=device,
            data_units_written=int(log.get("data_units_written", 0)),
            percentage_used=int(log.get("percentage_used", 0)),
            media_errors=int(log.get("media_errors", 0)),
            critical_warning=int(log.get("critical_warning", 0)),
            smart_passed=bool(status.get("passed", False)),
            temperature=log.get("temperature"),
        )


def run_smartctl(device: str) -> dict:
    result = subprocess.run(
        ["smartctl", "-j", "-A", "-H", device],
        capture_output=True,
        text=True,
        check=False,
    )
    # smartctl uses non-zero exit codes to signal advisory conditions
    # even when the JSON body is valid — we ignore returncode and parse.
    if not result.stdout:
        raise RuntimeError(f"smartctl produced no output: {result.stderr.strip()}")
    return json.loads(result.stdout)


def read_history(path: Path) -> list[Snapshot]:
    if not path.exists():
        return []
    rows: list[Snapshot] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            rows.append(
                Snapshot(
                    ts=datetime.fromisoformat(row["ts"]),
                    device=row["device"],
                    data_units_written=int(row["data_units_written"]),
                    percentage_used=int(row["percentage_used"]),
                    media_errors=int(row["media_errors"]),
                    critical_warning=int(row["critical_warning"]),
                    smart_passed=bool(row["smart_passed"]),
                    temperature=row.get("temperature"),
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Corrupt line: skip rather than crash the monitor.
            continue
    return rows


def append_history(path: Path, snapshot: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(snapshot.to_row()) + "\n")


def find_baseline(history: Iterable[Snapshot], now: datetime, min_age: timedelta) -> Optional[Snapshot]:
    """Oldest snapshot within [now - 2*min_age, now - min_age]."""
    window_start = now - 2 * min_age
    window_end = now - min_age
    candidates = [s for s in history if window_start <= s.ts <= window_end]
    return min(candidates, key=lambda s: s.ts) if candidates else None


def evaluate(current: Snapshot, history: list[Snapshot]) -> list[dict]:
    """Return list of alert dicts. Empty list = OK."""
    alerts: list[dict] = []

    if not current.smart_passed:
        alerts.append({"level": "ERROR", "reason": "smart_status_failed"})
    if current.critical_warning != 0:
        alerts.append(
            {"level": "ERROR", "reason": "critical_warning", "value": current.critical_warning}
        )
    if current.media_errors > 0:
        alerts.append(
            {"level": "ERROR", "reason": "media_errors", "value": current.media_errors}
        )
    if current.percentage_used >= ERROR_PCT_USED:
        alerts.append(
            {
                "level": "ERROR",
                "reason": "percentage_used",
                "value": current.percentage_used,
                "threshold": ERROR_PCT_USED,
            }
        )

    baseline_24h = find_baseline(history, current.ts, timedelta(hours=24))
    if baseline_24h:
        delta = current.bytes_written - baseline_24h.bytes_written
        if delta > WARN_DELTA_BYTES:
            alerts.append(
                {
                    "level": "WARN",
                    "reason": "write_delta_24h",
                    "value_bytes": delta,
                    "threshold_bytes": WARN_DELTA_BYTES,
                    "baseline_ts": baseline_24h.ts.isoformat(),
                }
            )

    return alerts


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/nvme0n1")
    parser.add_argument(
        "--history",
        type=Path,
        default=Path(os.environ.get("NVME_WEAR_HISTORY", "/home/enrico/data/nvme_wear.jsonl")),
    )
    parser.add_argument("--dry-run", action="store_true", help="don't append to history")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    payload = run_smartctl(args.device)
    current = Snapshot.from_smartctl_json(payload, args.device, now)
    history = read_history(args.history)

    alerts = evaluate(current, history)

    if not args.dry_run:
        append_history(args.history, current)

    event = {
        "ts": now.isoformat(),
        "device": args.device,
        "percentage_used": current.percentage_used,
        "data_units_written": current.data_units_written,
        "bytes_written": current.bytes_written,
        "alerts": alerts,
    }
    print(json.dumps(event))

    if any(a["level"] == "ERROR" for a in alerts):
        return 2
    if any(a["level"] == "WARN" for a in alerts):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
