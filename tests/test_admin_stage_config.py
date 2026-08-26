"""
관리자 무대 설정 폼 로직 단위 테스트 (ISSUE-39, TC-052 확장).

검증 대상은 모두 ``admin_logic`` 의 **순수 함수**다 (RL-001 / RL-005) —
Streamlit 위젯 호출은 ``admin.py`` 에 남기고, 폼 ↔ ``stage_config`` 변환·
업로드 분류·삭제 정합성은 여기서 Streamlit 없이 검증한다.

- ``build_stage_config_from_form`` : 폼 입력 → stage_config 후보 dict
- ``partition_uploads``            : 업로드 목록 → (수락, 거부[사유]) 분리
- ``delete_stage_logo``            : 파일 삭제 + config 갱신 (둘 다 필수)
- ``arm/is/clear_stage_logo_delete`` : 로고 삭제 2단계 확인 상태 (A11Y-03)
- ``describe_stage_config_drops``  : normalize 가 조용히 버린 값 경고 (F-5)
- ``find_asset_drift``             : config ↔ 디스크 불일치 감지
- ``Room.update_stage_config`` / ``get_stage_config`` DB 왕복 (AC-2 / AC-3)
- F-5 경고가 **DB 원본 블롭** 에서 실제로 발화하는지 (production 경로)
- ``admin._warn_stage_config_drops`` : 그 경고가 화면에 뜨는지 / 실패 degrade
- ``admin._render_stage_logo_manager`` : 삭제 2단계 흐름 (Streamlit mock + AST)

에셋을 건드리는 테스트는 모두 ``BRANDING_DIR`` 을 ``tmp_path`` 로 돌린다 —
실제 ``data/branding/`` 에는 어떤 파일도 쓰지 않는다. 외부 네트워크 호출 없음.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

from admin_logic import (
    STAGE_DELETE_ARMED_KEY,
    UPLOAD_FAILED_MESSAGE,
    arm_stage_logo_delete,
    build_stage_config_from_form,
    clear_stage_logo_delete,
    delete_stage_logo,
    describe_stage_config_drops,
    find_asset_drift,
    is_stage_logo_delete_armed,
    partition_uploads,
)
from branding_assets import MAX_ASSET_BYTES
from stage_config import (
    DEFAULT_STAGE_CONFIG,
    LOGO_GROUP_LABELS,
    normalize_stage_config,
    validate_stage_config,
)

# database -> streamlit 의존성 차단 (다른 테스트와 동일한 방어 패턴).
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# 샘플 바이트 (branding_assets 의 매직바이트 검증을 통과/실패시키기 위한 값)
# ---------------------------------------------------------------------------
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_BYTES = PNG_SIG + b"\x00" * 32
OVERSIZED_PNG = PNG_SIG + b"\x00" * MAX_ASSET_BYTES  # 2 MiB + 8 bytes


@pytest.fixture
def branding_dir(tmp_path, monkeypatch):
    """에셋 저장 루트를 tmp_path 로 격리한다."""
    root = tmp_path / "branding"
    monkeypatch.setenv("BRANDING_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# build_stage_config_from_form
# ---------------------------------------------------------------------------
class TestBuildStageConfigFromForm:
    """AC: 유효한 폼 입력 → validate_stage_config 가 True 를 주는 dict."""

    def test_valid_form_produces_validatable_config(self):
        cfg = build_stage_config_from_form(
            event_title="2026 개발자 콘퍼런스",
            event_subtitle="AI 트랙",
            caption_ratio="1/3",
            logo_groups={"주최": ["logo.png"], "후원": ["a.svg", "b.jpg"]},
        )

        assert validate_stage_config(cfg) == (True, "")
        assert cfg["event_title"] == "2026 개발자 콘퍼런스"
        assert cfg["event_subtitle"] == "AI 트랙"
        assert cfg["caption_ratio"] == "1/3"
        assert [g["label"] for g in cfg["logo_groups"]] == list(LOGO_GROUP_LABELS)
        by_label = {g["label"]: g["assets"] for g in cfg["logo_groups"]}
        assert by_label["주최"] == ["logo.png"]
        assert by_label["주관"] == []
        assert by_label["후원"] == ["a.svg", "b.jpg"]

    def test_normalized_output_keeps_every_field(self):
        """정규화를 통과해도 값이 바뀌지 않아야 한다 (조용한 보정 금지)."""
        cfg = build_stage_config_from_form(
            event_title="타이틀",
            event_subtitle="부제",
            caption_ratio="1/4",
            logo_groups={"주관": ["x.png"]},
        )
        assert normalize_stage_config(cfg) == cfg

    def test_whitespace_is_trimmed(self):
        cfg = build_stage_config_from_form(
            event_title="  앞뒤 공백  ",
            event_subtitle="\t부제\n",
            caption_ratio="1/4",
            logo_groups={},
        )
        assert cfg["event_title"] == "앞뒤 공백"
        assert cfg["event_subtitle"] == "부제"

    def test_empty_form_matches_documented_default(self):
        cfg = build_stage_config_from_form(
            event_title="",
            event_subtitle="",
            caption_ratio=DEFAULT_STAGE_CONFIG["caption_ratio"],
            logo_groups={},
        )
        assert cfg == DEFAULT_STAGE_CONFIG

    def test_invalid_ratio_is_reported_not_silently_fixed(self):
        """AC: 잘못된 비율은 조용히 기본값으로 바뀌지 않고 거절 사유가 나온다."""
        cfg = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/2",
            logo_groups={},
        )
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert "1/4" in reason and "1/3" in reason

    def test_unknown_label_survives_into_candidate(self):
        """알 수 없는 라벨을 조용히 지우지 않는다 — 폐기 경고(F-5)의 입력이 된다."""
        cfg = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={"협찬": ["x.png"]},
        )
        labels = [g["label"] for g in cfg["logo_groups"]]
        assert labels[: len(LOGO_GROUP_LABELS)] == list(LOGO_GROUP_LABELS)
        assert "협찬" in labels

    def test_none_logo_groups_is_tolerated(self):
        cfg = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups=None,
        )
        assert [g["assets"] for g in cfg["logo_groups"]] == [[], [], []]

    def test_duplicate_filenames_in_a_group_are_collapsed(self):
        """회귀 방지 (코드 리뷰 R-01): 같은 그룹의 중복 파일명은 하나로 합친다.

        디스크에서 파일이 사라진 뒤 관리자가 드리프트 경고를 보고 같은 이름을
        다시 업로드하면 ``save_asset`` 이 충돌 회피를 하지 않아 기존 참조와
        같은 이름이 한 번 더 들어온다. 중복을 그대로 저장하면 관리자 화면의
        삭제 버튼 key 가 충돌해(StreamlitDuplicateElementKey) 무대 설정
        섹션 이후 전체가 렌더되지 않는다.
        """
        cfg = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={"주최": ["a.png", "logo.png", "a.png"], "주관": ["b.png"]},
        )
        by_label = {g["label"]: g["assets"] for g in cfg["logo_groups"]}
        # 순서는 첫 등장 순서를 유지해야 무대 하단 로고 바 순서가 흔들리지 않는다.
        assert by_label["주최"] == ["a.png", "logo.png"]
        assert by_label["주관"] == ["b.png"]

    def test_duplicates_do_not_consume_the_room_asset_budget(self):
        """중복이 12개 상한을 갉아먹어 정상 업로드가 거절되면 안 된다."""
        cfg = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={"주최": ["dup.png"] * 20},
        )
        assert validate_stage_config(cfg) == (True, "")

    def test_too_long_title_is_rejected_by_validator(self):
        cfg = build_stage_config_from_form(
            event_title="가" * 121,
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={},
        )
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert "행사 타이틀" in reason


# ---------------------------------------------------------------------------
# partition_uploads
# ---------------------------------------------------------------------------
class TestPartitionUploads:
    """AC: 2 MB 초과 파일은 거부되고 정상 파일은 저장된다 (사유 문자열 포함)."""

    def test_oversized_and_valid_are_partitioned(self, branding_dir):
        from branding_assets import list_assets

        accepted, rejected = partition_uploads(
            "room1",
            [("big.png", OVERSIZED_PNG), ("logo.png", PNG_BYTES)],
        )

        assert accepted == ["logo.png"]
        assert len(rejected) == 1
        name, reason = rejected[0]
        assert name == "big.png"
        # RL-004: 사유 문자열 자체를 검증한다 (truthy 확인으로는 회귀를 못 잡는다).
        assert "2MB 이하" in reason
        # 거부된 파일은 디스크에 흔적을 남기지 않는다.
        assert list_assets("room1") == ["logo.png"]

    def test_one_rejection_does_not_abort_the_rest(self, branding_dir):
        accepted, rejected = partition_uploads(
            "room1",
            [
                ("bad.txt", b"hello"),
                ("ok1.png", PNG_BYTES),
                ("mismatch.png", b"not a png"),
                ("ok2.png", PNG_BYTES),
            ],
        )
        assert accepted == ["ok1.png", "ok2.png"]
        assert [name for name, _ in rejected] == ["bad.txt", "mismatch.png"]

    def test_returns_stored_name_when_filename_collides(self, branding_dir):
        partition_uploads("room1", [("logo.png", PNG_BYTES)])
        accepted, rejected = partition_uploads("room1", [("logo.png", PNG_BYTES)])
        # 덮어쓰기 대신 충돌 회피 이름이 반환되어야 config 가 실제 파일을 가리킨다.
        assert accepted == ["logo-2.png"]
        assert rejected == []

    def test_unreadable_upload_gets_user_safe_reason(self, branding_dir):
        """읽기 실패(None)는 save_asset 이 만든 한국어 사유로 거절된다."""
        accepted, rejected = partition_uploads("room1", [("logo.png", None)])
        assert accepted == []
        assert len(rejected) == 1
        assert "읽을 수 없습니다" in rejected[0][1]

    def test_unexpected_exception_is_not_leaked_to_the_admin(self, capsys):
        """AC-8/RL-006: 로고 저장 중 디스크 오류 같은 예기치 못한 예외가 나도
        사용자에게는 일반 실패 메시지만 보이고 상세는 서버 콘솔에만 남는다.

        ValueError 가 아닌 예외의 ``str(e)`` 는 UI 사유가 되면 안 된다.
        """

        def _boom(room_id, filename, data):
            raise RuntimeError("/srv/secret/path denied")

        accepted, rejected = partition_uploads(
            "room1", [("logo.png", PNG_BYTES)], save_fn=_boom
        )

        assert accepted == []
        assert rejected == [("logo.png", UPLOAD_FAILED_MESSAGE)]
        assert "secret" not in rejected[0][1]
        # 상세는 서버 콘솔에만 남는다.
        captured = capsys.readouterr().out
        assert "[Admin]" in captured
        assert "/srv/secret/path denied" in captured

    def test_empty_upload_list_returns_two_empty_lists(self, branding_dir):
        assert partition_uploads("room1", []) == ([], [])

    def test_none_upload_list_is_tolerated(self, branding_dir):
        assert partition_uploads("room1", None) == ([], [])

    def test_save_fn_receives_room_and_payload(self):
        save_fn = MagicMock(return_value="stored.png")
        accepted, rejected = partition_uploads(
            "room-x", [("logo.png", PNG_BYTES)], save_fn=save_fn
        )
        save_fn.assert_called_once_with("room-x", "logo.png", PNG_BYTES)
        assert accepted == ["stored.png"]
        assert rejected == []


# ---------------------------------------------------------------------------
# delete_stage_logo
# ---------------------------------------------------------------------------
class TestDeleteStageLogo:
    """AC: 삭제는 파일 제거 + config 갱신이 **둘 다** 일어나야 한다."""

    def _room_model(self, assets_by_label, *, updated=True):
        model = MagicMock()
        model.get_stage_config.return_value = {
            "event_title": "t",
            "event_subtitle": "",
            "caption_ratio": "1/4",
            "logo_groups": [
                {"label": label, "assets": list(assets_by_label.get(label, []))}
                for label in LOGO_GROUP_LABELS
            ],
        }
        model.update_stage_config.return_value = updated
        return model

    def test_deletes_file_and_updates_config(self):
        model = self._room_model({"주최": ["logo.png", "keep.png"]})
        delete_fn = MagicMock(return_value=True)

        ok, message = delete_stage_logo(
            room_model=model,
            room_id="r-1",
            filename="logo.png",
            delete_fn=delete_fn,
        )

        assert ok is True
        assert message
        delete_fn.assert_called_once_with("r-1", "logo.png")
        model.update_stage_config.assert_called_once()
        saved_room_id, saved_cfg = model.update_stage_config.call_args.args
        assert saved_room_id == "r-1"
        by_label = {g["label"]: g["assets"] for g in saved_cfg["logo_groups"]}
        assert by_label["주최"] == ["keep.png"]

    def test_config_is_updated_even_when_file_already_gone(self):
        """회귀 방지: 파일만 지우고 config 를 갱신하지 않는 경로를 막는다."""
        model = self._room_model({"주관": ["ghost.png"]})
        delete_fn = MagicMock(return_value=False)

        ok, _ = delete_stage_logo(
            room_model=model,
            room_id="r-1",
            filename="ghost.png",
            delete_fn=delete_fn,
        )

        assert ok is True
        model.update_stage_config.assert_called_once()
        _, saved_cfg = model.update_stage_config.call_args.args
        by_label = {g["label"]: g["assets"] for g in saved_cfg["logo_groups"]}
        assert by_label["주관"] == []

    def test_filename_is_removed_from_every_group(self):
        model = self._room_model(
            {"주최": ["dup.png"], "주관": ["dup.png", "x.png"], "후원": ["dup.png"]}
        )

        delete_stage_logo(
            room_model=model,
            room_id="r-1",
            filename="dup.png",
            delete_fn=MagicMock(return_value=True),
        )

        _, saved_cfg = model.update_stage_config.call_args.args
        remaining = [a for g in saved_cfg["logo_groups"] for a in g["assets"]]
        assert remaining == ["x.png"]

    def test_update_failure_returns_false(self):
        model = self._room_model({"주최": ["logo.png"]}, updated=False)
        ok, message = delete_stage_logo(
            room_model=model,
            room_id="r-1",
            filename="logo.png",
            delete_fn=MagicMock(return_value=True),
        )
        assert ok is False
        assert message

    def test_db_exception_is_not_leaked(self, capsys):
        """RL-006: 내부 예외 문자열은 반환 메시지에 들어가면 안 된다."""
        model = MagicMock()
        model.get_stage_config.side_effect = RuntimeError("sqlite3 /var/db/app.db")

        ok, message = delete_stage_logo(
            room_model=model,
            room_id="r-1",
            filename="logo.png",
            delete_fn=MagicMock(return_value=True),
        )

        assert ok is False
        assert "sqlite3" not in message
        assert "/var/db/app.db" not in message
        assert "sqlite3 /var/db/app.db" in capsys.readouterr().out

    def test_uses_branding_delete_asset_by_default(self, branding_dir):
        """delete_fn 미지정 시 실제 파일이 사라진다 (기본 주입 확인)."""
        from branding_assets import list_assets

        partition_uploads("r-real", [("logo.png", PNG_BYTES)])
        assert list_assets("r-real") == ["logo.png"]

        model = self._room_model({"주최": ["logo.png"]})
        ok, _ = delete_stage_logo(
            room_model=model, room_id="r-real", filename="logo.png"
        )

        assert ok is True
        assert list_assets("r-real") == []


# ---------------------------------------------------------------------------
# describe_stage_config_drops (ISSUE-37 리뷰 F-5)
# ---------------------------------------------------------------------------
class TestDescribeStageConfigDrops:
    """normalize 가 조용히 버린 값을 관리자에게 알릴 수 있어야 한다."""

    def _candidate(self, groups):
        return {
            "event_title": "t",
            "event_subtitle": "",
            "caption_ratio": "1/4",
            "logo_groups": groups,
        }

    def test_clean_config_reports_nothing(self):
        candidate = build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={"주최": ["a.png"]},
        )
        assert (
            describe_stage_config_drops(candidate, normalize_stage_config(candidate))
            == []
        )

    def test_unknown_label_is_reported(self):
        candidate = self._candidate(
            [
                {"label": "주최", "assets": ["a.png"]},
                {"label": "협찬", "assets": ["b.png"]},
            ]
        )
        warnings = describe_stage_config_drops(
            candidate, normalize_stage_config(candidate)
        )
        assert len(warnings) == 1
        assert "협찬" in warnings[0]

    def test_dropped_asset_entry_is_reported(self):
        candidate = self._candidate(
            [{"label": "주최", "assets": ["a.png", 12345, None]}]
        )
        warnings = describe_stage_config_drops(
            candidate, normalize_stage_config(candidate)
        )
        assert len(warnings) == 1
        assert "주최" in warnings[0]

    def test_duplicate_label_second_group_is_reported(self):
        candidate = self._candidate(
            [
                {"label": "주최", "assets": ["a.png"]},
                {"label": "주최", "assets": ["b.png"]},
            ]
        )
        warnings = describe_stage_config_drops(
            candidate, normalize_stage_config(candidate)
        )
        assert len(warnings) == 1
        assert "b.png" in warnings[0]

    @pytest.mark.parametrize("bad", [None, "", [], 5, {"logo_groups": "nope"}])
    def test_malformed_inputs_return_empty_list(self, bad):
        assert describe_stage_config_drops(bad, DEFAULT_STAGE_CONFIG) == []

    @pytest.mark.parametrize(
        "raw",
        [None, [], "문자열 블롭", {"logo_groups": {"주최": ["a.png"]}}],
        ids=["none", "array", "string", "logo_groups-as-dict"],
    )
    def test_raw_db_blob_shapes_degrade_to_no_warning(self, raw):
        """DB 원본 블롭은 dict 가 아닐 수도 있다 (빈 컬럼/깨진 값/구버전 스키마).

        관리자 폼 로드 경로가 이 함수에 DB 원본을 그대로 넘기므로, 어떤
        모양이 들어와도 예외 없이 빈 목록으로 degrade 해야 폼이 열린다.
        """
        assert describe_stage_config_drops(raw, DEFAULT_STAGE_CONFIG) == []

    def test_non_dict_group_entry_is_skipped_not_crashing(self):
        """깨진 항목이 섞여 있어도 나머지 그룹의 경고는 계속 만들어진다."""
        candidate = self._candidate(
            ["문자열 그룹", {"label": "협찬", "assets": ["b.png"]}]
        )
        warnings = describe_stage_config_drops(
            candidate, normalize_stage_config(candidate)
        )
        assert len(warnings) == 1
        assert "협찬" in warnings[0]


# ---------------------------------------------------------------------------
# find_asset_drift
# ---------------------------------------------------------------------------
class TestFindAssetDrift:
    """config ↔ 디스크 불일치 양방향 감지."""

    def _config(self, assets):
        return build_stage_config_from_form(
            event_title="t",
            event_subtitle="",
            caption_ratio="1/4",
            logo_groups={"주최": assets},
        )

    def test_missing_on_disk_is_reported(self):
        missing, orphaned = find_asset_drift(
            self._config(["a.png", "b.png"]), ["a.png"]
        )
        assert missing == ["b.png"]
        assert orphaned == []

    def test_orphaned_on_disk_is_reported(self):
        missing, orphaned = find_asset_drift(
            self._config(["a.png"]), ["a.png", "z.png"]
        )
        assert missing == []
        assert orphaned == ["z.png"]

    def test_both_directions_at_once(self):
        missing, orphaned = find_asset_drift(self._config(["a.png"]), ["z.png"])
        assert missing == ["a.png"]
        assert orphaned == ["z.png"]

    def test_in_sync_returns_two_empty_lists(self):
        assert find_asset_drift(self._config(["a.png"]), ["a.png"]) == ([], [])

    def test_malformed_config_returns_disk_files_as_orphans(self):
        assert find_asset_drift(None, ["z.png"]) == ([], ["z.png"])

    def test_broken_group_entries_are_ignored(self):
        """깨진 그룹이 섞여도 정상 그룹의 참조는 그대로 인식된다."""
        config = {"logo_groups": ["문자열", {"label": "주최", "assets": ["a.png", 7]}]}
        assert find_asset_drift(config, ["a.png", "z.png"]) == ([], ["z.png"])


# ---------------------------------------------------------------------------
# DB 왕복 (AC-2 / AC-3) — TC-052 의 폼 경로 버전
# ---------------------------------------------------------------------------
@pytest.fixture
def db_manager(tmp_path):
    from database import DatabaseManager

    return DatabaseManager(str(tmp_path / "issue39.db"))


@pytest.fixture
def room_model(db_manager):
    from database import Room

    return Room(db_manager)


@pytest.fixture
def admin_user_id(db_manager):
    from database import User

    return User(db_manager).create_user(
        username="adm39", password="pwadmin", role="admin"
    )


class TestStageConfigRoundTrip:
    """AC-2 / AC-3: 저장 후 다시 조회하면 같은 값이 나온다."""

    def test_form_values_survive_the_round_trip(self, room_model, admin_user_id):
        room_model.create(room_id="r-stage", name="A홀", created_by=admin_user_id)
        cfg = build_stage_config_from_form(
            event_title="2026 개발자 콘퍼런스",
            event_subtitle="AI 트랙",
            caption_ratio="1/3",
            logo_groups={"주최": ["logo.png"]},
        )

        assert room_model.update_stage_config("r-stage", cfg) is True

        stored = room_model.get_stage_config("r-stage")
        assert stored["event_title"] == "2026 개발자 콘퍼런스"
        assert stored["event_subtitle"] == "AI 트랙"
        assert stored["caption_ratio"] == "1/3"
        assert stored["logo_groups"][0] == {"label": "주최", "assets": ["logo.png"]}

    def test_invalid_ratio_leaves_db_untouched(self, room_model, admin_user_id):
        room_model.create(room_id="r-bad", name="B홀", created_by=admin_user_id)
        cfg = build_stage_config_from_form(
            event_title="지워지면 안 됨",
            event_subtitle="",
            caption_ratio="1/2",
            logo_groups={},
        )

        assert room_model.update_stage_config("r-bad", cfg) is False
        assert room_model.get_stage_config("r-bad") == DEFAULT_STAGE_CONFIG


# ---------------------------------------------------------------------------
# F-5 경고가 **production 경로**에서 발화하는가 (코드 리뷰 R-02)
# ---------------------------------------------------------------------------
_RAW_ROOM_ID = "r-rawblob"

# 알 수 없는 그룹 라벨이 섞인 원본 블롭 — 구버전 빌드/수동 DB 수정/백업
# 복원으로 실제 컬럼에 들어올 수 있는 모양이다.
_UNKNOWN_LABEL_BLOB = {
    "event_title": "2026 개발자 콘퍼런스",
    "event_subtitle": "",
    "caption_ratio": "1/3",
    "logo_groups": [
        {"label": "주최", "assets": ["host.png"]},
        {"label": "협찬", "assets": ["ghost.png"]},
    ],
}

# 문자열이 아닌 에셋 항목 — 정규화가 조용히 버리는 두 번째 경우.
_BAD_ASSET_BLOB = {
    "event_title": "타이틀",
    "event_subtitle": "",
    "caption_ratio": "1/4",
    "logo_groups": [{"label": "주최", "assets": ["host.png", {"src": "ghost.png"}]}],
}


def _seed_raw_stage_config(db_manager, room_model, admin_user_id, blob):
    """stage_config 컬럼에 정규화되지 않은 블롭을 직접 넣고 room_id 를 준다.

    ``update_stage_config`` 는 정규화 후 저장하므로 이 상태를 만들 수 없다 —
    실제 위험(원본에만 있는 값이 폼 저장 한 번으로 사라짐)을 재현하려면
    SQL 로 직접 써야 한다.
    """
    room_model.create(room_id=_RAW_ROOM_ID, name="A홀", created_by=admin_user_id)
    with db_manager.get_connection() as conn:
        conn.execute(
            "UPDATE rooms SET stage_config = ? WHERE id = ?",
            (json.dumps(blob, ensure_ascii=False), _RAW_ROOM_ID),
        )
        conn.commit()
    return _RAW_ROOM_ID


class TestStageConfigDropWarningsFromDatabase:
    """F-5 경고가 손으로 만든 fixture 가 아니라 DB 원본에서 나오는지 확인한다.

    호출부가 이미 정규화된 값만 넘기면 이 경고는 영원히 빈 목록이고, 단위
    테스트만 초록으로 남는다(리뷰 R-02). 그래서 여기서는 admin.py 로드 경로와
    **같은 순서** 로 DB → (원본, 정규화) → 경고를 흘려 보낸다.
    """

    def test_unknown_group_label_in_db_blob_produces_a_warning(
        self, db_manager, room_model, admin_user_id
    ):
        room_id = _seed_raw_stage_config(
            db_manager, room_model, admin_user_id, _UNKNOWN_LABEL_BLOB
        )

        current_config = room_model.get_stage_config(room_id)
        raw = room_model.get_raw_stage_config(room_id)
        warnings = describe_stage_config_drops(raw, current_config)

        assert len(warnings) == 1
        assert "협찬" in warnings[0]
        assert "ghost.png" in warnings[0]

    def test_non_string_asset_in_db_blob_produces_a_warning(
        self, db_manager, room_model, admin_user_id
    ):
        room_id = _seed_raw_stage_config(
            db_manager, room_model, admin_user_id, _BAD_ASSET_BLOB
        )

        current_config = room_model.get_stage_config(room_id)
        raw = room_model.get_raw_stage_config(room_id)
        warnings = describe_stage_config_drops(raw, current_config)

        assert len(warnings) == 1
        assert "주최" in warnings[0]
        assert "ghost.png" in warnings[0]

    def test_normalised_db_blob_produces_no_warning(
        self, db_manager, room_model, admin_user_id
    ):
        """판별력 확인 — 정상 룸에서는 경고가 나오면 안 된다 (RL-004)."""
        room_id = _seed_raw_stage_config(
            db_manager, room_model, admin_user_id, DEFAULT_STAGE_CONFIG
        )
        room_model.update_stage_config(
            room_id,
            build_stage_config_from_form(
                event_title="정상",
                event_subtitle="",
                caption_ratio="1/4",
                logo_groups={"주최": ["host.png"]},
            ),
        )

        current_config = room_model.get_stage_config(room_id)
        raw = room_model.get_raw_stage_config(room_id)

        assert describe_stage_config_drops(raw, current_config) == []


class TestStageConfigLoadPathWarnings:
    """admin.py 로드 경로가 그 경고를 실제로 화면에 띄우는지 (Streamlit mock)."""

    def _admin_with_mock_st(self, monkeypatch):
        import admin

        fake_st = MagicMock()
        monkeypatch.setattr(admin, "st", fake_st)
        return admin, fake_st

    def _messages(self, fake_st):
        return [call.args[0] for call in fake_st.warning.call_args_list]

    def test_warning_states_the_consequence_of_saving(
        self, monkeypatch, db_manager, room_model, admin_user_id
    ):
        """저장 **전에** "저장하면 사라진다" 를 알려야 의미가 있다."""
        admin, fake_st = self._admin_with_mock_st(monkeypatch)
        room_id = _seed_raw_stage_config(
            db_manager, room_model, admin_user_id, _UNKNOWN_LABEL_BLOB
        )

        admin._warn_stage_config_drops(
            room_model, room_id, room_model.get_stage_config(room_id)
        )

        messages = self._messages(fake_st)
        assert len(messages) == 1
        assert "협찬" in messages[0]
        assert "저장하면" in messages[0]

    def test_clean_room_shows_no_warning(self, monkeypatch, room_model, admin_user_id):
        admin, fake_st = self._admin_with_mock_st(monkeypatch)
        room_model.create(room_id="r-clean", name="B홀", created_by=admin_user_id)

        admin._warn_stage_config_drops(
            room_model, "r-clean", room_model.get_stage_config("r-clean")
        )

        assert self._messages(fake_st) == []

    def test_raw_read_failure_does_not_break_the_form(self, monkeypatch, capsys):
        """RL-006: 원본을 못 읽어도 폼은 열리고 내부 상세는 화면에 없다."""
        admin, fake_st = self._admin_with_mock_st(monkeypatch)
        model = MagicMock()
        model.get_raw_stage_config.side_effect = RuntimeError("sqlite3 /var/db/app.db")

        admin._warn_stage_config_drops(model, "r-1", DEFAULT_STAGE_CONFIG)

        messages = self._messages(fake_st)
        assert len(messages) == 1
        assert "/var/db/app.db" not in messages[0]
        assert "sqlite3" not in messages[0]
        # 폼 렌더를 막는 st.error / 조기 반환이 아니라 경고로만 degrade 한다.
        fake_st.error.assert_not_called()
        captured = capsys.readouterr().out
        assert "[Admin]" in captured
        assert "sqlite3 /var/db/app.db" in captured


# ---------------------------------------------------------------------------
# 로고 삭제 2단계 확인 (A11Y-03 / WCAG 2.1 SC 3.3.4 Error Prevention)
# ---------------------------------------------------------------------------
_ROW = {"room_id": "r-1", "label": "주최", "index": 0, "filename": "a.png"}


class TestStageLogoDeleteArming:
    """어느 행이 "확인 대기" 인지 판단하는 규칙 (Streamlit 없이 검증).

    admin.py 는 커버리지 제외 대상이므로 판단 규칙 자체는 admin_logic 에 두고
    여기서 검증한다 (RL-001/RL-005). 핵심은 **한 번에 한 행만** 무장된다는 것 —
    삭제 버튼이 세로로 늘어선 화면에서 다른 행까지 함께 무장되면 2단계 확인이
    오히려 오삭제를 부른다.
    """

    def test_nothing_is_armed_by_default(self):
        assert is_stage_logo_delete_armed({}, **_ROW) is False

    def test_arming_marks_the_targeted_row(self):
        state = {}
        arm_stage_logo_delete(state, **_ROW)
        assert is_stage_logo_delete_armed(state, **_ROW) is True

    def test_arming_file_a_does_not_arm_file_b(self):
        """같은 그룹의 다른 파일은 무장되지 않는다."""
        state = {}
        other_file = {**_ROW, "filename": "b.png"}
        arm_stage_logo_delete(state, **_ROW)
        assert is_stage_logo_delete_armed(state, **other_file) is False

    def test_same_filename_in_another_group_is_not_armed(self):
        state = {}
        arm_stage_logo_delete(state, **_ROW)
        assert is_stage_logo_delete_armed(state, **{**_ROW, "label": "후원"}) is False

    def test_same_filename_at_another_index_is_not_armed(self):
        """같은 그룹에 같은 이름이 두 번 있어도 눌린 행만 무장된다."""
        state = {}
        arm_stage_logo_delete(state, **_ROW)
        assert is_stage_logo_delete_armed(state, **{**_ROW, "index": 1}) is False

    def test_same_file_in_another_room_is_not_armed(self):
        state = {}
        arm_stage_logo_delete(state, **_ROW)
        assert is_stage_logo_delete_armed(state, **{**_ROW, "room_id": "r-2"}) is False

    def test_arming_another_row_replaces_the_previous_one(self):
        state = {}
        other_file = {**_ROW, "filename": "b.png"}
        arm_stage_logo_delete(state, **_ROW)
        arm_stage_logo_delete(state, **other_file)
        assert is_stage_logo_delete_armed(state, **_ROW) is False
        assert is_stage_logo_delete_armed(state, **other_file) is True

    def test_clear_disarms(self):
        state = {}
        arm_stage_logo_delete(state, **_ROW)
        clear_stage_logo_delete(state)
        assert STAGE_DELETE_ARMED_KEY not in state
        assert is_stage_logo_delete_armed(state, **_ROW) is False

    def test_clear_on_untouched_state_is_a_noop(self):
        state = {"other": 1}
        clear_stage_logo_delete(state)
        assert state == {"other": 1}


# ---------------------------------------------------------------------------
# 삭제 흐름 (admin.py + Streamlit mock)
# ---------------------------------------------------------------------------
class _Rerun(Exception):
    """``st.rerun()`` 은 예외로 스크립트를 끊는다 — 그 흐름을 그대로 흉내낸다.

    MagicMock 이 그냥 None 을 돌려주면 rerun 뒤 코드가 계속 실행돼, 실제로는
    일어나지 않는 렌더까지 단언하게 된다 (RL-004).
    """


def _fake_columns(spec, **kwargs):
    """``st.columns`` 대역 — 언패킹 가능한 컨텍스트 매니저 목록을 준다."""
    count = len(spec) if isinstance(spec, list | tuple) else int(spec)
    return [MagicMock() for _ in range(count)]


def _fake_admin(monkeypatch, *, delete_result=(True, "로고를 삭제했습니다.")):
    """``admin`` 모듈에 Streamlit 대역과 삭제 mock 을 꽂고 돌려준다."""
    import admin

    fake_st = MagicMock()
    fake_st.session_state = {}
    fake_st.button.return_value = False
    fake_st.columns.side_effect = _fake_columns
    fake_st.rerun.side_effect = _Rerun
    monkeypatch.setattr(admin, "st", fake_st)

    delete_mock = MagicMock(return_value=delete_result)
    monkeypatch.setattr(admin, "delete_stage_logo", delete_mock)
    return admin, fake_st, delete_mock


def _click(fake_st, label):
    """정확히 그 **보이는 라벨** 의 버튼만 눌린 것으로 만든다."""
    fake_st.button.side_effect = lambda text, **kwargs: text == label


def _button_labels(fake_st):
    return [call.args[0] for call in fake_st.button.call_args_list if call.args]


def _button_keys(fake_st):
    return [call.kwargs.get("key") for call in fake_st.button.call_args_list]


def _config_with(groups):
    """정규화/중복 제거를 거치지 않은 raw config (중복 파일명 재현용)."""
    return {
        "event_title": "t",
        "event_subtitle": "",
        "caption_ratio": "1/4",
        "logo_groups": groups,
    }


class TestStageLogoDeleteIsConfirmed:
    """A11Y-03: 첫 클릭은 확인을 띄우기만 하고 파일을 지우지 않는다.

    삭제 버튼 라벨에 파일명이 들어가면서 비슷한 버튼이 세로로 늘어서므로,
    한 번의 오클릭/스트레이 키 입력이 되돌릴 수 없는 파일 삭제가 되면 안 된다
    (WCAG 2.1 SC 3.3.4). 사용자 계정 삭제와 같은 확인/취소 패턴을 따른다.
    """

    def _two_logos(self):
        return _config_with([{"label": "주최", "assets": ["a.png", "b.png"]}])

    def test_first_click_arms_without_deleting(self, monkeypatch, branding_dir):
        """첫 클릭은 확인 UI 를 띄우기만 한다 — 파일도 rerun 도 건드리지 않는다.

        rerun 을 끼우면 포커스와 탭 선택 같은 화면 맥락이 통째로 날아간다
        (A11Y-04). 확인 버튼은 같은 런에서 바로 이어 그려져야 한다.
        """
        admin, fake_st, delete_mock = _fake_admin(monkeypatch)
        _click(fake_st, "삭제 · a.png")

        admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        delete_mock.assert_not_called()
        fake_st.rerun.assert_not_called()
        assert fake_st.session_state[STAGE_DELETE_ARMED_KEY] == (
            "r-1",
            "주최",
            0,
            "a.png",
        )
        # 확인/취소가 같은 런에서 바로 나타나야 다음 조작을 이어 갈 수 있다.
        assert _button_labels(fake_st) == [
            "삭제 · a.png",
            "삭제 확인 · a.png",
            "삭제 취소 · a.png",
            "삭제 · b.png",
        ]

    def test_unarmed_rows_only_offer_the_delete_button(self, monkeypatch, branding_dir):
        admin, fake_st, _ = _fake_admin(monkeypatch)

        admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        assert _button_labels(fake_st) == ["삭제 · a.png", "삭제 · b.png"]

    def test_armed_row_offers_confirm_and_cancel_named_for_the_file(
        self, monkeypatch, branding_dir
    ):
        """RL-010 / WCAG 4.1.2: 확인·취소도 어느 파일인지 라벨로 말해야 한다.

        Streamlit 에는 aria-label 파라미터가 없고 ``help=`` 는 감싸는 div 에
        aria-describedby 로 붙으므로, <button> 의 접근 가능한 이름을 만드는
        수단은 보이는 라벨뿐이다. 무장되지 않은 행은 그대로 "삭제 · b.png" 다.
        """
        admin, fake_st, _ = _fake_admin(monkeypatch)
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )

        admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        assert _button_labels(fake_st) == [
            "삭제 확인 · a.png",
            "삭제 취소 · a.png",
            "삭제 · b.png",
        ]

    def test_confirm_deletes_only_the_armed_file(self, monkeypatch, branding_dir):
        admin, fake_st, delete_mock = _fake_admin(monkeypatch)
        room_model = MagicMock()
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )
        _click(fake_st, "삭제 확인 · a.png")

        with pytest.raises(_Rerun):
            admin._render_stage_logo_manager(room_model, "r-1", self._two_logos())

        delete_mock.assert_called_once_with(
            room_model=room_model, room_id="r-1", filename="a.png"
        )
        # 확인이 끝나면 무장은 반드시 풀려야 한다 (다음 런에서 또 뜨면 안 된다).
        assert STAGE_DELETE_ARMED_KEY not in fake_st.session_state

    def test_cancel_disarms_without_deleting(self, monkeypatch, branding_dir):
        admin, fake_st, delete_mock = _fake_admin(monkeypatch)
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )
        _click(fake_st, "삭제 취소 · a.png")

        with pytest.raises(_Rerun):
            admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        delete_mock.assert_not_called()
        assert STAGE_DELETE_ARMED_KEY not in fake_st.session_state

    def test_widget_keys_stay_unique_with_duplicate_filenames(
        self, monkeypatch, branding_dir
    ):
        """중복 파일명이 남아 있어도 key 가 충돌하면 안 된다.

        키 충돌은 ``StreamlitDuplicateElementKey`` 로 룸 관리 탭의 이후 섹션
        (오퍼레이터 배정 / 룸 강제 종료 / 룸별 대화 기록) 전체를 죽인다.
        확인·취소 버튼도 삭제 버튼과 같은 index 포함 shape 를 써야 한다.
        """
        admin, fake_st, _ = _fake_admin(monkeypatch)
        config = _config_with([{"label": "주최", "assets": ["dup.png", "dup.png"]}])
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="dup.png",
        )

        admin._render_stage_logo_manager(MagicMock(), "r-1", config)

        keys = _button_keys(fake_st)
        assert len(keys) == 3
        assert None not in keys
        assert len(set(keys)) == 3

    def test_delete_result_is_announced_after_the_rerun(
        self, monkeypatch, branding_dir
    ):
        """A11Y-07: 삭제 직후 rerun 이 성공 메시지를 지워 버리면 안 된다.

        마지막 로고를 지운 경우(등록된 로고 0개)에도 결과가 표시되어야 한다 —
        "등록된 로고가 없습니다" 안내로 조기 반환하기 **전** 에 내야 한다.
        """
        admin, fake_st, _ = _fake_admin(monkeypatch)
        fake_st.session_state[admin._STAGE_DELETE_RESULT_KEY] = (
            "success",
            "로고를 삭제했습니다.",
        )

        admin._render_stage_logo_manager(MagicMock(), "r-1", _config_with([]))

        fake_st.success.assert_called_once_with("로고를 삭제했습니다.")
        # 한 번만 안내한다 — 다음 런까지 남으면 유령 메시지가 된다.
        assert admin._STAGE_DELETE_RESULT_KEY not in fake_st.session_state

    def test_failed_delete_is_reported_as_an_error(self, monkeypatch, branding_dir):
        admin, fake_st, _ = _fake_admin(
            monkeypatch, delete_result=(False, "로고 삭제에 실패했습니다.")
        )
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )
        _click(fake_st, "삭제 확인 · a.png")

        with pytest.raises(_Rerun):
            admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        fake_st.button.side_effect = None
        fake_st.button.return_value = False
        admin._render_stage_logo_manager(MagicMock(), "r-1", self._two_logos())

        fake_st.error.assert_called_once_with("로고 삭제에 실패했습니다.")
        fake_st.success.assert_not_called()


class TestStageDeleteArmingFollowsTheRoom:
    """룸을 바꾸면 다른 룸 파일을 가리키는 확인 대기 상태가 남으면 안 된다."""

    def test_room_change_clears_the_armed_state(self, monkeypatch):
        admin, fake_st, _ = _fake_admin(monkeypatch)
        fake_st.session_state[admin._STAGE_SEEDED_ROOM_KEY] = "r-1"
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )

        admin._seed_stage_form_state("r-2", DEFAULT_STAGE_CONFIG)

        assert STAGE_DELETE_ARMED_KEY not in fake_st.session_state

    def test_same_room_keeps_the_armed_state(self, monkeypatch):
        """무조건 지우면 확인 버튼이 뜰 틈도 없이 사라진다 (RL-004 판별력)."""
        admin, fake_st, _ = _fake_admin(monkeypatch)
        fake_st.session_state[admin._STAGE_SEEDED_ROOM_KEY] = "r-1"
        arm_stage_logo_delete(
            fake_st.session_state,
            room_id="r-1",
            label="주최",
            index=0,
            filename="a.png",
        )

        admin._seed_stage_form_state("r-1", DEFAULT_STAGE_CONFIG)

        assert fake_st.session_state[STAGE_DELETE_ARMED_KEY] == (
            "r-1",
            "주최",
            0,
            "a.png",
        )


# ---------------------------------------------------------------------------
# 삭제가 **구조적으로** 2단계인가 (AST)
# ---------------------------------------------------------------------------
# tests/ 아래의 모든 .py 는 e2e 게이트 때문에 test_* 를 가져야 해서 공용 헬퍼
# 모듈을 둘 수 없다. 다른 테스트 모듈에서 import 해 오면 테스트끼리 얽히므로
# AST 헬퍼는 이 파일에 최소한으로 둔다 (test_admin_room_mgmt.py 와 같은 형태).
_ARM_FN = "arm_stage_logo_delete"
_ARMED_GUARD_FN = "is_stage_logo_delete_armed"
_DELETE_FN = "delete_stage_logo"


def _admin_source() -> str:
    path = pathlib.Path(__file__).resolve().parents[1] / "admin.py"
    return path.read_text(encoding="utf-8")


def _calls_named(nodes, name: str) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in nodes:
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == name
            ):
                found.append(child)
    return found


def _is_armed_guard(node) -> bool:
    """``if is_stage_logo_delete_armed(...):`` 인가 (부정형은 가드가 아니다).

    ``if not is_...(...)`` 의 body 는 "확인 대기가 **아닐** 때" 도는 곳이므로
    여기서 삭제가 일어나면 그것이야말로 1클릭 삭제다. 부정형을 가드로 인정하면
    검사기가 그 회귀를 놓친다.
    """
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == _ARMED_GUARD_FN
    )


def _two_step_delete_violations(source: str) -> list[str]:
    """소스가 "무장 → 확인 → 삭제" 2단계를 지키는지 검사한다.

    문자열 grep 은 확인 분기가 사라져도 통과하므로 AST 로 위치를 본다
    (RL-004). 규칙:

    1. 무장(1단계) 호출이 존재한다.
    2. 삭제 호출이 존재한다 (경로가 통째로 사라진 것도 회귀다).
    3. 모든 삭제 호출은 ``if is_stage_logo_delete_armed(...)`` 의 body 안에 있다.
    4. 무장하는 분기가 같은 자리에서 삭제까지 하지 않는다.
    """
    tree = ast.parse(source)
    violations: list[str] = []

    arm_calls = _calls_named(tree.body, _ARM_FN)
    delete_calls = _calls_named(tree.body, _DELETE_FN)
    if not arm_calls:
        violations.append(f"{_ARM_FN} 호출이 없다 — 1단계(확인 무장)가 사라졌다.")
    if not delete_calls:
        violations.append(f"{_DELETE_FN} 호출이 없다 — 삭제 경로가 사라졌다.")

    guarded_body: list[ast.stmt] = []
    for node in ast.walk(tree):
        if _is_armed_guard(node):
            guarded_body.extend(node.body)
    guarded_ids = {id(call) for call in _calls_named(guarded_body, _DELETE_FN)}
    if any(id(call) not in guarded_ids for call in delete_calls):
        violations.append(
            f"{_DELETE_FN} 이(가) `if {_ARMED_GUARD_FN}(...)` 밖에서 호출된다."
        )

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and _calls_named(node.body, _ARM_FN)
            and _calls_named(node.body, _DELETE_FN)
        ):
            violations.append("확인을 무장하는 분기가 그 자리에서 삭제까지 한다.")

    return violations


# 회귀 시나리오 — 검사기가 실제로 이것들을 잡아내야 판별력이 있다 (RL-004).
_ONE_CLICK_SOURCE = '''
def _render_stage_logo_manager(room_model, room_id, config):
    """한 번 클릭으로 바로 지우는 회귀 형태."""
    for index, filename in enumerate(config):
        if st.button(f"삭제 · {filename}", key=f"del_{index}_{filename}"):
            ok, message = delete_stage_logo(
                room_model=room_model, room_id=room_id, filename=filename
            )
            st.rerun()
'''

_ARM_AND_DELETE_SOURCE = '''
def _render_stage_logo_manager(room_model, room_id, config):
    """무장은 하지만 같은 분기에서 그대로 지워 버리는 형태 (확인이 무의미)."""
    for index, filename in enumerate(config):
        if st.button(f"삭제 · {filename}", key=f"del_{index}_{filename}"):
            arm_stage_logo_delete(
                st.session_state,
                room_id=room_id,
                label="주최",
                index=index,
                filename=filename,
            )
            ok, message = delete_stage_logo(
                room_model=room_model, room_id=room_id, filename=filename
            )
            st.rerun()
'''


_DELETE_IN_UNARMED_BRANCH_SOURCE = '''
def _render_stage_logo_manager(room_model, room_id, config):
    """확인 가드를 쓰지만 **부정형** 분기에서 지우는 형태 (사실상 1클릭)."""
    for index, filename in enumerate(config):
        target = {"room_id": room_id, "filename": filename}
        if not is_stage_logo_delete_armed(st.session_state, **target):
            if st.button(f"삭제 · {filename}", key=f"del_{index}_{filename}"):
                arm_stage_logo_delete(st.session_state, **target)
                ok, message = delete_stage_logo(
                    room_model=room_model, room_id=room_id, filename=filename
                )
'''


class TestStageLogoDeleteIsStructurallyTwoStep:
    def test_admin_source_gates_delete_behind_the_confirmation(self):
        assert _two_step_delete_violations(_admin_source()) == []

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (_ONE_CLICK_SOURCE, f"{_ARM_FN} 호출이 없다"),
            (_ARM_AND_DELETE_SOURCE, "그 자리에서 삭제까지 한다"),
            (_DELETE_IN_UNARMED_BRANCH_SOURCE, "그 자리에서 삭제까지 한다"),
        ],
        ids=[
            "one-click",
            "arm-and-delete-in-one-branch",
            "delete-in-negated-guard",
        ],
    )
    def test_checker_rejects_single_click_regressions(self, source, expected):
        """검사기가 1클릭 삭제로 되돌린 코드를 실제로 거부하는지 확인한다."""
        violations = _two_step_delete_violations(source)
        assert any(expected in v for v in violations), violations
        # 두 회귀 모두 "확인 가드 밖에서 삭제" 로도 걸려야 한다.
        assert any(_ARMED_GUARD_FN in v for v in violations), violations


class TestStageFormHelpText:
    """R-04: 길이 제한 문구는 ``stage_config`` 상수에서 파생되어야 한다.

    admin 계층이 "최대 120자" 를 하드코딩하면 규칙이 바뀌는 순간 도움말만
    조용히 거짓말을 한다 (검증 규칙의 소유자는 ``stage_config`` 다).
    """

    def test_length_limits_come_from_stage_config_constants(self, monkeypatch):
        import admin

        fake_st = MagicMock()
        monkeypatch.setattr(admin, "st", fake_st)
        monkeypatch.setattr(admin, "EVENT_TITLE_MAX_LEN", 7)
        monkeypatch.setattr(admin, "EVENT_SUBTITLE_MAX_LEN", 5)

        admin._render_stage_config_form("r-1")

        helps = [
            call.kwargs.get("help", "") for call in fake_st.text_input.call_args_list
        ]
        assert len(helps) == 2
        assert "7자" in helps[0]
        assert "5자" in helps[1]
