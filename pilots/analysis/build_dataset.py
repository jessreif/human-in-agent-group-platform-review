"""Assemble analysis tables for the 2026-10-18 pilots.

S60 = study.live.v14 (overlap none/some x case file full/thin, no workload cue)
S30 = study.live.v15 (some overlap, thin file; workload cue high vs mixed)

Outputs (this folder):
  participants.csv   one row per completer (N=90)
  crates.csv         one row per assigned crate (4 per completer)
  scripted_requests.csv  the 5 scheduled teammate->participant requests per completer
  participant_requests.csv  platform-parsed participant->teammate requests
  funnel.csv         every session incl. non-completers
"""
import glob, json, os
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SAMPLES = [("S60", "data/pilot1"), ("S30", "data/pilot2")]


def load(folder, kind):
    # N=60 folder has byte-identical duplicate exports; take the first
    f = sorted(glob.glob(os.path.join(ROOT, folder, f"investigators_{kind}_*.csv")))[0]
    return pd.read_csv(f, low_memory=False)


coded = pd.read_csv(os.path.join(ROOT, "coding", "participant_messages_coded.csv"), dtype={"crates": str})
coded["crates"] = coded.crates.fillna("")
for t in ["request", "intel", "social", "other", "offer"]:
    coded[t] = coded.types.str.split("+").apply(lambda x: t in x)

P, C, SR, PR, F = [], [], [], [], []
for tag, folder in SAMPLES:
    w = load(folder, "participant_wide")
    F.append(w.assign(sample=tag)[["sample", "session_id", "manifest_id", "inv_condition", "stage", "terminal_reason"]])
    w = w[w.stage == "completion"].copy()
    comp = set(w.session_id)
    ev = load(folder, "event"); ev = ev[ev.session_id.isin(comp)]
    msg = load(folder, "message"); msg = msg[msg.session_id.isin(comp)]
    sh = load(folder, "share"); sh = sh[sh.session_id.isin(comp)]
    ep = load(folder, "request_episode"); ep = ep[ep.session_id.isin(comp)]
    cl = load(folder, "crate_long"); cl = cl[cl.session_id.isin(comp) & cl.assigned]
    ir = load(folder, "item_response"); ir = ir[ir.session_id.isin(comp)]

    ev["t"] = pd.to_datetime(ev.server_time, utc=True)
    msg["t"] = pd.to_datetime(msg.created_at, utc=True)
    start = ev[ev.type == "task_clock_started"].groupby("session_id").t.min()
    # active-ms clock for each committed message (message.accepted_seq == event.seq);
    # message_accepted rows carry no active_ms, so carry forward the last clocked event
    ev = ev.sort_values(["session_id", "seq"])
    ev["active_ffill"] = ev.groupby("session_id").active_ms.ffill()
    acc = ev[ev.type == "message_accepted"][["session_id", "seq", "active_ffill"]].rename(columns={"active_ffill": "active_ms"})
    msg = msg.merge(acc.rename(columns={"seq": "accepted_seq", "active_ms": "msg_active_ms"}),
                    on=["session_id", "accepted_seq"], how="left")

    # ---- seat workload levels (S30 only)
    lv = {}
    for _, r in ev[ev.type == "checkin_reveal"].iterrows():
        a = json.loads(r.payload)["assigned_values"]
        lv[r.session_id] = a

    # ---- participant messages, joined to hand codes
    pm = msg[(msg.actor == "participant") & (msg.channel == "team")]
    pm = pm.merge(coded[["message_id", "types", "crates", "request", "intel", "social", "other", "offer"]],
                  on="message_id", how="left")
    assert pm.types.notna().all()

    # ---- scripted (teammate -> participant) requests
    sched = ev[ev.type == "scripted_request_delivered"].copy()
    sched["request_id"] = sched.payload.apply(lambda s: json.loads(s)["request_id"])
    sched["crate"] = sched.payload.apply(lambda s: json.loads(s)["crate"])
    sched["index"] = sched.payload.apply(lambda s: json.loads(s)["index"])
    req_msg = msg[msg.request_id.notna() & msg.actor.isin(["JT", "PM"])].drop_duplicates("request_id")
    sched = sched.merge(req_msg[["request_id", "actor", "body"]].rename(columns={"actor": "requester"}),
                        on="request_id", how="left")
    sched = sched.merge(ep[["request_id", "status", "close_reason"]], on="request_id", how="left")
    psh = sh[sh.sharer == "participant"]
    filled = set(psh.prompted_by_request_id.dropna())
    sched["fulfilled"] = sched.request_id.isin(filled)
    sched["sample"] = tag
    sched["requester_level"] = [lv.get(s, {}).get(a, np.nan) for s, a in zip(sched.session_id, sched.requester)]
    SR.append(sched[["sample", "session_id", "request_id", "index", "crate", "requester", "requester_level",
                     "close_reason", "fulfilled"]])

    # ---- participant -> teammate requests as parsed by the platform
    tr = ev[ev.type == "teammate_requested"].copy()
    tr["crate"] = tr.payload.apply(lambda s: json.loads(s).get("crate"))
    tr["responder"] = tr.payload.apply(lambda s: json.loads(s).get("responder"))
    tr["responder_level"] = [lv.get(s, {}).get(a, np.nan) for s, a in zip(tr.session_id, tr.responder)]
    tr["after_checkin"] = tr.active_ms >= 60000
    resp = ev[ev.type == "structured_response_delivered"].payload.apply(lambda s: json.loads(s)["request_id"])
    # parsed request ids are not in the event payload; use agent shares tied back to requests instead
    tr["sample"] = tag
    PR.append(tr[["sample", "session_id", "active_ms", "crate", "responder", "responder_level", "after_checkin"]])

    # ---- post-task items
    ir["value"] = ir.response.apply(lambda s: json.loads(s).get("value") if isinstance(s, str) and s.startswith("{") else np.nan)
    items = ir[ir.instrument_id == "post_task.tp_demo"].pivot(index="session_id", columns="item_id", values="value")
    oe = ir[ir.instrument_id == "open_ended.tp_followup"].pivot(index="session_id", columns="item_id", values="response1")

    for _, r in w.iterrows():
        s = r.session_id
        m = pm[pm.session_id == s]
        reqm = m[m.request]
        req_crates = set(c for x in reqm.crates for c in x.split(",") if c)
        mine = sched[sched.session_id == s]
        a = cl[cl.session_id == s]
        agent_sh = sh[(sh.session_id == s) & sh.sharer.isin(["JT", "PM"])]
        agent_msgs = msg[(msg.session_id == s) & msg.actor.isin(["JT", "PM"])]
        L = lv.get(s, {})
        man = r.manifest_id
        row = dict(
            sample=tag, session_id=s, manifest=man,
            overlap="some" if "ovsome" in man else "none",
            casefile="thin" if "thin" in man else "full",
            workload={"nopress": "none"}.get(r.inv_condition, r.inv_condition),
            cell=f"{'some' if 'ovsome' in man else 'none'}_{'thin' if 'thin' in man else 'full'}",
            JT_level=L.get("JT"), PM_level=L.get("PM"),
            light_seat=(min(L, key=L.get) if L and min(L.values()) <= 3 else None),
            own_workload=r.inv_own_pressure,
            # chat behaviour (hand-coded)
            n_msgs=len(m), n_req_msgs=int(m.request.sum()), any_req=int(m.request.any()),
            n_req_crates=len(req_crates), n_offer=int(m.offer.sum()), n_intel_typed=int(m.intel.sum()),
            n_social=int(m.social.sum()), n_other=int(m.other.sum()),
            first_req_s=(reqm.msg_active_ms.min() / 1000 if len(reqm) else np.nan),
            n_req_msgs_post60=int((reqm.msg_active_ms >= 60000).sum()),
            n_req_msgs_pre60=int((reqm.msg_active_ms < 60000).sum()),
            # platform-parsed requests
            n_parsed_req=int(((ev.session_id == s) & (ev.type == "teammate_requested")).sum()),
            # sharing
            n_shares=int((psh.session_id == s).sum()),
            n_shares_prompted=int(((psh.session_id == s) & psh.prompted_by_request_id.notna()).sum()),
            n_shares_proactive=int(((psh.session_id == s) & psh.prompted_by_request_id.isna()).sum()),
            n_sched_req=len(mine), n_sched_fulfilled=int(mine.fulfilled.sum()),
            # agent side
            n_agent_shares=len(agent_sh), n_agent_msgs=len(agent_msgs),
            # performance
            n_answered=int(a.final_answer.notna().sum()),
            correct_total=r.inv_correct_total, correct_own=r.inv_correct_ownfile, correct_team=r.inv_correct_teamdep,
            conf_mean=a.confidence_value.mean(),
            # file engagement
            file_viewed=r.file_viewed_count, file_coverage=r.file_coverage_frac,
            file_dwell_s=r.file_dwell_ms_total / 1000, own_records_in_file=a.n_in_file.mean(),
            attn_hidden_s=r.attention_hidden_ms_total / 1000,
        )
        for it in items.columns:
            row[it] = items.loc[s, it] if s in items.index else np.nan
        for it in oe.columns:
            row["oe_" + it] = oe.loc[s, it] if s in oe.index else None
        P.append(row)

    # ---- crate level
    ash = sh[sh.sharer.isin(["JT", "PM"])].copy()
    ash["crate"] = ash.record_id.str.extract(r"C(\d+)-")[0].astype(int)
    got = set(zip(ash.session_id, ash.crate))
    for _, r in cl.iterrows():
        m = pm[pm.session_id == r.session_id]
        req = set(int(c) for x in m[m.request].crates for c in x.split(",") if c)
        C.append(dict(sample=tag, session_id=r.session_id, crate=r.crate, role=r.role,
                      correct=r.correct, answered=pd.notna(r.final_answer), confidence=r.confidence_value,
                      n_in_file=r.n_in_file, n_records=r.n_records,
                      requested=int(r.crate in req), received_agent_share=int((r.session_id, r.crate) in got)))

P = pd.DataFrame(P); C = pd.DataFrame(C)
C = C.merge(P[["session_id", "overlap", "casefile", "workload", "cell"]], on="session_id")
SR = pd.concat(SR).merge(P[["session_id", "workload", "cell", "light_seat"]], on="session_id")
PR = pd.concat(PR).merge(P[["session_id", "workload", "cell", "light_seat"]], on="session_id")
F = pd.concat(F)
P.to_csv(os.path.join(HERE, "participants.csv"), index=False)
C.to_csv(os.path.join(HERE, "crates.csv"), index=False)
SR.to_csv(os.path.join(HERE, "scripted_requests.csv"), index=False)
PR.to_csv(os.path.join(HERE, "participant_requests.csv"), index=False)
F.to_csv(os.path.join(HERE, "funnel.csv"), index=False)
print(P.groupby(["sample", "cell", "workload"]).size())
print(P.describe().T.round(2).to_string())
