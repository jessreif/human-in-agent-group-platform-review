#!/usr/bin/env python3
"""Generate the per-build click-through HTML mock (UX round 3, FR-2; plan §17.2).

    python3 scripts/build_clickthrough.py          # write docs/clickthrough/...html
    python3 scripts/build_clickthrough.py --check  # fail if stale

One self-contained, editable HTML file — inline CSS/JS, no network, no live agents — that
walks every participant screen in order. Back/Next always work (nothing gates progress);
canned replies stand in for teammates and the assistant. Toggles switch the FR-1
interaction mode (structured + natural language vs natural-language only) and the study
family (time pressure vs peer evaluation). Each screen is a ``<section class="screen">``
with a stable ``id`` and an EDIT-ME comment so testers can revise copy directly and send
the file back as UX feedback.

Participant copy comes from the bound content artifacts (consent, check-in, instruments,
warning) and the UI's own stylesheet, so the mock tracks the build; the page is stamped
with the source-bundle checksum and a stale mock fails ``scripts/check.sh``. ``docs/`` is
outside the source bundle. Runs on the host's Python 3.9.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import source_bundle  # noqa: E402

OUT = ROOT / "docs" / "clickthrough" / "investigators_clickthrough.html"
CONTENT = ROOT / "content"


def _artifact(rel: str) -> Dict[str, Any]:
    body: Dict[str, Any] = json.loads((CONTENT / rel).read_text(encoding="utf-8"))["body"]
    return body


def _hideouts() -> List[str]:
    src = (ROOT / "backend" / "app" / "domain" / "world.py").read_text(encoding="utf-8")
    m = re.search(r"HIDEOUTS[^=]*=\s*\(([^)]*)\)", src)
    names = re.findall(r'"([^"]+)"', m.group(1)) if m else []
    return names or ["The Sawmill", "The Marina", "Blackwell Storage", "Old Cannery"]


e = html.escape


def _likert(name: str, lo: int, hi: int) -> str:
    buttons = "".join(
        f'<button type="button" class="mock-pick" data-group="{e(name)}">{n}</button>'
        for n in range(lo, hi + 1)
    )
    return f'<div class="likert-row">{buttons}</div>'


def render() -> str:
    sha = source_bundle.aggregate(source_bundle.manifest())
    css = (ROOT / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")
    consent = _artifact("instruments/consent.reference.v5.json")["text"]
    checkin = _artifact("scripts/checkin.pressure_demo.v1.json")
    confidence = _artifact("instruments/confidence.demo.v1.json")
    post = _artifact("instruments/post_task.tp_demo.v2.json")
    peer = _artifact("instruments/peer_ratings.pe_demo.v1.json")
    warning = _artifact("scripts/warning.one_minute_left.v1.json")["text"]
    places = _hideouts()
    options = "".join(f"<option>{e(p)}</option>" for p in places)
    crate_opts = "".join(f"<option>Crate {n}</option>" for n in range(1, 9))

    consent_html = "".join(
        (f"<h2>{e(sec['title'])}</h2>" if sec.get("title") else "")
        + "".join(f"<p>{e(p)}</p>" for p in sec["paragraphs"])
        for sec in consent["sections"]
    )
    screens: List[str] = []

    def screen(sid: str, title: str, body: str, *, study: str = "", wide: bool = False) -> None:
        attrs = f' data-study="{study}"' if study else ""
        cls = "screen wide" if wide else "screen"
        screens.append(
            f"<!-- EDIT-ME: screen {sid} ({title}) — revise copy/layout here -->\n"
            f'<section class="{cls}" id="{sid}" data-title="{e(title)}"{attrs}>\n{body}\n</section>'
        )

    screen(
        "consent",
        "Consent",
        f'<main class="panel consent-panel"><h1>{e(consent["heading"])}</h1>'
        f'<p class="consent-sub">{e(consent.get("subheading", ""))}</p>'
        f'<div class="consent-copy">{consent_html}</div>'
        f'<p class="consent-question">{e(consent["question"])}</p>'
        f'<div class="btn-row"><button class="primary mock-next">{e(consent["accept_label"])}</button>'
        f'<button class="secondary">{e(consent["decline_label"])}</button></div></main>',
    )
    screen(
        "background",
        "Background + initials",
        """<main class="panel"><h1>Team-based investigation game</h1>
<p>For this study, you will be one of three investigators working together on a shared case.</p>
<h2>Case background</h2>
<p>Eight crates of high-value merchandise have been stolen. The thieves are moving the crates
between six known hideouts. As a team, your goal is to identify the locations of all eight crates.</p>
<p>You and your teammates will each receive time-stamped intelligence that will help you solve the
mystery. You can choose to share intelligence from your case file in the team chat if you want. You
can also ask your teammates to share intelligence with you.</p>
<p>Each teammate has been <b>privately assigned four of the eight crates</b> to work on.</p>
<div class="initials-block"><label for="m-initials">You will be identified to your team by your initials.</label>
<input id="m-initials" maxlength="3" value="AB"></div>
<button class="primary mock-next">Continue to your workspace</button></main>""",
    )
    screen(
        "workspace-tour",
        "Workspace tour",
        """<main class="panel"><h1>Your workspace</h1>
<p>During the task you will see one screen with three panes and an answer bar:</p>
<div class="workspace-diagram"><div class="wd-case">Case file</div><div class="wd-chat">Team chat</div>
<div class="wd-ai">AI assistant</div><div class="wd-answers">Submit a location for each of your crates</div></div>
<ul><li>You will have <b>6 minutes</b> to locate as many of the crates as you can.</li>
<li>You can drag entries in your case file to reorder them.</li>
<li>Use the team chat to ask teammates for intel and to share information that may help them.</li>
<li>The AI assistant can read your case file, including any intel your teammates share with you from their case files in the chat.</li></ul>
<button class="primary mock-next">Continue</button></main>""",
    )
    screen(
        "practice-team",
        "Practice: Sharing intel with your team",
        """<main class="panel"><h1>Practice: Sharing intel with your team</h1>
<p>Try sharing an entry with your team below. The team chat on the right is a preview
of what it looks like during the task.</p>
<section><h2>Share intel from your file</h2>
<ul class="files-list"><li class="file"><div class="file-main"><div class="file-head"><span class="file-stamp">Today, 9:05 AM</span></div>
<div class="file-body">Practice entry K3-04.</div></div><div class="file-actions"><button class="mock-share">Share</button></div></li></ul></section>
<section><h2>Team chat</h2><ul class="msgs mock-chat"><li class="msg"><span class="who">Teammate (practice)</span>
<span class="msg-body">Sharing intel on Crate 1</span></li>
<li class="intel-card"><span class="filepill"><span class="fs">Today, 9:10 AM</span>
Crate 1 sighted near The Marina dock entrance.<span class="file-source"> — in your case file</span></span></li></ul>
<p class="pane-hint">This is what the team chat looks like during the task. When a teammate shares
intel here, it appears in your case file automatically with a “Shared by” tag.</p>
<div class="composer"><input placeholder="Message the team" disabled><button disabled>Send</button></div></section>
<button class="primary mock-next">Continue</button></main>""",
    )
    screen(
        "practice-assistant",
        "Practice: Interacting with the AI assistant",
        """<main class="panel"><h1>Practice: Interacting with the AI assistant</h1>
<section><h2>AI assistant</h2><p class="pane-hint">Ask about a crate and it will review the evidence currently in your case file.</p>
<ul class="msgs mock-ai"><li class="msg mine"><span class="who">You</span>
<span class="msg-body">What does my file say about Crate 3?</span></li>
<li class="msg"><span class="who">Assistant (practice)</span>
<span class="msg-body">In the task, the assistant answers from what is currently in your case file.</span></li></ul>
<div class="composer"><input placeholder="Ask the assistant" disabled><button disabled>Ask</button></div></section>
<div class="btn-row"><button class="secondary mock-back">Back</button><button class="primary mock-next">Continue to your team</button></div></main>""",
    )
    screen(
        "waiting-room",
        "Waiting room",
        """<main class="panel"><h1>Connecting you to your team</h1>
<ul class="joining-list"><li><span class="dot online"></span> JT <span class="status">connected</span></li>
<li><span class="dot online"></span> AB (you) <span class="status">connected</span></li>
<li class="mock-join"><span class="dot"></span> PM <span class="status">joining…</span></li></ul>
<p>The clock starts when all of you have pressed Start.</p>
<button class="primary mock-next">Start</button>
<p class="overlay-note">(Mock: PM joins after a few seconds; Start always advances here.)</p></main>""",
    )
    task_body = f"""<div class="task-screen"><header class="task-header"><b>Case file</b><span class="goal">Locate your crates, then record your answers at bottom of page</span>
<span class="mine-strip">Your crates: 2, 4, 6, 8</span><span class="countdown">5:12</span></header>
<div class="task-grid">
<section class="pane"><header class="pane-head"><h2>Case file</h2><span class="count">3 entries</span></header>
<ul class="files-list">
<li class="file"><span class="drag-grip">✥</span><div class="file-main"><div class="file-head"><span class="file-stamp">Today, 9:00 AM</span></div>
<div class="file-body">Crate 2 spotted at {e(places[0])}.</div></div><div class="file-actions"><button class="mock-share">Share</button></div></li>
<li class="file"><span class="drag-grip">✥</span><div class="file-main"><div class="file-head"><span class="file-stamp">Yesterday, 4:30 PM</span></div>
<div class="file-body">Crate 6 left {e(places[1])}.</div></div><div class="file-actions"><button class="mock-share">Share</button></div></li>
<li class="file from-team"><span class="drag-grip">✥</span><div class="file-main"><div class="file-head"><span class="file-stamp">Today, 10:00 AM</span>
<span class="file-source">Shared by JT</span></div><div class="file-body">Crate 4 sighted near {e(places[3])}.</div></div>
<div class="file-actions"></div></li>
</ul></section>
<section class="pane"><header class="pane-head"><h2>Team chat</h2></header>
<ul class="msgs mock-chat"><li class="msg"><span class="who">JT</span><span class="msg-body">hey all</span></li>
<li class="msg"><span class="who">PM</span><span class="msg-body">Hi everyone, good luck!</span></li>
<li class="msg"><span class="who">System</span><span class="msg-body">{e(warning)}</span></li></ul>
<div class="greeting-row"><button class="mock-greet">Hello!</button><button class="mock-greet">Hi!</button><button class="mock-greet">Hey!</button></div>
<div class="team-actions structured-only"><select class="mock-ask"><option value="">Ask the team about…</option>{crate_opts}</select>
<button class="mock-ask-btn" data-reply="sharing my latest on that — check the card in chat">Ask</button></div>
<div class="composer"><input class="mock-text" placeholder="Reply to the team"><button class="mock-send"
 data-reply="doing ok thanks, working through mine. you?">Send</button></div></section>
<section class="pane"><header class="pane-head"><h2>AI assistant</h2></header>
<p class="pane-hint">Ask about a crate and it will review the evidence currently in your case file.</p>
<ul class="msgs mock-ai"></ul>
<div class="team-actions structured-only"><select class="mock-ask"><option value="">Ask the assistant about…</option>{crate_opts}</select></div>
<div class="composer"><input class="mock-text" placeholder="Type a question"><button class="mock-assist"
 data-reply="The latest entry in your file says Crate 2 was spotted at {e(places[0])} today at 9:00 AM.">Ask</button></div></section>
</div>
<footer class="submit-bar"><h2 class="submit-title">Your answers</h2>
<p class="prompt">Record where each of your crates is currently located. Your answers are recorded automatically when the clock runs out.</p>
<div class="submit-row">{"".join(f'<div class="crate-picker"><label>Crate {n}</label><select><option>— choose a location —</option>{options}</select></div>' for n in (2, 4, 6, 8))}</div>
</footer></div>"""
    screen("workbench", "Workbench (task)", task_body, wide=True)
    screen(
        "checkin",
        "Check-in (time pressure)",
        f"""<div class="overlay" style="position:static;background:none"><div class="overlay-box">
<h2>Brief team check-in</h2><p class="overlay-sub">The task clock is paused.</p>
<p>{e(checkin["question"])}</p>{_likert("checkin", 1, 7)}
<div class="scale-ends"><span>{e(checkin["scale_min_label"])}</span><span>{e(checkin["scale_max_label"])}</span></div>
<p class="overlay-note">{e(checkin["note"])}</p>
<hr><p role="status">{e(checkin["collecting"])}</p>
<h3>{e(checkin["reveal_title"])}</h3>
<p class="pane-hint">Time Pressure Reported at Check-in:</p>
<div class="reveal-row"><span class="reveal-chip">JT: <b>6/7</b></span>
<span class="reveal-chip">PM: <b>5/7</b></span><span class="reveal-chip">AB (you): <b>4/7</b></span></div>
<p class="overlay-note">{e(checkin["reveal_note"])}</p><button class="primary mock-next">{e(checkin["resume_label"])}</button>
</div></div>""",
        study="tp",
    )
    screen(
        "time-up",
        "Time is up",
        f'<main class="panel"><h1>Time is up</h1><p>Your answers are now locked.</p>'
        f"<ul><li>Crate 2: {e(places[0])}</li><li>Crate 6: {e(places[2])}</li></ul>"
        '<button class="primary mock-next">Continue</button></main>',
    )
    r = confidence["response"]
    screen(
        "confidence",
        "Confidence ratings",
        '<main class="panel"><h1>Confidence ratings</h1><p>Rate how confident you are in each of your answers.</p>'
        '<div class="confidence-grid">'
        + "".join(
            f'<div class="confidence-card"><p class="card-title">Crate {n}</p><p class="card-answer">Your answer: <b>{e(p)}</b></p>'
            f'<input type="range" min="{r["min"]}" max="{r["max"]}"><div class="scale-ends"><span>{e(r["min_label"])}</span>'
            f'<span>{e(r["max_label"])}</span></div></div>'
            for n, p in ((2, places[0]), (6, places[2]))
        )
        + '</div><button class="primary mock-next">Continue</button></main>',
    )
    pr = peer["response"]
    screen(
        "peer-ratings",
        "Peer ratings",
        '<main class="panel"><h1>Your teammates</h1>'
        + "".join(
            f'<section><h2>About {seat}</h2><div class="item"><p>{e(peer["items"][0]["text"].replace("{seat}", seat))}</p>'
            f'{_likert("peer-" + seat, pr["min"], pr["max"])}<div class="scale-ends"><span>{e(pr["min_label"])}</span>'
            f'<span>{e(pr["max_label"])}</span></div></div></section>'
            for seat in ("JT", "PM")
        )
        + '<button class="primary mock-next">Continue</button></main>',
        study="pe",
    )
    q = post["response"]

    def _post_item(i: int, it: Dict[str, Any]) -> str:
        # UX round 4, item 14: the last post_task item is a fixed-choice pick —
        # render its declared options rather than the Likert row.
        if it.get("options"):
            buttons = "".join(
                f'<button type="button" class="mock-pick choice" data-group="q{i}">{e(o)}</button>'
                for o in it["options"]
            )
            return f'<div class="item"><p>{e(it["text"])}</p><div class="choice-list">{buttons}</div></div>'
        return (
            f'<div class="item"><p>{e(it["text"])}</p>{_likert("q" + str(i), q["min"], q["max"])}'
            f'<div class="scale-ends"><span>{e(q["min_label"])}</span><span>{e(q["max_label"])}</span></div></div>'
        )

    # UX round 5, item 4: fixed-choice items (the teammate-nature suspicion
    # check) get their own page after the mechanism items — same split the live
    # SessionItemsPage applies.
    post_main = [it for it in post["items"] if not it.get("options")]
    post_choice = [it for it in post["items"] if it.get("options")]
    screen(
        "questionnaire",
        "A few questions",
        '<main class="panel"><h1>A few questions</h1>'
        + "".join(_post_item(i, it) for i, it in enumerate(post_main))
        + '<button class="primary mock-next">Continue</button></main>',
    )
    if post_choice:
        screen(
            "suspicion",
            "A few questions (teammate-nature check)",
            '<main class="panel"><h1>A few questions</h1>'
            + "".join(_post_item(i, it) for i, it in enumerate(post_choice))
            + '<button class="primary mock-next">Continue</button></main>',
        )
    screen(
        "debrief",
        "Debrief",
        """<main class="panel"><h1>Debrief</h1><p>Thank you for taking part. Please read this before finishing.</p>
<p>The teammates you worked with were automated systems operated by the research team. (The full,
study-specific debrief copy is shown in the live build.)</p>
<p>If you have questions about this research, please contact the research team listed in the consent form.</p>
<button class="primary mock-next">Continue</button></main>""",
    )
    # BA-56: the real flow passes through the data-use choice between debrief and
    # completion (stage graph debrief → data_use_choice → completion).
    screen(
        "data-use",
        "Questions or concerns? (data-use)",
        """<main class="panel"><h1>Questions or concerns?</h1>
<p>Your responses will be used for research as described in the consent form. If you
would like your responses removed, or if you have any other questions about this
research, please contact the research team listed in the consent form.</p>
<p>Whatever you decide does not change your payment.</p>
<button class="primary mock-next">Finish</button></main>""",
    )
    screen(
        "completion",
        "You are finished",
        '<main class="panel"><h1>You are finished</h1><p>Thank you — your session is complete.</p>'
        "<p>Your completion code is <b>INV-MOCK0000</b>.</p></main>",
    )

    nav_css = """
body { margin: 0; padding-top: 52px; }
.mock-bar { position: fixed; top: 0; left: 0; right: 0; z-index: 100; display: flex; gap: 10px;
  align-items: center; padding: 8px 12px; background: #1f2d3a; color: #fff; font: 13px system-ui, sans-serif; }
.mock-bar button, .mock-bar select { font: inherit; padding: 4px 10px; }
.mock-bar .mock-title { flex: 1; }
.screen { display: none; }
.screen.active { display: block; }
.screen.wide .task-screen { height: calc(100vh - 52px); }
body.nl .structured-only { display: none !important; }
body:not(.nl) .nl-only { display: none !important; }
body.study-tp [data-study="pe"], body.study-pe [data-study="tp"] { display: none !important; }
.mock-pick.on { outline: 3px solid #2f6b5e; }
"""
    js = """
(function () {
  var screens = Array.prototype.slice.call(document.querySelectorAll('.screen'));
  var idx = 0;
  function visible() {
    var tp = document.body.classList.contains('study-tp');
    return screens.filter(function (s) {
      var st = s.getAttribute('data-study');
      return !st || (st === 'tp') === tp;
    });
  }
  function show(i) {
    var list = visible();
    idx = Math.max(0, Math.min(i, list.length - 1));
    screens.forEach(function (s) { s.classList.remove('active'); });
    list[idx].classList.add('active');
    document.getElementById('mock-title').textContent =
      (idx + 1) + ' / ' + list.length + ' — ' + list[idx].getAttribute('data-title');
    location.hash = list[idx].id;
    window.scrollTo(0, 0);
    if (list[idx].id === 'waiting-room') {
      setTimeout(function () {
        var j = document.querySelector('.mock-join');
        if (j) { j.querySelector('.dot').classList.add('online'); j.querySelector('.status').textContent = 'connected'; }
      }, 3000);
    }
  }
  function current() { return visible()[idx]; }
  document.getElementById('mock-next').onclick = function () { show(idx + 1); };
  document.getElementById('mock-prev').onclick = function () { show(idx - 1); };
  document.getElementById('mock-mode').onchange = function (ev) {
    document.body.classList.toggle('nl', ev.target.value === 'nl');
  };
  document.getElementById('mock-study').onchange = function (ev) {
    var id = current().id;
    document.body.classList.toggle('study-tp', ev.target.value === 'tp');
    document.body.classList.toggle('study-pe', ev.target.value === 'pe');
    var list = visible(), k = list.findIndex(function (s) { return s.id === id; });
    show(k < 0 ? idx : k);
  };
  document.addEventListener('keydown', function (ev) {
    if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return;
    if (ev.key === 'ArrowRight') show(idx + 1);
    if (ev.key === 'ArrowLeft') show(idx - 1);
  });
  // BA-73: same crate-ask subset as the live classifier (crate three, #5,
  // bare "what about 3?", addressed "jt, crate 3") — statements like
  // "i have crate 3" do not fire. Mirrors engines.is_intel_ask.
  var NUM = '(?:[1-8]|one|two|three|four|five|six|seven|eight|1st|2nd|3rd|4th|5th|6th|7th|8th|first|second|third|fourth|fifth|sixth|seventh|eighth)';
  var NUMV = {'1':1,'2':2,'3':3,'4':4,'5':5,'6':6,'7':7,'8':8,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,'1st':1,'2nd':2,'3rd':3,'4th':4,'5th':5,'6th':6,'7th':7,'8th':8,first:1,second:2,third:3,fourth:4,fifth:5,sixth:6,seventh:7,eighth:8};
  var BARE_ASK = new RegExp('^\\\\s*(?:(?:please|pls|plz|hey|hi|ok|okay|so|and|the|on|about|for|of|what|how|any|anything|some|you|guys|team|got|have|is|there|send|share|check|news|updates?|info|intel|me)\\\\b[\\\\s,]*)*(?:crate\\\\s*(?:number|no\\\\.?|#)?\\\\s*|#\\\\s*)?(' + NUM + ')(?:\\\\s+one)?\\\\s*(?:(?:please|pls|plz|thanks|thank you|ty|one|crates?)\\\\b[\\\\s,]*)*\\\\??\\\\s*$', 'i');
  function mockCrate(sent) {
    // Crate extraction runs on the raw text; the first-person clause is
    // stripped only for the ask-word scan — "i have crate 3, do you?" still
    // resolves crate 3 and counts as an ask (mirrors engines.is_intel_ask).
    var scanned = sent.replace(/\\b\\d{1,2}(?::\\d{2})?\\s*pm\\b/gi, ' ')
      .replace(/^\\s*(?:(?:jt|pm)\\b[\\s,]*)+/i, '');
    var askScanned = scanned.replace(/\\b(?:i|we)\\s+(?:have|got|found|see|saw)\\b[^,.?!:;]*/gi, ' ');
    var m = new RegExp('crate\\\\s*(?:number|no\\\\.?|#)?\\\\s*(' + NUM + ')\\\\b', 'i').exec(scanned)
      || /#\\s*([1-8])\\b/i.exec(scanned);
    if (m && (/\\b(?:where|what|which|whats|what's|anything|any|got|have|newer|new|recent|latest|info|intel|update|news|send|share|pass|check|see|hear|know|find|track)\\b|\\?/i.test(askScanned) || BARE_ASK.test(scanned))) return NUMV[m[1].toLowerCase()];
    var b = BARE_ASK.exec(scanned);
    return b ? NUMV[b[1].toLowerCase()] : null;
  }
  function say(list, who, text, mine) {
    var li = document.createElement('li');
    li.className = mine ? 'msg mine' : 'msg';
    li.innerHTML = '<span class="who"></span><span class="msg-body"></span>';
    li.children[0].textContent = who; li.children[1].textContent = text;
    list.appendChild(li); list.scrollTop = list.scrollHeight;
    return li;
  }
  // UX round 4, item 9: a shared record is its own card — a sibling of the
  // message bubble, never a pill embedded inside it.
  function sayIntel(list, who, text, stamp, bodyText) {
    say(list, who, text, false);
    var li = document.createElement('li');
    li.className = 'intel-card';
    var pill = document.createElement('span');
    pill.className = 'filepill';
    var fs = document.createElement('span'); fs.className = 'fs'; fs.textContent = stamp;
    var src = document.createElement('span'); src.className = 'file-source'; src.textContent = ' — in your case file';
    pill.appendChild(fs); pill.appendChild(document.createTextNode(bodyText)); pill.appendChild(src);
    li.appendChild(pill); list.appendChild(li); list.scrollTop = list.scrollHeight;
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('button');
    if (!b) return;
    var sec = b.closest('section.pane, section, main');
    if (b.classList.contains('mock-next')) { show(idx + 1); return; }
    if (b.classList.contains('mock-back')) { show(idx - 1); return; }
    if (b.classList.contains('mock-share')) {
      var tag = document.createElement('span'); tag.className = 'shared-tag'; tag.textContent = 'Shared in team chat';
      b.replaceWith(tag); return;
    }
    if (b.classList.contains('mock-pick')) {
      var g = b.getAttribute('data-group');
      document.querySelectorAll('.mock-pick[data-group="' + g + '"]').forEach(function (x) { x.classList.remove('on'); });
      b.classList.add('on'); return;
    }
    // BA-73: the chat list can live in a *sibling* section of the controls
    // (the practice screen nests it under "Team chat" while Ask/Send sit under
    // "Ask the team") — resolve it at screen scope, not button-section scope.
    var scr = b.closest('.screen') || document;
    var chat = scr.querySelector('.mock-chat');
    function fileCard(who, crateLabel) {
      // A teammate share auto-files the record (O-4) — the mock claims the
      // card lands, so the mock must actually land it.
      var ul = scr.querySelector('.files-list'); if (!ul) return;
      var li = document.createElement('li'); li.className = 'file from-team';
      li.innerHTML = '<div class="file-main"><div class="file-head"><span class="file-stamp">Just now</span>'
        + '<span class="file-source">Shared by ' + who + '</span></div>'
        + '<div class="file-body"></div></div>';
      li.querySelector('.file-body').textContent = 'Record card on ' + crateLabel + ' — shared in team chat.';
      ul.appendChild(li); ul.scrollTop = ul.scrollHeight;
      var cnt = scr.querySelector('.pane-head .count');
      if (cnt && /entries?$/.test(cnt.textContent)) {
        var n = parseInt(cnt.textContent, 10); if (!isNaN(n)) cnt.textContent = (n + 1) + ' entries';
      }
    }
    if (b.classList.contains('mock-greet') && chat) {
      say(chat, 'AB (you)', b.textContent, true); b.parentNode.remove(); return;
    }
    if (b.classList.contains('mock-ask-btn') && chat) {
      var s = sec.querySelector('.mock-ask'); if (!s.value) return;
      var asked = s.value;
      say(chat, 'AB (you)', 'Requesting intel on ' + asked + '.', true);
      // BA-55: per-seat outcomes — the holder shares a card, the other seat
      // reports nothing newer, just like the real broadcast.
      setTimeout(function () { sayIntel(chat, 'JT', 'Sharing intel on ' + asked, 'Just now', 'Record card on ' + asked + ' — shared in team chat.'); fileCard('JT', asked); }, 900);
      setTimeout(function () { say(chat, 'PM', 'Nothing newer on ' + asked + ' on my end.'); }, 1900);
      s.value = ''; return;
    }
    if (b.classList.contains('mock-send') && chat) {
      var t = b.parentNode.querySelector('.mock-text'); if (!t.value.trim()) return;
      var sent = t.value;
      say(chat, 'AB (you)', sent, true); t.value = '';
      var m = mockCrate(sent);
      if (m !== null) {
        setTimeout(function () { sayIntel(chat, 'JT', 'Sharing intel on Crate ' + m, 'Just now', 'Record card on Crate ' + m + ' — shared in team chat.'); fileCard('JT', 'Crate ' + m); }, 900);
        setTimeout(function () { say(chat, 'PM', 'Nothing newer on Crate ' + m + ' on my end.'); }, 1900);
      } else {
        setTimeout(function () { say(chat, 'PM', b.getAttribute('data-reply')); }, 900);
      }
      return;
    }
    var ai = scr.querySelector('.mock-ai');
    if (b.classList.contains('mock-assist') && ai) {
      var q = b.parentNode.querySelector('.mock-text'), c = sec.querySelector('.mock-ask');
      var crate = document.body.classList.contains('nl') ? '' : (c && c.value);
      var text = q.value.trim() || (crate ? 'What does my file say about ' + crate + '?' : '');
      if (!text) return;
      say(ai, 'AB (you)', text, true); q.value = ''; if (c) c.value = '';
      var wait = say(ai, 'Assistant', 'Generating a response…'); wait.classList.add('thinking');
      setTimeout(function () { wait.remove(); say(ai, 'Assistant', b.getAttribute('data-reply')); }, 1500);
    }
  });
  var start = screens.findIndex(function (s) { return '#' + s.id === location.hash; });
  show(start < 0 ? 0 : start);
})();
"""
    return f"""<!doctype html>
<!-- GENERATED by scripts/build_clickthrough.py for build {sha}.
     Click-through mock only: no network access, no live agents, canned replies.
     Testers: edit any screen below (look for EDIT-ME) and send the file back with your notes. -->
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="investigators-build" content="{sha}">
<title>Investigators — click-through mock ({sha[:12]})</title>
<style>
{css}
{nav_css}
</style></head>
<body class="study-tp">
<div class="mock-bar" role="navigation" aria-label="Mock navigation">
<button id="mock-prev" type="button">◀ Back</button><button id="mock-next" type="button">Next ▶</button>
<span class="mock-title" id="mock-title"></span>
<label>Study <select id="mock-study"><option value="tp">Time pressure</option><option value="pe">Peer evaluation</option></select></label>
<label>Interaction <select id="mock-mode"><option value="structured">Structured + natural language</option><option value="nl">Natural language only</option></select></label>
<span title="source-bundle SHA-256">build {sha[:12]}</span>
</div>
{chr(10).join(screens)}
<script>
{js}
</script>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the mock is stale")
    args = parser.parse_args()
    text = render()
    rel = OUT.relative_to(ROOT)
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print(f"FAIL: {rel} is stale; run scripts/build_clickthrough.py")
            return 1
        print(f"OK: {rel} current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
