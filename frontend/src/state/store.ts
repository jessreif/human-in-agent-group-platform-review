/**
 * Session store (INV-0804/0805/0806): the single client-side state machine for the
 * participant workbench. It owns the event cursor, the monotonic revision, the
 * interpolated clock anchor, drafts and connection status — and it is deliberately free
 * of DOM/React so the tricky transitions can be unit-tested in isolation.
 *
 * Invariants (spec §6.2, §8.2, §10):
 * - ``revision`` and the event ``cursor`` are monotonic; a replayed ``seq`` with a
 *   different ``event_id`` is an integrity violation and triggers a full resync.
 * - Unsent edits stay "dirty" until a command ack; a resync never clobbers a dirty draft;
 *   a rejected answer shows the rejection, never a false Saved.
 * - Client timers interpolate only; state transitions arrive exclusively through the
 *   event feed / session snapshot.
 */
import { ApiError, type CommandBody } from "../api/client";
import type {
  ChannelName,
  CheckinCopy,
  CommandKind,
  CommandResult,
  EventsResponse,
  ExposureProjection,
  FileRecord,
  InstrumentProjection,
  MaterialProjection,
  ParticipantEvent,
  PracticeWorld,
  QaExchange,
  SessionResponse,
  WorkbenchResponse,
} from "../api/types";
import { STAGES, type Stage } from "../api/types";
import {
  activeAt,
  anchorFromClock,
  anchorFromEvent,
  GREETING_WINDOW_MS,
  type ClockAnchor,
} from "./clock";
import { adoptWriter, clearWriter, tabId, writeClaim } from "./writerLease";

export type ConnectionState = "connecting" | "live" | "polling" | "disconnected";
export type WriterState = "held" | "readonly" | "unknown";
export type SaveStatus = "clean" | "dirty" | "saving" | "saved" | "rejected";
export type Phase = "entering" | "refused" | "booting" | "ready";

/** 2026-10-15 crate-condition experiment: the bound manifest's task shape —
 * absent/absent-field sessions mean the legacy 8-crate independent deal. */
export interface TaskShape {
  nCrates: number;
  nAssigned: number;
  crateOverlap: "independent" | "none" | "some" | "full";
  intelBudget: number | null;
}

export interface AnswerEntry {
  /** The value the participant currently sees. "" = unanswered. */
  value: string;
  /** Last value the server acknowledged. */
  committed: string;
  status: SaveStatus;
  error: string | null;
}

export interface ChatMessage {
  id: string;
  channel: "team" | "assistant" | "system";
  actor: string;
  mine: boolean;
  body: string;
  crate: number | null;
  recordId: string | null;
  seq: number;
}

export interface CheckinState {
  phase: "closed" | "collect" | "wait" | "reveal";
  copy: CheckinCopy | null;
  /** Seat values committed at checkin_open (shown only at reveal). */
  assignedValues: Record<string, number> | null;
  /** Revealed values, kept for the persistent strip above the chat after resume. */
  revealedValues: Record<string, number> | null;
  ownValue: number | null;
  submitStatus: SaveStatus;
  /** SQ-7/BA-90: checkin_open's committed server time (clamped to receipt) —
   * anchors the stated response-window countdown across replays/resync. */
  openedAtMs: number | null;
}

export interface StoreState {
  phase: Phase;
  refusedReason: string | null;
  /** Bound once the session is known; keys the per-tab writer claim. */
  sessionId: string | null;
  stage: Stage | null;
  revision: number;
  /** Last applied event seq (the replay cursor). */
  cursor: number;
  /** Server high-water: next_event_seq - 1. */
  headSeq: number;
  clock: ClockAnchor | null;
  connection: ConnectionState;
  writer: WriterState;
  /** Set when a mutating command was refused by the writer lease (second tab). */
  leaseNotice: boolean;
  /** Visible error for the final debrief→data-use→complete chain (BA-44). The
   *  chain crosses a stage transition that unmounts the screen which started it,
   *  so the error lives in store state, not component state. */
  handoffError: string | null;
  /** True while ``finishHandoff`` is running (UX round 3, item 11): the chain passes
   *  through ``data_use_choice`` on its way to completion, and the UI keeps the
   *  debrief on screen instead of flashing the resume-only "Questions" page. */
  handoffInFlight: boolean;
  /** Set when the credential was rejected (401/403) — the session must be re-entered. */
  authFailed: boolean;
  terminalReason: string | null;
  /** Which study family this session belongs to (``study_key`` — not the arm). */
  studyKey: string | null;
  messages: ChatMessage[];
  presentedSeats: string[];
  greetingOptions: string[] | null;
  /** Active-time anchor of the greeting_prompt event — the 30 s greeting window runs
   * from prompt delivery, not task start (review-first sessions defer the prompt to
   * channel open, so task-start anchoring would hide the buttons entirely). */
  greetingAnchorActiveMs: number | null;
  file: FileRecord[];
  delivered: Record<string, FileRecord>;
  /** Client-side display order of file record_ids (sort/reorder surface state). */
  fileOrder: string[];
  /** Records the participant already shared to the team chat — the Share affordance
   * is replaced by a "Shared in team chat" tag (UX round 2, item 6). */
  sharedRecordIds: string[];
  /** Server-stored participant initials (UX round 2, item 10) — replaces "You"
   * wherever the UI names the participant. */
  initials: string | null;
  assignedCrates: number[];
  locations: string[];
  /** SQ-11: crate → item labels for pickers/ask dropdowns. */
  crateItems: Record<number, string>;
  answers: Record<number, AnswerEntry>;
  frozenAnswers: Record<number, string>;
  channels: ChannelName[];
  /** FR-1: per-crate request controls are offered (structured + natural language). */
  structuredRequests: boolean;
  /** AI_ASSISTANT_ENABLED (deployment flag): false removes the assistant pane,
   * channel and assistant-specific items experiment-wide. */
  aiAssistant: boolean;
  /** TIME_PRESSURE_ENABLED (deployment flag): false removes the check-in,
   * "One minute left" warning, low-time styling and rushed-feeling items —
   * the countdown stays a neutral timer and the deadline still applies. */
  timePressure: boolean;
  /** BONUS_PAYMENT_ENABLED (deployment flag): false removes every
   * accuracy-bonus mention — case background, gameplay banner, debrief
   * disclosure. The consent artifact and payment policy are substituted
   * server-side. */
  bonusPayment: boolean;
  /** Bound manifest task shape (crate-condition experiment): crate count,
   * overlap level, and thin-file budget drive instructions/connecting copy. */
  taskShape: TaskShape | null;
  /** Seconds of solo review before chat/assistant open, when the manifest has one. */
  soloReviewSeconds: number | null;
  worldReady: boolean;
  checkin: CheckinState;
  instrument: InstrumentProjection | null;
  /** Bound material_reference artifact for the current stage (INV-0901). */
  material: MaterialProjection | null;
  /** Stage-scoped PE exposure surfaces (INV-0904) — absent entries are never shown. */
  exposure: ExposureProjection | null;
  /** Fixed practice world during training (INV-0901). */
  practice: PracticeWorld | null;
  /** AI_QA_ENABLED: the session's committed rules-Q&A exchanges on the
   * ``ai_qa`` stage (empty off-stage — the stage itself is the flag signal). */
  qa: QaExchange[];
  /** Recoverable completion handoff code (INV-0907). */
  completionCode: string | null;
  completionUrl: string | null;
  completionPlatform: string | null;
  taskClosed: boolean;
  lowTimeActive: boolean;
  warningMessageId: string | null;
  pendingCommands: number;
  /** Bumped on every visible stage transition so views can do one-time resets. */
  stageTick: number;
}

const CLOSED_CHECKIN: CheckinState = {
  phase: "closed",
  copy: null,
  assignedValues: null,
  revealedValues: null,
  ownValue: null,
  submitStatus: "clean",
  openedAtMs: null,
};

export const INITIAL_STATE: StoreState = {
  phase: "entering",
  refusedReason: null,
  sessionId: null,
  stage: null,
  revision: 0,
  cursor: 0,
  headSeq: 0,
  clock: null,
  connection: "connecting",
  writer: "unknown",
  leaseNotice: false,
  handoffError: null,
  handoffInFlight: false,
  authFailed: false,
  terminalReason: null,
  studyKey: null,
  messages: [],
  presentedSeats: [],
  greetingOptions: null,
  greetingAnchorActiveMs: null,
  file: [],
  delivered: {},
  fileOrder: [],
  sharedRecordIds: [],
  initials: null,
  assignedCrates: [],
  locations: [],
  crateItems: {},
  answers: {},
  frozenAnswers: {},
  channels: [],
  structuredRequests: true,
  aiAssistant: true,
  timePressure: true,
  bonusPayment: true,
  taskShape: null,
  soloReviewSeconds: null,
  worldReady: false,
  checkin: CLOSED_CHECKIN,
  instrument: null,
  material: null,
  exposure: null,
  practice: null,
  qa: [],
  completionCode: null,
  completionUrl: null,
  completionPlatform: null,
  taskClosed: false,
  lowTimeActive: false,
  warningMessageId: null,
  pendingCommands: 0,
  stageTick: 0,
};

/** The API surface the store needs — satisfied by the real client, mockable in tests. */
export interface StoreApi {
  getSession(): Promise<SessionResponse>;
  getEvents(afterSeq: number): Promise<EventsResponse>;
  getWorkbench(): Promise<WorkbenchResponse>;
  postCommand(body: CommandBody): Promise<CommandResult>;
  postComplete(body: {
    command_id: string;
    expected_revision: number;
    client_monotonic_ms?: number;
  }): Promise<CommandResult>;
}

export interface StoreDeps {
  api: StoreApi;
  /** Monotonic local ms source (defaults to Date.now). */
  now?: () => number;
  /** Fresh command UUIDs (defaults to crypto.randomUUID). */
  uuid?: () => string;
  /** Telemetry tap: (event, replayed) for each applied event. */
  onEventApplied?: (event: ParticipantEvent, replayed: boolean) => void;
  /** Tap when the visible stage changes. */
  onStageChange?: (from: Stage | null, to: Stage) => void;
}

const STAGE_SET: ReadonlySet<string> = new Set<string>(STAGES);

/** Stages after the task freeze — answers can no longer be accepted. */
const POST_TASK_STAGES: ReadonlySet<Stage> = new Set<Stage>([
  "answers_locked",
  "confidence",
  "peer_ratings",
  "post_questionnaire",
  "open_ended",
  "debrief",
  "data_use_choice",
  "completion",
  "declined",
  "withdrawn",
  "technical_stop",
  "abandoned",
]);

const CHECKIN_STAGES: ReadonlySet<Stage> = new Set<Stage>([
  "checkin_collect",
  "checkin_wait",
  "checkin_reveal",
]);

/** Events whose commit accompanies a stage transition — prompt a session refetch. */
const STAGE_HINT_EVENTS = new Set([
  "consent_recorded",
  "training_started",
  "task_clock_started",
  "checkin_open",
  "checkin_reveal",
  "task_deadline",
  "answers_frozen",
  "stage_proceeded",
  "instrument_complete",
  "session_completed",
  "technical_stop",
  "writer_takeover",
]);

/** How long a "Saved" marker stays before fading back to clean. */
export const SAVED_FADE_MS = 2500;
export const WORKBENCH_RETRY_MS = 2500;
/** Per-crate debounce between the last edit and the answer_update command. */
export const ANSWER_DEBOUNCE_MS = 400;
/** Maximum stale-revision resync+retry rounds for one command. */
export const MAX_STALE_RETRIES = 2;

export class SessionStore {
  private deps: StoreDeps & { now: () => number };
  private _state: StoreState = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private seen = new Map<number, string>(); // seq -> event_id integrity map
  private flushTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private savedTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private resyncing: Promise<void> | null = null;
  private workbenchRetryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(deps: StoreDeps) {
    this.deps = { ...deps, now: deps.now ?? (() => Date.now()) };
  }

  // ----- subscription --------------------------------------------------------------

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getSnapshot = (): StoreState => this._state;

  private emit(patch: Partial<StoreState>): void {
    this._state = { ...this._state, ...patch };
    for (const fn of this.listeners) fn();
  }

  setConnection(connection: ConnectionState): void {
    if (this._state.connection !== connection) this.emit({ connection });
  }

  /** Phase/writer transitions driven by the bootstrap controller (not user commands). */
  setBootPhase(phase: Phase, extra: Partial<StoreState> = {}): void {
    this.emit({ phase, ...extra });
  }

  setWriter(writer: WriterState, leaseNotice = false): void {
    this.emit({ writer, leaseNotice });
  }

  private setStage(to: Stage): void {
    const prev = this._state;
    if (prev.stage === to) return;
    this.emit({ stage: to, stageTick: prev.stageTick + 1 });
    this.deps.onStageChange?.(prev.stage, to);
    // The projection is stage-scoped (channels, instrument items, world rows): a stage
    // transition always warrants a refresh so a pre-task snapshot can't linger.
    void this.refreshWorkbench();
  }

  // ----- session / snapshot ----------------------------------------------------------

  /**
   * Session identity change (BA-33): a terminal-cookie re-entry hands the store a
   * DIFFERENT session whose revision/cursor counters restart low, while every
   * anchor in this store is monotonic (``Math.max``). Max-ing across two sessions
   * pins ``revision`` above the new session's real revision — every command then
   * dies on ``stale_revision`` and a resync can never pull it back down. Re-anchor
   * all session-scoped state from scratch instead.
   */
  // BA-47: projection fencing. Session and workbench snapshots share ONE applied
  // revision watermark — a response resolving older than anything already applied
  // (either kind) is dropped whole, so an old workbench can never roll back a
  // stage a newer session snapshot established. ``sessionEpoch`` fences in-flight
  // reads across session re-entry — a response fetched against the old session
  // can never be applied into the new one.
  private sessionEpoch = 0;
  private snapshotRevWatermark = -1;

  private resetSessionScoped(): void {
    this.sessionEpoch += 1;
    this.clearWorkbenchRetry();
    this.snapshotRevWatermark = -1;
    this.seen.clear();
    for (const t of this.flushTimers.values()) clearTimeout(t);
    this.flushTimers.clear();
    this.answerInFlight.clear();
    this.answerSaveTail = null;
    for (const t of this.savedTimers.values()) clearTimeout(t);
    this.savedTimers.clear();
    this.completeId = null;
    this.emit({
      stage: null,
      revision: 0,
      cursor: 0,
      headSeq: 0,
      clock: null,
      writer: "unknown",
      leaseNotice: false,
      handoffError: null,
      handoffInFlight: false,
      authFailed: false,
      terminalReason: null,
      studyKey: null,
      messages: [],
      presentedSeats: [],
      greetingOptions: null,
      greetingAnchorActiveMs: null,
      file: [],
      delivered: {},
      fileOrder: [],
      sharedRecordIds: [],
      initials: null,
      assignedCrates: [],
      locations: [],
      crateItems: {},
      answers: {},
      frozenAnswers: {},
      channels: [],
      structuredRequests: true,
      aiAssistant: true,
      timePressure: true,
      bonusPayment: true,
      taskShape: null,
      soloReviewSeconds: null,
      worldReady: false,
      checkin: CLOSED_CHECKIN,
      instrument: null,
      material: null,
      exposure: null,
      practice: null,
      qa: [],
      completionCode: null,
      completionUrl: null,
      completionPlatform: null,
      taskClosed: false,
      lowTimeActive: false,
      warningMessageId: null,
      pendingCommands: 0,
      stageTick: this._state.stageTick + 1,
    });
  }

  applySessionResponse(resp: SessionResponse): void {
    const incomingSessionId = resp.session.session_id ?? null;
    if (
      incomingSessionId !== null &&
      this._state.sessionId !== null &&
      incomingSessionId !== this._state.sessionId
    ) {
      this.resetSessionScoped();
    }
    // Reject a snapshot older than anything already applied — otherwise a delayed
    // response can roll the stage/projection backwards while a maxed revision
    // pretends it didn't (BA-47).
    if (resp.session.revision < this.snapshotRevWatermark) return;
    this.snapshotRevWatermark = resp.session.revision;
    const prev = this._state;
    const stageChanged = prev.stage !== null && prev.stage !== resp.session.stage;
    const closeCheckin =
      !CHECKIN_STAGES.has(resp.session.stage) && prev.checkin.phase !== "closed";
    this.emit({
      sessionId: resp.session.session_id ?? prev.sessionId,
      stage: resp.session.stage,
      revision: Math.max(prev.revision, resp.session.revision),
      headSeq: Math.max(prev.headSeq, resp.session.next_event_seq - 1),
      clock: anchorFromClock(resp.clock, this.deps.now()),
      terminalReason: resp.session.terminal_reason,
      studyKey: resp.session.study_key ?? prev.studyKey,
      completionCode: resp.session.completion_code ?? prev.completionCode,
      completionUrl: resp.session.completion_url ?? prev.completionUrl,
      completionPlatform: resp.session.completion_platform ?? prev.completionPlatform,
      initials: resp.session.initials ?? prev.initials,
      taskClosed: prev.taskClosed || POST_TASK_STAGES.has(resp.session.stage),
      stageTick: stageChanged ? prev.stageTick + 1 : prev.stageTick,
      // The stage is authoritative — a missed stage_proceeded must not pin the overlay.
      ...(closeCheckin
        ? { checkin: { ...prev.checkin, phase: "closed" as const } }
        : {}),
    });
    if (closeCheckin) this.reflushDirtyAnswers();
    if (stageChanged) {
      this.deps.onStageChange?.(prev.stage, resp.session.stage);
      // The projection is stage-scoped — refresh it on every transition.
      void this.refreshWorkbench();
    }
  }

  applyWorkbenchResponse(resp: WorkbenchResponse): void {
    // Stale projection fence (BA-47): an older snapshot — older than ANY applied
    // projection, session or workbench — must not roll stage, file, instruments
    // or shared markers backwards while keeping a newer revision.
    if (resp.revision < this.snapshotRevWatermark) return;
    this.snapshotRevWatermark = resp.revision;
    const prev = this._state;
    const answers = { ...prev.answers };
    for (const [key, record] of Object.entries(resp.answers)) {
      const crate = Number(key);
      const existing: AnswerEntry | undefined = answers[crate];
      if (existing && existing.status !== "clean" && existing.status !== "saved") {
        // Dirty/saving/rejected drafts keep the unsent value; committed advances.
        answers[crate] = { ...existing, committed: record.location_id };
      } else {
        answers[crate] = {
          value: record.location_id,
          committed: record.location_id,
          status: existing?.status === "saved" ? "saved" : "clean",
          error: null,
        };
      }
    }
    const frozenAnswers: Record<number, string> = {};
    for (const [key, value] of Object.entries(resp.frozen_answers)) {
      const crate = Number(key);
      frozenAnswers[crate] = value;
      const existing: AnswerEntry | undefined = answers[crate];
      answers[crate] = {
        value: existing?.status === "rejected" ? existing.value : value,
        committed: value,
        status: existing?.status === "rejected" ? "rejected" : "clean",
        error: existing?.error ?? null,
      };
    }
    const fileOrder = mergeOrder(
      prev.fileOrder,
      resp.file.map((r) => r.record_id),
    );
    this.emit({
      revision: Math.max(prev.revision, resp.revision),
      file: resp.file,
      fileOrder,
      delivered: Object.fromEntries(resp.delivered.map((r) => [r.record_id, r])),
      sharedRecordIds: resp.shared ?? [],
      assignedCrates: resp.assigned_crates,
      locations: resp.locations,
      crateItems: Object.fromEntries(
        Object.entries(resp.crate_contents ?? {}).map(([k, v]) => [Number(k), v]),
      ),
      answers,
      frozenAnswers,
      channels: resp.channels ?? [],
      structuredRequests: resp.structured_requests ?? true,
      aiAssistant: resp.ai_assistant ?? true,
      timePressure: resp.time_pressure ?? true,
      bonusPayment: resp.bonus_payment ?? true,
      taskShape: resp.task_shape
        ? {
            nCrates: resp.task_shape.n_crates,
            nAssigned: resp.task_shape.n_assigned,
            crateOverlap: resp.task_shape.crate_overlap,
            intelBudget: resp.task_shape.intel_budget,
          }
        : prev.taskShape,
      soloReviewSeconds: resp.solo_review_seconds ?? null,
      worldReady: resp.world_ready,
      instrument: resp.instrument,
      material: resp.material,
      exposure: resp.exposure,
      practice: resp.practice,
      qa: resp.qa ?? [],
    });
    if (resp.stage !== prev.stage) this.setStage(resp.stage);
    if (POST_TASK_STAGES.has(resp.stage)) this.emit({ taskClosed: true });
    if (!CHECKIN_STAGES.has(resp.stage) && this._state.checkin.phase !== "closed") {
      this.emit({ checkin: { ...this._state.checkin, phase: "closed" } });
      this.reflushDirtyAnswers();
    }
  }

  /** A dirty answer draft refused mid-check-in gets another save attempt when
   * the pause lifts — see flushAnswer's transient wrong_stage handling. */
  private reflushDirtyAnswers(): void {
    for (const [crate, entry] of Object.entries(this._state.answers)) {
      if (entry.status === "dirty" && entry.value !== "") {
        this.requeueFlush(Number(crate));
      }
    }
  }

  // ----- event application -----------------------------------------------------------

  /**
   * Apply one batch of participant-visible events. ``replayed`` marks events that
   * arrived during catch-up (initial replay or resync) as opposed to live ones.
   * A replayed ``seq`` bound to a different ``event_id`` is an integrity violation:
   * the store triggers a full resync and reports false.
   */
  applyEvents(
    events: ParticipantEvent[],
    opts: { replayed?: boolean; nextEventSeq?: number; scannedSeq?: number } = {},
  ): boolean {
    const replayed = opts.replayed ?? false;
    const sorted = [...events].sort((a, b) => a.seq - b.seq);
    let cursor = this._state.cursor;
    let revision = this._state.revision;
    let clock = this._state.clock;
    let stageHint = false;
    let integrityOk = true;
    const consumed: ParticipantEvent[] = [];
    for (const e of sorted) {
      if (e.seq <= cursor) {
        const known = this.seen.get(e.seq);
        if (known !== undefined && known !== e.event_id) integrityOk = false;
        continue;
      }
      this.seen.set(e.seq, e.event_id);
      cursor = e.seq;
      revision = Math.max(revision, e.revision);
      clock = anchorFromEvent(clock, e, this.deps.now()) ?? clock;
      if (STAGE_HINT_EVENTS.has(e.type)) stageHint = true;
      consumed.push(e);
    }
    // Internal events never appear in the page — the poll cursor still advances to
    // the last scanned seq so internal-only runs are not rescanned forever (BA-17).
    if (opts.scannedSeq !== undefined) cursor = Math.max(cursor, opts.scannedSeq);
    const headSeq =
      opts.nextEventSeq !== undefined
        ? Math.max(this._state.headSeq, opts.nextEventSeq - 1)
        : this._state.headSeq;
    this.emit({ cursor, revision, clock, headSeq });
    for (const e of consumed) {
      this.reduceEvent(e);
      this.deps.onEventApplied?.(e, replayed);
    }
    if (!integrityOk) {
      void this.resync();
      return false;
    }
    if (stageHint) {
      void this.refreshSession().catch(() => this.setConnection("disconnected"));
    }
    return true;
  }

  /** Refresh the session snapshot (authoritative stage + clock anchor). */
  async refreshSession(): Promise<void> {
    const epoch = this.sessionEpoch;
    const resp = await this.deps.api.getSession();
    if (epoch !== this.sessionEpoch) return; // fetched against a retired session
    this.applySessionResponse(resp);
  }

  async refreshWorkbench(): Promise<void> {
    const epoch = this.sessionEpoch;
    try {
      const wb = await this.deps.api.getWorkbench();
      if (epoch !== this.sessionEpoch) return; // fetched against a retired session
      this.clearWorkbenchRetry();
      this.applyWorkbenchResponse(wb);
    } catch {
      if (epoch !== this.sessionEpoch) return;
      this.setConnection("disconnected");
      // SSE can stay idle after qa_answered, so an event-driven retry alone
      // leaves a committed answer stuck at Thinking indefinitely.
      if (this.workbenchRetryTimer === null) {
        this.workbenchRetryTimer = setTimeout(() => {
          this.workbenchRetryTimer = null;
          this.queueWorkbenchRefresh();
        }, WORKBENCH_RETRY_MS);
      }
    }
  }

  private clearWorkbenchRetry(): void {
    if (this.workbenchRetryTimer !== null) clearTimeout(this.workbenchRetryTimer);
    this.workbenchRetryTimer = null;
  }

  // Per-event callers coalesce here: a burst (replay, a delivery batch) issues
  // at most one in-flight fetch plus one trailing run (UX round 5 — the new
  // record_added_to_file event would otherwise fire one fetch per replayed
  // share).
  private wbInFlight = false;
  private wbAgain = false;

  queueWorkbenchRefresh(): void {
    if (this.wbInFlight) {
      this.wbAgain = true;
      return;
    }
    this.wbInFlight = true;
    void this.refreshWorkbench().finally(() => {
      this.wbInFlight = false;
      if (this.wbAgain) {
        this.wbAgain = false;
        this.queueWorkbenchRefresh();
      }
    });
  }

  /**
   * Full resync: rewind the cursor, rebuild the event-derived projection from a complete
   * replay, then re-anchor session + workbench state. Dirty/saving/rejected drafts and
   * the user's file ordering survive — they are not event-derived.
   */
  async resync(): Promise<void> {
    if (this.resyncing) return this.resyncing;
    this.resyncing = (async () => {
      const epoch = this.sessionEpoch;
      const session = await this.deps.api.getSession();
      if (epoch !== this.sessionEpoch) return; // resync fetched against a retired session
      this.seen.clear();
      this.emit({
        cursor: 0,
        messages: [],
        presentedSeats: [],
        greetingOptions: null,
        greetingAnchorActiveMs: null,
        warningMessageId: null,
        lowTimeActive: false,
        // Preserve the check-in rather than resetting to closed: a resync
        // triggered mid-submit (stale_revision retry) replays checkin_open
        // BEFORE the response is committed — resetting to closed lets the
        // replay land on "collect", visibly re-asking a rating the
        // participant already gave (observed 2026-10-05). applySessionResponse
        // still closes it when the stage moved on; the replay rebuilds it.
        checkin: this._state.checkin,
      });
      this.applySessionResponse(session);
      // A session change inside applySessionResponse bumps the epoch — that is
      // THIS resync's session taking over, so re-anchor the fence to it. A bump
      // after this point means a different session arrived while events were in
      // flight and the replay must be dropped (BA-47).
      const establishedEpoch = this.sessionEpoch;
      const events = await this.deps.api.getEvents(0);
      if (establishedEpoch !== this.sessionEpoch) return;
      this.applyEvents(events.events, {
        replayed: true,
        nextEventSeq: events.next_event_seq,
        scannedSeq: events.scanned_seq,
      });
      await this.refreshWorkbench();
    })();
    try {
      await this.resyncing;
    } finally {
      this.resyncing = null;
    }
  }

  // ----- command dispatch ------------------------------------------------------------

  /**
   * Send one command with the current expected revision. On ``stale_revision`` the store
   * resynchronizes and retries with a NEW command id (the old one is a stored rejection
   * receipt). Draft values are untouched throughout.
   */
  async dispatch(kind: CommandKind, payload: Record<string, unknown>): Promise<CommandResult> {
    // BA-26: a mutating command never reaches the network unless this tab holds a
    // confirmed lease — the profile-shared ``inv_writer`` cookie would otherwise let a
    // superseded tab's queued saves write as the winning tab. ``takeover`` is the sole
    // exception (it is how the lease is reacquired); ``resume`` never comes through
    // here (the controller posts it directly). The refusal mirrors a server-side
    // ``writer_lease_held`` so the UI lands on read-only + the takeover affordance.
    if (kind !== "takeover" && this._state.writer !== "held") {
      clearWriter();
      this.emit({ writer: "readonly", leaseNotice: true });
      throw new ApiError(409, "writer lease not held by this tab", "writer_lease_held");
    }
    const epoch = this.sessionEpoch;
    let lastError: ApiError | null = null;
    for (let attempt = 0; attempt <= MAX_STALE_RETRIES; attempt += 1) {
      if (epoch !== this.sessionEpoch) {
        throw new ApiError(409, "Session changed before save", "session_changed");
      }
      const body: CommandBody = {
        command_id: this.deps.uuid?.() ?? crypto.randomUUID(),
        kind,
        expected_revision: this._state.revision,
        payload,
        client_monotonic_ms: Math.round(this.deps.now()),
      };
      this.emit({ pendingCommands: this._state.pendingCommands + 1 });
      try {
        const result = await this.deps.api.postCommand(body);
        if (epoch === this.sessionEpoch) this.applyCommandResult(result);
        return result;
      } catch (err) {
        if (epoch !== this.sessionEpoch) throw err;
        if (!(err instanceof ApiError)) throw err;
        lastError = err;
        if (err.status === 409 && err.kind === "stale_revision") {
          await this.resync().catch(() => undefined);
          continue;
        }
        if (err.status === 409 && err.kind === "writer_lease_held") {
          clearWriter();
          this.emit({ writer: "readonly", leaseNotice: true });
          throw err;
        }
        if (err.classification === "auth") {
          this.emit({ authFailed: true });
          throw err;
        }
        if (err.classification === "quota" || err.classification === "unavailable") {
          this.setConnection("disconnected");
        }
        throw err;
      } finally {
        if (epoch === this.sessionEpoch) {
          this.emit({ pendingCommands: Math.max(0, this._state.pendingCommands - 1) });
        }
      }
    }
    throw lastError ?? new ApiError(409, "stale revision retries exhausted");
  }

  private applyCommandResult(result: CommandResult): void {
    // A committed receipt proves the session is at least this revision — any
    // snapshot resolving older is stale and must not be applied (BA-47).
    this.snapshotRevWatermark = Math.max(
      this.snapshotRevWatermark,
      result.committed_revision,
    );
    this.emit({ revision: Math.max(this._state.revision, result.committed_revision) });
    const stage = result.result.stage;
    if (typeof stage === "string" && STAGE_SET.has(stage)) {
      this.setStage(stage as Stage);
    }
    if (result.result.writer_id) this.emit({ writer: "held" });
    if (typeof result.result.completion_code === "string") {
      this.emit({ completionCode: result.result.completion_code });
    }
    if (typeof result.result.completion_url === "string") {
      this.emit({ completionUrl: result.result.completion_url });
    }
    if (typeof result.result.completion_platform === "string") {
      this.emit({ completionPlatform: result.result.completion_platform });
    }
  }

  // ----- event reduction -------------------------------------------------------------

  private reduceEvent(e: ParticipantEvent): void {
    const s = this._state;
    switch (e.type) {
      case "message_delivered":
      case "message_accepted": {
        const msg = toMessage(e);
        if (msg && !s.messages.some((m) => m.id === msg.id)) {
          this.emit({ messages: insertBySeq(s.messages, msg) });
        }
        break;
      }
      case "teammate_presented": {
        const seat = typeof e.payload.seat === "string" ? e.payload.seat : null;
        if (seat && !s.presentedSeats.includes(seat)) {
          this.emit({ presentedSeats: [...s.presentedSeats, seat] });
        }
        break;
      }
      case "greeting_prompt": {
        const options = Array.isArray(e.payload.options)
          ? e.payload.options.filter((o): o is string => typeof o === "string")
          : null;
        // The 30 s window is anchored to the prompt's own active_ms: solo review
        // defers greeting_prompt delivery to channel open, so anchoring on
        // time-since-task-start would suppress the buttons in review-first
        // sessions (UX round 6, item 4).
        const stamped = typeof e.active_ms === "number" ? e.active_ms : null;
        const active = s.clock ? activeAt(s.clock, this.deps.now()) : 0;
        // Events without an active stamp (pre-fix sessions) keep the original
        // task-start window — we cannot anchor them, and re-showing stale
        // buttons on resync is worse.
        const inWindow =
          stamped !== null
            ? active - stamped < GREETING_WINDOW_MS
            : active < GREETING_WINDOW_MS;
        this.emit({
          greetingOptions: options && inWindow ? options : null,
          greetingAnchorActiveMs: stamped ?? 0,
        });
        break;
      }
      case "warning_committed": {
        const mid = typeof e.payload.message_id === "string" ? e.payload.message_id : null;
        this.emit({ lowTimeActive: true, warningMessageId: mid });
        break;
      }
      case "task_deadline":
      case "answers_frozen": {
        this.emit({ taskClosed: true });
        break;
      }
      case "checkin_open": {
        // Never regress a committed or in-flight response back to collect: a
        // resync can replay this open while the participant's rating is still
        // in flight, and re-showing the likert row reads as a second ask.
        if (s.checkin.phase === "wait" || s.checkin.phase === "reveal") break;
        const copy = isCheckinCopy(e.payload.copy) ? e.payload.copy : null;
        const assigned = numericMap(e.payload.assigned_values);
        // BA-90: anchor the displayed countdown to the event's committed
        // server_time, not client receipt — a reload/delayed replay must never
        // restart the window while the unchanged server cap keeps counting.
        // Clamp at now: a future anchor (skewed server clock) would extend the
        // claimed window, the unsafe direction.
        const serverMs = e.server_time ? Date.parse(e.server_time) : NaN;
        // Re-application during an in-flight submit keeps the entered rating
        // and disabled state; a genuinely new check-in (closed) starts clean.
        const resumed = s.checkin.phase === "collect";
        this.emit({
          checkin: {
            phase: "collect",
            copy,
            assignedValues: assigned,
            revealedValues: s.checkin.revealedValues,
            ownValue: resumed ? s.checkin.ownValue : null,
            submitStatus: resumed ? s.checkin.submitStatus : "clean",
            openedAtMs: Number.isNaN(serverMs) ? Date.now() : Math.min(serverMs, Date.now()),
          },
        });
        break;
      }
      case "checkin_response": {
        // A replayed response must never pull the reveal back to waiting.
        if (s.checkin.phase !== "reveal") {
          this.emit({ checkin: { ...s.checkin, phase: "wait" } });
        }
        break;
      }
      case "checkin_reveal": {
        const revealed = numericMap(e.payload.assigned_values) ?? s.checkin.assignedValues;
        // The reveal payload carries the committed own rating — restore it on resync so
        // the persistent strip can show "You" alongside the teammate chips (demo parity).
        const own =
          typeof e.payload.own_rating === "number" ? e.payload.own_rating : s.checkin.ownValue;
        this.emit({
          checkin: {
            ...s.checkin,
            phase: "reveal",
            revealedValues: revealed,
            assignedValues: revealed ?? s.checkin.assignedValues,
            ownValue: own,
          },
        });
        break;
      }
      case "stage_proceeded": {
        if (s.checkin.phase !== "closed") {
          this.emit({ checkin: { ...s.checkin, phase: "closed" } });
        }
        break;
      }
      case "answer_updated": {
        const crate = typeof e.payload.crate === "number" ? e.payload.crate : null;
        const location =
          typeof e.payload.location_id === "string" ? e.payload.location_id : null;
        if (crate !== null && location !== null) {
          const existing: AnswerEntry | undefined = s.answers[crate];
          const unsent =
            existing !== undefined &&
            (existing.status === "dirty" ||
              existing.status === "saving" ||
              existing.status === "rejected");
          const answers = { ...s.answers };
          answers[crate] = {
            value: unsent ? existing.value : location,
            committed: location,
            status: unsent ? existing.status : "clean",
            error: existing?.error ?? null,
          };
          this.emit({ answers });
        }
        break;
      }
      case "record_shared": {
        // A participant share is its own durable artifact (no separate message row) —
        // render it in the chat like the demo's share bubble with the record attached.
        const crate = typeof e.payload.crate === "number" ? e.payload.crate : null;
        const recordId =
          typeof e.payload.record_id === "string" ? e.payload.record_id : null;
        const id = `share-${e.seq}`;
        if (e.actor === "participant" && recordId !== null && !s.sharedRecordIds.includes(recordId)) {
          this.emit({ sharedRecordIds: [...s.sharedRecordIds, recordId] });
        }
        if (e.actor === "participant" && !s.messages.some((m) => m.id === id)) {
          this.emit({
            messages: insertBySeq(s.messages, {
              id,
              channel: "team",
              actor: "participant",
              mine: true,
              body: crate !== null ? `Sharing intel on Crate ${crate}` : "Sharing intel",
              crate,
              recordId,
              seq: e.seq,
            }),
          });
        }
        this.queueWorkbenchRefresh();
        break;
      }
      case "record_added_to_file": {
        // UX round 5 (items 1–2): the event carries the full record — apply it
        // synchronously so the teammate's just-delivered chat card renders the
        // intel in the same pass as its message (no "Attached record …"
        // fallback) and the case file gains the card immediately. The coalesced
        // workbench refetch reconciles order and any missed fields.
        const p = e.payload;
        if (
          typeof p.record_id === "string" &&
          typeof p.body === "string" &&
          !s.file.some((r) => r.record_id === p.record_id)
        ) {
          const rec: FileRecord = {
            record_id: p.record_id,
            crate: typeof p.crate === "number" ? p.crate : 0,
            stamp: typeof p.stamp === "string" ? p.stamp : "",
            body: p.body,
            event_type: typeof p.event_type === "string" ? p.event_type : "arrival",
            source: typeof p.source === "string" ? p.source : "added_from_team",
            shared_by: typeof p.shared_by === "string" ? p.shared_by : null,
          };
          this.emit({
            file: [...s.file, rec],
            // UX v7: the new entry belongs at the top of the case file even
            // before the coalesced refetch reconciles order.
            fileOrder: [rec.record_id, ...s.fileOrder],
          });
        }
        this.queueWorkbenchRefresh();
        break;
      }
      case "structured_response_delivered": {
        // File membership / delivered-record content changed → refetch the projection.
        this.queueWorkbenchRefresh();
        break;
      }
      case "qa_asked":
      case "qa_answered": {
        // AI_QA_ENABLED: an exchange committed or resolved — the qa projection
        // on the workbench snapshot carries the question/answer text.
        this.queueWorkbenchRefresh();
        break;
      }
      case "technical_stop": {
        this.emit({
          terminalReason:
            typeof e.payload.outcome === "string" ? e.payload.outcome : s.terminalReason,
          taskClosed: true,
        });
        break;
      }
      case "writer_takeover": {
        if (s.writer !== "held") this.emit({ writer: "readonly" });
        break;
      }
      default:
        break;
    }
  }

  // ----- polling ---------------------------------------------------------------------

  /** One polling cycle — the SSE fallback path (and the only path without EventSource). */
  async pollOnce(): Promise<void> {
    try {
      const resp = await this.deps.api.getEvents(this._state.cursor);
      this.applyEvents(resp.events, { nextEventSeq: resp.next_event_seq });
      if (this._state.connection !== "live") this.setConnection("polling");
    } catch (err) {
      if (err instanceof ApiError && err.classification === "auth") {
        this.emit({ authFailed: true });
      }
      this.setConnection("disconnected");
      throw err;
    }
  }

  // ----- user actions ------------------------------------------------------------------

  /** Record an unsent answer edit; it stays dirty until the command acks. */
  setAnswerDraft(crate: number, value: string): void {
    const prev: AnswerEntry | undefined = this._state.answers[crate];
    const entry: AnswerEntry = {
      value,
      committed: prev?.committed ?? "",
      status: "dirty",
      error: null,
    };
    this.emit({ answers: { ...this._state.answers, [crate]: entry } });
    const timer = this.flushTimers.get(crate);
    if (timer) clearTimeout(timer);
    this.flushTimers.set(
      crate,
      setTimeout(() => void this.flushAnswer(crate), ANSWER_DEBOUNCE_MS),
    );
  }

  // BA-43: exactly one in-flight save per crate. An acknowledgement updates the
  // committed baseline for the value it actually saved — it must never mark a newer,
  // unsent draft as saved (that is silent response loss).
  private answerInFlight = new Set<number>();
  private answerSaveTail: Promise<void> | null = null;

  private requeueFlush(crate: number): void {
    const existing = this.flushTimers.get(crate);
    if (existing) clearTimeout(existing);
    this.flushTimers.set(
      crate,
      setTimeout(() => void this.flushAnswer(crate), ANSWER_DEBOUNCE_MS),
    );
  }

  /** Flush a dirty answer draft. Never reports Saved on failure. */
  async flushAnswer(crate: number): Promise<void> {
    const epoch = this.sessionEpoch;
    const entry: AnswerEntry | undefined = this._state.answers[crate];
    if (!entry || this.answerInFlight.has(crate)) return;
    if (entry.value === "") {
      // Clearing back to "no answer" restores the committed value; removal of a saved
      // answer is not a supported command kind.
      this.emit({
        answers: {
          ...this._state.answers,
          [crate]: { ...entry, value: entry.committed, status: "clean" },
        },
      });
      return;
    }
    if (entry.value === entry.committed && entry.status !== "rejected") {
      this.emit({
        answers: { ...this._state.answers, [crate]: { ...entry, status: "clean" } },
      });
      return;
    }
    const value = entry.value;
    this.answerInFlight.add(crate);
    this.emit({
      answers: { ...this._state.answers, [crate]: { ...entry, status: "saving" } },
    });
    try {
      // Different crates share the session revision. Serialize their saves so
      // rapid edits cannot consume every stale-revision retry against each other.
      const send = (): Promise<CommandResult | void> => epoch === this.sessionEpoch
        ? this.dispatch("answer_update", { crate, location_id: value })
        : Promise.resolve();
      const pending = this.answerSaveTail ? this.answerSaveTail.then(send) : send();
      const tail = pending.then(() => undefined, () => undefined);
      this.answerSaveTail = tail;
      void tail.then(() => {
        if (this.answerSaveTail === tail) this.answerSaveTail = null;
      });
      await pending;
      if (epoch !== this.sessionEpoch) return;
      const current: AnswerEntry | undefined = this._state.answers[crate];
      if (current !== undefined && current.value !== value) {
        // A newer draft was entered while this save was in flight: the ack only
        // commits the sent value — the draft stays dirty and gets its own save.
        this.emit({
          answers: {
            ...this._state.answers,
            [crate]: { value: current.value, committed: value, status: "dirty", error: null },
          },
        });
        this.requeueFlush(crate);
      } else {
        this.emit({
          answers: {
            ...this._state.answers,
            [crate]: { value, committed: value, status: "saved", error: null },
          },
        });
        this.scheduleSavedFade(crate);
      }
    } catch (err) {
      if (epoch !== this.sessionEpoch) return;
      const reason = err instanceof ApiError ? (err.kind ?? "save_failed") : "save_failed";
      const current: AnswerEntry | undefined = this._state.answers[crate];
      // A wrong_stage refusal during the check-in pause is transient — the
      // task resumes on reveal-close and the draft re-flushes then (v15:
      // observed a picker pick land inside the pause, get wrong_stage, and
      // freeze the crate as "Not saved — task closed"). wrong_stage is only
      // terminal once the task has genuinely closed.
      const terminal =
        reason === "task_closed" ||
        (reason === "wrong_stage" && this._state.taskClosed);
      this.emit({
        answers: {
          ...this._state.answers,
          [crate]: {
            value: current?.value ?? value,
            committed: current?.committed ?? "",
            // A lease refusal is not a rejection — takeover can still save the draft.
            status: terminal ? "rejected" : "dirty",
            error: terminal ? "closed" : reason,
          },
        },
      });
      // A newer unsent draft still deserves a save attempt; the failed value itself
      // is NOT retried automatically (a persistent rejection must not loop).
      if (!terminal && current !== undefined && current.value !== value && current.value !== "") {
        this.requeueFlush(crate);
      }
    } finally {
      if (epoch === this.sessionEpoch) this.answerInFlight.delete(crate);
    }
  }

  private scheduleSavedFade(crate: number): void {
    const existing = this.savedTimers.get(crate);
    if (existing) clearTimeout(existing);
    this.savedTimers.set(
      crate,
      setTimeout(() => {
        const entry: AnswerEntry | undefined = this._state.answers[crate];
        if (entry && entry.status === "saved") {
          this.emit({
            answers: { ...this._state.answers, [crate]: { ...entry, status: "clean" } },
          });
        }
      }, SAVED_FADE_MS),
    );
  }

  async sendTeamReply(body: string, to?: string): Promise<CommandResult> {
    const payload: Record<string, unknown> = { body };
    if (to === "PM" || to === "JT") payload.to = to;
    return this.dispatch("social_reply", payload);
  }

  async sendGreeting(text: string): Promise<CommandResult> {
    const result = await this.dispatch("greeting", { text });
    this.emit({ greetingOptions: null, greetingAnchorActiveMs: null });
    return result;
  }

  async askTeam(crate: number): Promise<CommandResult> {
    return this.dispatch("teammate_request", { crate });
  }

  async askAssistant(body: string, crate?: number): Promise<CommandResult> {
    const payload: Record<string, unknown> = {};
    if (body) payload.body = body;
    if (crate !== undefined) payload.crate = crate;
    return this.dispatch("assistant_request", payload);
  }

  /**
   * UX round 3, item 6: the committed receipt proves the share is durable, so the
   * record is marked shared immediately — the control goes "Sharing…" → "Shared in
   * team chat" without flashing back to "Share" while the record_shared event /
   * workbench refresh is still in flight. A server-side duplicate refusal means it
   * is already shared, too.
   */
  async shareRecord(recordId: string): Promise<CommandResult> {
    const markShared = () => {
      if (!this._state.sharedRecordIds.includes(recordId)) {
        this.emit({ sharedRecordIds: [...this._state.sharedRecordIds, recordId] });
      }
    };
    try {
      const result = await this.dispatch("record_share", { record_id: recordId });
      markShared();
      return result;
    } catch (err) {
      if (err instanceof ApiError && err.kind === "record_already_shared") markShared();
      throw err;
    }
  }

  async submitCheckin(value: number): Promise<CommandResult> {
    // Optimistic wait: the click is the participant's committed intent —
    // show "collecting" immediately instead of leaving the likert row up
    // while the command (plus any stale_revision resync+retry) resolves.
    this.emit({
      checkin: { ...this._state.checkin, ownValue: value, phase: "wait", submitStatus: "saving" },
    });
    try {
      const result = await this.dispatch("pressure_response", { value });
      this.emit({
        checkin: {
          ...this._state.checkin,
          ownValue: value,
          // A reveal that landed during the round-trip stays ahead.
          phase: this._state.checkin.phase === "reveal" ? "reveal" : "wait",
          submitStatus: "saved",
        },
      });
      return result;
    } catch (err) {
      this.emit({
        checkin: {
          ...this._state.checkin,
          // Revert only the optimistic wait — a reveal is committed state and
          // outranks this failure.
          phase: this._state.checkin.phase === "reveal" ? "reveal" : "collect",
          submitStatus: "rejected",
        },
      });
      throw err;
    }
  }

  /** AI_QA_ENABLED: submit a rules question on the ai_qa page. */
  async askQa(question: string): Promise<CommandResult> {
    const result = await this.dispatch("qa_ask", { question });
    // The exchange row lands with the command — refetch the projection so the
    // pending question renders immediately (the answer arrives via event).
    this.queueWorkbenchRefresh();
    return result;
  }

  async proceed(): Promise<CommandResult> {
    return this.dispatch("proceed", {});
  }

  /**
   * Participant-initiated withdrawal (INV-0908): reachable from every pre-terminal
   * screen. The command commits the terminal stage; a session refresh picks up the
   * new stage + terminal_reason so the terminal screen renders immediately.
   */
  async withdraw(): Promise<CommandResult> {
    const result = await this.dispatch("withdraw", {});
    await this.refreshSession().catch(() => this.setConnection("disconnected"));
    return result;
  }

  private completeId: string | null = null;

  /**
   * Idempotent completion handoff (POST /complete). An uncertain outcome (network
   * failure, lost response) retries the SAME command id — the receipt replay returns
   * the committed result, restoring the completion code. A persisted rejection is
   * final for that id, so ``stale_revision`` recovery must resync and mint a NEW id —
   * the same shape ``dispatch`` already uses (BA-44).
   */
  async complete(): Promise<CommandResult> {
    // Same lease gate as dispatch (BA-26): /complete is a mutating command.
    if (this._state.writer !== "held") {
      clearWriter();
      this.emit({ writer: "readonly", leaseNotice: true });
      throw new ApiError(409, "writer lease not held by this tab", "writer_lease_held");
    }
    let lastError: ApiError | null = null;
    for (let attempt = 0; attempt <= MAX_STALE_RETRIES; attempt += 1) {
      const commandId = (this.completeId ??= this.deps.uuid?.() ?? crypto.randomUUID());
      try {
        const result = await this.deps.api.postComplete({
          command_id: commandId,
          expected_revision: this._state.revision,
          client_monotonic_ms: Math.round(this.deps.now()),
        });
        this.applyCommandResult(result);
        return result;
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        lastError = err;
        if (err.status === 409 && err.kind === "stale_revision") {
          // The rejection receipt is persisted against this id — replaying it can
          // never succeed. Resync for the current revision and allocate a new id.
          this.completeId = null;
          await this.resync().catch(() => undefined);
          continue;
        }
        if (err.status === 409 && err.kind === "writer_lease_held") {
          clearWriter();
          this.emit({ writer: "readonly", leaseNotice: true });
          throw err;
        }
        if (err.classification === "auth") {
          this.emit({ authFailed: true });
          throw err;
        }
        if (err.classification === "quota" || err.classification === "unavailable") {
          this.setConnection("disconnected");
        }
        throw err;
      }
    }
    throw lastError ?? new ApiError(409, "stale revision retries exhausted");
  }

  /**
   * Final handoff chain (BA-44): debrief ack → recorded data-use choice →
   * completion. ``debrief_ack`` transitions the stage mid-chain, which UNMOUNTS
   * the screen that started it — an error raised afterward would be written into
   * dead component state. Keeping it in ``handoffError`` lets whichever screen is
   * on stage render the failure, and each step re-checks the live stage so a
   * resync-forwarded session skips already-committed commands instead of
   * re-submitting them.
   */
  async finishHandoff(): Promise<void> {
    // Only a chain started on the debrief hides the intermediate stage; Finish on
    // the resume screen must keep that screen up.
    this.emit({ handoffError: null, handoffInFlight: this._state.stage === "debrief" });
    try {
      if (this._state.stage === "debrief") {
        await this.dispatch("debrief_ack", {});
      }
      if (this._state.stage === "debrief") {
        // The ack committed but its result carried no stage — re-anchor rather
        // than skip the disposition the completion invariant requires.
        await this.refreshSession().catch(() => undefined);
      }
      if (this._state.stage === "data_use_choice") {
        await this.dispatch("data_use_choice", { choice: "use_all" });
      }
      await this.complete();
    } catch (err) {
      this.emit({
        handoffError: "That did not go through — please try again.",
      });
      throw err;
    } finally {
      this.emit({ handoffInFlight: false });
    }
  }

  async takeover(): Promise<CommandResult> {
    const result = await this.dispatch("takeover", {});
    const wid = result.result.writer_id;
    if (typeof wid === "string") {
      adoptWriter(wid);
      if (this._state.sessionId) writeClaim(this._state.sessionId, tabId());
    }
    this.emit({ writer: "held", leaseNotice: false });
    return result;
  }

  async itemResponse(
    itemId: string,
    value: number | string,
    extra: { crate?: number; seat?: string } = {},
  ): Promise<CommandResult> {
    const payload: Record<string, unknown> = { item_id: itemId, value };
    if (extra.crate !== undefined) payload.crate = extra.crate;
    if (extra.seat !== undefined) payload.seat = extra.seat;
    return this.dispatch("item_response", payload);
  }

  async itemSkip(
    itemId: string,
    extra: { crate?: number; seat?: string; missing_reason?: string } = {},
  ): Promise<CommandResult> {
    const payload: Record<string, unknown> = { item_id: itemId };
    if (extra.crate !== undefined) payload.crate = extra.crate;
    if (extra.seat !== undefined) payload.seat = extra.seat;
    if (extra.missing_reason !== undefined) payload.missing_reason = extra.missing_reason;
    return this.dispatch("item_skip", payload);
  }

  /** Reorder the case file display (client-side only; unknown ids are dropped). */
  reorderFile(order: string[]): void {
    const fileIds = this._state.file.map((r) => r.record_id);
    this.emit({ fileOrder: mergeOrder(order, fileIds) });
  }

  moveFileRecord(recordId: string, delta: -1 | 1): void {
    const order = [...this._state.fileOrder];
    const i = order.indexOf(recordId);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= order.length) return;
    const a = order[i] as string;
    order[i] = order[j] as string;
    order[j] = a;
    this.emit({ fileOrder: order });
  }

  dispose(): void {
    this.sessionEpoch += 1; // fence every in-flight read (BA-47)
    this.clearWorkbenchRetry();
    for (const t of this.flushTimers.values()) clearTimeout(t);
    for (const t of this.savedTimers.values()) clearTimeout(t);
    this.flushTimers.clear();
    this.savedTimers.clear();
    this.listeners.clear();
    this.resyncing = null;
  }
}

function insertBySeq(list: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  const next = [...list];
  let i = next.length;
  while (i > 0 && (next[i - 1] as ChatMessage).seq > msg.seq) i -= 1;
  next.splice(i, 0, msg);
  return next;
}

function toMessage(e: ParticipantEvent): ChatMessage | null {
  const body = typeof e.payload.body === "string" ? e.payload.body : null;
  const id = typeof e.payload.message_id === "string" ? e.payload.message_id : null;
  if (body === null || id === null) return null;
  const rawChannel = e.payload.channel;
  const channel: ChatMessage["channel"] =
    rawChannel === "assistant" ? "assistant" : rawChannel === "system" ? "system" : "team";
  const crate = typeof e.payload.crate === "number" ? e.payload.crate : null;
  const recordId = typeof e.payload.record_id === "string" ? e.payload.record_id : null;
  return {
    id,
    channel,
    actor: e.actor,
    mine: e.actor === "participant",
    body,
    crate,
    recordId,
    seq: e.seq,
  };
}

function isCheckinCopy(value: unknown): value is CheckinCopy {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.question === "string" && typeof v.collecting === "string";
}

function numericMap(value: unknown): Record<string, number> | null {
  if (typeof value !== "object" || value === null) return null;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(value)) {
    if (typeof v === "number") out[k] = v;
  }
  return Object.keys(out).length ? out : null;
}

/** BA-26: mutating controls are enabled only on a confirmed, un-revoked lease —
 * ``unknown`` (boot resume still pending) is just as locked as ``readonly``. */
export function canWrite(state: StoreState): boolean {
  return state.writer === "held" && !state.authFailed;
}

/** Merge a user ordering over newly arrived file records — UX v7 (2026-10-11):
 * new ids land at the TOP so incoming teammate intel is the first thing seen;
 * the participant's own ordering of existing records is preserved. */
export function mergeOrder(order: string[], fileIds: string[]): string[] {
  const fileSet = new Set(fileIds);
  const kept = order.filter((id) => fileSet.has(id));
  const keptSet = new Set(kept);
  return [...fileIds.filter((id) => !keptSet.has(id)), ...kept];
}
