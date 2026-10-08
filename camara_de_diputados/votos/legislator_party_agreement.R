# Legislator agreement with every party and with data-driven party blocs,
# LXVI Legislature, Cámara de Diputados and Senado.
#
# Source this file from RStudio. It reads election_data.db but never modifies it.
#
# Definitions
#
#   Group position on a roll call: the group's most common vote among Favor,
#   Contra and Abstención. Absences are not votes and are ignored. A tie, or a
#   group with nobody voting, leaves the group without a position on that roll
#   call.
#
#   Agreement: the share of a legislator's votes (absences excluded) that
#   matched a group's position, over the roll calls where the group had one.
#   When the legislator belongs to the group, their own vote is removed from
#   the group's count first, so nobody agrees with themselves. That matters for
#   the small benches.
#
#   Blocs: the parties are clustered on how often their positions coincide
#   (distance = 1 - agreement, average linkage). The number of blocs is the one
#   with the best mean silhouette, unless BLOC_K fixes it. A bloc's position is
#   the majority of all its members pooled, so large benches weigh more.
#
#   Party is the bench each vote was cast under, so a legislator who switched
#   counts with the party they voted for at the time. Their label in the
#   output is their latest bench.
#
#   Sin grupo: legislators voting as independents or without a group are
#   scored against each bloc over the votes they cast while unaffiliated only.
#
#   Party unity on a roll call: the share of the party's votes cast that went
#   with its most common choice. 100% = unanimous, 50% = split down the middle.
#
#   No-votes: absences plus abstentions, as a share of the roll calls the
#   legislator was seated for. The Cámara records every absence. The Senado
#   records only excused ones ("Ausente", always "comisión oficial") and leaves
#   anyone else out of the roll call; those are reconstructed as "Sin registro"
#   with the website's rule (see `fill_no_registro_gaps` in
#   camara_de_senadores/escanos/seat_members.py): a roll call strictly between
#   a senator's own first and last recorded vote, unless another member of the
#   same seat voted in it. Gaps before a first or after a last vote are never
#   filled, so Senado absences are a floor. SENATE_GAP_RULE = "occupant"
#   (the default) also gives each gap to only one member of the seat.
#
# Required packages:
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyr", "ggplot2", "scales", "cluster"))

required_packages <- c("DBI", "RSQLite", "dplyr", "tidyr", "ggplot2", "scales", "cluster")
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

LEGISLATURE <- 66L
# Benches with fewer distinct legislators get no bloc and no column of their
# own. Their members are still scored.
MIN_PARTY_MEMBERS <- 5L
# Labels for having no bench. They never take part in bloc building, however
# many legislators carry them: the Senado has six senators sin grupo, and
# clustering them as if they voted together would invent a party.
NON_PARTIES <- c("IND", "SG", "SP")
# Charts only: suplentes who cast a handful of votes sit at 0% or 100% and
# would dominate the extremes. The tables keep everyone.
MIN_CHART_VOTES <- 20L
# A party roll call below this unity counts as split in the unity bands.
SPLIT_THRESHOLD <- 0.9
# How unrecorded Senado absences are attributed (see "No-votes" above):
# "occupant" gives a seat's gap only to whoever last voted from that seat;
# "website" reproduces the site's Sin registro counts exactly.
SENATE_GAP_RULE <- "occupant"
# NA picks the number of blocs by silhouette; set an integer to force one.
BLOC_K <- c(diputados = NA_integer_, senado = NA_integer_)

# Same aliases as lib/canonical.py, so benches read as they do on the site.
PARTY_ALIASES <- c(MRN = "MORENA", CAND_INDEPENDIENTE = "IND", "SIN GRUPO" = "SG")
# Same palette as web/app/visualizaciones/parties.ts. MORENA, PT and PRI are
# close reds, so no chart below identifies a party by colour alone.
PARTY_COLORS <- c(
  PT = "#c7323f", MORENA = "#8e2533", PVEM = "#3b8b62", MC = "#e97935",
  PRI = "#d55d75", PAN = "#2d69a4", PRD = "#e5ad31", IND = "#7c7f82",
  SG = "#7c7f82", SP = "#7c7f82"
)
# Seating order, left to right, as in parties.ts.
PARTY_ORDER <- c("PT", "MORENA", "PVEM", "MC", "PRI", "PAN")
CHOICE_CODES <- c(Favor = "favor", Contra = "contra", "Abstención" = "abstencion")

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

# One row per legislator x roll call, both chambers in one shape. The Cámara
# keeps only roll calls whose official party totals reconcile with the
# per-deputy rows, the same quality rule as gaceta_party_correlations.R.
votes <- dbGetQuery(con, "
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
    'diputados' AS chamber,
    f.gaceta_vote_id AS vote_id,
    v.vote_date,
    f.deputy_id AS legislator_id,
    d.deputy_name AS legislator_name,
    f.party_key AS party,
    CASE
      WHEN f.vote_choice IN ('Favor', 'A favor') THEN 'Favor'
      WHEN f.vote_choice IN ('Contra', 'En contra') THEN 'Contra'
      WHEN f.vote_choice IN ('Abstención', 'Abstencion') THEN 'Abstención'
      WHEN f.vote_choice = 'Ausente' THEN 'Ausente'
    END AS choice
  FROM fact_gaceta_deputy_vote AS f
  JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
  JOIN dim_gaceta_deputy AS d ON d.deputy_id = f.deputy_id
  JOIN summary_party_totals AS s ON s.gaceta_vote_id = f.gaceta_vote_id
  JOIN detail_totals AS t ON t.gaceta_vote_id = f.gaceta_vote_id
  WHERE v.legislature = ?
    AND s.summary_party_total = t.detail_rows

  UNION ALL

  SELECT
    'senado' AS chamber,
    CAST(f.votacion_id AS TEXT) AS vote_id,
    v.vote_date,
    CAST(f.senador_id AS TEXT) AS legislator_id,
    s.senador_name AS legislator_name,
    COALESCE(NULLIF(TRIM(f.grupo_parlamentario), ''), 'SG') AS party,
    CASE f.voto
      WHEN 'PRO' THEN 'Favor'
      WHEN 'CONTRA' THEN 'Contra'
      WHEN 'ABSTENCIÓN' THEN 'Abstención'
      WHEN 'AUSENTE' THEN 'Ausente'
    END AS choice
  FROM fact_senador_vote AS f
  JOIN dim_senado_vote AS v ON v.votacion_id = f.votacion_id
  LEFT JOIN dim_senador AS s ON s.senador_id = f.senador_id
  WHERE v.legislature = ?
    AND f.voto IS NOT NULL
", params = list(LEGISLATURE, LEGISLATURE)) %>%
  mutate(
    vote_date = as.Date(vote_date),
    party = toupper(party),
    party = coalesce(unname(PARTY_ALIASES[party]), party),
    legislator_name = sub("^Sen\\. ", "", legislator_name)
  )

if (anyNA(votes$choice)) {
  stop("Unmapped vote choices: add them to the CASE expressions in the query.")
}

# Roll-call identities merged into one person, the same table the website
# reads (a deputy spelled two ways across roll calls, never overlapping).
aliases <- dbGetQuery(con, "
  SELECT
    CASE chamber WHEN 'DIP' THEN 'diputados' ELSE 'senado' END AS chamber,
    person_id AS legislator_id,
    canonical_person_id AS canonical_id
  FROM fact_legislature_66_person_alias
")
votes <- votes %>%
  left_join(aliases, by = c("chamber", "legislator_id")) %>%
  mutate(legislator_id = coalesce(canonical_id, legislator_id)) %>%
  select(-canonical_id)

# Senado "Sin registro": rebuild the absences the source omits.
senate_order <- votes %>%
  filter(chamber == "senado") %>%
  distinct(vote_id, vote_date) %>%
  arrange(vote_date, as.integer(vote_id)) %>%
  mutate(position = row_number())
senate_recorded <- votes %>%
  filter(chamber == "senado") %>%
  inner_join(senate_order, by = c("vote_id", "vote_date"))
senate_seats <- dbGetQuery(con, "
  SELECT person_id AS legislator_id, seat_id
  FROM fact_legislature_66_seat_member
  WHERE chamber = 'SEN'
")
# A seat counts as present in a roll call if any of its members voted in it.
seat_claims <- senate_recorded %>%
  inner_join(senate_seats, by = "legislator_id") %>%
  distinct(seat_id, vote_id)
senate_gaps <- senate_recorded %>%
  group_by(legislator_id) %>%
  summarise(first = min(position), last = max(position), .groups = "drop") %>%
  inner_join(senate_order, by = join_by(first < position, last > position)) %>%
  anti_join(senate_recorded, by = c("legislator_id", "vote_id")) %>%
  left_join(senate_seats, by = "legislator_id") %>%
  anti_join(seat_claims, by = c("seat_id", "vote_id"))
if (SENATE_GAP_RULE == "occupant") {
  # Give a seat's gap only to the member who last voted from that seat before
  # it. The website rule gives it to every member whose span covers it, so a
  # suplente with two stints is absent through the titular's whole term in
  # between, and the seat's absence is counted twice.
  seat_holders <- senate_recorded %>%
    inner_join(senate_seats, by = "legislator_id") %>%
    select(seat_id, holder = legislator_id, held_at = position)
  occupied <- senate_gaps %>%
    filter(!is.na(seat_id)) %>%
    inner_join(seat_holders, by = join_by(seat_id, closest(position > held_at))) %>%
    filter(holder == legislator_id) %>%
    distinct(legislator_id, vote_id)
  senate_gaps <- senate_gaps %>%
    filter(is.na(seat_id)) %>%
    bind_rows(semi_join(senate_gaps, occupied, by = c("legislator_id", "vote_id")))
}
senate_gaps <- select(senate_gaps, legislator_id, vote_id, vote_date, position)
# Name and bench come from the senator's previous recorded vote, which always
# exists because only gaps inside their own span are filled.
no_record <- bind_rows(
  select(senate_recorded, legislator_id, vote_id, vote_date, position, legislator_name, party),
  mutate(senate_gaps, filled = TRUE)
) %>%
  arrange(legislator_id, position) %>%
  group_by(legislator_id) %>%
  fill(legislator_name, party, .direction = "down") %>%
  ungroup() %>%
  filter(filled %in% TRUE) %>%
  transmute(chamber = "senado", vote_id, vote_date, legislator_id, legislator_name,
            party, choice = "Sin registro")
votes <- bind_rows(votes, no_record)

# Votes actually cast. Absences only count toward the participation columns.
cast <- votes %>%
  filter(choice %in% names(CHOICE_CODES)) %>%
  mutate(choice = unname(CHOICE_CODES[choice]))

majority_position <- function(favor, contra, abstencion) {
  top <- pmax(favor, contra, abstencion)
  tied <- (favor == top) + (contra == top) + (abstencion == top)
  case_when(
    top == 0 | tied > 1 ~ NA_character_,
    favor == top ~ "favor",
    contra == top ~ "contra",
    TRUE ~ "abstencion"
  )
}

# Favor / Contra / Abstención counts for each group on each roll call.
group_counts <- function(cast, member_col) {
  counts <- cast %>%
    filter(!is.na(.data[[member_col]])) %>%
    count(chamber, vote_id, group = .data[[member_col]], choice) %>%
    pivot_wider(names_from = choice, values_from = n, values_fill = 0L)
  for (code in CHOICE_CODES) {
    if (!code %in% names(counts)) counts[[code]] <- 0L
  }
  counts
}

# Every cast vote against every group's leave-one-out position.
match_votes <- function(cast, member_col) {
  cast %>%
    transmute(chamber, vote_id, legislator_id, party, choice,
              own_group = .data[[member_col]]) %>%
    inner_join(group_counts(cast, member_col), by = c("chamber", "vote_id"),
               relationship = "many-to-many") %>%
    mutate(
      is_member = !is.na(own_group) & own_group == group,
      favor = favor - (is_member & choice == "favor"),
      contra = contra - (is_member & choice == "contra"),
      abstencion = abstencion - (is_member & choice == "abstencion"),
      position = majority_position(favor, contra, abstencion)
    ) %>%
    filter(!is.na(position)) %>%
    mutate(agrees = choice == position)
}

summarise_agreement <- function(matched, ...) {
  matched %>%
    group_by(chamber, legislator_id, ...) %>%
    summarise(roll_calls = n(), agreed = sum(agrees), .groups = "drop") %>%
    mutate(agreement = agreed / roll_calls)
}

# --- Data-driven blocs ------------------------------------------------------

party_members <- votes %>%
  distinct(chamber, legislator_id, party) %>%
  count(chamber, party, name = "members")
major_parties <- party_members %>%
  filter(members >= MIN_PARTY_MEMBERS, !party %in% NON_PARTIES)

# Each major party's position on each roll call, everyone counted.
party_positions <- group_counts(cast, "party") %>%
  semi_join(major_parties, by = c("chamber", "group" = "party")) %>%
  mutate(position = majority_position(favor, contra, abstencion)) %>%
  filter(!is.na(position)) %>%
  select(chamber, vote_id, party = group, position)

# Share of roll calls where two parties took the same position. The diagonal
# is 1 by construction.
party_pair_agreement <- party_positions %>%
  rename(party_a = party, position_a = position) %>%
  inner_join(party_positions %>% rename(party_b = party, position_b = position),
             by = c("chamber", "vote_id"), relationship = "many-to-many") %>%
  group_by(chamber, party_a, party_b) %>%
  summarise(roll_calls = n(), agreement = mean(position_a == position_b),
            .groups = "drop")

find_blocs <- function(pairs, k_override) {
  wide <- pairs %>%
    select(party_a, party_b, agreement) %>%
    pivot_wider(names_from = party_b, values_from = agreement)
  agreement <- as.matrix(wide[, -1])
  rownames(agreement) <- wide$party_a
  agreement <- agreement[wide$party_a, wide$party_a]
  if (nrow(agreement) < 3) stop("Need at least three major parties to find blocs.")

  distance <- as.dist(1 - agreement)
  tree <- hclust(distance, method = "average")
  ks <- 2:(nrow(agreement) - 1)
  silhouette <- tibble(
    k = ks,
    mean_silhouette = vapply(ks, function(k) {
      mean(cluster::silhouette(cutree(tree, k), distance)[, "sil_width"])
    }, numeric(1))
  )
  k <- if (is.na(k_override)) silhouette$k[which.max(silhouette$mean_silhouette)] else k_override
  list(tree = tree, k = k, silhouette = silhouette,
       membership = tibble(party = names(cutree(tree, k)), cluster = unname(cutree(tree, k))))
}

chambers <- intersect(c("diputados", "senado"), unique(votes$chamber))
bloc_models <- setNames(lapply(chambers, function(ch) {
  find_blocs(filter(party_pair_agreement, chamber == ch), BLOC_K[[ch]])
}), chambers)

bloc_silhouette <- bind_rows(lapply(chambers, function(ch) {
  mutate(bloc_models[[ch]]$silhouette, chamber = ch, chosen = k == bloc_models[[ch]]$k)
})) %>% relocate(chamber)

# Blocs are numbered by size and named after their members, largest first.
party_blocs <- bind_rows(lapply(chambers, function(ch) {
  mutate(bloc_models[[ch]]$membership, chamber = ch)
})) %>%
  inner_join(major_parties, by = c("chamber", "party")) %>%
  group_by(chamber, cluster) %>%
  mutate(bloc_size = sum(members),
         bloc_label = paste(party[order(-members)], collapse = " + ")) %>%
  group_by(chamber) %>%
  mutate(bloc = paste("Bloc", dense_rank(-bloc_size))) %>%
  ungroup() %>%
  select(chamber, bloc, bloc_label, party, members) %>%
  arrange(chamber, bloc, desc(members))

cast <- cast %>%
  left_join(select(party_blocs, chamber, party, bloc), by = c("chamber", "party"))

# --- Legislator scores ------------------------------------------------------

party_matches <- match_votes(cast, "party") %>%
  semi_join(major_parties, by = c("chamber", "group" = "party"))
bloc_matches <- match_votes(cast, "bloc")

# Long: one row per legislator x party or bloc.
legislator_agreement <- bind_rows(
  summarise_agreement(party_matches, group) %>% mutate(group_type = "party"),
  summarise_agreement(bloc_matches, group) %>% mutate(group_type = "bloc")
) %>%
  relocate(group_type, .before = group)

legislators <- votes %>%
  arrange(vote_date, vote_id) %>%
  group_by(chamber, legislator_id) %>%
  summarise(
    # Before `party` is overwritten: summarise sees the new value from here on.
    benches = n_distinct(party),
    legislator_name = last(legislator_name),
    party = last(party),
    roll_calls = n(),
    votes_cast = sum(choice %in% names(CHOICE_CODES)),
    absent = sum(choice == "Ausente"),
    no_record = sum(choice == "Sin registro"),
    abstained = sum(choice == "Abstención"),
    .groups = "drop"
  ) %>%
  mutate(absence_rate = (absent + no_record) / roll_calls,
         no_vote_rate = (absent + no_record + abstained) / roll_calls) %>%
  left_join(select(party_blocs, chamber, party, bloc), by = c("chamber", "party"))

# Wide: one row per legislator. `own_party` / `own_bloc` use the bench of each
# vote; `with_<party>` and `with_Bloc N` are against every major party and bloc
# of the chamber (see `party_blocs` for what each bloc number holds).
legislator_scores <- legislators %>%
  left_join(summarise_agreement(filter(party_matches, is_member)) %>%
              select(chamber, legislator_id, own_party = agreement),
            by = c("chamber", "legislator_id")) %>%
  left_join(summarise_agreement(filter(bloc_matches, is_member)) %>%
              select(chamber, legislator_id, own_bloc = agreement),
            by = c("chamber", "legislator_id")) %>%
  left_join(legislator_agreement %>%
              select(chamber, legislator_id, group, agreement) %>%
              pivot_wider(names_from = group, values_from = agreement,
                          names_prefix = "with_"),
            by = c("chamber", "legislator_id")) %>%
  arrange(chamber, party, own_party)

# --- Friction and absences --------------------------------------------------

positions_of <- function(member_col) {
  group_counts(cast, member_col) %>%
    mutate(position = majority_position(favor, contra, abstencion)) %>%
    filter(!is.na(position)) %>%
    select(chamber, vote_id, group, position)
}

# Roll calls with friction: the blocs' majorities took different positions.
# Near-unanimous votes make every pair of parties agree; these are the votes
# where blocs and defections show. (A party breaking from its own bloc while
# the blocs agree does not count: 14 Cámara and 40 Senado roll calls.)
contested_votes <- positions_of("bloc") %>%
  group_by(chamber, vote_id) %>%
  filter(n_distinct(position) > 1) %>%
  ungroup() %>%
  distinct(chamber, vote_id)

party_pair_agreement_contested <- party_positions %>%
  semi_join(contested_votes, by = c("chamber", "vote_id")) %>%
  rename(party_a = party, position_a = position) %>%
  inner_join(party_positions %>% rename(party_b = party, position_b = position),
             by = c("chamber", "vote_id"), relationship = "many-to-many") %>%
  group_by(chamber, party_a, party_b) %>%
  summarise(roll_calls = n(), agreement = mean(position_a == position_b),
            .groups = "drop")

# Absences that count against agreement: every Cámara absence, and in the
# Senado only the reconstructed "Sin registro" (its recorded "Ausente" is
# always an excused official commission).
counted_absences <- votes %>%
  filter((chamber == "diputados" & choice == "Ausente") |
           (chamber == "senado" & choice == "Sin registro"))

# An absence never matches. It counts on every roll call where the bloc had a
# position, and against the legislator's own bench when it is a major party.
absent_matches <- bind_rows(
  counted_absences %>%
    inner_join(positions_of("bloc"), by = c("chamber", "vote_id"),
               relationship = "many-to-many") %>%
    mutate(group_type = "bloc"),
  counted_absences %>%
    inner_join(positions_of("party"), by = c("chamber", "vote_id"),
               relationship = "many-to-many") %>%
    filter(group == party) %>%
    semi_join(major_parties, by = c("chamber", "party")) %>%
    mutate(group_type = "own")
) %>%
  transmute(chamber, vote_id, legislator_id, group_type, group, agrees = FALSE)

cast_matches <- bind_rows(
  bloc_matches %>% mutate(group_type = "bloc"),
  party_matches %>% filter(is_member) %>% mutate(group_type = "own")
) %>%
  select(chamber, vote_id, legislator_id, group_type, group, agrees)

# One row per legislator x mode x bloc (or own bench). Modes:
#   all-cast          every roll call, votes cast only (= legislator_scores)
#   contested-cast    roll calls with friction only
#   all-seated        absences count as not agreeing
#   contested-seated  both
seated_matches <- bind_rows(cast_matches, absent_matches)
legislator_mode_scores <- bind_rows(
  mutate(cast_matches, mode = "all-cast"),
  semi_join(cast_matches, contested_votes, by = c("chamber", "vote_id")) %>%
    mutate(mode = "contested-cast"),
  mutate(seated_matches, mode = "all-seated"),
  semi_join(seated_matches, contested_votes, by = c("chamber", "vote_id")) %>%
    mutate(mode = "contested-seated")
) %>%
  mutate(group = if_else(group_type == "own", "own_party", group)) %>%
  group_by(chamber, legislator_id, mode, group) %>%
  summarise(roll_calls = n(), agreed = sum(agrees), .groups = "drop") %>%
  mutate(agreement = agreed / roll_calls)

# --- Sin grupo --------------------------------------------------------------

# Agreement over the votes cast while unaffiliated, one row per legislator x
# label x party or bloc. A senator who was sin grupo and then joined a bench
# is scored here on the sin-grupo stretch only.
no_group_agreement <- bind_rows(
  party_matches %>% mutate(group_type = "party"),
  bloc_matches %>% mutate(group_type = "bloc")
) %>%
  filter(party %in% NON_PARTIES) %>%
  group_by(chamber, legislator_id, label = party, group_type, group) %>%
  summarise(roll_calls = n(), agreed = sum(agrees), .groups = "drop") %>%
  mutate(agreement = agreed / roll_calls) %>%
  left_join(select(legislators, chamber, legislator_id, legislator_name,
                   latest_party = party),
            by = c("chamber", "legislator_id")) %>%
  relocate(legislator_name, .after = legislator_id)

# One row per unaffiliated stretch: agreement with each bloc and the lean.
no_group_lean <- no_group_agreement %>%
  filter(group_type == "bloc") %>%
  select(chamber, legislator_id, legislator_name, label, latest_party, roll_calls, group, agreement) %>%
  group_by(chamber, legislator_id, label) %>%
  mutate(roll_calls = max(roll_calls)) %>%
  ungroup() %>%
  pivot_wider(names_from = group, values_from = agreement, names_prefix = "with_") %>%
  mutate(lean = `with_Bloc 1` - `with_Bloc 2`) %>%
  arrange(chamber, desc(roll_calls))

# --- Party unity ------------------------------------------------------------

vote_dates <- distinct(votes, chamber, vote_id, vote_date)

# One row per major party x roll call.
party_unity <- group_counts(cast, "party") %>%
  semi_join(major_parties, by = c("chamber", "group" = "party")) %>%
  rename(party = group) %>%
  mutate(voting = favor + contra + abstencion,
         unity = pmax(favor, contra, abstencion) / voting) %>%
  left_join(vote_dates, by = c("chamber", "vote_id")) %>%
  mutate(month = as.Date(format(vote_date, "%Y-%m-01")))

summarise_unity <- function(data) {
  summarise(data,
            roll_calls = n(),
            mean_unity = mean(unity),
            # Every vote weighs the same, so large benches weigh more here.
            pooled_unity = sum(pmax(favor, contra, abstencion)) / sum(voting),
            unanimous_share = mean(unity == 1),
            split_share = mean(unity < SPLIT_THRESHOLD),
            .groups = "drop")
}

party_unity_total <- party_unity %>%
  group_by(chamber, party) %>%
  summarise_unity() %>%
  arrange(chamber, mean_unity)

party_unity_monthly <- party_unity %>%
  group_by(chamber, party, month) %>%
  summarise_unity() %>%
  arrange(chamber, party, month)

# --- No-votes ---------------------------------------------------------------

summarise_no_votes <- function(data) {
  summarise(data,
            seats = n(),
            absent = sum(choice == "Ausente"),
            no_record = sum(choice == "Sin registro"),
            abstained = sum(choice == "Abstención"),
            .groups = "drop") %>%
    mutate(absence_rate = (absent + no_record) / seats,
           no_vote_rate = (absent + no_record + abstained) / seats)
}

# `seats` counts legislator x roll call slots, under the bench of the time.
no_votes_total <- votes %>%
  group_by(chamber, party) %>%
  summarise_no_votes() %>%
  arrange(chamber, desc(no_vote_rate))

no_votes_monthly <- votes %>%
  mutate(month = as.Date(format(vote_date, "%Y-%m-01"))) %>%
  group_by(chamber, party, month) %>%
  summarise_no_votes() %>%
  left_join(count(distinct(votes, chamber, vote_id, vote_date) %>%
                    mutate(month = as.Date(format(vote_date, "%Y-%m-01"))),
                  chamber, month, name = "roll_calls"),
            by = c("chamber", "month")) %>%
  arrange(chamber, party, month)

print(bloc_silhouette)
print(party_blocs, n = Inf)
print(no_group_lean, width = Inf)
print(party_unity_total, n = Inf)
print(no_votes_total, n = Inf)

# --- Charts -----------------------------------------------------------------

chamber_title <- c(diputados = "Cámara de Diputados", senado = "Senado")

chart_theme <- theme_minimal(base_size = 12) +
  theme(panel.grid.minor = element_blank(),
        plot.title.position = "plot",
        plot.subtitle = element_text(colour = "grey35"))

# Segments of an hclust tree: x is the leaf position (1..n, in tree order),
# y the merge height. Each merge is two verticals and the bar joining them.
dendrogram_segments <- function(tree) {
  n <- length(tree$labels)
  leaf_x <- match(seq_len(n), tree$order)
  node_x <- numeric(n - 1)
  node_at <- function(i) if (i < 0) c(leaf_x[-i], 0) else c(node_x[i], tree$height[i])
  bind_rows(lapply(seq_len(n - 1), function(i) {
    a <- node_at(tree$merge[i, 1])
    b <- node_at(tree$merge[i, 2])
    h <- tree$height[i]
    node_x[i] <<- (a[1] + b[1]) / 2
    tibble(x = c(a[1], b[1], a[1]), xend = c(a[1], b[1], b[1]),
           y = c(a[2], b[2], h), yend = c(h, h, h))
  }))
}

# Party x party agreement as a clustered heatmap: the bloc tree runs along the
# top and the left, boxes mark the blocs and the dashed line is where the tree
# was cut into them. Everything shares one coordinate system (tile = 1 unit),
# so no layout package is needed.
plot_party_agreement <- function(ch) {
  model <- bloc_models[[ch]]
  tree <- model$tree
  order <- tree$labels[tree$order]
  n <- length(order)
  gap <- 0.15
  tree_depth <- 1.6
  scale_h <- tree_depth / max(tree$height)
  top_base <- n + 0.5 + gap
  left_base <- 0.5 - gap
  heights <- sort(tree$height)
  cut_h <- mean(heights[c(n - model$k, n - model$k + 1)])

  tiles <- party_pair_agreement %>%
    filter(chamber == ch) %>%
    mutate(x = match(party_a, order), y = n + 1 - match(party_b, order))
  midpoint <- mean(range(tiles$agreement))
  segments <- dendrogram_segments(tree)
  top_tree <- segments %>%
    transmute(x, xend, y = top_base + y * scale_h, yend = top_base + yend * scale_h)
  # Rotated: height runs leftwards, leaf position runs down.
  left_tree <- segments %>%
    transmute(x_new = left_base - y * scale_h, xend_new = left_base - yend * scale_h,
              y = n + 1 - x, yend = n + 1 - xend) %>%
    rename(x = x_new, xend = xend_new)
  bloc_boxes <- tibble(party = names(cutree(tree, model$k)), cluster = cutree(tree, model$k)) %>%
    mutate(pos = match(party, order)) %>%
    group_by(cluster) %>%
    summarise(lo = min(pos), hi = max(pos), .groups = "drop")

  ggplot() +
    geom_tile(data = tiles, aes(x, y, fill = agreement), colour = "white", linewidth = 1) +
    geom_text(data = tiles, aes(x, y, label = percent(agreement, accuracy = 1),
                                colour = agreement > midpoint), size = 3.2) +
    geom_rect(data = bloc_boxes,
              aes(xmin = lo - 0.5, xmax = hi + 0.5, ymin = n + 0.5 - hi, ymax = n + 1.5 - lo),
              fill = NA, colour = "grey15", linewidth = 0.7) +
    geom_segment(data = top_tree, aes(x, y, xend = xend, yend = yend),
                 colour = "grey30", linewidth = 0.5, lineend = "round") +
    geom_segment(data = left_tree, aes(x, y, xend = xend, yend = yend),
                 colour = "grey30", linewidth = 0.5, lineend = "round") +
    annotate("segment", x = 0.5, xend = n + 0.5,
             y = top_base + cut_h * scale_h, yend = top_base + cut_h * scale_h,
             colour = "grey55", linetype = "dashed", linewidth = 0.4) +
    annotate("segment", y = 0.5, yend = n + 0.5,
             x = left_base - cut_h * scale_h, xend = left_base - cut_h * scale_h,
             colour = "grey55", linetype = "dashed", linewidth = 0.4) +
    scale_fill_gradient(low = "#eef3f8", high = "#1f4e79", labels = percent) +
    scale_colour_manual(values = c(`TRUE` = "white", `FALSE` = "grey15"), guide = "none") +
    scale_x_continuous(breaks = seq_len(n), labels = order) +
    scale_y_continuous(breaks = rev(seq_len(n)), labels = order, position = "right") +
    coord_equal(xlim = c(left_base - tree_depth - 0.05, n + 0.5),
                ylim = c(0.5, top_base + tree_depth + 0.05),
                expand = FALSE, clip = "off") +
    labs(title = paste("How often parties take the same position:", chamber_title[[ch]]),
         subtitle = paste0(
           "Share of roll calls where both parties' majority vote matched; absences excluded\n",
           "Trees join parties that vote alike lower down; cut at the dashed line into ",
           model$k, " blocs (", ifelse(is.na(BLOC_K[[ch]]), "best silhouette", "set by BLOC_K"), ")"
         ),
         x = NULL, y = NULL, fill = "Agreement") +
    chart_theme +
    theme(panel.grid = element_blank(),
          axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1),
          legend.position = "bottom",
          legend.key.width = unit(1.6, "cm"),
          legend.key.height = unit(0.3, "cm"))
}

chart_legislators <- legislator_scores %>%
  filter(votes_cast >= MIN_CHART_VOTES)

# Every legislator's agreement with every party and bloc, one panel per bench.
plot_agreement_strips <- function(ch) {
  blocs <- party_blocs %>% filter(chamber == ch) %>% distinct(bloc, bloc_label)
  parties <- bloc_models[[ch]]$tree$labels[bloc_models[[ch]]$tree$order]
  targets <- c(parties, blocs$bloc)
  data <- legislator_agreement %>%
    filter(chamber == ch) %>%
    inner_join(chart_legislators %>% filter(party %in% parties) %>%
                 select(chamber, legislator_id, party),
               by = c("chamber", "legislator_id")) %>%
    mutate(group = factor(group, levels = targets),
           party = factor(party, levels = parties))
  ggplot(data, aes(group, agreement)) +
    geom_jitter(aes(colour = party), width = 0.22, height = 0, size = 1.4, alpha = 0.55) +
    stat_summary(fun = median, geom = "crossbar", width = 0.6, linewidth = 0.3,
                 colour = "grey10") +
    facet_grid(party ~ ., switch = "y") +
    scale_y_continuous(labels = percent, breaks = c(0, 0.5, 1)) +
    scale_colour_manual(values = PARTY_COLORS, guide = "none") +
    labs(title = paste("Agreement with each party and bloc:", chamber_title[[ch]]),
         subtitle = paste0("One dot per legislator (", MIN_CHART_VOTES,
                           "+ votes cast), panel = their latest bench, bar = median\n",
                           paste(blocs$bloc, blocs$bloc_label, sep = ": ", collapse = "   ")),
         x = NULL, y = NULL) +
    chart_theme +
    theme(strip.placement = "outside", strip.text.y.left = element_text(angle = 0, face = "bold"))
}

# Agreement with the two largest blocs, highlighting one bench per panel.
plot_bloc_map <- function(ch) {
  blocs <- party_blocs %>% filter(chamber == ch) %>% distinct(bloc, bloc_label) %>%
    arrange(bloc) %>% head(2)
  x_col <- paste0("with_", blocs$bloc[1])
  y_col <- paste0("with_", blocs$bloc[2])
  data <- chart_legislators %>%
    filter(chamber == ch, party %in% major_parties$party[major_parties$chamber == ch]) %>%
    transmute(party, x = .data[[x_col]], y = .data[[y_col]])
  ggplot(data, aes(x, y)) +
    geom_point(data = select(data, -party), colour = "grey82", size = 1.2) +
    geom_point(aes(colour = party), size = 1.8, alpha = 0.8) +
    facet_wrap(~ party) +
    scale_x_continuous(labels = percent, limits = c(0, 1)) +
    scale_y_continuous(labels = percent, limits = c(0, 1)) +
    scale_colour_manual(values = PARTY_COLORS, guide = "none") +
    coord_equal() +
    labs(title = paste("Where each legislator sits between the blocs:", chamber_title[[ch]]),
         subtitle = "Grey: every other legislator in the chamber",
         x = paste("Agreement with", blocs$bloc_label[1]),
         y = paste("Agreement with", blocs$bloc_label[2])) +
    chart_theme
}

# The legislators who most often voted against their own bench.
plot_least_aligned <- function(ch, n = 20) {
  data <- chart_legislators %>%
    filter(chamber == ch, !is.na(own_party)) %>%
    slice_min(own_party, n = n, with_ties = FALSE) %>%
    mutate(label = paste0(legislator_name, " (", party, ")"),
           label = reorder(label, -own_party))
  ggplot(data, aes(own_party, label)) +
    geom_segment(aes(x = 1, xend = own_party, yend = label), colour = "grey80", linewidth = 0.6) +
    geom_point(aes(colour = party), size = 2.6) +
    geom_text(aes(label = percent(own_party, accuracy = 0.1)), hjust = 1.35,
              size = 3.2, colour = "grey25") +
    scale_x_continuous(labels = percent,
                       limits = c(min(data$own_party) - 0.25 * (1 - min(data$own_party)), 1)) +
    scale_colour_manual(values = PARTY_COLORS, guide = "none") +
    labs(title = paste("Least aligned with their own bench:", chamber_title[[ch]]),
         subtitle = paste0("Share of votes matching the rest of their bench; ",
                           MIN_CHART_VOTES, "+ votes cast"),
         x = NULL, y = NULL) +
    chart_theme +
    theme(panel.grid.major.y = element_blank())
}

# Sin grupo: each unaffiliated stretch against the two blocs, as a dumbbell.
plot_no_group <- function(min_votes = 5) {
  blocs <- distinct(party_blocs, chamber, bloc, bloc_label)
  data <- no_group_agreement %>%
    filter(group_type == "bloc") %>%
    group_by(chamber, legislator_id, label) %>%
    mutate(stint_votes = max(roll_calls)) %>%
    ungroup() %>%
    filter(stint_votes >= min_votes) %>%
    left_join(blocs, by = c("chamber", "group" = "bloc")) %>%
    left_join(select(no_group_lean, chamber, legislator_id, label, lean),
              by = c("chamber", "legislator_id", "label")) %>%
    mutate(row = paste0(legislator_name, "\n", label, ", ", chamber_title[chamber],
                        ", ", stint_votes, " votes"),
           row = reorder(row, lean))
  # A bloc wears the colour of its largest party.
  bloc_colours <- distinct(data, bloc_label) %>%
    mutate(colour = PARTY_COLORS[sub(" .*", "", bloc_label)])
  ggplot(data, aes(agreement, row)) +
    geom_line(aes(group = row), colour = "grey80", linewidth = 1.2) +
    geom_point(aes(colour = bloc_label), size = 3.2) +
    geom_text(aes(label = percent(agreement, accuracy = 1), colour = bloc_label),
              vjust = -1.1, size = 3.2, show.legend = FALSE) +
    scale_x_continuous(labels = percent, limits = c(0, 1)) +
    scale_colour_manual(values = setNames(bloc_colours$colour, bloc_colours$bloc_label)) +
    labs(title = "Which bloc do legislators without a group vote with?",
         subtitle = paste0("Agreement with each bloc's majority, over votes cast while unaffiliated (",
                           min_votes, "+ votes); sorted by lean"),
         x = NULL, y = NULL, colour = NULL) +
    chart_theme +
    theme(legend.position = "top", legend.justification = "left",
          panel.grid.major.y = element_blank())
}

# Party unity by month, one panel per party and chamber.
plot_unity_monthly <- function() {
  data <- party_unity_monthly %>%
    filter(party %in% PARTY_ORDER) %>%
    mutate(party = factor(party, levels = PARTY_ORDER),
           chamber = factor(chamber_title[chamber], levels = chamber_title))
  ggplot(data, aes(month, mean_unity)) +
    geom_line(aes(colour = party), linewidth = 0.6) +
    geom_point(aes(colour = party, size = roll_calls)) +
    facet_grid(party ~ chamber, switch = "y") +
    scale_x_date(date_labels = "%b %Y", date_breaks = "6 months") +
    scale_y_continuous(labels = percent) +
    scale_size_area(max_size = 3, name = "Roll calls in month") +
    scale_colour_manual(values = PARTY_COLORS, guide = "none") +
    labs(title = "How united each party votes, month by month",
         subtitle = "Average share of the party's votes cast that went with its majority choice; lines bridge months without sessions",
         x = NULL, y = NULL) +
    chart_theme +
    theme(strip.placement = "outside", strip.text.y.left = element_text(angle = 0, face = "bold"),
          legend.position = "bottom")
}

# How often each party is unanimous, mostly united or split, over the legislature.
plot_unity_bands <- function() {
  bands <- c(paste0("Split (under ", percent(SPLIT_THRESHOLD), ")"),
             paste0("Mostly united (", percent(SPLIT_THRESHOLD), "-99%)"),
             "Unanimous")
  data <- party_unity %>%
    filter(party %in% PARTY_ORDER) %>%
    mutate(band = case_when(unity == 1 ~ bands[3],
                            unity >= SPLIT_THRESHOLD ~ bands[2],
                            TRUE ~ bands[1])) %>%
    count(chamber, party, band) %>%
    group_by(chamber, party) %>%
    mutate(share = n / sum(n)) %>%
    ungroup() %>%
    mutate(band = factor(band, levels = bands),
           party = factor(party, levels = rev(PARTY_ORDER)),
           chamber = factor(chamber_title[chamber], levels = chamber_title))
  totals <- party_unity_total %>%
    filter(party %in% PARTY_ORDER) %>%
    mutate(party = factor(party, levels = rev(PARTY_ORDER)),
           chamber = factor(chamber_title[chamber], levels = chamber_title))
  ggplot(data, aes(share, party)) +
    geom_col(aes(fill = band), width = 0.7, colour = "white", linewidth = 0.5,
             position = position_stack(reverse = TRUE)) +
    geom_text(aes(label = ifelse(share >= 0.08, percent(share, accuracy = 1), ""),
                  colour = band),
              position = position_stack(vjust = 0.5, reverse = TRUE), size = 3.2,
              show.legend = FALSE) +
    geom_text(data = totals, aes(x = 1.02, label = paste("avg", percent(mean_unity, accuracy = 0.1))),
              hjust = 0, size = 3.2, colour = "grey25") +
    facet_wrap(~ chamber) +
    scale_x_continuous(labels = percent, limits = c(0, 1.2), breaks = c(0, 0.5, 1)) +
    scale_fill_manual(values = setNames(c("#86b6ef", "#3987e5", "#104281"), bands)) +
    scale_colour_manual(values = setNames(c("grey10", "white", "white"), bands)) +
    labs(title = "How often each party votes as one",
         subtitle = "Share of roll calls by the party's unity; avg = mean unity over the legislature",
         x = NULL, y = NULL, fill = NULL) +
    chart_theme +
    theme(legend.position = "top", legend.justification = "left",
          panel.grid.major.y = element_blank())
}

NO_VOTE_COLOURS <- c(Ausente = "#184f95", "Sin registro" = "#5598e7", "Abstención" = "#eb6834")

no_vote_parts <- function(data, denominator) {
  data %>%
    mutate(Ausente = absent / .data[[denominator]],
           `Sin registro` = no_record / .data[[denominator]],
           `Abstención` = abstained / .data[[denominator]]) %>%
    pivot_longer(c(Ausente, `Sin registro`, `Abstención`), names_to = "kind", values_to = "rate") %>%
    mutate(kind = factor(kind, levels = names(NO_VOTE_COLOURS)))
}

# Absences and abstentions by month, one panel per party and chamber.
plot_no_votes_monthly <- function() {
  data <- no_votes_monthly %>%
    filter(party %in% PARTY_ORDER) %>%
    no_vote_parts("seats") %>%
    mutate(party = factor(party, levels = PARTY_ORDER),
           chamber = factor(chamber_title[chamber], levels = chamber_title))
  ggplot(data, aes(month, rate, fill = kind)) +
    geom_col(position = position_stack(reverse = TRUE), width = 25) +
    facet_grid(party ~ chamber, switch = "y") +
    scale_x_date(date_labels = "%b %Y", date_breaks = "6 months") +
    scale_y_continuous(labels = percent) +
    scale_fill_manual(values = NO_VOTE_COLOURS) +
    labs(title = "Who doesn't vote: absences and abstentions by month",
         subtitle = paste("Share of each party's seats per roll call. Senado: Ausente = excused (comisión oficial);",
                          "Sin registro = left out of the roll call (inferred)"),
         x = NULL, y = NULL, fill = NULL) +
    chart_theme +
    theme(strip.placement = "outside", strip.text.y.left = element_text(angle = 0, face = "bold"),
          legend.position = "top", legend.justification = "left")
}

# The legislators who most often did not take a side.
plot_top_no_votes <- function(ch, n = 20) {
  top <- legislator_scores %>%
    filter(chamber == ch, roll_calls >= MIN_CHART_VOTES) %>%
    slice_max(no_vote_rate, n = n, with_ties = FALSE) %>%
    mutate(label = paste0(legislator_name, " (", party, ")"),
           label = reorder(label, no_vote_rate))
  data <- no_vote_parts(top, "roll_calls")
  ggplot(data, aes(rate, label)) +
    geom_col(aes(fill = kind), width = 0.7, position = position_stack(reverse = TRUE)) +
    geom_text(data = top, aes(no_vote_rate, label,
                              label = paste0(percent(no_vote_rate, accuracy = 1), " of ", roll_calls)),
              hjust = -0.1, size = 3.2, colour = "grey25") +
    scale_x_continuous(labels = percent, expand = expansion(mult = c(0, 0.15))) +
    scale_fill_manual(values = NO_VOTE_COLOURS, drop = FALSE) +
    labs(title = paste("Most often absent or abstaining:", chamber_title[[ch]]),
         subtitle = paste0("Share of the roll calls they were seated for; ", MIN_CHART_VOTES,
                           "+ roll calls"),
         x = NULL, y = NULL, fill = NULL) +
    chart_theme +
    theme(legend.position = "top", legend.justification = "left",
          panel.grid.major.y = element_blank())
}

charts <- list()
for (ch in chambers) {
  charts[[paste0(ch, "_party_agreement")]] <- plot_party_agreement(ch)
  charts[[paste0(ch, "_agreement_strips")]] <- plot_agreement_strips(ch)
  charts[[paste0(ch, "_bloc_map")]] <- plot_bloc_map(ch)
  charts[[paste0(ch, "_least_aligned")]] <- plot_least_aligned(ch)
  charts[[paste0(ch, "_top_no_votes")]] <- plot_top_no_votes(ch)
}
charts$no_group <- plot_no_group()
charts$unity_bands <- plot_unity_bands()
charts$unity_monthly <- plot_unity_monthly()
charts$no_votes_monthly <- plot_no_votes_monthly()

for (chart in charts) print(chart)

# write.csv(legislator_scores, file.path(root, "output", "legislator_party_agreement.csv"), row.names = FALSE)
# write.csv(legislator_agreement, file.path(root, "output", "legislator_party_agreement_long.csv"), row.names = FALSE)
# for (name in names(charts)) ggsave(file.path(root, "output", paste0(name, ".png")), charts[[name]], width = 10, height = 8, dpi = 150)

library(DBI)
library(RSQLite)
library(dplyr)
library(tidyr)
library(ggplot2)
library(scales)

con <- dbConnect(SQLite(), "/Users/efrenzagal/Documents/GitHub/current-affairs-mx-elections-pres-2024/election_data.db")

# Favor / Contra / Abstención counts per legislature x roll call x party.
# Only roll calls whose official party totals reconcile with the per-deputy
# rows, and only parties with 5+ deputies (independents and sin partido out).
# MRN is MORENA's Gaceta code; Convergencia became Movimiento Ciudadano in LXI.
party_votes <- dbGetQuery(con, "
  WITH summary_party_totals AS (
    SELECT gaceta_vote_id, SUM(count) AS summary_party_total
    FROM fact_gaceta_vote_summary
    WHERE vote_choice = 'Total' AND party_key <> 'Total'
    GROUP BY gaceta_vote_id
  ), detail_totals AS (
    SELECT gaceta_vote_id, COUNT(*) AS detail_rows
    FROM fact_gaceta_deputy_vote
    GROUP BY gaceta_vote_id
  ), labelled AS (
    SELECT
      v.legislature,
      v.vote_date,
      f.gaceta_vote_id,
      f.deputy_id,
      f.vote_choice,
      CASE f.party_key WHEN 'MRN' THEN 'MORENA' WHEN 'CONV' THEN 'MC' ELSE f.party_key END AS party
    FROM fact_gaceta_deputy_vote AS f
    JOIN dim_gaceta_vote AS v ON v.gaceta_vote_id = f.gaceta_vote_id
    JOIN summary_party_totals AS s ON s.gaceta_vote_id = f.gaceta_vote_id
    JOIN detail_totals AS d ON d.gaceta_vote_id = f.gaceta_vote_id
    WHERE s.summary_party_total = d.detail_rows
  ), major AS (
    SELECT legislature, party
    FROM labelled
    WHERE party NOT IN ('IND', 'SP', 'SG')
    GROUP BY legislature, party
    HAVING COUNT(DISTINCT deputy_id) >= 5
  )
  SELECT
    l.legislature,
    l.vote_date,
    l.gaceta_vote_id AS vote_id,
    l.party,
    SUM(l.vote_choice = 'Favor') AS favor,
    SUM(l.vote_choice = 'Contra') AS contra,
    SUM(l.vote_choice IN ('Abstención', 'Abstencion')) AS abstencion
  FROM labelled AS l
  JOIN major AS m ON m.legislature = l.legislature AND m.party = l.party
  GROUP BY l.legislature, l.vote_date, l.gaceta_vote_id, l.party
")

# A party's position is its most common vote; absences and quorum-only
# records don't count, and a tie leaves it without one.
party_positions <- party_votes %>%
  mutate(top = pmax(favor, contra, abstencion),
         tied = (favor == top) + (contra == top) + (abstencion == top),
         position = case_when(top == 0 | tied > 1 ~ NA_character_,
                              favor == top ~ "favor",
                              contra == top ~ "contra",
                              TRUE ~ "abstencion")) %>%
  filter(!is.na(position)) %>%
  select(legislature, vote_id, party, position)

party_pair_agreement <- party_positions %>%
  inner_join(party_positions, by = c("legislature", "vote_id"),
             suffix = c("_a", "_b"), relationship = "many-to-many") %>%
  group_by(legislature, party_a, party_b) %>%
  summarise(roll_calls = n(), agreement = mean(position_a == position_b), .groups = "drop")

legislature_info <- party_votes %>%
  group_by(legislature) %>%
  summarise(first_year = substr(min(vote_date, na.rm = TRUE), 1, 4),
            last_year = substr(max(vote_date, na.rm = TRUE), 1, 4),
            roll_calls = n_distinct(vote_id))

# Average-linkage tree on 1 - agreement; blocs cut where the silhouette is best.
cluster_parties <- function(pairs) {
  wide <- pairs %>%
    select(party_a, party_b, agreement) %>%
    pivot_wider(names_from = party_b, values_from = agreement)
  agreement <- as.matrix(wide[, -1])
  rownames(agreement) <- wide$party_a
  agreement <- agreement[wide$party_a, wide$party_a]
  distance <- as.dist(1 - agreement)
  tree <- hclust(distance, method = "average")
  ks <- 2:(nrow(agreement) - 1)
  silhouette <- vapply(ks, function(k) {
    mean(cluster::silhouette(cutree(tree, k), distance)[, "sil_width"])
  }, numeric(1))
  list(tree = tree, k = ks[which.max(silhouette)])
}

legislatures <- sort(unique(party_pair_agreement$legislature))
models <- setNames(lapply(legislatures, function(leg) {
  cluster_parties(filter(party_pair_agreement, legislature == leg))
}), legislatures)

# Segments of an hclust tree: x is the leaf position (1..n, in tree order),
# y the merge height.
dendrogram_segments <- function(tree) {
  n <- length(tree$labels)
  leaf_x <- match(seq_len(n), tree$order)
  node_x <- numeric(n - 1)
  node_at <- function(i) if (i < 0) c(leaf_x[-i], 0) else c(node_x[i], tree$height[i])
  bind_rows(lapply(seq_len(n - 1), function(i) {
    a <- node_at(tree$merge[i, 1])
    b <- node_at(tree$merge[i, 2])
    h <- tree$height[i]
    node_x[i] <<- (a[1] + b[1]) / 2
    tibble(x = c(a[1], b[1], a[1]), xend = c(a[1], b[1], b[1]),
           y = c(a[2], b[2], h), yend = c(h, h, h))
  }))
}

# One colour scale for every legislature, so shades compare across plots.
fill_limits <- c(floor(min(party_pair_agreement$agreement) * 20) / 20, 1)
text_switch <- mean(fill_limits)

plot_legislature <- function(leg) {
  model <- models[[as.character(leg)]]
  tree <- model$tree
  order <- tree$labels[tree$order]
  n <- length(order)
  info <- filter(legislature_info, legislature == leg)
  tree_base <- n + 0.5 + 0.15
  tree_depth <- 0.3 * n
  scale_h <- tree_depth / max(tree$height)
  heights <- sort(tree$height)
  cut_h <- mean(heights[c(n - model$k, n - model$k + 1)])
  
  tiles <- party_pair_agreement %>%
    filter(legislature == leg) %>%
    mutate(x = match(party_a, order), y = n + 1 - match(party_b, order))
  top_tree <- dendrogram_segments(tree) %>%
    mutate(y = tree_base + y * scale_h, yend = tree_base + yend * scale_h)
  bloc_boxes <- tibble(pos = match(names(cutree(tree, model$k)), order),
                       cluster = cutree(tree, model$k)) %>%
    group_by(cluster) %>%
    summarise(lo = min(pos), hi = max(pos), .groups = "drop")
  
  ggplot() +
    geom_tile(data = tiles, aes(x, y, fill = agreement), colour = "white", linewidth = 1) +
    geom_text(data = tiles, aes(x, y, label = percent(agreement, accuracy = 1),
                                colour = agreement > text_switch), size = 3) +
    geom_rect(data = bloc_boxes,
              aes(xmin = lo - 0.5, xmax = hi + 0.5, ymin = n + 0.5 - hi, ymax = n + 1.5 - lo),
              fill = NA, colour = "grey15", linewidth = 0.7) +
    geom_segment(data = top_tree, aes(x, y, xend = xend, yend = yend),
                 colour = "grey30", linewidth = 0.5, lineend = "round") +
    annotate("segment", x = 0.5, xend = n + 0.5,
             y = tree_base + cut_h * scale_h, yend = tree_base + cut_h * scale_h,
             colour = "grey55", linetype = "dashed", linewidth = 0.4) +
    scale_fill_gradient(low = "#eef3f8", high = "#1f4e79", limits = fill_limits, guide = "none") +
    scale_colour_manual(values = c(`TRUE` = "white", `FALSE` = "grey15"), guide = "none") +
    scale_x_continuous(breaks = seq_len(n), labels = order) +
    scale_y_continuous(breaks = rev(seq_len(n)), labels = order) +
    coord_equal(xlim = c(0.5, n + 0.5), ylim = c(0.5, tree_base + tree_depth + 0.05),
                expand = FALSE, clip = "off") +
    labs(title = paste0(as.roman(leg), " Legislature, ", info$first_year, "-", info$last_year,
                        " (", info$roll_calls, " roll calls, ", model$k, " blocs)"),
         x = NULL, y = NULL) +
    theme_minimal(base_size = 11) +
    theme(panel.grid = element_blank(),
          plot.title = element_text(size = 11),
          plot.title.position = "plot",
          axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1))
}

heatmaps <- setNames(lapply(legislatures, plot_legislature), as.roman(legislatures))
for (heatmap in heatmaps) print(heatmap)
