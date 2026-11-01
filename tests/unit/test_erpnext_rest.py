"""Contract tests for the ERPNext REST adapter against recorded-shape HTTP fixtures.

These verify request construction and response handling. They are NOT a live ERPNext test.
"""

import json
from pathlib import Path

import httpx
import pytest

from procurelens.adapters.erpnext.base import SourceError
from procurelens.adapters.erpnext.mapping import normalize_document
from procurelens.adapters.erpnext.rest import ERPNextSource

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "erpnext"
DETAIL = {
    "Purchase Order": "purchase_order_PUR-ORD-2026-00012.json",
    "Purchase Receipt": "purchase_receipt_MAT-PRE-2026-00031.json",
    "Purchase Invoice": "purchase_invoice_ACC-PINV-2026-00044.json",
}


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeERPNext:
    """Minimal Frappe REST emulation with request recording."""

    def __init__(self, *, list_pages=None, status_plan=None):
        self.requests: list[httpx.Request] = []
        self.list_pages = list_pages
        self.status_plan = list(status_plan or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status_plan:
            code, headers = self.status_plan.pop(0)
            if code != 200:
                return httpx.Response(code, headers=headers, json={"exc_type": "Err"})
        path = request.url.path
        if path == "/api/method/frappe.auth.get_logged_user":
            return httpx.Response(200, json={"message": "integration@example.invalid"})
        if path == "/api/resource/Supplier":
            return httpx.Response(200, json=load("supplier_list.json"))
        for doctype, fname in DETAIL.items():
            base = "/api/resource/" + doctype
            if path == base:
                start = int(request.url.params["limit_start"])
                size = int(request.url.params["limit_page_length"])
                names = self.list_pages or [load(fname)["data"]["name"]]
                return httpx.Response(
                    200, json={"data": [{"name": n} for n in names[start : start + size]]}
                )
            if path.startswith(base + "/"):
                name = path[len(base) + 1 :]
                doc = load(fname)
                if name != doc["data"]["name"]:
                    return httpx.Response(404, json={"exc_type": "DoesNotExistError"})
                return httpx.Response(200, json=doc)
        return httpx.Response(404, json={})


def source(fake, **kw):
    return ERPNextSource(
        "https://erp.example.invalid",
        "key123",
        "secret456",
        transport=httpx.MockTransport(fake),
        sleep=lambda _s: None,
        **kw,
    )


def test_token_auth_header_and_read_only_get_requests():
    fake = FakeERPNext()
    src = source(fake)
    assert src.check_connection().ok
    src.fetch("Purchase Order")
    assert all(r.method == "GET" for r in fake.requests)
    assert fake.requests[0].headers["Authorization"] == "token key123:secret456"
    assert "secret456" not in repr(src)


def test_list_request_filters_submitted_and_cancelled_and_sync_from():
    fake = FakeERPNext()
    source(fake, sync_from="2026-01-01").fetch("Purchase Invoice")
    params = fake.requests[0].url.params
    filters = json.loads(params["filters"])
    assert ["docstatus", "in", [1, 2]] in filters
    assert ["posting_date", ">=", "2026-01-01"] in filters
    assert params["order_by"] == "name asc"


@pytest.mark.parametrize("doctype", list(DETAIL))
def test_fixture_documents_map_with_line_references(doctype):
    result = source(FakeERPNext()).fetch(doctype)
    doc = normalize_document(doctype, result.records[0])
    assert doc.lines[0].po_id == "PUR-ORD-2026-00012"
    assert doc.lines[0].po_line_id == "a1b2c3d4e5"
    assert doc.raw["name"] == doc.source_id  # raw snapshot preserved


def test_receipt_fixture_keeps_rejected_quantity():
    rec = source(FakeERPNext()).fetch("Purchase Receipt").records[0]
    line = normalize_document("Purchase Receipt", rec).lines[0]
    assert (line.qty, line.received_qty, line.rejected_qty) == (80, 85, 5)


def test_pagination_walks_all_pages():
    names = [f"PO-{i:03d}" for i in range(7)]
    fake = FakeERPNext(list_pages=names)
    src = source(fake, page_size=3)
    result = src.fetch("Purchase Order")
    list_calls = [r for r in fake.requests if r.url.path == "/api/resource/Purchase Order"]
    assert [int(r.url.params["limit_start"]) for r in list_calls] == [0, 3, 6]
    # these names have no detail fixture (404) → skipped with warnings, not fatal
    assert result.records == [] and len(result.warnings) == 7


def test_record_cap_truncates_with_warning():
    fake = FakeERPNext(list_pages=[f"PO-{i}" for i in range(10)])
    result = source(fake, page_size=2, max_records=4).fetch("Purchase Order")
    assert result.truncated and any("cap of 4" in w for w in result.warnings)


def test_auth_failure_is_fast_and_clear():
    fake = FakeERPNext(status_plan=[(401, {})])
    with pytest.raises(SourceError) as err:
        source(fake).fetch("Supplier")
    assert err.value.kind == "auth" and len(fake.requests) == 1
    assert "secret456" not in str(err.value)
    assert not source(FakeERPNext(status_plan=[(403, {})])).check_connection().ok


def test_rate_limit_retries_with_retry_after_then_succeeds():
    sleeps = []
    fake = FakeERPNext(status_plan=[(429, {"Retry-After": "2"}), (200, {})])
    src = ERPNextSource(
        "https://erp.example.invalid",
        "k",
        "s",
        transport=httpx.MockTransport(fake),
        sleep=sleeps.append,
    )
    assert src.fetch("Supplier").records
    assert sleeps == [2.0]


def test_retries_are_bounded_for_server_errors():
    fake = FakeERPNext(status_plan=[(503, {})] * 10)
    with pytest.raises(SourceError) as err:
        source(fake, max_retries=2).fetch("Supplier")
    assert err.value.kind == "server_error" and len(fake.requests) == 3


def test_timeouts_are_retried_then_reported():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    src = ERPNextSource(
        "https://erp.example.invalid",
        "k",
        "s",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        max_retries=1,
    )
    with pytest.raises(SourceError) as err:
        src.fetch("Supplier")
    assert err.value.kind == "timeout" and len(calls) == 2


def test_redirects_are_not_followed():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://evil.example.invalid/"})

    src = ERPNextSource(
        "https://erp.example.invalid",
        "k",
        "s",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    with pytest.raises(SourceError) as err:
        src.fetch("Supplier")
    assert err.value.kind == "redirect"


def test_non_json_response_is_rejected():
    src = ERPNextSource(
        "https://erp.example.invalid",
        "k",
        "s",
        sleep=lambda _s: None,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")),
    )
    with pytest.raises(SourceError) as err:
        src.fetch("Supplier")
    assert err.value.kind == "invalid_response"


def test_base_url_scheme_is_validated():
    with pytest.raises(ValueError):
        ERPNextSource("ftp://erp", "k", "s")
