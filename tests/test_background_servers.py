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


class TestBootPathDoesNotPoisonComponentRegistration:
    """`serve.py` 경로의 import 가 커스텀 컴포넌트 등록을 깨지 않는지 (#174).

    Streamlit 의 `declare_component()` 는 이렇게 끝난다::

        ctx = get_script_run_ctx()
        if ctx is not None:
            get_instance().component_registry.register_component(component)

    **스크립트 런 컨텍스트 안에서만 등록된다.** `serve.py` 는
    `start_background_servers()` 를 스크립트 런 밖에서 부르고, 그 호출이
    `websocket_handler → auth` 를 끌어온다. `auth` 가 모듈 레벨에서
    `extra_streamlit_components` 를 import 하면 그 시점에 `ctx` 가 None 이라
    등록이 **조용히** 건너뛰어지고, `sys.modules` 캐시 때문에 이후 세션의
    import 가 `declare_component()` 를 다시 실행하지 않는다 — 컴포넌트는 영구
    미등록이 되어 번들이 404 가 된다.

    실측된 결과 (2026-10-22 프로덕션): `user_session` 쿠키가 발급되지 않아
    오퍼레이터가 WS 인증에 실패하고 자막을 송출할 수 없었다. 화면에는 OpenAI
    전사(원문)만 흘렀고 뷰어·무대는 "잠시 후 시작됩니다" 에서 멈췄다 — 번역과
    무대가 각각 고장난 것처럼 보였지만 원인은 이 하나였다.

    e2e 는 `tests/e2e/test_serve_boot_path_e2e.py` 가 같은 불변식을 브라우저로
    확인한다. 여기 있는 것은 그 비싼 테스트가 스킵될 때도 남는 구조적 가드다.
    """

    @staticmethod
    def _module_level_imports(filename: str) -> set[str]:
        """모듈 **최상단**(함수/클래스 바깥)에서 import 하는 모듈 이름들."""
        tree = ast.parse((REPO / filename).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in tree.body:  # body 만 — 함수 안의 지연 import 는 제외한다
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_auth_defers_the_cookie_component_import(self):
        assert "extra_streamlit_components" not in self._module_level_imports(
            "auth.py"
        ), (
            "auth.py 가 extra_streamlit_components 를 모듈 레벨에서 import 한다. "
            "serve.py 부팅 경로가 스크립트 런 밖에서 이 모듈을 끌어오므로 "
            "declare_component() 등록이 유실되고 CookieManager 번들이 404 가 된다 "
            "— user_session 쿠키가 발급되지 않아 오퍼레이터가 자막을 송출할 수 "
            "없다 (#174). get_cookie_manager() 안에서 import 하라."
        )

    def test_the_component_import_still_exists_somewhere(self):
        """양성 대조군 — "지연 import" 를 "import 삭제" 로 만들지 않았다."""
        src = (REPO / "auth.py").read_text(encoding="utf-8")
        assert "from extra_streamlit_components import CookieManager" in src, (
            "CookieManager import 가 아예 사라졌다 — 지연시키는 것과 없애는 것은 "
            "다르다."
        )
