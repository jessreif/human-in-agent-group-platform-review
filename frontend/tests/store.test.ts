/**
 * Store state-machine tests (INV-0810): resync-on-gap, stale-revision retry, draft
 * preservation, deadline lock, warning display and writer-lease takeover — all without
 * a DOM or network.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { ApiError } from "../src/api/client";
import type {
  CommandResult,
  EventsResponse,
  ParticipantEvent,
  SessionResponse,
  WorkbenchResponse,
} from "../src/api/types";
import {
  ANSWER_DEBOUNCE_MS,
  SAVED_FADE_MS,
  WORKBENCH_RETRY_MS,
  SessionStore,
  type StoreApi,
} from "../src/state/store";

let uuidCounter = 0;
function nextUuid(): string {
  uuidCounter += 1;
  return `00000000-0000-4000-8000-${String(uuidCounter).padStart(12, "0")}`;
}

function event(seq: number, type: string, payload: Record<string, unknown> = {}, extra = {}): ParticipantEvent {
  return {
    seq,
    event_id: `evt-${seq}`,
    stage: "task",
    actor: "system",
    type,
    revision: seq,
    active_ms: seq * 1000,
    server_time: "2026-01-01T00:00:00+00:00",
    payload,
    ...extra,
  } as ParticipantEvent;
}

function sessionResponse(stage = "task", revision = 0, nextSeq = 1): SessionResponse {
  return {
    session: {
      session_id: "s-1",
      stage,
      revision,
      mode: "live",
      manifest_id: "tp_demo",
      study_key: "tp",
      timing_mode: "active",
      next_event_seq: nextSeq,
      compatibility_key: "k",
      terminal_reason: null,
      completion_code: null,
      writer_held: true,
      stage_entered_at: null,
    },
    clock: {
      server_now: "2026-01-01T00:00:00+00:00",
      active_ms: 100_000,
      budget_ms: 360_000,
      clock_running: true,
    },
  } as SessionResponse;
}

function workbenchResponse(overrides: Partial<WorkbenchResponse> = {}): WorkbenchResponse {
  return {
    stage: "task",
    revision: 0,
    server_now: "2026-01-01T00:00:00+00:00",
    world_ready: true,
    locations: ["The Sawmill", "The Marina", "Blackwell Storage"],
    assigned_crates: [2, 4, 6, 8],
    file: [
      {
        record_id: "r1",
        crate: 2,
        stamp: "Today, 9:00 AM",
        body: "Crate 2 seen at The Sawmill.",
        event_type: "arrival",
        source: "initial",
      },
    ],
    delivered: [],
    shared: [],
    answers: {},
    frozen_answers: {},
    channels: ["file_work", "answer_drafting", "structured_seeking", "sharing", "social_reply", "assistant_prompting"],
    instrument: null,
    material: null,
    exposure: null,
    practice: null,
    ...overrides,
  };
}

function commandResult(overrides: Partial<CommandResult> = {}): CommandResult {
  return {
    command_id: nextUuid(),
    accepted: true,
    reason: null,
    committed_revision: 5,
    result: {},
    event_seqs: [],
    replayed: false,
    ...overrides,
  };
}

function makeApi(overrides: Partial<StoreApi> = {}): StoreApi & {
  postCommand: Mock;
  getEvents: Mock;
  getSession: Mock;
  getWorkbench: Mock;
  postComplete: Mock;
} {
  const api = {
    getSession: vi.fn(async () => sessionResponse()),
    getEvents: vi.fn(
      async (afterSeq: number): Promise<EventsResponse> => ({
        events: [],
        scanned_seq: afterSeq,
        next_event_seq: afterSeq + 1,
      }),
    ),
    getWorkbench: vi.fn(async () => workbenchResponse()),
    postCommand: vi.fn(async () => commandResult()),
    postComplete: vi.fn(async () => commandResult()),
    ...overrides,
  };
  return api as unknown as StoreApi & {
    postCommand: Mock;
    getEvents: Mock;
    getSession: Mock;
    getWorkbench: Mock;
    postComplete: Mock;
  };
}

// Helpers register here. afterEach disposes every store, including when a
// test throws before its own dispose().
const constructedStores: SessionStore[] = [];

function makeStore(api: StoreApi): SessionStore {
  const store = new SessionStore({ api, uuid: nextUuid, now: () => 10_000 });
  constructedStores.push(store);
  return store;
}

beforeEach(() => {
  constructedStores.length = 0;
});

afterEach(() => {
  for (const store of constructedStores) store.dispose();
  constructedStores.length = 0;
  try {
    if (vi.isFakeTimers()) expect(vi.getTimerCount()).toBe(0);
  } finally {
    if (vi.isFakeTimers()) vi.useRealTimers();
  }
});

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// refreshWorkbench's continuation and the coalescer's finally are two
// microtasks behind the fetch promise the store is awaiting.
function afterCoalescerTurn(slot: { promise: Promise<unknown> }): Promise<void> {
  return slot.promise.then(
    () =>
      new Promise<void>((resolve) => {
        queueMicrotask(() => {
          queueMicrotask(() => resolve());
        });
      }),
  );
}

describe("event cursor integrity (resync-on-gap)", () => {
  it("applies events in order and advances the cursor", async () => {
    const store = makeStore(makeApi());
    store.applyEvents([event(1, "resumed"), event(2, "resumed")], { nextEventSeq: 3 });
    expect(store.getSnapshot().cursor).toBe(2);
    expect(store.getSnapshot().revision).toBe(2);
    expect(store.getSnapshot().headSeq).toBe(2);
  });

  it("detects a replayed seq with a different event_id and resyncs from 0", async () => {
    const api = makeApi({
      getEvents: vi.fn(async () => ({
        events: [event(1, "resumed"), event(2, "resumed")],
        scanned_seq: 2,
        next_event_seq: 3,
      })),
    });
    const store = makeStore(api);
    store.applyEvents([event(1, "resumed")]);
    // Replay claims seq 1 now belongs to a different event → integrity violation.
    const ok = store.applyEvents([
      { ...event(1, "resumed"), event_id: "evt-different" },
    ]);
    expect(ok).toBe(false);
    await vi.waitFor(() => expect(api.getEvents).toHaveBeenCalledWith(0));
    // After replay the cursor reflects the server's committed list.
    expect(store.getSnapshot().cursor).toBe(2);
  });

  it("keeps the cursor monotonic when duplicate events arrive", () => {
    const store = makeStore(makeApi());
    store.applyEvents([event(1, "resumed"), event(2, "resumed")]);
    store.applyEvents([event(1, "resumed")]); // replayed dup — ignored
    expect(store.getSnapshot().cursor).toBe(2);
  });
});

describe("commands and revisions", () => {
  it("retries a stale_revision command with a fresh id after resync", async () => {
    const postCommand = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, "stale", "stale_revision"))
      .mockResolvedValueOnce(commandResult({ committed_revision: 9 }));
    const api = makeApi({
      postCommand,
      getSession: vi.fn(async () => sessionResponse("task", 7, 5)),
    });
    const store = makeStore(api);
    store.setWriter("held");
    const result = await store.askTeam(3);
    expect(postCommand).toHaveBeenCalledTimes(2);
    const [first, second] = postCommand.mock.calls.map((c) => c[0]);
    expect(first.command_id).not.toBe(second.command_id);
    // Second attempt carries the resynchronized expected revision.
    expect(second.expected_revision).toBe(7);
    expect(result.committed_revision).toBe(9);
  });

  it("never silently swallows a rejection — wrong_stage surfaces", async () => {
    const api = makeApi({
      postCommand: vi.fn(async () => {
        throw new ApiError(409, "rejected", "wrong_stage");
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    await expect(store.askTeam(3)).rejects.toMatchObject({ kind: "wrong_stage" });
  });

  it("surfaces writer_lease_held as a read-only takeover state", async () => {
    const api = makeApi({
      postCommand: vi
        .fn()
        .mockRejectedValueOnce(new ApiError(409, "held", "writer_lease_held"))
        .mockResolvedValueOnce(commandResult({ result: { writer_id: "w-2" } })),
    });
    const store = makeStore(api);
    store.setWriter("held");
    await expect(store.askTeam(3)).rejects.toMatchObject({ kind: "writer_lease_held" });
    expect(store.getSnapshot().writer).toBe("readonly");
    expect(store.getSnapshot().leaseNotice).toBe(true);
    await store.takeover();
    expect(store.getSnapshot().writer).toBe("held");
    expect(store.getSnapshot().leaseNotice).toBe(false);
  });
});

describe("dispatch lease gate (BA-26)", () => {
  it("refuses mutating commands locally while the lease is unconfirmed, but allows takeover", async () => {
    const api = makeApi({
      postCommand: vi.fn(async () => commandResult({ result: { writer_id: "w-9" } })),
    });
    const store = makeStore(api); // writer starts "unknown" — not yet confirmed
    await expect(store.askTeam(3)).rejects.toMatchObject({ kind: "writer_lease_held" });
    // Refused before ever touching the network, and the takeover affordance shows.
    expect(api.postCommand).not.toHaveBeenCalled();
    expect(store.getSnapshot().writer).toBe("readonly");
    expect(store.getSnapshot().leaseNotice).toBe(true);

    await store.takeover();
    expect(api.postCommand).toHaveBeenCalledWith(expect.objectContaining({ kind: "takeover" }));
    expect(store.getSnapshot().writer).toBe("held");
    expect(store.getSnapshot().leaseNotice).toBe(false);
  });

  it("refuses mutating commands while explicitly read-only too", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setWriter("readonly", true);
    await expect(store.proceed()).rejects.toMatchObject({ kind: "writer_lease_held" });
    expect(api.postCommand).not.toHaveBeenCalled();
  });

  it("gates the /complete handoff the same way", async () => {
    const api = makeApi();
    const store = makeStore(api);
    await expect(store.complete()).rejects.toMatchObject({ kind: "writer_lease_held" });
    expect(api.postComplete).not.toHaveBeenCalled();
    store.setWriter("held");
    await store.complete();
    expect(api.postComplete).toHaveBeenCalledTimes(1);
  });
});

describe("answer drafts (INV-0806)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("keeps an unsent edit dirty and preserves it across a workbench resync", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setAnswerDraft(2, "The Marina");
    expect(store.getSnapshot().answers[2]?.status).toBe("dirty");
    await store.refreshWorkbench();
    // The server still has no answer for crate 2 — the draft is untouched.
    expect(store.getSnapshot().answers[2]?.value).toBe("The Marina");
    expect(store.getSnapshot().answers[2]?.status).toBe("dirty");
  });

  it("marks the draft saved only after the command ack", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(4, "The Sawmill");
    await store.flushAnswer(4);
    expect(api.postCommand).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "answer_update", payload: { crate: 4, location_id: "The Sawmill" } }),
    );
    expect(store.getSnapshot().answers[4]?.status).toBe("saved");
    expect(store.getSnapshot().answers[4]?.committed).toBe("The Sawmill");
  });

  it("a late answer rejected as task_closed is never shown as saved", async () => {
    const api = makeApi({
      postCommand: vi.fn(async () => {
        throw new ApiError(409, "closed", "task_closed");
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(6, "The Marina");
    await store.flushAnswer(6);
    const entry = store.getSnapshot().answers[6];
    expect(entry?.status).toBe("rejected");
    expect(entry?.error).toBe("closed");
    expect(entry?.value).toBe("The Marina"); // the attempt stays visible
  });

  it("a lease refusal keeps the draft dirty rather than rejected", async () => {
    const api = makeApi({
      postCommand: vi.fn(async () => {
        throw new ApiError(409, "held", "writer_lease_held");
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(8, "The Sawmill");
    await store.flushAnswer(8);
    expect(store.getSnapshot().answers[8]?.status).toBe("dirty");
    expect(store.getSnapshot().leaseNotice).toBe(true);
  });

  it("advancing time after dispose emits no debounced command and no notification", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setWriter("held");
    let notifications = 0;
    store.subscribe(() => {
      notifications += 1;
    });
    store.setAnswerDraft(2, "The Marina");
    expect(notifications).toBe(1);
    expect(api.postCommand).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(1);
    store.dispose();
    await vi.advanceTimersByTimeAsync(ANSWER_DEBOUNCE_MS);
    expect(api.postCommand).not.toHaveBeenCalled();
    expect(notifications).toBe(1);
  });

  it("advancing time after dispose emits no fade and no notification", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(4, "The Sawmill");
    await store.flushAnswer(4);
    expect(store.getSnapshot().answers[4]?.status).toBe("saved");
    expect(api.postCommand).toHaveBeenCalledTimes(1);
    let notifications = 0;
    store.subscribe(() => {
      notifications += 1;
    });
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    store.dispose();
    await vi.advanceTimersByTimeAsync(ANSWER_DEBOUNCE_MS + SAVED_FADE_MS);
    expect(store.getSnapshot().answers[4]?.status).toBe("saved");
    expect(api.postCommand).toHaveBeenCalledTimes(1);
    expect(notifications).toBe(0);
  });

  it("a throw before dispose still leaves the debounce timer for afterEach to clear", () => {
    const api = makeApi();
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(2, "The Marina");
    expect(vi.getTimerCount()).toBe(1);
    try {
      throw new Error("stop before dispose");
    } catch {
      // The timer stays armed; the registry afterEach is what clears it.
    }
    expect(vi.getTimerCount()).toBe(1);
  });
});

describe("deadline, warning and check-in events", () => {
  it("freezes answers on task_deadline / answers_frozen", async () => {
    const store = makeStore(makeApi());
    store.applyEvents([event(3, "task_deadline")]);
    expect(store.getSnapshot().taskClosed).toBe(true);
    store.applyEvents([event(4, "answers_frozen", { crates: [2, 4] })]);
    expect(store.getSnapshot().taskClosed).toBe(true);
  });

  it("displays the committed warning message and activates low-time", () => {
    const store = makeStore(makeApi());
    store.applyEvents([
      event(5, "message_delivered", {
        message_id: "m-warn",
        channel: "system",
        body: "One minute left.",
      }),
      event(6, "warning_committed", { message_id: "m-warn", low_time: true }),
    ]);
    const s = store.getSnapshot();
    expect(s.lowTimeActive).toBe(true);
    expect(s.warningMessageId).toBe("m-warn");
    expect(s.messages.some((m) => m.body === "One minute left.")).toBe(true);
  });

  it("opens the check-in overlay on checkin_open and reveals values", () => {
    const store = makeStore(makeApi());
    store.applyEvents([
      event(7, "checkin_open", {
        assigned_values: { JT: 6, PM: 7 },
        copy: {
          question: "How much time pressure?",
          scale_min_label: "1",
          scale_max_label: "7",
          note: "n",
          collecting: "collecting…",
          reveal_title: "revealed",
          reveal_note: "r",
          resume_label: "Resume task",
        },
      }),
    ]);
    expect(store.getSnapshot().checkin.phase).toBe("collect");
    store.applyEvents([event(8, "checkin_reveal", { assigned_values: { JT: 6, PM: 7 } })]);
    expect(store.getSnapshot().checkin.phase).toBe("reveal");
    expect(store.getSnapshot().checkin.revealedValues).toEqual({ JT: 6, PM: 7 });
  });

  it("closes the check-in overlay when the authoritative stage moves on", () => {
    const store = makeStore(makeApi());
    store.applyEvents([event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })]);
    expect(store.getSnapshot().checkin.phase).toBe("collect");
    // The stage is authoritative — a missed stage_proceeded must not pin the overlay.
    store.applySessionResponse(sessionResponse("task"));
    expect(store.getSnapshot().checkin.phase).toBe("closed");
  });

  it("a wrong_stage refusal during the check-in pause stays dirty and saves on resume", async () => {
    // v15 (observed live): a crate pick landing in the same instant the
    // check-in opens gets answer_update refused with wrong_stage — the pause
    // stages accept no task commands. The crate must not freeze as
    // "Not saved — task closed"; the draft re-flushes when the pause lifts.
    vi.useFakeTimers();
    const api = makeApi({
      postCommand: vi
        .fn()
        .mockRejectedValueOnce(new ApiError(409, "rejected", "wrong_stage"))
        .mockResolvedValue(commandResult()),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.applyEvents([
      event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } }),
    ]);
    store.setAnswerDraft(6, "The Marina");
    await store.flushAnswer(6);
    expect(store.getSnapshot().answers[6]?.status).toBe("dirty");
    expect(store.getSnapshot().answers[6]?.error).toBe("wrong_stage");
    // Resume: the authoritative stage returning to task re-queues the draft.
    store.applySessionResponse(sessionResponse("task", 8));
    await vi.advanceTimersByTimeAsync(ANSWER_DEBOUNCE_MS);
    expect(api.postCommand).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().answers[6]?.status).toBe("saved");
    expect(store.getSnapshot().answers[6]?.committed).toBe("The Marina");
  });

  it("a wrong_stage refusal after the task has closed still marks the crate terminal", async () => {
    // Guard: wrong_stage is transient only while the task could still resume —
    // post-lock it stays the honest terminal "Not saved — task closed".
    const api = makeApi({
      postCommand: vi.fn(async () => {
        throw new ApiError(409, "rejected", "wrong_stage");
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.applySessionResponse(sessionResponse("answers_locked", 5));
    store.setAnswerDraft(6, "The Marina");
    await store.flushAnswer(6);
    expect(store.getSnapshot().answers[6]?.status).toBe("rejected");
    expect(store.getSnapshot().answers[6]?.error).toBe("closed");
  });

  // BA-90: the displayed window anchors to the event's committed server_time,
  // never to client receipt — a reload/delayed replay must not restart it.
  it("anchors the check-in countdown to server_time, not receipt (BA-90)", () => {
    const store = makeStore(makeApi());
    const opened = Date.now() - 90_000; // opened 90 s ago, replayed now
    store.applyEvents([
      event(
        7,
        "checkin_open",
        { assigned_values: { JT: 6, PM: 7 } },
        { server_time: new Date(opened).toISOString() },
      ),
    ]);
    const anchor = store.getSnapshot().checkin.openedAtMs;
    expect(anchor).not.toBeNull();
    expect(Math.abs(anchor! - opened)).toBeLessThan(2_000);
    expect(anchor!).toBeLessThan(Date.now() - 80_000);
  });

  it("clamps a future checkin_open server_time to now — never extends the window", () => {
    const store = makeStore(makeApi());
    const future = Date.now() + 90_000;
    const before = Date.now();
    store.applyEvents([
      event(
        7,
        "checkin_open",
        { assigned_values: { JT: 6, PM: 7 } },
        { server_time: new Date(future).toISOString() },
      ),
    ]);
    const anchor = store.getSnapshot().checkin.openedAtMs!;
    expect(anchor).toBeGreaterThanOrEqual(before);
    expect(anchor).toBeLessThanOrEqual(Date.now());
  });

  // 2026-10-05: a stale_revision resync mid-submit replayed checkin_open before
  // the response was committed — the overlay visibly re-asked the rating then
  // jumped to the reveal. The phase must never regress.
  it("moves to wait optimistically on click — no collect re-render mid round-trip", async () => {
    let resolve: (r: CommandResult) => void = () => undefined;
    const api = makeApi({
      postCommand: vi.fn(
        () => new Promise<CommandResult>((res) => { resolve = res; }),
      ),
      getWorkbench: vi.fn(async () => workbenchResponse({ stage: "checkin_wait" })),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.applyEvents([event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })]);
    const pending = store.submitCheckin(5);
    // The phase is wait synchronously — before the command resolves.
    expect(store.getSnapshot().checkin.phase).toBe("wait");
    expect(store.getSnapshot().checkin.ownValue).toBe(5);
    resolve(commandResult({ result: { stage: "checkin_wait" } }));
    await pending;
    expect(store.getSnapshot().checkin.phase).toBe("wait");
    expect(store.getSnapshot().checkin.submitStatus).toBe("saved");
  });

  it("reverts the optimistic wait to collect when the submit fails", async () => {
    const api = makeApi({
      postCommand: vi.fn(async () => {
        throw new ApiError(422, "no open check-in", "no_open_checkin");
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.applyEvents([event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })]);
    await store.submitCheckin(5).catch(() => undefined);
    const c = store.getSnapshot().checkin;
    expect(c.phase).toBe("collect");
    expect(c.submitStatus).toBe("rejected");
  });

  it("keeps the check-in at wait through a mid-submit resync — replayed open never re-asks", async () => {
    const api = makeApi({
      getSession: vi.fn(async () => sessionResponse("checkin_collect")),
      // The response is not yet committed — the replay ends at checkin_open.
      getEvents: vi.fn(async () => ({
        events: [event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })],
        scanned_seq: 7,
        next_event_seq: 8,
      })),
      getWorkbench: vi.fn(async () => workbenchResponse({ stage: "checkin_collect" })),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.applyEvents([event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })]);
    const pending = store.submitCheckin(5);
    await vi.waitFor(() => expect(api.postCommand).toHaveBeenCalled());
    expect(store.getSnapshot().checkin.phase).toBe("wait");
    await pending;
    await store.resync();
    expect(store.getSnapshot().checkin.phase).toBe("wait");
  });

  it("keeps the reveal through a resync — replayed open/response never regress", async () => {
    const api = makeApi({
      getSession: vi.fn(async () => sessionResponse("checkin_reveal")),
      getEvents: vi.fn(async () => ({
        events: [
          event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } }),
          event(8, "checkin_response"),
          event(9, "checkin_reveal", {
            assigned_values: { JT: 6, PM: 7 },
            own_rating: 5,
          }),
        ],
        scanned_seq: 9,
        next_event_seq: 10,
      })),
      getWorkbench: vi.fn(async () => workbenchResponse({ stage: "checkin_reveal" })),
    });
    const store = makeStore(api);
    store.applyEvents([
      event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } }),
      event(8, "checkin_response"),
      event(9, "checkin_reveal", { assigned_values: { JT: 6, PM: 7 }, own_rating: 5 }),
    ]);
    expect(store.getSnapshot().checkin.phase).toBe("reveal");
    await store.resync();
    const c = store.getSnapshot().checkin;
    expect(c.phase).toBe("reveal");
    expect(c.ownValue).toBe(5);
    expect(c.revealedValues).toEqual({ JT: 6, PM: 7 });
  });

  it("still re-opens at collect on resync when no response was ever committed", async () => {
    const api = makeApi({
      getSession: vi.fn(async () => sessionResponse("checkin_collect")),
      getEvents: vi.fn(async () => ({
        events: [event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })],
        scanned_seq: 7,
        next_event_seq: 8,
      })),
      getWorkbench: vi.fn(async () => workbenchResponse({ stage: "checkin_collect" })),
    });
    const store = makeStore(api);
    store.applyEvents([event(7, "checkin_open", { assigned_values: { JT: 6, PM: 7 } })]);
    expect(store.getSnapshot().checkin.phase).toBe("collect");
    await store.resync();
    expect(store.getSnapshot().checkin.phase).toBe("collect");
  });
});

describe("stage transitions (INV-0805)", () => {
  it("refreshes the workbench projection when the stage changes", async () => {
    const getWorkbench = vi.fn(async () => workbenchResponse({ stage: "solo_review" }));
    const api = makeApi({ getWorkbench });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("consent"));
    expect(getWorkbench).not.toHaveBeenCalled();
    store.applySessionResponse(sessionResponse("solo_review"));
    await Promise.resolve();
    await Promise.resolve();
    expect(getWorkbench).toHaveBeenCalledTimes(1);
  });
});

describe("share surfacing (INV-0802)", () => {
  it("renders the participant's own share as a team chat message", async () => {
    const store = makeStore(makeApi());
    store.applySessionResponse(sessionResponse());
    await store.refreshWorkbench();
    store.applyEvents([
      event(1, "record_shared", { record_id: "r1", crate: 2 }, { actor: "participant" }),
    ]);
    const msg = store.getSnapshot().messages.find((m) => m.id === "share-1");
    expect(msg).toBeTruthy();
    expect(msg?.mine).toBe(true);
    expect(msg?.body).toBe("Sharing intel on Crate 2");
    expect(msg?.recordId).toBe("r1");
  });
});

describe("teammate share delivery (UX round 5, items 1–2)", () => {
  it("applies record_added_to_file synchronously — the chat card never sees a bare id", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse());
    await store.refreshWorkbench();
    // The delivery batch: the share message then the membership event, in the
    // same applyEvents pass. After the pass the record must already be in
    // state.file — before any workbench refetch resolves.
    store.applyEvents([
      event(2, "message_delivered", {
        message_id: "m1",
        actor: "JT",
        channel: "team",
        body: "Sharing intel on Crate 4",
        crate: 4,
        record_id: "shared-1",
      }),
      event(3, "record_added_to_file", {
        record_id: "shared-1",
        crate: 4,
        stamp: "Just now",
        body: "Crate 4 sighted near the depot.",
        event_type: "arrival",
        source: "added_from_team",
        shared_by: "JT",
      }),
    ]);
    const rec = store.getSnapshot().file.find((r) => r.record_id === "shared-1");
    expect(rec).toBeTruthy();
    expect(rec?.body).toBe("Crate 4 sighted near the depot.");
    expect(rec?.source).toBe("added_from_team");
    expect(rec?.shared_by).toBe("JT");
    await vi.waitFor(() => expect(api.getWorkbench.mock.calls.length).toBeGreaterThan(1));
  });

  it("dedupes a repeated record_added_to_file for the same record", async () => {
    const api = makeApi();
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse());
    await store.refreshWorkbench();
    const payload = {
      record_id: "shared-1",
      crate: 4,
      stamp: "Just now",
      body: "Crate 4 sighted near the depot.",
      event_type: "arrival",
      source: "added_from_team",
      shared_by: "JT",
    };
    store.applyEvents([
      event(2, "record_added_to_file", payload),
      event(3, "record_added_to_file", payload),
    ]);
    expect(
      store.getSnapshot().file.filter((r) => r.record_id === "shared-1").length,
    ).toBe(1);
  });

  it("coalesces per-event workbench refetches — a delivery burst is one fetch", async () => {
    const queued: Array<ReturnType<typeof deferred<WorkbenchResponse>>> = [];
    const api = makeApi({
      getWorkbench: vi.fn(() => {
        const slot = deferred<WorkbenchResponse>();
        queued.push(slot);
        return slot.promise;
      }),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse());
    const primed = store.refreshWorkbench();
    queued[0]?.resolve(workbenchResponse());
    await primed;
    const calls0 = api.getWorkbench.mock.calls.length;
    store.applyEvents([
      event(2, "record_added_to_file", { record_id: "a", body: "A" }),
      event(3, "record_added_to_file", { record_id: "b", body: "B" }),
      event(4, "record_added_to_file", { record_id: "c", body: "C" }),
    ]);
    // One turn collapses the burst onto the in-flight fetch. The trailing run
    // starts only after that fetch settles — not once per event.
    expect(api.getWorkbench.mock.calls.length).toBe(calls0 + 1);
    const inFlight = queued[calls0];
    expect(inFlight).toBeTruthy();
    const inFlightSettled = afterCoalescerTurn(inFlight!);
    inFlight!.resolve(workbenchResponse());
    await inFlightSettled;
    expect(api.getWorkbench.mock.calls.length).toBe(calls0 + 2);
    const trailing = queued[calls0 + 1];
    expect(trailing).toBeTruthy();
    const trailingSettled = afterCoalescerTurn(trailing!);
    trailing!.resolve(workbenchResponse());
    await trailingSettled;
    // A later turn does not schedule a third fetch.
    await Promise.resolve();
    expect(api.getWorkbench.mock.calls.length).toBe(calls0 + 2);
  });
});

describe("workbench projection retry", () => {
  it("recovers a Q&A answer without another event or a nonempty poll", async () => {
    vi.useFakeTimers();
    const pending = {
      exchange_id: "qa-1", seq: 1, question: "How many crates?",
      answer: "", status: "pending" as const, asked_at: null, answered_at: null,
    };
    const api = makeApi({
      getWorkbench: vi.fn()
        .mockRejectedValueOnce(new Error("temporary projection failure"))
        .mockResolvedValue(workbenchResponse({
          stage: "ai_qa", revision: 1,
          qa: [{ ...pending, answer: "Four crates.", status: "answered" }],
        })),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("ai_qa"));
    store.applyWorkbenchResponse(workbenchResponse({ stage: "ai_qa", qa: [pending] }));
    store.applyEvents([event(1, "qa_answered", {}, { stage: "ai_qa" })]);
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getSnapshot().qa[0]?.status).toBe("pending");
    await store.pollOnce(); // no events, no new trigger
    await vi.advanceTimersByTimeAsync(WORKBENCH_RETRY_MS);
    expect(api.getWorkbench).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().qa[0]?.answer).toBe("Four crates.");
    expect(store.getSnapshot().cursor).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("maps the bonus_payment deployment flag, defaulting true on older servers", () => {
    const store = makeStore(makeApi());
    store.applyWorkbenchResponse(workbenchResponse({ bonus_payment: false }));
    expect(store.getSnapshot().bonusPayment).toBe(false);
    store.applyWorkbenchResponse(workbenchResponse({ bonus_payment: true }));
    expect(store.getSnapshot().bonusPayment).toBe(true);
    // Absent on older servers — treated as the flag-on posture.
    store.applyWorkbenchResponse(workbenchResponse());
    expect(store.getSnapshot().bonusPayment).toBe(true);
  });

  it("cancels a scheduled retry on disposal", async () => {
    vi.useFakeTimers();
    const api = makeApi({ getWorkbench: vi.fn().mockRejectedValue(new Error("offline")) });
    const store = makeStore(api);
    await store.refreshWorkbench();
    expect(vi.getTimerCount()).toBe(1);
    store.dispose();
    await vi.advanceTimersByTimeAsync(WORKBENCH_RETRY_MS);
    expect(api.getWorkbench).toHaveBeenCalledTimes(1);
  });

  it("cancels a scheduled retry when another session takes over", async () => {
    vi.useFakeTimers();
    const api = makeApi({ getWorkbench: vi.fn().mockRejectedValue(new Error("offline")) });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("ai_qa"));
    await store.refreshWorkbench();
    expect(vi.getTimerCount()).toBe(1);
    const next = sessionResponse();
    next.session.session_id = "s-2";
    store.applySessionResponse(next);
    await vi.advanceTimersByTimeAsync(WORKBENCH_RETRY_MS);
    expect(api.getWorkbench).toHaveBeenCalledTimes(1);
  });

  it("does not schedule a retry for a late failure from a retired session", async () => {
    vi.useFakeTimers();
    let reject!: (error: Error) => void;
    const response = new Promise<WorkbenchResponse>((_, fail) => { reject = fail; });
    const api = makeApi({ getWorkbench: vi.fn(() => response) });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("ai_qa"));
    const refresh = store.refreshWorkbench();
    const next = sessionResponse();
    next.session.session_id = "s-2";
    store.applySessionResponse(next);
    reject(new Error("retired response"));
    await refresh;
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("file ordering (INV-0803)", () => {
  it("keeps a user reorder across workbench refreshes with new records appended", async () => {
    const file = [
      { record_id: "a", crate: 1, stamp: "Today, 9:00 AM", body: "A", event_type: "arrival", source: "initial" },
      { record_id: "b", crate: 2, stamp: "Today, 10:00 AM", body: "B", event_type: "arrival", source: "initial" },
      { record_id: "c", crate: 3, stamp: "Today, 11:00 AM", body: "C", event_type: "arrival", source: "initial" },
    ];
    const api = makeApi({
      getWorkbench: vi.fn(async () => workbenchResponse({ file })),
    });
    const store = makeStore(api);
    await store.refreshWorkbench();
    store.moveFileRecord("a", 1);
    expect(store.getSnapshot().fileOrder).toEqual(["b", "a", "c"]);
    await store.refreshWorkbench();
    expect(store.getSnapshot().fileOrder).toEqual(["b", "a", "c"]);
  });

  it("lands newly delivered intel at the top of the case file (UX v7)", async () => {
    const file = [
      { record_id: "a", crate: 1, stamp: "Today, 9:00 AM", body: "A", event_type: "arrival", source: "initial" },
      { record_id: "b", crate: 2, stamp: "Today, 10:00 AM", body: "B", event_type: "arrival", source: "initial" },
    ];
    const api = makeApi({
      getWorkbench: vi.fn(async () => workbenchResponse({ file })),
    });
    const store = makeStore(api);
    await store.refreshWorkbench();
    // The event path prepends immediately, before the coalesced refetch.
    store.applyEvents([
      event(2, "record_added_to_file", {
        record_id: "new-1",
        crate: 5,
        stamp: "Just now",
        body: "New intel",
        source: "added_from_team",
        shared_by: "JT",
      }),
    ]);
    expect(store.getSnapshot().fileOrder[0]).toBe("new-1");
  });
});

describe("shared-in-chat tag + greeting window (UX round 2)", () => {
  it("marks a participant share so the file shows 'Shared in team chat'", async () => {
    const store = makeStore(makeApi());
    store.applySessionResponse(sessionResponse());
    await store.refreshWorkbench();
    store.applyEvents([
      event(1, "record_shared", { record_id: "r1", crate: 2 }, { actor: "participant" }),
    ]);
    expect(store.getSnapshot().sharedRecordIds).toContain("r1");
  });

  it("rehydrates shared ids from the workbench projection", async () => {
    const api = makeApi({
      getWorkbench: vi.fn(async () => workbenchResponse({ shared: ["r1"] })),
    });
    const store = makeStore(api);
    await store.refreshWorkbench();
    expect(store.getSnapshot().sharedRecordIds).toEqual(["r1"]);
  });

  it("suppresses a greeting_prompt replayed after the 30-second window", () => {
    const store = makeStore(makeApi());
    store.applySessionResponse(sessionResponse()); // clock anchored at 100 s active
    store.applyEvents([event(1, "greeting_prompt", { options: ["Hi!"] })]);
    expect(store.getSnapshot().greetingOptions).toBeNull();
  });

  it("shows greeting options when the prompt arrives inside the window", () => {
    const store = makeStore(makeApi());
    const early = sessionResponse();
    early.clock.active_ms = 2_000;
    store.applySessionResponse(early);
    store.applyEvents([event(1, "greeting_prompt", { options: ["Hi!"] })]);
    expect(store.getSnapshot().greetingOptions).toEqual(["Hi!"]);
  });

  it("shows greeting options for a deferred review-first prompt (UX round 6)", () => {
    // Solo review defers greeting_prompt delivery to channel open (~60 s active);
    // anchoring the 30 s window on task start suppressed the buttons entirely.
    const store = makeStore(makeApi());
    const late = sessionResponse();
    late.clock.active_ms = 65_000;
    store.applySessionResponse(late);
    store.applyEvents([event(60, "greeting_prompt", { options: ["Hi!"] })]);
    expect(store.getSnapshot().greetingOptions).toEqual(["Hi!"]);
    expect(store.getSnapshot().greetingAnchorActiveMs).toBe(60_000);
  });
});

describe("2026-10-14 concurrent answer saves", () => {
  it("saves four rapid crate edits at consecutive revisions without competing retries", async () => {
    vi.useFakeTimers();
    let revision = 0;
    const api = makeApi({
      postCommand: vi.fn(async (body) => {
        await Promise.resolve();
        if (body.expected_revision !== revision) throw new ApiError(409, "stale", "stale_revision");
        revision += 1;
        return commandResult({ committed_revision: revision });
      }),
      getSession: vi.fn(async () => sessionResponse("task", revision)),
      getWorkbench: vi.fn(async () => workbenchResponse({ revision })),
    });
    const store = makeStore(api);
    store.setWriter("held");
    const crates = [2, 4, 6, 8];
    for (const crate of crates) store.setAnswerDraft(crate, "The Marina");
    await Promise.all(crates.map((crate) => store.flushAnswer(crate)));
    expect(api.postCommand).toHaveBeenCalledTimes(4);
    expect(api.postCommand.mock.calls.map(([body]) => body.expected_revision)).toEqual([0, 1, 2, 3]);
    expect(crates.map((crate) => store.getSnapshot().answers[crate]?.status)).toEqual(
      crates.map(() => "saved"),
    );
    expect(api.getSession).not.toHaveBeenCalled();
  });

  it("a failed save does not block other queued crates or pretend the failed draft was saved", async () => {
    vi.useFakeTimers();
    const api = makeApi();
    api.postCommand
      .mockRejectedValueOnce(new ApiError(503, "unavailable", "save_failed"))
      .mockResolvedValueOnce(commandResult());
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(2, "The Marina");
    store.setAnswerDraft(4, "The Sawmill");
    await Promise.all([store.flushAnswer(2), store.flushAnswer(4)]);
    expect(api.postCommand).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().answers[2]).toMatchObject({ status: "dirty", error: "save_failed" });
    expect(store.getSnapshot().answers[4]?.status).toBe("saved");
  });

  it("session replacement drops old queued saves and acknowledgements without blocking new answers", async () => {
    vi.useFakeTimers();
    let finishOld!: (result: CommandResult) => void;
    const api = makeApi();
    api.postCommand
      .mockImplementationOnce(() => new Promise<CommandResult>((resolve) => { finishOld = resolve; }))
      .mockResolvedValue(commandResult({ committed_revision: 1 }));
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse());
    store.setWriter("held");
    store.setAnswerDraft(2, "The Marina");
    store.setAnswerDraft(4, "The Marina");
    const old = [store.flushAnswer(2), store.flushAnswer(4)];
    const next = sessionResponse();
    next.session.session_id = "s-2";
    store.applySessionResponse(next);
    store.setWriter("held");
    store.setAnswerDraft(2, "The Sawmill");
    await store.flushAnswer(2);
    finishOld(commandResult({ committed_revision: 99 }));
    await Promise.all(old);
    expect(api.postCommand).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot()).toMatchObject({
      sessionId: "s-2", revision: 1, pendingCommands: 0,
      answers: { 2: { value: "The Sawmill", status: "saved" } },
    });
    expect(store.getSnapshot().answers[4]).toBeUndefined();
  });
});

describe("2026-10-02 audit remediations (BA-43/44/47)", () => {
  it("an older save acknowledgement cannot overwrite a newer draft (BA-43)", async () => {
    vi.useFakeTimers();
    const resolvers: Array<(r: CommandResult) => void> = [];
    const api = makeApi({
      postCommand: vi.fn(
        () => new Promise<CommandResult>((resolve) => resolvers.push(resolve)),
      ),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(2, "The Sawmill");
    const saving = store.flushAnswer(2);
    store.setAnswerDraft(2, "The Marina"); // typed while the first save is in flight
    resolvers[0]?.(commandResult());
    await saving;
    const entry = store.getSnapshot().answers[2];
    // The ack commits the value it actually sent; the newer draft stays dirty.
    expect(entry?.value).toBe("The Marina");
    expect(entry?.committed).toBe("The Sawmill");
    expect(entry?.status).toBe("dirty");
    // …and the newer draft gets its own save.
    await vi.advanceTimersByTimeAsync(450);
    expect(api.postCommand).toHaveBeenCalledTimes(2);
    expect(api.postCommand.mock.calls[1]?.[0].payload).toMatchObject({
      crate: 2,
      location_id: "The Marina",
    });
    store.dispose();
  });

  it("a second save cannot start while one is in flight for the same crate (BA-43)", async () => {
    vi.useFakeTimers();
    const resolvers: Array<(r: CommandResult) => void> = [];
    const api = makeApi({
      postCommand: vi.fn(
        () => new Promise<CommandResult>((resolve) => resolvers.push(resolve)),
      ),
    });
    const store = makeStore(api);
    store.setWriter("held");
    store.setAnswerDraft(2, "The Sawmill");
    void store.flushAnswer(2);
    void store.flushAnswer(2); // duplicate flush while in flight — must no-op
    expect(api.postCommand).toHaveBeenCalledTimes(1);
    resolvers[0]?.(commandResult());
    await vi.advanceTimersByTimeAsync(0);
    store.dispose();
  });

  it("completion retries a persisted stale_revision with a NEW command id (BA-44)", async () => {
    const ids: string[] = [];
    const revs: number[] = [];
    const api = makeApi({
      postComplete: vi.fn(async (body) => {
        ids.push(body.command_id);
        revs.push(body.expected_revision);
        if (ids.length === 1) throw new ApiError(409, "stale", "stale_revision");
        return commandResult({ committed_revision: 21 });
      }),
      getSession: vi.fn(async () => sessionResponse("data_use_choice", 20, 30)),
      getWorkbench: vi.fn(async () =>
        workbenchResponse({ stage: "data_use_choice", revision: 20 }),
      ),
    });
    const store = makeStore(api);
    store.setWriter("held");
    const result = await store.complete();
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]); // a persisted rejection is final for its id
    expect(revs[1]).toBe(20); // retry carries the resynchronized revision
    expect(result.committed_revision).toBe(21);
    store.dispose();
  });

  it("completion reuses the command id only across uncertain outcomes (BA-44)", async () => {
    const ids: string[] = [];
    const api = makeApi({
      postComplete: vi.fn(async (body) => {
        ids.push(body.command_id);
        if (ids.length === 1) throw new TypeError("network down");
        return commandResult({ committed_revision: 9 });
      }),
    });
    const store = makeStore(api);
    store.setWriter("held");
    await expect(store.complete()).rejects.toThrow("network down");
    // The response may have committed server-side — replay the same id.
    const result = await store.complete();
    expect(ids[0]).toBe(ids[1]);
    expect(result.accepted).toBe(true);
    store.dispose();
  });

  it("rejects a stale workbench projection instead of rolling state back (BA-47)", () => {
    const store = makeStore(makeApi());
    store.applyWorkbenchResponse(
      workbenchResponse({ stage: "confidence", revision: 20, shared: ["r1"] }),
    );
    store.applyWorkbenchResponse(
      workbenchResponse({ stage: "task", revision: 19, shared: [] }),
    );
    const snap = store.getSnapshot();
    expect(snap.revision).toBe(20);
    expect(snap.stage).toBe("confidence"); // older projection dropped, not maxed
    expect(snap.sharedRecordIds).toEqual(["r1"]);
    store.dispose();
  });

  it("rejects a stale session projection (BA-47)", () => {
    const store = makeStore(makeApi());
    store.applySessionResponse(sessionResponse("task", 20));
    store.applySessionResponse(sessionResponse("consent", 5));
    const snap = store.getSnapshot();
    expect(snap.stage).toBe("task");
    expect(snap.revision).toBe(20);
    store.dispose();
  });

  it("an old workbench cannot roll back a stage a newer session set (BA-47)", async () => {
    // The failing sequence: session rev 20 lands stage=confidence; a workbench
    // read issued earlier resolves rev 19 — it must be dropped whole.
    const api = makeApi({
      getWorkbench: vi.fn(async () =>
        workbenchResponse({ stage: "task", revision: 19 }),
      ),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("task", 10));
    const pending = store.refreshWorkbench();
    // A newer session snapshot lands while the workbench fetch is in flight.
    store.applySessionResponse(sessionResponse("confidence", 20));
    await pending;
    const snap = store.getSnapshot();
    expect(snap.stage).toBe("confidence"); // not rolled back to "task"
    expect(snap.revision).toBe(20);
    store.dispose();
  });

  it("a resync drops the fetched event replay when a session change lands mid-fetch (BA-47)", async () => {
    let resolveEvents!: (r: EventsResponse) => void;
    const api = makeApi({
      getSession: vi.fn(async () => sessionResponse("task", 30)),
      getEvents: vi.fn(
        () => new Promise<EventsResponse>((r) => { resolveEvents = r; }),
      ),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("task", 30));
    const pending = store.resync();
    // Let the resync's session fetch resolve and the events fetch begin.
    for (let i = 0; i < 10 && !api.getEvents.mock.calls.length; i += 1) {
      await Promise.resolve();
    }
    expect(api.getEvents).toHaveBeenCalled();
    // A different session arrives while the replay is still in flight.
    const reentry = sessionResponse("consent", 0);
    reentry.session.session_id = "s-2";
    store.applySessionResponse(reentry);
    resolveEvents({
      events: [event(1, "stage_proceeded", { to_stage: "completion" })],
      scanned_seq: 1,
      next_event_seq: 2,
    });
    await pending;
    const snap = store.getSnapshot();
    expect(snap.sessionId).toBe("s-2");
    expect(snap.stage).toBe("consent"); // the old session's replay never applied
    store.dispose();
  });

  it("a handoff failure after debrief_ack stays visible on the next screen (BA-44)", async () => {
    // The audit's gap: debrief_ack transitions the stage — an error raised later
    // in the chain lived in the unmounted DebriefScreen. finishHandoff stores it.
    const api = makeApi({
      postCommand: vi.fn(async (body: { kind: string }) => {
        if (body.kind === "debrief_ack") {
          return commandResult({
            result: { stage: "data_use_choice" },
            committed_revision: 12,
          });
        }
        if (body.kind === "data_use_choice") {
          return commandResult({
            result: { stage: "data_use_choice" },
            committed_revision: 13,
          });
        }
        return commandResult();
      }),
      postComplete: vi.fn(async () => {
        throw new ApiError(500, "server exploded", "internal_error");
      }),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("debrief", 10));
    store.setWriter("held");
    await expect(store.finishHandoff()).rejects.toThrow("server exploded");
    const snap = store.getSnapshot();
    // The visible stage is data_use_choice and the error is readable there.
    expect(snap.stage).toBe("data_use_choice");
    expect(snap.handoffError).toContain("try again");
    store.dispose();
  });

  it("finishHandoff skips debrief_ack when the session already advanced (BA-44)", async () => {
    // A resync may advance the stage before retry — the chain re-checks the live
    // stage rather than re-submitting a command the server already committed.
    const kinds: string[] = [];
    const api = makeApi({
      postCommand: vi.fn(async (body: { kind: string }) => {
        kinds.push(body.kind);
        return commandResult({ committed_revision: 21 });
      }),
      postComplete: vi.fn(async () => commandResult({ committed_revision: 22 })),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("data_use_choice", 20));
    store.setWriter("held");
    await store.finishHandoff();
    expect(kinds).toEqual(["data_use_choice"]); // no debrief_ack re-post
    expect(store.getSnapshot().handoffError).toBeNull();
    store.dispose();
  });

  it("drops a workbench fetch that resolved after a session re-entry (BA-47)", async () => {
    let resolveWb!: (r: WorkbenchResponse) => void;
    const api = makeApi({
      getWorkbench: vi.fn(
        () => new Promise<WorkbenchResponse>((r) => { resolveWb = r; }),
      ),
    });
    const store = makeStore(api);
    store.applySessionResponse(sessionResponse("task", 10));
    const pending = store.refreshWorkbench();
    // Re-entry into a different session retires every in-flight read.
    const reentry = sessionResponse("consent", 0);
    reentry.session.session_id = "s-2";
    store.applySessionResponse(reentry);
    resolveWb(workbenchResponse({ stage: "completion", revision: 99 }));
    await pending;
    const snap = store.getSnapshot();
    expect(snap.sessionId).toBe("s-2");
    expect(snap.stage).toBe("consent"); // the stale fetch never landed
    expect(snap.revision).toBe(0);
    store.dispose();
  });
});
