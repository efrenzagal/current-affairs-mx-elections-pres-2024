# ─────────────────────────────────────────────────────────────────────────────
# DIP bloc-level shares: national / state / district
# Requires 00_query_helpers.R, 01_queries_dip.R, 02_bloc_mapping.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Coverage check -----------------------------------------------------------
}
  check_bloc_coverage(dip_national_votes, "DIP")

}

{ # Bloc shares ------------------------------------------------------------

  dip_national_bloc_shares <- dip_national_votes %>%
    to_bloc_shares(election_id, year) %>%
    mutate(is_midterm = year %in% MIDTERM_YEARS) %>%
    arrange(year, desc(share))

  dip_state_bloc_shares <- dip_state_votes %>%
    to_bloc_shares(election_id, year, id_estado) %>%
    arrange(year, id_estado, desc(share))

  dip_district_bloc_shares <- dip_district_votes %>%
    mutate(district_key = paste(id_estado, id_distrito_federal, sep = "_")) %>%
    to_bloc_shares(election_id, year, district_key) %>%
    arrange(year, district_key, desc(share))

}

{ # Sanity check -----------------------------------------------------------

  dip_national_bloc_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
