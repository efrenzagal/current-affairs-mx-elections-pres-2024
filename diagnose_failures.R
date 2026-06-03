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
{
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
dim_dis |> filter(id_distrito_federal == 0 | is.na(id_distrito_federal)) |> print(n = Inf)

cat("\n── dim_distritos per estado (how many districts per state?):\n")
dim_dis |>
  count(id_estado) |>
  arrange(desc(n)) |>
  print()
}

# =============================================================================
# FAILURE 2 & 3: NA state in winners vs estado reconciliation
# =============================================================================
{
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
  print(width = Inf)

cat("\n── estado parquet — id_estado values present (candidato view):\n")
estado |>
  filter(vote_type == "candidato") |>
  pull(id_estado) |>
  unique() |>
  sort() |>
  print()

cat("\n── Are there NA id_estado values in estado parquet?\n")
estado |> filter(is.na(id_estado)) |> count(vote_type, party_key) |> print(width = Inf)

cat("\n── Top vote-getter per state in estado candidato view:\n")
estado |>
  filter(vote_type == "candidato", !is.na(num_votos)) |>
  group_by(id_estado) |>
  slice_max(num_votos, n = 1, with_ties = FALSE) |>
  ungroup() |>
  select(id_estado, party_key, num_votos) |>
  arrange(id_estado) |>
  print()
}

# =============================================================================
# BONUS: Inspect KNOWN_BLANK_MR — is the Estado de México D23 gap real?
# =============================================================================
{
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
}


library(tidyverse)
library(arrow)

TIDY_DIR <- "~/electoral_tidy"
winners <- read_parquet(file.path(TIDY_DIR, "winners_2024_pre.parquet"))
estado  <- read_parquet(file.path(TIDY_DIR, "votes_estado_2024_pre.parquet"))

cat("── Distinct nombre_actor_politico values in DIP_MR winners:\n")
winners |>
  filter(tipo_de_candidatura == "DIP_MR") |>
  count(nombre_actor_politico, partido_politico) |>
  arrange(nombre_actor_politico) |>
  print(n = Inf)

cat("\n── Distinct party_key values in estado candidato view:\n")
estado |>
  filter(vote_type == "candidato") |>
  count(party_key) |>
  arrange(party_key) |>
  print()

cat("\n── The 5 mismatched states — what does winners say vs parquet?\n")
dip_per_state <- winners |>
  filter(tipo_de_candidatura == "DIP_MR", !is.na(id_estado)) |>
  count(id_estado, nombre_estado, nombre_actor_politico, partido_politico)

estado_top <- estado |>
  filter(vote_type == "candidato", !is.na(num_votos)) |>
  group_by(id_estado) |>
  slice_max(num_votos, n = 1, with_ties = FALSE) |>
  ungroup()

dip_per_state |>
  left_join(estado_top |> select(id_estado, parquet_key = party_key), by = "id_estado") |>
  filter(nombre_estado %in% c("BAJA CALIFORNIA", "HIDALGO", "NUEVO LEON", "QUERETARO", "TABASCO")) |>
  arrange(nombre_estado, desc(n)) |>
  print(n = Inf)
