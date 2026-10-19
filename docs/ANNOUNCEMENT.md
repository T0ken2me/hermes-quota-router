# Community announcement — draft, not posted

Hermes Quota Router is an independent plugin for people running several Hermes profiles across AI subscriptions. You define a fallback ladder per task class; a scheduled, non-LLM job checks provider usage and proposes configuration changes. It starts in dry-run, supports holds and manual overrides, and exposes routing decisions plus read-only spend estimates in a dashboard.

The README includes a synthetic screenshot rendered from the real frontend, a minimal policy example, standalone CI and explicit limitations. Missing subscription windows are not counted as spare capacity; credit providers must remain above the configured reserve. This is provider-level polling, not exact account binding, a hard spending cap or a promise to avoid every rate limit.

Repository and installation: https://github.com/T0ken2me/hermes-quota-router

Feedback particularly welcome on installation against your Hermes version, probe compatibility and fallback policy. Include your platform and Hermes version in bug reports, but never tokens, account identities or private configuration. Report vulnerabilities privately as described in SECURITY.md.

Before posting: confirm the update is merged and CI is green; choose the relevant community and obtain approval for the actual post. Do not claim independent adoption or request artificial stars.
