"""Administrator-editable business settings (tolerances, criticality). Secrets never live here."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from procurelens.adapters.persistence.db import Database
from procurelens.adapters.persistence.models import AppSetting
from procurelens.security.permissions import Permission, Principal

from .audit import record_audit

DEFAULTS = {
    "price_tolerance_pct": "2.0",
    "overdue_grace_days": "3",
    "critical_items": "MTR-750,BRG-6204",
    "retrieval_min_similarity": "0.60",
}


@dataclass(frozen=True)
class BusinessSettings:
    price_tolerance_pct: Decimal
    overdue_grace_days: int
    critical_items: frozenset[str]
    retrieval_min_similarity: float


def validate_settings(values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        tol = Decimal(values["price_tolerance_pct"])
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("Price tolerance must be a number.") from exc
    if not (Decimal(0) <= tol <= Decimal(50)):
        raise ValueError("Price tolerance must be between 0 and 50 percent.")
    out["price_tolerance_pct"] = str(tol)
    try:
        grace = int(values["overdue_grace_days"])
    except (ValueError, KeyError) as exc:
        raise ValueError("Grace days must be a whole number.") from exc
    if not 0 <= grace <= 365:
        raise ValueError("Grace days must be between 0 and 365.")
    out["overdue_grace_days"] = str(grace)
    items = [i.strip() for i in values.get("critical_items", "").split(",") if i.strip()]
    if any(len(i) > 140 for i in items) or len(items) > 200:
        raise ValueError("Critical items: at most 200 item codes of up to 140 characters.")
    out["critical_items"] = ",".join(sorted(set(items)))
    try:
        sim = float(values["retrieval_min_similarity"])
    except (ValueError, KeyError) as exc:
        raise ValueError("Minimum similarity must be a number.") from exc
    if not 0.0 <= sim <= 1.0:
        raise ValueError("Minimum similarity must be between 0 and 1.")
    out["retrieval_min_similarity"] = str(sim)
    return out


def load_business_settings(session: Session) -> BusinessSettings:
    stored = dict(session.execute(select(AppSetting.key, AppSetting.value)).all())
    v = {**DEFAULTS, **stored}
    return BusinessSettings(
        price_tolerance_pct=Decimal(v["price_tolerance_pct"]),
        overdue_grace_days=int(v["overdue_grace_days"]),
        critical_items=frozenset(i for i in v["critical_items"].split(",") if i),
        retrieval_min_similarity=float(v["retrieval_min_similarity"]),
    )


class ConfigService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, principal: Principal) -> BusinessSettings:
        principal.require(Permission.VIEW)
        with self.db.read() as s:
            return load_business_settings(s)

    def update(self, principal: Principal, values: dict[str, str], reason: str) -> None:
        principal.require(Permission.CONFIGURE)
        if len((reason or "").strip()) < 5:
            raise ValueError("A reason of at least 5 characters is required.")
        clean = validate_settings(values)
        with self.db.transaction() as s:
            before = dict(s.execute(select(AppSetting.key, AppSetting.value)).all())
            for key, value in clean.items():
                row = s.get(AppSetting, key)
                if row is None:
                    s.add(AppSetting(key=key, value=value, updated_by=principal.username))
                elif row.value != value:
                    row.value, row.updated_by = value, principal.username
            record_audit(
                s,
                actor=principal.username,
                action="settings_updated",
                entity_type="app_settings",
                reason=reason.strip(),
                details={"before": {k: before.get(k, DEFAULTS[k]) for k in clean}, "after": clean},
            )
