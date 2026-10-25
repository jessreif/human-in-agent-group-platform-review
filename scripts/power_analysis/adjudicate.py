"""Human adjudication of DV1 (teammate requests) and DV2 (assistant questions).

The platform classifiers (teammate_requested, wants_intel_ask) are candidate generators,
not ground truth. Every participant team-channel message and every assistant entry gets a
human verdict keyed by (session_id, seq). analyze.py refuses to run while any in-window
item lacks a verdict.

  python3 adjudicate.py worksheet --data raw_sessions.json --adj adjudication.json \
      --out worksheet.csv          # rows still needing a verdict (blind to arm)
  # fill the `verdict` column (and optionally `borderline`, `note`) in worksheet.csv
  python3 adjudicate.py merge --adj adjudication.json --sheet worksheet.csv --coder NAME
  python3 adjudicate.py status --data raw_sessions.json --adj adjudication.json

Verdicts:
  team channel:      request | not_request
  assistant channel: question | not_question
`borderline` = 1 flags a judgment call. Strict-coding sensitivity drops those rows.
Coding rules: PLAYBOOK §5.
"""

import argparse
import csv
import datetime as dt
import json
import sys

TEAM = {"request", "not_request"}
ASST = {"question", "not_question"}


def load_adj(path):
    try:
        return json.load(open(path))
    except FileNotFoundError:
        return {"rules_version": "playbook-v1", "entries": []}


def key(e):
    return (e["session_id"], e["channel"], int(e["seq"]))


def items(raw):
    for x in raw:
        if x["stage"] != "completion":
            continue
        op, rv, end = x["marks"]["checkin_open"], x["marks"]["checkin_reveal"], x["end_seq"]
        for ch, rows in (("team", x["team_msgs"]), ("assistant", x["assistant_q"])):
            for m in rows:
                s = m["seq"]
                win = ("pre" if s < op else "post" if rv < s <= (end or 10**9)
                       else "checkin" if s < rv else "after")
                yield {
                    "session_id": x["session_id"], "sid": x["sid"], "round": x["round"],
                    "channel": ch, "seq": s, "window": win, "body": m["body"] or "",
                    "logged": int(m.get("logged", False)), "reparse": int(m.get("reparse", False)),
                }


def cmd_worksheet(a):
    raw, adj = json.load(open(a.data)), load_adj(a.adj)
    done = {key(e) for e in adj["entries"]}
    todo = [i for i in items(raw) if key(i) not in done]
    cols = ["session_id", "sid", "round", "channel", "seq", "window", "logged", "reparse",
            "body", "verdict", "borderline", "note"]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i in todo:  # arm deliberately omitted — code blind to condition
            w.writerow({**i, "verdict": "", "borderline": "", "note": ""})
    print(f"{len(todo)} uncoded rows -> {a.out}")


def cmd_merge(a):
    adj = load_adj(a.adj)
    have = {key(e): e for e in adj["entries"]}
    n = 0
    for r in csv.DictReader(open(a.sheet)):
        v = r["verdict"].strip()
        if not v:
            continue
        ok = TEAM if r["channel"] == "team" else ASST
        if v not in ok:
            sys.exit(f"bad verdict {v!r} for {r['sid']} seq {r['seq']} ({r['channel']})")
        e = {"session_id": r["session_id"], "sid": r["sid"], "channel": r["channel"],
             "seq": int(r["seq"]), "body": r["body"], "verdict": v,
             "borderline": r.get("borderline", "").strip() in ("1", "true", "yes"),
             "note": r.get("note", ""), "coder": a.coder,
             "coded_on": dt.date.today().isoformat()}
        have[key(e)] = e
        n += 1
    adj["entries"] = sorted(have.values(), key=lambda e: (e["session_id"], e["channel"], e["seq"]))
    json.dump(adj, open(a.adj, "w"), indent=1)
    print(f"merged {n} verdicts; {len(adj['entries'])} total in {a.adj}")


def cmd_status(a):
    raw, adj = json.load(open(a.data)), load_adj(a.adj)
    done = {key(e) for e in adj["entries"]}
    allr = list(items(raw))
    miss = [i for i in allr if key(i) not in done]
    print(f"{len(allr)} items, {len(allr) - len(miss)} coded, {len(miss)} uncoded")
    for i in miss[:20]:
        print(f"  {i['sid']} {i['channel']:9} seq {i['seq']:4} {i['window']:7} {i['body'][:70]!r}")
    sys.exit(1 if miss else 0)


p = argparse.ArgumentParser()
sp = p.add_subparsers(dest="cmd", required=True)
w = sp.add_parser("worksheet")
w.add_argument("--data", required=True)
w.add_argument("--adj", required=True)
w.add_argument("--out", required=True)
m = sp.add_parser("merge")
m.add_argument("--adj", required=True)
m.add_argument("--sheet", required=True)
m.add_argument("--coder", required=True)
s = sp.add_parser("status")
s.add_argument("--data", required=True)
s.add_argument("--adj", required=True)
a = p.parse_args()
{"worksheet": cmd_worksheet, "merge": cmd_merge, "status": cmd_status}[a.cmd](a)
