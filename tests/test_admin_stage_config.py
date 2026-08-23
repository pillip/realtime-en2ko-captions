"""
관리자 무대 설정 폼 로직 단위 테스트 (ISSUE-39, TC-052 확장).

검증 대상은 모두 ``admin_logic`` 의 **순수 함수**다 (RL-001 / RL-005) —
Streamlit 위젯 호출은 ``admin.py`` 에 남기고, 폼 ↔ ``stage_config`` 변환·
업로드 분류·삭제 정합성은 여기서 Streamlit 없이 검증한다.

- ``build_stage_config_from_form`` : 폼 입력 → stage_config 후보 dict
- ``partition_uploads``            : 업로드 목록 → (수락, 거부[사유]) 분리
- ``delete_stage_logo``            : 파일 삭제 + config 갱신 (둘 다 필수)
- ``describe_stage_config_drops``  : normalize 가 조용히 버린 값 경고 (F-5)
- ``find_asset_drift``             : config ↔ 디스크 불일치 감지
- ``Room.update_stage_config`` / ``get_stage_config`` DB 왕복 (AC-2 / AC-3)

에셋을 건드리는 테스트는 모두 ``BRANDING_DIR`` 을 ``tmp_path`` 로 돌린다 —
실제 ``data/branding/`` 에는 어떤 파일도 쓰지 않는다. 외부 네트워크 호출 없음.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from admin_logic import (
    UPLOAD_FAILED_MESSAGE,
    build_stage_config_from_form,
    delete_stage_logo,
    describe_stage_config_drops,
    find_asset_drift,
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
