# =============================================================================
# diagnostic_casillas.R
# =============================================================================
library(tidyverse)
library(janitor)

cat("Reading CSV...\n")

# Read the raw file and clean names properly
raw <- read_csv(
  "~/PRESIDENCIA_2024/CSV/2024_SEE_PRE_NAL_CAS.csv",
  locale = locale(encoding = "latin1"),
  show_col_types = FALSE
) |> clean_names()
{
cat("\n1. RAW ROW COUNTS\n")
cat("--------------------------------------------------\n")
cat("Total rows in CSV:  ", scales::comma(nrow(raw)), "\n")
cat("INE Ground Truth:   170,181\n")
cat("Difference:         ", scales::comma(nrow(raw) - 170181), "\n")

cat("\n2. DO THESE COLUMNS UNIQUELY IDENTIFY A ROW?\n")
cat("--------------------------------------------------\n")
# Test the combination of columns we suspect forms the unique key
unique_keys <- raw |>
  select(id_estado, id_distrito_federal, id_municipio, seccion, tipo_casilla, id_casilla, ext_contigua) |>
  distinct() |>
  nrow()

cat("Distinct keys found: ", scales::comma(unique_keys), "\n")

if (unique_keys == nrow(raw)) {
  cat("Result: YES! These 7 columns form a perfect unique key for every row.\n")
} else {
  cat("Result: NO. There are duplicates. The unique keys are less than the total rows.\n")
}

cat("\n3. WHAT ARE THE 585 EXTRA ROWS?\n")
cat("--------------------------------------------------\n")
# Let's look at the breakdown of 'tipo_casilla'
# Standard casillas are usually B (Básica), C (Contigua), E (Extraordinaria), S (Especial)
# M might be Mesa de Escrutinio (Postal, Prison, etc.)
tipo_breakdown <- raw |> 
  count(tipo_casilla, sort = TRUE) |> 
  mutate(is_standard = tipo_casilla %in% c("B", "C", "E", "S"))

print(tipo_breakdown)

# Count standard vs non-standard
standard_count <- sum(tipo_breakdown$n[tipo_breakdown$is_standard])
special_count  <- sum(tipo_breakdown$n[!tipo_breakdown$is_standard])

cat("\nStandard casillas (B, C, E, S): ", scales::comma(standard_count), "\n")
cat("Special tables (M, etc):        ", scales::comma(special_count), "\n")

if (standard_count == 170181) {
  cat("\n✅ MYSTERY SOLVED!\n")
  cat("The ground truth of 170,181 only counts physical 'Standard' casillas.\n")
  cat("The 585 extra rows are Special Mesas (e.g., postal voting, prison voting).\n")
} else {
  cat("\n❌ Still a mystery. The numbers don't perfectly align with 170,181.\n")
}
}
