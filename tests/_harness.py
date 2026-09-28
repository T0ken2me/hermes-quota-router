"""Shared test harness: loads the engine against a TEMPORARY copy of the example policy, with every
path to the live system fenced off. Nothing here can read or change a real Hermes install."""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
_hermes = Path(os.environ.get("QR_HERMES_ROOT") or Path.home() / ".hermes") / "hermes-agent"
if _hermes.is_dir():
    sys.path.insert(0, str(_hermes))
try:  # Hermes may re-launch into its managed interpreter here; do it before anything else is imported
    import hermes_bootstrap  # noqa: F401,E402
except ImportError:
    pass

FULL = {"anthropic": {"ok": True, "weekly": 20, "session": 5}, "openai-codex": {"ok": True, "weekly": 20, "session": 5},
        "nous": {"ok": True, "credits": 100}, "copilot": {"ok": True, "plan": "individual"},
        "deepseek": {"ok": True, "balance_usd": 19.6}, "openrouter": {"ok": True, "balance_usd": 0.19}}


class _NoCLI:
    def __getattr__(self, name):
        raise RuntimeError("test tried to run a subprocess: " + name)


def load_engine(policy_edit=None):
    """-> (engine module, temp dir). policy_edit(text) -> text lets a test tweak the policy."""
    tmp = Path(tempfile.mkdtemp(prefix="qr-test-"))
    text = (HERE / "examples" / "policy.example.yaml").read_text()
    if policy_edit:
        text = policy_edit(text)
    (tmp / "policy.yaml").write_text(text)
    os.environ.update(QR_HERMES_ROOT=str(tmp / "fake-hermes"), QR_STATE=str(tmp / "state.json"),
                      QR_OVERRIDES=str(tmp / "overrides.json"), QR_NOLOG="1")
    spec = importlib.util.spec_from_file_location(f"qr_engine_{tmp.name}", HERE / "engine" / "quota_router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.WS, m.POLICY = tmp, tmp / "policy.yaml"
    m.subprocess = _NoCLI()
    applied = []
    m.applied = applied
    m.apply = lambda c: applied.append((c["scope"], list(c["to"])))
    m.actual = lambda scope, cache: tuple(_rung(m, scope))
    return m, tmp


def _rung(m, scope):
    pol = m.load_policy()
    return pol["classes"][pol["scopes"][scope]][int(m.load_state().get(scope, 0))]


def readings(**over):
    q = {k: dict(v) for k, v in FULL.items()}
    for k, v in over.items():
        q[k.replace("_", "-") if k == "openai_codex" else k] = {**q.get(k, {}), **v}
    return q


def cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


class Results:
    def __init__(self):
        self.fails = 0

    def check(self, cond, label):
        self.fails += not cond
        print(("PASS " if cond else "FAIL ") + label)

    def done(self):
        print(f"\n{'FAILED' if self.fails else 'OK'} ({self.fails} failure(s))")
        sys.exit(1 if self.fails else 0)
