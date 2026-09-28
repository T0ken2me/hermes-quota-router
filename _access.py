"""Who may use /quota. Deny by default on every remote surface.

- Local surfaces (CLI, TUI, desktop app) belong to whoever operates the machine: full access.
  The desktop app reaching a remote host is already behind that host's dashboard login.
- Messaging and any other remote surface (Telegram, Discord, Slack, API server, webhooks…): the
  sender must be listed in policy.yaml `control.admins` as "platform:user_id". Nobody is listed by
  default, so a fresh install answers nobody over chat.
- Changes (run, mode, hold, release, force) from messaging additionally require a private chat, so
  nobody else in a group sees or triggers fleet changes.
- An unidentified caller inside the messaging gateway process is treated as remote (denied).
"""
from __future__ import annotations

import sys

LOCAL_SURFACES = frozenset({"cli", "tui", "desktop", "local"})
PRIVATE_CHATS = frozenset({"dm", "private", "direct"})


def _session(name: str) -> str:
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return ""
    return (get_session_env(name, "") or "").strip()


def caller():
    """-> (surface, user_id, chat_type) for the current command invocation."""
    platform = _session("HERMES_SESSION_PLATFORM").lower()
    source = _session("HERMES_SESSION_SOURCE").lower()
    surface = platform or source
    if not surface:
        # Nothing bound: the plain CLI does this; the messaging gateway never should. Fail closed there.
        surface = "unknown" if "gateway.run" in sys.modules else "cli"
    return surface, _session("HERMES_SESSION_USER_ID"), _session("HERMES_SESSION_CHAT_TYPE").lower()


def check_access(policy: dict, mutate: bool):
    """-> (denial message or None, 'who' label for the audit log)."""
    surface, user, chat = caller()
    if surface in LOCAL_SURFACES:
        return None, surface
    admins = set((policy.get("control") or {}).get("admins") or [])
    who = f"{surface}:{user}" if user else surface
    if not user or who not in admins:
        return ("⛔ /quota is not enabled for you here. The operator can add "
                f"'{who}' to control.admins in policy.yaml." if user else "⛔ /quota is not available here."), who
    if mutate and chat not in PRIVATE_CHATS:
        return "⛔ Changes are only accepted in a private chat with the bot. Read-only commands work here.", who
    return None, who
