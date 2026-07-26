# Exploratory data analysis for Gaceta Parlamentaria roll-call votes.
#
# Run from the repository root in RStudio:
#   source("aux_scripts/gaceta_votes/gaceta_eda.R")
#
# Required packages:
# install.packages(c("arrow", "DBI", "dplyr", "ggplot2", "plotly", "RSQLite",
#                    "scales", "stringr", "tidyr"))

required_packages <- c(
  "arrow", "DBI", "dplyr", "ggplot2", "plotly", "RSQLite", "scales", "stringr", "tidyr"
)
missing_packages <- required_packages[!vapply(required_packages, requireNamespace,
                                              logical(1), quietly = TRUE)]
if (length(missing_packages) > 0) {
  stop("Install required packages first: ", paste(missing_packages, collapse = ", "))
}

library(arrow)
library(dplyr)
library(ggplot2)
library(scales)
library(stringr)
library(tidyr)

# Locate the project root whether this is sourced from the root or its own folder.
find_project_root <- function(path = getwd()) {
  current <- normalizePath(path, mustWork = TRUE)
  repeat {
    if (file.exists(file.path(current, "election_data.db")) &&
        dir.exists(file.path(current, "data", "materialized"))) return(current)
    parent <- dirname(current)
    if (identical(parent, current)) stop("Could not locate project root.")
    current <- parent
  }
}

root <- find_project_root()
data_dir <- file.path(root, "data", "materialized")

votes <- read_parquet(file.path(data_dir, "gaceta_vote_index.parquet")) |>
  mutate(
    vote_date = as.Date(vote_date),
    title_clean = title |>
      str_replace_all("<[^>]+>", " ") |>
      str_squish(),
    outcome = case_when(
      !is.na(status_text) & str_detect(str_to_lower(status_text), "aprob") ~ "Aprobado",
      !is.na(status_text) & str_detect(str_to_lower(status_text), "rechaz") ~ "Rechazado",
      TRUE ~ "Sin clasificar"
    ),
    margin = favor - contra,
    active_votes = favor + contra + abstencion,
    favor_share = if_else(active_votes > 0, favor / active_votes, NA_real_),
    attendance_rate = if_else(total > 0, presentes / total, NA_real_),
    month = as.Date(format(vote_date, "%Y-%m-01"))
  )

quality <- read_parquet(file.path(data_dir, "gaceta_vote_quality.parquet"))
alignment <- read_parquet(file.path(data_dir, "gaceta_deputy_alignment.parquet"))
cohesion <- read_parquet(file.path(data_dir, "gaceta_party_cohesion.parquet"))

# -----------------------------------------------------------------------------
# 1. Coverage and quality audit
# -----------------------------------------------------------------------------

coverage_by_legislature <- votes |>
  count(legislature, name = "vote_pages") |>
  left_join(
    quality |>
      group_by(legislature) |>
      summarise(
        complete_detail_votes = sum(detail_complete == 1),
        incomplete_detail_votes = sum(detail_complete != 1),
        deputy_vote_records = sum(detail_rows),
        missing_detail_rows = sum(missing_detail_rows),
        .groups = "drop"
      ),
    by = "legislature"
  ) |>
  left_join(
    votes |>
      group_by(legislature) |>
      summarise(
        first_date = min(vote_date, na.rm = TRUE),
        last_date = max(vote_date, na.rm = TRUE),
        undated_votes = sum(is.na(vote_date)),
        .groups = "drop"
      ),
    by = "legislature"
  ) |>
  mutate(detail_coverage = complete_detail_votes / vote_pages)

print(coverage_by_legislature)

p_coverage <- ggplot(coverage_by_legislature,
                     aes(x = factor(legislature), y = detail_coverage)) +
  geom_col(fill = "#1f6f8b") +
  geom_text(aes(label = percent(detail_coverage, accuracy = 1)), vjust = -0.3) +
  scale_y_continuous(labels = percent, limits = c(0, 1.1)) +
  labs(title = "Deputy-detail coverage by legislature",
       subtitle = "Only complete-detail roll calls should underpin deputy/party metrics",
       x = "Legislature", y = "Complete-detail votes / all vote pages") +
  theme_minimal(base_size = 12)

print(p_coverage)

# -----------------------------------------------------------------------------
# 2. Chamber outcomes, attendance, and voting activity
# -----------------------------------------------------------------------------

vote_summary <- votes |>
  group_by(legislature) |>
  summarise(
    votes = n(),
    approved = sum(outcome == "Aprobado"),
    rejected = sum(outcome == "Rechazado"),
    median_attendance = median(attendance_rate, na.rm = TRUE),
    median_favor_share = median(favor_share, na.rm = TRUE),
    qualified_majority = sum(mayoria_calificada_ok, na.rm = TRUE),
    .groups = "drop"
  )

p_monthly <- votes |>
  filter(!is.na(month)) |>
  count(legislature, month, name = "votes") |>
  ggplot(aes(month, votes)) +
  geom_line(color = "#1f6f8b", linewidth = 0.55) +
  facet_wrap(~ legislature, scales = "free_y") +
  labs(title = "Roll-call activity over time", x = NULL, y = "Vote pages") +
  theme_minimal(base_size = 12)

print(p_monthly)

p_outcomes <- votes |>
  count(legislature, outcome) |>
  ggplot(aes(factor(legislature), n, fill = outcome)) +
  geom_col(position = "fill") +
  scale_y_continuous(labels = percent) +
  labs(title = "Recorded outcome mix", x = "Legislature", y = "Share of vote pages", fill = NULL) +
  theme_minimal(base_size = 12)

print(p_outcomes)

p_attendance <- ggplot(votes, aes(factor(legislature), attendance_rate)) +
  geom_boxplot(fill = "#8ecae6", outlier.alpha = 0.15) +
  scale_y_continuous(labels = percent) +
  labs(title = "Attendance distribution", x = "Legislature", y = "Present members / reported total") +
  theme_minimal(base_size = 12)
print(p_attendance)

# Closest votes are a useful review queue for substantive vote-detail analysis.
closest_votes <- votes |>
  filter(!is.na(vote_date)) |>
  transmute(legislature, vote_date, title = title_clean, outcome,
            favor, contra, abstencion, total, margin, favor_share) |>
  arrange(abs(margin)) |>
  slice_head(n = 100)
print(closest_votes)

# -----------------------------------------------------------------------------
# 3. Party cohesion and deputy alignment
# These metrics already exclude incomplete deputy-detail vote pages.
# -----------------------------------------------------------------------------

p_cohesion <- cohesion |>
  ggplot(aes(factor(legislature), cohesion_mean, fill = party_key)) +
  geom_col(position = position_dodge(width = 0.8)) +
  scale_y_continuous(labels = percent, limits = c(0, 1)) +
  labs(title = "Mean party cohesion", x = "Legislature", y = "Mean within-party majority share",
       fill = "Party") +
  theme_minimal(base_size = 12) +
  theme(legend.position = "bottom")

print(p_cohesion)

p_alignment <- alignment |>
  ggplot(aes(absence_rate, alignment_rate, color = party_key)) +
  geom_point(alpha = 0.55, size = 1.5) +
  facet_wrap(~ legislature) +
  scale_x_continuous(labels = percent) +
  scale_y_continuous(labels = percent, limits = c(0, 1)) +
  labs(title = "Deputy alignment and absence", x = "Absence rate",
       y = "Alignment with party majority", color = "Party") +
  theme_minimal(base_size = 12) +
  theme(legend.position = "bottom")

print(p_alignment)

deputy_outliers <- alignment |>
  group_by(legislature) |>
  mutate(
    alignment_rank = min_rank(alignment_rate),
    absence_rank = min_rank(desc(absence_rate))
  ) |>
  ungroup() |>
  arrange(legislature, alignment_rate, desc(absence_rate))

print(deputy_outliers)

# -----------------------------------------------------------------------------
# 4. One-vote deputy grid
#
# Change this ID, or set it before sourcing with:
# Sys.setenv(GACETA_VOTE_ID = "GACETA_L66_TABLA1OR1_2")
# Every square is a named deputy, and panels split the chamber by party.
# -----------------------------------------------------------------------------

selected_vote_id <- Sys.getenv("GACETA_VOTE_ID", unset = "GACETA_L66_TABLA1OR1_2")
# Wider party blocks make the overall chamber comparison much more compact.
tile_columns <- 24
facet_columns <- 3
db <- DBI::dbConnect(RSQLite::SQLite(), file.path(root, "election_data.db"))

selected_vote <- DBI::dbGetQuery(
  db,
  "SELECT gaceta_vote_id, legislature, vote_date, title, source_url
   FROM dim_gaceta_vote
   WHERE gaceta_vote_id = ?",
  params = list(selected_vote_id)
)
if (nrow(selected_vote) == 0) {
  stop("No vote found for GACETA_VOTE_ID: ", selected_vote_id)
}

selected_vote_metrics <- votes |>
  filter(gaceta_vote_id == selected_vote_id) |>
  slice(1)
if (nrow(selected_vote_metrics) == 0) {
  stop("No materialized vote summary found for GACETA_VOTE_ID: ", selected_vote_id)
}

vote_deputies <- DBI::dbGetQuery(
  db,
  "SELECT
      f.gaceta_vote_id,
      f.deputy_id,
      f.party_key,
      f.vote_choice,
      f.ordinal,
      d.deputy_name
   FROM fact_gaceta_deputy_vote AS f
   JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
   WHERE f.gaceta_vote_id = ?
   ORDER BY f.party_key, f.ordinal, d.deputy_name",
  params = list(selected_vote_id)
) |>
  mutate(
    vote_display = recode(
      vote_choice,
      "Favor" = "Sí",
      "Contra" = "No",
      "Abstención" = "Abstención",
      "Abstencion" = "Abstención",
      "Ausente" = "Ausente",
      "Quórum *" = "Presente, sin voto",
      .default = vote_choice
    ),
    vote_display = factor(
      vote_display,
      levels = c("Sí", "No", "Abstención", "Ausente", "Presente, sin voto")
    ),
    party_display = case_when(
      party_key == "MRN" ~ "MORENA",
      party_key == "SP" ~ "Sin partido",
      TRUE ~ party_key
    )
  )

# Largest parliamentary groups first; use the full public-facing MORENA label.
party_order <- vote_deputies |>
  count(party_display, sort = TRUE) |>
  pull(party_display)

vote_deputies <- vote_deputies |>
  mutate(party_display = factor(party_display, levels = party_order)) |>
  group_by(party_display) |>
  # Group identical positions together; retain source order within each color.
  arrange(vote_display, ordinal, deputy_name, .by_group = TRUE) |>
  mutate(
    tile_number = row_number(),
    x = (tile_number - 1) %% tile_columns + 1,
    y = -((tile_number - 1) %/% tile_columns + 1),
    tooltip = paste0(
      "<b>", deputy_name, "</b>",
      "<br>Grupo parlamentario: ", party_display,
      "<br>Voto individual: ", vote_display
    )
  ) |>
  ungroup()

print(selected_vote)
print(vote_deputies)

vote_colors <- c(
  "Sí" = "#2E7D32",
  "No" = "#C62828",
  "Abstención" = "#9E9E9E",
  "Ausente" = "#111111",
  "Presente, sin voto" = "#F9A825"
)

p_vote_grid <- ggplot(vote_deputies, aes(x, y, fill = vote_display)) +
  suppressWarnings(
    geom_tile(aes(text = tooltip), width = 0.92, height = 0.92,
              color = "white", linewidth = 0.2)
  ) +
  # Fixed scales preserve square tiles across party panels.
  facet_wrap(~ party_display, ncol = facet_columns) +
  # Only show positions present in the selected roll call.
  scale_fill_manual(values = vote_colors) +
  guides(
    fill = guide_legend(
      title = NULL,
      label.position = "right",
      label.hjust = 0,
      direction = "vertical",
      nrow = 1,
      byrow = TRUE
    )
  ) +
  coord_equal() +
  labs(title = NULL) +
  theme_void(base_size = 12) +
  theme(
    plot.background = element_rect(fill = "white", color = NA),
    panel.background = element_rect(fill = "white", color = NA),
    strip.background = element_rect(fill = "white", color = NA),
    strip.text = element_text(face = "bold"),
    plot.title = element_text(face = "bold"),
    legend.position = "right",
    legend.key.width = grid::unit(0.28, "in"),
    legend.key.height = grid::unit(0.24, "in"),
    legend.text = element_text(hjust = 0),
    legend.spacing.y = grid::unit(0.12, "in")
  )

full_vote_title <- selected_vote$title[[1]] |>
  str_replace("<p>.*$", "") |>
  str_replace_all("<[^>]+>", " ") |>
  str_squish()

context_text <- paste0(
  str_wrap(full_vote_title, width = 150)
)
result_text <- paste0(
  selected_vote_metrics$favor[[1]], " a favor   ·   ",
  selected_vote_metrics$contra[[1]], " en contra   ·   ",
  selected_vote_metrics$abstencion[[1]], " abstenciones   ·   ",
  selected_vote_metrics$ausente[[1]], " ausencias"
)
quorum_text <- paste0(
  selected_vote_metrics$presentes[[1]], " presentes de ", selected_vote_metrics$total[[1]],
  "\nMínimo ", selected_vote_metrics$quorum_requerido[[1]]
)
relative_text <- paste0(
  selected_vote_metrics$favor[[1]], " a favor vs. ", selected_vote_metrics$contra[[1]], " en contra"
)
absolute_text <- paste0(
  selected_vote_metrics$favor[[1]], " a favor · mínimo ",
  selected_vote_metrics$mayoria_absoluta_requerida[[1]]
)
qualified_text <- paste0(
  selected_vote_metrics$favor[[1]], " a favor · mínimo ",
  selected_vote_metrics$mayoria_calificada_requerida[[1]]
)
status_color <- function(ok) if_else(ok, "#2E7D32", "#C62828")
header_meta <- paste0("LXVI · ", selected_vote$vote_date[[1]])

p_vote_context <- ggplot() +
  annotate("rect", xmin = 0, xmax = 1, ymin = 0, ymax = 1,
           fill = "#F3F5F7", color = NA) +
  annotate("text", x = 0.04, y = 0.89, label = "VOTACIÓN",
           hjust = 0, vjust = 1, size = 5.2, fontface = "bold", color = "#18324A") +
  annotate("text", x = 0.96, y = 0.89, label = header_meta,
           hjust = 1, vjust = 1, size = 3.7, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.04, y = 0.73, label = context_text,
           hjust = 0, vjust = 1, size = 3.55, lineheight = 1.18, color = "#1A1A1A") +
  annotate("text", x = 0.04, y = 0.47, label = "RESULTADO",
           hjust = 0, vjust = 1, size = 3.25, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.04, y = 0.35, label = result_text,
           hjust = 0, vjust = 1, size = 3.8, color = "#1A1A1A") +
  annotate("text", x = 0.04, y = 0.30, label = "QUÓRUM",
           hjust = 0, vjust = 1, size = 2.8, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.52, y = 0.30, label = "MAYORÍA SIMPLE",
           hjust = 0, vjust = 1, size = 2.8, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.04, y = 0.14, label = "MAYORÍA ABSOLUTA",
           hjust = 0, vjust = 1, size = 2.8, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.52, y = 0.14, label = "MAYORÍA CALIFICADA",
           hjust = 0, vjust = 1, size = 2.8, fontface = "bold", color = "#566573") +
  annotate("text", x = 0.04, y = 0.25, label = quorum_text,
           hjust = 0, vjust = 1, size = 2.75, lineheight = 1.1, color = "#1A1A1A") +
  annotate("text", x = 0.52, y = 0.25, label = relative_text,
           hjust = 0, vjust = 1, size = 2.75, lineheight = 1.1, color = "#1A1A1A") +
  annotate("text", x = 0.04, y = 0.09, label = absolute_text,
           hjust = 0, vjust = 1, size = 2.75, lineheight = 1.1, color = "#1A1A1A") +
  annotate("text", x = 0.52, y = 0.09, label = qualified_text,
           hjust = 0, vjust = 1, size = 2.75, lineheight = 1.1, color = "#1A1A1A") +
  annotate("point", x = 0.45, y = 0.22, shape = 16, size = 4,
           color = status_color(selected_vote_metrics$quorum_ok[[1]])) +
  annotate("point", x = 0.93, y = 0.22, shape = 16, size = 4,
           color = status_color(selected_vote_metrics$mayoria_simple_ok[[1]])) +
  annotate("point", x = 0.45, y = 0.06, shape = 16, size = 4,
           color = status_color(selected_vote_metrics$mayoria_absoluta_ok[[1]])) +
  annotate("point", x = 0.93, y = 0.06, shape = 16, size = 4,
           color = status_color(selected_vote_metrics$mayoria_calificada_ok[[1]])) +
  xlim(0, 1) + ylim(0, 1) +
  theme_void() +
  theme(
    plot.background = element_rect(fill = "white", color = NA),
    panel.background = element_rect(fill = "white", color = NA)
  )

# Two-row display: full-width vote context above, party facets below.
grid::grid.newpage()
two_row_layout <- grid::grid.layout(
  nrow = 2, ncol = 1,
  heights = grid::unit(c(1.5, 1.9), "null")
)
grid::pushViewport(grid::viewport(layout = two_row_layout))
print(
  p_vote_context,
  vp = grid::viewport(layout.pos.row = 1, layout.pos.col = 1),
  newpage = FALSE
)
print(
  p_vote_grid,
  vp = grid::viewport(layout.pos.row = 2, layout.pos.col = 1),
  newpage = FALSE
)
grid::popViewport()

# Interactive companion chart for RStudio's Viewer pane. Hover over a square
# to inspect the deputy name, group, individual vote, and source-list order.
interactive_vote_grid <- suppressWarnings(
  plotly::ggplotly(p_vote_grid, tooltip = c("text"))
) |>
  plotly::style(
    hoverinfo = "text",
    hovertemplate = "%{text}<extra></extra>"
  ) |>
  plotly::layout(hoverlabel = list(align = "left"))
print(interactive_vote_grid)

# -----------------------------------------------------------------------------
# 5. Deputy vote calendar
#
# Defaults to the first deputy in the selected roll call. To choose another:
# Sys.setenv(GACETA_DEPUTY_ID = "DEP_...")
# Sys.setenv(GACETA_DEPUTY_LEGISLATURE = "66")
# -----------------------------------------------------------------------------

selected_deputy_id <- Sys.getenv(
  "GACETA_DEPUTY_ID",
  unset = vote_deputies$deputy_id[[1]]
)
selected_deputy_legislature <- suppressWarnings(as.integer(Sys.getenv(
  "GACETA_DEPUTY_LEGISLATURE",
  unset = as.character(selected_vote$legislature[[1]])
)))
if (is.na(selected_deputy_legislature)) {
  stop("GACETA_DEPUTY_LEGISLATURE must be a legislature number, e.g. 66.")
}
calendar_columns <- 30

deputy_calendar <- DBI::dbGetQuery(
  db,
  "SELECT
      v.gaceta_vote_id,
      v.legislature,
      v.vote_date,
      v.title,
      f.party_key,
      f.vote_choice,
      d.deputy_name
   FROM fact_gaceta_deputy_vote AS f
   JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
   JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
   WHERE f.deputy_id = ? AND v.legislature = ?
   ORDER BY v.vote_date, v.gaceta_vote_id",
  params = list(selected_deputy_id, selected_deputy_legislature)
) |>
  mutate(
    vote_date = as.Date(vote_date),
    year = format(vote_date, "%Y"),
    vote_display = recode(
      vote_choice,
      "Favor" = "Sí",
      "Contra" = "No",
      "Abstención" = "Abstención",
      "Abstencion" = "Abstención",
      "Ausente" = "Ausente",
      "Quórum *" = "Presente, sin voto",
      .default = vote_choice
    ),
    vote_display = factor(
      vote_display,
      levels = c("Sí", "No", "Abstención", "Ausente", "Presente, sin voto")
    ),
    party_display = case_when(
      party_key == "MRN" ~ "MORENA",
      party_key == "SP" ~ "Sin partido",
      TRUE ~ party_key
    ),
    title_clean = title |>
      str_replace_all("<[^>]+>", " ") |>
      str_squish()
  ) |>
  filter(!is.na(vote_date)) |>
  group_by(year) |>
  mutate(
    vote_number = row_number(),
    calendar_row = (vote_number - 1) %/% calendar_columns + 1,
    calendar_column = (vote_number - 1) %% calendar_columns + 1,
    tooltip = paste0(
      "<b>", deputy_name, "</b>",
      "<br>Fecha: ", vote_date,
      "<br>Grupo parlamentario: ", party_display,
      "<br>Voto individual: ", vote_display,
      "<br><br>", str_wrap(title_clean, width = 65)
    )
  ) |>
  ungroup()

DBI::dbDisconnect(db)

if (nrow(deputy_calendar) == 0) {
  stop("No dated deputy votes found for this deputy and legislature.")
}

deputy_name <- deputy_calendar$deputy_name[[1]]
year_levels <- sort(unique(deputy_calendar$year), decreasing = TRUE)
deputy_calendar <- deputy_calendar |>
  mutate(year = factor(year, levels = year_levels))

deputy_attendance <- mean(deputy_calendar$vote_display != "Ausente")
p_deputy_calendar <- ggplot(
  deputy_calendar,
  aes(calendar_column, -calendar_row, fill = vote_display)
) +
  suppressWarnings(
    geom_tile(aes(text = tooltip), width = 0.92, height = 0.92,
              color = "white", linewidth = 0.18)
  ) +
  scale_fill_manual(values = vote_colors) +
  guides(
    fill = guide_legend(title = NULL, direction = "vertical", ncol = 1)
  ) +
  labs(
    title = paste0("Calendario de votaciones · ", deputy_name),
    subtitle = paste0(
      "Legislatura ", selected_deputy_legislature,
      " · ", nrow(deputy_calendar), " votaciones registradas",
      " · asistencia ", percent(deputy_attendance, accuracy = 0.1),
      " · cada cuadro es una votación; paneles anuales del mismo tamaño"
    ),
    x = NULL,
    y = NULL
  ) +
  # Keep a common y-scale so every year has the same calendar footprint.
  # Shorter years retain blank cells instead of collapsing into a smaller panel.
  facet_grid(rows = vars(year), switch = "y") +
  scale_x_continuous(breaks = NULL) +
  scale_y_continuous(breaks = NULL) +
  theme_minimal(base_size = 12) +
  theme(
    plot.title = element_text(face = "bold"),
    plot.subtitle = element_text(color = "#566573"),
    panel.grid = element_blank(),
    strip.placement = "outside",
    strip.background = element_blank(),
    strip.text.y.left = element_text(face = "bold", angle = 0),
    legend.position = "right",
    legend.key.width = grid::unit(0.24, "in"),
    legend.key.height = grid::unit(0.22, "in")
  )

# Keep the roll-call context with the calendar too: this is especially useful
# when the calendar is viewed or exported on its own.
grid::grid.newpage()
calendar_layout <- grid::grid.layout(
  nrow = 2, ncol = 1,
  heights = grid::unit(c(1.15, 2.4), "null")
)
grid::pushViewport(grid::viewport(layout = calendar_layout))
print(
  p_vote_context,
  vp = grid::viewport(layout.pos.row = 1, layout.pos.col = 1),
  newpage = FALSE
)
print(
  p_deputy_calendar,
  vp = grid::viewport(layout.pos.row = 2, layout.pos.col = 1),
  newpage = FALSE
)
grid::popViewport()

calendar_context_html <- paste0(
  "<b>VOTACIÓN · ", header_meta, "</b><br>",
  str_replace_all(str_wrap(full_vote_title, width = 145), "\\n", "<br>"),
  "<br><br><b>Resultado:</b> ", result_text,
  "<br><b>Quórum:</b> ", selected_vote_metrics$presentes[[1]], "/",
  selected_vote_metrics$total[[1]], " (mínimo ",
  selected_vote_metrics$quorum_requerido[[1]], ") ",
  if_else(selected_vote_metrics$quorum_ok[[1]], "✅", "❌"),
  " &nbsp; <b>Simple:</b> ",
  if_else(selected_vote_metrics$mayoria_simple_ok[[1]], "✅", "❌"),
  " &nbsp; <b>Absoluta:</b> ",
  if_else(selected_vote_metrics$mayoria_absoluta_ok[[1]], "✅", "❌"),
  " &nbsp; <b>Calificada:</b> ",
  if_else(selected_vote_metrics$mayoria_calificada_ok[[1]], "✅", "❌")
)

interactive_deputy_calendar <- suppressWarnings(
  plotly::ggplotly(p_deputy_calendar, tooltip = c("text"))
) |>
  plotly::style(
    hoverinfo = "text",
    hovertemplate = "%{text}<extra></extra>"
  ) |>
  plotly::layout(
    title = list(
      text = calendar_context_html,
      x = 0,
      xanchor = "left",
      y = 0.99,
      yanchor = "top",
      font = list(size = 13, color = "#1A1A1A")
    ),
    margin = list(t = 180, l = 65, r = 140, b = 65),
    hoverlabel = list(align = "left")
  )
print(interactive_deputy_calendar)

message("EDA complete. No files were written.")



library(DBI)
library(RSQLite)

con <- dbConnect(SQLite(), "election_data.db")

dim_gaceta_vote <- dbGetQuery(con, "SELECT * FROM dim_gaceta_vote
                 ORDER BY legislature DESC, vote_date DESC, gaceta_vote_id")

dim_gaceta_deputy <- dbGetQuery(con, "SELECT * FROM dim_gaceta_deputy
                 ORDER BY deputy_name, deputy_id")

fact_gaceta_vote_summary <- dbGetQuery(con, "SELECT * FROM fact_gaceta_vote_summary
                 ORDER BY gaceta_vote_id DESC, vote_choice, party_key")

fact_gaceta_deputy_vote <- dbGetQuery(con, "SELECT * FROM fact_gaceta_deputy_vote
                 ORDER BY gaceta_vote_id DESC, deputy_id")


