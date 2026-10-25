/**
 * Session controller: bootstrap orchestration tying the store, the event pump and the
 * telemetry queue to the DOM-facing app. Kept thin — the state machine lives in
 * ``SessionStore``; this file owns startup ordering and disposal only.
 *
 * Boot order (INV-0805/0806):
 * 1. Try GET /session — a live ``inv_session`` cookie means this browser already holds a
 *    credential (reload path; no re-entry needed and the query params may be gone).
 * 2. Otherwise POST /entry with the current query string (platform linkage or signed
 *    preview descriptor). A refusal renders the closed-study screen.
 * 3. Full resync (session + workbench + event replay from seq 0), then start the pump —
 *    SSE when EventSource exists, polling otherwise — and the telemetry flusher.
 * 4. Claim/confirm the writer lease via ``resume``; a lease held elsewhere is reported as
 *    a read-only second window with an explicit takeover affordance.
 */
import { ApiError, enter, getEvents, getSession, getWorkbench, postCommand, postComplete, readCookie } from "../api/client";
import { TERMINAL_STAGES } from "../api/types";
import { EventPump, type EventSourceLike } from "./pump";
import { SessionStore, type StoreApi } from "./store";
import { TelemetryQueue } from "../telemetry";
import {
  CLAIM_HEARTBEAT_MS,
  adoptWriter,
  clearWriter,
  clearClaim,
  closeTabChannel,
  detectClone,
  foreignClaimActive,
  resetTabId,
  storedWriter,
  tabId,
  writeClaim,
} from "./writerLease";

/** Boot GET /session retries before falling back to the disconnected/retry wait. */
const BOOT_SESSION_RETRIES = 2;
const BOOT_SESSION_DELAY_MS = 300;
/** How often a transiently failed boot re-runs itself while the page lives. */
const BOOT_RESTART_MS = 2_000;
/** Total resume attempts per call — the first try plus one stale-revision retry. */
const RESUME_MAX_ATTEMPTS = 2;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface ControllerDeps {
  fetchImpl?: typeof fetch;
  /** Injected for tests / non-DOM runtimes; defaults to window.EventSource. */
  eventSourceFactory?: ((url: string) => EventSourceLike) | null;
  now?: () => number;
  uuid?: () => string;
}

function defaultEventSourceFactory(): ((url: string) => EventSourceLike) | null {
  if (typeof EventSource === "undefined") return null;
  return (url) => new EventSource(url) as unknown as EventSourceLike;
}

export class SessionController {
  readonly store: SessionStore;
  readonly pump: EventPump;
  readonly telemetry: TelemetryQueue;
  private readonly fetchImpl: typeof fetch;
  private readonly uuid: () => string;
  private leaseTimer: ReturnType<typeof setInterval> | null = null;
  private bootTimer: ReturnType<typeof setTimeout> | null = null;
  private leaseSessionId: string | null = null;
  private leaseTabId: string | null = null;
  private resumeBusy = false;
  private disposed = false;
  /** Bumped on every start() and dispose() — a stale boot aborts at its next await. */
  private bootGen = 0;

  constructor(deps: ControllerDeps = {}) {
    this.fetchImpl = deps.fetchImpl ?? fetch;
    this.uuid = deps.uuid ?? (() => crypto.randomUUID());
    this.telemetry = new TelemetryQueue({ fetchImpl: this.fetchImpl });
    const api: StoreApi = {
      getSession: () => getSession(this.fetchImpl),
      getEvents: (afterSeq) => getEvents(afterSeq, this.fetchImpl),
      getWorkbench: () => getWorkbench(this.fetchImpl),
      postCommand: (body) => postCommand(body, this.fetchImpl),
      postComplete: (body) => postComplete(body, this.fetchImpl),
    };
    this.store = new SessionStore({
      api,
      ...(deps.now ? { now: deps.now } : {}),
      ...(deps.uuid ? { uuid: deps.uuid } : {}),
      onEventApplied: (event, replayed) => {
        // Bounded receive receipts (INV-0807); replayed events are marked as such.
        this.telemetry.emit("receive", { seq: event.seq, type: event.type, replayed });
      },
    });
    this.pump = new EventPump(this.store, {
      eventSourceFactory:
        deps.eventSourceFactory === undefined
          ? defaultEventSourceFactory()
          : deps.eventSourceFactory,
      fetchImpl: this.fetchImpl,
    });
  }

  /** Parse the current URL's query params into the plain map /entry forwards. */
  private static queryFrom(search: string): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of new URLSearchParams(search)) out[k] = v;
    return out;
  }

  /**
   * Bootstrap the session. Returns when the store holds a usable snapshot or the phase
   * is settled to ``refused`` / ``authFailed``.
   */
  async start(search: string): Promise<void> {
    const gen = (this.bootGen += 1);
    this.disposed = false;
    if (this.bootTimer !== null) {
      clearTimeout(this.bootTimer);
      this.bootTimer = null;
    }
    this.store.setBootPhase("booting");
    // BA-29: /entry is reached ONLY for the unauthenticated case (401/403). A network
    // or 5xx failure on GET /session is transient — pushing a cookie-holding
    // participant into entry could surface a credential_recovery_required refusal —
    // so retry a couple of times, then hold in a disconnected boot phase that
    // re-runs start() on a timer until the API comes back or the page is gone.
    let sessionReady = false;
    let unauthenticated = false;
    for (let attempt = 0; attempt <= BOOT_SESSION_RETRIES; attempt += 1) {
      try {
        this.store.applySessionResponse(await getSession(this.fetchImpl));
        sessionReady = true;
        break;
      } catch (err) {
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          unauthenticated = true;
          break;
        }
        if (attempt < BOOT_SESSION_RETRIES) await delay(BOOT_SESSION_DELAY_MS);
      }
    }
    if (!sessionReady && !unauthenticated) {
      this.store.setBootPhase("booting", { connection: "disconnected" });
      this.bootTimer = setTimeout(() => {
        if (!this.disposed) void this.start(search);
      }, BOOT_RESTART_MS);
      return;
    }
    // A cookie for a session that is already over (terminal or completed) must not trap
    // a different entry link on the same browser — a shared lab machine that finished or
    // lost one session would otherwise show "Session ended" for every new link while the
    // ``pt``/``batch`` params sit unhonoured in the address bar. Re-enter only when the
    // URL actually carries an entry link and the cookie's session is done; entry resolves
    // to the descriptor's own session (or refuses cleanly). Live sessions are never
    // hijacked — ``entry`` would force-take the writer lease, so a second tab opening the
    // same link mid-session keeps the read-only path instead.
    const query = SessionController.queryFrom(search);
    const cookieStage = this.store.getSnapshot().stage;
    const sessionOver =
      cookieStage !== null &&
      (TERMINAL_STAGES.has(cookieStage) || cookieStage === "completion");
    const carriesEntryLink = query["pt"] !== undefined || query["batch"] !== undefined;
    if (unauthenticated || (sessionReady && sessionOver && carriesEntryLink)) {
      try {
        await enter(query, this.fetchImpl);
      } catch (err) {
        const entryNotes: Record<string, string> = {
          batch_at_capacity: "All study places are currently reserved. Please return to the study platform.",
          platform_not_allowed: "Please use the link and participant ID supplied by the study platform.",
          quota_configuration_changed: "This study is temporarily unavailable. Please contact the research team.",
          test_only_batch: "This link is reserved for internal testing. Please return to the study platform.",
          test_identity_not_allowed: "Internal test IDs cannot enter the paid study. Please use the testing link.",
        };
        const reason = err instanceof ApiError
          ? (err.kind === "entry_refused" ? entryNotes[err.message] : undefined)
            ?? `${err.status}:${err.kind ?? err.message}`
          : "network";
        this.store.setBootPhase("refused", { refusedReason: reason });
        return;
      }
    }
    // A superseded/disposed boot stops here — a newer start() owns the pipeline.
    if (gen !== this.bootGen) return;
    try {
      await this.store.resync();
    } catch (err) {
      if (err instanceof ApiError && err.classification === "auth") {
        this.store.setBootPhase("refused", { refusedReason: "auth", authFailed: true });
        return;
      }
      // A transient failure still leaves a usable shell: polling retries keep coming.
    }
    if (gen !== this.bootGen) return;
    this.store.setBootPhase("ready");
    this.pump.start();
    this.telemetry.start();
    // Writer lease (INV-0407): the inv_writer cookie is shared by all tabs, so a second
    // tab must never reach ``resume`` — a fresh foreign claim in localStorage already
    // proves the lease has a live writer. Otherwise resume renews (same writer) or
    // acquires (free/expired) and this tab adopts the returned writer id.
    const sessionId = this.store.getSnapshot().sessionId;
    let myTab = tabId(this.uuid);
    this.leaseSessionId = sessionId;
    this.leaseTabId = myTab;
    // BA-27: a browser "Duplicate Tab" clones sessionStorage — same tab id AND writer
    // id — and would pass the claim check as the same writer. The BroadcastChannel
    // handshake asks whether a live document already owns this tab id; an ``alive``
    // answer with a foreign document nonce means this tab is a clone. It mints a fresh
    // tab id and goes read-only — no resume attempt; the incumbent's claim is foreign
    // to the new id and keeps it that way.
    const clone = sessionId !== null && (await detectClone(sessionId, myTab));
    if (gen !== this.bootGen) return;
    if (clone) {
      myTab = resetTabId(this.uuid);
      this.leaseTabId = myTab;
      clearWriter();
      this.store.setWriter("readonly", true);
    } else if (sessionId && foreignClaimActive(sessionId, myTab)) {
      this.store.setWriter("readonly", true);
    } else {
      let outcome = await this.tryResume(gen);
      if (outcome === "lost") {
        // A newer grant (re-entry or a takeover response) may have replaced the cookie
        // since this tab stored its writer id — adopt the cookie value once before
        // conceding read-only.
        const cookieWriter = readCookie("inv_writer");
        if (cookieWriter && cookieWriter !== storedWriter()) {
          adoptWriter(cookieWriter);
          outcome = await this.tryResume(gen);
        }
      }
      if (gen !== this.bootGen) return;
      if (outcome === "lost") {
        clearWriter();
        this.store.setWriter("readonly", true);
      }
      // "pending" (transient failure — network, 5xx, or a writer-id-less response):
      // the tab stays writer="unknown". Locked-when-not-held gating (BA-26) keeps
      // every mutating control inert and dispatch refuses locally; the lease
      // heartbeat re-runs tryResume once per beat until it resolves either way.
    }
    this.leaseTimer = setInterval(() => this.leaseBeat(), CLAIM_HEARTBEAT_MS);
  }

  /**
   * Bounded ``resume`` attempts (BA-28). A resume only SUCCEEDS when the response
   * carries a valid ``writer_id`` — that is the sole path to ``held``. Outcomes:
   * - ``"lost"``: the server refused (``writer_lease_held``) → caller goes read-only.
   * - ``"pending"``: transient/unresolved (network, 5xx, lost or writer-id-less
   *   response) → the caller leaves the writer state untouched ("unknown").
   * A ``stale_revision`` resyncs the store and retries with a NEW command id — the
   * rejected id is a stored rejection receipt and must never be reused.
   */
  private async tryResume(
    gen?: number,
  ): Promise<"held" | "lost" | "pending"> {
    for (let attempt = 0; attempt < RESUME_MAX_ATTEMPTS; attempt += 1) {
      try {
        if (gen !== undefined && gen !== this.bootGen) return "pending";
        const result = await postCommand(
          {
            command_id: this.uuid(),
            kind: "resume",
            expected_revision: this.store.getSnapshot().revision,
            payload: {},
          },
          this.fetchImpl,
        );
        const wid = result.result.writer_id;
        // A response that lands after the boot was superseded/disposed must not
        // adopt a writer or publish a claim for a dead pipeline.
        if (gen !== undefined && gen !== this.bootGen) return "pending";
        if (typeof wid !== "string") return "pending";
        adoptWriter(wid);
        if (this.leaseSessionId && this.leaseTabId) {
          writeClaim(this.leaseSessionId, this.leaseTabId);
        }
        this.store.setWriter("held");
        return "held";
      } catch (err) {
        if (err instanceof ApiError && err.kind === "writer_lease_held") return "lost";
        if (
          err instanceof ApiError &&
          err.kind === "stale_revision" &&
          attempt + 1 < RESUME_MAX_ATTEMPTS
        ) {
          await this.store.resync().catch(() => undefined);
          continue;
        }
        return "pending";
      }
    }
    return "pending";
  }

  /**
   * Heartbeat: refresh my claim while writer; drop to read-only on foreign takeover.
   * While NOT holding (a boot resume that ended "pending", or a local lease refusal),
   * one fresh resume is attempted per beat — skipped while a live foreign claim proves
   * another tab is writing (the server would refuse anyway, but the claim check keeps
   * the second tab from churning requests) and reentrancy-guarded by ``resumeBusy``.
   */
  private leaseBeat(): void {
    const { sessionId, writer, authFailed } = this.store.getSnapshot();
    if (!sessionId || !this.leaseTabId) return;
    if (writer === "held") {
      if (foreignClaimActive(sessionId, this.leaseTabId)) {
        clearWriter();
        this.store.setWriter("readonly", true);
      } else {
        writeClaim(sessionId, this.leaseTabId);
      }
      return;
    }
    if (authFailed || this.resumeBusy || foreignClaimActive(sessionId, this.leaseTabId)) {
      return;
    }
    this.resumeBusy = true;
    void this.tryResume()
      .then((outcome) => {
        if (outcome === "lost") {
          clearWriter();
          this.store.setWriter("readonly", true);
        }
      })
      .finally(() => {
        this.resumeBusy = false;
      });
  }

  dispose(): void {
    this.disposed = true;
    this.bootGen += 1; // abort any in-flight start()
    if (this.bootTimer !== null) {
      clearTimeout(this.bootTimer);
      this.bootTimer = null;
    }
    if (this.leaseTimer !== null) {
      clearInterval(this.leaseTimer);
      this.leaseTimer = null;
    }
    if (this.leaseSessionId && this.leaseTabId) {
      clearClaim(this.leaseSessionId, this.leaseTabId);
    }
    closeTabChannel();
    this.pump.stop();
    this.telemetry.stop();
    this.store.dispose();
  }
}
