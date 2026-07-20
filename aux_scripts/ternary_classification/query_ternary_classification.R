# ─────────────────────────────────────────────────────────────────────────────
# Municipio ternary classification — query helpers
# Table built by aux_scripts/ternary_classification/build_municipio_ternary_classification.py
# Regenerate with:
#   python aux_scripts/ternary_classification/build_municipio_ternary_classification.py
#
# One row per municipio x presidential election (1994-2024). "category" is
# the Base / Plural / Contenciosa / Empate label from the majority-rule
# ternary classifier in ui/common.py::classify_ternary — a bloc only counts
# as "Base" with an outright majority (>50%); below that the other two
# blocs could in principle coalesce and outvote it, so it's "Plural" (one
# bloc clearly leads, but the 2nd/3rd blocs are themselves near-tied, so
# there's no real top-two race), "Contenciosa" (two blocs close, genuinely
# fighting for 2nd/1st), or "Empate" (all three near 33/33/33).
# ─────────────────────────────────────────────────────────────────────────────

library(arrow)
library(dplyr)

PARQUET_PATH <- "data/materialized/municipio_ternary_classification.parquet"
CSV_PATH     <- "data/materialized/municipio_ternary_classification.csv"

# arrow is the fast path; falls back to the CSV if the package isn't installed.
load_ternary_classification <- function() {
  if (requireNamespace("arrow", quietly = TRUE) && file.exists(PARQUET_PATH)) {
    arrow::read_parquet(PARQUET_PATH)
  } else {
    readr::read_csv(CSV_PATH, show_col_types = FALSE)
  }
}

ternary <- load_ternary_classification()

# ── Schema ───────────────────────────────────────────────────────────────────
# id_estado, nombre_estado, municipio, municipio_key
# year, election_id                    -- e.g. 2024, "PRE_2024"
# L, R, C                              -- raw votes per bloc (Izquierda/Derecha/Centro)
# total_votos, pct_L, pct_R, pct_C
# category                             -- "Base Izquierda" | "Base Derecha" | "Base Centro"
#                                          "Plural Izquierda" | "Plural Derecha" | "Plural Centro"
#                                          "Contenciosa Izquierda-Centro" | "...-Derecha" | "Centro-Derecha"
#                                          "Empate"
# tie_radius                           -- classifier parameter used (default 8)

glimpse(ternary)

# ── Quick sanity checks ──────────────────────────────────────────────────────

# National mix of categories per election
ternary |>
  count(year, category) |>
  group_by(year) |>
  mutate(pct = n / sum(n) * 100) |>
  arrange(year, desc(n)) |>
  print(n = 50)

# Municipios that flipped from "Base X" to "Empate"/"Contenciosa" between two
# elections -- likely the most interesting cases (formerly safe seats
# becoming genuinely contested).
ternary |>
  filter(year %in% c(2018, 2024)) |>
  select(municipio_key, nombre_estado, municipio, year, category) |>
  tidyr::pivot_wider(names_from = year, values_from = category, names_prefix = "cat_") |>
  filter(startsWith(cat_2018, "Base"), !startsWith(cat_2024, "Base")) |>
  arrange(nombre_estado, municipio)
