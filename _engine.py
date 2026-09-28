"""Loads the router engine from its canonical script so the cron job, the dashboard tab and /quota
share one implementation (engine/quota_router.py) (no copy that could disagree with what the scheduler does)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent / "engine" / "quota_router.py"
_mod = None
_mtime = None


def engine():
    """Return the engine module, reloading it when the script changes on disk."""
    global _mod, _mtime
    mt = ENGINE.stat().st_mtime
    if _mod is None or mt != _mtime:
        spec = importlib.util.spec_from_file_location("quota_router_engine", ENGINE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["quota_router_engine"] = mod
        spec.loader.exec_module(mod)
        _mod, _mtime = mod, mt
    return _mod
