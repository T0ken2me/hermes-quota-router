#!/usr/bin/env python3
"""Quota router — collector + tiered-ladder decision engine (dry-run by default).

Ladder per task class: subscriptions -> Nous credits -> flat-fee plans -> metered API with credit.
Runs as a --no-agent cron job (zero LLM tokens); prints ONLY when a decision changes.
Cron runs it through the launcher <hermes root>/scripts/quota_router.py (installed by
scripts/install.sh). Policy + state: <hermes root>/workspace/quota-router/. Docs: README.md, docs/DESIGN.md.
Test hooks: QR_FAKE='{"openai-codex":{"weekly":75}}' overrides readings; QR_STATE=<path> isolates state;
QR_NOLOG=1 skips the jsonl logs; QR_DRYRUN=1 forces dry-run (tests MUST set it).
"""
import dataclasses, datetime as dt, json, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Fleet root (default profile home; named profiles live under profiles/). Override with QR_HERMES_ROOT.
HOME = Path(os.environ.get("QR_HERMES_ROOT") or Path.home() / ".hermes")
# The cron scheduler runs scripts with the gateway's bare managed interpreter (sys.executable);
# Hermes' dependencies (yaml, dotenv, httpx…) only become importable after hermes_bootstrap.
# Source installs keep the checkout in <root>/hermes-agent; package installs already have it on path.
if (HOME / "hermes-agent").is_dir():
    sys.path.insert(0, str(HOME / "hermes-agent"))
try:
    import hermes_bootstrap  # noqa: F401,E402
except ImportError:
    pass
WS = HOME / "workspace" / "quota-router"
STATE = Path(os.environ.get("QR_STATE", WS / "state.json"))
QUOTA_LOG, DECISION_LOG = WS / "quota.jsonl", WS / "decisions.jsonl"
NOLOG = bool(os.environ.get("QR_NOLOG"))
OVERRIDES = Path(os.environ.get("QR_OVERRIDES", WS / "overrides.json"))
POLICY = WS / "policy.yaml"


class _Lock:
    """Exclusive lock shared by the cron run, the dashboard plugin and /quota: every read-modify-write
    of state/overrides/policy happens under it so a UI click never races a scheduled run."""
    def __enter__(self):
        import fcntl
        WS.mkdir(parents=True, exist_ok=True)
        self.f = open(WS / ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


_NAME = r"[a-z0-9][a-z0-9_-]{0,63}"
SCOPE_RE = re.compile(rf"^(?P<bot>{_NAME})\.(main|delegation|aux\.[a-z0-9_]{{1,64}}|cron\.[A-Za-z0-9_-]{{1,64}})$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
KINDS = ("subscription", "credits", "flat", "metered")


def validate_policy(p):
    """Reject anything that could make apply() write an unexpected config key, reach outside the
    profiles directory, or pass a strange value to the CLI. Raises ValueError."""
    if not isinstance(p, dict):
        raise ValueError("policy.yaml is empty or not a mapping")
    for key in ("thresholds", "kinds", "classes", "scopes"):
        if not isinstance(p.get(key), dict):
            raise ValueError(f"policy.yaml: '{key}' section missing")
    if p.get("mode", "dry_run") not in ("dry_run", "enforce"):
        raise ValueError("policy.yaml: mode must be dry_run or enforce")
    for prov, kind in p["kinds"].items():
        if kind not in KINDS or not re.fullmatch(_NAME, str(prov)):
            raise ValueError(f"policy.yaml: bad kind entry {prov}: {kind}")
    # Optional deny-list: providers (or aggregator vendor prefixes such as "vendor/model")
    # that must never appear in any ladder. Empty by default. Local models without a
    # vendor prefix are not matched, so a local model can share a name with a blocked vendor.
    blocked = p.get("blocked_providers") or []
    if not isinstance(blocked, list) or not all(re.fullmatch(_NAME, str(b)) for b in blocked):
        raise ValueError("policy.yaml: blocked_providers must be a list of provider/vendor names")
    blocked = {str(b).lower() for b in blocked}
    for cls, ladder in p["classes"].items():
        for rung in ladder if isinstance(ladder, list) else []:
            if isinstance(rung, list) and len(rung) == 2 and (
                    str(rung[0]).lower() in blocked
                    or ("/" in str(rung[1]) and str(rung[1]).split("/")[0].lower() in blocked)):
                raise ValueError(f"policy.yaml: class {cls}: rung {rung} uses a blocked provider/vendor")
    for cls, ladder in p["classes"].items():
        if not isinstance(ladder, list) or not ladder:
            raise ValueError(f"policy.yaml: class {cls} has no ladder")
        for rung in ladder:
            if not (isinstance(rung, list) and len(rung) == 2 and rung[0] in p["kinds"] and _MODEL_RE.match(str(rung[1]))):
                raise ValueError(f"policy.yaml: class {cls}: bad rung {rung} (provider must be listed in kinds)")
    for scope, cls in p["scopes"].items():
        if not SCOPE_RE.match(str(scope)):
            raise ValueError(f"policy.yaml: bad scope name {scope!r}")
        if cls not in p["classes"]:
            raise ValueError(f"policy.yaml: scope {scope} uses unknown class {cls}")
    admins = (p.get("control") or {}).get("admins") or []
    if not isinstance(admins, list) or not all(re.fullmatch(r"[a-z0-9_-]+:[A-Za-z0-9_.@-]+", str(a)) for a in admins):
        raise ValueError("policy.yaml: control.admins must be a list of 'platform:user_id'")
    return p


def load_policy():
    import yaml
    return validate_policy(yaml.safe_load(POLICY.read_text()))


def _untouchable(policy, scope):
    return scope.split(".")[0] in (policy.get("never_touch") or [])


def _clean_note(note):
    """Free text from chat/UI: printable, single line, bounded."""
    note = "".join(c for c in str(note or "") if c.isprintable())
    return note[:200]


def load_state():
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    return st


def save_state(st):
    STATE.write_text(json.dumps(st, indent=1))


def load_overrides():
    o = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    o.setdefault("holds", {})
    return o


def save_overrides(o):
    OVERRIDES.write_text(json.dumps(o, indent=1))


def log_decision(rec):
    if not NOLOG:
        with DECISION_LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")


def _env():
    from dotenv import dotenv_values
    return {**dotenv_values(HOME / ".env"), **os.environ}


def _http_json(url, headers):
    req = urllib.request.Request(url, headers={"User-Agent": "quota-router", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _window(snap, *needles):
    for w in (snap or {}).get("windows", []):
        if any(n in (w.get("label") or "").lower() for n in needles):
            return w.get("used_percent")
    return None


def _subscription(p):
    from agent.account_usage import fetch_account_usage
    s = fetch_account_usage(p)
    d = dataclasses.asdict(s) if s else None
    if not d or d.get("unavailable_reason"):
        return {"ok": False, "unknown": True, "error": (d or {}).get("unavailable_reason") or "no snapshot"}
    return {"ok": True, "weekly": _window(d, "week"), "session": _window(d, "session")}


def _nous():
    from hermes_cli.nous_account import get_nous_portal_account_info
    from agent.account_usage import build_nous_credits_snapshot
    info = get_nous_portal_account_info(force_fresh=True)
    if not getattr(info, "logged_in", False):
        return {"ok": False, "error": "logged out"}
    snap = build_nous_credits_snapshot(info)
    d = dataclasses.asdict(snap) if snap else {}
    credits = None  # usable credits appear in details as "Total usable: N"; unknown = ineligible
    for line in d.get("details") or []:
        if "usable" in str(line).lower():
            try:
                credits = float("".join(c for c in str(line).split(":")[-1] if c in "0123456789."))
            except ValueError:
                pass
    if credits is None:
        return {"ok": False, "unknown": True, "error": "credits unreadable"}
    return {"ok": True, "credits": credits}


def _copilot():
    # Undocumented endpoint (the one editors use); opt-in only via policy probes.copilot: true.
    tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15).stdout.strip()
    if not tok:
        return {"ok": False, "error": "no gh token"}
    d = _http_json("https://api.github.com/copilot_internal/user",
                   {"Authorization": f"token {tok}", "Editor-Version": "vscode/1.99"})
    return {"ok": bool(d.get("copilot_plan")), "plan": d.get("copilot_plan")}


def _deepseek(env):
    if not env.get("DEEPSEEK_API_KEY"):
        return {"ok": False, "error": "no API key"}
    d = _http_json("https://api.deepseek.com/user/balance", {"Authorization": "Bearer " + env["DEEPSEEK_API_KEY"]})
    usd = sum(float(b["total_balance"]) for b in d.get("balance_infos", []) if b.get("currency") == "USD")
    return {"ok": True, "balance_usd": round(usd, 2)}


def _openrouter(env):
    if not env.get("OPENROUTER_API_KEY"):
        return {"ok": False, "error": "no API key"}
    d = _http_json("https://openrouter.ai/api/v1/credits", {"Authorization": "Bearer " + env["OPENROUTER_API_KEY"]})["data"]
    return {"ok": True, "balance_usd": round(d["total_credits"] - d["total_usage"], 2)}


def collect(policy=None):
    """Probe only the providers the policy uses. Keys are read from the Hermes .env/environment and
    only ever sent to that provider's own endpoint; nothing is logged but the readings."""
    env = _env()
    probes = (policy or {}).get("probes") or {}
    wanted = set((policy or {}).get("kinds") or {}) or None
    copilot = _copilot if probes.get("copilot") else (lambda: {"ok": True, "assumed": True})
    jobs = {"anthropic": lambda: _subscription("anthropic"), "openai-codex": lambda: _subscription("openai-codex"),
            "nous": _nous, "copilot": copilot,
            "deepseek": lambda: _deepseek(env), "openrouter": lambda: _openrouter(env)}
    if wanted is not None:
        jobs = {k: f for k, f in jobs.items() if k in wanted}
    out = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {k: pool.submit(f) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                out[k] = f.result(timeout=90)
            except Exception as e:  # unknown != zero: record, treat as ineligible
                out[k] = {"ok": False, "unknown": True, "error": f"{type(e).__name__}: {e}"[:160]}
    fake = os.environ.get("QR_FAKE")
    if fake:
        for p, v in json.loads(fake).items():
            out[p] = {"ok": True, "fake": True, **v}
    return out


def status(provider, q, kind, t):
    """-> 'room' (may receive load), 'ok' (may keep load), 'hot' (should shed), 'down' (confirmed
    unusable), 'unknown' (probe failed: never a reason to move, never a destination)."""
    r = q.get(provider) or {}
    if not r or r.get("unknown"):
        return "unknown"
    if not r.get("ok"):
        return "down"
    if kind == "subscription":
        w, s = r.get("weekly") or 0, r.get("session") or 0
        if w >= 100 or s >= 100:
            return "down"
        if w >= t["hot_weekly"] or s >= t["hot_session"]:
            return "hot"
        return "room" if (w < t["room_weekly"] and s < t["room_session"]) else "ok"
    if kind == "credits":
        return "room" if (r.get("credits") or 0) > 0 else "down"
    if kind == "flat":
        return "room"
    if kind == "metered":
        if not t.get("_allow_metered"):
            return "down"  # pay-per-use is opt-in: policy allow_metered: true
        return "room" if (r.get("balance_usd") or 0) >= t["min_balance_usd"] else "down"
    return "down"


def cooled(provider, q, kind, t):
    r = q.get(provider) or {}
    if kind != "subscription":
        return status(provider, q, kind, t) == "room"
    return bool(r.get("ok")) and (r.get("weekly") or 0) < t["return_weekly"] and (r.get("session") or 0) < t["return_session"]


def _t(policy):
    return {**policy["thresholds"], "_allow_metered": bool(policy.get("allow_metered", False))}


def disabled_providers(bot, cache=None):
    """Providers the bot's own config switches off (providers.<p>.enabled: false). Routing a bot onto
    one of these breaks it outright (gateway: "provider 'x' is disabled in config"), so they are
    never a destination. Unreadable/missing config -> nothing known to be disabled."""
    cache = {} if cache is None else cache
    if bot not in cache:
        try:
            import yaml
            c = yaml.safe_load((_bot_home(bot) / "config.yaml").read_text()) or {}
            cache[bot] = {p for p, v in (c.get("providers") or {}).items()
                          if isinstance(v, dict) and v.get("enabled") is False}
        except Exception:
            cache[bot] = set()
    return cache[bot]


def decide(policy, q, state, skip=(), off=None):
    """off(bot) -> set of providers disabled for that bot (injectable for tests)."""
    t, kinds, changes = _t(policy), policy["kinds"], []
    _cache = {}
    off = off or (lambda b: disabled_providers(b, _cache))
    for scope, cls in policy["scopes"].items():
        bot = scope.split(".")[0]
        if bot in policy.get("never_touch", []) or scope in skip:
            continue
        ladder = policy["classes"][cls]
        dis = off(bot)
        cur = min(int(state.get(scope, 0)), len(ladder) - 1)
        prov = ladder[cur][0]
        st = "down" if prov in dis else status(prov, q, kinds[prov], t)
        new, why = cur, None
        # 1) climb back: highest rung above current that has cooled and has room
        for j in range(cur):
            pj = ladder[j][0]
            if pj not in dis and cooled(pj, q, kinds[pj], t) and status(pj, q, kinds[pj], t) == "room":
                new, why = j, f"{pj} recovered"
                break
        # 2) shed: current is hot/down -> first rung (top-down) on another provider that has room
        if new == cur and st in ("hot", "down"):
            for j, (pj, _) in enumerate(ladder):
                if pj != prov and pj not in dis and status(pj, q, kinds[pj], t) == "room":
                    new, why = j, f"{prov} {st}"
                    break
        # 3) stranded on a provider the bot has disabled and nothing better: back to rung 0 and let the
        #    bot's own reactive fallback chain handle a 429 — a disabled provider never resolves at all.
        if new == cur and prov in dis and cur != 0:
            new, why = 0, f"{prov} disabled in {bot} config"
        if new != cur:
            to = ladder[new]
            changes.append({"scope": scope, "bot": bot, "class": cls, "from": ladder[cur], "to": to, "rung": new,
                            "metered": kinds[to[0]] == "metered", "reason": why})
    return changes


def _bot_home(bot):
    if not re.fullmatch(_NAME, bot):
        raise ValueError(f"bad profile name {bot!r}")
    return HOME if bot == "default" else HOME / "profiles" / bot


def _wrapper(bot):
    """CLI argv prefix that targets a profile. Always explicit, even for default: a bare `hermes`
    follows the caller's HERMES_HOME, so a run from inside a profile shell would edit that profile."""
    return ["hermes", "-p", bot]


def _keys(scope):
    """scope grammar: <bot>.main | <bot>.delegation | <bot>.aux.<task> | <bot>.cron.<job_id>"""
    if not SCOPE_RE.match(scope):
        raise ValueError(f"bad scope {scope}")
    parts = scope.split(".")
    bot, kind = parts[0], parts[1]
    if kind == "main":
        return bot, "config", ("model.provider", "model.default")
    if kind == "delegation":
        return bot, "config", ("delegation.provider", "delegation.model")
    if kind == "aux":
        return bot, "config", (f"auxiliary.{parts[2]}.provider", f"auxiliary.{parts[2]}.model")
    if kind == "cron":
        return bot, "cron", parts[2]
    raise ValueError(f"bad scope {scope}")


def _get(d, dotted):
    for k in dotted.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _cron_jobs(bot):
    f = _bot_home(bot) / "cron" / "jobs.json"
    try:
        j = json.loads(f.read_text())
    except Exception:
        return {}
    J = j.get("jobs", j) if isinstance(j, dict) else j
    return {x.get("id"): x for x in J if isinstance(x, dict)}


def actual(scope, cfg_cache):
    """-> (provider, model) currently configured, or None if the target no longer exists."""
    import yaml
    bot, where, keys = _keys(scope)
    if where == "cron":
        job = _cron_jobs(bot).get(keys)
        return None if job is None else (job.get("provider"), job.get("model"))
    if bot not in cfg_cache:
        cfg_cache[bot] = yaml.safe_load((_bot_home(bot) / "config.yaml").read_text()) or {}
    c = cfg_cache[bot]
    return (_get(c, keys[0]), _get(c, keys[1]))


def apply(change):
    bot, where, keys = _keys(change["scope"])
    prov, model = change["to"]
    if where == "cron":
        subprocess.run([*_wrapper(bot), "cron", "edit", keys, "--provider", prov, "--model", model],
                       check=True, capture_output=True, timeout=90)
        return
    for k, v in zip(keys, (prov, model)):
        subprocess.run([*_wrapper(bot), "config", "set", k, v], check=True, capture_output=True, timeout=60)


def drift(policy, state):
    """Scopes whose live config differs from the rung the router believes is active (hand edits,
    deleted jobs). Reported, never auto-corrected: a human change wins until policy.yaml is updated."""
    out, cache = {}, {}
    for scope, cls in policy["scopes"].items():
        if _untouchable(policy, scope):
            continue  # never read a protected profile's config
        ladder = policy["classes"][cls]
        want = ladder[min(int(state.get(scope, 0)), len(ladder) - 1)]
        try:
            have = actual(scope, cache)
        except Exception as e:
            out[scope] = f"unreadable ({type(e).__name__})"
            continue
        if have is None:
            out[scope] = "target gone"
        elif (have[0], have[1]) != (want[0], want[1]):
            out[scope] = f"live {have[0]}/{have[1]} != policy {want[0]}/{want[1]}"
    return out


def _brief(q):
    b = []
    for p in ("anthropic", "openai-codex"):
        r = q.get(p, {})
        b.append(f"{p} wk {r.get('weekly')}%" if r.get("ok") else f"{p} ?")
    r = q.get("nous", {})
    b.append(f"nous {r.get('credits')} cr" if r.get("ok") else f"nous {r.get('error', '?')}")
    b.append("copilot " + ("ok" if q.get("copilot", {}).get("ok") else "?"))
    for p in ("deepseek", "openrouter"):
        r = q.get(p, {})
        b.append(f"{p} ${r.get('balance_usd')}" if r.get("ok") else f"{p} ?")
    return " · ".join(b)


def run(source="cron"):
    """One router pass. Returns the notification lines (empty = nothing to report)."""
    WS.mkdir(parents=True, exist_ok=True)
    try:
        q = collect(load_policy())  # network probes happen outside the lock
    except ValueError as e:
        return [f"⚠️ Quota router: {e} — nothing changed."]
    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _Lock():
        policy = load_policy()
        if not NOLOG:
            with QUOTA_LOG.open("a") as f:
                f.write(json.dumps({"ts": ts, **q}) + "\n")
        raw = load_state()
        prev_drift = raw.get("_drift") if isinstance(raw.get("_drift"), dict) else {}
        state = {k: v for k, v in raw.items() if isinstance(v, int)}
        holds = load_overrides()["holds"]
        dr = drift(policy, state)
        changes = decide(policy, q, state, skip=set(dr) | set(holds))
        # QR_DRYRUN=1 forces dry-run whatever policy.yaml says: every test harness sets it, so a
        # scenario run can never rewrite live profile configs or cron jobs.
        dry = policy.get("mode", "dry_run") != "enforce" or bool(os.environ.get("QR_DRYRUN"))
        lines = []
        for c in changes:
            applied = False
            if not dry:
                try:
                    apply(c); applied = True
                except Exception as e:
                    c["apply_error"] = str(e)[:200]
            if dry or applied:
                state[c["scope"]] = c["rung"]
            log_decision({"ts": ts, "mode": "dry_run" if dry else "enforce", "applied": applied, "source": source, **c})
            tag = "WOULD switch" if dry else ("switched" if applied else "FAILED")
            warn = " 💳 METERED" if c["metered"] else ""
            lines.append(f"• {c['scope']}: {tag} {c['from'][1]} → {c['to'][0]}/{c['to'][1]}{warn} — {c['reason']}")
        for k in (k for k, v in dr.items() if prev_drift.get(k) != v):
            lines.append(f"• ⚠️ {k}: {dr[k]} — held; update policy.yaml or restore the setting")
        save_state({**state, "_drift": dr})
    if lines:
        head = ("🧪 Quota router (dry-run)" if dry else "🔀 Quota router") + f" — {len(lines)} item(s)"
        return [head, *lines, _brief(q)]
    return []


def force(scope, rung, note="", who="manual"):
    """Human override: move <scope> to ladder rung <rung> NOW (regardless of mode) and HOLD it there
    so the automatic router leaves it alone until released."""
    with _Lock():
        policy = load_policy()
        if scope not in policy["scopes"]:
            raise ValueError(f"unknown scope {scope}")
        if _untouchable(policy, scope):
            raise ValueError(f"{scope} is in never_touch")
        ladder = policy["classes"][policy["scopes"][scope]]
        if not 0 <= rung < len(ladder):
            raise ValueError(f"rung must be 0..{len(ladder) - 1}")
        if policy["kinds"][ladder[rung][0]] == "metered" and not policy.get("allow_metered", False):
            raise ValueError("that rung is a pay-per-use API and allow_metered is off in policy.yaml")
        note = _clean_note(note)
        raw = load_state()
        cur = int(raw.get(scope, 0)) if isinstance(raw.get(scope, 0), int) else 0
        c = {"scope": scope, "bot": scope.split(".")[0], "class": policy["scopes"][scope], "from": ladder[cur],
             "to": ladder[rung], "rung": rung, "metered": policy["kinds"][ladder[rung][0]] == "metered",
             "reason": f"forced by {who}" + (f": {note}" if note else "")}
        apply(c)
        raw[scope] = rung
        if isinstance(raw.get("_drift"), dict):
            raw["_drift"].pop(scope, None)
        save_state(raw)
        o = load_overrides()
        o["holds"][scope] = {"note": note or "forced", "by": who, "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        save_overrides(o)
        log_decision({"ts": o["holds"][scope]["ts"], "mode": "manual", "applied": True, "source": who, **c})
        return c


def hold(scope, note="", who="manual"):
    note = _clean_note(note)
    with _Lock():
        if scope not in load_policy()["scopes"]:
            raise ValueError(f"unknown scope {scope}")
        o = load_overrides()
        o["holds"][scope] = {"note": note or "held", "by": who, "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        save_overrides(o)


def release(scope, who="manual"):
    """Drop a hold. If the live setting was changed by hand meanwhile, adopt it when it matches a ladder
    rung (so releasing never reverts a deliberate human choice); otherwise it will show as drift."""
    with _Lock():
        o = load_overrides()
        existed = o["holds"].pop(scope, None) is not None
        save_overrides(o)
        policy = load_policy()
        if scope in policy["scopes"] and not _untouchable(policy, scope):
            ladder = policy["classes"][policy["scopes"][scope]]
            have = actual(scope, {})
            raw = load_state()
            for i, r in enumerate(ladder):
                if have and (have[0], have[1]) == (r[0], r[1]):
                    raw[scope] = i
                    save_state(raw)
                    break
        return existed


def set_mode(mode, who="manual"):
    if mode not in ("dry_run", "enforce"):
        raise ValueError("mode must be dry_run or enforce")
    _edit_policy(lambda d: d.__setitem__("mode", mode))
    log_decision({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "mode": mode,
                  "source": who, "event": "mode_change"})


THRESHOLD_KEYS = ("hot_weekly", "hot_session", "room_weekly", "room_session", "return_weekly", "return_session", "min_balance_usd")


def set_thresholds(values, who="manual"):
    clean = {}
    for k, v in values.items():
        if k not in THRESHOLD_KEYS:
            raise ValueError(f"unknown threshold {k}")
        v = float(v)
        if k != "min_balance_usd" and not 0 < v <= 100:
            raise ValueError(f"{k} must be in (0, 100]")
        if k == "min_balance_usd" and v < 0:
            raise ValueError("min_balance_usd must be >= 0")
        clean[k] = int(v) if v.is_integer() else v
    merged = {**load_policy()["thresholds"], **clean}
    # anti-flap: a route must come back only below the hot trigger, and a provider may only receive
    # load below it (room == hot is fine: "room" is < and "hot" is >=)
    for w in ("weekly", "session"):
        if not (merged[f"return_{w}"] < merged[f"hot_{w}"] and merged[f"room_{w}"] <= merged[f"hot_{w}"]):
            raise ValueError(f"need return_{w} < hot_{w} and room_{w} <= hot_{w}")
    _edit_policy(lambda d: d["thresholds"].update(clean))
    log_decision({"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source": who,
                  "event": "thresholds", "values": clean})
    return merged


def _edit_policy(fn):
    """Round-trip edit (keeps comments/order) under the lock."""
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    with _Lock():
        d = y.load(POLICY.read_text())
        fn(d)
        tmp = POLICY.with_suffix(".yaml.tmp")
        with tmp.open("w") as f:
            y.dump(d, f)
        tmp.replace(POLICY)


def last_quota():
    try:
        with QUOTA_LOG.open() as f:
            last = None
            for last in f:
                pass
        return json.loads(last) if last else {}
    except FileNotFoundError:
        return {}


def overview():
    """Everything the UI/Telegram needs, from files only (no network probes)."""
    policy, raw, holds = load_policy(), load_state(), load_overrides()["holds"]
    dr = raw.get("_drift") if isinstance(raw.get("_drift"), dict) else {}
    cache, routes = {}, []
    for scope, cls in policy["scopes"].items():
        ladder = policy["classes"][cls]
        cur = min(int(raw.get(scope, 0)) if isinstance(raw.get(scope, 0), int) else 0, len(ladder) - 1)
        live = None
        if not _untouchable(policy, scope):
            try:
                live = actual(scope, cache)
            except Exception:
                live = None
        routes.append({"scope": scope, "class": cls, "rung": cur, "ladder": ladder, "live": list(live) if live else None,
                       "hold": holds.get(scope), "drift": dr.get(scope),
                       "never_touch": scope.split(".")[0] in policy.get("never_touch", [])})
    return {"mode": policy.get("mode"), "thresholds": policy["thresholds"], "kinds": policy["kinds"],
            "allow_metered": bool(policy.get("allow_metered", False)),
            "quota": last_quota(), "routes": routes}


def recent_decisions(n=50):
    try:
        lines = DECISION_LOG.read_text().splitlines()[-n:]
    except FileNotFoundError:
        return []
    out = []
    for l in reversed(lines):
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def main():
    lines = run("cron")
    if lines:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
