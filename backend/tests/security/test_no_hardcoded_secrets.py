"""Repository hygiene: no credentials or key material in tracked-style source files."""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent

SCAN_ROOTS = [BACKEND / "src", REPO / "deploy", REPO / "docs", REPO / "poc"]
SCAN_FILES = [REPO / "README.md", BACKEND / "README.md", BACKEND / ".env.example"]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".sql", ".ps1", ".xml", ".html", ".yml", ".yaml", ".toml", ".example",
                 ".cfg", ".ini", ".json", ".env", ""}
SKIP_DIRS = {"__pycache__", ".venv", "instance", "logs", "certs", ".pytest_cache", "node_modules"}

# Strings that used to appear in the prototype (demo passwords / constant fallback key).
RETIRED_SECRETS = ["-pass-123", "dev-only-insecure-key", "change-me-to-a-long-random-string"]

ASSIGNED_SECRET = re.compile(
    r"""(?ix)\b[\w.]*(password|passwd|secret|api[_-]?key|private[_-]?key)[\w.]*\s*[:=]\s*["'][^"'\s]{8,}["']"""
)
KEY_MATERIAL = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
URL_WITH_CREDENTIALS = re.compile(r"\b(?:postgres(?:ql)?|mysql|mssql|redis)\S*://[^/\s:@]+:[^@\s]+@", re.I)


def files():
    seen = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and not (SKIP_DIRS & set(path.parts)):
                seen.append(path)
    seen.extend(p for p in SCAN_FILES if p.exists())
    return seen


def test_scan_actually_covers_the_source_tree():
    assert any(p.name == "config.py" for p in files())


@pytest.mark.parametrize("path", files(), ids=lambda p: str(p.relative_to(REPO)))
def test_file_has_no_credentials(path):
    if path == Path(__file__):
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    for retired in RETIRED_SECRETS:
        assert retired not in text, f"retired demo secret {retired!r} found"
    assert not KEY_MATERIAL.search(text), "private key material found"
    assert not URL_WITH_CREDENTIALS.search(text), "database URL with embedded credentials found"
    match = ASSIGNED_SECRET.search(text)
    assert match is None, f"possible hardcoded secret: {match.group(0) if match else ''}"


def test_no_secret_bearing_files_in_source_directories():
    forbidden = {".env", "secret_key"}
    suffixes = {".pem", ".key", ".pfx", ".p12", ".sqlite3", ".db"}
    for root in (BACKEND / "src", REPO / "deploy", REPO / "docs"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not (SKIP_DIRS & set(path.parts)):
                assert path.name not in forbidden and path.suffix not in suffixes, str(path)
