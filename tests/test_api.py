"""Dashboard/desktop API: same-origin + JSON enforcement, rate limits, no internal error leaks."""
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))  # noqa: E702
import importlib.util
import sys

from _harness import cleanup, load_engine, HERE, Results

R = Results()
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError as _e:
    print(f"SKIP fastapi test client unavailable: {_e}"); sys.exit(0)

m, tmp = load_engine()
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("qr_api", HERE / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
api.engine = lambda: m
app = FastAPI(); app.include_router(api.router, prefix="/api/plugins/quota-router")
c = TestClient(app, base_url="http://127.0.0.1:9119")
B = "/api/plugins/quota-router"
J = {"Content-Type": "application/json"}

R.check(c.get(B + "/overview").status_code == 200, "overview readable")
R.check(c.post(B + "/hold", data="scope=coder.main", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415,
        "form-encoded (cross-site form) POST refused")
R.check(c.post(B + "/hold", json={"scope": "coder.main"}, headers={**J, "Origin": "https://evil.example"}).status_code == 403,
        "foreign Origin refused")
R.check(c.post(B + "/hold", json={"scope": "coder.main"}, headers={**J, "Origin": "http://127.0.0.1:9119"}).status_code == 200,
        "same Origin accepted")
R.check(c.post(B + "/hold", json={"scope": "../../etc"}, headers=J).status_code == 400, "bad route -> 400")
R.check(c.post(B + "/force", json={"scope": "coder.main", "rung": "x"}, headers=J).status_code == 400, "non-integer rung -> 400")

def boom(*a, **k):
    raise RuntimeError("/Users/someone/secret/path token=abc")
m.overview = boom
r = c.get(B + "/overview")
R.check(r.status_code == 500 and "secret" not in r.text and "token" not in r.text, "internal errors not echoed")

api._writes.clear()
codes = [c.post(B + "/release", json={"scope": "coder.main"}, headers=J).status_code for _ in range(25)]
R.check(429 in codes, "mutations rate limited")
api._last_run[0] = 0; api._writes.clear()
m.collect = lambda policy=None: {}
r1 = c.post(B + "/run", json={}, headers=J).status_code
r2 = c.post(B + "/run", json={}, headers=J).status_code
R.check(r1 == 200 and r2 == 429, "run-now throttled")
cleanup(tmp)

# _wrapper always emits ["hermes", "-p", bot], even for "default"
# (old code returned bare ["hermes"] for default, so runners from inside a profile shell
#  would silently edit that shell's profile instead of the intended one)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("qr_eng_w", HERE / "engine" / "quota_router.py")
_w = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_w)
R.check(_w._wrapper("default") == ["hermes", "-p", "default"],
        "_wrapper('default') is ['hermes', '-p', 'default']")
R.check(_w._wrapper("writer") == ["hermes", "-p", "writer"],
        "_wrapper('writer') is ['hermes', '-p', 'writer']")

# behaviour: apply() for a default.* scope targets the default profile explicitly
m2, tmp2 = load_engine()
_calls = []


class _FakeSubprocess:
    @staticmethod
    def run(argv, **kw):
        _calls.append(list(argv))
        return type("R", (), {"returncode": 0})()


_w.subprocess = _FakeSubprocess()
_w.HOME = m2.WS / "fake-hermes"
_w.apply({"scope": "default.main", "to": ["anthropic", "claude-sonnet-4-5"], "from": ["openai-codex", "gpt-5"]})
R.check(bool(_calls) and all(a[:3] == ["hermes", "-p", "default"] for a in _calls),
        "apply() for default.* runs `hermes -p default ...`")
cleanup(tmp2)
R.done()
