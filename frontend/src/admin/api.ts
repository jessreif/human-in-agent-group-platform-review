/**
 * Admin API client (INV-1301..1308). Same-origin calls to /investigators/api/admin/*;
 * the signed ``inv_admin`` session cookie travels automatically and every mutation
 * echoes the readable ``inv_admin_csrf`` cookie in ``X-CSRF-Token`` (double-submit).
 * No forwarded identity header is ever sent or read — the app is the only admin gate.
 *
 * Bundle-hygiene note: this module is loaded only on the admin route and deliberately
 * uses neutral field names — no participant-surface condition/truth vocabulary.
 */
import { BASE_PATH } from "../config";

const API = `${BASE_PATH}api/admin`;

export type Role = "researcher" | "operator" | "compensation";

export class AdminApiError extends Error {
  readonly status: number;
  readonly kind: string;
  constructor(status: number, kind: string, detail: string) {
    super(detail || kind);
    this.status = status;
    this.kind = kind;
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)inv_admin_csrf=([^;]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  };
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRF-Token"] = csrfToken();
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${API}${path}`, {
    credentials: "same-origin",
    ...init,
    headers,
  });
  const body: unknown = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = body as { error?: string; detail?: string };
    throw new AdminApiError(
      resp.status,
      err.error ?? `http_${resp.status}`,
      err.detail ?? "",
    );
  }
  return body as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") u.set(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

export type DashFilters = Record<string, string | undefined>;

/** One platform link randomizing across member batches (balanced cell blocks). */
export interface BatchGroup {
  group_id: string;
  mode: string;
  enrollment_open: boolean;
  allocated: number;
  quota?: {
    platform: string;
    test_only: boolean;
    target_completers_per_cell: number;
    total_target: number;
    completed: number;
    active: number;
    available: number;
    target_met: boolean;
    configuration_matches: boolean;
  };
  cells: {
    batch_id: string;
    manifest_id: string;
    arm: string;
    allocated: number;
    allocatable?: boolean;
    target?: number;
    completed?: number;
    active?: number;
    available?: number;
  }[];
}

export interface Whoami {
  role: Role;
  actor: string;
  expires_at: number;
  admin_emails: string[];
  app_mode?: string;
}

export const adminApi = {
  login: (role: Role, password: string, email?: string) =>
    req<{ role: Role; actor: string; expires_at: number; app_mode?: string }>(
      "/login",
      {
        method: "POST",
        body: JSON.stringify({ role, password, email: email || undefined }),
      },
    ),
  logout: () => req<{ ok: boolean }>("/logout", { method: "POST", body: "{}" }),
  whoami: () => req<Whoami>("/whoami"),

  sessions: (f: {
    q?: string | undefined;
    batch_id?: string | undefined;
    batch_group_id?: string | undefined;
    stage?: string | undefined;
    mode?: string | undefined;
  }) => req<{ sessions: Record<string, unknown>[] }>(`/sessions${qs(f)}`),
  sessionDetail: (sid: string) =>
    req<Record<string, unknown>>(`/sessions/${sid}`),
  sessionReplay: (sid: string) =>
    req<Record<string, unknown>>(`/sessions/${sid}/replay`),
  sessionAudit: (sid: string) =>
    req<{ entries: Record<string, unknown>[] }>(`/sessions/${sid}/audit`),
  monitor: () => req<Record<string, unknown>>("/monitor"),
  balance: () => req<Record<string, unknown>>("/reports/balance"),
  dispositions: () => req<Record<string, unknown>>("/dispositions"),
  auditLog: () => req<{ entries: Record<string, unknown>[] }>("/audit"),

  batches: () =>
    req<{ batches: Record<string, unknown>[]; groups?: BatchGroup[] }>(
      "/batches",
    ),
  setGroupEnrollment: (group_id: string, open: boolean, reason: string) =>
    req(`/batch-groups/${encodeURIComponent(group_id)}/enrollment`, {
      method: "POST",
      body: JSON.stringify({ open, reason }),
    }),
  createBatch: (batch_id: string, manifest_id: string, mode: string) =>
    req("/batches", {
      method: "POST",
      body: JSON.stringify({ batch_id, manifest_id, mode }),
    }),
  setEnrollment: (batch_id: string, open: boolean, reason: string) =>
    req(`/batches/${encodeURIComponent(batch_id)}/enrollment`, {
      method: "POST",
      body: JSON.stringify({ open, reason }),
    }),
  previewCombos: () =>
    req<{
      combos: Record<string, unknown>[];
      timing_modes: string[];
      scripted_teammates_enabled?: boolean;
      ai_assistant_enabled?: boolean;
      crate_overlap_enabled?: boolean;
      crate_number_enabled?: boolean;
      time_pressure_enabled?: boolean;
      ai_qa_enabled?: boolean;
      bonus_payment_enabled?: boolean;
    }>("/preview-combos"),
  issuePreview: (manifest_id: string, armChoice: string, timing_mode: string) =>
    req<{ descriptor: string; entry_url: string }>("/previews", {
      method: "POST",
      body: JSON.stringify({ manifest_id, arm: armChoice, timing_mode }),
    }),
  myPreviews: () =>
    req<{
      links: {
        manifest_id: string;
        arm: string;
        nonce: string;
        entry_url: string;
        issued_at: string;
        expires_at: number | null;
        session: {
          session_id: string;
          stage: string;
          terminal_reason: string | null;
          created_at: string;
          stage_entered_at: string;
          deleted?: boolean;
          purge_pending?: {
            mutable: number;
            privileged: number;
            identity: number;
            total: number;
          } | null;
        } | null;
      }[];
    }>("/previews/mine"),

  technicalStop: (sid: string, reason: string, notes: string) =>
    req(`/sessions/${sid}/technical_stop`, {
      method: "POST",
      body: JSON.stringify({ reason, notes }),
    }),
  credentialRecovery: (sid: string, reason: string) =>
    req<{ credential_token: string }>(`/sessions/${sid}/credential_recovery`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  openIncident: (
    sid: string,
    kind: string,
    clock_effect: string,
    notes: string,
  ) =>
    req<{ incident_id: string }>(`/sessions/${sid}/incidents`, {
      method: "POST",
      body: JSON.stringify({ kind, clock_effect, notes }),
    }),
  closeIncident: (sid: string, iid: string, reason: string) =>
    req(`/sessions/${sid}/incidents/${iid}/close`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  deletion: (sid: string, reason: string, dry_run: boolean) =>
    req(`/sessions/${sid}/deletion`, {
      method: "POST",
      body: JSON.stringify({ dry_run, reason }),
    }),
  decideEntitlement: (
    sid: string,
    kind: string,
    decision: string,
    note: string,
  ) =>
    req(`/entitlements/${sid}/${kind}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    }),

  retentionStatus: () => req<Record<string, unknown>>("/retention"),
  scheduleRetention: (study_close_at: string) =>
    req("/retention/schedule", {
      method: "POST",
      body: JSON.stringify({ study_close_at }),
    }),
  purgeLinks: (dry_run: boolean) =>
    req("/retention/purge", {
      method: "POST",
      body: JSON.stringify({ dry_run }),
    }),
  reapplyTombstones: () =>
    req("/retention/reapply", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }),
  cleanup: (
    scope: string,
    dry_run: boolean,
    reason: string,
    confirm_live: boolean,
  ) =>
    req("/cleanup", {
      method: "POST",
      body: JSON.stringify({ scope, dry_run, reason, confirm_live }),
    }),

  docs: () =>
    req<{
      docs: {
        id: string;
        label: string;
        file: string;
        size?: number;
        modified_at?: string;
      }[];
    }>("/docs"),
  docUrl: (id: string, download: boolean) =>
    `${API}/docs/${encodeURIComponent(id)}${download ? "?download=1" : ""}`,

  exportRun: (
    kind: string,
    body: {
      batch_id?: string;
      study_key?: string;
      session_ids?: string[];
      scope?: Record<string, string | undefined>;
    } = {},
  ) =>
    req("/exports", {
      method: "POST",
      body: JSON.stringify({ kind, ...body }),
    }),

  // Study Admin Dashboard (WP17) — read-only GETs; a caller-supplied
  // AbortSignal fences races so a stale response can never overwrite a newer
  // filter's result.
  dashOverview: (f: Record<string, string | undefined>, signal?: AbortSignal) =>
    req<Record<string, unknown>>(
      `/dashboard/overview${qs(f)}`,
      signal ? { signal } : {},
    ),
  dashReadiness: (
    f: Record<string, string | undefined>,
    pageSize: number,
    offset: number,
    auditScope: string,
    signal?: AbortSignal,
  ) =>
    req<Record<string, unknown>>(
      `/dashboard/readiness${qs({ ...f, page_size: pageSize, offset, audit_scope: auditScope })}`,
      signal ? { signal } : {},
    ),
  dashTiming: (f: Record<string, string | undefined>, signal?: AbortSignal) =>
    req<Record<string, unknown>>(
      `/dashboard/timing${qs(f)}`,
      signal ? { signal } : {},
    ),
  dashInstruments: (
    f: Record<string, string | undefined>,
    signal?: AbortSignal,
  ) =>
    req<Record<string, unknown>>(
      `/dashboard/instruments${qs(f)}`,
      signal ? { signal } : {},
    ),
  dashAnalysis: (f: Record<string, string | undefined>, signal?: AbortSignal) =>
    req<Record<string, unknown>>(
      `/dashboard/analysis${qs(f)}`,
      signal ? { signal } : {},
    ),
  dashChat: (f: Record<string, string | undefined>, signal?: AbortSignal) =>
    req<Record<string, unknown>>(
      `/dashboard/chat${qs(f)}`,
      signal ? { signal } : {},
    ),
  dashChatJudge: (sessionId: string, question: string) =>
    req<Record<string, unknown>>("/dashboard/chat_judge", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, question }),
    }),
  dashChatJudgeList: (sessionId: string, signal?: AbortSignal) =>
    req<Record<string, unknown>>(
      `/dashboard/chat_judge/${sessionId}`,
      signal ? { signal } : {},
    ),
  exportsCatalog: (signal?: AbortSignal) =>
    req<Record<string, unknown>>("/exports", signal ? { signal } : {}),
  exportTableNames: (signal?: AbortSignal) =>
    req<{ tables: string[] }>("/exports/tables", signal ? { signal } : {}),
  // Data-egress download: a CSRF-protected POST whose 200 carries the CSV body
  // (not JSON), so the raw Response is returned for the caller to blob().
  exportTableCsv: (name: string, f: DashFilters) => {
    const scope: Record<string, string> = {};
    for (const [k, v] of Object.entries(f))
      if (v !== undefined && v !== "") scope[k] = v;
    const token = csrfToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    // An absent cookie must not send a header. An empty string is not a token.
    if (token !== "") headers["X-CSRF-Token"] = token;
    return fetch(`${API}/exports/table`, {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify({ name, scope }),
    });
  },

  compensationQueue: () =>
    req<{ rows: Record<string, unknown>[] }>("/compensation/queue"),
  markPaid: (sid: string, kind: string, paid_ref: string) =>
    req(`/compensation/entitlements/${sid}/${kind}/paid`, {
      method: "POST",
      body: JSON.stringify({ paid_ref }),
    }),
};
