"""백그라운드 서버 기동이 **프로세스 단위**인지, 그리고 부팅 경로가 맞는지 (#158).

원래 두 서버(WS 8765 / SSE 8766)는 `app.py` 안에서 `st.session_state` 게이트로
기동했다. `st.session_state` 는 **브라우저 세션 단위**라, 아무도 `:8501` 을 열지
않으면 Streamlit 이 스크립트를 실행하지 않고 두 서버도 뜨지 않았다 — 컨테이너를
재시작하면 오퍼레이터가 접속할 때까지 `/view/*`·`/stream/*`·`/stage/*`·`/ws` 가
전부 죽어 있었다.

여기서 지키는 것은 세 가지다.
  1. 기동 함수가 **프로세스 전역 멱등** (Streamlit 은 세션마다/리런마다 스크립트를
     돌리므로 이 성질이 없으면 스레드가 증식한다)
  2. `app.py` 에 **session_state 기반 스레드 게이트가 남아 있지 않다**
  3. 부팅 경로가 `serve.py` 이고, 거기서 **Streamlit 보다 먼저** 서버를 띄운다
"""

from __future__ import annotations

import ast
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# websocket_handler -> auth -> streamlit / extra_streamlit_components 의존성 회피
# (tests/test_rooms_db.py 와 동일 패턴). 둘 다 막아야 한다 — streamlit 만 stub 하면
# `extra_streamlit_components` 가 `streamlit.components` 를 찾다 터진다.
if "streamlit" not in sys.modules:
    sys.modules["streamlit"] = MagicMock()
if "extra_streamlit_components" not in sys.modules:
    sys.modules["extra_streamlit_components"] = MagicMock()


@pytest.fixture
def fresh_module(monkeypatch):
    """`background_servers` 를 깨끗한 상태로 다시 import 한다.

    모듈 전역 `_STATE` 를 쓰므로, 테스트끼리 기동 상태가 새면 멱등성 검증이
    무의미해진다.
    """
    sys.modules.pop("background_servers", None)
    import background_servers as mod

    yield mod
    sys.modules.pop("background_servers", None)


class TestStartIsProcessWideIdempotent:
    """두 번째 호출이 스레드를 또 띄우지 않는다."""

    @staticmethod
    def _stub_dependencies(monkeypatch, started):
        """실제 소켓을 열지 않도록 스레드 타깃과 저장소를 stub 으로 바꾼다."""
        import threading

        real_thread = threading.Thread

        def _counting_thread(*args, **kwargs):
            started.append(kwargs.get("name", "?"))
            # target 을 no-op 로 바꿔 실제 서버가 뜨지 않게 한다.
            kwargs["target"] = lambda *a, **k: None
            return real_thread(*args, **kwargs)

        monkeypatch.setattr("threading.Thread", _counting_thread)

        for name, attr in [
            ("database", "get_room_model"),
            ("websocket_handler", "attach_broadcast_metrics_repo"),
            ("websocket_handler", "get_broadcast_manager"),
            ("websocket_handler", "start_websocket_server"),
            ("sse_broadcast", "run_sse_server"),
        ]:
            monkeypatch.setattr(f"{name}.{attr}", MagicMock(), raising=False)

    def test_second_call_starts_no_new_threads(self, fresh_module, monkeypatch):
        started: list[str] = []
        self._stub_dependencies(monkeypatch, started)

        first = fresh_module.start_background_servers()
        count_after_first = len(started)
        second = fresh_module.start_background_servers()

        assert count_after_first == 2, f"WS/SSE 두 스레드가 떠야 한다: {started}"
        assert len(started) == count_after_first, (
            f"두 번째 호출이 스레드를 더 띄웠다: {started}. Streamlit 은 리런마다 "
            "스크립트를 돌리므로 매 리런 스레드가 증식한다."
        )
        assert first["started"] is True
        assert second["started"] is True

    def test_state_reports_the_sse_port(self, fresh_module, monkeypatch):
        started: list[str] = []
        self._stub_dependencies(monkeypatch, started)
        monkeypatch.setenv("SSE_PORT", "9999")

        state = fresh_module.start_background_servers()

        assert state["sse_port"] == 9999

    def test_sse_port_is_read_at_call_time(self, fresh_module, monkeypatch):
        """`os.getenv` 를 import 시점에 고정하지 않는다 (DB_PATH 와 같은 관례)."""
        monkeypatch.setenv("SSE_PORT", "7777")
        assert fresh_module.sse_port() == 7777
        monkeypatch.setenv("SSE_PORT", "7778")
        assert fresh_module.sse_port() == 7778


class TestAppNoLongerGatesThreadsOnSessionState:
    """`app.py` 에 세션 단위 스레드 게이트가 남아 있지 않다."""

    def test_no_thread_handles_in_session_state(self):
        src = (REPO / "app.py").read_text(encoding="utf-8")
        for leaked in ('"websocket_thread"', '"sse_thread"'):
            assert f"st.session_state[{leaked}]" not in src, (
                f"app.py 가 아직 {leaked} 를 session_state 에 보관한다 — "
                "브라우저 세션이 없으면 서버가 뜨지 않는 상태로 되돌아간다 (#158)."
            )

    def test_app_delegates_to_the_shared_starter(self):
        src = (REPO / "app.py").read_text(encoding="utf-8")
        assert "start_background_servers()" in src, (
            "app.py 가 공유 기동 함수를 부르지 않는다 — `streamlit run app.py` "
            "개발 경로에서 서버가 뜨지 않는다."
        )


class TestServeIsTheBootPath:
    """`serve.py` 가 Streamlit 보다 **먼저** 서버를 띄운다."""

    @staticmethod
    def _main_body() -> list[ast.stmt]:
        tree = ast.parse((REPO / "serve.py").read_text(encoding="utf-8"))
        main = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
        )
        return main.body

    def test_servers_start_before_streamlit_bootstrap(self):
        """순서가 핵심이다 — bootstrap 뒤에 부르면 아무 의미가 없다.

        `bootstrap.run` 은 블로킹이라, 그 뒤의 코드는 서버가 멈출 때까지
        실행되지 않는다.
        """
        lines = ast.dump(ast.Module(body=self._main_body(), type_ignores=[]))
        start_at = lines.find("start_background_servers")
        boot_at = lines.find("bootstrap")
        assert start_at != -1, "serve.py main() 이 서버를 띄우지 않는다"
        assert boot_at != -1, "serve.py main() 이 Streamlit 을 띄우지 않는다"
        assert start_at < boot_at, (
            "serve.py 가 Streamlit 을 먼저 띄운다 — bootstrap.run 은 블로킹이라 "
            "그 뒤의 서버 기동은 영영 실행되지 않는다 (#158)."
        )

    def test_dockerfile_runs_serve_not_streamlit_run(self):
        """컨테이너 엔트리포인트가 `serve.py` 다.

        `streamlit run app.py` 로 되돌아가면 lazy-start 가 그대로 복귀한다 —
        이 저장소에서 가장 비싼 회귀 중 하나다.
        """
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        cmd_lines = [
            line
            for line in dockerfile.splitlines()
            if line.startswith("CMD") and not line.lstrip().startswith("#")
        ]
        assert cmd_lines, "Dockerfile 에 CMD 가 없다"
        cmd = cmd_lines[-1]
        assert "serve.py" in cmd, f"CMD 가 serve.py 를 쓰지 않는다: {cmd}"
        assert (
            "streamlit" not in cmd
        ), f"CMD 가 streamlit 을 직접 실행한다: {cmd}. lazy-start 가 복귀한다."
