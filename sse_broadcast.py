"""
뷰어용 SSE (Server-Sent Events) 브로드캐스트 모듈 (ISSUE-30).

행사장 청중이 인증 없이 룸의 자막을 실시간 수신할 수 있도록 비인증 HTTP
엔드포인트 ``GET /stream/{room_id}?lang=<code>`` 을 제공한다.

설계 요약
---------
- Streamlit 은 SSE 를 네이티브 지원하지 않으므로, 별도 aiohttp 기반 경량 HTTP
  서버를 daemon thread 로 구동한다 (``services.py`` 의 health server 패턴 참고).
- 룸 단위 / 언어 단위로 viewer 큐를 분리한다. websocket_handler 의
  ``_handle_transcript`` 가 메인 언어 번역을 publish 한 직후 추가 언어를
  ``asyncio.create_task`` 로 비동기 번역해 메인 송출이 블로킹되지 않도록 한다.
- 추가 언어에 viewer 가 한 명도 없으면 번역 자체를 스킵한다 (lazy translation).
- 외부 노출 에러 메시지는 모두 generic 하게 유지한다 (RL-006). 룸 조회/내부
  처리 예외는 서버 로그에만 남기고 클라이언트에는 일반 404/500 만 노출한다.

API
---
- :class:`BroadcastManager` : (room_id, lang) 채널별 viewer 큐 등록/해제/배포.
- :func:`build_sse_app` : aiohttp ``web.Application`` 을 생성한다 (테스트 친화).
- :func:`run_sse_server` : daemon thread 진입점. 서버 시작 시 한 번 호출한다.
- :func:`broadcast_translation_for_room` : websocket_handler 가 호출하는
  메인+추가 언어 publish 헬퍼. 비동기 backgrounding 정책을 캡슐화한다.
"""

from __future__ import annotations

import asyncio
import html
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import web

from branding_routes import handle_branding_asset
from script_escape import PLACEHOLDER_RE, json_for_script
from stage_config import normalize_stage_config
from translation import SUPPORTED_OUTPUT_LANGS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# 뷰어 큐의 최대 길이. 청중이 일시적으로 끊겨도 backpressure 로 메모리가
# 무한 증가하지 않도록 막는 안전장치. 큐가 가득 차면 가장 오래된 메시지를
# 1건 비우고 새 메시지를 넣는다 (캡션 도메인 — 약간의 자막 손실은 허용,
# 메모리 누수는 불가).
_DEFAULT_QUEUE_MAXSIZE = 32

# 기본 `message` 가 아닌 이름으로 나갈 수 있는 이벤트의 **전부** (ISSUE-53).
# 이름 결정 권한을 payload 에 통째로 넘기면 그 성질이 코드가 아니라 관행으로만
# 유지된다. 허용 목록 한 줄이 그것을 구조적 불변식으로 바꾼다.
_CONTROL_EVENT_NAMES = frozenset({"control"})


class _ControlEnvelope(dict):
    """`publish_control` 이 큐에 넣는 payload 래퍼 (ISSUE-53).

    ``dict`` 그대로라 큐/직렬화/동등 비교가 전부 기존과 같지만, **타입으로**
    자막 경로와 구분된다. 이 구분이 필요한 이유: 이벤트 이름을 payload 내용
    에서만 읽으면 ``{"event": "control"}`` 이 섞인 자막 payload 가 자기 이름을
    정하게 된다. 이름은 **어느 경로로 발행됐는가**가 정해야 하고, 그 사실을
    담을 수 있는 것은 내용이 아니라 타입뿐이다.
    """

    __slots__ = ()


# Type alias for the translate callback the websocket_handler passes in.
# Signature: (text, source_lang, target_lang) -> translated_text
TranslateFn = Callable[[str, str, str], Awaitable[str | None]]


def build_control_payload(room_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """룸 control 상태를 SSE 프레임 payload 로 만든다 (ISSUE-53).

    고정 키를 ``**state`` **뒤에** 놓는 것이 의도된 순서다 — 상태에 ``event``
    키가 들어와도 프레임 이름을 바꿀 수 없다. `_handle_stream` 의 허용 목록과
    같은 규칙을 조립 시점에서 한 번 더 적용한다.
    """
    return {
        **state,
        "event": "control",
        "room_id": room_id,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# BroadcastManager
# ---------------------------------------------------------------------------
class BroadcastManager:
    """(room_id, lang) 채널 기준 viewer 큐 레지스트리.

    websocket_handler 가 메인/추가 언어 번역 결과를 publish 하면, 해당
    채널을 구독 중인 모든 SSE viewer 큐에 payload 가 enqueue 된다.
    SSE handler 는 큐에서 dequeue 해 ``data: ...\\n\\n`` 포맷으로 송신한다.

    ISSUE-33: viewer metrics
    -------------------------
    - 인메모리 카운트 (room_id → 전체/언어별) 가 register/unregister 시점에
      O(1) 로 갱신된다 (admin.py 의 metrics 위젯이 이 snapshot 을 읽는다).
    - 옵션으로 ``metrics_repo`` (database.Room 호환 인터페이스) 를 주입받아
      register 시점에 누적/peak 를 DB 에 영속화한다. 누적/peak 는 서버
      재시작에도 보존되어야 하기 때문이다 (in-memory current 는 휘발성).
    - DB 실패가 SSE 연결을 절대 끊지 않는다 — RL-006 의 일관된 적용 (서버
      로그에 디테일을 남기되, 클라이언트는 generic 한 동작을 본다).
    """

    def __init__(
        self,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        *,
        metrics_repo: Any | None = None,
    ) -> None:
        # (room_id, lang) -> set[asyncio.Queue]
        self._channels: dict[tuple[str, str], set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._queue_maxsize = queue_maxsize
        # ISSUE-33: viewer metrics state.
        # room_id -> total current count
        self._current: dict[str, int] = {}
        # room_id -> {lang: count}
        self._by_lang: dict[str, dict[str, int]] = {}
        # ISSUE-53: 룸 단위 프레젠테이션 control 상태 (primary_lang / caption_scale).
        # **프로세스 메모리 전용이다.** rooms.stage_config 에 넣지 않는 이유는
        # 그 블롭이 관리자 소유의 행사 전 브랜딩 기록이고, 관리자 폼의
        # normalize→save 왕복이 자기가 모르는 키를 조용히 버리기 때문이다
        # (stage_config.normalize_stage_config) — 행사 중에 맞춰 둔 값이 로고
        # 하나 바꾸는 순간 아무 로그 없이 사라진다 (RL-024/RL-025).
        # 잔여 갭: **서버 재시작 시 caption_scale 이 1.0 으로 돌아간다.**
        # 복구는 오퍼레이터가 슬라이더를 한 번 움직이는 것이고, 언어는 룸 행에
        # 남아 있으므로(ISSUE-52) 페이지 로드 시점 정합은 유지된다.
        self._control: dict[str, dict[str, Any]] = {}
        # database.Room 호환 (update_viewer_metrics / get_viewer_metrics).
        # Typed Any to avoid an import cycle and to keep tests trivial.
        self._metrics_repo = metrics_repo

    async def register_viewer(self, room_id: str, lang: str) -> asyncio.Queue:
        """Add a viewer to (room_id, lang) and return its dedicated queue.

        Side effects (ISSUE-33):
          - in-memory current count for ``room_id`` increments by 1.
          - in-memory ``by_lang[lang]`` count increments by 1.
          - if a ``metrics_repo`` is attached, its
            ``update_viewer_metrics(room_id, total_delta=1, current=N)`` is
            called with N == the new in-memory current. DB failures are
            logged and swallowed (RL-006) — viewers must never see a 5xx
            because the metrics persistence path is flaky.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._channels.setdefault((room_id, lang), set()).add(q)
            # In-memory metrics — accurate even when no DB repo is attached.
            new_total = self._current.get(room_id, 0) + 1
            self._current[room_id] = new_total
            lang_map = self._by_lang.setdefault(room_id, {})
            lang_map[lang] = lang_map.get(lang, 0) + 1
            current_for_db = new_total

        # DB persistence runs OUTSIDE the asyncio lock — repo is sync and
        # we don't want a slow SQLite write to serialise concurrent
        # registers. Errors are isolated; in-memory state is the source of
        # truth for the live snapshot.
        if self._metrics_repo is not None:
            try:
                self._metrics_repo.update_viewer_metrics(
                    room_id, total_delta=1, current=current_for_db
                )
            except Exception as e:
                # RL-006: never propagate raw error text. Log internally.
                print(
                    f"[SSE] metrics_repo.update_viewer_metrics failed "
                    f"(room={room_id} lang={lang}): {e!r}"
                )
        return q

    async def unregister_viewer(
        self, room_id: str, lang: str, queue: asyncio.Queue
    ) -> None:
        """Remove a viewer queue. No-op if the channel/queue is unknown.

        Side effects (ISSUE-33):
          - in-memory current count decrements by 1 (clamped at 0 — defensive).
          - in-memory by_lang[lang] decrements by 1; entry removed at 0.
          - room dict entries are dropped when both counts reach 0 so
            get_metrics returns the clean zero-state without stale keys.
          - DB peak is NOT touched on unregister — peak is monotonically
            non-decreasing (it was already max'd above on register).
        """
        key = (room_id, lang)
        async with self._lock:
            viewers = self._channels.get(key)
            if viewers is None:
                return
            had_queue = queue in viewers
            viewers.discard(queue)
            if not viewers:
                # Drop empty channel so has_viewers reports False quickly
                # and lazy translation gates publication accurately.
                self._channels.pop(key, None)

            # Only mutate counters if the queue we removed was actually
            # registered — guards against double-unregister scenarios that
            # would otherwise underflow the count.
            if had_queue:
                # Per-language decrement.
                lang_map = self._by_lang.get(room_id)
                if lang_map is not None:
                    lang_map[lang] = max(0, lang_map.get(lang, 0) - 1)
                    if lang_map[lang] == 0:
                        lang_map.pop(lang, None)
                    if not lang_map:
                        self._by_lang.pop(room_id, None)
                # Total decrement (clamped).
                new_total = max(0, self._current.get(room_id, 0) - 1)
                if new_total == 0:
                    self._current.pop(room_id, None)
                else:
                    self._current[room_id] = new_total

    def get_metrics(self, room_id: str) -> dict[str, Any]:
        """Return live in-memory snapshot for ``room_id`` (ISSUE-33).

        Shape::
            {
                "current": int,            # 전체 동시 viewer 수
                "by_lang": {lang: int},    # 언어별 동시 viewer 수 (>0 만)
            }

        Read-only and lock-free — admin.py polls this at every Streamlit
        rerun so the metric widgets reflect the latest count without
        blocking the publish path. Unknown rooms return zeros (no KeyError)
        so the dashboard can render rooms with no current viewers as "0명".
        """
        # dict.copy() so the caller can't mutate internal state.
        by_lang_src = self._by_lang.get(room_id) or {}
        return {
            "current": self._current.get(room_id, 0),
            "by_lang": dict(by_lang_src),
        }

    def has_viewers(self, room_id: str, lang: str) -> bool:
        """Return True iff at least one viewer is subscribed to (room, lang).

        Read-only: synchronous and lock-free for low-overhead use as the
        lazy-translation gate inside ``broadcast_translation_for_room``.
        Snapshot semantics — a viewer arriving milliseconds later is fine,
        the next published message will reach them.
        """
        viewers = self._channels.get((room_id, lang))
        return bool(viewers)

    def set_control(self, room_id: str, **fields: Any) -> dict[str, Any]:
        """Merge ``fields`` into the room's control state; return the snapshot.

        Synchronous and lock-free like :meth:`has_viewers` / :meth:`get_metrics`
        — this is a two-scalar dict update on the operator's path, and putting
        it behind the channel registry lock would block the SSE handler on an
        operator gesture. Merge (not replace) so a caption-scale update cannot
        wipe the primary language, and vice versa.
        """
        state = self._control.setdefault(room_id, {})
        state.update(fields)
        # Copy on the way out too — the caller builds a wire payload from this
        # and must not be able to reach back into the manager's state.
        return dict(state)

    def get_control(self, room_id: str) -> dict[str, Any] | None:
        """Return a **copy** of the room's control state, or None if it has none.

        ``None`` rather than ``{}`` is deliberate: "no state" is what stops
        `_handle_stream` from writing a connect-time snapshot, and inventing a
        default there would push a made-up language onto every new viewer.
        """
        state = self._control.get(room_id)
        return dict(state) if state else None

    def _enqueue(
        self, queue: asyncio.Queue, payload: dict[str, Any], *, room_id: str, lang: str
    ) -> None:
        """Put ``payload`` on ``queue``, dropping its oldest item when full.

        Shared by :meth:`publish` and :meth:`publish_control` so the saturation
        policy has exactly one definition — a second copy drifts, and the shape
        of that drift is "control frames are the only ones silently lost"
        (RL-001).
        """
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop one old message, then put the new one.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Still full (concurrent producer) — give up on this viewer.
                print(
                    f"[SSE] dropping payload, viewer queue saturated "
                    f"(room={room_id} lang={lang})"
                )

    async def publish(self, room_id: str, lang: str, payload: dict[str, Any]) -> None:
        """Enqueue payload for every viewer of (room_id, lang).

        Full queues drop the oldest item — this prevents one stuck viewer
        from blocking the publish loop or causing unbounded memory growth.
        """
        viewers = self._channels.get((room_id, lang))
        if not viewers:
            return
        # Snapshot so concurrent unregisters don't trip iteration.
        for q in list(viewers):
            self._enqueue(q, payload, room_id=room_id, lang=lang)

    async def publish_control(self, room_id: str, payload: dict[str, Any]) -> None:
        """Fan a control frame out to **every language channel** of the room.

        Room-wide rather than per-language because the whole point of a
        language announcement is to reach the clients still sitting on the
        *old* channel — a per-language publish would deliver it to everyone
        except the people who need it.
        """
        envelope = _ControlEnvelope(payload)
        for (rid, lang), viewers in list(self._channels.items()):
            if rid != room_id:
                continue
            for q in list(viewers):
                self._enqueue(q, envelope, room_id=room_id, lang=lang)

    def channel_count(self) -> int:
        """Total number of (room, lang) channels with at least one viewer."""
        return len(self._channels)


# ---------------------------------------------------------------------------
# aiohttp app factory + handler
# ---------------------------------------------------------------------------
def build_sse_app(
    *,
    broadcast_manager: BroadcastManager,
    room_repo: Any,
) -> web.Application:
    """Construct the aiohttp Application that serves /stream/{room_id}.

    ``room_repo`` only needs a ``.get_by_id(room_id) -> dict | None`` method,
    so tests can pass a lightweight fake without spinning up SQLite. In
    production this is :class:`database.Room`.
    """
    app = web.Application()
    app["broadcast_manager"] = broadcast_manager
    app["room_repo"] = room_repo
    app.router.add_get("/stream/{room_id}", _handle_stream)
    app.router.add_get("/view/{room_id}", _handle_view)
    app.router.add_get("/branding/{room_id}/{filename}", handle_branding_asset)
    app.router.add_get("/stage/{room_id}", _handle_stage)
    app.router.add_get("/health", _handle_health)
    return app


async def _handle_health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


# ---------------------------------------------------------------------------
# Viewer page (/view/{room_id}) — ISSUE-31
# ---------------------------------------------------------------------------
# Path to the viewer template, relative to this module. Resolved once at import
# so the handler doesn't pay the lookup cost per request.
_VIEWER_TEMPLATE_PATH = Path(__file__).resolve().parent / "components" / "viewer.html"

# Friendly 404 body — kept as a constant so the same generic message is reused
# for both "unknown room" and "internal repo error" branches (RL-006: never
# echo internal exception text to unauthenticated viewers).
_NOT_FOUND_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>룸을 찾을 수 없습니다</title>
<style>
  html, body { margin: 0; padding: 0; height: 100vh; height: 100dvh; }
  body {
    display: flex; align-items: center; justify-content: center;
    background: #0f0f23; color: #fff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
      "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
    text-align: center; padding: 24px;
  }
  .box { max-width: 480px; }
  h1 { font-size: clamp(20px, 4vw, 28px); font-weight: 500; margin: 0 0 12px; }
  p  { font-size: 15px; color: rgba(255,255,255,0.65); margin: 0; }
</style>
</head>
<body>
  <div class="box">
    <h1>룸을 찾을 수 없습니다</h1>
    <p>QR 코드 또는 링크가 올바른지 다시 확인해 주세요.</p>
  </div>
</body>
</html>
"""


def _coerce_output_langs_list(raw: Any, fallback: str) -> list[str]:
    """Public-shaped coerce — JSON list[str], with `fallback` as a safety net.

    Mirrors the private :func:`_coerce_output_langs` below but always
    guarantees the fallback is present in the result so the viewer's language
    dropdown never renders empty.
    """
    langs = _coerce_output_langs(raw, fallback)
    if fallback and fallback not in langs:
        langs = [fallback, *langs]
    return langs


def _supported_output_langs(primary: str) -> list[str]:
    """뷰어 언어 목록 — 전역 지원 언어 고정 (#91/#92).

    룸별 rooms.output_langs 대신 translation.SUPPORTED_OUTPUT_LANGS 를
    쓴다. admin 룸 설정과 무관하게 뷰어는 지원 언어 전부를 고를 수 있고,
    번역 비용은 has_viewers lazy 게이트가 언어 단위로 제어한다.
    primary 가 목록 밖 코드여도 앞에 끼워 넣어 드롭다운이 비지 않게 한다.
    """
    langs = list(SUPPORTED_OUTPUT_LANGS)
    if primary and primary not in langs:
        langs.insert(0, primary)
    return langs


# 이스케이퍼와 치환 문법의 정의는 `script_escape` 한 곳뿐이다 (ISSUE-48 /
# RL-001). 여기서는 기존 이름만 별칭으로 유지한다 — 열 곳 넘는 테스트가
# `from sse_broadcast import _json_for_script` 로 잡고 있고, 오퍼레이터
# 컴포넌트(`operator_ui.render_component_html`)가 **같은 객체**를 쓴다.
_json_for_script = json_for_script
_PLACEHOLDER_RE = PLACEHOLDER_RE


def _render_viewer_html(
    *,
    room_id: str,
    room_name: str,
    output_langs: list[str],
    primary_lang: str,
    initial_state: str,
) -> str:
    """Render the viewer template with safe substitutions.

    Same rules as :func:`_render_stage_html` (ISSUE-44 — parity). The escaper
    is picked by **sink**, not by "is this user input" (RL-020): the room name
    reaches markup as ``{{ROOM_NAME}}`` and is HTML-escaped, while everything
    inside the inline ``<script>`` — the room name included, under the separate
    ``{{ROOM_NAME_JSON}}`` name — is emitted by :func:`_json_for_script` as a
    complete JS literal, so the template supplies no quotes of its own. A
    ``<script>`` is raw text: HTML entities never decode there, so ``html.escape``
    silently corrupts the value (``a"b`` → ``a&quot;b``, which then builds the
    ``/stream/{room_id}`` URL) and leaves ``\\`` untouched, letting a trailing
    backslash escape the closing quote and kill the whole bootstrap.

    Substitution is single-pass. Chained ``str.replace`` lets a value written
    by an earlier step be re-read by a later one, so a room named
    ``{{OUTPUT_LANGS_JSON}}`` would render the language list into the
    ``<title>`` (RL-021). The lambda replacement also keeps ``re.sub`` from
    interpreting backslashes and backreferences in the substituted values, and
    indexing ``values`` (rather than ``.get``) makes a mistyped placeholder
    fail loudly instead of leaking to the page.
    """
    template = _VIEWER_TEMPLATE_PATH.read_text(encoding="utf-8")
    name = room_name or room_id
    values = {
        "ROOM_NAME": html.escape(name, quote=True),
        "ROOM_NAME_JSON": _json_for_script(name),
        "ROOM_ID": _json_for_script(room_id),
        "OUTPUT_LANGS_JSON": _json_for_script(output_langs),
        "PRIMARY_LANG": _json_for_script(primary_lang or "ko"),
        "INITIAL_STATE": _json_for_script(initial_state),
    }
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


async def _handle_view(request: web.Request) -> web.Response:
    """Serve the unauthenticated viewer HTML for a room.

    Branches:
      1. Unknown room (or repo failure) → 404 + friendly HTML body.
      2. closed room → 200 + viewer page rendered with initial_state='closed'
         so the JS bootstrap shows the ended state without opening SSE.
      3. Otherwise → 200 + viewer page; JS connects to /stream/{room_id}.

    All branches return text/html. RL-006: server-side log keeps the full
    detail; the response body is generic.
    """
    room_id = request.match_info["room_id"]
    room_repo = request.app["room_repo"]

    try:
        room = room_repo.get_by_id(room_id)
    except Exception as e:
        # RL-006: log internal detail, return generic 404.
        print(f"[View] room lookup failed: {e!r}")
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    if room is None:
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    primary_lang = room.get("primary_output_lang") or "ko"
    output_langs = _supported_output_langs(primary_lang)
    room_name = room.get("name") or room_id
    status = room.get("status") or "waiting"
    initial_state = "closed" if status == "closed" else status

    try:
        body = _render_viewer_html(
            room_id=room_id,
            room_name=room_name,
            output_langs=output_langs,
            primary_lang=primary_lang,
            initial_state=initial_state,
        )
    except Exception as e:
        # Template missing or unreadable — log, do not propagate.
        print(f"[View] viewer template render failed: {e!r}")
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    return web.Response(status=200, text=body, content_type="text/html")


# ---------------------------------------------------------------------------
# Stage composite page (/stage/{room_id}) — ISSUE-40
# ---------------------------------------------------------------------------
_STAGE_TEMPLATE_PATH = Path(__file__).resolve().parent / "components" / "stage.html"

# stage_config.caption_ratio → 자막 컬럼 CSS 폭. 매핑이 여기(서버)에만 있으므로
# 응답 HTML 에는 해당 룸의 폭 하나만 등장한다 — 템플릿이 두 값을 모두 갖고
# 있으면 렌더 결과로 설정을 구분할 수 없다.
_CAPTION_WIDTHS = {"1/4": "25%", "1/3": "33.333%"}
_DEFAULT_CAPTION_WIDTH = _CAPTION_WIDTHS["1/4"]


def _render_stage_html(
    *,
    room_id: str,
    room_name: str,
    output_langs: list[str],
    caption_lang: str,
    lang_pinned: bool,
    initial_state: str,
    stage_config: dict[str, Any],
) -> str:
    """Render the stage template with safe substitutions.

    The escaper is picked by **sink**, not by "is this user input" (RL-020):
    ``{{ROOM_NAME}}`` appears only in markup and is HTML-escaped, while every
    other placeholder appears only inside the inline ``<script>`` and is
    emitted by :func:`_json_for_script` as a complete JS literal — the
    template supplies no quotes of its own.

    Substitution is single-pass. Chained ``str.replace`` lets a value written
    by an earlier step be re-read by a later one, so a room named
    ``{{STAGE_CONFIG_JSON}}`` would render the config blob into the ``<h1>``
    (RL-021). The lambda replacement also keeps ``re.sub`` from interpreting
    backslashes and backreferences in the substituted values.

    ``stage_config`` must already be normalised
    (:func:`stage_config.normalize_stage_config`); a render-only
    ``caption_width`` key is derived here so the template never has to know
    the ratio→width mapping.
    """
    template = _STAGE_TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = {
        **stage_config,
        "caption_width": _CAPTION_WIDTHS.get(
            stage_config.get("caption_ratio"), _DEFAULT_CAPTION_WIDTH
        ),
    }
    values = {
        "ROOM_NAME": html.escape(room_name or room_id, quote=True),
        "ROOM_ID": _json_for_script(room_id),
        "OUTPUT_LANGS_JSON": _json_for_script(output_langs),
        "PRIMARY_LANG": _json_for_script(caption_lang or "ko"),
        # ISSUE-53: "이 언어는 명시적으로 요청된 것인가" — 서버만 답할 수 있다.
        # 클라이언트는 `caption_lang` 이 `?lang=` 에서 왔는지 룸 기본값인지
        # 구분할 수 없고, 브라우저에서 쿼리를 다시 파싱하면 서버가 거부한
        # 코드가 되살아난다. `_json_for_script` 를 통과시키는 것은 스칼라도
        # 예외가 아니기 때문이다 (RL-016/RL-020) — Python `True` 가 그대로
        # 나가면 JS 부트스트랩 전체가 죽는다.
        "LANG_PINNED": _json_for_script(bool(lang_pinned)),
        "INITIAL_STATE": _json_for_script(initial_state),
        "STAGE_CONFIG_JSON": _json_for_script(payload),
    }
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)


async def _handle_stage(request: web.Request) -> web.Response:
    """Serve the unauthenticated stage composite page for a room.

    Same three branches as :func:`_handle_view`:
      1. Unknown room (or repo failure) → 404 + the shared friendly body.
      2. closed room → 200 rendered with initial_state='closed' so the caption
         column shows the ended state without opening SSE.
      3. Otherwise → 200.

    The caption language is fixed by ``?lang=`` (the stage screen carries no
    controls — a form element could intercept presenter remote keys, NFR-025);
    an unsupported code falls back to the room's primary language.
    ``stage_config`` is normalised here, so a malformed blob degrades to the
    documented defaults instead of 500ing during an event.
    """
    room_id = request.match_info["room_id"]
    room_repo = request.app["room_repo"]

    try:
        room = room_repo.get_by_id(room_id)
    except Exception as e:
        # RL-006: log internal detail, return generic 404.
        print(f"[Stage] room lookup failed: {e!r}")
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    if room is None:
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    primary_lang = room.get("primary_output_lang") or "ko"
    output_langs = _supported_output_langs(primary_lang)
    requested_lang = request.query.get("lang")
    caption_lang = requested_lang if requested_lang in output_langs else primary_lang
    # ISSUE-53 / FR-085: "이 화면은 자기 언어를 명시적으로 골랐는가."
    # 값 비교(`caption_lang != primary_lang`)로 대신할 수 없다 — `?lang=ko` 는
    # 룸 기본값과 같아도 **명시적 선택**이고, 그런 화면이 control 에 끌려가면
    # 일부러 고른 언어가 발표 중간에 바뀐다.
    lang_pinned = requested_lang is not None and requested_lang in output_langs
    room_name = room.get("name") or room_id
    status = room.get("status") or "waiting"
    initial_state = "closed" if status == "closed" else status

    try:
        body = _render_stage_html(
            room_id=room_id,
            room_name=room_name,
            output_langs=output_langs,
            caption_lang=caption_lang,
            lang_pinned=lang_pinned,
            initial_state=initial_state,
            stage_config=normalize_stage_config(room.get("stage_config")),
        )
    except Exception as e:
        # Template missing or unreadable — log, do not propagate (RL-006).
        print(f"[Stage] stage template render failed: {e!r}")
        return web.Response(status=404, text=_NOT_FOUND_HTML, content_type="text/html")

    return web.Response(status=200, text=body, content_type="text/html")


async def _handle_stream(request: web.Request) -> web.StreamResponse:
    """SSE handler — one connection per viewer.

    Lifecycle:
      1. Resolve the room via repo. Unknown → 404 (RL-006: generic message).
      2. If room is closed → write a single ``session_end`` event then close.
      3. Otherwise pick the requested lang (?lang=<code>, default
         primary_output_lang) and register a viewer queue.
      4. Stream payloads as ``data: <json>\\n\\n`` until the client disconnects.

    Generic error messages only — never echo exception text.
    """
    room_id = request.match_info["room_id"]
    room_repo = request.app["room_repo"]
    mgr: BroadcastManager = request.app["broadcast_manager"]

    # --- 1) Resolve the room -------------------------------------------------
    try:
        room = room_repo.get_by_id(room_id)
    except Exception as e:
        # RL-006: log full detail server-side, return generic 404.
        # Treat repo failure as "room not available" to avoid information
        # leak about the persistence layer to unauthenticated viewers.
        print(f"[SSE] room lookup failed: {e!r}")
        return web.Response(status=404, text="not found")

    if room is None:
        return web.Response(status=404, text="not found")

    # --- 2) Closed rooms emit one session_end event then close ---------------
    sse_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Disable Nginx/CDN response buffering for SSE.
        "X-Accel-Buffering": "no",
    }

    if room.get("status") == "closed":
        resp = web.StreamResponse(status=200, headers=sse_headers)
        await resp.prepare(request)
        end_payload = {
            "event": "session_end",
            "room_id": room_id,
            "timestamp": time.time(),
        }
        await _write_sse_event(resp, "session_end", end_payload)
        await resp.write_eof()
        return resp

    # --- 3) Determine subscription language ---------------------------------
    primary_lang = room.get("primary_output_lang") or "ko"
    requested_lang = request.query.get("lang") or primary_lang

    # --- 4) Register viewer + stream ---------------------------------------
    queue = await mgr.register_viewer(room_id, requested_lang)
    resp = web.StreamResponse(status=200, headers=sse_headers)
    await resp.prepare(request)

    # Initial comment frame so clients confirm the connection is established
    # before any payload arrives. SSE spec: lines starting with ":" are
    # ignored by EventSource — used purely as a keep-alive ping.
    try:
        await resp.write(b": connected\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        await mgr.unregister_viewer(room_id, requested_lang, queue)
        return resp

    # ISSUE-53: connect-time control snapshot. The position matters — **after**
    # register_viewer (earlier and an update arriving during registration is
    # lost), **before** the queue loop (inside it and the snapshot interleaves
    # with caption frames). A room with no control state gets no frame at all:
    # inventing a default here would push a made-up language onto every viewer.
    # This is what lets a reloaded stage screen recover the current language and
    # font scale with no DB round trip and nothing persisted.
    control_state = mgr.get_control(room_id)
    if control_state:
        try:
            await _write_sse_event(
                resp, "control", build_control_payload(room_id, control_state)
            )
        except (ConnectionResetError, asyncio.CancelledError):
            await mgr.unregister_viewer(room_id, requested_lang, queue)
            return resp

    # Watcher task: poll the underlying transport so client TCP close
    # (FIN/RST) is detected within ~0.5s instead of waiting for the next
    # heartbeat write to fail. Without this the in-memory viewer count
    # would lag client disconnects by up to one heartbeat cycle, breaking
    # ISSUE-33 metrics fidelity (#69).
    disconnect_event = asyncio.Event()

    async def _watch_disconnect() -> None:
        while not disconnect_event.is_set():
            transport = request.transport
            if transport is None or transport.is_closing():
                disconnect_event.set()
                return
            await asyncio.sleep(0.25)

    watcher = asyncio.create_task(_watch_disconnect())

    try:
        while True:
            get_task = asyncio.create_task(queue.get())
            disc_task = asyncio.create_task(disconnect_event.wait())
            try:
                # Race the queue read against client disconnect; the 5s
                # ceiling doubles as a keep-alive heartbeat so idle proxies
                # don't drop the connection.
                done, pending = await asyncio.wait(
                    {get_task, disc_task},
                    timeout=5.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                get_task.cancel()
                disc_task.cancel()
                break

            for p in pending:
                p.cancel()

            if disc_task in done:
                break

            if not done:
                # Timed out — emit heartbeat. Failure on write reliably
                # confirms a dead connection even when transport.is_closing
                # somehow lags.
                try:
                    await resp.write(b": ping\n\n")
                except (ConnectionResetError, asyncio.CancelledError):
                    break
                continue

            try:
                payload = get_task.result()
            except asyncio.CancelledError:
                break

            # ISSUE-53: the event name comes from an allow-list, and only a
            # frame published through `publish_control` may declare one at all.
            # Reading `event` off any dict would let a caption payload carrying
            # `{"event": "control"}` name itself; the envelope type records the
            # publishing path, which content never can.
            declared = (
                payload.get("event") if isinstance(payload, _ControlEnvelope) else None
            )
            event_name = declared if declared in _CONTROL_EVENT_NAMES else "message"

            try:
                await _write_sse_event(resp, event_name, payload)
            except (ConnectionResetError, asyncio.CancelledError):
                break
            except Exception as e:
                # RL-006: log internal write error, do not echo to client.
                print(
                    f"[SSE] write error (room={room_id} lang={requested_lang}): {e!r}"
                )
                break
    finally:
        watcher.cancel()
        await mgr.unregister_viewer(room_id, requested_lang, queue)

    return resp


async def _write_sse_event(
    response: web.StreamResponse, event_name: str, payload: dict[str, Any]
) -> None:
    """Write a single SSE event in the canonical ``event: ...\\ndata: ...\\n\\n`` form.

    The ``message`` event name is the EventSource default — emitting it
    explicitly keeps the wire format readable and uniform across event
    types (``session_end`` etc.).
    """
    data_line = json.dumps(payload, ensure_ascii=False)
    frame = f"event: {event_name}\ndata: {data_line}\n\n".encode()
    await response.write(frame)


# ---------------------------------------------------------------------------
# Helper used by websocket_handler to keep its translation pipeline tidy
# ---------------------------------------------------------------------------
async def broadcast_translation_for_room(
    manager: BroadcastManager,
    room: dict[str, Any],
    *,
    primary_translated: str,
    source_text: str,
    source_lang: str,
    translate_fn: TranslateFn,
) -> None:
    """Publish primary-language translation now; schedule secondary langs.

    This is the ISSUE-30 pipeline contract:

      음성 인식 완료
        ├── [즉시] primary 언어 publish (이미 번역 완료된 결과)
        └── [후순위] output_langs 중 primary 외의 언어를 ``asyncio.create_task``
                    로 백그라운드 번역 → publish.
                    뷰어 없는 언어는 translate_fn 호출 자체를 스킵한다.

    Why this lives here (not in ``websocket_handler``):
    - Keeps the WS path purely sequential and easy to read.
    - Lets unit tests inject a fake ``translate_fn`` that asserts on call
      patterns (lazy-skip, non-blocking) without bringing AWS into scope.
    """
    room_id = room["id"]
    primary_lang = room.get("primary_output_lang") or "ko"

    # 1) Publish the primary language result NOW. Must not await any
    #    secondary work — this is the AC ("메인 언어 번역이 추가 언어 번역에
    #    의해 블로킹되지 않는다").
    await manager.publish(
        room_id,
        primary_lang,
        {
            "text": primary_translated,
            "lang": primary_lang,
            "timestamp": time.time(),
        },
    )

    # 2) Resolve secondary languages — 전역 지원 언어 기준 (#91/#92).
    #    뷰어 없는 언어는 아래 has_viewers 게이트가 스킵하므로, 목록을
    #    넓혀도 번역 호출 수는 늘지 않는다.
    output_langs = _supported_output_langs(primary_lang)
    secondaries = [lang for lang in output_langs if lang != primary_lang]
    if not secondaries:
        return

    # 3) For each secondary language with viewers, schedule a background
    #    translate+publish task. Lazy-skip when no viewers exist.
    for lang in secondaries:
        if not manager.has_viewers(room_id, lang):
            continue
        # Closure captures lang; create_task ensures the WS path is not
        # blocked by AWS Bedrock latency.
        asyncio.create_task(
            _translate_and_publish_secondary(
                manager,
                room_id,
                source_text=source_text,
                source_lang=source_lang,
                target_lang=lang,
                translate_fn=translate_fn,
            )
        )


async def _translate_and_publish_secondary(
    manager: BroadcastManager,
    room_id: str,
    *,
    source_text: str,
    source_lang: str,
    target_lang: str,
    translate_fn: TranslateFn,
) -> None:
    """Background worker — translate and publish for one secondary lang.

    Errors are isolated and logged server-side (RL-006). A single failed
    translation never affects other languages or the primary channel.
    """
    try:
        translated = await translate_fn(source_text, source_lang, target_lang)
    except Exception as e:
        print(
            f"[SSE] secondary translate failed "
            f"(room={room_id} target={target_lang}): {e!r}"
        )
        return
    if not translated:
        return
    try:
        await manager.publish(
            room_id,
            target_lang,
            {
                "text": translated,
                "lang": target_lang,
                "timestamp": time.time(),
            },
        )
    except Exception as e:
        # publish never normally raises but be defensive in a background task.
        print(
            f"[SSE] secondary publish failed "
            f"(room={room_id} target={target_lang}): {e!r}"
        )


def _coerce_output_langs(raw: Any, fallback: str) -> list[str]:
    """Normalise output_langs into a list[str].

    rooms.output_langs is stored as a JSON-encoded TEXT column. RoomManager
    hydration may already decode it; this helper accepts both shapes plus
    None / malformed input and falls back to ``[fallback]``.
    """
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str)]
    if isinstance(raw, str) and raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                return [str(x) for x in decoded if isinstance(x, str)]
        except (ValueError, TypeError):
            pass
    return [fallback]


# ---------------------------------------------------------------------------
# Daemon-thread entry point (called from app.py / services.py)
# ---------------------------------------------------------------------------
def run_sse_server(
    *,
    broadcast_manager: BroadcastManager,
    room_repo: Any,
    host: str = "0.0.0.0",
    port: int,
) -> None:
    """Blocking entry point intended to be run in a daemon thread.

    Mirrors ``services.start_health_server`` style — owns its event loop,
    swallows-and-logs unexpected errors so that a transient init failure
    can't crash the Streamlit parent process.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = build_sse_app(broadcast_manager=broadcast_manager, room_repo=room_repo)

        async def _serve() -> None:
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            print(f"[SSE] viewer broadcast server: http://{host}:{port}")
            # Block forever; the daemon thread is killed on parent exit.
            while True:
                await asyncio.sleep(3600)

        loop.run_until_complete(_serve())
    except Exception as e:
        # RL-006: log internal error server-side, do not propagate.
        print(f"[SSE] server failed to start: {e!r}")
