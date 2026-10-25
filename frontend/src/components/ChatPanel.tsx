/**
 * Team chat pane (INV-0802/0803): message list with record attachments (shared
 * cards join the case file automatically — O-4), the persistent teammate-rating
 * strip after a check-in reveal, greeting buttons, the structured "Ask the
 * team" request and a reply composer.
 * The pane is also the drop target for case-file record shares.
 */
import { Fragment, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { activeAt, GREETING_WINDOW_MS } from "../state/clock";
import { displaySeat, youLabel } from "../state/names";
import type { ChatMessage, SessionStore, StoreState } from "../state/store";
import type { TelemetryQueue } from "../telemetry";

function actorLabel(msg: ChatMessage, initials: string | null): string {
  if (msg.mine) return youLabel(initials);
  if (msg.channel === "system" || msg.actor === "system") return "System";
  return displaySeat(msg.actor, initials);
}

function RecordPill({
  state,
  recordId,
  mine,
}: {
  state: StoreState;
  recordId: string;
  mine: boolean;
}) {
  const record = state.delivered[recordId] ?? state.file.find((r) => r.record_id === recordId);
  const inFile = state.file.some((r) => r.record_id === recordId);
  if (!record) {
    return <span className="filepill">Attached record {recordId.slice(0, 8)}…</span>;
  }
  return (
    <span className="filepill">
      <span className="fs">{record.stamp}</span>
      <span className="file-crate">Crate {record.crate}</span>
      {" — "}
      {record.body}
      {/* UX round 4, item 11: the "in your case file" flag only matters on
          teammate shares — on the participant's own share it is noise. */}
      {!mine && inFile ? <span className="file-source"> — in your case file</span> : null}
    </span>
  );
}

export function ChatPanel({
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
  const [askCrate, setAskCrate] = useState("");
  const [sending, setSending] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [greetTick, setGreetTick] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const stickRef = useRef(true);
  // Greeting buttons live for 30 s from prompt delivery (the store anchors on the
  // event's own active_ms — review-first defers the prompt to channel open); the
  // tick re-evaluates the window so they disappear on time without a server event.
  const greetingVisible =
    greetTick >= 0 &&
    !!state.greetingOptions?.length &&
    !!state.clock &&
    (state.greetingAnchorActiveMs === null ||
      activeAt(state.clock, Date.now()) - state.greetingAnchorActiveMs <
        GREETING_WINDOW_MS);
  useEffect(() => {
    if (!greetingVisible) return;
    const timer = setInterval(() => setGreetTick((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, [greetingVisible]);

  const messages = state.messages.filter((m) => m.channel === "team" || m.channel === "system");
  const canChat = !disabled && state.channels.includes("social_reply") && !state.taskClosed;
  const canAsk = !disabled && state.channels.includes("structured_seeking") && !state.taskClosed;

  // Autoscroll to the newest message only when the reader is already near the bottom —
  // an arriving message must never yank someone back mid-read (INV-0808).
  useEffect(() => {
    const el = listRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  // Render receipts for visible messages (bounded telemetry; ids only). Deliveries
  // arrive fully committed, so the first paint is also the first final render.
  const renderedRef = useRef(new Set<string>());
  useEffect(() => {
    for (const m of messages) {
      if (!renderedRef.current.has(m.id)) {
        renderedRef.current.add(m.id);
        telemetry.emit("render", { message_id: m.id, channel: m.channel });
        telemetry.emit("render_first_final", { message_id: m.id, channel: m.channel });
      }
    }
  }, [messages, telemetry]);

  const lastScrollEmit = useRef(0);
  function onScroll() {
    const el = listRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    // cb-1.11 read-side signal — throttled to one emit per 1.5 s, geometry only.
    const now = Date.now();
    if (now - lastScrollEmit.current >= 1500) {
      lastScrollEmit.current = now;
      telemetry.emit("chat_scroll", {
        scroll_top: Math.round(el.scrollTop),
        at_bottom: stickRef.current,
      });
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await store.sendTeamReply(text);
      setDraft("");
    } catch {
      /* rejection shows via lease/connection state */
    } finally {
      setSending(false);
    }
  }

  async function ask() {
    if (askCrate === "" || askBusy) return;
    setAskBusy(true);
    try {
      await store.askTeam(Number(askCrate));
    } catch {
      /* e.g. no_teammate_records — surfaced as a rejected command */
    } finally {
      setAskBusy(false);
    }
  }

  // TIME_PRESSURE_ENABLED=0: no check-in runs, so the persistent pressure
  // strip can never appear.
  const revealed = state.timePressure ? state.checkin.revealedValues : null;

  return (
    <section
      className="pane"
      aria-label="Team chat"
      data-pane="team-chat"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("text/record-id")) e.preventDefault();
      }}
      onDrop={(e) => {
        const recordId = e.dataTransfer.getData("text/record-id");
        if (recordId) {
          e.preventDefault();
          // UX round 5, item 3: teammate-added intel came *from* this chat —
          // dropping it back would re-share a duplicate. Only own-file
          // records may be shared back.
          const rec = state.file.find((r) => r.record_id === recordId);
          if (rec && rec.source !== "initial") {
            telemetry.emit("share_attempt", {
              record_id: recordId,
              via: "drag",
              result: "ignored_not_own",
            });
            return;
          }
          void store
            .shareRecord(recordId)
            .then(
              () =>
                telemetry.emit("share_attempt", {
                  record_id: recordId,
                  via: "drag",
                  result: "sent",
                }),
              (err: unknown) =>
                telemetry.emit("share_attempt", {
                  record_id: recordId,
                  via: "drag",
                  result:
                    err instanceof ApiError ? `rejected:${err.kind}` : "error",
                }),
            );
        }
      }}
    >
      <header className="pane-head">
        <h2>Team chat</h2>
      </header>
      {revealed ? (
        // The persistent post-reveal strip (INV-0902): teammate chips plus the
        // participant's own rating ("—" when they skipped), kept above the chat for the
        // rest of the task exactly like the demo.
        <div className="pressure-strip" role="note">
          {/* UX round 4, item 12: name the manipulation — the strip reports the
              check-in's workload ratings with /7 anchors (v15: relabeled from
              time pressure to workload). */}
          Workload Reported at Check-in:{" "}
          {[
            ...Object.entries(revealed)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([seat, v]) => `${displaySeat(seat, state.initials)}: ${v}/7`),
            `${youLabel(state.initials)}: ${state.checkin.ownValue ?? "—"}/7`,
          ].join(" · ")}
        </div>
      ) : null}
      <ul className="msgs" ref={listRef} onScroll={onScroll} aria-label="Team messages">
        {messages.map((m) => (
          // UX round 4, item 9: an attached record is its own card in the list —
          // a sibling of the message bubble, not a pill embedded inside it. This
          // holds for teammate and participant shares alike.
          <Fragment key={m.id}>
            <li className={m.mine ? "msg mine" : "msg"} data-message-id={m.id}>
              <span className="who">{actorLabel(m, state.initials)}</span>
              <span className="msg-body">{m.body}</span>
            </li>
            {m.recordId ? (
              <li className={m.mine ? "intel-card mine" : "intel-card"}>
                <RecordPill state={state} recordId={m.recordId} mine={m.mine} />
              </li>
            ) : null}
          </Fragment>
        ))}
      </ul>
      {greetingVisible ? (
        <div className="greeting-row" role="group" aria-label="Say hello">
          {(state.greetingOptions ?? []).map((text) => (
            <button
              key={text}
              type="button"
              disabled={!canChat}
              onClick={() => void store.sendGreeting(text).catch(() => undefined)}
            >
              {text}
            </button>
          ))}
        </div>
      ) : null}
      {/* FR-1: natural-language-only sessions ask in their own words — no
          per-crate request control is rendered. */}
      {state.structuredRequests ? (
        <div className="team-actions">
          <label className="visually-hidden" htmlFor="ask-crate">
            Ask the team for intel on a crate
          </label>
          <select
            id="ask-crate"
            value={askCrate}
            onChange={(e) => setAskCrate(e.target.value)}
            disabled={!canAsk}
          >
            <option value="">Ask the team about…</option>
            {state.assignedCrates.map((n) => (
              <option key={n} value={n}>
                Crate {n}
                {state.crateItems[n] ? ` — ${state.crateItems[n]}` : ""}
              </option>
            ))}
          </select>
          <button type="button" disabled={!canAsk || askCrate === "" || askBusy} onClick={() => void ask()}>
            {askBusy ? "Asking…" : "Ask"}
          </button>
        </div>
      ) : null}
      <div className="composer">
        <label className="visually-hidden" htmlFor="chat-input">
          Message the team
        </label>
        <input
          id="chat-input"
          type="text"
          value={draft}
          disabled={!canChat}
          placeholder="Message the team"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
          }}
        />
        <button type="button" disabled={!canChat || !draft.trim() || sending} onClick={() => void send()}>
          Send
        </button>
      </div>
    </section>
  );
}
