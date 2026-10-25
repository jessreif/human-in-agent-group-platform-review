"""Development-corpus runner (INV-1203/1209).

Converts ``coding.corpus_dev`` cases into ``Subject`` + history pairs, runs the coder
and compares expected-vs-actual field-by-field. Corpus cases never persist: this is a
validation harness, and its metrics must never be reported as real held-out results
(``corpus_kind`` is ``development`` forever — WP12b owns real-gold validation).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Connection

from app.coding.coder import CODER
from app.coding.runs import RunSpec, create_run, finish_run, insert_results, start_run
from app.coding.types import (
    CODE_VERSION,
    VALIDATOR_VERSION,
    Attachment,
    CodedAssertion,
    CodedResult,
    HistoryItem,
    ResultRow,
    Subject,
)
from app.domain.manifests.artifacts import (
    CodingCorpusArtifact,
    CorpusAttachment,
    CorpusCase,
    CorpusHistoryItem,
    ExpectedAssertion,
)
from app.domain.manifests.store import ContentError, ContentStore

CORPUS_KEY = "coding.corpus_dev@v1"


@dataclass(frozen=True)
class CorpusSubjectView:
    case: CorpusCase
    subject_key: str  # "corpus:<artifact_key>:<case_id>"
    subject: Subject
    history: tuple[HistoryItem, ...]


def _attachment(att: CorpusAttachment) -> Attachment:
    return Attachment(
        record_id=att.record_id,
        crate=att.crate,
        location_id=att.location_id,
        event_type=att.event_type,
    )


def _history_item(item: CorpusHistoryItem) -> HistoryItem:
    return HistoryItem(
        seq=item.seq,
        actor=item.actor,
        kind=item.kind,
        scope=item.scope,
        record_id=item.record_id,
        crate=item.crate,
        location_id=item.location_id,
        event_type=item.event_type,
        body=item.body,
    )


def corpus_subjects(artifact: CodingCorpusArtifact) -> tuple[CorpusSubjectView, ...]:
    out: list[CorpusSubjectView] = []
    for case in artifact.body.cases:
        out.append(
            CorpusSubjectView(
                case=case,
                subject_key=f"corpus:{artifact.key}:{case.case_id}",
                subject=Subject(
                    seq=case.subject.seq,
                    actor=case.subject.actor,
                    channel=case.subject.channel,
                    body=case.subject.body,
                    context_crate=case.subject.context_crate,
                    attachments=tuple(_attachment(a) for a in case.subject.attachments),
                ),
                history=tuple(_history_item(h) for h in case.history),
            )
        )
    return tuple(out)


def load_corpus(store: ContentStore | None = None, key: str = CORPUS_KEY) -> CodingCorpusArtifact:
    store = store or ContentStore.load_default()
    stored = store.artifacts.get(key)
    if stored is None or not isinstance(stored.artifact, CodingCorpusArtifact):
        raise ContentError([f"coding corpus {key} not found in content store"])
    return stored.artifact


def _diffs(expected: ExpectedAssertion, actual: CodedAssertion) -> list[str]:
    problems: list[str] = []
    fields = (
        "crate",
        "location_id",
        "event_type",
        "assertion",
        "stance",
        "inference",
        "ambiguous",
        "attribution",
        "temporal_challenge",
        "off_crate",
        "novelty",
        "novel_to_recipient",
    )
    for name in fields:
        want = getattr(expected, name)
        if want is not None and getattr(actual, name) != want:
            problems.append(f"assertion.{name}: expected {want!r}, got {getattr(actual, name)!r}")
    if expected.span_text is not None:
        evidence = [sp.text for sp in actual.spans if sp.kind == "evidence"]
        if expected.span_text not in evidence:
            problems.append(
                f"assertion.span_text: expected evidence span {expected.span_text!r}, "
                f"got {evidence!r}"
            )
    return problems


def evaluate_case(view: CorpusSubjectView, actual: CodedResult) -> list[str]:
    """Expected-vs-actual for one case. Every declared expected field is compared;
    undeclared fields are deliberately unchecked."""
    exp = view.case.expected
    problems: list[str] = []
    if actual.status != exp.status:
        problems.append(f"status: expected {exp.status!r}, got {actual.status!r}")
        return problems  # statuses other than coded have no comparable payload
    if len(actual.assertions) != len(exp.assertions):
        problems.append(f"assertions: expected {len(exp.assertions)}, got {len(actual.assertions)}")
    for i, (want, got) in enumerate(zip(exp.assertions, actual.assertions, strict=False)):
        problems += [f"assertions[{i}].{p}" for p in _diffs(want, got)]
    if len(actual.questions) != exp.n_questions:
        problems.append(f"questions: expected {exp.n_questions}, got {len(actual.questions)}")
    if len(actual.solicitations) != exp.n_solicitations:
        problems.append(
            f"solicitations: expected {exp.n_solicitations}, got {len(actual.solicitations)}"
        )
    got_crates = sorted({c for s in actual.solicitations for c in s.crates})
    if tuple(got_crates) != tuple(sorted(exp.solicitation_crates)):
        problems.append(
            f"solicitation_crates: expected {sorted(exp.solicitation_crates)}, got {got_crates}"
        )
    if (
        exp.acknowledgment_only is not None
        and actual.acknowledgment_only != exp.acknowledgment_only
    ):
        problems.append(
            f"acknowledgment_only: expected {exp.acknowledgment_only}, "
            f"got {actual.acknowledgment_only}"
        )
    return problems


@dataclass(frozen=True)
class CorpusReport:
    corpus_key: str
    n_cases: int
    failures: tuple[tuple[str, tuple[str, ...]], ...]  # (case_id, problems)

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate_corpus(
    artifact: CodingCorpusArtifact,
) -> CorpusReport:
    """Run the deterministic coder over every case and diff against gold."""
    failures: list[tuple[str, tuple[str, ...]]] = []
    for view in corpus_subjects(artifact):
        actual = CODER.code(view.subject, view.history)
        problems = evaluate_case(view, actual)
        if problems:
            failures.append((view.case.case_id, tuple(problems)))
    return CorpusReport(
        corpus_key=artifact.key,
        n_cases=len(artifact.body.cases),
        failures=tuple(failures),
    )


def persist_corpus_run(
    conn: Connection,
    store: ContentStore,
    *,
    corpus_key: str = CORPUS_KEY,
    rubric_key: str = "coding.rubric@v1",
    prompt_key: str = "coding.prompt@v1",
    created_by: str,
) -> uuid.UUID:
    """Code every corpus case and persist one immutable ``coding_run`` + results.

    Binds the corpus artifact body + SHA-256, the rubric and prompt artifact versions,
    the deterministic ``model`` label (``model_digest`` NULL — the model path is
    dormant pending D05), and the code/validator versions (INV-1205)."""
    corpus = load_corpus(store, corpus_key)
    rubric = store.artifacts[rubric_key]
    prompt = store.artifacts[prompt_key]
    spec = RunSpec(
        corpus={
            "kind": "coding_corpus",
            "artifact": corpus.key,
            "sha256": store.artifacts[corpus.key].sha256,
            "body": corpus.body.model_dump(mode="json"),
        },
        corpus_sha256=store.artifacts[corpus.key].sha256,
        rubric_artifact_id=rubric.artifact.artifact_id,
        rubric_version=rubric.artifact.version,
        prompt_artifact_id=prompt.artifact.artifact_id,
        prompt_version=prompt.artifact.version,
        model="deterministic",
        model_digest=None,
        parameters={},
        code_version=CODE_VERSION,
        validator_version=VALIDATOR_VERSION,
        created_by=created_by,
    )
    run_id = create_run(conn, spec)
    start_run(conn, run_id)
    views = corpus_subjects(corpus)
    rows = []
    for view in views:
        result = CODER.code(view.subject, view.history)
        rows.append(
            ResultRow(
                subject_key=view.subject_key,
                status=result.status,
                codes=result.codes,
                adjudication=result.adjudication,
            )
        )
    insert_results(conn, run_id, rows)
    finish_run(conn, run_id, status="completed", expected_subjects=len(views))
    return run_id
