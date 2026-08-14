# Export the inventory of polling houses behind the Oraculus-compiled approval
# spreadsheets to Google Sheets, so each source can be re-located and the series
# continued directly from the pollsters now that Oraculus has gone stale.
#
# The spreadsheets carry pollster names only - no source URLs - so the exported
# sheet leaves url_fuente_actual / estado_publicacion / notas blank for you to
# fill in during re-sourcing. Everything else is derived from the data.
#
# Intended use: open this file in RStudio, edit the CONFIGURATION block, and
# click Source. googlesheets4 handles interactive browser authentication; no
# Google Cloud project key is required.
#
# Install the dependencies once, if needed:
# install.packages(c("readxl", "googlesheets4"))

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

load_approval_secrets <- function(repo_root = find_repo_root()) {
  path <- file.path(repo_root, "config", "approval_rates.env")
  if (!file.exists(path)) {
    stop(
      "Missing ", path, ". Copy config/approval_rates.env.example ",
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

env_flag <- function(name, default) {
  value <- env_or_null(name)
  if (is.null(value)) return(default)
  normalized <- tolower(value)
  if (normalized %in% c("true", "t", "yes", "y", "1")) return(TRUE)
  if (normalized %in% c("false", "f", "no", "n", "0")) return(FALSE)
  stop(
    name, " must be true or false, not \"", value, "\".",
    call. = FALSE
  )
}

env_date <- function(name, default) {
  value <- env_or_null(name)
  if (is.null(value)) return(default)
  parsed <- as.Date(value, format = "%Y-%m-%d")
  if (is.na(parsed)) {
    stop(
      name, " must be a YYYY-MM-DD date, not \"", value, "\".",
      call. = FALSE
    )
  }
  parsed
}

load_approval_secrets()

# ---- CONFIGURATION ---------------------------------------------------------
#
# Every value below is read from the gitignored config/approval_rates.env.
# Edit that file, not this one; the defaults here apply when a key is absent.

# Set APPROVAL_RATES_CREATE_NEW=true to build a brand-new spreadsheet from
# scratch. It takes precedence over APPROVAL_RATES_SHEET_ID, so a stale ID left
# in the env file cannot cause a new export to overwrite an existing sheet. The
# new spreadsheet's ID is printed at the end: paste it into
# APPROVAL_RATES_SHEET_ID and flip this back to false to keep updating it.
CREATE_NEW <- env_flag("APPROVAL_RATES_CREATE_NEW", FALSE)

# Existing spreadsheet to update, as an ID or a URL. Ignored when CREATE_NEW is
# true. An empty value also creates a new spreadsheet.
SPREADSHEET <- env_or_null("APPROVAL_RATES_SHEET_ID")

# Title for a newly created spreadsheet. An empty value uses a dated default.
# Ignored when updating an existing sheet, whose title is left alone.
SHEET_TITLE <- env_or_null("APPROVAL_RATES_SHEET_TITLE")

# Optional Google account hint. An empty value lets you choose during auth.
GOOGLE_EMAIL <- env_or_null("APPROVAL_RATES_GOOGLE_EMAIL")

# A house is flagged activa_al_cierre when it published on or after this date.
# These are the houses worth contacting first to continue the series.
ACTIVE_SINCE <- env_date("APPROVAL_RATES_ACTIVE_SINCE", as.Date("2025-01-01"))

# Save the exact extract locally before uploading it. Snapshots are timestamped
# and gitignored under data/approval_rates_cache/exports/.
WRITE_CSV_CACHE <- env_flag("APPROVAL_RATES_WRITE_CSV_CACHE", TRUE)

# ---------------------------------------------------------------------------

required_packages <- c("readxl", "googlesheets4")
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

DATA_DIR <- function(repo_root) {
  file.path(repo_root, "aux_scripts", "approval_rates")
}

PRESIDENT_ORDER <- c("EZPL", "VFQ", "FCH", "EPN", "AMLO", "Sheinbaum")

PRESIDENT_LABELS <- c(
  EZPL = "Ernesto Zedillo", VFQ = "Vicente Fox", FCH = "Felipe Calderón",
  EPN = "Enrique Peña Nieto", AMLO = "Andrés Manuel López Obrador", Sheinbaum = "Claudia Sheinbaum"
)

# Sheinbaum took office on 1 October 2024. Compared against a parsed date, not
# against the raw "Oct 2024" label, whose lexicographic ordering is meaningless.
SHEINBAUM_START <- as.Date("2024-10-01")

# Houses the raw names split apart but that are the same firm. Kept as a
# suggestion in its own column rather than merged, because collapsing them
# changes the house-effect estimates and is an editorial call.
SUGGESTED_FAMILY <- c(
  "BGC"                = "BGC",
  "BGC Telefonica"     = "BGC",
  "BGC Vivienda"       = "BGC",
  "Ipsos"              = "Ipsos",
  "Ipsos Bimsa"        = "Ipsos",
  "Buendia y Laredo"   = "Buendía (Laredo / Márquez)",
  "Buendia y Marquez"  = "Buendía (Laredo / Márquez)"
)

# Newspapers commissioning their own polls, and the Zedillo-era presidency's
# in-house polling, which is not an independent commercial pollster at all.
MEDIA_HOUSES <- c("Reforma", "El Universal", "El Financiero")
GOVERNMENT_HOUSES <- c("Presidencia")

read_approval_files <- function(repo_root) {
  dir <- DATA_DIR(repo_root)
  archive_path <- file.path(dir, "table-aprobacion_archivo.xlsx")
  recent_path <- file.path(dir, "table-aprobacion.xlsx")
  for (path in c(archive_path, recent_path)) {
    if (!file.exists(path)) {
      stop("Missing source spreadsheet: ", path, call. = FALSE)
    }
  }

  archive <- readxl::read_xlsx(archive_path)
  archive$source_file <- "table-aprobacion_archivo.xlsx"

  recent <- readxl::read_xlsx(recent_path)
  recent$source_file <- "table-aprobacion.xlsx"
  # The recent file has no Presidente column; it is derived below from the date.
  recent$Presidente <- NA_character_

  columns <- c("Presidente", "Mes", "Encuestadora", "Aprueba", "source_file")
  combined <- rbind(
    as.data.frame(archive)[, columns, drop = FALSE],
    as.data.frame(recent)[, columns, drop = FALSE]
  )

  # Month labels are English abbreviations ("Sep 2025"), so parse under the C
  # locale rather than a Spanish one that has no match for Jan/Apr/Aug/Dec.
  combined$fecha <- as.Date(
    paste("01", combined$Mes), format = "%d %b %Y"
  )
  if (anyNA(combined$fecha)) {
    stop(
      "Unparsed month labels: ",
      paste(unique(combined$Mes[is.na(combined$fecha)]), collapse = ", "),
      call. = FALSE
    )
  }

  combined$Presidente[is.na(combined$Presidente)] <- ifelse(
    combined$fecha[is.na(combined$Presidente)] >= SHEINBAUM_START,
    "Sheinbaum", "AMLO"
  )
  combined
}

detect_method <- function(raw_name) {
  ifelse(grepl("telef|/tel", raw_name, ignore.case = TRUE), "Telefónica",
  ifelse(grepl("vivien|/viv", raw_name, ignore.case = TRUE), "Vivienda",
  ifelse(grepl("online|web|internet", raw_name, ignore.case = TRUE), "Online",
         "No especificado")))
}

clean_house <- function(raw_name) {
  trimws(gsub("/tel|/viv|/online", "", raw_name, ignore.case = TRUE))
}

classify_type <- function(house) {
  ifelse(house %in% GOVERNMENT_HOUSES, "Gobierno",
  ifelse(house %in% MEDIA_HOUSES, "Medio", "Casa encuestadora"))
}

collapse_unique <- function(x, sep = " · ") {
  paste(unique(x[!is.na(x)]), collapse = sep)
}

make_variants <- function(polls) {
  parts <- split(polls, polls$Encuestadora)
  result <- do.call(rbind, lapply(parts, function(part) {
    data.frame(
      nombre_crudo     = part$Encuestadora[1],
      encuestadora     = clean_house(part$Encuestadora[1]),
      metodo_detectado = detect_method(part$Encuestadora[1]),
      n_encuestas      = nrow(part),
      primera          = format(min(part$fecha), "%Y-%m"),
      ultima           = format(max(part$fecha), "%Y-%m"),
      stringsAsFactors = FALSE
    )
  }))
  rownames(result) <- NULL
  result[order(result$encuestadora, result$nombre_crudo), ]
}

make_inventory <- function(polls, active_since) {
  polls$encuestadora <- clean_house(polls$Encuestadora)
  polls$metodo <- detect_method(polls$Encuestadora)
  polls$Presidente <- factor(polls$Presidente, levels = PRESIDENT_ORDER)

  parts <- split(polls, polls$encuestadora)
  result <- do.call(rbind, lapply(parts, function(part) {
    house <- part$encuestadora[1]
    presidents <- levels(droplevels(part$Presidente))
    first_date <- min(part$fecha)
    last_date <- max(part$fecha)
    data.frame(
      encuestadora        = house,
      familia_sugerida    = unname(
        if (house %in% names(SUGGESTED_FAMILY)) SUGGESTED_FAMILY[[house]] else house
      ),
      tipo                = classify_type(house),
      n_encuestas         = nrow(part),
      primera_encuesta    = format(first_date, "%Y-%m"),
      ultima_encuesta     = format(last_date, "%Y-%m"),
      anios_cobertura     = round(
        as.numeric(difftime(last_date, first_date, units = "days")) / 365.25, 1
      ),
      presidentes         = collapse_unique(unname(PRESIDENT_LABELS[presidents])),
      metodos             = collapse_unique(part$metodo),
      variantes_nombre    = collapse_unique(part$Encuestadora),
      archivos_origen     = collapse_unique(part$source_file),
      activa_al_cierre    = last_date >= active_since,
      # Filled in by hand while re-sourcing; intentionally blank on export.
      url_fuente_actual   = "",
      estado_publicacion  = "",
      notas               = "",
      stringsAsFactors    = FALSE
    )
  }))
  rownames(result) <- NULL
  result[order(-result$n_encuestas, result$encuestadora), ]
}

make_coverage <- function(polls) {
  polls$encuestadora <- clean_house(polls$Encuestadora)
  polls$Presidente <- factor(polls$Presidente, levels = PRESIDENT_ORDER)
  parts <- split(polls, polls$Presidente, drop = TRUE)
  result <- do.call(rbind, lapply(parts, function(part) {
    data.frame(
      presidente      = unname(
        PRESIDENT_LABELS[as.character(part$Presidente[1])]
      ),
      n_encuestas     = nrow(part),
      n_encuestadoras = length(unique(part$encuestadora)),
      primer_mes      = format(min(part$fecha), "%Y-%m"),
      ultimo_mes      = format(max(part$fecha), "%Y-%m"),
      stringsAsFactors = FALSE
    )
  }))
  rownames(result) <- NULL
  result
}

write_tab <- function(ss, sheet, data) {
  # sheet_write() creates the tab when absent and replaces its contents when it
  # exists, making reruns deterministic without deleting the spreadsheet. It
  # also formats and freezes the header row by default.
  googlesheets4::sheet_write(data, ss = ss, sheet = sheet)
}

write_export_cache <- function(inventory, variants, coverage, repo_root) {
  timestamp <- format(Sys.time(), "%Y%m%dT%H%M%S")
  path <- file.path(
    repo_root, "data", "approval_rates_cache", "exports",
    paste0(timestamp, "-", Sys.getpid())
  )
  dir.create(path, recursive = TRUE, showWarnings = FALSE)
  write.csv(inventory, file.path(path, "encuestadoras.csv"), row.names = FALSE)
  write.csv(variants, file.path(path, "variantes.csv"), row.names = FALSE)
  write.csv(coverage, file.path(path, "cobertura.csv"), row.names = FALSE)
  path
}

export_approval_sources <- function(
    spreadsheet = SPREADSHEET,
    google_email = GOOGLE_EMAIL,
    active_since = ACTIVE_SINCE,
    write_csv_cache = WRITE_CSV_CACHE,
    create_new = CREATE_NEW,
    sheet_title = SHEET_TITLE) {
  repo_root <- find_repo_root()

  polls <- read_approval_files(repo_root)
  named <- !is.na(polls$Encuestadora) & nzchar(trimws(polls$Encuestadora))
  polls <- polls[named, , drop = FALSE]
  if (nrow(polls) == 0) {
    stop("No polls found in the source spreadsheets.", call. = FALSE)
  }

  inventory <- make_inventory(polls, active_since)
  variants <- make_variants(polls)
  coverage <- make_coverage(polls)

  cache_path <- NULL
  if (isTRUE(write_csv_cache)) {
    cache_path <- write_export_cache(inventory, variants, coverage, repo_root)
    message("Local CSV snapshot: ", cache_path)
  }

  googlesheets4::gs4_auth(email = google_email)
  build_from_scratch <- isTRUE(create_new) || is.null(spreadsheet) ||
    (is.character(spreadsheet) && length(spreadsheet) == 1L &&
       !nzchar(trimws(spreadsheet)))
  if (build_from_scratch) {
    if (is.null(sheet_title)) {
      sheet_title <- paste0("Fuentes de aprobación presidencial - ", Sys.Date())
    }
    spreadsheet <- googlesheets4::gs4_create(
      sheet_title,
      sheets = list(Encuestadoras = inventory)
    )
  } else {
    spreadsheet <- googlesheets4::as_sheets_id(spreadsheet)
    write_tab(spreadsheet, "Encuestadoras", inventory)
  }
  write_tab(spreadsheet, "Variantes", variants)
  write_tab(spreadsheet, "Cobertura", coverage)

  spreadsheet_id <- googlesheets4::as_sheets_id(spreadsheet)
  spreadsheet_url <- paste0(
    "https://docs.google.com/spreadsheets/d/", as.character(spreadsheet_id)
  )
  message(
    "Export complete: ", nrow(inventory), " encuestadoras across ",
    nrow(variants), " name variants and ", nrow(polls), " polls."
  )
  message(
    "Still publishing since ", format(active_since, "%Y-%m"), ": ",
    paste(inventory$encuestadora[inventory$activa_al_cierre], collapse = ", ")
  )
  message("Google Sheet: ", spreadsheet_url)
  if (build_from_scratch) {
    message(
      "Created from scratch. To keep updating this same spreadsheet, set ",
      "APPROVAL_RATES_SHEET_ID=", as.character(spreadsheet_id),
      " and APPROVAL_RATES_CREATE_NEW=false in config/approval_rates.env."
    )
  }
  invisible(list(
    spreadsheet = spreadsheet_id,
    url = spreadsheet_url,
    cache_path = cache_path,
    inventory = inventory,
    variants = variants,
    coverage = coverage
  ))
}

# Clicking Source in RStudio runs the export. When this file is sourced from a
# non-interactive pipeline, call export_approval_sources() explicitly instead.
if (interactive()) {
  export_result <- export_approval_sources()
}
