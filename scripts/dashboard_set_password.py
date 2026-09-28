#!/usr/bin/env python3
"""Set the Hermes dashboard login (username + scrypt password hash) in <hermes root>/.env.

Run it yourself in a terminal on the Hermes machine:  bash scripts/dashboard_set_password.sh
The password is typed at a hidden prompt, hashed immediately, and only the hash is stored.
A new session-signing secret is generated each time, so changing the password signs out every
existing session.
"""
import getpass
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(os.environ.get("QR_HERMES_ROOT") or Path.home() / ".hermes")
if (ROOT / "hermes-agent").is_dir():
    sys.path.insert(0, str(ROOT / "hermes-agent"))
try:
    import hermes_bootstrap  # noqa: F401
except ImportError:
    pass
try:
    from plugins.dashboard_auth.basic import hash_password
except ImportError:
    sys.exit("Could not find Hermes' dashboard login module — is Hermes installed and up to date?")

ENV = ROOT / ".env"
P = "HERMES_DASHBOARD_BASIC_AUTH_"

if not sys.stdin.isatty():
    sys.exit("Run this in an interactive terminal (the password is read at a hidden prompt).")

user = input("Dashboard username [admin]: ").strip() or "admin"
if not user.replace("-", "").replace("_", "").replace(".", "").isalnum():
    sys.exit("Username: letters, digits, '.', '-', '_' only.")
while True:
    pw = getpass.getpass("New dashboard password (min 14 chars): ")
    if len(pw) < 14:
        print("Too short — use at least 14 characters.")
        continue
    if pw != getpass.getpass("Repeat: "):
        print("Didn't match, try again.")
        continue
    break

lines = ENV.read_text().splitlines() if ENV.exists() else []
keep = [line for line in lines if not line.startswith(P)]
keep += [f"{P}USERNAME={user}", f"{P}PASSWORD_HASH={hash_password(pw)}",
         f"{P}SECRET={secrets.token_urlsafe(48)}"]
del pw

tmp = ENV.with_name(ENV.name + ".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others
with os.fdopen(fd, "w") as f:
    f.write("\n".join(keep) + "\n")
os.replace(tmp, ENV)
os.chmod(ENV, 0o600)
print(f"Saved login for '{user}' (hash only). Restart the dashboard for it to take effect.")
