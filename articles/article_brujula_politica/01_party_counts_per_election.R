# ─────────────────────────────────────────────────────────────────────────────
# How many parties have been in the presidential race per election cycle
# ─────────────────────────────────────────────────────────────────────────────
# Source: election_data.db (SQLite), tables fact_casilla_vote / dim_party
#
# A "party_key" in fact_casilla_vote is a vote option: it can be a single
# party, a coalition of several parties, an independent candidacy, or a
# residual option (nulos, no registrados). dim_party tells us whether a
# party_key is a coalition and, if so, which parties it is made of.
#
# This script produces two counts per election cycle:
#   - n_candidacies : distinct vote options (party_key) on the ballot
#   - n_parties     : distinct underlying parties in the race, after
#                     expanding coalitions into their member parties
#
# Run this file top to bottom in RStudio. Set DB_PATH below if the repo is
# not at the working directory RStudio opens in.

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)
setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')

# ── Config ──────────────────────────────────────────────────────────────────

DB_PATH <- "election_data.db"   # adjust if running from a different folder

# party_keys that are not real parties (independents, void/unregistered
# votes) and should be excluded when counting "parties"
NON_PARTY_KEYS <- c(
  "CAND_IND_01", "CAND_IND_02",
  "NO_REGISTRADOS", "NULOS"
)

# ── Connect ─────────────────────────────────────────────────────────────────

con <- dbConnect(SQLite(), DB_PATH)

party_keys_by_election <- dbGetQuery(con, "
  SELECT DISTINCT election_id, party_key
  FROM fact_casilla_vote
  WHERE election_id LIKE 'PRE_%'
")

dim_party <- dbGetQuery(con, "
  SELECT party_key, is_coalition, members
  FROM dim_party
")

dbDisconnect(con)

# ── Expand coalitions into member parties ──────────────────────────────────

coalition_members <- dim_party |>
  filter(is_coalition == 1, !is.na(members), members != "") |>
  mutate(member = strsplit(members, ",")) |>
  unnest(member) |>
  mutate(member = trimws(member)) |>
  select(party_key = party_key, member_key = member)

parties_expanded <- party_keys_by_election |>
  filter(!party_key %in% NON_PARTY_KEYS) |>
  left_join(coalition_members, by = "party_key", relationship = "many-to-many") |>
  mutate(
    # if the party_key is a coalition, use its member parties;
    # otherwise the party_key itself is the party
    party = if_else(is.na(member_key), party_key, member_key)
  )

# ── Counts per election cycle ──────────────────────────────────────────────

candidacies_per_election <- party_keys_by_election |>
  filter(!party_key %in% NON_PARTY_KEYS) |>
  group_by(election_id) |>
  summarise(n_candidacies = n_distinct(party_key), .groups = "drop")

parties_per_election <- parties_expanded |>
  group_by(election_id) |>
  summarise(n_parties = n_distinct(party), .groups = "drop")

party_counts <- candidacies_per_election |>
  left_join(parties_per_election, by = "election_id") |>
  mutate(year = as.integer(sub("PRE_", "", election_id))) |>
  arrange(year) |>
  select(election_id, year, n_candidacies, n_parties)

# ── List of parties per cycle (handy for a table/appendix) ─────────────────

parties_list_per_election <- parties_expanded |>
  distinct(election_id, party) |>
  arrange(election_id, party) |>
  group_by(election_id) |>
  summarise(parties = paste(sort(unique(party)), collapse = ", "), .groups = "drop")

# ── Output ──────────────────────────────────────────────────────────────────

print(party_counts)
print(parties_list_per_election)

# ── Canonical party names (same legal party, different ballot labels) ──────
# party_key is the exact label used on that cycle's ballot; some parties
# changed their registered name/abbreviation across cycles. This map is NOT
# written back to dim_party (see note below) -- it's only used here to roll
# up party history across cycles for reporting.

canonical_map <- c(
  "PAN"                   = "Partido Acción Nacional (PAN)",
  "PARM"                  = "Partido Auténtico de la Revolución Mexicana (PARM)",
  "PFCRN"                 = "Partido del Frente Cardenista de Reconstrucción Nacional (PFCRN)",
  "PPS"                   = "Partido Popular Socialista (PPS)",
  "PRD"                   = "Partido de la Revolución Democrática (PRD)",
  "PRI"                   = "Partido Revolucionario Institucional (PRI)",
  "PT"                    = "Partido del Trabajo (PT)",
  "PVEM"                  = "Partido Verde Ecologista de México (PVEM)",
  "UNO_PDM"                = "Partido Demócrata Mexicano (PDM)",
  "CONVERGENCIA"          = "Movimiento Ciudadano (antes Convergencia) (MC)",
  "MC"                    = "Movimiento Ciudadano (antes Convergencia) (MC)",
  "MOVIMIENTO CIUDADANO"  = "Movimiento Ciudadano (antes Convergencia) (MC)",
  "PSN"                   = "Partido de la Sociedad Nacionalista (PSN)",
  "PAS"                   = "Partido Alianza Social (PAS)",
  "DSPPN"                 = "Partido Democracia Social (DSPPN)",
  "PCD"                   = "Partido Centro Democrático (PCD)",
  "ASDC"                  = "Alternativa Socialdemócrata y Campesina (ASDC)",
  "NVA_A"                 = "Partido Nueva Alianza (PANAL)",
  "PANAL"                 = "Partido Nueva Alianza (PANAL)",
  "NUEVA ALIANZA"         = "Partido Nueva Alianza (PANAL)",
  "ENCUENTRO SOCIAL"      = "Partido Encuentro Social (PES)",
  "MORENA"                = "Movimiento Regeneración Nacional (MORENA)"
)

parties_canonical <- parties_expanded |>
  mutate(
    party_name = canonical_map[party],
    party_name = if_else(is.na(party_name), party, party_name),
    year = as.integer(sub("PRE_", "", election_id))
  ) |>
  distinct(party_name, year) |>
  arrange(party_name, year)

party_history_summary <- parties_canonical |>
  group_by(party_name) |>
  summarise(
    n_cycles = n_distinct(year),
    elections = paste(sort(unique(year)), collapse = ", "),
    .groups = "drop"
  ) |>
  arrange(desc(n_cycles), party_name) |>
  mutate(entry = paste0(party_name, " (", elections, ")"))

# ── Ideology bloc (izquierda / centro / derecha) ────────────────────────────
# Ported from IDEOLOGY_MAP in ui/common.py, which classifies by party_key
# (single parties and, where the raw data only reports a combined coalition
# line, the coalition as a whole -- e.g. "A. MEX." in 2000). Here we need one
# bloc per underlying party, so PSN and PAS (2000) inherit the bloc of the
# only coalition they ever ran in, "A. MEX." = Izquierda, since they never
# appear as standalone party_key rows in the source data.
ideology_bloc <- c(
  "Partido de la Revolución Democrática (PRD)"                        = "Izquierda",
  "Partido del Trabajo (PT)"                                          = "Izquierda",
  "Partido del Frente Cardenista de Reconstrucción Nacional (PFCRN)"  = "Izquierda",
  "Partido Popular Socialista (PPS)"                                  = "Izquierda",
  "Partido Auténtico de la Revolución Mexicana (PARM)"                = "Izquierda",
  "Alternativa Socialdemócrata y Campesina (ASDC)"                    = "Izquierda",
  "Movimiento Regeneración Nacional (MORENA)"                         = "Izquierda",
  "Partido de la Sociedad Nacionalista (PSN)"                         = "Izquierda",
  "Partido Alianza Social (PAS)"                                      = "Izquierda",

  "Partido Acción Nacional (PAN)"                                     = "Derecha",
  "Partido Demócrata Mexicano (PDM)"                                  = "Derecha",
  "Partido Encuentro Social (PES)"                                    = "Derecha",

  "Partido Revolucionario Institucional (PRI)"                        = "Centro",
  "Partido Verde Ecologista de México (PVEM)"                         = "Centro",
  "Partido Centro Democrático (PCD)"                                  = "Centro",
  "Partido Democracia Social (DSPPN)"                                 = "Centro",
  "Movimiento Ciudadano (antes Convergencia) (MC)"                    = "Centro",
  "Partido Nueva Alianza (PANAL)"                                     = "Centro"
)

party_history_summary <- party_history_summary |>
  mutate(bloc = ideology_bloc[party_name]) |>
  relocate(bloc, .after = party_name)

bloc_summary <- party_history_summary |>
  group_by(bloc) |>
  summarise(
    n_parties = n(),
    parties = paste(sub(" \\(.*", "", party_name), collapse = ", "),
    .groups = "drop"
  ) |>
  arrange(factor(bloc, levels = c("Izquierda", "Centro", "Derecha")))

print(party_history_summary)
print(bloc_summary)

party_history_string <- paste(party_history_summary$entry, collapse = "; ")

cat(party_history_string, "\n")


# Uncomment to save results
# write.csv(party_counts, "article_brujula_politica/output_party_counts.csv", row.names = FALSE)
# write.csv(parties_list_per_election, "article_brujula_politica/output_parties_list.csv", row.names = FALSE)
