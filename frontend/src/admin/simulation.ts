/**
 * Session simulation export (admin): given the replay payload, build a single
 * self-contained HTML file that replays the session through a recreation of the
 * participant workbench — case file, team chat, assistant, answers, check-in
 * overlay — in chronological order, plus a researcher-only internals rail
 * (internal events, command ledger, model calls).
 *
 * The file is generated on demand (button in the walkthrough header) and
 * downloaded as a Blob; nothing is produced per walkthrough automatically.
 * It carries restricted-tier data (internals, command receipts) so it inherits
 * the researcher's access scope — never share it past the research team.
 */

type Ev = {
  seq?: number;
  type: string;
  actor?: string;
  stage?: string;
  active_ms?: number | null;
  server_time?: string | null;
  payload?: Record<string, unknown>;
};

const CRATE_ITEMS: Record<number, string> = {
  1: "laptops",
  2: "smart watches",
  3: "gaming consoles",
  4: "TVs",
  5: "headphones",
  6: "cameras",
  7: "tablets",
  8: "power tools",
};

const LOCATIONS = [
  "The Sawmill",
  "The Marina",
  "Blackwell Storage",
  "Old Cannery",
  "Fairground Sheds",
  "Ridgeline Bunker",
];

// Fallback for replays without a bound budget (profiles before 2026-10-09 were all 360 s).
const DEFAULT_BUDGET_MS = 360_000;

function clip(v: unknown, n = 120): string {
  const s = String(v ?? "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

/** Internal-only rows → the researcher rail: a uniform {t, wall, label, detail}. */
function internalRows(restricted: Record<string, unknown> | null | undefined) {
  const rows: { seq: number | null; wall: string | null; label: string; detail: string }[] = [];
  for (const e of (restricted?.internal_events ?? []) as Ev[]) {
    const p = (e.payload ?? {}) as Record<string, unknown>;
    let detail: string;
    if (e.type === "generation_resolved") {
      const plan = (p.plan ?? {}) as Record<string, unknown>;
      detail =
        `${p.status ?? "?"}${p.fallback ? ` · fallback ${p.fallback}` : ""}` +
        `${plan.mechanism ? ` · ${plan.mechanism}` : ""}` +
        `${p.fallback_reason ? ` · ${p.fallback_reason}` : ""}`;
    } else if (e.type === "exposure_committed") {
      detail = `surface ${p.surface ?? "?"}`;
    } else if (e.type === "scripted_request_delivered") {
      detail = `crate ${p.crate ?? "?"} · request #${p.index ?? "?"}`;
    } else if (e.type === "structured_response_delivered") {
      detail = `record ${p.record_id ?? "?"}`;
    } else if (e.type === "warning_suppressed") {
      detail = String(p.reason ?? "");
    } else if (e.type === "automation_probe") {
      detail = clip(p.body, 80);
    } else if (e.type === "task_schedule_committed") {
      detail = clip(JSON.stringify(p.jobs ?? p), 100);
    } else {
      detail = clip(JSON.stringify(p), 100);
    }
    rows.push({
      seq: typeof e.seq === "number" ? e.seq : null,
      wall: (e.server_time as string | null) ?? null,
      label: `[${e.actor ?? "sys"}] ${e.type.replaceAll("_", " ")}`,
      detail,
    });
  }
  for (const r of (restricted?.command_receipts ?? []) as Record<string, unknown>[]) {
    rows.push({
      seq: null,
      wall: (r.received_at as string | null) ?? null,
      label: `command ${r.kind ?? "?"} → ${r.outcome ?? "?"}`,
      detail:
        r.outcome === "rejected"
          ? `${r.reason ?? "?"} · expected rev ${r.expected_revision ?? "?"}, server at ${r.committed_revision ?? "?"}`
          : `rev ${r.expected_revision ?? "?"} → ${r.committed_revision ?? "?"}`,
    });
  }
  for (const mc of ((restricted?.model_calls ?? []) as Record<string, unknown>[])) {
    rows.push({
      seq: null,
      wall: (mc.requested_at as string | null) ?? null,
      label: `model call → ${mc.status ?? "?"}`,
      detail:
        `${mc.provider ?? ""} ${String(mc.model ?? "").split("@")[0]}`.trim() +
        `${mc.duration_ms != null ? ` · ${mc.duration_ms}ms` : ""}`,
    });
  }
  return rows;
}

export function buildSimulationHtml(replay: Record<string, unknown>): string {
  const session = (replay.session ?? {}) as Record<string, unknown>;
  const participant = (replay.participant ?? {}) as Record<string, unknown>;
  const restricted = (replay.restricted ?? null) as Record<string, unknown> | null;
  const events = (participant.events ?? []) as Ev[];

  const records: Record<string, unknown> = {};
  for (const r of (restricted?.records ?? []) as Record<string, unknown>[]) {
    records[String(r.record_id)] = {
      crate: r.crate,
      stamp: r.stamp,
      body: r.body,
      location: r.location_id ?? null,
    };
  }
  const initialFile = ((restricted?.file_membership ?? []) as Record<string, unknown>[])
    .filter((m) => m.source === "initial")
    .map((m) => String(m.record_id));

  const worldRows = (restricted?.world ?? []) as Record<string, unknown>[];
  const worldContent = (worldRows[0]?.content ?? {}) as Record<string, unknown>;
  const assigned = (worldContent.assigned ?? []) as number[];

  const sim = {
    meta: {
      session_id: session.session_id ?? "?",
      mode: session.mode ?? "?",
      timing_mode: session.timing_mode ?? "?",
      manifest: session.manifest_id ?? "?",
      arm: session.arm ?? "?",
      stage: session.stage ?? "?",
      terminal: session.terminal_reason ?? null,
      // Harness runs stamp the bot strategy on the payload (runner.py); live
      // sessions carry none.
      persona: (replay.persona as string | undefined) ?? null,
      generated_at: new Date().toISOString(),
      restricted: Boolean(restricted),
    },
    // AI_ASSISTANT_ENABLED=0 sessions never had the pane — the replay hides it
    // and lets the team chat span the right column, matching the live layout.
    ai_assistant:
      (participant.pages as Record<string, unknown> | undefined)?.ai_assistant !==
      false,
    // TIME_PRESSURE_ENABLED era (session-evidence-resolved server-side):
    // flag-off sessions render no check-in, warning banner, or low-time
    // styling; flag-on history keeps the full experience on replay.
    time_pressure:
      (participant.pages as Record<string, unknown> | undefined)
        ?.time_pressure !== false,
    // BONUS_PAYMENT_ENABLED era (session-evidence-resolved server-side):
    // flag-off sessions never saw a bonus offer — the answer-bar line and the
    // background/debrief clauses drop out of the replay.
    bonus_payment:
      (participant.pages as Record<string, unknown> | undefined)
        ?.bonus_payment !== false,
    budget_ms:
      typeof (participant.pages as Record<string, unknown> | undefined)?.budget_s === "number"
        ? ((participant.pages as Record<string, unknown>).budget_s as number) * 1000
        : DEFAULT_BUDGET_MS,
    // Audit F02: the session's persisted merchandise labels — replay pages
    // carry `crate_contents` from the world snapshot; the static map is the
    // legacy fallback for replays that predate it.
    crate_items: {
      ...CRATE_ITEMS,
      ...(((participant.pages as Record<string, unknown> | undefined)
        ?.crate_contents ?? {}) as Record<string, string>),
    },
    locations: LOCATIONS,
    assigned_crates: assigned,
    records,
    initial_file: initialFile,
    events,
    internals: internalRows(restricted),
  };

  // JSON embedded in a script tag: neutralize `</script>` / HTML-breakout chars.
  const payload = JSON.stringify(sim).replace(/</g, "\\u003c").replace(/\u2028|\u2029/g, " ");
  return HTML_DOC.replace("/*__SIM_PAYLOAD__*/", `const SIM=${payload};`);
}

const HTML_DOC = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Session simulation</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #e8e8e8; color: #1c1c1c; display: flex; flex-direction: column; height: 100vh; }
.sim-banner { background: #5b3a86; color: #fff; padding: 4px 14px; font-size: 12.5px; display: flex; gap: 14px; align-items: center; flex: none; }
.sim-banner b { letter-spacing: .04em; }
.task-screen { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.task-header { padding: 10px 14px; background: #222; color: #fff; display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); align-items: center; gap: 8px 14px; font-size: 15px; flex: none; }
.task-heading { display: flex; flex-wrap: wrap; gap: 6px 14px; min-width: 0; }
.task-header .goal { color: #ccc; }
.mine-strip { background: rgba(255,255,255,.12); padding: 2px 9px; border-radius: 3px; justify-self: end; max-width: 100%; overflow-wrap: anywhere; text-align: right; }
.status-strip { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.timer { font-variant-numeric: tabular-nums; font-size: 19px; font-weight: 700; padding: 2px 10px; border-radius: 4px; }
.timer.low-time { background: #8a1f1f; color: #fff; }
.stage-tag { font-size: 12.5px; color: #ffd9a8; }
.notice-bar { background: #fff4d6; border-bottom: 1px solid #e0c97f; padding: 8px 14px; font-size: 14px; flex: none; }
.task-grid { flex: 1; display: grid; grid-template-columns: 45% 1fr; grid-template-rows: 55% 1fr; gap: 10px; padding: 10px; min-height: 0; }
.pane { background: #fff; border: 1px solid #ddd; border-radius: 8px; display: flex; flex-direction: column; min-height: 0; min-width: 0; overflow: hidden; }
.pane-head { padding: 8px 12px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: baseline; gap: 8px; flex: none; }
.pane-head h2 { margin: 0; font-size: 15px; }
.pane-head .count { margin-left: auto; font-size: 12px; color: #777; }
.pane[data-pane="case-file"] { grid-column: 1; grid-row: 1 / 3; }
.pane[data-pane="team-chat"] { grid-column: 2; grid-row: 1; border-color: #c8d6ec; }
.pane[data-pane="assistant"] { grid-column: 2; grid-row: 2; border-color: #d9cfec; }
.pane[data-pane="team-chat"] .pane-head { background: #e6eef9; color: #274a71; }
.pane[data-pane="assistant"] .pane-head { background: #efe9f9; color: #5b3a86; }
.task-grid.no-assistant .pane[data-pane="team-chat"] { grid-row: 1 / 3; }
.files-list { list-style: none; margin: 0; padding: 8px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 8px; }
.file { border: 1px solid #ddd; border-radius: 8px; padding: 6px 10px; font-size: 14px; background: #fff; }
.file.from-team { border-left: 4px solid #6a3fb5; background: #fbf9fe; }
.file-head { display: flex; gap: 8px; align-items: baseline; }
.file-stamp { font-size: 11px; color: #777; font-weight: 700; }
.file-crate { font-size: 11px; font-weight: 700; color: #333; }
.file-source { font-size: 11px; color: #6a3fb5; margin-left: auto; }
.file-body { white-space: pre-wrap; margin-top: 2px; }
.file-actions { margin-top: 4px; }
.shared-tag { font-size: 11.5px; color: #1a6b2f; border: 1px solid #9ccfa8; border-radius: 4px; padding: 2px 7px; background: #effaf1; }
.msgs { list-style: none; margin: 0; padding: 8px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 8px; }
.msg { border: 1px solid #e0e0e0; border-radius: 8px; padding: 6px 10px; font-size: 14px; line-height: 1.45; max-width: 92%; }
.msg.mine { background: #f3f6f5; align-self: flex-end; }
.msg.sys { background: #fbf7e8; border-color: #e5d9a8; font-style: italic; font-size: 13px; }
.msg .who { display: block; font-size: 11.5px; font-weight: 700; color: #555; margin-bottom: 2px; }
.msg-body { white-space: pre-wrap; }
.filepill { display: block; margin-top: 6px; border: 1px solid #ccc; border-radius: 6px; padding: 6px 8px; font-size: 13px; background: #fafafa; }
.filepill .fs { display: block; font-size: 11px; color: #777; }
.submit-bar { flex: none; border-top: 2px solid #333; background: #f4f4f4; padding: 10px 14px; }
.submit-title { margin: 0 0 2px; font-size: 14px; }
.submit-bar .prompt { margin: 0 0 8px; font-size: 12.5px; color: #555; }
.submit-row { display: flex; gap: 12px; flex-wrap: wrap; }
.crate-picker { display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
.crate-picker select { font-size: 13px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 5px; background: #fff; }
.crate-picker[data-status="saved"] select { border-color: #9ccfa8; }
.save-status { font-size: 11px; color: #1a6b2f; min-height: 13px; }
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: none; align-items: center; justify-content: center; z-index: 10; }
.overlay.open { display: flex; }
.overlay-box { background: #fff; border-radius: 10px; padding: 20px 24px; max-width: 460px; width: 92%; box-shadow: 0 8px 30px rgba(0,0,0,.3); }
.overlay-box h2 { margin: 0 0 4px; font-size: 18px; }
.overlay-sub { margin: 0 0 8px; color: #666; font-size: 13px; }
.checkin-deadline { font-size: 13px; color: #8a6d00; margin: 0 0 10px; }
.likert-row { display: flex; gap: 6px; margin: 10px 0 4px; }
.likert-row button { width: 38px; height: 38px; border: 1px solid #bbb; border-radius: 6px; background: #fff; font-size: 15px; }
.likert-row button.chosen { background: #274a71; color: #fff; border-color: #274a71; }
.scale-ends { display: flex; justify-content: space-between; font-size: 12px; color: #777; margin-bottom: 8px; }
.overlay-note { font-size: 12.5px; color: #777; }
.reveal-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.reveal-chip { border: 1px solid #ccc; border-radius: 14px; padding: 4px 12px; font-size: 13px; }
.stage-card { flex: 1; display: flex; align-items: flex-start; justify-content: center; padding: 20px; min-height: 0; overflow-y: auto; }
.stage-card > div { background: #fff; border: 1px solid #ddd; border-radius: 10px; padding: 26px 32px; max-width: 520px; width: 100%; margin: auto 0; }
.stage-card h2 { margin: 0 0 10px; }
.stage-card ul { margin: 0; padding-left: 18px; font-size: 13.5px; color: #444; }
.stage-card li { margin: 3px 0; }
.controls { flex: none; background: #181818; color: #eee; display: flex; align-items: center; gap: 10px; padding: 8px 14px; font-size: 13px; }
.controls button { font-size: 13px; padding: 4px 12px; border-radius: 5px; border: 1px solid #555; background: #2a2a2a; color: #eee; cursor: pointer; }
.controls button:hover { background: #3a3a3a; }
.controls input[type="range"] { flex: 1; }
.controls .pos { font-variant-numeric: tabular-nums; white-space: nowrap; }
.internals { background: #141414; color: #bbb; font-size: 12px; max-height: 140px; overflow-y: auto; padding: 4px 14px 8px; flex: none; display: none; font-family: ui-monospace, monospace; }
.internals.open { display: block; }
.internals .irow { padding: 1px 0; border-bottom: 1px solid #222; }
.internals .irow .t { color: #777; margin-right: 8px; }
.gap-note { text-align: center; font-size: 11.5px; color: #999; font-style: italic; }
button.rec { pointer-events: none; }
@media (max-width: 900px) {
  body { height: auto; min-height: 100vh; overflow: auto; }
  .task-screen { flex: none; min-height: 0; }
  .task-grid { display: flex; flex-direction: column; flex: none; }
  .pane { flex: none; }
  .files-list, .msgs { max-height: 45vh; overflow-y: auto; }
  .stage-card { flex: none; min-height: 40vh; }
  .submit-bar { flex: none; }
}
</style>
</head>
<body>
<div class="sim-banner"><b>SIMULATION</b><span id="sim-meta"></span><span style="margin-left:auto">researcher copy — not participant-facing</span></div>
<div class="task-screen">
  <header class="task-header">
    <div class="task-heading"><b>Case file</b><span class="goal">Locate your crates, then record your answers at bottom of page</span></div>
    <span class="status-strip"><span class="stage-tag" id="stageTag"></span><span class="timer" id="timer">—</span></span>
    <span class="mine-strip" id="mine"></span>
  </header>
  <div class="notice-bar" id="noticeBar" style="display:none"></div>
  <div class="task-grid" id="grid">
    <section class="pane" data-pane="case-file"><header class="pane-head"><h2>Case file</h2><span class="count" id="fileCount"></span></header><ul class="files-list" id="fileList"></ul></section>
    <section class="pane" data-pane="team-chat"><header class="pane-head"><h2>Team chat</h2></header><ul class="msgs" id="chatList"></ul></section>
    <section class="pane" data-pane="assistant"><header class="pane-head"><h2>AI assistant</h2></header><ul class="msgs" id="assistList"></ul></section>
  </div>
  <div class="stage-card" id="stageCard" style="display:none"><div><h2 id="stageTitle"></h2><ul id="stageLines"></ul></div></div>
  <footer class="submit-bar" id="submitBar"><div class="submit-head"><h2 class="submit-title">Your answers</h2><p class="prompt" id="answerPrompt">Record where each of your crates is currently located. Answers are recorded automatically when the clock runs out.</p></div><div class="submit-row" id="submitRow"></div></footer>
</div>
<div class="overlay" id="overlay"><div class="overlay-box" id="overlayBox"></div></div>
<div class="internals" id="internals"></div>
<div class="controls">
  <button id="playBtn">Play</button>
  <select id="speed"><option value="1">1×</option><option value="2">2×</option><option value="4" selected>4×</option><option value="8">8×</option><option value="16">16×</option><option value="0">instant</option></select>
  <input type="range" id="scrub" min="0" value="0">
  <span class="pos" id="posLabel"></span>
  <button id="internalsBtn">internals</button>
</div>
<script>
/*__SIM_PAYLOAD__*/
(function () {
  "use strict";
  var evs = SIM.events;
  var internals = SIM.internals;
  var records = SIM.records;
  var fileIds = {};
  var chatSeen = {};
  var idx = 0, playing = false, timer = null, intIdx = 0;

  // AI_ASSISTANT_ENABLED=0 sessions had no assistant pane — hide it so the
  // replay matches the layout the participant actually saw.
  if (SIM.ai_assistant === false) {
    el("grid").classList.add("no-assistant");
    var assistPane = document.querySelector('.pane[data-pane="assistant"]');
    if (assistPane) assistPane.style.display = "none";
  }

  // BONUS_PAYMENT_ENABLED=0 sessions never saw the answer-bar bonus line.
  if (SIM.bonus_payment !== false) {
    el("answerPrompt").innerHTML += " <b>You are eligible for a bonus based on how many locations you correctly identify.</b>";
  }

  function el(id) { return document.getElementById(id); }
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function fmt(ms) { if (ms == null) return "—"; var s = Math.max(0, Math.round(ms / 1000)); var m = Math.floor(s / 60); return String(m).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
  function stageLabel(s) { return (s || "").replaceAll("_", " "); }

  // ---- state ------------------------------------------------------------------
  var S = { stage: "", activeMs: 0, answers: {}, frozen: false, fileCount: 0, checkinCopy: null };

  function seedFile() {
    for (var i = 0; i < SIM.initial_file.length; i++) addFileCard(SIM.initial_file[i], "initial", null);
  }
  function addFileCard(rid, source, sharedBy) {
    if (!rid || fileIds[rid]) return;
    fileIds[rid] = true;
    var r = records[rid] || {};
    var li = document.createElement("li");
    li.className = "file" + (source && source !== "initial" ? " from-team" : "");
    li.id = "file-" + rid;
    li.innerHTML =
      '<div class="file-head"><span class="file-stamp">' + esc(r.stamp || rid) + '</span>' +
      (r.crate != null ? '<span class="file-crate">Crate ' + esc(String(r.crate)) + '</span>' : "") +
      (source && source !== "initial" ? '<span class="file-source">' + esc(sharedBy ? "Shared by " + sharedBy : "From team chat") + '</span>' : "") +
      '</div><div class="file-body">' + esc(r.body || "(record " + rid + ")") + '</div>' +
      '<div class="file-actions"><span class="shared-tag" data-sharetag style="display:none">Shared in team chat</span></div>';
    el("fileList").appendChild(li);
    S.fileCount++;
    el("fileCount").textContent = S.fileCount + " entries";
  }
  function markShared(rid) {
    var li = document.getElementById("file-" + rid);
    if (li) { var tag = li.querySelector("[data-sharetag]"); if (tag) tag.style.display = ""; }
  }
  function bubble(listId, who, body, cls, recordId) {
    var li = document.createElement("li");
    li.className = "msg" + (cls ? " " + cls : "");
    li.innerHTML = '<span class="who">' + esc(who) + '</span><span class="msg-body">' + esc(body) + '</span>';
    var list = el(listId);
    list.appendChild(li);
    if (recordId) {
      // UX round 4, items 9/11: the record is its own card, sibling to the
      // bubble; the "in your case file" flag shows on teammate shares only.
      var r = records[recordId] || {};
      var card = document.createElement("li");
      card.className = "intel-card" + (cls === "mine" ? " mine" : "");
      card.innerHTML = '<span class="filepill"><span class="fs">' + esc(r.stamp || "") + '</span>' +
        (r.crate != null ? '<span class="file-crate">Crate ' + esc(String(r.crate)) + '</span> — ' : "") +
        esc(r.body || recordId) +
        (cls === "mine" ? "" : '<span class="file-source"> — in your case file</span>') + '</span>';
      list.appendChild(card);
    }
    list.scrollTop = list.scrollHeight;
  }
  function stageLine(text) {
    var ul = el("stageLines");
    var li = document.createElement("li");
    li.textContent = text;
    ul.appendChild(li);
  }
  function isWorkbenchStage(s) {
    return ["solo_review", "task", "checkin_collect", "checkin_wait", "checkin_reveal", "answers_locked"].indexOf(s) >= 0;
  }
  function setStage(s) {
    S.stage = s;
    el("stageTag").textContent = stageLabel(s);
    var wb = isWorkbenchStage(s);
    el("grid").style.display = wb ? "" : "none";
    el("submitBar").style.display = wb ? "" : "none";
    el("stageCard").style.display = wb ? "none" : "flex";
    el("stageTitle").textContent = stageLabel(s).replace(/^./, function (c) { return c.toUpperCase(); }) || "…";
    if (["checkin_collect", "checkin_wait", "checkin_reveal"].indexOf(s) < 0) {
      el("overlay").classList.remove("open");
      S.checkinCopy = null;
    }
  }

  // ---- check-in overlay ---------------------------------------------------------
  function renderCheckin(phase, copy, own, assigned) {
    var ov = el("overlay"), box = el("overlayBox");
    if (!phase || phase === "closed") { ov.classList.remove("open"); return; }
    ov.classList.add("open");
    copy = copy || {};
    var h = '<h2>Brief team check-in</h2><p class="overlay-sub">The task clock is paused.</p>' +
      '<p class="checkin-deadline">All members should respond within 60 seconds so the group task can continue.</p>';
    if (phase === "collect") {
      h += '<p>' + esc(copy.question || "How heavy does your current workload feel right now?") + '</p><div class="likert-row">';
      for (var n = 1; n <= 7; n++) h += '<button class="rec">' + n + '</button>';
      h += '</div><div class="scale-ends"><span>' + esc(copy.scale_min_label || "Low") + '</span><span>' + esc(copy.scale_max_label || "High") + '</span></div><p class="overlay-note">' + esc(copy.note || "") + '</p>';
    } else if (phase === "wait") {
      h += '<p>' + esc(copy.collecting || "Collecting your teammates\u2019 ratings\u2026") + '</p>';
    } else if (phase === "reveal") {
      h += '<h3>' + esc(copy.reveal_title || "Ratings reported") + '</h3><div class="reveal-row">';
      var vals = assigned || {};
      for (var k in vals) h += '<span class="reveal-chip">' + esc(k) + ': <b>' + esc(vals[k]) + '</b></span>';
      h += '<span class="reveal-chip">You: <b>' + esc(own == null ? "—" : own) + '</b></span></div>' +
        '<p class="overlay-note">' + esc(copy.reveal_note || "") + '</p><button class="rec" style="padding:8px 18px;border-radius:6px;background:#274a71;color:#fff;border:0">' + esc(copy.resume_label || "Resume task") + '</button>';
    }
    box.innerHTML = h;
  }

  // ---- event application ---------------------------------------------------------
  function apply(ev) {
    var p = ev.payload || {};
    if (ev.active_ms != null) S.activeMs = ev.active_ms;
    if (ev.stage && ev.stage !== S.stage) setStage(ev.stage);
    switch (ev.type) {
      case "session_opened": stageLine("Session opened — " + (p.mode || "")); break;
      case "consent_recorded": stageLine("Consent: " + (p.action || "")); break;
      case "initials_recorded": stageLine("Display initials recorded"); break;
      case "training_started": stageLine("Orientation started"); break;
      case "training_step": stageLine("Training step: " + (p.step || "")); break;
      case "stage_proceeded": if (p.to) setStage(p.to); stageLine("Stage advanced → " + (p.to || "")); if (p.to === "task" || p.to === "solo_review") seedFile(); break;
      case "teammate_presented": stageLine((ev.actor || "?") + " joined (" + (p.seat || "") + ")"); break;
      case "greeting_prompt": stageLine("Greeting options shown"); if (Array.isArray(p.options)) bubble("chatList", "System", "Greeting options: " + p.options.join(" / "), "sys"); break;
      case "task_clock_started": setStage(p.stage || "task"); seedFile(); break;
      case "task_deadline": stageLine("Time ran out — answers recorded"); break;
      case "message_accepted":
      case "message_delivered": {
        // The participant's visible artifacts are the message rows: an ask is
        // both a bookkeeping event (assistant_requested / teammate_requested)
        // and a message_accepted — rendering both doubles every "You" bubble.
        var mid = p.message_id || null;
        if (mid && chatSeen[mid]) break;
        if (mid) chatSeen[mid] = true;
        var mine = ev.actor === "participant";
        if (p.channel === "assistant") bubble("assistList", mine ? "You" : "AI assistant", p.body || "", mine ? "mine" : "");
        else if (p.channel === "system") bubble("chatList", "System", p.body || "", "sys", p.record_id);
        else bubble("chatList", mine ? "You" : (ev.actor || "?"), p.body || "", mine ? "mine" : "", p.record_id);
        if (p.record_id) addFileCard(p.record_id, "added_from_team", mine ? null : ev.actor);
        break;
      }
      // Bookkeeping only — the visible ask/share lands via the message rows
      // above (assistant_requested → accepted assistant message;
      // teammate_requested → "Requesting intel on Crate N." in team chat).
      case "assistant_requested": break;
      case "teammate_requested": break;
      case "record_shared":
        markShared(p.record_id);
        bubble("chatList", "You", p.crate != null ? "Sharing intel on Crate " + p.crate : "Sharing intel", "mine", p.record_id);
        break;
      case "record_added_to_file": addFileCard(p.record_id, "added_from_team", p.shared_by || null); break;
      case "answer_updated": S.answers[p.crate] = p.location_id; renderAnswers(); break;
      case "answers_frozen": S.frozen = true; renderAnswers(); stageLine("Answers locked"); break;
      case "warning_committed": if (SIM.time_pressure === false) break; el("noticeBar").style.display = ""; el("noticeBar").textContent = "Low on time — finishing soon."; break;
      case "checkin_open": if (SIM.time_pressure === false) break; S.checkinCopy = p.copy || {}; renderCheckin("collect", S.checkinCopy, null, null); break;
      case "checkin_response": if (SIM.time_pressure === false) break; renderCheckin("wait", S.checkinCopy, null, null); break;
      case "checkin_reveal": if (SIM.time_pressure === false) break; renderCheckin("reveal", S.checkinCopy, p.own_rating, p.assigned_values); break;
      case "qa_asked": stageLine("Rules Q&A asked: " + String(p.question || "").slice(0, 80)); break;
      case "qa_answered": stageLine("Rules Q&A answered" + (p.status === "fallback" ? " (fallback)" : "")); break;
      case "confidence_recorded": stageLine("Confidence — crate " + (p.crate ?? "?") + (p.skipped ? " skipped" : " recorded")); break;
      case "item_recorded": stageLine((p.item_id || "item") + (p.skipped ? " — skipped" : "")); break;
      case "peer_rating_recorded": stageLine("Peer rating recorded"); break;
      case "instrument_complete": stageLine("Instrument complete: " + (p.stage || "")); break;
      case "debrief_acknowledged": stageLine("Debrief acknowledged"); break;
      case "acknowledgment_skipped": stageLine("Acknowledgment skipped"); break;
      case "data_use_recorded": stageLine("Data-use choice: " + (p.choice || "")); break;
      case "session_completed": stageLine("Session completed"); break;
      case "session_terminated": stageLine("Session terminated — " + (p.reason || "")); break;
      case "session_abandoned": stageLine("Session abandoned" + (p.idle_window_s != null ? " (idle " + p.idle_window_s + "s)" : "")); break;
      case "technical_stop": stageLine("Technical stop — " + (p.outcome || "")); break;
      case "resumed": case "session_resumed": stageLine("Session resumed"); break;
      case "writer_takeover": stageLine("Writer takeover (another window took over)"); break;
    }
  }

  // ---- internals rail ---------------------------------------------------------------
  function pushInternalsUpto(wall) {
    var box = el("internals");
    while (intIdx < internals.length) {
      var r = internals[intIdx];
      if (wall && r.wall && r.wall > wall) break;
      var d = document.createElement("div");
      d.className = "irow";
      d.innerHTML = '<span class="t">' + esc((r.wall || "").slice(11, 19)) + '</span>' + esc(r.label) + (r.detail ? " — " + esc(r.detail) : "");
      box.appendChild(d);
      intIdx++;
    }
    box.scrollTop = box.scrollHeight;
  }

  // ---- render + playback ---------------------------------------------------------
  function renderAnswers() {
    var row = el("submitRow");
    row.innerHTML = "";
    for (var i = 0; i < SIM.assigned_crates.length; i++) {
      var c = SIM.assigned_crates[i];
      var v = S.answers[c];
      var div = document.createElement("div");
      div.className = "crate-picker";
      div.setAttribute("data-status", v ? "saved" : "clean");
      var opts = '<option value="">— choose a location —</option>';
      for (var j = 0; j < SIM.locations.length; j++) {
        var L = SIM.locations[j];
        opts += '<option' + (v === L ? " selected" : "") + '>' + esc(L) + '</option>';
      }
      div.innerHTML = '<label>Crate ' + c + (SIM.crate_items[c] ? ' — ' + SIM.crate_items[c] : "") + '</label><select disabled>' + opts + '</select><span class="save-status">' + (S.frozen ? "" : v ? "Saved" : "") + '</span>';
      row.appendChild(div);
    }
  }
  function renderTimer() {
    var t = el("timer");
    if (S.stage === "task" || S.stage === "solo_review" || S.stage.indexOf("checkin") === 0 || S.stage === "answers_locked") {
      var rem = SIM.budget_ms - S.activeMs;
      t.textContent = fmt(rem);
      t.className = "timer" + (rem < 60000 && SIM.time_pressure !== false ? " low-time" : "");
    } else t.textContent = "—";
  }
  function renderMeta() {
    el("mine").textContent = "Your crates: " + SIM.assigned_crates.join(", ");
    el("sim-meta").textContent =
      "session " + String(SIM.meta.session_id).slice(0, 8) + " · " + SIM.meta.mode + "/" + SIM.meta.timing_mode +
      " · manifest " + SIM.meta.manifest + " · arm " + SIM.meta.arm +
      (SIM.meta.persona ? " · persona " + SIM.meta.persona : "") +
      (SIM.meta.terminal ? " · ended " + SIM.meta.terminal : "") +
      " · " + evs.length + " events" + (SIM.meta.restricted ? "" : " (participant view only — sign in as researcher for internals)");
  }
  function renderPos() {
    var ev = evs[Math.max(0, idx - 1)] || {};
    el("posLabel").textContent = "event " + idx + "/" + evs.length + " · T+" + fmt(ev.active_ms) + " · " + stageLabel(S.stage);
    el("scrub").value = String(idx);
    renderTimer();
  }

  function delayFor(i) {
    if (i <= 0) return 400;
    var a = evs[i - 1], b = evs[i];
    var ta = a.active_ms, tb = b.active_ms;
    var gap;
    if (ta != null && tb != null) gap = tb - ta;
    else if (a.server_time && b.server_time) gap = Date.parse(b.server_time) - Date.parse(a.server_time);
    else gap = 800;
    return Math.min(Math.max(gap, 120), 4000);
  }
  function step() {
    if (idx >= evs.length) { pause(); el("playBtn").textContent = "Replay"; return; }
    var ev = evs[idx];
    idx++;
    apply(ev);
    if (ev.server_time) pushInternalsUpto(ev.server_time);
    renderPos();
    if (playing) timer = setTimeout(step, speed() === 0 ? 0 : delayFor(idx) / speed());
  }
  function speed() { var v = Number(el("speed").value); return isNaN(v) ? 4 : v; }
  function play() { if (idx >= evs.length) seek(0); playing = true; el("playBtn").textContent = "Pause"; timer = setTimeout(step, 120); }
  function pause() { playing = false; el("playBtn").textContent = "Play"; if (timer) clearTimeout(timer); }
  function seek(n) {
    pause();
    idx = 0; intIdx = 0; fileIds = {}; chatSeen = {};
    S = { stage: "", activeMs: 0, answers: {}, frozen: false, fileCount: 0 };
    el("chatList").innerHTML = ""; el("assistList").innerHTML = ""; el("fileList").innerHTML = "";
    el("stageLines").innerHTML = ""; el("internals").innerHTML = ""; el("overlay").classList.remove("open");
    S.checkinCopy = null;
    el("noticeBar").style.display = "none";
    setStage("consent");
    for (var i = 0; i < n; i++) { var ev = evs[i]; apply(ev); if (ev.server_time) pushInternalsUpto(ev.server_time); idx++; }
    renderPos();
  }

  el("playBtn").onclick = function () { playing ? pause() : play(); };
  el("scrub").oninput = function () { seek(Number(el("scrub").value)); };
  el("internalsBtn").onclick = function () { el("internals").classList.toggle("open"); };
  el("scrub").max = String(evs.length);
  document.addEventListener("keydown", function (e) { if (e.code === "Space" && e.target === document.body) { e.preventDefault(); playing ? pause() : play(); } });
  renderMeta(); setStage(evs.length ? (evs[0].stage || "consent") : "consent"); renderAnswers(); renderPos();
})();
</script>
</body>
</html>
`;

/** Build the sim HTML and trigger a browser download. */
export function downloadSimulation(replay: Record<string, unknown>): void {
  const html = buildSimulationHtml(replay);
  const sid = String(
    ((replay.session ?? {}) as Record<string, unknown>).session_id ?? "session",
  ).slice(0, 8);
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `session-${sid}-simulation.html`;
  a.click();
  URL.revokeObjectURL(url);
}
