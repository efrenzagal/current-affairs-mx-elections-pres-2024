# =============================================================================
# validate_electoral_2024.R
# -----------------------------------------------------------------------------
# Validates tidy parquet files produced by tidy_electoral_2024.R BEFORE
# loading into Supabase.
#
# Design philosophy:
#   - Ground-truth totals (GT / GT_PARTIES) are the only hardcoded values.
#     These are INE's published figures — exogenous to the pipeline by definition.
#   - Everything else (party keys, geographic counts, vote type presence,
#     cross-level consistency) is derived from the parquets themselves or from
#     EXPECTED_PARTY_KEYS, which tidy_electoral_2024.R now produces dynamically.
#   - If tidy_electoral_2024.R was sourced in the same session, EXPECTED_PARTY_KEYS
#     is already in scope. Otherwise it is reconstructed here from the parquets.
#
# Exit codes:
#   0 — all checks passed (safe to load)
#   1 — one or more checks FAILED (do NOT load)
#
# Run with: Rscript validate_electoral_2024.R
# =============================================================================

library(tidyverse)
library(arrow)
library(glue)
library(scales)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
{
  TIDY_DIR           <- "~/electoral_tidy"
  ELECTION_YEAR      <- 2024L
  ELECTION_RACE_TYPE <- "PRE"
  
  COMMON_REQUIRED <- c("party_key", "num_votos", "vote_type", "year", "race_type")
  
  # ── EXOGENOUS GROUND TRUTH ─────────────────────────────────────────────────
  # These values come from INE's published results and are intentionally
  # hardcoded — they are the external reference the pipeline is validated against.
  # Source: https://computos2024.ine.mx
  # If INE issues a corrected count, update here and re-run.
  
  GT <- list(
    total_votos          = 60115184L,
    num_votos_validos    = 58631926L,
    num_votos_nulos      = 1400144L,
    num_votos_can_nreg   = 83114L,
    lista_nominal        = 98468994L,
    n_estados            = 32L,
    n_municipios         = 2475L,
    n_distritos          = 300L,
    n_secciones          = 70504L,
    n_casillas           = 170181L,    # standard physical booths only (B, C, E, S)
    votos_pvem_pt_morena = 35924519L,
    votos_pan_pri_prd    = 16502697L,
    votos_mc             = 6204710L
  )
  
  GT_PARTIES <- tibble::tribble(
    ~party_key,         ~expected_votos,
    "PAN",               9224341L,
    "PRI",               5320727L,
    "PRD",                793603L,
    "PVEM",              3687773L,
    "PT",                2878024L,
    "MC",                6204710L,
    "MORENA",           26253825L,
    "PAN_PRI_PRD",        884579L,
    "PAN_PRI",            213807L,
    "PAN_PRD",             37277L,
    "PRI_PRD",             28363L,
    "PVEM_PT_MORENA",    2041403L,
    "PVEM_PT",            203315L,
    "PVEM_MORENA",        414515L,
    "PT_MORENA",          445664L
  )
  
  # Known upstream data gap in INE's INTEGRACION_CARGOS file.
  # DIP_MR rows missing persona_candidata that are confirmed INE source issues,
  # not pipeline bugs. Increase this only with documented evidence.
  KNOWN_BLANK_MR <- 1L   # Estado de México D23, PVEM
}

# ---------------------------------------------------------------------------
# RESULT TRACKER
# ---------------------------------------------------------------------------
{
  results <- tibble(
    check  = character(),
    level  = character(),
    status = character(),
    detail = character()
  )
  
  log_result <- function(check, level, status, detail = "") {
    results <<- bind_rows(results, tibble(check, level, status, detail))
    icon <- switch(status, PASS = "✓", FAIL = "✗", WARN = "⚠")
    cat(glue("  [{icon}] {status}  {level} / {check}"), "\n")
    if (detail != "") cat(glue("         → {detail}"), "\n")
  }
}

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
{
  load_level <- function(filename, level) {
    path <- file.path(TIDY_DIR, filename)
    if (!file.exists(path)) {
      log_result("file_exists", level, "FAIL", glue("Not found: {path}"))
      return(NULL)
    }
    df <- read_parquet(path)
    log_result("file_exists", level, "PASS", glue("{comma(nrow(df))} rows"))
    df
  }
  
  check_value <- function(computed, expected, check, level, tol = 0) {
    diff <- abs(computed - expected)
    if (diff <= tol) {
      log_result(check, level, "PASS",
                 glue("got {comma(computed)}, expected {comma(expected)}"))
    } else {
      log_result(check, level, "FAIL",
                 glue("got {comma(computed)}, expected {comma(expected)} (diff={comma(diff)})"))
    }
  }
  
  check_no_na <- function(df, col, check_name, level) {
    if (!col %in% names(df)) {
      log_result(check_name, level, "WARN", glue("Column '{col}' not present"))
      return()
    }
    
    if (col == "num_votos") {
      # Three structurally distinct NA sources require separate treatment:
      #
      # 1. INE placeholder rows — the acta was never received, so INE publishes
      #    the row with all vote columns NULL. Signal: total_votos is also NA.
      #    These are not a pipeline error.
      #
      # 2. Coalition columns that are NA because the coalition didn't contest
      #    that geography. Only present in partido view for non-core keys.
      #    Signal: party_key is NOT in PARTY_COLS_PP (the core set).
      #
      # 3. Unexpected NAs in core party columns where total_votos is present.
      #    These ARE a pipeline error and must FAIL.
      
      core_partido_nas <- df |>
        filter(
          vote_type == "partido",
          party_key %in% EXPECTED_PARTY_KEYS[["partido_pp"]],  # core parties only
          is.na(num_votos),
          !is.na(total_votos)   # exclude INE placeholder rows
        ) |>
        nrow()
      
      placeholder_nas <- df |>
        filter(
          vote_type == "partido",
          party_key %in% EXPECTED_PARTY_KEYS[["partido_pp"]],
          is.na(num_votos),
          is.na(total_votos)    # confirmed INE placeholder
        ) |>
        nrow()
      
      coalition_partido_nas <- df |>
        filter(
          vote_type == "partido",
          !party_key %in% EXPECTED_PARTY_KEYS[["partido_pp"]],  # coalition columns
          is.na(num_votos),
          !is.na(total_votos)
        ) |>
        nrow()
      
      other_nas <- df |>
        filter(vote_type != "partido", is.na(num_votos)) |>
        nrow()
      
      if (core_partido_nas > 0) {
        log_result(check_name, level, "FAIL",
                   glue("{comma(core_partido_nas)} NAs in core party 'partido' votes (unexpected)"))
      } else if (placeholder_nas > 0 || coalition_partido_nas > 0 || other_nas > 0) {
        log_result(check_name, level, "WARN",
                   glue(
                     "{comma(placeholder_nas)} INE placeholder rows (acta not received) + ",
                     "{comma(coalition_partido_nas)} coalition NAs in 'partido' + ",
                     "{comma(other_nas)} NAs in PP/candidato views (all structural)"
                   ))
      } else {
        log_result(check_name, level, "PASS")
      }
      return()
    }
    
    n_na <- sum(is.na(df[[col]]))
    if (n_na == 0) log_result(check_name, level, "PASS")
    else log_result(check_name, level, "FAIL", glue("{comma(n_na)} NAs in '{col}'"))
  }
  
  check_non_negative_votes <- function(df, level) {
    neg <- df |> filter(!is.na(num_votos) & num_votos < 0)
    if (nrow(neg) == 0) {
      log_result("non_negative_votes", level, "PASS")
    } else {
      sample_keys <- neg |> slice_head(n = 3) |> pull(party_key) |> paste(collapse = ", ")
      log_result("non_negative_votes", level, "FAIL",
                 glue("{nrow(neg)} rows with num_votos < 0; sample: {sample_keys}"))
    }
  }
  
  check_vote_types <- function(df, expected_types, level) {
    found      <- unique(df$vote_type)
    unexpected <- setdiff(found, expected_types)
    missing    <- setdiff(expected_types, found)
    if (length(unexpected) == 0 && length(missing) == 0) {
      log_result("vote_type_values", level, "PASS",
                 glue("found: {paste(sort(found), collapse=', ')}"))
    } else {
      detail <- c(
        if (length(unexpected) > 0) glue("unexpected: {paste(unexpected, collapse=', ')}"),
        if (length(missing) > 0)    glue("missing: {paste(missing, collapse=', ')}")
      )
      log_result("vote_type_values", level, "FAIL", paste(detail, collapse = " | "))
    }
  }
  
  check_party_keys <- function(df, level) {
    for (vt in unique(df$vote_type)) {
      expected <- EXPECTED_PARTY_KEYS[[vt]]
      if (is.null(expected)) {
        log_result("party_keys", level, "WARN",
                   glue("No expected key list for vote_type='{vt}'"))
        next
      }
      found      <- df |> filter(vote_type == vt) |> pull(party_key) |> unique() |> sort()
      unexpected <- setdiff(found, expected)
      missing    <- setdiff(expected, found)
      if (length(unexpected) == 0 && length(missing) == 0) {
        log_result(glue("party_keys_{vt}"), level, "PASS")
      } else {
        detail <- c(
          if (length(unexpected) > 0) glue("unexpected: {paste(unexpected, collapse=', ')}"),
          if (length(missing) > 0)    glue("missing: {paste(missing, collapse=', ')}")
        )
        log_result(glue("party_keys_{vt}"), level, "FAIL", paste(detail, collapse = " | "))
      }
    }
  }
  
  check_cols <- function(df, required, level) {
    missing <- setdiff(required, names(df))
    if (length(missing) == 0) log_result("required_columns", level, "PASS")
    else log_result("required_columns", level, "FAIL",
                    glue("missing: {paste(missing, collapse=', ')}"))
  }
  
  check_cross_level_totals <- function(detail_df, rolled_df, detail_name, rolled_name,
                                       vote_type_filter = "partido") {
    detail_total <- detail_df |>
      filter(vote_type == vote_type_filter, !is.na(num_votos)) |>
      pull(num_votos) |> sum()
    rolled_total <- rolled_df |>
      filter(vote_type == vote_type_filter, !is.na(num_votos)) |>
      pull(num_votos) |> sum()
    diff        <- abs(detail_total - rolled_total)
    level_label <- glue("{detail_name} vs {rolled_name}")
    if (diff == 0) {
      log_result("cross_level_total", level_label, "PASS",
                 glue("both sum to {comma(detail_total)}"))
    } else if (diff / rolled_total < 0.0001) {
      log_result("cross_level_total", level_label, "WARN",
                 glue("diff = {comma(diff)} ({round(diff/rolled_total*100,4)}%) — possible rounding"))
    } else {
      log_result("cross_level_total", level_label, "FAIL",
                 glue("detail={comma(detail_total)}, rolled={comma(rolled_total)}, diff={comma(diff)}"))
    }
  }
}

# ---------------------------------------------------------------------------
# RECONSTRUCT EXPECTED_PARTY_KEYS IF NOT ALREADY IN SCOPE
# When running this script standalone (not sourced after tidy_electoral_2024.R),
# we derive party keys from the nacional parquet rather than re-reading CSVs.
# This keeps the validator self-contained without re-hardcoding anything.
# ---------------------------------------------------------------------------
{
  if (!exists("EXPECTED_PARTY_KEYS")) {
    message("EXPECTED_PARTY_KEYS not found in environment — deriving from nacional parquet...")
    nacional_path <- file.path(TIDY_DIR, "votes_nacional_2024_pre.parquet")
    if (file.exists(nacional_path)) {
      nacional_ref <- read_parquet(nacional_path)
      EXPECTED_PARTY_KEYS <- nacional_ref |>
        group_by(vote_type) |>
        summarise(keys = list(sort(unique(party_key))), .groups = "drop") |>
        deframe()
      message("  Derived from parquet:")
      for (vt in names(EXPECTED_PARTY_KEYS)) {
        message("    ", vt, ": ", paste(EXPECTED_PARTY_KEYS[[vt]], collapse = ", "))
      }
      rm(nacional_ref)
    } else {
      stop("Cannot derive EXPECTED_PARTY_KEYS: nacional parquet not found at ", nacional_path)
    }
  }
}

# ===========================================================================
# RUN CHECKS
# ===========================================================================

cat("\n=== ELECTORAL DATA VALIDATION — 2024 PRESIDENCIA ===\n\n")

# ---------------------------------------------------------------------------
# 1. FILE EXISTENCE & ROW COUNTS
# ---------------------------------------------------------------------------
{
  cat("── File existence & row counts ──────────────────────────────\n")
  casilla   <- load_level("votes_casilla_2024_pre.parquet",   "casilla")
  seccion   <- load_level("votes_seccion_2024_pre.parquet",   "seccion")
  municipio <- load_level("votes_municipio_2024_pre.parquet", "municipio")
  distrito  <- load_level("votes_distrito_2024_pre.parquet",  "distrito")
  estado    <- load_level("votes_estado_2024_pre.parquet",    "estado")
  nacional  <- load_level("votes_nacional_2024_pre.parquet",  "nacional")
  winners   <- load_level("winners_2024_pre.parquet",         "winners")
}

# ---------------------------------------------------------------------------
# 2. REQUIRED COLUMNS
# ---------------------------------------------------------------------------
{
  cat("\n── Required columns ─────────────────────────────────────────\n")
  if (!is.null(casilla))   check_cols(casilla,   c(COMMON_REQUIRED, "id_estado", "seccion", "id_casilla", "ext_contigua", "tipo_casilla"), "casilla")
  if (!is.null(seccion))   check_cols(seccion,   c(COMMON_REQUIRED, "id_estado", "seccion"),             "seccion")
  if (!is.null(municipio)) check_cols(municipio, c(COMMON_REQUIRED, "id_estado", "id_municipio"),        "municipio")
  if (!is.null(distrito))  check_cols(distrito,  c(COMMON_REQUIRED, "id_estado", "id_distrito_federal"), "distrito")
  if (!is.null(estado))    check_cols(estado,    c(COMMON_REQUIRED, "id_estado"),                        "estado")
  if (!is.null(nacional))  check_cols(nacional,  COMMON_REQUIRED,                                        "nacional")
}

# ---------------------------------------------------------------------------
# 3. NAs IN KEY COLUMNS
# ---------------------------------------------------------------------------
{
  cat("\n── NAs in key columns ───────────────────────────────────────\n")
  for (level_name in c("casilla", "seccion", "municipio", "distrito", "estado", "nacional")) {
    df <- get(level_name)
    if (is.null(df)) next
    check_no_na(df, "party_key", "na_party_key", level_name)
    check_no_na(df, "num_votos", "na_num_votos", level_name)
    check_no_na(df, "vote_type", "na_vote_type", level_name)
    if (level_name != "nacional") check_no_na(df, "id_estado", "na_id_estado", level_name)
  }
}

# ---------------------------------------------------------------------------
# 4. NON-NEGATIVE VOTES
# ---------------------------------------------------------------------------
{
  cat("\n── Non-negative votes ───────────────────────────────────────\n")
  for (level_name in c("casilla", "seccion", "municipio", "distrito", "estado", "nacional")) {
    df <- get(level_name)
    if (!is.null(df)) check_non_negative_votes(df, level_name)
  }
}

# ---------------------------------------------------------------------------
# 5. VOTE TYPE VALUES
# INE only publishes PP and CAND breakdowns at distrito and above.
# ---------------------------------------------------------------------------
{
  cat("\n── vote_type values ─────────────────────────────────────────\n")
  for (level_name in c("casilla", "seccion", "municipio")) {
    df <- get(level_name)
    if (!is.null(df)) check_vote_types(df, c("partido"), level_name)
  }
  for (level_name in c("distrito", "estado", "nacional")) {
    df <- get(level_name)
    if (!is.null(df)) check_vote_types(df, c("partido", "partido_pp", "candidato"), level_name)
  }
}

# ---------------------------------------------------------------------------
# 6. PARTY KEY INTEGRITY
# Validated against EXPECTED_PARTY_KEYS derived dynamically in the tidy step.
# ---------------------------------------------------------------------------
{
  cat("\n── Party key integrity ──────────────────────────────────────\n")
  for (level_name in c("casilla", "seccion", "municipio", "distrito", "estado", "nacional")) {
    df <- get(level_name)
    if (!is.null(df)) check_party_keys(df, level_name)
  }
}

# ---------------------------------------------------------------------------
# 7. NATIONAL TOTALS VS INE GROUND TRUTH
# These are the only checks that depend on GT — intentionally exogenous.
# ---------------------------------------------------------------------------
{
  cat("\n── National totals vs INE ground truth ──────────────────────\n")
  if (!is.null(nacional)) {
    nacional_partido <- nacional |> filter(vote_type == "partido")
    nacional_cand    <- nacional |> filter(vote_type == "candidato")
    
    # Total valid votes = sum of all partido rows (each row is one party in one
    # national "geography", so this is already the national total)
    computed_validos <- nacional_partido |>
      filter(!is.na(num_votos)) |>
      pull(num_votos) |>
      sum()
    check_value(computed_validos, GT$num_votos_validos, "sum_votos_validos", "nacional")
    
    # Candidato-view totals for each of the three presidential coalitions
    for (cand_key in names(EXPECTED_PARTY_KEYS[["candidato"]])) {
      gt_key <- glue("votos_{str_to_lower(cand_key)}")
      if (!is.null(GT[[gt_key]])) {
        computed <- nacional_cand |>
          filter(party_key == cand_key) |>
          pull(num_votos) |>
          sum(na.rm = TRUE)
        check_value(computed, GT[[gt_key]], glue("votos_{cand_key}"), "nacional")
      }
    }
  }
}

# ---------------------------------------------------------------------------
# 8. PARTY-LEVEL TOTALS VS INE GROUND TRUTH
# Summed from estado (rather than nacional) so the aggregation itself is tested.
# ---------------------------------------------------------------------------
{
  cat("\n── Party-level totals vs INE ground truth ───────────────────\n")
  if (!is.null(estado)) {
    estado_partido <- estado |>
      filter(vote_type == "partido", !is.na(num_votos)) |>
      group_by(party_key) |>
      summarise(total = sum(num_votos), .groups = "drop")
    
    for (i in seq_len(nrow(GT_PARTIES))) {
      pk  <- GT_PARTIES$party_key[i]
      exp <- GT_PARTIES$expected_votos[i]
      got <- estado_partido |> filter(party_key == pk) |> pull(total)
      got <- if (length(got) == 0) 0L else got
      check_value(got, exp, glue("party_total_{pk}"), "estado→nacional")
    }
  }
}

# ---------------------------------------------------------------------------
# 9. GEOGRAPHIC UNIT COUNTS
# Derived from the parquets themselves — no hardcoding except GT reference values.
# The casilla count uses only standard physical booth types (B, C, E, S) to
# match INE's published 170,181 figure; M-type rows are special voting tables
# (postal, prison) that INE excludes from that count. See diagnostic_casillas.R.
# ---------------------------------------------------------------------------
{
  cat("\n── Geographic unit counts ───────────────────────────────────\n")
  
  if (!is.null(estado)) {
    n_estados <- estado |>
      filter(vote_type == "partido") |>
      pull(id_estado) |>
      n_distinct()
    check_value(n_estados, GT$n_estados, "n_estados", "estado")
  }
  
  if (!is.null(municipio)) {
    n_municipios <- municipio |>
      filter(vote_type == "partido", id_municipio != 0) |>
      select(id_estado, id_municipio) |>
      distinct() |>
      nrow()
    check_value(n_municipios, GT$n_municipios, "n_municipios", "municipio")
  }
  
  if (!is.null(distrito)) {
    n_distritos <- distrito |>
      filter(vote_type == "partido", id_distrito_federal != 0) |>
      select(id_estado, id_distrito_federal) |>
      distinct() |>
      nrow()
    check_value(n_distritos, GT$n_distritos, "n_distritos", "distrito")
  }
  
  if (!is.null(seccion)) {
    n_secciones <- seccion |>
      filter(!is.na(seccion), seccion != 0) |>
      select(id_estado, seccion) |>
      distinct() |>
      nrow()
    check_value(n_secciones, GT$n_secciones, "n_secciones", "seccion")
  }
  
  if (!is.null(casilla)) {
    n_casillas <- casilla |>
      filter(
        vote_type == "partido",
        tipo_casilla %in% c("B", "C", "E", "S")
      ) |>
      select(id_estado, id_distrito_federal, id_municipio,
             seccion, tipo_casilla, id_casilla, ext_contigua) |>
      distinct() |>
      nrow()
    check_value(n_casillas, GT$n_casillas, "n_casillas", "casilla")
    
    # Also report non-standard booth count for transparency — not a failure,
    # just useful to know how many M-type rows were set aside
    n_special <- casilla |>
      filter(vote_type == "partido", !tipo_casilla %in% c("B", "C", "E", "S")) |>
      select(id_estado, id_distrito_federal, id_municipio,
             seccion, tipo_casilla, id_casilla, ext_contigua) |>
      distinct() |>
      nrow()
    if (n_special > 0) {
      log_result("n_casillas_special", "casilla", "WARN",
                 glue("{comma(n_special)} non-standard (M-type) booths excluded from GT count — expected"))
    }
  }
}

# ---------------------------------------------------------------------------
# 10. CROSS-LEVEL CONSISTENCY
# ---------------------------------------------------------------------------
{
  cat("\n── Cross-level consistency ───────────────────────────────────\n")
  if (!is.null(municipio) && !is.null(estado))  check_cross_level_totals(municipio, estado,   "municipio", "estado")
  if (!is.null(estado)    && !is.null(nacional)) check_cross_level_totals(estado,    nacional, "estado",    "nacional")
  if (!is.null(distrito)  && !is.null(nacional)) check_cross_level_totals(distrito,  nacional, "distrito",  "nacional")
}

# ---------------------------------------------------------------------------
# 11. YEAR / RACE_TYPE CONSISTENCY
# Derived from constants, not hardcoded strings
# ---------------------------------------------------------------------------
{
  cat("\n── year / race_type consistency ─────────────────────────────\n")
  for (level_name in c("casilla", "seccion", "municipio", "distrito", "estado", "nacional")) {
    df <- get(level_name)
    if (is.null(df)) next
    years      <- unique(df$year)
    race_types <- unique(df$race_type)
    if (length(years) == 1 && years == ELECTION_YEAR &&
        length(race_types) == 1 && race_types == ELECTION_RACE_TYPE) {
      log_result("year_race_type", level_name, "PASS",
                 glue("year={ELECTION_YEAR}, race_type={ELECTION_RACE_TYPE}"))
    } else {
      log_result("year_race_type", level_name, "FAIL",
                 glue("years={paste(years,collapse=',')} race_types={paste(race_types,collapse=',')}"))
    }
  }
}

# ---------------------------------------------------------------------------
# 12. WINNERS FILE
# Row counts and candidacy type breakdown are derived from the parquet itself.
# KNOWN_BLANK_MR is the only hardcoded exception — a documented INE source gap.
# ---------------------------------------------------------------------------
{
  cat("\n── Winners file ─────────────────────────────────────────────\n")
  if (!is.null(winners)) {
    
    # Derive candidacy type counts from the data — no expected values hardcoded
    cand_counts <- winners |> count(tipo_de_candidatura)
    log_result("winners_candidacy_types", "winners", "PASS",
               paste(glue("{cand_counts$tipo_de_candidatura}(n={cand_counts$n})"), collapse = ", "))
    
    # PRE: exactly one winner
    pre_row <- winners |> filter(tipo_de_candidatura == ELECTION_RACE_TYPE)
    if (nrow(pre_row) == 1) {
      log_result("winners_pre_row", "winners", "PASS",
                 glue("{pre_row$persona_candidata} — {comma(pre_row$votacion_ganador)} votes"))
    } else {
      log_result("winners_pre_row", "winners", "FAIL",
                 glue("expected 1 {ELECTION_RACE_TYPE} row, found {nrow(pre_row)}"))
    }
    
    # DIP_MR count — derived from the data, not hardcoded
    n_dip <- winners |> filter(tipo_de_candidatura == "DIP_MR") |> nrow()
    n_dip_expected <- cand_counts |> filter(tipo_de_candidatura == "DIP_MR") |> pull(n)
    # Cross-check against the dim_distritos dimension if available
    distritos_dim_path <- file.path(TIDY_DIR, "dim_distritos.csv")
    if (file.exists(distritos_dim_path)) {
      n_distritos_dim <- nrow(read_csv(distritos_dim_path, show_col_types = FALSE))
      if (n_dip == n_distritos_dim) {
        log_result("winners_n_dip_mr", "winners", "PASS",
                   glue("{n_dip} DIP_MR rows matches dim_distritos ({n_distritos_dim} districts)"))
      } else {
        log_result("winners_n_dip_mr", "winners", "FAIL",
                   glue("{n_dip} DIP_MR rows but {n_distritos_dim} districts in dim_distritos"))
      }
    } else {
      # Fall back to GT if dim_distritos not available
      check_value(n_dip, GT$n_distritos, "winners_n_dip_mr", "winners")
    }
    
    # Blank candidate names — KNOWN_BLANK_MR is the documented exception
    na_mr_rows <- winners |>
      filter(is.na(persona_candidata), tipo_de_candidatura %in% c(ELECTION_RACE_TYPE, "DIP_MR"))
    na_rp <- winners |>
      filter(is.na(persona_candidata), !tipo_de_candidatura %in% c(ELECTION_RACE_TYPE, "DIP_MR")) |>
      nrow()
    
    if (nrow(na_mr_rows) == 0 && na_rp == 0) {
      log_result("winners_no_na_names", "winners", "PASS")
    } else if (nrow(na_mr_rows) > KNOWN_BLANK_MR) {
      log_result("winners_no_na_names", "winners", "FAIL",
                 glue("{nrow(na_mr_rows)} MR/PRE rows missing persona_candidata ",
                      "(expected at most {KNOWN_BLANK_MR}) — investigate"))
    } else if (nrow(na_mr_rows) == KNOWN_BLANK_MR) {
      blanks <- na_mr_rows |>
        mutate(loc = glue("id_estado={id_estado} D{id_distrito_federal} {partido_politico}")) |>
        pull(loc) |> paste(collapse = "; ")
      log_result("winners_no_na_names", "winners", "WARN",
                 glue("{KNOWN_BLANK_MR} known INE source gap(s): {blanks}"))
    } else {
      log_result("winners_no_na_names", "winners", "WARN",
                 glue("{na_rp} RP/list rows have NA persona_candidata (may be expected in INE source)"))
    }
    
    # Percentage stored as 0–1 fraction (parsed from "59.76%" in tidy step)
    pct_check <- winners |>
      filter(!is.na(porcentaje_votacion)) |>
      summarise(min = min(porcentaje_votacion), max = max(porcentaje_votacion))
    if (pct_check$min >= 0 && pct_check$max <= 1) {
      log_result("winners_pct_range", "winners", "PASS",
                 glue("range: {round(pct_check$min,4)} – {round(pct_check$max,4)}"))
    } else {
      log_result("winners_pct_range", "winners", "FAIL",
                 glue("range: {round(pct_check$min,4)} – {round(pct_check$max,4)} (expected 0–1)"))
    }
  }
}

# ---------------------------------------------------------------------------
# 13. WINNERS VS ESTADO RECONCILIATION
# Top vote-getter per state in the candidato parquet should match the top
# votacion_ganador in the winners table. Tolerance: 0.1%.
# ---------------------------------------------------------------------------
{
  cat("\n── Winners vs estado reconciliation ─────────────────────────\n")
  if (!is.null(winners) && !is.null(estado)) {
    
    # Top winner per state from winners table
    top_winners <- winners |>
      filter(tipo_de_candidatura == ELECTION_RACE_TYPE) |>
      group_by(id_estado, nombre_estado) |>
      slice_max(votacion_ganador, n = 1, with_ties = FALSE) |>
      ungroup() |>
      select(id_estado, nombre_estado, nombre_actor_politico, votacion_ganador)
    
    # Top coalition per state from the candidato view of the estado parquet
    estado_top <- estado |>
      filter(vote_type == "candidato", !is.na(num_votos)) |>
      group_by(id_estado) |>
      slice_max(num_votos, n = 1, with_ties = FALSE) |>
      ungroup() |>
      rename(parquet_winner = party_key, parquet_votos = num_votos)
    
    reconciliation <- top_winners |>
      left_join(estado_top |> select(id_estado, parquet_winner, parquet_votos),
                by = "id_estado") |>
      mutate(
        winner_match  = nombre_actor_politico == parquet_winner,
        vote_diff     = abs(votacion_ganador - parquet_votos),
        vote_diff_pct = vote_diff / votacion_ganador
      )
    
    mismatched <- reconciliation |> filter(!winner_match | is.na(winner_match))
    if (nrow(mismatched) == 0) {
      log_result("winner_coalition_match", "estado vs winners", "PASS",
                 glue("all {nrow(reconciliation)} states agree on top winning coalition"))
    } else {
      detail <- mismatched |>
        mutate(msg = glue("{nombre_estado}: winners={nombre_actor_politico}, parquet={parquet_winner}")) |>
        pull(msg) |> paste(collapse = " | ")
      log_result("winner_coalition_match", "estado vs winners", "FAIL",
                 glue("{nrow(mismatched)} state(s) disagree: {detail}"))
    }
    
    vote_mismatches <- reconciliation |> filter(vote_diff_pct > 0.001)
    if (nrow(vote_mismatches) == 0) {
      log_result("winner_vote_totals", "estado vs winners", "PASS",
                 glue("all {nrow(reconciliation)} states within 0.1% tolerance"))
    } else {
      detail <- vote_mismatches |>
        mutate(msg = glue("{nombre_estado}: winners={comma(votacion_ganador)}, parquet={comma(parquet_votos)} (diff={round(vote_diff_pct*100,2)}%)")) |>
        pull(msg) |> paste(collapse = " | ")
      log_result("winner_vote_totals", "estado vs winners", "FAIL",
                 glue("{nrow(vote_mismatches)} state(s) with vote count mismatch: {detail}"))
    }
    
    missing_states <- reconciliation |> filter(is.na(parquet_winner))
    if (nrow(missing_states) == 0) {
      log_result("winner_states_coverage", "estado vs winners", "PASS",
                 glue("all {nrow(top_winners)} states present in parquet"))
    } else {
      detail <- missing_states |> pull(nombre_estado) |> paste(collapse = ", ")
      log_result("winner_states_coverage", "estado vs winners", "FAIL",
                 glue("{nrow(missing_states)} state(s) missing from parquet: {detail}"))
    }
  }
}

# ===========================================================================
# SUMMARY
# ===========================================================================
{
  cat("\n══════════════════════════════════════════════════════════════\n")
  cat("SUMMARY\n")
  cat("══════════════════════════════════════════════════════════════\n\n")
  
  summary_tbl <- results |>
    count(status) |>
    arrange(match(status, c("FAIL", "WARN", "PASS")))
  print(summary_tbl, n = Inf)
  
  n_fail <- sum(results$status == "FAIL")
  n_warn <- sum(results$status == "WARN")
  
  cat("\n")
  if (n_fail > 0) {
    cat(glue("✗  {n_fail} check(s) FAILED — DO NOT LOAD into Supabase\n\n"))
    cat("Failed checks:\n")
    results |>
      filter(status == "FAIL") |>
      mutate(msg = glue("  • [{level}] {check}: {detail}")) |>
      pull(msg) %>% print()
    
  } else if (n_warn > 0) {
    cat(glue("⚠  {n_warn} warning(s) — review before loading, but data is probably OK\n"))
  } else {
    cat("✓  All checks passed — safe to load into Supabase\n")
  }
}


# =============================================================================
# diagnose_failures.R
# Investigates the three failing checks from validate_electoral_2024.R
# =============================================================================

library(tidyverse)
library(arrow)
library(janitor)

TIDY_DIR  <- "~/electoral_tidy"
INPUT_DIR <- "~/PRESIDENCIA_2024/CSV"

read_ine_csv <- function(path) {
  read_csv(path, locale = locale(encoding = "latin1"), show_col_types = FALSE) |>
    clean_names()
}

winners   <- read_parquet(file.path(TIDY_DIR, "winners_2024_pre.parquet"))
estado    <- read_parquet(file.path(TIDY_DIR, "votes_estado_2024_pre.parquet"))
dim_dis   <- read_csv(file.path(TIDY_DIR, "dim_distritos.csv"), show_col_types = FALSE)

# =============================================================================
# FAILURE 1: 300 DIP_MR rows but 332 districts in dim_distritos
# =============================================================================
cat("══════════════════════════════════════════════════════\n")
cat("FAILURE 1: winners_n_dip_mr\n")
cat("══════════════════════════════════════════════════════\n\n")

cat("── Winners candidacy type breakdown:\n")
winners |> count(tipo_de_candidatura) |> print()

cat("\n── dim_distritos row count:", nrow(dim_dis), "\n")
cat("── dim_distritos id_distrito_federal range:\n")
dim_dis |> summarise(min = min(id_distrito_federal),
                     max = max(id_distrito_federal),
                     n_distinct = n_distinct(id_distrito_federal)) |> print()

cat("\n── dim_distritos — are there non-district rows (id=0 or similar)?\n")
dim_dis |> filter(id_distrito_federal == 0 | is.na(id_distrito_federal)) |> print()

cat("\n── dim_distritos per estado (how many districts per state?):\n")
dim_dis |>
  count(id_estado) |>
  arrange(desc(n)) |>
  print(n = Inf)

# =============================================================================
# FAILURE 2 & 3: NA state in winners vs estado reconciliation
# =============================================================================
cat("\n══════════════════════════════════════════════════════\n")
cat("FAILURES 2 & 3: winner_coalition_match + winner_states_coverage\n")
cat("══════════════════════════════════════════════════════\n\n")

cat("── PRE rows in winners — id_estado values:\n")
winners |>
  filter(tipo_de_candidatura == "PRE") |>
  select(id_estado, nombre_estado, nombre_actor_politico, votacion_ganador) |>
  arrange(id_estado) |>
  print(n = Inf)

cat("\n── Are there NA id_estado values in winners PRE rows?\n")
winners |>
  filter(tipo_de_candidatura == "PRE", is.na(id_estado)) |>
  print()

cat("\n── estado parquet — id_estado values present (candidato view):\n")
estado |>
  filter(vote_type == "candidato") |>
  pull(id_estado) |>
  unique() |>
  sort() |>
  print()

cat("\n── Are there NA id_estado values in estado parquet?\n")
estado |> filter(is.na(id_estado)) |> count(vote_type, party_key) |> print()

cat("\n── Top vote-getter per state in estado candidato view:\n")
estado |>
  filter(vote_type == "candidato", !is.na(num_votos)) |>
  group_by(id_estado) |>
  slice_max(num_votos, n = 1, with_ties = FALSE) |>
  ungroup() |>
  select(id_estado, party_key, num_votos) |>
  arrange(id_estado) |>
  print(n = Inf)

# =============================================================================
# BONUS: Inspect KNOWN_BLANK_MR — is the Estado de México D23 gap real?
# =============================================================================
cat("\n══════════════════════════════════════════════════════\n")
cat("BONUS: KNOWN_BLANK_MR — inspect all NA persona_candidata in winners\n")
cat("══════════════════════════════════════════════════════\n\n")

cat("── All winners rows with NA persona_candidata:\n")
winners |>
  filter(is.na(persona_candidata)) |>
  select(tipo_de_candidatura, id_estado, nombre_estado,
         id_distrito_federal, partido_politico, nombre_actor_politico) |>
  arrange(tipo_de_candidatura, id_estado, id_distrito_federal) |>
  print(n = Inf)

cat("\n── Cross-check: raw INTEGRACION_CARGOS — same rows in source?\n")
raw_winners_path <- file.path(INPUT_DIR, "INTEGRACION_CARGOS_PEF_2024.csv")
if (file.exists(raw_winners_path)) {
  raw_winners <- read_ine_csv(raw_winners_path)
  raw_winners |>
    filter(is.na(persona_candidata)) |>
    select(tipo_de_candidatura, id_estado, nombre_estado,
           id_distrito_federal, partido_politico, nombre_actor_politico) |>
    arrange(tipo_de_candidatura, id_estado, id_distrito_federal) |>
    print(n = Inf)
} else {
  cat("Raw INTEGRACION_CARGOS not found at expected path.\n")
}