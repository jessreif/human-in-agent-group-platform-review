/**
 * Study Admin Dashboard behavior tests (WP17 audit remediation, AG-01):
 * URL-synchronized filter scope, UTC date normalization, AbortController
 * fencing on filter change, auth-loss propagation, scoped export POSTs with
 * resolved-count echo, and safe rendering of empty/zero envelopes (DA-01).
 * fetch is mocked — no network.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "../src/admin/dashboard";

type FetchMock = ReturnType<typeof vi.fn>;

let fetchMock: FetchMock;
let cookieSnapshot = "";

// Checked after spyOn restore. A raw URL assignment would not show up here.
const originalCreateObjectURL = Object.getOwnPropertyDescriptor(
  URL,
  "createObjectURL",
);
const originalRevokeObjectURL = Object.getOwnPropertyDescriptor(
  URL,
  "revokeObjectURL",
);

function expireCookie(name: string): void {
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
}

function restoreDocumentCookie(snapshot: string): void {
  const names = new Set<string>();
  for (const jar of [document.cookie, snapshot]) {
    for (const part of jar.split(";")) {
      const name = part.split("=")[0]?.trim();
      if (name) names.add(name);
    }
  }
  for (const name of names) expireCookie(name);
  for (const part of snapshot.split(";")) {
    const trimmed = part.trim();
    if (trimmed) document.cookie = `${trimmed}; path=/`;
  }
}

function jsonResponse(status: number, body: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const EMPTY_OVERVIEW = {
  schema_version: "dashboard.a1",
  codebook_version: "cb-test",
  snapshot_time: "2026-10-06T00:00:00Z",
  cohort: { selected: 0, eligible_research: 0, tombstoned_excluded: 0, excluded: {} },
  mode_counts: {},
  outcomes: {},
  recent_sessions: [],
};

const EMPTY_READINESS = {
  schema_version: "dashboard.a1",
  sections: { status: "ok" },
  audit: { scope: "completers", total: 0, rows: [] },
  summary: {},
  coverage: {},
};

beforeEach(() => {
  cookieSnapshot = document.cookie;
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  window.history.replaceState(null, "", "/investigators/admin/");
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  restoreDocumentCookie(cookieSnapshot);
  expect(Object.getOwnPropertyDescriptor(URL, "createObjectURL")).toEqual(
    originalCreateObjectURL,
  );
  expect(Object.getOwnPropertyDescriptor(URL, "revokeObjectURL")).toEqual(
    originalRevokeObjectURL,
  );
});

function urls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe("Dashboard filter scope + URL sync (DA-21/24)", () => {
  it("writes the confirmed scope into the URL and restores it", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(200, EMPTY_OVERVIEW)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(urls().some((u) => u.includes("/dashboard/overview"))).toBe(true),
    );

    const modeSel = screen.getByLabelText(/mode/i, { selector: "select" });
    fireEvent.change(modeSel, { target: { value: "qa" } });
    await waitFor(() =>
      expect(window.location.search).toContain("mode=qa"),
    );
    await waitFor(() =>
      expect(urls().some((u) => u.includes("mode=qa"))).toBe(true),
    );
  });

  it("restores scope from a deep-linked URL", async () => {
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?mode=qa&batch_id=lab-1&dsec=readiness",
    );
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/readiness")
        ? jsonResponse(200, EMPTY_READINESS)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(
        urls().some(
          (u) => u.includes("mode=qa") && u.includes("batch_id=lab-1"),
        ),
      ).toBe(true),
    );
  });

  it("normalizes datetime-local wall time as UTC, never local zone", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(200, EMPTY_OVERVIEW)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() => expect(urls().length).toBeGreaterThan(0));

    const since = screen.getByLabelText(/since/i, { selector: "input" });
    fireEvent.change(since, { target: { value: "2026-10-01T12:30" } });
    await waitFor(() =>
      expect(
        urls().some((u) => u.includes("since=2026-10-01T12%3A30%3A00.000Z")),
      ).toBe(true),
    );
  });
});

describe("Assistant generation partitions — build currentness", () => {
  it("floats current-build rows first and marks superseded alerts as historical", async () => {
    // model_call rows are immutable history: a superseded build's recorded
    // incident must render labelled, never as a live failure.
    const overview = {
      ...EMPTY_OVERVIEW,
      assistant_generation: {
        partitions: [
          {
            profile: "tp_ai_v3", arm: "high", cohort: "live",
            build: "a".repeat(64), current: false,
            model: "gemma4:26b", intended_provider: 5, intentional_deterministic: 0,
            call_statuses: { validation_rejected: 5 },
            generated_delivered: 0, delivered: 5, rendered: 5,
            acceptance: 0, alerts: ["assistant_generation_degraded"],
          },
          {
            profile: "tp_ai_v6", arm: "high", cohort: "live",
            build: "b".repeat(64), current: true,
            model: "gemma4:26b", intended_provider: 2, intentional_deterministic: 0,
            call_statuses: { succeeded: 2 },
            generated_delivered: 2, delivered: 2, rendered: 2,
            acceptance: 1, alerts: [],
          },
        ],
      },
    };
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(200, overview)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    const table = (await screen.findByText(/immutable history/)).closest("table")!;
    const rows = within(table).getAllByRole("row");
    const currentRow = rows[1]!.textContent ?? "";
    const supersededRow = rows[2]!.textContent ?? "";
    expect(currentRow).toContain("tp_ai_v6");
    expect(currentRow).toContain("current");
    expect(currentRow).toContain("b".repeat(8));
    expect(supersededRow).toContain("tp_ai_v3");
    expect(supersededRow).toContain("superseded");
    expect(supersededRow).toContain("assistant_generation_degraded (historical)");
  });
});

describe("Dashboard history navigation (DA-24)", () => {
  it("section clicks push history; Back restores the section without pushing", async () => {
    window.history.replaceState(null, "", "/investigators/admin/");
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(200, EMPTY_OVERVIEW)
        : jsonResponse(200, {}),
    );
    const pushSpy = vi.spyOn(window.history, "pushState");
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(urls().some((u) => u.includes("/dashboard/overview"))).toBe(true),
    );

    // Section pick → navigable history entry.
    fireEvent.click(screen.getByRole("button", { name: "Page Timing" }));
    await waitFor(() =>
      expect(window.location.search).toContain("dsec=timing"),
    );
    expect(pushSpy).toHaveBeenCalled();
    pushSpy.mockClear();

    // Simulate the browser popping to the previous entry, then firing
    // popstate — the handler must restore state without pushing a duplicate
    // (a push here would clobber the forward stack).
    window.history.replaceState(null, "", "/investigators/admin/?dsec=overview");
    fireEvent(window, new PopStateEvent("popstate"));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Dashboard" }).className,
      ).toContain("active"),
    );
    expect(pushSpy).not.toHaveBeenCalled();
  });
});

describe("Dashboard lifecycle (AG-03, DA-01)", () => {
  it("a 401 mid-session propagates auth loss instead of showing stale data", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(401, { error: "unauthenticated" })
        : jsonResponse(200, {}),
    );
    const onAuthLoss = vi.fn();
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={onAuthLoss} />);
    await waitFor(() => expect(onAuthLoss).toHaveBeenCalled());
  });

  it("renders an empty cohort without crashing (zero counts, empty maps)", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/overview")
        ? jsonResponse(200, EMPTY_OVERVIEW)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(screen.getAllByText(/eligible/i).length).toBeGreaterThan(0),
    );
    // zero is rendered as a number, not swallowed as missing
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("aborts the in-flight request when the filter generation changes", async () => {
    const signals: AbortSignal[] = [];
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/dashboard/overview")) {
        if (init?.signal) signals.push(init.signal);
        return new Promise<Response>(() => {}); // never resolves
      }
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() => expect(signals.length).toBe(1));

    const modeSel = screen.getByLabelText(/mode/i, { selector: "select" });
    fireEvent.change(modeSel, { target: { value: "qa" } });
    await waitFor(() => expect(signals.length).toBe(2));
    expect(signals[0]!.aborted).toBe(true);
  });
});

describe("Per-table CSV downloads (PCS-style grid)", () => {
  let createObjectURLSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  });

  const tablesResponse = () =>
    jsonResponse(200, {
      tables: ["participant_wide", "session", "message", "fidelity_rate"],
    });

  it("renders curated cards with downloads and no publish controls", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=exports");
    fetchMock.mockImplementation((url: string) =>
      url.includes("/admin/exports/tables")
        ? tablesResponse()
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText("Participants");
    expect(screen.getByText("Team chat")).toBeTruthy();
    expect(screen.queryByText(/Publish/i)).toBeNull();
    // Unlisted curated names still reachable via the "Other tables" select.
    const sel = await screen.findByLabelText(/Other export table/i, {
      selector: "select",
    });
    await waitFor(() =>
      expect(
        Array.from(sel.querySelectorAll("option")).some(
          (o) => o.textContent === "fidelity_rate",
        ),
      ).toBe(true),
    );
  });

  it("downloads the CSV for the active filter scope", async () => {
    // Admin export reads inv_admin_csrf. The participant inv_csrf cookie is not echoed.
    document.cookie = "inv_csrf=participant-token; path=/";
    document.cookie = "inv_admin_csrf=csrf-export-test; path=/";
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?mode=qa&batch_id=lab-9&dsec=exports",
    );
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith("/admin/exports/table")) {
        return Promise.resolve(
          new Response("session_id\nabc\n", {
            status: 200,
            headers: {
              "Content-Type": "text/csv",
              "Content-Disposition":
                'attachment; filename="investigators_session_x.csv"',
            },
          }),
        );
      }
      if (url.includes("/admin/exports/tables")) return tablesResponse();
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    const card = await screen.findByText("Sessions");
    const btn = card.parentElement!.querySelector("button")!;
    fireEvent.click(btn);
    await screen.findByText(/Downloaded session\.csv/i);
    // CSRF-gated POST carrying the table + resolved filter scope in the body.
    const dl = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith("/admin/exports/table"),
    )!;
    const init = dl[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-export-test");
    expect(headers["x-csrf-token"]).toBeUndefined();
    for (const call of fetchMock.mock.calls) {
      const callInit = (call[1] ?? {}) as RequestInit;
      const method = (callInit.method ?? "GET").toUpperCase();
      if (method !== "GET" && method !== "HEAD") continue;
      const getHeaders = (callInit.headers ?? {}) as Record<string, string>;
      expect(getHeaders["X-CSRF-Token"]).toBeUndefined();
      expect(getHeaders["x-csrf-token"]).toBeUndefined();
    }
    const body = JSON.parse(String(init.body)) as {
      name: string;
      scope: Record<string, string>;
    };
    expect(body.name).toBe("session");
    expect(body.scope).toMatchObject({ mode: "qa", batch_id: "lab-9" });
  });

  it("does not send a CSRF token header when the admin cookie is absent", async () => {
    document.cookie = "inv_admin_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    document.cookie = "inv_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    window.history.replaceState(null, "", "/investigators/admin/?dsec=exports");
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith("/admin/exports/table")) {
        return Promise.resolve(
          new Response("session_id\nabc\n", {
            status: 200,
            headers: { "Content-Type": "text/csv" },
          }),
        );
      }
      if (url.includes("/admin/exports/tables")) return tablesResponse();
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    const card = await screen.findByText("Sessions");
    fireEvent.click(card.parentElement!.querySelector("button")!);
    await screen.findByText(/Downloaded session\.csv/i);
    const dl = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith("/admin/exports/table"),
    )!;
    const headers = ((dl[1] as RequestInit).headers ?? {}) as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
    expect(headers["X-CSRF-Token"]).not.toBe("");
  });

  it("surfaces a scope refusal as an error, not a download", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=exports");
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith("/admin/exports/table")) {
        return jsonResponse(422, {
          error: "empty_scope",
          detail: "the filter scope resolves to zero sessions",
        });
      }
      if (url.includes("/admin/exports/tables")) return tablesResponse();
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    const card = await screen.findByText("Sessions");
    const btn = card.parentElement!.querySelector("button")!;
    fireEvent.click(btn);
    await screen.findByText(/zero sessions/i);
    expect(screen.queryByText(/Downloaded/i)).toBeNull();
    expect(createObjectURLSpy).not.toHaveBeenCalled();
  });

  it("a 403 does not trigger a download", async () => {
    document.cookie = "inv_admin_csrf=csrf-export-test; path=/";
    window.history.replaceState(null, "", "/investigators/admin/?dsec=exports");
    const onAuthLoss = vi.fn();
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith("/admin/exports/table")) {
        return jsonResponse(403, { error: "bad_csrf", detail: "csrf token mismatch" });
      }
      if (url.includes("/admin/exports/tables")) return tablesResponse();
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={onAuthLoss} />);
    const card = await screen.findByText("Sessions");
    fireEvent.click(card.parentElement!.querySelector("button")!);
    await waitFor(() => expect(onAuthLoss).toHaveBeenCalled());
    expect(screen.queryByText(/Downloaded/i)).toBeNull();
    expect(createObjectURLSpy).not.toHaveBeenCalled();
    const dl = fetchMock.mock.calls.find((c) =>
      String(c[0]).endsWith("/admin/exports/table"),
    )!;
    const init = dl[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "csrf-export-test",
    );
  });

  it("disables all download buttons while one is in flight", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=exports");
    fetchMock.mockImplementation((url: string) => {
      if (String(url).endsWith("/admin/exports/table")) {
        return new Promise<Response>(() => {}); // hang — never resolves
      }
      if (url.includes("/admin/exports/tables")) return tablesResponse();
      return jsonResponse(200, {});
    });
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    const card = await screen.findByText("Sessions");
    fireEvent.click(card.parentElement!.querySelector("button")!);
    await waitFor(() =>
      expect(
        screen
          .getAllByRole("button", { name: /Downloading…|Download CSV/i })
          .every((b) => (b as HTMLButtonElement).disabled),
      ).toBe(true),
    );
  });
});

describe("Readiness audit scope (DA-20)", () => {
  it("sends the selected audit_scope and repaginates", async () => {
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?mode=qa&dsec=readiness",
    );
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/readiness")
        ? jsonResponse(200, EMPTY_READINESS)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(urls().some((u) => u.includes("audit_scope=completers"))).toBe(true),
    );
    const sel = await screen.findByLabelText(/audit scope/i, {
      selector: "select",
    });
    fireEvent.change(sel, { target: { value: "incomplete" } });
    await waitFor(() =>
      expect(urls().some((u) => u.includes("audit_scope=incomplete"))).toBe(
        true,
      ),
    );
  });
});

describe("Dashboard cohort analysis (per-pool arm contrasts)", () => {
  const CELL = (n: number, mean: number) => ({
    n_sessions: n, n_missing: 0, n, mean, sd: 0.1, median: mean,
    q1: mean, q3: mean, min: mean, max: mean,
  });
  // BA-104: cohort_analysis is partition-first — pools never cross manifests;
  // each pool is single-mechanism and carries its own arm contrast.
  const ANALYSIS = {
    schema_version: "dashboard.analysis.a2",
    snapshot_time: "2026-10-10T18:00:00Z",
    cohort: { selected: 24, eligible_research: 24, analysis_id: "abc123" },
    sections: {},
    items: [],
    power_inputs: {
      status: "descriptive_only",
      rows: [],
      mde_scenarios: { status: "unsupported_model", gated_on: ["D12"] },
    },
    contrasts: [],
    cohort_analysis: {
      status: "descriptive_only",
      n_sessions: 24,
      pooling: {
        boundary: "compatibility_key",
        equivalences: [],
      },
      pools: [
        {
          pool_id: "ck:aaa",
          pool_kind: "compatibility_key",
          equivalence: null,
          n_sessions: 11,
          mechanisms: { ai: ["tp_ai_v7"] },
          compatibility_keys: ["ck1:aaa"],
          cells: { "ai|high": 6, "ai|low": 5 },
          partitions_pooled: ["p1"],
          measures: [
            {
              measure: "accuracy.correct_teamdep",
              unit: "proportion_of_assigned_team",
              overall: { n_sessions: 11, n: 11, mean: 0.0, sd: 0.05, median: 0, q1: 0, q3: 0, min: 0, max: 0.5 },
              cells: { "ai|high": CELL(6, 0), "ai|low": CELL(5, 0) },
              contrasts: {
                arm_high_minus_low: { diff: 0.0, pooled_sd: 0.05, cohen_d: 0.0 },
              },
            },
            {
              measure: "confidence.mean",
              unit: "confidence_0_100",
              overall: { n_sessions: 11, n: 10, mean: 55, sd: 12, median: 55, q1: 48, q3: 62, min: 20, max: 90 },
              cells: { "ai|high": CELL(6, 57), "ai|low": CELL(5, 52) },
              contrasts: {
                arm_high_minus_low: { diff: 5.0, pooled_sd: 11, cohen_d: 0.45 },
              },
            },
          ],
          correlations: [
            { a: "accuracy.correct_ownfile", b: "ai_use.prompts", n: 11, r: 0.64 },
            { a: "accuracy.correct_ownfile", b: "accuracy.correct_teamdep", n: 11, r: null },
          ],
        },
        {
          pool_id: "ck:bbb",
          pool_kind: "compatibility_key",
          equivalence: null,
          n_sessions: 13,
          mechanisms: { scripted: ["tp_scripted_v9"] },
          compatibility_keys: ["ck1:bbb"],
          cells: { "scripted|high": 7, "scripted|low": 6 },
          partitions_pooled: ["p2"],
          measures: [
            {
              measure: "accuracy.correct_teamdep",
              unit: "proportion_of_assigned_team",
              overall: { n_sessions: 13, n: 13, mean: 0.035, sd: 0.06, median: 0, q1: 0, q3: 0, min: 0, max: 0.5 },
              cells: { "scripted|high": CELL(7, 0.04), "scripted|low": CELL(6, 0.03) },
              contrasts: {
                arm_high_minus_low: { diff: 0.01, pooled_sd: 0.05, cohen_d: 0.2 },
              },
            },
            {
              measure: "confidence.mean",
              unit: "confidence_0_100",
              overall: { n_sessions: 13, n: 12, mean: 55, sd: 12, median: 55, q1: 48, q3: 62, min: 20, max: 90 },
              cells: { "scripted|high": CELL(7, 56), "scripted|low": CELL(6, 54) },
              contrasts: {
                arm_high_minus_low: { diff: 2.0, pooled_sd: 11, cohen_d: 0.18 },
              },
            },
          ],
          correlations: [],
        },
      ],
    },
    diagnostics: { n_sessions: 0 },
    findings: [
      { card_id: "cohort_coverage", cohort: "research_descriptive", n_eligible: 24, analysis_id: "abc123", text: "Eligible completers by arm: high=13, low=11." },
    ],
    notes: [],
  };

  const mockAnalysis = () =>
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/analysis")
        ? jsonResponse(200, ANALYSIS)
        : jsonResponse(200, {}),
    );

  it("Arm Contrasts renders per-pool arm cells and Δ columns", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=contrasts");
    mockAnalysis();
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText(/Between-subjects contrasts — arm difference per pool/);
    // each pool's cell columns render
    expect(screen.getAllByText("ai·high").length).toBeGreaterThan(0);
    expect(screen.getAllByText("scripted·high").length).toBeGreaterThan(0);
    // measure row + arm Δ column (headers name the contrast direction)
    expect(screen.getAllByText("accuracy.correct_teamdep").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Δ high−low").length).toBeGreaterThan(0);
    // mechanism contrasts are never produced — pools cannot cross manifests
    expect(screen.queryByText("Δ ai−scripted")).toBeNull();
    expect(screen.queryByText("interaction")).toBeNull();
    // formatted diff + cohen's d
    expect(screen.getAllByText("5 (d=0.45)").length).toBeGreaterThan(0);
  });

  it("Arm Contrasts hides the scripted pool when the arm flag is off", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=contrasts");
    const off = { ...ANALYSIS, scripted_teammates_enabled: false };
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/analysis")
        ? jsonResponse(200, off)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText(/Between-subjects contrasts — arm difference per pool/);
    // ai pool stays; the scripted pool is hidden whole — its sessions can
    // never contribute to a visible estimate (BA-104)
    expect(screen.getAllByText("ai·high").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ai·low").length).toBeGreaterThan(0);
    expect(screen.queryByText("scripted·high")).toBeNull();
    expect(screen.queryByText("scripted·low")).toBeNull();
    expect(screen.getAllByText("Δ high−low").length).toBeGreaterThan(0);
    expect(screen.getByText(/Scripted-teammate pools are hidden/)).toBeTruthy();
  });

  it("Item Analytics cell split hides scripted columns when the arm flag is off", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=items");
    const off = { ...ANALYSIS, scripted_teammates_enabled: false };
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/analysis")
        ? jsonResponse(200, off)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText(/Item descriptives — pooled across identical instruments/);
    expect(screen.getAllByText("ai·high").length).toBeGreaterThan(0);
    expect(screen.queryByText("scripted·high")).toBeNull();
    expect(screen.queryByText("scripted·low")).toBeNull();
  });

  it("Power Inputs renders pooled scalar descriptives and the correlation table", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=power");
    mockAnalysis();
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText(/Power inputs — pooled scalar descriptives/);
    // per-pool blocks: confidence.mean appears once per pool
    expect(screen.getAllByText("confidence.mean").length).toBe(2);
    expect(screen.getAllByText(/Correlations — scalar measures/).length).toBeGreaterThan(0);
    expect(screen.getByText("ai_use.prompts")).toBeTruthy();
    // r=0.64 renders two decimals
    expect(screen.getByText("0.64")).toBeTruthy();
    // a zero-variance pair renders an explicit null r, never a fake 0
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("Page Timing column-heading filters", () => {
  it("partition filter toggles categories off/on per table", async () => {
    window.history.replaceState(null, "", "/investigators/admin/?dsec=timing");
    const timing = {
      schema_version: "dashboard.timing.a1",
      session_totals: [],
      wall_durations: [
        { partition: "study|ck:ai|live|faithful", arm: "high", stage: "task", n: 5, median: 300000, iqr: 10000, censored: 0 },
        { partition: "study|ck:scr|live|faithful", arm: "low", stage: "task", n: 6, median: 290000, iqr: 9000, censored: 0 },
      ],
      active_durations: [],
      checkin: [],
      task_budget: [],
      notes: [],
    };
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/timing")
        ? jsonResponse(200, timing)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await screen.findByText("Stage durations — wall");
    const caption = await screen.findByText(/Median\/IQR wall time per stage visit/);
    const table = caption.closest("table")!;
    const tbody = table.querySelector("tbody")!;
    // both partitions visible before filtering
    expect(within(tbody as HTMLElement).getByText("study|ck:ai|live|faithful")).toBeTruthy();
    expect(within(tbody as HTMLElement).getByText("study|ck:scr|live|faithful")).toBeTruthy();
    // open the partition heading filter and hide the scripted partition
    fireEvent.click(within(table as HTMLElement).getByRole("button", { name: /^partition/ }));
    fireEvent.click(within(table as HTMLElement).getByLabelText("study|ck:scr|live|faithful"));
    expect(within(tbody as HTMLElement).queryByText("study|ck:scr|live|faithful")).toBeNull();
    expect(within(tbody as HTMLElement).getByText("study|ck:ai|live|faithful")).toBeTruthy();
    expect(await screen.findByText(/1 of 2 rows shown/)).toBeTruthy();
    // re-check restores the row
    fireEvent.click(within(table as HTMLElement).getByLabelText("study|ck:scr|live|faithful"));
    expect(within(tbody as HTMLElement).getByText("study|ck:scr|live|faithful")).toBeTruthy();
  });
});

describe("version (batch group) filter", () => {
  const GROUPS = {
    batches: [],
    groups: [
      { group_id: "study.live.v9", mode: "live", enrollment_open: false, allocated: 5, cells: [] },
      { group_id: "study.smoke.v10", mode: "live", enrollment_open: false, allocated: 0, cells: [] },
      { group_id: "study.live.v10", mode: "live", enrollment_open: true, allocated: 0, cells: [] },
    ],
  };

  it("defaults to the newest study.live.* group and scopes every fetch", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/batches")
        ? jsonResponse(200, GROUPS)
        : url.includes("/dashboard/overview")
          ? jsonResponse(200, EMPTY_OVERVIEW)
          : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(urls().some((u) => u.includes("batch_group_id=study.live.v10"))).toBe(true),
    );
    expect(window.location.search).toContain("batch_group_id=study.live.v10");
    // Newest first, "All" last.
    const sel = screen.getByLabelText(/version/i, { selector: "select" });
    const labels = within(sel).getAllByRole("option").map((o) => o.textContent);
    expect(labels[0]).toBe("study.live.v10");
    expect(labels.at(-1)).toBe("All");
  });

  it("'All' clears the filter and persists as batch_group_id=all", async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes("/batches")
        ? jsonResponse(200, GROUPS)
        : url.includes("/dashboard/overview")
          ? jsonResponse(200, EMPTY_OVERVIEW)
          : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(window.location.search).toContain("batch_group_id=study.live.v10"),
    );
    const before = urls().filter((u) => u.includes("/dashboard/overview")).length;
    const sel = screen.getByLabelText(/version/i, { selector: "select" });
    fireEvent.change(sel, { target: { value: "all" } });
    await waitFor(() =>
      expect(window.location.search).toContain("batch_group_id=all"),
    );
    await waitFor(() =>
      expect(urls().filter((u) => u.includes("/dashboard/overview")).length).toBeGreaterThan(before),
    );
    const latest = urls().filter((u) => u.includes("/dashboard/overview")).at(-1) ?? "";
    expect(latest).not.toContain("batch_group_id=study");
  });

  it("an explicit batch_group_id in a deep link wins over the latest default", async () => {
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?batch_group_id=study.live.v9&dsec=overview",
    );
    fetchMock.mockImplementation((url: string) =>
      url.includes("/batches")
        ? jsonResponse(200, GROUPS)
        : url.includes("/dashboard/overview")
          ? jsonResponse(200, EMPTY_OVERVIEW)
          : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(urls().some((u) => u.includes("batch_group_id=study.live.v9"))).toBe(true),
    );
    expect(urls().some((u) => u.includes("batch_group_id=study.live.v10"))).toBe(false);
  });
});

describe("Readiness telemetry column + Survey Documentation vocabulary (cb-1.8)", () => {
  const READINESS_TEL = {
    ...EMPTY_READINESS,
    flag_summary: { telemetry_file_missing: 1 },
    flag_definitions: [
      { code: "telemetry_file_missing", severity: "review", category: "telemetry" },
    ],
    audit: {
      scope: "completers",
      total: 1,
      rows: [
        {
          session_id: "aaaaaaaa-0000-0000-0000-000000000001",
          research_pid: "TEST_X",
          batch_id: "b1",
          stage: "completion",
          arm: "high",
          mode: "qa",
          variant: "default",
          outcome: "completed",
          data_use: null,
          item_coverage: { answered: 0, skipped: 0, missing: 0, absent_expected: 0 },
          telemetry: { events: 12, epochs: 1, file_meta: true, file_events: 3, receipts: 8 },
          flags: [],
        },
      ],
    },
  };

  it("renders the telemetry summary cell from the audit payload", async () => {
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?mode=qa&dsec=readiness",
    );
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/readiness")
        ? jsonResponse(200, READINESS_TEL)
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText(/12ev · 1ep · meta✓ · 8rc/)).toBeTruthy(),
    );
  });

  it("renders the telemetry vocabulary table on Survey Documentation", async () => {
    window.history.replaceState(
      null,
      "",
      "/investigators/admin/?mode=qa&dsec=instruments",
    );
    fetchMock.mockImplementation((url: string) =>
      url.includes("/dashboard/instruments")
        ? jsonResponse(200, {
            artifacts: [],
            code_defined_instruments: [],
            codebook: {
              codebook_version: "cb-1.8",
              measures: [
                { key: "file.coverage_frac", name: "File coverage", denominator: "file_records", missing_rule: "missing_when_no_file_telemetry" },
              ],
              telemetry_kinds: [
                { kind: "file_meta", emitted_by: "case-file pane", payload: "{io_supported, n_items}", notes: "zero-vs-missing marker" },
              ],
            },
            notes: [],
          })
        : jsonResponse(200, {}),
    );
    render(<Dashboard onOpenSession={() => {}} onAuthLoss={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText(/telemetry_file_meta/)).toBeTruthy(),
    );
    expect(screen.getByText("missing_when_no_file_telemetry")).toBeTruthy();
  });
});
