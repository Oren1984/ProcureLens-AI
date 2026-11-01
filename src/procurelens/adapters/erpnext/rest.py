"""Read-only ERPNext (Frappe) REST client.

* Token auth header ``Authorization: token <api_key>:<api_secret>`` (Frappe token auth).
* Only GET requests to the configured base URL; redirects are not followed.
* Pagination with ``limit_start``/``limit_page_length``; a hard cap per doctype.
* Bounded retries with exponential backoff for timeouts, 429 and 5xx; ``Retry-After`` honoured
  up to a cap. 401/403 fail fast with a clear, secret-free message.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from urllib.parse import quote

import httpx

from .base import ConnectionStatus, FetchResult, SourceError

log = logging.getLogger(__name__)

LIST_FILTERS = {
    "Purchase Order": ("transaction_date", [["docstatus", "in", [1, 2]]]),
    "Purchase Receipt": ("posting_date", [["docstatus", "in", [1, 2]]]),
    "Purchase Invoice": ("posting_date", [["docstatus", "in", [1, 2]]]),
}
SUPPLIER_FIELDS = ["name", "supplier_name", "supplier_group", "disabled", "modified"]
MAX_RETRY_AFTER = 10.0


class ERPNextSource:
    adapter_name = "erpnext"
    dataset = "live"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        timeout: float = 15.0,
        max_retries: int = 3,
        page_size: int = 100,
        max_records: int = 2000,
        sync_from: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("ERPNEXT_BASE_URL must start with https:// (or http:// for local)")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"token {api_key}:{api_secret}",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )
        self.max_retries = max(0, max_retries)
        self.page_size = max(1, min(page_size, 500))
        self.max_records = max(1, max_records)
        self.sync_from = sync_from
        self._sleep = sleep

    def __repr__(self) -> str:  # never expose the auth header
        return f"ERPNextSource(base_url={str(self._client.base_url)!r})"

    def close(self) -> None:
        self._client.close()

    # -- HTTP -------------------------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        attempt = 0
        while True:
            try:
                resp = self._client.get(path, params=params)
            except httpx.TimeoutException as exc:
                error: SourceError = SourceError("timeout", "ERPNext request timed out.")
                cause: Exception | None = exc
                retry_after = None
            except httpx.TransportError as exc:
                error = SourceError("network", f"Could not reach ERPNext ({type(exc).__name__}).")
                cause, retry_after = exc, None
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except json.JSONDecodeError as exc:
                        raise SourceError(
                            "invalid_response", "ERPNext returned non-JSON data."
                        ) from exc
                if resp.status_code in (401, 403):
                    raise SourceError(
                        "auth",
                        f"ERPNext rejected the credentials or permissions "
                        f"(HTTP {resp.status_code})."
                        " Check the integration user's API key and read permissions.",
                    )
                if resp.status_code == 404:
                    raise SourceError("not_found", f"ERPNext resource not found: {path}")
                if 300 <= resp.status_code < 400:
                    raise SourceError("redirect", "ERPNext redirected the request; check the URL.")
                if resp.status_code == 429 or resp.status_code >= 500:
                    kind = "rate_limited" if resp.status_code == 429 else "server_error"
                    error = SourceError(kind, f"ERPNext returned HTTP {resp.status_code}.")
                    cause = None
                    retry_after = _retry_after(resp)
                else:
                    raise SourceError("http_error", f"ERPNext returned HTTP {resp.status_code}.")
            if attempt >= self.max_retries:
                raise error from cause
            delay = retry_after if retry_after is not None else 0.5 * (2**attempt)
            log.warning(
                "erpnext retry",
                extra={"event": "erpnext_retry", "kind": error.kind, "attempt": attempt + 1},
            )
            self._sleep(min(delay, MAX_RETRY_AFTER))
            attempt += 1

    # -- public API ---------------------------------------------------------------------------
    def check_connection(self) -> ConnectionStatus:
        try:
            data = self._get("/api/method/frappe.auth.get_logged_user")
        except SourceError as exc:
            return ConnectionStatus(False, str(exc))
        user = data.get("message") if isinstance(data, dict) else None
        return ConnectionStatus(True, "Connected to ERPNext.", str(user) if user else None)

    def _list(self, doctype: str, fields: list[str], filters: list) -> tuple[list[dict], bool]:
        rows: list[dict] = []
        start = 0
        while True:
            params = {
                "fields": json.dumps(fields),
                "filters": json.dumps(filters),
                "limit_start": start,
                "limit_page_length": self.page_size,
                "order_by": "name asc",
            }
            data = self._get(f"/api/resource/{quote(doctype)}", params)
            page = data.get("data") if isinstance(data, dict) else None
            if not isinstance(page, list):
                raise SourceError("invalid_response", f"Unexpected list response for {doctype}.")
            rows.extend(page)
            if len(rows) >= self.max_records:
                return rows[: self.max_records], True
            if len(page) < self.page_size:
                return rows, False
            start += self.page_size

    def fetch(self, doctype: str) -> FetchResult:
        if doctype == "Supplier":
            rows, truncated = self._list("Supplier", SUPPLIER_FIELDS, [])
            return FetchResult(
                rows, truncated, _trunc_warning(doctype, truncated, self.max_records)
            )
        date_field, filters = LIST_FILTERS[doctype]
        filters = list(filters)
        if self.sync_from:
            filters.append([date_field, ">=", self.sync_from])
        names, truncated = self._list(doctype, ["name"], filters)
        records: list[dict] = []
        warnings = _trunc_warning(doctype, truncated, self.max_records)
        for row in names:
            name = row.get("name") if isinstance(row, dict) else None
            if not isinstance(name, str) or not name:
                warnings.append(f"{doctype}: list row without a name skipped")
                continue
            try:
                data = self._get(f"/api/resource/{quote(doctype)}/{quote(name, safe='')}")
            except SourceError as exc:
                if exc.kind == "not_found":
                    warnings.append(f"{doctype} {name}: no longer available, skipped")
                    continue
                raise
            doc = data.get("data") if isinstance(data, dict) else None
            if not isinstance(doc, dict):
                warnings.append(f"{doctype} {name}: unexpected detail response, skipped")
                continue
            records.append(doc)
        return FetchResult(records, truncated, warnings)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def _trunc_warning(doctype: str, truncated: bool, cap: int) -> list[str]:
    return [f"{doctype}: stopped at the configured cap of {cap} records"] if truncated else []
