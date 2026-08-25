import sqlite3
import unittest

# DIP fixtures throughout, so this exercises the Camara's copy of the temporal
# rebuild; the Senado's is pinned to it by tests/test_chamber_pipeline_parity.py.
from camara_de_diputados.composicion.ingest import (
    SCHEMA,
    _rebuild_occupancy_history,
    _rebuild_party_membership_history,
)


class CongressTemporalTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(
            """
            CREATE TABLE dim_gaceta_vote (
                gaceta_vote_id TEXT PRIMARY KEY, legislature INTEGER, vote_date TEXT
            );
            CREATE TABLE fact_gaceta_deputy_vote (
                gaceta_vote_id TEXT, deputy_id TEXT, party_key TEXT
            );
            CREATE TABLE dim_senado_vote (
                votacion_id INTEGER PRIMARY KEY, legislature INTEGER, vote_date TEXT
            );
            CREATE TABLE fact_senador_vote (
                votacion_id INTEGER, senador_id INTEGER, grupo_parlamentario TEXT
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def _snapshot(self, snapshot_id, observed_at, party, status="en_funciones"):
        self.conn.execute(
            """
            INSERT INTO dim_congress_roster_snapshot
                (snapshot_id, chamber, legislature, observed_at, source_url,
                 source_sha256, roster_row_count, constitutional_seats)
            VALUES (?, 'DIP', 66, ?, 'https://example.test', ?, 1, 500)
            """,
            (snapshot_id, observed_at, snapshot_id),
        )
        self.conn.execute(
            """
            INSERT INTO fact_congress_roster_seat
                (snapshot_id, chamber, seat_id, member_source_id, current_name,
                 current_party, member_status, vote_person_id, election_name,
                 election_party, seat_type, match_method)
            VALUES (?, 'DIP', 'DIP_TEST', '7', 'Persona Prueba', ?, ?,
                    'DEP_TEST', 'Persona Prueba', 'PVEM', 'MR', 'district_key')
            """,
            (snapshot_id, party, status),
        )

    def test_occupancy_collapses_unchanged_snapshots_and_dates_change(self):
        self._snapshot("S1", "2026-01-01T00:00:00+00:00", "PVEM")
        self._snapshot("S2", "2026-02-01T00:00:00+00:00", "PVEM")
        self._snapshot("S3", "2026-03-01T00:00:00+00:00", "MORENA")

        history = _rebuild_occupancy_history(self.conn)

        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[0]["valid_to"], "2026-03-01T00:00:00+00:00")
        self.assertIsNone(history.iloc[1]["valid_to"])
        self.assertEqual(history.iloc[1]["party_key"], "MORENA")

    def test_vote_party_changes_become_source_specific_episodes(self):
        self._snapshot("S1", "2026-01-01T00:00:00+00:00", "MORENA")
        self.conn.executemany(
            "INSERT INTO dim_gaceta_vote VALUES (?, 66, ?)",
            [("V1", "2025-01-01"), ("V2", "2025-01-02"), ("V3", "2025-02-01")],
        )
        self.conn.executemany(
            "INSERT INTO fact_gaceta_deputy_vote VALUES (?, 'DEP_TEST', ?)",
            [("V1", "PVEM"), ("V2", "PVEM"), ("V3", "MRN")],
        )

        occupancy = _rebuild_occupancy_history(self.conn)
        memberships = _rebuild_party_membership_history(self.conn, occupancy)
        vote_episodes = memberships[memberships["source_type"] == "vote_reported"]

        self.assertEqual(vote_episodes["party_key"].tolist(), ["PVEM", "MORENA"])
        self.assertEqual(vote_episodes.iloc[0]["valid_to"], "2025-02-01")
        self.assertEqual(int(vote_episodes.iloc[0]["observations"]), 2)

    def test_license_is_preserved_as_occupancy_status(self):
        self._snapshot("S1", "2026-01-01T00:00:00+00:00", "MORENA", "licencia")
        history = _rebuild_occupancy_history(self.conn)
        self.assertEqual(history.iloc[0]["status"], "licencia")


if __name__ == "__main__":
    unittest.main()
