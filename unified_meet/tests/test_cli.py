import sqlite3

import pytest

from meetbridge import cli
from meetbridge.config import get_settings
from meetbridge.migrate import upgrade_to_head


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "db" / "mb.db"
    monkeypatch.setenv("MB_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("MB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MB_ENV", "test")
    monkeypatch.setenv("MB_SECRET_KEY", "x" * 40)
    get_settings.cache_clear()
    yield db
    get_settings.cache_clear()


def users(db):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("SELECT email FROM users ORDER BY email")]


def test_create_user_and_duplicate(env, monkeypatch, capsys):
    monkeypatch.setenv("MB_NEW_USER_PASSWORD", "a-strong-password")
    assert cli.main(["create-user", "--email", "Admin@Example.com", "--name", "Admin"]) == 0
    assert users(env) == ["admin@example.com"]
    assert cli.main(["create-user", "--email", "admin@example.com"]) == 1


def test_create_user_rejects_short_password(env, monkeypatch):
    monkeypatch.setenv("MB_NEW_USER_PASSWORD", "short")
    assert cli.main(["create-user", "--email", "a@example.com"]) == 1


def test_backup_and_restore_roundtrip(env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MB_NEW_USER_PASSWORD", "a-strong-password")
    cli.main(["create-user", "--email", "keep@example.com"])
    backup_file = tmp_path / "b.db"
    assert cli.main(["backup", str(backup_file)]) == 0
    cli.main(["create-user", "--email", "later@example.com"])
    assert users(env) == ["keep@example.com", "later@example.com"]

    assert cli.main(["restore", str(backup_file)]) == 3  # needs --yes
    assert cli.main(["restore", str(backup_file), "--yes"]) == 0
    assert users(env) == ["keep@example.com"]
    safety = list((tmp_path / "data" / "backups").glob("pre-restore-*.db"))
    assert len(safety) == 1 and users(safety[0]) == ["keep@example.com", "later@example.com"]


def test_default_backup_location(env, tmp_path):
    upgrade_to_head(f"sqlite:///{env}")
    assert cli.main(["backup"]) == 0
    assert len(list((tmp_path / "data" / "backups").glob("meetbridge-*.db"))) == 1


def test_restore_rejects_invalid_files(env, tmp_path):
    upgrade_to_head(f"sqlite:///{env}")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    assert cli.main(["restore", str(junk), "--yes"]) == 1
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert cli.main(["restore", str(empty), "--yes"]) == 1
    assert cli.main(["restore", str(tmp_path / "missing.db"), "--yes"]) == 1


def test_doctor_healthy_and_failing(env, monkeypatch, capsys):
    upgrade_to_head(f"sqlite:///{env}")
    assert cli.main(["doctor", "--skip-browser"]) == 0
    assert "healthy" in capsys.readouterr().out

    monkeypatch.setenv("MB_ENV", "production")
    monkeypatch.setenv("MB_SECRET_KEY", "short")
    get_settings.cache_clear()
    assert cli.main(["doctor", "--skip-browser"]) == 1
    out = capsys.readouterr().out
    assert "MB_SECRET_KEY" in out and "FAILED" in out


def test_doctor_checks_browser(env, capsys):
    upgrade_to_head(f"sqlite:///{env}")
    assert cli.main(["doctor"]) == 0
    assert "Chromium launches" in capsys.readouterr().out


def test_migrate_command(env):
    assert cli.main(["migrate"]) == 0
    assert users(env) == []


def test_production_refuses_unsafe_config(tmp_path):
    from meetbridge.config import Settings
    from meetbridge.main import create_app

    bad = Settings(_env_file=None, env="production", secret_key="short", enable_mock_provider=True,
                   public_base_url="http://example.com", database_url=f"sqlite:///{tmp_path / 'x.db'}")
    with pytest.raises(SystemExit) as exc:
        create_app(bad)
    msg = str(exc.value)
    assert "MB_SECRET_KEY" in msg and "MB_ENABLE_MOCK_PROVIDER" in msg and "https" in msg
