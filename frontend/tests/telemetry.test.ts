/**
 * TelemetryQueue tests (audit G01 / TS-B10): every item carries the per-load
 * ``client_epoch``, the queue keeps at most 200 items, flushes batches of 50
 * through ``postTelemetry``, and ``depart`` sends at most 100 via fetch
 * ``keepalive`` so pagehide-time finalization survives the page leaving.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TelemetryItem } from "../src/api/client";
import { TelemetryQueue } from "../src/telemetry";

const postTelemetry = vi.fn().mockResolvedValue({ ok: true });
const readCookie = vi.fn<(name: string) => string | null>(() => "csrf-token");

vi.mock("../src/api/client", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../src/api/client")>();
  return {
    ...orig,
    postTelemetry: (...args: unknown[]) => postTelemetry(...args),
    apiUrl: (p: string) => `https://test.local${p}`,
    readCookie: (name: string) => readCookie(name),
  };
});

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve: (value: T | PromiseLike<T>) => void = () => {};
  let reject: (reason?: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function asFetch(impl: ReturnType<typeof vi.fn>): typeof fetch {
  return impl as unknown as typeof fetch;
}

function seq(from: number, count: number): number[] {
  return Array.from({ length: count }, (_, offset) => from + offset);
}

function sentItems(index: number): TelemetryItem[] {
  const items = postTelemetry.mock.calls[index]?.[0];
  if (!Array.isArray(items)) throw new Error(`missing telemetry batch ${index}`);
  return items as TelemetryItem[];
}

function sentMarks(index: number): unknown[] {
  return sentItems(index).map((item) => item.data?.n);
}

function departEvents(impl: ReturnType<typeof vi.fn>, index: number): TelemetryItem[] {
  const init = impl.mock.calls[index]?.[1] as RequestInit | undefined;
  const body = JSON.parse(String(init?.body)) as { data: { events: TelemetryItem[] } };
  return body.data.events;
}

function departMarks(impl: ReturnType<typeof vi.fn>, index: number): unknown[] {
  return departEvents(impl, index).map((item) => item.data?.n);
}

/** Emit ``count`` items whose ``data.n`` runs from ``start``. */
function emitCount(
  q: TelemetryQueue,
  count: number,
  kind: "focus" | "blur" = "focus",
  start = 0,
): void {
  for (let offset = 0; offset < count; offset += 1) {
    q.emit(kind, { n: start + offset });
  }
}

describe("TelemetryQueue", () => {
  let fetchImpl: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    postTelemetry.mockReset();
    postTelemetry.mockResolvedValue({ ok: true });
    readCookie.mockReset();
    readCookie.mockImplementation((name: string) => (name === "inv_csrf" ? "csrf-token" : null));
    fetchImpl = vi.fn().mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("stamps every queued item with the per-load client_epoch", async () => {
    const q = new TelemetryQueue({ fetchImpl: fetchImpl as unknown as typeof fetch });
    q.emit("focus");
    q.emit("blur");
    await q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    const items = postTelemetry.mock.calls[0]?.[0] as TelemetryItem[];
    expect(items).toHaveLength(2);
    for (const item of items) {
      expect(item.client_epoch).toBe(q.clientEpoch);
      expect(typeof item.client_monotonic_ms).toBe("number");
    }
  });

  it("mints a distinct epoch per queue instance", () => {
    const a = new TelemetryQueue();
    const b = new TelemetryQueue();
    expect(a.clientEpoch).not.toBe(b.clientEpoch);
  });

  it("depart() drains the queue through a keepalive fetch", () => {
    const q = new TelemetryQueue({ fetchImpl: fetchImpl as unknown as typeof fetch });
    q.emit("file_record_dwell", { record_id: "r1", dwell_ms: 500, final: true });
    q.emit("file_order_final", { order: ["r1"], n_items: 1 });
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://test.local/telemetry");
    expect(init.keepalive).toBe(true);
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as {
      kind: string;
      data: { events: TelemetryItem[] };
    };
    expect(body.kind).toBe("batch");
    expect(body.data.events).toHaveLength(2);
    expect(body.data.events[0]?.kind).toBe("file_record_dwell");
    expect(body.data.events[0]?.client_epoch).toBe(q.clientEpoch);
    // The queue is drained — a second depart sends nothing.
    fetchImpl.mockClear();
    q.depart();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("caps the departure drain at the server batch limit", () => {
    const q = new TelemetryQueue({ fetchImpl: fetchImpl as unknown as typeof fetch });
    // 160 emits: the #50 emit kicks an in-flight flush of the first 50
    // (still ``sending`` during the synchronous loop), leaving 110 queued —
    // depart must send at most the server's 100-item request cap.
    for (let i = 0; i < 160; i += 1) q.emit("focus");
    q.depart();
    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as { data: { events: unknown[] } };
    expect(body.data.events).toHaveLength(100);
  });

  it("drops the oldest item when the 201st emit exceeds the queue cap", async () => {
    // A rejected flush sets the offline cooldown. Later emits stay queued
    // (the 50-item flush would otherwise drain them) until MAX_QUEUED bites.
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementationOnce(() => gate.promise);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });
    q.emit("focus", { n: -1 });
    const failing = q.flush();
    gate.reject(new Error("offline"));
    await failing;

    emitCount(q, 201);
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(sentMarks(0)).toEqual([-1]);

    q.depart();
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(departMarks(fetchImpl, 0)).toEqual(seq(1, 100));
    expect(departMarks(fetchImpl, 1)).toEqual(seq(101, 100));
    for (const item of [...departEvents(fetchImpl, 0), ...departEvents(fetchImpl, 1)]) {
      expect(item.client_epoch).toBe(q.clientEpoch);
    }
    fetchImpl.mockClear();
    q.depart();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("flushes one batch of 50 on the 50th emit and leaves the rest queued", async () => {
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementation(() => gate.promise);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });

    emitCount(q, 49);
    expect(postTelemetry).not.toHaveBeenCalled();
    expect(fetchImpl).not.toHaveBeenCalled();

    q.emit("focus", { n: 49 });
    emitCount(q, 10, "focus", 50);
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(postTelemetry.mock.calls[0]?.[1]).toBe(fetchImpl);
    expect(sentMarks(0)).toEqual(seq(0, 50));
    expect(sentItems(0).every((item) => item.client_epoch === q.clientEpoch)).toBe(true);

    q.depart();
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(departMarks(fetchImpl, 0)).toEqual(seq(50, 10));

    gate.resolve({ ok: true });
    await gate.promise;
  });

  it("depart() sends at most 100 items with keepalive, same-origin, and x-csrf-token", () => {
    readCookie.mockImplementation((name: string) =>
      name === "inv_csrf" ? "synthetic-inv-csrf" : null,
    );
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementation(() => gate.promise);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });

    // 50 leave in the in-flight postTelemetry batch; 110 stay queued.
    emitCount(q, 160);
    q.depart();
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://test.local/telemetry");
    expect(init.method).toBe("POST");
    expect(init.keepalive).toBe(true);
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
      "x-csrf-token": "synthetic-inv-csrf",
    });
    expect(readCookie).toHaveBeenCalledWith("inv_csrf");
    expect(departMarks(fetchImpl, 0)).toEqual(seq(50, 100));

    // The cap leaves the tail queued; a later depart carries it.
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(departMarks(fetchImpl, 1)).toEqual(seq(150, 10));
    gate.resolve({ ok: true });
  });

  it("depart() omits x-csrf-token when inv_csrf is absent", () => {
    readCookie.mockReturnValue(null);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });
    q.emit("blur", { n: 1 });
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(init.keepalive).toBe(true);
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).toEqual({
      Accept: "application/json",
      "Content-Type": "application/json",
    });
    expect(readCookie).toHaveBeenCalledWith("inv_csrf");
    expect(departEvents(fetchImpl, 0)[0]?.kind).toBe("blur");
  });

  it("a second depart after more emits sends the tail and an empty depart does not fetch", () => {
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });

    q.depart();
    expect(fetchImpl).not.toHaveBeenCalled();

    q.emit("focus", { n: 1 });
    q.emit("blur", { n: 2 });
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(departMarks(fetchImpl, 0)).toEqual([1, 2]);

    fetchImpl.mockClear();
    q.depart();
    expect(fetchImpl).not.toHaveBeenCalled();

    q.emit("file_order_final", { order: ["r1", "r2"], n_items: 2 });
    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const tail = departEvents(fetchImpl, 0);
    expect(tail).toHaveLength(1);
    expect(tail[0]?.kind).toBe("file_order_final");
    expect(tail[0]?.data).toEqual({ order: ["r1", "r2"], n_items: 2 });
    expect(tail[0]?.client_epoch).toBe(q.clientEpoch);
  });

  it("stop() clears the interval, detaches listeners, and departs once", async () => {
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });
    const timersAtRest = vi.getTimerCount();

    q.start();
    expect(vi.getTimerCount()).toBe(timersAtRest + 1);

    q.emit("focus", { n: 1 });
    window.dispatchEvent(new Event("pagehide"));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://test.local/telemetry");
    expect(init.keepalive).toBe(true);
    expect(init.credentials).toBe("same-origin");
    expect(departEvents(fetchImpl, 0).map((item) => item.kind)).toEqual(["focus"]);

    fetchImpl.mockClear();
    q.emit("blur", { n: 2 });
    q.stop();
    expect(vi.getTimerCount()).toBe(timersAtRest);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(departEvents(fetchImpl, 0).map((item) => item.kind)).toEqual(["blur"]);

    // Listeners are gone: pagehide/focus/blur/visibility do not depart or enqueue.
    // The cleared interval does not flush the item emitted after stop.
    fetchImpl.mockClear();
    q.emit("file_meta", { record_count: 1 });
    window.dispatchEvent(new Event("pagehide"));
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("blur"));
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(4_000);
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(postTelemetry).not.toHaveBeenCalled();

    q.depart();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const left = departEvents(fetchImpl, 0);
    expect(left).toHaveLength(1);
    expect(left[0]?.kind).toBe("file_meta");
    expect(left[0]?.data).toEqual({ record_count: 1 });
  });

  it("does not overlap flushes while sending and flushes a full queue after resolve", async () => {
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementation(() => gate.promise);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });

    emitCount(q, 50);
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(sentMarks(0)).toEqual(seq(0, 50));
    expect(fetchImpl).not.toHaveBeenCalled();

    const overlapped = q.flush();
    emitCount(q, 50, "blur", 50);
    await overlapped;
    expect(postTelemetry).toHaveBeenCalledTimes(1);

    gate.resolve({ ok: true });
    await gate.promise;
    expect(postTelemetry).toHaveBeenCalledTimes(1);

    await q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(2);
    expect(postTelemetry.mock.calls[1]?.[1]).toBe(fetchImpl);
    expect(sentMarks(1)).toEqual(seq(50, 50));
    expect(sentItems(1).every((item) => item.kind === "blur")).toBe(true);

    await q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(2);
  });

  it("drops a rejected batch and blocks sends for 30s", async () => {
    // The failed batch is dropped, not requeued — a later send carries only
    // items emitted after the failure.
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementationOnce(() => gate.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });
    q.emit("focus", { n: -1 });
    const failing = q.flush();
    gate.reject(new Error("offline"));
    await failing;
    expect(sentMarks(0)).toEqual([-1]);

    emitCount(q, 50, "blur");
    await q.flush();
    await vi.advanceTimersByTimeAsync(29_999);
    await q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(fetchImpl).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1);
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    await q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(2);
    expect(sentMarks(1)).toEqual(seq(0, 50));
    expect(sentItems(1).every((item) => item.kind === "blur")).toBe(true);
    expect(sentItems(1).every((item) => item.client_epoch === q.clientEpoch)).toBe(true);
  });

  it("delivers a burst of 48 items intact", async () => {
    const gate = deferred<{ ok: boolean }>();
    postTelemetry.mockImplementation(() => gate.promise);
    const fetched = deferred<Response>();
    fetchImpl.mockImplementation(() => fetched.promise);
    const q = new TelemetryQueue({ fetchImpl: asFetch(fetchImpl) });
    const other = new TelemetryQueue();
    const ids = seq(0, 48).map((n) => `rec-${n}`);

    for (const recordId of ids) q.emit("file_record_view", { record_id: recordId });
    expect(postTelemetry).not.toHaveBeenCalled();
    expect(fetchImpl).not.toHaveBeenCalled();

    const flushing = q.flush();
    expect(postTelemetry).toHaveBeenCalledTimes(1);
    expect(postTelemetry.mock.calls[0]?.[1]).toBe(fetchImpl);
    const items = sentItems(0);
    expect(items).toHaveLength(48);
    expect(items.map((item) => item.kind)).toEqual(ids.map(() => "file_record_view"));
    expect(items.map((item) => item.data)).toEqual(ids.map((recordId) => ({ record_id: recordId })));
    for (const item of items) expect(item.client_epoch).toBe(q.clientEpoch);
    expect(q.clientEpoch).not.toBe(other.clientEpoch);

    gate.resolve({ ok: true });
    await flushing;
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
