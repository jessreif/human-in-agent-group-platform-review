/**
 * Non-workbench stage screens (WP09: INV-0901..0908).
 *
 * Participant-visible copy is bound through the manifest: consent/training/debrief
 * screens render the served ``material_reference`` descriptor (key + body) and PE
 * exposure surfaces come from the served ``exposure`` projection — nothing here keys
 * copy off the arm. The *text itself* is interim reference copy anchored to the bound
 * artifacts' ``reference_copy`` provenance (the demo screens); the frozen wording is a
 * D06 publication step and the artifacts carry ``unresolved_placeholder``/``reference``
 * status until then.
 */
import { Fragment, useRef, useState } from "react";

import { displaySeat, youLabel } from "../state/names";
import { ApiError } from "../api/client";
import { canWrite, type SessionStore, type StoreState } from "../state/store";
import type { TelemetryQueue } from "../telemetry";
import type {
  ConsentAction,
  InstrumentItem,
  InstrumentProjection,
} from "../api/types";

/** PE exposure copy is stored as demo-HTML markup; render its text only — raw markup
 * is never injected into the page (spec §11 sanitization rule). */
export function surfaceText(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
}

function Panel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <main className="panel">
      <h1>{title}</h1>
      {children}
    </main>
  );
}

/**
 * Busy-state helper. The returned ``error`` is a participant-visible, non-technical
 * failure note — a failed action must never look inert (BA-44): the control stays
 * retryable and the message says so.
 */
export function actionErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.kind === "batch_at_capacity")
      return "All study places are currently reserved. Please return to the study platform.";
    if (error.kind === "batch_closed")
      return "This study is no longer accepting new participants. Please return to the study platform.";
    if (error.kind === "quota_configuration_changed")
      return "This study is temporarily unavailable. Please contact the research team.";
    if (error.kind === "instrument_incomplete")
      return "Please answer all questions on this page before continuing.";
    if (error.kind === "practice_share_required")
      return "Please return to practice and share one intelligence record before continuing.";
    if (error.kind === "practice_pick_required")
      return "Please return to practice and pick a location for the practice crate before continuing.";
    if (error.kind === "comprehension_required")
      return "Please answer the comprehension questions correctly before continuing.";
    if (error.kind === "writer_lease_held")
      return "This session is active in another tab. Return to that tab or close it and refresh this page.";
    if (error.kind === "stale_revision")
      return "Your session is syncing. Please wait a moment and try again.";
  }
  return fallback;
}

function useBusy(
  failureNote = "We couldn't save your response. Please check your connection and try again.",
): [boolean, (fn: () => Promise<unknown>) => void, string | null] {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return [
    busy,
    (fn) => {
      setBusy(true);
      setError(null);
      void fn()
        .catch((error: unknown) =>
          setError(actionErrorMessage(error, failureNote)),
        )
        .finally(() => setBusy(false));
    },
    error,
  ];
}

export function ConsentScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run] = useBusy();
  // BA-37: the rendered copy IS the bound artifact's published body — no component
  // literals. The recorded text version is the *bound* artifact key.
  const text = state.material?.body?.text;
  const textVersion = state.material?.artifact_key ?? "unbound";
  const consent = (action: ConsentAction) =>
    run(() => store.dispatch("consent", { action, text_version: textVersion }));
  return (
    <main className="panel consent-panel">
      <h1>{text?.heading ?? "Team Decision-Making Study"}</h1>
      {text?.subheading ? <p className="consent-sub">{text.subheading}</p> : null}
      <div className="consent-copy">
        {text ? (
          text.sections.map((section, i) => (
            <section key={i}>
              {section.title ? <h2>{section.title}</h2> : null}
              {section.paragraphs.map((paragraph, j) => (
                <p key={j}>{paragraph}</p>
              ))}
            </section>
          ))
        ) : (
          // Placeholder-bound sessions (older pinned manifests) get no fabricated copy —
          // only the bound artifact's provenance is shown.
          <p role="alert">
            The consent text bound to this session ({textVersion}) is a reference pointer,
            not the approved form. Please do not continue; contact the research team.
          </p>
        )}
      </div>
      {text ? (
        <>
          <p className="consent-question">
            {text.question ?? "Would you like to participate?"}
          </p>
          <div className="btn-row">
            <button
              type="button"
              className="primary"
              disabled={busy || !canWrite(state)}
              onClick={() => consent("consented")}
            >
              {text.accept_label ?? "Yes, I consent to participate"}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy || !canWrite(state)}
              onClick={() => consent("declined")}
            >
              {text.decline_label ?? "No, I do not wish to participate"}
            </button>
          </div>
        </>
      ) : null}
    </main>
  );
}

/**
 * Paged orientation (UX round 2, item 2): the instructions stage is a two-page
 * walkthrough — case background + initials entry first, then a workspace tour.
 * Initials are stored server-side (``set_initials``) so every participant-facing
 * surface can name the participant consistently.
 */
// 2026-10-15 crate-condition experiment: crate counts in participant copy are
// spelled to match the legacy "Eight crates" register; the bound task shape
// falls back to the 8-crate independent deal when the server omits it.
const CRATE_WORDS: Record<number, string> = { 4: "four", 8: "eight", 12: "twelve" };

function crateWord(n: number, capitalized = false): string {
  const w = CRATE_WORDS[n] ?? String(n);
  return capitalized ? w.charAt(0).toUpperCase() + w.slice(1) : w;
}

export function AssignmentRule({ shape }: { shape: StoreState["taskShape"] }) {
  const overlap = shape?.crateOverlap ?? "independent";
  const nCrates = shape?.nCrates ?? 8;
  if (overlap === "full")
    return <>Each teammate will be <b>assigned to work on all four crates</b>.</>;
  if (overlap === "none" || overlap === "some")
    return <>
      Each teammate has been <b>assigned four of the {crateWord(nCrates)} crates</b> to work on.{" "}
      {overlap === "none"
        ? <>There will be <b>no overlaps</b> in assignment.</>
        : <>There will be <b>some overlap</b> in your assignments.</>}
    </>;
  return <>Each teammate has been <b>privately assigned four of the eight crates</b> to work on. Assignments can overlap.</>;
}

export function InstructionsScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run] = useBusy();
  const [page, setPage] = useState(0);
  const [initials, setInitials] = useState(state.initials ?? "");
  const [initialsError, setInitialsError] = useState<string | null>(null);
  const intro = state.exposure?.intro ? surfaceText(state.exposure.intro) : null;
  const callout = state.exposure?.callout ? surfaceText(state.exposure.callout) : null;
  const aiAssistant = state.aiAssistant;
  const shape = state.taskShape;
  const nCrates = shape?.nCrates ?? 8;
  const overlap = shape?.crateOverlap ?? "independent";
  const minutes =
    state.clock?.budgetMs != null ? Math.round(state.clock.budgetMs / 60000) : null;

  if (page === 0) {
    return (
      <Panel title="Team-based investigation game">
        <p>
          For this study, you will be one of three investigators working together on a
          shared case.
        </p>
        <h2>Case background</h2>
        <p>
          {crateWord(nCrates, true)} crates of high-value merchandise have been stolen.
          The thieves are moving the crates between six known hideouts. As a team, your
          goal is to identify the locations of all {crateWord(nCrates)} crates.
        </p>
        <p>
          You and your teammates will each receive time-stamped intelligence that will
          help you solve the mystery. You can choose to share intelligence from your
          case file in the team chat if you want. You can also ask your teammates to
          share intelligence with you.
        </p>
        {/* 2026-10-15 audit F05: locked owner copy verbatim per overlap level
            — the "will not be told…" clause is superseded under the disclosed
            levels, and `some` is a guaranteed overlap, never "may". */}
        {overlap === "none" ? (
          <p>
            <AssignmentRule shape={shape} /> Your crates will be assigned
            after a brief training. Your file may contain information about crates
            outside your assignment, and that information may be valuable to a
            teammate.
          </p>
        ) : overlap === "some" ? (
          <p>
            <AssignmentRule shape={shape} /> Your crates will be
            assigned after a brief training. Your file may contain information about
            crates outside your assignment, and that information may be valuable to a
            teammate.
          </p>
        ) : overlap === "full" ? (
          <p>
            <AssignmentRule shape={shape} /> Your
            crates will be assigned after a brief training.
          </p>
        ) : (
          <p>
            <AssignmentRule shape={shape} /> Your crates will be assigned after a
            brief training; you will not be told which crates your teammates have been
            assigned. Your file may contain information about crates outside your
            assignment, and that information may be valuable to a teammate.
          </p>
        )}
        {intro ? <p data-surface="intro">{intro}</p> : null}
        <p>
          Your task will be to locate each of the assigned crates
          {state.bonusPayment
            ? ", and as noted, you are eligible to receive a bonus based on your performance"
            : ""}
          . Next, you will review your workspace.
        </p>
        <div className="initials-block">
          <label htmlFor="initials">
            You will be identified to your team by your initials.
          </label>
          <input
            id="initials"
            autoComplete="off"
            maxLength={3}
            value={initials}
            onChange={(e) => {
              setInitials(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""));
              setInitialsError(null);
            }}
          />
          {initialsError ? (
            <p className="error-note" role="alert">
              {initialsError}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          className="primary"
          disabled={busy || !canWrite(state)}
          onClick={() => {
            if (!initials.trim()) {
              setInitialsError("Enter your initials to continue.");
              return;
            }
            run(async () => {
              try {
                await store.dispatch("set_initials", { initials: initials.trim() });
                setPage(1);
              } catch {
                setInitialsError("Those initials could not be saved — try again.");
              }
            });
          }}
        >
          Continue to your workspace
        </button>
      </Panel>
    );
  }

  return (
    <Panel title="Your workspace">
      <p>
        During the task you will see one screen with{" "}
        {aiAssistant ? "three" : "two"} panes and an answer bar:
      </p>
      <div
        className={aiAssistant ? "workspace-diagram" : "workspace-diagram no-assistant"}
        aria-hidden="true"
      >
        <div className="wd-case">Case file</div>
        <div className="wd-chat">Team chat</div>
        {aiAssistant ? <div className="wd-ai">AI assistant</div> : null}
        <div className="wd-answers">Submit a location for each of your crates</div>
      </div>
      <ul>
        <li>
          You will have{" "}
          <b>{minutes !== null ? `${minutes} minutes` : "a limited amount of time"}</b>{" "}
          to locate as many of the crates as you can.
        </li>
        <li>You can drag entries in your case file to reorder them.</li>
        <li>
          {overlap === "full"
            ? "Everyone works on the same four crates."
            : "Everyone has a private four-crate assignment."}
        </li>
        {state.structuredRequests ? (
          <li>
            Ask teammates for intel with the <b>Ask the team about…</b> dropdown in
            the team chat — or type your own message. Share intel that may help
            them too.
          </li>
        ) : (
          <li>
            Use the team chat to ask teammates for intel and to share information
            that may help them.
          </li>
        )}
        {state.soloReviewSeconds ? (
          <li>
            You start with a short solo review of your case file —{" "}
            {aiAssistant
              ? "the team chat and assistant open after it."
              : "the team chat opens after it."}
          </li>
        ) : null}
        <li>
          You can share an intel entry from your case file in the team chat. Anything
          you share in the chat will be visible to both of your teammates.
        </li>
        {aiAssistant ? (
          <li>
            The AI assistant can read your case file, including any intel your
            teammates share with you from their case files in the chat.
          </li>
        ) : null}
        {/* UX_Feedback_v9 item 1: bolded final bullet above Continue — the
            freshness rule restated in goal terms. */}
        <li>
          <b>
            All intelligence is accurate as of the time it was recorded, but a
            crate may have changed locations later. Your goal is to identify
            where the crate is now.
          </b>
        </li>
      </ul>
      {callout ? (
        <p className="callout" data-surface="callout">
          {callout}
        </p>
      ) : null}
      <button
        type="button"
        className="primary"
        disabled={busy || !canWrite(state)}
        onClick={() => run(() => store.dispatch("training_step", { step: "instructions" }))}
      >
        Continue
      </button>
    </Panel>
  );
}

/**
 * Paged try-it practice (UX round 2, item 3): the all-at-once practice workspace is
 * replaced by two guided pages — page 1 covers sharing an entry and previews the
 * team chat; page 2 previews the assistant (answer picking practice was dropped in
 * UX round 3). UX round 4: only one share is practised, no teammate card sits in
 * the practice file. UX_Feedback_v9 (2026-10-12): the typed intel ask is
 * optional practice — its canned reply card still shows what a share looks
 * like (never model text), but only the share gates Continue after a pilot
 * participant dead-ended on the combined requirement. The
 * fixed practice world is still a separate, read-only projection, and every
 * interaction is recorded as a ``training_step`` command — the practice event
 * namespace — so nothing touches analytic state.
 */
interface PracticeMsg {
  who: string;
  text: string;
  mine?: boolean;
  recordId?: string;
  card?: { crate: number; stamp: string; body: string; sharedBy?: string };
}

// UX round 3: the "N actions left" footer is gone (item 1) and answer-recording
// practice is dropped (item 3) — Continue still gates on each page's actions.
// UX_Feedback_v9 item 3 (2026-10-12): page 1 gates on the share only — the
// typed ask stays available as optional practice (a canned card still
// returns) but no longer blocks Continue; a pilot participant dead-ended
// here treating the disabled button as an error.
// v14 (2026-10-16, owner direction): page 1 also gates on one answer-entry
// pick — the mechanic that produces the DV was the one never rehearsed
// (UX round 3 dropped it; the v13 zero-answer cohort reinstated it). The pick
// writes a ``training_step`` (action="pick_location") to the practice
// namespace, credited only on an accepted receipt like the share.
const PRACTICE_PAGE1 = ["share", "pick_location"];
const PRACTICE_PAGE2: string[] = [];

// Comprehension check (2026-10-10): the last training page is a six-item
// (2026-10-16: five originals + record_before_timer)
// true/false gate before "Connecting you to your team". The item ids are sent
// to the server, which holds the answer key — a committed zero-miss
// comprehension event is required before `complete` may advance the stage.
// Attempts may be retried; `result.missed` carries the wrong item ids back for
// highlighting. The canonical enabled-state list is asserted verbatim by the
// backend contract test — flag-off sessions filter the rendered list below.
const COMPREHENSION_ITEMS: { id: string; text: string }[] = [
  {
    id: "partial_intel",
    text: "Every team member receives some, but not all, available intelligence.",
  },
  {
    id: "same_crates",
    text: "Every team member is working on the exact same crates.",
  },
  {
    id: "accurate_not_latest",
    text: "The intelligence in my case file is accurate, but may not be the latest available.",
  },
  {
    id: "teammates_may_have_mine",
    text: "My teammates may have intelligence about my assigned crates.",
  },
  {
    id: "assistant_reads_team",
    text: "The AI assistant has access to my teammates’ case files.",
  },
  // v14 (2026-10-16): answer-entry task-model probe — the v13 zero-answer
  // cohort never discovered where picks land. Unconditional (every build
  // freezes answers at the deadline); the backend key carries the same item.
  {
    id: "record_before_timer",
    text: "I record each of my crate locations before the timer expires.",
  },
];

// AI_ASSISTANT_ENABLED=0: the assistant item is never presented or scored — the
// backend comprehension key drops it under the same flag, so the client omits
// it from the rendered list rather than only hiding it.
const ASSISTANT_COMPREHENSION_ITEM = "assistant_reads_team";

export function TrainingScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run, runError] = useBusy(
    "We couldn't submit your comprehension answers. Please check your connection and try again.",
  );
  const [page, setPage] = useState(1);
  const [done, setDone] = useState<ReadonlySet<string>>(new Set());
  const [chat, setChat] = useState<PracticeMsg[]>([]);
  const [sharedIds, setSharedIds] = useState<ReadonlySet<string>>(new Set());
  const [sharePending, setSharePending] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);
  const [pickPending, setPickPending] = useState(false);
  const [pickError, setPickError] = useState<string | null>(null);
  const [pickedLocation, setPickedLocation] = useState<string | null>(null);
  const [askText, setAskText] = useState("");
  const [askPending, setAskPending] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const [missed, setMissed] = useState<ReadonlySet<string>>(new Set());
  const practice = state.practice;
  const writable = canWrite(state);
  const you = youLabel(state.initials);
  // AI_ASSISTANT_ENABLED=0: the assistant practice page is skipped entirely and
  // the comprehension page follows the team-chat practice page directly.
  const aiAssistant = state.aiAssistant;
  const comprehensionItems = aiAssistant
    ? COMPREHENSION_ITEMS
    : COMPREHENSION_ITEMS.filter((q) => q.id !== ASSISTANT_COMPREHENSION_ITEM);

  if (!practice) {
    return (
      <Panel title="Practice">
        <p role="status">Preparing the practice workspace…</p>
      </Panel>
    );
  }

  const shared = practice.shared;
  const sayChat = (m: PracticeMsg) => setChat((l) => [...l, m]);

  // BA-95: the one required practice action is credited only after an accepted
  // command receipt — a failed or rejected dispatch must never look like a
  // share, and the server independently refuses `complete` without one.
  const share = async (r: { record_id: string; crate: number; body: string }) => {
    if (sharedIds.has(r.record_id) || sharePending) return;
    setSharePending(true);
    setShareError(null);
    try {
      const res = await store.dispatch("training_step", {
        step: "practice",
        action: "share",
        record_id: r.record_id,
      });
      if (!res.accepted) {
        setShareError("That share was not accepted — please try again.");
        return;
      }
      setDone((d) => new Set(d).add("share"));
      setSharedIds((s) => new Set(s).add(r.record_id));
      sayChat({
        who: you,
        mine: true,
        text: `Sharing intel on Crate ${r.crate}.`,
        recordId: r.record_id,
      });
    } catch {
      setShareError(
        "That share did not go through — check your connection and try again.",
      );
    } finally {
      setSharePending(false);
    }
  };

  // The typed ask is optional practice (UX_Feedback_v9) — still credited
  // only on an accepted receipt; invalid text is rejected server-side with
  // `practice_ask_invalid`. Only the share gates completion.
  const ask = async () => {
    const text = askText.trim();
    if (!text || askPending) return;
    setAskPending(true);
    setAskError(null);
    try {
      const res = await store.dispatch("training_step", {
        step: "practice",
        action: "ask",
        text,
      });
      if (!res.accepted) {
        setAskError(
          "That doesn't look like an intel ask — try something like “anything on crate 3?”",
        );
        return;
      }
      const card = res.result?.card as
        | { crate: number; stamp: string; body: string; shared_by?: string }
        | undefined;
      sayChat({ who: you, mine: true, text });
      if (card) {
        sayChat({
          who: `${card.shared_by ?? "Teammate"} (practice)`,
          text: `Sharing intel on Crate ${card.crate}`,
          card: { crate: card.crate, stamp: card.stamp, body: card.body },
        });
      }
      setDone((d) => new Set(d).add("ask"));
      setAskText("");
    } catch {
      setAskError("That ask did not go through — check your connection and try again.");
    } finally {
      setAskPending(false);
    }
  };

  // v14: the answer-entry pick rehearses the DV mechanic — one committed pick
  // for the practice crate, credited only on an accepted receipt (the same
  // BA-95 rule as the share; the server refuses `complete` without it).
  const pickCrate = practice.records[0]?.crate ?? 3;
  const pick = async (location: string) => {
    if (!location || pickPending || pickedLocation) return;
    setPickPending(true);
    setPickError(null);
    try {
      const res = await store.dispatch("training_step", {
        step: "practice",
        action: "pick_location",
        crate: pickCrate,
        location,
      });
      if (!res.accepted) {
        setPickError("That pick was not accepted — please try again.");
        return;
      }
      setDone((d) => new Set(d).add("pick_location"));
      setPickedLocation(location);
    } catch {
      setPickError(
        "That pick did not go through — check your connection and try again.",
      );
    } finally {
      setPickPending(false);
    }
  };

  // BA-106: the Continue gate follows the bound training version, not the
  // build — ``training.reference@v5`` sessions must also complete the ask;
  // v6+ (UX_Feedback_v9) gates on the share alone. v14 adds the pick to every
  // page-1 path.
  const askRequired = practice.ask_required === true;
  const pageActions =
    page === 1
      ? !state.structuredRequests && askRequired
        ? ["share", "ask", "pick_location"]
        : PRACTICE_PAGE1
      : PRACTICE_PAGE2;
  const pageDone = pageActions.every((a) => done.has(a));

  if (page === 1) {
    return (
      <Panel title="Practice: Sharing intel with your team">
        <p>
          Try sharing an entry with your team below. The team chat on the right is
          a preview of what it looks like during the task.
        </p>
        <section aria-label="Share intel from your file">
          <h2>Share intel from your file</h2>
          <p className="pane-hint">
            Each entry has a Share button. You can also drag an entry into the team
            chat below.
          </p>
          <ul className="files-list" aria-label="Practice case file records">
            {practice.records.map((r) => (
              <li
                key={r.record_id}
                className="file"
                data-crate={r.crate}
                aria-label={`Crate ${r.crate} — ${r.stamp} — ${r.body}`}
                draggable={writable}
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/record-id", r.record_id);
                  e.dataTransfer.effectAllowed = "copyMove";
                }}
              >
                <div className="file-main">
                  <div className="file-head">
                    <span className="file-stamp">{r.stamp}</span>
                    <span className="file-crate">Crate {r.crate}</span>
                  </div>
                  <div className="file-body">{r.body}</div>
                </div>
                <div className="file-actions">
                  {sharedIds.has(r.record_id) ? (
                    <span className="shared-tag">Shared in team chat</span>
                  ) : (
                    <button
                      type="button"
                      disabled={!writable || sharePending}
                      onClick={() => void share(r)}
                    >
                      {sharePending ? "Sharing…" : "Share"}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section
          aria-label="Team chat"
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("text/record-id")) e.preventDefault();
          }}
          onDrop={(e) => {
            const recordId = e.dataTransfer.getData("text/record-id");
            const rec = practice.records.find((r) => r.record_id === recordId);
            if (rec) {
              e.preventDefault();
              void share(rec);
            }
          }}
        >
          <h2>Team chat</h2>
          <ul className="msgs" aria-label="Practice team messages">
            {shared ? (
              <>
                <li className="msg">
                  <span className="who">Teammate (practice)</span>
                  <span className="msg-body">Sharing intel on Crate {shared.crate}</span>
                </li>
                <li className="intel-card">
                  <span className="filepill">
                    <span className="fs">{shared.stamp}</span>
                    {shared.body}
                    <span className="file-source"> — in your case file</span>
                  </span>
                </li>
              </>
            ) : null}
            {chat.map((m, i) => (
              <Fragment key={i}>
                <li className={m.mine ? "msg mine" : "msg"}>
                  <span className="who">{m.who}</span>
                  <span className="msg-body">{m.text}</span>
                </li>
                {m.recordId ? (
                  <li className={m.mine ? "intel-card mine" : "intel-card"}>
                    <span className="filepill">
                      {practice.records.find((r) => r.record_id === m.recordId)?.body}
                    </span>
                  </li>
                ) : null}
                {m.card ? (
                  <li className="intel-card">
                    <span className="filepill">
                      <span className="file-crate">Crate {m.card.crate}</span>{" "}
                      <span className="fs">{m.card.stamp}</span> {m.card.body}
                      <span className="file-source"> — in your case file</span>
                    </span>
                  </li>
                ) : null}
              </Fragment>
            ))}
          </ul>
          <p className="pane-hint">
            This is what the team chat looks like during the task. When a
            teammate shares intel here, it appears in your case file
            automatically with a “Shared by” tag.
          </p>
          {shareError ? (
            <p className="error-note" role="alert">
              {shareError}
            </p>
          ) : null}
          {state.structuredRequests ? (
            <div className="team-actions">
              <label className="visually-hidden" htmlFor="practice-ask-crate">
                Ask the team for intel on a crate
              </label>
              <select id="practice-ask-crate" disabled defaultValue="">
                <option value="">Ask the team about…</option>
                {state.assignedCrates.map((n) => (
                  <option key={n} value={n}>
                    Crate {n}
                  </option>
                ))}
              </select>
              <button type="button" disabled>
                Ask
              </button>
            </div>
          ) : null}
          {state.structuredRequests ? (
            <>
              <div className="composer">
                <label className="visually-hidden" htmlFor="practice-team-input">
                  Message the team
                </label>
                <input
                  id="practice-team-input"
                  type="text"
                  disabled
                  placeholder="Message the team"
                  readOnly
                />
                <button type="button" disabled>
                  Send
                </button>
              </div>
              <p className="pane-hint preview-note" role="note">
                Preview only — these controls unlock when the task begins.
              </p>
            </>
          ) : askRequired ? (
            <>
              <p className="pane-hint">
                Now try asking the team for intel. Type a question naming a crate —
                for example “anything on crate 3?” — and send it. A teammate will
                answer with a card like the ones above.
              </p>
              <div className="composer">
                <label className="visually-hidden" htmlFor="practice-team-input">
                  Ask the team for intel
                </label>
                <input
                  id="practice-team-input"
                  type="text"
                  disabled={!writable || askPending || done.has("ask")}
                  placeholder='Try: "anything on crate 3?"'
                  value={askText}
                  onChange={(e) => setAskText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void ask();
                    }
                  }}
                />
                <button
                  type="button"
                  disabled={!writable || askPending || done.has("ask") || !askText.trim()}
                  onClick={() => void ask()}
                >
                  {askPending ? "Sending…" : done.has("ask") ? "Asked" : "Send"}
                </button>
              </div>
              {askError ? (
                <p className="error-note" role="alert">
                  {askError}
                </p>
              ) : null}
            </>
          ) : (
            <>
              <div className="composer">
                <label className="visually-hidden" htmlFor="practice-team-input">
                  Ask the team for intel
                </label>
                <input
                  id="practice-team-input"
                  type="text"
                  disabled
                  placeholder='Try: "anything on crate 3?"'
                  readOnly
                />
                <button type="button" disabled>
                  Send
                </button>
              </div>
              <p className="pane-hint preview-note" role="note">
                Preview only — these controls unlock when the task begins.
              </p>
            </>
          )}
        </section>
        <section aria-label="Record a practice location">
          <h2>Record a crate location</h2>
          <p className="pane-hint">
            During the task, a bar at the bottom of the screen holds a picker for
            each of your crates. Try it here — pick a location for practice Crate{" "}
            {pickCrate}.
          </p>
          {pickedLocation ? (
            <p className="shared-tag" role="status">
              Location saved for Crate {pickCrate} — {pickedLocation} (practice)
            </p>
          ) : (
            <div className="composer">
              <label className="visually-hidden" htmlFor="practice-pick-location">
                Pick a location for practice Crate {pickCrate}
              </label>
              <select
                id="practice-pick-location"
                disabled={!writable || pickPending}
                defaultValue=""
                onChange={(e) => void pick(e.target.value)}
              >
                <option value="" disabled>
                  — choose a location —
                </option>
                {practice.locations.map((loc) => (
                  <option key={loc} value={loc}>
                    {loc}
                  </option>
                ))}
              </select>
            </div>
          )}
          {pickError ? (
            <p className="error-note" role="alert">
              {pickError}
            </p>
          ) : null}
        </section>
        <button
          type="button"
          className="primary"
          disabled={!pageDone}
          onClick={() => setPage(aiAssistant ? 2 : 3)}
        >
          {pageDone ? "Continue" : "Complete the practice steps to Continue"}
        </button>
      </Panel>
    );
  }

  if (page === 3 || (page === 2 && !aiAssistant)) {
    const allAnswered = comprehensionItems.every(
      (q) => answers[q.id] !== undefined,
    );
    const answer = (id: string, value: boolean) => {
      setAnswers((a) => ({ ...a, [id]: value }));
      setMissed((m) => {
        if (!m.has(id)) return m;
        const n = new Set(m);
        n.delete(id);
        return n;
      });
    };
    return (
      <Panel title="Comprehension check">
        <p>
          To confirm that you understand the task, please answer the following
          True or False questions.
        </p>
        <ul className="comprehension-list">
          {comprehensionItems.map((q) => (
            <li key={q.id} className={missed.has(q.id) ? "missed" : undefined}>
              <fieldset>
                <legend>{q.text}</legend>
                <label>
                  <input
                    type="radio"
                    name={`comprehension-${q.id}`}
                    checked={answers[q.id] === true}
                    disabled={!writable}
                    aria-invalid={missed.has(q.id) || undefined}
                    onChange={() => answer(q.id, true)}
                  />
                  True
                </label>
                <label>
                  <input
                    type="radio"
                    name={`comprehension-${q.id}`}
                    checked={answers[q.id] === false}
                    disabled={!writable}
                    aria-invalid={missed.has(q.id) || undefined}
                    onChange={() => answer(q.id, false)}
                  />
                  False
                </label>
              </fieldset>
            </li>
          ))}
        </ul>
        {missed.size > 0 ? (
          <p className="error-note" role="alert">
            Some answers are incorrect — review the highlighted items and try
            again.
          </p>
        ) : null}
        {runError ? (
          <p className="error-note" role="alert">
            {runError}
          </p>
        ) : null}
        <div className="btn-row">
          <button
            type="button"
            className="secondary"
            onClick={() => setPage(aiAssistant ? 2 : 1)}
          >
            Back
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || !allAnswered || !writable}
            onClick={() =>
              // The completing command carries the answers: a zero-miss
              // submission advances to connecting; misses come back on
              // `result.missed` for highlighting and the stage holds.
              run(async () => {
                const res = await store.dispatch("training_step", {
                  step: "comprehension",
                  complete: true,
                  answers,
                });
                if (!res.accepted) throw new ApiError(409, "Comprehension submission rejected", res.reason);
                const m = res.result.missed;
                setMissed(
                  new Set(Array.isArray(m) ? m.map((x) => String(x)) : []),
                );
              })
            }
          >
            Continue to your team
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="Practice: Interacting with the AI assistant">
      <section aria-label="AI assistant preview">
        <h2>AI assistant</h2>
        <p className="pane-hint">
          Ask about a crate and it will review the evidence currently in your
          case file.
        </p>
        <ul className="msgs" aria-label="Practice assistant messages">
          {practice.replies.assistant_question ? (
            <>
              <li className="msg mine">
                <span className="who">{you}</span>
                <span className="msg-body">{practice.replies.assistant_question}</span>
              </li>
              <li className="msg">
                <span className="who">Assistant (practice)</span>
                <span className="msg-body">{practice.replies.assistant_answer}</span>
              </li>
            </>
          ) : null}
        </ul>
        {state.structuredRequests ? (
          <div className="team-actions">
            <label className="visually-hidden" htmlFor="practice-ai-crate">
              Ask the assistant about a crate
            </label>
            <select id="practice-ai-crate" disabled defaultValue="">
              <option value="">Ask the assistant about…</option>
              {state.assignedCrates.map((n) => (
                <option key={n} value={n}>
                  Crate {n}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="composer">
          <label className="visually-hidden" htmlFor="practice-ai-input">
            Ask the assistant
          </label>
          <input
            id="practice-ai-input"
            type="text"
            disabled
            placeholder="Ask the assistant"
            readOnly
          />
          <button type="button" disabled>
            Ask
          </button>
        </div>
        <p className="pane-hint preview-note" role="note">
          Preview only — these controls unlock when the task begins.
        </p>
      </section>
      {runError ? (
        <p className="error-note" role="alert">
          {runError}
        </p>
      ) : null}
      <div className="btn-row">
        <button type="button" className="secondary" onClick={() => setPage(1)}>
          Back
        </button>
        <button
          type="button"
          className="primary"
          disabled={busy || !pageDone || !writable}
          onClick={() => setPage(3)}
        >
          Continue to your team
        </button>
      </div>
    </Panel>
  );
}

export function AiQaScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run] = useBusy();
  const [draft, setDraft] = useState("");
  const [askError, setAskError] = useState<string | null>(null);
  const pendingCount = state.qa.filter((x) => x.status === "pending").length;
  const canAsk = canWrite(state) && !busy && draft.trim().length > 0 && pendingCount === 0;
  return (
    <Panel title="AI Q&A (Optional)">
      <p>
        If anything about the rules of the task is unclear, you can ask a
        question below before you meet your team. The assistant answers
        questions about how the Investigators task works. You do not have to
        ask anything to continue.
      </p>
      {state.qa.map((x) => (
        <div className="qa-exchange" key={x.seq}>
          <p className="qa-question">{x.question}</p>
          {x.answer !== null ? (
            <p className="qa-answer">{x.answer}</p>
          ) : (
            <p className="qa-answer pending" role="status">
              Thinking…
            </p>
          )}
        </div>
      ))}
      <label className="qa-label" htmlFor="qa-question">
        What is your question? (about the rules of the Investigators task)
      </label>
      <textarea
        id="qa-question"
        className="qa-input"
        rows={3}
        maxLength={1000}
        placeholder="Type your question here…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={!canWrite(state)}
      />
      <div className="qa-actions">
        <button
          type="button"
          className="primary qa-button"
          disabled={!canAsk}
          onClick={() =>
            run(async () => {
              try {
                await store.askQa(draft.trim());
                setDraft("");
                setAskError(null);
              } catch {
                setAskError("Your question could not be sent — please try again.");
              }
            })
          }
        >
          Ask
        </button>
      </div>
      {askError ? (
        <p className="error-note" role="alert">
          {askError}
        </p>
      ) : null}
      <button
        type="button"
        className="primary qa-button"
        // AI_QA_ENABLED: Continue is always active — a question is never
        // required to advance (server enforces the same: proceed has no
        // exchange prerequisite).
        disabled={busy || !canWrite(state)}
        onClick={() => run(() => store.proceed())}
      >
        Continue
      </button>
    </Panel>
  );
}

export function ConnectingScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run] = useBusy();
  const [rejected, setRejected] = useState(false);
  const overlap = state.taskShape?.crateOverlap ?? "independent";
  const presented = new Set(state.presentedSeats);
  const pmJoined = presented.has("PM");
  // UX round 3, item 4: JT was already waiting when the participant arrived — its
  // presence commits in the same transaction as this stage, so it is never shown
  // as "joining"; the participant comes next and PM joins after them. The Start
  // gate stays server-side (``ready`` refuses until PM has presented).
  const rows: { key: string; name: string; online: boolean }[] = [
    { key: "JT", name: displaySeat("JT", state.initials), online: true },
    { key: "you", name: youLabel(state.initials), online: true },
    { key: "PM", name: displaySeat("PM", state.initials), online: pmJoined },
  ];
  return (
    <Panel title="Connecting you to your team">
      <ul className="joining-list">
        {rows.map((r) => (
          <li key={r.key} data-seat={r.key}>
            <span className={r.online ? "dot online" : "dot"} /> {r.name}
            <span className="status">{r.online ? "connected" : "joining…"}</span>
          </li>
        ))}
      </ul>
      <p>The clock starts when all of you have pressed Start.</p>
      <p><AssignmentRule shape={state.taskShape} /></p>
      {/* UX_Feedback_v9 item 2: goal reminder on the connecting screen.
          2026-10-15: the assignment condition is restated here per the owner's
          copy spec (participants are reminded before the task starts). */}
      <p>
        Remember: Your goal is to identify the current location of{" "}
        {overlap === "full" ? "the" : "your"} assigned crates.
        {" "}
        You and your teammates will each receive time-stamped intelligence that
        will help you solve the mystery. Be sure to scroll through your case
        file and check your record for each crate, including the day, time, and
        location.
      </p>
      {rejected ? (
        <p className="error-note" role="alert">Still waiting for your team — try again in a moment.</p>
      ) : null}
      <button
        type="button"
        className="primary"
        disabled={busy || !pmJoined || !canWrite(state)}
        onClick={() =>
          run(async () => {
            try {
              await store.dispatch("ready", {});
              setRejected(false);
            } catch {
              setRejected(true);
            }
          })
        }
      >
        Start
      </button>
    </Panel>
  );
}

export function ReadyScreen() {
  return (
    <Panel title="Get ready">
      <p role="status">Your workspace is being prepared…</p>
    </Panel>
  );
}

export function LockedScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run] = useBusy();
  const frozen = Object.entries(state.frozenAnswers).sort(([a], [b]) => Number(a) - Number(b));
  return (
    <Panel title="Time is up">
      <p>Your answers are now locked.</p>
      {frozen.length ? (
        <ul>
          {frozen.map(([crate, loc]) => (
            <li key={crate}>
              Crate {crate}: {loc}
            </li>
          ))}
        </ul>
      ) : null}
      <button
        type="button"
        className="primary"
        disabled={busy || !canWrite(state)}
        onClick={() => run(() => store.proceed())}
      >
        Continue
      </button>
    </Panel>
  );
}

// ----- instrument items (INV-0905/0906) --------------------------------------------------------
//
// Page-level submission (UX feedback): every pending item on the page is resolved
// together and one Continue submits them all then advances — no per-item submit.
// Skip-without-penalty is preserved without a per-item control (UX round 2,
// item 9): leaving a skippable item blank and pressing Continue records an
// explicit skip — ``status=skipped`` with ``missing_reason="left_blank"``, never
// a fabricated answer. ``item_start`` fires on an item's first interaction,
// ``item_end`` when the page submits it.

interface PendingItem {
  /** Local form key — unique per rendered control. */
  key: string;
  item: InstrumentItem;
  context: { crate?: number; seat?: string };
}

function useInstrumentPage(
  pending: PendingItem[],
  store: SessionStore,
  telemetry: TelemetryQueue,
) {
  const [values, setValues] = useState<Record<string, number | string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);
  const startedRef = useRef(new Set<string>());

  const markStarted = (p: PendingItem) => {
    if (!startedRef.current.has(p.key)) {
      startedRef.current.add(p.key);
      telemetry.emit("item_start", { item_id: p.key, ...p.context });
    }
  };
  const setValue = (p: PendingItem, value: number | string) => {
    markStarted(p);
    setValues((v) => ({ ...v, [p.key]: value }));
  };
  // Required items (skippable=false) block Continue — the page may not submit
  // while any of its required controls are blank (2026-10-08 owner direction:
  // all measurement items are required; the server also rejects item_skip).
  const incomplete = pending.some(
    (p) => !p.item.skippable && values[p.key] === undefined,
  );
  // ``advance=false`` saves the page's items without leaving the stage — the
  // caller moves to its next local page instead (UX round 5, item 4).
  const submit = async (advance = true): Promise<boolean> => {
    if (incomplete) return false;
    setSubmitting(true);
    setFailed(false);
    const submittingStage = store.getSnapshot().stage;
    try {
      for (const p of pending) {
        const value = values[p.key];
        if (value !== undefined) {
          await store.itemResponse(p.item.item_id, value, p.context);
        } else if (p.item.skippable) {
          await store.itemSkip(p.item.item_id, {
            ...p.context,
            missing_reason: "left_blank",
          });
        } else {
          continue;
        }
        telemetry.emit("item_end", { item_id: p.key, ...p.context });
      }
      // The last required response can advance the server stage itself.
      // Never send a second Continue into the newly opened questionnaire.
      if (advance && store.getSnapshot().stage === submittingStage) await store.proceed();
      return true;
    } catch {
      setFailed(true);
      return false;
    } finally {
      setSubmitting(false);
    }
  };
  return { values, setValue, submitting, failed, incomplete, submit };
}

function PageSubmit({
  page,
  state,
  label = "Continue",
  advance = true,
  onDone,
}: {
  page: ReturnType<typeof useInstrumentPage>;
  state: StoreState;
  label?: string;
  advance?: boolean;
  onDone?: () => void;
}) {
  return (
    <>
      <button
        type="button"
        className="primary"
        disabled={page.submitting || page.incomplete || !canWrite(state)}
        onClick={() => void page.submit(advance).then((ok) => ok && onDone?.())}
      >
        {page.submitting ? "Saving…" : label}
      </button>
      {page.incomplete ? (
        <p className="overlay-note">Please answer every item to continue.</p>
      ) : null}
      {page.failed ? (
        <p className="error-note" role="alert">
          Some answers were not saved — press {label} to try again.
        </p>
      ) : null}
    </>
  );
}

function LikertControl({
  text,
  label,
  response,
  value,
  onChange,
  disabled,
}: {
  text: string;
  label: string;
  response: InstrumentProjection["response"];
  value: number | undefined;
  onChange: (v: number) => void;
  disabled: boolean;
}) {
  const scale = Array.from(
    { length: response.max - response.min + 1 },
    (_, i) => response.min + i,
  );
  return (
    <div className="item">
      <p>{text}</p>
      <div className="likert-row" role="radiogroup" aria-label={label}>
        {scale.map((n) => (
          <button
            key={n}
            type="button"
            className={value === n ? "on" : ""}
            disabled={disabled}
            onClick={() => onChange(n)}
          >
            {n}
          </button>
        ))}
      </div>
      <div className="scale-ends">
        <span>{response.min_label}</span>
        <span>{response.max_label}</span>
      </div>
    </div>
  );
}

function ChoiceControl({
  text,
  label,
  options,
  value,
  onChange,
  disabled,
}: {
  text: string;
  label: string;
  options: string[];
  value: number | string | undefined;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="item">
      <p>{text}</p>
      <div className="choice-list" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className={value === option ? "choice on" : "choice"}
            disabled={disabled}
            onClick={() => onChange(option)}
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * UX round 8: the open-ended follow-ups render one at a time in declared order —
 * each question appears only after the previous one is submitted, below the
 * completed one on the same page. The final item commits server-side into
 * debrief (no explicit proceed, so no spurious wrong-stage rejection).
 */
function OpenEndedPage({
  state,
  store,
  telemetry,
  inst,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  inst: InstrumentProjection;
}) {
  const serverAnswered = new Set(inst.answered_items);
  const [values, setValues] = useState<Record<string, string>>({});
  const [localDone, setLocalDone] = useState<ReadonlySet<string>>(new Set());
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const startedRef = useRef(new Set<string>());
  const writable = canWrite(state);
  const isDone = (id: string) => serverAnswered.has(id) || localDone.has(id);
  const markStarted = (id: string) => {
    if (!startedRef.current.has(id)) {
      startedRef.current.add(id);
      telemetry.emit("item_start", { item_id: id });
    }
  };
  const submitItem = (item: InstrumentItem) => {
    const raw = values[item.item_id] ?? "";
    if (!raw.trim()) return;
    setSubmitting(item.item_id);
    setFailed(null);
    void store
      .itemResponse(item.item_id, raw)
      .then(async () => {
        telemetry.emit("item_end", { item_id: item.item_id });
        setLocalDone((d) => new Set(d).add(item.item_id));
        await store.refreshWorkbench().catch(() => undefined);
      })
      .catch(() => setFailed(item.item_id))
      .finally(() => setSubmitting(null));
  };

  const first = inst.items[0];
  if (!first) {
    return (
      <Panel title="Questions">
        <p role="status">Loading your questions…</p>
      </Panel>
    );
  }
  return (
    <Panel title="Two last questions">
      {inst.items.slice(0, 2).map((item, idx) => {
        const previousDone = inst.items.slice(0, idx).every((i) => isDone(i.item_id));
        if (!previousDone) return null;
        const answeredItem = isDone(item.item_id);
        const value = values[item.item_id] ?? "";
        return (
          <div className="item" key={item.item_id} data-item={item.item_id}>
            <p>{item.text}</p>
            {answeredItem ? (
              <p className="done-line">Response recorded.</p>
            ) : (
              <>
                <textarea
                  className="open-ended-text"
                  aria-label={item.text}
                  rows={4}
                  maxLength={inst.response.max}
                  value={value}
                  disabled={!writable || submitting !== null}
                  onChange={(e) => {
                    markStarted(item.item_id);
                    setValues((v) => ({ ...v, [item.item_id]: e.target.value }));
                  }}
                />
                <button
                  type="button"
                  className="primary"
                  disabled={!writable || submitting !== null || !value.trim()}
                  onClick={() => submitItem(item)}
                >
                  {submitting === item.item_id ? "Saving…" : "Continue"}
                </button>
                {failed === item.item_id ? (
                  <p className="error-note" role="alert">
                    Your answer was not saved — press Continue to try again.
                  </p>
                ) : null}
              </>
            )}
          </div>
        );
      })}
    </Panel>
  );
}

export function InstrumentScreen({
  state,
  store,
  telemetry,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
}) {
  const inst = state.instrument;
  if (!inst) {
    return (
      <Panel title="Questions">
        <p role="status">Loading your questions…</p>
      </Panel>
    );
  }
  if (inst.stage === "open_ended") {
    return <OpenEndedPage state={state} store={store} telemetry={telemetry} inst={inst} />;
  }
  if (inst.applies_to === "per_answered_crate") {
    return <ConfidencePage state={state} store={store} telemetry={telemetry} inst={inst} />;
  }
  if (inst.applies_to === "per_teammate_seat") {
    return <SeatRatingsPage state={state} store={store} telemetry={telemetry} inst={inst} />;
  }
  return <SessionItemsPage state={state} store={store} telemetry={telemetry} inst={inst} />;
}

/**
 * Per-answered-crate confidence (INV-0905): a card per rated crate in the same
 * arrangement the answer bar uses — the crate tag and the submitted location sit
 * beside the slider, matching the fielded layout.
 */
function ConfidencePage({
  state,
  store,
  telemetry,
  inst,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  inst: InstrumentProjection;
}) {
  const pendingCrates = inst.pending_crates.filter((c) => !inst.answered_crates.includes(c));
  const pending: PendingItem[] = pendingCrates.flatMap((crate) =>
    inst.items.map((item) => ({
      key: `${item.item_id}.${crate}`,
      item,
      context: { crate },
    })),
  );
  const page = useInstrumentPage(pending, store, telemetry);
  const writable = canWrite(state);
  const recorded = inst.answered_crates.filter((c) => inst.pending_crates.includes(c));
  const answerFor = (crate: number) =>
    state.frozenAnswers[crate] ?? state.answers[crate]?.value ?? null;

  // No submitted answers means there is nothing to rate — say so instead of
  // presenting a page that completes with zero items (UX round 2, item 8).
  if (inst.pending_crates.length === 0) {
    return (
      <Panel title="Confidence ratings">
        <p>You did not enter a location for any crate, so there is nothing to rate.</p>
        <PageSubmit page={page} state={state} />
      </Panel>
    );
  }

  return (
    <Panel title="Confidence ratings">
      <p>Rate how confident you are in each of your answers.</p>
      <div className="confidence-grid">
        {pending.map((p) => {
          const crate = p.context.crate as number;
          const answer = answerFor(crate);
          const value = page.values[p.key];
          return (
            <div className="confidence-card" key={p.key}>
              <p className="card-title">Crate {crate}</p>
              {answer ? (
                <p className="card-answer">
                  Your answer: <b>{answer}</b>
                </p>
              ) : null}
              <div className="slider-readout" aria-live="polite">
                {value === undefined ? "Confidence: —" : `Confidence: ${value}%`}
              </div>
              <input
                type="range"
                aria-label={`Confidence for Crate ${crate}`}
                min={inst.response.min}
                max={inst.response.max}
                value={value ?? (inst.response.min + inst.response.max) / 2}
                disabled={!writable || page.submitting}
                onChange={(e) => page.setValue(p, Number(e.target.value))}
              />
              <div className="scale-ends">
                <span>{inst.response.min_label}</span>
                <span>{inst.response.max_label}</span>
              </div>
              {value === undefined ? (
                <p className="overlay-note">
                  Move the slider to answer. It has no value until you do.
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
      {recorded.map((crate) => (
        <p key={crate} className="done-line">
          Crate {crate}: response recorded
        </p>
      ))}
      <PageSubmit page={page} state={state} />
    </Panel>
  );
}

function SeatRatingsPage({
  state,
  store,
  telemetry,
  inst,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  inst: InstrumentProjection;
}) {
  const answered = new Set(inst.answered_items);
  const pendingBySeat = (seat: string): PendingItem[] =>
    inst.items
      .filter((i) => !answered.has(`${i.item_id}.${seat}`))
      .map((item) => ({ key: `${item.item_id}.${seat}`, item, context: { seat } }));
  const pending = inst.seats.flatMap(pendingBySeat);
  const page = useInstrumentPage(pending, store, telemetry);
  const peerIntro = state.exposure?.peer_intro ? surfaceText(state.exposure.peer_intro) : null;
  const seatName = (seat: string) => displaySeat(seat, state.initials);
  return (
    <Panel title="Your teammates">
      {peerIntro ? <p data-surface="peer_intro">{peerIntro}</p> : null}
      {inst.seats.map((seat) => {
        const items = pendingBySeat(seat);
        if (!items.length) return null;
        // The instrument text templates the seat as "{seat}" — substitute the
        // displayed name (UX round 2, item 11).
        const seatText = (text: string) => text.replaceAll("{seat}", seatName(seat));
        return (
          <section key={seat} aria-label={`About ${seatName(seat)}`}>
            <h2>About {seatName(seat)}</h2>
            {items.map((p) =>
              p.item.options?.length ? (
                <ChoiceControl
                  key={p.key}
                  text={seatText(p.item.text)}
                  label={seatText(p.item.text)}
                  options={p.item.options}
                  value={page.values[p.key]}
                  onChange={(v) => page.setValue(p, v)}
                  disabled={!canWrite(state) || page.submitting}
                />
              ) : (
                <LikertControl
                  key={p.key}
                  text={seatText(p.item.text)}
                  label={seatText(p.item.text)}
                  response={inst.response}
                  value={
                    typeof page.values[p.key] === "number"
                      ? (page.values[p.key] as number)
                      : undefined
                  }
                  onChange={(v) => page.setValue(p, v)}
                  disabled={!canWrite(state) || page.submitting}
                />
              ),
            )}
          </section>
        );
      })}
      <PageSubmit page={page} state={state} />
    </Panel>
  );
}

function SessionItemsPage({
  state,
  store,
  telemetry,
  inst,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  inst: InstrumentProjection;
}) {
  const answered = new Set(inst.answered_items);
  const allPending: PendingItem[] = inst.items
    .filter((i) => !answered.has(i.item_id))
    .map((item) => ({ key: item.item_id, item, context: {} }));
  // UX round 5, item 4: fixed-choice items (the teammate-nature suspicion
  // check) get their own page after the mechanism items — co-presenting them
  // on one page risks contaminating the mechanism answers.
  const groups = [
    allPending.filter((p) => !p.item.options?.length),
    allPending.filter((p) => p.item.options?.length),
  ].filter((g) => g.length > 0);
  const [pageIdx, setPageIdx] = useState(0);
  const idx = Math.min(pageIdx, groups.length - 1);
  const pending = groups[idx] ?? [];
  const lastPage = idx >= groups.length - 1;
  const page = useInstrumentPage(pending, store, telemetry);
  return (
    <Panel title="A few questions">
      {pending.map((p) =>
        p.item.options?.length ? (
          <ChoiceControl
            key={p.key}
            text={p.item.text}
            label={p.item.text}
            options={p.item.options}
            value={page.values[p.key]}
            onChange={(v) => page.setValue(p, v)}
            disabled={!canWrite(state) || page.submitting}
          />
        ) : inst.response.type === "slider" ? (
          <div key={p.key} className="item">
            <p>{p.item.text}</p>
            <div className="slider-readout" aria-live="polite">
              {page.values[p.key] === undefined ? "—" : `${page.values[p.key]}`}
            </div>
            <input
              type="range"
              aria-label={p.item.text}
              min={inst.response.min}
              max={inst.response.max}
              value={
                page.values[p.key] ?? (inst.response.min + inst.response.max) / 2
              }
              disabled={!canWrite(state) || page.submitting}
              onChange={(e) => page.setValue(p, Number(e.target.value))}
            />
            <div className="scale-ends">
              <span>{inst.response.min_label}</span>
              <span>{inst.response.max_label}</span>
            </div>
            {page.values[p.key] === undefined ? (
              <p className="overlay-note">
                Move the slider to answer. It has no value until you do.
              </p>
            ) : null}
          </div>
        ) : (
          <LikertControl
            key={p.key}
            text={p.item.text}
            label={p.item.text}
            response={inst.response}
            value={typeof page.values[p.key] === "number" ? (page.values[p.key] as number) : undefined}
            onChange={(v) => page.setValue(p, v)}
            disabled={!canWrite(state) || page.submitting}
          />
        ),
      )}
      <PageSubmit
        page={page}
        state={state}
        advance={lastPage}
        onDone={() => setPageIdx((i) => i + 1)}
      />
    </Panel>
  );
}

/**
 * Debrief (INV-0907): artifact-backed and never auto-dismissing — the only way forward
 * is the explicit Continue that records ``debrief_ack``. The copy explains the
 * automated teammates and the study's real purpose; for the peer-eval family it adds
 * the evaluation clarification. Wording is interim reference copy (D06/D09 pending).
 */
export function DebriefScreen({
  state,
  store,
}: {
  state: StoreState;
  store: SessionStore;
}) {
  const [busy, run, runError] = useBusy();
  const isPeerEval = state.studyKey === "investigators_peer_eval";
  // AI_ASSISTANT_ENABLED=0 sessions never saw an assistant — the debrief must
  // not describe a surface the participant never had.
  const aiAssistant = state.aiAssistant;
  // TIME_PRESSURE_ENABLED=0 sessions never saw a check-in — the debrief must
  // not claim a ratings disclosure that never happened.
  const timePressure = state.timePressure;
  // BONUS_PAYMENT_ENABLED=0 sessions never saw a bonus offer — the debrief must
  // not disclose a bonus payment that was never described during the study.
  const bonusPayment = state.bonusPayment;
  return (
    <Panel title="Debrief">
      <p>Thank you for taking part. Please read this before finishing.</p>
      {isPeerEval ? (
        <>
          <p>
            This study examined how people evaluate teammates during a short
            collaborative task. The teammates you worked with were automated systems
            operated by the research team — no other participants were present.
          </p>
          <p>
            To be clear about what was collected: you were asked to rate your teammates,
            but no ratings of you were collected from them.
          </p>
        </>
      ) : timePressure ? (
        <p>
          This study examined whether learning about teammates' workloads
          changes how much people ask them for information
          {aiAssistant ? ", and whether they turn to an AI assistant instead" : ""}.
          The teammates you worked with were automated systems
          operated by the research team. The workload ratings shown for your
          teammates at the check-in were assigned by the research team, not reported by
          anyone. Your own rating was recorded exactly as you entered it.
          {bonusPayment
            ? " The accuracy bonus described during the study is paid to every completer."
            : ""}
        </p>
      ) : (
        <p>
          This study examined how team members share information and make
          decisions together on a short investigative task
          {aiAssistant ? ", including whether they turn to an AI assistant for help" : ""}.
          The teammates you worked with were automated systems
          operated by the research team.
          {bonusPayment
            ? " The accuracy bonus described during the study is paid to every completer."
            : ""}
        </p>
      )}
      <p>
        If you have questions about this research, please contact [Principal Investigator]
        (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also
        contact the university's human research protection program by emailing
        irb@university.edu. Please refer to protocol number XXXX-XXXX.
      </p>
      <button
        type="button"
        className="primary"
        disabled={busy || state.handoffInFlight || !canWrite(state)}
        // Debrief goes straight to the payment code (UX round 2, item 13):
        // ack, then the recorded use_all disposition, then completion.
        onClick={() => run(() => store.finishHandoff())}
      >
        {state.handoffInFlight ? "Finishing…" : "Continue"}
      </button>
      {runError || state.handoffError ? (
        <p className="error-note" role="alert">
          {runError ?? state.handoffError}
        </p>
      ) : null}
    </Panel>
  );
}

/**
 * Resume fallback for sessions already sitting in ``data_use_choice`` (the
 * debrief Continue chains through it — UX round 2, item 13). Finish records the
 * default ``use_all`` disposition explicitly — the backend completion invariant
 * still requires a recorded choice.
 */
export function DataUseScreen({ state, store }: { state: StoreState; store: SessionStore }) {
  const [busy, run, runError] = useBusy();
  const writable = canWrite(state);
  return (
    <Panel title="Questions or concerns?">
      <p>
        Your responses will be used for research as described in the consent form. If you
        would like your responses removed, or if you have any other questions about this
        research, please contact [Principal Investigator] (pi@university.edu) or
        [Co-Investigator] (coi@university.edu). You may also contact the university's human
        research protection program by emailing irb@university.edu. Please refer to
        protocol number XXXX-XXXX.
      </p>
      <p>Whatever you decide does not change your payment.</p>
      <button
        type="button"
        className="primary"
        disabled={busy || !writable}
        onClick={() => run(() => store.finishHandoff())}
      >
        Finish
      </button>
      {runError || state.handoffError ? (
        <p className="error-note" role="alert">
          {runError ?? state.handoffError}
        </p>
      ) : null}
    </Panel>
  );
}

/**
 * Completion handoff (INV-0907): the code is served from the persisted receipt and is
 * stable across reloads — a refresh re-shows this screen, it never dead-ends.
 */
export function CompletionScreen({ state }: { state: StoreState }) {
  return (
    <Panel title="You are finished">
      <p>Thank you — your session is complete.</p>
      {state.completionCode ? (
        <p>
          Your completion code is:{" "}
          <b data-testid="completion-code">{state.completionCode}</b>
        </p>
      ) : null}
      {state.completionPlatform === "connect" ? (
        state.completionUrl ? (
          <p><a href={state.completionUrl}>Return to Connect</a></p>
        ) : (
          <p>Return to Connect and enter this code to submit your participation.</p>
        )
      ) : state.completionPlatform === "prolific" ? (
        <p>Return to Prolific and enter this code to submit your participation.</p>
      ) : null}
    </Panel>
  );
}

export function TerminalScreen({ state }: { state: StoreState }) {
  // Payment language mirrors the bound payment policy's standing rules: decline and
  // withdrawal leave the study's payment terms unchanged; a technical stop preserves
  // everything recorded before it. Final wording is a D04/D06 publication step.
  const text = {
    declined:
      "You have declined to take part. The payment choices the study listing described are unchanged. You can close this window.",
    withdrawn:
      "You have withdrawn from the study. What was recorded before you left is preserved, and the payment the study listing described is unaffected. You can close this window.",
    technical_stop:
      "This session stopped because of a technical problem. Your answers and study record up to that point were saved. You can close this window.",
    abandoned:
      "This session has ended. If you return using your original study link, you will resume where your session stands. You can close this window.",
  } as const;
  // SQ-6/BA-91: a check-in kill names its cause in plain words — "not
  // completed" covers both an unanswered collect phase and a submitted rating
  // left waiting at reveal; a named cause *replaces* the generic "technical
  // problem" paragraph rather than contradicting it.
  const reasonText: Record<string, string> = {
    checkin_incomplete:
      "This session ended because the team check-in was not completed within the response window. Your answers and study record up to that point were saved. You can close this window.",
  };
  const reason = state.terminalReason;
  const reasonCopy = reason ? (reasonText[reason] ?? null) : null;
  return (
    <Panel title="Session ended">
      <p>{reasonCopy ?? text[state.stage as keyof typeof text] ?? "This session has ended."}</p>
      {reason && !reasonCopy ? <p className="overlay-note">{reason}</p> : null}
    </Panel>
  );
}



export function RefusedScreen({ state }: { state: StoreState }) {
  return (
    <Panel title="This study is not open">
      <p>Please return using the link provided by the study platform.</p>
      {state.refusedReason ? (
        <p className="overlay-note" data-testid="refused-reason">
          {state.refusedReason}
        </p>
      ) : null}
    </Panel>
  );
}
