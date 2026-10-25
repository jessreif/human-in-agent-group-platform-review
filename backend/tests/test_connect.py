from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.entry import EntryError, Linkage, extract_linkage
from app.settings import ConnectCompletion


@pytest.mark.parametrize(
    "query",
    [
        {"participantId": "P1", "projectId": "PR1", "assignmentId": "A1"},
        {"participant_id": "P1", "project_id": "PR1", "submission_id": "A1"},
        {
            "participantId": " P1 ",
            "participant_id": "P1",
            "projectId": "PR1",
            "submission_id": "A1",
        },
    ],
)
def test_connect_parameter_names_and_legacy_aliases(query: dict[str, str]) -> None:
    assert extract_linkage(query) == Linkage("connect", "P1", "PR1", "A1")


@pytest.mark.parametrize(
    "canonical,legacy",
    [
        ("participantId", "participant_id"),
        ("projectId", "project_id"),
        ("assignmentId", "submission_id"),
    ],
)
def test_conflicting_aliases_are_refused(canonical: str, legacy: str) -> None:
    with pytest.raises(EntryError, match="conflicting_platform_parameters"):
        extract_linkage({"participantId": "P1", canonical: "one", legacy: "two"})


def test_connect_and_prolific_ids_remain_ambiguous() -> None:
    with pytest.raises(EntryError, match="ambiguous_platform_id"):
        extract_linkage({"participantId": "P1", "PROLIFIC_PID": "P2"})


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"code": " "},
        {"code": "<script>"},
        {"url": "https://example.com/participant/project/P1/complete"},
        {"url": "http://connect.cloudresearch.com/participant/project/P1/complete"},
        {"url": "https://connect.cloudresearch.com@evil.test/participant/project/P1/complete"},
        {"url": "https://connect.cloudresearch.com/participant/project/P1/complete?next=evil"},
    ],
)
def test_completion_configuration_rejects_empty_or_unsafe_values(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ConnectCompletion(**values)


def test_completion_supports_code_or_connect_redirect() -> None:
    assert ConnectCompletion(code=" TEST-CODE ").code == "TEST-CODE"
    url = "https://connect.cloudresearch.com/participant/project/P1/complete"
    assert ConnectCompletion(url=url).url == url
