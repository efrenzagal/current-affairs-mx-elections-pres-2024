# ============================================================================
# qa_warehouse_vs_csv_2024.R
#
# Reconciles election_data.db (SQLite warehouse) against INE's own official
# aggregate CSVs, for all 5 2024 election_ids.
#
# Narrowed scope (per discussion): we only care about two things matching:
#   1. CASILLAS  — does the warehouse count the same number of physical
#                  casillas as INE does, at every geography level?
#   2. VOTES     — do summed votes per party match INE's official numbers?
#
# These are treated as SEPARATE checks because they fail for structurally
# different reasons:
#   - CASILLAS mismatches are an IDENTITY problem: one physical casilla can
#     file multiple actas (e.g. SMR + SRP pair = 1 physical casilla, not 2;
#     VA-prefixed actas are excluded from INE's casilla count entirely).
#     This was discovered empirically in SEN_RP_2024 and is not yet confirmed
#     to generalize to other election types — that's exactly what this run
#     will help confirm or contradict.
#   - VOTES mismatches are arithmetic: did we sum the right column to the
#     right party_key.
#
# The acta-identity rule is applied ONCE, upstream, as its own step — not
# buried inside the comparison function — so if it turns out votes are ALSO
# affected by the same duplicate-acta issue, that will show up directly in
# the votes report.
# ============================================================================

{
  library(DBI)
  library(RSQLite)
  library(readr)
  library(dplyr)
  library(tidyr)
  library(stringr)
  library(purrr)
  library(glue)
}

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

{
  DB_PATH <- "election_data.db"
  
  FOLDERS <- tibble::tribble(
    ~folder,                          ~election_id,
    "DIPUTACIONES_FED_MR_2024",       "DIP_MR_2024",
    "DIPUTACIONES_FED_RP_2024",       "DIP_RP_2024",
    "PRESIDENCIA_2024",               "PRE_2024",
    "SENADURIAS_MR_2024",             "SEN_MR_2024",
    "SENADURIAS_RP_2024",             "SEN_RP_2024"
  )
  
  OUT_DIR <- "aux_scripts/qa_reports"
  dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)
}

# ----------------------------------------------------------------------------
# Acta-identity rules (the SMR/SRP/VA discovery, generalized to a lookup
# table instead of hardcoded case_when logic, so a new prefix you haven't
# seen yet falls into "unknown" instead of silently miscounting)
# ----------------------------------------------------------------------------

{
  # Each row: a regex matched against ACTA_CASILLA_MEC prefix, and what it
  # means for casilla identity vs. vote counting. These are SEPARATE concerns:
  # an acta type can be excluded from the physical CASILLAS count while its
  # votes still count toward official totals (e.g. VMRE/VeMRE — voto desde el
  # extranjero — are not a physical polling-station casilla, but the votes
  # cast there are fully counted by INE).
  #   casilla_rule:
  #     "pair_collapse" — this acta type pairs with another to form ONE
  #                        physical casilla (SMR + SRP). Handled specially in
  #                        collapse_to_physical_casilla().
  #     "exclude"        — this acta type is excluded from the CASILLAS count
  #                         entirely.
  #     "normal"         — counts as one casilla, no special handling.
  #   votes_rule:
  #     "include"         — votes count toward official totals (default).
  #     "exclude"         — votes are NOT counted by INE (VA = voto
  #                          anticipado only, confirmed).
  #
  # BUGFIX (found via worst-offender drill-down on ID_ESTADO=9, SECCION=0,
  # PARTY_KEY=PAN): VMRE/VeMRE were previously sharing the same single "rule"
  # column as VA, which zeroed their votes out of every comparison even
  # though warehouse VMRE/VeMRE sums matched INE's official CSV exactly
  # (22570 = 22570). VMRE/VeMRE should be excluded from CASILLAS but kept in
  # VOTES — now expressed as two independent columns instead of one.
  ACTA_RULES <- tibble::tribble(
    ~prefix_regex,        ~casilla_rule,      ~votes_rule,
    "^SMR",                "pair_collapse",    "include",
    "^SRP",                "pair_collapse",    "include",
    "^VA",                 "exclude",          "exclude",  # voto anticipado —
    # genuinely excluded from official vote totals, per original discovery.
    "^VMRE|^VeMRE",        "exclude",          "include",  # voto desde el
    # extranjero — NOT a physical casilla (excluded from CASILLAS), but votes
    # DO count toward official totals. Confirmed: warehouse VMRE/VeMRE sum
    # for ID_ESTADO=9, SECCION=0, PAN = 22570 = official CSV value exactly.
    "^S(?!MR|RP)",         "pair_collapse",    "include",  # PRE_2024-only
    # special casilla prefix, distinct from SMR/SRP
    # (negative lookahead excludes
    # those). UNCONFIRMED: assumed to
    # behave like SMR/SRP pairing
    # (pairs with itself / collapses
    # to 1 per section) since PRE_2024
    # has no SMR/SRP of its own and
    # this is the prefix that showed
    # up instead — verify against the
    # NAL-level off-by-one once rerun.
    "^VPPP",               "exclude",          "exclude"   # PRE_2024-only —
    # UNCONFIRMED guess (procedure unknown, assumed similar
    # to VA as a non-physical-casilla, non-counted
    # special acta type). Watch the rerun's
    # PRE_2024 NAL-level diff to confirm or
    # reject this.
  )
  
  classify_acta <- function(acta_casilla_mec) {
    casilla_out <- rep("normal", length(acta_casilla_mec))
    votes_out   <- rep("include", length(acta_casilla_mec))
    for (i in seq_len(nrow(ACTA_RULES))) {
      hit <- str_detect(acta_casilla_mec, ACTA_RULES$prefix_regex[i])
      casilla_out[hit] <- ACTA_RULES$casilla_rule[i]
      votes_out[hit]   <- ACTA_RULES$votes_rule[i]
    }
    list(casilla_class = casilla_out, votes_class = votes_out)
  }
  
  # Collapses acta-level rows to physical-casilla-level rows, applying the
  # rules above. Input: one row per (casilla_id, acta_casilla_mec) at minimum.
  # Output: same rows, but with a `casilla_count_weight` column — 1 for a
  # normal casilla, 1 for the FIRST acta in a genuine SMR/SRP pair (0 for its
  # partner), 0 for excluded types (VA/VMRE). Sum this weight instead of
  # n_distinct(casilla_id) to get INE's notion of "how many casillas."
  #
  # IMPORTANT: pairing is keyed on (ID_ESTADO, SECCION, ID_CASILLA), matching
  # an SMR row to an SRP row that share the same ID_CASILLA — this is the
  # correct unit of "same physical casilla filed two acta types." A section
  # with two SMRs and zero SRPs (confirmed real case: ID_ESTADO=1, SECCION=218,
  # DIP_MR_2024 — SMR01 + SMR02, no SRP) is NOT a pair; both must count as 1.
  # The earlier version of this function paired by (ID_ESTADO, SECCION) alone,
  # which wrongly zeroed out the second SMR in exactly that scenario — found
  # via direct inspection against dim_casilla, which has all 5 rows correctly.
  collapse_to_physical_casilla <- function(df) {
    acta_classes <- classify_acta(df$ACTA_CASILLA_MEC)
    df <- df |>
      mutate(
        acta_class  = acta_classes$casilla_class,  # drives CASILLAS weighting (unchanged)
        votes_class = acta_classes$votes_class       # drives votes inclusion (bugfix)
      )
    
    pairable <- df |> filter(acta_class == "pair_collapse")
    other    <- df |> filter(acta_class != "pair_collapse")
    
    # Tag each pairable row with whether its ID_CASILLA has BOTH an SMR and an
    # SRP present in the same section — only those are genuine pairs.
    pair_membership <- pairable |>
      mutate(prefix_tag = if_else(str_detect(ACTA_CASILLA_MEC, "^SMR"), "SMR",
                                  if_else(str_detect(ACTA_CASILLA_MEC, "^SRP"), "SRP", "OTHER"))) |>
      group_by(ID_ESTADO, SECCION, ID_CASILLA) |>
      mutate(
        has_smr = any(prefix_tag == "SMR"),
        has_srp = any(prefix_tag == "SRP"),
        is_true_pair = has_smr & has_srp
      ) |>
      ungroup()
    
    true_pairs <- pair_membership |>
      filter(is_true_pair) |>
      group_by(ID_ESTADO, SECCION, ID_CASILLA) |>
      mutate(
        pair_rank = row_number(),
        casilla_count_weight = if_else(pair_rank == 1, 1, 0)
      ) |>
      ungroup() |>
      select(-prefix_tag, -has_smr, -has_srp, -is_true_pair, -pair_rank)
    
    # Pairable-classified rows that did NOT find their SMR/SRP counterpart
    # (e.g. an SMR with no matching SRP for that ID_CASILLA) — count normally,
    # weight 1, same as any other distinct casilla.
    unpaired <- pair_membership |>
      filter(!is_true_pair) |>
      mutate(casilla_count_weight = 1) |>
      select(-prefix_tag, -has_smr, -has_srp, -is_true_pair)
    
    excluded <- other |>
      filter(acta_class == "exclude") |>
      mutate(casilla_count_weight = 0)
    
    normal <- other |>
      filter(acta_class == "normal") |>
      mutate(casilla_count_weight = 1)
    
    bind_rows(normal, true_pairs, unpaired, excluded)
  }
}

# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------

{
  clean_names <- function(x) {
    x |>
      str_trim() |>
      str_to_upper() |>
      str_replace_all("-", "_") |>
      str_replace_all("\\s+", "_")
  }
  
  id_cols <- c(
    "CIRCUNSCRIPCION", "NUMERO_CIRCUNSCRIPCION",
    "ID_ESTADO", "NOMBRE_ESTADO",
    "ID_DISTRITO_FEDERAL", "CABECERA_DISTRITAL_FEDERAL",
    "ID_MUNICIPIO", "MUNICIPIO",
    "SECCION", "TIPO_CASILLA", "ID_CASILLA", "EXT_CONTIGUA",
    "ACTA_CASILLA_MEC", "URNA_ELECTRONICA",
    "ESTADOS", "DISTRITOS", "MUNICIPIOS", "SECCIONES",
    "CASILLAS", "ACTAS_CASILLA_MEC",
    "TRIBUNAL", "RUTA_ACTA", "OBSERVACIONES", "ESTATUS_ACTA"
  )
  
  read_ine_csv <- function(path) {
    df <- read_csv(
      path,
      col_types = cols(.default = col_character()),
      locale = locale(encoding = "Latin1"),
      show_col_types = FALSE
    )
    
    names(df) <- clean_names(names(df))
    df <- df |> select(!matches("^\\.\\.\\.[0-9]+$"))
    
    numeric_candidates <- setdiff(names(df), id_cols)
    
    df |>
      mutate(across(
        all_of(numeric_candidates),
        ~ suppressWarnings(parse_number(as.character(.x), locale = locale(grouping_mark = ",")))
      ))
  }
  
  detect_level <- function(filename) {
    filename <- str_to_upper(filename)
    case_when(
      str_detect(filename, "_NAL_CAS") ~ "CAS",
      str_detect(filename, "_NAL_SEC") ~ "SEC",
      str_detect(filename, "_NAL_MUN") ~ "MUN",
      str_detect(filename, "_NAL_DIS") ~ "DIS",
      str_detect(filename, "_NAL_ENT") ~ "ENT",
      str_detect(filename, "_NAL_CIR") ~ "CIR",
      str_detect(filename, "_NAL(PP|CAND)?\\.CSV$") ~ "NAL",
      TRUE ~ NA_character_
    )
  }
  
  level_keys <- function(level) {
    switch(
      level,
      "CAS" = c("ID_ESTADO", "SECCION", "ACTA_CASILLA_MEC"),
      "SEC" = c("ID_ESTADO", "SECCION"),
      "MUN" = c("ID_ESTADO", "ID_MUNICIPIO"),
      "DIS" = c("ID_ESTADO", "ID_DISTRITO_FEDERAL"),
      "ENT" = c("ID_ESTADO"),
      "CIR" = c("CIRCUNSCRIPCION"),
      "NAL" = character(0),
      stop("Unknown level: ", level)
    )
  }
  
  group_by_level <- function(df, keys) {
    if (length(keys) == 0) {
      df |> mutate(QA_KEY = 1) |> group_by(QA_KEY)
    } else {
      df |> group_by(across(all_of(keys)))
    }
  }
}

# ----------------------------------------------------------------------------
# Pull raw acta-level warehouse rows for one election (votes + casilla meta,
# unaggregated) — both downstream checks build off this same base so any
# acta-identity correction applies consistently to both.
# ----------------------------------------------------------------------------

{
  warehouse_base <- function(conn, election_id) {
    sql <- glue("
    SELECT
      f.election_id,
      c.casilla_id,
      c.acta_casilla_mec AS ACTA_CASILLA_MEC,
      c.id_casilla AS ID_CASILLA,
      g.id_estado AS ID_ESTADO,
      g.nombre_estado AS NOMBRE_ESTADO,
      g.id_distrito_federal AS ID_DISTRITO_FEDERAL,
      g.cabecera_distrital_federal AS CABECERA_DISTRITAL_FEDERAL,
      g.id_municipio AS ID_MUNICIPIO,
      g.municipio AS MUNICIPIO,
      g.seccion AS SECCION,
      g.circunscripcion AS CIRCUNSCRIPCION,
      f.party_key AS PARTY_KEY,
      f.votes AS VOTES
    FROM fact_casilla_vote f
    JOIN dim_casilla c
      ON f.election_id = c.election_id
     AND f.casilla_id = c.casilla_id
    JOIN dim_geography g
      ON c.geo_id = g.geo_id
    WHERE f.election_id = '{election_id}'
  ")
    
    base <- dbGetQuery(conn, sql)
    names(base) <- clean_names(names(base))
    base
  }
}

# ----------------------------------------------------------------------------
# CHECK 1: Casillas — does warehouse casilla count match INE's, at each level
# ----------------------------------------------------------------------------

{
  warehouse_casillas_by_level <- function(base_weighted, level) {
    keys <- level_keys(level)
    
    casilla_meta <- base_weighted |>
      distinct(
        CASILLA_ID, ACTA_CASILLA_MEC, casilla_count_weight,
        across(any_of(keys))
      )
    
    casilla_meta |>
      group_by_level(keys) |>
      summarise(
        CASILLAS = sum(casilla_count_weight),
        .groups = "drop"
      ) |>
      { \(x) if ("QA_KEY" %in% names(x)) select(x, -QA_KEY) else x }()
  }
  
  compare_casillas <- function(warehouse_cas, official, keys, file_label) {
    if (length(keys) == 0) {
      warehouse_cas <- warehouse_cas |> mutate(QA_KEY = 1)
      official      <- official      |> mutate(QA_KEY = 1)
      keys <- "QA_KEY"
    }
    
    if (!"CASILLAS" %in% names(official)) {
      return(tibble(file = file_label, issue = "CASILLAS_column_missing_in_csv"))
    }
    
    warehouse_cas <- warehouse_cas |> mutate(across(all_of(keys), as.character))
    official_cas  <- official |>
      select(all_of(keys), CASILLAS) |>
      mutate(across(all_of(keys), as.character)) |>
      mutate(CASILLAS = replace_na(suppressWarnings(as.numeric(CASILLAS)), 0)) |>
      rename(official = CASILLAS)
    
    warehouse_cas |>
      rename(warehouse = CASILLAS) |>
      full_join(official_cas, by = keys) |>
      mutate(
        warehouse = replace_na(warehouse, 0),
        official  = replace_na(official, 0),
        diff      = warehouse - official,
        file      = file_label,
        issue     = case_when(
          diff != 0 ~ "casillas_mismatch",
          TRUE      ~ "ok"
        )
      ) |>
      filter(issue != "ok") |>
      select(file, issue, all_of(keys), warehouse, official, diff)
  }
}

# ----------------------------------------------------------------------------
# CHECK 2: Votes by party — pure arithmetic, on top of the same weighted base
# (excluded actas contribute 0 votes; paired SMR/SRP actas both still
# contribute their own votes — exclusion/pairing is a CASILLA-COUNT concept,
# NOT a vote-zeroing concept, except where rule == "exclude")
# ----------------------------------------------------------------------------

{
  warehouse_votes_by_level <- function(base_weighted, level) {
    keys <- level_keys(level)
    
    votes_base <- base_weighted |>
      filter(votes_class != "exclude")   # only VA/VPPP excluded from vote
    # totals; VMRE/VeMRE (votes from abroad) now correctly included — see
    # ACTA_RULES bugfix above
    
    votes_base |>
      group_by_level(keys) |>
      group_by(PARTY_KEY, .add = TRUE) |>
      summarise(VOTES = sum(VOTES, na.rm = TRUE), .groups = "drop") |>
      { \(x) if ("QA_KEY" %in% names(x)) select(x, -QA_KEY) else x }()
  }
  
  # Columns that show up in INE's official CSVs alongside party vote columns,
  # but are NOT parties — they're the same totals/metadata that appear in the
  # warehouse's own fact table or casilla dim. Previously these were leaking
  # into party_cols (via the old "anything numeric not in id_cols" logic),
  # which inflated the votes mismatch count enormously by comparing e.g.
  # LISTA_NOMINAL against itself as if it were a party.
  NON_PARTY_METRIC_COLS <- c(
    "NUM_VOTOS_VALIDOS", "NUM_VOTOS_NULOS", "NUM_VOTOS_CAN_NREG",
    "TOTAL_VOTOS", "LISTA_NOMINAL", "TOTAL_VOTOS_CALCULADOS",
    "CASILLAS", "ACTAS_CASILLA_MEC", "SECCIONES", "MUNICIPIOS",
    "DISTRITOS", "ESTADOS", "PARTICIPACION", "PORCENTAJE_PARTICIPACION"
  )
  
  compare_votes <- function(warehouse_v, official, keys, file_label) {
    party_cols <- setdiff(names(official), c(id_cols, keys, NON_PARTY_METRIC_COLS))
    party_cols <- party_cols[
      sapply(official[party_cols], function(col) is.numeric(col) || all(!is.na(suppressWarnings(as.numeric(col)))))
    ]
    
    if (length(party_cols) == 0) {
      return(tibble(file = file_label, issue = "no_party_columns_found_in_csv"))
    }
    
    if (length(keys) == 0) {
      warehouse_v <- warehouse_v |> mutate(QA_KEY = 1)
      official    <- official    |> mutate(QA_KEY = 1)
      keys <- "QA_KEY"
    }
    
    official_long <- official |>
      select(all_of(keys), all_of(party_cols)) |>
      mutate(across(all_of(keys), as.character)) |>
      mutate(across(all_of(party_cols), ~ replace_na(suppressWarnings(as.numeric(.x)), 0))) |>
      pivot_longer(all_of(party_cols), names_to = "PARTY_KEY", values_to = "official")
    
    warehouse_long <- warehouse_v |>
      mutate(across(all_of(keys), as.character)) |>
      rename(warehouse = VOTES)
    
    warehouse_long |>
      full_join(official_long, by = c(keys, "PARTY_KEY")) |>
      mutate(
        warehouse = replace_na(warehouse, 0),
        official  = replace_na(official, 0),
        diff      = warehouse - official,
        file      = file_label,
        issue     = case_when(
          diff != 0 ~ "votes_mismatch",
          TRUE      ~ "ok"
        )
      ) |>
      filter(issue != "ok") |>
      select(file, issue, all_of(keys), PARTY_KEY, warehouse, official, diff)
  }
}

# ----------------------------------------------------------------------------
# Main QA loop
# ----------------------------------------------------------------------------

{
  run_qa <- function() {
    conn <- dbConnect(SQLite(), DB_PATH)
    on.exit(dbDisconnect(conn), add = TRUE)
    
    casillas_results <- list()
    votes_results    <- list()
    acta_class_log   <- list()   # tracks which acta prefixes showed up, so an
    # unrecognized prefix is visible, not silent
    
    for (i in seq_len(nrow(FOLDERS))) {
      folder      <- FOLDERS$folder[i]
      election_id <- FOLDERS$election_id[i]
      
      message("\n=== ", folder, " / ", election_id, " ===")
      
      base <- warehouse_base(conn, election_id)
      if (nrow(base) == 0) {
        message("  warehouse empty, skipping")
        next
      }
      
      base_weighted <- collapse_to_physical_casilla(base)
      
      acta_class_log[[length(acta_class_log) + 1]] <- base_weighted |>
        distinct(ACTA_CASILLA_MEC, acta_class) |>
        mutate(election_id = election_id)
      
      csv_dir   <- file.path("data", folder, "CSV")
      csv_paths <- list.files(csv_dir, pattern = "\\.csv$", full.names = TRUE)
      csv_paths <- csv_paths[
        !str_detect(basename(csv_paths), "_NAL_CAS") &
          !str_detect(basename(csv_paths), "INTEGRACION_CARGOS") &
          !str_detect(basename(csv_paths), "CAND") &
          !str_detect(basename(csv_paths), regex("PP\\.csv$", ignore_case = TRUE)) &
          !str_detect(basename(csv_paths), regex("_NAL_CIR", ignore_case = TRUE))
      ]
      # *CAND files excluded at every level (NALCAND, DISCAND, ENTCAND, ...):
      # confirmed via diagnostic read that NALCAND is a single-row,
      # coalition-only file (only MC/PAN_PRI_PRD/PVEM_PT_MORENA columns — the
      # registered candidacy tickets, not all party_keys) — CAND = candidatura,
      # PP = partido politico (confirmed). DISCAND/ENTCAND are the same file
      # shape at district/state granularity; detect_level()'s loose
      # "_NAL_DIS"/"_NAL_ENT" matching was classifying them as ordinary
      # DIS/ENT files and comparing them against the warehouse's full
      # per-party sums, producing huge spurious mismatches (e.g. MORENA
      # warehouse=3,940,566 vs official=0 at ENT level for ID_ESTADO=15).
      # A single "CAND" substring match catches all granularities at once,
      # since CAND never appears in any genuinely comparable filename.
      #
      # *PP files excluded at every level (NALPP, DISPP, ENTPP, ...):
      # confirmed PP files do NOT report coalition columns at all — they
      # reallocate coalition votes to their constituent individual parties
      # (the apportionment-math view INE uses for seat allocation), not the
      # as-cast-ballot view the warehouse stores under coalition party_keys
      # like PVEM_PT_MORENA / PAN_PRI_PRD. This is a legitimate alternate
      # aggregation, not a data error — but it's structurally incomparable
      # to fact_casilla_vote's party_key scheme, so it's excluded from this
      # QA check the same way CAND is. Pattern anchored on "PP\\.CSV$" (not
      # a bare "PP" substring) since "PP" could otherwise collide with other
      # filename tokens.
      #
      # _NAL_CIR excluded for the SAME underlying reason as PP, despite NOT
      # having a "PP" suffix in its filename: confirmed via diagnostic read
      # that 2024_SEE_DIP_FED_RP_NAL_CIR.csv reports only PAN/PRI/PRD/PVEM/
      # PT/MC/MORENA — no coalition columns. CIR (circunscripcion) is the
      # geography used for PR seat-apportionment math, the same purpose PP
      # files serve, so INE evidently only publishes it in individual-party
      # form. The underlying rule is content-based ("apportionment-purpose
      # files never carry coalition columns"), not filename-based — CAND/PP/
      # CIR are three different naming conventions converging on the same
      # shape mismatch. Re-check any NEW file type added to this pipeline
      # against this rule rather than assuming filename patterns generalize.
      
      for (path in csv_paths) {
        file  <- basename(path)
        level <- detect_level(file)
        
        if (is.na(level)) {
          message("  skipping unrecognized file: ", file)
          next
        }
        
        message("  ", file, " => ", level)
        
        official <- read_ine_csv(path)
        keys     <- level_keys(level)
        
        missing_keys <- setdiff(keys, names(official))
        if (length(missing_keys) > 0) {
          message("    missing key(s) in csv: ", paste(missing_keys, collapse = ", "))
          next
        }
        
        wh_cas <- warehouse_casillas_by_level(base_weighted, level)
        cas_result <- compare_casillas(wh_cas, official, keys, file)
        if (nrow(cas_result) > 0) {
          casillas_results[[length(casillas_results) + 1]] <- cas_result |> mutate(level = level, .before = 1)
        }
        
        wh_votes <- warehouse_votes_by_level(base_weighted, level)
        vote_result <- compare_votes(wh_votes, official, keys, file)
        if (nrow(vote_result) > 0) {
          votes_results[[length(votes_results) + 1]] <- vote_result |> mutate(level = level, .before = 1)
        }
      }
    }
    
    casillas_final <- bind_rows(casillas_results)
    votes_final    <- bind_rows(votes_results)
    acta_log_final <- bind_rows(acta_class_log) |> distinct()
    
    write_csv(casillas_final, file.path(OUT_DIR, "qa_casillas_mismatch.csv"))
    write_csv(votes_final,    file.path(OUT_DIR, "qa_votes_mismatch.csv"))
    write_csv(acta_log_final, file.path(OUT_DIR, "acta_class_log.csv"))
    
    message("\n\n========== SUMMARY ==========")
    
    message("\n-- Acta classification (review 'normal' entries for unrecognized special prefixes) --")
    acta_summary <- acta_log_final |>
      mutate(prefix = str_extract(ACTA_CASILLA_MEC, "^[A-Za-z]+")) |>
      count(election_id, acta_class, prefix, sort = TRUE)
    print(acta_summary)
    
    message("\n-- Casillas mismatches by level/file --")
    if (nrow(casillas_final) > 0) {
      print(casillas_final |> count(level, file, issue, sort = TRUE))
    } else {
      message("  none — casilla counts match at every level")
    }
    
    message("\n-- Votes mismatches by level/file --")
    if (nrow(votes_final) > 0) {
      print(votes_final |> count(level, file, issue, sort = TRUE))
      message("\n-- Votes mismatches by party (top offenders) --")
      print(votes_final |> count(PARTY_KEY, sort = TRUE))
    } else {
      message("  none — vote totals match at every level")
    }
    
    invisible(list(casillas = casillas_final, votes = votes_final, acta_log = acta_log_final))
  }
}

{
  qa_results <- run_qa()
}

# ----------------------------------------------------------------------------
# Pull the pieces out into standalone dataframes for manual inspection in
# your R session (RStudio's View(), head(), filter(), etc.) — same data as
# the CSVs written to disk, just kept in memory too.
# ----------------------------------------------------------------------------

{
  qa_casillas  <- qa_results$casillas
  qa_votes     <- qa_results$votes
  qa_acta_log  <- qa_results$acta_log
}

{
  # Quick look — uncomment whichever you want to inspect
  # View(qa_casillas)
  # View(qa_votes)
  # View(qa_acta_log)
  
  message("qa_casillas : ", nrow(qa_casillas), " rows")
  message("qa_votes    : ", nrow(qa_votes), " rows")
  message("qa_acta_log : ", nrow(qa_acta_log), " rows")
}

qa_votes %>% arrange(diff)

qa_votes %>% arrange(-diff)

qa_votes %>% mutate(rel_diff = diff/official) %>% View()

{
  qa_votes |>
    mutate(rel_diff = diff / official) |>
    summarise(
      n = n(),
      pct_warehouse_higher = mean(diff > 0) * 100,
      pct_warehouse_lower  = mean(diff < 0) * 100,
      median_abs_rel_diff  = median(abs(rel_diff), na.rm = TRUE),
      max_abs_rel_diff     = max(abs(rel_diff), na.rm = TRUE)
    ) |>
    print()
  
  { 
    library(ggplot2) 
    
    CHART_DIR <- file.path(OUT_DIR, "charts") 
    dir.create(CHART_DIR, showWarnings = FALSE, recursive = TRUE) 
    }

  { 
    # Relative-diff histogram, split by direction (warehouse > vs < official). 
    # Inf/NaN rows (official == 0) are excluded from this chart and reported 
    # separately, since they're a different failure mode — not a magnitude 
    # comparison. 
    rel_diff_data <- qa_votes |> 
      mutate(rel_diff = diff / official) |> 
      filter(is.finite(rel_diff)) |> 
      mutate( 
        direction = if_else(diff > 0, "warehouse > official", "warehouse < official"), 
        abs_rel_diff_bucket = case_when( 
          abs(rel_diff) < 0.001 ~ "<0.1%", 
          abs(rel_diff) < 0.005 ~ "0.1-0.5%", 
          abs(rel_diff) < 0.01  ~ "0.5-1%", 
          abs(rel_diff) < 0.02  ~ "1-2%", 
          abs(rel_diff) < 0.05  ~ "2-5%", 
          abs(rel_diff) < 0.10  ~ "5-10%", 
          TRUE                   ~ ">10%" 
        ), 
        abs_rel_diff_bucket = factor( 
          abs_rel_diff_bucket, 
          levels = c("<0.1%", "0.1-0.5%", "0.5-1%", "1-2%", "2-5%", "5-10%", ">10%") 
        ) 
      ) 
    
    n_inf_rows <- qa_votes |> mutate(rel_diff = diff / official) |> filter(!is.finite(rel_diff)) |> nrow() 
    message("Rows excluded from relative-diff chart (official == 0): ", n_inf_rows) 
    
    p_rel_diff_hist <- rel_diff_data |> 
      count(abs_rel_diff_bucket, direction) |> 
      ggplot(aes(x = abs_rel_diff_bucket, y = n, fill = direction)) + 
      geom_col(position = "stack") + 
      scale_fill_manual(values = c("warehouse > official" = "#378ADD", "warehouse < official" = "#D85A30")) + 
      labs( 
        title = "Votes mismatch: relative difference distribution", 
        subtitle = "Absolute |warehouse - official| / official, split by direction", 
        x = "Absolute relative difference", y = "Number of rows", fill = NULL 
      ) + 
      theme_minimal() + 
      theme(legend.position = "top") 
    
    ggsave(file.path(CHART_DIR, "votes_relative_diff_distribution.png"), p_rel_diff_hist, width = 8, height = 5, dpi = 150) 
  }
  
  { 
    # Direction balance by election — is the over/under split even everywhere, 
    # or does any one election cycle skew systematically in one direction 
    # (which would suggest a real bug rather than scattered recount drift)? 
    p_direction_by_election <- qa_votes |> 
      mutate( 
        election_tag = str_extract(file, "(?<=SEE_).*?(?=_NAL)"), 
        direction = if_else(diff > 0, "warehouse > official", "warehouse < official") 
      ) |> 
      count(election_tag, direction) |> 
      ggplot(aes(x = election_tag, y = n, fill = direction)) + 
      geom_col(position = "fill") + 
      scale_fill_manual(values = c("warehouse > official" = "#378ADD", "warehouse < official" = "#D85A30")) + 
      scale_y_continuous(labels = scales::percent) + 
      coord_flip() + 
      labs( 
        title = "Votes mismatch direction balance by election", 
        subtitle = "An even ~50/50 split suggests scattered drift, not a systematic bug", 
        x = NULL, y = "Share of mismatch rows", fill = NULL 
      ) + 
      theme_minimal() + 
      theme(legend.position = "top") 
    
    ggsave(file.path(CHART_DIR, "votes_direction_balance_by_election.png"), p_direction_by_election, width = 8, height = 5, dpi = 150) 
  }
  }


