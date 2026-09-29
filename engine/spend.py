# Adapted from Diabloluo/hermes-hud dashboard/hud/cost.py @004c18a7, MIT
# Copyright (c) 2026 Hermes HUD contributors
"""Read-only spend aggregator for the quota-router dashboard.

Reads session_model_usage from every Hermes profile state.db (default home +
every profiles/<name>/state.db) and returns token/cost aggregations.

Rules:
- Open every DB read-only: sqlite3.connect("file:<path>?mode=ro", uri=True, timeout=2)
- Never write, never re-price history.
- Skip every profile listed in the policy's never_touch list — do not even
  attempt to open its state.db.
- Local-time day boundaries from the machine timezone (datetime.astimezone()).
- Provenance on every response: cost_semantics ("estimated" | "recorded" |
  "mixed"), window_exact (True only for lifetime/all totals), pricing_unknown
  count.
- Top sessions: NO titles, NO message content. Profile, model, date, tokens,
  cost, shortened session_id only.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("quota_router.spend")

VALID_RANGES = {"today", "7d", "30d"}

_USAGE_COLS = (
    "session_id", "model", "billing_provider",
    "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
    "estimated_cost_usd", "actual_cost_usd",
    "cost_status", "cost_source",
    "first_seen", "last_seen",
)
# Column index constants for clarity
_C = {c: i for i, c in enumerate(_USAGE_COLS)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pricing_known(cost_status: Any, cost_source: Any) -> bool:
    """Provenance contract (from hermes-hud audit):
    known: cost_status='estimated' AND cost_source non-empty and != 'none'
    unknown: cost_status='unknown' AND cost_source='none'
    NULL legacy: treat as unknown (not verifiable)
    """
    if cost_status == "estimated" and cost_source and str(cost_source) != "none":
        return True
    return False


def _local_day_start(ts: float) -> str:
    """Return 'YYYY-MM-DD' in the machine's local timezone for a given epoch."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _cutoff(time_range: str, now: Optional[float] = None) -> Optional[float]:
    """Return epoch cutoff (last_seen >= cutoff).  None means no lower bound."""
    now_ = now if now is not None else _time.time()
    if time_range == "today":
        local_now = datetime.fromtimestamp(now_).astimezone()
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()
    if time_range == "7d":
        return now_ - 7 * 86400
    if time_range == "30d":
        return now_ - 30 * 86400
    return None  # "all" — not a public endpoint range but used internally


def _window_attrs(time_range: str) -> dict:
    """Attribution truth (matches hermes-hud semantics)."""
    if time_range == "all":
        return {
            "window_exact": True,
            "window_attribution": "lifetime_cumulative",
            "window_semantics": "lifetime_cumulative",
        }
    return {
        "window_exact": False,
        "window_attribution": "last_seen",
        "window_semantics": (
            "cumulative usage rows attributed by last activity; "
            "range boundaries are approximate"
        ),
    }


# ---------------------------------------------------------------------------
# Profile discovery
# ---------------------------------------------------------------------------

def _hermes_home() -> Path:
    return Path(os.environ.get("QR_HERMES_ROOT") or Path.home() / ".hermes")


def _profile_dbs(never_touch: set[str]) -> list[tuple[str, Path]]:
    """Return [(profile_label, state.db path)] for every profile NOT in never_touch.

    The default home is labelled "default"; named profiles use their folder name.
    never_touch skips are unconditional — the file is never opened.
    """
    home = _hermes_home()
    results: list[tuple[str, Path]] = []
    # Default home
    if "default" not in never_touch:
        db = home / "state.db"
        if db.exists():
            results.append(("default", db))
    # Named profiles
    profiles_dir = home / "profiles"
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if name in never_touch:
                continue
            db = entry / "state.db"
            if db.exists():
                results.append((name, db))
    return results


def _load_never_touch() -> set[str]:
    """Load the router's never_touch list from policy.yaml.  Returns empty set on failure."""
    try:
        import yaml
        home = _hermes_home()
        policy_path = home / "workspace" / "quota-router" / "policy.yaml"
        p = yaml.safe_load(policy_path.read_text()) or {}
        return set(p.get("never_touch") or [])
    except Exception:
        return set()


def _load_daily_budget() -> Optional[float]:
    """Return spend.daily_budget_usd from policy.yaml, or None."""
    try:
        import yaml
        home = _hermes_home()
        policy_path = home / "workspace" / "quota-router" / "policy.yaml"
        p = yaml.safe_load(policy_path.read_text()) or {}
        v = (p.get("spend") or {}).get("daily_budget_usd")
        if v is not None:
            return float(v)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Low-level DB read
# ---------------------------------------------------------------------------

def _read_rows(db_path: Path, cutoff: Optional[float]) -> tuple[list[tuple], bool]:
    """Read session_model_usage rows (read-only, cutoff pushed down to SQL).
    Returns (rows, source_ok).  Never writes, never re-prices.
    """
    try:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2)
        where, params = [], []
        if cutoff is not None:
            where.append("last_seen >= ?")
            params.append(cutoff)
        sql = (
            "SELECT " + ", ".join(_USAGE_COLS) +
            " FROM session_model_usage"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen DESC"
        rows = con.execute(sql, params).fetchall()
        con.close()
        return rows, True
    except Exception as exc:
        log.debug("spend: cannot read %s: %s", db_path, exc)
        return [], False


def _shorten_session_id(sid: str) -> str:
    """Return first 8 chars + … — enough to identify, not enough to correlate."""
    if not sid:
        return "?"
    s = str(sid)
    return s[:8] + "…" if len(s) > 8 else s


# ---------------------------------------------------------------------------
# Public compute functions
# ---------------------------------------------------------------------------

def compute_summary(time_range: str, never_touch: Optional[set] = None,
                    now: Optional[float] = None) -> dict:
    """Aggregate totals across all accessible profiles for the given range."""
    if never_touch is None:
        never_touch = _load_never_touch()
    dbs = _profile_dbs(never_touch)
    cutoff = _cutoff(time_range, now)

    total_cost = 0.0
    total_input = total_output = total_cache = 0
    pricing_known_rows = pricing_unknown_rows = usage_rows = 0
    profiles_ok = profiles_partial = 0
    any_source_unavailable = False

    for _label, db_path in dbs:
        rows, ok = _read_rows(db_path, cutoff)
        if not ok:
            any_source_unavailable = True
            profiles_partial += 1
            continue
        profiles_ok += 1
        for r in rows:
            usage_rows += 1
            total_input += (r[_C["input_tokens"]] or 0)
            total_output += (r[_C["output_tokens"]] or 0)
            total_cache += (r[_C["cache_read_tokens"]] or 0)
            if _pricing_known(r[_C["cost_status"]], r[_C["cost_source"]]):
                pricing_known_rows += 1
                total_cost += float(r[_C["estimated_cost_usd"]] or 0)
            else:
                pricing_unknown_rows += 1

    source_ok = not any_source_unavailable
    out = {
        "schema_version": 1,
        "range": time_range,
        "estimated_cost_usd": total_cost,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache,
        "total_tokens": total_input + total_output,
        "cost_semantics": "estimated",
        "coverage": {
            "usage_rows": usage_rows,
            "pricing_known_rows": pricing_known_rows,
            "pricing_unknown_rows": pricing_unknown_rows,
            "pricing_coverage_ratio": (
                (pricing_known_rows / usage_rows) if usage_rows else 1.0
            ),
            "profiles_ok": profiles_ok,
            "profiles_partial": profiles_partial,
        },
        "source_status": "healthy" if source_ok else "partial",
        "partial": any_source_unavailable or (pricing_unknown_rows > 0),
    }
    out.update(_window_attrs(time_range))
    return out


def compute_timeseries(time_range: str, never_touch: Optional[set] = None,
                       now: Optional[float] = None) -> dict:
    """Daily cost/token timeseries across all accessible profiles."""
    if never_touch is None:
        never_touch = _load_never_touch()
    dbs = _profile_dbs(never_touch)
    cutoff = _cutoff(time_range, now)

    days: dict[str, dict] = {}
    pricing_known_total = usage_rows = 0
    any_unavail = False

    for _label, db_path in dbs:
        rows, ok = _read_rows(db_path, cutoff)
        if not ok:
            any_unavail = True
            continue
        for r in rows:
            ts = r[_C["last_seen"]] or 0
            day = _local_day_start(ts)
            d = days.setdefault(day, {
                "date": day,
                "estimated_cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
            })
            if _pricing_known(r[_C["cost_status"]], r[_C["cost_source"]]):
                pricing_known_total += 1
                d["estimated_cost_usd"] += float(r[_C["estimated_cost_usd"]] or 0)
            d["input_tokens"] += (r[_C["input_tokens"]] or 0)
            d["output_tokens"] += (r[_C["output_tokens"]] or 0)
            usage_rows += 1

    series = [v for _, v in sorted(days.items())]
    out = {
        "schema_version": 1,
        "range": time_range,
        "points": series,
        "source_status": "partial" if any_unavail else "healthy",
        "pricing_coverage": {
            "usage_rows": usage_rows,
            "pricing_known_rows": pricing_known_total,
            "pricing_coverage_ratio": (
                (pricing_known_total / usage_rows) if usage_rows else 1.0
            ),
        },
    }
    out.update(_window_attrs(time_range))
    return out


def compute_models(time_range: str, never_touch: Optional[set] = None,
                   now: Optional[float] = None) -> dict:
    """Per-model aggregation across all accessible profiles."""
    if never_touch is None:
        never_touch = _load_never_touch()
    dbs = _profile_dbs(never_touch)
    cutoff = _cutoff(time_range, now)

    models: dict[str, dict] = {}
    any_unavail = False

    for _label, db_path in dbs:
        rows, ok = _read_rows(db_path, cutoff)
        if not ok:
            any_unavail = True
            continue
        for r in rows:
            model = r[_C["model"]] or "unknown"
            m = models.setdefault(model, {
                "model": model,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "sessions": set(),
                "pricing_known_rows": 0,
                "pricing_unknown_rows": 0,
                "rows_total": 0,
            })
            m["input_tokens"] += (r[_C["input_tokens"]] or 0)
            m["output_tokens"] += (r[_C["output_tokens"]] or 0)
            m["total_tokens"] += (r[_C["input_tokens"]] or 0) + (r[_C["output_tokens"]] or 0)
            m["rows_total"] += 1
            if _pricing_known(r[_C["cost_status"]], r[_C["cost_source"]]):
                m["estimated_cost_usd"] += float(r[_C["estimated_cost_usd"]] or 0)
                m["pricing_known_rows"] += 1
            else:
                m["pricing_unknown_rows"] += 1
            m["sessions"].add(r[_C["session_id"]])

    out = []
    for m in models.values():
        m["sessions"] = len(m["sessions"])
        m["pricing_coverage_ratio"] = (
            (m["pricing_known_rows"] / m["rows_total"]) if m["rows_total"] else 1.0
        )
        m["cost_complete"] = (
            m["rows_total"] == 0 or m["pricing_known_rows"] == m["rows_total"]
        )
        out.append(m)
    out.sort(key=lambda x: x["estimated_cost_usd"], reverse=True)

    resp = {
        "schema_version": 1,
        "range": time_range,
        "models": out,
        "source_status": "partial" if any_unavail else "healthy",
    }
    resp.update(_window_attrs(time_range))
    return resp


def compute_profiles(time_range: str, never_touch: Optional[set] = None,
                     now: Optional[float] = None) -> dict:
    """Per-profile aggregation — hermes-hud is single-home; quota-router is multi-profile."""
    if never_touch is None:
        never_touch = _load_never_touch()
    dbs = _profile_dbs(never_touch)
    cutoff = _cutoff(time_range, now)

    results = []
    for label, db_path in dbs:
        rows, ok = _read_rows(db_path, cutoff)
        total_cost = 0.0
        total_input = total_output = 0
        pricing_known = pricing_unknown = usage_rows = 0
        if ok:
            for r in rows:
                usage_rows += 1
                total_input += (r[_C["input_tokens"]] or 0)
                total_output += (r[_C["output_tokens"]] or 0)
                if _pricing_known(r[_C["cost_status"]], r[_C["cost_source"]]):
                    pricing_known += 1
                    total_cost += float(r[_C["estimated_cost_usd"]] or 0)
                else:
                    pricing_unknown += 1
        results.append({
            "profile": label,
            "estimated_cost_usd": total_cost if ok else None,
            "input_tokens": total_input if ok else None,
            "output_tokens": total_output if ok else None,
            "total_tokens": (total_input + total_output) if ok else None,
            "usage_rows": usage_rows if ok else None,
            "pricing_unknown_rows": pricing_unknown if ok else None,
            "source_status": "healthy" if ok else "unavailable",
        })
    results.sort(key=lambda x: (x["estimated_cost_usd"] or 0), reverse=True)

    resp = {
        "schema_version": 1,
        "range": time_range,
        "profiles": results,
        "cost_semantics": "estimated",
    }
    resp.update(_window_attrs(time_range))
    return resp


def compute_top_sessions(time_range: str, limit: int = 20,
                         never_touch: Optional[set] = None,
                         now: Optional[float] = None) -> dict:
    """Top sessions by estimated cost.

    PRIVACY: No titles, no message content, no session-preview data.
    Shows profile, model(s), date, tokens, cost, and a shortened session id only.
    Session titles can contain client/matter names and must never appear here.
    """
    if never_touch is None:
        never_touch = _load_never_touch()
    limit = max(1, min(int(limit), 50))
    dbs = _profile_dbs(never_touch)
    cutoff = _cutoff(time_range, now)

    # Key: (profile_label, session_id) to avoid cross-profile id collisions
    sessions: dict[tuple, dict] = {}
    any_unavail = False

    for label, db_path in dbs:
        rows, ok = _read_rows(db_path, cutoff)
        if not ok:
            any_unavail = True
            continue
        for r in rows:
            sid = r[_C["session_id"]]
            key = (label, sid)
            s = sessions.setdefault(key, {
                "profile": label,
                "session_id_short": _shorten_session_id(sid),
                "models": set(),
                "estimated_cost_usd": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "first_seen": None,
                "last_seen_epoch": None,
                "date": None,
                "pricing_known_rows": 0,
                "pricing_unknown_rows": 0,
                "rows_total": 0,
            })
            s["models"].add(r[_C["model"]] or "unknown")
            s["rows_total"] += 1
            if _pricing_known(r[_C["cost_status"]], r[_C["cost_source"]]):
                s["estimated_cost_usd"] += float(r[_C["estimated_cost_usd"]] or 0)
                s["pricing_known_rows"] += 1
            else:
                s["pricing_unknown_rows"] += 1
            s["input_tokens"] += (r[_C["input_tokens"]] or 0)
            s["output_tokens"] += (r[_C["output_tokens"]] or 0)
            last = r[_C["last_seen"]]
            if last and (s["last_seen_epoch"] is None or last > s["last_seen_epoch"]):
                s["last_seen_epoch"] = last
                s["date"] = _local_day_start(last)
            first = r[_C["first_seen"]]
            if first and (s["first_seen"] is None or first < s["first_seen"]):
                s["first_seen"] = first

    out = []
    for s in sessions.values():
        s["models"] = sorted(s["models"])
        del s["last_seen_epoch"]  # not needed in output
        out.append(s)
    out.sort(key=lambda x: x["estimated_cost_usd"], reverse=True)

    resp = {
        "schema_version": 1,
        "range": time_range,
        "sessions": out[:limit],
        "source_status": "partial" if any_unavail else "healthy",
        "cost_semantics": "estimated",
    }
    # No 'title' key anywhere in the response
    assert all("title" not in s for s in resp["sessions"]), "title must never appear in sessions"
    resp.update(_window_attrs(time_range))
    return resp
