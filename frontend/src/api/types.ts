/**
 * Participant API contracts (INV-0801; spec §8.2). These mirror the wire shapes of
 * backend/app/api/participant.py exactly; nothing here may carry condition, truth or
 * agent-implementation fields (INV-0809).
 */

export const STAGES = [
  "entry",
  "consent",
  "instructions",
  "training",
  "ai_qa",
  "scripted_connecting",
  "ready",
  "solo_review",
  "task",
  "checkin_collect",
  "checkin_wait",
  "checkin_reveal",
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
] as const;

export type Stage = (typeof STAGES)[number];

export const TERMINAL_STAGES: ReadonlySet<Stage> = new Set([
  "declined",
  "withdrawn",
  "technical_stop",
  "abandoned",
]);

/** Stages where the simultaneous workbench stays mounted (the check-in overlays it). */
export const WORKBENCH_STAGES: ReadonlySet<Stage> = new Set([
  "solo_review",
  "task",
  "checkin_collect",
  "checkin_wait",
  "checkin_reveal",
]);

export type CommandKind =
  | "consent"
  | "set_initials"
  | "training_step"
  | "qa_ask"
  | "ready"
  | "withdraw"
  | "answer_update"
  | "pressure_response"
  | "pressure_skip"
  | "item_response"
  | "item_skip"
  | "debrief_ack"
  | "proceed"
  | "data_use_choice"
  | "resume"
  | "takeover"
  | "teammate_request"
  | "record_share"
  | "social_reply"
  | "assistant_request"
  | "greeting";

export interface EntryResponse {
  session_id: string;
  resumed: boolean;
  stage: Stage;
}

export interface SessionSnapshot {
  session_id: string;
  stage: Stage;
  revision: number;
  mode: string;
  manifest_id: string;
  study_key: string;
  timing_mode: string;
  next_event_seq: number;
  compatibility_key: string;
  terminal_reason: string | null;
  /** Recoverable completion handoff code (INV-0907) — present once the receipt exists. */
  completion_code: string | null;
  completion_url?: string | null;
  completion_platform?: string | null;
  /** Server-stored participant initials entered on the background page (UX round 2).
   * Identity-adjacent: never present in research exports. */
  initials: string | null;
  writer_held: boolean;
  stage_entered_at: string | null;
}

export interface ClockState {
  server_now: string;
  active_ms: number;
  budget_ms: number | null;
  clock_running: boolean;
}

export interface SessionResponse {
  session: SessionSnapshot;
  clock: ClockState;
}

/** One participant-visible event, already scrubbed by the server allowlist. */
export interface ParticipantEvent {
  seq: number;
  event_id: string;
  stage: Stage;
  actor: string;
  type: string;
  revision: number;
  active_ms: number | null;
  server_time: string | null;
  payload: Record<string, unknown>;
}

export interface EventsResponse {
  events: ParticipantEvent[];
  /** Last event seq the server scanned for this page (internal events included);
   * the poll cursor advances to it even when the visible page is empty (BA-17). */
  scanned_seq: number;
  next_event_seq: number;
}

export interface CommandResult {
  command_id: string;
  accepted: boolean;
  reason: string | null;
  committed_revision: number;
  result: Record<string, unknown>;
  event_seqs: number[];
  replayed: boolean;
}

export interface FileRecord {
  record_id: string;
  crate: number;
  stamp: string;
  body: string;
  event_type: "arrival" | "still_at" | "departure" | "transit" | string;
  source: string;
  /** O-4: for shared cards, the teammate seat that shared the record. */
  shared_by?: string | null;
}

export interface AnswerRecord {
  location_id: string;
  revision: number;
}

export interface InstrumentItem {
  item_id: string;
  text: string;
  skippable: boolean;
  /** UX round 4, item 14: when present the item is a fixed-choice pick — the
   * stored value is the chosen option's text. */
  options?: string[] | null;
  /** UX round 8: free-text items (the open-ended follow-ups) render a textarea
   * and store the typed string. */
  free_text?: boolean;
}

export interface InstrumentProjection {
  stage: Stage;
  instrument_id: string;
  instrument_version: number;
  applies_to: "session" | "per_answered_crate" | "per_teammate_seat";
  response: {
    /** ``free_text`` (UX round 8 open-ended stage): min/max carry the character
     * bound, no scale labels. */
    type: "likert" | "slider" | "free_text";
    min: number;
    max: number;
    min_label: string;
    max_label: string;
  };
  items: InstrumentItem[];
  seats: string[];
  answered_items: string[];
  answered_crates: number[];
  pending_crates: number[];
}

export type ChannelName =
  | "file_work"
  | "answer_drafting"
  | "structured_seeking"
  | "sharing"
  | "social_reply"
  | "assistant_prompting";

/**
 * Literal participant-facing copy published inside a ``material_reference`` artifact
 * (BA-37). When present, the UI renders this verbatim — never component literals.
 * Absent marks an unresolved placeholder version whose wording is only a pointer to
 * ``reference_copy``.
 */
export interface MaterialText {
  heading?: string;
  subheading?: string;
  sections: { title?: string; paragraphs: string[] }[];
  question?: string;
  accept_label?: string;
  decline_label?: string;
}

/**
 * Bound participant material (INV-0901): the ``material_reference`` artifact the session
 * pinned for this stage — key, version, status and body verbatim. ``body.text`` carries
 * the literal published copy when the bound version contains it.
 */
export interface MaterialProjection {
  slot: string;
  artifact_key: string;
  artifact_id: string;
  artifact_version: number;
  status: string;
  body: {
    material?: string;
    reference_copy?: string;
    freeze_decision?: string;
    text?: MaterialText;
  };
}

/**
 * PE exposure surfaces for the current stage (INV-0904): resolved copy for this
 * session's bound arm, without the arm label or artifact key. Empty control-arm
 * surfaces are absent entirely.
 */
export type ExposureSurface =
  | "intro"
  | "callout"
  | "strip"
  | "system_message"
  | "peer_intro";
export type ExposureProjection = Partial<Record<ExposureSurface, string>>;

/** One record in the fixed practice world (INV-0901). */
export interface PracticeRecord {
  record_id: string;
  crate: number;
  stamp: string;
  body: string;
}

/** The fixed practice surface payload served during ``training``. */
export interface PracticeWorld {
  records: PracticeRecord[];
  /** A record a practice teammate already shared into the chat — the message
   * previews what teammate shares look like during the task. */
  shared: PracticeRecord | null;
  locations: string[];
  actions: string[];
  replies: Record<string, string>;
  /** BA-106: bound-training-version gate — ``training.reference@v5`` builds
   * required the typed ask; v6+ gate on the share alone. Drives required/
   * optional copy and the Continue gate. */
  ask_required?: boolean;
  /** AI_ASSISTANT_ENABLED: false when the practice payload has no assistant
   * surface (no assistant replies, no assistant practice page). Absent on
   * older servers, treated as true. */
  ai_assistant?: boolean;
}

/** AI_QA_ENABLED: one committed rules-Q&A exchange on the ``ai_qa`` stage —
 * ``answer`` is null while the answering job is still pending. */
export interface QaExchange {
  seq: number;
  question: string;
  answer: string | null;
  status: "pending" | "answered" | "fallback";
}

export interface WorkbenchResponse {
  stage: Stage;
  revision: number;
  server_now: string;
  world_ready: boolean;
  locations: string[];
  /** SQ-11: fixed crate→item map ("7" → "tablets") for picker/dropdown labels. */
  crate_contents?: Record<string, string>;
  assigned_crates: number[];
  file: FileRecord[];
  delivered: FileRecord[];
  /** Record ids the participant has already shared to the team chat (durable — the
   * UI shows "Shared in team chat" in place of the Share affordance). */
  shared: string[];
  answers: Record<string, AnswerRecord>;
  frozen_answers: Record<string, string>;
  channels: ChannelName[] | null;
  /** FR-1 interaction mode (UX round 3): when false the per-crate request controls
   * are not offered — the participant asks teammates and the assistant in their own
   * words. Absent on older servers, treated as true. */
  structured_requests?: boolean;
  /** Seconds of the bound solo-review phase, when the manifest has one — lets
   * instructions pre-announce the locked-chat opening honestly. */
  solo_review_seconds?: number | null;
  instrument: InstrumentProjection | null;
  material: MaterialProjection | null;
  exposure: ExposureProjection | null;
  practice: PracticeWorld | null;
  /** AI_QA_ENABLED: the session's rules-Q&A exchanges on the ``ai_qa`` stage
   * (null on every other stage — the stage itself is the flag signal). */
  qa?: QaExchange[] | null;
  /** AI_ASSISTANT_ENABLED: false removes the assistant pane, assistant channel
   * and assistant-specific practice/instrument items experiment-wide. Absent
   * on older servers, treated as true. */
  ai_assistant?: boolean;
  /** TIME_PRESSURE_ENABLED: false removes the manipulation experiment-wide —
   * no check-in pop-up, no "One minute left" warning, no low-time styling (the
   * countdown stays as a neutral timer), and no rushed-feeling questionnaire
   * items. Absent on older servers, treated as true. */
  time_pressure?: boolean;
  /** BONUS_PAYMENT_ENABLED: false removes the $0.50 accuracy bonus
   * experiment-wide — the case-background, gameplay and debrief bonus lines
   * drop out of the rendered copy. The consent artifact and payment policy
   * are substituted server-side. Absent on older servers, treated as true. */
  bonus_payment?: boolean;
  /** 2026-10-15 crate-condition experiment: the bound manifest's task shape —
   * drives crate-count and overlap wording on instructions/connecting screens.
   * Absent on older servers, treated as the legacy 8-crate independent deal. */
  task_shape?: {
    n_crates: number;
    n_assigned: number;
    crate_overlap: "independent" | "none" | "some" | "full";
    intel_budget: number | null;
  };
}

/**
 * Bounded telemetry kinds (INV-0807; spec §8.2). The server stamps ``telemetry_<kind>``
 * events; this union is the whole client-side vocabulary — no keystroke or content
 * streams are emitted.
 */
export type TelemetryKind =
  | "receive"
  | "render"
  | "render_first_final"
  | "visibility"
  | "focus"
  | "blur"
  | "item_start"
  | "item_end"
  // 2026-10-12 case-file telemetry: ids/indexes/timings only (no content), same
  // bounded doctrine. Server whitelist must list the same kinds or items drop.
  // file_meta (2026-10-13): once-per-mount instrumentation marker — proves the
  // collector ran so "zero views" is distinguishable from "telemetry absent".
  | "file_meta"
  | "file_record_view"
  | "file_record_dwell"
  | "file_scroll_summary"
  | "file_reorder"
  | "file_order_final"
  | "share_attempt"
  // 2026-10-16 chat read-side (cb-1.11): throttled chat-pane scroll emits —
  // scroll position/at-bottom only. The only participant-visible signal that
  // the teammate channel was actually read; delivery/render receipts cannot
  // show that.
  | "chat_scroll"
  // 2026-10-18 answer-bar visibility (zero-answer cohort hypothesis): the
  // submit bar is in-flow on <900px viewports — IntersectionObserver bucket +
  // viewport geometry only, no content.
  | "submit_bar_view";

export type ConsentAction = "consented" | "declined";

export type DataUseChoice = "use_all" | "use_no_free_text" | "withdraw_all";

export interface CheckinCopy {
  question: string;
  scale_min_label: string;
  scale_max_label: string;
  note: string;
  collecting: string;
  reveal_title: string;
  reveal_note: string;
  resume_label: string;
}
