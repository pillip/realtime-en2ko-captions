#!/usr/bin/env python3
"""보안 가드 뮤테이션 점검 러너 (ISSUE-43, PRD-Ref: NFR-028 / RL-004).

선언된 보안 가드를 하나씩 무력화한 뒤 **그 가드를 지목한 테스트만** 돌려,
가드가 사라지면 반드시 이름을 특정할 수 있는 테스트가 실패한다는 사실을 한
명령으로 재현한다.

    uv run python guard_mutations.py

RL-004 는 "라인 커버리지는 100% 인데 가드를 지워도 스위트가 초록색" 이라는
형태로 다섯 번 재발했다. 커버리지는 실행 여부만 보고 **변별력**은 보지 않는다.
이 러너는 그 변별력을 기계가 확인 가능한 성질로 바꾼다.

배치 — 왜 ``scripts/`` 가 아닌 저장소 루트인가
----------------------------------------------
이슈 본문은 ``scripts/guard_mutations.py`` 를 제안했지만, 이 저장소의
``scripts`` 는 ``.claude-kit/scripts`` (git submodule) 를 가리키는 심볼릭
링크라 저장소에 추적되지 않는다 — 워크트리와 CI 체크아웃에는 존재하지 않는
경로다. 그래서 ``branding_assets.py`` / ``admin_logic.py`` 와 같은 평면 모듈
배치를 따라 루트에 둔다 (러너 + ``guard_mutations.toml`` 카탈로그).

킬 테스트 대응 — 왜 카탈로그에 ``tests`` 키가 없는가
----------------------------------------------------
이슈는 카탈로그 항목마다 pytest 노드 ID 목록(``tests``)을 두자고 제안했다.
그러면 같은 정보가 TOML 과 테스트 양쪽에 존재하게 되고(RL-001), 테스트 이름이
바뀌거나 삭제될 때 TOML 쪽이 조용히 썩는다 — 이 이슈가 막으려는 실패 모드
그 자체다. 그래서 **단일 출처는 테스트 docstring** 이다.

    def test_...():
        \"\"\"Guard: branding_assets.resolve_asset_path#symlink\"\"\"

러너는 ``tests/**/*.py`` 를 ``ast`` 로 읽어(수집 부작용이 있는
``pytest --collect-only`` 를 쓰지 않는다) 이 표기를 모아 노드 ID 를 만든다.
docstring 한 줄을 지우면 그 가드는 ``unnamed`` 이 되어 CI 가 깨진다 — 표기가
장식이 아니라 하중을 받는 구조다. 카탈로그에 없는 표기 역시 오류다(양방향).

격리 — 작업 트리는 어떤 경로로도 수정되지 않는다
------------------------------------------------
뮤테이션은 시작할 때 한 번 만든 **임시 샌드박스 사본**에만 적용한다. 원본
바이트는 메모리에 보관해 항목 사이에 샌드박스 안에서 되돌리고, 샌드박스는
``try/finally`` + ``atexit`` + ``SIGTERM`` 핸들러로 정상/예외/
``KeyboardInterrupt``/``SystemExit``/``kill`` 종료 경로에서 제거한다(제거 실패 시
경로를 출력한다). ``atexit`` 는 ``SIGTERM`` 에 돌지 않으므로 핸들러가 따로 필요하다
— 없으면 **무력화된 보안 가드가 들어 있는 사본**이 ``$TMPDIR`` 에 남는다.
``SIGKILL`` 은 프로세스가 개입할 수 없어 예외다. 원본 트리에는 애초에 쓰지
않으므로 실행이 중간에 죽어도 ``git diff --quiet`` 는 **구조적으로** 0 이다
— 제자리 수정 후 복구보다 강한 성질이며 같은 AC 를 만족한다.

샌드박스 사본은 ``.env`` 와 ``data/`` 를 제외한다. 러너가 비밀값이나 운영 DB 를
``$TMPDIR`` 로 복제할 이유가 없고, 실행이 비정상 종료해 사본이 남는 경우 그
복제본이 그대로 디스크에 남기 때문이다.

유한성 — 열린 루프가 없다
-------------------------
카탈로그에 적힌 항목 수만큼만, 한 번씩 돈다. 뮤턴트 자동 생성도 재시도도 없다.
pytest 서브프로세스마다 하드 타임아웃이 걸려 있고, 타임아웃은 kill 이 아니라
ERROR 다.

부분 실행 오탐 차단
-------------------
``pyproject.toml`` 의 ``addopts`` 에 ``--cov-fail-under=50`` 이 있어, 노드 몇 개만
돌리면 커버리지 미달로 실패한다 — 그러면 **뮤턴트를 죽인 것처럼 보인다**. 그
오탐이야말로 RL-004 의 재발이므로 ``--no-cov`` 는 필수이고, 그 위에 두 겹을
더 둔다.

1. **baseline**: 뮤테이션 전에 같은 노드를 그대로 돌려 종료 코드 0 을 확인한다.
2. **깨진 뮤턴트 차단**: 종료 코드만으로는 부족하다. 이 저장소의 킬 테스트는
   대상 모듈을 **함수 안에서** import 하므로(``def test_...(): import
   branding_assets``), ``replace`` 오타로 모듈이 깨져도 수집 오류(``2``)가
   아니라 **테스트 실패(``1``)** 로 나타난다 — 가드를 지운 적이 없는데 kill 로
   계상되는 RL-004 의 재발이다. 그래서 두 겹으로 막는다: 쓰기 전에
   ``compile()`` 로 문법을 검증하고, 실행 뒤에는 pytest 출력에
   ``E   SyntaxError|IndentationError|ImportError|ModuleNotFoundError`` 가
   있으면 kill 을 ERROR 로 강등한다. 가드 뮤테이션은 **단언**을 실패시켜야지
   import 를 깨뜨려서는 안 된다.
3. **종료 코드 해석**: ``1`` 만 kill 후보다. ``0`` 은 survived, 그 밖의 값
   (``2`` 수집 오류 / ``4`` 사용법 오류 / ``5`` 미수집 / 타임아웃)은 ERROR 다.

``-rf`` 를 붙이는 것은 "killed by <노드 ID>" 출력 계약을 위해서다 (어떤 노드가
실제로 실패했는지 이름을 대야 한다).

종료 코드: 카탈로그의 모든 항목이 killed 이고 오류가 0 건일 때만 0.
표준 라이브러리만 사용한다 (``tomllib`` 은 3.11 stdlib).
"""

from __future__ import annotations

import argparse
import ast
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ALLOWED_FILES",
    "CatalogError",
    "GuardResult",
    "Mutation",
    "Report",
    "collect_guard_annotations",
    "load_catalog",
    "main",
    "run_catalog",
]

# 뮤테이션이 허용되는 파일. **러너에 하드코딩**한다 — 카탈로그(데이터)가
# 자신의 허용 범위를 스스로 넓힐 수 있으면 허용 목록이 아니다. 범위 확대는
# 이 상수를 고치는 코드 리뷰를 거쳐야 한다.
ALLOWED_FILES: frozenset[str] = frozenset(
    {
        "branding_assets.py",  # 유일한 사용자 업로드 경로
        "branding_routes.py",  # 미인증 공개 라우트
        "auth.py",  # 역할 판정 (RL-002)
        "admin_logic.py",  # 역할별 룸 가시성 (RL-002)
    }
)

# 샌드박스 사본에서 제외할 이름. 실측 ~110 파일 / 3.5 MB, 복사는 ~50 ms 다.
# `.env` / `data` / `*.db` 는 크기가 아니라 **내용** 때문에 제외한다 — API 키와
# 운영 SQLite 를 $TMPDIR 로 복제할 이유가 없고, 사본이 남는 순간 그대로 노출된다.
SANDBOX_IGNORE: tuple[str, ...] = (
    ".git",
    ".venv",
    ".worktrees",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".claude",
    ".claude-kit",
    ".serena",
    "scripts",
    "node_modules",
    "htmlcov",
    "figma-export",
    "logs",
    ".env",
    ".env.*",
    "data",
    "*.db",
)

# pytest 서브프로세스 1회당 하드 상한. 초과는 kill 이 아니라 ERROR 다.
PYTEST_TIMEOUT_SECONDS = 180

DEFAULT_CATALOG_NAME = "guard_mutations.toml"

# 판정 상태.
KILLED = "killed"
SURVIVED = "survived"
ERROR = "error"
UNNAMED = "unnamed"

# 타임아웃을 종료 코드로 표현할 때 쓰는 관용값 (실제 pytest 코드와 겹치지 않는다).
_TIMEOUT_CODE = 124

_GUARD_LINE = re.compile(r"^[ \t]*Guard:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

# pytest 실패 요약에서 "가드가 아니라 모듈 자체가 깨졌다" 를 알아보는 표식.
# 이 저장소의 킬 테스트는 함수 안에서 import 하므로 이런 실패도 exit=1 로 온다.
_IMPORT_BREAKAGE = re.compile(
    r"^E\s+(SyntaxError|IndentationError|ImportError|ModuleNotFoundError):.*$",
    re.MULTILINE,
)

# 정리해야 할 샌드박스 홀더 경로. atexit 가 마지막 방어선이다.
_SANDBOXES: list[Path] = []


class CatalogError(Exception):
    """카탈로그 파일이 없거나, 파싱되지 않거나, 구조가 잘못된 경우."""


@dataclass(frozen=True)
class Mutation:
    """카탈로그 항목 1건 — "이 스니펫을 저걸로 바꾸면 무슨 일이 나는가"."""

    guard: str
    file: str
    find: str
    replace: str


@dataclass(frozen=True)
class GuardResult:
    guard: str
    status: str
    detail: str = ""

    def render(self) -> str:
        if self.status == KILLED:
            return f"{self.guard} killed by {self.detail}"
        if self.status == SURVIVED:
            return f"{self.guard} SURVIVED — {self.detail}"
        if self.status == UNNAMED:
            return f'{self.guard} unnamed — no test declares "Guard: {self.guard}"'
        return f"{self.guard} ERROR — {self.detail}"


@dataclass
class Report:
    results: list[GuardResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    # 이번 실행이 검사하려던 항목 수. fail-fast 로 중간에 멈추면 결과 수보다
    # 크다 — 분모가 없으면 "1 guards" 가 "카탈로그에 1건뿐" 으로 오독된다.
    total: int = 0

    def _count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def killed(self) -> int:
        return self._count(KILLED)

    @property
    def survived(self) -> int:
        return self._count(SURVIVED)

    @property
    def error_count(self) -> int:
        return self._count(ERROR) + self._count(UNNAMED) + len(self.errors)

    @property
    def ok(self) -> bool:
        return (
            bool(self.results)
            and not self.errors
            and all(r.status == KILLED for r in self.results)
        )

    def summary(self) -> str:
        return (
            f"{len(self.results)}/{self.total or len(self.results)} guards, "
            f"{self.killed} killed, {self.survived} survived, "
            f"{self.error_count} errors — {self.elapsed:.1f}s"
        )


# ---------------------------------------------------------------------------
# 카탈로그
# ---------------------------------------------------------------------------
def load_catalog(path: str | os.PathLike[str]) -> list[Mutation]:
    """``guard_mutations.toml`` 을 읽어 :class:`Mutation` 목록으로 만든다.

    Raises:
        CatalogError: 파일이 없거나, TOML 이 깨졌거나, 필수 키가 없거나,
            같은 가드 ID 가 두 번 선언된 경우.
    """
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CatalogError(f"카탈로그 파일이 없습니다: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"카탈로그 TOML 파싱 실패 ({path}): {e}") from e

    entries = raw.get("mutation")
    if not isinstance(entries, list) or not entries:
        raise CatalogError(f"카탈로그에 [[mutation]] 항목이 없습니다: {path}")

    catalog: list[Mutation] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        missing = [
            key
            for key in ("guard", "file", "find", "replace")
            if not isinstance(entry.get(key), str)
        ]
        if missing:
            raise CatalogError(
                f"[[mutation]] #{index}: 필수 키 누락/타입 오류 {missing}"
            )
        guard = entry["guard"]
        if guard in seen:
            raise CatalogError(f"중복된 가드 ID 입니다: {guard}")
        seen.add(guard)
        catalog.append(
            Mutation(
                guard=guard,
                file=entry["file"],
                find=entry["find"],
                replace=entry["replace"],
            )
        )
    return catalog


# ---------------------------------------------------------------------------
# `Guard:` 표기 수집 (ast — pytest 수집 부작용 없이)
# ---------------------------------------------------------------------------
def _iter_test_scopes(node: ast.AST, prefix: list[str]):
    """클래스 안까지 내려가며 (스코프 경로, 함수 노드) 를 낸다.

    함수 몸통 안의 헬퍼 함수는 pytest 노드가 아니므로 내려가지 않는다.
    """
    for child in getattr(node, "body", []):
        if isinstance(child, ast.ClassDef):
            yield from _iter_test_scopes(child, [*prefix, child.name])
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield [*prefix, child.name], child


def collect_guard_annotations(
    tests_root: str | os.PathLike[str],
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, list[str]]:
    """``Guard: <id>`` 표기 → pytest 노드 ID 목록.

    노드 ID 는 ``repo_root`` 기준 상대 경로로 만들어, 저장소 루트를 cwd 로 두고
    그대로 pytest 인자에 넘길 수 있다.
    """
    tests_root = Path(tests_root)
    repo_root = Path(repo_root) if repo_root is not None else tests_root.parent

    found: dict[str, set[str]] = {}
    for py_file in sorted(tests_root.rglob("*.py")):
        try:
            rel = py_file.relative_to(repo_root).as_posix()
        except ValueError:  # pragma: no cover - tests_root 가 저장소 밖인 경우
            rel = py_file.as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for scope, func in _iter_test_scopes(tree, []):
            docstring = ast.get_docstring(func) or ""
            for match in _GUARD_LINE.finditer(docstring):
                node_id = "::".join([rel, *scope])
                found.setdefault(match.group(1), set()).add(node_id)

    return {guard: sorted(nodes) for guard, nodes in sorted(found.items())}


# ---------------------------------------------------------------------------
# 샌드박스
# ---------------------------------------------------------------------------
def _make_sandbox(repo_root: Path) -> Path:
    """저장소를 임시 디렉터리로 1회 복사하고 사본 경로를 돌려준다."""
    holder = Path(tempfile.mkdtemp(prefix="guard-mutations-"))
    _SANDBOXES.append(holder)
    atexit.register(_cleanup_sandboxes)
    sandbox = holder / "repo"
    shutil.copytree(
        repo_root,
        sandbox,
        symlinks=True,
        ignore=shutil.ignore_patterns(*SANDBOX_IGNORE),
    )
    return sandbox


def _cleanup_sandboxes() -> None:
    """모든 샌드박스를 제거한다. 여러 번 호출해도 안전하다."""
    while _SANDBOXES:
        holder = _SANDBOXES.pop()
        shutil.rmtree(holder, ignore_errors=True)
        if holder.exists():  # pragma: no cover - 정리 실패는 사람 손이 필요하다
            print(
                f"[guard-mutations] 샌드박스를 지우지 못했습니다. "
                f"수동 삭제가 필요합니다: rm -rf {holder}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# pytest 실행
# ---------------------------------------------------------------------------
def _run_pytest(sandbox: Path, nodes: list[str], timeout: int) -> tuple[int, str]:
    """샌드박스 안에서 지정 노드만 실행하고 (종료 코드, 출력) 을 돌려준다."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(sandbox)
    env["NO_COLOR"] = "1"  # FAILED 라인 파싱이 ANSI 코드에 흔들리지 않게.
    command = [
        sys.executable,
        "-m",
        "pytest",
        *nodes,
        "--no-cov",
        "-p",
        "no:cacheprovider",
        "-q",
        "-rf",
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - 방어적 (유한성 보장)
        return _TIMEOUT_CODE, f"pytest 가 {timeout}s 안에 끝나지 않았습니다."
    return proc.returncode, proc.stdout + proc.stderr


def _failed_nodes(output: str) -> list[str]:
    """`-rf` 요약에서 실패한 노드 ID 를 뽑는다."""
    nodes = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            nodes.append(line[len("FAILED ") :].split(" ")[0].strip())
    return nodes


def _snippet_line(text: str, snippet: str) -> int:
    """원본 파일에서 스니펫이 시작하는 1-based 행 번호."""
    index = text.find(snippet)
    return text.count("\n", 0, index) + 1 if index >= 0 else 0


# ---------------------------------------------------------------------------
# 검증 → 실행
# ---------------------------------------------------------------------------
def _validate(
    selected: list[Mutation],
    all_guards: set[str],
    annotations: dict[str, list[str]],
    repo_root: Path,
) -> tuple[list[GuardResult], list[str]]:
    """뮤테이션을 적용하기 **전에** 카탈로그 전체를 검증한다."""
    results: list[GuardResult] = []
    errors: list[str] = []

    for guard, nodes in annotations.items():
        if guard not in all_guards:
            errors.append(
                f"{guard} 은(는) 카탈로그에 없는 Guard 표기입니다 — {', '.join(nodes)}"
            )

    for mutation in selected:
        if mutation.file not in ALLOWED_FILES:
            results.append(
                GuardResult(
                    mutation.guard,
                    ERROR,
                    f"허용 목록 밖 파일입니다: {mutation.file} "
                    f"(허용: {', '.join(sorted(ALLOWED_FILES))})",
                )
            )
            continue

        target = repo_root / mutation.file
        if not target.is_file():
            results.append(
                GuardResult(mutation.guard, ERROR, f"대상 파일이 없습니다: {target}")
            )
            continue

        count = target.read_text(encoding="utf-8").count(mutation.find)
        if count != 1:
            results.append(
                GuardResult(
                    mutation.guard,
                    ERROR,
                    f"find 스니펫이 {mutation.file} 에 {count}회 등장합니다 — "
                    f"정확히 1회여야 합니다",
                )
            )
            continue

        if not annotations.get(mutation.guard):
            results.append(GuardResult(mutation.guard, UNNAMED))

    return results, errors


def run_catalog(
    *,
    repo_root: str | os.PathLike[str],
    catalog: list[Mutation],
    tests_root: str | os.PathLike[str] | None = None,
    guards: list[str] | None = None,
    timeout: int = PYTEST_TIMEOUT_SECONDS,
    stream=None,
) -> Report:
    """카탈로그를 한 번 훑고 :class:`Report` 를 돌려준다 (원본 트리 불변)."""
    stream = stream if stream is not None else sys.stdout
    started = time.monotonic()
    repo_root = Path(repo_root).resolve()
    tests_root = Path(tests_root) if tests_root else repo_root / "tests"
    report = Report()

    def emit(line: str) -> None:
        print(line, file=stream)

    all_guards = {m.guard for m in catalog}
    selected = catalog
    report.total = len(catalog)
    if guards:
        unknown = sorted(set(guards) - all_guards)
        if unknown:
            report.errors.append(
                f"카탈로그에 없는 가드를 지정했습니다: {', '.join(unknown)}"
            )
            emit(report.errors[-1])
            report.elapsed = time.monotonic() - started
            emit(report.summary())
            return report
        selected = [m for m in catalog if m.guard in guards]
        report.total = len(selected)

    annotations = collect_guard_annotations(tests_root, repo_root)
    report.results, report.errors = _validate(
        selected, all_guards, annotations, repo_root
    )
    if report.results or report.errors:
        # 하나라도 어긋나면 아무것도 뮤테이션하지 않고 끝낸다 (fail fast).
        for line in report.errors:
            emit(line)
        for result in report.results:
            emit(result.render())
        report.elapsed = time.monotonic() - started
        emit(report.summary())
        return report

    sandbox = _make_sandbox(repo_root)
    try:
        baseline_cache: dict[frozenset[str], tuple[int, str]] = {}
        for mutation in selected:
            nodes = annotations[mutation.guard]
            key = frozenset(nodes)
            if key not in baseline_cache:
                baseline_cache[key] = _run_pytest(sandbox, nodes, timeout)
            code, output = baseline_cache[key]
            if code != 0:
                report.results.append(
                    GuardResult(
                        mutation.guard,
                        ERROR,
                        f"baseline-failed — 뮤테이션 전에 이미 실패/오류입니다 "
                        f"(exit={code}): {', '.join(nodes)}",
                    )
                )
                emit(report.results[-1].render())
                continue

            target = sandbox / mutation.file
            original = target.read_bytes()
            mutated = original.decode("utf-8").replace(
                mutation.find, mutation.replace, 1
            )
            # 뮤턴트가 import 조차 되지 않으면 바인딩된 테스트는 가드와 무관하게
            # 실패하고 pytest 는 exit=1 을 낸다 — 그 오탐을 kill 로 세는 것이야말로
            # RL-004 의 재발이다. 문법 검증에 실패하면 kill 이 아니라 ERROR 다.
            try:
                compile(mutated, str(target), "exec")
            except SyntaxError as e:
                report.results.append(
                    GuardResult(
                        mutation.guard,
                        ERROR,
                        f"replace 결과가 문법적으로 유효하지 않습니다 "
                        f"({mutation.file}:{e.lineno}): {e.msg}",
                    )
                )
                emit(report.results[-1].render())
                continue
            try:
                target.write_text(mutated, encoding="utf-8")
                code, output = _run_pytest(sandbox, nodes, timeout)
            finally:
                target.write_bytes(original)

            report.results.append(
                _verdict(mutation, nodes, code, output, timeout, repo_root)
            )
            emit(report.results[-1].render())
    finally:
        _cleanup_sandboxes()

    report.elapsed = time.monotonic() - started
    emit(report.summary())
    return report


def _verdict(
    mutation: Mutation,
    nodes: list[str],
    code: int,
    output: str,
    timeout: int,
    repo_root: Path,
) -> GuardResult:
    """pytest 종료 코드를 판정으로 옮긴다. **1 만 kill 후보다.**"""
    if code == 1:
        # 킬 테스트는 대상 모듈을 함수 안에서 import 한다 — 모듈이 깨졌을 때도
        # 수집 오류가 아니라 exit=1 로 온다. 가드 뮤테이션은 **단언**을
        # 실패시켜야지 import 를 깨뜨려서는 안 되므로 이건 kill 이 아니다.
        broken = _IMPORT_BREAKAGE.search(output)
        if broken:
            return GuardResult(
                mutation.guard,
                ERROR,
                f"뮤턴트를 import 하지 못했습니다 — 가드와 무관한 실패라 kill 이 "
                f"아닙니다: {broken.group(0).strip()}",
            )
        failing = _failed_nodes(output) or nodes
        return GuardResult(mutation.guard, KILLED, ", ".join(failing))
    if code == 0:
        source = (repo_root / mutation.file).read_text(encoding="utf-8")
        line = _snippet_line(source, mutation.find)
        return GuardResult(
            mutation.guard,
            SURVIVED,
            f"{mutation.file}:{line} — bound tests: {', '.join(nodes)}",
        )
    if code == _TIMEOUT_CODE:
        detail = f"pytest 타임아웃 ({timeout}s) — kill 로 세지 않습니다"
    else:
        detail = (
            f"pytest 가 exit={code} 로 끝났습니다 (1 이 아니므로 kill 이 아닙니다). "
            f"{_tail(output)}"
        )
    return GuardResult(mutation.guard, ERROR, detail)


def _tail(output: str, lines: int = 3) -> str:
    kept = [line for line in output.strip().splitlines() if line.strip()][-lines:]
    return " | ".join(kept)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    # `atexit` 는 SIGTERM 에 돌지 않는다 — 핸들러가 없으면 무력화된 가드가 든
    # 사본이 $TMPDIR 에 남는다. 샌드박스가 만들어지기 전에 미리 걸어 둔다.
    signal.signal(
        signal.SIGTERM,
        lambda *_: (_cleanup_sandboxes(), sys.exit(143)),
    )
    default_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="보안 가드 뮤테이션 점검 (ISSUE-43, NFR-028 / RL-004)"
    )
    parser.add_argument("--catalog", default=None, help="카탈로그 TOML 경로")
    parser.add_argument("--repo-root", default=str(default_root), help="저장소 루트")
    parser.add_argument("--tests-root", default=None, help="테스트 루트")
    parser.add_argument(
        "--guard",
        action="append",
        dest="guards",
        default=None,
        help="지정한 가드 ID 만 실행 (반복 가능)",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    catalog_path = (
        Path(args.catalog) if args.catalog else repo_root / DEFAULT_CATALOG_NAME
    )
    try:
        catalog = load_catalog(catalog_path)
    except CatalogError as e:
        print(f"[guard-mutations] {e}")
        return 2

    report = run_catalog(
        repo_root=repo_root,
        catalog=catalog,
        tests_root=args.tests_root,
        guards=args.guards,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    sys.exit(main())
