"""보안 가드 뮤테이션 러너(`guard_mutations.py`) 자체 단위 테스트 (ISSUE-43).

러너는 "가드를 지우면 반드시 이름 있는 테스트가 깨진다"를 증명하는 장치다.
그 장치가 스스로 RL-004(무의미하게 통과하는 단언)를 반복하면 아무것도 증명하지
못하므로, 여기서는 **러너가 실패를 실패라고 말하는지**를 먼저 검증한다.

검증 대상:
- 아무 테스트도 깨지 않는 무해한 뮤테이션 → `survived` + 비정상 종료
- 가드를 실제로 무력화하는 뮤테이션 → `killed by <노드 ID>` + 종료 코드 0
- `find` 스니펫이 1회가 아닌 항목 → 뮤테이션 미적용 + 비정상 종료
- 뮤테이션 직후 예외 → 대상 파일 바이트 동일 + 샌드박스 제거
- 허용 목록 밖 파일 → 거부, 그 파일 미수정
- `Guard:` 로 아무도 지목하지 않은 가드 → `unnamed` + 비정상 종료
- pytest 종료 코드 1 이 아닌 값(수집 오류/미수집/타임아웃) → kill 이 아니라 ERROR
- **문법이 깨지거나 import 되지 않는 뮤턴트 → kill 이 아니라 ERROR**
- SIGTERM → 샌드박스 제거 후 종료 (atexit 는 SIGTERM 에 돌지 않는다)
- 실제 카탈로그의 정적 대응 관계 (고아 가드 0, 고아 `Guard:` 표기 0)

Note: 대부분의 테스트는 `tmp_path` 안에 만든 **가짜 저장소**를 대상으로 돈다
(실제 스위트를 재실행하지 않으므로 빠르다). 전체 카탈로그 종단 실행은
`GUARD_MUTATION_FULL=1` 일 때만 도는 별도 테스트이며 CI 의 `mutation` job 이
그 역할을 맡는다. 외부 네트워크 호출 없음.

AC 대응표 (ISSUE-43)
--------------------
가드 ↔ 킬 테스트 대응을 강제하는 이슈이므로, 이 파일 자신도 AC ↔ 테스트 대응을
명시한다. 대응이 암묵적이면 그것이 곧 RL-004 다.

- 러너 계약 / 전체 카탈로그가 종료 코드 0 · `survived` 0
  → `test_full_catalog_run_kills_every_guard` (`GUARD_MUTATION_FULL=1`),
    CI `mutation` job
- 아무 테스트도 깨지 못하는 무해한 뮤테이션 → `survived` + 비정상 종료
  → `test_harmless_mutation_is_reported_as_survived_and_run_fails`
- `find` 스니펫이 정확히 1회가 아닌 항목 → 비정상 종료 + 대상 파일 불변
  → `test_find_snippet_appearing_twice_aborts_and_leaves_file_untouched`
- **뮤테이션 적용 직후 예외를 발생시키도록 패치한 러너 → 프로세스가 끝난 뒤
  대상 파일이 원본과 바이트 단위로 동일**
  → `test_crash_after_mutation_leaves_source_intact_and_removes_sandbox`
    (샌드박스 사본만 변형하므로 작업 트리는 애초에 쓰이지 않는다)
- 허용 목록 밖 파일(예: `translation.py`)을 가리키는 항목 → 거부 + 그 파일 불변
  → `test_entry_outside_the_allowlist_is_rejected_and_file_unmodified`
- 카탈로그 가드 ↔ `Guard:` 표기 대응 (고아 0, 양방향)
  → `test_every_guard_is_owned_and_no_annotation_is_orphaned`
- **`replace` 오타로 import 조차 되지 않는 뮤턴트가 kill 로 계상되지 않을 것**
  → `test_syntactically_invalid_replace_is_an_error_not_a_kill`,
    `test_mutant_that_cannot_be_imported_is_an_error_not_a_kill`
- `#quiet_reject` 킬 테스트 (널바이트 파일명 → `None` + stdout 비어 있음)
  → `tests/test_branding_assets.py::TestTraversalContainment::
     test_nul_byte_filename_is_rejected_without_writing_any_log`
- **접근 방식 결정 AC (오프더셸프 `mutmut` / `cosmic-ray` 대 선언형 자체
  스크립트의 실측 비교)는 테스트가 아니라 문서 산출물이다** — 실측 실행 시간과
  탈락 사유는 `docs/architecture.md` 의 Tradeoffs 표에 기록한다. 여기에
  대응 테스트가 없는 것은 누락이 아니라 성질상 검증 대상이 코드가 아니기 때문이다.
"""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import guard_mutations

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CATALOG = REPO_ROOT / "guard_mutations.toml"

FAKE_GUARD = "branding_assets.is_safe#reject"


@pytest.fixture(autouse=True)
def _restore_sigterm_handler():
    """`main()` 이 프로세스 전역 SIGTERM 핸들러를 설치하므로 되돌린다."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


# 가짜 저장소의 "프로덕션" 모듈. 허용 목록은 파일명 기준이라 이름을
# `branding_assets.py` 로 두어야 러너가 실제 경로와 같은 규칙으로 다룬다.
FAKE_MODULE = '''\
"""Fake allowlisted module used by the guard-mutation runner tests."""


def is_safe(value: str) -> bool:
    # harmless comment
    if value == "bad":
        return False
    return True
'''

# `find` 가 2회 등장하는 변형 (정확히 1회 규칙을 깨는 입력).
FAKE_MODULE_DUPLICATED = '''\
"""Fake module whose guard snippet appears twice."""


def is_safe(value: str) -> bool:
    if value == "bad":
        return False
    return True


def is_safe_again(value: str) -> bool:
    if value == "bad":
        return False
    return True
'''

FAKE_TEST = '''\
"""Fake test module for the guard-mutation runner."""

import branding_assets


def test_rejects_bad_value():
    """{marker} {guard}

    가짜 저장소용 킬 테스트.
    """
    assert branding_assets.is_safe("bad") is False
'''

# 실제 저장소의 킬 테스트와 같은 형태 — import 가 **함수 안**에 있다. 이 형태에서는
# 모듈이 깨져도 수집 오류(exit 2)가 아니라 테스트 실패(exit 1)로 나타나므로,
# 종료 코드만 보면 "가드가 죽었다" 와 구분되지 않는다.
FAKE_TEST_LOCAL_IMPORT = '''\
"""Fake test module importing the target inside the test function."""


def test_rejects_bad_value():
    """{marker} {guard}

    가짜 저장소용 킬 테스트 (함수 지역 import).
    """
    import branding_assets

    assert branding_assets.is_safe("bad") is False
'''

FAKE_TEST_WITHOUT_ANNOTATION = '''\
"""Fake test module with no guard annotation."""

import branding_assets


def test_rejects_bad_value():
    """어떤 가드도 지목하지 않는 테스트."""
    assert branding_assets.is_safe("bad") is False
'''

# 가짜 저장소의 rootdir 을 고정한다 — 이게 없으면 pytest 가 tmp 경로 위쪽의
# 설정 파일을 주워 실제 저장소의 addopts 를 상속할 수 있다.
FAKE_PYPROJECT = """\
[tool.pytest.ini_options]
addopts = ""
testpaths = ["tests"]
"""


def _catalog_toml(entries: list[dict[str, str]]) -> str:
    """카탈로그 TOML 문자열을 만든다 (스니펫은 escape 없는 literal string)."""
    chunks = []
    for entry in entries:
        chunks.append(
            "[[mutation]]\n"
            f"guard = '{entry['guard']}'\n"
            f"file = '{entry['file']}'\n"
            f"find = '''\n{entry['find']}'''\n"
            f"replace = '''\n{entry['replace']}'''\n"
        )
    return "\n".join(chunks)


def _build_fake_repo(
    tmp_path: Path,
    entries: list[dict[str, str]],
    *,
    module_src: str = FAKE_MODULE,
    annotate: bool = True,
    test_template: str | None = None,
) -> Path:
    """`tmp_path` 안에 최소 저장소(모듈 + 테스트 + 카탈로그)를 만든다."""
    repo = tmp_path / "fake-repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(FAKE_PYPROJECT, encoding="utf-8")
    (repo / "branding_assets.py").write_text(module_src, encoding="utf-8")
    # 허용 목록 밖 파일 — 어떤 경로로도 수정되면 안 된다.
    (repo / "translation.py").write_text('SECRET = "untouched"\n', encoding="utf-8")
    if not annotate:
        test_src = FAKE_TEST_WITHOUT_ANNOTATION
    else:
        # "Guard:" 표기를 이 파일 안에 literal 로 두면 실제 카탈로그의 고아
        # 표기 검사에 걸리므로 런타임에 조립한다.
        template = test_template if test_template is not None else FAKE_TEST
        test_src = template.format(marker="Guard:", guard=FAKE_GUARD)
    (repo / "tests" / "test_fake_guard.py").write_text(test_src, encoding="utf-8")
    (repo / "guard_mutations.toml").write_text(_catalog_toml(entries), encoding="utf-8")
    return repo


def _run(repo: Path, stream: io.StringIO | None = None):
    stream = stream if stream is not None else io.StringIO()
    catalog = guard_mutations.load_catalog(repo / "guard_mutations.toml")
    report = guard_mutations.run_catalog(
        repo_root=repo,
        catalog=catalog,
        tests_root=repo / "tests",
        stream=stream,
    )
    return report, stream.getvalue()


def _harmless_entry() -> dict[str, str]:
    """어떤 테스트도 깨지 않는 뮤테이션 (주석 문자열 치환)."""
    return {
        "guard": FAKE_GUARD,
        "file": "branding_assets.py",
        "find": "    # harmless comment",
        "replace": "    # mutated comment",
    }


def _lethal_entry() -> dict[str, str]:
    """가드를 실제로 무력화하는 뮤테이션."""
    return {
        "guard": FAKE_GUARD,
        "file": "branding_assets.py",
        "find": '    if value == "bad":',
        "replace": '    if False and value == "bad":',
    }


# ---------------------------------------------------------------------------
# 러너 계약 — 살아남은 뮤턴트는 실패다 (러너가 RL-004 를 반복하지 않음을 증명)
# ---------------------------------------------------------------------------
class TestRunnerVerdicts:
    def test_harmless_mutation_is_reported_as_survived_and_run_fails(self, tmp_path):
        """무해한 뮤테이션은 `survived` 로 보고되고 종료 코드가 0 이 아니다."""
        repo = _build_fake_repo(tmp_path, [_harmless_entry()])

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.SURVIVED]
        assert "SURVIVED" in output
        # 원본 파일·행 위치가 출력된다 (AC).
        assert "branding_assets.py:5" in output
        assert "tests/test_fake_guard.py::test_rejects_bad_value" in output

    def test_guard_removal_is_reported_as_killed_by_the_owning_node(self, tmp_path):
        """가드를 무력화하면 `killed by <노드 ID>` 가 출력되고 성공 종료한다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])

        report, output = _run(repo)

        assert report.ok is True
        assert [r.status for r in report.results] == [guard_mutations.KILLED]
        assert (
            f"{FAKE_GUARD} killed by tests/test_fake_guard.py::test_rejects_bad_value"
            in output
        )
        assert "1/1 guards, 1 killed, 0 survived, 0 errors" in output

    def test_unexpected_pytest_exit_code_is_an_error_not_a_kill(
        self, tmp_path, monkeypatch
    ):
        """수집 오류/미수집(종료 코드 1 이 아닌 값)을 kill 로 세면 안 된다.

        `--cov-fail-under` 같은 부분 실행 오탐이 "뮤턴트를 죽인 것"처럼 보이는
        것이 RL-004 의 재발 경로다. 1 이 아닌 종료 코드는 전부 ERROR 다.
        """
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])
        calls: list[list[str]] = []

        def _fake(sandbox, nodes, timeout):
            calls.append(list(nodes))
            return (0, "") if len(calls) == 1 else (5, "no tests ran")

        monkeypatch.setattr(guard_mutations, "_run_pytest", _fake)

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.ERROR]
        assert "killed" not in output.lower().split("errors")[0].replace("0 killed", "")
        assert "exit=5" in output

    def test_baseline_failure_is_reported_before_any_mutation_is_credited(
        self, tmp_path, monkeypatch
    ):
        """뮤테이션 전에 이미 실패하는 테스트는 kill 의 증거가 될 수 없다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])
        monkeypatch.setattr(
            guard_mutations, "_run_pytest", lambda *a, **k: (1, "FAILED baseline")
        )

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.ERROR]
        assert "baseline-failed" in output


# ---------------------------------------------------------------------------
# 깨진 뮤턴트 — "import 조차 되지 않는 코드" 를 kill 로 세면 아무것도 증명하지 못한다
# ---------------------------------------------------------------------------
class TestBrokenMutantIsNotAKill:
    """`replace` 오타로 모듈이 깨진 경우를 kill 과 구분한다.

    이 저장소의 킬 테스트는 대상 모듈을 **함수 안에서** import 한다. 그래서
    모듈이 깨지면 수집 오류(exit 2)가 아니라 테스트 실패(exit 1) 로 나타나고,
    종료 코드만 보면 "가드가 죽었다" 와 똑같이 보인다 — 가드를 지운 적이 없는데
    게이트가 초록색이 되는 RL-004 의 재발이다.
    """

    def test_syntactically_invalid_replace_is_an_error_not_a_kill(
        self, tmp_path, capsys
    ):
        """괄호가 안 맞는 `replace` 는 kill 이 아니라 ERROR 이고 비정상 종료다."""
        entry = _lethal_entry() | {"replace": '    if False and (value == "bad":'}
        repo = _build_fake_repo(tmp_path, [entry], test_template=FAKE_TEST_LOCAL_IMPORT)

        code = guard_mutations.main(
            [
                "--catalog",
                str(repo / "guard_mutations.toml"),
                "--repo-root",
                str(repo),
                "--tests-root",
                str(repo / "tests"),
            ]
        )
        output = capsys.readouterr().out

        assert code != 0
        assert "killed by" not in output
        assert "문법적으로 유효하지 않습니다" in output
        assert "branding_assets.py" in output
        assert "1/1 guards, 0 killed, 0 survived, 1 errors" in output

    def test_mutant_that_cannot_be_imported_is_an_error_not_a_kill(self, tmp_path):
        """문법은 맞지만 import 가 깨지는 뮤턴트도 kill 이 아니다.

        가드를 무력화한 뮤턴트는 **단언**을 실패시켜야 한다. import 가 실패하면
        그 테스트는 가드와 무관하게 빨간불이 되므로 증거 능력이 없다.
        """
        entry = _lethal_entry() | {
            "find": "def is_safe(value: str) -> bool:",
            "replace": (
                "import no_such_module_xyz\n\n\ndef is_safe(value: str) -> bool:"
            ),
        }
        repo = _build_fake_repo(tmp_path, [entry], test_template=FAKE_TEST_LOCAL_IMPORT)

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.ERROR]
        assert "killed by" not in output
        assert "no_such_module_xyz" in output


# ---------------------------------------------------------------------------
# 카탈로그 검증 — 잘못된 항목은 아무것도 건드리지 않고 즉시 실패한다
# ---------------------------------------------------------------------------
class TestCatalogValidation:
    def test_find_snippet_appearing_twice_aborts_and_leaves_file_untouched(
        self, tmp_path
    ):
        """`find` 가 2회 등장하면 적용하지 않고 "정확히 1회" 오류로 실패한다."""
        repo = _build_fake_repo(
            tmp_path, [_lethal_entry()], module_src=FAKE_MODULE_DUPLICATED
        )
        before = (repo / "branding_assets.py").read_bytes()

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.ERROR]
        assert "2회" in output
        assert "정확히 1회" in output
        assert (repo / "branding_assets.py").read_bytes() == before

    def test_entry_outside_the_allowlist_is_rejected_and_file_unmodified(
        self, tmp_path
    ):
        """허용 목록 밖 파일(translation.py)은 뮤테이션 대상이 될 수 없다."""
        entry = {
            "guard": FAKE_GUARD,
            "file": "translation.py",
            "find": '    SECRET = "untouched"',
            "replace": '    SECRET = "mutated"',
        }
        repo = _build_fake_repo(tmp_path, [entry])
        before = (repo / "translation.py").read_bytes()

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.ERROR]
        assert "허용 목록" in output
        assert "translation.py" in output
        assert (repo / "translation.py").read_bytes() == before

    def test_fail_fast_summary_prints_the_catalog_denominator(self, tmp_path):
        """중간에 멈춰도 "몇 건 중 몇 건" 인지 보여야 오독되지 않는다.

        분모가 없으면 `1 guards` 가 "카탈로그에 항목이 하나뿐" 으로 읽힌다.
        """
        outside = {
            "guard": "branding_assets.is_safe#outside",
            "file": "translation.py",
            "find": '    SECRET = "untouched"',
            "replace": '    SECRET = "mutated"',
        }
        repo = _build_fake_repo(tmp_path, [_lethal_entry(), outside])

        report, output = _run(repo)

        assert report.ok is False
        assert len(report.results) == 1
        assert "1/2 guards, 0 killed, 0 survived, 1 errors" in output

    def test_guard_without_any_owning_test_is_reported_unnamed(self, tmp_path):
        """`Guard:` 로 아무도 지목하지 않은 가드는 `unnamed` 로 보고된다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry()], annotate=False)

        report, output = _run(repo)

        assert report.ok is False
        assert [r.status for r in report.results] == [guard_mutations.UNNAMED]
        assert (
            f'{FAKE_GUARD} unnamed — no test declares "Guard: {FAKE_GUARD}"' in output
        )

    def test_annotation_that_is_not_in_the_catalog_is_an_error(self, tmp_path):
        """카탈로그에 없는 `Guard:` 표기는 고아다 — 조용히 넘어가지 않는다."""
        entry = _lethal_entry() | {"guard": "branding_assets.is_safe#other"}
        repo = _build_fake_repo(tmp_path, [entry])

        report, output = _run(repo)

        assert report.ok is False
        assert FAKE_GUARD in output
        assert "카탈로그에 없는" in output

    def test_duplicate_guard_id_is_rejected_when_loading(self, tmp_path):
        """같은 가드 ID 가 두 번 선언되면 카탈로그 로드 자체가 실패한다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry(), _harmless_entry()])

        with pytest.raises(guard_mutations.CatalogError) as exc:
            guard_mutations.load_catalog(repo / "guard_mutations.toml")

        assert FAKE_GUARD in str(exc.value)


# ---------------------------------------------------------------------------
# 격리 — 작업 트리는 어떤 종료 경로에서도 변경되지 않는다
# ---------------------------------------------------------------------------
class TestWorkingTreeIsolation:
    def test_crash_after_mutation_leaves_source_intact_and_removes_sandbox(
        self, tmp_path, monkeypatch
    ):
        """뮤테이션 적용 직후 예외가 나도 원본은 바이트 단위로 동일하다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])
        before = (repo / "branding_assets.py").read_bytes()
        sandboxes: list[Path] = []

        def _boom(sandbox, nodes, timeout):
            sandboxes.append(Path(sandbox))
            if len(sandboxes) == 1:
                return 0, ""  # baseline 통과
            raise RuntimeError("simulated crash right after mutating")

        monkeypatch.setattr(guard_mutations, "_run_pytest", _boom)

        with pytest.raises(RuntimeError):
            _run(repo)

        assert (repo / "branding_assets.py").read_bytes() == before
        # 샌드박스는 예외 경로에서도 제거된다.
        assert sandboxes and not sandboxes[0].exists()
        assert guard_mutations._SANDBOXES == []

    def test_sigterm_removes_the_sandbox_before_exiting(self, tmp_path):
        """SIGTERM 에서도 샌드박스가 남지 않는다.

        `atexit` 는 SIGTERM 에 돌지 않는다 — 핸들러가 없으면 무력화된 가드가
        들어 있는 사본이 `$TMPDIR` 에 그대로 남는다.
        """
        # 카탈로그 로드 실패로 즉시 반환하지만, 핸들러는 그 전에 설치된다.
        assert guard_mutations.main(["--catalog", str(tmp_path / "missing.toml")]) == 2

        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)

        holder = Path(tempfile.mkdtemp(prefix="guard-mutations-sigterm-"))
        guard_mutations._SANDBOXES.append(holder)
        try:
            with pytest.raises(SystemExit) as exc:
                handler(signal.SIGTERM, None)
        finally:
            shutil.rmtree(holder, ignore_errors=True)
            guard_mutations._SANDBOXES.clear()

        assert exc.value.code == 143
        assert not holder.exists()

    def test_normal_run_never_writes_into_the_repo_root(self, tmp_path):
        """정상 실행에서도 원본 트리에는 어떤 파일도 쓰이지 않는다."""
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])
        before = {
            p.relative_to(repo): p.read_bytes()
            for p in sorted(repo.rglob("*"))
            if p.is_file()
        }

        _run(repo)

        after = {
            p.relative_to(repo): p.read_bytes()
            for p in sorted(repo.rglob("*"))
            if p.is_file()
        }
        assert after == before


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCommandLine:
    def test_main_returns_non_zero_for_a_surviving_mutant(self, tmp_path, capsys):
        repo = _build_fake_repo(tmp_path, [_harmless_entry()])

        code = guard_mutations.main(
            [
                "--catalog",
                str(repo / "guard_mutations.toml"),
                "--repo-root",
                str(repo),
                "--tests-root",
                str(repo / "tests"),
            ]
        )

        assert code != 0
        assert "SURVIVED" in capsys.readouterr().out

    def test_main_reports_unknown_guard_id(self, tmp_path, capsys):
        repo = _build_fake_repo(tmp_path, [_lethal_entry()])

        code = guard_mutations.main(
            [
                "--catalog",
                str(repo / "guard_mutations.toml"),
                "--repo-root",
                str(repo),
                "--tests-root",
                str(repo / "tests"),
                "--guard",
                "does.not#exist",
            ]
        )

        assert code != 0
        assert "does.not#exist" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 실제 카탈로그 — 정적 대응 관계 (subprocess 없이 ast 만, 빠르다)
# ---------------------------------------------------------------------------
class TestRealCatalogBindings:
    def test_every_guard_is_owned_and_no_annotation_is_orphaned(self):
        """가드 ↔ `Guard:` 표기가 양방향으로 정확히 대응한다.

        한쪽이라도 어긋나면 러너가 잡아 주지만, 그 검사가 CI 의 느린 job
        에서만 돌면 로컬에서 즉시 알 수 없다. 여기서 빠르게 고정한다.
        """
        catalog = guard_mutations.load_catalog(REAL_CATALOG)
        annotations = guard_mutations.collect_guard_annotations(
            REPO_ROOT / "tests", REPO_ROOT
        )
        guards = {m.guard for m in catalog}

        assert sorted(g for g in guards if g not in annotations) == []
        assert sorted(a for a in annotations if a not in guards) == []

    def test_catalog_targets_only_allowlisted_files_with_unique_snippets(self):
        """모든 항목이 허용 목록 안 파일을 겨냥하고 `find` 는 정확히 1회 등장한다."""
        catalog = guard_mutations.load_catalog(REAL_CATALOG)

        assert len(catalog) >= 20  # 이슈가 정의한 초기 카탈로그 규모
        for mutation in catalog:
            assert mutation.file in guard_mutations.ALLOWED_FILES
            source = (REPO_ROOT / mutation.file).read_text(encoding="utf-8")
            assert source.count(mutation.find) == 1, mutation.guard
            assert mutation.replace != mutation.find, mutation.guard


# ---------------------------------------------------------------------------
# 종단 실행 — CI 의 `mutation` job 이 담당한다 (로컬 기본 스위트에서는 제외)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not os.getenv("GUARD_MUTATION_FULL"),
    reason=(
        "실측 ~9s 로 느려서가 아니라, pytest 서브프로세스를 ~40개 새로 띄우는 "
        "중첩 실행이라 기본 스위트에서 제외한다 (CI 의 mutation job 이 정식 게이트). "
        "GUARD_MUTATION_FULL=1 로 활성화"
    ),
)
def test_full_catalog_run_kills_every_guard():
    """카탈로그 전체를 서브프로세스로 돌리면 종료 코드 0 이고 생존 0 건이다."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "guard_mutations.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert "SURVIVED" not in proc.stdout
    assert proc.returncode == 0, proc.stdout + proc.stderr
