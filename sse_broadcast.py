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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# 뷰어 큐의 최대 길이. 청중이 일시적으로 끊겨도 backpressure 로 메모리가
# 무한 증가하지 않도록 막는 안전장치. 큐가 가득 차면 가장 오래된 메시지를
# 1건 비우고 새 메시지를 넣는다 (캡션 도메인 — 약간의 자막 손실은 허용,
# 메모리 누수는 불가).
_DEFAULT_QUEUE_MAXSIZE = 32

# Type alias for the translate callback the websocket_handler passes in.
# Signature: (text, source_lang, target_lang) -> translated_text
TranslateFn = Callable[[str, str, str], Awaitable[str | None]]


# ---------------------------------------------------------------------------
# BroadcastManager
# ---------------------------------------------------------------------------
class BroadcastManager:
    """(room_id, lang) 채널 기준 viewer 큐 레지스트리.

    websocket_handler 가 메인/추가 언어 번역 결과를 publish 하면, 해당
    채널을 구독 중인 모든 SSE viewer 큐에 payload 가 enqueue 된다.
    SSE handler 는 큐에서 dequeue 해 ``data: ...\\n\\n`` 포맷으로 송신한다.
    """

    def __init__(self, queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE) -> None:
        # (room_id, lang) -> set[asyncio.Queue]
        self._channels: dict[tuple[str, str], set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._queue_maxsize = queue_maxsize

    async def register_viewer(self, room_id: str, lang: str) -> asyncio.Queue:
        """Add a viewer to (room_id, lang) and return its dedicated queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._channels.setdefault((room_id, lang), set()).add(q)
        return q

    async def unregister_viewer(
        self, room_id: str, lang: str, queue: asyncio.Queue
    ) -> None:
        """Remove a viewer queue. No-op if the channel/queue is unknown."""
        key = (room_id, lang)
        async with self._lock:
            viewers = self._channels.get(key)
            if viewers is None:
                return
            viewers.discard(queue)
            if not viewers:
                # Drop empty channel so has_viewers reports False quickly
                # and lazy translation gates publication accurately.
                self._channels.pop(key, None)

    def has_viewers(self, room_id: str, lang: str) -> bool:
        """Return True iff at least one viewer is subscribed to (room, lang).

        Read-only: synchronous and lock-free for low-overhead use as the
        lazy-translation gate inside ``broadcast_translation_for_room``.
        Snapshot semantics — a viewer arriving milliseconds later is fine,
        the next published message will reach them.
        """
        viewers = self._channels.get((room_id, lang))
        return bool(viewers)

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
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop one old message, then put the new one.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # Still full (concurrent producer) — give up on this viewer.
                    print(
                        f"[SSE] dropping payload, viewer queue saturated "
                        f"(room={room_id} lang={lang})"
                    )

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


def _render_viewer_html(
    *,
    room_id: str,
    room_name: str,
    output_langs: list[str],
    primary_lang: str,
    initial_state: str,
) -> str:
    """Render the viewer template with safe substitutions.

    Text fields (room_id/room_name/primary_lang/initial_state) are HTML-
    escaped so a malicious room name (DB write, not user-facing) cannot
    inject markup. The output_langs list is rendered as a JSON literal —
    json.dumps already escapes the dangerous characters for an HTML script
    context (``<`` / ``>`` / ``&``).
    """
    template = _VIEWER_TEMPLATE_PATH.read_text(encoding="utf-8")
    safe_room_id = html.escape(room_id, quote=True)
    safe_room_name = html.escape(room_name or room_id, quote=True)
    safe_primary = html.escape(primary_lang or "ko", quote=True)
    safe_initial = html.escape(initial_state, quote=True)
    # json.dumps is sufficient for embedding inside <script>; the output is
    # a valid JS array literal with no closing-tag risk for our token set.
    langs_json = json.dumps(output_langs, ensure_ascii=False)

    return (
        template.replace("{{ROOM_ID}}", safe_room_id)
        .replace("{{ROOM_NAME}}", safe_room_name)
        .replace("{{OUTPUT_LANGS_JSON}}", langs_json)
        .replace("{{PRIMARY_LANG}}", safe_primary)
        .replace("{{INITIAL_STATE}}", safe_initial)
    )


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
    output_langs = _coerce_output_langs_list(room.get("output_langs"), primary_lang)
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

    try:
        while True:
            try:
                # Periodic timeout doubles as a keep-alive heartbeat —
                # proxies / browsers that idle-kill connections will see
                # a ":\n\n" comment line every 15s.
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                try:
                    await resp.write(b": ping\n\n")
                except (ConnectionResetError, asyncio.CancelledError):
                    break
                continue
            except asyncio.CancelledError:
                break

            try:
                await _write_sse_event(resp, "message", payload)
            except (ConnectionResetError, asyncio.CancelledError):
                break
            except Exception as e:
                # RL-006: log internal write error, do not echo to client.
                print(
                    f"[SSE] write error (room={room_id} lang={requested_lang}): {e!r}"
                )
                break
    finally:
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

    # 2) Resolve secondary languages.
    output_langs = _coerce_output_langs(room.get("output_langs"), primary_lang)
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
