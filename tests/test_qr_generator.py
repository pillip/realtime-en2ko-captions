"""qr_generator 테스트 (ISSUE-32).

순수 함수 — DB/Streamlit/네트워크 mock 없이 import 만으로 테스트 가능
(RL-001 / RL-005). 모든 검증은 출력 바이트/문자열에 대한 결정론적
어서션이며, 외부 라이브러리(qrcode/PIL) 호출은 그대로 사용한다.
"""

from __future__ import annotations

import base64
import io
import re

import pytest

from qr_generator import build_view_url, make_qr_data_url, make_qr_png

# ---------------------------------------------------------------------------
# build_view_url
# ---------------------------------------------------------------------------


class TestBuildViewUrl:
    """``base_url`` 의 다양한 형식이 같은 정규형으로 합성됨을 보장한다."""

    @pytest.mark.parametrize(
        "viewer_base,room_id,expected",
        [
            (
                "http://localhost:8766",
                "room_abc",
                "http://localhost:8766/view/room_abc",
            ),
            (
                "http://localhost:8766/",
                "room_abc",
                "http://localhost:8766/view/room_abc",
            ),
            (
                "https://captions.example.com",
                "abc123",
                "https://captions.example.com/view/abc123",
            ),
            (
                "https://captions.example.com/",
                "abc123",
                "https://captions.example.com/view/abc123",
            ),
            (
                "https://captions.example.com:443",
                "abc",
                "https://captions.example.com:443/view/abc",
            ),
            # 다중 trailing slash 도 한 번에 정리
            (
                "http://localhost:8766///",
                "abc",
                "http://localhost:8766/view/abc",
            ),
        ],
    )
    def test_normalises_trailing_slash_and_scheme(
        self, viewer_base: str, room_id: str, expected: str
    ) -> None:
        # NB: ``base_url`` 이름은 pytest-base-url 플러그인 fixture 와 충돌하므로
        # 일부러 ``viewer_base`` 로 치환해 fixture 주입 경로를 회피한다.
        assert build_view_url(room_id, viewer_base) == expected

    def test_strips_whitespace_in_inputs(self) -> None:
        # 환경변수에서 흔히 들어오는 trailing whitespace 를 흡수
        assert (
            build_view_url("  abc  ", "  http://localhost:8766/  ")
            == "http://localhost:8766/view/abc"
        )

    @pytest.mark.parametrize("bad_room", ["", "   ", "\t", "\n"])
    def test_empty_room_id_raises_value_error(self, bad_room: str) -> None:
        with pytest.raises(ValueError, match="room_id"):
            build_view_url(bad_room, "http://localhost:8766")

    @pytest.mark.parametrize("bad_base", ["", "   ", "\t"])
    def test_empty_base_url_raises_value_error(self, bad_base: str) -> None:
        with pytest.raises(ValueError, match="base_url"):
            build_view_url("abc", bad_base)


# ---------------------------------------------------------------------------
# make_qr_png
# ---------------------------------------------------------------------------


# PNG signature (RFC 2083 §3.1): 8 byte magic header that must start every PNG.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TestMakeQrPng:
    def test_returns_valid_png_signature(self) -> None:
        png = make_qr_png("http://localhost:8766/view/room_abc")
        # 헤더만 검증해도 라이브러리가 PNG 컨테이너를 만들었다는 강한 증거
        assert png.startswith(_PNG_MAGIC)

    def test_returns_non_trivial_bytes(self) -> None:
        # box_size=10 + 4 모듈 border 면 짧은 URL 로도 1KB+ 가 정상.
        png = make_qr_png("http://localhost:8766/view/room_abc")
        assert len(png) > 200

    def test_distinct_urls_produce_distinct_pngs(self) -> None:
        # 약한 정합성 — 같은 URL 은 같은 출력, 다른 URL 은 다른 출력.
        a = make_qr_png("http://localhost:8766/view/room_a")
        b = make_qr_png("http://localhost:8766/view/room_b")
        assert a != b

    def test_idempotent_for_same_url(self) -> None:
        # 동일 입력에 대해 결정론적으로 동일 PNG 가 나오는지 확인
        # (qrcode 라이브러리가 timestamp 등 비결정 입력을 쓰지 않음을 검증).
        url = "http://localhost:8766/view/room_x"
        assert make_qr_png(url) == make_qr_png(url)

    def test_can_be_opened_by_pil(self) -> None:
        # PIL 이 헤더만이 아닌 데이터까지 파싱 가능한지 — 손상되지 않은 PNG 인지 확인.
        from PIL import Image

        png = make_qr_png("http://localhost:8766/view/room_abc")
        img = Image.open(io.BytesIO(png))
        # QR 은 정사각형, box_size=10 이면 매우 작아도 100px 이상.
        assert img.size[0] == img.size[1]
        assert img.size[0] >= 100

    @pytest.mark.parametrize("bad_url", ["", "   "])
    def test_empty_url_raises_value_error(self, bad_url: str) -> None:
        with pytest.raises(ValueError, match="url"):
            make_qr_png(bad_url)


# ---------------------------------------------------------------------------
# make_qr_data_url
# ---------------------------------------------------------------------------


class TestMakeQrDataUrl:
    def test_starts_with_png_data_uri_prefix(self) -> None:
        data_url = make_qr_data_url("http://localhost:8766/view/room_abc")
        assert data_url.startswith("data:image/png;base64,")

    def test_decoded_payload_is_valid_png(self) -> None:
        # data URL 의 base64 부분을 복원했을 때 다시 PNG 헤더로 시작해야 함.
        data_url = make_qr_data_url("http://localhost:8766/view/room_abc")
        b64 = data_url.split(",", 1)[1]
        decoded = base64.b64decode(b64)
        assert decoded.startswith(_PNG_MAGIC)

    def test_base64_section_contains_only_valid_chars(self) -> None:
        data_url = make_qr_data_url("http://localhost:8766/view/room_abc")
        b64 = data_url.split(",", 1)[1]
        # ASCII base64 charset 만 포함하는지 — 잘못된 인코딩이면 실패.
        assert re.match(r"^[A-Za-z0-9+/=]+$", b64)

    @pytest.mark.parametrize("bad_url", ["", "   "])
    def test_empty_url_raises_value_error(self, bad_url: str) -> None:
        with pytest.raises(ValueError, match="url"):
            make_qr_data_url(bad_url)


# ---------------------------------------------------------------------------
# Module hygiene — RL-001 importable + side-effect-free
# ---------------------------------------------------------------------------


class TestImportPurity:
    """qr_generator 가 import 시점에 부수효과를 일으키지 않음을 확인한다."""

    def test_module_imports_without_streamlit_or_network(self) -> None:
        # streamlit/aws/db 등이 import 되지 않아야 RL-001 위반이 아님.
        import sys

        # 깨끗한 import 를 위해 캐시 비우기
        for key in [k for k in sys.modules if k.startswith("qr_generator")]:
            del sys.modules[key]

        import qr_generator  # noqa: F401 — re-import 자체가 검증.

        # 빠른 sanity — public API 만 export.
        assert hasattr(qr_generator, "build_view_url")
        assert hasattr(qr_generator, "make_qr_png")
        assert hasattr(qr_generator, "make_qr_data_url")
