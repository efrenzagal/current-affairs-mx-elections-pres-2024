# ─────────────────────────────────────────────────────────────────────────────
# PRE bloc-level shares: national / state / district
# Requires 00_query_helpers.R, 01_queries_pre.R, 02_bloc_mapping.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Coverage check -----------------------------------------------------------

  check_bloc_coverage(pre_national_votes, "PRE")

}

{ # Bloc shares ------------------------------------------------------------

  pre_national_bloc_shares <- pre_national_votes %>%
    to_bloc_shares(election_id, year) %>%
    arrange(year, desc(share))

  pre_state_bloc_shares <- pre_state_votes %>%
    to_bloc_shares(election_id, year, id_estado) %>%
    arrange(year, id_estado, desc(share))

  pre_district_bloc_shares <- pre_district_votes %>%
    mutate(district_key = paste(id_estado, id_distrito_federal, sep = "_")) %>%
    to_bloc_shares(election_id, year, district_key) %>%
    arrange(year, district_key, desc(share))

}

{ # Sanity check -----------------------------------------------------------

  pre_national_bloc_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
