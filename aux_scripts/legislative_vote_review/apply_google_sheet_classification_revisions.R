# Apply deliberate manual classification revisions from the Google Sheet made
# by export_legislative_votes_to_google_sheets.R back to election_data.db.
#
# IMPORTANT: sourcing this file never writes to SQLite. First preview changes:
#
#   revision_preview <- apply_google_sheet_revisions(
#     spreadsheet = "PASTE_GOOGLE_SHEET_URL_HERE"
#   )
#   View(revision_preview)
#
# Only after reviewing the preview, apply the same diff explicitly:
#
#   applied <- apply_google_sheet_revisions(
#     spreadsheet = "PASTE_GOOGLE_SHEET_URL_HERE",
#     apply = TRUE
#   )
#
# Authentication is interactive through googlesheets4. Source vote totals and
# OpenAI provenance fields are intentionally read-only: this importer updates
# only manual classification fields.

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

# Loaded from the gitignored config/legislative_vote_review.env. Sourcing this
# file still performs no authentication and no database writes.
SPREADSHEET <- env_or_null("LEGISLATIVE_REVIEW_SHEET_ID")
GOOGLE_EMAIL <- env_or_null("LEGISLATIVE_REVIEW_GOOGLE_EMAIL")

# Before apply = TRUE updates SQLite, cache the exact Sheet input, current
# database classifications, and before/after diff as timestamped Parquet files.
WRITE_PARQUET_BACKUP <- TRUE

# ---------------------------------------------------------------------------

required_packages <- c(
  "DBI", "RSQLite", "googlesheets4",
  if (WRITE_PARQUET_BACKUP) "arrow"
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

COMMON_FIELDS <- c(
  "origen", "etapa_votacion", "tipo_instrumento", "tema_politica",
  "requiere_revision", "evidencia"
)
DIPUTADOS_FIELDS <- c(COMMON_FIELDS, "review_status", "review_notes")

ALLOWED_STAGE <- c(
  "en_lo_general", "en_lo_particular", "en_lo_general_y_particular",
  "articulos_reservados_o_modificacion", "procedimental",
  "asunto_completo_o_no_especificado", "no_claro"
)
ALLOWED_INSTRUMENT <- c(
  "legislativo", "constitucional", "presupuesto_finanzas_publicas",
  "nombramiento_o_ratificacion", "acuerdo_o_proposicion", "permiso",
  "mocion_procedimental", "otro", "no_claro"
)
ALLOWED_TOPIC <- c(
  "finanzas_publicas", "justicia_y_seguridad", "salud", "educacion",
  "medio_ambiente", "trabajo_y_seguridad_social",
  "gobernacion_y_elecciones", "relaciones_exteriores",
  "economia_e_industria", "infraestructura_y_transporte",
  "agricultura_y_desarrollo_rural", "derechos_humanos_e_igualdad",
  "cultura_y_deporte", "energia", "administracion_publica",
  "organizacion_y_regimen_del_congreso", "desarrollo_social_y_vivienda",
  "otro", "no_aplica", "no_claro"
)
ALLOWED_ORIGIN <- list(
  Diputados = c(
    "dictamen_de_comision", "acuerdo_institucional", "minuta_del_senado",
    "iniciativa", "asunto_directo_del_pleno", "no_claro"
  ),
  Senado = c(
    "dictamen_de_comisiones", "minuta_de_camara_de_diputados",
    "acuerdo_institucional", "iniciativa", "asunto_directo_del_pleno",
    "no_claro"
  )
)
ALLOWED_REVIEW_STATUS <- c(
  "rule_checked", "needs_review", "audited", "legacy_model_only"
)

require_columns <- function(data, required, sheet) {
  missing <- setdiff(required, names(data))
  if (length(missing) > 0) {
    stop(
      "Sheet '", sheet, "' is missing column(s): ",
      paste(missing, collapse = ", "),
      call. = FALSE
    )
  }
}

parse_review_flag <- function(x, sheet) {
  if (is.logical(x)) {
    if (anyNA(x)) stop("Blank requiere_revision value in '", sheet, "'.", call. = FALSE)
    return(as.integer(x))
  }
  normalized <- toupper(trimws(as.character(x)))
  result <- ifelse(
    normalized %in% c("TRUE", "T", "1", "SI", "SÍ"), 1L,
    ifelse(normalized %in% c("FALSE", "F", "0", "NO"), 0L, NA_integer_)
  )
  if (anyNA(result)) {
    bad <- unique(as.character(x[is.na(result)]))
    stop(
      "Invalid requiere_revision value(s) in '", sheet, "': ",
      paste(bad, collapse = ", "),
      call. = FALSE
    )
  }
  result
}

validate_values <- function(data, field, allowed, sheet) {
  values <- trimws(as.character(data[[field]]))
  bad <- unique(values[is.na(values) | !nzchar(values) | !values %in% allowed])
  if (length(bad) > 0) {
    stop(
      "Invalid ", field, " value(s) in '", sheet, "': ",
      paste(ifelse(is.na(bad), "<blank>", bad), collapse = ", "),
      call. = FALSE
    )
  }
  data[[field]] <- values
  data
}

normalize_sheet <- function(data, sheet) {
  fields <- if (sheet == "Diputados") DIPUTADOS_FIELDS else COMMON_FIELDS
  require_columns(data, c("id_votacion", fields), sheet)
  data$id_votacion <- trimws(as.character(data$id_votacion))
  if (anyNA(data$id_votacion) || any(!nzchar(data$id_votacion))) {
    stop("Blank id_votacion in '", sheet, "'.", call. = FALSE)
  }
  duplicate_ids <- unique(data$id_votacion[duplicated(data$id_votacion)])
  if (length(duplicate_ids) > 0) {
    stop(
      "Duplicate id_votacion in '", sheet, "': ",
      paste(duplicate_ids, collapse = ", "),
      call. = FALSE
    )
  }

  categorical_fields <- setdiff(
    fields, c("requiere_revision", "evidencia", "review_notes")
  )
  for (field in categorical_fields) {
    data[[field]] <- trimws(as.character(data[[field]]))
    if (anyNA(data[[field]]) || any(!nzchar(data[[field]]))) {
      stop("Blank ", field, " value in '", sheet, "'.", call. = FALSE)
    }
  }
  data$evidencia <- as.character(data$evidencia)
  if (anyNA(data$evidencia) || any(!nzchar(trimws(data$evidencia)))) {
    stop("Blank evidencia value in '", sheet, "'.", call. = FALSE)
  }
  data$requiere_revision <- parse_review_flag(data$requiere_revision, sheet)
  if (sheet == "Diputados") {
    data$review_notes <- as.character(data$review_notes)
    data$review_notes[is.na(data$review_notes)] <- ""
  }

  data <- validate_values(data, "origen", ALLOWED_ORIGIN[[sheet]], sheet)
  data <- validate_values(data, "etapa_votacion", ALLOWED_STAGE, sheet)
  data <- validate_values(data, "tipo_instrumento", ALLOWED_INSTRUMENT, sheet)
  data <- validate_values(data, "tema_politica", ALLOWED_TOPIC, sheet)
  if (sheet == "Diputados") {
    data <- validate_values(
      data, "review_status", ALLOWED_REVIEW_STATUS, sheet
    )
  }
  data[, c("id_votacion", fields), drop = FALSE]
}

read_current_classifications <- function(con, sheet) {
  if (sheet == "Diputados") {
    query <- paste0(
      "SELECT gaceta_vote_id AS id_votacion, ",
      paste(DIPUTADOS_FIELDS, collapse = ", "),
      " FROM fact_gaceta_vote_classification"
    )
  } else {
    query <- paste0(
      "SELECT CAST(votacion_id AS TEXT) AS id_votacion, ",
      paste(COMMON_FIELDS, collapse = ", "),
      " FROM fact_senado_vote_classification"
    )
  }
  current <- DBI::dbGetQuery(con, query)
  current$id_votacion <- as.character(current$id_votacion)
  current$requiere_revision <- as.integer(current$requiere_revision)
  current
}

changed_rows <- function(sheet_data, current, sheet) {
  fields <- if (sheet == "Diputados") DIPUTADOS_FIELDS else COMMON_FIELDS
  unknown <- setdiff(sheet_data$id_votacion, current$id_votacion)
  if (length(unknown) > 0) {
    stop(
      "Sheet '", sheet, "' contains ID(s) absent from the classification table: ",
      paste(head(unknown, 10), collapse = ", "),
      if (length(unknown) > 10) " ..." else "",
      call. = FALSE
    )
  }

  old <- current[match(sheet_data$id_votacion, current$id_votacion), , drop = FALSE]
  changed <- rep(FALSE, nrow(sheet_data))
  changed_fields <- character(nrow(sheet_data))
  for (i in seq_len(nrow(sheet_data))) {
    row_changes <- fields[vapply(fields, function(field) {
      !identical(as.character(old[[field]][i]), as.character(sheet_data[[field]][i]))
    }, logical(1))]
    if (sheet == "Diputados" && any(row_changes %in% COMMON_FIELDS)) {
      if (!identical(sheet_data$review_status[i], "audited")) {
        stop(
          "Manual classification change for ", sheet_data$id_votacion[i],
          " must set review_status to 'audited'.",
          call. = FALSE
        )
      }
      if (!nzchar(trimws(sheet_data$review_notes[i]))) {
        stop(
          "Manual classification change for ", sheet_data$id_votacion[i],
          " must include review_notes.",
          call. = FALSE
        )
      }
    }
    changed[i] <- length(row_changes) > 0
    changed_fields[i] <- paste(row_changes, collapse = ", ")
  }
  if (!any(changed)) return(data.frame())

  old <- old[changed, , drop = FALSE]
  new <- sheet_data[changed, , drop = FALSE]
  preview <- data.frame(
    camara = sheet,
    id_votacion = new$id_votacion,
    campos_modificados = changed_fields[changed],
    stringsAsFactors = FALSE
  )
  for (field in fields) {
    preview[[paste0(field, "_antes")]] <- old[[field]]
    preview[[paste0(field, "_despues")]] <- new[[field]]
  }
  preview
}

bind_previews <- function(...) {
  inputs <- list(...)
  inputs <- inputs[vapply(inputs, nrow, integer(1)) > 0L]
  if (length(inputs) == 0) return(data.frame())
  all_names <- unique(unlist(lapply(inputs, names), use.names = FALSE))
  inputs <- lapply(inputs, function(data) {
    missing <- setdiff(all_names, names(data))
    for (name in missing) data[[name]] <- NA
    data[, all_names, drop = FALSE]
  })
  do.call(rbind, inputs)
}

write_audit_csv <- function(preview, repo_root) {
  audit_dir <- file.path(repo_root, "data", "legislative_vote_manual_revisions")
  dir.create(audit_dir, recursive = TRUE, showWarnings = FALSE)
  timestamp <- format(Sys.time(), "%Y%m%dT%H%M%S")
  path <- file.path(audit_dir, paste0("revision_", timestamp, ".csv"))
  utils::write.csv(preview, path, row.names = FALSE, na = "")
  path
}

write_import_backup <- function(
    diputados_sheet, senado_sheet, diputados_current, senado_current,
    preview, repo_root) {
  timestamp <- format(Sys.time(), "%Y%m%dT%H%M%S")
  path <- file.path(
    repo_root, "data", "legislative_vote_review_cache", "imports",
    paste0(timestamp, "-", Sys.getpid())
  )
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
  arrow::write_parquet(
    diputados_sheet, file.path(path, "sheet_diputados.parquet")
  )
  arrow::write_parquet(senado_sheet, file.path(path, "sheet_senado.parquet"))
  arrow::write_parquet(
    diputados_current, file.path(path, "database_diputados_before.parquet")
  )
  arrow::write_parquet(
    senado_current, file.path(path, "database_senado_before.parquet")
  )
  arrow::write_parquet(preview, file.path(path, "changes_before_after.parquet"))
  path
}

apply_preview <- function(con, preview) {
  if (nrow(preview) == 0) return(invisible(NULL))
  for (sheet in c("Diputados", "Senado")) {
    rows <- preview[preview$camara == sheet, , drop = FALSE]
    if (nrow(rows) == 0) next
    fields <- if (sheet == "Diputados") DIPUTADOS_FIELDS else COMMON_FIELDS
    table <- if (sheet == "Diputados") {
      "fact_gaceta_vote_classification"
    } else {
      "fact_senado_vote_classification"
    }
    id_field <- if (sheet == "Diputados") "gaceta_vote_id" else "votacion_id"
    sql <- paste0(
      "UPDATE ", table, " SET ",
      paste0(fields, " = ?", collapse = ", "),
      " WHERE ", id_field, " = ?"
    )
    for (i in seq_len(nrow(rows))) {
      values <- lapply(fields, function(field) rows[[paste0(field, "_despues")]][i])
      values[[length(values) + 1L]] <- rows$id_votacion[i]
      affected <- DBI::dbExecute(con, sql, params = values)
      if (!identical(affected, 1L)) {
        stop("Expected exactly one updated row for ", rows$id_votacion[i], call. = FALSE)
      }
    }
  }
  invisible(NULL)
}

apply_google_sheet_revisions <- function(
    spreadsheet = SPREADSHEET,
    google_email = GOOGLE_EMAIL,
    apply = FALSE,
    write_parquet_backup = WRITE_PARQUET_BACKUP) {
  if (missing(spreadsheet) || is.null(spreadsheet) ||
      (is.character(spreadsheet) && !nzchar(trimws(spreadsheet)))) {
    stop("Provide the Google Sheet URL or ID in spreadsheet=.", call. = FALSE)
  }
  if (!is.logical(apply) || length(apply) != 1L || is.na(apply)) {
    stop("apply must be exactly TRUE or FALSE.", call. = FALSE)
  }

  googlesheets4::gs4_auth(email = google_email)
  ss <- googlesheets4::as_sheets_id(spreadsheet)
  sheets <- googlesheets4::sheet_names(ss)
  required_sheets <- c("Diputados", "Senado")
  missing_sheets <- setdiff(required_sheets, sheets)
  if (length(missing_sheets) > 0) {
    stop(
      "Spreadsheet is missing tab(s): ", paste(missing_sheets, collapse = ", "),
      call. = FALSE
    )
  }

  diputados_sheet <- normalize_sheet(
    googlesheets4::read_sheet(ss, sheet = "Diputados", .name_repair = "minimal"),
    "Diputados"
  )
  senado_sheet <- normalize_sheet(
    googlesheets4::read_sheet(ss, sheet = "Senado", .name_repair = "minimal"),
    "Senado"
  )

  repo_root <- find_repo_root()
  con <- DBI::dbConnect(RSQLite::SQLite(), file.path(repo_root, "election_data.db"))
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  diputados_current <- read_current_classifications(con, "Diputados")
  senado_current <- read_current_classifications(con, "Senado")
  preview <- bind_previews(
    changed_rows(diputados_sheet, diputados_current, "Diputados"),
    changed_rows(senado_sheet, senado_current, "Senado")
  )

  if (nrow(preview) == 0) {
    message("No classification differences found; SQLite was not changed.")
    return(invisible(preview))
  }
  if (!apply) {
    message(
      nrow(preview), " changed row(s) found. SQLite was not changed. ",
      "Review this result, then rerun with apply = TRUE."
    )
    return(preview)
  }

  backup_path <- NULL
  if (isTRUE(write_parquet_backup)) {
    if (!requireNamespace("arrow", quietly = TRUE)) {
      stop("Install arrow to write the Parquet backup: install.packages('arrow').")
    }
    backup_path <- write_import_backup(
      diputados_sheet, senado_sheet, diputados_current, senado_current,
      preview, repo_root
    )
  }
  audit_path <- write_audit_csv(preview, repo_root)
  DBI::dbWithTransaction(con, apply_preview(con, preview))
  message(nrow(preview), " row(s) updated in one SQLite transaction.")
  message("Before/after audit: ", audit_path)
  if (!is.null(backup_path)) message("Parquet backup: ", backup_path)
  message(
    "To refresh the static webpage snapshot, run: ",
    "python3 web/scripts/export_gaceta_web.py"
  )
  invisible(preview)
}

# There is deliberately no automatic call here. Sourcing this file only defines
# the functions above; applying changes always requires apply = TRUE.
