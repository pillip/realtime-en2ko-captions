"""
인증 시스템 bcrypt 마이그레이션 테스트
PBKDF2 → bcrypt 점진적 전환 검증
"""

import tempfile
from pathlib import Path

from database import DatabaseManager, PasswordManager, User


def test_pbkdf2_to_bcrypt_migration():
    """PBKDF2 사용자가 로그인 시 bcrypt로 마이그레이션되는지 테스트"""
    # 임시 데이터베이스 생성
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        test_db_path = f.name

    try:
        # 데이터베이스 초기화
        db = DatabaseManager(test_db_path)

        # hash_type 컬럼 추가 (마이그레이션 시뮬레이션)
        with db.get_connection() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN hash_type TEXT DEFAULT 'pbkdf2'")
            conn.commit()

        user_model = User(db)

        # 1. PBKDF2로 레거시 사용자 생성 (직접 INSERT로 시뮬레이션)
        test_username = "legacy_user"
        test_password = "test_password_123"
        salt = PasswordManager.generate_salt()
        pbkdf2_hash = PasswordManager.hash_password(test_password, salt)

        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, salt, email, full_name,
                                   role, hash_type, usage_limit_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    test_username,
                    pbkdf2_hash,
                    salt,
                    "legacy@test.com",
                    "Legacy User",
                    "user",
                    "pbkdf2",
                    3600,
                ),
            )
            conn.commit()

        # 2. PBKDF2 사용자로 인증 (첫 로그인)
        print("[Test] PBKDF2 사용자 첫 로그인 시도...")
        user = user_model.authenticate(test_username, test_password)
        assert user is not None, "PBKDF2 인증 실패"
        assert user["username"] == test_username
        print("✅ PBKDF2 인증 성공")

        # 3. DB에서 hash_type 확인 (bcrypt로 변경되었는지)
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT hash_type, password_hash, salt FROM users WHERE username = ?",
                (test_username,),
            )
            row = cursor.fetchone()
            assert row["hash_type"] == "bcrypt", "hash_type이 bcrypt로 변경되지 않음"
            assert row["salt"] == "", "salt가 비워지지 않음"
            assert row["password_hash"] != pbkdf2_hash, "password_hash가 변경되지 않음"
            new_bcrypt_hash = row["password_hash"]
            print("✅ PBKDF2 → bcrypt 마이그레이션 완료")

        # 4. 다시 로그인 (이번엔 bcrypt로)
        print("[Test] bcrypt 사용자 재로그인 시도...")
        user = user_model.authenticate(test_username, test_password)
        assert user is not None, "bcrypt 재인증 실패"
        print("✅ bcrypt 재인증 성공")

        # 5. bcrypt 해시가 변경되지 않았는지 확인 (불필요한 재해싱 방지)
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?", (test_username,)
            )
            row = cursor.fetchone()
            assert (
                row["password_hash"] == new_bcrypt_hash
            ), "bcrypt 해시가 불필요하게 재생성됨"
            print("✅ bcrypt 해시 불변성 확인")

        print("\n🎉 PBKDF2 → bcrypt 마이그레이션 테스트 통과!\n")

    finally:
        # 임시 파일 정리
        Path(test_db_path).unlink(missing_ok=True)


def test_new_user_with_bcrypt():
    """새 사용자가 bcrypt로 생성되는지 테스트"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        test_db_path = f.name

    try:
        db = DatabaseManager(test_db_path)

        # hash_type 컬럼 추가
        with db.get_connection() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN hash_type TEXT DEFAULT 'pbkdf2'")
            conn.commit()

        user_model = User(db)

        # 1. 새 사용자 생성
        test_username = "new_user"
        test_password = "new_password_456"
        user_id = user_model.create_user(
            username=test_username,
            password=test_password,
            email="new@test.com",
            full_name="New User",
        )

        assert user_id is not None, "사용자 생성 실패"
        print("✅ 새 사용자 생성 성공")

        # 2. 바로 bcrypt로 생성되었는지 확인
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT hash_type, salt FROM users WHERE username = ?", (test_username,)
            )
            row = cursor.fetchone()
            assert row["hash_type"] == "bcrypt", "새 사용자가 bcrypt로 생성되지 않음"
            assert row["salt"] == "", "새 사용자의 salt가 비워지지 않음"
            print("✅ 새 사용자 bcrypt 생성 확인")

        # 3. 인증 테스트
        user = user_model.authenticate(test_username, test_password)
        assert user is not None, "새 사용자 인증 실패"
        assert user["username"] == test_username
        print("✅ 새 사용자 bcrypt 인증 성공")

        print("\n🎉 새 사용자 bcrypt 생성 테스트 통과!\n")

    finally:
        Path(test_db_path).unlink(missing_ok=True)


def test_password_change_with_bcrypt():
    """비밀번호 변경 시 bcrypt 사용 테스트"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        test_db_path = f.name

    try:
        db = DatabaseManager(test_db_path)

        # hash_type 컬럼 추가
        with db.get_connection() as conn:
            conn.execute("ALTER TABLE users ADD COLUMN hash_type TEXT DEFAULT 'pbkdf2'")
            conn.commit()

        user_model = User(db)

        # 1. 사용자 생성
        test_username = "change_password_user"
        old_password = "old_password_789"
        new_password = "new_password_012"

        user_id = user_model.create_user(username=test_username, password=old_password)
        assert user_id is not None

        # 2. 비밀번호 변경
        success = user_model.change_password(user_id, new_password)
        assert success, "비밀번호 변경 실패"
        print("✅ 비밀번호 변경 성공")

        # 3. bcrypt로 저장되었는지 확인
        with db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT hash_type, salt FROM users WHERE id = ?", (user_id,)
            )
            row = cursor.fetchone()
            assert (
                row["hash_type"] == "bcrypt"
            ), "변경된 비밀번호가 bcrypt로 저장되지 않음"
            assert row["salt"] == "", "변경된 비밀번호의 salt가 비워지지 않음"
            print("✅ 변경된 비밀번호 bcrypt 저장 확인")

        # 4. 새 비밀번호로 인증
        user = user_model.authenticate(test_username, new_password)
        assert user is not None, "새 비밀번호 인증 실패"
        print("✅ 새 비밀번호 인증 성공")

        # 5. 이전 비밀번호로 인증 실패 확인
        user = user_model.authenticate(test_username, old_password)
        assert user is None, "이전 비밀번호로 인증 성공 (보안 문제)"
        print("✅ 이전 비밀번호 인증 실패 확인")

        print("\n🎉 비밀번호 변경 bcrypt 테스트 통과!\n")

    finally:
        Path(test_db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("인증 시스템 bcrypt 마이그레이션 테스트 시작")
    print("=" * 60 + "\n")

    try:
        test_pbkdf2_to_bcrypt_migration()
        test_new_user_with_bcrypt()
        test_password_change_with_bcrypt()

        print("=" * 60)
        print("🎉 모든 테스트 통과!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ 테스트 실패: {e}")
        exit(1)
    except Exception as e:
        print(f"\n💥 예외 발생: {e}")
        import traceback

        traceback.print_exc()
        exit(1)
