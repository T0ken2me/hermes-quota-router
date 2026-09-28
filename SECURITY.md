# Security policy

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository. You will get an acknowledgement within
7 days. Include the version (`plugin.yaml`), your Hermes version and steps to reproduce.

## Supported versions

Only the latest release.

## Scope and design

What the router can touch, what it sends where, and who may control it:
[docs/DESIGN.md → Security model](docs/DESIGN.md#security-model). In short:

- It changes only the model/provider settings of routes listed in your policy, through the
  official `hermes` CLI with validated arguments.
- Provider keys are read from your Hermes install and sent only to that provider.
- Chat control needs an explicit allow-list (`control.admins`); the dashboard API relies on the
  Hermes dashboard login and refuses cross-site requests.

Out of scope: running the Hermes dashboard without its login, or exposing it to the internet (the
README shows the supported tailnet-only setup).
