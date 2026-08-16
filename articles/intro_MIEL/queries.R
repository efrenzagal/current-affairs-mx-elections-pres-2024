# MIEL (Monitor Integral y Estadístico Legislativo) — intro queries.
#
# What we have per roll-call vote, and what we don't:
#
#   HAVE
#   - Vote metadata: title/description, date, chamber, legislature, status
#     text as published (dim_gaceta_vote for Diputados, dim_senado_vote for
#     Senado).
#   - Chamber-wide totals as reported by the source (dim_senado_vote.en_pro /
#     en_contra / abstencion for Senado; fact_gaceta_vote_summary party_key =
#     'Total' rows for Diputados).
#   - Individual-level vote choice PLUS the parliamentary group (party_key /
#     grupo_parlamentario) each legislator sat under *at the time of that
#     specific vote* (fact_gaceta_deputy_vote, fact_senador_vote). That's
#     what makes the party-cohesion queries below possible.
#   - For legislatura 66 Diputados votes only: an LLM-derived classification
#     (origen, etapa_votacion, tipo_instrumento, tema_politica) in
#     fact_gaceta_vote_classification — topic/stage/instrument type, not an
#     author.
#
#   INITIATIVE PROPOSERS (who introduced the bill)
#   Not on the roll-call pages above — a separate source page tree per
#   chamber, now crawled into dim_gaceta_iniciativa (legislatura 66, 6,720
#   rows) and dim_senado_iniciativa (4,663 rows, legislatura LXVI only).
#   Both have proposer_type (legislador/ejecutivo/minuta for Diputados;
#   legislador/otro for Senado — that feed only covers legislator-authored
#   initiatives), proposer_name/proposer_party when a named legislator is
#   identified, and comision (committee referral). ~4-5% of rows are
#   needs_review=1 (proposer text didn't match a known template, mostly
#   multi-signatory joint initiatives) — title/proposer_raw are preserved
#   regardless, so nothing is silently dropped.
#   Only dim_gaceta_iniciativa.vote_url joins to a resulting vote
#   (dim_gaceta_vote.source_url); most initiatives never reach a vote, and
#   no such join exists yet for Senado.
#
#   ON PARTY COHESION
#   Parties are very often NOT unanimous, but the common case is a large
#   majority bloc plus a handful of dissenters/absences. The queries below
#   compute, per vote per party: the majority position, how many voted with
#   it, how many against it, and a cohesion rate = majority_share of the
#   directional (Favor/Contra) vote. This mirrors the definition already used
#   in camara_de_diputados/votos/gaceta_party_correlations.R:
#     position = (favor - contra) / (favor + contra)
#   party_key/grupo_parlamentario is the parliamentary group reported AT VOTE
#   TIME, not a static electoral affiliation — a legislator who changes group
#   mid-legislature shows up under both.
#
# Required packages:
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyr"))

required_packages <- c("DBI", "RSQLite", "dplyr", "tidyr")
missing_packages <- required_packages[!vapply(required_packages, requireNamespace,
                                              logical(1), quietly = TRUE)]
if (length(missing_packages) > 0) {
  stop("Install required packages first: ", paste(missing_packages, collapse = ", "))
}

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)

MIN_PARTY_DIRECTIONAL_VOTES <- 5L

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

## ── Diputados (Gaceta) ───────────────────────────────────────────────────

# One row per vote x party x reported choice (Favor/Contra/Abstencion/
# Ausente/Quorum*), with vote metadata attached. Use this to see the full
# breakdown of "how the party voted", not just the majority side.
votes_by_party_dip <- dbGetQuery(con, "
  SELECT
    v.legislature, v.gaceta_vote_id, v.vote_date, v.title,
    f.party_key, f.vote_choice, COUNT(*) AS n
  FROM fact_gaceta_deputy_vote AS f
  JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
  GROUP BY v.legislature, v.gaceta_vote_id, v.vote_date, v.title,
           f.party_key, f.vote_choice
")

# Party majority position + cohesion per vote. Only votes where the
# party-total summary reconciles exactly with the individual detail rows are
# kept (same quality gate as gaceta_party_correlations.R).
party_positions_dip <- dbGetQuery(con, "
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
    v.legislature, v.gaceta_vote_id, v.vote_date, v.title,
    f.party_key,
    SUM(CASE WHEN f.vote_choice IN ('Favor', 'A favor') THEN 1 ELSE 0 END) AS favor,
    SUM(CASE WHEN f.vote_choice IN ('Contra', 'En contra') THEN 1 ELSE 0 END) AS contra
  FROM fact_gaceta_deputy_vote AS f
  JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
  JOIN summary_party_totals AS s ON s.gaceta_vote_id = f.gaceta_vote_id
  JOIN detail_totals AS d ON d.gaceta_vote_id = f.gaceta_vote_id
  WHERE s.summary_party_total = d.detail_rows
    AND f.vote_choice IN ('Favor', 'Contra', 'A favor', 'En contra')
  GROUP BY v.legislature, v.gaceta_vote_id, v.vote_date, v.title, f.party_key
") %>%
  mutate(
    vote_date = as.Date(vote_date),
    directional_votes = favor + contra,
    majority_choice = ifelse(favor >= contra, "Favor", "Contra"),
    majority_votes = pmax(favor, contra),
    dissenters = pmin(favor, contra),
    cohesion = majority_votes / directional_votes
  ) %>%
  filter(directional_votes >= MIN_PARTY_DIRECTIONAL_VOTES) %>%
  arrange(legislature, vote_date, gaceta_vote_id, party_key)

# Legislatura 66 vote metadata + topic/stage/instrument classification, for
# picking illustrative votes for the article.
votes_overview_dip_l66 <- dbGetQuery(con, "
  SELECT
    v.gaceta_vote_id, v.vote_date, v.title, v.status_text,
    c.tema_politica, c.tipo_instrumento, c.etapa_votacion, c.origen
  FROM dim_gaceta_vote AS v
  LEFT JOIN fact_gaceta_vote_classification AS c
    ON c.gaceta_vote_id = v.gaceta_vote_id
  WHERE v.legislature = 66
  ORDER BY v.vote_date
")

## ── Senado ────────────────────────────────────────────────────────────────

# One row per vote x party x reported choice (PRO/CONTRA/ABSTENCIÓN/AUSENTE).
votes_by_party_sen <- dbGetQuery(con, "
  SELECT
    v.votacion_id, v.vote_date, v.description,
    f.grupo_parlamentario AS party_key, f.voto AS vote_choice, COUNT(*) AS n
  FROM fact_senador_vote AS f
  JOIN dim_senado_vote AS v ON v.votacion_id = f.votacion_id
  GROUP BY v.votacion_id, v.vote_date, v.description,
           f.grupo_parlamentario, f.voto
")

# Party majority position + cohesion per vote. Senado has no separate
# party-total summary table to reconcile against (unlike Diputados), so this
# is computed directly off the individual-level detail rows.
party_positions_sen <- dbGetQuery(con, "
  SELECT
    v.votacion_id, v.vote_date, v.description,
    f.grupo_parlamentario AS party_key,
    SUM(CASE WHEN f.voto = 'PRO' THEN 1 ELSE 0 END) AS favor,
    SUM(CASE WHEN f.voto = 'CONTRA' THEN 1 ELSE 0 END) AS contra
  FROM fact_senador_vote AS f
  JOIN dim_senado_vote AS v ON v.votacion_id = f.votacion_id
  GROUP BY v.votacion_id, v.vote_date, v.description, f.grupo_parlamentario
") %>%
  mutate(
    vote_date = as.Date(vote_date),
    directional_votes = favor + contra,
    majority_choice = ifelse(favor >= contra, "Favor", "Contra"),
    majority_votes = pmax(favor, contra),
    dissenters = pmin(favor, contra),
    cohesion = majority_votes / directional_votes
  ) %>%
  filter(directional_votes >= MIN_PARTY_DIRECTIONAL_VOTES) %>%
  arrange(vote_date, votacion_id, party_key)

## ── Iniciativas (proposers) — Diputados ─────────────────────────────────

# Full legislatura 66 initiative roster: one row per initiative, with
# proposer identity (when named), committee referral, and vote_url when the
# initiative reached a floor vote.
iniciativas_dip <- dbGetQuery(con, "
  SELECT *
  FROM dim_gaceta_iniciativa
  WHERE legislature = 66
")

# Named-legislator initiatives per party: volume proposed vs. share that
# actually reached a floor vote. proposer_type = 'ejecutivo'/'minuta' rows
# have no party and are excluded here on purpose. Uses proposer_party_canonical
# (MORENA/PAN/PRI/PVEM/PT/MC/IND), not the raw proposer_party text -- the raw
# text mixes full names, abbreviations, and casing, and can be contaminated
# with co-signer names for multi-signatory initiatives.
iniciativas_by_party_dip <- iniciativas_dip %>%
  filter(proposer_type == "legislador") %>%
  group_by(proposer_party_canonical) %>%
  summarise(
    n_iniciativas = n(),
    n_con_votacion = sum(!is.na(vote_url)),
    share_con_votacion = n_con_votacion / n_iniciativas,
    .groups = "drop"
  ) %>%
  arrange(desc(n_iniciativas))

# Initiatives that reached a vote, joined to that vote's outcome — lets you
# check e.g. whether a party's own initiatives tend to pass.
iniciativas_con_resultado_dip <- dbGetQuery(con, "
  SELECT
    i.gaceta_iniciativa_id, i.title, i.proposer_name, i.proposer_party,
    i.comision, v.vote_date, v.status_text
  FROM dim_gaceta_iniciativa AS i
  JOIN dim_gaceta_vote AS v ON v.source_url = i.vote_url
  WHERE i.legislature = 66
")

## ── Iniciativas (proposers) — Senado ────────────────────────────────────

# Full initiative roster (tipo=Inic feed only -- legislator-authored;
# Ejecutivo federal and Minuta initiatives are out of scope, see header).
iniciativas_sen <- dbGetQuery(con, "SELECT * FROM dim_senado_iniciativa")

iniciativas_by_party_sen <- iniciativas_sen %>%
  filter(proposer_type == "legislador") %>%
  group_by(proposer_party_canonical) %>%
  summarise(n_iniciativas = n(), .groups = "drop") %>%
  arrange(desc(n_iniciativas))

## ── Quick sanity checks ─────────────────────────────────────────────────

cat("Diputados: ", nrow(party_positions_dip), " (vote, party) rows; median cohesion = ",
    round(median(party_positions_dip$cohesion), 3), "\n", sep = "")
cat("Senado: ", nrow(party_positions_sen), " (vote, party) rows; median cohesion = ",
    round(median(party_positions_sen$cohesion), 3), "\n", sep = "")
cat("Iniciativas Diputados: ", nrow(iniciativas_dip), " (",
    round(mean(iniciativas_dip$needs_review), 3) * 100, "% needs_review); ",
    sum(!is.na(iniciativas_dip$vote_url)), " reached a vote\n", sep = "")
cat("Iniciativas Senado: ", nrow(iniciativas_sen), " (",
    round(mean(iniciativas_sen$needs_review), 3) * 100, "% needs_review)\n", sep = "")
