#!/usr/bin/env python3
"""Vendor the FTD / DD decision modules from a claude-trading-skills checkout.

Files are read with `git show <ref>:<path>` (committed blobs only, never the working
tree) and written byte-for-byte. VENDOR.lock records the source ref and SHA-256s.

Usage:
    python scripts/sync_vendor.py --src <checkout> --ref <git sha>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# source path in claude-trading-skills -> path under vendor/
FILE_MAP: dict[str, str] = {
    "skills/ftd-detector/scripts/rally_tracker.py": "ftd_detector/rally_tracker.py",
    "skills/ftd-detector/scripts/post_ftd_monitor.py": "ftd_detector/post_ftd_monitor.py",
    "skills/ibd-distribution-day-monitor/scripts/distribution_day_tracker.py": (
        "ibd_dd/distribution_day_tracker.py"
    ),
    "skills/ibd-distribution-day-monitor/scripts/models.py": "ibd_dd/models.py",
    "skills/ibd-distribution-day-monitor/scripts/history_utils.py": "ibd_dd/history_utils.py",
    "skills/ibd-distribution-day-monitor/scripts/risk_classifier.py": "ibd_dd/risk_classifier.py",
    "skills/ibd-distribution-day-monitor/scripts/math_utils.py": "ibd_dd/math_utils.py",
}


def _git(src: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True).stdout


def sync(src: Path, ref: str, dest_root: Path = ROOT) -> dict:
    src = Path(src)
    full_ref = _git(src, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    remote = ""
    try:
        remote = _git(src, "config", "--get", "remote.origin.url").decode().strip()
    except subprocess.CalledProcessError:
        pass

    files = []
    for srcpath, rel in FILE_MAP.items():
        blob = _git(src, "show", f"{full_ref}:{srcpath}")
        out = dest_root / "vendor" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
        files.append(
            {
                "source": srcpath,
                "dest": f"vendor/{rel}",
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )
    lock = {"source_repo": remote or "claude-trading-skills", "ref": full_ref, "files": files}
    (dest_root / "vendor" / "VENDOR.lock").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return lock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path, help="claude-trading-skills checkout")
    ap.add_argument("--ref", required=True, help="git commit SHA to vendor from")
    args = ap.parse_args(argv)
    lock = sync(args.src, args.ref)
    print(f"vendored {len(lock['files'])} files from {lock['ref']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
