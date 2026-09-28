"""Ladder behaviour on the example policy: fallback order, hysteresis, unknown readings, opt-ins."""
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))  # noqa: E702
from _harness import cleanup, load_engine, readings, Results

R = Results()


def on(m, scope):
    pol = m.load_policy()
    return pol["classes"][pol["scopes"][scope]][int(m.load_state().get(scope, 0))][0]


def step(m, q):
    m.collect = lambda policy=None: q
    m.run("test")


def allow_metered(t):
    return t.replace("allow_metered: false", "allow_metered: true")


S = "coder.main"   # coding: codex -> anthropic -> nous -> copilot -> deepseek
m, tmp = load_engine(allow_metered)
m.load_policy()["mode"]  # smoke
m._edit_policy(lambda d: d.__setitem__("mode", "enforce"))
step(m, readings()); R.check(on(m, S) == "openai-codex", "healthy: first rung")
step(m, readings(**{"openai-codex": {"weekly": 75}})); R.check(on(m, S) == "anthropic", "codex hot -> other subscription")
step(m, readings(**{"openai-codex": {"weekly": 95}}, anthropic={"weekly": 92})); R.check(on(m, S) == "nous", "both hot -> Nous")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 100}, nous={"credits": 0}))
R.check(on(m, S) == "copilot", "Nous empty -> flat-fee plan")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 100}, nous={"credits": 0}, copilot={"ok": False}))
R.check(on(m, S) == "deepseek", "copilot down -> metered with credit (allow_metered on)")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 100}, nous={"credits": 0}, copilot={"ok": False},
                 deepseek={"balance_usd": 0.5}))
R.check(on(m, S) == "deepseek", "nothing eligible -> hold")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"ok": False, "unknown": True}, nous={"credits": 0},
                 copilot={"ok": False}, deepseek={"balance_usd": 0.5}))
R.check(on(m, S) == "deepseek", "unknown reading -> no move")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 60}))
R.check(on(m, S) == "deepseek" or on(m, S) != "anthropic", "anthropic at 60% (above return 55) -> not yet back")
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 3, "session": 1}))
R.check(on(m, S) == "anthropic", "anthropic reset -> climbs back")
step(m, readings(**{"openai-codex": {"weekly": 2, "session": 1}}, anthropic={"weekly": 3}))
R.check(on(m, S) == "openai-codex", "codex reset -> back to first rung")
cleanup(tmp)

# metered is opt-in: default example policy never lands on a pay-per-use rung
m, tmp = load_engine()
m._edit_policy(lambda d: d.__setitem__("mode", "enforce"))
step(m, readings(**{"openai-codex": {"weekly": 100}}, anthropic={"weekly": 100}, nous={"credits": 0}, copilot={"ok": False}))
R.check(on(m, S) != "deepseek", "allow_metered off -> never moves to a metered rung")
cleanup(tmp)

# dry-run: state records what WOULD happen, nothing applied
m, tmp = load_engine()
step(m, readings(**{"openai-codex": {"weekly": 75}}))
R.check(m.applied == [], "dry_run applies nothing")
cleanup(tmp)

# QR_DRYRUN overrides an enforce policy
import os
m, tmp = load_engine()
m._edit_policy(lambda d: d.__setitem__("mode", "enforce"))
os.environ["QR_DRYRUN"] = "1"
step(m, readings(**{"openai-codex": {"weekly": 75}}))
R.check(m.applied == [], "QR_DRYRUN=1 blocks apply even in enforce")
del os.environ["QR_DRYRUN"]
cleanup(tmp)

# the sensitive class has no metered rung at all
m, tmp = load_engine()
pol = m.load_policy()
R.check(all(pol["kinds"][p] != "metered" for p, _ in pol["classes"]["sensitive"]), "example 'sensitive' class has no metered rung")
cleanup(tmp)
R.done()
