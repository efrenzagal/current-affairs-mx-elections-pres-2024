import sqlite3
import unittest

from ingestion.audited_overrides import load_person_aliases, load_seat_overrides
from ingestion.congress_seat_member_resolve import (
    covers_name,
    name_tokens,
    write_chamber,
)


def resolved(chamber="DIP", **overrides):
    """One seat with a titular and a substitute, in the shape write_chamber wants."""
    payload = {
        "chamber": chamber,
        "roster": {"observedAt": "2025-06-01", "sourceUrl": "https://camara.example/"},
        "seats": [
            {
                "id": "DIP_TEST",
                "electedPersonId": "DEP_TITULAR",
                "electedParty": "PAN",
                "titularName": "Persona Titular",
                "substituteName": "Persona Suplente",
                "currentPersonId": "DEP_SUBSTITUTE",
                "currentName": "Persona Suplente",
                "currentParty": "PAN",
                "currentStatus": "en_funciones",
            }
        ],
        "seatMembers": {
            "DIP_TEST": [
                {
                    "personId": "DEP_TITULAR",
                    "name": "Persona Titular",
                    "party": "PAN",
                    "role": "titular",
                    "sourceUrl": "https://ine.example/",
                    "voteCount": 12,
                },
                {
                    "personId": "DEP_SUBSTITUTE",
                    "name": "Persona Suplente",
                    "party": "PAN",
                    "role": "suplente",
                    "sourceUrl": "https://camara.example/",
                    "voteCount": 3,
                },
            ]
        },
        "formerMembers": [],
        "aliases": {},
        "conflicts": [],
        "displayNames": [
            (chamber, "DEP_TITULAR", "Persona Titular", "seat_table"),
            (chamber, "DEP_SUBSTITUTE", "Persona Suplente", "roll_call"),
        ],
    }
    payload.update(overrides)
    return payload


class WriteChamberTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = OFF")

    def tearDown(self):
        self.conn.close()

    def test_marks_the_directory_occupant_not_the_elected_titular(self):
        write_chamber(self.conn, resolved())

        rows = self.conn.execute(
            """
            SELECT person_id, seat_role, is_current_occupant, vote_count
            FROM fact_legislature_66_seat_member ORDER BY person_id
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("DEP_SUBSTITUTE", "suplente", 1, 3),
                ("DEP_TITULAR", "titular", 0, 12),
            ],
        )

    def test_member_order_is_preserved_for_the_client_index(self):
        # The published history stores a member's position, not their id, so a
        # reordered rebuild would silently reattribute votes.
        write_chamber(self.conn, resolved())
        self.assertEqual(
            self.conn.execute(
                "SELECT person_id FROM fact_legislature_66_seat_member"
                " ORDER BY member_index"
            ).fetchall(),
            [("DEP_TITULAR",), ("DEP_SUBSTITUTE",)],
        )

    def test_rebuild_replaces_rather_than_accumulates(self):
        write_chamber(self.conn, resolved())
        shrunk = resolved()
        shrunk["seatMembers"]["DIP_TEST"] = shrunk["seatMembers"]["DIP_TEST"][:1]
        write_chamber(self.conn, shrunk)

        self.assertEqual(
            self.conn.execute(
                "SELECT person_id FROM fact_legislature_66_seat_member"
            ).fetchall(),
            [("DEP_TITULAR",)],
        )

    def test_rebuilding_one_chamber_leaves_the_other_intact(self):
        # The two chambers are resolved in separate passes against one database.
        write_chamber(self.conn, resolved(chamber="SEN"))
        write_chamber(self.conn, resolved(chamber="DIP"))
        write_chamber(self.conn, resolved(chamber="DIP"))

        self.assertEqual(
            self.conn.execute(
                "SELECT chamber, COUNT(*) FROM fact_legislature_66_seat_member"
                " GROUP BY chamber ORDER BY chamber"
            ).fetchall(),
            [("DIP", 2), ("SEN", 2)],
        )

    def test_conflict_preserves_every_reported_voter(self):
        payload = resolved(
            conflicts=[
                {
                    "seatId": "DIP_TEST",
                    "voteId": "5030",
                    "countedPersonId": "DEP_TITULAR",
                    "reportedPersonIds": ["DEP_TITULAR", "DEP_SUBSTITUTE"],
                }
            ]
        )
        write_chamber(self.conn, payload)

        self.assertEqual(
            self.conn.execute(
                "SELECT counted_person_id, reported_person_ids"
                " FROM fact_legislature_66_seat_vote_conflict"
            ).fetchone(),
            ("DEP_TITULAR", "DEP_TITULAR,DEP_SUBSTITUTE"),
        )


    def test_seat_table_spelling_wins_over_the_roll_call(self):
        # A legislator must read the same way in the hemicycle, the profile
        # search and the per-square tooltips.
        write_chamber(self.conn, resolved())
        self.assertEqual(
            self.conn.execute(
                "SELECT display_name, name_source FROM fact_legislature_66_person"
                " WHERE person_id = 'DEP_TITULAR'"
            ).fetchone(),
            ("Persona Titular", "seat_table"),
        )


class AuditedOverrideTests(unittest.TestCase):
    """The audited corrections have no other guard: nothing derives them."""

    def test_senate_seat_override_still_names_its_seat(self):
        overrides = load_seat_overrides()
        self.assertEqual(
            overrides[("SEN", "1805")]["seatId"], "SEN_4D4CFB205261"
        )
        self.assertTrue(
            overrides[("SEN", "1805")]["sourceUrl"].startswith("https://")
        )

    def test_both_chamber_aliases_resolve_to_their_canonical_identity(self):
        aliases = load_person_aliases()
        self.assertEqual(
            aliases["DIP"],
            {
                "DEP_0AC343D3EC5E": "DEP_10ACE730EC87",
                "DEP_62F86822CD67": "DEP_A9A4258198C3",
            },
        )
        self.assertEqual(aliases["SEN"], {})


class NameMatchingTests(unittest.TestCase):
    def test_initial_stands_in_for_a_given_name(self):
        self.assertTrue(
            covers_name(
                name_tokens("PEREZ JAEN ZERMEÑO M. ELENA"),
                name_tokens("MARIA ELENA PEREZ-JAEN ZERMEÑO"),
            )
        )

    def test_surname_alone_is_not_a_match(self):
        # Two full-length agreements are required, so people who merely share a
        # surname must not be collapsed into one person.
        self.assertFalse(
            covers_name(name_tokens("GARCIA JUAN"), name_tokens("GARCIA PEDRO"))
        )

    def test_senate_and_camara_spellings_of_one_person_agree(self):
        self.assertEqual(
            name_tokens("Sen. Macías Rábago, Julieta"),
            name_tokens("JULIETA MACIAS RABAGO"),
        )


if __name__ == "__main__":
    unittest.main()
