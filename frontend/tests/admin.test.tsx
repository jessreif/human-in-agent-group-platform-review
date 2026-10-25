/**
 * Admin subtree tests (INV-1301/1309 surface level): login gating, role-scoped tabs,
 * CSRF double-submit on mutations, and error rendering. fetch is mocked — no network.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { JSDOM } from "jsdom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminApp } from "../src/admin/AdminApp";
import { adminApi, AdminApiError } from "../src/admin/api";
import { buildParticipantViewHtml } from "../src/admin/participantView";
import { buildSimulationHtml } from "../src/admin/simulation";
import { Walkthrough } from "../src/admin/walkthrough";

type FetchMock = ReturnType<typeof vi.fn>;

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: FetchMock;

// jsdom baseline, captured before any test mutates these descriptors.
const originalScrollIntoView = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "scrollIntoView",
);
const originalCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

// Pushed before assertions so a failed expect still restores.
const afterEachRestores: Array<() => void> = [];

function restoreDescriptor(
  target: object,
  key: string,
  descriptor: PropertyDescriptor | undefined,
): void {
  if (descriptor) Object.defineProperty(target, key, descriptor);
  else Reflect.deleteProperty(target, key);
}

function trackWindow<W extends { close(): void }>(opened: W): W {
  afterEachRestores.push(() => opened.close());
  return opened;
}

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "inv_admin_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

afterEach(() => {
  cleanup();
  // Unstub first so URL method restores land on the real constructor.
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  while (afterEachRestores.length) afterEachRestores.pop()?.();
  expect(Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView")).toEqual(
    originalScrollIntoView,
  );
  expect(Object.getOwnPropertyDescriptor(URL, "createObjectURL")).toEqual(
    originalCreateObjectURL,
  );
  expect(Object.getOwnPropertyDescriptor(URL, "revokeObjectURL")).toEqual(
    originalRevokeObjectURL,
  );
});

describe("admin api client", () => {
  it("sends X-CSRF-Token on mutations from the readable cookie", async () => {
    document.cookie = "inv_admin_csrf=csrf-test-value; path=/";
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    await adminApi.logout();
    const call = fetchMock.mock.calls[0]!;
    expect(call[0]).toBe("/investigators/api/admin/logout");
    const headers = call[1].headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-test-value");
  });

  it("does not send CSRF on GETs", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { sessions: [] }));
    await adminApi.sessions({});
    const headers = fetchMock.mock.calls[0]![1].headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("raises AdminApiError with the server error token", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(403, { error: "forbidden_role", detail: "nope" }),
    );
    await expect(adminApi.monitor()).rejects.toMatchObject({
      status: 403,
      kind: "forbidden_role",
    });
    await expect(adminApi.monitor()).rejects.toBeInstanceOf(AdminApiError);
  });

  it("never forwards identity headers", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { sessions: [] }));
    await adminApi.sessions({});
    const headers = (fetchMock.mock.calls[0]![1].headers ?? {}) as Record<string, string>;
    for (const name of Object.keys(headers)) {
      expect(name.toLowerCase()).not.toMatch(/remote-user|eppn|shib/);
    }
  });
});

describe("AdminApp", () => {
  it("shows the login form when unauthenticated", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { error: "unauthenticated" }),
    );
    render(<AdminApp />);
    await screen.findByText("Research admin sign-in");
    expect(screen.getByLabelText(/Role/)).toBeTruthy();
  });

  it("logs in as researcher and shows researcher tabs", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { error: "unauthenticated" })) // whoami
      .mockResolvedValueOnce(
        jsonResponse(200, {
          role: "researcher",
          actor: "pi@university.edu",
          expires_at: 1,
          app_mode: "live",
        }),
      ) // login
      .mockResolvedValue(jsonResponse(200, { sessions: [] })); // sessions tab
    render(<AdminApp />);
    fireEvent.change(await screen.findByLabelText(/Password/), {
      target: { value: "pw" },
    });
    fireEvent.click(screen.getByText("Sign in"));
    await waitFor(() => screen.getByText("monitor"));
    expect(screen.getByText("sessions")).toBeTruthy();
    // the redaction gate was removed (owner direction 2026-10-12)
    expect(screen.queryByText("redaction")).toBeNull();
    // documentation tab is researcher-visible
    expect(screen.getByText("documentation")).toBeTruthy();
    // compensation tab is not a researcher surface
    expect(screen.queryByText("compensation")).toBeNull();
  });

  it("keeps historical group allocations visible beside current neutral cells", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(jsonResponse(200, {
          role: "researcher", actor: "test", expires_at: 1, app_mode: "live",
        }));
      if (url.includes("/batches"))
        return Promise.resolve(jsonResponse(200, {
          batches: [],
          groups: [{
            group_id: "study.live.v11", mode: "live",
            enrollment_open: true, allocated: 38,
            cells: [
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "nopress", allocated: 0, allocatable: true },
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "high", allocated: 19, allocatable: false },
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "low", allocated: 19, allocatable: false },
            ],
          }],
        }));
      if (url.includes("/preview-combos"))
        return Promise.resolve(jsonResponse(200, { combos: [] }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    fireEvent.click(await screen.findByText("batches"));
    await screen.findByText("allocated=38");
    const counts = screen.getByText(/tp_ai_v13\/default: 0/).textContent;
    expect(counts).toContain("tp_ai_v13/variant high: 19 (historical)");
    expect(counts).toContain("tp_ai_v13/variant low: 19 (historical)");
    expect(document.body.textContent).not.toMatch(/time.pressure|TIME_PRESSURE_ENABLED/i);
  });

  it("shows valid completion targets separately from assignments and active holds", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami")) return Promise.resolve(jsonResponse(200, {
        role: "researcher", actor: "test", expires_at: 1, app_mode: "live",
      }));
      if (url.includes("/batches")) return Promise.resolve(jsonResponse(200, {
        batches: [], groups: [{
          group_id: "study.live.v12", mode: "live", enrollment_open: true, allocated: 18,
          quota: { platform: "prolific", test_only: false, completed: 12, total_target: 16,
            active: 3, available: 1, target_met: false, configuration_matches: true },
          cells: [{ batch_id: "pilot.none", manifest_id: "tp_ai_ovnone_v3", arm: "nopress",
            allocated: 6, target: 4, completed: 3, active: 0, available: 1 }],
        }],
      }));
      return Promise.resolve(jsonResponse(200, { combos: [] }));
    });
    render(<AdminApp />);
    fireEvent.click(await screen.findByText("batches"));
    await screen.findByText(/prolific only · valid completers=12\/16/);
    expect(screen.getByText(/3\/4 valid completers, 0 active, 1 available/)).toBeTruthy();
    expect(screen.getByText("allocated=18")).toBeTruthy();
  });

  it("documentation tab lists files with view and download links", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, {
          role: "researcher",
          actor: "pi@university.edu",
          expires_at: 1,
          app_mode: "live",
        }),
      ) // whoami
      .mockImplementation((url: string) => {
        if (url.includes("/docs"))
          return Promise.resolve(
            jsonResponse(200, {
              docs: [
                {
                  id: "readme",
                  label: "README — repository overview and ops commands",
                  file: "README.md",
                  size: 8123,
                  modified_at: "2026-10-05T09:00:00",
                },
                {
                  id: "codegraph",
                  label: "CodeGraph index (SQLite) — investigators tree only",
                  file: ".codegraph/codegraph.db",
                  size: 13631488,
                },
              ],
            }),
          );
        return Promise.resolve(jsonResponse(200, { links: [], sessions: [] }));
      });
    render(<AdminApp />);
    await waitFor(() => screen.getByText("documentation"));
    fireEvent.click(screen.getByText("documentation"));
    const doc = await screen.findByText(/README — repository overview/);
    expect(doc).toBeTruthy();
    expect(screen.getByText("7.9 KB")).toBeTruthy();
    expect(screen.getByText("13.0 MB")).toBeTruthy();
    const links = screen.getAllByRole("link");
    const hrefs = links.map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/investigators/api/admin/docs/readme");
    expect(hrefs).toContain("/investigators/api/admin/docs/readme?download=1");
    expect(hrefs).toContain("/investigators/api/admin/docs/codegraph?download=1");
  });

  it("compensation role sees only the compensation tab", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, {
          role: "compensation",
          actor: "c@university.edu",
          expires_at: 1,
          admin_emails: [],
        }),
      ) // whoami
      .mockResolvedValue(jsonResponse(200, { rows: [] })); // compensation queue
    render(<AdminApp />);
    await waitFor(() => screen.getByText("compensation"));
    expect(screen.queryByText("redaction")).toBeNull();
    expect(screen.queryByText("monitor")).toBeNull();
  });

  it("surfaces API errors instead of crashing", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, {
          role: "researcher",
          actor: "a",
          expires_at: 1,
          admin_emails: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(503, { error: "persistence_unavailable" }),
      );
    render(<AdminApp />);
    await screen.findByText(/persistence_unavailable/);
  });

  it("hides expired/dead links by default and reveals them via the toggle", async () => {
    const past = Math.floor(Date.now() / 1000) - 3600;
    const future = Math.floor(Date.now() / 1000) + 3600;
    const link = (nonce: string, expires_at: number, session: unknown) => ({
      manifest_id: "tp_demo_v3",
      arm: "low",
      nonce,
      entry_url: `/investigators/?pt=${nonce}`,
      issued_at: "2026-10-03T00:00:00Z",
      expires_at,
      session,
    });
    const finishedSession = {
      session_id: "11111111-2222-3333-4444-555555555555",
      stage: "completion",
      terminal_reason: null,
      created_at: "2026-10-03T00:00:00Z",
      stage_entered_at: "2026-10-03T00:00:00Z",
    };
    const liveSession = {
      ...finishedSession,
      session_id: "22222222-2222-3333-4444-555555555555",
      stage: "task",
    };
    const links = [
      link("dead-unused", past, null),
      link("dead-finished", past, finishedSession),
      link("live-unopened", future, null),
      link("live-running", past, liveSession), // expired link, session still live
    ];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "preview",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos: [], timing_modes: [] }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    // no redaction surface exists at any mode (owner direction 2026-10-12)
    expect(screen.queryByText("redaction")).toBeNull();
    // Only live links show by default: unopened-but-valid and the running session
    // (whose entry window expired mid-session — status stays honest about that).
    await screen.findByText("No live session");
    expect(
      screen.getByText("In progress — Working on the task (entry link expired)"),
    ).toBeTruthy();
    expect(screen.queryByText("Link expired — mint a new one")).toBeNull();
    expect(screen.queryByText("Finished — completion code shown")).toBeNull();
    // Toggle reveals the two dead links.
    const toggle = await screen.findByLabelText(/show 2 expired\/deleted links/);
    fireEvent.click(toggle);
    await screen.findByText("Finished — completion code shown");
    expect(screen.getAllByText("Link expired — mint a new one")).toHaveLength(1);
  });

  it("links tab offers a walkthrough button that opens session detail inline", async () => {
    const future = Math.floor(Date.now() / 1000) + 3600;
    const sid = "22222222-2222-3333-4444-555555555555";
    const links = [
      {
        manifest_id: "tp_demo_v3",
        arm: "low",
        nonce: "live",
        entry_url: "/investigators/?pt=live",
        issued_at: "2026-10-03T00:00:00Z",
        expires_at: future,
        session: {
          session_id: sid,
          stage: "task",
          terminal_reason: null,
          created_at: "2026-10-03T00:00:00Z",
          stage_entered_at: "2026-10-03T00:00:00Z",
        },
      },
    ];
    const replayBody = {
      session: { mode: "preview", timing_mode: "faithful" },
      participant: {
        events: [
          {
            seq: 1,
            type: "session_opened",
            actor: "system",
            stage: "consent",
            server_time: "2026-10-03T17:00:00Z",
            payload: { mode: "preview" },
          },
        ],
        truncated: false,
      },
      restricted_status: "ok",
      restricted: { internal_events: [], model_calls: [], command_receipts: [] },
    };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "preview",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos: [], timing_modes: [] }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links }));
      if (url.includes(`/sessions/${sid}/replay`))
        return Promise.resolve(jsonResponse(200, replayBody));
      if (url.includes(`/sessions/${sid}/audit`))
        return Promise.resolve(jsonResponse(200, { entries: [] }));
      if (url.includes(`/sessions/${sid}`))
        return Promise.resolve(
          jsonResponse(200, { session: { session_id: sid, stage: "task" }, entitlements: [] }),
        );
      return Promise.resolve(jsonResponse(200, {}));
    });
    render(<AdminApp />);
    const btn = await screen.findByRole("button", { name: "walkthrough" });
    fireEvent.click(btn);
    // the unified timeline mounts inline — no navigation to the sessions tab needed
    await screen.findByText("Session opened");
    expect(screen.getByText("Session walkthrough")).toBeTruthy();
    // toggling the same button hides the panel again
    fireEvent.click(screen.getByText("hide"));
    expect(screen.queryByText("Session opened")).toBeNull();
  });

  it("sessions tab row click opens the detail and scrolls it into view", async () => {
    const sid = "33333333-3333-4333-8333-333333333333";
    const scrollSpy = vi.fn();
    // jsdom does not implement scrollIntoView; the component feature-checks it.
    const priorScrollIntoView = Object.getOwnPropertyDescriptor(
      Element.prototype,
      "scrollIntoView",
    );
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: scrollSpy,
    });
    afterEachRestores.push(() =>
      restoreDescriptor(Element.prototype, "scrollIntoView", priorScrollIntoView),
    );
    const replayBody = {
      session: { mode: "live", timing_mode: "faithful" },
      participant: {
        events: [
          {
            seq: 1,
            type: "session_opened",
            actor: "system",
            stage: "consent",
            server_time: "2026-10-03T17:00:00Z",
            payload: { mode: "live" },
          },
        ],
        truncated: false,
      },
      restricted_status: "ok",
      restricted: { internal_events: [], model_calls: [], command_receipts: [] },
    };
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes(`/sessions/${sid}/replay`))
        return Promise.resolve(jsonResponse(200, replayBody));
      if (url.includes(`/sessions/${sid}/audit`))
        return Promise.resolve(jsonResponse(200, { entries: [] }));
      if (url.includes(`/sessions/${sid}`))
        return Promise.resolve(
          jsonResponse(200, {
            session: { session_id: sid, stage: "completion" },
            entitlements: [],
          }),
        );
      if (url.includes("/sessions"))
        return Promise.resolve(
          jsonResponse(200, {
            sessions: [
              {
                session_id: sid,
                batch_id: "study.live.v5.ai",
                created_at: "2026-10-09T21:56:47.904446-04:00",
                mode: "live",
                stage: "completion",
                terminal_reason: null,
                n_answered_crates: 4,
                n_reconnects: 0,
                n_rejected_commands: 3,
                n_open_episodes: 0,
                completion_outcome: "completed",
              },
            ],
          }),
        );
      return Promise.resolve(jsonResponse(200, {}));
    });
    render(<AdminApp />);
    fireEvent.click(await screen.findByText("sessions"));
    // The detail card mounts below the table — the regression was that nothing
    // visibly changed on click, so selection now scrolls it into view.
    fireEvent.click(await screen.findByText("33333333"));
    await screen.findByText("Session walkthrough");
    expect(scrollSpy).toHaveBeenCalled();
    await screen.findByText("Session opened");
  });

  it("sessions tab defaults the version filter to the latest study.live group", async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes("/batches"))
        return Promise.resolve(
          jsonResponse(200, {
            batches: [],
            groups: [
              {
                group_id: "study.live.v10",
                mode: "live",
                enrollment_open: false,
                allocated: 4,
                cells: [],
              },
              {
                group_id: "study.smoke.v11",
                mode: "qa",
                enrollment_open: false,
                allocated: 0,
                cells: [],
              },
              {
                group_id: "study.live.v11",
                mode: "live",
                enrollment_open: true,
                allocated: 0,
                cells: [],
              },
            ],
          }),
        );
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    fireEvent.click(await screen.findByText("sessions"));
    // The newest study.live group wins — a newer smoke group must not shadow it,
    // and no unfiltered (pooled) sessions request should fire first.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) =>
            String(c[0]).includes("/sessions") &&
            String(c[0]).includes("batch_group_id=study.live.v11"),
        ),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.some(
        (c) =>
          String(c[0]).includes("/sessions") &&
          !String(c[0]).includes("batch_group_id"),
      ),
    ).toBe(false);
    // "All" drops the group filter entirely.
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "all" } });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          (c) =>
            String(c[0]).includes("/sessions") &&
            !String(c[0]).includes("batch_group_id"),
        ),
      ).toBe(true),
    );
  });

  it("uses neutral default-build labels without changing the minted arm or historical records", async () => {
    const combo = {
      manifest_id: "tp_ai_v13", arm: "nopress",
      study_key: "investigators_time_pressure", material_status: "approved",
      test_group: "comparison", consent_current: true, current: true,
      summary: ["300-second task on a generated world (8 crates, 6 hideouts)"],
    };
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/whoami"))
        return Promise.resolve(jsonResponse(200, {
          role: "researcher", actor: "test", expires_at: 1, app_mode: "live",
        }));
      if (url.includes("/preview-combos"))
        return Promise.resolve(jsonResponse(200, {
          combos: [combo], time_pressure_enabled: false,
        }));
      if (url.includes("/batches"))
        return Promise.resolve(jsonResponse(200, {
          batches: [],
          groups: [{
            group_id: "study.live.v11", mode: "live", enrollment_open: true,
            allocated: 38,
            cells: [
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "nopress", allocated: 0, allocatable: true },
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "high", allocated: 19, allocatable: false },
              { batch_id: "study.live.v11.ai", manifest_id: "tp_ai_v13", arm: "low", allocated: 19, allocatable: false },
            ],
          }],
        }));
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, {
          links: [{
            nonce: "test-default", manifest_id: "tp_ai_v13", arm: "nopress",
            entry_url: "/investigators/?pt=test", expires_at: null, session: null,
          }],
        }));
      if (url.endsWith("/previews") && init?.method === "POST")
        return Promise.resolve(jsonResponse(200, { entry_url: "/investigators/?pt=test" }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    const mintBtn = await screen.findByRole("button", {
      name: "⚑ AI teammates · typed chat",
    });
    expect(mintBtn.title).toBe("Fielded live on study.live.v11");
    const myLink = await screen.findByText("AI teammates · typed chat");
    expect(myLink.closest("tr")?.textContent).not.toContain("nopress");
    expect(document.body.textContent).not.toMatch(/pressure|lab comparison/i);
    fireEvent.click(mintBtn);
    await screen.findByRole("status");
    const mint = fetchMock.mock.calls.find((call) =>
      String(call[0]).endsWith("/previews") && call[1]?.method === "POST",
    );
    expect(JSON.parse(String(mint?.[1]?.body))).toMatchObject({
      manifest_id: "tp_ai_v13", arm: "nopress", timing_mode: "faithful",
    });
    fireEvent.click(screen.getByText("monitor"));
    await screen.findByText("Assignment / exposure / completion balance");
    expect(document.body.textContent).not.toMatch(/pressure|2×2/i);
  });

  it("links tab defaults to the fielded contract while a live group is open", async () => {
    const combos = [
      {
        manifest_id: "tp_review_first_v11",
        arm: "high",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "pilot_contract",
        consent_current: true,
        current: true,
        summary: [
          "360-second task on a generated world (8 crates, 6 hideouts)",
          "All channels open from the start — no solo-review period",
          "Asking: 'Ask the team about…' crate picker plus typed chat",
        ],
        demo_url: "demos/investigators_nosolo_structured_high_demo.html",
      },
      {
        manifest_id: "tp_review_first_v11",
        arm: "low",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "pilot_contract",
        consent_current: true,
        current: true,
        summary: ["Optional check-in at 60 s: teammate ratings 2 and 3"],
        demo_url: "demos/investigators_nosolo_structured_low_demo.html",
      },
      {
        manifest_id: "tp_ai_v2",
        arm: "high",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "comparison",
        consent_current: true,
        current: true,
        summary: ["Asking: typed chat only — no structured ask controls"],
        demo_url: "demos/investigators_nosolo_nl_high_demo.html",
      },
      {
        manifest_id: "tp_ai_v2",
        arm: "low",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "comparison",
        consent_current: true,
        current: true,
        summary: ["Optional check-in at 60 s: teammate ratings 2 and 3"],
        demo_url: "demos/investigators_nosolo_nl_low_demo.html",
      },
      {
        manifest_id: "tp_scripted_v4",
        arm: "high",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "comparison",
        consent_current: true,
        current: true,
        summary: [
          "Asking: typed chat only — no structured ask controls",
          "Teammate replies: deterministic script",
        ],
        demo_url: "demos/investigators_nosolo_nl_high_demo.html",
      },
      {
        manifest_id: "tp_scripted_v4",
        arm: "low",
        study_key: "investigators_time_pressure",
        material_status: "approved",
        test_group: "comparison",
        consent_current: true,
        current: true,
        demo_url: "demos/investigators_nosolo_nl_low_demo.html",
      },
      {
        manifest_id: "tp_demo_nl_v2",
        arm: "high",
        study_key: "investigators_time_pressure",
        material_status: "reference",
        test_group: "archive",
        consent_current: false,
        current: false,
      },
    ];
    const confirmMock = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmMock);
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "preview",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos, timing_modes: ["faithful"] }),
        );
      if (url.includes("/batches"))
        return Promise.resolve(
          jsonResponse(200, {
            batches: [
              {
                batch_id: "prolific.live.n5.v11",
                manifest_id: "tp_review_first_v11",
                mode: "live",
                enrollment_open: true,
              },
            ],
          }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links: [] }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    // With a live group open the default surface IS the fielded contract —
    // testers mint the exact conditions participants get. The lab comparison
    // builds and the archive all sit behind the checkbox.
    for (const arm of ["high", "low"]) {
      const btn = await screen.findByRole("button", {
        name: new RegExp(`⚑ AI teammates · asks \\+ chat — variant ${arm}`),
      });
      expect(btn.title).toBe("Fielded live on prolific.live.n5.v11");
      fireEvent.click(btn);
      expect(confirmMock).not.toHaveBeenCalled();
    }
    // Comparison combos are not listed by default while fielding is live.
    expect(
      screen.queryByRole("button", {
        name: /AI teammates · typed chat — variant high/,
      }),
    ).toBeNull();
    // "One of each" covers the default test set: the two fielded cells.
    expect(
      screen.getByRole("button", { name: /One of each \(2 links\)/ }),
    ).toBeTruthy();
    // Every default-visible combo links its playable mock walkthrough — the
    // fielded build's structured variant.
    const mockLinks = screen.getAllByRole("link", { name: /playable mock/ });
    expect(mockLinks).toHaveLength(2);
    expect(
      mockLinks.every((l) =>
        String(l.getAttribute("href")).includes("nosolo_structured_"),
      ),
    ).toBe(true);
    // Minting the fielded build went straight through, no confirm.
    expect(
      fetchMock.mock.calls.some((c) => {
        const init = c[1] as RequestInit | undefined;
        return (
          init?.method === "POST" &&
          String(c[0]).includes("/previews") &&
          String(init?.body ?? "").includes("tp_review_first_v11")
        );
      }),
    ).toBe(true);
    // The checkbox reveals the comparison builds plus the archive.
    fireEvent.click(await screen.findByLabelText(/show all builds/));
    const comparisonBtns = await screen.findAllByRole("button", {
      name: /AI teammates · typed chat — variant high/,
    });
    const comparisonBtn = comparisonBtns.find(
      (b) => b.title === "Study preview build — creates a preview-mode test session",
    )!;
    fireEvent.click(comparisonBtn);
    expect(confirmMock).not.toHaveBeenCalled();
    // The superseded nl build stays in the archive and still asks before minting.
    const archivedBtn = await screen.findByRole("button", {
      name: /natural-language only \(v2\)|\(archived\)/,
    });
    fireEvent.click(archivedBtn);
    expect(confirmMock).toHaveBeenCalledWith(
      expect.stringContaining("archived profile"),
    );
    // Confirm cancelled → no mint request was issued for the archived profile.
    expect(
      fetchMock.mock.calls.some((c) => {
        const init = c[1] as RequestInit | undefined;
        const body = String(init?.body ?? "");
        return (
          init?.method === "POST" &&
          String(c[0]).includes("/previews") &&
          body.includes("tp_demo_nl_v2")
        );
      }),
    ).toBe(false);
  });

  it("links tab keeps the last fielded contract when the live group is closed", async () => {
    const mkCombo = (manifest_id: string, test_group: string) => ({
      manifest_id,
      arm: "nopress",
      study_key: "investigators_time_pressure",
      material_status: "approved",
      test_group,
      consent_current: true,
      current: true,
    });
    const combos = [
      // The four fielded conditions plus the rest of the launch-eligible set.
      mkCombo("tp_ai_ovnone_v3", "comparison"),
      mkCombo("tp_ai_ovnone_thin_v3", "comparison"),
      mkCombo("tp_ai_ovsome_v3", "comparison"),
      mkCombo("tp_ai_ovsome_thin_v3", "comparison"),
      mkCombo("tp_ai_ovfull_v3", "comparison"),
      mkCombo("tp_ai_ovfull_thin_v3", "comparison"),
      mkCombo("tp_ai_v14", "comparison"),
      mkCombo("tp_scripted_v13", "archive"),
    ];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos, timing_modes: ["faithful"] }),
        );
      if (url.includes("/batches"))
        return Promise.resolve(
          jsonResponse(200, {
            batches: [],
            groups: [
              {
                group_id: "study.live.v11",
                mode: "live",
                enrollment_open: false,
                cells: [
                  { batch_id: "study.live.v11.a", manifest_id: "tp_ai_v14", arm: "nopress", allocatable: true },
                ],
              },
              {
                group_id: "study.live.v12",
                mode: "live",
                enrollment_open: false,
                cells: [
                  { batch_id: "study.live.v12.none", manifest_id: "tp_ai_ovnone_v3", arm: "nopress", allocatable: true },
                  { batch_id: "study.live.v12.none_thin", manifest_id: "tp_ai_ovnone_thin_v3", arm: "nopress", allocatable: true },
                  { batch_id: "study.live.v12.some", manifest_id: "tp_ai_ovsome_v3", arm: "nopress", allocatable: true },
                  { batch_id: "study.live.v12.some_thin", manifest_id: "tp_ai_ovsome_thin_v3", arm: "nopress", allocatable: true },
                ],
              },
            ],
          }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links: [] }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    // With the group closed, the default set is still the last fielded
    // contract — the four v12 cells — not every launch-eligible manifest.
    for (const mid of [
      "tp_ai_ovnone_v3",
      "tp_ai_ovnone_thin_v3",
      "tp_ai_ovsome_v3",
      "tp_ai_ovsome_thin_v3",
    ]) {
      const btn = await screen.findByRole("button", {
        name: new RegExp(`⚑ ${mid}`),
      });
      expect(btn.title).toBe("Fielded live on study.live.v12 (closed)");
    }
    // The launchable-but-not-fielded builds hide behind the checkbox.
    expect(
      screen.queryByRole("button", { name: /ovfull/ }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /AI teammates · typed chat/ }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: /One of each \(4 links\)/ }),
    ).toBeTruthy();
  });

  it("links tab defaults to the pilot contract over the last closed fielded group", async () => {
    // v15 (2026-10-17): once a newer approved contract is published, the
    // default test set is the contract the NEXT round fields — the v4 pair —
    // not the closed v14 group's cells. The fielded builds stay ⚑-marked
    // behind "show all builds".
    const mkCombo = (manifest_id: string, arm: string, test_group: string) => ({
      manifest_id,
      arm,
      study_key: "investigators_time_pressure",
      material_status: "approved",
      test_group,
      consent_current: true,
      current: true,
    });
    const combos = [
      mkCombo("tp_ai_ovsome_thin_v9", "high", "pilot_contract"),
      mkCombo("tp_ai_ovsome_thin_v9", "mixed", "pilot_contract"),
      mkCombo("tp_ai_ovnone_v3", "high", "comparison"),
      mkCombo("tp_ai_ovnone_thin_v3", "high", "comparison"),
      mkCombo("tp_ai_ovsome_v3", "high", "comparison"),
      mkCombo("tp_ai_v14", "high", "comparison"),
    ];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos, timing_modes: ["faithful"] }),
        );
      if (url.includes("/batches"))
        return Promise.resolve(
          jsonResponse(200, {
            batches: [],
            groups: [
              {
                group_id: "study.live.v14",
                mode: "live",
                enrollment_open: false,
                cells: [
                  { batch_id: "study.live.v14.none", manifest_id: "tp_ai_ovnone_v3", arm: "nopress", allocatable: true },
                  { batch_id: "study.live.v14.none_thin", manifest_id: "tp_ai_ovnone_thin_v3", arm: "nopress", allocatable: true },
                  { batch_id: "study.live.v14.some", manifest_id: "tp_ai_ovsome_v3", arm: "nopress", allocatable: true },
                ],
              },
            ],
          }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links: [] }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    // The contract pair is the default set — nothing else is offered up front.
    expect(
      await screen.findByRole("button", {
        name: /tp_ai_ovsome_thin_v9 — variant high/,
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: /tp_ai_ovsome_thin_v9 — variant mixed/,
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /One of each \(2 links\)/ }),
    ).toBeTruthy();
    // The closed fielded builds and the rest of the launch set hide behind
    // the checkbox — they are not the contract the next round fields.
    expect(screen.queryByRole("button", { name: /ovnone/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /tp_ai_v14/ })).toBeNull();
  });

  it("links tab explains preview sessions on live deployments and offers the test batch URL", async () => {
    const combos = [
      {
        manifest_id: "tp_demo_nl_v3",
        arm: "high",
        study_key: "investigators_time_pressure",
        material_status: "reference",
        test_group: "comparison",
        consent_current: false,
        current: false,
        summary: ["360-second task"],
      },
    ];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes("/preview-combos"))
        return Promise.resolve(
          jsonResponse(200, { combos, timing_modes: ["faithful"] }),
        );
      if (url.includes("/batches"))
        return Promise.resolve(
          jsonResponse(200, {
            batches: [
              {
                batch_id: "connect.smoke.v11",
                manifest_id: "tp_review_first_v11",
                mode: "live",
                enrollment_open: true,
              },
              {
                batch_id: "prolific.live.n5.v11",
                manifest_id: "tp_review_first_v11",
                mode: "live",
                enrollment_open: true,
              },
            ],
            groups: [
              {
                group_id: "connect.smoke.4cell",
                mode: "live",
                enrollment_open: true,
                allocated: 0,
                cells: [],
              },
              {
                group_id: "connect.live.4cell",
                mode: "live",
                enrollment_open: true,
                allocated: 0,
                cells: [],
              },
            ],
          }),
        );
      if (url.includes("/previews/mine"))
        return Promise.resolve(jsonResponse(200, { links: [] }));
      return Promise.resolve(jsonResponse(200, { sessions: [] }));
    });
    render(<AdminApp />);
    // Live deployment: minting works (preview-mode test sessions), the notice says
    // so up front, and the dedicated test batch — never the fielded one — is offered.
    await screen.findByText(/preview-mode test sessions/);
    const mintBtn = await screen.findByRole("button", {
      name: /variant high/,
    });
    expect((mintBtn as HTMLButtonElement).disabled).toBe(false);
    expect(await screen.findByText("connect.smoke.v11")).toBeTruthy();
    expect(screen.queryByText("prolific.live.n5.v11")).toBeNull();
    // Smoke batch groups are offered too; the fielded group link never is.
    expect(await screen.findByText("connect.smoke.4cell")).toBeTruthy();
    expect(screen.queryByText("connect.live.4cell")).toBeNull();
    fireEvent.click(mintBtn);
    expect(
      fetchMock.mock.calls.some((c) => {
        const init = c[1] as RequestInit | undefined;
        return init?.method === "POST" && String(c[0]).includes("/previews");
      }),
    ).toBe(true);
  });

  it("sessions tab column headings filter categories client-side", async () => {
    const mk = (id: string, batch: string, stage: string, outcome: string | null) => ({
      session_id: id,
      batch_id: batch,
      created_at: "2026-10-10T14:00:00Z",
      mode: "live",
      stage,
      terminal_reason: stage === "abandoned" ? "heartbeat_timeout" : null,
      n_answered_crates: 4,
      n_reconnects: 0,
      n_rejected_commands: 0,
      n_open_episodes: 0,
      completion_outcome: outcome,
    });
    const sessions = [
      mk("aaaaaaaa-0000-4000-8000-000000000001", "study.live.v8.ai", "completion", "completed"),
      mk("bbbbbbbb-0000-4000-8000-000000000002", "study.live.v8.scripted", "abandoned", "abandoned"),
      mk("cccccccc-0000-4000-8000-000000000003", "study.live.v8.scripted", "completion", "completed"),
    ];
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/whoami"))
        return Promise.resolve(
          jsonResponse(200, {
            role: "researcher",
            actor: "a",
            expires_at: 1,
            app_mode: "live",
          }),
        );
      if (url.includes("/admin/sessions"))
        return Promise.resolve(jsonResponse(200, { sessions }));
      return Promise.resolve(jsonResponse(200, { sessions }));
    });
    render(<AdminApp />);
    fireEvent.click(await screen.findByText("sessions"));
    // three rows land
    await screen.findByText("aaaaaaaa");
    const table = document.querySelector(".admin-table")!;
    // open the "stage" column filter and hide abandoned
    fireEvent.click(within(table as HTMLElement).getByRole("button", { name: /^stage/ }));
    const abandonedBox = within(table as HTMLElement).getByLabelText("abandoned");
    fireEvent.click(abandonedBox);
    await screen.findByText(/2 of 3 rows shown/);
    expect(screen.queryByText("bbbbbbbb")).toBeNull();
    expect(screen.getByText("aaaaaaaa")).toBeTruthy();
    expect(screen.getByText("cccccccc")).toBeTruthy();
    // batch filter narrows to one member batch
    fireEvent.click(within(table as HTMLElement).getByRole("button", { name: /^batch/ }));
    fireEvent.click(within(table as HTMLElement).getByLabelText("study.live.v8.scripted"));
    await screen.findByText(/1 of 3 rows shown/);
    expect(screen.getByText("aaaaaaaa")).toBeTruthy();
    expect(screen.queryByText("cccccccc")).toBeNull();
    // re-checking the value restores the row
    fireEvent.click(within(table as HTMLElement).getByLabelText("study.live.v8.scripted"));
    await screen.findByText("cccccccc");
  });
});

describe("Walkthrough", () => {
  const replay = {
    session: { mode: "preview", timing_mode: "faithful" },
    participant: {
      events: [
        {
          seq: 1,
          type: "session_opened",
          actor: "system",
          stage: "consent",
          active_ms: null,
          server_time: "2026-10-03T17:00:00Z",
          payload: { mode: "preview", timing_mode: "faithful" },
        },
        {
          seq: 2,
          type: "assigned",
          actor: "system",
          stage: "consent",
          active_ms: 0,
          server_time: "2026-10-03T17:00:05Z",
          payload: { arm: "eval" },
        },
        {
          seq: 3,
          type: "message_accepted",
          actor: "participant",
          stage: "task",
          active_ms: 61000,
          server_time: "2026-10-03T17:05:00Z",
          payload: { channel: "team", body: "looking at crate 1" },
        },
        {
          seq: 4,
          type: "answer_updated",
          actor: "participant",
          stage: "task",
          active_ms: 120000,
          server_time: "2026-10-03T17:06:00Z",
          payload: { crate: 1, revision: 1, location_id: "The Sawmill" },
        },
        {
          seq: 5,
          type: "checkin_reveal",
          actor: "system",
          stage: "checkin_reveal",
          active_ms: 300000,
          server_time: "2026-10-03T17:09:00Z",
          payload: { own_rating: 4, assigned_values: { JT: 6, PM: 7 } },
        },
        {
          seq: 6,
          type: "telemetry_render",
          actor: "participant",
          stage: "task",
          active_ms: 61000,
          server_time: "2026-10-03T17:05:01Z",
          payload: { data: { channel: "team", message_id: "m1" } },
        },
      ],
      truncated: false,
    },
    restricted: {
      internal_events: [
        {
          seq: 7,
          type: "generation_resolved",
          actor: "scheduler",
          stage: "task",
          active_ms: 62000,
          server_time: "2026-10-03T17:05:02Z",
          payload: {
            status: "ok",
            fallback: "none",
            plan: { mechanism: "model", scheduled_ms: 4200 },
          },
        },
      ],
      model_calls: [
        {
          requested_at: "2026-10-03T17:05:02Z",
          requested_active_ms: 62000,
          status: "ok",
          provider: "local_openai_chat",
          model: "gemma4:26b@sha256:abc",
          duration_ms: 1200,
          attempt: 1,
          validation: {},
        },
      ],
      command_receipts: [
        {
          kind: "pressure_response",
          outcome: "rejected",
          reason: "stale_revision",
          expected_revision: 64,
          committed_revision: 65,
          received_at: "2026-10-03T17:08:58Z",
        },
        {
          kind: "pressure_response",
          outcome: "accepted",
          reason: null,
          expected_revision: 65,
          committed_revision: 66,
          received_at: "2026-10-03T17:09:02Z",
        },
      ],
    },
  };

  it("renders a merged timeline and hides telemetry by default", () => {
    render(<Walkthrough replay={replay} />);
    expect(screen.getByText("Session opened")).toBeTruthy();
    expect(screen.getByText(/arm eval/)).toBeTruthy();
    expect(screen.getByText(/looking at crate 1/)).toBeTruthy();
    expect(screen.getByText(/The Sawmill/)).toBeTruthy();
    expect(screen.getByText(/own 4 · JT 6 · PM 7/)).toBeTruthy();
    // agent internals on by default: generation + model call rows
    expect(screen.getByText("Generation ok")).toBeTruthy();
    expect(screen.getByText("Model call — ok")).toBeTruthy();
    // telemetry hidden until opted in
    expect(screen.queryByText("telemetry render")).toBeNull();
    fireEvent.click(screen.getByLabelText(/telemetry \(1\)/));
    expect(screen.getByText("telemetry render")).toBeTruthy();
    // command ledger rows show rejections that leave no events
    expect(screen.getByText(/pressure_response → rejected/)).toBeTruthy();
    expect(
      screen.getByText(/stale_revision · expected rev 64, server at 65/),
    ).toBeTruthy();
    expect(screen.getByText(/pressure_response → accepted/)).toBeTruthy();
    // category filter narrows the feed
    fireEvent.click(screen.getByText("answers 1"));
    expect(screen.queryByText("Session opened")).toBeNull();
    expect(screen.getByText(/The Sawmill/)).toBeTruthy();
  });

  const simReplay = {
    session: { mode: "preview", timing_mode: "faithful", session_id: "abcd1234-…" },
    participant: {
      events: [
        {
          seq: 1,
          type: "task_clock_started",
          actor: "system",
          stage: "task",
          active_ms: 0,
          server_time: "2026-10-03T17:00:00Z",
          payload: { stage: "task" },
        },
        {
          seq: 2,
          type: "assistant_requested",
          actor: "participant",
          stage: "task",
          active_ms: 9900,
          server_time: "2026-10-03T17:00:09Z",
          payload: { body: "where is crate 1?" },
        },
        {
          seq: 3,
          type: "message_accepted",
          actor: "participant",
          stage: "task",
          active_ms: 10000,
          server_time: "2026-10-03T17:00:10Z",
          payload: { channel: "assistant", body: "where is crate 1?", message_id: "a0" },
        },
        {
          seq: 4,
          type: "message_delivered",
          actor: "assistant",
          stage: "task",
          active_ms: 14000,
          server_time: "2026-10-03T17:00:14Z",
          payload: { channel: "assistant", body: "Crate 1 holds laptops.", message_id: "a1" },
        },
        {
          seq: 5,
          type: "message_delivered",
          actor: "JT",
          stage: "task",
          active_ms: 30000,
          server_time: "2026-10-03T17:00:30Z",
          payload: { channel: "team", body: "found this — sharing", message_id: "m1", record_id: "r9" },
        },
        {
          seq: 6,
          type: "checkin_open",
          actor: "system",
          stage: "checkin_collect",
          active_ms: 60000,
          server_time: "2026-10-03T17:01:00Z",
          payload: {
            at_active_ms: 60000,
            assigned_values: { JT: 6, PM: 7 },
            copy: { question: "How much time pressure do you feel right now?" },
          },
        },
        {
          seq: 7,
          type: "checkin_response",
          actor: "participant",
          stage: "checkin_wait",
          active_ms: 60000,
          server_time: "2026-10-03T17:01:12Z",
          payload: { exposure_id: "x", skipped: false },
        },
        {
          seq: 8,
          type: "checkin_reveal",
          actor: "system",
          stage: "checkin_reveal",
          active_ms: 60000,
          server_time: "2026-10-03T17:01:16Z",
          payload: { own_rating: 4, assigned_values: { JT: 6, PM: 7 } },
        },
        {
          seq: 9,
          type: "stage_proceeded",
          actor: "participant",
          stage: "task",
          active_ms: 60000,
          server_time: "2026-10-03T17:01:20Z",
          payload: { to: "task" },
        },
      ],
      truncated: false,
    },
    restricted: {
      records: [
        { record_id: "r9", crate: 2, stamp: "14:02", body: "shipping ledger" },
      ],
      file_membership: [{ record_id: "r9", source: "added_from_team" }],
      world: [{ content: { assigned: [1, 2, 3, 4] } }],
      internal_events: [],
      model_calls: [],
      command_receipts: [
        {
          kind: "pressure_response",
          outcome: "rejected",
          reason: "stale_revision",
          expected_revision: 64,
          committed_revision: 65,
          received_at: "2026-10-03T17:01:11Z",
        },
      ],
    },
  };

  function bootSimulation(replay: unknown) {
    const html = buildSimulationHtml(replay as Record<string, unknown>);
    const dom = new JSDOM(html, { runScripts: "dangerously" });
    return trackWindow(dom.window);
  }
  function simSeek(w: ReturnType<typeof bootSimulation>, n: number) {
    const s = w.document.querySelector("#scrub")!;
    (s as HTMLInputElement).value = String(n);
    s.dispatchEvent(new w.Event("input"));
  }

  it("generates a self-contained HTML session simulation on demand", () => {
    const html = buildSimulationHtml(simReplay as Record<string, unknown>);
    // one file: payload + player + chrome, no external fetches
    expect(html).toContain("<!doctype html>");
    expect(html).toContain("const SIM=");
    expect(html).toContain('"checkin_open"');
    expect(html).toContain("Case file");
    expect(html).toContain("Team chat");
    expect(html).toContain("AI assistant");
    expect(html).toContain("internals");
    // no external resources — it must work as a saved file
    expect(html).not.toMatch(/src="http|href="http|fetch\(/);
    // embedded JSON cannot break out of the script tag
    expect(html).not.toContain("</script><script");
  });

  it("plays back messages, file cards, and the check-in overlay chronologically", () => {
    const w = bootSimulation(simReplay);
    const d = w.document;
    // scrub just past the teammate share: file card appears tagged from team
    simSeek(w, 5);
    const fileCard = d.querySelector("#fileList .file");
    expect(fileCard?.textContent).toContain("shipping ledger");
    expect(fileCard?.className).toContain("from-team");
    expect(d.querySelector("#chatList")?.textContent).toContain("found this");
    expect(d.querySelector("#assistList")?.textContent).toContain("where is crate 1?");
    expect(d.querySelector("#assistList")?.textContent).toContain("Crate 1 holds laptops.");
    // the assistant_requested bookkeeping event must not double the ask bubble
    const mineBubbles = [
      ...d.querySelectorAll("#assistList .msg"),
    ].filter((el) => el.textContent?.includes("where is crate 1?"));
    expect(mineBubbles.length).toBe(1);
    // check-in opens with the copy from checkin_open
    simSeek(w, 6);
    expect(d.querySelector("#overlay")?.classList.contains("open")).toBe(true);
    expect(d.querySelector("#overlayBox")?.textContent).toContain("time pressure");
    // wait phase keeps the copy visible through response (the AB-24 fix path)
    simSeek(w, 7);
    expect(d.querySelector("#overlay")?.classList.contains("open")).toBe(true);
    // reveal shows all three ratings
    simSeek(w, 8);
    const reveal = d.querySelector("#overlayBox")?.textContent ?? "";
    expect(reveal).toContain("JT");
    expect(reveal).toContain("PM");
    // returning to task closes the overlay
    simSeek(w, 9);
    expect(d.querySelector("#overlay")?.classList.contains("open")).toBe(false);
    w.close();
  });

  it("renders the researcher internals rail including command receipts", () => {
    const w = bootSimulation(simReplay);
    const d = w.document;
    d.querySelector<HTMLButtonElement>("#internalsBtn")!.click();
    expect(d.querySelector("#internals")?.classList.contains("open")).toBe(true);
    simSeek(w, 9);
    const rail = d.querySelector("#internals")?.textContent ?? "";
    expect(rail).toContain("command pressure_response → rejected");
    expect(rail).toContain("stale_revision");
    w.close();
  });

  it("renders the generate button and downloads via an object URL", () => {
    const createUrl = vi.fn(() => "blob:sim-test");
    const revokeUrl = vi.fn();
    const priorCreateObjectURL = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const priorRevokeObjectURL = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    vi.stubGlobal("URL", { ...URL, createObjectURL: createUrl, revokeObjectURL: revokeUrl });
    afterEachRestores.push(() => {
      restoreDescriptor(URL, "createObjectURL", priorCreateObjectURL);
      restoreDescriptor(URL, "revokeObjectURL", priorRevokeObjectURL);
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    afterEachRestores.push(() => click.mockRestore());
    render(<Walkthrough replay={replay} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate HTML simulation" }));
    expect(createUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(revokeUrl).toHaveBeenCalledOnce();
  });
});

describe("participant view", () => {
  const pvReplay = {
    session: {
      session_id: "abc12345-0000-0000-0000-000000000000",
      mode: "live",
      manifest_id: "tp_ai_v3",
      stage: "completion",
    },
    participant: {
      events: [
        {
          seq: 1,
          type: "message_accepted",
          actor: "participant",
          stage: "task",
          payload: { channel: "team", body: "any intel on crate 3?", message_id: "m1" },
        },
        {
          seq: 2,
          type: "message_accepted",
          actor: "JT",
          stage: "task",
          payload: {
            channel: "team",
            body: "found this",
            record_id: "r9",
            message_id: "m2",
          },
        },
      ],
      pages: {
        stage_order: [
          "entry",
          "consent",
          "instructions",
          "training",
          "scripted_connecting",
          "task",
          "checkin_collect",
          "checkin_reveal",
          "answers_locked",
          "confidence",
          "post_questionnaire",
          "debrief",
          "data_use_choice",
          "completion",
        ],
        entered_at: { consent: "2026-10-08T22:00:00Z" },
        initials: "AB",
        materials: {
          consent: {
            artifact_key: "consent.reference@v5",
            status: "approved",
            body: {
              text: {
                heading: "Study Consent Form",
                subheading: "Please read",
                sections: [{ title: "About", paragraphs: ["consent copy para"] }],
                question: "Participate?",
                accept_label: "Yes, I consent",
                decline_label: "No thanks",
              },
            },
          },
          training: null,
          debrief: null,
        },
        instruments: {
          confidence: {
            stage: "confidence",
            instrument_id: "confidence.demo",
            applies_to: "per_answered_crate",
            response: {
              type: "slider",
              min: 0,
              max: 100,
              min_label: "0% Completely unsure",
              max_label: "100% Positive",
            },
            items: [
              {
                item_id: "confidence",
                text: "Confidence for this crate",
                skippable: false,
                options: null,
              },
            ],
            pending_crates: [1],
            crate_responses: { "1": { value: 75, status: "answered" } },
          },
          post_questionnaire: {
            stage: "post_questionnaire",
            instrument_id: "post_task.tp_demo",
            applies_to: "session",
            response: {
              type: "likert",
              min: 1,
              max: 7,
              min_label: "Strongly disagree",
              max_label: "Strongly agree",
            },
            items: [
              {
                item_id: "tp_post_01",
                text: "I trusted my teammates.",
                skippable: false,
                options: null,
              },
              {
                item_id: "tp_post_10",
                text: "Which best describes them?",
                skippable: false,
                options: ["All were automated", "All were human"],
              },
            ],
            responses: {
              tp_post_01: { value: 5, status: "answered" },
              tp_post_10: { value: "All were automated", status: "answered" },
            },
          },
        },
        consent: { action: "consented", text_version: "consent.reference@v5" },
        checkin: {
          copy: {
            question: "How much time pressure do you feel?",
            scale_min_label: "1 — No time pressure",
            scale_max_label: "7 — A great deal",
            note: "Visible to teammates.",
            collecting: "Collecting…",
            reveal_title: "Time pressure reported",
            reveal_note: "Stays visible.",
            resume_label: "Resume task",
          },
          own_rating: 5,
          assigned_values: { JT: 7, PM: 6 },
          status: "rendered",
        },
        frozen_answers: { "1": "The Sawmill" },
        data_use: { choice: "use_all" },
        completion: {
          outcome: "completed",
          code: "PLACEHOLDER-CODE",
          url: null,
          platform: "connect",
        },
        terminal: null,
        structured_requests: false,
        solo_review_seconds: null,
        workbench: {
          assigned_crates: [1, 2, 3, 4],
          initial_file: ["r1"],
          records: {
            r1: { stamp: "14:00", body: "initial intel card", crate: 1 },
            r9: { stamp: "14:02", body: "teammate intel card", crate: 2 },
          },
        },
      },
    },
    restricted: null,
  };

  it("renders every participant page in order with recorded responses", () => {
    const html = buildParticipantViewHtml(pvReplay as Record<string, unknown>);
    const w = trackWindow(new JSDOM(html, { runScripts: "dangerously" }).window);
    const d = w.document;
    const next = () => d.querySelector<HTMLButtonElement>("#nextBtn")!.click();
    const pageText = () => d.querySelector("#pageHost")!.textContent ?? "";
    const label = () => d.querySelector(".pstage")!.textContent ?? "";

    // one page at a time, starting at Entry
    expect(d.body.textContent).toContain("PARTICIPANT VIEW");
    expect(label()).toBe("Entry");
    expect(d.querySelector(".pnum")?.textContent).toContain("Page 1 of");

    // click through like the participant: collect the page labels in order
    const labels: string[] = [label()];
    const texts: string[] = [pageText()];
    while (!d.querySelector<HTMLButtonElement>("#nextBtn")!.disabled) {
      next();
      labels.push(label());
      texts.push(pageText());
    }
    const all = texts.join("\n");

    expect(labels).toEqual([
      "Entry",
      "Consent",
      "Orientation (page 1 of 2)",
      "Orientation (page 2 of 2)",
      "Practice",
      "Connecting",
      "Team task",
      "Team check-in",
      "Answers locked",
      "Confidence ratings",
      "A few questions",
      "Debrief",
      "Questions or concerns?",
      "Completion",
    ]);

    // consent: bound copy + chosen action highlighted
    expect(all).toContain("Study Consent Form");
    expect(all).toContain("consent copy para");
    expect(all).toContain("Participant chose");
    expect(all).toContain("consented");
    // orientation records the initials
    expect(all).toContain("AB");
    // workbench snapshot: file + chat transcript + frozen answer
    expect(all).toContain("initial intel card");
    expect(all).toContain("teammate intel card");
    expect(all).toContain("any intel on crate 3?");
    expect(all).toContain("found this");
    expect(all).toContain("The Sawmill");
    // check-in: question + own rating + revealed teammate values
    expect(all).toContain("How much time pressure do you feel?");
    expect(all).toContain("Time pressure reported");
    expect(all).toContain("JT: 7");
    expect(all).toContain("Participant rated: 5");
    // confidence: per-crate slider with the recorded value
    expect(all).toContain("Confidence: 75%");
    // questionnaire: likert item + categorical option marked
    expect(all).toContain("I trusted my teammates.");
    expect(all).toContain("All were automated");
    // debrief + completion
    expect(all).toContain("XXXX-XXXX");
    expect(all).toContain("PLACEHOLDER-CODE");
    expect(all).toContain("Return to Connect");

    w.close();
  });

  it("navigates back and jumps between pages", () => {
    const html = buildParticipantViewHtml(pvReplay as Record<string, unknown>);
    const w = trackWindow(new JSDOM(html, { runScripts: "dangerously" }).window);
    const d = w.document;
    const label = () => d.querySelector(".pstage")!.textContent ?? "";
    // jump straight to Completion via the numbered dots, then Back once
    const jumps = d.querySelectorAll(".jumps button");
    (jumps[jumps.length - 1] as HTMLButtonElement).click();
    expect(label()).toBe("Completion");
    d.querySelector<HTMLButtonElement>("#backBtn")!.click();
    expect(label()).toBe("Questions or concerns?");
    w.close();
  });

  it("carries no researcher internals", () => {
    const html = buildParticipantViewHtml(pvReplay as Record<string, unknown>);
    expect(html).not.toContain('id="internals"');
    expect(html).not.toContain("command_receipts");
    expect(html).not.toContain("model_call");
    // self-contained — no external fetches
    expect(html).not.toMatch(/src="http|fetch\(/);
    // embedded JSON cannot break out of the script tag
    expect(html).not.toContain("</script><script");
  });

  // 2026-10-15 crate-condition experiment (audit F02/F03/F05): replay copy and
  // merchandise labels must follow the session's persisted task_shape /
  // crate_contents, not the legacy eight-crate defaults.
  type MutableReplay = {
    participant: {
      pages: {
        workbench: { assigned_crates: number[] };
        instruments: { confidence: { pending_crates: number[] } };
        task_shape?: unknown;
        crate_contents?: Record<string, string>;
        [k: string]: unknown;
      };
    };
  };

  it.each([
    {
      name: "none / 12 crates",
      shape: { n_crates: 12, n_assigned: 4, crate_overlap: "none" },
      expectCopy: [
        "Twelve crates of high-value merchandise",
        "assigned four of the twelve crates",
        "no overlaps",
        "Each of you is working on your own four crates.",
      ],
      absent: ["will not be told which crates", "some overlap"],
    },
    {
      name: "some / 8 crates",
      shape: { n_crates: 8, n_assigned: 4, crate_overlap: "some" },
      expectCopy: [
        "Eight crates of high-value merchandise",
        "some overlap",
        "Your four-crate assignments overlap.",
      ],
      absent: ["will not be told which crates", "no overlaps"],
    },
    {
      name: "full / 4 crates",
      shape: { n_crates: 4, n_assigned: 4, crate_overlap: "full" },
      expectCopy: [
        "Four crates of high-value merchandise",
        "assigned to work on all four crates",
        "Everyone works on the same four crates.",
        "All three of you are working on the same four crates.",
      ],
      absent: [
        "will not be told which crates",
        "crates outside your assignment",
        "privately assigned",
      ],
    },
    {
      name: "independent legacy / 8 crates",
      shape: { n_crates: 8, n_assigned: 4, crate_overlap: "independent" },
      expectCopy: [
        "Eight crates of high-value merchandise",
        "privately assigned four of the eight crates",
        "will not be told which crates",
      ],
      absent: ["no overlaps", "same four crates"],
    },
  ])("renders session-shape copy — $name", ({ shape, expectCopy, absent }) => {
    const replay = JSON.parse(JSON.stringify(pvReplay)) as MutableReplay;
    replay.participant.pages.task_shape = shape;
    const html = buildParticipantViewHtml(replay);
    for (const text of expectCopy) expect(html).toContain(text);
    for (const text of absent) expect(html).not.toContain(text);
  });

  it("labels crates from persisted crate_contents including 9-12", () => {
    const replay = JSON.parse(JSON.stringify(pvReplay)) as MutableReplay;
    const pages = replay.participant.pages;
    pages.task_shape = { n_crates: 12, n_assigned: 4, crate_overlap: "none" };
    pages.crate_contents = {
      "9": "sneakers",
      "10": "drones",
      "11": "e-bikes",
      "12": "VR headsets",
    };
    pages.workbench.assigned_crates = [9, 10, 11, 12];
    pages.instruments.confidence.pending_crates = [9, 10, 11, 12];
    const html = buildParticipantViewHtml(replay);
    expect(html).toContain("Crate 9 — sneakers");
    expect(html).toContain("Crate 10 — drones");
    expect(html).toContain("Crate 11 — e-bikes");
    expect(html).toContain("Crate 12 — VR headsets");
  });
});
