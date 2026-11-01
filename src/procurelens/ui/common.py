"""Shared UI helpers. The UI holds no authority: every call goes through services, which
re-check the session and permissions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import streamlit as st

from procurelens import CREDIT
from procurelens.config import load_settings
from procurelens.logging_setup import configure_logging
from procurelens.security.permissions import AuthenticationError, Principal
from procurelens.services.container import Container, build_container

TOKEN_KEY = "_pl_session_token"  # noqa: S105 - session_state key name, not a secret
STATUS_BADGE = {
    "open": ":red-badge[Open]",
    "in_review": ":orange-badge[In review]",
    "resolved": ":green-badge[Resolved]",
}
TYPE_LABEL = {
    "qty_exceeds_receipt": "Qty exceeds receipt",
    "price_variance": "Price variance",
    "overdue_delivery": "Overdue delivery",
    "manual_review": "Manual review",
}
DATASET_LABEL = {"demo": "Demo (synthetic)", "live": "Live ERPNext"}


@st.cache_resource(show_spinner=False)
def container() -> Container:
    """Process-wide services. Takes no arguments, so no user data or secret enters a cache key;
    it caches only connection pools and the embedding model, never user/session state."""
    configure_logging()
    return build_container(load_settings())


def current_principal() -> Principal | None:
    """Validate the server-side session token on every script run."""
    token = st.session_state.get(TOKEN_KEY)
    if not token:
        return None
    try:
        return container().auth.validate(token)
    except AuthenticationError as exc:
        st.session_state.pop(TOKEN_KEY, None)
        st.session_state["_pl_flash"] = str(exc)
        return None


def require_principal() -> Principal:
    p = current_principal()
    if p is None:
        st.rerun()
    return p


def selected_dataset(principal: Principal) -> str:
    ds = st.session_state.get("dataset", "demo")
    return ds if ds in principal.datasets else "demo"


def money(value: Decimal | float | str, currency: str | None = None) -> str:
    cur = currency or container().settings.base_currency
    return f"{Decimal(str(value)):,.2f} {cur}"


def ago(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    delta = datetime.now(UTC) - ts
    secs = int(delta.total_seconds())
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} days ago"


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")


def safe_md(text: str) -> str:
    """Escape untrusted text (ERP fields, knowledge excerpts, model output) for st.markdown,
    so it cannot inject links, images, HTML or formatting."""
    return _MD_SPECIAL.sub(r"\\\1", text or "")


def quote_block(text: str) -> str:
    return "\n".join("> " + safe_md(line) if line.strip() else ">" for line in text.splitlines())


def help_link(topic: str, label: str) -> None:
    if st.button(f"ⓘ {label}", key=f"help-{topic}-{label}", type="tertiary"):
        st.session_state["help_topic"] = topic
        st.switch_page(st.session_state["_pl_pages"]["help"])


def footer() -> None:
    st.divider()
    st.caption(CREDIT)


def page_header(title: str, subtitle: str | None = None) -> None:
    st.title(title)
    if subtitle:
        st.caption(subtitle)
