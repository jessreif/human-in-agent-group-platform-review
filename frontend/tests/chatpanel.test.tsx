/**
 * ChatPanel drag-share telemetry tests (2026-10-12 plan, audit G01): dropping
 * a case-file card into the team chat emits ``share_attempt`` with
 * ``via: "drag"`` — sent on success, ``ignored_not_own`` for teammate-added
 * records (which never reach the store).
 */
import { cleanup, fireEvent, render, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FileRecord } from "../src/api/types";
import { ChatPanel } from "../src/components/ChatPanel";
import type { SessionStore, StoreState } from "../src/state/store";
import type { TelemetryQueue } from "../src/telemetry";

const RECORDS: FileRecord[] = [
  {
    record_id: "r1",
    crate: 1,
    stamp: "Today, 8:55 AM",
    body: "record one",
    event_type: "arrival",
    source: "initial",
  },
  {
    record_id: "r3",
    crate: 3,
    stamp: "3 days ago, 9:30 AM",
    body: "record three",
    event_type: "still_at",
    source: "added_from_team",
    shared_by: "JT",
  },
];

function makeState(): StoreState {
  return {
    file: RECORDS,
    messages: [],
    delivered: {},
    channels: ["file_work", "sharing", "structured_seeking", "social_reply"],
    taskClosed: false,
    initials: null,
    checkin: { phase: "closed", revealedValues: null, ownValue: null },
    clock: null,
    greetingOptions: null,
    greetingAnchorActiveMs: null,
    structuredRequests: false,
    assignedCrates: [],
    crateItems: {},
  } as unknown as StoreState;
}

function makeStore(): SessionStore {
  return {
    shareRecord: vi.fn().mockResolvedValue({ accepted: true }),
    sendTeamReply: vi.fn().mockResolvedValue({ accepted: true }),
    askTeam: vi.fn().mockResolvedValue({ accepted: true }),
    sendGreeting: vi.fn().mockResolvedValue({ accepted: true }),
  } as unknown as SessionStore;
}

function dropRecord(container: HTMLElement, recordId: string) {
  const pane = container.querySelector('[data-pane="team-chat"]') as HTMLElement;
  fireEvent.drop(pane, {
    dataTransfer: {
      getData: (t: string) => (t === "text/record-id" ? recordId : ""),
      types: ["text/record-id"],
    },
  });
}

describe("ChatPanel drag-share telemetry", () => {
  let emit: ReturnType<typeof vi.fn>;
  let telemetry: TelemetryQueue;
  let store: SessionStore;

  beforeEach(() => {
    emit = vi.fn();
    telemetry = { emit } as unknown as TelemetryQueue;
    store = makeStore();
  });

  afterEach(() => {
    cleanup();
  });

  function emitCalls(kind: string): unknown[][] {
    return emit.mock.calls.filter((c) => c[0] === kind);
  }

  it("emits share_attempt via:drag sent when an own record is dropped", async () => {
    const { container } = render(
      <ChatPanel state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    await act(async () => {
      dropRecord(container, "r1");
    });
    expect(store.shareRecord).toHaveBeenCalledWith("r1");
    expect(emitCalls("share_attempt")).toEqual([
      ["share_attempt", { record_id: "r1", via: "drag", result: "sent" }],
    ]);
  });

  it("emits ignored_not_own and never reaches the store for teammate intel", async () => {
    const { container } = render(
      <ChatPanel state={makeState()} store={store} telemetry={telemetry} disabled={false} />,
    );
    await act(async () => {
      dropRecord(container, "r3");
    });
    expect(store.shareRecord).not.toHaveBeenCalled();
    expect(emitCalls("share_attempt")).toEqual([
      ["share_attempt", { record_id: "r3", via: "drag", result: "ignored_not_own" }],
    ]);
  });
});
