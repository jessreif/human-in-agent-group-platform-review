import { describe, expect, it } from "vitest";

import {
  anchorFromClock,
  anchorFromEvent,
  formatCountdown,
  isLowTime,
  remainingMs,
} from "../src/state/clock";

describe("server-interpolated countdown (INV-0804)", () => {
  const clock = {
    server_now: "2026-01-01T00:00:00+00:00",
    active_ms: 100_000,
    budget_ms: 360_000,
    clock_running: true,
  };

  it("interpolates between resyncs using the local monotonic delta", () => {
    const anchor = anchorFromClock(clock, 1_000);
    expect(remainingMs(anchor, 1_000)).toBe(260_000);
    expect(remainingMs(anchor, 61_000)).toBe(200_000);
  });

  it("does not run while the clock is paused (check-in)", () => {
    const anchor = anchorFromClock({ ...clock, clock_running: false }, 1_000);
    expect(remainingMs(anchor, 999_999)).toBe(260_000);
  });

  it("advances the anchor from stamped events, never backward", () => {
    const anchor = anchorFromClock(clock, 1_000);
    const fwd = anchorFromEvent(
      anchor,
      { active_ms: 120_000, server_time: "2026-01-01T00:00:20+00:00" },
      2_000,
    );
    expect(fwd?.activeMs).toBe(120_000);
    const back = anchorFromEvent(
      fwd,
      { active_ms: 50_000, server_time: "2026-01-01T00:00:10+00:00" },
      3_000,
    );
    expect(back?.activeMs).toBe(120_000);
  });

  it("clamps at zero and flags the final minute as low-time", () => {
    const anchor = anchorFromClock(clock, 1_000);
    expect(remainingMs(anchor, 1_000 + 400_000)).toBe(0);
    const late = anchorFromClock({ ...clock, active_ms: 305_000 }, 5_000);
    expect(isLowTime(late, 5_000)).toBe(true);
    expect(formatCountdown(59_500)).toBe("1:00");
    expect(formatCountdown(59_000)).toBe("0:59");
  });
});
