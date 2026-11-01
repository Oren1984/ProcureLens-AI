"""Transparent triage score (0–100). A sorting aid only — never a decision."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import ExceptionType, Finding

EXPOSURE_POINTS_MAX = 50
EXPOSURE_PER_POINT = Decimal("100")  # 1 point per 100 units of base currency
OVERDUE_POINTS_MAX = 30
CRITICAL_POINTS = 15
MANUAL_REVIEW_POINTS = 5


@dataclass(frozen=True)
class PriorityScore:
    score: int
    reasons: tuple[str, ...]

    @property
    def band(self) -> str:
        if self.score >= 60:
            return "High"
        if self.score >= 30:
            return "Medium"
        return "Low"


def score_finding(f: Finding) -> PriorityScore:
    reasons: list[str] = []
    exposure_pts = min(EXPOSURE_POINTS_MAX, int(f.exposure / EXPOSURE_PER_POINT))
    reasons.append(
        f"Financial exposure {f.exposure}: {exposure_pts}/{EXPOSURE_POINTS_MAX} "
        f"(1 point per {EXPOSURE_PER_POINT}, capped)"
    )
    overdue_pts = min(OVERDUE_POINTS_MAX, max(0, f.days_overdue))
    if f.exception_type == ExceptionType.OVERDUE_DELIVERY:
        reasons.append(
            f"Overdue {f.days_overdue} days: {overdue_pts}/{OVERDUE_POINTS_MAX} (1 point per day)"
        )
    crit_pts = CRITICAL_POINTS if f.critical_item else 0
    if f.critical_item:
        reasons.append(f"Critical item {f.item_code}: +{CRITICAL_POINTS}")
    manual_pts = MANUAL_REVIEW_POINTS if f.exception_type == ExceptionType.MANUAL_REVIEW else 0
    if manual_pts:
        reasons.append(f"Needs manual review (unsupported record): +{MANUAL_REVIEW_POINTS}")
    total = min(100, exposure_pts + overdue_pts + crit_pts + manual_pts)
    return PriorityScore(score=total, reasons=tuple(reasons))
