"""Dormant model-assisted coding path (INV-1202; decision D05).

This module is an interface/config point only. No coding run may execute a model until
D05 selects the approved local provider, model, digest and capacity — and even then the
only permitted provider is the loopback-only local adapter (:mod:`app.providers.local`),
with a pinned model digest. External judges, relays and fallbacks can never serve
coding. Human labels must precede model predictions in any validation comparison
(spec §9.4).

``ModelCoder`` therefore refuses construction unless ``d05_approved=True`` is passed by
the caller, and :meth:`ModelCoder.code` fails closed regardless — there is no approved
response schema until the decision lands.
"""

from __future__ import annotations

from app.coding.types import CodedResult, HistoryItem, Subject
from app.domain.manifests.artifacts import CodingPromptArtifact
from app.providers.base import ProviderNotAllowedError, TextProvider


class ModelCodingUnavailableError(RuntimeError):
    """Raised whenever model-assisted coding is attempted before D05 resolves."""


class ModelCoder:
    """Configuration point for the future local-model coder. Dormant by construction."""

    def __init__(
        self,
        provider: TextProvider,
        *,
        prompt: CodingPromptArtifact,
        model: str,
        model_digest: str,
        d05_approved: bool,
    ) -> None:
        if not d05_approved:
            raise ModelCodingUnavailableError(
                "D05 is unresolved: model-assisted coding is dormant; use the deterministic coder"
            )
        if prompt.body.dormant_until != "D05":
            raise ModelCodingUnavailableError("coding prompt artifact is not D05-gated")
        if provider.is_double:
            raise ProviderNotAllowedError("provider doubles can never serve coding")
        self.provider = provider
        self.prompt = prompt
        self.model = model
        self.model_digest = model_digest

    def code(self, subject: Subject, history: tuple[HistoryItem, ...]) -> CodedResult:  # noqa: ARG002 - CodingModel protocol signature
        raise ModelCodingUnavailableError(
            "model-assisted coding is a config point only until D05 approves the "
            "provider/model/digest and the response schema"
        )
