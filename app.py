"""
실시간 자막 서비스 - Streamlit 메인 UI
모든 비즈니스 로직은 별도 모듈에 위치:
- translation.py: 번역 서비스
- services.py: OpenAI/AWS 세션 관리
- websocket_handler.py: WebSocket 서버/핸들러
- operator_ui.py: 사이드바 룸 선택 순수 로직 (ISSUE-27)
"""

import asyncio
import json
import os
import threading

import streamlit as st
from dotenv import load_dotenv

from auth import (
    display_login_form,
    display_user_info,
    get_current_user,
    init_session_state,
    is_authenticated,
)
from database import get_room_model
from operator_ui import (
    build_bootstrap_payload,
    build_room_dropdown_options,
    has_assigned_rooms,
    select_default_room,
)
from qr_generator import build_view_url, make_qr_data_url
from services import (
    create_openai_session,
    get_aws_access_key_id,
    get_aws_secret_access_key,
)
from sse_broadcast import run_sse_server
from websocket_handler import (
    attach_broadcast_metrics_repo,
    get_broadcast_manager,
    start_websocket_server,
)

# Load environment variables
load_dotenv()

# WebSocket 포트를 dict로 관리 (스레드 간 공유)
if "websocket_port_ref" not in st.session_state:
    st.session_state["websocket_port_ref"] = {"port": None}

# 사이드바 상태 관리
if "sidebar_state" not in st.session_state:
    st.session_state["sidebar_state"] = "expanded"

# 페이지 설정
st.set_page_config(
    page_title="실시간 자막",
    layout="wide",
    initial_sidebar_state=st.session_state["sidebar_state"],
)

# 사용자 인증 체크
init_session_state()

if not is_authenticated():
    display_login_form()
    st.stop()

# ALB/프록시 환경에서 HTTPS 지원을 위한 설정
try:
    import streamlit.web.server.server as _st_server  # noqa: E402

    _st_server.ENABLE_XSRF_PROTECTION = True
    os.environ.setdefault("STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION", "true")
except Exception:
    pass

# 전체 페이지 스크롤 방지 (외부 HTML 파일)
try:
    with open("components/scroll_lock.html", encoding="utf-8") as f:
        scroll_lock_html = f.read()
    st.markdown(scroll_lock_html, unsafe_allow_html=True)
except FileNotFoundError:
    pass


def _load_assigned_rooms_for_user(user):
    """배정된 룸 목록을 안전하게 조회한다 (ISSUE-27).

    - admin은 룸 배정 개념이 없으므로 빈 리스트로 처리해 사이드바에서 룸
      드롭다운 자체가 노출되지 않도록 한다 (관리자 전용 룸 관리는 ISSUE-29).
    - DB 조회 실패 시 RL-006 (내부 에러 누설 방지) 에 따라 서버 로그만
      남기고 빈 리스트를 반환한다.
    """
    if not user or user.get("role") == "admin":
        return []
    try:
        return get_room_model().list_by_operator(user["id"])
    except Exception as e:
        print(f"[Sidebar] 배정된 룸 조회 실패: {e!r}")
        return []


# === 사이드바 ===
with st.sidebar:
    st.header("🧑‍💻 유저 정보")
    display_user_info()

    if not get_aws_access_key_id() or not get_aws_secret_access_key():
        st.error("⚠️ AWS 자격 증명이 설정되지 않았습니다.")
        st.info("💡 .env 파일에 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY를 설정하세요")
        st.stop()

    # 룸 선택 (오퍼레이터 전용, ISSUE-27) ----------------------------
    sidebar_user = get_current_user()
    assigned_rooms = _load_assigned_rooms_for_user(sidebar_user)
    selected_room_id = None
    show_room_section = sidebar_user is not None and sidebar_user.get("role") != "admin"

    if show_room_section:
        st.markdown("---")
        st.subheader("🏛️ 배정된 룸")
        if has_assigned_rooms(assigned_rooms):
            options = build_room_dropdown_options(assigned_rooms)
            labels = [opt["label"] for opt in options]
            ids = [opt["id"] for opt in options]
            default_id = select_default_room(
                assigned_rooms, st.session_state.get("selected_room_id")
            )
            default_index = ids.index(default_id) if default_id in ids else 0
            chosen_label = st.selectbox(
                "룸 선택",
                options=labels,
                index=default_index,
                key="operator_room_selector",
                help="자막을 송출할 룸을 선택하세요",
            )
            selected_room_id = ids[labels.index(chosen_label)]
            st.session_state["selected_room_id"] = selected_room_id
            # ISSUE-28: 웰컴 화면에 룸 이름을 보여주기 위해 함께 보관.
            # assigned_rooms 는 이미 메모리에 있으므로 추가 DB 조회 없이 매핑.
            selected_room = next(
                (r for r in assigned_rooms if r["id"] == selected_room_id),
                None,
            )
            st.session_state["selected_room_name"] = (
                selected_room.get("name") if selected_room else None
            )
        else:
            st.info("배정된 룸이 없습니다. 관리자에게 문의하세요.")
            st.session_state.pop("selected_room_name", None)

    # 시스템 제어
    st.subheader("🎛️ 시스템 제어")
    col1, col2 = st.columns([1, 1])

    current_status = st.session_state.get("action", "idle")
    # 룸 선택 UI가 노출되는데 룸이 없으면 "시작"을 차단해
    # 의미 없는 세션 생성을 막는다 (AC2 invariant: 시작 시 room_id 필요).
    no_room_for_operator = show_room_section and selected_room_id is None

    with col1:
        start_disabled = (
            current_status
            in [
                "start",
                "starting",
            ]
            or no_room_for_operator
        )
        start = st.button(
            "🎯 시작",
            type="primary",
            use_container_width=True,
            disabled=start_disabled,
            help=(
                "배정된 룸이 없어 시작할 수 없습니다." if no_room_for_operator else None
            ),
        )

    with col2:
        stop_disabled = current_status in [
            "idle",
            "stop",
            "error",
        ]
        stop = st.button(
            "⏹️ 정지",
            use_container_width=True,
            disabled=stop_disabled,
        )

    # 시스템 상태
    st.markdown("---")
    st.subheader("📊 시스템 상태")
    status = st.session_state.get("action", "idle")
    if status == "start":
        st.success("🟢 서비스 실행 중")
        st.info("💡 자막 세부 설정은 화면 우상단 ⚙️ 버튼을 클릭하세요")
    elif status == "error":
        st.error("🔴 오류 발생")
    else:
        st.info("🟡 대기 중")

    # WebSocket 포트 정보 표시
    ws_port = st.session_state["websocket_port_ref"]["port"]
    if ws_port:
        st.markdown("---")
        st.subheader("🔗 연결 정보")
        st.code(f"WebSocket: ws://localhost:{ws_port}")
        if (
            st.session_state.get("websocket_thread")
            and st.session_state["websocket_thread"].is_alive()
        ):
            st.success("🟢 WebSocket 서버 실행 중")
        else:
            st.warning("🟡 WebSocket 서버 대기 중")


# === WebSocket 서버 스레드 ===
if (
    "websocket_thread" not in st.session_state
    or not st.session_state["websocket_thread"].is_alive()
):
    if "websocket_thread" in st.session_state:
        if st.session_state["websocket_thread"].is_alive():
            print("[WebSocket] 기존 스레드가 여전히 실행 중입니다.")
        else:
            print("[WebSocket] 기존 스레드 정리 중...")

    print("[WebSocket] 새로운 WebSocket 스레드 시작 중...")
    port_ref = st.session_state["websocket_port_ref"]
    st.session_state["websocket_thread"] = threading.Thread(
        target=start_websocket_server,
        args=(port_ref,),
        daemon=True,
    )
    st.session_state["websocket_thread"].start()
    print("[WebSocket] WebSocket 스레드 시작됨")


# === SSE 뷰어 브로드캐스트 서버 (ISSUE-30) ===
# 별도 daemon thread 로 aiohttp 기반 viewer SSE 엔드포인트를 구동한다.
# 포트는 SSE_PORT 환경변수, 기본 8766.
if (
    "sse_thread" not in st.session_state
    or not st.session_state["sse_thread"].is_alive()
):
    print("[SSE] viewer 브로드캐스트 서버 스레드 시작 중...")
    sse_port = int(os.getenv("SSE_PORT", "8766"))
    sse_repo = get_room_model()
    sse_mgr = get_broadcast_manager()
    # ISSUE-33: attach the metrics_repo so SSE register/unregister
    # persists cumulative + peak viewer counts to rooms.total_viewers /
    # rooms.peak_viewers. Idempotent — safe to call across reruns.
    attach_broadcast_metrics_repo(sse_repo)
    st.session_state["sse_thread"] = threading.Thread(
        target=run_sse_server,
        kwargs={
            "broadcast_manager": sse_mgr,
            "room_repo": sse_repo,
            "port": sse_port,
        },
        daemon=True,
    )
    st.session_state["sse_thread"].start()
    st.session_state["sse_port"] = sse_port
    print(f"[SSE] viewer 브로드캐스트 서버 스레드 시작됨 (port={sse_port})")


# === Session state ===
if "action" not in st.session_state:
    st.session_state["action"] = "idle"

# === Handle actions ===
openai_session = None
if start:
    try:
        st.session_state["sidebar_state"] = "expanded"

        with st.spinner("시작 중..."):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            openai_session = loop.run_until_complete(create_openai_session())
            loop.close()

            st.session_state["openai_session"] = openai_session
            st.session_state["action"] = "start"
            st.rerun()
    except ValueError as e:
        st.error(f"❌ {e!s}")
        st.session_state["action"] = "error"
        st.session_state["sidebar_state"] = "expanded"
        st.rerun()

elif stop:
    st.session_state["sidebar_state"] = "expanded"
    st.session_state["action"] = "stop"
    st.session_state.pop("openai_session", None)
    st.rerun()


def _resolve_viewer_base_url() -> str:
    """``VIEWER_BASE_URL`` 환경변수 → 기본 ``http://localhost:{SSE_PORT}`` 로 fallback.

    ISSUE-32: QR 이 가리킬 viewer 페이지의 base URL 을 결정한다.
    - 운영 환경에서는 ``VIEWER_BASE_URL`` 을 명시적으로 설정해야 함
      (예: ``https://captions.example.com``).
    - 미설정 시 ``http://localhost:{SSE_PORT}`` 로 떨어져 로컬 개발이 동작한다.

    RL-006 관점: 잘못된 환경변수가 들어와도 내부 traceback 이 노출되지
    않도록 ``str.strip()`` 만 하고 형식 검증은 하지 않는다.
    빈 문자열일 때는 fallback 으로 사용 — 호출자가 늘 truthy URL 을 받도록 한다.
    """
    raw = os.getenv("VIEWER_BASE_URL", "").strip()
    if raw:
        return raw
    sse_port = int(os.getenv("SSE_PORT", "8766"))
    return f"http://localhost:{sse_port}"


def _build_view_url_and_qr(room_id: str | None) -> tuple[str | None, str | None]:
    """selected room 에 대한 viewer URL + QR data URL 쌍을 만든다.

    ``room_id`` 가 None 이면 ``(None, None)`` — admin / 룸 미선택 경로에서
    호출되므로 webrtc.html 이 QR 영역을 숨길 수 있도록 한다.

    내부 예외는 RL-006 에 따라 server-side 로그만 남기고 ``(None, None)``
    을 돌려준다 — QR 생성 실패가 메인 캡션 UI 를 깨뜨리지 않게 한다.
    """
    if not room_id:
        return None, None
    try:
        view_url = build_view_url(room_id, _resolve_viewer_base_url())
        qr_data_url = make_qr_data_url(view_url)
        return view_url, qr_data_url
    except Exception as e:
        # RL-006: QR 생성 실패는 사용자에게 노출하지 않는다.
        print(f"[QR] view URL/QR 생성 실패 (room={room_id}): {e!r}")
        return None, None


# === 메인 캡션 뷰어 ===
try:
    with open("components/webrtc.html", encoding="utf-8") as f:
        html_template = f.read()

    current_user = get_current_user()

    # ISSUE-32: 오퍼레이터 룸이 선택된 경우에만 QR 데이터 동봉.
    bootstrap_room_id = st.session_state.get("selected_room_id")
    view_url, qr_data_url = _build_view_url_and_qr(bootstrap_room_id)

    payload = build_bootstrap_payload(
        action=st.session_state["action"],
        openai_session=st.session_state.get("openai_session"),
        websocket_port=st.session_state["websocket_port_ref"]["port"],
        user_info=current_user,
        room_id=bootstrap_room_id,
        # ISSUE-28 AC3: 웰컴 화면에 룸 이름을 보이기 위한 BOOT 필드.
        room_name=st.session_state.get("selected_room_name"),
        # ISSUE-32: 웰컴 화면에 QR 코드를 표시하기 위한 BOOT 필드.
        view_url=view_url,
        qr_data_url=qr_data_url,
    )

    html_content = html_template.replace("{{BOOTSTRAP_JSON}}", json.dumps(payload))
    st.components.v1.html(html_content, height=900, scrolling=False)

except Exception:
    st.error("❌ 시스템을 로드할 수 없습니다.")
