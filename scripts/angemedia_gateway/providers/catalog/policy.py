"""政策来源的严格解析与审核时效投影，不据此推断账户可用性。"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .common import _dict, _reject_unknown_keys, _require_keys, _require_status, _require_string, _string_tuple
from .errors import CatalogValidationError
from .schema import PolicyEvidence

POLICY_KEYS = {"sources", "reviewed_at", "review_after", "lifecycle", "billing", "account_requirements", "notes"}
LIFECYCLES = {"documented", "legacy_unverified", "retired", "unknown"}
BILLING_KINDS = {"paid", "promotional", "usage_credits", "unknown"}
ACCOUNT_REQUIREMENTS = {"credential", "model_permission", "balance", "bound_aliyun_account", "real_name_verified", "magicube_balance"}


def parse_policy(label: str, value: Any) -> PolicyEvidence | None:
    """缺失记录保持未知；非法来源、日期或字段在加载时拒绝。"""
    if value is None:
        return None
    data = _dict(label, value)
    _reject_unknown_keys(label, data, POLICY_KEYS)
    _require_keys(label, data, POLICY_KEYS)
    sources = _string_tuple(f"{label}.sources", data["sources"])
    if not sources or len(sources) > 8:
        raise CatalogValidationError(f"{label}.sources must contain 1-8 public documentation URLs")
    for source in sources:
        try:
            parsed = urlsplit(source)
            valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
            valid = valid and not any(name.lower() in {"key", "api_key", "token", "secret", "password"} for name, _ in parse_qsl(parsed.query))
        except ValueError:
            valid = False
        if not valid:
            raise CatalogValidationError(f"{label}.sources must use public HTTPS URLs without credentials")
    dates = []
    for key in ("reviewed_at", "review_after"):
        raw_date = data[key]
        # YAML可把未加引号的日期解析成date，统一后仍要求标准日历日期。
        value = raw_date.isoformat() if type(raw_date) is date else _require_string(f"{label}.{key}", raw_date)
        try:
            parsed_date = date.fromisoformat(value)
            if parsed_date.isoformat() != value:
                raise ValueError("date must use YYYY-MM-DD")
        except ValueError as exc:
            raise CatalogValidationError(f"{label}.{key} must use YYYY-MM-DD") from exc
        dates.append(parsed_date)
    if dates[1] <= dates[0]:
        raise CatalogValidationError(f"{label}.review_after must be later than reviewed_at")
    requirements = _string_tuple(f"{label}.account_requirements", data["account_requirements"])
    if set(requirements) - ACCOUNT_REQUIREMENTS:
        raise CatalogValidationError(f"{label}.account_requirements contains an unknown requirement")
    notes = data["notes"]
    return PolicyEvidence(
        sources=sources, reviewed_at=dates[0].isoformat(), review_after=dates[1].isoformat(),
        lifecycle=_require_status(f"{label}.lifecycle", data["lifecycle"], LIFECYCLES),
        billing=_require_status(f"{label}.billing", data["billing"], BILLING_KINDS),
        account_requirements=requirements, notes="" if notes == "" else _require_string(f"{label}.notes", notes),
    )


def policy_api_response(policy: PolicyEvidence | None, *, as_of: date | None = None) -> dict[str, Any]:
    """审核期限是本项目复核窗口；到期不继续把促销或能力当成当前事实。"""
    if policy is None:
        return {
            "review_status": "unknown", "sources": [], "reviewed_at": None, "review_after": None,
            "billing": "unknown", "reviewed_billing": "unknown", "lifecycle": "unknown", "reviewed_lifecycle": "unknown",
            "account_requirements": [], "notes": "", "evidence_scope": "public_documentation",
            "account_access_verified": False, "generation_verified": False,
        }
    today = as_of or datetime.now(timezone.utc).date()
    current = date.fromisoformat(policy.reviewed_at) <= today < date.fromisoformat(policy.review_after)
    return {
        "review_status": "current" if current else "review_due",
        "sources": list(policy.sources), "reviewed_at": policy.reviewed_at, "review_after": policy.review_after,
        "billing": policy.billing if current else "unknown", "reviewed_billing": policy.billing,
        "lifecycle": policy.lifecycle if current or policy.lifecycle == "retired" else "unknown",
        "reviewed_lifecycle": policy.lifecycle,
        "account_requirements": list(policy.account_requirements), "notes": policy.notes,
        "evidence_scope": "public_documentation", "account_access_verified": False, "generation_verified": False,
    }
