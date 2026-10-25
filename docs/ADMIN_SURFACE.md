# Investigators admin surface (WP13)

Terminology key: `docs/GLOSSARY.md`.

Scope: `/investigators/admin/` (UI) and `/investigators/api/admin/` (API). Apache does
**not** enforce Shibboleth on these paths — the public `/investigators/` block strips
`X-Remote-User`, `Eppn`, `X-Shib-*` and friends and forwards everything. The application
is therefore the *only* admin gate; forwarded identity headers are never read
(`adminauth.UNTRUSTED_IDENTITY_HEADERS` documents the ignored set).

## Role model

Three application-level roles, three independent secrets:

| Role | Secret | Reach |
|---|---|---|
| `researcher` | `ADMIN_PASSWORD` | sessions/detail/replay (incl. restricted truth view), monitor, balance, dispositions, audit log, batch create, preview issuance + own-link listing, entitlement decisions, retention schedule, redaction review, study/sharing exports, deletion, documentation view/download |
| `operator` | `ADMIN_OPERATOR_PASSWORD` | sessions/detail/replay (participant projection only), monitor, batches list, enrollment pause/resume, preview issuance + own-link listing (lab self-service), technical stop, credential recovery, incidents, cleanup, retention purge/tombstone reapply, deletion |
| `compensation` | `ADMIN_COMPENSATION_PASSWORD` | compensation queue + mark-paid only. No research data, no sessions, no exports |

`ADMIN_PASSWORD` is both the researcher credential *and* the master switch: when it is
empty the entire admin surface fails closed (login → `403 admin_disabled`, everything
else → `401`). A login POST claims `{role, password}` and the password is compared with
`hmac.compare_digest` **only** against that role's secret — a compensation secret can
never mint a researcher session. The optional `email` field is audit attribution only
(must be on `ADMIN_EMAILS`), never an authentication signal.

Rationale: matches the existing PCS/DCT one-password deployment pattern while keeping a
real privilege split; the signed session carries the role claim and every endpoint
enforces it server-side.

## Session model

- `inv_admin` — HttpOnly, Secure, SameSite=Strict cookie, path `/investigators/`,
  HMAC-SHA256 signed, expires after `ADMIN_SESSION_TTL_SECONDS` (default 1 h). Payload:
  `v1:role:urlquoted-actor:issued:expires:nonce`, integrity-checked and bound to the
  configured TTL — editing the claim or replaying past expiry is rejected.
- `inv_admin_csrf` — readable SameSite=Strict cookie; every mutating request must echo
  it in `X-CSRF-Token` (double-submit) and any `Origin` header must match the host.
- Login is throttled per (client, role): `ADMIN_LOGIN_MAX_ATTEMPTS` per
  `ADMIN_LOGIN_WINDOW_SECONDS` (default 5/300 s), in-memory.
- Session signing key derives from `ADMIN_PASSWORD` — rotating it invalidates all
  sessions; disabling a role secret kills that role's live sessions immediately.

## Endpoints

Auth: `POST /admin/login`, `POST /admin/logout`, `GET /admin/whoami`.

Read (researcher+operator): `GET /admin/sessions` (q/batch/mode/stage filters,
stage/terminal/active-ms/answers/reconnects/rejections/exposures/open-episodes/
pending-jobs/outcome per row), `GET /admin/sessions/{id}`, `GET
/admin/sessions/{id}/replay`, `GET /admin/sessions/{id}/audit`, `GET /admin/monitor`,
`GET /admin/dispositions`, `GET /admin/batches`, `GET /admin/preview-combos`, `GET
/admin/retention`. Researcher-only: `/admin/reports/balance`, `/admin/audit`,
`/admin/redaction/queue`, `GET /admin/docs` + `GET /admin/docs/{id}?download=1`
(fixed allowlist — README, DECISIONS, BEHAVIORS, HARNESS, the
platform overview at `docs/PLATFORM_OVERVIEW.md`, the investigators-scoped
CodeGraph index — served
from the read-only `/srv/docs` mount; ids are dict keys, never paths).
Compensation-only: `/admin/compensation/queue`.

Dashboard (WP17, researcher-only, `Cache-Control: no-store`, one
`REPEATABLE READ READ ONLY` snapshot per request, shared filter set
`study_key`/`batch_id`/`compatibility_key`(`unkeyed`|`ck1:<64 hex>`)/
`mode`(live|qa|preview|all)/`since`/`until` (UTC) → 422 on invalid,
unknown or duplicated filter params, on every dashboard route and the
export catalog; tombstoned sessions are always excluded under the same
scope predicate, and qa/preview rows never enter the research N — they
are reported in a labeled non-live diagnostics block instead):
`GET /admin/dashboard/overview` (mode mix, stage occupancy vs
ever-entered, receipt-based outcomes reconciled with stage/result/
scoring-version/timing/mode/data-use evidence + anomalies, manipulation
anchors, arm balance, batch fill, model-call status by arm, bounded
recent list),
`GET /admin/dashboard/readiness` (bound-version expected-item coverage —
answered/skipped/missing/absent/invalid/unexpected, invalid values never
echoed — flags counted once per session, withdrawal gate applied before
response loads, paginated audit with `audit_scope`
(completers|incomplete|exceptional|all) applied before `page_size`/
`offset` slicing, `page_size` ≤ 200),
`GET /admin/dashboard/timing` (stage-visit wall/active medians + IQR
split by arm within each partition, open visits censored and included in
task-budget totals, check-in segments),
`GET /admin/dashboard/instruments` (kind-aware serialization: verbatim
participant copy only for allowlisted participant-facing slots —
`prompt`, `payment_policy`, coding, QA-overlay and unknown kinds return
metadata only; ContentStore hash verification, codebook registry),
`GET /admin/dashboard/analysis` (canonical `scoring.score_rows` accuracy
proportion, measured-zero counts, latest-revision confidence, item
descriptives + descriptive power inputs + arm contrasts all partitioned
by compatibility×arm, neutral findings cards — one snapshot; inference
is `analysis_not_configured`, MDE is `unsupported_model`),
`GET /admin/exports` (published-export catalog from `ADMIN_EXPORT_DIR`
manifest sidecars — researcher kinds only (`research_study`/`sharing`;
payment manifests counted as restricted, never listed), per-entry
defensive validation, `unavailable` when unset),
`GET /admin/exports/tables` (the safe research-export vocabulary served by
the download — payment/linkage/compensation names are never listed).
`POST /admin/exports` accepts the dashboard's confirmed `scope` object;
the server resolves it to explicit `session_ids` (an empty selection
stays empty — never widens to unfiltered) and echoes the resolved scope
+ count. The design contract was an internal planning document and is not included.
Phase A is descriptive/operational only; Phase B inference is gated on
D12/D14/O-13.

Researcher+operator: `GET /admin/previews/mine` — the issuer-scoped list behind the
"test links" tab: the caller's minted preview links with live session status (the
issuance audit row stores the descriptor nonce + entry URL, joined to the session via
the `preview-<nonce>` participant identity).

Replay returns `participant.events` filtered by the *same* allowlist as the participant
API (`app.api.participant._project_event`), plus a `restricted` block (truth, holdings,
context, timing, claims, internal events) present only for the researcher role —
`restricted_status: "requires_researcher_role"` otherwise. The restricted projection is
never substituted into participant-visible data.

Actions (all audited in `admin_audit` in the same transaction as their effect):

- operator: `POST /admin/sessions/{id}/technical_stop` (mirrors the abandonment sweep:
  terminal transition, clock close, episode expiry, job suppression, durable outcome),
  `POST /admin/sessions/{id}/credential_recovery` (rotates credentials; the recovery
  token is returned once and never stored/logged raw), `POST
  /admin/sessions/{id}/incidents` + `/incidents/{iid}/close`,
  `POST /admin/batches/{id}/enrollment`, `POST /admin/retention/purge`,
  `POST /admin/retention/reapply`, `POST /admin/cleanup`,
  `POST /admin/sessions/{id}/deletion`,
  `POST /admin/previews` (HMAC descriptors per published manifest×arm combo; fixed
  seed; faithful or accelerated-QA timing; previews never consume allocations or
  create payment rows — also researcher; widened for lab self-service, 2026-09-30).
  `GET /admin/preview-combos` reports each combo's bound `consent_key` and
  `consent_current` (BA-37): only manifests bound to the latest literal consent copy
  are "current"; pointer-only ancestors and superseded literal versions are labeled
  "(legacy)" in the UI. Each combo also carries `test_group` (UX v6, 2026-10-07):
  `pilot_contract` (approved + current — the fielded contract), `comparison`
  (the newest `nl_only` build *and* the newest structured demo build outside the
  pilot family of a fielded study — today `tp_demo_nl_v3` and `tp_demo_v6`, the
  NL-vs-structured lab comparison pair, both arms), or `archive`. The default
  test-page surface and one-of-each issuance cover `pilot_contract` +
  `comparison`.
- researcher: `POST /admin/batches` (binds the manifest first — a batch can never open
  against a broken binding; `register_manifest` enforces immutable manifest content),
  `POST /admin/batch-groups/{id}/enrollment-target` (raise a live group's per-cell
  completer target — increase-only, re-validated as an `EnrollmentPolicy` under the
  allocation group-row lock, audited as `enrollment_target_update`; the only supported
  `enrollment_policy` mutation — 2026-10-17, replaces the v14 raw-SQL top-offs),
  `POST /admin/entitlements/{sid}/{kind}/decision`,
  `POST /admin/redaction/review`, `POST /admin/exports` (study/sharing),
  `POST /admin/exports/table` (per-table CSV download — CSRF/origin-gated
  mutation since it is data egress; body `{name, scope}` with `extra=forbid`
  and the same 422 filter contract; one `REPEATABLE READ` read-only snapshot
  resolves the scope, re-reads data-use, and renders the INV-1106 table via
  `export.study.download_table` — sessions without an explicit `use_all`
  choice are absent from text-bearing tables, all free-text cells emit
  verbatim (2026-10-16 owner direction: no exportable file carries
  `withheld_unreviewed`), `session.seed_hex` is
  stripped; the `export_table` audit row pins table/scope/counts/`sha256`
  and is written before the `no-store` CSV response is returned — an
  audit-insert failure refuses the download),
  `POST /admin/retention/schedule`, `POST /admin/sessions/{id}/deletion`.
- compensation: `POST /admin/compensation/entitlements/{sid}/{kind}/paid`,
  `POST /admin/exports` with `kind=payment` (the full linkage read needs the
  `investigators_compensation` DSN — under the runtime role it reports
  `blocked_privileged`; the privileged path is `scripts/export_study.py --kind
  payment`).

Deliberately absent: any write to assignment (`session.arm`/`binding`), truth/world
records, or frozen answers. Those tables are append-only anyway, and no endpoint or
service function reaches them.

## Monitoring honesty

`GET /admin/monitor` returns per-section `status`: `ok` or `unavailable` (+note).
Coding coverage reads `coding_*`/`validation_gate`, which the runtime role cannot read
(`RUNTIME_NO_ACCESS`) — the section reports `unavailable`, never a blanket "complete".
Fidelity rates and incident summaries reuse `app.export.aggregates` keyed on
`compatibility_key`; unkeyed sessions form explicit `unkeyed` groups.

## Retention & deletion (INV-1306/1307)

- `POST /admin/retention/schedule` sets `platform_link.purge_after = study_close + 48h`
  (S5). Identity columns beyond `(platform, platform_pid, session_id)` are not granted
  to the runtime role — the endpoint reports `blocked_privileged` rather than failing
  silently, and `scripts/retention_purge.py` (run with the migrator DSN) performs the
  privileged update/delete.
- `POST /admin/sessions/{id}/deletion`: `dry_run` (default) returns the per-table plan
  and writes a `deletion_dry_run` audit row only; `dry_run=false` deletes mutable
  session rows, attempts privileged tables savepoint-by-savepoint (denied → `blocked`),
  releases the allocation slot, and writes the `deletion_tombstone` audit row.
  `payment_entitlement`, `completion_receipt`, `result` and `data_use_choice` are never
  deleted — compensation eligibility is independent of the data-use choice.
- Tombstones are append-only `admin_audit` rows, so they survive the backup that
  restores data. `POST /admin/retention/reapply` (or the CLI) re-deletes any tombstoned
  session whose rows reappeared.
- A tombstone is also an **access revocation** (BA-39): tombstoned sessions fail
  credential authentication, preview-descriptor redemption, live re-entry and
  credential recovery, and their scheduled jobs are never claimed. They are
  excluded from the monitor, balance report and session lists. Surviving rows
  (identity FKs, append-only research tables the runtime role cannot delete)
  are pending privileged purge — `/admin/previews/mine` reports `deleted` +
  `purge_pending`, `session_detail` and `/admin/retention` report pending-purge
  row counts, and reapply records report `mutable_blocked`.
- `POST /admin/cleanup` defaults to `scope=preview` + `dry_run`; a live cleanup
  additionally requires `dry_run=false`, `confirm_live=true` and a reason.

## Export/redaction queue (INV-1308)

`GET /admin/redaction/queue` lists free-text cells lacking a `redaction_review` row;
`POST /admin/redaction/review` records the decision (append-only). Sharing exports
withhold unreviewed free text (`app.export.restricted`); all file output goes through
`app.export.csvsafe` (formula-safe UTF-8 CSV). `ADMIN_EXPORT_DIR` empty → exports return
the manifest/row summary without touching the filesystem.

Deployment wiring (2026-10-06): containers run `read_only`, so the export root must be
a mounted path — `docker-compose.yml` bind-mounts host dir
`./local-data/exports` (mode 1777, outside the repo so files never sit
under the `/srv/docs` read-only tree) at `/srv/exports`, and `.env` sets
`ADMIN_EXPORT_DIR=/srv/exports`. Retrieval: `ls ./local-data/exports/`
on the host — each run is a subdirectory containing `manifest.json` + CSV tables.
Export scope resolves server-side: the dashboard sends its confirmed filter object,
the API resolves it to explicit `session_ids`, and an empty resolution is refused
rather than widened. Preview sessions are ordinary rows — scope `mode=preview` or
`mode=all` plus a `batch_id`/`manifest` filter to export QA runs.

## Frontend

`/investigators/admin` mounts the lazy `AdminApp` chunk (`src/admin/`), separate from
the participant surface. Tabs are role-scoped; all API bodies are bounded and sanitized
server-side (`adminauth.sanitize_text`, pydantic limits); the UI renders text nodes
only — no HTML injection.

## Configuration

```
ADMIN_PASSWORD=            # researcher + master gate (empty = disabled)
ADMIN_OPERATOR_PASSWORD=
ADMIN_COMPENSATION_PASSWORD=
ADMIN_EMAILS=a@university.edu,b@university.edu
ADMIN_SESSION_TTL_SECONDS=3600
ADMIN_LOGIN_MAX_ATTEMPTS=5
ADMIN_LOGIN_WINDOW_SECONDS=300
ADMIN_EXPORT_DIR=
```

`.env` is never committed, printed or logged; all four secrets are registered with the
log redactor at settings load.

`.env.example` documents `ADMIN_EXPORT_DIR` (added 2026-10-06, pin `a912095d…`); the
remaining `ADMIN_*` knobs above live only in the deployed `.env`. All `ADMIN_*` settings
have safe defaults — omitting them disables the corresponding role/throttle/export knobs,
and an empty `ADMIN_PASSWORD` disables the whole surface regardless.

## Privileged operations

`scripts/retention_purge.py` runs with `INVESTIGATORS_MIGRATION_DATABASE_URL` (never the
runtime DSN): schedules purges, deletes due `platform_link` rows, re-applies tombstones,
and re-runs per-session deletion. Dry-run is the default; `--apply` deletes.
