"""컨테이너 엔트리포인트 (#158) — SSE/WS 를 먼저 띄우고 Streamlit 을 돌린다.

`streamlit run app.py` 를 그대로 쓰면 두 서버가 **첫 브라우저 세션 전까지 뜨지
않는다**. Streamlit 은 스크립트를 세션 단위로 실행하므로, 아무도 `:8501` 을
열지 않으면 `app.py` 가 실행되지 않기 때문이다. 그래서 컨테이너를 재시작하면
오퍼레이터가 접속할 때까지 `/view/*`, `/stream/*`, `/stage/*`, `/ws` 가 전부
죽어 있었다 (#158).

이 스크립트는 순서를 뒤집는다:

    1) 백그라운드 서버(WS 8765 / SSE 8766)를 데몬 스레드로 띄우고
    2) **같은 프로세스에서** Streamlit 을 돌린다

**같은 프로세스여야 한다.** WS 핸들러와 SSE 브로드캐스터는
`websocket_handler._broadcast_manager` / `_room_manager` 같은 모듈 전역 상태를
공유한다. 서버를 별도 프로세스로 쪼개면 자막이 SSE 로 흘러가지 않는다. 그래서
`os.exec*` 로 streamlit 을 띄우지 않고(그러면 스레드가 죽는다),
`streamlit.web.bootstrap` 을 직접 호출한다.

설정은 **환경변수**로 준다 (`STREAMLIT_*`). `bootstrap` 의 `flag_options` 로
넘기는 방법도 있지만 그쪽은 click 옵션 이름에 결합돼 버전 변화에 약하다.
"""

from __future__ import annotations

import os
import sys


def _configure_streamlit_defaults() -> None:
    """Dockerfile CMD 가 주던 플래그를 환경변수 기본값으로 옮긴다.

    이미 설정돼 있으면 건드리지 않는다 — docker-compose 나 운영자가 덮어쓸 수
    있어야 한다.
    """
    os.environ.setdefault("STREAMLIT_SERVER_PORT", "8501")
    os.environ.setdefault("STREAMLIT_SERVER_ADDRESS", "0.0.0.0")
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ.setdefault("STREAMLIT_SERVER_ENABLE_CORS", "false")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")


def main(script: str = "app.py") -> None:
    _configure_streamlit_defaults()

    # 1) 백그라운드 서버 먼저 — 브라우저 접속과 무관하게 포트를 연다.
    from background_servers import start_background_servers

    state = start_background_servers()
    if state["errors"]:
        # 기동 실패를 조용히 넘기지 않는다. Streamlit 은 계속 띄운다 —
        # 오퍼레이터 UI 까지 죽이면 복구 수단이 사라진다.
        print(f"[Boot] 백그라운드 서버 일부 기동 실패: {state['errors']}")

    # 2) 같은 프로세스에서 Streamlit 실행.
    from streamlit import config as _config
    from streamlit.web import bootstrap

    script_path = os.path.abspath(script)
    # CLI 의 `_main_run` 과 같은 순서다 — config/secret 파일 해석에 쓰이므로
    # `load_config_options` 보다 먼저 세팅해야 한다.
    _config._main_script_path = script_path
    bootstrap.load_config_options(flag_options={})
    bootstrap.run(script_path, False, [], {})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "app.py")
