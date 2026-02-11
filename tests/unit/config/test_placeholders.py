"""Unit tests for recursive placeholder resolution."""

from __future__ import annotations

import pytest

from orchid_commons.config.errors import PlaceholderResolutionError
from orchid_commons.config.placeholders import resolve_placeholders


def test_resolve_placeholders_recurses_through_nested_lists_and_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("X", "ok")

    resolved = resolve_placeholders(
        {
            "items": [
                {"v": "${X}"},
                ["${X}", {"nested": "pre-${X}-post"}],
                "${X}",
            ],
            "other": 42,
        }
    )

    assert resolved == {
        "items": [
            {"v": "ok"},
            ["ok", {"nested": "pre-ok-post"}],
            "ok",
        ],
        "other": 42,
    }


def test_resolve_placeholders_reports_nested_path_in_lists() -> None:
    with pytest.raises(PlaceholderResolutionError) as exc_info:
        resolve_placeholders({"items": [{"deep": "${MISSING_ENV}"}]})

    assert "items[0].deep" in str(exc_info.value)


def test_resolve_placeholders_keeps_unresolved_when_non_strict() -> None:
    resolved = resolve_placeholders(
        {"items": [{"deep": "${MISSING_ENV}"}, ["${MISSING_ENV}"]]},
        strict=False,
    )

    assert resolved == {"items": [{"deep": "${MISSING_ENV}"}, ["${MISSING_ENV}"]]}
