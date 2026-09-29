# Changelog

## Unreleased

### Added
- **Readable grouped Telegram alerts.** `format_alert()` is a new pure function
  (unit-testable, no side effects) that replaces the raw per-scope bullet list.
  Groups changes by `(from_provider, trigger, destination)` and emits one
  sentence per group. Routes ≤ 3 are listed by name; larger groups show counts
  split by type (bots / background tasks / scheduled jobs).
- **Icon discipline.** Headlines now carry `⚠️` (routes moved away), `✅`
  (all returned to first choice), `🛑` (apply failure or drift — listed
  individually with a corrective hint), or `🧪` (dry-run).
- **Budget line with reset times.** `_format_budget_line()` renders human-facing
  provider names (Claude, ChatGPT, Nous, …), ⚠️ at/above 70 %, and "resets
  HH:MM" when a 5h window or weekly window is at 100 %. Disabled/unknown
  providers are omitted.
- **Reset time plumbing.** `_subscription()` now returns `session_reset` and
  `weekly_reset` ISO strings from the `reset_at` field of account-usage windows,
  backward-compatible with existing `quota.jsonl` consumers.
- **Away time on return alerts.** `_away_minutes()` scans `decisions.jsonl` to
  compute how long routes were away from rung 0; shown as "(away N min)" on
  return events.
- **27 formatter unit tests** in `tests/test_alert.py` covering grouping,
  ≤3 individual listing, icon precedence, dry-run wording, return + away time,
  budget line variants (resets, ⚠️, omissions).

### Changed
- `run()` now calls `format_alert()` and returns a single multi-line message
  instead of a list of per-scope bullets. Per-scope detail is unchanged in
  `decisions.jsonl` and the `/quota` command output.
- `run_all.sh` includes `test_alert` in the suite.

## 0.2.2
- Readable Telegram alerts: one line per event (grouped counts, provider names, reset times), failures always listed individually; full detail stays in the decision log and /quota.
- Read-only Spend panel in the dashboard tab: tokens and estimated cost per model, profile and day (adapted from hermes-hud, MIT); never_touch profiles are never read; no session titles or content.
- Optional `blocked_providers` deny-list (empty by default): a policy whose ladders use a blocked provider or aggregator vendor is refused.

## 0.2.1 — 2026-09-28

### Fixed
- **Never route a bot to a provider disabled in its own config.** A provider with
  `providers.<p>.enabled: false` in the bot's `config.yaml` is no longer a destination, and a
  bot left on one is moved back to its first rung. Before, when every other rung was busy, the
  router could move bots to such a provider and they then failed to start
  ("provider is disabled in config").
- **Always target the profile explicitly.** The router now runs `hermes -p <profile> …` for
  every profile, including `default`. Before, changes for `default` used a bare `hermes`, which
  follows the caller's `HERMES_HOME` — run from inside another profile's shell, they edited
  that profile instead.

## 0.2.0 — 2026-09-28

Initial release: tiered quota router (dry-run by default), `/quota` command, dashboard tab,
desktop page, installer, tailnet remote-access and dashboard-password scripts, tests.
