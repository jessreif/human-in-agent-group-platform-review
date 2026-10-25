/**
 * Typed, same-origin API client (INV-0801). All participant traffic goes to
 * /investigators/api on the experiment's own origin; there is no third-party endpoint
 * (spec §2.3). Non-GET calls echo the readable ``inv_csrf`` cookie in ``X-CSRF-Token``
 * (double-submit); session and writer cookies travel automatically.
 */
import { BASE_PATH } from "../config";
import { storedWriter } from "../state/writerLease";

import type {
  CommandKind,
  CommandResult,
  EntryResponse,
  EventsResponse,
  SessionResponse,
  WorkbenchResponse,
} from "./types";

export const API_BASE = `${BASE_PATH}api`;

export type HealthResponse = { status: "ok" };

/** Stable client-side classification of a failed call (spec §8.2 status vocabulary). */
export type ErrorClass =
  | "malformed" // 400 / 422 — the request was not well formed; do not retry verbatim
  | "auth" // 401 / 403 / 410 — credential missing, revoked or session gone
  | "conflict" // 409 — stale revision, rejection reason or writer-lease refusal
  | "quota" // 429 — slow down and retry later
  | "unavailable" // 503 — persistence unavailable; nothing was committed
  | "network"; // no response at all

export class ApiError extends Error {
  readonly status: number;

  /** The server's stable error token (the ``error`` field), when present. */
  readonly kind: string | null;

  constructor(status: number, message: string, kind: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = kind;
  }

  get classification(): ErrorClass {
    if (this.status === 400 || this.status === 422) return "malformed";
    if (this.status === 401 || this.status === 403 || this.status === 410) return "auth";
    if (this.status === 409) return "conflict";
    if (this.status === 429) return "quota";
    if (this.status === 503) return "unavailable";
    return "network";
  }
}

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new Error(`API paths must start with "/": ${path}`);
  }
  return `${API_BASE}${path}`;
}

/** Read one readable cookie by name (``inv_csrf`` / ``inv_writer`` are not HttpOnly). */
export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

function csrfHeaders(): Record<string, string> {
  const token = readCookie("inv_csrf");
  return token ? { "x-csrf-token": token } : {};
}

/** Per-tab writer identity (the ``inv_writer`` cookie is shared across tabs; the
 * sessionStorage id is not). Sent only when this tab has claimed the lease. Server
 * contract: every mutating command requires this header EXCEPT the two claim commands
 * ``resume``/``takeover``, which may still fall back to the cookie. */
function writerHeader(): Record<string, string> {
  const writer = storedWriter();
  return writer ? { "x-writer-id": writer } : {};
}

async function parseProblem(response: Response): Promise<ApiError> {
  let kind: string | null = null;
  let detail = "";
  try {
    const body = (await response.json()) as { error?: unknown; detail?: unknown };
    if (typeof body.error === "string") kind = body.error;
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(response.status, detail || `request failed (${response.status})`, kind);
}

async function request<T>(
  method: string,
  path: string,
  body: unknown,
  fetchImpl: typeof fetch,
  query?: Record<string, string>,
): Promise<T> {
  let url = apiUrl(path);
  if (query) {
    const params = new URLSearchParams(query);
    url += `?${params.toString()}`;
  }
  const init: RequestInit = {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(method !== "GET"
        ? { "Content-Type": "application/json", ...csrfHeaders(), ...writerHeader() }
        : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };
  let response: Response;
  try {
    response = await fetchImpl(url, init);
  } catch (err) {
    const e = new ApiError(0, "network failure");
    e.cause = err;
    throw e;
  }
  if (!response.ok) {
    throw await parseProblem(response);
  }
  return (await response.json()) as T;
}

export async function getHealth(fetchImpl: typeof fetch = fetch): Promise<HealthResponse> {
  const response = await fetchImpl(apiUrl("/health"), {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new ApiError(response.status, "health check failed");
  }
  const body = (await response.json()) as Partial<HealthResponse>;
  if (body.status !== "ok") {
    throw new ApiError(response.status, "unexpected health payload");
  }
  return { status: "ok" };
}

/** Entry: the current URL's platform/preview query params are forwarded verbatim. */
export async function enter(
  query: Record<string, string>,
  fetchImpl: typeof fetch = fetch,
): Promise<EntryResponse> {
  return request<EntryResponse>("POST", "/entry", undefined, fetchImpl, query);
}

export async function getSession(
  fetchImpl: typeof fetch = fetch,
): Promise<SessionResponse> {
  return request<SessionResponse>("GET", "/session", undefined, fetchImpl);
}

export async function getEvents(
  afterSeq: number,
  fetchImpl: typeof fetch = fetch,
  limit = 1000,
): Promise<EventsResponse> {
  return request<EventsResponse>("GET", "/events", undefined, fetchImpl, {
    after_seq: String(afterSeq),
    limit: String(limit),
  });
}

export async function getWorkbench(
  fetchImpl: typeof fetch = fetch,
): Promise<WorkbenchResponse> {
  return request<WorkbenchResponse>("GET", "/workbench", undefined, fetchImpl);
}

export interface CommandBody {
  command_id: string;
  kind: CommandKind;
  expected_revision: number;
  payload: Record<string, unknown>;
  client_monotonic_ms?: number;
}

export async function postCommand(
  body: CommandBody,
  fetchImpl: typeof fetch = fetch,
): Promise<CommandResult> {
  return request<CommandResult>("POST", "/commands", body, fetchImpl);
}

export async function postComplete(
  body: Pick<CommandBody, "command_id" | "expected_revision" | "client_monotonic_ms"> & {
    payload?: Record<string, unknown>;
  },
  fetchImpl: typeof fetch = fetch,
): Promise<CommandResult> {
  return request<CommandResult>("POST", "/complete", body, fetchImpl);
}

export interface TelemetryItem {
  kind: string;
  client_monotonic_ms: number;
  /** Per-load clock identity — ``client_monotonic_ms`` values are comparable
   * only within one epoch (a reload mints a new one). 2026-10-13 audit F06. */
  client_epoch?: string;
  data?: Record<string, unknown>;
}

/** Bounded telemetry batches (spec §8.2): items, not keystroke streams. */
export async function postTelemetry(
  items: TelemetryItem[],
  fetchImpl: typeof fetch = fetch,
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(
    "POST",
    "/telemetry",
    { kind: "batch", data: { events: items } },
    fetchImpl,
  );
}

/** SSE stream URL for ``EventSource`` (cookies ride same-origin; cursor in the query). */
export function eventStreamUrl(afterSeq: number): string {
  return `${apiUrl("/events/stream")}?after_seq=${afterSeq}`;
}
