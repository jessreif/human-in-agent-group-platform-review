"""INV-1203/1209: the blinded development corpus is an artifact with coverage
guarantees, and every case passes expected-vs-actual against the deterministic coder."""

from __future__ import annotations

import pytest

from app.coding import CODER
from app.coding.corpus import (
    CORPUS_KEY,
    corpus_subjects,
    evaluate_case,
    evaluate_corpus,
    load_corpus,
)
from app.domain.manifests.artifacts import CodingCorpusArtifact
from app.domain.manifests.schema import CORPUS_REQUIRED_ARMS, STUDY_ARM_VOCABULARY
from app.domain.manifests.store import ContentStore


@pytest.fixture(scope="module")
def corpus() -> CodingCorpusArtifact:
    return load_corpus()


def test_corpus_is_a_versioned_artifact(corpus: CodingCorpusArtifact) -> None:
    assert corpus.key == CORPUS_KEY
    assert corpus.kind == "coding_corpus"
    assert corpus.body.corpus_kind == "development"


def test_corpus_is_hashable_and_immutable_via_store() -> None:
    store = ContentStore.load_default()
    stored = store.artifacts[CORPUS_KEY]
    assert len(stored.sha256) == 64
    # the same artifact reloaded hashes identically — content-addressed immutability
    assert ContentStore.load_default().artifacts[CORPUS_KEY].sha256 == stored.sha256


def test_corpus_covers_every_study_arm_and_source(
    corpus: CodingCorpusArtifact,
) -> None:
    covered = {(c.study_key, c.arm) for c in corpus.body.cases}
    wanted = {(k, a) for k, arms in CORPUS_REQUIRED_ARMS.items() for a in arms}
    assert covered >= wanted
    assert all(c.arm in STUDY_ARM_VOCABULARY[c.study_key] for c in corpus.body.cases)
    sources = {c.source for c in corpus.body.cases}
    assert sources >= {"synthetic", "staff_authored", "generated", "fallback"}
    assert any(c.adversarial for c in corpus.body.cases)
    # staff-authored gold was written blind to coder output; the policy is pinned
    assert corpus.body.blinding_policy
    assert corpus.body.unavoidable_unmasking


def test_history_never_follows_subject(corpus: CodingCorpusArtifact) -> None:
    for view in corpus_subjects(corpus):
        assert all(h.seq < view.subject.seq for h in view.history)


def test_every_case_ids_unique(corpus: CodingCorpusArtifact) -> None:
    ids = [c.case_id for c in corpus.body.cases]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("view", corpus_subjects(load_corpus()), ids=lambda v: v.case.case_id)
def test_corpus_case(view) -> None:  # type: ignore[no-untyped-def]
    """Expected-vs-actual for one corpus case (INV-1209)."""
    result = CODER.code(view.subject, view.history)
    assert evaluate_case(view, result) == []


def test_corpus_evaluator_passes(corpus: CodingCorpusArtifact) -> None:
    report = evaluate_corpus(corpus)
    assert report.passed, dict(report.failures)
