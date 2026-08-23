"""
Migration idempotency integration test.

Verifies that the schema migrations introduced this sprint
(rooms table, usage_logs.room_id, rooms.primary_output_lang,
rooms.output_langs, rooms.total_viewers, rooms.peak_viewers,
rooms.stage_config) can be applied to a pre-existing legacy database and
are safe to re-run any number of times without raising errors,
duplicating columns, or clobbering existing rows.

Why this test
-------------
- Production DBs (deployed before each migration shipped) MUST converge
  to the latest schema after a deploy, regardless of how many times the
  init runs (Streamlit reruns the module on every session, so
  ``DatabaseManager.__init__`` may execute hundreds of times against the
  same file).
- Per RL-005: the migration helpers are tested in isolation but the
  integration of "legacy DB on disk → DatabaseManager() → fully migrated
  schema with original rows preserved" is the actual production path
  we care about — covered here.
"""

from __future__ import annotations

import sqlite3

import pytest

from database import DatabaseManager, User

# ---------------------------------------------------------------------------
# Legacy schema fixtures
# ---------------------------------------------------------------------------
# Schema deliberately predates ALL sprint migrations:
#   1. ISSUE-26 rooms table
#   2. ISSUE-29 usage_logs.room_id
#   3. ISSUE-30 rooms.primary_output_lang / rooms.output_langs
#   4. ISSUE-33 rooms.total_viewers / rooms.peak_viewers
#   5. ISSUE-37 rooms.stage_config
# This is the most aggressive "old DB on disk" scenario the migration path
# can encounter — if it works here, every intermediate state works too.
_LEGACY_USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT,
    full_name TEXT,
    role TEXT DEFAULT 'user',
    is_active BOOLEAN DEFAULT 1,
    total_usage_seconds INTEGER DEFAULT 0,
    usage_limit_seconds INTEGER DEFAULT 3600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
)
"""

_LEGACY_USAGE_LOGS_DDL = """
CREATE TABLE usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    source_language TEXT,
    target_language TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
"""


# rooms 테이블은 있으나 ISSUE-30/33/37 컬럼이 없는 중간 세대 스키마.
# "이미 룸 행이 들어 있는 운영 DB" 를 재현하기 위한 별도 시드 — 위의
# _seed_legacy_db 는 rooms 테이블 자체가 없는 더 오래된 상태를 다룬다.
_LEGACY_ROOMS_DDL = """
CREATE TABLE rooms (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting'
        CHECK (status IN ('waiting','active','inactive','closed')),
    input_lang TEXT NOT NULL DEFAULT 'auto',
    output_lang TEXT NOT NULL DEFAULT 'ko',
    created_by INTEGER NOT NULL,
    operator_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP,
    timeout_minutes INTEGER NOT NULL DEFAULT 30,
    closed_at TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (operator_id) REFERENCES users(id)
)
"""


def _seed_legacy_db(db_path: str) -> dict[str, list[int]]:
    """Create a legacy DB on disk and seed a few rows. Return seeded ids."""
    conn = sqlite3.connect(db_path)
    conn.execute(_LEGACY_USERS_DDL)
    conn.execute(_LEGACY_USAGE_LOGS_DDL)

    # Use a real bcrypt hash for the user so authenticate() can succeed.
    import bcrypt

    pw_hash = bcrypt.hashpw(b"legacy-pass", bcrypt.gensalt()).decode()

    user_ids: list[int] = []
    for username in ("legacy_alice", "legacy_bob", "legacy_carol"):
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active) "
            "VALUES (?, ?, ?, ?)",
            (username, pw_hash, "user", 1),
        )
        user_ids.append(cur.lastrowid)

    log_ids: list[int] = []
    for uid in user_ids:
        cur = conn.execute(
            "INSERT INTO usage_logs "
            "(user_id, action, duration_seconds, source_language, target_language) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, "transcribe", 30, "en", "ko"),
        )
        log_ids.append(cur.lastrowid)

    conn.commit()
    conn.close()
    return {"user_ids": user_ids, "log_ids": log_ids}


def _seed_legacy_db_with_rooms(db_path: str) -> list[str]:
    """Seed users/usage_logs plus a pre-ISSUE-30 rooms table with rows.

    Returns the seeded room ids. Used by the ISSUE-37 migration test which
    must assert that *pre-existing* room rows pick up the new column's
    DEFAULT rather than NULL.
    """
    _seed_legacy_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(_LEGACY_ROOMS_DDL)
    creator = conn.execute("SELECT id FROM users LIMIT 1").fetchone()[0]

    room_ids = ["legacy-hall-a", "legacy-hall-b"]
    for room_id in room_ids:
        conn.execute(
            "INSERT INTO rooms (id, name, status, created_by, timeout_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            (room_id, f"{room_id} 홀", "active", creator, 45),
        )
    conn.commit()
    conn.close()
    return room_ids


def _column_names(db: DatabaseManager, table: str) -> set[str]:
    with db.get_connection() as conn:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row["name"] for row in cur.fetchall()}


def _table_names(db: DatabaseManager) -> set[str]:
    with db.get_connection() as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row["name"] for row in cur.fetchall()}


def _row_counts(db: DatabaseManager) -> dict[str, int]:
    counts: dict[str, int] = {}
    with db.get_connection() as conn:
        for table in ("users", "usage_logs"):
            cur = conn.execute(f"SELECT COUNT(*) AS c FROM {table}")
            counts[table] = cur.fetchone()["c"]
    return counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestMigrationFromLegacyDb:
    """One-time application of all migrations to a legacy DB."""

    def test_first_init_adds_rooms_table_and_new_columns(self, tmp_path):
        """Legacy DB → DatabaseManager() once → all new schema present."""
        db_path = str(tmp_path / "app.db")
        _seed_legacy_db(db_path)

        # Sanity: legacy DB has neither the rooms table nor room_id column.
        legacy_conn = sqlite3.connect(db_path)
        legacy_tables = {
            r[0]
            for r in legacy_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        legacy_logs_cols = {
            r[1] for r in legacy_conn.execute("PRAGMA table_info(usage_logs)")
        }
        legacy_conn.close()
        assert "rooms" not in legacy_tables
        assert "room_id" not in legacy_logs_cols

        # Run init exactly once.
        db = DatabaseManager(db_path)

        # rooms table now exists with all sprint columns.
        tables = _table_names(db)
        assert "rooms" in tables

        rooms_cols = _column_names(db, "rooms")
        for col in (
            "id",
            "name",
            "status",
            "primary_output_lang",  # ISSUE-30
            "output_langs",  # ISSUE-30
            "total_viewers",  # ISSUE-33
            "peak_viewers",  # ISSUE-33
            "stage_config",  # ISSUE-37
        ):
            assert col in rooms_cols, f"rooms missing column {col!r}"

        # usage_logs.room_id now exists (ISSUE-29).
        usage_cols = _column_names(db, "usage_logs")
        assert "room_id" in usage_cols

    def test_first_init_preserves_legacy_user_and_log_rows(self, tmp_path):
        """Legacy rows survive the migration unchanged."""
        db_path = str(tmp_path / "app.db")
        seeded = _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)
        counts = _row_counts(db)
        assert counts["users"] == len(seeded["user_ids"])
        assert counts["usage_logs"] == len(seeded["log_ids"])

        # Verify per-row data hasn't been lost.
        with db.get_connection() as conn:
            usernames = {
                r["username"] for r in conn.execute("SELECT username FROM users")
            }
        assert usernames == {"legacy_alice", "legacy_bob", "legacy_carol"}

    def test_first_init_legacy_user_can_authenticate(self, tmp_path):
        """Legacy bcrypt-hashed user still authenticates after migration."""
        db_path = str(tmp_path / "app.db")
        _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)
        user_model = User(db)
        user = user_model.authenticate("legacy_alice", "legacy-pass")
        assert user is not None
        assert user["username"] == "legacy_alice"
        # Wrong password still fails.
        assert user_model.authenticate("legacy_alice", "wrong-pass") is None


class TestMigrationIdempotency:
    """Re-running init_database() multiple times must be safe."""

    def test_init_run_multiple_times_no_errors_no_duplicate_columns(self, tmp_path):
        """init_database() called 3x — no ALTER errors, columns appear once."""
        db_path = str(tmp_path / "app.db")
        _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)
        # Re-run init twice more (3 total). The PRAGMA table_info gating in
        # the migration helpers means each call must be a silent no-op for
        # already-applied ALTERs. Without that gating SQLite raises
        # 'duplicate column name' on the second call.
        db.init_database()
        db.init_database()

        # Each new column must appear exactly ONCE in PRAGMA table_info.
        with db.get_connection() as conn:
            rooms_info = list(conn.execute("PRAGMA table_info(rooms)"))
            usage_info = list(conn.execute("PRAGMA table_info(usage_logs)"))

        rooms_col_names = [row["name"] for row in rooms_info]
        usage_col_names = [row["name"] for row in usage_info]

        for col in (
            "primary_output_lang",
            "output_langs",
            "total_viewers",
            "peak_viewers",
            "stage_config",
        ):
            assert rooms_col_names.count(col) == 1, (
                f"rooms.{col} appeared {rooms_col_names.count(col)} times "
                f"after 3 inits — migration is not idempotent"
            )
        assert usage_col_names.count("room_id") == 1

    def test_repeated_init_preserves_row_counts(self, tmp_path):
        """Running init multiple times must not delete or duplicate rows."""
        db_path = str(tmp_path / "app.db")
        seeded = _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)
        counts_before = _row_counts(db)

        # Run two more times.
        db.init_database()
        db.init_database()

        counts_after = _row_counts(db)
        assert counts_after == counts_before
        assert counts_after["users"] == len(seeded["user_ids"])
        assert counts_after["usage_logs"] == len(seeded["log_ids"])

    def test_repeated_migration_helpers_directly_idempotent(self, tmp_path):
        """Calling the per-migration helpers directly multiple times is safe.

        Mirrors how production may end up calling these helpers (e.g.
        sub-class overrides, partial init paths) without going through the
        full ``init_database()`` flow.
        """
        db_path = str(tmp_path / "app.db")
        _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)

        # Each helper must be safe to call standalone any number of times.
        for _ in range(3):
            db._migrate_add_usage_logs_room_id()
            db._migrate_add_room_output_lang_columns()
            db._migrate_add_room_viewer_metric_columns()
            db._migrate_add_room_stage_config()

        # Verify no duplicate columns slipped in.
        with db.get_connection() as conn:
            rooms_info = list(conn.execute("PRAGMA table_info(rooms)"))
            usage_info = list(conn.execute("PRAGMA table_info(usage_logs)"))

        rooms_col_names = [row["name"] for row in rooms_info]
        usage_col_names = [row["name"] for row in usage_info]

        for col in (
            "primary_output_lang",
            "output_langs",
            "total_viewers",
            "peak_viewers",
            "stage_config",
        ):
            assert rooms_col_names.count(col) == 1
        assert usage_col_names.count("room_id") == 1

    def test_repeated_init_does_not_break_authentication(self, tmp_path):
        """After 3 inits, the legacy bcrypt hash still verifies."""
        db_path = str(tmp_path / "app.db")
        _seed_legacy_db(db_path)

        db = DatabaseManager(db_path)
        db.init_database()
        db.init_database()

        user_model = User(db)
        user = user_model.authenticate("legacy_bob", "legacy-pass")
        assert user is not None
        assert user["username"] == "legacy_bob"


class TestMigrationOnFreshDatabase:
    """Idempotency on a from-scratch DB (no legacy seed) — the other path."""

    def test_fresh_init_then_reinit_is_noop(self, tmp_path):
        """Brand new DB: first init creates everything, second is a no-op."""
        db_path = str(tmp_path / "fresh.db")
        db = DatabaseManager(db_path)

        rooms_cols_first = _column_names(db, "rooms")
        usage_cols_first = _column_names(db, "usage_logs")

        # Re-init.
        db.init_database()

        rooms_cols_second = _column_names(db, "rooms")
        usage_cols_second = _column_names(db, "usage_logs")

        assert rooms_cols_first == rooms_cols_second
        assert usage_cols_first == usage_cols_second

    def test_new_columns_have_documented_defaults_on_fresh_db(self, tmp_path):
        """Newly-created room rows pick up correct defaults for new columns."""
        db_path = str(tmp_path / "fresh.db")
        db = DatabaseManager(db_path)

        # Need a user for the FK, then a room.
        user_model = User(db)
        uid = user_model.create_user(username="freshman", password="pw", role="admin")
        assert uid is not None

        from database import Room

        room_model = Room(db)
        room = room_model.create(
            room_id="fresh-room",
            name="Fresh",
            created_by=uid,
        )
        assert room is not None
        # Sprint-defined defaults
        assert room["primary_output_lang"] == "ko"
        assert room["output_langs"] == '["ko"]'
        assert room["total_viewers"] == 0
        assert room["peak_viewers"] == 0
        assert room["stage_config"] == "{}"


class TestStageConfigMigrationOnExistingRooms:
    """ISSUE-37 / TC-049 — rooms 행이 이미 있는 DB 에 stage_config 추가."""

    def test_double_init_adds_stage_config_exactly_once(self, tmp_path):
        """init_database() 2회 → duplicate column name 없이 컬럼 1개."""
        db_path = str(tmp_path / "rooms_legacy.db")
        _seed_legacy_db_with_rooms(db_path)

        # 마이그레이션 전에는 컬럼이 없다 (테스트가 공허하게 통과하지 않도록).
        legacy_conn = sqlite3.connect(db_path)
        legacy_cols = {r[1] for r in legacy_conn.execute("PRAGMA table_info(rooms)")}
        legacy_conn.close()
        assert "stage_config" not in legacy_cols

        db = DatabaseManager(db_path)  # 1st init
        db.init_database()  # 2nd init — 여기서 예외가 나면 실패

        with db.get_connection() as conn:
            rooms_info = list(conn.execute("PRAGMA table_info(rooms)"))
        names = [row["name"] for row in rooms_info]
        assert names.count("stage_config") == 1

        col = next(row for row in rooms_info if row["name"] == "stage_config")
        assert col["type"].upper() == "TEXT"
        assert col["notnull"] == 1

    def test_existing_room_rows_get_empty_object_default(self, tmp_path):
        """기존 룸 행의 stage_config 는 NULL 이 아니라 '{}' 이다."""
        db_path = str(tmp_path / "rooms_legacy.db")
        room_ids = _seed_legacy_db_with_rooms(db_path)

        db = DatabaseManager(db_path)
        db.init_database()

        with db.get_connection() as conn:
            rows = {
                r["id"]: dict(r)
                for r in conn.execute(
                    "SELECT id, name, status, timeout_minutes, stage_config FROM rooms"
                )
            }

        assert set(rows) == set(room_ids)
        for room_id in room_ids:
            row = rows[room_id]
            assert row["stage_config"] == "{}"
            # 기존 컬럼 값이 마이그레이션으로 훼손되지 않았다.
            assert row["name"] == f"{room_id} 홀"
            assert row["status"] == "active"
            assert row["timeout_minutes"] == 45

    def test_existing_rows_normalise_to_default_config(self, tmp_path):
        """'{}' 는 Room.get_stage_config() 를 통해 기본 설정으로 읽힌다."""
        db_path = str(tmp_path / "rooms_legacy.db")
        room_ids = _seed_legacy_db_with_rooms(db_path)

        from database import Room
        from stage_config import DEFAULT_STAGE_CONFIG

        db = DatabaseManager(db_path)
        room_model = Room(db)
        assert room_model.get_stage_config(room_ids[0]) == DEFAULT_STAGE_CONFIG

    def test_stored_config_survives_repeated_init(self, tmp_path):
        """마이그레이션 재실행이 이미 저장된 설정을 덮어쓰지 않는다."""
        db_path = str(tmp_path / "rooms_legacy.db")
        room_ids = _seed_legacy_db_with_rooms(db_path)

        from database import Room

        db = DatabaseManager(db_path)
        room_model = Room(db)
        config = {
            "event_title": "2026 개발자 콘퍼런스",
            "event_subtitle": "A홀 기조연설",
            "caption_ratio": "1/3",
            "logo_groups": [
                {"label": "주최", "assets": ["host-1.png"]},
                {"label": "주관", "assets": []},
                {"label": "후원", "assets": []},
            ],
        }
        assert room_model.update_stage_config(room_ids[0], config) is True

        db.init_database()
        db.init_database()

        assert room_model.get_stage_config(room_ids[0]) == config


@pytest.mark.parametrize(
    "extra_init_calls",
    [0, 1, 2, 5],
    ids=["init=1x", "init=2x", "init=3x", "init=6x"],
)
def test_init_database_idempotent_parametrised(tmp_path, extra_init_calls):
    """Parametrised sanity check — 1 to 6 inits all converge to same schema.

    Independently asserts the invariants again across a range of repeat
    counts so a regression that only triggers on a specific N (e.g. an
    accidental ``while`` loop in a helper) is still caught.
    """
    db_path = str(tmp_path / f"param_{extra_init_calls}.db")
    seeded = _seed_legacy_db(db_path)
    db = DatabaseManager(db_path)
    for _ in range(extra_init_calls):
        db.init_database()

    rooms_cols = _column_names(db, "rooms")
    usage_cols = _column_names(db, "usage_logs")
    counts = _row_counts(db)

    expected_rooms = {
        "id",
        "name",
        "status",
        "primary_output_lang",
        "output_langs",
        "total_viewers",
        "peak_viewers",
        "stage_config",
    }
    assert expected_rooms.issubset(rooms_cols)
    assert "room_id" in usage_cols
    assert counts["users"] == len(seeded["user_ids"])
    assert counts["usage_logs"] == len(seeded["log_ids"])
