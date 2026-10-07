-- One row per ballot option (candidate, coalition or party) in every federal
-- vote-intention poll, with the poll's metadata.
--
-- pct_efectiva rescales the option to the poll's ballot + "otros" total, so
-- polls that report with and without undecided land on the same basis.
-- result_pct_valid is the solo-key result; the R script rebuilds the benchmark
-- with coalition ballots split among their parties.
-- Source: view_vote_intention_vs_result (vote_intention/ingest.py).
SELECT
    poll_id,
    election_id,
    election_date,
    reference_date,
    days_before_election,
    date_precision,
    poll_kind,
    intention_level,
    share_basis,
    pollster,
    familia,
    cliente,
    metodo,
    sample_size,
    opcion,
    option_kind,
    candidato,
    partido_coalicion,
    party_keys,
    pct,
    pct_efectiva,
    result_pct_valid
FROM view_vote_intention_vs_result
