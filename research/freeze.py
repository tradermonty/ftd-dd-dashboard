"""Freeze of the variant grids (L2) and what is known about when it happened.

The two variant grids, of the selection research R (docs/plan.md section 3.6) and of R2
(section 3.7), were WRITTEN in docs/plan.md before the computations. The hash file
`research/grid_freeze.json` that fixes them for the publish gate was created LATER, after
several runs (for R after the runs that already produced variants.csv, for R2 after the runs
that already produced overlay_variants.csv). So the freeze is a freeze of a grid that had not
changed, not a record made before the first run. `provenance` below measures this from the
files of the earlier runs: it counts the runs made before the freeze and says whether their
variants outputs were identical. It is computed once, when the freeze file is written (or by
`record_provenance`), and stored in the freeze file, so deleting old runs does not change it.

Each grid is hashed (sha256 of its canonical JSON); the hash and the pre-registration date are
stored in selected.json / overlay_selected.json and in `research/grid_freeze.json`. The publish
gate fails when the code's current hash differs from the frozen one, unless
`--allow-grid-change` is passed (and then it records the change).

Dates: `pre_registered` and `frozen_on` are local dates (Pacific time, UTC-8 in November).
`frozen_at_utc` is the same moment in UTC, which can already be the next day:
2026-11-07 20:06:20 PST is 2026-11-08T04:06:20Z.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / "research" / "grid_freeze.json"
PRE_REGISTRATION_DATE = "2026-11-07"
KINDS = ("selection", "overlay")
PROVENANCE_FILES = {
    "selection": "research/variants.csv",
    "overlay": "research/overlay_variants.csv",
}
LOCAL_UTC_OFFSET_HOURS = -8  # Pacific standard time, the zone of the dates above


def grid_sha256(grid: dict) -> str:
    text = json.dumps(grid, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def current_grids() -> dict[str, dict]:
    from research import overlay, variants

    return {"selection": variants.grid_definition(), "overlay": overlay.grid_definition()}


def current_hashes() -> dict[str, str]:
    return {k: grid_sha256(g) for k, g in current_grids().items()}


def local_date_of(frozen_at_utc: str) -> str:
    """The local (Pacific, UTC-8) calendar date of a freeze timestamp."""
    dt = datetime.strptime(frozen_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    return (dt + timedelta(hours=LOCAL_UTC_OFFSET_HOURS)).date().isoformat()


def info(kind: str) -> dict:
    """Block stored in selected.json / overlay_selected.json."""
    frozen = load() or {}
    return {
        "grid_sha256": current_hashes()[kind],
        "pre_registered": PRE_REGISTRATION_DATE,
        "frozen_at_utc": frozen.get("frozen_at_utc"),
        "frozen_file": "research/grid_freeze.json",
    }


def _file_digests(path: Path) -> dict:
    data = path.read_bytes()
    body = data.split(b"\n", 1)[1] if b"\n" in data else b""
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows_sha256": hashlib.sha256(body).hexdigest(),
    }


def compute_provenance(runs_dir: Path | None, frozen_at_utc: str | None) -> dict:
    """What the runs made BEFORE the freeze show about each grid's variants file.

    A run counts as made before the freeze when its name (its UTC start time,
    YYYYmmddTHHMMSSZ) is strictly earlier than the freeze timestamp: a run named in the same
    second as the freeze began at that moment, after the freeze file was written. Per kind:
    the runs with their sha256 (whole file) and rows_sha256 (everything after the header line),
    `identical_bytes`, and `identical_rows`, true when only the header line differs between
    the files, for example after a column was renamed.

    Computed from the files, never typed in."""
    stamp = frozen_at_utc.replace("-", "").replace(":", "") if frozen_at_utc else None
    out: dict = {
        "rule": "runs whose name (UTC start time) is earlier than frozen_at_utc",
        "frozen_at_utc": frozen_at_utc,
        "files": {},
    }
    for kind, rel in PROVENANCE_FILES.items():
        runs = []
        if runs_dir is not None and Path(runs_dir).is_dir() and stamp:
            for d in sorted(Path(runs_dir).iterdir()):
                f = d / rel
                if d.is_dir() and f.is_file() and d.name < stamp:
                    runs.append({"run": d.name, **_file_digests(f)})
        out["files"][kind] = {
            "file": rel,
            "n_runs": len(runs),
            "first_run": runs[0]["run"] if runs else None,
            "runs": runs,
            "identical_bytes": len({r["sha256"] for r in runs}) == 1 if runs else None,
            "identical_rows": len({r["rows_sha256"] for r in runs}) == 1 if runs else None,
        }
    return out


def stored_provenance() -> dict | None:
    """The provenance block of the freeze file (None when it has none yet)."""
    return (load() or {}).get("provenance")


def record_provenance(runs_dir: Path, path: Path = FREEZE_PATH, *, replace: bool = False) -> dict:
    """Compute the provenance from `runs_dir` and store it in the freeze file. An existing block
    is kept unless replace=True (it describes the past and must not change when runs are
    deleted)."""
    data = load(path)
    if data is None:
        raise FileNotFoundError(path)
    if data.get("provenance") and not replace:
        return data["provenance"]
    data["provenance"] = compute_provenance(runs_dir, data.get("frozen_at_utc"))
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data["provenance"]


def load(path: Path = FREEZE_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write(
    path: Path = FREEZE_PATH,
    *,
    previous: dict | None = None,
    note: str = "",
    runs_dir: Path | None = None,
) -> dict:
    """Write the freeze file from the current code; keep the old entry in `history`.

    `runs_dir`: earlier runs, from which the provenance (runs made before this freeze) is
    computed and stored."""
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "pre_registered": PRE_REGISTRATION_DATE,
        "frozen_on": local_date_of(stamp),
        "frozen_at_utc": stamp,
        "grid_sha256": current_hashes(),
        "history": (previous or {}).get("history", []),
        "note": note or "variant grids hashed from research.variants / research.overlay",
        "provenance": compute_provenance(runs_dir, stamp),
    }
    if previous:
        data["history"] = [
            *data["history"],
            {k: previous.get(k) for k in ("frozen_on", "frozen_at_utc", "grid_sha256")},
        ]
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data
