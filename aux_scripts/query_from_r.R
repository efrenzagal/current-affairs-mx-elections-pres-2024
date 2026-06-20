# Election Data Warehouse — R Query Examples
# 
# Install required packages:
# install.packages(c("DBI", "RSQLite", "dplyr", "tidyverse"))

library(DBI)
library(RSQLite)
library(dplyr)

# ============================================================================
# CONNECTION
# ============================================================================
setwd("~/Documents/GitHub/current-affairs-mx-elections-pres-2024/")

# Connect to the SQLite database
con <- dbConnect(RSQLite::SQLite(), "election_data.db")

# List all tables
dbListTables(con)

# ============================================================================
# BASIC QUERIES
# ============================================================================

# Query 1: Total votes by party (2024 Presidential)
votes_by_party <- dbGetQuery(con, "
    SELECT 
        p.party_key,
        p.is_coalition,
        SUM(f.votes) as total_votes
    FROM fact_casilla_vote f
    JOIN dim_party p ON f.party_key = p.party_key
    WHERE f.election_id = 'PRE_2024'
    GROUP BY f.party_key
    ORDER BY total_votes DESC
")

print(votes_by_party)


# Query 2: Presidential votes by state
votes_by_state <- dbGetQuery(con, "
    SELECT 
        g.id_estado,
        g.nombre_estado,
        SUM(f.votes) as total_votes
    FROM fact_casilla_vote f
    JOIN dim_casilla c ON f.election_id = c.election_id AND f.casilla_id = c.casilla_id
    JOIN dim_geography g ON c.geo_id = g.geo_id
    WHERE f.election_id = 'PRE_2024'
    GROUP BY g.id_estado
    ORDER BY total_votes DESC
")

print(votes_by_state)


# Query 3: Vote share (percentage)
vote_share <- dbGetQuery(con, "
    SELECT 
        p.party_key,
        SUM(f.votes) as total_votes,
        ROUND(100.0 * SUM(f.votes) / SUM(SUM(f.votes)) OVER (), 2) as pct_share
    FROM fact_casilla_vote f
    JOIN dim_party p ON f.party_key = p.party_key
    WHERE f.election_id = 'PRE_2024'
    GROUP BY f.party_key
    ORDER BY total_votes DESC
")

print(vote_share)


# ============================================================================
# DPLYR INTERFACE (tidyverse style)
# ============================================================================

# Using tbl() to create lazy connections (data stays in DB until collected)

fact_votes <- tbl(con, "fact_casilla_vote")
parties <- tbl(con, "dim_party")
casillas <- tbl(con, "dim_casilla")
geography <- tbl(con, "dim_geography")
elections <- tbl(con, "dim_election")

# Calculate votes by party using dplyr
parties_small <- parties %>%
  select(party_key, is_coalition) %>%
  collect()

votes_party_dplyr <- fact_votes %>%
  filter(election_id == "PRE_2024") %>%
  select(party_key, votes) %>%
  collect() %>%
  inner_join(parties_small, by = "party_key") %>%
  group_by(party_key, is_coalition) %>%
  summarise(total_votes = sum(votes, na.rm = TRUE), .groups = "drop") %>%
  arrange(desc(total_votes))

print(votes_party_dplyr)


# Calculate votes by state using dplyr
# Select only needed columns from each table to avoid column name conflicts
casillas_small <- casillas %>%
  select(election_id, casilla_id, geo_id) %>%
  collect()

geography_small <- geography %>%
  select(geo_id, id_estado, nombre_estado) %>%
  collect()

votes_state_dplyr <- fact_votes %>%
  filter(election_id == "PRE_2024") %>%
  select(election_id, casilla_id, votes) %>%
  collect() %>%
  inner_join(casillas_small, by = c("election_id", "casilla_id")) %>%
  inner_join(geography_small, by = "geo_id") %>%
  group_by(id_estado, nombre_estado) %>%
  summarise(total_votes = sum(votes, na.rm = TRUE), .groups = "drop") %>%
  arrange(desc(total_votes))

print(votes_state_dplyr)


# ============================================================================
# ADVANCED QUERIES
# ============================================================================

# Query: Coalition vs. non-coalition votes
coalition_comparison <- dbGetQuery(con, "
    SELECT 
        p.is_coalition,
        COUNT(DISTINCT p.party_key) as num_parties,
        SUM(f.votes) as total_votes,
        ROUND(100.0 * SUM(f.votes) / SUM(SUM(f.votes)) OVER (), 2) as pct_share
    FROM fact_casilla_vote f
    JOIN dim_party p ON f.party_key = p.party_key
    WHERE f.election_id = 'PRE_2024'
    GROUP BY p.is_coalition
")

print(coalition_comparison)


# Query: Top 10 sections by turnout
turnout_by_section <- dbGetQuery(con, "
    SELECT 
        g.nombre_estado,
        g.seccion,
        COUNT(DISTINCT f.casilla_id) as num_casillas,
        SUM(f.total_votos) as total_votes,
        ROUND(AVG(f.total_votos), 1) as avg_votes_per_casilla
    FROM fact_casilla_vote f
    JOIN dim_casilla c ON f.election_id = c.election_id AND f.casilla_id = c.casilla_id
    JOIN dim_geography g ON c.geo_id = g.geo_id
    WHERE f.election_id = 'PRE_2024'
    GROUP BY g.id_estado, g.seccion
    ORDER BY total_votes DESC
    LIMIT 10
")

print(turnout_by_section)


# Query: Elections by year
elections_summary <- dbGetQuery(con, "
    SELECT 
        e.year,
        e.election_type,
        e.election_id,
        COUNT(DISTINCT f.casilla_id) as casillas,
        SUM(f.votes) as total_votes
    FROM dim_election e
    LEFT JOIN fact_casilla_vote f ON e.election_id = f.election_id
    GROUP BY e.election_id
    ORDER BY e.year DESC, e.election_type
")

print(elections_summary)

# ============================================================================
# DISCONNECT
# ============================================================================

dbDisconnect(con)
print("Database connection closed.")
