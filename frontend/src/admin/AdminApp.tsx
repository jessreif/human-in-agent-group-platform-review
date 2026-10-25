/**
 * Admin subtree (INV-1301..1308). Route-gated: mounted by main.tsx only when the path
 * is under /investigators/admin — the participant surface never ships this code path
 * and this subtree never renders into participant routes.
 *
 * Views are intentionally utilitarian: read models plus role-gated action forms. All
 * rendered values come from sanitized JSON — nothing here injects HTML.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { adminApi, AdminApiError, type BatchGroup, type Role } from "./api";
import { Dashboard } from "./dashboard";
import { Walkthrough } from "./walkthrough";
import { FilterTh, distinct, useColFilters } from "./colfilter";

type Tab =
  | "links"
  | "sessions"
  | "monitor"
  | "batches"
  | "retention"
  | "documentation"
  | "dashboard"
  | "compensation";

const TABS_BY_ROLE: Record<Role, Tab[]> = {
  researcher: [
    "links",
    "sessions",
    "monitor",
    "batches",
    "retention",
    "documentation",
    "dashboard",
  ],
  operator: ["links", "sessions", "monitor", "batches", "retention"],
  compensation: ["compensation"],
};

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="admin-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="admin-json">{JSON.stringify(value, null, 2)}</pre>;
}

function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg =
    error instanceof AdminApiError
      ? `${error.kind} (${error.status})`
      : error instanceof Error
        ? error.message
        : String(error);
  return <p className="admin-error">{msg}</p>;
}

function useFetch<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    setLoading(true);
    fn()
      .then(setData)
      .catch(setError)
      .finally(() => setLoading(false));
    // `deps` is the caller-supplied dependency list of this generic loader hook;
    // the linter cannot see through it, and `fn` is re-created by every caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, loading, reload };
}

// ----- login ---------------------------------------------------------------------------------

function Login({
  onLogin,
}: {
  onLogin: (role: Role, actor: string, appMode?: string) => void;
}) {
  const [role, setRole] = useState<Role>("researcher");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="admin-card"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        adminApi
          .login(role, password, email || undefined)
          .then((r) => onLogin(r.role, r.actor, r.app_mode))
          .catch(setError)
          .finally(() => setBusy(false));
      }}
    >
      <h2>Research admin sign-in</h2>
      <p className="admin-note">
        Application-enforced access — forwarded identity headers are never used.
      </p>
      <Field label="Role">
        <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="researcher">researcher</option>
          <option value="operator">operator</option>
          <option value="compensation">compensation</option>
        </select>
      </Field>
      <Field label="Password">
        <input
          type="password"
          value={password}
          autoComplete="off"
          onChange={(e) => setPassword(e.target.value)}
        />
      </Field>
      <Field label="Email (optional, allowlisted)">
        <input value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <button disabled={busy || !password}>Sign in</button>
      <ErrorNote error={error} />
    </form>
  );
}

// ----- sessions ------------------------------------------------------------------------------

function Sessions({
  role,
  focus,
}: {
  role: Role;
  focus?: { id: string; n: number } | null;
}) {
  const [q, setQ] = useState("");
  const [batch, setBatch] = useState("");
  const [batchGroup, setBatchGroup] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  // Dashboard "open session" handoff: each jump carries a nonce so re-opening
  // the same session still re-focuses the detail pane.
  useEffect(() => {
    if (focus) setSelected(focus.id);
  }, [focus]);
  // Same defaulting as the dashboard version selector: batch groups are the
  // fielding rounds, the API returns them created_at-ascending, and the tab
  // opens on the newest ``study.live.*`` group instead of the pooled cohort.
  const groupsFetch = useFetch(() => adminApi.batches(), []);
  const groupList = useMemo(
    () => (groupsFetch.data?.groups ?? []) as BatchGroup[],
    [groupsFetch.data],
  );
  const latestGroup = useMemo(() => {
    const live = groupList.filter((g) => g.group_id.startsWith("study.live."));
    return (live.at(-1) ?? groupList.at(-1))?.group_id ?? "";
  }, [groupList]);
  const groupDefaultApplied = useRef(false);
  useEffect(() => {
    if (groupDefaultApplied.current || batchGroup !== "" || latestGroup === "")
      return;
    groupDefaultApplied.current = true;
    setBatchGroup(latestGroup);
  }, [latestGroup, batchGroup]);
  // Hold the session fetch until the group default resolves — otherwise the
  // table briefly shows every session before snapping to the current round.
  // When groups load but none exists, fall back to unscoped rather than block.
  const groupReady =
    batchGroup !== "" ||
    groupsFetch.error != null ||
    (!groupsFetch.loading && groupsFetch.data !== null && latestGroup === "");
  const { data, error, loading, reload } = useFetch(
    () =>
      groupReady
        ? adminApi.sessions({
            q: q || undefined,
            batch_id: batch || undefined,
            batch_group_id:
              batchGroup && batchGroup !== "all" ? batchGroup : undefined,
          })
        : new Promise<{ sessions: Record<string, unknown>[] }>(() => {}),
    [q, batch, batchGroup, groupReady],
  );
  const rows = data?.sessions ?? [];
  const colFilters = useColFilters();
  // Filterable columns: 1=batch, 3=mode, 4=stage, 5=terminal, 10=outcome —
  // client-side narrowing over the loaded rows (q/batch above still query).
  const rowVals = (r: Record<string, unknown>): string[] => [
    "",
    String(r.batch_id),
    "",
    String(r.mode),
    String(r.stage),
    String(r.terminal_reason ?? ""),
    "",
    "",
    "",
    "",
    String(r.completion_outcome ?? ""),
  ];
  const shown = rows.filter((r) => colFilters.pass(rowVals(r)));
  const filterCols = new Set([1, 3, 4, 5, 10]);
  // [label, hover explanation] — the purpose paragraph above stays, but the
  // per-column tooltip answers "what does this column mean" in place.
  const headCols: [string, string][] = [
    ["session", "Session ID prefix — click the row for the full record (stage history, exposures, replay)"],
    ["batch", "The member batch this session entered through (e.g. study.live.v10.ai)"],
    ["datetime", "When the session was created (UTC)"],
    ["mode", "live = fielded participant; preview/qa = test traffic excluded from research exports"],
    ["stage", "Where the participant currently is in the study flow (consent → training → task → …)"],
    ["terminal", "Why the session ended, if it ended abnormally (e.g. abandoned, technical stop)"],
    ["answers", "Crate answers the participant saved — each edit counts"],
    ["reconnects", "Re-entry events — page refresh, second tab, dropped connection"],
    ["rejections", "Commands the server refused (proceed before required items, invalid values, late input) — a nonzero count is normal"],
    ["open episodes", "Teammate requests still awaiting a reply when the task closed"],
    ["outcome", "The completion receipt's disposition (e.g. completed)"],
  ];
  return (
    <section>
      <div className="admin-card">
        <p className="admin-note">
          <strong>Purpose:</strong> every session the app knows about — fielded
          (<code>live</code>) participants plus <code>preview</code>/
          <code>qa</code> test sessions. Columns —{" "}
          <strong>answers</strong>: crate answers saved (edits count too);{" "}
          <strong>reconnects</strong>: re-entry events (refresh, second tab);{" "}
          <strong>rejections</strong>: commands the server refused (proceed
          before required items, invalid values, late input — a nonzero count
          is normal); <strong>open episodes</strong>: teammate requests still
          awaiting a reply when the task closed; <strong>outcome</strong>: the
          completion receipt's disposition. <strong>Click a row</strong> for
          the full record: stage history, manipulation exposures, entitlements,
          replay, and (operator) technical-stop / incident / deletion controls.
        </p>
      </div>
      <div className="admin-row">
        <input
          placeholder="research id / session prefix"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label>
          version
          <select
            value={batchGroup}
            onChange={(e) => setBatchGroup(e.target.value)}
          >
            {batchGroup === "" && <option value="">(latest…)</option>}
            {[...groupList].reverse().map((g) => (
              <option key={g.group_id} value={g.group_id}>
                {g.group_id}
              </option>
            ))}
            <option value="all">All</option>
          </select>
        </label>
        <input
          placeholder="batch"
          value={batch}
          onChange={(e) => setBatch(e.target.value)}
        />
        <button onClick={reload}>Refresh</button>
      </div>
      <ErrorNote error={error} />
      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
        {shown.length !== rows.length && (
          <p className="admin-note">
            {shown.length} of {rows.length} rows shown — column filters active.
          </p>
        )}
        <table className="admin-table">
          <thead>
            <tr>
              {headCols.map(([label, tip], i) =>
                filterCols.has(i) ? (
                  <FilterTh
                    key={label}
                    col={i}
                    label={label}
                    tip={tip}
                    values={distinct(rows, (r) => rowVals(r)[i] ?? "")}
                    hidden={colFilters.hidden[i]}
                    onToggle={colFilters.toggle}
                    onSet={colFilters.setCol}
                  />
                ) : (
                  <th key={label} title={tip}>{label}</th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={String(r.session_id)}
                onClick={() => setSelected(String(r.session_id))}
                className={selected === r.session_id ? "sel" : ""}
              >
                <td className="mono">{String(r.session_id).slice(0, 8)}</td>
                <td>{String(r.batch_id)}</td>
                <td className="mono" title={String(r.created_at ?? "")}>
                  {fmtSessionTime(r.created_at)}
                </td>
                <td>{String(r.mode)}</td>
                <td>{String(r.stage)}</td>
                <td>{String(r.terminal_reason ?? "")}</td>
                <td>{String(r.n_answered_crates)}</td>
                <td>{String(r.n_reconnects)}</td>
                <td>{String(r.n_rejected_commands)}</td>
                <td>{String(r.n_open_episodes)}</td>
                <td>{String(r.completion_outcome ?? "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </>
      )}
      {selected && <SessionDetail sid={selected} role={role} />}
    </section>
  );
}

function Action({
  label,
  run,
  confirm,
}: {
  label: string;
  run: () => Promise<unknown>;
  confirm?: string;
}) {
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <span className="admin-action">
      <button
        disabled={busy}
        onClick={() => {
          if (confirm && !window.confirm(confirm)) return;
          setBusy(true);
          setError(null);
          run()
            .then(setResult)
            .catch(setError)
            .finally(() => setBusy(false));
        }}
      >
        {label}
      </button>
      {result !== null && <JsonBlock value={result} />}
      <ErrorNote error={error} />
    </span>
  );
}

function fmtSessionTime(v: unknown): string {
  const s = String(v ?? "");
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function SessionDetail({ sid, role }: { sid: string; role: Role }) {
  const detail = useFetch(() => adminApi.sessionDetail(sid), [sid]);
  const replay = useFetch(() => adminApi.sessionReplay(sid), [sid]);
  const audit = useFetch(() => adminApi.sessionAudit(sid), [sid]);
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  // The detail card renders below the sessions table — past the fold on most
  // viewports, so a row click that only updates state looks like a dead click.
  // Bring the card into view whenever a different session is selected.
  useEffect(() => {
    rootRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [sid]);
  const session = (detail.data?.session ?? {}) as Record<string, unknown>;
  const entitlements = (detail.data?.entitlements ?? []) as Record<
    string,
    unknown
  >[];
  const replayBody = (replay.data ?? {}) as Record<string, unknown>;
  return (
    <div className="admin-card" ref={rootRef} style={{ scrollMarginTop: 16 }}>
      <h3>
        Session <span className="mono">{sid}</span>
      </h3>
      <ErrorNote error={detail.error} />
      <p>
        stage <b>{String(session.stage ?? "?")}</b> · terminal{" "}
        <b>{String(session.terminal_reason ?? "—")}</b> · batch{" "}
        <b>{String(session.batch_id ?? "?")}</b>
      </p>
      <h4>
        Session walkthrough <button onClick={replay.reload}>Refresh</button>
      </h4>
      <p className="admin-note">
        Unified timeline — participant-visible events plus restricted internals
        (restricted view:{" "}
        {String((replayBody.restricted_status as string) ?? "unknown")}).
      </p>
      <ErrorNote error={replay.error} />
      {replay.data ? <Walkthrough replay={replayBody} /> : null}
      {replayBody.restricted ? (
        <details>
          <summary className="admin-note">
            Raw replay JSON (restricted record)
          </summary>
          <JsonBlock value={replayBody.participant ?? null} />
          <JsonBlock value={replayBody.restricted} />
        </details>
      ) : (
        <details>
          <summary className="admin-note">
            Raw replay JSON (participant view)
          </summary>
          <JsonBlock value={replayBody.participant ?? null} />
        </details>
      )}
      <h4>Support actions (operator)</h4>
      {role === "operator" ? (
        <div className="admin-row">
          <input
            placeholder="reason (required)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <input
            placeholder="notes"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Action
            label="Technical stop"
            confirm={`Stop session ${sid}? Audited, terminal.`}
            run={() => adminApi.technicalStop(sid, reason, note)}
          />
          <Action
            label="Credential recovery"
            confirm="Revoke all credentials and mint a recovery token?"
            run={() => adminApi.credentialRecovery(sid, reason)}
          />
          <Action
            label="Open incident (excluded)"
            run={() => adminApi.openIncident(sid, "other", "excluded", note)}
          />
          <Action
            label="Plan deletion (dry run)"
            run={() => adminApi.deletion(sid, reason, true)}
          />
          <Action
            label="Apply deletion"
            confirm="Delete participant data rows? Tombstoned and audited."
            run={() => adminApi.deletion(sid, reason, false)}
          />
        </div>
      ) : (
        <p className="admin-note">Operator role required.</p>
      )}
      <h4>Entitlements (researcher decides)</h4>
      {role === "researcher" ? (
        entitlements.map((e) => (
          <div key={String(e.kind)} className="admin-row">
            <span>
              {String(e.kind)}: {String(e.status)} ({String(e.cents)}c)
            </span>
            {(["approved", "void", "ineligible"] as const).map((d) => (
              <Action
                key={d}
                label={d}
                run={() =>
                  adminApi.decideEntitlement(sid, String(e.kind), d, note)
                }
              />
            ))}
          </div>
        ))
      ) : (
        <ul>
          {entitlements.map((e) => (
            <li key={String(e.kind)}>
              {String(e.kind)}: {String(e.status)}
            </li>
          ))}
        </ul>
      )}
      <h4>Admin audit for this session</h4>
      <ErrorNote error={audit.error} />
      <JsonBlock value={audit.data?.entries ?? []} />
    </div>
  );
}

// ----- monitor --------------------------------------------------------------------------------

function Monitor() {
  const monitor = useFetch(() => adminApi.monitor());
  const balance = useFetch(() => adminApi.balance());
  const dispositions = useFetch(() => adminApi.dispositions());
  const body = (monitor.data ?? {}) as Record<string, unknown>;
  const sections = [
    "sessions_by_stage",
    "fidelity_rates",
    "teammate_fidelity",
    "manipulation_receipts",
    "unresolved_episodes",
    "incidents",
    "coding_coverage",
  ] as const;
  const SECTION_CAPTION: Record<string, string> = {
    sessions_by_stage:
      "Session funnel — count per lifecycle stage (consent → task → completion/abandoned).",
    fidelity_rates:
      "Generation pipeline health — how model calls and scheduled jobs ended (succeeded / validation_rejected / fallback), per surface.",
    teammate_fidelity:
      "The AI-arm gate: share of delivered teammate replies that were generated vs. deterministic fallback. Pause the group if a cohort with ≥2 measured AI sessions drops strictly below 80%.",
    manipulation_receipts:
      "Delivery evidence — receipts that scheduled cues, requests, and notices fired and reached the client. committed_without_receive should stay 0.",
    unresolved_episodes:
      "Teammate requests still awaiting an answer — open ones belong to running sessions; a few left at task end are normal.",
    incidents:
      "Operator-flagged session incidents (technical stops, clock effects). Should be empty or explained.",
    coding_coverage:
      "Post-study text-coding pipeline. 'unavailable' is expected — the console's database role cannot read coding tables by design; coding runs under the research role after data collection.",
  };
  return (
    <section>
      <div className="admin-card">
        <p className="admin-note">
          <strong>Purpose:</strong> live operational health while fielding —
          check the funnel, the AI-fidelity gate, and manipulation receipts
          after each new session. Each section reports its own status; an{" "}
          <em>unavailable</em> section fails closed and never reports a false
          "ok" — it is not necessarily an outage (see each caption).
        </p>
      </div>
      <ErrorNote error={monitor.error} />
      {sections.map((name) => {
        const s = (body[name] ?? {}) as Record<string, unknown>;
        return (
          <div key={name} className="admin-card">
            <h3>
              {name}{" "}
              <em className={s.status === "ok" ? "ok" : "warn"}>
                {String(s.status ?? "unrun")}
              </em>
            </h3>
            <p className="admin-note">{SECTION_CAPTION[name]}</p>
            <JsonBlock value={s.data ?? s.note ?? "not run"} />
          </div>
        );
      })}
      <div className="admin-card">
        <h3>Assignment / exposure / completion balance</h3>
        <p className="admin-note">
          Cell fill and outcome balance across the configured conditions — use this to confirm
          the permuted-block assignment is filling cells evenly.
        </p>
        <ErrorNote error={balance.error} />
        <JsonBlock value={balance.data} />
      </div>
      <div className="admin-card">
        <h3>Withdrawal &amp; data-use dispositions</h3>
        <p className="admin-note">
          How sessions ended and each participant's post-debrief data-use
          choice — declining data use does not affect payment.
        </p>
        <ErrorNote error={dispositions.error} />
        <JsonBlock value={dispositions.data} />
      </div>
    </section>
  );
}

// ----- self-service test links (lab) ---------------------------------------------------------------
//
// The lab session is self-managed: a tester signs in, picks a scenario, and hands the
// minted link to whoever is testing (or uses it themselves). Each row below is one
// issued link with its live session status — no SQL, no operator needed. The plain-
// language labels mirror docs/GLOSSARY.md so nobody has to decode tp/pe/arm naming.

// Family-pattern labels: new manifest versions of a family inherit the label
// automatically so the map never goes stale when a successor is published.
// Order matters — the nl and fielding-candidate families must match before the demos.
// UX v6: labels name the actual contrast axis — whether the two group-chat
// teammates draw on the local model (AI teammates) or the deterministic pool
// (scripted teammates) — plus the participant's ask surface.
const FAMILY_LABEL: [RegExp, string][] = [
  // v11+ dropped the solo_review phase (owner direction 2026-10-07); v2–v10 are
  // the archived review-first builds and keep the old label for archaeology.
  [/^tp_review_first_v(1[1-9]|[2-9]\d+)$/, "AI teammates · asks + chat"],
  [/^tp_review_first_v\d+$/, "Solo review · AI teammates"],
  [/^tp_ai_v\d+$/, "AI teammates · typed chat"],
  [/^tp_demo_nl_v\d+$/, "AI teammates · typed chat"],
  // v3+ (2026-10-08, clean 2x2): scripted teammates on the same typed-chat
  // surface as the AI build — the ONLY differences across launch cells are
  // pressure arm and teammate engine. v1/v2 kept the v11 ask picker.
  [/^tp_scripted_v([3-9]|\d{2,})$/, "Scripted teammates · typed chat"],
  [/^tp_scripted_v\d+$/, "Scripted teammates · ask picker"],
  [/^tp_demo_v\d+$/, "AI teammates · ask picker"],
  [/^pe_pilot_v\d+$/, "Peer-eval · scripted teammates"],
  [/^pe_demo_nl_v\d+$/, "Peer-eval · AI teammates · typed chat"],
  [/^pe_demo_v\d+$/, "Peer-eval · AI teammates"],
];

function profileLabel(manifestId: string): string {
  for (const [re, label] of FAMILY_LABEL) if (re.test(manifestId)) return label;
  return manifestId;
}

/** Newest issuable build: current consent copy AND not superseded by a newer
 * version of the same profile family (servers before UX round 3 send only
 * ``consent_current``). */
function isCurrent(c: {
  consent_current?: unknown;
  current?: unknown;
}): boolean {
  return typeof c.current === "boolean"
    ? c.current
    : Boolean(c.consent_current);
}

const ARM_LABEL: Record<string, string> = {
  high: "variant high",
  low: "variant low",
  mixed: "variant mixed",
  eval: "teammates evaluate the participant",
  control: "neutral framing (no evaluation)",
  nopress: "default",
};

const STAGE_LABEL: Record<string, string> = {
  consent: "Consent",
  instructions: "Instructions",
  practice: "Practice",
  connecting: "Connecting",
  solo_review: "Solo review",
  task: "Working on the task",
  checkin_collect: "Check-in pause",
  checkin_wait: "Check-in pause",
  checkin_reveal: "Check-in pause",
  answers_locked: "Answers locked",
  confidence: "Confidence rating",
  peer_ratings: "Peer ratings",
  post_questionnaire: "Post-task questions",
  open_ended: "Open-ended questions",
  debrief: "Debrief",
  data_use_choice: "Data-use choice",
  completion: "Finished — completion code shown",
};

const TERMINAL_LABEL: Record<string, string> = {
  participant_withdrawal: "Withdrawn by participant",
  consent_declined: "Declined at consent",
  heartbeat_timeout: "Timed out — browser closed or went silent",
  checkin_incomplete: "Stopped — check-in left unanswered",
  technical_stop: "Stopped by researcher",
};

function comboLabel(manifestId: string, arm: string): string {
  const profile = profileLabel(manifestId);
  if (arm === "nopress") return profile;
  const condition = ARM_LABEL[arm] ?? arm;
  return `${profile} — ${condition}`;
}

/** API paths like "/investigators/?pt=…" are origin-relative — clipboard
 * copies and shares need the absolute form. */
function absoluteUrl(url: string): string {
  return url.startsWith("http") ? url : `${window.location.origin}${url}`;
}

/** The combos a tester should normally see: the newest version of their
 * profile family, bound to the current consent copy, and carrying
 * ``material_status: approved`` — i.e. what a live batch could field today.
 * The pilot is the fielded run of that contract (spec §1, D13); everything
 * else is archive: superseded versions, demo/reference material, and
 * unapproved drafts (still mintable, but only deliberately). */
function isPilotContract(c: {
  current?: unknown;
  material_status?: unknown;
}): boolean {
  return isCurrent(c) && c.material_status === "approved";
}

/** Server-assigned test grouping (``GET /admin/preview-combos``): the
 * ``pilot_contract`` group is the fielded contract; ``comparison`` is the
 * newest nl_only build of the same study — the intended lab test set.
 * Falls back to the pilot-contract rule for older API responses. */
function testGroup(
  c: Record<string, unknown>,
): "pilot_contract" | "comparison" | "archive" {
  const g = c.test_group;
  if (g === "pilot_contract" || g === "comparison" || g === "archive") return g;
  return isPilotContract(c) ? "pilot_contract" : "archive";
}

type BatchesPayload = {
  batches?: Record<string, unknown>[];
  groups?: BatchGroup[];
};

/** Manifests fielded by an open live batch or batch group, mapped to the
 * batch/group ids fielding them. Reads ``GET /admin/batches``. When no live
 * group is open, the most recently created live group (API order is
 * created_at-ascending) is still the fielded contract — its members are
 * "the current conditions" — so the default link set doesn't fall back to
 * every launchable manifest between rounds. */
function fieldedManifestMap(
  data: BatchesPayload | null | undefined,
): Map<string, string[]> {
  const fieldedBy = new Map<string, string[]>();
  for (const b of data?.batches ?? []) {
    if (b.mode === "live" && b.enrollment_open === true) {
      const mid = String(b.manifest_id);
      fieldedBy.set(mid, [...(fieldedBy.get(mid) ?? []), String(b.batch_id)]);
    }
  }
  // Batch groups field their member manifests through the group link.
  const liveGroups = (data?.groups ?? []).filter((g) => g.mode === "live");
  const fielding = liveGroups.some((g) => g.enrollment_open)
    ? liveGroups.filter((g) => g.enrollment_open)
    : liveGroups.slice(-1);
  for (const g of fielding) {
    for (const mid of new Set(
      g.cells
        .filter((c) => c.allocatable !== false)
        .map((c) => String(c.manifest_id)),
    )) {
      const tag = g.enrollment_open ? String(g.group_id) : `${String(g.group_id)} (closed)`;
      fieldedBy.set(mid, [...(fieldedBy.get(mid) ?? []), tag]);
    }
  }
  return fieldedBy;
}

type AdminLink = {
  session: {
    stage: string;
    terminal_reason: string | null;
    deleted?: boolean;
    purge_pending?: { total: number } | null;
  } | null;
  expires_at: number | null;
};

function linkStatus(l: AdminLink): {
  text: string;
  tone: "idle" | "live" | "done" | "ended";
} {
  const expired = l.expires_at !== null && l.expires_at * 1000 < Date.now();
  const sess = l.session;
  if (sess === null)
    return expired
      ? { text: "Link expired — mint a new one", tone: "ended" }
      : { text: "No live session", tone: "idle" };
  if (sess.deleted) {
    // BA-39: honest tombstone label — distinguish a complete purge from rows
    // still awaiting the privileged cleanup run.
    const pending = sess.purge_pending?.total ?? 0;
    return pending > 0
      ? {
          text: `Tombstoned — ${pending} row${pending === 1 ? "" : "s"} awaiting privileged purge`,
          tone: "ended",
        }
      : { text: "Tombstoned — session data purged", tone: "ended" };
  }
  if (sess.stage === "completion")
    return { text: STAGE_LABEL.completion!, tone: "done" };
  if (sess.terminal_reason)
    return {
      text: `Ended — ${TERMINAL_LABEL[sess.terminal_reason] ?? sess.terminal_reason}`,
      tone: "ended",
    };
  const progress = `In progress — ${STAGE_LABEL[sess.stage] ?? sess.stage}`;
  return expired
    ? { text: `${progress} (entry link expired)`, tone: "live" }
    : { text: progress, tone: "live" };
}

const STATUS_TONE_COLOR: Record<string, string> = {
  idle: "#666",
  live: "#1a6ed2",
  done: "#1d7a35",
  ended: "#a33",
};

function TestLinks({
  role,
  appMode,
}: {
  role: Role;
  appMode: string | undefined;
}) {
  const combos = useFetch(() => adminApi.previewCombos());
  const links = useFetch(() => adminApi.myPreviews());
  const batches = useFetch(() => adminApi.batches());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [walkthroughSid, setWalkthroughSid] = useState<string | null>(null);

  // Gentle auto-refresh so a watcher can leave this page open during the session.
  useEffect(() => {
    const t = setInterval(links.reload, 15_000);
    return () => clearInterval(t);
  }, [links.reload]);

  const issue = (manifestId: string, arm: string) => {
    setBusy(true);
    setError(null);
    setMinted(null);
    adminApi
      .issuePreview(manifestId, arm, "faithful")
      .then(async (res) => {
        try {
          await navigator.clipboard.writeText(
            absoluteUrl(String(res.entry_url)),
          );
          setMinted(
            "Link minted and copied to clipboard — send it to your tester.",
          );
        } catch {
          setMinted("Link minted — copy it from My links below.");
        }
        links.reload();
      })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  const issueAll = () => {
    setBusy(true);
    setError(null);
    setMinted(null);
    // Mint the default-visible test set: while a live group is open that is
    // the fielded contract itself (preview-mode sessions — no allocation slot,
    // no entitlement); otherwise the lab comparison pair. Never the archive.
    const combos_ = combos.data?.combos ?? [];
    const fieldedNow = fieldedManifestMap(batches.data);
    const fielded = combos_.filter((c) =>
      fieldedNow.has(String(c.manifest_id)),
    );
    const all = fielded.length
      ? fielded
      : combos_.filter(
          (c) => testGroup(c as Record<string, unknown>) === "comparison",
        );
    Promise.all(
      all.map((c) =>
        adminApi.issuePreview(String(c.manifest_id), String(c.arm), "faithful"),
      ),
    )
      .then(() => {
        setMinted(
          `${all.length} links minted — copy each from My links below.`,
        );
        links.reload();
      })
      .catch(setError)
      .finally(() => setBusy(false));
  };
  // entry_url values are relative ("/investigators/?pt=…") — a clipboard copy
  // must be absolute or the tester gets a path, not a link. If the clipboard
  // API is unavailable or refused, surface the URL for manual copy instead of
  // failing silently (the button used to look dead — UX v6 follow-up).
  const copy = (nonce: string, url: string) => {
    const absolute = absoluteUrl(url);
    const fallback = () => window.prompt("Copy this link manually:", absolute);
    if (!navigator.clipboard?.writeText) {
      fallback();
      return;
    }
    navigator.clipboard
      .writeText(absolute)
      .then(() => setCopied(nonce))
      .catch(fallback);
  };

  const [showCleaned, setShowCleaned] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [minted, setMinted] = useState<string | null>(null);
  const allCombos = combos.data?.combos ?? [];
  // Manifests bound by an open live batch/group = the fielded contract.
  const fieldedBy = fieldedManifestMap(batches.data);
  const pilotCombos = allCombos.filter(
    (c) => testGroup(c as Record<string, unknown>) === "pilot_contract",
  );
  const comparisonCombos = allCombos.filter(
    (c) => testGroup(c as Record<string, unknown>) === "comparison",
  );
  const archivedCombos = allCombos.filter(
    (c) => testGroup(c as Record<string, unknown>) === "archive",
  );
  // While a live group is OPEN the default test set IS the fielded contract —
  // testers mint the exact conditions participants get. With nothing open the
  // newest approved contract (pilot_contract) leads — that is the contract the
  // next round fields. Only when no contract is published does the last-closed
  // live group's cells keep the default, then the lab comparison pair.
  const openFielded = new Set(
    [...fieldedBy].flatMap(([mid, tags]) =>
      tags.some((t) => !t.endsWith(" (closed)")) ? [mid] : [],
    ),
  );
  const openFieldedCombos = allCombos.filter((c) =>
    openFielded.has(String(c.manifest_id)),
  );
  const closedFieldedCombos = allCombos.filter(
    (c) =>
      fieldedBy.has(String(c.manifest_id)) &&
      !openFielded.has(String(c.manifest_id)),
  );
  const defaultCombos = openFieldedCombos.length
    ? openFieldedCombos
    : pilotCombos.length
      ? pilotCombos
      : closedFieldedCombos.length
        ? closedFieldedCombos
        : comparisonCombos;
  const visibleCombos = showArchived ? allCombos : defaultCombos;
  // Audit F04: report the *actual* gate — a current approved profile archived
  // only because a crate flag is off must say so, not fall through to the
  // misleading material-status reason. Both gates can apply (thin overlap
  // profiles); describe each that does.
  const archiveReason = (c: Record<string, unknown>): string => {
    const gates: string[] = [];
    if (
      combos.data?.scripted_teammates_enabled === false &&
      c.social_generation === "deterministic_only"
    )
      gates.push(
        "Scripted teammates disabled (SCRIPTED_TEAMMATES_ENABLED=0) — mintable deliberately for preview",
      );
    if (
      combos.data?.crate_overlap_enabled === false &&
      typeof c.crate_overlap === "string" &&
      c.crate_overlap !== "independent"
    )
      gates.push(
        `Crate overlap condition "${String(c.crate_overlap)}" disabled (CRATE_OVERLAP_ENABLED=0) — mintable deliberately for preview`,
      );
    if (combos.data?.crate_number_enabled === false && c.intel_budget != null)
      gates.push(
        `Thin case file (${String(c.intel_budget)} records) disabled (CRATE_NUMBER_ENABLED=0) — mintable deliberately for preview`,
      );
    if (gates.length) return gates.join("; ");
    return !isCurrent(c)
      ? c.consent_current
        ? "Superseded by a newer version of this profile"
        : `Legacy consent (${String(c.consent_key)}) — superseded or pointer-bound`
      : `Material status ${String(c.material_status)} — not the approved pilot contract`;
  };
  const rows = links.data?.links ?? [];
  // Dead links are hidden by default: tombstoned sessions, or the link's entry
  // window has closed and its session is over (or was never opened). A live
  // session under an expired link stays visible — the link, not the session,
  // is what expired.
  const linkDead = (l: AdminLink): boolean => {
    if (l.session?.deleted) return true;
    if (l.expires_at === null || l.expires_at * 1000 >= Date.now())
      return false;
    const sess = l.session;
    if (sess === null) return true;
    return Boolean(sess.terminal_reason) || sess.stage === "completion";
  };
  const cleanedCount = rows.filter(linkDead).length;
  const visible = showCleaned ? rows : rows.filter((l) => !linkDead(l));
  return (
    <section>
      <div className="admin-card">
        <h3>Run a test session</h3>
        <p className="admin-note">
          Pick a scenario to mint a one-session link, then send it to a tester
          (or open it yourself). One link = one session — reopening resumes it,
          so each tester needs their own. Links live 24 h. The default list is
          the current study builds available on this deployment. The older pilot
          contract, superseded, demo, and unapproved profiles sit behind the
          checkbox so a test link is never minted against the wrong build by
          accident.
        </p>
        {combos.data?.scripted_teammates_enabled === false && (
          <p className="admin-note" role="note">
            <strong>Scripted teammates are disabled</strong> on this deployment
            (<code>SCRIPTED_TEAMMATES_ENABLED=0</code>) — scripted-teammate
            builds sit behind the archive checkbox and stay mintable
            deliberately for preview.
          </p>
        )}
        {combos.data?.ai_assistant_enabled === false && (
          <p className="admin-note" role="note">
            <strong>The AI assistant is disabled</strong> on this deployment
            (<code>AI_ASSISTANT_ENABLED=0</code>) — sessions and previews omit
            the assistant pane, its practice page and its questionnaire items,
            and the playable mocks below use the <code>_noassist</code>{" "}
            variants.
          </p>
        )}
        {combos.data?.crate_overlap_enabled === false && (
          <p className="admin-note" role="note">
            <strong>Crate-overlap conditions are disabled</strong> on this
            deployment (<code>CRATE_OVERLAP_ENABLED=0</code>) — overlap-condition
            builds (12-crate none / 8-crate some / 4-crate full) sit behind the
            archive checkbox, stay mintable for preview, and refuse at consent
            on live batches.
          </p>
        )}
        {combos.data?.crate_number_enabled === false && (
          <p className="admin-note" role="note">
            <strong>Thin case-file conditions are disabled</strong> on this
            deployment (<code>CRATE_NUMBER_ENABLED=0</code>) — reduced-intel
            builds sit behind the archive checkbox, stay mintable for preview,
            and refuse at consent on live batches.
          </p>
        )}
        {combos.data?.ai_qa_enabled === true && (
          <p className="admin-note" role="note">
            <strong>The optional AI Q&amp;A page is enabled</strong> on this
            deployment (<code>AI_QA_ENABLED=1</code>) — sessions and previews
            see the "AI Q&amp;A (Optional)" page between the comprehension
            check and connecting, and the playable mocks below use the{" "}
            <code>_aiqa</code> variants.
          </p>
        )}
        {combos.data?.bonus_payment_enabled === false && (
          <p className="admin-note" role="note">
            <strong>The accuracy bonus is disabled</strong> on this deployment
            (<code>BONUS_PAYMENT_ENABLED=0</code>) — sessions and previews serve
            consent without the bonus-eligibility sentence, drop the
            background/gameplay/debrief bonus lines, and entitle $3.00 flat
            under the no-bonus policy. The playable mocks below use the{" "}
            <code>_nobonus</code> variants.
          </p>
        )}
        <ErrorNote error={combos.error} />
        <ErrorNote error={error} />
        {appMode === "live" && (
          <div className="admin-note" role="note">
            <strong>This deployment runs live mode.</strong> Links minted here
            create preview-mode test sessions — they never consume an allocation
            slot or a payable entitlement and are excluded from research
            exports. Purge them before the first real participant arrives
            (retention tab). You can also enter through a dedicated test batch —
            never the fielded batch:
            {[
              ...(batches.data?.batches ?? []),
              ...(batches.data?.groups ?? []).map((g) => ({
                ...g,
                batch_id: g.group_id,
              })),
            ]
              .filter(
                (b) =>
                  b.mode === "live" &&
                  b.enrollment_open === true &&
                  /smoke|qa|test/i.test(String(b.batch_id)),
              )
              .map((b) => {
                const url = `${window.location.origin}/investigators/?batch=${String(
                  b.batch_id,
                )}&participantId=TEST_NAME&projectId=test&assignmentId=test1`;
                return (
                  <div key={String(b.batch_id)} style={{ marginTop: 6 }}>
                    Test batch <code>{String(b.batch_id)}</code> (replace
                    TEST_NAME per tester):{" "}
                    <button onClick={() => copy(String(b.batch_id), url)}>
                      {copied === String(b.batch_id)
                        ? "copied"
                        : "copy test URL"}
                    </button>
                  </div>
                );
              })}
          </div>
        )}
        {minted && (
          <p role="status" className="admin-note">
            {minted}
          </p>
        )}
        <div className="admin-row" style={{ flexWrap: "wrap" }}>
          {visibleCombos.map((c) => {
            const mid = String(c.manifest_id);
            const group = testGroup(c as Record<string, unknown>);
            const fielded = fieldedBy.get(mid);
            return (
              <div className="combo-cell" key={`${mid}:${c.arm}`}>
                <button
                  disabled={busy}
                  title={
                    fielded && group !== "archive"
                      ? `Fielded live on ${fielded.join(", ")}`
                      : group === "pilot_contract"
                        ? "Approved pilot contract — not currently fielded"
                      : group === "comparison"
                        ? "Study preview build — creates a preview-mode test session"
                        : archiveReason(c as Record<string, unknown>)
                  }
                  onClick={() => {
                    if (
                      group === "archive" &&
                      !window.confirm(
                        `Mint a link for archived profile "${mid}"?\n\n${archiveReason(
                          c as Record<string, unknown>,
                        )}. This is not the fielded pilot contract — mint anyway?`,
                      )
                    )
                      return;
                    issue(mid, String(c.arm));
                  }}
                >
                  {fielded ? "⚑ " : ""}
                  {comboLabel(mid, String(c.arm))}
                  {group === "archive" ? " (archived)" : ""}
                </button>
                {typeof c.demo_url === "string" && c.demo_url && (
                  <a
                    className="admin-note"
                    href={`${window.location.origin}/investigators/${String(c.demo_url)}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    playable mock — walk the screens
                  </a>
                )}
                {Array.isArray(c.summary) && c.summary.length > 0 && (
                  <details className="combo-design">
                    <summary>what this build does</summary>
                    <ul>
                      {c.summary.map((line, i) => (
                        <li key={i}>{String(line)}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            );
          })}
          {defaultCombos.length > 0 && (
            <button disabled={busy} onClick={issueAll}>
              One of each ({defaultCombos.length} links)
            </button>
          )}
          {pilotCombos.length + archivedCombos.length > 0 && (
            <label className="admin-note" style={{ alignSelf: "center" }}>
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
              />{" "}
              show all builds — {pilotCombos.length} pilot contract +{" "}
              {archivedCombos.length} archived (superseded, demo, or
              unapproved)
            </label>
          )}
        </div>
        {defaultCombos.length === 0 && !combos.loading && (
          <p className="admin-note">
            No current study builds are available for preview — tick the box to
            reach the fielded contract or demo/superseded profiles for QA.
          </p>
        )}
      </div>
      <div className="admin-card">
        <h3>
          My links <button onClick={links.reload}>Refresh</button>
        </h3>
        <p className="admin-note">
          Every preview link you have minted — this list is a permanent record
          (the audit log), so links stay listed after use; they are never
          deleted, they only expire (24 h after issue). Each link opens or
          resumes exactly one session. "No live session" means the link was
          never opened <em>or</em> its session was wiped by the pre-launch
          reset — reopening it simply starts a fresh session. Expired or
          deleted links hide behind the checkbox below.
        </p>
        {cleanedCount > 0 && (
          <label className="admin-note" style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={showCleaned}
              onChange={(e) => setShowCleaned(e.target.checked)}
            />{" "}
            show {cleanedCount} expired/deleted link
            {cleanedCount === 1 ? "" : "s"}
          </label>
        )}
        <ErrorNote error={links.error} />
        {rows.length === 0 && !links.loading ? (
          <p className="admin-note">
            Nothing issued yet — every link you create above shows up here with
            its live status.
          </p>
        ) : visible.length === 0 ? (
          <p className="admin-note">
            All issued links have expired or been deleted — tick the box above
            to see them.
          </p>
        ) : (
          <table className="admin-table">
            <thead>
              <tr>
                <th>scenario</th>
                <th>link</th>
                <th>status</th>
                <th>session</th>
                <th>walkthrough</th>
                <th>issued</th>
                <th>expires</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((l) => {
                const url = `${window.location.origin}${l.entry_url}`;
                const st = linkStatus(l);
                return (
                  <tr key={l.nonce}>
                    <td>{comboLabel(String(l.manifest_id), String(l.arm))}</td>
                    <td>
                      <button onClick={() => copy(l.nonce, url)}>
                        {copied === l.nonce ? "copied!" : "copy link"}
                      </button>
                    </td>
                    <td style={{ color: STATUS_TONE_COLOR[st.tone] }}>
                      {st.text}
                    </td>
                    <td className="mono">
                      {l.session
                        ? String(l.session.session_id).slice(0, 8)
                        : "—"}
                    </td>
                    <td>
                      {l.session ? (
                        <button
                          onClick={() =>
                            setWalkthroughSid(
                              walkthroughSid === l.session!.session_id
                                ? null
                                : String(l.session!.session_id),
                            )
                          }
                        >
                          {walkthroughSid === l.session.session_id
                            ? "hide"
                            : "walkthrough"}
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {l.issued_at ? String(l.issued_at).slice(0, 16) : ""}
                    </td>
                    <td className="mono">
                      {l.expires_at !== null
                        ? new Date(Number(l.expires_at) * 1000)
                            .toISOString()
                            .slice(0, 16)
                        : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {walkthroughSid && <SessionDetail sid={walkthroughSid} role={role} />}
    </section>
  );
}

// ----- batches + previews ------------------------------------------------------------------------

function Batches({ role }: { role: Role }) {
  const batches = useFetch(() => adminApi.batches());
  const combos = useFetch(() => adminApi.previewCombos());
  const [batchId, setBatchId] = useState("");
  const [manifestId, setManifestId] = useState("tp_demo_v3");
  const [mode, setMode] = useState("qa");
  const [previewUrl, setPreviewUrl] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [showLegacyBatches, setShowLegacyBatches] = useState(false);
  const allBatches = (batches.data?.batches ?? []) as {
    batch_id: unknown;
    enrollment_open: unknown;
  }[];
  // Operative = fielded/smoke family or currently open; the rest is legacy
  // preview/qa scaffolding kept for audit — hidden behind the toggle.
  const operative = allBatches.filter(
    (b) =>
      b.enrollment_open === true ||
      /^(study|smoke)\./.test(String(b.batch_id)),
  );
  const legacy = allBatches.filter((b) => !operative.includes(b));
  const listed = showLegacyBatches ? allBatches : operative;
  return (
    <section>
      <div className="admin-card">
        <h3>Batch groups (one link, balanced across conditions)</h3>
        <p className="admin-note">
          This is the operative recruitment control. The fielded study runs
          through one group link (<code>study.live.vN</code>), which randomizes
          each consenting participant into one cell (member batch × enabled
          arm). Uncapped groups use balanced blocks. Quota-controlled groups
          randomly choose among the least-occupied eligible cells: valid
          completers and active participants hold capacity, and terminal
          dropouts free capacity for new assignments. Assignments are never
          rewritten. Quota groups pin their cells at creation and stop new
          assignments if deployment features change those cells. Decliners
          use no slot. Member batches stay
          closed — participants arrive only through the group link.{" "}
          <strong>Resume enrollment</strong> opens intake;{" "}
          <strong>Pause</strong> stops new entries but does not end sessions
          already running. <code>allocated</code> counts consented sessions
          assigned to each cell.
        </p>
        {(batches.data?.groups ?? []).map((g) => (
          <div
            key={g.group_id}
            className="admin-row"
            style={{ flexWrap: "wrap" }}
          >
            <span className="mono">{g.group_id}</span>
            <span>{g.mode}</span>
            <span>open={String(g.enrollment_open)}</span>
            <span>allocated={g.allocated}</span>
            {g.quota && (
              <span>
                {g.quota.test_only && "TEST ONLY (not payable) · "}
                {g.quota.platform} only · valid completers={g.quota.completed}/
                {g.quota.total_target} · active={g.quota.active} · available=
                {g.quota.available}
                {!g.quota.configuration_matches && " · configuration mismatch: intake blocked"}
                {g.quota.target_met && " · target reached"}
              </span>
            )}
            <span className="admin-note">
              {g.cells
                .map((c) =>
                  `${c.manifest_id}/${ARM_LABEL[c.arm] ?? c.arm}: ${c.allocated}${c.target !== undefined ? ` assigned, ${c.completed}/${c.target} valid completers, ${c.active} active, ${c.available} available` : ""}${c.allocatable === false ? " (historical)" : ""}`,
                )
                .join(" · ")}
            </span>
            {(role === "operator" || role === "researcher") && (
              <Action
                label={
                  g.enrollment_open ? "Pause enrollment" : "Resume enrollment"
                }
                run={() =>
                  adminApi
                    .setGroupEnrollment(
                      g.group_id,
                      !g.enrollment_open,
                      "console toggle",
                    )
                    .then(() => batches.reload())
                }
              />
            )}
          </div>
        ))}
      </div>
      <div className="admin-card">
        <h3>Batches (member + legacy)</h3>
        <p className="admin-note">
          A batch pins one manifest and a mode; the group distributes
          participants across its member batches' cells, so the{" "}
          <code>study.*.ai</code> / <code>study.*.scripted</code> members stay
          closed — opening a member directly would let participants enter
          outside the balanced group. Individual batches are only toggled for
          standalone QA/test intake — the create form below is for QA setups;
          group creation itself is a launch-time op done host-side. Day-to-day
          fielding needs only the group control above.
        </p>
        <ErrorNote error={batches.error} />
        {legacy.length > 0 && (
          <label className="admin-note" style={{ display: "block" }}>
            <input
              type="checkbox"
              checked={showLegacyBatches}
              onChange={(e) => setShowLegacyBatches(e.target.checked)}
            />{" "}
            show {legacy.length} legacy preview/qa batch
            {legacy.length === 1 ? "" : "es"} (closed scaffolding from earlier
            rounds — safe to ignore)
          </label>
        )}
        {role === "researcher" && (
          <div className="admin-row">
            <input
              placeholder="new batch id"
              value={batchId}
              onChange={(e) => setBatchId(e.target.value)}
            />
            <input
              placeholder="manifest"
              value={manifestId}
              onChange={(e) => setManifestId(e.target.value)}
            />
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="preview">preview</option>
              <option value="qa">qa</option>
              <option value="live">live</option>
            </select>
            <button
              onClick={() =>
                adminApi
                  .createBatch(batchId, manifestId, mode)
                  .then(() => batches.reload())
                  .catch(setError)
              }
            >
              Create (enrollment closed)
            </button>
          </div>
        )}
        {(role === "operator" || role === "researcher") &&
          listed.map((b) => (
            <div key={String(b.batch_id)} className="admin-row">
              <span className="mono">{String(b.batch_id)}</span>
              <span>open={String(b.enrollment_open)}</span>
              <Action
                label={
                  b.enrollment_open ? "Pause enrollment" : "Resume enrollment"
                }
                run={() =>
                  adminApi
                    .setEnrollment(
                      String(b.batch_id),
                      !b.enrollment_open,
                      "console toggle",
                    )
                    .then(() => batches.reload())
                }
              />
            </div>
          ))}
        <details className="admin-note">
          <summary>raw batch JSON ({allBatches.length} rows)</summary>
          <JsonBlock value={batches.data?.batches ?? []} />
        </details>
        <ErrorNote error={error} />
      </div>
      <div className="admin-card">
        <h3>Signed previews</h3>
        <ErrorNote error={combos.error} />
        {role === "researcher" ? (
          <div className="admin-row">
            {(combos.data?.combos ?? []).map((c) => (
              <Action
                key={`${c.manifest_id}:${c.arm}`}
                label={`${String(c.manifest_id)} / ${ARM_LABEL[String(c.arm)] ?? String(c.arm)}${isCurrent(c) ? "" : " (legacy)"}`}
                run={() =>
                  adminApi
                    .issuePreview(
                      String(c.manifest_id),
                      String(c.arm),
                      "faithful",
                    )
                    .then((r) => {
                      setPreviewUrl(r.entry_url);
                      return r;
                    })
                }
              />
            ))}
          </div>
        ) : (
          <JsonBlock value={combos.data?.combos ?? []} />
        )}
        {previewUrl && (
          <p>
            Entry link: <code>{previewUrl}</code>
          </p>
        )}
      </div>
    </section>
  );
}

// ----- retention ----------------------------------------------------------------------------------

function Retention({ role }: { role: Role }) {
  const status = useFetch(() => adminApi.retentionStatus());
  const [closeAt, setCloseAt] = useState("");
  return (
    <section>
      <div className="admin-card">
        <h3>Retention posture</h3>
        <p className="admin-note">
          <strong>Purpose:</strong> how platform-identity data (the
          Prolific/Connect IDs that link a person to a session) is scheduled
          for deletion. Per policy, platform IDs become purgeable{" "}
          <strong>48 h after study close</strong>; the actual deletion runs
          under a privileged host path, never this console.
        </p>
        <p className="admin-note">
          Field guide — <code>platform_links.unavailable</code> is{" "}
          <strong>expected</strong>: the console's database role is
          deliberately denied identity rows, so live counts come from the
          privileged <code>retention_purge.py</code> report instead.{" "}
          <code>deletion_tombstones</code> = sessions marked for deletion
          (withdrawal/data-use declines); <code>purge_pending_*</code> = their
          rows still awaiting the privileged physical purge.
        </p>
        <ErrorNote error={status.error} />
        <JsonBlock value={status.data} />
        <div className="admin-row">
          <input
            placeholder="study close ISO time"
            value={closeAt}
            onChange={(e) => setCloseAt(e.target.value)}
          />
          <Action
            label="Schedule platform-ID purge (+48h)"
            run={() =>
              adminApi.scheduleRetention(closeAt).then(() => status.reload())
            }
          />
          {role === "operator" && (
            <>
              <Action
                label="Purge due links (dry run)"
                run={() =>
                  adminApi.purgeLinks(true).then(() => status.reload())
                }
              />
              <Action
                label="Re-apply tombstones"
                confirm="Re-delete any restored tombstoned sessions?"
                run={() =>
                  adminApi.reapplyTombstones().then(() => status.reload())
                }
              />
              <Action
                label="Cleanup preview scope (dry run)"
                run={() => adminApi.cleanup("preview", true, "", false)}
              />
            </>
          )}
        </div>
        <p className="admin-note">
          <strong>When to use these:</strong> set the study-close timestamp once
          recruitment ends — it stamps every participant link with
          <code>purge_after = close + 48 h</code>. The two dry-run buttons are
          previews only: <strong>Purge due links</strong> shows what the
          privileged purge would delete once links come due, and{" "}
          <strong>Cleanup preview scope</strong> shows what removing leftover
          preview-mode test sessions would touch — the real deletion runs
          host-side. <strong>Re-apply tombstones</strong> re-deletes any
          tombstoned session that reappeared (e.g. after a restore). None of
          these touch paid-participant research data before its scheduled
          time.
        </p>
      </div>
    </section>
  );
}

// ----- documentation --------------------------------------------------------------------------------

function formatSize(n: number | undefined): string {
  if (n === undefined) return "—";
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function Docs() {
  const docs = useFetch(() => adminApi.docs());
  return (
    <section>
      <div className="admin-card">
        <h3>Project documentation</h3>
        <p className="admin-note">
          Researcher-gated files served live from the host tree. View opens the
          raw file in a new tab; Download saves a copy.
        </p>
        <ErrorNote error={docs.error} />
        <table className="admin-table">
          <thead>
            <tr>
              <th>document</th>
              <th>file</th>
              <th>size</th>
              <th>modified</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(docs.data?.docs ?? []).map((d) => (
              <tr key={d.id}>
                <td>{d.label}</td>
                <td>
                  <code>{d.file}</code>
                </td>
                <td>{formatSize(d.size)}</td>
                <td>{d.modified_at ?? "—"}</td>
                <td>
                  <a
                    href={adminApi.docUrl(d.id, false)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    view
                  </a>
                  {" · "}
                  <a href={adminApi.docUrl(d.id, true)}>download</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ----- compensation --------------------------------------------------------------------------------

function Compensation() {
  const queue = useFetch(() => adminApi.compensationQueue());
  const [paidRef, setPaidRef] = useState("");
  return (
    <section>
      <div className="admin-card">
        <h3>Compensation queue</h3>
        <ErrorNote error={queue.error} />
        <input
          placeholder="payment reference"
          value={paidRef}
          onChange={(e) => setPaidRef(e.target.value)}
        />
        <table className="admin-table">
          <thead>
            <tr>
              <th>session</th>
              <th>kind</th>
              <th>cents</th>
              <th>status</th>
              <th>outcome</th>
              <th>platform</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(queue.data?.rows ?? []).map((r) => (
              <tr key={`${r.session_id}:${r.kind}`}>
                <td className="mono">{String(r.session_id).slice(0, 8)}</td>
                <td>{String(r.kind)}</td>
                <td>{String(r.cents)}</td>
                <td>{String(r.status)}</td>
                <td>{String(r.completion_outcome ?? "")}</td>
                <td>{String(r.platform ?? "")}</td>
                <td>
                  {r.status === "approved" && (
                    <Action
                      label="Mark paid"
                      run={() =>
                        adminApi
                          .markPaid(
                            String(r.session_id),
                            String(r.kind),
                            paidRef,
                          )
                          .then(() => queue.reload())
                      }
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ----- shell ---------------------------------------------------------------------------------------

export function AdminApp() {
  const [me, setMe] = useState<{
    role: Role;
    actor: string;
    appMode: string | undefined;
  } | null>(null);
  const [checked, setChecked] = useState(false);
  const [tab, setTab] = useState<Tab>("links");
  const [sessionFocus, setSessionFocus] = useState<{
    id: string;
    n: number;
  } | null>(null);
  const openSession = useCallback((sid: string) => {
    setSessionFocus({ id: sid, n: Date.now() });
    setTab("sessions");
  }, []);
  // Mid-session auth loss (expired cookie / role revoked): a section fetch that
  // 401/403s drops the app back to the login gate instead of leaving stale
  // numbers on screen.
  const authLost = useCallback(() => setMe(null), []);

  useEffect(() => {
    adminApi
      .whoami()
      .then((w) => setMe({ role: w.role, actor: w.actor, appMode: w.app_mode }))
      .catch(() => setMe(null))
      .finally(() => setChecked(true));
  }, []);

  const tabs = useMemo(() => (me ? TABS_BY_ROLE[me.role] : []), [me]);
  useEffect(() => {
    const first = tabs[0];
    if (first && !tabs.includes(tab)) setTab(first);
  }, [tabs, tab]);

  if (!checked) return <main className="admin-shell">Checking session…</main>;
  if (!me)
    return (
      <main className="admin-shell">
        <Login
          onLogin={(role, actor, appMode) => setMe({ role, actor, appMode })}
        />
      </main>
    );
  return (
    <main className="admin-shell">
      <header className="admin-head">
        <h1>Investigators admin</h1>
        <span>
          {me.actor} · {me.role}
        </span>
        <button
          onClick={() =>
            adminApi
              .logout()
              .catch(() => undefined)
              .finally(() => setMe(null))
          }
        >
          Sign out
        </button>
      </header>
      <nav className="admin-nav">
        {tabs.map((t) => (
          <button
            key={t}
            className={t === tab ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      {tab === "links" && <TestLinks role={me.role} appMode={me.appMode} />}
      {tab === "sessions" && <Sessions role={me.role} focus={sessionFocus} />}
      {tab === "monitor" && <Monitor />}
      {tab === "batches" && <Batches role={me.role} />}
      {tab === "retention" && <Retention role={me.role} />}
      {tab === "documentation" && <Docs />}
      {tab === "dashboard" && (
        <Dashboard onOpenSession={openSession} onAuthLoss={authLost} />
      )}
      {tab === "compensation" && <Compensation />}
    </main>
  );
}
