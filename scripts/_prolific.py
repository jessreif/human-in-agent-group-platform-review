"""Stub of the recruitment-platform client.

The deployed client called the recruitment platform's HTTP API with a study
token. That client is not part of this repository. This stub keeps the same
method interface so the operator scripts import cleanly, and it does not take
a token, a study id, or a network client.
"""

from __future__ import annotations

from typing import Any


class RecruitmentClientStub(RuntimeError):
    """Raised when a recruitment-platform call is attempted."""


class Prolific:
    def __init__(self) -> None:
        raise RecruitmentClientStub(
            "The recruitment-platform client is a stub and does not call an "
            "external API."
        )

    def study(self, study_id: str) -> dict[str, Any]:
        raise RecruitmentClientStub(
            "The recruitment-platform client is a stub."
        )

    def submissions(self, study_id: str) -> list[dict[str, Any]]:
        raise RecruitmentClientStub(
            "The recruitment-platform client is a stub."
        )

    def set_places(self, study_id: str, total_available_places: int) -> dict[str, Any]:
        raise RecruitmentClientStub(
            "The recruitment-platform client is a stub."
        )

    def transition(self, study_id: str, action: str) -> dict[str, Any]:
        """Production accepted publish, pause, start, and stop actions."""
        raise RecruitmentClientStub(
            "The recruitment-platform client is a stub."
        )
