"""
database.py 단위 테스트
DatabaseManager, PasswordManager, User, UsageLog 클래스 테스트
"""

import os
import sqlite3
from unittest.mock import patch

import pytest

from database import (
    DatabaseManager,
    PasswordManager,
    UsageLog,
    User,
    init_admin_from_env,
)


@pytest.fixture
def db_path(tmp_path):
    """임시 데이터베이스 경로"""
    return str(tmp_path / "test.db")


@pytest.fixture
def db_manager(db_path):
    """테스트용 DatabaseManager"""
    return DatabaseManager(db_path)


@pytest.fixture
def user_model(db_manager):
    """테스트용 User 모델"""
    return User(db_manager)


@pytest.fixture
def usage_log_model(db_manager):
    """테스트용 UsageLog 모델"""
    return UsageLog(db_manager)


@pytest.fixture
def sample_user(user_model):
    """테스트용 샘플 사용자 생성"""
    user_id = user_model.create_user(
        username="testuser",
        password="testpass123",
        email="test@example.com",
        full_name="Test User",
        role="user",
        usage_limit_seconds=3600,
    )
    return user_id


# === DatabaseManager Tests ===


class TestDatabaseManager:
    def test_init_creates_directory(self, tmp_path):
        """DB 경로의 디렉토리가 없으면 자동 생성"""
        db_path = str(tmp_path / "subdir" / "test.db")
        DatabaseManager(db_path)
        assert os.path.isdir(str(tmp_path / "subdir"))

    def test_init_creates_tables(self, db_manager):
        """초기화 시 users, usage_logs 테이블 생성"""
        with db_manager.get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}

        assert "users" in tables
        assert "usage_logs" in tables

    def test_get_connection_context_manager(self, db_manager):
        """get_connection이 컨텍스트 매니저로 동작"""
        with db_manager.get_connection() as conn:
            cursor = conn.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1


# === PasswordManager Tests ===


class TestPasswordManager:
    def test_hash_password_returns_bcrypt_hash(self):
        """비밀번호 해싱이 bcrypt 형식 반환"""
        hashed = PasswordManager.hash_password("mypassword")
        assert hashed.startswith("$2b$")
        assert hashed != "mypassword"

    def test_hash_password_different_salts(self):
        """같은 비밀번호라도 다른 해시 생성"""
        hash1 = PasswordManager.hash_password("same_password")
        hash2 = PasswordManager.hash_password("same_password")
        assert hash1 != hash2

    def test_verify_password_correct(self):
        """올바른 비밀번호 검증 성공"""
        password = "correct_password"
        hashed = PasswordManager.hash_password(password)
        assert PasswordManager.verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """잘못된 비밀번호 검증 실패"""
        hashed = PasswordManager.hash_password("correct_password")
        assert PasswordManager.verify_password("wrong_password", hashed) is False


# === User Model Tests ===


class TestUser:
    def test_create_user(self, user_model):
        """사용자 생성 성공"""
        user_id = user_model.create_user(
            username="newuser",
            password="password123",
            email="new@example.com",
            full_name="New User",
        )
        assert user_id is not None
        assert user_id > 0

    def test_create_user_duplicate_username(self, user_model, sample_user):
        """중복 사용자명 생성 실패"""
        result = user_model.create_user(
            username="testuser",
            password="anotherpass",
        )
        assert result is None

    def test_authenticate_success(self, user_model, sample_user):
        """올바른 인증 성공"""
        user = user_model.authenticate("testuser", "testpass123")
        assert user is not None
        assert user["username"] == "testuser"
        assert user["email"] == "test@example.com"
        assert "password_hash" not in user

    def test_authenticate_wrong_password(self, user_model, sample_user):
        """잘못된 비밀번호로 인증 실패"""
        user = user_model.authenticate("testuser", "wrongpass")
        assert user is None

    def test_authenticate_nonexistent_user(self, user_model):
        """존재하지 않는 사용자 인증 실패"""
        user = user_model.authenticate("nobody", "password")
        assert user is None

    def test_authenticate_inactive_user(self, user_model, sample_user):
        """비활성 사용자 인증 실패"""
        user_model.update_user(sample_user, is_active=False)
        user = user_model.authenticate("testuser", "testpass123")
        assert user is None

    def test_get_user_by_id(self, user_model, sample_user):
        """ID로 사용자 조회"""
        user = user_model.get_user_by_id(sample_user)
        assert user is not None
        assert user["username"] == "testuser"

    def test_get_user_by_id_nonexistent(self, user_model):
        """존재하지 않는 ID 조회 시 None 반환"""
        user = user_model.get_user_by_id(9999)
        assert user is None

    def test_get_user_by_username(self, user_model, sample_user):
        """사용자명으로 조회"""
        user = user_model.get_user_by_username("testuser")
        assert user is not None
        assert user["id"] == sample_user

    def test_get_user_by_username_nonexistent(self, user_model):
        """존재하지 않는 사용자명 조회 시 None 반환"""
        user = user_model.get_user_by_username("nobody")
        assert user is None

    def test_get_all_users(self, user_model, sample_user):
        """모든 사용자 목록 조회"""
        user_model.create_user(username="user2", password="pass2")
        users = user_model.get_all_users()
        assert len(users) == 2

    def test_update_user(self, user_model, sample_user):
        """사용자 정보 업데이트"""
        result = user_model.update_user(
            sample_user, email="updated@example.com", role="admin"
        )
        assert result is True

        user = user_model.get_user_by_id(sample_user)
        assert user["email"] == "updated@example.com"
        assert user["role"] == "admin"

    def test_update_user_no_fields(self, user_model, sample_user):
        """업데이트 필드 없으면 False 반환"""
        result = user_model.update_user(sample_user)
        assert result is False

    def test_add_usage(self, user_model, sample_user):
        """사용량 추가"""
        result = user_model.add_usage(sample_user, 100)
        assert result is True

        user = user_model.get_user_by_id(sample_user)
        assert user["total_usage_seconds"] == 100

    def test_add_usage_cumulative(self, user_model, sample_user):
        """사용량 누적"""
        user_model.add_usage(sample_user, 100)
        user_model.add_usage(sample_user, 200)

        user = user_model.get_user_by_id(sample_user)
        assert user["total_usage_seconds"] == 300

    def test_get_remaining_seconds(self, user_model, sample_user):
        """남은 시간 조회"""
        remaining = user_model.get_remaining_seconds(sample_user)
        assert remaining == 3600

        user_model.add_usage(sample_user, 1000)
        remaining = user_model.get_remaining_seconds(sample_user)
        assert remaining == 2600

    def test_get_remaining_seconds_nonexistent(self, user_model):
        """존재하지 않는 사용자의 남은 시간 조회"""
        remaining = user_model.get_remaining_seconds(9999)
        assert remaining is None

    def test_change_password(self, user_model, sample_user):
        """비밀번호 변경 후 인증"""
        result = user_model.change_password(sample_user, "newpassword456")
        assert result is True

        # 새 비밀번호로 인증 성공
        user = user_model.authenticate("testuser", "newpassword456")
        assert user is not None

        # 이전 비밀번호로 인증 실패
        user = user_model.authenticate("testuser", "testpass123")
        assert user is None

    def test_delete_user(self, user_model, sample_user):
        """사용자 삭제"""
        result = user_model.delete_user(sample_user)
        assert result is True

        user = user_model.get_user_by_id(sample_user)
        assert user is None

    def test_delete_user_cascades_logs(self, user_model, db_manager, sample_user):
        """사용자 삭제 시 사용량 로그도 삭제"""
        usage_log = UsageLog(db_manager)
        usage_log.record_usage(sample_user, "transcribe", 30)

        user_model.delete_user(sample_user)

        logs = usage_log.get_user_logs(sample_user)
        assert len(logs) == 0

    def test_update_last_login(self, user_model, sample_user):
        """마지막 로그인 시간 업데이트"""
        user_model.update_last_login(sample_user)
        user = user_model.get_user_by_id(sample_user)
        assert user["last_login"] is not None


# === UsageLog Tests ===


class TestUsageLog:
    def test_record_usage(self, usage_log_model, sample_user):
        """사용량 기록"""
        log_id = usage_log_model.record_usage(
            user_id=sample_user,
            action="transcribe",
            duration_seconds=30,
            source_language="en",
            target_language="ko",
        )
        assert log_id is not None

    def test_record_usage_with_metadata(self, usage_log_model, sample_user):
        """메타데이터 포함 사용량 기록"""
        metadata = {"transcript_length": 100, "used_llm": True}
        log_id = usage_log_model.record_usage(
            user_id=sample_user,
            action="transcribe",
            duration_seconds=60,
            metadata=metadata,
        )
        assert log_id is not None

        logs = usage_log_model.get_user_logs(sample_user)
        assert len(logs) == 1
        assert logs[0]["metadata"]["used_llm"] is True

    def test_get_user_logs(self, usage_log_model, sample_user):
        """사용자별 로그 조회"""
        for i in range(5):
            usage_log_model.record_usage(sample_user, "transcribe", 10 + i)

        logs = usage_log_model.get_user_logs(sample_user, limit=3)
        assert len(logs) == 3

    def test_get_user_logs_pagination(self, usage_log_model, sample_user):
        """로그 페이지네이션"""
        for i in range(5):
            usage_log_model.record_usage(sample_user, "transcribe", 10)

        page1 = usage_log_model.get_user_logs(sample_user, limit=2, offset=0)
        page2 = usage_log_model.get_user_logs(sample_user, limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]

    def test_get_all_user_logs(self, usage_log_model, sample_user):
        """특정 사용자의 모든 로그 조회"""
        for i in range(3):
            usage_log_model.record_usage(sample_user, "transcribe", 10)

        logs = usage_log_model.get_all_user_logs(sample_user)
        assert len(logs) == 3

    def test_get_all_logs_with_username(self, usage_log_model, user_model, sample_user):
        """모든 로그 조회 시 username 포함"""
        usage_log_model.record_usage(sample_user, "transcribe", 30)

        logs = usage_log_model.get_all_logs()
        assert len(logs) == 1
        assert logs[0]["username"] == "testuser"

    def test_get_usage_stats(self, usage_log_model, sample_user):
        """사용량 통계 조회"""
        usage_log_model.record_usage(
            sample_user, "transcribe", 30, source_language="en", target_language="ko"
        )
        usage_log_model.record_usage(
            sample_user, "transcribe", 60, source_language="ko", target_language="en"
        )

        stats = usage_log_model.get_usage_stats(user_id=sample_user)
        assert stats["total_sessions"] == 2
        assert stats["total_duration"] == 90
        assert len(stats["language_stats"]) == 2

    def test_get_usage_stats_empty(self, usage_log_model):
        """로그 없을 때 통계"""
        stats = usage_log_model.get_usage_stats()
        assert stats["total_sessions"] == 0
        assert stats["total_duration"] is None


# === init_admin_from_env Tests ===


class TestInitAdminFromEnv:
    def test_creates_admin_from_env(self, db_manager):
        """환경변수에서 관리자 계정 생성"""
        with patch.dict(
            os.environ,
            {
                "ADMIN_USERNAME": "admin",
                "ADMIN_PASSWORD": "adminpass123",
                "ADMIN_EMAIL": "admin@example.com",
            },
        ):
            result = init_admin_from_env(db_manager)
            assert result is True

            user_model = User(db_manager)
            admin = user_model.get_user_by_username("admin")
            assert admin is not None
            assert admin["role"] == "admin"

    def test_skips_if_no_env_vars(self, db_manager):
        """환경변수 없으면 False 반환"""
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the env vars are not set
            os.environ.pop("ADMIN_USERNAME", None)
            os.environ.pop("ADMIN_PASSWORD", None)
            result = init_admin_from_env(db_manager)
            assert result is False

    def test_skips_if_admin_already_exists(self, db_manager):
        """관리자 이미 존재하면 True 반환 (재생성 안함)"""
        user_model = User(db_manager)
        user_model.create_user(username="admin", password="existing", role="admin")

        with patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "newpass"},
        ):
            result = init_admin_from_env(db_manager)
            assert result is True


# === Foreign Key Enforcement Tests (ISSUE-12) ===


class TestForeignKeyEnforcement:
    """PRAGMA foreign_keys = ON 적용 검증"""

    def test_insert_usage_log_with_nonexistent_user_raises_error(self, db_manager):
        """존재하지 않는 user_id로 usage_logs INSERT 시 IntegrityError 발생"""
        with db_manager.get_connection() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO usage_logs (user_id, action, duration_seconds)
                    VALUES (?, ?, ?)
                    """,
                    (99999, "transcribe", 30),
                )

    def test_foreign_keys_pragma_is_on(self, db_manager):
        """PRAGMA foreign_keys가 ON 상태인지 확인"""
        with db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA foreign_keys")
            result = cursor.fetchone()
            assert result[0] == 1

    def test_valid_user_id_insert_succeeds(self, db_manager, sample_user):
        """유효한 user_id로 usage_logs INSERT 성공"""
        with db_manager.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO usage_logs (user_id, action, duration_seconds)
                VALUES (?, ?, ?)
                """,
                (sample_user, "transcribe", 30),
            )
            conn.commit()

            cursor = conn.execute(
                "SELECT COUNT(*) FROM usage_logs WHERE user_id = ?",
                (sample_user,),
            )
            assert cursor.fetchone()[0] == 1

    def test_delete_user_cascade_still_works(self, db_manager, sample_user):
        """사용자 삭제 시 usage_logs도 함께 삭제 (기존 cascade 유지)"""
        usage_log = UsageLog(db_manager)
        usage_log.record_usage(sample_user, "transcribe", 30)

        user_model = User(db_manager)
        user_model.delete_user(sample_user)

        logs = usage_log.get_user_logs(sample_user)
        assert len(logs) == 0


# === Index Tests (ISSUE-13) ===


class TestUsageLogsIndexes:
    """usage_logs 테이블 인덱스 검증"""

    def test_user_id_index_exists(self, db_manager):
        """idx_usage_logs_user_id 인덱스 존재 확인"""
        with db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA index_list('usage_logs')")
            indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_usage_logs_user_id" in indexes

    def test_created_at_index_exists(self, db_manager):
        """idx_usage_logs_created_at 인덱스 존재 확인"""
        with db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA index_list('usage_logs')")
            indexes = {row["name"] for row in cursor.fetchall()}
        assert "idx_usage_logs_created_at" in indexes

    def test_indexes_idempotent(self, db_manager):
        """init_database 재실행 시 인덱스 중복 생성 없음"""
        db_manager.init_database()  # 두 번째 호출
        with db_manager.get_connection() as conn:
            cursor = conn.execute("PRAGMA index_list('usage_logs')")
            index_names = [row["name"] for row in cursor.fetchall()]
        # 중복 인덱스 없음
        assert index_names.count("idx_usage_logs_user_id") == 1
        assert index_names.count("idx_usage_logs_created_at") == 1

    def test_existing_tests_pass_with_indexes(self, db_manager, sample_user):
        """인덱스 추가 후 기존 usage_logs 작업 정상 동작"""
        usage_log = UsageLog(db_manager)
        log_id = usage_log.record_usage(sample_user, "transcribe", 30)
        assert log_id is not None

        logs = usage_log.get_user_logs(sample_user)
        assert len(logs) == 1
