"""
룸별 브랜딩 로고 에셋 저장소 (ISSUE-38, FR-077 / NFR-028).

주최·주관·후원 로고 파일을 ``data/branding/{room_id}/`` 아래에 검증된 형태로
저장·열거·삭제하고, 서빙 계층이 쓸 안전한 경로/응답 헤더를 만들어 준다.
바이너리는 DB 에 넣지 않는다 — 파일은 파일시스템, 파일명만
``rooms.stage_config.logo_groups[].assets`` 에 남는다 (docs/data_model.md).

설계 원칙
---------
- **Importable + side-effect-free** (RL-001 / RL-005): import 시점에 디렉터리를
  만들거나 환경변수를 캐시하지 않는다. ``BRANDING_DIR`` 은 ``DB_PATH`` 와 같은
  관례로 호출 시점에 ``os.getenv`` 로 읽는다. aiohttp / Streamlit 을 import
  하지 않으므로 관리자 폼(ISSUE-39)과 라우트 계층이 그대로 재사용한다.
- **쓰기 경로는 엄격하게** : :func:`save_asset` 은 신뢰 경계다. 조용히 고쳐
  저장하지 않고, 사람이 읽을 수 있는 한국어 사유와 함께 ``ValueError`` 로 거절한다.
- **읽기 경로는 절대 예외를 던지지 않는다** : :func:`resolve_asset_path` /
  :func:`list_assets` / :func:`delete_asset` 은 무대 페이지와 정적 라우트가
  행사 중에 호출하는 함수다. 어떤 입력이 와도 ``None`` / ``[]`` / ``False`` 로
  degrade 하고 상세는 서버 로그에만 남긴다 (RL-006).

경로 봉인 (NFR-028) — 2단 방어
------------------------------
1. **sanitize** : 디렉터리 구분자(``/`` ``\\``)·널바이트·제어문자·``..`` 는
   조용히 제거하지 않고 **거부**한다. 조용히 basename 만 뽑으면
   ``"a/b.png"`` 이 ``"b.png"`` 로 통과해 버려 방어가 무의미해진다.
   그 밖의 문자(한글 등)는 ``[A-Za-z0-9._-]`` 만 남기고 제거한다.
2. **containment** : 최종 ``Path.resolve()`` 결과가 룸 디렉터리의
   ``resolve()`` 하위인지 ``is_relative_to()`` 로 재확인하고,
   ``Path.is_symlink()`` 로 심볼릭 링크를 거부한다.

Public API
----------
- :func:`save_asset` : 검증 후 저장하고 **실제 저장된 파일명**을 반환.
- :func:`list_assets` : 룸에 저장된 에셋 파일명 목록 (정렬).
- :func:`delete_asset` : 파일 1건 삭제. 성공 여부를 bool 로 반환.
- :func:`resolve_asset_path` : 서빙 가능한 실제 경로 또는 ``None``.
- :func:`build_asset_headers` : Content-Type / 캐시 / SVG CSP 응답 헤더.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 룸당 상한은 stage_config 가 이미 정의하고 있다 (validate_stage_config 가 같은
# 값으로 검증한다). 상수를 복제하지 않고 재사용해 두 계층이 어긋나지 않게 한다.
from stage_config import MAX_ASSETS_PER_ROOM

__all__ = [
    "ALLOWED_EXTENSIONS",
    "DEFAULT_BRANDING_DIR",
    "MAX_ASSETS_PER_ROOM",
    "MAX_ASSET_BYTES",
    "SVG_CSP",
    "build_asset_headers",
    "delete_asset",
    "list_assets",
    "resolve_asset_path",
    "save_asset",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# 저장 루트. docker-compose 의 ``./data`` 볼륨 안이라 컨테이너 재시작에도 로고가
# 보존된다 (app.db 와 같은 볼륨).
DEFAULT_BRANDING_DIR = "data/branding"

# 파일당 상한 2 MiB. 로고는 벡터/작은 PNG 로 충분하고, 상한이 없으면 행사장
# 담당자가 원본 인쇄용 파일을 그대로 올려 무대 페이지 로딩을 망친다.
MAX_ASSET_BYTES = 2 * 1024 * 1024

# 확장자 → (Content-Type, 내용 검증 함수 키). 화이트리스트 밖은 저장도 서빙도 안 된다.
ALLOWED_EXTENSIONS: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".svg")

_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

# 업로드된 SVG 는 마크업이다 — 무대 페이지와 같은 오리진에서 서빙되므로
# 내부 <script>/외부 참조가 실행되면 그대로 XSS 다. 이미지로만 쓰이니
# default-src 'none' 으로 전부 막고, 인라인 style 만 허용한다 (NFR-028).
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'"

_CACHE_CONTROL = "public, max-age=300"  # 행사 중 로고 교체가 5분 내 반영되도록 짧게.

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_SVG_PREFIXES = (b"<?xml", b"<svg")
_UTF8_BOM = b"\xef\xbb\xbf"

# 허용 문자 클래스 밖은 제거한다. 구분자/제어문자는 아래에서 먼저 거부하므로
# 여기까지 오는 건 한글·공백·이모지 같은 "안전하지만 못 쓰는" 문자뿐이다.
_DISALLOWED_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# 파일명 stem 상한. 실측(RL-015)상 255바이트를 넘는 이름은 ``is_symlink()`` /
# ``write_bytes()`` 가 ValueError 가 아니라 ``OSError(ENAMETOOLONG)`` 로 터진다.
# 충돌 회피 접미사(``-12``)를 붙여도 여유가 남도록 넉넉히 낮춰 잡는다.
_MAX_STEM_LEN = 80
_MAX_ROOM_ID_LEN = 64

# 충돌 회피 시도 횟수 상한. 룸당 12개 제한이 있으므로 실제로는 몇 번이면 끝난다.
_MAX_COLLISION_ATTEMPTS = 100

# stem 이 전부 제거된 경우(예: "로고.png")의 대체 이름.
_FALLBACK_STEM = "asset"


# ---------------------------------------------------------------------------
# Public API — 쓰기 경로 (엄격, ValueError 로 거절)
# ---------------------------------------------------------------------------
def save_asset(room_id: str, filename: str, data: bytes) -> str:
    """에셋 1건을 검증 후 저장하고 **실제 저장된 파일명**을 반환한다.

    검증 순서는 의도적이다 — 용량/이름/내용 검사를 모두 통과하기 전에는
    디렉터리조차 만들지 않는다. 덕분에 거부된 업로드는 디스크에 부분 파일은
    물론 빈 룸 디렉터리도 남기지 않는다 (AC: "부분 파일조차 생성되지 않는다").

    같은 파일명이 이미 있으면 덮어쓰지 않고 ``logo-2.png`` 처럼 숫자 접미사를
    붙인 새 이름으로 저장한다. 배타 생성(``open(..., "xb")``)을 쓰므로 동시
    업로드 중에도 기존 파일을 덮어쓰지 않는다.

    Args:
        room_id: 룸 식별자. 신뢰할 수 없는 입력으로 취급해 파일명과 동일하게 봉인한다.
        filename: 업로드 원본 파일명.
        data: 파일 내용.

    Returns:
        저장된 파일명 (호출자는 이 값을 ``stage_config`` 에 기록해야 한다).

    Raises:
        ValueError: 타입/용량/확장자/내용/개수/경로 검증에 하나라도 걸린 경우.
            사유 문자열은 관리자 폼에 그대로 노출할 수 있는 한국어다.
    """
    if not isinstance(data, bytes | bytearray):
        raise ValueError("업로드 파일 내용을 읽을 수 없습니다.")
    data = bytes(data)

    # 1) 용량 — 파일시스템을 건드리기 전에 먼저 막는다.
    if len(data) > MAX_ASSET_BYTES:
        limit_mb = MAX_ASSET_BYTES // (1024 * 1024)
        raise ValueError(
            f"로고 파일은 {limit_mb}MB 이하만 업로드할 수 있습니다 "
            f"(현재 {len(data) / (1024 * 1024):.1f}MB)."
        )

    # 2) 이름 봉인 (구분자/제어문자/`..`/확장자 화이트리스트).
    safe_room = _sanitize_room_id(room_id)
    safe_name = _sanitize_filename(filename)
    suffix = _suffix_of(safe_name)

    # 3) 내용-확장자 일치 (매직바이트).
    if not _content_matches_extension(suffix, data):
        raise ValueError(
            f"파일 내용이 {suffix} 형식과 일치하지 않습니다. 확장자를 확인해 주세요."
        )

    # 4) 룸당 개수 상한.
    existing = list_assets(safe_room)
    if len(existing) >= MAX_ASSETS_PER_ROOM:
        raise ValueError(
            f"로고 파일은 룸당 최대 {MAX_ASSETS_PER_ROOM}개까지 등록할 수 있습니다 "
            f"(현재 {len(existing)}개). 기존 파일을 삭제한 뒤 다시 시도해 주세요."
        )

    # 5) 2단 방어의 두 번째 단계 — resolve() 결과가 룸 디렉터리 하위인지 확인.
    room_dir = _contained_room_dir(safe_room)
    if room_dir is None:
        raise ValueError("잘못된 룸 식별자입니다.")

    try:
        room_dir.mkdir(parents=True, exist_ok=True)
        return _write_without_overwriting(room_dir, safe_name, data)
    except OSError as e:
        # RL-006: 내부 경로/errno 는 로그에만. 호출자에게는 일반 메시지.
        print(f"[Branding] save failed (room={safe_room} name={safe_name}): {e!r}")
        raise ValueError(
            "로고 파일을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from e


# ---------------------------------------------------------------------------
# Public API — 읽기 경로 (절대 예외를 던지지 않는다)
# ---------------------------------------------------------------------------
def resolve_asset_path(room_id: str, filename: str) -> Path | None:
    """서빙 가능한 실제 파일 경로를 돌려준다. 아니면 ``None``.

    다음 중 하나라도 걸리면 ``None`` 이다 — 잘못된 룸/파일명, traversal 시도,
    화이트리스트 밖 확장자, 심볼릭 링크, 디렉터리, 존재하지 않는 파일.
    라우트 계층이 이 반환값만 보고 200/404 를 결정할 수 있게 하려는 의도다.

    **절대 예외를 던지지 않는다.** 계약이 무조건이므로 ``except Exception`` 을
    쓴다 (RL-015). 실제 퍼징에서 이 경로는 세 갈래로 터졌다 — 타입이 틀리면
    ``re.sub`` 이 ``TypeError``, 널바이트는 ``Path.resolve()`` 가 ``ValueError``,
    255바이트를 넘는 이름은 ``is_symlink()`` 가 ``OSError(ENAMETOOLONG)``.
    ``OSError`` 는 ``ValueError`` 의 하위 클래스가 아니므로 열거형 except 는
    조용히 불완전해진다.
    """
    try:
        safe_room = _sanitize_room_id(room_id)
        safe_name = _sanitize_filename(filename)
        room_dir = _contained_room_dir(safe_room)
        if room_dir is None:
            return None

        candidate = room_dir / safe_name
        # 심볼릭 링크는 lstat 기반으로 먼저 거부한다 (링크 대상이 룸 안이어도 금지).
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        if not resolved.is_relative_to(room_dir):
            return None
        if not resolved.is_file():
            return None
        return resolved
    except ValueError:
        # 정상적인 거부 경로 (sanitize 실패, 널바이트). 로그를 남기지 않는다 —
        # 공개 라우트라 잘못된 요청만으로 로그가 오염되면 안 된다.
        return None
    except Exception as e:  # pragma: no cover - 방어적 (RL-015)
        print(f"[Branding] resolve failed (room={room_id!r}): {e!r}")
        return None


def list_assets(room_id: str) -> list[str]:
    """룸에 저장된 에셋 파일명 목록 (이름순). 알 수 없는 룸은 ``[]``.

    화이트리스트 밖 확장자, 심볼릭 링크, 디렉터리는 목록에서 제외한다 —
    ``resolve_asset_path`` 가 서빙을 거부하는 항목이 관리자 화면에만 보이는
    불일치를 막기 위해서다. 절대 예외를 던지지 않는다.
    """
    try:
        safe_room = _sanitize_room_id(room_id)
        room_dir = _contained_room_dir(safe_room)
        if room_dir is None or not room_dir.is_dir():
            return []

        names = []
        for entry in room_dir.iterdir():
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            names.append(entry.name)
        return sorted(names)
    except ValueError:
        return []
    except Exception as e:  # pragma: no cover - 방어적 (RL-015)
        print(f"[Branding] list failed (room={room_id!r}): {e!r}")
        return []


def delete_asset(room_id: str, filename: str) -> bool:
    """에셋 1건을 삭제한다. 삭제했으면 ``True``, 아니면 ``False``.

    ``resolve_asset_path`` 를 그대로 재사용하므로 traversal/심볼릭 링크/
    화이트리스트 밖 확장자는 삭제 대상이 될 수 없다. 절대 예외를 던지지 않는다.
    """
    path = resolve_asset_path(room_id, filename)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except OSError as e:
        print(f"[Branding] delete failed (room={room_id!r}): {e!r}")
        return False


def build_asset_headers(filename: str) -> dict[str, str]:
    """정적 서빙용 응답 헤더를 만든다 (FR-078).

    - Content-Type: 확장자 매핑. 알 수 없으면 ``application/octet-stream``
      (브라우저가 추측해서 실행 가능한 타입으로 해석하지 못하게 한다).
    - Cache-Control: ``public, max-age=300`` — 행사 중 로고 교체가 5분 내 반영.
    - SVG 에 한해 :data:`SVG_CSP` — 업로드 마크업 안의 스크립트/외부 참조 차단.
    """
    suffix = Path(str(filename)).suffix.lower()
    headers = {
        "Content-Type": _CONTENT_TYPES.get(suffix, "application/octet-stream"),
        "Cache-Control": _CACHE_CONTROL,
    }
    if suffix == ".svg":
        headers["Content-Security-Policy"] = SVG_CSP
    return headers


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _branding_root() -> Path:
    """저장 루트. RL-001 — import 시점이 아니라 호출 시점에 환경변수를 읽는다."""
    return Path(os.getenv("BRANDING_DIR") or DEFAULT_BRANDING_DIR)


def _reject_path_signatures(value: str, label: str) -> None:
    """구분자·널바이트·제어문자·``..`` 는 제거가 아니라 거부한다.

    조용히 제거하면 ``"a/b.png"`` 이 ``"ab.png"`` 로 통과해 방어가 우연에
    기대게 된다. 이 문자들은 정상 업로드에 등장할 이유가 없으므로 거부가 맞다.
    """
    if "/" in value or "\\" in value:
        raise ValueError(f"{label}에 경로 구분자를 사용할 수 없습니다.")
    if ".." in value:
        raise ValueError(f"{label}에 '..' 을 사용할 수 없습니다.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label}에 제어 문자를 사용할 수 없습니다.")


def _sanitize_filename(filename: str) -> str:
    """파일명을 ``<stem><ext>`` 형태의 안전한 이름으로 정규화한다.

    Raises:
        ValueError: str 이 아니거나, 비어 있거나, 경로 구분자/제어문자/``..``
            를 포함하거나, 확장자가 화이트리스트 밖인 경우.
    """
    if not isinstance(filename, str):
        # (RL-015) 타입 체크가 없으면 아래 re.sub 이 TypeError 로 터진다.
        raise ValueError("파일명이 올바르지 않습니다.")

    name = filename.strip()
    if not name:
        raise ValueError("파일명이 비어 있습니다.")

    _reject_path_signatures(name, "파일명")

    stem, dot, ext = name.rpartition(".")
    if not dot:
        raise ValueError(
            f"확장자가 없는 파일은 업로드할 수 없습니다 "
            f"(허용: {', '.join(ALLOWED_EXTENSIONS)})."
        )
    suffix = f".{ext.lower()}"
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"허용되지 않는 확장자입니다 (허용: {', '.join(ALLOWED_EXTENSIONS)})."
        )

    # 한글/공백/이모지 등은 제거. 전부 제거되면 기본 stem 으로 대체해
    # 한국어 파일명 업로드가 통째로 막히지 않게 한다.
    safe_stem = _DISALLOWED_CHARS.sub("", stem).lstrip(".")
    if not safe_stem:
        safe_stem = _FALLBACK_STEM
    # (RL-015) 255바이트 초과 이름은 OSError(ENAMETOOLONG) 를 유발하므로 절단한다.
    safe_stem = safe_stem[:_MAX_STEM_LEN]

    return f"{safe_stem}{suffix}"


def _sanitize_room_id(room_id: str) -> str:
    """룸 식별자를 봉인한다. 실패하면 ``ValueError``.

    룸 id 는 서버가 ``secrets.token_urlsafe`` 로 만드는 ``[A-Za-z0-9_-]`` 문자열
    이다. 파일명과 달리 사용자가 타이핑하는 값이 아니므로, 클래스 밖 문자가
    보이면 제거하지 않고 거부한다 (버그이거나 공격이다).
    """
    if not isinstance(room_id, str):
        raise ValueError("룸 식별자가 올바르지 않습니다.")

    value = room_id.strip()
    if not value:
        raise ValueError("룸 식별자가 비어 있습니다.")
    if len(value) > _MAX_ROOM_ID_LEN:
        raise ValueError("룸 식별자가 너무 깁니다.")

    _reject_path_signatures(value, "룸 식별자")

    if value.startswith("."):
        raise ValueError("룸 식별자는 '.' 으로 시작할 수 없습니다.")
    if _DISALLOWED_CHARS.search(value):
        raise ValueError("룸 식별자에 사용할 수 없는 문자가 있습니다.")
    return value


def _contained_room_dir(safe_room: str) -> Path | None:
    """룸 디렉터리를 resolve 하고 branding 루트 하위인지 재확인한다 (2단 방어).

    루트/룸 디렉터리 자체가 루트 밖을 가리키는 심볼릭 링크여도 ``resolve()``
    가 실제 위치로 펼쳐 주므로 ``is_relative_to`` 검사에서 걸러진다.
    """
    root = _branding_root().resolve()
    room_dir = (root / safe_room).resolve()
    if not room_dir.is_relative_to(root):
        return None
    return room_dir


def _suffix_of(safe_name: str) -> str:
    return safe_name[safe_name.rfind(".") :].lower()


def _content_matches_extension(suffix: str, data: bytes) -> bool:
    """매직바이트로 내용-확장자 일치를 확인한다 (NFR-028).

    SVG 는 텍스트 포맷이라 시그니처가 없으므로, 선행 공백(과 Windows 도구가
    자주 붙이는 UTF-8 BOM)을 제거한 뒤 ``<?xml`` 또는 ``<svg`` 로 시작하는지
    본다. ``<script>...`` 로 시작하는 파일은 여기서 걸린다.
    """
    if suffix == ".png":
        return data.startswith(_PNG_SIGNATURE)
    if suffix in (".jpg", ".jpeg"):
        return data.startswith(_JPEG_SIGNATURE)
    if suffix == ".svg":
        head = data.removeprefix(_UTF8_BOM).lstrip()
        return head.startswith(_SVG_PREFIXES)
    return False  # pragma: no cover - 화이트리스트 밖은 이미 거부됨


def _write_without_overwriting(room_dir: Path, safe_name: str, data: bytes) -> str:
    """``logo.png`` → 충돌 시 ``logo-2.png`` → ``logo-3.png`` … 순으로 저장한다.

    배타 생성 모드(``"xb"``)를 쓰기 때문에 존재 확인과 쓰기 사이의 경합에서도
    기존 파일을 덮어쓰지 않는다 (AC: "기존 파일을 덮어쓰지 않고").
    """
    suffix = _suffix_of(safe_name)
    stem = safe_name[: -len(suffix)]

    for attempt in range(1, _MAX_COLLISION_ATTEMPTS + 1):
        candidate_name = safe_name if attempt == 1 else f"{stem}-{attempt}{suffix}"
        candidate = room_dir / candidate_name
        try:
            with open(candidate, "xb") as fp:
                fp.write(data)
            return candidate_name
        except FileExistsError:
            continue

    raise ValueError(  # pragma: no cover - 룸당 12개 상한 때문에 도달 불가
        "같은 이름의 파일이 너무 많습니다. 파일명을 변경한 뒤 다시 시도해 주세요."
    )
