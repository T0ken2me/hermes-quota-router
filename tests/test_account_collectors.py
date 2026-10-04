import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone

from engine.accounts import Account, choose_account
from engine.account_collectors import Binding, UsageReceipt, collect_usage

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def raw_codex(used=0):
    return {
        "plan_type": "pro",
        "primary_window": {
            "duration": 604800,
            "used_percent": used,
            "reset_at": 1791504000,
        },
        "secondary_window": None,
        "allowed": True,
        "limit_reached": False,
    }


def setup_pair(personal=0):
    a = Account("personal", "pool-p", "openai-codex", "cred-p", 0)
    b = Account("business", "pool-b", "openai-codex", "cred-b", 1)
    bindings = [Binding(a, "pool-p", "cred-p"), Binding(b, "pool-b", "cred-b")]
    calls = []

    def resolve(account_ref, pool_ref, provider, credential_ref):
        calls.append((account_ref, pool_ref, provider, credential_ref))
        return next(
            (
                x
                for x in bindings
                if (
                    x.account.account_ref,
                    x.pool_ref,
                    x.account.provider,
                    x.credential_ref,
                )
                == (account_ref, pool_ref, provider, credential_ref)
            ),
            None,
        )

    def fetch(binding):
        raw = raw_codex(personal if binding.account.account_ref == "personal" else 21)
        return UsageReceipt(
            binding.account.account_ref,
            binding.pool_ref,
            binding.account.provider,
            binding.credential_ref,
            NOW,
            raw,
        )

    obs = [
        collect_usage(a, resolve, fetch, now=lambda: NOW),
        collect_usage(b, resolve, fetch, now=lambda: NOW),
    ]
    return a, b, obs, calls


class CollectorTests(unittest.TestCase):
    def test_same_provider_separately_and_select_personal(self):
        a, b, obs, calls = setup_pair()
        observations = [x.observation for x in obs]
        d = choose_account([a, b], observations, now=NOW)
        self.assertEqual(d.account_ref, "personal")
        self.assertEqual(d.credential_ref, "cred-p")
        self.assertEqual(
            calls,
            [
                ("personal", "pool-p", "openai-codex", "cred-p"),
                ("business", "pool-b", "openai-codex", "cred-b"),
            ],
        )

    def test_hot_personal_eligible_business(self):
        a, b, obs, _ = setup_pair(90)
        self.assertEqual(
            choose_account([a, b], [x.observation for x in obs], now=NOW).account_ref,
            "business",
        )

    def test_wrong_account_ref_refused_before_fetch(self):
        a, _, _, _ = setup_pair()
        fetched = []
        r = collect_usage(
            a, lambda *args: None, lambda b: fetched.append(b), now=lambda: NOW
        )
        self.assertEqual(r.code, "binding_mismatch")
        self.assertFalse(fetched)

    def test_fetch_exception_safe(self):
        a, _, _, _ = setup_pair()
        binding = Binding(a, "pool-p", "cred-p")
        r = collect_usage(
            a,
            lambda *args: binding,
            lambda b: (_ for _ in ()).throw(RuntimeError("secret")),
            now=lambda: NOW,
        )
        self.assertEqual(r.code, "fetch_failed")
        self.assertNotIn("secret", repr(r))

    def test_receipt_binding_mismatch(self):
        a, _, _, _ = setup_pair()
        b = Binding(a, "pool-p", "cred-p")
        receipt = UsageReceipt(
            "other", "pool-p", a.provider, "cred-p", NOW, raw_codex()
        )
        self.assertEqual(
            collect_usage(a, lambda *args: b, lambda _: receipt, now=lambda: NOW).code,
            "receipt_mismatch",
        )

    def test_codex_weekly_only(self):
        a, _, _, _ = setup_pair()
        binding = Binding(a, "pool-p", "cred-p")
        o = collect_usage(
            a,
            lambda *args: binding,
            lambda _: UsageReceipt(
                "personal", "pool-p", a.provider, "cred-p", NOW, raw_codex()
            ),
            now=lambda: NOW,
        )
        self.assertEqual(o.observation.quota_shape, "weekly_only")

    def test_codex_standard_order_independent(self):
        a, _, _, _ = setup_pair()
        raw = {
            "plan_type": "pro",
            "secondary_window": {
                "duration": 604800,
                "used_percent": 0,
                "reset_at": 1791504000,
            },
            "primary_window": {
                "duration": 18000,
                "used_percent": 1,
                "reset_at": 1791504000,
            },
            "allowed": True,
            "limit_reached": False,
        }
        o = collect_usage(
            a,
            lambda *args: Binding(a, "pool-p", "cred-p"),
            lambda _: UsageReceipt(
                "personal", "pool-p", a.provider, "cred-p", NOW, raw
            ),
            now=lambda: NOW,
        )
        self.assertEqual(o.observation.quota_shape, "session_and_weekly")

    def test_codex_reject_bad_shapes(self):
        cases = [
            {},
            {**raw_codex(), "allowed": None},
            {**raw_codex(), "limit_reached": None},
            {**raw_codex(), "extra_limits": {"x": 1}},
            {
                **raw_codex(),
                "primary_window": {**raw_codex()["primary_window"], "duration": 123},
            },
            {
                **raw_codex(),
                "secondary_window": {
                    "duration": 604800,
                    "used_percent": 0,
                    "reset_at": 1791504000,
                },
            },
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(self.codex(raw).status, "refused")

    def codex(self, raw):
        a = Account("a", "p", "openai-codex", "c")
        b = Binding(a, "p", "c")
        return collect_usage(
            a,
            lambda *args: b,
            lambda _: UsageReceipt("a", "p", a.provider, "c", NOW, raw),
            now=lambda: NOW,
        )

    def test_codex_bad_usage_types(self):
        for value in (True, "0", float("nan"), float("inf"), -1, 101):
            raw = raw_codex()
            raw["primary_window"]["used_percent"] = value
            self.assertEqual(self.codex(raw).status, "refused")

    def test_codex_future_timestamp(self):
        raw = raw_codex()
        raw["primary_window"]["reset_at"] = 1791504000.0
        self.assertEqual(self.codex(raw).status, "refused")

    def test_anthropic_normalization(self):
        a = Account("a", "p", "anthropic", "c")
        b = Binding(a, "p", "c")
        raw = {
            "five_hour": {"utilization": 10, "resets_at": "2026-10-05T02:00:00+02:00"},
            "seven_day": {"utilization": 20, "resets_at": "2026-10-11T00:00:00Z"},
            "extra_usage": {"is_enabled": False},
        }
        r = collect_usage(
            a,
            lambda *args: b,
            lambda _: UsageReceipt("a", "p", "anthropic", "c", NOW, raw),
            now=lambda: NOW,
        )
        self.assertEqual(
            r.observation.windows[0].reset_at.utcoffset().total_seconds(), 0
        )

    def test_anthropic_unknown_limiting_data_refused(self):
        a = Account("a", "p", "anthropic", "c")
        b = Binding(a, "p", "c")
        for extra in (
            {"model_limits": {"x": 1}},
            {"extra_usage": {"is_enabled": True}},
        ):
            raw = {
                "five_hour": {"utilization": 10, "resets_at": "2026-10-05T00:00:00Z"},
                "seven_day": {"utilization": 20, "resets_at": "2026-10-11T00:00:00Z"},
                **extra,
            }
            self.assertEqual(
                collect_usage(
                    a,
                    lambda *args: b,
                    lambda _: UsageReceipt("a", "p", "anthropic", "c", NOW, raw),
                    now=lambda: NOW,
                ).status,
                "refused",
            )

    def test_nous_numeric_only(self):
        a = Account("a", "p", "nous", "c", kind="credits")
        b = Binding(a, "p", "c")
        r = collect_usage(
            a,
            lambda *args: b,
            lambda _: UsageReceipt(
                "a",
                "p",
                "nous",
                "c",
                NOW,
                {
                    "subscription_remaining": 7,
                    "subscription_allowance": 10,
                    "purchased_credits": 2,
                },
            ),
            now=lambda: NOW,
        )
        self.assertEqual(r.observation.credit_remaining, 7)

    def test_nous_formatted_or_inconsistent_refused(self):
        for raw in (
            {"subscription_remaining": "7", "subscription_allowance": 10},
            {
                "subscription_remaining": 12,
                "subscription_allowance": 10,
                "purchased_credits": 2,
            },
            {"total_usable": 10},
        ):
            a = Account("a", "p", "nous", "c", kind="credits")
            b = Binding(a, "p", "c")
            self.assertEqual(
                collect_usage(
                    a,
                    lambda *args: b,
                    lambda _: UsageReceipt("a", "p", "nous", "c", NOW, raw),
                    now=lambda: NOW,
                ).status,
                "refused",
            )

    def test_bad_clock_and_binding(self):
        a, _, _, _ = setup_pair()
        b = Binding(a, "pool-p", "cred-p")
        for clock in (lambda: "bad", lambda: datetime(2026, 10, 4)):
            self.assertEqual(
                collect_usage(a, lambda *args: b, lambda _: None, now=clock).status,
                "refused",
            )

    def test_empty_fetch_not_observation(self):
        a, _, _, _ = setup_pair()
        b = Binding(a, "pool-p", "cred-p")
        self.assertEqual(
            collect_usage(a, lambda *args: b, lambda _: None, now=lambda: NOW).status,
            "refused",
        )

    def test_receipt_time_preserved(self):
        a, _, _, _ = setup_pair()
        b = Binding(a, "pool-p", "cred-p")
        earlier = datetime(2026, 10, 3, tzinfo=timezone.utc)
        r = collect_usage(
            a,
            lambda *args: b,
            lambda _: UsageReceipt(
                "personal", "pool-p", a.provider, "cred-p", earlier, raw_codex()
            ),
            now=lambda: NOW,
        )
        self.assertEqual(r.observation.fetched_at, earlier)

    def test_result_never_contains_raw_error(self):
        a, _, _, _ = setup_pair()
        b = Binding(a, "pool-p", "cred-p")
        r = collect_usage(
            a,
            lambda *args: b,
            lambda _: (_ for _ in ()).throw(ValueError("bearer secret")),
            now=lambda: NOW,
        )
        self.assertNotIn("bearer", repr(r))

    def test_no_second_account_fallback(self):
        a, b, _, _ = setup_pair()
        binding = Binding(b, "pool-b", "cred-b")
        calls = []
        r = collect_usage(
            a,
            lambda *args: (calls.append(args) or binding),
            lambda _: self.fail("must not fetch"),
            now=lambda: NOW,
        )
        self.assertEqual(r.status, "refused")
        self.assertEqual(len(calls), 1)

    def test_anthropic_missing_window_refused(self):
        a = Account("a", "p", "anthropic", "c")
        b = Binding(a, "p", "c")
        raw = {"five_hour": {"utilization": 1, "resets_at": "2026-10-05T00:00:00Z"}}
        self.assertEqual(
            collect_usage(
                a,
                lambda *args: b,
                lambda _: UsageReceipt("a", "p", "anthropic", "c", NOW, raw),
                now=lambda: NOW,
            ).status,
            "refused",
        )

    def test_anthropic_boolean_utilization_refused(self):
        a = Account("a", "p", "anthropic", "c")
        b = Binding(a, "p", "c")
        raw = {
            "five_hour": {"utilization": True, "resets_at": "2026-10-05T00:00:00Z"},
            "seven_day": {"utilization": 1, "resets_at": "2026-10-11T00:00:00Z"},
        }
        self.assertEqual(
            collect_usage(
                a,
                lambda *args: b,
                lambda _: UsageReceipt("a", "p", "anthropic", "c", NOW, raw),
                now=lambda: NOW,
            ).status,
            "refused",
        )

    def test_anthropic_future_and_bad_time_refused(self):
        a = Account("a", "p", "anthropic", "c")
        b = Binding(a, "p", "c")
        for stamp in ("2026-10-03T00:00:00Z", "nonsense"):
            raw = {
                "five_hour": {"utilization": 1, "resets_at": stamp},
                "seven_day": {"utilization": 1, "resets_at": "2026-10-11T00:00:00Z"},
            }
            self.assertEqual(
                collect_usage(
                    a,
                    lambda *args: b,
                    lambda _: UsageReceipt("a", "p", "anthropic", "c", NOW, raw),
                    now=lambda: NOW,
                ).status,
                "refused",
            )

    def test_receipt_credential_mismatch(self):
        a = Account("a", "p", "openai-codex", "c")
        b = Binding(a, "p", "c")
        receipt = UsageReceipt("a", "p", a.provider, "different", NOW, raw_codex())
        self.assertEqual(
            collect_usage(a, lambda *args: b, lambda _: receipt, now=lambda: NOW).code,
            "receipt_mismatch",
        )

    def test_receipt_non_utc_time_refused(self):
        a = Account("a", "p", "openai-codex", "c")
        b = Binding(a, "p", "c")
        receipt = UsageReceipt(
            "a", "p", a.provider, "c", datetime(2026, 10, 4), raw_codex()
        )
        self.assertEqual(
            collect_usage(a, lambda *args: b, lambda _: receipt, now=lambda: NOW).code,
            "invalid_receipt_time",
        )

    def test_nous_boolean_values_refused(self):
        a = Account("a", "p", "nous", "c", kind="credits")
        b = Binding(a, "p", "c")
        raw = {"subscription_remaining": True, "subscription_allowance": 10}
        self.assertEqual(
            collect_usage(
                a,
                lambda *args: b,
                lambda _: UsageReceipt("a", "p", "nous", "c", NOW, raw),
                now=lambda: NOW,
            ).status,
            "refused",
        )

    def test_no_fetch_on_invalid_clock(self):
        a = Account("a", "p", "openai-codex", "c")
        fetched = []
        result = collect_usage(
            a,
            lambda *args: Binding(a, "p", "c"),
            lambda b: fetched.append(b),
            now=lambda: "bad",
        )
        self.assertEqual(result.code, "invalid_clock")
        self.assertEqual(fetched, [])

    def test_unrecognized_codex_plan_refused(self):
        raw = raw_codex()
        raw["plan_type"] = "mystery"
        self.assertEqual(self.codex(raw).status, "refused")

    def test_codex_unknown_key_refused(self):
        raw = raw_codex()
        raw["remaining"] = "looks harmless"
        self.assertEqual(self.codex(raw).status, "refused")

    def test_receipt_missing_refused(self):
        a = Account("a", "p", "openai-codex", "c")
        b = Binding(a, "p", "c")
        result = collect_usage(a, lambda *args: b, lambda _: None, now=lambda: NOW)
        self.assertIsNone(result.observation)
        self.assertEqual(result.code, "receipt_missing")

    def test_codex_false_availability_refused(self):
        for key, value in (("allowed", False), ("limit_reached", True)):
            raw = raw_codex()
            raw[key] = value
            self.assertIn(
                self.codex(raw).code, {"unavailable", "malformed_observation"}
            )

    def test_resolver_exception_safe(self):
        a = Account("a", "p", "openai-codex", "c")
        result = collect_usage(
            a,
            lambda *args: (_ for _ in ()).throw(RuntimeError("secret")),
            lambda _: None,
            now=lambda: NOW,
        )
        self.assertEqual(result.code, "binding_unavailable")
        self.assertNotIn("secret", repr(result))

    def test_fetch_data_credential_is_not_used_for_other_account(self):
        a, b, obs, calls = setup_pair()
        self.assertNotEqual(
            obs[0].observation.credential_ref, obs[1].observation.credential_ref
        )
        self.assertEqual([x[3] for x in calls], ["cred-p", "cred-b"])

    def test_refusal_has_no_observation(self):
        result = self.codex({})
        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.observation)


if __name__ == "__main__":
    unittest.main()
