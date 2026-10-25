/**
 * Bounded telemetry (INV-0807): a small client-side queue that batches
 * ``POST /telemetry`` items and emits ONLY the taxonomy the spec defines —
 * receive, render, render_first_final, visibility, focus, blur, item_start,
 * item_end, file_meta, file_record_view, file_record_dwell,
 * file_scroll_summary, file_reorder, file_order_final, share_attempt
 * (see ``TelemetryKind``). No keystroke-level logging, no content payloads
 * beyond what the spec's taxonomy needs.
 *
 * Every item carries ``client_epoch`` — a random id minted per queue instance
 * (≈ per page load). Reloaded sessions get a new monotonic clock origin, so
 * the measurement layer must pair/order ``client_monotonic_ms`` only within
 * an epoch and order epochs by server sequence (audit F06).
 */
import { apiUrl, postTelemetry, readCookie } from "./api/client";
import type { TelemetryItem } from "./api/client";
import type { TelemetryKind } from "./api/types";

const FLUSH_INTERVAL_MS = 4000;
/** Never let the queue grow without bound while offline — drop the oldest. */
const MAX_QUEUED = 200;
/** Max items per POST. */
const BATCH_SIZE = 50;
/** Server-side per-request item cap (participant._TELEMETRY_BATCH_MAX) — the
 * departure drain sends at most this many items. */
const DEPART_MAX_ITEMS = 100;

function mintEpoch(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `e-${Math.random().toString(16).slice(2, 10)}${Date.now().toString(16)}`;
}

export class TelemetryQueue {
  private queue: TelemetryItem[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending = false;
  private startedAt: number;
  private fetchImpl: typeof fetch;
  private detach: (() => void) | null = null;
  private failedRecently = false;
  /** Random per-load clock identity — see module docblock (audit F06). */
  readonly clientEpoch: string;

  constructor(opts: { fetchImpl?: typeof fetch; now?: () => number } = {}) {
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.startedAt = opts.now?.() ?? (typeof performance !== "undefined" ? performance.now() : Date.now());
    this.clientEpoch = mintEpoch();
  }

  private monotonic(): number {
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    return Math.round(now - this.startedAt);
  }

  emit(kind: TelemetryKind, data: Record<string, unknown> = {}): void {
    this.queue.push({
      kind,
      client_monotonic_ms: this.monotonic(),
      client_epoch: this.clientEpoch,
      data,
    });
    if (this.queue.length > MAX_QUEUED) this.queue.splice(0, this.queue.length - MAX_QUEUED);
    if (this.queue.length >= BATCH_SIZE) void this.flush();
  }

  async flush(): Promise<void> {
    if (this.sending || this.queue.length === 0 || this.failedRecently) return;
    this.sending = true;
    const batch = this.queue.splice(0, BATCH_SIZE);
    try {
      await postTelemetry(batch, this.fetchImpl);
    } catch {
      // Telemetry is best-effort (spec §10.5): do not requeue aggressively — drop the
      // batch and keep collecting so a dead endpoint cannot grow memory.
      this.failedRecently = true;
      setTimeout(() => {
        this.failedRecently = false;
      }, 30_000);
    } finally {
      this.sending = false;
    }
  }

  /** Departure drain (F05): synchronous, best-effort send of the remaining
   * queue via ``fetch(..., {keepalive: true})`` — safe to call from pagehide
   * handlers, where normal async fetches may never complete. Idempotent per
   * queue state: emitters that finalize during pagehide can call this again
   * after their late emits; whichever call runs last carries the tail. */
  depart(): void {
    if (this.queue.length === 0) return;
    const batch = this.queue.splice(0, DEPART_MAX_ITEMS);
    const csrf = readCookie("inv_csrf");
    const init: RequestInit = {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(csrf ? { "x-csrf-token": csrf } : {}),
      },
      body: JSON.stringify({ kind: "batch", data: { events: batch } }),
    };
    try {
      void this.fetchImpl(apiUrl("/telemetry"), init);
    } catch {
      /* best-effort — the page is leaving */
    }
  }

  /** Start the periodic flush and bind visibility/focus listeners (browser only). */
  start(): void {
    if (this.timer !== null) return;
    this.timer = setInterval(() => void this.flush(), FLUSH_INTERVAL_MS);
    if (typeof document !== "undefined" && typeof window !== "undefined") {
      const onVisibility = () => {
        this.emit("visibility", { state: document.visibilityState });
        if (document.visibilityState === "hidden") void this.flush();
      };
      const onFocus = () => this.emit("focus");
      const onBlur = () => this.emit("blur");
      const onDepart = () => this.depart();
      document.addEventListener("visibilitychange", onVisibility);
      window.addEventListener("focus", onFocus);
      window.addEventListener("blur", onBlur);
      window.addEventListener("pagehide", onDepart);
      this.detach = () => {
        document.removeEventListener("visibilitychange", onVisibility);
        window.removeEventListener("focus", onFocus);
        window.removeEventListener("blur", onBlur);
        window.removeEventListener("pagehide", onDepart);
      };
    }
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.detach?.();
    this.detach = null;
    this.depart();
  }
}
