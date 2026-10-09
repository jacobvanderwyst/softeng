import secrets

import pytest

from degreeplan.config import BACKEND_DIR, ConfigError, Settings, load_dotenv_for_development


@pytest.fixture
def prod_env(tmp_path):
    """A valid production environment (random key, absolute DB paths outside the source tree)."""
    return {
        "APP_ENV": "production",
        "SECRET_KEY": secrets.token_urlsafe(48),
        "USERS_DATABASE_URL": f"sqlite:///{(tmp_path / 'users.sqlite3').as_posix()}",
        "PLANS_DATABASE_URL": f"sqlite:///{(tmp_path / 'plans.sqlite3').as_posix()}",
    }


def test_valid_production_environment(prod_env):
    s = Settings.from_env(prod_env)
    assert s.is_production and s.cookie_secure and s.cookie_name == "__Host-sid"
    assert s.ratelimit_enabled


def test_app_env_is_mandatory():
    with pytest.raises(ConfigError, match="APP_ENV"):
        Settings.from_env({})
    with pytest.raises(ConfigError, match="APP_ENV"):
        Settings.from_env({"APP_ENV": "staging"})


@pytest.mark.parametrize("drop", ["SECRET_KEY", "USERS_DATABASE_URL", "PLANS_DATABASE_URL"])
def test_production_requires_secrets_and_databases(prod_env, drop):
    del prod_env[drop]
    with pytest.raises(ConfigError):
        Settings.from_env(prod_env)


@pytest.mark.parametrize("weak", ["short", "change-me-" + "x" * 40, "an-example-" + "y" * 40, "dev-only-" + "z" * 40])
def test_production_rejects_weak_or_placeholder_keys(prod_env, weak):
    prod_env["SECRET_KEY"] = weak
    with pytest.raises(ConfigError, match="SECRET_KEY"):
        Settings.from_env(prod_env)


def test_secret_can_come_from_a_file_and_is_stripped(prod_env, tmp_path):
    key = secrets.token_urlsafe(48)
    key_file = tmp_path / "key"
    key_file.write_text(key + "\r\n", encoding="utf-8")
    del prod_env["SECRET_KEY"]
    prod_env["SECRET_KEY_FILE"] = str(key_file)
    assert Settings.from_env(prod_env).secret_key == key


def test_secret_value_and_file_together_is_ambiguous(prod_env, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("x" * 40)
    prod_env["SECRET_KEY_FILE"] = str(key_file)
    with pytest.raises(ConfigError, match="only one"):
        Settings.from_env(prod_env)


def test_empty_or_missing_secret_file_fails_without_leaking_paths_contents(prod_env, tmp_path):
    del prod_env["SECRET_KEY"]
    empty = tmp_path / "empty"
    empty.write_text("  \n")
    prod_env["SECRET_KEY_FILE"] = str(empty)
    with pytest.raises(ConfigError, match="empty"):
        Settings.from_env(prod_env)
    prod_env["SECRET_KEY_FILE"] = str(tmp_path / "does-not-exist")
    with pytest.raises(ConfigError, match="Cannot read"):
        Settings.from_env(prod_env)


def test_database_urls_may_come_from_secret_files(prod_env, tmp_path):
    url_file = tmp_path / "users_url"
    url_file.write_text(prod_env.pop("USERS_DATABASE_URL"))
    prod_env["USERS_DATABASE_URL_FILE"] = str(url_file)
    assert Settings.from_env(prod_env).users_database_url.startswith("sqlite:///")


def test_production_sqlite_paths_must_be_absolute_and_outside_the_source_tree(prod_env):
    prod_env["USERS_DATABASE_URL"] = "sqlite:///relative/users.sqlite3"
    with pytest.raises(ConfigError, match="absolute"):
        Settings.from_env(prod_env)
    prod_env["USERS_DATABASE_URL"] = f"sqlite:///{(BACKEND_DIR / 'users.sqlite3').as_posix()}"
    with pytest.raises(ConfigError, match="outside the source tree"):
        Settings.from_env(prod_env)


def test_the_two_databases_must_differ(prod_env):
    prod_env["PLANS_DATABASE_URL"] = prod_env["USERS_DATABASE_URL"]
    with pytest.raises(ConfigError, match="different"):
        Settings.from_env(prod_env)


@pytest.mark.parametrize(
    "key,value,match",
    [
        ("CORS_ORIGINS", "*", "exact origin"),
        ("CORS_ORIGINS", "https://ok.example,*", "exact origin"),
        ("CORS_ORIGINS", "http://app.example", "https"),
        ("COOKIE_SECURE", "false", "COOKIE_SECURE"),
        ("RATELIMIT_ENABLED", "false", "RATELIMIT_ENABLED"),
        ("PROXY_HOPS", "99", "between"),
        ("LOG_LEVEL", "LOUD", "LOG_LEVEL"),
        ("SESSION_IDLE_TIMEOUT", "86400", "must not exceed"),
    ],
)
def test_unsafe_or_invalid_options_are_rejected(prod_env, key, value, match):
    prod_env[key] = value
    with pytest.raises(ConfigError, match=match):
        Settings.from_env(prod_env)


def test_development_gets_an_ephemeral_random_key_never_a_constant(caplog):
    first = Settings.from_env({"APP_ENV": "development"})
    second = Settings.from_env({"APP_ENV": "development"})
    assert first.secret_key != second.secret_key and len(first.secret_key) >= 48
    assert not first.cookie_secure and first.cookie_name == "sid"
    assert "ephemeral" in caplog.text


def test_repr_never_contains_secrets(prod_env):
    s = Settings.from_env(prod_env)
    text = repr(s)
    assert s.secret_key not in text
    assert "sqlite" not in text and "database_url" not in text


def test_dotenv_is_only_read_in_development(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_LEVEL=DEBUG\nAPP_ENV=production\n")
    monkeypatch.setattr("degreeplan.config.BACKEND_DIR", tmp_path)

    production: dict[str, str] = {"APP_ENV": "production"}
    load_dotenv_for_development(production)
    assert "LOG_LEVEL" not in production

    development: dict[str, str] = {"APP_ENV": "development", "OTHER": "kept"}
    load_dotenv_for_development(development)
    assert development["LOG_LEVEL"] == "DEBUG"
    assert development["APP_ENV"] == "development"  # a .env file can never switch the environment

    explicit: dict[str, str] = {"APP_ENV": "development", "LOG_LEVEL": "ERROR"}
    load_dotenv_for_development(explicit)
    assert explicit["LOG_LEVEL"] == "ERROR"  # real environment variables win over the file
