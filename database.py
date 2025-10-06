"""
데이터베이스 연결 및 모델 관리 모듈
사용자 관리 시스템을 위한 SQLite 기반 데이터베이스 레이어
"""

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any


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
        try:
            yield conn
        finally:
            conn.close()

    def init_database(self):
        """데이터베이스 스키마 초기화"""
        with self.get_connection() as conn:
            # users 테이블 생성
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
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

            conn.commit()


class PasswordManager:
    """비밀번호 해싱 및 검증 관리"""

    @staticmethod
    def generate_salt() -> str:
        """랜덤 salt 생성"""
        return secrets.token_hex(32)

    @staticmethod
    def hash_password(password: str, salt: str) -> str:
        """PBKDF2-SHA256으로 비밀번호 해싱"""
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000,  # 반복 횟수
        ).hex()

    @staticmethod
    def verify_password(password: str, salt: str, password_hash: str) -> bool:
        """비밀번호 검증"""
        return PasswordManager.hash_password(password, salt) == password_hash


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
        """새 사용자 생성"""
        salt = PasswordManager.generate_salt()
        password_hash = PasswordManager.hash_password(password, salt)

        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, salt, email, full_name,
                        role, usage_limit_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        username,
                        password_hash,
                        salt,
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
        """사용자 인증"""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, username, password_hash, salt, email, full_name,
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

            # 비밀번호 검증
            if not PasswordManager.verify_password(
                password, user_dict["salt"], user_dict["password_hash"]
            ):
                return None

            # 마지막 로그인 시간 업데이트
            self.update_last_login(user_dict["id"])

            # 비밀번호 관련 정보는 제거
            del user_dict["password_hash"]
            del user_dict["salt"]

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
        """비밀번호 변경"""
        salt = PasswordManager.generate_salt()
        password_hash = PasswordManager.hash_password(new_password, salt)

        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE users SET password_hash = ?, salt = ? WHERE id = ?
            """,
                (password_hash, salt, user_id),
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
                SELECT ul.id, ul.user_id, u.username, ul.action, ul.duration_seconds,
                       ul.source_language, ul.target_language, ul.created_at, ul.metadata
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


def get_db_manager() -> DatabaseManager:
    """데이터베이스 매니저 싱글톤 인스턴스 반환"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
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
