"""Small atomic JSON account metadata store. Credential handles are never persisted."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.RLock()
_FIELDS = ("account_ref", "pool_ref", "provider", "label", "kind", "priority", "enabled", "notes", "last_validated_at", "plan_label", "status")


class AccountStore:
    def __init__(self, path):
        self.path = Path(path)
        self._records = None

    def _load(self):
        with _LOCK:
            if self._records is None:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    self._records = {r["account_ref"]: {k: r[k] for k in _FIELDS if k in r} for r in data if isinstance(r, dict) and isinstance(r.get("account_ref"), str)}
                except FileNotFoundError:
                    self._records = {}
                except (OSError, ValueError, TypeError):
                    self._records = {}
            return self._records

    def _persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [{k: v for k, v in rec.items() if k in _FIELDS} for rec in self._load().values()]
        fd, temp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def list_accounts(self):
        with _LOCK:
            return [dict(record) for record in self._load().values()]

    def add_account(self, record):
        with _LOCK:
            records = self._load()
            ref = record["account_ref"]
            if ref in records:
                raise ValueError("account already exists")
            saved = {k: record.get(k) for k in _FIELDS if k in record}
            records[ref] = saved
            self._persist()
            return dict(record)

    def update(self, ref, **changes):
        with _LOCK:
            record = self._load().get(ref)
            if record is None:
                raise ValueError("unknown account")
            record.update(changes)
            self._persist()
            return dict(record)


_STORES = {}


def store(path):
    key = str(Path(path).resolve())
    with _LOCK:
        if key not in _STORES:
            _STORES[key] = AccountStore(key)
        return _STORES[key]
