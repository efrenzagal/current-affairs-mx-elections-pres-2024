-- LXVI Camara roll-call records by constitutional seat and legislator.
-- vote_records includes Ausente; votes_cast excludes Ausente.
WITH votes_66 AS (
    SELECT f.*, v.vote_date
    FROM fact_gaceta_deputy_vote AS f
    JOIN dim_gaceta_vote AS v
      ON v.gaceta_vote_id = f.gaceta_vote_id
    WHERE v.legislature = 66
)
SELECT
    sm.seat_id,
    d.ine_candidate_name AS registered_titular,
    d.ine_substitute_name AS registered_substitute,
    sm.deputy_id,
    sm.deputy_name,
    sm.party_key,
    sm.seat_role,
    sm.is_current_occupant,
    COUNT(v.gaceta_vote_id) AS vote_records,
    COALESCE(SUM(v.vote_choice != 'Ausente'), 0) AS votes_cast,
    COALESCE(SUM(v.vote_choice = 'Favor'), 0) AS favor,
    COALESCE(SUM(v.vote_choice = 'Contra'), 0) AS contra,
    COALESCE(SUM(v.vote_choice = 'Abstención'), 0) AS abstentions,
    COALESCE(SUM(v.vote_choice = 'Ausente'), 0) AS absences,
    MIN(v.vote_date) AS first_vote_date,
    MAX(v.vote_date) AS latest_vote_date
FROM fact_legislature_66_deputy_seat_member AS sm
JOIN dim_diputados AS d
  ON d.diputado_id = sm.seat_id
LEFT JOIN votes_66 AS v
  ON v.deputy_id = sm.deputy_id
GROUP BY
    sm.seat_id,
    d.ine_candidate_name,
    d.ine_substitute_name,
    sm.deputy_id,
    sm.deputy_name,
    sm.party_key,
    sm.seat_role,
    sm.is_current_occupant
ORDER BY sm.seat_id, sm.seat_role, sm.deputy_name;
