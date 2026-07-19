# ─────────────────────────────────────────────────────────────────────────────
# DIP (deputies) — national / state / district vote shares, 2000-2024
# Includes 2015 and 2021, the two midterm years with no PRE/SEN race.
# District is the natural level here (300 MR seats).
# Requires 00_query_helpers.R.
# ─────────────────────────────────────────────────────────────────────────────

{ # Extract -----------------------------------------------------------------

  dip_national_votes <- get_national_votes("DIP")
  dip_state_votes    <- get_state_votes("DIP")
  dip_district_votes <- get_district_votes("DIP")

}

{ # Shares --------------------------------------------------------------

  dip_national_shares <- dip_national_votes %>% add_share(election_id, year)
  dip_state_shares    <- dip_state_votes    %>% add_share(election_id, year, id_estado)
  dip_district_shares <- dip_district_votes %>%
    mutate(district_key = paste(id_estado, id_distrito_federal, sep = "_")) %>%
    add_share(election_id, year, district_key)

}

{ # Midterm flag -----------------------------------------------------------
  # 2015 and 2021 have no concurrent PRE/SEN race -- useful to mark since a
  # DIP fundamentals model may want incumbency/approval predictors that
  # behave differently in a midterm vs. a presidential-year DIP race.

  MIDTERM_YEARS <- c(2015, 2021)

  dip_national_shares <- dip_national_shares %>% mutate(is_midterm = year %in% MIDTERM_YEARS)

}

{ # Sanity check -----------------------------------------------------------

  dip_national_shares %>% group_by(election_id) %>% summarise(check_sum = sum(share), .groups = "drop")

}
