"""Exception lifecycle: Open → In review → Resolved, with reasoned reopen."""

from __future__ import annotations

from enum import StrEnum

from .models import ExceptionStatus

S = ExceptionStatus
MAX_REASON = 2000
MIN_REASON = 5


class Action(StrEnum):
    START_REVIEW = "start_review"
    RESOLVE = "resolve"
    REOPEN = "reopen"


TRANSITIONS: dict[Action, tuple[frozenset[ExceptionStatus], ExceptionStatus, bool]] = {
    # action: (allowed from, to, reason required)
    Action.START_REVIEW: (frozenset({S.OPEN}), S.IN_REVIEW, False),
    Action.RESOLVE: (frozenset({S.OPEN, S.IN_REVIEW}), S.RESOLVED, True),
    Action.REOPEN: (frozenset({S.RESOLVED}), S.OPEN, True),
}


class LifecycleError(ValueError):
    pass


def apply(action: Action, current: ExceptionStatus, reason: str | None) -> ExceptionStatus:
    allowed_from, target, needs_reason = TRANSITIONS[action]
    if current not in allowed_from:
        raise LifecycleError(
            f"Cannot {action.value.replace('_', ' ')} an exception that is {current.label.lower()}."
        )
    text = (reason or "").strip()
    if needs_reason and len(text) < MIN_REASON:
        raise LifecycleError(f"A reason of at least {MIN_REASON} characters is required.")
    if len(text) > MAX_REASON:
        raise LifecycleError(f"Reason must be at most {MAX_REASON} characters.")
    return target


def allowed_actions(current: ExceptionStatus) -> list[Action]:
    return [a for a, (frm, _, _) in TRANSITIONS.items() if current in frm]
