"""
ISSUE-33 — 뷰어 지표 e2e (라이브 SSE 연결 기반).

aiohttp /stream/{room_id} 엔드포인트를 실제 TCP 포트에 띄우고, 별도 클라이언트
태스크가 SSE 로 접속한 시점에 ``BroadcastManager.get_metrics`` 가 반환하는
인메모리 카운트가 살아있는 뷰어 수와 일치하는지 확인한다. 또한 attached 된
metrics_repo (DB persistence hook) 가 실제 connect 시점에 누적/peak 를
업데이트하는지도 검증한다.

Why e2e (not pure unit)
-----------------------
- BroadcastManager 카운트의 *옳음* 은 unit 으로 충분히 검증되지만, 이 issue
  의 AC ("뷰어 접속/해제 시 카운트가 정확히 갱신된다") 는 실제 HTTP/SSE
  생명주기 (handshake, register, write) 가 동작하는지 함께 봐야 의미가 있다.
- 다른 e2e 와 동일하게 ``e2e`` 마크가 기본 deselect 되어 일반
  ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# Streamlit 의존성 회피.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


class _RecordingMetricsRepo:
    """metrics_repo stub — Broadcast 가 호출하는 두 메서드만 구현."""

    def __init__(self) -> None:
        self.totals: dict[str, int] = {}
        self.peaks: dict[str, int] = {}
        self.calls: list[tuple[str, int, int]] = []

    def update_viewer_metrics(
        self, room_id: str, *, total_delta: int, current: int
    ) -> bool:
        self.calls.append((room_id, total_delta, current))
        self.totals[room_id] = self.totals.get(room_id, 0) + total_delta
        self.peaks[room_id] = max(self.peaks.get(room_id, 0), current)
        return True

    def get_viewer_metrics(self, room_id: str) -> dict[str, int] | None:
        if room_id not in self.totals and room_id not in self.peaks:
            return None
        return {
            "total_viewers": self.totals.get(room_id, 0),
            "peak_viewers": self.peaks.get(room_id, 0),
        }


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def metrics_server():
    """Run aiohttp SSE app in a daemon thread.

    Yields (base_url, mgr, metrics_repo, loop).
    """
    from aiohttp import web

    from sse_broadcast import BroadcastManager, build_sse_app

    rows = {
        "live": {
            "id": "live",
            "name": "Live",
            "status": "active",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
        },
    }
    repo = _StubRoomRepo(rows)
    metrics_repo = _RecordingMetricsRepo()
    mgr = BroadcastManager(metrics_repo=metrics_repo)
    app = build_sse_app(broadcast_manager=mgr, room_repo=repo)

    port = _free_port()
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    started = threading.Event()

    def _serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", port)
        loop.run_until_complete(site.start())
        started.set()
        loop.run_forever()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    assert started.wait(timeout=5), "aiohttp server failed to start in 5s"

    base_url = f"http://127.0.0.1:{port}"
    yield (base_url, mgr, metrics_repo, loop)

    # Teardown
    try:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


def _open_sse_in_background(base_url: str, room_id: str, lang: str) -> socket.socket:
    """Open a raw TCP socket and send an SSE GET request — keep it open."""
    host = base_url.removeprefix("http://").split(":")[0]
    port = int(base_url.removeprefix("http://").split(":")[1])
    s = socket.create_connection((host, port), timeout=5)
    req = (
        f"GET /stream/{room_id}?lang={lang} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Accept: text/event-stream\r\n"
        "Connection: keep-alive\r\n\r\n"
    )
    s.sendall(req.encode("ascii"))
    # 첫 헤더/comment frame 까지 읽어서 등록을 강제 (서버가
    # ``: connected\n\n`` 을 prepare 한 직후 register_viewer 가 호출됨).
    s.recv(256)
    return s


def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestViewerMetricsLive:
    def test_single_viewer_count_visible_in_snapshot(self, metrics_server):
        """단일 SSE 뷰어 → snapshot.current == 1, by_lang['ko'] == 1."""
        base_url, mgr, _repo, _loop = metrics_server

        s = _open_sse_in_background(base_url, "live", "ko")
        try:
            assert _wait_for(lambda: mgr.get_metrics("live")["current"] == 1)
            m = mgr.get_metrics("live")
            assert m["current"] == 1
            assert m["by_lang"].get("ko") == 1
        finally:
            s.close()

        # 클라이언트 disconnect 가 반영되면 카운트는 0 으로 회복.
        assert _wait_for(lambda: mgr.get_metrics("live")["current"] == 0, timeout=5.0)

    def test_concurrent_viewers_reported_per_lang(self, metrics_server):
        """동시 다중 뷰어 (ko + en) → by_lang 분리 카운트."""
        base_url, mgr, _repo, _loop = metrics_server

        s_ko1 = _open_sse_in_background(base_url, "live", "ko")
        s_ko2 = _open_sse_in_background(base_url, "live", "ko")
        s_en = _open_sse_in_background(base_url, "live", "en")
        try:
            assert _wait_for(lambda: mgr.get_metrics("live")["current"] == 3)
            m = mgr.get_metrics("live")
            assert m["current"] == 3
            assert m["by_lang"].get("ko") == 2
            assert m["by_lang"].get("en") == 1
        finally:
            s_ko1.close()
            s_ko2.close()
            s_en.close()

        assert _wait_for(lambda: mgr.get_metrics("live")["current"] == 0, timeout=5.0)

    def test_db_metrics_repo_receives_increments(self, metrics_server):
        """metrics_repo 가 실제 SSE connect 마다 update_viewer_metrics 호출됨."""
        base_url, mgr, repo, _loop = metrics_server

        before_total = repo.totals.get("live", 0)
        before_peak = repo.peaks.get("live", 0)

        s = _open_sse_in_background(base_url, "live", "ko")
        try:
            assert _wait_for(
                lambda: repo.totals.get("live", 0) >= before_total + 1, timeout=3.0
            )
            assert repo.totals["live"] >= before_total + 1
            # peak 는 max — current 가 1 이상 도달했으면 peak 도 같거나 그 이상.
            assert repo.peaks["live"] >= max(before_peak, 1)
        finally:
            s.close()
