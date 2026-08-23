import sqlite3
import unittest

from web.scripts.export_gaceta_web import materialize_lxvi_deputy_seat_members


class LxviDeputySeatMemberTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = OFF")

    def tearDown(self):
        self.conn.close()

    def test_materializes_and_replaces_lxvi_seat_members(self):
        seats = [
            {
                "id": "DIP_TEST",
                "currentPersonId": "DEP_SUBSTITUTE",
            }
        ]
        members = {
            "DIP_TEST": [
                {
                    "personId": "DEP_TITULAR",
                    "name": "Persona Titular",
                    "party": "PAN",
                    "role": "titular",
                    "sourceUrl": "https://ine.example/",
                },
                {
                    "personId": "DEP_SUBSTITUTE",
                    "name": "Persona Suplente",
                    "party": "PAN",
                    "role": "suplente",
                    "sourceUrl": "https://camara.example/",
                },
            ]
        }

        count = materialize_lxvi_deputy_seat_members(self.conn, seats, members)

        self.assertEqual(count, 2)
        rows = self.conn.execute(
            """
            SELECT deputy_id, seat_role, is_current_occupant
            FROM fact_legislature_66_deputy_seat_member
            ORDER BY deputy_id
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("DEP_SUBSTITUTE", "suplente", 1),
                ("DEP_TITULAR", "titular", 0),
            ],
        )

        replacement = {
            "DIP_TEST": [
                {
                    "personId": "DEP_TITULAR",
                    "name": "Persona Titular",
                    "party": "PAN",
                    "role": "titular",
                    "sourceUrl": "https://ine.example/",
                }
            ]
        }
        materialize_lxvi_deputy_seat_members(self.conn, seats, replacement)
        remaining = self.conn.execute(
            "SELECT deputy_id FROM fact_legislature_66_deputy_seat_member"
        ).fetchall()
        self.assertEqual(remaining, [("DEP_TITULAR",)])


if __name__ == "__main__":
    unittest.main()
