import sqlite3

from alembic import command
from sqlalchemy import create_engine, inspect, text

from meetbridge.migrate import alembic_config, upgrade_to_head

TABLES = {"users", "auth_sessions", "rooms", "meeting_links", "room_events", "login_attempts", "alembic_version"}


def test_migrate_from_zero(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh' / 'db.sqlite'}"
    upgrade_to_head(url)
    names = set(inspect(create_engine(url)).get_table_names())
    assert TABLES <= names


def test_upgrade_is_idempotent_and_keeps_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, display_name, password_hash, created_at) VALUES ('u1', 'a@b.c', 'A', 'h', CURRENT_TIMESTAMP)")
        )
    upgrade_to_head(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT email FROM users")).scalar_one() == "a@b.c"


def test_downgrade_and_upgrade_again(tmp_path):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    command.downgrade(alembic_config(url), "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}
    upgrade_to_head(url)
    assert TABLES <= set(inspect(create_engine(url)).get_table_names())


def test_schema_constraints(tmp_path):
    path = tmp_path / "db.sqlite"
    upgrade_to_head(f"sqlite:///{path}")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            "INSERT INTO rooms (id, owner_id, title, invite_token, bot_display_name, created_at, updated_at) "
            "VALUES ('r1', 'missing-user', 't', 'tok', 'b', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    finally:
        conn.close()
    assert raised, "rooms.owner_id must reference users.id"
