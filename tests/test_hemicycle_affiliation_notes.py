import unittest

from ui.hemicycle import electoral_affiliation_note


class HemicycleAffiliationNoteTests(unittest.TestCase):
    def test_explains_consistently_different_observed_group(self):
        note = electoral_affiliation_note(
            [
                {
                    "party_key": "PARTIDO B",
                    "valid_from": "2024-09-03",
                    "valid_to": None,
                    "observations": 303,
                }
            ],
            "PARTIDO A",
            "PERSONA PRUEBA",
        )
        self.assertIn("Partido electoral distinto", note)
        self.assertIn("INE atribuyó electoralmente el escaño a PARTIDO A", note)
        self.assertIn("303 votaciones nominales", note)
        self.assertIn("siempre en el grupo parlamentario PARTIDO B", note)
        self.assertIn("2024-09-03", note)
        self.assertIn("no aparece registrada bajo PARTIDO A", note)
        self.assertIn("no la coincidencia del sentido de sus votos", note)
        self.assertIn("no demuestra", note)

    def test_no_note_when_first_group_matches_election_party(self):
        note = electoral_affiliation_note(
            [{"party_key": "PAN", "valid_from": "2024-09-03"}],
            "PAN",
            "PERSONA PRUEBA",
        )
        self.assertIsNone(note)

    def test_mentions_later_timeline_when_electoral_party_appears(self):
        note = electoral_affiliation_note(
            [
                {"party_key": "MORENA", "valid_from": "2024-09-03"},
                {"party_key": "PVEM", "valid_from": "2025-01-01"},
            ],
            "PVEM",
            "PERSONA PRUEBA",
        )
        self.assertIn("muestra cuándo aparece después el grupo electoral", note)

    def test_lists_multiple_non_electoral_groups(self):
        note = electoral_affiliation_note(
            [
                {"party_key": "PARTIDO B", "valid_from": "2024-09-03"},
                {"party_key": "PARTIDO C", "valid_from": "2025-01-01"},
            ],
            "PARTIDO A",
            "PERSONA PRUEBA",
        )
        self.assertIn("primero en PARTIDO B", note)
        self.assertIn("después en PARTIDO C", note)
        self.assertIn("ninguna observación", note)


if __name__ == "__main__":
    unittest.main()
