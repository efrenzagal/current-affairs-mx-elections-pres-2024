# ─────────────────────────────────────────────────────────────────────────────
# SEN bloc-level shares: national / state
# Requires 00_query_helpers.R, 01_queries_sen.R, 02_bloc_mapping.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Coverage check -----------------------------------------------------------

  check_bloc_coverage(sen_national_votes, "SEN")

}

{ # Bloc shares ------------------------------------------------------------

  sen_national_bloc_shares <- sen_national_votes %>%
    to_bloc_shares(election_id, year) %>%
    arrange(year, desc(share))

  sen_state_bloc_shares <- sen_state_votes %>%
    to_bloc_shares(election_id, year, id_estado) %>%
    arrange(year, id_estado, desc(share))

}

{ # Sanity check -----------------------------------------------------------

  sen_national_bloc_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
