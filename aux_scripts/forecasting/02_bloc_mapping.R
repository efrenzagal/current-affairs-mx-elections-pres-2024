# ─────────────────────────────────────────────────────────────────────────────
# Bloc consolidation — shared across PRE, SEN, and DIP
# Collapses raw party_key rows (which fragment across coalition ballot lines)
# into the project's existing L/R/C/MC ideology blocs. Chamber-agnostic:
# PRE/SEN/DIP ran the same national coalitions in shared years, so one map
# covers all three. DIP's midterm-only years (2015, 2021) introduce a few
# extra minor-party codes not seen in PRE/SEN -- flagged below.
# Requires 00_query_helpers.R (and whichever 01_queries_*.R chamber file(s)
# you're using) to have been run first.
# ─────────────────────────────────────────────────────────────────────────────

{ # Bloc lookup table -----------------------------------------------------
  # Ported from IDEOLOGY_MAP in ui/common.py (the map that drives the
  # municipio trajectory ternaries -- kept identical here so the fundamentals
  # model and the existing charts agree on what "L/R/C" means).
  #
  # A single flat map, NOT one per election: it tracks each party's enduring
  # ideological family rather than its tactical coalition-of-the-day. E.g.
  # PRD is "L" even in 2018 when it ran allied with PAN, because the model
  # cares about the PRD/MORENA lineage's underlying support, not who it
  # struck a deal with that cycle.
  # L = Left (PRD/MORENA tradition), R = Right (PAN tradition),
  # C = Center (PRI tradition), MC = Movimiento Ciudadano as an independent
  # political force.
  #
  # MC deviation from ui/common.py: in 2012 and 2018 MC ran as junior
  # coalition partner (PRD+PT+MC for AMLO in 2012; PAN+PRD+MC "Frente" for
  # Anaya in 2018) -- those ballot lines elected AMLO/Anaya, so they stay L/R
  # respectively. Only in 2024 did MC run a fully independent candidate
  # (Alvarez Maynez), so only PRE_2024's "MC" row gets its own bloc. Handled
  # as an election-scoped override below rather than in the flat map, since
  # every other year's MC line supported someone else's coalition.

  ideology_map <- c(
    # 1994
    "PRD" = "L", "PT" = "L", "PFCRN" = "L", "PPS" = "L", "PARM" = "L",
    "PAN" = "R", "UNO_PDM" = "R",
    "PRI" = "C", "PVEM" = "C",
    # 2000
    "A. MEX." = "L",
    "A. CAM." = "R",
    "PCD" = "C", "DSPPN" = "C",
    # 2006
    "PBT" = "L",
    "APM" = "C",
    "ASDC" = "L",
    "NVA_A" = "C",
    # 2012 -- MC here is junior partner to AMLO's coalition, so L not C
    "C_PRD_PT_MC" = "L", "C_PRD_PT" = "L", "C_PRD_MC" = "L", "C_PT_MC" = "L",
    "MC" = "L",
    "C_PRI_PVEM" = "C", "PANAL" = "C",
    # 2018 -- MOVIMIENTO CIUDADANO here is junior partner to Anaya's Frente, so R
    "MORENA" = "L", "PT_MORENA" = "L", "PT_MORENA_PES" = "L", "MORENA_PES" = "L",
    "PT_PES" = "L",
    "ENCUENTRO SOCIAL" = "R",
    "PAN_PRD_MC" = "R", "PAN_PRD" = "R", "PAN_MC" = "R",
    "MOVIMIENTO CIUDADANO" = "R", "PRD_MC" = "R",
    "PRI_PVEM" = "C", "PRI_NA" = "C", "PRI_PVEM_NA" = "C", "PVEM_NA" = "C",
    "NUEVA ALIANZA" = "C",
    "CAND_IND_01" = "C", "CAND_IND_02" = "C",
    # 2024
    "PVEM_MORENA" = "L", "PVEM_PT" = "L", "PVEM_PT_MORENA" = "L",
    "PAN_PRI_PRD" = "R", "PAN_PRI" = "R", "PRI_PRD" = "R",

    # DIP-only midterm years (2015, 2021) -- minor/independent codes not seen
    # in PRE/SEN. These are coarser guesses than the major-party assignments
    # above; review before trusting a DIP fundamentals model on them.
    # 2015
    "PES" = "R",          # Partido Encuentro Social, same family as ENCUENTRO SOCIAL
    "PH" = "L",            # Partido Humanista, ran allied with PRD in most 2015 races
    "CAND_IND_1" = "C", "CAND_IND_2" = "C",
    # 2021
    "CI" = "C",             # independent candidate, generic code
    "FXM" = "R",            # Fuerza por Mexico, PES successor, right-populist
    "RSP" = "C",            # Redes Sociales Progresistas, small/ideologically ambiguous
    # 2024 DIP (no-underscore independent-candidate variant)
    "CAND_IND1" = "C", "CAND_IND2" = "C"
  )

  bloc_lookup <- tibble(party_key = names(ideology_map), bloc = unname(ideology_map)) %>%
    # 2024 MC ran independently -- override only for that election
    bind_rows(tibble(election_id = "PRE_2024", party_key = "MC", bloc = "MC"))

}

{ # Join helper -------------------------------------------------------------
  # bloc_lookup has two shapes: a global (party_key -> bloc) map plus one
  # election-scoped override row. This resolves both in one join per level.

  join_bloc <- function(votes_df) {
    votes_df %>%
      left_join(
        bloc_lookup %>% filter(!is.na(election_id)),
        by = c("election_id", "party_key")
      ) %>%
      left_join(
        bloc_lookup %>% filter(is.na(election_id)) %>% select(party_key, bloc_default = bloc),
        by = "party_key"
      ) %>%
      mutate(bloc = coalesce(bloc, bloc_default)) %>%
      select(-bloc_default)
  }

}

{ # Coverage check helper ---------------------------------------------------
  # Every party_key that actually appears in a chamber's vote data must have
  # a bloc assigned, or it silently drops out of the aggregates. Call this
  # with e.g. pre_national_votes / sen_national_votes / dip_national_votes
  # after sourcing the relevant 01_queries_*.R.

  check_bloc_coverage <- function(votes_df, label) {
    unmapped <- votes_df %>% anti_join(bloc_lookup, by = "party_key")
    if (nrow(unmapped) > 0) {
      warning(sprintf("Unmapped party_key rows in %s -- update ideology_map:", label))
      print(unmapped %>% distinct(election_id, party_key))
    }
  }

}

{ # Bloc-share helper ---------------------------------------------------
  # Generic: collapses any votes_df (national/state/district, any chamber)
  # from party_key to bloc and recomputes shares within the given grouping.

  to_bloc_shares <- function(votes_df, ...) {
    votes_df %>%
      join_bloc() %>%
      group_by(..., bloc) %>%
      summarise(votes = sum(votes), .groups = "drop") %>%
      group_by(...) %>%
      mutate(share = votes / sum(votes)) %>%
      ungroup()
  }

}
