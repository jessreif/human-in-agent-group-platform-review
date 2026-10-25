/**
 * AI assistant pane (INV-0802): its own message stream (channel "assistant"), a
 * per-crate structured ask and a free-text composer. Channel availability comes from
 * the server's phase projection, never from a client guess.
 */
import { useEffect, useRef, useState } from "react";

import { youLabel } from "../state/names";
import type { SessionStore, StoreState } from "../state/store";
import type { TelemetryQueue } from "../telemetry";

export function AssistantPanel({
  state,
  store,
  telemetry,
  disabled,
}: {
  state: StoreState;
  store: SessionStore;
  telemetry: TelemetryQueue;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [crate, setCrate] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLUListElement>(null);

  const messages = state.messages.filter((m) => m.channel === "assistant");
  const canAsk =
    !disabled && state.channels.includes("assistant_prompting") && !state.taskClosed;
  // UX round 3, item 9: every accepted ask surfaces as the participant's own
  // assistant-channel message and is answered by exactly one reply (generated or
  // the deterministic fallback), so "still generating" is derivable from the
  // event-backed stream — it survives reloads and resyncs with no extra state.
  const asked = messages.filter((m) => m.mine).length;
  const answered = messages.length - asked;
  const generating = !state.taskClosed && (busy || asked > answered);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, generating]);

  const renderedRef = useRef(new Set<string>());
  useEffect(() => {
    for (const m of messages) {
      if (!renderedRef.current.has(m.id)) {
        renderedRef.current.add(m.id);
        telemetry.emit("render", { message_id: m.id, channel: "assistant" });
        telemetry.emit("render_first_final", { message_id: m.id, channel: "assistant" });
      }
    }
  }, [messages, telemetry]);

  const structured = state.structuredRequests;
  async function ask() {
    if (busy || ((crate === "" || !structured) && !draft.trim())) return;
    setBusy(true);
    try {
      const body = draft.trim() || undefined;
      await store.askAssistant(
        body ?? "",
        crate === "" || !structured ? undefined : Number(crate),
      );
      setDraft("");
    } catch {
      /* rejection surfaces via lease/connection state */
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="pane" aria-label="AI assistant" data-pane="assistant">
      <header className="pane-head">
        <h2>AI assistant</h2>
      </header>
      <p className="pane-hint">
        Ask about a crate and it will review the evidence currently in your
        case file.
      </p>
      <ul className="msgs" ref={listRef} aria-label="Assistant messages">
        {messages.map((m) => (
          <li key={m.id} className={m.mine ? "msg mine" : "msg"}>
            <span className="who">{m.mine ? youLabel(state.initials) : "Assistant"}</span>
            <span className="msg-body">{m.body}</span>
          </li>
        ))}
        {generating ? (
          <li className="msg thinking" data-testid="assistant-thinking">
            <span className="who">Assistant</span>
            <span className="msg-body" role="status">
              Generating a response
              <span className="thinking-dots" aria-hidden="true">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </span>
            </span>
          </li>
        ) : null}
      </ul>
      {structured ? (
        <div className="team-actions">
          <label className="visually-hidden" htmlFor="ai-crate">
            Ask the assistant about a crate
          </label>
          <select
            id="ai-crate"
            value={crate}
            onChange={(e) => setCrate(e.target.value)}
            disabled={!canAsk}
          >
            <option value="">Ask the assistant about…</option>
            {state.assignedCrates.map((n) => (
              <option key={n} value={n}>
                Crate {n}
                {state.crateItems[n] ? ` — ${state.crateItems[n]}` : ""}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="composer">
        <label className="visually-hidden" htmlFor="ai-input">
          {structured ? "Or type a question for the assistant" : "Type a question for the assistant"}
        </label>
        <input
          id="ai-input"
          maxLength={600}
          type="text"
          value={draft}
          disabled={!canAsk}
          placeholder={structured ? "Or type a question" : "Type a question"}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void ask();
          }}
        />
        <button type="button" disabled={!canAsk || busy || (!draft.trim() && (crate === "" || !structured))} onClick={() => void ask()}>
          {busy ? "Sending…" : "Ask"}
        </button>
      </div>
    </section>
  );
}
