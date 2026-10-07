# Pollster accuracy in federal elections, 1994-2024
#
# How far each firm's final polls landed from the national result, and in
# which direction. Polls come from fact_vote_intention_poll / _option, loaded
# by vote_intention/ingest.py from Spanish Wikipedia plus a hand-entered
# supplement of polls Wikipedia never listed (source = "prensa").
#
# Every share is put on the effective basis (undecided removed) and compared
# with the share of the valid vote. Error = poll - result, in points, so a
# positive error means the poll overstated that option.
#
# Two kinds of error are separated:
#   industry error  the average miss of all firms in a cycle (e.g. everyone
#                   under-polled Sheinbaum in 2024)
#   house effect    a firm's miss relative to that industry average — the
#                   part that is about the firm, not the year

# Libraries
{
  library(tidyverse)
  library(DBI)
  library(RSQLite)
  library(plotly)
  library(htmlwidgets)
  library(scales)
}

# Parameters
{
  setwd('~/Documents/GitHub/current-affairs-mx-elections-pres-2024/')

  WINDOW_DAYS     <- 30          # "final polls": reference date this close to election day
  TRAJECTORY_DAYS <- 365         # how far back the trajectory and convergence plots go
  MIN_ELECTIONS   <- 2           # cycles a firm needs to enter the scorecard
  FIRM            <- "familia"   # "pollster" keeps renamed houses apart (Buendía y Laredo / Márquez)
  LAST_POLLS_CYCLE <- "PRE_2024" # cycle for the every-pollster plot (any election_id)
  SAVE_HTML       <- FALSE
  OUT_DIR         <- "output/vote_intention"

  GRAY_LIGHT <- "#E8E8E8"
  GRAY_MID   <- "#AAAAAA"
  TEXT_DARK  <- "#1A1A1A"

  bloque_colors <- c(
    "Izquierda" = "#8B0000",
    "PAN"       = "#1565C0",
    "PRI"       = "#2E7D32",
    "Otros"     = GRAY_MID
  )
}

# Read data
{
  con <- dbConnect(SQLite(), "election_data.db")
  options_raw <- dbGetQuery(con, read_file("vote_intention/analysis/queries/poll_options.sql")) %>%
    as_tibble()
  # Slow: aggregates fact_casilla_vote for the nine elections with polls
  party_votes <- dbGetQuery(con, read_file("vote_intention/analysis/queries/national_party_votes.sql")) %>%
    as_tibble()
  dbDisconnect(con)
}

# Benchmark: each option's share of the valid vote
#
# Joint coalition marks (PT_MORENA, C_PRI_PVEM, ...) are split equally among
# the parties marked, which is INE's rule minus its remainder rounding. For a
# candidate this changes nothing — every key of their coalition is theirs — but
# for a single party inside a coalition (the 2009/2015/2021 midterm polls) it
# credits the party's part of the joint ballots that its solo key misses.
{
  key_members <- party_votes %>%
    mutate(member = str_split(members, ",")) %>%
    unnest(member) %>%
    group_by(election_id, party_key) %>%
    mutate(n_members = n()) %>%
    ungroup()

  option_members <- options_raw %>%
    filter(!is.na(party_keys)) %>%
    distinct(election_id, party_keys) %>%
    mutate(party_key = str_split(party_keys, fixed("|"))) %>%
    unnest(party_key) %>%
    inner_join(distinct(key_members, election_id, party_key, member),
               by = c("election_id", "party_key"), relationship = "many-to-many") %>%
    distinct(election_id, party_keys, member)

  benchmark <- option_members %>%
    inner_join(select(key_members, election_id, member, votes, n_members, valid_votes),
               by = c("election_id", "member"), relationship = "many-to-many") %>%
    group_by(election_id, party_keys) %>%
    summarise(votes = sum(votes / n_members), valid_votes = first(valid_votes), .groups = "drop") %>%
    mutate(result = 100 * votes / valid_votes)

  # Candidates own every key they are credited with, so splitting must leave
  # their result exactly where the warehouse view has it
  check <- options_raw %>%
    filter(option_kind == "candidato", !is.na(party_keys)) %>%
    distinct(election_id, party_keys, result_pct_valid) %>%
    inner_join(benchmark, by = c("election_id", "party_keys"))
  stopifnot(max(abs(check$result - check$result_pct_valid)) < 0.01)
}

# Clean: one row per ballot option, with its error
{
  candidato_bloque <- c(
    "Ernesto Zedillo" = "PRI", "Francisco Labastida" = "PRI", "Roberto Madrazo" = "PRI",
    "Enrique Peña Nieto" = "PRI", "José Antonio Meade" = "PRI",
    "Diego Fernández de Cevallos" = "PAN", "Vicente Fox" = "PAN", "Felipe Calderón" = "PAN",
    "Josefina Vázquez Mota" = "PAN", "Ricardo Anaya" = "PAN",
    "Xóchitl Gálvez" = "PAN",   # PAN-PRI-PRD, PAN-nominated
    "Cuauhtémoc Cárdenas" = "Izquierda", "Andrés Manuel López Obrador" = "Izquierda",
    "Claudia Sheinbaum" = "Izquierda"
  )

  options <- options_raw %>%
    filter(
      poll_kind == "encuesta",              # drops web/street "sondeos", averages and mock votes
      share_basis != "inconsistente",       # 5 polls whose shares add up under no reading
      !is.na(party_keys), !is.na(pct_efectiva),
      # A party question in a presidential year measures party ID, not the
      # ballot. 1994 is the exception: its table names parties for candidates.
      intention_level != "partido" | str_starts(election_id, "DIP") | election_id == "PRE_1994"
    ) %>%
    inner_join(select(benchmark, election_id, party_keys, result),
               by = c("election_id", "party_keys")) %>%
    mutate(
      firm      = .data[[FIRM]],
      year      = as.integer(str_extract(election_id, "\\d{4}")),
      ciclo     = str_c(if_else(str_starts(election_id, "PRE"), "Presidencial", "Diputados"), " ", year),
      ciclo     = fct_reorder(ciclo, year),
      reference_date = as.Date(reference_date),
      bloque    = case_when(
        !is.na(candidato) ~ coalesce(candidato_bloque[candidato], "Otros"),
        partido_coalicion %in% c("PRI", "PAN") ~ partido_coalicion,
        partido_coalicion == "PAN-PRD-MC" ~ "PAN",          # 2017 "probable coalition" polls
        partido_coalicion == "PRI-PVEM-NA" ~ "PRI",
        partido_coalicion == "MORENA-PT" ~ "Izquierda",
        partido_coalicion %in% c("MORENA", "PT") ~ "Izquierda",
        str_detect(partido_coalicion, "Salvemos") ~ "Izquierda",
        partido_coalicion == "PRD" & year < 2018 ~ "Izquierda",   # PRD ran with PAN-PRI from 2018
        TRUE ~ "Otros"
      ),
      bloque    = factor(bloque, levels = names(bloque_colors)),
      error     = pct_efectiva - result
    )

  final <- options %>% filter(days_before_election <= WINDOW_DAYS)

  # Actual top two per cycle, for the error on the lead
  top_two <- final %>%
    distinct(election_id, party_keys, result) %>%
    group_by(election_id) %>%
    slice_max(result, n = 2, with_ties = FALSE) %>%
    mutate(rank = row_number()) %>%
    ungroup() %>%
    select(election_id, party_keys, rank)

  # Bloc totals per poll: a bloc can be two options (PRD + MORENA in 2015)
  poll_bloque <- options %>%
    filter(days_before_election <= TRAJECTORY_DAYS) %>%
    group_by(election_id, ciclo, year, poll_id, firm, pollster, reference_date,
             days_before_election, sample_size, intention_level, bloque) %>%
    summarise(estimate = sum(pct_efectiva), result = sum(result), .groups = "drop") %>%
    mutate(error = estimate - result)
}

# EDA: what is in the final window
{
  final %>%
    distinct(ciclo, poll_id, firm) %>%
    count(ciclo, name = "polls") %>%
    left_join(final %>% distinct(ciclo, firm) %>% count(ciclo, name = "firms"), by = "ciclo") %>%
    print()

  final %>%
    distinct(firm, election_id) %>%
    count(firm, name = "cycles", sort = TRUE) %>%
    print(n = 40)
}

# Per poll: average miss across options and miss on the lead
#
# Expected sampling error assumes simple random sampling on the reported n.
# Effective shares drop undecided, so the true n behind them is smaller and
# this expected error is a lower bound — ratios above 1 are partly that.
{
  poll_metrics <- final %>%
    left_join(top_two, by = c("election_id", "party_keys")) %>%
    group_by(election_id, ciclo, year, poll_id, firm, pollster, reference_date,
             days_before_election, sample_size) %>%
    summarise(
      n_options    = n(),
      mae          = mean(abs(error)),
      has_top_two  = sum(rank %in% 1:2, na.rm = TRUE) == 2,
      margin_poll  = sum(pct_efectiva[rank %in% 1], na.rm = TRUE) - sum(pct_efectiva[rank %in% 2], na.rm = TRUE),
      margin_real  = sum(result[rank %in% 1], na.rm = TRUE) - sum(result[rank %in% 2], na.rm = TRUE),
      p1           = sum(result[rank %in% 1], na.rm = TRUE) / 100,
      p2           = sum(result[rank %in% 2], na.rm = TRUE) / 100,
      .groups = "drop"
    ) %>%
    filter(has_top_two) %>%
    mutate(
      margin_error  = margin_poll - margin_real,        # > 0: overstated the winner's lead
      called_winner = margin_poll > 0,
      se_margin     = 100 * sqrt((p1 + p2 - (p1 - p2)^2) / sample_size)
    )

  # Each cycle counts once per firm, so a house that tracked weekly in one
  # cycle does not outweigh the others; the standard error comes from the
  # spread across cycles, which is where the independent information is.
  firm_cycle <- poll_metrics %>%
    group_by(firm, election_id, ciclo, year) %>%
    summarise(
      n_polls       = n(),
      mae           = mean(mae),
      margin_error  = mean(margin_error),
      called_winner = mean(called_winner),
      sq_error      = mean(margin_error^2),
      se_margin     = sqrt(mean(se_margin^2)),
      .groups = "drop"
    )

  firm_scorecard <- firm_cycle %>%
    group_by(firm) %>%
    summarise(
      n_cycles      = n(),
      n_polls       = sum(n_polls),
      cycles        = str_c(sort(year), collapse = ", "),
      mae_se        = sd(mae) / sqrt(n()),   # before mae is overwritten below
      mae           = mean(mae),
      margin_bias   = mean(margin_error),
      margin_bias_se = sd(margin_error) / sqrt(n()),
      margin_rmse   = sqrt(mean(sq_error)),
      sampling_se   = sqrt(mean(se_margin^2, na.rm = TRUE)),
      called_winner = mean(called_winner),
      .groups = "drop"
    ) %>%
    mutate(error_ratio = margin_rmse / sampling_se) %>%
    arrange(mae)

  firm_scorecard %>% filter(n_cycles >= MIN_ELECTIONS) %>% print(n = 40, width = Inf)
}

# Industry error and house effects by bloc
{
  # A firm's cycle-average first, then the industry as the mean over firms,
  # so the industry line is not just whichever house polled the most
  firm_cycle_bloque <- poll_bloque %>%
    filter(days_before_election <= WINDOW_DAYS) %>%
    group_by(firm, election_id, ciclo, year, bloque) %>%
    summarise(n_polls = n(), error = mean(error), result = first(result), .groups = "drop")

  industry <- firm_cycle_bloque %>%
    group_by(election_id, ciclo, year, bloque) %>%
    summarise(
      n_firms  = n(),
      industry = mean(error),
      se       = sd(error) / sqrt(n()),
      result   = first(result),
      .groups = "drop"
    )

  house <- firm_cycle_bloque %>%
    left_join(select(industry, election_id, bloque, industry, n_firms),
              by = c("election_id", "bloque")) %>%
    filter(n_firms >= 2) %>%                     # no house effect against oneself
    mutate(house_effect = error - industry)

  house_summary <- house %>%
    group_by(firm, bloque) %>%
    summarise(
      n_cycles     = n(),
      house_effect = mean(house_effect),
      se           = sd(house_effect) / sqrt(n()),
      raw_bias     = mean(error),
      .groups = "drop"
    )

  industry %>%
    select(ciclo, bloque, n_firms, result, industry, se) %>%
    arrange(ciclo, bloque) %>%
    print(n = 60)
}

# Plot helpers
{
  theme_polls <- theme_minimal(base_size = 11) +
    theme(
      panel.grid.minor = element_blank(),
      panel.grid.major = element_line(color = GRAY_LIGHT),
      plot.title       = element_text(color = TEXT_DARK, face = "bold"),
      legend.position  = "bottom"
    )

  # ggplotly drops the subtitle, so title and subtitle are rebuilt as one
  # plotly title, with room above the facet strips
  show_plot <- function(gg, name, tooltip = "text", legend = TRUE) {
    gg_labs <- get_labs(gg)
    title <- str_c(
      "<b>", gg_labs$title, "</b>",
      if (is.null(gg_labs$subtitle)) "" else str_c("<br><sup>", gg_labs$subtitle, "</sup>")
    )
    widget <- ggplotly(gg, tooltip = tooltip) %>%
      layout(title = list(text = title, y = 0.98), margin = list(t = 100),
             showlegend = legend, legend = list(orientation = "h", y = -0.15))
    if (SAVE_HTML) {
      dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)
      saveWidget(widget, file.path(OUT_DIR, str_c(name, ".html")),
                 selfcontained = FALSE, libdir = "lib")
    }
    if (interactive()) print(widget)
    invisible(widget)
  }

  fmt <- function(x, digits = 1) formatC(x, format = "f", digits = digits)
}

# Plot 1: poll trajectories against the result, by cycle
{
  results_line <- poll_bloque %>%
    filter(bloque != "Otros") %>%
    distinct(ciclo, bloque, result)

  p_trayectorias <- poll_bloque %>%
    filter(bloque != "Otros") %>%
    ggplot(aes(reference_date, estimate, color = bloque,
               text = str_c(pollster, "<br>", reference_date,
                            "<br>", bloque, ": ", fmt(estimate), "%",
                            "<br>Resultado: ", fmt(result), "%",
                            "<br>Error: ", fmt(error), " pts"))) +
    geom_hline(data = results_line, aes(yintercept = result, color = bloque),
               linetype = "dashed", linewidth = 0.4, inherit.aes = FALSE) +
    geom_point(alpha = 0.6, size = 1.4) +
    facet_wrap(~ciclo, scales = "free_x", ncol = 3) +
    scale_color_manual(values = bloque_colors) +
    scale_y_continuous(labels = label_number(suffix = "%")) +
    labs(title = "Encuestas vs. resultado (línea punteada)",
         subtitle = "Preferencia efectiva por bloque, último año de cada ciclo",
         x = NULL, y = NULL, color = NULL) +
    theme_polls

  show_plot(p_trayectorias, "01_trayectorias")
}

# Plot 2: does error shrink as election day nears?
{
  p_convergencia <- poll_bloque %>%
    filter(bloque != "Otros") %>%
    ggplot(aes(days_before_election, error, color = bloque)) +
    geom_hline(yintercept = 0, color = GRAY_MID) +
    geom_point(aes(text = str_c(pollster, " · ", ciclo,
                                "<br>", days_before_election, " días antes",
                                "<br>", bloque, " error: ", fmt(error), " pts")),
               alpha = 0.5, size = 1.3) +
    geom_smooth(method = "loess", se = FALSE, span = 0.8, linewidth = 0.8) +
    scale_x_reverse() +
    scale_color_manual(values = bloque_colors) +
    labs(title = "Error por bloque según la distancia a la elección",
         subtitle = "Error = encuesta − resultado (puntos); todas las elecciones",
         x = "Días antes de la elección", y = "Error (pts)", color = NULL) +
    theme_polls

  show_plot(p_convergencia, "02_convergencia")
}

# Plot 3: industry-wide miss per cycle
{
  p_industria <- industry %>%
    filter(bloque != "Otros") %>%
    ggplot(aes(ciclo, industry, fill = bloque,
               text = str_c(ciclo, " · ", bloque,
                            "<br>Error promedio: ", fmt(industry), " pts",
                            "<br>± ", fmt(1.96 * se), " (IC 95%)",
                            "<br>Firmas: ", n_firms,
                            "<br>Resultado: ", fmt(result), "%"))) +
    geom_hline(yintercept = 0, color = GRAY_MID) +
    geom_col(position = position_dodge(width = 0.8), width = 0.75) +
    geom_errorbar(aes(ymin = industry - 1.96 * se, ymax = industry + 1.96 * se),
                  position = position_dodge(width = 0.8), width = 0.2, color = TEXT_DARK) +
    scale_fill_manual(values = bloque_colors) +
    labs(title = "Error de la industria por ciclo",
         subtitle = str_c("Promedio entre firmas, encuestas a ≤", WINDOW_DAYS,
                          " días; > 0 = sobreestimado"),
         x = NULL, y = "Error promedio (pts)", fill = NULL) +
    theme_polls +
    theme(axis.text.x = element_text(angle = 30, hjust = 1))

  show_plot(p_industria, "03_error_industria")
}

# Plot 4: firm accuracy scorecard
{
  scorecard_plot <- firm_scorecard %>%
    filter(n_cycles >= MIN_ELECTIONS) %>%
    mutate(firm = fct_reorder(firm, mae, .desc = TRUE))

  p_scorecard <- scorecard_plot %>%
    ggplot(aes(mae, firm,
               text = str_c(firm, "<br>Ciclos: ", cycles,
                            "<br>Encuestas: ", n_polls,
                            "<br>Error absoluto medio: ", fmt(mae), " pts",
                            "<br>Sesgo en la ventaja: ", fmt(margin_bias), " ± ",
                            fmt(1.96 * margin_bias_se), " pts",
                            "<br>RMSE ventaja: ", fmt(margin_rmse),
                            " (muestreo esperado: ", fmt(sampling_se), ")",
                            "<br>Ganador correcto: ", percent(called_winner, 1)))) +
    geom_errorbar(aes(xmin = pmax(mae - 1.96 * mae_se, 0), xmax = mae + 1.96 * mae_se),
                  orientation = "y", width = 0, color = GRAY_MID) +
    geom_point(aes(size = n_polls), color = TEXT_DARK) +
    scale_size_area(max_size = 6) +
    labs(title = "Precisión por encuestadora",
         subtitle = str_c("Error absoluto medio por opción, encuestas a ≤", WINDOW_DAYS,
                          " días; cada ciclo pesa igual; firmas con ≥", MIN_ELECTIONS, " ciclos"),
         x = "Error absoluto medio (pts)", y = NULL, size = "Encuestas") +
    theme_polls

  show_plot(p_scorecard, "04_precision_firmas")
}

# Plot 5: bias on the lead — overstate or understate the winner?
{
  p_ventaja <- poll_metrics %>%
    semi_join(filter(firm_scorecard, n_cycles >= MIN_ELECTIONS), by = "firm") %>%
    mutate(firm = fct_reorder(firm, margin_error)) %>%
    ggplot(aes(margin_error, firm, color = ciclo,
               text = str_c(pollster, " · ", ciclo, " · ", reference_date,
                            "<br>Ventaja encuesta: ", fmt(margin_poll), " pts",
                            "<br>Ventaja real: ", fmt(margin_real), " pts",
                            "<br>Error: ", fmt(margin_error), " pts"))) +
    geom_vline(xintercept = 0, color = GRAY_MID) +
    geom_point(size = 2, alpha = 0.8) +
    scale_color_viridis_d(option = "D", end = 0.9) +
    labs(title = "Error en la ventaja del ganador",
         subtitle = "> 0: la encuesta exageró la ventaja del primer lugar; < 0: la subestimó",
         x = "Error en la ventaja (pts)", y = NULL, color = NULL) +
    theme_polls

  show_plot(p_ventaja, "05_error_ventaja")
}

# Plot 6: house effects by bloc (relative to the industry in each cycle)
{
  house_plot <- house_summary %>%
    filter(bloque != "Otros") %>%
    semi_join(filter(firm_scorecard, n_cycles >= MIN_ELECTIONS), by = "firm") %>%
    mutate(firm = fct_reorder(firm, house_effect * (bloque == "Izquierda"), .fun = sum))

  p_casa <- house_plot %>%
    ggplot(aes(bloque, firm, fill = house_effect,
               text = str_c(firm, " · ", bloque,
                            "<br>Efecto casa: ", fmt(house_effect), " pts",
                            if_else(is.na(se), "", str_c(" ± ", fmt(1.96 * se))),
                            "<br>Sesgo bruto: ", fmt(raw_bias), " pts",
                            "<br>Ciclos: ", n_cycles))) +
    geom_tile(color = "white") +
    geom_text(aes(label = fmt(house_effect)), size = 3) +
    scale_fill_gradient2(low = "#B2182B", mid = "white", high = "#2166AC", midpoint = 0,
                         limits = c(-1, 1) * max(abs(house_plot$house_effect))) +
    labs(title = "Efecto casa por bloque",
         subtitle = "Error de la firma menos el error promedio de la industria en el mismo ciclo",
         x = NULL, y = NULL, fill = "pts") +
    theme_polls +
    theme(panel.grid.major = element_blank())

  show_plot(p_casa, "06_efecto_casa")
}

# Plot 7: house effect on the left bloc across cycles
{
  p_casa_tiempo <- house %>%
    filter(bloque == "Izquierda") %>%
    semi_join(filter(firm_scorecard, n_cycles >= 3), by = "firm") %>%
    ggplot(aes(year, house_effect, color = firm, group = firm,
               text = str_c(firm, " · ", ciclo,
                            "<br>Efecto casa: ", fmt(house_effect), " pts",
                            "<br>Error bruto: ", fmt(error), " pts",
                            "<br>Industria: ", fmt(industry), " pts"))) +
    geom_hline(yintercept = 0, color = GRAY_MID) +
    geom_line(alpha = 0.6) +
    geom_point(size = 2) +
    scale_x_continuous(breaks = sort(unique(house$year))) +
    labs(title = "Efecto casa sobre la izquierda (Cárdenas / PRD / AMLO / MORENA / Sheinbaum)",
         subtitle = "Firmas con ≥3 ciclos; > 0 = más favorable a la izquierda que el promedio",
         x = NULL, y = "Efecto casa (pts)", color = NULL) +
    theme_polls

  show_plot(p_casa_tiempo, "07_efecto_casa_izquierda")
}

# Plot 8: observed error vs what sampling alone would produce
{
  p_muestreo <- poll_metrics %>%
    filter(!is.na(se_margin)) %>%
    ggplot(aes(se_margin, abs(margin_error), color = ciclo,
               text = str_c(pollster, " · ", ciclo,
                            "<br>n = ", comma(sample_size),
                            "<br>Error esperado (1 EE): ", fmt(se_margin), " pts",
                            "<br>Error observado: ", fmt(abs(margin_error)), " pts"))) +
    geom_abline(slope = 1.96, intercept = 0, linetype = "dashed", color = GRAY_MID) +
    geom_point(size = 2, alpha = 0.8) +
    scale_color_viridis_d(option = "D", end = 0.9) +
    labs(title = "Error observado vs. error de muestreo esperado",
         subtitle = "Ventaja del ganador; sobre la línea punteada = fuera del IC 95% de muestreo",
         x = "Error estándar esperado (pts)", y = "|Error en la ventaja| (pts)", color = NULL) +
    theme_polls

  show_plot(p_muestreo, "08_error_vs_muestreo")
}

# Plot 9: every pollster's last poll of one cycle vs. the result
#
# One row per house, with its latest poll in the year before the election (on
# a tie the candidate question beats the coalition one). Houses that stopped
# polling before the final window stay in, drawn faint, so the chart shows
# everyone who published without passing early polls off as final calls.
# Panels are blocs, so a nominee change (MC: García -> Máynez in 2024) or a
# coalition-only question still lands in the right panel.
{
  plot_last_polls <- function(cycle) {
    cycle_bloque <- poll_bloque %>% filter(election_id == cycle)

    last_polls <- cycle_bloque %>%
      distinct(pollster, poll_id, reference_date, intention_level) %>%
      arrange(pollster, desc(reference_date), intention_level != "candidato", desc(poll_id)) %>%
      group_by(pollster) %>%
      slice(1) %>%
      ungroup()

    shown <- cycle_bloque %>%
      semi_join(last_polls, by = "poll_id") %>%
      mutate(
        fila    = str_c(pollster, " · ", format(reference_date, "%d/%m/%y")),
        periodo = if_else(days_before_election <= WINDOW_DAYS,
                          str_c("Últimos ", WINDOW_DAYS, " días"), "Antes")
      )

    # A bloc's result can differ slightly between polls when they list
    # different options (2018: Bronco alone vs. Bronco + Zavala); the panel
    # line uses the most common one, each poll's bar its own
    results <- shown %>%
      count(bloque, result) %>%
      group_by(bloque) %>%
      slice_max(n, n = 1, with_ties = FALSE) %>%
      ungroup() %>%
      select(bloque, result)

    nominees <- options %>%
      filter(election_id == cycle, !is.na(candidato)) %>%
      count(bloque, candidato) %>%
      group_by(bloque) %>%
      slice_max(n, n = 1, with_ties = FALSE) %>%
      ungroup() %>%
      select(bloque, candidato)

    panel_titles <- results %>%
      left_join(nominees, by = "bloque") %>%
      mutate(title = str_c(bloque, if_else(is.na(candidato), "", str_c(" (", candidato, ")")),
                           " · resultado ", fmt(result), "%")) %>%
      arrange(desc(result))

    # Rows ordered by the winner's number, so a house sits on the same row in every panel
    winner <- as.character(panel_titles$bloque[1])
    fila_order <- shown %>%
      filter(bloque == winner) %>%
      arrange(estimate) %>%
      pull(fila)
    fila_order <- c(setdiff(unique(shown$fila), fila_order), fila_order)

    shown %>%
      mutate(
        fila   = factor(fila, levels = fila_order),
        bloque = factor(bloque, levels = panel_titles$bloque)
      ) %>%
      ggplot(aes(estimate, fila, color = bloque)) +
      geom_vline(data = mutate(results, bloque = factor(bloque, levels = panel_titles$bloque)),
                 aes(xintercept = result),
                 color = TEXT_DARK, linetype = "dashed", linewidth = 0.4) +
      geom_segment(aes(x = result, xend = estimate, yend = fila),
                   color = GRAY_LIGHT, linewidth = 1.2) +
      geom_point(aes(alpha = periodo,
                     text = str_c(pollster, " · ", reference_date,
                                  " (", days_before_election, " días antes)",
                                  "<br>", bloque, ", pregunta por ", intention_level,
                                  "<br>Encuesta: ", fmt(estimate), "%",
                                  "<br>Resultado: ", fmt(result), "%",
                                  "<br>Error: ", fmt(error), " pts")),
                 size = 2.8) +
      facet_wrap(~bloque, scales = "free_x",
                 labeller = as_labeller(setNames(panel_titles$title, panel_titles$bloque))) +
      scale_color_manual(values = bloque_colors, guide = "none") +
      scale_alpha_manual(values = setNames(c(1, 0.35), c(str_c("Últimos ", WINDOW_DAYS, " días"), "Antes")),
                         name = NULL) +
      scale_x_continuous(labels = label_number(suffix = "%"),
                         expand = expansion(mult = 0.08)) +
      labs(title = str_c(shown$ciclo[1], ": última encuesta de cada casa vs. resultado"),
           subtitle = str_c("Preferencia efectiva; puntos tenues = más de ", WINDOW_DAYS,
                            " días antes de la elección; línea punteada = resultado"),
           x = NULL, y = NULL) +
      theme_polls +
      theme(strip.text = element_text(face = "bold"),
            panel.spacing.x = unit(1.5, "lines"))
  }

  p_ultimas <- plot_last_polls(LAST_POLLS_CYCLE)
  show_plot(p_ultimas, str_c("09_ultimas_encuestas_", LAST_POLLS_CYCLE), legend = FALSE)

  # Every cycle at once:
  # walk(sort(unique(options$election_id)),
  #      ~ show_plot(plot_last_polls(.x), str_c("09_ultimas_encuestas_", .x), legend = FALSE))
}
