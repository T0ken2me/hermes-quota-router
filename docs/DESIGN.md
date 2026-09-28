# Quota Router — design

## Problem
Each Hermes bot is pinned to one subscription. Load piles up on whichever plan a bot happens to
use, and Hermes' built-in fallback only kicks in at 100 % / HTTP 429 — mid-task, cache lost.

## What the router does
Every 30 minutes a zero-token cron job (`--no-agent`):

1. **Collects** readings: Anthropic and ChatGPT/Codex subscription windows (weekly + session %),
   Nous credit, GitHub Copilot plan status, DeepSeek and OpenRouter balances.
2. **Decides**, per *route* (scope), which rung of that route's ladder to use.
3. **Applies** the choice with the official CLI (`hermes -p <bot> config set …`,
   `hermes -p <bot> cron edit … --provider --model`) and reports each change once (to the cron job's delivery target).

## Scopes (what can be routed)
`<bot>.main` · `<bot>.delegation` · `<bot>.aux.<task>` · `<bot>.cron.<job_id>` — mapped to a
task class in `policy.yaml`. Profiles listed in `never_touch` are never read
or written — not even their config.

## Ladders
Each task class has an ordered ladder. Provider **kinds** decide eligibility:

| kind | eligible when |
|---|---|
| subscription | weekly < `room_weekly` and session < `room_session` |
| credits (Nous) | credit > 0 |
| flat (Copilot) | plan active |
| metered API | `allow_metered: true` and balance ≥ `min_balance_usd` |

Order used: **subscriptions → Nous → flat-fee → metered with credit**. If nothing below is eligible
the route **holds** where it is.

A class can simply omit rungs it must never use: the example `sensitive` class has no pay-per-use
rung at all, and `vision` lists only image-capable models.

## Rules
- **Hot**: a subscription at/over `hot_weekly` or `hot_session` sheds its routes.
- **Hysteresis**: a route climbs back only when the preferred rung is below `return_*`
  (or its window reset) — prevents flapping.
- **Unknown ≠ exhausted**: a failed probe never moves a route and never makes a provider a target.
- **Drift**: if a live setting differs from what the router last applied (someone edited it by
  hand), the router notifies once and **holds** that route — it never reverts a manual change.
- **Holds / Force**: `hold` freezes a route; `force <rung>` moves it now *and* holds it; `release`
  hands it back to automatic routing.
- **Mode**: `dry_run` logs "WOULD switch" only; `enforce` applies.

## When a change takes effect
Settings are re-read per turn/session start. A running conversation keeps its current model until
its next turn; new sessions and cron runs pick up the new route immediately.

## One engine, three surfaces
`engine/quota_router.py` is the only implementation. The cron shim, the `/quota` slash command,
the dashboard tab and the desktop page all call it; an exclusive `fcntl` lock serialises every
read-modify-write of state, holds and policy.

## Files (runtime, not in the repo)
`<hermes root>/workspace/quota-router/`: `policy.yaml`, `state.json` (current rung per route +
drift), `overrides.json` (holds), `quota.jsonl` (readings), `decisions.jsonl` (audit), `.lock`.

## Security model
- **Who can act.** Local CLI/TUI/desktop: the machine's operator. Messaging: only
  `control.admins` (`platform:user_id`), nobody by default; changes only in a private chat.
  Dashboard/desktop API: behind Hermes' own session/login, JSON-only, same-origin, rate limited.
- **What it can change.** Only `model.*`, `delegation.*`, `auxiliary.<task>.*` and cron job
  provider/model, through the official `hermes` CLI with validated arguments (no shell). Scope
  names, model ids and profile names are validated before any file path or CLI argument is built.
- **What it sends where.** Each provider key goes only to that provider's usage endpoint. Nothing
  else leaves the machine. No telemetry.
- **Pay-per-use.** Off unless `allow_metered: true`; every switch to a metered rung is flagged 💳.

## Not done / open
- Per-query routing by a small classifier model.
- Local models are not router rungs; keep them as each profile's own fallback.
