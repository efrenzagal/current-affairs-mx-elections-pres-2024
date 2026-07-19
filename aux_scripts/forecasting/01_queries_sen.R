# ─────────────────────────────────────────────────────────────────────────────
# SEN (senate) — national / state vote shares, 2000-2024
# No district level: senators are elected by state (2 MR + 1a minoria per
# state, plus a national PR list), not by federal district.
# Requires 00_query_helpers.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Extract -----------------------------------------------------------------

  sen_national_votes <- get_national_votes("SEN")
  sen_state_votes    <- get_state_votes("SEN")

}

{ # Shares --------------------------------------------------------------

  sen_national_shares <- sen_national_votes %>% add_share(election_id, year)
  sen_state_shares    <- sen_state_votes    %>% add_share(election_id, year, id_estado)

}

{ # Sanity check -----------------------------------------------------------

  sen_national_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
