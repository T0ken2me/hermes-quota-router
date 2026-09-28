# Changelog

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
