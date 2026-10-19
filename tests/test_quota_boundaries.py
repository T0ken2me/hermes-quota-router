"""Admission boundaries: unknown readings are not free capacity."""
import math
import unittest
from _harness import cleanup, load_engine, readings


class QuotaBoundaries(unittest.TestCase):
    def setUp(self):
        self.engine, tmp = load_engine()
        self.addCleanup(cleanup, tmp)
        self.policy = self.engine.load_policy()
        self.thresholds = self.engine._t(self.policy)

    def test_missing_subscription_windows_are_unknown_not_zero(self):
        for window in ('weekly', 'session'):
            with self.subTest(window=window):
                q = readings(anthropic={window: None})
                self.assertEqual(self.engine.status('anthropic', q, 'subscription', self.thresholds), 'unknown')
                self.assertFalse(self.engine.cooled('anthropic', q, 'subscription', self.thresholds))

    def test_actual_zero_is_known_capacity(self):
        q = readings(anthropic={'weekly': 0, 'session': 0})
        self.assertEqual(self.engine.status('anthropic', q, 'subscription', self.thresholds), 'room')
        self.assertTrue(self.engine.cooled('anthropic', q, 'subscription', self.thresholds))

    def test_invalid_subscription_numbers_are_unknown(self):
        for value in (True, False, -1, '5', math.nan, math.inf, -math.inf):
            for window in ('weekly', 'session'):
                with self.subTest(value=value, window=window):
                    q = readings(anthropic={window: value})
                    self.assertEqual(self.engine.status('anthropic', q, 'subscription', self.thresholds), 'unknown')
                    self.assertFalse(self.engine.cooled('anthropic', q, 'subscription', self.thresholds))

    def test_missing_destination_is_never_chosen_or_recovered(self):
        q = readings(**{'openai-codex': {'weekly': 100}}, anthropic={'weekly': None})
        changes = self.engine.decide(self.policy, q, {}, off=lambda _: set())
        self.assertFalse(any(c['to'][0] == 'anthropic' for c in changes))
        changes = self.engine.decide(self.policy, q, {'coder.main': 2}, off=lambda _: set())
        self.assertFalse(any(c['to'][0] == 'anthropic' for c in changes))

    def test_credit_reserve_has_strict_boundary(self):
        t = dict(self.thresholds, min_balance_usd=8)
        for balance, expected in ((0, 'down'), (0.01, 'down'), (7.99, 'down'), (8, 'down'), (8.01, 'room')):
            with self.subTest(balance=balance):
                self.assertEqual(self.engine.status('nous', readings(nous={'credits': balance}), 'credits', t), expected)

    def test_invalid_credit_readings_are_unknown(self):
        for balance in (None, True, False, -1, '9', math.nan, math.inf, -math.inf):
            with self.subTest(balance=balance):
                self.assertEqual(self.engine.status('nous', readings(nous={'credits': balance}), 'credits', self.thresholds), 'unknown')

    def test_credit_reserve_blocks_destination_in_real_decision(self):
        self.policy['thresholds']['min_balance_usd'] = 8
        q = readings(**{'openai-codex': {'weekly': 100}}, anthropic={'weekly': 100}, nous={'credits': 8})
        changes = self.engine.decide(self.policy, q, {}, off=lambda _: set())
        self.assertFalse(any(c['to'][0] == 'nous' for c in changes))

    def test_disabled_current_does_not_bypass_unknown_primary(self):
        q = readings(**{'openai-codex': {'weekly': None}}, anthropic={'weekly': 100},
                     nous={'credits': 0}, copilot={'ok': False}, deepseek={'balance_usd': 0})
        changes = self.engine.decide(self.policy, q, {'coder.main': 3}, off=lambda _: {'copilot'})
        self.assertFalse(any(c['scope'] == 'coder.main' for c in changes))

    def test_disabled_current_does_not_bypass_credit_reserve_at_primary(self):
        self.policy['classes']['coding'] = [['nous', 'example-model'], ['copilot', 'example-model']]
        self.policy['thresholds']['min_balance_usd'] = 8
        changes = self.engine.decide(self.policy, readings(nous={'credits': 8}),
                                     {'coder.main': 1}, off=lambda _: {'copilot'})
        self.assertFalse(any(c['scope'] == 'coder.main' for c in changes))

    def test_metered_invalid_balance_is_not_spend_permission(self):
        t = dict(self.thresholds, _allow_metered=True, min_balance_usd=0)
        for balance in (None, True, False, -1, '9', math.nan, math.inf, -math.inf):
            with self.subTest(balance=balance):
                self.assertEqual(self.engine.status('deepseek', readings(deepseek={'balance_usd': balance}), 'metered', t), 'unknown')

    def test_metered_balance_boundary_and_opt_in(self):
        t = dict(self.thresholds, _allow_metered=True, min_balance_usd=8)
        for balance, expected in ((0, 'down'), (7.99, 'down'), (8, 'room'), (8.01, 'room')):
            self.assertEqual(self.engine.status('deepseek', readings(deepseek={'balance_usd': balance}), 'metered', t), expected)
        self.assertEqual(self.engine.status('deepseek', readings(deepseek={'balance_usd': 100}), 'metered', dict(t, _allow_metered=False)), 'down')

    def test_credit_absent_threshold_defaults_but_null_is_unknown(self):
        q = readings(nous={'credits': 0.01})
        self.assertEqual(self.engine.status('nous', q, 'credits', {}), 'room')
        self.assertEqual(self.engine.status('nous', q, 'credits', {'min_balance_usd': None}), 'unknown')

    def test_zero_reserve_still_excludes_empty_credit(self):
        t = dict(self.thresholds, min_balance_usd=0)
        self.assertEqual(self.engine.status('nous', readings(nous={'credits': 0}), 'credits', t), 'down')
        self.assertEqual(self.engine.status('nous', readings(nous={'credits': 0.01}), 'credits', t), 'room')


if __name__ == '__main__':
    unittest.main()
