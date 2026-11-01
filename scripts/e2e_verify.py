"""Browser end-to-end verification of the running Compose stack, plus README screenshots.

    PROCURELENS_CREDENTIALS_FILE=<private dir>/procurelens-credentials.txt \
        .venv/Scripts/python scripts/e2e_verify.py [--screenshots]

Uses Playwright with an installed Edge/Chrome (channel). Passwords are read from the private
credentials file into memory only; they are never printed, logged or screenshotted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

URL = "http://localhost:8089"
ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
RESULTS: list[tuple[str, bool, str]] = []


def creds() -> dict[str, str]:
    path = Path(os.environ["PROCURELENS_CREDENTIALS_FILE"])
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^password\[(\w+)\]=(.+)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


PAGES: list[Page] = []


def check(name: str, fn) -> None:
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"PASS  {name}")
    except Exception as exc:  # noqa: BLE001 - report every check
        RESULTS.append((name, False, " | ".join(str(exc).splitlines()[:6])[:600]))
        print(f"FAIL  {name}: {RESULTS[-1][2]}")
        art = ROOT / "e2e-artifacts"
        art.mkdir(exist_ok=True)
        for i, pg in enumerate(PAGES):
            pg.screenshot(path=str(art / f"fail-{len(RESULTS)}-{i}.png"), full_page=True)


def idle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)
    expect(page.get_by_test_id("stStatusWidget")).to_have_count(0, timeout=30000)


def login(page: Page, user: str, password: str) -> None:
    page.goto(URL)
    page.get_by_role("textbox", name="Username").fill(user)
    page.get_by_role("textbox", name="Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    idle(page)


def logout(page: Page) -> None:
    page.get_by_role("button", name="Sign out").click()
    idle(page)


def nav(page: Page, label: str) -> None:
    page.get_by_test_id("stSidebarNav").get_by_text(label, exact=True).click()
    idle(page)


def open_exception(page: Page, text: str) -> None:
    nav(page, "Exceptions")
    page.get_by_test_id("stSelectbox").filter(has_text="Open exception").click()
    page.get_by_role("option", name=re.compile(re.escape(text))).first.click()
    page.get_by_role("button", name="Open details").click()
    idle(page)


def shot(page: Page, name: str, enabled: bool) -> None:
    if enabled:
        IMAGES.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(IMAGES / name), full_page=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--screenshots", action="store_true")
    ap.add_argument("--channel", default="msedge")
    ap.add_argument(
        "--reset-demo",
        action="store_true",
        help="reset the synthetic demo dataset first (guarded CLI) for a repeatable run",
    )
    args = ap.parse_args()
    if args.reset_demo:
        import subprocess

        subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "init",
                "python",
                "-m",
                "procurelens.cli",
                "reset-demo",
                "--confirm",
                "demo",
            ],
            cwd=ROOT,
            check=True,
        )
    pw_ = creds()
    qty_case = "BRG-6204: invoiced 100, accepted 80 on PO-2026-0001"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
        page = ctx.new_page()
        PAGES.append(page)

        def bad_login():
            login(page, "no-such-user", "definitely-wrong")
            expect(
                page.get_by_text("Sign-in failed. Check your username and password")
            ).to_be_visible()

        check("wrong password shows generic error", bad_login)

        def admin_login():
            login(page, "admin", pw_["admin"])
            expect(page.get_by_role("heading", name="Overview")).to_be_visible()
            expect(page.get_by_text("Administrator")).to_be_visible()
            expect(
                page.get_by_text("Developed and built by Oren Salami · V1.0").first
            ).to_be_visible()
            assert "token" not in page.url.lower() and "session" not in page.url.lower()

        check("administrator login + footer credit + no token in URL", admin_login)
        shot(page, "01-overview.png", args.screenshots)

        def seeded():
            nav(page, "Exceptions")
            expect(page.get_by_text("10 exception(s)")).to_be_visible()

        check("seeded demo data yields 10 open exceptions", seeded)

        def investigate():
            open_exception(page, qty_case)
            calc = page.get_by_role("tabpanel", name="Calculation & source records")
            expect(calc.get_by_text("Unmatched quantity = 100 − 80 = 20 Nos")).to_be_visible()
            expect(
                calc.get_by_text("Exposure = 20 × average invoiced rate 12.50 = 250.00 EUR")
            ).to_be_visible()

        check("exception detail shows exact discrepancy calculation", investigate)
        shot(page, "02-exception-investigation.png", args.screenshots)

        def evidence():
            page.get_by_role("tab", name="Evidence & clarification draft").click()
            idle(page)
            expect(
                page.get_by_text(
                    re.compile(r"supplier-agreement-nordwind\.md@v2026\.1#chunk-")
                ).first
            ).to_be_visible()
            expect(page.get_by_text("Supplier clarification draft")).to_be_visible()
            expect(page.get_by_role("button", name="Export draft (.md)")).to_be_visible()

        check("evidence retrieval shows traceable agreement excerpt + draft", evidence)
        if args.screenshots:  # Streamlit scrolls an inner container, not the window
            page.get_by_text("2 · Retrieved agreement / policy evidence").evaluate(
                "el => el.scrollIntoView({block: 'start'})"
            )
            page.wait_for_timeout(500)
        shot(page, "03-evidence-and-draft.png", args.screenshots)

        def resolve():
            form = page.get_by_test_id("stForm").filter(has_text="Reason (required")
            form.get_by_text("Start review").click()
            form.get_by_role("button", name="Apply").click()
            idle(page)
            expect(page.get_by_text("In review").first).to_be_visible()
            form = page.get_by_test_id("stForm").filter(has_text="Reason (required")
            form.get_by_text("Resolve", exact=True).click()
            form.get_by_role("textbox").fill("Credit note CN-1042 received for 20 units (E2E)")
            form.get_by_role("button", name="Apply").click()
            idle(page)
            page.get_by_role("tab", name="History").click()
            expect(page.get_by_text("Evidence snapshot · Resolved")).to_be_visible()

        check("administrator start review → resolve with reason → history", resolve)

        def audit():
            nav(page, "Integrations & knowledge")
            page.get_by_role("tab", name="Diagnostics").click()
            idle(page)
            expect(page.get_by_text("Recent audit events")).to_be_visible()
            # Grid cells are canvas-rendered; audit row contents are verified via SQL separately.

        check("audit log section visible in diagnostics (admin)", audit)
        page.wait_for_timeout(5000)  # let the confirmation toast disappear
        shot(page, "04-integrations-diagnostics.png", args.screenshots)

        def help_center():
            nav(page, "Help Center")
            expect(page.get_by_text("Product overview and limitations").first).to_be_visible()
            page.get_by_text("Exception rules and tolerances").first.click()
            idle(page)
            expect(page.get_by_text("Matching uses")).to_be_visible()

        check("help center articles render", help_center)

        def do_logout():
            logout(page)
            expect(page.get_by_text("You have been signed out.")).to_be_visible()
            page.goto(URL + "/exceptions")
            idle(page)
            expect(page.get_by_role("button", name="Sign in")).to_be_visible()

        check("logout invalidates session; protected page requires sign-in", do_logout)

        # --- demo viewer, isolated browser context --------------------------------------
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
        page2 = ctx2.new_page()
        PAGES.append(page2)

        def demo_view():
            login(page2, "demo", pw_["demo"])
            expect(page2.get_by_text("Demo viewer (read-only)")).to_be_visible()
            expect(page2.get_by_text("Dataset: Demo (synthetic) only")).to_be_visible()
            open_exception(page2, "PAL-EU")
            expect(
                page2.get_by_text("Read-only demo account: review actions are disabled")
            ).to_be_visible()
            expect(page2.get_by_role("button", name="Apply")).to_be_disabled()
            nav(page2, "Integrations & knowledge")
            expect(page2.get_by_role("button", name="Sync demo source")).to_be_disabled()
            page2.get_by_role("tab", name="Knowledge").click()
            expect(page2.get_by_role("button", name="Import and index")).to_be_disabled()
            expect(page2.get_by_role("button", name="Reindex all embeddings")).to_be_disabled()

        check("demo viewer: read-only, writes disabled, demo dataset only", demo_view)

        def isolation():
            login(page, "admin", pw_["admin"])  # tab A: admin; tab B: demo viewer
            expect(page.get_by_text("Administrator")).to_be_visible()
            nav(page2, "Overview")
            expect(page2.get_by_text("Demo viewer (read-only)")).to_be_visible()
            logout(page)  # signing out in A must not affect B
            nav(page2, "Exceptions")
            expect(page2.get_by_text("Demo viewer (read-only)")).to_be_visible()
            expect(page2.get_by_text("exception(s)").first).to_be_visible()
            login(page, "admin", pw_["admin"])

        check("sessions isolated between browser contexts", isolation)

        def refresh_requires_login():
            page.reload()
            idle(page)
            expect(page.get_by_role("button", name="Sign in")).to_be_visible()

        check("browser refresh starts a new session (documented behaviour)", refresh_requires_login)
        browser.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} browser checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
