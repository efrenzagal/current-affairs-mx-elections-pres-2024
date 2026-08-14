# Export the legislative roll-call votes and their applied OpenAI
# classifications from election_data.db to Google Sheets.
#
# Intended use: open this file in RStudio, edit the CONFIGURATION block, and
# click Source. googlesheets4 handles interactive browser authentication; no
# Google Cloud project key is required.
#
# Install the dependencies once, if needed:
# install.packages(c("DBI", "RSQLite", "googlesheets4", "arrow"))

find_repo_root <- function(start = getwd()) {
  path <- normalizePath(start, winslash = "/", mustWork = TRUE)
  repeat {
    if (file.exists(file.path(path, "election_data.db"))) return(path)
    parent <- dirname(path)
    if (identical(parent, path)) break
    path <- parent
  }
  stop(
    "Could not find election_data.db. Run this file from the repository ",
    "or one of its subdirectories.",
    call. = FALSE
  )
}

load_review_secrets <- function(repo_root = find_repo_root()) {
  path <- file.path(repo_root, "config", "legislative_vote_review.env")
  if (!file.exists(path)) {
    stop(
      "Missing ", path, ". Copy config/legislative_vote_review.env.example ",
      "to that path and fill in the local values.",
      call. = FALSE
    )
  }
  readRenviron(path)
  invisible(path)
}

env_or_null <- function(name) {
  value <- trimws(Sys.getenv(name, unset = ""))
  if (nzchar(value)) value else NULL
}

load_review_secrets()

# ---- CONFIGURATION ---------------------------------------------------------

# Loaded from the gitignored config/legislative_vote_review.env. An empty Sheet
# value creates a new spreadsheet; an ID or URL updates an existing one.
SPREADSHEET <- env_or_null("LEGISLATIVE_REVIEW_SHEET_ID")

# Optional Google account hint. An empty value lets you choose during auth.
GOOGLE_EMAIL <- env_or_null("LEGISLATIVE_REVIEW_GOOGLE_EMAIL")

# NULL exports every classified legislature in the database. For a smaller
# validation cut, use an integer vector such as c(64, 65, 66).
LEGISLATURES <- c(66)

# FALSE retains every roll call in the selected legislatures and leaves
# classification cells blank if a future ingestion has not been classified.
# TRUE exports only rows that have an applied OpenAI classification.
ONLY_CLASSIFIED <- FALSE

# Save the exact database extract locally before uploading it. Snapshots are
# timestamped and gitignored under data/legislative_vote_review_cache/exports/.
WRITE_PARQUET_CACHE <- TRUE

# ---------------------------------------------------------------------------

required_packages <- c(
  "DBI", "RSQLite", "googlesheets4",
  if (WRITE_PARQUET_CACHE) "arrow"
)
missing_packages <- required_packages[
  !vapply(required_packages, requireNamespace, logical(1), quietly = TRUE)
]
if (length(missing_packages) > 0) {
  stop(
    "Install the missing package(s) first: install.packages(c(",
    paste(sprintf('"%s"', missing_packages), collapse = ", "),
    "))",
    call. = FALSE
  )
}

validate_legislatures <- function(legislatures) {
  if (is.null(legislatures)) return(NULL)
  if (!is.numeric(legislatures) || anyNA(legislatures) ||
      any(legislatures != as.integer(legislatures))) {
    stop("LEGISLATURES must be NULL or a vector of whole numbers.", call. = FALSE)
  }
  unique(as.integer(legislatures))
}

filter_legislatures <- function(data, legislatures) {
  if (is.null(legislatures)) return(data)
  data[data$legislatura %in% legislatures, , drop = FALSE]
}

read_diputados_votes <- function(con) {
  DBI::dbGetQuery(con, "
    WITH resultados AS (
      SELECT
        gaceta_vote_id,
        SUM(CASE WHEN vote_choice = 'Favor' THEN count ELSE 0 END) AS favor,
        SUM(CASE WHEN vote_choice = 'Contra' THEN count ELSE 0 END) AS contra,
        SUM(CASE WHEN vote_choice = 'Abstención' THEN count ELSE 0 END) AS abstencion,
        SUM(CASE WHEN vote_choice = 'Ausente' THEN count ELSE 0 END) AS ausente,
        SUM(CASE WHEN vote_choice = 'Quórum *' THEN count ELSE 0 END) AS quorum_sin_voto,
        SUM(CASE WHEN vote_choice = 'Total' THEN count ELSE 0 END) AS total_registrado
      FROM fact_gaceta_vote_summary
      WHERE party_key = 'Total'
      GROUP BY gaceta_vote_id
    )
    SELECT
      'Cámara de Diputados' AS camara,
      v.gaceta_vote_id AS id_votacion,
      v.legislature AS legislatura,
      v.vote_date AS fecha,
      v.title AS titulo_descripcion,
      v.vote_context AS contexto_fuente,
      v.chamber AS organo_fuente,
      v.gaceta_number AS gaceta_numero,
      v.gaceta_date AS gaceta_fecha,
      v.status_text AS estado_fuente,
      v.source_url AS url_fuente,
      r.favor AS votos_favor,
      r.contra AS votos_contra,
      r.abstencion AS votos_abstencion,
      r.ausente AS votos_ausente,
      r.quorum_sin_voto,
      r.total_registrado,
      c.origen,
      c.etapa_votacion,
      c.tipo_instrumento,
      c.tema_politica,
      NULL AS confianza_modelo,
      c.requiere_revision,
      c.evidencia,
      c.review_status,
      c.review_notes,
      c.model AS modelo_openai,
      c.prompt_version,
      c.classified_at AS clasificado_en
    FROM dim_gaceta_vote AS v
    LEFT JOIN resultados AS r USING (gaceta_vote_id)
    LEFT JOIN fact_gaceta_vote_classification AS c USING (gaceta_vote_id)
    ORDER BY v.legislature, v.vote_date, v.gaceta_vote_id
  ")
}

read_senado_votes <- function(con) {
  DBI::dbGetQuery(con, "
    WITH detalle AS (
      SELECT
        votacion_id,
        SUM(CASE WHEN voto = 'AUSENTE' THEN 1 ELSE 0 END) AS ausente,
        SUM(CASE WHEN voto IS NULL OR TRIM(voto) = '' THEN 1 ELSE 0 END) AS sin_dato
      FROM fact_senador_vote
      GROUP BY votacion_id
    )
    SELECT
      'Senado de la República' AS camara,
      CAST(v.votacion_id AS TEXT) AS id_votacion,
      v.legislature AS legislatura,
      v.vote_date AS fecha,
      v.description AS titulo_descripcion,
      v.vote_type AS tipo_voto_fuente,
      v.period_type AS tipo_periodo,
      v.ordinal_period AS periodo_ordinal,
      v.exercise_year AS anio_ejercicio,
      v.url AS url_fuente,
      v.en_pro AS votos_favor,
      v.en_contra AS votos_contra,
      v.abstencion AS votos_abstencion,
      d.ausente AS votos_ausente_detalle,
      d.sin_dato AS votos_sin_dato_detalle,
      (v.en_pro + v.en_contra + v.abstencion) AS total_publicado,
      c.origen,
      c.etapa_votacion,
      c.tipo_instrumento,
      c.tema_politica,
      c.confianza AS confianza_modelo,
      c.requiere_revision,
      c.evidencia,
      c.model AS modelo_openai,
      c.prompt_version,
      c.classified_at AS clasificado_en
    FROM dim_senado_vote AS v
    LEFT JOIN detalle AS d USING (votacion_id)
    LEFT JOIN fact_senado_vote_classification AS c USING (votacion_id)
    ORDER BY v.legislature, v.vote_date, v.votacion_id
  ")
}

make_coverage <- function(diputados, senado) {
  all_votes <- rbind(
    data.frame(
      camara = "Cámara de Diputados",
      legislatura = diputados$legislatura,
      clasificado = !is.na(diputados$modelo_openai)
    ),
    data.frame(
      camara = "Senado de la República",
      legislatura = senado$legislatura,
      clasificado = !is.na(senado$modelo_openai)
    )
  )

  if (nrow(all_votes) == 0) {
    return(data.frame(
      camara = character(), legislatura = integer(), votos = integer(),
      clasificados_openai = integer(), sin_clasificar = integer()
    ))
  }

  totals <- aggregate(
    rep.int(1L, nrow(all_votes)),
    by = list(camara = all_votes$camara, legislatura = all_votes$legislatura),
    FUN = sum
  )
  classified <- aggregate(
    as.integer(all_votes$clasificado),
    by = list(camara = all_votes$camara, legislatura = all_votes$legislatura),
    FUN = sum
  )
  names(totals)[3] <- "votos"
  names(classified)[3] <- "clasificados_openai"
  result <- merge(totals, classified, by = c("camara", "legislatura"))
  result$sin_clasificar <- result$votos - result$clasificados_openai
  result[order(result$camara, result$legislatura), ]
}

write_tab <- function(ss, sheet, data) {
  # sheet_write() creates the tab when absent and replaces its contents when it
  # exists, making reruns deterministic without deleting the spreadsheet. It
  # also formats and freezes the header row by default.
  googlesheets4::sheet_write(data, ss = ss, sheet = sheet)
}

new_cache_dir <- function(repo_root, direction) {
  timestamp <- format(Sys.time(), "%Y%m%dT%H%M%S")
  path <- file.path(
    repo_root, "data", "legislative_vote_review_cache", direction,
    paste0(timestamp, "-", Sys.getpid())
  )
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
  path
}

write_export_cache <- function(diputados, senado, coverage, repo_root) {
  path <- new_cache_dir(repo_root, "exports")
  arrow::write_parquet(diputados, file.path(path, "diputados.parquet"))
  arrow::write_parquet(senado, file.path(path, "senado.parquet"))
  arrow::write_parquet(coverage, file.path(path, "cobertura.parquet"))
  path
}

export_legislative_votes <- function(
    spreadsheet = SPREADSHEET,
    google_email = GOOGLE_EMAIL,
    legislatures = LEGISLATURES,
    only_classified = ONLY_CLASSIFIED,
    write_parquet_cache = WRITE_PARQUET_CACHE) {
  legislatures <- validate_legislatures(legislatures)
  repo_root <- find_repo_root()
  db_path <- file.path(repo_root, "election_data.db")

  con <- DBI::dbConnect(RSQLite::SQLite(), db_path, flags = RSQLite::SQLITE_RO)
  on.exit(DBI::dbDisconnect(con), add = TRUE)

  diputados <- filter_legislatures(read_diputados_votes(con), legislatures)
  senado <- filter_legislatures(read_senado_votes(con), legislatures)
  coverage <- make_coverage(diputados, senado)

  if (isTRUE(only_classified)) {
    diputados <- diputados[!is.na(diputados$modelo_openai), , drop = FALSE]
    senado <- senado[!is.na(senado$modelo_openai), , drop = FALSE]
  }
  if (nrow(diputados) + nrow(senado) == 0) {
    stop("No votes match the selected configuration.", call. = FALSE)
  }

  cache_path <- NULL
  if (isTRUE(write_parquet_cache)) {
    if (!requireNamespace("arrow", quietly = TRUE)) {
      stop("Install arrow to write the Parquet cache: install.packages('arrow').")
    }
    cache_path <- write_export_cache(diputados, senado, coverage, repo_root)
    message("Local Parquet snapshot: ", cache_path)
  }

  googlesheets4::gs4_auth(email = google_email)
  create_new <- is.null(spreadsheet) ||
    (is.character(spreadsheet) && length(spreadsheet) == 1L &&
       !nzchar(trimws(spreadsheet)))
  if (create_new) {
    spreadsheet <- googlesheets4::gs4_create(
      paste0("Votaciones legislativas clasificadas - ", Sys.Date()),
      sheets = list(Cobertura = coverage)
    )
  } else {
    spreadsheet <- googlesheets4::as_sheets_id(spreadsheet)
    write_tab(spreadsheet, "Cobertura", coverage)
  }

  if (nrow(diputados) > 0) write_tab(spreadsheet, "Diputados", diputados)
  if (nrow(senado) > 0) write_tab(spreadsheet, "Senado", senado)

  spreadsheet_id <- googlesheets4::as_sheets_id(spreadsheet)
  spreadsheet_url <- paste0(
    "https://docs.google.com/spreadsheets/d/", as.character(spreadsheet_id)
  )
  message(
    "Export complete: ", nrow(diputados), " Cámara rows and ",
    nrow(senado), " Senado rows."
  )
  message("Google Sheet: ", spreadsheet_url)
  invisible(list(
    spreadsheet = spreadsheet_id,
    url = spreadsheet_url,
    cache_path = cache_path,
    coverage = coverage,
    diputados = diputados,
    senado = senado
  ))
}

# Clicking Source in RStudio runs the export. When this file is sourced from a
# non-interactive pipeline, call export_legislative_votes() explicitly instead.
if (interactive()) {
  export_result <- export_legislative_votes()
}
