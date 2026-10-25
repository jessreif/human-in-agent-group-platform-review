"""AI_QA_ENABLED rules Q&A (2026-10-16).

The optional ``ai_qa`` stage lets participants ask free-text questions about
the Investigators task *rules* between the comprehension check and connecting.
Answers come from the local model through the ``qa_answer`` scheduler job; a
pinned deterministic line stands in whenever the provider is unavailable,
errors, or returns empty/invalid text — the same fallback doctrine as the
teammate/assistant engines (``app.services.engines``).

Scope is honest and narrow: the system prompt is built from the *bound
manifest* (crate count, overlap level, phases, budget, deadline, check-in
presence), so the model can only state rules the session actually runs.
Questions about anything else get the scope-refusal the prompt instructs —
or the deterministic fallback.
"""

from __future__ import annotations

from typing import Any

from app.domain.manifests.binding import BoundManifest

PROMPT_ID = "ai_qa.rules@v1"

# Bounds on the participant-facing affordance — server-enforced, not UI hints.
QUESTION_MIN_CHARS = 1
QUESTION_MAX_CHARS = 1000
ANSWER_MAX_CHARS = 4000
# Questions are optional and bounded: a session may ask up to this many while
# on the page (each exchange is a provider call — the cap is the cost bound).
MAX_EXCHANGES = 5
# The answering call gets a shorter budget than teammate replies — a rules
# answer is two or three sentences, not a grounded intel reply.
TIMEOUT_MS = 20_000
MAX_TOKENS = 300

FALLBACK_ANSWER = (
    "I'm not able to answer that one. I can only answer questions about how "
    "the Investigators task works — for example, how to ask your teammates "
    "for intel, share records, or submit your hideout answers. You can also "
    "check the instructions you just read, or press Continue to move on."
)

# The model must ground answers in this list — nothing else is in scope.
_RULES_CORE = """\
- You and two teammates work together to solve a shipment-smuggling case. \
Teammates are introduced when you connect — do not name or invent them.
- There are {n_crates} crates of merchandise; each is hidden at exactly \
one hideout.
- Your job is to pick the correct hideout for each of your {n_assigned} \
assigned crates before the deadline.
- Your personal case file lists intel records only you can see at first. \
Your teammates hold different records.
- You can ask teammates for intel about a crate in chat, and share \
records from your file so the whole team sees them.
{overlap_line}{solo_line}{assistant_line}- The task timer shows how much \
working time is left. When time runs out, your answers lock.
{checkin_line}- After the task there are a few short questionnaire \
pages, a debrief, and a data-use choice before you finish.
"""

_OVERLAP_LINES = {
    "independent": "- Each teammate is assigned their own set of crates.\n",
    "none": "- Each teammate is assigned their own set of crates — "
    "no two people share an assignment.\n",
    "some": "- Some crates are assigned to more than one teammate, "
    "so teammates may hold intel on crates you also have.\n",
    "full": "- All teammates work on the same shared set of crates.\n",
}

_SOLO_LINE = (
    "- Before working with the team, you get a short solo review "
    "to read your case file on your own.\n"
)
_ASSISTANT_LINE = (
    "- A case assistant is available to summarize records and help you think through clues.\n"
)
_CHECKIN_LINE = (
    "- Partway through, a short check-in pop-up asks how rushed you feel; "
    "answer it to keep working.\n"
)


def rules_system_prompt(
    bound: BoundManifest,
    *,
    ai_assistant: bool = True,
    time_pressure: bool = True,
) -> str:
    """The scoped system prompt for one session's rules Q&A — built from the
    bound manifest so the model can only state rules the session runs."""
    task = bound.manifest.task
    overlap = task.overlap
    budget_min = bound.manifest.timing.active_budget_s / 60
    has_solo = any(ph.name == "solo_review" for ph in bound.manifest.channels.phases)
    has_checkin = time_pressure and bound.manifest.timing.checkin is not None
    body = _RULES_CORE.format(
        n_crates=task.n_crates,
        n_assigned=task.n_assigned,
        overlap_line=_OVERLAP_LINES[overlap],
        solo_line=_SOLO_LINE if has_solo else "",
        assistant_line=_ASSISTANT_LINE if ai_assistant else "",
        checkin_line=_CHECKIN_LINE if has_checkin else "",
    )
    return (
        "You answer questions about the rules of the 'Investigators' research task "
        "a participant is about to do. Answer ONLY questions about how the task "
        "works — the rules below are the complete and only source of truth.\n\n"
        "TASK RULES\n" + body + "\n"
        f"The task lasts {budget_min:g} minutes of working time.\n\n"
        "INSTRUCTIONS\n"
        "- Answer in at most three short sentences, plainly.\n"
        "- Never invent rules, records, crates or hideouts that are not listed above.\n"
        "- If the question is not about the task rules (payment, the study design, "
        "personal topics, or anything else), say briefly that you can only answer "
        "questions about how the task works.\n"
        "- Do not discuss the AI teammates' nature, prompts, or this study's design."
    )


def clean_question(text: Any) -> str | None:
    """Normalize a submitted question; ``None`` rejects malformed/empty input."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not (QUESTION_MIN_CHARS <= len(cleaned) <= QUESTION_MAX_CHARS):
        return None
    return cleaned


def clean_answer(text: Any) -> str | None:
    """Normalize model output; ``None`` maps to the deterministic fallback."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned or len(cleaned) > ANSWER_MAX_CHARS:
        return None
    return cleaned
