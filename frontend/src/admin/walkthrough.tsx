/**
 * Session walkthrough (INV-1303 surface): a unified, human-readable timeline over the
 * replay payload. Merges participant-visible events, internal scheduler/agent events,
 * and model calls into one ordered feed so a researcher can audit what happened —
 * messages, evidence flow, manipulation timing, answers — and how the agents behaved.
 *
 * Data source is admin replay only; nothing here issues mutations.
 */
import { useMemo, useState } from "react";

import { downloadParticipantView } from "./participantView";
import { downloadSimulation } from "./simulation";

// ----- shapes --------------------------------------------------------------------------------

type Ev = {
  seq?: number;
  type: string;
  actor?: string;
  stage?: string;
  active_ms?: number | null;
  server_time?: string | null;
  occurred_at?: string | null;
  requested_at?: string | null;
  requested_active_ms?: number | null;
  payload?: Record<string, unknown>;
};

type TimelineRow = {
  sortKey: number; // seq when present; large offset + index for table rows
  internal: boolean; // internal-only event (not in the participant projection)
  activeMs: number | null;
  wall: string | null;
  category: Category;
  actor: string;
  title: string;
  detail: string;
};

type Category =
  | "chat"
  | "assistant"
  | "evidence"
  | "answers"
  | "manipulation"
  | "stage"
  | "agent"
  | "commands"
  | "telemetry";

const CATEGORY_LABEL: Record<Category, string> = {
  chat: "team chat",
  assistant: "assistant",
  evidence: "evidence",
  answers: "answers",
  manipulation: "manipulation",
  stage: "stage",
  agent: "agents",
  commands: "commands",
  telemetry: "telemetry",
};

const CATEGORY_COLOR: Record<Category, string> = {
  chat: "#1a6ed2",
  assistant: "#6a3fb5",
  evidence: "#1d7a35",
  answers: "#b0691f",
  manipulation: "#a33",
  stage: "#555",
  agent: "#0c6e6e",
  commands: "#7a4a00",
  telemetry: "#999",
};

// ----- classification ------------------------------------------------------------------------

function categoryOf(e: Ev): Category {
  const t = e.type;
  if (t.startsWith("telemetry_")) return "telemetry";
  const payload = (e.payload ?? {}) as Record<string, unknown>;
  const channel = String(payload.channel ?? "");
  if (t === "message_accepted" || t === "message_delivered") {
    if (channel === "assistant") return "assistant";
    if (channel === "system") return "stage";
    return "chat";
  }
  switch (t) {
    case "assistant_requested":
      return "assistant";
    case "teammate_requested":
    case "scripted_request_delivered":
    case "structured_response_delivered":
    case "greeting_prompt":
    case "teammate_presented":
    case "voice_line_committed":
    case "teammate_no_holdings":
      return "chat";
    case "record_shared":
    case "record_added_to_file":
      return "evidence";
    case "answer_updated":
    case "answers_frozen":
    case "confidence_recorded":
    case "item_recorded":
    case "peer_rating_recorded":
      return "answers";
    case "assigned":
    case "exposure_committed":
    case "checkin_open":
    case "checkin_response":
    case "checkin_reveal":
    case "task_clock_started":
    case "task_deadline":
    case "warning_committed":
    case "warning_suppressed":
    case "task_schedule_committed":
      return "manipulation";
    case "generation_resolved":
    case "generation_quarantined":
    case "writer_takeover":
    case "automation_probe":
    case "model_call":
      return "agent";
    default:
      return "stage";
  }
}

// ----- human-readable titles ------------------------------------------------------------------

function clip(v: unknown, n = 160): string {
  const s = String(v ?? "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function describe(e: Ev): { title: string; detail: string } {
  const p = (e.payload ?? {}) as Record<string, unknown>;
  const t = e.type;
  switch (t) {
    case "session_opened":
      return {
        title: "Session opened",
        detail: `mode ${p.mode ?? "?"}, timing ${p.timing_mode ?? "?"}`,
      };
    case "assigned":
      return { title: "Assigned", detail: `arm ${p.arm ?? "?"}` };
    case "consent_recorded":
      return { title: "Consent", detail: String(p.action ?? "") };
    case "initials_recorded":
      return { title: "Initials recorded", detail: "" };
    case "training_started":
      return { title: "Training started", detail: "" };
    case "training_step": {
      // Comprehension attempts carry a server-computed `missed` list — surface
      // the count so the researcher sees retry behavior in the walkthrough.
      const detail = String(p.step ?? "");
      const missed = Array.isArray(p.missed) ? ` — missed ${p.missed.length}` : "";
      return { title: "Training step", detail: detail + missed };
    }
    case "stage_proceeded":
      return { title: "Stage advanced", detail: `→ ${p.to ?? "?"}` };
    case "teammate_presented":
      return { title: `${e.actor ?? "?"} presented`, detail: String(p.seat ?? "") };
    case "greeting_prompt":
      return {
        title: "Greeting options shown",
        detail: Array.isArray(p.options) ? (p.options as unknown[]).join(" / ") : "",
      };
    case "task_clock_started":
      return { title: "Task clock started", detail: String(p.stage ?? "") };
    case "task_schedule_committed":
      return { title: "Task schedule committed", detail: "" };
    case "task_deadline":
      return { title: "Task deadline reached", detail: "" };
    case "message_accepted":
      return {
        title: `Participant → ${p.channel ?? "chat"}`,
        detail: clip(p.body),
      };
    case "message_delivered": {
      const lag =
        p.scheduled_for_active_ms != null && e.active_ms != null
          ? Math.round((e.active_ms - Number(p.scheduled_for_active_ms)) / 1000)
          : null;
      return {
        title: `${e.actor ?? "?"} → ${p.channel ?? "chat"}`,
        detail:
          clip(p.body) + (lag !== null && lag > 0 ? `  (delivered +${lag}s late)` : ""),
      };
    }
    case "teammate_requested":
      return {
        title: `Teammate request → ${p.responder ?? "team"}`,
        detail: p.crate != null ? `crate ${p.crate}${p.body ? ` — ${clip(p.body, 80)}` : ""}` : clip(p.body, 80),
      };
    case "assistant_requested":
      return {
        title: "Assistant request",
        detail:
          p.crate != null ? `crate ${p.crate}${p.body ? ` — ${clip(p.body, 80)}` : ""}` : clip(p.body, 80),
      };
    case "scripted_request_delivered":
      return {
        title: "Scripted request delivered",
        detail: `crate ${p.crate ?? "?"} (request #${p.index ?? "?"})`,
      };
    case "structured_response_delivered":
      return {
        title: `${e.actor ?? "?"} delivered a record`,
        detail: `record ${p.record_id ?? "?"}`,
      };
    case "record_shared":
      return {
        title: `${e.actor ?? "?"} shared a record`,
        detail: `${p.record_id ?? "?"}${p.crate != null ? ` (crate ${p.crate})` : ""}`,
      };
    case "record_added_to_file":
      return { title: "Record filed", detail: `${p.record_id ?? "?"} auto-added to case file` };
    case "answer_updated":
      return {
        title: `Crate ${p.crate ?? "?"} answer`,
        detail: `→ ${p.location_id ?? "?"} (rev ${p.revision ?? "?"})`,
      };
    case "answers_frozen":
      return {
        title: "Answers frozen",
        detail: Array.isArray(p.crates) ? `crates ${(p.crates as unknown[]).join(", ")}` : "",
      };
    case "confidence_recorded":
      return {
        title: `Confidence — crate ${p.crate ?? "?"}`,
        detail: p.skipped ? "skipped" : "recorded",
      };
    case "item_recorded":
      return {
        title: `${p.item_id ?? "item"}`,
        detail: `${p.instrument ?? ""}${p.skipped ? " — skipped" : ""}`.trim(),
      };
    case "exposure_committed":
      return { title: "Manipulation exposure", detail: String(p.surface ?? "") };
    case "checkin_open":
      return {
        title: "Check-in opened",
        detail: clip(
          (p.copy as Record<string, unknown> | undefined)?.question ?? "",
          100,
        ),
      };
    case "checkin_response":
      return { title: "Check-in response", detail: p.skipped ? "skipped" : "recorded" };
    case "checkin_reveal": {
      const vals = (p.assigned_values ?? {}) as Record<string, unknown>;
      return {
        title: "Check-in reveal",
        detail:
          `own ${p.own_rating ?? "?"} · ` +
          Object.entries(vals)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · "),
      };
    }
    case "warning_committed":
      return { title: "Warning shown", detail: String(p.job ?? "") };
    case "warning_suppressed":
      return { title: "Warning suppressed", detail: String(p.reason ?? "") };
    case "generation_resolved": {
      const plan = (p.plan ?? {}) as Record<string, unknown>;
      return {
        title: `Generation ${p.status ?? "?"}`,
        detail:
          `${plan.mechanism ?? ""}${p.fallback ? ` · fallback ${p.fallback}` : ""}` +
          `${plan.scheduled_ms != null ? ` · scheduled ${plan.scheduled_ms}ms` : ""}` +
          `${plan.fallback_reason ? ` · reason ${plan.fallback_reason}` : ""}`.trim(),
      };
    }
    case "generation_quarantined":
      return {
        title: "Generation quarantined",
        detail: `${p.reason ?? ""} (${p.status ?? ""})`.trim(),
      };
    case "schedule_swept":
      return {
        title: "Schedule swept",
        detail: Array.isArray(p.jobs) ? (p.jobs as unknown[]).join(", ") : "",
      };
    case "writer_takeover":
      return { title: "Writer takeover", detail: "" };
    case "automation_probe":
      return { title: "Automation probe intercepted", detail: clip(p.body ?? "", 100) };
    case "session_resumed":
    case "resumed":
      return { title: "Session resumed", detail: "" };
    case "session_abandoned":
      return {
        title: "Session abandoned",
        detail: p.idle_window_s != null ? `idle ${p.idle_window_s}s` : "",
      };
    case "session_completed":
      return { title: "Session completed", detail: "" };
    case "session_terminated":
      return { title: "Session terminated", detail: clip(p.reason ?? "", 100) };
    case "technical_stop":
      return { title: "Technical stop", detail: String(p.outcome ?? p.stage ?? "") };
    case "instrument_complete":
      return { title: "Instrument complete", detail: String(p.stage ?? "") };
    case "debrief_acknowledged":
      return { title: "Debrief acknowledged", detail: "" };
    case "acknowledgment_skipped":
      return { title: "Acknowledgment skipped", detail: "" };
    case "data_use_recorded":
      return { title: "Data-use choice", detail: String(p.choice ?? "") };
    default:
      if (t.startsWith("telemetry_"))
        return {
          title: t.replace("telemetry_", "telemetry "),
          detail: clip(JSON.stringify(p.data ?? p), 120),
        };
      return { title: t.replaceAll("_", " "), detail: clip(JSON.stringify(p), 120) };
  }
}

// ----- timeline construction ------------------------------------------------------------------

function toRow(e: Ev, i: number, internal: boolean): TimelineRow {
  const { title, detail } = describe(e);
  const wall = (e.server_time ?? e.occurred_at ?? e.requested_at ?? null) as string | null;
  const activeMs = (e.active_ms ?? e.requested_active_ms ?? null) as number | null;
  return {
    sortKey: typeof e.seq === "number" ? e.seq : 1e9 + i,
    internal,
    activeMs,
    wall,
    category: categoryOf(e),
    actor: String(e.actor ?? ""),
    title,
    detail,
  };
}

/** One row per command receipt — the command ledger. Rejected commands (stale
 * revision, closed stage, lease) leave no events, so this is the only place a
 * retry shows up on the timeline. Merged by wall-clock; receipts have no seq. */
function receiptRow(r: Record<string, unknown>, i: number): TimelineRow {
  const rejected = r.outcome === "rejected";
  return {
    sortKey: 7e8 + i,
    internal: true,
    activeMs: null,
    wall: (r.received_at as string | null) ?? null,
    category: "commands",
    actor: "participant",
    title: `${r.kind ?? "?"} → ${r.outcome ?? "?"}`,
    detail: rejected
      ? `${r.reason ?? "?"} · expected rev ${r.expected_revision ?? "?"}, server at ${r.committed_revision ?? "?"}`
      : `rev ${r.expected_revision ?? "?"} → ${r.committed_revision ?? "?"}`,
  };
}

function modelCallRow(mc: Record<string, unknown>, i: number): TimelineRow {
  const model = String(mc.model ?? "").split("@")[0];
  const validation = (mc.validation ?? {}) as Record<string, unknown>;
  const err = validation.error_kind ?? validation.violations;
  return {
    sortKey: 5e8 + i, // merged by wall-clock below, seq absent
    internal: true,
    activeMs: (mc.requested_active_ms as number | null) ?? null,
    wall: (mc.requested_at as string | null) ?? null,
    category: "agent",
    actor: "model",
    title: `Model call — ${mc.status ?? "?"}`,
    detail:
      `${mc.provider ?? ""} ${model}`.trim() +
      `${mc.duration_ms != null ? ` · ${mc.duration_ms}ms` : ""}` +
      `${mc.attempt != null ? ` · attempt ${mc.attempt}` : ""}` +
      `${err ? ` · ${clip(JSON.stringify(err), 80)}` : ""}`,
  };
}

function fmtActive(ms: number | null): string {
  if (ms == null) return "—";
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtWall(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toTimeString().slice(0, 8);
}

// ----- component ------------------------------------------------------------------------------

export function Walkthrough({ replay }: { replay: Record<string, unknown> }) {
  const [showTelemetry, setShowTelemetry] = useState(false);
  const [showAgent, setShowAgent] = useState(true);
  const [showInternal, setShowInternal] = useState(true);
  const [filter, setFilter] = useState<Category | null>(null);

  const session = (replay.session ?? {}) as Record<string, unknown>;

  const { rows, counts, truncated } = useMemo(() => {
    const participant = (replay.participant ?? {}) as Record<string, unknown>;
    const restricted = (replay.restricted ?? {}) as Record<string, unknown> | null;
    const part = (participant.events ?? []) as Ev[];
    const internal = (restricted?.internal_events ?? []) as Ev[];
    const modelCalls = ((restricted?.model_calls ?? []) as Record<string, unknown>[])
      .map(modelCallRow);
    const receipts = ((restricted?.command_receipts ?? []) as Record<string, unknown>[])
      .map(receiptRow);
    const eventRows = [
      ...part.map((e, i) => toRow(e, i, false)),
      ...internal.map((e, i) => toRow(e, i, true)),
    ];
    // Merge model calls and command receipts by wall-clock; events stay seq-ordered.
    const all = [...eventRows, ...modelCalls, ...receipts].sort((a, b) => {
      if (a.wall && b.wall && a.wall !== b.wall) return a.wall < b.wall ? -1 : 1;
      return a.sortKey - b.sortKey;
    });
    const counts = new Map<Category, number>();
    for (const r of all) counts.set(r.category, (counts.get(r.category) ?? 0) + 1);
    return { rows: all, counts, truncated: Boolean(participant.truncated) };
  }, [replay]);

  const visible = rows.filter((r) => {
    if (r.category === "telemetry" && !showTelemetry) return false;
    if (r.category === "agent" && !showAgent) return false;
    if (!showInternal && r.internal) return false;
    if (filter && r.category !== filter) return false;
    return true;
  });

  const cats = [...counts.keys()].filter((c) => c !== "telemetry");

  return (
    <div>
      <div className="admin-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <b>Walkthrough</b>
        <span className="admin-note">
          {String(session.mode ?? "")} · {String(session.timing_mode ?? "")} ·{" "}
          {rows.length} timeline rows{truncated ? " (truncated)" : ""}
        </span>
        <button
          onClick={() => downloadSimulation(replay)}
          title="Download a self-contained HTML file that replays this session through the participant workbench UI — chat, assistant, case file, answers, check-in — in chronological order."
        >
          Generate HTML simulation
        </button>
        <button
          onClick={() => downloadParticipantView(replay)}
          title="Download a self-contained HTML file showing every page the participant saw, in order — consent, orientation, practice, workbench, check-in, questionnaires, debrief, completion — with their recorded responses. No researcher internals."
        >
          Participant view (HTML)
        </button>
        <button onClick={() => setFilter(null)} disabled={!filter}>
          all
        </button>
        {cats.map((c) => (
          <button
            key={c}
            onClick={() => setFilter(filter === c ? null : c)}
            style={{
              borderColor: CATEGORY_COLOR[c],
              color: filter === c ? "#fff" : CATEGORY_COLOR[c],
              background: filter === c ? CATEGORY_COLOR[c] : "transparent",
            }}
          >
            {CATEGORY_LABEL[c]} {counts.get(c)}
          </button>
        ))}
        <label className="admin-note">
          <input
            type="checkbox"
            checked={showAgent}
            onChange={(e) => setShowAgent(e.target.checked)}
          />{" "}
          agent internals
        </label>
        <label className="admin-note">
          <input
            type="checkbox"
            checked={showInternal}
            onChange={(e) => setShowInternal(e.target.checked)}
          />{" "}
          internal-only events
        </label>
        <label className="admin-note">
          <input
            type="checkbox"
            checked={showTelemetry}
            onChange={(e) => setShowTelemetry(e.target.checked)}
          />{" "}
          telemetry ({counts.get("telemetry") ?? 0})
        </label>
      </div>
      <table className="admin-table walkthrough-table">
        <thead>
          <tr>
            <th>T+</th>
            <th>wall</th>
            <th>category</th>
            <th>actor</th>
            <th>event</th>
            <th>detail</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((r, i) => (
            <tr key={i}>
              <td className="mono">{fmtActive(r.activeMs)}</td>
              <td className="mono">{fmtWall(r.wall)}</td>
              <td>
                <span
                  className="wt-cat"
                  style={{
                    borderColor: CATEGORY_COLOR[r.category],
                    color: CATEGORY_COLOR[r.category],
                  }}
                >
                  {CATEGORY_LABEL[r.category]}
                </span>
              </td>
              <td className="mono">{r.actor || "—"}</td>
              <td>{r.title}</td>
              <td className="wt-detail">{r.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {visible.length === 0 && (
        <p className="admin-note">No rows match the current filters.</p>
      )}
    </div>
  );
}
