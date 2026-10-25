/**
 * Event pump (INV-0805): cursor-based SSE first, polling fallback.
 *
 * The server-side stream replays the participant-visible projection after the client's
 * cursor and then emits new events as they commit; ``id:`` frames let the browser's
 * EventSource resume at Last-Event-ID after a reconnect, and a ``head`` frame carries the
 * committed high-water mark so filtered internal seqs are never mistaken for a gap.
 *
 * When EventSource is unavailable (older runtimes, the jsdom test env, some proxies) the
 * pump falls back to interval polling of GET /events — the polling equivalent the spec
 * requires. Any stream that closes for good flips to polling permanently; transient
 * reconnects (the stream is deliberately bounded server-side) are normal.
 */
import { eventStreamUrl, getEvents, ApiError } from "../api/client";
import type { ParticipantEvent } from "../api/types";
import type { SessionStore } from "./store";

export type EventSourceLike = {
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  onopen: ((ev: Event) => void) | null;
  readyState: number;
  close(): void;
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
};

export interface PumpDeps {
  /** EventSource constructor; absent → polling. */
  eventSourceFactory?: ((url: string) => EventSourceLike) | null;
  fetchImpl?: typeof fetch;
  /** Poll interval when in fallback mode. */
  pollIntervalMs?: number;
  /** Give up SSE after this many consecutive failed connections. */
  maxSseErrors?: number;
}

const DEFAULT_POLL_MS = 2500;
const DEFAULT_MAX_SSE_ERRORS = 4;

export class EventPump {
  private store: SessionStore;
  private deps: PumpDeps;
  private es: EventSourceLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private sseErrors = 0;
  private polling = false;

  constructor(store: SessionStore, deps: PumpDeps = {}) {
    this.store = store;
    this.deps = deps;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    const factory = this.deps.eventSourceFactory;
    if (factory) this.openStream(factory);
    else this.startPolling();
  }

  stop(): void {
    this.running = false;
    this.es?.close();
    this.es = null;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.polling = false;
  }

  // ----- SSE -------------------------------------------------------------------------

  private openStream(factory: (url: string) => EventSourceLike): void {
    const url = eventStreamUrl(this.store.getSnapshot().cursor);
    const es = factory(url);
    this.es = es;
    es.onopen = () => {
      this.sseErrors = 0;
      this.store.setConnection("live");
    };
    es.onmessage = (ev: MessageEvent) => {
      this.sseErrors = 0;
      try {
        const event = JSON.parse(String(ev.data)) as ParticipantEvent;
        this.store.applyEvents([event]);
      } catch {
        // A malformed frame is ignored; the cursor stays put and the next head/keepalive
        // keeps the stream honest.
      }
    };
    es.addEventListener("head", (ev: MessageEvent) => {
      try {
        const head = JSON.parse(String(ev.data)) as { next_event_seq?: number };
        if (typeof head.next_event_seq === "number") {
          this.store.applyEvents([], { nextEventSeq: head.next_event_seq });
        }
      } catch {
        /* ignore malformed head */
      }
    });
    es.addEventListener("stream_end", () => {
      // Server-side bound reached; EventSource reconnects on its own (retry: 3000).
    });
    es.onerror = () => {
      if (!this.running) return;
      this.sseErrors += 1;
      if (this.sseErrors >= (this.deps.maxSseErrors ?? DEFAULT_MAX_SSE_ERRORS)) {
        // Repeated failures: fall back to polling (spec §3.1 must work through Apache).
        es.close();
        this.es = null;
        this.store.setConnection("polling");
        this.startPolling();
      } else {
        this.store.setConnection("connecting");
      }
    };
  }

  // ----- polling fallback --------------------------------------------------------------

  startPolling(): void {
    if (this.polling || !this.running) return;
    this.polling = true;
    const interval = this.deps.pollIntervalMs ?? DEFAULT_POLL_MS;
    const tick = async () => {
      if (!this.running || !this.polling) return;
      try {
        const resp = await getEvents(
          this.store.getSnapshot().cursor,
          this.deps.fetchImpl ?? fetch,
        );
        this.store.applyEvents(resp.events, {
          nextEventSeq: resp.next_event_seq,
          scannedSeq: resp.scanned_seq,
        });
        this.store.setConnection(this.es ? "live" : "polling");
      } catch (err) {
        this.store.setConnection("disconnected");
        if (err instanceof ApiError && err.classification === "auth") {
          // Session credential rejected — polling stops until re-entry.
          this.stop();
          return;
        }
      }
      this.timer = setTimeout(() => void tick(), interval);
    };
    this.timer = setTimeout(() => void tick(), 0);
  }
}
