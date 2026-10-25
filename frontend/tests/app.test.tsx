/**
 * App-level tests (INV-0810): boot refusal, the simultaneous workbench render, keyboard
 * paths for share/answer actions, low-time warning display and deadline lock.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { API_BASE, ApiError, apiUrl, getHealth } from "../src/api/client";
import { SessionController } from "../src/state/controller";
import { canWrite } from "../src/state/store";

// jsdom's BroadcastChannel makes detectClone wait out CLONE_DETECT_MS (250)
// with no incumbent. Tests that are not about a clone drop the channel so the
// handshake resolves false immediately. BA-27 installs its own incumbent.
beforeEach(() => {
  vi.stubGlobal("BroadcastChannel", undefined);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SESSION = {
  session: {
    session_id: "s-1",
    stage: "task",
    revision: 0,
    mode: "live",
    manifest_id: "tp_demo",
    study_key: "tp",
    timing_mode: "active",
    next_event_seq: 1,
    compatibility_key: "k",
    terminal_reason: null as string | null,
    writer_held: true,
    stage_entered_at: null,
  },
  clock: {
    server_now: new Date().toISOString(),
    active_ms: 60_000,
    budget_ms: 360_000,
    clock_running: true,
  },
};

const WORKBENCH = {
  stage: "task",
  revision: 0,
  server_now: new Date().toISOString(),
  world_ready: true,
  locations: [
    "The Sawmill",
    "The Marina",
    "Blackwell Storage",
    "Old Cannery",
    "Fairground Sheds",
    "Ridgeline Bunker",
  ],
  assigned_crates: [8, 2, 6, 4], // deliberately unsorted — UI must render ascending
  file: [
    {
      record_id: "r1",
      crate: 2,
      stamp: "Today, 9:00 AM",
      body: "Crate 2 spotted at The Sawmill.",
      event_type: "arrival",
      source: "initial",
    },
    {
      record_id: "r2",
      crate: 6,
      stamp: "Yesterday, 4:30 PM",
      body: "Crate 6 left The Marina.",
      event_type: "departure",
      source: "initial",
    },
  ],
  delivered: [],
  answers: {},
  frozen_answers: {},
  channels: [
    "file_work",
    "answer_drafting",
    "structured_seeking",
    "sharing",
    "social_reply",
    "assistant_prompting",
  ],
  instrument: null,
};

function okCommand(body: Record<string, unknown>): Response {
  return json({
    command_id: body.command_id,
    accepted: true,
    reason: null,
    committed_revision: 1,
    result: { writer_id: "w-1" },
    event_seqs: [],
    replayed: false,
  });
}

type RouteMap = Record<string, (body?: Record<string, unknown>) => Response>;

function fakeFetch(routes: RouteMap) {
  const calls: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
  const impl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const raw = typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
    const path = new URL(raw, "http://test.local").pathname.replace(API_BASE, "");
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined;
    calls.push({ url: raw, method, body });
    const key = `${method} ${path}`;
    const handler = routes[key];
    if (!handler) return json({ error: "not_found", detail: key }, 404);
    return handler(body);
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

/** The UI is locked until the boot resume confirms the lease (writer === "held") —
 * interactions must wait for their control to become enabled. */
async function waitEnabled(el: Element): Promise<void> {
  await waitFor(() => expect((el as HTMLButtonElement).disabled).toBe(false));
}

function workbenchRoutes(extra: RouteMap = {}): RouteMap {
  return {
    "GET /session": () => json(SESSION),
    "GET /events": () => json({ events: [], next_event_seq: 1 }),
    "GET /workbench": () => json(WORKBENCH),
    "POST /commands": (b) => okCommand(b ?? {}),
    "POST /telemetry": () => json({ ok: true }),
    ...extra,
  };
}

describe("api client", () => {
  it("targets the same-origin API beneath the ingress prefix", () => {
    expect(API_BASE).toBe("/investigators/api");
    expect(apiUrl("/health")).toBe("/investigators/api/health");
    expect(() => apiUrl("health")).toThrow();
  });

  it("parses a healthy response", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json({ status: "ok" }));
    await expect(getHealth(fetchImpl)).resolves.toEqual({ status: "ok" });
    expect(fetchImpl).toHaveBeenCalledWith("/investigators/api/health", expect.any(Object));
  });

  it.each([
    [400, "malformed"],
    [422, "malformed"],
    [401, "auth"],
    [403, "auth"],
    [409, "conflict"],
    [429, "quota"],
    [503, "unavailable"],
  ] as const)("maps status %i to %s", async (status, cls) => {
    const fetchImpl = vi.fn().mockResolvedValue(json({ error: "x" }, status));
    const err = await getHealth(fetchImpl).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).classification).toBe(cls);
  });
});

describe("entry refusal", () => {
  it.each([
    ["batch_at_capacity", "places are currently reserved"],
    ["platform_not_allowed", "participant ID supplied"],
    ["quota_configuration_changed", "temporarily unavailable"],
    ["test_only_batch", "reserved for internal testing"],
    ["test_identity_not_allowed", "cannot enter the paid study"],
  ])("explains %s without exposing an internal code", async (detail, expected) => {
    const { impl } = fakeFetch({
      "GET /session": () => json({ error: "unauthenticated" }, 401),
      "POST /entry": () => json({ error: "entry_refused", detail }, 403),
    });
    render(<App fetchImpl={impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "This study is not open" });
    expect(screen.getByTestId("refused-reason").textContent).toContain(expected);
    expect(screen.getByTestId("refused-reason").textContent).not.toContain(detail);
  });
  it("shows the not-open screen when entry is refused", async () => {
    const { impl } = fakeFetch({
      "GET /session": () => json({ error: "unauthenticated" }, 401),
      "POST /entry": () => json({ error: "batch_closed", detail: "closed" }, 403),
    });
    render(<App fetchImpl={impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "This study is not open" });
    expect(screen.getByTestId("refused-reason").textContent).toContain("batch_closed");
  });
});

describe("workbench", () => {
  let fetchMock: ReturnType<typeof fakeFetch>;

  beforeEach(() => {
    fetchMock = fakeFetch(workbenchRoutes());
  });

  it("renders case file, team chat, assistant and four ascending crate controls", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    expect(screen.getByRole("heading", { name: "Team chat" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "AI assistant" })).toBeTruthy();
    // Four pickers in ascending crate order regardless of server order.
    const pickers = screen.getAllByRole("combobox", { name: /Crate \d/ });
    const labels = pickers.map((p) => p.id);
    expect(labels).toEqual(["crate-2", "crate-4", "crate-6", "crate-8"]);
    // Records render with their content and share buttons.
    expect(screen.getByText("Crate 2 spotted at The Sawmill.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Share" }).length).toBe(2);
    // Countdown: 300s remaining → 5:00.
    expect(screen.getByRole("timer").textContent).toBe("5:00");
  });

  it("exposes separators that resize the three workbench panes", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    const grid = document.querySelector(".task-grid") as HTMLElement;
    expect(grid.style.getPropertyValue("--sx")).toBe("0.45");
    expect(grid.style.getPropertyValue("--sy")).toBe("0.55");

    // Keyboard path: the vertical separator moves the case-file/right-column split.
    const sepX = screen.getByRole("separator", {
      name: "Resize case file and chat panels",
    });
    expect(sepX.getAttribute("aria-valuenow")).toBe("45");
    fireEvent.keyDown(sepX, { key: "ArrowRight" });
    expect(grid.style.getPropertyValue("--sx")).toBe("0.5");
    expect(sepX.getAttribute("aria-valuenow")).toBe("50");

    // Bounds: the split clamps at 25% minimum / 75% maximum.
    for (let i = 0; i < 10; i += 1) fireEvent.keyDown(sepX, { key: "ArrowLeft" });
    expect(grid.style.getPropertyValue("--sx")).toBe("0.25");
    fireEvent.keyDown(sepX, { key: "ArrowLeft" });
    expect(grid.style.getPropertyValue("--sx")).toBe("0.25");
    fireEvent.keyDown(sepX, { key: "End" });
    expect(grid.style.getPropertyValue("--sx")).toBe("0.75");

    // The horizontal separator moves the chat/assistant split in the right column.
    const sepY = screen.getByRole("separator", {
      name: "Resize team chat and assistant panels",
    });
    fireEvent.keyDown(sepY, { key: "ArrowDown" });
    expect(grid.style.getPropertyValue("--sy")).toBe("0.6");
    fireEvent.keyDown(sepY, { key: "ArrowUp", shiftKey: true });
    expect(grid.style.getPropertyValue("--sy")).toBe("0.5");

    // Pointer-drag path: jsdom has no layout, so stub the grid rect (1000px wide).
    vi.spyOn(grid, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      width: 1030,
      height: 830,
      right: 1030,
      bottom: 830,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent.pointerDown(sepX, { pointerId: 1, clientX: 460 });
    fireEvent.pointerMove(sepX, { pointerId: 1, clientX: 610 });
    fireEvent.pointerUp(sepX, { pointerId: 1 });
    // (610 - padding 10) / (1030 - padding 20 - gap 10) = 0.6
    expect(grid.style.getPropertyValue("--sx")).toBe("0.6");

    // A drag past the edge still clamps.
    fireEvent.pointerDown(sepY, { pointerId: 2, clientY: 100 });
    fireEvent.pointerMove(sepY, { pointerId: 2, clientY: 10 });
    fireEvent.pointerUp(sepY, { pointerId: 2 });
    expect(grid.style.getPropertyValue("--sy")).toBe("0.25");

    // BA-77: a second pointer cannot move the split or end an in-flight drag.
    fireEvent.pointerDown(sepX, { pointerId: 3, clientX: 610 });
    fireEvent.pointerMove(sepX, { pointerId: 4, clientX: 950 }); // intruder — ignored
    expect(grid.style.getPropertyValue("--sx")).toBe("0.6");
    fireEvent.pointerUp(sepX, { pointerId: 4 }); // intruder up — drag survives
    fireEvent.pointerMove(sepX, { pointerId: 3, clientX: 260 }); // owner still drives
    expect(grid.style.getPropertyValue("--sx")).toBe("0.25");
    fireEvent.pointerUp(sepX, { pointerId: 3 });
    fireEvent.pointerMove(sepX, { pointerId: 3, clientX: 900 }); // ended — no effect
    expect(grid.style.getPropertyValue("--sx")).toBe("0.25");
  });

  it("AI_ASSISTANT_ENABLED=0: drops the assistant pane and its splitter — team chat fills the right column", async () => {
    const mock = fakeFetch({
      ...workbenchRoutes(),
      "GET /workbench": () =>
        json({
          ...WORKBENCH,
          ai_assistant: false,
          channels: WORKBENCH.channels.filter((c) => c !== "assistant_prompting"),
        }),
    });
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    expect(screen.getByRole("heading", { name: "Team chat" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "AI assistant" })).toBeNull();
    const grid = document.querySelector(".task-grid") as HTMLElement;
    expect(grid.classList.contains("no-assistant")).toBe(true);
    // The chat/assistant splitter is gone; the case-file | chat split remains.
    expect(
      screen.queryByRole("separator", { name: "Resize team chat and assistant panels" }),
    ).toBeNull();
    expect(
      screen.getByRole("separator", { name: "Resize case file and chat panels" }),
    ).toBeTruthy();
  });

  it("sends record_share when a record's Share button is activated (keyboard path)", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    const share = (await screen.findAllByRole("button", { name: "Share" }))[0]!;
    await waitEnabled(share);
    fireEvent.click(share as HTMLElement);
    await waitFor(() =>
      expect(
        fetchMock.calls.some(
          (c) => c.method === "POST" && c.url.endsWith("/commands") && c.body?.kind === "record_share",
        ),
      ).toBe(true),
    );
  });

  it("keeps an answer edit dirty until the command ack", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    const picker = await screen.findByRole("combobox", { name: "Crate 2" });
    await waitEnabled(picker);
    fireEvent.change(picker, { target: { value: "The Marina" } });
    fireEvent.blur(picker);
    await waitFor(() =>
      expect(
        fetchMock.calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.endsWith("/commands") &&
            c.body?.kind === "answer_update" &&
            (c.body.payload as Record<string, unknown>).crate === 2,
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.getByText("Saved")).toBeTruthy());
  });

  it("keyboard reorder: the focusable grip moves a card with arrow keys and announces it (BA-38)", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });

    const grips = screen.getAllByRole("button", { name: /^Reorder Crate/ });
    expect(grips.length).toBe(2);
    await waitEnabled(grips[1]!); // Crate 6 entry — second in the list

    const order = () =>
      Array.from(document.querySelectorAll(".files-list .file")).map(
        (el) => `Crate ${el.getAttribute("data-crate")}`,
      );
    expect(order()).toEqual(["Crate 2", "Crate 6"]);

    const announce = () => document.querySelector(".sr-only")?.textContent ?? "";
    grips[1]!.focus();
    fireEvent.keyDown(grips[1]!, { key: "ArrowUp" });
    await waitFor(() => expect(order()).toEqual(["Crate 6", "Crate 2"]));
    expect(announce()).toContain("position 1 of 2");

    // Boundary: moving past the first position is a no-op with a clear announcement.
    fireEvent.keyDown(grips[1]!, { key: "ArrowUp" });
    await waitFor(() => expect(announce()).toContain("already first"));
    expect(order()).toEqual(["Crate 6", "Crate 2"]);

    // And back down.
    fireEvent.keyDown(grips[1]!, { key: "ArrowDown" });
    await waitFor(() => expect(order()).toEqual(["Crate 2", "Crate 6"]));
    expect(announce()).toContain("position 2 of 2");
  });

  it("click reorder: clicking grips grabs then drops a card (BA-38)", async () => {
    render(<App fetchImpl={fetchMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });

    const order = () =>
      Array.from(document.querySelectorAll(".files-list .file")).map(
        (el) => `Crate ${el.getAttribute("data-crate")}`,
      );
    const announce = () => document.querySelector(".sr-only")?.textContent ?? "";
    expect(order()).toEqual(["Crate 2", "Crate 6"]);

    const gripSix = () =>
      screen.getByRole("button", { name: /Crate 6/ });
    await waitEnabled(gripSix());

    // Click the Crate 6 grip: it is "grabbed" (pressed) and announced.
    fireEvent.click(gripSix());
    await waitFor(() => expect(announce()).toContain("Grabbed"));
    expect(gripSix().getAttribute("aria-pressed")).toBe("true");
    expect(gripSix().className).toContain("grabbed");

    // Escape releases the grab without moving.
    fireEvent.keyDown(gripSix(), { key: "Escape" });
    await waitFor(() => expect(announce()).toContain("without moving"));
    expect(gripSix().getAttribute("aria-pressed")).toBe("false");
    expect(order()).toEqual(["Crate 2", "Crate 6"]);

    // Grab again, then click the Crate 2 grip: Crate 6 moves into its position.
    fireEvent.click(gripSix());
    await waitFor(() => expect(gripSix().getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(screen.getByRole("button", { name: /Crate 2/ }));
    await waitFor(() => expect(order()).toEqual(["Crate 6", "Crate 2"]));
    expect(announce()).toContain("position 1 of 2");
    expect(
      screen.getByRole("button", { name: /Crate 6/ }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("locks answers after task_deadline", async () => {
    const events = {
      events: [
        {
          seq: 1,
          event_id: "e1",
          stage: "task",
          actor: "system",
          type: "task_deadline",
          revision: 1,
          active_ms: 360_000,
          server_time: new Date().toISOString(),
          payload: {},
        },
      ],
      next_event_seq: 2,
    };
    const mock = fakeFetch(workbenchRoutes({ "GET /events": () => json(events) }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() => expect(screen.getByText("Answers locked")).toBeTruthy());
    const picker = screen.getByRole("combobox", { name: "Crate 2" }) as HTMLSelectElement;
    expect(picker.disabled).toBe(true);
  });

  it("shows the committed warning text and applies the low-time style", async () => {
    const events = {
      events: [
        {
          seq: 1,
          event_id: "e-warn",
          stage: "task",
          actor: "system",
          type: "message_delivered",
          revision: 1,
          active_ms: 300_000,
          server_time: new Date().toISOString(),
          payload: { message_id: "m1", channel: "system", body: "One minute left." },
        },
        {
          seq: 2,
          event_id: "e-warn2",
          stage: "task",
          actor: "system",
          type: "warning_committed",
          revision: 2,
          active_ms: 300_000,
          server_time: new Date().toISOString(),
          payload: { message_id: "m1", low_time: true },
        },
      ],
      next_event_seq: 3,
    };
    const clockLow = {
      ...SESSION,
      clock: { ...SESSION.clock, active_ms: 320_000 },
    };
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /session": () => json(clockLow),
        "GET /events": () => json(events),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() => expect(screen.getByText("One minute left.")).toBeTruthy());
    const timer = screen.getByRole("timer");
    expect(timer.className).toContain("low-time");
  });
});

// ----- BA-26/27/28/29: writer-lease boot hardening ------------------------------------------------

describe("writer-lease boot hardening", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
  });

  const commandCalls = (mock: ReturnType<typeof fakeFetch>, kind?: string) =>
    mock.calls.filter(
      (c) =>
        c.method === "POST" && c.url.endsWith("/commands") && (kind === undefined || c.body?.kind === kind),
    );

  it("BA-28: resume retries stale_revision with a resync and a NEW command id", async () => {
    let resumeCount = 0;
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /session": () =>
          json({ ...SESSION, session: { ...SESSION.session, revision: 7 } }),
        "POST /commands": (b) => {
          if (b?.kind === "resume") {
            resumeCount += 1;
            if (resumeCount === 1) return json({ error: "stale_revision" }, 409);
          }
          return okCommand(b ?? {});
        },
      }),
    );
    const controller = new SessionController({ fetchImpl: mock.impl, eventSourceFactory: null });
    await controller.start("");
    try {
      const resumes = commandCalls(mock, "resume");
      expect(resumes.length).toBe(2);
      expect(resumes[0]?.body?.command_id).not.toBe(resumes[1]?.body?.command_id);
      // The retry carries the resynchronized expected revision.
      expect(resumes[1]?.body?.expected_revision).toBe(7);
      // The resync re-fetched the session between attempts.
      expect(mock.calls.filter((c) => c.url.endsWith("/session")).length).toBeGreaterThanOrEqual(2);
      expect(controller.store.getSnapshot().writer).toBe("held");
      expect(window.localStorage.getItem("inv_writer_claim:s-1")).not.toBeNull();
    } finally {
      controller.dispose();
    }
  });

  it("BA-28: a failed boot resume leaves the tab non-writable and publishes no claim", async () => {
    const mock = fakeFetch(
      workbenchRoutes({
        "POST /commands": () => {
          throw new TypeError("network down");
        },
      }),
    );
    const controller = new SessionController({ fetchImpl: mock.impl, eventSourceFactory: null });
    await controller.start("");
    try {
      const state = controller.store.getSnapshot();
      // Unconfirmed — not held, and not mislabelled as a foreign-held read-only.
      expect(state.writer).toBe("unknown");
      expect(canWrite(state)).toBe(false);
      expect(state.leaseNotice).toBe(false);
      expect(window.localStorage.getItem("inv_writer_claim:s-1")).toBeNull();
      expect(window.sessionStorage.getItem("inv_writer_id")).toBeNull();
      expect(commandCalls(mock, "resume").length).toBe(1); // bounded — no inline storm
    } finally {
      controller.dispose();
    }
  });

  it("BA-27: a duplicate-tab clone mints a fresh tab id and goes read-only without resuming", async () => {
    // A live "incumbent" BroadcastChannel: answers any hello with an alive carrying a
    // different document nonce.
    class StubChannel {
      onmessage: ((ev: MessageEvent) => void) | null = null;
      private listeners = new Set<(ev: MessageEvent) => void>();
      constructor(public name: string) {}
      postMessage(data: unknown) {
        const msg = data as { type?: string; tabId?: string };
        if (msg?.type === "hello") {
          const alive = { type: "alive", tabId: msg.tabId, nonce: "incumbent-nonce" };
          setTimeout(() => {
            const ev = { data: alive } as MessageEvent;
            this.onmessage?.(ev);
            for (const fn of this.listeners) fn(ev);
          }, 0);
        }
      }
      addEventListener(_type: string, fn: (ev: MessageEvent) => void) {
        this.listeners.add(fn);
      }
      removeEventListener(_type: string, fn: (ev: MessageEvent) => void) {
        this.listeners.delete(fn);
      }
      close() {}
    }
    // Overrides the file no-incumbent stub. The production 250 ms timeout stays
    // armed; only an alive answer from this incumbent may mark the tab a clone.
    vi.stubGlobal("BroadcastChannel", StubChannel);
    // The clone inherits the incumbent's sessionStorage: same tab id + writer id.
    window.sessionStorage.setItem("inv_writer_tab", "incumbent-tab");
    window.sessionStorage.setItem("inv_writer_id", "w-old");
    let n = 0;
    const mock = fakeFetch(workbenchRoutes());
    const controller = new SessionController({
      fetchImpl: mock.impl,
      eventSourceFactory: null,
      uuid: () => `uuid-${++n}`,
    });
    await controller.start("");
    try {
      expect(window.sessionStorage.getItem("inv_writer_tab")).toBe("uuid-1");
      expect(window.sessionStorage.getItem("inv_writer_tab")).not.toBe("incumbent-tab");
      expect(window.sessionStorage.getItem("inv_writer_id")).toBeNull();
      const state = controller.store.getSnapshot();
      expect(state.writer).toBe("readonly");
      expect(state.leaseNotice).toBe(true);
      // A clone never reaches resume — no command traffic at all.
      expect(commandCalls(mock).length).toBe(0);
    } finally {
      controller.dispose();
      vi.unstubAllGlobals();
    }
  });

  it("BA-29: a transient getSession failure retries but never falls through to /entry", async () => {
    // Production backoff is BOOT_SESSION_DELAY_MS in controller.ts: two waits.
    const BACKOFF_MS = 300;
    let entryCalls = 0;
    let sessionCalls = 0;
    const mock = fakeFetch({
      "GET /session": () => {
        sessionCalls += 1;
        return json({ error: "boom" }, 500);
      },
      "POST /entry": () => {
        entryCalls += 1;
        return json({ session_id: "s-1", resumed: false, stage: "consent" });
      },
    });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    const waits: number[] = [];
    const fakeSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
      waits.push(typeof timeout === "number" ? timeout : 0);
      return fakeSetTimeout(handler, timeout, ...(args as []));
    }) as typeof setTimeout;
    const controller = new SessionController({ fetchImpl: mock.impl, eventSourceFactory: null });
    try {
      const pending = controller.start("?pid=X");
      await vi.advanceTimersByTimeAsync(BACKOFF_MS - 1);
      expect(sessionCalls).toBe(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(sessionCalls).toBe(2);
      await vi.advanceTimersByTimeAsync(BACKOFF_MS - 1);
      expect(sessionCalls).toBe(2);
      await vi.advanceTimersByTimeAsync(1);
      await pending;
      expect(entryCalls).toBe(0);
      expect(sessionCalls).toBe(3); // initial + two bounded retries
      expect(waits.filter((ms) => ms === BACKOFF_MS)).toEqual([BACKOFF_MS, BACKOFF_MS]);
      const state = controller.store.getSnapshot();
      expect(state.phase).toBe("booting"); // recoverable — boot re-runs on a timer
      expect(state.connection).toBe("disconnected");
    } finally {
      globalThis.setTimeout = fakeSetTimeout;
      controller.dispose();
      vi.useRealTimers();
    }
  });

  it("re-enters when the cookie's session is terminal and the URL carries an entry link", async () => {
    // A shared lab browser whose previous session hit heartbeat_timeout must not trap
    // every later preview link on the "Session ended" screen — the ?pt= descriptor is
    // the credential for its own session, so a terminal cookie session + entry params
    // boots through /entry.
    let entryCalls = 0;
    let entryQuery = "";
    const abandoned = JSON.parse(JSON.stringify(SESSION)) as typeof SESSION;
    abandoned.session.stage = "abandoned";
    abandoned.session.terminal_reason = "heartbeat_timeout";
    const mock = fakeFetch({
      "GET /session": () => json(abandoned),
      "GET /events": () => json({ events: [], next_event_seq: 1 }),
      "GET /workbench": () => json({ stage: "abandoned", revision: 0 }),
      "POST /entry": () => {
        entryCalls += 1;
        return json({ session_id: "s-2", resumed: false, stage: "consent" });
      },
    });
    const wrapped = async (url: string | URL | Request, init?: RequestInit) => {
      const raw = typeof url === "string" ? url : url instanceof URL ? url.href : url.url;
      if (raw.includes("/entry")) entryQuery = raw;
      return mock.impl(url, init);
    };
    const controller = new SessionController({ fetchImpl: wrapped, eventSourceFactory: null });
    await controller.start("?pt=signed-descriptor");
    try {
      expect(entryCalls).toBe(1);
      expect(entryQuery).toContain("pt=signed-descriptor");
    } finally {
      controller.dispose();
    }
  });

  it("BA-33: re-entry re-anchors the revision — the dead session's counter must not wedge resume", async () => {
    // Live repro of the stuck "Preparing your session…" banner: the cookie's terminal
    // session carried revision 5; the new pt session commits at revision 1. With a
    // monotonic Math.max anchor the resume kept posting expected_revision=5 against
    // a revision-1 session — stale_revision forever, writer stuck "unknown".
    const abandoned = JSON.parse(JSON.stringify(SESSION)) as typeof SESSION;
    abandoned.session.stage = "abandoned";
    abandoned.session.terminal_reason = "heartbeat_timeout";
    abandoned.session.revision = 5;
    const fresh = JSON.parse(JSON.stringify(SESSION)) as typeof SESSION;
    fresh.session.session_id = "s-2";
    fresh.session.stage = "consent";
    fresh.session.revision = 1;
    let entered = false;
    const revisions: number[] = [];
    const mock = fakeFetch({
      "GET /session": () => json(entered ? fresh : abandoned),
      "GET /events": () => json({ events: [], next_event_seq: 2, scanned_seq: 0 }),
      "GET /workbench": () => json({ stage: "consent", revision: 1 }),
      "POST /entry": () => {
        entered = true;
        return json({ session_id: "s-2", resumed: false, stage: "consent" });
      },
      "POST /commands": (b) => {
        revisions.push(Number(b?.expected_revision));
        return okCommand(b ?? {});
      },
      "POST /telemetry": () => json({ ok: true }),
    });
    const controller = new SessionController({ fetchImpl: mock.impl, eventSourceFactory: null });
    await controller.start("?pt=signed-descriptor");
    try {
      const state = controller.store.getSnapshot();
      expect(state.sessionId).toBe("s-2");
      expect(state.stage).toBe("consent");
      expect(state.writer).toBe("held");
      // The resume carried the NEW session's revision — never the dead session's 5.
      expect(revisions).toEqual([1]);
    } finally {
      controller.dispose();
    }
  });

  it("does NOT re-enter a live session just because the URL carries a link", async () => {
    // Reloading your own in-progress link must resume silently — /entry would
    // force-take the writer lease, so entry params alone never trigger re-entry
    // while the cookie's session is still running.
    const entryCalls = { count: 0 };
    const routes = workbenchRoutes({
      "POST /entry": () => {
        entryCalls.count += 1;
        return json({ session_id: "s-9", resumed: false, stage: "consent" });
      },
    });
    const mock = fakeFetch(routes);
    const controller = new SessionController({ fetchImpl: mock.impl, eventSourceFactory: null });
    await controller.start("?pt=signed-descriptor");
    try {
      expect(entryCalls.count).toBe(0);
      expect(controller.store.getSnapshot().stage).toBe("task");
    } finally {
      controller.dispose();
    }
  });
});

// ----- UX round 3 (UX_Feedback_v3.pdf) — workbench items ------------------------------------------

function participantEvent(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
  actor = "participant",
) {
  return {
    seq,
    event_id: `e-${seq}`,
    stage: "task",
    actor,
    type,
    revision: seq,
    active_ms: 60_000,
    server_time: new Date().toISOString(),
    payload,
  };
}

describe("UX round 3 — workbench", () => {
  it("Share goes straight from Sharing… to Shared in team chat, never back to Share (item 6)", async () => {
    let release: (() => void) | null = null;
    const mock = fakeFetch(
      workbenchRoutes({
        "POST /commands": ((b?: Record<string, unknown>) =>
          b?.kind === "record_share"
            ? new Promise<Response>((resolve) => {
                release = () => resolve(okCommand(b));
              })
            : okCommand(b ?? {})) as unknown as RouteMap[string],
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    const share = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(share);
    fireEvent.click(share);
    await screen.findByRole("button", { name: "Sharing…" });
    expect(release).not.toBeNull();
    release!();
    // No record_shared event and no workbench refresh carrying it: the committed
    // receipt alone flips the control — the old code fell back to "Share" here.
    await screen.findByText("Shared in team chat");
    expect(screen.getAllByRole("button", { name: "Share" }).length).toBe(1);
  });

  it("shows the visible crate badge alongside crate identity (item 7 reversed)", async () => {
    // 2026-10-10: crate badges are back on stamped intelligence (owner request
    // — makes the task a little easier); data-crate and the accessible name
    // keep carrying the identity too.
    render(<App fetchImpl={fakeFetch(workbenchRoutes()).impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    const cards = Array.from(document.querySelectorAll(".files-list .file"));
    expect(cards.map((c) => c.getAttribute("data-crate"))).toEqual(["2", "6"]);
    expect(cards[0]!.getAttribute("aria-label")).toContain("Crate 2");
    expect(cards[0]!.querySelector(".file-crate")?.textContent).toBe("Crate 2");
  });

  it("teammate-added intel is styled distinctly from the participant's own (item 8)", async () => {
    const wb = {
      ...WORKBENCH,
      file: [
        ...WORKBENCH.file,
        {
          record_id: "r9",
          crate: 4,
          stamp: "Today, 10:00 AM",
          body: "Crate 4 sighted near Old Cannery.",
          event_type: "arrival",
          source: "added_from_team",
          shared_by: "PM",
        },
      ],
    };
    render(
      <App
        fetchImpl={fakeFetch(workbenchRoutes({ "GET /workbench": () => json(wb) })).impl}
        deps={{ eventSourceFactory: null }}
      />,
    );
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() => expect(document.querySelectorAll(".files-list .file").length).toBe(3));
    const fromTeam = document.querySelectorAll(".files-list .file.from-team");
    expect(fromTeam.length).toBe(1);
    expect(fromTeam[0]!.getAttribute("data-crate")).toBe("4");
    // O-4: the tag names the teammate who shared the card.
    expect(fromTeam[0]!.textContent).toContain("Shared by PM");
  });

  it("does not flag crates whose file gained a teammate card (cue removed)", async () => {
    // 2026-10-10 revision added a neutral "Entry on Crate N added to your file"
    // cue beside the answer picker; owner direction 2026-10-11 removed it —
    // bottom-ribbon clutter and a possible perceived nudge to revise answers
    // from team intel would confound the source-reliance comparison. This test
    // pins the removal: an added_from_team record renders no note at all.
    const wb = {
      ...WORKBENCH,
      file: [
        ...WORKBENCH.file,
        {
          record_id: "r9",
          crate: 4,
          stamp: "Today, 10:00 AM",
          body: "Crate 4 sighted near Old Cannery.",
          event_type: "arrival",
          source: "added_from_team",
          shared_by: "PM",
        },
      ],
    };
    render(
      <App
        fetchImpl={fakeFetch(workbenchRoutes({ "GET /workbench": () => json(wb) })).impl}
        deps={{ eventSourceFactory: null }}
      />,
    );
    await screen.findByRole("heading", { name: "Your answers" });
    expect(document.querySelectorAll(".file-added-note").length).toBe(0);
    expect(screen.queryByText(/added to your file/)).toBeNull();
    expect(screen.queryByText(/New entry/)).toBeNull();
  });

  it("shows a generating cue until the assistant reply arrives (item 9)", async () => {
    const asked = participantEvent(1, "message_accepted", {
      message_id: "m-ask",
      channel: "assistant",
      body: "What does my file say about Crate 2?",
    });
    const reply = participantEvent(
      2,
      "message_delivered",
      { message_id: "m-reply", channel: "assistant", body: "The latest entry says the Sawmill." },
      "assistant",
    );
    let events = [asked];
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /events": () => json({ events, next_event_seq: events.length + 1 }),
      }),
    );
    const { unmount } = render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByTestId("assistant-thinking");
    unmount();
    cleanup();
    events = [asked, reply];
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByText("The latest entry says the Sawmill.");
    expect(screen.queryByTestId("assistant-thinking")).toBeNull();
  });

  it("the submission bar is a labelled, separate answer area (item 10)", async () => {
    render(<App fetchImpl={fakeFetch(workbenchRoutes()).impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    const bar = document.querySelector("footer.submit-bar")!;
    expect(bar.getAttribute("aria-label")).toBe("Your answers");
    expect(screen.getByRole("heading", { name: "Your answers" })).toBeTruthy();
    // Incentive-compatibility offer (consent v8 / payment v5): the bonus line
    // rides the answer bar where crates are selected — eligibility framing.
    expect(bar.textContent).toContain(
      "You are eligible for a bonus based on how many locations you correctly identify.",
    );
  });

  it("drops the answer-bar bonus line when bonus_payment is false (item 10)", async () => {
    // BONUS_PAYMENT_ENABLED=0: the gameplay bonus line leaves the rendered copy.
    const wb = { ...WORKBENCH, bonus_payment: false };
    render(
      <App
        fetchImpl={fakeFetch(workbenchRoutes({ "GET /workbench": () => json(wb) })).impl}
        deps={{ eventSourceFactory: null }}
      />,
    );
    await screen.findByRole("heading", { name: "Case file" });
    const bar = document.querySelector("footer.submit-bar")!;
    expect(bar.textContent).toContain("Record where each of your crates is currently located");
    expect(bar.textContent).not.toContain("bonus");
  });

  it("the time-pressure check-in offers no skip (item 5)", async () => {
    const open = participantEvent(
      1,
      "checkin_open",
      {
        copy: {
          question: "How heavy is your workload on this task right now?",
          scale_min_label: "Low",
          scale_max_label: "High",
          note: "",
          collecting: "Collecting…",
          reveal_title: "Ratings",
          reveal_note: "",
          resume_label: "Resume task",
        },
        assigned_values: { JT: 6, PM: 5 },
      },
      "system",
    );
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /session": () =>
          json({ ...SESSION, session: { ...SESSION.session, stage: "checkin_collect" } }),
        "GET /workbench": () => json({ ...WORKBENCH, stage: "checkin_collect" }),
        "GET /events": () => json({ events: [open], next_event_seq: 2 }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByTestId("checkin-overlay");
    expect(screen.queryByRole("button", { name: /skip/i })).toBeNull();
    expect(screen.getAllByRole("button", { name: /^[1-7]$/ }).length).toBe(7);
  });
});

describe("UX round 4 — chat intel cards and check-in copy", () => {
  it("shared intel renders on its own card for teammate and participant shares (items 9/11)", async () => {
    const wb = {
      ...WORKBENCH,
      // Teammate shares join the file automatically (O-4) — that is exactly why
      // the card carries the "in your case file" flag.
      file: [
        ...WORKBENCH.file,
        {
          record_id: "rT",
          crate: 5,
          stamp: "Today, 10:05 AM",
          body: "Crate 5 heard near The Marina.",
          event_type: "arrival",
          source: "added_from_team",
          shared_by: "JT",
        },
      ],
      delivered: [
        {
          record_id: "rT",
          crate: 5,
          stamp: "Today, 10:05 AM",
          body: "Crate 5 heard near The Marina.",
          event_type: "arrival",
          source: "added_from_team",
          shared_by: "JT",
        },
      ],
    };
    const events = [
      participantEvent(
        1,
        "message_delivered",
        {
          message_id: "m-t",
          channel: "team",
          body: "Sharing intel on Crate 5",
          crate: 5,
          record_id: "rT",
        },
        "JT",
      ),
      participantEvent(2, "record_shared", { record_id: "r1", crate: 2 }, "participant"),
    ];
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /workbench": () => json(wb),
        "GET /events": () => json({ events, next_event_seq: 3 }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    // The body renders twice: the file card and the chat intel card.
    await screen.findAllByText("Crate 5 heard near The Marina.");

    // The record is a sibling card, not a pill embedded inside the bubble.
    expect(document.querySelector(".msg .filepill")).toBeNull();
    const cards = document.querySelectorAll("li.intel-card");
    expect(cards.length).toBe(2);
    const teammate = cards[0]!;
    const mine = cards[1]!;
    expect(teammate.classList.contains("mine")).toBe(false);
    expect(teammate.textContent).toContain("in your case file");
    expect(mine.classList.contains("mine")).toBe(true);
    expect(mine.textContent).toContain("Crate 2 spotted at The Sawmill.");
    // Item 11: the participant's own share drops the "in your case file" flag.
    expect(mine.textContent).not.toContain("in your case file");
  });

  it("check-in strip reads 'Workload Reported at Check-in' with /7 anchors (item 12)", async () => {
    const events = [
      participantEvent(
        1,
        "checkin_open",
        {
          copy: {
            question: "How heavy is your workload on this task right now?",
            scale_min_label: "Low",
            scale_max_label: "High",
            note: "",
            collecting: "Collecting…",
            reveal_title: "Ratings",
            reveal_note: "",
            resume_label: "Resume task",
          },
          assigned_values: { JT: 6, PM: 7 },
        },
        "system",
      ),
      participantEvent(
        2,
        "checkin_reveal",
        { assigned_values: { JT: 6, PM: 7 }, own_rating: 5 },
        "system",
      ),
    ];
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /events": () => json({ events, next_event_seq: 3 }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await waitFor(() => {
      const strip = document.querySelector(".pressure-strip");
      expect(strip).not.toBeNull();
      expect(strip!.textContent).toBe(
        "Workload Reported at Check-in: JT: 6/7 · PM: 7/7 · You: 5/7",
      );
    });
  });
});

describe("FR-1 interaction mode (UX round 3)", () => {
  it("structured + natural language offers both per-crate selectors", async () => {
    render(<App fetchImpl={fakeFetch(workbenchRoutes()).impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    expect(screen.getByRole("combobox", { name: "Ask the team for intel on a crate" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Ask the assistant about a crate" })).toBeTruthy();
  });

  it("natural-language only removes the selectors and asks in typed words", async () => {
    const mock = fakeFetch(
      workbenchRoutes({
        "GET /workbench": () => json({ ...WORKBENCH, structured_requests: false }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() =>
      expect(screen.queryByRole("combobox", { name: "Ask the team for intel on a crate" })).toBeNull(),
    );
    expect(screen.queryByRole("combobox", { name: "Ask the assistant about a crate" })).toBeNull();
    const input = screen.getByRole("textbox", { name: "Type a question for the assistant" });
    await waitEnabled(input);
    fireEvent.change(input, { target: { value: "where was crate 2 last seen?" } });
    const askBtn = screen.getByRole("button", { name: "Ask" });
    await waitEnabled(askBtn);
    fireEvent.click(askBtn);
    await waitFor(() => {
      const call = mock.calls.find((c) => c.body?.kind === "assistant_request");
      expect(call).toBeTruthy();
      expect(call!.body!.payload).toEqual({ body: "where was crate 2 last seen?" });
    });
    expect(mock.calls.some((c) => c.body?.kind === "teammate_request")).toBe(false);
  });
});

describe("UX round 5 — teammate share delivery + re-share gating", () => {
  it("a teammate share renders its intel body immediately and lands in the case file (items 1–2)", async () => {
    const shared = {
      record_id: "rT",
      crate: 5,
      stamp: "Today, 10:05 AM",
      body: "Crate 5 heard near The Marina.",
      event_type: "arrival",
      source: "added_from_team",
      shared_by: "JT",
    };
    let wbCalls = 0;
    const mock = fakeFetch(
      workbenchRoutes({
        // The share commits before its events emit, so the post-event refetch
        // already sees the record — the boot fetch does not.
        "GET /workbench": () => {
          wbCalls += 1;
          return json(
            wbCalls > 1 ? { ...WORKBENCH, file: [...WORKBENCH.file, shared] } : WORKBENCH,
          );
        },
        "GET /events": () =>
          json({
            events: [
              participantEvent(
                1,
                "message_delivered",
                {
                  message_id: "m-t",
                  channel: "team",
                  body: "Sharing intel on Crate 5",
                  crate: 5,
                  record_id: "rT",
                },
                "JT",
              ),
              participantEvent(2, "record_added_to_file", shared, "JT"),
            ],
            next_event_seq: 3,
          }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    // The intel card renders the record body — never the bare "Attached record" fallback.
    await screen.findAllByText("Crate 5 heard near The Marina.");
    expect(screen.queryByText(/Attached record/)).toBeNull();
    // And the record is already in the case file with the from-team marker.
    const fromTeam = document.querySelectorAll(".files-list .file.from-team");
    expect(fromTeam.length).toBe(1);
    expect(fromTeam[0]!.textContent).toContain("Shared by JT");
  });

  it("a team-sourced file record shows the Shared-by marker but no Share button (item 3)", async () => {
    const wb = {
      ...WORKBENCH,
      file: [
        ...WORKBENCH.file,
        {
          record_id: "r9",
          crate: 4,
          stamp: "Today, 10:00 AM",
          body: "Crate 4 sighted near Old Cannery.",
          event_type: "arrival",
          source: "added_from_team",
          shared_by: "PM",
        },
      ],
    };
    const mock = fakeFetch(
      workbenchRoutes({ "GET /workbench": () => json(wb) }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() =>
      expect(document.querySelectorAll(".files-list .file").length).toBe(3),
    );
    // The two initial records still offer Share; the team-sourced card does not
    // (its drag-grip for reordering stays — that button is not a share action).
    expect(screen.getAllByRole("button", { name: "Share" }).length).toBe(2);
    const fromTeam = document.querySelector(".files-list .file.from-team")!;
    expect(
      [...fromTeam.querySelectorAll("button")].some(
        (b) => b.textContent === "Share",
      ),
    ).toBe(false);
    expect(fromTeam.textContent).toContain("Shared by PM");
    // Dragging it back into the chat is refused at the drop guard too — proven
    // against a live lease by the control drop right after: r1 shares normally
    // while r9 never produced a record_share call.
    await waitEnabled(screen.getAllByRole("button", { name: "Share" })[0]!);
    const pane = document.querySelector('[data-pane="team-chat"]')!;
    fireEvent.drop(pane, {
      dataTransfer: {
        types: ["text/record-id"],
        getData: (t: string) => (t === "text/record-id" ? "r9" : ""),
      },
    });
    fireEvent.drop(pane, {
      dataTransfer: {
        types: ["text/record-id"],
        getData: (t: string) => (t === "text/record-id" ? "r1" : ""),
      },
    });
    await waitFor(() => {
      const shares = mock.calls.filter(
        (c) => c.body?.kind === "record_share",
      );
      expect(shares.length).toBe(1);
      expect(shares[0]?.body?.payload).toEqual({ record_id: "r1" });
    });
  });
});
