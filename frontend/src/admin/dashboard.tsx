/**
 * Study Admin Dashboard (WP17 Phase A) — researcher-only read views over
 * committed rows. Nine sections under a second-level nav; analytic sections
 * (items/power/contrasts/findings) share one /admin/dashboard/analysis snapshot
 * fetched once per filter change. Every request carries an AbortController so
 * a stale response can never overwrite a newer filter's result.
 *
 * Phase A is descriptive/operational only: no causal claims, no significance
 * tests, no alpha/PCA — the backend reports those families as
 * analysis_not_configured / unsupported_model and this UI renders them as such.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { adminApi, AdminApiError, type BatchGroup, type DashFilters } from "./api";
import { FilterTh, distinct, useColFilters } from "./colfilter";

type Section =
  | "overview"
  | "exports"
  | "readiness"
  | "timing"
  | "instruments"
  | "items"
  | "chat"
  | "power"
  | "contrasts"
  | "findings";

const SECTIONS: { id: Section; label: string }[] = [
  { id: "overview", label: "Dashboard" },
  { id: "exports", label: "Export" },
  { id: "readiness", label: "Data Readiness" },
  { id: "timing", label: "Page Timing" },
  { id: "instruments", label: "Survey Documentation" },
  { id: "items", label: "Item Analytics" },
  { id: "chat", label: "Team Chat" },
  { id: "power", label: "Power Inputs" },
  { id: "contrasts", label: "Arm Contrasts" },
  { id: "findings", label: "Findings" },
];

const VALID = new Set(SECTIONS.map((s) => s.id));

type R = Record<string, unknown>;

function num(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(2)) : "—";
}

function str(v: unknown): string {
  return v === null || v === undefined ? "—" : String(v);
}

/** Milliseconds → human duration: "12.3 s" under a minute, "7m 14s" under an
 * hour, "1h 23m" beyond. Page-timing values only. */
function fmtMs(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const ms = Number(v);
  if (!Number.isFinite(ms)) return "—";
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  if (ms < 3_600_000) {
    const m = Math.floor(ms / 60_000);
    const s = Math.round((ms % 60_000) / 1000);
    return s === 60 ? `${m + 1}m` : `${m}m ${s}s`;
  }
  const h = Math.floor(ms / 3_600_000);
  const m = Math.round((ms % 3_600_000) / 60_000);
  return m === 60 ? `${h + 1}h` : `${h}h ${m}m`;
}

function errMsg(error: unknown): string {
  // DA-01: null/undefined means "no error" — String(null) would render the
  // literal text "null" as if it were an error message.
  if (error === null || error === undefined || error === "") return "";
  if (error instanceof AdminApiError) return `${error.kind} (${error.status})`;
  if (error instanceof DOMException && error.name === "AbortError") return "";
  return error instanceof Error ? error.message : String(error);
}

function Kv({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="admin-card" style={{ minWidth: 140 }}>
      <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{label}</div>
      <div style={{ fontSize: "1.4em", fontWeight: 600 }}>{str(value)}</div>
    </div>
  );
}

function Table({
  caption,
  head,
  rows,
}: {
  caption?: string | undefined;
  head: string[];
  rows: (string | number | React.ReactNode)[][];
}) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="admin-table">
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A `Table` whose heading cells can filter categories client-side:
 * `filterCols` lists the column indexes that get the ▾ popover; `rowVals[i]`
 * supplies the string category value for filterable columns ("" elsewhere —
 * those columns never filter). */
function FilterableTable({
  caption,
  head,
  filterCols,
  rows,
  vals,
}: {
  caption?: string | undefined;
  head: string[];
  filterCols: number[];
  rows: (string | number | React.ReactNode)[][];
  vals: string[][];
}) {
  const filters = useColFilters();
  const filterSet = new Set(filterCols);
  const shownIdx = rows
    .map((_, i) => i)
    .filter((i) => filters.pass(vals[i] ?? []));
  const shown = shownIdx.map((i) => rows[i] ?? []);
  return (
    <div style={{ overflowX: "auto" }}>
      {shown.length !== rows.length && (
        <p className="admin-note">
          {shown.length} of {rows.length} rows shown — column filters active.
        </p>
      )}
      <table className="admin-table">
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {head.map((h, i) =>
              filterSet.has(i) ? (
                <FilterTh
                  key={h}
                  col={i}
                  label={h}
                  values={distinct(vals.map((v) => v[i] ?? ""), (v) => v)}
                  hidden={filters.hidden[i]}
                  onToggle={filters.toggle}
                  onSet={filters.setCol}
                />
              ) : (
                <th key={h}>{h}</th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={shownIdx[i] ?? i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One fenced fetch generation — AbortController per request; stale
 * completions are dropped by the generation check. */
function useFenced() {
  const gen = useRef(0);
  const controllers = useRef<AbortController[]>([]);
  const fence = useCallback(() => {
    gen.current += 1;
    for (const c of controllers.current) c.abort();
    controllers.current = [];
    return gen.current;
  }, []);
  const run = useCallback(
    <T,>(fn: (signal: AbortSignal) => Promise<T>): Promise<T | null> => {
      const g = gen.current;
      const ctrl = new AbortController();
      controllers.current.push(ctrl);
      return fn(ctrl.signal).then(
        (v) => (g === gen.current ? v : null),
        (e: unknown) => {
          if (e instanceof DOMException && e.name === "AbortError") return null;
          if (g === gen.current) throw e;
          return null;
        },
      );
    },
    [],
  );
  useEffect(
    () => () => {
      for (const c of controllers.current) c.abort();
    },
    [],
  );
  return { fence, run };
}

/** Section fetch lifecycle (AG-03): fenced + auth-aware + stale-labeled.
 * A 401/403 clears the session (the parent drops to the login gate); previous
 * data stays visible but is marked "refreshing" until the new scope lands —
 * old numbers are never presented as belonging to the new filter state. */
function useDashData(
  fetcher: (signal: AbortSignal) => Promise<R>,
  deps: readonly unknown[],
  onAuthLoss: () => void,
) {
  const { fence, run } = useFenced();
  const [data, setData] = useState<R | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const hasData = useRef(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const authRef = useRef(onAuthLoss);
  authRef.current = onAuthLoss;

  useEffect(() => {
    fence();
    setLoading(true);
    if (hasData.current) setRefreshing(true);
    let stale = false;
    run((sig) => fetcherRef.current(sig))
      .then((d) => {
        if (!stale && d !== null) {
          hasData.current = true;
          setData(d);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (stale) return;
        if (e instanceof AdminApiError && (e.status === 401 || e.status === 403)) {
          authRef.current();
          return;
        }
        setError(e);
      })
      .finally(() => {
        if (!stale) {
          setLoading(false);
          setRefreshing(false);
        }
      });
    return () => {
      stale = true;
    };
    // `deps` is the caller-supplied dependency list of this generic section
    // loader; the linter cannot see through it (same pattern as AdminApp useLoad).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, refreshing };
}

/** Cohort scope as UTC wall-clock strings (``datetime-local`` shape). */
interface Scope {
  studyKey: string;
  batchGroupId: string;
  batchId: string;
  compat: string;
  mode: string;
  since: string;
  until: string;
}

const SCOPE_PARAMS: Record<keyof Scope, string> = {
  studyKey: "study_key",
  batchGroupId: "batch_group_id",
  batchId: "batch_id",
  compat: "compatibility_key",
  mode: "mode",
  since: "since",
  until: "until",
};

/** ``datetime-local`` carries no zone; the labels say UTC, so interpret the
 * entered wall time AS UTC (DA-21) — never in the browser's local zone. */
function scopeToUTC(v: string): string | undefined {
  if (!v) return undefined;
  const iso = v.length === 16 ? `${v}:00Z` : v.endsWith("Z") ? v : `${v}Z`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

/** ISO instant → UTC wall time for a ``datetime-local`` input. */
function utcToInput(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 16);
}

function readScope(): { scope: Scope; section: Section } {
  const q = new URLSearchParams(window.location.search);
  const sec = q.get("dsec");
  const scope: Scope = { studyKey: "", batchGroupId: "", batchId: "", compat: "", mode: "live", since: "", until: "" };
  for (const [k, p] of Object.entries(SCOPE_PARAMS) as [keyof Scope, string][]) {
    const v = q.get(p);
    if (v === null) continue;
    scope[k] = k === "since" || k === "until" ? utcToInput(v) : v;
  }
  return { scope, section: VALID.has(sec as Section) ? (sec as Section) : "overview" };
}

function writeScope(scope: Scope, section: Section, push: boolean) {
  const u = new URL(window.location.href);
  for (const p of Object.values(SCOPE_PARAMS)) u.searchParams.delete(p);
  for (const [k, p] of Object.entries(SCOPE_PARAMS) as [keyof Scope, string][]) {
    const v = scope[k];
    if (!v || (k === "mode" && v === "live")) continue;
    u.searchParams.set(p, k === "since" || k === "until" ? (scopeToUTC(v) ?? "") : v);
  }
  u.searchParams.set("dsec", section);
  // Section changes push a history entry so Back restores the previous
  // section+scope; per-keystroke filter edits replace — typing must not spam
  // history (DA-24).
  if (push) window.history.pushState(null, "", u.toString());
  else window.history.replaceState(null, "", u.toString());
}

export function Dashboard({
  onOpenSession,
  onAuthLoss,
}: {
  onOpenSession: (sid: string) => void;
  onAuthLoss: () => void;
}) {
  const init = useRef(readScope());
  const [section, setSection] = useState<Section>(init.current.section);
  const [scope, setScope] = useState<Scope>(init.current.scope);
  const [nonce, setNonce] = useState(0);

  // Version selector (DA-25 follow-up): batch groups are the fielding rounds —
  // the dropdown defaults to the newest ``study.live.*`` group (created_at
  // ascending from the API, so the last match is newest) so the dashboard
  // opens on the current round rather than the pooled historical cohort.
  // ``all`` = no group filter; an absent URL param re-defaults to latest.
  const groupsFetch = useDashData(() => adminApi.batches(), [], onAuthLoss);
  const groupList = useMemo(
    () => ((groupsFetch.data?.groups as BatchGroup[] | undefined) ?? []),
    [groupsFetch.data],
  );
  const latestGroup = useMemo(() => {
    const live = groupList.filter((g) => g.group_id.startsWith("study.live."));
    return (live.at(-1) ?? groupList.at(-1))?.group_id ?? "";
  }, [groupList]);
  const defaultApplied = useRef(false);
  useEffect(() => {
    if (defaultApplied.current || scope.batchGroupId !== "" || latestGroup === "") return;
    defaultApplied.current = true;
    setScope((s) => (s.batchGroupId === "" ? { ...s, batchGroupId: latestGroup } : s));
  }, [latestGroup, scope.batchGroupId]);
  // Hold section renders until the version default resolves — otherwise the
  // first fetch would flash the pooled cohort before snapping to the latest
  // round. On fetch error/empty, fall back to unscoped rather than blocking.
  const scopeReady =
    scope.batchGroupId !== "" || (!groupsFetch.loading && groupsFetch.data !== null) || groupsFetch.error != null;

  const filters = useCallback((): DashFilters => {
    const f: DashFilters = { mode: scope.mode };
    if (scope.studyKey) f.study_key = scope.studyKey;
    if (scope.batchGroupId && scope.batchGroupId !== "all")
      f.batch_group_id = scope.batchGroupId;
    if (scope.batchId) f.batch_id = scope.batchId;
    if (scope.compat) f.compatibility_key = scope.compat;
    const s = scopeToUTC(scope.since);
    const u = scopeToUTC(scope.until);
    if (s) f.since = s;
    if (u) f.until = u;
    return f;
  }, [scope]);

  // DA-24: the full validated scope lives in the URL — refresh, deep links,
  // back/forward and the session-detail round trip all restore it. Only
  // section changes push a history entry.
  const prevSection = useRef(section);
  useEffect(() => {
    const push = prevSection.current !== section;
    prevSection.current = section;
    writeScope(scope, section, push);
  }, [scope, section]);
  useEffect(() => {
    const onPop = () => {
      const r = readScope();
      // The browser already moved the URL — mark the section as current so
      // the write effect replaces rather than pushing a duplicate entry
      // (pushing here would clobber the forward stack).
      prevSection.current = r.section;
      setScope(r.scope);
      setSection(r.section);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const setField = (k: keyof Scope) => (e: { target: { value: string } }) =>
    setScope((s) => ({ ...s, [k]: e.target.value }));

  const pick = (s: Section) => setSection(s);
  const refresh = () => setNonce((n) => n + 1);

  // Auto-refresh: a dashboard tab left open during live fielding must not
  // silently freeze on an old snapshot — bump the fetch nonce when the tab
  // regains focus/visibility and on a 30 s poll while visible.
  useEffect(() => {
    const bump = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const iv = window.setInterval(bump, 30_000);
    document.addEventListener("visibilitychange", bump);
    window.addEventListener("focus", bump);
    return () => {
      window.clearInterval(iv);
      document.removeEventListener("visibilitychange", bump);
      window.removeEventListener("focus", bump);
    };
  }, []);

  return (
    <section>
      <nav className="admin-nav" aria-label="dashboard sections">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            className={s.id === section ? "active" : ""}
            onClick={() => pick(s.id)}
          >
            {s.label}
          </button>
        ))}
      </nav>
      <div className="admin-row" role="group" aria-label="cohort filters">
        <label>
          study
          <input value={scope.studyKey} onChange={setField("studyKey")} placeholder="investigators_*" />
        </label>
        <label>
          version
          <select value={scope.batchGroupId} onChange={setField("batchGroupId")}>
            {scope.batchGroupId === "" && <option value="">(latest…)</option>}
            {[...groupList].reverse().map((g) => (
              <option key={g.group_id} value={g.group_id}>
                {g.group_id}
              </option>
            ))}
            <option value="all">All</option>
          </select>
        </label>
        <label>
          batch
          <input value={scope.batchId} onChange={setField("batchId")} />
        </label>
        <label>
          compatibility
          <input value={scope.compat} onChange={setField("compat")} placeholder="ck1:… or unkeyed" />
        </label>
        <label>
          mode
          <select value={scope.mode} onChange={setField("mode")}>
            <option value="live">live</option>
            <option value="qa">qa</option>
            <option value="preview">preview</option>
            <option value="all">all</option>
          </select>
        </label>
        <label>
          since (UTC)
          <input type="datetime-local" value={scope.since} onChange={setField("since")} />
        </label>
        <label>
          until (UTC)
          <input type="datetime-local" value={scope.until} onChange={setField("until")} />
        </label>
        <button onClick={refresh}>Refresh</button>
      </div>
      {scopeReady && section === "overview" && <Overview filters={filters} nonce={nonce} onOpenSession={onOpenSession} onAuthLoss={onAuthLoss} />}
      {scopeReady && section === "exports" && <Exports filters={filters} nonce={nonce} onAuthLoss={onAuthLoss} />}
      {scopeReady && section === "readiness" && <Readiness filters={filters} nonce={nonce} onOpenSession={onOpenSession} onAuthLoss={onAuthLoss} />}
      {scopeReady && section === "timing" && <Timing filters={filters} nonce={nonce} onAuthLoss={onAuthLoss} />}
      {scopeReady && section === "instruments" && <Instruments filters={filters} nonce={nonce} onAuthLoss={onAuthLoss} />}
      {scopeReady &&
        (section === "items" || section === "power" || section === "contrasts" || section === "findings" || section === "chat") && (
        <Analysis section={section} filters={filters} nonce={nonce} onAuthLoss={onAuthLoss} />
      )}
    </section>
  );
}

// ----- overview --------------------------------------------------------------------------------

function Overview({
  filters,
  nonce,
  onOpenSession,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onOpenSession: (sid: string) => void;
  onAuthLoss: () => void;
}) {
  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashOverview(filters(), sig),
    [nonce, filters],
    onAuthLoss,
  );

  if (loading && !data) return <p>Loading…</p>;
  const msg = errMsg(error);
  if (msg) return <p className="admin-error">{msg}</p>;
  if (!data) return <p>No data.</p>;

  const cohort = (data.cohort ?? {}) as R;
  const excluded = (cohort.excluded ?? {}) as R;
  const modeCounts = (data.mode_counts ?? {}) as R;
  const stages = (data.stage_table ?? []) as R[];
  const outcomes = (data.outcomes ?? {}) as R;
  const anchors = (data.manipulation_anchors ?? {}) as R;
  const balance = (data.arm_balance ?? []) as R[];
  const fill = (data.batch_fill ?? []) as R[];
  const fidelity = (data.model_call_status_by_arm ?? {}) as R;
  const recent = (data.recent_sessions ?? []) as R[];
  const anomalies = (data.outcome_anomalies ?? []) as R[];

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        <strong>Purpose:</strong> cohort summary of the filtered scope — the
        study/batch/mode/time filters above apply to every card and table on
        this page. <strong>Eligible research N</strong> is the analytic
        denominator: live sessions that completed <em>and</em> whose result is
        scored — the exclusion line below lists why the rest don't count
        (e.g. <code>result_missing_or_unscored</code> for abandonments). Use the
        stage funnel and arm-balance blocks to watch intake; anomalies and
        model-call fidelity flag sessions worth opening on the Sessions tab.
      </p>
      <p className="admin-note">
        Snapshot {str(data.snapshot_time)} · selected {str(cohort.selected)} ·
        tombstoned excluded {str(cohort.tombstoned_excluded)} · research-eligible{" "}
        {str(cohort.eligible_research)}
      </p>
      <div className="admin-row">
        {Object.entries(modeCounts).map(([m, n]) => (
          <Kv key={m} label={`${m} sessions`} value={n} />
        ))}
        <Kv label="eligible research N" value={cohort.eligible_research} />
      </div>
      {(filters().mode ?? "live") === "live" && cohort.selected === 0 && (
        <p className="admin-note">
          No live sessions match these filters. Switch mode to qa/preview for
          diagnostics traffic — qa/preview rows never enter the research N.
        </p>
      )}
      {Object.keys(excluded).length > 0 && (
        <p className="admin-note">
          Excluded from research N:{" "}
          {Object.entries(excluded)
            .filter(([, n]) => Number(n) > 0)
            .map(([k, n]) => `${k}=${n}`)
            .join(", ") || "none"}
        </p>
      )}

      <h3>Stages — occupancy vs ever-entered</h3>
      <Table
        caption="Current stage occupancy vs sessions that ever entered each stage (branch-aware; TP task re-entry counts visits, PE peer_ratings is its own stage)."
        head={["stage", "current", "ever entered"]}
        rows={stages.map((r) => [str(r.stage), num(r.current), num(r.ever_entered)])}
      />

      <h3>Outcomes</h3>
      <Table
        caption="Durable completion_receipt outcomes; sessions without a receipt show stage/terminal state."
        head={["outcome", "sessions"]}
        rows={Object.entries(outcomes).map(([k, v]) => [k, num(v)])}
      />
      {anomalies.length > 0 && (
        <Table
          caption="Receipt/stage/result disagreements — anomalies, never silently reconciled."
          head={["session", "flag", "detail"]}
          rows={anomalies.map((a) => [
            <button key="b" onClick={() => onOpenSession(str(a.session_id))}>
              {str(a.session_id).slice(0, 8)}
            </button>,
            str(a.flag),
            str(a.stage ?? a.result_status ?? ""),
          ])}
        />
      )}

      <h3>Manipulation anchors</h3>
      <Table
        caption="Each anchor counted from its own committed seq — an exposure row alone is not proof of rendering."
        head={["kind", "scheduled", "committed", "received", "rendered", "acknowledged"]}
        rows={Object.entries(anchors).map(([k, a]) => {
          const x = a as R;
          return [k, num(x.scheduled), num(x.committed), num(x.received), num(x.rendered), num(x.acknowledged)];
        })}
      />

      <h3>Arm balance</h3>
      <Table
        caption="Sessions by compatibility_key × arm — unkeyed sessions are never pooled with keyed rows."
        head={["compatibility", "arm", "n", "assigned", "exposed", "completed", "terminal", "mean correct", "mean latency ms"]}
        rows={balance.map((r) => [
          <span key="k" className="mono">{str(r.compatibility_key).slice(0, 18)}</span>,
          str(r.arm),
          num(r.n_sessions),
          num(r.n_assigned),
          num(r.n_exposed),
          num(r.n_completed),
          num(r.n_terminal),
          num(r.mean_correct_total),
          num(r.mean_model_latency_ms),
        ])}
      />

      <h3>Batch fill</h3>
      <Table head={["batch", "manifest", "mode", "open", "slots", "used", "sessions in scope"]}
        rows={fill.map((r) => [
          str(r.batch_id), str(r.manifest_id), str(r.mode), str(r.enrollment_open),
          num(r.slots), num(r.slots_used), num(r.sessions_in_scope),
        ])}
      />

      <h3>Assistant generation and delivery</h3>
      <Table
        caption={
          "model_call rows are immutable history — partitions on superseded builds keep " +
          "the state recorded under that older deployed build, including past incident " +
          "alerts (the Oct 8–9 validation_rejected incident lives there). Those alerts are " +
          "history, not live failures: judge current operational health by 'current' rows " +
          "only. 'build' = the deployed source checksum."
        }
        head={["profile / arm / cohort", "build", "model", "intended / deterministic", "call outcomes", "generated / delivered / rendered", "acceptance", "alerts"]}
        rows={[...(((data.assistant_generation as R)?.partitions ?? []) as R[])]
          .sort((a, b) => Number(b.current === true) - Number(a.current === true))
          .map((r) => {
            const alerts = (r.alerts as string[]) ?? [];
            const current = r.current === true;
            return [
              `${str(r.profile)} / ${str(r.arm)} / ${str(r.cohort)}`,
              <span key="b" className="mono" title={str(r.build)}>
                {str(r.build).slice(0, 8)}
                {typeof r.current === "boolean" && (
                  <>
                    {" "}
                    <em className={current ? "ok" : "muted"}>
                      {current ? "current" : "superseded"}
                    </em>
                  </>
                )}
              </span>,
              str(r.model), `${str(r.intended_provider)} / ${str(r.intentional_deterministic)}`,
              JSON.stringify(r.call_statuses),
              `${str(r.generated_delivered)} / ${str(r.delivered)} / ${str(r.rendered)}`,
              r.acceptance == null ? "N/A" : `${(Number(r.acceptance) * 100).toFixed(1)}%`,
              current || alerts.length === 0 ? (
                alerts.join(", ")
              ) : (
                <em className="muted">{alerts.join(", ")} (historical)</em>
              ),
            ];
          })}
      />
      <h3>Model-call status by arm (operational fidelity)</h3>
      <Table
        head={["arm", "statuses"]}
        rows={Object.entries(fidelity).map(([arm, st]) => [
          arm,
          Object.entries(st as R).map(([k, v]) => `${k}=${v}`).join(", "),
        ])}
      />

      <h3>Recent sessions</h3>
      <Table
        caption={`Most recent ${recent.length} selected sessions (bounded ${str(data.recent_limit)}).`}
        head={["session", "pid", "batch", "stage", "arm", "mode", "created", "terminal"]}
        rows={recent.map((r) => [
          <button key="b" onClick={() => onOpenSession(str(r.session_id))}>
            {str(r.session_id).slice(0, 8)}
          </button>,
          str(r.research_pid),
          str(r.batch_id),
          str(r.stage),
          str(r.arm),
          str(r.mode),
          str(r.created_at).slice(0, 19),
          str(r.terminal_reason),
        ])}
      />
    </div>
  );
}

// ----- exports ---------------------------------------------------------------------------------

// Per-table CSV downloads (PCS-style). Each card streams the identical table
// definition the published study export writes; the server resolves the filter
// bar's scope to an explicit session list (empty scope refuses, never widens).
const EXPORT_CARDS: { name: string; label: string; desc: string }[] = [
  { name: "participant_wide", label: "Participants", desc: "one row per session — arm, versions, status, behavioral summaries incl. telemetry measures (attention, file views/dwell/scroll/reorder, share channel, return latency + family states)" },
  { name: "session", label: "Sessions", desc: "registry rows — stage, arm, compatibility key, version stamps" },
  { name: "crate_long", label: "Crates — long", desc: "per session × crate — truth, answer, confidence, acquisition + world-relative latest/stale view flags" },
  { name: "event", label: "Events", desc: "committed event log incl. telemetry_* rows (kind, client_epoch, client_ms, active_ms, flush stage) — payloads verbatim (use-all sessions only)" },
  { name: "message", label: "Team chat", desc: "chat log — all message text verbatim (use-all sessions only)" },
  { name: "record", label: "Records", desc: "evidence cards" },
  { name: "share", label: "Shares", desc: "record shares into team chat" },
  { name: "file_membership", label: "Case file", desc: "file membership — how each card arrived" },
  { name: "evidence_transfer", label: "Evidence transfer", desc: "per session × record — acquisition, view/dwell and return-latency reachability" },
  { name: "item_response", label: "Post-task items", desc: "instrument responses — question wording follows each response; open-ended answers also split into response1 (crate location) / response2 (source reliance)" },
  { name: "confidence_response", label: "Confidence", desc: "per-crate confidence ratings" },
  { name: "request_episode", label: "Requests", desc: "scripted teammate requests and resolution" },
  { name: "model_call", label: "Model calls", desc: "generation audit — all prompt/response text verbatim (use-all sessions only)" },
  { name: "chat_text", label: "Chat text — verbatim", desc: "all session free text in one file — chat message bodies, model-call prompts & responses, event payloads (use-all sessions only)" },
];

function Exports({
  filters,
  nonce,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onAuthLoss: () => void;
}) {
  const { data, error } = useDashData(
    (sig) => adminApi.exportTableNames(sig),
    [nonce],
    onAuthLoss,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [other, setOther] = useState("");

  const download = async (name: string) => {
    setBusy(name);
    setErr(null);
    setDone(null);
    try {
      const resp = await adminApi.exportTableCsv(name, filters());
      if (!resp.ok) {
        if (resp.status === 401 || resp.status === 403) {
          onAuthLoss();
          return;
        }
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const cd = resp.headers.get("content-disposition") ?? "";
      const fname = /filename="([^"]+)"/.exec(cd)?.[1] ?? `investigators_${name}.csv`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname;
      a.click();
      URL.revokeObjectURL(url);
      setDone(name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "download failed");
    } finally {
      setBusy(null);
    }
  };

  const all = ((data?.tables ?? []) as string[]).filter(
    (n) => !EXPORT_CARDS.some((c) => c.name === n),
  );
  const msg = errMsg(error);

  return (
    <div>
      <p className="admin-note">
        Downloads honor the dashboard filters (study / batch / compatibility / mode /
        time window). The server resolves the filter scope to an explicit session
        list — an empty scope refuses rather than widening. Tombstoned and
        withdrawn sessions are excluded; payment and identity linkage never appear
        in researcher exports. On text-bearing tables (chat, events, model calls)
        sessions without an explicit use-all data-use choice are absent; all
        participant and model free text exports verbatim (2026-10-16 owner
        direction — no exportable file carries withheld markers).
        Audited publication copies (manifest + hashes, all tables) remain
        available via the POST /admin/exports API.
      </p>
      <div className="admin-grid">
        {EXPORT_CARDS.map((c) => (
          <div key={c.name} className="admin-card">
            <h3>{c.label}</h3>
            <p className="admin-note">{c.desc}</p>
            <button disabled={busy !== null} onClick={() => void download(c.name)}>
              {busy === c.name ? "Downloading…" : "Download CSV"}
            </button>
          </div>
        ))}
      </div>
      <div className="admin-card">
        <h3>Other tables</h3>
        <div className="admin-row">
          <select
            aria-label="Other export table"
            value={other}
            onChange={(e) => setOther(e.target.value)}
          >
            <option value="">select a table…</option>
            {all.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <button
            disabled={busy !== null || other === ""}
            onClick={() => void download(other)}
          >
            {busy === other ? "Downloading…" : "Download CSV"}
          </button>
        </div>
      </div>
      {done !== null && <p className="admin-note">Downloaded {done}.csv</p>}
      {err !== null && <p className="admin-error">{err}</p>}
      {msg && <p className="admin-error">{msg}</p>}
    </div>
  );
}

// ----- readiness --------------------------------------------------------------------------------

function Readiness({
  filters,
  nonce,
  onOpenSession,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onOpenSession: (sid: string) => void;
  onAuthLoss: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const [auditScope, setAuditScope] = useState("completers");
  const pageSize = 50;

  useEffect(() => setOffset(0), [nonce, filters, auditScope]);

  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashReadiness(filters(), pageSize, offset, auditScope, sig),
    [nonce, filters, offset, auditScope],
    onAuthLoss,
  );

  const msg = errMsg(error);
  if (msg) return <p className="admin-error">{msg}</p>;
  if (loading && !data) return <p>Loading…</p>;
  if (!data) return <p>No data.</p>;

  const summary = (data.flag_summary ?? {}) as R;
  const defs = (data.flag_definitions ?? []) as R[];
  const audit = (data.audit ?? {}) as R;
  const rows = (audit.rows ?? []) as R[];
  const total = Number(audit.total ?? 0);

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        Per-session coverage vs the *bound* manifest/artifact expectations — flags
        are review signals, never a blanket usable/unusable verdict. Each flag
        counts affected sessions once.
      </p>
      <p className="admin-note">
        <strong>How to use:</strong> the audit scope narrows the per-session
        table — <em>completers</em> (completed receipt) for pilot review,{" "}
        <em>incomplete</em> for dropouts, <em>exceptional</em> for terminal
        exits like technical stops, <em>all</em> for everything in scope. The
        flag summary shows which issues are common before you open individual
        sessions; the flag definitions expander explains each code's severity.
      </p>
      <div className="admin-row" role="group" aria-label="audit scope">
        <label>
          audit scope
          <select value={auditScope} onChange={(e) => setAuditScope(e.target.value)}>
            <option value="completers">completers (completed receipt)</option>
            <option value="incomplete">incomplete (ended, not completed)</option>
            <option value="exceptional">exceptional (terminal exits)</option>
            <option value="all">all selected</option>
          </select>
        </label>
      </div>
      <p className="admin-note">
        <strong>Telemetry column:</strong> persisted client-telemetry events ·
        distinct client epochs · file-collector marker (meta✓) · chat
        receive/render receipts. "absent" on a completer means the client
        queue never persisted — a review flag, same as the telemetry_* flag
        codes. Rows without epoch stamps predate the instrumented build.
      </p>
      <Table
        caption="Flag summary across selected sessions (sessions per flag)"
        head={["flag", "sessions"]}
        rows={Object.entries(summary)
          .filter(([, n]) => Number(n) > 0)
          .map(([k, v]) => [k, num(v)])}
      />
      <details>
        <summary>Flag definitions</summary>
        <Table
          head={["code", "severity", "category"]}
          rows={defs.map((d) => [str(d.code), str(d.severity), str(d.category)])}
        />
      </details>
      <Table
        caption={`${auditScope === "completers" ? "Completer" : auditScope} audit — rows ${total === 0 ? 0 : offset + 1}–${Math.min(offset + pageSize, total)} of ${total}`}
        head={["session", "pid", "stage", "arm", "mode", "variant", "outcome", "data-use", "items a/s/m", "telemetry", "flags"]}
        rows={rows.map((r) => {
          const ic = (r.item_coverage ?? {}) as R;
          const coverage =
            ic.unavailable !== undefined
              ? `n/a (${str(ic.unavailable)})`
              : `${num(ic.answered)}/${num(ic.skipped)}/${num(ic.missing)}+${num(ic.absent_expected)} absent`;
          const tel = (r.telemetry ?? {}) as R;
          const telCell =
            tel.events === undefined
              ? "—"
              : Number(tel.events) === 0
                ? "absent"
                : `${num(tel.events)}ev · ${num(tel.epochs)}ep · ` +
                  `${tel.file_meta === true ? "meta✓" : "meta—"} · ${num(tel.receipts)}rc`;
          return [
            <button key="b" onClick={() => onOpenSession(str(r.session_id))}>
              {str(r.session_id).slice(0, 8)}
            </button>,
            str(r.research_pid),
            str(r.stage),
            str(r.arm),
            str(r.mode),
            str(r.variant),
            str(r.outcome),
            str(r.data_use),
            coverage,
            telCell,
            ((r.flags ?? []) as string[]).join(", ") || "—",
          ];
        })}
      />
      <div className="admin-row">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>
          Previous
        </button>
        <button disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)}>
          Next
        </button>
      </div>
    </div>
  );
}

// ----- timing ------------------------------------------------------------------------------------

function Timing({
  filters,
  nonce,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onAuthLoss: () => void;
}) {
  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashTiming(filters(), sig),
    [nonce, filters],
    onAuthLoss,
  );

  const msg = errMsg(error);
  if (msg) return <p className="admin-error">{msg}</p>;
  if (loading && !data) return <p>Loading…</p>;
  if (!data) return <p>No data.</p>;

  const totals = (data.session_totals ?? []) as R[];
  const wall = (data.wall_durations ?? []) as R[];
  const active = (data.active_durations ?? []) as R[];
  const checkin = (data.checkin ?? []) as R[];
  const budget = (data.task_budget ?? []) as R[];
  const visits = (data.stage_visits ?? []) as R[];
  const drift = (data.deadline_drift ?? []) as R[];
  const notes = (data.notes ?? []) as string[];

  const dur = (r: R) => [
    <span key="p" className="mono">{str(r.partition)}</span>,
    str(r.arm),
    str(r.stage),
    num(r.n),
    fmtMs(r.median),
    fmtMs(r.iqr),
    num(r.censored),
  ];

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        <strong>Purpose:</strong> how long sessions actually took vs the
        designed clock. <strong>Wall</strong> durations are real elapsed time
        per stage; <strong>active</strong> durations count only time the task
        clock was running (idle/away excluded) — the manipulation-relevant
        number. The check-in and task-budget tables below verify participants
        hit the rating window and the task ran its full bound active budget (360 s through 2026-10-08 profiles, 300 s from tp_ai_v4 / tp_scripted_v6)
        in both arms — a materially shorter or longer active total would signal
        a clock or pacing defect, since the task length is identical across
        conditions.
      </p>
      <h3>Total study time — entry to final stage</h3>
      <FilterableTable
        caption="Wall time from session entry to reaching the final stage (median/IQR per partition × arm). Only sessions that reached a terminal stage count — in-progress sessions are not averaged."
        head={["partition", "arm", "ended at stage", "n", "median", "IQR"]}
        filterCols={[0, 1, 2]}
        vals={totals.map((r) => [str(r.partition), str(r.arm), str(r.stage)])}
        rows={totals.map((r) => [
          <span key="p" className="mono">{str(r.partition)}</span>,
          str(r.arm),
          str(r.stage),
          num(r.n),
          fmtMs(r.median),
          fmtMs(r.iqr),
        ])}
      />
      <h3>Stage durations — wall</h3>
      <FilterableTable
        caption="Median/IQR wall time per stage visit by partition (study|compat|mode|timing) x arm. Open visits are right-censored at the snapshot, counted but never averaged."
        head={["partition", "arm", "stage", "n", "median", "IQR", "censored"]}
        filterCols={[0, 1, 2]}
        vals={wall.map((r) => [str(r.partition), str(r.arm), str(r.stage)])}
        rows={wall.map(dur)}
      />
      <h3>Stage durations — active (task clock)</h3>
      <FilterableTable
        caption="Active time inside running clock intervals, factor-scaled for accelerated QA. Non-task stages are n/a — the task clock never runs there."
        head={["partition", "arm", "stage", "n", "median", "IQR", "censored"]}
        filterCols={[0, 1, 2]}
        vals={active.map((r) => [str(r.partition), str(r.arm), str(r.stage)])}
        rows={active.map(dur)}
      />
      <h3>Check-in segments</h3>
      <FilterableTable
        head={["partition", "arm", "stage", "n", "median", "IQR", "censored"]}
        filterCols={[0, 1, 2]}
        vals={checkin.map((r) => [str(r.partition), str(r.arm), str(r.stage)])}
        rows={checkin.map(dur)}
      />
      <h3>Task-budget consumption</h3>
      <FilterableTable
        caption="Observed task-clock consumption (task + solo_review, pauses excluded) vs each session's bound active budget. Censored rows have an open visit — the total can still grow; over-budget is undecidable until closed."
        head={["session", "partition", "arm", "observed active", "observed wall", "budget", "factor", "censored", "over"]}
        filterCols={[1, 2]}
        vals={budget.map((r) => [
          str(r.session_id), str(r.partition), str(r.arm),
        ])}
        rows={budget.map((r) => [
          <span key="s" className="mono">{str(r.session_id).slice(0, 8)}</span>,
          <span key="p" className="mono">{str(r.partition)}</span>,
          str(r.arm),
          fmtMs(r.observed_active_ms),
          fmtMs(r.observed_wall_ms),
          fmtMs(r.budget_ms),
          num(r.clock_speed_factor),
          str(r.censored),
          r.over_budget === null || r.over_budget === undefined ? "?" : str(r.over_budget),
        ])}
      />
      <h3>Stage visits — per session</h3>
      <FilterableTable
        caption="Wall time per stage visit per session — the per-session detail behind the aggregate table above. Filter by session id to see where a slow participant spent their time (idle/away shows up as a long instructions or training visit). Open visits report observed-so-far time."
        head={["session", "partition", "arm", "stage", "wall", "open"]}
        filterCols={[0, 1, 2, 3]}
        vals={visits.map((r) => [
          str(r.session_id), str(r.partition), str(r.arm), str(r.stage),
        ])}
        rows={visits.map((r) => [
          <span key="s" className="mono">{str(r.session_id).slice(0, 8)}</span>,
          <span key="p" className="mono">{str(r.partition)}</span>,
          str(r.arm),
          str(r.stage),
          fmtMs(r.wall_ms),
          str(r.open),
        ])}
      />
      <h3>Deadline drift — timer 0:00 to next action</h3>
      <FilterableTable
        caption="Per task_deadline event (the moment the on-page timer hit 0:00 and answers froze): how idle the participant already was when it fired (gap since their last action, with that action's type) and how long until their next real action (with its type — usually the continue click, stage_proceeded). Telemetry flushes never count as actions. No next-action yet = censored."
        head={["session", "partition", "arm", "deadline at", "idle before", "idle since", "next action in", "next action", "censored"]}
        filterCols={[0, 1, 2]}
        vals={drift.map((r) => [str(r.session_id), str(r.partition), str(r.arm)])}
        rows={drift.map((r) => [
          <span key="s" className="mono">{str(r.session_id).slice(0, 8)}</span>,
          <span key="p" className="mono">{str(r.partition)}</span>,
          str(r.arm),
          str(r.deadline_at).slice(11, 19),
          r.idle_before_ms === null || r.idle_before_ms === undefined ? "—" : fmtMs(r.idle_before_ms),
          str(r.idle_before_type ?? "—"),
          r.next_action_ms === null || r.next_action_ms === undefined ? "—" : fmtMs(r.next_action_ms),
          str(r.next_action_type ?? "—"),
          str(r.censored),
        ])}
      />
      {notes.length > 0 && (
        <ul>
          {notes.map((n, i) => (
            <li key={i} className="admin-note">{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ----- instruments ---------------------------------------------------------------------------------

function Instruments({
  filters,
  nonce,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onAuthLoss: () => void;
}) {
  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashInstruments(filters(), sig),
    [nonce, filters],
    onAuthLoss,
  );

  const msg = errMsg(error);
  if (msg) return <p className="admin-error">{msg}</p>;
  if (loading && !data) return <p>Loading…</p>;
  if (!data) return <p>No data.</p>;

  const artifacts = (data.artifacts ?? []) as R[];
  const codeDefined = (data.code_defined_instruments ?? []) as R[];
  const codebook = (data.codebook ?? {}) as R;
  const measures = (codebook.measures ?? []) as R[];
  const notes = (data.notes ?? []) as string[];

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        <strong>Purpose:</strong> the verbatim participant-facing wording of
        every artifact the filtered cohort's sessions actually bound (consent,
        instruments, debrief, prompts) plus the codebook's measure
        definitions — this is the audit trail for "what exactly did
        participants see." Wording resolved through the baked ContentStore at
        the *bound* key+hash —
        a missing bound artifact is labeled, never substituted with a newer version.
        Only participant-facing copy kinds publish bodies; model prompts and
        payment/policy payloads are provenance-only.
      </p>
      {artifacts.map((a, i) => {
        const body = (a.body ?? {}) as R;
        const items = (body.items ?? []) as R[];
        const resp = (body.response ?? {}) as R;
        const copyEntries = Object.entries(body).filter(
          ([k, v]) =>
            typeof v === "string" &&
            !["kind", "material", "format", "arm", "selection", "repeat_policy", "classifier", "freeze_decision"].includes(k),
        );
        return (
          <details key={i} className="admin-card">
            <summary>
              <strong>{str(a.slot)}</strong> — <span className="mono">{str(a.key)}</span> ·{" "}
              {str(a.sessions)} sessions · {str(a.status)}
              {a.hash_match === false ? " (HASH MISMATCH)" : ""}
            </summary>
            <p className="admin-note">
              artifact {str(a.artifact_id)} v{str(a.version)} ·{" "}
              {str(a.bound_sha256).slice(0, 16)}…
            </p>
            {body.restricted === true && (
              <p className="admin-note">Body restricted ({str(body.note)}).</p>
            )}
            {body.pointer_only === true && <p className="admin-note">Pointer-only historical reference — non-literal.</p>}
            {body.text !== null && typeof body.text === "object" && (
              <pre className="admin-json">{JSON.stringify(body.text, null, 2)}</pre>
            )}
            {copyEntries.length > 0 && (
              <Table
                head={["field", "text"]}
                rows={copyEntries.map(([k, v]) => [
                  <span key="f" className="mono">{k}</span>,
                  str(v),
                ])}
              />
            )}
            {body.templates !== undefined && (
              <Table
                head={["template"]}
                rows={((body.templates ?? []) as string[]).map((t) => [t])}
              />
            )}
            {body.rules !== undefined && (
              <Table
                head={["when", "lines"]}
                rows={((body.rules ?? []) as R[]).map((r) => [
                  str(r.when),
                  ((r.lines ?? []) as string[]).join(" | "),
                ])}
              />
            )}
            {items.length > 0 && (
              <>
                <p className="admin-note">
                  applies_to={str(body.applies_to)} · order={str(body.order)} ·
                  response {str(resp.type)} {str(resp.min)}–{str(resp.max)}
                </p>
                <Table
                  head={["item_id", "text", "skippable", "options"]}
                  rows={items.map((it) => [
                    str(it.item_id),
                    str(it.text),
                    str(it.skippable),
                    ((it.options ?? []) as string[]).join(" | ") || "—",
                  ])}
                />
              </>
            )}
          </details>
        );
      })}
      {codeDefined.length > 0 && (
        <>
          <h3>Code-defined instruments</h3>
          <p className="admin-note">
            Participant-facing instruments that live in code rather than bound
            artifacts — the open_ended follow-up pair asked between the
            questionnaire and the debrief, and the comprehension gate at the end
            of training. Free-text responses count as answered in analytics but
            the prose is withheld from dashboards and table downloads
            (unreviewed participant text). Comprehension answers persist as
            training_step events, not item_response.
          </p>
          {codeDefined.map((inst, i) => {
            const items = (inst.items ?? []) as R[];
            const resp = (inst.response ?? {}) as R;
            const hasCorrect = items.some((it) => it.correct !== undefined);
            return (
              <details key={i} className="admin-card">
                <summary>
                  <strong>{str(inst.instrument_id)}</strong> —{" "}
                  <span className="mono">code-defined</span> · v{str(inst.version)} ·{" "}
                  {str(inst.origin)}
                </summary>
                <p className="admin-note">
                  applies_to={str(inst.applies_to)} · response {str(resp.type)}
                  {resp.max !== undefined ? ` (max ${str(resp.max)} chars)` : ""}
                  {inst.storage !== undefined ? ` · stored as ${str(inst.storage)}` : ""}
                </p>
                <Table
                  head={hasCorrect ? ["item_id", "text", "correct", "skippable"] : ["item_id", "text", "skippable"]}
                  rows={items.map((it) => {
                    // Audit A01: when the effective key varies by overlap
                    // level (same_crates under `full`), show the per-level
                    // map — the static canonical value alone would
                    // misrepresent full-overlap sessions.
                    const byLevel = (it.correct_by_overlap ?? {}) as R;
                    const levels = Object.keys(byLevel);
                    const varies =
                      new Set(levels.map((l) => String(byLevel[l]))).size > 1;
                    const single = levels.length === 1 ? levels[0] : undefined;
                    const correctCell = varies
                      ? levels
                          .map((l) => `${l}: ${String(byLevel[l])}`)
                          .join(" · ")
                      : single !== undefined
                        ? String(byLevel[single])
                        : str(it.correct);
                    return hasCorrect
                      ? [str(it.item_id), str(it.text), correctCell, str(it.skippable)]
                      : [str(it.item_id), str(it.text), str(it.skippable)];
                  })}
                />
              </details>
            );
          })}
        </>
      )}
      <h3>Measure codebook</h3>
      <p className="admin-note">
        Codebook version {str(codebook.codebook_version)} — generated registry;
        docs/CODEBOOK.md is presentation of this, not a separate truth.
      </p>
      <Table
        head={["key", "name", "denominator", "missing rule"]}
        rows={measures.map((m) => [
          <span key="k" className="mono">{str(m.key)}</span>,
          str(m.name),
          str(m.denominator),
          <span key="mr" className="mono">{str(m.missing_rule)}</span>,
        ])}
      />
      <h3>Telemetry vocabulary</h3>
      <p className="admin-note">
        Whitelisted client telemetry kinds — each persists as a
        `telemetry_&lt;kind&gt;` event whose payload is
        `{"{client_ms, client_epoch, data}"}`. client_ms is comparable only
        within one client_epoch (a page load); event.stage/active_ms are
        flush-time stamps. Best-effort: dropped batches mean missing, never
        zero — `file_meta` is the marker separating "instrumented, nothing
        observed" from "not observed".
      </p>
      <Table
        caption="Telemetry event kinds (server whitelist; CODEBOOK.md §telemetry)"
        head={["kind", "emitted by", "payload.data", "notes"]}
        rows={((codebook.telemetry_kinds ?? []) as R[]).map((k) => [
          <span key="k" className="mono">telemetry_{str(k.kind)}</span>,
          str(k.emitted_by),
          <span key="p" className="mono">{str(k.payload)}</span>,
          str(k.notes),
        ])}
      />
      {notes.map((n, i) => (
        <p key={i} className="admin-note">{n}</p>
      ))}
    </div>
  );
}

// ----- analysis (items / power / contrasts / findings — one snapshot) ------------------------------

function Analysis({
  section,
  filters,
  nonce,
  onAuthLoss,
}: {
  section: Section;
  filters: () => DashFilters;
  nonce: number;
  onAuthLoss: () => void;
}) {
  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashAnalysis(filters(), sig),
    [nonce, filters],
    onAuthLoss,
  );

  if (loading && !data) return <p>Loading…</p>;
  const msg = errMsg(error);
  if (msg) return <p className="admin-error">{msg}</p>;
  if (!data) return <p>No data.</p>;

  const cohort = (data.cohort ?? {}) as R;
  const notes = (data.notes ?? []) as string[];
  const diag = (data.diagnostics ?? {}) as R;
  const diagN = Number(diag.n_sessions ?? 0);

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        One snapshot ({str(data.snapshot_time)}) · eligible N={str(cohort.eligible_research)} ·
        diagnostic (non-live) N={str(cohort.diagnostic_sessions ?? 0)} ·
        analysis digest <span className="mono">{str(cohort.analysis_id)}</span> ·
        descriptive only — no significance tests in Phase A.
      </p>
      {section === "items" && <ItemsSection data={data} />}
      {section === "chat" && (
        <ChatSection filters={filters} nonce={nonce} onAuthLoss={onAuthLoss} />
      )}
      {section === "power" && <PowerSection data={data} />}
      {section === "contrasts" && <ContrastsSection data={data} />}
      {section === "findings" && <FindingsSection data={data} />}
      {diagN > 0 && (section === "items" || section === "power" || section === "contrasts") && (
        <DiagnosticsBlock section={section} diag={diag} />
      )}
      {notes.map((n, i) => (
        <p key={i} className="admin-note">{n}</p>
      ))}
    </div>
  );
}

/** DA-13: explicitly-labeled non-live diagnostics — same descriptive machinery,
 * visually fenced off from the research cohort. */
function DiagnosticsBlock({ section, diag }: { section: Section; diag: R }) {
  return (
    <details className="admin-card" style={{ borderStyle: "dashed" }}>
      <summary>
        <strong>Diagnostics</strong> — non-live traffic only (qa/preview/accelerated;{" "}
        {str(diag.n_sessions)} sessions). Never part of the research N.
      </summary>
      <p className="admin-note">{str(diag.label)}</p>
      {section === "items" && (
        <Table
          caption="Diagnostic item descriptives — bound expected keys, pooled per instrument@version."
          head={["partition", "instrument", "item", "v", "expected", "answered", "skipped", "missing", "absent", "invalid", "unexpected"]}
          rows={((diag.items ?? []) as R[]).map((r) => {
            const c = (r.counts ?? {}) as R;
            return [
              <PartitionCell key="p" r={r} />,
              str(r.instrument_id),
              str(r.item_id),
              num(r.instrument_version),
              num(r.sessions_expected),
              num(c.answered),
              num(c.skipped),
              num(c.missing),
              num(c.absent),
              num(c.invalid),
              num(c.unexpected),
            ];
          })}
        />
      )}
      {(section === "power" || section === "contrasts") && (
        <Table
          caption="Diagnostic per-partition arm descriptives."
          head={["partition", "measure", "arm", "n", "measured", "mean", "SD", "missing"]}
          rows={((diag.power_inputs ?? []) as R[]).map((r) => [
            <span key="p" className="mono">{str(r.partition)}</span>,
            <span key="m" className="mono">{str(r.measure)}</span>,
            str(r.arm),
            num(r.n_sessions),
            num(r.n_measured),
            num(r.mean),
            num(r.sd),
            num(r.missing),
          ])}
        />
      )}
    </details>
  );
}

/** One `cohort_analysis.pools[]` entry (BA-104): sessions pool only within a
 * compatibility key, or across keys under a recorded equivalence — a pool is
 * therefore always single-mechanism and its cells split on arm only. */
function cellColumns(pool: R | null): string[] {
  return Object.keys((pool?.cells ?? {}) as R).sort();
}

/** Pools shown while the scripted arm is fielding-disabled (AID-63/BA-104):
 * a scripted pool is hidden whole — nothing scripted can contribute to a
 * visible estimate because pools never cross manifests. */
function visiblePools(ca: R | null, data: R): R[] {
  const pools = (ca?.pools ?? []) as R[];
  if (data.scripted_teammates_enabled !== false) return pools;
  return pools.filter(
    (p) => !Object.keys((p.mechanisms ?? {}) as R).includes("scripted"),
  );
}

function poolTitle(p: R): string {
  const mechs = Object.keys((p.mechanisms ?? {}) as R).join("+") || "—";
  const nKeys = ((p.compatibility_keys ?? []) as string[]).length;
  const eq = p.equivalence ? ` · equivalence: ${str(p.equivalence)}` : "";
  return `${mechs} · ${str(p.pool_kind)} · n=${str(p.n_sessions)} · ${nKeys} compat key(s)${eq}`;
}

// v15: workload-era arms read better as the felt contrast — "high" = both
// teammates heavy, "mixed" = one heavy + one light — than raw arm names.
const ARM_DISPLAY: Record<string, string> = {
  high: "high",
  low: "low",
  mixed: "mixed (1 heavy/1 light)",
};

function cellLabel(key: string): string {
  const [mech, arm] = key.split("|");
  const m = mech === "ai" ? "ai" : mech === "scripted" ? "scripted" : mech;
  return `${m}·${ARM_DISPLAY[arm ?? ""] ?? arm ?? key}`;
}

function diffCell(diff: R | null): string {
  if (!diff || diff.diff === null || diff.diff === undefined) return "—";
  const d = num(diff.diff);
  const dCohen = diff.cohen_d === null || diff.cohen_d === undefined
    ? "—"
    : `d=${num(diff.cohen_d)}`;
  return `${d} (${dCohen})`;
}

/** The pooled item rows: `item.<instrument>.<item>` scalar keys carry the
 * numeric descriptives; `…@slug` keys carry the per-option binary share. */
function cohortItems(pool: R | null): R[] {
  return ((pool?.measures ?? []) as R[]).filter((m) =>
    str(m.measure).startsWith("item.") && !str(m.measure).includes("@"));
}

function cohortBinaryItems(pool: R | null): R[] {
  return ((pool?.measures ?? []) as R[]).filter((m) =>
    str(m.measure).startsWith("item.") && str(m.measure).includes("@"));
}

function cohortScalars(pool: R | null): R[] {
  return (((pool?.measures ?? []) as R[]).filter((m) =>
    !str(m.measure).startsWith("item.")));
}

/** A `mean (n)` or `—` cell for one mechanism|arm bucket. */
function cellMean(row: R, cell: string): string {
  const c = ((row.cells ?? {}) as R)[cell] as R | undefined;
  if (!c || c.n === null || c.n === undefined || Number(c.n) === 0) return "—";
  return `${num(c.mean)} (n=${num(c.n)})`;
}

/** A `mean ± sd (n)` or `—` cell for one condition-comparison column. */
function cmpCell(d: R | undefined): string {
  if (!d || d.n === null || d.n === undefined || Number(d.n) === 0) return "—";
  const sd = d.sd === null || d.sd === undefined ? "" : ` ± ${num(d.sd)}`;
  return `${num(d.mean)}${sd} (n=${num(d.n)})`;
}

/** Visible condition columns from the comparison block — respects the same
 * scripted-pool visibility rule as the rest of the analysis surfaces. */
function cmpColumns(data: R): R[] {
  const cmp = (data.condition_comparison ?? null) as R | null;
  const ca = (data.cohort_analysis ?? null) as R | null;
  const visIds = new Set(visiblePools(ca, data).map((p) => str(p.pool_id)));
  return ((cmp?.columns ?? []) as R[]).filter((c) => {
    // 2026-10-18: condition columns group (manifest, arm) and carry the compat
    // pools they span in pool_ids; pool_id kept as fallback for older payloads.
    const ids = (c.pool_ids ?? [c.pool_id]) as string[];
    return ids.some((id) => visIds.has(str(id)));
  });
}

/** Columns of the current round: the conditions on the manifest carrying the
 * newest session. Historical-era columns stay available behind a disclosure —
 * the eligible cohort spans every round, and showing all of them made the
 * Arm Contrasts table unreadable (v15 cleanup, 2026-10-18). */
function currentConditionColumns(data: R): R[] {
  const cols = cmpColumns(data);
  if (cols.length <= 2) return cols;
  let newest: R | null = null;
  for (const c of cols) if (!newest || str(c.latest_at) > str(newest.latest_at)) newest = c;
  const mid = newest ? (((newest as R).manifests ?? []) as string[])[0] : null;
  return mid ? cols.filter((c) => (((c as R).manifests ?? []) as string[]).includes(mid)) : cols;
}

/** Mean ± SD (n) per row across Overall + one column per condition pool.
 * `prefix` strips a common key prefix (e.g. "item.") from row labels.
 * `cols` overrides the default column set (e.g. current-round only). */
function ConditionTable({ data, rows, prefix, cols }: { data: R; rows: R[]; prefix?: string | undefined; cols?: R[] }) {
  const cmp = (data.condition_comparison ?? null) as R | null;
  const columns = cols ?? cmpColumns(data);
  if (!cmp || columns.length === 0) return null;
  return (
    <Table
      head={[
        "measure",
        `Overall (n=${num(cmp.n_sessions)})`,
        ...columns.map((c) => `${str(c.label)} (n=${num(c.n_sessions)})`),
      ]}
      rows={rows.map((m) => [
        <span key="m" className="mono">
          {prefix ? str(m.measure).replace(new RegExp(`^${prefix}`), "") : str(m.measure)}
        </span>,
        cmpCell(m.overall as R | undefined),
        ...columns.map((c) =>
          cmpCell(((m.cells ?? {}) as R)[str(c.pool_id)] as R | undefined),
        ),
      ])}
    />
  );
}

function cmpRowsAll(data: R): R[] {
  return (((data.condition_comparison ?? {}) as R).rows ?? []) as R[];
}

/** Current-round columns by default; a disclosure reveals the full history.
 * The eligible cohort spans every round — the "all" disclosure is the
 * documented escape hatch (Arm Contrasts + Findings + Items all use it). */
function CurrentOrAllConditionTable({
  data,
  rows,
  prefix,
}: {
  data: R;
  rows: R[];
  prefix?: string | undefined;
}) {
  const all = cmpColumns(data);
  const cur = currentConditionColumns(data);
  return (
    <>
      <ConditionTable data={data} rows={rows} prefix={prefix} cols={cur} />
      {all.length > cur.length && (
        <details className="admin-card">
          <summary>
            All condition columns ({all.length}) — historical rounds
          </summary>
          <ConditionTable data={data} rows={rows} prefix={prefix} cols={all} />
        </details>
      )}
    </>
  );
}

function ItemsSection({ data }: { data: R }) {
  const items = (data.items ?? []) as R[];
  const ca = (data.cohort_analysis ?? null) as R | null;
  const pools = visiblePools(ca, data);
  return (
    <>
      <h3>Item descriptives — pooled across identical instruments</h3>
      <p className="admin-note">
        One row per bound item: n, mean, SD, median and min–max pooled across
        every session whose binding declared the identical instrument
        artifact@version (the version pins the wording — a different version
        never merges). Rows marked <em>pooled</em> aggregate several strict
        partitions — hover the partition cell for provenance. By-condition
        tables follow; per-pool arm splits sit in the collapsed provenance
        block ({pools.length} pool(s) in scope, {str(ca?.n_sessions)}{" "}
        sessions). Descriptive only.
      </p>
      {(() => {
        const pooledItems = items.filter(
          (r) => !r.binary_option && ((r.numeric ?? {}) as R).n != null,
        );
        return (
          pooledItems.length > 0 && (
            <Table
              caption="Pooled item descriptives — all conditions sharing the bound instrument@version."
              head={["item", "instrument@v", "expected", "answered", "n", "mean", "SD", "median", "IQR", "range", "partition"]}
              rows={pooledItems.map((r) => {
                const n = (r.numeric ?? {}) as R;
                const c = (r.counts ?? {}) as R;
                return [
                  <span key="i" className="mono">{str(r.item_id)}</span>,
                  `${str(r.instrument_id)}@${num(r.instrument_version)}`,
                  num(r.sessions_expected),
                  num(c.answered),
                  num(n.n),
                  num(n.mean),
                  num(n.sd),
                  num(n.median),
                  n.q1 === null ? "—" : `${num(n.q1)}–${num(n.q3)}`,
                  n.min === null ? "—" : `${num(n.min)}–${num(n.max)}`,
                  <PartitionCell key="p" r={r} />,
                ];
              })}
            />
          )
        );
      })()}
      <h3>Items — by condition</h3>
      <p className="admin-note">
        The same item measures side-by-side — one Overall column for the whole
        eligible cohort plus one column per condition pool, mean ± SD (n=) per
        cell. Descriptive only; pooling boundaries still govern inference.
      </p>
      <CurrentOrAllConditionTable
        data={data}
        rows={cmpRowsAll(data).filter(
          (m) => str(m.measure).startsWith("item.") && !str(m.measure).includes("@"),
        )}
        prefix="item\."
      />
      {cmpRowsAll(data).some(
        (m) => str(m.measure).startsWith("item.") && str(m.measure).includes("@"),
      ) && (
        <>
          <h3>Item option shares — by condition</h3>
          <p className="admin-note">
            Choice items as per-option binary shares — mean = share of
            sessions choosing the option (the tp_post_10 suspicion check reads
            here), split by condition.
          </p>
          <CurrentOrAllConditionTable
            data={data}
            rows={cmpRowsAll(data).filter(
              (m) => str(m.measure).startsWith("item.") && str(m.measure).includes("@"),
            )}
            prefix="item\."
          />
        </>
      )}
      <details className="admin-card">
        <summary>
          <strong>Per-pool splits</strong> — compatibility partitions & arm
          cells, provenance view
        </summary>
        <p className="admin-note">
          Per-pool splits (BA-104): estimates pool only inside a compatibility
          key, or across keys under a recorded equivalence.
        </p>
        {pools.length === 0 && (
          <p className="admin-note">No poolable eligible sessions in this scope.</p>
        )}
        {pools.map((p) => {
          const cells = cellColumns(p);
          const pooled = cohortItems(p);
          const binary = cohortBinaryItems(p);
          return (
            <div key={str(p.pool_id)}>
              <p className="admin-note" role="note">
                <strong>Pool:</strong> <span className="mono">{poolTitle(p)}</span>
              </p>
              <Table
                caption="Per-item descriptives for this compatibility pool, split by arm cell (mean (n=) per cell)."
                head={["item", "n", "mean", "SD", "median", "IQR", "range",
                  ...cells.map(cellLabel)]}
                rows={pooled.map((m) => {
                  const o = (m.overall ?? {}) as R;
                  return [
                    <span key="i" className="mono">{str(m.measure).replace(/^item\./, "")}</span>,
                    num(o.n),
                    num(o.mean),
                    num(o.sd),
                    num(o.median),
                    o.q1 === null ? "—" : `${num(o.q1)}–${num(o.q3)}`,
                    o.min === null ? "—" : `${num(o.min)}–${num(o.max)}`,
                    ...cells.map((c) => cellMean(m, c)),
                  ];
                })}
              />
              {binary.length > 0 && (
                <Table
                  caption="Choice items as per-option binary shares — mean = share of sessions choosing the option, split by cell (the tp_post_10 suspicion check reads here)."
                  head={["item · option", "n", "share", ...cells.map(cellLabel)]}
                  rows={binary.map((m) => {
                    const o = (m.overall ?? {}) as R;
                    return [
                      <span key="i" className="mono">{str(m.measure).replace(/^item\./, "")}</span>,
                      num(o.n),
                      num(o.mean),
                      ...cells.map((c) => cellMean(m, c)),
                    ];
                  })}
                />
              )}
            </div>
          );
        })}
      </details>
      <details className="admin-card">
        <summary><strong>Per-item raw table</strong> — bound-item reconciliation detail (pooled per instrument@version)</summary>
        <Table
          caption="Per bound item (instrument@version x item): cells pool across partitions when sessions bind the identical artifact version — pooled rows show provenance on hover. expected = sessions whose bound artifact defines the item; missing = expected-but-no-row; absent = item absent from that session's bound version. Malformed/out-of-range values count as invalid, never averaged."
          head={["partition", "instrument", "item", "v", "expected", "answered", "skipped", "missing", "absent", "invalid", "unexpected", "n", "mean", "SD", "median", "IQR", "range", "distribution / categories"]}
          rows={items.map((r) => {
            const n = (r.numeric ?? {}) as R;
            const counts = (r.counts ?? {}) as R;
            const cats = (r.categories ?? {}) as R;
            const dist = (r.distribution ?? {}) as R;
            return [
              <PartitionCell key="p" r={r} />,
              str(r.instrument_id),
              r.binary_option ? (
                <span key="i">
                  {str(r.item_id)}
                  <br />
                  <small>= {str(r.binary_option)}</small>
                </span>
              ) : (
                str(r.item_id)
              ),
              num(r.instrument_version),
              num(r.sessions_expected),
              num(counts.answered),
              num(counts.skipped),
              num(counts.missing),
              num(counts.absent),
              num(counts.invalid),
              num(counts.unexpected),
              num(n.n),
              num(n.mean),
              num(n.sd),
              num(n.median),
              n.q1 === null ? "—" : `${num(n.q1)}–${num(n.q3)}`,
              n.min === null ? "—" : `${num(n.min)}–${num(n.max)}`,
              Object.keys(cats).length
                ? Object.entries(cats).map(([k, v]) => `${k}: ${v}`).join("; ")
                : Object.entries(dist).map(([k, v]) => `${k}→${v}`).join(" "),
            ];
          })}
        />
      </details>
    </>
  );
}

function PowerSection({ data }: { data: R }) {
  const pi = (data.power_inputs ?? {}) as R;
  const rows = (pi.rows ?? []) as R[];
  const mde = (pi.mde_scenarios ?? {}) as R;
  const ca = (data.cohort_analysis ?? null) as R | null;
  const pools = visiblePools(ca, data);
  return (
    <>
      <h3>Power inputs — pooled scalar descriptives</h3>
      <p className="admin-note">
        Pooled coverage and variance on the registered scalars — the inputs a
        declared power model consumes, not power claims. Shown per
        compatibility pool (BA-104): {pools.length} pool(s) in scope. MDE
        scenarios: {str(mde.status)} (gated on{" "}
        {((mde.gated_on ?? []) as string[]).join("/")}).
      </p>
      {pools.map((p) => {
        const scalars = cohortScalars(p);
        const corr = ((p.correlations ?? []) as R[]);
        return (
          <div key={str(p.pool_id)}>
            <p className="admin-note" role="note">
              <strong>Pool:</strong> <span className="mono">{poolTitle(p)}</span>
            </p>
            <Table
              caption="Core scalar measures for this compatibility pool — N, mean, sample SD, median, IQR and observed range."
              head={["measure", "n", "mean", "SD", "median", "IQR", "range", "missing"]}
              rows={scalars.map((m) => {
                const o = (m.overall ?? {}) as R;
                return [
                  <span key="m" className="mono">{str(m.measure)}</span>,
                  num(o.n),
                  num(o.mean),
                  num(o.sd),
                  num(o.median),
                  o.q1 === null ? "—" : `${num(o.q1)}–${num(o.q3)}`,
                  o.min === null ? "—" : `${num(o.min)}–${num(o.max)}`,
                  num(cellsTotalMissing(m)),
                ];
              })}
            />
            {corr.length > 0 && (
              <>
                <h3>Correlations — scalar measures (Pearson r, pairwise)</h3>
                <Table
                  caption="Pairwise Pearson correlations inside this compatibility pool over sessions where both measures are measured. Descriptive only — r without a declared model is not a test."
                  head={["measure A", "measure B", "n", "r"]}
                  rows={corr.map((r) => [
                    <span key="a" className="mono">{str(r.a)}</span>,
                    <span key="b" className="mono">{str(r.b)}</span>,
                    num(r.n),
                    r.r === null || r.r === undefined ? "—" : num(r.r),
                  ])}
                />
              </>
            )}
          </div>
        );
      })}
      <details className="admin-card">
        <summary><strong>Per-pool coverage table</strong> — {rows.length} rows</summary>
        <Table
          caption="Per analytic pool x arm — pools merge compatibility partitions only under a recorded equivalence; a cross-pool arm row would be Simpson-ambiguous, so coverage stays split."
          head={["partition", "measure", "arm", "n sessions", "n measured", "mean", "SD", "missing"]}
          rows={rows.map((r) => [
            <span key="p" className="mono">{str(r.partition)}</span>,
            <span key="m" className="mono">{str(r.measure)}</span>,
            str(r.arm),
            num(r.n_sessions),
            num(r.n_measured),
            num(r.mean),
            num(r.sd),
            num(r.missing),
          ])}
        />
      </details>
    </>
  );
}

/** Partition label for a pooled row: renders the label plus a provenance
 * tooltip listing contributing strict partitions when the row is pooled. */
function PartitionCell({ r }: { r: R }) {
  const parts = Array.isArray(r.partitions)
    ? (r.partitions as unknown[]).map(str)
    : [];
  return (
    <span className="mono" title={parts.join("\n")}>
      {str(r.partition)}
      {parts.length > 1 && <small> ·{parts.length}</small>}
    </span>
  );
}

/** Sum of per-cell missing counts — the honest "unmeasured" denominator for a
 * pooled row (the overall n_sessions counts every session in the cells). */
function cellsTotalMissing(row: R): number {
  return Object.values((row.cells ?? {}) as R)
    .reduce<number>((acc, c) => acc + Number((c as R).n_missing ?? 0), 0);
}

function ContrastsSection({ data }: { data: R }) {
  const contrasts = (data.contrasts ?? []) as R[];
  const ca = (data.cohort_analysis ?? null) as R | null;
  const pools = visiblePools(ca, data);
  const scriptedOff = data.scripted_teammates_enabled === false;
  const allCols = cmpColumns(data);
  const curCols = currentConditionColumns(data);
  const contrastKeys = (m: R) => Object.keys((m.contrasts ?? {}) as R);
  // Contrast direction from the key name: `arm_high_minus_low` → "high−low".
  // Direction is constant across measures, so the first populated key labels
  // the column for every row.
  const dirLabel = (scalars: R[], prefix: string) => {
    const key = scalars
      .flatMap((m) => contrastKeys(m))
      .find((k) => k.startsWith(prefix));
    return key ? `Δ ${key.slice(prefix.length).replace(/_minus_/, "−")}` : "Δ";
  };
  return (
    <>
      <h3>Between-subjects contrasts — arm difference per pool (descriptive)</h3>
      <p className="admin-note">
        BA-104: a contrast is computed only inside one compatibility pool —
        the same manifest hash (plus any recorded equivalence), so the shown
        cell means and the Δ column use exactly the same sessions. A
        mechanism (AI vs scripted) contrast is never produced: that pools
        different manifests and needs an explicit equivalence record, which
        none exists. Significance and equivalence are gated on the declared
        outcome families (D12/D14/O-13).
      </p>
      {scriptedOff && (
        <p className="admin-note" role="note">
          <strong>Scripted-teammate pools are hidden</strong> on this
          deployment (<code>SCRIPTED_TEAMMATES_ENABLED=0</code>) — the view
          mirrors the fieldable design. Historical scripted sessions remain in
          the per-partition tables; nothing scripted contributes to any
          visible estimate.
        </p>
      )}
      {pools.length === 0 ? (
        <p className="admin-note">No poolable eligible sessions in this scope.</p>
      ) : (
        <>
          <h3>Conditions side-by-side — descriptive means</h3>
          <p className="admin-note">
            Mean ± SD (n=) per measure: Overall plus one column per condition
            pool. No between-condition Δ column exists — conditions differ by
            manifest, and a cross-manifest contrast requires a recorded
            equivalence that does not exist, so no difference is estimated.
          </p>
          <ConditionTable
            data={data}
            cols={curCols}
            rows={cmpRowsAll(data).filter(
              (m) => !str(m.measure).startsWith("item."),
            )}
          />
          {allCols.length > curCols.length && (
            <details className="admin-card">
              <summary>
                All condition columns ({allCols.length}) — historical rounds
              </summary>
              <ConditionTable
                data={data}
                cols={allCols}
                rows={cmpRowsAll(data).filter(
                  (m) => !str(m.measure).startsWith("item."),
                )}
              />
            </details>
          )}
          <details className="admin-card">
            <summary>
              <strong>Per-pool arm contrasts</strong> — cell means and Δ
              inside each compatibility pool
            </summary>
            {pools.map((p) => {
              const cells = cellColumns(p);
              const scalars = cohortScalars(p);
              return (
                <div key={str(p.pool_id)}>
                  <p className="admin-note" role="note">
                    <strong>Pool:</strong>{" "}
                    <span className="mono">{poolTitle(p)}</span>
                  </p>
                  <Table
                    caption="Per-measure cell means (n=) inside this compatibility pool, with the arm difference. Cohen's d is a descriptive effect size on the pooled within-group SD."
                    head={[
                      "measure",
                      ...cells.map(cellLabel),
                      dirLabel(scalars, "arm_"),
                    ]}
                    rows={scalars.map((m) => {
                      const c = (m.contrasts ?? {}) as R;
                      const armKey = contrastKeys(m).find((k) =>
                        k.startsWith("arm_"),
                      );
                      return [
                        <span key="m" className="mono">{str(m.measure)}</span>,
                        ...cells.map((cell) => cellMean(m, cell)),
                        armKey ? diffCell(c[armKey] as R) : "—",
                      ];
                    })}
                  />
                </div>
              );
            })}
          </details>
        </>
      )}
      <details className="admin-card">
        <summary><strong>Per-pool arm descriptives</strong> — {contrasts.length} contrast rows</summary>
        {contrasts.map((c, i) => {
          const arms = (c.arms ?? {}) as R;
          return (
            <details key={i}>
              <summary>
                <span className="mono">{str(c.partition).slice(0, 40)}</span> ·{" "}
                {str(c.measure)} · {str(c.direction) ?? "single-arm"} · Δ {num(c.mean_diff)}
              </summary>
              <Table
                head={["arm", "n", "measured", "missing", "mean", "SD", "median", "IQR"]}
                rows={Object.entries(arms).map(([arm, a]) => {
                  const x = a as R;
                  return [
                    arm, num(x.n_sessions), num(x.n_measured), num(x.missing),
                    num(x.mean),
                    num(x.sd), num(x.median),
                    x.q1 === null ? "—" : `${num(x.q1)}–${num(x.q3)}`,
                  ];
                })}
              />
            </details>
          );
        })}
      </details>
    </>
  );
}

function FindingsSection({ data }: { data: R }) {
  const cards = (data.findings ?? []) as R[];
  const ca = (data.cohort_analysis ?? null) as R | null;
  const pools = visiblePools(ca, data);
  const cmp = (data.condition_comparison ?? null) as R | null;
  const scalarRows = cmpRowsAll(data).filter(
    (m) => !str(m.measure).startsWith("item."),
  );
  return (
    <>
      {cmp && cmpColumns(data).length > 0 && (
        <>
          <h3>Condition comparison</h3>
          <p className="admin-note">
            Scalar descriptives side-by-side: one Overall column for the whole
            eligible cohort ({num(cmp.n_sessions)} sessions) plus one column
            per condition pool — mean ± SD (n=) per cell. Descriptive only;
            adjacent columns are a visual comparison, not an estimate —
            pooling boundaries still govern inference. Item-level rows live on
            the Items tab.
          </p>
          <CurrentOrAllConditionTable data={data} rows={scalarRows} />
        </>
      )}
      <details className="admin-card">
        <summary>
          <strong>Headline results — pooled cells</strong> — per-pool arm
          cells, provenance view
        </summary>
        <p className="admin-note">
          The study's scalar measures split by arm cell inside each
          compatibility pool ({pools.length} pool(s), {str(ca?.n_sessions)}{" "}
          sessions) — mean (n=) per cell. Descriptive only.
        </p>
        {pools.map((p) => {
          const cells = cellColumns(p);
          const headline = cohortScalars(p);
          return headline.length === 0 ? null : (
            <div key={str(p.pool_id)}>
              <p className="admin-note" role="note">
                <strong>Pool:</strong> <span className="mono">{poolTitle(p)}</span>
              </p>
              <Table
                head={["measure", "overall", ...cells.map(cellLabel)]}
                rows={headline.map((m) => {
                  const o = (m.overall ?? {}) as R;
                  return [
                    <span key="m" className="mono">{str(m.measure)}</span>,
                    `${num(o.mean)} (n=${num(o.n)})`,
                    ...cells.map((c) => cellMean(m, c)),
                  ];
                })}
              />
            </div>
          );
        })}
      </details>
      <h3>Findings (neutral)</h3>
      <p className="admin-note">
        Auto-generated neutral finding cards from the analysis snapshot — one
        per declared descriptive result. Each card's digest pins the exact
        input set it was computed from, so a card and its data can always be
        traced back. Descriptive statements only — no claims about significance.
      </p>
      {cards.map((c) => (
        <div key={str(c.card_id)} className="admin-card">
          <p>{str(c.text)}</p>
          <p className="admin-note">
            cohort={str(c.cohort)} · N={str(c.n_eligible)} · digest{" "}
            <span className="mono">{str(c.analysis_id)}</span>
          </p>
        </div>
      ))}
    </>
  );
}


// ----- Team Chat tab -------------------------------------------------------
// Two-pane transcript browser modeled on transcript_viewer.html: session list
// (filtered by condition / automation judgment / request-only / search) on the
// left, the scoped session's chat on the right — mm:ss offsets, per-seat bubble
// colors, request/scheduled tags, and teammate src badges (generated vs
// scripted mechanism). Data is scoped by the same dashboard filters via
// GET /admin/dashboard/chat; participant bodies arrive withheld (b:null) when
// the session's data-use choice isn't use_all.

const mmss = (sec: number) =>
  `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;

function ChatBubble({ m, q }: { m: R; q: string }) {
  const seat = str(m.a);
  const cls =
    m.withheld || m.src === "system"
      ? "system"
      : seat === "participant"
        ? "participant"
        : seat;
  const body = m.withheld
    ? "— withheld: data-use choice is not use_all —"
    : str(m.b);
  const parts: ReactNode[] = [];
  if (q && !m.withheld) {
    const lo = body.toLowerCase();
    let i = 0;
    let j = lo.indexOf(q);
    while (j >= 0) {
      parts.push(body.slice(i, j));
      parts.push(<mark key={j}>{body.slice(j, j + q.length)}</mark>);
      i = j + q.length;
      j = lo.indexOf(q, i);
    }
    parts.push(body.slice(i));
  } else {
    parts.push(body);
  }
  return (
    <div className={`msg ${cls}`} style={{ display: "flex", gap: 10, margin: "4px 0", alignItems: "flex-start" }}>
      <div style={{ width: 48, flex: "none", color: "var(--adm-muted,#666)", fontSize: 12, textAlign: "right", paddingTop: 6, fontVariantNumeric: "tabular-nums" }}>
        {mmss(Number(m.s) || 0)}
      </div>
      <div
        className="bub"
        style={{
          padding: "6px 10px", borderRadius: 8, maxWidth: 760,
          whiteSpace: "pre-wrap", wordWrap: "break-word",
          background:
            seat === "JT" ? "#e8f0fe" : seat === "PM" ? "#fdf0e6"
            : seat === "participant" ? "#e9f6ec" : "#f1f1ef",
          fontStyle: cls === "system" ? "italic" : undefined,
          fontSize: cls === "system" ? 12 : 14,
          color: cls === "system" ? "#6b6b70" : undefined,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 12, marginRight: 6 }}>
          {seat === "participant" ? "Participant" : seat}
        </span>
        {parts}
        {seat !== "participant" && seat !== "system" && (
          <span
            className="mono"
            style={{
              fontSize: 11, fontWeight: 600, padding: "0 6px", marginLeft: 6,
              borderRadius: 9, whiteSpace: "nowrap",
              border: "1px solid currentColor",
              color: m.src === "generated" ? "#1f7a4a" : "#5b5b66",
            }}
          >
            {str(m.src)}
          </span>
        )}
        {m.req === true && (
          <span style={{ fontSize: 11, padding: "0 6px", marginLeft: 6, borderRadius: 9, border: "1px solid #b4232a", color: "#b4232a" }}>
            request{m.crate != null ? ` · crate ${num(m.crate)}` : ""}
          </span>
        )}
        {m.sched === true && (
          <span style={{ fontSize: 11, padding: "0 6px", marginLeft: 6, borderRadius: 9, border: "1px solid #aaa", color: "#777" }}>
            scheduled
          </span>
        )}
      </div>
    </div>
  );
}

function JudgePanel({ sessionId, onAuthLoss }: { sessionId: string; onAuthLoss: () => void }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [verdicts, setVerdicts] = useState<R[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = (await adminApi.dashChatJudgeList(sessionId)) as R;
      setVerdicts((d.verdicts ?? []) as R[]);
    } catch (e) {
      if (e instanceof AdminApiError && e.status === 401) onAuthLoss();
    }
  }, [sessionId, onAuthLoss]);

  useEffect(() => {
    setVerdicts([]);
    void load();
    const t = window.setInterval(load, 8000); // poll for the worker's verdict
    return () => window.clearInterval(t);
  }, [load]);

  const ask = async () => {
    if (!question.trim() || busy) return;
    setBusy(true); setErr(null);
    try {
      await adminApi.dashChatJudge(sessionId, question.trim());
      await load();
    } catch (e) {
      setErr(errMsg(e) ?? "judge request failed");
      if (e instanceof AdminApiError && e.status === 401) onAuthLoss();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-card" style={{ marginBottom: 12 }}>
      <h3>AI judge — gemma4:26b</h3>
      <p className="admin-note">
        Sends this session's transcript to the local model (worker-side, loopback
        only) and records the verdict as a session audit row. Ad-hoc analysis —
        not participant-facing, not part of the recorded task.
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          type="text" value={question} style={{ flex: 1 }}
          placeholder="e.g. Did the participant treat JT and PM as teammates or tools?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void ask(); }}
        />
        <button onClick={() => void ask()} disabled={busy || !question.trim()}>
          {busy ? "Queuing…" : "Judge"}
        </button>
      </div>
      {err && <p className="admin-error">{err}</p>}
      {verdicts.map((v) => {
        const p = (v.payload ?? {}) as R;
        return (
          <details key={str(v.audit_id)} className="admin-card" style={{ marginTop: 8 }} open={verdicts[0] === v}>
            <summary>
              <span className="mono">{str(v.occurred_at).slice(0, 19)}</span> — {str(p.question)}
              {p.error ? <span style={{ color: "#b4232a" }}> · error: {str(p.error)}</span> : null}
            </summary>
            <p style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{str(p.verdict)}</p>
            <p className="admin-note" style={{ fontSize: 11 }}>
              {str(p.model_id)} · {num(p.latency_ms)}ms · asked by {str(p.asked_by)}
            </p>
          </details>
        );
      })}
    </div>
  );
}

function ChatSection({
  filters,
  nonce,
  onAuthLoss,
}: {
  filters: () => DashFilters;
  nonce: number;
  onAuthLoss: () => void;
}) {
  const { data, error, loading, refreshing } = useDashData(
    (sig) => adminApi.dashChat(filters(), sig),
    [nonce, filters],
    onAuthLoss,
  );
  const [sel, setSel] = useState<string | null>(null);
  const [fCell, setFCell] = useState("");
  const [fAI, setFAI] = useState("");
  const [fReq, setFReq] = useState(false);
  const [fSys, setFSys] = useState(true);
  const [q, setQ] = useState("");

  if (loading && !data) return <p>Loading…</p>;
  const emsg = errMsg(error);
  if (emsg && !data) return <p className="admin-error">{emsg}</p>;
  const sessions = (((data ?? {}) as R).sessions ?? []) as R[];
  if (sessions.length === 0) return <p className="admin-note">No sessions in scope.</p>;

  const cells = [...new Set(sessions.map((s) => str(s.cell)))].sort();
  const ais = [...new Set(sessions.map((s) => str(s.tp_post_10)).filter(Boolean))].sort();
  const qq = q.trim().toLowerCase();
  const shown = sessions.filter(
    (s) =>
      (!fCell || str(s.cell) === fCell) &&
      (!fAI || str(s.tp_post_10) === fAI) &&
      (!fReq || Number(s.n_req_msgs) > 0) &&
      (!qq ||
        (((s.msgs ?? []) as R[]).some(
          (m) => str(m.b).toLowerCase().includes(qq),
        ))),
  );
  const cur = sessions.find((s) => str(s.session_id) === sel) ?? null;

  return (
    <div>
      {refreshing && <p className="admin-note">Refreshing — previous scope shown…</p>}
      <p className="admin-note">
        Team-chat transcripts for the scoped cohort ({sessions.length} sessions).
        Participant bodies show only when the session's data-use choice is
        use_all — otherwise the bubble reads withheld, matching the export gate.
        Timestamps are mm:ss from the session's first delivered message.
      </p>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", margin: "8px 0" }}>
        <select value={fCell} onChange={(e) => setFCell(e.target.value)}>
          <option value="">All conditions</option>
          {cells.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={fAI} onChange={(e) => setFAI(e.target.value)}>
          <option value="">Any automation judgment</option>
          {ais.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={fReq} onChange={(e) => setFReq(e.target.checked)} />
          only sessions with a request
        </label>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={fSys} onChange={(e) => setFSys(e.target.checked)} />
          show system messages
        </label>
        <input
          type="search" placeholder="Search message text…" value={q}
          onChange={(e) => setQ(e.target.value)} style={{ minWidth: 200 }}
        />
        <span className="admin-note" style={{ marginLeft: "auto" }}>{shown.length} sessions</span>
      </div>
      <div style={{ display: "flex", minHeight: 0, gap: 12 }}>
        <div style={{ width: 300, flex: "none", maxHeight: "70vh", overflow: "auto", border: "1px solid var(--line,#e3e3e0)", borderRadius: 8 }}>
          {shown.map((s) => (
            <div
              key={str(s.session_id)}
              onClick={() => setSel(str(s.session_id))}
              style={{
                padding: "8px 12px", borderBottom: "1px solid var(--line,#e3e3e0)",
                cursor: "pointer",
                background: str(s.session_id) === sel ? "#e8f0fe" : undefined,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600 }}>
                <span className="mono">{str(s.session_id).slice(0, 8)}</span>
                <span>{s.correct_total != null ? `${num(s.correct_total)}/4` : "—"}</span>
              </div>
              <div style={{ color: "var(--adm-muted,#666)", fontSize: 12 }}>{str(s.cell)}</div>
              <div style={{ color: "var(--adm-muted,#666)", fontSize: 12 }}>
                {num(s.n_req_msgs)} request msgs · {num(s.n_msgs)} msgs
                {str(s.tp_post_10) ? ` · ${str(s.tp_post_10)}` : ""}
              </div>
            </div>
          ))}
        </div>
        <div style={{ flex: 1, maxHeight: "70vh", overflow: "auto", border: "1px solid var(--line,#e3e3e0)", borderRadius: 8, padding: 16 }}>
          {!cur ? (
            <p className="admin-note" style={{ padding: 40, textAlign: "center" }}>Select a session.</p>
          ) : (
            <>
              <JudgePanel sessionId={str(cur.session_id)} onAuthLoss={onAuthLoss} />
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(160px,1fr))", gap: "6px 16px", fontSize: 13, marginBottom: 12 }}>
                {([
                  ["Session", str(cur.session_id)],
                  ["Condition", str(cur.cell)],
                  ["Stage", str(cur.stage)],
                  ["Light seat", str(cur.light_seat)],
                  ["JT / PM workload",
                    (() => { const av = (cur.assigned_values ?? {}) as R;
                      return av.JT != null || av.PM != null ? `${str(av.JT)} / ${str(av.PM)}` : null; })()],
                  ["Own workload (check-in)", cur.own_workload != null ? num(cur.own_workload) : null],
                  ["Correct (own / team)",
                    cur.correct_total != null
                      ? `${num(cur.correct_total)} (${num(cur.correct_own)} / ${num(cur.correct_team)})` : null],
                  ["Request messages", num(cur.n_req_msgs)],
                  ["Records shared", num(cur.n_shares)],
                  ["Asking takes their time (05)", cur.tp_post_05 != null ? num(cur.tp_post_05) : null],
                  ["Willing to help (07)", cur.tp_post_07 != null ? num(cur.tp_post_07) : null],
                  ["Competent (08)", cur.tp_post_08 != null ? num(cur.tp_post_08) : null],
                  ["Workloads differed (12)", cur.tp_post_12 != null ? num(cur.tp_post_12) : null],
                  ["Automation judgment", str(cur.tp_post_10)],
                ] as [string, string | number | null][]).map(([k, v]) => (
                  <div key={k}>
                    <b style={{ display: "block", fontSize: 11, color: "var(--adm-muted,#666)", textTransform: "uppercase", letterSpacing: ".03em", fontWeight: 500 }}>{k}</b>
                    {v ?? "–"}
                  </div>
                ))}
              </div>
              {((cur.msgs ?? []) as R[])
                .filter((m) => fSys || m.src !== "system")
                .map((m, i) => <ChatBubble key={i} m={m} q={qq} />)}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
