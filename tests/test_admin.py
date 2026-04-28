"""
admin_logic.py 비즈니스 로직 단위 테스트
validate_password, prepare_user_table_data, export_user_logs_csv 함수 테스트
"""

import csv
import io

import pytest

from admin_logic import export_user_logs_csv, prepare_user_table_data, validate_password


# ============================================================
# validate_password 테스트
# ============================================================
class TestValidatePassword:
    """validate_password 함수 테스트"""

    def test_valid_password(self):
        """유효한 비밀번호와 일치하는 확인 -> (True, '')"""
        valid, msg = validate_password("secure123", "secure123")
        assert valid is True
        assert msg == ""

    def test_empty_password(self):
        """빈 비밀번호 -> (False, 에러 메시지)"""
        valid, msg = validate_password("", "")
        assert valid is False
        assert "필수" in msg

    def test_password_too_short(self):
        """6자 미만 비밀번호 -> (False, 에러 메시지)"""
        valid, msg = validate_password("abc12", "abc12")
        assert valid is False
        assert "6자" in msg

    def test_password_exactly_6_chars(self):
        """정확히 6자 비밀번호 -> (True, '')"""
        valid, msg = validate_password("abc123", "abc123")
        assert valid is True
        assert msg == ""

    def test_password_mismatch(self):
        """비밀번호 불일치 -> (False, 에러 메시지)"""
        valid, msg = validate_password("secure123", "secure456")
        assert valid is False
        assert "일치" in msg

    def test_short_password_checked_before_mismatch(self):
        """짧은 비밀번호는 불일치 확인보다 먼저 체크"""
        valid, msg = validate_password("abc", "xyz")
        assert valid is False
        assert "6자" in msg

    def test_password_5_chars(self):
        """5자 비밀번호 -> (False, 에러 메시지)"""
        valid, msg = validate_password("12345", "12345")
        assert valid is False
        assert "6자" in msg

    def test_long_password(self):
        """긴 비밀번호도 통과"""
        long_pass = "a" * 100
        valid, msg = validate_password(long_pass, long_pass)
        assert valid is True
        assert msg == ""


# ============================================================
# prepare_user_table_data 테스트
# ============================================================
class TestPrepareUserTableData:
    """prepare_user_table_data 함수 테스트"""

    @pytest.fixture
    def sample_users(self):
        """테스트용 사용자 데이터"""
        return [
            {
                "id": 1,
                "username": "user1",
                "full_name": "User One",
                "email": "user1@example.com",
                "role": "user",
                "is_active": True,
                "total_usage_seconds": 1800,
                "usage_limit_seconds": 3600,
                "created_at": "2026-01-01",
                "last_login": "2026-04-01",
            },
            {
                "id": 2,
                "username": "admin1",
                "full_name": None,
                "email": None,
                "role": "admin",
                "is_active": True,
                "total_usage_seconds": 0,
                "usage_limit_seconds": 0,
                "created_at": "2026-01-01",
                "last_login": None,
            },
            {
                "id": 3,
                "username": "inactive_user",
                "full_name": "Inactive",
                "email": "inactive@test.com",
                "role": "user",
                "is_active": False,
                "total_usage_seconds": 500,
                "usage_limit_seconds": 3600,
                "created_at": "2026-02-01",
                "last_login": "2026-03-01",
            },
        ]

    def test_basic_conversion(self, sample_users):
        """기본 변환: 사용자 수만큼 행 반환"""
        result = prepare_user_table_data(
            sample_users, lambda uid: 1800 if uid == 1 else 0
        )
        assert len(result) == 3

    def test_remaining_minutes_calculation(self, sample_users):
        """남은시간(분) 올바른 계산"""
        result = prepare_user_table_data(
            sample_users, lambda uid: 1800 if uid == 1 else 0
        )
        # 1800초 = 30.0분
        assert result[0]["남은시간(분)"] == "30.0"

    def test_remaining_seconds_none_returns_zero(self, sample_users):
        """get_remaining_seconds가 None 반환 시 0분"""
        result = prepare_user_table_data(sample_users, lambda uid: None)
        assert result[0]["남은시간(분)"] == "0.0"

    def test_null_full_name_shows_dash(self, sample_users):
        """full_name이 None이면 '-' 표시"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[1]["소속"] == "-"

    def test_null_email_shows_dash(self, sample_users):
        """email이 None이면 '-' 표시"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[1]["이메일"] == "-"

    def test_null_last_login_shows_dash(self, sample_users):
        """last_login이 None이면 '-' 표시"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[1]["최근로그인"] == "-"

    def test_active_user_status(self, sample_users):
        """활성 사용자 상태 표시"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[0]["상태"] == "활성"

    def test_inactive_user_status(self, sample_users):
        """비활성 사용자 상태 표시"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[2]["상태"] == "비활성"

    def test_empty_users_list(self):
        """빈 사용자 리스트 -> 빈 결과"""
        result = prepare_user_table_data([], lambda uid: 0)
        assert result == []

    def test_preserves_user_fields(self, sample_users):
        """사용자 필드가 올바르게 매핑"""
        result = prepare_user_table_data(sample_users, lambda uid: 0)
        assert result[0]["ID"] == 1
        assert result[0]["사용자명"] == "user1"
        assert result[0]["역할"] == "user"
        assert result[0]["사용량(초)"] == 1800
        assert result[0]["제한(초)"] == 3600
        assert result[0]["생성일"] == "2026-01-01"

    def test_fractional_remaining_minutes(self, sample_users):
        """소수점 잔여 시간 계산"""
        # 90초 = 1.5분
        result = prepare_user_table_data([sample_users[0]], lambda uid: 90)
        assert result[0]["남은시간(분)"] == "1.5"


# ============================================================
# export_user_logs_csv 테스트
# ============================================================
class TestExportUserLogsCsv:
    """export_user_logs_csv 함수 테스트"""

    @pytest.fixture
    def sample_logs(self):
        """테스트용 로그 데이터"""
        return [
            {
                "id": 1,
                "user_id": 10,
                "action": "transcription",
                "duration_seconds": 30,
                "source_language": "en",
                "target_language": "ko",
                "created_at": "2026-04-28T10:00:00",
                "metadata": {
                    "source_text": "Hello world",
                    "target_text": "안녕하세요 세계",
                },
            },
            {
                "id": 2,
                "user_id": 10,
                "action": "transcription",
                "duration_seconds": 15,
                "source_language": "ko",
                "target_language": "en",
                "created_at": "2026-04-28T10:01:00",
                "metadata": None,
            },
        ]

    def test_csv_starts_with_bom(self, sample_logs):
        """CSV 출력이 UTF-8 BOM으로 시작"""
        result = export_user_logs_csv(sample_logs, "testuser")
        assert result[:3] == b"\xef\xbb\xbf"

    def test_csv_returns_bytes(self, sample_logs):
        """반환값이 bytes 타입"""
        result = export_user_logs_csv(sample_logs, "testuser")
        assert isinstance(result, bytes)

    def test_csv_has_correct_headers(self, sample_logs):
        """CSV에 올바른 헤더가 포함"""
        result = export_user_logs_csv(sample_logs, "testuser")
        # BOM 제거 후 디코딩
        csv_text = result[3:].decode("utf-8")
        reader = csv.reader(io.StringIO(csv_text))
        headers = next(reader)
        expected_headers = [
            "ID",
            "사용자ID",
            "작업",
            "시간(초)",
            "소스언어",
            "대상언어",
            "원문",
            "번역문",
            "생성일시",
            "메타데이터",
        ]
        assert headers == expected_headers

    def test_csv_row_count(self, sample_logs):
        """CSV 행 수 = 헤더 1 + 로그 수"""
        result = export_user_logs_csv(sample_logs, "testuser")
        csv_text = result[3:].decode("utf-8")
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 3  # 1 header + 2 data rows

    def test_csv_data_correct_values(self, sample_logs):
        """CSV 데이터 값 검증"""
        result = export_user_logs_csv(sample_logs, "testuser")
        csv_text = result[3:].decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        assert rows[0]["ID"] == "1"
        assert rows[0]["사용자ID"] == "10"
        assert rows[0]["작업"] == "transcription"
        assert rows[0]["시간(초)"] == "30"
        assert rows[0]["소스언어"] == "en"
        assert rows[0]["대상언어"] == "ko"
        assert rows[0]["원문"] == "Hello world"
        assert rows[0]["번역문"] == "안녕하세요 세계"

    def test_csv_metadata_none_handled(self, sample_logs):
        """metadata가 None인 로그도 처리"""
        result = export_user_logs_csv(sample_logs, "testuser")
        csv_text = result[3:].decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        # metadata가 None인 두 번째 로그
        assert rows[1]["원문"] == ""
        assert rows[1]["번역문"] == ""
        assert rows[1]["메타데이터"] == ""

    def test_csv_empty_logs(self):
        """빈 로그 리스트 -> 헤더만 포함된 CSV"""
        result = export_user_logs_csv([], "testuser")
        csv_text = result[3:].decode("utf-8")
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_csv_null_language_fields(self):
        """source_language/target_language가 None인 경우 빈 문자열"""
        logs = [
            {
                "id": 1,
                "user_id": 10,
                "action": "transcription",
                "duration_seconds": 5,
                "source_language": None,
                "target_language": None,
                "created_at": "2026-04-28T10:00:00",
                "metadata": {},
            },
        ]
        result = export_user_logs_csv(logs, "testuser")
        csv_text = result[3:].decode("utf-8")
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        assert rows[0]["소스언어"] == ""
        assert rows[0]["대상언어"] == ""

    def test_csv_encoding_korean_text(self):
        """한국어 텍스트가 올바르게 인코딩"""
        logs = [
            {
                "id": 1,
                "user_id": 10,
                "action": "transcription",
                "duration_seconds": 5,
                "source_language": "ko",
                "target_language": "en",
                "created_at": "2026-04-28T10:00:00",
                "metadata": {
                    "source_text": "안녕하세요",
                    "target_text": "Hello",
                },
            },
        ]
        result = export_user_logs_csv(logs, "testuser")
        csv_text = result[3:].decode("utf-8")
        assert "안녕하세요" in csv_text
