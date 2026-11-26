"""Load vendored decision modules without touching sys.path or leaking module names.

- FTD modules (rally_tracker, post_ftd_monitor) have no intra-package imports and are
  loaded straight from their files.
- The DD tracker does `from models import ...`, so the vendored models module is
  registered as sys.modules["models"] only while the tracker executes, then the
  previous entry (if any) is restored.
- `load_rally_tracker(min_correction_pct=...)` returns an independent module instance
  whose MIN_CORRECTION_PCT global is overridden (vendored file stays byte-identical).
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
from functools import cache
from pathlib import Path
from types import ModuleType, SimpleNamespace

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
FTD_DIR = VENDOR / "ftd_detector"
DD_DIR = VENDOR / "ibd_dd"

_counter = itertools.count()


def _exec_module(path: Path, register_as: str) -> ModuleType:
    """Execute `path` as a fresh module; sys.modules[register_as] is set only during exec."""
    spec = importlib.util.spec_from_file_location(register_as, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous = sys.modules.get(register_as, sentinel)
    sys.modules[register_as] = module  # dataclasses / enum need this during exec
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is sentinel:
            sys.modules.pop(register_as, None)
        else:
            sys.modules[register_as] = previous
    return module


def _unique(name: str) -> str:
    return f"_ftd_dd_vendor_{name}_{next(_counter)}"


def _validate_pct(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"min_correction_pct must be a number, got {type(value).__name__}")
    if not value > 0:
        raise ValueError(f"min_correction_pct must be > 0, got {value}")
    return float(value)


def _validate_gain(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"ftd_gain_minimum must be a number, got {type(value).__name__}")
    if not value > 0:
        raise ValueError(f"ftd_gain_minimum must be > 0, got {value}")
    return float(value)


def load_rally_tracker(
    min_correction_pct: float | None = None, ftd_gain_minimum: float | None = None
) -> ModuleType:
    """Return a rally_tracker module instance.

    With no override the shared default instance is returned (same one used by
    `load_ftd_modules`). With an override, a brand-new instance is created every call and
    only the overridden module globals (MIN_CORRECTION_PCT, FTD_GAIN_MINIMUM) differ.
    """
    if min_correction_pct is None and ftd_gain_minimum is None:
        return load_ftd_modules().rally_tracker
    pct = _validate_pct(min_correction_pct) if min_correction_pct is not None else None
    gain = _validate_gain(ftd_gain_minimum) if ftd_gain_minimum is not None else None
    module = _exec_module(FTD_DIR / "rally_tracker.py", _unique("rally_tracker"))
    if pct is not None:
        module.MIN_CORRECTION_PCT = pct
    if gain is not None:
        module.FTD_GAIN_MINIMUM = gain
    return module


@cache
def load_ftd_modules() -> SimpleNamespace:
    rally = _exec_module(FTD_DIR / "rally_tracker.py", _unique("rally_tracker"))
    post = _exec_module(FTD_DIR / "post_ftd_monitor.py", _unique("post_ftd_monitor"))
    return SimpleNamespace(rally_tracker=rally, post_ftd_monitor=post)


@cache
def load_dd_modules() -> SimpleNamespace:
    models = _exec_module(DD_DIR / "models.py", _unique("models"))
    sentinel = object()
    previous = sys.modules.get("models", sentinel)
    sys.modules["models"] = models  # satisfy `from models import ...` in tracker / classifier
    try:
        tracker = _exec_module(DD_DIR / "distribution_day_tracker.py", _unique("dd_tracker"))
        risk_classifier = _exec_module(DD_DIR / "risk_classifier.py", _unique("risk_classifier"))
    finally:
        if previous is sentinel:
            sys.modules.pop("models", None)
        else:
            sys.modules["models"] = previous
    history_utils = _exec_module(DD_DIR / "history_utils.py", _unique("history_utils"))
    math_utils = _exec_module(DD_DIR / "math_utils.py", _unique("math_utils"))  # no imports
    return SimpleNamespace(
        models=models,
        tracker=tracker,
        history_utils=history_utils,
        risk_classifier=risk_classifier,
        math_utils=math_utils,
    )
