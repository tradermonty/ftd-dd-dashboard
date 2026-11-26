"""Shared fixtures: real-bar fixtures and the ORIGINAL (non-vendored) skill modules.

The originals are taken from the claude-trading-skills checkout at the vendored ref via
`git archive`, so the comparison does not drift with that checkout's working tree.
Set CTS_CHECKOUT to point at a different checkout. Imports are isolated: sys.path and
sys.modules are restored afterwards (only modules loaded from the extracted tree are removed).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
DEFAULT_CHECKOUT = Path.home() / "PycharmProjects" / "claude-trading-skills"


def vendored_ref() -> str:
    return json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())["ref"]


@pytest.fixture(scope="session")
def bars() -> dict[str, dict[str, list[dict]]]:
    data = json.loads((FIXTURES / "bars.json").read_text())
    return data["segments"]


@contextmanager
def isolated_imports(tree: Path):
    saved_path = list(sys.path)
    before = set(sys.modules)
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - before:
            f = getattr(sys.modules[name], "__file__", None)
            if f and str(f).startswith(str(tree)):
                del sys.modules[name]


@pytest.fixture(scope="session")
def original_skills(tmp_path_factory) -> SimpleNamespace:
    checkout = Path(os.environ.get("CTS_CHECKOUT", DEFAULT_CHECKOUT))
    ref = vendored_ref()
    if not (checkout / ".git").exists():
        pytest.skip(f"claude-trading-skills checkout not found at {checkout} (set CTS_CHECKOUT)")
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "archive",
            ref,
            "skills/ftd-detector",
            "skills/ibd-distribution-day-monitor",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip(f"ref {ref} not available in {checkout}: {proc.stderr.decode()[:200]}")
    tree = tmp_path_factory.mktemp("original_skills")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tf:
        tf.extractall(tree, filter="data")

    ftd_dir = tree / "skills" / "ftd-detector" / "scripts"
    dd_dir = tree / "skills" / "ibd-distribution-day-monitor" / "scripts"
    ns = SimpleNamespace(tree=tree, dd_dir=dd_dir, ftd_dir=ftd_dir)

    try:
        with isolated_imports(tree):
            sys.path.insert(0, str(ftd_dir))
            import post_ftd_monitor
            import rally_tracker

            ns.rally_tracker, ns.post_ftd_monitor = rally_tracker, post_ftd_monitor
    except ImportError as exc:
        pytest.skip(f"cannot import original ftd modules: {exc}")

    try:
        with isolated_imports(tree):
            sys.path.insert(0, str(dd_dir))
            import ibd_monitor

            ns.ibd_monitor = ibd_monitor
    except ImportError as exc:
        pytest.skip(f"cannot import original ibd_monitor (missing dependency?): {exc}")
    return ns
