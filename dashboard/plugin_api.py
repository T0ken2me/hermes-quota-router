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
