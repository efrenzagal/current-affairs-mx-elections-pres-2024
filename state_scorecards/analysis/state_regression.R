# Libraries
{
  library(tidyverse)
  library(ggplot2)
  library(DBI)
  library(RSQLite)
  library(duckdb)
}

# Read data
{
  setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')

  # Votes: one row per state x election (PRE, DIP_MR, SEN_MR in 2018 and 2024)
  con <- dbConnect(SQLite(), "election_data.db")
  votes_long <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/state_vote_share.sql"))
  dbDisconnect(con)

  # EIC 2025: one row per state
  con <- dbConnect(duckdb(), "state_scorecards/data/eic2025.duckdb", read_only = TRUE)
  eic <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/state_eic_predictors.sql"))
  dbDisconnect(con, shutdown = TRUE)
}

# Clean: one row per state, every race side by side, joined to the EIC
{
  votes <- votes_long %>%
    select(state_code, election_type, year, pct_morena, pct_coalicion_morena,
           pct_bloque_pan, pct_tercer_bloque, turnout) %>%
    pivot_wider(
      names_from  = c(election_type, year),
      names_glue  = "{.value}_{tolower(election_type)}_{year}",
      values_from = c(pct_morena, pct_coalicion_morena, pct_bloque_pan,
                      pct_tercer_bloque, turnout)
    )
  
  states <- eic %>%
    inner_join(votes, by = "state_code") %>%
    mutate(
      log_ingreso = log(ingreso_trabajo_hogar),
      # Points the presidential race adds to the MORENA coalition over Congress
      premio_pre_2024 = pct_coalicion_morena_pre_2024 - pct_coalicion_morena_dip_2024
    )
  
  # 32 states, no gaps after the join
  stopifnot(nrow(states) == 32, !anyNA(states))
}

# EDA: overview
{
  glimpse(states)
  
  states %>%
    select(pct_hog_gob, ingreso_trabajo_hogar, pct_coalicion_morena_pre_2024,
           pct_coalicion_morena_dip_2024, pct_coalicion_morena_sen_2024,
           premio_pre_2024) %>%
    summary()
}

# EDA: the same coalition across races
{
  votes_long %>%
    ggplot(aes(election_type, pct_coalicion_morena, fill = factor(year))) +
    geom_boxplot() +
    labs(x = NULL, y = "% coalición MORENA, votos válidos", fill = "Año")
  
  states %>%
    ggplot(aes(pct_coalicion_morena_dip_2024, pct_coalicion_morena_pre_2024,
               label = state_name)) +
    geom_abline(linetype = "dashed") +
    geom_text(size = 3) +
    labs(x = "% coalición MORENA, diputados 2024",
         y = "% coalición MORENA, presidencial 2024")
}

# EDA: distributions of the key variables
{
  states %>%
    select(pct_hog_gob, log_ingreso, pct_coalicion_morena_dip_2024,
           pct_coalicion_morena_sen_2024) %>%
    pivot_longer(everything()) %>%
    ggplot(aes(value)) +
    geom_histogram(bins = 12) +
    facet_wrap(~ name, scales = "free")
}

# EDA: correlations with the 2024 congressional vote
{
  states %>%
    select(pct_coalicion_morena_dip_2024, pct_coalicion_morena_sen_2024,
           pct_hog_gob, log_ingreso, escolaridad, pct_indigena,
           pct_hog_sin_alim, pct_viv_auto, pct_hog_jub, pct_hog_remesas) %>%
    cor() %>%
    round(2) %>% pheatmap::pheatmap(treeheight_row = 0, treeheight_col = 0, angle_col = 45)
}

# EDA: is Tabasco driving the fit? Food insecurity vs log income
{
  tabasco_plot <- states %>%
    select(state_name, pct_coalicion_morena_dip_2024, pct_hog_sin_alim, log_ingreso) %>%
    pivot_longer(c(pct_hog_sin_alim, log_ingreso),
                 names_to = "predictor", values_to = "value") %>%
    mutate(
      tabasco   = state_name == "Tabasco",
      predictor = recode(predictor,
                         pct_hog_sin_alim = "% hogares sin acceso a alimentos",
                         log_ingreso      = "log ingreso por trabajo del hogar")
    )
  
  plotly::ggplotly(ggplot(tabasco_plot, aes(value, pct_coalicion_morena_dip_2024)) +
    geom_smooth(aes(linetype = "Todos los estados"),
                method = "lm", se = FALSE, color = "grey40") +
    geom_smooth(data = filter(tabasco_plot, !tabasco),
                aes(linetype = "Sin Tabasco"),
                method = "lm", se = FALSE, color = "grey40") +
    geom_point(aes(color = tabasco), size = 2) +
    geom_text(data = filter(tabasco_plot, tabasco),
              aes(label = state_name), vjust = -1, color = "firebrick", size = 3) +
    scale_color_manual(values = c(`FALSE` = "grey60", `TRUE` = "firebrick"),
                       guide = "none") +
    facet_wrap(~ predictor, scales = "free_x") +
    labs(x = NULL, y = "% coalición MORENA, diputados 2024", linetype = NULL) +
    theme(legend.position = "bottom"))
}


# EDA: programs vs vote, with income as color
{
  states %>%
    ggplot(aes(pct_hog_gob, pct_coalicion_morena_dip_2024,
               color = log_ingreso, label = state_name)) +
    geom_text(size = 3) +
    scale_color_viridis_c() +
    labs(x = "% hogares que reciben programas de gobierno (EIC 2025)",
         y = "% coalición MORENA, diputados 2024",
         color = "log ingreso")
  
  states %>%
    ggplot(aes(pct_hog_gob, log_ingreso, label = state_name)) +
    geom_text(size = 3) +
    labs(x = "% hogares que reciben programas de gobierno",
         y = "log ingreso por trabajo del hogar")
}

