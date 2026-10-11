"""WebSocket 인증 경로용 공유 fake 하네스 (ISSUE-55).

ISSUE-55 가 `_authenticate_client` 에 **핸드셰이크 쿠키 검증**을 추가하면서,
fake websocket 이 `request.headers` 를 갖추지 않으면 **모든 인증 테스트가 조용히
거절로 바뀐다.** 기존 하네스는 `ws = AsyncMock()` 이라
`ws.request.headers.get("Cookie")` 가 MagicMock 을 돌려주고, 프로덕션 코드가
`isinstance(raw, str)` 에서 걸러 `None` 으로 degrade 한다 — 즉 테스트가 깨지는
것이 아니라 **다른 이유로 통과**하게 된다. 그게 더 위험하다.

그래서 쿠키를 붙이는 자리를 **한 곳**으로 모았다 (RL-001). 네 곳에 흩어져 있던
`_make_websocket` 사본들이 각자 쿠키를 붙이면 반드시 갈라진다.

**영향 받는 하네스는 4개다**:
- `tests/test_websocket_auth.py` 의 `_make_websocket` (호출 14곳)
- `tests/test_rooms_db.py` 의 `TestWebSocketAuthClosedRoom._make_websocket`
- `tests/test_room_manager.py` 의 인라인 `AsyncMock`
- `tests/test_coverage_gaps.py` 의 인라인 `AsyncMock`

**`tests/test_rate_limit.py` 와 `tests/test_websocket_handler.py` 는 영향이 없다** —
두 파일은 `_authenticate_client` 자체를 patch 하므로 인증 경로를 타지 않는다.
6개를 다 고치려다 시간을 버리지 않도록 여기 적어 둔다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

# 기본값과 "명시적으로 쿠키 없음"을 구분하기 위한 sentinel. `cookie=None` 은
# 미인증 케이스를 **의도적으로** 만드는 신호이고, 인자를 생략한 것은 "인증된
# 오퍼레이터" 를 뜻한다.
_DEFAULT = object()


def signed_cookie_value(
    user_id: int,
    username: str = "testuser",
    token: str = "t0ken",
) -> str:
    """`auth._sign_cookie` 로 서명한 `user_session` 쿠키 값.

    프로덕션과 **같은** 서명 함수를 쓴다 — 사본을 만들면 서명 체계가 갈라져
    테스트가 통과하는데 실제로는 거절되는(또는 반대의) 상황이 생긴다.
    """
    from auth import _sign_cookie

    return _sign_cookie(user_id, username, token)


def cookie_value_signed_with(
    secret: str,
    user_id: int | str,
    username: str = "testuser",
    token: str = "t0ken",
) -> str:
    """**다른** `SESSION_SECRET` 으로 서명한 쿠키 값 (부정 테스트용).

    `auth._compute_hmac` 은 환경변수에서 secret 을 읽으므로, 다른 secret 으로
    서명한 쿠키를 만들려면 서명 식을 여기서 한 번 더 쓸 수밖에 없다. 식이
    `auth._sign_cookie` / `_compute_hmac` 과 어긋나면 이 함수가 만든 쿠키가
    "우연히" 거절돼 테스트가 거짓 통과하므로, 두 구현이 같은 형식
    (`user_id:username:token` 을 HMAC-SHA256 hexdigest 로 서명하고 `:` 로 이어
    붙임)을 유지하는지를 `test_websocket_auth.py` 가 양성 대조로 확인한다.
    """
    payload = f"{user_id}:{username}:{token}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def make_websocket(
    auth_message: dict[str, Any] | None,
    *,
    cookie: Any = _DEFAULT,
) -> AsyncMock:
    """`recv()` 가 주어진 auth 프레임을 돌려주는 fake websocket.

    `cookie` 를 생략하면 **auth 프레임이 주장하는 신원과 일치하는 유효 서명
    쿠키**를 자동으로 붙인다. 그래야 ISSUE-55 이전에 쓰인 테스트들이 원래
    의도대로 — 존재하지 않는 user_id, 비활성 사용자, 사용자명 불일치 같은 **DB
    단계**의 거절을 — 계속 검증한다. 쿠키 게이트에서 미리 걸려 버리면 그
    테스트들은 아무것도 증명하지 않게 된다.

    Args:
        auth_message: `recv()` 가 돌려줄 프레임. `None` 이면 쿠키도 기본 신원으로
            만든다.
        cookie: `None` → Cookie 헤더 없음(미인증 케이스). 문자열 → 그 값을
            `user_session` 쿠키 값으로 사용. 완전한 헤더 문자열을 직접 넣으려면
            `raw_cookie_header=` 대신 이 인자에 `"user_session=...; other=1"`
            형태를 주면 된다 — `=` 가 포함되어 있으면 헤더 전체로 간주한다.
    """
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value=json.dumps(auth_message))
    ws.send = AsyncMock()

    if cookie is _DEFAULT:
        user = (auth_message or {}).get("user") or {}
        # **브라우저가 실제로 저장하는 형태**로 만든다 — percent-encoded.
        # `CookieManager` 의 JS 가 `encodeURIComponent` 를 거치므로 구분자 `:` 가
        # `%3A` 로 바뀐다 (e2e 실측). 서버측 `_sign_cookie` 출력(리터럴 `:`)을
        # 그대로 쓰면 하네스가 프로덕션보다 **관대해져서**, `unquote` 누락 같은
        # 결함을 통과시킨다. 실제로 한 번 통과시켰다.
        cookie = signed_cookie_value(
            user.get("id", 1), user.get("username", "testuser")
        ).replace(":", "%3A")

    if cookie is None:
        header_value = None
    elif "=" in str(cookie):
        # 이미 `name=value` 형태면 헤더 전체로 쓴다 (다중 쿠키 케이스).
        header_value = str(cookie)
    else:
        header_value = f"user_session={cookie}"

    headers: dict[str, str] = {}
    if header_value is not None:
        headers["Cookie"] = header_value

    # 실제 `websockets` 15.0.1 의 핸들러 인자는 `ServerConnection` 이고 핸드셰이크
    # 헤더는 `connection.request.headers` 다 (legacy `.request_headers` 가 아니다).
    # 프로덕션 코드가 읽는 경로와 **같은 모양**을 흉내 낸다.
    ws.request = SimpleNamespace(headers=headers)
    return ws


def sent_messages(ws: AsyncMock) -> list[dict[str, Any]]:
    """fake websocket 의 `send()` 호출에서 파싱한 JSON 메시지 목록."""
    return [json.loads(call.args[0]) for call in ws.send.call_args_list]
