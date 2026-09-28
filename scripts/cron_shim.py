#!/usr/bin/env python3
"""Cron entry point for the quota router. The engine lives in the quota-router plugin; cron only
runs scripts physically inside ~/.hermes/scripts/, so this shim hands over to it. Installed by
the plugin's scripts/install.sh — edit the plugin, not this file."""
import os, runpy
from pathlib import Path
root = Path(os.environ.get("QR_HERMES_ROOT") or Path.home() / ".hermes")
runpy.run_path(str(root / "plugins" / "quota-router" / "engine" / "quota_router.py"), run_name="__main__")
