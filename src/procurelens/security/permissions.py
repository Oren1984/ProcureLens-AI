"""Roles, permissions and the authenticated principal passed to every service call."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    DEMO_VIEWER = "demo_viewer"


class Permission(StrEnum):
    VIEW = "view"
    VIEW_LIVE = "view_live"
    INVESTIGATE = "investigate"
    SYNC = "sync"
    IMPORT_KNOWLEDGE = "import_knowledge"
    CONFIGURE = "configure"
    GENERATE_AI = "generate_ai"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.DEMO_VIEWER: frozenset({Permission.VIEW}),
}


class AuthorizationError(PermissionError):
    """Raised by services when the principal lacks a permission or dataset access."""


class AuthenticationError(PermissionError):
    """Raised when there is no valid, unexpired session."""


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    role: Role
    session_id: int

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise AuthorizationError(f"Your role ({self.role.value}) may not perform this action.")

    @property
    def datasets(self) -> tuple[str, ...]:
        return ("demo", "live") if self.can(Permission.VIEW_LIVE) else ("demo",)

    def require_dataset(self, dataset: str) -> None:
        if dataset not in self.datasets:
            raise AuthorizationError("This account is restricted to synthetic demo data.")
