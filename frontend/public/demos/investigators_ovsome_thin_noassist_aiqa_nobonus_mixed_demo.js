"use strict";
(function () {

/* ===== world ================================================================ */
var HIDEOUTS = ["The Sawmill", "The Marina", "Blackwell Storage", "Old Cannery",
                "Fairground Sheds", "Ridgeline Bunker"];
/* 2026-10-15 crate-condition experiment: contents grow to 12 (mirroring the
   generator's CRATE_CONTENTS) — the no-overlap level runs a 12-crate world. */
var CONTENTS = {1:"laptops",2:"smart watches",3:"gaming consoles",4:"TVs",
                5:"headphones",6:"cameras",7:"tablets",8:"power tools",
                9:"sneakers",10:"drones",11:"e-bikes",12:"VR headsets"};
var ASSIGNED = [1, 3, 5, 7];

/* Records. role "own" starts in the participant file; "team" is held by a seat and
   delivered on a matching ask; latest record per crate names the true location. */
var RECORDS = [
  {id:"r1a", crate:1, stamp:"3 days ago, 10:15 AM", body:"Field team: Crate 1 (laptops) arriving at The Sawmill.", role:"own"},
  {id:"r1b", crate:1, stamp:"Yesterday, 2:40 PM", body:"Traffic camera: a van leaves The Sawmill headed east, Crate 1 believed aboard.", role:"own"},
  {id:"r1c", crate:1, stamp:"Today, 8:55 AM", body:"Field team: Crate 1 (laptops) at Old Cannery, confirmed by walk-by.", role:"own"},
  {id:"r3a", crate:3, stamp:"2 days ago, 9:30 AM", body:"Warehouse camera confirms Crate 3 offloaded at Blackwell Storage.", role:"own"},
  {id:"r3b", crate:3, stamp:"Yesterday, 6:10 PM", body:"GPS ping: vehicle linked to Crate 3 between Blackwell Storage and The Marina.", role:"own"},
  {id:"r3c", crate:3, stamp:"Today, 9:20 AM", body:"Informant: gaming consoles still on site at Fairground Sheds.", role:"own"},
  {id:"r2a", crate:2, stamp:"Yesterday, 11:05 AM", body:"Surveillance photo: Crate 2 at the loading dock of Ridgeline Bunker.", role:"own"},
  {id:"r4a", crate:4, stamp:"2 days ago, 4:50 PM", body:"Field team: Crate 4 (TVs) arriving at The Marina.", role:"own"},
  {id:"r8a", crate:8, stamp:"Today, 7:45 AM", body:"Anonymous tip: power tools have not moved from The Sawmill.", role:"own"},
  {id:"r5a", crate:5, stamp:"Yesterday, 3:15 PM", body:"Field team: Crate 5 (headphones) unloaded at Ridgeline Bunker.", role:"team", seat:"PM"},
  {id:"r5b", crate:5, stamp:"Today, 9:40 AM", body:"Warehouse camera: headphones still at Ridgeline Bunker.", role:"team", seat:"PM"},
  {id:"r7a", crate:7, stamp:"2 days ago, 1:25 PM", body:"Traffic camera: vehicle carrying the tablets leaves Old Cannery heading toward the Fairground Sheds area.", role:"team", seat:"JT"},
  {id:"r7b", crate:7, stamp:"Today, 8:30 AM", body:"Field team: Crate 7 (tablets) at Fairground Sheds, confirmed by walk-by.", role:"team", seat:"JT"},
  {id:"r6a", crate:6, stamp:"Today, 9:05 AM", body:"Drone footage: Crate 6 (cameras) is loaded onto a vehicle at The Marina.", role:"team", seat:"PM"},
  {id:"r4b", crate:4, stamp:"Today, 9:50 AM", body:"Field team: activity at The Marina, Crate 4 still on site.", role:"team", seat:"JT"}
];

/* Scripted teammate requests (requests.demo@v4 templates, drawn without
   replacement; times from the TP manifest: 45/130/210 active s). Seats follow
   the profile's coin_majority_shuffle (the majority seat is a fair coin);
   targets mirror the manifest's [unassigned, assigned, unassigned] pattern —
   the participant's assigned crates are 1, 3, 5, 7. */
var MAJORITY_SEAT = Math.random() < 0.5 ? "JT" : "PM";
var MINORITY_SEAT = MAJORITY_SEAT === "JT" ? "PM" : "JT";
var REQ_SEATS = [MAJORITY_SEAT, MINORITY_SEAT, MAJORITY_SEAT];
var SCRIPTED = [
  {at:45,  seat:REQ_SEATS[0], text:"Requesting intel on Crate 2."},
  {at:130, seat:REQ_SEATS[1], text:"What does your file have on Crate 5?"},
  {at:210, seat:REQ_SEATS[2], text:"Could use whatever you have on Crate 8."}
];

/* Level-keyed register (owner direction 2026-10-18): a teammate who just
   reported 6–7/7 must not sound relaxed — words carry the manipulation, so
   busy seats get terse/strained lines and light seats keep the relaxed pool.
   Reply TIMING stays identical across arms (latency would confound asks). */
var SOCIAL = {
  high: [
    "hang on, swamped",
    "working on mine, one sec",
    "a lot to track",
    "behind on mine"
  ],
  low: [
    "doing ok, working my list, you?",
    "on it, checking timestamps",
    "quiet so far over here",
    "checking mine now"
  ]
};
var THANKS = ["thanks — that helps", "got it, thanks", "that's the one I needed, thanks"];
var GREETINGS = ["Hello!", "Hi!", "Hey!"];

/* Per-variant configuration — scripts/build_demo_mocks.py stamps the
   published variants by rewriting the @DEMO_CFG@ line below; edit the object
   to preview a combination by hand. Axes: solo = solo-review phase present;
   interaction "nl" = typed-chat-only (hides structured ask controls);
   scripted = teammates answer from the deterministic pool; arm = check-in
   reveal values and scheduled-remark wording; ai = assistant surfaces on;
   overlap = "none"|"some"|"full" reshapes the crate world per the
   crate-condition experiment (unset = legacy 8-crate independent deal);
   thin = reduced own-file record count. */
var DEMO_CFG = {"solo": false, "interaction": "nl", "scripted": false, "overlap": "some", "thin": true, "label": "some overlap · 8 crates · thin case file · AI teammates · typed chat · no AI assistant · AI Q&A · no bonus · variant mixed", "ai": false, "aiqa": true, "nobonus": true, "pressure": "mixed", "code": "PLACEHOLDER-CODE"}; /* @DEMO_CFG@ */
var SOLO_PHASE = !!DEMO_CFG.solo;
var NL_ONLY = DEMO_CFG.interaction === "nl";
var SCRIPTED_TEAM = !!DEMO_CFG.scripted;
/* AI_ASSISTANT_ENABLED=0 mirror: ai=false removes the assistant pane, its
   practice page, its comprehension item, its questionnaire item and the
   open-ended "or the AI assistant" clause — same surface list as the real
   build's feature flag. */
var AI_ASSISTANT = DEMO_CFG.ai !== false;

/* 2026-10-15 crate-condition experiment: ``overlap``/``thin`` axes reshape the
   mock world to mirror the bound task shape — crate count, the participant's
   assignment, the intel economy, the scripted-request targets and the
   condition copy. ``independent`` (the default, unset axis) keeps the legacy
   8-crate world above byte-for-byte. */
var OVERLAP = DEMO_CFG.overlap || "independent";
var THIN = !!DEMO_CFG.thin;
/* TIME_PRESSURE_ENABLED=0 mirror: nopress=true removes the manipulation —
   no check-in pop-up, no teammate pressure remarks, no "One minute left"
   warning, no low-time styling, no rushed-feeling questionnaire items, and a
   debrief that does not claim a check-in disclosure. The countdown stays a
   neutral timer and the 300 s deadline still applies. */
var NOPRESS = !!DEMO_CFG.nopress;
/* AI_QA_ENABLED=1 mirror: aiqa=true inserts the optional "AI Q&A (Optional)"
   page between the comprehension check and connecting — a rules-questions
   textbox with a canned local answer, always-active Continue. */
var AIQA = !!DEMO_CFG.aiqa;
/* BONUS_PAYMENT_ENABLED=0 mirror: nobonus=true removes every accuracy-bonus
   mention — consent compensation sentence, case-background clause, gameplay
   bonus line, and the debrief's paid-to-every-completer disclosure. */
var NOBONUS = !!DEMO_CFG.nobonus;
var N_CRATES = OVERLAP === "none" ? 12 : OVERLAP === "full" ? 4 : 8;
var CRATE_WORDS = {4: "four", 8: "eight", 12: "twelve"};
function crateWord(n, cap) {
  var w = CRATE_WORDS[n] || String(n);
  return cap ? w.charAt(0).toUpperCase() + w.slice(1) : w;
}

/* Condition worlds: intel economies matching the owner's spec — the file holds
   the latest intel for 2 of the 4 assigned crates (teammates hold the latest
   for the other 2) and, outside full overlap, at least one record for four
   unassigned crates. */
function rec(id, crate, stamp, body, role, seat) {
  return {id: id, crate: crate, stamp: stamp, body: body, role: role, seat: seat};
}
function shapeWorld(overlap) {
  if (overlap === "none") {
    /* 12 crates, pairwise-disjoint assignments: JT 1-4, participant 5-8, PM 9-12. */
    return {
      assigned: [5, 6, 7, 8],
      scripted: [
        {at:45,  seat:REQ_SEATS[0], text:"Requesting intel on Crate 2."},
        {at:130, seat:REQ_SEATS[1], text:"What does your file have on Crate 6?"},
        {at:210, seat:REQ_SEATS[2], text:"Could use whatever you have on Crate 9."}
      ],
      records: [
        rec("r5a",5,"3 days ago, 10:15 AM","Field team: Crate 5 (headphones) arriving at The Sawmill.","own"),
        rec("r5b",5,"Yesterday, 2:40 PM","Traffic camera: a van leaves The Sawmill headed east, Crate 5 believed aboard.","own"),
        rec("r5c",5,"Today, 8:55 AM","Field team: Crate 5 (headphones) at Old Cannery, confirmed by walk-by.","own"),
        rec("r6a",6,"2 days ago, 9:30 AM","Warehouse camera confirms Crate 6 offloaded at Blackwell Storage.","own"),
        rec("r6b",6,"Today, 9:20 AM","GPS ping: Crate 6 (cameras) confirmed still at Blackwell Storage.","own"),
        rec("r7a",7,"3 days ago, 1:25 PM","Surveillance photo: Crate 7 at the loading dock of Ridgeline Bunker.","own"),
        rec("r8a",8,"2 days ago, 4:50 PM","Informant: power tools last seen at The Marina.","own"),
        rec("r1a",1,"Yesterday, 11:05 AM","Field team: Crate 1 (laptops) arriving at Fairground Sheds.","own"),
        rec("r2a",2,"Today, 7:45 AM","Anonymous tip: smart watches spotted near Old Cannery.","own"),
        rec("r9a",9,"Yesterday, 3:15 PM","Warehouse camera: Crate 9 (sneakers) unloaded at The Sawmill.","own"),
        rec("r10a",10,"2 days ago, 6:10 PM","Field team: Crate 10 (drones) moved to Ridgeline Bunker.","own"),
        rec("r7b",7,"Today, 9:40 AM","Field team: Crate 7 (tablets) at Fairground Sheds, confirmed by walk-by.","team","JT"),
        rec("r8b",8,"Today, 9:50 AM","Field team: Crate 8 (power tools) at Old Cannery, confirmed by walk-by.","team","PM")
      ]
    };
  }
  if (overlap === "full") {
    /* 4 crates, every seat works the same assignment — no unassigned intel. */
    return {
      assigned: [1, 2, 3, 4],
      scripted: [
        {at:45,  seat:REQ_SEATS[0], text:"Requesting intel on Crate 1."},
        {at:130, seat:REQ_SEATS[1], text:"What does your file have on Crate 3?"},
        {at:210, seat:REQ_SEATS[2], text:"Could use whatever you have on Crate 2."}
      ],
      records: [
        rec("r1a",1,"3 days ago, 10:15 AM","Field team: Crate 1 (laptops) arriving at The Sawmill.","own"),
        rec("r1b",1,"Yesterday, 2:40 PM","Traffic camera: a van leaves The Sawmill headed east, Crate 1 believed aboard.","own"),
        rec("r1c",1,"Today, 8:55 AM","Field team: Crate 1 (laptops) at Old Cannery, confirmed by walk-by.","own"),
        rec("r2a",2,"2 days ago, 9:30 AM","Warehouse camera confirms Crate 2 offloaded at Blackwell Storage.","own"),
        rec("r2b",2,"Today, 9:20 AM","GPS ping: Crate 2 (smart watches) confirmed still at Blackwell Storage.","own"),
        rec("r3a",3,"3 days ago, 1:25 PM","Surveillance photo: Crate 3 at the loading dock of Ridgeline Bunker.","own"),
        rec("r4a",4,"2 days ago, 4:50 PM","Informant: TVs last seen at The Marina.","own"),
        rec("r3b",3,"Today, 9:40 AM","Field team: Crate 3 (gaming consoles) at Fairground Sheds, confirmed by walk-by.","team","JT"),
        rec("r4b",4,"Today, 9:50 AM","Field team: Crate 4 (TVs) at Old Cannery, confirmed by walk-by.","team","PM")
      ]
    };
  }
  /* "some" — 8 crates, partial pairwise overlap: JT 1-4, participant 3-6, PM 1/4/7/8. */
  return {
    assigned: [3, 4, 5, 6],
    scripted: [
      {at:45,  seat:REQ_SEATS[0], text:"Requesting intel on Crate 2."},
      {at:130, seat:REQ_SEATS[1], text:"What does your file have on Crate 5?"},
      {at:210, seat:REQ_SEATS[2], text:"Could use whatever you have on Crate 7."}
    ],
    records: [
      rec("r3a",3,"3 days ago, 10:15 AM","Field team: Crate 3 (gaming consoles) arriving at The Sawmill.","own"),
      rec("r3b",3,"Yesterday, 2:40 PM","Traffic camera: a van leaves The Sawmill headed east, Crate 3 believed aboard.","own"),
      rec("r3c",3,"Today, 8:55 AM","Field team: Crate 3 (gaming consoles) at Old Cannery, confirmed by walk-by.","own"),
      rec("r4a",4,"2 days ago, 9:30 AM","Warehouse camera confirms Crate 4 offloaded at Blackwell Storage.","own"),
      rec("r4b2",4,"Today, 9:20 AM","GPS ping: Crate 4 (TVs) confirmed still at Blackwell Storage.","own"),
      rec("r5a",5,"3 days ago, 1:25 PM","Surveillance photo: Crate 5 at the loading dock of Ridgeline Bunker.","own"),
      rec("r6a",6,"2 days ago, 4:50 PM","Informant: cameras last seen at The Marina.","own"),
      rec("r2a",2,"Yesterday, 11:05 AM","Field team: Crate 2 (smart watches) arriving at Fairground Sheds.","own"),
      rec("r7a",7,"Today, 7:45 AM","Anonymous tip: tablets spotted near Old Cannery.","own"),
      rec("r8a",8,"Yesterday, 3:15 PM","Warehouse camera: Crate 8 (power tools) unloaded at The Sawmill.","own"),
      rec("r1a",1,"2 days ago, 6:10 PM","Field team: Crate 1 (laptops) moved to Ridgeline Bunker.","own"),
      rec("r5b",5,"Today, 9:40 AM","Field team: Crate 5 (headphones) at Fairground Sheds, confirmed by walk-by.","team","JT"),
      rec("r6b",6,"Today, 9:50 AM","Field team: Crate 6 (cameras) at Old Cannery, confirmed by walk-by.","team","PM")
    ]
  };
}
if (OVERLAP !== "independent") {
  var SHAPED = shapeWorld(OVERLAP);
  ASSIGNED = SHAPED.assigned;
  RECORDS = SHAPED.records;
  SCRIPTED = SHAPED.scripted;
}
if (THIN) {
  /* Thin case file (the live condition administers exactly 20 records —
     audit G03): the mock file is illustrative, not the real 47/49 → 20
     density, but it must still *read* visibly thinner, so each assigned
     crate keeps only its latest own-file record while the unassigned-crate
     singles and teammate-held entries stay (matching the live trim's
     coverage semantics). */
  var _byCrate = {};
  RECORDS.forEach(function (r) {
    if (r.role === "own" && ASSIGNED.indexOf(r.crate) >= 0)
      (_byCrate[r.crate] = _byCrate[r.crate] || []).push(r.id);
  });
  var _drop = {};
  Object.keys(_byCrate).forEach(function (k) {
    var ids = _byCrate[k];
    if (ids.length > 1) ids.slice(0, ids.length - 1).forEach(function (id) { _drop[id] = 1; });
  });
  RECORDS = RECORDS.filter(function (r) { return !_drop[r.id]; });
}

var SOLO_UNTIL = 60;                 /* active seconds: solo review occupies 0-60 */
/* v15 mixed arm: a fair coin picks which seat reports the heavy workload
   (6) — the other reports the light one (2). High/low arms keep both seats at
   the arm's level. */
var MIXED_HIGH = Math.random() < 0.5 ? "JT" : "PM";
var CHECKIN_REVEAL = DEMO_CFG.pressure === "low" ? {JT: 3, PM: 2}
  : DEMO_CFG.pressure === "mixed" ? (MIXED_HIGH === "JT" ? {JT: 6, PM: 2} : {JT: 2, PM: 6})
  : {JT: 7, PM: 6};
function seatLevel(seat) { return CHECKIN_REVEAL[seat] >= 6 ? "high" : "low"; }
var CHECKIN_AT = 60, WARN_AT = 240, BUDGET = 300, GREET_WINDOW = 30;

/* Scheduled pressure remarks (remarks.tp_pressure@v1) — fixed bound copy
   delivered verbatim at 75 and 83 active s (15 and 23 s after the check-in
   opens). Which teammate speaks first is a fair coin per session. */
/* Under mixed the heavy seat always speaks first and the light seat replies
   (owner direction 2026-10-18 — the contrast is first-vs-second, not
   coin-flipped); under high/low the seat order stays a fair coin. */
var REMARK_FIRST = DEMO_CFG.pressure === "mixed" ? MIXED_HIGH
  : (Math.random() < 0.5 ? "JT" : "PM");
var REMARK_SECOND = REMARK_FIRST === "JT" ? "PM" : "JT";
var REMARK1 = {
  high: "yikes this is a lot for 5 min, not sure i'll finish",
  low: "feels ok, think i'll finish early"
};
var REMARK2 = {
  high: "me too, feels like a lot",
  low: "same, feels light"
};
/* Under mixed, the reply contrasts with the speaker's own level
   (mixed_lines): a heavy seat answers a light opener with "ugh lucky...",
   a light seat answers a heavy opener with "mine's actually pretty light". */
var REMARK2_MIXED = {
  high: "ugh lucky, mine feels like a lot",
  low: "actually i'm on track"
};
/* Under mixed the progress exchange inverts the check-in order: the light
   seat (ahead) announces progress first, the heavy seat answers. */
var REMARK3_FIRST = DEMO_CFG.pressure === "mixed"
  ? (MIXED_HIGH === "JT" ? "PM" : "JT") : REMARK_FIRST;
var REMARK3_SECOND = REMARK3_FIRST === "JT" ? "PM" : "JT";
var REMARK3 = {
  high: "ah not done with my 4",
  low: "ok almost done with my 4"
};
var REMARK4 = {
  high: "me either!",
  low: "nice, almost there myself"
};
var REMARK4_MIXED = {
  high: "i am not!",
  low: "nice!"
};
var REMARKS = NOPRESS
  ? []
  : [
      {at:75, seat:REMARK_FIRST,  text:REMARK1[seatLevel(REMARK_FIRST)]},
      {at:83, seat:REMARK_SECOND, text:(DEMO_CFG.pressure === "mixed" ? REMARK2_MIXED : REMARK2)[seatLevel(REMARK_SECOND)]},
      /* Warning reinforcement (owner+student 2026-10-18): arm-keyed progress
         exchange after the one-minute reminder. Under mixed the LIGHT seat
         leads and the heavy answers; high = both behind; low = both fine. */
      {at:246, seat:REMARK3_FIRST,  text:REMARK3[seatLevel(REMARK3_FIRST)]},
      {at:254, seat:REMARK3_SECOND, text:(DEMO_CFG.pressure === "mixed" ? REMARK4_MIXED : REMARK4)[seatLevel(REMARK3_SECOND)]}
    ];
/* Deterministic-pool texture for the scripted-teammate (non-NL) build — terse,
   mechanical, repeatable. The LLM builds use the warmer paraphrases above. */
var SOCIAL_SCRIPTED = [
  "checking my records now",
  "request received — reviewing my entries",
  "crate status check in progress",
  "working through my assigned crates"
];

/* ===== state ================================================================ */
var st = {
  stage: "consent", initials: "", page: 0,
  file: RECORDS.filter(function (r) { return r.role === "own"; }).slice(),
  sharedIds: {}, deliveredIds: {},
  msgs: [],           /* {who, mine, text, recordId, system} */
  aiMsgs: [],         /* assistant thread */
  answers: {},
  activeSec: 0, timerId: null, checkinOpen: false, checkinPhase: null,
  ownRating: null, revealed: false,
  greeted: false, taskClosed: false,
  fired: {},          /* scheduled-event flags */
  pmJoined: false,
  answersFrozen: false
};

var root = document.getElementById("root");
function esc(s) { return s.replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }

/* AI_QA_ENABLED=1 mirror: canned rules-scoped answers for the mock Q&A page.
   Keyword match picks a rules topic; anything else gets the same decline the
   backend's deterministic fallback returns. */
function demoQaAnswer(q) {
  var t = q.toLowerCase();
  if (/\b(time|long|minute|deadline|timer|clock)\b/.test(t)) {
    return "The task has a working-time limit — the timer at the top shows how much time is left. When it runs out, your answers lock and the study moves on to the questionnaire pages.";
  }
  if (/\b(crate|crates|assign)\b/.test(t)) {
    return "Your goal is to pick the correct hideout for each of your assigned crates. Each crate is hidden at exactly one hideout, and your case file plus your teammates' intel hold the clues.";
  }
  if (/\b(share|shared)\b/.test(t)) {
    return "You can share any record from your case file to the team chat so all three of you can see it.";
  }
  if (/\b(ask|question|intel|record)\b/.test(t)) {
    return "You can ask your teammates for intel about a crate in the team chat — each teammate holds different records, so asking is how the team pools what everyone knows.";
  }
  if (/\b(answer|submit|pick|choose|select)\b/.test(t)) {
    return "For each of your assigned crates, pick a hideout from its drop-down in the answers panel. You can change a pick any time before the deadline.";
  }
  /* v14 (2026-10-16): never name teammates pre-connect — same fix as the
     backend qa.py rules prompt. */
  if (/\b(name|names|initials|who)\b.*\b(team|teammate|jt|pm)\b|\b(jt|pm)\b/.test(t)) {
    return "I cannot name or invent your teammates. They will be introduced to you once you connect to the task.";
  }
  if (/\b(chat|teammate|team)\b/.test(t)) {
    return "Use the team chat to ask your teammates for intel or to read what they share — teammates are introduced once you connect.";
  }
  return "I'm not able to answer that one. I can only answer questions about how the Investigators task works — for example, how to ask your teammates for intel, share records, or submit your hideout answers. You can also check the instructions you just read, or press Continue to move on.";
}
function fmt(sec) { var m = Math.floor(sec / 60), s = sec % 60; return m + ":" + (s < 10 ? "0" : "") + s; }
function rand(a) { return a[Math.floor(Math.random() * a.length)]; }
function you() { return st.initials ? st.initials + " (you)" : "you"; }

function crateLabel(n) { return "Crate " + n + " — " + CONTENTS[n]; }

/* ===== chat helpers ========================================================= */
function postMsg(m) { st.msgs.push(m); renderChat(); }
function postAiMsg(m) { st.aiMsgs.push(m); renderAi(); }

function intelLi(rec, mine) {
  return '<li class="intel-card' + (mine ? " mine" : "") + '"><span class="filepill">' +
    '<span class="fs">' + esc(rec.stamp) + "</span>" + '<span class="file-crate">Crate ' + rec.crate + '</span> — ' + esc(rec.body) +
    (!mine ? '<span class="file-source"> — in your case file</span>' : "") +
    "</span></li>";
}

/* Deliver a teammate-held record: announcement + card, auto-joins the file. */
function deliverRecord(seat, rec) {
  st.deliveredIds[rec.id] = seat;
  postMsg({who: seat, text: "Sharing intel on Crate " + rec.crate});
  st.msgs.push({who: seat, text: "", recordId: rec.id});
  st.file.unshift(rec);  // UX v7: new intel lands at the top of the case file
  renderChat(); renderFile();
}

function fileLi(rec) {
  var shared = st.sharedIds[rec.id];
  var fromTeam = st.deliveredIds[rec.id];
  return '<li class="file' + (fromTeam ? " from-team" : "") + '" draggable="true" data-rec="' + rec.id + '">' +
    '<span class="drag-grip">✥</span><div class="file-main"><div class="file-head">' +
    '<span class="file-stamp">' + esc(rec.stamp) + "</span>" +
    '<span class="file-crate">Crate ' + rec.crate + "</span>" +
    (fromTeam ? '<span class="file-source">Shared by ' + fromTeam + "</span>" : "") +
    '</div><div class="file-body">' + esc(rec.body) + "</div></div>" +
    '<div class="file-actions">' +
    (shared ? '<span class="shared-tag">Shared in team chat</span>'
            : (fromTeam ? "" /* teammate intel came from chat — no re-share (UX v5 item 3) */
                        : '<button type="button" class="share-btn" data-rec="' + rec.id + '">Share</button>')) +
    "</div></li>";
}

function shareRecord(id) {
  var rec = st.file.filter(function (r) { return r.id === id; })[0];
  if (!rec || st.sharedIds[id] || st.taskClosed || st.deliveredIds[id]) return;
  st.sharedIds[id] = true;
  st.msgs.push({who: you(), mine: true, text: "Sharing intel on Crate " + rec.crate, recordId: rec.id});
  renderChat(); renderFile();
  /* A teammate acknowledges a responsive share (short delay, canned thanks). */
  setTimeout(function () {
    if (!st.taskClosed) postMsg({who: rand(["JT", "PM"]), text: rand(THANKS)});
  }, 2500 + Math.random() * 3000);
}

/* ===== task engine ========================================================== */
function applySoloLock() {
  ["chat-lock", "ai-lock", "solo-notice"].forEach(function (id) {
    var e = document.getElementById(id); if (e) e.hidden = !st.solo;
  });
  ["chat-in", "chat-send", "ask-sel", "ask-btn", "ai-in", "ai-btn", "ai-sel"].forEach(function (id) {
    var e = document.getElementById(id); if (e) e.disabled = st.solo;
  });
}
function tick() {
  if (st.stage !== "task" || st.checkinOpen || st.taskClosed) return;
  st.activeSec++;
  var t = st.activeSec;
  if (st.solo && t >= SOLO_UNTIL) {
    st.solo = false; applySoloLock();
    postMsg({who: "System", system: true, text: AI_ASSISTANT
      ? "Team chat and the AI assistant are now open."
      : "Team chat is now open."});
  }
  if (!NOPRESS && t === CHECKIN_AT && !st.fired.checkin) { st.fired.checkin = true; openCheckin(); }
  SCRIPTED.forEach(function (req, i) {
    if (t === req.at && !st.fired["req" + i]) {
      st.fired["req" + i] = true;
      postMsg({who: req.seat, text: req.text});
    }
  });
  REMARKS.forEach(function (rem, i) {
    if (t === rem.at && !st.fired["rem" + i]) {
      st.fired["rem" + i] = true;
      postMsg({who: rem.seat, text: rem.text});
    }
  });
  if (!NOPRESS && t === WARN_AT && !st.fired.warn) {
    st.fired.warn = true;
    postMsg({who: "System", system: true, text: "One minute left."});
  }
  /* v14 (2026-10-16): final-check notice at T-15s — shown to every
     participant; factual, not urgency; fires under nopress too (it is not a
     low-time cue). */
  if (!st.fired.finalCheck && BUDGET - t <= 15 && BUDGET - t > 0) {
    var noteEl = document.getElementById("final-check-note");
    if (noteEl) { noteEl.hidden = false; st.fired.finalCheck = true; }
  }
  if (t >= BUDGET) { closeTask(); return; }
  renderTimer();
}

function sendChat() {
  var inp = document.getElementById("chat-in");
  var text = (inp.value || "").trim();
  if (!text) return;
  inp.value = "";
  postMsg({who: you(), mine: true, text: text});
  setTimeout(function () {
    if (!st.taskClosed) {
      var _s = rand(["JT", "PM"]);
      postMsg({who: _s, text: rand(SCRIPTED_TEAM ? SOCIAL_SCRIPTED : SOCIAL[seatLevel(_s)])});
    }
  }, 1800 + Math.random() * 4000);
}

function greet(text) {
  st.greeted = true;
  postMsg({who: you(), mine: true, text: text});
  /* Verbatim greeting reply (UX round 4): same words back, other seat dedup. */
  setTimeout(function () { if (!st.taskClosed) postMsg({who: "JT", text: text}); }, 2400);
  renderChat();
}

function askTeam() {
  var sel = document.getElementById("ask-sel");
  var n = Number(sel.value);
  if (!n) return;
  sel.value = "";
  postMsg({who: you(), mine: true, text: "Requesting intel on Crate " + n + "."});
  setTimeout(function () {
    if (st.taskClosed) return;
    var undelivered = RECORDS.filter(function (r) {
      return r.role === "team" && r.crate === n && !st.deliveredIds[r.id];
    });
    if (!undelivered.length) {
      postMsg({who: rand(["JT", "PM"]), text: "nothing in my file on Crate " + n + ", sorry"});
      return;
    }
    /* Deliver the latest held record (decisive entry) for that crate. */
    deliverRecord(undelivered[undelivered.length - 1].seat, undelivered[undelivered.length - 1]);
  }, 5000 + Math.random() * 4500);
}

function askAssistant() {
  var sel = document.getElementById("ai-sel"), inp = document.getElementById("ai-in");
  var n = Number(sel.value);
  var text = (inp.value || "").trim();
  if (!n && text) {
    /* Audit F01: capture the whole integer — "/crate\s*(\d)/" turned
       "crate 10" into crate 1. Out-of-range mentions stay 0 → the
       "which crate?" reply rather than a wrong-crate answer. */
    var m = text.match(/crate\s*(\d+)/i);
    var parsed = m ? Number(m[1]) : 0;
    n = parsed >= 1 && parsed <= N_CRATES ? parsed : 0;
  }
  if (!n && !text) return;
  var q = text || ("What does my file say about Crate " + n + "?");
  sel.value = ""; inp.value = "";
  postAiMsg({who: you(), mine: true, text: q});
  setTimeout(function () {
    var ans;
    if (!n) {
      /* Typed-only builds have no crate picker; a message with no crate
         mention gets a canned "which crate?" reply so Ask never dead-ends. */
      ans = "I can review the evidence in your case file — tell me which crate "
          + "you are asking about, for example \"Crate 3\".";
    } else {
      var recs = st.file.filter(function (r) { return r.crate === n; });
      if (!recs.length) {
        ans = "Your case file currently has no entries on Crate " + n + ".";
      } else {
        var lines = recs.map(function (r) { return r.stamp + " — " + r.body; }).join(" ");
        var last = recs[recs.length - 1];
        ans = "Your file has " + recs.length + " entr" + (recs.length === 1 ? "y" : "ies") +
              " on Crate " + n + ". " + lines +
              " The most recent entry names " + hideoutOf(last.body) + " (" + last.stamp + ").";
      }
    }
    postAiMsg({who: "AI assistant", text: ans});
  }, 1500 + Math.random() * 1500);
}
function hideoutOf(body) {
  for (var i = 0; i < HIDEOUTS.length; i++) if (body.indexOf(HIDEOUTS[i]) >= 0) return HIDEOUTS[i];
  return "an unnamed site";
}

/* ===== check-in ============================================================= */
function openCheckin() {
  st.checkinOpen = true; st.checkinPhase = "collect";
  renderOverlay();
}
function submitCheckin(n) {
  st.ownRating = n; st.checkinPhase = "wait"; renderOverlay();
  setTimeout(function () {
    st.checkinPhase = "reveal"; renderOverlay();
  }, 3500);
}
function resumeCheckin() {
  st.checkinOpen = false; st.revealed = true;
  renderOverlay(); renderChat();
}
function closeTask() {
  st.taskClosed = true;
  if (st.timerId) clearInterval(st.timerId);
  st.answersFrozen = true;
  go("timeup");
}

/* ===== screens ============================================================== */
var SCR = {};
function go(stage) {
  st.stage = stage;
  var el = SCR[stage] || SCR.missing;
  root.innerHTML = el();
  wire(stage);
  window.scrollTo(0, 0);
}

function panel(title, inner) {
  return '<main class="panel"><h1>' + title + "</h1>" + inner + "</main>";
}

SCR.consent = function () {
  return '<main class="panel consent-panel"><h1>Team Decision-Making Study</h1>' +
    '<p class="consent-sub">[University] · Protocol XXXX-XXXX</p>' +
    '<div class="consent-copy"><section><h2>Key Information</h2>' +
    [
      "This is a research study conducted by researchers at [University]. This study examines how small teams work together and make decisions. Your participation is completely voluntary and you may withdraw at any time by closing your browser.",
      "In this study you will take part in an online team session with other team members. Your team will be given information about a business decision and asked to discuss it by text chat and make a recommendation. You will then answer questions about how your team worked together and about your experience with technology at work.",
      "Your typed messages during the team session will be visible to the other members of your team. Please do not share your real name, your employer, your location, or any other identifying or personal information during the session. You will be asked to enter initials to identify yourself.",
      "There are some features of this study's design that we are not describing to you now, because telling you in advance would change how people behave during the task and make the results invalid. We will explain these fully at the end of the session, before you finish.",
      "We estimate the study will take approximately 12 minutes to complete, and you will be paid $3.00" + (NOBONUS ? "" : " and will be eligible to receive a bonus of up to $0.50 based on your performance on the task") + ". To be compensated you need to reach the end of the study and click Submit. A payment code will appear on the final page of the study.",
      "We do not ask for your name or any other information that might identify you. The participant recruitment platform will provide us with demographic information including your age, level of education, employment status, and gender, which we may analyze as part of this research. Your platform identification number will be removed from our data, and de-identified information, including chat transcripts produced during the study, may be shared with other researchers, made public, or used for future research purposes.",
      "This study may use AI tools to generate content you see during the session. The AI tools used in this study run on a secure university-managed server.",
      "Taking part in this study involves no more than minimal risk. As in any group discussion, another team member could write something you find unhelpful. You may leave the session at any time by closing your browser, without giving a reason. If you do leave the study early, you will not be paid. There are no direct benefits to you for participating.",
      "If you have questions about this research, please contact [Principal Investigator] (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also contact the university's human research protection program by emailing irb@university.edu. Please refer to protocol number #XXXX-XXXX."
    ].map(function (p) { return "<p>" + p + "</p>"; }).join("") +
    "</section></div>" +
    '<p class="consent-question">Would you like to participate?</p>' +
    '<div class="btn-row"><button type="button" class="primary" id="consent-yes">Yes, I consent to participate</button>' +
    '<button type="button" class="secondary" id="consent-no">No, I do not wish to participate</button></div></main>';
};

SCR.declined = function () {
  return panel("Session ended",
    "<p>You have declined to take part. The payment choices the study listing described are unchanged. You can close this window.</p>");
};

function assignmentRule() {
  if (OVERLAP === "full") return "Each teammate will be <b>assigned to work on all four crates</b>.";
  if (OVERLAP === "none" || OVERLAP === "some")
    return "Each teammate has been <b>assigned four of the " + crateWord(N_CRATES) +
      " crates</b> to work on. There will be <b>" +
      (OVERLAP === "none" ? "no overlaps</b> in assignment." : "some overlap</b> in your assignments.");
  return "Each teammate has been <b>privately assigned four of the eight crates</b> to work on. Assignments can overlap.";
}

SCR.intro1 = function () {
  /* Audit F05: locked owner copy verbatim per overlap level — the "will not
     be told…" clause is superseded under the disclosed levels and `some` is
     a guaranteed overlap, never "may". */
  var assignPara = "<p>" + assignmentRule() +
    " Your crates will be assigned after a brief training." +
    (OVERLAP === "independent" ? " You will not be told which crates your teammates have been assigned." : "") +
    (OVERLAP !== "full" ? " Your file may contain information about crates outside your assignment, and that information may be valuable to a teammate." : "") + "</p>";
  return panel("Team-based investigation game",
    "<p>For this study, you will be one of three investigators working together on a shared case.</p>" +
    "<h2>Case background</h2>" +
    "<p>" + crateWord(N_CRATES, true) + " crates of high-value merchandise have been stolen. The thieves are moving the crates between six known hideouts. As a team, your goal is to identify the locations of all " + crateWord(N_CRATES) + " crates.</p>" +
    "<p>You and your teammates will each receive time-stamped intelligence that will help you solve the mystery. You can choose to share intelligence from your case file in the team chat if you want. You can also ask your teammates to share intelligence with you.</p>" +
    assignPara +
    "<p>Your task will be to locate each of the assigned crates" + (NOBONUS ? "" : ", and as noted, you are eligible to receive a bonus based on your performance") + ". Next, you will review your workspace.</p>" +
    '<div class="initials-block"><label for="initials">You will be identified to your team by your initials.</label>' +
    '<input id="initials" autocomplete="off" maxlength="3" value=""></div>' +
    '<p class="error-note" id="initials-err" role="alert" style="display:none">Enter your initials to continue.</p>' +
    '<button type="button" class="primary" id="intro1-next">Continue to your workspace</button>');
};

SCR.intro2 = function () {
  return panel("Your workspace",
    "<p>During the task you will see one screen with " + (AI_ASSISTANT ? "three" : "two") + " panes and an answer bar:</p>" +
    '<div class="workspace-diagram' + (AI_ASSISTANT ? "" : " no-assistant") + '" aria-hidden="true"><div class="wd-case">Case file</div>' +
    '<div class="wd-chat">Team chat</div>' + (AI_ASSISTANT ? '<div class="wd-ai">AI assistant</div>' : "") +
    '<div class="wd-answers">Submit a location for each of your crates</div></div>' +
    "<ul><li>You will have <b>5 minutes</b> to locate as many of the crates as you can.</li>" +
    "<li>You can drag entries in your case file to reorder them.</li>" +
    "<li>" +
    (OVERLAP === "full"
      ? "Everyone works on the same four crates. "
      : "Everyone has a private four-crate assignment. ") +
    (NL_ONLY
      ? "Use the team chat to ask teammates for intel and to share information that may help them."
      : "Ask teammates for intel with the <b>Ask the team about…</b> picker in the team chat — or type your own message.") + "</li>" +
    "<li>You can share an intel entry from your case file in the team chat. Anything you share in the chat will be visible to both of your teammates.</li>" +
    (AI_ASSISTANT
      ? "<li>The AI assistant can read your case file, including any intel your teammates share with you from their case files in the chat.</li>"
      : "") +
    /* UX_Feedback_v9 item 1: bolded final bullet above Continue. */
    "<li><b>All intelligence is accurate as of the time it was recorded, but a crate may have changed locations later. Your goal is to identify where the crate is now.</b></li></ul>" +
    '<button type="button" class="primary" id="intro2-next">Continue</button>');
};

SCR.practice1 = function () {
  return panel("Practice: Sharing intel with your team",
    "<p>Try sharing an entry with your team below. The team chat on the right is a preview of what it looks like during the task.</p>" +
    '<section aria-label="Share intel from your file"><h2>Share intel from your file</h2>' +
    '<p class="pane-hint">Each entry has a Share button. You can also drag an entry into the team chat below.</p>' +
    '<ul class="files-list" id="practice-file">' +
    '<li class="file" draggable="true" data-rec="PRACTICE-K3-04">' +
    '<div class="file-main"><div class="file-head"><span class="file-stamp">2 days ago, 2:45 PM</span><span class="file-crate">Crate 3</span></div>' +
    '<div class="file-body">Field team: Crate 3 arriving at Ridgeline Bunker.</div></div>' +
    '<div class="file-actions" id="practice-share-slot"><button type="button" id="practice-share">Share</button></div></li></ul></section>' +
    '<section aria-label="Team chat"><h2>Team chat</h2>' +
    '<ul class="msgs" id="practice-chat">' +
    '<li class="msg"><span class="who">Teammate (practice)</span><span class="msg-body">Sharing intel on Crate 1</span></li>' +
    '<li class="intel-card"><span class="filepill"><span class="fs">Today, 9:10 AM</span><span class="file-crate">Crate 1</span> — Teammate shared: Crate 1 sighted near The Marina dock entrance.<span class="file-source"> — in your case file</span></span></li>' +
    "</ul>" +
    '<p class="pane-hint">This is what the team chat looks like during the task. When a teammate shares intel here, it appears in your case file automatically with a “Shared by” tag.</p>' +
    '<div class="team-actions structured-only"><select disabled><option value="">Ask the team about…</option>' +
    ASSIGNED.map(function (n) { return "<option>" + crateLabel(n) + "</option>"; }).join("") +
    '</select><button type="button" disabled>Ask</button></div>' +
    '<div class="composer structured-only"><input type="text" disabled placeholder="Message the team" readonly><button type="button" disabled>Send</button></div>' +
    '<p class="pane-hint structured-only">Preview only — these controls unlock when the task begins.</p>' +
    '<p class="pane-hint nl-only">Preview only — these controls unlock when the task begins.</p>' +
    '<div class="composer nl-only"><input type="text" id="practice-ask-input" disabled placeholder="Try: anything on crate 3?" readonly><button type="button" id="practice-ask-send" disabled>Send</button></div>' +
    '<p class="error-note nl-only" id="practice-ask-error" role="alert" hidden>That doesn’t look like an intel ask — try something like “anything on crate 3?”</p></section>' +
    /* v14 (2026-10-16, owner direction): the answer-entry pick is practised
       here too — the DV mechanic the v13 zero-answer cohort never rehearsed.
       Mirrors the real gate: share + pick both required. */
    '<section aria-label="Record a practice location"><h2>Record a crate location</h2>' +
    '<p class="pane-hint">During the task, a bar at the bottom of the screen holds a picker for each of your crates. Try it here — pick a location for practice Crate 3.</p>' +
    '<div class="composer" id="practice-pick-slot"><select id="practice-pick"><option value="" disabled selected>— choose a location —</option>' +
    HIDEOUTS.map(function (h) { return '<option value="' + h + '">' + h + '</option>'; }).join("") +
    '</select></div></section>' +
    '<div class="btn-row"><button type="button" class="primary" id="practice1-next" disabled>Complete the practice steps to Continue</button></div>');
};

SCR.practice2 = function () {
  return panel("Practice: Interacting with the AI assistant",
    '<section aria-label="AI assistant preview"><h2>AI assistant</h2>' +
    '<p class="pane-hint">Ask about a crate and it will review the evidence currently in your case file.</p>' +
    '<ul class="msgs"><li class="msg mine"><span class="who">' + esc(you()) + '</span>' +
    '<span class="msg-body">What does my file say about Crate 3?</span></li>' +
    '<li class="msg"><span class="who">Assistant (practice)</span>' +
    '<span class="msg-body">In the task, the assistant answers from what is currently in your case file.</span></li></ul>' +
    '<div class="team-actions structured-only"><select disabled><option value="">Ask the assistant about…</option>' +
    ASSIGNED.map(function (n) { return "<option>" + crateLabel(n) + "</option>"; }).join("") +
    '</select></div>' +
    '<div class="composer"><input type="text" disabled placeholder="Ask the assistant" readonly><button type="button" disabled>Ask</button></div>' +
    '<p class="pane-hint">Preview only — these controls unlock when the task begins.</p></section>' +
    '<div class="btn-row"><button type="button" class="secondary" id="practice2-back">Back</button>' +
    '<button type="button" class="primary" id="practice2-next">Continue to your team</button></div>');
};

/* Comprehension check (mirrors the real build, 2026-10-10; v2 2026-10-16 adds
   record_before_timer): six true/false items gate "Continue to your team";
   wrong answers highlight, retries allowed.
   The mock holds the key locally — the real server holds it and records misses. */
var COMP_ITEMS = [
  {id: "partial_intel", text: "Every team member receives some, but not all, available intelligence.", want: true},
  /* 2026-10-15 crate-condition experiment: under full overlap the item is TRUE —
     mirrors the bound-manifest key flip in the real build. */
  {id: "same_crates", text: "Every team member is working on the exact same crates.", want: OVERLAP === "full"},
  {id: "accurate_not_latest", text: "The intelligence in my case file is accurate, but may not be the latest available.", want: true},
  {id: "teammates_may_have_mine", text: "My teammates may have intelligence about my assigned crates.", want: true},
  {id: "assistant_reads_team", text: "The AI assistant has access to my teammates’ case files.", want: false},
  /* v14 (2026-10-16): answer-entry task-model probe — unconditional. */
  {id: "record_before_timer", text: "I record each of my crate locations before the timer expires.", want: true}
].filter(function (q) { return AI_ASSISTANT || q.id !== "assistant_reads_team"; });
SCR.comprehension = function () {
  var lis = COMP_ITEMS.map(function (q) {
    return '<li id="comp-' + q.id + '"><fieldset><legend>' + esc(q.text) + '</legend>' +
      '<label><input type="radio" name="comprehension-' + q.id + '" value="true"> True</label>' +
      '<label><input type="radio" name="comprehension-' + q.id + '" value="false"> False</label>' +
      '</fieldset></li>';
  }).join("");
  return panel("Comprehension check",
    '<p>To confirm that you understand the task, please answer the following True or False questions.</p>' +
    '<ul class="comprehension-list">' + lis + "</ul>" +
    '<p class="error-note" id="comp-missed" role="alert" hidden>Some answers are incorrect — review the highlighted items and try again.</p>' +
    '<div class="btn-row"><button type="button" class="secondary" id="comp-back">Back</button>' +
    '<button type="button" class="primary" id="comp-next" disabled>Continue to your team</button></div>');
};

SCR.aiqa = function () {
  return panel("AI Q&amp;A (Optional)",
    "<p>If anything about the rules of the task is unclear, you can ask a question below before you meet your team. The assistant answers questions about how the Investigators task works. You do not have to ask anything to continue.</p>" +
    '<div id="qa-log"></div>' +
    '<label class="qa-label" for="qa-question">What is your question? (about the rules of the Investigators task)</label>' +
    '<textarea id="qa-question" class="qa-input" rows="3" maxlength="1000" placeholder="Type your question here…"></textarea>' +
    '<div class="qa-actions"><button type="button" class="primary qa-button" id="qa-ask" disabled>Ask</button></div>' +
    '<button type="button" class="primary qa-button" id="qa-next">Continue</button>');
};

SCR.connecting = function () {
  return panel("Connecting you to your team",
    '<ul class="joining-list">' +
    '<li><span class="dot online"></span> JT <span class="status">connected</span></li>' +
    '<li><span class="dot online"></span> ' + esc(you()) + ' <span class="status">connected</span></li>' +
    '<li id="pm-join"><span class="dot"></span> PM <span class="status">joining…</span></li></ul>' +
    "<p>The clock starts when all of you have pressed Start.</p>" +
    "<p>" + assignmentRule() + "</p>" +
    /* UX_Feedback_v9 item 2: goal reminder on the connecting screen —
       2026-10-15: the assignment condition is restated here. */
    "<p>Remember: Your goal is to identify the current location of " +
    (OVERLAP === "full" ? "the" : "your") + " assigned crates." +
    " You and your teammates will each receive time-stamped intelligence that will help you solve the mystery. Be sure to scroll through your case file and check your record for each crate, including the day, time, and location.</p>" +
    '<button type="button" class="primary" id="connect-start" disabled>Start</button>');
};

SCR.ready = function () {
  return panel("Get ready", '<p role="status">Your workspace is being prepared…</p>');
};

SCR.task = function () {
  var fileHtml = st.file.map(fileLi).join("");
  var pickers = ASSIGNED.map(function (n) {
    var opts = HIDEOUTS.map(function (h) {
      return '<option value="' + h + '"' + (st.answers[n] === h ? " selected" : "") + ">" + h + "</option>";
    }).join("");
    return '<div class="crate-picker"><label>Crate ' + n + '</label><select class="ans-sel" data-crate="' + n + '">' +
      '<option value="">— choose a location —</option>' + opts + "</select></div>";
  }).join("");
  return '<div class="task-screen"><header class="task-header"><div class="task-heading"><b>Case file</b>' +
    '<span class="goal">Locate your crates, then record your answers at bottom of page</span></div>' +
    /* v14 (2026-10-16, owner direction): answer progress lives in the top
       ribbon, left of the timer — the footer copy was buried. */
    '<span class="task-center"><span class="task-progress">' +
    '<span class="progress-count" id="ans-progress">0 of ' + ASSIGNED.length + ' crates recorded</span>' +
    '<span class="crate-pills" id="ans-pills">' +
    ASSIGNED.map(function (n) { return '<span class="crate-pill" id="pill-' + n + '">' + n + '</span>'; }).join("") +
    '</span></span>' +
    '<span class="final-check-note" id="final-check-note" hidden role="status">Please double-check your crate locations now.</span>' +
    '<span class="timer" id="timer">5:00</span></span>' +
    '<div class="task-controls"><span class="mine-strip">Your crates: ' + ASSIGNED.join(", ") + "</span>" +
    '<button type="button" class="skip-btn" id="skip-btn" title="Tester shortcut (mock only) — end the task now and continue to the post-task screens">skip →</button></div></header>' +
    '<div class="task-grid' + (AI_ASSISTANT ? "" : " no-assistant") + '">' +
    '<section class="pane" data-pane="case-file"><header class="pane-head"><h2>Case file</h2><span class="count" id="file-count"></span></header>' +
    '<ul class="files-list" id="file-list">' + fileHtml + "</ul></section>" +
    '<section class="pane" id="chat-pane" data-pane="team-chat"><header class="pane-head"><h2>Team chat</h2></header>' +
    '<div class="notice-bar" id="solo-notice" hidden>Review your case file first — ' +
    (AI_ASSISTANT ? "the team chat and the assistant open" : "the team chat opens") +
    ' when the task begins.</div>' +
    '<div class="pane-lock" id="chat-lock" hidden>Solo review — team chat opens when the task begins</div>' +
    '<div id="strip-slot"></div><ul class="msgs" id="chat-list"></ul>' +
    '<div id="greet-slot"></div>' +
    '<div class="team-actions structured-only"><select id="ask-sel"><option value="">Ask the team about…</option>' +
    ASSIGNED.map(function (n) { return "<option value=\"" + n + "\">" + crateLabel(n) + "</option>"; }).join("") +
    '</select><button type="button" id="ask-btn">Ask</button></div>' +
    '<div class="composer"><input id="chat-in" type="text" placeholder="Message the team"><button type="button" id="chat-send">Send</button></div></section>' +
    (AI_ASSISTANT
      ? '<section class="pane" id="ai-pane" data-pane="assistant"><header class="pane-head"><h2>AI assistant</h2></header>' +
        '<div class="pane-lock" id="ai-lock" hidden>Solo review — the assistant opens when the task begins</div>' +
        '<p class="pane-hint">Ask about a crate and it will review the evidence currently in your case file.</p>' +
        '<ul class="msgs" id="ai-list"></ul>' +
        '<div class="team-actions structured-only"><select id="ai-sel"><option value="">Ask the assistant about…</option>' +
        ASSIGNED.map(function (n) { return "<option value=\"" + n + "\">" + crateLabel(n) + "</option>"; }).join("") +
        '</select></div>' +
        '<div class="composer"><input id="ai-in" type="text" placeholder="Type a question"><button type="button" id="ai-btn">Ask</button></div></section>'
      : "") +
    "</div>" +
    '<footer class="submit-bar"><div class="submit-head"><h2 class="submit-title">Your answers</h2>' +
    '<p class="prompt">Record where each of your crates is currently located. Any crate location selected before the clock runs out is your final answer.' + (NOBONUS ? "" : ' <b>You are eligible for a bonus based on how many locations you correctly identify.</b>') + '</p></div>' +
    '<div class="submit-row">' + pickers + "</div></footer></div>" +
    '<div id="overlay-slot"></div>';
};

SCR.timeup = function () {
  var frozen = ASSIGNED.filter(function (n) { return st.answers[n]; })
    .map(function (n) { return "<li>Crate " + n + ": " + esc(st.answers[n]) + "</li>"; }).join("");
  return panel("Time is up",
    "<p>Your answers are now locked.</p>" +
    (frozen ? "<ul>" + frozen + "</ul>" : '<p><i class="no-ans">No answers were submitted.</i></p>') +
    '<button type="button" class="primary" id="timeup-next">Continue</button>');
};

SCR.confidence = function () {
  /* Walkthrough aid: always render a rating card per assigned crate so the
     instrument is visible even when a tester skipped the task without
     answering (the live app only rates recorded answers). Sliders carry no
     value until moved — Continue stays disabled until each is touched, the
     same required-items contract as the fielded build. */
  st.confDone = st.confDone || {};
  var allTouched = ASSIGNED.every(function (n) { return st.confDone[n] !== undefined; });
  var cards = ASSIGNED.map(function (n) {
    var ans = st.answers[n];
    var v = st.confDone[n];
    return '<div class="confidence-card"><p class="card-title">Crate ' + n + "</p>" +
      '<p class="card-answer">Your answer: ' + (ans ? "<b>" + esc(ans) + "</b>" : '<i class="no-ans">no answer submitted</i>') + "</p>" +
      '<div class="slider-readout" aria-live="polite">' +
        (v === undefined ? "Confidence: —" : "Confidence: " + v + "%") + "</div>" +
      '<input type="range" min="0" max="100" data-crate="' + n + '"' +
        (v === undefined ? "" : ' value="' + v + '"') + ">" +
      '<div class="scale-ends"><span>0% Completely unsure</span><span>100% Positive</span></div>' +
      '<p class="slider-hint"' + (v === undefined ? "" : " hidden") +
        ">Move the slider to answer. It has no value until you do.</p></div>";
  }).join("");
  return panel("Confidence ratings",
    "<p>Rate how confident you are in each of your answers.</p>" +
    '<div class="confidence-grid">' + cards + "</div>" +
    '<button type="button" class="primary" id="conf-next"' + (allTouched ? "" : " disabled") +
      ">Continue</button>");
};

var POST_ITEMS = [
  "I had the information I needed to make good decisions.",
  "There was important information that I did not have.",
  "My teammates knew things that I did not.",
  /* AI_ASSISTANT_ENABLED=0: the post-task "useful" item is never served. */
].concat(
  AI_ASSISTANT ? ["The AI assistant was useful for this task."] : [],
  [
  "Asking my teammates for information would have taken up time they did not have.",
  "I expected my teammates to reply quickly if I asked them something.",
  "My teammates were willing to help me.",
  "My teammates seemed competent at the task."
  /* TIME_PRESSURE_ENABLED=0: the check-in follow-ups presuppose a check-in
     that never happened — they drop out like the real build's
     PRESSURE_INSTRUMENT_ITEMS filter. */
].concat(NOPRESS ? [] : [
  "After the check-in, I felt like my workload was heavier.",
  "After the check-in, my teammates seemed to feel like their workloads were heavier.",
  "After the check-in, my teammates seemed to feel differently about their workloads."
]));
var SUSPICION = ["All were human", "Some were human, some were automated", "All were automated", "I am not sure"];

SCR.questionnaire = function () {
  var likert = POST_ITEMS.map(function (q, i) {
    return '<div class="item"><p>' + q + '</p><div class="likert-row">' +
      [1,2,3,4,5,6,7].map(function (n) {
        return '<button type="button" class="pick" data-group="q' + i + '">' + n + "</button>";
      }).join("") +
      '</div><div class="scale-ends"><span>Strongly disagree</span><span>Strongly agree</span></div></div>';
  }).join("");
  return panel("A few questions", likert +
    '<p class="slider-hint" id="quest-hint">Please answer every item to continue.</p>' +
    '<button type="button" class="primary" id="quest-next" disabled>Continue</button>');
};

/* UX v5 item 4: the teammate-nature suspicion check sits on its own page,
   after the mechanism items — co-presenting it risks contaminating them. */
SCR.suspicion = function () {
  var choice = '<div class="item"><p>Thinking about the other two members of your team, which best describes them?</p>' +
    '<div class="choice-list">' +
    SUSPICION.map(function (o) {
      return '<button type="button" class="pick choice" data-group="suspicion">' + o + "</button>";
    }).join("") + "</div></div>";
  return panel("A few questions", choice +
    '<p class="slider-hint" id="suspicion-hint">Please answer every item to continue.</p>' +
    '<button type="button" class="primary" id="suspicion-next" disabled>Continue</button>');
};

/* UX v8: two open-ended follow-ups — the second appears below the first only
   after the first is submitted. */
SCR.openended = function () {
  return panel("Two last questions",
    '<div class="item"><p>There was no single right way to approach this task, and people worked through it very differently. Briefly, how did you decide on a location for each of your crates?</p>' +
    '<textarea id="oe1" class="open-ended-text" rows="4"></textarea>' +
    '<p class="done-line" id="oe1-done" hidden>Response recorded.</p>' +
    '<button type="button" class="primary" id="oe1-next" disabled>Continue</button></div>' +
    '<div class="item" id="oe2-block" hidden><p>' +
    (AI_ASSISTANT
      ? 'When you weren\'t sure about a crate, how did you decide which source to rely on: your case file, the team chat, or the AI assistant?'
      : 'When you weren\'t sure about a crate, how did you decide which source to rely on: your case file or the team chat?') +
    '</p>' +
    '<textarea id="oe2" class="open-ended-text" rows="4"></textarea>' +
    '<button type="button" class="primary" id="oe2-next" disabled>Continue</button></div>');
};

SCR.debrief = function () {
  return panel("Debrief",
    "<p>Thank you for taking part. Please read this before finishing.</p>" +
    (NOPRESS
      ? "<p>This study examined how team members share information and make decisions together on a short investigative task" +
        (AI_ASSISTANT ? ", including whether they turn to an AI assistant for help" : "") +
        ". The teammates you worked with were automated systems operated by the research team." + (NOBONUS ? "" : " The accuracy bonus described during the study is paid to every completer.") + "</p>"
      : "<p>This study examined whether learning about teammates' workloads changes how much people ask them for information" +
        (AI_ASSISTANT ? ", and whether they turn to an AI assistant instead" : "") +
        ". The teammates you worked with were automated systems operated by the research team. The workload ratings shown for your teammates at the check-in were assigned by the research team, not reported by anyone. Your own rating was recorded exactly as you entered it." + (NOBONUS ? "" : " The accuracy bonus described during the study is paid to every completer.") + "</p>") +
    "<p>If you have questions about this research, please contact [Principal Investigator] (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also contact the university's human research protection program by emailing irb@university.edu. Please refer to protocol number XXXX-XXXX.</p>" +
    '<button type="button" class="primary" id="debrief-next">Continue</button>');
};

SCR.datause = function () {
  return panel("Questions or concerns?",
    "<p>Your responses will be used for research as described in the consent form. If you would like your responses removed, or if you have any other questions about this research, please contact [Principal Investigator] (pi@university.edu) or [Co-Investigator] (coi@university.edu). You may also contact the university's human research protection program by emailing irb@university.edu. Please refer to protocol number XXXX-XXXX.</p>" +
    "<p>Whatever you decide does not change your payment.</p>" +
    '<button type="button" class="primary" id="datause-next">Finish</button>');
};

SCR.completion = function () {
  return panel("You are finished",
    "<p>Thank you — your session is complete.</p>" +
    '<p>Your completion code is: <b>' + esc(DEMO_CFG.code || "PLACEHOLDER-CODE") + "</b></p>");
};
SCR.missing = function () { return panel("Missing screen", ""); };

/* ===== partial renders (task) =============================================== */
/* v14 (2026-10-16): mirror the real answer bar — pills fill as picks land,
   and the final-check note fires at T-15s for every participant (factual, not
   a warning; nopress keeps it — it is not a low-time cue). */
function updateAnswerProgress() {
  var n = ASSIGNED.filter(function (c) { return st.answers[c]; }).length;
  var count = document.getElementById("ans-progress");
  if (count) count.textContent = n + " of " + ASSIGNED.length + " crates recorded";
  ASSIGNED.forEach(function (c) {
    var pill = document.getElementById("pill-" + c);
    if (pill) pill.classList.toggle("entered", !!st.answers[c]);
  });
}

function renderTimer() {
  var el = document.getElementById("timer");
  if (!el) return;
  var left = BUDGET - st.activeSec;
  el.textContent = fmt(left);
  el.classList.toggle("low-time", !NOPRESS && left <= 60);
}
function renderFile() {
  var ul = document.getElementById("file-list");
  if (!ul) return;
  ul.innerHTML = st.file.map(fileLi).join("");
  var c = document.getElementById("file-count");
  if (c) c.textContent = st.file.length + " entries";
}
function renderChat() {
  var ul = document.getElementById("chat-list");
  if (!ul) return;
  var html = "";
  st.msgs.forEach(function (m) {
    html += '<li class="msg' + (m.mine ? " mine" : "") + '"><span class="who">' + esc(m.who) +
      '</span><span class="msg-body">' + esc(m.text) + "</span></li>";
    if (m.recordId) {
      var rec = RECORDS.filter(function (r) { return r.id === m.recordId; })[0];
      if (rec) html += intelLi(rec, m.mine);
    }
  });
  ul.innerHTML = html;
  ul.scrollTop = ul.scrollHeight;

  var strip = document.getElementById("strip-slot");
  if (strip) {
    strip.innerHTML = st.revealed
      ? '<div class="pressure-strip" role="note">Workload Reported at Check-in: JT: ' +
        CHECKIN_REVEAL.JT + "/7 · PM: " + CHECKIN_REVEAL.PM + "/7 · " + esc(you()) + ": " +
        (st.ownRating == null ? "—" : st.ownRating) + "/7</div>"
      : "";
  }
  var gs = document.getElementById("greet-slot");
  if (gs) {
    gs.innerHTML = (!st.greeted && st.activeSec < GREET_WINDOW && !st.taskClosed)
      ? '<div class="greeting-row" role="group" aria-label="Say hello">' +
        GREETINGS.map(function (g) {
          return '<button type="button" class="greet-btn" data-g="' + g + '">' + g + "</button>";
        }).join("") + "</div>"
      : "";
  }
}
function renderAi() {
  var ul = document.getElementById("ai-list");
  if (!ul) return;
  ul.innerHTML = st.aiMsgs.map(function (m) {
    return '<li class="msg' + (m.mine ? " mine" : "") + '"><span class="who">' + esc(m.who) +
      '</span><span class="msg-body">' + esc(m.text) + "</span></li>";
  }).join("");
  ul.scrollTop = ul.scrollHeight;
}
function renderOverlay() {
  var slot = document.getElementById("overlay-slot");
  if (!slot) return;
  if (!st.checkinOpen) { slot.innerHTML = ""; return; }
  var inner = "";
  if (st.checkinPhase === "collect") {
    inner = "<h2>Brief team check-in</h2><p class=\"overlay-sub\">The task clock is paused.</p>" +
      '<p class="checkin-deadline" role="status">All members need to respond so the group task can continue.</p>' +
      "<p>How heavy does your current workload feel right now?</p>" +
      '<div class="likert-row" role="radiogroup">' +
      [1,2,3,4,5,6,7].map(function (n) {
        return '<button type="button" class="checkin-pick" data-n="' + n + '">' + n + "</button>";
      }).join("") +
      '</div><div class="scale-ends"><span>1 — Very light</span><span>7 — Overwhelming</span></div>' +
      '<p class="overlay-note">Your rating will be visible to your teammates. Your teammates are answering the same question.</p>';
  } else if (st.checkinPhase === "wait") {
    inner = "<h2>Brief team check-in</h2><p role=\"status\">Thanks. Collecting your teammates’ ratings…</p>";
  } else {
    inner = "<h2>Brief team check-in</h2><h3>Workload reported at the check-in</h3>" +
      '<div class="reveal-row"><span class="reveal-chip">JT: <b>' + CHECKIN_REVEAL.JT + "/7</b></span>" +
      '<span class="reveal-chip">PM: <b>' + CHECKIN_REVEAL.PM + "/7</b></span>" +
      '<span class="reveal-chip">' + esc(you()) + ": <b>" + st.ownRating + "/7</b></span></div>" +
      '<p class="overlay-note">These ratings will stay visible above the team chat.</p>' +
      '<button type="button" class="primary" id="checkin-resume">Resume task</button>';
  }
  slot.innerHTML = '<div class="overlay"><div class="overlay-box">' + inner + "</div></div>";
  if (st.checkinPhase === "collect") {
    Array.prototype.forEach.call(slot.querySelectorAll(".checkin-pick"), function (b) {
      b.onclick = function () { submitCheckin(Number(b.getAttribute("data-n"))); };
    });
  } else if (st.checkinPhase === "reveal") {
    document.getElementById("checkin-resume").onclick = resumeCheckin;
  }
}

/* ===== wiring =============================================================== */
function wire(stage) {
  if (stage === "consent") {
    document.getElementById("consent-yes").onclick = function () { go("intro1"); };
    document.getElementById("consent-no").onclick = function () { go("declined"); };
  } else if (stage === "intro1") {
    document.getElementById("intro1-next").onclick = function () {
      var v = document.getElementById("initials").value.trim().toUpperCase().replace(/[^A-Z]/g, "");
      if (!v) { document.getElementById("initials-err").style.display = "block"; return; }
      st.initials = v; go("intro2");
    };
  } else if (stage === "intro2") {
    document.getElementById("intro2-next").onclick = function () { go("practice1"); };
  } else if (stage === "practice1") {
    var rec = {id: "PRACTICE-K3-04", crate: 3, stamp: "2 days ago, 2:45 PM",
               body: "Field team: Crate 3 arriving at Ridgeline Bunker."};
    /* UX_Feedback_v10 (2026-10-14): the typed ask is a disabled preview on
       page 1. v14 (2026-10-16): the share + the answer-entry pick gate
       Continue together (label flips when both are done). */
    var shared = false, asked = false, picked = false;
    var gate = function () {
      var next = document.getElementById("practice1-next");
      next.disabled = !(shared && picked);
      if (shared && picked) next.textContent = "Continue";
    };
    /* v14: the practice pick mirrors the real picker's commit — a saved pill
       replaces the select so the mechanic reads like the task's answer bar. */
    var pickSel = document.getElementById("practice-pick");
    if (pickSel) {
      pickSel.onchange = function () {
        if (!pickSel.value || picked) return;
        picked = true;
        document.getElementById("practice-pick-slot").innerHTML =
          '<span class="shared-tag">Location saved for Crate 3 — ' + esc(pickSel.value) + ' (practice)</span>';
        gate();
      };
    }
    var doShare = function () {
      if (shared) return;
      shared = true;
      var slot = document.getElementById("practice-share-slot");
      slot.innerHTML = '<span class="shared-tag">Shared in team chat</span>';
      var chat = document.getElementById("practice-chat");
      chat.innerHTML += '<li class="msg mine"><span class="who">' + esc(you()) + '</span>' +
        '<span class="msg-body">Sharing intel on Crate 3</span></li>' + intelLi(rec, true);
      gate();
    };
    document.getElementById("practice-share").onclick = doShare;
    var askInput = document.getElementById("practice-ask-input");
    if (askInput) {
      var doAsk = function () {
        var text = (askInput.value || "").trim();
        var err = document.getElementById("practice-ask-error");
        /* Audit F01: whole-integer parse bound to the session crate count —
           crates 9–12 are valid asks under the no-overlap world. */
        var m = text.match(/crate\s*(\d+)|\b(\d+)\b/i);
        var parsedAsk = m ? Number(m[1] || m[2]) : 0;
        if (!(parsedAsk >= 1 && parsedAsk <= N_CRATES)) m = null;
        var asky = /\?|what|where|anything|any|intel|update|news/i.test(text);
        if (!m || !asky) { err.hidden = false; return; }
        err.hidden = true;
        if (asked) return;
        asked = true;
        var crate = parseInt(m[1] || m[2], 10);
        var chat = document.getElementById("practice-chat");
        chat.innerHTML += '<li class="msg mine"><span class="who">' + esc(you()) + '</span>' +
          '<span class="msg-body">' + esc(text) + '</span></li>' +
          '<li class="intel-card"><span class="filepill"><span class="fs">Today, 9:20 AM</span>' +
          '<span class="file-crate">Crate ' + crate + '</span> — Crate ' + crate +
          ' sighted near The Marina dock entrance.' +
          '<span class="file-source"> — in your case file</span></span></li>';
        askInput.disabled = true;
        document.getElementById("practice-ask-send").disabled = true;
        gate();
      };
      document.getElementById("practice-ask-send").onclick = doAsk;
      askInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); doAsk(); }
      });
    }
    document.getElementById("practice1-next").onclick = function () {
      go(AI_ASSISTANT ? "practice2" : "comprehension");
    };
    /* drag-drop share (mirrors the real affordance) */
    var li = document.querySelector("#practice-file .file");
    li.addEventListener("dragstart", function (e) { e.dataTransfer.setData("text/plain", "x"); });
    var chatPane = document.querySelector('[aria-label="Team chat"]');
    chatPane.addEventListener("dragover", function (e) { e.preventDefault(); });
    chatPane.addEventListener("drop", function (e) { e.preventDefault(); doShare(); });
  } else if (stage === "practice2") {
    document.getElementById("practice2-back").onclick = function () { go("practice1"); };
    document.getElementById("practice2-next").onclick = function () { go("comprehension"); };
  } else if (stage === "comprehension") {
    var nextBtn = document.getElementById("comp-next");
    var answered = function () {
      return COMP_ITEMS.every(function (q) {
        return document.querySelector('input[name="comprehension-' + q.id + '"]:checked');
      });
    };
    document.querySelectorAll('.comprehension-list input[type="radio"]').forEach(function (r) {
      r.addEventListener("change", function () {
        r.closest("li").classList.remove("missed");
        if (answered()) nextBtn.disabled = false;
      });
    });
    document.getElementById("comp-back").onclick = function () {
      go(AI_ASSISTANT ? "practice2" : "practice1");
    };
    nextBtn.onclick = function () {
      var missed = COMP_ITEMS.filter(function (q) {
        var r = document.querySelector('input[name="comprehension-' + q.id + '"]:checked');
        return (r.value === "true") !== q.want;
      });
      COMP_ITEMS.forEach(function (q) {
        document.getElementById("comp-" + q.id).classList.toggle(
          "missed", missed.indexOf(q) >= 0);
      });
      document.getElementById("comp-missed").hidden = missed.length === 0;
      if (missed.length === 0) go(AIQA ? "aiqa" : "connecting");
    };
  } else if (stage === "aiqa") {
    var qaInput = document.getElementById("qa-question");
    var qaBtn = document.getElementById("qa-ask");
    qaInput.addEventListener("input", function () {
      qaBtn.disabled = qaInput.value.trim().length === 0;
    });
    qaBtn.onclick = function () {
      var q = qaInput.value.trim();
      if (!q) return;
      var log = document.getElementById("qa-log");
      log.insertAdjacentHTML("beforeend",
        '<div class="qa-exchange"><p class="qa-question">' + esc(q) + '</p>' +
        '<p class="qa-answer pending">Thinking…</p></div>');
      qaInput.value = "";
      qaBtn.disabled = true;
      var pending = log.lastElementChild.querySelector(".qa-answer");
      setTimeout(function () {
        pending.classList.remove("pending");
        pending.textContent = demoQaAnswer(q);
      }, 1200);
    };
    document.getElementById("qa-next").onclick = function () { go("connecting"); };
  } else if (stage === "connecting") {
    /* PM joins at CONNECT_PM_JOIN_WALL_MS (~10.6 s in the real build). */
    setTimeout(function () {
      var j = document.getElementById("pm-join");
      if (j) {
        j.querySelector(".dot").classList.add("online");
        j.querySelector(".status").textContent = "connected";
        document.getElementById("connect-start").disabled = false;
      }
    }, 10600);
    document.getElementById("connect-start").onclick = function () { go("ready"); };
  } else if (stage === "ready") {
    setTimeout(function () { go("task"); }, 1800);
  } else if (stage === "task") {
    st.solo = SOLO_PHASE;
    renderFile(); renderChat(); renderAi(); renderTimer();
    applySoloLock();
    st.timerId = setInterval(tick, 1000);
    document.getElementById("skip-btn").onclick = function () {
      if (st.checkinOpen) resumeCheckin();
      closeTask();
    };
    document.getElementById("chat-send").onclick = sendChat;
    document.getElementById("chat-in").addEventListener("keydown", function (e) {
      if (e.key === "Enter") sendChat();
    });
    document.getElementById("ask-btn").onclick = askTeam;
    var aiBtn = document.getElementById("ai-btn"), aiIn = document.getElementById("ai-in");
    if (aiBtn) aiBtn.onclick = askAssistant;
    if (aiIn) aiIn.addEventListener("keydown", function (e) {
      if (e.key === "Enter") askAssistant();
    });
    /* delegated: share buttons, greeting buttons, drag-drop share, answer selects */
    document.getElementById("file-list").addEventListener("click", function (e) {
      var b = e.target.closest(".share-btn");
      if (b) shareRecord(b.getAttribute("data-rec"));
    });
    document.getElementById("file-list").addEventListener("dragstart", function (e) {
      var li = e.target.closest(".file");
      if (li) e.dataTransfer.setData("text/plain", li.getAttribute("data-rec"));
    });
    document.getElementById("chat-pane").addEventListener("dragover", function (e) { e.preventDefault(); });
    document.getElementById("chat-pane").addEventListener("drop", function (e) {
      var id = e.dataTransfer.getData("text/plain");
      if (id) { e.preventDefault(); shareRecord(id); }
    });
    document.getElementById("greet-slot").addEventListener("click", function (e) {
      var b = e.target.closest(".greet-btn");
      if (b) greet(b.getAttribute("data-g"));
    });
    Array.prototype.forEach.call(document.querySelectorAll(".ans-sel"), function (sel) {
      sel.onchange = function () {
        st.answers[Number(sel.getAttribute("data-crate"))] = sel.value || null;
        updateAnswerProgress();
      };
    });
  } else if (stage === "timeup") {
    document.getElementById("timeup-next").onclick = function () { go("confidence"); };
  } else if (stage === "confidence") {
    st.confDone = st.confDone || {};
    var sliders = root.querySelectorAll('.confidence-card input[type="range"]');
    Array.prototype.forEach.call(sliders, function (sl) {
      sl.addEventListener("input", function () {
        var n = Number(sl.getAttribute("data-crate"));
        st.confDone[n] = Number(sl.value);
        var card = sl.closest(".confidence-card");
        card.querySelector(".slider-readout").textContent = "Confidence: " + sl.value + "%";
        var hint = card.querySelector(".slider-hint");
        if (hint) hint.hidden = true;
        if (ASSIGNED.every(function (c) { return st.confDone[c] !== undefined; }))
          document.getElementById("conf-next").disabled = false;
      });
    });
    document.getElementById("conf-next").onclick = function () { go("questionnaire"); };
  } else if (stage === "questionnaire" || stage === "suspicion") {
    /* Same required-items contract as the fielded build: Continue stays
       disabled until every group on the page has a picked answer. */
    var next = document.getElementById("quest-next") || document.getElementById("suspicion-next");
    var hint = document.getElementById("quest-hint") || document.getElementById("suspicion-hint");
    var checkPicks = function () {
      var groups = {};
      Array.prototype.forEach.call(root.querySelectorAll(".pick"), function (b) {
        groups[b.getAttribute("data-group")] = true;
      });
      var done = Object.keys(groups).every(function (g) {
        return !!root.querySelector('.pick.picked[data-group="' + g + '"]');
      });
      if (next) next.disabled = !done;
      if (hint) hint.hidden = done;
    };
    root.addEventListener("click", function h(e) {
      var b = e.target.closest(".pick");
      if (!b) return;
      var g = b.getAttribute("data-group");
      Array.prototype.forEach.call(
        root.querySelectorAll('.pick[data-group="' + g + '"]'),
        function (x) { x.classList.remove("picked"); });
      b.classList.add("picked");
      checkPicks();
    });
    var qn = document.getElementById("quest-next");
    if (qn) qn.onclick = function () { go("suspicion"); };
    var sn = document.getElementById("suspicion-next");
    if (sn) sn.onclick = function () { go("openended"); };
  } else if (stage === "openended") {
    var oe1 = document.getElementById("oe1");
    var oe2 = document.getElementById("oe2");
    var n1 = document.getElementById("oe1-next");
    var n2 = document.getElementById("oe2-next");
    oe1.addEventListener("input", function () { n1.disabled = !oe1.value.trim(); });
    oe2.addEventListener("input", function () { n2.disabled = !oe2.value.trim(); });
    n1.onclick = function () {
      oe1.disabled = true;
      n1.hidden = true;
      document.getElementById("oe1-done").hidden = false;
      document.getElementById("oe2-block").hidden = false;
    };
    n2.onclick = function () { go("debrief"); };
  } else if (stage === "debrief") {
    document.getElementById("debrief-next").onclick = function () { go("datause"); };
  } else if (stage === "datause") {
    document.getElementById("datause-next").onclick = function () { go("completion"); };
  }
}

/* Variant chrome: nl builds hide structured ask controls; every variant carries
   a fixed mock bar naming what it is, so screenshots are self-identifying. */
if (NL_ONLY) document.body.classList.add("nl");
var mb = document.createElement("div");
mb.className = "mock-bar";
mb.innerHTML = '<span class="mock-title">PLAYABLE MOCK — ' +
  esc(DEMO_CFG.label || "Investigators task") + "</span>" +
  "<span>canned replies · not a live session</span>";
document.body.insertBefore(mb, document.body.firstChild);

go("consent");

/* Debug/test handle: drives the stage machine and the active-second clock without
   waiting on real timers (used by the headless smoke; harmless in the mock). */
window.__demo = { st: st, go: go, tick: tick, shareRecord: shareRecord,
                  askTeam: function () {}, submitCheckin: submitCheckin,
                  resumeCheckin: resumeCheckin, deliverRecord: deliverRecord,
                  greet: greet, sendChatText: function (t) {
                    st.msgs.push({who: you(), mine: true, text: t}); renderChat();
                  } };
})();
