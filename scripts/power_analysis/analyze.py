"""Pre-registered DV1/DV2/DV3 models + effect-size precision + power simulation.

Host-side (needs numpy, scipy, statsmodels, pandas). From the run's evidence directory:

  python3 ./scripts/power_analysis/analyze.py \
      --data raw_sessions.json --adj adjudication.json \
      --config ./scripts/power_analysis/power_config.json \
      [--rounds study.live.v11,study.live.v12] [--quick]

Writes results.md, participant_measures.csv, crate_measures.csv next to --data.
Refuses to run while any in-window message lacks a human verdict (adjudicate.py status).
The participant is the unit for H1/H2. H3 is crate-level with participant-clustered SEs.
Method and interpretation rules: INVESTIGATORS_TEAM_EXPERIMENT_POWER_ANALYSIS_PLAYBOOK.md.
"""

import argparse
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import norm

from conditions import require_single_condition

warnings.filterwarnings("ignore")

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True)
ap.add_argument("--adj", required=True)
ap.add_argument("--config", required=True)
ap.add_argument("--rounds", default="", help="comma list; default = every round in --data")
ap.add_argument("--quick", action="store_true", help="1/4 the simulation reps (drafting only)")
args = ap.parse_args()
cfg = json.load(open(args.config))
rng = np.random.default_rng(cfg["seed"])
outdir = os.path.dirname(os.path.abspath(args.data))
ALPHA = float(cfg.get("alpha_level", 0.05))
REPS = {k: max(50, v // 4) if args.quick else v for k, v in cfg["reps"].items()}
lines = []


def out(s=""):
    lines.append(str(s))


def h(t):
    out(f"\n## {t}\n")


def table(df):
    out("```")
    out(df.to_string())
    out("```")


raw = json.load(open(args.data))
if args.rounds:
    keep = set(args.rounds.split(","))
    raw = [x for x in raw if x["round"] in keep]
unknown = sorted({x["round"] for x in raw} - set(cfg["eras"]))
if unknown:
    sys.exit(f"rounds missing from power_config.json eras: {unknown}")

adj = json.load(open(args.adj))
V = {(e["session_id"], e["channel"], int(e["seq"])): e for e in adj["entries"]}

# ---- funnel (all stages) -----------------------------------------------------------------
fun = pd.DataFrame([{"round": x["round"], "arm": x["arm"] or "(pre-alloc)", "stage": x["stage"]}
                    for x in raw])

# ---- participant + crate tables ----------------------------------------------------------
P, CR, missing = [], [], []
for x in raw:
    if x["stage"] != "completion":
        continue
    op, rv, end = x["marks"]["checkin_open"], x["marks"]["checkin_reveal"], x["end_seq"]
    if None in (op, rv, end):
        sys.exit(f"{x['sid']}: missing checkin/deadline marks {x['marks']}")

    def win(s):
        return "pre" if s < op else "post" if rv < s <= end else "other"

    def verdict(ch, m):
        e = V.get((x["session_id"], ch, int(m["seq"])))
        if e is None:
            missing.append((x["sid"], ch, m["seq"], m["body"]))
        return e

    t_req, t_req_strict, t_logged, t_pre = 0, 0, 0, 0
    for m in x["team_msgs"]:
        e = verdict("team", m)
        w = win(m["seq"])
        is_req = bool(e and e["verdict"] == "request")
        if w == "post":
            t_req += is_req
            t_req_strict += is_req and not e["borderline"]
            t_logged += m["logged"]
        elif w == "pre":
            t_pre += is_req
    t_req += sum(win(r["seq"]) == "post" for r in x["menu_requests"])  # menu asks count
    t_pre += sum(win(r["seq"]) == "pre" for r in x["menu_requests"])
    a_q, a_q_strict, a_pre = 0, 0, 0
    for m in x["assistant_q"]:
        e = verdict("assistant", m)
        w = win(m["seq"])
        is_q = bool(e and e["verdict"] == "question")
        if w == "post":
            a_q += 1  # pre-registered DV2 = every assistant entry in the window
            a_q_strict += is_q and not e["borderline"]
        elif w == "pre":
            a_pre += 1
    it = x.get("items", {})

    def num(k):
        try:
            return float(it[k])
        except (KeyError, TypeError, ValueError):
            return np.nan

    row = {"sid": x["sid"], "round": x["round"], "era": cfg["eras"][x["round"]],
           "manifest": x["manifest"], "high": int(x["arm"] == "high"),
           # Audit G02: administered condition factors, exported by
           # extract_measures.py — legacy defaults for pre-factor extracts.
           "crate_overlap": x.get("crate_overlap") or "independent",
           "n_crates": int(x.get("n_crates") or 8),
           "intel_budget": x.get("intel_budget"),
           "own_rating": x["own_rating"], "dv1": t_req, "dv1_strict": t_req_strict,
           "dv1_logged": t_logged, "pre_req": t_pre, "dv2": a_q, "dv2_strict": a_q_strict,
           "pre_asst": a_pre, "tp05": num("tp_post_05"), "tp11": num("tp_post_11"),
           "tp10": it.get("tp_post_10"), "data_use": x.get("data_use")}
    P.append(row)
    for pc in x["per_crate"] or []:
        if pc["role"] not in ("own", "team"):
            continue  # 'other' = unassigned crate, not one of the participant's four
        CR.append({"sid": x["sid"], "high": row["high"], "era": row["era"],
                   "crate_overlap": row["crate_overlap"], "n_crates": row["n_crates"],
                   "dep": 0.5 if pc["role"] == "team" else -0.5,
                   "correct": int(pc["correct"]), "answered": int(pc["answered"]),
                   "pre_req": t_pre, "pre_asst": a_pre, "own_rating": row["own_rating"]})
if missing:
    print(f"{len(missing)} messages lack a verdict — run adjudicate.py worksheet first:")
    for m in missing[:15]:
        print("  ", m)
    sys.exit(2)
P, CR = pd.DataFrame(P), pd.DataFrame(CR)
# Audit G02: the crate-condition experiment adds between-subject factors.
# The existing models and simulations support one factor cell only.
# A declaration of cells is not an implementation of condition-aware models.
try:
    _condition_cells = require_single_condition(P.to_dict("records"))
except ValueError as exc:
    sys.exit(str(exc))
P.to_csv(os.path.join(outdir, "participant_measures.csv"), index=False)
CR.to_csv(os.path.join(outdir, "crate_measures.csv"), index=False)
bad_crates = P.sid[~P.sid.isin(CR.sid.unique())].tolist() + [
    s for s, n in CR.groupby("sid").size().items() if n != 4]
if bad_crates:
    sys.exit(f"sessions without exactly 4 assigned crates: {bad_crates}")

out("# Power-analysis run — results")
out(f"\nData `{args.data}` · rounds: {args.rounds or 'all'} · reps {REPS}"
    f"{' (QUICK — drafting only)' if args.quick else ''}")

h("1. Funnel (all live sessions in scope)")
table(pd.crosstab([fun["round"], fun.arm], fun.stage, margins=True))
h("2. Analytic sample (completers)")
table(pd.crosstab([P.era, P["round"]], P.high.map({0: "low", 1: "high"}), margins=True))
nu = P.data_use.value_counts(dropna=False).to_dict()
out(f"\ndata_use choices: {nu}  (non-`use_all` completers must be removed before confirmatory use)")

h("3. Participant-level descriptives (high=1)")
cols = ["dv1", "dv1_strict", "dv1_logged", "dv2", "dv2_strict", "pre_req", "pre_asst",
        "own_rating", "tp05", "tp11"]
table(P.groupby("high")[cols].agg(["mean", "std", "count"]).round(2).T)
for dv in ("dv1", "dv2"):
    for arm in (0, 1):
        v = P[P.high == arm][dv]
        out(f"- {dv} {'high' if arm else 'low '}: {sorted(v.tolist())}  zeros {int((v == 0).sum())}/{len(v)}"
            f"  var/mean {v.var() / v.mean() if v.mean() else float('nan'):.2f}")
out("\ntp_post_10 (teammate nature) by arm:")
table(pd.crosstab(P.tp10, P.high.map({0: "low", 1: "high"})))

h("4. Crate-level accuracy by arm × dependence (descriptive; crates cluster in people)")
cell = CR.groupby(["high", "dep"])[["correct", "answered"]].mean().round(3)
table(cell)
lo_hd = float(CR[(CR.high == 0) & (CR.dep == 0.5)].correct.mean())
out(f"\nFloor bound: if high pressure leaves low-dependence accuracy unchanged, the most "
    f"negative attainable H3 interaction ≈ −{lo_hd:.3f} (= low-arm high-dependence accuracy).")


def nb(formula, df):
    try:
        return smf.negativebinomial(formula, df).fit(disp=0, maxiter=500), "NB2"
    except Exception:
        return smf.poisson(formula, df).fit(disp=0), "Poisson"


def covs(df, base):
    used = [c for c in ("pre_req", "pre_asst", "own_rating") if df[c].nunique() > 1]
    dropped = [c for c in ("pre_req", "pre_asst", "own_rating") if c not in used]
    return base + "".join(f" + {c}" for c in used), dropped


h("5. H1 / H2 — negative binomial (pre-registered; H1 expects IRR<1, H2 IRR>1)")
fit = {}
for dv in ("dv1", "dv2"):
    for label in ("no covariates", "prereg covariates"):
        f, dropped = (f"{dv} ~ high", []) if label == "no covariates" else covs(P, f"{dv} ~ high")
        m, kind = nb(f, P)
        lo, hi = m.conf_int().loc["high"]
        a = float(m.params.get("alpha", 0.0))
        out(f"- **{dv} {label}** [{kind}] IRR **{np.exp(m.params['high']):.2f}** "
            f"95% CI [{np.exp(lo):.2f}, {np.exp(hi):.2f}], p={m.pvalues['high']:.3f}, "
            f"alpha={a:.2f} — `{f}`" + (f" (dropped, zero variance: {dropped})" if dropped else ""))
        fit[(dv, label)] = m
    for alt in ([f"{dv}_strict"] + (["dv1_logged"] if dv == "dv1" else [])):
        m, _ = nb(f"{alt} ~ high", P)
        out(f"  - sensitivity `{alt}`: IRR {np.exp(m.params['high']):.2f}, p={m.pvalues['high']:.3f}")
    if P.era.nunique() > 1:
        m, _ = nb(f"{dv} ~ high + C(era)", P)
        out(f"  - era fixed effects: IRR {np.exp(m.params['high']):.2f}, p={m.pvalues['high']:.3f}")
    sub = P[P.era == cfg["prereg_era"]]
    if len(sub):
        out(f"  - pre-registration-era only (n={len(sub)}): mean low "
            f"{sub[sub.high == 0][dv].mean():.2f} vs high {sub[sub.high == 1][dv].mean():.2f}")

h("6. H3 — LPM with participant-clustered SEs (pre-registered; H3 expects high:dep < 0)")
for label in ("no covariates", "prereg covariates"):
    f, dropped = ("correct ~ high * dep", []) if label == "no covariates" else \
        covs(CR, "correct ~ high * dep")
    m = smf.ols(f, CR).fit(cov_type="cluster", cov_kwds={"groups": CR.sid})
    for k in ("high", "dep", "high:dep"):
        lo, hi = m.conf_int().loc[k]
        out(f"- {label:18} `{k:8}` b={m.params[k]:+.3f} SE={m.bse[k]:.3f} "
            f"95% CI [{lo:+.3f}, {hi:+.3f}] p={m.pvalues[k]:.3f}")
    if dropped:
        out(f"  (dropped, zero variance: {dropped})")
try:
    m = smf.logit("correct ~ high * dep", CR).fit(disp=0, cov_type="cluster",
                                                 cov_kwds={"groups": CR.sid})
    out(f"- logit robustness `high:dep` b={m.params['high:dep']:+.3f} p={m.pvalues['high:dep']:.3f}")
except Exception as exc:
    out(f"- logit robustness not estimable ({type(exc).__name__}: {exc}) — expected near a floor")

h("7. Manipulation checks")
for k, lab in (("tp11", "tp_post_11 teammates seemed more rushed (v5 instrument only)"),
               ("tp05", "tp_post_05 asking would take teammates' time")):
    s = P.dropna(subset=[k])
    if s.high.nunique() == 2:
        a, b = s[s.high == 0][k], s[s.high == 1][k]
        sd = np.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
        out(f"- {lab}: low {a.mean():.2f} (n={len(a)}) vs high {b.mean():.2f} (n={len(b)}); "
            f"d={(b.mean() - a.mean()) / sd if sd else float('nan'):.2f}")

# ---- simulation --------------------------------------------------------------------------
mu = {dv: (P[P.high == 0][dv].mean(), P[P.high == 1][dv].mean()) for dv in ("dv1", "dv2")}
alpha = {dv: max(0.0, float(fit[(dv, "no covariates")].params.get("alpha", 0.0)))
         for dv in ("dv1", "dv2")}
icc_m = sm.MixedLM.from_formula("correct ~ high*dep", groups="sid", data=CR).fit()
var_u = float(icc_m.cov_re.iloc[0, 0])
icc = var_u / (var_u + icc_m.scale) if var_u > 0 else 0.0
pilot_cells = {(int(k[0]), float(k[1])): float(v)
               for k, v in CR.groupby(["high", "dep"]).correct.mean().items()}

h("8. Simulation inputs (nuisance parameters from this pilot)")
for dv in mu:
    out(f"- {dv}: mean low {mu[dv][0]:.2f}, high {mu[dv][1]:.2f}; NB alpha {alpha[dv]:.2f}")
out(f"- crate cells {pilot_cells}; latent ICC ≈ {icc:.2f}")


def draw_nb(mean, a, n):
    if a <= 1e-6:
        return rng.poisson(mean, n)
    r = 1.0 / a
    return rng.negative_binomial(r, r / (r + mean), n)


def sim_count(N, base, irr, a, reps):
    n, hits, ses, ests = N // 2, 0, [], []
    X = sm.add_constant(np.r_[np.zeros(n), np.ones(n)])
    for _ in range(reps):
        y = np.r_[draw_nb(base, a, n), draw_nb(base * irr, a, n)]
        if y.sum() == 0:
            continue
        try:
            f = sm.NegativeBinomial(y, X).fit(disp=0, maxiter=200)
            b, se, p = f.params[1], f.bse[1], f.pvalues[1]
        except Exception:
            continue
        if not np.isfinite(se):
            continue
        ses.append(se)
        ests.append(b)
        hits += (p < ALPHA) and np.sign(b) == np.sign(np.log(irr))
    return hits / reps, float(np.median(ses)), np.array(ests)


def sim_h3(N, cells, icc_, reps):
    # Power counts only significant estimates in H3's direction (interaction < 0).
    n, hits, ses, ests = N // 2, 0, [], []
    sd_u = np.sqrt(icc_ / (1 - icc_)) if 0 < icc_ < 1 else 0.0
    for _ in range(reps):
        rows = []
        for hi in (0, 1):
            u = rng.normal(0, sd_u, n)
            for i in range(n):
                for dep in (-0.5, 0.5):
                    z = norm.ppf(np.clip(cells[(hi, dep)], 1e-3, 1 - 1e-3)) + u[i]
                    for _k in range(2):  # two crates per dependence level
                        rows.append((f"{hi}-{i}", hi, dep, int(rng.random() < norm.cdf(z))))
        d = pd.DataFrame(rows, columns=["sid", "high", "dep", "correct"])
        try:
            f = smf.ols("correct ~ high*dep", d).fit(cov_type="cluster",
                                                      cov_kwds={"groups": d.sid})
        except Exception:
            continue
        b, se, p = f.params["high:dep"], f.bse["high:dep"], f.pvalues["high:dep"]
        ses.append(se)
        ests.append(b)
        hits += (p < ALPHA) and b < 0
    return hits / reps, float(np.median(ses)), np.array(ests)


h("9. Precision — how tightly would a study of size N estimate the effect if the pilot "
  "estimate were true?")
out("| quantity | N | 95% CI width | 2.5–97.5th pct of estimates |\n|---|---|---|---|")
for dv in ("dv1", "dv2"):
    irr = mu[dv][1] / mu[dv][0] if mu[dv][0] else float("nan")
    for N in cfg["precision_n"]:
        _, se, ests = sim_count(N, mu[dv][0], irr, alpha[dv], REPS["precision"])
        q = np.exp(np.percentile(ests, [2.5, 97.5]))
        out(f"| {dv} IRR (pilot {irr:.2f}) | {N} | ×/÷ {np.exp(1.96 * se):.2f} | [{q[0]:.2f}, {q[1]:.2f}] |")
for N in cfg["precision_n"]:
    _, se, ests = sim_h3(N, pilot_cells, icc, REPS["h3"])
    q = np.percentile(ests, [2.5, 97.5])
    out(f"| H3 interaction (pilot) | {N} | ± {1.96 * se:.3f} | [{q[0]:+.3f}, {q[1]:+.3f}] |")


def first_ok(row):
    ok = [N for N, pw in row if pw >= 0.8]
    return f"≈{ok[0]}" if ok else f">{cfg['n_grid'][-1]}"


h(f"10. Power for smallest effects of interest (α={ALPHA:g} two-sided, correct sign; total N)")
out("Nuisance parameters from §8. The alpha=1.0 column checks the case where the pilot "
    "under-estimates overdispersion.\n")
out("| test | effect | dispersion | " + " | ".join(f"N={N}" for N in cfg["n_grid"])
    + " | N for 80% |\n|" + "---|" * (len(cfg["n_grid"]) + 4))
for dv, grid in (("dv1", cfg["sesoi"]["dv1_irr"]), ("dv2", cfg["sesoi"]["dv2_irr"])):
    for irr in grid:
        for a in sorted({alpha[dv], 1.0}):
            row = [(N, sim_count(N, mu[dv][0], irr, a, REPS["power"])[0]) for N in cfg["n_grid"]]
            out(f"| {dv} | IRR {irr} | α={a:.2f} | " + " | ".join(f"{p:.2f}" for _, p in row)
                + f" | {first_ok(row)} |")
hc = cfg["h3_hypothetical"]
hyp = {(0, -0.5): hc["low_lowdep"], (1, -0.5): hc["high_lowdep"],
       (0, 0.5): hc["low_highdep"], (1, 0.5): hc["high_highdep"]}
target = (hyp[(1, .5)] - hyp[(1, -.5)]) - (hyp[(0, .5)] - hyp[(0, -.5)])
row = [(N, sim_h3(N, hyp, icc, REPS["h3"])[0]) for N in cfg["n_grid"]]
out(f"| H3 | interaction {target:+.2f} (config hypothetical) | ICC {icc:.2f} | "
    + " | ".join(f"{p:.2f}" for _, p in row) + f" | {first_ok(row)} |")
row = [(N, sim_h3(N, pilot_cells, icc, REPS["h3"])[0]) for N in cfg["n_grid"]]
out(f"| H3 | pilot cells (as observed) | ICC {icc:.2f} | "
    + " | ".join(f"{p:.2f}" for _, p in row) + f" | {first_ok(row)} |")

open(os.path.join(outdir, "results.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
