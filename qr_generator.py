"""
QR 코드 생성 모듈 (ISSUE-32).

행사장 청중이 룸 뷰어 페이지(``/view/{room_id}``)에 빠르게 접속할 수 있도록
QR 코드 PNG 와 data URL 을 생성하는 순수 함수들을 제공한다.

설계 원칙
---------
- **Importable + side-effect-free** (RL-001 / RL-005): 이 모듈은 import 시점에
  네트워크/DB/Streamlit 호출을 일체 하지 않는다. 단일 책임 = "URL 또는 PNG
  바이트를 만드는 것" 뿐이다.
- **Generic errors only** (RL-006): 예외는 그대로 raise 하지 않고 호출자가
  generic 메시지로 변환할 수 있도록 ValueError 같은 표준 타입만 노출한다.
  내부 라이브러리 traceback 이 사용자에게 누설되어선 안 된다.
- 외부 인터넷 의존성 회피: Google Charts 같은 외부 API 가 아닌 로컬
  ``qrcode[pil]`` 라이브러리를 사용해 오프라인 행사장에서도 동작한다.

Public API
----------
- :func:`build_view_url` : ``viewer_base_url + /view/{room_id}`` 정규화.
- :func:`make_qr_png` : 임의의 URL 을 PNG 바이트로 변환.
- :func:`make_qr_data_url` : ``data:image/png;base64,...`` URL 생성.
  webrtc.html 의 ``<img src>`` 에 직접 삽입할 수 있도록 인라인 데이터를
  반환한다 (RL-006: 외부 호스팅 의존을 피해 신뢰 경계 단순화).
"""

from __future__ import annotations

import base64
import io

import qrcode

# 뷰어 페이지 경로 — sse_broadcast.build_sse_app 의 ``/view/{room_id}`` 와 일치.
# 한 군데에서만 정의해 두어 라우트 변경 시 동기화 누락을 막는다.
_VIEW_PATH_TEMPLATE = "/view/{room_id}"


def build_view_url(room_id: str, base_url: str) -> str:
    """``base_url`` + ``/view/{room_id}`` 로 정규화된 뷰어 URL 을 만든다.

    다음 입력 변종을 모두 동일한 결과로 매핑해 호출자가 환경변수 형식을
    너무 신경 쓰지 않게 한다:

      - 끝에 ``/`` 가 있든 없든 동일 결과.
      - http/https 모두 그대로 전달.
      - host:port 형태도 그대로 보존.

    Args:
        room_id: ``Room.id`` 값. 빈 문자열은 ``ValueError``.
        base_url: ``VIEWER_BASE_URL`` 환경변수 값 또는 기본 fallback. 빈
            문자열/None 은 ``ValueError`` (호출자에서 fallback 합성 후
            전달해야 한다).

    Returns:
        ``https://example.com/view/abc123`` 형식의 절대 URL.

    Raises:
        ValueError: 입력이 비어 있거나 공백만 있는 경우.
    """
    if not room_id or not room_id.strip():
        raise ValueError("room_id must be non-empty")
    if not base_url or not base_url.strip():
        raise ValueError("base_url must be non-empty")

    cleaned_base = base_url.strip().rstrip("/")
    cleaned_room = room_id.strip()
    return cleaned_base + _VIEW_PATH_TEMPLATE.format(room_id=cleaned_room)


def make_qr_png(url: str) -> bytes:
    """URL 을 인코딩한 PNG 바이트를 반환한다.

    행사장 인쇄/스크린 표시용으로 충분히 또렷하도록 box_size=10 으로 고정.
    error_correction 은 ``ERROR_CORRECT_M`` (15% 손상 허용) — 인쇄/사진
    캡처 시 약간의 노이즈에도 스캔 성공률이 높다.

    Args:
        url: QR 에 인코딩할 문자열. 빈 문자열은 ``ValueError``.

    Returns:
        유효한 PNG 시그니처(``\\x89PNG\\r\\n\\x1a\\n``)로 시작하는 바이트.

    Raises:
        ValueError: 빈 url.
    """
    if not url or not url.strip():
        raise ValueError("url must be non-empty")

    qr = qrcode.QRCode(
        version=None,  # auto-fit 데이터 크기에 맞춰 버전 선택
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_qr_data_url(url: str) -> str:
    """``data:image/png;base64,...`` URL 을 만든다.

    ``webrtc.html`` 같은 클라이언트가 ``<img src>`` 에 곧바로 사용할 수
    있게 base64 PNG 를 인라인한다. 외부 CDN/QR 서비스 의존성이 없어
    오프라인 행사장에서도 동작한다.

    Args:
        url: QR 에 인코딩할 문자열. 빈 문자열은 ``ValueError``.

    Returns:
        ``"data:image/png;base64,<...>"`` 형식 문자열.

    Raises:
        ValueError: 빈 url.
    """
    png_bytes = make_qr_png(url)
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"
