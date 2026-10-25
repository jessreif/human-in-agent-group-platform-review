# Analysis of the 2026-10-18 pilots. Run build_dataset.py first.
# Rscript analysis.R > results.txt
suppressPackageStartupMessages({
  library(dplyr); library(tidyr); library(MASS); library(lme4); library(emmeans)
  library(sandwich); library(lmtest); library(ggplot2)
})
options(width = 160, dplyr.summarise.inform = FALSE)
here <- if (interactive()) "." else dirname(sub("--file=", "", grep("--file=", commandArgs(FALSE), value = TRUE)))
setwd(here)
dir.create("figures", showWarnings = FALSE)

P  <- read.csv("participants.csv")
C  <- read.csv("crates.csv")
SR <- read.csv("scripted_requests.csv")
PR <- read.csv("participant_requests.csv")
Fu <- read.csv("funnel.csv")
C$correct <- as.integer(C$correct == "True")
C$correct[is.na(C$correct)] <- 0           # unanswered = incorrect (as scored)
SR$fulfilled <- as.integer(SR$fulfilled == "True")

hdr <- function(x) cat("\n\n", strrep("=", 100), "\n", x, "\n", strrep("=", 100), "\n", sep = "")
sub_ <- function(x) cat("\n--- ", x, " ---\n", sep = "")
msd <- function(x) sprintf("%.2f (%.2f)", mean(x, na.rm = TRUE), sd(x, na.rm = TRUE))
dval <- function(x, g) { # Cohen's d, group2 - group1 (factor order)
  l <- levels(factor(g)); a <- x[g == l[1]]; b <- x[g == l[2]]
  sp <- sqrt(((length(a) - 1) * var(a, na.rm = TRUE) + (length(b) - 1) * var(b, na.rm = TRUE)) / (length(a) + length(b) - 2))
  (mean(b, na.rm = TRUE) - mean(a, na.rm = TRUE)) / sp
}
hc3 <- function(fit) coeftest(fit, vcov = vcovHC(fit, type = "HC3"))

items <- c(tp_post_01 = "Had info needed", tp_post_02 = "Important info I lacked",
           tp_post_03 = "Teammates knew things I didn't", tp_post_05 = "Asking would take teammates' scarce time",
           tp_post_06 = "Expected quick replies", tp_post_07 = "Teammates willing to help",
           tp_post_08 = "Teammates competent", tp_post_09 = "MC: my workload heavier after check-in",
           tp_post_11 = "MC: teammates' workloads heavier", tp_post_12 = "MC: teammates differed in workload")

behav <- c(n_req_msgs = "Request messages (hand-coded)", any_req = "Any request (0/1)",
           n_req_crates = "Distinct crates requested", n_parsed_req = "Requests (platform parser)",
           first_req_s = "Active sec to first request", n_offer = "Offers of help",
           n_intel_typed = "Typed intel messages", n_msgs = "All participant messages",
           n_sched_fulfilled = "Teammate requests fulfilled (of 5)", n_shares = "Records shared (total)",
           n_shares_proactive = "Unprompted shares", correct_total = "Correct (of 4)",
           correct_own = "Correct own-file crates (of 2)", correct_team = "Correct team-dependent crates (of 2)",
           conf_mean = "Mean confidence (0-100)", file_viewed = "Case-file records viewed",
           file_coverage = "File coverage (share viewed)", file_dwell_s = "File dwell (s, summed record-level)",
           own_records_in_file = "Records in file per own crate")

# =====================================================================================================
hdr("0. FUNNEL / ATTRITION")
Fu$cond <- ifelse(Fu$sample == "S60", Fu$manifest_id, Fu$inv_condition)
print(table(Fu$sample, Fu$stage))
print(with(Fu, table(cond, stage)))
for (s in c("S60", "S30")) {
  f <- Fu[Fu$sample == s, ]; tb <- table(f$cond, f$stage == "completion")
  cat(s, "Fisher test completion ~ condition: p =", signif(fisher.test(tb)$p.value, 3), "\n")
}

# =====================================================================================================
hdr("1. STUDY 1 (S60): CASE-FILE OVERLAP (none/some) x CASE-FILE SIZE (full/thin), n = 15/cell")
A <- P %>% filter(sample == "S60") %>%
  mutate(overlap = factor(overlap, c("none", "some")), casefile = factor(casefile, c("full", "thin")))
contrasts(A$overlap) <- contr.sum(2); contrasts(A$casefile) <- contr.sum(2)

sub_("Cell means (SD)")
vars1 <- c(names(behav), names(items)[1:7])
tab <- A %>% group_by(overlap, casefile) %>% summarise(across(all_of(vars1), msd)) %>% ungroup() %>%
  pivot_longer(-c(overlap, casefile)) %>% mutate(cell = paste(overlap, casefile, sep = "/")) %>%
  dplyr::select(-overlap, -casefile) %>% pivot_wider(names_from = cell, values_from = value)
tab$label <- c(behav, items)[tab$name]
print(as.data.frame(tab), row.names = FALSE)

sub_("Main effects & interaction (linear model, HC3 SEs; sum-coded so main effects are averaged over the other factor)")
cat("Estimates are the difference some-none and thin-full (= -2 x sum-coded coefficient)\n")
res1 <- list()
for (v in vars1) {
  d <- A[!is.na(A[[v]]), ]
  fit <- lm(as.formula(paste(v, "~ overlap * casefile")), data = d)
  ct <- hc3(fit)
  res1[[v]] <- data.frame(var = v, label = c(behav, items)[v], n = nrow(d),
    overlap_diff = -2 * ct[2, 1], overlap_p = ct[2, 4], overlap_d = dval(d[[v]], d$overlap),
    file_diff = -2 * ct[3, 1], file_p = ct[3, 4], file_d = dval(d[[v]], d$casefile),
    int_p = ct[4, 4])
}
r1 <- bind_rows(res1); r1[, -(1:3)] <- round(r1[, -(1:3)], 3); print(r1, row.names = FALSE)

sub_("Count models for help-seeking / sharing (negative binomial; Poisson if theta -> inf)")
for (v in c("n_req_msgs", "n_req_crates", "n_parsed_req", "n_shares_proactive", "n_shares")) {
  fit <- tryCatch(glm.nb(as.formula(paste(v, "~ overlap * casefile")), data = A), error = function(e) NULL)
  disp <- with(A, var(A[[v]]) / mean(A[[v]]))
  cat("\n", v, " (var/mean =", round(disp, 2), ")\n")
  if (!is.null(fit)) print(round(coef(summary(fit)), 3)) else print("glm.nb failed")
}
sub_("Any request (logistic) and teammate requests fulfilled (binomial of 5)")
print(round(coef(summary(glm(any_req ~ overlap * casefile, binomial, A))), 3))
print(round(coef(summary(glm(cbind(n_sched_fulfilled, 5 - n_sched_fulfilled) ~ overlap * casefile, binomial, A))), 3))
cat("Fisher exact, any request by overlap:\n"); print(with(A, table(overlap, any_req))); print(fisher.test(table(A$overlap, A$any_req))$p.value)
cat("Fisher exact, any request by casefile:\n"); print(with(A, table(casefile, any_req))); print(fisher.test(table(A$casefile, A$any_req))$p.value)

sub_("Crate-level accuracy: correct ~ overlap * casefile * role + (1|participant)")
CA <- C %>% filter(sample == "S60") %>%
  mutate(overlap = factor(overlap, c("none", "some")), casefile = factor(casefile, c("full", "thin")),
         role = factor(role, c("own", "team")))
contrasts(CA$overlap) <- contr.sum(2); contrasts(CA$casefile) <- contr.sum(2); contrasts(CA$role) <- contr.sum(2)
g1 <- glmer(correct ~ overlap * casefile * role + (1 | session_id), family = binomial, data = CA,
            control = glmerControl(optimizer = "bobyqa"))
print(round(coef(summary(g1)), 3))
cat("\nPredicted P(correct) by cell x crate type:\n")
print(as.data.frame(emmeans(g1, ~ overlap * casefile | role, type = "response")))
cat("\nObserved P(correct):\n")
print(CA %>% group_by(overlap, casefile, role) %>% summarise(p = round(mean(correct), 3), n = n()) %>% as.data.frame())
g1b <- glmer(correct ~ (overlap + casefile) * role + (1 | session_id), family = binomial, data = CA,
             control = glmerControl(optimizer = "bobyqa"))
cat("\nWithout 3-way terms:\n"); print(round(coef(summary(g1b)), 3))
cat("\nEffect of thin file on P(correct), by crate type (odds ratio):\n")
print(summary(contrast(emmeans(g1b, ~ casefile | role), "revpairwise"), type = "response"))

sub_("Believability (tp_post_10) by cell")
print(table(A$cell, A$tp_post_10)); set.seed(20261018); cat("Fisher p =", fisher.test(table(A$cell, A$tp_post_10), simulate.p.value = TRUE, B = 1e4)$p.value, "\n")

# =====================================================================================================
hdr("2. STUDY 2 (S30): TEAMMATE WORKLOAD CUE, HIGH (both heavy) vs MIXED (one heavy, one light), n = 15/arm")
B <- P %>% filter(sample == "S30") %>% mutate(workload = factor(workload, c("high", "mixed")))
sub_("Seat levels revealed at the check-in")
print(with(B, table(workload, paste0("JT=", JT_level, " PM=", PM_level))))
print(with(B, table(workload, light_seat, useNA = "ifany")))

sub_("Baseline equivalence: own workload rating at check-in (pre-reveal)")
print(B %>% group_by(workload) %>% summarise(own = msd(own_workload)))
print(t.test(own_workload ~ workload, B))

vars2 <- c("tp_post_09", "tp_post_11", "tp_post_12", names(items)[1:7], names(behav))
sub_("High vs mixed: M (SD), Welch t, Wilcoxon, d (mixed - high)")
res2 <- lapply(vars2, function(v) {
  d <- B[!is.na(B[[v]]), ]
  if (length(unique(d[[v]])) < 2) return(NULL)
  tt <- t.test(as.formula(paste(v, "~ workload")), d)
  wt <- suppressWarnings(wilcox.test(as.formula(paste(v, "~ workload")), d, exact = FALSE))
  data.frame(var = v, label = c(behav, items)[v], high = msd(d[[v]][d$workload == "high"]),
             mixed = msd(d[[v]][d$workload == "mixed"]), diff = round(diff(rev(tt$estimate)) * -1, 2),
             t_p = round(tt$p.value, 3), wilcox_p = round(wt$p.value, 3), d = round(dval(d[[v]], d$workload), 2))
})
print(bind_rows(res2), row.names = FALSE)
cat("\nNB for request messages: \n"); print(round(coef(summary(glm.nb(n_req_msgs ~ workload, B))), 3))
cat("Any request:\n"); print(with(B, table(workload, any_req))); print(fisher.test(table(B$workload, B$any_req))$p.value)
cat("\nBelievability:\n"); print(table(B$workload, B$tp_post_10))

sub_("Fidelity: who answered the participant's typed requests (system-assigned responder), mixed arm")
PRm <- PR %>% filter(sample == "S30", workload == "mixed") %>%
  mutate(responder_is_light = responder == light_seat)
print(table(responder_is_light = PRm$responder_is_light))
print(binom.test(sum(PRm$responder_is_light), nrow(PRm)))
cat("Responder level x arm (all S30 parsed requests):\n")
print(with(PR %>% filter(sample == "S30"), table(workload, responder_level)))

sub_("Helping: fulfilment of the 5 scheduled teammate requests, by requester's revealed workload")
cat("Index 0 (45 s) precedes the 60 s check-in -> pre-manipulation baseline; indices 1-4 follow it\n")
SRb <- SR %>% filter(sample == "S30") %>%
  mutate(req_type = case_when(workload == "high" ~ "high-arm heavy",
                              requester_level <= 3 ~ "mixed: light",
                              TRUE ~ "mixed: heavy"),
         req_type = factor(req_type, c("high-arm heavy", "mixed: heavy", "mixed: light")),
         post = index >= 1)
print(SRb %>% group_by(post, req_type) %>% summarise(p_fulfilled = round(mean(fulfilled), 3), n = n()) %>% as.data.frame())
g2 <- glmer(fulfilled ~ req_type + (1 | session_id), family = binomial, data = filter(SRb, post),
            control = glmerControl(optimizer = "bobyqa"))
print(round(coef(summary(g2)), 3))
cat("Within mixed arm only (paired by participant), post check-in:\n")
mx <- SRb %>% filter(post, workload == "mixed") %>% group_by(session_id, req_type) %>%
  summarise(p = mean(fulfilled), n = n()) %>% pivot_wider(id_cols = session_id, names_from = req_type, values_from = p)
print(as.data.frame(mx)); print(colMeans(mx[, -1], na.rm = TRUE))
print(tryCatch(wilcox.test(mx$`mixed: light`, mx$`mixed: heavy`, paired = TRUE, exact = FALSE), error = function(e) e))

sub_("Crate-level accuracy, S30: correct ~ workload * role + (1|participant)")
CB <- C %>% filter(sample == "S30") %>% mutate(workload = factor(workload, c("high", "mixed")), role = factor(role, c("own", "team")))
g3 <- glmer(correct ~ workload * role + (1 | session_id), family = binomial, data = CB, control = glmerControl(optimizer = "bobyqa"))
print(round(coef(summary(g3)), 3))
print(CB %>% group_by(workload, role) %>% summarise(p = round(mean(correct), 3)) %>% as.data.frame())

# =====================================================================================================
hdr("3. POOLED (N = 90)")
P$wl3 <- factor(ifelse(P$sample == "S60", "none", P$workload), c("none", "high", "mixed"))
sub_("Overall paradigm descriptives (N = 90)")
desc <- P %>% summarise(across(all_of(c(names(behav), names(items)[1:7])),
          list(m = ~ mean(.x, na.rm = TRUE), sd = ~ sd(.x, na.rm = TRUE), md = ~ median(.x, na.rm = TRUE)))) %>%
  pivot_longer(everything(), names_to = c("var", ".value"), names_pattern = "(.*)_(m|sd|md)$")
desc$label <- c(behav, items)[desc$var]; print(as.data.frame(desc %>% mutate(across(m:md, ~ round(.x, 2)))), row.names = FALSE)
cat("\nAny request: ", mean(P$any_req), "; any unprompted share: ", mean(P$n_shares_proactive > 0),
    "; fulfilled >= 1 teammate request: ", mean(P$n_sched_fulfilled > 0), "\n")
print(table(P$tp_post_10))

sub_("Hand coding vs platform request parser")
cat("r =", round(cor(P$n_req_msgs, P$n_parsed_req), 3), "; any-request agreement =",
    round(mean((P$n_req_msgs > 0) == (P$n_parsed_req > 0)), 3), "\n")
print(table(hand_any = P$n_req_msgs > 0, parser_any = P$n_parsed_req > 0))

sub_("Same task cell (some overlap / thin file): no cue (S60) vs high vs mixed (S30) -- different build & day, interpret cautiously")
S3 <- P %>% filter(cell == "some_thin")
res3 <- lapply(c(names(behav), names(items)[1:7]), function(v) {
  d <- S3[!is.na(S3[[v]]), ]; if (length(unique(d[[v]])) < 2) return(NULL)
  k <- kruskal.test(as.formula(paste(v, "~ wl3")), d)
  ln <- lm(as.formula(paste(v, "~ wl3")), d); ct <- hc3(ln)
  data.frame(var = v, label = c(behav, items)[v],
             none = msd(d[[v]][d$wl3 == "none"]), high = msd(d[[v]][d$wl3 == "high"]), mixed = msd(d[[v]][d$wl3 == "mixed"]),
             high_vs_none_p = round(ct[2, 4], 3), mixed_vs_none_p = round(ct[3, 4], 3), kw_p = round(k$p.value, 3))
})
print(bind_rows(res3), row.names = FALSE)
cat("\nHelping (teammate requests fulfilled), crate-level index>=1 by cue:\n")
SRp <- SR %>% filter(index >= 1) %>% left_join(P %>% dplyr::select(session_id, wl3, cell), by = c("session_id", "cell")) %>% filter(cell == "some_thin")
print(SRp %>% group_by(wl3) %>% summarise(p = round(mean(fulfilled), 3), n = n()) %>% as.data.frame())
print(round(coef(summary(glmer(fulfilled ~ wl3 + (1 | session_id), family = binomial, data = SRp, control = glmerControl(optimizer = "bobyqa")))), 3))

sub_("Pooled associations (participant level, Spearman)")
cv <- c("n_req_msgs", "n_req_crates", "n_sched_fulfilled", "n_shares_proactive", "correct_total", "correct_own",
        "correct_team", "conf_mean", "file_viewed", "tp_post_02", "tp_post_03", "tp_post_05", "tp_post_06", "tp_post_07")
cm <- cor(P[, cv], method = "spearman", use = "pairwise"); print(round(cm, 2))
for (v in c("correct_team", "correct_own", "correct_total")) {
  ct <- cor.test(P$n_req_crates, P[[v]], method = "spearman", exact = FALSE)
  cat("n_req_crates vs", v, ": rho =", round(ct$estimate, 3), " p =", signif(ct$p.value, 3), "\n")
}
for (v in c("tp_post_03", "tp_post_05", "tp_post_06", "tp_post_07", "tp_post_02")) {
  ct <- cor.test(P$n_req_msgs, P[[v]], method = "spearman", exact = FALSE)
  cat("n_req_msgs vs", v, ": rho =", round(ct$estimate, 3), " p =", signif(ct$p.value, 3), "\n")
}

sub_("Crate level (exploratory): did asking about a crate go with getting it right? correct ~ requested * role + sample + (1|participant)")
cat("NB: 'requested' = participant asked about that crate number at least once; requests are NOT classified by need.\n")
C$role <- factor(C$role, c("own", "team"))
g4 <- glmer(correct ~ requested * role + sample + (1 | session_id), family = binomial, data = C, control = glmerControl(optimizer = "bobyqa"))
print(round(coef(summary(g4)), 3))
print(C %>% group_by(role, requested) %>% summarise(p = round(mean(correct), 3), n = n(),
       got_agent_share = round(mean(received_agent_share), 3)) %>% as.data.frame())

sub_("Planning: n per cell for 80% power (two-sided alpha .05) at observed and benchmark d")
pw <- function(d) if (is.na(d) || abs(d) < .05) NA else ceiling(power.t.test(delta = abs(d), power = .8)$n)
plan <- bind_rows(
  r1 %>% transmute(study = "S60 overlap", var, d = overlap_d),
  r1 %>% transmute(study = "S60 casefile", var, d = file_d),
  bind_rows(res2) %>% transmute(study = "S30 mixed-high", var, d)) %>%
  filter(var %in% c("n_req_msgs", "any_req", "n_req_crates", "n_sched_fulfilled", "n_shares_proactive",
                    "correct_total", "correct_team", "tp_post_05", "tp_post_09", "tp_post_11", "tp_post_12")) %>%
  mutate(d = round(d, 2), n_per_cell_80 = sapply(d, pw))
print(as.data.frame(plan), row.names = FALSE)
cat("Benchmarks: d=.3 ->", pw(.3), "/cell; d=.4 ->", pw(.4), "; d=.5 ->", pw(.5), "\n")

# =====================================================================================================
# Figures
th <- theme_minimal(base_size = 11) + theme(panel.grid.minor = element_blank(), legend.position = "bottom")
pal <- c(none = "#8a8a8a", high = "#c0504d", mixed = "#4f81bd")
fig1 <- P %>% filter(sample == "S60") %>%
  dplyr::select(overlap, casefile, `Request messages` = n_req_msgs, `Teammate requests fulfilled (of 5)` = n_sched_fulfilled,
                `Correct (of 4)` = correct_total, `Records viewed / 10` = file_viewed) %>%
  mutate(`Records viewed / 10` = `Records viewed / 10` / 10) %>%
  pivot_longer(-c(overlap, casefile)) %>% group_by(overlap, casefile, name) %>%
  summarise(m = mean(value), se = sd(value) / sqrt(n()))
ggsave("figures/fig1_s60_cells.png", ggplot(fig1, aes(casefile, m, colour = overlap, group = overlap)) +
  geom_point(position = position_dodge(.3), size = 2.5) + geom_errorbar(aes(ymin = m - se, ymax = m + se), width = .15, position = position_dodge(.3)) +
  geom_line(position = position_dodge(.3)) + facet_wrap(~ name, scales = "free_y", nrow = 1) +
  scale_colour_manual(values = c(none = "#7f7f7f", some = "#2e75b6"), name = "Case-file overlap") +
  labs(x = "Case file size", y = "Mean (+/- 1 SE)", title = "Study 1 (N = 60): information environment") + th,
  width = 11, height = 3.6, dpi = 160)

fig2 <- CA %>% group_by(overlap, casefile, role) %>% summarise(m = mean(correct), se = sqrt(m * (1 - m) / n()))
ggsave("figures/fig2_s60_accuracy.png", ggplot(fig2, aes(casefile, m, colour = overlap, group = overlap)) +
  geom_point(position = position_dodge(.3), size = 2.5) + geom_errorbar(aes(ymin = m - se, ymax = m + se), width = .15, position = position_dodge(.3)) +
  geom_line(position = position_dodge(.3)) + facet_wrap(~ role, labeller = as_labeller(c(own = "Own-file crates", team = "Team-dependent crates"))) +
  scale_colour_manual(values = c(none = "#7f7f7f", some = "#2e75b6"), name = "Case-file overlap") + scale_y_continuous(labels = scales::percent, limits = c(0, 1)) +
  labs(x = "Case file size", y = "Proportion correct (+/- 1 SE)", title = "Study 1: accuracy by crate type") + th, width = 7, height = 3.6, dpi = 160)

fig3 <- P %>% filter(cell == "some_thin") %>%
  dplyr::select(wl3, `MC: teammates heavier` = tp_post_11, `MC: teammates differed` = tp_post_12,
                `Asking takes their time` = tp_post_05, `Request messages` = n_req_msgs,
                `Teammate requests fulfilled` = n_sched_fulfilled, `Correct (of 4)` = correct_total) %>%
  pivot_longer(-wl3) %>% filter(!is.na(value)) %>% group_by(wl3, name) %>% summarise(m = mean(value), se = sd(value) / sqrt(n()))
fig3$name <- factor(fig3$name, c("MC: teammates heavier", "MC: teammates differed", "Asking takes their time", "Request messages", "Teammate requests fulfilled", "Correct (of 4)"))
ggsave("figures/fig3_workload.png", ggplot(fig3, aes(wl3, m, colour = wl3)) + geom_point(size = 2.5) +
  geom_errorbar(aes(ymin = m - se, ymax = m + se), width = .15) + facet_wrap(~ name, scales = "free_y", nrow = 1) +
  scale_colour_manual(values = pal, guide = "none") +
  labs(x = "Teammate workload cue (none = Study 1 some/thin cell)", y = "Mean (+/- 1 SE)", title = "Study 2 (N = 30) + matched Study 1 cell") + th,
  width = 12, height = 3.6, dpi = 160)

fig4 <- SRb %>% group_by(post = ifelse(post, "After check-in (100-245 s)", "Before check-in (45 s)"), req_type) %>%
  summarise(m = mean(fulfilled), se = sqrt(m * (1 - m) / n()), n = n())
ggsave("figures/fig4_helping_by_requester.png", ggplot(fig4, aes(req_type, m, fill = req_type)) + geom_col(width = .6) +
  geom_errorbar(aes(ymin = m - se, ymax = m + se), width = .15) + facet_wrap(~ post) +
  geom_text(aes(y = 0.03, label = paste0("n=", n)), colour = "white", size = 3) +
  scale_fill_manual(values = c("#c0504d", "#e3a19f", "#4f81bd"), guide = "none") + scale_y_continuous(labels = scales::percent) +
  labs(x = "Who asked the participant", y = "Requests answered with a share", title = "Study 2: helping the heavy vs light teammate") + th,
  width = 8, height = 3.6, dpi = 160)
cat("\nFigures written to figures/\n")
