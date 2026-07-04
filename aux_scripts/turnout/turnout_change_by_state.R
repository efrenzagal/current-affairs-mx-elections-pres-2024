# ─────────────────────────────────────────────────────────────────────────────
# Turnout change by state: lista nominal, voters, participation
# Presidential elections 2018 vs 2024
# Outputs:
#   1. p_scatter_turnout
#   2. p_absolute_facets
#   3. p_participation_bars
# ─────────────────────────────────────────────────────────────────────────────

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)
library(ggplot2)
library(scales)
library(stringi)
library(showtext)
library(ggrepel)

DB_PATH <- "election_data.db"
TOP_N_STATES <- 32

font_add_google("IBM Plex Sans", "ibm_sans")
font_add_google("IBM Plex Mono", "ibm_mono")
showtext_auto()

col_bg   <- "#F7F7F5"
col_grid <- "#E2E2DE"
col_text <- "#1A1A1A"
col_sub  <- "#888888"
col_gain <- "#4CAF50"
col_loss <- "#C84B31"
col_ln   <- "#2F6DB3"
col_vote <- "#2E8B57"

normalize_state <- function(x) {
  x |>
    stringi::stri_trans_general("Latin-ASCII") |>
    trimws() |>
    toupper()
}

title_state <- function(x) {
  x |>
    tolower() |>
    stringi::stri_trans_totitle()
}

theme_election <- function(legend_position = "top") {
  theme_minimal(base_size = 10) +
    theme(
      plot.background  = element_rect(fill = col_bg, colour = NA),
      panel.background = element_rect(fill = col_bg, colour = NA),
      panel.grid.major = element_line(colour = col_grid, linewidth = 0.4),
      panel.grid.minor = element_blank(),
      axis.text.x = element_text(family = "ibm_mono", size = 7, colour = col_sub),
      axis.text.y = element_text(family = "ibm_sans", size = 7, colour = col_text),
      axis.title = element_text(family = "ibm_sans", size = 8, colour = col_sub),
      axis.ticks = element_blank(),
      plot.title = element_text(
        family = "ibm_mono", face = "bold",
        size = 13, colour = col_text, margin = margin(b = 4)
      ),
      plot.subtitle = element_text(
        family = "ibm_sans", size = 8,
        colour = col_sub, margin = margin(b = 12)
      ),
      plot.caption = element_text(
        family = "ibm_mono", size = 7,
        colour = col_sub, margin = margin(t = 8)
      ),
      strip.background = element_rect(fill = "#EAEAEA", colour = NA),
      strip.text = element_text(
        family = "ibm_mono", face = "bold",
        size = 9, colour = col_text,
        margin = margin(5, 0, 5, 0)
      ),
      legend.position = legend_position,
      legend.justification = "left",
      legend.text = element_text(family = "ibm_mono", size = 8, colour = col_text),
      legend.title = element_text(family = "ibm_mono", size = 8, colour = col_sub),
      legend.background = element_rect(fill = col_bg, colour = NA),
      plot.margin = margin(16, 16, 12, 16)
    )
}

# ── Load data ────────────────────────────────────────────────────────────────

conn <- dbConnect(SQLite(), DB_PATH)

lista_state <- dbGetQuery(conn, "
  SELECT
    c.election_id,
    g.nombre_estado,
    SUM(CAST(c.lista_nominal AS REAL)) AS lista_nominal
  FROM dim_casilla c
  JOIN dim_geography g
    ON c.geo_id = g.geo_id
  WHERE c.election_id IN ('PRE_2018', 'PRE_2024')
    AND c.tipo_casilla != 'S'
    AND g.seccion != 0
  GROUP BY
    c.election_id,
    g.nombre_estado
")

votes_state <- dbGetQuery(conn, "
  WITH casilla_totals AS (
    SELECT
      f.election_id,
      f.casilla_id,
      c.geo_id,
      MAX(CAST(f.total_votos AS REAL)) AS total_votes
    FROM fact_casilla_vote f
    JOIN dim_casilla c
      ON  f.casilla_id  = c.casilla_id
      AND f.election_id = c.election_id
    JOIN dim_geography g
      ON c.geo_id = g.geo_id
    WHERE f.election_id IN ('PRE_2018', 'PRE_2024')
      AND c.tipo_casilla != 'S'
      AND g.seccion != 0
    GROUP BY
      f.election_id,
      f.casilla_id,
      c.geo_id
  )
  SELECT
    ct.election_id,
    g.nombre_estado,
    SUM(ct.total_votes) AS total_votes
  FROM casilla_totals ct
  JOIN dim_geography g
    ON ct.geo_id = g.geo_id
  GROUP BY
    ct.election_id,
    g.nombre_estado
")

dbDisconnect(conn)

# ── Prepare data ─────────────────────────────────────────────────────────────

state_turnout <- lista_state |>
  inner_join(votes_state, by = c("election_id", "nombre_estado")) |>
  mutate(
    estado = normalize_state(nombre_estado),
    year = sub("PRE_", "", election_id),
    lista_nominal = as.numeric(lista_nominal),
    total_votes = as.numeric(total_votes)
  ) |>
  group_by(estado, year) |>
  summarise(
    lista_nominal = sum(lista_nominal, na.rm = TRUE),
    total_votes = sum(total_votes, na.rm = TRUE),
    .groups = "drop"
  ) |>
  mutate(
    participation = total_votes / lista_nominal
  )

print(
  state_turnout |>
    group_by(year) |>
    summarise(
      lista_nominal = sum(lista_nominal, na.rm = TRUE),
      total_votes = sum(total_votes, na.rm = TRUE),
      participation = total_votes / lista_nominal,
      .groups = "drop"
    )
)

top_states <- state_turnout |>
  group_by(estado) |>
  summarise(avg_lista = mean(lista_nominal, na.rm = TRUE), .groups = "drop") |>
  arrange(desc(avg_lista)) |>
  slice_head(n = if (!is.null(TOP_N_STATES)) TOP_N_STATES else n()) |>
  pull(estado)

state_turnout <- state_turnout |>
  filter(estado %in% top_states)

change_state <- state_turnout |>
  pivot_wider(
    id_cols = estado,
    names_from = year,
    values_from = c(lista_nominal, total_votes, participation)
  ) |>
  mutate(
    lista_growth_pct = (lista_nominal_2024 / lista_nominal_2018 - 1) * 100,
    votes_growth_pct = (total_votes_2024 / total_votes_2018 - 1) * 100,
    participation_change_pp = (participation_2024 - participation_2018) * 100,
    participation_2018_pct = participation_2018 * 100,
    participation_2024_pct = participation_2024 * 100,
    estado_lbl = title_state(estado),
    turnout_grew = participation_change_pp >= 0
  ) |>
  filter(
    !is.na(lista_growth_pct),
    !is.na(votes_growth_pct),
    !is.na(participation_change_pp)
  )

# ─────────────────────────────────────────────────────────────────────────────
# 1. Scatter: lista nominal growth vs voters growth
# ─────────────────────────────────────────────────────────────────────────────

p_scatter_turnout <- ggplot(
  change_state,
  aes(
    x = lista_growth_pct,
    y = votes_growth_pct,
    color = participation_change_pp,
    size = total_votes_2024
  )
) +
  geom_abline(
    slope = 1,
    intercept = 0,
    linetype = "dashed",
    linewidth = 0.5,
    color = col_grid
  ) +
  geom_point(alpha = 0.85) +
  geom_text_repel(
    aes(label = estado_lbl),
    family = "ibm_sans",
    size = 2.6,
    max.overlaps = 14,
    segment.size = 0.25,
    segment.alpha = 0.35,
    show.legend = FALSE
  ) +
  scale_x_continuous(
    labels = label_percent(scale = 1, accuracy = 1),
    expand = expansion(mult = c(0.05, 0.12))
  ) +
  scale_y_continuous(
    labels = label_percent(scale = 1, accuracy = 1),
    expand = expansion(mult = c(0.08, 0.12))
  ) +
  scale_color_gradient2(
    low = col_loss,
    mid = col_sub,
    high = col_gain,
    midpoint = 0,
    labels = label_number(suffix = " pp", accuracy = 0.1),
    name = "Cambio en participación"
  ) +
  scale_size_continuous(
    range = c(2.5, 10),
    labels = label_number(scale = 1e-6, suffix = "M", accuracy = 0.1),
    name = "Votos 2024"
  ) +
  labs(
    title = "¿Dónde creció el electorado y dónde creció la participación?",
    subtitle = paste0(
      "Presidencial 2018–2024 · X = crecimiento de lista nominal · Y = crecimiento de votantes\n",
      "La diagonal marca crecimiento igual: arriba sube la participación, abajo cae"
    ),
    x = "Cambio en lista nominal (%)",
    y = "Cambio en votantes (%)",
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  theme_election("right")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Facet: absolute movement in lista nominal and voters
# ─────────────────────────────────────────────────────────────────────────────

movement_data <- change_state |>
  select(
    estado_lbl,
    lista_nominal_2018,
    lista_nominal_2024,
    total_votes_2018,
    total_votes_2024
  ) |>
  pivot_longer(
    cols = c(
      lista_nominal_2018,
      lista_nominal_2024,
      total_votes_2018,
      total_votes_2024
    ),
    names_to = c("metric", "year"),
    names_pattern = "(lista_nominal|total_votes)_(2018|2024)",
    values_to = "value"
  ) |>
  mutate(
    metric = recode(
      metric,
      lista_nominal = "Lista nominal",
      total_votes = "Votantes"
    ),
    year = factor(year, levels = c("2018", "2024"))
  )

movement_segments <- movement_data |>
  pivot_wider(
    id_cols = c(estado_lbl, metric),
    names_from = year,
    values_from = value
  )

state_order_abs <- movement_segments |>
  filter(metric == "Lista nominal") |>
  arrange(`2024`) |>
  pull(estado_lbl)

movement_data <- movement_data |>
  mutate(
    estado_lbl = factor(estado_lbl, levels = state_order_abs),
    metric = factor(metric, levels = c("Lista nominal", "Votantes"))
  )

movement_segments <- movement_segments |>
  mutate(
    estado_lbl = factor(estado_lbl, levels = state_order_abs),
    metric = factor(metric, levels = c("Lista nominal", "Votantes")),
    label_x = `2024` + max(`2024`, na.rm = TRUE) * 0.025
  )

p_absolute_facets <- ggplot() +
  geom_segment(
    data = movement_segments,
    aes(
      x = `2018`,
      xend = `2024`,
      y = estado_lbl,
      yend = estado_lbl,
      color = metric
    ),
    linewidth = 0.5,
    arrow = arrow(length = unit(0.10, "cm"), type = "closed")
  ) +
  geom_point(
    data = movement_data |> filter(year == "2018"),
    aes(x = value, y = estado_lbl, color = metric),
    shape = 21,
    fill = col_bg,
    size = 1.6,
    stroke = 0.65
  ) +
  geom_point(
    data = movement_data |> filter(year == "2024"),
    aes(x = value, y = estado_lbl, color = metric),
    shape = 16,
    size = 1.5
  ) +
  geom_text(
    data = movement_segments,
    aes(
      x = label_x,
      y = estado_lbl,
      label = label_number(scale = 1e-6, suffix = "M", accuracy = 0.1)(`2024`),
      color = metric
    ),
    family = "ibm_mono",
    size = 2.1,
    hjust = 0
  ) +
  facet_wrap(
    ~ metric,
    nrow = 1,
    scales = "fixed"
  ) +
  scale_x_continuous(
    labels = label_number(scale = 1e-6, suffix = "M", accuracy = 1),
    expand = expansion(mult = c(0.02, 0.22))
  ) +
  scale_color_manual(
    values = c(
      "Lista nominal" = col_ln,
      "Votantes" = col_vote
    ),
    guide = "none"
  ) +
  labs(
    title = "La lista nominal creció más que los votantes en la mayoría de los estados",
    subtitle = paste0(
      "Presidencial 2018–2024 · Movimiento absoluto por estado\n",
      "Círculo vacío = 2018 · Punto lleno = 2024 · Flecha muestra el cambio absoluto"
    ),
    x = "Millones de personas",
    y = NULL,
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  theme_election("none") +
  theme(
    panel.spacing.x = unit(1.2, "lines")
  )

# ─────────────────────────────────────────────────────────────────────────────
# 3. Bars: participation change
# ─────────────────────────────────────────────────────────────────────────────

p_participation_bars <- change_state |>
  mutate(
    estado_lbl = factor(
      estado_lbl,
      levels = change_state |>
        arrange(participation_change_pp) |>
        pull(estado_lbl)
    ),
    label_text = paste0(
      if_else(participation_change_pp >= 0, "+", ""),
      number(participation_change_pp, accuracy = 0.1),
      " pp"
    )
  ) |>
  ggplot(aes(
    x = participation_change_pp,
    y = estado_lbl,
    fill = turnout_grew
  )) +
  geom_col(width = 0.7, alpha = 0.9) +
  geom_vline(xintercept = 0, linewidth = 0.5, color = col_grid) +
  geom_text(
    aes(
      label = label_text,
      hjust = if_else(participation_change_pp >= 0, -0.1, 1.1)
    ),
    family = "ibm_mono",
    size = 2.5,
    color = col_text
  ) +
  scale_x_continuous(
    labels = label_number(suffix = " pp", accuracy = 1),
    expand = expansion(mult = c(0.14, 0.14))
  ) +
  scale_fill_manual(
    values = c("TRUE" = col_gain, "FALSE" = col_loss),
    guide = "none"
  ) +
  labs(
    title = "Cambio en participación electoral por estado",
    subtitle = paste0(
      "Participación = votantes / lista nominal · Presidencial 2018 vs 2024",
      if (!is.null(TOP_N_STATES)) paste0(" · Top ", TOP_N_STATES, " estados por lista nominal") else ""
    ),
    x = "Cambio en participación, puntos porcentuales",
    y = NULL,
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  theme_election("none")

# ── Print plots ──────────────────────────────────────────────────────────────

p_scatter_turnout %>% print()
p_absolute_facets %>% print()
p_participation_bars %>% print()

# ── Optional saves ───────────────────────────────────────────────────────────

# ggsave("turnout_01_scatter_lista_vs_voters.png", p_scatter_turnout, width = 11, height = 8, dpi = 300)
# ggsave("turnout_02_absolute_facets.png", p_absolute_facets, width = 13, height = 8, dpi = 300)
# ggsave("turnout_03_participation_bars.png", p_participation_bars, width = 9, height = 10, dpi = 300)