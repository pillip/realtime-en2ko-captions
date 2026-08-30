"""브랜딩 에셋 라우트의 404 계약 단위 테스트 (ISSUE-38 / ISSUE-43).

`GET /branding/{room_id}/{filename}` 은 미인증 공개 라우트다. 200 의 조건은
`branding_assets.resolve_asset_path` 가 경로를 돌려주는 경우뿐이며, 그 외에는
전부 404 여야 한다 — 이 분기가 무너지면 존재하지 않는 파일 요청에 200 이
나가면서 룸/파일 존재 여부가 열거 가능해진다.

`tests/test_sse_broadcast.py` 에도 이 라우트의 헤더/traversal 테스트가 있지만,
여기서는 `sse_broadcast` 를 전혀 import 하지 않고 라우트 하나만 올린 최소
앱으로 검증한다 — SSE 서버의 상태(룸 저장소, 브로드캐스터)와 무관하게
성립해야 하는 계약이기 때문이다.

Note: `tmp_path` + `BRANDING_DIR` monkeypatch 로 격리된다. 외부 네트워크 없음.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# database -> streamlit 의존성을 끌고 오는 다른 테스트와 동일한 방어 패턴.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_BYTES = PNG_SIG + b"\x33" * 48


@pytest.fixture
def branding_root(tmp_path, monkeypatch):
    root = tmp_path / "branding"
    monkeypatch.setenv("BRANDING_DIR", str(root))
    return root


def _app() -> web.Application:
    """브랜딩 라우트 하나만 등록한 최소 aiohttp 앱."""
    from branding_routes import handle_branding_asset

    app = web.Application()
    app.router.add_get("/branding/{room_id}/{filename}", handle_branding_asset)
    return app


class TestBrandingAssetNotFound:
    @pytest.mark.asyncio
    async def test_stored_asset_is_served_with_200(self, branding_root):
        """저장된 파일은 200 으로 나간다 — 404 단언이 무의미하지 않음을 보인다."""
        import branding_assets

        stored = branding_assets.save_asset("r1", "logo.png", PNG_BYTES)

        async with TestClient(TestServer(_app())) as client:
            resp = await client.get(f"/branding/r1/{stored}")
            assert resp.status == 200
            assert await resp.read() == PNG_BYTES

    @pytest.mark.asyncio
    async def test_unresolvable_asset_returns_404_not_a_body_with_200(
        self, branding_root
    ):
        """`resolve_asset_path` 가 None 이면 상태 코드가 404 여야 한다.

        Guard: branding_routes.handle_branding_asset#not_found

        본문만 "not found" 로 두고 200 을 돌려주면 캐시/프록시/클라이언트가
        정상 응답으로 취급하고, 룸·파일의 존재 여부가 상태 코드로 새어 나가지
        않는다는 열거 방지 계약도 깨진다. 본문이 아니라 **상태 코드**를 고정한다.
        """
        import branding_assets

        branding_assets.save_asset("r1", "logo.png", PNG_BYTES)

        async with TestClient(TestServer(_app())) as client:
            for path in (
                "/branding/r1/missing.png",  # 없는 파일
                "/branding/no-such-room/logo.png",  # 없는 룸
                "/branding/r1/app.db",  # 화이트리스트 밖 확장자
                "/branding/r1/..%2F..%2Fapp.db",  # traversal
            ):
                resp = await client.get(path)
                assert resp.status == 404, path
                body = await resp.text()
                assert str(branding_root) not in body
                assert "Traceback" not in body
