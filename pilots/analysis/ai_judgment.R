# Exploratory: participant behaviour / teammate exposure vs judging teammates "All were automated"
# Run ai_judgment.py first. Rscript ai_judgment.R > ai_judgment_results.txt
suppressPackageStartupMessages(library(dplyr))
options(width = 160)
P <- read.csv("ai_judgment_data.csv")
cat("Answers by sample:\n"); print(table(P$tp_post_10, P$sample))
preds <- c(n_msgs = "Participant messages", n_req_msgs = "Request messages", any_req = "Any request",
  n_social = "Social messages", n_intel_typed = "Typed intel", n_other = "Other messages",
  n_shares = "Records shared", n_sched_fulfilled = "Teammate requests fulfilled", correct_total = "Correct (of 4)",
  conf_mean = "Confidence", file_dwell_s = "File dwell (s)", n_agent_msgs = "AGENT msgs received",
  n_agent_shares = "AGENT shares received", ag_clarify = "AGENT 'which crate?' clarifications",
  ag_free_text = "AGENT free-text (non-template) msgs", ag_share_dupes = "AGENT duplicate shares",
  teammate_fallback_replies = "AGENT scripted fallbacks", teammate_generated_replies = "AGENT model-generated replies",
  tp_post_06 = "Expected quick replies", tp_post_07 = "Teammates willing to help", tp_post_08 = "Teammates competent")
res <- bind_rows(lapply(names(preds), function(v) {
  x <- P[[v]]; g <- P$judged_ai
  w <- suppressWarnings(wilcox.test(x[g == 1], x[g == 0], exact = FALSE))
  rb <- 2 * w$statistic / (sum(g == 1) * sum(g == 0)) - 1
  data.frame(predictor = preds[[v]], judged_AI = round(mean(x[g == 1]), 2), other = round(mean(x[g == 0]), 2),
             rank_biserial = round(unname(rb), 2), p = round(w$p.value, 3))
}))
cat("\n'All were automated' (n =", sum(P$judged_ai), ") vs any other answer (n =", sum(!P$judged_ai), "); Mann-Whitney; rank-biserial > 0 = higher among AI judges\n")
print(res[order(res$p), ], row.names = FALSE)
cat("\nMeans by full answer:\n")
print(P %>% group_by(tp_post_10) %>% summarise(n = n(), across(c(n_msgs, n_req_msgs, n_social, n_agent_msgs, ag_free_text, ag_clarify, teammate_generated_replies, correct_total, tp_post_07, tp_post_08), ~ round(mean(.x), 2))) %>% as.data.frame())
cat("\nMultivariable logit (standardized predictors):\n")
z <- function(x) (x - mean(x)) / sd(x)
fit <- glm(judged_ai ~ z(n_msgs) + z(ag_free_text) + z(correct_total) + z(tp_post_08) + sample, binomial, P)
print(round(cbind(OR = exp(coef(fit)), coef(summary(fit))[, c(2, 4)]), 3))
cat("\nBy condition:\n"); print(table(paste(P$cell, P$workload), P$judged_ai))
cat("Fisher, condition: p =", fisher.test(table(paste(P$cell, P$workload), P$judged_ai))$p.value, "\n")
cat("\nSessions where an agent disclosed being automated:\n"); print(P[P$ag_disclosed == 1, c("session_id", "tp_post_10")])
