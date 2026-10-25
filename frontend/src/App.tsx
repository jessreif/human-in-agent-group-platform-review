/**
 * Participant app root (WP08): boots the session, then routes on the server-driven
 * stage. The workbench stages keep the simultaneous three-pane surface mounted; every
 * other stage is a single-purpose screen. Nothing here carries condition, truth or
 * implementation vocabulary (INV-0809).
 */
import { useEffect, useRef, useState } from "react";

import { SessionController, type ControllerDeps } from "./state/controller";
import { useStoreState } from "./state/useStore";
import { TERMINAL_STAGES, WORKBENCH_STAGES } from "./api/types";
import { Workbench } from "./components/Workbench";
import {
  AiQaScreen,
  CompletionScreen,
  ConnectingScreen,
  ConsentScreen,
  DataUseScreen,
  DebriefScreen,
  InstructionsScreen,
  InstrumentScreen,
  LockedScreen,
  ReadyScreen,
  RefusedScreen,
  TerminalScreen,
  TrainingScreen,
} from "./components/StageScreens";

const INSTRUMENT_STAGES = new Set([
  "confidence",
  "peer_ratings",
  "post_questionnaire",
  "open_ended",
]);

export function App({
  fetchImpl,
  deps,
}: {
  fetchImpl?: typeof fetch;
  deps?: Omit<ControllerDeps, "fetchImpl">;
} = {}) {
  const [controller] = useState(
    () =>
      new SessionController({
        ...(fetchImpl ? { fetchImpl } : {}),
        ...(deps ?? {}),
      }),
  );
  const state = useStoreState(controller.store);
  const prevStageTick = useRef(state.stageTick);

  useEffect(() => {
    void controller.start(window.location.search);
    return () => controller.dispose();
  }, [controller]);

  // One-time scroll reset per stage transition (INV-0808): the new screen starts at its
  // top; there is no persistent scroll policing.
  useEffect(() => {
    if (state.stageTick !== prevStageTick.current) {
      prevStageTick.current = state.stageTick;
      window.scrollTo(0, 0);
    }
  }, [state.stageTick]);

  if (state.phase === "entering" || state.phase === "booting") {
    return (
      <main className="panel">
        <h1>Loading</h1>
        <p role="status">Preparing your session…</p>
      </main>
    );
  }
  if (state.phase === "refused") {
    return <RefusedScreen state={state} />;
  }

  const store = controller.store;
  const onPanelScreen = !WORKBENCH_STAGES.has(state.stage ?? "entry");
  const leaseBanner =
    state.leaseNotice && onPanelScreen ? (
      <div className="notice-bar" role="alert">
        This session may be open in another window — this window is read-only.
        <button type="button" onClick={() => void store.takeover().catch(() => undefined)}>
          Take over here
        </button>
      </div>
    ) : null;
  // BA-26: while the boot resume is still unresolved the writer is "unknown" — panel
  // controls are inert and a neutral pending note stands in (workbench stages render
  // their own inside Workbench).
  const writerPending =
    state.writer === "unknown" && !state.authFailed && onPanelScreen ? (
      <div className="notice-bar" role="status">
        Preparing your session…
      </div>
    ) : null;

  let screen: React.ReactNode;
  switch (state.stage) {
    case "consent":
      screen = <ConsentScreen state={state} store={store} />;
      break;
    case "instructions":
      screen = <InstructionsScreen state={state} store={store} />;
      break;
    case "training":
      screen = <TrainingScreen state={state} store={store} />;
      break;
    case "ai_qa":
      screen = <AiQaScreen state={state} store={store} />;
      break;
    case "scripted_connecting":
      screen = <ConnectingScreen state={state} store={store} />;
      break;
    case "ready":
      screen = <ReadyScreen />;
      break;
    case "solo_review":
    case "task":
    case "checkin_collect":
    case "checkin_wait":
    case "checkin_reveal":
      screen = <Workbench state={state} store={store} telemetry={controller.telemetry} />;
      break;
    case "answers_locked":
      screen = <LockedScreen state={state} store={store} />;
      break;
    case "confidence":
    case "peer_ratings":
    case "post_questionnaire":
    case "open_ended":
      if (INSTRUMENT_STAGES.has(state.stage)) {
        screen = <InstrumentScreen state={state} store={store} telemetry={controller.telemetry} />;
      }
      break;
    case "debrief":
      screen = <DebriefScreen state={state} store={store} />;
      break;
    case "data_use_choice":
      // UX round 3, item 11: the normal debrief Continue passes through this stage
      // mid-chain — keep the (same, still-mounted) debrief on screen until
      // completion lands. Only a session that genuinely sits here (reload/resume)
      // or a failed chain shows the resume screen.
      screen = state.handoffInFlight ? (
        <DebriefScreen state={state} store={store} />
      ) : (
        <DataUseScreen state={state} store={store} />
      );
      break;
    case "completion":
      screen = <CompletionScreen state={state} />;
      break;
    default:
      if (state.stage !== null && TERMINAL_STAGES.has(state.stage)) {
        screen = <TerminalScreen state={state} />;
      } else {
        screen = (
          <main className="panel">
            <h1>Loading</h1>
            <p role="status">Preparing your session…</p>
          </main>
        );
      }
  }

  // RL 2026-10-05: no on-screen withdrawal affordance — IRB does not require
  // one (closing the browser abandons with identical preserved-record +
  // entitlement treatment), and a persistent "Leave the study" link suggests
  // leaving. The ``withdraw`` command stays reachable for admin/monitor use.
  return (
    <>
      {leaseBanner}
      {writerPending}
      {screen}
    </>
  );
}
