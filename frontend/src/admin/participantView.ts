/**
 * Participant view export (admin): given the replay payload, build a single
 * self-contained HTML document that walks the session *page by page*, the way the
 * participant saw it — consent form, orientation, practice, connecting, the task
 * workbench, check-in overlay, confidence, questionnaires, debrief, completion.
 *
 * Everything rendered comes from ``participant.pages`` (bound artifact copy +
 * this participant's recorded responses) and the participant-projected event
 * stream — no truth, holdings, model calls or scheduler internals are in the
 * payload. It is still a researcher tool: share it only inside the research team.
 *
 * Literal-copy screens (orientation, connecting, debrief, terminal) reproduce the
 * component copy from ``frontend/src/components/StageScreens.tsx`` — the bound
 * artifacts only carry consent/training text, so if those literals change this
 * file should be updated to match.
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

// Legacy fallback merchandise map (audit F02): the session's own persisted
// crate contents (`pages.crate_contents`) are authoritative — this map only
// covers replays whose payload predates that field.
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

function crateItem(pages: P, crate: number): string {
  const persisted = (pages.crate_contents ?? {}) as Record<string, string>;
  return persisted[String(crate)] ?? CRATE_ITEMS[crate] ?? "";
}

const DEFAULT_BUDGET_MS = 360_000;

// Replay payloads are heterogeneous historical JSON (every build since v4); this
// renderer reads them defensively with `?? fallback`. The escape hatch is scoped to
// this one alias instead of the whole file (audit L4).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type P = Record<string, any>;

function esc(s: unknown): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtClock(iso: unknown): string {
  if (!iso) return "";
  const d = Date.parse(String(iso));
  if (Number.isNaN(d)) return "";
  return new Date(d).toTimeString().slice(0, 8);
}

/** End-state of the participant's workbench, folded from participant-visible events. */
function foldWorkbench(events: Ev[], wb: P) {
  const records = (wb.records ?? {}) as Record<string, P>;
  const fileIds = new Set<string>();
  const files: { rid: string; source: string; sharedBy?: string | undefined }[] = [];
  const chat: { who: string; body: string; mine: boolean; sys: boolean; rid?: string }[] = [];
  const assist: { who: string; body: string; mine: boolean }[] = [];
  const answers: Record<string, string> = {};
  const seenMsg = new Set<string>();
  const addFile = (rid: string, source: string, sharedBy?: string) => {
    if (!rid || fileIds.has(rid)) return;
    fileIds.add(rid);
    files.push({ rid, source, sharedBy });
  };
  for (const rid of (wb.initial_file ?? []) as string[]) addFile(rid, "initial");
  for (const ev of events) {
    const p = (ev.payload ?? {}) as P;
    if (ev.type === "message_accepted" || ev.type === "message_delivered") {
      const mid = p.message_id as string | undefined;
      if (mid && seenMsg.has(mid)) continue;
      if (mid) seenMsg.add(mid);
      const mine = ev.actor === "participant";
      if (p.channel === "assistant") {
        assist.push({ who: mine ? "You" : "AI assistant", body: p.body ?? "", mine });
      } else if (p.channel === "system") {
        chat.push({ who: "System", body: p.body ?? "", mine: false, sys: true, rid: p.record_id });
      } else {
        chat.push({ who: mine ? "You" : ev.actor ?? "?", body: p.body ?? "", mine, sys: false, rid: p.record_id });
      }
      if (p.record_id) addFile(String(p.record_id), "added_from_team", mine ? undefined : ev.actor);
    } else if (ev.type === "record_shared") {
      chat.push({
        who: "You",
        body: p.crate != null ? `Sharing intel on Crate ${p.crate}.` : "Sharing intel.",
        mine: true,
        sys: false,
        rid: p.record_id,
      });
    } else if (ev.type === "record_added_to_file") {
      addFile(String(p.record_id), "added_from_team", p.shared_by as string | undefined);
    } else if (ev.type === "answer_updated") {
      answers[String(p.crate)] = String(p.location_id);
    }
  }
  for (const [c, loc] of Object.entries((wb.frozen ?? {}) as Record<string, string>)) answers[c] = loc;
  return { records, files, chat, assist, answers };
}

function fileCard(rid: string, r: P | undefined, source: string, sharedBy?: string): string {
  const tag =
    source === "initial"
      ? ""
      : `<span class="file-source">${esc(sharedBy ? `Shared by ${sharedBy}` : "From team chat")}</span>`;
  return `<li class="file${source === "initial" ? "" : " from-team"}"><div class="file-head"><span class="file-stamp">${esc(
    r?.stamp ?? rid,
  )}</span>${r?.crate != null ? `<span class="file-crate">Crate ${esc(String(r.crate))}</span>` : ""}${tag}</div><div class="file-body">${esc(r?.body ?? `(record ${rid})`)}</div></li>`;
}

function bubble(m: { who: string; body: string; mine: boolean; sys?: boolean; rid?: string }, records: Record<string, P>): string {
  let h = `<li class="msg${m.mine ? " mine" : ""}${m.sys ? " sys" : ""}"><span class="who">${esc(m.who)}</span><span class="msg-body">${esc(m.body)}</span>`;
  if (m.rid) {
    const r = records[m.rid] ?? {};
    h += `<span class="filepill"><span class="fs">${esc(r.stamp ?? "")}</span>${esc(r.body ?? m.rid)}${m.mine ? "" : '<span class="file-source"> — in your case file</span>'}</span>`;
  }
  return `${h}</li>`;
}

/** The task workbench as it stood when the stage closed — a static snapshot. */
function workbenchPage(pages: P, events: Ev[], note: string): string {
  const wb = (pages.workbench ?? {}) as P;
  // AI_ASSISTANT_ENABLED=0 sessions never had the pane — omit it and let the
  // team chat span the right column, matching the live layout.
  const ai = pages.ai_assistant !== false;
  const folded = foldWorkbench(events, { ...wb, frozen: pages.frozen_answers });
  const files = folded.files
    .map((f) => fileCard(f.rid, folded.records[f.rid], f.source, f.sharedBy))
    .join("");
  const chat = folded.chat.map((m) => bubble(m, folded.records)).join("");
  const assist = folded.assist
    .map((m) => `<li class="msg${m.mine ? " mine" : ""}"><span class="who">${esc(m.who)}</span><span class="msg-body">${esc(m.body)}</span></li>`)
    .join("");
  const assigned = (wb.assigned_crates ?? []) as number[];
  const answerCells = assigned
    .map((c) => {
      const v = folded.answers[String(c)];
      const item = crateItem(pages, c);
      return `<div class="crate-picker"><label>Crate ${c}${item ? ` — ${esc(item)}` : ""}</label><select disabled><option>${v ? esc(v) : "— no answer —"}</option></select></div>`;
    })
    .join("");
  return `
  <div class="wb">
    <header class="task-header"><div class="task-heading"><b>Case file</b><span class="goal">Locate your crates, then record your answers at bottom of page</span></div>
      <span class="status-strip"><span class="timer">0:00</span></span>
      <span class="mine-strip">Your crates: ${esc(assigned.join(", ") || "—")}</span>
      </header>
    <div class="task-grid${ai ? "" : " no-assistant"}">
      <section class="pane" data-pane="case-file"><header class="pane-head"><h2>Case file</h2><span class="count">${folded.files.length} entries</span></header><ul class="files-list">${files || '<li class="msg sys">No entries</li>'}</ul></section>
      <section class="pane" data-pane="team-chat"><header class="pane-head"><h2>Team chat</h2></header><ul class="msgs">${chat || '<li class="msg sys">No messages</li>'}</ul></section>
      ${ai ? `<section class="pane" data-pane="assistant"><header class="pane-head"><h2>AI assistant</h2></header><ul class="msgs">${assist || '<li class="msg sys">No assistant exchanges</li>'}</ul></section>` : ""}
    </div>
    <footer class="submit-bar"><h2 class="submit-title">Your answers</h2><p class="prompt">${Number(pages.materials?.training?.artifact_version ?? 0) >= 8 ? "Record where each of your crates is currently located." : "Enter a location for each of your crates."} Answers are recorded automatically when the clock runs out.${pages.bonus_payment !== false ? " <b>You are eligible for a bonus based on how many locations you correctly identify.</b>" : ""}</p><div class="submit-row">${answerCells}</div></footer>
  </div>
  <p class="page-note">${esc(note)}</p>`;
}

function materialText(mat: P | null | undefined): string {
  const t = mat?.body?.text;
  if (!t) {
    return `<p class="placeholder-note">The bound material (${esc(
      mat?.artifact_key ?? "unbound",
    )}) is a reference pointer with no rendered text — the participant saw a placeholder notice, not real copy.</p>`;
  }
  let h = "";
  if (t.subheading) h += `<p class="consent-sub">${esc(t.subheading)}</p>`;
  for (const s of (t.sections ?? []) as P[]) {
    h += `<section>${s.title ? `<h2>${esc(s.title)}</h2>` : ""}${(s.paragraphs ?? [])
      .map((p: string) => `<p>${esc(p)}</p>`)
      .join("")}</section>`;
  }
  return h;
}

function consentPage(pages: P): string {
  const mat = pages.materials?.consent;
  const text = mat?.body?.text;
  const action = pages.consent?.action as string | undefined;
  const heading = esc(text?.heading ?? "Team Decision-Making Study");
  let h = `<h1>${heading}</h1>${materialText(mat)}`;
  if (text) {
    h += `<p class="consent-question">${esc(text.question ?? "Would you like to participate?")}</p><div class="btn-row">`;
    for (const [act, label] of [
      ["consented", text.accept_label ?? "Yes, I consent to participate"],
      ["declined", text.decline_label ?? "No, I do not wish to participate"],
    ] as const) {
      h += `<button class="${act === "consented" ? "primary" : "secondary"}${action === act ? " chosen" : ""}">${esc(label)}${action === act ? " ✓" : ""}</button>`;
    }
    h += `</div>`;
  }
  if (action) h += `<p class="did">Participant chose: <b>${esc(action)}</b></p>`;
  return h;
}

// 2026-10-15 crate-condition experiment: replay derives crate-count/overlap
// copy from the session's bound ``task_shape`` (absent → legacy 8-crate
// independent deal), so replays always match what the participant saw.
const CRATE_WORDS: Record<number, string> = { 4: "four", 8: "eight", 12: "twelve" };
function crateWord(n: number, capitalized = false): string {
  const w = CRATE_WORDS[n] ?? String(n);
  return capitalized ? w.charAt(0).toUpperCase() + w.slice(1) : w;
}

function instructionsPages(pages: P): string[] {
  const initials = pages.initials;
  const budgetMs = typeof pages.budget_s === "number" ? pages.budget_s * 1000 : DEFAULT_BUDGET_MS;
  const minutes = Math.round(budgetMs / 60000);
  const structured = Boolean(pages.structured_requests);
  const solo = pages.solo_review_seconds != null;
  const ai = pages.ai_assistant !== false;
  // BONUS_PAYMENT_ENABLED era (session-evidence-resolved server-side): flag-off
  // sessions never saw a bonus offer — the replay drops every bonus clause.
  const bonus = pages.bonus_payment !== false;
  const shape = (pages.task_shape ?? {}) as P;
  const nCrates = (shape.n_crates as number) ?? 8;
  const overlap = (shape.crate_overlap as string) ?? "independent";
  // Audit F05: locked owner copy verbatim per overlap level — same wording as
  // StageScreens.tsx; the "will not be told…" clause is superseded under the
  // disclosed levels and `some` is a guaranteed overlap.
  const assignPara =
    overlap === "none"
      ? `<p>Each teammate has been <b>assigned four of the ${crateWord(nCrates)} crates</b> to work on. There will be <b>no overlaps</b> in assignment. Your crates will be assigned after a brief training. Your file may contain information about crates outside your assignment, and that information may be valuable to a teammate.</p>`
      : overlap === "some"
        ? `<p>Each teammate has been <b>assigned four of the ${crateWord(nCrates)} crates</b> to work on. There will be <b>some overlap</b> in your assignments. Your crates will be assigned after a brief training. Your file may contain information about crates outside your assignment, and that information may be valuable to a teammate.</p>`
        : overlap === "full"
          ? `<p>Each teammate will be <b>assigned to work on all four crates</b>. Your crates will be assigned after a brief training.</p>`
          : `<p>Each teammate has been <b>privately assigned four of the eight crates</b> to work on. Assignments can overlap. Your crates will be assigned after a brief training; you will not be told which crates your teammates have been assigned. Your file may contain information about crates outside your assignment, and that information may be valuable to a teammate.</p>`;
  const workspaceBullet =
    overlap === "full"
      ? "Everyone works on the same four crates."
      : "Everyone has a private four-crate assignment.";
  const p1 = `
    <h1>Team-based investigation game</h1>
    <p>For this study, you will be one of three investigators working together on a shared case.</p>
    <h2>Case background</h2>
    <p>${crateWord(nCrates, true)} crates of high-value merchandise have been stolen. The thieves are moving the crates between six known hideouts. As a team, your goal is to identify the locations of all ${crateWord(nCrates)} crates.</p>
    <p>You and your teammates will each receive time-stamped intelligence that will help you solve the mystery. You can choose to share intelligence from your case file in the team chat if you want. You can also ask your teammates to share intelligence with you.</p>
    ${assignPara}
    <p>Your task will be to locate each of the assigned crates${bonus ? ", and as noted, you are eligible to receive a bonus based on your performance" : ""}. Next, you will review your workspace.</p>
    <div class="initials-block"><label>You will be identified to your team by your initials.</label>
      <input disabled value="${esc(initials ?? "")}" placeholder="—">${initials ? ` <span class="did">recorded: ${esc(initials)}</span>` : ""}</div>
    <button class="primary">Continue to your workspace</button>`;
  const p2 = `
    <h1>Your workspace</h1>
    <p>During the task you will see one screen with ${ai ? "three" : "two"} panes and an answer bar:</p>
    <div class="workspace-diagram${ai ? "" : " no-assistant"}"><div class="wd-case">Case file</div><div class="wd-chat">Team chat</div>${ai ? '<div class="wd-ai">AI assistant</div>' : ""}<div class="wd-answers">Submit a location for each of your crates</div></div>
    <ul>
      <li>You will have <b>${minutes} minutes</b> to locate as many of the crates as you can.</li>
      <li>You can drag entries in your case file to reorder them.</li>
      <li>${workspaceBullet}</li>
      ${
        structured
          ? "<li>Ask teammates for intel with the <b>Ask the team about…</b> dropdown in the team chat — or type your own message. Share intel that may help them too.</li>"
          : "<li>Use the team chat to ask teammates for intel and to share information that may help them.</li>"
      }
      ${solo ? `<li>You start with a short solo review of your case file — ${ai ? "the team chat and assistant open" : "the team chat opens"} after it.</li>` : ""}
      <li>You can share an intel entry from your case file in the team chat. Anything you share in the chat will be visible to both of your teammates.</li>
      ${ai ? "<li>The AI assistant can read your case file, including any intel your teammates share with you from their case files in the chat.</li>" : ""}
    </ul>
    <button class="primary">Continue</button>`;
  return [p1, p2];
}

function trainingPage(pages: P, events: Ev[]): string {
  let h = `<h1>Practice: Sharing intel with your team</h1>`;
  const shared = events.some((e) => e.type === "training_step" && (e.payload?.action === "share" || e.payload?.step === "practice"));
  const mat = pages.materials?.training;
  if (mat?.body?.text) h += materialText(mat);
  else
    h += `<p>Try sharing an entry with your team below. The team chat on the right is a preview of what it looks like during the task.</p>
    <p class="page-note">The practice page shows a small fixed file with Share buttons beside each entry and a preview team chat${pages.ai_assistant !== false ? ", then a second page previewing the AI assistant" : ""}.</p>`;
  h += `<p class="did">${shared ? "Participant completed the practice share." : "No practice share was recorded."}</p>`;
  return h;
}

function qaPage(pages: P): string {
  const exchanges = (pages.qa_exchanges ?? []) as P[];
  const newCopy = Number(pages.materials?.training?.artifact_version ?? 0) >= 8;
  let h = `<h1>AI Q&amp;A (Optional)</h1>
  <p>If anything about the rules of the task is unclear, you can ask a question below before you meet your team — the assistant answers questions about how the task works. You do not have to ask anything to continue.</p>
  <p>What is your question? (about the rules of the Investigators task)</p>
  <div class="qa-box"><textarea disabled placeholder="Type your question here…"></textarea><button class="${newCopy ? "primary qa-button" : "secondary"}" disabled>Ask</button></div>`;
  if (!exchanges.length) {
    h += `<p class="did">Participant continued without asking a question.</p>`;
  } else {
    h += `<ol class="qa-log">` +
      exchanges
        .map(
          (x) =>
            `<li><div class="qa-q">${esc(x.question)}</div><div class="qa-a">${esc(
              x.answer ?? "(no answer recorded)",
            )}${x.status === "fallback" ? ' <span class="page-note">[fallback]</span>' : ""}</div></li>`,
        )
        .join("") +
      `</ol>`;
  }
  return h + `<button class="primary${newCopy ? " qa-button" : ""}">Continue</button>`;
}

function connectingPage(pages: P): string {
  const you = pages.initials ? `You (${pages.initials})` : "You";
  // Audit F03: the connecting screen repeats the assignment rule — replay must
  // show the same condition-aware reminder the participant saw.
  const shape = (pages.task_shape ?? {}) as P;
  const overlap = (shape.crate_overlap as string) ?? "independent";
  const nCrates = Number(shape.n_crates ?? 8);
  const newCopy = Number(pages.materials?.training?.artifact_version ?? 0) >= 8;
  const assignment =
    overlap === "full"
      ? "Each teammate will be <b>assigned to work on all four crates</b>."
      : overlap === "none" || overlap === "some"
        ? `Each teammate has been <b>assigned four of the ${crateWord(nCrates)} crates</b> to work on. There will be <b>${overlap === "none" ? "no overlaps</b> in assignment." : "some overlap</b> in your assignments."}`
        : "Each teammate has been <b>privately assigned four of the eight crates</b> to work on. Assignments can overlap.";
  const reminder =
    overlap === "none"
      ? " Each of you is working on your own four crates."
      : overlap === "some"
        ? " Your four-crate assignments overlap."
        : overlap === "full"
          ? " All three of you are working on the same four crates."
          : "";
  return `<h1>Connecting you to your team</h1>
  <ul class="joining-list">
    <li><span class="dot online"></span> JT <span class="status">connected</span></li>
    <li><span class="dot online"></span> ${esc(you)} <span class="status">connected</span></li>
    <li><span class="dot online"></span> PM <span class="status">connected</span></li>
  </ul>
  <p>The clock starts when all of you have pressed Start.</p>
  ${newCopy ? `<p>${assignment}</p>` : ""}
  <p>Remember: Your goal is to identify the current location of ${overlap === "full" ? "the" : "your"} assigned crates.${newCopy ? "" : reminder} You and your teammates will each receive time-stamped intelligence that will help you solve the mystery. Be sure to scroll through your case file and check your record for each crate, including the day, time, and location.</p>
  <button class="primary">Start</button>`;
}

function lockedPage(pages: P): string {
  const frozen = (pages.frozen_answers ?? {}) as Record<string, string>;
  const rows = Object.keys(frozen)
    .sort((a, b) => Number(a) - Number(b))
    .map((c) => `<li>Crate ${c}: <b>${esc(frozen[c])}</b></li>`)
    .join("");
  return `<h1>Time is up</h1><p>Your answers are now locked.</p>${rows ? `<ul>${rows}</ul>` : "<p><i>No answers were entered.</i></p>"}<button class="primary">Continue</button>`;
}

function checkinPage(pages: P): string {
  const ck = pages.checkin ?? {};
  const copy = (ck.copy ?? {}) as P;
  const own = ck.own_rating;
  let row = "";
  for (let n = 1; n <= 7; n++) row += `<button class="rec${own === n ? " chosen" : ""}">${n}</button>`;
  const assigned = (ck.assigned_values ?? {}) as Record<string, number>;
  const chips = Object.entries(assigned)
    .map(([k, v]) => `<span class="reveal-chip">${esc(k)}: <b>${esc(v)}</b></span>`)
    .join("");
  return `<h1>Brief team check-in</h1><p class="overlay-sub">The task clock is paused.</p>
  <p>${esc(copy.question ?? "How heavy does your current workload feel right now?")}</p>
  <div class="likert-row">${row}</div>
  <div class="scale-ends"><span>${esc(copy.scale_min_label ?? "")}</span><span>${esc(copy.scale_max_label ?? "")}</span></div>
  <p class="overlay-note">${esc(copy.note ?? "")}</p>
  <p class="did">${own == null ? "No rating was recorded." : `Participant rated: ${esc(own)}`}</p>
  <h2>${esc(copy.reveal_title ?? "Workload reported at the check-in")}</h2>
  <div class="reveal-row">${chips}<span class="reveal-chip">You: <b>${esc(own ?? "—")}</b></span></div>
  <p class="overlay-note">${esc(copy.reveal_note ?? "")}</p>`;
}

function likertRow(item: P, inst: P, resp: P | undefined, label: string): string {
  const respSpec = inst.response ?? {};
  const val = resp?.value;
  let h = `<div class="item"><p>${esc(label)}</p>`;
  if (item.options?.length) {
    h += `<ul class="choice-list">`;
    for (const o of item.options as string[]) {
      h += `<li class="${resp?.status === "answered" && val === o ? "chosen-opt" : ""}">${esc(o)}${resp?.status === "answered" && val === o ? " ✓" : ""}</li>`;
    }
    h += `</ul>`;
  } else if (respSpec.type === "slider") {
    h += `<div class="slider-readout">${resp?.status === "answered" ? `Confidence: ${esc(val)}%` : "Confidence: —"}</div>
      <div class="fake-slider"><span class="fake-fill" style="width:${resp?.status === "answered" && typeof val === "number" ? val : 0}%"></span></div>
      <div class="scale-ends"><span>${esc(respSpec.min_label ?? "")}</span><span>${esc(respSpec.max_label ?? "")}</span></div>`;
  } else {
    const min = Number(respSpec.min ?? 1);
    const max = Number(respSpec.max ?? 7);
    let row = "";
    for (let n = min; n <= max; n++) row += `<button class="rec${resp?.status === "answered" && val === n ? " chosen" : ""}">${n}</button>`;
    h += `<div class="likert-row">${row}</div><div class="scale-ends"><span>${esc(respSpec.min_label ?? "")}</span><span>${esc(respSpec.max_label ?? "")}</span></div>`;
  }
  if (resp && resp.status !== "answered") h += `<p class="did">${esc(resp.status)}</p>`;
  return `${h}</div>`;
}

function instrumentPage(pages: P, stage: string): string {
  const inst = pages.instruments?.[stage];
  if (!inst) return `<h1>Questions</h1><p class="placeholder-note">No bound instrument was resolved for this stage.</p>`;
  if (inst.applies_to === "per_answered_crate") {
    const crates = (inst.pending_crates ?? []) as number[];
    if (!crates.length)
      return `<h1>Confidence ratings</h1><p>You did not enter a location for any crate, so there is nothing to rate.</p>`;
    const frozen = (pages.frozen_answers ?? {}) as Record<string, string>;
    const cards = crates
      .map((c) => {
        const resp = inst.crate_responses?.[String(c)];
        const answer = frozen[String(c)];
        const item = crateItem(pages, c);
        return `<div class="confidence-card"><p class="card-title">Crate ${c}${item ? ` — ${esc(item)}` : ""}</p>
          ${answer ? `<p class="card-answer">Your answer: <b>${esc(answer)}</b></p>` : ""}
          <div class="slider-readout">${resp?.status === "answered" ? `Confidence: ${esc(resp.value)}%` : "Confidence: —"}</div>
          <div class="fake-slider"><span class="fake-fill" style="width:${resp?.status === "answered" ? Number(resp.value) : 0}%"></span></div>
          <div class="scale-ends"><span>${esc(inst.response?.min_label ?? "")}</span><span>${esc(inst.response?.max_label ?? "")}</span></div>
          ${resp && resp.status !== "answered" ? `<p class="did">${esc(resp.status)}</p>` : ""}</div>`;
      })
      .join("");
    return `<h1>Confidence ratings</h1><p>Rate how confident you are in each of your answers.</p><div class="confidence-grid">${cards}</div>`;
  }
  if (inst.applies_to === "per_teammate_seat") {
    const sections = (inst.seats ?? [])
      .map((seat: string) => {
        const items = (inst.items as P[])
          .map((i) => {
            const text = String(i.text).replaceAll("{seat}", seat);
            return likertRow(i, inst, inst.responses?.[`${i.item_id}.${seat}`], text);
          })
          .join("");
        return `<section><h2>About ${esc(seat)}</h2>${items}</section>`;
      })
      .join("");
    return `<h1>Your teammates</h1>${sections}`;
  }
  // UX round 8: free-text follow-ups render as question + recorded text.
  if (inst.response?.type === "free_text") {
    const parts = (inst.items as P[]).map((i) => {
      const resp = inst.responses?.[String(i.item_id)];
      const val = resp?.status === "answered" ? String(resp.value ?? "") : "";
      return `<div class="item"><p>${esc(i.text)}</p>${
        val ? `<p class="open-ended-replay">${esc(val)}</p>` : `<p class="did">${esc(resp?.status ?? "unanswered")}</p>`
      }</div>`;
    });
    return `<h1>Two last questions</h1>${parts.join("")}`;
  }
  const items = inst.items as P[];
  const likerts = items.filter((i) => !i.options?.length);
  const choices = items.filter((i) => i.options?.length);
  let h = `<h1>A few questions</h1>`;
  h += likerts.map((i) => likertRow(i, inst, inst.responses?.[i.item_id], i.text)).join("");
  if (choices.length) {
    h += `<hr class="page-split"><p class="page-note">— next page —</p>`;
    h += choices.map((i) => likertRow(i, inst, inst.responses?.[i.item_id], i.text)).join("");
  }
  return h;
}

function debriefPage(pages: P): string {
  const mat = pages.materials?.debrief;
  if (mat?.body?.text) return `<h1>Debrief</h1><p>Thank you for taking part. Please read this before finishing.</p>${materialText(mat)}<button class="primary">Continue</button>`;
  // Literal copy from DebriefScreen (tp family; the bound debrief material carries no text).
  const ai = pages.ai_assistant !== false;
  // TIME_PRESSURE_ENABLED=0-era sessions never saw the check-in — the replay
  // debrief must not describe a ratings disclosure that never happened.
  const tp = pages.time_pressure !== false;
  const bonusSentence =
    pages.bonus_payment !== false
      ? " The accuracy bonus described during the study is paid to every completer."
      : "";
  const purpose = tp
    ? `This study examined whether learning about teammates' workloads changes how much people ask them for information${ai ? ", and whether they turn to an AI assistant instead" : ""}. The teammates you worked with were automated systems operated by the research team. The workload ratings shown for your teammates at the check-in were assigned by the research team, not reported by anyone. Your own rating was recorded exactly as you entered it.${bonusSentence}`
    : `This study examined how team members share information and make decisions together on a short investigative task${ai ? ", including whether they turn to an AI assistant for help" : ""}. The teammates you worked with were automated systems operated by the research team.${bonusSentence}`;
  return `<h1>Debrief</h1><p>Thank you for taking part. Please read this before finishing.</p>
  <p>${purpose}</p>
  <p>If you have questions about this research, please contact [Principal Investigator] (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also contact the university's human research protection program by emailing irb@university.edu. Please refer to protocol number XXXX-XXXX.</p>
  <button class="primary">Continue</button>`;
}

function dataUsePage(pages: P): string {
  return `<h1>Questions or concerns?</h1>
  <p>Your responses will be used for research as described in the consent form. If you would like your responses removed, or if you have any other questions about this research, please contact [Principal Investigator] (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also contact the university's human research protection program by emailing irb@university.edu. Please refer to protocol number XXXX-XXXX.</p>
  <p>Whatever you decide does not change your payment.</p>
  <button class="primary">Finish</button>
  ${pages.data_use?.choice ? `<p class="did">Recorded data-use choice: <b>${esc(pages.data_use.choice)}</b></p>` : ""}`;
}

function completionPage(pages: P): string {
  const c = pages.completion ?? {};
  let h = `<h1>You are finished</h1><p>Thank you — your session is complete.</p>`;
  if (c.code) h += `<p>Your completion code is: <b class="code">${esc(c.code)}</b></p>`;
  if (c.platform === "connect") {
    h += c.url
      ? `<p><a href="${esc(c.url)}">Return to Connect</a></p>`
      : `<p>Return to Connect and enter this code to submit your participation.</p>`;
  } else if (c.platform === "prolific") {
    h += `<p>Return to Prolific and enter this code to submit your participation.</p>`;
  }
  return h;
}

function terminalPage(pages: P, session: P): string {
  const stage = String(pages.terminal?.stage ?? session.stage ?? "");
  const reason = pages.terminal?.reason as string | undefined;
  const text: Record<string, string> = {
    declined:
      "You have declined to take part. The payment choices the study listing described are unchanged. You can close this window.",
    withdrawn:
      "You have withdrawn from the study. What was recorded before you left is preserved, and the payment the study listing described is unaffected. You can close this window.",
    technical_stop:
      "This session stopped because of a technical problem. Your answers and study record up to that point were saved. You can close this window.",
    abandoned:
      "This session has ended. If you return using your original study link, you will resume where your session stands. You can close this window.",
  };
  const reasonText: Record<string, string> = {
    checkin_incomplete:
      "This session ended because the team check-in was not completed within the response window. Your answers and study record up to that point were saved. You can close this window.",
  };
  const copy = (reason && reasonText[reason]) ?? text[stage] ?? "This session has ended.";
  return `<h1>Session ended</h1><p>${esc(copy)}</p>`;
}

const STAGE_LABEL: Record<string, string> = {
  entry: "Entry",
  consent: "Consent",
  instructions: "Orientation",
  training: "Practice",
  ai_qa: "AI Q&A (Optional)",
  scripted_connecting: "Connecting",
  ready: "Get ready",
  solo_review: "Solo review",
  task: "Team task",
  checkin: "Team check-in",
  answers_locked: "Answers locked",
  confidence: "Confidence ratings",
  peer_ratings: "Your teammates",
  post_questionnaire: "A few questions",
  open_ended: "Two last questions",
  debrief: "Debrief",
  data_use_choice: "Questions or concerns?",
  completion: "Completion",
  declined: "Declined",
  withdrawn: "Withdrawn",
  technical_stop: "Technical stop",
  abandoned: "Abandoned",
};

/** Render the page content for one stage. */
function pageBody(stage: string, pages: P, events: Ev[], session: P): string[] {
  switch (stage) {
    case "entry":
      return [`<h1>Team Decision-Making Study</h1><p>Preparing your session…</p>`];
    case "consent":
      return [consentPage(pages)];
    case "instructions":
      return instructionsPages(pages);
    case "training":
      return [trainingPage(pages, events)];
    case "ai_qa":
      return [qaPage(pages)];
    case "scripted_connecting":
      return [connectingPage(pages)];
    case "ready":
      return [`<h1>Get ready</h1><p>Your workspace is being prepared…</p>`];
    case "solo_review":
      return [
        workbenchPage(
          pages,
          events,
          pages.ai_assistant !== false
            ? "Workbench shown in its final state — during solo review the participant reads their case file alone before chat and the assistant open."
            : "Workbench shown in its final state — during solo review the participant reads their case file alone before the team chat opens.",
        ),
      ];
    case "task":
      return [
        workbenchPage(
          pages,
          events,
          pages.ai_assistant !== false
            ? "Workbench shown in its final (task-end) state — case file, team chat, assistant and the answer bar exactly as the participant left them. For the moment-by-moment replay use the researcher HTML simulation."
            : "Workbench shown in its final (task-end) state — case file, team chat and the answer bar exactly as the participant left them. For the moment-by-moment replay use the researcher HTML simulation.",
        ),
      ];
    case "checkin":
      return [
        checkinPage(pages) +
          `<p class="page-note">The check-in is a modal overlay on top of the task screen: it collects the participant's rating, waits, then reveals the teammates' ratings alongside it.</p>`,
      ];
    case "answers_locked":
      return [lockedPage(pages)];
    case "confidence":
    case "peer_ratings":
    case "post_questionnaire":
    case "open_ended":
      return [instrumentPage(pages, stage)];
    case "debrief":
      return [debriefPage(pages)];
    case "data_use_choice":
      return [dataUsePage(pages)];
    case "completion":
      return [completionPage(pages)];
    case "declined":
    case "withdrawn":
    case "technical_stop":
    case "abandoned":
      return [terminalPage(pages, session)];
    default:
      return [`<h1>${esc(STAGE_LABEL[stage] ?? stage)}</h1><p class="placeholder-note">No participant page rendering for this stage.</p>`];
  }
}

export function buildParticipantViewHtml(replay: Record<string, unknown>): string {
  const session = (replay.session ?? {}) as P;
  const participant = (replay.participant ?? {}) as P;
  const pages = (participant.pages ?? {}) as P;
  const events = (participant.events ?? []) as Ev[];

  const seen = new Set<string>();
  const order = ((pages.stage_order ?? []) as string[])
    // checkin_collect/wait/reveal are phases of one modal, not three pages.
    .map((s) => (s.startsWith("checkin_") ? "checkin" : s))
    .filter((s) => {
      if (seen.has(s)) return false;
      seen.add(s);
      return true;
    });
  // A session that never left entry has no transitions — still render the entry page.
  if (!order.length) order.push(String(session.stage ?? "entry"));

  const pageList: { label: string; at: string; body: string }[] = [];
  for (const stage of order) {
    const bodies = pageBody(stage, pages, events, session);
    const at = fmtClock(
      pages.entered_at?.[stage] ??
        (stage === "checkin" ? pages.entered_at?.checkin_collect : undefined),
    );
    for (let i = 0; i < bodies.length; i++) {
      const label = STAGE_LABEL[stage] ?? stage.replaceAll("_", " ");
      const sub = bodies.length > 1 ? ` (page ${i + 1} of ${bodies.length})` : "";
      pageList.push({ label: `${label}${sub}`, at, body: bodies[i] ?? "" });
    }
  }

  const payload = {
    meta: {
      session_id: session.session_id ?? "?",
      mode: session.mode ?? "?",
      manifest: session.manifest_id ?? "?",
      stage: session.stage ?? "?",
      outcome: pages.completion?.outcome ?? null,
      generated_at: new Date().toISOString(),
    },
    pages: pageList,
  };
  const json = JSON.stringify(payload).replace(/</g, "\\u003c").replace(/\u2028|\u2029/g, " ");
  return DOC.replace("/*__PV_PAYLOAD__*/", `const PV=${json};`);
}

const DOC = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Participant view</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #e8e8e8; color: #1c1c1c; display: flex; flex-direction: column; height: 100vh; }
.banner { background: #274a71; color: #fff; padding: 6px 16px; font-size: 12.5px; display: flex; gap: 14px; align-items: center; flex: none; }
.banner b { letter-spacing: .04em; }
.banner .right { margin-left: auto; }
.wrap { flex: 1; overflow-y: auto; padding: 22px 16px; }
.deck { max-width: 1060px; margin: 0 auto; }
.page { background: #fff; border: 1px solid #ccc; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); min-height: 60vh; }
.pager { flex: none; background: #181818; color: #eee; display: flex; align-items: center; gap: 12px; padding: 10px 16px; font-size: 13px; }
.pager button { font-size: 13.5px; padding: 6px 18px; border-radius: 6px; border: 1px solid #555; background: #2a2a2a; color: #eee; cursor: pointer; }
.pager button:hover:not(:disabled) { background: #3a3a3a; }
.pager button:disabled { opacity: .4; cursor: default; }
.pager .next { background: #274a71; border-color: #274a71; }
.pager .next:hover:not(:disabled) { background: #31598a; }
.pager .pos { font-variant-numeric: tabular-nums; color: #bbb; }
.pager .jumps { display: flex; gap: 5px; margin-left: auto; flex-wrap: wrap; }
.pager .jumps button { width: 26px; height: 26px; padding: 0; border-radius: 50%; font-size: 11px; }
.pager .jumps button.here { background: #274a71; border-color: #274a71; color: #fff; }
.page-head { background: #f0f4f9; border-bottom: 1px solid #dde5ee; padding: 6px 14px; display: flex; gap: 12px; align-items: baseline; font-size: 12px; color: #555; }
.page-head .pnum { font-weight: 700; color: #274a71; }
.page-head .pstage { font-weight: 600; }
.page-head .pat { margin-left: auto; font-variant-numeric: tabular-nums; }
.page-body { padding: 20px 28px 26px; }
.page-body h1 { font-size: 21px; margin: 0 0 10px; }
.page-body h2 { font-size: 16px; margin: 14px 0 6px; }
.page-body p { font-size: 14.5px; line-height: 1.5; }
.page-body ul { font-size: 14.5px; line-height: 1.5; }
button.primary { display: inline-block; font-size: 14px; padding: 8px 18px; border-radius: 6px; border: 0; background: #274a71; color: #fff; margin-top: 10px; }
button.secondary { display: inline-block; font-size: 14px; padding: 8px 18px; border-radius: 6px; border: 1px solid #bbb; background: #fff; color: #333; margin-top: 10px; }
button.chosen { outline: 3px solid #6a3fb5; outline-offset: 2px; }
.btn-row { display: flex; gap: 10px; }
.did { font-size: 12.5px; color: #1a6b2f; background: #effaf1; border: 1px solid #9ccfa8; border-radius: 6px; padding: 6px 10px; margin-top: 12px; }
.qa-box textarea { width: 100%; min-height: 64px; font-size: 14px; padding: 8px; border: 1px solid #bbb; border-radius: 6px; margin: 6px 0; }
.qa-box button { font-size: 13px; padding: 6px 14px; border-radius: 6px; border: 1px solid #888; background: #eee; }
.qa-log { margin: 12px 0; padding-left: 20px; }
.qa-log li { margin-bottom: 10px; }
.qa-q { font-weight: 600; font-size: 14px; }
.qa-a { font-size: 14px; color: #333; background: #f3f6fa; border: 1px solid #dde5ee; border-radius: 6px; padding: 8px 10px; margin-top: 4px; }
.page-note { font-size: 12px; color: #777; font-style: italic; margin-top: 14px; }
.placeholder-note { font-size: 13px; color: #8a6d00; background: #fff4d6; border: 1px solid #e0c97f; border-radius: 6px; padding: 8px 12px; }
.consent-sub { color: #555; }
.consent-question { font-weight: 600; }
.initials-block { margin: 14px 0; }
.initials-block label { display: block; font-size: 13px; color: #555; margin-bottom: 4px; }
.initials-block input { font-size: 16px; letter-spacing: 2px; padding: 6px 8px; width: 90px; border: 1px solid #bbb; border-radius: 5px; }
.workspace-diagram { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 70px 70px 34px; gap: 6px; max-width: 420px; margin: 10px 0; }
.workspace-diagram div { border: 1px dashed #999; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 12.5px; color: #555; background: #fafafa; }
.wd-case { grid-column: 1; grid-row: 1 / 3; }
.wd-chat { grid-column: 2; grid-row: 1; }
.wd-ai { grid-column: 2; grid-row: 2; }
.wd-answers { grid-column: 1 / 3; grid-row: 3; }
.workspace-diagram.no-assistant .wd-chat { grid-row: 1 / 3; }
.joining-list { list-style: none; padding: 0; }
.joining-list li { display: flex; gap: 10px; align-items: center; padding: 6px 0; font-size: 15px; }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #ccc; display: inline-block; }
.dot.online { background: #2e9e4f; }
.joining-list .status { margin-left: auto; font-size: 12.5px; color: #777; }
.task-header { padding: 10px 14px; background: #222; color: #fff; display: grid; grid-template-columns: minmax(0,1fr) auto minmax(0,1fr); align-items: center; gap: 8px 14px; font-size: 15px; }
.task-heading { display: flex; flex-wrap: wrap; gap: 6px 14px; min-width: 0; }
.task-header .goal { color: #ccc; }
.qa-button { min-width: 120px; min-height: 42px; }
.mine-strip { background: rgba(255,255,255,.12); padding: 2px 9px; border-radius: 3px; justify-self: end; max-width: 100%; overflow-wrap: anywhere; text-align: right; }
.status-strip { text-align: center; }
.timer { font-variant-numeric: tabular-nums; font-size: 19px; font-weight: 700; padding: 2px 10px; border-radius: 4px; background: #444; }
.task-grid { display: grid; grid-template-columns: 45% 1fr; grid-template-rows: minmax(160px, 240px) minmax(120px, 200px); gap: 10px; padding: 10px; background: #e8e8e8; }
.pane { background: #fff; border: 1px solid #ddd; border-radius: 8px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.pane-head { padding: 8px 12px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: baseline; gap: 8px; }
.pane-head h2 { margin: 0; font-size: 15px; }
.pane-head .count { margin-left: auto; font-size: 12px; color: #777; }
.pane[data-pane="case-file"] { grid-column: 1; grid-row: 1 / 3; }
.pane[data-pane="team-chat"] { grid-column: 2; grid-row: 1; border-color: #c8d6ec; }
.pane[data-pane="assistant"] { grid-column: 2; grid-row: 2; border-color: #d9cfec; }
.pane[data-pane="team-chat"] .pane-head { background: #e6eef9; color: #274a71; }
.pane[data-pane="assistant"] .pane-head { background: #efe9f9; color: #5b3a86; }
.task-grid.no-assistant .pane[data-pane="team-chat"] { grid-row: 1 / 3; }
.files-list, .msgs { list-style: none; margin: 0; padding: 8px; overflow-y: auto; flex: 1; display: flex; flex-direction: column; gap: 8px; }
.file { border: 1px solid #ddd; border-radius: 8px; padding: 6px 10px; font-size: 13.5px; background: #fff; }
.file.from-team { border-left: 4px solid #6a3fb5; background: #fbf9fe; }
.file-head { display: flex; gap: 8px; align-items: baseline; }
.file-stamp { font-size: 11px; color: #777; font-weight: 700; }
.file-crate { font-size: 11px; font-weight: 700; color: #333; }
.file-source { font-size: 11px; color: #6a3fb5; margin-left: auto; }
.file-body { white-space: pre-wrap; margin-top: 2px; }
.msg { border: 1px solid #e0e0e0; border-radius: 8px; padding: 6px 10px; font-size: 13.5px; line-height: 1.45; max-width: 92%; }
.msg.mine { background: #f3f6f5; align-self: flex-end; }
.msg.sys { background: #fbf7e8; border-color: #e5d9a8; font-style: italic; font-size: 12.5px; }
.msg .who { display: block; font-size: 11.5px; font-weight: 700; color: #555; margin-bottom: 2px; }
.msg-body { white-space: pre-wrap; }
.filepill { display: block; margin-top: 6px; border: 1px solid #ccc; border-radius: 6px; padding: 6px 8px; font-size: 12.5px; background: #fafafa; }
.filepill .fs { display: block; font-size: 11px; color: #777; }
.submit-bar { border-top: 2px solid #333; background: #f4f4f4; padding: 10px 14px; }
.submit-title { margin: 0 0 2px; font-size: 14px; }
.submit-bar .prompt { margin: 0 0 8px; font-size: 12.5px; color: #555; }
.submit-row { display: flex; gap: 12px; flex-wrap: wrap; }
.crate-picker { display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
.crate-picker select { font-size: 13px; padding: 4px 6px; border: 1px solid #bbb; border-radius: 5px; background: #fff; }
.wb { border: 1px solid #bbb; border-radius: 8px; overflow: hidden; }
.overlay-sub { margin: 0 0 8px; color: #666; font-size: 13px; }
.likert-row { display: flex; gap: 6px; margin: 8px 0 4px; }
.likert-row .rec { width: 38px; height: 38px; border: 1px solid #bbb; border-radius: 6px; background: #fff; font-size: 15px; display: inline-flex; align-items: center; justify-content: center; }
.likert-row .rec.chosen { background: #274a71; color: #fff; border-color: #274a71; }
.scale-ends { display: flex; justify-content: space-between; font-size: 12px; color: #777; margin-bottom: 8px; max-width: 460px; }
.overlay-note { font-size: 12.5px; color: #777; }
.reveal-row { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.reveal-chip { border: 1px solid #ccc; border-radius: 14px; padding: 4px 12px; font-size: 13px; }
.item { margin: 0 0 18px; }
.item > p { font-weight: 500; margin: 0 0 4px; }
.choice-list { list-style: none; padding: 0; margin: 6px 0; }
.choice-list li { border: 1px solid #ccc; border-radius: 6px; padding: 6px 12px; margin: 4px 0; font-size: 14px; max-width: 460px; }
.choice-list li.chosen-opt { background: #eef3fa; border-color: #274a71; font-weight: 600; }
.fake-slider { height: 6px; background: #ddd; border-radius: 3px; max-width: 460px; position: relative; margin: 4px 0; }
.fake-fill { display: block; height: 100%; background: #274a71; border-radius: 3px; }
.slider-readout { font-size: 13px; font-weight: 600; color: #274a71; }
.confidence-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
.confidence-card { border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; }
.confidence-card .card-title { font-weight: 700; margin: 0 0 2px; }
.confidence-card .card-answer { margin: 0 0 6px; font-size: 13px; }
.confidence-card .scale-ends, .confidence-card .fake-slider { max-width: none; }
.page-split { border: 0; border-top: 1px dashed #bbb; margin: 18px 0 4px; }
.code { font-size: 18px; letter-spacing: 1px; }
</style>
</head>
<body>
<div class="banner"><b>PARTICIPANT VIEW</b><span id="meta"></span><span class="right">click through pages as the participant did · researcher copy — not participant-facing</span></div>
<div class="wrap" id="wrap"><div class="deck"><section class="page" id="pageHost"></section></div></div>
<div class="pager">
  <button id="backBtn">← Back</button>
  <span class="pos" id="posLabel"></span>
  <button id="nextBtn" class="next">Next →</button>
  <span class="jumps" id="jumps"></span>
</div>
<script>
/*__PV_PAYLOAD__*/
(function () {
  "use strict";
  var i = 0, pages = PV.pages || [];
  function el(id) { return document.getElementById(id); }
  el("meta").textContent =
    "session " + String(PV.meta.session_id).slice(0, 8) + " · " + PV.meta.mode +
    " · manifest " + PV.meta.manifest + " · stage " + PV.meta.stage +
    (PV.meta.outcome ? " · " + PV.meta.outcome : "") + " · generated " + PV.meta.generated_at;
  var jumps = el("jumps");
  pages.forEach(function (p, n) {
    var b = document.createElement("button");
    b.textContent = String(n + 1);
    b.title = p.label;
    b.onclick = function () { show(n); };
    jumps.appendChild(b);
  });
  function show(n) {
    i = Math.min(Math.max(n, 0), pages.length - 1);
    var p = pages[i] || { label: "", at: "", body: "" };
    el("pageHost").innerHTML =
      '<div class="page-head"><span class="pnum">Page ' + (i + 1) + " of " + pages.length + "</span>" +
      '<span class="pstage">' + p.label + "</span>" +
      (p.at ? '<span class="pat">entered ' + p.at + "</span>" : "") +
      '</div><div class="page-body">' + p.body + "</div>";
    el("posLabel").textContent = p.label;
    el("backBtn").disabled = i === 0;
    el("nextBtn").disabled = i === pages.length - 1;
    var dots = jumps.children;
    for (var k = 0; k < dots.length; k++) dots[k].className = k === i ? "here" : "";
    el("wrap").scrollTop = 0;
  }
  el("backBtn").onclick = function () { show(i - 1); };
  el("nextBtn").onclick = function () { show(i + 1); };
  document.addEventListener("keydown", function (e) {
    if (e.code === "ArrowRight") show(i + 1);
    else if (e.code === "ArrowLeft") show(i - 1);
  });
  show(0);
})();
</script>
</body>
</html>
`;

/** Build the participant-view HTML and trigger a browser download. */
export function downloadParticipantView(replay: Record<string, unknown>): void {
  const html = buildParticipantViewHtml(replay);
  const sid = String(
    ((replay.session ?? {}) as Record<string, unknown>).session_id ?? "session",
  ).slice(0, 8);
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `session-${sid}-participant-view.html`;
  a.click();
  URL.revokeObjectURL(url);
}
