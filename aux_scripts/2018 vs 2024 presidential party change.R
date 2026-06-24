# ─────────────────────────────────────────────────────────────────────────────
# 2018 vs 2024 presidential party change plots
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

# ── Config ──────────────────────────────────────────────────────────────────

TOP_N_STATES <- 10
DB_PATH <- "election_data.db"

parties_of_interest <- c("MORENA", "PAN", "PRI", "PRD", "MC", "PT")
party_order_fixed <- c("MORENA", "PAN", "PRI", "MC", "PT", "PRD")

party_colors <- c(
  "MORENA" = "#8B0000",
  "PAN"    = "#1E90FF",
  "PRI"    = "#006847",
  "PRD"    = "#FFD700",
  "MC"     = "#FF8C00",
  "PT"     = "#CC0000"
)

party_labels <- c(
  "MORENA" = "Morena",
  "PAN"    = "PAN",
  "PRI"    = "PRI",
  "PRD"    = "PRD",
  "MC"     = "MC",
  "PT"     = "PT"
)

col_gain <- "#4CAF50"
col_loss <- "#C84B31"
col_bg   <- "#F7F7F5"
col_grid <- "#E2E2DE"
col_text <- "#1A1A1A"
col_sub  <- "#888888"

font_add_google("IBM Plex Sans", "ibm_sans")
font_add_google("IBM Plex Mono", "ibm_mono")
showtext_auto()

# ── Helpers ─────────────────────────────────────────────────────────────────

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

base_theme <- function(legend_position = "top") {
  theme_minimal(base_size = 10) +
    theme(
      plot.background  = element_rect(fill = col_bg, colour = NA),
      panel.background = element_rect(fill = col_bg, colour = NA),
      strip.background = element_rect(fill = "#EAEAEA", colour = NA),
      
      panel.grid.major = element_line(colour = col_grid, linewidth = 0.4),
      panel.grid.minor = element_blank(),
      
      axis.text.x = element_text(
        family = "ibm_mono", size = 7,
        colour = col_sub, margin = margin(t = 3)
      ),
      axis.text.y = element_text(
        family = "ibm_sans", size = 7,
        colour = col_text
      ),
      axis.title = element_text(
        family = "ibm_sans", size = 8,
        colour = col_sub
      ),
      axis.ticks = element_blank(),
      
      strip.text = element_text(
        family = "ibm_mono", face = "bold",
        size = 9, colour = col_text,
        margin = margin(4, 0, 4, 0)
      ),
      
      plot.title = element_text(
        family = "ibm_mono", face = "bold",
        size = 12, colour = col_text,
        margin = margin(b = 4)
      ),
      plot.subtitle = element_text(
        family = "ibm_sans", size = 8,
        colour = col_sub,
        margin = margin(b = 12)
      ),
      plot.caption = element_text(
        family = "ibm_mono", size = 7,
        colour = col_sub,
        margin = margin(t = 8)
      ),
      plot.margin = margin(16, 16, 12, 16),
      
      legend.position      = legend_position,
      legend.justification = "left",
      legend.text          = element_text(family = "ibm_mono", size = 8, colour = col_text),
      legend.title         = element_text(family = "ibm_mono", size = 8, colour = col_sub),
      legend.key.width     = unit(1.2, "cm"),
      legend.background    = element_rect(fill = col_bg, colour = NA)
    )
}

# ── Load data ────────────────────────────────────────────────────────────────

conn <- dbConnect(SQLite(), DB_PATH)

party_votes <- dbGetQuery(conn, "
  SELECT
    f.election_id,
    f.party_key,
    f.votes,
    g.nombre_estado
  FROM fact_casilla_vote f
  JOIN dim_casilla c
    ON  f.casilla_id  = c.casilla_id
    AND f.election_id = c.election_id
  JOIN dim_geography g
    ON c.geo_id = g.geo_id
  WHERE f.election_id IN ('PRE_2018', 'PRE_2024')
    AND c.tipo_casilla != 'S'
    AND g.seccion       != 0
")

dim_party <- dbGetQuery(conn, "
  SELECT party_key, is_coalition, members
  FROM dim_party
  WHERE is_coalition = 1
")

dbDisconnect(conn)

# ── Clean / normalize ───────────────────────────────────────────────────────

party_votes <- party_votes |>
  mutate(
    party_key = if_else(party_key == "MOVIMIENTO CIUDADANO", "MC", party_key),
    estado    = normalize_state(nombre_estado)
  )

coalition_members <- dim_party |>
  mutate(member = strsplit(members, ",")) |>
  unnest(member) |>
  mutate(
    member = trimws(member),
    member = case_when(
      member == "MOVIMIENTO CIUDADANO" ~ "MC",
      TRUE ~ member
    )
  ) |>
  select(coalition_key = party_key, member_key = member) |>
  distinct()

# ── Shared aggregation ──────────────────────────────────────────────────────

state_raw <- party_votes |>
  group_by(election_id, estado, party_key) |>
  summarise(votes = sum(votes, na.rm = TRUE), .groups = "drop")

state_totals <- state_raw |>
  group_by(election_id, estado) |>
  summarise(state_total = sum(votes, na.rm = TRUE), .groups = "drop")

top_states <- state_totals |>
  group_by(estado) |>
  summarise(avg_total = mean(state_total, na.rm = TRUE), .groups = "drop") |>
  arrange(desc(avg_total)) |>
  slice_head(n = if (!is.null(TOP_N_STATES)) TOP_N_STATES else n()) |>
  pull(estado)

state_raw <- state_raw |>
  filter(estado %in% top_states)

state_totals <- state_totals |>
  filter(estado %in% top_states)

individual_weights <- state_raw |>
  filter(party_key %in% parties_of_interest) |>
  rename(indiv_votes = votes)

coalition_state_votes <- state_raw |>
  filter(party_key %in% dim_party$party_key)

attributed <- coalition_state_votes |>
  rename(coalition_key = party_key, coalition_votes = votes) |>
  inner_join(
    coalition_members,
    by = "coalition_key",
    relationship = "many-to-many"
  ) |>
  filter(member_key %in% parties_of_interest) |>
  left_join(
    individual_weights |>
      rename(member_key = party_key, member_indiv = indiv_votes),
    by = c("election_id", "estado", "member_key")
  ) |>
  group_by(election_id, estado, coalition_key) |>
  mutate(
    total_member_indiv = sum(member_indiv, na.rm = TRUE),
    weight = if_else(
      total_member_indiv > 0,
      member_indiv / total_member_indiv,
      1 / n()
    ),
    attributed_votes = coalition_votes * weight
  ) |>
  ungroup() |>
  group_by(election_id, estado, party_key = member_key) |>
  summarise(votes = sum(attributed_votes, na.rm = TRUE), .groups = "drop")

all_votes <- bind_rows(
  state_raw |>
    filter(party_key %in% parties_of_interest) |>
    rename(direct_votes = votes),
  attributed |>
    rename(direct_votes = votes)
) |>
  group_by(election_id, estado, party_key) |>
  summarise(party_votes = sum(direct_votes, na.rm = TRUE), .groups = "drop") |>
  mutate(year = sub("PRE_", "", election_id))

avg_size <- state_totals |>
  group_by(estado) |>
  summarise(avg_total = mean(state_total, na.rm = TRUE), .groups = "drop")

state_order <- avg_size |>
  arrange(avg_total) |>
  pull(estado)

# ─────────────────────────────────────────────────────────────────────────────
# Plot 1: scatter 2018 share vs 2024 share, denominator = 6-party vote
# ─────────────────────────────────────────────────────────────────────────────

party_totals_6 <- all_votes |>
  group_by(election_id, estado) |>
  summarise(total_6 = sum(party_votes, na.rm = TRUE), .groups = "drop")

shares_6 <- all_votes |>
  left_join(party_totals_6, by = c("election_id", "estado")) |>
  mutate(share = party_votes / total_6)

scatter_data <- shares_6 |>
  select(estado, party_key, year, share) |>
  pivot_wider(names_from = year, values_from = share) |>
  left_join(avg_size, by = "estado") |>
  mutate(
    grew       = `2024` >= `2018`,
    delta      = `2024` - `2018`,
    estado_lbl = title_state(estado),
    party_key  = factor(party_key, levels = party_order_fixed)
  ) |>
  filter(!is.na(`2018`), !is.na(`2024`))

p_scatter <- ggplot(scatter_data, aes(color = party_key)) +
  geom_abline(
    slope = 1,
    intercept = 0,
    color = col_grid,
    linewidth = 0.5,
    linetype = "dashed"
  ) +
  annotate(
    "text", x = Inf, y = Inf,
    label = "Ganó terreno ↑",
    hjust = 1.1, vjust = 1.8,
    size = 2.4, family = "ibm_mono", color = col_gain
  ) +
  annotate(
    "text", x = Inf, y = -Inf,
    label = "Perdió terreno ↓",
    hjust = 1.1, vjust = -0.8,
    size = 2.4, family = "ibm_mono", color = col_loss
  ) +
  geom_point(
    aes(x = `2018`, y = `2024`, size = avg_total),
    alpha = 0.80,
    shape = 16
  ) +
  geom_text_repel(
    aes(x = `2018`, y = `2024`, label = estado_lbl),
    size = 2.3,
    family = "ibm_sans",
    max.overlaps = 14,
    segment.size = 0.25,
    segment.alpha = 0.4,
    box.padding = 0.3,
    min.segment.length = 0.3,
    show.legend = FALSE
  ) +
  scale_x_continuous(
    labels = label_percent(accuracy = 1),
    expand = expansion(mult = c(0.02, 0.06))
  ) +
  scale_y_continuous(
    labels = label_percent(accuracy = 1),
    expand = expansion(mult = c(0.06, 0.06))
  ) +
  scale_color_manual(values = party_colors, guide = "none") +
  scale_size_continuous(
    range = c(2, 11),
    labels = label_number(scale = 1e-6, suffix = "M", accuracy = 0.1),
    name = "Votos totales promedio"
  ) +
  facet_wrap(
    ~ party_key,
    nrow = 2,
    ncol = 3,
    #scales = 'free',
    labeller = labeller(party_key = party_labels)
  ) +
  labs(
    title = "MORENA se consolida, el PRI se desvanece y MC llena el vacío opositor",
    subtitle = paste0(
      "Participación por estado · % del total de votos de 6 partidos · 2018 vs 2024\n",
      "Cada burbuja = un estado · Diagonal = sin cambio · Tamaño proporcional al total de votos"
    ),
    x = "Participación 2018 (%)",
    y = "Participación 2024 (%)",
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  base_theme(legend_position = "bottom")

# ─────────────────────────────────────────────────────────────────────────────
# Plot 2: arrows by share, denominator = total state vote
# ─────────────────────────────────────────────────────────────────────────────

all_votes_share <- all_votes |>
  left_join(state_totals, by = c("election_id", "estado")) |>
  mutate(share = party_votes / state_total)

delta_share <- all_votes_share |>
  select(party_key, estado, year, share) |>
  pivot_wider(names_from = year, values_from = share) |>
  mutate(
    grew = `2024` >= `2018`,
    delta_share = `2024` - `2018`,
    share_2024_adj = `2024` - if_else(`2024` >= `2018`, 0.003, -0.003)
  )

party_order_share <- all_votes_share |>
  group_by(party_key) |>
  summarise(avg_share = mean(share, na.rm = TRUE), .groups = "drop") |>
  arrange(desc(avg_share)) |>
  pull(party_key)

plot_data_share <- all_votes_share |>
  left_join(
    delta_share |> select(party_key, estado, grew),
    by = c("party_key", "estado")
  ) |>
  mutate(
    estado = factor(title_state(estado), levels = title_state(state_order)),
    party_key = factor(party_key, levels = party_order_share)
  ) |>
  filter(!is.na(share))

arrow_data_share <- delta_share |>
  mutate(
    estado = factor(title_state(estado), levels = title_state(state_order)),
    party_key = factor(party_key, levels = party_order_share)
  ) |>
  filter(!is.na(`2018`), !is.na(`2024`))

p_share_arrows <- ggplot() +
  geom_segment(
    data = arrow_data_share,
    aes(
      x = `2018`,
      xend = share_2024_adj,
      y = estado,
      yend = estado,
      colour = grew
    ),
    linewidth = 0.5,
    arrow = arrow(length = unit(0.13, "cm"), type = "closed")
  ) +
  geom_point(
    data = plot_data_share |> filter(year == "2018"),
    aes(x = share, y = estado, colour = grew),
    shape = 21,
    fill = col_bg,
    size = 2,
    stroke = 0.9
  ) +
  geom_point(
    data = plot_data_share |> filter(year == "2024"),
    aes(x = share, y = estado, colour = grew),
    shape = 23,
    size = 2,
    stroke = 0.3
  ) +
  scale_x_continuous(
    labels = label_percent(accuracy = 1),
    expand = expansion(mult = c(0, 0.05))
  ) +
  scale_colour_manual(
    values = c("TRUE" = col_gain, "FALSE" = col_loss),
    labels = c("TRUE" = "Avanzó", "FALSE" = "Retrocedió"),
    name = "2018 → 2024"
  ) +
  facet_wrap(
    ~ party_key,
    nrow = 3,
    ncol = 2,
    labeller = labeller(party_key = party_labels)
  ) +
  labs(
    title = "MORENA se consolida, el PRI se desvanece y MC llena el vacío opositor",
    subtitle = paste0(
      "Participación electoral por estado · % del total de votos · 2018 vs 2024",
      if (!is.null(TOP_N_STATES)) paste0(" · Top ", TOP_N_STATES, " estados por tamaño") else "",
      "\nVotos de coalición atribuidos proporcionalmente a cada partido"
    ),
    x = NULL,
    y = NULL,
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  base_theme(legend_position = "top")

# ─────────────────────────────────────────────────────────────────────────────
# Plot 3: arrows by absolute votes
# ─────────────────────────────────────────────────────────────────────────────

delta_votes <- all_votes |>
  select(party_key, estado, year, party_votes) |>
  pivot_wider(names_from = year, values_from = party_votes) |>
  mutate(
    grew = `2024` >= `2018`,
    delta_votes = `2024` - `2018`,
    votes_2024_adj = `2024` - if_else(`2024` >= `2018`, 8000, -8000)
  )

party_order_votes <- all_votes |>
  group_by(party_key) |>
  summarise(avg_votes = mean(party_votes, na.rm = TRUE), .groups = "drop") |>
  arrange(desc(avg_votes)) |>
  pull(party_key)

plot_data_votes <- all_votes |>
  left_join(
    delta_votes |> select(party_key, estado, grew),
    by = c("party_key", "estado")
  ) |>
  mutate(
    estado = factor(title_state(estado), levels = title_state(state_order)),
    party_key = factor(party_key, levels = party_order_votes)
  )

arrow_data_votes <- delta_votes |>
  mutate(
    estado = factor(title_state(estado), levels = title_state(state_order)),
    party_key = factor(party_key, levels = party_order_votes)
  )

p_vote_arrows <- ggplot() +
  geom_segment(
    data = arrow_data_votes,
    aes(
      x = `2018`,
      xend = votes_2024_adj,
      y = estado,
      yend = estado,
      colour = grew
    ),
    linewidth = 0.5,
    arrow = arrow(length = unit(0.13, "cm"), type = "closed")
  ) +
  geom_point(
    data = plot_data_votes |> filter(year == "2018"),
    aes(x = party_votes, y = estado, colour = grew),
    shape = 21,
    fill = col_bg,
    size = 2,
    stroke = 0.9
  ) +
  geom_point(
    data = plot_data_votes |> filter(year == "2024"),
    aes(x = party_votes, y = estado, colour = grew),
    shape = 23,
    size = 2,
    stroke = 0.3
  ) +
  scale_x_continuous(
    labels = label_number(scale = 1e-6, suffix = "M", accuracy = 0.1),
    expand = expansion(mult = c(0, 0.05))
  ) +
  scale_colour_manual(
    values = c("TRUE" = col_gain, "FALSE" = col_loss),
    labels = c("TRUE" = "Avanzó", "FALSE" = "Retrocedió"),
    name = "2018 → 2024"
  ) +
  facet_wrap(
    ~ party_key,
    nrow = 6,
    ncol = 1,
    labeller = labeller(party_key = party_labels)
  ) +
  labs(
    title = "MORENA se consolida, el PRI se desvanece y MC llena el vacío opositor",
    subtitle = paste0(
      "Votos presidenciales absolutos por estado · 2018 vs 2024",
      if (!is.null(TOP_N_STATES)) paste0(" · Top ", TOP_N_STATES, " estados por tamaño") else "",
      "\nVotos de coalición atribuidos proporcionalmente a cada partido"
    ),
    x = NULL,
    y = NULL,
    caption = "Fuente: INE PREP 2018 y 2024"
  ) +
  base_theme(legend_position = "top")

# ── Print plots ─────────────────────────────────────────────────────────────

p_scatter %>% print()
p_share_arrows %>% print()
p_vote_arrows %>% print()

# ── Optional saves ──────────────────────────────────────────────────────────
# ggsave("plot_scatter_share_6party.png", p_scatter, width = 12, height = 8, dpi = 300)
# ggsave("plot_arrows_share_total.png", p_share_arrows, width = 10, height = 12, dpi = 300)
# ggsave("plot_arrows_absolute_votes.png", p_vote_arrows, width = 10, height = 12, dpi = 300)