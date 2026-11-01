"""Operator CLI (runs as the schema-owner role inside the one-shot ``init`` container).

  python -m procurelens.cli bootstrap        # migrate, grant, create users once, seed demo once
  python -m procurelens.cli reset-password --user admin
  python -m procurelens.cli reset-demo --confirm demo
  python -m procurelens.cli reindex --reason "..."
  python -m procurelens.cli diagnostics

Passwords are written only to the private credentials file, never to stdout or logs.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.engine import make_url

from procurelens import CREDIT
from procurelens.adapters.persistence.models import (
    ExceptionEvent,
    KnowledgeDocument,
    ProcurementException,
    SourceDocument,
    Supplier,
    SyncRun,
    User,
)
from procurelens.adapters.persistence.roles import apply_grants, ensure_runtime_role
from procurelens.config import load_settings, read_secret
from procurelens.logging_setup import configure_logging
from procurelens.security.passwords import generate_password
from procurelens.security.permissions import Principal, Role
from procurelens.services.audit import record_audit
from procurelens.services.container import build_container

log = logging.getLogger("procurelens.cli")
SYSTEM = Principal(user_id=0, username="system", role=Role.ADMIN, session_id=0)
CRED_FILE = "procurelens-credentials.txt"
ADMIN_USER, DEMO_USER = "admin", "demo"
# The read-only demo account has a deliberately public, documented password (README) so
# reviewers can try the synthetic demo. It grants no write access and no live-data access.
# The administrator password is always randomly generated and private.
DEMO_DEFAULT_PASSWORD = "procurelens-demo"  # noqa: S105 - intentionally public demo login
DEMO_PASSWORD_MIN_LENGTH = 12


def demo_password() -> str:
    pw = os.environ.get("PROCURELENS_DEMO_PASSWORD", "").strip() or DEMO_DEFAULT_PASSWORD
    if len(pw) < DEMO_PASSWORD_MIN_LENGTH:
        sys.exit(
            f"PROCURELENS_DEMO_PASSWORD must be at least {DEMO_PASSWORD_MIN_LENGTH} characters."
        )
    return pw


def _owner_url() -> str:
    url = read_secret("MIGRATION_DATABASE_URL")
    if not url:
        sys.exit("MIGRATION_DATABASE_URL is required for operator commands.")
    return url


def _wait_for_db(url: str, attempts: int = 30) -> None:
    eng = create_engine(url)
    for i in range(attempts):
        try:
            with eng.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except Exception:  # noqa: BLE001 - retry on any connection error
            log.info("waiting for database", extra={"attempt": i + 1})
            time.sleep(2)
    sys.exit("Database did not become available.")


def write_credentials(private_dir: Path, entries: dict[str, str], login_url: str) -> Path:
    """Atomically write the credentials file with owner-only permissions."""
    private_dir.mkdir(parents=True, exist_ok=True)
    target = private_dir / CRED_FILE
    existing: dict[str, str] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.startswith("password[") and "]=" in line:
                k, v = line[len("password[") :].split("]=", 1)
                existing[k] = v
    existing.update(entries)
    body = [
        "# ProcureLens AI local credentials — keep private, do not commit.",
        f"# {CREDIT}",
        f"login_url={login_url}",
    ]
    body += [f"username={u}" for u in sorted(existing)]
    body += [f"password[{u}]={p}" for u, p in sorted(existing.items())]
    fd, tmp = tempfile.mkstemp(dir=private_dir, prefix=".cred-")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def cmd_bootstrap(_args) -> None:
    settings = load_settings()
    owner_url = _owner_url()
    _wait_for_db(owner_url)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    cfg.attributes["url"] = owner_url
    command.upgrade(cfg, "head")
    log.info("migrations applied", extra={"event": "migrations_applied"})

    owner_engine = create_engine(owner_url)
    runtime = make_url(settings.database_url)
    if runtime.username and runtime.password and runtime.username != make_url(owner_url).username:
        ensure_runtime_role(owner_engine, runtime.username, runtime.password)
        apply_grants(owner_engine, runtime.username, make_url(owner_url).database)
        log.info("runtime role ready", extra={"event": "grants_applied", "role": runtime.username})

    c = build_container(settings, database_url=owner_url)
    created: dict[str, str] = {}
    with c.db.read() as s:
        have_admin = s.scalar(select(func.count()).where(User.role == "admin")) or 0
        have_demo = s.scalar(select(func.count()).where(User.username == DEMO_USER)) or 0
    if not have_admin:
        pw = generate_password()
        c.auth.create_user(ADMIN_USER, Role.ADMIN, pw, actor="bootstrap")
        created[ADMIN_USER] = pw
    if not have_demo:
        pw = demo_password()
        c.auth.create_user(DEMO_USER, Role.DEMO_VIEWER, pw, actor="bootstrap")
        created[DEMO_USER] = pw
    if created:
        path = write_credentials(settings.private_dir, created, settings.public_url)
        log.info(
            "initial accounts created; credentials written to private file",
            extra={
                "event": "accounts_created",
                "users": sorted(created),
                "credentials_file": str(path),
            },
        )
    else:
        log.info(
            "accounts already exist; credentials file not modified",
            extra={"event": "accounts_exist"},
        )

    with c.db.read() as s:
        demo_synced = (
            s.scalar(
                select(func.count()).where(
                    SyncRun.dataset == "demo", SyncRun.status.like("succeeded%")
                )
            )
            or 0
        )
        demo_docs = s.scalar(select(func.count()).where(KnowledgeDocument.dataset == "demo")) or 0
    if not demo_synced:
        run = c.sync.run(SYSTEM, c.source("demo"))
        log.info(
            "demo data seeded",
            extra={"event": "demo_seeded", "status": run.status, "run_id": run.id},
        )
    if not demo_docs and settings.knowledge_dir.is_dir():
        counts = c.knowledge.seed_directory(settings.knowledge_dir, "demo")
        log.info("demo knowledge indexed", extra={"event": "knowledge_seeded", **counts})
    log.info("bootstrap complete", extra={"event": "bootstrap_complete"})


def cmd_reset_password(args) -> None:
    settings = load_settings()
    c = build_container(settings, database_url=_owner_url())
    user = args.user.lower()
    if user == DEMO_USER:
        pw = c.auth.reset_password(user, actor="cli", password=demo_password())
        write_credentials(settings.private_dir, {user: pw}, settings.public_url)
        print(
            "Demo password set to the documented demo password (see README). "
            "All demo sessions were revoked."
        )
        return
    pw = c.auth.reset_password(user, actor="cli")
    path = write_credentials(settings.private_dir, {user: pw}, settings.public_url)
    print(
        f"Password for '{user}' reset. All of its sessions were revoked. "
        f"New password written to {path}"
    )


def cmd_reset_demo(args) -> None:
    if args.confirm != "demo":
        sys.exit("Refusing: pass --confirm demo to reset the synthetic demo dataset.")
    settings = load_settings()
    c = build_container(settings, database_url=_owner_url())
    with c.db.transaction() as s:
        # Only rows with dataset='demo' are touched; live ERPNext data is never deleted here.
        exc_ids = select(ProcurementException.id).where(ProcurementException.dataset == "demo")
        s.execute(delete(ExceptionEvent).where(ExceptionEvent.exception_id.in_(exc_ids)))
        s.execute(delete(ProcurementException).where(ProcurementException.dataset == "demo"))
        s.execute(delete(SourceDocument).where(SourceDocument.dataset == "demo"))
        s.execute(delete(Supplier).where(Supplier.dataset == "demo"))
        s.execute(delete(SyncRun).where(SyncRun.dataset == "demo"))
        record_audit(
            s,
            actor="cli",
            action="demo_reset",
            entity_type="dataset",
            entity_id="demo",
            dataset="demo",
            reason="Demo reset via CLI",
        )
    run = c.sync.run(SYSTEM, c.source("demo"))
    print(
        f"Demo dataset reset and re-seeded (sync status: {run.status}). Audit history is retained."
    )


def cmd_reindex(args) -> None:
    c = build_container(load_settings(), database_url=_owner_url())
    n = c.knowledge._reindex(actor="cli", reason=args.reason)
    print(f"Re-embedded {n} chunks with {c.knowledge.embedder.model_id}.")


def cmd_diagnostics(_args) -> None:
    settings = load_settings()
    c = build_container(settings)
    with c.db.read() as s:
        version = s.scalar(text("SELECT version_num FROM alembic_version"))
        ext = s.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
    print(f"database: ok (migration {version}, pgvector {ext})")
    print(f"erpnext configured: {settings.erpnext_configured}")
    if c.generator:
        print(f"ollama: {c.generator.status()[1]}")
    else:
        print("ollama: not configured (deterministic fallback active)")


def main(argv: list[str] | None = None) -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    p = argparse.ArgumentParser(prog="procurelens")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap").set_defaults(fn=cmd_bootstrap)
    rp = sub.add_parser("reset-password")
    rp.add_argument("--user", required=True, choices=[ADMIN_USER, DEMO_USER])
    rp.set_defaults(fn=cmd_reset_password)
    rd = sub.add_parser("reset-demo")
    rd.add_argument("--confirm", required=True)
    rd.set_defaults(fn=cmd_reset_demo)
    ri = sub.add_parser("reindex")
    ri.add_argument("--reason", required=True)
    ri.set_defaults(fn=cmd_reindex)
    sub.add_parser("diagnostics").set_defaults(fn=cmd_diagnostics)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
