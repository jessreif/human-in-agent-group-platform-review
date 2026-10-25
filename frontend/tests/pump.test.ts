/**
 * EventPump (TS-B09): controllable EventSource, no socket and no fetch.
 * Polling is intercepted by mocking getEvents on the API client.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { eventStreamUrl } from "../src/api/client";
import type { ParticipantEvent } from "../src/api/types";
import { SessionController } from "../src/state/controller";
import { EventPump, type EventSourceLike } from "../src/state/pump";
import type { ConnectionState, SessionStore } from "../src/state/store";

const { getEvents } = vi.hoisted(() => ({
  getEvents: vi.fn(),
}));

vi.mock("../src/api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../src/api/client")>();
  return {
    ...orig,
    getEvents: (...args: unknown[]) => getEvents(...args),
  };
});

/** EventSource readyState: CONNECTING / OPEN / CLOSED. */
const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 2;

class FakeEventSource implements EventSourceLike {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onopen: ((ev: Event) => void) | null = null;
  readyState = CONNECTING;

  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();

  close(): void {
    this.readyState = CLOSED;
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    const bucket = this.listeners.get(type);
    if (bucket) bucket.push(listener);
    else this.listeners.set(type, [listener]);
  }

  /** A closed source delivers nothing, matching EventSource.close(). */
  private deliver(type: string, data: string): void {
    if (this.readyState === CLOSED) return;
    const ev = new MessageEvent(type, { data });
    if (type === "message") this.onmessage?.(ev);
    for (const listener of this.listeners.get(type) ?? []) listener(ev);
  }

  open(): void {
    if (this.readyState === CLOSED) return;
    this.readyState = OPEN;
    this.onopen?.(new Event("open"));
  }

  message(data: string): void {
    this.deliver("message", data);
  }

  head(data: string): void {
    this.deliver("head", data);
  }

  streamEnd(): void {
    this.deliver("stream_end", "");
  }

  fail(): void {
    if (this.readyState === CLOSED) return;
    this.onerror?.(new Event("error"));
  }
}

type ApplyOpts = {
  replayed?: boolean;
  nextEventSeq?: number;
  scannedSeq?: number;
};

type AppliedCall = {
  events: ParticipantEvent[];
  opts?: ApplyOpts;
};

function createStore(cursor = 0) {
  let current = cursor;
  const applied: AppliedCall[] = [];
  const connections: ConnectionState[] = [];
  return {
    applied,
    connections,
    applyEvents(events: ParticipantEvent[], opts?: ApplyOpts): boolean {
      if (opts === undefined) applied.push({ events });
      else applied.push({ events, opts });
      for (const event of events) {
        if (event.seq > current) current = event.seq;
      }
      return true;
    },
    setConnection(connection: ConnectionState): void {
      connections.push(connection);
    },
    getSnapshot(): { cursor: number } {
      return { cursor: current };
    },
  };
}

function participantEvent(seq: number): ParticipantEvent {
  return {
    seq,
    event_id: `evt-${seq}`,
    stage: "task",
    actor: "system",
    type: "note",
    revision: seq,
    active_ms: null,
    server_time: null,
    payload: { seq },
  };
}

function startPump(opts?: {
  cursor?: number;
  maxSseErrors?: number;
  pollIntervalMs?: number;
  fetchImpl?: typeof fetch;
}) {
  const store = createStore(opts?.cursor ?? 0);
  const source = new FakeEventSource();
  const pump = new EventPump(store as unknown as SessionStore, {
    eventSourceFactory: () => source,
    ...(opts?.maxSseErrors !== undefined ? { maxSseErrors: opts.maxSseErrors } : {}),
    ...(opts?.pollIntervalMs !== undefined ? { pollIntervalMs: opts.pollIntervalMs } : {}),
    ...(opts?.fetchImpl !== undefined ? { fetchImpl: opts.fetchImpl } : {}),
  });
  pump.start();
  return { store, source, pump };
}

describe("EventPump", () => {
  beforeEach(() => {
    getEvents.mockReset();
    getEvents.mockResolvedValue({
      events: [],
      scanned_seq: 0,
      next_event_seq: 0,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("valid message applies one event and clears the error count", () => {
    const { store, source, pump } = startPump({ maxSseErrors: 2 });
    const event = participantEvent(4);
    try {
      source.fail();
      expect(store.connections).toEqual(["connecting"]);

      source.message(JSON.stringify(event));
      expect(store.applied).toEqual([{ events: [event] }]);
      expect(store.getSnapshot().cursor).toBe(4);

      // One more error would fall back if the message had not zeroed sseErrors.
      source.fail();
      expect(store.connections).toEqual(["connecting", "connecting"]);
      expect(source.readyState).toBe(CONNECTING);
      expect(getEvents).not.toHaveBeenCalled();
    } finally {
      pump.stop();
    }
  });

  it("malformed message does not move the cursor", () => {
    const { store, source, pump } = startPump({ cursor: 5 });
    try {
      source.message("{");
      source.message("not-json");
      expect(store.applied).toEqual([]);
      expect(store.connections).toEqual([]);
      expect(store.getSnapshot().cursor).toBe(5);
    } finally {
      pump.stop();
    }
  });

  it("head with next_event_seq advances the high-water mark without an event body", () => {
    const { store, source, pump } = startPump({ cursor: 5 });
    try {
      source.head(JSON.stringify({ next_event_seq: 11 }));
      expect(store.applied).toEqual([{ events: [], opts: { nextEventSeq: 11 } }]);
      expect(store.getSnapshot().cursor).toBe(5);
    } finally {
      pump.stop();
    }
  });

  it("stream_end does not by itself drop the connection or double-apply", () => {
    const { store, source, pump } = startPump();
    const event = participantEvent(6);
    try {
      source.open();
      source.message(JSON.stringify(event));
      source.streamEnd();
      expect(source.readyState).toBe(OPEN);
      expect(store.connections).toEqual(["live"]);
      expect(store.applied).toEqual([{ events: [event] }]);
    } finally {
      pump.stop();
    }
  });

  it("one error sets connecting; maxSseErrors errors poll on exactly one loop", async () => {
    vi.useFakeTimers();
    const fetchImpl = vi.fn(() => Promise.reject(new Error("network"))) as unknown as typeof fetch;
    const { store, source, pump } = startPump({ fetchImpl, pollIntervalMs: 1000 });
    try {
      source.fail();
      expect(store.connections).toEqual(["connecting"]);
      expect(source.readyState).toBe(CONNECTING);
      expect(getEvents).not.toHaveBeenCalled();

      source.fail();
      source.fail();
      expect(store.connections).toEqual(["connecting", "connecting", "connecting"]);
      expect(source.readyState).toBe(CONNECTING);

      source.fail();
      expect(source.readyState).toBe(CLOSED);
      expect(store.connections).toEqual(["connecting", "connecting", "connecting", "polling"]);

      // Another error callback must not arm a second loop.
      source.onerror?.(new Event("error"));

      await vi.advanceTimersByTimeAsync(0);
      expect(getEvents).toHaveBeenCalledTimes(1);
      expect(getEvents).toHaveBeenCalledWith(store.getSnapshot().cursor, fetchImpl);

      await vi.advanceTimersByTimeAsync(1000);
      expect(getEvents).toHaveBeenCalledTimes(2);
      expect(fetchImpl).not.toHaveBeenCalled();
    } finally {
      pump.stop();
    }
  });

  it("stop() closes the source and a later message is not applied", () => {
    const { store, source, pump } = startPump();
    const first = participantEvent(1);
    const later = participantEvent(2);
    source.message(JSON.stringify(first));
    pump.stop();
    expect(source.readyState).toBe(CLOSED);
    source.message(JSON.stringify(later));
    expect(store.applied).toEqual([{ events: [first] }]);
    expect(store.getSnapshot().cursor).toBe(1);
  });
});

describe("SessionController event source factory", () => {
  beforeEach(() => {
    getEvents.mockReset();
    getEvents.mockResolvedValue({
      events: [],
      scanned_seq: 0,
      next_event_seq: 0,
    });
  });

  it("calls an injected non-null factory with the stream URL", () => {
    const source = new FakeEventSource();
    const fetchImpl = vi.fn(() => Promise.reject(new Error("network"))) as unknown as typeof fetch;
    const factory = vi.fn((): EventSourceLike => source);
    // pump.start is what invokes the factory (controller.start does too, after boot).
    const controller = new SessionController({
      eventSourceFactory: factory,
      fetchImpl,
    });
    try {
      expect(controller.store.getSnapshot().cursor).toBe(0);
      controller.pump.start();
      expect(eventStreamUrl(0)).toBe("/investigators/api/events/stream?after_seq=0");
      expect(factory).toHaveBeenCalledTimes(1);
      expect(factory).toHaveBeenCalledWith(eventStreamUrl(0));
      expect(source.onmessage).toBeTypeOf("function");
      expect(getEvents).not.toHaveBeenCalled();
      expect(fetchImpl).not.toHaveBeenCalled();
    } finally {
      controller.pump.stop();
    }
  });
});
