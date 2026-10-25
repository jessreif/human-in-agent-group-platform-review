"""Build features for ai_judgment.R: what predicts judging the teammates as automated (tp_post_10)?"""
import glob, os
import pandas as pd, numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
P = pd.read_csv(os.path.join(HERE, "participants.csv"))
rows = []
for folder in ["data/pilot1", "data/pilot2"]:
    m = pd.read_csv(sorted(glob.glob(os.path.join(ROOT, folder, "investigators_message_*")))[0])
    m = m[m.actor.isin(["JT", "PM"]) & (m.channel == "team")]
    for s, g in m.groupby("session_id"):
        b = g.body.fillna("").str.lower()
        rows.append(dict(session_id=s,
            ag_clarify=int(b.str.contains("which crate|which one|which crate do you mean|referring to").sum()),
            ag_share_dupes=int(g[b.str.startswith("sharing intel")].duplicated("body").sum()),
            ag_free_text=int((~b.str.match(r"(sharing intel|requesting intel|could use whatever|any updates on|do you have anything|what does your file|hi!|hey!|hello!)")).sum()),
            ag_disclosed=int(b.str.contains("automated").sum() > 0)))
A = pd.DataFrame(rows)
P = P.merge(A, on="session_id", how="left")
W = pd.concat([pd.read_csv(sorted(glob.glob(os.path.join(ROOT, f, "investigators_participant_wide_*")))[0]) for f in ["data/pilot1", "data/pilot2"]])
P = P.merge(W[["session_id", "teammate_fallback_replies", "teammate_generated_replies"]], on="session_id")
P["judged_ai"] = (P.tp_post_10 == "All were automated").astype(int)
P["judged_human_any"] = P.tp_post_10.isin(["All were human", "Some were human, some were automated"]).astype(int)
P.to_csv(os.path.join(HERE, "ai_judgment_data.csv"), index=False)

