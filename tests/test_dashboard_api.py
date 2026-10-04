"""Isolated dashboard account API tests; no live engine or network."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_api", ROOT / "dashboard/plugin_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class FakeEngine:
    def __init__(self):
        self.records = {}
        self.path = Path(tempfile.mkdtemp()) / "account_store.json"
        self.never = set()

    def account_store_path(self):
        return self.path

    def is_never_touch(self, ref):
        return ref in self.never

    def list_accounts(self):
        return list(self.records.values())

    def account_usage(self, ref):
        return {"windows": [], "credits": None, "fetched_at": None, "freshness": "unknown"}

    def switching_history(self, *args):
        return []

    def validate_account(self, ref):
        return {"status": "unsupported", "reason": "native adapter pending", "plan_label": None}

    def dryrun_priorities(self, profile, task_class):
        return {"would_select": None, "reason": "no eligible account", "eligible": [], "ineligible": []}

    def add_account(self, record):
        self.records[record["account_ref"]] = record
        return record

    def set_account_enabled(self, ref, enabled):
        self.records[ref]["enabled"] = enabled
        return self.records[ref]

    def set_account_priority(self, ref, priority):
        self.records[ref]["priority"] = priority
        return self.records[ref]


class DashboardAccountTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        api.engine = lambda: self.engine
        api._writes.clear()
        app = FastAPI()
        app.include_router(api.router, prefix="/api/plugins/quota-router")
        self.client = TestClient(app)
        self.base = "/api/plugins/quota-router"

    def body(self, **overrides):
        result = dict(label="Test", provider="openai", kind="subscription", account_ref="acct1", pool_ref="pool1", credential_ref="handle1", priority=2, enabled=True)
        result.update(overrides)
        return result

    def post(self, path, body):
        return self.client.post(self.base + path, json=body)

    def test_add_valid(self):
        r = self.post("/accounts", self.body())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_priority_rejects_float(self):
        self.post("/accounts", self.body())
        self.assertEqual(self.post("/accounts/acct1/priority", {"priority": 1.5}).status_code, 400)

    def test_reject_mixed_case_bearer(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="BEARER-secret")).status_code, 400)

    def test_reject_mixed_case_abacus(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="abacusApiKey-x")).status_code, 400)

    def test_accounts_list_no_credential_ref(self):
        self.post("/accounts", self.body())
        data = self.client.get(self.base + "/accounts").json()
        for acct in data["accounts"]:
            self.assertNotIn("credential_ref", acct)

    def test_duplicate(self):
        self.post("/accounts", self.body())
        self.assertEqual(self.post("/accounts", self.body()).status_code, 400)

    def test_unknown_provider(self):
        self.assertEqual(self.post("/accounts", self.body(provider="evil")).status_code, 400)

    def test_bad_ref(self):
        self.assertEqual(self.post("/accounts", self.body(account_ref="../x")).status_code, 400)

    def test_long_label(self):
        self.assertEqual(self.post("/accounts", self.body(label="x" * 121)).status_code, 400)

    def test_reject_bearer_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="Bearer secret")).status_code, 400)

    def test_reject_sk_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="sk-secret")).status_code, 400)

    def test_reject_jwt_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="eyJtoken")).status_code, 400)

    def test_reject_ghp_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="ghp_secret")).status_code, 400)

    def test_reject_xai_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="xai-secret")).status_code, 400)

    def test_reject_abacus_secret(self):
        self.assertEqual(self.post("/accounts", self.body(credential_ref="AbacusApiKey_secret")).status_code, 400)

    def test_unsupported_validation(self):
        self.post("/accounts", self.body())
        self.assertEqual(self.post("/accounts/acct1/validate", {}).json()["status"], "unsupported")

    def test_enable_disable(self):
        self.post("/accounts", self.body())
        r = self.post("/accounts/acct1/enable", {"enabled": False})
        self.assertEqual(r.json()["enabled"], False)

    def test_priority_accepts_bounds(self):
        self.post("/accounts", self.body())
        for value in (0, 99):
            self.assertEqual(self.post("/accounts/acct1/priority", {"priority": value}).status_code, 200)

    def test_priority_rejects_negative(self):
        self.post("/accounts", self.body())
        self.assertEqual(self.post("/accounts/acct1/priority", {"priority": -1}).status_code, 400)

    def test_priority_rejects_overflow(self):
        self.post("/accounts", self.body())
        self.assertEqual(self.post("/accounts/acct1/priority", {"priority": 100}).status_code, 400)

    def test_accounts_empty(self):
        self.assertEqual(self.client.get(self.base + "/accounts").json(), {"accounts": []})

    def test_usage_unknown(self):
        self.assertEqual(self.client.get(self.base + "/accounts/acct1/usage").json()["freshness"], "unknown")

    def test_history_empty(self):
        self.assertEqual(self.client.get(self.base + "/history").json(), {"events": []})

    def test_dryrun_deferred(self):
        r = self.post("/priorities/dryrun", {"profile": "default", "task_class": "coding"})
        self.assertIsNone(r.json()["would_select"])

    def test_store_roundtrip_strips_handle(self):
        from engine.account_store import AccountStore
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "account_store.json")
            store = AccountStore(path)
            rec = {k: v for k, v in self.body().items()}
            # strip credential_ref like the API does before persisting
            disk_rec = {k: v for k, v in rec.items() if k != "credential_ref"}
            store.add_account(disk_rec)
            with open(path) as fh:
                disk = fh.read()
            self.assertNotIn("handle1", disk)
            self.assertNotIn("credential_ref", disk)
            reloaded = AccountStore(path).list_accounts()
            self.assertNotIn("credential_ref", str(reloaded))

    def test_cross_origin_mutation_blocked(self):
        r = self.client.post(self.base + "/accounts", json=self.body(), headers={"Origin": "https://evil.invalid"})
        self.assertEqual(r.status_code, 403)

    def test_rate_limiter(self):
        api._writes[:] = [api.time.monotonic()] * api._MAX_WRITES
        with self.assertRaises(Exception):
            api._rate_limit()


if __name__ == "__main__":
    unittest.main()
