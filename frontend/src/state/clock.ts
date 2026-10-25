/**
 * Server-authoritative countdown (INV-0804; spec §6.1). The client never owns a
 * transition: it anchors on the server's ``active_ms`` + ``server_now`` pair taken at one
 * instant, then interpolates with the local monotonic clock until the next event or
 * session fetch resynchronizes the anchor. A stalled interpolation just means the next
 * sync corrects the display — no client timer can freeze answers or fire a warning.
 */

export interface ClockAnchor {
  /** Server active-task elapsed ms at the anchor instant. */
  activeMs: number;
  /** Server wall time (ms epoch) at the anchor instant, when known. */
  serverNowMs: number | null;
  /** Total active budget (ms), or null when the profile provides none. */
  budgetMs: number | null;
  /** Whether the active clock was running at the anchor instant. */
  running: boolean;
  /** Local monotonic ms at the moment the anchor was received. */
  receivedAtMs: number;
}

/** Build an anchor from a GET /session clock block. */
export function anchorFromClock(
  clock: { server_now: string; active_ms: number; budget_ms: number | null; clock_running: boolean },
  receivedAtMs: number,
): ClockAnchor {
  return {
    activeMs: clock.active_ms,
    serverNowMs: Date.parse(clock.server_now),
    budgetMs: clock.budget_ms,
    running: clock.clock_running,
    receivedAtMs,
  };
}

/**
 * Refresh the anchor from an event's ``active_ms``/``server_time`` pair. Returns the
 * previous anchor unchanged when the event carries no active-time stamp. ``running`` is
 * preserved: an event stamped while the clock runs does not mean it still runs — that
 * comes only from the session clock block — but a stamped event proves the clock had
 * reached ``active_ms``, so the anchor is advanced forward (never backward).
 */
export function anchorFromEvent(
  prev: ClockAnchor | null,
  event: { active_ms: number | null; server_time: string | null },
  receivedAtMs: number,
): ClockAnchor | null {
  if (event.active_ms === null) return prev;
  const serverNowMs = event.server_time === null ? null : Date.parse(event.server_time);
  if (prev && event.active_ms < prev.activeMs) return prev;
  return {
    activeMs: event.active_ms,
    serverNowMs,
    budgetMs: prev?.budgetMs ?? null,
    running: prev?.running ?? false,
    receivedAtMs,
  };
}

/** Interpolated active elapsed (ms) at local monotonic ``nowMs``. */
export function activeAt(anchor: ClockAnchor, nowMs: number): number {
  if (!anchor.running) return anchor.activeMs;
  return anchor.activeMs + Math.max(0, nowMs - anchor.receivedAtMs);
}

/** Remaining active ms, or null when no budget applies. Clamped at 0. */
export function remainingMs(anchor: ClockAnchor | null, nowMs: number): number | null {
  if (!anchor || anchor.budgetMs === null) return null;
  return Math.max(0, anchor.budgetMs - activeAt(anchor, nowMs));
}

/** The low-time style applies for the final minute of the active budget (spec §4.1). */
export const LOW_TIME_MS = 60_000;

/** Greeting buttons are offered only during the first 30 s of active task time
 * (UX round 2, item 7) — a resync replaying the prompt later must not re-show them. */
export const GREETING_WINDOW_MS = 30_000;

export function isLowTime(anchor: ClockAnchor | null, nowMs: number): boolean {
  const remaining = remainingMs(anchor, nowMs);
  return remaining !== null && remaining <= LOW_TIME_MS;
}

/** Format ``m:ss`` for the countdown display. */
export function formatCountdown(remainingMs: number): string {
  const totalSeconds = Math.ceil(remainingMs / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
