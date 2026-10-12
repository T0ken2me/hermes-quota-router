"""Dashboard / desktop backend for the quota router, mounted by Hermes at /api/plugins/quota-router/.

Hermes authenticates every request before it reaches these routes (session token on a local
dashboard, login on a remote one). On top of that, mutations here:
- only accept `Content-Type: application/json` (a cross-site HTML form cannot send it without a
  CORS preflight, which Hermes does not grant) and, when the browser sends an Origin header, it
  must match the Host;
- are rate limited;
- go through the engine's validation and lock, exactly like cron and /quota.
Internal errors are logged, never echoed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _engine import engine  # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter()

_WINDOW, _MAX_WRITES, _RUN_GAP = 60.0, 20, 30.0
_lock = threading.Lock()
_writes: list[float] = []
_last_run = [0.0]

# ── account-ref validation (mirrors accounts.py _SAFE) ──────────────────────
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/\-]{0,127}$")
_PROVIDERS = {"openai-codex", "anthropic", "nous", "openai", "google", "xai", "deepseek", "mistral"}
_SECRET_PREFIXES = ("bearer", "sk-", "eyj", "ghp_", "xai-", "abacusapikey")


def _bad_handle(value: str) -> bool:
    return value.lower().startswith(_SECRET_PREFIXES)


def _same_origin(request: Request) -> None:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(status_code=415, detail="JSON body required")
    origin = request.headers.get("origin")
    if origin and origin != "null":
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
        if urlparse(origin).netloc.lower() != host.lower():
            raise HTTPException(status_code=403, detail="cross-origin request refused")


def _rate_limit() -> None:
    now = time.monotonic()
    with _lock:
        _writes[:] = [t for t in _writes if now - t < _WINDOW]
        if len(_writes) >= _MAX_WRITES:
            raise HTTPException(status_code=429, detail="too many changes, wait a minute")
        _writes.append(now)


def mutation(request: Request) -> None:
    _same_origin(request)
    _rate_limit()


async def _call(fn, *a):
    try:
        return await asyncio.to_thread(fn, *a)
    except ValueError as e:  # validation messages are safe and useful
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("quota-router API call failed")
        raise HTTPException(status_code=500, detail="internal error — see Hermes logs")


@router.get("/overview")
async def overview():
    return await _call(engine().overview)


@router.get("/decisions")
async def decisions(n: int = 60):
    return {"items": await _call(engine().recent_decisions, max(1, min(n, 500)))}


@router.post("/run", dependencies=[Depends(mutation)])
async def run_now():
    now = time.monotonic()
    with _lock:
        if now - _last_run[0] < _RUN_GAP:
            raise HTTPException(status_code=429, detail="a run just happened, wait 30 s")
        _last_run[0] = now
    return {"lines": await _call(engine().run, "dashboard")}


@router.post("/mode", dependencies=[Depends(mutation)])
async def set_mode(body: dict):
    await _call(engine().set_mode, str(body.get("mode", "")), "dashboard")
    return {"ok": True}


@router.post("/thresholds", dependencies=[Depends(mutation)])
async def set_thresholds(body: dict):
    if not isinstance(body, dict) or len(body) > 10:
        raise HTTPException(status_code=400, detail="bad thresholds")
    return {"thresholds": await _call(engine().set_thresholds, body, "dashboard")}


@router.post("/hold", dependencies=[Depends(mutation)])
async def hold(body: dict):
    await _call(engine().hold, str(body.get("scope", ""))[:200], str(body.get("note", ""))[:200], "dashboard")
    return {"ok": True}


@router.post("/release", dependencies=[Depends(mutation)])
async def release(body: dict):
    return {"released": await _call(engine().release, str(body.get("scope", ""))[:200], "dashboard")}


@router.post("/force", dependencies=[Depends(mutation)])
async def force(body: dict):
    try:
        rung = int(body.get("rung"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rung must be an integer")
    c = await _call(engine().force, str(body.get("scope", ""))[:200], rung, str(body.get("note", ""))[:200], "dashboard")
    return {"change": c}


# ---------------------------------------------------------------------------
# Spend panel (read-only, GET only, JSON)
# ---------------------------------------------------------------------------

_SPEND_RANGES = {"today", "7d", "30d"}


def _spend_range(range_: str) -> str:
    if range_ not in _SPEND_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid range: {range_!r} — must be one of: today, 7d, 30d",
        )
    return range_


@router.get("/spend/summary")
async def spend_summary(range: str = "7d"):
    from engine.spend import compute_summary
    return await _call(compute_summary, _spend_range(range))


@router.get("/spend/timeseries")
async def spend_timeseries(range: str = "7d"):
    from engine.spend import compute_timeseries
    return await _call(compute_timeseries, _spend_range(range))


@router.get("/spend/models")
async def spend_models(range: str = "7d"):
    from engine.spend import compute_models
    return await _call(compute_models, _spend_range(range))


@router.get("/spend/profiles")
async def spend_profiles(range: str = "7d"):
    from engine.spend import compute_profiles
    return await _call(compute_profiles, _spend_range(range))


@router.get("/spend/sessions")
async def spend_sessions(range: str = "7d", limit: int = 20):
    from engine.spend import compute_top_sessions
    limit = max(1, min(int(limit), 50))
    return await _call(compute_top_sessions, _spend_range(range), limit)


# ---------------------------------------------------------------------------
# Account manager endpoints
# ---------------------------------------------------------------------------

def _validate_account_body(body: dict) -> dict:
    """Parse and validate an add-account request body. Returns a clean dict (no credential_ref)."""
    label = str(body.get("label", "")).strip()
    if not label or len(label) > 120:
        raise HTTPException(status_code=400, detail="label required and ≤ 120 chars")
    provider = str(body.get("provider", ""))
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unsupported provider: {provider!r}")
    kind = str(body.get("kind", ""))
    if kind not in ("subscription", "credits"):
        raise HTTPException(status_code=400, detail="kind must be 'subscription' or 'credits'")
    for field in ("account_ref", "pool_ref", "credential_ref"):
        val = str(body.get(field, ""))
        if not _SAFE.fullmatch(val):
            raise HTTPException(status_code=400, detail=f"invalid {field}")
        if _bad_handle(val):
            raise HTTPException(status_code=400, detail=f"{field} looks like a raw secret; use an opaque handle")
    try:
        priority = int(body.get("priority", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="priority must be an integer")
    if not 0 <= priority <= 99:
        raise HTTPException(status_code=400, detail="priority must be 0–99")
    notes = str(body.get("notes", ""))[:500]
    enabled = bool(body.get("enabled", True))
    if body.get("purchased_credits_enabled") or body.get("auto_recharge"):
        raise HTTPException(status_code=400, detail="purchased credits and auto-recharge are disabled")
    return dict(
        account_ref=str(body["account_ref"]),
        pool_ref=str(body["pool_ref"]),
        credential_ref=str(body["credential_ref"]),  # runtime-only; account_store strips it before disk
        provider=provider,
        kind=kind,
        label=label,
        priority=priority,
        enabled=enabled,
        notes=notes,
        status="unvalidated",
        last_validated_at=None,
        plan_label=None,
    )


def _engine_account_stubs(eng):
    """Attach account-manager stubs to the engine object if the live engine lacks them.
    The quota_router.py engine is not aware of accounts yet — stubs keep the API live
    until the native integration is wired in."""
    from engine.account_store import store as _store

    def _store_path():
        ws = getattr(eng, "_ws", None)
        if ws:
            return Path(ws) / "account_store.json"
        return Path.home() / ".hermes" / "workspace" / "quota-router" / "account_store.json"

    if not hasattr(eng, "account_store_path"):
        eng.account_store_path = _store_path
    if not hasattr(eng, "_acct_store"):
        eng._acct_store = _store(eng.account_store_path())

    def _list_accounts():
        return eng._acct_store.list_accounts()

    def _add_account(record):
        # Strip credential_ref before persisting — it is runtime-only
        disk_record = {k: v for k, v in record.items() if k != "credential_ref"}
        eng._acct_store.add_account(disk_record)
        return record

    def _account_usage(ref):
        return {"windows": [], "credits": None, "fetched_at": None, "freshness": "unknown"}

    def _switching_history(range_, account_ref, profile):
        return []

    def _validate_account(ref):
        return {"status": "unsupported", "reason": "native adapter pending", "plan_label": None}

    def _set_account_enabled(ref, enabled):
        return eng._acct_store.update(ref, enabled=enabled)

    def _set_account_priority(ref, priority):
        return eng._acct_store.update(ref, priority=priority)

    def _is_never_touch(ref):
        return getattr(eng, "never_touch", lambda r: False)(ref) if callable(getattr(eng, "never_touch", None)) else False

    def _dryrun_priorities(profile, task_class):
        return {"would_select": None, "reason": "no eligible account", "eligible": [], "ineligible": []}

    for name, fn in [
        ("list_accounts", _list_accounts),
        ("add_account", _add_account),
        ("account_usage", _account_usage),
        ("switching_history", _switching_history),
        ("validate_account", _validate_account),
        ("set_account_enabled", _set_account_enabled),
        ("set_account_priority", _set_account_priority),
        ("is_never_touch", _is_never_touch),
        ("dryrun_priorities", _dryrun_priorities),
    ]:
        if not hasattr(eng, name):
            setattr(eng, name, fn)
    return eng


def _acct_engine():
    return _engine_account_stubs(engine())


@router.get("/accounts")
async def list_accounts():
    eng = _acct_engine()
    records = await _call(eng.list_accounts)
    # credential_ref is runtime-only; strip it from every record before responding
    safe = [{k: v for k, v in r.items() if k != "credential_ref"} for r in records]
    return {"accounts": safe}


@router.get("/accounts/{account_ref}/usage")
async def account_usage(account_ref: str):
    if not _SAFE.fullmatch(account_ref):
        raise HTTPException(status_code=400, detail="invalid account_ref")
    eng = _acct_engine()
    return await _call(eng.account_usage, account_ref)


@router.get("/history")
async def switching_history(range: str = "7d", account_ref: str | None = None, profile: str | None = None):
    if range not in ("24h", "7d", "30d"):
        raise HTTPException(status_code=400, detail="range must be 24h, 7d or 30d")
    eng = _acct_engine()
    events = await _call(eng.switching_history, range, account_ref, profile)
    return {"events": events}


@router.post("/accounts", dependencies=[Depends(mutation)])
async def add_account(body: dict):
    record = _validate_account_body(body)
    eng = _acct_engine()
    # Check for duplicate before delegating to engine
    existing = await _call(eng.list_accounts)
    if any(a.get("account_ref") == record["account_ref"] for a in existing):
        raise HTTPException(status_code=400, detail="account already exists")
    result = await _call(eng.add_account, record)
    return {"account_ref": result["account_ref"], "ok": True}


@router.post("/accounts/{account_ref}/validate", dependencies=[Depends(mutation)])
async def validate_account(account_ref: str):
    if not _SAFE.fullmatch(account_ref):
        raise HTTPException(status_code=400, detail="invalid account_ref")
    eng = _acct_engine()
    return await _call(eng.validate_account, account_ref)


@router.post("/accounts/{account_ref}/enable", dependencies=[Depends(mutation)])
async def enable_account(account_ref: str, body: dict):
    if not _SAFE.fullmatch(account_ref):
        raise HTTPException(status_code=400, detail="invalid account_ref")
    if not isinstance(body.get("enabled"), bool):
        raise HTTPException(status_code=400, detail="enabled must be a boolean")
    eng = _acct_engine()
    result = await _call(eng.set_account_enabled, account_ref, body["enabled"])
    return {"account_ref": account_ref, "enabled": result.get("enabled")}


@router.post("/accounts/{account_ref}/priority", dependencies=[Depends(mutation)])
async def set_priority(account_ref: str, body: dict):
    if not _SAFE.fullmatch(account_ref):
        raise HTTPException(status_code=400, detail="invalid account_ref")
    try:
        priority = int(body.get("priority") or 0)
        if body.get("priority") is None:
            raise HTTPException(status_code=400, detail="priority required")
        # Reject floats: int(1.5)==1 would silently truncate
        if isinstance(body.get("priority"), float):
            raise HTTPException(status_code=400, detail="priority must be an integer")
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="priority must be an integer")
    if not 0 <= priority <= 99:
        raise HTTPException(status_code=400, detail="priority must be 0–99")
    eng = _acct_engine()
    result = await _call(eng.set_account_priority, account_ref, priority)
    return {"account_ref": account_ref, "priority": result.get("priority")}


@router.post("/priorities/dryrun", dependencies=[Depends(mutation)])
async def priorities_dryrun(body: dict):
    profile = str(body.get("profile", "")).strip()
    task_class = str(body.get("task_class", "")).strip()
    if not profile or not task_class:
        raise HTTPException(status_code=400, detail="profile and task_class required")
    eng = _acct_engine()
    return await _call(eng.dryrun_priorities, profile, task_class)
