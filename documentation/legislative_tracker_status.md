# Legislative tracker (MIEL) — last update

Last refreshed: **2026-09-05**, Senado only (votes + roster + website export run
directly, not via `aux_scripts/update_legislative_tracker.py`). The Diputados
stages were skipped: the Gaceta index listed no new L66 votes and had not yet
opened a `vot66_a3*` period page for the tercer año.

| Source | Rows | Latest date |
| --- | --- | --- |
| Senado roll-call votes | 379 | 2026-09-02 |
| Diputados roll-call votes (legislatura 66) | 295 | 2026-05-28 |
| Diputados seats (`dim_diputados`) | 500 | — |
| Senadores seats (`dim_senadores`) | 128 | — |

The Senate roster was re-crawled on 2026-09-06 (snapshot `2026-09-06T03:43:16Z`)
after the tercer año began: four senators joined and three left, taking the
directory from 127 to 128 in-office members and closing the last vacant seat.
`web/public/data/` was re-exported against that roster; `legislature-66.json`
came back byte-identical, confirming the Diputados side was genuinely unchanged.

Known gap as of this refresh:

- `fact_senado_vote_classification` covers 378 of 379 votes; `votacion_id` 5123
  (2026-09-02) is unclassified. A submit-ready single-request batch is staged at
  `data/senado_vote_classification/pending_5123/`.

Re-run the full update with:

```bash
/usr/bin/python3 aux_scripts/update_legislative_tracker.py
```
