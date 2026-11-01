from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError

from procurelens.adapters.erpnext.demo import DemoSource
from procurelens.adapters.persistence.roles import apply_grants, ensure_runtime_role
from procurelens.domain.lifecycle import Action
from procurelens.security.permissions import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    Role,
)
from procurelens.services.auth import GENERIC_LOGIN_ERROR, AuthService

pytestmark = pytest.mark.db


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


# -- authentication ------------------------------------------------------------------------------
def test_login_failures_are_generic(container, admin):
    with pytest.raises(AuthenticationError) as wrong_pw:
        container.auth.login("admin", "nope")
    with pytest.raises(AuthenticationError) as no_user:
        container.auth.login("ghost", "nope")
    assert str(wrong_pw.value) == str(no_user.value) == GENERIC_LOGIN_ERROR


def test_login_throttling_blocks_even_correct_password(container, admin):
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            container.auth.login("admin", "wrong")
    with pytest.raises(AuthenticationError):
        container.auth.login("admin", "Admin-Test-Pass-1")


def test_throttle_window_expires(container):
    clock = Clock()
    auth = AuthService(container.db, clock=clock, max_failures=2, window_minutes=15)
    auth.create_user("u1", Role.ADMIN, "Pass-Word-123", actor="t")
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            auth.login("u1", "bad")
    with pytest.raises(AuthenticationError):
        auth.login("u1", "Pass-Word-123")
    clock.now += timedelta(minutes=16)
    auth.login("u1", "Pass-Word-123")


def test_idle_and_absolute_expiry(container):
    clock = Clock()
    auth = AuthService(container.db, clock=clock, idle_minutes=30, absolute_hours=8)
    auth.create_user("u2", Role.ADMIN, "Pass-Word-123", actor="t")
    token, _ = auth.login("u2", "Pass-Word-123")
    clock.now += timedelta(minutes=29)
    assert auth.validate(token).username == "u2"  # activity refreshes last_seen
    clock.now += timedelta(minutes=29)
    auth.validate(token)
    clock.now += timedelta(minutes=31)
    with pytest.raises(AuthenticationError):
        auth.validate(token)  # idle expired
    token2, _ = auth.login("u2", "Pass-Word-123")
    for _ in range(17):  # keep active every 29 min, past the 8 h absolute limit
        clock.now += timedelta(minutes=29)
        try:
            auth.validate(token2)
        except AuthenticationError:
            break
    else:
        pytest.fail("absolute expiry not enforced")
    assert clock.now - datetime(2026, 11, 1, 12, 0, tzinfo=UTC) >= timedelta(hours=8)


def test_logout_invalidates_and_sessions_are_isolated(container, admin):
    t1, _ = container.auth.login("admin", "Admin-Test-Pass-1")
    t2, _ = container.auth.login("admin", "Admin-Test-Pass-1")
    assert t1 != t2
    container.auth.logout(t1)
    with pytest.raises(AuthenticationError):
        container.auth.validate(t1)
    assert container.auth.validate(t2).username == "admin"
    with pytest.raises(AuthenticationError):
        container.auth.validate("forged-token")
    with pytest.raises(AuthenticationError):
        container.auth.validate(None)


def test_password_reset_revokes_sessions(container, admin):
    t, _ = container.auth.login("admin", "Admin-Test-Pass-1")
    new_pw = container.auth.reset_password("admin", actor="test")
    with pytest.raises(AuthenticationError):
        container.auth.validate(t)
    with pytest.raises(AuthenticationError):
        container.auth.login("admin", "Admin-Test-Pass-1")
    container.auth.login("admin", new_pw)


# -- authorization on every mutation --------------------------------------------------------------
def test_viewer_denied_every_mutation(container, admin, viewer):
    container.sync.run(admin, DemoSource())
    exc = container.exceptions.list(viewer, "demo")[0]  # viewing is allowed
    denied = [
        lambda: container.sync.run(viewer, DemoSource()),
        lambda: container.exceptions.transition(viewer, exc.id, Action.START_REVIEW, None),
        lambda: container.exceptions.transition(viewer, exc.id, Action.RESOLVE, "valid reason"),
        lambda: container.exceptions.reevaluate_all(viewer, "attempt"),
        lambda: container.knowledge.import_document(viewer, "demo", "x.md", b"# x\n\nbody"),
        lambda: container.knowledge.reindex(viewer, "attempt"),
        lambda: container.config.update(
            viewer,
            {
                "price_tolerance_pct": "50",
                "overdue_grace_days": "1",
                "retrieval_min_similarity": "0.1",
            },
            "attempt",
        ),
    ]
    for call in denied:
        with pytest.raises(AuthorizationError):
            call()
    assert container.exceptions.get(viewer, exc.id).status == "open"


def test_demo_viewer_cannot_read_live_data(container, admin, viewer):
    class LiveFake(DemoSource):
        dataset = "live"
        adapter_name = "erpnext"

    container.sync.run(admin, LiveFake())
    live = container.exceptions.list(admin, "live")
    assert live
    for call in [
        lambda: container.exceptions.list(viewer, "live"),
        lambda: container.exceptions.get(viewer, live[0].id),  # no access by guessing an id
        lambda: container.exceptions.overview(viewer, "live"),
        lambda: container.sync.history(viewer, "live"),
        lambda: container.knowledge.documents(viewer, "live"),
        lambda: container.knowledge.search(viewer, "live", "price"),
    ]:
        with pytest.raises(AuthorizationError):
            call()


def test_demo_and_live_datasets_are_isolated(container, admin):
    class LiveFake(DemoSource):
        dataset = "live"
        adapter_name = "erpnext"

    container.sync.run(admin, DemoSource())
    container.sync.run(admin, LiveFake())
    demo = container.exceptions.list(admin, "demo")
    live = container.exceptions.list(admin, "live")
    assert {e.dataset for e in demo} == {"demo"} and {e.dataset for e in live} == {"live"}
    container.exceptions.transition(admin, demo[0].id, Action.START_REVIEW, None)
    assert all(e.status == "open" for e in container.exceptions.list(admin, "live"))


def test_forged_principal_role_has_no_extra_power(container):
    p = Principal(999, "x", Role.DEMO_VIEWER, 0)
    with pytest.raises(AuthorizationError):
        p.require_dataset("live")


# -- database-level controls --------------------------------------------------------------------
def test_runtime_role_cannot_alter_audit_trail(container, admin, migrated_db):
    owner = create_engine(migrated_db)
    ensure_runtime_role(owner, "pl_app_test", "app-test-pass")
    apply_grants(owner, "pl_app_test", make_url(migrated_db).database)
    app_url = make_url(migrated_db).set(username="pl_app_test", password="app-test-pass")
    app = create_engine(app_url)
    with app.begin() as c:
        c.execute(
            text(
                "INSERT INTO audit_events (actor_name, action, entity_type, details) "
                "VALUES ('t','t','t','{}')"
            )
        )
    for stmt in [
        "UPDATE audit_events SET reason = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
        "DELETE FROM exception_events",
        "DROP TABLE users",
        "UPDATE exception_events SET reason = 'x'",
    ]:
        with pytest.raises(ProgrammingError), app.begin() as c:
            c.execute(text(stmt))
    # The trigger also blocks the owner role (a superuser can still disable it — documented).
    with pytest.raises(DBAPIError, match="append-only"), owner.begin() as c:
        c.execute(text("UPDATE audit_events SET reason = 'x'"))
    app.dispose()
    owner.dispose()


def test_reset_with_given_password_for_public_demo_account(container, viewer):
    t, _ = container.auth.login("demo", "Demo-Test-Pass-1")
    pw = container.auth.reset_password("demo", actor="test", password="procurelens-demo")
    assert pw == "procurelens-demo"
    with pytest.raises(AuthenticationError):
        container.auth.validate(t)
    _, p = container.auth.login("demo", "procurelens-demo")
    assert p.role == Role.DEMO_VIEWER
