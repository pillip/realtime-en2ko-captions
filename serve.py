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

설정 인터페이스는 **환경변수**(`STREAMLIT_*`)지만, 그 값을 반드시
`bootstrap.load_config_options(flag_options=...)` 로 **넘겨야** 적용된다 —
환경변수만 세팅하는 것으로는 먹지 않는다 (`_streamlit_flag_options` 참고).

주의 — 이 경로의 import 는 **스크립트 런 컨텍스트 밖**이다 (#174)
-----------------------------------------------------------------
1) 이 파일이 `start_background_servers()` 를 부르는 시점에는 Streamlit 의
   `ScriptRunContext` 가 없다. Streamlit 의 `declare_component()` 는
   `get_script_run_ctx()` 가 None 이면 **등록을 조용히 건너뛴다**. 그리고
   `sys.modules` 캐시 때문에 나중에 세션이 `app.py` 를 돌며 같은 모듈을
   import 해도 `declare_component()` 는 다시 실행되지 않는다. 결과적으로 커스텀
   컴포넌트가 **영구 미등록**이 되어 번들이 404 가 된다. 그래서 커스텀 컴포넌트를
   쓰는 모듈(`auth.py` 의 `CookieManager`)은 모듈 레벨에서 import 하면 안 된다 —
   자세한 사정은 `auth.get_cookie_manager()` 주석에 있다.
2) 같은 이유로 DB 초기화도 여기서 차갑게 시작된다. 그 경로의 마이그레이션 경합은
   `database._SINGLETON_LOCK` 이 막는다 (#172).
"""

from __future__ import annotations

import os
import sys


def _streamlit_flag_options() -> dict[str, object]:
    """Dockerfile CMD 가 주던 플래그를 `bootstrap` 에 넘길 형태로 만든다.

    **환경변수만 세팅하는 것으로는 먹지 않는다 (#174).** 이전 구현은
    `os.environ.setdefault("STREAMLIT_SERVER_PORT", ...)` 만 했는데, 그 값은
    `bootstrap.load_config_options(flag_options={})` 경로에서 반영되지 않았다 —
    실측으로 `STREAMLIT_SERVER_PORT=8597` 을 줘도 8501 에 바인딩했다. 프로덕션이
    멀쩡해 보인 건 8501 이 마침 기본값이라 가려졌을 뿐이고, 포트를 바꾸려는
    순간 조용히 무시됐을 것이다.

    `load_config_options` 는 키의 `_` 를 `.` 로 바꿔 설정 이름으로 쓴다
    (`server_port` → `server.port`). docstring 이 "CLI flag version" 이라고
    부르지만 click 옵션 이름이 아니라 **설정 옵션 이름**이므로 버전 변화에
    약하지 않다 — 이전 주석의 우려는 사실과 달랐다.

    **CORS / gatherUsageStats 는 일부러 넘기지 않는다.** `.streamlit/config.toml`
    이 `enableCORS = true` 를 명시하는데 옛 env 기본값은 `false` 를 주장했다.
    env 가 inert 였으므로 실제로는 config.toml 의 `true` 가 적용돼 왔다. 여기서
    env 쪽을 "되살리면" 운영 동작이 조용히 뒤집힌다. 그 판단은 이 수정의 범위가
    아니다.
    """
    return {
        "server_port": int(os.getenv("STREAMLIT_SERVER_PORT", "8501")),
        "server_address": os.getenv("STREAMLIT_SERVER_ADDRESS", "0.0.0.0"),
        "server_headless": True,
    }


def main(script: str = "app.py") -> None:
    # 1) 백그라운드 서버 먼저 — 브라우저 접속과 무관하게 포트를 연다.
    from background_servers import start_background_servers, wait_until_ready

    state = start_background_servers()

    # 스레드가 떴다는 것과 포트가 열렸다는 것은 다르다. 2026-10-22 배포에서
    # SSE 스레드가 마이그레이션 경합으로 죽었는데, 컨테이너는 "정상" 으로 보이고
    # 포트 미개방은 ALB 502 로만 드러났다 — 로그에 판정용 한 줄이 없었다.
    # 여기서 실제 바인딩을 확인해 한 줄로 남긴다.
    ready = wait_until_ready()
    down = sorted(name for name, ok in ready.items() if not ok)
    errors = state["errors"]

    if down or errors:
        # 기동 실패를 조용히 넘기지 않는다. Streamlit 은 계속 띄운다 —
        # 오퍼레이터 UI 까지 죽이면 복구 수단이 사라진다.
        print(f"[Boot] ⚠ 백그라운드 서버 기동 실패 — 포트 미개방: {down or '없음'}")
        if errors:
            print(f"[Boot] ⚠ 기동 중 발생한 오류: {errors}")
        print(
            "[Boot] ⚠ 뷰어(/view, /stage, /stream, /branding)와 번역 WebSocket 이 "
            "동작하지 않는다. 위 오류를 해결하고 컨테이너를 재시작하라."
        )
    else:
        print("[Boot] 백그라운드 서버 준비 완료 — 포트 전부 응답 중")

    # 2) 같은 프로세스에서 Streamlit 실행.
    from streamlit import config as _config
    from streamlit.web import bootstrap

    script_path = os.path.abspath(script)
    # CLI 의 `_main_run` 과 같은 순서다 — config/secret 파일 해석에 쓰이므로
    # `load_config_options` 보다 먼저 세팅해야 한다.
    _config._main_script_path = script_path
    flag_options = _streamlit_flag_options()
    bootstrap.load_config_options(flag_options=flag_options)
    bootstrap.run(script_path, False, [], flag_options)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "app.py")
