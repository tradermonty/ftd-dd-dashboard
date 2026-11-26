"""P1: vendor integrity, sync script, and loader isolation. No network."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_vendor  # noqa: E402

from engine import loader  # noqa: E402

PINNED_REF = "a1d65fd4500051e267863a7d2229cf66d5f5fed5"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_lock_hashes_match_files():
    lock = json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())
    assert lock["ref"] == PINNED_REF
    assert len(lock["files"]) == 7
    for entry in lock["files"]:
        path = ROOT / entry["dest"]
        assert path.exists(), entry["dest"]
        assert _sha(path) == entry["sha256"], f"hash drift: {entry['dest']}"
    dests = {e["dest"] for e in lock["files"]}
    assert dests == {f"vendor/{rel}" for rel in sync_vendor.FILE_MAP.values()}


def test_no_unlisted_files_in_vendor():
    lock = json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())
    listed = {e["dest"] for e in lock["files"]}
    actual = {
        str(p.relative_to(ROOT))
        for p in (ROOT / "vendor").rglob("*.py")
        if "__pycache__" not in p.parts
    }
    assert actual == listed


def test_sync_vendor_copies_via_git_show(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git = ["git", "-C", str(src)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "config", "user.email", "t@example.com"], check=True)
    subprocess.run([*git, "config", "user.name", "t"], check=True)
    for srcpath in sync_vendor.FILE_MAP:
        f = src / srcpath
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {srcpath}\nX = 1\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    ref = subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    # Working tree edit must NOT leak: sync uses the committed blob.
    first = next(iter(sync_vendor.FILE_MAP))
    (src / first).write_text("# dirty\n")

    dest = tmp_path / "out"
    lock = sync_vendor.sync(src, ref, dest)
    assert lock["ref"] == ref
    for entry in lock["files"]:
        p = dest / entry["dest"]
        assert p.read_text().startswith("# skills/")
        assert _sha(p) == entry["sha256"]
    assert (dest / "vendor" / "VENDOR.lock").exists()


def test_both_skills_load_in_one_process():
    ftd = loader.load_ftd_modules()
    dd = loader.load_dd_modules()
    assert hasattr(ftd.rally_tracker, "get_market_state")
    assert hasattr(ftd.post_ftd_monitor, "assess_post_ftd_health")
    assert hasattr(dd.tracker, "detect_distribution_days")
    assert hasattr(dd.models, "DistributionDayRule")
    assert hasattr(dd.risk_classifier, "classify_risk")
    assert hasattr(dd.math_utils, "calc_ema")


LEAK_SCRIPT = """
import json, sys, types
sys.path.insert(0, sys.argv[1])
fake = types.ModuleType("models")  # an unrelated pre-existing module must survive loading
sys.modules["models"] = fake
from engine import loader
ftd = loader.load_ftd_modules()
dd = loader.load_dd_modules()  # the @cache is empty in this fresh process: real loading happens
restored = sys.modules.get("models") is fake
del sys.modules["models"]
bare = ["models", "distribution_day_tracker", "rally_tracker", "post_ftd_monitor",
        "risk_classifier", "math_utils", "history_utils"]
print(json.dumps({
    "restored": restored,
    "leaked": [n for n in bare if n in sys.modules],
    "loaded": hasattr(dd.tracker, "detect_distribution_days")
    and hasattr(ftd.rally_tracker, "get_market_state"),
}))
"""


def test_loading_leaks_no_bare_module_names_fresh_process(tmp_path):
    """Run in a subprocess: in-process the loaders are @cache'd so a check there is vacuous."""
    script = tmp_path / "leak_check.py"
    script.write_text(LEAK_SCRIPT)
    proc = subprocess.run(
        [sys.executable, "-I", str(script), str(ROOT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["loaded"] is True
    assert out["restored"] is True  # pre-existing sys.modules["models"] is put back
    assert out["leaked"] == []


def test_dd_tracker_uses_vendored_models_class():
    dd = loader.load_dd_modules()
    rule = dd.models.DistributionDayRule()
    recs, _ = dd.tracker.detect_distribution_days(
        [
            {"date": "2020-01-03", "close": 99.0, "volume": 200, "high": 100, "low": 98},
            {"date": "2020-01-02", "close": 100.0, "volume": 100, "high": 101, "low": 99},
        ],
        rule,
    )
    assert len(recs) == 1
    assert isinstance(recs[0], dd.models.DDRecord)


def test_risk_classifier_uses_vendored_models_class():
    dd = loader.load_dd_modules()
    th = dd.models.RiskThresholds()
    assert dd.risk_classifier.classify_risk(0, 0, 0, None, th) == "NORMAL"
    assert dd.risk_classifier.classify_risk(0, 0, 6, None, th) == "SEVERE"
    # the classifier's RiskThresholds is the vendored class, not some other `models`
    assert dd.risk_classifier.RiskThresholds is dd.models.RiskThresholds


def test_rally_tracker_instances_are_isolated():
    default = loader.load_ftd_modules()
    deep = loader.load_rally_tracker(min_correction_pct=8.0)
    other = loader.load_rally_tracker(min_correction_pct=5.0)
    assert default.rally_tracker.MIN_CORRECTION_PCT == 3.0
    assert deep.MIN_CORRECTION_PCT == 8.0
    assert other.MIN_CORRECTION_PCT == 5.0
    assert deep is not other
    # functions read the module-global of their own instance
    assert deep.get_market_state.__globals__["MIN_CORRECTION_PCT"] == 8.0
    assert default.rally_tracker.get_market_state.__globals__["MIN_CORRECTION_PCT"] == 3.0
    # Enum classes are distinct objects too (no shared module state)
    assert deep.MarketState is not default.rally_tracker.MarketState


def test_load_rally_tracker_none_override_keeps_default():
    rt = loader.load_rally_tracker()
    assert rt.MIN_CORRECTION_PCT == 3.0


def test_loader_does_not_modify_vendor_files():
    lock = json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())
    loader.load_rally_tracker(min_correction_pct=10.0)
    for entry in lock["files"]:
        assert _sha(ROOT / entry["dest"]) == entry["sha256"]


@pytest.mark.parametrize("bad", [0, -1, "3"])
def test_invalid_override_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        loader.load_rally_tracker(min_correction_pct=bad)
