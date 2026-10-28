#!/usr/bin/env python3
"""macOS launchd agent for the snapshot job at config.yaml's schedule.run_times_et, weekdays.

Prints the plist by default. `--install` writes it to ~/Library/LaunchAgents and (re)loads it;
`--uninstall` removes it. launchd runs a missed slot when the Mac wakes; run_snapshot.py itself
skips market holidays. Output goes to data/snapshot.log.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from calscan.settings import REPO_ROOT, AppConfig  # noqa: E402

LABEL = "com.calscan.snapshot"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ET = ZoneInfo("America/New_York")


def build_plist(run_times_et: list[str], uv: str) -> bytes:
    intervals = []
    for t in run_times_et:
        # launchd fires in the Mac's local time zone; convert from ET (identity on an ET Mac)
        local = datetime.combine(date.today(), time.fromisoformat(t), tzinfo=ET).astimezone()
        intervals += [
            {"Weekday": weekday, "Hour": local.hour, "Minute": local.minute}
            for weekday in range(1, 6)  # Monday-Friday
        ]
    log = str(REPO_ROOT / "data" / "snapshot.log")
    script = str(REPO_ROOT / "scripts" / "run_snapshot.py")
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [uv, "run", "python", script],
            "WorkingDirectory": str(REPO_ROOT),
            "StartCalendarInterval": intervals,
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "EnvironmentVariables": {"PATH": f"{Path(uv).parent}:/usr/bin:/bin"},
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    domain = f"gui/{os.getuid()}"
    if args.uninstall:
        subprocess.run(["launchctl", "bootout", domain, str(PLIST_PATH)], check=False)
        PLIST_PATH.unlink(missing_ok=True)
        print(f"Removed {PLIST_PATH}")
        return

    uv = shutil.which("uv")
    if uv is None:
        sys.exit("uv not found on PATH")
    plist = build_plist(AppConfig.load().schedule.run_times_et, uv)
    if not args.install:
        sys.stdout.write(plist.decode())
        return

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    # unload any previous version first; on a first install there's none, and launchctl's
    # "Boot-out failed: 5" for that case is noise
    subprocess.run(
        ["launchctl", "bootout", domain, str(PLIST_PATH)], check=False, stderr=subprocess.DEVNULL
    )
    PLIST_PATH.write_bytes(plist)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], check=True)
    print(f"Installed and loaded {PLIST_PATH}")


if __name__ == "__main__":
    main()
