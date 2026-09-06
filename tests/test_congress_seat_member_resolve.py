import sqlite3
import unittest

from camara_de_diputados.escanos import audited_overrides as dip_overrides
from camara_de_diputados.escanos import seat_members as dip
from camara_de_senadores.escanos import audited_overrides as sen_overrides
from camara_de_senadores.escanos import seat_members as sen


def resolved(chamber="DIP", **overrides):
    """One Camara seat with a titular and substitute, as write_seats expects."""
    payload = {
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


class WriteSeatsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys = OFF")

    def tearDown(self):
        self.conn.close()

    def test_marks_the_directory_occupant_not_the_elected_titular(self):
        dip.write_seats(self.conn, resolved())

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
        dip.write_seats(self.conn, resolved())
        self.assertEqual(
            self.conn.execute(
                "SELECT person_id FROM fact_legislature_66_seat_member"
                " ORDER BY member_index"
            ).fetchall(),
            [("DEP_TITULAR",), ("DEP_SUBSTITUTE",)],
        )

    def test_rebuild_replaces_rather_than_accumulates(self):
        dip.write_seats(self.conn, resolved())
        shrunk = resolved()
        shrunk["seatMembers"]["DIP_TEST"] = shrunk["seatMembers"]["DIP_TEST"][:1]
        dip.write_seats(self.conn, shrunk)

        self.assertEqual(
            self.conn.execute(
                "SELECT person_id FROM fact_legislature_66_seat_member"
            ).fetchall(),
            [("DEP_TITULAR",)],
        )

    def test_senate_resolution_has_no_warehouse_writer(self):
        # Senado is now resolved directly into the website JSON. This guards
        # against accidentally reintroducing its intermediate table family.
        self.assertFalse(hasattr(sen, "write_seats"))
        self.assertFalse(hasattr(sen, "SCHEMA"))

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
        dip.write_seats(self.conn, payload)

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
        dip.write_seats(self.conn, resolved())
        self.assertEqual(
            self.conn.execute(
                "SELECT display_name, name_source FROM fact_legislature_66_person"
                " WHERE person_id = 'DEP_TITULAR'"
            ).fetchone(),
            ("Persona Titular", "seat_table"),
        )


class AuditedOverrideTests(unittest.TestCase):
    """The audited corrections have no other guard: nothing derives them.

    Each chamber reads only its own rows out of the two shared CSVs, so these
    also check that the filtering did not quietly swallow a correction.
    """

    def test_senate_seat_override_still_names_its_seat(self):
        overrides = sen_overrides.load_seat_overrides()
        self.assertEqual(overrides["1805"]["seatId"], "SEN_4D4CFB205261")
        self.assertTrue(overrides["1805"]["sourceUrl"].startswith("https://"))

    def test_the_camara_reads_no_seat_overrides_of_its_own(self):
        self.assertEqual(dip_overrides.load_seat_overrides(), {})

    def test_both_chamber_aliases_resolve_to_their_canonical_identity(self):
        self.assertEqual(
            dip_overrides.load_person_aliases(),
            {
                "DEP_0AC343D3EC5E": "DEP_10ACE730EC87",
                "DEP_62F86822CD67": "DEP_A9A4258198C3",
            },
        )
        self.assertEqual(sen_overrides.load_person_aliases(), {})


class NameMatchingTests(unittest.TestCase):
    """Run against both copies: the matcher is duplicated per chamber now."""

    def test_initial_stands_in_for_a_given_name(self):
        for module in (dip, sen):
            with self.subTest(chamber=module.CHAMBER):
                self.assertTrue(
                    module.covers_name(
                        module.name_tokens("PEREZ JAEN ZERMEÑO M. ELENA"),
                        module.name_tokens("MARIA ELENA PEREZ-JAEN ZERMEÑO"),
                    )
                )

    def test_surname_alone_is_not_a_match(self):
        # Two full-length agreements are required, so people who merely share a
        # surname must not be collapsed into one person.
        for module in (dip, sen):
            with self.subTest(chamber=module.CHAMBER):
                self.assertFalse(
                    module.covers_name(
                        module.name_tokens("GARCIA JUAN"),
                        module.name_tokens("GARCIA PEDRO"),
                    )
                )

    def test_senate_and_camara_spellings_of_one_person_agree(self):
        for module in (dip, sen):
            with self.subTest(chamber=module.CHAMBER):
                self.assertEqual(
                    module.name_tokens("Sen. Macías Rábago, Julieta"),
                    module.name_tokens("JULIETA MACIAS RABAGO"),
                )


if __name__ == "__main__":
    unittest.main()
