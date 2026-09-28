"""Security and control tests: access control, input validation, never_touch, holds/force."""
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))  # noqa: E702
import sys
import types

from _harness import cleanup, load_engine, readings, Results, HERE

R = Results()
sys.path.insert(0, str(HERE))

# ---- policy validation: anything odd is refused before apply() could see it ----
bad_policies = {
    "scope with path traversal": lambda t: t.replace("coder.main: coding", "../x.main: coding"),
    "scope with dotted config key": lambda t: t.replace("coder.main: coding", "coder.aux.x.y.z: coding"),
    "model id with shell/option chars": lambda t: t.replace("gpt-5-codex]", "--help; rm -rf /]"),
    "unknown provider in a rung": lambda t: t.replace("[deepseek, deepseek-chat]]", "[evil, x]]", 1),
    "bad mode": lambda t: t.replace("mode: dry_run", "mode: yolo"),
    "bad admin entry": lambda t: t.replace("admins: []", "admins: [\"not an id\"]"),
}
for label, edit in bad_policies.items():
    m, tmp = load_engine(edit)
    try:
        m.load_policy()
        R.check(False, f"rejects {label}")
    except ValueError:
        R.check(True, f"rejects {label}")
    cleanup(tmp)

m, tmp = load_engine(lambda t: t.replace("coder.main: coding", "../x.main: coding"))
m.collect = lambda policy=None: readings()
out = m.run("test")
R.check(bool(out) and "nothing changed" in out[0] and m.applied == [], "invalid policy -> run() reports and changes nothing")
cleanup(tmp)

# ---- controls ----
m, tmp = load_engine()
m.collect = lambda policy=None: readings()
m.hold("coder.main", "note\nwith\x07control chars" + "x" * 500)
note = m.load_overrides()["holds"]["coder.main"]["note"]
R.check("\n" not in note and "\x07" not in note and len(note) <= 200, "hold note sanitised and bounded")
m._edit_policy(lambda d: d.__setitem__("mode", "enforce"))
m.run("test")
R.check("coder.main" not in [a[0] for a in m.applied], "held route not moved by run()")
m.applied.clear()
c = m.force("coder.delegation", 2, "t")
R.check(m.applied == [("coder.delegation", ["nous", "openai/gpt-5-mini"])], "force applies the chosen rung")
R.check("coder.delegation" in m.load_overrides()["holds"], "force also holds")
R.check(m.release("coder.delegation") and not m.release("coder.delegation"), "release once, then 'not held'")
for fn, label in ((lambda: m.force("nope.main", 0), "unknown route"), (lambda: m.force("coder.main", 99), "rung out of range"),
                  (lambda: m.force("coder.main", 4), "metered rung while allow_metered is off"),
                  (lambda: m.set_mode("yolo"), "bad mode"), (lambda: m.set_thresholds({"hot_weekly": 40}), "incoherent hysteresis"),
                  (lambda: m.set_thresholds({"bogus": 1}), "unknown threshold")):
    try:
        fn(); R.check(False, f"refuses {label}")
    except ValueError:
        R.check(True, f"refuses {label}")
m.set_thresholds({"hot_weekly": 75})
R.check("hot_weekly: 75" in m.POLICY.read_text() and "# a subscription is" in m.POLICY.read_text(), "threshold saved, comments kept")
cleanup(tmp)

# ---- never_touch: never read, never written ----
m, tmp = load_engine(lambda t: t.replace("writer.main: sensitive", "writer.main: sensitive\n  private.main: worker"))
reads = []
real_actual = m.actual
m.actual = lambda scope, cache: reads.append(scope) or real_actual(scope, cache)
m._edit_policy(lambda d: d.__setitem__("mode", "enforce"))
m.collect = lambda policy=None: readings(anthropic={"weekly": 90})
m.run("test"); m.overview()
R.check("private.main" not in reads, "never_touch profile config is never read")
R.check("private.main" not in [a[0] for a in m.applied], "never_touch profile is never changed")
try:
    m.force("private.main", 1); R.check(False, "force refuses never_touch")
except ValueError:
    R.check(True, "force refuses never_touch")
cleanup(tmp)

# ---- /quota access control ----
import importlib.util
spec = importlib.util.spec_from_file_location("qr_access", HERE / "_access.py")
acc = importlib.util.module_from_spec(spec); spec.loader.exec_module(acc)
POL = {"control": {"admins": ["telegram:111"]}}


def as_caller(surface, user="", chat="", in_gateway=False):
    env = {"HERMES_SESSION_PLATFORM": surface, "HERMES_SESSION_SOURCE": "", "HERMES_SESSION_USER_ID": user,
           "HERMES_SESSION_CHAT_TYPE": chat}
    acc._session = lambda name: env.get(name, "")
    if in_gateway:
        sys.modules.setdefault("gateway.run", types.ModuleType("gateway.run"))
    else:
        sys.modules.pop("gateway.run", None) if isinstance(sys.modules.get("gateway.run"), types.ModuleType) and \
            not getattr(sys.modules.get("gateway.run"), "__file__", None) else None


as_caller("cli");                         R.check(acc.check_access(POL, True)[0] is None, "local CLI may change")
as_caller("desktop");                     R.check(acc.check_access(POL, True)[0] is None, "desktop app may change")
as_caller("telegram", "999", "dm");       R.check(acc.check_access(POL, False)[0] is not None, "non-admin on Telegram: denied, even read-only")
as_caller("telegram", "111", "dm");       R.check(acc.check_access(POL, True)[0] is None, "admin in private chat may change")
as_caller("telegram", "111", "group");    R.check(acc.check_access(POL, True)[0] is not None, "admin in a group: changes refused")
as_caller("telegram", "111", "group");    R.check(acc.check_access(POL, False)[0] is None, "admin in a group: read-only allowed")
as_caller("discord", "111", "dm");        R.check(acc.check_access(POL, False)[0] is not None, "admin id is per platform")
as_caller("api_server", "", "");          R.check(acc.check_access(POL, False)[0] is not None, "API server without identity: denied")
as_caller("", "", "", in_gateway=True);   R.check(acc.check_access(POL, False)[0] is not None, "unidentified caller in gateway: denied")
as_caller("telegram", "111", "dm");       R.check(acc.check_access({}, False)[0] is not None, "no admins configured: nobody over chat")
R.done()
