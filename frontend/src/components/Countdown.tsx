/**
 * Countdown + status header strip (INV-0804). The digits interpolate from the latest
 * server anchor — ``active_ms`` + ``clock_running`` + ``budget_ms`` — and resynchronize
 * on every event/session update. The client never derives a transition from the timer;
 * ``taskClosed`` and the low-time flag come from server events.
 */
import { useEffect, useReducer } from "react";

import type { ConnectionState, StoreState } from "../state/store";
import { formatCountdown, isLowTime, remainingMs } from "../state/clock";

const TICK_MS = 250;

function connectionLabel(connection: ConnectionState): string {
  switch (connection) {
    case "live":
      return "";
    case "polling":
      return "Syncing";
    case "connecting":
      return "Connecting…";
    case "disconnected":
      return "Disconnected — retrying";
  }
}

export function Countdown({ state }: { state: StoreState }) {
  const [, tick] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const id = setInterval(tick, TICK_MS);
    return () => clearInterval(id);
  }, []);

  const now = Date.now();
  const remaining = remainingMs(state.clock, now);
  // TIME_PRESSURE_ENABLED=0: the countdown stays a neutral timer — no low-time
  // styling (and the server never emits warning_committed).
  const low =
    state.timePressure && (isLowTime(state.clock, now) || state.lowTimeActive);
  const paused = state.clock !== null && !state.clock.running;
  const connNote = connectionLabel(state.connection);

  return (
    <span className="status-strip">
      {connNote ? (
        <span className="conn" role="status" data-connection={state.connection}>
          {connNote}
        </span>
      ) : null}
      {remaining !== null ? (
        <span
          className={low ? "timer low-time" : "timer"}
          role="timer"
          aria-label="Time remaining"
          data-low-time={low}
        >
          {formatCountdown(remaining)}
        </span>
      ) : null}
      {paused ? <span className="paused-note">Clock paused</span> : null}
      {state.taskClosed ? <span className="locked-note">Answers locked</span> : null}
    </span>
  );
}
