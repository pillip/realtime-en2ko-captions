"""Regression guard for the pre-commit formatter policy (ISSUE-65).

This repository runs exactly **one** formatter: ``black``. ``ruff`` is present
as a **linter only**. The ``ruff-format`` hook was deliberately removed because
``ruff format`` and ``black`` do not converge on the wrapped
``assert <cond>, <long msg>`` layout:

    ruff format ->  assert cond, (
                        msg
                    )
    black       ->  assert (
                        cond
                    ), msg

Each reverts the other, so the two hooks cycle with period 2 and **both** report
"files were modified by this hook" on every run, forever. A file containing that
layout could not be committed at all without ``SKIP=ruff-format``. The deadlock
recurred across four sprints before it got an owner.

The long ``assert`` statements in this module are written in black's layout **on
purpose** — 교착 레이아웃을 이 파일이 직접 들고 있다. They are black-stable and
ruff-format-unstable, so this module is a live canary: re-adding the
``ruff-format`` hook both fails these assertions and makes this very file
uncommittable again.
"""

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parent.parent
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _hooks() -> list[dict[str, Any]]:
    """Every hook declared in .pre-commit-config.yaml, flattened, in file order."""
    config = yaml.safe_load(PRECOMMIT_CONFIG.read_text(encoding="utf-8"))
    return [hook for repo in config["repos"] for hook in repo["hooks"]]


def _hook_ids() -> list[str]:
    return [hook["id"] for hook in _hooks()]


def _tool(name: str) -> str:
    """Resolve a dev tool from the active interpreter's bin dir, else from PATH."""
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.exists() else name


def test_no_ruff_format_hook_is_registered():
    """ruff-format must stay out: it and black never converge on assert layout."""
    ids = _hook_ids()
    assert (
        "ruff-format" not in ids
    ), f"{PRECOMMIT_CONFIG.name} registers a ruff-format hook again; black is this repo's only formatter — see this module's docstring for why the two cannot coexist"


def test_ruff_lint_hook_survives_with_fix():
    """Dropping the formatter must not drop the linter."""
    ruff_hooks = [hook for hook in _hooks() if hook["id"] == "ruff"]
    assert (
        len(ruff_hooks) == 1
    ), f"expected exactly one 'ruff' lint hook in {PRECOMMIT_CONFIG.name}, found {len(ruff_hooks)}"
    assert ruff_hooks[0].get("args") == [
        "--fix"
    ], f"the ruff lint hook lost its --fix args: {ruff_hooks[0].get('args')!r}"


def test_black_remains_the_sole_formatter():
    """Exactly one formatter hook, and it is black."""
    ids = _hook_ids()
    assert (
        ids.count("black") == 1
    ), f"expected exactly one black hook, found {ids.count('black')}"
    other_formatters = {"ruff-format", "autopep8", "yapf", "blacken-docs"} & set(ids)
    assert (
        not other_formatters
    ), f"a second formatter crept back into {PRECOMMIT_CONFIG.name}: {sorted(other_formatters)}"


def test_pyproject_declares_no_live_ruff_format_table():
    """[tool.ruff.format] would be config for a formatter this repo never runs."""
    ruff_config = (
        tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        .get("tool", {})
        .get("ruff", {})
    )
    assert (
        "format" not in ruff_config
    ), "pyproject.toml still declares a live [tool.ruff.format] table, but no hook runs `ruff format` — remove it or comment it out with the reason (RL-007)"


@pytest.mark.parametrize(
    ("label", "argv", "expected_returncode", "why"),
    [
        (
            "black",
            [_tool("black"), "--check", str(THIS_FILE)],
            0,
            "이 파일은 black 기준으로 이미 안정적이어야 한다. 실패하면 재포맷 커밋이 빠졌다는 뜻이다.",
        ),
        (
            "ruff format",
            [
                _tool("ruff"),
                "format",
                "--check",
                "--config",
                str(PYPROJECT),
                str(THIS_FILE),
            ],
            1,
            "ruff format 은 여전히 이 레이아웃을 되돌리려 해야 한다. 되돌리지 않는다면 누군가 assert 들을 두 포매터가 모두 동의하는 형태로 바꿔 카나리아를 죽인 것이거나, ruff 가 black 과 같은 레이아웃을 채택한 것이다 — 후자라면 이 이슈의 전제를 다시 확인해야 한다.",
        ),
    ],
    ids=["black-accepts-canary", "ruff-format-still-rejects-canary"],
)
def test_canary_layout_still_splits_the_two_formatters(
    label, argv, expected_returncode, why
):
    """The canary must stay a canary — the two formatters must still disagree here.

    Without this, the guard above is decorative (RL-024): someone could rewrite
    these asserts into a layout both formatters accept, and re-adding the
    ruff-format hook would then deadlock nothing until the next unlucky file.
    """
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert result.returncode == expected_returncode, (
        f"{label} exited {result.returncode}, expected {expected_returncode}. {why}\n"
        f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
    )
