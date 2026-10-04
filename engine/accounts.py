"""Pure, fail-closed account quota selector. No I/O or credential material."""

from dataclasses import dataclass
from datetime import datetime
import math
import re

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_PROVIDERS = {
    "openai-codex",
    "anthropic",
    "nous",
    "openai",
    "google",
    "xai",
    "deepseek",
    "mistral",
}
_KINDS = {"subscription", "credits"}


def _ref(value, label):
    if type(value) is not str or not _SAFE.fullmatch(value):
        raise ValueError(f"invalid {label}")


def _utc(value, label):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset().total_seconds() != 0
    ):
        raise ValueError(f"{label} must be UTC-aware")


def _num(value, label, low=None, high=None):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or (low is not None and value < low)
        or (high is not None and value > high)
    ):
        raise ValueError(f"invalid {label}")


@dataclass(frozen=True)
class Account:
    account_ref: str
    pool_ref: str
    provider: str
    credential_ref: str
    priority: int = 0
    kind: str = "subscription"

    def __post_init__(self):
        for key in ("account_ref", "pool_ref", "credential_ref"):
            _ref(getattr(self, key), key)
        if type(self.provider) is not str or self.provider not in _PROVIDERS:
            raise ValueError("unsupported provider")
        if type(self.priority) is not int:
            raise ValueError("priority must be int")
        if self.kind not in _KINDS:
            raise ValueError("unsupported account kind")


@dataclass(frozen=True)
class QuotaWindow:
    name: str
    used_percent: float
    reset_at: datetime

    def __post_init__(self):
        if self.name not in {"session", "weekly"}:
            raise ValueError("unsupported quota window")
        _num(self.used_percent, "usage", 0, 100)
        _utc(self.reset_at, "reset_at")


@dataclass(frozen=True)
class AccountObservation:
    account_ref: str
    pool_ref: str
    credential_ref: str
    fetched_at: datetime
    windows: tuple = ()
    quota_shape: str | None = None
    subscription_only: bool = False
    credit_remaining: float | None = None
    credit_allowance: float | None = None
    available: bool = True

    def __post_init__(self):
        for key in ("account_ref", "pool_ref", "credential_ref"):
            _ref(getattr(self, key), key)
        _utc(self.fetched_at, "fetched_at")
        if type(self.windows) is not tuple or any(
            not isinstance(w, QuotaWindow) for w in self.windows
        ):
            raise ValueError("windows must be tuple of quota windows")
        names = [w.name for w in self.windows]
        if len(set(names)) != len(names):
            raise ValueError("duplicate windows")
        if self.quota_shape not in (None, "weekly_only", "session_and_weekly"):
            raise ValueError("unsupported quota shape")
        expected = (
            {"weekly"}
            if self.quota_shape == "weekly_only"
            else {"session", "weekly"}
            if self.quota_shape == "session_and_weekly"
            else set()
        )
        if self.quota_shape is not None and set(names) != expected:
            raise ValueError("quota shape/window mismatch")
        if type(self.subscription_only) is not bool or type(self.available) is not bool:
            raise ValueError("availability flags must be bool")
        for val, label in (
            (self.credit_remaining, "credit_remaining"),
            (self.credit_allowance, "credit_allowance"),
        ):
            if val is not None:
                _num(val, label, 0)


@dataclass(frozen=True)
class Decision:
    status: str
    account_ref: str | None
    pool_ref: str | None
    provider: str | None
    credential_ref: str | None
    reason: str

    def __post_init__(self):
        fields = (self.account_ref, self.pool_ref, self.provider, self.credential_ref)
        if self.status == "select":
            if any(x is None for x in fields) or not self.reason:
                raise ValueError("select requires complete binding and reason")
            for field in (self.account_ref, self.pool_ref, self.credential_ref):
                _ref(field, "decision reference")
            if self.provider not in _PROVIDERS:
                raise ValueError("unsupported provider")
        elif self.status == "defer":
            if any(x is not None for x in fields) or not self.reason:
                raise ValueError("defer cannot contain binding")
        else:
            raise ValueError("invalid status")


def choose_account(
    accounts,
    observations,
    *,
    now,
    current_account_ref=None,
    min_remaining_percent=50,
    max_age_seconds=300,
    emergency_credit_authorized=False,
    emergency_credit_budget=None,
    emergency_credit_spent=0,
):
    _utc(now, "now")
    _num(min_remaining_percent, "minimum", 0, 100)
    _num(max_age_seconds, "max age", 0)
    _num(emergency_credit_spent, "credit spent", 0)
    if type(emergency_credit_authorized) is not bool:
        raise ValueError("authorization must be bool")
    if emergency_credit_budget is not None:
        _num(emergency_credit_budget, "credit budget", 0)
    accounts, observations = tuple(accounts), tuple(observations)
    if any(not isinstance(a, Account) for a in accounts) or any(
        not isinstance(o, AccountObservation) for o in observations
    ):
        raise ValueError("unexpected account or observation")
    refs = [a.account_ref for a in accounts]
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate account identifiers")
    by_ref = {}
    for o in observations:
        if o.account_ref in by_ref:
            raise ValueError("duplicate observations")
        by_ref[o.account_ref] = o
    # Aliases in one pool denote one capacity. Contradictory snapshot metadata fails closed.
    pool_metadata = {}
    for o in observations:
        meta = (
            o.windows,
            o.quota_shape,
            o.credit_remaining,
            o.credit_allowance,
            o.available,
        )
        if o.pool_ref in pool_metadata and pool_metadata[o.pool_ref] != meta:
            return Decision(
                "defer",
                None,
                None,
                None,
                None,
                "contradictory shared-pool observations",
            )
        pool_metadata[o.pool_ref] = meta
    eligible = []
    for a in accounts:
        o = by_ref.get(a.account_ref)
        if o is None or (o.account_ref, o.pool_ref, o.credential_ref) != (
            a.account_ref,
            a.pool_ref,
            a.credential_ref,
        ):
            continue
        if (
            not o.available
            or o.subscription_only is not True
            or (now - o.fetched_at).total_seconds() < 0
            or (now - o.fetched_at).total_seconds() > max_age_seconds
        ):
            continue
        if a.kind == "subscription":
            if (
                o.quota_shape is None
                or not o.windows
                or any(
                    w.reset_at <= now or 100 - w.used_percent < min_remaining_percent
                    for w in o.windows
                )
            ):
                continue
            eligible.append(a)
        elif (
            emergency_credit_authorized
            and emergency_credit_budget is not None
            and o.credit_allowance is not None
            and o.credit_allowance > 0
            and o.credit_remaining is not None
            and o.credit_remaining <= o.credit_allowance
            and o.credit_remaining > 0
            and 100 * o.credit_remaining / o.credit_allowance >= min_remaining_percent
            and emergency_credit_budget > emergency_credit_spent
        ):
            eligible.append(a)
    subs = [a for a in eligible if a.kind == "subscription"]
    candidates = subs or [a for a in eligible if a.kind == "credits"]
    if not candidates:
        return Decision("defer", None, None, None, None, "no eligible account")
    if current_account_ref is not None:
        current = next(
            (a for a in candidates if a.account_ref == current_account_ref), None
        )
        if current is not None:
            chosen = current
        else:
            chosen = None
    else:
        chosen = None
    if chosen is None:
        chosen = min(candidates, key=lambda a: (a.priority, a.account_ref))
    return Decision(
        "select",
        chosen.account_ref,
        chosen.pool_ref,
        chosen.provider,
        chosen.credential_ref,
        "eligible subscription quota"
        if chosen.kind == "subscription"
        else "explicitly authorised emergency credits",
    )
