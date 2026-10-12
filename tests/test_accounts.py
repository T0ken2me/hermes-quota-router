"""Synthetic account-aware selector regressions; no live auth or providers."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class AccountSelectorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def sub(
        self,
        ref="a",
        pool="p",
        cred="c",
        priority=0,
        provider="openai-codex",
        kind="subscription",
    ):
        from engine.accounts import Account

        return Account(ref, pool, provider, cred, priority, kind)

    def obs(
        self,
        a,
        usage=0,
        *,
        fetched=None,
        reset=None,
        shape="weekly_only",
        names=None,
        **kw,
    ):
        from engine.accounts import AccountObservation, QuotaWindow

        names = (
            names
            if names is not None
            else (["weekly"] if shape == "weekly_only" else ["session", "weekly"])
        )
        windows = tuple(
            QuotaWindow(n, usage, reset or self.now + timedelta(days=1)) for n in names
        )
        return AccountObservation(
            a.account_ref,
            a.pool_ref,
            a.credential_ref,
            fetched or self.now,
            windows,
            shape,
            True,
            **kw,
        )

    def test_business_used_if_personal_hot_and_business_cool(self):
        a, b = self.sub("personal", "pp", "cp"), self.sub("business", "pb", "cb", 1)
        d = self.choose((a, b), (self.obs(a, 80), self.obs(b, 20)))
        self.assertEqual((d.account_ref, d.credential_ref), ("business", "cb"))

    def choose(self, accounts, observations, **kwargs):
        from engine.accounts import choose_account

        return choose_account(accounts, observations, now=self.now, **kwargs)

    def test_provider_order_does_not_override_priority(self):
        a, b = (
            self.sub("a", "p1", "c1", 0, "anthropic"),
            self.sub("b", "p2", "c2", 1, "openai-codex"),
        )
        self.assertEqual(
            self.choose((a, b), (self.obs(a), self.obs(b))).account_ref, "a"
        )

    def test_threshold_is_inclusive_and_above_fails(self):
        a, b = self.sub("a", "p1", "c1"), self.sub("b", "p2", "c2", 1)
        d = self.choose((a, b), (self.obs(a, 50), self.obs(b, 50.01)))
        self.assertEqual(d.account_ref, "a")

    def test_session_window_also_limits(self):
        from engine.accounts import AccountObservation, QuotaWindow

        a = self.sub()
        o = AccountObservation(
            "a",
            "p",
            "c",
            self.now,
            (
                QuotaWindow("session", 80, self.now + timedelta(days=1)),
                QuotaWindow("weekly", 0, self.now + timedelta(days=1)),
            ),
            "session_and_weekly",
            True,
        )
        self.assertEqual(self.choose((a,), (o,)).status, "defer")

    def test_weekly_only_is_supported(self):
        a = self.sub()
        self.assertEqual(self.choose((a,), (self.obs(a),)).status, "select")

    def test_stale_future_and_elapsed_reset_defer(self):
        from engine.accounts import AccountObservation, QuotaWindow

        a = self.sub()
        for fetched, reset in (
            (self.now - timedelta(seconds=301), self.now + timedelta(days=1)),
            (self.now + timedelta(seconds=1), self.now + timedelta(days=1)),
            (self.now, self.now),
        ):
            o = AccountObservation(
                "a",
                "p",
                "c",
                fetched,
                (QuotaWindow("weekly", 0, reset),),
                "weekly_only",
                True,
            )
            self.assertEqual(self.choose((a,), (o,)).status, "defer")

    def test_identity_and_missing_observation_fail_closed(self):
        a = self.sub()
        from engine.accounts import AccountObservation

        wrong = AccountObservation("a", "p", "other", self.now, (), None, True)
        self.assertEqual(self.choose((a,), (wrong,)).status, "defer")
        self.assertEqual(self.choose((a,), ()).status, "defer")

    def test_duplicate_candidates_rejected(self):
        a = self.sub()
        with self.assertRaises(ValueError):
            self.choose((a, a), (self.obs(a),))

    def test_duplicate_windows_rejected(self):
        from engine.accounts import AccountObservation, QuotaWindow

        w = QuotaWindow("weekly", 0, self.now + timedelta(days=1))
        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, (w, w), "weekly_only", True)

    def test_empty_shape_windows_rejected_at_construction(self):
        from engine.accounts import AccountObservation

        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, (), "weekly_only", True)

    def test_shared_pool_contradiction_deferred(self):
        a, b = self.sub("a", "p", "c1"), self.sub("b", "p", "c2")
        self.assertEqual(
            self.choose((a, b), (self.obs(a, 0), self.obs(b, 10))).status, "defer"
        )

    def test_shared_pool_alias_deduplicated(self):
        a, b = self.sub("a", "p", "c1"), self.sub("b", "p", "c2", 1)
        d = self.choose((a, b), (self.obs(a), self.obs(b)))
        self.assertEqual(d.account_ref, "a")

    def test_unknown_current_does_not_grant_route(self):
        a = self.sub()
        self.assertEqual(
            self.choose(
                (a,), (self.obs(a),), current_account_ref="unknown"
            ).account_ref,
            "a",
        )

    def test_current_route_sticks_then_hot_route_fails_over(self):
        a, b = self.sub("a", "p1", "c1"), self.sub("b", "p2", "c2", 1)
        self.assertEqual(
            self.choose(
                (a, b), (self.obs(a), self.obs(b)), current_account_ref="b"
            ).account_ref,
            "b",
        )
        self.assertEqual(
            self.choose(
                (a, b), (self.obs(a, 80), self.obs(b)), current_account_ref="a"
            ).account_ref,
            "b",
        )

    def test_credit_requires_authorization_and_remaining_budget(self):
        a = self.sub("r", "p", "c", kind="credits")
        o = self.obs(a, shape=None, names=[], credit_remaining=36, credit_allowance=110)
        self.assertEqual(self.choose((a,), (o,)).status, "defer")
        self.assertEqual(
            self.choose(
                (a,),
                (o,),
                emergency_credit_authorized=True,
                emergency_credit_budget=20,
                emergency_credit_spent=20,
            ).status,
            "defer",
        )

    def test_authorised_emergency_credits_still_respect_minimum_reserve(self):
        a = self.sub("reserve", "p", "c", provider="nous", kind="credits")
        o = self.obs(a, shape=None, names=[], credit_remaining=36, credit_allowance=110)
        self.assertEqual(
            self.choose(
                (a,),
                (o,),
                emergency_credit_authorized=True,
                emergency_credit_budget=2,
            ).status,
            "defer",
        )

    def test_emergency_credits_exactly_at_reserve_can_be_selected(self):
        a = self.sub("reserve", "p", "c", provider="nous", kind="credits")
        o = self.obs(a, shape=None, names=[], credit_remaining=55, credit_allowance=110)
        self.assertEqual(
            self.choose(
                (a,),
                (o,),
                emergency_credit_authorized=True,
                emergency_credit_budget=2,
            ).account_ref,
            "reserve",
        )

    def test_credits_after_subscription(self):
        a, b = (
            self.sub("credits", "p1", "c1", 0, kind="credits"),
            self.sub("sub", "p2", "c2", 9),
        )
        oc = self.obs(
            a, shape=None, names=[], credit_remaining=36, credit_allowance=110
        )
        self.assertEqual(
            self.choose(
                (a, b),
                (oc, self.obs(b)),
                emergency_credit_authorized=True,
                emergency_credit_budget=20,
            ).account_ref,
            "sub",
        )

    def test_decision_cannot_select_without_binding(self):
        from engine.accounts import Decision

        with self.assertRaises(ValueError):
            Decision("select", None, None, None, None, "bad")

    def test_unexpected_object_rejected(self):
        with self.assertRaises(ValueError):
            self.choose((object(),), ())

    def test_bad_usage_types_nonfinite_and_range(self):
        from engine.accounts import QuotaWindow

        for value in (True, float("nan"), float("inf"), 101, -1):
            with self.assertRaises(ValueError):
                QuotaWindow("weekly", value, self.now + timedelta(days=1))

    def test_bad_references_and_bool_priority_rejected(self):
        from engine.accounts import Account

        for ref in ("", "unsafe value", "x" * 129):
            with self.assertRaises(ValueError):
                Account(ref, "p", "openai-codex", "c")
        with self.assertRaises(ValueError):
            Account("a", "p", "openai-codex", "c", True)

    def test_non_utc_or_naive_timestamps_rejected(self):
        from engine.accounts import QuotaWindow

        for dt in (
            datetime(2030, 1, 1),
            datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        ):
            with self.assertRaises(ValueError):
                QuotaWindow("weekly", 0, dt)

    def test_credit_remaining_cannot_exceed_allowance(self):
        a = self.sub("r", "p", "c", kind="credits")
        o = self.obs(
            a, shape=None, names=[], credit_remaining=111, credit_allowance=110
        )
        self.assertEqual(
            self.choose(
                (a,), (o,), emergency_credit_authorized=True, emergency_credit_budget=10
            ).status,
            "defer",
        )

    def test_unavailable_account_defers(self):
        a = self.sub()
        o = self.obs(a)
        from dataclasses import replace

        self.assertEqual(
            self.choose((a,), (replace(o, available=False),)).status, "defer"
        )

    def test_extra_window_fails_at_construction(self):
        from engine.accounts import AccountObservation, QuotaWindow

        windows = (
            QuotaWindow("weekly", 0, self.now + timedelta(days=1)),
            QuotaWindow("session", 0, self.now + timedelta(days=1)),
        )
        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, windows, "weekly_only", True)

    def test_unsupported_window_name_refused(self):
        from engine.accounts import QuotaWindow

        with self.assertRaises(ValueError):
            QuotaWindow("daily", 0, self.now + timedelta(days=1))

    def test_observation_bool_and_type_fields_rejected(self):
        from engine.accounts import AccountObservation

        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, (), None, 1)
        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, [], None, True)

    def test_credit_budget_and_amount_types_fail_closed(self):
        a = self.sub("r", "p", "c", kind="credits")
        for remaining, allowance in (
            (True, 10),
            (float("nan"), 10),
            (1, float("inf")),
            (-1, 10),
        ):
            with self.assertRaises(ValueError):
                self.obs(
                    a,
                    shape=None,
                    names=[],
                    credit_remaining=remaining,
                    credit_allowance=allowance,
                )
        with self.assertRaises(ValueError):
            self.choose((a,), (), emergency_credit_budget=True)

    def test_credit_authorization_with_valid_budget_selects(self):
        a = self.sub("r", "p", "c", kind="credits")
        # Positive control must meet the approved reserve as well as the budget.
        o = self.obs(a, shape=None, names=[], credit_remaining=55, credit_allowance=110)
        d = self.choose(
            (a,), (o,), emergency_credit_authorized=True, emergency_credit_budget=20
        )
        self.assertEqual((d.status, d.account_ref), ("select", "r"))

    def test_duplicate_observations_rejected(self):
        a = self.sub()
        o = self.obs(a)
        with self.assertRaises(ValueError):
            self.choose((a,), (o, o))

    def test_reset_in_future_and_observation_exact_freshness_boundary(self):
        a = self.sub()
        o = self.obs(a, fetched=self.now - timedelta(seconds=300))
        self.assertEqual(self.choose((a,), (o,)).status, "select")

    def test_subscription_fallback_ignores_credit_priority(self):
        c = self.sub("credit", "pc", "cc", -50, kind="credits")
        s = self.sub("subscription", "ps", "cs", 999)
        oc = self.obs(c, shape=None, names=[], credit_remaining=10, credit_allowance=10)
        self.assertEqual(
            self.choose(
                (c, s),
                (oc, self.obs(s)),
                emergency_credit_authorized=True,
                emergency_credit_budget=2,
            ).account_ref,
            "subscription",
        )

    def test_unknown_shape_and_subscription_only_without_shape_defer(self):
        from engine.accounts import AccountObservation

        a = self.sub()
        o = AccountObservation("a", "p", "c", self.now, (), None, True)
        self.assertEqual(self.choose((a,), (o,)).status, "defer")
        with self.assertRaises(ValueError):
            AccountObservation("a", "p", "c", self.now, (), "future_shape", True)

    def test_defer_decision_cannot_include_binding(self):
        from engine.accounts import Decision

        with self.assertRaises(ValueError):
            Decision("defer", "a", None, None, None, "reason")

    def test_unused_personal_account_selected_when_same_provider_business_is_hot(self):
        from engine.accounts import (
            Account,
            AccountObservation,
            QuotaWindow,
            choose_account,
        )

        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        personal = Account(
            "personal", "pool-personal", "openai-codex", "oauth-personal", priority=0
        )
        business = Account(
            "business", "pool-business", "openai-codex", "oauth-business", priority=1
        )
        observations = (
            AccountObservation(
                "personal",
                "pool-personal",
                "oauth-personal",
                now,
                windows=(QuotaWindow("weekly", 0, now + timedelta(days=7)),),
                quota_shape="weekly_only",
                subscription_only=True,
            ),
            AccountObservation(
                "business",
                "pool-business",
                "oauth-business",
                now,
                windows=(QuotaWindow("weekly", 79, now + timedelta(days=7)),),
                quota_shape="weekly_only",
                subscription_only=True,
            ),
        )
        decision = choose_account((personal, business), observations, now=now)
        self.assertEqual(decision.status, "select")
        self.assertEqual(decision.account_ref, "personal")
        self.assertEqual(decision.credential_ref, "oauth-personal")

    def test_positive_nous_balance_does_not_authorise_automatic_credit_spending(self):
        from engine.accounts import Account, AccountObservation, choose_account

        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        nous = Account(
            "reserve", "pool-reserve", "nous", "oauth-reserve", kind="credits"
        )
        observation = AccountObservation(
            "reserve",
            "pool-reserve",
            "oauth-reserve",
            now,
            credit_remaining=36,
            credit_allowance=110,
            subscription_only=True,
        )
        decision = choose_account((nous,), (observation,), now=now)
        self.assertEqual(decision.status, "defer")
        self.assertIsNone(decision.account_ref)


if __name__ == "__main__":
    unittest.main()
