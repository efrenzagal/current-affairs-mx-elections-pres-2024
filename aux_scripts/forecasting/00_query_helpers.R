# ─────────────────────────────────────────────────────────────────────────────
# Fundamentals models — shared query helpers
# PRE, SEN, and DIP all share the same fact/dim schema; only election_type
# and which geography levels make sense differ per chamber:
#   PRE - one national race. National / state / district all meaningful.
#   SEN - elected by state (2 MR seats + 1a minoria per state). District
#         doesn't apply even though id_distrito_federal is present in the
#         geography table (inherited from casilla location, not the race).
#   DIP - elected by district (300 MR seats). District is the natural unit;
#         state and national are roll-ups. Includes midterm-only years
#         (2015, 2021) with no PRE/SEN race.
# ─────────────────────────────────────────────────────────────────────────────

{ # Setup ----------------------------------------------------------------

  library(DBI)
  library(RSQLite)
  library(dplyr)

  DB_PATH <- "election_data.db"
  con <- dbConnect(RSQLite::SQLite(), DB_PATH)

}

{ # Query builders ----------------------------------------------------------

  get_national_votes <- function(election_type) {
    dbGetQuery(con, "
      SELECT f.election_id, e.year, f.party_key, SUM(f.votes) AS votes
      FROM fact_casilla_vote f
      JOIN dim_election e ON e.election_id = f.election_id
      WHERE e.election_type = ?
      GROUP BY f.election_id, e.year, f.party_key
    ", params = list(election_type))
  }

  get_state_votes <- function(election_type) {
    dbGetQuery(con, "
      SELECT
        f.election_id, e.year, g.id_estado, g.nombre_estado,
        f.party_key, SUM(f.votes) AS votes
      FROM fact_casilla_vote f
      JOIN dim_election  e ON e.election_id = f.election_id
      JOIN dim_casilla   c ON c.casilla_id  = f.casilla_id AND c.election_id = f.election_id
      JOIN dim_geography g ON g.geo_id      = c.geo_id     AND g.election_id = c.election_id
      WHERE e.election_type = ?
      GROUP BY f.election_id, e.year, g.id_estado, g.nombre_estado, f.party_key
    ", params = list(election_type))
  }

  get_district_votes <- function(election_type) {
    dbGetQuery(con, "
      SELECT
        f.election_id, e.year, g.id_estado, g.nombre_estado,
        g.id_distrito_federal, g.cabecera_distrital_federal,
        f.party_key, SUM(f.votes) AS votes
      FROM fact_casilla_vote f
      JOIN dim_election  e ON e.election_id = f.election_id
      JOIN dim_casilla   c ON c.casilla_id  = f.casilla_id AND c.election_id = f.election_id
      JOIN dim_geography g ON g.geo_id      = c.geo_id     AND g.election_id = c.election_id
      WHERE e.election_type = ?
        AND g.id_distrito_federal IS NOT NULL
      GROUP BY f.election_id, e.year, g.id_estado, g.nombre_estado,
               g.id_distrito_federal, g.cabecera_distrital_federal, f.party_key
    ", params = list(election_type))
  }

  add_share <- function(votes_df, ...) {
    votes_df %>%
      group_by(...) %>%
      mutate(total_votes = sum(votes), share = votes / total_votes) %>%
      ungroup()
  }

}
