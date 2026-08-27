"""
ISSUE-31 — 뷰어 페이지 e2e (browser-driven).

aiohttp /view/{room_id} 엔드포인트를 실제 TCP 포트에 띄우고, Playwright
브라우저로 접속해 다음을 검증한다:

  1. 알려진 룸: 페이지가 200 + HTML 로 응답하고, 언어 셀렉터/대기 상태가
     실제 DOM 으로 렌더링된다.
  2. 알 수 없는 룸: 404 본문이 친절한 안내 메시지를 보여주고, 내부 디테일
     (Traceback, 파일 경로) 은 노출하지 않는다 (RL-006).
  3. closed 룸: ended 상태가 즉시 활성화되고 종료 카피가 보인다.

ISSUE-46 이 추가한 ``TestViewerCaptionStream`` 은 자막 파이프라인의 네 결함
(연속 final 유실 / 빈 final 공백화 / 줄어든 partial 잔상 / DOM 리셋·트리밍)을
브라우저에서 값으로 검증한다 — stage 쪽 대응 테스트는
``tests/e2e/test_stage_page_e2e.py`` 의 ``TestStageCaptionStream`` 이다.

Streamlit 서버가 필요 없는 e2e — aiohttp TestServer 는 session-scope 로
띄워두고 Playwright 가 그 위에 직접 접속한다. fullscreen e2e 와 동일하게
``e2e`` 마크가 기본 deselect 되어 일반 ``pytest -q`` 실행을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

# Streamlit 의존성 회피 (다른 테스트와 동일 패턴).
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


pytestmark = pytest.mark.e2e


# ISSUE-44 — 따옴표 + 끝 백슬래시. `html.escape` 는 `\` 를 건드리지 않으므로
# 그대로 JS 문자열 리터럴에 넣으면 닫는 따옴표가 탈출되어 부트스트랩이 죽는다.
_HOSTILE_ROOM_ID = 'q"b\\'
# `&` 는 `html.escape` 로 `&amp;` 가 되는데 `textContent` 는 엔티티를 디코드하지
# 않는다 — 스크립트 컨텍스트에 마크업 이스케이퍼를 쓴 대가 (이중 이스케이프).
_AMPERSAND_NAME = "A홀 & B홀"


# ---------------------------------------------------------------------------
# Stub repo + aiohttp server fixture
# ---------------------------------------------------------------------------
class _StubRoomRepo:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    def get_by_id(self, room_id: str) -> dict[str, Any] | None:
        return self._rows.get(room_id)


def _free_port() -> int:
    """Bind to port 0 and immediately release — returns a likely-free port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def viewer_server():
    """Run aiohttp app in a daemon thread, yielding (base_url, repo)."""
    from aiohttp import web

    from sse_broadcast import BroadcastManager, build_sse_app

    rows = {
        "active-room": {
            "id": "active-room",
            "name": "E2E Active Room",
            "status": "active",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
        },
        "closed-room": {
            "id": "closed-room",
            "name": "E2E Closed Room",
            "status": "closed",
            "primary_output_lang": "ko",
            "output_langs": '["ko"]',
        },
        # ISSUE-44 — 따옴표 + 끝 백슬래시 id, 앰퍼샌드 이름.
        _HOSTILE_ROOM_ID: {
            "id": _HOSTILE_ROOM_ID,
            "name": _AMPERSAND_NAME,
            "status": "waiting",
            "primary_output_lang": "ko",
            "output_langs": '["ko","en"]',
        },
    }
    repo = _StubRoomRepo(rows)
    mgr = BroadcastManager()
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
    yield base_url

    # Teardown
    try:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(timeout=5)
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)


# ---------------------------------------------------------------------------
# ISSUE-46 — 자막 파이프라인 하네스
# ---------------------------------------------------------------------------
# 진짜 EventSource 를 쓰면 SSE 프레임 타이밍이 테스트에 섞여 flaky 해지고,
# 201건 주입 같은 시나리오를 서버 쪽에서 만들어야 한다. 네트워크 레벨에서
# 생성 URL 만 기록하고 이벤트를 손으로 밀어 넣는 스텁으로 대체한다.
# (tests/e2e/test_stage_page_e2e.py 의 같은 하네스를 옮겨 온 것 — 두 e2e 모듈은
# 서로를 import 하지 않는다.)
_FAKE_EVENTSOURCE = """
(() => {
  window.__sse = { urls: [], last: null };
  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.readyState = 0;
      this.closed = false;
      this._listeners = {};
      window.__sse.urls.push(this.url);
      window.__sse.last = this;
    }
    addEventListener(type, fn) {
      if (!this._listeners[type]) this._listeners[type] = [];
      this._listeners[type].push(fn);
    }
    removeEventListener(type, fn) {
      const fns = this._listeners[type];
      if (!fns) return;
      const i = fns.indexOf(fn);
      if (i !== -1) fns.splice(i, 1);
    }
    close() {
      this.closed = true;
      this.readyState = 2;
    }
    _emit(type, data) {
      const event = { type: type, data: data };
      for (const fn of (this._listeners[type] || []).slice()) fn(event);
    }
  }
  window.EventSource = FakeEventSource;
  window.__emit = (type, data) => {
    const es = window.__sse.last;
    if (!es) throw new Error("no EventSource was constructed by the viewer page");
    es._emit(type, data);
  };
})();
"""


def _fake_eventsource(page) -> None:
    """Replace `window.EventSource` with a recording stub (must precede goto)."""
    page.add_init_script(_FAKE_EVENTSOURCE)


def _settle(page, predicate: str, timeout: int = 4000) -> None:
    """Wait until `predicate` holds, swallowing the timeout.

    On failure we want the test's own assertion to report what the DOM
    actually looks like, not an opaque Playwright timeout.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        page.wait_for_function(predicate, timeout=timeout)
    except PlaywrightTimeout:
        pass


def _lines(page) -> list[str]:
    return page.eval_on_selector_all(
        ".caption-line", "els => els.map(el => el.textContent)"
    )


def _emit_message(page, text: str, *, partial: bool = False) -> None:
    payload = {"text": text}
    if partial:
        payload["partial"] = True
    page.evaluate(
        "(payload) => window.__emit('message', JSON.stringify(payload))", payload
    )


# ---------------------------------------------------------------------------
# Browser-driven assertions
# ---------------------------------------------------------------------------
class TestViewerPageInBrowser:
    def test_active_room_renders_language_selector(self, page, viewer_server):
        """활성 룸 → 페이지 200 + #lang-select 가 DOM 에 존재한다."""
        resp = page.goto(f"{viewer_server}/view/active-room", wait_until="load")
        assert resp is not None
        assert resp.status == 200, f"unexpected status: {resp.status}"

        sel = page.locator("#lang-select")
        sel.wait_for(state="attached", timeout=5000)
        assert sel.count() == 1

        # waiting/active 컨테이너 노드도 DOM 에 있어야 한다.
        assert page.locator("#state-waiting").count() == 1
        assert page.locator("#state-active").count() == 1

        # 룸 이름이 화면에 노출된다 (template 주입 검증).
        body_text = page.locator("body").inner_text()
        assert "E2E Active Room" in body_text

    def test_unknown_room_shows_friendly_404(self, page, viewer_server):
        """존재하지 않는 룸 → 404 + 친절한 안내, 내부 디테일 비노출 (RL-006)."""
        resp = page.goto(f"{viewer_server}/view/no-such-room", wait_until="load")
        assert resp is not None
        assert resp.status == 404

        body_text = page.locator("body").inner_text()
        # 사용자 친화 메시지 노출
        assert (
            "찾을 수 없" in body_text
            or "존재하지 않" in body_text
            or "not found" in body_text.lower()
        )
        # 내부 디테일은 비노출
        assert "Traceback" not in body_text
        assert "RuntimeError" not in body_text

    def test_closed_room_shows_ended_copy(self, page, viewer_server):
        """closed 룸 → ended 카피가 즉시 노출된다."""
        resp = page.goto(f"{viewer_server}/view/closed-room", wait_until="load")
        assert resp is not None
        assert resp.status == 200

        body_text = page.locator("body").inner_text()
        assert "세션이 종료되었습니다" in body_text

    def test_backslash_room_id_does_not_kill_the_bootstrap(self, page, viewer_server):
        """ISSUE-44 AC 2 — `\\` 로 끝나는 room_id 가 JS 리터럴을 탈출하지 않는다.

        `html.escape` 는 백슬래시를 건드리지 않으므로 그대로 쓰면 스크립트
        블록 전체가 SyntaxError 로 죽는다 — 언어 셀렉터도 상태 전환도 함께
        사라진다. 값 손상(`"` → `&quot;`)까지 브라우저에서 확인한다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        resp = page.goto(f"{viewer_server}/view/q%22b%5C", wait_until="load")
        assert resp is not None and resp.status == 200
        assert errors == [], f"bootstrap raised JS errors: {errors}"

        # 1) 부트스트랩이 살아 있고 값이 손상되지 않았다.
        assert page.evaluate("typeof CONFIG !== 'undefined'") is True
        assert page.evaluate("CONFIG.room_id") == _HOSTILE_ROOM_ID

        # 2) 언어 셀렉터가 CONFIG 로부터 실제로 채워졌다 (죽으면 0개).
        options = page.eval_on_selector_all(
            "#lang-select option", "els => els.map(el => el.value)"
        )
        assert options == page.evaluate("CONFIG.output_langs")
        assert len(options) >= 2

        # 3) 상태 전환이 동작한다 — 부트스트랩이 waiting 을 활성화했고,
        #    setState 가 전역에 살아 있어 ended 로 넘어간다.
        assert page.locator("#state-waiting").is_visible() is True
        page.evaluate("setState('ended')")
        assert page.locator("#state-ended").is_visible() is True
        assert page.locator("#state-waiting").is_visible() is False

    def test_ampersand_room_name_is_not_double_escaped(self, page, viewer_server):
        """ISSUE-44 AC 4 — `A홀 & B홀` 이 `A홀 &amp; B홀` 로 보이지 않는다.

        `#room-name` 은 서버 렌더 마크업(엔티티 디코드됨), `#waiting-room` 은
        `CONFIG.room_name` 을 `textContent` 로 쓴 결과(디코드 안 됨)다. 두 노드가
        같은 문자열이어야 sink 별 이스케이퍼가 올바르게 골라진 것이다.
        """
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{viewer_server}/view/q%22b%5C", wait_until="load")
        assert errors == [], f"bootstrap raised JS errors: {errors}"

        assert page.locator("#room-name").inner_text().strip() == _AMPERSAND_NAME
        assert page.locator("#waiting-room").inner_text().strip() == _AMPERSAND_NAME


# ---------------------------------------------------------------------------
# ISSUE-46 — 자막 파이프라인 파리티 (stage.html `cc0681f` 와 동일 동작)
# ---------------------------------------------------------------------------
class TestViewerCaptionStream:
    """공개 뷰어(`/view/{room_id}`)의 자막 파이프라인 결함 회귀 가드.

    ISSUE-41 은 이 로직을 stage 로 복사하면서 복사본에서만 고쳤다 (RL-001).
    여기서는 원본을 **값으로** 검증한다 — 개수만 세거나 "라인이 존재한다" 로
    끝내면 결함이 살아 있는 구현도 통과한다 (RL-004).
    """

    def test_back_to_back_finals_keep_both_lines(self, page, viewer_server):
        """결함 1 — 연속 final 이 앞 자막을 유실시키면 안 된다 (헤드라인).

        두 final 을 **한 evaluate 안에서** 밀어 넣는다 — 28ms 인터벌 틱이
        사이에 낄 수 없는, SSE 가 몰아치는 실제 행사의 모양이다. 확정 분기가
        없으면 라인 1이 두 번째 자막으로 덮여 첫 자막이 한 글자도 남지 않는다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        page.evaluate(
            """() => {
              window.__emit("message", JSON.stringify({text: "첫 문장"}));
              window.__emit("message", JSON.stringify({text: "둘째 문장"}));
            }"""
        )
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 2 && els[1].textContent === "둘째 문장";
            }""",
        )

        lines = _lines(page)
        dropped = (
            f"back-to-back finals rendered {lines!r} — the first caption was "
            "dropped (finalizeCaption must lock the pending line first)"
        )
        assert lines == ["첫 문장", "둘째 문장"], dropped

    def test_a_final_after_a_locked_line_opens_a_new_line(self, page, viewer_server):
        """결함 1 — partial 이 흐르던 라인이 확정된 뒤 다음 final 은 새 줄이다.

        partial 이 정착한 뒤 두 final 을 연속 주입한다: 첫 final 은 진행 중인
        라인을 확정하고, 둘째 final 은 **새 라인**을 열어야 한다. 두 자막이
        한 줄로 합쳐지면 실패한다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        _emit_message(page, "진행 중", partial=True)
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === "진행 중";
            }""",
        )

        page.evaluate(
            """() => {
              window.__emit("message", JSON.stringify({text: "첫 자막"}));
              window.__emit("message", JSON.stringify({text: "둘째 자막"}));
            }"""
        )
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 2 && els[1].textContent === "둘째 자막";
            }""",
        )

        lines = _lines(page)
        merged = f"the two finals merged into {lines!r} instead of two lines"
        assert lines == ["첫 자막", "둘째 자막"], merged

    def test_empty_finals_do_not_blank_the_column(self, page, viewer_server):
        """결함 2 — 빈 final 이 대기 문구를 지우고 컬럼을 공백화하면 안 된다.

        `broadcast_translation_for_room` 에 비어있음 가드가 없고 AWS Translate
        는 구두점만 있는 입력에 `""` 를 돌려준다. 그대로 통과시키면
        `_ensureCurrentLine()` 이 `#caption-empty` 를 제거한 뒤 빈
        `.caption-line` 만 쌓인다 (test_stage_page_e2e.py 의
        `test_empty_final_does_not_blank_the_column` 미러링).
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        for _ in range(3):
            _emit_message(page, "")
        page.wait_for_timeout(300)

        state = page.evaluate(
            """() => ({
              lines: [...document.querySelectorAll(".caption-line")]
                       .map(el => el.textContent),
              placeholder: document.getElementById("caption-empty") === null
                ? null
                : document.getElementById("caption-empty").textContent,
            })"""
        )
        blanked = f"empty finals left the column in {state}"
        assert state["lines"] == [], blanked
        assert state["placeholder"] == "잠시 후 시작됩니다", blanked

        # 진행 중인 partial 이 있을 때의 빈 final 은 그 라인을 확정하는 신호다.
        _emit_message(page, "이어지는 문장입니다", partial=True)
        _emit_message(page, "")
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 &&
                     els[0].textContent === "이어지는 문장입니다";
            }""",
        )
        fallback = f"the empty-final fallback left {_lines(page)!r}"
        assert _lines(page) == ["이어지는 문장입니다"], fallback

        # 확정된 라인이므로 다음 final 은 **두 번째** 줄로 열려야 한다.
        _emit_message(page, "다음")
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 2 && els[1].textContent === "다음";
            }""",
        )
        reopened = f"the next final did not open a new line: {_lines(page)!r}"
        assert _lines(page) == ["이어지는 문장입니다", "다음"], reopened

    def test_a_shrinking_partial_repaints_instead_of_leaving_stale_text(
        self, page, viewer_server
    ):
        """결함 3 — 짧아진 partial 은 화면을 즉시 줄여야 한다.

        번역 후처리는 문자열을 깎기도 한다. `twShown` 을 클램프만 하면 `gap`
        이 0 이 되어 쓰기 분기를 건너뛰므로 이전의 더 긴 텍스트가 잔상으로
        남는다. `startswith` / 길이 비교가 아니라 완전 일치로 단언한다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        long_text = "안녕하세요 반갑습니다"
        _emit_message(page, long_text, partial=True)
        _settle(
            page,
            f"""() => {{
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === {json.dumps(long_text)};
            }}""",
        )
        revealed = f"the long partial never fully revealed: {_lines(page)!r}"
        assert _lines(page) == [long_text], revealed

        _emit_message(page, "안녕하세요", partial=True)
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 1 && els[0].textContent === "안녕하세요";
            }""",
        )
        stale = f"line still reads {_lines(page)!r} after the target shrank"
        assert _lines(page) == ["안녕하세요"], stale

    def test_two_hundred_one_finals_trim_to_two_hundred_lines(
        self, page, viewer_server
    ):
        """결함 1/4 — DOM 상한 200. 개수와 **값** 을 함께 단언한다 (RL-004).

        살아남은 첫 줄이 2번째 페이로드여야 가장 오래된 **요소**가 지워졌음이
        증명된다 — `firstChild` 로 지우면 공백 텍스트 노드부터 걷어낸다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        page.evaluate(
            """() => {
              for (let i = 1; i <= 201; i++) {
                window.__emit("message", JSON.stringify({text: "자막 " + i}));
              }
            }"""
        )
        _settle(
            page,
            """() => {
              const els = document.querySelectorAll(".caption-line");
              return els.length === 200 &&
                     els[els.length - 1].textContent === "자막 201";
            }""",
            timeout=10000,
        )

        lines = _lines(page)
        capped = f"expected exactly 200 caption lines, got {len(lines)}"
        assert len(lines) == 200, capped
        oldest = f"oldest surviving line is {lines[0]!r}, want the 2nd payload"
        assert lines[0] == "자막 2", oldest
        newest = f"newest line is {lines[-1]!r}, want the 201st payload"
        assert lines[-1] == "자막 201", newest

    def test_language_switch_clears_captions_and_restores_the_placeholder(
        self, page, viewer_server
    ):
        """결함 4 회귀 가드 — `replaceChildren()` 전환 후에도 언어 전환이 같다.

        자막 스택은 비워지고, 새 언어의 대기 문구를 담은 `#caption-empty` 가
        **정확히 1개** 다시 생긴다. 비움 단언이 공허하지 않도록 전환 직전에
        자막이 실제로 쌓여 있었음을 먼저 확인한다 (RL-026).
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        page.evaluate(
            """() => {
              for (const t of ["첫 줄", "둘째 줄", "셋째 줄"]) {
                window.__emit("message", JSON.stringify({text: t}));
              }
            }"""
        )
        _settle(
            page,
            """() => document.querySelectorAll(".caption-line").length >= 1""",
        )
        seeded = "no captions were rendered — the clear assertion would be vacuous"
        assert len(_lines(page)) >= 1, seeded

        page.select_option("#lang-select", "en")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              lines: document.querySelectorAll(".caption-line").length,
              placeholders: document.querySelectorAll("#caption-empty").length,
              text: document.getElementById("caption-empty") === null
                ? null
                : document.getElementById("caption-empty").textContent,
            })"""
        )
        cleared = f"language switch left the column in {state}"
        assert state["lines"] == 0, cleared
        assert state["placeholders"] == 1, cleared
        assert state["text"] == "Starting shortly", cleared


# ---------------------------------------------------------------------------
# 자막 라이브 리전 (ISSUE-45 / ISSUE-41 FU-3)
# ---------------------------------------------------------------------------
_ANNOUNCER = "() => document.getElementById('caption-announcer').textContent"

# 라이브 리전이 실제로 **몇 번** 쓰였는지 기록한다. "확정 텍스트와 일치한다" 만
# 보면 28ms 틱마다 갱신하는 구현도 마지막 값이 같아서 통과한다 (RL-004).
_RECORD_ANNOUNCEMENTS = """
(final) => {
  const el = document.getElementById("caption-announcer");
  window.__annSamples = [];
  window.__midSample = null;
  new MutationObserver(() => {
    const text = el.textContent;
    const seen = window.__annSamples;
    if (seen.length === 0 || seen[seen.length - 1] !== text) seen.push(text);
  }).observe(el, {childList: true, characterData: true, subtree: true});
  // 공개 도중 한 번 샘플: .caption-line 이 final 보다 **짧은** 순간.
  const iv = setInterval(() => {
    const line = document.querySelector(".caption-line:last-child");
    if (line === null || window.__midSample !== null) return;
    const shown = line.textContent;
    if (shown.length > 0 && shown.length < final.length) {
      window.__midSample = el.textContent;
      clearInterval(iv);
    }
  }, 8);
}
"""


class TestViewerLiveRegion:
    """RL-019 (행동) — 라이브 리전은 **확정된 줄에서만** 갱신된다.

    정적 테스트는 속성의 위치만 본다. 여기서는 실제 브라우저에서 partial 이
    흐르는 동안 announcer 가 조용한지, 애니메이션 노드가 런타임 DOM 에서도
    `aria-hidden` 인지, 그리고 `aria-hidden` 안에 갇힌 대기 문구가 그래도
    스크린리더에 닿는지를 **값으로** 검증한다.

    무대 쪽 대응은 `tests/e2e/test_stage_page_e2e.py`
    `test_only_finalised_lines_reach_the_live_region` /
    `test_waiting_state_is_announced_to_screen_readers`.
    """

    def test_only_finalised_lines_reach_the_live_region(self, page, viewer_server):
        """partial 3건 + final 1건 — announcer 는 확정 텍스트와 정확히 일치한다.

        공개 도중 샘플한 값이 final 이 아니고, 이 라인에 대해 라이브 리전이
        **정확히 한 번** 쓰였음을 함께 단언한다. 28ms 틱마다 쓰는 구현은 마지막
        값이 같아 최종 일치 단언만으로는 통과해 버린다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        final = "오늘 발표를 시작하겠습니다. 자막은 실시간으로 이어집니다."
        for partial in ("오늘", "오늘 발표를", "오늘 발표를 시작"):
            _emit_message(page, partial, partial=True)
        page.wait_for_timeout(300)

        during = page.evaluate(_ANNOUNCER)
        leaked = (
            f"#caption-announcer holds {during!r} while only partials have "
            "arrived — the live region must not carry unfinalised text"
        )
        assert during != final, leaked
        # active 로 전환되며 announcer 가 대기 문구를 들고 있는 상태다 (UI-7).
        assert during == "잠시 후 시작됩니다", leaked

        page.evaluate(_RECORD_ANNOUNCEMENTS, final)
        _emit_message(page, final)
        _settle(
            page,
            f"""() => {{
              const el = document.getElementById("caption-announcer");
              return el !== null && el.textContent === {json.dumps(final)};
            }}""",
        )

        result = page.evaluate(
            """() => {
              const box = document.getElementById("captionContainer");
              return {
                announced: document.getElementById("caption-announcer").textContent,
                samples: window.__annSamples,
                mid: window.__midSample,
                hidden: box.getAttribute("aria-hidden"),
                live: box.getAttribute("aria-live"),
              };
            }"""
        )

        exact = f"announcer reads {result['announced']!r}, want {final!r}"
        assert result["announced"] == final, exact

        ticked = (
            f"the live region took {result['samples']!r} for one line — a "
            "finalised line must be written exactly once (RL-019: writing from "
            "the 28ms interval callback floods the screen reader)"
        )
        assert result["samples"] == [final], ticked

        sampled = (
            f"mid-reveal the announcer already read {result['mid']!r} — the "
            "final text must not appear until the line is locked"
        )
        assert result["mid"] is not None, "no mid-reveal sample was captured"
        assert result["mid"] != final, sampled
        assert result["mid"] == "잠시 후 시작됩니다", sampled

        exposed = (
            f"#captionContainer aria-hidden is {result['hidden']!r} — the "
            "per-tick typewriter node must not be exposed"
        )
        assert result["hidden"] == "true", exposed
        flooded = (
            f"#captionContainer still carries aria-live={result['live']!r}; every "
            "reveal tick would be announced (RL-019)"
        )
        assert result["live"] is None, flooded

    def test_the_active_waiting_copy_reaches_the_live_region(self, page, viewer_server):
        """AC — active 인데 `#caption-empty` 만 보이는 상태도 낭독되어야 한다.

        빈 final 은 `finalizeCaption("")` 에서 조기 반환하므로 페이지는 active
        인데 대기 문구만 남는다. 그 문구는 `aria-hidden` 컨테이너 **안**이라
        그대로 두면 무음이다 — 무대 페이지가 빠졌던 함정(UI-7)이다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        _emit_message(page, "")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              active: document
                .getElementById("state-active")
                .classList.contains("active"),
              lines: document.querySelectorAll(".caption-line").length,
              visible: document.getElementById("caption-empty").textContent,
              announced: document.getElementById("caption-announcer").textContent,
              hidden: document
                .getElementById("captionContainer")
                .getAttribute("aria-hidden"),
            })"""
        )
        silent = (
            "the waiting copy is inside an aria-hidden subtree and the announcer "
            f"is empty — screen readers get silence in the active state: {state}"
        )
        assert state["active"] is True, state
        assert state["lines"] == 0, state
        assert state["hidden"] == "true", state
        assert state["visible"] == "잠시 후 시작됩니다", state
        assert state["announced"] == "잠시 후 시작됩니다", silent

    def test_language_switch_announces_the_new_waiting_copy(self, page, viewer_server):
        """`clearCaptions()` 가 대기 문구를 재생성한 뒤 **새 언어** 문구가 들린다.

        핸들러 순서가 함정이다: `clearCaptions()`(아직 옛 `currentLang`) →
        `connect(next)`(여기서 `currentLang` 이 바뀐다) → `applyWaitingText(next)`.
        announcer 는 최종적으로 새 언어의 문구를 들고 있어야 한다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        page.evaluate(
            """() => {
              for (const t of ["첫 줄", "둘째 줄", "셋째 줄"]) {
                window.__emit("message", JSON.stringify({text: t}));
              }
            }"""
        )
        _settle(page, "() => document.querySelectorAll('.caption-line').length >= 1")
        seeded = "no captions were rendered — the switch assertion would be vacuous"
        assert len(_lines(page)) >= 1, seeded

        page.select_option("#lang-select", "en")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              visible: document.getElementById("caption-empty").textContent,
              announced: document.getElementById("caption-announcer").textContent,
              lines: document.querySelectorAll(".caption-line").length,
            })"""
        )
        stale = (
            "after the language switch the announcer holds "
            f"{state['announced']!r} — it must carry the NEW language's waiting "
            f"copy, matching what is on screen: {state}"
        )
        assert state["lines"] == 0, state
        assert state["visible"] == "Starting shortly", state
        assert state["announced"] == "Starting shortly", stale

    def test_session_end_leaves_the_announcer_silent(self, page, viewer_server):
        """AC — 종료 문구를 announcer 가 되풀이하지 않는다 (이중 낭독 금지).

        `#state-ended` 가 이미 `aria-live="polite"` 로 직접 알린다.
        """
        _fake_eventsource(page)
        page.goto(f"{viewer_server}/view/active-room", wait_until="load")

        _emit_message(page, "마지막 자막입니다")
        _settle(
            page,
            """() => document.getElementById("caption-announcer").textContent
                     === "마지막 자막입니다";""",
        )
        assert page.evaluate(_ANNOUNCER) == "마지막 자막입니다"

        page.evaluate("() => window.__emit('session_end', '')")
        page.wait_for_timeout(200)

        state = page.evaluate(
            """() => ({
              announced: document.getElementById("caption-announcer").textContent,
              ended: document
                .getElementById("state-ended")
                .classList.contains("active"),
            })"""
        )
        doubled = (
            f"the announcer reads {state['announced']!r} after session_end — "
            "#state-ended announces the ended copy itself, so anything left "
            "here is a second read of the same information"
        )
        assert state["ended"] is True, state
        assert state["announced"] == "", doubled
