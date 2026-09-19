import unittest

from camara_de_senadores.votos.ingest import missing_no_registro_pairs


class MissingNoRegistroPairsTests(unittest.TestCase):
    def test_fills_gap_strictly_inside_a_senators_own_span(self):
        vote_order = [1, 2, 3, 4, 5]
        existing = {100: {1, 3, 5}}

        pairs = missing_no_registro_pairs(vote_order, existing)

        self.assertEqual(sorted(pairs), [(100, 2), (100, 4)])

    def test_never_backfills_before_first_or_after_last_appearance(self):
        # Senator 100 is only ever seen on votes 2-4: votes 1 and 5 stay
        # untouched, since we have no evidence they held the seat then.
        vote_order = [1, 2, 3, 4, 5]
        existing = {100: {2, 4}}

        pairs = missing_no_registro_pairs(vote_order, existing)

        self.assertEqual(pairs, [(100, 3)])

    def test_single_appearance_has_no_span_to_fill(self):
        vote_order = [1, 2, 3]
        existing = {100: {2}}

        self.assertEqual(missing_no_registro_pairs(vote_order, existing), [])

    def test_no_appearances_yields_nothing(self):
        self.assertEqual(missing_no_registro_pairs([1, 2, 3], {}), [])

    def test_no_gaps_yields_nothing(self):
        vote_order = [1, 2, 3]
        existing = {100: {1, 2, 3}}

        self.assertEqual(missing_no_registro_pairs(vote_order, existing), [])

    def test_senators_are_independent(self):
        vote_order = [1, 2, 3, 4]
        existing = {100: {1, 4}, 200: {2, 3}}

        pairs = missing_no_registro_pairs(vote_order, existing)

        self.assertEqual(sorted(pairs), [(100, 2), (100, 3)])


if __name__ == "__main__":
    unittest.main()
