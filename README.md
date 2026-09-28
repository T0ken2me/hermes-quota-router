# Hermes Quota Router

Keeps your [Hermes](https://github.com/NousResearch/hermes-agent) bots running on whichever AI
subscription still has headroom, instead of hitting one plan's limit mid-task.

Every 30 minutes a zero-token cron job reads your usage (Claude, ChatGPT/Codex, Nous credit,
optionally Copilot, DeepSeek/OpenRouter balances) and moves each bot's model — main, subagents,
auxiliary tasks, cron jobs — down or back up a per-task ladder:

**subscriptions → Nous credits → flat-fee plan → pay-per-use API (opt-in) → hold**

You control it with `/quota` (CLI, TUI, desktop app, and — if you allow it — chat), from a
**dashboard tab**, or from a **desktop-app page**. All of them drive the same engine.
How it decides: [docs/DESIGN.md](docs/DESIGN.md).

## Contents

| Path | What |
|---|---|
| `engine/quota_router.py` | The engine (collect → decide → apply). The only implementation. |
| `__init__.py`, `_access.py`, `_engine.py`, `plugin.yaml` | Hermes plugin: `/quota` and who may use it |
| `dashboard/` | Dashboard tab (`dist/`) + backend API (`plugin_api.py`) |
| `desktop/plugin.js` | Desktop-app page, status-bar chip, command-palette entries |
| `scripts/install.sh` | Installer (safe to re-run) |
| `scripts/dashboard_set_password.sh` | Sets the dashboard login (hidden prompt, stores a hash only) |
| `scripts/remote_access.sh` | Puts the dashboard on your tailnet (HTTPS + login) |
| `examples/policy.example.yaml` | Starter policy: ladders, thresholds, routes |
| `tests/` | Ladder, security and API tests (never touch a live install) |

## Install

Requires Hermes with `hermes` on your PATH (macOS or Linux).

```bash
git clone https://github.com/T0ken2me/hermes-quota-router.git
cd hermes-quota-router
bash scripts/install.sh
hermes gateway restart
```

The installer copies the plugin to `~/.hermes/plugins/quota-router`, puts a small launcher in
`~/.hermes/scripts/`, writes a starter policy **in dry-run**, enables the plugin and creates the
half-hourly job. It never overwrites an existing policy and never turns enforcement on.
Change reports go to local files (`~/.hermes/cron/output/`) unless you install with, e.g.,
`QR_DELIVER=telegram bash scripts/install.sh`.

Then:

1. Edit `~/.hermes/workspace/quota-router/policy.yaml`: list your bots' routes under `scopes:`,
   adjust the ladders under `classes:`, and put any private or local-only profile in
   `never_touch`. Optional switches, all **off** by default:
   - `allow_metered: true` — allow pay-per-use API rungs;
   - `control.admins: ["telegram:<your user id>"]` — allow `/quota` from chat;
   - `probes.copilot: true` — read the Copilot plan status (see *Safety notes*).
2. Watch a few dry-run cycles: `/quota` shows what it *would* switch.
3. Turn it on: `/quota mode enforce`.
4. Desktop app: **Capabilities → Plugins → Rescan**, then enable **Quota Router**.

Update later: `git pull && bash scripts/install.sh`.

## Using it

```
/quota                        status: accounts, mode, routes off their first choice
/quota routes                 every route with its current model
/quota ladder <route>         the fallback ladder for one route
/quota run                    run a pass now
/quota hold <route> [note]    freeze a route
/quota release <route>        give it back to automatic routing
/quota force <route> <n>      move to rung n now and hold it
/quota mode dry_run|enforce
```

**Who can use it.** The local CLI, TUI and desktop app always can. Over Telegram, Discord, etc.
only the people in `control.admins` can — nobody by default — and changes are refused outside a
private chat with the bot (read-only commands still work in a group).

Routes are named `<bot>.main`, `<bot>.delegation`, `<bot>.aux.<task>`, `<bot>.cron.<job_id>`.
If you change a bot's model by hand, the router notices, tells you once and leaves that route
alone until you `release` it or update the policy.

## Dashboard login and remote access (optional)

Locally the dashboard needs no login: run `hermes dashboard` and open the **Quota Router** tab.
To reach it from other devices, use Tailscale — never plain LAN or the internet. The dashboard
shows much more than quotas: chat history, the API-key editor, a terminal.

**1. Set a password.** On the Hermes machine, in a terminal:

```bash
bash scripts/dashboard_set_password.sh
```

It asks for a username and a password (at least 14 characters) at a hidden prompt, stores only a
scrypt hash and a session-signing secret in `~/.hermes/.env` (file mode 600), and never prints
them. Run it again to change the password.

**2. Publish on your tailnet** (macOS; Tailscale installed and logged in):

```bash
bash scripts/remote_access.sh on
```

This keeps the dashboard bound to `127.0.0.1`, starts it as a login item, sets
`dashboard.public_url` so Hermes **refuses to start it without a login**, checks the login is
actually required, and only then turns on Tailscale Serve (tailnet only, not Funnel). It prints the
address, e.g. `https://<machine>.<tailnet>.ts.net`. On Linux, run the dashboard under your own
service manager with `--host 127.0.0.1` and use `tailscale serve` the same way.

**3. Connect.** Open that address from any device on your tailnet and sign in, or in the Hermes
desktop app use **Settings → Gateways → Remote gateway**.

Turn it off: `bash scripts/remote_access.sh off`.
Tip: a Tailscale ACL can limit port 443 on that machine to the devices that should see it.

## Tests

```bash
bash tests/run_all.sh
```

- `test_ladder` — fallback order, hysteresis, unknown readings, pay-per-use opt-in, dry-run.
- `test_security` — policy validation (path traversal, odd model ids), `never_touch`,
  holds/force, `/quota` access control.
- `test_api` — dashboard API: cross-site and form requests refused, rate limits, no error leaks.

They use a temporary copy of the example policy with the Hermes CLI fenced off, so they cannot
read or change a real install.

## Safety notes

- **Nothing leaves your machine** except each provider's own usage/balance check, sent with the
  key Hermes already holds for that provider. No telemetry, no third-party service.
- **No credentials** in this repo or in the files it writes. Your policy holds names and model ids.
- **What it can change:** the model/provider of the routes you list — nothing else — through the
  official `hermes` CLI with validated arguments.
- Profiles in `never_touch` are never read or written.
- An unreadable quota counts as *unknown*: nothing moves because of it.
- Pay-per-use APIs only with `allow_metered: true`, only above `min_balance_usd`, flagged 💳.
- The **Copilot probe** calls an undocumented GitHub endpoint (the one editors use) with your
  `gh` login. It is off by default; without it the plan is assumed active.
- The dashboard is the whole Hermes dashboard, not just this tab: never expose it without the
  login and Tailscale.

Found a security problem? See [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE). Independent project; not affiliated with Nous Research, Anthropic,
OpenAI or GitHub.
