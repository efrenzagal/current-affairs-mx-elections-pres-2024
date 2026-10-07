# Which EIC indicator is worth adding as a second regressor next to income?
#
# For each of 218 EIC 2025 municipal indicators (rates and averages), with
# every number weighted by population (EIC POBTOT):
#   cor_ingreso      correlation with log household labor income. Close to
#                    0 = adds information income does not already carry
#   cor_voto         plain correlation with the vote
#   partial          partial correlation with the vote given log income: the
#                    indicator's link to the part of the vote income leaves
#                    unexplained (the residual of y ~ log_ingreso)
#   partial_estado   the same, also holding the state fixed: compares
#                    municipalities within the same state only
#
# Cautions:
#   - The EIC was collected in 2025, after the 2024 election. Program
#     coverage, IMSS-Bienestar affiliation and similar can be consequences of
#     the vote rather than causes.
#   - With ~2,460 municipalities almost every indicator is "significant".
#     Rank by size and prefer indicators with a reason behind them.

# Libraries
{
  library(tidyverse)
  library(DBI)
  library(RSQLite)
  library(duckdb)
  library(plotly)
  library(sandwich)
  library(lmtest)
}

# Read data
{
  setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')

  # Votes: one row per municipality x election (PRE, DIP_MR, SEN_MR 2024)
  con <- dbConnect(SQLite(), "election_data.db")
  votes_long <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/municipal_vote_share.sql"))
  dbDisconnect(con)

  # EIC 2025: income and population per municipality, and the 218 indicators
  # in long format
  con <- dbConnect(duckdb(), "state_scorecards/data/eic2025.duckdb", read_only = TRUE)
  eic <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/municipal_eic_predictors.sql"))
  indicators_long <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/municipal_eic_indicators_long.sql"))
  dbDisconnect(con, shutdown = TRUE)
}

# Base table: the outcome, log income and the weight, one row per municipality
# Outcome: MORENA coalition share for deputies. Change election_type to
# "SEN" or "PRE" to screen against another race.
{
  base <- votes_long %>%
    filter(election_type == "DIP") %>%
    select(cvegeo, y = pct_coalicion_morena) %>%
    inner_join(eic %>% select(cvegeo, state_code, state_name, municipio,
                              poblacion, ingreso_trabajo_hogar),
               by = "cvegeo") %>%
    mutate(log_ingreso = log(ingreso_trabajo_hogar)) %>%
    drop_na(y, log_ingreso, poblacion)

  nrow(base)
}

# Step 1: how much of the income model's error is just the state?
# If state dummies alone raise R² a lot, state fixed effects come before any
# second EIC variable
{
  m_ingreso        <- lm(y ~ log_ingreso, data = base, weights = poblacion)
  m_ingreso_estado <- lm(y ~ log_ingreso + state_code, data = base, weights = poblacion)

  tibble(
    modelo = c("y ~ log_ingreso", "y ~ log_ingreso + estado"),
    r2     = c(summary(m_ingreso)$r.squared, summary(m_ingreso_estado)$r.squared)
  )
}

# Step 2: screen every indicator
# Partial correlation = correlation between the two sets of residuals after
# regressing both the vote and the indicator on log income (and on the state,
# for partial_estado), with the same population weights
{
  wcor <- function(a, b, w) cov.wt(cbind(a, b), wt = w, cor = TRUE)$cor[1, 2]

  screen_one <- function(d) {
    if (sd(d$value) == 0) return(tibble(n = nrow(d)))

    w    <- d$poblacion
    r_y  <- resid(lm(y     ~ log_ingreso,              data = d, weights = w))
    r_x  <- resid(lm(value ~ log_ingreso,              data = d, weights = w))
    r_ye <- resid(lm(y     ~ log_ingreso + state_code, data = d, weights = w))
    r_xe <- resid(lm(value ~ log_ingreso + state_code, data = d, weights = w))

    tibble(
      n              = nrow(d),
      cor_ingreso    = wcor(d$value, d$log_ingreso, w),
      cor_voto       = wcor(d$value, d$y, w),
      partial        = wcor(r_x, r_y, w),
      partial_estado = wcor(r_xe, r_ye, w)
    )
  }

  screen <- indicators_long %>%
    inner_join(base, by = "cvegeo") %>%
    group_by(indicator, section, name) %>%
    group_modify(~ screen_one(.x)) %>%
    ungroup() %>%
    arrange(desc(abs(partial)))

  # Strongest links with the vote after income
  screen %>%
    select(indicator, name, n, cor_ingreso, cor_voto, partial, partial_estado) %>%
    mutate(across(cor_ingreso:partial_estado, ~ round(.x, 2))) %>%
    head(25)
}

# Chart: correlation with income (x) vs partial correlation with the vote (y)
# The useful candidates sit far from 0 on y and close to 0 on x. Color shows
# whether the link survives within states
{
  p_screen <- screen %>%
    mutate(hover = paste0(
      "<b>", indicator, "</b><br>",
      str_replace_all(str_wrap(name, 60), "\n", "<br>"),
      "<br>Sección: ", section,
      "<br>Corr. con log ingreso: ", round(cor_ingreso, 2),
      "<br>Corr. con el voto: ", round(cor_voto, 2),
      "<br>Parcial | ingreso: ", round(partial, 2),
      "<br>Parcial | ingreso + estado: ", round(partial_estado, 2),
      "<br>n = ", n
    )) %>%
    ggplot(aes(cor_ingreso, partial, color = partial_estado, text = hover)) +
    geom_hline(yintercept = 0, color = "grey60") +
    geom_vline(xintercept = 0, color = "grey60") +
    geom_point(size = 2) +
    scale_color_gradient2(low = "steelblue", mid = "grey85", high = "firebrick",
                          midpoint = 0, limits = c(-1, 1)) +
    labs(x = "Correlación con log ingreso",
         y = "Correlación parcial con el voto, dado el ingreso",
         color = "Parcial\n| ingreso\n+ estado")

  ggplotly(p_screen, tooltip = "text")
}

# Added-variable plots: what does educación media superior add once income
# is in the model?
# Step 1 is the vote against income. Step 2 takes what income leaves
# unexplained in the vote (resid_y) and in the share with high school
# (resid_ems, the part of education income does not predict) and plots one
# against the other. By the Frisch-Waugh theorem its slope is exactly the
# education coefficient in y ~ log_ingreso + ems, so this is that coefficient
# drawn as a scatter.
{
  av <- base %>%
    inner_join(indicators_long %>%
                 filter(indicator == "PCN_P15YM_EMS") %>%
                 select(cvegeo, ems = value),
               by = "cvegeo")

  m_y    <- lm(y   ~ log_ingreso,       data = av, weights = poblacion)
  m_ems  <- lm(ems ~ log_ingreso,       data = av, weights = poblacion)
  m_both <- lm(y   ~ log_ingreso + ems, data = av, weights = poblacion)

  av <- av %>%
    mutate(
      resid_y    = resid(m_y),
      resid_ems  = resid(m_ems),
      resid_both = resid(m_both),
      hover = paste0(
        "<b>", municipio, ", ", state_name, "</b>",
        "<br>log ingreso: ", round(log_ingreso, 2),
        "<br>% educación media superior: ", round(ems, 1),
        "<br>% coalición MORENA: ", round(y, 1),
        "<br>Población: ", scales::comma(poblacion)
      )
    )

  # Same number twice: the slope of plot 2 and the coefficient in m_both
  c(pendiente_grafica = unname(coef(lm(resid_y ~ resid_ems, data = av,
                                       weights = poblacion))["resid_ems"]),
    coef_m_both       = unname(coef(m_both)["ems"]))
}

# Plot 1: income vs vote
{
  p_av1 <- ggplot(av, aes(log_ingreso, y)) +
    geom_point(aes(size = poblacion, text = hover),
               color = "steelblue", alpha = 0.4) +
    geom_smooth(aes(weight = poblacion), method = "lm", formula = y ~ x,
                se = FALSE, color = "firebrick") +
    scale_size_area(max_size = 15, guide = "none") +
    labs(x = "log ingreso por trabajo del hogar",
         y = "% coalición MORENA, diputados 2024")

  ggplotly(p_av1, tooltip = "text")
}

# Plot 2: what income leaves unexplained, vote vs education
# x = 0 is a municipality with exactly the high-school share its income
# predicts; y = 0 one that votes exactly as its income predicts
{
  p_av2 <- ggplot(av, aes(resid_ems, resid_y)) +
    geom_hline(yintercept = 0, color = "grey60") +
    geom_vline(xintercept = 0, color = "grey60") +
    geom_point(aes(size = poblacion, text = hover),
               color = "steelblue", alpha = 0.4) +
    geom_smooth(aes(weight = poblacion), method = "lm", formula = y ~ x,
                se = FALSE, color = "firebrick") +
    scale_size_area(max_size = 15, guide = "none") +
    labs(x = "% educación media superior, residuo dado el ingreso",
         y = "% coalición MORENA, residuo dado el ingreso")

  ggplotly(p_av2, tooltip = "text")
}

# Plot 3: what is left for each state after income and education
# Population-weighted mean residual of m_both per state: points above (+) or
# below (-) what income and education predict. Close to what state fixed
# effects would estimate, without re-fitting the slopes. The question is what
# the states at each end have in common
{
  state_resid <- av %>%
    group_by(state_name) %>%
    summarise(resid = weighted.mean(resid_both, poblacion),
              poblacion = sum(poblacion), .groups = "drop")

  p_av3 <- ggplot(state_resid,
                  aes(resid, reorder(state_name, resid), fill = resid > 0,
                      text = paste0("<b>", state_name, "</b>",
                                    "<br>Residuo medio: ", round(resid, 1), " pts"))) +
    geom_col() +
    geom_vline(xintercept = 0, color = "grey40") +
    scale_fill_manual(values = c(`TRUE` = "firebrick", `FALSE` = "steelblue"),
                      guide = "none") +
    labs(x = "Puntos sobre / bajo lo que predicen ingreso y educación",
         y = NULL)

  ggplotly(p_av3, tooltip = "text")
}

# Plot 4: the same residual for the largest municipalities
# The n_mun most populous municipalities, biggest on top. Same residual as
# plot 3 (m_both: income + educación media superior), one bar per municipality
{
  n_mun <- 50

  mun_resid <- av %>%
    slice_max(poblacion, n = n_mun) %>%
    mutate(etiqueta = paste0(municipio, " (", state_name, ")"),
           etiqueta = reorder(etiqueta, poblacion))

  p_av4 <- ggplot(mun_resid %>% arrange(resid_both),
                  aes(resid_both, etiqueta, fill = resid_both > 0,
                      text = paste0("<b>", municipio, ", ", state_name, "</b>",
                                    "<br>Residuo: ", round(resid_both, 1), " pts",
                                    "<br>% coalición MORENA: ", round(y, 1),
                                    "<br>Población: ", scales::comma(poblacion)))) +
    geom_col() +
    geom_vline(xintercept = 0, color = "grey40") +
    scale_fill_manual(values = c(`TRUE` = "firebrick", `FALSE` = "steelblue"),
                      guide = "none") +
    labs(x = "Puntos sobre / bajo lo que predicen ingreso y educación",
         y = NULL)

  ggplotly(p_av4, tooltip = "text", height = 900)
}

# With the state control: each variable after the other two
# m_full = y ~ log_ingreso + ems + state. For income and education these are
# added-variable plots: residual of the vote vs residual of the variable,
# both after the other two. Each slope equals that variable's coefficient in
# m_full (checked below). State is categorical, so its "plot" is the vote
# after income and education, grouped by state
{
  m_full <- lm(y ~ log_ingreso + ems + state_code, data = av, weights = poblacion)

  resid_after <- function(f) resid(lm(f, data = av, weights = poblacion))

  av <- av %>%
    mutate(
      ry_ingreso = resid_after(y           ~ ems + state_code),
      rx_ingreso = resid_after(log_ingreso ~ ems + state_code),
      ry_ems     = resid_after(y           ~ log_ingreso + state_code),
      rx_ems     = resid_after(ems         ~ log_ingreso + state_code),
      # Partial residual for state: what m_full attributes to the state plus
      # what it leaves unexplained
      parcial_estado = predict(m_full, type = "terms")[, "state_code"] + resid(m_full)
    )

  slope <- function(yv, xv) unname(coef(lm(av[[yv]] ~ av[[xv]], weights = av$poblacion))[2])
  tibble(
    variable    = c("log_ingreso", "ems"),
    pendiente   = c(slope("ry_ingreso", "rx_ingreso"), slope("ry_ems", "rx_ems")),
    coef_m_full = unname(coef(m_full)[c("log_ingreso", "ems")])
  )
}

# Plot 5: income after education and state
# Plot 6: education after income and state
{
  av_plot <- function(xv, yv, xlab) {
    p <- ggplot(av, aes(.data[[xv]], .data[[yv]])) +
      geom_hline(yintercept = 0, color = "grey60") +
      geom_vline(xintercept = 0, color = "grey60") +
      geom_point(aes(size = poblacion, text = hover),
                 color = "steelblue", alpha = 0.4) +
      geom_smooth(aes(weight = poblacion), method = "lm", formula = y ~ x,
                  se = FALSE, color = "firebrick") +
      scale_size_area(max_size = 15, guide = "none") +
      labs(x = xlab, y = "% coalición MORENA, residuo")
    ggplotly(p, tooltip = "text")
  }
}

{
  av_plot("rx_ingreso", "ry_ingreso",
          "log ingreso, residuo dada educación media superior y estado")
}

{
  av_plot("rx_ems", "ry_ems",
          "% educación media superior, residuo dado ingreso y estado")
}

# Plot 7: state after income and education
# One row per state: each municipality's partial residual (blue), and the
# state's effect in m_full (red diamond, population-weighted mean), ordered by
# that effect. The spread around the diamond is what neither the state nor
# the two variables explain
{
  estado_efecto <- av %>%
    group_by(state_name) %>%
    summarise(efecto = weighted.mean(parcial_estado, poblacion), .groups = "drop")

  p_estado <- av %>%
    left_join(estado_efecto, by = "state_name") %>%
    mutate(state_name = reorder(state_name, efecto)) %>%
    ggplot(aes(parcial_estado, state_name)) +
    geom_vline(xintercept = 0, color = "grey60") +
    geom_point(aes(size = poblacion, text = hover),
               color = "steelblue", alpha = 0.3) +
    geom_point(aes(efecto, state_name,
                   text = paste0("<b>", state_name, "</b><br>Efecto del estado: ",
                                 round(efecto, 1), " pts")),
               data = estado_efecto, color = "firebrick", shape = 18, size = 4) +
    scale_size_area(max_size = 8, guide = "none") +
    labs(x = "% coalición MORENA, efecto del estado + residuo", y = NULL)

  ggplotly(p_estado, tooltip = "text", height = 900)
}

# Within-between model: how much of each state's baseline is its poverty?
# ---------------------------------------------------------------------------
# Each variable is split in two (population-weighted):
#   ing_estado   the state's average log income: same value for every
#                municipality in the state ("how rich is the state")
#   ing_dentro   the municipality minus its state's average ("how rich is it
#                for its state"). Tuxtla is +0.66 in Chiapas; Mina, with the
#                same income, is -0.39 in Nuevo León
#   ems_estado / ems_dentro, the same for educación media superior
# The within coefficients compare municipalities inside the same state and
# equal the fixed-effects model exactly. The between coefficients compare
# states with each other: only 32 observations, so standard errors are
# clustered by state. No state dummies: what the state means do not explain is
# left as each state's "history"
# ---------------------------------------------------------------------------
{
  mw <- av %>%
    group_by(state_code, state_name) %>%
    mutate(
      ing_estado = weighted.mean(log_ingreso, poblacion),
      ems_estado = weighted.mean(ems, poblacion),
      ing_dentro = log_ingreso - ing_estado,
      ems_dentro = ems - ems_estado
    ) %>%
    ungroup()

  m_wb <- lm(y ~ ing_dentro + ing_estado + ems_dentro + ems_estado,
             data = mw, weights = poblacion)
  m_fe <- lm(y ~ log_ingreso + ems + state_code, data = mw, weights = poblacion)

  # Clustered by state in both models
  print(coeftest(m_wb, vcov = vcovCL(m_wb, cluster = ~ state_code)))
  print(coeftest(m_fe, vcov = vcovCL(m_fe, cluster = ~ state_code))[c("log_ingreso", "ems"), ])

  tibble(
    modelo = c("within-between", "efectos fijos de estado"),
    r2     = c(summary(m_wb)$r.squared, summary(m_fe)$r.squared)
  )
}

# How much of each state's baseline is structure and how much is history
#   efecto     the state's effect in the fixed-effects model (plot 7's red
#              diamond): everything about the state, relative to the average
#   historia   what is left of that effect after the state's average income
#              and education: path dependence, governors, machines, or
#              anything else at the state level
{
  estados <- mw %>%
    distinct(state_name, ing_estado, ems_estado) %>%
    inner_join(estado_efecto, by = "state_name")

  m_estados <- lm(efecto ~ ing_estado + ems_estado, data = estados)
  print(summary(m_estados)$r.squared)   # share of the state baselines that is structure

  estados <- estados %>%
    mutate(historia = resid(m_estados)) %>%
    arrange(desc(historia))

  estados %>%
    transmute(state_name,
              efecto     = round(efecto, 1),
              historia   = round(historia, 1),
              ingreso    = round(exp(ing_estado)),
              ems_estado = round(ems_estado, 1))
}

# Plot 8: state effect vs what is left as history
# Grey dot = the state's effect; red/blue dot = the part its income and
# education do not explain. The bar is what structure accounts for: a bar
# toward 0 means income and education explain much of the state (Tabasco);
# a short bar means its baseline is mostly history (Oaxaca); a bar away from
# 0 or across it means structure predicts the opposite of how the state votes
# (Baja California, a rich state that votes MORENA)
{
  p_historia <- estados %>%
    mutate(state_name = reorder(state_name, historia)) %>%
    ggplot(aes(y = state_name)) +
    geom_vline(xintercept = 0, color = "grey60") +
    geom_segment(aes(x = efecto, xend = historia, yend = state_name),
                 color = "grey70") +
    geom_point(aes(x = efecto,
                   text = paste0("<b>", state_name, "</b><br>Efecto del estado: ",
                                 round(efecto, 1), " pts")),
               color = "grey50", size = 2.5) +
    geom_point(aes(x = historia, color = historia > 0,
                   text = paste0("<b>", state_name, "</b><br>Historia: ",
                                 round(historia, 1), " pts",
                                 "<br>Ingreso medio: $", scales::comma(round(exp(ing_estado))),
                                 "<br>% educación media superior: ", round(ems_estado, 1))),
               size = 3) +
    scale_color_manual(values = c(`TRUE` = "firebrick", `FALSE` = "steelblue"),
                       guide = "none") +
    labs(x = "Puntos de voto MORENA (gris: efecto del estado; color: historia)",
         y = NULL)

  ggplotly(p_historia, tooltip = "text", height = 900)
}

# Final model: state fixed effects
# y ~ log income + educación media superior + one dummy per state, weighted by
# population. summary() is the full lm output; its standard errors assume
# independent municipalities. coeftest() repeats the two coefficients with
# standard errors clustered by state, which allow municipalities in the same
# state to share unobserved shocks: use those for inference
{
  print(summary(m_fe))

  coeftest(m_fe, vcov = vcovCL(m_fe, cluster = ~ state_code))[c("log_ingreso", "ems"), ]
}

# Curvature check: does the income effect bend?
# Income is centered at its weighted mean, so the linear coefficient is the
# slope at the average municipality and the squared term is the bend. The
# Wald test (clustered) asks whether the squared term earns its place
{
  mw <- mw %>%
    mutate(ing_c = log_ingreso - weighted.mean(log_ingreso, poblacion))

  m_fe_lineal <- lm(y ~ ing_c + ems + state_code, data = mw, weights = poblacion)
  m_fe_curva  <- lm(y ~ ing_c + I(ing_c^2) + ems + state_code, data = mw, weights = poblacion)

  print(coeftest(m_fe_curva, vcov = vcovCL(m_fe_curva, cluster = ~ state_code))[c("ing_c", "I(ing_c^2)", "ems"), ])
  print(waldtest(m_fe_lineal, m_fe_curva, vcov = vcovCL(m_fe_curva, cluster = ~ state_code)))

  # Slope of income at the 10th, 50th and 90th percentile municipality
  b <- coef(m_fe_curva)
  q <- quantile(mw$ing_c, c(0.1, 0.5, 0.9))
  print(tibble(percentil = c("p10", "p50", "p90"),
               pendiente_ingreso = b["ing_c"] + 2 * b["I(ing_c^2)"] * q))

  tibble(
    modelo = c("lineal", "con curvatura"),
    r2     = c(summary(m_fe_lineal)$r.squared, summary(m_fe_curva)$r.squared),
    r2_adj = c(summary(m_fe_lineal)$adj.r.squared, summary(m_fe_curva)$adj.r.squared)
  )
}
