import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_settings(**env):
    """Import config.settings in a clean process and return (exit code, stderr)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("LEZNARD_")}
    clean.update(env)
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE if hasattr(s, 'SESSION_COOKIE_SECURE') else False)"],
        cwd=ROOT, env=clean, capture_output=True, text=True,
    )
    return result


def test_development_defaults_still_work_on_a_laptop():
    result = load_settings()
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "False"]


def test_production_refuses_to_start_without_a_secret_key():
    result = load_settings(LEZNARD_ENV="production", LEZNARD_DB_PASSWORD="x")
    assert result.returncode != 0
    assert "LEZNARD_SECRET_KEY" in result.stderr


def test_production_refuses_to_start_without_a_database_password():
    result = load_settings(LEZNARD_ENV="production", LEZNARD_SECRET_KEY="k" * 50)
    assert result.returncode != 0
    assert "LEZNARD_DB_PASSWORD" in result.stderr


def test_production_is_not_in_debug_mode_and_uses_secure_cookies():
    result = load_settings(
        LEZNARD_ENV="production", LEZNARD_SECRET_KEY="k" * 50, LEZNARD_DB_PASSWORD="x"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "True"]
