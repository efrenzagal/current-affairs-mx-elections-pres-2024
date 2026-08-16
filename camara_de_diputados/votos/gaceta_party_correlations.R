# Party voting alignment in Cámara de Diputados roll calls.
#
# Source this file from RStudio. It reads election_data.db but never modifies it.
# The substantive party position on a roll call is:
#
#   (Favor - Contra) / (Favor + Contra)
#
# Abstentions, absences, and quorum records are intentionally excluded. A value
# of +1 means every directional vote was Favor; -1 means every directional vote
# was Contra. Party-pair correlations are Pearson correlations of those positions
# over roll calls where both parties meet the participation threshold.
#
# Required packages:
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyr", "ggplot2", "scales"))

required_packages <- c("DBI", "RSQLite", "dplyr", "tidyr", "ggplot2", "scales")
missing_packages <- required_packages[!vapply(required_packages, requireNamespace,
                                              logical(1), quietly = TRUE)]
if (length(missing_packages) > 0) {
  stop("Install required packages first: ", paste(missing_packages, collapse = ", "))
}

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)
library(ggplot2)
library(scales)

MIN_PARTY_DIRECTIONAL_VOTES <- 5L
MIN_CORRELATION_VOTES <- 20L
ROLLING_WINDOW_DAYS <- 183L

find_project_root <- function(path = getwd()) {
  current <- normalizePath(path, mustWork = TRUE)
  repeat {
    if (file.exists(file.path(current, "election_data.db"))) return(current)
    parent <- dirname(current)
    if (identical(parent, current)) stop("Could not locate election_data.db.")
    current <- parent
  }
}

root <- find_project_root()
con <- dbConnect(SQLite(), file.path(root, "election_data.db"))
# Keep `con` open after sourcing for interactive SQL work. Disconnect with:
# dbDisconnect(con)

# A vote is retained only when its party-total summary reconciles exactly with
# its individual deputy rows, matching the quality rule used by materialization.
party_positions <- dbGetQuery(con, "
  WITH summary_party_totals AS (
    SELECT gaceta_vote_id, SUM(count) AS summary_party_total
    FROM fact_gaceta_vote_summary
    WHERE vote_choice = 'Total' AND party_key <> 'Total'
    GROUP BY gaceta_vote_id
  ), detail_totals AS (
    SELECT gaceta_vote_id, COUNT(*) AS detail_rows
    FROM fact_gaceta_deputy_vote
    GROUP BY gaceta_vote_id
  )
  SELECT
    v.legislature,
    v.gaceta_vote_id,
    v.vote_date,
    f.party_key,
    SUM(CASE WHEN f.vote_choice IN ('Favor', 'A favor') THEN 1 ELSE 0 END) AS favor,
    SUM(CASE WHEN f.vote_choice IN ('Contra', 'En contra') THEN 1 ELSE 0 END) AS contra
  FROM fact_gaceta_deputy_vote AS f
  JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
  JOIN summary_party_totals AS s ON s.gaceta_vote_id = f.gaceta_vote_id
  JOIN detail_totals AS d ON d.gaceta_vote_id = f.gaceta_vote_id
  WHERE s.summary_party_total = d.detail_rows
    AND f.vote_choice IN ('Favor', 'Contra', 'A favor', 'En contra')
  GROUP BY v.legislature, v.gaceta_vote_id, v.vote_date, f.party_key
") %>%
  mutate(
    vote_date = as.Date(vote_date),
    directional_votes = favor + contra,
    position = (favor - contra) / directional_votes
  ) %>%
  filter(directional_votes >= MIN_PARTY_DIRECTIONAL_VOTES) %>%
  arrange(legislature, vote_date, gaceta_vote_id, party_key)

empty_correlations <- function() {
  tibble(
    party_a = character(), party_b = character(), roll_calls = integer(),
    pearson_correlation = numeric()
  )
}

pairwise_correlations <- function(data) {
  parties <- sort(unique(data$party_key))
  if (length(parties) < 2) return(empty_correlations())

  bind_rows(lapply(combn(parties, 2, simplify = FALSE), function(pair) {
    paired <- data %>%
      filter(party_key %in% pair) %>%
      select(gaceta_vote_id, party_key, position) %>%
      pivot_wider(names_from = party_key, values_from = position) %>%
      drop_na(all_of(pair))

    if (nrow(paired) < MIN_CORRELATION_VOTES ||
        n_distinct(paired[[pair[1]]]) < 2 ||
        n_distinct(paired[[pair[2]]]) < 2) return(empty_correlations())

    tibble(
      party_a = pair[1],
      party_b = pair[2],
      roll_calls = nrow(paired),
      pearson_correlation = cor(paired[[pair[1]]], paired[[pair[2]]])
    )
  }))
}

party_correlations <- party_positions %>%
  group_by(legislature) %>%
  group_modify(~ pairwise_correlations(.x)) %>%
  ungroup() %>%
  arrange(legislature, desc(pearson_correlation), party_a, party_b)

# The window ends at every roll-call date. For a lighter series, replace
# `endpoints` with unique(format(leg_data$vote_date, "%Y-%m-01")) converted
# back to Date.
rolling_party_correlations <- bind_rows(lapply(sort(unique(party_positions$legislature)), function(leg) {
  leg_data <- party_positions %>% filter(legislature == leg, !is.na(vote_date))
  endpoints <- sort(unique(leg_data$vote_date))

  bind_rows(lapply(endpoints, function(window_end) {
    window_start <- window_end - ROLLING_WINDOW_DAYS
    pairwise_correlations(
      leg_data %>% filter(vote_date > window_start, vote_date <= window_end)
    ) %>%
      mutate(
        legislature = leg,
        window_start = window_start,
        window_end = window_end,
        .before = 1
      )
  }))
})) %>%
  arrange(legislature, party_a, party_b, window_end)

# -----------------------------------------------------------------------------
# Plot helpers
# -----------------------------------------------------------------------------

plot_party_correlation_matrix <- function(legislature_number) {
  pair_data <- party_correlations %>% filter(legislature == legislature_number)
  if (nrow(pair_data) == 0) stop("No correlations available for this legislature.")

  diagonal <- tibble(party_a = unique(c(pair_data$party_a, pair_data$party_b))) %>%
    transmute(party_b = party_a, pearson_correlation = 1, roll_calls = NA_integer_)
  symmetric <- bind_rows(
    pair_data,
    pair_data %>% transmute(party_a = party_b, party_b = party_a,
                            roll_calls, pearson_correlation),
    diagonal
  )

  ggplot(symmetric, aes(party_a, party_b, fill = pearson_correlation)) +
    geom_tile(color = "white", linewidth = 0.35) +
    geom_text(aes(label = number(pearson_correlation, accuracy = 0.01)), size = 3) +
    scale_fill_gradient2(low = "#b2182b", mid = "white", high = "#2166ac",
                         midpoint = 0, limits = c(-1, 1), oob = scales::squish) +
    coord_equal() +
    labs(
      title = paste("Party voting alignment — Legislature", legislature_number),
      subtitle = "Pearson correlation of party positions; abstentions excluded",
      x = NULL, y = NULL, fill = "Correlation"
    ) +
    theme_minimal(base_size = 12) +
    theme(panel.grid = element_blank(), axis.text.x = element_text(angle = 45, hjust = 1))
}

plot_party_alignment_trend <- function(party_one, party_two, legislature_number = NULL) {
  pair <- sort(c(party_one, party_two))
  trend <- rolling_party_correlations %>%
    filter(party_a == pair[1], party_b == pair[2])
  if (!is.null(legislature_number)) trend <- trend %>% filter(legislature == legislature_number)
  if (nrow(trend) == 0) stop("No rolling correlations available for that party pair.")

  ggplot(trend, aes(window_end, pearson_correlation, color = factor(legislature))) +
    geom_hline(yintercept = 0, color = "grey70") +
    geom_line(linewidth = 0.7) +
    scale_y_continuous(limits = c(-1, 1)) +
    labs(
      title = paste(pair[1], "and", pair[2], "voting alignment"),
      subtitle = "Trailing six-month Pearson correlation of party positions",
      x = NULL, y = "Correlation", color = "Legislature"
    ) +
    theme_minimal(base_size = 12)
}

# Example objects — print them in RStudio's Plots pane:
p_l66_correlation_matrix <- plot_party_correlation_matrix(66)
p_l66_pan_pri <- plot_party_alignment_trend("PAN", "PRI", 66)

# Optional exports for non-R consumers:
# write.csv(party_positions, file.path(root, "output", "gaceta_party_positions.csv"), row.names = FALSE)
# write.csv(party_correlations, file.path(root, "output", "gaceta_party_correlations.csv"), row.names = FALSE)
# write.csv(rolling_party_correlations, file.path(root, "output", "gaceta_party_correlations_rolling.csv"), row.names = FALSE)
