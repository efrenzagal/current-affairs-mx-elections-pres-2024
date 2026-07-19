# ─────────────────────────────────────────────────────────────────────────────
# PRE (president) — national / state / district vote shares, 1994-2024
# Requires 00_query_helpers.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Extract -----------------------------------------------------------------

  pre_national_votes <- get_national_votes("PRE")
  pre_state_votes    <- get_state_votes("PRE")
  pre_district_votes <- get_district_votes("PRE")

}

{ # Shares --------------------------------------------------------------

  pre_national_shares <- pre_national_votes %>% add_share(election_id, year)
  pre_state_shares    <- pre_state_votes    %>% add_share(election_id, year, id_estado)
  pre_district_shares <- pre_district_votes %>%
    mutate(district_key = paste(id_estado, id_distrito_federal, sep = "_")) %>%
    add_share(election_id, year, district_key)

}

{ # Sanity check -----------------------------------------------------------

  pre_national_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
