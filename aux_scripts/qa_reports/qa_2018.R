library(DBI)
library(RSQLite)
library(dplyr)
library(readr)

# ── Read summary rows from raw CSVs ───────────────────────────────────────────
read_summary <- function(path, election_id) {
  read_delim(path, skip = 3, n_max = 1, delim = "|", show_col_types = FALSE) |>
    mutate(election_id = election_id) |>
    select(election_id,
           actas_raw       = ACTAS_COMPUTADAS,
           total_votos_raw = TOTAL_VOTOS)
}

raw_totals <- bind_rows(
  read_summary("data/20180708_2130_CW/20180708_2130_CW_presidencia/presidencia.csv",   "PRE_2018"),
  read_summary("data/20180708_2130_CW/20180708_2130_CW_senadurias/senadurias.csv",     "SEN_MR_2018"),
  read_summary("data/20180708_2130_CW/20180708_2130_CW_diputaciones/diputaciones.csv", "DIP_MR_2018"),
)

# ── Connect ───────────────────────────────────────────────────────────────────
conn <- dbConnect(SQLite(), "election_data.db")

# ── Aggregate from fact: deduplicate to casilla level before summing
#    nulos and cnreg since they are denormalized (repeated once per party row)
fact_totals <- dbGetQuery(conn, "
  SELECT
    election_id,
    SUM(votes)         AS sum_party_votes_db,
    SUM(casilla_nulos) AS sum_nulos_db,
    SUM(casilla_cnreg) AS sum_cnreg_db,
    COUNT(*)           AS actas_db
  FROM (
    SELECT
      election_id,
      casilla_id,
      SUM(votes)              AS votes,
      MAX(num_votos_nulos)    AS casilla_nulos,
      MAX(num_votos_can_nreg) AS casilla_cnreg
    FROM fact_casilla_vote
    WHERE election_id IN ('PRE_2018', 'SEN_MR_2018', 'DIP_MR_2018')
    GROUP BY election_id, casilla_id
  )
  GROUP BY election_id
")

dbDisconnect(conn)

# ── Join and compare ──────────────────────────────────────────────────────────
validation <- raw_totals |>
  left_join(fact_totals, by = "election_id") |>
  mutate(
    total_votos_db = sum_party_votes_db + sum_nulos_db + sum_cnreg_db,
    actas_match    = actas_raw       == actas_db,
    total_match    = total_votos_raw == total_votos_db,
    actas_diff     = actas_db        - actas_raw,
    total_diff     = total_votos_db  - total_votos_raw,
  ) |>
  select(election_id, actas_raw, actas_db, actas_match, actas_diff,
         total_votos_raw, total_votos_db, total_match, total_diff)

print(validation, width = Inf)