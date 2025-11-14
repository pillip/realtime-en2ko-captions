"""
DB 스키마 마이그레이션: hash_type 컬럼 추가
PBKDF2에서 bcrypt로 점진적 전환을 위한 준비
"""

import sqlite3
import sys


def migrate_database(db_path: str = "data/app.db"):
    """hash_type 컬럼 추가"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # hash_type 컬럼이 이미 있는지 확인
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        if "hash_type" in columns:
            print("✅ hash_type 컬럼이 이미 존재합니다.")
            return True

        # hash_type 컬럼 추가 (기본값: pbkdf2)
        cursor.execute(
            """
            ALTER TABLE users
            ADD COLUMN hash_type TEXT DEFAULT 'pbkdf2'
        """
        )

        conn.commit()
        print("✅ hash_type 컬럼 추가 완료")

        # 기존 사용자 확인
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        print(f"📊 기존 사용자 수: {user_count}명")
        print("💡 이 사용자들은 다음 로그인 시 자동으로 bcrypt로 전환됩니다.")

        conn.close()
        return True

    except sqlite3.Error as e:
        print(f"❌ 마이그레이션 실패: {e}")
        return False


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/app.db"
    print(f"🔧 DB 마이그레이션 시작: {db_path}")

    if migrate_database(db_path):
        print("🎉 마이그레이션 완료!")
    else:
        print("💥 마이그레이션 실패")
        sys.exit(1)
