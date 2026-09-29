"""Tests for the format_alert() pure formatter and _format_budget_line()."""
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))  # noqa: E702
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

from _harness import load_engine, cleanup, Results

R = Results()

# ---------------------------------------------------------------------------
# Load the engine module (harness wires temp workspace; we never call run())
# ---------------------------------------------------------------------------
m, tmp = load_engine()

# Helpers
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 29, 13, 2, 11, tzinfo=UTC).astimezone()  # aware local

def q_snap(**overrides):
    base = {
        "anthropic":    {"ok": True, "weekly": 36.0, "session": 100.0,
                         "session_reset": "2026-09-29T11:30:00+00:00",
                         "weekly_reset": None},
        "openai-codex": {"ok": True, "weekly": 78.0, "session": 10.0,
                         "weekly_reset": None, "session_reset": None},
        "nous":         {"ok": True, "credits": 110.02},
        "copilot":      {"ok": True, "plan": "individual"},
        "openrouter":   {"ok": True, "balance_usd": 0.19},
        "deepseek":     {"ok": False, "error": "no API key"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. No changes, no drift -> empty string
# ---------------------------------------------------------------------------
msg = m.format_alert([], {}, q_snap(), dry=False, now=NOW)
R.check(msg == "", "empty changes + empty drift -> empty string")

# ---------------------------------------------------------------------------
# 2. Icon precedence: 🛑 when apply_error present, even alongside returned
# ---------------------------------------------------------------------------
changes_fail = [
    {"scope": "coder.main", "bot": "coder", "class": "coding",
     "from": ["anthropic", "claude-sonnet-4-5"], "to": ["nous", "openai/gpt-5"],
     "rung": 2, "metered": False, "reason": "anthropic down",
     "apply_error": "hermes: connection refused"}
]
msg = m.format_alert(changes_fail, {}, q_snap(), dry=False, now=NOW)
R.check(msg.startswith("🛑"), "apply_error -> 🛑 icon")
R.check("check logs & restore manually" in msg, "apply_error includes action hint")
R.check("coder.main: apply FAILED" in msg, "failed scope listed individually")

# ---------------------------------------------------------------------------
# 3. 🛑 wins even when drift-only
# ---------------------------------------------------------------------------
msg_drift = m.format_alert([], {"writer.main": "live gpt-5/x != policy anthropic/y"}, q_snap(), dry=False, now=NOW)
R.check(msg_drift.startswith("🛑"), "drift-only -> 🛑 icon")
R.check("update policy.yaml or restore the setting" in msg_drift, "drift includes action hint")

# ---------------------------------------------------------------------------
# 4. ⚠️ when routes move away from rung 0
# ---------------------------------------------------------------------------
changes_away = [
    {"scope": f"coder.main", "bot": "coder", "class": "coding",
     "from": ["anthropic", "claude-sonnet-4-5"], "to": ["nous", "openai/gpt-5"],
     "rung": 2, "metered": False, "reason": "anthropic down"},
]
msg = m.format_alert(changes_away, {}, q_snap(), dry=False, now=NOW)
R.check(msg.startswith("⚠️"), "route away from rung 0 -> ⚠️")

# ---------------------------------------------------------------------------
# 5. ✅ when all routes return to rung 0
# ---------------------------------------------------------------------------
changes_return = [
    {"scope": "coder.main", "bot": "coder", "class": "coding",
     "from": ["nous", "openai/gpt-5"], "to": ["anthropic", "claude-sonnet-4-5"],
     "rung": 0, "metered": False, "reason": "anthropic recovered"},
]
msg = m.format_alert(changes_return, {}, q_snap(), dry=False, now=NOW)
R.check(msg.startswith("✅"), "all routes return to rung 0 -> ✅")

# ---------------------------------------------------------------------------
# 6. 🧪 prefix + "(dry-run)" in headline for dry runs
# ---------------------------------------------------------------------------
msg = m.format_alert(changes_away, {}, q_snap(), dry=True, now=NOW)
R.check(msg.startswith("🧪"), "dry-run -> 🧪 icon")
R.check("(dry-run)" in msg.splitlines()[0], "dry-run wording in headline")

# ---------------------------------------------------------------------------
# 7. Grouping: a large fleet event (>3 per group) should produce grouped
#    summary lines, not individual scope bullets
# ---------------------------------------------------------------------------
# Build 31 synthetic changes (all from anthropic -> nous, reason "anthropic down")
# using generic example profile names only: default, coder, writer, designer, private
def _synthetic_fleet():
    """Generic 31-route fleet: 7 bots x (main + subagent), 6 background tasks, 11 scheduled jobs."""
    bots = ["default", "coder", "writer", "designer", "bot5", "bot6", "bot7"]
    rows = [(f"{b}.{k}", "worker", "anthropic", "model-a", "nous", "vendor/model-b")
            for b in bots for k in ("main", "delegation")]
    rows += [(f"coder.aux.task{i}", "worker", "anthropic", "model-a", "nous", "vendor/model-b") for i in range(6)]
    rows += [(f"{bots[i % 4]}.cron.job{i:08d}", "worker", "anthropic", "model-a", "nous", "vendor/model-b")
             for i in range(11)]
    return rows


scopes_large = _synthetic_fleet()
changes_31 = [
    {"scope": sc, "bot": sc.split(".")[0], "class": cls,
     "from": [fp, fm], "to": [tp, tm],
     "rung": 2, "metered": False, "reason": "anthropic down"}
    for sc, cls, fp, fm, tp, tm in scopes_large
]
q_13_02 = {
    "anthropic":    {"ok": True, "weekly": 36.0, "session": 100.0,
                     "session_reset": "2026-09-29T11:30:00+00:00", "weekly_reset": None},
    "openai-codex": {"ok": True, "weekly": 78.0, "session": 10.0,
                     "weekly_reset": None, "session_reset": None},
    "nous":         {"ok": True, "credits": 110.02},
    "copilot":      {"ok": True, "assumed": True},
    "openrouter":   {"ok": True, "balance_usd": 0.19},
    "deepseek":     {"ok": False, "error": "no API key"},
}
msg_13_02 = m.format_alert(changes_31, {}, q_13_02, dry=False, now=NOW)
lines_13_02 = msg_13_02.splitlines()

# Should have a single group line "Claude ... → N routes moved to Nous ..."
group_lines = [l for l in lines_13_02 if "routes moved to Nous" in l]
R.check(len(group_lines) >= 1, "31-scope event: grouped into summary line(s) (not 31 bullets)")
# Should NOT list individual scopes
R.check(not any("default.cron.job00000000" in l for l in lines_13_02), "IDs not listed when >3 in group")
# Budget line present
R.check(any(l.startswith("Budget:") for l in lines_13_02), "budget line present")

# ---------------------------------------------------------------------------
# 8. ≤3 routes: list them individually in human words
# ---------------------------------------------------------------------------
changes_small = [
    {"scope": "writer.main", "bot": "writer", "class": "sensitive",
     "from": ["anthropic", "claude-sonnet-4-5"], "to": ["nous", "anthropic/claude-sonnet-4.5"],
     "rung": 2, "metered": False, "reason": "anthropic down"},
    {"scope": "default.delegation", "bot": "default", "class": "worker",
     "from": ["anthropic", "claude-sonnet-4-6"], "to": ["nous", "anthropic/claude-sonnet-5"],
     "rung": 2, "metered": False, "reason": "anthropic down"},
]
msg_small = m.format_alert(changes_small, {}, q_snap(), dry=False, now=NOW)
R.check("writer" in msg_small, "≤3 listing: bot name present")
R.check("→ Nous" in msg_small, "≤3 listing: destination provider present")

# ---------------------------------------------------------------------------
# 9. Return with away time included
# ---------------------------------------------------------------------------
msg_ret = m.format_alert(changes_return, {}, q_snap(), dry=False, now=NOW, away_minutes=31)
R.check("away 31 min" in msg_ret, "away_minutes shown on return")

# ---------------------------------------------------------------------------
# 10. Budget line: Claude session at 100% shows "resets HH:MM"
# ---------------------------------------------------------------------------
q_cap = dict(q_snap())
q_cap["anthropic"] = {"ok": True, "weekly": 36, "session": 100,
                      "session_reset": "2026-09-29T11:30:00+00:00", "weekly_reset": None}
budget = m._format_budget_line(q_cap)
R.check("resets" in budget, "session at 100% -> resets time in budget")
R.check("5h 100%" in budget, "budget shows 5h label at 100%")

# ---------------------------------------------------------------------------
# 11. Budget line: ⚠️ at/above hot threshold (70%)
# ---------------------------------------------------------------------------
q_hot = dict(q_snap())
q_hot["openai-codex"] = {"ok": True, "weekly": 80, "session": 10, "weekly_reset": None, "session_reset": None}
budget_hot = m._format_budget_line(q_hot)
R.check("⚠️" in budget_hot, "budget shows ⚠️ at 80% weekly")

# ---------------------------------------------------------------------------
# 12. Budget line: DeepSeek omitted when ok=False / no API key
# ---------------------------------------------------------------------------
budget_nodeep = m._format_budget_line(q_snap())
R.check("DeepSeek" not in budget_nodeep, "DeepSeek omitted when not ok")

# ---------------------------------------------------------------------------
# 13. Budget line: OpenRouter shown when ok=True
# ---------------------------------------------------------------------------
R.check("OpenRouter $0.19" in budget_nodeep, "OpenRouter shown when ok")

# ---------------------------------------------------------------------------
# 14. Copilot shown only when probed (not assumed)
# ---------------------------------------------------------------------------
q_cop_assumed = dict(q_snap())
q_cop_assumed["copilot"] = {"ok": True, "assumed": True}
budget_cop = m._format_budget_line(q_cop_assumed)
R.check("Copilot" not in budget_cop, "Copilot omitted when assumed (not probed)")
q_cop_probed = dict(q_snap())
q_cop_probed["copilot"] = {"ok": True, "plan": "individual"}
budget_cop2 = m._format_budget_line(q_cop_probed)
R.check("Copilot ok" in budget_cop2, "Copilot shown when probed")

# ---------------------------------------------------------------------------
# 15. Time in headline matches now parameter (timezone-agnostic check)
# ---------------------------------------------------------------------------
now_test = dt.datetime(2026, 9, 29, 13, 33, 8, tzinfo=UTC).astimezone()
expected_time = now_test.strftime("%H:%M")  # whatever local tz renders
msg_t = m.format_alert(changes_away, {}, q_snap(), dry=False, now=now_test)
R.check(expected_time in msg_t.splitlines()[0], f"headline shows correct local time ({expected_time})")

# ---------------------------------------------------------------------------
# 16. _away_minutes: reads log file correctly
# ---------------------------------------------------------------------------
logfile = tmp / "test_decisions.jsonl"
# Record a departure 31 minutes ago
import time as _time
away_ts = (dt.datetime.now(UTC) - dt.timedelta(minutes=31)).isoformat(timespec="seconds")
rec = {"ts": away_ts, "scope": "coder.main", "rung": 2, "reason": "anthropic down"}
logfile.write_text(json.dumps(rec) + "\n")
mins = m._away_minutes(["coder.main"], log_path=str(logfile))
R.check(mins is not None and 29 <= mins <= 33, f"_away_minutes reads log correctly (got {mins})")

# missing log -> None
mins_none = m._away_minutes(["coder.main"], log_path=str(tmp / "nonexistent.jsonl"))
R.check(mins_none is None, "_away_minutes returns None when log absent")

# scope not in log -> None
mins_miss = m._away_minutes(["unknown.scope"], log_path=str(logfile))
R.check(mins_miss is None, "_away_minutes returns None when scope not found")

# probe output must survive the JSON quota log (account_usage windows carry datetime reset_at)
import datetime as _dt, json as _json
_snap = {"windows": [{"label": "Session (5h)", "used_percent": 100.0,
                      "reset_at": _dt.datetime(2026, 1, 1, 13, 30, tzinfo=_dt.timezone.utc)}]}
_r = m._window_reset(_snap, "session")
R.check(isinstance(_r, str) and _json.loads(_json.dumps({"r": _r}))["r"] == _r, "reset_at becomes an ISO string (JSON-safe)")

# bots are counted per bot, not per route
_grp = [{"scope": "coder.main"}, {"scope": "coder.delegation"}, {"scope": "writer.main"}, {"scope": "writer.delegation"}]
R.check(m._bots_phrase(_grp) == "2 bots (main + subagents)", "two bots with main+subagent routes -> '2 bots (main + subagents)'")

cleanup(tmp)
R.done()
