"""quota-router plugin: `/quota` slash command (CLI, TUI, desktop, messaging) over the router engine.

/quota                      status: mode, quotas, routes off their first choice, holds, drift
/quota routes               every route with its current rung
/quota ladder <route>       the rungs for one route
/quota run                  run one router pass now
/quota mode dry_run|enforce
/quota hold <route> [note]  the router leaves the route alone
/quota release <route>      give it back to automatic routing
/quota force <route> <n>    move a route to rung n now and hold it
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _access import check_access  # noqa: E402
from _engine import engine  # noqa: E402

logger = logging.getLogger(__name__)

READ_ONLY = {"", "routes", "ladder", "help"}


def _quota_line(q):
    b = []
    for p, label in (("anthropic", "Anthropic"), ("openai-codex", "Codex")):
        r = q.get(p) or {}
        b.append(f"{label} {r.get('weekly')}% wk" if r.get("ok") else f"{label} ?")
    r = q.get("nous") or {}
    b.append(f"Nous ${r.get('credits')}" if r.get("ok") else "Nous ?")
    b.append("Copilot " + ("ok" if (q.get("copilot") or {}).get("ok") else "?"))
    for p, label in (("deepseek", "DeepSeek"), ("openrouter", "OpenRouter")):
        r = q.get(p) or {}
        if p in q:
            b.append(f"{label} ${r.get('balance_usd')}" if r.get("ok") else f"{label} ?")
    return " · ".join(b)


def _route_line(r):
    tgt = r["ladder"][r["rung"]]
    mark = "🔒 " if r["hold"] else ("⚠️ " if r["drift"] else "")
    return f"{mark}{r['scope']} → {tgt[0]}/{tgt[1]} (rung {r['rung']})"


def _status(all_routes=False):
    o = engine().overview()
    q = o["quota"]
    out = [f"Quota router — mode: {o['mode']}", f"as of {q.get('ts', '?')}", _quota_line(q), ""]
    rs = o["routes"] if all_routes else [r for r in o["routes"] if r["rung"] or r["hold"] or r["drift"]]
    out.append("All routes:" if all_routes else f"Off-default / held / drift ({len(rs)} of {len(o['routes'])}):")
    out += [_route_line(r) for r in rs] or ["(none — every route on its preferred model)"]
    return "\n".join(out)


def _ladder(scope):
    o = engine().overview()
    r = next((r for r in o["routes"] if r["scope"] == scope), None)
    if not r:
        return "Unknown route. Try /quota routes."
    lines = [f"{scope} ({r['class']}):"]
    for i, (p, m) in enumerate(r["ladder"]):
        lines.append(f"{'▶' if i == r['rung'] else ' '} {i}. {p}/{m}  [{o['kinds'].get(p)}]")
    return "\n".join(lines)


def quota_command(raw_args: str = "") -> str:
    args = (raw_args or "").split()
    cmd, rest = (args[0].lower(), args[1:]) if args else ("", [])
    try:
        e = engine()
        denied, who = check_access(e.load_policy(), mutate=cmd not in READ_ONLY)
        if denied:
            return denied
        if not cmd:
            return _status()
        if cmd == "routes":
            return _status(all_routes=True)
        if cmd == "ladder" and rest:
            return _ladder(rest[0])
        if cmd == "run":
            lines = e.run(who)
            return "\n".join(lines) if lines else "Router pass done — no changes."
        if cmd == "mode" and rest and rest[0] in ("dry_run", "enforce"):
            e.set_mode(rest[0], who=who)
            return f"Mode set to {rest[0]}."
        if cmd == "hold" and rest:
            e.hold(rest[0], " ".join(rest[1:]), who=who)
            return f"🔒 {rest[0]} held — the router leaves it alone until /quota release {rest[0]}."
        if cmd == "release" and rest:
            ok = e.release(rest[0], who=who)
            return f"Released {rest[0]}." if ok else f"{rest[0]} was not held."
        if cmd == "force" and len(rest) >= 2 and rest[1].isdigit():
            c = e.force(rest[0], int(rest[1]), " ".join(rest[2:]), who=who)
            warn = " 💳 METERED" if c["metered"] else ""
            return f"🔒 {c['scope']}: {c['from'][1]} → {c['to'][0]}/{c['to'][1]}{warn} (held)."
    except ValueError as ex:  # validation errors are safe to show
        return f"/quota: {ex}"
    except Exception as ex:  # anything else: log details, show nothing internal
        logger.warning("quota-router: /quota %s failed: %s", cmd, ex)
        return "/quota failed — see the Hermes logs."
    return __doc__.split("\n\n", 1)[1]


def register(ctx) -> None:
    ctx.register_command("quota", quota_command, description="Quota router: status, holds, mode, force",
                         args_hint="[routes|ladder|run|mode|hold|release|force]")
