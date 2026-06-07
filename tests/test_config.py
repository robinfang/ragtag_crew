from __future__ import annotations

from ragtag_crew.config import Settings


def test_verify_defaults_to_enabled_with_lint_and_tests() -> None:
    assert Settings.model_fields["verify_enabled"].default is True
    assert (
        Settings.model_fields["verify_commands"].default
        == "ruff check . && pytest tests/ -x -q"
    )
