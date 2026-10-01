import unittest

from camara_de_senadores.escanos.seat_members import fill_no_registro_gaps


def votes(*ids: str) -> list[dict]:
    return [{"id": vote_id} for vote_id in ids]


class FillNoRegistroGapsTests(unittest.TestCase):
    def test_fills_gap_strictly_inside_a_persons_own_span(self):
        v = votes("1", "2", "3", "4", "5")
        histories = {"P": [["1", "Favor"], ["3", "Contra"], ["5", "Favor"]]}

        filled = fill_no_registro_gaps(histories, v, {})

        self.assertEqual(
            filled["P"],
            [
                ["1", "Favor"],
                ["2", "Sin registro"],
                ["3", "Contra"],
                ["4", "Sin registro"],
                ["5", "Favor"],
            ],
        )

    def test_never_fills_before_first_or_after_last_appearance(self):
        v = votes("1", "2", "3", "4", "5")
        histories = {"P": [["2", "Favor"], ["4", "Contra"]]}

        filled = fill_no_registro_gaps(histories, v, {})

        self.assertEqual(filled["P"], [["2", "Favor"], ["3", "Sin registro"], ["4", "Contra"]])

    def test_single_appearance_has_no_span_to_fill(self):
        v = votes("1", "2", "3")
        histories = {"P": [["2", "Favor"]]}

        self.assertEqual(fill_no_registro_gaps(histories, v, {}), {"P": [["2", "Favor"]]})

    def test_never_overwrites_a_vote_another_seat_member_genuinely_cast(self):
        """Regression: a titular whose own first/last vote brackets an entire
        interim licencia must not be filled straight through the suplente who
        actually covered it -- the seat-merged calendar prefers the titular
        whenever both have any entry, so a wrongful fill here would bury the
        suplente's real votes under the titular's placeholder.
        """
        v = votes("1", "2", "3", "4", "5", "6", "7", "8", "9", "10")
        histories = {
            "titular": [["1", "Favor"], ["10", "Contra"]],
            "suplente": [["5", "Favor"], ["6", "Contra"]],
        }
        person_to_seat = {"titular": "SEAT_1", "suplente": "SEAT_1"}

        filled = fill_no_registro_gaps(histories, v, person_to_seat)

        titular_votes = dict(filled["titular"])
        self.assertNotIn("5", titular_votes)
        self.assertNotIn("6", titular_votes)
        self.assertEqual(titular_votes["2"], "Sin registro")
        self.assertEqual(titular_votes["9"], "Sin registro")

        # The suplente's own genuinely cast votes are untouched, and their
        # short span (5-6, no gap) gets nothing backfilled either.
        self.assertEqual(filled["suplente"], [["5", "Favor"], ["6", "Contra"]])

    def test_unclaimed_gap_inside_span_still_fills_for_a_seated_person(self):
        v = votes("1", "2", "3", "4", "5")
        histories = {"titular": [["1", "Favor"], ["5", "Contra"]]}
        person_to_seat = {"titular": "SEAT_1"}

        filled = fill_no_registro_gaps(histories, v, person_to_seat)

        titular_votes = dict(filled["titular"])
        self.assertEqual(titular_votes["3"], "Sin registro")

    def test_person_with_no_seat_mapping_still_fills_normally(self):
        v = votes("1", "2", "3")
        histories = {"P": [["1", "Favor"], ["3", "Contra"]]}

        filled = fill_no_registro_gaps(histories, v, {})

        self.assertEqual(dict(filled["P"])["2"], "Sin registro")


if __name__ == "__main__":
    unittest.main()
