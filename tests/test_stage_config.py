"""
stage_config.py 단위 테스트 (ISSUE-37, test_plan TC-050 / TC-051).

검증 대상:
1) `DEFAULT_STAGE_CONFIG` 의 형태 — data_model.md 의 blob shape 와 일치
2) `normalize_stage_config(raw)` — JSON 문자열 / dict / None 입력 보정
   - 깨진 JSON, 잘못된 최상위 타입, 알 수 없는 라벨, dict 아닌 원소 폐기
   - 반환 dict 를 변형해도 모듈 상수나 다음 호출 결과가 오염되지 않는다
3) `validate_stage_config(cfg)` — caption_ratio / 길이 제한 / 로고 개수 상한

설계 메모
---------
- 이 모듈은 import 시 부수효과가 없어야 하므로(RL-001/RL-005) mock 없이
  그대로 import 한다. DB·파일시스템·네트워크 접근 없음.
- RL-004: "값이 존재한다" 가 아니라 "정확히 이 값이다" 를 단언한다.
"""

from __future__ import annotations

import pytest

from stage_config import (
    DEFAULT_STAGE_CONFIG,
    normalize_stage_config,
    validate_stage_config,
)

# data_model.md 가 규정한 고정 라벨 3종 (순서 포함).
_EXPECTED_LABELS = ["주최", "주관", "후원"]


# ---------------------------------------------------------------------------
# DEFAULT_STAGE_CONFIG
# ---------------------------------------------------------------------------
class TestDefaultStageConfig:
    """AC: 기본값은 data_model.md 의 문서화된 형태와 정확히 일치한다."""

    def test_default_shape(self):
        assert DEFAULT_STAGE_CONFIG == {
            "event_title": "",
            "event_subtitle": "",
            "caption_ratio": "1/4",
            "logo_groups": [
                {"label": "주최", "assets": []},
                {"label": "주관", "assets": []},
                {"label": "후원", "assets": []},
            ],
        }

    def test_default_is_importable_without_side_effects(self):
        """모듈 상수만으로 기본값을 얻을 수 있어야 한다 (RL-001)."""
        import sys

        # DB / Streamlit 의존성을 끌어오지 않는다.
        assert "stage_config" in sys.modules
        module = sys.modules["stage_config"]
        assert not hasattr(module, "sqlite3")
        assert not hasattr(module, "st")


# ---------------------------------------------------------------------------
# normalize_stage_config — TC-050
# ---------------------------------------------------------------------------
class TestNormalizeFallsBackToDefault:
    """AC: NULL / 빈 문자열 / 깨진 JSON / 잘못된 최상위 타입 → 기본값."""

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            "{",  # 깨진 JSON
            "[]",  # 최상위 타입이 object 가 아님
            "null",
            "not json at all",
            123,
            [],
            ["주최"],
        ],
        ids=[
            "None",
            "empty-string",
            "whitespace",
            "broken-json",
            "json-array",
            "json-null",
            "plain-text",
            "int",
            "python-list",
            "python-list-of-str",
        ],
    )
    def test_returns_default_dict(self, raw):
        assert normalize_stage_config(raw) == DEFAULT_STAGE_CONFIG

    @pytest.mark.parametrize(
        "raw",
        ["[" * 100_000, '{"a":' * 100_000],
        ids=["deep-array", "deep-object"],
    )
    def test_deeply_nested_json_degrades_instead_of_raising(self, raw):
        """깊게 중첩된 JSON 은 RecursionError 를 내지 않고 기본값이 된다.

        json.loads 는 이 입력에 대해 JSONDecodeError 가 아니라 RecursionError
        를 던진다. 이걸 잡지 않으면 "절대 예외를 던지지 않는다" 계약이 깨지고
        무대 페이지(ISSUE-40)가 행사 중에 죽는다.
        """
        assert normalize_stage_config(raw) == DEFAULT_STAGE_CONFIG

    def test_empty_json_object_is_valid_and_normalises_to_default(self):
        """컬럼 DEFAULT 값인 '{}' 도 기본값으로 정규화된다."""
        assert normalize_stage_config("{}") == DEFAULT_STAGE_CONFIG
        assert normalize_stage_config({}) == DEFAULT_STAGE_CONFIG

    def test_never_returns_the_module_constant_itself(self):
        result = normalize_stage_config(None)
        assert result is not DEFAULT_STAGE_CONFIG
        assert result["logo_groups"] is not DEFAULT_STAGE_CONFIG["logo_groups"]

    def test_mutating_result_does_not_affect_later_calls(self):
        """반환 dict 는 매번 새 복사본 — 호출자가 기본값을 오염시킬 수 없다."""
        first = normalize_stage_config(None)
        first["event_title"] = "오염된 타이틀"
        first["caption_ratio"] = "1/3"
        first["logo_groups"][0]["assets"].append("evil.png")
        first["logo_groups"].append({"label": "침입", "assets": []})

        second = normalize_stage_config(None)
        assert second == DEFAULT_STAGE_CONFIG
        assert second["event_title"] == ""
        assert second["caption_ratio"] == "1/4"
        assert second["logo_groups"][0]["assets"] == []
        assert len(second["logo_groups"]) == 3

        # 모듈 상수 자체도 그대로여야 한다.
        assert DEFAULT_STAGE_CONFIG["event_title"] == ""
        assert DEFAULT_STAGE_CONFIG["logo_groups"][0]["assets"] == []
        assert len(DEFAULT_STAGE_CONFIG["logo_groups"]) == 3

    def test_input_dict_is_not_mutated(self):
        """입력 dict 를 in-place 수정하지 않는다."""
        raw = {"event_title": "행사", "logo_groups": []}
        normalize_stage_config(raw)
        assert raw == {"event_title": "행사", "logo_groups": []}


class TestNormalizeAcceptedShapes:
    """AC: JSON 문자열과 dict 를 동일하게 처리한다."""

    def test_json_string_and_dict_produce_the_same_result(self):
        import json

        cfg = {
            "event_title": "2026 개발자 콘퍼런스",
            "event_subtitle": "A홀 기조연설",
            "caption_ratio": "1/3",
            "logo_groups": [
                {"label": "주최", "assets": ["host-1.png"]},
                {"label": "주관", "assets": []},
                {"label": "후원", "assets": ["sponsor-a.svg", "sponsor-b.png"]},
            ],
        }
        from_dict = normalize_stage_config(cfg)
        from_json = normalize_stage_config(json.dumps(cfg, ensure_ascii=False))
        assert from_dict == from_json == cfg

    def test_unknown_caption_ratio_falls_back_to_quarter(self):
        result = normalize_stage_config({"caption_ratio": "1/2"})
        assert result["caption_ratio"] == "1/4"

    def test_non_string_title_becomes_empty_string(self):
        result = normalize_stage_config({"event_title": 42, "event_subtitle": None})
        assert result["event_title"] == ""
        assert result["event_subtitle"] == ""

    def test_title_is_stripped(self):
        result = normalize_stage_config({"event_title": "  A홀 기조연설  "})
        assert result["event_title"] == "A홀 기조연설"

    def test_unknown_keys_are_dropped(self):
        result = normalize_stage_config({"event_title": "행사", "hacker": "payload"})
        assert "hacker" not in result
        assert set(result) == set(DEFAULT_STAGE_CONFIG)


# ---------------------------------------------------------------------------
# normalize_stage_config — logo_groups (TC-050 / AC7)
# ---------------------------------------------------------------------------
class TestNormalizeLogoGroups:
    """AC: 알려진 라벨 3종만 고정 순서로 남고 나머지는 폐기된다."""

    def test_unknown_labels_and_non_dict_elements_are_discarded(self):
        result = normalize_stage_config(
            {
                "logo_groups": [
                    "그냥 문자열",
                    None,
                    ["주최"],
                    {"label": "협찬", "assets": ["ghost.png"]},
                    {"label": "후원", "assets": ["sponsor-a.svg"]},
                    {"no_label": True},
                ]
            }
        )
        assert [g["label"] for g in result["logo_groups"]] == _EXPECTED_LABELS
        assert result["logo_groups"][0]["assets"] == []
        assert result["logo_groups"][1]["assets"] == []
        assert result["logo_groups"][2]["assets"] == ["sponsor-a.svg"]

    def test_labels_are_reordered_to_the_fixed_order(self):
        result = normalize_stage_config(
            {
                "logo_groups": [
                    {"label": "후원", "assets": ["c.png"]},
                    {"label": "주최", "assets": ["a.png"]},
                    {"label": "주관", "assets": ["b.png"]},
                ]
            }
        )
        assert result["logo_groups"] == [
            {"label": "주최", "assets": ["a.png"]},
            {"label": "주관", "assets": ["b.png"]},
            {"label": "후원", "assets": ["c.png"]},
        ]

    def test_missing_labels_are_filled_with_empty_groups(self):
        result = normalize_stage_config({"logo_groups": [{"label": "주최"}]})
        assert result["logo_groups"] == [
            {"label": "주최", "assets": []},
            {"label": "주관", "assets": []},
            {"label": "후원", "assets": []},
        ]

    def test_duplicate_labels_keep_the_first_occurrence(self):
        result = normalize_stage_config(
            {
                "logo_groups": [
                    {"label": "주최", "assets": ["first.png"]},
                    {"label": "주최", "assets": ["second.png"]},
                ]
            }
        )
        assert result["logo_groups"][0]["assets"] == ["first.png"]
        assert len(result["logo_groups"]) == 3

    def test_non_string_assets_are_dropped(self):
        result = normalize_stage_config(
            {
                "logo_groups": [
                    {
                        "label": "주최",
                        "assets": ["ok.png", 7, None, {"x": 1}, "ok2.svg"],
                    }
                ]
            }
        )
        assert result["logo_groups"][0]["assets"] == ["ok.png", "ok2.svg"]

    def test_non_list_assets_becomes_empty_list(self):
        result = normalize_stage_config(
            {"logo_groups": [{"label": "주관", "assets": "host.png"}]}
        )
        assert result["logo_groups"][1]["assets"] == []

    def test_non_list_logo_groups_becomes_three_empty_groups(self):
        result = normalize_stage_config({"logo_groups": {"주최": ["a.png"]}})
        assert result["logo_groups"] == DEFAULT_STAGE_CONFIG["logo_groups"]


# ---------------------------------------------------------------------------
# validate_stage_config — TC-051
# ---------------------------------------------------------------------------
class TestValidateCaptionRatio:
    """AC: caption_ratio 는 '1/4' | '1/3' 만 허용된다."""

    @pytest.mark.parametrize("ratio", ["1/4", "1/3"])
    def test_allowed_ratios_pass(self, ratio):
        cfg = dict(DEFAULT_STAGE_CONFIG, caption_ratio=ratio)
        ok, reason = validate_stage_config(cfg)
        assert ok is True
        assert reason == ""

    @pytest.mark.parametrize(
        "ratio",
        ["1/2", "", None, "25%", 0.25],
        ids=["half", "empty", "none", "percent", "float"],
    )
    def test_rejected_ratios_return_reason(self, ratio):
        cfg = dict(DEFAULT_STAGE_CONFIG, caption_ratio=ratio)
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert isinstance(reason, str) and reason.strip(), "사유 문자열이 비어 있다"

    def test_missing_caption_ratio_is_rejected(self):
        ok, reason = validate_stage_config({"event_title": "행사"})
        assert ok is False
        assert reason.strip()


class TestValidateTextLengths:
    """AC: event_title 120자 / event_subtitle 80자 상한."""

    def test_title_at_the_120_char_boundary_passes(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, event_title="가" * 120)
        ok, reason = validate_stage_config(cfg)
        assert ok is True
        assert reason == ""

    def test_title_over_120_chars_is_rejected(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, event_title="가" * 121)
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert reason.strip()

    def test_subtitle_at_the_80_char_boundary_passes(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, event_subtitle="나" * 80)
        ok, reason = validate_stage_config(cfg)
        assert ok is True
        assert reason == ""

    def test_subtitle_over_80_chars_is_rejected(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, event_subtitle="나" * 81)
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert reason.strip()

    def test_non_string_title_is_rejected(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, event_title=123)
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert reason.strip()


class TestValidateStructure:
    """AC: 신뢰 경계 — 최상위 타입과 로고 개수 상한을 검사한다."""

    @pytest.mark.parametrize(
        "cfg", [None, "1/4", [], 7], ids=["none", "str", "list", "int"]
    )
    def test_non_dict_config_is_rejected(self, cfg):
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert reason.strip()

    def test_default_config_is_valid(self):
        ok, reason = validate_stage_config(DEFAULT_STAGE_CONFIG)
        assert ok is True
        assert reason == ""

    def test_twelve_assets_pass_and_thirteen_are_rejected(self):
        """data_model.md: 룸당 로고는 전체 그룹 합산 최대 12개."""
        twelve = dict(
            DEFAULT_STAGE_CONFIG,
            logo_groups=[
                {"label": "주최", "assets": [f"h{i}.png" for i in range(4)]},
                {"label": "주관", "assets": [f"m{i}.png" for i in range(4)]},
                {"label": "후원", "assets": [f"s{i}.png" for i in range(4)]},
            ],
        )
        ok, reason = validate_stage_config(twelve)
        assert ok is True
        assert reason == ""

        thirteen = dict(
            twelve,
            logo_groups=[
                {"label": "주최", "assets": [f"h{i}.png" for i in range(5)]},
                {"label": "주관", "assets": [f"m{i}.png" for i in range(4)]},
                {"label": "후원", "assets": [f"s{i}.png" for i in range(4)]},
            ],
        )
        ok, reason = validate_stage_config(thirteen)
        assert ok is False
        assert reason.strip()

    def test_non_list_logo_groups_is_rejected(self):
        cfg = dict(DEFAULT_STAGE_CONFIG, logo_groups={"주최": []})
        ok, reason = validate_stage_config(cfg)
        assert ok is False
        assert reason.strip()

    def test_normalized_output_is_always_valid(self):
        """normalize → validate 는 항상 통과해야 한다 (두 함수의 계약 일치)."""
        for raw in (None, "{", "[]", {"caption_ratio": "1/2"}, {"event_title": 1}):
            ok, reason = validate_stage_config(normalize_stage_config(raw))
            assert ok is True, f"{raw!r} → {reason}"
