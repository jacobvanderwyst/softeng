"""Optional real-browser verification with generated credentials and temporary databases.

Run with the backend venv: python -m pip install playwright; python scripts/frontend_smoke_test.py
Uses installed Edge/Chrome when available, otherwise Playwright's installed Chromium.
No application environment variables, demo databases or existing accounts are used.
"""
from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from degreeplan import create_app
from degreeplan.config import Settings
from degreeplan.db import migrate
from degreeplan.devtools.sample_data import SAMPLE_USERNAMES, seed_sample_data


def main() -> None:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with TemporaryDirectory(prefix="edunav-browser-") as directory:
        root = Path(directory)
        settings = Settings.for_testing(
            f"sqlite:///{(root / 'users.sqlite3').as_posix()}",
            f"sqlite:///{(root / 'plans.sqlite3').as_posix()}",
            cookie_secure=False, log_level="ERROR",
        )
        app = create_app(settings)
        services = app.extensions["degreeplan"]
        credentials = {name: secrets.token_urlsafe(24) for name in SAMPLE_USERNAMES}
        migrate.upgrade(services.databases.users, "users")
        migrate.upgrade(services.databases.plans, "plans")
        with services.databases.users.connect() as users, services.databases.plans.connect() as plans:
            seed_sample_data(users, plans, settings=settings, passwords=services.passwords, credentials=credentials)
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
                chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
                executable = next((path for path in (edge, chrome) if path.is_file()), None)
                browser = playwright.chromium.launch(
                    headless=True, **({"executable_path": str(executable)} if executable else {}),
                )
                try:
                    page = browser.new_page()
                    failures: list[str] = []
                    page.on("pageerror", lambda error: failures.append(str(error)))

                    def visit(path: str) -> None:
                        page.goto(base + path)
                        expect(page.locator("main")).to_be_visible()
                        expect(page.locator("#appMessage")).to_have_text("")

                    def saved(message: str) -> None:
                        expect(page.locator("#appMessage")).to_have_text(message)

                    def login(name: str) -> None:
                        page.goto(base + "/login.html")
                        page.locator("#username").fill(name)
                        page.locator("#password").fill(credentials[name])
                        page.get_by_role("button", name="Sign In", exact=True).click()
                        expect(page.locator("#workspace")).to_be_visible()
                        expect(page.locator("#appMessage")).to_have_text("")

                    page.goto(base + "/")
                    expect(page.locator("#loginForm")).to_be_visible()
                    login("alice")
                    expect(page.locator("#dashboardCompletedCredits")).to_have_text("0")
                    expect(page.locator("#dashboardGPA")).to_have_text("—")
                    expect(page.locator("#dashboardGradedCredits")).to_have_text("0")
                    page.locator("#planName").fill("Browser Plan")
                    page.get_by_role("button", name="Create Plan", exact=True).click()
                    saved("Plan created. Add a semester to begin planning.")
                    expect(page.locator("#planSelect")).to_have_value("1")
                    page.get_by_role("link", name="Planner", exact=True).click()
                    expect(page.locator("#appMessage")).to_have_text("")
                    page.get_by_role("button", name="+ Add Semester", exact=True).click()
                    expect(page.locator("#addSemesterForm")).to_be_visible()
                    page.locator("#semesterTerm").select_option("Spring")
                    page.locator("#semesterYear").fill("2027")
                    page.get_by_role("button", name="Create Semester", exact=True).click()
                    saved("Semester saved.")
                    expect(page.locator(".semester-card h2")).to_have_text("Spring 2027")
                    page.reload()
                    expect(page.locator(".semester-card h2")).to_have_text("Spring 2027")
                    page.get_by_role("button", name="+ Add / Move Course", exact=True).click()
                    page.locator("#courseSelect").select_option("1")
                    page.get_by_role("button", name="Add to Semester", exact=True).click()
                    saved("Course saved.")
                    expect(page.locator(".planned-course h3")).to_have_text("CS101")
                    expect(page.locator(".semester-credits strong")).to_have_text("4")

                    page.get_by_role("link", name="My Degree", exact=True).click()
                    expect(page.locator("#appMessage")).to_have_text("")
                    page.get_by_label("Student-reported status for CS101", exact=True).select_option("Completed")
                    saved("Course progress saved.")
                    expect(page.locator("#degreeCompletedCredits")).to_have_text("4")
                    grade = page.get_by_label("Self-reported grade for CS101", exact=True)
                    grade.select_option("A")
                    saved("Course grade saved.")
                    expect(page.locator("#degreeGPA")).to_have_text("4.00")
                    expect(page.locator("#degreeGPACredits")).to_have_text("4")
                    expect(page.locator("#degreeQualityPoints")).to_have_text("16.00")
                    page.reload()
                    expect(page.locator("#degreeCompletedCredits")).to_have_text("4")
                    expect(grade).to_have_value("A")
                    # Status-only writes preserve grades, but non-completed grades never affect GPA.
                    status = page.get_by_label("Student-reported status for CS101", exact=True)
                    status.select_option("In Progress")
                    saved("Course progress saved.")
                    expect(grade).to_have_value("A")
                    expect(page.locator("#degreeGPA")).to_have_text("—")
                    status.select_option("Completed")
                    saved("Course progress saved.")
                    expect(page.locator("#degreeGPA")).to_have_text("4.00")
                    grade.select_option("F")
                    saved("Course grade saved.")
                    expect(page.locator("#degreeGPA")).to_have_text("0.00")
                    expect(page.locator("#degreeGPACredits")).to_have_text("4")
                    grade.select_option("")
                    saved("Course grade saved.")
                    expect(page.locator("#degreeGPA")).to_have_text("—")
                    grade.select_option("A")
                    saved("Course grade saved.")
                    page.get_by_role("link", name="Dashboard", exact=True).click()
                    expect(page.locator("#dashboardCompletedCredits")).to_have_text("4")
                    expect(page.locator("#dashboardGPA")).to_have_text("4.00")
                    expect(page.locator("#dashboardRequirements .course")).to_have_count(6)
                    page.get_by_role("link", name="Courses", exact=True).click()
                    expect(page.locator("#courseCount")).to_have_text("6 courses found")
                    page.locator("#courseSearch").fill("CS101")
                    expect(page.locator(".catalog-course-card")).to_have_count(1)
                    page.get_by_role("button", name="View Details", exact=True).click()
                    expect(page.locator("#detailCourseCode")).to_have_text("CS101")
                    expect(page.locator("#detailCourseStatus")).to_have_text("Completed")
                    page.keyboard.press("Escape")
                    expect(page.locator("#catalogModal")).to_be_hidden()

                    # Alternate plans do not overwrite course progress or each other's semesters.
                    page.locator("#planName").fill("Alternate")
                    page.get_by_role("button", name="Create Plan", exact=True).click()
                    saved("Plan created. Add a semester to begin planning.")
                    expect(page.locator("#planSelect")).to_have_value("2")
                    page.get_by_role("link", name="Planner", exact=True).click()
                    expect(page.locator("#semesterContainer .semester-card")).to_have_count(0)
                    page.locator("#planSelect").select_option("1")
                    saved("Plan loaded.")
                    expect(page.locator(".planned-course h3")).to_have_text("CS101")
                    # Failed plan loads must retain the previous complete snapshot.
                    page.route("**/api/v1/plans/2", lambda route: route.fulfill(
                        status=503, content_type="application/json",
                        body='{"error":{"message":"Temporary plan load failure"}}',
                    ))
                    page.locator("#planSelect").select_option("2")
                    expect(page.locator("#appMessage")).to_have_text("Temporary plan load failure")
                    expect(page.locator("#planSelect")).to_have_value("1")
                    expect(page.locator(".planned-course h3")).to_have_text("CS101")
                    page.unroute("**/api/v1/plans/2")
                    # A background plan switch must invalidate an open course dialog.
                    page.get_by_role("button", name="+ Add / Move Course", exact=True).click()
                    expect(page.locator("#courseModal")).to_be_visible()
                    page.locator("#planSelect").evaluate(
                        "element => { element.value = '2'; element.dispatchEvent(new Event('change')); }"
                    )
                    saved("Plan loaded.")
                    expect(page.locator("#courseModal")).to_be_hidden()
                    page.locator("#planSelect").select_option("1")
                    saved("Plan loaded.")
                    page.get_by_role("button", name="Remove", exact=True).click()
                    saved("Course removed.")
                    expect(page.locator(".planned-course")).to_have_count(0)
                    expect(page.locator(".semester-card h2")).to_have_text("Spring 2027")
                    page.once("dialog", lambda dialog: dialog.accept())
                    page.get_by_role("button", name="Delete Semester", exact=True).click()
                    saved("Semester deleted.")
                    expect(page.locator(".semester-card")).to_have_count(0)
                    # Incorrect current password is a form error, not a spurious session redirect.
                    page.get_by_text("Change Password", exact=True).click()
                    page.locator("#currentPassword").fill(secrets.token_urlsafe(24))
                    replacement = secrets.token_urlsafe(32)
                    page.locator("#newPassword").fill(replacement)
                    page.locator("#confirmPassword").fill(replacement)
                    page.get_by_role("button", name="Save Password", exact=True).click()
                    expect(page.locator("#passwordChangeMessage")).to_have_text("Current password is incorrect.")
                    expect(page.locator("#currentPassword")).to_have_value("")
                    page.locator("#currentPassword").fill(credentials["alice"])
                    page.locator("#newPassword").fill(replacement)
                    page.locator("#confirmPassword").fill(replacement)
                    page.get_by_role("button", name="Save Password", exact=True).click()
                    expect(page.locator("#passwordChangeMessage")).to_have_text(
                        "Password changed. Other sessions have been signed out."
                    )
                    expect(page.locator("#newPassword")).to_have_value("")
                    credentials["alice"] = replacement
                    page.get_by_role("button", name="Sign Out", exact=True).click()
                    expect(page.locator("#loginForm")).to_be_visible()
                    login("alice")
                    expect(page.locator("#dashboardGPA")).to_have_text("4.00")

                    page.get_by_role("button", name="Sign Out", exact=True).click()
                    expect(page.locator("#loginForm")).to_be_visible()
                    login("teacher1")
                    visit("/degree.html?student_id=1")
                    expect(page.locator("#degreeCompletedCredits")).to_have_text("4")
                    expect(page.locator(".degree-status-select")).to_have_count(0)
                    expect(page.locator(".degree-grade-select")).to_have_count(0)
                    expect(page.locator("#degreeGPA")).to_have_text("4.00")
                    expect(page.locator(".degree-grade-readonly").first).to_have_text("Self-reported grade: A")
                    expect(page.get_by_role("button", name="Create Plan", exact=True)).to_have_count(0)
                    visit("/planner.html?student_id=1")
                    expect(page.locator("#addSemesterButton")).to_be_hidden()
                    page.goto(base + "/degree.html?student_id=3")
                    expect(page.locator("#appMessage")).to_contain_text("Student not found")
                    expect(page.locator("#studentId")).to_be_visible()
                    expect(page.locator("#degreeRequirementsList")).to_be_hidden()

                    # Session expiry redirects rather than displaying stale account data.
                    page.context.clear_cookies()
                    page.reload()
                    expect(page.locator("#loginForm")).to_be_visible()
                    if failures:
                        raise RuntimeError(f"Browser JavaScript errors: {failures}")
                    print("Frontend browser smoke test passed: login, four pages, persistence, alternate plans, "
                          "semesters, progress, grades/GPA, password change, staff authorization, "
                          "logout and session expiry.")
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            services.databases.dispose()


if __name__ == "__main__":
    main()
