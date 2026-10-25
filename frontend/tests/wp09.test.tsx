/**
 * WP09 participant-experience tests (INV-0901..0908): bound material provenance,
 * PE exposure surfaces (eval vs control), the isolated practice world, check-in
 * own-rating reveal and its persistent strip, untouched slider semantics, per-seat
 * peer ratings, debrief provenance, the recoverable completion code, and the
 * withdrawal / terminal surfaces.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { App } from "../src/App";
import { API_BASE } from "../src/api/client";
import type {
  ParticipantEvent,
  SessionResponse,
  WorkbenchResponse,
} from "../src/api/types";
import { SessionStore, type StoreApi } from "../src/state/store";

// jsdom's BroadcastChannel makes detectClone wait out CLONE_DETECT_MS (250)
// with no incumbent. This file does not cover clone, takeover, or incumbent.
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

function session(stage: string, extra: Record<string, unknown> = {}): SessionResponse {
  return {
    session: {
      session_id: "s-1",
      stage,
      revision: 0,
      mode: "live",
      manifest_id: "tp_demo",
      study_key: "investigators_time_pressure",
      timing_mode: "active",
      next_event_seq: 1,
      compatibility_key: "k",
      terminal_reason: null,
      completion_code: null,
      initials: null,
      writer_held: true,
      stage_entered_at: null,
      ...extra,
    },
    clock: {
      server_now: new Date().toISOString(),
      active_ms: 60_000,
      budget_ms: 360_000,
      clock_running: true,
    },
  } as SessionResponse;
}

function workbench(overrides: Record<string, unknown> = {}): WorkbenchResponse {
  return {
    stage: "task",
    revision: 0,
    server_now: new Date().toISOString(),
    world_ready: true,
    locations: ["The Sawmill", "The Marina"],
    assigned_crates: [2],
    file: [],
    delivered: [],
    shared: [],
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
    material: null,
    exposure: null,
    practice: null,
    ...overrides,
  } as WorkbenchResponse;
}

const MATERIAL = {
  slot: "instruments.consent",
  artifact_key: "consent.reference@v2",
  artifact_id: "consent.reference",
  artifact_version: 2,
  status: "proposed_unapproved",
  body: {
    material: "consent",
    reference_copy: "S5 approved protocol XXXX-XXXX Appendix B (consent), panel variant",
    freeze_decision: "D06",
    text: {
      heading: "Team Decision-Making Study",
      subheading: "[University] · Protocol XXXX-XXXX",
      sections: [
        {
          title: "Key Information",
          paragraphs: [
            "This is a research study conducted by researchers at [University].",
            "You may skip questions during the study and doing so will not affect your payment.",
          ],
        },
      ],
      question: "Would you like to participate?",
      accept_label: "Yes, I consent to participate",
      decline_label: "No, I do not wish to participate",
    },
  },
};

const PRACTICE = {
  records: [
    {
      record_id: "PRACTICE-K3-04",
      crate: 3,
      stamp: "Today, 9:00 AM",
      body: "Practice entry K3-04.",
    },
  ],
  shared: {
    record_id: "PRACTICE-SHARED-K1",
    crate: 1,
    stamp: "Today, 9:10 AM",
    body: "Teammate shared entry K1.",
  },
  locations: ["The Sawmill", "The Marina"],
  actions: ["share"],
  replies: {
    assistant_question: "What does my file say about Crate 3?",
    assistant_answer: "Practice summary.",
  },
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

function routesFor(stage: string, wb: Record<string, unknown>, extra: RouteMap = {}): RouteMap {
  return {
    "GET /session": () => json(session(stage)),
    "GET /events": () => json({ events: [], next_event_seq: 1 }),
    "GET /workbench": () => json(workbench({ stage, ...wb })),
    "POST /commands": (b) => okCommand(b ?? {}),
    "POST /telemetry": () => json({ ok: true }),
    ...extra,
  };
}

function commandCalls(
  calls: { method: string; body?: Record<string, unknown> | undefined }[],
  kind: string,
) {
  return calls.filter((c) => c.method === "POST" && c.body?.kind === kind);
}

/** Mutating controls stay disabled until the boot resume confirms the lease —
 * interactions must wait for their control to become enabled. */
async function waitEnabled(el: Element): Promise<void> {
  await waitFor(() => expect((el as HTMLButtonElement).disabled).toBe(false));
}

// ----- INV-0901: bound material + isolated practice -------------------------------------------

describe("bound material projection", () => {
  it("consent renders the bound artifact body verbatim and records its key (BA-37)", async () => {
    const mock = fakeFetch(routesFor("consent", { material: MATERIAL }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Team Decision-Making Study" });
    // Every rendered paragraph comes from the bound artifact body — nothing is a
    // component literal (BA-37 copy-to-key equivalence).
    for (const paragraph of MATERIAL.body.text.sections[0]!.paragraphs) {
      expect(screen.getByText(paragraph)).toBeTruthy();
    }
    // No artifact-key chrome on the participant page (UX round 2, item 1).
    expect(screen.queryByTestId("material-ref")).toBeNull();
    const agree = screen.getByRole("button", { name: "Yes, I consent to participate" });
    await waitEnabled(agree);
    fireEvent.click(agree);
    await waitFor(() => {
      const consentCalls = commandCalls(mock.calls, "consent");
      expect(consentCalls.length).toBe(1);
      expect((consentCalls[0]?.body?.payload as Record<string, unknown>).text_version).toBe(
        "consent.reference@v2",
      );
    });
  });

  it("a placeholder-bound consent shows provenance and no fabricated copy or consent buttons", async () => {
    const placeholder = {
      ...MATERIAL,
      artifact_key: "consent.reference@v1",
      artifact_version: 1,
      status: "unresolved_placeholder",
      body: {
        material: "consent",
        reference_copy: "demo consent screens",
        freeze_decision: "D06",
      },
    };
    const mock = fakeFetch(routesFor("consent", { material: placeholder }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Team Decision-Making Study" });
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("consent.reference@v1");
    expect(screen.queryByRole("button", { name: /consent|participate/i })).toBeNull();
    expect(screen.queryByTestId("material-ref")).toBeNull();
  });
});

describe("PE exposure surfaces (INV-0904)", () => {
  it("renders every served exposure surface and omits absent control copy", async () => {
    const evalMock = fakeFetch(
      routesFor("instructions", {
        exposure: {
          intro: "<p>Your teammates will also rate how helpful you were.</p>",
          callout: "<b>You will be rated by your teammates.</b>",
        },
      }),
    );
    const evalRender = render(<App fetchImpl={evalMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Team-based investigation game" });
    // Raw markup is never injected — only its text content surfaces.
    expect(screen.getByText("Your teammates will also rate how helpful you were.")).toBeTruthy();
    expect(document.querySelector('[data-surface="intro"]')).toBeTruthy();
    // The reminder callout is on page 2 — enter initials to page forward.
    const input = screen.getByLabelText(/identified to your team by your initials/);
    fireEvent.change(input, { target: { value: "ab" } });
    const cont = screen.getByRole("button", { name: "Continue to your workspace" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() =>
      expect(commandCalls(evalMock.calls, "set_initials").length).toBe(1),
    );
    await screen.findByRole("heading", { name: "Your workspace" });
    expect(screen.getByText("You will be rated by your teammates.")).toBeTruthy();
    expect(document.querySelector('[data-surface="callout"]')).toBeTruthy();
    evalRender.unmount();

    const controlMock = fakeFetch(
      routesFor("instructions", {
        exposure: { intro: "<p>You will answer a short questionnaire afterwards.</p>" },
      }),
    );
    render(<App fetchImpl={controlMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Team-based investigation game" });
    expect(screen.getByText("You will answer a short questionnaire afterwards.")).toBeTruthy();
    // The control arm serves no callout — the surface is absent, not blank.
    expect(document.querySelector('[data-surface="callout"]')).toBeNull();
  });

  it("names the ask picker and pre-announces solo review only where the manifest has them", async () => {
    const goToWorkspace = async (overrides: Record<string, unknown>) => {
      const mock = fakeFetch(routesFor("instructions", overrides));
      const view = render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
      await screen.findByRole("heading", { name: "Team-based investigation game" });
      fireEvent.change(screen.getByLabelText(/identified to your team by your initials/), {
        target: { value: "ab" },
      });
      const cont = screen.getByRole("button", { name: "Continue to your workspace" });
      await waitEnabled(cont);
      fireEvent.click(cont);
      await screen.findByRole("heading", { name: "Your workspace" });
      return view;
    };

    const structured = await goToWorkspace({
      structured_requests: true,
      solo_review_seconds: 60,
    });
    expect(screen.getByText(/Ask the team about/)).toBeTruthy();
    expect(screen.getByText(/short solo review of your case file/)).toBeTruthy();
    structured.unmount();

    await goToWorkspace({ structured_requests: false, solo_review_seconds: null });
    expect(screen.queryByText(/Ask the team about/)).toBeNull();
    expect(screen.getByText(/Use the team chat to ask teammates/)).toBeTruthy();
    expect(screen.queryByText(/short solo review/)).toBeNull();
  });

  it("renders the eval exposure strip in the task header and nothing for control", async () => {
    const evalMock = fakeFetch(
      routesFor("task", {
        exposure: { strip: "<span>Your teammates rate how helpful you are.</span>" },
      }),
    );
    const evalRender = render(<App fetchImpl={evalMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    await waitFor(() =>
      expect(screen.getByText("Your teammates rate how helpful you are.")).toBeTruthy(),
    );
    evalRender.unmount();

    const controlMock = fakeFetch(routesFor("task", { exposure: null }));
    render(<App fetchImpl={controlMock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    expect(document.querySelector('[data-surface="strip"]')).toBeNull();
  });
});

describe("practice world (INV-0901)", () => {
  it("pages through one share then the assistant preview, gating page 1 on the share", async () => {
    const mock = fakeFetch(routesFor("training", { material: null, practice: PRACTICE }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    // Page 1 (UX round 4): a single share try-it; the team chat is a preview.
    await screen.findByRole("heading", {
      name: "Practice: Sharing intel with your team",
    });
    expect(screen.getByText("Practice entry K3-04.")).toBeTruthy();
    // 2026-10-10: the visible crate badge is back on stamped intel (reverses
    // UX round 3, item 7), and no remaining-actions footer.
    expect(document.querySelector(".file .file-crate")?.textContent).toBe("Crate 3");
    expect(screen.queryByText(/left to try/)).toBeNull();
    // UX round 4 (items 3/4): no ask-the-team try-it and no teammate card in the
    // practice file — the chat pane shows a teammate share as a preview only.
    expect(screen.queryByRole("heading", { name: "Ask the team" })).toBeNull();
    expect(screen.queryByText("Shared by teammate")).toBeNull();
    const teamInput = screen.getByRole("textbox", { name: "Message the team" });
    expect((teamInput as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Teammate shared entry K1.")).toBeTruthy();
    const cont1 = screen.getByRole("button", { name: "Complete the practice steps to Continue" });
    expect((cont1 as HTMLButtonElement).disabled).toBe(true);

    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);
    // The share affordance is replaced by the shared tag (UX round 2, item 6 parity).
    await screen.findByText("Shared in team chat");
    // v14: the share alone no longer unlocks Continue — the answer-entry pick
    // (the DV mechanic) is also gated, credited on an accepted receipt.
    expect((cont1 as HTMLButtonElement).disabled).toBe(true);
    const pick = screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ });
    fireEvent.change(pick, { target: { value: "The Sawmill" } });
    await screen.findByText(/Location saved for Crate 3/);
    await waitEnabled(cont1);
    fireEvent.click(cont1);

    // Page 2 (UX round 4, item 7): a worked-example preview — nothing to type,
    // Continue is enabled without any practice interaction.
    await screen.findByRole("heading", {
      name: "Practice: Interacting with the AI assistant",
    });
    expect(screen.queryByText(/left to try/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "Record your answers" })).toBeNull();
    expect(screen.getByText("What does my file say about Crate 3?")).toBeTruthy();
    expect(screen.getByText("Practice summary.")).toBeTruthy();
    const aiInput = screen.getByRole("textbox", { name: "Ask the assistant" });
    expect((aiInput as HTMLInputElement).disabled).toBe(true);
    const cont2 = screen.getByRole("button", { name: "Continue to your team" });
    expect((cont2 as HTMLButtonElement).disabled).toBe(false);

    const steps = commandCalls(mock.calls, "training_step");
    const actions = steps.map(
      (c) => (c.body?.payload as Record<string, unknown>).action,
    );
    expect(actions).toEqual(["share", "pick_location"]);
    for (const c of steps) {
      expect((c.body?.payload as Record<string, unknown>).step).toBe("practice");
    }

    fireEvent.click(cont2);

    // Page 3 (2026-10-10): the comprehension check gates "Continue to your
    // team" — five true/false items, then the completing submission carries
    // the answers to the server-held key.
    await screen.findByRole("heading", { name: "Comprehension check" });
    const done3 = screen.getByRole("button", { name: "Continue to your team" });
    expect((done3 as HTMLButtonElement).disabled).toBe(true);
    for (const legend of [
      "Every team member receives some, but not all, available intelligence.",
      "Every team member is working on the exact same crates.",
      "The intelligence in my case file is accurate, but may not be the latest available.",
      "My teammates may have intelligence about my assigned crates.",
      "The AI assistant has access to my teammates’ case files.",
      "I record each of my crate locations before the timer expires.",
    ]) {
      const group = screen.getByText(legend).closest("fieldset")!;
      fireEvent.click(within(group as HTMLElement).getByRole("radio", { name: "True" }));
    }
    await waitEnabled(done3);
    fireEvent.click(done3);
    await waitFor(() => {
      const steps = commandCalls(mock.calls, "training_step");
      const completing = steps.find(
        (c) => (c.body?.payload as Record<string, unknown>).complete === true,
      );
      expect(completing).toBeTruthy();
      const p = completing!.body?.payload as Record<string, unknown>;
      expect(p.step).toBe("comprehension");
      expect((p.answers as Record<string, boolean>).partial_intel).toBe(true);
      expect((p.answers as Record<string, boolean>).assistant_reads_team).toBe(true);
    });
  });

  it("AI_ASSISTANT_ENABLED=0: skips the assistant practice page and drops the assistant gate item", async () => {
    const mock = fakeFetch(
      routesFor("training", {
        material: null,
        practice: { ...PRACTICE, ai_assistant: false, replies: {} },
        ai_assistant: false,
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", {
      name: "Practice: Sharing intel with your team",
    });
    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);
    await screen.findByText("Shared in team chat");
    // v14: the pick gates Continue under the flag-off path too.
    const pick = screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ });
    fireEvent.change(pick, { target: { value: "The Sawmill" } });
    await screen.findByText(/Location saved for Crate 3/);
    const cont1 = await screen.findByRole("button", { name: "Continue" });
    await waitEnabled(cont1);
    fireEvent.click(cont1);

    // The comprehension page follows the share page directly — no assistant
    // practice page, no assistant item among the four remaining gate items.
    await screen.findByRole("heading", { name: "Comprehension check" });
    expect(
      screen.queryByRole("heading", {
        name: "Practice: Interacting with the AI assistant",
      }),
    ).toBeNull();
    expect(
      screen.queryByText("The AI assistant has access to my teammates’ case files."),
    ).toBeNull();
    const done = screen.getByRole("button", { name: "Continue to your team" });
    expect((done as HTMLButtonElement).disabled).toBe(true);
    for (const legend of [
      "Every team member receives some, but not all, available intelligence.",
      "Every team member is working on the exact same crates.",
      "The intelligence in my case file is accurate, but may not be the latest available.",
      "My teammates may have intelligence about my assigned crates.",
      "I record each of my crate locations before the timer expires.",
    ]) {
      const group = screen.getByText(legend).closest("fieldset")!;
      fireEvent.click(within(group as HTMLElement).getByRole("radio", { name: "True" }));
    }
    await waitEnabled(done);
    fireEvent.click(done);
    await waitFor(() => {
      const completing = commandCalls(mock.calls, "training_step").find(
        (c) => (c.body?.payload as Record<string, unknown>).complete === true,
      );
      expect(completing).toBeTruthy();
      const p = completing!.body?.payload as Record<string, unknown>;
      expect(p.step).toBe("comprehension");
      expect(
        Object.keys(p.answers as Record<string, boolean>),
      ).not.toContain("assistant_reads_team");
    });
  });

  it("BA-95: credits the share only on an accepted receipt; failure holds Continue and a retry succeeds", async () => {
    let failShare = true;
    const routes = routesFor("training", { material: null, practice: PRACTICE });
    routes["POST /commands"] = (b) => {
      const p = (b?.payload ?? {}) as Record<string, unknown>;
      if (
        b?.kind === "training_step" &&
        p.step === "practice" &&
        p.action === "share" &&
        failShare
      ) {
        // A rejected command is a 409 problem response — the UI must not treat
        // it as done.
        return json({ error: "rejected", detail: "command rejected" }, 409);
      }
      return okCommand(b ?? {});
    };
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", {
      name: "Practice: Sharing intel with your team",
    });

    const cont1 = screen.getByRole("button", { name: "Complete the practice steps to Continue" });
    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);

    // Rejected receipt: error surfaced, no shared card/tag, Continue stays gated.
    await screen.findByRole("alert");
    expect(screen.queryByText("Shared in team chat")).toBeNull();
    expect(screen.queryByText("Sharing intel on Crate 3.")).toBeNull();
    expect((cont1 as HTMLButtonElement).disabled).toBe(true);

    // Retry after the rejection clears records the share; Continue still gates
    // on the v14 answer-entry pick.
    failShare = false;
    const retry = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(retry);
    fireEvent.click(retry);
    await screen.findByText("Shared in team chat");
    await screen.findByText("Sharing intel on Crate 3.");
    expect((cont1 as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ }), { target: { value: "The Marina" } });
    await screen.findByText(/Location saved for Crate 3/);
    await waitEnabled(cont1);
  });

  it("comprehension gate highlights server-reported misses and retries", async () => {
    // The server owns the answer key: a wrong submission stays in training and
    // returns `missed` ids, which the page highlights until the next attempt.
    let failComp = true;
    const routes = routesFor("training", { material: null, practice: PRACTICE });
    routes["POST /commands"] = (b) => {
      const p = (b?.payload ?? {}) as Record<string, unknown>;
      if (b?.kind === "training_step" && p.step === "comprehension" && failComp) {
        return json({
          command_id: b?.command_id,
          accepted: true,
          reason: null,
          committed_revision: 1,
          result: { stage: "training", missed: ["same_crates"] },
          event_seqs: [],
          replayed: false,
        });
      }
      return okCommand(b ?? {});
    };
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", {
      name: "Practice: Sharing intel with your team",
    });
    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);
    fireEvent.change(screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ }), { target: { value: "The Marina" } });
    await screen.findByText(/Location saved for Crate 3/);
    const cont1 = await screen.findByRole("button", { name: "Continue" });
    await waitEnabled(cont1);
    fireEvent.click(cont1);
    await screen.findByRole("heading", {
      name: "Practice: Interacting with the AI assistant",
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue to your team" }));
    await screen.findByRole("heading", { name: "Comprehension check" });

    const done = screen.getByRole("button", { name: "Continue to your team" });
    const field = (legend: string) =>
      screen.getByText(legend).closest("fieldset") as HTMLElement;
    for (const q of [
      "Every team member receives some, but not all, available intelligence.",
      "Every team member is working on the exact same crates.",
      "The intelligence in my case file is accurate, but may not be the latest available.",
      "My teammates may have intelligence about my assigned crates.",
      "The AI assistant has access to my teammates’ case files.",
      "I record each of my crate locations before the timer expires.",
    ]) {
      fireEvent.click(within(field(q)).getByRole("radio", { name: "True" }));
    }
    await waitEnabled(done);
    fireEvent.click(done);

    // The missed item is highlighted with the retry note; the stage holds.
    await screen.findByText(/Some answers are incorrect/);
    expect(screen.queryByText(/couldn't submit|did not go through/i)).toBeNull();
    const missedLi = field("Every team member is working on the exact same crates.")
      .closest("li")!;
    expect(missedLi.classList.contains("missed")).toBe(true);
    expect(
      field("Every team member receives some, but not all, available intelligence.")
        .closest("li")!.classList.contains("missed"),
    ).toBe(false);

    // Revising the missed answer clears its highlight; a clean retry submits.
    failComp = false;
    fireEvent.click(within(field("Every team member is working on the exact same crates."))
      .getByRole("radio", { name: "False" }));
    expect(missedLi.classList.contains("missed")).toBe(false);
    fireEvent.click(done);
    await waitFor(() => {
      const steps = commandCalls(mock.calls, "training_step").filter(
        (c) => (c.body?.payload as Record<string, unknown>).step === "comprehension",
      );
      expect(steps.length).toBe(2);
      const last = steps[1]!.body?.payload as Record<string, unknown>;
      expect((last.answers as Record<string, boolean>).same_crates).toBe(false);
    });
    expect(screen.queryByText(/Some answers are incorrect/)).toBeNull();
  });
});

// ----- INV-0902: check-in reveal + persistent strip ---------------------------------------------

const CHECKIN_COPY = {
  question: "How much time pressure?",
  scale_min_label: "1",
  scale_max_label: "7",
  note: "n",
  collecting: "Collecting…",
  reveal_title: "Ratings reported",
  reveal_note: "r",
  resume_label: "Resume task",
};

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

let uuidCounter = 1000;
function makeStore(overrides: Partial<StoreApi> = {}): { store: SessionStore; api: StoreApi & { postCommand: Mock } } {
  const api = {
    getSession: vi.fn(async () => session("task")),
    getEvents: vi.fn(async (afterSeq: number) => ({ events: [], next_event_seq: afterSeq + 1 })),
    getWorkbench: vi.fn(async () => workbench()),
    postCommand: vi.fn(async () => ({
      command_id: "c",
      accepted: true,
      reason: null,
      committed_revision: 5,
      result: {},
      event_seqs: [],
      replayed: false,
    })),
    postComplete: vi.fn(async () => ({
      command_id: "c",
      accepted: true,
      reason: null,
      committed_revision: 6,
      result: {},
      event_seqs: [],
      replayed: false,
    })),
    ...overrides,
  };
  const store = new SessionStore({ api: api as unknown as StoreApi, uuid: () => `id-${uuidCounter++}`, now: () => 10_000 });
  return { store, api: api as StoreApi & { postCommand: Mock } };
}

describe("check-in own rating (INV-0902)", () => {
  it("stores the committed own rating from the reveal and keeps the strip after resume", async () => {
    const { store, api } = makeStore();
    store.setWriter("held"); // mutating commands are gated on a confirmed lease (BA-26)
    store.applyEvents([
      event(1, "checkin_open", {
        assigned_values: { JT: 6, PM: 7 },
        copy: CHECKIN_COPY,
      }),
    ]);
    expect(store.getSnapshot().checkin.phase).toBe("collect");

    await store.submitCheckin(5);
    expect(api.postCommand).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "pressure_response", payload: { value: 5 } }),
    );
    expect(store.getSnapshot().checkin.ownValue).toBe(5);

    store.applyEvents([event(2, "checkin_response")]);
    expect(store.getSnapshot().checkin.phase).toBe("wait");

    // The reveal payload carries the server's committed rating — authoritative on resync.
    store.applyEvents([
      event(3, "checkin_reveal", { assigned_values: { JT: 6, PM: 7 }, own_rating: 5 }),
    ]);
    expect(store.getSnapshot().checkin.phase).toBe("reveal");
    expect(store.getSnapshot().checkin.revealedValues).toEqual({ JT: 6, PM: 7 });
    expect(store.getSnapshot().checkin.ownValue).toBe(5);

    // Resume: overlay closes, the persistent strip data (incl. own rating) survives.
    // The session snapshot carries a post-commit revision — a lower one would be
    // a stale response and must be dropped (BA-47).
    store.applySessionResponse(session("task", { revision: 6 }));
    const s = store.getSnapshot();
    expect(s.checkin.phase).toBe("closed");
    expect(s.checkin.revealedValues).toEqual({ JT: 6, PM: 7 });
    expect(s.checkin.ownValue).toBe(5);
  });

  it("stray keystrokes while typing never submit — mouse click required (RL 2026-10-05)", async () => {
    // Observed: the check-in opened mid-"Crate 1"; focus landed on rating
    // button "1" and the spacebar submitted it. Focus must rest on the dialog
    // itself, and Enter/Space must never activate a control.
    const mock = fakeFetch(
      routesFor("checkin_collect", {}, {
        "GET /events": () =>
          json({
            events: [
              event(1, "checkin_open", {
                assigned_values: { JT: 6, PM: 7 },
                copy: CHECKIN_COPY,
              }),
            ],
            next_event_seq: 2,
          }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    const overlay = await screen.findByTestId("checkin-overlay");
    const dialog = overlay.querySelector('[role="dialog"]') as HTMLElement;
    await waitFor(() => expect(document.activeElement).toBe(dialog));
    // Keys arriving while the participant thinks they're still typing are dead:
    // fireEvent.keyDown returns false when defaultPrevented — the dialog must
    // cancel Enter/Space so a focused control can never arm.
    expect(fireEvent.keyDown(dialog, { key: " " })).toBe(false);
    expect(fireEvent.keyDown(dialog, { key: "Enter" })).toBe(false);
    fireEvent.keyDown(dialog, { key: "1" });
    expect(commandCalls(mock.calls, "pressure_response").length).toBe(0);
    // Even with a button focused (e.g. via Tab), Enter/Space must not activate.
    const one = screen.getByRole("button", { name: "1" });
    await waitEnabled(one); // mutating controls unlock on the confirmed lease
    (one as HTMLElement).focus();
    expect(fireEvent.keyDown(one, { key: " " })).toBe(false);
    fireEvent.keyUp(one, { key: " " });
    expect(fireEvent.keyDown(one, { key: "Enter" })).toBe(false);
    expect(commandCalls(mock.calls, "pressure_response").length).toBe(0);
    // A real click still submits.
    fireEvent.click(one);
    await waitFor(() =>
      expect(commandCalls(mock.calls, "pressure_response").length).toBe(1),
    );
  });

  it("renders the reveal chips including the participant's own rating, then the persistent strip", async () => {
    const events = {
      events: [
        { ...event(1, "checkin_open", { assigned_values: { JT: 6, PM: 7 }, copy: CHECKIN_COPY }), stage: "task" },
        { ...event(2, "checkin_reveal", { assigned_values: { JT: 6, PM: 7 }, own_rating: 4 }), stage: "checkin_reveal" },
      ],
      next_event_seq: 3,
    };
    const mock = fakeFetch(
      routesFor("checkin_reveal", {}, { "GET /events": () => json(events) }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByTestId("checkin-overlay");
    expect(screen.getByText("JT:").parentElement?.textContent).toContain("6");
    expect(screen.getByText("You:").parentElement?.textContent).toContain("4");
  });
});

// ----- INV-0905: untouched slider semantics -----------------------------------------------------

const SLIDER_INSTRUMENT = {
  stage: "confidence",
  instrument_id: "post_task.confidence",
  instrument_version: 1,
  applies_to: "per_answered_crate",
  response: { type: "slider", min: 0, max: 100, min_label: "Not at all", max_label: "Completely" },
  items: [{ item_id: "confidence", text: "How confident are you?", skippable: true }],
  seats: [],
  answered_items: [],
  answered_crates: [],
  pending_crates: [2],
};

describe("slider untouched semantics (INV-0905)", () => {
  it("an unmoved slider records an explicit skip on Continue — never a fabricated value", async () => {
    const mock = fakeFetch(routesFor("confidence", { instrument: SLIDER_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Confidence ratings" });
    // UX round 2, item 9: no per-item skip control — Continue is always available
    // and a blank skippable item records a skip instead.
    expect(screen.queryByRole("button", { name: "Prefer not to answer" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const skips = commandCalls(mock.calls, "item_skip");
      expect(skips.length).toBe(1);
      expect(skips[0]?.body?.payload).toMatchObject({
        item_id: "confidence",
        crate: 2,
        missing_reason: "left_blank",
      });
      expect(commandCalls(mock.calls, "item_response").length).toBe(0);
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("a moved slider submits the rating and proceeds", async () => {
    const mock = fakeFetch(routesFor("confidence", { instrument: SLIDER_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Confidence ratings" });
    const slider = screen.getByRole("slider", { name: "Confidence for Crate 2" });
    await waitEnabled(slider);
    fireEvent.change(slider, {
      target: { value: "72" },
    });
    await waitFor(() => expect(screen.getByText("Confidence: 72%")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      const calls = commandCalls(mock.calls, "item_response");
      expect(calls.length).toBe(1);
      const payload = calls[0]?.body?.payload as Record<string, unknown>;
      expect(payload).toMatchObject({ item_id: "confidence", value: 72, crate: 2 });
      expect(commandCalls(mock.calls, "item_skip").length).toBe(0);
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("does not send a second proceed after the last response advances the stage", async () => {
    const routes = routesFor("confidence", { instrument: SLIDER_INSTRUMENT });
    routes["POST /commands"] = (body) => {
      if (body?.kind !== "item_response") return okCommand(body ?? {});
      const advanced = body?.kind === "item_response";
      if (advanced) {
        routes["GET /session"] = () => json(session("post_questionnaire", { revision: 1 }));
        routes["GET /workbench"] = () => json(workbench({
          stage: "post_questionnaire", revision: 1, instrument: POST_INSTRUMENT,
        }));
      }
      return json({
        command_id: body?.command_id, accepted: true, reason: null,
        committed_revision: 1, event_seqs: [], replayed: false,
        result: { stage: advanced ? "post_questionnaire" : "confidence" },
      });
    };
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    const slider = await screen.findByRole("slider", { name: "Confidence for Crate 2" });
    await waitEnabled(slider);
    fireEvent.change(slider, { target: { value: "72" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "A few questions" });
    expect(commandCalls(mock.calls, "item_response")).toHaveLength(1);
    expect(commandCalls(mock.calls, "proceed")).toHaveLength(0);
  });

  it("shows an explicit empty state when no answers were recorded (UX round 2)", async () => {
    const noCrates = { ...SLIDER_INSTRUMENT, pending_crates: [] };
    const mock = fakeFetch(routesFor("confidence", { instrument: noCrates }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Confidence ratings" });
    expect(
      screen.getByText(/did not enter a location for any crate, so there is nothing to rate/),
    ).toBeTruthy();
    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => expect(commandCalls(mock.calls, "proceed").length).toBe(1));
  });
});

// ----- INV-0905: per-seat peer ratings ------------------------------------------------------------

const PEER_INSTRUMENT = {
  stage: "peer_ratings",
  instrument_id: "post_task.peer_ratings",
  instrument_version: 1,
  applies_to: "per_teammate_seat",
  response: { type: "likert", min: 1, max: 7, min_label: "Not at all", max_label: "Very" },
  items: [{ item_id: "peer_helpfulness", text: "How helpful was {seat} during the task?", skippable: true }],
  seats: ["JT", "PM"],
  answered_items: [],
  answered_crates: [],
  pending_crates: [],
};

describe("per-seat peer ratings (INV-0905)", () => {
  it("renders a section per seat with {seat} substituted and submits the seat identity", async () => {
    const mock = fakeFetch(routesFor("peer_ratings", { instrument: PEER_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Your teammates" });
    expect(screen.getByRole("heading", { name: "About JT" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "About PM" })).toBeTruthy();
    // The item's {seat} template is substituted — never shown raw (UX round 2).
    expect(screen.getByText("How helpful was JT during the task?")).toBeTruthy();
    expect(screen.queryByText(/\{seat\}/)).toBeNull();

    const jtSection = screen.getByRole("heading", { name: "About JT" }).closest("section")!;
    const jtGroup = within(jtSection).getByRole("radiogroup", {
      name: /How helpful was JT/,
    });
    const jtButtons = jtGroup.querySelectorAll("button");
    await waitEnabled(jtButtons[0]!);
    fireEvent.click(jtButtons[5]!); // value 6
    const cont = screen.getByRole("button", { name: "Continue" });
    const pmSection = screen.getByRole("heading", { name: "About PM" }).closest("section")!;
    const pmGroup = within(pmSection).getByRole("radiogroup", {
      name: /How helpful was PM/,
    });
    fireEvent.click(pmGroup.querySelectorAll("button")[3]!); // value 4
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const calls = commandCalls(mock.calls, "item_response");
      expect(calls.length).toBe(2);
      expect(calls[0]?.body?.payload).toMatchObject({
        item_id: "peer_helpfulness",
        value: 6,
        seat: "JT",
      });
      expect(calls[1]?.body?.payload).toMatchObject({
        item_id: "peer_helpfulness",
        value: 4,
        seat: "PM",
      });
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("initials that collide with a seat label render the seat as AR (UX round 2)", async () => {
    const routes = routesFor("peer_ratings", { instrument: PEER_INSTRUMENT });
    routes["GET /session"] = () => json(session("peer_ratings", { initials: "JT" }));
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Your teammates" });
    // The participant took the JT initials — the JT seat displays as AR.
    expect(screen.getByRole("heading", { name: "About AR" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "About JT" })).toBeNull();
    expect(screen.getByText("How helpful was AR during the task?")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "About PM" })).toBeTruthy();
  });

  it("an unrated seat records a skip on Continue — mixed answer+skip submits together", async () => {
    const mock = fakeFetch(routesFor("peer_ratings", { instrument: PEER_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Your teammates" });
    // No per-item skip control (UX round 2, item 9).
    expect(screen.queryByRole("button", { name: "Prefer not to answer" })).toBeNull();

    const jtSection = screen.getByRole("heading", { name: "About JT" }).closest("section")!;
    const jtGroup = within(jtSection).getByRole("radiogroup", {
      name: /How helpful was JT/,
    });
    await waitEnabled(jtGroup.querySelectorAll("button")[0]!);
    fireEvent.click(jtGroup.querySelectorAll("button")[2]!); // JT value 3; PM left blank

    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const skips = commandCalls(mock.calls, "item_skip");
      expect(skips.length).toBe(1);
      expect(skips[0]?.body?.payload).toMatchObject({
        item_id: "peer_helpfulness",
        seat: "PM",
        missing_reason: "left_blank",
      });
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(1);
      expect(answers[0]?.body?.payload).toMatchObject({
        item_id: "peer_helpfulness",
        value: 3,
        seat: "JT",
      });
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });
});

// ----- INV-0906: post-task items — skip + persisted-skip recovery (BA-36) --------------------------

const POST_INSTRUMENT = {
  stage: "post_questionnaire",
  instrument_id: "post_task.questionnaire",
  instrument_version: 1,
  applies_to: "session_items",
  response: { type: "likert", min: 1, max: 5, min_label: "Not at all", max_label: "Very" },
  items: [
    { item_id: "post_1", text: "I felt rushed.", skippable: true },
    { item_id: "post_2", text: "The task was clear.", skippable: true },
  ],
  seats: [],
  answered_items: [],
  answered_crates: [],
  pending_crates: [],
};

describe("post-task questionnaire skips (BA-36)", () => {
  it("a persisted skip is not re-presented after reload", async () => {
    // Reload snapshot: post_1 already persisted (as a skip), post_2 still owed.
    const reloaded = { ...POST_INSTRUMENT, answered_items: ["post_1"] };
    const mock = fakeFetch(routesFor("post_questionnaire", { instrument: reloaded }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "A few questions" });
    // The resolved item is not re-rendered; only post_2 is pending.
    expect(screen.queryByText("I felt rushed.")).toBeNull();
    expect(screen.getByText("The task was clear.")).toBeTruthy();

    const group = screen.getByRole("radiogroup", { name: "The task was clear." });
    await waitEnabled(group.querySelectorAll("button")[0]!);
    fireEvent.click(group.querySelectorAll("button")[1]!); // value 2
    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(1);
      expect(answers[0]?.body?.payload).toMatchObject({ item_id: "post_2", value: 2 });
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("blank item + answered item — Continue records a skip and the answer together", async () => {
    const mock = fakeFetch(routesFor("post_questionnaire", { instrument: POST_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "A few questions" });
    // UX round 2, item 9: no per-item skip control — post_1 is simply left blank.
    expect(screen.queryByRole("button", { name: "Prefer not to answer" })).toBeNull();
    const group = screen.getByRole("radiogroup", { name: "The task was clear." });
    const first = group.querySelectorAll("button")[0]!;
    await waitEnabled(first);
    fireEvent.click(group.querySelectorAll("button")[4]!); // post_2 value 5

    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const skipCalls = commandCalls(mock.calls, "item_skip");
      expect(skipCalls.length).toBe(1);
      expect(skipCalls[0]?.body?.payload).toMatchObject({
        item_id: "post_1",
        missing_reason: "left_blank",
      });
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(1);
      expect(answers[0]?.body?.payload).toMatchObject({ item_id: "post_2", value: 5 });
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });
});

describe("required items (2026-10-08 launch contract)", () => {
  const REQUIRED = {
    ...POST_INSTRUMENT,
    items: [
      { item_id: "post_1", text: "I felt rushed.", skippable: false },
      { item_id: "post_2", text: "The task was clear.", skippable: false },
    ],
  };

  it("Continue is blocked until every required item is answered — never a skip", async () => {
    const mock = fakeFetch(routesFor("post_questionnaire", { instrument: REQUIRED }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "A few questions" });

    const cont = screen.getByRole("button", { name: "Continue" });
    expect((cont as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Please answer every item to continue.")).toBeTruthy();

    // Answering one of two is not enough — still blocked, nothing submitted.
    const group2 = screen.getByRole("radiogroup", { name: "The task was clear." });
    await waitEnabled(group2.querySelectorAll("button")[0]!);
    fireEvent.click(group2.querySelectorAll("button")[4]!);
    await waitFor(() => expect((cont as HTMLButtonElement).disabled).toBe(true));
    expect(commandCalls(mock.calls, "item_skip").length).toBe(0);
    expect(commandCalls(mock.calls, "item_response").length).toBe(0);

    const group1 = screen.getByRole("radiogroup", { name: "I felt rushed." });
    fireEvent.click(group1.querySelectorAll("button")[2]!);
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(2);
      expect(commandCalls(mock.calls, "item_skip").length).toBe(0);
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("a required slider has no value until moved — Continue blocked, never a fabricated 50", async () => {
    const requiredSlider = {
      ...SLIDER_INSTRUMENT,
      items: [{ ...SLIDER_INSTRUMENT.items[0], skippable: false }],
    };
    const mock = fakeFetch(routesFor("confidence", { instrument: requiredSlider }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Confidence ratings" });

    // Unanswered state is explicit: — readout, prior-study hint, Continue disabled.
    expect(screen.getByText("Confidence: —")).toBeTruthy();
    expect(screen.getByText(/has no value until you do/)).toBeTruthy();
    const cont = screen.getByRole("button", { name: "Continue" });
    expect((cont as HTMLButtonElement).disabled).toBe(true);
    expect(commandCalls(mock.calls, "item_response").length).toBe(0);
    expect(commandCalls(mock.calls, "item_skip").length).toBe(0);

    // Moving the slider is the only way to answer — the value is what was set.
    const slider = screen.getByRole("slider", { name: "Confidence for Crate 2" });
    fireEvent.change(slider, { target: { value: "30" } });
    await waitFor(() => expect(screen.getByText("Confidence: 30%")).toBeTruthy());
    expect(screen.queryByText(/has no value until you do/)).toBeNull();
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const calls = commandCalls(mock.calls, "item_response");
      expect(calls.length).toBe(1);
      expect(calls[0]?.body?.payload).toMatchObject({
        item_id: "confidence",
        value: 30,
        crate: 2,
      });
      expect(commandCalls(mock.calls, "item_skip").length).toBe(0);
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });
});

// ----- UX round 5, item 4: choice items get their own trailing page --------------------------------

const SPLIT_INSTRUMENT = {
  ...POST_INSTRUMENT,
  items: [
    { item_id: "post_1", text: "I felt rushed.", skippable: true },
    {
      item_id: "post_10",
      text: "Looking back, what do you think your teammates were?",
      options: [
        "Other participants, like me",
        "I wasn't sure",
        "Computer programs",
      ],
      skippable: true,
    },
  ],
};

describe("post-task pagination — options items on their own page (UX round 5, item 4)", () => {
  it("the likert page and the choice page submit separately; proceed fires once, on the last page", async () => {
    const mock = fakeFetch(routesFor("post_questionnaire", { instrument: SPLIT_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "A few questions" });
    // Page 1: only the likert item — the choice item is not rendered yet.
    expect(screen.getByText("I felt rushed.")).toBeTruthy();
    expect(
      screen.queryByText("Looking back, what do you think your teammates were?"),
    ).toBeNull();

    const group = screen.getByRole("radiogroup", { name: "I felt rushed." });
    await waitEnabled(group.querySelectorAll("button")[0]!);
    fireEvent.click(group.querySelectorAll("button")[3]!); // value 4
    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);

    // Page 2: the choice item alone — options as buttons, not a likert scale.
    await screen.findByText("Looking back, what do you think your teammates were?");
    expect(screen.queryByText("I felt rushed.")).toBeNull();
    // The intermediate page submitted its answer but did NOT proceed.
    expect(commandCalls(mock.calls, "proceed").length).toBe(0);
    await waitFor(() => {
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(1);
      expect(answers[0]?.body?.payload).toMatchObject({ item_id: "post_1", value: 4 });
    });
    // Choice options render as labelled buttons — not likert 1..5.
    expect(document.querySelector(".likert-row")).toBeNull();
    expect(document.querySelector(".choice-list")).not.toBeNull();
    const opt = screen.getByRole("button", { name: "Computer programs" });
    await waitEnabled(opt);
    fireEvent.click(opt);
    const cont2 = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont2);
    fireEvent.click(cont2);
    await waitFor(() => {
      const answers = commandCalls(mock.calls, "item_response");
      expect(answers.length).toBe(2);
      expect(answers[1]?.body?.payload).toMatchObject({
        item_id: "post_10",
        value: "Computer programs",
      });
      expect(commandCalls(mock.calls, "proceed").length).toBe(1);
    });
  });

  it("with no options items the page behaves exactly as before — one page, one proceed", async () => {
    const mock = fakeFetch(routesFor("post_questionnaire", { instrument: POST_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "A few questions" });
    const cont = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => expect(commandCalls(mock.calls, "proceed").length).toBe(1));
    // No second page appears.
    expect(screen.queryByText("Looking back")).toBeNull();
  });
});

// ----- UX round 8: sequential open-ended follow-ups --------------------------------------------

const OE_INSTRUMENT = {
  stage: "open_ended",
  instrument_id: "open_ended_followup",
  instrument_version: 1,
  applies_to: "session",
  response: { type: "free_text", min: 0, max: 4000, min_label: "", max_label: "" },
  items: [
    {
      item_id: "crate_strategy",
      text: "There was no single right way to approach this task, and people worked through it very differently. Briefly, how did you decide on a location for each of your crates?",
      skippable: false,
      options: null,
      free_text: true,
    },
    {
      item_id: "source_reliance",
      text: "When you weren't sure about a crate, how did you decide which source to rely on: your case file, the team chat, or the AI assistant?",
      skippable: false,
      options: null,
      free_text: true,
    },
  ],
  seats: [],
  answered_items: [],
  answered_crates: [],
  pending_crates: [],
};

describe("open-ended follow-ups (UX round 8)", () => {
  it("shows the first question alone until it is submitted, then the second below it", async () => {
    const mock = fakeFetch(routesFor("open_ended", { instrument: OE_INSTRUMENT }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Two last questions" });
    // Q1 visible, Q2 hidden until Q1 submits.
    const q1 = screen.getByLabelText(/There was no single right way/);
    expect(screen.queryByLabelText(/which source to rely on/)).toBeNull();
    const cont = screen.getByRole("button", { name: "Continue" });
    expect((cont as HTMLButtonElement).disabled).toBe(true); // blank textarea blocks

    await waitEnabled(q1);
    fireEvent.change(q1, { target: { value: "I matched each crate to its file." } });
    await waitEnabled(cont);
    fireEvent.click(cont);
    await waitFor(() => {
      const calls = commandCalls(mock.calls, "item_response");
      expect(calls.length).toBe(1);
      expect(calls[0]?.body?.payload).toMatchObject({
        item_id: "crate_strategy",
        value: "I matched each crate to its file.",
      });
    });

    // Q2 appears below the completed Q1 on the same page — the first item
    // shows its recorded state instead of disappearing.
    await screen.findByLabelText(/which source to rely on/);
    expect(screen.getByText(/There was no single right way/)).toBeTruthy();
    expect(screen.getByText("Response recorded.")).toBeTruthy();
    // No proceed — the final item's commit advances the stage server-side.
    expect(commandCalls(mock.calls, "proceed").length).toBe(0);

    const q2 = screen.getByLabelText(/which source to rely on/);
    fireEvent.change(q2, { target: { value: "The file first, then chat." } });
    const cont2 = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(cont2);
    fireEvent.click(cont2);
    await waitFor(() => {
      const calls = commandCalls(mock.calls, "item_response");
      expect(calls.length).toBe(2);
      expect(calls[1]?.body?.payload).toMatchObject({
        item_id: "source_reliance",
        value: "The file first, then chat.",
      });
      expect(commandCalls(mock.calls, "proceed").length).toBe(0);
    });
  });

  it("a reload mid-stage restores submitted state from answered_items", async () => {
    const resumed = {
      ...OE_INSTRUMENT,
      answered_items: ["crate_strategy"],
    };
    const mock = fakeFetch(routesFor("open_ended", { instrument: resumed }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Two last questions" });
    // Q1 already recorded — locked; Q2 ready for input.
    expect(screen.getByText("Response recorded.")).toBeTruthy();
    const q2 = await screen.findByLabelText(/which source to rely on/);
    await waitEnabled(q2);
    expect(screen.queryAllByRole("textbox").length).toBe(1);
  });
});

describe("debrief and completion (INV-0907)", () => {
  it("Connect completion reload displays the fixed project code and return instruction", async () => {
    const mock = fakeFetch(routesFor("completion", {}, {
      "GET /session": () => json(session("completion", {
        completion_code: "CONNECT-TEST", completion_platform: "connect",
      })),
    }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "You are finished" });
    expect(screen.getByTestId("completion-code").textContent).toBe("CONNECT-TEST");
    expect(screen.getByText(/Return to Connect and enter this code/)).toBeTruthy();
  });

  it("Connect redirect handoff survives reload without showing an unrelated code", async () => {
    const url = "https://connect.cloudresearch.com/participant/project/PROJECT1/complete";
    const mock = fakeFetch(routesFor("completion", {}, {
      "GET /session": () => json(session("completion", {
        completion_url: url, completion_platform: "connect",
      })),
    }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    expect((await screen.findByRole("link", { name: "Return to Connect" })).getAttribute("href")).toBe(url);
    expect(screen.queryByTestId("completion-code")).toBeNull();
  });

  it("debrief goes straight to completion — ack, disposition and code in one click", async () => {
    const routes = routesFor("debrief", {
      material: { ...MATERIAL, artifact_key: "debrief.tp_reference@v1", slot: "instruments.debrief" },
    });
    // The real debrief_ack returns the transitioned stage — the handoff chain
    // keys the disposition step off it.
    routes["POST /commands"] = (b) => {
      const out = okCommand(b ?? {});
      if (b?.kind === "debrief_ack") {
        return json({
          command_id: b.command_id,
          accepted: true,
          reason: null,
          committed_revision: 6,
          result: { stage: "data_use_choice", writer_id: "w-1" },
          event_seqs: [],
          replayed: false,
        });
      }
      return out;
    };
    routes["POST /complete"] = (b) =>
      json({
        command_id: b?.command_id,
        accepted: true,
        reason: null,
        committed_revision: 7,
        result: { stage: "completion", completion_code: "INV-TEST-4321", writer_id: "w-1" },
        event_seqs: [],
        replayed: false,
      });
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Debrief" });
    // No artifact chrome on the participant page (UX round 2, item 12).
    expect(screen.queryByTestId("material-ref")).toBeNull();
    // No dismissal affordance — Continue is the only control.
    const ack = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(ack);
    fireEvent.click(ack);
    // UX round 2, item 13: the questions/concerns page is gone — the debrief
    // Continue records the ack, the use_all disposition and completion in order.
    await waitFor(() => {
      expect(commandCalls(mock.calls, "debrief_ack").length).toBe(1);
      const choices = commandCalls(mock.calls, "data_use_choice");
      expect(choices.length).toBe(1);
      expect((choices[0]?.body?.payload as Record<string, unknown>).choice).toBe("use_all");
      expect(
        mock.calls.some((c) => c.method === "POST" && c.url.endsWith("/complete")),
      ).toBe(true);
    });
  });

  it("data-use choice gates Finish; completion serves the recoverable code", async () => {
    // The workbench projection follows the committed stage once complete lands.
    let completed = false;
    const routes = routesFor("data_use_choice", {});
    routes["GET /workbench"] = () =>
      json(workbench({ stage: completed ? "completion" : "data_use_choice" }));
    routes["POST /complete"] = (b) => {
      completed = true;
      return json({
        command_id: b?.command_id,
        accepted: true,
        reason: null,
        committed_revision: 7,
        result: { stage: "completion", completion_code: "INV-TEST-1234", writer_id: "w-1" },
        event_seqs: [],
        replayed: false,
      });
    };
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    // UX feedback: the post-debrief picker is gone — contact info only, Finish records
    // the default disposition and completes.
    await screen.findByRole("heading", { name: "Questions or concerns?" });
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByText(/irb@university.edu/)).toBeTruthy();
    const finish = screen.getByRole("button", { name: "Finish" });
    await waitEnabled(finish);
    fireEvent.click(finish);
    await waitFor(() => expect(commandCalls(mock.calls, "data_use_choice").length).toBe(1));
    expect(
      (commandCalls(mock.calls, "data_use_choice")[0]?.body?.payload as Record<string, unknown>)
        .choice,
    ).toBe("use_all");
    await screen.findByRole("heading", { name: "You are finished" });
    expect(screen.getByTestId("completion-code").textContent).toBe("INV-TEST-1234");
  });

  it("a reloaded completion snapshot re-serves the same code (no dead end)", async () => {
    const mock = fakeFetch(
      routesFor("completion", {}, {
        "GET /session": () =>
          json(session("completion", { completion_code: "INV-TEST-9999" })),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "You are finished" });
    expect(screen.getByTestId("completion-code").textContent).toBe("INV-TEST-9999");
  });
});

// ----- INV-0908: withdrawal + terminal surfaces -----------------------------------------------------

describe("withdrawal (INV-0908)", () => {
  it("no on-screen withdraw control — closing the browser is the quit path (RL 2026-10-05)", async () => {
    // The command stays reachable server-side (admin/monitor); the persistent
    // "Leave the study" link is removed — IRB does not require it and a
    // constant quit affordance suggests leaving mid-task.
    const mock = fakeFetch(routesFor("consent", { material: MATERIAL }));
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Team Decision-Making Study" });
    expect(screen.queryByRole("button", { name: "Leave the study" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Yes, withdraw" })).toBeNull();
  });

  it("no withdraw control on the workbench either", async () => {
    render(
      <App fetchImpl={fakeFetch(routesFor("task", {})).impl} deps={{ eventSourceFactory: null }} />,
    );
    await screen.findByRole("heading", { name: "Case file" });
    expect(screen.queryByRole("button", { name: "Leave the study" })).toBeNull();
  });

  it("the terminal screen preserves payment/record language", async () => {
    const mock = fakeFetch(
      routesFor("withdrawn", {}, {
        "GET /session": () =>
          json(session("withdrawn", { terminal_reason: "participant_withdrawal" })),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Session ended" });
    expect(screen.getByText(/payment the study listing described is unaffected/)).toBeTruthy();
    // Withdraw is not offered on terminal screens.
    expect(screen.queryByRole("button", { name: "Leave the study" })).toBeNull();
  });

  it("a check-in kill says 'not completed' and drops the generic technical copy (BA-91)", async () => {
    const mock = fakeFetch(
      routesFor("technical_stop", {}, {
        "GET /session": () =>
          json(session("technical_stop", { terminal_reason: "checkin_incomplete" })),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Session ended" });
    // "Not completed" covers a submitted rating stalled at reveal — it must not
    // claim the check-in went unanswered, and the generic line would conflict.
    expect(screen.getByText(/check-in was not completed within the response window/)).toBeTruthy();
    expect(screen.queryByText(/technical problem/)).toBeNull();
    expect(screen.queryByText(/not answered/)).toBeNull();
  });
});

// ----- INV-0903: review-first banner ------------------------------------------------------------------

describe("review-first solo review (INV-0903)", () => {
  it("shows the solo-review notice while drafting stays enabled", async () => {
    const mock = fakeFetch(
      routesFor("solo_review", {
        channels: ["file_work", "answer_drafting"],
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Case file" });
    expect(document.querySelector('[data-surface="solo_review"]')).toBeTruthy();
    expect(
      screen.getByText(/Review your case file independently for one minute/),
    ).toBeTruthy();
  });
});

// ----- UX round 3 (UX_Feedback_v3.pdf) ------------------------------------------------------------

describe("UX round 3 — waiting room and completion", () => {
  it("shows one teammate already connected, the third joining after the participant (item 4)", async () => {
    const mock = fakeFetch(
      routesFor("scripted_connecting", {}, {
        "GET /events": () =>
          json({
            events: [event(1, "teammate_presented", { seat: "JT" }, { stage: "scripted_connecting" })],
            next_event_seq: 2,
            scanned_seq: 1,
          }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Connecting you to your team" });
    expect(screen.getByText("The clock starts when all of you have pressed Start.")).toBeTruthy();
    const rows = Array.from(document.querySelectorAll(".joining-list li"));
    expect(rows.map((r) => r.getAttribute("data-seat"))).toEqual(["JT", "you", "PM"]);
    expect(rows.map((r) => r.querySelector(".status")?.textContent)).toEqual([
      "connected",
      "connected",
      "joining…",
    ]);
    // Start waits for the third teammate.
    expect((screen.getByRole("button", { name: "Start" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables Start once the third teammate has joined", async () => {
    const mock = fakeFetch(
      routesFor("scripted_connecting", {}, {
        "GET /events": () =>
          json({
            events: [
              event(1, "teammate_presented", { seat: "JT" }, { stage: "scripted_connecting" }),
              event(2, "teammate_presented", { seat: "PM" }, { stage: "scripted_connecting" }),
            ],
            next_event_seq: 3,
            scanned_seq: 2,
          }),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Connecting you to your team" });
    await waitEnabled(screen.getByRole("button", { name: "Start" }));
    const statuses = Array.from(document.querySelectorAll(".joining-list .status")).map(
      (s) => s.textContent,
    );
    expect(statuses).toEqual(["connected", "connected", "connected"]);
  });

  it("the completion page shows the code without the removed copy (item 12)", async () => {
    const mock = fakeFetch(
      routesFor("completion", {}, {
        "GET /session": () => json(session("completion", { completion_code: "INV-TEST-0003" })),
      }),
    );
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "You are finished" });
    expect(screen.getByTestId("completion-code").textContent).toBe("INV-TEST-0003");
    expect(screen.queryByText(/Copy it and keep it/)).toBeNull();
    expect(screen.queryByText(/return to the study platform/)).toBeNull();
    expect(screen.queryByText(/safely be reloaded/)).toBeNull();
  });
});

describe("UX round 3 — final handoff", () => {
  it("the debrief chain never flashes the Questions or concerns page (item 11)", async () => {
    let releaseComplete: (() => void) | null = null;
    const routes = routesFor("debrief", {
      material: { ...MATERIAL, artifact_key: "debrief.tp_reference@v1", slot: "instruments.debrief" },
    });
    routes["POST /commands"] = (b) =>
      b?.kind === "debrief_ack"
        ? json({
            command_id: b.command_id,
            accepted: true,
            reason: null,
            committed_revision: 6,
            result: { stage: "data_use_choice", writer_id: "w-1" },
            event_seqs: [],
            replayed: false,
          })
        : okCommand(b ?? {});
    routes["POST /complete"] = ((b?: Record<string, unknown>) =>
      new Promise<Response>((resolve) => {
        releaseComplete = () =>
          resolve(
            json({
              command_id: b?.command_id,
              accepted: true,
              reason: null,
              committed_revision: 7,
              result: { stage: "completion", completion_code: "INV-TEST-1111", writer_id: "w-1" },
              event_seqs: [],
              replayed: false,
            }),
          );
      })) as unknown as RouteMap[string];
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Debrief" });
    const ack = screen.getByRole("button", { name: "Continue" });
    await waitEnabled(ack);
    fireEvent.click(ack);
    // Mid-chain: the session is in data_use_choice, completion not yet committed.
    await waitFor(() => expect(releaseComplete).not.toBeNull());
    expect(commandCalls(mock.calls, "data_use_choice").length).toBe(1);
    expect(screen.queryByRole("heading", { name: "Questions or concerns?" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Debrief" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Finishing…" })).toBeTruthy();
    releaseComplete!();
    await screen.findByRole("heading", { name: "You are finished" });
    expect(screen.queryByRole("heading", { name: "Questions or concerns?" })).toBeNull();
  });
});

describe("FR-1 interaction mode — practice (UX round 4)", () => {
  it("natural-language-only mode renders the ask composer as a disabled preview — share + pick gate page 1", async () => {
    // UX_Feedback_v9 + 2026-10-12 owner direction: on share-only builds the typed
    // ask is not exercised at all — the composer renders inert (like the
    // structured variant), only Share can be activated. v14 (2026-10-16):
    // the answer-entry pick also gates.
    const routes = routesFor("training", {
      material: null,
      practice: PRACTICE,
      structured_requests: false,
    });
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Practice: Sharing intel with your team" });
    expect(screen.queryByRole("combobox", { name: "Ask the team about a crate" })).toBeNull();
    const input = screen.getByRole("textbox", { name: "Ask the team for intel" });
    expect((input as HTMLInputElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.queryByText(/Now try asking the team for intel/)).toBeNull();
    expect(screen.getByText("Teammate shared entry K1.")).toBeTruthy();

    const cont = screen.getByRole("button", { name: "Complete the practice steps to Continue" });
    expect((cont as HTMLButtonElement).disabled).toBe(true);

    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);
    await screen.findByText("Shared in team chat");
    expect((cont as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ }), { target: { value: "The Sawmill" } });
    await screen.findByText(/Location saved for Crate 3/);
    const done = await screen.findByRole("button", { name: "Continue" });
    await waitEnabled(done);
    // No ask command is ever dispatched on a share-only build.
    const asks = commandCalls(mock.calls, "training_step").filter(
      (c) => (c.body?.payload as Record<string, unknown>).action === "ask",
    );
    expect(asks.length).toBe(0);
  });

  it("an ask-required build keeps the typed ask live; a rejected ask holds Continue", async () => {
    const routes = routesFor("training", {
      material: null,
      practice: { ...PRACTICE, ask_required: true },
      structured_requests: false,
    });
    let failAsk = true;
    routes["POST /commands"] = (b) => {
      const p = (b?.payload ?? {}) as Record<string, unknown>;
      if (b?.kind === "training_step" && p.action === "ask") {
        if (failAsk) {
          return json({ error: "rejected", detail: "practice_ask_invalid" }, 409);
        }
        return json({
          command_id: b?.command_id,
          accepted: true,
          reason: null,
          committed_revision: 1,
          result: {
            stage: "training",
            card: {
              crate: 3,
              stamp: "Today, 9:20 AM",
              body: "Crate 3 sighted near The Marina dock entrance.",
              shared_by: "Teammate",
            },
          },
          event_seqs: [],
          replayed: false,
        });
      }
      return okCommand(b ?? {});
    };
    const mock = fakeFetch(routes);
    render(<App fetchImpl={mock.impl} deps={{ eventSourceFactory: null }} />);
    await screen.findByRole("heading", { name: "Practice: Sharing intel with your team" });
    expect(screen.getByText(/Now try asking the team for intel/)).toBeTruthy();
    const shareBtn = screen.getAllByRole("button", { name: "Share" })[0]!;
    await waitEnabled(shareBtn);
    fireEvent.click(shareBtn);
    await screen.findByText("Shared in team chat");
    // Share alone is not enough — the required ask (and the v14 pick) still
    // gate Continue.
    const cont = screen.getByRole("button", { name: "Complete the practice steps to Continue" });
    expect((cont as HTMLButtonElement).disabled).toBe(true);
    const input = screen.getByRole("textbox", { name: "Ask the team for intel" });
    fireEvent.change(input, { target: { value: "hello" } });
    const sendBtn = screen.getByRole("button", { name: "Send" });
    await waitEnabled(sendBtn);
    fireEvent.click(sendBtn);
    await screen.findByRole("alert");
    expect(
      (screen.getByRole("button", { name: "Complete the practice steps to Continue" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    // A valid retry clears the ask — but the pick still gates.
    failAsk = false;
    fireEvent.change(input, { target: { value: "anything on crate 3?" } });
    await waitEnabled(sendBtn);
    fireEvent.click(sendBtn);
    await screen.findByText(/Crate 3 sighted near The Marina dock entrance/);
    expect(
      (screen.getByRole("button", { name: "Complete the practice steps to Continue" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.change(screen.getByRole("combobox", { name: /Pick a location for practice Crate 3/ }), { target: { value: "The Sawmill" } });
    await screen.findByText(/Location saved for Crate 3/);
    const done = await screen.findByRole("button", { name: "Continue" });
    await waitEnabled(done);
  });
});
