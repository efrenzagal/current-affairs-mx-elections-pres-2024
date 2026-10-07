-- National votes per ballot key for every election that has polls, with the
-- parties that key credits. A joint coalition mark (PT_MORENA, C_PRI_PVEM,
-- A. CAM., ...) lists all its member parties; a solo key lists itself.
-- valid_votes is the election's valid vote (party keys + unregistered
-- candidates), the same denominator as fact_vote_intention_result.pct_valid.
WITH votes AS (
    SELECT election_id, party_key, SUM(votes) AS votes
    FROM fact_casilla_vote
    WHERE election_id IN (SELECT DISTINCT election_id FROM fact_vote_intention_poll)
    GROUP BY election_id, party_key
),
valid AS (
    SELECT DISTINCT election_id, valid_votes
    FROM fact_vote_intention_result
)
SELECT
    v.election_id,
    v.party_key,
    CASE WHEN d.is_coalition = 1 THEN d.members ELSE v.party_key END AS members,
    v.votes,
    valid.valid_votes
FROM votes v
LEFT JOIN dim_party d USING (party_key)
JOIN valid USING (election_id)
