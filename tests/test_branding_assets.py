"""
브랜딩 로고 에셋 저장소 단위 테스트 (ISSUE-38, TC-053/TC-054).

검증 대상 (`branding_assets.py`):
- `save_asset` / `list_assets` / `delete_asset` / `resolve_asset_path`
- NFR-028 보안 계약:
    - 파일명 sanitize (`[A-Za-z0-9._-]`) + 디렉터리 구분자/`..`/제어문자 거부
    - 2단 traversal 방어 (sanitize → `Path.resolve()` + `is_relative_to()`)
    - 심볼릭 링크 거부
    - 확장자 화이트리스트 + 내용(매직바이트) 일치 확인
    - 파일당 2 MB / 룸당 12개 상한
    - 같은 파일명 재업로드 시 덮어쓰기 금지 (충돌 회피 파일명 반환)
- RL-001: import 시점 부수효과 없음 (`BRANDING_DIR` 은 함수 안에서 읽는다)
- RL-015: "예외를 던지지 않는" 경로를 실제 퍼징 입력(None/int/bytes/list/
  빈 문자열/255바이트 초과 이름/`\\x00`/NFD 유니코드/초대형 traversal)으로 검증

Note: 모든 테스트는 `tmp_path` + `BRANDING_DIR` monkeypatch 로 격리된다.
실제 `data/branding/` 에는 어떤 파일도 쓰지 않는다. 외부 네트워크 호출 없음.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# database -> streamlit 의존성을 끌고 오는 다른 테스트와 동일한 방어 패턴.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


# ---------------------------------------------------------------------------
# 샘플 바이트 (매직바이트 검증용)
# ---------------------------------------------------------------------------
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_BYTES = PNG_SIG + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
SVG_BYTES = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
SVG_XML_DECL_BYTES = b'  \n\t<?xml version="1.0"?><svg><rect/></svg>'
SVG_BOM_BYTES = b"\xef\xbb\xbf<svg></svg>"

# RL-015 퍼징 입력 — sanitize/resolve 경로가 실제로 만나는 예외를 재현한다.
#   None/int/bytes/list  -> re.sub 이 TypeError
#   "\x00"               -> Path.resolve/mkdir 이 ValueError
#   255바이트 초과 이름   -> is_symlink/exists/write_bytes 가 OSError(ENAMETOOLONG)
#   NFD 유니코드          -> sanitize 후 stem 이 비는 경계
#
# REJECTED_NAMES: save_asset 이 반드시 ValueError 로 거절해야 하는 입력.
REJECTED_NAMES = [
    None,
    12,
    b"logo.png",
    ["logo.png"],
    "",
    "   ",
    "///",
    "..",
    "../secret",
    "/etc/passwd",
    "a/b.png",
    "..\\win.png",
    "lo\x00go.png",
    "lo\ngo\t.png",
    "../" * 10000 + "x.png",
]

# NORMALISED_NAMES: 거절 대상이 아니라 "정규화되어 통과"하는 경계 입력.
# (길이 절단 / 화이트리스트 밖 문자 제거) — 별도 테스트에서 결과를 단언한다.
NORMALISED_NAMES = [
    "a" * 300 + ".png",
    "b" * 5000 + ".png",
    unicodedata.normalize("NFD", "로고.png"),
]

# 읽기 경로(resolve/delete)는 위 두 부류 전부에 대해 예외 없이 degrade 해야 한다.
HOSTILE_NAMES = REJECTED_NAMES + NORMALISED_NAMES

HOSTILE_ROOM_IDS = [
    None,
    12,
    b"room",
    ["room"],
    "",
    "   ",
    "..",
    "../..",
    "/etc",
    "a/b",
    "a\\b",
    "ro\x00om",
    "r" * 300,
    ".hidden",
    "room 1",
    "룸",
    "room;rm -rf /",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def branding_root(tmp_path, monkeypatch):
    """격리된 BRANDING_DIR. 디렉터리는 일부러 만들지 않는다 (lazy 생성 검증)."""
    root = tmp_path / "branding"
    monkeypatch.setenv("BRANDING_DIR", str(root))
    return root


@pytest.fixture
def secret_file(tmp_path):
    """`data/branding` 바깥에 두는 미끼 파일 — traversal 로 도달하면 안 된다."""
    path = tmp_path / "app.db"
    path.write_bytes(b"SQLite format 3\x00TOP-SECRET-ROWS")
    return path


def _room_dir(branding_root: Path, room_id: str = "room1") -> Path:
    return branding_root / room_id


def _file_count(path: Path) -> int:
    return len(list(path.iterdir())) if path.is_dir() else 0


# ---------------------------------------------------------------------------
# RL-001 — import 부수효과 없음 / BRANDING_DIR 은 호출 시점에 읽는다
# ---------------------------------------------------------------------------
class TestImportPurity:
    def test_import_does_not_touch_filesystem_or_streamlit(self, tmp_path, monkeypatch):
        """import 만으로는 BRANDING_DIR 디렉터리가 생기지 않는다."""
        root = tmp_path / "never-created"
        monkeypatch.setenv("BRANDING_DIR", str(root))

        for mod in ("branding_assets",):
            sys.modules.pop(mod, None)
        import branding_assets

        assert not root.exists()
        # 순수 모듈 — streamlit / aiohttp / sqlite3 를 끌어오지 않는다.
        assert not hasattr(branding_assets, "st")
        assert not hasattr(branding_assets, "streamlit")
        assert not hasattr(branding_assets, "web")
        assert not hasattr(branding_assets, "aiohttp")

    def test_branding_dir_is_read_per_call_not_at_import(self, tmp_path, monkeypatch):
        """import 이후에 BRANDING_DIR 을 바꿔도 그 값이 즉시 반영된다 (RL-001)."""
        import branding_assets

        first = tmp_path / "first"
        second = tmp_path / "second"

        monkeypatch.setenv("BRANDING_DIR", str(first))
        branding_assets.save_asset("room1", "logo.png", PNG_BYTES)

        monkeypatch.setenv("BRANDING_DIR", str(second))
        branding_assets.save_asset("room1", "logo.png", PNG_BYTES)

        assert (first / "room1" / "logo.png").is_file()
        assert (second / "room1" / "logo.png").is_file()
        # 두 번째 저장이 첫 번째 루트를 건드리지 않았다 (충돌 회피 이름 없음).
        assert sorted(p.name for p in (first / "room1").iterdir()) == ["logo.png"]


# ---------------------------------------------------------------------------
# TC-053 — traversal / 심볼릭 링크 / 확장자 봉인
# ---------------------------------------------------------------------------
class TestTraversalContainment:
    @pytest.mark.parametrize(
        "filename", ["../secret", "/etc/passwd", "a/b.png", "..\\win.png"]
    )
    def test_resolve_asset_path_rejects_traversal(
        self, branding_root, secret_file, filename
    ):
        """TC-053: 4개 traversal 파일명은 모두 None (조용한 basename 언랩 금지)."""
        import branding_assets

        assert branding_assets.resolve_asset_path("room1", filename) is None
        # 미끼 파일은 그대로 남아 있고, 어떤 경로로도 노출되지 않았다.
        assert secret_file.read_bytes().startswith(b"SQLite format 3")

    @pytest.mark.parametrize(
        "filename", ["../secret.png", "/etc/passwd.png", "a/b.png", "..\\win.png"]
    )
    def test_save_asset_rejects_traversal(self, branding_root, secret_file, filename):
        """AC: traversal 파일명은 ValueError, 루트 바깥에 파일이 생기지 않는다."""
        import branding_assets

        with pytest.raises(ValueError):
            branding_assets.save_asset("room1", filename, PNG_BYTES)

        assert secret_file.read_bytes().startswith(b"SQLite format 3")
        # branding 루트 자체가 아예 생성되지 않았다 (거부는 mkdir 전에 끝난다).
        assert not branding_root.exists()

    def test_save_asset_rejects_parent_db_name(self, branding_root, secret_file):
        """AC 원문: filename='../../app.db' → ValueError."""
        import branding_assets

        with pytest.raises(ValueError):
            branding_assets.save_asset("room1", "../../app.db", PNG_BYTES)
        assert branding_assets.resolve_asset_path("room1", "../../app.db") is None
        assert secret_file.exists()

    def test_resolve_rejects_non_whitelisted_extension_already_on_disk(
        self, branding_root
    ):
        """룸 디렉터리 안에 있어도 화이트리스트 밖 확장자는 서빙되지 않는다."""
        import branding_assets

        room = _room_dir(branding_root)
        room.mkdir(parents=True)
        (room / "app.db").write_bytes(b"SQLite format 3\x00")

        assert branding_assets.resolve_asset_path("room1", "app.db") is None
        assert branding_assets.list_assets("room1") == []

    def test_resolve_rejects_symlink(self, branding_root, secret_file):
        """심볼릭 링크는 Path.is_symlink() 로 거부한다 (NFR-028)."""
        import branding_assets

        room = _room_dir(branding_root)
        room.mkdir(parents=True)
        (room / "evil.png").symlink_to(secret_file)

        assert branding_assets.resolve_asset_path("room1", "evil.png") is None
        assert branding_assets.list_assets("room1") == []
        # 링크 자체는 지우지 않았고, 대상 파일도 그대로다.
        assert secret_file.read_bytes().startswith(b"SQLite format 3")

    def test_resolve_rejects_symlink_that_points_inside_the_room(self, branding_root):
        """심볼릭 링크 거부가 containment 검사에 업혀 가지 않는지 확인한다 (RL-004).

        위의 `test_resolve_rejects_symlink` 은 링크 대상이 룸 **바깥**이라
        `is_symlink()` 를 지워도 `is_relative_to()` 가 대신 막아 준다 — 즉 심볼릭
        링크 방어 자체는 검증되지 않는다. NFR-028 이 별도 통제로 명시한 항목이
        조용히 썩지 않도록, 두 방어가 갈라지는 유일한 입력(대상이 룸 **안**인
        링크)으로 `is_symlink()` 만 단독 검증한다.
        """
        import branding_assets

        branding_assets.save_asset("room1", "real.png", PNG_BYTES)
        room = _room_dir(branding_root)
        (room / "link.png").symlink_to(room / "real.png")

        # 대상이 룸 안이므로 containment 는 통과한다 — 막는 것은 is_symlink() 뿐이다.
        assert (room / "link.png").resolve().is_relative_to(room.resolve())
        assert branding_assets.resolve_asset_path("room1", "link.png") is None
        assert branding_assets.delete_asset("room1", "link.png") is False
        assert branding_assets.list_assets("room1") == ["real.png"]
        # 원본은 그대로 서빙 가능하다 (과잉 차단이 아님).
        assert branding_assets.resolve_asset_path("room1", "real.png") is not None

    def test_resolve_rejects_symlink_to_sibling_room(self, branding_root):
        """다른 룸을 가리키는 링크도 서빙되지 않는다 (룸 간 격리)."""
        import branding_assets

        branding_assets.save_asset("roomB", "secret.png", PNG_BYTES)
        room_a = _room_dir(branding_root, "roomA")
        room_a.mkdir(parents=True)
        (room_a / "peek.png").symlink_to(branding_root / "roomB" / "secret.png")

        assert branding_assets.resolve_asset_path("roomA", "peek.png") is None
        assert branding_assets.list_assets("roomA") == []

    def test_dotdot_in_filename_is_rejected_not_stripped(self, branding_root):
        """`..` 는 제거가 아니라 거부다 — 구분자가 없어도 마찬가지 (NFR-028).

        구분자 제거만으로는 `..` 이 살아남는 입력이 있으므로, 시그니처 자체를
        거부하는 규칙이 독립적으로 성립하는지 확인한다.
        """
        import branding_assets

        for name in ("a..b.png", "..logo.png", "logo...png"):
            with pytest.raises(ValueError) as exc:
                branding_assets.save_asset("room1", name, PNG_BYTES)
            assert ".." in str(exc.value)
            assert branding_assets.resolve_asset_path("room1", name) is None
        assert not branding_root.exists()

    def test_rejected_public_requests_do_not_write_server_logs(
        self, branding_root, capsys
    ):
        """공개 라우트라 잘못된 요청만으로 로그가 오염되면 안 된다.

        `resolve_asset_path` 의 `except ValueError` 가 `except Exception`
        (로그를 남기는 쪽)으로 흡수되면 인증 없는 `/branding/...` 요청 하나로
        서버 로그를 무한히 부풀릴 수 있다. 그 분기를 고정한다.
        """
        import branding_assets

        capsys.readouterr()  # 이전 출력 비우기
        for name in HOSTILE_NAMES:
            assert branding_assets.resolve_asset_path("room1", name) is None
            assert branding_assets.list_assets("../..") == []

        assert capsys.readouterr().out == ""

    def test_resolve_rejects_directory(self, branding_root):
        """디렉터리는 파일이 아니므로 None."""
        import branding_assets

        room = _room_dir(branding_root)
        (room / "nested.png").mkdir(parents=True)

        assert branding_assets.resolve_asset_path("room1", "nested.png") is None

    @pytest.mark.parametrize("room_id", ["../..", "/etc", "a/b", "..\\other"])
    def test_room_id_traversal_rejected(self, branding_root, secret_file, room_id):
        """room_id 도 신뢰할 수 없는 입력 — 파일명과 동일하게 봉인한다."""
        import branding_assets

        assert branding_assets.resolve_asset_path(room_id, "logo.png") is None
        assert branding_assets.list_assets(room_id) == []
        assert branding_assets.delete_asset(room_id, "logo.png") is False
        with pytest.raises(ValueError):
            branding_assets.save_asset(room_id, "logo.png", PNG_BYTES)
        assert secret_file.exists()


# ---------------------------------------------------------------------------
# RL-015 — "예외를 던지지 않는다" 계약을 퍼징으로 검증
# ---------------------------------------------------------------------------
class TestHostileInputNeverExplodes:
    @pytest.mark.parametrize("filename", HOSTILE_NAMES)
    def test_resolve_asset_path_returns_none_and_never_raises(
        self, branding_root, filename
    ):
        import branding_assets

        assert branding_assets.resolve_asset_path("room1", filename) is None

    @pytest.mark.parametrize("filename", HOSTILE_NAMES)
    def test_delete_asset_returns_false_and_never_raises(self, branding_root, filename):
        import branding_assets

        assert branding_assets.delete_asset("room1", filename) is False

    @pytest.mark.parametrize("room_id", HOSTILE_ROOM_IDS)
    def test_list_assets_returns_empty_and_never_raises(self, branding_root, room_id):
        import branding_assets

        assert branding_assets.list_assets(room_id) == []

    @pytest.mark.parametrize("room_id", HOSTILE_ROOM_IDS)
    def test_resolve_with_hostile_room_id_returns_none(self, branding_root, room_id):
        import branding_assets

        assert branding_assets.resolve_asset_path(room_id, "logo.png") is None

    @pytest.mark.parametrize("filename", REJECTED_NAMES)
    def test_save_asset_raises_valueerror_only(self, branding_root, filename):
        """거부는 반드시 ValueError — TypeError/OSError 가 새어 나오면 안 된다.

        (RL-015) 퍼징 결과: 타입 체크가 없으면 `re.sub` 이 TypeError,
        255바이트 초과 이름은 `OSError(ENAMETOOLONG)`, `\\x00` 은 ValueError 를
        낸다. 세 갈래를 모두 ValueError 로 정규화하는 것이 이 함수의 계약이다.
        """
        import branding_assets

        try:
            branding_assets.save_asset("room1", filename, PNG_BYTES)
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover - 실패 시 진단용
            pytest.fail(
                f"ValueError 가 아닌 예외가 누출됨: {type(exc).__name__}: {exc}"
            )
        pytest.fail("거부되어야 할 입력이 저장되었다")

    @pytest.mark.parametrize("filename", NORMALISED_NAMES)
    def test_normalised_names_are_stored_under_a_safe_ascii_name(
        self, branding_root, filename
    ):
        """길이 절단/문자 제거로 통과하는 경계 입력은 안전한 이름으로 저장된다."""
        import branding_assets

        stored = branding_assets.save_asset("room1", filename, PNG_BYTES)

        assert len(stored.encode()) <= 255
        assert stored.endswith(".png")
        # 저장된 이름은 화이트리스트 문자만 포함한다.
        assert re.fullmatch(r"[A-Za-z0-9._-]+", stored)
        assert branding_assets.resolve_asset_path("room1", stored) is not None

    @pytest.mark.parametrize("room_id", HOSTILE_ROOM_IDS)
    def test_save_asset_with_hostile_room_id_raises_valueerror_only(
        self, branding_root, room_id
    ):
        import branding_assets

        try:
            branding_assets.save_asset(room_id, "logo.png", PNG_BYTES)
        except ValueError:
            return
        except Exception as exc:  # pragma: no cover - 실패 시 진단용
            pytest.fail(
                f"ValueError 가 아닌 예외가 누출됨: {type(exc).__name__}: {exc}"
            )
        pytest.fail("거부되어야 할 room_id 가 저장되었다")

    def test_overlong_but_otherwise_valid_name_is_truncated_not_oserror(
        self, branding_root
    ):
        """255바이트 초과 이름 → OSError 대신 절단 저장 (RL-015 실측 근거)."""
        import branding_assets

        stored = branding_assets.save_asset("room1", "a" * 300 + ".png", PNG_BYTES)

        assert len(stored.encode()) <= 255
        assert stored.endswith(".png")
        resolved = branding_assets.resolve_asset_path("room1", stored)
        assert resolved is not None
        assert resolved.read_bytes() == PNG_BYTES
        # 정규화가 대칭이므로 원래의 긴 이름으로도 같은 파일에 도달한다.
        assert (
            branding_assets.resolve_asset_path("room1", "a" * 300 + ".png") == resolved
        )

    def test_non_bytes_payload_raises_valueerror(self, branding_root):
        import branding_assets

        for payload in (None, "not-bytes", 12, ["x"]):
            with pytest.raises(ValueError):
                branding_assets.save_asset("room1", "logo.png", payload)
        assert not branding_root.exists()


# ---------------------------------------------------------------------------
# TC-054 — 2 MB 상한
# ---------------------------------------------------------------------------
class TestSizeCap:
    def test_exactly_max_bytes_is_accepted(self, branding_root):
        import branding_assets
        from branding_assets import MAX_ASSET_BYTES

        data = PNG_SIG + b"\x00" * (MAX_ASSET_BYTES - len(PNG_SIG))
        assert len(data) == MAX_ASSET_BYTES

        stored = branding_assets.save_asset("room1", "big.png", data)
        assert stored == "big.png"
        assert (_room_dir(branding_root) / "big.png").stat().st_size == MAX_ASSET_BYTES

    def test_one_byte_over_cap_rejected_and_file_count_unchanged(self, branding_root):
        """TC-054 / AC: 2 MB + 1 byte → ValueError, 부분 파일도 남지 않는다."""
        import branding_assets
        from branding_assets import MAX_ASSET_BYTES

        branding_assets.save_asset("room1", "keep.png", PNG_BYTES)
        room = _room_dir(branding_root)
        before = _file_count(room)

        data = PNG_SIG + b"\x00" * (MAX_ASSET_BYTES + 1 - len(PNG_SIG))
        with pytest.raises(ValueError) as exc:
            branding_assets.save_asset("room1", "toobig.png", data)

        assert "2" in str(exc.value)  # 사람이 읽을 수 있는 용량 안내
        assert _file_count(room) == before
        assert not (room / "toobig.png").exists()

    def test_three_mb_png_rejected_before_directory_is_created(self, branding_root):
        """AC 원문: 3 MB PNG → ValueError, 디스크에 부분 파일조차 없다."""
        import branding_assets

        data = PNG_SIG + b"\x00" * (3 * 1024 * 1024)
        with pytest.raises(ValueError):
            branding_assets.save_asset("room1", "huge.png", data)

        assert not branding_root.exists()


# ---------------------------------------------------------------------------
# 확장자 화이트리스트 + 내용 일치 (매직바이트)
# ---------------------------------------------------------------------------
class TestExtensionAndContentMatch:
    def test_three_valid_samples_stored_three_mismatches_rejected(self, branding_root):
        """TC 원문: 정상 3건만 저장되고 확장자 불일치 3건은 거부된다."""
        import branding_assets

        assert branding_assets.save_asset("room1", "a.png", PNG_BYTES) == "a.png"
        assert branding_assets.save_asset("room1", "b.jpg", JPEG_BYTES) == "b.jpg"
        assert branding_assets.save_asset("room1", "c.svg", SVG_BYTES) == "c.svg"

        mismatches = [
            ("d.png", JPEG_BYTES),  # PNG 확장자 + JPEG 내용
            ("e.jpg", b"<html><body>hi</body></html>"),  # JPEG 확장자 + HTML
            ("f.svg", PNG_BYTES),  # SVG 확장자 + PNG 내용
        ]
        for name, data in mismatches:
            with pytest.raises(ValueError):
                branding_assets.save_asset("room1", name, data)

        assert branding_assets.list_assets("room1") == ["a.png", "b.jpg", "c.svg"]

    def test_png_extension_with_non_png_signature_rejected(self, branding_root):
        """AC 원문: 확장자는 .png 인데 시그니처가 아니면 거부."""
        import branding_assets

        with pytest.raises(ValueError):
            branding_assets.save_asset("room1", "fake.png", b"GIF89a" + b"\x00" * 32)
        assert branding_assets.list_assets("room1") == []

    def test_jpeg_alias_extension_accepted(self, branding_root):
        import branding_assets

        assert branding_assets.save_asset("room1", "x.jpeg", JPEG_BYTES) == "x.jpeg"

    def test_uppercase_extension_normalised(self, branding_root):
        import branding_assets

        stored = branding_assets.save_asset("room1", "LOGO.PNG", PNG_BYTES)
        assert stored == "LOGO.png"
        assert branding_assets.resolve_asset_path("room1", stored) is not None

    def test_svg_with_leading_whitespace_and_xml_declaration_accepted(
        self, branding_root
    ):
        import branding_assets

        stored = branding_assets.save_asset("room1", "decl.svg", SVG_XML_DECL_BYTES)
        assert stored == "decl.svg"

    def test_svg_with_utf8_bom_accepted(self, branding_root):
        import branding_assets

        assert (
            branding_assets.save_asset("room1", "bom.svg", SVG_BOM_BYTES) == "bom.svg"
        )

    def test_svg_that_is_actually_html_rejected(self, branding_root):
        import branding_assets

        with pytest.raises(ValueError):
            branding_assets.save_asset(
                "room1", "evil.svg", b"<script>alert(1)</script><svg/>"
            )
        assert branding_assets.list_assets("room1") == []

    @pytest.mark.parametrize("name", ["logo.gif", "logo.db", "logo.svgz", "logo"])
    def test_non_whitelisted_extensions_rejected(self, branding_root, name):
        import branding_assets

        with pytest.raises(ValueError) as exc:
            branding_assets.save_asset("room1", name, PNG_BYTES)
        assert str(exc.value)  # 사람이 읽을 수 있는 사유 문자열


# ---------------------------------------------------------------------------
# 룸당 12개 상한
# ---------------------------------------------------------------------------
class TestAssetCountCap:
    def test_thirteenth_upload_rejected_and_list_stays_twelve(self, branding_root):
        """TC 원문 + AC: 13번째 저장 거부, 사유에 개수 제한 언급, 목록 길이 12."""
        import branding_assets
        from branding_assets import MAX_ASSETS_PER_ROOM

        assert MAX_ASSETS_PER_ROOM == 12
        for i in range(MAX_ASSETS_PER_ROOM):
            branding_assets.save_asset("room1", f"logo{i}.png", PNG_BYTES)
        assert len(branding_assets.list_assets("room1")) == 12

        with pytest.raises(ValueError) as exc:
            branding_assets.save_asset("room1", "one-too-many.png", PNG_BYTES)

        reason = str(exc.value)
        assert "12" in reason
        assert "개" in reason  # 개수 제한임이 드러나야 한다
        assert len(branding_assets.list_assets("room1")) == 12
        assert not (_room_dir(branding_root) / "one-too-many.png").exists()

    def test_cap_frees_up_after_delete(self, branding_root):
        import branding_assets
        from branding_assets import MAX_ASSETS_PER_ROOM

        for i in range(MAX_ASSETS_PER_ROOM):
            branding_assets.save_asset("room1", f"logo{i}.png", PNG_BYTES)

        assert branding_assets.delete_asset("room1", "logo0.png") is True
        stored = branding_assets.save_asset("room1", "fresh.png", PNG_BYTES)
        assert stored == "fresh.png"
        assert len(branding_assets.list_assets("room1")) == 12


# ---------------------------------------------------------------------------
# 충돌 회피 (덮어쓰기 금지)
# ---------------------------------------------------------------------------
class TestCollisionAvoidance:
    def test_same_name_twice_does_not_overwrite(self, branding_root):
        """AC: 두 번째 저장은 logo-2.png 를 반환하고 첫 파일을 보존한다."""
        import branding_assets

        first_bytes = PNG_SIG + b"\x01" * 16
        second_bytes = PNG_SIG + b"\x02" * 16

        assert (
            branding_assets.save_asset("room1", "logo.png", first_bytes) == "logo.png"
        )
        second = branding_assets.save_asset("room1", "logo.png", second_bytes)

        assert second == "logo-2.png"
        room = _room_dir(branding_root)
        assert (room / "logo.png").read_bytes() == first_bytes
        assert (room / "logo-2.png").read_bytes() == second_bytes

    def test_third_collision_increments_suffix(self, branding_root):
        import branding_assets

        names = [
            branding_assets.save_asset("room1", "logo.png", PNG_BYTES) for _ in range(3)
        ]
        assert names == ["logo.png", "logo-2.png", "logo-3.png"]

    def test_unsanitisable_stem_falls_back_to_generic_name(self, branding_root):
        """한글 등 화이트리스트 밖 문자만 남은 stem 은 기본 이름으로 대체된다."""
        import branding_assets

        first = branding_assets.save_asset("room1", "로고.png", PNG_BYTES)
        second = branding_assets.save_asset("room1", "주최.png", PNG_BYTES)

        assert first.endswith(".png")
        assert second.endswith(".png")
        assert first != second  # 덮어쓰지 않는다
        assert set(branding_assets.list_assets("room1")) == {first, second}


# ---------------------------------------------------------------------------
# list_assets / delete_asset
# ---------------------------------------------------------------------------
class TestListAndDelete:
    def test_list_unknown_room_returns_empty(self, branding_root):
        import branding_assets

        assert branding_assets.list_assets("no-such-room") == []

    def test_list_is_sorted_and_excludes_dirs_and_other_extensions(self, branding_root):
        import branding_assets

        branding_assets.save_asset("room1", "z.png", PNG_BYTES)
        branding_assets.save_asset("room1", "a.svg", SVG_BYTES)
        room = _room_dir(branding_root)
        (room / "notes.txt").write_text("ignore me")
        (room / "sub.png").mkdir()

        assert branding_assets.list_assets("room1") == ["a.svg", "z.png"]

    def test_rooms_are_isolated_from_each_other(self, branding_root):
        import branding_assets

        branding_assets.save_asset("roomA", "a.png", PNG_BYTES)
        branding_assets.save_asset("roomB", "b.png", PNG_BYTES)

        assert branding_assets.list_assets("roomA") == ["a.png"]
        assert branding_assets.list_assets("roomB") == ["b.png"]
        assert branding_assets.resolve_asset_path("roomA", "b.png") is None

    def test_delete_removes_file_and_returns_true(self, branding_root):
        import branding_assets

        branding_assets.save_asset("room1", "logo.png", PNG_BYTES)
        assert branding_assets.delete_asset("room1", "logo.png") is True
        assert branding_assets.list_assets("room1") == []
        assert not (_room_dir(branding_root) / "logo.png").exists()

    def test_delete_missing_file_returns_false(self, branding_root):
        import branding_assets

        assert branding_assets.delete_asset("room1", "ghost.png") is False

    def test_delete_symlink_leaves_target_untouched(self, branding_root, secret_file):
        import branding_assets

        room = _room_dir(branding_root)
        room.mkdir(parents=True)
        (room / "evil.png").symlink_to(secret_file)

        assert branding_assets.delete_asset("room1", "evil.png") is False
        assert secret_file.exists()


# ---------------------------------------------------------------------------
# 2단 방어의 두 번째 단계 — sanitize 를 우회해도 봉인이 유지되는가
# ---------------------------------------------------------------------------
class TestContainmentStageIndependently:
    """sanitize(1단)를 무력화한 상태에서 resolve/is_relative_to(2단)만 검증한다.

    두 단계가 각각 단독으로 성립해야 "2단 방어"라고 부를 수 있다. 1단이
    통과시키는 입력이 없어서 2단이 한 번도 실행되지 않으면, 나중에 1단이
    완화됐을 때 아무도 눈치채지 못한다 (NFR-028).
    """

    def test_containment_holds_when_filename_sanitiser_is_bypassed(
        self, branding_root, secret_file, monkeypatch
    ):
        import branding_assets

        branding_assets.save_asset("room1", "logo.png", PNG_BYTES)
        monkeypatch.setattr(branding_assets, "_sanitize_filename", lambda name: name)

        assert branding_assets.resolve_asset_path("room1", "../../app.db") is None
        assert secret_file.read_bytes().startswith(b"SQLite format 3")

    def test_containment_holds_when_room_sanitiser_is_bypassed(
        self, branding_root, secret_file, monkeypatch
    ):
        import branding_assets

        monkeypatch.setattr(branding_assets, "_sanitize_room_id", lambda rid: rid)

        # 확장자는 화이트리스트 안의 값을 써야 1단(확장자 검사)에서 막히지 않고
        # 2단(containment)까지 실제로 도달한다 (RL-004: vacuous pass 방지).
        assert branding_assets.resolve_asset_path("../..", "logo.png") is None
        assert branding_assets.list_assets("../..") == []
        assert secret_file.exists()

    def test_save_rejects_when_containment_check_fails(
        self, branding_root, secret_file, monkeypatch
    ):
        import branding_assets

        monkeypatch.setattr(branding_assets, "_sanitize_room_id", lambda rid: rid)

        with pytest.raises(ValueError):
            branding_assets.save_asset("../escaped", "logo.png", PNG_BYTES)
        assert secret_file.read_bytes().startswith(b"SQLite format 3")


# ---------------------------------------------------------------------------
# IO 실패 — RL-006 (상세는 서버 로그, 호출자에겐 일반 메시지)
# ---------------------------------------------------------------------------
class TestIoFailureIsLoggedNotLeaked:
    def test_save_io_failure_raises_generic_valueerror_and_logs(
        self, tmp_path, monkeypatch, capsys
    ):
        """루트가 파일이라 mkdir 이 OSError → ValueError 로 정규화 + 서버 로그."""
        import branding_assets

        root = tmp_path / "branding-is-a-file"
        root.write_text("not a directory")
        monkeypatch.setenv("BRANDING_DIR", str(root))

        with pytest.raises(ValueError) as exc:
            branding_assets.save_asset("room1", "logo.png", PNG_BYTES)

        message = str(exc.value)
        assert "Errno" not in message
        assert str(root) not in message
        assert "[Branding] save failed" in capsys.readouterr().out

    def test_delete_io_failure_returns_false_and_logs(
        self, branding_root, monkeypatch, capsys
    ):
        import branding_assets

        branding_assets.save_asset("room1", "logo.png", PNG_BYTES)

        def _boom(self, *_args, **_kwargs):
            raise OSError("read-only filesystem at /secret/mount")

        monkeypatch.setattr(Path, "unlink", _boom)

        assert branding_assets.delete_asset("room1", "logo.png") is False
        assert "[Branding] delete failed" in capsys.readouterr().out
        # 파일은 여전히 남아 있고, 목록에서도 사라지지 않는다.
        assert branding_assets.list_assets("room1") == ["logo.png"]


# ---------------------------------------------------------------------------
# 응답 헤더 계약 (aiohttp 없이 순수 검증)
# ---------------------------------------------------------------------------
class TestAssetHeaders:
    @pytest.mark.parametrize(
        ("name", "content_type"),
        [
            ("logo.png", "image/png"),
            ("logo.jpg", "image/jpeg"),
            ("logo.jpeg", "image/jpeg"),
            ("logo.svg", "image/svg+xml"),
        ],
    )
    def test_content_type_mapping(self, name, content_type):
        from branding_assets import build_asset_headers

        assert build_asset_headers(name)["Content-Type"] == content_type

    def test_cache_control_is_five_minutes(self):
        from branding_assets import build_asset_headers

        assert build_asset_headers("logo.png")["Cache-Control"] == "public, max-age=300"

    def test_svg_carries_restrictive_csp(self):
        """AC: SVG 내부 스크립트가 실행되지 않도록 CSP 를 붙인다."""
        from branding_assets import build_asset_headers

        headers = build_asset_headers("sponsor.svg")
        assert (
            headers["Content-Security-Policy"]
            == "default-src 'none'; style-src 'unsafe-inline'"
        )

    def test_non_svg_has_no_csp_header(self):
        from branding_assets import build_asset_headers

        assert "Content-Security-Policy" not in build_asset_headers("logo.png")

    def test_unknown_extension_falls_back_to_octet_stream(self):
        from branding_assets import build_asset_headers

        assert (
            build_asset_headers("weird.bin")["Content-Type"]
            == "application/octet-stream"
        )

    @pytest.mark.parametrize(
        "name",
        ["logo.png", "logo.jpg", "logo.jpeg", "sponsor.svg", "weird.bin"],
    )
    def test_nosniff_is_always_present(self, name):
        """R-04 (ISSUE-38 리뷰): 업로드 바이트의 content sniffing 을 막는다.

        확장자와 무관하게 항상 붙어야 한다 — 알 수 없는 확장자야말로
        브라우저가 타입을 추측할 여지가 가장 큰 경우다.
        """
        from branding_assets import build_asset_headers

        assert build_asset_headers(name)["X-Content-Type-Options"] == "nosniff"
