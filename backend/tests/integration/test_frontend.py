from html.parser import HTMLParser

import pytest

from degreeplan.web import ASSET_DIR, PUBLIC_FILES


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []
        self.assets: list[str] = []
        self.modules: list[str] = []
        self.forms: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.forms.append(attributes)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.assets.append(attributes["href"])
        if tag == "script":
            self.modules.append(attributes.get("src"))
            assert attributes.get("type") == "module"


@pytest.mark.parametrize("filename", sorted(PUBLIC_FILES))
def test_frontend_assets_are_public_and_have_separate_csp(api, filename):
    response = api.get(f"/{filename}")
    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "'unsafe-eval'" not in policy
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    if filename.endswith(".js"):
        assert "javascript" in response.content_type


def test_home_is_dashboard_and_api_policy_remains_restrictive(api):
    assert api.get("/").data == api.get("/index.html").data
    assert "script-src" not in api.get("/api/v1/health/live").headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", [
    "/config.py", "/.env", "/../config.py", "/frontend/app.js", "/frontend/",
    "/web.py", "/style.css/../config.py", "/student-data.js", "/script.js",
])
def test_non_public_files_are_not_served(api, path):
    response = api.get(path)
    assert response.status_code == 404
    assert response.is_json
    assert "script-src" not in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("filename", sorted(name for name in PUBLIC_FILES if name.endswith(".html")))
def test_pages_reference_existing_assets_and_have_no_duplicate_ids(api, filename):
    parser = PageParser()
    parser.feed(api.get(f"/{filename}").get_data(as_text=True))
    assert len(parser.ids) == len(set(parser.ids))
    assert parser.modules == (["admin.js"] if filename == "admin.html" else ["app.js"])
    assert set(parser.assets) == {"style.css", "integration.css"}
    assert all((ASSET_DIR / asset).is_file() for asset in [*parser.assets, *parser.modules])


def test_frontend_has_no_browser_only_state_or_mock_course_arrays():
    for name in ("app.js", "model.js", "index.html", "degree.html", "courses.html", "planner.html"):
        source = (ASSET_DIR / name).read_text(encoding="utf-8")
        assert "localStorage" not in source
        assert "innerHTML" not in source
        assert "COSC 1436" not in source


@pytest.mark.parametrize("filename, ids", [
    ("index.html", {"dashboardGPA", "dashboardGradedCredits", "dashboardQualityPoints"}),
    ("degree.html", {"degreeGPA", "degreeGPACredits", "degreeQualityPoints", "summaryGPA"}),
])
def test_pinned_gpa_markup_has_safe_empty_defaults(api, filename, ids):
    source = api.get(f"/{filename}").get_data(as_text=True)
    parser = PageParser()
    parser.feed(source)
    assert ids <= set(parser.ids)
    assert "7db3e17f7ef14469df4ba50b6a3090c87a99a395" in source
    assert "Self-reported Planning GPA" in source
    assert "GPA credits are not earned credits" in source
    assert '<main class="dashboard" hidden>' in source
    assert "<style>" not in source
    assert "student-data.js" not in source


def test_documented_client_matches_the_shipped_client():
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / "docs" / "examples" / "api-client.js"
    assert example.read_text(encoding="utf-8") == (ASSET_DIR / "api-client.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("filename, action", [
    ("login.html", "/api/v1/auth/login"), ("register.html", "/api/v1/auth/register"),
])
def test_account_forms_never_fall_back_to_credentials_in_query_strings(api, filename, action):
    source = api.get(f"/{filename}").get_data(as_text=True)
    parser = PageParser()
    parser.feed(source)
    assert parser.forms[0]["method"] == "post"
    assert parser.forms[0]["action"] == action
    if filename == "register.html":
        for field in ("role", "advisor_id", "is_active", "user_id"):
            assert f'name="{field}"' not in source
