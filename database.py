"""
데이터베이스 연결 및 모델 관리 모듈
사용자 관리 시스템을 위한 SQLite 기반 데이터베이스 레이어
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import bcrypt


class InvalidRoomTransition(ValueError):
    """Raised when a room status transition violates the lifecycle diagram.

    Diagram (ISSUE-26):
        waiting  → active | closed
        active   → inactive | closed
        inactive → active | closed
        closed   → (terminal)
    """


# Allowed status transitions for rooms (ISSUE-26).
# Source: issues.md ISSUE-26 state-transition diagram.
_ROOM_VALID_STATUSES: tuple[str, ...] = ("waiting", "active", "inactive", "closed")
_ROOM_TRANSITIONS: dict[str, set[str]] = {
    "waiting": {"active", "closed"},
    "active": {"inactive", "closed"},
    "inactive": {"active", "closed"},
    "closed": set(),  # terminal
}


class DatabaseManager:
    """데이터베이스 연결 및 스키마 관리"""

    def __init__(self, db_path: str = "data/app.db"):
        self.db_path = db_path
        # data 디렉토리가 없으면 생성
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.init_database()

    @contextmanager
    def get_connection(self):
        """컨텍스트 매니저로 DB 연결 관리"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 딕셔너리 형태로 결과 반환
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    def init_database(self):
        """데이터베이스 스키마 초기화"""
        with self.get_connection() as conn:
            # users 테이블 생성 (salt/hash_type 제거 — ISSUE-20)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
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
            )

            # usage_logs 테이블 생성
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_logs (
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
            )

            # usage_logs 인덱스 생성 (조회 성능 개선)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_logs_user_id
                ON usage_logs(user_id)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at
                ON usage_logs(created_at)
            """
            )

            # rooms 테이블 생성 (ISSUE-26)
            # 룸 라이프사이클을 DB에 영속화한다. 상태 전이는 application
            # 레이어 (Room.transition_status) 에서 강제하며, DB 레벨의
            # CHECK 제약은 검증된 application path 우회를 막기 위한
            # 방어선 역할이다.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
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
            )
            # status 필터 (cleanup_stale_rooms, list_by_status, hydrate)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rooms_status
                ON rooms(status)
            """
            )

            # Enable WAL journal mode for better concurrent access (ISSUE-23)
            # WAL is persistent per DB file — only needs to be set once.
            conn.execute("PRAGMA journal_mode=WAL")

            conn.commit()

        # Migrate existing databases: drop legacy columns (ISSUE-20)
        self._migrate_drop_legacy_columns()

    def _migrate_drop_legacy_columns(self):
        """Drop unused salt and hash_type columns from users table.

        Python 3.11 bundles SQLite >= 3.35 which supports
        ALTER TABLE ... DROP COLUMN.
        """
        with self.get_connection() as conn:
            # Check which columns currently exist
            cursor = conn.execute("PRAGMA table_info(users)")
            existing_columns = {row["name"] for row in cursor.fetchall()}

            for col in ("salt", "hash_type"):
                if col in existing_columns:
                    conn.execute(f"ALTER TABLE users DROP COLUMN {col}")
                    print(f"[Migration] Dropped legacy column: users.{col}")

            conn.commit()


class PasswordManager:
    """비밀번호 해싱 및 검증 관리 (bcrypt)"""

    @staticmethod
    def hash_password(password: str) -> str:
        """bcrypt로 비밀번호 해싱"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """bcrypt 비밀번호 검증"""
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


class User:
    """사용자 모델 및 CRUD 작업"""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def create_user(
        self,
        username: str,
        password: str,
        email: str | None = None,
        full_name: str | None = None,
        role: str = "user",
        usage_limit_seconds: int = 3600,
    ) -> int | None:
        """새 사용자 생성 (bcrypt 사용)"""
        password_hash = PasswordManager.hash_password(password)

        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, email,
                        full_name, role, usage_limit_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        username,
                        password_hash,
                        email,
                        full_name,
                        role,
                        usage_limit_seconds,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            # 사용자명 중복 등의 오류
            return None

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        """사용자 인증 (bcrypt)"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, password_hash, email, full_name,
                       role, is_active, total_usage_seconds, usage_limit_seconds,
                       created_at, last_login
                FROM users WHERE username = ? AND is_active = 1
            """,
                (username,),
            )

            user_row = cursor.fetchone()
            if not user_row:
                return None

            user_dict = dict(user_row)

            # bcrypt 비밀번호 검증
            if not PasswordManager.verify_password(
                password, user_dict["password_hash"]
            ):
                return None

            # 마지막 로그인 시간 업데이트
            self.update_last_login(user_dict["id"])

            # 비밀번호 해시는 제거
            del user_dict["password_hash"]

            return user_dict

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        """ID로 사용자 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, email, full_name, role, is_active,
                       total_usage_seconds, usage_limit_seconds, created_at, last_login
                FROM users WHERE id = ?
            """,
                (user_id,),
            )

            user_row = cursor.fetchone()
            return dict(user_row) if user_row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """사용자명으로 사용자 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, email, full_name, role, is_active,
                       total_usage_seconds, usage_limit_seconds, created_at, last_login
                FROM users WHERE username = ?
            """,
                (username,),
            )

            user_row = cursor.fetchone()
            return dict(user_row) if user_row else None

    def get_all_users(self) -> list[dict[str, Any]]:
        """모든 사용자 목록 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, email, full_name, role, is_active,
                       total_usage_seconds, usage_limit_seconds, created_at, last_login
                FROM users ORDER BY created_at DESC
            """
            )

            return [dict(row) for row in cursor.fetchall()]

    def update_user(
        self,
        user_id: int,
        email: str | None = None,
        full_name: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        usage_limit_seconds: int | None = None,
    ) -> bool:
        """사용자 정보 업데이트"""
        updates = []
        params = []

        if email is not None:
            updates.append("email = ?")
            params.append(email)
        if full_name is not None:
            updates.append("full_name = ?")
            params.append(full_name)
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(is_active)
        if usage_limit_seconds is not None:
            updates.append("usage_limit_seconds = ?")
            params.append(usage_limit_seconds)

        if not updates:
            return False

        params.append(user_id)

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE users SET {", ".join(updates)} WHERE id = ?
            """,
                params,
            )
            conn.commit()

            return cursor.rowcount > 0

    def add_usage(self, user_id: int, duration_seconds: int) -> bool:
        """사용자 사용량 추가"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE users SET total_usage_seconds = total_usage_seconds + ?
                WHERE id = ?
            """,
                (duration_seconds, user_id),
            )
            conn.commit()

            return cursor.rowcount > 0

    def get_remaining_seconds(self, user_id: int) -> int | None:
        """사용자의 남은 사용 가능 시간 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT usage_limit_seconds - total_usage_seconds as remaining
                FROM users WHERE id = ?
            """,
                (user_id,),
            )

            result = cursor.fetchone()
            return result["remaining"] if result else None

    def update_last_login(self, user_id: int):
        """마지막 로그인 시간 업데이트"""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?
            """,
                (user_id,),
            )
            conn.commit()

    def change_password(self, user_id: int, new_password: str) -> bool:
        """비밀번호 변경 (bcrypt 사용)"""
        password_hash = PasswordManager.hash_password(new_password)

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE users SET password_hash = ? WHERE id = ?
            """,
                (password_hash, user_id),
            )
            conn.commit()

            return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        """사용자 삭제"""
        with self.db.get_connection() as conn:
            # 사용자의 사용량 로그도 함께 삭제
            conn.execute("DELETE FROM usage_logs WHERE user_id = ?", (user_id,))
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()

            return cursor.rowcount > 0


class UsageLog:
    """사용량 로그 모델 및 작업"""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def record_usage(
        self,
        user_id: int,
        action: str,
        duration_seconds: int,
        source_language: str | None = None,
        target_language: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        """사용량 기록"""
        metadata_json = json.dumps(metadata) if metadata else None

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO usage_logs (
                    user_id, action, duration_seconds, source_language,
                    target_language, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    action,
                    duration_seconds,
                    source_language,
                    target_language,
                    metadata_json,
                ),
            )
            conn.commit()

            return cursor.lastrowid

    def get_user_logs(
        self, user_id: int, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """특정 사용자의 사용량 로그 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, action, duration_seconds, source_language,
                       target_language, created_at, metadata
                FROM usage_logs
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """,
                (user_id, limit, offset),
            )

            logs = []
            for row in cursor.fetchall():
                log_dict = dict(row)
                if log_dict["metadata"]:
                    log_dict["metadata"] = json.loads(log_dict["metadata"])
                logs.append(log_dict)

            return logs

    def get_all_user_logs(self, user_id: int) -> list[dict[str, Any]]:
        """특정 사용자의 모든 사용량 로그 조회 (CSV 다운로드용)"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, action, duration_seconds, source_language,
                       target_language, created_at, metadata
                FROM usage_logs
                WHERE user_id = ?
                ORDER BY created_at DESC
            """,
                (user_id,),
            )

            logs = []
            for row in cursor.fetchall():
                log_dict = dict(row)
                if log_dict["metadata"]:
                    log_dict["metadata"] = json.loads(log_dict["metadata"])
                logs.append(log_dict)

            return logs

    def get_all_logs(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """모든 사용량 로그 조회"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ul.id, ul.user_id, u.username,
                       ul.action, ul.duration_seconds,
                       ul.source_language, ul.target_language,
                       ul.created_at, ul.metadata
                FROM usage_logs ul
                JOIN users u ON ul.user_id = u.id
                ORDER BY ul.created_at DESC
                LIMIT ? OFFSET ?
            """,
                (limit, offset),
            )

            logs = []
            for row in cursor.fetchall():
                log_dict = dict(row)
                if log_dict["metadata"]:
                    log_dict["metadata"] = json.loads(log_dict["metadata"])
                logs.append(log_dict)

            return logs

    def get_usage_stats(
        self,
        user_id: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """사용량 통계 조회"""
        where_conditions = []
        params = []

        if user_id:
            where_conditions.append("user_id = ?")
            params.append(user_id)

        if start_date:
            where_conditions.append("created_at >= ?")
            params.append(start_date.isoformat())

        if end_date:
            where_conditions.append("created_at <= ?")
            params.append(end_date.isoformat())

        where_clause = (
            "WHERE " + " AND ".join(where_conditions) if where_conditions else ""
        )

        with self.db.get_connection() as conn:
            # 총 사용량과 로그 수
            cursor = conn.execute(
                f"""
                SELECT
                    COUNT(*) as total_sessions,
                    SUM(duration_seconds) as total_duration,
                    AVG(duration_seconds) as avg_duration,
                    MIN(created_at) as first_usage,
                    MAX(created_at) as last_usage
                FROM usage_logs {where_clause}
            """,
                params,
            )

            stats = dict(cursor.fetchone())

            # 언어별 통계
            cursor = conn.execute(
                f"""
                SELECT
                    source_language,
                    target_language,
                    COUNT(*) as session_count,
                    SUM(duration_seconds) as total_duration
                FROM usage_logs {where_clause}
                GROUP BY source_language, target_language
                ORDER BY total_duration DESC
            """,
                params,
            )

            stats["language_stats"] = [dict(row) for row in cursor.fetchall()]

            return stats


class Room:
    """rooms 테이블에 대한 CRUD + 상태 전이 (ISSUE-26).

    - 상태 전이는 _ROOM_TRANSITIONS 다이어그램에 따라 application
      레이어에서 강제한다 (CHECK 제약은 fallback 방어선).
    - cleanup_stale_rooms() 는 timeout_minutes 를 초과한 active/inactive
      룸을 closed 처리한다 (좀비 룸 정리).
    - 모든 메서드는 외부에서 SQLite tmp_path 로 격리 가능하도록
      DatabaseManager 인스턴스 주입을 받는다.
    """

    # Statuses that should be re-loaded into RoomManager on server restart.
    # Closed rooms are terminal and stay only in the audit trail.
    HYDRATABLE_STATUSES: tuple[str, ...] = ("waiting", "active", "inactive")

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def create(
        self,
        room_id: str,
        name: str,
        created_by: int,
        input_lang: str = "auto",
        output_lang: str = "ko",
        timeout_minutes: int = 30,
        operator_id: int | None = None,
    ) -> dict[str, Any] | None:
        """룸 행을 생성하고 created row 를 dict 로 반환한다.

        Returns None on PK collision (mirrors User.create_user semantics).
        Raises sqlite3.IntegrityError when FK references are invalid —
        callers that pass user-provided created_by/operator_id should
        handle this explicitly so that the failure is not silent.
        """
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO rooms (
                        id, name, status, input_lang, output_lang,
                        created_by, operator_id, last_activity,
                        timeout_minutes
                    ) VALUES (?, ?, 'waiting', ?, ?, ?, ?,
                              CURRENT_TIMESTAMP, ?)
                    """,
                    (
                        room_id,
                        name,
                        input_lang,
                        output_lang,
                        created_by,
                        operator_id,
                        timeout_minutes,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as e:
            # PK 충돌은 None, FK 위반은 그대로 전파
            msg = str(e).lower()
            if "unique" in msg or "primary key" in msg:
                return None
            raise

        return self.get_by_id(room_id)

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_all(self) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.execute("SELECT * FROM rooms ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def list_by_status(self, statuses: list[str]) -> list[dict[str, Any]]:
        if not statuses:
            return []
        # Validate to prevent unknown status values from confusing callers.
        unknown = set(statuses) - set(_ROOM_VALID_STATUSES)
        if unknown:
            raise ValueError(f"Unknown status(es): {sorted(unknown)}")
        placeholders = ",".join("?" for _ in statuses)
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM rooms WHERE status IN ({placeholders}) "
                f"ORDER BY created_at DESC",
                statuses,
            )
            return [dict(r) for r in cursor.fetchall()]

    def assign_operator(self, room_id: str, operator_id: int) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE rooms SET operator_id = ? WHERE id = ?",
                (operator_id, room_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_by_operator(self, operator_id: int) -> list[dict[str, Any]]:
        """Return non-closed rooms assigned to the given operator (ISSUE-27).

        - Filters out closed rooms (terminal state, not actionable from the
          operator UI's start/stop buttons).
        - Ordered by created_at DESC for deterministic dropdown ordering.
        - Read-only helper; admin assignment logic lives in ISSUE-29.
        """
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM rooms WHERE operator_id = ? "
                "AND status != 'closed' "
                "ORDER BY created_at DESC",
                (operator_id,),
            )
            return [dict(r) for r in cursor.fetchall()]

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def transition_status(self, room_id: str, target_status: str) -> bool:
        """Transition the room to target_status.

        Returns False if the room does not exist.
        Raises InvalidRoomTransition when the target status is unknown
        OR the source→target edge is not in _ROOM_TRANSITIONS.

        Side effects on success:
          - status updated
          - last_activity = CURRENT_TIMESTAMP
          - closed_at = CURRENT_TIMESTAMP iff target_status == 'closed'
        """
        if target_status not in _ROOM_VALID_STATUSES:
            raise InvalidRoomTransition(f"Unknown room status: {target_status!r}")

        room = self.get_by_id(room_id)
        if room is None:
            return False

        current = room["status"]
        if target_status not in _ROOM_TRANSITIONS.get(current, set()):
            raise InvalidRoomTransition(
                f"Invalid transition: {current} → {target_status}"
            )

        with self.db.get_connection() as conn:
            if target_status == "closed":
                conn.execute(
                    "UPDATE rooms SET status = ?, "
                    "last_activity = CURRENT_TIMESTAMP, "
                    "closed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (target_status, room_id),
                )
            else:
                conn.execute(
                    "UPDATE rooms SET status = ?, "
                    "last_activity = CURRENT_TIMESTAMP WHERE id = ?",
                    (target_status, room_id),
                )
            conn.commit()
        return True

    def touch(self, room_id: str) -> bool:
        """Update last_activity = CURRENT_TIMESTAMP. No status change."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE rooms SET last_activity = CURRENT_TIMESTAMP WHERE id = ?",
                (room_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup_stale_rooms(self, now: datetime | None = None) -> list[str]:
        """Close active/inactive rooms whose last_activity is older than
        their per-row timeout_minutes.

        Returns the list of room ids that were closed.

        The check is performed in Python (not a single UPDATE) so we can:
          - Apply per-row timeout_minutes accurately
          - Run the same transition_status path used by the rest of the
            code, keeping closed_at + audit semantics consistent
          - Be unit-testable without monkey-patching SQLite NOW().
        """
        now = now or datetime.utcnow()
        closed_ids: list[str] = []
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, last_activity, timeout_minutes, status "
                "FROM rooms WHERE status IN ('active', 'inactive')"
            )
            candidates = [dict(r) for r in cursor.fetchall()]

        for room in candidates:
            last_activity = room.get("last_activity")
            if not last_activity:
                # No activity recorded yet — leave alone, will be cleaned
                # up on the next touch/transition.
                continue
            if isinstance(last_activity, str):
                try:
                    last_dt = datetime.strptime(last_activity, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    # Unknown timestamp shape; skip rather than crash.
                    continue
            elif isinstance(last_activity, datetime):
                last_dt = last_activity
            else:
                continue

            timeout = timedelta(minutes=room["timeout_minutes"])
            if now - last_dt >= timeout:
                try:
                    self.transition_status(room["id"], "closed")
                    closed_ids.append(room["id"])
                except InvalidRoomTransition:
                    # Race: another caller closed it first; skip.
                    continue
        return closed_ids


def init_admin_from_env(db_manager: DatabaseManager) -> bool:
    """환경변수에서 초기 관리자 계정 생성"""
    admin_username = os.getenv("ADMIN_USERNAME")
    admin_password = os.getenv("ADMIN_PASSWORD")
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_full_name = os.getenv("ADMIN_FULL_NAME", "Administrator")

    if not admin_username or not admin_password:
        return False

    user_model = User(db_manager)

    # 기존 관리자 계정이 있는지 확인
    existing_admin = user_model.get_user_by_username(admin_username)
    if existing_admin:
        return True  # 이미 존재함

    # 관리자 계정 생성
    user_id = user_model.create_user(
        username=admin_username,
        password=admin_password,
        email=admin_email,
        full_name=admin_full_name,
        role="admin",
        usage_limit_seconds=0,  # 관리자는 무제한
    )

    return user_id is not None


# 전역 데이터베이스 인스턴스들
_db_manager = None
_user_model = None
_usage_log_model = None
_room_model = None


def get_db_manager() -> DatabaseManager:
    """데이터베이스 매니저 싱글톤 인스턴스 반환"""
    global _db_manager
    if _db_manager is None:
        db_path = os.getenv("DB_PATH", "data/app.db")
        _db_manager = DatabaseManager(db_path)
        # 초기 관리자 계정 생성 시도
        init_admin_from_env(_db_manager)
    return _db_manager


def get_user_model() -> User:
    """User 모델 인스턴스 반환"""
    global _user_model
    if _user_model is None:
        _user_model = User(get_db_manager())
    return _user_model


def get_usage_log_model() -> UsageLog:
    """UsageLog 모델 인스턴스 반환"""
    global _usage_log_model
    if _usage_log_model is None:
        _usage_log_model = UsageLog(get_db_manager())
    return _usage_log_model


def get_room_model() -> "Room":
    """Room 모델 싱글톤 (ISSUE-26)."""
    global _room_model
    if _room_model is None:
        _room_model = Room(get_db_manager())
    return _room_model
