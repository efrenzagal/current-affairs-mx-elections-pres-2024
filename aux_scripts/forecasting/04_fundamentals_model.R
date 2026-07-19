# ─────────────────────────────────────────────────────────────────────────────
# Fundamentals model -- rung 1: static log-ratio regression
# No polls, no dynamics. Predicts bloc vote share from what was already known
# before the campaign: the bloc's prior result and whether it's the
# incumbent president's bloc going into the race.
#
# Reusable across chambers -- call build_predictors() / fit_fundamentals() /
# predict_fundamentals() on pre_national_bloc_shares, sen_national_bloc_shares,
# or dip_national_bloc_shares.
#
# Requires 00_query_helpers.R, the relevant 01_queries_*.R, 02_bloc_mapping.R,
# and the relevant 03_bloc_shares_*.R to have been run first.
# ─────────────────────────────────────────────────────────────────────────────

{ # Incumbency table --------------------------------------------------------
  # Which bloc held the presidency going into each election. Same for all
  # three chambers, since it's about who's the incumbent, not who's running.
  # 2015/2021 (DIP midterms) use whoever was president that year.

  incumbent_bloc_by_year <- tribble(
    ~year, ~incumbent_bloc,
    1994, "C",  # Salinas, PRI
    2000, "C",  # Zedillo, PRI
    2006, "R",  # Fox, PAN
    2012, "R",  # Calderon, PAN
    2015, "C",  # Pena Nieto, PRI (midterm)
    2018, "C",  # Pena Nieto, PRI
    2021, "L",  # AMLO, MORENA (midterm)
    2024, "L",  # AMLO, MORENA
  )

}

{ # Predictor builder ---------------------------------------------------
  # log_ratio = log(share_bloc / share_baseline), baseline = "C" (present,
  # nonzero, in every election of every chamber -- see 02_bloc_mapping.R).
  # prior_log_ratio = same bloc's log-ratio last time this chamber was
  # contested. incumbent = 1 if this bloc held the presidency going in.
  #
  # MC is dropped: it only has one observation (PRE_2024) so there's no prior
  # value to regress on yet. Revisit once a second MC-solo race exists.

  build_predictors <- function(bloc_shares) {
    baseline <- bloc_shares %>%
      filter(bloc == "C") %>%
      select(election_id, year, share_baseline = share)

    bloc_shares %>%
      filter(bloc %in% c("L", "R", "C")) %>%
      left_join(baseline, by = c("election_id", "year")) %>%
      mutate(log_ratio = log(share / share_baseline)) %>%
      arrange(bloc, year) %>%
      group_by(bloc) %>%
      mutate(prior_log_ratio = lag(log_ratio)) %>%
      ungroup() %>%
      left_join(incumbent_bloc_by_year, by = "year") %>%
      mutate(incumbent = as.integer(bloc == incumbent_bloc)) %>%
      filter(!is.na(prior_log_ratio)) %>%  # first election per bloc has no prior
      select(election_id, year, bloc, share, log_ratio, prior_log_ratio, incumbent)
  }

}

{ # Fit -----------------------------------------------------------------
  # One regression per bloc (L and R; C is the baseline by construction).
  # prior_log_ratio only -- with 4-6 usable observations per bloc, adding
  # incumbent (0/1, often just one or two 1's in that window) leaves ~1
  # residual df and the fit goes rank-deficient on out-of-sample predictors.
  # Single-predictor keeps 2+ residual df, which is still tiny but at least
  # estimable. Revisit once SEN/DIP years are pooled in for more df, or once
  # incumbent has enough variation to earn its own coefficient.

  fit_fundamentals <- function(predictors) {
    predictors %>%
      filter(bloc != "C") %>%
      group_by(bloc) %>%
      summarise(
        model = list(lm(log_ratio ~ prior_log_ratio, data = pick(everything()))),
        .groups = "drop"
      )
  }

}

{ # Predict ---------------------------------------------------------------
  # Given fitted models and a new set of predictor rows (e.g. one holdout
  # election), returns predicted shares. Renormalizes L/R/C to sum to 1
  # (MC and any other bloc not modeled here isn't included).

  predict_fundamentals <- function(models, new_predictors) {
    preds <- new_predictors %>%
      filter(bloc != "C") %>%
      left_join(models, by = "bloc") %>%
      rowwise() %>%
      mutate(pred_log_ratio = predict(model, newdata = pick(prior_log_ratio))) %>%
      ungroup() %>%
      select(election_id, year, bloc, pred_log_ratio)

    preds %>%
      bind_rows(tibble(
        election_id = unique(preds$election_id), year = unique(preds$year),
        bloc = "C", pred_log_ratio = 0
      )) %>%
      mutate(rel_share = exp(pred_log_ratio)) %>%
      mutate(pred_share = rel_share / sum(rel_share)) %>%
      select(election_id, year, bloc, pred_share)
  }

}

{ # Predict with uncertainty -------------------------------------------------
  # Point predictions alone hide how little data this is trained on (as few
  # as 4 observations per bloc, ~1 residual df). This simulates draws from
  # each bloc's predictive t-distribution (mean = predicted log_ratio, scale
  # from predict.lm's prediction SE, df = residual df), then renormalizes
  # every draw to the simplex jointly -- so a high draw for L mechanically
  # pulls C down in that same draw, same logic as the paper's MCMC draws.
  # With ~1 residual df per bloc these intervals will be very wide (a
  # t-distribution with 1 df is Cauchy) -- that width is the honest answer
  # to "how much can 4 elections really tell you," not a bug.

  simulate_fundamentals <- function(models, new_predictors, n_sims = 4000, conf = 0.90) {
    draws <- new_predictors %>%
      filter(bloc != "C") %>%
      left_join(models, by = "bloc") %>%
      rowwise() %>%
      mutate(
        pred = list(predict(model, newdata = pick(prior_log_ratio), se.fit = TRUE)),
        fit_val   = pred$fit,
        pred_se   = sqrt(pred$se.fit^2 + pred$residual.scale^2),
        resid_df  = pred$df
      ) %>%
      ungroup() %>%
      select(election_id, year, bloc, fit_val, pred_se, resid_df)

    sim_long <- draws %>%
      rowwise() %>%
      mutate(sim = list(fit_val + pred_se * rt(n_sims, df = resid_df))) %>%
      ungroup() %>%
      select(election_id, year, bloc, sim) %>%
      tidyr::unnest(sim) %>%
      group_by(election_id, year, bloc) %>%
      mutate(draw_id = row_number()) %>%
      ungroup() %>%
      bind_rows(
        draws %>% distinct(election_id, year) %>%
          tidyr::crossing(draw_id = seq_len(n_sims)) %>%
          mutate(bloc = "C", sim = 0)
      ) %>%
      mutate(rel_share = exp(sim)) %>%
      group_by(election_id, year, draw_id) %>%
      mutate(share_draw = rel_share / sum(rel_share)) %>%
      ungroup()

    lo <- (1 - conf) / 2
    hi <- 1 - lo

    sim_long %>%
      group_by(election_id, year, bloc) %>%
      summarise(
        pred_share = median(share_draw),
        lower      = quantile(share_draw, lo),
        upper      = quantile(share_draw, hi),
        .groups = "drop"
      )
  }

}

{ # Demo: fit on PRE 1994-2018, predict PRE 2024 ---------------------------

  pre_predictors <- build_predictors(pre_national_bloc_shares)
  pre_train      <- pre_predictors %>% filter(year < 2024)
  pre_models     <- fit_fundamentals(pre_train)

  pre_2024_actual    <- pre_national_bloc_shares %>% filter(year == 2024, bloc %in% c("L", "R", "C"))
  pre_2024_predictor <- pre_predictors %>% filter(year == 2024)
  pre_2024_forecast  <- predict_fundamentals(pre_models, pre_2024_predictor)
  pre_2024_interval  <- simulate_fundamentals(pre_models, pre_2024_predictor, conf = 0.90)

  pre_2024_actual %>%
    select(bloc, actual_share = share) %>%
    inner_join(pre_2024_interval, by = "bloc") %>%
    mutate(
      in_interval = actual_share >= lower & actual_share <= upper,
      across(c(pred_share, lower, upper, actual_share), ~ round(.x * 100, 1))
    )

}
