# Final municipal regression and its diagnostics
#
#   y ~ log_ingreso + ems + superior + state, weighted by population (EIC POBTOT)
#     y          MORENA coalition share (MORENA + PT + PVEM), deputies MR 2024
#     ems        % of adults 15+ whose highest level is educación media superior
#     superior   % of adults 15+ with university or postgraduate
#     (reference: basic education or less)
#   Standard errors clustered by state.
#
# Diagnostics:
#   1. Residuals by municipality size, and residuals vs fitted values
#   2. Geographic clustering of the residuals (Moran's I) and a residual map
#   3. Residual screen: for each of 218 EIC indicators, how strongly it relates
#      to what the model leaves unexplained, and how much adding it reduces
#      the geographic clustering
#
# Same queries and join as municipal_regression.R; see its Joins section for
# how the cvegeo key works.

# Libraries
{
  library(tidyverse)
  library(DBI)
  library(RSQLite)
  library(duckdb)
  library(plotly)
  library(sandwich)
  library(lmtest)
  library(sf)
  library(Matrix)
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

  mun_map <- st_read("data/materialized/municipios_processed.geojson", quiet = TRUE) %>%
    select(cvegeo = CVEGEO)
  state_map <- st_read("data/materialized/estados_processed.geojson", quiet = TRUE)
}

# Model table: one row per municipality
{
  education <- indicators_long %>%
    filter(indicator %in% c("PCN_P15YM_EMS", "PCN_P15YM_ES", "PCN_P15YM_POS")) %>%
    select(cvegeo, indicator, value) %>%
    pivot_wider(names_from = indicator, values_from = value) %>%
    transmute(cvegeo, ems = PCN_P15YM_EMS, superior = PCN_P15YM_ES + PCN_P15YM_POS)

  municipios <- votes_long %>%
    filter(election_type == "DIP") %>%
    select(cvegeo, y = pct_coalicion_morena) %>%
    inner_join(eic %>% select(cvegeo, state_code, state_name, municipio,
                              poblacion, ingreso_trabajo_hogar),
               by = "cvegeo") %>%
    inner_join(education, by = "cvegeo") %>%
    mutate(log_ingreso = log(ingreso_trabajo_hogar)) %>%
    drop_na(y, log_ingreso, ems, superior, poblacion) %>%
    arrange(cvegeo)

  stopifnot(!anyDuplicated(municipios$cvegeo))
  nrow(municipios)
}

# Final model
# summary() assumes independent municipalities; coeftest() gives the
# state-clustered standard errors to use for inference
{
  m_final <- lm(y ~ log_ingreso + ems + superior + state_code,
                data = municipios, weights = poblacion)

  print(summary(m_final))

  coeftest(m_final, vcov = vcovCL(m_final, cluster = ~ state_code))[c("log_ingreso", "ems", "superior"), ]
}

# 2024: each pollster's final campaign poll vs. the result
{
  candidato_colors <- c(
    "Claudia Sheinbaum"    = "#8B0000",
    "Xóchitl Gálvez"       = "#1565C0",
    "Jorge Álvarez Máynez" = "#F57C00"
  )
  
  # Last candidate poll per house once campaigns opened (1 Mar 2024)
  polls_2024 <- options %>%
    filter(election_id == "PRE_2024",
           intention_level == "candidato",
           reference_date >= as.Date("2024-03-01"),
           candidato %in% names(candidato_colors)) %>%
    group_by(pollster) %>%
    filter(reference_date == max(reference_date)) %>%
    filter(poll_id == max(poll_id)) %>%            # two waves the same day: keep one
    ungroup() %>%
    mutate(candidato = factor(candidato, levels = names(candidato_colors)))
  
  # Pollsters ordered by their Sheinbaum number, so rows read the same in every panel
  pollster_order <- polls_2024 %>%
    filter(candidato == "Claudia Sheinbaum") %>%
    arrange(pct_efectiva) %>%
    pull(pollster)
  
  polls_2024 <- polls_2024 %>%
    mutate(pollster = factor(pollster, levels = pollster_order))
  
  results_2024 <- polls_2024 %>% distinct(candidato, result)
  
  p_2024 <- polls_2024 %>%
    ggplot(aes(pct_efectiva, pollster, color = candidato)) +
    geom_vline(data = results_2024, aes(xintercept = result),
               color = TEXT_DARK, linetype = "dashed", linewidth = 0.4) +
    geom_segment(aes(x = result, xend = pct_efectiva, yend = pollster),
                 color = GRAY_LIGHT, linewidth = 1.2) +
    geom_point(aes(text = str_c(pollster, " · ", reference_date,
                                "<br>", candidato,
                                "<br>Encuesta: ", fmt(pct_efectiva), "%",
                                "<br>Resultado: ", fmt(result), "%",
                                "<br>Error: ", fmt(error), " pts")),
               size = 2.8) +
    geom_text(aes(label = fmt(pct_efectiva, 0),
                  hjust = if_else(pct_efectiva >= result, -0.6, 1.6)),
              size = 2.9, show.legend = FALSE) +
    geom_text(data = results_2024,
              aes(x = result, y = Inf, label = str_c("Resultado ", fmt(result), "%")),
              vjust = -0.4, size = 3, fontface = "bold", color = TEXT_DARK,
              inherit.aes = FALSE) +
    facet_wrap(~candidato, scales = "free_x") +
    scale_color_manual(values = candidato_colors, guide = "none") +
    scale_x_continuous(labels = label_number(suffix = "%"),
                       expand = expansion(mult = 0.15)) +
    coord_cartesian(clip = "off") +
    labs(title = "Presidencial 2024: última encuesta de cada casa vs. resultado",
         subtitle = "Preferencia efectiva desde el 1 de marzo; línea punteada = resultado (votación válida)",
         x = NULL, y = NULL) +
    theme_polls +
    theme(strip.text = element_text(face = "bold", margin = margin(b = 14)),
          panel.spacing.x = unit(1.5, "lines"))
  
  p_2024                                     # static, labels next to each dot
  show_plot(p_2024, "09_presidencial_2024")  # interactive, hover for date and error
}

# Diagnostic 1: residuals by municipality size, and vs fitted values
# Small municipalities are noisier; a pattern against fitted values would mean
# the model misses curvature
{
  municipios <- municipios %>%
    mutate(ajustado = fitted(m_final),
           residuo  = resid(m_final),
           hover = paste0("<b>", municipio, ", ", state_name, "</b>",
                          "<br>% coalición MORENA: ", round(y, 1),
                          "<br>Predicho: ", round(ajustado, 1),
                          "<br>Residuo: ", round(residuo, 1),
                          "<br>Población: ", scales::comma(poblacion)))

  print(
    municipios %>%
      mutate(tamano = cut(poblacion, c(0, 5e3, 2e4, 1e5, 5e5, Inf),
                          labels = c("<5k", "5-20k", "20-100k", "100-500k", ">500k"))) %>%
      group_by(tamano) %>%
      summarise(n = n(), sd_residuo = sd(residuo), .groups = "drop")
  )

  p_ajuste <- ggplot(municipios, aes(ajustado, residuo)) +
    geom_hline(yintercept = 0, color = "grey60") +
    geom_point(aes(size = poblacion, text = hover), color = "steelblue", alpha = 0.3) +
    geom_smooth(aes(weight = poblacion), method = "loess", formula = y ~ x,
                se = FALSE, color = "firebrick") +
    scale_size_area(max_size = 12, guide = "none") +
    labs(x = "% coalición MORENA predicho", y = "Residuo")

  ggplotly(p_ajuste, tooltip = "text")
}

# Diagnostic 2: geographic clustering of the residuals
# ---------------------------------------------------------------------------
# Neighbors: municipalities whose boundaries are within 1 km (the simplified
# boundaries leave small gaps, so "touching" misses real neighbors).
# W is row-standardized: each municipality's neighbors weigh 1 / their count.
# Moran's I compares each residual with its neighbors' average: ~0 if
# unrelated, toward 1 if neighbors err in the same direction. The baseline
# for "unrelated" comes from shuffling the residuals across the map.
# ---------------------------------------------------------------------------
{
  geo <- mun_map %>%
    filter(cvegeo %in% municipios$cvegeo) %>%
    arrange(cvegeo) %>%
    st_make_valid() %>%
    st_transform(6372)   # INEGI Lambert conformal, meters
  stopifnot(identical(geo$cvegeo, municipios$cvegeo))

  neighbors <- st_is_within_distance(geo, geo, dist = 1000)
  W_all <- sparseMatrix(i = rep(seq_along(neighbors), lengths(neighbors)),
                        j = unlist(neighbors), x = 1,
                        dims = c(nrow(geo), nrow(geo)))
  diag(W_all) <- 0
  W_all <- drop0(W_all)

  # Moran's I on any subset of municipalities (keep = logical vector)
  moran_i <- function(z, keep = rep(TRUE, length(z))) {
    W <- W_all[keep, keep]
    W <- Diagonal(x = 1 / pmax(rowSums(W), 1)) %*% W
    z <- z - mean(z)
    n_con_vecinos <- sum(rowSums(W) > 0)
    as.numeric((length(z) / n_con_vecinos) * (t(z) %*% W %*% z) / sum(z^2))
  }

  set.seed(1)
  azar <- replicate(99, moran_i(sample(municipios$residuo)))

  tibble(
    moran_residuo  = moran_i(municipios$residuo),
    azar_maximo    = max(azar),
    vecinos_median = median(lengths(neighbors)) - 1
  )
}

# Residual map: where does the model miss?
# Red = more MORENA than predicted, blue = less
{
  ggplot() +
    geom_sf(data = mun_map %>% left_join(municipios %>% select(cvegeo, residuo), by = "cvegeo"),
            aes(fill = residuo), color = NA) +
    geom_sf(data = state_map, fill = NA, color = "grey30", linewidth = 0.2) +
    scale_fill_gradient2(low = "steelblue", mid = "grey95", high = "firebrick",
                         midpoint = 0, limits = c(-40, 40), oob = scales::squish,
                         na.value = "grey70") +
    labs(fill = "Residuo\n(pts)", title = "Residuo del modelo final, diputados 2024") +
    theme_void()
}

# Diagnostic 3: residual screen over every EIC indicator
# ---------------------------------------------------------------------------
# For each of the 218 indicators, on the municipalities where it is not NA:
#   cor_residuo      partial correlation with the vote given the final model:
#                    correlation between the model's residual and the part of
#                    the indicator that income, education and state do not
#                    already explain (population-weighted)
#   r2_extra         R² gained by adding the indicator to the final model
#   moran_base       Moran's I of the final model's residuals on that subset
#   moran_con        Moran's I after adding the indicator
#   baja_moran       moran_base - moran_con: how much of the neighbor pattern
#                    the indicator captures
# Takes two to three minutes.
# ---------------------------------------------------------------------------
{
  wcor <- function(a, b, w) cov.wt(cbind(a, b), wt = w, cor = TRUE)$cor[1, 2]
  controls <- "log_ingreso + ems + superior + state_code"

  screen_residuo <- function(d, keep) {
    if (sd(d$value) == 0) return(tibble(n = nrow(d)))
    w      <- d$poblacion
    m_base <- lm(as.formula(paste("y ~", controls)), data = d, weights = w)
    m_con  <- lm(as.formula(paste("y ~ value +", controls)), data = d, weights = w)
    r_x    <- resid(lm(as.formula(paste("value ~", controls)), data = d, weights = w))
    tibble(
      n           = nrow(d),
      cor_residuo = wcor(resid(m_base), r_x, w),
      r2_extra    = summary(m_con)$r.squared - summary(m_base)$r.squared,
      moran_base  = moran_i(resid(m_base), keep),
      moran_con   = moran_i(resid(m_con), keep),
      baja_moran  = moran_base - moran_con
    )
  }

  residual_screen <- indicators_long %>%
    filter(!indicator %in% c("PCN_P15YM_EMS", "PCN_P15YM_ES", "PCN_P15YM_POS")) %>%
    group_by(indicator, section, name) %>%
    group_modify(function(ind, key) {
      d <- municipios %>%
        select(cvegeo, y, log_ingreso, ems, superior, state_code, poblacion) %>%
        inner_join(ind %>% select(cvegeo, value), by = "cvegeo")
      keep <- municipios$cvegeo %in% d$cvegeo
      screen_residuo(d, keep)
    }) %>%
    ungroup()

  print(
    residual_screen %>%
      slice_max(abs(cor_residuo), n = 15) %>%
      select(indicator, name, cor_residuo, r2_extra, baja_moran) %>%
      mutate(across(where(is.numeric), ~ round(.x, 3)))
  )

  # The ones that capture the most of the neighbor pattern
  residual_screen %>%
    slice_max(baja_moran, n = 15) %>%
    select(indicator, name, cor_residuo, r2_extra, moran_base, moran_con) %>%
    mutate(across(where(is.numeric), ~ round(.x, 3)))
}

# Chart: link to the residual (x) vs reduction in clustering (y)
# Useful indicators sit far from 0 on x; the ones that explain the neighbor
# pattern sit high on y
{
  p_residual_screen <- residual_screen %>%
    mutate(hover = paste0(
      "<b>", indicator, "</b><br>",
      str_replace_all(str_wrap(name, 60), "\n", "<br>"),
      "<br>Sección: ", section,
      "<br>Corr. con el residuo: ", round(cor_residuo, 3),
      "<br>R² adicional: ", round(r2_extra, 3),
      "<br>Moran: ", round(moran_base, 3), " → ", round(moran_con, 3)
    )) %>%
    ggplot(aes(cor_residuo, baja_moran, color = r2_extra, text = hover)) +
    geom_hline(yintercept = 0, color = "grey60") +
    geom_vline(xintercept = 0, color = "grey60") +
    geom_point(size = 2) +
    scale_color_viridis_c() +
    labs(x = "Correlación con el residuo del modelo final",
         y = "Reducción del Moran's I al agregarlo",
         color = "R² adicional")

  ggplotly(p_residual_screen, tooltip = "text")
  
  write.csv(residual_screen, 'state_scorecards/analysis/residual_screen.csv')
}
