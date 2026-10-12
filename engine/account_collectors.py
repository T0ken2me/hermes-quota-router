"""Injected, fail-closed quota collection boundary; performs no I/O itself."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math

from .accounts import Account, AccountObservation, QuotaWindow


@dataclass(frozen=True)
class Binding:
    account: Account
    pool_ref: str
    credential_ref: str


@dataclass(frozen=True)
class UsageReceipt:
    account_ref: str
    pool_ref: str
    provider: str
    credential_ref: str
    collected_at: datetime
    payload: object


@dataclass(frozen=True)
class CollectionResult:
    status: str
    observation: AccountObservation | None = None
    code: str = ""


def _utc(value):
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset().total_seconds() == 0
    )


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def _epoch(value, now):
    if type(value) is not int:
        raise ValueError
    dt = datetime.fromtimestamp(value, timezone.utc)
    if dt <= now:
        raise ValueError
    return dt


def _iso(value, now):
    if type(value) is not str:
        raise ValueError
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt <= now:
        raise ValueError
    return dt.astimezone(timezone.utc)


def _codex(payload, account, at):
    if type(payload) is not dict or set(payload) - {
        "plan_type",
        "primary_window",
        "secondary_window",
        "allowed",
        "limit_reached",
        "additional_limits",
    }:
        raise ValueError("supported_contract_required")
    if type(payload.get("plan_type")) is not str or payload["plan_type"] not in {
        "pro",
        "prolite",
        "team",
        "business",
        "enterprise",
    }:
        raise ValueError("supported_contract_required")
    if (
        type(payload.get("allowed")) is not bool
        or type(payload.get("limit_reached")) is not bool
    ):
        raise ValueError("malformed")
    if payload["allowed"] is not True or payload["limit_reached"] is not False:
        raise ValueError("unavailable")
    additional = payload.get("additional_limits")
    if additional not in (None, {}, []):
        raise ValueError("supported_contract_required")
    p, s = payload.get("primary_window"), payload.get("secondary_window")
    if type(p) is not dict:
        raise ValueError("malformed")
    windows = []
    durations = []
    for item in (p, s):
        if item is None:
            continue
        if type(item) is not dict or set(item) != {
            "duration",
            "used_percent",
            "reset_at",
        }:
            raise ValueError("malformed")
        d = item["duration"]
        if (
            type(d) is not int
            or d not in (18000, 604800)
            or d in durations
            or not _number(item["used_percent"])
        ):
            raise ValueError("supported_contract_required")
        durations.append(d)
        name = "session" if d == 18000 else "weekly"
        windows.append(
            QuotaWindow(name, float(item["used_percent"]), _epoch(item["reset_at"], at))
        )
    if durations not in ([604800], [18000, 604800]) and set(durations) != {
        18000,
        604800,
    }:
        raise ValueError("supported_contract_required")
    if set(durations) == {604800}:
        shape = "weekly_only"
    elif set(durations) == {18000, 604800}:
        shape = "session_and_weekly"
    else:
        raise ValueError("supported_contract_required")
    return AccountObservation(
        account.account_ref,
        account.pool_ref,
        account.credential_ref,
        at,
        tuple(windows),
        shape,
        True,
    )


def _anthropic(payload, account, at):
    if type(payload) is not dict or set(payload) - {
        "five_hour",
        "seven_day",
        "extra_usage",
    }:
        raise ValueError("supported_contract_required")
    extra = payload.get("extra_usage")
    if extra is not None and (
        type(extra) is not dict
        or set(extra) - {"is_enabled", "monthly_limit", "used_credits"}
    ):
        raise ValueError("supported_contract_required")
    if extra and extra.get("is_enabled") is True:
        raise ValueError("supported_contract_required")
    windows = []
    for key, name in (("five_hour", "session"), ("seven_day", "weekly")):
        item = payload.get(key)
        if (
            type(item) is not dict
            or set(item) != {"utilization", "resets_at"}
            or not _number(item["utilization"])
        ):
            raise ValueError("malformed")
        windows.append(
            QuotaWindow(name, float(item["utilization"]), _iso(item["resets_at"], at))
        )
    return AccountObservation(
        account.account_ref,
        account.pool_ref,
        account.credential_ref,
        at,
        tuple(windows),
        "session_and_weekly",
        True,
    )


def _nous(payload, account, at):
    if type(payload) is not dict or set(payload) - {
        "subscription_remaining",
        "subscription_allowance",
        "purchased_credits",
    }:
        raise ValueError("supported_contract_required")
    rem, allow = (
        payload.get("subscription_remaining"),
        payload.get("subscription_allowance"),
    )
    bought = payload.get("purchased_credits", 0)
    if (
        any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in (rem, allow, bought)
        )
        or allow <= 0
        or rem > allow
    ):
        raise ValueError("malformed")
    return AccountObservation(
        account.account_ref,
        account.pool_ref,
        account.credential_ref,
        at,
        subscription_only=False,
        credit_remaining=float(rem),
        credit_allowance=float(allow),
    )


def collect_usage(account, resolver, fetcher, *, now):
    """Resolve(account_ref,pool_ref,provider,credential_ref)->Binding; fetcher(Binding)->UsageReceipt."""
    if not isinstance(account, Account):
        return CollectionResult("refused", code="binding_unavailable")
    try:
        at = now()
        if not _utc(at):
            return CollectionResult("refused", code="invalid_clock")
        binding = resolver(
            account.account_ref,
            account.pool_ref,
            account.provider,
            account.credential_ref,
        )
        if (
            not isinstance(binding, Binding)
            or binding.account != account
            or binding.pool_ref != account.pool_ref
            or binding.credential_ref != account.credential_ref
        ):
            return CollectionResult("refused", code="binding_mismatch")
    except Exception:
        return CollectionResult("refused", code="binding_unavailable")
    try:
        receipt = fetcher(binding)
    except Exception:
        return CollectionResult("refused", code="fetch_failed")
    if not isinstance(receipt, UsageReceipt):
        return CollectionResult("refused", code="receipt_missing")
    if (
        receipt.account_ref,
        receipt.pool_ref,
        receipt.provider,
        receipt.credential_ref,
    ) != (
        account.account_ref,
        account.pool_ref,
        account.provider,
        account.credential_ref,
    ):
        return CollectionResult("refused", code="receipt_mismatch")
    if not _utc(receipt.collected_at):
        return CollectionResult("refused", code="invalid_receipt_time")
    try:
        if account.provider == "openai-codex":
            obs = _codex(receipt.payload, account, receipt.collected_at)
        elif account.provider == "anthropic":
            obs = _anthropic(receipt.payload, account, receipt.collected_at)
        elif account.provider == "nous":
            obs = _nous(receipt.payload, account, receipt.collected_at)
        else:
            return CollectionResult("refused", code="unsupported_provider")
        return CollectionResult("observed", obs)
    except Exception as exc:
        reason = (
            str(exc)
            if str(exc) in {"supported_contract_required", "unavailable"}
            else "malformed_observation"
        )
        return CollectionResult("refused", code=reason)
