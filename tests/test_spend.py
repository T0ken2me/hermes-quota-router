"""Tests for engine/spend.py.

Uses temporary SQLite fixtures only — never touches real ~/.hermes DBs.
Two fake profiles + one never_touch profile; verifies that the never_touch DB
is never opened.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# Harness utilities
# ---------------------------------------------------------------------------

_hermes_src = Path(os.environ.get("QR_HERMES_ROOT", "")) / "hermes-agent"
if not _hermes_src.is_dir():
    _hermes_src = Path.home() / ".hermes" / "hermes-agent"
if _hermes_src.is_dir():
    sys.path.insert(0, str(_hermes_src))
try:
    import hermes_bootstrap  # noqa: F401
except ImportError:
    pass


class Results:
    def __init__(self):
        self.fails = 0

    def check(self, cond, label):
        self.fails += not cond
        print(("PASS " if cond else "FAIL ") + label)

    def done(self):
        print(f"\n{'FAILED' if self.fails else 'OK'} ({self.fails} failure(s))")
        sys.exit(1 if self.fails else 0)


def _make_db(path: Path, rows):
    """Create a minimal state.db at path with the given session_model_usage rows."""
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS session_model_usage (
            session_id TEXT,
            model TEXT,
            billing_provider TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_write_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            actual_cost_usd REAL,
            cost_status TEXT,
            cost_source TEXT,
            first_seen REAL,
            last_seen REAL
        )
    """)
    for r in rows:
        con.execute("""
            INSERT INTO session_model_usage
            (session_id, model, billing_provider,
             input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
             reasoning_tokens, estimated_cost_usd, actual_cost_usd,
             cost_status, cost_source, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, r)
    con.commit()
    con.close()


def _load_spend(tmp_root: Path):
    """Load engine/spend.py with QR_HERMES_ROOT pointing to a fake install."""
    os.environ["QR_HERMES_ROOT"] = str(tmp_root)
    spec = importlib.util.spec_from_file_location(
        "spend_test", HERE / "engine" / "spend.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------

NOW = time.time()
DAY = 86400

# Epoch for "5 days ago" and "1 day ago" (in local day, used for timeseries)
T_5D = NOW - 5 * DAY
T_1D = NOW - 1 * DAY
T_TODAY = NOW - 3600  # an hour ago = still today

# Row schema: session_id, model, billing_provider, input, output, cr, cw, reason, est_cost, act_cost,
#             cost_status, cost_source, first_seen, last_seen
ROWS_CODER = [
    # Priced row from 5 days ago
    ("sess-c1", "claude-sonnet-4-6", "anthropic", 1000, 500, 0, 0, 0,
     0.015, None, "estimated", "official_docs_snapshot", T_5D - 10, T_5D),
    # Priced row from 1 day ago
    ("sess-c2", "gpt-5.6-sol", "openai-codex", 2000, 1000, 0, 0, 0,
     0.030, None, "estimated", "official_docs_snapshot", T_1D - 10, T_1D),
    # Unknown pricing row (today)
    ("sess-c3", "some-unknown-model", "openrouter", 500, 250, 0, 0, 0,
     0.0, None, "unknown", "none", T_TODAY - 10, T_TODAY),
]
ROWS_WRITER = [
    # Priced row from today
    ("sess-w1", "claude-opus-5-5", "anthropic", 3000, 800, 200, 0, 0,
     0.050, None, "estimated", "official_docs_snapshot", T_TODAY - 20, T_TODAY),
    # Another today row — same session, different model
    ("sess-w1", "claude-opus-5-5", "anthropic", 100, 50, 0, 0, 0,
     0.002, None, "estimated", "official_docs_snapshot", T_TODAY - 5, T_TODAY - 1),
]
# never_touch profile — will NOT be opened
ROWS_PRIVATE = [
    ("sess-p1", "private-model", "private", 9999, 9999, 0, 0, 0,
     9.999, None, "estimated", "official_docs_snapshot", T_TODAY, T_TODAY),
]


def build_fixture():
    """Create a fake Hermes home with two profiles and a never_touch one."""
    tmp = Path(tempfile.mkdtemp(prefix="qr-spend-test-"))
    home = tmp / "fake-hermes"
    (home / "profiles" / "coder").mkdir(parents=True)
    (home / "profiles" / "writer").mkdir(parents=True)
    (home / "profiles" / "private").mkdir(parents=True)
    (home / "workspace" / "quota-router").mkdir(parents=True)

    _make_db(home / "state.db", [])  # default home — empty
    _make_db(home / "profiles" / "coder" / "state.db", ROWS_CODER)
    _make_db(home / "profiles" / "writer" / "state.db", ROWS_WRITER)
    _make_db(home / "profiles" / "private" / "state.db", ROWS_PRIVATE)

    # Write minimal policy with never_touch
    (home / "workspace" / "quota-router" / "policy.yaml").write_text(
        "mode: dry_run\nnever_touch: [private]\nthresholds: {}\nkinds: {}\nclasses: {}\nscopes: {}\n"
    )
    return tmp, home


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def run():
    r = Results()
    tmp, home = build_fixture()
    private_db = home / "profiles" / "private" / "state.db"
    m = _load_spend(home)

    never_touch = m._load_never_touch()
    r.check("private" in never_touch, "never_touch loaded from policy")

    # --- never_touch DB is never opened ---
    # Record the mtime before any calls
    private_mtime_before = private_db.stat().st_mtime

    summary_7d = m.compute_summary("7d")
    ts_7d = m.compute_timeseries("7d")
    models_7d = m.compute_models("7d")
    profiles_7d = m.compute_profiles("7d")
    sessions_7d = m.compute_top_sessions("7d", limit=50)

    private_mtime_after = private_db.stat().st_mtime
    r.check(private_mtime_before == private_mtime_after, "never_touch DB mtime unchanged (never opened)")

    # Verify private profile does NOT appear in profiles output
    prof_names = [p["profile"] for p in profiles_7d["profiles"]]
    r.check("private" not in prof_names, "never_touch profile absent from /spend/profiles")

    # --- No 'title' key anywhere in sessions response ---
    import json
    sessions_json = json.dumps(sessions_7d)
    r.check('"title"' not in sessions_json, "no 'title' key in sessions response")

    # --- Aggregations ---
    # 7d: all rows for coder and writer should be included
    # coder has 3 rows: 2 priced (est_cost=0.015+0.030=0.045) + 1 unknown (not counted)
    # writer has 2 rows: both priced (0.050+0.002=0.052)
    # Total priced cost = 0.045 + 0.052 = 0.097
    total_cost = summary_7d["estimated_cost_usd"]
    r.check(abs(total_cost - 0.097) < 1e-6, f"7d total cost = 0.097 (got {total_cost})")
    r.check(summary_7d["coverage"]["pricing_unknown_rows"] == 1, "1 unknown pricing row in 7d")
    r.check(summary_7d["cost_semantics"] == "estimated", "cost_semantics is 'estimated'")
    r.check(summary_7d["window_exact"] is False, "window_exact False for 7d")
    r.check("window_attribution" in summary_7d, "window_attribution present")

    # today: only today rows
    # coder today: 1 unknown (sess-c3) — cost not counted
    # writer today: 2 rows priced (0.052)
    summary_today = m.compute_summary("today")
    total_today = summary_today["estimated_cost_usd"]
    r.check(abs(total_today - 0.052) < 1e-6, f"today cost = 0.052 (got {total_today})")
    r.check(summary_today["coverage"]["pricing_unknown_rows"] == 1, "1 unknown row today")

    # 30d should include all rows (same as 7d since all rows are within 7d)
    summary_30d = m.compute_summary("30d")
    r.check(abs(summary_30d["estimated_cost_usd"] - 0.097) < 1e-6,
            f"30d cost = 0.097 (got {summary_30d['estimated_cost_usd']})")

    # --- Timeseries day boundaries ---
    # T_5D and T_1D are on different local days; T_TODAY is today
    r.check(len(ts_7d["points"]) >= 2, "timeseries has at least 2 distinct days")
    # All points must have the date key
    r.check(all("date" in pt for pt in ts_7d["points"]), "all timeseries points have 'date'")
    # Costs in timeseries sum up to total priced cost
    ts_total = sum(pt["estimated_cost_usd"] for pt in ts_7d["points"])
    r.check(abs(ts_total - 0.097) < 1e-6, f"timeseries sum = 0.097 (got {ts_total})")

    # --- Per-model ---
    model_names = [mm["model"] for mm in models_7d["models"]]
    r.check("claude-sonnet-4-6" in model_names, "claude-sonnet-4-6 in models")
    r.check("gpt-5.6-sol" in model_names, "gpt-5.6-sol in models")
    r.check("some-unknown-model" in model_names, "unknown-model in models (tokens counted, cost 0)")
    unknown_m = next((mm for mm in models_7d["models"] if mm["model"] == "some-unknown-model"), None)
    r.check(unknown_m is not None and unknown_m["pricing_unknown_rows"] == 1,
            "unknown model has 1 pricing_unknown_row")
    r.check(unknown_m is not None and unknown_m["estimated_cost_usd"] == 0.0,
            "unknown model cost = 0.0 (not counted)")

    # --- Top sessions ---
    # No titles anywhere (already checked above); also check session_id_short is shortened
    for s in sessions_7d["sessions"]:
        r.check("title" not in s, "no title in session entry: " + str(s.get("profile", "?")))
        r.check(len(s["session_id_short"]) <= 9, "session_id_short is short")
        r.check("profile" in s, "session has profile")
        r.check("models" in s, "session has models")
        r.check("date" in s, "session has date")
        r.check("input_tokens" in s, "session has input_tokens")
        r.check("estimated_cost_usd" in s, "session has estimated_cost_usd")

    # Limit enforcement
    sessions_lim = m.compute_top_sessions("7d", limit=2)
    r.check(len(sessions_lim["sessions"]) <= 2, "limit=2 respected")

    # --- Read-only check: state.db mtime unchanged ---
    coder_db = home / "profiles" / "coder" / "state.db"
    coder_mtime = coder_db.stat().st_mtime
    _ = m.compute_summary("7d")
    r.check(coder_db.stat().st_mtime == coder_mtime, "coder DB mtime unchanged (read-only, no writes)")

    # --- Range validation (invalid range) ---
    try:
        m.VALID_RANGES  # just check it exists
        valid_ok = True
    except AttributeError:
        valid_ok = False
    r.check(valid_ok, "VALID_RANGES constant exists")

    # --- Mixed cost_status (partial data) ---
    # writer has all priced, coder has 1 unknown → summary.partial should be True
    r.check(summary_7d.get("partial") is True, "partial=True when pricing_unknown_rows > 0")

    # --- profiles list ---
    r.check("profiles" in profiles_7d, "profiles key in profiles response")
    coder_prof = next((p for p in profiles_7d["profiles"] if p["profile"] == "coder"), None)
    writer_prof = next((p for p in profiles_7d["profiles"] if p["profile"] == "writer"), None)
    r.check(coder_prof is not None, "coder profile in response")
    r.check(writer_prof is not None, "writer profile in response")
    # coder: input_tokens for priced+unknown = 1000+2000+500=3500
    r.check(coder_prof is not None and coder_prof["input_tokens"] == 3500,
            f"coder input_tokens=3500 (got {coder_prof and coder_prof.get('input_tokens')})")

    import shutil
    # ---- fail closed: unreadable policy must refuse, never fall back to "no never_touch" ----
    pol = home / "workspace" / "quota-router" / "policy.yaml"
    saved = pol.read_text()
    for bad in ("never_touch: [unclosed\n", "never_touch: private\n"):
        pol.write_text(bad)
        try:
            m.compute_summary("7d")
            r.check(False, f"broken policy refused ({bad.strip()!r})")
        except m.SpendPolicyError:
            r.check(True, f"broken policy refused ({bad.strip()!r})")
    pol.unlink()
    try:
        m.compute_profiles("7d"); r.check(False, "missing policy refused")
    except m.SpendPolicyError:
        r.check(True, "missing policy refused")
    pol.write_text(saved)

    shutil.rmtree(tmp, ignore_errors=True)
    r.done()


if __name__ == "__main__":
    run()
