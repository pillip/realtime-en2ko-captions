"""
브랜딩 로고 정적 서빙 라우트 (ISSUE-38, FR-078).

``GET /branding/{room_id}/{filename}`` 한 개만 담당한다. 무대 페이지(ISSUE-40)와
뷰어 페이지가 aiohttp SSE 서버(기본 8766)에서 서빙되므로, 로고도 **같은 오리진**
에서 나가야 프록시/HTTPS 환경에서 mixed-origin 으로 깨지지 않는다.

검증·경로 봉인·헤더 결정은 전부 :mod:`branding_assets` (aiohttp 비의존, 순수
모듈)에 있고 여기 있는 핸들러는 얇은 래퍼다 (RL-001/RL-005). 덕분에 파일 IO
규칙은 HTTP 없이 단위 테스트되고, 이 모듈은 ``sse_broadcast`` 에 라우트 한 줄만
더한다 — 같은 파일을 건드리는 다른 작업과 충돌할 여지를 최소화하기 위한 분리다.

RL-006: 알 수 없는 룸/파일, traversal 시도, 내부 IO 실패는 전부 동일한 generic
404 로 응답한다. 상세는 ``[Branding]`` 접두사로 서버 로그에만 남긴다.
"""

from __future__ import annotations

from aiohttp import web

import branding_assets

# 어떤 실패 경로에서도 동일하게 나가는 본문. 내부 경로/예외 문자열을 담지
# 않으며, 존재하지 않는 파일과 거부된 요청을 구분해 주지도 않는다 (열거 방지).
_NOT_FOUND_TEXT = "not found"


async def handle_branding_asset(request: web.Request) -> web.StreamResponse:
    """저장된 로고 파일 1건을 서빙한다.

    200 조건은 :func:`branding_assets.resolve_asset_path` 가 경로를 돌려주는
    경우뿐이다 — 즉 sanitize·containment·심볼릭 링크·확장자 화이트리스트·존재
    여부를 모두 통과한 정규 파일. 그 외에는 전부 404 다.
    """
    room_id = request.match_info["room_id"]
    filename = request.match_info["filename"]

    try:
        path = branding_assets.resolve_asset_path(room_id, filename)
        if path is None:
            return web.Response(status=404, text=_NOT_FOUND_TEXT)
        body = path.read_bytes()
    except Exception as e:
        # resolve_asset_path 는 예외를 던지지 않는 계약이지만, read_bytes 는
        # 경합(삭제/권한 변경)으로 실패할 수 있다. RL-006: 로그만, 응답은 generic.
        print(f"[Branding] asset read failed (room={room_id!r}): {e!r}")
        return web.Response(status=404, text=_NOT_FOUND_TEXT)

    return web.Response(
        status=200, body=body, headers=branding_assets.build_asset_headers(path.name)
    )
