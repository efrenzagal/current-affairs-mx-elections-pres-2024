{
  library(DBI)
  library(RSQLite)
  library(dplyr)
  library(tibble)
  
  con <- dbConnect(SQLite(), "election_data.db")
  
  # ── Expected values from file headers (skip=2, n_max=1) ─────────────────────
  # ACTAS_CONTABILIZADAS / ACTAS_NO_CONTABILIZADAS / ACTAS_CAPTURADAS
  
  header_stats <- tribble(
    ~election_id,   ~actas_contabilizadas, ~actas_no_contabilizadas, ~actas_capturadas,
    "PRE_2012",     139962L,               1973L,                    141935L,
    "DIP_MR_2012",  140184L,               2089L,                    142273L,
    "SEN_MR_2012",  140359L,               1943L,                    142302L,
  )
  
  # ── 1. dim_election — all 3 elections present ────────────────────────────────
  cat("\n── dim_election ─────────────────────────────────────────\n")
  
  dim_election <- tbl(con, "dim_election") |>
    filter(year == 2012) |>
    collect()
  
  print(dim_election)
  
  stopifnot("All 3 elections present" = nrow(dim_election) == 3)
  
  # ── 2. dim_casilla — row count vs ACTAS_CONTABILIZADAS ──────────────────────
  cat("\n── dim_casilla vs header ACTAS_CONTABILIZADAS ───────────\n")
  
  casilla_counts <- tbl(con, "dim_casilla") |>
    filter(election_id %in% c("PRE_2012", "DIP_MR_2012", "SEN_MR_2012")) |>
    count(election_id, name = "n_casillas") |>
    collect() |>
    left_join(header_stats, by = "election_id") |>
    mutate(
      diff  = n_casillas - actas_contabilizadas,
      check = if_else(diff == 0, "✓", "✗")
    ) |>
    select(election_id, n_casillas, actas_contabilizadas, diff, check)
  
  print(casilla_counts)
  
  # ── 3. fact_casilla_vote — unique casilla count vs ACTAS_CONTABILIZADAS ──────
  cat("\n── fact_casilla_vote unique casillas vs header ───────────\n")
  
  fact_casilla_counts <- tbl(con, "fact_casilla_vote") |>
    filter(election_id %in% c("PRE_2012", "DIP_MR_2012", "SEN_MR_2012")) |>
    group_by(election_id) |>
    summarise(
      n_casillas_fact = n_distinct(casilla_id),
      total_votes     = sum(votes, na.rm = TRUE),
      .groups = "drop"
    ) |>
    collect() |>
    left_join(header_stats, by = "election_id") |>
    mutate(
      diff  = n_casillas_fact - actas_contabilizadas,
      check = if_else(diff == 0, "✓", "✗")
    ) |>
    select(election_id, n_casillas_fact, actas_contabilizadas, diff, check, total_votes)
  
  print(fact_casilla_counts)
  
  # ── 4. Vote totals by party ───────────────────────────────────────────────────
  cat("\n── Vote totals by party (PRE_2012) ───────────────────────\n")
  
  pre_totals <- tbl(con, "fact_casilla_vote") |>
    filter(election_id == "PRE_2012") |>
    group_by(party_key) |>
    summarise(votes = sum(votes, na.rm = TRUE), .groups = "drop") |>
    collect() |>
    arrange(desc(votes))
  
  print(pre_totals)
  
  cat("\n── Vote totals by party (DIP_MR_2012) ───────────────────\n")
  
  dip_totals <- tbl(con, "fact_casilla_vote") |>
    filter(election_id == "DIP_MR_2012") |>
    group_by(party_key) |>
    summarise(votes = sum(votes, na.rm = TRUE), .groups = "drop") |>
    collect() |>
    arrange(desc(votes))
  
  print(dip_totals)
  
  cat("\n── Vote totals by party (SEN_MR_2012) ───────────────────\n")
  
  sen_totals <- tbl(con, "fact_casilla_vote") |>
    filter(election_id == "SEN_MR_2012") |>
    group_by(party_key) |>
    summarise(votes = sum(votes, na.rm = TRUE), .groups = "drop") |>
    collect() |>
    arrange(desc(votes))
  
  print(sen_totals)
  
  # ── 5. dim_party — 2012 parties present ──────────────────────────────────────
  cat("\n── dim_party (2012 entries) ──────────────────────────────\n")
  
  parties_2012 <- c("PAN", "PRI", "PRD", "PVEM", "PT", "MC", "PANAL",
                    "C_PRI_PVEM", "C_PRD_PT_MC", "C_PRD_PT", "C_PRD_MC", "C_PT_MC")
  
  dim_party <- tbl(con, "dim_party") |>
    filter(party_key %in% parties_2012) |>
    collect() |>
    arrange(party_key)
  
  print(dim_party)
  
  missing_parties <- setdiff(parties_2012, dim_party$party_key)
  if (length(missing_parties) > 0) {
    cat("✗ Missing parties:", paste(missing_parties, collapse = ", "), "\n")
  } else {
    cat("✓ All 12 expected 2012 party keys present\n")
  }
  
  # ── 6. Geography — section count sanity ──────────────────────────────────────
  cat("\n── dim_geography sections per election ──────────────────\n")
  
  geo_counts <- tbl(con, "dim_casilla") |>
    filter(election_id %in% c("PRE_2012", "DIP_MR_2012", "SEN_MR_2012")) |>
    left_join(tbl(con, "dim_geography"), by = "geo_id") |>
    group_by(election_id) |>
    summarise(
      n_estados  = n_distinct(id_estado),
      n_secciones = n_distinct(geo_id),
      .groups = "drop"
    ) |>
    collect()
  
  print(geo_counts)
  
  dbDisconnect(con)
  cat("\n✓ Validation complete\n")
}