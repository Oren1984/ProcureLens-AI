"""Interface shared by the demo source and the real ERPNext REST source.

Both return records in ERPNext's REST JSON shape, so one mapping/normalisation path is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DOCTYPES = ("Supplier", "Purchase Order", "Purchase Receipt", "Purchase Invoice")


class SourceError(RuntimeError):
    """A failure fetching from the source. ``kind`` is safe to display (no secrets)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ConnectionStatus:
    ok: bool
    message: str
    user: str | None = None


@dataclass
class FetchResult:
    records: list[dict]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


class ProcurementSource(Protocol):
    adapter_name: str
    dataset: str

    def check_connection(self) -> ConnectionStatus: ...

    def fetch(self, doctype: str) -> FetchResult: ...
