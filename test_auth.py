"""
bcrypt 인증 시스템 테스트
"""

import tempfile
from pathlib import Path

from database import DatabaseManager, PasswordManager, User


def test_password_hashing():
    """비밀번호 해싱 및 검증 테스트"""
    password = "test_password_123"

    # 해싱
    hashed = PasswordManager.hash_password(password)
    assert hashed != password, "비밀번호가 해싱되지 않음"
    assert hashed.startswith("$2b$"), "bcrypt 해시 형식이 아님"
    print("✅ 비밀번호 해싱 성공")

    # 검증 성공
    assert PasswordManager.verify_password(
        password, hashed
    ), "올바른 비밀번호 검증 실패"
    print("✅ 올바른 비밀번호 검증 성공")

    # 검증 실패
    assert not PasswordManager.verify_password(
        "wrong_password", hashed
    ), "잘못된 비밀번호 검증 통과 (보안 문제)"
    print("✅ 잘못된 비밀번호 검증 실패 확인")


def test_user_creation_and_authentication():
    """사용자 생성 및 인증 테스트"""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".db") as f:
        test_db_path = f.name

    try:
        db = DatabaseManager(test_db_path)
        user_model = User(db)

        # 1. 사용자 생성
        username = "testuser"
        password = "testpass123"
        user_id = user_model.create_user(
            username=username,
            password=password,
            email="test@example.com",
            full_name="Test User",
        )

        assert user_id is not None, "사용자 생성 실패"
        print(f"✅ 사용자 생성 성공 (ID: {user_id})")

        # 2. 올바른 비밀번호로 인증
        user = user_model.authenticate(username, password)
        assert user is not None, "인증 실패"
        assert user["username"] == username, "사용자명 불일치"
        assert user["email"] == "test@example.com", "이메일 불일치"
        print("✅ 올바른 비밀번호 인증 성공")

        # 3. 잘못된 비밀번호로 인증 실패
        user = user_model.authenticate(username, "wrong_password")
        assert user is None, "잘못된 비밀번호로 인증 성공 (보안 문제)"
        print("✅ 잘못된 비밀번호 인증 실패 확인")

        # 4. 존재하지 않는 사용자 인증 실패
        user = user_model.authenticate("nonexistent", password)
        assert user is None, "존재하지 않는 사용자 인증 성공"
        print("✅ 존재하지 않는 사용자 인증 실패 확인")

        # 5. 비밀번호 변경
        new_password = "new_password_456"
        success = user_model.change_password(user_id, new_password)
        assert success, "비밀번호 변경 실패"
        print("✅ 비밀번호 변경 성공")

        # 6. 새 비밀번호로 인증
        user = user_model.authenticate(username, new_password)
        assert user is not None, "새 비밀번호 인증 실패"
        print("✅ 새 비밀번호 인증 성공")

        # 7. 이전 비밀번호로 인증 실패
        user = user_model.authenticate(username, password)
        assert user is None, "이전 비밀번호로 인증 성공 (보안 문제)"
        print("✅ 이전 비밀번호 인증 실패 확인")

        print("\n🎉 모든 인증 테스트 통과!")

    finally:
        Path(test_db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("bcrypt 인증 시스템 테스트 시작")
    print("=" * 60 + "\n")

    try:
        test_password_hashing()
        print()
        test_user_creation_and_authentication()

        print("\n" + "=" * 60)
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
