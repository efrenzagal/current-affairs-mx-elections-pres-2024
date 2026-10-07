# Libraries
{
  library(tidyverse)
  library(DBI)
  library(RSQLite)
  library(duckdb)
  library(pheatmap)
  library(RColorBrewer)
  library(sf)
  library(plotly)
}

# Read data
{
  setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')

  # Votes: one row per municipality x election (PRE, DIP_MR, SEN_MR 2024)
  con <- dbConnect(SQLite(), "election_data.db")
  votes_long <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/municipal_vote_share.sql"))
  dbDisconnect(con)

  # EIC 2025: one row per municipality, including POBTOT (regression weight)
  con <- dbConnect(duckdb(), "state_scorecards/data/eic2025.duckdb", read_only = TRUE)
  eic <- dbGetQuery(con, read_file("state_scorecards/analysis/queries/municipal_eic_predictors.sql"))
  dbDisconnect(con, shutdown = TRUE)
}

# Joins
# ---------------------------------------------------------------------------
# The key is cvegeo, INEGI's 5-digit municipality code: 2-digit state +
# 3-digit municipality ("09002" = Azcapotzalco).
#
#   EIC        CVE_ENT || CVE_MUN, already INEGI.
#   Votes      INE numbers municipalities its own way, so the vote query walks
#              casilla -> seccion (dim_geography.id_municipio, INE's number)
#              -> dim_municipio_map_crosswalk -> cvegeo. The crosswalk was
#              built by matching names (electoral/materialize.py):
#                exact_name   2,450 of 2,475 INE municipalities
#                unresolved   25 with only a suggested code. 19 are name
#                             variants ("GRAL. ESCOBEDO" -> General Escobedo)
#                             and are used as suggested; 6 were wrong and are
#                             fixed by hand in the query (cvegeo_overrides):
#                             Cintalapa, Jonacatepec, Ozuluama, Zontecomatlán,
#                             and the second San Juan / San Pedro Mixtepec in
#                             Oaxaca, which shared a code with the larger
#                             namesake (told apart by size)
#              The fixes live in the query, not the DB. To make them permanent,
#              add them to data/municipio_cvegeo_overrides.csv and rebuild.
#
# Expected gaps after the join:
#   EIC only   Chicomuselo and Pantelhó (Chiapas): no casillas in 2024
#              Villa de Pozos (SLP): new municipality, INE still counts it
#              inside San Luis Potosí
#   0 votes    La Reforma and Capulálpam de Méndez (Oaxaca): casillas with a
#              lista nominal but no votes, so shares are NA
#   EIC NAs    7 municipalities with too small a sample for the indicators
# ---------------------------------------------------------------------------
{
  # How each municipality reached its cvegeo
  votes_long %>%
    distinct(cvegeo, match_method) %>%
    count(match_method)

  votes <- votes_long %>%
    select(cvegeo, election_type, votos_validos, pct_coalicion_morena,
           pct_bloque_pan, pct_mc, turnout) %>%
    pivot_wider(
      names_from  = election_type,
      names_glue  = "{.value}_{tolower(election_type)}_2024",
      values_from = c(votos_validos, pct_coalicion_morena, pct_bloque_pan,
                      pct_mc, turnout)
    )

  # Who falls out of the join, and why (see the list above)
  anti_join(eic, votes, by = "cvegeo") %>% select(cvegeo, state_name, municipio)
  anti_join(votes, eic, by = "cvegeo") %>% select(cvegeo)

  municipios <- eic %>%
    inner_join(votes, by = "cvegeo") %>%
    mutate(log_ingreso = log(ingreso_trabajo_hogar)) %>%
    drop_na(pct_coalicion_morena_dip_2024, log_ingreso, pct_hog_gob)

  # One row per municipality
  stopifnot(!anyDuplicated(municipios$cvegeo))
  nrow(municipios)
}

# Map: does every municipality land where it should?
# Boundaries are INEGI municipalities keyed by CVEGEO, the same cvegeo key
{
  mun_map <- st_read("data/materialized/municipios_processed.geojson", quiet = TRUE) %>%
    select(cvegeo = CVEGEO, nombre = NOMGEO)
  state_map <- st_read("data/materialized/estados_processed.geojson", quiet = TRUE)

  # How each municipality reached the model table
  join_status <- mun_map %>%
    left_join(votes_long %>% distinct(cvegeo, .keep_all = TRUE) %>%
                select(cvegeo, match_method),
              by = "cvegeo") %>%
    left_join(municipios %>% transmute(cvegeo, en_modelo = TRUE), by = "cvegeo") %>%
    mutate(join = case_when(
      is.na(match_method) ~ "sin votos",
      is.na(en_modelo)    ~ "fuera del modelo (NA)",
      TRUE                ~ match_method
    ))

  count(st_drop_geometry(join_status), join)

  # Everything that is not an exact name match, to check by hand
  join_status %>%
    st_drop_geometry() %>%
    filter(join != "exact_name") %>%
    arrange(join, cvegeo)

  ggplot() +
    geom_sf(data = join_status, aes(fill = join), color = NA) +
    geom_sf(data = state_map, fill = NA, color = "white", linewidth = 0.2) +
    scale_fill_manual(values = c(
      exact_name              = "grey80",
      unresolved              = "steelblue",
      override                = "darkorange",
      `sin votos`             = "firebrick",
      `fuera del modelo (NA)` = "black"
    )) +
    labs(fill = NULL, title = "Cómo llegó cada municipio a la tabla") +
    theme_void()
}

# Map: MORENA coalition vote for deputies, 2024
# Pedro Ascencio Alquisiras and Cutzamala de Pinzón (Guerrero) vote ~97% MORENA
# for deputies but ~7% for senators, with every other party at zero: recorded
# that way at the casilla, not a join error
{
  vote_map <- mun_map %>%
    left_join(municipios %>% select(cvegeo, pct_coalicion_morena_dip_2024),
              by = "cvegeo")

  ggplot() +
    geom_sf(data = vote_map, aes(fill = pct_coalicion_morena_dip_2024), color = NA) +
    geom_sf(data = state_map, fill = NA, color = "white", linewidth = 0.2) +
    scale_fill_viridis_c(option = "magma", direction = -1, na.value = "grey60",
                         limits = c(0, 100)) +
    labs(fill = "% coalición\nMORENA",
         title = "Diputados MR 2024, por municipio (gris = sin dato)") +
    theme_void()
}

# Heatmap: correlations weighted by population (EIC 2025 POBTOT)
# Unweighted, Oaxaca's 570 municipalities (3% of the population) would set
# the pattern
{
  heatmap_vars <- municipios %>%
    select(pct_coalicion_morena_dip_2024, pct_coalicion_morena_sen_2024,
           pct_hog_gob, log_ingreso, escolaridad, pct_indigena,
           pct_hog_sin_alim, pct_viv_auto, pct_hog_jub, pct_hog_remesas,
           poblacion) %>%
    drop_na()

  cor_mun <- heatmap_vars %>%
    select(-poblacion) %>%
    cov.wt(wt = heatmap_vars$poblacion, cor = TRUE) %>%
    pluck("cor")

  round(cor_mun, 2)

  pheatmap(
    cor_mun,
    color           = colorRampPalette(rev(brewer.pal(7, "RdYlBu")))(100),
    breaks          = seq(-1, 1, length.out = 101),  # 0 stays at the center
    treeheight_row  = 0,
    treeheight_col  = 0
  )
}

# First regression: MORENA coalition vote for deputies on log household
# labor income, weighted by population (EIC 2025)
{
  m_ingreso <- lm(pct_coalicion_morena_dip_2024 ~ log_ingreso,
                  data = municipios, weights = poblacion)

  summary(m_ingreso)
}

# Chart: income vs vote, one bubble per municipality sized by population
# The line is the same weighted fit as m_ingreso
{
  p_ingreso <- municipios %>%
    ggplot(aes(log_ingreso, pct_coalicion_morena_dip_2024)) +
    geom_point(aes(size = poblacion,
                   text = paste0(
                     "<b>", municipio, ", ", state_name, "</b>",
                     "<br>Ingreso por trabajo del hogar: $",
                     scales::comma(ingreso_trabajo_hogar, accuracy = 1),
                     " (log ", round(log_ingreso, 2), ")",
                     "<br>% coalición MORENA, diputados: ",
                     round(pct_coalicion_morena_dip_2024, 1),
                     "<br>Población: ", scales::comma(poblacion)
                   )),
               color = "steelblue", alpha = 0.4) +
    geom_smooth(aes(weight = poblacion), method = "lm", se = FALSE,
                color = "firebrick") +
    geom_smooth() +
    scale_size_area(max_size = 15, guide = "none") +
    labs(x = "log ingreso por trabajo del hogar",
         y = "% coalición MORENA, diputados 2024")

  ggplotly(p_ingreso, tooltip = "text")
}
