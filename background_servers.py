"""뷰어 SSE(8766) / 오퍼레이터 WS(8765) 서버를 띄우는 **프로세스 단위** 진입점 (#158).

왜 이 모듈이 따로 있나
----------------------
원래 두 서버는 `app.py` 안에서 `st.session_state` 게이트로 기동했다.
`st.session_state` 는 **브라우저 세션 단위**라 다음이 성립했다:

    아무도 :8501 을 열지 않으면 스크립트가 아예 실행되지 않고, 두 서버도 뜨지
    않는다.

그래서 컨테이너를 재시작하면 **오퍼레이터가 Streamlit 을 열기 전까지**
`/view/*`, `/stream/*`, `/stage/*`, `/ws` 가 전부 죽어 있었다. 재배포마다
반복됐고, 로컬에서도 그대로 재현된다. ALB 타겟그룹은 그 사이 unhealthy 이고,
QR 을 스캔한 관객은 흰 화면을 본다 — 복구 조건이 "운영자가 브라우저를 연다"
라는 사람 동작이었다.

이 모듈은 그 기동을 **프로세스 단위 멱등 함수**로 바꾼다. `serve.py` 가
Streamlit 보다 **먼저** 부르면 브라우저 없이도 두 포트가 열린다.
`streamlit run app.py` (개발 경로)에서도 `app.py` 가 그대로 부르므로 동작은
같다 — 다만 그때는 첫 세션 시점에 뜬다.

설계 메모
---------
- **프로세스 전역 멱등**: 락으로 감싸 두 번째 호출은 아무 일도 하지 않는다.
  Streamlit 스크립트는 세션마다/리런마다 실행되므로 이 성질이 필수다.
- `start_websocket_server` 는 이미 자체 싱글턴 가드가 있다
  (`websocket_handler._WS_SERVER_STARTED`, #84). `run_sse_server` 에는 없었고,
  그래서 두 번째 세션이 스레드를 또 띄워 8766 바인드에 실패하면
  `except Exception` 이 로그만 찍고 삼켰다. 여기서 한 겹 더 막는다.
- **기동 실패를 삼키지 않는다**: `started` / `error` 를 반환해 호출자가
  판정할 수 있게 한다. RL-006 은 "클라이언트에 내부 정보 노출 금지" 이지
  "서버가 실패를 모르게 하라" 가 아니다.
"""

from __future__ import annotations

import os
import threading
from typing import Any

_START_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "started": False,
    # WS 포트는 dict 로 공유한다 — 스레드가 실제 바인딩한 포트를 써 넣는다 (#84).
    "websocket_port_ref": {"port": None},
    "sse_port": None,
    "websocket_thread": None,
    "sse_thread": None,
    "errors": [],
}


def sse_port() -> int:
    """SSE 서버 포트 (환경변수 기준, 기본 8766).

    `os.getenv` 를 **호출 시점에** 읽는다 — import 시점에 고정하면 테스트가
    환경변수를 바꿔도 반영되지 않는다 (`DB_PATH` / `BRANDING_DIR` 과 같은 관례).
    """
    return int(os.getenv("SSE_PORT", "8766"))


def get_state() -> dict[str, Any]:
    """현재 기동 상태 스냅샷 (읽기 전용 용도)."""
    return dict(_STATE)


def start_background_servers() -> dict[str, Any]:
    """WS / SSE 서버를 데몬 스레드로 띄운다. **멱등** — 두 번째 호출은 no-op.

    Returns:
        상태 dict: ``started``, ``websocket_port_ref``, ``sse_port``, ``errors``.
    """
    with _START_LOCK:
        if _STATE["started"]:
            return dict(_STATE)

        # import 를 함수 안에서 한다 — 이 모듈이 import 되는 것만으로 무거운
        # 의존(streamlit, boto3)을 끌어오지 않게 한다. `serve.py` 는 서버를
        # 띄우기 전에 이 모듈을 import 하므로 import 비용이 기동 시간에 직결된다.
        from database import get_room_model
        from sse_broadcast import run_sse_server
        from websocket_handler import (
            attach_broadcast_metrics_repo,
            get_broadcast_manager,
            start_websocket_server,
        )

        errors: list[str] = []

        # --- WebSocket (번역 파이프라인) ---
        try:
            ws_thread = threading.Thread(
                target=start_websocket_server,
                args=(_STATE["websocket_port_ref"],),
                daemon=True,
                name="ws-server",
            )
            ws_thread.start()
            _STATE["websocket_thread"] = ws_thread
            print("[WebSocket] 서버 스레드 시작됨")
        except Exception as e:  # noqa: BLE001
            errors.append(f"websocket: {e!r}")
            print(f"[WebSocket] 서버 스레드 시작 실패: {e!r}")

        # --- SSE (뷰어 브로드캐스트, ISSUE-30) ---
        try:
            port = sse_port()
            repo = get_room_model()
            manager = get_broadcast_manager()
            # ISSUE-33: register/unregister 가 rooms.total_viewers /
            # rooms.peak_viewers 를 갱신하도록 metrics_repo 를 연결한다. 멱등.
            attach_broadcast_metrics_repo(repo)
            sse_thread = threading.Thread(
                target=run_sse_server,
                kwargs={
                    "broadcast_manager": manager,
                    "room_repo": repo,
                    "port": port,
                },
                daemon=True,
                name="sse-server",
            )
            sse_thread.start()
            _STATE["sse_thread"] = sse_thread
            _STATE["sse_port"] = port
            print(f"[SSE] viewer 브로드캐스트 서버 스레드 시작됨 (port={port})")
        except Exception as e:  # noqa: BLE001
            errors.append(f"sse: {e!r}")
            print(f"[SSE] 서버 스레드 시작 실패: {e!r}")

        _STATE["errors"] = errors
        # 한쪽이 실패해도 started 는 True 다 — 재시도 루프를 돌면 매 리런마다
        # 스레드를 또 띄우게 된다. 실패는 errors 로 드러내고 재시도는 하지 않는다.
        _STATE["started"] = True
        return dict(_STATE)
