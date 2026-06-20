# qa_warehouse_vs_csv_2024.R

library(DBI)
library(RSQLite)
library(readr)
library(dplyr)
library(tidyr)
library(stringr)
library(purrr)

DB_PATH <- "election_data.db"

FOLDERS <- tibble::tribble(
  ~folder,                         ~election_id,
  "DIPUTACIONES_FED_MR_2024",       "DIP_MR_2024",
  "DIPUTACIONES_FED_RP_2024",       "DIP_RP_2024",
  "PRESIDENCIA_2024",               "PRE_2024",
  "SENADURIAS_MR_2024",             "SEN_MR_2024",
  "SENADURIAS_RP_2024",             "SEN_RP_2024"
)

OUT_DIR <- "aux_scripts/qa_reports"
dir.create(OUT_DIR, showWarnings = FALSE)

# -----------------------------
# Helpers
# -----------------------------

clean_names <- function(x) {
  x |>
    str_trim() |>
    str_to_upper() |>
    str_replace_all("-", "_") |>
    str_replace_all("\\s+", "_")
}

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
    df |>
      mutate(QA_KEY = 1) |>
      group_by(QA_KEY)
  } else {
    df |>
      group_by(across(all_of(keys)))
  }
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

standard_total_cols <- c(
  "NUM_VOTOS_VALIDOS",
  "NUM_VOTOS_CAN_NREG",
  "NUM_VOTOS_NULOS",
  "TOTAL_VOTOS",
  "LISTA_NOMINAL",
  "CASILLAS",
  "ACTAS_CASILLA_MEC",
  "SECCIONES",
  "MUNICIPIOS",
  "DISTRITOS",
  "ESTADOS"
)

measure_cols <- function(df) {
  names(df)[
    names(df) %in% standard_total_cols |
      (!names(df) %in% id_cols & sapply(df, is.numeric))
  ]
}

compare_tables <- function(expected, actual, keys, file_label, tol = 0) {
  
  if (length(keys) == 0) {
    expected <- expected |> mutate(QA_KEY = 1)
    actual   <- actual   |> mutate(QA_KEY = 1)
    keys <- "QA_KEY"
  }
  
  missing_expected_keys <- setdiff(keys, names(expected))
  missing_actual_keys <- setdiff(keys, names(actual))
  
  if (length(missing_expected_keys) > 0 || length(missing_actual_keys) > 0) {
    return(tibble(
      file = file_label,
      issue = "missing_join_key",
      missing_expected_keys = paste(missing_expected_keys, collapse = ", "),
      missing_actual_keys = paste(missing_actual_keys, collapse = ", ")
    ))
  }
  
  expected <- expected |>
    mutate(across(all_of(keys), as.character))
  
  actual <- actual |>
    mutate(across(all_of(keys), as.character))
  
  expected_measures <- measure_cols(expected)
  actual_measures   <- measure_cols(actual)
  
  common_measures <- intersect(expected_measures, actual_measures)
  common_measures <- setdiff(common_measures, keys)
  
  missing_in_expected_cols <- setdiff(actual_measures, expected_measures)
  missing_in_actual_cols   <- setdiff(expected_measures, actual_measures)
  
  column_issues <- bind_rows(
    if (length(missing_in_expected_cols) > 0) {
      tibble(
        file = file_label,
        issue = "measure_column_missing_in_warehouse",
        metric = missing_in_expected_cols
      )
    },
    if (length(missing_in_actual_cols) > 0) {
      tibble(
        file = file_label,
        issue = "measure_column_missing_in_csv",
        metric = missing_in_actual_cols
      )
    }
  )
  
  if (length(common_measures) == 0) {
    return(bind_rows(
      column_issues,
      tibble(file = file_label, issue = "no_common_measure_columns")
    ))
  }
  
  # For vote/count measures, blank cells in official aggregates usually mean 0,
  # especially for sparse candidates like CAND_IND1 / CAND_IND2.
  expected <- expected |>
    mutate(across(
      all_of(common_measures),
      ~ replace_na(suppressWarnings(as.numeric(.x)), 0)
    ))
  
  actual <- actual |>
    mutate(across(
      all_of(common_measures),
      ~ replace_na(suppressWarnings(as.numeric(.x)), 0)
    ))
  
  expected_long <- expected |>
    select(all_of(keys), all_of(common_measures)) |>
    pivot_longer(
      cols = all_of(common_measures),
      names_to = "metric",
      values_to = "expected"
    )
  
  actual_long <- actual |>
    select(all_of(keys), all_of(common_measures)) |>
    pivot_longer(
      cols = all_of(common_measures),
      names_to = "metric",
      values_to = "actual"
    )
  
  row_comparison <- expected_long |>
    full_join(actual_long, by = c(keys, "metric")) |>
    mutate(
      diff = actual - expected,
      file = file_label,
      issue = case_when(
        is.na(expected) ~ "row_missing_in_warehouse",
        is.na(actual) ~ "row_missing_in_csv",
        abs(diff) > tol ~ "value_mismatch",
        TRUE ~ "ok"
      )
    ) |>
    filter(issue != "ok") |>
    select(file, issue, all_of(keys), metric, expected, actual, diff)
  
  bind_rows(column_issues, row_comparison)
}

# -----------------------------
# Warehouse aggregation
# -----------------------------

warehouse_level <- function(conn, election_id, level) {
  keys <- level_keys(level)
  
  base_sql <- glue::glue("
    SELECT
      f.election_id,
      c.casilla_id,
      c.acta_casilla_mec AS ACTA_CASILLA_MEC,
      c.tipo_casilla AS TIPO_CASILLA,
      c.id_casilla AS ID_CASILLA,
      c.ext_contigua AS EXT_CONTIGUA,
      c.lista_nominal AS LISTA_NOMINAL,
      g.id_estado AS ID_ESTADO,
      g.nombre_estado AS NOMBRE_ESTADO,
      g.id_distrito_federal AS ID_DISTRITO_FEDERAL,
      g.cabecera_distrital_federal AS CABECERA_DISTRITAL_FEDERAL,
      g.id_municipio AS ID_MUNICIPIO,
      g.municipio AS MUNICIPIO,
      g.seccion AS SECCION,
      g.circunscripcion AS CIRCUNSCRIPCION,
      f.party_key AS PARTY_KEY,
      f.votes AS VOTES,
      f.num_votos_validos AS NUM_VOTOS_VALIDOS,
      f.num_votos_can_nreg AS NUM_VOTOS_CAN_NREG,
      f.num_votos_nulos AS NUM_VOTOS_NULOS,
      f.total_votos AS TOTAL_VOTOS
    FROM fact_casilla_vote f
    JOIN dim_casilla c
      ON f.election_id = c.election_id
     AND f.casilla_id = c.casilla_id
    JOIN dim_geography g
      ON c.geo_id = g.geo_id
    WHERE f.election_id = '{election_id}'
  ")
  
  base <- dbGetQuery(conn, base_sql)
  names(base) <- clean_names(names(base))
  
  if (nrow(base) == 0) {
    return(tibble())
  }
  
  votes <- base |>
    group_by_level(keys) |>
    group_by(PARTY_KEY, .add = TRUE) |>
    summarise(
      VOTES = sum(VOTES, na.rm = TRUE),
      .groups = "drop"
    ) |>
    pivot_wider(
      names_from = PARTY_KEY,
      values_from = VOTES,
      values_fill = 0
    )
  
  casilla_meta <- base |>
    distinct(
      CASILLA_ID,
      across(any_of(keys)),
      ID_ESTADO,
      ID_MUNICIPIO,
      ID_DISTRITO_FEDERAL,
      SECCION,
      LISTA_NOMINAL,
      NUM_VOTOS_VALIDOS,
      NUM_VOTOS_CAN_NREG,
      NUM_VOTOS_NULOS,
      TOTAL_VOTOS
    )
  
  totals <- casilla_meta |>
    group_by_level(keys) |>
    summarise(
      LISTA_NOMINAL = sum(LISTA_NOMINAL, na.rm = TRUE),
      NUM_VOTOS_VALIDOS = sum(NUM_VOTOS_VALIDOS, na.rm = TRUE),
      NUM_VOTOS_CAN_NREG = sum(NUM_VOTOS_CAN_NREG, na.rm = TRUE),
      NUM_VOTOS_NULOS = sum(NUM_VOTOS_NULOS, na.rm = TRUE),
      TOTAL_VOTOS = sum(TOTAL_VOTOS, na.rm = TRUE),
      CASILLAS = n_distinct(CASILLA_ID),
      ACTAS_CASILLA_MEC = n_distinct(CASILLA_ID),
      SECCIONES = n_distinct(SECCION),
      MUNICIPIOS = n_distinct(ID_MUNICIPIO),
      DISTRITOS = n_distinct(ID_DISTRITO_FEDERAL),
      ESTADOS = n_distinct(ID_ESTADO),
      .groups = "drop"
    )
  
  join_keys <- keys
  if (length(join_keys) == 0) {
    join_keys <- "QA_KEY"
  }
  
  out <- votes |>
    full_join(totals, by = join_keys)
  
  if ("QA_KEY" %in% names(out)) {
    out <- out |> select(-QA_KEY)
  }
  
  out
}

# -----------------------------
# Main QA loop
# -----------------------------

run_qa <- function() {
  conn <- dbConnect(SQLite(), DB_PATH)
  on.exit(dbDisconnect(conn), add = TRUE)
  
  all_results <- list()
  
  for (i in seq_len(nrow(FOLDERS))) {
    folder <- FOLDERS$folder[i]
    election_id <- FOLDERS$election_id[i]
    
    message("\nChecking ", folder, " / ", election_id)
    
    csv_dir <- file.path("data", folder, "CSV")
    csv_paths <- list.files(csv_dir, pattern = "\\.csv$", full.names = TRUE)
    
    # Skip casilla source itself and candidate integration file
    csv_paths <- csv_paths[
      !str_detect(basename(csv_paths), "_NAL_CAS") &
        !str_detect(basename(csv_paths), "INTEGRACION_CARGOS")
    ]
    
    for (path in csv_paths) {
      file <- basename(path)
      level <- detect_level(file)
      
      if (is.na(level)) {
        message("  Skipping unknown level: ", file)
        next
      }
      
      message("  ", file, " => ", level)
      
      official <- read_ine_csv(path)
      keys <- level_keys(level)
      
      # Some files use NUMERO_CIRCUNSCRIPCION instead of CIRCUNSCRIPCION
      if (level == "CIR" && !"CIRCUNSCRIPCION" %in% names(official)) {
        if ("NUMERO_CIRCUNSCRIPCION" %in% names(official)) {
          official <- official |> rename(CIRCUNSCRIPCION = NUMERO_CIRCUNSCRIPCION)
        }
      }
      
      warehouse <- warehouse_level(conn, election_id, level)
      
      if (nrow(warehouse) == 0) {
        all_results[[length(all_results) + 1]] <- tibble(
          file = file,
          issue = "warehouse_level_empty",
          level = level
        )
        next
      }
      
      missing_keys <- setdiff(keys, names(official))
      if (length(missing_keys) > 0) {
        all_results[[length(all_results) + 1]] <- tibble(
          file = file,
          issue = paste0("missing_key_in_csv: ", paste(missing_keys, collapse = ", ")),
          level = level
        )
        next
      }
      
      result <- compare_tables(
        expected = warehouse,
        actual = official,
        keys = keys,
        file_label = file,
        tol = 0
      )
      
      if (nrow(result) == 0) {
        result <- tibble(
          file = file,
          issue = "ok",
          level = level
        )
      } else {
        result <- result |> mutate(level = level, .before = 1)
      }
      
      all_results[[length(all_results) + 1]] <- result
    }
  }
  
  final <- bind_rows(all_results)
  
  write_csv(final, file.path(OUT_DIR, "warehouse_vs_official_csv_mismatches.csv"))
  
  summary <- final |>
    count(level, file, issue, name = "n") |>
    arrange(desc(n))
  
  write_csv(summary, file.path(OUT_DIR, "qa_summary.csv"))
  
  print(summary)
  
  invisible(final)
}

qa_results <- run_qa()

example_sec <- qa_results |>
  filter(
    file == "2024_SEE_SEN_FED_RP_NAL_SEC.csv",
    metric == "CASILLAS",
    diff == -1
  ) |>
  slice(1)

example_sec


base <- dbGetQuery(conn, "
SELECT DISTINCT
  g.id_estado,
  g.seccion,
  c.casilla_id,
  c.tipo_casilla,
  c.id_casilla,
  c.ext_contigua,
  c.acta_casilla_mec
FROM fact_casilla_vote f
JOIN dim_casilla c
  ON f.election_id = c.election_id
 AND f.casilla_id = c.casilla_id
JOIN dim_geography g
  ON c.geo_id = g.geo_id
WHERE f.election_id = 'SEN_RP_2024'
  AND g.id_estado = 1
  AND g.seccion = 20
ORDER BY c.casilla_id
")

base

mismatch_sections <- qa_results |>
  filter(
    file == "2024_SEE_SEN_FED_RP_NAL_SEC.csv",
    metric == "CASILLAS",
    diff == -1
  ) |>
  select(ID_ESTADO, SECCION)

test <- base_all |>
  semi_join(
    mismatch_sections,
    by = c(
      "id_estado" = "ID_ESTADO",
      "seccion" = "SECCION"
    )
  ) |>
  count(acta_casilla_mec, sort = TRUE)

rp_casillas <- dbGetQuery(conn, "
SELECT DISTINCT
  g.id_estado,
  g.seccion,
  c.casilla_id,
  c.acta_casilla_mec
FROM fact_casilla_vote f
JOIN dim_casilla c
  ON f.election_id = c.election_id
 AND f.casilla_id = c.casilla_id
JOIN dim_geography g
  ON c.geo_id = g.geo_id
WHERE f.election_id = 'SEN_RP_2024'
")

mismatch_sections <- qa_results |>
  filter(
    file == "2024_SEE_SEN_FED_RP_NAL_SEC.csv",
    metric == "CASILLAS",
    diff == -1
  ) |>
  transmute(
    id_estado = as.integer(ID_ESTADO),
    seccion = as.integer(SECCION)
  )

rp_casillas |>
  semi_join(
    mismatch_sections,
    by = c("id_estado", "seccion")
  ) |>
  count(acta_casilla_mec, sort = TRUE)


rp_casillas |>
  mutate(
    is_special = str_detect(acta_casilla_mec, "^(SMR|SRP|VA|VMRE)")
  ) |>
  semi_join(mismatch_sections, by = c("id_estado", "seccion")) |>
  group_by(id_estado, seccion) |>
  summarise(
    total_warehouse = n_distinct(casilla_id),
    regular_warehouse = n_distinct(casilla_id[!is_special]),
    special_warehouse = n_distinct(casilla_id[is_special]),
    special_types = paste(sort(unique(acta_casilla_mec[is_special])), collapse = ", "),
    .groups = "drop"
  ) |>
  left_join(
    qa_results |>
      filter(
        file == "2024_SEE_SEN_FED_RP_NAL_SEC.csv",
        metric == "CASILLAS"
      ) |>
      transmute(
        id_estado = as.integer(ID_ESTADO),
        seccion = as.integer(SECCION),
        warehouse_expected = expected,
        official_actual = actual,
        diff
      ),
    by = c("id_estado", "seccion")
  ) |>
  mutate(
    official_equals_regular = official_actual == regular_warehouse
  ) |>
  count(official_equals_regular, sort = TRUE)


library(dplyr)
library(stringr)
library(DBI)

# 1. All warehouse casillas for SEN_RP_2024
rp_casillas <- dbGetQuery(conn, "
SELECT DISTINCT
  g.id_estado,
  g.seccion,
  c.casilla_id,
  c.acta_casilla_mec
FROM fact_casilla_vote f
JOIN dim_casilla c
  ON f.election_id = c.election_id
 AND f.casilla_id = c.casilla_id
JOIN dim_geography g
  ON c.geo_id = g.geo_id
WHERE f.election_id = 'SEN_RP_2024'
")

# 2. Sections where official CASILLAS is 1 lower than warehouse
mismatch_sections <- qa_results |>
  filter(
    file == "2024_SEE_SEN_FED_RP_NAL_SEC.csv",
    metric == "CASILLAS",
    issue == "value_mismatch",
    diff == -1
  ) |>
  transmute(
    id_estado = as.integer(ID_ESTADO),
    seccion = as.integer(SECCION),
    warehouse_expected = expected,
    official_actual = actual,
    diff
  )

# 3. Summarise special casillas by mismatching section
special_summary <- rp_casillas |>
  semi_join(mismatch_sections, by = c("id_estado", "seccion")) |>
  group_by(id_estado, seccion) |>
  summarise(
    total_casillas = n_distinct(casilla_id),
    regular_casillas = n_distinct(casilla_id[
      !str_detect(acta_casilla_mec, "^(VA|VMRE|SMR|SRP)")
    ]),
    va_casillas = n_distinct(casilla_id[
      str_detect(acta_casilla_mec, "^VA")
    ]),
    smr_casillas = n_distinct(casilla_id[
      str_detect(acta_casilla_mec, "^SMR")
    ]),
    srp_casillas = n_distinct(casilla_id[
      str_detect(acta_casilla_mec, "^SRP")
    ]),
    vmre_casillas = n_distinct(casilla_id[
      str_detect(acta_casilla_mec, "^VMRE|^VeMRE")
    ]),
    special_types = paste(
      sort(unique(acta_casilla_mec[
        str_detect(acta_casilla_mec, "^(VA|VMRE|VeMRE|SMR|SRP)")
      ])),
      collapse = ", "
    ),
    all_types = paste(sort(unique(acta_casilla_mec)), collapse = ", "),
    .groups = "drop"
  ) |>
  left_join(
    mismatch_sections,
    by = c("id_estado", "seccion")
  ) |>
  mutate(
    official_equals_regular = official_actual == regular_casillas,
    official_equals_without_va = official_actual == total_casillas - va_casillas,
    official_equals_smr_srp_as_one =
      official_actual == total_casillas - pmin(smr_casillas, srp_casillas)
  )

# 4. Summary: which rule explains the mismatch?
special_summary |>
  summarise(
    n_sections = n(),
    official_equals_regular = sum(official_equals_regular, na.rm = TRUE),
    official_equals_without_va = sum(official_equals_without_va, na.rm = TRUE),
    official_equals_smr_srp_as_one = sum(official_equals_smr_srp_as_one, na.rm = TRUE)
  )

# 5. Inspect sections with both SMR and SRP
special_summary |>
  filter(smr_casillas > 0, srp_casillas > 0) |>
  select(
    id_estado, seccion,
    warehouse_expected, official_actual, diff,
    total_casillas, regular_casillas,
    smr_casillas, srp_casillas, va_casillas,
    official_equals_smr_srp_as_one,
    all_types
  ) |>
  arrange(id_estado, seccion) |>
  print(n = 20)

# 6. Inspect one concrete SMR/SRP example
suspect <- special_summary |>
  filter(smr_casillas > 0, srp_casillas > 0) |>
  slice(1)

rp_casillas |>
  filter(
    id_estado == suspect$id_estado,
    seccion == suspect$seccion
  ) |>
  arrange(acta_casilla_mec)

# 7. Distribution of special patterns
special_summary |>
  count(
    va_casillas,
    smr_casillas,
    srp_casillas,
    vmre_casillas,
    official_equals_smr_srp_as_one,
    sort = TRUE
  )


# For SEN_RP_2024 section-level CASILLAS mismatches with diff == -1:
#   
#   1. 827 sections have both SMR01 and SRP01.
# * Warehouse counts both.
# * Official counts them as one casilla-equivalent.
# * Rule: SMR + SRP = 1, not 2.
# 2. 245 sections have VA01.
# * Warehouse counts VA01.
# * Official excludes VA01 from CASILLAS.
# * Rule: VA does not count toward CASILLAS.

qa_results |>
  filter(
    issue == "value_mismatch",
    !metric %in% c(
      "CASILLAS",
      "ACTAS_CASILLA_MEC",
      "SECCIONES",
      "MUNICIPIOS",
      "DISTRITOS",
      "ESTADOS"
    )
  ) |>
  count(metric, sort = TRUE)

qa_results |>
  filter(
    metric == "PVEM_PT_MORENA",
    issue == "value_mismatch"
  ) |>
  count(file, sort = TRUE)
