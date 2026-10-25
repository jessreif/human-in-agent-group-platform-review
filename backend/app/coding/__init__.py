"""Offline semantic coding pipeline (WP12a; spec §9.4, INV-1201/1202/1203/1205/1208/1209).

Deterministic, server-authoritative and offline: message-level coding first
(:mod:`app.coding.text`), then history/recipient-novelty derivation
(:mod:`app.coding.history`) over already-visible evidence only. Authenticated record
attachments bypass semantic matching. Ambiguity produces ``abstained`` — never a guess.
Runs and results persist through :mod:`app.coding.runs` into the existing
``coding_run``/``coding_result`` tables; :mod:`app.coding.guard` blocks semantic
products on mixed runs or incomplete coverage. The model-assisted path
(:mod:`app.coding.llm`) is dormant pending D05.
"""

from app.coding.coder import CODER, DeterministicCoder
from app.coding.types import (
    CODE_SCHEMA,
    CODE_VERSION,
    VALIDATOR_VERSION,
    Attachment,
    CodedResult,
    HistoryItem,
    ResultRow,
    Subject,
)

__all__ = [
    "CODER",
    "CODE_SCHEMA",
    "CODE_VERSION",
    "VALIDATOR_VERSION",
    "Attachment",
    "CodedResult",
    "DeterministicCoder",
    "HistoryItem",
    "ResultRow",
    "Subject",
]
